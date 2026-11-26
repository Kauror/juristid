"""The parser child: one file, one process, then gone (docs/adr/0230).

Started by `isolation.run_child` as ``python -B -P -m`` with a job directory as
its only argument. It never sees the deployment's settings module: Django is
configured from the ``EXTRACTION_*`` values in the request and nothing else,
so there is no database configuration, no secret key and no installed app to
reach for. It reads ``request.json`` and ``input.bin`` from the job directory,
runs the one parser that owns the MIME type, checks the result against the
same postcondition the parent will check again, and prints one JSON verdict.

The verdict goes to a private duplicate of the original stdout; file
descriptor 1 is pointed at stderr first, so a library that prints cannot
corrupt the answer — and stderr is discarded by the parent unread.

This module must stay importable without Django settings and must not import
anything from the application outside `app.documents.extraction` and
`app.documents.enums`; `tests/test_parser_isolation.py` reads its imports.
"""

from __future__ import annotations

import json
import os
import signal
import sys
from pathlib import Path
from typing import Any

_ANSWER_FD = 1


def main(argv: list[str]) -> int:
    global _ANSWER_FD
    if sys.platform == "win32":  # started only by run_child, which is POSIX only
        return 2
    # The answer channel, taken before anything can print to stdout.
    _ANSWER_FD = os.dup(1)
    answer = os.fdopen(_ANSWER_FD, "wb")
    os.dup2(2, 1)
    # A file-size limit hit becomes an OSError the verdict can name, rather
    # than a SIGXFSZ that kills the process mid-sentence.
    signal.signal(signal.SIGXFSZ, signal.SIG_IGN)
    verdict: dict[str, Any] = (
        _run(Path(argv[1])) if len(argv) == 2 else {"status": "error", "type": "Usage"}
    )
    # The child's own peak address space, for sizing RLIMIT_AS (a number only).
    verdict["vm_peak_bytes"] = _vm_peak_bytes()
    answer.write(json.dumps(verdict, ensure_ascii=False).encode("utf-8"))
    answer.flush()
    return 0


def _run(job: Path) -> dict[str, Any]:
    try:
        request = json.loads((job / "request.json").read_text(encoding="utf-8"))
        content = (job / "input.bin").read_bytes()
    except (OSError, ValueError) as error:
        return {"status": "error", "type": type(error).__name__}

    _configure(request.get("settings") or {})

    from app.documents.extraction import parsers  # noqa: F401  (registers every parser)
    from app.documents.extraction.base import SourceFile, registry
    from app.documents.extraction.errors import ExtractionFailed, ExtractionNotApplicable
    from app.documents.extraction.isolation import (
        FILE_LIMIT,
        MEMORY_LIMIT,
        encode_result,
    )
    from app.documents.extraction.limits import current_limits, guard_parse_result

    mime_type = str(request.get("mime_type") or "")
    parser = registry.for_mime_type(mime_type)
    if parser is None:
        return {"status": "not_applicable", "code": "unsupported", "detail": ""}
    source = SourceFile(
        content=content, filename=str(request.get("filename") or ""), mime_type=mime_type
    )
    del content
    try:
        hooked = _test_hook(source.filename)
        if hooked is not None:
            return hooked
        result = parser.parse(source)
        guard_parse_result(result, current_limits())
        encoded = encode_result(result, text_only=bool(request.get("text_only")))
    except ExtractionNotApplicable as error:
        return {"status": "not_applicable", "code": error.code, "detail": error.detail}
    except ExtractionFailed as error:
        return {"status": "failed", "code": error.code, "detail": error.detail}
    except MemoryError:
        return {"status": "failed", "code": MEMORY_LIMIT, "detail": ""}
    except OSError as error:
        import errno

        if error.errno == errno.EFBIG:
            return {"status": "failed", "code": FILE_LIMIT, "detail": ""}
        return {"status": "error", "type": type(error).__name__}
    except Exception as error:
        return {"status": "error", "type": type(error).__name__}
    return {"status": "done", "parser": parser.name, "result": encoded}


def _vm_peak_bytes() -> int:
    try:
        for line in Path("/proc/self/status").read_text().splitlines():
            if line.startswith("VmPeak:"):
                return int(line.split()[1]) * 1024
    except (OSError, ValueError, IndexError):
        pass
    return 0


def _configure(values: dict[str, Any]) -> None:
    """Django, configured from the parser limits and nothing else."""
    from django.conf import settings

    chosen = {
        name: value
        for name, value in values.items()
        if isinstance(name, str) and name.startswith("EXTRACTION_")
    }
    settings.configure(
        INSTALLED_APPS=[],
        DATABASES={},
        USE_I18N=False,
        USE_TZ=True,
        LOGGING_CONFIG=None,
        **chosen,
    )
    import django

    django.setup()


#: Test hooks, recognised only when the file is *named* for them and only when
#: the parent's settings let a hook through (they never do outside the test
#: suite: `DOCUMENT_PARSER_TEST_HOOKS` is false by default and the test
#: settings switch it on for the isolation tests alone). They stand in for a
#: parser that hangs, crashes natively, floods its output, forks, or eats
#: memory, so the boundary can be shown to stop each of them without a real
#: exploit file.
_HOOK_PREFIX = "juristid-parser-test-hook-"


def _test_hook(filename: str) -> dict[str, Any] | None:
    from django.conf import settings

    if not getattr(settings, "EXTRACTION_PARSER_TEST_HOOKS", False):
        return None
    if not filename.startswith(_HOOK_PREFIX):
        return None
    hook = filename[len(_HOOK_PREFIX) :].split(".", 1)[0]
    if sys.platform == "win32":  # the hooks are POSIX signals and processes
        return None
    if hook == "hang":
        while True:
            signal.pause()
    if hook == "spin":
        while True:
            pass
    if hook == "crash":
        os.kill(os.getpid(), signal.SIGSEGV)
    if hook == "flood":
        while True:
            os.write(_ANSWER_FD, b"[" * 65536)
    if hook == "fork-hang":
        import subprocess

        subprocess.Popen(["sleep", "600"])  # noqa: S607 - test hook
        subprocess.Popen(["sleep", "600"])  # noqa: S607 - test hook
        while True:
            signal.pause()
    if hook == "memory":
        hoard = []
        while True:
            hoard.append(bytearray(64 * 1024 * 1024))
    if hook == "secrets":
        # What anything running inside the child could find.
        import socket

        found = {
            "environment": sorted(os.environ),
            "parent_environ_readable": _readable(f"/proc/{os.getppid()}/environ"),
            "network": _can_connect(socket),
        }
        return {"status": "failed", "code": "test_hook", "detail": json.dumps(found)}
    return None


def _readable(path: str) -> bool:
    try:
        with open(path, "rb") as handle:
            handle.read(1)
    except OSError:
        return False
    return True


def _can_connect(socket: Any) -> bool:
    try:
        with socket.create_connection(("1.1.1.1", 53), timeout=2):
            return True
    except OSError:
        return False


if __name__ == "__main__":
    sys.exit(main(sys.argv))
