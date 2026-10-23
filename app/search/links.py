"""Where a search result opens — one rule, read by every surface that links one.

Moved here from `app.search.views` (it was `_target_url`) when a second reader
arrived: the MCP server's `search` and `search_records` tools return the same
destination as an absolute URL (docs/adr/0185 §2). Two copies of this decision
would be two opinions about where an opinion lives, and the day one of them
learnt a new kind the other would quietly keep sending people to the Matter.

Relative paths only, built with `reverse()` from the typed identities the
authorized projection row carried — never from anything a caller typed. Making
them absolute is the caller's business (`app.mcp_server.links`), because the
page links relatively and must go on doing so.
"""

from __future__ import annotations

from typing import Any

from django.urls import reverse

from app.search.models import SearchSourceKind
from app.submissions.opinions import opinions_url


def matter_path(matter_id: Any) -> str:
    """The Teema page."""
    return reverse("matters:matter_detail", kwargs={"pk": matter_id})


def entry_path(matter_id: Any, entry_id: Any) -> str:
    """The Teema page, at the entry's own anchor in `Teema käik`."""
    return f"{matter_path(matter_id)}#sissekanne-{entry_id}"


def document_path(document_id: Any) -> str:
    """A document's own page — its preview, versions and extracted text."""
    return reverse("documents:document_detail", kwargs={"pk": document_id})


def source_page_path(link_id: Any) -> str:
    """One historical OneNote page, by its Matter↔page link (`MatterSourcePage`)."""
    return reverse("legacy_import:source_page", kwargs={"pk": link_id})


def target_path(result: Any) -> str:
    """Where clicking this result goes — as close to the source as practical.

    Server-rendered URLs and anchors, not a client-side router: the timeline is
    already one page with one anchor per entry, and a document already has a
    detail page. Building a navigation layer to get a few hundred pixels closer
    would be a large amount of machinery for a scroll position
    (Stage-2B brief 77).
    """
    kind = result.source_kind
    matter_url = matter_path(result.matter.pk)

    if (
        kind in (SearchSourceKind.DOCUMENT_FRAGMENT, SearchSourceKind.DOCUMENT)
        and result.document_id
    ):
        return document_path(result.document_id)
    if kind == SearchSourceKind.ENTRY and result.entry_id:
        return f"{matter_url}#sissekanne-{result.entry_id}"
    if kind == SearchSourceKind.LEGACY_SOURCE_PAGE and result.source_page_id:
        return source_page_path(result.source_page_id)
    if kind == SearchSourceKind.SUBMISSION and result.submission_id:
        # The Matter's own opinion material, which is Dokumendid filtered to
        # `Arvamus` since the per-Matter Arvamused page was retired — an address
        # this used to point at and which would now only redirect here anyway
        # (docs/adr/0061).
        #
        # Filtered rather than anchored on the opinion file itself. The
        # projection carries `submission_id` and no document identity, and
        # resolving one per result would be a query per row *and* an
        # authorization decision per row: a Submission a reader may find can
        # point at a Document restricted below it, so an anchor built from the
        # submission alone would name a file the page then refuses to render.
        # The filtered list answers the same question and cannot leak — every
        # row on it went through `Document.objects.visible_to` (AUTH-003 §21).
        return opinions_url(result.matter)
    return matter_url
