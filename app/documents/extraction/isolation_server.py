"""The parser service: the container untrusted files are opened in (docs/adr/0230).

Run as ``python -m app.documents.extraction.isolation_server --socket PATH``
inside the `intake-parser` Compose service. That container is the boundary,
and this loop is deliberately small, because everything that makes it a
boundary is outside it:

* ``network_mode: none`` — no interface but loopback; nothing a parser does can
  reach the database, the internet or another container.
* no ``env_file`` — the deployment's credentials are not in this container's
  environment to begin with, so no child can inherit them or read them out of
  ``/proc``.
* a read-only root, the socket volume and a size-bounded scratch tmpfs as the
  only writable places, and nothing else mounted — not the staging volume, not
  evidence.
* ``mem_limit`` / ``cpus`` / ``pids_limit`` on the whole container, inside
  which each file additionally gets `isolation.run_child`'s per-child limits,
  its own session, a deadline and an output cap.

One file at a time. The answer for a file is sent and the child's whole
process tree is already dead before the next connection is accepted, and the
scratch tmpfs and the socket directory are swept of anything a child left
there. The service holds no state between files.

This module never loads the deployment's settings module. It configures
Django with nothing in it, so a mistake elsewhere cannot make a setting — or a
secret — appear here.
"""

from __future__ import annotations

import argparse
import contextlib
import logging
import os
import shutil
import signal
import socket
import stat
import sys
from pathlib import Path
from typing import Any

logger = logging.getLogger("juristid.parser_service")

_DEFAULTS = {
    "JURISTID_PARSER_MAX_WALL_SECONDS": 1500,
    "JURISTID_PARSER_MAX_CPU_SECONDS": 1500,
    "JURISTID_PARSER_MAX_MEMORY_BYTES": 2 * 1024**3,
    "JURISTID_PARSER_MAX_FILE_BYTES": 256 * 1024**2,
    "JURISTID_PARSER_MAX_OUTPUT_BYTES": 512 * 1024**2,
    "JURISTID_PARSER_MAX_OPEN_FILES": 256,
    "JURISTID_PARSER_MAX_INPUT_BYTES": 128 * 1024**2,
}

#: How long a client may take to hand over one request. A reader on the same
#: host sends 100 MB in well under a second; anything slower is not a reader.
REQUEST_SECONDS = 30.0


def _env_number(name: str) -> int:
    raw = os.environ.get(name, "")
    if not raw:
        return int(_DEFAULTS[name])
    value = int(raw)
    if value <= 0:
        raise SystemExit(f"{name} must be positive")
    return value


def ceiling_from_environment() -> Any:
    from app.documents.extraction.isolation import ChildLimits

    return ChildLimits(
        wall_seconds=float(_env_number("JURISTID_PARSER_MAX_WALL_SECONDS")),
        cpu_seconds=_env_number("JURISTID_PARSER_MAX_CPU_SECONDS"),
        memory_bytes=_env_number("JURISTID_PARSER_MAX_MEMORY_BYTES"),
        file_bytes=_env_number("JURISTID_PARSER_MAX_FILE_BYTES"),
        output_bytes=_env_number("JURISTID_PARSER_MAX_OUTPUT_BYTES"),
        open_files=_env_number("JURISTID_PARSER_MAX_OPEN_FILES"),
    )


def _configure_empty_django() -> None:
    from django.conf import settings

    if not settings.configured:
        settings.configure(INSTALLED_APPS=[], DATABASES={}, USE_I18N=False, LOGGING_CONFIG=None)
        import django

        django.setup()


class Service:
    def __init__(
        self,
        *,
        socket_path: Path,
        scratch: Path,
        ceiling: Any,
        max_input: int,
        allow_test_hooks: bool = False,
    ) -> None:
        self.socket_path = socket_path
        self.scratch = scratch
        self.ceiling = ceiling
        self.max_input = max_input
        self.allow_test_hooks = allow_test_hooks
        self.listener: socket.socket | None = None
        self._inode: int | None = None

    # -- the socket -------------------------------------------------------

    def bind(self) -> None:
        if sys.platform == "win32":  # Unix sockets; mypy checks the rest on Linux
            raise RuntimeError("the parser service needs a POSIX host")
        self.socket_path.parent.mkdir(parents=True, exist_ok=True)
        with contextlib.suppress(FileNotFoundError):
            if stat.S_ISSOCK(self.socket_path.lstat().st_mode):
                self.socket_path.unlink()
        listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        old_umask = os.umask(0o077)
        try:
            listener.bind(str(self.socket_path))
        finally:
            os.umask(old_umask)
        listener.listen(8)
        listener.settimeout(5.0)
        if self.listener is not None:
            self.listener.close()
        self.listener = listener
        self._inode = self.socket_path.lstat().st_ino

    def _socket_intact(self) -> bool:
        try:
            return self.socket_path.lstat().st_ino == self._inode
        except FileNotFoundError:
            return False

    # -- the loop ---------------------------------------------------------

    def serve(self, *, once: bool = False) -> None:
        self.bind()
        logger.info("parser service listening")
        while True:
            if self.listener is None:  # pragma: no cover - bind() sets it
                raise RuntimeError("parser service is not bound")
            try:
                connection, _ = self.listener.accept()
            except TimeoutError:
                if not self._socket_intact():
                    # A child removed or replaced it. Children cannot outlive
                    # their file, so whatever did this is already dead.
                    logger.warning("parser socket was disturbed; rebinding")
                    self.bind()
                continue
            with connection:
                self.handle(connection)
            self.sweep()
            if once:
                return

    def handle(self, connection: socket.socket) -> None:
        from app.documents.extraction import isolation

        connection.settimeout(REQUEST_SECONDS)
        try:
            header, payload = isolation.receive_request(connection, max_payload=self.max_input)
        except (OSError, ValueError, ConnectionError) as error:
            logger.warning("parser request refused (%s)", type(error).__name__)
            return
        if header.get("op") == "ping":
            self._reply(connection, {"kind": "pong"})
            return
        try:
            limits = isolation.ChildLimits.from_dict(header.get("limits") or {}).clamped(
                self.ceiling
            )
        except ValueError:
            logger.warning("parser request refused (limits)")
            return
        header = dict(header)
        settings = header.get("settings")
        settings = dict(settings) if isinstance(settings, dict) else {}
        if not self.allow_test_hooks:
            settings.pop("EXTRACTION_PARSER_TEST_HOOKS", None)
        header["settings"] = settings
        try:
            run = isolation.run_child(
                header=header, payload=payload, limits=limits, scratch_root=self.scratch
            )
        except isolation.ParserUnavailable:
            logger.exception("parser child could not be started")
            self._reply(connection, {"kind": "unavailable"})
            return
        del payload
        logger.info(
            "parser child mime=%s seconds=%.2f cpu=%.2f peak_rss_mb=%.1f exit=%s code=%s",
            str(header.get("mime_type") or "")[:100],
            run.usage.seconds,
            run.usage.cpu_seconds,
            run.usage.peak_rss_bytes / (1024 * 1024),
            run.usage.exit_status or "-",
            run.failure_code or "-",
        )
        self._reply(connection, isolation.run_response(run))

    def _reply(self, connection: socket.socket, response: dict[str, Any]) -> None:
        from app.documents.extraction import isolation

        connection.settimeout(REQUEST_SECONDS * 2)
        try:
            isolation.send_response(connection, response)
        except OSError as error:
            logger.warning("parser response not delivered (%s)", type(error).__name__)

    def sweep(self) -> None:
        """Remove anything a child left in the two writable places."""
        for root, keep in ((self.scratch, None), (self.socket_path.parent, self.socket_path)):
            if not root.is_dir():
                continue
            for entry in root.iterdir():
                if keep is not None and entry == keep:
                    continue
                if entry.is_dir() and not entry.is_symlink():
                    shutil.rmtree(entry, ignore_errors=True)
                else:
                    with contextlib.suppress(OSError):
                        entry.unlink()
        if not self._socket_intact():
            self.bind()


def check(socket_path: Path) -> int:
    """Healthcheck: is something listening on the socket? Connect only.

    A connect succeeds while the service is busy with a file, so a long OCR
    run does not make the container look dead.
    """
    if sys.platform == "win32":  # Unix sockets; mypy checks the rest on Linux
        return 1
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as probe:
            probe.settimeout(5.0)
            probe.connect(str(socket_path))
    except OSError:
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Juristid parser service")
    parser.add_argument("--socket", required=True)
    parser.add_argument("--scratch", default=os.environ.get("JURISTID_PARSER_SCRATCH", "/scratch"))
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--once", action="store_true", help=argparse.SUPPRESS)
    options = parser.parse_args(argv)
    if options.check:
        return check(Path(options.socket))

    logging.basicConfig(
        level=logging.INFO, stream=sys.stderr, format="%(asctime)s %(name)s %(message)s"
    )
    _configure_empty_django()

    def stop(signum: int, frame: Any) -> None:
        # SystemExit unwinds through run_child's finally: the current child's
        # tree is killed and its scratch removed on the way out.
        raise SystemExit(0)

    if sys.platform == "win32":  # Unix sockets; mypy checks the rest on Linux
        raise SystemExit("the parser service needs a POSIX host")
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    Service(
        socket_path=Path(options.socket),
        scratch=Path(options.scratch),
        ceiling=ceiling_from_environment(),
        max_input=_env_number("JURISTID_PARSER_MAX_INPUT_BYTES"),
        allow_test_hooks=os.environ.get("JURISTID_PARSER_TEST_HOOKS") == "1",
    ).serve(once=options.once)
    return 0


if __name__ == "__main__":
    sys.exit(main())
