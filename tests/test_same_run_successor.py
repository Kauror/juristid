# ruff: noqa: F811 - pytest fixtures imported from the sync suites are used as arguments
"""A successor Lovable created tonight converges its predecessor tonight
(audit 2026-11-11 LS-01, the root cause of ST-007).

The reference world of `tests/test_r4_authority.py`: 2026_140 continues under
2026_259, which appears in Lovable as a new topic. The cohort is planned before
the creation, so 140 is held SUCCESSOR_NOT_IN_JURISTID; after the creation only
such plans are planned again, through `apply_matter`, under the same gates:
an APPLY night that writes, every read-back good so far, and the Teemad-written
breaker counting the re-plan. Every value is invented.
"""

from __future__ import annotations

from typing import Any

from app.legacy_import import lovable_sync
from app.legacy_import.lovable_sync_models import SyncRunStatus
from tests.test_lovable_pilot import pilot_on, world  # noqa: F401
from tests.test_lovable_sync import _decisions, _matter, _run
from tests.test_r4_authority import _at, _lovable_now, reference  # noqa: F401


def test_a_failed_read_back_of_the_successor_never_supersedes_the_predecessor(
    reference, monkeypatch
):
    """Regression: LS-01 — 2026_259 is created but does not read back as
    written: the run stops writing, and 2026_140 stays open, held as before."""
    real = lovable_sync.verify_matter

    def verify(plan: Any) -> bool:
        return False if plan.state.reference == "2026_259" else real(plan)

    monkeypatch.setattr(lovable_sync, "verify_matter", verify)
    _lovable_now(reference["source"])
    run = _run(reference, at=_at(0))
    assert run.status == SyncRunStatus.FAILED and run.error.startswith("VERIFY"), run.error
    assert run.counts["new_topics_created"] == 1
    assert _matter("2026_140").is_open
    assert _matter("2026_140").superseded_by_id is None
    found = _decisions(run, "2026_140")
    assert found["stage"].reason == lovable_sync.SUCCESSOR_MISSING


def test_the_re_plan_is_counted_once_against_the_teemad_written_breaker(reference, monkeypatch):
    """Regression: LS-01 — breaker accounting includes the re-plan: 2026_140 is
    one of the night's changed Teemad (its source changed), so its re-plan is
    counted once, never twice; at exactly the limit the night still writes."""
    _lovable_now(reference["source"])
    monkeypatch.setattr(lovable_sync, "MAX_CHANGED_MATTERS", CHANGED)
    run = _run(reference, at=_at(0))
    assert run.status == SyncRunStatus.SUCCEEDED, run.error
    assert run.counts["changed_source"] == CHANGED
    assert not _matter("2026_140").is_open


def test_a_night_over_the_breaker_re_plans_nothing(reference, monkeypatch):
    """Regression: LS-01 — under the same breaker: a night over it writes
    nothing, creates nothing and re-plans nothing."""
    _lovable_now(reference["source"])
    monkeypatch.setattr(lovable_sync, "MAX_CHANGED_MATTERS", CHANGED - 1)
    run = _run(reference, at=_at(0))
    assert (run.status, run.error) == (SyncRunStatus.REVIEW_ONLY, lovable_sync.BREAKER)
    assert run.counts["new_topics_created"] == 0
    assert _matter("2026_140").is_open
    again = _run(reference, at=_at(24))
    assert again.status == SyncRunStatus.REVIEW_ONLY


#: Teemad of the reference night with a source change, 2026_140 among them.
CHANGED = 8
