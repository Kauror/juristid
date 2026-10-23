"""Haldus → Klassifikaatorid: deliberate edits to the institution catalogue.

`app.organisations.services` answers *what does this typed name mean* for every
counterparty field, and it reuses an existing institution whenever it can. This
module is the other half — a person whose job is the catalogue adding a body,
correcting a name or recording another spelling — and its answers are stricter
for exactly that reason: where the resolver *reuses* a match, these *refuse* it,
because somebody adding an institution by hand who is told "that already exists"
has learnt something, and silently being handed the other row would teach them
nothing (docs/adr/0187).

Five rules every function here keeps:

* **Identity is normalised-exact, never similar.** The same key the resolver
  matches on (`normalize_organisation_name`), and the same advisory lock, so a
  catalogue edit and a Teema save naming one new body cannot both create it.
* **A name is never lost.** A corrected name keeps the old one as a
  `HISTORICAL_NAME` spelling unless the person says it was simply a typo, so a
  search for what a letter was addressed to in 2019 still finds it. A
  historical name, once recorded, is not removed here.
* **The reviewed baseline is not edited here.** The ministries, Riigikogu and
  its committees arrive from `app/organisations/reference_data.py` through a
  reviewed plan (docs/adr/0029). Renaming one, or removing one of its reviewed
  spellings, is refused with a sentence saying it needs a reviewed change —
  `verify_reference_data` would otherwise report the baseline broken on the
  next deployment.
* **Stale pages are refused.** Every change names the version of the row its
  page was drawn from (`updated_at`, the token every optimistic-concurrency seam
  in this product uses) and compares it under the row lock.
* **Search freshness follows from the save.** Renaming an institution and every
  alias write already record a durable rebuild obligation in
  `app.search.signals` (SEARCH-001); these functions save through the model so
  those handlers run, and never through `QuerySet.update()`.

Nothing here merges two institutions or switches one off. The catalogue has no
supported semantics for either — no `is_active`, no merge pointer, and
`valid_to` is a fact about the body rather than a switch on the row — so the
pages show what points at each row and leave the decision to a reviewed change
(docs/adr/0187 §5).
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import cache
from typing import Any

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q, QuerySet
from django.utils.text import capfirst

from app.audit.enums import ChangeEventType
from app.audit.services import record_change_event
from app.core.errors import DomainError
from app.core.text import clean_single_line_name, normalize_organisation_name
from app.organisations.duplicates import references_to
from app.organisations.models import (
    AliasType,
    Organisation,
    OrganisationAlias,
    OrganisationType,
)
from app.organisations.reference_data import PUBLIC_REFERENCE_ORGANISATIONS
from app.organisations.services import find_matches, lock_identities

NAME_REQUIRED = "Organisatsioon vajab nime."
ALIAS_REQUIRED = "Nimekuju ei tohi olla tühi."
ALIAS_NOT_FOUND = "Nimekuju ei leitud."
UNKNOWN_TYPE = "Tundmatu organisatsiooni tüüp."
STALE = "Seda organisatsiooni muudeti vahepeal. Ava leht uuesti ja vaata muudatus üle."
NAME_UNCHANGED = "Nimi on juba selline."
ALIAS_IS_THE_NAME = "See nimekuju on organisatsiooni enda nimi."
ALIAS_ALREADY_HERE = "See nimekuju on organisatsioonil juba olemas."
HISTORICAL_NAME_KEPT = "Ajaloolist nime ei eemaldata: selle järgi leitakse vanad kirjad ja teemad."
REVIEWED_RENAME = (
    "See on kinnitatud alusandmestiku organisatsioon. Nime muutmine vajab "
    "arendaja kaudu ülevaadatud muudatust (alusandmestik ja selle plaan), mitte "
    "muutmist siin."
)
REVIEWED_ALIAS = (
    "See nimekuju kuulub kinnitatud alusandmestikku. Selle eemaldamine vajab "
    "arendaja kaudu ülevaadatud muudatust."
)


def duplicate_refusal(existing: Organisation) -> str:
    """The refusal for a name that already means an institution.

    It names the institution, which is reference data and visible to whoever
    may reach this page, and nothing about any Teema filed against it.
    """
    return f"Nimi on juba kasutusel: «{existing.name}». Lisa see vajadusel nimekujuna."


def registry_code_refusal(existing: Organisation) -> str:
    return f"Registrikood on juba organisatsioonil «{existing.name}»."


def organisation_revision(organisation: Organisation) -> str:
    """Which version of the row a page was drawn from."""
    return organisation.updated_at.isoformat()


# -- reading -----------------------------------------------------------------------------


def search_organisations(query: str, *, limit: int = 50) -> QuerySet[Organisation]:
    """Institutions whose name, a recorded spelling or registry code contains the text.

    Folded with the catalogue's own key, so `Pollumajandus` finds
    `Põllumajandus` and a pasted soft hyphen finds nothing it should not. A
    *search* rather than a resolution: containment is right for finding a row
    to open, and wrong for deciding identity, which is why nothing here creates
    or refuses on it.
    """
    rows = Organisation.objects.all()
    folded = normalize_organisation_name(query or "")
    if folded:
        code = (query or "").strip()
        rows = rows.filter(
            Q(normalized_name__contains=folded)
            | Q(aliases__normalized_alias__contains=folded)
            | Q(registry_code=code)
        ).distinct()
    return rows.order_by("name")[:limit]


@cache
def _reviewed_keys() -> tuple[frozenset[str], dict[str, frozenset[str]]]:
    """The baseline's names, and each name's reviewed spellings, as keys."""
    names: set[str] = set()
    aliases: dict[str, frozenset[str]] = {}
    for entry in PUBLIC_REFERENCE_ORGANISATIONS:
        key = normalize_organisation_name(entry.name)
        names.add(key)
        aliases[key] = frozenset(normalize_organisation_name(alias) for alias in entry.aliases)
    return frozenset(names), aliases


def reviewed_entry_key(organisation: Organisation) -> str:
    """The baseline entry this row *is*, by name or by a recorded spelling; or ``""``.

    The same two ways `verify_reference_data` finds an entry, so a row the
    deployment check counts as a reviewed institution is one this module
    protects.
    """
    names, _ = _reviewed_keys()
    if organisation.normalized_name in names:
        return organisation.normalized_name
    for alias in organisation.aliases.all():
        if alias.normalized_alias in names:
            return alias.normalized_alias
    return ""


def is_reviewed_alias(alias: OrganisationAlias) -> bool:
    key = reviewed_entry_key(alias.organisation)
    if not key:
        return False
    _, aliases = _reviewed_keys()
    return alias.normalized_alias in aliases.get(key, frozenset()) or alias.normalized_alias == key


@dataclass(frozen=True)
class OrganisationReferences:
    """What points at one institution, by relation, for the page and the decision."""

    #: ``"Model.field" -> count``, the model graph's own keys.
    by_relation: dict[str, int]

    @property
    def total(self) -> int:
        return sum(self.by_relation.values())

    @property
    def labelled(self) -> list[tuple[str, int]]:
        """``(Estonian label, count)`` per relation — what the page prints."""
        return sorted(
            ((relation_label(key), count) for key, count in self.by_relation.items()),
            key=lambda row: row[0],
        )


#: The relations a person reads by name. Explicit where the model's own
#: verbose names would read oddly; anything added later falls back to them.
RELATION_LABELS: dict[str, str] = {
    "Organisation.predecessor": "Järglased (selle asutuse eelkäijana)",
    "Matter.addressee_organisation": "Teemad, kus see on adressaat",
    "MatterSourceOrganisation.organisation": "Teemad, kus see on saatja",
    "Entry.organisation": "Sissekanded",
    "MatterExternalPosition.organisation": "Välised seisukohad",
    "SubmissionRecipient.organisation": "Arvamused, mille saaja see on",
    "SubmissionJointSubmitter.organisation": "Arvamused, mille kaasesitaja see on",
}


def relation_label(key: str) -> str:
    """The Estonian label of one model-graph key; never the key itself."""
    if key in RELATION_LABELS:
        return RELATION_LABELS[key]
    for relation in Organisation._meta.related_objects:
        if f"{relation.related_model.__name__}.{relation.field.name}" == key:
            plural = str(relation.related_model._meta.verbose_name_plural)
            return f"{capfirst(plural)}: {relation.field.verbose_name}"
    return "Muud kirjed"


def organisation_references(organisation: Organisation) -> OrganisationReferences:
    """Every record that names this institution, counted per relation.

    Read from the model graph (`duplicates.references_to`), so a relation added
    later is counted without anybody remembering to add it here.
    """
    counts = references_to({organisation.pk}).get(organisation.pk, {})
    return OrganisationReferences(by_relation=dict(sorted(counts.items())))


# -- writing -----------------------------------------------------------------------------


def _locked(organisation: Any) -> Organisation:
    # `no_key=True`: every Teema and opinion that names the institution holds a
    # foreign key to it, and a plain `FOR UPDATE` here would wait on — and be
    # waited on by — every one of their saves (app/matters/locks.py).
    try:
        return Organisation.objects.select_for_update(no_key=True).get(
            pk=getattr(organisation, "pk", organisation)
        )
    except Organisation.DoesNotExist:
        raise DomainError("Organisatsiooni ei leitud.") from None


def _check_revision(locked: Organisation, expected_revision: str | None) -> None:
    if expected_revision is not None and organisation_revision(locked) != expected_revision:
        raise DomainError(STALE)


def _claimed_by_another(key: str, organisation: Organisation | None) -> Organisation | None:
    """Another institution this key already means, by its name or by a spelling."""
    by_name = Organisation.objects.filter(normalized_name=key)
    by_alias = Organisation.objects.filter(aliases__normalized_alias=key)
    if organisation is not None:
        by_name = by_name.exclude(pk=organisation.pk)
        by_alias = by_alias.exclude(pk=organisation.pk)
    return by_name.first() or by_alias.distinct().first()


@transaction.atomic
def create_organisation(
    *, actor: Any, name: str, organisation_type: str = "", registry_code: str = ""
) -> Organisation:
    """Add an institution to the catalogue — and refuse one that is already in it.

    Refused when the name already names an institution, canonically or as a
    recorded spelling, and when the registry code is already somebody's: a
    person adding a body by hand is told which row they meant instead of being
    given a second one. Decided again under the identity lock, so a Teema save
    creating the same body at the same moment cannot slip a row in between.
    """
    cleaned = clean_single_line_name(name)
    key = normalize_organisation_name(cleaned)
    if not key:
        raise DomainError(NAME_REQUIRED)
    kind = organisation_type or OrganisationType.OTHER
    if kind not in OrganisationType.values:
        raise DomainError(UNKNOWN_TYPE)
    code = (registry_code or "").strip()

    lock_identities(names=[cleaned], registry_codes=[code])
    matches = find_matches(cleaned)
    if matches:
        raise DomainError(duplicate_refusal(matches[0]))
    if code:
        by_code = Organisation.objects.filter(registry_code=code).first()
        if by_code is not None:
            raise DomainError(registry_code_refusal(by_code))

    organisation = Organisation.objects.create(
        name=cleaned, organisation_type=kind, registry_code=code
    )
    record_change_event(
        event_type=ChangeEventType.ORGANISATION_CREATED,
        actor=actor,
        obj=organisation,
        summary=organisation.name[:200],
        payload={
            "organisation": str(organisation.pk),
            "name": organisation.name,
            "organisation_type": organisation.organisation_type,
            "registry_code": organisation.registry_code,
        },
    )
    return organisation


@dataclass(frozen=True)
class RenameResult:
    organisation: Organisation
    #: The old name, recorded as a historical spelling; ``None`` when it was not.
    historical_alias: OrganisationAlias | None


@transaction.atomic
def rename_organisation(
    *,
    actor: Any,
    organisation: Any,
    name: str,
    expected_revision: str | None,
    keep_old_name: bool = True,
) -> RenameResult:
    """Correct an institution's canonical name, keeping the old one findable.

    ``keep_old_name`` records the previous name as a `HISTORICAL_NAME` spelling,
    which is what keeps every letter addressed to it findable by what it was
    called then (the alias is indexed beside the name, `_alias_text_for`). A
    person correcting a plain typo may say no; a rename that only changes case
    or spacing folds to the same key and records nothing either way.

    Refused for a reviewed baseline institution, for a name another institution
    already answers to, and for a page drawn from an older version of the row.
    The save goes through the model, so `app.search.signals` records the
    `ORGANISATION_RENAMED` rebuild obligation in this transaction.
    """
    cleaned = clean_single_line_name(name)
    key = normalize_organisation_name(cleaned)
    if not key:
        raise DomainError(NAME_REQUIRED)

    locked = _locked(organisation)
    _check_revision(locked, expected_revision)
    if reviewed_entry_key(locked):
        raise DomainError(REVIEWED_RENAME)
    if cleaned == locked.name:
        raise DomainError(NAME_UNCHANGED)

    lock_identities(names=[cleaned, locked.name])
    other = _claimed_by_another(key, locked)
    if other is not None:
        raise DomainError(duplicate_refusal(other))

    old_name = locked.name
    old_key = locked.normalized_name
    locked.name = cleaned
    locked.save(update_fields=["name", "updated_at"])

    historical: OrganisationAlias | None = None
    if keep_old_name and old_key != locked.normalized_name:
        already = locked.aliases.filter(normalized_alias=old_key).first()
        if already is None and _claimed_by_another(old_key, locked) is None:
            historical = OrganisationAlias.objects.create(
                organisation=locked, alias=old_name, alias_type=AliasType.HISTORICAL_NAME
            )

    record_change_event(
        event_type=ChangeEventType.ORGANISATION_RENAMED,
        actor=actor,
        obj=locked,
        summary=f"{old_name} → {locked.name}"[:200],
        payload={
            "organisation": str(locked.pk),
            "from": old_name,
            "to": locked.name,
            "historical_alias": str(historical.pk) if historical else None,
        },
    )
    return RenameResult(organisation=locked, historical_alias=historical)


@transaction.atomic
def add_organisation_alias(
    *,
    actor: Any,
    organisation: Any,
    alias: str,
    alias_type: str = AliasType.SYNONYM,
    expected_revision: str | None = None,
) -> OrganisationAlias:
    """Record another spelling an institution is known by.

    Refused when the spelling is the institution's own name or already one of
    its spellings, and — the duplicate check — when it already names *another*
    institution: one key meaning two bodies is exactly the ambiguity the
    resolver refuses to guess at, and this is where it would be created.
    """
    cleaned = clean_single_line_name(alias)
    key = normalize_organisation_name(cleaned)
    if not key:
        raise DomainError(ALIAS_REQUIRED)
    if alias_type not in AliasType.values:
        raise DomainError("Tundmatu nimekuju tüüp.")

    locked = _locked(organisation)
    _check_revision(locked, expected_revision)
    if key == locked.normalized_name:
        raise DomainError(ALIAS_IS_THE_NAME)
    if locked.aliases.filter(normalized_alias=key).exists():
        raise DomainError(ALIAS_ALREADY_HERE)

    lock_identities(names=[cleaned])
    other = _claimed_by_another(key, locked)
    if other is not None:
        raise DomainError(duplicate_refusal(other))

    created = OrganisationAlias.objects.create(
        organisation=locked, alias=cleaned, alias_type=alias_type
    )
    # The page's revision token is the institution's row, so a second tab
    # holding the page from before this spelling is told so.
    locked.save(update_fields=["updated_at"])
    record_change_event(
        event_type=ChangeEventType.ORGANISATION_ALIAS_ADDED,
        actor=actor,
        obj=locked,
        summary=f"{locked.name} ← {created.alias}"[:200],
        payload={
            "organisation": str(locked.pk),
            "alias": created.alias,
            "alias_id": str(created.pk),
            "alias_type": created.alias_type,
        },
    )
    return created


@transaction.atomic
def remove_organisation_alias(
    *, actor: Any, alias: Any, expected_revision: str | None = None
) -> Organisation:
    """Remove a spelling somebody recorded by mistake.

    Refused for a historical name — removing it would make old correspondence
    unfindable by what the body was called — and for a spelling the reviewed
    baseline carries. Deleting the row through the model records the
    `ORGANISATION_ALIAS_CHANGED` rebuild obligation (`app.search.signals`).
    """
    alias_pk = getattr(alias, "pk", alias)
    try:
        organisation_id = OrganisationAlias.objects.values_list("organisation_id", flat=True).get(
            pk=alias_pk
        )
    except (OrganisationAlias.DoesNotExist, ValueError, ValidationError):
        raise DomainError(ALIAS_NOT_FOUND) from None
    # The institution first, then the spelling read again under its lock: two
    # removals of one spelling are serialised here, and the second finds it
    # gone instead of deleting nothing and auditing a removal anyway.
    locked = _locked(organisation_id)
    row = OrganisationAlias.objects.filter(pk=alias_pk, organisation=locked).first()
    if row is None:
        raise DomainError(ALIAS_NOT_FOUND)
    _check_revision(locked, expected_revision)
    if row.alias_type == AliasType.HISTORICAL_NAME:
        raise DomainError(HISTORICAL_NAME_KEPT)
    if is_reviewed_alias(row):
        raise DomainError(REVIEWED_ALIAS)

    payload = {
        "organisation": str(locked.pk),
        "alias": row.alias,
        "alias_id": str(row.pk),
        "alias_type": row.alias_type,
    }
    row.delete()
    locked.save(update_fields=["updated_at"])
    record_change_event(
        event_type=ChangeEventType.ORGANISATION_ALIAS_REMOVED,
        actor=actor,
        obj=locked,
        summary=f"{locked.name} ← {payload['alias']}"[:200],
        payload=payload,
    )
    return locked
