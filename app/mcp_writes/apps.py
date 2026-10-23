from django.apps import AppConfig


class McpWritesConfig(AppConfig):
    """Proposed, approved and executed MCP writes (docs/adr/0189)."""

    name = "app.mcp_writes"
    label = "mcp_writes"
    verbose_name = "MCP muudatused"
