"""Named use cases for institutions.

Two operations, and both exist to answer the same question safely: *is this
institution already here?* Getting that wrong in either direction is expensive.
Answer "yes" too eagerly and two ministries merge into one, taking a decade of
filing with them. Answer "no" too eagerly and the register fills with four
spellings of the same body, none of which find each other in search.

So the comparison is **normalised exact** and nothing more: casefolded,
diacritics stripped, whitespace collapsed. That changes spelling, not identity.
Similarity scoring is deliberately absent — ``Keskkonnaministeerium`` and
``Kliimaministeerium`` score highly against each other and are different
institutions with different remits.
"""

from __future__ import annotations

from dataclasses import dataclass

from django.db import transaction

from app.core.errors import DomainError
from app.core.text import normalize_for_matching
from app.organisations.models import (
    AliasType,
    Organisation,
    OrganisationAlias,
    OrganisationType,
)
from app.organisations.reference_data import MINISTRIES, ReferenceOrganisation


def find_exact(name: str) -> Organisation | None:
    """The one institution this name unambiguously refers to, or nothing.

    Checks canonical names first, then recorded aliases. An alias match is not
    fuzzy matching: somebody decided that ``MKM`` means that ministry, and this
    is reading their decision.

    Returns ``None`` when two rows match, because "which of these did you mean"
    is a question for a person.
    """
    normalized = normalize_for_matching(name)
    if not normalized:
        return None

    exact = list(Organisation.objects.filter(normalized_name=normalized)[:2])
    if len(exact) == 1:
        return exact[0]
    if len(exact) > 1:
        return None

    aliased = list(Organisation.objects.filter(aliases__normalized_alias=normalized).distinct()[:2])
    return aliased[0] if len(aliased) == 1 else None


@dataclass(frozen=True)
class OrganisationResult:
    organisation: Organisation
    created: bool


@transaction.atomic
def get_or_create_organisation(
    *,
    name: str,
    organisation_type: str = OrganisationType.OTHER,
    registry_code: str = "",
) -> OrganisationResult:
    """Return the institution this name means, creating it only if it is new.

    The quick-create path behind every counterparty field. It reuses an exact
    match rather than creating a near-duplicate, and it never merges anything on
    similarity — if the name is only *approximately* like an existing one, a new
    row is created and a person can merge them later with the evidence in front
    of them. That is recoverable; a wrong merge is not.
    """
    cleaned = " ".join((name or "").split())
    if not cleaned:
        raise DomainError("Organisatsioon vajab nime.")
    if organisation_type not in OrganisationType.values:
        raise DomainError(f"Tundmatu organisatsiooni tüüp {organisation_type!r}.")

    existing = find_exact(cleaned)
    if existing is not None:
        return OrganisationResult(organisation=existing, created=False)

    code = (registry_code or "").strip()
    if code:
        # The registry code is a real identifier, so a clash is a genuine
        # duplicate rather than a coincidence of spelling.
        by_code = Organisation.objects.filter(registry_code=code).first()
        if by_code is not None:
            return OrganisationResult(organisation=by_code, created=False)

    organisation = Organisation.objects.create(
        name=cleaned, organisation_type=organisation_type, registry_code=code
    )
    return OrganisationResult(organisation=organisation, created=True)


@dataclass
class ReferenceSeedResult:
    created: list[str]
    existing: list[str]
    aliases_added: int

    @property
    def total(self) -> int:
        return len(self.created) + len(self.existing)


@transaction.atomic
def seed_reference_organisations(
    entries: tuple[ReferenceOrganisation, ...] = MINISTRIES,
) -> ReferenceSeedResult:
    """Add public institutions that are missing. Never change ones that exist.

    Idempotent by normalised name, so running it twice adds nothing. Aliases are
    topped up for rows that already exist, because an alias is additive
    information and adding one cannot lose anything — unlike a name, a type or a
    validity date, none of which this touches on an existing row.
    """
    result = ReferenceSeedResult(created=[], existing=[], aliases_added=0)

    for entry in entries:
        organisation = find_exact(entry.name)
        if organisation is None:
            organisation = Organisation.objects.create(
                name=entry.name, organisation_type=entry.organisation_type
            )
            result.created.append(entry.name)
        else:
            result.existing.append(entry.name)

        for alias in entry.aliases:
            normalized = normalize_for_matching(alias)
            # Skip an alias that already points at a *different* institution:
            # silently moving it would rewrite somebody's earlier decision.
            claimed_elsewhere = (
                OrganisationAlias.objects.filter(normalized_alias=normalized)
                .exclude(organisation=organisation)
                .exists()
            )
            if claimed_elsewhere:
                continue
            _, created = OrganisationAlias.objects.get_or_create(
                organisation=organisation,
                normalized_alias=normalized,
                defaults={"alias": alias, "alias_type": AliasType.ABBREVIATION},
            )
            result.aliases_added += int(created)

    return result
