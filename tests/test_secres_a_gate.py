"""Security/resilience review 2026-10-29, workstream 1 (assessor A): the shared gate.

Each test names its finding id. A test that FAILS on current code demonstrates
the weakness; the docstring says what the smallest fix is. Tests marked
"sound" prove a boundary and pass.
"""

from __future__ import annotations

import pytest
from django.contrib.sessions.models import Session
from django.test import Client
from django.urls import reverse

from app.accounts.models import SharedGateThrottle
from tests import factories
from tests.gate import PASSWORD, apply_shared_gate

GATE = "/konto/varav/"


@pytest.fixture(autouse=True)
def shared_gate(settings):
    apply_shared_gate(settings)
    settings.TRUST_CF_CONNECTING_IP = True
    return settings


# ---------------------------------------------------------------------------
# A-01  The gate throttle keys on the full IPv6 address; the personal sign-in
#       throttle keys on the /64. Behind Cloudflare the client's real IPv6
#       address arrives in CF-Connecting-IP, and one subscriber holds a /64,
#       so the gate's lockout is bypassed by rotating the host part.
#       Fix: `shared_gate.client_key` should count `throttle._counting_unit(address)`
#       (the /64 for IPv6), the same unit `app.accounts.throttle.network_of` uses.
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_a01_gate_lockout_is_bypassed_by_rotating_inside_an_ipv6_64(client: Client):
    """A-01 (P3, CONFIRMED when this fails): six wrong passwords from six hosts of one /64."""
    statuses = []
    for host in range(1, 7):
        response = client.post(
            GATE, {"password": "vale"}, HTTP_CF_CONNECTING_IP=f"2001:db8:0:1::{host:x}"
        )
        statuses.append(response.status_code)
    # With SHARED_GATE_MAX_ATTEMPTS = 5 the sixth attempt from one client is
    # locked out (429). Six distinct throttle rows means nothing was counted
    # together.
    assert SharedGateThrottle.objects.count() == 1, (
        f"{SharedGateThrottle.objects.count()} throttle rows for one /64 — "
        "each host of the subscriber's prefix gets its own five guesses"
    )
    assert statuses[-1] == 429, statuses


@pytest.mark.django_db
def test_a01_reference_the_personal_sign_in_throttle_counts_the_64_as_one(client: Client, settings):
    """Reference (sound): `throttle.network_of` already does what A-01 asks of the gate."""
    from django.test import RequestFactory

    from app.accounts import throttle

    factory = RequestFactory()
    units = {
        throttle.network_of(factory.get("/", HTTP_CF_CONNECTING_IP=f"2001:db8:0:1::{host:x}"))
        for host in range(1, 7)
    }
    assert units == {"2001:db8:0:1::/64"}


# ---------------------------------------------------------------------------
# A-02  Passing the gate rotates the session key but not the CSRF secret.
#       Django's `login()` rotates both for exactly this reason: a CSRF
#       cookie planted before authentication (cookie tossing from a sibling
#       host of the production domain, or a shared/kiosk browser) stays valid
#       for every POST the gate-session-without-persona can make
#       (`act_as`, the administrator step-up sign-in, `/konto/plokid/`).
#       Fix: call `django.middleware.csrf.rotate_token(request)` in
#       `shared_gate.open_gate` when the pass is fresh (carried is None).
# ---------------------------------------------------------------------------


def _csrf_cookie(client: Client) -> str:
    morsel = client.cookies.get("csrftoken")
    return morsel.value if morsel is not None else ""


@pytest.mark.django_db
def test_a02_passing_the_gate_does_not_rotate_the_csrf_secret():
    """A-02 (P3, CONFIRMED when this fails): the csrftoken cookie survives the gate."""
    client = Client(enforce_csrf_checks=True)
    page = client.get(GATE)
    assert page.status_code == 200
    before = _csrf_cookie(client)
    assert before, "the gate page sets a CSRF cookie"
    token = page.context["csrf_token"]
    response = client.post(GATE, {"password": PASSWORD, "csrfmiddlewaretoken": token})
    assert response.status_code == 302, response.status_code
    after = _csrf_cookie(client)
    assert after and after != before, "the CSRF secret was not rotated when the gate opened"


@pytest.mark.django_db
def test_a02_reference_choosing_a_persona_does_rotate_the_csrf_secret():
    """Reference (sound): `login()` at persona choice rotates the CSRF secret."""
    person = factories.UserFactory()
    client = Client(enforce_csrf_checks=True)
    page = client.get(GATE)
    client.post(GATE, {"password": PASSWORD, "csrfmiddlewaretoken": page.context["csrf_token"]})
    chooser = client.get(reverse("accounts:choose_persona"))
    before = _csrf_cookie(client)
    response = client.post(
        reverse("accounts:act_as"),
        {"user_id": str(person.pk), "csrfmiddlewaretoken": chooser.context["csrf_token"]},
    )
    assert response.status_code == 302
    assert _csrf_cookie(client) != before


@pytest.mark.django_db
def test_sound_the_session_key_changes_at_the_gate_and_again_at_the_persona(client: Client):
    """Sound: fixation across both steps is closed (gate: cycle_key; persona: login)."""
    person = factories.UserFactory()
    client.get(GATE)
    client.cookies["sessionid"] = "planted-before-the-gate"
    client.post(GATE, {"password": PASSWORD})
    after_gate = client.cookies["sessionid"].value
    assert after_gate != "planted-before-the-gate"
    client.post(reverse("accounts:act_as"), {"user_id": str(person.pk)})
    assert client.cookies["sessionid"].value != after_gate


# ---------------------------------------------------------------------------
# A-03  An anonymous visitor creates a server-side session row without passing
#       the gate: a GET to a one-time-link page with any `?kood=` value makes
#       the middleware keep a "proof" — even for a malformed value whose proof
#       is "" — which saves the session. Nothing prunes `django_session`
#       (no `clearsessions` is scheduled in deploy/), so this is an
#       unauthenticated, unthrottled table-growth primitive.
#       Fix: in `local_views.keep_link_proof`, do not touch the session when
#       `tokens.proof_of(raw)` is ""; and schedule `manage.py clearsessions`.
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_a03_a_malformed_link_before_the_gate_creates_no_session_row(client: Client):
    """A-03 (P3, CONFIRMED when this fails): one anonymous GET, one new session row."""
    before = Session.objects.count()
    for n in range(5):
        fresh = Client()
        response = fresh.get(reverse("accounts:activate"), {"kood": f"not-a-link-{n}"})
        assert response.status_code == 302
        assert response["Location"].startswith(GATE)
    assert Session.objects.count() == before, (
        f"{Session.objects.count() - before} session rows written by anonymous requests "
        "carrying a value that can never be a link"
    )


@pytest.mark.django_db
def test_sound_an_anonymous_visitor_at_an_ordinary_page_creates_no_session_row(client: Client):
    """Sound: the gate redirect itself writes nothing."""
    before = Session.objects.count()
    for path in ("/", "/teemad/", "/admin/", "/haldus/", GATE):
        Client().get(path)
    assert Session.objects.count() == before
