"""Discovery and challenges name `juristid.write` only while writes are on (OAUTH-SCOPES.md).

Both metadata documents — the authorization server's on `web` (RFC 8414) and
the protected resource's on `mcp` (RFC 9728) — list the read scope, and the
write scope only while `MCP_WRITES_ENABLED` is on. `challenge(..., scope=)`
lets a write tool ask for a step-up; `/mcp` itself still requires only read.
"""

from __future__ import annotations

import pytest
from django.core.cache import cache

from app.mcp_server import auth
from tests import mcp_access_support as support

READ = "juristid.read"
WRITE = "juristid.write"


@pytest.fixture(autouse=True)
def mcp_on(settings):
    support.enable(settings)
    cache.clear()
    yield
    cache.clear()


@pytest.mark.django_db
@pytest.mark.parametrize(("writes", "expected"), [(False, [READ]), (True, [READ, WRITE])])
def test_the_authorization_server_metadata_lists_write_only_while_writes_are_on(
    client, settings, writes, expected
):
    settings.MCP_WRITES_ENABLED = writes
    response = client.get("/.well-known/oauth-authorization-server")
    assert response.status_code == 200
    assert response.json()["scopes_supported"] == expected


@pytest.mark.parametrize(("writes", "expected"), [(False, [READ]), (True, [READ, WRITE])])
def test_the_protected_resource_metadata_lists_write_only_while_writes_are_on(
    settings, writes, expected
):
    settings.MCP_WRITES_ENABLED = writes
    document = auth.protected_resource_metadata(support.RESOURCE)
    assert document["scopes_supported"] == expected
    assert document["authorization_servers"] == [support.ORIGIN]


def test_the_challenge_still_names_read_by_default(settings):
    settings.MCP_WRITES_ENABLED = True
    header = auth.challenge(support.RESOURCE)
    assert f'scope="{READ}"' in header and 'error="invalid_token"' in header


def test_a_write_tool_can_ask_for_the_write_step_up(settings):
    header = auth.challenge(
        support.RESOURCE, error="insufficient_scope", scope=auth.write_step_up_scope()
    )
    assert header.startswith("Bearer ")
    assert f'scope="{READ} {WRITE}"' in header
    assert 'error="insufficient_scope"' in header
    assert (
        'resource_metadata="https://mcp.example.test/.well-known/oauth-protected-resource/mcp"'
        in header
    )


@pytest.mark.parametrize("bad", ['juristid.read" error="x', "a\\b", "", "two  spaces", "tab\there"])
def test_a_scope_that_could_break_the_header_is_refused(bad):
    with pytest.raises(ValueError, match="scope tokens"):
        auth.challenge(support.RESOURCE, scope=bad)


def test_the_mcp_endpoint_still_requires_only_the_read_scope(settings):
    settings.MCP_WRITES_ENABLED = True
    assert auth.auth_settings().required_scopes == [READ]
