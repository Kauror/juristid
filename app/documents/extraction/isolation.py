"""Where an untrusted file is opened: never in the process that holds the keys.

Every parser in this package reads bytes that came from outside — a file a
person picked on `Uus teema`, a message somebody forwarded, a PDF a register
published. The libraries underneath (pypdfium2, Pillow, Tesseract, python-docx,
openpyxl, extract-msg, the e-mail parser) are large native and pure-Python code
bases, and "the parser is a pure function of bytes" (base.py) is a statement
about *our* code, not theirs. Before SEC-008 that function ran inside the
long-lived reader process, which holds the database password, the Django secret
key, every other variable of the deployment's environment and an unrestricted
network, and nothing but the parsers' own budgets bounded how long or how much
memory one file could take (docs/adr/0230).

This module is the one door to a parser. It has three modes, chosen by
``DOCUMENT_PARSER_ISOLATION``:

``service``
    The bytes go over a Unix socket to the parser service
    (`isolation_server`), a separate Compose container with no network at all,
    no environment file, a read-only root, a size-bounded scratch tmpfs and its
    own memory, CPU and process ceilings. That service runs each file in a
    fresh child exactly as ``subprocess`` mode does. This is the boundary the
    deployed `Uus teema` reader runs behind.

``subprocess``
    A fresh child process per file, started in this container with an
    allow-listed environment (no database settings, no secret key, no API
    keys), its own session so the whole tree can be killed, resource limits
    applied before ``exec`` (address space, CPU seconds, file size, open
    files, no core dumps), a wall-clock deadline, a cap on what it may print,
    and a private scratch directory that is removed afterwards. It does *not*
    take the network away or narrow the filesystem — an unprivileged process
    under Docker's default seccomp profile cannot do either — so on its own it
    is defence in depth, not the boundary (docs/adr/0230 §Gaps).

``inprocess``
    The parser runs here, as it did before. The test suite's mode, because a
    test that swaps a parser for a fake one must see the fake run; refused by
    `run_intake_reader` in a deployed runtime.

Whatever the mode, the answer is data and never code: the child's output is a
bounded JSON document that is validated field by field and rebuilt into the
dataclasses of `base.py` here, and the postcondition every parse must meet
(`guard_parse_result`) is checked again on this side of the boundary. Nothing
is unpickled.
"""

from __future__ import annotations

import base64
import contextlib
import dataclasses
import json
import logging
import os
import shutil
import signal
import socket
import struct
import subprocess
import sys
import tempfile
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from django.conf import settings

from app.documents.enums import DerivativeKind
from app.documents.extraction.base import (
    DerivativePayload,
    Fragment,
    ParsedAttachment,
    ParseResult,
    SourceFile,
)
from app.documents.extraction.errors import ExtractionFailed, ExtractionNotApplicable

logger = logging.getLogger(__name__)

SERVICE = "service"
SUBPROCESS = "subprocess"
INPROCESS = "inprocess"
MODES = frozenset({SERVICE, SUBPROCESS, INPROCESS})

#: The two budgets a caller may ask for. `intake` is a file somebody is waiting
#: for on a form whose claim goes stale in INTAKE_READER_STALE_CLAIM_MINUTES;
#: `batch` is a corpus run or a register read nobody is watching.
INTAKE = "intake"
BATCH = "batch"

CHILD_MODULE = "app.documents.extraction.isolation_child"

#: The environment a child is started with, and nothing else. Every name here
#: is about finding a program or a locale; none of them is a credential. The
#: deployment's environment — POSTGRES_PASSWORD, DJANGO_SECRET_KEY, the AI keys
#: the nightly run adds with `--env-file` — never reaches a parser.
_INHERITED_ENVIRONMENT = ("PATH", "LANG", "LC_ALL", "TESSDATA_PREFIX")

#: Error codes the boundary gives a file, beside the parsers' own.
TIMEOUT = "parser_timeout"
CPU_LIMIT = "parser_cpu_limit"
MEMORY_LIMIT = "parser_memory_limit"
FILE_LIMIT = "parser_file_limit"
OUTPUT_LIMIT = "parser_output_limit"
CRASHED = "parser_crashed"
PROTOCOL = "parser_protocol"

_KEEP = "Originaal on alles ja avatav."


class ParserUnavailable(RuntimeError):
    """The parser boundary itself could not be reached; no verdict on the file.

    Raised, not returned as a FAILED outcome, because a parser service that is
    restarting says nothing about the file in hand. The caller hands the claim
    back instead of settling it (`intake_extraction`, `orchestrator`).
    """


@dataclass(frozen=True)
class ChildLimits:
    """What one child, and everything it starts, may consume."""

    wall_seconds: float
    cpu_seconds: int
    memory_bytes: int
    file_bytes: int
    output_bytes: int
    open_files: int = 256

    def clamped(self, ceiling: ChildLimits) -> ChildLimits:
        """These limits, never above ``ceiling`` — the service's own numbers win."""
        return ChildLimits(
            wall_seconds=min(self.wall_seconds, ceiling.wall_seconds),
            cpu_seconds=min(self.cpu_seconds, ceiling.cpu_seconds),
            memory_bytes=min(self.memory_bytes, ceiling.memory_bytes),
            file_bytes=min(self.file_bytes, ceiling.file_bytes),
            output_bytes=min(self.output_bytes, ceiling.output_bytes),
            open_files=min(self.open_files, ceiling.open_files),
        )

    def as_dict(self) -> dict[str, float | int]:
        return {
            "wall_seconds": self.wall_seconds,
            "cpu_seconds": self.cpu_seconds,
            "memory_bytes": self.memory_bytes,
            "file_bytes": self.file_bytes,
            "output_bytes": self.output_bytes,
            "open_files": self.open_files,
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> ChildLimits:
        def number(name: str) -> float:
            value = raw.get(name)
            if isinstance(value, bool) or not isinstance(value, int | float) or value <= 0:
                raise ValueError(f"limit {name} is not a positive number")
            return value

        return cls(
            wall_seconds=float(number("wall_seconds")),
            cpu_seconds=int(number("cpu_seconds")),
            memory_bytes=int(number("memory_bytes")),
            file_bytes=int(number("file_bytes")),
            output_bytes=int(number("output_bytes")),
            open_files=int(number("open_files")),
        )


@dataclass(frozen=True)
class ChildUsage:
    """What a child actually used, measured by ``wait4``. Numbers only."""

    seconds: float = 0.0
    cpu_seconds: float = 0.0
    peak_rss_bytes: int = 0
    exit_status: str = ""


@dataclass(frozen=True)
class ChildRun:
    """What came back across the boundary, before it is believed."""

    verdict: dict[str, Any] | None
    usage: ChildUsage = field(default_factory=ChildUsage)
    failure_code: str = ""


def mode() -> str:
    chosen = str(getattr(settings, "DOCUMENT_PARSER_ISOLATION", SUBPROCESS)).strip().lower()
    if chosen not in MODES:
        raise ValueError(f"DOCUMENT_PARSER_ISOLATION={chosen!r}; expected one of {sorted(MODES)}.")
    return chosen


def limits_for(budget: str) -> ChildLimits:
    """The settings-derived limits for one budget (docs/adr/0230 §Limits)."""
    if budget == INTAKE:
        wall = float(settings.DOCUMENT_PARSER_INTAKE_WALL_SECONDS)
        output = int(settings.DOCUMENT_PARSER_INTAKE_OUTPUT_BYTES)
    elif budget == BATCH:
        wall = float(settings.DOCUMENT_PARSER_BATCH_WALL_SECONDS)
        output = int(settings.DOCUMENT_PARSER_BATCH_OUTPUT_BYTES)
    else:
        raise ValueError(f"unknown parser budget {budget!r}")
    return ChildLimits(
        wall_seconds=wall,
        # One parse is single-threaded Python plus, for a scan, one Tesseract
        # at a time; CPU seconds above wall seconds could only be spent by a
        # tree that forked to burn them.
        cpu_seconds=max(1, int(wall)),
        memory_bytes=int(settings.DOCUMENT_PARSER_MEMORY_BYTES),
        file_bytes=int(settings.DOCUMENT_PARSER_FILE_BYTES),
        output_bytes=output,
        open_files=int(settings.DOCUMENT_PARSER_OPEN_FILES),
    )


def extraction_settings() -> dict[str, Any]:
    """The parser-relevant settings, as plain JSON values, for the child to adopt.

    Only ``EXTRACTION_*`` and only scalars and lists of scalars: the child
    configures Django from this and from nothing else, so no other setting —
    and certainly no credential — can travel with it.
    """
    chosen: dict[str, Any] = {}
    for name in dir(settings):
        if not name.startswith("EXTRACTION_"):
            continue
        value = getattr(settings, name)
        if isinstance(value, bool | int | float | str):
            chosen[name] = value
        elif isinstance(value, list | tuple) and all(
            isinstance(item, bool | int | float | str) for item in value
        ):
            chosen[name] = list(value)
    return chosen


# ---------------------------------------------------------------------------
# The caller's side
# ---------------------------------------------------------------------------


def parse_isolated(
    parser: Any, source: SourceFile, *, budget: str = BATCH, wall_seconds: float | None = None
) -> ParseResult:
    """Run ``parser`` over ``source`` behind the configured boundary.

    ``wall_seconds`` narrows the budget's wall (and CPU) limit for this one file —
    a caller whose own time runs out sooner, such as the nightly Riigikogu step
    (r96 integration, ADR 0231). It never widens it, and the service clamps again.

    Returns the parser's result, or raises what the parser would have raised —
    ``ExtractionFailed`` / ``ExtractionNotApplicable`` — plus
    ``ExtractionFailed`` with one of this module's codes when the boundary
    stopped the child, and :class:`ParserUnavailable` when there was no
    boundary to ask. Never runs a parser in this process unless the mode is
    ``inprocess``.
    """
    chosen = mode()
    if chosen == INPROCESS:
        return parser.parse(source)

    limits = limits_for(budget)
    if wall_seconds is not None and wall_seconds < limits.wall_seconds:
        narrowed = max(1.0, float(wall_seconds))
        limits = dataclasses.replace(
            limits,
            wall_seconds=narrowed,
            cpu_seconds=min(limits.cpu_seconds, max(1, int(narrowed))),
        )
    header = {
        "op": "parse",
        "filename": source.filename,
        "mime_type": source.mime_type,
        "settings": extraction_settings(),
        # A form reader has no use for a thumbnail or an attachment's bytes, so
        # it does not ask for them and the output budget stays text-sized.
        "text_only": budget == INTAKE,
        "limits": limits.as_dict(),
    }
    started = time.monotonic()
    if chosen == SERVICE:
        run = _ask_service(header, source.content, limits)
    else:
        run = run_child(
            header=header,
            payload=source.content,
            limits=limits,
            scratch_root=Path(settings.DOCUMENT_PARSER_SCRATCH_ROOT or tempfile.gettempdir()),
            protect_parent=True,
        )
    # Numbers only: what one file cost is what the limits are sized from, and
    # none of it is content (Stage-2B brief 49).
    logger.info(
        "parser child mime=%s mode=%s budget=%s seconds=%.2f cpu=%.2f "
        "peak_rss_mb=%.1f exit=%s code=%s",
        source.mime_type,
        chosen,
        budget,
        time.monotonic() - started,
        run.usage.cpu_seconds,
        run.usage.peak_rss_bytes / (1024 * 1024),
        run.usage.exit_status or "-",
        run.failure_code or "-",
    )
    return interpret(parser, run, limits)


def interpret(parser: Any, run: ChildRun, limits: ChildLimits) -> ParseResult:
    """Turn what came back into the parser's own outcome, or a named failure."""
    if run.failure_code:
        raise ExtractionFailed(run.failure_code, failure_note(run.failure_code, limits))
    verdict = run.verdict
    if not isinstance(verdict, dict):
        raise ExtractionFailed(PROTOCOL, failure_note(PROTOCOL, limits))
    status = verdict.get("status")
    if status == "done":
        if verdict.get("parser") != parser.name:
            raise ExtractionFailed(PROTOCOL, failure_note(PROTOCOL, limits))
        try:
            return decode_result(verdict.get("result"))
        except (TypeError, ValueError, KeyError) as error:
            raise ExtractionFailed(PROTOCOL, failure_note(PROTOCOL, limits)) from error
    if status in {"failed", "not_applicable"}:
        code = _text(verdict.get("code"), 64) or "parser_error"
        detail = _text(verdict.get("detail"), 1000)
        if status == "not_applicable":
            raise ExtractionNotApplicable(code, detail)
        if code in _BOUNDARY_CODES:
            detail = failure_note(code, limits)
        raise ExtractionFailed(code, detail)
    if status == "error":
        # The parser raised something unforeseen. Logged as the in-process path
        # logs it, with the exception's type and no content.
        raised = _text(verdict.get("type"), 80) or "Exception"
        logger.error("Parser %s crashed in its child (%s)", parser.name, raised)
        raise ExtractionFailed(
            "parser_error", f"Parser {parser.name} andis ootamatu vea ({raised})."
        )
    raise ExtractionFailed(PROTOCOL, failure_note(PROTOCOL, limits))


_BOUNDARY_CODES = frozenset(
    {TIMEOUT, CPU_LIMIT, MEMORY_LIMIT, FILE_LIMIT, OUTPUT_LIMIT, CRASHED, PROTOCOL}
)


def failure_note(code: str, limits: ChildLimits) -> str:
    """The sentence a person reads beside a file the boundary stopped."""
    megabytes = limits.memory_bytes // (1024 * 1024)
    return {
        TIMEOUT: (
            f"Faili lugemine katkestati, sest see kestis üle {limits.wall_seconds:g} sekundi. "
            + _KEEP
        ),
        CPU_LIMIT: (
            f"Faili lugemine katkestati, sest see kasutas üle {limits.cpu_seconds} sekundi "
            "protsessoriaega. " + _KEEP
        ),
        MEMORY_LIMIT: (f"Faili lugemiseks oleks kulunud üle {megabytes} MB mälu. " + _KEEP),
        FILE_LIMIT: "Faili lugemise ajutine fail ületas lubatud mahu. " + _KEEP,
        OUTPUT_LIMIT: "Failist loetud tulemus ületas lubatud mahu. " + _KEEP,
        CRASHED: "Faili lugeja lõpetas töö ootamatult. " + _KEEP,
        PROTOCOL: "Faili lugeja vastus oli vigane ja jäeti kõrvale. " + _KEEP,
    }.get(code, "Faili ei õnnestunud lugeda. " + _KEEP)


def _text(value: Any, limit: int) -> str:
    return value[:limit] if isinstance(value, str) else ""


# ---------------------------------------------------------------------------
# The wire format: JSON, validated, never code
# ---------------------------------------------------------------------------


def encode_result(result: ParseResult, *, text_only: bool) -> dict[str, Any]:
    """A ParseResult as plain JSON values. Bytes travel as base64."""
    derivatives = []
    for payload in result.derivatives:
        if text_only and payload.kind not in (
            DerivativeKind.EXTRACTED_TEXT,
            DerivativeKind.OCR_TEXT,
            DerivativeKind.EMAIL_METADATA,
        ):
            continue
        binary = None if text_only or payload.binary is None else _b64(payload.binary)
        derivatives.append(
            {
                "kind": str(payload.kind),
                "fragments": [
                    {
                        "text": fragment.text,
                        "locator_kind": str(fragment.locator_kind),
                        "locator": fragment.locator,
                        "locator_label": fragment.locator_label,
                        "text_source": str(fragment.text_source),
                    }
                    for fragment in payload.fragments
                ],
                "metadata": payload.metadata,
                "binary": binary,
                "binary_extension": payload.binary_extension,
                "binary_mime_type": payload.binary_mime_type,
            }
        )
    attachments = (
        []
        if text_only
        else [
            {
                "content": _b64(attachment.content),
                "filename": attachment.filename,
                "mime_type": attachment.mime_type,
                "content_id": attachment.content_id,
                "inline": attachment.inline,
            }
            for attachment in result.attachments
        ]
    )
    return {"derivatives": derivatives, "attachments": attachments, "note": result.note}


def decode_result(raw: Any) -> ParseResult:
    """Rebuild a ParseResult from untrusted JSON, refusing anything off-shape."""
    if not isinstance(raw, dict):
        raise ValueError("result is not an object")
    kinds = set(DerivativeKind.values)
    derivatives = []
    for item in _list(raw.get("derivatives")):
        if not isinstance(item, dict):
            raise ValueError("derivative is not an object")
        kind = _str(item.get("kind"))
        if kind not in kinds:
            raise ValueError("unknown derivative kind")
        fragments = []
        for fragment in _list(item.get("fragments")):
            if not isinstance(fragment, dict):
                raise ValueError("fragment is not an object")
            fragments.append(
                Fragment(
                    text=_str(fragment.get("text")),
                    locator_kind=_str(fragment.get("locator_kind")),
                    locator=_dict(fragment.get("locator")),
                    locator_label=_str(fragment.get("locator_label")),
                    text_source=_str(fragment.get("text_source")),
                )
            )
        binary = item.get("binary")
        derivatives.append(
            DerivativePayload(
                kind=kind,
                fragments=tuple(fragments),
                metadata=_dict(item.get("metadata")),
                binary=None if binary is None else _unb64(binary),
                binary_extension=_str(item.get("binary_extension")),
                binary_mime_type=_str(item.get("binary_mime_type")),
            )
        )
    attachments = []
    for item in _list(raw.get("attachments")):
        if not isinstance(item, dict):
            raise ValueError("attachment is not an object")
        inline = item.get("inline")
        if not isinstance(inline, bool):
            raise ValueError("attachment inline flag is not a boolean")
        attachments.append(
            ParsedAttachment(
                content=_unb64(item.get("content")),
                filename=_str(item.get("filename")),
                mime_type=_str(item.get("mime_type")),
                content_id=_str(item.get("content_id")),
                inline=inline,
            )
        )
    return ParseResult(
        derivatives=tuple(derivatives),
        attachments=tuple(attachments),
        note=_str(raw.get("note")),
    )


def _b64(content: bytes) -> str:
    return base64.b64encode(content).decode("ascii")


def _unb64(value: Any) -> bytes:
    if not isinstance(value, str):
        raise ValueError("binary is not base64 text")
    return base64.b64decode(value.encode("ascii"), validate=True)


def _str(value: Any) -> str:
    if not isinstance(value, str):
        raise ValueError("expected text")
    return value


def _list(value: Any) -> list[Any]:
    if not isinstance(value, list):
        raise ValueError("expected a list")
    return value


def _dict(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("expected an object")
    return value


# ---------------------------------------------------------------------------
# The child, started and stopped (Linux)
# ---------------------------------------------------------------------------


def child_environment(job: Path) -> dict[str, str]:
    """The allow-listed environment of a parser child. Nothing secret is in it."""
    import app

    environment = {name: os.environ[name] for name in _INHERITED_ENVIRONMENT if name in os.environ}
    environment.setdefault("PATH", "/usr/local/bin:/usr/bin:/bin")
    environment.update(
        {
            "HOME": str(job),
            "TMPDIR": str(job),
            # The source tree, and only so `-m` can find the child module.
            "PYTHONPATH": str(Path(app.__file__).resolve().parent.parent),
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONUNBUFFERED": "1",
            # One Tesseract thread: CPU seconds then mean wall seconds, and a
            # scan cannot multiply its footprint by the core count.
            "OMP_THREAD_LIMIT": "1",
        }
    )
    return environment


def _apply_limits(limits: ChildLimits) -> None:  # pragma: no cover - runs in the child
    """Between fork and exec: the limits the whole parser tree inherits."""
    if sys.platform == "win32":  # POSIX only; mypy checks the rest on Linux
        raise ParserUnavailable("parser isolation needs a POSIX host")
    import resource

    def cap(which: int, soft: int, hard: int | None = None) -> None:
        resource.setrlimit(which, (soft, soft if hard is None else hard))

    cap(resource.RLIMIT_AS, limits.memory_bytes)
    # Soft limit sends SIGXCPU, which Python turns into a clean exit the child
    # reports; the hard limit five seconds later is SIGKILL for one that
    # ignores it.
    cap(resource.RLIMIT_CPU, limits.cpu_seconds, limits.cpu_seconds + 5)
    cap(resource.RLIMIT_FSIZE, limits.file_bytes)
    cap(resource.RLIMIT_NOFILE, limits.open_files)
    cap(resource.RLIMIT_CORE, 0)
    # The first process the kernel's OOM killer should pick inside the
    # parser's cgroup is the parser, never the service that reports on it.
    with contextlib.suppress(OSError):
        Path("/proc/self/oom_score_adj").write_text("1000")


def _protect_parent() -> None:
    """Make this process's /proc entries (environ, mem) unreadable to its children.

    A child runs as the same user; without this, the environment it was
    deliberately not given would be one ``open('/proc/<ppid>/environ')`` away.
    ``PR_SET_DUMPABLE = 0`` makes those files root-owned and also refuses a
    same-user ``ptrace``. Linux only; elsewhere there is no subprocess mode.
    """
    if not sys.platform.startswith("linux"):
        return
    import ctypes

    libc = ctypes.CDLL(None, use_errno=True)
    pr_set_dumpable = 4
    if libc.prctl(pr_set_dumpable, 0, 0, 0, 0) != 0:  # pragma: no cover - defensive
        raise OSError(ctypes.get_errno(), "prctl(PR_SET_DUMPABLE) failed")


def run_child(
    *,
    header: dict[str, Any],
    payload: bytes,
    limits: ChildLimits,
    scratch_root: Path,
    protect_parent: bool = False,
) -> ChildRun:
    """Parse once in a fresh, limited child, and always leave nothing running.

    The child gets a private directory holding the request and the bytes, is
    the leader of its own session, and is killed — with every process it
    started — the moment it finishes, overruns its deadline, or prints more
    than ``limits.output_bytes``. The directory goes with it.
    """
    if sys.platform == "win32":  # POSIX only; mypy checks the rest on Linux
        raise ParserUnavailable("parser isolation needs a POSIX host")
    if protect_parent:
        _protect_parent()
    scratch_root.mkdir(parents=True, exist_ok=True)
    job = Path(tempfile.mkdtemp(prefix="parse-", dir=scratch_root))
    try:
        (job / "input.bin").write_bytes(payload)
        (job / "request.json").write_text(
            json.dumps({**header, "limits": limits.as_dict()}), encoding="utf-8"
        )
        os.chmod(job, 0o700)
        started = time.monotonic()
        try:
            process = subprocess.Popen(  # noqa: S603 - fixed argv, no shell
                # -P: the job directory is the working directory and must never
                # be on sys.path; the source tree comes from PYTHONPATH.
                [sys.executable, "-B", "-P", "-m", CHILD_MODULE, str(job)],
                cwd=job,
                env=child_environment(job),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                close_fds=True,
                start_new_session=True,
                preexec_fn=lambda: _apply_limits(limits),
            )
        except OSError as error:
            raise ParserUnavailable(f"could not start a parser child: {error}") from error
        reaped = _Reaped()
        try:
            output, overflow, timed_out = _collect(process, limits, started, reaped)
        finally:
            _kill_tree(process.pid)
            reaped.wait(process)
        rusage = reaped.rusage
        status = reaped.status
        usage = ChildUsage(
            seconds=time.monotonic() - started,
            cpu_seconds=rusage.ru_utime + rusage.ru_stime if rusage else 0.0,
            # Linux reports ru_maxrss in kilobytes.
            peak_rss_bytes=int(rusage.ru_maxrss * 1024) if rusage else 0,
            exit_status=_describe(status),
        )
        return _classify(output, overflow, timed_out, status, usage, limits)
    finally:
        shutil.rmtree(job, ignore_errors=True)


class _Reaped:
    """The child's exit status and rusage, collected once with ``wait4``.

    ``Popen.poll``/``wait`` would reap the child through ``waitpid`` and throw
    the resource usage away, and that usage is what the limits are sized from.
    """

    def __init__(self) -> None:
        self.status: int | None = None
        self.rusage: Any = None

    def poll(self, process: subprocess.Popen[bytes]) -> bool:
        if sys.platform == "win32":  # POSIX only; mypy checks the rest on Linux
            raise ParserUnavailable("parser isolation needs a POSIX host")
        if self.status is not None:
            return True
        pid, status, rusage = os.wait4(process.pid, os.WNOHANG)
        if pid == 0:
            return False
        self._record(process, status, rusage)
        return True

    def wait(self, process: subprocess.Popen[bytes]) -> None:
        if sys.platform == "win32":  # POSIX only; mypy checks the rest on Linux
            raise ParserUnavailable("parser isolation needs a POSIX host")
        if self.status is None:
            _, status, rusage = os.wait4(process.pid, 0)
            self._record(process, status, rusage)

    def _record(self, process: subprocess.Popen[bytes], status: int, rusage: Any) -> None:
        self.status = status
        self.rusage = rusage
        # Popen must not try to reap a pid that is already gone.
        process.returncode = os.waitstatus_to_exitcode(status)


def _collect(
    process: subprocess.Popen[bytes], limits: ChildLimits, started: float, reaped: _Reaped
) -> tuple[bytes, bool, bool]:
    """Read the child's output under the deadline and the output cap.

    stderr is drained and thrown away — a traceback can quote the document, and
    the log must never — so a child that writes there cannot block on a full
    pipe either.
    """
    import selectors

    if process.stdout is None or process.stderr is None:  # pragma: no cover - PIPE above
        raise ParserUnavailable("parser child has no output pipes")
    chunks: list[bytes] = []
    size = 0
    deadline = started + limits.wall_seconds
    selector = selectors.DefaultSelector()
    for stream in (process.stdout, process.stderr):
        os.set_blocking(stream.fileno(), False)
        selector.register(stream, selectors.EVENT_READ)
    try:
        while selector.get_map():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return b"".join(chunks), False, True
            for key, _ in selector.select(timeout=min(remaining, 0.25)):
                try:
                    data = os.read(key.fd, 65536)
                except BlockingIOError:
                    continue
                if not data:
                    selector.unregister(key.fileobj)
                    continue
                if key.fileobj is process.stdout:
                    size += len(data)
                    if size > limits.output_bytes:
                        return b"", True, False
                    chunks.append(data)
            # The child is done but something it started still holds a pipe:
            # nothing more is coming that this side wants.
            if reaped.poll(process) and not _readable(selector):
                break
        # Output closed; give the child the rest of the deadline to exit.
        while not reaped.poll(process):
            if time.monotonic() >= deadline:
                return b"".join(chunks), False, True
            time.sleep(0.02)
        return b"".join(chunks), False, False
    finally:
        selector.close()
        process.stdout.close()
        process.stderr.close()


def _readable(selector: Any) -> bool:
    return bool(selector.select(timeout=0))


def _kill_tree(pid: int) -> None:
    """SIGKILL the child's whole session. Harmless when it already ended."""
    if sys.platform == "win32":  # POSIX only; mypy checks the rest on Linux
        raise ParserUnavailable("parser isolation needs a POSIX host")
    with contextlib.suppress(ProcessLookupError, PermissionError):
        os.killpg(pid, signal.SIGKILL)


def _describe(status: int | None) -> str:
    if sys.platform == "win32":  # POSIX only; mypy checks the rest on Linux
        raise ParserUnavailable("parser isolation needs a POSIX host")
    if status is None:
        return ""
    if os.WIFSIGNALED(status):
        return f"signal {signal.Signals(os.WTERMSIG(status)).name}"
    if os.WIFEXITED(status):
        return f"exit {os.WEXITSTATUS(status)}"
    return "unknown"


def _classify(
    output: bytes,
    overflow: bool,
    timed_out: bool,
    status: int | None,
    usage: ChildUsage,
    limits: ChildLimits,
) -> ChildRun:
    if sys.platform == "win32":  # POSIX only; mypy checks the rest on Linux
        raise ParserUnavailable("parser isolation needs a POSIX host")
    if timed_out:
        return ChildRun(None, usage, TIMEOUT)
    if overflow:
        return ChildRun(None, usage, OUTPUT_LIMIT)
    verdict = None
    if output:
        try:
            parsed = json.loads(output)
        except (ValueError, RecursionError):
            parsed = None
        verdict = parsed if isinstance(parsed, dict) else None
    if verdict is not None:
        return ChildRun(verdict, usage)
    if status is not None and os.WIFSIGNALED(status):
        signalled = os.WTERMSIG(status)
        if signalled == signal.SIGXCPU or (
            signalled == signal.SIGKILL and usage.cpu_seconds >= limits.cpu_seconds
        ):
            return ChildRun(None, usage, CPU_LIMIT)
        if signalled == signal.SIGXFSZ:
            return ChildRun(None, usage, FILE_LIMIT)
        if signalled == signal.SIGKILL:
            # Nobody here sent it before the child ended: the kernel did, and
            # inside a memory-limited cgroup that is the OOM killer.
            return ChildRun(None, usage, MEMORY_LIMIT)
    return ChildRun(None, usage, CRASHED)


# ---------------------------------------------------------------------------
# The service's wire protocol
# ---------------------------------------------------------------------------

#: A request is: 4-byte header length, JSON header, 8-byte payload length,
#: payload. A response is: 8-byte length, JSON. Big-endian throughout.
_HEADER_LENGTH = struct.Struct(">I")
_BODY_LENGTH = struct.Struct(">Q")
MAX_HEADER_BYTES = 256 * 1024


def send_request(connection: socket.socket, header: dict[str, Any], payload: bytes) -> None:
    encoded = json.dumps(header).encode("utf-8")
    connection.sendall(_HEADER_LENGTH.pack(len(encoded)) + encoded)
    connection.sendall(_BODY_LENGTH.pack(len(payload)))
    connection.sendall(payload)


def receive_request(connection: socket.socket, *, max_payload: int) -> tuple[dict[str, Any], bytes]:
    (header_length,) = _HEADER_LENGTH.unpack(_receive(connection, _HEADER_LENGTH.size))
    if header_length > MAX_HEADER_BYTES:
        raise ValueError("request header too large")
    header = json.loads(_receive(connection, header_length))
    if not isinstance(header, dict):
        raise ValueError("request header is not an object")
    (payload_length,) = _BODY_LENGTH.unpack(_receive(connection, _BODY_LENGTH.size))
    if payload_length > max_payload:
        raise ValueError("request payload too large")
    return header, _receive(connection, payload_length)


def send_response(connection: socket.socket, response: dict[str, Any]) -> None:
    encoded = json.dumps(response, ensure_ascii=False).encode("utf-8")
    connection.sendall(_BODY_LENGTH.pack(len(encoded)) + encoded)


def receive_response(connection: socket.socket, *, max_bytes: int) -> dict[str, Any]:
    (length,) = _BODY_LENGTH.unpack(_receive(connection, _BODY_LENGTH.size))
    if length > max_bytes:
        raise ValueError("response too large")
    response = json.loads(_receive(connection, length))
    if not isinstance(response, dict):
        raise ValueError("response is not an object")
    return response


def _receive(connection: socket.socket, size: int) -> bytes:
    buffer = bytearray()
    while len(buffer) < size:
        chunk = connection.recv(min(size - len(buffer), 1024 * 1024))
        if not chunk:
            raise ConnectionError("peer closed the connection")
        buffer.extend(chunk)
    return bytes(buffer)


def _ask_service(header: dict[str, Any], payload: bytes, limits: ChildLimits) -> ChildRun:
    """One request to the parser service; ParserUnavailable if it cannot answer."""
    path = str(settings.DOCUMENT_PARSER_SOCKET or "")
    if not path:
        raise ParserUnavailable("DOCUMENT_PARSER_SOCKET is not set")
    family = getattr(socket, "AF_UNIX", None)
    if family is None:
        raise ParserUnavailable("Unix sockets are not available on this host")
    # The service handles one file at a time, so this may first wait for the
    # file in front; the queue allowance is that wait, and the rest is this
    # file's own deadline plus the time it takes to hand the answer back.
    budget = limits.wall_seconds + float(settings.DOCUMENT_PARSER_QUEUE_SECONDS) + 15.0
    try:
        with socket.socket(family, socket.SOCK_STREAM) as connection:
            connection.settimeout(budget)
            connection.connect(path)
            send_request(connection, header, payload)
            response = receive_response(
                connection, max_bytes=limits.output_bytes + MAX_HEADER_BYTES
            )
    except (OSError, ValueError) as error:
        raise ParserUnavailable(f"parser service did not answer: {type(error).__name__}") from error
    if response.get("kind") == "unavailable":
        raise ParserUnavailable("parser service could not start a child")
    raw_usage = response.get("usage")
    usage_raw: dict[str, Any] = raw_usage if isinstance(raw_usage, dict) else {}
    usage = ChildUsage(
        seconds=_number(usage_raw.get("seconds")),
        cpu_seconds=_number(usage_raw.get("cpu_seconds")),
        peak_rss_bytes=int(_number(usage_raw.get("peak_rss_bytes"))),
        exit_status=_text(usage_raw.get("exit_status"), 40),
    )
    failure = _text(response.get("failure_code"), 64)
    if failure and failure not in _BOUNDARY_CODES:
        failure = PROTOCOL
    verdict = response.get("verdict")
    return ChildRun(verdict if isinstance(verdict, dict) else None, usage, failure)


def _number(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return 0.0
    return float(value)


def run_response(run: ChildRun) -> dict[str, Any]:
    """What the service sends back for one finished child."""
    return {
        "kind": "result",
        "verdict": run.verdict,
        "failure_code": run.failure_code,
        "usage": {
            "seconds": run.usage.seconds,
            "cpu_seconds": run.usage.cpu_seconds,
            "peak_rss_bytes": run.usage.peak_rss_bytes,
            "exit_status": run.usage.exit_status,
        },
    }


__all__ = [
    "BATCH",
    "INPROCESS",
    "INTAKE",
    "SERVICE",
    "SUBPROCESS",
    "ChildLimits",
    "ChildRun",
    "ChildUsage",
    "ParserUnavailable",
    "decode_result",
    "encode_result",
    "interpret",
    "limits_for",
    "mode",
    "parse_isolated",
    "run_child",
]
