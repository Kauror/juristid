"""`+ Märge · Tavaline`: one activity, one day, and the next step when it is ahead.

docs/adr/0124. The ordinary `Märge` asked for what happened and then, in a
second pair of boxes, for the next step and its day — so «Saadan ministeeriumile
kirja, 2.10» had to be written twice or put in the wrong box. The panel now asks
one `Tegevus` and one `Kuupäev`; a day after today offers
`Märgi järgmiseks tegevuseks`, ticked, and a save with it ticked makes that same
sentence and day the Matter's `Järgmiseks` through the ordinary step service.

Asserted here, each where it is decided:

* **the panel** — the label, the placeholder, no second pair of boxes, and the
  offer drawn hidden and disabled unless the day is ahead;
* **the rule** — past, today and an empty day never make a step, whatever the
  POST carries; ahead and ticked does; ahead and unticked does not;
* **the reuse** — the step is written by `set_next_action_for_new_work`, the
  service the old boxes called, and supersedes an open step exactly as they did;
* **the boundary** — the use case applies the same rule on its own clock, so a
  caller that skips the form cannot write a step on a day that is not ahead;
* **the neighbours** — `Oluline tähtaeg`, `Jõustumine` and `Töövõit` dated ahead
  still make no step, files and `Uus hetkeseis` still ride the same save, and
  correcting the `Märge` afterwards does not move the step it made.
"""

from __future__ import annotations

import datetime as dt
import re
from unittest import mock

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from django.utils import timezone

from app.audit.enums import ChangeEventType
from app.audit.models import ChangeEvent
from app.core.dates import format_estonian_date
from app.core.errors import DomainError
from app.documents.models import DocumentLink
from app.intelligence.models import MatterEffectiveDate, MatterImportantDate, MatterWorkVictory
from app.matters.forms import MatterProgressForm, ProceduralDevelopmentEditForm
from app.matters.models import MatterProceduralDevelopment
from app.matters.workspace import add_procedural_development
from app.workflow import services as workflow_services
from app.workflow.enums import ActionKind, ActionStatus, DatePrecision, DateSemantics
from app.workflow.models import NextAction
from app.workflow.services import NEXT_STEP_NEEDS_SENTENCE, set_next_action
from tests import factories

pytestmark = pytest.mark.django_db

PLACEHOLDER = "Kirjuta, mida tegid või mis on järgmine tegevus"
OFFER = "Märgi järgmiseks tegevuseks"


def _day(offset: int) -> dt.date:
    """A day relative to the application's clock, never written down.

    A fixed date is a test that changes meaning as the calendar moves past it —
    «2.10.2026» is ahead today and behind next week.
    """
    return timezone.localdate() + dt.timedelta(days=offset)


def _et(day: dt.date) -> str:
    return format_estonian_date(day)


def _teema(matter) -> str:
    return reverse("matters:matter_detail", kwargs={"pk": matter.pk})


def _add_note(matter) -> str:
    return reverse("matters:add_note", kwargs={"pk": matter.pk})


def _pdf(name: str) -> SimpleUploadedFile:
    return SimpleUploadedFile(name, b"%PDF-1.4 synthetic evidence", content_type="application/pdf")


def _panel(body: str) -> str:
    """`Märke liik · Tavaline`'s own panel, and nothing of its three siblings."""
    zone = body[body.index('id="lisa-teemale"') :]
    return zone[zone.index('id="marge-tavaline"') : zone.index('id="marge-tahtaeg"')]


def _offer(panel: str) -> str:
    """The `<label data-next-step-choice …>` element, open tag through close."""
    start = panel.index("data-next-step-choice")
    start = panel.rindex("<label", 0, start)
    return panel[start : panel.index("</label>", start)]


def _open_steps(matter):
    return NextAction.objects.filter(matter=matter, status=ActionStatus.OPEN)


def _existing_step(matter, actor) -> NextAction:
    return set_next_action(
        matter=matter,
        text="Koostan arvamuse eelnõule",
        kind=ActionKind.DO,
        date_semantics=DateSemantics.DEADLINE,
        target_date=_day(10),
        actor=actor,
    )


@pytest.fixture
def matter(specialist):
    return factories.MatterFactory(owner=specialist, stage=None)


# ---------------------------------------------------------------------------
# The panel
# ---------------------------------------------------------------------------


def test_the_panel_asks_tegevus_once(signed_in, matter):
    """One sentence, named for both tenses, and no second pair of boxes."""
    panel = _panel(signed_in.get(_teema(matter)).content.decode())

    assert re.search(r'cx-f__lab">\s*Tegevus\b', panel)
    assert f'placeholder="{PLACEHOLDER}"' in panel
    assert "Mis juhtus?" not in panel
    assert 'name="next_text"' not in panel
    assert 'name="next_date"' not in panel
    assert "Järgmine tegevus" not in panel
    assert "Millal?" not in panel
    # Everything else the panel had is still there.
    assert 'name="occurred_on"' in panel
    assert 'name="attachments"' in panel
    assert 'name="stage"' in panel
    assert "Jätan muutmata" in panel
    assert ">Salvesta<" in panel


def test_a_fresh_panel_draws_the_offer_hidden_disabled_and_ticked(signed_in, matter):
    """The day opens on today, so there is nothing ahead to offer yet.

    Ticked underneath, because that is what it shows when it appears; disabled,
    so a browser does not send a box nobody can see.
    """
    panel = _panel(signed_in.get(_teema(matter)).content.decode())
    offer = _offer(panel)

    assert OFFER in offer
    assert " hidden" in offer[: offer.index(">")]
    box = re.search(r"<input[^>]*name=\"as_next_step\"[^>]*>", offer).group(0)
    assert "checked" in box
    assert "disabled" in box


def test_the_panel_carries_the_application_s_today(signed_in, matter):
    """The script compares against the server's day, never the browser's."""
    panel = _panel(signed_in.get(_teema(matter)).content.decode())

    assert f'data-today="{timezone.localdate().isoformat()}"' in panel


def test_only_the_tavaline_panel_offers_it(signed_in, matter):
    """`Oluline tähtaeg`, `Jõustumine` and `Töövõit` do not grow the box."""
    body = signed_in.get(_teema(matter)).content.decode()

    assert body.count('name="as_next_step"') == 1


@pytest.mark.parametrize(
    ("offset", "offered"),
    [(-1, False), (0, False), (1, True), (30, True)],
    ids=["yesterday", "today", "tomorrow", "next-month"],
)
def test_the_offer_follows_the_day(offset, offered):
    form = MatterProgressForm({"occurred_on": _et(_day(offset))})

    assert form.next_step_offered is offered
    assert ("disabled" in form.fields["as_next_step"].widget.attrs) is not offered


@pytest.mark.parametrize("value", ["", "31.02.2026", "homme"])
def test_no_day_or_no_readable_day_offers_nothing(value):
    assert MatterProgressForm({"occurred_on": value}).next_step_offered is False


def test_a_fresh_form_offers_nothing():
    """Unbound, the box shows today — the one default the panel has."""
    assert MatterProgressForm().next_step_offered is False


def test_a_refused_save_ahead_redraws_the_offer_as_it_was_answered(signed_in, matter):
    """A refusal keeps the box visible, enabled and in the state it was sent."""
    response = signed_in.post(_add_note(matter), {"occurred_on": _et(_day(3))})

    assert response.status_code == 400
    offer = _offer(_panel(response.content.decode()))
    assert " hidden" not in offer[: offer.index(">")]
    box = re.search(r"<input[^>]*name=\"as_next_step\"[^>]*>", offer).group(0)
    assert "disabled" not in box
    assert "checked" not in box


# ---------------------------------------------------------------------------
# A. / B. Past and today: a record, and never a step
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("offset", [-1, 0], ids=["yesterday", "today"])
def test_a_past_or_today_activity_is_a_record_and_moves_no_step(
    signed_in, matter, specialist, offset
):
    """Even when the POST carries the box — a stale tick, or one put there by hand."""
    existing = _existing_step(matter, specialist)

    response = signed_in.post(
        _add_note(matter),
        {
            "title": "Saatsin ministeeriumile kirja",
            "occurred_on": _et(_day(offset)),
            "as_next_step": "on",
        },
    )

    assert response.status_code == 200
    record = MatterProceduralDevelopment.objects.get(matter=matter)
    assert record.title == "Saatsin ministeeriumile kirja"
    assert record.occurred_on == _day(offset)
    assert list(_open_steps(matter)) == [existing]
    existing.refresh_from_db()
    assert existing.text == "Koostan arvamuse eelnõule"
    assert existing.target_date == _day(10)
    assert NextAction.objects.filter(matter=matter).count() == 1


@pytest.mark.parametrize("offset", [-1, 0], ids=["yesterday", "today"])
def test_a_past_or_today_activity_creates_no_step_where_there_was_none(signed_in, matter, offset):
    response = signed_in.post(
        _add_note(matter),
        {"title": "Kohtusin ettevõtjaga", "occurred_on": _et(_day(offset)), "as_next_step": "on"},
    )

    assert response.status_code == 200
    assert MatterProceduralDevelopment.objects.filter(matter=matter).count() == 1
    assert not NextAction.objects.filter(matter=matter).exists()


def test_an_undated_activity_is_never_a_step(signed_in, matter):
    response = signed_in.post(
        _add_note(matter),
        {"title": "Saadan ministeeriumile kirja", "occurred_on": "", "as_next_step": "on"},
    )

    assert response.status_code == 200
    assert MatterProceduralDevelopment.objects.get(matter=matter).occurred_on is None
    assert not NextAction.objects.filter(matter=matter).exists()


def test_a_past_day_with_the_box_ticked_and_no_sentence_is_not_refused_as_a_step(
    signed_in, matter, stage
):
    """The box is inert on a day that is not ahead, so it asks for nothing either."""
    response = signed_in.post(
        _add_note(matter),
        {"occurred_on": _et(_day(-2)), "as_next_step": "on", "stage": str(stage.pk)},
    )

    assert response.status_code == 200
    assert NEXT_STEP_NEEDS_SENTENCE not in response.content.decode()
    assert not NextAction.objects.filter(matter=matter).exists()


# ---------------------------------------------------------------------------
# C. Ahead and ticked: the same sentence and day become the step
# ---------------------------------------------------------------------------


def test_an_activity_ahead_and_ticked_is_the_record_and_the_next_step(signed_in, matter):
    tomorrow = _day(1)

    response = signed_in.post(
        _add_note(matter),
        {
            "title": "Saadan ministeeriumile täpsustava kirja",
            "occurred_on": _et(tomorrow),
            "as_next_step": "on",
        },
    )

    assert response.status_code == 200
    record = MatterProceduralDevelopment.objects.get(matter=matter)
    assert record.title == "Saadan ministeeriumile täpsustava kirja"
    assert record.occurred_on == tomorrow
    step = _open_steps(matter).get()
    assert step.text == "Saadan ministeeriumile täpsustava kirja"
    assert step.target_date == tomorrow
    # What every native step stores (docs/adr/0052 §3): nothing new is invented.
    assert step.kind == ActionKind.DO
    assert step.date_semantics == DateSemantics.DEADLINE
    assert step.date_precision == DatePrecision.EXACT


def test_the_step_goes_through_the_service_the_old_boxes_used(matter, specialist):
    """`set_next_action_for_new_work`, called once, with the sentence and the day."""
    tomorrow = _day(1)
    with mock.patch(
        "app.matters.workspace.set_next_action_for_new_work",
        wraps=workflow_services.set_next_action_for_new_work,
    ) as service:
        result = add_procedural_development(
            matter=matter,
            author=specialist,
            title="Saadan ministeeriumile kirja",
            occurred_on=tomorrow,
            as_next_step=True,
        )

    service.assert_called_once()
    assert service.call_args.kwargs["text"] == "Saadan ministeeriumile kirja"
    assert service.call_args.kwargs["target_date"] == tomorrow
    assert result.action is not None
    assert result.action.pk == _open_steps(matter).get().pk


def test_a_step_made_here_supersedes_the_open_one_exactly_as_before(signed_in, matter, specialist):
    """The one-open invariant is the service's, and this panel does not change it."""
    existing = _existing_step(matter, specialist)

    signed_in.post(
        _add_note(matter),
        {"title": "Saadan kirja", "occurred_on": _et(_day(2)), "as_next_step": "on"},
    )

    existing.refresh_from_db()
    assert existing.status == ActionStatus.SUPERSEDED
    assert _open_steps(matter).get().text == "Saadan kirja"


def test_praegune_tegevus_and_minu_asjad_show_the_step(signed_in, matter):
    """What the rest of the product reads is the ordinary `NextAction`."""
    ahead = _day(2)
    signed_in.post(
        _add_note(matter),
        {
            "title": "Saadan ministeeriumile täpsustava kirja",
            "occurred_on": _et(ahead),
            "as_next_step": "on",
        },
    )

    body = signed_in.get(_teema(matter)).content.decode()
    current = body[body.index('id="praegune-tegevus"') : body.index('id="lisa-teemale"')]
    assert "Saadan ministeeriumile täpsustava kirja" in current
    assert _et(ahead) in current
    assert "Järgmine samm on määramata" not in current

    my_work = signed_in.get(reverse("matters:my_work")).content.decode()
    assert "Saadan ministeeriumile täpsustava kirja" in my_work


def test_an_activity_ahead_and_ticked_needs_its_sentence(signed_in, matter, stage):
    """A step is its sentence: refused on `Tegevus`, and nothing is written."""
    response = signed_in.post(
        _add_note(matter),
        {"occurred_on": _et(_day(1)), "as_next_step": "on", "stage": str(stage.pk)},
    )

    assert response.status_code == 400
    assert NEXT_STEP_NEEDS_SENTENCE in response.content.decode()
    assert not MatterProceduralDevelopment.objects.filter(matter=matter).exists()
    assert not NextAction.objects.filter(matter=matter).exists()
    matter.refresh_from_db()
    assert matter.stage_id is None


# ---------------------------------------------------------------------------
# D. Ahead and unticked: information, not a task
# ---------------------------------------------------------------------------


def test_an_activity_ahead_and_unticked_is_information(signed_in, matter, specialist):
    existing = _existing_step(matter, specialist)

    response = signed_in.post(
        _add_note(matter),
        {"title": "Ministeerium avaldab tulemused", "occurred_on": _et(_day(7))},
    )

    assert response.status_code == 200
    record = MatterProceduralDevelopment.objects.get(matter=matter)
    assert record.occurred_on == _day(7)
    assert list(_open_steps(matter)) == [existing]
    existing.refresh_from_db()
    assert existing.text == "Koostan arvamuse eelnõule"
    assert existing.target_date == _day(10)


def test_an_activity_ahead_and_unticked_creates_nothing_where_there_was_nothing(signed_in, matter):
    signed_in.post(
        _add_note(matter),
        {"title": "Ministeerium avaldab tulemused", "occurred_on": _et(_day(7))},
    )

    assert not NextAction.objects.filter(matter=matter).exists()


# ---------------------------------------------------------------------------
# The boundary: the use case applies the rule on its own clock
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("offset", [-3, 0], ids=["past", "today"])
def test_the_use_case_writes_no_step_on_a_day_that_is_not_ahead(matter, specialist, offset):
    """A caller that skips the form cannot make a step of a past or today's `Märge`."""
    result = add_procedural_development(
        matter=matter,
        author=specialist,
        title="Saatsin kirja",
        occurred_on=_day(offset),
        as_next_step=True,
    )

    assert result.action is None
    assert not NextAction.objects.filter(matter=matter).exists()


def test_the_use_case_refuses_a_step_with_no_sentence_and_writes_nothing(matter, specialist, stage):
    with pytest.raises(DomainError) as refusal:
        add_procedural_development(
            matter=matter,
            author=specialist,
            title="",
            occurred_on=_day(1),
            stage=stage,
            as_next_step=True,
        )

    assert str(refusal.value) == NEXT_STEP_NEEDS_SENTENCE
    assert not MatterProceduralDevelopment.objects.filter(matter=matter).exists()
    matter.refresh_from_db()
    assert matter.stage_id is None


def test_the_use_case_refuses_two_answers_to_one_question(matter, specialist):
    with pytest.raises(ValueError):
        add_procedural_development(
            matter=matter,
            author=specialist,
            title="Saadan kirja",
            occurred_on=_day(1),
            next_text="Midagi muud",
            as_next_step=True,
        )

    assert not MatterProceduralDevelopment.objects.filter(matter=matter).exists()


def test_the_form_drops_the_box_on_any_day_that_is_not_ahead():
    """The cleaned value is the answer the use case receives."""
    for offset in (-1, 0):
        form = MatterProgressForm(
            {"title": "Saatsin kirja", "occurred_on": _et(_day(offset)), "as_next_step": "on"}
        )
        assert form.is_valid(), form.errors
        assert form.cleaned_data["as_next_step"] is False

    ahead = MatterProgressForm(
        {"title": "Saadan kirja", "occurred_on": _et(_day(1)), "as_next_step": "on"}
    )
    assert ahead.is_valid(), ahead.errors
    assert ahead.cleaned_data["as_next_step"] is True


def test_an_absent_box_is_no_step_not_the_default():
    """Unticked in HTML is absent; `initial=True` is only what the box first shows."""
    form = MatterProgressForm({"title": "Saadan kirja", "occurred_on": _et(_day(1))})

    assert form.is_valid(), form.errors
    assert form.cleaned_data["as_next_step"] is False


# ---------------------------------------------------------------------------
# F. One save: file, state, record and step together
# ---------------------------------------------------------------------------


def test_file_state_record_and_step_are_one_save(signed_in, matter, stage, evidence_root):
    ahead = _day(4)
    before = set(ChangeEvent.objects.values_list("pk", flat=True))

    response = signed_in.post(
        _add_note(matter),
        {
            "title": "Saadan ministeeriumile täpsustava kirja",
            "occurred_on": _et(ahead),
            "as_next_step": "on",
            "stage": str(stage.pk),
            "attachments": [_pdf("eelnou_v3.pdf"), _pdf("seletuskiri.pdf")],
        },
    )

    assert response.status_code == 200
    record = MatterProceduralDevelopment.objects.get(matter=matter)
    linked = DocumentLink.objects.filter(procedural_development=record)
    assert sorted(link.document.title for link in linked) == ["eelnou_v3.pdf", "seletuskiri.pdf"]
    matter.refresh_from_db()
    assert matter.stage_id == stage.pk
    step = _open_steps(matter).get()
    assert (step.text, step.target_date) == ("Saadan ministeeriumile täpsustava kirja", ahead)

    # One operation: the record, its files, the stage and the step share it.
    events = ChangeEvent.objects.filter(matter=matter).exclude(pk__in=before)
    kinds = set(events.values_list("event_type", flat=True))
    assert ChangeEventType.NEXT_ACTION_SET in kinds
    assert ChangeEventType.MATTER_STAGE_CHANGED in kinds
    assert len(set(events.values_list("operation_id", flat=True))) == 1


def test_a_refused_file_leaves_no_step(signed_in, matter, evidence_root):
    """All or none: a rejected upload unwinds the record and the step with it."""
    response = signed_in.post(
        _add_note(matter),
        {
            "title": "Saadan kirja",
            "occurred_on": _et(_day(1)),
            "as_next_step": "on",
            "attachments": [SimpleUploadedFile("tühi.pdf", b"", content_type="application/pdf")],
        },
    )

    assert response.status_code == 400
    assert not MatterProceduralDevelopment.objects.filter(matter=matter).exists()
    assert not NextAction.objects.filter(matter=matter).exists()


# ---------------------------------------------------------------------------
# G. The three named kinds keep their meaning
# ---------------------------------------------------------------------------


def _post(client, route, matter, payload):
    return client.post(reverse(route, kwargs={"pk": matter.pk}), payload)


def test_an_important_date_ahead_is_not_a_next_step(signed_in, matter, specialist):
    existing = _existing_step(matter, specialist)

    response = _post(
        signed_in,
        "matters:add_important_date",
        matter,
        {
            "deadline_title": "Kooskõlastusringi lõpp",
            "deadline_date": _et(_day(5)),
            "deadline_precision": "EXACT",
            "as_next_step": "on",
        },
    )

    assert response.status_code == 200
    assert MatterImportantDate.objects.get(matter=matter).title == "Kooskõlastusringi lõpp"
    assert list(_open_steps(matter)) == [existing]
    assert NextAction.objects.filter(matter=matter).count() == 1


def test_an_effective_date_ahead_is_not_a_next_step(signed_in, matter):
    response = _post(
        signed_in,
        "matters:add_effective_date",
        matter,
        {
            "effective_title": "Pakendiseaduse muudatused",
            "effective_on": _et(_day(60)),
            "as_next_step": "on",
        },
    )

    assert response.status_code == 200
    assert MatterEffectiveDate.objects.get(matter=matter).description == (
        "Pakendiseaduse muudatused"
    )
    assert not NextAction.objects.filter(matter=matter).exists()


def test_a_work_victory_is_not_a_next_step(signed_in, matter):
    response = _post(
        signed_in,
        "matters:add_work_victory",
        matter,
        {
            "victory_change": "Üleminekuaeg pikendati",
            "victory_date": _et(_day(-1)),
            "as_next_step": "on",
        },
    )

    assert response.status_code == 200
    assert MatterWorkVictory.objects.filter(matter=matter).count() == 1
    assert not NextAction.objects.filter(matter=matter).exists()


# ---------------------------------------------------------------------------
# Correcting afterwards: the two records stay two
# ---------------------------------------------------------------------------


def test_the_correction_form_reads_the_panel_s_word():
    assert ProceduralDevelopmentEditForm.base_fields["title"].label == "Tegevus"
    assert "as_next_step" not in ProceduralDevelopmentEditForm.base_fields


def test_correcting_the_marge_does_not_move_the_step_it_made(signed_in, matter, specialist):
    """The step is a `NextAction` of its own, corrected through `Muuda` beside it."""
    ahead = _day(3)
    result = add_procedural_development(
        matter=matter,
        author=specialist,
        title="Saadan kirja",
        occurred_on=ahead,
        as_next_step=True,
    )
    record = result.record
    step = result.action
    assert step is not None

    response = signed_in.post(
        reverse(
            "matters:update_development",
            kwargs={"pk": matter.pk, "development_id": record.pk},
        ),
        {
            "title": "Saatsin kirja",
            "note": "",
            "occurred_on": _et(_day(-1)),
            "areng_precision": DatePrecision.EXACT,
            "areng_month": "",
            "areng_quarter": "",
            "areng_year": "",
            "revision": record.revision_token,
        },
    )

    assert response.status_code == 200
    record.refresh_from_db()
    assert (record.title, record.occurred_on) == ("Saatsin kirja", _day(-1))
    step.refresh_from_db()
    assert step.status == ActionStatus.OPEN
    assert (step.text, step.target_date) == ("Saadan kirja", ahead)


# ---------------------------------------------------------------------------
# Permissions are the route's, unchanged
# ---------------------------------------------------------------------------


def test_a_reader_without_business_write_writes_neither(client, matter, reader):
    client.force_login(reader)

    response = client.post(
        _add_note(matter),
        {"title": "Saadan kirja", "occurred_on": _et(_day(1)), "as_next_step": "on"},
    )

    assert response.status_code in (302, 403, 404)
    assert not MatterProceduralDevelopment.objects.filter(matter=matter).exists()
    assert not NextAction.objects.filter(matter=matter).exists()
