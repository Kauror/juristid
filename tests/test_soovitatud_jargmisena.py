"""`Tööplaan` is gone from view; `Soovitatud järgmisena` stays (docs/adr/0141).

* **A** — no visible plan: no `TÖÖPLAAN`, no editor, no `+ Lisa samm`, no
  `Muuda plaani`, no `taidab_sammu`;
* **B** — with nothing current and a step ahead, `PRAEGUNE TEGEVUS` suggests it,
  with `Alusta` and `×`;
* **C** — `Alusta` makes that exact step the current action, and the suggestion
  goes while it is current;
* **D** — completing it completes the step, and the next one is suggested;
* **E** — `×` dismisses the exact step for good, the next is suggested, and with
  none left the block is gone;
* **F** — a stale `×` cannot dismiss a different suggestion;
* **G** — `Muuda` keeps the step link, and completing the edited action still
  advances;
* **H** — `+ Lisa tegevus` is unlinked work and moves no suggestion;
* **I** — old plan rows render with no visible plan.
"""

from __future__ import annotations

import pytest
from django.urls import NoReverseMatch, reverse

from app.audit.enums import ChangeEventType
from app.audit.models import ChangeEvent
from app.workflow.enums import ActionStatus, PlanStepState
from app.workflow.models import MatterPlanStep, NextAction
from app.workflow.plan import STALE_PLAN_REFUSAL, plan_revision, plan_steps_of, seed_standard_plan
from tests import factories

pytestmark = pytest.mark.django_db

FIRST, SECOND, THIRD, FOURTH, FIFTH = (
    "Tutvu materjaliga",
    "Koosta kodulehe ülevaade",
    "Kaasa liikmeid / küsi tagasisidet",
    "Koonda tagasiside ja kujunda Koja seisukoht",
    "Saada Koja arvamus",
)

#: The plan administration this round removed, by route name.
REMOVED_ROUTES = (
    "seed_plan",
    "add_plan_step",
    "edit_plan_step",
    "skip_plan_step",
    "restore_plan_step",
    "repeat_plan_step",
    "move_plan_step",
)


@pytest.fixture
def planned(specialist):
    """An open Matter with the standard background sequence and nothing current."""
    matter = factories.MatterFactory(owner=specialist)
    seed_standard_plan(matter=matter, actor=specialist)
    return matter


def _detail(client, matter) -> str:
    return client.get(reverse("matters:matter_detail", kwargs={"pk": matter.pk})).content.decode()


def _zone(body: str) -> str:
    return body[body.index('id="praegune-tegevus"') : body.index('id="lisa-teemale"')]


def _suggested(body: str) -> str | None:
    zone = _zone(body)
    if 'id="soovitus"' not in zone:
        return None
    block = zone[zone.index('id="soovitus"') :]
    start = block.index('class="curact__suggesttext">') + len('class="curact__suggesttext">')
    return block[start : block.index("</span>", start)]


def _post(client, name: str, matter, data: dict, **kwargs):
    return client.post(
        reverse(f"matters:{name}", kwargs={"pk": matter.pk, **kwargs}),
        data,
        headers={"HX-Request": "true"},
    )


def _step(matter, title: str) -> MatterPlanStep:
    return MatterPlanStep.objects.get(matter=matter, title=title)


def _start(client, matter, title: str, **data):
    return _post(client, "start_plan_step", matter, data, step_id=_step(matter, title).pk)


def _dismiss(client, matter, title: str, revision: str | None = None):
    if revision is None:
        revision = plan_revision(plan_steps_of(matter))
    return _post(
        client,
        "dismiss_plan_step",
        matter,
        {"revision": revision},
        step_id=_step(matter, title).pk,
    )


def _open_action(matter) -> NextAction:
    return NextAction.objects.get(matter=matter, status=ActionStatus.OPEN)


def _complete(client, matter, **extra):
    return _post(
        client,
        "complete_current_action",
        matter,
        {"action_id": str(_open_action(matter).pk), "body": "<p>Tehtud.</p>", **extra},
    )


# ---------------------------------------------------------------------------
# A — no visible plan
# ---------------------------------------------------------------------------


def test_a_no_visible_tooplaan(signed_in, planned):
    body = _detail(signed_in, planned)

    assert 'id="tooplaan"' not in body
    assert "TÖÖPLAAN" not in body
    assert "Muuda plaani" not in body
    assert 'id="muuda-plaani"' not in body
    assert "taidab_sammu" not in body
    assert 'name="plan_step"' not in body
    # The rest of the sequence is not listed anywhere.
    for title in (SECOND, THIRD, FOURTH, FIFTH):
        assert title not in body, title
    # `+ Lisa samm` only as `Menetluse kulg`'s own editor (docs/adr/0119).
    index = body.find("+ Lisa samm")
    while index != -1:
        assert "menetluse-kulg" in body[max(0, index - 4000) : index]
        index = body.find("+ Lisa samm", index + 1)


@pytest.mark.parametrize("name", REMOVED_ROUTES)
def test_a_the_plan_administration_routes_are_gone(name):
    with pytest.raises(NoReverseMatch):
        reverse(
            f"matters:{name}",
            kwargs={
                "pk": "00000000-0000-0000-0000-000000000000",
                "step_id": "00000000-0000-0000-0000-000000000000",
            },
        )


# ---------------------------------------------------------------------------
# B — the suggestion
# ---------------------------------------------------------------------------


def test_b_a_matter_with_nothing_current_suggests_its_next_step(signed_in, planned):
    zone = _zone(_detail(signed_in, planned))

    assert "Järgmine samm on määramata" in zone
    assert "Järgmisena?" in zone
    assert _suggested(_detail(signed_in, planned)) == FIRST
    assert ">Alusta</summary>" in zone
    assert 'aria-label="Eemalda soovitus"' in zone
    assert 'title="Eemalda soovitus"' in zone
    assert "Kustuta" not in zone[zone.index('id="soovitus"') :]
    assert "+ Lisa tegevus" in zone


def test_b_uus_teema_seeds_the_background_sequence(signed_in):
    from app.matters.models import Matter

    signed_in.post(reverse("matters:matter_create"), {"title": "Soovitusega teema"})

    matter = Matter.objects.get(title="Soovitusega teema")
    assert MatterPlanStep.objects.filter(matter=matter).count() == 5
    assert not NextAction.objects.filter(matter=matter).exists()
    assert _suggested(_detail(signed_in, matter)) == FIRST


# ---------------------------------------------------------------------------
# C, D — start, complete, advance
# ---------------------------------------------------------------------------


def test_c_alusta_makes_the_exact_step_current_and_hides_the_suggestion(signed_in, planned):
    response = _start(signed_in, planned, FIRST, text=FIRST)

    assert response.status_code == 200
    action = _open_action(planned)
    assert action.text == FIRST
    assert action.plan_step_id == _step(planned, FIRST).pk
    body = _detail(signed_in, planned)
    assert 'id="soovitus"' not in body
    assert "Järgmisena?" not in body


def test_d_completing_it_completes_the_step_and_suggests_the_next(signed_in, planned):
    _start(signed_in, planned, FIRST, text=FIRST)

    response = _complete(signed_in, planned)

    assert response.status_code == 200
    assert _step(planned, FIRST).state == PlanStepState.COMPLETED
    assert _suggested(_detail(signed_in, planned)) == SECOND


# ---------------------------------------------------------------------------
# E, F — dismiss, and a stale dismiss
# ---------------------------------------------------------------------------


def test_e_dismiss_is_persistent_and_the_next_is_suggested(signed_in, planned):
    response = _dismiss(signed_in, planned, FIRST)

    assert response.status_code == 200
    assert _step(planned, FIRST).state == PlanStepState.SKIPPED
    assert ChangeEvent.objects.filter(event_type=ChangeEventType.PLAN_STEP_SKIPPED).count() == 1
    # A reload does not bring it back.
    assert _suggested(_detail(signed_in, planned)) == SECOND
    assert _suggested(_detail(signed_in, planned)) == SECOND


def test_e_with_every_suggestion_dismissed_the_block_is_gone(signed_in, planned):
    for title in (FIRST, SECOND, THIRD, FOURTH, FIFTH):
        assert _dismiss(signed_in, planned, title).status_code == 200

    zone = _zone(_detail(signed_in, planned))

    assert 'id="soovitus"' not in zone
    assert "Järgmisena?" not in zone
    assert "Järgmine samm on määramata" in zone
    assert "+ Lisa tegevus" in zone


def test_f_a_stale_dismiss_cannot_dismiss_a_different_suggestion(signed_in, planned):
    drawn = plan_revision(plan_steps_of(planned))
    # Another tab dismissed the first suggestion meanwhile.
    _dismiss(signed_in, planned, FIRST)

    # The stale page's `×` named the first step — already gone.
    stale_first = _dismiss(signed_in, planned, FIRST, revision=drawn)
    # And a crafted post naming the second at the old revision is refused too.
    stale_second = _dismiss(signed_in, planned, SECOND, revision=drawn)

    assert stale_first.status_code == 400
    assert stale_second.status_code == 400
    assert STALE_PLAN_REFUSAL in stale_second.content.decode()
    assert _step(planned, SECOND).state == PlanStepState.SUGGESTED
    assert MatterPlanStep.objects.filter(matter=planned, state=PlanStepState.SKIPPED).count() == 1


def test_f_the_current_step_is_never_dismissed(signed_in, planned):
    _start(signed_in, planned, FIRST, text=FIRST)

    response = _dismiss(signed_in, planned, FIRST)

    assert response.status_code == 400
    assert _step(planned, FIRST).state == PlanStepState.PLANNED
    assert _open_action(planned).plan_step_id == _step(planned, FIRST).pk


def test_f_a_stale_alusta_refuses_once_the_step_was_dismissed(signed_in, planned):
    _dismiss(signed_in, planned, FIRST)

    response = _start(signed_in, planned, FIRST, text=FIRST)

    assert response.status_code == 400
    assert not NextAction.objects.filter(matter=planned).exists()


# ---------------------------------------------------------------------------
# G — Muuda keeps the link
# ---------------------------------------------------------------------------


def test_g_muuda_keeps_the_step_and_completion_still_advances(signed_in, planned):
    _start(signed_in, planned, FIRST, text=FIRST)
    original = _open_action(planned)

    _post(
        signed_in,
        "set_action",
        planned,
        {"action_id": str(original.pk), "text": "Loen eelnõu ja seletuskirja", "target_date": ""},
    )

    replacement = _open_action(planned)
    assert replacement.pk != original.pk
    assert replacement.text == "Loen eelnõu ja seletuskirja"
    assert replacement.plan_step_id == _step(planned, FIRST).pk

    _complete(signed_in, planned)

    assert _step(planned, FIRST).state == PlanStepState.COMPLETED
    assert _suggested(_detail(signed_in, planned)) == SECOND


# ---------------------------------------------------------------------------
# H — manual work is not the suggestion
# ---------------------------------------------------------------------------


def test_h_a_manual_next_action_is_unlinked_and_moves_no_suggestion(signed_in, planned):
    _post(signed_in, "set_action", planned, {"text": FIRST, "target_date": ""})

    action = _open_action(planned)
    assert action.plan_step_id is None  # same words, still not the step
    assert 'id="soovitus"' not in _detail(signed_in, planned)

    _complete(signed_in, planned)

    assert _step(planned, FIRST).state == PlanStepState.SUGGESTED
    assert _suggested(_detail(signed_in, planned)) == FIRST


def test_h_completing_with_a_written_next_action_links_nothing(signed_in, planned):
    _start(signed_in, planned, FIRST, text=FIRST)

    _complete(signed_in, planned, next_text="Koostan ise kokkuvõtte")

    assert _step(planned, FIRST).state == PlanStepState.COMPLETED
    assert _open_action(planned).plan_step_id is None
    assert _step(planned, SECOND).state == PlanStepState.SUGGESTED


# ---------------------------------------------------------------------------
# I — old data, and the records that never fulfil a step
# ---------------------------------------------------------------------------


def test_i_an_old_matter_with_plan_rows_in_every_state_renders(signed_in, specialist):
    matter = factories.MatterFactory(owner=specialist)
    seed_standard_plan(matter=matter, actor=specialist)
    MatterPlanStep.objects.create(matter=matter, title="Oma lisatud samm", position=9)
    _start(signed_in, matter, FIRST, text=FIRST)
    _complete(signed_in, matter)
    _dismiss(signed_in, matter, SECOND)

    response = signed_in.get(reverse("matters:matter_detail", kwargs={"pk": matter.pk}))

    assert response.status_code == 200
    body = response.content.decode()
    assert 'id="tooplaan"' not in body
    assert "Oma lisatud samm" not in body
    assert _suggested(body) == THIRD


def test_i_a_closed_matter_suggests_nothing(signed_in, planned, specialist):
    from app.matters.services import close_matter
    from app.workflow.enums import Disposition

    close_matter(matter=planned, disposition=Disposition.COMPLETED, actor=specialist)

    body = _detail(signed_in, planned)

    assert 'id="soovitus"' not in body
    assert 'id="tooplaan"' not in body


def test_i_a_kaasamine_save_fulfils_no_step(signed_in, planned):
    _post(
        signed_in,
        "add_engagement_compact",
        planned,
        {"audience": "Liikmed", "taidab_sammu": str(_step(planned, THIRD).pk)},
    )

    assert _step(planned, THIRD).state == PlanStepState.SUGGESTED
    # Nothing is written, but the suggestion reads the record since docs/adr/0144
    # §2: a consultation proves the material was read and the members invited,
    # so the first step still unproven is the overview.
    assert _suggested(_detail(signed_in, planned)) == SECOND
