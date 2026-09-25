"""A full rebuild builds a new generation while business writes carry on (ENG-011).

The rebuild used to empty and refill the projection in one transaction holding
the exclusive side of the refresh gate, so every save that refreshes search
waited for all of it — 42–50 s at 38,000 rows in the audit. It now fills a new
generation in short batches and swaps it in at the end (docs/adr/0118). These
hold the promises that design makes, each with both halves in real
transactions on real connections, and each interleaving *forced* — a rebuild
paused at a named point, a writer started and seen to be waiting, then the
rebuild released — rather than hoped for with sleeps:

1.  readers see the old generation, complete, while the new one fills;
2.  the swap is one moment: before it the old generation, after it the new;
3.  a save between batches does not wait for the rebuild at all;
4.  a save during a batch waits for that batch only, and its change is in the
    generation that becomes active;
5.  a record created during the build is in the new generation;
6.  a Teema deleted during the build is not;
7.  a child hard-deleted from under a batch is not, and neither side fails;
8.  a writer holding a row lock the batch needs is never the deadlock victim;
9.  a second rebuild refuses rather than waits;
10. a crash before the swap leaves the old generation in use, and the next
    rebuild cleans up after it;
11. a crash after the swap leaves the new generation in use, and its leftovers
    are reported and cleaned;
12. debt written during the build survives it.

Plus the two properties everything else rests on: nothing in a generation
readers do not read can reach a search result, whoever is asking, and the
active generation is read once per statement.
"""

from __future__ import annotations

import re
import threading
import time
from collections.abc import Callable
from typing import Any

import pytest
from django.core.management import call_command
from django.db import connection, connections, transaction

from app.matters.models import Entry, Matter
from app.search import indexing
from app.search.freshness import (
    consume_once,
    mark_rebuild_owed,
    outstanding,
    rebuild_and_discharge,
)
from app.search.generations import active_generation, building_generation, projection
from app.search.indexing import (
    RebuildAlreadyRunning,
    RebuildResult,
    discard_dead_generations,
    rebuild_all,
    refresh_matters,
)
from app.search.management.commands.check_search_integrity import build_report
from app.search.models import (
    INITIAL_GENERATION,
    SearchDocument,
    SearchGeneration,
    SearchGenerationState,
    SearchSourceKind,
)
from app.search.services import result_count, search_matters
from tests import factories

WAIT = 20

transactional = pytest.mark.django_db(transaction=True, serialized_rollback=True)


class PausedRebuild:
    """A `rebuild_all` on its own connection, stopped at one chosen point.

    ``inside`` pauses in the first batch of that source kind, *holding* the exclusive
    side of the refresh gate and before the batch reads anything. ``after``
    pauses once the first batch of that kind has committed, holding nothing.
    ``before_swap`` pauses after the last batch, before activation.
    """

    def __init__(
        self,
        monkeypatch: pytest.MonkeyPatch,
        *,
        inside: str | None = None,
        after: str | None = None,
        before_swap: bool = False,
        batch_size: int = 5,
    ) -> None:
        self.paused = threading.Event()
        self.release = threading.Event()
        self.result: RebuildResult | None = None
        self.failures: list[BaseException] = []
        self._batch_size = batch_size
        self._after = after
        self._done_pausing = False
        self._thread = threading.Thread(target=self._run)
        self._current: dict[int, str] = {}

        if inside is not None:
            fill_batch = indexing._fill_batch
            bounded = indexing._bounded_lock_waits

            def tracking_fill_batch(number: int, kind: str, *args: Any, **kwargs: Any) -> int:
                self._current[threading.get_ident()] = kind
                return fill_batch(number, kind, *args, **kwargs)

            def pausing_bounded() -> None:
                if self._current.get(threading.get_ident()) == inside:
                    self._pause()
                bounded()

            monkeypatch.setattr(indexing, "_fill_batch", tracking_fill_batch)
            monkeypatch.setattr(indexing, "_bounded_lock_waits", pausing_bounded)
        if before_swap:
            activate = indexing._activate

            def pausing_activate(*args: Any, **kwargs: Any) -> int:
                self._pause()
                return activate(*args, **kwargs)

            monkeypatch.setattr(indexing, "_activate", pausing_activate)

    def _pause(self) -> None:
        if self._done_pausing:
            return
        self._done_pausing = True
        self.paused.set()
        assert self.release.wait(timeout=WAIT * 3)

    def _between(self, kind: str, sources: int) -> None:
        if kind == self._after:
            self._pause()

    def _run(self) -> None:
        try:
            self.result = rebuild_all(batch_size=self._batch_size, between_batches=self._between)
        except BaseException as error:
            self.failures.append(error)
        finally:
            connections.close_all()

    def __enter__(self) -> PausedRebuild:
        self._thread.start()
        assert self.paused.wait(timeout=WAIT), "the rebuild never reached its pause"
        return self

    def __exit__(self, *exc: object) -> None:
        self.release.set()
        self._thread.join(timeout=WAIT * 3)
        assert not self._thread.is_alive(), "the rebuild did not finish"


class Writer:
    """One business transaction on its own connection."""

    def __init__(self, work: Callable[[], None]) -> None:
        self.failures: list[BaseException] = []
        self.finished = threading.Event()
        self.seconds = 0.0
        self._work = work
        self._thread = threading.Thread(target=self._run)

    def _run(self) -> None:
        started = time.monotonic()
        try:
            with transaction.atomic():
                self._work()
        except BaseException as error:
            self.failures.append(error)
        finally:
            self.seconds = time.monotonic() - started
            self.finished.set()
            connections.close_all()

    def start(self) -> Writer:
        self._thread.start()
        return self

    def join(self) -> None:
        self._thread.join(timeout=WAIT * 3)
        assert not self._thread.is_alive(), "the writer did not finish"


def _wait_for_a_waiting_backend() -> None:
    """Until some other backend is queued on a lock — the writer, at the gate."""
    deadline = time.monotonic() + WAIT
    while time.monotonic() < deadline:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT count(*) FROM pg_stat_activity "
                "WHERE pid <> pg_backend_pid() AND wait_event_type = 'Lock'"
            )
            if cursor.fetchone()[0]:
                return
        time.sleep(0.01)
    raise AssertionError("no backend ever waited on a lock")


def _retitle(matter: Matter, title: str) -> Callable[[], None]:
    def work() -> None:
        saved = Matter.objects.get(pk=matter.pk)
        saved.title = title
        saved.save()

    return work


def _matter_rows(matter: Matter, generation: int) -> Any:
    return SearchDocument.objects.filter(
        matter=matter, source_kind=SearchSourceKind.MATTER, generation=generation
    )


@pytest.fixture
def corpus(specialist):
    matters = [
        factories.MatterFactory(owner=specialist, title=f"Taustateema number {index}")
        for index in range(12)
    ]
    for matter in matters[:4]:
        factories.EntryFactory(matter=matter, author=specialist)
    rebuild_all()
    return matters


# -- 1, 2: readers --------------------------------------------------------------


@transactional
def test_1_readers_see_the_old_generation_complete_while_the_new_one_fills(
    corpus, specialist, monkeypatch
):
    before = projection().count()
    old = active_generation()
    found = result_count(query="Taustateema", user=specialist)
    assert found >= 12

    with PausedRebuild(monkeypatch, after="matters", batch_size=5) as rebuild:
        # Five Matters are in the new generation, committed; nobody reads them.
        building = building_generation()
        assert building is not None and building != old
        assert SearchDocument.objects.filter(generation=building).count() == 5
        assert active_generation() == old
        assert projection().count() == before
        assert result_count(query="Taustateema", user=specialist) == found

    assert rebuild.failures == []
    assert active_generation() == rebuild.result.generation != old
    assert result_count(query="Taustateema", user=specialist) == found
    assert not SearchDocument.objects.exclude(generation=active_generation()).exists()


@transactional
def test_2_the_swap_is_one_moment_for_readers(corpus, specialist, monkeypatch):
    Matter.objects.filter(pk=corpus[0].pk).update(title="Vahetuse järel nähtav")
    old = active_generation()

    with PausedRebuild(monkeypatch, before_swap=True) as rebuild:
        # Fully built, not yet active: the new title is written and unread.
        assert _matter_rows(corpus[0], building_generation()).get().title == (
            "Vahetuse järel nähtav"
        )
        assert search_matters(query="Vahetuse", user=specialist) == []
        assert active_generation() == old

    assert rebuild.failures == []
    assert [r.matter for r in search_matters(query="Vahetuse", user=specialist)] == [corpus[0]]


def test_the_active_generation_is_read_once_per_statement(db, specialist):
    """An uncorrelated scalar subquery: an InitPlan, evaluated once, not per row.

    What makes the swap atomic to a reader: one statement cannot see the old
    generation for some rows and the new one for others.
    """
    from app.search.services import visible_documents

    sql, params = visible_documents(specialist).query.sql_with_params()
    with connection.cursor() as cursor:
        cursor.execute("EXPLAIN " + sql, params)
        plan = "\n".join(row[0] for row in cursor.fetchall())
    assert re.search(r"generation = COALESCE\(\(InitPlan \d+\)", plan), plan


def test_a_generation_nobody_reads_reaches_no_result_for_anyone(db, specialist, reader):
    """Rows outside the active generation are invisible to every reader.

    A building generation can hold text the active one does not — including,
    between a restriction and the next refresh, text that is no longer
    anybody's to read. Visibility is still checked on the live Matter; this
    holds that the generation filter is also always there.
    """
    matter = factories.MatterFactory(owner=specialist, title="Nähtav pealkiri")
    rebuild_all()
    row = _matter_rows(matter, active_generation()).get()
    SearchGeneration.objects.create(
        number=active_generation() + 1,
        state=SearchGenerationState.BUILDING,
        started_at=row.indexed_at,
    )
    SearchDocument.objects.filter(pk=row.pk).update(
        generation=active_generation() + 1, title="Ehitamisel sõnaolend"
    )
    with connection.cursor() as cursor:
        cursor.execute(
            "UPDATE search_searchdocument SET search_simple = to_tsvector('simple', title), "
            "search_estonian = to_tsvector('estonian', title) WHERE id = %s",
            [row.pk],
        )
    for user in (specialist, reader):
        assert result_count(query="sõnaolend", user=user) == 0
        assert result_count(query="Nähtav", user=user) == 0


# -- 3, 4, 5, 6, 7, 8: writes during the build ---------------------------------


@transactional
def test_3_a_save_between_batches_does_not_wait_for_the_rebuild(corpus, monkeypatch):
    with PausedRebuild(monkeypatch, after="matters", batch_size=5) as rebuild:
        writer = Writer(_retitle(corpus[11], "Salvestatud ehituse ajal")).start()
        # Finishes while the rebuild is still paused: nothing held it.
        assert writer.finished.wait(timeout=WAIT), "the save waited for the rebuild"
        writer.join()
        assert writer.failures == []

    assert rebuild.failures == []
    assert _matter_rows(corpus[11], active_generation()).get().title == ("Salvestatud ehituse ajal")
    assert _matter_rows(corpus[11], active_generation()).count() == 1


@transactional
def test_4_a_save_during_a_batch_waits_for_that_batch_and_lands_in_the_new_generation(
    corpus, monkeypatch
):
    """The race the gate exists for, now per batch.

    The batch holds the exclusive side and has not read yet; the save updates
    the Matter and queues at the gate. Released, the batch reads the Matter as
    it was committed — the old title — and writes that; the save then rewrites
    both live generations. Without the gate one of the two inserts would hit
    the one-row-per-source constraint, and if it were the save's, the user's
    work would roll back.
    """
    with PausedRebuild(monkeypatch, inside=SearchSourceKind.MATTER, batch_size=50) as rebuild:
        writer = Writer(_retitle(corpus[0], "Uus pealkiri")).start()
        _wait_for_a_waiting_backend()
        assert not writer.finished.is_set()
    writer.join()

    assert rebuild.failures == [] and writer.failures == []
    rows = _matter_rows(corpus[0], active_generation())
    assert rows.count() == 1
    assert rows.get().title == "Uus pealkiri"


@transactional
def test_5_a_record_created_during_the_build_is_in_the_new_generation(
    corpus, specialist, monkeypatch
):
    def create() -> None:
        entry = factories.EntryFactory(
            matter=corpus[7], author=specialist, body="<p>Ehituse ajal lisatud märge</p>"
        )
        created.append(entry)

    created: list[Entry] = []
    with PausedRebuild(monkeypatch, after="entries", batch_size=2) as rebuild:
        writer = Writer(create).start()
        writer.join()
        assert writer.failures == []

    assert rebuild.failures == []
    assert (
        projection()
        .filter(source_kind=SearchSourceKind.ENTRY, source_object_id=created[0].pk)
        .exists()
    )


@transactional
def test_6_a_matter_deleted_during_the_build_is_not_in_the_new_generation(
    corpus, specialist, monkeypatch
):
    from app.matters.deletion import delete_matter

    doomed = corpus[8]
    with PausedRebuild(monkeypatch, inside=SearchSourceKind.MATTER, batch_size=50) as rebuild:
        writer = Writer(lambda: delete_matter(matter=doomed, actor=specialist)).start()
        _wait_for_a_waiting_backend()
    writer.join()

    assert rebuild.failures == [] and writer.failures == []
    assert not SearchDocument.objects.filter(matter=doomed).exists()


@transactional
def test_7_a_child_deleted_from_under_a_batch_leaves_no_row_and_no_failure(corpus, monkeypatch):
    """A hard delete, which no refresh follows: the foreign key is the only order.

    The batch reads the Entry, the delete commits, the batch's insert names a
    row that is gone and fails its foreign key at commit. The batch is retried,
    reads the Entry as gone, and the rebuild finishes.
    """
    entry = Entry.objects.filter(matter=corpus[0]).get()

    with PausedRebuild(monkeypatch, inside=SearchSourceKind.ENTRY, batch_size=50) as rebuild:
        original = indexing._recompute_vectors
        calls = {"n": 0}

        def delete_then_recompute(documents: Any) -> None:
            # In the batch, after it inserted the Entry's row and before it
            # commits: the Entry goes, on another connection, and commits.
            calls["n"] += 1
            if calls["n"] == 1:
                deleter = Writer(lambda: Entry.objects.filter(pk=entry.pk).delete()).start()
                deleter.join()
                assert deleter.failures == []
            original(documents)

        monkeypatch.setattr(indexing, "_recompute_vectors", delete_then_recompute)

    assert rebuild.failures == []
    assert rebuild.result.retried_batches >= 1
    assert not SearchDocument.objects.filter(entry_id=entry.pk).exists()


@transactional
def test_8_a_writer_holding_a_row_the_batch_needs_is_never_the_deadlock_victim(corpus, monkeypatch):
    """The one cycle the per-batch gate can form, and who gives way.

    The batch holds the gate. The writer deletes an Entry — a row lock — and
    then refreshes its Matter, so it queues at the gate. Released, the batch
    reads the Entry (the delete is not committed), inserts a row naming it,
    and at commit its foreign-key check waits on the writer's lock. Each waits
    for the other. PostgreSQL would cancel whichever checks first, which could
    be the user's delete; instead the batch's bounded lock wait runs out first,
    it rolls back and releases the gate, the writer commits, and the retried
    batch finds the Entry gone.
    """
    entry = Entry.objects.filter(matter=corpus[1]).get()

    def delete_and_refresh() -> None:
        Entry.objects.filter(pk=entry.pk).delete()
        refresh_matters(Matter.objects.filter(pk=corpus[1].pk))

    with PausedRebuild(monkeypatch, inside=SearchSourceKind.ENTRY, batch_size=50) as rebuild:
        writer = Writer(delete_and_refresh).start()
        _wait_for_a_waiting_backend()
    writer.join()

    assert writer.failures == [], writer.failures
    assert rebuild.failures == [], rebuild.failures
    # The batch gave way — the interleaving happened, it was not dodged.
    assert rebuild.result.retried_batches >= 1
    assert not Entry.objects.filter(pk=entry.pk).exists()
    assert not SearchDocument.objects.filter(entry_id=entry.pk).exists()
    assert active_generation() == rebuild.result.generation


# -- 9, 10, 11, 12: the rebuild itself -----------------------------------------


@transactional
def test_9_a_second_rebuild_refuses_rather_than_waits(corpus, monkeypatch):
    with PausedRebuild(monkeypatch, after="matters", batch_size=5) as rebuild:
        with pytest.raises(RebuildAlreadyRunning):
            rebuild_all()
        assert indexing.rebuild_is_running()
        with pytest.raises(Exception, match="juba käib"):
            call_command("rebuild_search_index")
        # The worker neither fails nor queues: its debt stays owed for the
        # next pass, and no failed attempt is recorded against it.
        owed = mark_rebuild_owed("worker")
        assert consume_once().rebuilt is False
        owed.refresh_from_db()
        assert owed.attempts == 0
    assert rebuild.failures == []
    assert not indexing.rebuild_is_running()


def test_10_a_crash_before_the_swap_leaves_the_old_generation_in_use(corpus, specialist):
    old = active_generation()
    found = result_count(query="Taustateema", user=specialist)

    def die(kind: str, sources: int) -> None:
        if kind == "entries":
            raise KeyboardInterrupt  # the operator's Ctrl-C, mid-build

    with pytest.raises(KeyboardInterrupt):
        rebuild_all(batch_size=5, between_batches=die)

    assert active_generation() == old
    assert result_count(query="Taustateema", user=specialist) == found
    failed = SearchGeneration.objects.get(state=SearchGenerationState.FAILED)
    assert SearchDocument.objects.filter(generation=failed.number).exists()

    result = rebuild_all()
    assert result.generation > failed.number
    assert not SearchDocument.objects.exclude(generation=result.generation).exists()


def test_10b_a_process_that_died_mid_build_is_reported_and_cleaned(corpus):
    """Killed outright: no `except` ran, the generation is still BUILDING.

    Its session lock died with it, so the next rebuild may start; that is how
    it tells a dead build from a live one.
    """
    stranded = SearchGeneration.objects.create(
        number=active_generation() + 1,
        state=SearchGenerationState.BUILDING,
        started_at=projection().first().indexed_at,
    )
    SearchDocument.objects.create(
        matter=corpus[0],
        source_kind=SearchSourceKind.MATTER,
        source_object_id=corpus[0].pk,
        title="pooleli",
        indexed_at=stranded.started_at,
        generation=stranded.number,
    )
    labels = {finding.label for finding in build_report().findings}
    assert "Katkenud täisehitus" in labels

    result = rebuild_all()
    stranded.refresh_from_db()
    assert stranded.state == SearchGenerationState.FAILED
    assert not SearchDocument.objects.filter(generation=stranded.number).exists()
    assert result.generation > stranded.number
    assert build_report().ok


def test_11_a_crash_after_the_swap_leaves_the_new_generation_in_use(corpus, monkeypatch):
    old = active_generation()
    discard = indexing._discard_generations

    def die(*, keep: set[int]) -> int:
        if keep:  # the cleanup after the swap, not the one before the build
            raise KeyboardInterrupt
        return discard(keep=keep)

    monkeypatch.setattr(indexing, "_discard_generations", die)
    with pytest.raises(KeyboardInterrupt):
        rebuild_all()
    monkeypatch.undo()

    new = active_generation()
    assert new != old
    assert SearchGeneration.objects.get(number=new).state == SearchGenerationState.ACTIVE
    assert SearchDocument.objects.filter(generation=old).exists()
    report = build_report()
    assert "Vanad põlvkonnad" in {finding.label for finding in report.findings}
    assert report.dead_rows == SearchDocument.objects.filter(generation=old).count()

    assert discard_dead_generations() > 0
    assert build_report().ok


def test_12_debt_written_during_the_build_survives_it(corpus, monkeypatch):
    claimed = mark_rebuild_owed("before")

    def owe(kind: str, sources: int) -> None:
        if kind == "matters" and not outstanding().filter(reason="during").exists():
            mark_rebuild_owed("during")

    def rebuild_with_a_write(**kwargs: Any) -> RebuildResult:
        return rebuild_all(between_batches=owe, **kwargs)

    monkeypatch.setattr("app.search.freshness.rebuild_all", rebuild_with_a_write)
    outcome = rebuild_and_discharge()

    assert outcome.cleared == 1
    assert not outstanding().filter(pk=claimed.pk).exists()
    assert list(outstanding().values_list("reason", flat=True)) == ["during"]


# -- the report -----------------------------------------------------------------


def test_the_rebuild_reports_how_long_it_held_writers(corpus):
    result = rebuild_all(batch_size=3)
    assert result.batches >= 12 // 3
    assert 0 < result.swap_gate_seconds <= result.longest_gate_seconds
    assert result.generation == active_generation() > INITIAL_GENERATION
