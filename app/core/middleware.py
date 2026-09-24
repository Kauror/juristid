"""Response headers that depend on who the response is for.

One rule, and it is narrow: a page rendered for somebody who is signed in — or
behind the shared gate — must not be stored by anything.

`Vary: Cookie` already stops a shared cache from serving one person's page to
another, and Cloudflare marks these `DYNAMIC` and does not cache them. What
neither addresses is the browser's own history cache: after signing out, the
back button would re-display a page of member material from disk without asking
the server anything. `no-store` is the header that closes that, and it has to be
`no-store` rather than `no-cache` — the latter permits storing and only requires
revalidation.

Static files are deliberately untouched. They are the same bytes for everybody,
they are content-hashed, and making them uncacheable would cost every page load
for no privacy gain.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlencode, urlsplit

from django.conf import settings
from django.http import HttpRequest, HttpResponse
from django.shortcuts import resolve_url
from django.urls import NoReverseMatch, reverse
from django.utils.http import url_has_allowed_host_and_scheme

from app.accounts import shared_gate
from app.core.authorization import remember_grants_for_one_request

#: Paths whose responses are identical for everybody and safe to cache.
PUBLIC_PREFIXES = ("/static/", "/healthz", "/favicon.ico")

NO_STORE = "no-store, no-cache, must-revalidate, max-age=0"


class PrivateResponseMiddleware:
    """Mark anything rendered for an identified reader as unstorable."""

    def __init__(self, get_response: Any) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        response = self.get_response(request)
        if request.path.startswith(PUBLIC_PREFIXES):
            return response

        user = getattr(request, "user", None)
        identified = user is not None and user.is_authenticated
        if identified or shared_gate.has_passed(request):
            # setdefault, not assignment: a view that has thought about its own
            # caching — a download, a long-lived report — keeps its answer.
            response.setdefault("Cache-Control", NO_STORE)
            response.setdefault("Pragma", "no-cache")
        return response


class RequestScopeMiddleware:
    """Open the per-request authorization memo, and close it again.

    `scope_for_user` runs on every `visible_to`, and a page asks it over a
    hundred times about the same person. This is the boundary that lets the
    second and later asks reuse the first answer
    (`app.core.authorization.remember_grants_for_one_request`).

    **The `finally` is the whole point.** The memo lives in a `ContextVar`, and
    a worker thread serves one request after another: a dict that survived its
    request would answer an authorization question on behalf of somebody who
    never asked it. So this exists to guarantee the close, including when the
    view raises — which is why it wraps `get_response` rather than setting up in
    `process_request` and hoping for a matching `process_response`.

    Outermost of the application's own middleware, so the memo covers everything
    that can read business content, including the authenticator below it.
    """

    def __init__(self, get_response: Any) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        with remember_grants_for_one_request():
            return self.get_response(request)


#: The response headers an HTMX answer uses to say *why* it did not do what was
#: asked, where the status alone cannot. `static/js/app.js` reads them; nothing
#: else does, and they carry no business content — a word and an address on
#: this site.
FAILURE_HEADER = "X-Juristid-Failure"
SIGN_IN_HEADER = "X-Juristid-Sign-In"


def is_htmx(request: HttpRequest) -> bool:
    """Whether this request came from htmx rather than from a navigation."""
    return request.headers.get("HX-Request") == "true"


class HtmxSignInMiddleware:
    """Answer an HTMX request that needs signing in with a status, not a page.

    Every way this application sends somebody to sign in is a 302: the shared
    gate when it has aged out (`AuthenticationModeMiddleware._shared_gate`), and
    `login_required` to `LOGIN_URL` when there is a gate but no persona. For a
    navigation that is right. For an htmx request it is not: `XMLHttpRequest`
    follows a redirect by itself, so htmx received the *password page* with a
    200 and swapped it into whatever the save targeted — a Teema column
    replaced by a sign-in form, the Märge somebody had just typed gone with it
    (ENG-012).

    So the redirect is turned into a **401** carrying where to go. Nothing is
    swapped (`static/js/app.js` swaps only 2xx and the inline refusals); the
    page stays exactly as it was, typed text included, and says in Estonian
    that the session ended, with a link to sign in again. Deliberately not
    `HX-Redirect`: navigating away would throw away the very text this exists to
    keep, and the page is in a better position than the server to say whether
    there is any.

    **The gate is not loosened by one byte.** The decision to refuse was already
    taken below this middleware — the session has already been logged out and
    no view has run — and all this changes is the shape of the refusal. It
    recognises only a redirect *to a sign-in address*, so every other redirect
    an htmx request can receive passes through untouched.

    The address it offers returns to the page the person was on, not to the
    endpoint that refused: `login_required` writes the endpoint into `next`, and
    most endpoints here are POST-only, so signing in would have landed on a 405.
    `HX-Current-URL` is what htmx says the page is, and it is only used when it
    points back at this site — a `next` is exactly where an open redirect hides.
    The gate itself takes no `next` (it lands on the start page), and its
    address is offered as it is.
    """

    def __init__(self, get_response: Any) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        response = self.get_response(request)
        if not is_htmx(request) or response.status_code not in (301, 302, 303, 307, 308):
            return response
        location = response.get("Location", "")
        target = urlsplit(location)
        if target.netloc or target.path not in _sign_in_paths():
            return response
        sign_in = target.path
        if target.path != _gate_path():
            back = _current_page(request)
            if back:
                sign_in = f"{target.path}?{urlencode({'next': back})}"
        refusal = HttpResponse(status=401)
        refusal[FAILURE_HEADER] = "sign-in"
        refusal[SIGN_IN_HEADER] = sign_in
        # 401 is defined with a challenge. This one names no scheme a browser
        # would answer with a password dialog of its own.
        refusal["WWW-Authenticate"] = 'Session realm="juristid"'
        refusal["Cache-Control"] = NO_STORE
        return refusal


def _gate_path() -> str:
    try:
        return reverse("accounts:shared_gate")
    except NoReverseMatch:
        return ""


def _sign_in_paths() -> set[str]:
    """Every address this deployment sends somebody to sign in at."""
    paths = {urlsplit(resolve_url(settings.LOGIN_URL)).path, _gate_path()}
    return {path for path in paths if path}


def _current_page(request: HttpRequest) -> str:
    """The page the htmx request was made from, as a same-site path, or nothing."""
    current = request.headers.get("HX-Current-URL", "")
    if not current or not url_has_allowed_host_and_scheme(
        current, allowed_hosts={request.get_host()}, require_https=request.is_secure()
    ):
        return ""
    parts = urlsplit(current)
    return parts.path + (f"?{parts.query}" if parts.query else "")
