"""Reading a staged intake file: the whole universe of the intake reader.

**This module can only see one table.** ``MatterIntakeFile`` — the rows behind
somebody's open `Uus teema` form — and nothing else. It does not import
``DocumentVersion``, it holds no query that could reach one, and the worker
that calls it (`run_intake_reader`) imports nothing that could either. That is
not tidiness: on 2026-09-10 the canonical extraction backlog saturated the
production array's parity disk badly enough that a single-row INSERT into a
264 kB table blocked for two minutes, and the lesson taken from it is that a
reader serving a form somebody is sitting in front of must be structurally
incapable of joining a corpus-wide queue (docs/adr/0072).

Parsing itself is not reimplemented. `app.documents.extraction.orchestrator`'s
:func:`~app.documents.extraction.orchestrator.parse_source` is a pure function
of bytes that writes nothing anywhere, and it is what opens the file — so the
`Uus teema` reader and any deliberate corpus run agree about what a PDF says,
because they are the same parser (docs/adr/0014, docs/adr/0064).

What differs is only what happens to the answer. A ``DocumentVersion``
publishes derivative rows, text fragments, attachment Documents and a search
projection. A staged file publishes a little JSON on its own temporary row and
none of that — no derivative, no fragment table, no attachment Document, no
search row — because none of those things may exist before there is a Matter,
and since docs/adr/0072 none of them is created afterwards either.

The queue discipline is PostgreSQL's: ``SELECT … FOR UPDATE SKIP LOCKED``
makes a claim atomic, the claim is a timestamped row state so a worker that
dies leaves evidence rather than a lock, and the claim is re-asserted at the
moment of writing so a pass whose claim was reclaimed underneath it writes
nothing at all. No broker, no Redis, no Celery — the queue is at most a
handful of rows belonging to sessions that expire on their own.

**Attachments inside a staged message are deliberately not unpacked.** The
canonical path turns an `.eml`'s attachments into Documents of their own, and
there is nowhere to put them here — a Document needs a Matter. The message's
own headers and body are read, which is where the sender, the subject and the
sent time are; a person who wants the attachment filed adds it as a file of
its own. The alternative would be a second, weaker unpacking path.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from django.conf import settings
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from app.documents.enums import DerivativeKind, ExtractionState
from app.documents.extraction import parsers  # noqa: F401  (registers every parser)
from app.documents.extraction.orchestrator import parse_source
from app.matters.intake_staging import staging_storage
from app.matters.staging import MatterIntakeFile

logger = logging.getLogger(__name__)

#: Reported instead of an ``ExtractionState`` when a pass finds its claim gone.
#: The same distinction the canonical worker draws: it is not something the row
#: *is*, it is something that happened to this attempt.
CLAIM_LOST = "CLAIM_LOST"


class ClaimLost(RuntimeError):
    """This pass no longer owns the staged file, so it must not write."""


@dataclass(frozen=True)
class IntakeExtractionReport:
    file_id: Any
    state: str
    fragments: int
    seconds: float
    note: str = ""
    error_code: str = ""


def pending_intake_files() -> Any:
    """Staged files the reader may pick up, oldest first. Its whole universe.

    Three clauses beyond the claim state, and each of them is a reason this
    queue cannot grow: a file the person took off the form is not read, a
    session a Matter has already consumed is not read, and an expired session
    is not read. Work nobody is waiting for is work nobody should be doing —
    so with no open `Uus teema` form anywhere in the department this returns
    nothing and the reader idles.

    The bound is therefore the product's rather than a number somebody chose:
    at most `app.matters.intake.MAX_INTAKE_FILES` per session, sessions expire
    on their own, and `prune_intake_staging` sweeps what expiry leaves. A
    backlog of the kind a corpus queue accumulates cannot form here.

    A stale `PROCESSING` claim comes back after
    `INTAKE_READER_STALE_CLAIM_MINUTES` rather than the corpus worker's
    `EXTRACTION_STALE_CLAIM_MINUTES`, and the difference is the work: a corpus
    parse may be a 500-page OCR run and needs half an hour of grace, while a
    staged file is bounded by `MAX_INTAKE_UPLOAD_BYTES` and finishes in
    seconds. Thirty minutes here would strand somebody's form behind a reader
    that died, for longer than they would keep the page open.
    """
    stale_before = _stale_before()
    return (
        MatterIntakeFile.objects.filter(
            Q(extraction_state=ExtractionState.PENDING)
            | Q(
                extraction_state=ExtractionState.PROCESSING,
                extraction_claimed_at__lt=stale_before,
            )
            | Q(extraction_state=ExtractionState.PROCESSING, extraction_claimed_at__isnull=True)
        )
        .filter(
            removed_at__isnull=True,
            session__consumed_at__isnull=True,
            session__expires_at__gt=timezone.now(),
        )
        .order_by("created_at")
    )


def _stale_before() -> Any:
    """The moment before which a `PROCESSING` claim belongs to a dead reader.

    One expression, read by the queue and by the claim, because a queue that
    offered a row the claim then refused would hand back the same row for ever
    — the hot loop the first real-data deployment ran at full speed over 16 440
    attachments.
    """
    return timezone.now() - timedelta(minutes=settings.INTAKE_READER_STALE_CLAIM_MINUTES)


def claim_intake_file(file_id: Any, *, force: bool = False) -> MatterIntakeFile | None:
    """Take one staged file for processing, or return None if somebody has it."""
    stale_before = _stale_before()
    with transaction.atomic():
        locked = (
            MatterIntakeFile.objects.select_for_update(skip_locked=True).filter(pk=file_id).first()
        )
        if locked is None:
            return None
        if not force and not _is_claimable(locked, stale_before):
            return None
        locked.extraction_state = ExtractionState.PROCESSING
        locked.extraction_claimed_at = timezone.now()
        locked.save(update_fields=["extraction_state", "extraction_claimed_at", "updated_at"])
    return locked


def _is_claimable(staged: MatterIntakeFile, stale_before: Any) -> bool:
    if staged.extraction_state == ExtractionState.PENDING:
        return True
    if staged.extraction_state != ExtractionState.PROCESSING:
        return False
    claimed = staged.extraction_claimed_at
    return claimed is None or claimed < stale_before


def extract_intake_file(staged: MatterIntakeFile) -> IntakeExtractionReport:
    """Read one staged file and record what came out of it.

    Assumes the caller has claimed the row. No exit path leaves it PROCESSING
    for this pass: it ends DONE, FAILED or NOT_APPLICABLE, all three terminal.
    A file the reader cannot understand is one of the last two, and the form
    stays usable either way — `Loo teema` never waits on this
    (docs/adr/0072 §Failure).
    """
    started = time.monotonic()
    fence = staged.extraction_claimed_at

    outcome = parse_source(
        filename=staged.original_filename,
        mime_type=staged.mime_type,
        load=lambda: _read_staged(staged),
        reference=f"intake file {staged.pk}",
    )

    fragments: list[dict[str, Any]] = []
    email_metadata: dict[str, Any] | None = None
    characters = 0
    if outcome.result is not None:
        for payload in outcome.result.derivatives:
            if payload.kind == DerivativeKind.EMAIL_METADATA:
                email_metadata = dict(payload.metadata or {})
                continue
            if payload.kind not in (DerivativeKind.EXTRACTED_TEXT, DerivativeKind.OCR_TEXT):
                # A thumbnail or a safe preview is a picture, and a staged file
                # has no page to show it on. Skipped rather than stored: bytes
                # nothing renders are bytes nobody sweeps.
                continue
            for fragment in payload.fragments:
                text = fragment.text or ""
                if not text:
                    continue
                characters += len(text)
                fragments.append(
                    {
                        "text": text,
                        "locator_kind": fragment.locator_kind,
                        "locator": fragment.locator,
                        "locator_label": fragment.locator_label[:200],
                        "text_source": fragment.text_source,
                    }
                )

    try:
        _settle(
            staged,
            state=outcome.state,
            note=outcome.note,
            fence=fence,
            fragments=fragments,
            email_metadata=email_metadata,
            characters=characters,
        )
    except ClaimLost:
        logger.warning(
            "intake extraction claim lost file=%s; another worker owns the row now", staged.pk
        )
        return IntakeExtractionReport(
            file_id=staged.pk,
            state=CLAIM_LOST,
            fragments=0,
            seconds=time.monotonic() - started,
            note="Töötlemisõigus läks üle teisele töötajale; midagi ei kirjutatud.",
            error_code="claim_lost",
        )

    if outcome.error_code:
        logger.warning(
            "intake extraction failed file=%s mime=%s code=%s",
            staged.pk,
            staged.mime_type,
            outcome.error_code,
        )
    return IntakeExtractionReport(
        file_id=staged.pk,
        state=outcome.state,
        fragments=len(fragments),
        seconds=time.monotonic() - started,
        note=outcome.note,
        error_code=outcome.error_code,
    )


def _read_staged(staged: MatterIntakeFile) -> bytes:
    storage = staging_storage()
    with storage.open(staged.storage_key, "rb") as handle:
        return handle.read()


@transaction.atomic
def _settle(
    staged: MatterIntakeFile,
    *,
    state: str,
    note: str,
    fence: Any,
    fragments: list[dict[str, Any]],
    email_metadata: dict[str, Any] | None,
    characters: int,
) -> None:
    """Write the outcome, but only while this pass still holds the claim.

    One conditional UPDATE, with ``extraction_claimed_at`` as both the fence
    token and the field being cleared — so matching on it is the same statement
    that releases it, and there is no window between checking and writing.
    """
    updated = MatterIntakeFile.objects.filter(pk=staged.pk, extraction_claimed_at=fence).update(
        extraction_state=state,
        extraction_claimed_at=None,
        extraction_note=note[:300],
        text=fragments,
        text_character_count=characters,
        email_metadata=email_metadata,
        updated_at=timezone.now(),
    )
    if not updated:
        raise ClaimLost(f"Intake file {staged.pk} is no longer claimed by this pass.")
    staged.extraction_state = state
    staged.extraction_claimed_at = None
    staged.extraction_note = note[:300]
    staged.text = fragments
    staged.text_character_count = characters
    staged.email_metadata = email_metadata


def drain(*, limit: int = 25) -> list[IntakeExtractionReport]:
    """Process up to ``limit`` staged files and stop. What the worker loop calls."""
    reports: list[IntakeExtractionReport] = []
    # Bounded by attempts, not only by results. A row another worker is holding
    # is skipped by `select_for_update(skip_locked=True)` and stays at the front
    # of the queue, so counting only successes would spin on it for as long as
    # the other worker took.
    attempts = 0
    while len(reports) < limit and attempts < limit * 2:
        attempts += 1
        candidate = pending_intake_files().first()
        if candidate is None:
            break
        claimed = claim_intake_file(candidate.pk)
        if claimed is None:
            continue
        reports.append(extract_intake_file(claimed))
    return reports
