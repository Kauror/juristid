"""A deletion and a new cross-Matter pointer cannot both commit (ENG-073).

`delete_matter` refuses to delete a Matter that another Matter points at, and
removes the rows that exist only to pair two Matters. It decides both under the
lock of the Matter being deleted. The writers that create such pointers — a
closure naming a `Järglane`, `Seotud teema`, a dismissed suggestion, a
background citation of another Matter's opinion — locked only the Matter they
were written on. So a deletion that had already planned, and a writer holding
the *other* Matter, each passed their own check and both committed:

* a closed Matter whose successor was a tombstone, linked from its page to a 404;
* a MatterRelation left under a tombstone.

Every writer now locks both Matters in ascending-id order and re-reads them.
These tests force both orders of every pair. The deletion is held after its plan
and before its delete until the writer is seen waiting (or has finished); the
writer is held with its rows written until the deletion is seen waiting. Only one
logically compatible outcome may commit.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from typing import Any

import pytest
from django.db import connection, transaction

from app.audit.enums import ChangeEventType
from app.audit.models import ChangeEvent
from app.core.errors import DomainError
from app.matters import deletion
from app.matters.deletion import delete_matter
from app.matters.models import Matter
from app.matters.services import SUCCESSOR_DELETED_REFUSAL, close_matter
from app.related_materials.models import (
    MatterBackgroundMaterial,
    MatterRelation,
    RelatedSuggestionDismissal,
)
from app.related_materials.services import (
    BACKGROUND_SOURCE_GONE,
    RELATED_MATTER_GONE,
    add_background_submission,
    dismiss_related_suggestion,
    link_related_matters,
)
from app.workflow.enums import Disposition
from tests import factories
from tests.test_evidence_concurrency import TIMEOUT, Runner, wait_until_blocked
from tests.test_related_materials import _sent_opinion

pytestmark = pytest.mark.django_db(transaction=True, serialized_rollback=True)


def _another_backend_waits() -> bool:
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_stat_clear_snapshot()")
        cursor.execute(
            """
            SELECT count(*) FROM pg_stat_activity
             WHERE datname = current_database()
               AND wait_event_type = 'Lock'
               AND pid <> pg_backend_pid()
            """
        )
        return cursor.fetchone()[0] > 0


def _deletion_first(
    monkeypatch: Any, doomed: Matter, write: Callable[[], Any]
) -> tuple[BaseException | None, BaseException | None]:
    """Delete ``doomed`` while ``write`` tries to point at it.

    The deletion takes its lock and builds its plan, then holds before deleting
    anything until the writer is waiting on a lock — the fix — or has finished —
    the bug, where nothing made it wait.
    """
    real = deletion._collect_owned
    calls: list[int] = []
    planned = threading.Event()
    written = threading.Event()

    def collect(matter: Matter) -> Any:
        owned = real(matter)
        calls.append(1)
        # The second call is the one after the plan (`plan_matter_deletion`
        # makes the first), which is where the check has been decided.
        if len(calls) == 2 and not planned.is_set():
            planned.set()
            deadline = time.monotonic() + TIMEOUT
            while not written.is_set() and time.monotonic() < deadline:
                if _another_backend_waits():
                    break
                time.sleep(0.02)
        return owned

    monkeypatch.setattr(deletion, "_collect_owned", collect)

    def run_writer() -> None:
        assert planned.wait(TIMEOUT)
        try:
            with transaction.atomic():
                write()
        finally:
            written.set()

    deleting = Runner(
        lambda: delete_matter(matter=Matter.objects.get(pk=doomed.pk), actor=doomed.owner)
    ).start()
    writing = Runner(run_writer).start()
    return deleting.join(), writing.join()


def _writer_first(
    doomed: Matter, write: Callable[[], Any]
) -> tuple[BaseException | None, BaseException | None]:
    """``write`` commits only once the deletion is seen waiting for it."""
    wrote = threading.Event()
    release = threading.Event()

    def run_writer() -> None:
        with transaction.atomic():
            write()
            wrote.set()
            assert release.wait(TIMEOUT)

    def run_deletion() -> None:
        assert wrote.wait(TIMEOUT)
        delete_matter(matter=Matter.objects.get(pk=doomed.pk), actor=doomed.owner)

    writing = Runner(run_writer).start()
    deleting = Runner(run_deletion).start()
    try:
        wait_until_blocked(backends=1)
    finally:
        release.set()
    return writing.join(), deleting.join()


def _is_deleted(matter: Matter) -> bool:
    return Matter.all_objects.get(pk=matter.pk).deleted_at is not None


def assert_no_live_pointer_at_a_tombstone() -> None:
    tombstones = set(
        Matter.all_objects.filter(deleted_at__isnull=False).values_list("pk", flat=True)
    )
    live = Matter.objects.all()
    assert not live.filter(superseded_by__in=tombstones).exists(), (
        "a live Matter names a deleted successor"
    )
    assert not MatterRelation.objects.filter(matter_a__in=tombstones).exists()
    assert not MatterRelation.objects.filter(matter_b__in=tombstones).exists()
    assert not RelatedSuggestionDismissal.objects.filter(candidate_matter__in=tombstones).exists()
    assert not MatterBackgroundMaterial.objects.filter(submission__matter__in=tombstones).exists()


@pytest.fixture
def pair(specialist):
    doomed = factories.MatterFactory(owner=specialist, title="Kustutatav teema")
    other = factories.MatterFactory(owner=specialist, title="Teine teema")
    return doomed, other


# ---------------------------------------------------------------------------
# Järglane
# ---------------------------------------------------------------------------


def _close_under(other: Matter, doomed: Matter, actor: Any) -> Callable[[], Any]:
    return lambda: close_matter(
        matter=Matter.objects.get(pk=other.pk),
        disposition=Disposition.SUPERSEDED,
        successor=Matter.all_objects.get(pk=doomed.pk),
        actor=actor,
    )


def test_deletion_first_refuses_the_closure_that_names_it(pair, specialist, monkeypatch):
    doomed, other = pair

    deletion_error, closure_error = _deletion_first(
        monkeypatch, doomed, _close_under(other, doomed, specialist)
    )

    assert deletion_error is None
    assert isinstance(closure_error, DomainError)
    assert str(closure_error) == SUCCESSOR_DELETED_REFUSAL
    assert _is_deleted(doomed)
    survivor = Matter.objects.get(pk=other.pk)
    assert survivor.is_open and survivor.superseded_by_id is None
    assert not ChangeEvent.objects.filter(
        matter=other, event_type=ChangeEventType.MATTER_CLOSED
    ).exists()
    assert_no_live_pointer_at_a_tombstone()


def test_closure_first_refuses_the_deletion(pair, specialist):
    doomed, other = pair

    closure_error, deletion_error = _writer_first(doomed, _close_under(other, doomed, specialist))

    assert closure_error is None
    assert isinstance(deletion_error, DomainError)
    assert not _is_deleted(doomed)
    assert Matter.objects.get(pk=other.pk).superseded_by_id == doomed.pk
    assert_no_live_pointer_at_a_tombstone()


# ---------------------------------------------------------------------------
# Seotud teema
# ---------------------------------------------------------------------------


def _link(other: Matter, doomed: Matter, actor: Any) -> Callable[[], Any]:
    return lambda: link_related_matters(
        matter=Matter.objects.get(pk=other.pk),
        other=Matter.all_objects.get(pk=doomed.pk),
        actor=actor,
    )


def test_deletion_first_refuses_the_relation(pair, specialist, monkeypatch):
    doomed, other = pair

    deletion_error, link_error = _deletion_first(
        monkeypatch, doomed, _link(other, doomed, specialist)
    )

    assert deletion_error is None
    assert isinstance(link_error, DomainError)
    assert str(link_error) == RELATED_MATTER_GONE
    assert _is_deleted(doomed)
    assert MatterRelation.objects.count() == 0
    assert not ChangeEvent.objects.filter(
        matter=other, event_type=ChangeEventType.MATTER_RELATION_ADDED
    ).exists()
    assert_no_live_pointer_at_a_tombstone()


def test_relation_first_is_removed_with_the_deleted_matter(pair, specialist):
    """A relation is a pair row (docs/adr/0096): the deletion that waited for it
    sees it, and removes it with the Matter rather than leaving it behind."""
    doomed, other = pair

    link_error, deletion_error = _writer_first(doomed, _link(other, doomed, specialist))

    assert link_error is None and deletion_error is None
    assert _is_deleted(doomed)
    assert MatterRelation.objects.count() == 0
    assert_no_live_pointer_at_a_tombstone()


# ---------------------------------------------------------------------------
# Ei ole seotud, and a background citation
# ---------------------------------------------------------------------------


def test_deletion_first_refuses_the_dismissal(pair, specialist, monkeypatch):
    doomed, other = pair

    deletion_error, dismissal_error = _deletion_first(
        monkeypatch,
        doomed,
        lambda: dismiss_related_suggestion(
            matter=Matter.objects.get(pk=other.pk),
            candidate_matter=Matter.all_objects.get(pk=doomed.pk),
            actor=specialist,
        ),
    )

    assert deletion_error is None
    assert isinstance(dismissal_error, DomainError)
    assert RelatedSuggestionDismissal.objects.count() == 0
    assert_no_live_pointer_at_a_tombstone()


def test_deletion_first_refuses_the_background_citation(specialist, monkeypatch):
    doomed = factories.MatterFactory(owner=specialist, reference_year=2026, reference_number=731)
    citing = factories.MatterFactory(owner=specialist, reference_year=2026, reference_number=732)
    opinion = _sent_opinion(doomed, "Arvamus, mida tsiteeritakse")

    deletion_error, citation_error = _deletion_first(
        monkeypatch,
        doomed,
        lambda: add_background_submission(
            matter=Matter.objects.get(pk=citing.pk), submission=opinion, actor=specialist
        ),
    )

    assert deletion_error is None
    assert isinstance(citation_error, DomainError)
    assert str(citation_error) == BACKGROUND_SOURCE_GONE
    assert MatterBackgroundMaterial.objects.count() == 0
    assert_no_live_pointer_at_a_tombstone()


def test_background_citation_first_refuses_the_deletion(specialist):
    doomed = factories.MatterFactory(owner=specialist, reference_year=2026, reference_number=733)
    citing = factories.MatterFactory(owner=specialist, reference_year=2026, reference_number=734)
    opinion = _sent_opinion(doomed, "Arvamus, mida tsiteeritakse")

    citation_error, deletion_error = _writer_first(
        doomed,
        lambda: add_background_submission(
            matter=Matter.objects.get(pk=citing.pk), submission=opinion, actor=specialist
        ),
    )

    assert citation_error is None
    assert isinstance(deletion_error, DomainError)
    assert not _is_deleted(doomed)
    assert MatterBackgroundMaterial.objects.filter(matter=citing).count() == 1


# ---------------------------------------------------------------------------
# The order itself
# ---------------------------------------------------------------------------


def test_two_writers_on_the_same_pair_do_not_deadlock(pair, specialist):
    """Linking A→B and B→A at once: both take the two rows in the same order."""
    first, second = pair
    start = threading.Barrier(2, timeout=TIMEOUT)

    def link(source: Matter, target: Matter) -> Callable[[], None]:
        def run() -> None:
            start.wait()
            link_related_matters(
                matter=Matter.objects.get(pk=source.pk),
                other=Matter.objects.get(pk=target.pk),
                actor=specialist,
            )

        return run

    one = Runner(link(first, second)).start()
    two = Runner(link(second, first)).start()
    assert one.join() is None and two.join() is None
    assert MatterRelation.objects.count() == 1
