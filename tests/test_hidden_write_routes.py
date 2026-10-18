"""No live write route behind no button (owner decision D9, 2026-10-17; DC-G1).

The codebase's own rule is «gone, not hidden» (ENG-050A, docs/adr/0170 §5),
and the 2026-10-17 audit found three write routes that no page posts to:

* `matters:update_position` wrote `Koja seisukoht` / `Põhjendus` with no UI
  and silently dropped an invalid form. **Removed**: a POST is a 404 and
  writes nothing (also in `tests/test_retired_write_doors.py`).
* `matters:start_plan_step` turned a *structured* step into a generic
  `NextAction`, which docs/adr/0168 §1 replaced with «open the step's own
  form». The rule was kept only by which link the template drew, so a stale
  or crafted POST could still do it. **Refused on the server**, in
  `activate_plan_step`.
* `matters:add_note` (`+ Lisa · Tavaline` until 2026-10-07) still takes a
  `Märge`, its files, a `Hetkeseis` and a next step. More than a hundred test
  setups use it as a fixture door, so moving them is a follow-up. Until then
  it is **guarded**: it answers only where `ADD_NOTE_TEST_DOOR_ENABLED` is on,
  which defaults to `DEV_LOGIN_ENABLED`, and never beside real data. A
  real-data deployment therefore answers 404, as if the route were gone.
"""

from __future__ import annotations

import pytest
from django.urls import NoReverseMatch, reverse

from app.audit.enums import ChangeEventType
from app.audit.models import ChangeEvent
from app.matters.models import Matter, MatterProceduralDevelopment
from app.matters.services import set_position
from app.matters.step_launch import FORMS_BY_OPERATION
from app.workflow.enums import ActionStatus, PlanStepState
from app.workflow.models import MatterPlanStep, NextAction
from app.workflow.plan import (
    STEP_OPENS_ITS_OWN_FORM,
    STRUCTURED_OPERATIONS,
    activate_plan_step,
    seed_standard_plan,
)
from tests import factories
from tests.refusals import refused

pytestmark = pytest.mark.django_db

STRUCTURED_KEYS = ("website-overview", "consult-members", "send-opinion")


@pytest.fixture
def planned(specialist):
    matter = factories.MatterFactory(owner=specialist)
    seed_standard_plan(matter=matter, actor=specialist)
    return matter


def _step(matter, key) -> MatterPlanStep:
    return MatterPlanStep.objects.get(matter=matter, template_step_key=key)


# =========================================================================
# update_position — removed
# =========================================================================


def test_update_position_no_longer_resolves() -> None:
    with pytest.raises(NoReverseMatch):
        reverse("matters:update_position", kwargs={"pk": "00000000-0000-0000-0000-000000000000"})


def test_a_stale_position_post_is_404_and_writes_nothing(signed_in, specialist):
    matter = factories.MatterFactory(owner=specialist)
    set_position(matter=matter, position_summary="Koda toetab.", actor=specialist)
    events = ChangeEvent.objects.filter(matter=matter).count()

    response = signed_in.post(
        f"/teemad/{matter.pk}/seisukoht/salvesta/",
        {"position_summary": "Hiline seisukoht", "rationale_summary": "Hiline põhjendus"},
    )

    assert response.status_code == 404
    matter.refresh_from_db()
    assert (matter.position_summary, matter.rationale_summary) == ("Koda toetab.", "")
    assert ChangeEvent.objects.filter(matter=matter).count() == events


# =========================================================================
# start_plan_step — a structured step is refused on the server
# =========================================================================


def test_the_structured_operations_are_the_ones_with_a_canonical_form() -> None:
    """`app.workflow` may not import the Teema page's launcher, so the set is
    restated there; this keeps the two from drifting (docs/adr/0168 §1)."""
    assert set(STRUCTURED_OPERATIONS) == set(FORMS_BY_OPERATION)


@pytest.mark.parametrize("key", STRUCTURED_KEYS)
def test_starting_a_structured_step_is_refused_by_the_service(planned, specialist, key):
    step = _step(planned, key)

    with refused(STEP_OPENS_ITS_OWN_FORM):
        activate_plan_step(matter=planned, step=step, actor=specialist)

    step.refresh_from_db()
    assert step.state == PlanStepState.SUGGESTED
    assert not NextAction.objects.filter(matter=planned).exists()


@pytest.mark.parametrize("key", STRUCTURED_KEYS)
def test_a_crafted_alusta_for_a_structured_step_is_400_and_writes_nothing(signed_in, planned, key):
    step = _step(planned, key)

    response = signed_in.post(
        reverse("matters:start_plan_step", kwargs={"pk": planned.pk, "step_id": step.pk}),
        {"text": "Käsitsi alustatud"},
        headers={"HX-Request": "true"},
    )

    assert response.status_code == 400
    assert STEP_OPENS_ITS_OWN_FORM in response.content.decode()
    step.refresh_from_db()
    assert step.state == PlanStepState.SUGGESTED
    assert not NextAction.objects.filter(matter=planned).exists()
    assert not ChangeEvent.objects.filter(
        matter=planned, event_type=ChangeEventType.PLAN_STEP_ACTIVATED
    ).exists()


def test_a_generic_step_still_starts(signed_in, planned):
    step = _step(planned, "read-material")

    response = signed_in.post(
        reverse("matters:start_plan_step", kwargs={"pk": planned.pk, "step_id": step.pk}),
        {},
        headers={"HX-Request": "true"},
    )

    assert response.status_code == 200
    action = NextAction.objects.get(matter=planned, status=ActionStatus.OPEN)
    assert action.plan_step_id == step.pk


# =========================================================================
# add_note — guarded until its test uses move to real doors
# =========================================================================

NOTE = {"title": "Sünteetiline märge", "occurred_on": ""}


def test_with_the_door_off_add_note_is_404_and_writes_nothing(signed_in, specialist, settings):
    settings.ADD_NOTE_TEST_DOOR_ENABLED = False
    matter = factories.MatterFactory(owner=specialist)

    response = signed_in.post(
        reverse("matters:add_note", kwargs={"pk": matter.pk}),
        NOTE,
        headers={"HX-Request": "true"},
    )

    assert response.status_code == 404
    assert not MatterProceduralDevelopment.objects.filter(matter=matter).exists()
    assert Matter.objects.get(pk=matter.pk).stage_id == matter.stage_id


def test_with_the_door_on_add_note_still_files_the_marge(signed_in, specialist, settings):
    settings.ADD_NOTE_TEST_DOOR_ENABLED = True
    matter = factories.MatterFactory(owner=specialist)

    response = signed_in.post(
        reverse("matters:add_note", kwargs={"pk": matter.pk}),
        NOTE,
        headers={"HX-Request": "true"},
    )

    assert response.status_code == 200
    assert MatterProceduralDevelopment.objects.filter(matter=matter).count() == 1


def test_the_door_is_closed_beside_real_data_whatever_the_flag_says(
    signed_in, specialist, settings
):
    settings.ADD_NOTE_TEST_DOOR_ENABLED = True
    settings.REAL_DATA_ALLOWED = True
    matter = factories.MatterFactory(owner=specialist)

    response = signed_in.post(
        reverse("matters:add_note", kwargs={"pk": matter.pk}),
        NOTE,
        headers={"HX-Request": "true"},
    )

    assert response.status_code == 404
    assert not MatterProceduralDevelopment.objects.filter(matter=matter).exists()


def test_the_door_is_off_unless_the_development_sign_in_is() -> None:
    """The default follows `DEV_LOGIN_ENABLED`, which E002/E003 already keep
    away from production; the unit suite turns it on in `config/test_settings.py`."""
    import config.settings as deployed

    assert deployed.ADD_NOTE_TEST_DOOR_ENABLED == deployed.DEV_LOGIN_ENABLED
