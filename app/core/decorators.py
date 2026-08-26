"""View decorators for the HTTP authorization boundary.

Almost every view in this application is `@login_required`, and should stay
that way: authoring anything needs somebody to attribute it to. The exception is
the department landing page, which in shared-gate mode has to be useful to a
reader who has passed the door and not yet said whose work they are looking at
(Stage-2D auth brief 4, 6).

`@gate_required` is that exception, spelled out. It admits a request that is
either signed in *or* behind the shared gate, and it hands the view a viewer
rather than letting it reach for `request.user` — because with no persona
selected `request.user` is anonymous, and a page that quietly rendered "as
nobody" would show nothing rather than showing the department.

`@business_write_required` is the other half: the one place a mutating route
says "and this person may author business content". It holds no rule of its own
— the rule lives in `app.core.authorization.may_write_business_content` and
stays there — it is the HTTP boundary that applies it.
"""

from __future__ import annotations

import functools
from collections.abc import Callable
from typing import Any

from django.conf import settings
from django.http import Http404, HttpRequest
from django.http.response import HttpResponseBase
from django.shortcuts import redirect

from app.accounts import shared_gate
from app.core.authorization import may_write_business_content


def gate_required[R: HttpResponseBase](
    view: Callable[..., R],
) -> Callable[..., R | HttpResponseBase]:
    """Allow a signed-in user, or anybody past the shared gate.

    In every other mode this is exactly `login_required`: `none` has no gate to
    be behind, and `cloudflare_access` signs people in before a view ever runs,
    so a request that reaches here unauthenticated in those modes is one that
    should be bounced.
    """

    @functools.wraps(view)
    def wrapper(request: HttpRequest, *args: Any, **kwargs: Any) -> R | HttpResponseBase:
        user = getattr(request, "user", None)
        if user is not None and user.is_authenticated:
            return view(request, *args, **kwargs)
        if shared_gate.has_passed(request):
            return view(request, *args, **kwargs)
        return redirect(settings.LOGIN_URL)

    return wrapper


def viewer_for(request: HttpRequest) -> Any:
    """Who to authorize this request as: the persona, or the department."""
    return shared_gate.viewer(request)


#: What a refused business write says. Deliberately one sentence for every
#: route: the text a reader sees must not vary by endpoint, or the differences
#: become a map of what the application can do.
WRITE_REFUSED = "Selle toimingu jaoks on vaja sisu muutmise õigust."


def business_write_required[R: HttpResponseBase](
    view: Callable[..., R],
) -> Callable[..., R | HttpResponseBase]:
    """Refuse a request from somebody who may not author business content.

    **404, not 403**, matching the nine refusals already written by hand in
    `app.matters.views` and for the reason stated there: a reader who may not
    write is not told which surfaces exist for those who may. A 403 answers "you
    could do this with another role", which is a description of the application
    handed to exactly the person who should not have it.

    **Before anything else the view does.** Not after the object is fetched, not
    after the upload is parsed, not after the form validates — an unauthorized
    caller must not be able to spend the server's time, and must never reach a
    partially-applied write. This is why it is a decorator rather than a line
    inside each view body (§33).

    **It answers one question.** Whether this *actor* may write at all. Whether
    they may see the object is still `visible_to`, and whether a target may
    receive work is still `app.accounts.selectors`; both still run, and neither
    is replaced by this (§4, §14).

    Compose it *inside* `@login_required` so an anonymous request is still sent
    to sign in rather than told the route does not exist, and *outside*
    `@require_http_methods` so a non-writer gets the same 404 whatever verb they
    try — otherwise a 405 confirms the endpoint for the one caller who should
    learn nothing from it.
    """

    @functools.wraps(view)
    def wrapper(request: HttpRequest, *args: Any, **kwargs: Any) -> R | HttpResponseBase:
        if not may_write_business_content(getattr(request, "user", None)):
            raise Http404(WRITE_REFUSED)
        return view(request, *args, **kwargs)

    return wrapper
