"""Serve the MCP write gateway (docs/adr/0189): `python manage.py run_mcp_write_gateway`.

The `mcp-writer` Compose service's command. A management command, like every
worker on the image, so Django's system checks run before a byte is served —
the database checks too, among them the writer role's privilege check — and a
refused configuration never answers. It then refuses unless:

* `MCP_WRITES_ENABLED` is on (and MCP access with its public origin);
* the process role is `mcp_writer` (`JURISTID_PROCESS_ROLE`), the one whose
  database connections are its own least-privilege role with bounded
  statements;
* the gateway key is at least 32 characters.

and runs uvicorn in this process exactly as `run_mcp_server` does: one worker,
no proxy headers believed, no `Server` header, no access log, a short
keep-alive and a ceiling on concurrent connections. It listens on
`MCP_WRITE_LISTEN_HOST:MCP_WRITE_LISTEN_PORT` (default 127.0.0.1:8002; the
container sets 0.0.0.0 on the internal `mcp_write` network, no host port).
"""

from __future__ import annotations

from typing import Any

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

#: Connections uvicorn holds at once before it answers 503.
LIMIT_CONCURRENCY = 16
TIMEOUT_KEEP_ALIVE = 5
TIMEOUT_GRACEFUL_SHUTDOWN = 10


def uvicorn_config(app: Any) -> Any:
    import uvicorn

    return uvicorn.Config(
        app,
        host=str(settings.MCP_WRITE_LISTEN_HOST),
        port=int(settings.MCP_WRITE_LISTEN_PORT),
        workers=1,
        lifespan="off",
        log_config=None,
        access_log=False,
        proxy_headers=False,
        server_header=False,
        timeout_keep_alive=TIMEOUT_KEEP_ALIVE,
        timeout_graceful_shutdown=TIMEOUT_GRACEFUL_SHUTDOWN,
        limit_concurrency=LIMIT_CONCURRENCY,
    )


def start_refusal() -> str:
    """Why the gateway must not start, or "" when it may."""
    from app.mcp_writes.access import writes_enabled
    from app.mcp_writes.gateway import MIN_KEY_LENGTH, configured_key

    if not writes_enabled():
        return (
            "MCP_WRITES_ENABLED is off (or MCP access is off or has no public origin); "
            "the MCP write gateway does not start."
        )
    if settings.JURISTID_PROCESS_ROLE != settings.MCP_WRITER_PROCESS_ROLE:
        return (
            "JURISTID_PROCESS_ROLE is not 'mcp_writer'. Only that role connects as the "
            "write gateway's own database role, so the gateway does not start in any other."
        )
    if len(configured_key()) < MIN_KEY_LENGTH:
        return (
            f"MCP_WRITE_GATEWAY_KEY is missing or shorter than {MIN_KEY_LENGTH} characters; "
            "the MCP write gateway does not start."
        )
    return ""


class Command(BaseCommand):
    help = "Serve the MCP write gateway for approved AI-client changes (docs/adr/0189)."

    requires_system_checks = "__all__"

    def handle(self, *args: Any, **options: Any) -> None:
        refusal = start_refusal()
        if refusal:
            raise CommandError(refusal)

        from django.core.checks import Tags

        self.check(tags=[Tags.database], databases=["default"])

        import uvicorn

        from app.mcp_writes.gateway import build_app

        config = uvicorn_config(build_app())
        self.stdout.write(
            f"MCP write gateway on http://{config.host}:{config.port}/v1/ "
            "(prepare, commit; gateway key and bearer token required)."
        )
        uvicorn.Server(config).run()
