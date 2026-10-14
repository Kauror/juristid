"""A record derived from a restricted `Kaasamine` round is never less restricted.

docs/adr/0162. The sibling of 0137 (a file is never less restricted than its
record), 0138 (a completion record is never less restricted than its action) and
0146 §10 (a follow-up check is never more visible than its opinion), applied to
the records a round produces: `Meile saadetud tagasiside` tied to it
(`MatterExternalPosition.engagement`), the files captured for that reply, and the
`Ülevaade` its `Ülevaate link` records.

Two halves, and both are needed:

* **At creation** (:func:`restriction_inherited_from`) the derived row copies
  the round's restriction onto its own column, the shape 0137/0138 use, so a
  new reply and the files captured for it are restricted like every other
  restricted child and every existing reader of that column agrees.
* **At read time** (the ``*_visibility_q`` builders) the round's restriction is
  also read **live**, the shape 0146 §10 uses for follow-up checks. Rows
  written before the creation rule existed carry an empty override and are
  never rewritten (no data migration); the live read is what keeps them from
  the readers the round excludes. It also covers a reply re-pointed at a
  restricted round later, and a round restricted after its replies were filed.

The read-time rule only ever *narrows*: a scope that sees all restricted work
gets the plain child rule with no join, and the Matter's participants (owner,
collaborators) keep the participation half exactly as `child_visibility_q`
gives it. Nothing here makes a row visible that was hidden before.
"""

from __future__ import annotations

from typing import Any

from django.apps import apps
from django.db.models import Exists, OuterRef, Q

from app.core.authorization import (
    UNRESTRICTED_OVERRIDE_VALUES,
    Scope,
    child_visibility_q,
    restricted_participation_q,
    restricted_participation_subquery_q,
)
from app.core.enums import Visibility


def restriction_inherited_from(source: Any) -> str:
    """The ``visibility_override`` a record derived from ``source`` is created with.

    ``RESTRICTED`` when ``source`` (a `MatterEngagement`) carries its own
    restriction; empty otherwise, so the derived record inherits the Matter
    exactly as before. ``None`` — a reply tied to no round — passes nothing on.
    The same shape as `completion_visibility_override` (0138) and
    `evidence_visibility_override` (0137): read off the source's own column and
    nothing else, copied at creation.
    """
    if source is None:
        return ""
    inherited = getattr(source, "visibility_override", "") or ""
    return "" if inherited == Visibility.NORMAL else inherited


def _is_narrowed(scope: Scope) -> bool:
    """Whether the derived-record rule can hide anything from ``scope``."""
    return scope.is_authenticated and not scope.sees_all_restricted


def external_position_visibility_q(scope: Scope, *, prefix: str = "") -> Q:
    """`MatterExternalPosition` rows ``scope`` may read.

    The ordinary child rule on the position's own override, **and** the same
    rule on the round it answers, when it answers one. ``prefix`` reaches a
    position from another table (``external_position__`` from a `DocumentLink`).
    """
    base = child_visibility_q(
        scope,
        parent_prefix=f"{prefix}matter__",
        override_field=f"{prefix}visibility_override",
    )
    if not _is_narrowed(scope):
        return base
    round_ = child_visibility_q(
        scope,
        parent_prefix=f"{prefix}matter__",
        override_field=f"{prefix}engagement__visibility_override",
    )
    return base & (Q(**{f"{prefix}engagement__isnull": True}) | round_)


def replies_to_restricted_rounds() -> Any:
    """`DocumentLink` rows tying a file to a reply whose round is restricted.

    Whitelisted like every visibility clause: a round override that is not one
    of the unrestricted values counts as restricted, so an unrecognised value
    fails closed.
    """
    link = apps.get_model("documents", "DocumentLink")
    return link.objects.filter(external_position__engagement__isnull=False).exclude(
        external_position__engagement__visibility_override__in=UNRESTRICTED_OVERRIDE_VALUES
    )


def _derived_from_restricted_round(document_ref: str) -> Q:
    """The document at ``document_ref`` is evidence for a reply to a restricted round."""
    return Q(Exists(replies_to_restricted_rounds().filter(document=OuterRef(document_ref))))


def document_visibility_q(scope: Scope, *, prefix: str = "") -> Q:
    """`Document` rows ``scope`` may read.

    The ordinary child rule on the document's own override, **and** not the file
    of a reply to a restricted round — unless ``scope`` participates in the
    Matter, which reads restricted children of it anyway. Every link in this
    product is written by the evidence capture for a file created for that
    record (`capture_accepted_evidence`), so such a file *is* the reply's
    evidence; the rule asks the link, never a filename, date or author.

    ``prefix`` reaches a document from another table (``document__`` from a
    `DocumentVersion` or a `DocumentLink`).
    """
    base = child_visibility_q(
        scope,
        parent_prefix=f"{prefix}matter__",
        override_field=f"{prefix}visibility_override",
    )
    if not _is_narrowed(scope):
        return base
    participation = restricted_participation_q(scope, prefix=f"{prefix}matter__")
    return base & (~_derived_from_restricted_round(f"{prefix}pk") | participation)


def projected_derivation_q(
    scope: Scope,
    *,
    kind_field: str,
    document_kinds: tuple[str, ...],
    external_position_kind: str,
) -> Q:
    """The search projection's half of the same rule (`visible_documents`).

    A projection row of a document kind is hidden when its document is the file
    of a reply to a restricted round; a row of the external-position kind when
    its position answers a restricted round. Everything else passes. The
    participation half is the subquery form the projection already uses, so
    nothing here fans rows out.
    """
    if not _is_narrowed(scope):
        return Q()
    document_ok = ~Q(**{f"{kind_field}__in": document_kinds}) | (
        ~_derived_from_restricted_round("document_id")
    )
    position_ok = (
        ~Q(**{kind_field: external_position_kind})
        | Q(external_position__engagement__isnull=True)
        | Q(external_position__engagement__visibility_override__in=UNRESTRICTED_OVERRIDE_VALUES)
    )
    participation = restricted_participation_subquery_q(
        scope, prefix="matter__", matter_field="matter"
    )
    return (document_ok & position_ok) | participation
