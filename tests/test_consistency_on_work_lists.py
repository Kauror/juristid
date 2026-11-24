"""The docs/adr/0161 alert on Minu asjad and Osakond (amendment of 2026-11-25).

The Teema page's one alert — a current step started from the standard sequence
that the record already shows done — is also drawn on the row of that step:

* on Minu asjad, on the step's own row (the dated band, or «Kuupäevata»), for
  the desk's owner and for the department head reading the same desk;
* on Osakond, on the department head's «Vajab sekkumist» row for that step.

The same rule (`steps_already_recorded`; the Teema page asks it about one
step), the same title and reason, never the remedy on a row, no count, no
dismissal, no row added or dropped — and a constant number of reads, however
many Teemad the page holds. `tests/test_workflow_consistency.py` runs its
scenarios through the batched form as well (`tests/consistency_parity.py`).

Synthetic data only.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from app.core.enums import Visibility
from app.matters import department as dept
from app.matters import work_items as wi
from app.matters import workspace as ws
from app.matters.enums import EngagementKind
from app.matters.models import MatterEngagement
from app.matters.my_work import build_my_work
from app.workflow.consistency import (
    PROOF_ENGAGEMENT,
    STEP_ALREADY_RECORDED,
    STEP_ALREADY_RECORDED_REMEDY,
    STEP_ALREADY_RECORDED_TITLE,
    CurrentStep,
    step_already_recorded,
    steps_already_recorded,
    with_consistency_alerts,
)
from app.workflow.enums import ActionStatus, DateSemantics
from app.workflow.models import MatterPlanStep, NextAction
from app.workflow.plan import seed_standard_plan
from tests import factories
from tests.consistency_parity import work_rows
from tests.legacy_plans import start_as_before_adr_0168

pytestmark = pytest.mark.django_db

MARK = f'data-consistency-alert="{STEP_ALREADY_RECORDED}"'


def _flagged_teema(owner, *, title="Näidisseadus", late=True, record=True):
    """A Teema whose current step «Kaasa liikmeid» the record already shows done.

    ``late`` dates the step three days ago, so it is on Osakond's «Vajab
    sekkumist» and in Minu asjad's «Üle tähtaja»; otherwise it has no date and
    is in «Kuupäevata». ``record`` files the `Kaasamine` that proves it.
    """
    matter = factories.MatterFactory(owner=owner, title=title)
    seed_standard_plan(matter=matter, actor=owner)
    step = MatterPlanStep.objects.get(matter=matter, template_step_key="consult-members")
    action = start_as_before_adr_0168(matter=matter, step=step, actor=owner)
    changes = {"created_at": timezone.now() - timedelta(hours=1)}
    if late:
        changes |= {
            "target_date": timezone.localdate() - timedelta(days=3),
            "date_semantics": DateSemantics.DEADLINE,
        }
    NextAction.objects.filter(pk=action.pk).update(**changes)
    if record:
        ws.add_matter_engagement(
            matter=matter,
            author=owner,
            audience="Liikmed",
            kind=EngagementKind.SURVEY.value,
            occurred_on=timezone.localdate(),
        )
    action.refresh_from_db()
    return matter, action


def _rows_for(viewer, action):
    return [
        row
        for row in with_consistency_alerts(work_rows(viewer), viewer)
        if row.is_action and row.object_id == action.pk
    ]


# ---------------------------------------------------------------------------
# One rule: the work rows and the Teema page agree
# ---------------------------------------------------------------------------


def test_a_work_row_carries_exactly_the_teema_pages_alert(specialist):
    matter, action = _flagged_teema(specialist)

    [row] = _rows_for(specialist, action)

    assert row.consistency_alert is not None
    assert row.consistency_alert == step_already_recorded(matter, action, specialist)
    assert row.consistency_alert.reason == PROOF_ENGAGEMENT


def test_many_teemad_at_once_equal_each_asked_alone(specialist):
    """Parity across a mixed population: flagged, unproven, undated, hand-written."""
    flagged = [_flagged_teema(specialist, title=f"Näidis {n}") for n in range(3)]
    unproven = [_flagged_teema(specialist, title=f"Tõestamata {n}", record=False) for n in range(2)]
    undated = [_flagged_teema(specialist, title=f"Kuupäevata {n}", late=False) for n in range(2)]
    hand = factories.MatterFactory(owner=specialist)
    ws.add_matter_engagement(
        matter=hand,
        author=specialist,
        audience="Liikmed",
        kind=EngagementKind.SURVEY.value,
        occurred_on=timezone.localdate(),
    )

    batched = {
        row.object_id: row.consistency_alert
        for row in with_consistency_alerts(work_rows(specialist), specialist)
        if row.is_action
    }

    for matter, action in [*flagged, *unproven, *undated]:
        matter.refresh_from_db()
        assert batched[action.pk] == step_already_recorded(matter, action, specialist)
    assert sum(1 for alert in batched.values() if alert) == len(flagged) + len(undated)


def test_a_planned_row_is_never_flagged(specialist):
    """Only the current step is judged; a planned row of the same step is not."""
    _matter, action = _flagged_teema(specialist)
    NextAction.objects.filter(pk=action.pk).update(status=ActionStatus.PLANNED)

    assert [row.consistency_alert for row in _rows_for(specialist, action)] == [None]


def test_a_plan_step_of_another_matter_proves_nothing(specialist):
    """The step must belong to the row's own Teema, as on the Teema page."""
    matter, action = _flagged_teema(specialist)
    other, _ = _flagged_teema(specialist, title="Teine")
    foreign = MatterPlanStep.objects.filter(matter=other).first()
    step = CurrentStep.of_action(matter, action)
    moved = CurrentStep(**{**step.__dict__, "plan_step_id": foreign.pk})

    assert steps_already_recorded([moved], specialist) == {}


def test_nothing_to_judge_reads_nothing(specialist):
    """A page with no step from the standard sequence pays no query at all."""
    matter = factories.MatterFactory(owner=specialist)
    ws.add_matter_engagement(
        matter=matter,
        author=specialist,
        audience="Liikmed",
        kind=EngagementKind.SURVEY.value,
        occurred_on=timezone.localdate(),
    )
    rows = work_rows(specialist)

    with CaptureQueriesContext(connection) as captured:
        assert with_consistency_alerts(rows, specialist) == rows

    assert len(captured) == 0


# ---------------------------------------------------------------------------
# Reader scoping (§5): never more than the reader may see
# ---------------------------------------------------------------------------


def test_restricted_evidence_proves_nothing_to_a_reader_who_may_not_see_it(
    specialist, administrator
):
    """The rows are scoped, and the evidence is scoped again on its own.

    Even handed the specialist's own rows, the rule asked for a reader who may
    not see the `Kaasamine` says nothing about it.
    """
    _matter, action = _flagged_teema(specialist)
    MatterEngagement.objects.filter(matter=_matter).update(
        visibility_override=Visibility.RESTRICTED
    )
    rows = [row for row in work_rows(specialist) if row.is_action and row.object_id == action.pk]

    assert with_consistency_alerts(rows, specialist)[0].consistency_alert is not None
    assert with_consistency_alerts(rows, administrator)[0].consistency_alert is None


def test_a_restricted_step_has_no_row_for_an_outsider(specialist, administrator):
    _matter, action = _flagged_teema(specialist)
    NextAction.objects.filter(pk=action.pk).update(visibility_override=Visibility.RESTRICTED)

    assert _rows_for(specialist, action)[0].consistency_alert is not None
    assert _rows_for(administrator, action) == []


def test_a_colleague_reading_the_same_step_is_told_the_same(specialist, other_specialist):
    """Since docs/adr/0181 every business reader reads the open record."""
    _matter, action = _flagged_teema(specialist)

    [mine] = _rows_for(specialist, action)
    [theirs] = _rows_for(other_specialist, action)

    assert theirs.consistency_alert == mine.consistency_alert


# ---------------------------------------------------------------------------
# Minu asjad
# ---------------------------------------------------------------------------


def _body(client, user, name, **kwargs):
    client.force_login(user)
    response = client.get(reverse(name, kwargs=kwargs or None))
    assert response.status_code == 200
    return response.content.decode()


def test_minu_asjad_marks_the_steps_own_row_once(client, specialist):
    _flagged_teema(specialist)

    body = _body(client, specialist, "matters:my_work")

    assert body.count(MARK) == 1
    assert f"{STEP_ALREADY_RECORDED_TITLE}." in body
    assert PROOF_ENGAGEMENT in body
    band = body[body.index(f'id="{wi.BAND_OVERDUE}"') :]
    assert MARK in band[: band.index("</section>")]


def test_an_undated_step_is_marked_in_kuupaevata(client, specialist):
    _flagged_teema(specialist, late=False)

    body = _body(client, specialist, "matters:my_work")

    rail = body[body.index('aria-label="Kuupäevata"') :]
    assert body.count(MARK) == 1
    assert MARK in rail[: rail.index("</section>")]


def test_a_row_never_carries_the_remedy(client, specialist):
    """The remedy names the Teema page's controls; the row opens that page."""
    _flagged_teema(specialist)

    body = _body(client, specialist, "matters:my_work")

    assert STEP_ALREADY_RECORDED_REMEDY not in body


def test_no_alert_no_markup_on_minu_asjad(client, specialist):
    _flagged_teema(specialist, record=False)

    assert "data-consistency-alert" not in _body(client, specialist, "matters:my_work")


def test_the_marker_moves_no_count_on_minu_asjad(specialist):
    """Bands, totals and the strip are the same with and without the alert."""
    matter, action = _flagged_teema(specialist, record=False)
    before = build_my_work(specialist)
    ws.add_matter_engagement(
        matter=matter,
        author=specialist,
        audience="Liikmed",
        kind=EngagementKind.SURVEY.value,
        occurred_on=timezone.localdate(),
        feedback_deadline=None,
    )
    after = build_my_work(specialist)

    assert [(f.key, f.value) for f in after.seis] == [(f.key, f.value) for f in before.seis]
    flagged = [item for band in after.bands for item in band.items if item.consistency_alert]
    assert [item.object_id for item in flagged] == [action.pk]
    assert [band.total for band in after.bands] == [band.total for band in before.bands]


def test_the_head_reading_a_desk_sees_the_marker_the_teema_shows_them(
    client, specialist, department_head
):
    """§5 is about the reader: the head reads this step on the Teema page too."""
    _flagged_teema(specialist)

    body = _body(client, department_head, "matters:person_work", pk=specialist.pk)

    assert body.count(MARK) == 1
    assert STEP_ALREADY_RECORDED_REMEDY not in body


def test_minu_asjad_query_count_does_not_grow_with_flagged_teemad(client, specialist):
    for n in range(2):
        _flagged_teema(specialist, title=f"Esimene {n}")
    client.force_login(specialist)
    with CaptureQueriesContext(connection) as few:
        client.get(reverse("matters:my_work"))

    for n in range(10):
        _flagged_teema(specialist, title=f"Teine {n}")
    with CaptureQueriesContext(connection) as many:
        body = client.get(reverse("matters:my_work")).content.decode()

    assert body.count(MARK) == 12
    assert len(many) == len(few), f"2 Teemad cost {len(few)} queries, 12 cost {len(many)}"


def test_the_batched_rule_reads_a_constant_number_of_times(specialist):
    for n in range(2):
        _flagged_teema(specialist, title=f"Esimene {n}")
    rows = work_rows(specialist)
    with CaptureQueriesContext(connection) as few:
        with_consistency_alerts(rows, specialist)

    for n in range(10):
        _flagged_teema(specialist, title=f"Teine {n}")
    rows = work_rows(specialist)
    with CaptureQueriesContext(connection) as many:
        flagged = with_consistency_alerts(rows, specialist)

    assert sum(1 for row in flagged if row.consistency_alert) == 12
    assert len(many) == len(few) <= 5


# ---------------------------------------------------------------------------
# Osakond — the department head's «Vajab sekkumist»
# ---------------------------------------------------------------------------


def test_the_head_sees_the_marker_on_the_intervention_row(client, specialist, department_head):
    _flagged_teema(specialist)

    body = _body(client, department_head, "matters:department")

    assert body.count(MARK) == 1
    section = body[body.index(MARK) - 3000 : body.index(MARK)]
    assert "interrow" in section
    assert STEP_ALREADY_RECORDED_REMEDY not in body


def test_a_specialist_on_osakond_is_not_shown_the_marker(client, specialist):
    """The amendment names the head; the specialist has Minu asjad for it."""
    _flagged_teema(specialist)

    assert "data-consistency-alert" not in _body(client, specialist, "matters:department")


def test_the_marker_adds_drops_and_counts_no_intervention_row(specialist, department_head):
    matter, action = _flagged_teema(specialist, record=False)
    before = dept.build_department(department_head, is_head=True)
    ws.add_matter_engagement(
        matter=matter,
        author=specialist,
        audience="Liikmed",
        kind=EngagementKind.SURVEY.value,
        occurred_on=timezone.localdate(),
    )
    after = dept.build_department(department_head, is_head=True)

    def shape(page):
        return [(row.reason, row.matter.pk, row.detail) for row in page.interventions]

    assert shape(after) == shape(before)
    assert after.intervention_total == before.intervention_total
    assert after.intervention_matters == before.intervention_matters
    flagged = [row for row in after.interventions if row.consistency_alert]
    assert [row.source.object_id for row in flagged] == [action.pk]
    assert flagged[0].consistency_alert == step_already_recorded(
        matter, NextAction.objects.get(pk=action.pk), department_head
    )


def test_osakond_query_count_does_not_grow_with_flagged_teemad(client, specialist, department_head):
    for n in range(2):
        _flagged_teema(specialist, title=f"Esimene {n}")
    client.force_login(department_head)
    with CaptureQueriesContext(connection) as few:
        client.get(reverse("matters:department"))

    for n in range(10):
        _flagged_teema(specialist, title=f"Teine {n}")
    with CaptureQueriesContext(connection) as many:
        body = client.get(reverse("matters:department")).content.decode()

    assert body.count(MARK) == 12
    assert len(many) == len(few), f"2 Teemad cost {len(few)} queries, 12 cost {len(many)}"


def test_drawing_the_markers_writes_nothing(client, specialist, department_head):
    matter, _action = _flagged_teema(specialist)
    snapshot = list(NextAction.objects.filter(matter=matter).values_list("pk", "status", "text"))
    steps = list(MatterPlanStep.objects.filter(matter=matter).values_list("pk", "state"))

    _body(client, specialist, "matters:my_work")
    _body(client, department_head, "matters:department")

    assert (
        list(NextAction.objects.filter(matter=matter).values_list("pk", "status", "text"))
        == snapshot
    )
    assert list(MatterPlanStep.objects.filter(matter=matter).values_list("pk", "state")) == steps
