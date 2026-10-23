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
from urllib.parse import urlencode

from django.contrib.auth import login, logout
from django.contrib.auth.views import redirect_to_login
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.cache import add_never_cache_headers

from app.accounts import admin_session, cloudflare_access, local_auth, shared_gate
from app.accounts.enums import AuthMode
from app.accounts.models import User
from app.accounts.selectors import is_persona_candidate
from app.audit.enums import SecurityEventType
from app.audit.services import record_security_event
from app.core.client_ip import client_ip

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
        if shared_gate.is_machine_endpoint(request.path):
            # The MCP token, revocation and metadata endpoints: exact paths,
            # only while MCP access is on. They authenticate the client
            # themselves (docs/adr/0185 §4).
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
            shared_gate.note_activity(request)
            self._drop_an_ineligible_persona(request)
            return self.get_response(request)

        # The gate is closed for this session — expired, idle, or opened under
        # a shared password no longer configured. An administrator session
        # behind it ends with it, recorded as such (docs/adr/0186).
        admin_session.note_session_closing(
            request,
            reason=(
                "shared_password_changed"
                if shared_gate.opened_under_another_password(request)
                else "gate_closed"
            ),
        )
        if shared_gate.opened_under_another_password(request):
            self._end_a_superseded_gate(request)

        # A session that carries a persona but not a valid gate is a session
        # whose gate expired — absolutely or through being idle — or one
        # restored from before the gate existed.
        # Either way the persona goes with it: keeping it would let an aged-out
        # session go on acting as somebody.
        if getattr(request, "user", None) is not None and request.user.is_authenticated:
            logout(request)

        if request.path == gate_url:
            return self.get_response(request)
        link_page = self._arriving_link(request)
        if link_page:
            # An administrator's one-time link, followed from outside before
            # the gate: keep its proof (never the secret), and come back to
            # the bare page after the password (docs/adr/0186).
            return redirect(f"{gate_url}?{urlencode({'next': link_page})}")
        back = shared_gate.gate_return_target(request)
        if back:
            # An MCP consent or enrolment page reached from outside: come back
            # to it after the password (docs/adr/0185). Only those pages.
            return redirect(f"{gate_url}?{urlencode({'next': back})}")
        return redirect(gate_url)

    def _arriving_link(self, request: HttpRequest) -> str:
        """The bare path of a one-time link page this GET arrives at with its `?kood=`, or "".

        The link's secret goes no further than this request: its proof is kept
        in the session, which survives the gate (`open_gate` rotates the
        identifier and keeps the contents), and the gate's `next` is the bare
        path. Whether the link is any good, and whether its holder may use it
        here, the page decides after the password, as it always does.
        """
        from app.accounts.local_views import LINK_ROUTES, keep_link_proof

        if request.method != "GET":
            return ""
        for route, purpose in LINK_ROUTES.items():
            path = reverse(route)
            if request.path == path:
                return path if keep_link_proof(request, purpose=purpose) else ""
        return ""

    def _end_a_superseded_gate(self, request: HttpRequest) -> None:
        """End a session opened under a shared password that is no longer configured.

        Rotating `JURISTID_SHARED_GATE_PASSWORD` is how the department takes the
        door back from somebody who should no longer have it, and it only does
        that if the sessions the old password opened end too (csf_19450e2c,
        docs/adr/0183). The whole session goes — the gate, the persona and
        anything else in it — not only the gate keys, so nothing opened under
        the old password survives into a session re-opened under the new one.

        Recorded once: the flush removes what this test looks for, so the next
        request from the same browser is simply a visitor at the gate.
        """
        record_security_event(
            event_type=SecurityEventType.SESSION_ENDED,
            actor=None,
            ip_address=client_ip(request),
            user_agent=request.META.get("HTTP_USER_AGENT", ""),
            detail=shared_gate.audit_detail(
                request, path="shared_gate", reason="shared_password_changed"
            ),
        )
        logout(request)

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
        carried = shared_gate.current_pass(request)
        administrator = admin_session.carry(request)

        record_security_event(
            event_type=SecurityEventType.PERSONA_SELECTED,
            actor=None,
            ip_address=client_ip(request),
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
        # make. Re-opening it is what `act_as` does for the same reason, with
        # the original pass time and password version, so the 12-hour clock
        # keeps counting and a later rotation still ends this session.
        shared_gate.open_gate(request, carried=carried)
        # The persona went; the administrator session is not the persona's.
        admin_session.restore(request, administrator)

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

        token = request.META.get(cloudflare_access.HEADER, "")
        if cloudflare_access.was_signed_out_with(request.session, token):
            # Signed out here, and Cloudflare is still forwarding the assertion
            # from before: its own session has not ended yet. Signing back in
            # from it would undo the sign-out the person just asked for
            # (csf_92343c92, docs/adr/0183). A page, not a redirect, so there
            # is no loop to fall into: it offers the logout that ends the
            # Access session, after which a new sign-in brings a new assertion.
            return self._signed_out(request)

        person = User.objects.filter(upn__iexact=email, is_active=True).first()
        if person is None:
            return self._deny(request, reason="no_account", email=email)
        if person.is_synthetic:
            # Synthetic accounts exist for the rehearsal world. One must never
            # become a real identity on the real deployment.
            return self._deny(request, reason="synthetic_account", email=email)

        login(request, person, backend=MODEL_BACKEND)
        request.session.pop(cloudflare_access.SIGNED_OUT_ASSERTION, None)
        request.session["cf_access_email"] = email
        request.session.set_expiry(cloudflare_access.seconds_until_expiry(claims))
        return self.get_response(request)

    def _signed_out(self, request: HttpRequest) -> HttpResponse:
        response = render(
            request,
            "accounts/access_signed_out.html",
            {
                "logout_url": cloudflare_access.logout_url(),
                "application_logout_url": cloudflare_access.LOGOUT_PATH,
            },
        )
        add_never_cache_headers(response)
        return response

    def _deny(self, request: HttpRequest, *, reason: str, email: str = "") -> HttpResponse:
        record_security_event(
            event_type=SecurityEventType.AUTHENTICATION_FAILED,
            succeeded=False,
            ip_address=client_ip(request),
            user_agent=request.META.get("HTTP_USER_AGENT", ""),
            detail={"path": "cloudflare_access", "reason": reason, "email": email},
        )
        logger.warning("Cloudflare Access denied a request: %s", reason)
        return HttpResponse(
            "Ligipääs puudub. Palun logi sisse Koja kontoga.",
            status=403,
            content_type="text/plain; charset=utf-8",
        )
