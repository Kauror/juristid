"""The simplified Teema next-action workflow (ADR 0052).

The composer stopped asking a lawyer to classify their own work. Three
questions remain — *mida tegid või mis juhtus*, *järgmiseks*, *millal* — and
this module is the contract that keeps them three questions rather than five.

What is asserted here, and why each of them is a thing somebody could quietly
undo:

* the two text boxes stay two facts, neither derived from the other;
* a native step is stored `DO` / `DEADLINE` / `EXACT` and that is invisible;
* the classification vocabulary is not merely hidden — the form has no field
  that carries it, so a crafted POST cannot reintroduce it;
* every historical `WAIT` and `MONITOR` is untouched, unlabelled and still
  completable;
* `✓ Tehtud` completes, and completes *only* — no entry is invented;
* superseding a step is still not the same as completing it.

The domain underneath has its own suites and is unchanged. What is tested here
is that this surface reaches it, and that it stopped asking for things.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.urls import reverse
from django.utils import timezone

from app.audit.enums import ChangeEventType
from app.audit.models import ChangeEvent
from app.core.dates import format_estonian_date
from app.documents.enums import DocumentRole
from app.intelligence.models import MatterImportantDate
from app.matters.forms import ComposerForm
from app.matters.models import Entry
from app.workflow.enums import ActionKind, ActionStatus, DatePrecision, DateSemantics, Disposition
from app.workflow.models import NextAction
from app.workflow.services import current_next_action, set_next_action
from tests import factories

pytestmark = pytest.mark.django_db

#: Words this workflow retired from the Teema surface. Checked as a set on
#: every rendering assertion below, because removing one of the four and
#: leaving the other three is exactly the half-migration this module exists to
#: catch.
RETIRED_WORDS = ("TEEN", "OOTAN", "JÄLGIN", "Ei muuda")

#: And the date vocabulary that went with them, as the row used to shout it.
#: Checked inside the `Järgmiseks` row rather than over the whole page:
#: `Oluline tähtaeg` is a different concept, is still offered, and is still
#: spelled with the same word.
RETIRED_DATE_WORDS = ("TÄHTAEG", "VAATAN ÜLE", "OODATAV", "ÜLEVAATUS MÖÖDAS")


def _jargmiseks_row(body: str) -> str:
    """The `Järgmiseks` row alone, so an assertion about it cannot be answered
    by something else on the page."""
    start = body.index('id="jargmiseks-rida"')
    return " ".join(body[start : body.index("</div>", start)].split())


def _compose_url(matter) -> str:
    return reverse("matters:compose", kwargs={"pk": matter.pk})


def _detail(client, matter) -> str:
    return client.get(reverse("matters:matter_detail", kwargs={"pk": matter.pk})).content.decode()


def _post(client, matter, **fields):
    payload = {"kind": "NOTE", "attachment_role": DocumentRole.OTHER}
    payload.update(fields)
    return client.post(_compose_url(matter), payload, headers={"HX-Request": "true"})


def _in_a_week() -> str:
    return format_estonian_date(timezone.localdate() + timedelta(days=7))


# ---------------------------------------------------------------------------
# 1-3. The state matrix: what each combination of the two boxes writes
# ---------------------------------------------------------------------------


def test_a_body_alone_writes_an_entry_and_leaves_the_current_step_alone(
    signed_in, normal_matter, specialist
):
    """A. The ordinary save. This used to be spelled «Ei muuda»."""
    existing = set_next_action(
        matter=normal_matter,
        text="Helistada Kliimaministeeriumisse",
        kind=ActionKind.DO,
        date_semantics=DateSemantics.DEADLINE,
        target_date=timezone.localdate() + timedelta(days=3),
        actor=specialist,
    )

    response = _post(signed_in, normal_matter, body="<p>Ministeerium lubas uue versiooni.</p>")
    assert response.status_code == 200, response.content.decode()[:2000]

    assert Entry.objects.filter(matter=normal_matter).count() == 1
    existing.refresh_from_db()
    assert existing.status == ActionStatus.OPEN
    assert existing.text == "Helistada Kliimaministeeriumisse"
    assert current_next_action(normal_matter) == existing


def test_a_next_action_alone_writes_no_empty_entry(signed_in, normal_matter):
    """B. A supported case in its own right, and the one the old form refused.

    Under the previous contract the next step's wording *was* the entry body,
    so recording only what happens next was impossible: it always dragged an
    entry along saying the same sentence.
    """
    response = _post(
        signed_in,
        normal_matter,
        body="",
        next_text="Vaadata uus eelnõu versioon üle",
        next_date=_in_a_week(),
    )
    assert response.status_code == 200, response.content.decode()[:2000]

    assert not Entry.objects.filter(matter=normal_matter).exists()
    action = current_next_action(normal_matter)
    assert action is not None
    assert action.text == "Vaadata uus eelnõu versioon üle"


def test_a_body_and_a_next_action_are_two_different_records(signed_in, normal_matter):
    """C. The whole point of the change, in one save.

    What happened and what happens next are different sentences, and neither is
    derived from the other in either direction.
    """
    response = _post(
        signed_in,
        normal_matter,
        body="<p>Ministeerium lubas saata uue versiooni nädala lõpuks.</p>",
        next_text="Vaadata uus eelnõu versioon üle",
        next_date=_in_a_week(),
    )
    assert response.status_code == 200, response.content.decode()[:2000]

    entry = Entry.objects.get(matter=normal_matter)
    action = current_next_action(normal_matter)
    assert action is not None

    assert "Ministeerium lubas" in entry.body
    assert action.text == "Vaadata uus eelnõu versioon üle"
    # Neither leaked into the other. No splitting, no copying, no summarising.
    assert "Ministeerium" not in action.text
    assert "Vaadata uus eelnõu versioon üle" not in entry.body


# ---------------------------------------------------------------------------
# 4-5. The two refusals, each on the control that is empty
# ---------------------------------------------------------------------------


def test_a_next_action_without_a_date_is_refused_on_the_date(signed_in, normal_matter):
    """D. And never quietly filed for today."""
    response = _post(
        signed_in,
        normal_matter,
        body="<p>Käisin koosolekul.</p>",
        next_text="Vaadata uus versioon üle",
        next_date="",
    )
    assert response.status_code == 400

    form = response.context["composer_form"]
    assert "next_date" in form.errors
    assert form.errors["next_date"] == ["Vali järgmise tegevuse kuupäev."]
    assert "next_text" not in form.errors
    assert not NextAction.objects.filter(matter=normal_matter).exists()
    assert not Entry.objects.filter(matter=normal_matter).exists()


def test_a_date_without_a_next_action_is_refused_on_the_text(signed_in, normal_matter):
    """E. A date on its own is asking for a step, so it is answered as one."""
    response = _post(
        signed_in,
        normal_matter,
        body="<p>Käisin koosolekul.</p>",
        next_text="",
        next_date=_in_a_week(),
    )
    assert response.status_code == 400

    form = response.context["composer_form"]
    assert form.errors["next_text"] == ["Kirjuta järgmine tegevus."]
    assert "next_date" not in form.errors
    assert not NextAction.objects.filter(matter=normal_matter).exists()


def test_a_date_alone_is_not_reported_as_an_empty_save(signed_in, normal_matter):
    """The refusal has to point at the box, not say "you typed nothing".

    Something *was* typed. Falling through to the composer's empty-save guard
    would raise a non-field error that says the opposite and names no control.
    """
    response = _post(signed_in, normal_matter, body="", next_text="", next_date=_in_a_week())
    assert response.status_code == 400
    form = response.context["composer_form"]
    assert not form.non_field_errors()
    assert "next_text" in form.errors


# ---------------------------------------------------------------------------
# 6-8. The other composer paths still save on their own
# ---------------------------------------------------------------------------


def test_an_attachment_alone_still_saves(signed_in, normal_matter, pdf_bytes):
    """F, part one. No body, no next step — a file, and it lands."""
    from django.core.files.uploadedfile import SimpleUploadedFile

    response = signed_in.post(
        _compose_url(normal_matter),
        {
            "body": "",
            "next_text": "",
            "next_date": "",
            "kind": "NOTE",
            "attachment": SimpleUploadedFile("kiri.pdf", pdf_bytes, content_type="application/pdf"),
            "attachment_role": DocumentRole.OTHER,
        },
        headers={"HX-Request": "true"},
    )
    assert response.status_code == 200, response.content.decode()[:2000]
    assert normal_matter.documents.count() == 1
    assert not NextAction.objects.filter(matter=normal_matter).exists()


def test_an_important_deadline_alone_still_saves(signed_in, normal_matter):
    """F, part two — and the period control it keeps.

    `Oluline tähtaeg` is the surface where "in the autumn" is a real answer, so
    it keeps the precision group the next step gave up (ADR 0052 §4).
    """
    response = _post(
        signed_in,
        normal_matter,
        body="",
        next_text="",
        next_date="",
        deadline_title="Kooskõlastusring lõpeb",
        deadline_precision=DatePrecision.QUARTER,
        deadline_quarter="3",
        deadline_year=str(timezone.localdate().year),
    )
    assert response.status_code == 200, response.content.decode()[:2000]

    deadline = MatterImportantDate.objects.get(matter=normal_matter)
    assert deadline.title == "Kooskõlastusring lõpeb"
    assert deadline.date_precision == DatePrecision.QUARTER
    assert not NextAction.objects.filter(matter=normal_matter).exists()


def test_closing_the_matter_still_works_with_neither_box(signed_in, normal_matter, specialist):
    """F, part three. Closure is its own path and this change does not touch it."""
    set_next_action(
        matter=normal_matter,
        text="Saata kiri",
        kind=ActionKind.DO,
        date_semantics=DateSemantics.DEADLINE,
        target_date=timezone.localdate() + timedelta(days=2),
        actor=specialist,
    )

    response = _post(
        signed_in,
        normal_matter,
        body="<p>Menetlus lõppes.</p>",
        next_text="",
        next_date="",
        disposition=Disposition.COMPLETED,
        work_victory="EI",
    )
    assert response.status_code == 200, response.content.decode()[:2000]

    normal_matter.refresh_from_db()
    assert not normal_matter.is_open
    assert current_next_action(normal_matter) is None


def test_nothing_at_all_is_still_refused(signed_in, normal_matter):
    response = _post(signed_in, normal_matter, body="", next_text="", next_date="")
    assert response.status_code == 400
    assert response.context["composer_form"].non_field_errors()


# ---------------------------------------------------------------------------
# 9-10. Superseding, completing, and the difference between them
# ---------------------------------------------------------------------------


def test_a_new_step_supersedes_the_open_one_rather_than_completing_it(
    signed_in, normal_matter, specialist
):
    """9 + ADR 0052 §8. The distinction survives the simpler UI.

    A lawyer who replaces a step did not necessarily do the old one. Marking it
    `COMPLETED` because a replacement arrived would put work in the history
    that nobody did.
    """
    first = set_next_action(
        matter=normal_matter,
        text="Saata kiri",
        kind=ActionKind.DO,
        date_semantics=DateSemantics.DEADLINE,
        target_date=timezone.localdate() + timedelta(days=2),
        actor=specialist,
    )

    _post(
        signed_in,
        normal_matter,
        body="",
        next_text="Helistada ministeeriumile",
        next_date=_in_a_week(),
    )

    first.refresh_from_db()
    assert first.status == ActionStatus.SUPERSEDED
    assert first.status != ActionStatus.COMPLETED
    current = current_next_action(normal_matter)
    assert current is not None
    assert current.text == "Helistada ministeeriumile"
    assert first.replaced_by == current
    assert not ChangeEvent.objects.filter(
        matter=normal_matter, event_type=ChangeEventType.NEXT_ACTION_COMPLETED
    ).exists()


def test_tehtud_completes_the_step_and_writes_no_entry(signed_in, normal_matter, specialist):
    """10 + ADR 0052 §7. The system already knows what was completed.

    Manufacturing "Helistasin Kliimaministeeriumisse" as a note would be the
    application writing a lawyer's record for them under their name.
    """
    action = set_next_action(
        matter=normal_matter,
        text="Helistada Kliimaministeeriumisse",
        kind=ActionKind.DO,
        date_semantics=DateSemantics.DEADLINE,
        target_date=timezone.localdate() + timedelta(days=1),
        actor=specialist,
    )

    response = signed_in.post(
        reverse("matters:complete_action", kwargs={"pk": normal_matter.pk, "action_id": action.pk}),
        headers={"HX-Request": "true"},
    )
    assert response.status_code == 200

    action.refresh_from_db()
    assert action.status == ActionStatus.COMPLETED
    assert action.ended_by == specialist
    assert ChangeEvent.objects.filter(
        matter=normal_matter,
        event_type=ChangeEventType.NEXT_ACTION_COMPLETED,
        object_id=action.pk,
    ).exists()

    # The whole of it. No entry, and no replacement step.
    assert not Entry.objects.filter(matter=normal_matter).exists()
    assert current_next_action(normal_matter) is None
    assert NextAction.objects.filter(matter=normal_matter).count() == 1


def test_tehtud_answers_with_the_row_and_not_the_whole_column(signed_in, normal_matter, specialist):
    """ADR 0052 §9 — the server half of "do not lose unsaved composer content".

    The browser half is `e2e/test_simplified_next_action.py`. What is asserted
    here is that the response is a fragment small enough not to contain the
    composer at all: a response that carried `#teema-vaade` would replace the
    open form however the target was written.
    """
    action = set_next_action(
        matter=normal_matter,
        text="Saata kiri ministeeriumile",
        kind=ActionKind.DO,
        date_semantics=DateSemantics.DEADLINE,
        target_date=timezone.localdate() + timedelta(days=1),
        actor=specialist,
    )

    response = signed_in.post(
        reverse("matters:complete_action", kwargs={"pk": normal_matter.pk, "action_id": action.pk}),
        headers={"HX-Request": "true"},
    )
    body = response.content.decode()

    assert 'id="jargmiseks-rida"' in body
    assert 'id="teema-vaade"' not in body
    assert 'id="teema-koostaja"' not in body
    assert "composer__body" not in body


def test_the_completion_target_is_the_row(signed_in, normal_matter, specialist):
    """And the page asks for it. A fragment response into `#teema-vaade` would
    put one row where the whole column was."""
    set_next_action(
        matter=normal_matter,
        text="Saata kiri",
        kind=ActionKind.DO,
        date_semantics=DateSemantics.DEADLINE,
        target_date=timezone.localdate() + timedelta(days=1),
        actor=specialist,
    )
    body = _detail(signed_in, normal_matter)
    row = _jargmiseks_row(body)
    assert "/valmis/" in row, "the row no longer offers the completion route"
    complete_form = row[row.index("/valmis/") : row.index("Tehtud")]
    assert 'hx-target="#jargmiseks-rida"' in complete_form
    assert 'hx-target="#teema-vaade"' not in complete_form


# ---------------------------------------------------------------------------
# 11-12. Historical WAIT and MONITOR: stored, unlabelled, completable
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("kind", "semantics", "retired_label"),
    [
        (ActionKind.WAIT, DateSemantics.EXPECTED_AROUND, "OOTAN"),
        (ActionKind.MONITOR, DateSemantics.REVIEW_ON, "JÄLGIN"),
    ],
)
def test_a_historical_action_keeps_its_kind_and_never_shows_it(
    signed_in, normal_matter, specialist, kind, semantics, retired_label
):
    action = set_next_action(
        matter=normal_matter,
        text="Ootan ministeeriumi vastust",
        kind=kind,
        date_semantics=semantics,
        target_date=timezone.localdate() + timedelta(days=5),
        actor=specialist,
    )

    body = _detail(signed_in, normal_matter)
    flat = " ".join(body.split())

    # Rendered, honestly: the sentence and the date.
    assert "Ootan ministeeriumi vastust" in flat
    assert action.display_date in flat

    # Classified, silently.
    action.refresh_from_db()
    assert action.kind == kind
    assert action.date_semantics == semantics
    assert retired_label not in flat
    assert f"modechip--{kind.lower()}" not in body

    row = _jargmiseks_row(body)
    for word in RETIRED_DATE_WORDS:
        assert word not in row, f"the row still names the date «{word}»"


@pytest.mark.parametrize("kind", [ActionKind.WAIT, ActionKind.MONITOR])
def test_a_historical_action_can_still_be_completed_from_the_teema_page(
    signed_in, normal_matter, specialist, kind
):
    action = set_next_action(
        matter=normal_matter,
        text="Jälgida eelnõu menetlust",
        kind=kind,
        date_semantics=DateSemantics.REVIEW_ON,
        target_date=timezone.localdate() + timedelta(days=5),
        actor=specialist,
    )

    response = signed_in.post(
        reverse("matters:complete_action", kwargs={"pk": normal_matter.pk, "action_id": action.pk}),
        headers={"HX-Request": "true"},
    )
    assert response.status_code == 200

    action.refresh_from_db()
    assert action.status == ActionStatus.COMPLETED
    # Completing it did not rewrite what it was.
    assert action.kind == kind
    assert action.date_semantics == DateSemantics.REVIEW_ON


def test_an_undated_historical_action_is_shown_without_a_date(signed_in, normal_matter, specialist):
    set_next_action(
        matter=normal_matter,
        text="Jälgida menetlust",
        kind=ActionKind.MONITOR,
        date_semantics=DateSemantics.REVIEW_ON,
        target_date=None,
        actor=specialist,
    )
    body = _detail(signed_in, normal_matter)
    assert "Jälgida menetlust" in body
    assert "uxnext__date" not in body


def test_an_approximate_historical_action_keeps_its_period_wording(
    signed_in, normal_matter, specialist
):
    """The composer only writes EXACT. What is already stored is not rewritten."""
    action = set_next_action(
        matter=normal_matter,
        text="Jälgida sügisest menetlust",
        kind=ActionKind.MONITOR,
        date_semantics=DateSemantics.REVIEW_ON,
        target_date=timezone.localdate().replace(month=9, day=1),
        date_precision=DatePrecision.MONTH,
        actor=specialist,
    )
    body = _detail(signed_in, normal_matter)
    assert action.display_date in body
    assert action.date_precision == DatePrecision.MONTH


# ---------------------------------------------------------------------------
# The form's own shape: what it stores, and what it refuses to accept
# ---------------------------------------------------------------------------


def test_a_native_step_is_stored_do_deadline_exact(signed_in, normal_matter):
    target = timezone.localdate() + timedelta(days=7)
    _post(
        signed_in,
        normal_matter,
        body="",
        next_text="Vaadata uus versioon üle",
        next_date=format_estonian_date(target),
    )

    action = current_next_action(normal_matter)
    assert action is not None
    assert action.kind == ActionKind.DO
    assert action.date_semantics == DateSemantics.DEADLINE
    assert action.date_precision == DatePrecision.EXACT
    assert action.target_date == target


def test_the_composer_has_no_field_for_a_kind_or_a_date_meaning(normal_matter, specialist):
    """Removed, not hidden.

    A form that still declares the field accepts it however the page renders,
    so this is the assertion that stops the classification returning through a
    crafted POST (ADR 0052 §3).
    """
    form = ComposerForm(matter=normal_matter, viewer=specialist)
    for gone in (
        "next_kind",
        "next_date_semantics",
        "next_precision",
        "next_month",
        "next_quarter",
        "next_half",
        "next_year",
    ):
        assert gone not in form.fields, f"{gone} is still a composer field"
    # The deadline keeps its whole period group; it is the surface that needs it.
    for kept in ("deadline_precision", "deadline_month", "deadline_quarter", "deadline_year"):
        assert kept in form.fields


def test_a_crafted_post_cannot_choose_a_kind_or_a_date_meaning(signed_in, normal_matter):
    _post(
        signed_in,
        normal_matter,
        body="",
        next_text="Kontrollida, kas ministeerium vastas",
        next_date=_in_a_week(),
        next_kind=ActionKind.WAIT,
        next_date_semantics=DateSemantics.EXPECTED_AROUND,
        next_precision=DatePrecision.MONTH,
    )

    action = current_next_action(normal_matter)
    assert action is not None
    assert action.kind == ActionKind.DO
    assert action.date_semantics == DateSemantics.DEADLINE
    assert action.date_precision == DatePrecision.EXACT


def test_the_next_action_text_is_stored_exactly_as_typed(signed_in, normal_matter):
    _post(
        signed_in,
        normal_matter,
        body="<p>Ministeerium lubas vastata.</p>",
        next_text="  Kontrollida, kas ministeerium vastas  ",
        next_date=_in_a_week(),
    )
    action = current_next_action(normal_matter)
    assert action is not None
    assert action.text == "Kontrollida, kas ministeerium vastas"


# ---------------------------------------------------------------------------
# What the two surfaces render
# ---------------------------------------------------------------------------


def test_the_composer_asks_three_questions_and_no_classification(signed_in, normal_matter):
    body = _detail(signed_in, normal_matter)
    flat = " ".join(body.split())

    assert "Mida tegid või mis juhtus?" in flat
    assert "Järgmiseks" in flat
    assert "Millal?" in flat
    assert 'name="next_text"' in body

    for word in RETIRED_WORDS:
        assert word not in flat, f"the composer still offers «{word}»"
    assert 'name="next_kind"' not in body
    assert 'name="next_date_semantics"' not in body
    assert "Mida kuupäev" not in flat
    assert "Täpsemalt…" not in flat


def test_the_body_placeholder_stopped_asking_for_both_at_once(signed_in, normal_matter):
    body = _detail(signed_in, normal_matter)
    assert "Kirjelda, mis tegid ja mida teed edasi" not in body
    assert "Kirjelda, mida tegid või mis juhtus…" in body


def test_the_current_step_shows_its_text_its_date_and_tehtud(signed_in, normal_matter, specialist):
    """State A of the approved design, asserted on the rendered page."""
    target = timezone.localdate() + timedelta(days=22)
    action = set_next_action(
        matter=normal_matter,
        text="Vaadata uus eelnõu versioon üle",
        kind=ActionKind.DO,
        date_semantics=DateSemantics.DEADLINE,
        target_date=target,
        actor=specialist,
    )

    body = _detail(signed_in, normal_matter)
    flat = " ".join(body.split())

    assert "Vaadata uus eelnõu versioon üle" in flat
    assert action.display_date in flat
    assert "Tehtud" in flat
    for word in RETIRED_WORDS:
        assert word not in flat

    row = _jargmiseks_row(body)
    for word in RETIRED_DATE_WORDS:
        assert word not in row


def test_an_overdue_step_still_reads_as_late(signed_in, normal_matter, specialist):
    """Without the word «TÄHTAEG» in front of it."""
    set_next_action(
        matter=normal_matter,
        text="Saata kiri",
        kind=ActionKind.DO,
        date_semantics=DateSemantics.DEADLINE,
        target_date=timezone.localdate() - timedelta(days=6),
        actor=specialist,
    )
    body = _detail(signed_in, normal_matter)
    flat = " ".join(body.split())

    assert "uxnext--overdue" in body
    assert "uxnext__date--overdue" in body
    assert "6 p" in flat
    assert "TÄHTAEG MÖÖDAS" not in flat


def test_the_empty_state_stays_quiet(signed_in, normal_matter):
    body = _detail(signed_in, normal_matter)
    assert "Järgmine samm on määramata" in body
    assert "Määra allpool" not in body


# ---------------------------------------------------------------------------
# Sildid: retired from the page, untouched in the database
# ---------------------------------------------------------------------------


def test_sildid_are_gone_from_the_teema_detail_page(signed_in, normal_matter):
    tag = factories.TagFactory(name_et="Käibemaks")
    normal_matter.tags.add(tag)

    body = _detail(signed_in, normal_matter)
    assert "Sildid" not in body
    assert "Silte ei ole." not in body
    assert "Käibemaks" not in body


def test_reading_the_page_does_not_touch_stored_tags(signed_in, normal_matter):
    """A UI retirement, not a data migration (ADR 0052 §10)."""
    tag = factories.TagFactory(name_et="Käibemaks")
    normal_matter.tags.add(tag)

    _detail(signed_in, normal_matter)

    normal_matter.refresh_from_db()
    assert list(normal_matter.tags.all()) == [tag]


def test_muu_valdkond_is_in_the_teema_facts_block(signed_in, normal_matter):
    """It is not a tag, and it did not leave with the card it happened to be in."""
    normal_matter.policy_area_other = "Riigihanked ja ehitus"
    normal_matter.save(update_fields=["policy_area_other"])

    body = _detail(signed_in, normal_matter)
    facts = body[body.index('id="teema-andmed"') :]
    facts = facts[: facts.index("</aside>")]
    assert "Muu valdkond" in facts
    assert "Riigihanked ja ehitus" in facts


def test_muu_valdkond_is_still_editable_in_place(signed_in, normal_matter):
    response = signed_in.post(
        reverse(
            "matters:update_field",
            kwargs={"pk": normal_matter.pk, "field": "policy_area_other"},
        ),
        {"policy_area_other": "Riigihanked"},
        headers={"HX-Request": "true"},
    )
    assert response.status_code == 200, response.content.decode()[:1000]
    normal_matter.refresh_from_db()
    assert normal_matter.policy_area_other == "Riigihanked"


# ---------------------------------------------------------------------------
# Authorization is unchanged
# ---------------------------------------------------------------------------


def test_a_reader_sees_the_step_but_not_tehtud_and_not_the_composer(
    client, reader, normal_matter, specialist
):
    set_next_action(
        matter=normal_matter,
        text="Vaadata uus versioon üle",
        kind=ActionKind.DO,
        date_semantics=DateSemantics.DEADLINE,
        target_date=timezone.localdate() + timedelta(days=3),
        actor=specialist,
    )
    client.force_login(reader)
    body = _detail(client, normal_matter)

    assert "Vaadata uus versioon üle" in body
    assert "Tehtud" not in body
    assert 'name="next_text"' not in body
    assert 'id="teema-koostaja"' not in body


def test_a_reader_cannot_complete_a_step(client, reader, normal_matter, specialist):
    action = set_next_action(
        matter=normal_matter,
        text="Vaadata uus versioon üle",
        kind=ActionKind.DO,
        date_semantics=DateSemantics.DEADLINE,
        target_date=timezone.localdate() + timedelta(days=3),
        actor=specialist,
    )
    client.force_login(reader)
    response = client.post(
        reverse("matters:complete_action", kwargs={"pk": normal_matter.pk, "action_id": action.pk})
    )
    assert response.status_code == 404
    action.refresh_from_db()
    assert action.status == ActionStatus.OPEN


def test_a_refused_completion_says_so_inside_the_row(signed_in, normal_matter, specialist):
    """The refusal has nowhere else to go.

    `Tehtud` swaps this row and only this row, and the refusal
    `complete_next_action` actually raises — somebody else finished the step a
    moment ago — leaves no current action to hang a message on. So the message
    is rendered outside the row's three branches rather than inside one of them.
    """
    from app.workflow.services import complete_next_action

    action = set_next_action(
        matter=normal_matter,
        text="Saata kiri",
        kind=ActionKind.DO,
        date_semantics=DateSemantics.DEADLINE,
        target_date=timezone.localdate() + timedelta(days=1),
        actor=specialist,
    )
    complete_next_action(action=action, actor=specialist)

    response = signed_in.post(
        reverse("matters:complete_action", kwargs={"pk": normal_matter.pk, "action_id": action.pk}),
        headers={"HX-Request": "true"},
    )
    assert response.status_code == 400
    body = response.content.decode()
    assert 'id="jargmiseks-rida"' in body
    assert "Ainult kehtivat tegevust saab lõpetada." in body
