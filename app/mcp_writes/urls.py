"""The approval page's route (docs/adr/0189), included at ``/mcp-ligipaas/muudatus/``.

Namespace ``mcp_writes``. Behind the shared gate and the persona like every
page, and a 404 for every method while MCP writes are off.
"""

from django.urls import path

from app.mcp_writes import approval_views

urlpatterns = [
    path("<uuid:proposal_id>/", approval_views.approve, name="approve"),
]
