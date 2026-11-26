"""The approval page: the trusted component where an AI-prepared change is approved and saved.

``/mcp-ligipaas/muudatus/<proposal uuid>/`` on `web`, behind the shared gate
and the persona like every page (docs/adr/0189, amended by docs/adr/0234). A
model's tool call alone never changes a record: its `prepare` stores a
proposal, and only its own person, here, with their own MCP authenticator,
approves it — and the approval *is* the save.

**Nothing the write gateway stored is trusted (SEC-009).** The gateway is
reachable from the AI client's side, so its proposal row — canonical
arguments, state, preview — is writer-controlled. This page derives the change
again from the proposal's validated arguments with the operation's own code
(`app.mcp_writes.review.derive`), shows *that* preview, and refuses a proposal
whose stored canonical arguments or target differ from what the arguments
resolve to now (``mismatch``) or whose state moved on (``stale``). The form
carries the **binding digest** of everything shown (`review.binding_digest`);
«Kinnita» is accepted only with the same digest, computed again under the
proposal's row lock and the operation's canonical locks, so any change between
display, submission and execution means «look again», never «approve
something else».

Who sees what:

* **One generic refusal** for everything that is not this persona's own
  pending business: no such proposal, somebody else's proposal, a grant that
  is not ACTIVE, an account that may not write through it (`may_write`). The
  page does not say which, so a guessed or forwarded link teaches nothing.
* Its own person sees the derived preview — autoescaped — with the operation,
  the target and its link, each change current → proposed, the consequences,
  what does not change, the warnings and the expiry. A stale, mismatched,
  format-1 proposal, or one whose ChatGPT connection (token family) is no
  longer live, gets no approve button.

Approving is one explicit POST for one proposal, CSRF-protected, with the
binding digest and either

* the grant's current authenticator code, checked by
  `app.mcp_access.services.confirm_write_code` — the same single-use step,
  lock ladder and per-session/address throttle (the very same counters) as
  `/oauth/authorize`; or
* a code this same gate session typed less than `MCP_WRITE_REVERIFY_SECONDS`
  ago, kept in the session as a keyed binding of the grant, the account, its
  `security_epoch` and the gate pass. With ``MCP_WRITE_REVERIFY_SECONDS=0``
  (production, owner's decision 2026-10-29) there is no such window.

Every check that needs no code — pending, unexpired, write right, live
connection, derivation and binding — is made **before** the code is spent, so
a proposal that cannot be approved never consumes a valid code. After the code,
in **one transaction**: the proposal row is locked, the operation's canonical
locks are taken, everything is checked and derived again and the binding must
still match; then the approval row (`McpWriteApproval`, format 2, with the
binding digest and copies of the canonical arguments, state and preview the
person approved) is written, and the operation's canonical service runs in a
savepoint under the MCP audit provenance (the person as principal, the
proposal's correlation id). The proposal becomes EXECUTED with its receipt,
and `APPROVED` and `COMMITTED` events are written. A domain refusal at that
point leaves the approval, a FAILED proposal and its event, and nothing of the
business change; an unexpected error rolls the whole approval back (the
proposal stays PENDING, the person may try again).

Every response is no-store, may not be framed, and carries the application's
CSP. With MCP writes off every route is a 404 for every method.
"""

from __future__ import annotations

import functools
import hmac
import json
import logging
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any

from django.conf import settings
from django.contrib.auth.views import redirect_to_login
from django.db import transaction
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import render
from django.utils import timezone
from django.utils.cache import add_never_cache_headers
from django.utils.crypto import salted_hmac
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods

from app.accounts import shared_gate
from app.core.browser_policy import CONTENT_SECURITY_POLICY
from app.core.client_ip import client_ip
from app.mcp_access import config as access_config
from app.mcp_access import services as access_services
from app.mcp_access.models import GrantStatus, McpAccessGrant
from app.mcp_writes import access, approval, operations, review
from app.mcp_writes.models import (
    CURRENT_FORMAT,
    ApprovalMethod,
    McpWriteApproval,
    McpWriteEventType,
    McpWriteProposal,
    ProposalStatus,
)
from app.mcp_writes.proposals import record

logger = logging.getLogger("app.mcp_writes")

#: A code this gate session typed for an approval, and when (see the module docstring).
SESSION_RECENT = "mcp_writes:recent_verification"
_RECENT_SALT = "app.mcp_writes.approval_views.recent_verification"

GENERIC_REFUSAL = (
    "Seda muudatust ei saa siin kinnitada. Ava link uuesti oma ChatGPT vestlusest ja "
    "kontrolli, et tegutsed Juristidis iseendana."
)
BAD_CODE = (
    "Kood ei sobi või sellel kasutajal pole muudatuste kinnitamise õigust. Sisesta "
    "autentija praegune kood; kui see ikka ei sobi, pöördu Juristidi haldaja poole."
)
SUSPENDED = (
    "Ligipääs on peatatud, sest valesid koode sisestati liiga palju. "
    "Selle avab uuesti Juristidi haldaja."
)
CHOOSE = "Vali «Kinnita» või «Lükka tagasi»."
DONE = "Kinnitatud ja salvestatud. ChatGPT saab tulemuse, kui küsib seda uuesti."
CHANGED = (
    "Muudatus või kirje on vahepeal muutunud. Vaata allolev eelvaade uuesti üle ja kinnita "
    "siis, kui see on endiselt õige."
)
MISMATCH = (
    "Seda muudatust ei saa kinnitada, sest see ei vasta enam sellele, mida ChatGPT ette "
    "valmistas. Palu ChatGPT-l muudatus uuesti ette valmistada."
)
DISCONNECTED = (
    "ChatGPT ühendus, mis selle muudatuse ette valmistas, ei kehti enam. Ühenda Juristid "
    "ChatGPT-s uuesti ja palu muudatus uuesti ette valmistada."
)
RETRY = "Muudatust ei õnnestunud salvestada. Midagi ei muudetud; proovi uuesti."
FAILED_TEXT = "Seda muudatust ei õnnestunud salvestada. Midagi ei muudetud."
DECLINED = "Lükkasid selle muudatuse tagasi. Midagi ei salvestatud."
STALE = (
    "Kirje on pärast selle muudatuse ettevalmistamist muutunud, seega seda ei saa enam "
    "kinnitada. Palu ChatGPT-l muudatus uuesti ette valmistada."
)
EXPIRED = "Selle muudatuse kinnitamise aeg on läbi. Palu ChatGPT-l see uuesti ette valmistada."

#: What a proposal that is no longer waiting says to its own person.
STATUS_TEXT: dict[str, str] = {
    # An older release's approval waiting for its commit; migration 0002
    # retires every one, so this is only ever a rolled-back release's.
    ProposalStatus.APPROVED: EXPIRED,
    ProposalStatus.EXECUTED: "See muudatus on kinnitatud ja salvestatud.",
    ProposalStatus.DECLINED: DECLINED,
    ProposalStatus.EXPIRED: EXPIRED,
    ProposalStatus.FAILED: FAILED_TEXT,
    ProposalStatus.STALE: STALE,
}


def writes_enabled_only(view: Callable[..., HttpResponse]) -> Callable[..., HttpResponse]:
    """A 404 for every method while MCP writes are off — before anything else is said."""

    @functools.wraps(view)
    def wrapper(request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        if not access.writes_enabled():
            raise Http404("MCP writes are not enabled on this deployment.")
        return view(request, *args, **kwargs)

    return wrapper


# -- responses ------------------------------------------------------------------


def _harden(response: HttpResponse) -> HttpResponse:
    add_never_cache_headers(response)
    response["Cache-Control"] = "no-store, no-cache, must-revalidate, private, max-age=0"
    response["Pragma"] = "no-cache"
    response["Content-Security-Policy"] = CONTENT_SECURITY_POLICY
    response["X-Frame-Options"] = "DENY"
    # Deliberately no `Referrer-Policy: no-referrer` here: with it a browser
    # sends `Origin: null` (or no Origin and no Referer) on this page's own
    # same-origin POST and CSRF refuses «Kinnita». The global
    # SECURE_REFERRER_POLICY = "same-origin" already keeps the address off
    # every other site.
    return response


def _refusal_page(request: HttpRequest) -> HttpResponse:
    response = render(request, "mcp_writes/refused.html", {"message": GENERIC_REFUSAL}, status=404)
    return _harden(response)


def _text(value: Any) -> str:
    if value is None or value == "":
        return "—"
    if isinstance(value, bool):
        return "jah" if value else "ei"
    if isinstance(value, list | tuple):
        return ", ".join(_text(item) for item in value) or "—"
    if isinstance(value, dict):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return str(value)


def _safe_link(url: Any) -> str:
    """A link only to this site: a path, or the public origin. Anything else is not linked."""
    if not isinstance(url, str) or not url:
        return ""
    if url.startswith("/") and not url.startswith("//") and "\\" not in url:
        return url
    origin = access_config.issuer()
    if origin and url.startswith(origin + "/"):
        return url
    return ""


def _strings(value: Any) -> list[str]:
    return [str(item) for item in value] if isinstance(value, list) else []


def _preview_context(preview: Any) -> dict[str, Any] | None:
    """The template's view of a preview *the page derived* (or an approval row recorded).

    Never called with `McpWriteProposal.preview`: that is the gateway's copy."""
    if not isinstance(preview, dict) or not preview:
        return None
    target = preview.get("target") if isinstance(preview.get("target"), dict) else None
    changes = [
        {
            "label": str(change.get("label") or change.get("field") or ""),
            "current": _text(change.get("current")),
            "proposed": _text(change.get("proposed")),
        }
        for change in preview.get("changes") or []
        if isinstance(change, dict)
    ]
    return {
        "summary": str(preview.get("summary") or ""),
        "target": (
            {
                "reference": str(target.get("reference") or ""),
                "title": str(target.get("title") or ""),
                "url": _safe_link(target.get("url")),
                "record_label": str(target.get("record_label") or ""),
            }
            if target
            else None
        ),
        "changes": changes,
        "consequences": _strings(preview.get("consequences")),
        "unchanged": _strings(preview.get("unchanged")),
        "warnings": _strings(preview.get("warnings")),
    }


def _approved_preview(proposal: McpWriteProposal) -> Any:
    """What the person approved, from the approval row `web` wrote (format 2), or None."""
    approval = McpWriteApproval.objects.filter(proposal=proposal).first()
    if approval is None or approval.format_version != CURRENT_FORMAT:
        return None
    return approval.preview


def _page(
    request: HttpRequest,
    proposal: McpWriteProposal,
    *,
    can_approve: bool,
    reviewed: review.Reviewed | None = None,
    notice: str = "",
    error: str = "",
    done: str = "",
    status: int = 200,
) -> HttpResponse:
    op = operations.get(proposal.operation)
    user: Any = request.user
    grant = proposal.grant
    if reviewed is not None and not reviewed.problem:
        preview = _preview_context(reviewed.preview)
    elif proposal.status == ProposalStatus.EXECUTED or proposal.status == ProposalStatus.FAILED:
        preview = _preview_context(_approved_preview(proposal))
    else:
        preview = None
    can_approve = can_approve and reviewed is not None and not reviewed.problem
    context = {
        "proposal": proposal,
        "operation_label": str(op.label) if op is not None else proposal.operation,
        "preview": preview,
        "binding": reviewed.binding if can_approve and reviewed is not None else "",
        "persona": user,
        "can_approve": can_approve,
        "can_decline": proposal.status == ProposalStatus.PENDING and not done,
        "recent": can_approve and _recent_holds(request, user, grant),
        "notice": notice,
        "error": error,
        "done": done,
    }
    return _harden(render(request, "mcp_writes/approve.html", context, status=status))


# -- the recent verification ------------------------------------------------------


def _gate_pass(request: HttpRequest) -> str:
    return shared_gate.gate_passed_at(request) or request.session.session_key or ""


def _recent_binding(request: HttpRequest, user: Any, grant: McpAccessGrant) -> str:
    material = "\x1f".join(
        (str(grant.pk), str(user.pk), str(int(user.security_epoch or 0)), _gate_pass(request))
    )
    return salted_hmac(_RECENT_SALT, material, algorithm="sha256").hexdigest()


def reverify_seconds() -> int:
    return max(0, int(getattr(settings, "MCP_WRITE_REVERIFY_SECONDS", 300)))


def _recent_holds(request: HttpRequest, user: Any, grant: McpAccessGrant) -> bool:
    """A code this gate session typed for this grant moments ago — and the grant is not on hold.

    A lock or a suspension that began after the code was typed ends the
    shortcut: a right code is refused while the grant is locked or suspended
    (`services.confirm_write_code`), so a remembered one must not approve
    either. The page then asks for a code, which the lock ladder answers.
    """
    if grant.is_suspended or grant.is_locked_at(timezone.now()):
        return False
    stored = request.session.get(SESSION_RECENT)
    if not isinstance(stored, dict) or not reverify_seconds():
        return False
    binding = stored.get("binding")
    if not isinstance(binding, str) or not hmac.compare_digest(
        binding, _recent_binding(request, user, grant)
    ):
        return False
    try:
        verified = datetime.fromisoformat(str(stored.get("verified_at") or ""))
    except ValueError:
        return False
    if timezone.is_naive(verified):
        return False
    age = timezone.now() - verified
    return timedelta(0) <= age < timedelta(seconds=reverify_seconds())


def _remember(request: HttpRequest, user: Any, grant: McpAccessGrant) -> None:
    """Keep the window open — only when there is a window at all."""
    if not reverify_seconds():
        request.session.pop(SESSION_RECENT, None)
        return
    request.session[SESSION_RECENT] = {
        "binding": _recent_binding(request, user, grant),
        "verified_at": timezone.now().isoformat(),
    }


def _throttle_keys(request: HttpRequest, ip: str | None) -> list[access_services.ThrottleKey]:
    """The very counters `/oauth/authorize` charges (`app.mcp_access.views.throttle_keys`).

    A guess at the authenticator here and a guess there are the same guess, so
    they share one budget per gate session and per address — built by the one
    function both pages call, so the keys cannot drift apart.
    """
    from app.mcp_access.views import throttle_keys

    return throttle_keys(request, ip)


# -- the checks -------------------------------------------------------------------


def _owned(request: HttpRequest, proposal_id: Any) -> McpWriteProposal | None:
    """This persona's proposal through a grant that may still write, or None."""
    user: Any = request.user
    proposal = McpWriteProposal.objects.select_related("grant").filter(pk=proposal_id).first()
    if proposal is None or proposal.user_id != user.pk:
        return None
    grant = proposal.grant
    if grant.status != GrantStatus.ACTIVE or not access.may_write(user, grant):
        return None
    return proposal


#: What a review problem says on the page.
_PROBLEM_TEXT = {
    "stale": STALE,
    "mismatch": MISMATCH,
    "format": EXPIRED,
    "unknown_operation": MISMATCH,
}


def _notice(reviewed: review.Reviewed) -> str:
    return _PROBLEM_TEXT.get(reviewed.problem, GENERIC_REFUSAL)


# -- the view ---------------------------------------------------------------------


@writes_enabled_only
@never_cache
@require_http_methods(["GET", "POST"])
def approve(request: HttpRequest, proposal_id: Any) -> HttpResponse:
    user: Any = request.user
    if not user.is_authenticated:
        # No persona yet: choose one and come back here.
        return redirect_to_login(request.get_full_path())
    if shared_gate.is_shared_gate() and not shared_gate.has_passed(request):
        return _refusal_page(request)
    proposal = _owned(request, proposal_id)
    if proposal is None:
        if request.method == "POST":
            stranger = McpWriteProposal.objects.filter(pk=proposal_id).first()
            record(
                McpWriteEventType.APPROVAL_REFUSED,
                succeeded=False,
                proposal=stranger,
                user=user,
                code="refused",
                reason="not_this_persona_or_no_write_right",
            )
        return _refusal_page(request)

    now = timezone.now()
    if proposal.status == ProposalStatus.PENDING and proposal.is_expired_at(now):
        with transaction.atomic():
            locked = McpWriteProposal.objects.select_for_update().get(pk=proposal.pk)
            if locked.status == ProposalStatus.PENDING:
                locked.status = ProposalStatus.EXPIRED
                locked.failure_code = "expired"
                locked.save(update_fields=["status", "failure_code", "updated_at"])
                record(McpWriteEventType.EXPIRED, succeeded=False, proposal=locked, code="expired")
        proposal.refresh_from_db()

    if proposal.status != ProposalStatus.PENDING:
        return _page(request, proposal, can_approve=False, done=STATUS_TEXT[proposal.status])

    decision = request.POST.get("otsus", "") if request.method == "POST" else ""
    if decision == "keeldu":
        return _decline(request, proposal)

    reviewed = review.derive(proposal, user)
    live = access.family_live(proposal)
    if request.method == "GET":
        notice = _notice(reviewed) if reviewed.problem else ("" if live else DISCONNECTED)
        return _page(request, proposal, can_approve=live, reviewed=reviewed, notice=notice)
    if decision != "kinnita":
        return _page(
            request, proposal, can_approve=live, reviewed=reviewed, error=CHOOSE, status=400
        )
    return _approve(request, proposal)


def _decline(request: HttpRequest, proposal: McpWriteProposal) -> HttpResponse:
    with transaction.atomic():
        locked = McpWriteProposal.objects.select_for_update().get(pk=proposal.pk)
        if locked.status == ProposalStatus.PENDING:
            locked.status = ProposalStatus.DECLINED
            locked.failure_code = "declined"
            locked.save(update_fields=["status", "failure_code", "updated_at"])
            record(
                McpWriteEventType.DECLINED,
                succeeded=False,
                proposal=locked,
                ip=client_ip(request) or "",
            )
    locked.refresh_from_db()
    return _page(request, locked, can_approve=False, done=STATUS_TEXT[locked.status])


def _code_refused(
    request: HttpRequest,
    proposal: McpWriteProposal,
    reviewed: review.Reviewed,
    outcome: access_services.AuthorizationOutcome,
) -> HttpResponse:
    record(
        McpWriteEventType.APPROVAL_REFUSED,
        succeeded=False,
        proposal=proposal,
        code=outcome.status,
    )
    minutes = max(1, round(outcome.wait / 60))
    if outcome.status == "throttled":
        error, status = f"Liiga palju katseid. Proovi umbes {minutes} minuti pärast uuesti.", 429
    elif outcome.status == "locked":
        error, status = (
            f"Liiga palju valesid koode. Proovi umbes {minutes} minuti pärast uuesti.",
            429,
        )
    elif outcome.status == "suspended":
        error, status = SUSPENDED, 403
    else:
        # A wrong code, no grant, an unreadable authenticator: one answer.
        error, status = BAD_CODE, 400
    return _page(request, proposal, can_approve=True, reviewed=reviewed, error=error, status=status)


def _problem_page(
    request: HttpRequest, locked: McpWriteProposal, problem: str, reviewed: review.Reviewed | None
) -> HttpResponse:
    if problem == "no_write_right":
        return _refusal_page(request)
    locked.refresh_from_db()
    if locked.status == ProposalStatus.PENDING:
        notice = DISCONNECTED if problem == "disconnected" else GENERIC_REFUSAL
        return _page(
            request, locked, can_approve=False, reviewed=reviewed, notice=notice, status=409
        )
    return _page(request, locked, can_approve=False, done=STATUS_TEXT[locked.status])


def _approve(request: HttpRequest, proposal: McpWriteProposal) -> HttpResponse:
    user: Any = request.user
    grant = proposal.grant
    ip = client_ip(request)
    submitted = str(request.POST.get("sidumine", "") or "")[:64]

    # Before the code: a proposal that cannot be approved spends nothing and
    # opens no window (audit 2026-10-28 C-03); nor does one whose preview
    # changed since the page was shown.
    with transaction.atomic():
        checked = approval.check(proposal.pk, user, canonical_locks=False)
        if checked.problem:
            approval.settle(checked)
    locked, reviewed = checked.proposal, checked.reviewed
    if checked.problem or reviewed is None:
        return _problem_page(request, locked, checked.problem or "stale", reviewed)
    if not hmac.compare_digest(submitted, reviewed.binding):
        record(
            McpWriteEventType.APPROVAL_REFUSED,
            succeeded=False,
            proposal=locked,
            code="binding_changed",
        )
        return _page(
            request, locked, can_approve=True, reviewed=reviewed, notice=CHANGED, status=409
        )

    typed = False
    if _recent_holds(request, user, grant):
        method = ApprovalMethod.RECENT_TOTP
    else:
        outcome = access.confirm_write_code(
            user=user,
            grant_id=grant.pk,
            code=(request.POST.get("kood", "") or "")[:16],
            ip_address=ip,
            throttle=_throttle_keys(request, ip),
        )
        if outcome.status != "ok":
            return _code_refused(request, locked, reviewed, outcome)
        method, typed = ApprovalMethod.TOTP, True

    try:
        outcome_of = approval.approve_and_save(
            proposal_id=proposal.pk, user=user, binding=submitted, method=method, ip=ip
        )
    except Exception as error:
        # Not the person's decision and not the proposal's fault: the whole
        # approval rolled back, nothing was saved, the proposal is still
        # PENDING. The class only — a message could quote data.
        logger.error(
            "MCP approval of %s (%s) failed: %s",
            proposal.pk,
            proposal.operation,
            type(error).__name__,
        )
        record(
            McpWriteEventType.FAILED,
            succeeded=False,
            proposal=proposal,
            code="internal_error",
            retryable=True,
            error=type(error).__name__,
        )
        proposal.refresh_from_db()
        return _page(
            request,
            proposal,
            can_approve=False,
            reviewed=review.derive(proposal, user),
            error=RETRY,
            status=500,
        )
    locked = outcome_of.proposal
    if outcome_of.problem == "binding_changed":
        locked.refresh_from_db()
        fresh = review.derive(locked, user)
        return _page(request, locked, can_approve=True, reviewed=fresh, notice=CHANGED, status=409)
    if outcome_of.problem:
        return _problem_page(request, locked, outcome_of.problem, outcome_of.reviewed)
    if typed:
        # Only a code that approved something opens the window (none at 0).
        _remember(request, user, grant)
    if locked.status == ProposalStatus.FAILED:
        return _page(request, locked, can_approve=False, done=FAILED_TEXT, status=409)
    return _page(request, locked, can_approve=False, done=DONE)
