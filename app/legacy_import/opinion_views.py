"""The opinion-archive reconciliation queue.

An operator surface under `/haldus/`, not a lawyer's. Roughly two thirds of the
archive cannot be filed deterministically, and several hundred pending migration
decisions in the middle of a lawyer's navigation is not a feature (Stage-2H
brief 62).

**The queue records decisions; the importer executes them.** A reviewer marks
which Matter a letter belongs to and whether the evidence supports calling it
sent; the next ``opinion_archive apply`` — the process that actually holds the
archive and can write the bytes — creates the Submission. Splitting it that way
keeps every byte-writing path in one place and keeps a web request from needing
a 105 MB ZIP to be mounted (brief 25, 63).

Every decision is idempotent. A reviewer who clicks twice, or whose browser
retries a POST, changes the same row to the same state.
"""

from __future__ import annotations

import datetime
from typing import Any

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Count
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_http_methods

from app.legacy_import.opinion_access import require_opinion_queue_operator
from app.legacy_import.opinion_archive import OpinionMatchCandidate
from app.legacy_import.opinion_enums import (
    IRREVERSIBLE_CANDIDATE_STATES,
    OpinionCandidateState,
    OpinionMatchClass,
)


def _require_administrator(request: HttpRequest) -> None:
    """Reconciliation is migration work, and migration work is administrative.

    Technical administration does not on its own open ordinary restricted
    business content; what it opens is this queue, whose rows deliberately show
    filenames, dates and references rather than document text (AGENTS.md,
    brief 62, 71).

    Delegated to `may_use_opinion_queue` rather than compared here, so that the
    browse page deciding whether to *offer* this queue and this view deciding
    whether to *serve* it read the same rule. P3.3 widened who may read the
    archive without widening this, and two copies of "who works the queue" that
    agree today are two copies that can stop agreeing (docs/adr/0028).
    """
    require_opinion_queue_operator(request.user)


@login_required
def opinion_queue(request: HttpRequest) -> HttpResponse:
    """What the reconciliation could not settle, with its evidence beside it."""
    _require_administrator(request)
    state = request.GET.get("olek") or OpinionCandidateState.PENDING
    match_class = request.GET.get("klass") or ""

    candidates = OpinionMatchCandidate.objects.select_related(
        # `decided_by` is rendered beside every decided row. Without it here the
        # page issues one query per row the moment anything but PENDING is
        # selected — 200 of them, on a surface whose whole point is working
        # through a backlog.
        "item",
        "matter",
        "decided_by",
    ).prefetch_related("item__metadata_rows")
    if state:
        candidates = candidates.filter(state=state)
    if match_class:
        candidates = candidates.filter(match_class=match_class)

    counts = _pending_counts_by_class()
    return render(
        request,
        "legacy_import/opinion_queue.html",
        {
            "candidates": list(candidates[:200]),
            "total": candidates.count(),
            "counts": counts,
            "state": state,
            "match_class": match_class,
            "classes": OpinionMatchClass.choices,
            "states": OpinionCandidateState.choices,
            "nav_active": "haldus",
        },
    )


def _pending_counts_by_class() -> dict[str, int]:
    """How much unreviewed work each class still holds. One query, not seven.

    Every class is present in the result whether or not it has rows, because
    the filter row above reads these counts and a class that vanished from the
    strip would look like a class that does not exist rather than one that is
    finished.
    """
    tally: dict[str, int] = dict.fromkeys(OpinionMatchClass.values, 0)
    rows = (
        OpinionMatchCandidate.objects.filter(state=OpinionCandidateState.PENDING)
        .order_by()
        .values("match_class")
        .annotate(total=Count("id"))
    )
    for row in rows:
        if row["match_class"] in tally:
            tally[row["match_class"]] = row["total"]
    return tally


@login_required
@require_http_methods(["POST"])
def opinion_decide(request: HttpRequest, pk: Any) -> HttpResponse:
    """Record one decision, exactly once."""
    _require_administrator(request)
    candidate = get_object_or_404(
        OpinionMatchCandidate.objects.select_related("item", "matter"), pk=pk
    )
    if candidate.state in IRREVERSIBLE_CANDIDATE_STATES:
        # The queue only renders decision controls on a PENDING row, so this is
        # a crafted or stale post rather than a click — and "not offered in the
        # interface" is not a guard. An APPLIED row is named by a canonical
        # Submission as its justification and a SUPERSEDED one is the record of
        # a belief that was replaced; a decision written over either leaves the
        # provenance chain describing something that is no longer true.
        messages.error(
            request,
            f"Kandidaat on olekus „{candidate.get_state_display()}“ ja seda ei saa uuesti "
            "otsustada. Vajadusel tuleb kanooniline kirje eraldi tagasi võtta.",
        )
        return redirect("legacy_import:opinion_queue")

    handlers = {
        "link": _link_to_matter,
        "confirm-sent": _confirm_sent,
        "reject": lambda c, r: _mark(c, r, OpinionCandidateState.REJECTED),
        "duplicate": lambda c, r: _mark(c, r, OpinionCandidateState.DUPLICATE),
        "not-opinion": lambda c, r: _mark(c, r, OpinionCandidateState.NOT_AN_OPINION),
        "defer": lambda c, r: _mark(c, r, OpinionCandidateState.DEFERRED),
    }
    handler = handlers.get(request.POST.get("decision", ""))
    if handler is None:
        messages.error(request, "Tundmatu otsus.")
        return redirect("legacy_import:opinion_queue")

    try:
        message = handler(candidate, request)
    except ValueError as error:
        messages.error(request, str(error))
        return redirect("legacy_import:opinion_queue")

    messages.success(request, message)
    return redirect("legacy_import:opinion_queue")


def _reviewer(request: HttpRequest) -> Any:
    """The signed-in person, as the model wants them.

    Every view here is ``@login_required``, so ``request.user`` is a real User —
    but the type checker sees the ``AnonymousUser`` union that the decorator
    cannot narrow, and a cast at each call site is worse than one helper that
    says why.
    """
    return request.user


def _chosen_matter(candidate: OpinionMatchCandidate, request: HttpRequest) -> Any:
    from app.matters.models import Matter

    matter_id = request.POST.get("matter") or candidate.matter_id
    if not matter_id:
        raise ValueError("Tuleb valida teema.")
    matter = Matter.objects.filter(pk=matter_id).first()
    if matter is None:
        raise ValueError("Valitud teemat ei leitud.")
    return matter


def _link_to_matter(candidate: OpinionMatchCandidate, request: HttpRequest) -> str:
    """Confirm the Matter without claiming the letter was sent.

    The useful middle answer. "This file belongs to this matter" and "Koda sent
    this on this date" are different claims, and a reviewer who can make the
    first should not have to make the second in order to record it (brief 26).
    """
    matter = _chosen_matter(candidate, request)
    candidate.review_approves_submission = False
    _mark(candidate, request, OpinionCandidateState.LINKED, matter=matter)
    return f"Fail märgiti teema {matter.display_reference or matter.title[:40]} juurde."


def _confirm_sent(candidate: OpinionMatchCandidate, request: HttpRequest) -> str:
    """Approve a canonical SENT record, once a date exists to stand behind it.

    The threshold does not soften because a person pressed a button. What the
    reviewer supplies is the identity the evidence could not settle and, where
    the register was silent, a date they can defend — recorded as a reviewed
    decision, never as a register value (brief 25, 26).
    """
    matter = _chosen_matter(candidate, request)
    supplied = (request.POST.get("sent_date") or "").strip()
    reviewed_date: datetime.date | None = None
    if supplied:
        try:
            reviewed_date = datetime.date.fromisoformat(supplied)
        except ValueError as error:
            raise ValueError("Kuupäev peab olema kujul AAAA-KK-PP.") from error

    if reviewed_date is None and candidate.excel_sent_date is None:
        raise ValueError(
            "Kaitstavat väljasaatmise kuupäeva ei ole. Sisesta kuupäev või kasuta "
            "„Seo teemaga“, mis säilitab tõendi ilma saatmist väitmata."
        )

    candidate.review_approves_submission = True
    candidate.reviewed_sent_date = reviewed_date
    _mark(candidate, request, OpinionCandidateState.LINKED, matter=matter)
    return (
        "Saatmine kinnitatud. Kanooniline kirje tekib järgmisel "
        "`opinion_archive apply` käivitusel, mis kirjutab ka lõpliku tõendi."
    )


def _mark(
    candidate: OpinionMatchCandidate,
    request: HttpRequest,
    state: str,
    *,
    matter: Any = None,
) -> str:
    candidate.state = state
    candidate.decided_by = _reviewer(request)
    candidate.decided_at = timezone.now()
    candidate.decision_note = (request.POST.get("note") or "")[:2000]
    if matter is not None:
        candidate.matter = matter
    candidate.save(
        update_fields=[
            "state",
            "decided_by",
            "decided_at",
            "decision_note",
            "matter",
            "review_approves_submission",
            "reviewed_sent_date",
            "updated_at",
        ]
    )
    return f"Märgitud: {candidate.get_state_display()}."
