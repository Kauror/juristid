"""SEC-013: nobody makes the server parse a multipart body before signing in (ADR 0235).

Before `MultipartAuthBoundaryMiddleware`, `UploadLimitMiddleware` — and after it the
NUL check and the upload titles — read ``request.POST`` before
`AuthenticationModeMiddleware` decided anything, so an anonymous request had its
whole multipart body received, parsed and spooled to temporary files and was
refused only afterwards. These tests watch Django's own parser and upload
handlers: a refused request must never reach them. Every body is small and made
here; nothing leaves the machine.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.http.multipartparser import MultiPartParser
from django.urls import reverse
from django.utils import timezone

from app.accounts import shared_gate
from app.accounts.enums import AuthMode
from app.documents import multipart_boundary
from app.documents.upload_limits import UploadLimitHandler
from tests import factories
from tests.gate import PASSWORD, apply_shared_gate

pytestmark = pytest.mark.django_db

SAABUNUD = reverse("matters:intake")
PDF = b"%PDF-1.4\n1 0 obj<</Type/Catalog>>endobj\ntrailer<</Root 1 0 R>>\n%%EOF\n"


def pdf(name: str = "kiri.pdf") -> SimpleUploadedFile:
    return SimpleUploadedFile(name, PDF, content_type="application/pdf")


@pytest.fixture
def parses(monkeypatch) -> list[str]:
    """Every multipart parse and every upload handler made: what a refusal must not reach."""
    seen: list[str] = []
    parse = MultiPartParser.parse
    handler_init = UploadLimitHandler.__init__

    def counting_parse(self):
        seen.append("parse")
        return parse(self)

    def counting_handler(self, request=None):
        seen.append("handler")
        handler_init(self, request)

    monkeypatch.setattr(MultiPartParser, "parse", counting_parse)
    monkeypatch.setattr(UploadLimitHandler, "__init__", counting_handler)
    return seen


@pytest.fixture
def gate(settings):
    return apply_shared_gate(settings, PASSWORD)


@pytest.fixture
def behind_the_gate(client, gate):
    response = client.post(reverse("accounts:shared_gate"), {"password": PASSWORD})
    assert response.status_code == 302
    return client


@pytest.fixture
def persona(behind_the_gate):
    """Gate passed and a persona chosen: the state every upload door needs."""
    user = factories.UserFactory()
    behind_the_gate.force_login(user)
    return behind_the_gate


# -- refused before the body is read ---------------------------------------------------


def test_an_anonymous_multipart_post_is_refused_without_parsing(client, gate, parses):
    response = client.post(SAABUNUD, {"failid": [pdf()]})

    # The answer an anonymous request always got (the gate), now before the parse.
    assert response.status_code == 302
    assert response["Location"] == reverse("accounts:shared_gate")
    assert parses == []
    assert response["Cache-Control"].startswith("no-store")


def test_an_anonymous_htmx_upload_gets_the_sign_in_refusal(client, gate, parses):
    response = client.post(SAABUNUD, {"failid": [pdf()]}, HTTP_HX_REQUEST="true")

    assert response.status_code == 401
    assert response["X-Juristid-Failure"] == "sign-in"
    assert response["X-Juristid-Sign-In"] == reverse("accounts:shared_gate")
    assert parses == []


def test_the_gate_alone_is_not_enough_to_upload(behind_the_gate, parses):
    """The password typed but no persona chosen: nobody is signed in yet."""
    response = behind_the_gate.post(SAABUNUD, {"failid": [pdf()]})
    assert response.status_code == 302
    assert response["Location"] == reverse("accounts:choose_persona")
    assert parses == []


def test_the_gate_alone_offers_htmx_the_persona_back_to_its_page(behind_the_gate, parses):
    response = behind_the_gate.post(
        SAABUNUD,
        {"failid": [pdf()]},
        HTTP_HX_REQUEST="true",
        HTTP_HX_CURRENT_URL="http://testserver/saabunud/",
    )
    assert response.status_code == 401
    assert response["X-Juristid-Sign-In"] == (
        reverse("accounts:choose_persona") + "?next=%2Fsaabunud%2F"
    )
    assert parses == []


def test_a_persona_whose_gate_expired_is_refused_without_parsing(persona, settings, parses):
    session = persona.session
    session[shared_gate.GATE_PASSED_AT] = (
        timezone.now() - timedelta(seconds=settings.SHARED_GATE_SESSION_SECONDS + 60)
    ).isoformat()
    session.save()

    response = persona.post(SAABUNUD, {"failid": [pdf()]})

    assert response.status_code == 302
    assert response["Location"] == reverse("accounts:shared_gate")
    assert parses == []


def test_a_deactivated_persona_is_refused_without_parsing(persona, parses):
    user = persona.session["_auth_user_id"]
    factories.UserFactory._meta.model.objects.filter(pk=user).update(is_active=False)

    response = persona.post(SAABUNUD, {"failid": [pdf()]})

    assert response.status_code == 302
    assert parses == []


class Untouchable:
    """A WSGI input that fails the test if anything reads it."""

    def read(self, *args):
        raise AssertionError("the body was read")

    readline = read


def _unread_multipart(client, path: str, declared: str | None):
    extra = {"wsgi.input": Untouchable(), "CONTENT_TYPE": "multipart/form-data; boundary=XyZ"}
    if declared is not None:
        extra["CONTENT_LENGTH"] = declared
    return client.generic("POST", path, data=b"", **extra)


@pytest.mark.parametrize(
    "route",
    ["accounts:shared_gate", "accounts:choose_persona", "accounts:sign_in", "admin:login"],
)
@pytest.mark.parametrize("declared", [str(512 * 1024 * 1024), "65537", None, "kaks"])
def test_the_sign_in_surfaces_take_no_large_multipart_body_from_anybody(
    persona, parses, route, declared
):
    response = _unread_multipart(persona, reverse(route), declared)

    assert response.status_code == 413
    assert parses == []


@pytest.mark.parametrize("encoding", ["urlencoded", "multipart"])
def test_the_gate_still_takes_its_small_form(client, gate, encoding):
    """A browser sends the password urlencoded; a small multipart form (Django's
    test client, a script) is still a form, and is read as one."""
    if encoding == "urlencoded":
        response = client.post(
            reverse("accounts:shared_gate"),
            f"password={PASSWORD}",
            content_type="application/x-www-form-urlencoded",
        )
    else:
        response = client.post(reverse("accounts:shared_gate"), {"password": PASSWORD})
    assert response.status_code == 302
    assert shared_gate.has_passed(response.wsgi_request)


def test_a_refused_body_is_answered_without_reading_it(client, gate):
    """The body stream itself is never read, whatever length it declares."""
    assert _unread_multipart(client, SAABUNUD, str(512 * 1024 * 1024)).status_code == 302
    assert _unread_multipart(client, SAABUNUD, None).status_code == 302


# -- a signed-in request is parsed as before -------------------------------------------


def test_a_signed_in_persona_reaches_the_upload_policy(persona, settings, parses):
    """Past the boundary, the policy's own refusal still answers (here 413)."""
    settings.UPLOAD_MAX_FILES_PER_REQUEST = 1
    settings.DATA_UPLOAD_MAX_NUMBER_FILES = 11

    response = persona.post(SAABUNUD, {"failid": [pdf("a.pdf"), pdf("b.pdf")]})

    assert response.status_code == 413
    assert "parse" in parses and "handler" in parses


def test_a_signed_in_upload_still_fails_csrf_when_forged(persona, parses):
    persona.handler.enforce_csrf_checks = True
    response = persona.post(SAABUNUD, {"failid": [pdf()]})
    assert response.status_code == 403
    # Parsed (the person is signed in) and then refused by CSRF, as before.
    assert "parse" in parses


def test_development_mode_still_takes_a_signed_in_upload(client, settings, parses):
    settings.AUTH_MODE = AuthMode.NONE
    client.force_login(factories.UserFactory())
    client.post(SAABUNUD, {"failid": [pdf()]})
    assert "parse" in parses


def test_development_mode_refuses_an_anonymous_upload(client, settings, parses):
    settings.AUTH_MODE = AuthMode.NONE
    response = client.post(SAABUNUD, {"failid": [pdf()]})
    assert response.status_code == 302
    assert parses == []


# -- the other modes, read the way their middleware reads them ---------------------------


def test_local_password_needs_a_session_signed_in_here(client, settings, parses):
    settings.AUTH_MODE = AuthMode.LOCAL_PASSWORD
    client.force_login(factories.UserFactory())  # a session from another mode

    response = client.post(SAABUNUD, {"failid": [pdf()]})

    assert response.status_code == 302
    assert parses == []


def test_cloudflare_access_needs_an_assertion_naming_the_same_person(
    client, settings, monkeypatch, parses
):
    from app.accounts import cloudflare_access

    settings.AUTH_MODE = AuthMode.CLOUDFLARE_ACCESS
    user = factories.UserFactory(upn="jurist@koda.example")
    client.force_login(user)

    def nobody(meta):
        raise cloudflare_access.AccessDenied("no_assertion")

    monkeypatch.setattr(cloudflare_access, "identity_from_request", nobody)
    assert client.post(SAABUNUD, {"failid": [pdf()]}).status_code == 403

    monkeypatch.setattr(
        cloudflare_access, "identity_from_request", lambda meta: ("teine@koda.example", {})
    )
    assert client.post(SAABUNUD, {"failid": [pdf()]}).status_code == 403
    assert parses == []

    monkeypatch.setattr(
        cloudflare_access, "identity_from_request", lambda meta: ("jurist@koda.example", {})
    )
    assert multipart_boundary.signed_in_for_files(client.post(SAABUNUD, {"x": "1"}).wsgi_request)


# -- where it stands ---------------------------------------------------------------------


def test_the_boundary_comes_before_every_middleware_that_reads_a_body(settings):
    middleware = list(settings.MIDDLEWARE)
    boundary = middleware.index("app.documents.multipart_boundary.MultipartAuthBoundaryMiddleware")
    assert middleware.index("django.contrib.sessions.middleware.SessionMiddleware") < boundary
    for reader in (
        "app.documents.upload_limits.UploadLimitMiddleware",
        "app.core.middleware.RefuseNulMiddleware",
        "app.core.middleware.UploadTitlesMiddleware",
        "django.contrib.auth.middleware.AuthenticationMiddleware",
        "app.accounts.middleware.AuthenticationModeMiddleware",
    ):
        assert boundary < middleware.index(reader), reader


def test_every_public_sign_in_route_is_mounted():
    """A renamed route must not silently drop out of the list."""
    paths = multipart_boundary.public_auth_paths()
    assert len(paths) == len(multipart_boundary.PUBLIC_AUTH_ROUTES)
