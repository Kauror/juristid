"""View decorators for the HTTP authorization boundary.

Every view that reads business content is `@login_required`: a session that
is behind the shared gate but has chosen no persona is not an authorized
internal user, and `LOGIN_URL` sends it to the persona chooser
(docs/adr/0181). There used to be an exception, `@gate_required`, which let
Osakond, Statistika, Arvamused and the document routes render for such a
session as a department scope of NORMAL records. One internal read policy
retired it: the shared password proves somebody is behind the door, never that
an authorized person is reading. `tests/test_business_routes_need_a_reader.py`
walks every route so that no reading surface can reappear without one.

`viewer_for` stays, and after `login_required` it is simply the persona.

`@business_write_required` is the other half: the one place a mutating route
says "and this person may author business content". It holds no rule of its own
— the rule lives in `app.core.authorization.may_write_business_content` and
stays there — it is the HTTP boundary that applies it.
"""

from __future__ import annotations

import functools
from collections.abc import Callable
from typing import Any

from django.http import Http404, HttpRequest
from django.http.response import HttpResponseBase

from app.accounts import shared_gate
from app.core.authorization import may_write_business_content


def viewer_for(request: HttpRequest) -> Any:
    """Who to authorize this request as: the persona.

    Behind `login_required` that is always `request.user`. Without a persona
    `shared_gate.viewer` answers the `DepartmentViewer` sentinel, which reads
    nothing since docs/adr/0181 — so a view that forgot its decorator would
    render empty rather than render the department.
    """
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
