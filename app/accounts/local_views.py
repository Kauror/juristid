"""The personal sign-in pages: sign in, second factor, links, password, security.

Every view here answers **404 in any mode but `local_password`** — not a
redirect and not a "not available" page, because a route that admits it exists
is a route somebody probes (the shared gate's own views do the same,
app/accounts/views.py). With the mode dormant, as it is in every deployment
today, none of these pages exists as far as a visitor can tell.

The views hold no rule. They read a form, call `app.accounts.local_auth` or
`app.accounts.credentials`, and render what came back.
"""

from __future__ import annotations

import functools
from collections.abc import Callable
from typing import Any
from urllib.parse import urlencode, urlsplit

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.http import Http404, HttpRequest, HttpResponse, HttpResponseRedirect
from django.http.response import HttpResponseBase
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods

from app.accounts import capabilities, credentials, local_auth, mail, mfa, tokens
from app.accounts.enums import CredentialTokenPurpose
from app.accounts.forms import (
    ChangePasswordForm,
    EnrolmentForm,
    ForgotPasswordForm,
    NewPasswordForm,
    ReauthenticationForm,
    SecondFactorForm,
    SignInForm,
)
from app.core.request_params import safe_local_path

# -- the boundary -------------------------------------------------------------------


def local_mode_only[R: HttpResponseBase](view: Callable[..., R]) -> Callable[..., R]:
    """404 unless this deployment signs people in with personal passwords."""

    @functools.wraps(view)
    def wrapper(request: HttpRequest, *args: Any, **kwargs: Any) -> R:
        if not local_auth.is_local_password():
            raise Http404("Isiklik sisselogimine ei ole selles keskkonnas kasutusel.")
        return view(request, *args, **kwargs)

    return wrapper


def signed_in_here[R: HttpResponseBase](
    view: Callable[..., R],
) -> Callable[..., R | HttpResponseBase]:
    """A person signed in by this mode, and nobody else.

    The middleware already sends everybody else to the sign-in page; this is
    the same rule stated at the view, so a page cannot be reached by a session
    the middleware did not see (a test client with `force_login`, say).
    """

    @functools.wraps(view)
    def wrapper(request: HttpRequest, *args: Any, **kwargs: Any) -> R | HttpResponseBase:
        if not local_auth.is_local_password():
            raise Http404("Isiklik sisselogimine ei ole selles keskkonnas kasutusel.")
        if not local_auth.is_local_session(request):
            return redirect("accounts:sign_in")
        return view(request, *args, **kwargs)

    return wrapper


def recently_authenticated[R: HttpResponseBase](
    view: Callable[..., R],
) -> Callable[..., R | HttpResponseBase]:
    """Ask for the password (and code) again when the last proof is stale.

    For changes an attacker with a borrowed, unlocked laptop must not be able
    to make: a second factor removed or replaced, recovery codes reissued, and
    every account-administration change. The person comes back to the page
    they were on, never to the endpoint that asked.
    """

    @functools.wraps(view)
    def wrapper(request: HttpRequest, *args: Any, **kwargs: Any) -> R | HttpResponseBase:
        if local_auth.needs_reauthentication(request):
            back = _safe_next(request) or _referring_page(request) or reverse("accounts:profile")
            return HttpResponseRedirect(_reauthenticate_then(back))
        return view(request, *args, **kwargs)

    return wrapper


def _reauthenticate_then(back: str) -> str:
    return f"{reverse('accounts:reauthenticate')}?{urlencode({'next': back})}"


def _safe_next(request: HttpRequest) -> str:
    return safe_local_path(
        request.POST.get("next") or request.GET.get("next"),
        host=request.get_host(),
        require_https=request.is_secure(),
    )


def _referring_page(request: HttpRequest) -> str:
    """The page a POST came from, if it is this site's. Only for coming back to it."""
    referer = request.META.get("HTTP_REFERER", "")
    parts = urlsplit(referer)
    candidate = parts.path + (f"?{parts.query}" if parts.query else "")
    if not parts.path:
        return ""
    return safe_local_path(candidate, host=request.get_host(), require_https=request.is_secure())


def _wait_message(wait: int) -> str:
    minutes = max(1, round(wait / 60))
    return f"Liiga palju katseid. Proovi umbes {minutes} minuti pärast uuesti."


#: What the sign-in page says when it was sent here for a reason.
_SIGN_IN_NOTICES = {
    "valjas": "Sinu konto on välja lülitatud. Pöördu kasutajate halduri poole.",
    "aegunud": "Seanss aegus. Logi uuesti sisse.",
    "parool-seatud": "Parool on seatud. Logi nüüd sisse.",
    "valjunud": "Oled välja logitud.",
}


# -- signing in ------------------------------------------------------------------------


@never_cache
@local_mode_only
@require_http_methods(["GET", "POST"])
def sign_in(request: HttpRequest) -> HttpResponse:
    """E-post → parool → (kood) → Juristid."""
    if local_auth.is_local_session(request):
        return redirect("core:home")

    if request.method == "GET":
        next_path = _safe_next(request)
        if next_path:
            request.session[local_auth.SESSION_NEXT] = next_path
        notice = _SIGN_IN_NOTICES.get(request.GET.get("olek", ""), "")
        return render(
            request, "accounts/local/sign_in.html", {"form": SignInForm(), "notice": notice}
        )

    form = SignInForm(request.POST)
    if not form.is_valid():
        return render(
            request,
            "accounts/local/sign_in.html",
            {"form": SignInForm(), "error": local_auth.SIGN_IN_REFUSED},
            status=400,
        )
    outcome = local_auth.attempt_sign_in(
        request, email=form.cleaned_data["email"], password=form.cleaned_data["password"]
    )
    if outcome.status == "second_factor":
        return redirect("accounts:sign_in_second_factor")
    if outcome.status == "signed_in":
        return _after_sign_in(request)
    # The address is kept; the password never is.
    retry = SignInForm(initial={"email": form.cleaned_data["email"]})
    if outcome.status == "locked":
        return render(
            request,
            "accounts/local/sign_in.html",
            {"form": retry, "error": _wait_message(outcome.wait)},
            status=429,
        )
    return render(
        request,
        "accounts/local/sign_in.html",
        {"form": retry, "error": local_auth.SIGN_IN_REFUSED},
        status=400,
    )


def _after_sign_in(request: HttpRequest) -> HttpResponse:
    if local_auth.enrolment_pending(request):
        return redirect("accounts:security_enrol")
    target = request.session.pop(local_auth.SESSION_NEXT, "") or ""
    safe = safe_local_path(target, host=request.get_host(), require_https=request.is_secure())
    return HttpResponseRedirect(safe or reverse("core:home"))


@never_cache
@local_mode_only
@require_http_methods(["GET", "POST"])
def sign_in_second_factor(request: HttpRequest) -> HttpResponse:
    if local_auth.pending_user(request) is None:
        return redirect("accounts:sign_in")
    if request.method == "GET":
        return render(request, "accounts/local/second_factor.html", {"form": SecondFactorForm()})

    form = SecondFactorForm(request.POST)
    if not form.is_valid():
        return render(
            request,
            "accounts/local/second_factor.html",
            {"form": SecondFactorForm(), "error": local_auth.SECOND_FACTOR_REFUSED},
            status=400,
        )
    outcome = local_auth.verify_second_factor(
        request, code=form.cleaned_data["code"], kind=form.kind()
    )
    if outcome.status == "signed_in":
        return _after_sign_in(request)
    if local_auth.pending_user(request) is None:
        return redirect("accounts:sign_in")
    error = (
        _wait_message(outcome.wait)
        if outcome.status == "locked"
        else local_auth.SECOND_FACTOR_REFUSED
    )
    return render(
        request,
        "accounts/local/second_factor.html",
        {"form": SecondFactorForm(), "error": error},
        status=429 if outcome.status == "locked" else 400,
    )


# -- one-time links ------------------------------------------------------------------------


def _link_view(request: HttpRequest, *, purpose: str, route: str, template: str) -> HttpResponse:
    """Activation and reset share one shape.

    A link arrives as `?kood=…`. It is moved into the session and the browser
    is redirected to the bare address at once, so the secret leaves the address
    bar, the history and any Referer before the page that uses it renders. The
    production access log records paths, never queries (ENG-071).
    """
    raw = request.GET.get(mail.LINK_PARAMETER)
    if raw is not None:
        request.session[local_auth.SESSION_LINK] = {"purpose": str(purpose), "token": raw[:200]}
        return redirect(route)

    stored = request.session.get(local_auth.SESSION_LINK) or {}
    token = stored.get("token", "") if stored.get("purpose") == str(purpose) else ""
    holder = credentials.link_holder(tokens.peek(token, purpose=purpose)) if token else None
    if holder is None:
        request.session.pop(local_auth.SESSION_LINK, None)
        return render(request, template, {"invalid": True}, status=400)

    if request.method == "GET":
        return render(request, template, {"form": NewPasswordForm(), "holder": holder})

    form = NewPasswordForm(request.POST)
    if not form.is_valid():
        return render(request, template, {"form": form, "holder": holder}, status=400)
    try:
        user = credentials.set_password_with_link(
            token=token, purpose=purpose, password=form.cleaned_data["password"]
        )
    except ValidationError as error:
        form.add_error("password", error)
        return render(request, template, {"form": form, "holder": holder}, status=400)
    request.session.pop(local_auth.SESSION_LINK, None)
    if user is None:
        return render(request, template, {"invalid": True}, status=400)
    return redirect(f"{reverse('accounts:sign_in')}?olek=parool-seatud")


@never_cache
@local_mode_only
@require_http_methods(["GET", "POST"])
def activate(request: HttpRequest) -> HttpResponse:
    return _link_view(
        request,
        purpose=CredentialTokenPurpose.ACTIVATION,
        route="accounts:activate",
        template="accounts/local/activate.html",
    )


@never_cache
@local_mode_only
@require_http_methods(["GET", "POST"])
def reset_password(request: HttpRequest) -> HttpResponse:
    return _link_view(
        request,
        purpose=CredentialTokenPurpose.PASSWORD_RESET,
        route="accounts:reset_password",
        template="accounts/local/reset_password.html",
    )


@never_cache
@local_mode_only
@require_http_methods(["GET", "POST"])
def forgot_password(request: HttpRequest) -> HttpResponse:
    """The same answer for every address, real or not."""
    if request.method == "GET":
        return render(
            request, "accounts/local/forgot_password.html", {"form": ForgotPasswordForm()}
        )
    form = ForgotPasswordForm(request.POST)
    if form.is_valid():
        credentials.request_password_reset(request, email=form.cleaned_data["email"])
    return render(request, "accounts/local/forgot_password.html", {"sent": True})


# -- the signed-in person's own pages ---------------------------------------------------------


@never_cache
@signed_in_here
@require_http_methods(["GET"])
def profile(request: HttpRequest) -> HttpResponse:
    from app.core.authorization import effective_capabilities

    user = local_auth.person(request)
    held = effective_capabilities(user)
    return render(
        request,
        "accounts/local/profile.html",
        {
            "person": user,
            "capabilities": [
                capabilities.TEXT[capability]
                for capability in capabilities.ALL_CAPABILITIES
                if capability in held
            ],
            "has_second_factor": mfa.has_second_factor(user),
            "recovery_codes_left": mfa.remaining_recovery_codes(user),
            "may_administer": local_auth.may_administer_accounts(request),
            "nav_active": "profiil",
        },
    )


@never_cache
@signed_in_here
@require_http_methods(["GET", "POST"])
def change_password(request: HttpRequest) -> HttpResponse:
    user = local_auth.person(request)
    if request.method == "GET":
        return render(
            request, "accounts/local/change_password.html", {"form": ChangePasswordForm()}
        )
    form = ChangePasswordForm(request.POST)
    if not form.is_valid():
        return render(request, "accounts/local/change_password.html", {"form": form}, status=400)
    try:
        outcome = credentials.change_password(
            request,
            user=user,
            current=form.cleaned_data["current"],
            new=form.cleaned_data["password"],
        )
    except ValidationError as error:
        form.add_error("password", error)
        return render(request, "accounts/local/change_password.html", {"form": form}, status=400)
    if outcome.status == "changed":
        messages.success(request, "Parool on muudetud. Teised seansid lõpetati.")
        return redirect("accounts:profile")
    form.add_error(
        "current",
        _wait_message(outcome.wait) if outcome.status == "locked" else "Praegune parool on vale.",
    )
    return render(
        request,
        "accounts/local/change_password.html",
        {"form": form},
        status=429 if outcome.status == "locked" else 400,
    )


@never_cache
@signed_in_here
@require_http_methods(["GET"])
def security(request: HttpRequest) -> HttpResponse:
    user = local_auth.person(request)
    return render(
        request,
        "accounts/local/security.html",
        {
            "has_second_factor": mfa.has_second_factor(user),
            "mandatory": local_auth.second_factor_mandatory(user),
            "recovery_codes_left": mfa.remaining_recovery_codes(user),
            "nav_active": "profiil",
        },
    )


@never_cache
@signed_in_here
@require_http_methods(["GET", "POST"])
def security_enrol(request: HttpRequest) -> HttpResponse:
    """Set up — or replace — the authenticator app.

    A first enrolment needs only the session the person just signed in with.
    Replacing one that already works is a change somebody at an unattended
    keyboard must not be able to make, so it asks for a fresh proof first.
    """
    user = local_auth.person(request)
    replacing = mfa.has_second_factor(user)
    if replacing and local_auth.needs_reauthentication(request):
        return HttpResponseRedirect(_reauthenticate_then(reverse("accounts:security_enrol")))

    sealed = request.session.get(local_auth.SESSION_SEALED_ENROLMENT, "")
    enrolment = mfa.resume_enrolment(user, sealed) if sealed else None

    if request.method == "GET" or enrolment is None:
        enrolment = mfa.begin_enrolment(user)
        request.session[local_auth.SESSION_SEALED_ENROLMENT] = enrolment.sealed
        return render(
            request,
            "accounts/local/enrol.html",
            {
                "enrolment": enrolment,
                "form": EnrolmentForm(),
                "replacing": replacing,
                "mandatory": local_auth.enrolment_pending(request),
            },
        )

    form = EnrolmentForm(request.POST)
    codes = None
    if form.is_valid():
        codes = credentials.enrol_second_factor(
            request, user=user, sealed=enrolment.sealed, code=form.cleaned_data["code"]
        )
    if codes is None:
        if form.is_valid():
            form.add_error("code", "Kood ei sobinud. Kontrolli, et rakendus näitab sama kontot.")
        return render(
            request,
            "accounts/local/enrol.html",
            {
                "enrolment": enrolment,
                "form": form,
                "replacing": replacing,
                "mandatory": local_auth.enrolment_pending(request),
            },
            status=400,
        )
    request.session.pop(local_auth.SESSION_SEALED_ENROLMENT, None)
    local_auth.mark_second_factor_enrolled(request)
    # Shown in this response and never again: not stored, not redirected to.
    return render(request, "accounts/local/recovery_codes.html", {"codes": codes, "first": True})


@never_cache
@signed_in_here
@require_http_methods(["POST"])
@recently_authenticated
def security_recovery_codes(request: HttpRequest) -> HttpResponse:
    user = local_auth.person(request)
    if not mfa.has_second_factor(user):
        return redirect("accounts:security")
    codes = credentials.regenerate_recovery_codes(user=user)
    return render(request, "accounts/local/recovery_codes.html", {"codes": codes, "first": False})


@never_cache
@signed_in_here
@require_http_methods(["POST"])
@recently_authenticated
def security_remove(request: HttpRequest) -> HttpResponse:
    user = local_auth.person(request)
    if local_auth.second_factor_mandatory(user):
        messages.error(
            request,
            "Sinu kontol on teine tegur kohustuslik. Saad autentimisrakenduse välja vahetada, "
            "aga mitte eemaldada.",
        )
        return redirect("accounts:security")
    credentials.remove_own_second_factor(request, user=user)
    messages.success(request, "Teine tegur on eemaldatud.")
    return redirect("accounts:security")


@never_cache
@signed_in_here
@require_http_methods(["GET", "POST"])
def reauthenticate(request: HttpRequest) -> HttpResponse:
    user = local_auth.person(request)
    has_code = mfa.has_second_factor(user)
    next_path = _safe_next(request)
    context: dict[str, Any] = {"has_code": has_code, "next_url": next_path}
    if request.method == "GET":
        return render(
            request,
            "accounts/local/reauthenticate.html",
            {**context, "form": ReauthenticationForm()},
        )
    form = ReauthenticationForm(request.POST)
    if form.is_valid():
        outcome = local_auth.reauthenticate(
            request, password=form.cleaned_data["password"], code=form.cleaned_data["code"]
        )
        if outcome.accepted:
            return HttpResponseRedirect(next_path or reverse("accounts:profile"))
        error = _wait_message(outcome.wait) if outcome.wait else "Parool või kood ei sobinud."
    else:
        error = "Parool või kood ei sobinud."
    return render(
        request,
        "accounts/local/reauthenticate.html",
        {**context, "form": ReauthenticationForm(), "error": error},
        status=400,
    )
