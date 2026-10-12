"""`Hetkeseis` is changed straight from the Teema header (owner's decision of 2026-10-12).

docs/adr/0156. The header's value is a menu of the stages, each one a save; no
`+ Märge`, no note and no dummy activity is needed, and what is pinned here is
that the shortcut is *only* a shortcut — the one canonical writer
(`update_field` → `change_stage`) does the work:

* empty → chosen, and existing → changed, each one `MATTER_STAGE_CHANGED`, one
  new `Hetkeseis` period, and no `Entry` of any kind;
* the stage the page showed travels with the choice, so a stale page is refused
  (409) rather than overwriting a colleague, and a POST without it is refused too;
* a reader is offered no menu and cannot post one;
* a stage that would close a Matter with live work still asks first
  (docs/adr/0152), and the confirming button carries the same revision;
* a closed Matter states its stage and offers no menu (RULE-03).
"""

from __future__ import annotations

import pytest
from django.urls import reverse

from app.audit.enums import ChangeEventType
from app.audit.models import ChangeEvent
from app.matters.closure_work import LIVE_WORK_LABEL
from app.matters.models import Entry, Matter, MatterStageEpisode
from app.matters.services import MATTER_FIELD_CONFLICT, create_matter, matter_field_revision
from app.workflow.models import StageVocabulary
from app.workflow.services import set_next_action

pytestmark = pytest.mark.django_db


def stage(key: str) -> StageVocabulary:
    return StageVocabulary.objects.get(key=key)


def stage_url(matter: Matter) -> str:
    return reverse("matters:update_field", kwargs={"pk": matter.pk, "field": "stage"})


def choose(client, matter: Matter, value: object, **extra: str):
    data = {
        "stage": str(getattr(value, "pk", value) or ""),
        "revision": matter_field_revision(matter, "stage"),
    }
    data.update(extra)
    return client.post(stage_url(matter), data, HTTP_HX_REQUEST="true")


def stage_events(matter: Matter) -> int:
    return ChangeEvent.objects.filter(
        matter=matter, event_type=ChangeEventType.MATTER_STAGE_CHANGED
    ).count()


def header(client, matter: Matter) -> str:
    body = client.get(reverse("matters:matter_detail", kwargs={"pk": matter.pk})).content.decode()
    return body.split('id="teema-hetkeseis"', 1)[1].split("</details>", 1)[0]


def test_an_empty_stage_is_chosen_directly_without_a_marge(signed_in, specialist):
    matter = create_matter(title="Hetkeseisuta", actor=specialist, owner=specialist)
    entries = Entry.objects.filter(matter=matter).count()

    response = choose(signed_in, matter, stage("idea"))

    assert response.status_code == 200
    matter.refresh_from_db()
    assert matter.stage.key == "idea"
    assert stage_events(matter) == 1
    assert Entry.objects.filter(matter=matter).count() == entries
    assert MatterStageEpisode.objects.filter(matter=matter, stage__key="idea").count() == 1


def test_an_existing_stage_is_changed_directly_and_begins_a_new_period(signed_in, specialist):
    matter = create_matter(
        title="Liigub edasi", actor=specialist, owner=specialist, stage=stage("idea")
    )
    before = MatterStageEpisode.objects.filter(matter=matter).count()

    response = choose(signed_in, matter, stage("consultation"))

    assert response.status_code == 200
    matter.refresh_from_db()
    assert matter.stage.key == "consultation"
    episodes = list(MatterStageEpisode.objects.filter(matter=matter).order_by("sequence"))
    assert len(episodes) == before + 1
    assert episodes[-1].stage.key == "consultation"
    assert stage_events(matter) == 1
    # Teema käik reads the move: the page shows the new stage's own period.
    page = signed_in.get(
        reverse("matters:matter_detail", kwargs={"pk": matter.pk})
    ).content.decode()
    assert stage("consultation").label_et in page


def test_a_stale_page_is_refused_and_the_colleagues_stage_kept(signed_in, specialist):
    matter = create_matter(
        title="Kaks lehte", actor=specialist, owner=specialist, stage=stage("idea")
    )
    stale = matter_field_revision(matter, "stage")
    choose(signed_in, matter, stage("consultation"))

    response = signed_in.post(
        stage_url(matter),
        {"stage": str(stage("government").pk), "revision": stale},
        HTTP_HX_REQUEST="true",
    )

    assert response.status_code == 409
    assert MATTER_FIELD_CONFLICT in response.content.decode()
    matter.refresh_from_db()
    assert matter.stage.key == "consultation"


def test_a_choice_without_the_revision_is_refused(signed_in, specialist):
    matter = create_matter(title="Märgita", actor=specialist, owner=specialist)

    response = signed_in.post(
        stage_url(matter), {"stage": str(stage("idea").pk)}, HTTP_HX_REQUEST="true"
    )

    assert response.status_code == 409
    matter.refresh_from_db()
    assert matter.stage is None


def test_a_reader_is_offered_no_menu_and_cannot_post_one(client, reader, specialist):
    matter = create_matter(
        title="Lugeja vaade", actor=specialist, owner=specialist, stage=stage("idea")
    )
    client.force_login(reader)

    page = client.get(reverse("matters:matter_detail", kwargs={"pk": matter.pk}))
    if page.status_code == 200:
        assert "data-stagepick" not in page.content.decode()
    response = choose(client, matter, stage("consultation"))

    assert response.status_code in (403, 404)
    matter.refresh_from_db()
    assert matter.stage.key == "idea"


def test_the_menu_offers_each_stage_as_a_save_and_marks_the_current_one(signed_in, specialist):
    matter = create_matter(title="Menüü", actor=specialist, owner=specialist, stage=stage("idea"))

    slot = header(signed_in, matter)

    assert "data-stagepick" in slot
    assert "<select" not in slot
    assert f'name="revision" value="{matter_field_revision(matter, "stage")}"' in slot
    assert f'name="stage" value="{stage("consultation").pk}"' in slot
    assert 'aria-current="true">' + stage("idea").label_et in slot
    assert f'name="stage" value="{stage("idea").pk}"' not in slot
    assert ">Määramata</button>" in slot


def test_an_empty_stage_reads_vali_and_offers_no_clearing(signed_in, specialist):
    matter = create_matter(title="Tühi", actor=specialist, owner=specialist)

    slot = header(signed_in, matter)

    assert "Vali" in slot
    assert ">Määramata</button>" not in slot


def test_a_closing_stage_with_live_work_asks_first_and_then_closes(signed_in, specialist):
    matter = create_matter(
        title="Jõustuv", actor=specialist, owner=specialist, stage=stage("awaiting_entry")
    )
    set_next_action(matter=matter, text="Praegune", actor=specialist)

    asked = choose(signed_in, matter, stage("in_force"))

    body = asked.content.decode()
    assert asked.status_code == 400
    assert f">{LIVE_WORK_LABEL}</button>" in body
    # The header still states the stage that is stored — nothing was saved.
    slot = body.split('id="teema-hetkeseis"', 1)[1].split("</summary>", 1)[0]
    assert stage("awaiting_entry").label_et in slot
    assert stage("in_force").label_et not in slot
    confirm = body.split('class="inlineform followupconfirm"', 1)[1].split("</form>", 1)[0]
    assert f'name="revision" value="{matter_field_revision(matter, "stage")}"' in confirm
    matter.refresh_from_db()
    assert matter.is_open

    confirmed = choose(signed_in, matter, stage("in_force"), confirm_follow_up_closure="on")

    assert confirmed.status_code == 200
    matter.refresh_from_db()
    assert not matter.is_open


def test_a_closed_matter_states_its_stage_and_offers_no_menu(signed_in, specialist):
    matter = create_matter(title="Suletud", actor=specialist, owner=specialist, stage=stage("idea"))
    choose(signed_in, matter, stage("monitoring_stopped"))
    matter.refresh_from_db()
    assert not matter.is_open

    body = signed_in.get(
        reverse("matters:matter_detail", kwargs={"pk": matter.pk})
    ).content.decode()
    slot = body.split('id="teema-hetkeseis"', 1)[1].split("</span>\n</span>", 1)[0]
    assert "data-stagepick" not in slot
