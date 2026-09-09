"""Reading a staged intake file: the same parsers, a different publication target.

The security boundary this feature moves earlier is the one thing it may not
weaken. A file chosen on `Uus teema` is not opened in the request that
uploaded it, is not opened by a second parser written for the occasion, and is
not opened at all until it has passed the scan gate — because everything that
decides those three things is `app.documents.extraction.orchestrator`'s
:func:`~app.documents.extraction.orchestrator.parse_source`, which this module
calls and does not reimplement (docs/adr/0014, docs/adr/0064).

What differs is only what happens to the answer. A ``DocumentVersion``
publishes derivative rows, text fragments, attachment Documents and a search
projection. A staged file publishes a little JSON on its own temporary row and
none of that — no derivative, no fragment table, no attachment Document, no
search row — because none of those things may exist before there is a Matter.

The queue is the same shape as the canonical one, and for the same reasons:
PostgreSQL is the broker, ``SELECT … FOR UPDATE SKIP LOCKED`` makes a claim
atomic, the claim is a timestamped row state so a worker that dies leaves
evidence rather than a lock, and the claim is re-asserted at the moment of
writing so a pass whose claim was reclaimed underneath it writes nothing at
all (`run_extraction_worker`).

**Attachments inside a staged message are deliberately not unpacked.** The
canonical path turns an `.eml`'s attachments into Documents of their own, and
there is nowhere to put them here — a Document needs a Matter. The message's
own headers and body are read, which is where the sender, the subject and the
sent time are; the attachments arrive as Documents the moment `Loo teema`
promotes the message and the ordinary worker reads it properly. Nothing is
lost, and the alternative would be a second, weaker unpacking path.
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

from app.documents.enums import DerivativeKind, ExtractionState, MalwareScanState
from app.documents.extraction import parsers  # noqa: F401  (registers every parser)
from app.documents.extraction.orchestrator import is_scan_state_extractable, parse_source
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


def eligibility_q() -> Q:
    """:func:`is_scan_state_extractable`, as SQL.

    The same rule in two places, and it earns it here for the same reason it
    does on the canonical queue: the queue must exclude what the worker would
    refuse, or a file waiting on a scanner is offered, declined and offered
    again in a hot loop for ever. A test holds the two against each other.
    """
    eligible = Q(malware_scan_state=MalwareScanState.CLEAN)
    if not settings.REAL_DATA_ALLOWED:
        eligible |= Q(malware_scan_state=MalwareScanState.PENDING)
    return eligible


def pending_intake_files() -> Any:
    """Staged files a worker may pick up, oldest first.

    Three clauses beyond the canonical queue's, and each of them is the reason
    this queue cannot grow without bound: a file the person took off the form
    is not read, a session a Matter has already consumed is not read, and an
    expired session is not read. Work nobody is waiting for is work nobody
    should be doing.
    """
    stale_before = timezone.now() - timedelta(minutes=settings.EXTRACTION_STALE_CLAIM_MINUTES)
    return (
        MatterIntakeFile.objects.filter(
            Q(extraction_state=ExtractionState.PENDING)
            | Q(
                extraction_state=ExtractionState.PROCESSING,
                extraction_claimed_at__lt=stale_before,
            )
            | Q(extraction_state=ExtractionState.PROCESSING, extraction_claimed_at__isnull=True)
        )
        .filter(eligibility_q())
        .filter(
            removed_at__isnull=True,
            session__consumed_at__isnull=True,
            session__expires_at__gt=timezone.now(),
        )
        .order_by("created_at")
    )


def claim_intake_file(file_id: Any, *, force: bool = False) -> MatterIntakeFile | None:
    """Take one staged file for processing, or return None if somebody has it."""
    stale_before = timezone.now() - timedelta(minutes=settings.EXTRACTION_STALE_CLAIM_MINUTES)
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
    for this pass: it ends DONE, FAILED, NOT_APPLICABLE, or back at PENDING
    when the file is waiting on a scanner that has not run.
    """
    started = time.monotonic()
    fence = staged.extraction_claimed_at

    outcome = parse_source(
        filename=staged.original_filename,
        mime_type=staged.mime_type,
        scan_state=staged.malware_scan_state,
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


def is_extractable(staged: MatterIntakeFile) -> bool:
    """Whether this staged file may be opened by a parser at all."""
    return is_scan_state_extractable(staged.malware_scan_state)
