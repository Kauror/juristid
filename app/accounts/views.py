"""Local development sign-in.

This is the only authentication path that exists in Stage 0 and it is inert
unless ``DEV_LOGIN_ENABLED`` is on, which a system check refuses to allow
outside a debug environment. Production authenticates through Microsoft Entra
ID (docs/adr/0004-authentication-direction.md).
"""

from __future__ import annotations

import uuid

from django.conf import settings
from django.contrib.auth import login, logout
from django.db.models import QuerySet
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.views.decorators.http import require_http_methods

from app.accounts.models import User
from app.audit.enums import SecurityEventType
from app.audit.services import record_security_event

MODEL_BACKEND = "django.contrib.auth.backends.ModelBackend"


def _selected_user(queryset: QuerySet, raw_id: str) -> User | None:
    try:
        identifier = uuid.UUID(raw_id)
    except (ValueError, AttributeError):
        return None
    return queryset.filter(pk=identifier).first()


def _require_dev_login() -> None:
    if not settings.DEV_LOGIN_ENABLED:
        raise Http404("Development sign-in is not available in this environment.")


@require_http_methods(["GET", "POST"])
def dev_login(request: HttpRequest) -> HttpResponse:
    _require_dev_login()

    users = User.objects.filter(is_synthetic=True, is_active=True).order_by("display_name")

    if request.method == "POST":
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
                {"users": users, "error": "Vali kehtiv sünteetiline kasutaja."},
                status=400,
            )
        login(request, user, backend=MODEL_BACKEND)
        record_security_event(
            event_type=SecurityEventType.AUTHENTICATION_SUCCEEDED,
            actor=user,
            ip_address=request.META.get("REMOTE_ADDR"),
            user_agent=request.META.get("HTTP_USER_AGENT", ""),
            detail={"method": "dev_login"},
        )
        return redirect(settings.LOGIN_REDIRECT_URL)

    return render(request, "accounts/dev_login.html", {"users": users})


@require_http_methods(["POST"])
def sign_out(request: HttpRequest) -> HttpResponse:
    logout(request)
    return redirect("core:home")
