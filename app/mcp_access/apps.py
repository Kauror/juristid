from django.apps import AppConfig


class McpAccessConfig(AppConfig):
    """Who may connect an AI client to Juristid, and the OAuth state of those connections.

    The authorization-server half of docs/adr/0185: grants, personal
    authenticators, authorization codes and tokens. The MCP server itself lives
    in `app.mcp_server` and only ever reads from here.
    """

    name = "app.mcp_access"
    label = "mcp_access"
    verbose_name = "MCP ligipääs"
