"""The authorization server's HTTP surface (docs/adr/0185 §3–§4).

Two kinds of endpoint, and the difference is the whole security model:

* **Browser pages** — `/oauth/authorize` and the enrolment page under
  `/mcp-ligipaas/`. Behind the shared gate *and* a persona, like every page,
  and then behind the persona's own authenticator code. CSRF-protected.
* **Machine endpoints** — the RFC 8414 metadata, `/oauth/token` and
  `/oauth/revoke`. Called by the client's backend, never by a browser session,
  so they bypass the gate (an exact-path exemption in
  `app.accounts.shared_gate`) and authenticate the *client* themselves. No
  cookie means anything to them, so they are CSRF-exempt.

Every endpoint is a 404 while MCP access is switched off.
"""

from __future__ import annotations

import functools
import hashlib
import json
from collections.abc import Callable
from typing import Any
from urllib.parse import urlencode, urlsplit

from django.conf import settings
from django.contrib.auth.views import redirect_to_login
from django.core.cache import cache
from django.http import (
    Http404,
    HttpRequest,
    HttpResponse,
    HttpResponseRedirect,
    JsonResponse,
    QueryDict,
)
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.cache import add_never_cache_headers
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from app.accounts import shared_gate
from app.core.browser_policy import CONTENT_SECURITY_POLICY_DIRECTIVES
from app.core.client_ip import client_ip
from app.mcp_access import config, credentials, oauth, services
from app.mcp_access.models import McpAccessEventType
from app.mcp_access.services import AuthorizationRequest, OAuthError

#: What the enrolment page keeps in the session between its GET and its POST:
#: the link's proof (never the verifier) and the provisional secret, sealed.
SESSION_ENROLMENT = "mcp_access:enrolment"

#: Token and revocation requests per client address per minute.
RATE_LIMIT = 30
RATE_WINDOW = 60

READ_ONLY_DESCRIPTION = "Ainult lugemine: otsing ja teemade, dokumentide ning ajaloo lugemine"

#: The one answer to a wrong code *and* to a persona that cannot connect (no
#: grant, a pending grant, no business role): until the persona's own current
#: code is verified the page does not say which (MCPSEC-01).
GENERIC_REFUSAL = (
    "Kood ei sobi või sellel kasutajal pole MCP ligipääsu. Sisesta autentija praegune kood; "
    "kui see ikka ei sobi, pöördu Juristidi haldaja poole."
)
SUSPENDED_MESSAGE = (
    "Ligipääs on peatatud, sest valesid koode sisestati liiga palju. "
    "Selle avab uuesti Juristidi haldaja."
)


def mcp_enabled_only(view: Callable[..., HttpResponse]) -> Callable[..., HttpResponse]:
    """A 404 for every method while MCP access is off — before anything else is said.

    Outermost, so a disabled deployment does not even answer 405 for a verb a
    route would not take: there is no route to learn about.
    """

    @functools.wraps(view)
    def wrapper(request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        if not config.is_enabled():
            raise Http404("MCP access is not enabled on this deployment.")
        return view(request, *args, **kwargs)

    return wrapper


# -- discovery ---------------------------------------------------------------------


@mcp_enabled_only
@require_GET
def authorization_server_metadata(request: HttpRequest) -> HttpResponse:
    """RFC 8414. Built from settings only: the request's Host changes nothing in it."""
    metadata = {
        "issuer": config.issuer(),
        "authorization_endpoint": config.authorization_endpoint(),
        "token_endpoint": config.token_endpoint(),
        "revocation_endpoint": config.revocation_endpoint(),
        "response_types_supported": ["code"],
        "response_modes_supported": ["query"],
        "grant_types_supported": ["authorization_code", "refresh_token"],
        "code_challenge_methods_supported": ["S256"],
        "token_endpoint_auth_methods_supported": config.advertised_auth_methods(),
        "token_endpoint_auth_signing_alg_values_supported": ["RS256"],
        "revocation_endpoint_auth_methods_supported": config.advertised_auth_methods(),
        "scopes_supported": [config.scope()],
        "client_id_metadata_document_supported": True,
        "authorization_response_iss_parameter_supported": True,
    }
    response = JsonResponse(metadata)
    response["Cache-Control"] = "public, max-age=300"
    return response


# -- the authorization step ------------------------------------------------------------


def _with_params(uri: str, params: dict[str, str | None]) -> str:
    query = urlencode({key: value for key, value in params.items() if value is not None})
    return f"{uri}{'&' if '?' in uri else '?'}{query}"


def _redirect_back(uri: str, params: dict[str, str | None]) -> HttpResponse:
    response = HttpResponseRedirect(_with_params(uri, {**params, "iss": config.issuer()}))
    add_never_cache_headers(response)
    return response


def _form_action_policy(redirect_uri: str) -> str:
    """The page's CSP, with the client's redirect origin allowed as a form target.

    Browsers apply ``form-action`` to the redirect a form submission answers
    with, so the «Luba» / «Keeldu» POST could not otherwise reach the client's
    registered (and already validated) redirect URI.
    """
    parts = urlsplit(redirect_uri)
    origin = f"{parts.scheme}://{parts.netloc}"
    directives = [
        (name, f"{sources} {origin}" if name == "form-action" else sources)
        for name, sources in CONTENT_SECURITY_POLICY_DIRECTIVES
    ]
    return "; ".join(f"{name} {sources}" for name, sources in directives)


def _error_page(request: HttpRequest, message: str, *, status: int = 400) -> HttpResponse:
    response = render(request, "mcp_access/error.html", {"message": message}, status=status)
    add_never_cache_headers(response)
    return response


def _authorize_page(
    request: HttpRequest,
    parsed: AuthorizationRequest,
    *,
    error: str = "",
    status: int = 200,
) -> HttpResponse:
    # Deliberately nothing about the persona's grant: whether it has one, is
    # locked or is suspended is said only after its own current code has been
    # verified (MCPSEC-01). Every persona sees the same page.
    user: Any = request.user
    switch_url = ""
    if shared_gate.is_shared_gate():
        switch_url = (
            f"{reverse('accounts:choose_persona')}?{urlencode({'next': request.get_full_path()})}"
        )
    response = render(
        request,
        "mcp_access/authorize.html",
        {
            "client_name": parsed.client.name,
            "description": READ_ONLY_DESCRIPTION,
            "persona": user,
            "error": error,
            "switch_url": switch_url,
        },
        status=status,
    )
    response["Content-Security-Policy"] = _form_action_policy(parsed.redirect_uri)
    add_never_cache_headers(response)
    return response


@mcp_enabled_only
@never_cache
@require_http_methods(["GET", "POST"])
def authorize(request: HttpRequest) -> HttpResponse:
    """The consent page: the persona's own authenticator code, then a one-time code back.

    Parameters always come from the query string, on the POST too (the form
    posts back to the same address), so what is re-validated on «Luba» is
    exactly what was shown.
    """
    parsed = oauth.parse_authorization(request.GET)
    if isinstance(parsed, oauth.EarlyError):
        return _error_page(request, parsed.message)
    if isinstance(parsed, oauth.RedirectError):
        return _redirect_back(
            parsed.redirect_uri,
            {"error": parsed.error, "error_description": parsed.description, "state": parsed.state},
        )
    user: Any = request.user
    if not user.is_authenticated:
        # No persona yet: choose one and come back here, query string and all.
        return redirect_to_login(request.get_full_path())
    if request.method == "GET":
        return _authorize_page(request, parsed)

    ip = client_ip(request)
    decision = request.POST.get("otsus", "")
    if decision == "keeldu":
        services.record_denied(user=user, request=parsed, ip_address=ip)
        return _redirect_back(
            parsed.redirect_uri,
            {
                "error": "access_denied",
                "error_description": "The user denied the request.",
                "state": parsed.state,
            },
        )
    if decision != "luba":
        return _authorize_page(request, parsed, error="Vali «Luba» või «Keeldu».", status=400)
    outcome = services.authorize_attempt(
        user=user,
        request=parsed,
        totp_code=request.POST.get("kood", ""),
        ip_address=ip,
        throttle=_throttle_keys(request, ip),
    )
    if outcome.status == "ok":
        return _redirect_back(parsed.redirect_uri, {"code": outcome.code, "state": parsed.state})
    minutes = max(1, round(outcome.wait / 60))
    if outcome.status == "throttled":
        return _authorize_page(
            request,
            parsed,
            error=f"Liiga palju katseid. Proovi umbes {minutes} minuti pärast uuesti.",
            status=429,
        )
    # Only the persona's own current code reaches these two.
    if outcome.status == "locked":
        return _authorize_page(
            request,
            parsed,
            error=f"Liiga palju valesid koode. Proovi umbes {minutes} minuti pärast uuesti.",
            status=429,
        )
    if outcome.status == "suspended":
        return _authorize_page(request, parsed, error=SUSPENDED_MESSAGE, status=403)
    # No grant, a pending one, no business role, or a wrong code: one answer.
    return _authorize_page(request, parsed, error=GENERIC_REFUSAL, status=400)


def _throttle_keys(request: HttpRequest, ip: str | None) -> list[services.ThrottleKey]:
    """This gate session's and this address's counters (MCPSEC-01).

    The session is named by its gate pass, which survives a persona change —
    `login` gives the session a new identifier, the pass is carried — so
    switching persona does not start a fresh count. Outside the shared gate the
    session key stands in. Both are HMAC'd before they are stored.
    """
    gate_pass = shared_gate.gate_passed_at(request) or request.session.session_key or "none"
    return [
        services.ThrottleKey(
            key=shared_gate.key_for_address(f"mcp-authorize|session|{gate_pass}"),
            limit=int(settings.MCP_AUTHORIZE_MAX_FAILURES_PER_SESSION),
        ),
        services.ThrottleKey(
            key=shared_gate.key_for_address(f"mcp-authorize|address|{ip or 'unknown'}"),
            limit=int(settings.MCP_AUTHORIZE_MAX_FAILURES_PER_ADDRESS),
        ),
    ]


# -- the machine endpoints ------------------------------------------------------------


def _no_store(response: HttpResponse) -> HttpResponse:
    response["Cache-Control"] = "no-store"
    response["Pragma"] = "no-cache"
    return response


def _oauth_error(error: OAuthError) -> HttpResponse:
    body: dict[str, str] = {"error": error.error}
    if error.description:
        body["error_description"] = error.description
    response = JsonResponse(body, status=error.status)
    if error.status == 401:
        response["WWW-Authenticate"] = 'Bearer realm="juristid", error="invalid_client"'
    if error.status == 429:
        response["Retry-After"] = str(RATE_WINDOW)
    return _no_store(response)


def _rate_limited(request: HttpRequest, endpoint: str) -> bool:
    """A coarse per-address limit. A rate limit, never identity (app/core/client_ip.py)."""
    address = client_ip(request) or "unknown"
    key = f"mcp_access:rate:{endpoint}:" + hashlib.sha256(address.encode()).hexdigest()
    cache.add(key, 0, timeout=RATE_WINDOW)
    try:
        count = cache.incr(key)
    except ValueError:  # pragma: no cover - expired between the two calls
        cache.set(key, 1, timeout=RATE_WINDOW)
        count = 1
    return count > RATE_LIMIT


def _repeated(params: QueryDict) -> bool:
    """RFC 6749 §3.2: a parameter may not be sent more than once."""
    return any(len(params.getlist(name)) > 1 for name in params)


def _form_params(request: HttpRequest) -> QueryDict | None:
    content_type = request.META.get("CONTENT_TYPE", "").split(";", 1)[0].strip().lower()
    if content_type != "application/x-www-form-urlencoded":
        return None
    return request.POST


@csrf_exempt
@mcp_enabled_only
@require_POST
def token(request: HttpRequest) -> HttpResponse:
    """RFC 6749 §3.2: ``authorization_code`` with PKCE, and ``refresh_token`` with rotation."""
    if _rate_limited(request, "token"):
        return _oauth_error(OAuthError("invalid_request", "Too many requests.", status=429))
    params = _form_params(request)
    if params is None or _repeated(params):
        return _oauth_error(OAuthError("invalid_request", "Malformed token request."))
    client = oauth.authenticate_client(
        params, authorization_header=request.META.get("HTTP_AUTHORIZATION", "")
    )
    if isinstance(client, OAuthError):
        services.record(
            McpAccessEventType.TOKEN_REFUSED,
            succeeded=False,
            client_id=params.get("client_id", "")[:500],
            ip_address=client_ip(request),
            reason="invalid_client",
        )
        return _oauth_error(client)
    grant_type = params.get("grant_type", "")
    # `authenticate_client` accepted an assertion only if it verified.
    auth_method = (
        config.AUTH_METHOD_PRIVATE_KEY_JWT
        if params.get("client_assertion")
        else config.AUTH_METHOD_NONE
    )
    result: services.IssuedTokens | OAuthError
    if grant_type == "authorization_code":
        if not params.get("resource"):
            result = OAuthError("invalid_target", "A resource indicator is required.")
        else:
            result = services.exchange_code(
                client_id=client.client_id,
                code=params.get("code", ""),
                redirect_uri=params.get("redirect_uri", ""),
                code_verifier=params.get("code_verifier", ""),
                resource=params.get("resource", ""),
                ip_address=client_ip(request),
                auth_method=auth_method,
            )
    elif grant_type == "refresh_token":
        result = services.refresh(
            client_id=client.client_id,
            refresh_token=params.get("refresh_token", ""),
            resource=params.get("resource", ""),
            scope=params.get("scope", ""),
            ip_address=client_ip(request),
            auth_method=auth_method,
        )
    else:
        result = OAuthError("unsupported_grant_type", "Unsupported grant_type.")
    if isinstance(result, OAuthError):
        return _oauth_error(result)
    return _no_store(HttpResponse(json.dumps(result.as_json()), content_type="application/json"))


@csrf_exempt
@mcp_enabled_only
@require_POST
def revoke(request: HttpRequest) -> HttpResponse:
    """RFC 7009. 200 whether or not the token existed, once the client has authenticated."""
    if _rate_limited(request, "revoke"):
        return _oauth_error(OAuthError("invalid_request", "Too many requests.", status=429))
    params = _form_params(request)
    if params is None or _repeated(params):
        return _oauth_error(OAuthError("invalid_request", "Malformed revocation request."))
    client = oauth.authenticate_client(
        params, authorization_header=request.META.get("HTTP_AUTHORIZATION", "")
    )
    if isinstance(client, OAuthError):
        return _oauth_error(client)
    services.revoke_token(
        client_id=client.client_id, token=params.get("token", ""), ip_address=client_ip(request)
    )
    return _no_store(HttpResponse(status=200))


# -- enrolment ----------------------------------------------------------------------


def _enrol_page(
    request: HttpRequest, context: dict[str, Any], *, status: int = 200
) -> HttpResponse:
    response = render(request, "mcp_access/enrol.html", context, status=status)
    add_never_cache_headers(response)
    return response


@mcp_enabled_only
@never_cache
@require_http_methods(["GET", "POST"])
def enrol(request: HttpRequest) -> HttpResponse:
    """Enrol the personal authenticator from the one-time link an operator handed out.

    The link arrives as ``?kood=…``. What the session keeps is a *proof* — the
    selector and the SHA-256 of the verifier — and the browser is sent to the
    bare address at once, so the link leaves the address bar, the history and
    any Referer before the page renders (the access log records paths, never
    queries). The link opens only for the persona it was issued to; for anybody
    else it reads exactly like an expired one, so it says nothing about whose
    it is.
    """
    raw = request.GET.get("kood")
    if raw is not None:
        request.session[SESSION_ENROLMENT] = {"proof": credentials.link_proof(raw[:200])}
        return redirect("mcp_access:enrol")
    user: Any = request.user
    if not user.is_authenticated:
        return redirect_to_login(request.get_full_path())

    stored = request.session.get(SESSION_ENROLMENT) or {}
    proof = stored.get("proof", "") if isinstance(stored, dict) else ""
    link = services.link_for(proof, user) if proof else None
    if link is None:
        request.session.pop(SESSION_ENROLMENT, None)
        services.record(
            McpAccessEventType.ENROLMENT_REFUSED,
            succeeded=False,
            user=user,
            ip_address=client_ip(request),
            reason="link_not_usable",
        )
        return _enrol_page(request, {"invalid": True}, status=400)

    label = services.account_label(user)
    enrolment = credentials.resume_enrolment(stored.get("sealed"), label)
    if enrolment is None:
        enrolment = credentials.new_enrolment(label)
        request.session[SESSION_ENROLMENT] = {"proof": proof, "sealed": enrolment.sealed}

    if request.method == "GET":
        return _enrol_page(request, {"enrolment": enrolment, "persona": user})

    confirmed = services.confirm_enrolment(
        proof=proof,
        user=user,
        sealed=enrolment.sealed,
        code=request.POST.get("kood", ""),
        ip_address=client_ip(request),
    )
    if not confirmed:
        return _enrol_page(
            request,
            {
                "enrolment": enrolment,
                "persona": user,
                "error": "Kood ei sobi. Sisesta autentija praegune kuuekohaline kood.",
            },
            status=400,
        )
    request.session.pop(SESSION_ENROLMENT, None)
    return _enrol_page(request, {"done": True, "persona": user})
