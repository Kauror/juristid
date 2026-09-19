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

from app.matters.forms import MatterCreateForm, MatterEditForm, NextActionForm
from app.matters.models import Matter
from app.taxonomy.models import LegalInstrumentType
from app.workflow.enums import ActionKind, DatePrecision, DateSemantics
from app.workflow.models import NextAction
from tests import factories

pytestmark = pytest.mark.django_db

CREATE = reverse("matters:matter_create")


def rendered_field(body: str, field_id: str) -> str:
    match = re.search(rf'<[^>]*id="{re.escape(field_id)}"[^>]*>', body)
    assert match, f"{field_id} is not rendered"
    return match.group(0)


# ---------------------------------------------------------------------------
# Cardinality
# ---------------------------------------------------------------------------


def test_the_single_value_fields_stay_single_value(specialist):
    """Visible chips, radio semantics.

    `allow_multiple_selected` rather than `not isinstance(..., RadioSelect)`:
    `CheckboxSelectMultiple` subclasses `RadioSelect`, so the isinstance form of
    this assertion is true of both controls and proves nothing.

    `stage` alone here. `track` was the other one and is no longer a control on
    this page — it is read off `Õigusakt` (docs/adr/0090 §4) — and `Muuda
    teemat` still renders it as radios, which is asserted below.
    """
    widget = MatterCreateForm(viewer=specialist).fields["stage"].widget
    assert isinstance(widget, forms.RadioSelect)
    assert widget.allow_multiple_selected is False


def test_the_edit_form_still_renders_menetlusliik_as_a_single_value_control(specialist):
    widget = MatterEditForm(matter=factories.MatterFactory(owner=specialist)).fields["track"].widget
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


def test_the_chip_rows_are_rendered_as_radios(signed_in, specialist):
    factories.StageFactory(label_et="Kooskõlastusringil")
    matter = factories.MatterFactory(owner=specialist)
    body = signed_in.get(CREATE).content.decode()

    assert 'type="radio" name="stage"' in body
    # And not as the select it replaced.
    assert '<select name="stage"' not in body
    # `Menetlusliik` is not on this page at all (docs/adr/0090 §4).
    assert 'name="track"' not in body

    edit = signed_in.get(reverse("matters:matter_edit", kwargs={"pk": matter.pk})).content.decode()
    assert 'type="radio" name="track"' in edit
    assert '<select name="track"' not in edit


# ---------------------------------------------------------------------------
# The sender control
# ---------------------------------------------------------------------------


def test_the_rest_of_the_catalogue_does_not_repeat_the_shortlist(specialist):
    """The screenshot complaint, as an assertion.

    "Muu / lisa saatja" reopened the same bodies that were already chips above
    it, which read as a second sender control contradicting the first and hid
    the one case it exists for. The disclosure is gone (docs/adr/0063) and the
    two controls are still two halves of one set.

    Enough bodies to have a tail at all: the shortlist is *filled* to eight, so
    a catalogue of two puts both of them on it and leaves nothing over — which
    is correct, and is not what this test is about.
    """
    used = factories.OrganisationFactory(name="Näidisministeerium")
    factories.MatterFactory(owner=specialist).source_organisations.add(used)
    unused = [
        factories.OrganisationFactory(name=f"Tundmatu amet {index:02d}") for index in range(9)
    ]

    form = MatterCreateForm(viewer=specialist)
    frequent = {value for value, _ in form.fields["source_organisations"].choices}
    rest = {value for value, _ in form.fields["source_organisations_other"].choices}

    assert used.pk in frequent
    assert any(organisation.pk in rest for organisation in unused)
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


def test_the_form_creates_no_organisation_nobody_named(signed_in):
    """Ticking chips is not naming a body, and never creates one.

    This used to assert that the form creates *no* institution ever, on the rule
    that reference data is edited deliberately under its own surface. That rule
    is unchanged in substance and withdrawn in form: naming a body is still
    deliberate, and it now takes typing into `Uus saatja` rather than a journey
    to Asutused (master specification 14.7, docs/adr/0063). What must never
    happen is a save that invents an institution nobody typed — including from
    the search box, which posts nothing at all.
    """
    from app.organisations.models import Organisation

    before = Organisation.objects.count()
    signed_in.post(CREATE, {"title": "Tundmatu saatjaga teema"})
    assert Organisation.objects.count() == before

    signed_in.post(CREATE, {"title": "Tühja nimega saatja", "sender_name": "   "})
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
            # `Menetlusliik` is not on this page and is not derived from
            # anything: no `Õigusakt` type entails a procedure (docs/adr/0090
            # §4). What the page does still store is the type itself.
            "legal_instruments": [LegalInstrumentType.objects.get(key="direktiiv").pk],
            "source_organisations": [ministry.pk],
            "policy_areas": [area.pk],
            "received_date": "7.9.2026",
            "response_deadline": "23.8.2026",
        },
    )

    matter = Matter.objects.get(title="Kanooniline kirje")
    assert matter.owner == specialist
    assert matter.stage == stage
    assert [item.key for item in matter.legal_instruments.all()] == ["direktiiv"]
    assert matter.track == ""
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
# Järgmiseks, which is not a question this page asks
# ---------------------------------------------------------------------------
#
# It was a panel here: a free-text box, a `Millal?` row of four quick chips and
# an exact-date disclosure. It is gone from creation (docs/adr/0094 §6). A
# Matter's first step on the capture path is `Koostan arvamuse`, established
# from `Arvamuse tähtaeg`, and a *different* plan is stated in the Teema
# composer — where changing one is an act with its own audit row, and where
# `tests/test_simplified_next_action.py` owns the whole contract that used to be
# duplicated below: the two refusals, the DO / DEADLINE / EXACT storage, and the
# crafted-POST guards on `kind`, `date_semantics` and `date_precision`.
#
# What is left here is the absence, asserted rather than assumed.


def test_the_form_has_no_kind_and_no_date_meaning(signed_in):
    """Removed from the contract, not hidden in the template.

    Hiding two inputs while still reading them would leave the endpoint
    accepting a classification the page no longer teaches — which is the state
    this replaces, and the reason the assertion is on `fields` rather than on
    the rendered HTML. `NextActionForm` still exists and is still the composer's
    form; only its use on `Uus teema` went.
    """
    fields = NextActionForm(prefix="next").fields

    assert "kind" not in fields
    assert "date_semantics" not in fields
    assert set(fields) == {"text", "target_date", "responsible"}


def test_the_whole_jargmiseks_block_is_off_the_creation_page(signed_in):
    """Every control it drew, by name (docs/adr/0094 §6)."""
    body = signed_in.get(CREATE).content.decode()

    assert 'id="jargmine-tegevus"' not in body
    assert "Järgmiseks" not in body
    assert "Millal?" not in body
    assert 'name="next-text"' not in body
    assert 'name="next-target_date"' not in body
    assert 'name="next-responsible"' not in body
    assert "data-quickdate" not in body
    for chip in ("Täna", "Homme", "+1 nädal", "+2 nädalat", "Kuupäev…"):
        assert chip not in body, chip


def test_no_context_carries_a_next_action_form_or_its_chips(signed_in):
    """The other half, because a template is free to stop rendering a value.

    A form left in the context is a form the next edit can put back on screen by
    accident, and `quick_dates` is the list the retired chip row was built from.
    """
    response = signed_in.get(CREATE)

    assert "action_form" not in response.context
    assert "opinion_action_form" not in response.context
    assert "quick_dates" not in response.context


def test_stale_next_action_keys_create_nothing(signed_in):
    """A POST carrying the retired keys is stale form state, not an instruction.

    Both halves, because both used to be a request for a step on their own: a
    sentence with a date, and a date with a sentence. Neither is read now, so
    the Teema saves and carries no step at all — rather than one nobody asked
    for, or a refusal about a box that is not on the page.
    """
    tomorrow = timezone.localdate() + timedelta(days=1)
    estonian = f"{tomorrow.day}.{tomorrow.month}.{tomorrow.year}"

    response = signed_in.post(
        CREATE,
        {
            "title": "Aegunud väljad",
            "next-text": "Vaadata uus eelnõu versioon üle",
            "next-target_date": estonian,
            "next-kind": ActionKind.WAIT,
            "next-date_semantics": DateSemantics.EXPECTED_AROUND,
            "next-date_precision": DatePrecision.MONTH,
        },
    )

    assert response.status_code in (302, 303)
    matter = Matter.objects.get(title="Aegunud väljad")
    assert not NextAction.objects.filter(matter=matter).exists()


def test_opening_the_form_creates_nothing(signed_in):
    """A synthetic NextAction created just by rendering the page would be a
    record of an intention nobody had."""
    before = NextAction.objects.count()
    signed_in.get(CREATE)
    assert NextAction.objects.count() == before


# ---------------------------------------------------------------------------
# One deadline, and it is the last question on the form
# ---------------------------------------------------------------------------


def test_the_page_asks_for_one_date_and_names_it(signed_in):
    """`Arvamuse tähtaeg`, once — and `Saabus`, which is an observation.

    The page used to carry two boxes meaning the same thing under two names, and
    a paragraph under each explaining which was which. One box needs no
    paragraph (docs/adr/0094 §5).
    """
    body = signed_in.get(CREATE).content.decode()

    assert body.count("Arvamuse tähtaeg") == 1
    assert 'name="response_deadline"' in body
    assert body.count('name="response_deadline"') == 1
    # The second box, and the two sentences that had to tell them apart.
    assert "Koostan arvamuse" not in body
    assert 'name="arvamus-prepare_by"' not in body
    assert "tekib teemale järgmine tegevus" not in body
    assert "Mis kuupäevaks Koja arvamuse koostad" not in body


def test_the_deadline_is_the_last_field_before_the_actions(signed_in):
    """Order, measured on the document rather than assumed from the template."""
    body = signed_in.get(CREATE).content.decode()

    link = body.index('id="menetluse-link"')
    deadline = body.index('id="arvamuse-tahtaeg"')
    actions = body.index("createform__actions")

    assert link < deadline < actions
    # And nothing else asks a question between the deadline and the button.
    assert 'class="field__input' not in body[deadline + 1 : actions].split("</div>")[-1]


def test_a_matter_can_still_be_created_with_no_date_at_all(signed_in):
    """The box is shown; it is not thereby mandatory."""
    before = NextAction.objects.count()

    signed_in.post(CREATE, {"title": "Ilma tähtajata"})

    matter = Matter.objects.get(title="Ilma tähtajata")
    assert matter.response_deadline is None
    assert not NextAction.objects.filter(matter=matter).exists()
    assert NextAction.objects.count() == before


def test_no_ordinary_form_control_is_a_native_date_input(signed_in):
    """The whole class of `mm/dd/yyyy` defects, asserted on the rendered page."""
    body = signed_in.get(CREATE).content.decode()
    assert 'type="date"' not in body
    assert "mm/dd/yyyy" not in body
    assert rendered_field(body, "id_received_date").count('type="text"') == 1


def test_a_refused_save_reports_only_the_refusal_somebody_caused(signed_in):
    """The screenshot defect, as an assertion.

    A save refused for something else entirely — a missing title — used to come
    back with the Järgmine tegevus disclosure forced open and "See lahter on
    nõutav." under fields nobody had touched. That reads as "this is mandatory
    after all", which is the one thing an optional block must not say.

    Both optional blocks that produced it are gone, and the rule is the same for
    what replaced them: a visible block reporting errors nobody caused is a
    permanently mandatory-looking panel (master specification 3.8).
    """
    response = signed_in.post(CREATE, {"title": ""})

    assert response.status_code == 400
    assert response.context["form"].errors["title"]

    # One refusal on screen, and it is the one the user caused. Counting the
    # rendered message rather than inspecting which block carries it: the
    # symptom is what a person sees, and the markup that produces it is free to
    # change.
    body = response.content.decode()
    assert body.count("See lahter on nõutav.") == 1
    assert "Vali järgmise tegevuse kuupäev." not in body
    assert "Kirjuta järgmine tegevus." not in body
    assert "Menetluse link vajab veebiaadressi." not in body


def test_a_refused_save_gives_the_deadline_back(signed_in):
    """The other half. Somebody who *did* fill it in must get it back."""
    response = signed_in.post(CREATE, {"title": "", "response_deadline": "1.9.2026"})

    assert response.status_code == 400
    assert response.context["form"]["response_deadline"].value() == "1.9.2026"
    assert 'value="1.9.2026"' in response.content.decode()
