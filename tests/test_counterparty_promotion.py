"""Saatja and Adressaat: one catalogue, two questions, one shape.

Three things this file pins, and they are the three the department asked for.

**Saatja looks like Adressaat.** Chips first, «Vali nimekirjast (N)» holding the
search and the rest of the catalogue, `Uus saatja` outside where it is always
reachable. A person should not learn one interaction for the field on the left
and a different one for the field on the right when the two sit on the same row
of the same form and answer the two halves of one question.

**Whoever wrote to you is the first person you might answer.** A body chosen as
the sender is promoted to the front of the addressee choices — promoted, never
selected. Guessing a counterparty would put a fact on the register that nobody
stated, on a form where the person is right there to state it.

**One name is one institution.** Typing the same new body into `Uus saatja` and
`Uus adressaat` on one form creates exactly one `Organisation` row, used twice.
Two rows would be the catalogue quietly acquiring a duplicate on the most
ordinary journey there is (docs/adr/0063, docs/adr/0067).
"""

from __future__ import annotations

import pytest
from django.urls import reverse

from app.matters.forms import MatterCreateForm
from app.matters.models import Matter
from app.organisations.models import Organisation, OrganisationType
from tests import factories

pytestmark = pytest.mark.django_db

CREATE = reverse("matters:matter_create")


@pytest.fixture
def komisjon():
    return Organisation.objects.create(
        name="Euroopa Komisjon", organisation_type=OrganisationType.OTHER
    )


# ---------------------------------------------------------------------------
# The disclosure
# ---------------------------------------------------------------------------


def test_the_sender_control_offers_chips_then_a_disclosure(signed_in, komisjon):
    """The shape, read off the rendered page rather than off the form object.

    A count on the form and a control on the page are two different claims, and
    it is the second one the person meets.
    """
    for index in range(12):
        Organisation.objects.create(name=f"Asutus {index:02d}")

    page = signed_in.get(CREATE).content.decode()

    assert "Vali nimekirjast" in page
    # The typed-sender box is outside the disclosure, so it survives with the
    # door shut. Asserted through the label, which is what somebody looks for.
    assert "Uus saatja" in page


def test_the_sender_search_is_inside_the_disclosure_and_the_new_box_is_not(signed_in, komisjon):
    """The one structural claim worth making with a parser rather than a substring.

    «Uus saatja» being *outside* is the half of the previous round that was
    load-bearing: it is the answer to "the body I need is not on this page", and
    hiding that behind a click restores the workflow nobody used. The search
    being *inside* is what gives the row back to the eight chips that answer the
    question on almost every visit (task §9).
    """
    from html.parser import HTMLParser

    for index in range(12):
        Organisation.objects.create(name=f"Asutus {index:02d}")
    page = signed_in.get(CREATE).content.decode()

    class Reader(HTMLParser):
        def __init__(self) -> None:
            super().__init__()
            self.depth = 0
            self.in_sender = False
            self.search_depths: list[int] = []
            self.new_sender_depths: list[int] = []

        def handle_starttag(self, tag, attrs):
            values = dict(attrs)
            classes = (values.get("class") or "").split()
            if tag == "fieldset" and "senderpick" in classes:
                self.in_sender = True
                self.depth = 0
            if not self.in_sender:
                return
            if tag == "details":
                self.depth += 1
            if tag == "input" and values.get("type") == "search":
                self.search_depths.append(self.depth)
            if tag == "input" and values.get("name") == "sender_name":
                self.new_sender_depths.append(self.depth)

        def handle_endtag(self, tag):
            if self.in_sender and tag == "details":
                self.depth -= 1
            if self.in_sender and tag == "fieldset" and self.depth == 0:
                self.in_sender = False

    reader = Reader()
    reader.feed(page)

    assert reader.search_depths == [1], "the Saatja search box is not inside the disclosure"
    assert reader.new_sender_depths == [0], "Uus saatja is not outside the disclosure"


def test_the_disclosure_counts_the_bodies_behind_it(specialist):
    """(N) is the tail, not the catalogue: the chips are already on the page."""
    for index in range(15):
        Organisation.objects.create(name=f"Asutus {index:02d}")

    form = MatterCreateForm(viewer=specialist)

    assert len(form.frequent_senders) == 8
    assert form.sender_tail_count == 15 - 8


def test_a_catalogue_smaller_than_the_shortlist_renders_no_disclosure(specialist):
    """No door where there is nothing behind it."""
    Organisation.objects.create(name="Ainus Asutus")

    form = MatterCreateForm(viewer=specialist)

    assert form.sender_tail_count == 0


# ---------------------------------------------------------------------------
# One catalogue, both directions, on the rendered page
# ---------------------------------------------------------------------------


def test_an_existing_sender_is_in_the_rendered_addressee_catalogue(signed_in, komisjon):
    """CASE C from the report, asserted where the person would look for it.

    `tests/test_sender_free_entry.py` pins this on the form's choices. This pins
    it on the HTML, because "it is in `form.fields[...].choices`" and "it is on
    the page" are different claims and the second is the one that was doubted:
    a template that sliced the radio group wrongly would satisfy the first and
    fail the second.
    """
    factories.MatterFactory().source_organisations.add(komisjon)

    page = signed_in.get(CREATE).content.decode()
    radios = page.count(f'name="addressee_organisation" value="{komisjon.pk}"')

    assert radios == 1, "the sender organisation is not offered exactly once as an addressee"


def test_every_organisation_is_offered_in_both_directions(signed_in, specialist, komisjon):
    """One table behind both controls, stated as a set comparison.

    A sender-only or addressee-only catalogue would be a slow disaster — the two
    would drift for months before anybody could name what was wrong — so this
    compares the whole of each rather than sampling.
    """
    for index in range(5):
        Organisation.objects.create(name=f"Asutus {index}")

    form = MatterCreateForm(viewer=specialist)
    senders = {
        value
        for field in ("source_organisations", "source_organisations_other")
        for value, _label in form.fields[field].choices
    }
    addressees = {
        value for value, _label in form.fields["addressee_organisation"].choices if value != ""
    }
    everything = set(Organisation.objects.values_list("pk", flat=True))

    assert senders == everything
    assert addressees == everything


# ---------------------------------------------------------------------------
# The selected sender comes first
# ---------------------------------------------------------------------------


def _addressee_order(form: MatterCreateForm) -> list:
    return [value for value, _label in form.fields["addressee_organisation"].choices if value != ""]


def test_a_selected_sender_is_the_first_addressee_offered(specialist, komisjon):
    """The reported case, exactly: pick Euroopa Komisjon as Saatja.

    The bound form is what a refused save re-renders, and it is also what the
    server can promise without any script at all — the browser does the same
    thing live, but this is the half that survives scripting being off.
    """
    for index in range(6):
        # Bodies with real addressee history, so the shortlist is not empty and
        # the promotion has something to actually beat.
        other = Organisation.objects.create(name=f"Asutus {index}")
        factories.MatterFactory(owner=specialist, addressee_organisation=other)

    form = MatterCreateForm(
        {"title": "Vastus komisjonile", "source_organisations": [str(komisjon.pk)]},
        viewer=specialist,
    )

    assert _addressee_order(form)[0] == komisjon.pk


def test_promoting_a_sender_never_selects_it(specialist, komisjon):
    """Ordering is a suggestion. A counterparty is a fact, and stays unanswered."""
    form = MatterCreateForm(
        {"title": "Vastus komisjonile", "source_organisations": [str(komisjon.pk)]},
        viewer=specialist,
    )
    assert form.is_valid(), form.errors
    assert form.cleaned_data["addressee_organisation"] is None


def test_a_manually_chosen_addressee_survives_the_promotion(specialist, komisjon):
    """Display order may change under somebody. Their answer may not.

    The failure this prevents is the worst kind: silent, plausible, and only
    visible on the saved record — a Teema answered to the ministry that happened
    to be re-sorted into the slot the person had clicked.
    """
    chosen = Organisation.objects.create(name="Riigikogu majanduskomisjon")

    form = MatterCreateForm(
        {
            "title": "Vastus",
            "source_organisations": [str(komisjon.pk)],
            "addressee_organisation": str(chosen.pk),
        },
        viewer=specialist,
    )

    assert form.is_valid(), form.errors
    assert form.cleaned_data["addressee_organisation"] == chosen
    # Promoted for display, and still not the answer.
    assert _addressee_order(form)[0] == komisjon.pk


def test_several_selected_senders_all_come_first_in_a_stable_order(specialist):
    """Deterministic among themselves, so two renders of one form agree."""
    zulu = Organisation.objects.create(name="Zulu Amet")
    alfa = Organisation.objects.create(name="Alfa Amet")
    for index in range(6):
        other = Organisation.objects.create(name=f"Muu {index}")
        factories.MatterFactory(owner=specialist, addressee_organisation=other)

    form = MatterCreateForm(
        {
            "title": "Vastus",
            "source_organisations": [str(zulu.pk), str(alfa.pk)],
        },
        viewer=specialist,
    )

    assert _addressee_order(form)[:2] == [alfa.pk, zulu.pk]


def test_a_sender_chosen_from_the_long_tail_is_promoted_too(specialist, komisjon):
    """Both sender fields are one set, so both feed the promotion.

    `source_organisations_other` is the disclosure's half of the same answer.
    A promotion that read only the shortlist field would work on the eight
    frequent bodies and silently not work on the other forty — which is the
    half somebody opened the disclosure to reach.
    """
    for index in range(10):
        other = Organisation.objects.create(name=f"Muu {index}")
        factories.MatterFactory(owner=specialist, addressee_organisation=other)

    form = MatterCreateForm(
        {"title": "Vastus", "source_organisations_other": [str(komisjon.pk)]},
        viewer=specialist,
    )

    assert _addressee_order(form)[0] == komisjon.pk


def test_an_unbound_form_promotes_nothing(specialist, komisjon):
    """A fresh GET has no senders, so it has no reordering to do."""
    for index in range(6):
        other = Organisation.objects.create(name=f"Muu {index}")
        factories.MatterFactory(owner=specialist, addressee_organisation=other)

    unbound = _addressee_order(MatterCreateForm(viewer=specialist))
    assert unbound[0] != komisjon.pk


def test_a_malformed_sender_identifier_reorders_nothing_and_raises_nothing(specialist, komisjon):
    """The promotion runs in `__init__`, before validation, so it meets raw input."""
    form = MatterCreateForm(
        {"title": "Vastus", "source_organisations": ["not-a-uuid", ""]},
        viewer=specialist,
    )
    assert _addressee_order(form)  # rendered, not crashed


# ---------------------------------------------------------------------------
# One typed name, one row
# ---------------------------------------------------------------------------


def test_one_new_name_typed_into_both_fields_creates_one_organisation(signed_in):
    """The whole of §14, as the only assertion that matters: a count of one.

    A person types «Euroopa Näidiskomisjon» as the sender, chooses that same
    name as the addressee because they are answering the letter, and presses
    Loo teema. Two independent resolvers running in one transaction could each
    decide the name is new — so the register would gain two identical
    institutions on the most ordinary journey the form has.
    """
    signed_in.post(
        CREATE,
        {
            "title": "Vastus komisjonile",
            "sender_name": "Euroopa Näidiskomisjon",
            "addressee_name": "Euroopa Näidiskomisjon",
        },
    )

    rows = Organisation.objects.filter(name="Euroopa Näidiskomisjon")
    assert rows.count() == 1

    matter = Matter.objects.get(title="Vastus komisjonile")
    organisation = rows.get()
    assert list(matter.source_organisations.all()) == [organisation]
    assert matter.addressee_organisation == organisation


def test_the_same_name_in_different_spacing_is_still_one_row(signed_in):
    """Normalisation is shared, so the two fields cannot disagree about identity."""
    signed_in.post(
        CREATE,
        {
            "title": "Vastus",
            "sender_name": "Euroopa   Näidiskomisjon",
            "addressee_name": " Euroopa Näidiskomisjon ",
        },
    )

    assert Organisation.objects.filter(name="Euroopa Näidiskomisjon").count() == 1


def test_a_refused_save_creates_no_organisation_from_either_field(signed_in):
    """One transaction, so a late refusal takes both resolutions with it."""
    before = Organisation.objects.count()

    response = signed_in.post(
        CREATE,
        {
            # No title: the one required field, so the save is refused after the
            # form has already been asked what the names mean.
            "title": "",
            "sender_name": "Euroopa Näidiskomisjon",
            "addressee_name": "Euroopa Näidiskomisjon",
        },
    )

    assert response.status_code in (200, 400)
    assert Organisation.objects.count() == before
    assert not Organisation.objects.filter(name="Euroopa Näidiskomisjon").exists()


def test_a_typed_name_matching_an_existing_body_reuses_it_in_both_fields(signed_in, komisjon):
    """Reuse, not create — and the same row on both relations."""
    signed_in.post(
        CREATE,
        {
            "title": "Vastus",
            "sender_name": "Euroopa Komisjon",
            "addressee_name": "Euroopa Komisjon",
        },
    )

    assert Organisation.objects.filter(name="Euroopa Komisjon").count() == 1
    matter = Matter.objects.get(title="Vastus")
    assert list(matter.source_organisations.all()) == [komisjon]
    assert matter.addressee_organisation == komisjon
