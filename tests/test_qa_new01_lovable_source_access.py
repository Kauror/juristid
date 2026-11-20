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


def test_the_probe_expects_the_population_of_the_last_good_reading(pilot_on):
    from app.legacy_import.management.commands.lovable_sync import _last_live_population

    world = build_world(6)
    stored = LovableSyncSnapshot.objects.order_by("-taken_at").first()
    assert stored is not None
    assert _last_live_population() == len(stored.document["index"]) > 0
    hidden, _fake = _over_http(world, visible=_nothing_admitted)
    world.run(transport=hidden, at=world.runs[-1].started_at + DAY)  # refused: not a baseline
    assert _last_live_population() == len(stored.document["index"])
