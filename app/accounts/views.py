"""Local development sign-in.

This is the only authentication path that exists in Stage 0 and it is inert
unless ``DEV_LOGIN_ENABLED`` is on, which a system check refuses to allow
outside a debug environment. Production authenticates through Microsoft Entra
ID (docs/adr/0004-authentication-direction.md).
"""

from __future__ import annotations

import hmac
import uuid

from django.conf import settings
from django.contrib.auth import login, logout
from django.core.cache import cache
from django.db.models import QuerySet
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.views.decorators.http import require_http_methods

from app.accounts.models import User
from app.audit.enums import SecurityEventType
from app.audit.services import record_security_event

MODEL_BACKEND = "django.contrib.auth.backends.ModelBackend"


def _record_denied(request: HttpRequest, *, reason: str) -> None:
    record_security_event(
        event_type=SecurityEventType.AUTHENTICATION_FAILED,
        succeeded=False,
        ip_address=request.META.get("REMOTE_ADDR"),
        user_agent=request.META.get("HTTP_USER_AGENT", ""),
        detail={"path": "dev_login", "reason": reason},
    )


def _selected_user(queryset: QuerySet, raw_id: str) -> User | None:
    try:
        identifier = uuid.UUID(raw_id)
    except (ValueError, AttributeError):
        return None
    return queryset.filter(pk=identifier).first()


def _require_dev_login() -> None:
    if not settings.DEV_LOGIN_ENABLED:
        raise Http404("Development sign-in is not available in this environment.")


def _client_address(request: HttpRequest) -> str:
    """Who is knocking, for throttling purposes.

    Behind the Cloudflare tunnel every request arrives from the connector, so
    the forwarded address is the only thing that distinguishes callers. It is
    attacker-controlled and therefore useless as *identity* — but this is a rate
    limit, not an authorization decision, and an attacker who rotates the header
    to dodge it has still slowed themselves down.
    """
    forwarded = request.META.get("HTTP_CF_CONNECTING_IP") or request.META.get(
        "HTTP_X_FORWARDED_FOR", ""
    )
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR", "unknown")


def _attempt_key(request: HttpRequest) -> str:
    return f"dev-login-pin-attempts:{_client_address(request)}"


def _is_locked_out(request: HttpRequest) -> bool:
    if not settings.DEV_LOGIN_PIN:
        return False
    return cache.get(_attempt_key(request), 0) >= settings.DEV_LOGIN_PIN_MAX_ATTEMPTS


def _record_failure(request: HttpRequest) -> None:
    key = _attempt_key(request)
    # add() only sets when absent, so the window starts at the first failure and
    # does not slide forward with every later one.
    cache.add(key, 0, timeout=settings.DEV_LOGIN_PIN_LOCKOUT_SECONDS)
    try:
        cache.incr(key)
    except ValueError:  # pragma: no cover - the key expired between the two calls
        cache.set(key, 1, timeout=settings.DEV_LOGIN_PIN_LOCKOUT_SECONDS)


def _pin_is_correct(supplied: str) -> bool:
    # Constant-time: a four-digit secret is small enough that a timing oracle on
    # the prefix would meaningfully narrow it.
    return hmac.compare_digest(supplied.strip(), settings.DEV_LOGIN_PIN)


@require_http_methods(["GET", "POST"])
def dev_login(request: HttpRequest) -> HttpResponse:
    _require_dev_login()

    users = User.objects.filter(is_synthetic=True, is_active=True).order_by("display_name")

    if request.method == "POST":
        if _is_locked_out(request):
            _record_denied(request, reason="locked_out")
            return render(
                request,
                "accounts/dev_login.html",
                {
                    "users": users,
                    "pin_required": bool(settings.DEV_LOGIN_PIN),
                    "error": "Liiga palju vale PIN-i katseid. Proovi mõne minuti pärast uuesti.",
                },
                status=429,
            )

        if settings.DEV_LOGIN_PIN and not _pin_is_correct(request.POST.get("pin", "")):
            _record_failure(request)
            _record_denied(request, reason="bad_pin")
            return render(
                request,
                "accounts/dev_login.html",
                {"users": users, "pin_required": True, "error": "Vale PIN."},
                status=400,
            )

        user = _selected_user(users, request.POST.get("user_id", ""))
        if user is None:
            record_security_event(
                event_type=SecurityEventType.AUTHENTICATION_FAILED,
                succeeded=False,
                ip_address=request.META.get("REMOTE_ADDR"),
                user_agent=request.META.get("HTTP_USER_AGENT", ""),
                detail={"path": "dev_login"},
            )
            return render(
                request,
                "accounts/dev_login.html",
                {
                    "users": users,
                    "pin_required": bool(settings.DEV_LOGIN_PIN),
                    "error": "Vali kehtiv sünteetiline kasutaja.",
                },
                status=400,
            )
        # A correct PIN clears the counter, so an ordinary typo does not leave
        # somebody locked out for the rest of the window.
        cache.delete(_attempt_key(request))
        login(request, user, backend=MODEL_BACKEND)
        record_security_event(
            event_type=SecurityEventType.AUTHENTICATION_SUCCEEDED,
            actor=user,
            ip_address=request.META.get("REMOTE_ADDR"),
            user_agent=request.META.get("HTTP_USER_AGENT", ""),
            detail={"method": "dev_login"},
        )
        return redirect(settings.LOGIN_REDIRECT_URL)

    return render(
        request,
        "accounts/dev_login.html",
        {"users": users, "pin_required": bool(settings.DEV_LOGIN_PIN)},
    )


@require_http_methods(["POST"])
def sign_out(request: HttpRequest) -> HttpResponse:
    logout(request)
    return redirect("core:home")
