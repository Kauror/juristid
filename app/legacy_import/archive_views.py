"""The archive as a place you can look things up, not just a queue to work off.

The reconciliation queue asks one question — *whose letter is this?* — and shows
only what helps answer it. That leaves the archive itself unreachable: 767 real
letters that exist, are held, and cannot be found by anybody who wants to read
one. This is the surface that fixes that, and it is a different job:

* the queue is **ordered by what needs deciding**; this is ordered by date and
  searched by content, because the question here is "what did we write about
  X" rather than "what is still open";
* the queue shows **catalogue metadata only**; this serves the letters
  themselves, which is why its authorization is stricter rather than the same
  (app/legacy_import/opinion_access.py);
* the queue's unit is a **proposal**; here it is the letter. Two paths holding
  identical bytes are one row, with its several occurrences listed on the
  detail page.

Under `/haldus/`, beside the queue, for the same reason the queue is: this is
migration work, and several hundred unfiled letters do not belong in a lawyer's
navigation (Stage-2H brief 62).
"""

from __future__ import annotations

from typing import Any

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.http import FileResponse, Http404, HttpRequest, HttpResponse, HttpResponseBase
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_http_methods

from app.audit.enums import SecurityEventType
from app.audit.services import record_security_event
from app.core.errors import DomainError
from app.core.http import content_disposition
from app.documents import inline
from app.documents.services import evidence_storage
from app.legacy_import import opinion_links
from app.legacy_import.opinion_access import require_archive_reader
from app.legacy_import.opinion_archive import OpinionMatchCandidate
from app.legacy_import.opinion_binary import OpinionArchiveBinary
from app.legacy_import.opinion_enums import ArchiveLinkBasis, OpinionCandidateState
from app.legacy_import.opinion_search import (
    PAGE_SIZE,
    ArchiveFilters,
    ArchiveQueryRefused,
    archive_counts,
    search_archive,
)


#: The filename an archive letter is served under. Deliberately not the name it
#: had in the ZIP: those carry recipients and subjects, several are mojibake
#: from a Windows-1257 zip entry, and a `Content-Disposition` is the wrong place
#: to find out. The SHA is what identifies the bytes anyway.
def _served_filename(binary: OpinionArchiveBinary) -> str:
    return f"arhiiv-{binary.sha256[:16]}.pdf"


@login_required
def archive_browse(request: HttpRequest) -> HttpResponse:
    """Search and filter the held archive."""
    require_archive_reader(request.user)

    filters = ArchiveFilters(
        query=request.GET.get("q", "").strip(),
        year=request.GET.get("aasta", "").strip(),
        review_state=request.GET.get("olek", "").strip(),
        linked=request.GET.get("seotud", "").strip(),
        body=request.GET.get("sisu", "").strip(),
    )
    refusal = ""
    rows: Any = []
    total = 0
    try:
        found = search_archive(user=request.user, filters=filters)
        total = found.count()
        page = Paginator(found, PAGE_SIZE).get_page(request.GET.get("leht"))
        rows = page
    except ArchiveQueryRefused as error:
        # An explicit refusal, never an empty result set. "No matches" and "we
        # did not run your query" look identical on a page and mean opposite
        # things about the corpus.
        refusal = str(error)
        page = None

    return render(
        request,
        "legacy_import/opinion_archive_browse.html",
        {
            "rows": rows,
            "page": page,
            "total": total,
            "counts": archive_counts(request.user),
            "filters": filters,
            "refusal": refusal,
            "states": OpinionCandidateState.choices,
            "years": _years(request.user),
            "nav_active": "haldus",
        },
    )


def _years(user: Any) -> list[int]:
    """The years the corpus actually covers, for the filter."""
    rows = search_archive(user=user, filters=ArchiveFilters())
    return sorted(
        {year for year in rows.values_list("source_year", flat=True) if year is not None},
        reverse=True,
    )


@login_required
def archive_detail(request: HttpRequest, pk: Any) -> HttpResponse:
    """One letter: where it was found, what is known, what it is tied to."""
    require_archive_reader(request.user)
    binary = get_object_or_404(
        OpinionArchiveBinary.objects.select_related("text", "search_document"), pk=pk
    )

    candidates = list(
        OpinionMatchCandidate.objects.filter(item__binary=binary)
        .select_related("matter", "decided_by", "superseded_by", "superseded_by__matter")
        .order_by("state", "-created_at")
    )
    return render(
        request,
        "legacy_import/opinion_archive_detail.html",
        {
            "binary": binary,
            "text": getattr(binary, "text", None),
            "row": getattr(binary, "search_document", None),
            "occurrences": opinion_links.occurrences_for(binary),
            "links": opinion_links.links_for(binary),
            # Retired proposals are shown, not hidden. "We used to think this,
            # and here is what replaced it" is often the most useful sentence
            # on the page for somebody auditing the migration.
            "live_candidates": [
                candidate
                for candidate in candidates
                if candidate.state != OpinionCandidateState.SUPERSEDED
            ],
            "superseded": [
                candidate
                for candidate in candidates
                if candidate.state == OpinionCandidateState.SUPERSEDED
            ],
            "nav_active": "haldus",
        },
    )


@login_required
@require_http_methods(["POST"])
def archive_link(request: HttpRequest, pk: Any) -> HttpResponse:
    """Add or withdraw one archive-to-Matter relationship."""
    require_archive_reader(request.user)
    binary = get_object_or_404(OpinionArchiveBinary, pk=pk)
    action = request.POST.get("action", "")
    reference = request.POST.get("viide", "").strip()

    matter = _matter_by_reference(reference)
    if matter is None:
        # Never created here, and the message says so. An archive file naming
        # something is not authority to open a register entry for it.
        messages.error(
            request,
            f"Teemat viitega „{reference}“ ei leitud. Seos saab osutada ainult "
            "olemasolevale teemale.",
        )
        return redirect("legacy_import:opinion_archive_detail", pk=binary.pk)

    try:
        if action == "unlink":
            opinion_links.unlink_matter(binary=binary, matter=matter, actor=request.user)
            messages.success(request, f"Seos teemaga {matter.display_reference} on eemaldatud.")
        else:
            _, created = opinion_links.link_matter(
                binary=binary,
                matter=matter,
                basis=ArchiveLinkBasis.REVIEWED,
                actor=request.user,
                note=request.POST.get("markus", "").strip()[:2000],
            )
            messages.success(
                request,
                f"Seos teemaga {matter.display_reference} on "
                + ("lisatud." if created else "juba olemas.")
                + " See ei loo arvamust.",
            )
    except DomainError as error:
        messages.error(request, str(error))

    return redirect("legacy_import:opinion_archive_detail", pk=binary.pk)


def _matter_by_reference(reference: str) -> Any:
    """Resolve ``YYYY_N`` to a Matter, or nothing. Never creates."""
    from app.matters.models import Matter

    parts = reference.replace("/", "_").replace("-", "_").split("_")
    if len(parts) != 2 or not all(part.isdigit() for part in parts):
        return None
    return Matter.objects.filter(
        reference_year=int(parts[0]), reference_number=int(parts[1])
    ).first()


@login_required
def archive_file(request: HttpRequest, pk: Any) -> HttpResponseBase:
    """Serve the letter's bytes to somebody entitled to read them.

    The storage key never appears in a URL and is never accepted from one. The
    route names the binary; the key is looked up from the row, so a crafted
    path cannot reach an object the archive does not own.

    Inline by default because these are PDFs and the point is reading them,
    under the same headers every other stored file gets — the allow-list, the
    `nosniff`, and the CSP that lets the page be looked at and do nothing else.
    """
    require_archive_reader(request.user)
    binary = get_object_or_404(OpinionArchiveBinary, pk=pk)
    filename = _served_filename(binary)
    as_attachment = request.GET.get("laadi") == "alla" or not inline.may_open_inline(
        filename=filename, mime_type=binary.mime_type
    )

    storage = evidence_storage()
    try:
        handle = storage.open(binary.storage_key, "rb")
    except FileNotFoundError as error:
        # The row exists and the bytes do not. A 404 rather than a 500: this is
        # a real state — materialisation writes bytes before rows and an
        # interrupted run can leave the reverse — and `opinion_archive_search
        # verify` is where it gets counted rather than here.
        raise Http404("Arhiivi baiti ei ole salvestuses.") from error

    record_security_event(
        event_type=SecurityEventType.DOCUMENT_DOWNLOADED,
        actor=request.user,
        subject=binary,
        ip_address=request.META.get("REMOTE_ADDR"),
        user_agent=request.META.get("HTTP_USER_AGENT", ""),
        detail={
            "source": "opinion_archive",
            "sha256": binary.sha256,
            "disposition": "attachment" if as_attachment else "inline",
        },
    )

    if as_attachment:
        response = FileResponse(handle, content_type=binary.mime_type or "application/pdf")
        response["X-Content-Type-Options"] = "nosniff"
        response["Content-Disposition"] = content_disposition("attachment", filename)
        return response

    response = FileResponse(handle, content_type=inline.inline_mime_for(filename))
    return inline.apply_inline_headers(response, filename=filename)
