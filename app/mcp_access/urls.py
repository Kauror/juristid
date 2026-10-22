"""The authorization server's routes (docs/adr/0185 §4). All 404 while MCP access is off.

Exact paths, no trailing slash on the OAuth endpoints (as RFC 8414 and every
client write them). The machine endpoints are exempt from the shared gate by
these exact paths only (`app.accounts.shared_gate.is_machine_endpoint`).
"""

from django.urls import path

from app.mcp_access import views

urlpatterns = [
    path(
        ".well-known/oauth-authorization-server",
        views.authorization_server_metadata,
        name="authorization_server_metadata",
    ),
    path("oauth/authorize", views.authorize, name="authorize"),
    path("oauth/token", views.token, name="token"),
    path("oauth/revoke", views.revoke, name="revoke"),
    path("mcp-ligipaas/liitu/", views.enrol, name="enrol"),
]
