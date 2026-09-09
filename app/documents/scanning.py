"""The malware scan gate: one scanner, one vocabulary, two kinds of file.

Every file this system opens with a parser is supposed to have passed through
here first. That was already the *design* — `is_scan_state_extractable` has
refused anything but ``CLEAN`` under ``REAL_DATA_ALLOWED`` since docs/adr/0014 —
but nothing ever wrote ``CLEAN``, so on the real-data deployment the gate was
not a gate but a wall: a staged `Uus teema` file began ``PENDING``, no code path
could move it, and the person who chose it watched «Loen faili…» for as long as
they were willing to (docs/secure-pilot-gate.md row 6, docs/adr/0066).

**What this module is not allowed to be.** It would have been a great deal
easier to make reading work by letting ``PENDING`` through, or by stamping
``CLEAN`` on upload and calling the column satisfied. Both are refused here in
the strongest terms available: ``CLEAN`` in this system means *a scanner
examined these exact bytes and cleared them*, and a column that means anything
weaker is worse than no column at all, because every reader of it — the
extraction queue, the archive, the operator's dashboard — would go on believing
the strong claim.

So the transitions are the honest ones and there are only four:

    PENDING  --scanner says clean--------> CLEAN        may be parsed
    PENDING  --scanner finds a signature-> INFECTED     never parsed
    PENDING  --scanner errs on the file--> ERROR        never parsed
    PENDING  --scanner unreachable-------> PENDING      never parsed, retried

The last one is the one worth stating plainly: **an unavailable scanner leaves
the row exactly where it was.** It does not become ``SKIPPED``, it does not
become ``ERROR``, and it certainly does not become ``CLEAN``. A scanner that is
down is a scanner that has said nothing, and the file waits for it to say
something. That is what "fail closed" means when the failure is on our side.

**One meaning, two carriers.** A ``DocumentVersion`` and a ``MatterIntakeFile``
are different rows in different apps with different lifetimes, and they carry
the same ``MalwareScanState`` for the same reason they carry the same parsers:
a staged file is a canonical file that has not been promoted yet, and a second
interpretation of a scanner verdict is a second place for the two to disagree.
:func:`interpret` is that interpretation, it is written once, and both
:func:`scan_document_version` and :func:`scan_intake_file` reach it.

**The backend.** ClamAV, reached over its own TCP protocol (`clamd`), because
it is the scanner that ships as a reviewed container image, needs no licence and
no network egress, and speaks a protocol small enough to implement here rather
than adding a dependency to read a length-prefixed socket. The bytes are
streamed to it with ``INSTREAM`` from wherever they already live — evidence
storage or the staging volume — so the scanner container mounts nothing at all,
and a compromised scanner cannot reach a file nobody sent it.

Configuration is `MALWARE_SCANNER_BACKEND`. ``none`` is the development answer
and it scans nothing, which is exactly as safe as it sounds and no less safe
than today: with ``REAL_DATA_ALLOWED`` off, ``PENDING`` is already extractable
and always has been, because the data is invented. Combining ``none`` with real
data is refused at start-up by ``juristid.E015`` rather than left to be
discovered by a lawyer waiting at a form.
"""

from __future__ import annotations

import logging
import socket
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from app.documents.enums import MalwareScanState

logger = logging.getLogger(__name__)

#: Streamed to the scanner in pieces this size. ClamAV's own limit is on the
#: whole stream, not the chunk; this is only how much is held in memory at once.
CHUNK_BYTES = 64 * 1024


class ScannerUnavailable(RuntimeError):
    """The scanner could not be reached, or did not answer in time.

    Deliberately distinct from "the scanner examined this and disliked it". One
    is a fact about the file and is recorded on it; the other is a fact about
    our infrastructure and must never be.
    """


@dataclass(frozen=True)
class ScanVerdict:
    """What a scanner said about one set of bytes."""

    state: str
    #: The signature name, when there is one. Never the file's content.
    signature: str = ""
    seconds: float = 0.0

    @property
    def is_clean(self) -> bool:
        return self.state == MalwareScanState.CLEAN


# ---------------------------------------------------------------------------
# The backends
# ---------------------------------------------------------------------------


def interpret(response: str) -> ScanVerdict:
    """One clamd reply, as a ``MalwareScanState``.

    Written once and shared by both carriers, because "what does this reply
    mean" is the question a second implementation would eventually answer
    differently. clamd's stream replies are three shapes::

        stream: OK
        stream: Eicar-Test-Signature FOUND
        stream: <something> ERROR

    Anything else is unrecognised, and unrecognised is not clean. A parser that
    opens a file because the scanner said something we did not understand is the
    same defect as one that opens it because the scanner said nothing.
    """
    text = (response or "").strip().rstrip("\x00").strip()
    if text.endswith("FOUND"):
        # "stream: Eicar-Test-Signature FOUND" -> the middle.
        body = text.split(":", 1)[-1].strip()
        signature = body[: -len("FOUND")].strip() or "unknown"
        return ScanVerdict(state=MalwareScanState.INFECTED, signature=signature[:200])
    if text.endswith("ERROR"):
        # The scanner reached the bytes and refused them — a zip bomb, a
        # recursion limit, a stream over the configured maximum. That is a fact
        # about the file, so it is recorded on the file. It is *not* clean.
        return ScanVerdict(state=MalwareScanState.ERROR, signature=text[:200])
    if text.endswith("OK"):
        return ScanVerdict(state=MalwareScanState.CLEAN)
    raise ScannerUnavailable(f"Unrecognised scanner reply: {text[:120]!r}")


def _clamav_scan(content: bytes) -> ScanVerdict:
    """Stream these bytes to clamd and read its verdict.

    ``zINSTREAM`` rather than ``SCAN <path>``: the scanner then needs no view of
    our filesystem, so it mounts no volume, and the evidence tree stays visible
    to exactly the two containers that already have it.

    The wire format is a null-terminated command, then length-prefixed chunks,
    then a zero length to end the stream. Every number is network byte order.
    """
    host = settings.MALWARE_SCANNER_HOST
    port = settings.MALWARE_SCANNER_PORT
    timeout = settings.MALWARE_SCANNER_TIMEOUT_SECONDS

    started = time.monotonic()
    try:
        with socket.create_connection((host, port), timeout=timeout) as sock:
            sock.settimeout(timeout)
            sock.sendall(b"zINSTREAM\x00")
            for offset in range(0, len(content), CHUNK_BYTES):
                chunk = content[offset : offset + CHUNK_BYTES]
                sock.sendall(len(chunk).to_bytes(4, "big") + chunk)
            sock.sendall((0).to_bytes(4, "big"))

            reply = bytearray()
            while b"\x00" not in reply:
                received = sock.recv(4096)
                if not received:
                    break
                reply.extend(received)
                if len(reply) > 8192:  # pragma: no cover - a scanner gone mad
                    break
    except OSError as error:
        # Includes connection refused, DNS failure and the read timeout. All of
        # them mean the same thing to the caller: nothing was learned.
        raise ScannerUnavailable(f"clamd at {host}:{port} did not answer: {error}") from error

    verdict = interpret(reply.decode("utf-8", "replace"))
    return ScanVerdict(
        state=verdict.state,
        signature=verdict.signature,
        seconds=time.monotonic() - started,
    )


def _no_scanner(content: bytes) -> ScanVerdict:
    """The development answer: nothing is examined, so nothing is cleared.

    Raising rather than returning ``SKIPPED`` is the point. ``SKIPPED`` is a
    verdict, and a backend that examines nothing has none to give; leaving the
    row PENDING is what keeps a development environment's states honest about
    what has actually happened to a file.
    """
    raise ScannerUnavailable("No malware scanner is configured (MALWARE_SCANNER_BACKEND=none).")


BACKENDS: dict[str, Callable[[bytes], ScanVerdict]] = {
    "clamav": _clamav_scan,
    "none": _no_scanner,
}


def scanner_configured() -> bool:
    """Whether this process has a scanner that could clear anything."""
    return settings.MALWARE_SCANNER_BACKEND in BACKENDS and (
        settings.MALWARE_SCANNER_BACKEND != "none"
    )


def scan_bytes(content: bytes) -> ScanVerdict:
    """Ask the configured scanner about these exact bytes.

    Raises :class:`ScannerUnavailable` when no answer could be obtained. Every
    caller treats that as "not clean, ask again later" and none of them may
    treat it as anything else.
    """
    backend = BACKENDS.get(settings.MALWARE_SCANNER_BACKEND)
    if backend is None:
        raise ScannerUnavailable(
            f"Unknown MALWARE_SCANNER_BACKEND {settings.MALWARE_SCANNER_BACKEND!r}."
        )
    return backend(content)


def ping() -> bool:
    """Whether the scanner answers at all. For healthchecks and CI smoke."""
    if not scanner_configured():
        return False
    try:
        with socket.create_connection(
            (settings.MALWARE_SCANNER_HOST, settings.MALWARE_SCANNER_PORT),
            timeout=settings.MALWARE_SCANNER_TIMEOUT_SECONDS,
        ) as sock:
            sock.settimeout(settings.MALWARE_SCANNER_TIMEOUT_SECONDS)
            sock.sendall(b"zPING\x00")
            return b"PONG" in sock.recv(64)
    except OSError:
        return False


# ---------------------------------------------------------------------------
# The queues
# ---------------------------------------------------------------------------
#
# Two querysets rather than one generic helper over a model class. They differ
# in more than their model — a staged file has a session that can expire and an
# owner who may have taken it back off the form — and the shared part is the
# verdict, not the selection.


def pending_scan_versions() -> Any:
    """Canonical evidence waiting for a scanner, oldest first."""
    from app.documents.models import DocumentVersion

    return DocumentVersion.objects.filter(malware_scan_state=MalwareScanState.PENDING).order_by(
        "created_at"
    )


def pending_scan_intake_files() -> Any:
    """Staged `Uus teema` files waiting for a scanner, oldest first.

    The same three clauses the extraction queue carries, and for the same
    reason: a file taken off the form, a session a Matter has consumed and an
    expired session are all work nobody is waiting for, and scanning them would
    be the queue growing without bound in a different colour
    (`app.matters.intake_extraction.pending_intake_files`).
    """
    from app.matters.staging import MatterIntakeFile

    return MatterIntakeFile.objects.filter(
        malware_scan_state=MalwareScanState.PENDING,
        removed_at__isnull=True,
        session__consumed_at__isnull=True,
        session__expires_at__gt=timezone.now(),
    ).order_by("created_at")


# ---------------------------------------------------------------------------
# Scanning one row
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ScanReport:
    row_id: Any
    kind: str
    state: str
    seconds: float
    signature: str = ""
    #: The scanner said nothing. The row is untouched and will be offered again.
    unavailable: bool = False


def _settle(model: Any, row_id: Any, verdict: ScanVerdict) -> bool:
    """Record a verdict, but only on a row still waiting for one.

    Conditional on ``PENDING`` for the same reason every other write in this
    system is fenced: two workers may examine one file, and the second must not
    overwrite a verdict the first already recorded — least of all downgrade an
    ``INFECTED`` to a ``CLEAN`` because it happened to run against a signature
    database updated in between.
    """
    return bool(
        model.objects.filter(pk=row_id, malware_scan_state=MalwareScanState.PENDING).update(
            malware_scan_state=verdict.state, updated_at=timezone.now()
        )
    )


def scan_document_version(version: Any) -> ScanReport:
    """Scan one canonical binary and record what came back."""
    from app.documents.models import DocumentVersion
    from app.documents.services import evidence_storage

    def load() -> bytes:
        with evidence_storage().open(version.storage_key, "rb") as handle:
            return handle.read()

    return _scan_row(
        model=DocumentVersion,
        row_id=version.pk,
        kind="version",
        load=load,
        reference=f"document version {version.pk}",
    )


def scan_intake_file(staged: Any) -> ScanReport:
    """Scan one staged `Uus teema` file and record what came back."""
    from app.matters.intake_staging import staging_storage
    from app.matters.staging import MatterIntakeFile

    def load() -> bytes:
        with staging_storage().open(staged.storage_key, "rb") as handle:
            return handle.read()

    return _scan_row(
        model=MatterIntakeFile,
        row_id=staged.pk,
        kind="intake",
        load=load,
        reference=f"intake file {staged.pk}",
    )


def _scan_row(
    *, model: Any, row_id: Any, kind: str, load: Callable[[], bytes], reference: str
) -> ScanReport:
    started = time.monotonic()
    try:
        content = load()
    except OSError as error:
        # The bytes are gone. That is not a verdict about them either, so the
        # row stays PENDING: a staging object is cheap to lose and re-picking is
        # the recovery, while a missing canonical binary is a question for
        # `check_evidence_integrity` rather than for a scanner.
        logger.warning("could not read %s for scanning: %s", reference, error)
        return ScanReport(
            row_id=row_id,
            kind=kind,
            state=MalwareScanState.PENDING,
            seconds=time.monotonic() - started,
            unavailable=True,
        )

    try:
        verdict = scan_bytes(content)
    except ScannerUnavailable as error:
        # Left PENDING on purpose. See the module docstring: an unreachable
        # scanner has said nothing, and nothing is not a clearance.
        logger.warning("scanner unavailable for %s: %s", reference, error)
        return ScanReport(
            row_id=row_id,
            kind=kind,
            state=MalwareScanState.PENDING,
            seconds=time.monotonic() - started,
            unavailable=True,
        )

    with transaction.atomic():
        _settle(model, row_id, verdict)

    if verdict.state == MalwareScanState.INFECTED:
        # The signature, and never a byte of the file. An operator needs to know
        # which signature fired; a log carrying the document would be a second
        # copy of member material somewhere with none of evidence storage's
        # guarantees.
        logger.error("malware found in %s: %s", reference, verdict.signature)
    return ScanReport(
        row_id=row_id,
        kind=kind,
        state=verdict.state,
        seconds=time.monotonic() - started,
        signature=verdict.signature,
    )


def drain(*, limit: int = 25) -> list[ScanReport]:
    """Scan up to ``limit`` waiting files, staged ones first.

    Staged first for the reason the extraction worker reads staged files first:
    somebody is sitting in front of a form waiting for one of these, and an
    evidence backlog is a hundred files nobody is watching (docs/adr/0064).

    Stops the first time the scanner turns out to be unreachable. Trying the
    next twenty-four files against a scanner that is down produces twenty-four
    identical log lines and twenty-four connection timeouts, and leaves the
    queue in exactly the state it was in when the loop started.
    """
    reports: list[ScanReport] = []
    if not scanner_configured():
        return reports

    for staged in pending_scan_intake_files()[:limit]:
        report = scan_intake_file(staged)
        reports.append(report)
        if report.unavailable:
            return reports

    remaining = limit - len(reports)
    if remaining > 0:
        for version in pending_scan_versions()[:remaining]:
            report = scan_document_version(version)
            reports.append(report)
            if report.unavailable:
                return reports

    return reports


def awaiting_scan_counts() -> dict[str, int]:
    """How much is waiting, for the operator-facing commands."""
    return {
        "intake": pending_scan_intake_files().count(),
        "versions": pending_scan_versions().count(),
    }
