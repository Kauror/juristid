# ruff: noqa: F811 - pytest fixtures imported from the sync suites are used as arguments
"""The ordinary changed-Teemad limit is 100 a night (docs/adr/0213 §14), on synthetic data.

The owner raised `MAX_CHANGED_MATTERS` from 20 to 100 on 25.11.2026: the first r92
night reached 19. Everything else stays: recorded hand-overs keep their own 150
(docs/adr/0220 §2), a hand-over mixed with anything else is ordinary, every other
breaker keeps its value and works on its own, a supervised run may still set the
limit for itself, and an older run's summary keeps the limit it ran under.

The boundaries are tested on `over_teemad_limits`, the one comparison the night
makes, at the real limits; the night itself is tested on the small register world,
which proves the night calls that comparison with its real counts and limit.
Every name is invented.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

import pytest

from app.audit.models import ChangeEvent
from app.legacy_import import lovable_sync
from app.legacy_import.lovable_sync_models import LovableSyncRun, SyncMode, SyncRunStatus
from tests.test_lovable_handover_limit import OPEN, _hand_over, handover_source  # noqa: F401
from tests.test_lovable_pilot import pilot_on, world  # noqa: F401
from tests.test_lovable_sync import _run
from tests.test_ls_b_engine import NIGHT, register  # noqa: F401

HANDOVERS = lovable_sync.MAX_HANDOVER_MATTERS


def _at(hours: int) -> dt.datetime:
    return NIGHT + dt.timedelta(hours=hours)


# ---------------------------------------------------------------------------
# The limits themselves
# ---------------------------------------------------------------------------


def test_the_ordinary_limit_is_100_and_every_other_limit_is_unchanged():
    assert lovable_sync.MAX_CHANGED_MATTERS == 100
    assert lovable_sync.MAX_HANDOVER_MATTERS == 150
    assert lovable_sync.MAX_RESTORED_MATTERS == 100
    assert lovable_sync.MAX_NEW_HOLDS == 25
    assert lovable_sync.MAX_NEW_CLOSURE_HOLDS == 20
    assert lovable_sync.MAX_MISSING == 5
    assert lovable_sync.MAX_NEW_TOPICS == 10


@pytest.mark.parametrize(
    ("ordinary", "handovers", "trips"),
    [
        (20, 0, False),
        (21, 0, False),
        (100, 0, False),
        (101, 0, True),
        (0, 150, False),
        (0, 151, True),
        # Both kinds in one night, each within its own limit.
        (100, 150, False),
        (21, 84, False),
        # Either over its own limit holds the night, whatever the other is.
        (101, 1, True),
        (1, 151, True),
    ],
)
def test_the_night_compares_each_kind_against_its_own_real_limit(ordinary, handovers, trips):
    limit = lovable_sync.MAX_CHANGED_MATTERS
    assert lovable_sync.over_teemad_limits(ordinary, handovers, limit) is trips


def test_hand_overs_never_spend_the_ordinary_limit_and_ordinary_never_the_hand_overs():
    """150 hand-overs leave the ordinary 100 whole, and the other way round."""
    assert not lovable_sync.over_teemad_limits(100, HANDOVERS, 100)
    assert lovable_sync.over_teemad_limits(101, 0, 100)
    assert lovable_sync.over_teemad_limits(0, HANDOVERS + 1, 1000)


def test_a_supervised_limit_replaces_the_default_both_ways():
    """`run_sync(max_changed=…)` is the run's limit, above or below the default."""
    assert not lovable_sync.over_teemad_limits(150, 0, 150)
    assert lovable_sync.over_teemad_limits(6, 0, 5)
    assert lovable_sync.over_teemad_limits(1, 0, 0)
    # The override is for ordinary changes only; the hand-overs' limit is fixed.
    assert lovable_sync.over_teemad_limits(0, HANDOVERS + 1, 10_000)


# ---------------------------------------------------------------------------
# The night (the small register world)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_a_default_night_compares_its_real_counts_with_the_new_limit(register, monkeypatch):
    seen: list[tuple[int, int, int]] = []
    real = lovable_sync.over_teemad_limits

    def spy(ordinary: int, handovers: int, change_limit: int) -> bool:
        seen.append((ordinary, handovers, change_limit))
        return real(ordinary, handovers, change_limit)

    monkeypatch.setattr(lovable_sync, "over_teemad_limits", spy)
    for reference in OPEN:
        register["source"].edit(reference, teema=f"Näidisteema {reference} parandatud")
    run = _run(register, at=_at(0))
    assert run.status == SyncRunStatus.SUCCEEDED, run.error
    assert seen == [(len(OPEN), 0, 100)]
    assert (run.counts["changed_source"], run.counts["breaker_limit"]) == (len(OPEN), 100)
    assert run.counts["handover_limit"] == 150
    assert f"Lovable'i muudatusega teemasid: {len(OPEN)}/100; " in " ".join(
        lovable_sync.summary_lines(run)
    )


@pytest.mark.django_db
def test_one_over_the_limit_holds_the_whole_night_and_shows_the_limit(register, monkeypatch):
    """The same rule at the world's scale: 4 ordinary Teemad against a limit of 3."""
    monkeypatch.setattr(lovable_sync, "MAX_CHANGED_MATTERS", len(OPEN) - 1)
    for reference in OPEN:
        register["source"].edit(reference, teema=f"Näidisteema {reference} parandatud")
    events = ChangeEvent.objects.count()
    run = _run(register, at=_at(0))
    assert (run.status, run.error) == (SyncRunStatus.REVIEW_ONLY, lovable_sync.BREAKER)
    assert ChangeEvent.objects.count() == events
    assert run.counts["breaker_limit"] == len(OPEN) - 1
    assert f"Lovable'i muudatusega teemasid: {len(OPEN)}/{len(OPEN) - 1}; " in " ".join(
        lovable_sync.summary_lines(run)
    )


@pytest.mark.django_db
def test_hand_overs_and_ordinary_changes_within_their_limits_both_apply(
    handover_source, monkeypatch
):
    monkeypatch.setattr(lovable_sync, "MAX_CHANGED_MATTERS", 2)
    monkeypatch.setattr(lovable_sync, "MAX_HANDOVER_MATTERS", 2)
    source = handover_source["source"]
    _hand_over(source, OPEN[:2], "Jaan Näidis")
    source.edit(OPEN[2], jargmiseks="Uus samm", tegevuse_tahtaeg="2099-04-01")
    source.edit(OPEN[3], jargmiseks="Teine samm", tegevuse_tahtaeg="2099-04-02")
    run = _run(handover_source, at=_at(0))
    assert run.status == SyncRunStatus.SUCCEEDED, run.error
    assert (run.counts["changed_handovers"], run.counts["changed_source"]) == (2, 2)


@pytest.mark.django_db
def test_a_hand_over_mixed_with_a_substantive_change_spends_the_ordinary_limit(
    handover_source, monkeypatch
):
    """Two recorded hand-overs, one also retitled: one hand-over, one ordinary."""
    monkeypatch.setattr(lovable_sync, "MAX_CHANGED_MATTERS", 1)
    source = handover_source["source"]
    _hand_over(source, OPEN[:2], "Jaan Näidis")
    source.edit(OPEN[0], teema="Näidisteema, uus pealkiri")
    run = _run(handover_source, at=_at(0))
    assert run.status == SyncRunStatus.SUCCEEDED, run.error
    assert (run.counts["changed_handovers"], run.counts["changed_source"]) == (1, 1)


@pytest.mark.django_db
def test_a_supervised_run_still_sets_its_own_limit(register):
    for reference in OPEN:
        register["source"].edit(reference, teema=f"Näidisteema {reference} parandatud")
    run = _run(register, at=_at(0), max_changed=len(OPEN) - 1)
    assert (run.status, run.error) == (SyncRunStatus.REVIEW_ONLY, lovable_sync.BREAKER)
    assert run.counts["breaker_limit"] == len(OPEN) - 1


# ---------------------------------------------------------------------------
# History keeps the limit it ran under
# ---------------------------------------------------------------------------


def _past_run(counts: dict[str, Any]) -> LovableSyncRun:
    return LovableSyncRun(
        started_at=dt.datetime(2026, 11, 20, 22, 15, tzinfo=dt.UTC),
        status=SyncRunStatus.SUCCEEDED,
        mode=SyncMode.APPLY,
        counts=counts,
    )


def test_a_run_shows_the_limit_it_recorded_not_todays_default():
    lines = " ".join(
        lovable_sync.summary_lines(_past_run({"changed_source": 9, "breaker_limit": 20}))
    )
    assert "Lovable'i muudatusega teemasid: 9/20; " in lines
    lines = " ".join(
        lovable_sync.summary_lines(_past_run({"changed_source": 9, "breaker_limit": 85}))
    )
    assert "Lovable'i muudatusega teemasid: 9/85; " in lines


def test_a_run_from_before_limits_were_recorded_shows_the_limit_of_its_time():
    lines = " ".join(lovable_sync.summary_lines(_past_run({"changed_source": 9})))
    assert "Lovable'i muudatusega teemasid: 9/20; " in lines
    assert lovable_sync.CHANGED_LIMIT_BEFORE_RECORDING == 20
