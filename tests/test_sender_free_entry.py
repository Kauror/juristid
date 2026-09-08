"""Saatja, typed on the form that uses it, over one shared catalogue.

The workflow this replaces was written on the page in so many words:

    Kui saatjat siin ei ole, tuleb asutus enne lisada asutuste alla —
    teema vormilt uut asutust ei teki.

Nobody performed it. They filed the Teema with no sender, so the register lost
the fact rather than gaining a considered one (docs/adr/0063).

These tests pin three things, and they are deliberately different in kind:

* **identity**, which is the whole risk of the convenience — normalised exact or
  nothing, the same rule `tests/test_addressee_free_entry.py` pins for the other
  counterparty field, because it is literally the same function;
* **union**, which is where the sender contract legitimately differs from the
  addressee one. `Matter` holds several senders, so a typed name is added to
  what is ticked rather than winning over it;
* **one catalogue**, which is what the department actually asked for: a body
  named while entering a sender has to be available as an addressee afterwards,
  and the other way round, because there is one `Organisation` table and not
  two.
"""

from __future__ import annotations

import pytest
from django.urls import reverse

from app.core.enums import Visibility
from app.core.errors import DomainError
from app.matters.forms import MatterCreateForm, organisations_by_usage
from app.matters.models import Matter
from app.matters.services import resolve_source_organisations
from app.organisations.models import AliasType, Organisation
from tests import factories

pytestmark = pytest.mark.django_db

CREATE = reverse("matters:matter_create")
INTAKE = reverse("matters:intake")


def _edit(matter) -> str:
    return reverse("matters:matter_edit", kwargs={"pk": matter.pk})


# ---------------------------------------------------------------------------
# The resolver
# ---------------------------------------------------------------------------


def test_a_typed_sender_creates_one_organisation():
    resolved = resolve_source_organisations(chosen=[], typed_name="Eesti Näidisliit")

    assert [item.name for item in resolved] == ["Eesti Näidisliit"]
    assert Organisation.objects.count() == 1


def test_an_exact_existing_name_reuses_the_row_it_already_has():
    existing = factories.OrganisationFactory(name="Kliimaministeerium")

    resolved = resolve_source_organisations(chosen=[], typed_name="Kliimaministeerium")

    assert resolved == [existing]
    assert Organisation.objects.count() == 1


def test_a_recorded_alias_reuses_the_institution_it_names():
    ministry = factories.OrganisationFactory(name="Majandus- ja Kommunikatsiooniministeerium")
    ministry.aliases.create(alias="MKM", alias_type=AliasType.ABBREVIATION)

    resolved = resolve_source_organisations(chosen=[], typed_name="MKM")

    assert resolved == [ministry]
    assert Organisation.objects.count() == 1


def test_an_ambiguous_name_is_refused_rather_than_guessed():
    """Two rows under one spelling is a question for a person.

    Creating a third would make the ambiguity permanent and picking one would
    file the Teema against a body nobody named.
    """
    factories.OrganisationFactory(name="Ministeerium")
    factories.OrganisationFactory(name="ministeerium")

    with pytest.raises(DomainError):
        resolve_source_organisations(chosen=[], typed_name="Ministeerium")

    assert Organisation.objects.count() == 2


def test_a_typed_sender_is_added_to_the_ticked_ones_not_preferred_over_them():
    """The one place this differs from `resolve_addressee`, and it follows from
    the cardinality: a Matter really can arrive from two bodies at once."""
    chosen = factories.OrganisationFactory(name="Euroopa Komisjon")

    resolved = resolve_source_organisations(chosen=[chosen], typed_name="Eesti Näidisliit")

    assert [item.name for item in resolved] == ["Euroopa Komisjon", "Eesti Näidisliit"]


def test_a_body_ticked_and_then_typed_is_one_sender():
    chosen = factories.OrganisationFactory(name="Kliimaministeerium")

    resolved = resolve_source_organisations(chosen=[chosen], typed_name="Kliimaministeerium")

    assert resolved == [chosen]
    assert Organisation.objects.count() == 1


def test_a_blank_typed_name_leaves_the_ticked_senders_alone():
    chosen = factories.OrganisationFactory(name="Kliimaministeerium")

    assert resolve_source_organisations(chosen=[chosen], typed_name="   ") == [chosen]
    assert Organisation.objects.count() == 1


# ---------------------------------------------------------------------------
# Through the forms
# ---------------------------------------------------------------------------


def test_uus_teema_files_a_typed_sender(signed_in, specialist):
    signed_in.post(CREATE, {"title": "Uue saatjaga", "sender_name": "Eesti Näidisliit"})

    matter = Matter.objects.get(title="Uue saatjaga")
    assert [item.name for item in matter.source_organisations.all()] == ["Eesti Näidisliit"]


def test_uus_teema_unions_a_ticked_sender_with_a_typed_one(signed_in, specialist):
    chosen = factories.OrganisationFactory(name="Euroopa Komisjon")

    signed_in.post(
        CREATE,
        {
            "title": "Kahe saatjaga",
            "source_organisations": [str(chosen.pk)],
            "sender_name": "Eesti Näidisliit",
        },
    )

    matter = Matter.objects.get(title="Kahe saatjaga")
    assert {item.name for item in matter.source_organisations.all()} == {
        "Euroopa Komisjon",
        "Eesti Näidisliit",
    }


def test_muuda_teemat_files_a_typed_sender(signed_in, specialist):
    matter = factories.MatterFactory(owner=specialist, title="Muudetav")

    signed_in.post(
        _edit(matter),
        {
            "title": matter.title,
            "brief_summary": matter.brief_summary,
            "visibility": matter.visibility,
            "sender_name": "Eesti Näidisliit",
        },
    )

    matter.refresh_from_db()
    assert [item.name for item in matter.source_organisations.all()] == ["Eesti Näidisliit"]


def test_saabunud_files_a_typed_sender(signed_in, specialist, evidence_root):
    from tests import synthetic_corpus as corpus

    signed_in.post(
        INTAKE,
        {
            "title": "Saabunud uue saatjaga",
            "visibility": Visibility.NORMAL,
            "sender_name": "Eesti Näidisliit",
            "uploads": _upload("kaaskiri.pdf", corpus.government_pdf()),
        },
    )

    matter = Matter.objects.get(title="Saabunud uue saatjaga")
    assert [item.name for item in matter.source_organisations.all()] == ["Eesti Näidisliit"]


def _upload(name: str, content: bytes, content_type: str = "application/pdf"):
    from django.core.files.uploadedfile import SimpleUploadedFile

    return SimpleUploadedFile(name, content, content_type=content_type)


def test_an_ambiguous_typed_sender_refuses_the_save_and_creates_nothing(signed_in, specialist):
    factories.OrganisationFactory(name="Ministeerium")
    factories.OrganisationFactory(name="ministeerium")

    response = signed_in.post(CREATE, {"title": "Mitmetimõistetav", "sender_name": "Ministeerium"})

    assert response.status_code == 400
    assert not Matter.objects.filter(title="Mitmetimõistetav").exists()
    assert Organisation.objects.count() == 2


def test_a_refused_save_leaves_no_stray_sender_organisation(signed_in, specialist):
    """The transaction boundary, from the outside.

    The sender is resolved inside the same atomic block that writes the Matter,
    so a rejected attachment after it takes the institution with it. Without
    that, a refused save would leave a body in the catalogue that no Matter
    names and nobody asked for.
    """
    response = signed_in.post(
        CREATE,
        {
            "title": "Vigase failiga",
            "sender_name": "Eesti Näidisliit",
            "files": _upload("paha.exe", b"MZ", content_type="application/x-msdownload"),
        },
    )

    assert response.status_code == 400
    assert not Matter.objects.filter(title="Vigase failiga").exists()
    assert not Organisation.objects.filter(name="Eesti Näidisliit").exists()


# ---------------------------------------------------------------------------
# One catalogue
# ---------------------------------------------------------------------------


def test_an_organisation_created_as_a_sender_is_offered_as_an_addressee(signed_in, specialist):
    """CASE A. The department's own words: one place containing organisations."""
    signed_in.post(CREATE, {"title": "Saatja kaudu", "sender_name": "Eesti Näidisliit"})
    created = Organisation.objects.get(name="Eesti Näidisliit")

    form = MatterCreateForm(viewer=specialist)
    offered = {value for value, _label in form.fields["addressee_organisation"].choices}

    assert created.pk in offered


def test_an_organisation_created_as_an_addressee_is_offered_as_a_sender(signed_in, specialist):
    """CASE B, and the direction that used to be impossible to reach at all."""
    signed_in.post(CREATE, {"title": "Adressaadi kaudu", "addressee_name": "Eesti Näidisliit"})
    created = Organisation.objects.get(name="Eesti Näidisliit")

    form = MatterCreateForm(viewer=specialist)
    sender_choices = {
        value
        for field in ("source_organisations", "source_organisations_other")
        for value, _label in form.fields[field].choices
    }

    assert created.pk in sender_choices


def test_the_same_row_is_reused_whichever_field_names_it_second(signed_in, specialist):
    """No sender-only or addressee-only catalogue: one `Organisation` row."""
    signed_in.post(CREATE, {"title": "Esimene", "sender_name": "Eesti Näidisliit"})
    signed_in.post(CREATE, {"title": "Teine", "addressee_name": "Eesti Näidisliit"})

    assert Organisation.objects.filter(name="Eesti Näidisliit").count() == 1
    first = Matter.objects.get(title="Esimene")
    second = Matter.objects.get(title="Teine")
    assert first.source_organisations.first() == second.addressee_organisation


def test_sender_and_addressee_remain_separate_relations():
    """One catalogue is not one field. Naming a body as a sender must not make
    it an addressee, which is the register's own history: the counterparty
    column changed meaning from `KELLELT` to `KELLELE` in 2020."""
    matter = factories.MatterFactory()
    organisation = factories.OrganisationFactory(name="Kliimaministeerium")
    matter.source_organisations.add(organisation)

    matter.refresh_from_db()
    assert matter.addressee_organisation is None
    assert list(matter.source_organisations.all()) == [organisation]


# ---------------------------------------------------------------------------
# The quick shortlist
# ---------------------------------------------------------------------------


def test_the_shortlist_does_not_collapse_to_one_chip(specialist):
    """The reported defect: one body with sender history, one quick choice.

    The fallback only fired when there was *no* usage at all, so the
    nearly-empty case — the case a young dataset is in — was the one case not
    handled (docs/adr/0063).
    """
    used = factories.OrganisationFactory(name="Ainus kasutatud")
    factories.MatterFactory(owner=specialist, source_organisations=[used])
    for index in range(9):
        factories.OrganisationFactory(name=f"Asutus {index:02d}")

    shortlist = organisations_by_usage(specialist)

    assert len(shortlist) == 8
    assert shortlist[0] == used


def test_direct_sender_usage_outranks_the_fallbacks(specialist):
    sender = factories.OrganisationFactory(name="Zulu saatja")
    addressee = factories.OrganisationFactory(name="Yankee adressaat")
    factories.OrganisationFactory(name="Aabits")

    factories.MatterFactory(owner=specialist, source_organisations=[sender])
    factories.MatterFactory(owner=specialist, addressee_organisation=addressee)

    shortlist = [item.name for item in organisations_by_usage(specialist)]

    assert shortlist[0] == "Zulu saatja"
    assert shortlist.index("Yankee adressaat") < shortlist.index("Aabits")


def test_a_short_catalogue_offers_everything_it_has(specialist):
    for index in range(3):
        factories.OrganisationFactory(name=f"Asutus {index}")

    assert len(organisations_by_usage(specialist)) == 3


def test_a_restricted_matter_moves_no_chip(specialist, reader):
    """The authorization rule, stated on the shortlist rather than on a page.

    Four restricted Matters name one body and one visible Matter names another.
    A reader who cannot see the restricted four must not be shown an order that
    only those four could explain — that order *is* the disclosure.
    """
    hidden = factories.OrganisationFactory(name="Zulu salajane")
    open_sender = factories.OrganisationFactory(name="Avalik saatja")

    for _ in range(4):
        factories.MatterFactory(
            owner=specialist,
            visibility=Visibility.RESTRICTED,
            source_organisations=[hidden],
        )
    factories.MatterFactory(owner=specialist, source_organisations=[open_sender])

    for_reader = [item.name for item in organisations_by_usage(reader)]
    for_specialist = [item.name for item in organisations_by_usage(specialist)]

    # The reader's order is the alphabet after the one body they can account
    # for; the specialist's is led by the body four hidden Matters name.
    assert for_reader[0] == "Avalik saatja"
    assert for_specialist[0] == "Zulu salajane"


def test_a_selected_sender_outside_the_shortlist_stays_visible(signed_in, specialist):
    """A Matter's own senders are chips on the edit form even when nothing about
    this reader's history would have offered them."""
    from app.matters.forms import MatterEditForm

    unusual = factories.OrganisationFactory(name="Zulu harv asutus")
    for index in range(10):
        factories.OrganisationFactory(name=f"Asutus {index:02d}")
    matter = factories.MatterFactory(owner=specialist, source_organisations=[unusual])

    form = MatterEditForm(matter=matter, viewer=specialist)

    assert unusual in form.frequent_senders


# ---------------------------------------------------------------------------
# Query cost
# ---------------------------------------------------------------------------


def test_the_sender_control_costs_the_same_whatever_the_catalogue_holds(
    signed_in, specialist, django_assert_max_num_queries
):
    """No query per chip, and none per row of the catalogue list.

    Asserted as *constancy* rather than against a number, deliberately. A
    ceiling is a number somebody has to re-measure and re-justify every time an
    unrelated panel is added to this page, and the property this control has to
    keep is not "costs 24 queries" — it is "costs the same for eight bodies as
    for eighty". The shortlist is two aggregates and a fetch; the list beside it
    is one query; both are bounded by `SENDER_SHORTLIST_SIZE` and by the
    catalogue, not by what is rendered.
    """
    for index in range(8):
        organisation = factories.OrganisationFactory(name=f"Algne {index:02d}")
        factories.MatterFactory(owner=specialist, source_organisations=[organisation])

    with django_assert_max_num_queries(200) as small:
        signed_in.get(CREATE)
    baseline = len(small.captured_queries)

    for index in range(40):
        factories.OrganisationFactory(name=f"Lisatud {index:02d}")

    with django_assert_max_num_queries(baseline) as large:
        signed_in.get(CREATE)

    assert len(large.captured_queries) == baseline


# ---------------------------------------------------------------------------
# The rail's own inline editor
# ---------------------------------------------------------------------------


def _inline(matter, field: str) -> str:
    return reverse("matters:update_field", kwargs={"pk": matter.pk, "field": field})


def test_the_rail_editor_files_a_typed_sender(signed_in, specialist):
    """The fourth place a sender is set, and it must not be the one place a
    body cannot be named."""
    matter = factories.MatterFactory(owner=specialist)

    signed_in.post(_inline(matter, "source_organisations"), {"sender_name": "Eesti Näidisliit"})

    matter.refresh_from_db()
    assert [item.name for item in matter.source_organisations.all()] == ["Eesti Näidisliit"]


def test_the_rail_editor_unions_a_ticked_sender_with_a_typed_one(signed_in, specialist):
    matter = factories.MatterFactory(owner=specialist)
    chosen = factories.OrganisationFactory(name="Euroopa Komisjon")

    signed_in.post(
        _inline(matter, "source_organisations"),
        {"source_organisations": [str(chosen.pk)], "sender_name": "Eesti Näidisliit"},
    )

    matter.refresh_from_db()
    assert {item.name for item in matter.source_organisations.all()} == {
        "Euroopa Komisjon",
        "Eesti Näidisliit",
    }


def test_an_ambiguous_name_in_the_rail_editor_changes_nothing(signed_in, specialist):
    """Refused, and refused *whole*: the sender set the Matter already had is
    untouched and no third row is created."""
    existing = factories.OrganisationFactory(name="Kliimaministeerium")
    matter = factories.MatterFactory(owner=specialist, source_organisations=[existing])
    factories.OrganisationFactory(name="Ministeerium")
    factories.OrganisationFactory(name="ministeerium")

    response = signed_in.post(
        _inline(matter, "source_organisations"),
        {"source_organisations": [str(existing.pk)], "sender_name": "Ministeerium"},
    )

    assert response.status_code == 400
    matter.refresh_from_db()
    assert list(matter.source_organisations.all()) == [existing]
    assert Organisation.objects.count() == 3


def test_an_empty_rail_post_still_clears_every_sender(signed_in, specialist):
    """The behaviour the typed box must not break. An empty POST on this
    endpoint means «clear them all», which is why the field is named in the URL
    (Agent-E brief 20, 34)."""
    existing = factories.OrganisationFactory(name="Kliimaministeerium")
    matter = factories.MatterFactory(owner=specialist, source_organisations=[existing])

    signed_in.post(_inline(matter, "source_organisations"), {})

    matter.refresh_from_db()
    assert list(matter.source_organisations.all()) == []
