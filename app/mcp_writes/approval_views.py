"""The approval page: the one place an AI-prepared change becomes an approved one (docs/adr/0189).

``/mcp-ligipaas/muudatus/<proposal uuid>/`` on `web`, behind the shared gate
and the persona like every page. A model's tool call alone never changes a
record: its `prepare` stores a proposal, and only its own person, here, with
their own MCP authenticator, approves it. The write gateway commits it
afterwards — and only an approved one.

Who sees what:

* **One generic refusal** for everything that is not this persona's own
  pending business: no such proposal, somebody else's proposal, a grant that
  is not ACTIVE, an account that may not write through it (`may_write`). The
  page does not say which, so a guessed or forwarded link teaches nothing.
* Its own person sees the stored preview — autoescaped, every value as it was
  prepared — with the operation, the target and its link, each change
  current → proposed, the consequences, what does not change, the warnings and
  the expiry. Before showing «Kinnita» the page recomputes the operation's
  `state` and compares it with the stored one: a record that moved on since
  the proposal was prepared is **stale**, and there is no approve button.

Approving is one explicit POST for one proposal, CSRF-protected, with either

* the grant's current authenticator code, checked by
  `app.mcp_access.services.confirm_write_code` — the same single-use step,
  lock ladder and per-session/address throttle (the very same counters) as
  `/oauth/authorize`; or
* a code this same gate session typed less than `MCP_WRITE_REVERIFY_SECONDS`
  ago, kept in the session as a keyed binding of the grant, the account, its
  `security_epoch` and the gate pass — a persona change, a new gate pass, an
  epoch bump or another grant makes it worthless.

The approval row (`McpWriteApproval`) records the user, the grant and the
arguments digest the person saw, the method and the address; the proposal
becomes APPROVED and an `APPROVED` event is written, in one transaction, under
the proposal's lock, after every check has been made again.

Every response is no-store, may not be framed, and carries the application's
CSP. With MCP writes off every route is a 404 for every method.
"""

from __future__ import annotations

import functools
import hmac
import json
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
from app.core.errors import DomainError
from app.mcp_access import config as access_config
from app.mcp_access import services as access_services
from app.mcp_access.models import GrantStatus, McpAccessGrant
from app.mcp_writes import access, operations
from app.mcp_writes.models import (
    ApprovalMethod,
    McpWriteApproval,
    McpWriteEventType,
    McpWriteProposal,
    ProposalStatus,
)
from app.mcp_writes.operations.base import OperationRefused
from app.mcp_writes.proposals import digest_of, record
from app.mcp_writes.refusals import jsonable

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
DONE = (
    "Kinnitatud — mine tagasi ChatGPT-sse; muudatus salvestatakse, kui ChatGPT selle "
    "kinnitab (commit_prepared_action)."
)
DECLINED = "Lükkasid selle muudatuse tagasi. Midagi ei salvestatud."
STALE = (
    "Kirje on pärast selle muudatuse ettevalmistamist muutunud, seega seda ei saa enam "
    "kinnitada. Palu ChatGPT-l muudatus uuesti ette valmistada."
)
WITHDRAWN = "Tühistasid kinnituse. Muudatust ei salvestata."
EXPIRED = "Selle muudatuse kinnitamise aeg on läbi. Palu ChatGPT-l see uuesti ette valmistada."

#: What a proposal that is no longer waiting says to its own person.
STATUS_TEXT: dict[str, str] = {
    ProposalStatus.APPROVED: DONE,
    ProposalStatus.EXECUTED: "See muudatus on juba kinnitatud ja salvestatud.",
    ProposalStatus.DECLINED: DECLINED,
    ProposalStatus.EXPIRED: EXPIRED,
    ProposalStatus.FAILED: "Seda muudatust ei õnnestunud salvestada. Midagi ei muudetud.",
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


def _preview_context(proposal: McpWriteProposal) -> dict[str, Any]:
    preview = proposal.preview if isinstance(proposal.preview, dict) else {}
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


def _page(
    request: HttpRequest,
    proposal: McpWriteProposal,
    *,
    can_approve: bool,
    notice: str = "",
    error: str = "",
    done: str = "",
    status: int = 200,
) -> HttpResponse:
    op = operations.get(proposal.operation)
    user: Any = request.user
    grant = proposal.grant
    context = {
        "proposal": proposal,
        "operation_label": str(op.label) if op is not None else proposal.operation,
        "preview": _preview_context(proposal),
        "persona": user,
        "can_approve": can_approve,
        "can_decline": proposal.status == ProposalStatus.PENDING and not done,
        "can_withdraw": proposal.status == ProposalStatus.APPROVED,
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


def is_stale(proposal: McpWriteProposal, user: Any) -> bool:
    """Recompute the operation's state and compare it with the stored one.

    Inside a savepoint that is rolled back, so a state read can leave nothing
    behind. No canonical lock is taken here: the approval writes no business
    row, and the commit takes the locks and compares the state again under
    them.
    """
    op = operations.get(proposal.operation)
    if op is None:
        return True
    try:
        with transaction.atomic():
            current = jsonable(op.state(user=user, canonical=proposal.canonical_arguments))
            transaction.set_rollback(True)
    except (OperationRefused, DomainError):
        return True
    return bool(current != proposal.state)


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

    if request.method == "POST" and request.POST.get("otsus", "") == "tuhista":
        return _withdraw(request, proposal)
    if proposal.status != ProposalStatus.PENDING:
        return _page(request, proposal, can_approve=False, done=STATUS_TEXT[proposal.status])

    stale = is_stale(proposal, user)
    if request.method == "GET":
        return _page(request, proposal, can_approve=not stale, notice=STALE if stale else "")

    decision = request.POST.get("otsus", "")
    if decision == "keeldu":
        return _decline(request, proposal)
    if decision != "kinnita":
        return _page(request, proposal, can_approve=not stale, error=CHOOSE, status=400)
    if stale:
        return _page(request, proposal, can_approve=False, notice=STALE, status=409)
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


def _withdraw(request: HttpRequest, proposal: McpWriteProposal) -> HttpResponse:
    """«Tühista kinnitus»: an APPROVED proposal not yet committed is declined after all.

    No code is asked: withdrawing makes nothing happen. Under the proposal's
    row lock, which the commit takes first, so a withdrawal and a commit take
    turns — the commit then answers ``declined``, or the withdrawal finds the
    change already saved and says so. The approval row stays as it was
    (append-only); the proposal's status is what the commit reads.
    """
    with transaction.atomic():
        locked = McpWriteProposal.objects.select_for_update().get(pk=proposal.pk)
        if locked.status == ProposalStatus.APPROVED:
            locked.status = ProposalStatus.DECLINED
            locked.failure_code = "withdrawn"
            locked.save(update_fields=["status", "failure_code", "updated_at"])
            record(
                McpWriteEventType.DECLINED,
                succeeded=False,
                proposal=locked,
                reason="approval_withdrawn",
            )
            withdrawn = True
        else:
            withdrawn = False
    locked.refresh_from_db()
    if withdrawn:
        return _page(request, locked, can_approve=False, done=WITHDRAWN)
    if locked.status == ProposalStatus.PENDING:
        return _page(request, locked, can_approve=False, error=CHOOSE, status=400)
    return _page(request, locked, can_approve=False, done=STATUS_TEXT[locked.status], status=409)


def _code_refused(
    request: HttpRequest, proposal: McpWriteProposal, outcome: access_services.AuthorizationOutcome
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
    return _page(request, proposal, can_approve=True, error=error, status=status)


def _approve(request: HttpRequest, proposal: McpWriteProposal) -> HttpResponse:
    user: Any = request.user
    grant = proposal.grant
    ip = client_ip(request)
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
            return _code_refused(request, proposal, outcome)
        _remember(request, user, grant)
        method = ApprovalMethod.TOTP

    with transaction.atomic():
        locked = (
            McpWriteProposal.objects.select_for_update().select_related("grant").get(pk=proposal.pk)
        )
        now = timezone.now()
        problem = ""
        if locked.status != ProposalStatus.PENDING:
            problem = "not_pending"
        elif locked.is_expired_at(now):
            problem = "expired"
        elif digest_of(locked) != locked.arguments_digest:
            problem = "digest"
        elif locked.grant.status != GrantStatus.ACTIVE or not access.may_write(user, locked.grant):
            problem = "no_write_right"
        elif is_stale(locked, user):
            problem = "stale"
        if problem:
            final = {
                "expired": ProposalStatus.EXPIRED,
                "digest": ProposalStatus.FAILED,
                "stale": ProposalStatus.STALE,
            }.get(problem)
            if final is not None:
                locked.status = final
                locked.failure_code = "approval_mismatch" if problem == "digest" else problem
                locked.save(update_fields=["status", "failure_code", "updated_at"])
            record(
                McpWriteEventType.APPROVAL_REFUSED, succeeded=False, proposal=locked, code=problem
            )
        else:
            McpWriteApproval.objects.create(
                proposal=locked,
                user=user,
                grant=locked.grant,
                arguments_digest=locked.arguments_digest,
                approved_at=now,
                expires_at=now + timedelta(seconds=int(settings.MCP_WRITE_APPROVAL_SECONDS)),
                method=method,
                ip_address=ip,
            )
            locked.status = ProposalStatus.APPROVED
            locked.save(update_fields=["status", "updated_at"])
            record(McpWriteEventType.APPROVED, proposal=locked, method=str(method))
    if problem == "no_write_right":
        return _refusal_page(request)
    if problem:
        locked.refresh_from_db()
        if locked.status == ProposalStatus.PENDING:
            return _page(request, locked, can_approve=False, notice=GENERIC_REFUSAL, status=409)
        return _page(request, locked, can_approve=False, done=STATUS_TEXT[locked.status])
    return _page(request, locked, can_approve=False, done=DONE)
