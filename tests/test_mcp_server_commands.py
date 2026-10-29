"""`run_mcp_server` and `check_mcp_server`, the `mcp` service's command and healthcheck."""

from __future__ import annotations

import http.server
import socket
import threading
from collections.abc import Iterator
from typing import Any

import pytest
from django.core.management import CommandError, call_command, get_commands, load_command_class

from tests import mcp_server_fakes as fx


def _command(name: str) -> Any:
    return load_command_class(get_commands()[name], name)


@pytest.mark.parametrize("name", ["run_mcp_server", "check_mcp_server"])
def test_both_commands_keep_the_system_checks_on(name):
    # tests/test_deployment_image.py holds every worker command on the image to this.
    assert _command(name).requires_system_checks


def test_the_server_refuses_to_start_with_mcp_off(settings):
    settings.MCP_ENABLED = False
    settings.JURISTID_PROCESS_ROLE = "mcp"
    with pytest.raises(CommandError, match="MCP_ENABLED is off"):
        call_command("run_mcp_server")


@pytest.mark.parametrize("role", ["", "web"])
def test_the_server_refuses_to_start_outside_the_read_only_role(settings, role):
    settings.MCP_ENABLED = True
    settings.JURISTID_PROCESS_ROLE = role
    with pytest.raises(CommandError, match="JURISTID_PROCESS_ROLE is not 'mcp'"):
        call_command("run_mcp_server")


@pytest.mark.django_db
def test_the_server_runs_uvicorn_with_the_hardened_configuration(settings, monkeypatch):
    import uvicorn

    fx.configure(settings)
    settings.JURISTID_PROCESS_ROLE = "mcp"
    # The test database's owner role is not a SELECT-only reader, and E047 is an
    # error for it whatever REAL_DATA_ALLOWED says; this test is about uvicorn.
    monkeypatch.setattr("app.mcp_server.db_role.privilege_problems", lambda cursor: [])
    settings.MCP_LISTEN_HOST = "0.0.0.0"  # noqa: S104 - the container network, as in Compose
    settings.MCP_LISTEN_PORT = 8001
    served: list[Any] = []
    monkeypatch.setattr(
        uvicorn.Server, "run", lambda self, sockets=None: served.append(self.config)
    )

    call_command("run_mcp_server")

    [config] = served
    assert (config.host, config.port) == ("0.0.0.0", 8001)  # noqa: S104
    assert config.workers == 1
    assert config.proxy_headers is False
    assert config.server_header is False
    assert config.access_log is False
    assert config.log_config is None
    assert config.limit_concurrency == 32
    assert config.timeout_keep_alive == 5
    assert config.lifespan == "on"


class _Health(http.server.BaseHTTPRequestHandler):
    body = b'{"status": "ok"}'
    status = 200

    def do_GET(self) -> None:
        self.send_response(self.status if self.path == "/healthz" else 404)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(self.body)

    def log_message(self, *args: Any) -> None:
        pass


@pytest.fixture
def health_server() -> Iterator[http.server.HTTPServer]:
    server = http.server.HTTPServer(("127.0.0.1", 0), _Health)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()


def test_the_healthcheck_passes_when_healthz_answers(settings, health_server, capsys):
    settings.MCP_LISTEN_PORT = health_server.server_address[1]
    call_command("check_mcp_server", "--quiet")
    assert capsys.readouterr().out == ""


def test_the_healthcheck_fails_when_healthz_says_something_else(
    settings, health_server, monkeypatch
):
    monkeypatch.setattr(_Health, "status", 503)
    settings.MCP_LISTEN_PORT = health_server.server_address[1]
    with pytest.raises(SystemExit) as exited:
        call_command("check_mcp_server", "--quiet")
    assert exited.value.code == 1


def test_the_healthcheck_fails_when_nothing_listens(settings):
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        free_port = probe.getsockname()[1]
    settings.MCP_LISTEN_PORT = free_port
    with pytest.raises(SystemExit) as exited:
        call_command("check_mcp_server", "--quiet")
    assert exited.value.code == 1


def test_the_healthcheck_passes_against_the_real_application(settings, monkeypatch):
    """The real ASGI app under uvicorn on a free loopback port, asked by the command."""
    import time

    import uvicorn

    from app.mcp_server.server import build_app

    fx.configure(settings)
    fx.install(monkeypatch)
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    settings.MCP_LISTEN_PORT = port
    server = uvicorn.Server(
        uvicorn.Config(build_app(), host="127.0.0.1", port=port, log_config=None, lifespan="on")
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 15
        while not server.started and time.monotonic() < deadline:
            time.sleep(0.05)
        assert server.started
        call_command("check_mcp_server", "--quiet")
    finally:
        server.should_exit = True
        thread.join(10)
