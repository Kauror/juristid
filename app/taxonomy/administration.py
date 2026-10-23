"""Haldus → Klassifikaatorid: governing the `Silt` vocabulary.

Master specification 14.7 gives tags one accountable owner, and creation, merge
and deprecation "through admin/governance". Until now that was Django's own
admin, which no role reaches and which would let somebody delete a row or point
`merged_into` in a circle. This module is the governed path, and the semantics
it uses are the ones the model already has — nothing new is invented:

* **Merging** is `Tag.merged_into` plus `is_active=False` (the model's own check
  constraint insists on both). Assignments are **not moved**: a Teema or an
  opinion classified under the old word keeps that classification, exactly as
  `set_tags` and `set_submission_tags` keep a historical assignment
  (docs/adr/0093 §1). Search follows the merge instead — the merged tag's
  Matters are reprojected in this transaction by
  `app.search.signals.refresh_on_tag_merge`, so they become findable through the
  tag that replaced it.
* **Deactivating** is `is_active=False` and `deprecated_at`. The word stays on
  every record that carries it and stops being offered for new ones
  (`submissions.links`, the reporting filter); it can be reactivated, unless it
  was merged away — a merge is undone by a reviewed change, not by a toggle.
* **Nothing is deleted.** A tag a record carries is held by `PROTECT`, and one
  nothing carries is still somebody's earlier decision; retiring it says so.
* **Identity is folded, never similar.** A name or spelling that folds
  (`normalize_for_matching`) to another tag's name or spelling is refused, so
  the vocabulary cannot acquire two entries that a search treats as one.
* **Stale pages are refused**, by `updated_at` under the row lock, and every
  name change saves through the model so the `TAG_RENAMED` / `TAG_ALIAS_CHANGED`
  rebuild obligations in `app.search.signals` are recorded (SEARCH-001).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q, QuerySet
from django.utils import timezone

from app.audit.enums import ChangeEventType
from app.audit.services import record_change_event
from app.core.errors import DomainError
from app.core.text import clean_single_line_name, normalize_for_matching
from app.taxonomy.models import Tag, TagAlias

NAME_REQUIRED = "Silt vajab nime."
KEY_INVALID = "Võti võib sisaldada ainult väiketähti, numbreid ja sidekriipsu (kuni 64 märki)."
KEY_TAKEN = "Selle võtmega silt on juba olemas."
ALIAS_REQUIRED = "Nimekuju ei tohi olla tühi."
ALIAS_IS_THE_NAME = "See nimekuju on sildi enda nimi."
ALIAS_ALREADY_HERE = "See nimekuju on sildil juba olemas."
STALE = "Seda silti muudeti vahepeal. Ava leht uuesti ja vaata muudatus üle."
MERGED_IS_FROZEN = (
    "Liidetud silti ei muudeta: see elab edasi selle sildi kaudu, millega see liideti."
)
MERGE_INTO_ITSELF = "Silti ei saa liita iseendaga."
MERGE_TARGET_RETIRED = "Sihtsilt peab olema kasutusel ja mitte ise liidetud."
ALREADY_INACTIVE = "Silt on juba aegunuks märgitud."
ALREADY_ACTIVE = "Silt on juba kasutusel."

_KEY = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


def duplicate_refusal(existing: Tag) -> str:
    return f"Sama nimi või nimekuju on juba sildil «{existing.name_et}»."


def tag_revision(tag: Tag) -> str:
    return tag.updated_at.isoformat()


# -- reading -----------------------------------------------------------------------------


def search_tags(query: str = "", *, include_retired: bool = True) -> QuerySet[Tag]:
    """Tags whose name, key or a recorded spelling contains the folded text."""
    rows = Tag.objects.select_related("merged_into")
    if not include_retired:
        rows = rows.filter(is_active=True)
    folded = normalize_for_matching(query or "")
    if folded:
        rows = rows.filter(
            Q(name_et__icontains=query.strip())
            | Q(key__icontains=folded)
            | Q(aliases__normalized_alias__contains=folded)
        ).distinct()
    return rows.order_by("-is_active", "name_et")


@dataclass(frozen=True)
class TagUsage:
    matters: int
    submissions: int
    merged_from: int

    @property
    def total(self) -> int:
        return self.matters + self.submissions + self.merged_from


def tag_usage(tag: Tag) -> TagUsage:
    """How many records carry this tag directly, and how many tags were merged into it."""
    return TagUsage(
        matters=tag.assignments.count(),
        submissions=tag.submission_assignments.count(),
        merged_from=tag.merged_from.count(),
    )


def _claimed_by_another(key: str, tag: Tag | None) -> Tag | None:
    """Another tag this folded key is already the name or a spelling of.

    The vocabulary is small — tens of rows, not thousands — so names are folded
    here rather than stored folded; the alias column is already folded.
    """
    others = Tag.objects.only("pk", "name_et")
    aliases = TagAlias.objects.filter(normalized_alias=key).select_related("tag")
    if tag is not None:
        others = others.exclude(pk=tag.pk)
        aliases = aliases.exclude(tag_id=tag.pk)
    for other in others:
        if normalize_for_matching(other.name_et) == key:
            return other
    alias = aliases.first()
    return alias.tag if alias is not None else None


# -- writing -----------------------------------------------------------------------------


def _locked(tag: Any) -> Tag:
    # `no_key=True`: every assignment holds a foreign key to this row.
    try:
        return Tag.objects.select_for_update(no_key=True).get(pk=getattr(tag, "pk", tag))
    except (Tag.DoesNotExist, ValueError, ValidationError):
        raise DomainError("Silti ei leitud.") from None


def _check_revision(locked: Tag, expected_revision: str | None) -> None:
    if expected_revision is not None and tag_revision(locked) != expected_revision:
        raise DomainError(STALE)


#: The advisory-lock namespace for vocabulary decisions: beside institution
#: identity (24602) and search (24601), never sharing either's keys.
VOCABULARY_LOCK_NAMESPACE = 24603


def _vocabulary_lock() -> None:
    """Serialise every governed change to the vocabulary, for this transaction.

    Two people adding «VKE» and «vke» at the same moment would each pass the
    folded check. The vocabulary is small and edited rarely, so one
    transaction-scoped advisory lock covers all of it — taken first, before any
    row lock, by every writer in this module, so no two of them can wait on
    each other in opposite orders. Readers and Teema saves never take it.
    """
    from django.db import connection

    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_advisory_xact_lock(%s, 0)", [VOCABULARY_LOCK_NAMESPACE])


@transaction.atomic
def create_tag(*, actor: Any, key: str, name: str, definition: str = "") -> Tag:
    """Add a word to the vocabulary, refusing one it already has under any spelling."""
    cleaned = clean_single_line_name(name)
    folded = normalize_for_matching(cleaned)
    if not folded:
        raise DomainError(NAME_REQUIRED)
    slug = (key or "").strip()
    if len(slug) > 64 or not _KEY.match(slug):
        raise DomainError(KEY_INVALID)

    _vocabulary_lock()
    if Tag.objects.filter(key=slug).exists():
        raise DomainError(KEY_TAKEN)
    other = _claimed_by_another(folded, None)
    if other is not None:
        raise DomainError(duplicate_refusal(other))

    tag = Tag.objects.create(
        key=slug, name_et=cleaned, definition=(definition or "").strip(), owned_by=actor
    )
    record_change_event(
        event_type=ChangeEventType.TAG_CREATED,
        actor=actor,
        obj=tag,
        summary=tag.name_et[:200],
        payload={"tag": str(tag.pk), "key": tag.key, "name": tag.name_et},
    )
    return tag


@transaction.atomic
def update_tag(
    *,
    actor: Any,
    tag: Any,
    name: str,
    definition: str,
    expected_revision: str | None,
    keep_old_name: bool = True,
) -> Tag:
    """Correct a tag's name or its usage guidance.

    A changed name keeps the old one as a spelling unless the person says it
    was a typo — the same promise the institution catalogue makes — so a search
    for the word people used last year still finds what was filed under it.
    The key never changes: it is what reporting filters and exports name.
    """
    cleaned = clean_single_line_name(name)
    folded = normalize_for_matching(cleaned)
    if not folded:
        raise DomainError(NAME_REQUIRED)

    _vocabulary_lock()
    locked = _locked(tag)
    _check_revision(locked, expected_revision)
    if locked.merged_into_id is not None:
        raise DomainError(MERGED_IS_FROZEN)

    new_definition = (definition or "").strip()
    changed: dict[str, dict[str, str]] = {}
    old_name = locked.name_et
    old_folded = normalize_for_matching(old_name)
    if cleaned != old_name:
        other = _claimed_by_another(folded, locked)
        if other is not None:
            raise DomainError(duplicate_refusal(other))
        changed["name"] = {"from": old_name, "to": cleaned}
        locked.name_et = cleaned
    if new_definition != locked.definition:
        # The guidance can run to a paragraph; the audit row says that it
        # changed, and the row itself holds what it says now.
        changed["definition"] = {}
        locked.definition = new_definition
    if not changed:
        return locked
    locked.save(update_fields=["name_et", "definition", "updated_at"])

    if "name" in changed and keep_old_name and old_folded != folded:
        if not locked.aliases.filter(normalized_alias=old_folded).exists():
            TagAlias.objects.create(tag=locked, alias=old_name)

    record_change_event(
        event_type=ChangeEventType.TAG_CHANGED,
        actor=actor,
        obj=locked,
        summary=locked.name_et[:200],
        payload={"tag": str(locked.pk), "key": locked.key, "changed": changed},
    )
    return locked


@transaction.atomic
def add_tag_alias(
    *, actor: Any, tag: Any, alias: str, expected_revision: str | None = None
) -> TagAlias:
    cleaned = clean_single_line_name(alias)
    folded = normalize_for_matching(cleaned)
    if not folded:
        raise DomainError(ALIAS_REQUIRED)
    _vocabulary_lock()
    locked = _locked(tag)
    _check_revision(locked, expected_revision)
    if locked.merged_into_id is not None:
        raise DomainError(MERGED_IS_FROZEN)
    if folded == normalize_for_matching(locked.name_et):
        raise DomainError(ALIAS_IS_THE_NAME)
    if locked.aliases.filter(normalized_alias=folded).exists():
        raise DomainError(ALIAS_ALREADY_HERE)
    other = _claimed_by_another(folded, locked)
    if other is not None:
        raise DomainError(duplicate_refusal(other))

    created = TagAlias.objects.create(tag=locked, alias=cleaned)
    locked.save(update_fields=["updated_at"])
    record_change_event(
        event_type=ChangeEventType.TAG_ALIAS_ADDED,
        actor=actor,
        obj=locked,
        summary=f"{locked.name_et} ← {created.alias}"[:200],
        payload={"tag": str(locked.pk), "alias": created.alias, "alias_id": str(created.pk)},
    )
    return created


@transaction.atomic
def remove_tag_alias(*, actor: Any, alias: Any, expected_revision: str | None = None) -> Tag:
    _vocabulary_lock()
    try:
        row = TagAlias.objects.get(pk=getattr(alias, "pk", alias))
    except TagAlias.DoesNotExist:
        raise DomainError("Nimekuju ei leitud.") from None
    locked = _locked(row.tag_id)
    _check_revision(locked, expected_revision)
    if locked.merged_into_id is not None:
        raise DomainError(MERGED_IS_FROZEN)
    payload = {"tag": str(locked.pk), "alias": row.alias, "alias_id": str(row.pk)}
    row.delete()
    locked.save(update_fields=["updated_at"])
    record_change_event(
        event_type=ChangeEventType.TAG_ALIAS_REMOVED,
        actor=actor,
        obj=locked,
        summary=f"{locked.name_et} ← {payload['alias']}"[:200],
        payload=payload,
    )
    return locked


@transaction.atomic
def merge_tag(*, actor: Any, tag: Any, into: Any, expected_revision: str | None) -> Tag:
    """Retire ``tag`` into ``into``: one concept, two words, one of them canonical.

    Both rows are locked, in primary-key order so two opposite merges cannot
    deadlock — and then the second of them is refused, because by the time it
    holds the locks its target has been merged away. The target must be active
    and canonical itself: merging into a retired word would leave the
    vocabulary pointing nowhere a person can still choose.
    """
    source_pk = getattr(tag, "pk", tag)
    target_pk = getattr(into, "pk", into)
    if str(source_pk) == str(target_pk):
        raise DomainError(MERGE_INTO_ITSELF)
    _vocabulary_lock()
    try:
        locked = {
            str(row.pk): row
            for row in Tag.objects.select_for_update(no_key=True)
            .filter(pk__in=[source_pk, target_pk])
            .order_by("pk")
        }
    except (ValueError, ValidationError):
        raise DomainError("Silti ei leitud.") from None
    source = locked.get(str(source_pk))
    target = locked.get(str(target_pk))
    if source is None or target is None:
        raise DomainError("Silti ei leitud.")
    _check_revision(source, expected_revision)
    if source.merged_into_id is not None:
        raise DomainError(MERGED_IS_FROZEN)
    if target.merged_into_id is not None or not target.is_active:
        raise DomainError(MERGE_TARGET_RETIRED)

    usage = tag_usage(source)
    source.merged_into = target
    source.is_active = False
    source.deprecated_at = source.deprecated_at or timezone.now()
    # Through the model: `refresh_on_tag_merge` reprojects the Matters whose
    # canonical tag this changes, in this transaction (app/search/signals.py).
    source.save(update_fields=["merged_into", "is_active", "deprecated_at", "updated_at"])
    record_change_event(
        event_type=ChangeEventType.TAG_MERGED,
        actor=actor,
        obj=source,
        summary=f"{source.name_et} → {target.name_et}"[:200],
        payload={
            "tag": str(source.pk),
            "key": source.key,
            "into": str(target.pk),
            "into_key": target.key,
            "matters": usage.matters,
            "submissions": usage.submissions,
        },
    )
    return source


@transaction.atomic
def deactivate_tag(*, actor: Any, tag: Any, expected_revision: str | None) -> Tag:
    """Stop offering a word for new records; every record that carries it keeps it."""
    _vocabulary_lock()
    locked = _locked(tag)
    _check_revision(locked, expected_revision)
    if not locked.is_active:
        raise DomainError(ALREADY_INACTIVE)
    locked.is_active = False
    locked.deprecated_at = timezone.now()
    locked.save(update_fields=["is_active", "deprecated_at", "updated_at"])
    usage = tag_usage(locked)
    record_change_event(
        event_type=ChangeEventType.TAG_DEACTIVATED,
        actor=actor,
        obj=locked,
        summary=locked.name_et[:200],
        payload={
            "tag": str(locked.pk),
            "key": locked.key,
            "matters": usage.matters,
            "submissions": usage.submissions,
        },
    )
    return locked


@transaction.atomic
def reactivate_tag(*, actor: Any, tag: Any, expected_revision: str | None) -> Tag:
    """Offer a retired word again — unless it was merged, which is not a toggle."""
    _vocabulary_lock()
    locked = _locked(tag)
    _check_revision(locked, expected_revision)
    if locked.merged_into_id is not None:
        raise DomainError(MERGED_IS_FROZEN)
    if locked.is_active:
        raise DomainError(ALREADY_ACTIVE)
    locked.is_active = True
    locked.deprecated_at = None
    locked.save(update_fields=["is_active", "deprecated_at", "updated_at"])
    record_change_event(
        event_type=ChangeEventType.TAG_REACTIVATED,
        actor=actor,
        obj=locked,
        summary=locked.name_et[:200],
        payload={"tag": str(locked.pk), "key": locked.key},
    )
    return locked
