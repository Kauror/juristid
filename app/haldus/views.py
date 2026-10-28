"""The Haldus shell's own pages: Ülevaade, Osakonna töökorraldus, Süsteemi seis.

Every view decides for itself, from `app.haldus.sections`, whether this reader
may open it, and answers 404 otherwise — the convention every restricted
surface here follows, because a 403 would confirm the page is there. None of
them shows business content beyond counts already shown to the same reader
elsewhere, and none runs a command, a query a reader chose, or anything that
writes (docs/adr/0186).
"""

from __future__ import annotations

from typing import Any

from django.conf import settings
from django.db import DatabaseError
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods

from app.accounts import admin_session, administration, capabilities
from app.accounts.enums import AuthMode
from app.core.authorization import (
    effective_capabilities,
    may_review_work_victory,
    may_view_department_management,
)
from app.haldus.sections import Section, section, visible_sections
from app.legacy_import.lovable_sync_labels import LOVABLE_FIELD_LABELS, LOVABLE_REASONS

NOT_HERE = "Lehte ei leitud."


def _context(request: HttpRequest, active: str, **extra: Any) -> dict[str, Any]:
    return {
        "haldus_sections": visible_sections(request),
        "haldus_active": active,
        "nav_active": "haldus",
        **extra,
    }


def _require(request: HttpRequest, key: str) -> Section:
    found = section(key)
    if not found.visible(request):
        raise Http404(NOT_HERE)
    # A Haldus page is administration activity; business pages are not
    # (`admin_session.note_activity`).
    admin_session.note_activity(request)
    return found


def _session_summary(request: HttpRequest) -> dict[str, Any] | None:
    """Who the verified administrator is, and what that session may do — for display."""
    principal = admin_session.verified_administrator(request)
    if principal is None:
        return None
    held = effective_capabilities(principal)
    return {
        "principal": principal,
        "capabilities": [
            capabilities.TEXT[name].label
            for name in capabilities.ADMINISTRATIVE_CAPABILITIES
            if name in held
        ],
        "technical": admin_session.is_technical_administrator(request),
        "idle_minutes": admin_session.idle_seconds() // 60,
        "absolute_minutes": admin_session.absolute_seconds() // 60,
        "shared_gate": admin_session.is_available(),
    }


@never_cache
@require_http_methods(["GET"])
def index(request: HttpRequest) -> HttpResponse:
    """Haldus → Ülevaade: the administrator session, continuity, sections, Rakendus.

    Opens for every signed-in reader since 2026-10-28 (`may_open_overview`),
    because *Rakendus* — Uuendused, Kiirklahvid, the running build and
    «Taasta plokkide vaikeseaded» — is for everybody. What is privileged on it
    follows the verified administrator session, never the persona: the session
    and continuity panels, and the list of sections, each by its own predicate.
    The way to the administrator sign-in is in the persona menu now
    (templates/components/persona_switcher.html), the same for every reader.
    """
    _require(request, "ulevaade")
    summary = _session_summary(request)
    return render(
        request,
        "haldus/index.html",
        _context(
            request,
            "ulevaade",
            session=summary,
            # The two-administrator rule, for whoever administers: the persona's
            # holding of the capability is not enough to be shown it.
            continuity=administration.continuity_status() if summary is not None else None,
            others=[item for item in visible_sections(request) if item.key != "ulevaade"],
        ),
    )


def _candidate_victories(user: Any) -> int | None:
    """How many Töövõit candidates wait for review, for somebody who may review them."""
    if not may_review_work_victory(user):
        return None
    from app.intelligence.enums import WorkVictoryStatus
    from app.intelligence.models import MatterWorkVictory

    return (
        MatterWorkVictory.objects.visible_to(user)
        .active()
        .filter(status=WorkVictoryStatus.CANDIDATE)
        .count()
    )


@never_cache
@require_http_methods(["GET"])
def osakond(request: HttpRequest) -> HttpResponse:
    """Osakonna töökorraldus: links to the existing Osakond surfaces, and one count.

    Not a second Osakond dashboard: the management view is where it always was
    (`matters:department`). This page gathers the way in, the colleagues'
    desks the same capability opens, and how many work victories wait for the
    reader's review.
    """
    _require(request, "osakond")
    from app.accounts.selectors import department_workers

    user = request.user
    return render(
        request,
        "haldus/osakond.html",
        _context(
            request,
            "osakond",
            colleagues=list(department_workers()) if may_view_department_management(user) else [],
            candidate_victories=_candidate_victories(user),
        ),
    )


def _safely(read: Any) -> Any:
    try:
        return read()
    except DatabaseError:
        return None


def _search_freshness() -> dict[str, Any] | None:
    from app.search.freshness import status

    state = _safely(status)
    if state is None:
        return None
    stale_after = int(getattr(settings, "SEARCH_REBUILD_DEBT_STALE_SECONDS", 0) or 0)
    seconds = 0 if state.is_clear else int(state.seconds_owed())
    return {
        "clear": state.is_clear,
        "owed": state.owed,
        "failed_attempts": state.failed_attempts,
        "minutes_owed": seconds // 60,
        # The same verdict `production_status` reaches: pending debt is the
        # system working, a failed or outlived one is a finding.
        "ok": state.is_clear or (not state.failed_attempts and seconds < stale_after),
    }


def _readiness() -> dict[str, Any] | None:
    from app.core import deployment

    report = _safely(deployment.readiness_report)
    if report is None:
        return None
    return {"ok": report.ok, "problems": len(report.problems)}


def _processing_errors() -> dict[str, Any]:
    """Failed text extraction and failed OneNote content, as counts only."""
    from app.documents.enums import ExtractionState
    from app.documents.models import DocumentVersion
    from app.legacy_import.enums import OneNoteContentStatus
    from app.legacy_import.models import MatterSourceReference

    return {
        "extraction_failed": _safely(
            lambda: DocumentVersion.objects.filter(extraction_state=ExtractionState.FAILED).count()
        ),
        "onenote_failed": _safely(
            lambda: MatterSourceReference.objects.filter(
                onenote_content_status=OneNoteContentStatus.FAILED
            ).count()
        ),
    }


@never_cache
@require_http_methods(["GET"])
def susteem(request: HttpRequest) -> HttpResponse:
    """Süsteemi seis: version, search freshness, readiness, processing errors. Counts only.

    For a verified technical administrator. Nothing here names a record, a
    file, a person or a secret, and nothing runs a command: it reads the same
    aggregate verdicts `manage.py production_status` prints, and says which
    command gives the full answer.
    """
    _require(request, "susteem")
    return render(
        request,
        "haldus/susteem.html",
        _context(
            request,
            "susteem",
            revision=settings.APPLICATION_REVISION,
            environment=settings.APPLICATION_ENVIRONMENT,
            stage=settings.APPLICATION_STAGE,
            auth_mode=AuthMode(admin_session.shared_gate.current_mode()).label,
            freshness=_search_freshness(),
            readiness=_readiness(),
            errors=_processing_errors(),
            continuity=administration.continuity_status(),
        ),
    )


@never_cache
@require_http_methods(["GET"])
def lovable(request: HttpRequest) -> HttpResponse:
    """Lovable sünkroonimine (docs/adr/0192): the night's result and what waits.

    For a verified technical administrator, like Süsteemi seis: the run ledger
    and its counts. **What changed is business content**, so a pending change is
    shown with its wording only for a Teema this reader may already open
    (`Matter.objects.visible_to`); the rest are counted, never named.
    """
    from app.legacy_import.lovable_sync import STALE_AFTER, summary_lines, sync_health
    from app.legacy_import.lovable_sync_models import SyncOutcome
    from app.matters.models import Matter

    _require(request, "lovable")
    from app.legacy_import.lovable_sync_models import LovableSyncRun

    health = sync_health()
    # The newest run of any kind, `Kontrolli muudatusi` included, is what the
    # table below explains; the verdict above it is the nightly job's alone.
    latest = LovableSyncRun.objects.order_by("-started_at").first()
    pending: list[dict[str, Any]] = []
    hidden = 0
    if latest is not None:
        visible = set(Matter.objects.visible_to(request.user).values_list("pk", flat=True))
        decisions = latest.decisions.exclude(
            outcome__in=[SyncOutcome.UNCHANGED, SyncOutcome.ALREADY_CONVERGED]
        ).select_related("state", "state__matter")
        for decision in decisions:
            if decision.state.matter_id not in visible:
                hidden += 1
                continue
            pending.append(
                {
                    "matter": decision.state.matter,
                    "field": LOVABLE_FIELD_LABELS.get(decision.field, decision.field),
                    "outcome": SyncOutcome(decision.outcome).label,
                    "reason": LOVABLE_REASONS.get(decision.reason, decision.reason),
                    "baseline": _shown(decision.baseline),
                    "current": _shown(decision.current),
                    "source": _shown(decision.source),
                }
            )
    return render(
        request,
        "haldus/lovable.html",
        _context(
            request,
            "lovable",
            health=health,
            # The verdict's own run, never a `Kontrolli muudatusi` that came after
            # a failed night and would read like a success.
            summary=summary_lines(health.last_run) if health.last_run is not None else [],
            table_run=latest,
            stale_hours=int(STALE_AFTER.total_seconds() // 3600),
            pending=pending,
            hidden=hidden,
        ),
    )


def _shown(value: Any) -> str:
    if value is None or value == "":
        return "—"
    if isinstance(value, dict):
        return " · ".join(str(v) for k, v in value.items() if k != "id" and v not in (None, ""))
    if isinstance(value, list):
        return (
            "; ".join(
                " ".join(str(x) for x in item if x) if isinstance(item, list) else str(item)
                for item in value
            )
            or "—"
        )
    if isinstance(value, bool):
        return "jah" if value else "ei"
    return str(value)


@never_cache
@require_http_methods(["POST"])
def lovable_check(request: HttpRequest) -> HttpResponse:
    """`Kontrolli muudatusi`: the newest stored snapshot against Juristid now.

    Report-only. It reads no source and writes no Teema; it records one run in
    the ledger, so what it found is what the page then shows."""
    from app.legacy_import.lovable_sync import SyncRefused, run_sync
    from app.legacy_import.lovable_sync_models import SyncTrigger

    _require(request, "lovable")
    try:
        run_sync(trigger=SyncTrigger.CHECK)
    except SyncRefused:
        pass  # the page shows the control state and the newest run as they are
    return redirect("haldus:lovable")
