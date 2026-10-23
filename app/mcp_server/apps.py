from django.apps import AppConfig


class McpServerConfig(AppConfig):
    """The read-only MCP server for AI clients (docs/adr/0185).

    An installed app for its management commands (`run_mcp_server`,
    `check_mcp_server`) and nothing else: it owns no models and no migrations,
    and it answers only through the services the pages use, as the reader a
    verified access token names.
    """

    name = "app.mcp_server"
    label = "mcp_server"
    verbose_name = "MCP server"
