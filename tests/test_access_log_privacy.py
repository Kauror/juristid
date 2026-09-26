"""Business text stays out of the container logs (ENG-071).

The operator rule is that no real data leaves the host, "a log uploaded
anywhere" included (deploy/unraid-main/OPERATOR.md). Two routine paths broke it:

* gunicorn's default access-log format writes ``%(r)s`` — the method, the *raw
  URI* and the protocol — and ``%(f)s``, the Referer. Every search term and
  filter value is a GET query string here, and the Referer of a page reached
  from a search carries the same query, so both went to ``docker logs``;
* two warnings named the uploaded file, and a filename is business text: it is
  often the subject line of the letter.

Two layers of proof. The configuration tests read what the deployments pass
and render it through gunicorn's own logger, which needs POSIX modules and is
skipped only on a Windows workstation, never in CI. The wire test is in CI's
container smoke job: `scripts/ci/assert_access_log_privacy.py` runs the image's
gunicorn with the resolved format against the running application.
"""

from __future__ import annotations

import importlib
import logging
import sys
from datetime import timedelta
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest
import yaml
from django.conf import settings

from app.documents import pending
from app.documents.enums import ExtractionState
from app.documents.extraction import orchestrator
from app.documents.extraction.base import registry
from app.documents.uploads import AcceptedUpload

ROOT = Path(settings.BASE_DIR)

#: The two stacks that run gunicorn with an access log.
STACKS = {
    "main": ROOT / "deploy" / "unraid-main" / "compose.yml",
    "test": ROOT / "deploy" / "unraid-test" / "compose.yml",
}

#: The decision, stated once: remote address, time, method, *path*, protocol,
#: status, size, duration. No ``%(r)s`` (the raw URI), no ``%(q)s``, no
#: Referer, no user agent.
ACCESS_LOG_FORMAT = '%(h)s %(t)s "%(m)s %(U)s %(H)s" %(s)s %(b)s %(M)sms'

#: Every gunicorn atom that would put a query string or a Referer back. The
#: ``{...}i`` and ``{...}e`` forms are request headers and WSGI environ keys,
#: which gunicorn offers for any name.
LEAKING_ATOMS = (
    "%(r)s",
    "%(q)s",
    "%(f)s",
    "{referer}i",
    "{raw_uri}e",
    "{query_string}e",
    "{request_uri}e",
    "{http_referer}e",
)

QUERY_SECRET = "RESTRICTED-SENTINEL"
REFERER_SECRET = "REFERER-SENTINEL"
FILENAME_SECRET = "Kiri ministrile SALAJANE-SENTINEL.pdf"


def _compose(path: Path) -> dict[str, Any]:
    loaded: dict[str, Any] = yaml.safe_load(path.read_text(encoding="utf-8"))
    return loaded


def _flag(command: list[str], flag: str) -> str:
    assert flag in command, f"{flag} is missing from {command}"
    return command[command.index(flag) + 1]


def _gunicorn_commands() -> dict[str, list[str]]:
    """Every first-party Compose service that runs gunicorn, by file and name."""
    found: dict[str, list[str]] = {}
    for path in sorted([*ROOT.glob("deploy/**/*.yml"), ROOT / "docker-compose.yml"]):
        for name, service in (_compose(path).get("services") or {}).items():
            command = [str(part) for part in service.get("command") or []]
            if command and command[0] == "gunicorn":
                found[f"{path.relative_to(ROOT).as_posix()}:{name}"] = command
    return found


# -- the access log -------------------------------------------------------------


def test_every_gunicorn_that_writes_an_access_log_says_what_it_writes():
    """Not merely that `--access-logfile -` exists: the format, exactly.

    Over every Compose file in the repository, so a third stack cannot come in
    with gunicorn's default format and pass because nobody listed it here.
    """
    commands = _gunicorn_commands()
    assert {
        "deploy/unraid-main/compose.yml:web",
        "deploy/unraid-test/compose.yml:web",
    } <= set(commands), commands

    for where, command in commands.items():
        if "--access-logfile" in command:
            assert _flag(command, "--access-logformat") == ACCESS_LOG_FORMAT, where


def test_the_format_logs_the_path_and_nothing_that_carries_a_query():
    assert "%(U)s" in ACCESS_LOG_FORMAT
    for atom in LEAKING_ATOMS:
        assert atom not in ACCESS_LOG_FORMAT, atom
    # And still worth reading: when, what, how it ended and how long it took.
    for atom in ("%(t)s", "%(m)s", "%(s)s", "%(b)s", "%(M)s"):
        assert atom in ACCESS_LOG_FORMAT, atom


def _gunicorn_logging() -> ModuleType:
    """gunicorn's logger, which imports `fcntl`, `pwd` and `grp`.

    Skipped where those do not exist — a Windows workstation — and imported
    plainly everywhere else, so CI can never quietly skip it.
    """
    if sys.platform == "win32":
        return pytest.importorskip("gunicorn.glogging", reason="gunicorn needs POSIX modules")
    return importlib.import_module("gunicorn.glogging")


@pytest.mark.parametrize("stack", sorted(STACKS))
def test_a_search_is_logged_by_its_path_alone(stack, tmp_path):
    """The deployed format, rendered by gunicorn's own access logger.

    The request is the one the audit named: a search with a restricted term and
    a filter, arriving from a page whose own address carried a query.
    """
    glogging = _gunicorn_logging()
    from gunicorn.config import Config

    command = [str(part) for part in _compose(STACKS[stack])["services"]["web"]["command"]]
    config = Config()
    config.set("accesslog", str(tmp_path / "access.log"))
    config.set("access_log_format", _flag(command, "--access-logformat"))
    access = glogging.Logger(config)

    referer = f"https://juristid.example/teemad/?q={REFERER_SECRET}"
    environ = {
        "REQUEST_METHOD": "GET",
        "RAW_URI": f"/otsing/?q={QUERY_SECRET}&foo=bar",
        "PATH_INFO": "/otsing/",
        "QUERY_STRING": f"q={QUERY_SECRET}&foo=bar",
        "SERVER_PROTOCOL": "HTTP/1.1",
        "REMOTE_ADDR": "172.18.0.9",
        "HTTP_REFERER": referer,
        "HTTP_USER_AGENT": "Mozilla/5.0",
    }
    request = SimpleNamespace(headers=[("REFERER", referer), ("USER-AGENT", "Mozilla/5.0")])
    response = SimpleNamespace(status="200 OK", sent=5120, headers=[("Content-Type", "text/html")])
    access.access(response, request, environ, timedelta(milliseconds=42))
    for handler in access.access_log.handlers:
        handler.flush()

    [line] = (tmp_path / "access.log").read_text(encoding="utf-8").splitlines()
    assert '"GET /otsing/ HTTP/1.1" 200 5120 42ms' in line
    for leaked in (QUERY_SECRET, REFERER_SECRET, "q=", "foo=bar", "?"):
        assert leaked not in line, f"{leaked!r} reached the access log: {line}"


# -- log retention ----------------------------------------------------------------


@pytest.mark.parametrize("stack", sorted(STACKS))
def test_every_long_lived_container_keeps_a_bounded_log(stack):
    """`docker logs` must not grow for as long as a container lives.

    Every service with a restart policy runs indefinitely, so every one of them
    is bounded — with the driver named, because `max-size` and `max-file` are
    options of json-file and not of every driver a daemon might default to.
    """
    services = _compose(STACKS[stack])["services"]
    long_lived = {name: service for name, service in services.items() if service.get("restart")}
    assert {"db", "web", "intake-reader", "searchindex", "tunnel"} <= set(long_lived)

    for name, service in long_lived.items():
        logging_config = service.get("logging")
        assert logging_config == {
            "driver": "json-file",
            "options": {"max-size": "10m", "max-file": "5"},
        }, name


# -- filenames -----------------------------------------------------------------


@pytest.mark.django_db
def test_a_full_hold_does_not_log_the_file_it_turned_away(
    client, evidence_root, monkeypatch, caplog
):
    monkeypatch.setattr(pending, "MAX_HELD_FILES", 0)
    upload = AcceptedUpload(
        content=b"%PDF-1.4 hoitud", filename=FILENAME_SECRET, mime_type="application/pdf"
    )

    with caplog.at_level(logging.WARNING, logger="app.documents.pending"):
        held = pending.hold(client.session, [upload])

    assert held == []
    assert "hold is full" in caplog.text, "the warning this test is about was never written"
    assert "application/pdf" in caplog.text
    assert "SALAJANE" not in caplog.text
    assert FILENAME_SECRET not in caplog.text


@pytest.mark.parametrize(
    ("reference", "named_as"),
    [("version 4242", "version 4242"), ("", "a file with no row reference")],
)
def test_a_parser_crash_logs_the_row_and_never_the_filename(
    monkeypatch, caplog, reference, named_as
):
    """Both callers pass a row reference; a caller without one must not fall back to the name."""
    parser = registry.for_mime_type("application/pdf")
    assert parser is not None

    def crash(self: Any, source: Any) -> Any:
        raise RuntimeError("the parser tripped over its own feet")

    monkeypatch.setattr(type(parser), "parse", crash)

    with caplog.at_level(logging.ERROR, logger="app.documents.extraction.orchestrator"):
        outcome = orchestrator.parse_source(
            filename=FILENAME_SECRET,
            mime_type="application/pdf",
            load=lambda: b"%PDF-1.4",
            reference=reference,
        )

    assert outcome.state == ExtractionState.FAILED
    assert outcome.error_code == "parser_error"
    assert f"crashed on {named_as}" in caplog.text
    assert "SALAJANE" not in caplog.text
    assert FILENAME_SECRET not in caplog.text
