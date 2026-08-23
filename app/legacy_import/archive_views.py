"""The archive as a place you can look things up, not just a queue to work off.

The reconciliation queue asks one question — *whose letter is this?* — and shows
only what helps answer it. That leaves the archive itself unreachable: 767 real
letters that exist, are held, and cannot be found by anybody who wants to read
one. This is the surface that fixes that, and it is a different job:

* the queue is **ordered by what needs deciding**; this is ordered by date and
  searched by content, because the question here is "what did we write about
  X" rather than "what is still open";
* the queue shows **catalogue metadata only**; this serves the letters
  themselves, which is why its authorization is written separately
  (app/legacy_import/opinion_access.py);
* the queue's unit is a **proposal**; here it is the letter. Two paths holding
  identical bytes are one row, with its several occurrences listed on the
  detail page.

Under `/haldus/`, beside the queue, for the same reason the queue is: this is
migration work, and several hundred unfiled letters do not belong in a lawyer's
navigation (Stage-2H brief 62).

**Two boundaries meet on these pages, and they are not the same boundary.**
Whether somebody may open the archive at all is a question about the corpus,
answered by `may_read_archive`. What the page may then say about a *Matter* —
its title, its reference, a link to it — is the ordinary question every other
surface asks, answered by `Matter.objects.visible_to`. Reading the archive is
therefore never a route into a RESTRICTED register entry: the letters are
served, and the entries they are tied to are named only when the reader could
have opened them anyway (docs/adr/0027).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.http import FileResponse, Http404, HttpRequest, HttpResponse, HttpResponseBase
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_http_methods

from app.accounts import shared_gate
from app.audit.enums import SecurityEventType
from app.audit.services import record_security_event
from app.core.errors import DomainError
from app.core.http import content_disposition
from app.documents import inline
from app.documents.services import evidence_storage
from app.legacy_import import opinion_links
from app.legacy_import.opinion_access import (
    may_manage_archive_links,
    may_use_opinion_queue,
    require_archive_link_reviewer,
    require_archive_reader,
)
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


# ---------------------------------------------------------------------------
# What the page may say about a Matter
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MatterView:
    """One Matter as this reader may see it, or the fact that they may not.

    `matter` is ``None`` whenever the reader may not read it, rather than the
    template being trusted to check a flag beside a live object. A hidden Matter
    still reachable through the context is one ``{{ }}`` away from being
    rendered, and the mistake would look exactly like working code.
    """

    matter: Any = None

    @property
    def visible(self) -> bool:
        return self.matter is not None


@dataclass(frozen=True)
class LinkRow:
    link: Any
    view: MatterView
    may_withdraw: bool


@dataclass(frozen=True)
class CandidateRow:
    candidate: Any
    view: MatterView
    may_link: bool


def _visible_matter_ids(user: Any, matter_ids: Any) -> set[Any]:
    """Which of these Matters this reader may read. One query, never one per row.

    The obvious shape — asking `visible_to(...).filter(pk=...).exists()` inside
    the loop that renders links — is correct and costs a query per relationship
    on a page whose whole point is that one letter can concern several Matters.
    A single ``IN`` over the ids the page already has says the same thing once.
    """
    from app.matters.models import Matter

    wanted = {identifier for identifier in matter_ids if identifier is not None}
    if not wanted:
        return set()
    return set(Matter.objects.visible_to(user).filter(pk__in=wanted).values_list("pk", flat=True))


# ---------------------------------------------------------------------------
# Browsing
# ---------------------------------------------------------------------------


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

    counts = archive_counts(request.user)
    return render(
        request,
        "legacy_import/opinion_archive_browse.html",
        {
            "rows": rows,
            "page": page,
            "total": total,
            "counts": counts,
            # The review workload is the unlinked remainder, so the page names
            # it rather than making somebody assemble it out of the filter row.
            # Subtracted from figures already counted under the same boundary —
            # no extra query, and no way for the shortcut to disagree with the
            # coverage strip above it.
            "unlinked_count": counts["total"] - counts["linked"],
            "filters": filters,
            "refusal": refusal,
            "states": OpinionCandidateState.choices,
            "years": _years(request.user),
            # Whether this corpus can currently be searched by what the letters
            # *say*. Every held text is BLOCKED in production, so controls
            # promising body search would be promising something the archive
            # cannot do — and a reader who searched a phrase and got nothing
            # would reasonably conclude the Chamber never wrote it
            # (Stage-2H.2, brief 35, 37).
            "body_search_available": counts["with_body"] > 0,
            # Offered by capability, not by assumption. A department head may
            # read the archive and may not work the candidate queue, and a
            # button that can only ever produce a 403 is worse than no button.
            "can_use_opinion_queue": may_use_opinion_queue(request.user),
            "can_manage_links": may_manage_archive_links(request.user),
            "shared_gate_mode": shared_gate.is_shared_gate(),
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
    links = opinion_links.links_for(binary)

    # Both populations resolved against one visibility query. The letter is the
    # archive's to serve; every Matter named beside it belongs to the register,
    # and the register's rule is the one that answers for it.
    visible = _visible_matter_ids(
        request.user,
        [link.matter_id for link in links] + [candidate.matter_id for candidate in candidates],
    )
    linked_matter_ids = {link.matter_id for link in links}
    can_manage_links = may_manage_archive_links(request.user)

    link_rows = [
        LinkRow(
            link=link,
            view=MatterView(link.matter if link.matter_id in visible else None),
            # Withdrawing names the Matter it withdrew, so it is offered only
            # where the Matter could have been named anyway. A relationship this
            # reader may not read is acknowledged and left alone.
            may_withdraw=(
                can_manage_links
                and link.basis == ArchiveLinkBasis.REVIEWED
                and link.matter_id in visible
            ),
        )
        for link in links
    ]

    live_candidates = [
        candidate for candidate in candidates if candidate.state != OpinionCandidateState.SUPERSEDED
    ]
    candidate_rows = [
        CandidateRow(
            candidate=candidate,
            view=MatterView(candidate.matter if candidate.matter_id in visible else None),
            # The one-click file. Only where there is something to file, where
            # this reviewer could have opened it themselves, and where it is not
            # filed already.
            may_link=(
                can_manage_links
                and candidate.matter_id is not None
                and candidate.matter_id in visible
                and candidate.matter_id not in linked_matter_ids
            ),
        )
        for candidate in live_candidates
    ]

    return render(
        request,
        "legacy_import/opinion_archive_detail.html",
        {
            "binary": binary,
            "text": getattr(binary, "text", None),
            "row": getattr(binary, "search_document", None),
            "occurrences": opinion_links.occurrences_for(binary),
            "link_rows": link_rows,
            # How many relationships exist that this reader may not be told
            # about. The page has to be able to say "there is one, and not
            # here": rendering the empty state instead would tell somebody the
            # letter is unfiled while the projection records that it is not.
            "hidden_link_count": sum(1 for row in link_rows if not row.view.visible),
            "candidate_rows": candidate_rows,
            "hidden_candidate_count": sum(
                1
                for row in candidate_rows
                if row.candidate.matter_id is not None and not row.view.visible
            ),
            # Retired proposals are shown, not hidden. "We used to think this,
            # and here is what replaced it" is often the most useful sentence
            # on the page for somebody auditing the migration.
            "superseded": [
                candidate
                for candidate in candidates
                if candidate.state == OpinionCandidateState.SUPERSEDED
            ],
            "can_manage_links": can_manage_links,
            "shared_gate_mode": shared_gate.is_shared_gate(),
            "nav_active": "haldus",
        },
    )


@login_required
@require_http_methods(["POST"])
def archive_link(request: HttpRequest, pk: Any) -> HttpResponse:
    """Add or withdraw one archive-to-Matter relationship."""
    require_archive_link_reviewer(request.user)
    binary = get_object_or_404(OpinionArchiveBinary, pk=pk)
    action = request.POST.get("action", "")
    reference = request.POST.get("viide", "").strip()
    identifier = request.POST.get("teema", "").strip()

    # Two ways in, for two different callers. A person types a reference; the
    # withdraw button beside an existing link — and the one-click file beside a
    # candidate — post the Matter's id, because a register archive row may
    # legitimately have no reference at all and a link to one must still be
    # removable.
    #
    # Both resolve inside this reviewer's own visible population. Querying
    # `Matter` directly, as this did, turns the form into an oracle: type a
    # reference you are not entitled to and the confirmation message reads its
    # title back to you.
    from app.matters.models import Matter

    population = Matter.objects.visible_to(request.user)
    matter = (
        _matter_by_id(population, identifier)
        if identifier
        else _matter_by_reference(population, reference)
    )
    if matter is None:
        # One message for "there is no such Matter" and for "there is one you
        # may not read", echoing nothing but what was typed. Distinguishing them
        # would answer the question the boundary exists to refuse. Nothing is
        # created here either: an archive file naming something is not authority
        # to open a register entry for it.
        messages.error(
            request,
            f"Teemat viitega „{reference or identifier}“ ei leitud või ei ole see selles "
            "vaates saadaval. Seos saab osutada ainult olemasolevale nähtavale teemale.",
        )
        return redirect("legacy_import:opinion_archive_detail", pk=binary.pk)

    try:
        if action == "unlink":
            opinion_links.unlink_matter(binary=binary, matter=matter, actor=request.user)
            messages.success(request, _named(matter) + " seos on eemaldatud.")
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
                _named(matter)
                + (" seos on lisatud." if created else " seos oli juba olemas.")
                + " See ei loo arvamust.",
            )
    except DomainError as error:
        messages.error(request, str(error))

    return redirect("legacy_import:opinion_archive_detail", pk=binary.pk)


def _named(matter: Any) -> str:
    """How a Matter is named back to the reviewer.

    Its reference when it has one, and its title when it does not: archive rows
    imported without a register reference are exactly the ones a message saying
    "Teemaga  seos on lisatud" would leave unidentifiable.

    Only ever reached with a Matter that came out of the reviewer's own visible
    population, so naming it reveals nothing they could not already read.
    """
    return f"Teemaga {matter.display_reference or matter.title[:60]}"


def _matter_by_id(population: Any, identifier: str) -> Any:
    """Resolve a Matter id posted by the interface itself. Never creates."""
    import uuid

    try:
        parsed = uuid.UUID(identifier)
    except ValueError:
        return None
    return population.filter(pk=parsed).first()


def _matter_by_reference(population: Any, reference: str) -> Any:
    """Resolve ``YYYY_N`` to a Matter, or nothing. Never creates."""
    parts = reference.replace("/", "_").replace("-", "_").split("_")
    if len(parts) != 2 or not all(part.isdigit() for part in parts):
        return None
    return population.filter(reference_year=int(parts[0]), reference_number=int(parts[1])).first()


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
        # Through the shared-gate helper, like every other served file. In that
        # mode `actor` is the *persona* somebody selected, and a row naming it
        # without saying so would read as that individual having signed for real
        # correspondence. `authenticated_via` is what keeps the record honest
        # about how much identity stands behind it — and that honesty is the
        # condition on which this mode may serve the archive at all
        # (app/accounts/shared_gate.py, docs/adr/0016, 0027).
        detail=shared_gate.audit_detail(
            request,
            source="opinion_archive",
            sha256=binary.sha256,
            disposition="attachment" if as_attachment else "inline",
        ),
    )

    if as_attachment:
        response = FileResponse(handle, content_type=binary.mime_type or "application/pdf")
        response["X-Content-Type-Options"] = "nosniff"
        response["Content-Disposition"] = content_disposition("attachment", filename)
        return response

    response = FileResponse(handle, content_type=inline.inline_mime_for(filename))
    return inline.apply_inline_headers(response, filename=filename)
