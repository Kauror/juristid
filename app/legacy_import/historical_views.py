"""Reading the historical corpus, and reconciling what the audit could not.

Two surfaces, deliberately far apart.

The **source page** is a lawyer's surface. It renders a OneNote page as the case
file it was: narrative and files interleaved in the order somebody wrote them,
so that "Ettepaneku eestikeelne variant" still sits directly above the PDF it
introduces. Reducing that to an alphabetical attachment list at the bottom would
throw away the only thing OneNote was ever good at (Stage-2D brief 31).

The **review queue** is an operator's surface, under Admin, because 535 pending
decisions in the middle of a lawyer's navigation is not a feature.
"""

from __future__ import annotations

from typing import Any

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Count
from django.http import FileResponse, Http404, HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_http_methods

from app.accounts.enums import UserRole
from app.core.authorization import apply as apply_scope
from app.core.authorization import matter_visibility_q, scope_for_user
from app.core.http import content_disposition
from app.documents.inline import may_open_inline
from app.documents.models import Document
from app.legacy_import.historical_apply import index_source_link
from app.legacy_import.source_pages import (
    CandidateClass,
    CandidateState,
    HistoricalMatchCandidate,
    LegacySourcePage,
    MatterSourcePage,
    SourceMatchClass,
    SourceMatchMethod,
    SourceRelationshipKind,
)
from app.matters.enums import DataQualityTier, MatterOrigin, RecordMode
from app.matters.models import Matter


def visible_links(user: Any) -> Any:
    """Matter↔page relationships this user may read.

    Scoped through the Matter, exactly like every other read in the system. A
    historical page is not less confidential for being old: the 2019 file on a
    member's insolvency is the same kind of material as this year's
    (Stage-2D brief 60).
    """
    scope = scope_for_user(user)
    return apply_scope(
        MatterSourcePage.objects.select_related("matter", "source_page"),
        matter_visibility_q(scope, prefix="matter__"),
    )


@login_required
def source_page(request: HttpRequest, pk: Any) -> HttpResponse:
    """One historical page, as the case file it was."""
    link = get_object_or_404(visible_links(request.user), pk=pk)
    page = link.source_page

    documents = {
        record.resource.resource_key: record
        for record in link.resource_imports.select_related(
            "resource", "document", "document_version"
        )
    }
    resources = {resource.resource_key: resource for resource in page.resources.all()}

    return render(
        request,
        "legacy_import/source_page.html",
        {
            "matter": link.matter,
            "link": link,
            "page": page,
            "blocks": _rendered_blocks(page, resources, documents),
            "other_matters": (
                visible_links(request.user)
                .filter(source_page=page)
                .exclude(pk=link.pk)
                .select_related("matter")
            ),
            "nav_active": "teemad",
        },
    )


def _rendered_blocks(page: LegacySourcePage, resources: dict, documents: dict) -> list[dict]:
    """Narrative and files in one sequence, ready for the template.

    The template makes no decisions. Everything about what a block is, whether
    its file was materialised, and what may be done with it is settled here, so
    that changing the rendering cannot accidentally change what is shown.
    """
    out: list[dict] = []
    for block in page.blocks or []:
        kind = block.get("kind", "TEXT")
        if kind == "TITLE":
            # The page's own title is already the heading. Repeating it as the
            # first line of the narrative reads like a mistake.
            continue
        if kind in {"FILE_ATTACHMENT", "IMAGE"}:
            key = block.get("resource_key", "")
            resource = resources.get(key)
            record = documents.get(key)
            out.append(
                {
                    "kind": "file",
                    "ordinal": block.get("ordinal", 0),
                    "filename": resource.original_filename if resource else key,
                    "size_bytes": resource.size_bytes if resource else 0,
                    "document": record.document if record else None,
                    "version": record.document_version if record else None,
                    # What clicking the filename should do. Decided here, from
                    # the stored bytes' own extension and MIME type, so the
                    # template cannot accidentally make something openable by
                    # rendering it differently (app/documents/inline.py).
                    "opens_inline": bool(
                        record
                        and record.document_version
                        and may_open_inline(
                            filename=record.document_version.original_filename,
                            mime_type=record.document_version.mime_type,
                        )
                    ),
                    # A file the importer has not reached yet is shown as
                    # itself, waiting. Hiding it would make the page look like
                    # it had fewer materials than it does.
                    "state": _file_state(resource, record),
                }
            )
        else:
            text = (block.get("text") or "").strip()
            if text:
                out.append(
                    {
                        "kind": "text",
                        "ordinal": block.get("ordinal", 0),
                        "text": text,
                        "depth": block.get("depth", 0),
                        "list_item": kind == "LIST_ITEM",
                    }
                )
    return out


def _file_state(resource: Any, record: Any) -> str:
    """What actually happened to one attachment, said in one word.

    The first real import found this: six attachments in the corpus are zero
    bytes in OneNote itself, `add_evidence_version` refuses to store an empty
    evidence file — correctly, an empty file is not evidence — and the page then
    showed them as "Kopeerimisel" for ever. A file that will never arrive must
    not read as one that is on its way.

    `empty` and `unavailable` are separated because they are different facts
    about the source. An empty attachment is exactly what the lawyer's OneNote
    page contained; an unavailable one means the copy failed and is worth an
    operator's attention.
    """
    if record is None:
        return "pending"
    if record.document is not None:
        return "imported"
    if resource is not None and resource.size_bytes == 0:
        return "empty"
    return "unavailable"


@login_required
def source_xml(request: HttpRequest, pk: Any) -> FileResponse:
    """The page's own XML, for somebody who needs the evidence itself.

    Always an attachment. This is OneNote's markup, it is untrusted like any
    stored file, and it is never rendered — the readable version is the page
    above (Stage-2D brief 11).
    """
    from app.legacy_import.historical_apply import legacy_source_storage

    link = get_object_or_404(visible_links(request.user), pk=pk)
    page = link.source_page
    if not page.source_xml_storage_key:
        return HttpResponse(status=404)  # type: ignore[return-value]

    handle = legacy_source_storage().open(page.source_xml_storage_key, "rb")
    response = FileResponse(handle, content_type="application/xml", as_attachment=True)
    response["Content-Disposition"] = content_disposition("attachment", f"{page.page_key}.xml")
    response["X-Content-Type-Options"] = "nosniff"
    return response


def _require_administrator(request: HttpRequest) -> None:
    """The reconciliation queue is migration work, not legal work.

    Gated on the URL rather than only hidden from the navigation: a route that
    is merely unlinked is still a route, and this one can create Matters
    (Stage-2D brief 39).
    """
    if getattr(request.user, "role", None) != UserRole.ADMINISTRATOR:
        raise Http404


def _reviewer(request: HttpRequest) -> Any:
    """The signed-in person, as the model wants them.

    Every view here is `@login_required`, so `request.user` is a real User —
    but the type checker sees the `AnonymousUser` union that decorator cannot
    narrow, and three casts at three call sites is worse than one helper that
    says why.
    """
    return request.user


# -- reconciliation review -------------------------------------------------


@login_required
def review_queue(request: HttpRequest) -> HttpResponse:
    """What the audit could not settle, in one list with the evidence beside it."""
    _require_administrator(request)
    state = request.GET.get("olek") or CandidateState.PENDING
    candidate_class = request.GET.get("klass") or ""

    candidates = HistoricalMatchCandidate.objects.select_related(
        # `decided_by` for the same reason the opinion queue needs it: it is
        # rendered on every decided row, and without it each one costs a query.
        "source_page",
        "matter",
        "decided_by",
    ).order_by("candidate_class", "-score")
    if state:
        candidates = candidates.filter(state=state)
    if candidate_class:
        candidates = candidates.filter(candidate_class=candidate_class)

    counts = _pending_counts_by_class()

    return render(
        request,
        "legacy_import/review_queue.html",
        {
            "candidates": candidates[:200],
            "total": candidates.count(),
            "counts": counts,
            "state": state,
            "candidate_class": candidate_class,
            "classes": CandidateClass.choices,
            "states": CandidateState.choices,
            "nav_active": "haldus",
        },
    )


def _pending_counts_by_class() -> dict[str, int]:
    """Unreviewed work per class, in one query rather than one query per class.

    Every class appears whether or not it has rows: the filter strip reads
    this, and a class missing from it reads as "no such class" rather than
    "nothing left to do".
    """
    tally: dict[str, int] = dict.fromkeys(CandidateClass.values, 0)
    rows = (
        HistoricalMatchCandidate.objects.filter(state=CandidateState.PENDING)
        .order_by()
        .values("candidate_class")
        .annotate(total=Count("id"))
    )
    for row in rows:
        if row["candidate_class"] in tally:
            tally[row["candidate_class"]] = row["total"]
    return tally


@login_required
@require_http_methods(["POST"])
def review_decide(request: HttpRequest, pk: Any) -> HttpResponse:
    """Record one decision, and do what it implies. Idempotently.

    A reviewer who clicks twice, or whose browser retries, must not get two
    Matters or two links. Every branch below is a get-or-create or a state
    change that is already true the second time (Stage-2D brief 41).
    """
    _require_administrator(request)
    candidate = get_object_or_404(HistoricalMatchCandidate, pk=pk)
    decision = request.POST.get("decision", "")

    handlers = {
        "link": _link_to_matter,
        "create": _create_matter_from_page,
        "background": lambda c, r: _mark(c, r, CandidateState.BACKGROUND),
        "container": lambda c, r: _mark(c, r, CandidateState.CONTAINER),
        "reject": lambda c, r: _mark(c, r, CandidateState.REJECTED),
    }
    handler = handlers.get(decision)
    if handler is None:
        messages.error(request, "Tundmatu otsus.")
        return redirect("legacy_import:review_queue")

    try:
        message = handler(candidate, request)
    except ValueError as error:
        messages.error(request, str(error))
        return redirect("legacy_import:review_queue")

    messages.success(request, message)
    return redirect("legacy_import:review_queue")


def _link_to_matter(candidate: HistoricalMatchCandidate, request: HttpRequest) -> str:
    reviewer = _reviewer(request)
    if candidate.matter is None or candidate.source_page is None:
        raise ValueError("Kandidaadil puudub teema või lähteleht.")
    link, _ = MatterSourcePage.objects.get_or_create(
        matter=candidate.matter,
        source_page=candidate.source_page,
        defaults={
            "relationship_kind": SourceRelationshipKind.RELATED,
            "match_method": SourceMatchMethod.REVIEWED_MATCH,
            "match_class": SourceMatchClass.REVIEWED,
            "source_audit_reference": f"review:{candidate.pk}",
            "reviewed_by": reviewer,
            "reviewed_at": timezone.now(),
        },
    )
    index_source_link(link)
    _mark(candidate, request, CandidateState.LINKED, matter=candidate.matter)
    return (
        f"Leht seoti teemaga {candidate.matter.display_reference or candidate.matter.title[:40]}."
    )


def _create_matter_from_page(candidate: HistoricalMatchCandidate, request: HttpRequest) -> str:
    reviewer = _reviewer(request)
    if candidate.source_page is None:
        raise ValueError("Kandidaadil puudub lähteleht.")
    page = candidate.source_page
    existing = MatterSourcePage.objects.filter(
        source_page=page, match_method=SourceMatchMethod.ONENOTE_ONLY_MATTER
    ).first()
    if existing is not None:
        _mark(candidate, request, CandidateState.MATTER_CREATED, matter=existing.matter)
        return "Sellest lehest on teema juba loodud."

    matter = Matter.objects.create(
        title=(page.title or "Ajalooline teema")[:2000],
        reference_year=None,
        reference_number=None,
        record_mode=RecordMode.ARCHIVE,
        origin=MatterOrigin.LEGACY_ONENOTE,
        data_quality_tier=DataQualityTier.TIER_3_REGISTER_ARCHIVE,
        reporting_year=page.source_created_at.year if page.source_created_at else None,
        is_open=False,
    )
    link = MatterSourcePage.objects.create(
        matter=matter,
        source_page=page,
        relationship_kind=SourceRelationshipKind.PRIMARY,
        match_method=SourceMatchMethod.ONENOTE_ONLY_MATTER,
        match_class=SourceMatchClass.REVIEWED,
        source_audit_reference=f"review:{candidate.pk}",
        reviewed_by=reviewer,
        reviewed_at=timezone.now(),
    )
    index_source_link(link)
    _mark(candidate, request, CandidateState.MATTER_CREATED, matter=matter)
    return f"Loodi ajalooline teema „{matter.title[:50]}“."


def _mark(
    candidate: HistoricalMatchCandidate,
    request: HttpRequest,
    state: str,
    *,
    matter: Matter | None = None,
) -> str:
    candidate.state = state
    candidate.decided_by = _reviewer(request)
    candidate.decided_at = timezone.now()
    candidate.decision_note = (request.POST.get("note") or "")[:2000]
    if matter is not None:
        candidate.resulting_matter = matter
    candidate.save(
        update_fields=[
            "state",
            "decided_by",
            "decided_at",
            "decision_note",
            "resulting_matter",
            "updated_at",
        ]
    )
    return f"Märgitud: {candidate.get_state_display()}."


def historical_summary(matter: Matter, user: Any) -> dict[str, Any]:
    """The compact fact for a Matter's overview.

    Counts and one section name. The whole page belongs on the page, not on a
    dashboard that a lawyer reads in three seconds (Stage-2D brief 34).
    """
    links = list(visible_links(user).filter(matter=matter))
    if not links:
        return {}
    files = sum(link.source_page.file_count for link in links)
    sections = list(dict.fromkeys(link.source_page.source_section for link in links))
    return {
        "links": links,
        "page_count": len(links),
        "file_count": files,
        "sections": sections,
        "first": links[0],
    }


def historical_documents(matter: Matter, user: Any) -> Any:
    """Documents on this Matter that came out of the historical corpus."""
    return (
        Document.objects.visible_to(user)
        .filter(matter=matter, legacy_imports__isnull=False)
        .distinct()
        .select_related("current_version")
    )
