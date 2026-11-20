# ruff: noqa: F811 - the fixtures imported from the contract suite are used as arguments
"""QA-NEW-01 (coordinator QA 2026-11-20): the source stops admitting the service login.

On 19.11.2026 Lovable's migration 0035 made the live-topic row rule require an
active staff session. The profile-less service login has none, so PostgREST
answers every read with HTTP 200, an exact count of 0 and no rows — and the
child tables follow through their ``EXISTS`` on ``topic``. These tests replay
that answer through the real `HttpTransport` (the in-process PostgREST fake)
and pin the cross-system contract on the Juristid side:

* a zero-row reading is refused by its own name (`SOURCE_EMPTY`), not taken
  for 2,370 deletions, in APPLY and in COMPARE, night after night;
* a reading narrowed to part of the register still fails closed (`COHORT_MISSING`);
* a refused sign-in or read (401/403: a suspended or unknown login) fails closed;
* in every case no Juristid-owned row is written, no reading is stored as a
  baseline, and the first night access returns is an ordinary night;
* nothing but GET (and the sign-in POST) reaches Lovable.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

import pytest

from app.legacy_import import lovable_source, lovable_sync
from app.legacy_import.lovable_sync_models import (
    LovableSyncSnapshot,
    SyncMode,
    SyncRunStatus,
)
from tests.lovable_contract.fixtures import pilot_on  # noqa: F401 - fixture
from tests.lovable_contract.world import (
    BOOKKEEPING_TABLES,
    build_world,
    changed_tables,
    ref,
    source_id,
    table_hashes,
)
from tests.lovable_postgrest_fake import PostgrestFake, config

pytestmark = pytest.mark.django_db

DAY = dt.timedelta(days=1)


def _over_http(world: Any, *, visible: Any = None) -> tuple[Any, PostgrestFake]:
    """The world's Lovable behind HTTP; ``visible(table, row) -> bool`` is the row rule."""

    def source(table: str, columns: Any, *, filters: Any, order: str) -> list[dict[str, Any]]:
        rows = world.lovable.select(table, columns, filters=filters, order=order)
        if visible is None:
            return rows
        return [r for r in rows if visible(table, r)]

    fake = PostgrestFake(source=source)
    return lovable_source.HttpTransport(config(), opener=fake), fake


def _business_tables_unchanged(before: dict[str, str]) -> None:
    moved = changed_tables(before, table_hashes()) - BOOKKEEPING_TABLES
    # The run ledger itself is bookkeeping; nothing a person or a page reads moved.
    assert moved == set(), moved


def _nothing_admitted(_table: str, _row: dict[str, Any]) -> bool:
    return False


@pytest.mark.parametrize("mode", [SyncMode.APPLY, SyncMode.COMPARE])
def test_a_row_rule_that_admits_nothing_is_refused_by_name_and_writes_nothing(pilot_on, mode):
    world = build_world(6)
    baseline = world.runs[-1]
    assert baseline.status == SyncRunStatus.SUCCEEDED
    world.lovable.edit(ref(1), teema="Muudetud Lovable'is, kui lugemine oli suletud")
    snapshots = LovableSyncSnapshot.objects.count()
    before = table_hashes()
    transport, fake = _over_http(world, visible=_nothing_admitted)

    run = world.run(mode, transport=transport, at=baseline.started_at + DAY)

    assert run.status == SyncRunStatus.FAILED
    assert run.error.startswith("SOURCE_EMPTY:"), run.error
    assert LovableSyncSnapshot.objects.count() == snapshots  # never a baseline
    _business_tables_unchanged(before)
    assert fake.methods <= {"GET", "POST"}
    assert all(p == "/auth/v1/token" for m, p, _q in fake.requests if m == "POST")


def test_the_refusal_holds_every_night_and_the_first_night_back_is_ordinary(pilot_on):
    world = build_world(6)
    start = world.runs[-1].started_at
    hidden, _fake = _over_http(world, visible=_nothing_admitted)
    for night in (1, 2, 3):
        run = world.run(transport=hidden, at=start + night * DAY)
        assert run.error.startswith("SOURCE_EMPTY:"), (night, run.error)

    world.lovable.edit(ref(2), teema="Pealkiri pärast taastamist")
    restored, _fake = _over_http(world)
    run = world.run(transport=restored, at=start + 4 * DAY)

    assert run.status == SyncRunStatus.SUCCEEDED, run.error
    assert world.matter(ref(2)).title == "Pealkiri pärast taastamist"
    assert world.matter(ref(1)).title  # nothing was taken for a deletion meanwhile


def test_a_row_rule_narrowed_to_some_topics_still_fails_closed(pilot_on, monkeypatch):
    monkeypatch.setattr(lovable_sync, "MAX_MISSING", 2)
    world = build_world(6)
    before = table_hashes()
    kept = {source_id(ref(1))}

    def own_rows_only(table: str, row: dict[str, Any]) -> bool:
        if table == "topic_stage_link":
            return {str(row.get("prev_topic_id")), str(row.get("next_topic_id"))} <= kept
        return str(row.get("id") if table == "topic" else row.get("topic_id")) in kept

    transport, _fake = _over_http(world, visible=own_rows_only)
    run = world.run(transport=transport, at=world.runs[-1].started_at + DAY)

    assert run.status == SyncRunStatus.FAILED
    assert run.error.split(":", 1)[0] in {"COHORT_MISSING", "POPULATION_CHANGE"}, run.error
    _business_tables_unchanged(before)


@pytest.mark.parametrize("status", [401, 403])
def test_a_refused_login_or_read_fails_closed(pilot_on, status):
    world = build_world(6)
    before = table_hashes()
    transport, fake = _over_http(world)
    fake.fail = [status]

    run = world.run(transport=transport, at=world.runs[-1].started_at + DAY)

    assert run.status == SyncRunStatus.FAILED
    assert run.error.startswith(f"HTTP_{status}"), run.error
    _business_tables_unchanged(before)


# -- the access probe against the night it vouches for (coordinator review 2026-11-20) --


def _probe_over(world: Any, *, visible: Any = None) -> Any:
    """The access probe over the world's Lovable behind HTTP, judged against the
    database's last trusted reading; the admin-only history and deleted topics
    answer no rows."""
    from tests.lovable_postgrest_fake import _Answer

    _transport, fake = _over_http(world, visible=visible)

    def opener(request: Any, timeout: float) -> Any:
        url = request.full_url
        if "topic_change" in url or "deleted_at" in url:
            return _Answer([], url, {"Content-Range": "*/0"})
        return fake(request, timeout)

    return lovable_source.access_probe(
        config(), baseline=lovable_sync.probe_baseline(), opener=opener
    )


def test_the_probe_is_not_ready_where_the_night_refuses_missing_topics(pilot_on, monkeypatch):
    monkeypatch.setattr(lovable_sync, "MAX_MISSING", 2)
    world = build_world(6)
    hidden_ids = {source_id(ref(n)) for n in (1, 2, 3)}

    def narrowed(table: str, row: dict[str, Any]) -> bool:
        if table == "topic_stage_link":
            return not ({str(row["prev_topic_id"]), str(row["next_topic_id"])} & hidden_ids)
        return str(row.get("id") if table == "topic" else row.get("topic_id")) not in hidden_ids

    probe = _probe_over(world, visible=narrowed)
    assert not probe.reads_ready
    assert next(c for c in probe.checks if c.name == lovable_source.IDENTITY_CHECK).status == "FAIL"

    transport, _fake = _over_http(world, visible=narrowed)
    night = world.run(transport=transport, at=world.runs[-1].started_at + DAY)
    assert night.error.startswith("COHORT_MISSING"), night.error


def test_the_probe_is_ready_for_new_topics_and_an_ordinary_night(pilot_on):
    from tests.lovable_contract.world import lovable_row

    world = build_world(6)
    for number in (11, 12):
        world.lovable.put(source_id(ref(number)), lovable_row(ref(number)), version=1)

    probe = _probe_over(world)
    assert probe.reads_ready, [(c.name, c.status, c.detail) for c in probe.checks]
    assert "2 new" in next(
        c.detail for c in probe.checks if c.name == lovable_source.IDENTITY_CHECK
    )


def test_the_probe_is_not_ready_when_a_child_table_is_narrowed(pilot_on):
    world = build_world(6)
    world.lovable.set_commencements(
        ref(1),
        [
            {"liik": "Kindla kuupäevaga jõustumine", "kuupaev": "2099-01-01"},
            {"liik": "Üldises korras"},
        ],
    )
    settled = world.run(at=world.runs[-1].started_at + DAY)
    assert settled.status == SyncRunStatus.SUCCEEDED, settled.error
    first = source_id(ref(1))
    hidden_row = next(c["id"] for c in world.lovable.commencements if c["topic_id"] == first)

    def one_child_hidden(table: str, row: dict[str, Any]) -> bool:
        return not (table == "topic_commencement" and row.get("id") == hidden_row)

    probe = _probe_over(world, visible=one_child_hidden)
    checks = {c.name: c.status for c in probe.checks}
    assert checks[lovable_source.IDENTITY_CHECK] == "PASS"
    assert checks[lovable_source.CHILDREN_CHECK] == "FAIL"
    assert not probe.reads_ready


def test_the_probe_reads_the_trusted_reading_from_the_last_good_night_only(pilot_on):
    world = build_world(6)
    trusted = lovable_sync.probe_baseline()
    assert trusted is not None and len(trusted.cohort_ids) == 6
    hidden, _fake = _over_http(world, visible=_nothing_admitted)
    world.run(transport=hidden, at=world.runs[-1].started_at + DAY)  # refused: not a baseline
    again = lovable_sync.probe_baseline()
    assert again is not None and again.taken_at == trusted.taken_at


def test_a_child_row_hidden_under_an_unchanged_version_is_refused_not_applied(pilot_on):
    """Coordinator review 2026-11-20: a commencement hidden from the login while
    its topic is visible and unedited was applied as a removal (the Teema's
    important date cancelled). The night now refuses it before anything is
    stored; a save of the topic in Lovable moves its version and settles it."""
    world = build_world(6)
    world.lovable.set_commencements(
        ref(1),
        [
            {"liik": "Kindla kuupäevaga jõustumine", "kuupaev": "2099-01-01"},
            {"liik": "Üldises korras"},
        ],
    )
    settled = world.run(at=world.runs[-1].started_at + DAY)
    assert settled.status == SyncRunStatus.SUCCEEDED, settled.error
    first = source_id(ref(1))
    hidden_row = next(c["id"] for c in world.lovable.commencements if c["topic_id"] == first)

    def one_child_hidden(table: str, row: dict[str, Any]) -> bool:
        return not (table == "topic_commencement" and row.get("id") == hidden_row)

    snapshots = LovableSyncSnapshot.objects.count()
    before = table_hashes()
    transport, _fake = _over_http(world, visible=one_child_hidden)
    night = world.run(transport=transport, at=world.runs[-1].started_at + DAY)

    assert night.status == SyncRunStatus.FAILED
    assert night.error.startswith("CHILDREN_HIDDEN: 1 "), night.error
    assert LovableSyncSnapshot.objects.count() == snapshots
    _business_tables_unchanged(before)
    assert not world.events_of(night).exists()

    # A supervised COMPARE run, after a person has looked, stores the reading
    # (accepts it) and writes nothing; it is how every reading-level refusal is
    # accepted (independent review 2026-11-20, P2-A).
    before = table_hashes()
    looked, _fake = _over_http(world, visible=one_child_hidden)
    accepted = world.run(SyncMode.COMPARE, transport=looked, at=world.runs[-1].started_at + DAY)
    assert accepted.status == SyncRunStatus.SUCCEEDED, accepted.error
    _business_tables_unchanged(before)

    # A real edit in Lovable (any save moves the version) is an ordinary night.
    world.lovable.set_commencements(ref(1), [{"liik": "Üldises korras"}])
    edited, _fake = _over_http(world)
    after = world.run(transport=edited, at=world.runs[-1].started_at + DAY)
    assert after.status == SyncRunStatus.SUCCEEDED, after.error
