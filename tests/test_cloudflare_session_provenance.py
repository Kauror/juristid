"""A session is reused under Cloudflare Access only if Access established it (SEC-012).

The middleware used to let any authenticated session through whose account's
address matched the verified assertion — a statement about the *account*, not
about the *session*. A persona chosen behind the shared gate, a development
sign-in or a personal-password session naming the same person would have
carried on after the deployment switched to Access, with its own expiry, its
own gate keys and any administrator state it held. Now a session is reused only
when it carries the provenance marker Access wrote at sign-in, for this
identity, while the account is still active and real and the assertion that
established it has not expired; anything else is ended and the request decided
from the assertion alone (docs/adr/0232).

Access stays dormant: the mode is set by this module's own settings fixture,
against a key this test owns, exactly as tests/test_cloudflare_access.py does.
"""

from __future__ import annotations

import time

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from django.core.cache import cache
from django.test import Client
from django.urls import reverse

from app.accounts import admin_session, cloudflare_access, local_auth, shared_gate
from app.accounts.enums import AuthMode, UserRole
from app.accounts.models import User
from tests import factories
from tests.gate import apply_shared_gate
from tests.local_auth import apply_local_password

TEAM_DOMAIN = "https://naidiskoda.cloudflareaccess.invalid"
AUDIENCE = "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
PERSON = "jurist@naidiskoda.invalid"
PAGE = "/osakond/"


@pytest.fixture
def signing_key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def _access(settings, signing_key, monkeypatch) -> None:
    settings.AUTH_MODE = AuthMode.CLOUDFLARE_ACCESS
    settings.CF_ACCESS_TEAM_DOMAIN = TEAM_DOMAIN
    settings.CF_ACCESS_AUDIENCE = AUDIENCE
    settings.DEV_LOGIN_ENABLED = False
    cache.clear()
    published = jwt.algorithms.RSAAlgorithm.to_jwk(signing_key.public_key(), as_dict=True)
    published.update({"kid": "current", "alg": "RS256", "use": "sig"})
    monkeypatch.setattr(cloudflare_access, "_fetch_jwks", lambda: {"keys": [published]})


@pytest.fixture
def access(settings, signing_key, monkeypatch):
    _access(settings, signing_key, monkeypatch)
    yield settings
    cache.clear()


def assertion(key, *, email: str = PERSON, expires_in: int = 3600, **claims) -> str:
    now = int(time.time())
    payload = {
        "aud": AUDIENCE,
        "iss": TEAM_DOMAIN,
        "email": email,
        "iat": now,
        "exp": now + expires_in,
        **claims,
    }
    return jwt.encode(payload, key, algorithm="RS256", headers={"kid": "current"})


def real_person(**kwargs) -> User:
    return factories.UserFactory(is_synthetic=False, **kwargs)


def _get(client: Client, key, **claims):
    return client.get(PAGE, HTTP_CF_ACCESS_JWT_ASSERTION=assertion(key, **claims))


def _key(client: Client) -> str:
    morsel = client.cookies.get("sessionid")
    return morsel.value if morsel is not None else ""


# -- sessions another mode opened ---------------------------------------------------


@pytest.mark.django_db
def test_a_persona_session_from_the_shared_gate_does_not_become_an_access_session(
    settings, signing_key, monkeypatch
):
    """The finding: the gate's persona names the same address, and used to carry on."""
    from tests.admin_session import choose, pass_gate

    person = real_person(upn=PERSON, role=UserRole.SPECIALIST)
    apply_shared_gate(settings)
    client = pass_gate(Client())
    choose(client, person)
    session = client.session
    session[admin_session.SESSION_STATE] = {"user": str(person.pk), "binding": "x"}
    session.save()
    gate_key = _key(client)
    assert shared_gate.GATE_PASSED_AT in client.session

    _access(settings, signing_key, monkeypatch)
    response = _get(client, signing_key)

    assert response.status_code == 200
    assert response.wsgi_request.user.pk == person.pk
    assert _key(client) != gate_key
    carried = client.session
    assert shared_gate.GATE_PASSED_AT not in carried
    assert admin_session.SESSION_STATE not in carried
    assert carried[cloudflare_access.SESSION_MARKER]["via"] == cloudflare_access.PROVENANCE


@pytest.mark.django_db
def test_a_development_sign_in_does_not_become_an_access_session(access, signing_key):
    person = real_person(upn=PERSON)
    client = Client()
    client.force_login(person)
    before = _key(client)

    response = _get(client, signing_key)

    assert response.status_code == 200
    assert _key(client) != before
    assert cloudflare_access.SESSION_MARKER in client.session


@pytest.mark.django_db
def test_a_personal_password_session_does_not_become_an_access_session(access, signing_key):
    person = real_person(upn=PERSON)
    client = Client()
    client.force_login(person)
    session = client.session
    session[local_auth.SESSION_USER] = str(person.pk)
    session[local_auth.SESSION_SECOND_FACTOR_AT] = "2026-11-26T08:00:00+00:00"
    session.save()

    response = _get(client, signing_key)

    assert response.status_code == 200
    assert local_auth.SESSION_USER not in client.session
    assert local_auth.SESSION_SECOND_FACTOR_AT not in client.session


@pytest.mark.django_db
def test_a_forged_marker_without_a_session_access_wrote_is_still_not_enough(access, signing_key):
    """The marker is read only for the account it names, via this mode."""
    person = real_person(upn=PERSON)
    client = Client()
    client.force_login(person)
    session = client.session
    session[cloudflare_access.SESSION_MARKER] = {
        "via": "shared_gate",
        "user": str(person.pk),
        "email": PERSON,
        "sub": "",
        "expires_at": int(time.time()) + 3600,
    }
    session.save()
    before = _key(client)

    _get(client, signing_key)

    assert _key(client) != before


# -- sessions Access opened ---------------------------------------------------------------


@pytest.mark.django_db
def test_a_fresh_access_session_is_reused_while_it_holds(access, signing_key):
    person = real_person(upn=PERSON)
    client = Client()
    first = _get(client, signing_key, sub="subject-1")
    established = _key(client)

    second = _get(client, signing_key, sub="subject-1")

    assert first.status_code == second.status_code == 200
    assert second.wsgi_request.user.pk == person.pk
    assert _key(client) == established


@pytest.mark.django_db
def test_another_identity_replaces_the_session(access, signing_key):
    real_person(upn="eelmine@naidiskoda.invalid")
    arriving = real_person(upn=PERSON)
    client = Client()
    _get(client, signing_key, email="eelmine@naidiskoda.invalid")

    response = _get(client, signing_key)

    assert response.wsgi_request.user.pk == arriving.pk
    assert client.session[cloudflare_access.SESSION_MARKER]["email"] == PERSON


@pytest.mark.django_db
def test_another_subject_for_the_same_address_replaces_the_session(access, signing_key):
    real_person(upn=PERSON)
    client = Client()
    _get(client, signing_key, sub="subject-1")
    established = _key(client)

    response = _get(client, signing_key, sub="subject-2")

    assert response.status_code == 200
    assert _key(client) != established
    assert client.session[cloudflare_access.SESSION_MARKER]["sub"] == "subject-2"


@pytest.mark.django_db
def test_an_account_made_synthetic_after_sign_in_is_refused(access, signing_key):
    person = real_person(upn=PERSON)
    client = Client()
    assert _get(client, signing_key).status_code == 200
    User.objects.filter(pk=person.pk).update(is_synthetic=True)

    response = _get(client, signing_key)

    assert response.status_code == 403
    assert not response.wsgi_request.user.is_authenticated


@pytest.mark.django_db
def test_an_account_switched_off_after_sign_in_is_refused(access, signing_key):
    person = real_person(upn=PERSON)
    client = Client()
    assert _get(client, signing_key).status_code == 200
    User.objects.filter(pk=person.pk).update(is_active=False)

    response = _get(client, signing_key)

    assert response.status_code == 403
    assert not response.wsgi_request.user.is_authenticated


@pytest.mark.django_db
def test_a_session_past_its_establishing_assertion_is_established_again(access, signing_key):
    real_person(upn=PERSON)
    client = Client()
    _get(client, signing_key)
    session = client.session
    marker = dict(session[cloudflare_access.SESSION_MARKER])
    marker["expires_at"] = int(time.time()) - 1
    session[cloudflare_access.SESSION_MARKER] = marker
    session.save()
    before = _key(client)

    response = _get(client, signing_key)

    assert response.status_code == 200
    assert _key(client) != before
    assert client.session[cloudflare_access.SESSION_MARKER]["expires_at"] > time.time()


@pytest.mark.django_db
def test_an_assertion_in_its_last_seconds_never_makes_a_browser_session(access, signing_key):
    """`set_expiry(0)` is Django's "until the browser closes", the longest of all."""
    real_person(upn=PERSON)
    client = Client()

    response = _get(client, signing_key, expires_in=-5)  # inside the verifier's leeway

    assert response.status_code == 200
    established = response.wsgi_request.session
    assert established.get_expire_at_browser_close() is False
    assert established.get_expiry_age() <= 1


@pytest.mark.django_db
def test_an_expired_assertion_is_refused_even_for_an_established_session(access, signing_key):
    real_person(upn=PERSON)
    client = Client()
    _get(client, signing_key)

    response = _get(client, signing_key, expires_in=-3600)

    assert response.status_code == 403


# -- leaving Access -------------------------------------------------------------------------


@pytest.mark.django_db
def test_an_access_session_is_not_a_gate_pass(settings, signing_key, monkeypatch):
    real_person(upn=PERSON)
    _access(settings, signing_key, monkeypatch)
    client = Client()
    assert _get(client, signing_key).status_code == 200

    apply_shared_gate(settings)
    response = client.get(PAGE)

    assert response.status_code == 302
    assert response["Location"].startswith(reverse("accounts:shared_gate"))
    assert not response.wsgi_request.user.is_authenticated


@pytest.mark.django_db
def test_an_access_session_is_not_a_personal_sign_in(settings, signing_key, monkeypatch):
    real_person(upn=PERSON)
    _access(settings, signing_key, monkeypatch)
    client = Client()
    assert _get(client, signing_key).status_code == 200

    apply_local_password(settings)
    response = client.get(PAGE)

    assert response.status_code == 302
    assert response["Location"].startswith(reverse("accounts:sign_in"))
    assert not response.wsgi_request.user.is_authenticated
