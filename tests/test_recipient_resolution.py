"""`resolve_recipients` — a letter's recipient set from ticked bodies and typed names.

These rules were proven through the closing composer, which resolved an opinion's
recipients in the same save that closed the file. The composer is retired
(ENG-050A2, docs/adr/0075); the rules are the function's own and it is still the
one definition — Uus teema's and Muuda teemat's senders resolve through it
(`matters.services.resolve_source_organisations`). So they are stated on the
function directly:

* exact normalised identity — a name or a recorded alias — reuses;
* anything merely similar becomes its own row;
* the same body reached twice is one recipient;
* ambiguity is refused, never guessed, and a refusal leaves nothing behind.
"""

from __future__ import annotations

import pytest
from django.db import transaction

from app.organisations.models import AliasType, Organisation, OrganisationAlias, OrganisationType
from app.organisations.services import resolve_recipients
from tests import factories
from tests.refusals import refused

pytestmark = pytest.mark.django_db

SEVEN_PARTIES = [
    "Näidiserakond Alpha",
    "Näidiserakond Beeta",
    "Näidiserakond Gamma",
    "Näidiserakond Delta",
    "Näidiserakond Epsilon",
    "Näidiserakond Zeeta",
    "Näidiserakond Eeta",
]

AMBIGUOUS = "«Näidiskogu» sobib mitme organisatsiooniga — vali nimekirjast."


def _two_under_one_spelling() -> None:
    Organisation.objects.create(name="Näidiskogu", organisation_type=OrganisationType.OTHER)
    Organisation.objects.create(name="Näidiskogu", organisation_type=OrganisationType.COMPANY)


def test_seven_new_names_are_seven_new_institutions():
    """The case a fixed checkbox list could not record at all."""
    before = Organisation.objects.count()

    resolved = resolve_recipients(chosen=[], typed_names=SEVEN_PARTIES)

    assert [organisation.name for organisation in resolved] == SEVEN_PARTIES
    assert Organisation.objects.count() == before + 7
    # Named exactly as typed, and classified as nothing the person did not say.
    assert {organisation.organisation_type for organisation in resolved} == {OrganisationType.OTHER}


def test_an_existing_name_is_reused_rather_than_duplicated():
    existing = factories.OrganisationFactory(name="Näidisministeerium Kaks")
    before = Organisation.objects.count()

    assert resolve_recipients(chosen=[], typed_names=["Näidisministeerium Kaks"]) == [existing]
    assert Organisation.objects.count() == before


def test_an_existing_alias_resolves_to_its_organisation():
    """An alias match is somebody's recorded decision, not a fuzzy guess."""
    existing = factories.OrganisationFactory(name="Näidisministeerium Kolm")
    OrganisationAlias.objects.create(
        organisation=existing, alias="NMK", alias_type=AliasType.ABBREVIATION
    )
    before = Organisation.objects.count()

    assert resolve_recipients(chosen=[], typed_names=["NMK"]) == [existing]
    assert Organisation.objects.count() == before


def test_the_same_name_typed_twice_is_one_recipient():
    resolved = resolve_recipients(
        chosen=[], typed_names=["Näidiserakond Eeta", "  Näidiserakond Eeta  "]
    )

    assert len(resolved) == 1
    assert Organisation.objects.filter(name="Näidiserakond Eeta").count() == 1


def test_a_chosen_body_and_the_same_typed_name_are_one_recipient():
    existing = factories.OrganisationFactory(name="Näidisministeerium Neli")

    assert resolve_recipients(chosen=[existing], typed_names=["Näidisministeerium Neli"]) == [
        existing
    ]


def test_similar_names_are_never_merged():
    """`Keskkonnaministeerium` and `Kliimaministeerium` score highly against each
    other and are different institutions. Only exact normalised identity reuses."""
    near = factories.OrganisationFactory(name="Näidisministeerium Viis")

    (resolved,) = resolve_recipients(chosen=[], typed_names=["Näidisministeerium Viisteist"])

    assert resolved != near
    assert resolved.name == "Näidisministeerium Viisteist"
    assert Organisation.objects.filter(name="Näidisministeerium Viis").exists()


def test_an_ambiguous_name_is_refused_rather_than_guessed():
    """Two institutions under one spelling is a question for a person. Picking
    one files the letter against a body nobody named; creating a third makes the
    ambiguity permanent."""
    _two_under_one_spelling()
    before = Organisation.objects.count()

    with refused(AMBIGUOUS):
        resolve_recipients(chosen=[], typed_names=["Näidiskogu"])

    assert Organisation.objects.count() == before


def test_a_refused_save_leaves_no_new_institution_behind():
    """New recipients are persisted by the save that uses them, never by typing.

    Seven good names and one ambiguous one inside the caller's transaction: the
    refusal rolls back the seven that would have been created with it."""
    _two_under_one_spelling()
    before = Organisation.objects.count()

    with refused(AMBIGUOUS), transaction.atomic():
        resolve_recipients(chosen=[], typed_names=[*SEVEN_PARTIES, "Näidiskogu"])

    assert Organisation.objects.count() == before
    assert not Organisation.objects.filter(name__in=SEVEN_PARTIES).exists()
