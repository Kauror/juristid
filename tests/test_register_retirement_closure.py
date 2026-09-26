"""The register's retirements end what the Matter owed, as every closure does (ENG-006).

Three paths make a Matter stop being current work: a person closing it
(`close_matter`), the final register retiring it (`retire_from_current_register`,
used by the final cutover and the recurring refresh) and the historical default
(`mark_historical_archive_inactive`). Only the first ended the live obligations —
the open step, a planned `Kodulehe ülevaade` and an open `Kaasamine` feedback
wait — so the other two left them owed on a closed file, uncancellable (both
cancel paths refuse a closed Matter) and back in somebody's queue the day the
register re-activated it.

Now one helper, `end_live_work_for_closure`, is called by all three, each still
recording its own lifecycle event; and the planners hold back a Matter carrying
live or authored native work for review — re-asked under the row lock at apply,
so a plan minutes old cannot retire work added since.

Synthetic data only: the cutover world of `tests/synthetic_cutover.py`, and the
real services and transactions throughout.
"""

from __future__ import annotations

import datetime as dt

import pytest
from django.utils import timezone

from app.audit.enums import ChangeEventType
from app.audit.models import ChangeEvent
from app.core.invariants import SERVICE_RULES, check_domain_invariants
from app.legacy_import import historical_cutover
from app.legacy_import.final_cutover import (
    Action,
    ReviewReason,
    apply_cutover_plan,
    build_cutover_plan,
)
from app.legacy_import.models import OutreachChannel, RegisterEngagementImport
from app.matters import services
from app.matters.enums import EngagementKind, RecordMode, WebsiteOverviewStatus
from app.matters.models import Matter, MatterEngagement, MatterWebsiteOverview
from app.matters.work_items import open_feedback_waits, work_items
from app.workflow.enums import ActionStatus
from app.workflow.models import NextAction
from app.workflow.services import set_next_action
from tests.synthetic_cutover import (
    FINAL_SNAPSHOT,
    RETIRING_NO_PLANS,
    approve_snapshot,
    build_world,
)
from tests.test_historical_cutover import register_matter

pytestmark = pytest.mark.django_db


@pytest.fixture
def reviewed(monkeypatch: pytest.MonkeyPatch) -> None:
    approve_snapshot(monkeypatch, sha256=FINAL_SNAPSHOT)


@pytest.fixture
def world():
    return build_world()


def _plan():
    return build_cutover_plan(snapshot_sha256=FINAL_SNAPSHOT)


def _candidate(plan, title):
    return next(c for c in plan.candidates if c.matter.title == title)


def _events(matter, event_type) -> int:
    return ChangeEvent.objects.filter(matter=matter, event_type=event_type).count()


def _wait(matter, actor) -> MatterEngagement:
    engagement = services.add_engagement(
        matter=matter,
        kind=EngagementKind.SURVEY,
        title="Sünteetilised liikmed",
        occurred_on=timezone.localdate() - dt.timedelta(days=3),
        feedback_deadline=timezone.localdate() + dt.timedelta(days=10),
        actor=actor,
    )
    assert engagement.has_open_feedback_wait
    return engagement


def _plan_overview(matter, actor) -> MatterWebsiteOverview:
    return services.plan_website_overview(matter=matter, actor=actor)


def _step(matter, actor) -> NextAction:
    return set_next_action(
        matter=matter,
        text="Sünteetiline järgmine samm.",
        actor=actor,
        target_date=timezone.localdate() + dt.timedelta(days=5),
        kind="DO",
        date_semantics="DEADLINE",
    )


def _nothing_owed(matter) -> None:
    assert not NextAction.objects.filter(matter=matter, status=ActionStatus.OPEN).exists()
    assert not MatterWebsiteOverview.objects.filter(
        matter=matter, status=WebsiteOverviewStatus.PLANNED
    ).exists()
    assert not MatterEngagement.objects.filter(
        matter=matter, feedback_deadline__isnull=False, feedback_closed_at__isnull=True
    ).exists()


# =========================================================================
# 1–4 — retirement ends every obligation, with the canonical events
# =========================================================================


def test_retiring_cancels_a_planned_overview_with_its_own_event(world) -> None:
    matter = world[RETIRING_NO_PLANS]
    overview = _plan_overview(matter, world.people.sandra)

    services.retire_from_current_register(matter=matter, actor=None)

    overview.refresh_from_db()
    assert overview.status == WebsiteOverviewStatus.CANCELLED
    assert _events(matter, ChangeEventType.WEBSITE_OVERVIEW_CANCELLED) == 1
    assert _events(matter, ChangeEventType.MATTER_REGISTER_CUTOVER_RETIRED) == 1
    # The truthful cause: nobody closed it, so no ordinary closure is recorded.
    assert _events(matter, ChangeEventType.MATTER_CLOSED) == 0
    matter.refresh_from_db()
    assert (matter.disposition, matter.closed_at, matter.closed_by) == ("", None, None)


def test_retiring_closes_an_open_feedback_wait_with_its_own_event(world) -> None:
    matter = world[RETIRING_NO_PLANS]
    engagement = _wait(matter, world.people.sandra)

    services.retire_from_current_register(matter=matter, actor=None)

    engagement.refresh_from_db()
    assert not engagement.has_open_feedback_wait
    assert _events(matter, ChangeEventType.ENGAGEMENT_FEEDBACK_CLOSED) == 1


def test_retiring_ends_all_three_at_once(world) -> None:
    matter = world[RETIRING_NO_PLANS]
    _plan_overview(matter, world.people.sandra)
    _wait(matter, world.people.sandra)
    action = _step(matter, world.people.sandra)

    services.retire_from_current_register(matter=matter, actor=None)

    _nothing_owed(matter)
    action.refresh_from_db()
    assert action.status == ActionStatus.CANCELLED
    assert _events(matter, ChangeEventType.NEXT_ACTION_CANCELLED) == 1
    assert _events(matter, ChangeEventType.WEBSITE_OVERVIEW_CANCELLED) == 1
    assert _events(matter, ChangeEventType.ENGAGEMENT_FEEDBACK_CLOSED) == 1


def test_the_historical_default_ends_them_the_same_way(specialist) -> None:
    matter = register_matter(2014)
    _plan_overview(matter, specialist)
    _wait(matter, specialist)

    services.mark_historical_archive_inactive(matter=matter, actor=None)

    _nothing_owed(matter)
    assert _events(matter, ChangeEventType.MATTER_HISTORICAL_CUTOVER_CLOSED) == 1
    assert _events(matter, ChangeEventType.MATTER_CLOSED) == 0


def test_close_matter_still_ends_them_through_the_same_helper(normal_matter, specialist) -> None:
    _plan_overview(normal_matter, specialist)
    _wait(normal_matter, specialist)
    _step(normal_matter, specialist)

    services.close_matter(matter=normal_matter, disposition="COMPLETED", actor=specialist)

    _nothing_owed(normal_matter)
    assert _events(normal_matter, ChangeEventType.MATTER_CLOSED) == 1


# =========================================================================
# 5 — the planner holds native work back; untouched history still retires
# =========================================================================


@pytest.mark.parametrize(
    ("make", "reason"),
    [
        (lambda m, a: _wait(m, a), ReviewReason.OPEN_FEEDBACK_WAIT),
        (lambda m, a: _plan_overview(m, a), ReviewReason.PLANNED_WEBSITE_OVERVIEW),
        (
            lambda m, a: services.record_procedural_development(
                matter=m,
                title="Sünteetiline menetluse samm",
                occurred_on=timezone.localdate() - dt.timedelta(days=2),
                actor=a,
            ),
            ReviewReason.AUTHORED_RECORDS,
        ),
        (
            lambda m, a: services.add_engagement(
                matter=m, kind=EngagementKind.SURVEY, title="Sünteetiline ring", actor=a
            ),
            ReviewReason.AUTHORED_RECORDS,
        ),
    ],
    ids=["feedback-wait", "planned-overview", "marge", "native-kaasamine"],
)
def test_native_work_holds_a_retirement_back_for_review(world, reviewed, make, reason) -> None:
    matter = world[RETIRING_NO_PLANS]
    assert _candidate(_plan(), RETIRING_NO_PLANS).action == Action.RETIRE

    make(matter, world.people.sandra)

    candidate = _candidate(_plan(), RETIRING_NO_PLANS)
    assert candidate.action == Action.REVIEW_REQUIRED
    assert candidate.review_reason == reason
    apply_cutover_plan(_plan())
    assert world.refresh(RETIRING_NO_PLANS).is_open


def test_a_round_the_register_outreach_filed_is_not_native_work(world, reviewed) -> None:
    """An imported `Kaasamine` is the register's, not a person's — it does not hold."""
    matter = world[RETIRING_NO_PLANS]
    engagement = services.add_engagement(
        matter=matter, kind=EngagementKind.EMAIL_CAMPAIGN, title="Sünteetiline kampaania"
    )
    RegisterEngagementImport.objects.create(
        engagement=engagement,
        matter=matter,
        channel=OutreachChannel.EMAIL_CAMPAIGN,
        source_key="kampaania-1",
        mapping_sha256="0" * 64,
        created_engagement=True,
    )

    assert _candidate(_plan(), RETIRING_NO_PLANS).action == Action.RETIRE
    result = apply_cutover_plan(_plan())

    assert result.retired >= 1
    assert not world.refresh(RETIRING_NO_PLANS).is_open


def test_a_matter_with_only_history_still_retires(world, reviewed) -> None:
    result = apply_cutover_plan(_plan())

    matter = world.refresh(RETIRING_NO_PLANS)
    assert (matter.record_mode, matter.is_open) == (RecordMode.ARCHIVE, False)
    assert result.held_for_review == 0


# =========================================================================
# 6–7 — nothing revives, and repeating is a no-op
# =========================================================================


def test_reactivation_does_not_revive_what_retirement_ended(world) -> None:
    matter = world[RETIRING_NO_PLANS]
    overview = _plan_overview(matter, world.people.sandra)
    engagement = _wait(matter, world.people.sandra)
    services.retire_from_current_register(matter=matter, actor=None)
    matter.refresh_from_db()

    services.reactivate_historical_matter(
        matter=matter, actor=world.people.sandra, attestation="Töö jätkub tegelikult."
    )

    matter.refresh_from_db()
    assert matter.is_open and matter.record_mode == RecordMode.FULL
    overview.refresh_from_db()
    engagement.refresh_from_db()
    assert overview.status == WebsiteOverviewStatus.CANCELLED
    assert not engagement.has_open_feedback_wait
    assert not open_feedback_waits(world.people.sandra).filter(matter=matter).exists()
    assert not [item for item in work_items(world.people.sandra) if item.matter.pk == matter.pk]


def test_retiring_twice_writes_nothing_the_second_time(world) -> None:
    matter = world[RETIRING_NO_PLANS]
    _plan_overview(matter, world.people.sandra)
    _wait(matter, world.people.sandra)
    services.retire_from_current_register(matter=matter, actor=None)
    events = ChangeEvent.objects.filter(matter=matter).count()

    services.retire_from_current_register(matter=matter, actor=None)
    services.end_live_work_for_closure(matter=matter, actor=None)

    assert ChangeEvent.objects.filter(matter=matter).count() == events


def test_a_repeated_cutover_is_idempotent(world, reviewed) -> None:
    first = apply_cutover_plan(_plan())
    events = ChangeEvent.objects.count()

    second = apply_cutover_plan(_plan())

    assert first.retired >= 1
    assert second.retired == 0
    assert ChangeEvent.objects.count() == events


# =========================================================================
# 8–9 — all or nothing, and no stale decision
# =========================================================================


def test_a_failure_after_one_side_effect_rolls_the_whole_retirement_back(
    world, monkeypatch
) -> None:
    matter = world[RETIRING_NO_PLANS]
    overview = _plan_overview(matter, world.people.sandra)
    _wait(matter, world.people.sandra)

    def boom(**kwargs):
        raise RuntimeError("synthetic failure after the overview was cancelled")

    monkeypatch.setattr(services, "close_open_feedback_waits_for_closure", boom)

    with pytest.raises(RuntimeError):
        services.retire_from_current_register(matter=matter, actor=None)

    matter.refresh_from_db()
    overview.refresh_from_db()
    assert (matter.record_mode, matter.is_open) == (RecordMode.FULL, True)
    assert overview.status == WebsiteOverviewStatus.PLANNED
    assert _events(matter, ChangeEventType.WEBSITE_OVERVIEW_CANCELLED) == 0
    assert _events(matter, ChangeEventType.MATTER_REGISTER_CUTOVER_RETIRED) == 0


def test_work_added_after_the_plan_is_seen_under_the_lock(world, reviewed) -> None:
    """H-19. The plan said RETIRE; a wait opened since then keeps the Matter current."""
    plan = _plan()
    assert _candidate(plan, RETIRING_NO_PLANS).action == Action.RETIRE

    engagement = _wait(world[RETIRING_NO_PLANS], world.people.sandra)
    result = apply_cutover_plan(plan)

    assert result.held_for_review == 1
    assert world.refresh(RETIRING_NO_PLANS).is_open
    engagement.refresh_from_db()
    assert engagement.has_open_feedback_wait
    # And the next plan says why.
    assert _candidate(_plan(), RETIRING_NO_PLANS).review_reason == ReviewReason.OPEN_FEEDBACK_WAIT


def test_the_historical_planner_holds_live_work_back(specialist) -> None:
    waiting = register_matter(2014)
    _wait(waiting, specialist)
    plan = historical_cutover.build_cutover_plan(cutover_year=2026)

    held = next(c for c in plan.candidates if c.matter.pk == waiting.pk)
    assert held.review_reason == historical_cutover.ReviewReason.OPEN_FEEDBACK_WAIT


def test_the_historical_apply_re_asks_under_the_lock(specialist) -> None:
    late = register_matter(2014)
    plan = historical_cutover.build_cutover_plan(cutover_year=2026)
    assert [c for c in plan.closable if c.matter.pk == late.pk]
    _plan_overview(late, specialist)

    result = historical_cutover.apply_cutover_plan(plan)

    assert result.held_for_review == 1
    late.refresh_from_db()
    assert late.is_open
    replanned = historical_cutover.build_cutover_plan(cutover_year=2026)
    held = next(c for c in replanned.candidates if c.matter.pk == late.pk)
    assert held.review_reason == historical_cutover.ReviewReason.PLANNED_WEBSITE_OVERVIEW


# =========================================================================
# 10 — the surfaces agree, and the integrity queries read zero
# =========================================================================


def test_after_retirement_no_surface_counts_it_as_work(world) -> None:
    matter = world[RETIRING_NO_PLANS]
    matter.owner = world.people.sandra
    matter.save(update_fields=["owner"])
    _plan_overview(matter, world.people.sandra)
    _wait(matter, world.people.sandra)
    _step(matter, world.people.sandra)
    assert [item for item in work_items(world.people.sandra) if item.matter.pk == matter.pk]

    services.retire_from_current_register(matter=matter, actor=None)

    assert not [item for item in work_items(world.people.sandra) if item.matter.pk == matter.pk]
    assert not open_feedback_waits(world.people.sandra).filter(matter=matter).exists()


def test_the_integrity_queries_read_zero_after_every_closure_path(world) -> None:
    retired = world[RETIRING_NO_PLANS]
    for make in (_plan_overview, _wait, _step):
        make(retired, world.people.sandra)
    services.retire_from_current_register(matter=retired, actor=None)

    kinds = {finding.kind for finding in check_domain_invariants().findings}
    assert not kinds & {
        "closed-matter-open-next-action",
        "closed-matter-planned-website-overview",
        "closed-matter-open-feedback-wait",
    }


def test_the_integrity_queries_name_a_row_a_bypass_left_behind(world) -> None:
    """The audit's Q02/Q16/Q17 still catch the state, however it was reached."""
    matter = world[RETIRING_NO_PLANS]
    overview = _plan_overview(matter, world.people.sandra)
    engagement = _wait(matter, world.people.sandra)
    action = _step(matter, world.people.sandra)
    Matter.objects.filter(pk=matter.pk).update(is_open=False, record_mode=RecordMode.ARCHIVE)

    findings = {(f.kind, f.subject) for f in check_domain_invariants().findings}

    assert ("closed-matter-open-next-action", str(action.pk)) in findings
    assert ("closed-matter-planned-website-overview", str(overview.pk)) in findings
    assert ("closed-matter-open-feedback-wait", str(engagement.pk)) in findings
    assert {kind for kind, _ in findings} <= SERVICE_RULES
