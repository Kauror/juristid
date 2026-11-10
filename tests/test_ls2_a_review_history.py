"""Reviewer E (live-sync round 2): adversarial checks on historical closure semantics,
kept by LS2-A with the generic-path finding (P2-1) turned into refusals.

Synthetic data only; reuses the fixtures of tests/test_ls_a_history_closure.py.
"""

from __future__ import annotations

import copy
from typing import Any

import pytest

from app.legacy_import import lovable_backfill, lovable_pilot
from app.legacy_import.lovable_pilot import HISTORY_COHORT, plan_digest
from app.legacy_import.lovable_reading import ReadingRefused, activity, closure_reading, read_topic
from app.matters.models import Matter
from tests.test_ls_a_history_closure import AS_OF, BACKUP, _select, history, pilot_on  # noqa: F401
from tests.test_ls_b_reading import bundle

pytestmark = pytest.mark.django_db


def _stale_and_invented(package: dict[str, Any]) -> dict[str, Any]:
    """Round 1's package shape: reader v1, every unknown closure MONITORING_STOPPED."""
    stale = copy.deepcopy(package)
    stale["mapping_version"] = "lovable-reading/1"
    stale["reviewer"] = "machine reader lovable-reading/1"
    for topic in stale["topics"]:
        topic["reading"]["reader_version"] = "lovable-reading/1"
        if not topic.get("disposition"):
            topic["disposition"] = "MONITORING_STOPPED"
    return stale


def test_the_backfill_refuses_the_stale_invented_package(history, pilot_on):  # noqa: F811
    _ledger, selection = _select(history)
    stale = _stale_and_invented(selection.package)
    problems = lovable_backfill.validate(stale)
    assert any(lovable_backfill.STALE_READER in p for p in problems)
    assert any("is not what the source states" in p for p in problems)


def test_the_generic_pilot_path_refuses_the_stale_invented_history_package(
    history,  # noqa: F811
    pilot_on,  # noqa: F811
):
    """Reviewer E's P2-1, fixed: `lovable_pilot validate/plan/apply` (the generic
    command) carries the backfill's gates for the lovable-history cohort, so a
    package read by reader v1 with invented MONITORING_STOPPED closures is refused
    there too and nothing is written."""
    _ledger, selection = _select(history)
    stale = _stale_and_invented(selection.package)
    problems = lovable_pilot.validate_package(stale).problems
    assert any(lovable_backfill.STALE_READER in p for p in problems)
    assert any("is not what the source states" in p for p in problems)
    plan = lovable_pilot.build_plan(stale)
    assert any(lovable_backfill.STALE_READER in p for p in plan.problems)
    before = Matter.all_objects.count()
    with pytest.raises(lovable_pilot.LovablePilotError, match="The plan has problems"):
        lovable_pilot.apply_plan(
            plan,
            operator_intent=HISTORY_COHORT,
            expect_package_sha256=plan.digest,
            expect_plan_sha256=plan_digest(plan),
            backup_set=BACKUP,
        )
    with pytest.raises(lovable_pilot.LovablePilotError, match="The plan has problems"):
        lovable_pilot.dry_run(plan)
    assert Matter.all_objects.count() == before
    assert not Matter.objects.filter(evidence_pilot_records__pilot=HISTORY_COHORT).exists()


def test_the_generic_pilot_path_refuses_a_history_package_bound_to_old_decisions(
    history,  # noqa: F811
    pilot_on,  # noqa: F811
):
    from app.legacy_import.lovable_inventory_models import InventoryDecisionKind
    from tests.test_ls_a_inventory import decide

    _ledger, selection = _select(history)
    decide(2, InventoryDecisionKind.EXCLUDE)
    plan = lovable_pilot.build_plan(selection.package)
    assert any(lovable_backfill.DECISIONS_CHANGED in p for p in plan.problems)


def test_the_current_history_package_still_plans_through_the_generic_path(
    history,  # noqa: F811
    pilot_on,  # noqa: F811
):
    _ledger, selection = _select(history)
    assert lovable_pilot.validate_package(selection.package).problems == []
    assert lovable_pilot.build_plan(selection.package).problems == []


def test_activity_and_closure_fold_the_status_differently():
    """Documentation, by design: `activity` folds diacritics (joustunud ==
    jõustunud); `closure_reading` does not.
    An operational topic Lovable reports as «joustunud» is FINISHED but its closure is
    UNKNOWN, so the machine reader refuses it (CLOSURE_REASON_UNKNOWN)."""
    b = bundle("2026_901", operatiivne_ulatus=True, aasta=2026, hetkeseis="joustunud")
    assert activity(b) == "FINISHED"
    assert closure_reading(b)[2] == "UNKNOWN"
    with pytest.raises(ReadingRefused) as caught:
        read_topic(b, as_of=AS_OF, people={}, historical=False)
    assert caught.value.code == "CLOSURE_REASON_UNKNOWN"
