# ruff: noqa: F811 - the fixtures imported from the contract suite are used as arguments
"""TL-02: a closure from Lovable is held while Juristid-only work is live.

Owner decision of 12.11.2026 (Option A of `TL-02_DECISION_MEMO.md`), recorded in
docs/adr/0213 §10.13. Each test names the decision's point it proves:

1. the Teema stays open with a visible ``LOCAL_WORK_AT_CLOSURE`` hold;
2. all Juristid-only pending work is preserved (Lovable's own current step is
   not Juristid-only);
3. the discrepancy is visible in the run counts and the health line, explained,
   never an unexplained difference and never suppressed;
4. the owner resolves it, or the owner / an administrator releases it, attributed;
5. a hold 14 days old reaches the owner and the department head, once;
6. a release rechecks the latest reading and the work, and says what it cancels;
7. the current step and the opinion request are held with the closure;
8. repeat runs are idempotent.

The world is the contract suite's (`tests/lovable_contract/world.py`): the
importer-built register, the in-process Lovable fake and the real `run_sync`.
Synthetic topics 2099_*; invented names.
"""

from __future__ import annotations

import datetime as dt

import pytest

from app.audit.models import ChangeEvent
from app.legacy_import import lovable_sync
from app.legacy_import.lovable_closure_hold import (
    HOLD_CLEARED,
    RELEASE_NOT_ALLOWED,
    hold_of,
    release_closure_hold,
    release_preview,
)
from app.legacy_import.lovable_sync_models import (
    LovableSyncDecision,
    LovableSyncState,
    SyncMode,
    SyncOutcome,
    SyncRunStatus,
)
from app.matters.closure_work import (
    CLOSURE_HOLD_BANNER,
    LIVE_WORK_CHANGED,
    LOCAL_WORK_AT_CLOSURE,
    juristid_only_work,
    live_work_at_closure,
)
from app.matters.enums import EngagementKind, WebsiteOverviewStatus
from app.matters.models import MatterEngagement, MatterWebsiteOverview
from app.matters.removal import remove_matter_record
from app.matters.services import (
    add_engagement,
    close_matter,
    close_matter_from_source,
    plan_website_overview,
)
from app.workflow.enums import ActionStatus, Disposition
from app.workflow.models import NextAction
from app.workflow.services import add_planned_action, cancel_next_action
from tests import factories
from tests.lovable_contract import oracle
from tests.lovable_contract.fixtures import pilot_on, world  # noqa: F401 - fixtures
from tests.lovable_contract.world import AVA, NIGHT, UKU, decisions, ref
from tests.refusals import refused

pytestmark = pytest.mark.django_db

CLOSED = {"hetkeseis": "jõustunud", "jargmiseks": "", "tegevuse_tahtaeg": None}


def _close_in_lovable(world, number: int = 1) -> None:
    world.lovable.edit(ref(number), **CLOSED)


def _overview(world, number: int = 1):
    return plan_website_overview(matter=world.matter(ref(number)), actor=world.person(AVA))


def _planned(world, number: int = 1, text: str = "Helistan ministeeriumi"):
    return add_planned_action(
        matter=world.matter(ref(number)), text=text, target_date=dt.date(2099, 4, 1)
    )


def _wait(world, number: int = 1, *, deadline: dt.date | None = dt.date(2099, 3, 1)):
    return add_engagement(
        matter=world.matter(ref(number)),
        kind=EngagementKind.values[0],
        title="Näidiskaasamine",
        occurred_on=dt.date(2026, 11, 1),
        feedback_deadline=deadline,
        actor=world.person(AVA),
    )


def _stage(run, number: int = 1):
    return decisions(run, ref(number))["stage"]


def _events_since(pk_set: set) -> int:
    return ChangeEvent.objects.exclude(pk__in=pk_set).count()


# ---------------------------------------------------------------------------
# The domain rule: `close_matter_from_source` refuses past Juristid-only work
# ---------------------------------------------------------------------------


WORK_KINDS = {
    "overview": lambda w: _overview(w),
    "wait_with_deadline": lambda w: _wait(w),
    "wait_without_deadline": lambda w: _wait(w, deadline=None),
    "planned_step": lambda w: _planned(w),
}


@pytest.mark.parametrize("kind", sorted(WORK_KINDS))
def test_a_source_closure_is_refused_while_juristid_only_work_is_live(world, kind):
    """Regression: TL-02 (points 1, 2, 7). One live Juristid-only item holds a
    closure from the source: the Teema stays open, the item, Lovable's current
    step, the stage and the request are unchanged, and no event is written."""
    WORK_KINDS[kind](world)
    matter = world.matter(ref(1))
    step = NextAction.objects.get(matter=matter, status=ActionStatus.OPEN)
    stage_before = matter.stage_id
    events = set(ChangeEvent.objects.values_list("pk", flat=True))
    with refused(CLOSURE_HOLD_BANNER):
        close_matter_from_source(matter=matter, disposition=Disposition.COMPLETED)
    matter.refresh_from_db()
    assert matter.is_open and matter.stage_id == stage_before
    step.refresh_from_db()
    assert step.status == ActionStatus.OPEN
    assert _events_since(events) == 0
    assert not juristid_only_work(matter).is_empty


def test_lovables_own_current_step_is_not_juristid_only_work(world):
    """Regression: TL-02 (point 2). The current step is Lovable's `jargmiseks`:
    it is live work at closure but not Juristid-only, so alone it holds
    nothing — the source closure ends it, with the run's provenance on the
    cancellation and the id in `MATTER_CLOSED.ended` (memo case 1)."""
    matter = world.matter(ref(1))
    step = NextAction.objects.get(matter=matter, status=ActionStatus.OPEN)
    assert live_work_at_closure(matter).current
    assert juristid_only_work(matter).is_empty
    _close_in_lovable(world)
    run = world.run()
    step.refresh_from_db()
    assert step.status == ActionStatus.CANCELLED
    closed = world.events_of(run).get(event_type="MATTER_CLOSED")
    assert closed.payload["ended"]["actions"] == [str(step.pk)]
    cancelled = world.events_of(run).get(event_type="NEXT_ACTION_CANCELLED", object_id=step.pk)
    assert cancelled.payload["provenance"]["sync_run"] == str(run.pk)
    assert run.counts["held_local_work"] == 0


def test_a_removed_overview_or_kaasamine_is_not_live_work(world):
    """Guard: TL-02 (point 2, the definition; passes on the base by design). A
    record taken off the file («Kustuta») is on no surface, so it holds nothing
    (QA-WORKFLOW-01 parity): Lovable's closure goes through."""
    overview = _overview(world)
    engagement = _wait(world)
    matter = world.matter(ref(1))
    for key, row in (("ulevaade", overview), ("kaasamine", engagement)):
        remove_matter_record(
            matter_id=matter.pk, kind_key=key, record_id=row.pk, actor=world.person(AVA)
        )
    planned = _planned(world)
    cancel_next_action(action=planned, actor=world.person(AVA), reason="Pole vaja")
    assert juristid_only_work(matter).is_empty
    _close_in_lovable(world)
    world.run()
    assert not world.matter(ref(1)).is_open


def test_several_kinds_are_counted_each_and_preserved_through_three_runs(world):
    """Regression: TL-02 (points 1, 2, 3, 7, 8). A planned step, an overview and
    two waits: held with each kind counted; three runs later nothing is
    cancelled, no business event was written, the baseline did not move, the
    hold's first night did not restart, and every run holds exactly one stage
    decision for it. The step and the request are held with it."""
    planned = _planned(world)
    overview = _overview(world)
    waits = [_wait(world), _wait(world, deadline=None)]
    matter = world.matter(ref(1))
    _close_in_lovable(world)
    state = LovableSyncState.objects.get(matter=matter)
    baseline = dict(state.applied)
    events = set(ChangeEvent.objects.values_list("pk", flat=True))
    runs = [world.run() for _ in range(3)]
    for run in runs:
        assert run.status == SyncRunStatus.SUCCEEDED, run.error
        assert run.counts["held_local_work"] == 1
        held = decisions(run, ref(1))
        assert (held["stage"].outcome, held["stage"].reason) == (
            SyncOutcome.REVIEW_REQUIRED,
            LOCAL_WORK_AT_CLOSURE,
        )
        # The step is held with the closure, under the same code (point 7).
        assert held["next_action_text"].reason == LOCAL_WORK_AT_CLOSURE
        assert (
            LovableSyncDecision.objects.filter(
                run=run, state__reference=ref(1), field="stage"
            ).count()
            == 1
        )
        work = held["stage"].current["local_work"]
        assert (work["planned"], work["overviews"], work["waits"]) == (1, 1, 2)
        assert held["stage"].current["held_since"] == NIGHT.date().isoformat()
        assert held["stage"].current["escalates_on"] == "2026-11-26"
    assert _events_since(events) == 0
    state.refresh_from_db()
    assert state.applied == baseline
    matter.refresh_from_db()
    assert matter.is_open
    planned.refresh_from_db()
    assert planned.status == ActionStatus.PLANNED
    assert MatterWebsiteOverview.objects.get(pk=overview.pk).status == "PLANNED"
    assert all(MatterEngagement.objects.get(pk=w.pk).feedback_closed_at is None for w in waits)
    assert NextAction.objects.filter(matter=matter, status=ActionStatus.OPEN).exists()


def test_the_hold_counts_toward_no_breaker_and_the_rest_of_the_teema_still_syncs(world):
    """Regression: TL-02 (point 1, memo case 9). The title Lovable changed
    beside its closure is written; the held closure is neither a source change
    for `MAX_CHANGED_MATTERS` nor a hold for `MAX_NEW_HOLDS`."""
    _overview(world)
    world.lovable.edit(ref(1), teema="Uus näidispealkiri", **CLOSED)
    run = world.run(max_changed=1)
    assert run.status == SyncRunStatus.SUCCEEDED, run.error
    assert world.matter(ref(1)).title == "Uus näidispealkiri"
    assert world.matter(ref(1)).is_open
    assert run.counts["held_fields"] == 0
    assert run.counts["held_local_work"] == 1


def test_compare_reports_the_same_hold_and_writes_nothing(world):
    """Regression: TL-02 (points 3, 8; memo case 17). COMPARE holds identically."""
    _overview(world)
    _close_in_lovable(world)
    events = set(ChangeEvent.objects.values_list("pk", flat=True))
    run = world.run(mode=SyncMode.COMPARE)
    assert _stage(run).reason == LOCAL_WORK_AT_CLOSURE
    assert run.counts["held_local_work"] == 1
    assert _events_since(events) == 0


def test_a_restored_reclose_and_a_successor_closure_are_held_too(world, pilot_on):
    """Regression: TL-02 (memo cases 13, 14). A Teema a lawyer reopened locally
    (the lock off, the switch OFF) and gave planned work is not closed again over
    it; a closure under a successor with local work is held as well."""
    from app.matters.services import reopen_matter_into_stage

    _close_in_lovable(world)
    world.run()
    assert not world.matter(ref(1)).is_open
    # The lock is off only while the switch is OFF (open-decisions E9).
    lovable_sync.set_mode(SyncMode.OFF, reason="kontraktitest")
    reopen_matter_into_stage(
        matter=world.matter(ref(1)),
        stage=world.matter(ref(2)).stage,
        actor=world.person(AVA),
    )
    _overview(world)
    world.lovable.link(ref(3), ref(2))
    _overview(world, 3)
    run = world.run()
    assert _stage(run).reason == LOCAL_WORK_AT_CLOSURE
    assert _stage(run, 3).reason == LOCAL_WORK_AT_CLOSURE
    assert world.matter(ref(1)).is_open and world.matter(ref(3)).is_open
    assert run.counts["held_local_work"] == 2


# ---------------------------------------------------------------------------
# Resolution: finishing the work, the lawyer's own close, Lovable reopening
# ---------------------------------------------------------------------------


def test_finishing_the_work_lets_the_next_night_close_with_lovables_reason(world):
    """Regression: TL-02 (point 4a, memo case 11)."""
    planned = _planned(world)
    _close_in_lovable(world)
    world.run()
    assert world.matter(ref(1)).is_open
    cancel_next_action(action=planned, actor=world.person(AVA), reason="Tehtud muul viisil")
    run = world.run()
    one = world.matter(ref(1))
    assert (one.is_open, one.disposition) == (False, Disposition.COMPLETED)
    assert _stage(run).outcome == SyncOutcome.APPLIED
    assert run.counts["held_local_work"] == 0
    assert hold_of(one) is None


def test_the_lawyer_may_close_a_held_teema_and_the_night_sets_lovables_reason(world):
    """Regression: TL-02 (point 4b). While Lovable shows the Teema closed and
    the hold stands, the lawyer's own «Lõpeta teema» is allowed (the lock frees
    a field under an open technical hold, open-decisions E11); on a Teema
    Lovable still shows open it stays refused (E3). The next night sets
    Lovable's reason as a closure-reason change and ends nothing again."""
    _overview(world)
    _close_in_lovable(world)
    world.run()
    with refused("Teemat ei saa Juristides lõpetada."):
        close_matter(
            matter=world.matter(ref(2)),
            disposition=Disposition.MONITORING_STOPPED,
            actor=world.person(AVA),
        )
    matter = world.matter(ref(1))
    close_matter(
        matter=matter,
        disposition=Disposition.MONITORING_STOPPED,
        actor=world.person(AVA),
        follow_ups_confirmed=live_work_at_closure(matter).digest,
    )
    run = world.run()
    one = world.matter(ref(1))
    assert (one.is_open, one.disposition) == (False, Disposition.COMPLETED)
    assert world.events_of(run).filter(event_type="MATTER_CLOSURE_REASON_CHANGED").count() == 1
    assert not world.events_of(run).filter(event_type="MATTER_CLOSED").exists()


def test_lovable_reopening_clears_the_hold_and_a_release_then_changes_nothing(world):
    """Regression: TL-02 (points 6, 8; memo case 15). Lovable reopens while the
    hold stands: the next reading holds nothing, nothing is written, and a
    release finds no hold («the hold simply clears»)."""
    _overview(world)
    _close_in_lovable(world)
    world.run()
    world.lovable.edit(
        ref(1),
        hetkeseis="Riigikogus",
        jargmiseks="Ootan 2. lugemist",
        tegevuse_tahtaeg="2099-06-01",
    )
    events = set(ChangeEvent.objects.values_list("pk", flat=True))
    run = world.run()
    assert run.counts["held_local_work"] == 0
    assert not run.decisions.filter(outcome=SyncOutcome.APPLIED).exists()
    matter = world.matter(ref(1))
    assert hold_of(matter) is None
    result = release_closure_hold(matter=matter, actor=world.person(AVA), confirmed="x")
    assert (result.closed, result.message) == (False, HOLD_CLEARED)
    assert _events_since(events) == 0
    assert world.matter(ref(1)).is_open


# ---------------------------------------------------------------------------
# Release (points 4, 6)
# ---------------------------------------------------------------------------


def test_the_owner_releases_and_every_cancellation_names_them(world):
    """Regression: TL-02 (points 4, 6, 7). The release states what is closed and
    cancelled, rechecks the latest reading and the work, and closes in the
    owner's name: `MATTER_CLOSED` and each cancellation carry the person and
    the hold's run provenance; the request stays as a source closure leaves
    it. The next night only converges."""
    overview = _overview(world)
    planned = _planned(world)
    _close_in_lovable(world)
    hold_run = world.run()
    matter = world.matter(ref(1))
    preview = release_preview(matter)
    assert preview is not None
    assert "1 planeeritud kodulehe ülevaade" in preview.statement
    assert "praegune tegevus" in preview.statement
    ava = world.person(AVA)
    events = set(ChangeEvent.objects.values_list("pk", flat=True))
    result = release_closure_hold(matter=matter, actor=ava, confirmed=preview.digest)
    assert result.closed
    one = world.matter(ref(1))
    assert (one.is_open, one.disposition, one.closed_by_id) == (
        False,
        Disposition.COMPLETED,
        ava.pk,
    )
    written = ChangeEvent.objects.exclude(pk__in=events)
    closed = written.get(event_type="MATTER_CLOSED")
    assert closed.actor_id == ava.pk
    assert closed.payload["provenance"]["closure_hold"] == "RELEASED"
    assert closed.payload["provenance"]["sync_run"] == str(hold_run.pk)
    for kind, obj in (
        ("WEBSITE_OVERVIEW_CANCELLED", overview),
        ("NEXT_ACTION_CANCELLED", planned),
    ):
        row = written.get(event_type=kind, object_id=obj.pk)
        assert row.actor_id == ava.pk
        assert row.payload["provenance"]["sync_run"] == str(hold_run.pk)
    assert MatterWebsiteOverview.objects.get(pk=overview.pk).status == (
        WebsiteOverviewStatus.CANCELLED
    )
    later = world.run()
    assert _stage(later).outcome == SyncOutcome.ALREADY_CONVERGED
    assert not later.decisions.filter(outcome=SyncOutcome.APPLIED).exists()


def test_a_release_for_work_that_changed_is_refused_with_the_renewed_counts(world):
    """Regression: TL-02 (point 6; memo case 8, stale digest)."""
    _overview(world)
    _close_in_lovable(world)
    world.run()
    matter = world.matter(ref(1))
    stale = release_preview(matter).digest
    _planned(world)
    with refused(LIVE_WORK_CHANGED):
        release_closure_hold(matter=matter, actor=world.person(AVA), confirmed=stale)
    assert world.matter(ref(1)).is_open


def test_only_the_owner_or_a_verified_administrator_may_release(world):
    """Regression: TL-02 (point 4). A colleague who is not the owner is
    refused; a verified technical administrator (Haldus) may release, and is
    the recorded actor."""
    from app.accounts.admin_session import _mint

    _overview(world)
    _close_in_lovable(world)
    world.run()
    matter = world.matter(ref(1))
    digest = release_preview(matter).digest
    with refused(RELEASE_NOT_ALLOWED):
        release_closure_hold(matter=matter, actor=world.person(UKU), confirmed=digest)
    with refused(RELEASE_NOT_ALLOWED):
        release_closure_hold(matter=matter, actor=None, confirmed=digest)
    admin = factories.AdministratorFactory()
    with refused(RELEASE_NOT_ALLOWED):
        release_closure_hold(matter=matter, actor=admin, confirmed=digest)
    result = release_closure_hold(matter=matter, actor=_mint(admin, via="test"), confirmed=digest)
    assert result.closed
    assert world.matter(ref(1)).closed_by_id == admin.pk


# ---------------------------------------------------------------------------
# Visibility (point 3) and escalation (point 5)
# ---------------------------------------------------------------------------


def test_the_health_line_and_the_summary_say_the_hold_and_stay_ok(world):
    """Regression: TL-02 (point 3). Explained, never «not ok», never silent."""
    _overview(world)
    _close_in_lovable(world)
    run = world.run()
    health = lovable_sync.sync_health(now=run.started_at + dt.timedelta(minutes=5))
    assert health.ok
    assert "1 closure(s) held for Juristid-only work" in health.detail
    assert any("pooleli töö tõttu ootel: 1" in line for line in lovable_sync.summary_lines(run))


def test_the_teema_page_shows_the_hold_and_offers_the_release_only_to_the_owner(world, client):
    """Regression: TL-02 (points 1, 4). Counts only; the form only for the owner."""
    from django.urls import reverse

    _overview(world)
    _close_in_lovable(world)
    world.run()
    matter = world.matter(ref(1))
    url = reverse("matters:matter_detail", kwargs={"pk": matter.pk})
    client.force_login(world.person(AVA))
    body = client.get(url).content.decode()
    assert "data-closure-hold" in body
    assert "1 planeeritud kodulehe ülevaade" in body
    assert "data-closure-hold-release" in body
    client.force_login(world.person(UKU))
    body = client.get(url).content.decode()
    assert "data-closure-hold" in body
    assert "data-closure-hold-release" not in body
    client.force_login(world.person(AVA))
    response = client.post(
        reverse("matters:closure_hold_release", kwargs={"pk": matter.pk}),
        {"kinnitus": release_preview(matter).digest},
    )
    assert response.status_code == 302
    assert not world.matter(ref(1)).is_open


def test_a_hold_14_days_old_reaches_the_owner_and_the_department_head_once(world):
    """Regression: TL-02 (points 5, 8). On the hold's 14th day its owner gets one
    «Vajab tegevust» row; the next morning every department head gets one
    escalation. Reconciling again delivers nothing twice; once the hold ends
    the rows retire as done."""
    from app.notifications.deadlines import reconcile_deadline_notifications
    from app.notifications.enums import NotificationCategory, NotificationPurpose
    from app.notifications.models import DeadlineNotification

    head = factories.DepartmentHeadFactory()
    planned = _planned(world)
    _close_in_lovable(world)
    world.run()
    world.run()
    matter = world.matter(ref(1))
    rows = DeadlineNotification.objects.filter(category=NotificationCategory.CLOSURE_HOLD)
    reconcile_deadline_notifications(dt.date(2026, 11, 25))
    assert not rows.exists()
    for _ in range(2):
        reconcile_deadline_notifications(dt.date(2026, 11, 26))
    owner_row = rows.get()
    assert (owner_row.recipient_id, owner_row.purpose, owner_row.matter_id) == (
        world.person(AVA).pk,
        NotificationPurpose.ON_DAY,
        matter.pk,
    )
    for _ in range(2):
        reconcile_deadline_notifications(dt.date(2026, 11, 27))
    escalation = rows.get(purpose=NotificationPurpose.ESCALATION)
    assert escalation.recipient_id == head.pk
    assert rows.count() == 2
    cancel_next_action(action=planned, actor=world.person(AVA), reason="Pole vaja")
    world.run(at=NIGHT + dt.timedelta(days=15))
    assert not world.matter(ref(1)).is_open
    reconcile_deadline_notifications(dt.date(2026, 11, 28))
    assert set(rows.values_list("retired_reason", flat=True)) == {"DISCHARGED"}


def test_no_closed_matter_invariant_finding_after_hold_release_or_closure(world):
    """Regression: TL-02 (memo case 25). Holding, releasing and the source
    closure leave no closed Teema carrying live work."""
    from app.core.invariants import check_domain_invariants

    _overview(world)
    _overview(world, 2)
    _close_in_lovable(world)
    _close_in_lovable(world, 2)
    _close_in_lovable(world, 3)
    world.run()
    release_closure_hold(
        matter=world.matter(ref(2)),
        actor=world.person(AVA),
        confirmed=release_preview(world.matter(ref(2))).digest,
    )
    kinds = {f.kind for f in check_domain_invariants().findings}
    assert not {k for k in kinds if k.startswith("closed-matter-")}
    assert world.matter(ref(1)).is_open
    assert not world.matter(ref(2)).is_open and not world.matter(ref(3)).is_open
    assert {c.verdict for c in oracle.compare(world.lovable, today=world.today)} >= {oracle.HELD}


def test_a_koja_check_planned_or_current_holds_the_closure_and_stays_monitoring(world, pilot_on):
    """Regression: TL-02 (point 2; memo case 4). A «Koja arvamuse järelkontroll»
    — planned beside Lovable's step on one Teema, the current step on another
    whose Lovable `jargmiseks` is blank — holds the closure, and its follow-up
    stays MONITORING."""
    from app.organisations.models import Organisation
    from app.workflow.models import OpinionFollowUp
    from app.workflow.services import promote_next_planned_action
    from tests.follow_ups import day, send_koja_arvamus

    ministry = Organisation.objects.create(name="Näidisministeerium")
    world.lovable.edit(ref(2), jargmiseks="", tegevuse_tahtaeg=None)
    world.run()
    assert not NextAction.objects.filter(
        matter=world.matter(ref(2)), status=ActionStatus.OPEN
    ).exists()
    checks = {}
    for number in (1, 2):
        submission = send_koja_arvamus(
            world.matter(ref(number)), world.person(AVA), [ministry], day(-2)
        )
        checks[number] = OpinionFollowUp.objects.get(submission=submission)
    # Where Lovable states no step, the established rule promotes the check to
    # the current step (`promote_next_planned_action`, as after a completion).
    promote_next_planned_action(matter=world.matter(ref(2)))
    planned = NextAction.objects.get(follow_up=checks[1])
    current = NextAction.objects.get(follow_up=checks[2])
    assert (planned.status, current.status) == (ActionStatus.PLANNED, ActionStatus.OPEN)
    _close_in_lovable(world, 1)
    _close_in_lovable(world, 2)
    run = world.run()
    for number in (1, 2):
        assert _stage(run, number).reason == LOCAL_WORK_AT_CLOSURE
        assert _stage(run, number).current["local_work"]["checks"] == 1
        assert world.matter(ref(number)).is_open
        checks[number].refresh_from_db()
        assert checks[number].is_monitoring


def test_work_added_between_the_plan_and_the_write_is_held_at_the_write(world, monkeypatch):
    """Regression: TL-02 (memo case 7). Should the plan — and the re-plan under
    the Matter's lock — miss Juristid-only work, the domain guard inside
    `close_matter_from_source` refuses under the same lock, and the run records
    the hold for that Teema instead of a failure; nothing of it is written."""
    from app.matters import closure_work

    real = closure_work.juristid_only_work
    seen = {"calls": 0}

    def blind_planner(matter):
        # The planner's first two looks (the plan, the re-plan under the lock)
        # see nothing; the service's own look, and every later one, sees it.
        seen["calls"] += 1
        return closure_work.LiveWork() if seen["calls"] <= 2 else real(matter)

    class Planner:
        def __getattr__(self, name):
            return blind_planner if name == "juristid_only_work" else getattr(closure_work, name)

    overview = _overview(world)
    _close_in_lovable(world)
    monkeypatch.setattr(lovable_sync, "_closure_work", Planner())
    run = world.run()
    assert run.status == SyncRunStatus.SUCCEEDED, run.error
    assert _stage(run).reason == LOCAL_WORK_AT_CLOSURE
    assert world.matter(ref(1)).is_open
    assert MatterWebsiteOverview.objects.get(pk=overview.pk).status == "PLANNED"


def test_haldus_lists_the_hold_with_its_age_and_an_administrator_releases_it(
    world, client, settings
):
    """Regression: TL-02 (points 3, 4, 6). «Lovable'i üksikasjad» lists the held
    closure by reference, age and kind of work (never a record's words) and
    counts it; the administrator's release page states what is cancelled and
    closes in the verified administrator's name."""
    from django.urls import reverse

    from tests.admin_session import apply_admin_gate, technical_admin, verified

    apply_admin_gate(settings)
    _overview(world)
    _close_in_lovable(world)
    world.run()
    # The administrator session is set up as `tests/test_lovable_pages.py` does it.
    settings.REAL_DATA_ALLOWED = False
    admin, secret = technical_admin()
    verified(client, admin, secret)
    page = client.get(reverse("haldus:lovable")).content.decode()
    assert "data-closure-holds" in page
    assert ref(1) in page and "1 planeeritud kodulehe ülevaade" in page
    assert "Lovable'is suletud, Juristides pooleli töö tõttu ootel" in page
    url = f"{reverse('haldus:data_sync_release_hold')}?viide={ref(1)}"
    confirm = client.get(url).content.decode()
    assert "Sinu nimel tühistatakse: praegune tegevus ja 1 planeeritud" in confirm
    digest = release_preview(world.matter(ref(1))).digest
    response = client.post(
        reverse("haldus:data_sync_release_hold"), {"viide": ref(1), "kinnitus": digest}
    )
    assert response.status_code == 302, response
    one = world.matter(ref(1))
    assert (one.is_open, one.closed_by_id) == (False, admin.pk)
