"""View decorators for the pages that work before a persona is chosen.

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
"""

from __future__ import annotations

import functools
from collections.abc import Callable
from typing import Any

from django.conf import settings
from django.http import HttpRequest
from django.http.response import HttpResponseBase
from django.shortcuts import redirect

from app.accounts import shared_gate


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
