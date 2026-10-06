"""`Tööplaan` is no longer an active feature (docs/adr/0141).

The ordinary workflow is the current action: complete it or change it,
optionally move `Hetkeseis`, optionally write the next action. Nothing here
draws, suggests, starts or finishes a plan step.

* **A — the Teema page**: no `TÖÖPLAAN` section, no `+ Lisa samm` in it, no
  `Muuda plaani`, no `Soovitatud järgmisena` / `Alusta`; the current action
  still renders;
* **B — the current action**: written, completed, changed and followed by a
  next action with no plan step anywhere;
* **C — business records**: `Kaasamine`, `Koja arvamus` and `Ülevaade /
  uudis` save with no plan-step control and finish nothing they were not told
  to; a posted `taidab_sammu` from an old tab is ignored;
* **D — old data**: a Matter that still holds `MatterPlanStep` rows, and an
  open action pointing at one, renders and works, and the rows are left
  exactly as they were — no row rewritten, no event written;
* **E — removed routes**: the plan's addresses are gone, nothing on the page
  posts to them, and creating a Teema seeds no plan.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import NoReverseMatch, reverse
from django.utils import timezone

from app.audit.enums import ChangeEventType
from app.audit.models import ChangeEvent
from app.matters.models import Matter, MatterEngagement, MatterWebsiteOverview
from app.submissions.enums import SubmissionStatus
from app.submissions.models import Submission
from app.workflow.enums import ActionStatus, PlanStepOperation, PlanStepSource, PlanStepState
from app.workflow.models import MatterPlanStep, NextAction
from app.workflow.services import set_next_action_for_new_work
from tests import factories

pytestmark = pytest.mark.django_db

PLAN_EVENTS = (
    ChangeEventType.PLAN_SEEDED,
    ChangeEventType.PLAN_STEP_ADDED,
    ChangeEventType.PLAN_STEP_CHANGED,
    ChangeEventType.PLAN_STEP_MOVED,
    ChangeEventType.PLAN_STEP_SKIPPED,
    ChangeEventType.PLAN_STEP_RESTORED,
    ChangeEventType.PLAN_STEP_ACTIVATED,
    ChangeEventType.PLAN_STEP_COMPLETED,
)

#: The plan's former routes, by name.
REMOVED_ROUTES = (
    "seed_plan",
    "add_plan_step",
    "edit_plan_step",
    "start_plan_step",
    "skip_plan_step",
    "restore_plan_step",
    "repeat_plan_step",
    "move_plan_step",
)


def _et(day) -> str:
    return f"{day.day}.{day.month}.{day.year}"


def _detail(client, matter) -> str:
    return client.get(reverse("matters:matter_detail", kwargs={"pk": matter.pk})).content.decode()


def _post(client, name: str, matter, data: dict):
    return client.post(
        reverse(f"matters:{name}", kwargs={"pk": matter.pk}),
        data,
        headers={"HX-Request": "true"},
    )


def _pdf(name: str = "arvamus.pdf") -> SimpleUploadedFile:
    return SimpleUploadedFile(name, b"%PDF-1.4\n%%EOF", content_type="application/pdf")


def _old_plan(matter) -> dict[str, MatterPlanStep]:
    """The standard plan as a Matter seeded before docs/adr/0141 holds it.

    Written straight into the table, as the rows already in the database were:
    no service seeds a plan any more.
    """
    rows = (
        ("read-material", "Tutvu materjaliga", PlanStepOperation.GENERIC),
        ("website-overview", "Koosta kodulehe ülevaade", PlanStepOperation.WEBSITE_OVERVIEW),
        ("consult-members", "Kaasa liikmeid / küsi tagasisidet", PlanStepOperation.ENGAGEMENT),
        ("send-opinion", "Saada Koja arvamus", PlanStepOperation.SUBMISSION),
    )
    return {
        operation: MatterPlanStep.objects.create(
            matter=matter,
            position=index,
            title=title,
            source=PlanStepSource.TEMPLATE,
            operation=operation,
            state=PlanStepState.SUGGESTED,
            template_key="standard-legislative",
            template_version=1,
            template_step_key=key,
        )
        for index, (key, title, operation) in enumerate(rows)
    }


def _snapshot(matter) -> list[tuple]:
    return list(
        MatterPlanStep.objects.filter(matter=matter)
        .order_by("position")
        .values_list("pk", "state", "title", "position", "updated_at", "fulfilled_by_record")
    )


@pytest.fixture
def organisation():
    return factories.OrganisationFactory(name="Sünteetiline ministeerium")


@pytest.fixture
def old_matter(specialist):
    """An open Matter with an old plan and an open action started from its first step."""
    matter = factories.MatterFactory(owner=specialist)
    steps = _old_plan(matter)
    action = set_next_action_for_new_work(
        matter=matter,
        text="Tutvu materjaliga",
        target_date=timezone.localdate() + timedelta(days=3),
        actor=specialist,
    )
    NextAction.objects.filter(pk=action.pk).update(plan_step=steps[PlanStepOperation.GENERIC])
    return matter


# ---------------------------------------------------------------------------
# A — the Teema page
# ---------------------------------------------------------------------------


def test_the_teema_page_draws_no_tooplaan(signed_in, normal_matter, specialist):
    set_next_action_for_new_work(matter=normal_matter, text="Koosta kokkuvõte", actor=specialist)

    body = _detail(signed_in, normal_matter)

    assert 'id="praegune-tegevus"' in body
    assert "Koosta kokkuvõte" in body
    assert 'id="tooplaan"' not in body
    assert "Tööplaan" not in body
    assert "TÖÖPLAAN" not in body
    assert "Muuda plaani" not in body
    assert "Soovitatud järgmisena" not in body
    assert ">Alusta<" not in body
    assert 'id="alusta-samm"' not in body
    assert 'id="samm-toiming"' not in body
    assert "taidab_sammu" not in body
    assert 'name="plan_step"' not in body


def test_lisa_samm_is_only_the_procedure_rails_own(signed_in, normal_matter):
    """`+ Lisa samm` survives only as `Menetluse kulg`'s editor (docs/adr/0119),
    a different feature: steps of the external procedure, not of a work plan."""
    body = _detail(signed_in, normal_matter)

    index = body.find("+ Lisa samm")
    while index != -1:
        assert "menetluse-kulg" in body[max(0, index - 4000) : index]
        index = body.find("+ Lisa samm", index + 1)
    assert "/plaan/" not in body


def test_the_zones_run_current_action_then_lisa_teemale_then_tegevused(
    signed_in, normal_matter, specialist
):
    set_next_action_for_new_work(matter=normal_matter, text="Koosta kokkuvõte", actor=specialist)

    body = _detail(signed_in, normal_matter)

    current = body.index('id="praegune-tegevus"')
    add = body.index('id="lisa-teemale"')
    history = body.index('id="ajajoon"')
    assert current < add < history
    assert "workplan" not in body[current:add]


def test_a_file_with_no_step_offers_only_the_direct_next_action(signed_in, normal_matter):
    body = _detail(signed_in, normal_matter)
    zone = body[body.index('id="praegune-tegevus"') : body.index('id="lisa-teemale"')]

    assert "Järgmine samm on määramata" in zone
    assert "+ Määra järgmine tegevus" in zone
    assert "Alusta" not in zone


# ---------------------------------------------------------------------------
# B — the current action, with no plan step anywhere
# ---------------------------------------------------------------------------


def test_the_current_action_is_written_completed_changed_and_followed(signed_in, normal_matter):
    matter = normal_matter
    _post(signed_in, "set_action", matter, {"text": "Loen eelnõu läbi", "target_date": ""})
    first = NextAction.objects.get(matter=matter, status=ActionStatus.OPEN)
    assert first.plan_step_id is None

    _post(
        signed_in,
        "set_action",
        matter,
        {"action_id": str(first.pk), "text": "Loen eelnõu ja seletuskirja", "target_date": ""},
    )
    changed = NextAction.objects.get(matter=matter, status=ActionStatus.OPEN)
    assert changed.text == "Loen eelnõu ja seletuskirja"
    assert changed.plan_step_id is None

    response = _post(
        signed_in,
        "complete_current_action",
        matter,
        {
            "action_id": str(changed.pk),
            "body": "<p>Lugesin läbi.</p>",
            "next_text": "Koostan kokkuvõtte",
            "next_date": _et(timezone.localdate() + timedelta(days=7)),
        },
    )

    assert response.status_code == 200
    changed.refresh_from_db()
    assert changed.status == ActionStatus.COMPLETED
    following = NextAction.objects.get(matter=matter, status=ActionStatus.OPEN)
    assert following.text == "Koostan kokkuvõtte"
    assert following.plan_step_id is None
    assert not MatterPlanStep.objects.filter(matter=matter).exists()
    assert not ChangeEvent.objects.filter(event_type__in=PLAN_EVENTS).exists()


# ---------------------------------------------------------------------------
# C — business records save with no plan step, and finish nothing unasked
# ---------------------------------------------------------------------------


def test_the_three_add_forms_carry_no_plan_step_control(signed_in, specialist):
    matter = factories.MatterFactory(owner=specialist)
    _old_plan(matter)

    body = _detail(signed_in, matter)
    add = body[body.index('id="lisa-teemale"') : body.index('id="ajajoon"')]

    assert "taidab_sammu" not in add
    assert "Tööplaani samm" not in add
    assert "(soovitus)" not in add


def test_kaasamine_saves_and_finishes_nothing(signed_in, specialist):
    matter = factories.MatterFactory(owner=specialist)
    action = set_next_action_for_new_work(matter=matter, text="Kaasa liikmeid", actor=specialist)

    response = _post(
        signed_in, "add_engagement_compact", matter, {"audience": "Liikmed (sünteetiline)"}
    )

    assert response.status_code == 200
    assert MatterEngagement.objects.filter(matter=matter).count() == 1
    action.refresh_from_db()
    assert action.status == ActionStatus.OPEN


def test_ulevaade_saves_and_finishes_nothing(signed_in, specialist):
    matter = factories.MatterFactory(owner=specialist)
    action = set_next_action_for_new_work(matter=matter, text="Kirjuta ülevaade", actor=specialist)

    response = _post(
        signed_in,
        "add_website_overview",
        matter,
        {
            "url": "https://www.koda.ee/et/sunteetiline-ulevaade",
            "published_on": _et(timezone.localdate()),
        },
    )

    assert response.status_code == 200
    assert MatterWebsiteOverview.objects.filter(matter=matter).count() == 1
    action.refresh_from_db()
    assert action.status == ActionStatus.OPEN


def test_koja_arvamus_saves_and_finishes_only_what_is_ticked(signed_in, specialist, organisation):
    matter = factories.MatterFactory(owner=specialist)
    action = set_next_action_for_new_work(matter=matter, text="Saada arvamus", actor=specialist)
    payload = {
        "recipients": [str(organisation.pk)],
        "sent_on": _et(timezone.localdate()),
        "summary": "Sünteetiline arvamus.",
    }

    _post(signed_in, "add_koda_opinion", matter, {"upload": _pdf("esimene.pdf"), **payload})
    action.refresh_from_db()
    assert action.status == ActionStatus.OPEN

    # `Märgi praegune tegevus tehtuks` is not part of the plan and stays.
    _post(
        signed_in,
        "add_koda_opinion",
        matter,
        {
            "upload": _pdf("teine.pdf"),
            "complete_action": str(action.pk),
            **payload,
        },
    )
    assert Submission.objects.filter(matter=matter, status=SubmissionStatus.SENT).count() == 2
    action.refresh_from_db()
    assert action.status == ActionStatus.COMPLETED


def test_a_plan_step_posted_from_an_old_tab_is_ignored(signed_in, specialist, organisation):
    """A page drawn before docs/adr/0141 may still post `taidab_sammu` or the
    typed launch's `plan_step`: the record is saved and the step is untouched."""
    matter = factories.MatterFactory(owner=specialist)
    steps = _old_plan(matter)
    before = _snapshot(matter)

    _post(
        signed_in,
        "add_engagement_compact",
        matter,
        {"audience": "Liikmed", "taidab_sammu": str(steps[PlanStepOperation.ENGAGEMENT].pk)},
    )
    _post(
        signed_in,
        "add_koda_opinion",
        matter,
        {
            "upload": _pdf(),
            "recipients": [str(organisation.pk)],
            "sent_on": _et(timezone.localdate()),
            "taidab_sammu": str(steps[PlanStepOperation.SUBMISSION].pk),
            "plan_step": str(steps[PlanStepOperation.SUBMISSION].pk),
        },
    )

    assert MatterEngagement.objects.filter(matter=matter).count() == 1
    assert Submission.objects.filter(matter=matter).count() == 1
    assert _snapshot(matter) == before
    assert not ChangeEvent.objects.filter(event_type__in=PLAN_EVENTS).exists()


# ---------------------------------------------------------------------------
# D — old data
# ---------------------------------------------------------------------------


def test_an_old_matter_with_plan_rows_renders_without_them(signed_in, old_matter):
    body = _detail(signed_in, old_matter)

    assert 'id="praegune-tegevus"' in body
    assert "Tutvu materjaliga" in body  # the open action's own words
    assert 'id="tooplaan"' not in body
    assert "Saada Koja arvamus" not in body
    assert "Kaasa liikmeid / küsi tagasisidet" not in body
    assert "Soovitatud järgmisena" not in body


def test_an_old_plan_linked_action_completes_and_leaves_the_plan_as_it_was(signed_in, old_matter):
    before = _snapshot(old_matter)
    action = NextAction.objects.get(matter=old_matter, status=ActionStatus.OPEN)
    assert action.plan_step_id is not None

    response = _post(
        signed_in,
        "complete_current_action",
        old_matter,
        {
            "action_id": str(action.pk),
            "body": "<p>Lugesin materjali läbi.</p>",
            "next_text": "Kirjutan ülevaate",
        },
    )

    assert response.status_code == 200
    action.refresh_from_db()
    assert action.status == ActionStatus.COMPLETED
    assert action.plan_step_id is not None  # history is not rewritten
    following = NextAction.objects.get(matter=old_matter, status=ActionStatus.OPEN)
    assert following.plan_step_id is None
    assert _snapshot(old_matter) == before
    assert not ChangeEvent.objects.filter(event_type__in=PLAN_EVENTS).exists()


def test_an_old_plan_linked_action_can_be_changed(signed_in, old_matter):
    before = _snapshot(old_matter)
    action = NextAction.objects.get(matter=old_matter, status=ActionStatus.OPEN)

    _post(
        signed_in,
        "set_action",
        old_matter,
        {"action_id": str(action.pk), "text": "Loen ka seletuskirja", "target_date": ""},
    )

    replacement = NextAction.objects.get(matter=old_matter, status=ActionStatus.OPEN)
    assert replacement.text == "Loen ka seletuskirja"
    assert replacement.plan_step_id is None
    action.refresh_from_db()
    assert action.status == ActionStatus.SUPERSEDED
    assert action.plan_step_id is not None
    assert _snapshot(old_matter) == before


def test_a_closed_old_matter_with_plan_rows_renders(signed_in, specialist):
    from app.matters.services import close_matter
    from app.workflow.enums import Disposition

    matter = factories.MatterFactory(owner=specialist)
    _old_plan(matter)
    close_matter(matter=matter, disposition=Disposition.COMPLETED, actor=specialist)

    response = signed_in.get(reverse("matters:matter_detail", kwargs={"pk": matter.pk}))

    assert response.status_code == 200
    body = response.content.decode()
    assert 'id="tooplaan"' not in body
    assert "Tööplaan teema sulgemise ajal" not in body
    assert MatterPlanStep.objects.filter(matter=matter).count() == 4


# ---------------------------------------------------------------------------
# E — the plan's routes are gone, and nothing seeds a plan
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", REMOVED_ROUTES)
def test_the_plans_routes_are_gone(name):
    with pytest.raises(NoReverseMatch):
        reverse(f"matters:{name}", kwargs={"pk": "00000000-0000-0000-0000-000000000000"})


def test_an_old_plan_address_is_not_found(signed_in, old_matter):
    step = MatterPlanStep.objects.filter(matter=old_matter).first()

    response = signed_in.post(f"/teemad/{old_matter.pk}/plaan/{step.pk}/alusta/", {})

    assert response.status_code == 404
    assert NextAction.objects.filter(matter=old_matter, status=ActionStatus.OPEN).count() == 1


def test_uus_teema_seeds_no_plan(signed_in):
    signed_in.post(
        reverse("matters:matter_create"),
        {
            "title": "Plaanita teema",
            "response_deadline": _et(timezone.localdate() + timedelta(days=30)),
        },
    )

    matter = Matter.objects.get(title="Plaanita teema")
    assert not MatterPlanStep.objects.filter(matter=matter).exists()
    assert not NextAction.objects.filter(matter=matter).exists()
    assert not ChangeEvent.objects.filter(event_type=ChangeEventType.PLAN_SEEDED).exists()


def test_the_plan_service_modules_are_gone():
    import importlib

    for module in ("app.workflow.plan", "app.matters.plan_view"):
        with pytest.raises(ModuleNotFoundError):
            importlib.import_module(module)
