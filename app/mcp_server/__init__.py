"""The read-only MCP server (docs/adr/0185).

An installed app (`app.mcp_server.apps.McpServerConfig`) for its two
management commands, with no models. It runs in its own process,
`manage.py run_mcp_server` with `JURISTID_PROCESS_ROLE=mcp`, whose database
sessions are read-only, and answers through the same services the pages use,
as the reader the bearer token names:

- `server`       the MCP server, its eight tools and the ASGI application;
- `schemas`      the tools' input and output schemas;
- `auth`         bearer-token verification and the protected-resource metadata;
- `runtime`      the bounded worker pool every database call runs in;
- `instructions` what the server tells a client about the register;
- `readers`      what each tool reads (workstream A).

Nothing here is imported at Django start-up: the web process never loads the
SDK unless it is asked to.
"""
