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

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

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


def find_matches(name: str) -> list[Organisation]:
    """Every institution this exact name resolves to. Usually none or one.

    Canonical names first, then recorded aliases — an alias match is not fuzzy
    matching: somebody decided that ``MKM`` means that ministry, and this is
    reading their decision.

    Two rows come back only when the catalogue genuinely holds two institutions
    under one spelling, and that is the case callers must not resolve on their
    own. :func:`find_exact` answers ``None`` there and a *creating* caller must
    not read that as "so it is new" — a third row spelled the same way makes the
    ambiguity permanent (Teema closing redesign §7D).
    """
    normalized = normalize_for_matching(name)
    if not normalized:
        return []

    exact = list(Organisation.objects.filter(normalized_name=normalized)[:2])
    if exact:
        return exact
    return list(Organisation.objects.filter(aliases__normalized_alias=normalized).distinct()[:2])


def find_exact(name: str) -> Organisation | None:
    """The one institution this name unambiguously refers to, or nothing.

    Returns ``None`` when two rows match, because "which of these did you mean"
    is a question for a person.
    """
    matches = find_matches(name)
    return matches[0] if len(matches) == 1 else None


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


@transaction.atomic
def resolve_organisation_name(
    *, name: str, organisation_type: str = OrganisationType.OTHER
) -> Organisation | None:
    """The one institution a typed name means, creating it only if it is new.

    The single-name half of :func:`resolve_recipients`, extracted so that every
    counterparty field which accepts a typed name answers it the same way. Three
    outcomes and nothing else:

    * **one match — reuse.** A name that already names an institution, either
      canonically or through a recorded alias, *is* that institution. Nothing is
      created.
    * **no match — create.** A genuinely new body becomes a new row, through
      :func:`get_or_create_organisation`, so the quick-create rules — the
      trimmed name, the registry-code check — are the ones that already apply
      everywhere else.
    * **two or more matches — refuse.** ``find_matches`` returning two rows means
      the catalogue holds one spelling for two bodies, and that is a question for
      a person. Creating a third would make the ambiguity permanent and picking
      one would file the record against a body nobody named (§7D).

    That third case is why callers must not reach for
    :func:`get_or_create_organisation` directly with a name a person typed.
    ``find_exact`` answers ``None`` both for "nothing matches" and for "two
    things match", and a creating caller that reads the second as the first
    writes the duplicate this function exists to refuse.

    A blank name is not an error — it is the absence of an answer — so it
    returns ``None`` and the caller keeps whatever it already had.

    Runs inside the caller's transaction. A save refused after this point leaves
    no institution behind (§7E).
    """
    cleaned = " ".join((name or "").split())
    if not cleaned:
        return None

    matches = find_matches(cleaned)
    if len(matches) > 1:
        raise DomainError(f"«{cleaned}» sobib mitme organisatsiooniga — vali nimekirjast.")
    if matches:
        return matches[0]
    return get_or_create_organisation(
        name=cleaned, organisation_type=organisation_type
    ).organisation


@transaction.atomic
def resolve_recipients(
    *, chosen: Sequence[Organisation], typed_names: Sequence[str]
) -> list[Organisation]:
    """The recipient set of one letter, from a shortlist and from typed names.

    The closing composer lets somebody tick the bodies Koda usually writes to
    and type the ones it does not — seven political parties on one opinion is a
    real case, and creating them somewhere else first is not a workflow anybody
    would use (Teema closing redesign §7).

    Two rules do the work here, and both are about identity rather than
    convenience:

    * **Exact normalised identity reuses, everything else creates.** A typed
      name that already names an institution — canonically or through a
      recorded alias — *is* that institution. Anything merely similar becomes
      its own row, because a wrong merge takes a decade of filing with it and a
      duplicate does not (module docstring).
    * **Ambiguity is refused, never guessed.** ``find_matches`` returning two
      rows means the catalogue holds one spelling for two bodies. Creating a
      third would make that permanent, and picking one would file the letter
      against a body nobody named.

    Ordering is the order somebody entered, ticked first. Duplicates collapse:
    the same body reached twice — twice typed, or ticked and then typed — is one
    recipient, which is also what ``SubmissionRecipient`` enforces.

    Everything here runs inside the caller's transaction. A closure that is
    refused after this point leaves no institutions behind (§7E).
    """
    resolved: list[Organisation] = []
    seen: set[Any] = set()

    for organisation in chosen:
        if organisation.pk not in seen:
            seen.add(organisation.pk)
            resolved.append(organisation)

    for raw in typed_names:
        # One definition of "what does this typed name mean", shared with every
        # other counterparty field that accepts one. Reuse, create or refuse —
        # written once, above, rather than restated per caller.
        typed = resolve_organisation_name(name=raw)
        if typed is None:
            continue
        if typed.pk in seen:
            continue
        seen.add(typed.pk)
        resolved.append(typed)

    return resolved


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
