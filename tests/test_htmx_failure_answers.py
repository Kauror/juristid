"""The server's half of a failure the page can tell (ENG-012).

Three answers changed shape, and only shape:

* an htmx request that has to sign in gets a **401** saying where to go, instead
  of a redirect the browser followed into the fragment target;
* an htmx request refused by CSRF gets a **403 marked as such**, and a
  navigation gets an Estonian page, instead of Django's English one;
* 404 and 500 are Estonian, and the 404 says the same thing whatever the reason.

What must not change is who gets through, and most of this module is that: the
gate still logs the aged-out session out, `next` still only points back at this
site, CSRF still refuses, and a 404 still cannot tell «not there» from «not
yours». The browser's half is `e2e/test_workspace_failures_and_drafts.py`.
"""

from __future__ import annotations

from datetime import timedelta
from urllib.parse import quote

import pytest
from django.template.loader import render_to_string
from django.test import Client, RequestFactory
from django.urls import reverse
from django.utils import timezone
from django.views.defaults import server_error

from app.accounts import shared_gate
from app.core.enums import Visibility
from tests import factories
from tests.gate import PASSWORD, apply_shared_gate

pytestmark = pytest.mark.django_db

HX = {"HTTP_HX_REQUEST": "true"}


def _hx_from(page: str) -> dict[str, str]:
    return {**HX, "HTTP_HX_CURRENT_URL": f"http://testserver{page}"}


def _marge(matter) -> str:
    return reverse("matters:add_note", kwargs={"pk": matter.pk})


# -- signing in ------------------------------------------------------------------


@pytest.fixture
def gate(settings):
    return apply_shared_gate(settings)


@pytest.fixture
def aged_out(gate, client):
    """A persona session whose gate passed three days ago."""
    client.post(reverse("accounts:shared_gate"), {"password": PASSWORD})
    session = client.session
    session[shared_gate.GATE_PASSED_AT] = (timezone.now() - timedelta(days=3)).isoformat()
    session.save()
    return client


def test_an_aged_out_gate_answers_htmx_with_a_401_not_the_password_page(aged_out):
    matter = factories.MatterFactory()
    page = reverse("matters:matter_detail", kwargs={"pk": matter.pk})

    response = aged_out.post(_marge(matter), {"marge-title": "x"}, **_hx_from(page))

    assert response.status_code == 401
    assert response["X-Juristid-Failure"] == "sign-in"
    # The gate takes no `next`: it lands on the start page, so it is offered as it is.
    assert response["X-Juristid-Sign-In"] == reverse("accounts:shared_gate")
    assert response.content == b""
    assert "no-store" in response["Cache-Control"]


def test_the_gate_is_exactly_as_shut_after_the_401(aged_out):
    """Only the refusal's shape changed: the session was logged out regardless."""
    matter = factories.MatterFactory()

    aged_out.post(_marge(matter), {}, **HX)

    assert "_auth_user_id" not in aged_out.session
    response = aged_out.get("/osakond/")
    assert response.status_code == 302
    assert response["Location"] == reverse("accounts:shared_gate")


def test_a_navigation_to_an_aged_out_gate_is_still_redirected(aged_out):
    response = aged_out.get("/osakond/")

    assert response.status_code == 302
    assert response["Location"] == reverse("accounts:shared_gate")


def test_a_missing_persona_is_offered_sign_in_back_to_the_page_not_the_endpoint(gate, client):
    """`login_required` names the POST-only endpoint as `next`; that would land on a 405."""
    client.post(reverse("accounts:shared_gate"), {"password": PASSWORD})
    matter = factories.MatterFactory()
    page = reverse("matters:matter_detail", kwargs={"pk": matter.pk})

    response = client.post(_marge(matter), {}, **_hx_from(page))

    assert response.status_code == 401
    assert response["X-Juristid-Sign-In"] == (
        f"{reverse('accounts:choose_persona')}?next={quote(page, safe='')}"
    )


def test_a_foreign_current_url_is_not_offered_as_next(gate, client):
    client.post(reverse("accounts:shared_gate"), {"password": PASSWORD})
    matter = factories.MatterFactory()

    response = client.post(
        _marge(matter), {}, **HX, HTTP_HX_CURRENT_URL="https://elsewhere.example/phish/"
    )

    assert response.status_code == 401
    assert response["X-Juristid-Sign-In"] == reverse("accounts:choose_persona")


def test_a_redirect_that_is_not_to_sign_in_passes_through(client, specialist):
    """The start page redirects a signed-in reader onwards; htmx or not, that stands."""
    client.force_login(specialist)

    response = client.get("/", **HX)

    assert response.status_code == 302
    assert response["Location"] != reverse("accounts:dev_login")


def test_a_signed_out_htmx_request_under_the_default_mode_is_a_401_too(client):
    matter = factories.MatterFactory()
    page = reverse("matters:matter_detail", kwargs={"pk": matter.pk})

    response = client.post(_marge(matter), {}, **_hx_from(page))

    assert response.status_code == 401
    assert response["X-Juristid-Sign-In"] == (
        f"{reverse('accounts:dev_login')}?next={quote(page, safe='')}"
    )


def test_a_signed_out_navigation_is_still_redirected_to_sign_in(client):
    matter = factories.MatterFactory()
    page = reverse("matters:matter_detail", kwargs={"pk": matter.pk})

    response = client.get(page)

    assert response.status_code == 302
    assert response["Location"].startswith(reverse("accounts:dev_login"))


# -- CSRF --------------------------------------------------------------------


@pytest.fixture
def strict(specialist):
    client = Client(enforce_csrf_checks=True)
    client.force_login(specialist)
    return client


def test_a_csrf_refusal_is_a_marked_403_for_htmx(strict, specialist):
    matter = factories.MatterFactory(owner=specialist)

    response = strict.post(_marge(matter), {"marge-title": "x"}, **HX)

    assert response.status_code == 403
    assert response["X-Juristid-Failure"] == "csrf"
    assert response.content == b""


def test_a_csrf_refusal_is_an_estonian_page_for_a_navigation(strict, specialist):
    matter = factories.MatterFactory(owner=specialist)

    response = strict.post(_marge(matter), {"marge-title": "x"})

    body = response.content.decode()
    assert response.status_code == 403
    assert '<html lang="et"' in body
    assert "Leht on aegunud" in body
    assert "CSRF verification failed" not in body
    assert "X-Juristid-Failure" not in response


def test_a_csrf_refusal_still_writes_nothing(strict, specialist):
    from app.matters.models import MatterProceduralDevelopment

    matter = factories.MatterFactory(owner=specialist)

    strict.post(_marge(matter), {"marge-title": "Ei tohi salvestuda"}, **HX)

    assert not MatterProceduralDevelopment.objects.filter(matter=matter).exists()


# -- 404 and 500 ----------------------------------------------------------------


def test_the_404_is_estonian_and_the_same_for_absent_and_invisible(client, reader):
    client.force_login(reader)
    hidden = factories.MatterFactory(visibility=Visibility.RESTRICTED)
    absent = "01a0d351-0000-7000-8000-000000000000"

    invisible = client.get(reverse("matters:matter_detail", kwargs={"pk": hidden.pk}))
    missing = client.get(reverse("matters:matter_detail", kwargs={"pk": absent}))

    assert invisible.status_code == missing.status_code == 404
    assert invisible.content == missing.content
    body = missing.content.decode()
    assert '<html lang="et"' in body
    assert "Lehte ei leitud" in body
    assert absent not in body


def test_the_500_is_estonian_and_needs_no_context():
    body = render_to_string("500.html")
    assert '<html lang="et"' in body
    assert "Serveris tekkis viga" in body

    response = server_error(RequestFactory().get("/"))
    assert response.status_code == 500
    assert "Serveris tekkis viga" in response.content.decode()
