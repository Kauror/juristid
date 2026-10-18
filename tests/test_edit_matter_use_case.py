"""`workspace.edit_matter` — `Muuda teemat`'s save, without a view (JARCH-001).

The twelve service calls of a whole-record correction used to be composed in
`views.matter_edit`, where their order — the revision guard first, the held
`Õigusakt` read before it is rewritten, the `Menetluse link` inside the same
transaction, `Hetkeseis` last — was a property of one view. These tests call
the use case directly, so any other caller gets the same guarantees:

* every fact is applied, through its own service;
* a copy filled before somebody else's save is refused and writes nothing;
* only a change of `Õigusakt` in this save moves `Koja algatus`, which needs
  the instrument as it stood **before** the save;
* the procedural-link step runs after every other fact and before the stage,
  inside the transaction: a refusal there takes everything back, including a
  newly typed institution.
"""

from __future__ import annotations

import pytest

from app.core.errors import DomainError
from app.matters import workspace
from app.matters.initiative import KODA_PROPOSAL_INSTRUMENT_KEY, is_koda_initiative
from app.matters.models import Matter
from app.matters.services import MATTER_EDIT_CONFLICT, matter_revision_token
from app.organisations.models import Organisation
from app.taxonomy.models import LegalInstrumentType
from tests import factories
from tests.refusals import refused

pytestmark = pytest.mark.django_db

LINK_REFUSED = "Sünteetiline menetluse lingi keeldumine."


def _instrument(key: str) -> list[LegalInstrumentType]:
    return [LegalInstrumentType.objects.get(key=key)]


def _edit(matter: Matter, actor, **overrides):
    """The whole record as it stands, with ``overrides`` — what the page posts."""
    answers = {
        "expected_revision": matter_revision_token(matter),
        "title": matter.title,
        "brief_summary": matter.brief_summary,
        "owner": matter.owner,
        "source_organisations": list(matter.source_organisations.all()),
        "sender_name": "",
        "legal_instruments": list(matter.legal_instruments.all()),
        "legal_instrument_other": matter.legal_instrument_other,
        "received_date": matter.received_date,
        "response_deadline": matter.response_deadline,
        "policy_areas": list(matter.policy_areas.all()),
        "policy_area_other": matter.policy_area_other,
        "stage": None,
        **overrides,
    }
    workspace.edit_matter(matter=matter, actor=actor, **answers)
    matter.refresh_from_db()
    return matter


def test_every_fact_is_applied(specialist, other_specialist):
    matter = factories.MatterFactory(owner=specialist, title="Vana pealkiri")

    _edit(
        matter,
        specialist,
        title="Uus pealkiri",
        brief_summary="Lühikokkuvõte.",
        owner=other_specialist,
        sender_name="Sünteetiline Saatjaamet",
        policy_area_other="Muu valdkond",
        legal_instrument_other="Muu akt",
    )

    assert matter.title == "Uus pealkiri"
    assert matter.brief_summary == "Lühikokkuvõte."
    assert matter.owner == other_specialist
    assert [org.name for org in matter.source_organisations.all()] == ["Sünteetiline Saatjaamet"]
    assert matter.policy_area_other == "Muu valdkond"
    assert matter.legal_instrument_other == "Muu akt"


def test_a_stale_copy_is_refused_and_writes_nothing(specialist):
    matter = factories.MatterFactory(owner=specialist, title="Puutumata")
    stale = matter_revision_token(matter)
    _edit(matter, specialist, brief_summary="Kellegi teise salvestus.")

    with refused(MATTER_EDIT_CONFLICT):
        _edit(
            matter,
            specialist,
            expected_revision=stale,
            title="Vana koopia",
            sender_name="Sünteetiline Vananenud Amet",
        )

    matter.refresh_from_db()
    assert matter.title == "Puutumata"
    assert not Organisation.objects.filter(name="Sünteetiline Vananenud Amet").exists()


def test_a_change_to_the_proposal_instrument_makes_it_an_initiative(specialist):
    """Proves the held instrument is read before `set_legal_instruments`.

    Read afterwards, the save would compare the new instrument with itself,
    see no change, and leave the track as it was.
    """
    matter = factories.MatterFactory(owner=specialist)
    _edit(matter, specialist, legal_instruments=_instrument("seadus"))
    assert not is_koda_initiative(matter)

    _edit(matter, specialist, legal_instruments=_instrument(KODA_PROPOSAL_INSTRUMENT_KEY))
    assert is_koda_initiative(matter)

    # An unrelated correction leaves the track as it is.
    _edit(matter, specialist, title="Muu parandus")
    assert is_koda_initiative(matter)

    _edit(matter, specialist, legal_instruments=_instrument("seadus"))
    assert not is_koda_initiative(matter)


def test_the_link_step_runs_after_every_other_fact_and_inside_the_save(specialist):
    matter = factories.MatterFactory(owner=specialist, title="Enne")
    seen: list[str] = []

    def save_link() -> None:
        seen.append(Matter.objects.get(pk=matter.pk).title)

    _edit(matter, specialist, title="Pärast", save_procedural_link=save_link)

    assert seen == ["Pärast"]


def test_a_refused_link_takes_the_whole_correction_back(specialist):
    first = factories.OrganisationFactory(name="Rahandusministeerium")
    matter = factories.MatterFactory(
        owner=specialist, title="Puutumata pealkiri", addressee_organisation=first
    )
    organisations = Organisation.objects.count()

    def refuse() -> None:
        raise DomainError(LINK_REFUSED)

    with refused(LINK_REFUSED):
        _edit(
            matter,
            specialist,
            title="Muudetud pealkiri",
            sender_name="Uus tundmatu asutus",
            save_procedural_link=refuse,
        )

    matter.refresh_from_db()
    assert matter.title == "Puutumata pealkiri"
    assert matter.addressee_organisation == first
    assert Organisation.objects.count() == organisations
