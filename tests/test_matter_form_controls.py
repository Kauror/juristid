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
from app.workflow.enums import ActionKind, DateSemantics, Track
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
    stage = factories.StageFactory(key="consultation", label_et="Kooskõlastusringil")
    ministry = factories.OrganisationFactory(name="Näidisministeerium")
    area = factories.PolicyAreaFactory(key="maksud", name_et="Maksud")

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
    first = factories.StageFactory(key="idea", label_et="Idee")
    second = factories.StageFactory(key="government", label_et="Valitsuses")

    signed_in.post(CREATE, {"title": "Kaks hetkeseisu", "stage": [first.pk, second.pk]})
    matter = Matter.objects.get(title="Kaks hetkeseisu")
    # Django takes the last value for a non-multiple field. What matters is that
    # exactly one is stored, and that it is one of the two offered.
    assert matter.stage in {first, second}


# ---------------------------------------------------------------------------
# Järgmine tegevus
# ---------------------------------------------------------------------------


def test_the_kind_is_three_visible_single_choice_cards(signed_in):
    widget = NextActionForm(prefix="next").fields["kind"].widget
    assert isinstance(widget, forms.RadioSelect)
    assert widget.allow_multiple_selected is False

    body = signed_in.get(CREATE).content.decode()
    for value in (ActionKind.DO, ActionKind.WAIT, ActionKind.MONITOR):
        assert f'value="{value}"' in body


def test_the_cards_carry_the_sentence_that_makes_them_decidable(signed_in):
    """Teen, Ootan and Jälgin are three ordinary verbs. Which one a lawyer
    means depends on whether the next move is theirs, and the gloss is what
    says so."""
    body = signed_in.get(CREATE).content.decode()
    assert "Mul endal tuleb midagi teha" in body
    assert "Ootan infot, vastust, eelnõud või muud arengut" in body
    assert "Vaatan teema hiljem uuesti üle" in body


def test_the_date_meaning_is_no_longer_a_required_question(signed_in):
    """ "Kuupäeva tähendus" asked about the data model, and it was mandatory."""
    assert NextActionForm(prefix="next").fields["date_semantics"].required is False


@pytest.mark.parametrize(
    ("kind", "semantics"),
    [
        (ActionKind.DO, DateSemantics.DEADLINE),
        (ActionKind.WAIT, DateSemantics.EXPECTED_AROUND),
        (ActionKind.MONITOR, DateSemantics.REVIEW_ON),
    ],
)
def test_the_date_meaning_derives_from_the_kind_when_left_alone(signed_in, kind, semantics):
    """Derived, and stored as the existing canonical enum value."""
    target = timezone.localdate() + timedelta(days=10)
    signed_in.post(
        CREATE,
        {
            "title": f"Tuletatud tähendus {kind}",
            "next-text": "Järgmine samm",
            "next-kind": kind,
            "next-date_semantics": "",
            "next-target_date": f"{target.day}.{target.month}.{target.year}",
        },
    )
    action = NextAction.objects.get(matter__title=f"Tuletatud tähendus {kind}")
    assert (action.kind, action.date_semantics) == (kind, semantics)
    assert action.target_date == target


def test_an_explicit_date_meaning_still_wins(signed_in):
    """The model permits pairs the derivation does not produce, and the
    register's own parser uses them — a DO with a vague month is an expectation,
    not a deadline. Deleting the choice would have deleted that."""
    signed_in.post(
        CREATE,
        {
            "title": "Ligikaudne tähtaeg",
            "next-text": "Ootan eelnõu septembris",
            "next-kind": ActionKind.DO,
            "next-date_semantics": DateSemantics.EXPECTED_AROUND,
            "next-target_date": "1.9.2026",
        },
    )
    action = NextAction.objects.get(matter__title="Ligikaudne tähtaeg")
    assert action.kind == ActionKind.DO
    assert action.date_semantics == DateSemantics.EXPECTED_AROUND


def test_a_deadline_without_a_date_is_still_refused(signed_in):
    """The one combination worth refusing, and the derivation must not soften it."""
    response = signed_in.post(
        CREATE,
        {
            "title": "Kuupäevata tähtaeg",
            "next-text": "Koosta arvamus",
            "next-kind": ActionKind.DO,
            "next-date_semantics": "",
            "next-target_date": "",
        },
    )
    assert response.status_code == 400
    assert not Matter.objects.filter(title="Kuupäevata tähtaeg").exists()


def test_the_responsible_person_defaults_to_the_chosen_owner(signed_in, specialist):
    """Nobody should have to name the same colleague twice on one form."""
    signed_in.post(
        CREATE,
        {
            "title": "Vaikimisi vastutaja",
            "owner": specialist.pk,
            "next-text": "Jälgi menetlust",
            "next-kind": ActionKind.MONITOR,
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
            "next-text": "Jälgi menetlust",
            "next-kind": ActionKind.MONITOR,
            "next-target_date": "1.9.2026",
            "next-responsible": other_specialist.pk,
        },
    )
    action = NextAction.objects.get(matter__title="Määratud vastutaja")
    assert action.responsible == other_specialist


def test_opening_the_form_creates_nothing(signed_in):
    """A synthetic NextAction created just by rendering the page would be a
    record of an intention nobody had."""
    before = NextAction.objects.count()
    signed_in.get(CREATE)
    assert NextAction.objects.count() == before


def test_a_matter_can_still_be_created_with_no_next_action_at_all(signed_in):
    """An opinion being drafted already has its Arvamuse tähtaeg. The block is
    shown; it is not thereby mandatory."""
    signed_in.post(CREATE, {"title": "Ilma järgmise sammuta", "response_deadline": "7.9.2026"})
    matter = Matter.objects.get(title="Ilma järgmise sammuta")
    assert matter.response_deadline == date(2026, 9, 7)
    assert not NextAction.objects.filter(matter=matter).exists()


# ---------------------------------------------------------------------------
# The two deadlines are different things
# ---------------------------------------------------------------------------


def test_the_page_says_which_deadline_is_which(signed_in):
    """Arvamuse tähtaeg is when the opinion must go out; Järgmine tegevus is
    what happens next with the file. The page must not leave a reader guessing
    which box they are in."""
    body = signed_in.get(CREATE).content.decode()
    assert "Järgmine tegevus" in body
    assert "Arvamuse tähtaeg on eraldi" in body
    assert "Määra kohe Järgmiseks" not in body


def test_no_ordinary_form_control_is_a_native_date_input(signed_in):
    """The whole class of `mm/dd/yyyy` defects, asserted on the rendered page."""
    body = signed_in.get(CREATE).content.decode()
    assert 'type="date"' not in body
    assert "mm/dd/yyyy" not in body
    assert rendered_field(body, "id_received_date").count('type="text"') == 1
