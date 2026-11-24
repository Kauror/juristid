# ruff: noqa: F811 - the fixtures imported from fixtures.py are used as arguments
"""Two figures of the run summary, counted after the run's own writes.

Reporting only (ops report A3/A4, 24.11.2026): what the run writes, holds or
refuses is unchanged; only `stopped_active` and `outside_cohort` are counted
against the state the run left, not the one it started from.
"""

from __future__ import annotations

import pytest

from app.legacy_import import lovable_sync
from app.legacy_import.lovable_sync_models import (
    LovableSyncDecision,
    SyncMode,
    SyncOutcome,
    SyncRunStatus,
)
from tests.lovable_contract.fixtures import pilot_on, world  # noqa: F401 - fixtures
from tests.lovable_contract.world import lovable_row, ref, source_id

pytestmark = pytest.mark.django_db


def _register(run):
    return run.counts["by_cohort"][lovable_sync.REGISTER_COHORT]


def test_a_teema_the_run_closes_is_not_counted_finished_but_open(world):
    """A4: «jõustunud» in Lovable. Planned only (COMPARE), the Teema stays open
    and is counted; the APPLY run that closes it no longer counts it."""
    world.lovable.edit(ref(1), hetkeseis="jõustunud", jargmiseks="", tegevuse_tahtaeg=None)

    compare = world.run(SyncMode.COMPARE)
    assert compare.status == SyncRunStatus.SUCCEEDED, compare.error
    assert world.matter(ref(1)).is_open
    assert compare.counts["stopped_active"] == 1
    assert _register(compare)["stopped_active"] == 1

    apply = world.run()
    assert apply.status == SyncRunStatus.SUCCEEDED, apply.error
    stage = LovableSyncDecision.objects.get(run=apply, state__reference=ref(1), field="stage")
    assert stage.outcome == SyncOutcome.APPLIED
    assert not world.matter(ref(1)).is_open
    assert apply.counts["stopped_active"] == 0
    assert _register(apply)["stopped_active"] == 0
    # Still counted as finished in Lovable: only «open in Juristid» changed.
    assert _register(apply)["activity"]["FINISHED"] == 1


def test_a_topic_the_run_creates_is_counted_once(world):
    """A3: a new operational topic the run creates is in the register's checked
    count, never also outside the synchronised cohorts."""
    new = ref(50)
    world.lovable.put(source_id(new), lovable_row(new), version=1)
    archive = ref(201)
    world.lovable.put(source_id(archive), lovable_row(archive, operatiivne_ulatus=False), version=2)

    run = world.run()
    assert run.status == SyncRunStatus.SUCCEEDED, run.error
    assert run.counts["new_topics_created"] == 1
    assert world.matter(new).is_open
    assert _register(run)["checked"] == 4
    assert run.counts["outside_cohort"] == 1  # the archive topic only
    assert run.counts["checked"] + run.counts["outside_cohort"] == len(world.lovable.rows)
