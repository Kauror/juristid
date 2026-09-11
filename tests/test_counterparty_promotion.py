"""Saatja and Adressaat: one catalogue, two questions, one shape.

Three things this file pins, and they are the three the department asked for.

**Saatja looks like Adressaat.** One control: a box reading «Otsi või lisa
asutus…» with a `+` attached to it, and the quick choices under it. A person
should not learn one interaction for the field on the left and a different one
for the field on the right when the two sit on the same row of the same form and
answer the two halves of one question — and they should not learn three
interactions for either of them, which is what the shortlist, the «Vali
nimekirjast» disclosure and the separate «Uus saatja» box added up to
(docs/adr/0073).

**Whoever wrote to you is the first person you might answer** — and since
docs/adr/0069, the one the form answers. A body chosen as the sender is moved to
the front of the addressee choices *and* becomes the addressee. This file owns
the first half of that sentence, which is what makes the second half visible: a
default sitting in the long tail behind a closed disclosure would be an answer
nobody could find. What the default *is* belongs to
`tests/test_addressee_defaults_to_sender.py`.

**One name is one institution.** Naming the same new body as sender and as
addressee on one form creates exactly one `Organisation` row, used twice.
Two rows would be the catalogue quietly acquiring a duplicate on the most
ordinary journey there is (docs/adr/0063, docs/adr/0067).
"""

from __future__ import annotations

import re

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
# One control: search first, quick choices under it
# ---------------------------------------------------------------------------


def _without_noscript(page: str) -> str:
    """The page a scripted browser builds a document from.

    `<noscript>` content is not markup in a browser that runs scripts — it is
    text the parser skips — so anything asserted about what a person sees has to
    be asserted about the response with those blocks taken out. Without this the
    fallback's «Vali nimekirjast» would satisfy a test written to prove it gone.
    """
    return re.sub(r"<noscript>.*?</noscript>", "", page, flags=re.DOTALL)


def test_the_sender_control_is_a_search_box_and_then_chips(signed_in, komisjon):
    """The shape, read off the rendered page rather than off the form object.

    A count on the form and a control on the page are two different claims, and
    it is the second one the person meets. What that person now meets is one
    control: «Otsi või lisa asutus…», with the quick choices under it and
    nothing folded away (docs/adr/0073, task §3).
    """
    for index in range(12):
        Organisation.objects.create(name=f"Asutus {index:02d}")

    page = signed_in.get(CREATE).content.decode()

    assert "Otsi või lisa asutus…" in page
    assert "Lisa uus saatja" in page
    assert "Lisa uus adressaat" in page


def test_the_retired_controls_are_gone_from_the_scripted_page(signed_in, komisjon):
    """«Vali nimekirjast» and «Uus saatja» reach an ordinary browser nowhere.

    They are still in the response — inside `<noscript>`, which is the fallback
    for a browser that cannot search the catalogue — and a scripted browser
    never parses that into anything. So the assertion has to be made against the
    document *minus* those blocks, which is what the person actually sees
    (task §12, §22).

    `Muuda teemat` and `Saabunud` keep the old pair of controls and are not
    asserted here: this round changed `Uus teema` and deliberately nothing else
    (task §26).
    """
    for index in range(12):
        Organisation.objects.create(name=f"Asutus {index:02d}")

    scripted = _without_noscript(signed_in.get(CREATE).content.decode())

    assert "Vali nimekirjast" not in scripted
    assert "Uus saatja" not in scripted
    assert "Uus adressaat" not in scripted
    # The field is not merely invisible: it still posts, because `+` writes into
    # it and a refused save has to come back holding what was typed.
    assert 'name="sender_name"' in scripted
    assert 'name="addressee_name"' in scripted


def test_the_search_box_is_the_first_control_in_each_field(signed_in, komisjon):
    """The one structural claim worth making with a parser rather than a substring.

    «Search first» is the whole product decision, and a template that rendered
    the chips above the box would satisfy every substring assertion in this file
    while shipping the shape this round exists to replace. So the two fields are
    walked in document order and the first control in each is named (task §3).
    """
    from html.parser import HTMLParser

    for index in range(12):
        Organisation.objects.create(name=f"Asutus {index:02d}")
    page = _without_noscript(signed_in.get(CREATE).content.decode())

    class Reader(HTMLParser):
        """The controls inside each picker, in the order they are written."""

        def __init__(self) -> None:
            super().__init__()
            self.picker: str | None = None
            self.seen: dict[str, list[str]] = {}

        def handle_starttag(self, tag: str, attrs: list) -> None:
            values = dict(attrs)
            if tag == "div" and "data-orgfind" in values:
                self.picker = values.get("id") or ""
            if tag != "input" or self.picker is None:
                return
            kind = values.get("type") or "text"
            if kind == "hidden":
                # The carrier posts and is not a control anybody meets.
                return
            self.seen.setdefault(self.picker, []).append(
                "search" if kind == "search" else values.get("name") or "?"
            )

    # Two pickers on the page and neither carries an id, so they are told apart
    # by the order the halves of the document put them in.
    for index, half in enumerate(page.split("data-orgfind ")[1:], start=1):
        one = Reader()
        one.picker = str(index)
        one.feed(half)
        assert one.seen[str(index)][0] == "search", (
            f"picker {index} renders something before its search box: {one.seen[str(index)][:3]}"
        )


def test_a_catalogue_smaller_than_the_shortlist_has_no_hidden_tail(specialist):
    """Nothing behind the search when the shortlist already holds everything."""
    Organisation.objects.create(name="Ainus Asutus")

    form = MatterCreateForm(viewer=specialist)

    assert form.sender_tail_count == 0
    assert form.sender_tail_choices == []


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
# The selected sender comes first — and is the answer
# ---------------------------------------------------------------------------


def _addressee_order(form: MatterCreateForm) -> list:
    return [value for value, _label in form.fields["addressee_organisation"].choices if value != ""]


def test_a_selected_sender_is_the_first_addressee_offered(specialist, komisjon):
    """The reported case, exactly: pick Euroopa Komisjon as Saatja.

    The bound form is what a refused save re-renders, and it is also what the
    server can promise without any script at all — the browser does the same
    thing live, but this is the half that survives scripting being off.

    Being *first* matters more than it did. Adressaat is a closed disclosure
    now, so the shortlist is what opening it shows; an answer that had fallen
    into «Vali nimekirjast» would be one the person could only find by opening a
    second door to look for something the page had already decided.
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


def test_the_promoted_sender_is_also_the_answer(specialist, komisjon):
    """The decision this file used to assert the opposite of.

    It read `test_promoting_a_sender_never_selects_it`, on the argument that
    ordering is a suggestion and a counterparty is a fact that should be stated
    rather than guessed. The argument was right about the risk and wrong about
    the trade: answering the body that wrote to you is the ordinary case, and
    making the ordinary case free costs somebody answering a different body one
    correction they can see themselves making (docs/adr/0069, task §2).
    """
    form = MatterCreateForm(
        {"title": "Vastus komisjonile", "source_organisations": [str(komisjon.pk)]},
        viewer=specialist,
    )
    assert form.is_valid(), form.errors
    assert form.cleaned_data["addressee_organisation"] == komisjon


def test_a_manually_chosen_addressee_survives_the_promotion(specialist, komisjon):
    """Display order may change under somebody. Their answer may not.

    The failure this prevents is the worst kind: silent, plausible, and only
    visible on the saved record — a Teema answered to the ministry that happened
    to be re-sorted into the slot the person had clicked. It is also why the
    default above may never be inferred from *position*: this test's whole point
    is that the first chip and the chosen one are different bodies.
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
    # Moved for display, and still not the answer: an answer already given
    # outranks the one the sender would have supplied.
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
