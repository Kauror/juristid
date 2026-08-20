"""Cloudflare Access, against a signing key this test owns.

The header is not the authentication; the signature is. So these tests mint
their own RSA key, sign their own assertions, and hand Cloudflare's published
key set to the verifier — which makes it possible to ask the only question that
matters: what happens when somebody sends an assertion this deployment should
not accept.

Every case below is a way in that must be closed (Stage-2D brief 57, 58, 59).
"""

from __future__ import annotations

import time

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from django.core.cache import cache

from app.accounts import cloudflare_access
from app.accounts.enums import UserRole
from app.accounts.models import User
from tests import factories


def real_person(**kwargs):
    """A real identity, which is what Access asserts.

    `UserFactory` mints synthetic accounts because everything else in the suite
    wants one; the middleware refuses those on purpose, so these tests have to
    ask for the real thing.
    """
    return factories.UserFactory(is_synthetic=False, **kwargs)


TEAM_DOMAIN = "https://naidiskoda.cloudflareaccess.invalid"
AUDIENCE = "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
OTHER_AUDIENCE = "ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff"
PERSON = "jurist@naidiskoda.invalid"


@pytest.fixture
def signing_key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture
def other_key():
    """Somebody else's key. Correct algorithm, wrong signer."""
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture(autouse=True)
def access_configured(settings, signing_key, monkeypatch):
    settings.CF_ACCESS_ENABLED = True
    settings.CF_ACCESS_TEAM_DOMAIN = TEAM_DOMAIN
    settings.CF_ACCESS_AUDIENCE = AUDIENCE
    settings.DEV_LOGIN_ENABLED = False
    cache.clear()

    published = jwt.algorithms.RSAAlgorithm.to_jwk(signing_key.public_key(), as_dict=True)
    published.update({"kid": "current", "alg": "RS256", "use": "sig"})
    monkeypatch.setattr(cloudflare_access, "_fetch_jwks", lambda: {"keys": [published]})
    yield
    cache.clear()


def assertion(
    key,
    *,
    kid: str = "current",
    audience: str = AUDIENCE,
    issuer: str = TEAM_DOMAIN,
    email: str = PERSON,
    expires_in: int = 3600,
    algorithm: str = "RS256",
    **claims,
) -> str:
    now = int(time.time())
    payload = {
        "aud": audience,
        "iss": issuer,
        "email": email,
        "iat": now,
        "exp": now + expires_in,
        **claims,
    }
    return jwt.encode(payload, key, algorithm=algorithm, headers={"kid": kid})


# -- verification ----------------------------------------------------------


def test_a_correctly_signed_assertion_names_the_person(signing_key):
    claims = cloudflare_access.verify(assertion(signing_key))
    assert cloudflare_access.email_from(claims) == PERSON


def test_an_unsigned_assertion_is_refused(signing_key):
    """`alg: none` is the oldest way into a JWT verifier, and it stays shut."""
    token = jwt.encode(
        {"aud": AUDIENCE, "iss": TEAM_DOMAIN, "email": PERSON}, key=None, algorithm="none"
    )
    with pytest.raises(cloudflare_access.AccessDenied):
        cloudflare_access.verify(token)


def test_an_assertion_signed_by_somebody_else_is_refused(other_key):
    with pytest.raises(cloudflare_access.AccessDenied):
        cloudflare_access.verify(assertion(other_key))


def test_an_assertion_for_a_different_application_is_refused(signing_key):
    """The audience tag is what stops another app's token opening this one."""
    with pytest.raises(cloudflare_access.AccessDenied):
        cloudflare_access.verify(assertion(signing_key, audience=OTHER_AUDIENCE))


def test_an_assertion_from_a_different_team_is_refused(signing_key):
    with pytest.raises(cloudflare_access.AccessDenied):
        cloudflare_access.verify(
            assertion(signing_key, issuer="https://someone-else.cloudflareaccess.invalid")
        )


def test_an_expired_assertion_is_refused(signing_key):
    with pytest.raises(cloudflare_access.AccessDenied):
        cloudflare_access.verify(assertion(signing_key, expires_in=-120))


def test_an_assertion_naming_an_unpublished_key_is_refused(signing_key):
    with pytest.raises(cloudflare_access.AccessDenied):
        cloudflare_access.verify(assertion(signing_key, kid="retired"))


def test_an_empty_header_is_refused():
    with pytest.raises(cloudflare_access.AccessDenied):
        cloudflare_access.verify("")


def test_access_without_an_audience_denies_rather_than_defaults(settings, signing_key):
    settings.CF_ACCESS_AUDIENCE = ""
    with pytest.raises(cloudflare_access.AccessDenied):
        cloudflare_access.verify(assertion(signing_key))


def test_a_verified_assertion_with_no_email_is_refused(signing_key):
    claims = cloudflare_access.verify(assertion(signing_key, email=""))
    with pytest.raises(cloudflare_access.AccessDenied):
        cloudflare_access.email_from(claims)


# -- the middleware --------------------------------------------------------


@pytest.mark.django_db
def test_a_known_person_is_signed_in_from_the_assertion(client, signing_key):
    person = real_person(upn=PERSON, role=UserRole.SPECIALIST)
    response = client.get("/ulevaade/", HTTP_CF_ACCESS_JWT_ASSERTION=assertion(signing_key))
    assert response.status_code == 200
    assert response.wsgi_request.user.pk == person.pk


@pytest.mark.django_db
def test_a_request_with_no_assertion_is_denied_not_passed_through(client):
    """There is no public surface behind Access to fall through to."""
    response = client.get("/ulevaade/")
    assert response.status_code == 403


@pytest.mark.django_db
def test_a_forged_header_does_not_authenticate_anybody(client, other_key):
    real_person(upn=PERSON)
    response = client.get("/ulevaade/", HTTP_CF_ACCESS_JWT_ASSERTION=assertion(other_key))
    assert response.status_code == 403
    assert not response.wsgi_request.user.is_authenticated


@pytest.mark.django_db
def test_the_unsigned_email_header_authenticates_nobody(client):
    """`Cf-Access-Authenticated-User-Email` carries no signature at all."""
    real_person(upn=PERSON)
    response = client.get("/ulevaade/", HTTP_CF_ACCESS_AUTHENTICATED_USER_EMAIL=PERSON)
    assert response.status_code == 403


@pytest.mark.django_db
def test_a_verified_stranger_is_not_provisioned_a_seat(client, signing_key):
    """Widening an Access policy must not create an account here."""
    response = client.get(
        "/ulevaade/",
        HTTP_CF_ACCESS_JWT_ASSERTION=assertion(signing_key, email="keegi@mujal.invalid"),
    )
    assert response.status_code == 403
    assert not User.objects.filter(upn__iexact="keegi@mujal.invalid").exists()


@pytest.mark.django_db
def test_a_synthetic_account_cannot_become_a_real_identity(client, signing_key):
    from app.accounts.services import create_synthetic_user

    create_synthetic_user(upn=PERSON, display_name="Näidisjurist")
    response = client.get("/ulevaade/", HTTP_CF_ACCESS_JWT_ASSERTION=assertion(signing_key))
    assert response.status_code == 403


@pytest.mark.django_db
def test_a_deactivated_person_is_denied(client, signing_key):
    real_person(upn=PERSON, is_active=False)
    response = client.get("/ulevaade/", HTTP_CF_ACCESS_JWT_ASSERTION=assertion(signing_key))
    assert response.status_code == 403


@pytest.mark.django_db
def test_a_session_belonging_to_somebody_else_is_replaced(client, signing_key):
    """Two people, one browser profile. The assertion wins, not the session."""
    previous = real_person(upn="eelmine@naidiskoda.invalid")
    arriving = real_person(upn=PERSON)
    client.force_login(previous)

    response = client.get("/ulevaade/", HTTP_CF_ACCESS_JWT_ASSERTION=assertion(signing_key))
    assert response.status_code == 200
    assert response.wsgi_request.user.pk == arriving.pk


@pytest.mark.django_db
def test_the_health_check_answers_before_anybody_is_authenticated(client):
    """The container runtime is not a person and cannot hold an assertion."""
    response = client.get("/healthz")
    assert response.status_code == 200


@pytest.mark.django_db
def test_access_off_leaves_every_request_alone(client, settings):
    settings.CF_ACCESS_ENABLED = False
    person = real_person()
    client.force_login(person)
    assert client.get("/ulevaade/").status_code == 200


# -- the configuration itself ----------------------------------------------


def test_real_data_without_an_authenticator_fails_the_deployment_check(settings):
    from app.core.checks import check_runtime_safety

    settings.REAL_DATA_ALLOWED = True
    settings.CF_ACCESS_ENABLED = False
    settings.DEBUG = False
    assert "juristid.E006" in {problem.id for problem in check_runtime_safety(None)}


def test_a_synthetic_sign_in_behind_access_fails_the_deployment_check(settings):
    from app.core.checks import check_runtime_safety

    settings.CF_ACCESS_ENABLED = True
    settings.DEV_LOGIN_ENABLED = True
    assert "juristid.E008" in {problem.id for problem in check_runtime_safety(None)}


def test_an_unconfigured_access_fails_the_deployment_check(settings):
    from app.core.checks import check_runtime_safety

    settings.CF_ACCESS_ENABLED = True
    settings.CF_ACCESS_AUDIENCE = ""
    assert "juristid.E007" in {problem.id for problem in check_runtime_safety(None)}
