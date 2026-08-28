"""The shared department gate, and the persona picked behind it.

Two things are being tested, and the distinction between them is the point of
the whole mode:

**The gate is authentication.** A long password, hashed, constant-time compared,
rate limited with an escalating per-client lockout. An unauthenticated visitor
sees a password form and no data of any kind.

**The persona is not.** Selecting "Marko" changes which work the application
shows. It is not evidence that Marko is at the keyboard, and every audit row
this mode writes says so. A test suite that only checked the first half would
be endorsing the lie the second half exists to prevent (docs/adr/0016).

The cases below are the twelve checks the deployment brief requires before this
is called live, plus the ones that only a unit test can reach.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.urls import reverse
from django.utils import timezone

from app.accounts import shared_gate
from app.accounts.enums import AuthMode
from app.accounts.models import SharedGateThrottle
from app.audit.enums import SecurityEventType
from app.audit.models import SecurityAuditEvent
from app.core.authorization import (
    DEPARTMENT_VIEWER,
    department_scope,
    matter_visibility_q,
    restricted_participation_q,
    scope_for_user,
)
from app.core.enums import Visibility
from app.matters.models import Matter
from tests import factories

PASSWORD = "seda-parooli-ei-ole-kusagil-mujal"  # noqa: S105

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def gate_mode(settings):
    settings.AUTH_MODE = AuthMode.SHARED_GATE
    settings.SHARED_GATE_PASSWORD = PASSWORD
    settings.SHARED_GATE_MAX_ATTEMPTS = 5
    settings.SHARED_GATE_LOCKOUT_SECONDS = 300
    settings.SHARED_GATE_MAX_LOCKOUT_SECONDS = 3600
    settings.DEV_LOGIN_ENABLED = False
    settings.LOGIN_URL = "accounts:choose_persona"
    return settings


@pytest.fixture
def gate_url():
    return reverse("accounts:shared_gate")


@pytest.fixture
def behind_the_gate(client, gate_url):
    """A client that has typed the password correctly."""
    response = client.post(gate_url, {"password": PASSWORD})
    assert response.status_code == 302
    return client


# -- A. an unauthenticated visitor sees nothing ----------------------------


@pytest.mark.parametrize(
    "path",
    ["/", "/ulevaade/", "/minu-too/", "/teemad/", "/saabunud/", "/otsing/?q=eeln%C3%B5u"],
)
def test_nothing_is_reachable_before_the_password(client, path, gate_url):
    response = client.get(path)
    assert response.status_code == 302
    assert response["Location"] == gate_url


def test_the_gate_page_leaks_no_data(client, gate_url):
    """Not the user list, not a Matter title, not who works here."""
    owner = factories.UserFactory(display_name="Marko Näidisjurist")
    factories.MatterFactory(owner=owner, title="Pakendiseaduse muutmise eelnõu")

    body = client.get(gate_url).content.decode()
    assert "Marko Näidisjurist" not in body
    assert "Pakendiseaduse" not in body
    assert "Vali kasutaja" not in body


def test_the_health_check_answers_without_the_password(client):
    """The container runtime is not a person and cannot type a password."""
    assert client.get("/healthz").status_code == 200


# -- B, C. wrong passwords, and what repetition costs ----------------------


def test_a_wrong_password_is_rejected(client, gate_url):
    response = client.post(gate_url, {"password": "vale-parool-mis-ei-tööta"})
    assert response.status_code == 400
    assert not shared_gate.has_passed(response.wsgi_request)


def test_repeated_failures_are_locked_out(client, gate_url, gate_mode):
    for _ in range(gate_mode.SHARED_GATE_MAX_ATTEMPTS):
        client.post(gate_url, {"password": "vale"})

    # Even the correct password now waits.
    response = client.post(gate_url, {"password": PASSWORD})
    assert response.status_code == 429
    assert not shared_gate.has_passed(response.wsgi_request)


def test_the_refusal_never_says_which_refusal_it_was(client, gate_url, gate_mode):
    """Two different facts, and a prober should not be able to tell them apart.

    "Wrong password" tells somebody the account exists and the guess was wrong.
    "Locked out" tells them the throttle exists and roughly where its edge is.
    The locked page in particular must never confirm that the password it just
    refused was the correct one (Stage-2D auth brief 9).
    """
    wrong = client.post(gate_url, {"password": "vale"}).content.decode().lower()
    for _ in range(gate_mode.SHARED_GATE_MAX_ATTEMPTS):
        client.post(gate_url, {"password": "vale"})
    locked = client.post(gate_url, {"password": PASSWORD}).content.decode().lower()

    assert "vale parool" in wrong
    # The correct password, refused, and the page says only "try again later".
    assert "vale parool" not in locked
    assert "õige" not in locked
    assert "liiga palju katseid" in locked


def test_each_lockout_cycle_is_longer_than_the_last(gate_mode):
    record = SharedGateThrottle.objects.create(client_key="a" * 64)
    waits = []
    for _ in range(3):
        for _ in range(gate_mode.SHARED_GATE_MAX_ATTEMPTS - 1):
            record.register_failure(max_attempts=5, base_seconds=300, ceiling_seconds=3600)
        waits.append(
            record.register_failure(max_attempts=5, base_seconds=300, ceiling_seconds=3600)
        )

    assert waits == [300, 600, 1200]


def test_escalation_is_capped_so_nothing_becomes_permanent(gate_mode):
    record = SharedGateThrottle.objects.create(client_key="b" * 64, lockout_cycles=40)
    wait = record.register_failure(max_attempts=1, base_seconds=300, ceiling_seconds=3600)
    assert wait == 3600


def test_one_attacker_cannot_lock_out_the_department(client, gate_url, gate_mode):
    """The throttle is per client. A global counter would be a DoS primitive."""
    for _ in range(gate_mode.SHARED_GATE_MAX_ATTEMPTS + 3):
        client.post(gate_url, {"password": "vale"}, HTTP_CF_CONNECTING_IP="203.0.113.9")

    response = client.post(gate_url, {"password": PASSWORD}, HTTP_CF_CONNECTING_IP="198.51.100.4")
    assert response.status_code == 302


def test_a_correct_password_clears_the_failure_state(client, gate_url):
    for _ in range(3):
        client.post(gate_url, {"password": "vale"})
    client.post(gate_url, {"password": PASSWORD})
    assert not SharedGateThrottle.objects.exists()


def test_the_password_is_never_stored_in_the_clear(client, gate_url):
    client.post(gate_url, {"password": PASSWORD})
    for event in SecurityAuditEvent.objects.all():
        assert PASSWORD not in str(event.detail)
    for record in SharedGateThrottle.objects.all():
        assert PASSWORD not in record.client_key


def test_the_client_key_is_not_the_address(client, gate_url):
    client.post(gate_url, {"password": "vale"}, HTTP_CF_CONNECTING_IP="203.0.113.9")
    record = SharedGateThrottle.objects.get()
    assert "203.0.113.9" not in record.client_key
    assert len(record.client_key) == 64


# -- D, E. what the correct password opens ---------------------------------


def test_the_correct_password_opens_the_department_overview(behind_the_gate):
    response = behind_the_gate.get("/ulevaade/", follow=True)
    assert response.status_code == 200
    assert response.resolver_match.view_name == "matters:overview"


def test_the_dashboard_works_with_no_persona_selected(behind_the_gate):
    owner = factories.UserFactory()
    factories.MatterFactory(owner=owner, title="Avalik teema kõigile", is_open=True)

    response = behind_the_gate.get("/ulevaade/")
    assert response.status_code == 200
    assert not response.wsgi_request.user.is_authenticated
    assert "Avalik teema kõigile" in response.content.decode()


def test_the_session_identifier_changes_when_the_gate_opens(client, gate_url):
    client.get(gate_url)
    before = client.cookies.get("sessionid")
    client.post(gate_url, {"password": PASSWORD})
    after = client.cookies.get("sessionid")
    assert before is None or before.value != after.value


def test_an_aged_out_gate_asks_for_the_password_again(behind_the_gate, gate_url):
    session = behind_the_gate.session
    session[shared_gate.GATE_PASSED_AT] = (timezone.now() - timedelta(days=3)).isoformat()
    session.save()

    response = behind_the_gate.get("/ulevaade/")
    assert response.status_code == 302
    assert response["Location"] == gate_url


# -- F, G, H. personas -----------------------------------------------------


def test_selecting_a_persona_changes_whose_work_is_shown(behind_the_gate):
    marko = factories.UserFactory(display_name="Marko Näidisjurist")
    factories.MatterFactory(owner=marko, title="Marko oma teema", is_open=True)

    behind_the_gate.post(reverse("accounts:act_as"), {"user_id": str(marko.pk)})
    response = behind_the_gate.get("/minu-too/")
    assert response.status_code == 200
    assert response.wsgi_request.user.pk == marko.pk
    assert "Marko oma teema" in response.content.decode()


def test_changing_persona_changes_the_context(behind_the_gate):
    marko = factories.UserFactory(display_name="Marko Näidisjurist")
    ireen = factories.UserFactory(display_name="Ireen Näidisjurist")
    factories.MatterFactory(owner=marko, title="Marko oma teema", is_open=True)
    factories.MatterFactory(owner=ireen, title="Ireeni oma teema", is_open=True)

    behind_the_gate.post(reverse("accounts:act_as"), {"user_id": str(marko.pk)})
    first = behind_the_gate.get("/minu-too/").content.decode()

    behind_the_gate.post(reverse("accounts:act_as"), {"user_id": str(ireen.pk)})
    second = behind_the_gate.get("/minu-too/").content.decode()

    assert "Marko oma teema" in first and "Ireeni oma teema" not in first
    assert "Ireeni oma teema" in second and "Marko oma teema" not in second


def test_changing_persona_does_not_ask_for_the_password_again(behind_the_gate):
    marko = factories.UserFactory()
    ireen = factories.UserFactory()
    behind_the_gate.post(reverse("accounts:act_as"), {"user_id": str(marko.pk)})
    response = behind_the_gate.post(reverse("accounts:act_as"), {"user_id": str(ireen.pk)})

    assert response.status_code == 302
    assert shared_gate.has_passed(response.wsgi_request)


def test_every_persona_change_is_audited(behind_the_gate):
    marko = factories.UserFactory(display_name="Marko Näidisjurist")
    ireen = factories.UserFactory(display_name="Ireen Näidisjurist")
    behind_the_gate.post(reverse("accounts:act_as"), {"user_id": str(marko.pk)})
    behind_the_gate.post(reverse("accounts:act_as"), {"user_id": str(ireen.pk)})

    events = list(
        SecurityAuditEvent.objects.filter(event_type=SecurityEventType.PERSONA_SELECTED).order_by(
            "occurred_at"
        )
    )
    assert len(events) == 2
    assert events[1].detail["previous_persona"] == str(marko.pk)
    assert events[1].detail["chosen_persona"] == str(ireen.pk)


def test_the_audit_never_claims_an_individually_authenticated_identity(behind_the_gate):
    """The whole reason this mode is allowed to exist next to real data.

    "Marko selected" must not be recorded in a way that later reads as
    cryptographic proof that Marko was at the keyboard (Stage-2D auth brief 5).
    """
    marko = factories.UserFactory(display_name="Marko Näidisjurist")
    behind_the_gate.post(reverse("accounts:act_as"), {"user_id": str(marko.pk)})

    event = SecurityAuditEvent.objects.get(event_type=SecurityEventType.PERSONA_SELECTED)
    assert event.detail["authenticated_via"] == "SHARED_GATE"
    assert event.detail["chosen_persona"] == str(marko.pk)


def test_passing_the_gate_is_not_recorded_as_somebody_signing_in(behind_the_gate):
    assert SecurityAuditEvent.objects.filter(
        event_type=SecurityEventType.SHARED_GATE_PASSED
    ).exists()
    assert not SecurityAuditEvent.objects.filter(
        event_type=SecurityEventType.AUTHENTICATION_SUCCEEDED
    ).exists()


def test_stepping_back_to_the_department_drops_the_persona(behind_the_gate):
    marko = factories.UserFactory()
    behind_the_gate.post(reverse("accounts:act_as"), {"user_id": str(marko.pk)})
    response = behind_the_gate.post(reverse("accounts:act_as"), {"user_id": ""}, follow=True)

    assert response.status_code == 200
    assert not response.wsgi_request.user.is_authenticated
    assert shared_gate.has_passed(response.wsgi_request)


def test_an_unknown_persona_is_refused(behind_the_gate):
    response = behind_the_gate.post(
        reverse("accounts:act_as"), {"user_id": "00000000-0000-0000-0000-000000000000"}
    )
    assert response.status_code == 302
    assert not response.wsgi_request.user.is_authenticated


def test_the_persona_selector_will_not_redirect_off_site(behind_the_gate):
    marko = factories.UserFactory()
    response = behind_the_gate.post(
        reverse("accounts:act_as"),
        {"user_id": str(marko.pk), "next": "https://mujal.invalid/koguja"},
    )
    assert "mujal.invalid" not in response["Location"]


# -- I. restricted material does not leak through the department scope -----


def test_the_department_scope_sees_normal_and_nothing_else(behind_the_gate):
    owner = factories.UserFactory()
    factories.MatterFactory(owner=owner, title="Tavaline teema", visibility=Visibility.NORMAL)
    factories.MatterFactory(
        owner=owner, title="Konfidentsiaalne teema", visibility=Visibility.RESTRICTED
    )

    body = behind_the_gate.get("/ulevaade/").content.decode()
    assert "Konfidentsiaalne teema" not in body


def test_an_ownerless_restricted_matter_is_not_visible_to_the_department():
    """The trap this scope was built around.

    `Q(owner=None)` compiles to `owner IS NULL`, which matches every ownerless
    archive row — and the historical import creates thousands of them. A
    participation clause built for a scope with no user would hand the whole
    archive to anybody who typed the shared password.
    """
    hidden = factories.ArchiveMatterFactory(
        title="Omanikuta piiratud arhiivikirje", visibility=Visibility.RESTRICTED
    )
    factories.ArchiveMatterFactory(title="Omanikuta tavaline arhiivikirje")

    visible = set(Matter.objects.visible_to(DEPARTMENT_VIEWER).values_list("title", flat=True))
    assert "Omanikuta tavaline arhiivikirje" in visible
    assert hidden.title not in visible


def test_participation_is_nothing_when_the_scope_knows_nobody():
    """`NOTHING`, not an owner clause. The clause is what would leak."""
    from app.core.authorization import NOTHING

    assert restricted_participation_q(department_scope()) == NOTHING


def test_the_department_viewer_is_not_a_user_and_cannot_own_anything():
    """It has no primary key, so it cannot be written to a foreign key."""
    assert DEPARTMENT_VIEWER.pk is None
    assert not hasattr(DEPARTMENT_VIEWER, "_meta")
    assert scope_for_user(DEPARTMENT_VIEWER) == department_scope()


def test_the_department_scope_never_sees_restricted_by_role():
    scope = department_scope()
    assert not scope.sees_all_restricted
    assert matter_visibility_q(scope).children  # a real filter, not "everything"


def test_a_persona_still_sees_only_what_that_persona_may_see(behind_the_gate):
    """The gate changes who is asked, not what authorization answers.

    Marko is a reader rather than a second lawyer: since docs/adr/0042 two
    lawyers see the same department, so only a persona from outside the legal
    team can show that the switch is answered by authorization at all.
    """
    marko = factories.ReaderFactory()
    ireen = factories.UserFactory()
    factories.MatterFactory(
        owner=ireen, title="Ireeni piiratud teema", visibility=Visibility.RESTRICTED
    )

    behind_the_gate.post(reverse("accounts:act_as"), {"user_id": str(marko.pk)})
    assert "Ireeni piiratud teema" not in behind_the_gate.get("/teemad/").content.decode()

    behind_the_gate.post(reverse("accounts:act_as"), {"user_id": str(ireen.pk)})
    assert "Ireeni piiratud teema" in behind_the_gate.get("/teemad/").content.decode()


# -- K. leaving --------------------------------------------------------------


def test_signing_out_closes_the_gate_as_well_as_the_persona(behind_the_gate, gate_url):
    marko = factories.UserFactory()
    behind_the_gate.post(reverse("accounts:act_as"), {"user_id": str(marko.pk)})
    behind_the_gate.post(reverse("accounts:sign_out"))

    response = behind_the_gate.get("/ulevaade/")
    assert response.status_code == 302
    assert response["Location"] == gate_url


def test_a_persona_session_without_a_gate_is_dropped(client, gate_url):
    """A session from before the gate, or one whose gate aged out."""
    marko = factories.UserFactory()
    client.force_login(marko)

    response = client.get("/ulevaade/")
    assert response.status_code == 302
    assert response["Location"] == gate_url
    assert not response.wsgi_request.user.is_authenticated


# -- the configuration itself ------------------------------------------------


def test_an_unconfigured_gate_opens_for_nobody(settings, client, gate_url):
    settings.SHARED_GATE_PASSWORD = ""
    assert client.post(gate_url, {"password": ""}).status_code == 400
    assert client.post(gate_url, {"password": "ükskõik mis"}).status_code == 400


def test_the_gate_page_does_not_exist_in_other_modes(settings, client, gate_url):
    settings.AUTH_MODE = AuthMode.NONE
    assert client.get(gate_url).status_code == 404
    assert client.get(reverse("accounts:choose_persona")).status_code == 404


@pytest.mark.parametrize(
    ("changes", "expected"),
    [
        ({"AUTH_MODE": "nonsense"}, "juristid.E009"),
        ({"AUTH_MODE": "none", "REAL_DATA_ALLOWED": True}, "juristid.E006"),
        ({"DEV_LOGIN_ENABLED": True}, "juristid.E008"),
        ({"SHARED_GATE_PASSWORD": ""}, "juristid.E010"),
        ({"SHARED_GATE_PASSWORD": "lühike"}, "juristid.E011"),
        ({"SHARED_GATE_MAX_ATTEMPTS": 0}, "juristid.E012"),
    ],
)
def test_an_unsafe_configuration_refuses_to_start(settings, changes, expected):
    from app.core.checks import check_runtime_safety

    settings.DEBUG = True
    for key, value in changes.items():
        setattr(settings, key, value)
    assert expected in {problem.id for problem in check_runtime_safety(None)}


def test_the_shared_gate_counts_as_an_authenticator_for_real_data(settings):
    """Only with every safeguard present — that is the whole condition."""
    from app.core.checks import check_runtime_safety

    settings.DEBUG = False
    settings.SECRET_KEY = "a-real-secret"  # noqa: S105
    settings.REAL_DATA_ALLOWED = True
    settings.SESSION_COOKIE_SECURE = True
    # Behind the tunnel, and saying so — without which no HSTS header is sent
    # and CSRF skips its referer check (juristid.E014).
    settings.SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    assert check_runtime_safety(None) == []


def test_real_data_with_a_short_password_still_refuses(settings):
    from app.core.checks import check_runtime_safety

    settings.DEBUG = False
    settings.SECRET_KEY = "a-real-secret"  # noqa: S105
    settings.REAL_DATA_ALLOWED = True
    settings.SESSION_COOKIE_SECURE = True
    settings.SHARED_GATE_PASSWORD = "1925"  # noqa: S105
    assert "juristid.E011" in {problem.id for problem in check_runtime_safety(None)}


# -- response headers --------------------------------------------------------
#
# Found on the live deployment, over real HTTPS: authenticated pages carried no
# `Cache-Control` at all, and no `Strict-Transport-Security`, because nothing
# told Django that something in front had terminated TLS.


def test_a_page_behind_the_gate_is_never_stored(behind_the_gate):
    """`Vary: Cookie` stops a shared cache. It does not stop the back button.

    After signing out, a browser would re-display a page of member material from
    its own history cache without asking the server anything. `no-store` is what
    closes that, and it has to be `no-store` rather than `no-cache` — the latter
    permits storing and only asks for revalidation.
    """
    response = behind_the_gate.get("/ulevaade/")
    assert "no-store" in response["Cache-Control"]


def test_a_page_for_a_selected_persona_is_never_stored(behind_the_gate):
    marko = factories.UserFactory()
    behind_the_gate.post(reverse("accounts:act_as"), {"user_id": str(marko.pk)})
    assert "no-store" in behind_the_gate.get("/minu-too/")["Cache-Control"]


def test_the_gate_page_itself_may_be_cached(client, gate_url):
    """It is the same bytes for everybody and holds nothing worth protecting."""
    assert "no-store" not in client.get(gate_url).get("Cache-Control", "")


def test_static_files_stay_cacheable(behind_the_gate):
    """Content-hashed and identical for everybody; making them uncacheable
    would cost every page load for no privacy gain."""
    response = behind_the_gate.get("/static/css/app.css")
    assert "no-store" not in response.get("Cache-Control", "")


def test_a_view_that_set_its_own_caching_keeps_it(behind_the_gate, settings):
    """`setdefault`, not assignment — a download that thought about this wins."""
    from django.http import HttpResponse

    from app.core.middleware import PrivateResponseMiddleware

    def view(request):
        response = HttpResponse("x")
        response["Cache-Control"] = "private, max-age=60"
        return response

    middleware = PrivateResponseMiddleware(view)
    request = behind_the_gate.get("/ulevaade/").wsgi_request
    assert middleware(request)["Cache-Control"] == "private, max-age=60"


def test_a_deployment_behind_a_proxy_must_say_so(settings):
    """Without it: no HSTS, `is_secure()` False, and CSRF skips its referer check."""
    from app.core.checks import check_runtime_safety

    settings.DEBUG = False
    settings.SECRET_KEY = "a-real-secret"  # noqa: S105
    settings.SESSION_COOKIE_SECURE = True
    settings.SECURE_PROXY_SSL_HEADER = None
    assert "juristid.E014" in {problem.id for problem in check_runtime_safety(None)}
