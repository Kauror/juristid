"""Saatja and Adressaat: one catalogue, two questions, one shape.

Two things this file pins, and a third it used to.

**Saatja looks like Adressaat.** One control: a box reading «Otsi või lisa
asutus…» with a `+` attached to it, and the quick choices under it. A person
should not learn one interaction for the field on the left and a different one
for the field on the right, and they should not learn three interactions for
either of them, which is what the shortlist, the «Vali nimekirjast» disclosure
and the separate «Uus saatja» box added up to (docs/adr/0073).

**One name is one institution.** Naming the same new body as sender and as
addressee creates exactly one `Organisation` row, used twice. Two rows would be
the catalogue quietly acquiring a duplicate on the most ordinary journey there
is (docs/adr/0063, docs/adr/0067). That journey is now `Muuda teemat`, and it is
where these are asserted.

**The promotion is gone**, with the question it served. `Uus teema` asked who
Koda answers, pre-filled it from Saatja and moved the chosen sender to the front
of the addressee choices so the answer would be visible — and the lawyers
reported the whole arrangement as one question asked twice (docs/adr/0089 §5).
The eight tests that pinned the ordering went with the ordering; what the
default *was* is recorded in ADR 0069 and in this file's history.
"""

from __future__ import annotations

import re

import pytest
from django.urls import reverse

from app.matters.forms import MatterCreateForm, MatterEditForm
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
    # `Lisa uus adressaat` is on `Muuda teemat`, not here: `Uus teema` asks one
    # counterparty question now (docs/adr/0089 §5).
    assert "Lisa uus adressaat" not in page


def test_the_retired_controls_are_gone_from_the_scripted_page(signed_in, komisjon):
    """«Vali nimekirjast» and «Uus saatja» reach an ordinary browser nowhere.

    They are still in the response — inside `<noscript>`, which is the fallback
    for a browser that cannot search the catalogue — and a scripted browser
    never parses that into anything. So the assertion has to be made against the
    document *minus* those blocks, which is what the person actually sees
    (task §12, §22).

    `Saabunud` keeps the old pair of controls and is not asserted here: that
    round changed `Uus teema` and deliberately nothing else (task §26).
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
    # Adressaat's half is not on this page at all (docs/adr/0089 §5).
    assert 'name="addressee_name"' not in scripted


def test_the_search_box_is_the_first_control_in_each_field(signed_in, specialist, komisjon):
    """The one structural claim worth making with a parser rather than a substring.

    «Search first» is the whole product decision, and a template that rendered
    the chips above the box would satisfy every substring assertion in this file
    while shipping the shape this round exists to replace. So the two fields are
    walked in document order and the first control in each is named (task §3).
    """
    from html.parser import HTMLParser

    for index in range(12):
        Organisation.objects.create(name=f"Asutus {index:02d}")
    # Both Teema forms: `Uus teema` renders one picker and `Muuda teemat` two,
    # and «search first» has to hold in every one of them.
    matter = factories.MatterFactory(owner=specialist)
    page = _without_noscript(
        signed_in.get(CREATE).content.decode()
        + signed_in.get(reverse("matters:matter_edit", kwargs={"pk": matter.pk})).content.decode()
    )

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


def test_an_existing_sender_is_in_the_rendered_addressee_catalogue(signed_in, specialist, komisjon):
    """CASE C from the report, asserted where the person would look for it.

    `tests/test_sender_free_entry.py` pins this on the form's choices. This pins
    it on the HTML, because "it is in `form.fields[...].choices`" and "it is on
    the page" are different claims and the second is the one that was doubted:
    a template that sliced the radio group wrongly would satisfy the first and
    fail the second.

    On `Muuda teemat`, which is the form that asks both questions now
    (docs/adr/0089 §5).
    """
    matter = factories.MatterFactory(owner=specialist)
    matter.source_organisations.add(komisjon)

    page = signed_in.get(reverse("matters:matter_edit", kwargs={"pk": matter.pk})).content.decode()
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

    form = MatterEditForm(matter=factories.MatterFactory(owner=specialist), viewer=specialist)
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
# One typed name, one row
# ---------------------------------------------------------------------------
#
# On `Muuda teemat`, which is the one form that asks both questions since
# Adressaat left the create screen (docs/adr/0089 §5). The rule being pinned is
# the transaction's — two independent resolvers naming the same body must not
# each decide it is new — and that is as true of a correction as it was of a
# capture.


def both_fields(signed_in, specialist, **fields):
    """POST `Muuda teemat` with both typed controls filled, and return the Matter."""
    matter = factories.MatterFactory(owner=specialist, title="Vastus komisjonile")
    signed_in.post(
        reverse("matters:matter_edit", kwargs={"pk": matter.pk}),
        {
            "title": matter.title,
            "brief_summary": matter.brief_summary,
            "visibility": matter.visibility,
            **fields,
        },
    )
    matter.refresh_from_db()
    return matter


def test_one_new_name_typed_into_both_fields_creates_one_organisation(signed_in, specialist):
    """The whole of §14, as the only assertion that matters: a count of one.

    A person types «Euroopa Näidiskomisjon» as the sender, chooses that same
    name as the addressee because they are answering the letter, and presses
    Loo teema. Two independent resolvers running in one transaction could each
    decide the name is new — so the register would gain two identical
    institutions on the most ordinary journey the form has.
    """
    matter = both_fields(
        signed_in,
        specialist,
        sender_name="Euroopa Näidiskomisjon",
        addressee_name="Euroopa Näidiskomisjon",
    )

    rows = Organisation.objects.filter(name="Euroopa Näidiskomisjon")
    assert rows.count() == 1

    organisation = rows.get()
    assert list(matter.source_organisations.all()) == [organisation]
    assert matter.addressee_organisation == organisation


def test_the_same_name_in_different_spacing_is_still_one_row(signed_in, specialist):
    """Normalisation is shared, so the two fields cannot disagree about identity."""
    both_fields(
        signed_in,
        specialist,
        sender_name="Euroopa   Näidiskomisjon",
        addressee_name=" Euroopa Näidiskomisjon ",
    )

    assert Organisation.objects.filter(name="Euroopa Näidiskomisjon").count() == 1


def test_a_refused_save_creates_no_organisation_from_either_field(signed_in, specialist):
    """One transaction, so a late refusal takes both resolutions with it."""
    matter = factories.MatterFactory(owner=specialist)
    before = Organisation.objects.count()

    response = signed_in.post(
        reverse("matters:matter_edit", kwargs={"pk": matter.pk}),
        {
            # No title: the one required field, so the save is refused after the
            # form has already been asked what the names mean.
            "title": "",
            "visibility": matter.visibility,
            "sender_name": "Euroopa Näidiskomisjon",
            "addressee_name": "Euroopa Näidiskomisjon",
        },
    )

    assert response.status_code in (200, 400)
    assert Organisation.objects.count() == before
    assert not Organisation.objects.filter(name="Euroopa Näidiskomisjon").exists()


def test_a_typed_name_matching_an_existing_body_reuses_it_in_both_fields(
    signed_in, specialist, komisjon
):
    """Reuse, not create — and the same row on both relations."""
    matter = both_fields(
        signed_in,
        specialist,
        sender_name="Euroopa Komisjon",
        addressee_name="Euroopa Komisjon",
    )

    assert Organisation.objects.filter(name="Euroopa Komisjon").count() == 1
    assert list(matter.source_organisations.all()) == [komisjon]
    assert matter.addressee_organisation == komisjon
