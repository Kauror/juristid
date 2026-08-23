"""Executing the plan, once, and never twice.

Everything here is a get-or-create against a stable source identity. Running
audit → plan → apply a second time against the same snapshots must add no
occurrence, no Document, no DocumentVersion, no Submission and no recipient row
— because the alternative is an evidence archive that grows a copy of itself
every time somebody re-runs the importer, and nobody notices until a count is
wrong (Stage-2H brief 64, 65).

Three rules the code enforces rather than documents.

**A sent action is counted once.** The same bytes may reach a Matter from the
OneNote materialisation, from an email attachment and from this archive. Those
are three provenance records of one letter. If a Submission already carries the
binary, this run attaches provenance to it and creates nothing (brief 67, 68).

**Bytes are stored once.** If the exact SHA-256 already exists as a
DocumentVersion on the Matter, that version becomes the final evidence. A second
copy of a 400 KB PDF is not extra safety, it is a second thing that can drift
(brief 30).

**Evidence enters through the front door.** ``create_document`` and
``add_evidence_version``, with the malware state left PENDING for the normal
scanner. No parallel store, no hand-set CLEAN (brief 32).

**Cataloguing and applying are two authority levels, not two halves of one.**
`catalogue_plan` records what the archive contains and what the reconciliation
proposes; `apply_plan` additionally claims that the Chamber *sent* a letter, in
canonical rows the rest of the application counts. The first is bookkeeping over
evidence somebody else produced and is safe to run as soon as the sources are
pinned. The second asserts a fact about the department's history and needs
either a deterministic match or a person. Keeping them callable separately is
what lets an archive be held and read months before anybody decides whose letter
each one was (docs/adr/0019).
"""

from __future__ import annotations

import datetime
from dataclasses import dataclass, field
from typing import Any

from django.db import models, transaction
from django.utils import timezone

from app.documents.enums import DocumentRole
from app.legacy_import.opinion_archive import (
    OpinionArchiveBatch,
    OpinionArchiveItem,
    OpinionArchiveMetadata,
    OpinionMatchCandidate,
    OpinionSubmissionImport,
)
from app.legacy_import.opinion_enums import (
    OpinionCandidateState,
    OpinionMetadataSystem,
    RecipientBasis,
)
from app.legacy_import.opinion_plan import OpinionArchivePlan, SubmissionPlan
from app.legacy_import.opinion_sources import ArchiveReader
from app.submissions.enums import RecipientRole, SentAtPrecision, SubmissionStatus

IMPORTER_VERSION = "opinion-archive/1.0.0"

#: The anchor a date-only historical value is stored at. It exists because
#: ``sent_at`` is a ``DateTimeField`` and the corpus supplies dates; it is never
#: rendered, because ``sent_at_precision`` tells the UI not to. Midnight local
#: time is chosen so that the date a query groups by is the date the source
#: wrote — which is not true of a UTC midnight anchor east of Greenwich.
HISTORICAL_ANCHOR_TIME = datetime.time(0, 0)


class OpinionApplyError(RuntimeError):
    """Apply refused to run against these sources."""


@dataclass
class CatalogueReport:
    """What one catalogue run recorded. Aggregates only, never row content.

    ``submissions_created`` is present and always zero. It is not a placeholder:
    the operator's question after this phase is precisely "did this create any
    canonical sending?", and a report that answered by omission would leave them
    reading code to find out.
    """

    items_created: int = 0
    items_existing: int = 0
    metadata_rows_written: int = 0
    candidates_written: int = 0
    candidates_existing: int = 0
    automatic_class_proposals: int = 0
    pending_candidates: int = 0
    human_decided_candidates: int = 0
    archive_sha256: str = ""
    kodadash_sha256: str = ""
    #: Invariant, not a counter. Cataloguing cannot create one.
    submissions_created: int = 0

    def as_text(self) -> str:
        return "\n".join(
            [
                "Arvamuste arhiivi kataloogimine",
                f"  uusi arhiivikirjeid        {self.items_created:>6}",
                f"  juba olemas                {self.items_existing:>6}",
                f"  tuletatud metaandmeid      {self.metadata_rows_written:>6}",
                f"  uusi sidumiskandidaate     {self.candidates_written:>6}",
                f"  kandidaate juba olemas     {self.candidates_existing:>6}",
                f"  neist automaatset klassi   {self.automatic_class_proposals:>6}",
                f"  ootel kandidaate           {self.pending_candidates:>6}",
                f"  inimese otsustatud         {self.human_decided_candidates:>6}",
                f"  loodud arvamusi            {self.submissions_created:>6}",
                "",
                "  Kanoonilist arvamust ega saatmiskirjet ei loodud.",
            ]
        )


@dataclass
class ApplyReport:
    items_created: int = 0
    items_existing: int = 0
    metadata_rows_written: int = 0
    candidates_written: int = 0
    documents_created: int = 0
    versions_created: int = 0
    versions_reused: int = 0
    submissions_created: int = 0
    submissions_linked: int = 0
    recipients_created: int = 0
    recipients_unresolved: int = 0
    skipped: list[str] = field(default_factory=list)

    def as_text(self) -> str:
        return "\n".join(
            [
                "Arvamuste arhiivi import",
                f"  uusi arhiivikirjeid        {self.items_created:>6}",
                f"  juba olemas                {self.items_existing:>6}",
                f"  tuletatud metaandmeid      {self.metadata_rows_written:>6}",
                f"  sidumiskandidaate          {self.candidates_written:>6}",
                f"  uusi dokumente             {self.documents_created:>6}",
                f"  uusi tõendiversioone       {self.versions_created:>6}",
                f"  taaskasutatud tõendeid     {self.versions_reused:>6}",
                f"  loodud arvamusi            {self.submissions_created:>6}",
                f"  seotud olemasolevaid       {self.submissions_linked:>6}",
                f"  loodud saajaid             {self.recipients_created:>6}",
                f"  lahendamata saajaid        {self.recipients_unresolved:>6}",
            ]
            + [f"  vahele jäetud: {reason}" for reason in self.skipped]
        )


def open_batch(plan: OpinionArchivePlan, *, notes: str = "") -> OpinionArchiveBatch:
    """One row per run, whatever the run did.

    A batch is a *run*, not a lifecycle stage, and the phase split does not
    change that: cataloguing opens one, a later canonical apply opens another,
    and each records the sources it reasoned about. Rows stay attributed to the
    run that first created them — `get_or_create` sets ``batch`` in ``defaults``
    — so a candidate keeps pointing at the reconciliation that proposed it even
    after a later apply executes it, and a Submission's provenance names both
    through ``OpinionSubmissionImport``.

    ``notes`` is how a catalogue run says so without a schema change. A batch
    that recorded no sending is already visible as one — nothing references it
    from ``OpinionSubmissionImport`` — but an operator reading the batch list
    should not have to infer it from an absence.
    """
    return OpinionArchiveBatch.objects.create(
        notes=notes,
        archive_file_name=plan.archive_path.name,
        archive_sha256=plan.archive_sha256,
        archive_occurrence_count=len(plan.occurrences),
        archive_distinct_sha_count=plan.distinct_binaries,
        excel_sha256=plan.excel_sha256,
        kodadash_artifact_name=plan.kodadash_path.name if plan.kodadash_path else "",
        kodadash_artifact_sha256=plan.kodadash_sha256,
        onenote_capture_id=plan.onenote_capture_id,
        importer_version=IMPORTER_VERSION,
        started_at=timezone.now(),
    )


def require_unchanged_sources(plan: OpinionArchivePlan) -> None:
    """Refuse to apply a plan approved against different evidence.

    The plan was built by re-reading the archive, so its hash is current by
    construction; what has to be re-checked is the *database* side, where the
    register snapshot or the OneNote capture may have been replaced between the
    review and the apply (brief 48).
    """
    from app.legacy_import.opinion_plan import onenote_capture_id, register_snapshot_sha256

    current_excel = register_snapshot_sha256()
    if plan.excel_sha256 and current_excel and current_excel != plan.excel_sha256:
        raise OpinionApplyError(
            "The register was imported from a different Excel snapshot than the plan was "
            f"reviewed against ({current_excel[:16]}… vs {plan.excel_sha256[:16]}…). "
            "Rebuild the plan; a reviewed plan describes one set of sources."
        )
    current_capture = onenote_capture_id()
    if plan.onenote_capture_id and current_capture and current_capture != plan.onenote_capture_id:
        raise OpinionApplyError(
            "The OneNote capture changed since the plan was built "
            f"({current_capture} vs {plan.onenote_capture_id}). Rebuild the plan."
        )


@transaction.atomic
def catalogue_plan(plan: OpinionArchivePlan, *, batch: OpinionArchiveBatch) -> CatalogueReport:
    """Record what the archive holds and what the reconciliation proposes.

    Writes exactly four kinds of row — `OpinionArchiveBatch` (the caller's),
    `OpinionArchiveItem`, `OpinionArchiveMetadata`, `OpinionMatchCandidate` —
    and nothing that claims the Chamber sent anything. No Document, no
    DocumentVersion, no Submission, no SubmissionRecipient, no
    `OpinionSubmissionImport`, no `OpinionArchiveBinary`, no
    `OpinionArchiveMatterLink`, no text and no search row.

    This exists because materialisation needs a catalogue and the only thing
    that produced one used to be the full apply. Holding a letter's bytes then
    required first asserting who sent it, which is the wrong way round: the
    bytes are the evidence the assertion would be based on.

    It does not skip the automatic classes so much as decline to act on them.
    `AUTOMATIC_MATCH_CLASSES` decides what an *apply* may execute without a
    person; it says nothing about what a catalogue may record, and the proposals
    are written here in full so the queue and the counts are complete.
    """
    report = CatalogueReport(
        archive_sha256=plan.archive_sha256, kodadash_sha256=plan.kodadash_sha256
    )
    _catalogue(plan, batch, report)
    _count_candidates(plan, report)
    return report


def _catalogue(
    plan: OpinionArchivePlan, batch: OpinionArchiveBatch, report: Any
) -> dict[str, OpinionArchiveItem]:
    """The three catalogue writers, in the one order they may run in.

    Shared verbatim by `catalogue_plan` and `apply_plan` rather than reimplemented
    beside it: two copies of "record the archive" would be two things to keep
    idempotent, and only one of them would be exercised by the phase an operator
    actually runs first.
    """
    items = _write_items(plan, batch, report)
    _write_metadata(plan, items, report)
    _write_candidates(plan, batch, items, report)
    return items


def _count_candidates(plan: OpinionArchivePlan, report: CatalogueReport) -> None:
    """Aggregate the queue this run leaves behind, including rows it reused.

    Counted from the database rather than from the plan, because a rerun's
    interesting number is the *state* of the queue — how much of it a person has
    already decided — and the plan has no idea.
    """
    from app.legacy_import.opinion_enums import AUTOMATIC_MATCH_CLASSES, HUMAN_DECIDED_STATES

    report.automatic_class_proposals = sum(
        1 for proposal in plan.proposals if proposal.match_class in AUTOMATIC_MATCH_CLASSES
    )
    candidate_ids = [
        proposal.candidate_id for proposal in plan.proposals if proposal.candidate_id is not None
    ]
    rows = OpinionMatchCandidate.objects.filter(pk__in=candidate_ids)
    report.candidates_existing = len(candidate_ids) - report.candidates_written
    report.pending_candidates = rows.filter(state=OpinionCandidateState.PENDING).count()
    report.human_decided_candidates = rows.filter(state__in=HUMAN_DECIDED_STATES).count()


@transaction.atomic
def apply_plan(
    plan: OpinionArchivePlan, *, batch: OpinionArchiveBatch, actor: Any = None
) -> ApplyReport:
    """Write the catalogue, the metadata, the candidates and the Submissions.

    Unchanged as an operator contract. It catalogues first — idempotently, so a
    prior `catalogue_plan` run costs it nothing and loses nothing — and then does
    the part only an apply may do.
    """
    report = ApplyReport()
    items = _catalogue(plan, batch, report)
    _write_submissions(plan, batch, items, report, actor)
    return report


# -- catalogue --------------------------------------------------------------


def _write_items(
    plan: OpinionArchivePlan, batch: OpinionArchiveBatch, report: ApplyReport
) -> dict[str, OpinionArchiveItem]:
    """One row per occurrence, keyed by (archive, path, bytes).

    Keyed by the occurrence rather than by the binary: two paths holding
    identical bytes are two filings of one letter, and the archive is the only
    place that fact survives (brief 29).
    """
    items: dict[str, OpinionArchiveItem] = {}
    for occurrence in plan.occurrences:
        item, created = OpinionArchiveItem.objects.get_or_create(
            archive_sha256=plan.archive_sha256,
            archive_relative_path=occurrence.relative_path,
            sha256=occurrence.sha256,
            defaults={
                "batch": batch,
                "original_filename": occurrence.original_filename[:500],
                "filename_encoding": occurrence.filename_encoding,
                "size_bytes": occurrence.size_bytes,
                "detected_type": occurrence.detected_type,
                "filename_date": occurrence.filename_date,
                "filename_recipient": occurrence.filename_recipient[:300],
                "filename_title": occurrence.filename_title,
            },
        )
        report.items_created += int(created)
        report.items_existing += int(not created)
        items.setdefault(occurrence.sha256, item)
    return items


def _write_metadata(
    plan: OpinionArchivePlan, items: dict[str, OpinionArchiveItem], report: ApplyReport
) -> None:
    """KodaDash's reading, stored beside the evidence and under its own name."""
    if not plan.kodadash_rows:
        return
    captured = timezone.now()
    for sha256, row in plan.kodadash_rows.items():
        item = items.get(sha256)
        if item is None:
            continue
        _, created = OpinionArchiveMetadata.objects.get_or_create(
            item=item,
            source_system=OpinionMetadataSystem.KODADASH,
            source_artifact_sha256=plan.kodadash_sha256,
            external_id=row.external_id,
            defaults={
                "source_artifact_name": plan.kodadash_path.name if plan.kodadash_path else "",
                "captured_at": captured,
                "recipient_raw": row.recipient_raw[:400],
                "recipient_normalized": row.recipient_normalized[:400],
                "recipient_filter_group": row.recipient_filter_group[:200],
                "recipient_type": row.recipient_type[:40],
                "recipient_secondary": row.recipient_secondary[:400],
                "recipient_review_required": row.recipient_review_required,
                "document_date": row.document_date,
                "title": row.title,
                "related_koda_news_url": row.related_news_url,
                "related_koda_news_id": row.related_news_id[:100],
                "policy_thread_id": row.policy_thread_id[:100],
                "public_import_eligible": row.public_import_eligible,
                "excluded_from_public": row.excluded_from_public,
                "exclusion_reason": row.exclusion_reason,
                "payload": row.payload,
            },
        )
        report.metadata_rows_written += int(created)


def _write_candidates(
    plan: OpinionArchivePlan,
    batch: OpinionArchiveBatch,
    items: dict[str, OpinionArchiveItem],
    report: ApplyReport,
) -> None:
    """Every proposal, including the ones nothing will be done with.

    An unmatched file is a finding, not an absence. Recording it is what lets
    the review queue say "these 300 have no defensible Matter" instead of the
    corpus quietly shrinking to the part that matched (brief 40, 51).
    """
    for proposal in plan.proposals:
        item = items.get(proposal.sha256)
        if item is None:
            continue
        # `matter_id` is legitimately null for an unmatched occurrence, which
        # the ORM accepts and the type stubs do not describe.
        matter_id: Any = proposal.matter_id
        candidate, created = OpinionMatchCandidate.objects.get_or_create(
            item=item,
            matter_id=matter_id,
            match_class=proposal.match_class,
            defaults={
                "batch": batch,
                "signals": list(proposal.signals),
                "conflicts": list(proposal.conflicts),
                "excel_reference": proposal.excel_reference[:40],
                "excel_sent_date": proposal.excel_sent_date,
                "excel_addressee_raw": proposal.excel_addressee_raw[:400],
                "onenote_page_key": proposal.onenote_page_key[:100],
                "onenote_page_title": proposal.onenote_page_title,
                "onenote_section": proposal.onenote_section[:300],
                "onenote_block_ordinal": proposal.onenote_block_ordinal,
                "competing_matter_count": proposal.competing_matter_count,
                "explanation": proposal.explanation,
            },
        )
        report.candidates_written += int(created)
        proposal.candidate_id = candidate.pk


# -- submissions ------------------------------------------------------------


def _candidate_ids_by_key(plan: OpinionArchivePlan) -> dict[tuple[str, str, Any], Any]:
    """The candidate each proposal wrote, keyed the way the row is unique.

    ``(sha256, match_class, matter_id)`` is not a convenient approximation: it
    is exactly the tuple `_write_candidates` passes to ``get_or_create``, so a
    hit here is the same row and not a plausible neighbour. That is the whole
    point — the Submission must point at the candidate that justified it, and
    searching for "a candidate on this item" would quietly pick the wrong one
    the day an occurrence carries two proposals.
    """
    return {
        (proposal.sha256, proposal.match_class, proposal.matter_id): proposal.candidate_id
        for proposal in plan.proposals
        if proposal.candidate_id is not None
    }


def _candidate_for(submission_plan: SubmissionPlan, by_key: dict[tuple[str, str, Any], Any]) -> Any:
    """The exact candidate behind one planned Submission, or ``None``.

    A reviewed plan already carries its candidate. An automatic one is looked
    up by the uniqueness key of the row this same run has just written.
    """
    if submission_plan.candidate_id is not None:
        return submission_plan.candidate_id
    key = (submission_plan.sha256, submission_plan.match_class, submission_plan.matter_id)
    return by_key.get(key)


def _mark_candidate_applied(candidate_id: Any) -> None:
    """Record that this candidate produced a canonical Submission.

    Only the workflow state moves. ``decided_by``, ``decided_at``,
    ``decision_note``, ``reviewed_sent_date`` and ``review_approves_submission``
    belong to whoever reviewed the row and are left exactly as they are —
    including left empty, because the system applying a deterministic match is
    not a person and must not be recorded as one (brief 51, 64).

    Called only after the ``OpinionSubmissionImport`` row exists, so APPLIED
    always describes something that happened rather than something the plan
    expected to happen.

    Two states may become APPLIED and no others. ``PENDING`` is the row the
    importer wrote and still owns. ``LINKED`` **with** an approved sending is a
    reviewer handing the row back to be executed. A row somebody rejected,
    called a duplicate, said was not an opinion, deferred, or linked *without*
    approving the sending is a decision, and a rerun that quietly flipped it to
    APPLIED would overturn a person from a cron job — the one thing this
    pipeline exists not to do (this task, 21).
    """
    if candidate_id is None:
        return
    OpinionMatchCandidate.objects.filter(pk=candidate_id).filter(
        models.Q(state=OpinionCandidateState.PENDING)
        | models.Q(state=OpinionCandidateState.LINKED, review_approves_submission=True)
    ).update(state=OpinionCandidateState.APPLIED)


def _write_submissions(
    plan: OpinionArchivePlan,
    batch: OpinionArchiveBatch,
    items: dict[str, OpinionArchiveItem],
    report: ApplyReport,
    actor: Any,
) -> None:
    reader = ArchiveReader(plan.archive_path)
    by_key = _candidate_ids_by_key(plan)
    for submission_plan in plan.submissions:
        item = items.get(submission_plan.sha256)
        if item is None:
            continue
        _write_one_submission(plan, submission_plan, item, batch, report, actor, reader, by_key)


def _write_one_submission(
    plan: OpinionArchivePlan,
    submission_plan: SubmissionPlan,
    item: OpinionArchiveItem,
    batch: OpinionArchiveBatch,
    report: ApplyReport,
    actor: Any,
    reader: ArchiveReader,
    by_key: dict[tuple[str, str, Any], Any],
) -> None:
    from app.matters.models import Matter
    from app.submissions.models import Submission

    candidate_id = _candidate_for(submission_plan, by_key)

    existing_import = OpinionSubmissionImport.objects.filter(item=item).first()
    if existing_import is not None:
        # This occurrence has already been reconciled. Re-running must find the
        # row and stop, not add a second Submission for the same letter.
        #
        # It may still repair its own bookkeeping. An import written before the
        # candidate link existed leaves a row that produced a canonical
        # Submission while its candidate sits in the review queue as PENDING —
        # a wrong statement about work that is finished. Repairing that writes
        # no Submission, no Document, no version and no second import row; it
        # only finishes recording what already happened (brief 64).
        if existing_import.candidate_id is None and candidate_id is not None:
            existing_import.candidate_id = candidate_id
            existing_import.save(update_fields=["candidate", "updated_at"])
        _mark_candidate_applied(existing_import.candidate_id or candidate_id)
        return

    if submission_plan.existing_submission_id is not None:
        submission = Submission.objects.get(pk=submission_plan.existing_submission_id)
        report.submissions_linked += 1
        version = submission.final_version
    else:
        matter = Matter.objects.get(pk=submission_plan.matter_id)
        version, _reused = _final_version_for(matter, submission_plan, item, actor, reader, report)
        if version is None:
            report.skipped.append(
                f"{item.original_filename[:60]}: lõplikku tõendit ei õnnestunud salvestada"
            )
            return
        submission = Submission.objects.create(
            matter=matter,
            kind=submission_plan.kind,
            title=submission_plan.title,
            status=SubmissionStatus.SENT,
            sent_at=timezone.make_aware(
                datetime.datetime.combine(submission_plan.sent_date, HISTORICAL_ANCHOR_TIME)
            ),
            sent_at_precision=SentAtPrecision.DATE,
            final_version=version,
            created_by=actor,
            notes=_provenance_note(submission_plan),
        )
        report.submissions_created += 1
        _attach_recipient(submission, submission_plan, report)

    OpinionSubmissionImport.objects.create(
        item=item,
        submission=submission,
        batch=batch,
        candidate_id=candidate_id,
        created_submission=submission_plan.existing_submission_id is None,
        match_class=submission_plan.match_class,
        sent_date_basis=submission_plan.sent_date_basis,
        recipient_basis=submission_plan.recipient_basis,
        matter_match_signals=list(submission_plan.signals),
        document_version=version,
    )
    # Only now. Everything above can still bail out — most importantly the
    # evidence write, which returns early when the bytes cannot be stored — and
    # a candidate marked APPLIED by a run that then wrote nothing would be the
    # worst of both states: absent from the queue and absent from the archive.
    _mark_candidate_applied(candidate_id)


def _provenance_note(submission_plan: SubmissionPlan) -> str:
    """Why this record says what it says, in words a lawyer can read.

    The structured answer lives on ``OpinionSubmissionImport``; this is the
    same answer where somebody reading the Submission will actually look.
    """
    return (
        "Taastatud arvamuste arhiivist.\n"
        f"Teema tuvastus: {submission_plan.match_class} "
        f"({', '.join(submission_plan.signals) or 'ilma signaalideta'}).\n"
        f"Kuupäeva alus: {submission_plan.sent_date_basis}.\n"
        f"Saaja alus: {submission_plan.recipient_basis} — „{submission_plan.recipient_raw}“."
    )


def _final_version_for(
    matter: Any,
    submission_plan: SubmissionPlan,
    item: OpinionArchiveItem,
    actor: Any,
    reader: ArchiveReader,
    report: ApplyReport,
) -> tuple[Any, bool]:
    """The exact binary, stored once.

    If the Matter already holds this SHA — because the OneNote materialisation
    put it there — that version is the evidence and nothing is written. The
    archive's copy is then provenance rather than a second file (brief 30).
    """
    from app.documents.models import DocumentVersion
    from app.documents.services import add_evidence_version, create_document

    existing = (
        DocumentVersion.objects.filter(document__matter=matter, sha256=item.sha256)
        .order_by("version_number")
        .first()
    )
    if existing is not None:
        report.versions_reused += 1
        return existing, True

    content = reader.read(item.archive_relative_path)
    if content is None:
        return None, False

    document = create_document(
        matter=matter,
        title=submission_plan.title,
        role=DocumentRole.KODA_SUBMISSION_FINAL,
        created_by=actor,
        provenance_note=(
            f"Arvamuste arhiiv · {item.archive_relative_path} · "
            f"arhiivi SHA-256 {item.archive_sha256[:16]}…"
        ),
    )
    report.documents_created += 1
    version = add_evidence_version(
        document=document,
        content=content,
        original_filename=item.original_filename,
        mime_type=item.detected_type or "application/pdf",
        uploaded_by=actor,
        acquired_at=timezone.now(),
        source_path=item.archive_relative_path,
        source_identifier=f"opinions-archive:{item.archive_sha256}",
    )
    report.versions_created += 1
    return version, False


def _attach_recipient(
    submission: Any, submission_plan: SubmissionPlan, report: ApplyReport
) -> None:
    """Resolve the recipient conservatively, or preserve it unresolved.

    Exact identity or a reviewed alias. No similarity, and no creating an
    Organisation from a historical spelling: the register writes both
    ``Keskkonnaministeerium`` and ``Kliimaministeerium``, which look alike and
    are not the same body (brief 22).
    """
    from app.legacy_import.resolution import MappingTables, resolve_organisation
    from app.submissions.models import SubmissionRecipient

    raw = submission_plan.recipient_raw.strip()
    if not raw or submission_plan.recipient_basis == RecipientBasis.UNRESOLVED:
        report.recipients_unresolved += 1
        return

    resolution = resolve_organisation(raw, MappingTables.empty())
    if resolution.value is None:
        report.recipients_unresolved += 1
        return

    _, created = SubmissionRecipient.objects.get_or_create(
        submission=submission,
        organisation=resolution.value,
        defaults={"role": RecipientRole.ADDRESSEE, "note": raw[:200]},
    )
    report.recipients_created += int(created)
