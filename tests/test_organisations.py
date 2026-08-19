"""Institutions: the reference seed and the inline quick-create.

Both exist to answer "is this body already here?" safely. The tests that matter
are the ones proving the answer is never guessed: an exact match is reused, a
merely *similar* name is not, and nothing that already exists is rewritten.
"""

from __future__ import annotations

import pytest
from django.urls import reverse

from app.core.errors import DomainError
from app.matters.services import create_matter
from app.organisations.models import Organisation, OrganisationAlias, OrganisationType
from app.organisations.reference_data import MINISTRIES
from app.organisations.services import (
    find_exact,
    get_or_create_organisation,
    seed_reference_organisations,
)
from app.search.services import search_matters
from app.submissions.services import create_submission

pytestmark = pytest.mark.django_db


# -- the reference seed ----------------------------------------------------


def test_the_seed_creates_the_current_ministries() -> None:
    result = seed_reference_organisations()
    assert len(result.created) == len(MINISTRIES) == 11
    assert Organisation.objects.filter(organisation_type=OrganisationType.MINISTRY).count() == 11
    assert Organisation.objects.filter(name="Kliimaministeerium").exists()


def test_running_the_seed_twice_changes_nothing() -> None:
    seed_reference_organisations()
    before = Organisation.objects.count()

    second = seed_reference_organisations()
    assert second.created == []
    assert Organisation.objects.count() == before


def test_the_seed_creates_no_normalised_duplicate() -> None:
    """`Rahandusministeerium` typed with odd spacing is the same ministry."""
    Organisation.objects.create(
        name="  rahandus­ministeerium  ".replace("­", ""),
        organisation_type=OrganisationType.MINISTRY,
    )
    seed_reference_organisations()
    matches = Organisation.objects.filter(normalized_name="rahandusministeerium")
    assert matches.count() == 1


def test_the_seed_never_renames_an_existing_institution() -> None:
    """By the second run somebody may have edited a row deliberately."""
    existing = Organisation.objects.create(
        name="Kliimaministeerium", organisation_type=OrganisationType.OTHER, notes="käsitsi"
    )
    seed_reference_organisations()
    existing.refresh_from_db()
    assert existing.organisation_type == OrganisationType.OTHER
    assert existing.notes == "käsitsi"


def test_the_seed_adds_the_abbreviations_people_actually_type() -> None:
    seed_reference_organisations()
    ministry = find_exact("MKM")
    assert ministry is not None
    assert ministry.name == "Majandus- ja Kommunikatsiooniministeerium"


def test_an_alias_already_claimed_elsewhere_is_left_alone() -> None:
    """Moving somebody's recorded decision silently would be worse than skipping."""
    other = Organisation.objects.create(name="Muu asutus", organisation_type=OrganisationType.OTHER)
    OrganisationAlias.objects.create(organisation=other, alias="RM")

    seed_reference_organisations()
    assert find_exact("RM") == other


def test_the_ministries_that_are_not_the_same_institution_stay_apart() -> None:
    """Keskkonna- and Kliimaministeerium score highly against each other and are
    different bodies with different remits. Nothing here may merge them."""
    legacy = Organisation.objects.create(
        name="Keskkonnaministeerium", organisation_type=OrganisationType.MINISTRY
    )
    seed_reference_organisations()

    climate = Organisation.objects.get(name="Kliimaministeerium")
    assert climate.pk != legacy.pk
    assert Organisation.objects.filter(name="Keskkonnaministeerium").exists()


def test_the_command_is_idempotent(capsys) -> None:
    from django.core.management import call_command

    call_command("seed_public_organisations")
    call_command("seed_public_organisations")
    assert Organisation.objects.filter(name="Siseministeerium").count() == 1


# -- quick create ----------------------------------------------------------


def test_a_new_institution_is_created() -> None:
    result = get_or_create_organisation(name="Riigikogu majanduskomisjon")
    assert result.created
    assert result.organisation.organisation_type == OrganisationType.OTHER


def test_an_exact_existing_institution_is_reused_not_duplicated() -> None:
    first = get_or_create_organisation(
        name="Näidisamet", organisation_type=OrganisationType.AUTHORITY
    )
    second = get_or_create_organisation(name="  näidisamet ")

    assert not second.created
    assert second.organisation.pk == first.organisation.pk
    assert Organisation.objects.filter(normalized_name="naidisamet").count() == 1


def test_an_alias_resolves_to_its_institution() -> None:
    seed_reference_organisations()
    result = get_or_create_organisation(name="HTM")
    assert not result.created
    assert result.organisation.name == "Haridus- ja Teadusministeerium"


def test_a_merely_similar_name_creates_a_separate_row() -> None:
    """No fuzzy auto-merge. A wrong merge is unrecoverable; a duplicate is not."""
    get_or_create_organisation(name="Kliimaministeerium")
    result = get_or_create_organisation(name="Kliimaamet")
    assert result.created
    assert Organisation.objects.count() == 2


def test_a_registry_code_clash_reuses_the_existing_row() -> None:
    first = get_or_create_organisation(name="Näidis AS", registry_code="12345678")
    second = get_or_create_organisation(name="Näidis Aktsiaselts", registry_code="12345678")
    assert not second.created
    assert second.organisation.pk == first.organisation.pk


def test_an_empty_name_is_refused() -> None:
    with pytest.raises(DomainError):
        get_or_create_organisation(name="   ")


def test_an_unknown_type_is_refused() -> None:
    with pytest.raises(DomainError):
        get_or_create_organisation(name="Midagi", organisation_type="NOT_A_TYPE")


def test_two_identical_names_make_the_lookup_refuse_to_guess() -> None:
    """ "Which of these did you mean" is a question for a person."""
    Organisation.objects.create(name="Kaksik", organisation_type=OrganisationType.OTHER)
    Organisation.objects.create(name="kaksik", organisation_type=OrganisationType.OTHER)
    assert find_exact("Kaksik") is None


# -- the endpoint ----------------------------------------------------------


def test_quick_create_needs_signing_in(client) -> None:
    assert client.get(reverse("organisations:quick_create")).status_code == 302


def test_quick_create_returns_a_picker_with_the_new_row_selected(client, specialist) -> None:
    client.force_login(specialist)
    response = client.post(
        reverse("organisations:quick_create"),
        {"name": "Riigikogu rahanduskomisjon", "target": "source_organisation"},
    )
    assert response.status_code == 200
    body = response.content.decode()
    assert "Riigikogu rahanduskomisjon" in body
    assert "selected" in body


def test_quick_create_says_so_when_it_reused_an_existing_row(client, specialist) -> None:
    """Silently selecting a different institution than the one somebody typed is
    how a Matter ends up filed against the wrong ministry."""
    Organisation.objects.create(name="Näidisamet", organisation_type=OrganisationType.AUTHORITY)
    client.force_login(specialist)
    response = client.post(
        reverse("organisations:quick_create"),
        {"name": "näidisamet", "target": "source_organisation"},
    )
    assert "oli juba olemas" in response.content.decode()


def test_quick_create_rejects_a_blank_name(client, specialist) -> None:
    client.force_login(specialist)
    response = client.post(
        reverse("organisations:quick_create"), {"name": "", "target": "source_organisation"}
    )
    assert response.status_code == 400


# -- direction stays a property of the relationship ------------------------


def test_sender_and_addressee_remain_separate_facts(db, specialist) -> None:
    """One Organisation table; the direction lives on the Matter's fields.

    Separate incoming and outgoing tables would make "did we ever write to the
    body that wrote to us" unanswerable.
    """
    sender = get_or_create_organisation(name="Rahandusministeerium").organisation
    addressee = get_or_create_organisation(name="Riigikogu majanduskomisjon").organisation

    matter = create_matter(
        title="Suunaga teema",
        owner=specialist,
        reference_year=2026,
        source_organisation=sender,
        addressee_organisation=addressee,
    )
    matter.refresh_from_db()
    assert matter.source_organisation == sender
    assert matter.addressee_organisation == addressee
    assert matter.source_organisation != matter.addressee_organisation


def test_a_submission_accepts_an_organisation_created_moments_ago(db, specialist) -> None:
    matter = create_matter(title="Arvamuse teema", owner=specialist, reference_year=2026)
    recipient = get_or_create_organisation(name="Riigikogu põhiseaduskomisjon").organisation

    submission = create_submission(
        matter=matter, title="Koja arvamus", actor=specialist, recipients=[recipient]
    )
    assert recipient in [r.organisation for r in submission.recipient_rows.all()]


def test_search_finds_a_matter_through_a_newly_created_organisation(db, specialist) -> None:
    organisation = get_or_create_organisation(name="Riigikogu keskkonnakomisjon").organisation
    create_matter(
        title="Komisjonile saadetud teema",
        owner=specialist,
        reference_year=2026,
        addressee_organisation=organisation,
    )
    found = [r.matter.title for r in search_matters(query="keskkonnakomisjon", user=specialist)]
    assert "Komisjonile saadetud teema" in found
