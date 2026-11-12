# ruff: noqa: F811 - the fixtures imported from fixtures.py are used as arguments
"""LC-S1 — the result does not depend on how Lovable pages its answer (QA-01, C-L-06).

Through the real `lovable_source.HttpTransport` and the PostgREST stand-in
behind its ``opener=`` seam (`tests/lovable_postgrest_fake.py`, built by the
transport stream; this suite builds no second one): page size, the server's
row cap and the order of rows that tie do not change what a run writes, and a
server cap below the page size is refused, never read as the end of the data.

Skipped, with the reason, until that module is on the branch.
"""

from __future__ import annotations

import pytest

from app.legacy_import import lovable_source
from app.legacy_import.lovable_sync_models import SyncRunStatus
from tests.lovable_contract import oracle
from tests.lovable_contract.world import (
    BOOKKEEPING_TABLES,
    build_world,
    changed_tables,
    ref,
    table_hashes,
)

postgrest = pytest.importorskip(
    "tests.lovable_postgrest_fake",
    reason="LC-S1 needs the PostgREST fake of the transport stream (QA-01); "
    "it activates when tests/lovable_postgrest_fake.py lands",
)

from tests.lovable_contract.fixtures import pilot_on, world  # noqa: E402, F401 - fixtures

pytestmark = pytest.mark.django_db

PAGE = 4


def _http(world, **fake):
    opener = postgrest.PostgrestFake(source=world.lovable.select, **fake)
    return lovable_source.HttpTransport(postgrest.config(), opener=opener), opener


def _scenario(monkeypatch, count: int = 10):
    monkeypatch.setattr(lovable_source, "PAGE_SIZE", PAGE)
    world = build_world(count)  # baseline over the in-process fake
    for number in (1, 5, 9)[: 2 if count < 9 else 3]:
        world.lovable.edit(ref(number), teema=f"Kontraktiteema {ref(number)} (Lovable)")
    return world


@pytest.mark.parametrize(
    "fake",
    [{}, {"max_rows": PAGE}, {"shuffle_ties": True}, {"max_rows": PAGE * 3, "shuffle_ties": True}],
    ids=["plain", "cap equals page", "ties shuffled", "cap above page"],
)
def test_paging_the_server_cap_and_tie_order_do_not_change_the_result(
    db, pilot_on, monkeypatch, fake
):
    """LC-S1 (+ LC-F1, LC-B2 through HTTP): every row is read across several
    pages; the run writes what the in-process reading writes; the rerun writes
    nothing; only GET requests (and the sign-in) reach Lovable."""
    world = _scenario(monkeypatch)
    transport, opener = _http(world, **fake)
    run = world.run(transport=transport)
    assert run.status == SyncRunStatus.SUCCEEDED, run.error
    assert oracle.differences(world.lovable, today=world.today) == []
    assert len(opener.reads("topic")) > 2  # really paged
    assert opener.methods <= {"GET", "POST"}
    transport, _ = _http(world, **fake)
    assert not world.run(transport=transport).decisions.filter(outcome="APPLIED").exists()


def test_a_server_cap_below_the_page_size_never_truncates_the_reading(db, pilot_on, monkeypatch):
    """LC-S1 / C-L-06: a server that silently returns fewer rows than the page
    asks for never makes a smaller source: the run either reads every row (the
    result is the plain run's) or refuses and writes no business data.

    Five topics, pages of four, a cap of three: a reader that takes a short page
    for the end sees three topics and two «missing» — under the missing-topic
    breaker (5), so only the transport itself can keep the reading whole.
    Observed at `f3eabea7` with the PostgREST fake: SUCCEEDED with two topics
    silently missing."""
    from app.legacy_import.lovable_sync_models import SyncOutcome

    world = _scenario(monkeypatch, count=5)
    transport, _ = _http(world, max_rows=PAGE - 1)
    before = table_hashes()
    run = world.run(transport=transport)
    if run.status == SyncRunStatus.FAILED:
        assert changed_tables(before, table_hashes()) <= BOOKKEEPING_TABLES
        return
    assert run.status == SyncRunStatus.SUCCEEDED, run.error
    assert not run.decisions.filter(outcome=SyncOutcome.SOURCE_MISSING).exists()
    assert run.counts["source_live"] == len(world.lovable.rows)
    assert oracle.differences(world.lovable, today=world.today) == []
