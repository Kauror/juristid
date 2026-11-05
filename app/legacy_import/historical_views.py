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

import logging
from dataclasses import dataclass
from typing import Any

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db.models import Count
from django.http import FileResponse, Http404, HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_http_methods

from app.accounts import shared_gate
from app.audit.enums import SecurityEventType
from app.audit.services import record_security_event
from app.core.authorization import (
    QUEUE_NEEDS_A_READER,
    matter_visibility_q,
    may_read_business_content,
    may_review_historical_matches,
    scope_for_user,
)
from app.core.authorization import apply as apply_scope
from app.core.client_ip import client_ip
from app.core.http import content_disposition
from app.documents.inline import may_open_inline
from app.documents.models import Document
from app.legacy_import.historical_apply import is_empty_at_source
from app.legacy_import.opinion_views import MATTER_NOT_FOUND, queue_rows
from app.legacy_import.shared_dossiers import shared_dossier
from app.legacy_import.source_pages import (
    CandidateClass,
    CandidateState,
    HistoricalMatchCandidate,
    LegacySourcePage,
    LegacySourceResourceImport,
    MatterSourcePage,
    ResourceImportState,
    SourceMatchClass,
    SourceMatchMethod,
    SourceRelationshipKind,
)
from app.matters.enums import DataQualityTier, MatterOrigin, RecordMode
from app.matters.models import Matter

logger = logging.getLogger(__name__)


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
    # The page is readable; each attachment is a `Document` with its own
    # visibility, which may be stricter. Read once, by id (ENG-047).
    readable = set(
        Document.objects.visible_to(request.user)
        .filter(pk__in=[record.document_id for record in documents.values() if record.document_id])
        .values_list("pk", flat=True)
    )
    blocks = _rendered_blocks(
        page,
        resources,
        documents,
        readable=readable,
        servable=_servable_originals(page, request.user),
    )
    # The page's Teemad and how they relate, for this reader (docs/adr/0208).
    dossier = shared_dossier(page, request.user)

    return render(
        request,
        "legacy_import/source_page.html",
        {
            "matter": link.matter,
            "link": link,
            "page": page,
            "blocks": blocks,
            "page_links": page_links(page.links),
            "other_matters": (
                visible_links(request.user)
                .filter(source_page=page)
                .exclude(pk=link.pk)
                .select_related("matter")
            ),
            "dossier": dossier,
            "dossier_rows": [
                {"teema": item, "relation": dossier.relation_of(item)} for item in dossier.teemad
            ]
            if dossier.ambiguous
            else [],
            "dossier_files": [block for block in blocks if block["kind"] == "file"]
            if dossier.ambiguous
            else [],
            "nav_active": "teemad",
        },
    )


def _servable_originals(page: LegacySourcePage, user: Any) -> set[Any]:
    """Resources of `page` whose held original this reader may be offered.

    A resource some Teema holds as a `Document` this reader may not read is left
    out: the original is never a way round a stricter document (ENG-047). The
    download route checks the same again on its own (`source_originals`).
    """
    made = dict(
        LegacySourceResourceImport.objects.filter(
            resource__source_page=page, document__isnull=False
        ).values_list("document_id", "resource_id")
    )
    readable = set(
        Document.objects.visible_to(user).filter(pk__in=made).values_list("pk", flat=True)
    )
    stricter = {resource for document, resource in made.items() if document not in readable}
    return set(page.resources.exclude(pk__in=stricter).values_list("pk", flat=True))


def page_links(stored: Any) -> list[dict[str, str]]:
    """A page's own hyperlinks as ``{"url", "text"}``, in the order captured.

    The desktop archive has written them in two shapes, and
    `LegacySourcePage.links` keeps each page's exactly as captured: ``url`` /
    ``displayText`` in the 2026-08 corpus, ``href`` / ``text`` (with the
    ``blockOrdinal`` they sat in) in the evidence-pilot capture. Both are read
    here, nothing stored is rewritten, and an entry with no address is not a
    link to show. A template that named one shape's key failed the whole page on
    the other's (production, 20.10.2026).
    """
    links = []
    for item in stored or []:
        if not isinstance(item, dict):
            continue
        url = str(item.get("url") or item.get("href") or "").strip()
        if not url:
            continue
        text = str(item.get("displayText") or item.get("text") or "").strip()
        links.append({"url": url, "text": text})
    return links


def _rendered_blocks(
    page: LegacySourcePage,
    resources: dict,
    documents: dict,
    *,
    readable: set[Any],
    servable: set[Any] | None = None,
) -> list[dict]:
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
            state = _file_state(resource, record)
            if record is not None and record.document_id and record.document_id not in readable:
                # A file this reader may not read: its place in the page is kept
                # and nothing about it is — no name, no size, no id to follow
                # (ENG-047). Hiding the block altogether would make the page
                # look as though it had less material than it does.
                out.append(
                    {
                        "kind": "file",
                        "ordinal": block.get("ordinal", 0),
                        "filename": "",
                        "size_bytes": 0,
                        "document": None,
                        "version": None,
                        "opens_inline": False,
                        "state": "withheld",
                        "original_pk": None,
                        "added_at": None,
                        "added_known": False,
                    }
                )
                continue
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
                    "state": state,
                    # A held shared-page file's sealed original, read-only
                    # (docs/adr/0208): the resource id the download route takes.
                    "original_pk": (
                        resource.pk
                        if state in SERVED_STATES
                        and resource is not None
                        and resource.size_bytes > 0
                        and resource.pk in (servable or set())
                        else None
                    ),
                    # The OneNote day the file was added, when the source has
                    # one (docs/adr/0206); never the capture or import time.
                    "added_at": resource.source_added_at if resource else None,
                    "added_known": bool(resource and resource.source_added_at),
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


#: A deliberate «not now» recorded by an importer (`SKIPPED`), by its code, and
#: the state the page shows for it. None of these is a failure: the original is
#: kept in the source corpus and nothing was lost (docs/adr/0205 §4, docs/adr/0206).
HELD_STATES = {
    # A page linked to several Teemad: which Teema a file belongs to is a
    # person's decision, never the importer's (owner decision D4).
    "SHARED_PAGE_AWAITING_REVIEW": "held_shared",
    "LARGER_THAN_EVIDENCE_LIMIT": "held_too_large",
    # A row of an earlier capture of the same page, superseded by a later one.
    "EARLIER_CAPTURE_NOT_IMPORTED": "superseded",
    # The evidence pilot's review outcomes (app/legacy_import/evidence_pilot.py).
    "RECORD_HELD_FOR_REVIEW": "held_review",
}

#: The file states whose sealed original the page offers (docs/adr/0208). The
#: download route keeps its own list of the hold codes it serves.
SERVED_STATES = frozenset({"held_shared"})


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

    **A file held on purpose is neither** (ONENOTE-UI-003, 2026-11-05). The
    capture import records every file of a page shared by several Teemad as
    `SKIPPED · SHARED_PAGE_AWAITING_REVIEW`, and this function used to call
    every recorded, non-imported, non-empty file `unavailable` — so 5,539 held
    decisions read «Kopeerimine ebaõnnestus», which a lawyer reads as evidence
    lost. Only a `FAILED` row is a failure now; a `SKIPPED` row says what it was
    held for (`HELD_STATES`), and one whose code this view does not know reads
    as «not imported», never as an error.
    """
    if record is None:
        return "pending"
    if record.document_id is not None:
        return "imported"
    if is_empty_at_source(resource) or record.error_code == "EMPTY_FILE":
        return "empty"
    if record.state == ResourceImportState.SKIPPED:
        return HELD_STATES.get(record.error_code, "not_imported")
    return "unavailable"


@login_required
def source_xml(request: HttpRequest, pk: Any) -> FileResponse:
    """The page's own XML, for somebody who needs the evidence itself.

    Always an attachment. This is OneNote's markup, it is untrusted like any
    stored file, and it is never rendered — the readable version is the page
    above (Stage-2D brief 11).

    The whole page's business content leaves the system here, so it is audited
    as every other served file is (`DOCUMENT_DOWNLOADED`, through the shared-gate
    helper); a stored key whose object is gone is a 404 and a failed audit row,
    never a 500, and neither the key nor the page title is in the answer.
    """
    from app.legacy_import.historical_apply import legacy_source_storage

    link = get_object_or_404(visible_links(request.user), pk=pk)
    page = link.source_page
    if not page.source_xml_storage_key:
        return HttpResponse(status=404)  # type: ignore[return-value]

    def audit(*, succeeded: bool, outcome: str = "") -> None:
        detail = {
            "source": "onenote_source_xml",
            "matter": str(link.matter_id),
            "sha256": page.source_xml_sha256,
            "disposition": "attachment",
        }
        if outcome:
            detail["outcome"] = outcome
        record_security_event(
            event_type=SecurityEventType.DOCUMENT_DOWNLOADED,
            actor=request.user if request.user.is_authenticated else None,
            subject=page,
            ip_address=client_ip(request),
            user_agent=request.META.get("HTTP_USER_AGENT", ""),
            succeeded=succeeded,
            detail=shared_gate.audit_detail(request, **detail),
        )

    try:
        handle = legacy_source_storage().open(page.source_xml_storage_key, "rb")
    except FileNotFoundError:
        logger.error(
            "historical source XML missing page=%s storage_key=%s",
            page.pk,
            page.source_xml_storage_key,
        )
        audit(succeeded=False, outcome="source_object_missing")
        raise Http404("Lehe lähte-XML-i ei leitud hoidlast.") from None
    audit(succeeded=True)
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
    if not may_review_historical_matches(request.user):
        raise Http404
    # The queue is the administrator's; its rows — OneNote page titles, sizes,
    # the Excel title, the proposed Matter — are business content, which the
    # administrator reads only through a break-glass grant (docs/adr/0181). A
    # 403 rather than the 404 above: the route is theirs, its content is not
    # yet, and saying so is what sends them to ask for a grant.
    if not may_read_business_content(request.user):
        raise PermissionDenied(QUEUE_NEEDS_A_READER)


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
            "rows": queue_rows(list(candidates[:200]), request.user),
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
    if not Matter.objects.visible_to(request.user).filter(pk=candidate.matter_id).exists():
        # A proposal pointing at a Matter this reviewer may not read is not one
        # they may act on: linking would write onto it and the answer would name
        # it (ENG-067). The same sentence as for a Matter that is not there.
        raise ValueError(MATTER_NOT_FOUND)
    MatterSourcePage.objects.get_or_create(
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
    MatterSourcePage.objects.create(
        matter=matter,
        source_page=page,
        relationship_kind=SourceRelationshipKind.PRIMARY,
        match_method=SourceMatchMethod.ONENOTE_ONLY_MATTER,
        match_class=SourceMatchClass.REVIEWED,
        source_audit_reference=f"review:{candidate.pk}",
        reviewed_by=reviewer,
        reviewed_at=timezone.now(),
    )
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
    earlier = predecessor_pages(matter, user, own=links)
    if not links:
        return {"links": [], "predecessor_pages": earlier} if earlier else {}
    files = sum(link.source_page.file_count for link in links)
    sections = list(dict.fromkeys(link.source_page.source_section for link in links))
    statuses = import_statuses(links)
    for link in links:
        # Read by the page's row in «Ajalooline OneNote».
        link.import_status = statuses[link.pk]
    return {
        "links": links,
        "page_count": len(links),
        "file_count": files,
        "sections": sections,
        "first": links[0],
        "status": teema_import_status(list(statuses.values())),
        "predecessor_pages": earlier,
    }


def predecessor_pages(matter: Matter, user: Any, *, own: list[Any]) -> list[dict[str, Any]]:
    """The earlier stages' own OneNote pages, for «Eelnevate etappide ajalooline materjal».

    A Teema that continues another (`Matter.superseded_by`, docs/adr/0207) often
    started its dossier under the earlier reference. Those pages are listed
    here, each through the *predecessor's* link — so the page view a reader
    lands on is the predecessor's, governed by the predecessor's scope — and
    each page once, by its own identity. A page this Teema links itself is not
    repeated; its own row in «Ajalooline OneNote» says it is in an earlier
    stage's dossier too (`shared_with_earlier`, set on `own`). Nothing is
    counted into this Teema's files: these are not its Documents (docs/adr/0208).

    Only readable predecessors are walked (`continuations_for` re-reads every
    step through `visible_to`), and a predecessor's page link is read through
    `visible_links`. No read is granted by the edge itself.
    """
    from app.matters.continuations import continuations_for

    chain = continuations_for(matter, user)
    stages = list(dict.fromkeys([*chain.before, *chain.predecessors]))
    for link in own:
        link.shared_with_earlier = []
    if not stages:
        return []
    own_by_page = {link.source_page_id: link for link in own}
    order = {stage.pk: index for index, stage in enumerate(stages)}
    theirs = sorted(
        visible_links(user).filter(matter__in=[stage.pk for stage in stages]),
        key=lambda link: (order[link.matter_id], link.source_page.title or "", str(link.pk)),
    )
    pages: dict[Any, dict[str, Any]] = {}
    for link in theirs:
        mine = own_by_page.get(link.source_page_id)
        if mine is not None:
            if link.matter not in mine.shared_with_earlier:
                mine.shared_with_earlier.append(link.matter)
            continue
        entry = pages.setdefault(link.source_page_id, {"link": link, "stages": []})
        if link.matter not in entry["stages"]:
            entry["stages"].append(link.matter)
    rows = list(pages.values())
    statuses = import_statuses([row["link"] for row in rows])
    for row in rows:
        row["page"] = row["link"].source_page
        row["import_status"] = statuses[row["link"].pk]
    return rows


# -- what the import records say -------------------------------------------

#: The order a page's file counts are said in, and the words. Imported first,
#: then what is held on purpose, then what is wrong. `superseded` is absent: an
#: earlier capture's row is not a file on the page as it is now.
_STATUS_PARTS = (
    ("imported", "imporditud", ""),
    ("held_shared", "ootab teemaga seostamist", "waiting"),
    ("held_review", "ootab ülevaatust", "waiting"),
    ("held_too_large", "ületab tõendi ülempiiri", "waiting"),
    ("empty", "oli OneNote'is tühi", ""),
    ("not_imported", "jäeti importimata", ""),
    ("pending", "on veel importimata", "waiting"),
    ("unavailable", "kopeerimine ebaõnnestus", "problem"),
)


def _files(n: int) -> str:
    return "1 fail" if n == 1 else f"{n} faili"


@dataclass(frozen=True)
class ImportStatus:
    """One page's files as the import rows record them (ONENOTE-UI-002).

    Counted from `LegacySourceResourceImport`, one state per file block of the
    page as it is now (`_file_state`, the same answer the page view prints) —
    never inferred from the page row existing, which says only that the page's
    text is here.
    """

    counts: dict[str, int]
    files: int

    @property
    def label(self) -> str:
        """The page row's chip: «Kõik 27 faili imporditud», «145 faili ootab teemaga seostamist»."""
        if not self.files:
            return "Lehel ei ole faile"
        imported = self.counts.get("imported", 0)
        empty = self.counts.get("empty", 0)
        if imported and imported + empty == self.files:
            text = f"Kõik {imported} faili imporditud" if imported > 1 else "Fail imporditud"
            return text + (f" · {_files(empty)} oli OneNote'is tühi" if empty else "")
        return " · ".join(
            f"{_files(self.counts[state])} {words}"
            for state, words, _tone in _STATUS_PARTS
            if self.counts.get(state)
        )

    @property
    def tone(self) -> str:
        """`problem` when any copy failed, `waiting` when anything is held or pending."""
        tones = {tone for state, _w, tone in _STATUS_PARTS if self.counts.get(state)}
        return "problem" if "problem" in tones else "waiting" if "waiting" in tones else ""


def import_statuses(links: list[Any]) -> dict[Any, ImportStatus]:
    """Every link's `ImportStatus`, from one query over the links' import rows."""
    records: dict[Any, dict[str, Any]] = {link.pk: {} for link in links}
    for record in LegacySourceResourceImport.objects.filter(
        matter_source_page__in=[link.pk for link in links]
    ).select_related("resource"):
        records[record.matter_source_page_id][record.resource.resource_key] = record
    out = {}
    for link in links:
        mine = records[link.pk]
        states = []
        for block in link.source_page.blocks or []:
            if block.get("kind") not in {"FILE_ATTACHMENT", "IMAGE"}:
                continue
            found = mine.get(block.get("resource_key", ""))
            states.append(_file_state(found.resource if found else None, found))
        counts = {state: states.count(state) for state in set(states) if state != "superseded"}
        out[link.pk] = ImportStatus(counts=counts, files=sum(counts.values()))
    return out


def teema_import_status(statuses: list[ImportStatus]) -> dict[str, Any]:
    """The Teema's sentence under «Allikas», from its pages' import records.

    It replaced a fixed «Lehe sisu ja manuseid ei ole Juristidesse imporditud»,
    which stayed on every Teema after its OneNote files had been imported
    (ONENOTE-UI-002, 2026-11-05).
    """
    total = ImportStatus(
        counts={
            state: sum(s.counts.get(state, 0) for s in statuses) for state, _w, _t in _STATUS_PARTS
        },
        files=sum(s.files for s in statuses),
    )
    lines = []
    if not total.files:
        lines.append("Lehe sisu on Juristides; lehel ei ole faile.")
    else:
        imported = total.counts["imported"]
        empty = total.counts["empty"]
        if imported and imported + empty == total.files:
            lines.append(
                f"Lehe sisu ja {'kõik ' if imported > 1 else ''}{_files(imported)} on Juristidesse "
                "imporditud (failide loend ja «Ajalooline OneNote» ülal)."
            )
            if empty:
                lines.append(f"{_files(empty)} oli OneNote'is tühi ja jäi tõendina loomata.")
        else:
            lines.append(f"Lehe sisu on Juristides. Failid: {total.label}.")
        if total.counts["held_shared"]:
            lines.append(
                "Leht on seotud mitme teemaga: faili kuuluvuse otsustab inimene; "
                "originaalid on lähtekorpuses alles."
            )
    return {"lines": lines, "tone": total.tone, "counts": total.counts, "files": total.files}
