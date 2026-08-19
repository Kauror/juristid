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


def test_home_page_renders_for_anonymous_visitors(client):
    response = client.get(reverse("core:home"))
    assert response.status_code == 200
    assert "Stage 0" in response.content.decode()


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
    assert "--surface-canvas" in response.content.decode()
