"""A retried refresh does not end the connection (MCPSEC-09).

Reusing a rotated refresh token revokes the whole family — one of the two
holders is not the client — and that is right for theft. But a client whose
`/oauth/token` call timed out retries with the same token, and two refreshes
in flight at once do the same; both used to revoke the family, pair just
issued included, silently disconnecting the colleague.

Now, within `MCP_REFRESH_GRACE_SECONDS` (30 s) of the rotation, the same
client presenting the just-rotated token again — while the replacement is
still unused — gets the very same pair back. The successor is derived from
the presented token under the server's key, so nothing secret is stored to
make that possible. Outside the window, or once the replacement has been
used, reuse detection is unchanged.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.utils import timezone

from app.mcp_access import services, verification
from app.mcp_access.models import McpAccessEvent, McpAccessEventType, McpToken, TokenKind
from tests import factories
from tests import mcp_access_support as support

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def enabled(settings):
    support.enable(settings)


@pytest.fixture
def moment(monkeypatch):
    return support.freeze_totp(monkeypatch)


@pytest.fixture
def issued(moment):
    user = factories.UserFactory()
    _, secret = support.enrolled(user, moment)
    return support.tokens_for(user, secret, moment)


def _refresh(refresh_token: str, client_id: str = support.PUBLIC_CLIENT):
    return services.refresh(
        client_id=client_id, refresh_token=refresh_token, resource="", scope="", ip_address=None
    )


def _live(family_id) -> int:
    return McpToken.objects.filter(family_id=family_id, revoked_at__isnull=True).count()


def test_a_retry_inside_the_window_gets_the_same_pair(issued):
    first = _refresh(issued.refresh_token)
    live_after_first = _live(issued.family_id)
    second = _refresh(issued.refresh_token)

    assert isinstance(first, services.IssuedTokens) and isinstance(second, services.IssuedTokens)
    assert (second.access_token, second.refresh_token) == (first.access_token, first.refresh_token)
    assert 0 < second.expires_in <= first.expires_in
    assert _live(issued.family_id) == live_after_first  # nothing new, nothing revoked
    assert verification.verify_access_token(first.access_token) is not None
    assert not McpAccessEvent.objects.filter(
        event_type=McpAccessEventType.REFRESH_REUSE_DETECTED
    ).exists()
    # And the pair goes on working: its refresh token rotates normally.
    assert isinstance(_refresh(second.refresh_token), services.IssuedTokens)


def test_a_reuse_outside_the_window_still_ends_the_family(issued, settings):
    first = _refresh(issued.refresh_token)
    assert isinstance(first, services.IssuedTokens)
    McpToken.objects.filter(replaced_by__isnull=False).update(
        revoked_at=timezone.now() - timedelta(seconds=settings.MCP_REFRESH_GRACE_SECONDS + 1)
    )

    again = _refresh(issued.refresh_token)

    assert isinstance(again, services.OAuthError) and again.error == "invalid_grant"
    assert _live(issued.family_id) == 0
    event = McpAccessEvent.objects.get(event_type=McpAccessEventType.REFRESH_REUSE_DETECTED)
    assert event.detail["seconds_since_rotation"] > settings.MCP_REFRESH_GRACE_SECONDS


def test_a_reuse_after_the_replacement_was_used_ends_the_family(issued):
    first = _refresh(issued.refresh_token)
    assert isinstance(first, services.IssuedTokens)
    assert isinstance(_refresh(first.refresh_token), services.IssuedTokens)

    again = _refresh(issued.refresh_token)

    assert isinstance(again, services.OAuthError)
    assert _live(issued.family_id) == 0


def test_another_client_gets_no_grace(issued):
    assert isinstance(_refresh(issued.refresh_token), services.IssuedTokens)
    other = _refresh(issued.refresh_token, client_id=support.JWT_CLIENT)
    assert isinstance(other, services.OAuthError)


def test_the_successor_is_not_stored_anywhere_in_the_clear(issued):
    first = _refresh(issued.refresh_token)
    assert isinstance(first, services.IssuedTokens)
    stored = list(McpToken.objects.values_list("digest", flat=True))
    assert first.refresh_token not in stored and first.access_token not in stored
    assert McpToken.objects.filter(kind=TokenKind.REFRESH).count() == 2
