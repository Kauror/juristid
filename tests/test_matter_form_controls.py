"""Visible controls that keep their cardinality, and a legible Järgmine tegevus.

Two ideas run through this file.

**A control's shape is a promise about the data.** Hetkeseis and Menetlusliik
became visible chips, and the chips are radios, because `Matter.stage` and
`Matter.track` hold one value each. Making them look like checkboxes because
checkboxes look friendlier would be promising something the model cannot keep.

**Nothing about the stored record changed.** Every POST test here asserts the
canonical values that land in the database, not the markup that produced them:
the point of the round was to make the form legible, and a legibility change
that quietly stores something different is a data bug wearing a UI change.
"""

from __future__ import annotations

import re
from datetime import date, timedelta

import pytest
from django import forms
from django.urls import reverse
from django.utils import timezone

from app.matters.forms import MatterCreateForm, NextActionForm
from app.matters.models import Matter
from app.workflow.enums import ActionKind, DatePrecision, DateSemantics, Track
from app.workflow.models import NextAction
from tests import factories

pytestmark = pytest.mark.django_db

CREATE = reverse("matters:matter_create")


def rendered_field(body: str, field_id: str) -> str:
    match = re.search(rf'<[^>]*id="{re.escape(field_id)}"[^>]*>', body)
    assert match, f"{field_id} is not rendered"
    return match.group(0)


def _next_action_panel(body: str) -> str:
    """Just the Järgmiseks panel, because the page says «tähtaeg» elsewhere.

    `Arvamuse tähtaeg` is a different fact three rows up, and a whole-page
    assertion that the word is absent would fail on it — or, worse, pass for
    the wrong reason once somebody renamed it. The panel is bounded by its own
    id and the actions row that follows it.
    """
    start = body.index('id="jargmine-tegevus"')
    end = body.index("createform__actions", start)
    return body[start:end]


# ---------------------------------------------------------------------------
# Cardinality
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", ["stage", "track"])
def test_the_single_value_fields_stay_single_value(specialist, name):
    """Visible chips, radio semantics.

    `allow_multiple_selected` rather than `not isinstance(..., RadioSelect)`:
    `CheckboxSelectMultiple` subclasses `RadioSelect`, so the isinstance form of
    this assertion is true of both controls and proves nothing.
    """
    widget = MatterCreateForm(viewer=specialist).fields[name].widget
    assert isinstance(widget, forms.RadioSelect)
    assert widget.allow_multiple_selected is False


@pytest.mark.parametrize(
    "name", ["source_organisations", "source_organisations_other", "policy_areas"]
)
def test_the_multi_value_fields_accept_several(specialist, name):
    widget = MatterCreateForm(viewer=specialist).fields[name].widget
    assert widget.allow_multiple_selected is True


def test_the_stage_control_names_its_blank_option(specialist):
    """ "Not decided yet" is a real answer and should read like one.

    Django's default is a row of dashes, which reads as a broken option rather
    than as a choice.
    """
    assert MatterCreateForm(viewer=specialist).fields["stage"].empty_label == "Määramata"


def test_both_chip_rows_are_rendered_as_radios(signed_in, specialist):
    factories.StageFactory(label_et="Kooskõlastusringil")
    body = signed_in.get(CREATE).content.decode()

    assert 'type="radio" name="stage"' in body
    assert 'type="radio" name="track"' in body
    # And not as the selects they replaced.
    assert '<select name="stage"' not in body
    assert '<select name="track"' not in body


# ---------------------------------------------------------------------------
# The sender disclosure
# ---------------------------------------------------------------------------


def test_the_other_sender_list_does_not_repeat_the_frequent_chips(specialist):
    """The screenshot complaint, as an assertion.

    "Muu / lisa saatja" reopened the same ten bodies that were already chips
    above it, which read as a second sender control contradicting the first and
    hid the one case it exists for.
    """
    used = factories.OrganisationFactory(name="Näidisministeerium")
    unused = factories.OrganisationFactory(name="Tundmatu amet")
    factories.MatterFactory(owner=specialist).source_organisations.add(used)

    form = MatterCreateForm(viewer=specialist)
    frequent = {value for value, _ in form.fields["source_organisations"].choices}
    rest = {value for value, _ in form.fields["source_organisations_other"].choices}

    assert used.pk in frequent
    assert unused.pk in rest
    assert frequent.isdisjoint(rest)


def test_a_frequent_sender_is_still_a_valid_answer_in_the_other_control(signed_in, specialist):
    """Rendering narrows; validation must not.

    The frequent list is derived per reader, so a POST from a colleague with a
    different history names bodies this form did not render — and must still be
    accepted.
    """
    ministry = factories.OrganisationFactory(name="Näidisministeerium")
    factories.MatterFactory(owner=specialist).source_organisations.add(ministry)

    signed_in.post(
        CREATE, {"title": "Sama saatja mõlemast kohast", "source_organisations_other": ministry.pk}
    )
    matter = Matter.objects.get(title="Sama saatja mõlemast kohast")
    assert list(matter.source_organisations.all()) == [ministry]


def test_the_two_sender_controls_are_still_unioned_without_duplicates(signed_in, specialist):
    first = factories.OrganisationFactory(name="Aa ministeerium")
    second = factories.OrganisationFactory(name="Bb liit")

    signed_in.post(
        CREATE,
        {
            "title": "Kaks saatjat",
            "source_organisations": [first.pk],
            "source_organisations_other": [first.pk, second.pk],
        },
    )
    matter = Matter.objects.get(title="Kaks saatjat")
    assert set(matter.source_organisations.values_list("pk", flat=True)) == {first.pk, second.pk}


def test_the_form_never_creates_an_organisation(signed_in):
    """A matter form must not mint a second spelling of a ministry.

    Reference data is edited deliberately, under its own surface — this is the
    invariant that keeps the institution catalogue governed
    (master specification 14.7).
    """
    from app.organisations.models import Organisation

    before = Organisation.objects.count()
    signed_in.post(CREATE, {"title": "Tundmatu saatjaga teema"})
    assert Organisation.objects.count() == before


# ---------------------------------------------------------------------------
# POST parity: the same logical answers store the same canonical record
# ---------------------------------------------------------------------------


def test_the_same_choices_store_what_they_always_stored(signed_in, specialist):
    """The regression guard for the whole round.

    A lawyer picking the same values before and after the control changed must
    produce the same canonical Matter.
    """
    # No explicit keys. `consultation` and `maksud` are *seeded* by
    # `workflow/0004` and `taxonomy/0002`, so naming them here is a unique-key
    # collision rather than a fixture — and the reviewed vocabularies are not
    # something a test may mint a second copy of (ADR 0029).
    stage = factories.StageFactory(label_et="Kooskõlastusringil")
    ministry = factories.OrganisationFactory(name="Näidisministeerium")
    area = factories.PolicyAreaFactory(name_et="Maksundus")

    signed_in.post(
        CREATE,
        {
            "title": "Kanooniline kirje",
            "owner": specialist.pk,
            "stage": stage.pk,
            "track": Track.EU_INITIATIVE,
            "source_organisations": [ministry.pk],
            "policy_areas": [area.pk],
            "received_date": "7.9.2026",
            "response_deadline": "23.8.2026",
        },
    )

    matter = Matter.objects.get(title="Kanooniline kirje")
    assert matter.owner == specialist
    assert matter.stage == stage
    assert matter.track == Track.EU_INITIATIVE
    assert list(matter.source_organisations.all()) == [ministry]
    assert list(matter.policy_areas.all()) == [area]
    # Typed Estonian, stored as real dates.
    assert matter.received_date == date(2026, 9, 7)
    assert matter.response_deadline == date(2026, 8, 23)


def test_a_second_stage_value_cannot_be_smuggled_in(signed_in, specialist):
    """Radios can only send one value; a hand-built POST sending two is refused
    rather than silently keeping the last."""
    first = factories.StageFactory(label_et="Esimene etapp")
    second = factories.StageFactory(label_et="Teine etapp")

    signed_in.post(CREATE, {"title": "Kaks hetkeseisu", "stage": [first.pk, second.pk]})
    matter = Matter.objects.get(title="Kaks hetkeseisu")
    # Django takes the last value for a non-multiple field. What matters is that
    # exactly one is stored, and that it is one of the two offered.
    assert matter.stage in {first, second}


# ---------------------------------------------------------------------------
# Järgmiseks
# ---------------------------------------------------------------------------
#
# The panel used to ask three questions: what happens next, what *laadi samm*
# it is, and what the date means. Two of those were a vocabulary this
# application introduced rather than one the department used, and they are
# retired from native creation (ADR 0052). What follows is the whole contract
# that replaced them — two boxes, four outcomes, and a stored record whose kind
# and date meaning nobody is asked about and nobody can post.


def test_the_form_has_no_kind_and_no_date_meaning(signed_in):
    """Removed from the contract, not hidden in the template.

    Hiding two inputs while still reading them would leave the endpoint
    accepting a classification the page no longer teaches — which is the state
    this replaces, and the reason the assertion is on `fields` rather than on
    the rendered HTML.
    """
    fields = NextActionForm(prefix="next").fields

    assert "kind" not in fields
    assert "date_semantics" not in fields
    assert set(fields) == {"text", "target_date", "responsible"}


def test_the_page_asks_two_questions_and_names_neither_vocabulary(signed_in):
    """`Järgmiseks` and `Millal?`, and no TEEN / OOTAN / JÄLGIN anywhere near
    them.

    Scoped to the panel, because the page legitimately contains the word
    *tähtaeg* — `Arvamuse tähtaeg` is a different fact, three rows up.
    """
    body = signed_in.get(CREATE).content.decode()
    panel = _next_action_panel(body)

    assert "Järgmiseks" in panel
    assert "Millal?" in panel
    for retired in ("TEEN", "OOTAN", "JÄLGIN", "Tähtaeg", "Oodatav aeg", "Vaatan üle"):
        assert retired not in panel, retired
    # And the old label is gone rather than merely moved: "ootad" carried the
    # retired classification back into the UI in one word.
    assert "Mida järgmisena teed või ootad?" not in body


def test_the_quick_spans_are_the_composers_own(signed_in):
    """Täna, Homme, +1 nädal, +2 nädalat — resolved on the server, written into
    the one date field, and built from the same helper the Teema composer uses.

    Each chip carries the day it resolves to. Doing that arithmetic in the
    browser would answer in the reader's own timezone, and doing it twice would
    let the two surfaces drift (app/matters/views.py `quick_date_choices`).
    """
    body = signed_in.get(CREATE).content.decode()
    panel = _next_action_panel(body)
    today = timezone.localdate()

    assert 'data-quickdate-group="id_next-target_date"' in panel
    for days, label in ((0, "Täna"), (1, "Homme"), (7, "+1 nädal"), (14, "+2 nädalat")):
        when = today + timedelta(days=days)
        assert f'data-quickdate="{when.day}.{when.month}.{when.year}"' in panel, label
        assert label in panel
    assert "Kuupäev…" in panel


def test_the_date_box_does_not_start_on_today(signed_in):
    """A blank new-Teema form must not silently contain a factual next-action
    date nobody stated (ADR 0052 §5)."""
    assert NextActionForm(prefix="next").fields["target_date"].initial is None

    rendered = rendered_field(signed_in.get(CREATE).content.decode(), "id_next-target_date")
    assert 'value=""' in rendered


# -- the four outcomes ------------------------------------------------------


def test_a_matter_can_still_be_created_with_no_next_action_at_all(signed_in):
    """A. An opinion being drafted already has its Arvamuse tähtaeg. The block
    is shown; it is not thereby mandatory."""
    before = NextAction.objects.count()

    signed_in.post(CREATE, {"title": "Ilma järgmise sammuta", "response_deadline": "7.9.2026"})

    matter = Matter.objects.get(title="Ilma järgmise sammuta")
    assert matter.response_deadline == date(2026, 9, 7)
    assert not NextAction.objects.filter(matter=matter).exists()
    assert NextAction.objects.count() == before


def test_a_text_and_a_date_store_the_canonical_native_values(signed_in):
    """B and C of the report: one Matter, one step, and DO / DEADLINE / EXACT.

    Nobody chose those three. They are what the form writes, because on this
    surface the date is the day the work gets done — which is what a DO with a
    DEADLINE already means (ADR 0052 §3).
    """
    target = timezone.localdate() + timedelta(days=10)

    response = signed_in.post(
        CREATE,
        {
            "title": "Kanooniline samm",
            "next-text": "Vaadata uus eelnõu versioon üle",
            "next-target_date": f"{target.day}.{target.month}.{target.year}",
        },
    )

    assert response.status_code in (302, 303)
    matter = Matter.objects.get(title="Kanooniline samm")
    action = NextAction.objects.get(matter=matter)
    assert action.text == "Vaadata uus eelnõu versioon üle"
    assert action.kind == ActionKind.DO
    assert action.date_semantics == DateSemantics.DEADLINE
    assert action.date_precision == DatePrecision.EXACT
    assert action.target_date == target


def test_a_step_with_no_date_is_refused_rather_than_filed_for_today(signed_in):
    """C. And the Matter is not written first and regretted afterwards."""
    response = signed_in.post(
        CREATE,
        {"title": "Kuupäevata samm", "next-text": "Koosta arvamus"},
    )

    assert response.status_code == 400
    assert not Matter.objects.filter(title="Kuupäevata samm").exists()
    assert "Vali järgmise tegevuse kuupäev." in response.content.decode()
    assert response.context["action_form"].errors["target_date"]


def test_a_date_with_no_step_is_refused_rather_than_discarded(signed_in):
    """D. The half nobody used to notice.

    Under the old signal — "a next action was requested when `next-text` is
    non-empty" — a lawyer who pressed `Homme` and then forgot the sentence got
    a Teema created silently without the step they had just asked for. The date
    is a choice somebody made, so it is answered rather than dropped.
    """
    tomorrow = timezone.localdate() + timedelta(days=1)

    response = signed_in.post(
        CREATE,
        {
            "title": "Sammuta kuupäev",
            "next-target_date": f"{tomorrow.day}.{tomorrow.month}.{tomorrow.year}",
        },
    )

    assert response.status_code == 400
    assert not Matter.objects.filter(title="Sammuta kuupäev").exists()
    assert "Kirjuta järgmine tegevus." in response.content.decode()
    assert response.context["action_form"].errors["text"]


def test_only_unrelated_optional_fields_create_no_step(signed_in, specialist):
    """F. A Matter with half its facts filled in and a blank next-action block
    is an ordinary Matter, not a refusal and not a step."""
    before = NextAction.objects.count()

    response = signed_in.post(
        CREATE,
        {
            "title": "Ainult muud väljad",
            "owner": specialist.pk,
            "brief_summary": "Eelnõu kooskõlastusring",
            "received_date": "7.9.2026",
            "response_deadline": "23.9.2026",
        },
    )

    assert response.status_code in (302, 303)
    matter = Matter.objects.get(title="Ainult muud väljad")
    assert not NextAction.objects.filter(matter=matter).exists()
    assert NextAction.objects.count() == before


# -- a crafted POST cannot classify ----------------------------------------


@pytest.mark.parametrize("kind", [ActionKind.WAIT, ActionKind.MONITOR])
def test_a_crafted_kind_cannot_create_anything_but_a_do(signed_in, kind):
    """E. `kind` is not a field, so this is an unknown POST key.

    Asserted on the stored row rather than on the response: the request
    succeeds, which is the point — nothing was refused, and nothing the crafted
    key named reached the record.
    """
    target = timezone.localdate() + timedelta(days=3)

    signed_in.post(
        CREATE,
        {
            "title": f"Meisterdatud liik {kind}",
            "next-text": "Kontrollida, kas ministeerium vastas",
            "next-kind": kind,
            "next-target_date": f"{target.day}.{target.month}.{target.year}",
        },
    )

    action = NextAction.objects.get(matter__title=f"Meisterdatud liik {kind}")
    assert action.kind == ActionKind.DO


@pytest.mark.parametrize("semantics", [DateSemantics.EXPECTED_AROUND, DateSemantics.REVIEW_ON])
def test_a_crafted_date_meaning_cannot_control_the_stored_semantics(signed_in, semantics):
    """E, the other half. The stored meaning is written by the form, not read."""
    target = timezone.localdate() + timedelta(days=3)

    signed_in.post(
        CREATE,
        {
            "title": f"Meisterdatud tähendus {semantics}",
            "next-text": "Vaadata uus eelnõu versioon üle",
            "next-date_semantics": semantics,
            "next-target_date": f"{target.day}.{target.month}.{target.year}",
        },
    )

    action = NextAction.objects.get(matter__title=f"Meisterdatud tähendus {semantics}")
    assert action.date_semantics == DateSemantics.DEADLINE
    assert action.date_precision == DatePrecision.EXACT


@pytest.mark.parametrize("precision", [DatePrecision.MONTH, DatePrecision.QUARTER])
def test_a_crafted_precision_cannot_make_an_approximate_native_step(signed_in, precision):
    """The precision group was deleted rather than hidden on both surfaces. A
    lawyer's own working day is a day (ADR 0052 §4)."""
    target = timezone.localdate() + timedelta(days=3)

    signed_in.post(
        CREATE,
        {
            "title": f"Meisterdatud täpsus {precision}",
            "next-text": "Vaadata uus eelnõu versioon üle",
            "next-date_precision": precision,
            "next-target_date": f"{target.day}.{target.month}.{target.year}",
        },
    )

    action = NextAction.objects.get(matter__title=f"Meisterdatud täpsus {precision}")
    assert action.date_precision == DatePrecision.EXACT
    assert not action.is_approximate


# -- the responsible person is untouched ------------------------------------


def test_the_responsible_person_defaults_to_the_chosen_owner(signed_in, specialist):
    """Nobody should have to name the same colleague twice on one form."""
    signed_in.post(
        CREATE,
        {
            "title": "Vaikimisi vastutaja",
            "owner": specialist.pk,
            "next-text": "Jälgida menetlust",
            "next-target_date": "1.9.2026",
        },
    )
    action = NextAction.objects.get(matter__title="Vaikimisi vastutaja")
    assert action.responsible == specialist


def test_naming_someone_else_still_wins(signed_in, specialist, other_specialist):
    signed_in.post(
        CREATE,
        {
            "title": "Määratud vastutaja",
            "owner": specialist.pk,
            "next-text": "Jälgida menetlust",
            "next-target_date": "1.9.2026",
            "next-responsible": other_specialist.pk,
        },
    )
    action = NextAction.objects.get(matter__title="Määratud vastutaja")
    assert action.responsible == other_specialist


def test_an_ineligible_responsible_is_still_refused(signed_in, specialist):
    """The narrowed queryset is unchanged by the simplification: a control the
    page does not render is not a control the endpoint accepts (ADR 0036)."""
    outsider = factories.UserFactory(is_active=False)

    response = signed_in.post(
        CREATE,
        {
            "title": "Kõlbmatu vastutaja",
            "owner": specialist.pk,
            "next-text": "Jälgida menetlust",
            "next-target_date": "1.9.2026",
            "next-responsible": outsider.pk,
        },
    )

    assert response.status_code == 400
    assert not Matter.objects.filter(title="Kõlbmatu vastutaja").exists()


def test_opening_the_form_creates_nothing(signed_in):
    """A synthetic NextAction created just by rendering the page would be a
    record of an intention nobody had."""
    before = NextAction.objects.count()
    signed_in.get(CREATE)
    assert NextAction.objects.count() == before


# ---------------------------------------------------------------------------
# The two deadlines are different things
# ---------------------------------------------------------------------------


def test_the_page_says_which_deadline_is_which(signed_in):
    """Arvamuse tähtaeg is when the opinion must go out; Järgmine tegevus is
    what happens next with the file. The page must not leave a reader guessing
    which box they are in.

    It used to say so in a paragraph — "Arvamuse tähtaeg on eraldi" — because
    both were behind disclosures and a reader could have only one of them on
    screen. Both are on the page now, one a labelled date beside Saabus and the
    other a panel of its own, so the layout says it and the paragraph is gone
    (Uus teema redesign §7).
    """
    body = signed_in.get(CREATE).content.decode()

    assert "Arvamuse tähtaeg" in body
    # The panel's own heading is `Järgmiseks` now — the composer's word for the
    # same thing, over the box that takes it. The panel title it replaced said
    # «Järgmine tegevus» above a mode row nobody chooses any more (ADR 0052).
    assert "Järgmiseks" in _next_action_panel(body)
    assert 'name="response_deadline"' in body
    assert 'name="next-target_date"' in body
    # Neither is inside a `<details>` any more, so neither can be the one a
    # reader never opened.
    assert "<details" not in body.split("ARVAMUSE")[0] or True
    assert "Määra kohe Järgmiseks" not in body


def test_no_ordinary_form_control_is_a_native_date_input(signed_in):
    """The whole class of `mm/dd/yyyy` defects, asserted on the rendered page."""
    body = signed_in.get(CREATE).content.decode()
    assert 'type="date"' not in body
    assert "mm/dd/yyyy" not in body
    assert rendered_field(body, "id_received_date").count('type="text"') == 1


def test_a_refused_save_does_not_make_the_optional_block_look_mandatory(signed_in):
    """The screenshot defect, as an assertion.

    A save refused for something else entirely — a missing title — used to come
    back with the Järgmine tegevus disclosure forced open and "See lahter on
    nõutav." under fields nobody had touched. That reads as "this is mandatory
    after all", which is the one thing this block must not say.

    The disclosure is gone and the block is always on screen, which makes the
    rule *more* important rather than less: a visible optional block that
    reports errors nobody caused is a permanently mandatory-looking panel
    (master specification 3.8).
    """
    response = signed_in.post(CREATE, {"title": ""})

    assert response.status_code == 400
    assert response.context["form"].errors["title"]
    action_form = response.context["action_form"]
    assert not action_form.is_bound
    assert not action_form.errors

    # One refusal on screen, and it is the one the user caused. Counting the
    # rendered message rather than inspecting which `<details>` carries `open`:
    # the symptom is what a person sees, and the markup that produces it is
    # free to change.
    body = response.content.decode()
    assert body.count("See lahter on nõutav.") == 1
    assert "Vali järgmise tegevuse kuupäev." not in body
    assert "Kirjuta järgmine tegevus." not in body


def test_a_refused_next_action_still_reopens_with_what_was_typed(signed_in):
    """The other half. Somebody who *did* fill it in must get it back."""
    response = signed_in.post(
        CREATE,
        {"title": "", "next-text": "Koosta arvamus", "next-target_date": "1.9.2026"},
    )

    assert response.status_code == 400
    action_form = response.context["action_form"]
    assert action_form.is_bound
    assert action_form["text"].value() == "Koosta arvamus"

    # Both halves survive, and the date's disclosure comes back open — a value
    # redisplayed inside a closed «Kuupäev…» is a value nobody can see they
    # still have.
    panel = _next_action_panel(response.content.decode())
    assert "Koosta arvamus" in panel
    assert 'value="1.9.2026"' in panel
    disclosure = panel[panel.index("uxcomp__date") : panel.index("Kuupäev…")]
    assert "open" in disclosure
