"""The authorization server's HTTP surface (docs/adr/0185 §3–§4).

Two kinds of endpoint, and the difference is the whole security model:

* **Browser pages** — `/oauth/authorize` and the enrolment page under
  `/mcp-ligipaas/`. Behind the shared gate like every page, but **not** behind a
  persona: a persona is a choice, not a proof, and READER accounts are never
  offered as one. Each page asks for the account's own sign-in address and
  then proves it — the consent page with a current code from that account's
  own authenticator, the enrolment page with the one-time link an operator
  issued for that account (docs/adr/0185 §3). They render no business content.
  CSRF-protected.
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
import logging
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

logger = logging.getLogger("app.mcp_access")

#: How much of a refused `resource` the authorize WARNING quotes.
LOGGED_RESOURCE_LENGTH = 300

#: What the enrolment page keeps in the session between its GET and its POST:
#: the link's proof (never the verifier) and the provisional secret, sealed.
SESSION_ENROLMENT = "mcp_access:enrolment"

#: Token and revocation requests per client address per minute.
RATE_LIMIT = 30
RATE_WINDOW = 60

READ_ONLY_DESCRIPTION = "Ainult lugemine: otsing ja teemade, dokumentide ning ajaloo lugemine"
#: Shown when the request asks for ``juristid.write`` and writes are switched on.
#: The same for every persona: whether *this* account may prepare changes is
#: decided only once its own current code has been verified, and a persona
#: without the write permission is given read alone (docs/mcp/OAUTH-SCOPES.md).
READ_WRITE_DESCRIPTION = (
    "Lugemine ja muudatuste ettevalmistamine: otsing ja teemade, dokumentide ning ajaloo "
    "lugemine ning muudatuste ettepanekud — iga muudatus vajab eraldi kinnitust Juristidis"
)
READ_WRITE_NOTE = (
    "Ükski muudatus ei jõustu ilma, et kinnitaksid selle ise Juristidis oma autentija koodiga. "
    "Kui sinu ligipääsul muudatuste ettevalmistamise luba ei ole, antakse ainult lugemine."
)

#: The one answer to a wrong code *and* to an address that cannot connect (no
#: such account, no grant, a pending grant, no business role, a technical
#: account): until that account's own current code is verified the page does
#: not say which (MCPSEC-01).
GENERIC_REFUSAL = (
    "Kood ei sobi või sellel kasutajal pole MCP ligipääsu. Sisesta autentija praegune kood; "
    "kui see ikka ei sobi, pöördu Juristidi haldaja poole."
)
#: The enrolment page's one answer to an address that is not the link's account
#: — unknown, another colleague's, or the link stopped working meanwhile.
ENROLMENT_ADDRESS_REFUSAL = (
    "See e-posti aadress ei sobi selle lingiga. Sisesta oma konto e-posti aadress; "
    "kui see ikka ei sobi, küsi Juristidi haldajalt uus link."
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


def scopes_supported() -> list[str]:
    """The read scope, and the write scope only while `MCP_WRITES_ENABLED` is on."""
    scopes = [config.scope()]
    if bool(getattr(settings, "MCP_WRITES_ENABLED", False)):
        scopes.append(services.write_scope())
    return scopes


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
        "scopes_supported": scopes_supported(),
        "client_id_metadata_document_supported": True,
        "authorization_response_iss_parameter_supported": True,
    }
    response = JsonResponse(metadata)
    response["Cache-Control"] = "public, max-age=300"
    return response


# -- the authorization step ------------------------------------------------------------


def _door_closed(request: HttpRequest) -> HttpResponse | None:
    """The way back to the door, unless this request came through it; else None.

    The door is the shared gate (the middleware stops anybody who has not
    passed it, and brings them back here after the password). A persona is
    neither required nor read. Under the other modes the middleware's own
    sign-in is the door, and under `AUTH_MODE=none` — a laptop or CI, no door
    at all — the development sign-in stands in for it.
    """
    if shared_gate.has_passed(request):
        return None
    if not shared_gate.is_shared_gate() and request.user.is_authenticated:
        return None
    return redirect_to_login(request.get_full_path())


def _typed_address(request: HttpRequest) -> str:
    return (request.POST.get("epost") or "").strip()[:320]


def _too_many_attempts(wait: int) -> str:
    minutes = max(1, round(wait / 60))
    return f"Liiga palju katseid. Proovi umbes {minutes} minuti pärast uuesti."


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


def _log_refused_resource(request: HttpRequest) -> None:
    """One WARNING naming the `resource` the authorize step refused (`invalid_target`).

    The operator's only way to learn which URL a client now asks for, and so
    which one `MCP_RESOURCE_URLS` must list: on 2026-10-25 ChatGPT began
    sending its tunnel URL and the refusal left no trace. A redirect error
    comes only after the client and redirect URI were validated, so the
    client id is a registered one. Quoted with `repr` and capped, so a line
    break or a long value cannot forge or flood a log line; never `state` or
    `code_challenge`, which belong to one person's sign-in.
    """
    logger.warning(
        "MCP authorize refused resource %r for client %r (invalid_target); "
        "MCP_RESOURCE_URLS in juristid.env and juristid-mcp.env must list it to allow it",
        request.GET.get("resource", "")[:LOGGED_RESOURCE_LENGTH],
        request.GET.get("client_id", "")[:LOGGED_RESOURCE_LENGTH],
    )


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
    address: str = "",
) -> HttpResponse:
    # Deliberately nothing about any account: which addresses exist, who holds
    # a grant, and whether it is locked or suspended is said only after that
    # account's own current code has been verified (MCPSEC-01). Everybody sees
    # the same page; ``address`` is only what this browser has just typed.
    asks_for_write = oauth.asks_for_write(parsed.scope)
    response = render(
        request,
        "mcp_access/authorize.html",
        {
            "client_name": parsed.client.name,
            "description": READ_WRITE_DESCRIPTION if asks_for_write else READ_ONLY_DESCRIPTION,
            "write_note": READ_WRITE_NOTE if asks_for_write else "",
            "error": error,
            "address": address,
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
    """The consent page: an account's address and its own authenticator code, then a code back.

    Parameters always come from the query string, on the POST too (the form
    posts back to the same address), so what is re-validated on «Luba» is
    exactly what was shown. The account is the one the typed address names,
    never a persona this session may have chosen (docs/adr/0185 §3).
    """
    parsed = oauth.parse_authorization(request.GET)
    if isinstance(parsed, oauth.EarlyError):
        return _error_page(request, parsed.message)
    if isinstance(parsed, oauth.RedirectError):
        if parsed.error == "invalid_target":
            _log_refused_resource(request)
        return _redirect_back(
            parsed.redirect_uri,
            {"error": parsed.error, "error_description": parsed.description, "state": parsed.state},
        )
    closed = _door_closed(request)
    if closed is not None:
        return closed
    if request.method == "GET":
        return _authorize_page(request, parsed)

    ip = client_ip(request)
    decision = request.POST.get("otsus", "")
    if decision == "keeldu":
        services.record_denied(user=None, request=parsed, ip_address=ip)
        return _redirect_back(
            parsed.redirect_uri,
            {
                "error": "access_denied",
                "error_description": "The user denied the request.",
                "state": parsed.state,
            },
        )
    address = _typed_address(request)
    if decision != "luba":
        return _authorize_page(
            request, parsed, error="Vali «Luba» või «Keeldu».", status=400, address=address
        )
    outcome = services.authorize_attempt(
        user=services.account_for_address(address),
        request=parsed,
        totp_code=request.POST.get("kood", ""),
        ip_address=ip,
        throttle=throttle_keys(request, ip),
    )
    if outcome.status == "ok":
        return _redirect_back(parsed.redirect_uri, {"code": outcome.code, "state": parsed.state})
    minutes = max(1, round(outcome.wait / 60))
    if outcome.status == "throttled":
        return _authorize_page(
            request, parsed, error=_too_many_attempts(outcome.wait), status=429, address=address
        )
    # Only the account's own current code reaches these two.
    if outcome.status == "locked":
        return _authorize_page(
            request,
            parsed,
            error=f"Liiga palju valesid koode. Proovi umbes {minutes} minuti pärast uuesti.",
            status=429,
            address=address,
        )
    if outcome.status == "suspended":
        return _authorize_page(
            request, parsed, error=SUSPENDED_MESSAGE, status=403, address=address
        )
    # No such account, no grant, a pending one, no business role, a technical
    # account or a wrong code: one answer.
    return _authorize_page(request, parsed, error=GENERIC_REFUSAL, status=400, address=address)


def throttle_keys(request: HttpRequest, ip: str | None) -> list[services.ThrottleKey]:
    """This gate session's and this address's counters (MCPSEC-01).

    Shared by the consent page, the enrolment page's address step and the
    write-approval page (`services.confirm_write_code`), so a session cannot try
    on one what it could not try on another. The session is named by its gate
    pass, which survives a persona change — `login` gives the session a new
    identifier, the pass is carried — so neither switching persona nor naming
    another address starts a fresh count. Outside the shared gate the session
    key stands in. Both are HMAC'd before they are stored.
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
    queries).

    The link behind the gate is the proof of possession; no persona is asked
    for or read (docs/adr/0185 §3). Before any secret is drawn the page asks
    for the account's own sign-in address, and only the address of the account
    the link was issued to opens it — any other, known or not, gets one answer,
    counted by the consent page's throttle. A link that is spent, expired,
    superseded or for an account that may no longer connect reads the same
    whoever opens it, so it says nothing about whose it is.
    """
    raw = request.GET.get("kood")
    if raw is not None:
        request.session[SESSION_ENROLMENT] = {"proof": credentials.link_proof(raw[:200])}
        return redirect("mcp_access:enrol")
    closed = _door_closed(request)
    if closed is not None:
        return closed

    stored = request.session.get(SESSION_ENROLMENT) or {}
    if not isinstance(stored, dict):
        stored = {}
    proof = str(stored.get("proof", "") or "")
    account = services.enrolment_account(proof) if proof else None
    if account is None:
        request.session.pop(SESSION_ENROLMENT, None)
        services.record(
            McpAccessEventType.ENROLMENT_REFUSED,
            succeeded=False,
            ip_address=client_ip(request),
            reason="link_not_usable",
        )
        return _enrol_page(request, {"invalid": True}, status=400)

    if stored.get("account") != str(account.pk):
        return _enrol_address_step(request, proof)

    label = services.account_label(account)
    enrolment = credentials.resume_enrolment(stored.get("sealed"), label)
    if enrolment is None:
        enrolment = credentials.new_enrolment(label)
        request.session[SESSION_ENROLMENT] = {
            "proof": proof,
            "account": str(account.pk),
            "sealed": enrolment.sealed,
        }

    if request.method == "GET":
        return _enrol_page(request, {"enrolment": enrolment, "account": account})

    confirmed = services.confirm_enrolment(
        proof=proof,
        user=account,
        sealed=enrolment.sealed,
        code=request.POST.get("kood", ""),
        ip_address=client_ip(request),
    )
    if not confirmed:
        return _enrol_page(
            request,
            {
                "enrolment": enrolment,
                "account": account,
                "error": "Kood ei sobi. Sisesta autentija praegune kuuekohaline kood.",
            },
            status=400,
        )
    request.session.pop(SESSION_ENROLMENT, None)
    return _enrol_page(request, {"done": True})


def _enrol_address_step(request: HttpRequest, proof: str) -> HttpResponse:
    """«Sinu konto e-posti aadress»: the step before the secret is drawn.

    A match is remembered in the session beside the link's proof — the
    account's id, never the address — and the browser goes back to the bare
    page, which then shows the authenticator.
    """
    if request.method == "GET":
        return _enrol_page(request, {"ask_address": True})
    ip = client_ip(request)
    address = _typed_address(request)
    outcome = services.check_enrolment_address(
        proof=proof, address=address, ip_address=ip, throttle=throttle_keys(request, ip)
    )
    if outcome.status == "throttled":
        return _enrol_page(
            request,
            {"ask_address": True, "address": address, "error": _too_many_attempts(outcome.wait)},
            status=429,
        )
    if outcome.status != "ok" or outcome.user is None:
        return _enrol_page(
            request,
            {"ask_address": True, "address": address, "error": ENROLMENT_ADDRESS_REFUSAL},
            status=400,
        )
    request.session[SESSION_ENROLMENT] = {"proof": proof, "account": str(outcome.user.pk)}
    return redirect("mcp_access:enrol")
