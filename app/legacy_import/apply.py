"""Executing a plan.

This is the only module in the import pipeline that writes. It consumes the
plan the dry run produced — the same object, from the same code — so what a
reviewer approved is what runs.

Everything happens in one transaction. A half-applied import is worse than no
import: the ledger would claim rows were handled that were not, and a second
run would have to reason about a state nobody designed.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from django.db import transaction
from django.utils import timezone

from app.audit.enums import ChangeEventType, SecurityEventType
from app.audit.services import record_change_event, record_security_event
from app.legacy_import.contracts import contract_versions
from app.legacy_import.enums import (
    NON_IMPORTING_OUTCOMES,
    OneNoteContentStatus,
    ProposedRecordMode,
    RowOutcome,
)
from app.legacy_import.models import (
    ImportBatch,
    ImportRowLedger,
    MatterSourceReference,
    ReconciliationStatus,
)
from app.legacy_import.parser import PARSER_VERSION, SOURCE_SYSTEM
from app.legacy_import.planner import ImportPlan, RowPlan
from app.matters.enums import DataQualityTier, RecordMode
from app.matters.models import Matter
from app.matters.services import create_imported_matter, reserve_matter_reference
from app.search.indexing import indexable_matters, refresh_matters, suspend_indexing

#: Bumped when the apply step's behaviour changes. Stored on every batch.
IMPORTER_VERSION = "2A.1.0"


@dataclass
class ApplyResult:
    batch: ImportBatch
    created: int
    matched: int
    already_imported: int
    review_required: int
    reserved: int
    skipped: int
    references_reserved: dict[int, int]

    @property
    def accounted_rows(self) -> int:
        return (
            self.created
            + self.matched
            + self.already_imported
            + self.review_required
            + self.reserved
            + self.skipped
        )


def _record_mode_for(plan: RowPlan) -> str:
    """A proposal becomes a stored value here, and only here.

    Only an explicit reviewed override produces a FULL record. ``FULL_CANDIDATE``
    means "a person should look at whether this is live", and it lands as an
    ARCHIVE record with the proposal and its reason preserved in the ledger —
    visible, reviewable, and reversible by promoting the Matter, which is a
    designed operation rather than a data fix.
    """
    if plan.proposed_record_mode == ProposedRecordMode.FULL.value:
        return RecordMode.FULL
    return RecordMode.ARCHIVE


def _data_quality_tier(plan: RowPlan) -> str:
    if plan.proposed_record_mode == ProposedRecordMode.FULL_CANDIDATE.value:
        return DataQualityTier.TIER_2_RICH_HISTORY
    return DataQualityTier.TIER_3_REGISTER_ARCHIVE


def _matter_fields(plan: RowPlan) -> dict[str, Any]:
    row = plan.row
    fields: dict[str, Any] = {
        "source_era": row.era,
        "data_quality_tier": _data_quality_tier(plan),
        "received_date": row.received.value if row.received else None,
        "response_deadline": row.deadline.value if row.deadline else None,
    }

    if plan.owner is not None and plan.owner.resolved:
        fields["owner"] = plan.owner.value

    # The direction is whatever the era's contract said it was. It is never
    # inferred here, and the two columns never merge.
    #
    # The sender side takes a list because the Matter now holds a set of them,
    # and the list this importer builds is never longer than one. A raw KELLELT
    # cell that mentions two institutions is still whatever the reviewed
    # resolver made of it: plural storage is permission to record several
    # senders when we know them, not permission to start splitting historical
    # strings on a comma (Agent-E brief 17, 49).
    if plan.organisation is not None and plan.organisation.resolved:
        if row.counterparty_direction == "source":
            fields["source_organisations"] = [plan.organisation.value]
        elif row.counterparty_direction == "addressee":
            fields["addressee_organisation"] = plan.organisation.value

    status = plan.status
    if status is not None and status.stage is not None:
        fields["stage"] = status.stage
    if status is not None and status.is_closure:
        # An ARCHIVE Matter may be closed without a closure timestamp, which is
        # exactly why the import proposes ARCHIVE for closure labels: the
        # register recorded that Koda stopped and never recorded when, and the
        # alternative would be a fabricated date (app/matters/models.py
        # constraint ``matters_closure_fields_consistent``).
        fields["is_open"] = False
        fields["disposition"] = status.disposition

    return fields


def _source_reference_for(
    plan: RowPlan, matter: Matter, batch: ImportBatch, snapshot: str, file_name: str
) -> MatterSourceReference:
    row = plan.row
    return MatterSourceReference(
        matter=matter,
        import_batch=batch,
        source_system=SOURCE_SYSTEM,
        source_file_name=file_name,
        source_snapshot_sha256=snapshot,
        source_sheet=row.sheet,
        source_row_number=row.row_number,
        source_row_raw=row.raw_row,
        source_title=row.title,
        source_date_raw=(row.received.raw if row.received else "")[:200],
        onenote_url=row.onenote_url,
        # Parsed only when the link states it unambiguously. A page id guessed
        # from a URL shape is a guess about identity, and this column is used to
        # match pages later (Stage-2A brief 21).
        onenote_page_id="",
        onenote_content_status=(
            OneNoteContentStatus.NOT_IMPORTED
            if row.onenote_url
            else OneNoteContentStatus.NOT_APPLICABLE
        ),
        source_era=row.era,
        source_contract_version=row.contract_version,
        source_parser_version=PARSER_VERSION,
        match_method=plan.match_method,
        conflict_state="NONE",
    )


@transaction.atomic
def apply_plan(plan: ImportPlan, *, actor: Any = None, notes: str = "") -> ApplyResult:
    """Write a plan. One transaction, or nothing at all."""
    with suspend_indexing():
        return _apply(plan, actor=actor, notes=notes)


def _apply(plan: ImportPlan, *, actor: Any, notes: str) -> ApplyResult:
    started = timezone.now()
    batch = ImportBatch.objects.create(
        source_system=SOURCE_SYSTEM,
        source_file_name=plan.inventory.file_name,
        source_snapshot_sha256=plan.inventory.sha256,
        importer_version=IMPORTER_VERSION,
        contract_version=contract_versions(),
        started_at=started,
        source_row_count=len(plan.rows),
        reconciliation_status=ReconciliationStatus.RUNNING,
        notes=notes,
    )

    counts = {
        RowOutcome.WOULD_CREATE.value: 0,
        RowOutcome.WOULD_MATCH.value: 0,
        RowOutcome.ALREADY_IMPORTED.value: 0,
        RowOutcome.REVIEW_REQUIRED.value: 0,
        RowOutcome.RESERVED_REFERENCE.value: 0,
    }
    skipped = 0
    ledger: list[ImportRowLedger] = []
    references: list[MatterSourceReference] = []
    touched: list[Any] = []

    for row_plan in plan.rows:
        outcome = row_plan.outcome
        if outcome == RowOutcome.BLANK_PADDING.value:
            # Counted, never written. A ledger row for each of the 451 blank
            # padding rows in the real workbook would bury the ledger.
            skipped += 1
            continue

        matter = row_plan.matter
        if outcome == RowOutcome.WOULD_CREATE.value:
            matter = _create(row_plan, actor)
            touched.append(matter.pk)
            references.append(
                _source_reference_for(
                    row_plan, matter, batch, plan.inventory.sha256, plan.inventory.file_name
                )
            )
        elif outcome == RowOutcome.WOULD_MATCH.value and matter is not None:
            references.append(
                _source_reference_for(
                    row_plan, matter, batch, plan.inventory.sha256, plan.inventory.file_name
                )
            )

        if outcome in counts:
            counts[outcome] += 1

        ledger.append(
            ImportRowLedger(
                import_batch=batch,
                matter=matter if outcome not in NON_IMPORTING_OUTCOMES else None,
                source_sheet=row_plan.row.sheet,
                source_row_number=row_plan.row.row_number,
                source_reference=row_plan.row.display_reference or row_plan.row.reference_raw[:64],
                outcome=outcome,
                anomalies=row_plan.anomalies,
                proposed_record_mode=row_plan.proposed_record_mode,
                proposed_record_mode_reason=row_plan.proposed_record_mode_reason,
                note=row_plan.note,
            )
        )

    MatterSourceReference.objects.bulk_create(references)
    ImportRowLedger.objects.bulk_create(ledger)

    # One pass rather than 2,455 separate refreshes. Search is a derived layer,
    # so it is rebuilt after the import rather than maintained during it.
    if touched:
        refresh_matters(indexable_matters().filter(pk__in=touched))

    # Every reference the register has spoken for, imported or merely reserved,
    # so native creation after this run cannot collide with it.
    reserved: dict[int, int] = {}
    for year, number in sorted(plan.highest_reference_by_year.items()):
        reserved[year] = reserve_matter_reference(year, number)

    review_required = counts[RowOutcome.REVIEW_REQUIRED.value]
    batch.finished_at = timezone.now()
    batch.created_matter_count = counts[RowOutcome.WOULD_CREATE.value]
    batch.matched_count = counts[RowOutcome.WOULD_MATCH.value]
    batch.unmatched_count = review_required
    batch.reconciliation_status = (
        ReconciliationStatus.COMPLETED_WITH_GAPS
        if review_required
        else ReconciliationStatus.COMPLETED
    )
    batch.save(
        update_fields=[
            "finished_at",
            "created_matter_count",
            "matched_count",
            "unmatched_count",
            "reconciliation_status",
            "updated_at",
        ]
    )

    record_security_event(
        event_type=SecurityEventType.IMPORT_RUN,
        actor=actor,
        subject=batch,
        detail={
            "source_file": plan.inventory.file_name,
            "sha256": plan.inventory.sha256,
            "rows": len(plan.rows),
        },
    )

    return ApplyResult(
        batch=batch,
        created=counts[RowOutcome.WOULD_CREATE.value],
        matched=counts[RowOutcome.WOULD_MATCH.value],
        already_imported=counts[RowOutcome.ALREADY_IMPORTED.value],
        review_required=review_required,
        reserved=counts[RowOutcome.RESERVED_REFERENCE.value],
        skipped=skipped,
        references_reserved=reserved,
    )


def _create(plan: RowPlan, actor: Any) -> Matter:
    row = plan.row
    matter = create_imported_matter(
        title=row.title,
        reference_year=row.reference.year if row.reference else None,
        reference_number=row.reference.number if row.reference else None,
        actor=actor,
        record_mode=_record_mode_for(plan),
        **_matter_fields(plan),
    )
    record_change_event(
        event_type=ChangeEventType.IMPORT_APPLIED,
        matter=matter,
        actor=actor,
        obj=matter,
        summary=f"{row.sheet}:{row.row_number}",
        payload={
            "reference": matter.display_reference,
            "source_era": row.era,
            "proposed_record_mode": plan.proposed_record_mode,
            "proposed_record_mode_reason": plan.proposed_record_mode_reason,
            "anomalies": plan.anomalies,
        },
    )
    return matter
