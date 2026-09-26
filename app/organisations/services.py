"""Named use cases for institutions.

Two operations, and both exist to answer the same question safely: *is this
institution already here?* Getting that wrong in either direction is expensive.
Answer "yes" too eagerly and two ministries merge into one, taking a decade of
filing with them. Answer "no" too eagerly and the register fills with four
spellings of the same body, none of which find each other in search.

So the comparison is **normalised exact** and nothing more: casefolded,
diacritics stripped, whitespace collapsed, and invisible format characters —
a pasted soft hyphen, a zero-width space, a byte-order mark — ignored
(`app.core.text.normalize_organisation_name`). That changes spelling, not
identity. Similarity scoring is deliberately absent — ``Keskkonnaministeerium``
and ``Kliimaministeerium`` score highly against each other and are different
institutions with different remits.

**One resolver, three answers** (ENG-045). A typed name that matches exactly one
institution *is* that institution; one that matches two is refused, because
which of them was meant is a question for a person and a third row would make
the ambiguity permanent; one that matches nothing is created. Every path that
turns a typed name into an Organisation — `Uus teema`, `Muuda teemat`, inline
Saatja, the closing recipients and the quick-create panel — answers through
:func:`get_or_create_organisation`, so none of them can disagree.

**And one decision at a time.** "Nothing matches, so create it" is a check and
then a write, and two people naming one new body at the same moment used to
both pass the check. The create decision is therefore taken under a
transaction-scoped PostgreSQL advisory lock keyed by the normalised name, and
the check is repeated once the lock is held. There is no unique constraint on
the name, deliberately: the catalogue may already hold rows that the stronger
key now calls one institution, and those are for a person to merge — reported
by ``manage.py check_organisation_duplicates``, never merged here.
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from django.db import connection, transaction

from app.core.errors import DomainError
from app.core.text import clean_single_line_name, normalize_organisation_name
from app.organisations.models import (
    AliasType,
    Organisation,
    OrganisationAlias,
    OrganisationType,
)
from app.organisations.reference_data import MINISTRIES, ReferenceOrganisation

#: The advisory-lock namespace for institution identity. The two-integer form
#: `app.search.indexing` uses, under a fixed first key of its own (search holds
#: 24601), so neither subsystem can ever take the other's lock.
IDENTITY_LOCK_NAMESPACE = 24602


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
    normalized = normalize_organisation_name(name)
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


def identity_lock_key(kind: str, value: str) -> int:
    """A stable signed 32-bit advisory-lock key for one identity.

    ``kind`` keeps a name and a registry code that happen to be spelled alike
    from sharing a key. A digest rather than :func:`hash`, which is salted per
    process — two workers would compute different keys for one name and the
    lock would serialise nothing. A collision between two different names only
    makes them wait for each other; it cannot make either wrong.
    """
    digest = hashlib.blake2b(f"{kind}\x00{value}".encode(), digest_size=4).digest()
    return int.from_bytes(digest, "big", signed=True)


def lock_identities(*, names: Sequence[str] = (), registry_codes: Sequence[str] = ()) -> None:
    """Hold the create decision for these identities until this transaction ends.

    ``names`` are raw names; each is locked under its organisation key, so two
    spellings of one body wait for each other. Taken in sorted key order, so two
    transactions that need the same set can never take it in opposite orders
    and deadlock. Re-entrant: a transaction already holding a key is granted it
    again at once, and PostgreSQL releases everything at commit or rollback.

    Outside a transaction a transaction-scoped lock is released as soon as it
    is taken, which would serialise nothing — so that is a programming error.
    """
    if not connection.in_atomic_block:  # pragma: no cover - programming error
        raise RuntimeError("lock_identities needs a transaction to hold the lock in.")
    keys = {
        identity_lock_key("name", key)
        for key in (normalize_organisation_name(name) for name in names)
        if key
    }
    keys |= {identity_lock_key("registry_code", code) for code in registry_codes if code}
    if not keys:
        return
    with connection.cursor() as cursor:
        for key in sorted(keys):
            cursor.execute("SELECT pg_advisory_xact_lock(%s, %s)", [IDENTITY_LOCK_NAMESPACE, key])


class AmbiguousOrganisation(DomainError):
    """One spelling names two institutions, and which was meant is not guessed.

    A :class:`~app.core.errors.DomainError`, so every caller that already
    answers a refusal with its message answers this one the same way.
    """

    def __init__(self, name: str) -> None:
        # It names what was typed and nothing else — no institution's details
        # and no Teema one of them is filed on, which may be one the person
        # cannot see.
        super().__init__(f"«{name}» sobib mitme organisatsiooniga — vali nimekirjast.")


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
    """The institution a typed name means: reuse one, refuse two, create none.

    The one resolver every counterparty field and the quick-create panel answer
    through (module docstring). Three outcomes and nothing else:

    * **one match — reuse.** A name that already names an institution, either
      canonically or through a recorded alias, *is* that institution.
    * **two or more matches — refuse.** The catalogue holds one spelling for two
      bodies. Creating a third would make that permanent and picking one would
      file the record against a body nobody named (§7D). This used to be the
      caller's job, and the quick-create panel did not do it: every click on a
      doubled name added another row (ENG-045).
    * **no match — create**, unless the registry code names an institution
      already, in which case that one is reused: the code is a real identifier,
      so a clash is a genuine duplicate rather than a coincidence of spelling.

    It never merges on similarity — a name only *approximately* like an
    existing one is a new row, which a person can merge later with the evidence
    in front of them. That is recoverable; a wrong merge is not.

    The "none" answer is decided under :func:`lock_identities` and checked again
    once the lock is held, so two saves naming one new body at the same moment
    leave one row and both get it. Runs inside the caller's transaction, and the
    lock is held until that transaction ends: a save refused after this point
    leaves no institution behind (§7E).
    """
    cleaned = clean_single_line_name(name)
    if not normalize_organisation_name(cleaned):
        raise DomainError("Organisatsioon vajab nime.")
    if organisation_type not in OrganisationType.values:
        raise DomainError(f"Tundmatu organisatsiooni tüüp {organisation_type!r}.")
    code = (registry_code or "").strip()

    matches = find_matches(cleaned)
    if not matches:
        # Nothing yet — which is exactly the answer two concurrent saves can
        # both read. Decide it again under the lock; the second one in finds
        # the first one's row.
        lock_identities(names=[cleaned], registry_codes=[code])
        matches = find_matches(cleaned)
    if len(matches) > 1:
        raise AmbiguousOrganisation(cleaned)
    if matches:
        return OrganisationResult(organisation=matches[0], created=False)

    if code:
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

    The single-name half of :func:`resolve_recipients`, so that every
    counterparty field which accepts a typed name answers it the same way —
    through :func:`get_or_create_organisation`, which reuses one match, refuses
    two and creates none, under the lock that keeps two simultaneous saves from
    creating one body twice.

    A blank name is not an error — it is the absence of an answer — so it
    returns ``None`` and the caller keeps whatever it already had. A name made
    only of invisible characters is blank in every sense a reader can see.

    Runs inside the caller's transaction. A save refused after this point leaves
    no institution behind (§7E).
    """
    cleaned = clean_single_line_name(name)
    if not normalize_organisation_name(cleaned):
        return None
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

    Every typed name's identity lock is taken up front, in one sorted pass.
    Resolving them one by one would lock them in the order they were typed, and
    two letters naming the same two new bodies in opposite orders would each
    hold one lock and wait for the other.

    Everything here runs inside the caller's transaction. A closure that is
    refused after this point leaves no institutions behind (§7E).
    """
    resolved: list[Organisation] = []
    seen: set[Any] = set()

    for organisation in chosen:
        if organisation.pk not in seen:
            seen.add(organisation.pk)
            resolved.append(organisation)

    lock_identities(names=[clean_single_line_name(raw) for raw in typed_names])
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
    #: Reference names the catalogue already holds under two rows. Neither is
    #: topped up and no third is created; which one is the ministry is a
    #: question for a person (`manage.py check_organisation_duplicates`).
    ambiguous: list[str] = field(default_factory=list)

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

    Through the one resolver, so a reference name the catalogue holds twice is
    reported rather than seeded a third time.
    """
    result = ReferenceSeedResult(created=[], existing=[], aliases_added=0)

    for entry in entries:
        try:
            outcome = get_or_create_organisation(
                name=entry.name, organisation_type=entry.organisation_type
            )
        except AmbiguousOrganisation:
            result.ambiguous.append(entry.name)
            continue
        organisation = outcome.organisation
        if outcome.created:
            result.created.append(entry.name)
        else:
            result.existing.append(entry.name)

        for alias in entry.aliases:
            normalized = normalize_organisation_name(alias)
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
