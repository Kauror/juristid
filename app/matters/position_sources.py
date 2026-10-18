"""What still stands as the source of a recorded position (docs/adr/0167 §1).

docs/adr/0084 §3 (amended 2026-09-16) says a `MatterExternalPosition` always
holds one of three sources: the written `Seisukoht`, a public address, or an
attached document. The first two are columns on the row; the third is a
`DocumentLink` to a `Document` — and `Eemalda dokument` (docs/adr/0120) takes a
document off the Matter by setting `removed_at` while the link row stays. A
link to a removed document is therefore **not** a source any more: nobody can
open it through the product, it is not listed, not counted and not searched.

Two questions are asked of that, and both are answered here so the door that
removes a file and the door that corrects a position cannot disagree:

* :func:`live_source_document_count` — how many files still stand behind a
  position. `correct_external_position` asks it under the Matter's lock instead
  of counting every link row (QA-SUBDOCS-02).
* :func:`positions_resting_only_on` — which positions would hold nothing at all
  if this one document came off. `removal_refusal` asks it, so the last live
  file of a position with no text and no address is refused rather than
  removed.

Both read the plain managers, unscoped: they decide whether an act may happen
at all, never what a reader is shown — the rule `opinion_evidence_statuses`
keeps for the same reason.
"""

from __future__ import annotations

from typing import Any

from django.db.models import Count, Q

from app.matters.models import MatterExternalPosition


def live_source_document_count(position: MatterExternalPosition) -> int:
    """The files that still stand as this position's source.

    A link whose document was removed (`Document.removed_at`) is not counted:
    the bytes stay in the evidence store, but the product no longer offers the
    file to anybody, so it cannot be what makes the record complete.
    """
    from app.documents.links import DocumentLink

    return DocumentLink.objects.filter(
        external_position=position, document__removed_at__isnull=True
    ).count()


def positions_resting_only_on(document: Any) -> list[MatterExternalPosition]:
    """Live positions whose only remaining source is this one document.

    A position qualifies when it is not itself removed, carries no written
    `Seisukoht` and no address, and has no *other* linked document that is
    still on the Matter. Taking this document off would leave each of them
    holding none of the three sources docs/adr/0084 §3 requires.

    One query: the other live links are counted in the database rather than
    per position.
    """
    from app.documents.links import DocumentLink

    # The positions this document supports, as a subquery rather than a join:
    # a `filter` on `document_links` ahead of the `Count` below would narrow
    # the counted rows to this document's own link and every position would
    # look as if it had nothing else.
    supported = DocumentLink.objects.filter(
        document=document, external_position__isnull=False
    ).values("external_position_id")
    other_live_links = Count(
        "document_links",
        filter=Q(document_links__document__removed_at__isnull=True)
        & ~Q(document_links__document=document),
    )
    return list(
        MatterExternalPosition.objects.live()
        .filter(
            pk__in=supported,
            summary="",
            url="",
        )
        .annotate(other_live_links=other_live_links)
        .filter(other_live_links=0)
    )
