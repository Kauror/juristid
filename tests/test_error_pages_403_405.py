"""A refused permission and a wrong method get the Estonian error page (JUX-013).

ENG-012 replaced Django's English fallbacks with `templates/error_page.html`
for 400, 404, 500 and a refused CSRF check. Two answers were left out, and the
2026-10-17 audit (V4, `DEBUG=0`) found them:

* **403** from `PermissionDenied` — every `require_*` guard, the archive queue
  for a colleague who is not its operator — was Django's English
  `<h1>403 Forbidden</h1>`, `lang="en"`, with no way home;
* **405** from `require_http_methods` — a GET on a POST-only address, which is
  what a stale bookmark or a re-opened tab produces — was an empty body.

Both now render the same family of page: `lang="et"`, one heading, a link to the
start page. The status codes, the `Allow` header and who is refused are
unchanged; the message given to `PermissionDenied` is not shown, as Django's
own page never showed it either.
"""

from __future__ import annotations

import re

import pytest
from django.urls import reverse

from tests import factories

pytestmark = pytest.mark.django_db


def _assert_estonian_error_page(body: str) -> None:
    assert '<html lang="et"' in body
    assert len(re.findall(r"<h1\b", body)) == 1
    assert '<a href="/">Avalehele</a>' in body
    assert "Forbidden" not in body


def test_a_refused_permission_is_the_estonian_403_page(client, specialist, settings):
    settings.DEBUG = False
    client.force_login(specialist)

    response = client.get("/haldus/arvamuste-ulevaatus/")

    assert response.status_code == 403
    body = response.content.decode()
    _assert_estonian_error_page(body)
    assert "Sul pole sellele lehele ligipääsu" in body
    # The guard's own sentence stays server-side, as with Django's page.
    assert "halduri töövahend" not in body


def test_a_wrong_method_is_the_estonian_405_page(client, specialist, settings):
    settings.DEBUG = False
    client.force_login(specialist)
    matter = factories.MatterFactory()

    response = client.get(reverse("matters:add_note", kwargs={"pk": matter.pk}))

    assert response.status_code == 405
    assert response["Allow"] == "POST"
    assert response["Content-Type"].startswith("text/html")
    body = response.content.decode()
    _assert_estonian_error_page(body)
    assert "Seda aadressi ei saa nii avada" in body


def test_a_405_with_a_body_of_its_own_is_left_alone(rf):
    from django.http import HttpResponseNotAllowed

    from app.core.middleware import MethodNotAllowedPageMiddleware

    own = HttpResponseNotAllowed(["POST"], content=b"oma vastus")
    middleware = MethodNotAllowedPageMiddleware(lambda request: own)

    response = middleware(rf.get("/"))

    assert response.content == b"oma vastus"
    assert response["Allow"] == "POST"
