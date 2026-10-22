"""The read-only MCP server (docs/adr/0185).

Not a Django app: it owns no models. It runs in its own process
(`manage.py run_mcp_server`) and answers through the same services the pages
use, as the authenticated reader.
"""
