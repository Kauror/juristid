"""Stage-0 surfaces: health, landing page and the gated development sign-in."""

from __future__ import annotations

import pytest
from django.urls import reverse

pytestmark = pytest.mark.django_db


def test_healthz_reports_database_connectivity(client):
    response = client.get("/healthz")
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert payload["database"] == "ok"


def test_home_page_renders_for_anonymous_visitors(client, settings):
    """The doorway names the stage it is running, whatever that stage is."""
    settings.APPLICATION_STAGE = "Stage 1"
    response = client.get(reverse("core:home"))
    assert response.status_code == 200
    body = response.content.decode()
    assert settings.APPLICATION_NAME in body
    assert "Stage 1" in body


def test_development_sign_in_is_absent_unless_explicitly_enabled(client, settings):
    settings.DEV_LOGIN_ENABLED = False
    assert client.get(reverse("accounts:dev_login")).status_code == 404


def test_development_sign_in_only_offers_synthetic_users(client, settings, specialist):
    from app.accounts.models import User

    real_user = User.objects.create_user(upn="real@example.invalid", display_name="Päris")
    settings.DEV_LOGIN_ENABLED = True

    response = client.get(reverse("accounts:dev_login"))
    assert response.status_code == 200
    body = response.content.decode()
    # Assert on identifiers: display names can collide with the page's own copy.
    assert str(specialist.pk) in body
    assert str(real_user.pk) not in body
    assert specialist.display_name in body


def test_signing_in_as_a_synthetic_user_works(client, settings, specialist):
    settings.DEV_LOGIN_ENABLED = True
    response = client.post(
        reverse("accounts:dev_login"), {"user_id": str(specialist.pk)}, follow=True
    )
    assert response.status_code == 200
    assert response.context["user"].is_authenticated


def test_signing_in_as_an_unknown_user_is_refused(client, settings):
    from app.audit.enums import SecurityEventType
    from app.audit.models import SecurityAuditEvent

    settings.DEV_LOGIN_ENABLED = True
    response = client.post(reverse("accounts:dev_login"), {"user_id": "not-a-user"})
    assert response.status_code == 400
    assert SecurityAuditEvent.objects.filter(
        event_type=SecurityEventType.AUTHENTICATION_FAILED
    ).exists()


def test_design_token_page_requires_authentication(client, settings, specialist):
    settings.DEV_LOGIN_ENABLED = True
    assert client.get(reverse("core:design_tokens")).status_code == 302

    client.force_login(specialist)
    response = client.get(reverse("core:design_tokens"))
    assert response.status_code == 200
    body = response.content.decode()
    # Assert against what the view exposes, so renaming a token does not mean
    # editing this test.
    for token in response.context["surface_tokens"]:
        assert token in body


def test_favicon_no_longer_404s(client):
    """Browsers request this whether or not the page links an icon.

    Without it every visit logged `Not Found: /favicon.ico`, which is noise that
    makes a real 404 harder to notice.
    """
    response = client.get("/favicon.ico")
    assert response.status_code in {301, 302}
    assert "koda-logo-negative" in response["Location"]


def test_the_page_links_an_icon_so_browsers_need_not_probe(client, specialist):
    client.force_login(specialist)
    body = client.get(reverse("matters:department")).content.decode()
    assert 'rel="icon"' in body
