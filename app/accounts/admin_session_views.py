"""The verified administrator session's pages: sign in, code, enrol, re-prove, end.

Mounted under `/haldus/` (namespace `haldus`). They exist only under the shared
gate — a 404 in every other mode, the shape the other mode-bound pages use —
and the middleware already keeps everybody who has not passed the gate away
from them. Under `local_password` the personal sign-in is the verified
principal and the sign-in page sends the reader on to Haldus.

The views hold no rule. They read a form, call `app.accounts.admin_session`,
and render what came back (docs/adr/0186).
"""

from __future__ import annotations

import functools
from collections.abc import Callable
from typing import Any
from urllib.parse import urlencode

from django.contrib import messages
from django.http import Http404, HttpRequest, HttpResponse, HttpResponseRedirect
from django.http.response import HttpResponseBase
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods

from app.accounts import admin_session, local_auth
from app.accounts.forms import EnrolmentForm, ReauthenticationForm, SecondFactorForm, SignInForm
from app.core.request_params import safe_local_path

NOT_HERE = "Lehte ei leitud."


def shared_gate_only[R: HttpResponseBase](
    view: Callable[..., R],
) -> Callable[..., R | HttpResponseBase]:
    """404 unless this deployment runs the shared gate; on to Haldus under personal sign-in."""

    @functools.wraps(view)
    def wrapper(request: HttpRequest, *args: Any, **kwargs: Any) -> R | HttpResponseBase:
        if local_auth.is_local_password():
            return redirect("haldus:index")
        if not admin_session.is_available():
            raise Http404(NOT_HERE)
        return view(request, *args, **kwargs)

    return wrapper


def _safe(request: HttpRequest, raw: Any) -> str:
    return safe_local_path(raw, host=request.get_host(), require_https=request.is_secure())


def _wait_message(wait: int) -> str:
    minutes = max(1, round(wait / 60))
    return f"Liiga palju katseid. Proovi umbes {minutes} minuti pärast uuesti."


def _onward(request: HttpRequest) -> HttpResponse:
    target = _safe(request, request.session.pop(admin_session.SESSION_NEXT, ""))
    return HttpResponseRedirect(target or reverse("haldus:index"))


def reauthenticate_url(back: str) -> str:
    """Where a change sends a verified administrator whose last proof is too old."""
    if local_auth.is_local_password():
        return f"{reverse('accounts:reauthenticate')}?{urlencode({'next': back})}"
    return f"{reverse('haldus:reauthenticate')}?{urlencode({'next': back})}"


@never_cache
@shared_gate_only
@require_http_methods(["GET", "POST"])
def sign_in(request: HttpRequest) -> HttpResponse | HttpResponseBase:
    """Halduri sisselogimine: e-post → isiklik parool → oma autentimisrakenduse kood."""
    if admin_session.verified_administrator(request) is not None:
        return _onward(request)
    if request.method == "GET":
        target = _safe(request, request.GET.get("next"))
        if target:
            request.session[admin_session.SESSION_NEXT] = target
        return render(request, "haldus/sign_in.html", {"form": SignInForm()})

    form = SignInForm(request.POST)
    if not form.is_valid():
        return render(
            request,
            "haldus/sign_in.html",
            {"form": SignInForm(), "error": admin_session.SIGN_IN_REFUSED},
            status=400,
        )
    outcome = admin_session.attempt_password(
        request, email=form.cleaned_data["email"], password=form.cleaned_data["password"]
    )
    if outcome.status == "code":
        return redirect("haldus:sign_in_code")
    if outcome.status == "enrol":
        return redirect("haldus:enrol")
    retry = SignInForm(initial={"email": form.cleaned_data["email"]})
    locked = outcome.status == "locked"
    return render(
        request,
        "haldus/sign_in.html",
        {
            "form": retry,
            "error": _wait_message(outcome.wait) if locked else admin_session.SIGN_IN_REFUSED,
        },
        status=429 if locked else 400,
    )


@never_cache
@shared_gate_only
@require_http_methods(["GET", "POST"])
def sign_in_code(request: HttpRequest) -> HttpResponse | HttpResponseBase:
    user = admin_session.pending_user(request)
    if user is None:
        return redirect("haldus:sign_in")
    if request.method == "GET":
        return render(request, "haldus/code.html", {"form": SecondFactorForm()})
    form = SecondFactorForm(request.POST)
    if not form.is_valid():
        return render(
            request,
            "haldus/code.html",
            {"form": SecondFactorForm(), "error": admin_session.CODE_REFUSED},
            status=400,
        )
    outcome = admin_session.verify_code(request, code=form.cleaned_data["code"], kind=form.kind())
    if outcome.status == "verified":
        messages.success(request, "Halduri seanss on avatud.")
        return _onward(request)
    if admin_session.pending_user(request) is None:
        return redirect("haldus:sign_in")
    locked = outcome.status == "locked"
    return render(
        request,
        "haldus/code.html",
        {
            "form": SecondFactorForm(),
            "error": _wait_message(outcome.wait) if locked else admin_session.CODE_REFUSED,
        },
        status=429 if locked else 400,
    )


@never_cache
@shared_gate_only
@require_http_methods(["GET", "POST"])
def enrol(request: HttpRequest) -> HttpResponse | HttpResponseBase:
    """The first step-up of an administrator without an authenticator: set one up."""
    enrolment = admin_session.begin_enrolment(request)
    if enrolment is None:
        return redirect("haldus:sign_in")
    if request.method == "GET":
        return render(
            request, "haldus/enrol.html", {"enrolment": enrolment, "form": EnrolmentForm()}
        )
    form = EnrolmentForm(request.POST)
    if form.is_valid():
        enrolled = admin_session.confirm_enrolment(request, code=form.cleaned_data["code"])
        if enrolled.outcome.status == "verified":
            # Shown in this response and never again: not stored, not redirected to.
            return render(request, "haldus/recovery_codes.html", {"codes": enrolled.recovery_codes})
        if admin_session.pending_user(request) is None:
            return redirect("haldus:sign_in")
        message = (
            _wait_message(enrolled.outcome.wait)
            if enrolled.outcome.status == "locked"
            else "Kood ei sobinud. Kontrolli, et rakendus näitab sama kontot."
        )
        form.add_error("code", message)
    return render(request, "haldus/enrol.html", {"enrolment": enrolment, "form": form}, status=400)


@never_cache
@shared_gate_only
@require_http_methods(["GET", "POST"])
def reauthenticate(request: HttpRequest) -> HttpResponse | HttpResponseBase:
    """Password and code again, before a change once the last proof is too old."""
    next_path = _safe(request, request.POST.get("next") or request.GET.get("next"))
    if admin_session.verified_administrator(request) is None:
        if next_path:
            return redirect(f"{reverse('haldus:sign_in')}?{urlencode({'next': next_path})}")
        return redirect("haldus:sign_in")
    admin_session.note_activity(request)
    context: dict[str, Any] = {"next_url": next_path}
    if request.method == "GET":
        return render(
            request, "haldus/reauthenticate.html", {**context, "form": ReauthenticationForm()}
        )
    form = ReauthenticationForm(request.POST)
    error = "Parool või kood ei sobinud."
    if form.is_valid():
        outcome = admin_session.reauthenticate(
            request, password=form.cleaned_data["password"], code=form.cleaned_data["code"]
        )
        if outcome.accepted:
            return HttpResponseRedirect(next_path or reverse("haldus:index"))
        if outcome.wait:
            error = _wait_message(outcome.wait)
    return render(
        request,
        "haldus/reauthenticate.html",
        {**context, "form": ReauthenticationForm(), "error": error},
        status=400,
    )


@never_cache
@shared_gate_only
@require_http_methods(["POST"])
def sign_out(request: HttpRequest) -> HttpResponse | HttpResponseBase:
    """End the administrator session only. The gate and the persona stay as they were."""
    admin_session.end(request, reason="signed_out")
    messages.info(request, "Halduri seanss on lõpetatud.")
    return redirect("core:home")
