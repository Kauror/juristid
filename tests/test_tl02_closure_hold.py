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
    world.lovable.edit(ref(1), teema="Kirjutamata pealkiri", **CLOSED)
    monkeypatch.setattr(lovable_sync, "_closure_work", Planner())
    run = world.run()
    assert run.status == SyncRunStatus.SUCCEEDED, run.error
    assert _stage(run).reason == LOCAL_WORK_AT_CLOSURE
    assert world.matter(ref(1)).is_open
    assert MatterWebsiteOverview.objects.get(pk=overview.pk).status == "PLANNED"
    # The title that would have been written beside it waits, and says why
    # (review P3-4): not executed, with its own reason.
    title = decisions(run, ref(1))["title"]
    assert (title.outcome, title.reason) == (SyncOutcome.ELIGIBLE, lovable_sync.HELD_AT_WRITE)
    assert run.counts["not_executed"] == 1
    assert world.matter(ref(1)).title != "Kirjutamata pealkiri"


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


# ---------------------------------------------------------------------------
# Review of 12.11.2026 (P2-1 … P2-5, P3)
# ---------------------------------------------------------------------------


def _hold_one(world):
    """One standing hold on 2099_1 (an overview), recorded by an APPLY night."""
    overview = _overview(world)
    _close_in_lovable(world)
    world.run()
    return overview


def _newer_reading(document, *, at):
    """A stored reading newer than the hold's own, from a run that read Lovable
    but did not get as far as observing (it failed after storing the snapshot)."""
    from app.legacy_import.lovable_sync_models import (
        LovableSyncRun,
        LovableSyncSnapshot,
        SyncRunStatus,
        SyncTrigger,
    )

    run = LovableSyncRun.objects.create(
        run_key="test-newer-reading",
        trigger=SyncTrigger.MANUAL,
        mode=SyncMode.APPLY,
        status=SyncRunStatus.FAILED,
        started_at=at,
        snapshot_taken_at=at,
        mapping_version=lovable_sync.MAPPING_VERSION,
    )
    return LovableSyncSnapshot.objects.create(
        run=run, taken_at=at, sha256="d" * 64, document=document
    )


def test_a_release_is_refused_in_compare_off_and_after_the_cutover(world):
    """Regression: TL-02 review P2-1/P2-2. A hold leads to a write only while
    the synchronisation writes: COMPARE and OFF refuse a release with one
    sentence, and after the cutover there is no standing hold to release."""
    from app.legacy_import.lovable_closure_hold import RELEASE_NOT_WRITING, standing_holds

    _hold_one(world)
    matter = world.matter(ref(1))
    digest = release_preview(matter).digest
    for mode in (SyncMode.COMPARE, SyncMode.OFF):
        lovable_sync.set_mode(mode, reason="kontraktitest")
        with refused(RELEASE_NOT_WRITING):
            release_closure_hold(matter=matter, actor=world.person(AVA), confirmed=digest)
        assert world.matter(ref(1)).is_open
    lovable_sync.cut_over(reason="kontraktitest")
    assert standing_holds() == []
    with refused(RELEASE_NOT_WRITING):
        release_closure_hold(matter=matter, actor=world.person(AVA), confirmed=digest)
    assert world.matter(ref(1)).is_open


def test_compare_shows_an_informational_banner_without_a_release(world, rf):
    """Regression: TL-02 review P2-1. In COMPARE the hold is recorded and, where
    the banner is drawn (#618 locks in COMPARE too), it says what *would*
    happen once the synchronisation writes: no release form, and no promise
    that the night closes the Teema."""
    from django.template.loader import render_to_string

    from app.matters.views import _closure_hold_context

    _overview(world)
    _close_in_lovable(world)
    run = world.run(mode=SyncMode.COMPARE)
    assert _stage(run).reason == LOCAL_WORK_AT_CLOSURE
    matter = world.matter(ref(1))
    request = rf.get("/")
    request.user = world.person(AVA)
    context = _closure_hold_context(request, matter, held=True)
    assert context["closure_hold"]["writes"] is False
    assert context["closure_hold"]["can_release"] is False
    body = render_to_string(
        "matters/partials/matter_banner.html", {"matter": matter, **context}, request=request
    )
    assert "data-closure-hold-proposed" in body
    assert "Kui sünkroonimine hakkab Juristidesse kirjutama" in body
    assert "data-closure-hold-release" not in body
    assert "järgmine öine sünkroonimine suleb" not in body


def test_no_escalation_in_compare_off_or_for_a_retired_cohort(world):
    """Regression: TL-02 review P2-1/P2-2. The 14-day rows exist only while the
    synchronisation writes and the Teema's cohort is live."""
    from app.legacy_import.lovable_sync_models import LovableSyncCohort
    from app.notifications.deadlines import reconcile_deadline_notifications
    from app.notifications.enums import NotificationCategory
    from app.notifications.models import DeadlineNotification

    factories.DepartmentHeadFactory()
    _hold_one(world)
    rows = DeadlineNotification.objects.filter(category=NotificationCategory.CLOSURE_HOLD)
    for mode in (SyncMode.COMPARE, SyncMode.OFF):
        lovable_sync.set_mode(mode, reason="kontraktitest")
        reconcile_deadline_notifications(dt.date(2026, 11, 26))
        reconcile_deadline_notifications(dt.date(2026, 11, 27))
        assert not rows.exists(), mode
    lovable_sync.set_mode(SyncMode.APPLY, reason="kontraktitest")
    cohort = LovableSyncState.objects.get(matter=world.matter(ref(1))).cohort
    LovableSyncCohort.objects.filter(key=cohort).update(retired_at=NIGHT)
    reconcile_deadline_notifications(dt.date(2026, 11, 26))
    assert not rows.exists()


def test_more_than_the_cap_of_new_closure_holds_stops_the_night(world, monkeypatch):
    """Regression: TL-02 review P2-3. More closure holds beginning in one night
    than `MAX_NEW_CLOSURE_HOLDS` (20, proposed, owner approval needed) is a
    REVIEW_ONLY night with its own code that writes nothing; health and the
    summary say so. The next night those holds are no longer new."""
    assert lovable_sync.MAX_NEW_CLOSURE_HOLDS == 20
    monkeypatch.setattr(lovable_sync, "MAX_NEW_CLOSURE_HOLDS", 1)
    _overview(world, 1)
    _overview(world, 2)
    _close_in_lovable(world, 1)
    _close_in_lovable(world, 2)
    world.lovable.edit(ref(3), teema="Ootav pealkiri")
    run = world.run()
    assert (run.status, run.error) == (
        SyncRunStatus.REVIEW_ONLY,
        lovable_sync.CLOSURE_HOLD_BREAKER,
    )
    assert run.counts["new_closure_holds"] == 2
    assert world.matter(ref(3)).title != "Ootav pealkiri"
    health = lovable_sync.sync_health(now=run.started_at + dt.timedelta(minutes=5))
    assert not health.ok and lovable_sync.CLOSURE_HOLD_BREAKER in health.detail
    assert any("PEATATUD" in line for line in lovable_sync.summary_lines(run))
    later = world.run()
    assert later.status == SyncRunStatus.SUCCEEDED, later.error
    assert later.counts["new_closure_holds"] == 0
    assert world.matter(ref(3)).title == "Ootav pealkiri"


def test_a_hold_older_than_21_days_makes_health_not_ok(world):
    """Regression: TL-02 review P2-3. `MAX_CLOSURE_HOLD_AGE_DAYS` (21, proposed,
    owner approval needed): the escalation did not resolve it — a warning."""
    assert lovable_sync.MAX_CLOSURE_HOLD_AGE_DAYS == 21
    _hold_one(world)
    later = world.run(at=NIGHT + dt.timedelta(days=22))
    assert later.status == SyncRunStatus.SUCCEEDED, later.error
    health = lovable_sync.sync_health(now=later.started_at + dt.timedelta(minutes=5))
    assert not health.ok
    assert "older than 21 days" in health.detail


def test_a_newer_stored_reading_that_reopens_refuses_the_release(world):
    """Regression: TL-02 review P2-4. The web process cannot read Lovable live,
    so the release rereads the stored readings: a newer one (a run that read
    Lovable but did not observe) in which the Teema is open again refuses it.
    The preview names when the hold's reading was taken."""
    import copy

    from app.legacy_import.lovable_closure_hold import NEWER_READING_DISAGREES

    _hold_one(world)
    matter = world.matter(ref(1))
    preview = release_preview(matter)
    state = LovableSyncState.objects.select_related("observed_run").get(matter=matter)
    assert preview.read_at == state.observed_run.snapshot_taken_at
    document = copy.deepcopy(state.observed_run.snapshot.document)
    document["cohort"][str(state.source_id)]["row"]["hetkeseis"] = "Riigikogus"
    _newer_reading(document, at=state.observed_run.snapshot_taken_at + dt.timedelta(hours=2))
    with refused(NEWER_READING_DISAGREES):
        release_closure_hold(matter=matter, actor=world.person(AVA), confirmed=preview.digest)
    assert world.matter(ref(1)).is_open


def test_the_migration_reverse_deletes_the_closure_hold_rows(world):
    """Regression: TL-02 review P2-5. Back to `notifications` 0003 the narrower
    CHECK cannot hold `CLOSURE_HOLD` rows: the reverse deletes them, and only
    them."""
    import importlib

    from django.apps import apps

    from app.notifications.enums import (
        NotificationCategory,
        NotificationKind,
        NotificationPurpose,
        NotificationSource,
    )
    from app.notifications.models import DeadlineNotification

    matter = world.matter(ref(1))
    common = {
        "recipient": world.person(AVA),
        "matter": matter,
        "source_id": matter.pk,
        "source_date": dt.date(2026, 11, 26),
        "purpose": NotificationPurpose.ON_DAY,
        "offset_days": 0,
        "kind": NotificationKind.ACTION,
        "due_on": dt.date(2026, 11, 26),
        "source_type": NotificationSource.MATTER,
    }
    DeadlineNotification.objects.create(category=NotificationCategory.CLOSURE_HOLD, **common)
    kept = DeadlineNotification.objects.create(
        category=NotificationCategory.OPINION_DEADLINE, **common
    )
    migration = importlib.import_module("app.notifications.migrations.0004_closure_hold_category")
    migration._delete_closure_hold_rows(apps, None)
    assert list(DeadlineNotification.objects.values_list("pk", flat=True)) == [kept.pk]


def test_reopen_is_not_freed_after_a_release(world, client):
    """Regression: TL-02 review P3-2. The hold frees the closure while it stands;
    once the Teema is closed it frees nothing more, so «Ava uuesti» is refused
    and not offered until Lovable reopens it."""
    from django.urls import reverse

    from app.matters.services import reopen_matter_into_stage

    _hold_one(world)
    matter = world.matter(ref(1))
    release_closure_hold(
        matter=matter, actor=world.person(AVA), confirmed=release_preview(matter).digest
    )
    with refused("Teemat ei saa Juristides uuesti avada."):
        reopen_matter_into_stage(
            matter=world.matter(ref(1)),
            stage=world.matter(ref(2)).stage,
            actor=world.person(AVA),
        )
    client.force_login(world.person(AVA))
    body = client.get(reverse("matters:matter_detail", kwargs={"pk": matter.pk})).content.decode()
    assert "data-source-owned-reopen" in body


def test_the_domain_service_checks_the_releaser_and_has_no_bypass(world):
    """Regression: TL-02 review P3-3. `close_matter_from_source` refuses a
    `released_by` who may not release, whatever its caller checked, and
    `close_matter` offers no successor-visibility bypass keyword."""
    import inspect

    from app.legacy_import.lovable_closure_hold import RELEASE_NOT_ALLOWED

    _hold_one(world)
    matter = world.matter(ref(1))
    with refused(RELEASE_NOT_ALLOWED):
        close_matter_from_source(
            matter=matter,
            disposition=Disposition.COMPLETED,
            released_by=world.person(UKU),
            confirmed=release_preview(matter).digest,
        )
    assert world.matter(ref(1)).is_open
    assert "successor_from_source" not in inspect.signature(close_matter).parameters


def test_a_department_head_who_is_not_the_owner_and_a_reader_are_refused(world):
    """Guard: TL-02 review P3-9 (held before the review too). Only the owner or
    a verified administrator: a department head who is not the owner and a
    reader are refused at the service."""
    from app.legacy_import.lovable_closure_hold import RELEASE_NOT_ALLOWED

    _hold_one(world)
    matter = world.matter(ref(1))
    digest = release_preview(matter).digest
    for person in (factories.DepartmentHeadFactory(), factories.ReaderFactory()):
        with refused(RELEASE_NOT_ALLOWED):
            release_closure_hold(matter=matter, actor=person, confirmed=digest)
    assert world.matter(ref(1)).is_open


def test_a_released_kaasamine_wait_is_closed_in_the_owners_name(world):
    """Guard: TL-02 review P3-9 (held before the review too). The wait a
    release ends names the person who released it, beside the hold's run
    provenance."""
    engagement = _wait(world)
    _close_in_lovable(world)
    hold_run = world.run()
    matter = world.matter(ref(1))
    ava = world.person(AVA)
    events = set(ChangeEvent.objects.values_list("pk", flat=True))
    release_closure_hold(matter=matter, actor=ava, confirmed=release_preview(matter).digest)
    closed = ChangeEvent.objects.exclude(pk__in=events).get(
        event_type="ENGAGEMENT_FEEDBACK_CLOSED", object_id=engagement.pk
    )
    assert closed.actor_id == ava.pk
    assert closed.payload["provenance"]["sync_run"] == str(hold_run.pk)
    assert closed.payload["provenance"]["closure_hold"] == "RELEASED"


def _verdicts(world, reference):
    return {
        c.field: c.verdict
        for c in oracle.compare(world.lovable, today=world.today)
        if c.reference == reference
    }


def test_the_oracle_does_not_swallow_a_wrong_step_written_during_a_hold(world):
    """Regression: TL-02 review P3-9. During a hold whose step Lovable did not
    change, the step must still read as Lovable states it: a different step
    written meanwhile is DIFFERENT, not HELD."""
    from app.workflow.services import set_next_action

    _overview(world)
    world.lovable.edit(ref(1), hetkeseis="jõustunud")  # the step sentence stays
    world.run()
    verdicts = _verdicts(world, ref(1))
    assert verdicts["lifecycle"] == oracle.HELD
    assert verdicts["step"] == oracle.MATCH
    set_next_action(matter=world.matter(ref(1)), text="Vale samm", actor=None)
    assert _verdicts(world, ref(1))["step"] == oracle.DIFFERENT


# ---------------------------------------------------------------------------
# Second review of 12.11.2026 (P2-A, P3-A … P3-E)
# ---------------------------------------------------------------------------


def test_the_escalation_clock_starts_on_the_first_apply_night(world):
    """Regression: TL-02 second review P2-A. A hold that stood for three weeks
    of COMPARE starts its 14 days on the first APPLY night: the owner's row
    comes 14 days after it and the department head's the morning after —
    never an escalation day already outside the seven-day catch-up."""
    from app.notifications.deadlines import reconcile_deadline_notifications
    from app.notifications.enums import NotificationCategory, NotificationPurpose
    from app.notifications.models import DeadlineNotification

    head = factories.DepartmentHeadFactory()
    _overview(world)
    _close_in_lovable(world)
    first = world.run(mode=SyncMode.COMPARE)
    assert _stage(first).current["escalates_on"] is None
    world.run(mode=SyncMode.COMPARE, at=NIGHT + dt.timedelta(days=22))
    applying = world.run(at=NIGHT + dt.timedelta(days=23))
    current = _stage(applying).current
    assert current["held_since"] == NIGHT.date().isoformat()
    assert current["writing_since"] == "2026-12-05"
    assert current["escalates_on"] == "2026-12-19"
    rows = DeadlineNotification.objects.filter(category=NotificationCategory.CLOSURE_HOLD)
    reconcile_deadline_notifications(dt.date(2026, 12, 18))
    assert not rows.exists()
    reconcile_deadline_notifications(dt.date(2026, 12, 19))
    owner = rows.get()
    assert (owner.recipient_id, owner.purpose) == (world.person(AVA).pk, NotificationPurpose.ON_DAY)
    reconcile_deadline_notifications(dt.date(2026, 12, 20))
    assert rows.get(purpose=NotificationPurpose.ESCALATION).recipient_id == head.pk


def test_holds_carried_from_compare_are_not_new_on_the_first_apply_night(world, monkeypatch):
    """Regression guard: TL-02 second review P3-B. The closure-hold cap counts
    holds first recorded by the run: carried from COMPARE they are not new,
    so the first APPLY night is not stopped by them."""
    monkeypatch.setattr(lovable_sync, "MAX_NEW_CLOSURE_HOLDS", 0)
    _overview(world)
    _close_in_lovable(world)
    world.run(mode=SyncMode.COMPARE)
    applying = world.run()
    assert applying.status == SyncRunStatus.SUCCEEDED, applying.error
    assert applying.counts["new_closure_holds"] == 0


def test_the_compare_banner_with_no_work_left_promises_nothing(world, rf):
    """Regression: TL-02 second review P3-C. In COMPARE, once the work is done,
    the informational banner no longer says the closure would wait for it."""
    from django.template.loader import render_to_string

    from app.matters.services import cancel_website_overview
    from app.matters.views import _closure_hold_context

    overview = _overview(world)
    _close_in_lovable(world)
    world.run(mode=SyncMode.COMPARE)
    cancel_website_overview(overview=overview, actor=world.person(AVA))
    matter = world.matter(ref(1))
    request = rf.get("/")
    request.user = world.person(AVA)
    context = _closure_hold_context(request, matter, held=True)
    body = render_to_string(
        "matters/partials/matter_banner.html", {"matter": matter, **context}, request=request
    )
    assert "Lovable näitab teemat suletuna." in body
    assert "pooleli töö tõttu" not in body
    assert "data-closure-hold-release" not in body


def test_haldus_says_paused_in_off_and_shows_no_escalation_chip_outside_apply(
    world, client, settings, monkeypatch
):
    """Regression: TL-02 second review P3-C. In OFF the closure-hold list does
    not claim the synchronisation «ainult võrdleb», and the «üle 14 päeva»
    chip shows only in APPLY."""
    from django.urls import reverse

    from app.legacy_import.lovable_closure_hold import ClosureHold
    from tests.admin_session import apply_admin_gate, technical_admin, verified

    apply_admin_gate(settings)
    _hold_one(world)
    monkeypatch.setattr(ClosureHold, "escalated", lambda self, today: True)
    settings.REAL_DATA_ALLOWED = False
    admin, secret = technical_admin()
    verified(client, admin, secret)
    page = client.get(reverse("haldus:lovable")).content.decode()
    assert "üle 14 päeva" in page
    lovable_sync.set_mode(SyncMode.OFF, reason="kontraktitest")
    page = client.get(reverse("haldus:lovable")).content.decode()
    assert "data-closure-holds" in page
    assert "ainult võrdleb" not in page
    assert "Sünkroonimine on peatatud" in page
    assert "üle 14 päeva" not in page
    assert "Vabasta…" not in page
    lovable_sync.set_mode(SyncMode.COMPARE, reason="kontraktitest")
    page = client.get(reverse("haldus:lovable")).content.decode()
    assert "ainult võrdleb" in page
    assert "üle 14 päeva" not in page


def test_a_release_takes_the_control_row_for_share_before_the_matter(world):
    """Regression: TL-02 second review P3-D. Inside the release transaction the
    control row is locked FOR SHARE before the mode is read and before the
    Matter's lock, so `set_mode`/`cut_over` cannot change the mode under it."""
    from django.db import connection
    from django.test.utils import CaptureQueriesContext

    _hold_one(world)
    matter = world.matter(ref(1))
    digest = release_preview(matter).digest
    with CaptureQueriesContext(connection) as queries:
        release_closure_hold(matter=matter, actor=world.person(AVA), confirmed=digest)
    sql = [q["sql"] for q in queries.captured_queries]
    share = next(
        i
        for i, q in enumerate(sql)
        if "lovablesynccontrol" in q and q.rstrip().endswith("FOR SHARE")
    )
    matter_lock = next(
        i for i, q in enumerate(sql) if 'FROM "matters_matter"' in q and "FOR NO KEY UPDATE" in q
    )
    assert share < matter_lock


def test_a_stale_reading_is_not_offered_for_release(world, rf):
    """Regression: TL-02 second review P3-E. A release a newer stored reading
    would refuse is not offered: the preview says so and the banner has no
    button."""
    import copy

    from app.matters.views import _closure_hold_context

    _hold_one(world)
    matter = world.matter(ref(1))
    state = LovableSyncState.objects.select_related("observed_run").get(matter=matter)
    document = copy.deepcopy(state.observed_run.snapshot.document)
    document["cohort"][str(state.source_id)]["row"]["hetkeseis"] = "Riigikogus"
    _newer_reading(document, at=state.observed_run.snapshot_taken_at + dt.timedelta(hours=2))
    assert release_preview(matter).stale
    request = rf.get("/")
    request.user = world.person(AVA)
    assert _closure_hold_context(request, matter, held=True)["closure_hold"]["can_release"] is False


def test_the_teema_page_in_compare_draws_at_most_an_informational_banner(world, client):
    """Regression guard: TL-02 second review P3-A. With #618's lock in COMPARE
    the page draws the informational banner without a button; with the lock
    APPLY-only it draws none. Either way, never a release in COMPARE."""
    from django.urls import reverse

    from app.matters.source_ownership import ownership

    _overview(world)
    _close_in_lovable(world)
    world.run(mode=SyncMode.COMPARE)
    matter = world.matter(ref(1))
    client.force_login(world.person(AVA))
    body = client.get(reverse("matters:matter_detail", kwargs={"pk": matter.pk})).content.decode()
    assert "data-closure-hold-release" not in body
    if ownership(matter).active:
        assert "data-closure-hold-proposed" in body
    else:
        assert "data-closure-hold" not in body
