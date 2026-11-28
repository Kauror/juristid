"""Nobody makes this server parse a multipart body before they are signed in (SEC-013).

Django parses a ``multipart/form-data`` body the first time anything touches
``request.POST`` or ``request.FILES``: it streams every part through the upload
handlers, writing files to temporary storage. Before this boundary, the first
toucher was `UploadLimitMiddleware` — deliberately early, so the upload policy
answers before any view (docs/adr/0182) — and after it `RefuseNulMiddleware` and
`UploadTitlesMiddleware`, all of them *before* `AuthenticationModeMiddleware`
decided whether the request may go anywhere at all. So anybody who could reach
the tunnel could make a web worker receive, parse and spool up to
``UPLOAD_MAX_REQUEST_BYTES`` (512 MiB) of files, once per request, with no
session; the gate refused them only afterwards (docs/adr/0233).

`MultipartAuthBoundaryMiddleware` sits before every middleware that reads the
body and decides from the **session and the headers alone** — never the body —
whether a multipart request may be parsed:

* on the sign-in surfaces (`PUBLIC_AUTH_ROUTES`: the gate, persona, sign-in,
  one-time links, the admin's sign-in, the MCP OAuth endpoints), which must answer
  before anybody is signed in and none of which takes a file, only a small one:
  a declared ``Content-Length`` of at most `PUBLIC_FORM_MAX_BYTES` (64 KiB, a
  password form is a few hundred bytes). A larger or undeclared (chunked) body
  is refused with 413 before it is read;
* elsewhere only for a request whose session already carries a signed-in person
  who passes the deployment's own test, read the way
  `AuthenticationModeMiddleware` reads it: behind the shared gate, a gate still
  open (`shared_gate.has_passed`); under ``local_password``, a session signed in
  here and inside its limits; under ``cloudflare_access``, a verified assertion
  naming that same person. Anything else is refused before a byte of the body is
  read: htmx gets the 401 its failure line already tells as «session ended»
  (`HtmxSignInMiddleware`'s shape), a navigation a 403 page.

The unread body is never drained: gunicorn's sync worker closes every connection
after its answer (``resp.force_close()``; a WSGI application may not set the
hop-by-hop ``Connection`` header itself). Nothing is written, so no temporary
file exists to clean up. A signed-in request goes on
exactly as before: the upload policy, CSRF, the NUL check, the titles and the
mode's full checks all still run, in their order.

Only multipart: a ``application/x-www-form-urlencoded`` body is read whole into
memory but is capped by ``DATA_UPLOAD_MAX_MEMORY_SIZE`` (10 MiB) before it is read,
and the sign-in forms need it.
"""

from __future__ import annotations

import functools
import logging
from typing import Any
from urllib.parse import urlencode

from django.contrib.auth.middleware import get_user  # caches on the request (_cached_user)
from django.http import HttpRequest, HttpResponse, HttpResponseRedirect
from django.template.loader import render_to_string
from django.urls import NoReverseMatch, reverse

from app.accounts.enums import AuthMode
from app.core.middleware import FAILURE_HEADER, NO_STORE, SIGN_IN_HEADER, is_htmx

logger = logging.getLogger(__name__)

#: The surfaces that answer before anybody is signed in, or that sign somebody
#: in or out. None of them takes a file; a multipart body there is refused for all.
PUBLIC_AUTH_ROUTES = (
    "accounts:dev_login",
    "accounts:shared_gate",
    "accounts:choose_persona",
    "accounts:act_as",
    "accounts:sign_out",
    "accounts:sign_in",
    "accounts:sign_in_second_factor",
    "accounts:activate",
    "accounts:forgot_password",
    "accounts:reset_password",
    "accounts:reauthenticate",
    "admin:login",
    # The administrator's own sign-in under the shared gate (docs/adr/0185): a
    # gate-passed visitor with no persona reaches these (r96 integration).
    "haldus:sign_in",
    "haldus:sign_in_code",
    "haldus:enrol",
    "haldus:reauthenticate",
    "haldus:sign_out",
    # OAuth discovery for MCP clients: answered before anybody signs in (r96
    # integration; a small form there is read and refused by the route itself).
    "mcp_access:authorization_server_metadata",
    "mcp_access:authorize",
    "mcp_access:token",
    "mcp_access:revoke",
    "mcp_access:enrol",
)
BODY_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
#: The most a multipart body on a sign-in surface may declare. Browsers send these
#: forms urlencoded; a client that sends them as multipart (Django's test client,
#: a script) sends a few hundred bytes.
PUBLIC_FORM_MAX_BYTES = 64 * 1024
MULTIPART = "multipart/form-data"
#: The failure word `static/js/app.js` already tells as «sign in again».
SIGN_IN_FAILURE = "sign-in"


@functools.cache
def public_auth_paths() -> frozenset[str]:
    found = set()
    for name in PUBLIC_AUTH_ROUTES:
        try:
            found.add(reverse(name))
        except NoReverseMatch:  # a route this configuration does not mount
            continue
    return frozenset(found)


def is_multipart(request: HttpRequest) -> bool:
    """From the header alone: Django parses a body as multipart exactly then."""
    return request.method in BODY_METHODS and (request.content_type or "").lower() == MULTIPART


def signed_in_for_files(request: HttpRequest) -> bool:
    """Whether this request's session already carries a signed-in person who passes
    the mode's own test. Reads the session and the headers, never the body."""
    from app.accounts import admin_session, cloudflare_access, local_auth, shared_gate

    user = get_user(request)  # cached on the request: AuthenticationMiddleware reuses it
    mode = shared_gate.current_mode()
    if user is None or not user.is_authenticated or not getattr(user, "is_active", False):
        # The administrator's own verified session behind the gate, with no
        # persona (docs/adr/0181, 0185): signed in by its own password and second
        # factor, from session state alone (r96 integration). Asked only when
        # there is no persona, so a lawyer's upload pays nothing for it.
        return (
            mode == AuthMode.SHARED_GATE
            and shared_gate.has_passed(request)
            and admin_session.verified_administrator(request) is not None
        )
    request.user = user  # what the mode's helpers read; AuthenticationMiddleware sets it again
    if mode == AuthMode.SHARED_GATE:
        return shared_gate.has_passed(request)
    if mode == AuthMode.LOCAL_PASSWORD:
        return local_auth.is_local_session(request) and not local_auth.expiry_reason(request)
    if mode == AuthMode.CLOUDFLARE_ACCESS:
        try:
            email, claims = cloudflare_access.identity_from_request(request.META)
        except cloudflare_access.AccessDenied:
            return False
        # The same test the mode's fast path applies (SEC-012, docs/adr/0232): a
        # session Access itself established for this identity, still inside its
        # assertion, for an active, non-synthetic account. A session from the
        # shared gate, `none` or `local_password` whose account merely carries the
        # same address is not one — its multipart body waits until Access signs
        # the person in afresh (r96 integration).
        return cloudflare_access.session_holds(request.session, user, email=email, claims=claims)
    return mode == AuthMode.NONE


class MultipartAuthBoundaryMiddleware:
    """Refuse a multipart body nobody signed in sent, before anything parses it."""

    def __init__(self, get_response: Any) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        if is_multipart(request):
            if request.path in public_auth_paths():
                if not _small(request):
                    return _refuse(request, status=413, why="sign-in surface")
                return self.get_response(request)
            if not signed_in_for_files(request):
                return _refuse(request, status=401, why="no session")
        return self.get_response(request)


def _small(request: HttpRequest) -> bool:
    """A declared length within `PUBLIC_FORM_MAX_BYTES`; undeclared is not small."""
    try:
        declared = int(request.META.get("CONTENT_LENGTH") or "")
    except ValueError:
        return False
    return 0 <= declared <= PUBLIC_FORM_MAX_BYTES


def _refuse(request: HttpRequest, *, status: int, why: str) -> HttpResponse:
    # The declared size and the reason only: never a field, a filename or a query.
    logger.warning(
        "multipart body refused before parsing (%s): declared %s bytes",
        why,
        request.META.get("CONTENT_LENGTH") or "?",
    )
    if status == 413:
        response = HttpResponse(
            "Siia ei saa faile saata.", status=413, content_type="text/plain; charset=utf-8"
        )
    elif (sign_in := _sign_in_address(request)) and is_htmx(request):
        # The answer `HtmxSignInMiddleware` gives: nothing swapped, the page says
        # the session ended and offers the way back.
        response = HttpResponse(status=401)
        response[FAILURE_HEADER] = SIGN_IN_FAILURE
        response[SIGN_IN_HEADER] = sign_in
        response["WWW-Authenticate"] = 'Session realm="juristid"'
    elif sign_in:
        # What the mode middleware or `login_required` would have answered.
        response = HttpResponseRedirect(sign_in.split("?", 1)[0])
    else:
        response = HttpResponse(
            render_to_string("403.html", {}), status=403, content_type="text/html; charset=utf-8"
        )
    response["Cache-Control"] = NO_STORE
    return response


def _sign_in_address(request: HttpRequest) -> str:
    """Where this deployment sends somebody to sign in, as `HtmxSignInMiddleware`
    offers it: the gate when it is closed, else the sign-in page back to the page
    the request came from. "" under Cloudflare Access (no sign-in page here)."""
    from django.conf import settings
    from django.shortcuts import resolve_url

    from app.accounts import shared_gate
    from app.core.middleware import _current_page

    mode = shared_gate.current_mode()
    if mode == AuthMode.CLOUDFLARE_ACCESS:
        return ""
    if mode == AuthMode.SHARED_GATE and not shared_gate.has_passed(request):
        return reverse("accounts:shared_gate")
    sign_in = str(resolve_url(settings.LOGIN_URL))
    back = _current_page(request) if is_htmx(request) else ""
    return f"{sign_in}?{urlencode({'next': back})}" if back else sign_in
