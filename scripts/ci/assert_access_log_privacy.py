"""Prove, with the image's own gunicorn, that an access-log format keeps query strings out.

Run inside the application container — CI's container smoke test does it with
``docker compose exec`` against the running local stack — with the format under
test in ``ACCESS_LOG_FORMAT``: the value a deployed stack's Compose file
resolves to (ENG-071).

Two short-lived gunicorns serve the real application on a spare loopback port,
and each answers one request that carries a secret in its query string and
another in its Referer:

1. with gunicorn's *default* format, which must show both secrets. Otherwise
   this probe cannot see a query string at all, and its second answer would be
   a pass by blindness;
2. with the format under test, which must log the path and neither secret, no
   query string and no ``?``.

Standard library only, and it prints the one line it judged, so a failure in CI
shows exactly what reached the log.
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

QUERY_SENTINEL = "RESTRICTED-SENTINEL"
REFERER_SENTINEL = "REFERER-SENTINEL"
PATH = "/otsing/"
QUERY = f"q={QUERY_SENTINEL}&foo=bar"


def _spare_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """A redirect to sign-in is still one logged request; following it adds a second."""

    def redirect_request(self, *args: Any, **kwargs: Any) -> None:
        return None


def _wait_until_listening(port: int, process: subprocess.Popen[bytes]) -> None:
    # A bare TCP connect, not an HTTP request: it sends nothing, so it writes
    # no access-log line of its own.
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise SystemExit(f"gunicorn exited with {process.returncode} before listening")
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=1):
                return
        except OSError:
            time.sleep(0.2)
    raise SystemExit("gunicorn did not start listening within 60 s")


def _request(port: int) -> None:
    request = urllib.request.Request(
        f"http://127.0.0.1:{port}{PATH}?{QUERY}",
        headers={"Referer": f"http://127.0.0.1:{port}/teemad/?q={REFERER_SENTINEL}"},
    )
    try:
        urllib.request.build_opener(_NoRedirect).open(request, timeout=60).read()
    except urllib.error.HTTPError:
        pass  # the redirect to sign-in, answered and logged like any response


def access_line(format_args: list[str]) -> str:
    """The one access-log line a gunicorn with these format arguments writes."""
    port = _spare_port()
    with tempfile.TemporaryDirectory() as scratch:
        log = Path(scratch) / "access.log"
        process = subprocess.Popen(  # noqa: S603 - a fixed argv, no shell
            [
                sys.executable,
                "-m",
                "gunicorn",
                "config.wsgi:application",
                "--bind",
                f"127.0.0.1:{port}",
                "--workers",
                "1",
                "--access-logfile",
                str(log),
                *format_args,
            ]
        )
        try:
            _wait_until_listening(port, process)
            _request(port)
        finally:
            process.terminate()
            process.wait(timeout=60)
        lines = log.read_text(encoding="utf-8").splitlines()
    if len(lines) != 1:
        raise SystemExit(f"expected exactly one access-log line, found {len(lines)}: {lines!r}")
    return lines[0]


def main() -> int:
    candidate = os.environ.get("ACCESS_LOG_FORMAT", "")
    if not candidate:
        print("::error::ACCESS_LOG_FORMAT is empty; nothing to check", file=sys.stderr)
        return 2

    control = access_line([])
    print(f"gunicorn default format: {control}")
    if QUERY_SENTINEL not in control or REFERER_SENTINEL not in control:
        print(
            "::error::even gunicorn's default format showed no secret, so this probe "
            "cannot see a query string and proves nothing"
        )
        return 1

    line = access_line(["--access-logformat", candidate])
    print(f"format under test:       {line}")
    problems = []
    if f" {PATH} " not in line:
        problems.append(f"the path {PATH} is not in the line")
    for leaked in (QUERY_SENTINEL, REFERER_SENTINEL, "q=", "foo=bar", "?"):
        if leaked in line:
            problems.append(f"{leaked!r} reached the access log")
    for problem in problems:
        print(f"::error::{problem}")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
