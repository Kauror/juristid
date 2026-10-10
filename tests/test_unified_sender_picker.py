"""Saatja is one control on all three sender pages, fed by one implementation.

`Uus teema`, `Muuda teemat` and `Saabunud` ask the same question — who sent
this file to Koda? — over the same catalogue. Until 2026-10-10 the first two
drew the unified picker and searched recorded spellings, and `Saabunud` drew the
older `sender_control.html` with plain checkboxes, so «MKM» found the ministry
everywhere except on the page where a ministry's letter actually lands. Each
form also carried its own copy of the shortlist/tail slicing and of the sender
union, and the shortlist was ranked by two implementations of one order
(`organisations_by_usage` and `OrganisationChoices.shortlist_and_tail`).

What is pinned here is that the three forms are now fed by one call and unioned
by one rule, so they cannot drift apart again: the same spellings, the same
chips in the same order, the same answer for the same POST, and one read of the
catalogue (AUD-011, product decision D-4, docs/adr/0073 amendment of
2026-10-10). The browser half is `e2e/test_saabunud_organisation_picker.py`.
"""

from __future__ import annotations

import re

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from app.core.enums import Visibility
from app.matters.forms import (
    IncomingIntakeForm,
    MatterCreateForm,
    MatterEditForm,
    organisations_by_usage,
    read_organisation_choices,
)
from app.matters.models import Matter
from app.organisations.models import Organisation, OrganisationType
from tests import factories

pytestmark = pytest.mark.django_db

INTAKE = reverse("matters:intake")


def _sender_forms(viewer, *, data=None) -> dict[str, object]:
    """The three sender forms, as each view builds them for one reader.

    `Muuda teemat` without a Matter, so it has no current senders to add to the
    chips and its shortlist is comparable with the other two as it stands.
    """
    return {
        "uus-teema": MatterCreateForm(data, viewer=viewer),
        "muuda-teemat": MatterEditForm(data, matter=None, viewer=viewer),
        "saabunud": IncomingIntakeForm(data, viewer=viewer),
    }


def _offered(form, field: str) -> list[str]:
    return [label for _value, label in form.fields[field].choices]


def _scripted(page: str) -> str:
    """The page a scripted browser builds: `<noscript>` content is never parsed."""
    return re.sub(r"<noscript>.*?</noscript>", "", page, flags=re.DOTALL)


def _upload(name: str, content: bytes):
    from django.core.files.uploadedfile import SimpleUploadedFile

    return SimpleUploadedFile(name, content, content_type="application/pdf")


@pytest.fixture
def ministry():
    """A body with a recorded abbreviation the label does not contain."""
    body = Organisation.objects.create(
        name="Majandus- ja Kommunikatsiooniministeerium",
        organisation_type=OrganisationType.MINISTRY,
    )
    body.aliases.create(alias="MKM")
    return body


@pytest.fixture
def busy_catalogue(specialist):
    """More institutions than the shortlist holds, several of them used.

    Twelve bodies, five of them senders on this reader's Matters with distinct
    counts, so the ranked half of the shortlist has an order of its own and the
    alphabetical top-up and the tail both have something to hold.
    """
    bodies = [factories.OrganisationFactory(name=f"Asutus {index:02d}") for index in range(12)]
    for rank, body in enumerate(reversed(bodies[:5]), start=1):
        for _ in range(rank):
            factories.MatterFactory(owner=specialist, source_organisations=[body])
    return bodies


# ---------------------------------------------------------------------------
# One search behaviour
# ---------------------------------------------------------------------------


def test_saabunud_writes_the_recorded_spelling_onto_the_sender_chip(specialist, ministry):
    """The alias reaches `Saabunud` the way it reaches the other two forms."""
    form = IncomingIntakeForm(viewer=specialist)

    for field in ("source_organisations", "source_organisations_other"):
        assert form.fields[field].widget.alias_terms.get(str(ministry.pk)) == "mkm"
    rendered = "".join(str(choice.tag()) for choice in form["source_organisations"])
    assert 'data-aliases="mkm"' in rendered


def test_every_sender_form_carries_the_same_spellings(specialist, ministry):
    terms = {
        name: form.fields["source_organisations"].widget.alias_terms
        for name, form in _sender_forms(specialist).items()
    }

    assert terms["uus-teema"] == terms["muuda-teemat"] == terms["saabunud"]
    assert terms["saabunud"][str(ministry.pk)] == "mkm"


def test_the_saabunud_page_draws_the_unified_picker(signed_in, ministry):
    """The same partial as the other two pages, with its alias on the chip."""
    page = signed_in.get(INTAKE).content.decode()
    scripted = _scripted(page)

    assert 'id="saabunud-saatja-valik" data-orgfind' in page
    assert 'id="saabunud-saatja-otsi"' in scripted
    assert "Otsi või lisa asutus…" in scripted
    assert 'data-aliases="mkm"' in scripted
    # The older control is gone from what a scripted browser builds: no
    # disclosure and no visible second box for a new name.
    assert "Vali nimekirjast" not in scripted
    assert 'name="sender_name" type="text"' not in scripted
    assert re.search(r'<input type="hidden"[^>]*name="sender_name"', scripted)


def test_the_saabunud_carrier_comes_before_its_noscript_fallback(signed_in):
    """With scripting off Django reads the last `sender_name`, which must be the
    box the person typed into (docs/adr/0144 §4)."""
    page = signed_in.get(INTAKE).content.decode()
    picker = page[page.index('id="saabunud-saatja-valik"') :]

    assert picker.index('name="sender_name"') < picker.index("<noscript>")


# ---------------------------------------------------------------------------
# One ranking
# ---------------------------------------------------------------------------


def test_the_three_forms_offer_the_same_chips_and_tail_in_the_same_order(
    specialist, busy_catalogue
):
    forms = _sender_forms(specialist)
    shortlists = {name: _offered(form, "source_organisations") for name, form in forms.items()}
    tails = {name: _offered(form, "source_organisations_other") for name, form in forms.items()}

    expected_short, expected_tail = read_organisation_choices(specialist).shortlist_and_tail()
    assert shortlists["uus-teema"] == [item.name for item in expected_short]
    assert shortlists["uus-teema"] == shortlists["muuda-teemat"] == shortlists["saabunud"]
    assert tails["uus-teema"] == [item.name for item in expected_tail]
    assert tails["uus-teema"] == tails["muuda-teemat"] == tails["saabunud"]
    # Usage first, best first; the alphabetical top-up after it.
    assert shortlists["saabunud"][:5] == [f"Asutus {index:02d}" for index in range(5)]
    assert len(shortlists["saabunud"]) == 8
    assert all(form.sender_tail_count == 4 for form in forms.values())


def test_organisations_by_usage_is_the_shortlist_of_the_one_ranking(specialist, busy_catalogue):
    shortlist, _tail = read_organisation_choices(specialist).shortlist_and_tail()

    assert organisations_by_usage(specialist) == shortlist


def test_muuda_teemat_keeps_current_and_suggested_senders_among_the_chips(specialist):
    """Outside the ranked eight, and still chips rather than hidden in the tail."""
    for index in range(8):
        body = factories.OrganisationFactory(name=f"Sage {index:02d}")
        factories.MatterFactory(owner=specialist, source_organisations=[body])
    current = factories.OrganisationFactory(name="Zulu praegune saatja")
    suggested = factories.OrganisationFactory(name="Zulu pakutud saatja")
    matter = factories.MatterFactory(owner=specialist, source_organisations=[current])
    ranked = organisations_by_usage(specialist)
    assert current not in ranked and suggested not in ranked, "the test would prove nothing"

    form = MatterEditForm(matter=matter, viewer=specialist, suggested_senders=[suggested])

    chips = _offered(form, "source_organisations")
    assert chips[: len(ranked)] == [item.name for item in ranked]
    assert chips[len(ranked) :] == [current.name, suggested.name]
    tail = _offered(form, "source_organisations_other")
    assert current.name not in tail and suggested.name not in tail


# ---------------------------------------------------------------------------
# One answer
# ---------------------------------------------------------------------------


def test_the_union_of_chosen_and_typed_senders_is_the_same_on_every_form(
    specialist, busy_catalogue
):
    """Ticked in either half, ticked twice, and typed: one answer, by name."""
    shortlist, tail = read_organisation_choices(specialist).shortlist_and_tail()
    first, second, third = shortlist[0], shortlist[1], tail[0]
    data = {
        "title": "Kolm saatjat",
        "source_organisations": [str(first.pk), str(second.pk)],
        "source_organisations_other": [str(second.pk), str(third.pk)],
        "sender_name": "  Eesti   Näidisliit ",
    }

    answers = {}
    for name, form in _sender_forms(specialist, data=data).items():
        form.is_valid()
        answers[name] = (
            [item.name for item in form.cleaned_data["source_organisations"]],
            form.cleaned_data["sender_name"],
        )

    expected = (sorted([first.name, second.name, third.name]), "Eesti Näidisliit")
    assert answers == {"uus-teema": expected, "muuda-teemat": expected, "saabunud": expected}


def test_saabunud_files_a_chosen_and_a_typed_sender_and_creates_the_new_one_once(
    signed_in, specialist, ministry, evidence_root
):
    """The typed name is resolved once, server-side, beside the chosen body."""
    from tests import synthetic_corpus as corpus

    response = signed_in.post(
        INTAKE,
        {
            "title": "Saabunud kahe saatjaga",
            "visibility": Visibility.NORMAL,
            "source_organisations_other": [str(ministry.pk)],
            "sender_name": "Eesti Näidisliit",
            "uploads": _upload("kaaskiri.pdf", corpus.government_pdf()),
        },
    )

    assert response.status_code == 302
    matter = Matter.objects.get(title="Saabunud kahe saatjaga")
    assert sorted(item.name for item in matter.source_organisations.all()) == [
        "Eesti Näidisliit",
        ministry.name,
    ]
    assert Organisation.objects.filter(name="Eesti Näidisliit").count() == 1


def test_saabunud_reuses_a_body_typed_by_its_recorded_alias(
    signed_in, specialist, ministry, evidence_root
):
    from tests import synthetic_corpus as corpus

    before = Organisation.objects.count()
    signed_in.post(
        INTAKE,
        {
            "title": "Saabunud lühendiga",
            "sender_name": "MKM",
            "uploads": _upload("kaaskiri.pdf", corpus.government_pdf()),
        },
    )

    matter = Matter.objects.get(title="Saabunud lühendiga")
    assert list(matter.source_organisations.all()) == [ministry]
    assert Organisation.objects.count() == before


# ---------------------------------------------------------------------------
# One read
# ---------------------------------------------------------------------------


def _catalogue_reads(build) -> int:
    table = f'FROM "{Organisation._meta.db_table}"'
    with CaptureQueriesContext(connection) as captured:
        build()
    return sum(1 for query in captured.captured_queries if table in query["sql"])


def test_uus_teema_reads_the_catalogue_once(specialist, busy_catalogue):
    """It was read twice — the tail, and the ranked rows plus their top-up."""
    assert _catalogue_reads(lambda: MatterCreateForm(viewer=specialist)) == 1


def test_saabunud_reads_the_catalogue_once(specialist, busy_catalogue):
    assert _catalogue_reads(lambda: IncomingIntakeForm(viewer=specialist)) == 1
