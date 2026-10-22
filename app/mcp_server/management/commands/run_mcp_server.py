"""Serve the read-only MCP server (docs/adr/0185): `python manage.py run_mcp_server`.

The `mcp` Compose service's command. A management command, like every worker
on the image, so Django's system checks run before a byte is served and a
refused configuration never answers (tests/test_deployment_image.py). It then
refuses unless MCP_ENABLED is on and the process role is `mcp` — the role is
what makes the database read-only — and runs uvicorn in this process: one
worker, no proxy headers believed, no `Server` header, no access log (a request
line carries nothing worth keeping and a query string could), a short
keep-alive and a ceiling on concurrent connections.

The process's whole environment — and nothing else; juristid.E046 refuses the
browser's secrets:

    JURISTID_PROCESS_ROLE=mcp            read-only database sessions; the MCP checks
    DJANGO_SECRET_KEY=<secret>           its own value is fine; >= 50 chars on real data
    POSTGRES_DB / POSTGRES_USER=juristid_mcp / POSTGRES_PASSWORD (>= 16 chars on real data)
                                         the SELECT-only role `mcp_db_role` made (E047)
    POSTGRES_HOST=db  POSTGRES_PORT=5432  POSTGRES_SSLMODE=disable   (or DATABASE_URL)
    REAL_DATA_ALLOWED=1                  on the real-data stack
    AUTH_MODE=shared_gate                the deployment's mode (E006 refuses none on
                                         real data); its gate password is NOT given
    MCP_ENABLED=1
    JURISTID_PUBLIC_ORIGIN=https://juristid.orgusaar.ee
    MCP_RESOURCE_URLS=<Secure MCP Tunnel URL>[,<public MCP URL>]   each https
    MCP_LISTEN_HOST=0.0.0.0              inside the container network; default 127.0.0.1
    JURISTID_RUNTIME=deployed            the deployed-stack marker every service carries

Optional: MCP_LISTEN_PORT (8001), MCP_ALLOWED_HOSTS, MCP_ALLOWED_ORIGINS,
MCP_MAX_CONCURRENT_CALLS (4), MCP_CALL_TIMEOUT_SECONDS (60), MCP_FETCH_MAX_CHARS,
MCP_MAX_REQUEST_BYTES (65536), APPLICATION_ENVIRONMENT, APPLICATION_REVISION,
DJANGO_LOG_LEVEL. Never: JURISTID_SHARED_GATE_PASSWORD,
LOCAL_AUTH_MFA_ENCRYPTION_KEY, DJANGO_EMAIL_HOST_PASSWORD (E046), DJANGO_DEBUG
(E044), DEV_LOGIN_ENABLED. Not needed: DJANGO_ALLOWED_HOSTS,
DJANGO_CSRF_TRUSTED_ORIGINS, DJANGO_BEHIND_TLS_PROXY, CF_ACCESS_*, any storage
root or mount.
"""

from __future__ import annotations

from typing import Any

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

#: Connections uvicorn holds at once before it answers 503. Far above
#: MCP_MAX_CONCURRENT_CALLS, which bounds the work; this bounds the sockets.
LIMIT_CONCURRENCY = 32
#: Seconds an idle keep-alive connection is held open.
TIMEOUT_KEEP_ALIVE = 5
#: Seconds a stopping server waits for calls in flight.
TIMEOUT_GRACEFUL_SHUTDOWN = 10


def uvicorn_config(app: Any) -> Any:
    import uvicorn

    return uvicorn.Config(
        app,
        host=str(settings.MCP_LISTEN_HOST),
        port=int(settings.MCP_LISTEN_PORT),
        workers=1,
        lifespan="on",
        # Django's LOGGING already owns the root logger; uvicorn's own config
        # would replace it and add an access log.
        log_config=None,
        access_log=False,
        proxy_headers=False,
        server_header=False,
        timeout_keep_alive=TIMEOUT_KEEP_ALIVE,
        timeout_graceful_shutdown=TIMEOUT_GRACEFUL_SHUTDOWN,
        limit_concurrency=LIMIT_CONCURRENCY,
    )


class Command(BaseCommand):
    help = "Serve the read-only MCP server for AI clients (docs/adr/0185)."

    # The default, stated: the system checks run first, as for every worker.
    requires_system_checks = "__all__"

    def handle(self, *args: Any, **options: Any) -> None:
        if not settings.MCP_ENABLED:
            raise CommandError("MCP_ENABLED is off; the MCP server does not start.")
        if settings.JURISTID_PROCESS_ROLE != settings.MCP_PROCESS_ROLE:
            raise CommandError(
                "JURISTID_PROCESS_ROLE is not 'mcp'. Only that role opens the database "
                "read-only, so the MCP server does not start in any other."
            )

        # The database checks, which `requires_system_checks` leaves out: among
        # them juristid.E047, that this process is connected as a SELECT-only
        # role and not as the owner of the tables (MCPSEC-03). An error stops
        # the server here; elsewhere than on real data it is a printed warning.
        from django.core.checks import Tags

        self.check(tags=[Tags.database], databases=["default"])

        import uvicorn

        from app.mcp_server.server import build_app

        config = uvicorn_config(build_app())
        self.stdout.write(
            f"MCP server on http://{config.host}:{config.port}/mcp "
            f"(stateless, JSON, read-only database)."
        )
        uvicorn.Server(config).run()
