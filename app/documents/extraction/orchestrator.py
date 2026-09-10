"""Where a parse becomes committed state.

Everything that writes is here, and nothing that parses is. That split is the
point: a parser is a pure function of bytes, so "the parser raised halfway" has
exactly one meaning — nothing was written — and there is one place to reason
about what a partial success would look like, rather than nine.

The order of operations, and why each step is where it is:

1. **Claim** the version. One `UPDATE ... WHERE extraction_state = PENDING`
   under a row lock, so two workers cannot both take it.
2. **Read and parse**, outside any transaction. OCR on a 200-page scan takes
   minutes; holding a database transaction open for that would idle a
   connection and block nothing useful.
3. **Publish**, in one transaction. New derivatives are written as BUILDING,
   the previous ACTIVE ones are demoted, the new ones are promoted, and the
   version's state moves to DONE — all or none of it.

Step 3 is why a parser upgrade cannot empty somebody's search results. The old
representation keeps serving until the new one is complete, and a failure at any
point leaves the old one exactly where it was (Stage-2B brief 8, 10).

**The claim is a fence, not a lock.** Step 2 runs outside any transaction and
takes as long as the file takes, so nothing stops a parse from outliving
``EXTRACTION_STALE_CLAIM_MINUTES`` and having its claim reclaimed by a second
worker that is behaving perfectly correctly. The claim timestamp is therefore
carried through the run and re-asserted at the moment of writing: every terminal
write is conditional on the claim still being the one this pass took. A pass
that lost its claim writes nothing at all — no derivative, no attachment
Document, no state — instead of racing the worker that now owns the row and
leaving the two of them to disagree about whether the file succeeded.
"""

from __future__ import annotations

import hashlib
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from django.conf import settings
from django.core.files.base import ContentFile
from django.core.files.storage import storages
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from app.documents.enums import (
    DerivativeKind,
    DerivativeStatus,
    ExtractionState,
)
from app.documents.extraction import parsers  # noqa: F401  (registers every parser)
from app.documents.extraction.base import DerivativePayload, ParseResult, SourceFile, registry
from app.documents.extraction.errors import ExtractionFailed, ExtractionNotApplicable
from app.documents.models import (
    DocumentDerivative,
    DocumentTextFragment,
    DocumentVersion,
)
from app.documents.services import evidence_storage

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ExtractionReport:
    version_id: Any
    state: str
    derivatives: int
    fragments: int
    attachments: int
    seconds: float
    note: str = ""
    error_code: str = ""


#: Reported instead of an ``ExtractionState`` when a pass finds its claim gone.
#: Deliberately not a member of that enum: it is not something the row *is*, it
#: is something that happened to this attempt. The row's own state belongs to
#: whoever holds the claim now.
CLAIM_LOST = "CLAIM_LOST"


class ClaimLost(RuntimeError):
    """This pass no longer owns the version, so it must not write.

    Raised by :func:`_settle` when the conditional update matches no row, which
    means the claim this pass took has been replaced — by a reclaim after the
    stale window, or by an operator's ``--force``. Raised rather than returned
    so it unwinds the publish transaction with it: the derivatives and the
    attachment Documents of a pass that lost its claim must not survive either.
    """


def derivative_storage() -> Any:
    return storages[settings.DERIVATIVE_STORAGE_ALIAS]


def claim_version(version_id: Any, *, force: bool = False) -> DocumentVersion | None:
    """Take a version for processing, or return None if somebody else has it.

    The lock is held for one statement. ``skip_locked`` means a second worker
    moves on to the next row instead of queueing behind this one, which is what
    makes running two workers useful rather than merely safe.

    The stamped ``extraction_claimed_at`` is this pass's fence token. It is read
    back off the returned object by :func:`extract_document_version` and quoted
    at every write, so a claim that gets reclaimed while the parse is still
    running costs the wasted work and nothing else.
    """
    stale_before = timezone.now() - timedelta(minutes=settings.EXTRACTION_STALE_CLAIM_MINUTES)
    with transaction.atomic():
        locked = (
            DocumentVersion.objects.select_for_update(skip_locked=True)
            .filter(pk=version_id)
            .first()
        )
        if locked is None:
            return None
        if not force and not _is_claimable(locked, stale_before):
            return None
        locked.extraction_state = ExtractionState.PROCESSING
        locked.extraction_claimed_at = timezone.now()
        locked.save(update_fields=["extraction_state", "extraction_claimed_at", "updated_at"])
    return locked


def _is_claimable(version: DocumentVersion, stale_before: Any) -> bool:
    if version.extraction_state == ExtractionState.PENDING:
        return True
    if version.extraction_state != ExtractionState.PROCESSING:
        return False
    # A PROCESSING row whose claim is old belongs to a worker that is not coming
    # back. The only honest way to tell a long parse from a dead one is elapsed
    # time, which is why the timeout is generous.
    claimed = version.extraction_claimed_at
    return claimed is None or claimed < stale_before


def pending_versions() -> Any:
    """Versions a worker may pick up, oldest first.

    Includes stale PROCESSING claims, so a killed worker's queue drains without
    anyone running a recovery command.

    **`INTAKE_READ` is not here, and that is the point of it.** A binary
    promoted out of `Uus teema` was already read while the form was open, so
    offering it again would be the corpus re-doing work an operator has already
    had the answer to — which is what this system did until docs/adr/0072.
    Nothing about that row is pending: it is finished, and it says so in a word
    that cannot be confused with `DONE` (`app.matters.intake_staging`).

    **There is no scan gate in front of this any more.** It used to exclude
    anything a scanner had not cleared, and the scanner is gone with the
    subsystem it belonged to (docs/adr/0066 §Superseded, docs/adr/0072). What
    it filtered on — `malware_scan_state` — is a column nothing writes and
    nothing reads.
    """
    stale_before = timezone.now() - timedelta(minutes=settings.EXTRACTION_STALE_CLAIM_MINUTES)
    return (
        DocumentVersion.objects.filter(
            Q(extraction_state=ExtractionState.PENDING)
            | Q(
                extraction_state=ExtractionState.PROCESSING,
                extraction_claimed_at__lt=stale_before,
            )
            | Q(extraction_state=ExtractionState.PROCESSING, extraction_claimed_at__isnull=True)
        )
        .select_related("document", "document__matter")
        .order_by("created_at")
    )


def extract_document_version(version: DocumentVersion) -> ExtractionReport:
    """Parse one binary and publish what came out of it.

    Assumes the caller has claimed the row. No exit path leaves the row
    PROCESSING for this pass — it ends DONE, FAILED or NOT_APPLICABLE, all
    three of them terminal. There used to be a fourth, non-terminal exit back
    to PENDING for a file no scanner had cleared; the gate that produced it is
    gone (docs/adr/0072).

    The one exit that changes nothing is a lost claim. The row is then somebody
    else's to finish and this pass reports what happened without touching it.
    """
    started = time.monotonic()
    # Read before any parsing, because the parse is what takes long enough for
    # the claim to be reclaimed underneath it.
    fence = version.extraction_claimed_at
    try:
        return _run(version, fence=fence, started=started)
    except ClaimLost:
        logger.warning(
            "extraction claim lost version=%s; another worker owns the row now",
            version.pk,
        )
        return ExtractionReport(
            version_id=version.pk,
            state=CLAIM_LOST,
            derivatives=0,
            fragments=0,
            attachments=0,
            seconds=time.monotonic() - started,
            note="Töötlemisõigus läks üle teisele töötajale; midagi ei kirjutatud.",
            error_code="claim_lost",
        )


@dataclass(frozen=True)
class ParseOutcome:
    """What one pass over a file’s bytes decided, before anything is written.

    The pure half of extraction: the parser lookup, the parse itself, and the
    name every failure is given. It carries a terminal
    ``ExtractionState`` and the parser’s own output, and it commits nothing.

    That separation is what lets one parser stack serve two publication
    targets. A canonical ``DocumentVersion`` publishes derivative rows, text
    fragments, attachment Documents and a search projection; a file staged on
    ``Uus teema`` before the Matter exists publishes a little JSON on its own
    temporary row and none of that (docs/adr/0064). What must never differ is
    the gate the bytes passed to get here, so it is written once.
    """

    state: str
    note: str = ""
    error_code: str = ""
    parser: Any | None = None
    result: ParseResult | None = None

    @property
    def failed(self) -> bool:
        return self.state == ExtractionState.FAILED


def parse_source(
    *,
    filename: str,
    mime_type: str,
    load: Callable[[], bytes],
    reference: str = "",
) -> ParseOutcome:
    """Read one file and say what came out of it. Writes nothing, anywhere.

    ``load`` is a callable rather than the bytes themselves, and deliberately:
    a format no parser claims is decided before a single byte is fetched, so an
    unreadable file costs no storage read it would never have used.
    ``FileNotFoundError`` from it is the one storage failure with a name of its
    own — the row says where the bytes are and they are not there — and it is
    reported as a verdict on the file rather than raised at the caller.

    ``reference`` names the row in the one log line that mentions one. No
    document content is ever logged (Stage-2B brief 67).

    **There is no scan state parameter any longer.** This function used to
    refuse anything a scanner had not cleared, and the whole subsystem behind
    that refusal is gone (docs/adr/0072). What remains — the size limit, the
    extension allowlist and the content-signature check — happens where it
    always did, at upload, before any of these bytes are stored
    (`app.documents.uploads.read_upload`).
    """
    parser = registry.for_mime_type(mime_type)
    if parser is None:
        return ParseOutcome(state=ExtractionState.NOT_APPLICABLE, note=_unsupported_note(mime_type))

    try:
        content = load()
    except FileNotFoundError:
        # Deliberately without ``parser``: it never ran, and recording its name
        # as the generator of this failure would say that it had.
        return ParseOutcome(
            state=ExtractionState.FAILED,
            note="Tõendi baite ei leitud hoidlast.",
            error_code="evidence_missing",
        )

    source = SourceFile(content=content, filename=filename, mime_type=mime_type)
    try:
        result = parser.parse(source)
    except ExtractionNotApplicable as error:
        return ParseOutcome(state=ExtractionState.NOT_APPLICABLE, note=error.detail, parser=parser)
    except ExtractionFailed as error:
        return ParseOutcome(
            state=ExtractionState.FAILED,
            note=error.detail,
            error_code=error.code,
            parser=parser,
        )
    except Exception as error:
        # A parser that raises something unforeseen is a bug, not a valid file
        # verdict. It is logged with the row reference and *no content*, the
        # file is marked failed, and the loop continues: one malformed file
        # must never stop the queue (Stage-2B brief 67).
        logger.exception("Parser %s crashed on %s", parser.name, reference or filename)
        return ParseOutcome(
            state=ExtractionState.FAILED,
            note=f"Parser {parser.name} andis ootamatu vea ({type(error).__name__}).",
            error_code="parser_error",
            parser=parser,
        )

    return ParseOutcome(state=ExtractionState.DONE, note=result.note, parser=parser, result=result)


def _run(version: DocumentVersion, *, fence: Any, started: float) -> ExtractionReport:
    outcome = parse_source(
        filename=version.original_filename,
        mime_type=version.mime_type,
        load=lambda: _read_evidence(version),
        reference=f"version {version.pk}",
    )

    if outcome.failed:
        return _record_failure(
            version,
            code=outcome.error_code,
            detail=outcome.note,
            parser=outcome.parser,
            started=started,
            fence=fence,
        )

    if outcome.result is None:
        # NOT_APPLICABLE: no parser claims the format, or the one that does
        # declined it. Nothing is written.
        return _finish_without_derivatives(
            version,
            state=outcome.state,
            note=outcome.note,
            started=started,
            fence=fence,
        )

    try:
        return _publish(
            version, parser=outcome.parser, result=outcome.result, started=started, fence=fence
        )
    except ClaimLost:
        # Not a failure of this file. Somebody else owns the row and will write
        # its outcome; the publish transaction has already rolled back, so this
        # pass leaves nothing behind but the wasted work.
        raise
    except Exception as error:
        # Publishing failed after the parse succeeded — a full disk, a
        # read-only mount, a database that went away. The transaction rolled
        # back, so nothing is half-written, but the row would otherwise sit in
        # PROCESSING until the stale timeout and then fail exactly the same way,
        # for ever, invisibly.
        #
        # Recorded as a failure so it surfaces, and left needing --force to
        # retry: a publish failure is an environment problem, and a queue that
        # silently retries one is a queue that hides it (Stage-2B brief 10).
        logger.exception("Publishing extraction for version %s failed", version.pk)
        return _record_failure(
            version,
            code="publish_failed",
            detail=f"Tulemuse salvestamine ebaõnnestus ({type(error).__name__}).",
            parser=outcome.parser,
            started=started,
            fence=fence,
        )


def _read_evidence(version: DocumentVersion) -> bytes:
    storage = evidence_storage()
    with storage.open(version.storage_key, "rb") as handle:
        return handle.read()


def _unsupported_note(mime_type: str) -> str:
    return {
        "application/zip": (
            "ZIP-arhiiv säilitatakse originaalina. Sisu ei pakita automaatselt lahti."
        ),
        "application/msword": (
            "Vana Wordi vorming. Sisu ei eraldata; originaal on alles ja avatav."
        ),
        "application/vnd.ms-excel": (
            "Vana Exceli vorming. Sisu ei eraldata; originaal on alles ja avatav."
        ),
    }.get(mime_type, f"Vormingu {mime_type} sisu ei eraldata.")


def _settle(version: DocumentVersion, *, state: str, note: str, fence: Any) -> None:
    """Write the terminal state, but only while this pass still holds the claim.

    One conditional UPDATE. ``extraction_claimed_at`` is both the fence token
    and the field being cleared, so matching on it is the same statement that
    releases it — there is no window between checking and writing for a third
    party to slip into.

    A fence of ``None`` is not a special case. It means this pass ran on a row
    nobody had claimed, and the condition then reads "nobody has claimed it
    since", which is exactly the right question.
    """
    detail = note[:300]
    updated = DocumentVersion.objects.filter(pk=version.pk, extraction_claimed_at=fence).update(
        extraction_state=state,
        extraction_claimed_at=None,
        extraction_note=detail,
        # `update()` bypasses `auto_now`, and a row whose state moved without
        # its timestamp moving is a row that lies to every operator query.
        updated_at=timezone.now(),
    )
    if not updated:
        raise ClaimLost(f"Version {version.pk} is no longer claimed by this pass.")
    version.extraction_state = state
    version.extraction_claimed_at = None
    version.extraction_note = detail


@transaction.atomic
def _finish_without_derivatives(
    version: DocumentVersion, *, state: str, note: str, started: float, fence: Any
) -> ExtractionReport:
    _settle(version, state=state, note=note, fence=fence)
    return ExtractionReport(
        version_id=version.pk,
        state=state,
        derivatives=0,
        fragments=0,
        attachments=0,
        seconds=time.monotonic() - started,
        note=note,
    )


@transaction.atomic
def _record_failure(
    version: DocumentVersion,
    *,
    code: str,
    detail: str,
    started: float,
    fence: Any,
    parser: Any = None,
) -> ExtractionReport:
    """Mark the version failed, keeping whatever already worked.

    Existing ACTIVE derivatives are deliberately left alone. A parser upgrade
    that fails on a file it used to handle must not take that file's search
    representation with it — degraded is recoverable, empty is not
    (Stage-2B brief 8).

    The claim is re-asserted first, before the FAILED derivative row is written.
    A pass that lost its claim must not leave an operator-facing failure on a
    file another worker is in the middle of extracting successfully.
    """
    _settle(version, state=ExtractionState.FAILED, note=detail, fence=fence)
    DocumentDerivative.objects.create(
        version=version,
        kind=DerivativeKind.EXTRACTED_TEXT,
        generator=getattr(parser, "name", "orchestrator"),
        generator_version=getattr(parser, "version", ""),
        status=DerivativeStatus.FAILED,
        error_code=code,
        error_detail=detail[:2000],
        built_at=timezone.now(),
    )
    logger.warning(
        "extraction failed version=%s parser=%s mime=%s code=%s",
        version.pk,
        getattr(parser, "name", "-"),
        version.mime_type,
        code,
    )
    return ExtractionReport(
        version_id=version.pk,
        state=ExtractionState.FAILED,
        derivatives=0,
        fragments=0,
        attachments=0,
        seconds=time.monotonic() - started,
        note=detail,
        error_code=code,
    )


def _publish(
    version: DocumentVersion, *, parser: Any, result: ParseResult, started: float, fence: Any
) -> ExtractionReport:
    """Write the parse, swap it in, and reindex — all in one transaction.

    The claim is re-asserted inside that transaction, so losing it takes the
    derivatives, the attachment Documents and the search rows down with it
    rather than publishing a second opinion over a row somebody else owns.
    """
    from app.documents.email_intake import register_email_attachments

    fragment_total = 0
    attachment_total = 0

    with transaction.atomic():
        # First, not last. Two things follow from re-asserting the claim before
        # any of the writing rather than after it. A pass that already lost its
        # claim does none of the work — which matters because
        # `register_email_attachments` writes evidence bytes to storage, and
        # bytes written inside a transaction that rolls back are orphans the
        # pruner has to find later. And the conditional UPDATE takes the row
        # lock, so a genuinely concurrent second publish queues on it and then
        # finds its own fence gone, instead of the two of them interleaving.
        _settle(version, state=ExtractionState.DONE, note=result.note, fence=fence)

        for payload in result.derivatives:
            fragment_total += _write_derivative(version, parser=parser, payload=payload)

        if result.attachments:
            attachment_total = register_email_attachments(
                parent_version=version, attachments=result.attachments
            )

        # Inside the transaction on purpose. A committed derivative with no
        # search row is a document whose content exists and cannot be found,
        # which is the silent half of every search complaint.
        from app.search.indexing import refresh_document_version

        refresh_document_version(version)

    logger.info(
        "extraction done version=%s parser=%s mime=%s fragments=%d attachments=%d",
        version.pk,
        parser.name,
        version.mime_type,
        fragment_total,
        attachment_total,
    )
    return ExtractionReport(
        version_id=version.pk,
        state=ExtractionState.DONE,
        derivatives=len(result.derivatives),
        fragments=fragment_total,
        attachments=attachment_total,
        seconds=time.monotonic() - started,
        note=result.note,
    )


def _write_derivative(version: DocumentVersion, *, parser: Any, payload: DerivativePayload) -> int:
    """One derivative, built then promoted, with the old one demoted first.

    The demote-then-promote order matters and is enforced by the partial unique
    constraint: two ACTIVE rows of the same kind cannot exist even for the
    duration of a statement, so getting this backwards raises rather than
    silently leaving a duplicate.
    """
    body = "\n\n".join(fragment.text for fragment in payload.fragments)
    digest = hashlib.sha256(
        (body + repr(sorted(payload.metadata.items()))).encode("utf-8")
    ).hexdigest()

    derivative = DocumentDerivative.objects.create(
        version=version,
        kind=payload.kind,
        generator=parser.name,
        generator_version=parser.version,
        status=DerivativeStatus.BUILDING,
        content_sha256=digest,
        metadata=payload.metadata,
        character_count=len(body),
        fragment_count=len(payload.fragments),
    )

    if payload.fragments:
        DocumentTextFragment.objects.bulk_create(
            [
                DocumentTextFragment(
                    derivative=derivative,
                    ordinal=ordinal,
                    text=fragment.text,
                    text_source=fragment.text_source,
                    locator_kind=fragment.locator_kind,
                    locator=fragment.locator,
                    locator_label=fragment.locator_label[:200],
                    character_count=len(fragment.text),
                )
                for ordinal, fragment in enumerate(payload.fragments, start=1)
            ]
        )

    if payload.binary is not None:
        derivative.storage_key = _store_binary(version, derivative, payload)
        derivative.save(update_fields=["storage_key", "updated_at"])

    DocumentDerivative.objects.filter(
        version=version, kind=payload.kind, status=DerivativeStatus.ACTIVE
    ).exclude(pk=derivative.pk).update(status=DerivativeStatus.SUPERSEDED)

    derivative.status = DerivativeStatus.ACTIVE
    derivative.built_at = timezone.now()
    derivative.save(update_fields=["status", "built_at", "updated_at"])
    return len(payload.fragments)


def _store_binary(
    version: DocumentVersion, derivative: DocumentDerivative, payload: DerivativePayload
) -> str:
    storage = derivative_storage()
    extension = payload.binary_extension or "bin"
    key = (
        f"{version.document_id}/{version.pk}/{derivative.kind.lower()}-{derivative.pk}.{extension}"
    )
    return storage.save(key, ContentFile(payload.binary or b""))


def discard_derivatives(version: DocumentVersion) -> int:
    """Delete every derivative of one version, and its stored binaries.

    `EmailAttachmentLink` rows are untouched: they record which message a stored
    binary arrived in, which is provenance rather than derived content and
    cannot be recovered by parsing again once the parser has changed (see
    `derivatives.py`).

    Note what this does *not* belong in front of. Deleting the live
    representation and then re-extracting is the destroy-first order the whole
    publish design exists to avoid: a parser that has regressed then leaves the
    file with nothing, and on a corpus-wide run it leaves the archive with
    nothing. `rebuild_document_derivatives` extracts first and calls
    :func:`discard_inactive_derivatives` afterwards.
    """
    return _delete_derivatives(DocumentDerivative.objects.filter(version=version))


def discard_inactive_derivatives(version: DocumentVersion) -> int:
    """Delete everything but the live representation of one version.

    What a completed rebuild leaves behind: the derivative it has just
    superseded, the FAILED rows earlier attempts recorded, and any BUILDING row
    a killed worker abandoned. The ACTIVE one is what search reads and is the
    reason this is a separate function rather than an argument.
    """
    return _delete_derivatives(
        DocumentDerivative.objects.filter(version=version).exclude(status=DerivativeStatus.ACTIVE)
    )


def _delete_derivatives(queryset: Any) -> int:
    """Remove derivative rows, then the binaries they addressed.

    Rows first. A row that survives while its bytes are gone is a broken
    thumbnail on a page; bytes that survive while the row is gone are a few
    kilobytes nobody addresses, and the derivative store is the one place where
    that is genuinely only wasted space.
    """
    storage = derivative_storage()
    keys = list(queryset.exclude(storage_key="").values_list("storage_key", flat=True))
    count, _ = queryset.delete()
    for key in keys:
        try:
            storage.delete(key)
        except Exception:  # pragma: no cover - best effort, the row is gone
            logger.warning("Could not remove derivative object %s", key)
    return count
