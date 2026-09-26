"""Local development sign-in.

This is the only authentication path that exists in Stage 0 and it is inert
unless ``DEV_LOGIN_ENABLED`` is on, which a system check refuses to allow
outside a debug environment. Production authenticates through Microsoft Entra
ID (docs/adr/0004-authentication-direction.md).
"""

from __future__ import annotations

import hashlib
import hmac
import uuid
from typing import Any

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import login, logout
from django.core.cache import cache
from django.db import connection, transaction
from django.db.models import QuerySet
from django.http import Http404, HttpRequest, HttpResponse, HttpResponseRedirect
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.http import require_http_methods

from app.accounts import shared_gate
from app.accounts.models import User
from app.accounts.selectors import persona_candidates, persona_from_id
from app.audit.enums import SecurityEventType
from app.audit.services import record_security_event
from app.core.request_params import safe_local_path

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


#: Advisory-lock namespace for the PIN counter. Arbitrary but fixed, and not the
#: search subsystem's (`app.search.indexing._LOCK_NAMESPACE`), so the two can
#: never wait on each other.
_PIN_LOCK_NAMESPACE = 31337


def _hold_attempt_counter(request: HttpRequest) -> None:
    """Take this client's PIN counter for the rest of the transaction.

    The counter lives in Django's database cache, whose ``incr`` is a ``get``
    followed by a ``set`` with nothing making two writers take turns, and whose
    rows cannot be locked before they exist. A PostgreSQL advisory lock keyed on
    the client is what serialises them instead: no schema, released by the
    transaction's end whatever happens, and per client — two keys only share a
    lock if their 32-bit digests collide, which makes two strangers take turns
    for a moment and locks nobody out.
    """
    digest = hashlib.sha256(_attempt_key(request).encode("utf-8")).digest()
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT pg_advisory_xact_lock(%s, %s)",
            [_PIN_LOCK_NAMESPACE, int.from_bytes(digest[:4], "big", signed=True)],
        )


def _is_locked_out(request: HttpRequest) -> bool:
    if not settings.DEV_LOGIN_PIN:
        return False
    return cache.get(_attempt_key(request), 0) >= settings.DEV_LOGIN_PIN_MAX_ATTEMPTS


def _record_failure(request: HttpRequest) -> None:
    key = _attempt_key(request)
    # add() only sets when absent, so the window starts at the first failure.
    # (On the database cache the deployments use, `incr` is a get and a `set`
    # with the cache's default timeout, so each counted miss also moves the
    # window's end to that timeout from now — with the shipped 300/300, the
    # lockout ends five minutes after the miss that armed it. Unchanged here.)
    #
    # Only ever called under `_hold_attempt_counter`: that lock is what makes
    # the get-and-set inside `incr` safe against a concurrent miss.
    cache.add(key, 0, timeout=settings.DEV_LOGIN_PIN_LOCKOUT_SECONDS)
    try:
        cache.incr(key)
    except ValueError:  # pragma: no cover - the key expired between the two calls
        cache.set(key, 1, timeout=settings.DEV_LOGIN_PIN_LOCKOUT_SECONDS)


def _pin_is_correct(supplied: str) -> bool:
    # Constant-time: a four-digit secret is small enough that a timing oracle on
    # the prefix would meaningfully narrow it.
    return hmac.compare_digest(supplied.strip(), settings.DEV_LOGIN_PIN)


def _spend_pin_attempt(request: HttpRequest) -> str:
    """Check the lockout, compare the PIN and count a miss, as one step.

    Returns ``"locked_out"``, ``"bad_pin"``, or ``""`` for a correct PIN.

    Under the client's counter lock, because the three used to be separate: the
    lockout was read, the PIN compared, and the miss counted with a
    read-then-write increment. Attempts fired together all read "not locked",
    all had their PIN compared, and could overwrite each other's increments — a
    burst got more than `DEV_LOGIN_PIN_MAX_ATTEMPTS` guesses at a four-digit
    secret (ENG-070). Taking turns, the attempt after the one that reaches the
    limit finds it reached and is refused without its PIN being compared.

    The window is what it was: it starts at the first miss, the limit-th miss is
    still compared and counted, and every attempt after it is refused until the
    window expires or a correct PIN signs somebody in.
    """
    with transaction.atomic():
        _hold_attempt_counter(request)
        if _is_locked_out(request):
            return "locked_out"
        if not _pin_is_correct(request.POST.get("pin", "")):
            _record_failure(request)
            return "bad_pin"
    return ""


@require_http_methods(["GET", "POST"])
def dev_login(request: HttpRequest) -> HttpResponse:
    _require_dev_login()

    users = User.objects.filter(is_synthetic=True, is_active=True).order_by("display_name")

    if request.method == "POST":
        # No PIN, no counter: the environments without one have no cache table
        # either, and the sign-in must not reach for it (see the success path).
        refusal = _spend_pin_attempt(request) if settings.DEV_LOGIN_PIN else ""
        if refusal == "locked_out":
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

        if refusal == "bad_pin":
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
        #
        # Guarded on the PIN being configured at all. Without the guard this
        # touches the cache on every successful sign-in, including the many
        # environments that have no PIN and therefore no cache table — which is
        # a 500 on the one path that must always work.
        if settings.DEV_LOGIN_PIN:
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
    """Leave. Both the persona and the gate, in that order.

    `logout` flushes the session, which takes the gate state with it — so
    signing out really does mean the password is asked for again, rather than
    dropping back to a still-open door with nobody standing in it.
    """
    actor = request.user if request.user.is_authenticated else None
    if shared_gate.is_shared_gate():
        record_security_event(
            event_type=SecurityEventType.SHARED_GATE_CLOSED,
            actor=actor,
            ip_address=request.META.get("REMOTE_ADDR"),
            user_agent=request.META.get("HTTP_USER_AGENT", ""),
            detail=shared_gate.audit_detail(request),
        )
    shared_gate.close_gate(request)
    logout(request)
    response = redirect("core:home")
    # What the browser itself kept of the session goes with it: the pages are
    # `no-store` already, and this clears the origin's storage as well, where
    # an older htmx kept copies of the register (ENG-009). "storage" only —
    # not cookies, which `logout` has dealt with, and not the HTTP cache, which
    # a `no-store` page never entered.
    response["Clear-Site-Data"] = '"storage"'
    return response


# --------------------------------------------------------------------------
# The shared gate, and the persona picked behind it
# --------------------------------------------------------------------------


@require_http_methods(["GET", "POST"])
def gate(request: HttpRequest) -> HttpResponse:
    """One password for the department. Not one identity.

    Deliberately says nothing about *why* an attempt failed. "Wrong password",
    "locked out" and "no password is configured here" are three different
    pieces of information, and only somebody probing the door benefits from
    being able to tell them apart (Stage-2D auth brief 9).
    """
    if not shared_gate.is_shared_gate():
        raise Http404("This deployment does not use a shared gate.")

    if shared_gate.has_passed(request):
        return redirect(settings.LOGIN_REDIRECT_URL)

    context: dict[str, Any] = {}

    if request.method == "POST":
        # The lockout check, the verification and the count are one step under
        # this client's lock, so parallel attempts cannot all be verified before
        # the one that arms the lockout is counted (ENG-070).
        outcome = shared_gate.attempt(request, request.POST.get("password", ""))
        if not outcome.passed:
            return _gate_refused(request, context, reason=outcome.reason, wait=outcome.wait)

        shared_gate.open_gate(request)
        record_security_event(
            event_type=SecurityEventType.SHARED_GATE_PASSED,
            ip_address=request.META.get("REMOTE_ADDR"),
            user_agent=request.META.get("HTTP_USER_AGENT", ""),
            detail=shared_gate.audit_detail(request),
        )
        return redirect(settings.LOGIN_REDIRECT_URL)

    return render(request, "accounts/shared_gate.html", context)


def _gate_refused(
    request: HttpRequest, context: dict[str, Any], *, reason: str, wait: int
) -> HttpResponse:
    record_security_event(
        event_type=SecurityEventType.AUTHENTICATION_FAILED,
        succeeded=False,
        ip_address=request.META.get("REMOTE_ADDR"),
        user_agent=request.META.get("HTTP_USER_AGENT", ""),
        # The reason is recorded for an operator reading the log, and not shown.
        detail=shared_gate.audit_detail(request, path="shared_gate", reason=reason),
    )
    message = "Vale parool."
    if wait:
        minutes = max(1, round(wait / 60))
        message = f"Liiga palju katseid. Proovi umbes {minutes} minuti pärast uuesti."
    return render(
        request,
        "accounts/shared_gate.html",
        {**context, "error": message},
        status=429 if wait else 400,
    )


@require_http_methods(["GET"])
def choose_persona(request: HttpRequest) -> HttpResponse:
    """Whose work am I looking at?

    Not a sign-in page, and worded so nobody mistakes it for one. Picking a name
    changes which work the application shows and proves nothing about who is
    reading it.

    The list is the department's policy and legal people, from
    `app.accounts.selectors.persona_candidates` — the same population `act_as`
    accepts a POST against, so the page and the endpoint cannot disagree about
    who is selectable (docs/adr/0034).
    """
    if not shared_gate.is_shared_gate():
        raise Http404("This deployment does not use a persona selector.")
    if not shared_gate.has_passed(request):
        return redirect("accounts:shared_gate")

    return render(
        request,
        "accounts/choose_persona.html",
        {
            # A list rather than the queryset: the page names each person
            # relative to the others on it, so the population is read once per
            # row (`app/accounts/naming.py`).
            "people": list(persona_candidates()),
            "current": request.user if request.user.is_authenticated else None,
            "next_url": _safe_next(request),
        },
    )


@require_http_methods(["POST"])
def act_as(request: HttpRequest) -> HttpResponse:
    """Become a persona, or stop being one. Logged either way."""
    if not shared_gate.is_shared_gate():
        raise Http404("This deployment does not use a persona selector.")
    if not shared_gate.has_passed(request):
        return redirect("accounts:shared_gate")

    previous = request.user if request.user.is_authenticated else None
    raw = request.POST.get("user_id", "")

    if raw == "":
        # Stepping back to the department view. Worth having: it is the only way
        # to see what a page looks like to somebody with no persona selected.
        logout(request)
        shared_gate.open_gate(request)
        _record_persona_change(request, previous=previous, chosen=None)
        # The same safe target a named choice gets. Switching from the top bar
        # has to leave somebody where they were reading, and "somewhere else
        # entirely" is as disorienting when the choice is *nobody* as when it is
        # a colleague. A target that needs a persona — Minu töö — then answers
        # with the application's usual redirect to this page, which is the
        # honest outcome rather than a page invented for a reader who has not
        # said who they are (Vali kasutaja brief 19, 23).
        return HttpResponseRedirect(_safe_next(request) or reverse("matters:overview"))

    # The central candidate population, not "every active account". A crafted
    # POST carrying an administrator's, a superuser's or a reader's identifier
    # has to be refused by the *endpoint*: everybody behind the shared door can
    # reach this view, so a list narrowed only in the template narrows nothing
    # (app/accounts/selectors.py, docs/adr/0034).
    person = persona_from_id(raw)
    if person is None:
        messages.error(request, "Vali kehtiv kasutaja.")
        return redirect("accounts:choose_persona")

    login(request, person, backend=MODEL_BACKEND)
    # `login` cycles the session key and would otherwise drop the gate state
    # with it, sending somebody back to the password form for changing persona.
    shared_gate.open_gate(request)
    shared_gate.note_persona_chosen(request)
    _record_persona_change(request, previous=previous, chosen=person)
    messages.success(request, f"Vaatad rakendust nüüd kasutajana {person.display_name}.")
    return HttpResponseRedirect(_safe_next(request) or reverse("matters:my_work"))


def _record_persona_change(request: HttpRequest, *, previous: Any, chosen: Any) -> None:
    """Every persona change, with what it is and is not evidence of."""
    record_security_event(
        event_type=SecurityEventType.PERSONA_SELECTED,
        actor=chosen,
        ip_address=request.META.get("REMOTE_ADDR"),
        user_agent=request.META.get("HTTP_USER_AGENT", ""),
        detail=shared_gate.audit_detail(
            request,
            previous_persona=str(previous.pk) if previous is not None else None,
            chosen_persona=str(chosen.pk) if chosen is not None else None,
        ),
    )


def _safe_next(request: HttpRequest) -> str:
    """A redirect target, only if it points back at this site.

    An open redirect on the one page everybody passes through would be a
    convenient place to send somebody somewhere else.
    """
    # A path on this site and nothing else: `/teemad/`, never a host (even
    # this one), `//elsewhere`, `teemad/` or a bare URL name, which
    # `redirect()` would resolve as a view (ENG-046). Callers hand the answer to
    # `HttpResponseRedirect`, never to `redirect()`.
    return safe_local_path(
        request.POST.get("next") or request.GET.get("next"),
        host=request.get_host(),
        require_https=request.is_secure(),
    )
