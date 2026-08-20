"""Signing a person in from a verified Cloudflare Access assertion.

Runs after Django's own `AuthenticationMiddleware`, so `request.user` already
holds whatever the session says. This middleware's job is narrow:

1. verify the assertion cryptographically (`cloudflare_access.verify`),
2. make sure the session belongs to the person the assertion names,
3. sign in a *known, active* account — and nobody else.

**Nobody is provisioned here.** An account exists in this system because an
administrator created it. Auto-creating a User from a verified email would mean
that widening an Access policy — a change made in a Cloudflare dashboard, by
somebody who may not know what this application holds — silently grants a
stranger a seat inside a system of confidential member material
(Stage-2D brief 59).

The failure mode is chosen deliberately too. When the assertion is missing or
invalid the request is **denied**, not passed through as anonymous: there is no
public surface behind Access to fall through to, and a soft failure would mean a
misconfiguration presents as "please sign in" rather than as an outage
(Stage-2D brief 58).
"""

from __future__ import annotations

import logging
from typing import Any

from django.conf import settings
from django.contrib.auth import login, logout
from django.http import HttpRequest, HttpResponse

from app.accounts import cloudflare_access
from app.accounts.models import User
from app.audit.enums import SecurityEventType
from app.audit.services import record_security_event

logger = logging.getLogger(__name__)

MODEL_BACKEND = "django.contrib.auth.backends.ModelBackend"

#: Paths that must answer before anybody is authenticated. Kept to exactly what
#: the platform needs: a health check the container runtime calls, and the
#: static files Whitenoise serves.
EXEMPT_PREFIXES = ("/healthz", "/static/")


class CloudflareAccessMiddleware:
    """Trust the signature, never the header."""

    def __init__(self, get_response: Any) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        if not cloudflare_access.is_enabled():
            return self.get_response(request)
        if request.path.startswith(EXEMPT_PREFIXES):
            return self.get_response(request)

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


def cloudflare_access_settings() -> dict[str, object]:
    """What the deployment thinks it is doing, for `manage.py check`."""
    return {
        "enabled": cloudflare_access.is_enabled(),
        "team_domain": getattr(settings, "CF_ACCESS_TEAM_DOMAIN", ""),
        "audience_configured": bool(getattr(settings, "CF_ACCESS_AUDIENCE", "")),
    }
