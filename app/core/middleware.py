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

from django.http import HttpRequest, HttpResponse

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
