"""Is the MCP server answering? The `mcp` service's container healthcheck.

`["CMD", "python", "manage.py", "check_mcp_server", "--quiet"]`. Asks the
process's own `/healthz` on loopback at MCP_LISTEN_PORT, with a three-second
timeout, and exits 0 when it says `{"status": "ok"}` and 1 otherwise, saying
why on stderr. A management command rather than a `python -c` in the Compose
file, so the rule lives in one place, the system checks run first, and the
test suite covers it. The image has no curl, by design.
"""

from __future__ import annotations

import http.client
import json
from typing import Any

from django.conf import settings
from django.core.management.base import BaseCommand

TIMEOUT_SECONDS = 3


class Command(BaseCommand):
    help = "Exit non-zero unless the MCP server's /healthz answers on loopback."

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument(
            "--quiet",
            action="store_true",
            help="Say nothing on success. The healthcheck does not read stdout.",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        port = int(settings.MCP_LISTEN_PORT)
        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=TIMEOUT_SECONDS)
        try:
            connection.request("GET", "/healthz", headers={"Accept": "application/json"})
            response = connection.getresponse()
            status = response.status
            body = response.read(1024)
        except (OSError, http.client.HTTPException) as exc:
            message = f"The MCP server on port {port} did not answer: {exc}."
            raise SystemExit(self._fail(message)) from exc
        finally:
            connection.close()

        try:
            healthy = status == 200 and json.loads(body) == {"status": "ok"}
        except ValueError:
            healthy = False
        if not healthy:
            raise SystemExit(self._fail(f"The MCP server on port {port} answered {status}."))

        if not options["quiet"]:
            self.stdout.write(f"MCP server is answering on port {port}.")

    def _fail(self, message: str) -> int:
        self.stderr.write(message)
        return 1
