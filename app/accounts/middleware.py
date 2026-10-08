"""Establishing who is at the keyboard, according to the deployment's mode.

One middleware, three modes, and a deliberate asymmetry between them: the mode
decides how much the deployment may *claim*, and the claim is recorded on every
audit row it produces (`app/accounts/enums.py`, docs/adr/0016).

**`cloudflare_access`** — Cloudflare authenticates the individual and forwards a
signed assertion; this verifies the RS256 signature against the team's published
keys before believing the email in it. A request header on its own is
attacker-controlled: anybody who could reach the container directly could set it
to anything. There is no fallback to the unsigned
`Cf-Access-Authenticated-User-Email` header, and nobody is provisioned — a
verified email that matches no active, non-synthetic account is refused, because
widening an Access policy in a Cloudflare dashboard must not hand somebody a
seat inside a system of confidential member material (Stage-2D brief 59).

**`shared_gate`** — one department password guards the door; a persona is picked
behind it. Passing the gate is required for *everything*, so an unauthenticated
visitor sees a password form and no data. Selecting a persona is a choice, not a
proof, and the audit says so.

**`local_password`** — the individual signs in here, with a personal password
and, where required, a second factor (docs/adr/0145). Dormant: no deployment
runs it. Everything except the sign-in surfaces themselves needs a session
that was signed in *here* — a persona session left over from the shared gate
is signed out, never promoted — and that session ends when it outlives its
idle or absolute limit. Somebody who must enrol a second factor reaches
nothing but the enrolment page until they have.

**`none`** — a developer laptop and CI. This middleware does nothing at all.

In every mode the failure is a refusal, not a pass-through as anonymous. There
is no public surface behind any of these to fall through to, and a soft failure
would make a misconfiguration present as "please sign in" rather than as the
outage it is (Stage-2D brief 58).
"""

from __future__ import annotations

import functools
import logging
from typing import Any

from django.contrib.auth import login, logout
from django.contrib.auth.views import redirect_to_login
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect
from django.urls import reverse

from app.accounts import cloudflare_access, local_auth, shared_gate
from app.accounts.enums import AuthMode
from app.accounts.models import User
from app.accounts.selectors import is_persona_candidate
from app.audit.enums import SecurityEventType
from app.audit.services import record_security_event

logger = logging.getLogger(__name__)

MODEL_BACKEND = "django.contrib.auth.backends.ModelBackend"

#: Paths that must answer before anybody is authenticated. Kept to exactly what
#: the platform needs: a health check the container runtime calls, and the
#: static files Whitenoise serves.
EXEMPT_PREFIXES = ("/healthz", "/static/")

#: The local sign-in's own pages, which answer before anybody is signed in.
#: Named routes resolved once, rather than prefixes: `/konto/` also holds pages
#: that need a signed-in person, and a prefix would exempt them too.
_LOCAL_AUTH_PUBLIC_ROUTES = (
    "accounts:sign_in",
    "accounts:sign_in_second_factor",
    "accounts:sign_out",
    "accounts:activate",
    "accounts:forgot_password",
    "accounts:reset_password",
)

#: What somebody who must enrol a second factor may still reach.
_ENROLMENT_ROUTES = ("accounts:security_enrol", "accounts:sign_out")


@functools.cache
def local_auth_public_paths() -> frozenset[str]:
    return frozenset(reverse(name) for name in _LOCAL_AUTH_PUBLIC_ROUTES)


@functools.cache
def enrolment_paths() -> frozenset[str]:
    return frozenset(reverse(name) for name in _ENROLMENT_ROUTES)


class AuthenticationModeMiddleware:
    """Whatever `AUTH_MODE` says, applied before any view runs."""

    def __init__(self, get_response: Any) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        mode = shared_gate.current_mode()
        if mode == AuthMode.NONE or request.path.startswith(EXEMPT_PREFIXES):
            return self.get_response(request)
        if mode == AuthMode.CLOUDFLARE_ACCESS:
            return self._cloudflare_access(request)
        if mode == AuthMode.LOCAL_PASSWORD:
            return self._local_password(request)
        return self._shared_gate(request)

    # -- local password ----------------------------------------------------

    def _local_password(self, request: HttpRequest) -> HttpResponse:
        """Only individually signed-in sessions, inside their limits, get through.

        The public surfaces — sign-in, the second-factor step, the one-time
        links and «Unustasid parooli?» — answer anybody, and decide for
        themselves what a visitor may do there. Everything else requires a
        session this mode signed in.
        """
        if request.path in local_auth_public_paths():
            return self.get_response(request)

        user = getattr(request, "user", None)
        sign_in = reverse("accounts:sign_in")
        if user is None or not user.is_authenticated:
            if self._was_switched_off(request):
                logout(request)
                return redirect(f"{sign_in}?olek=valjas")
            return redirect_to_login(request.get_full_path(), sign_in)

        if not local_auth.is_local_session(request):
            # A session from another mode — a persona chosen behind the shared
            # gate, a synthetic development sign-in — after the deployment
            # changed mode. It proves nothing about who is reading; it goes.
            local_auth.end_session(request, reason="not_signed_in_here")
            return redirect_to_login(request.get_full_path(), sign_in)

        reason = local_auth.expiry_reason(request)
        if reason:
            local_auth.end_session(request, reason=reason)
            return redirect(f"{sign_in}?olek=aegunud")
        local_auth.touch(request)

        if local_auth.enrolment_pending(request) and request.path not in enrolment_paths():
            return redirect("accounts:security_enrol")
        return self.get_response(request)

    def _was_switched_off(self, request: HttpRequest) -> bool:
        """Whether this anonymous request is a session whose account was switched off.

        Django answers such a session as anonymous without saying why. The local
        sign-in marker is still in it, and it names an account that is no longer
        active: say so on the sign-in page, which only the holder of that
        session cookie can see.
        """
        marker = request.session.get(local_auth.SESSION_USER)
        if not marker:
            return False
        return User.objects.filter(pk=marker, is_active=False).exists()

    # -- shared gate -------------------------------------------------------

    def _shared_gate(self, request: HttpRequest) -> HttpResponse:
        gate_url = reverse("accounts:shared_gate")

        if shared_gate.has_passed(request):
            self._drop_an_ineligible_persona(request)
            return self.get_response(request)

        # A session that carries a persona but not a valid gate is a session
        # whose gate expired, or one restored from before the gate existed.
        # Either way the persona goes with it: keeping it would let an aged-out
        # session go on acting as somebody.
        if getattr(request, "user", None) is not None and request.user.is_authenticated:
            logout(request)

        if request.path == gate_url:
            return self.get_response(request)
        return redirect(gate_url)

    def _drop_an_ineligible_persona(self, request: HttpRequest) -> None:
        """Stop acting as somebody who may no longer be acted as.

        Narrowing the candidate rule closes the *endpoint* immediately, and does
        nothing about a session that already selected an account the new rule
        excludes — an administrator persona chosen this morning would go on
        being an administrator persona until the gate aged out twelve hours
        later. The fix is only complete if it also applies to sessions that are
        already open (docs/adr/0034).

        The same treatment an expired gate gets a few lines below: the persona
        goes, the door stays open, and the reader lands on the department view
        with nobody selected. Recorded through the existing persona-change
        event, with the reason, rather than disappearing silently — somebody
        whose selection vanished mid-task should be able to find out why.
        """
        user = getattr(request, "user", None)
        if user is None or not user.is_authenticated or is_persona_candidate(user):
            return

        record_security_event(
            event_type=SecurityEventType.PERSONA_SELECTED,
            actor=None,
            ip_address=request.META.get("REMOTE_ADDR"),
            user_agent=request.META.get("HTTP_USER_AGENT", ""),
            detail=shared_gate.audit_detail(
                request,
                previous_persona=str(user.pk),
                chosen_persona=None,
                reason="persona_no_longer_eligible",
            ),
        )
        logout(request)
        # `logout` flushes the session, which would take the gate with it and
        # send somebody back to the password form for a change they did not
        # make. Re-opening it is what `act_as` does for the same reason.
        shared_gate.open_gate(request)

    # -- cloudflare access -------------------------------------------------

    def _cloudflare_access(self, request: HttpRequest) -> HttpResponse:
        try:
            email, claims = cloudflare_access.identity_from_request(request.META)
        except cloudflare_access.AccessDenied as error:
            return self._deny(request, reason=str(error))

        current = getattr(request, "user", None)
        if current is not None and current.is_authenticated:
            if (current.upn or "").lower() == email:
                return self.get_response(request)
            # A session that belongs to somebody else. This happens when two
            # people share a browser profile, and it must not be resolved in
            # favour of the older session.
            logout(request)

        person = User.objects.filter(upn__iexact=email, is_active=True).first()
        if person is None:
            return self._deny(request, reason="no_account", email=email)
        if person.is_synthetic:
            # Synthetic accounts exist for the rehearsal world. One must never
            # become a real identity on the real deployment.
            return self._deny(request, reason="synthetic_account", email=email)

        login(request, person, backend=MODEL_BACKEND)
        request.session["cf_access_email"] = email
        request.session.set_expiry(cloudflare_access.seconds_until_expiry(claims))
        return self.get_response(request)

    def _deny(self, request: HttpRequest, *, reason: str, email: str = "") -> HttpResponse:
        record_security_event(
            event_type=SecurityEventType.AUTHENTICATION_FAILED,
            succeeded=False,
            ip_address=request.META.get("REMOTE_ADDR"),
            user_agent=request.META.get("HTTP_USER_AGENT", ""),
            detail={"path": "cloudflare_access", "reason": reason, "email": email},
        )
        logger.warning("Cloudflare Access denied a request: %s", reason)
        return HttpResponse(
            "Ligipääs puudub. Palun logi sisse Koja kontoga.",
            status=403,
            content_type="text/plain; charset=utf-8",
        )
