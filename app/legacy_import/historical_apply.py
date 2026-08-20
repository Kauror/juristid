"""Writing the plan.

Everything here is idempotent by construction: each step looks for the row that
would prove it already ran, and does nothing if it finds one. That is not
defensive habit, it is the design requirement — 10,916 files take time, a run
will be interrupted, and a second apply must produce zero duplicate Matters,
pages, links or Documents (Stage-2D brief 47, 48).

Idempotency keys, each chosen because it is a property of the *source* rather
than of a run:

* source page → ``(source_system, source_page_id)``
* resource → ``(source_page, resource_key)``
* link → ``(matter, source_page)``
* materialised file → ``(matter_source_page, resource)``

The work is deliberately split in two. ``apply_structure`` brings in the pages,
links, Matters and review queue — hundreds of small rows, seconds of work, and
the point at which the corpus becomes *navigable*. ``materialise_resources``
then streams 4.14 GiB of originals into evidence, and can be stopped and resumed
all day without the first half being at risk. A historical corpus that is
readable but still copying files is far more useful than one that is unavailable
until the last PDF lands (Stage-2D brief 63).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from django.core.files.base import ContentFile
from django.core.files.storage import storages
from django.db import transaction
from django.utils import timezone

from app.core.enums import Visibility
from app.documents.enums import ExtractionState, MalwareScanState
from app.documents.models import DocumentVersion
from app.documents.services import add_evidence_version, create_document
from app.legacy_import.historical_materials import (
    extraction_note_for,
    is_extractable,
    mime_type_for,
    role_for,
)
from app.legacy_import.historical_plan import HistoricalPlan, SourcePagePlan
from app.legacy_import.models import ImportBatch
from app.legacy_import.onenote_archive import ArchivePage, OneNoteArchive
from app.legacy_import.source_pages import (
    CandidateClass,
    HistoricalMatchCandidate,
    LegacySourcePage,
    LegacySourceResource,
    LegacySourceResourceImport,
    MatterSourcePage,
    ResourceImportState,
    ResourceKind,
    SourceMatchClass,
    SourceMatchMethod,
    SourcePageRole,
    SourceRelationshipKind,
    SourceSystem,
)
from app.matters.enums import DataQualityTier, MatterOrigin, RecordMode
from app.matters.models import Matter

logger = logging.getLogger(__name__)


def index_source_link(link: MatterSourcePage) -> None:
    """Make a newly linked page findable in the same breath as linking it.

    Imported inside the function because the search app imports the legacy
    models, and importing it at module scope closes the circle.
    """
    from app.search.indexing import refresh_source_link

    refresh_source_link(link)


#: Page XML lives in its own storage class. It is source evidence — kept byte
#: for byte, hashed, never rendered — but it is not a Document, and mixing it
#: into the evidence store would put rows in the document tables that no
#: DocumentVersion owns (Stage-2D brief 11).
LEGACY_SOURCE_STORAGE_ALIAS = "legacy_source"


@dataclass
class ApplyReport:
    batch_id: Any = None
    source_pages_created: int = 0
    source_pages_updated: int = 0
    resources_catalogued: int = 0
    exact_links_created: int = 0
    exact_links_unmatched: list[str] = field(default_factory=list)
    onenote_matters_created: int = 0
    candidates_created: int = 0
    documents_created: int = 0
    document_bytes: int = 0
    materialisations_skipped: int = 0
    failures: list[str] = field(default_factory=list)

    def as_text(self) -> str:
        return "\n".join(
            [
                f"  source pages          {self.source_pages_created} new, "
                f"{self.source_pages_updated} refreshed",
                f"  resources catalogued  {self.resources_catalogued}",
                f"  exact links           {self.exact_links_created}"
                + (
                    f" ({len(self.exact_links_unmatched)} references not found)"
                    if self.exact_links_unmatched
                    else ""
                ),
                f"  OneNote-only Matters  {self.onenote_matters_created}",
                f"  review candidates     {self.candidates_created}",
                f"  documents materialised {self.documents_created} "
                f"({self.document_bytes:,} bytes)",
                f"  already present       {self.materialisations_skipped}",
                f"  failures              {len(self.failures)}",
            ]
        )


def legacy_source_storage() -> Any:
    return storages[LEGACY_SOURCE_STORAGE_ALIAS]


def open_batch(plan: HistoricalPlan, *, importer_version: str) -> ImportBatch:
    return ImportBatch.objects.create(
        source_system="ONENOTE_DESKTOP+EXCEL",
        source_file_name=plan.archive_root.name,
        source_snapshot_sha256=plan.manifest_sha256,
        importer_version=importer_version,
        contract_version="historical-corpus/1",
        started_at=timezone.now(),
        source_row_count=len(plan.source_pages),
    )


# -- structure -------------------------------------------------------------


def apply_structure(
    plan: HistoricalPlan, *, batch: ImportBatch, archive: OneNoteArchive
) -> ApplyReport:
    """Pages, links, OneNote-only Matters and the review queue.

    One transaction per page rather than one for the whole run. A single
    transaction over 755 pages would mean an interruption at page 700 discards
    699 correct imports, and there is no correctness argument for atomicity
    here: a page that committed is complete on its own.
    """
    report = ApplyReport(batch_id=batch.pk)

    for page_plan in plan.source_pages:
        try:
            with transaction.atomic():
                page, created = _upsert_source_page(page_plan, batch=batch, archive=archive)
                report.source_pages_created += int(created)
                report.source_pages_updated += int(not created)
                report.resources_catalogued += _catalogue_resources(page, page_plan.page)
        except Exception as error:  # one page must not end the run
            logger.exception("Importing source page %s failed", page_plan.page.page_key)
            report.failures.append(f"{page_plan.page.page_key}: {type(error).__name__}")

    _apply_exact_links(plan, batch=batch, report=report)
    _create_onenote_matters(plan, batch=batch, report=report)
    _record_candidates(plan, batch=batch, report=report)
    return report


def _upsert_source_page(
    page_plan: SourcePagePlan, *, batch: ImportBatch, archive: OneNoteArchive
) -> tuple[LegacySourcePage, bool]:
    page = page_plan.page
    profile = page_plan.profile
    now = timezone.now()

    existing = LegacySourcePage.objects.filter(
        source_system=SourceSystem.ONENOTE_DESKTOP, source_page_id=page.page_id
    ).first()

    values = {
        "page_key": page.page_key,
        "source_notebook": page.notebook,
        "source_section": page.section,
        "source_section_group": page.section_group or "",
        "source_parent_page": profile.parent_page,
        "title": page.title,
        "page_level": page.level,
        "page_order": page.page_order,
        "child_count": profile.child_count,
        "page_role": _role(profile.role),
        "role_reason": profile.role_reason,
        "source_created_at": page.created_at,
        "source_modified_at": page.modified_at,
        "capture_id": page.capture_id,
        "source_xml_sha256": page.xml_sha256,
        "derived_text": page.derived_text,
        "blocks": [
            {
                "ordinal": block.ordinal,
                "kind": block.kind,
                "text": block.text,
                "resource_key": block.resource_key,
                "depth": block.depth,
            }
            for block in page.blocks
        ],
        "links": list(page.links),
        "reading_order_strategy": page.reading_order_strategy,
        "reading_order_ambiguous": page.reading_order_ambiguous,
        "onenote_hyperlink": profile.hyperlink,
        "reference_tokens": profile.reference_tokens[:300],
        "text_characters": len(page.derived_text),
        "block_count": len(page.blocks),
        "file_count": page.file_count,
        "file_bytes": page.file_bytes,
        "latest_imported_at": now,
        "import_batch": batch,
    }

    if existing is None:
        record = LegacySourcePage.objects.create(
            source_system=SourceSystem.ONENOTE_DESKTOP,
            source_page_id=page.page_id,
            first_imported_at=now,
            **values,
        )
        created = True
    else:
        for name, value in values.items():
            setattr(existing, name, value)
        existing.save()
        record = existing
        created = False

    _store_page_xml(record, page, archive)
    return record, created


def _store_page_xml(record: LegacySourcePage, page: ArchivePage, archive: OneNoteArchive) -> None:
    """Keep the page's own XML, byte for byte.

    Stored once per capture. The key carries the capture id, so a page that is
    re-archived later gets a new object rather than overwriting the evidence for
    what it used to say.
    """
    key = f"{record.page_key}/{page.capture_id}/page.source.xml"
    if record.source_xml_storage_key == key:
        return
    storage = legacy_source_storage()
    if not storage.exists(key):
        storage.save(key, ContentFile(archive.read_page_xml(page)))
    record.source_xml_storage_key = key
    record.save(update_fields=["source_xml_storage_key", "updated_at"])


def _role(raw: str) -> str:
    return raw if raw in SourcePageRole.values else SourcePageRole.UNCLEAR


def _catalogue_resources(record: LegacySourcePage, page: ArchivePage) -> int:
    """The page's files, as a catalogue. No bytes are copied here."""
    existing = set(
        LegacySourceResource.objects.filter(source_page=record).values_list(
            "resource_key", flat=True
        )
    )
    fresh = [
        LegacySourceResource(
            source_page=record,
            resource_key=resource.resource_key,
            original_filename=resource.original_filename[:500],
            resource_kind=(
                resource.resource_kind
                if resource.resource_kind in ResourceKind.values
                else ResourceKind.OTHER
            ),
            source_block_ordinal=resource.source_block_ordinal,
            sha256=resource.sha256,
            size_bytes=resource.size_bytes,
            archive_relative_path=resource.relative_path,
            is_inline=resource.is_inline,
        )
        for resource in page.resources
        if resource.resource_key not in existing
    ]
    LegacySourceResource.objects.bulk_create(fresh)
    return len(fresh)


def _apply_exact_links(plan: HistoricalPlan, *, batch: ImportBatch, report: ApplyReport) -> None:
    """Attach pages to the Excel Matters whose hyperlinks name them.

    A reference the register does not contain is reported, not invented. It
    means the Excel import has not run or that row did not become a Matter, and
    both are worth an operator's attention (Stage-2D brief 20).
    """
    pages = {
        page.page_key: page
        for page in LegacySourcePage.objects.filter(
            page_key__in=[link.page_key for link in plan.exact_links]
        )
    }
    for link in plan.exact_links:
        page = pages.get(link.page_key)
        if page is None:
            report.failures.append(f"exact link {link.excel_reference}: page not imported")
            continue
        matter = _matter_by_reference(link.excel_reference)
        if matter is None:
            report.exact_links_unmatched.append(link.excel_reference)
            continue
        record, created = MatterSourcePage.objects.get_or_create(
            matter=matter,
            source_page=page,
            defaults={
                "relationship_kind": SourceRelationshipKind.PRIMARY,
                "match_method": SourceMatchMethod.EXCEL_EXACT_PAGE_ID,
                "match_class": SourceMatchClass.EXACT,
                "source_audit_reference": link.audit_row,
            },
        )
        if created:
            report.exact_links_created += 1
            index_source_link(record)


def _matter_by_reference(reference: str) -> Matter | None:
    parsed = Matter.parse_reference(reference)
    if parsed is None:
        return None
    year, number = parsed
    return Matter.objects.filter(reference_year=year, reference_number=number).first()


def _create_onenote_matters(
    plan: HistoricalPlan, *, batch: ImportBatch, report: ApplyReport
) -> None:
    """A Matter for each page that is one, with no register reference invented.

    Idempotent through the link rather than through the title: a second run
    finds the page already has a ``ONENOTE_ONLY_MATTER`` relationship and stops.
    Matching on title would merge two genuinely different pages that a lawyer
    happened to name the same thing.
    """
    for page_plan in plan.onenote_only_matters:
        page = LegacySourcePage.objects.filter(
            source_system=SourceSystem.ONENOTE_DESKTOP, source_page_id=page_plan.page.page_id
        ).first()
        if page is None:
            report.failures.append(f"{page_plan.page.page_key}: page not imported")
            continue
        if MatterSourcePage.objects.filter(
            source_page=page, match_method=SourceMatchMethod.ONENOTE_ONLY_MATTER
        ).exists():
            continue

        try:
            with transaction.atomic():
                matter = Matter.objects.create(
                    title=page_plan.matter_title[:2000],
                    # No reference. The register never had this, and minting one
                    # would put fiction in the column the product treats as
                    # identity (Stage-2D brief 16).
                    reference_year=None,
                    reference_number=None,
                    record_mode=RecordMode.ARCHIVE,
                    origin=MatterOrigin.LEGACY_ONENOTE,
                    data_quality_tier=DataQualityTier.TIER_3_REGISTER_ARCHIVE,
                    reporting_year=(
                        page_plan.page.created_at.year if page_plan.page.created_at else None
                    ),
                    is_open=False,
                    visibility=Visibility.NORMAL,
                )
                record = MatterSourcePage.objects.create(
                    matter=matter,
                    source_page=page,
                    relationship_kind=SourceRelationshipKind.PRIMARY,
                    match_method=SourceMatchMethod.ONENOTE_ONLY_MATTER,
                    match_class=SourceMatchClass.EXACT,
                    source_audit_reference=f"unmatched-onenote.csv:{page.page_key}",
                )
                index_source_link(record)
            report.onenote_matters_created += 1
        except Exception as error:
            logger.exception("Creating a OneNote-only Matter for %s failed", page.page_key)
            report.failures.append(f"{page.page_key}: {type(error).__name__}")


def _record_candidates(plan: HistoricalPlan, *, batch: ImportBatch, report: ApplyReport) -> None:
    """Everything a person still has to decide, kept where they can decide it."""
    pages = {
        page.page_key: page
        for page in LegacySourcePage.objects.filter(
            page_key__in=[candidate.page_key for candidate in plan.candidates if candidate.page_key]
        )
    }
    for candidate in plan.candidates:
        page = pages.get(candidate.page_key)
        if page is None and candidate.page_key:
            continue
        klass = (
            candidate.candidate_class
            if candidate.candidate_class in CandidateClass.values
            else CandidateClass.REVIEW_REQUIRED
        )
        _, created = HistoricalMatchCandidate.objects.get_or_create(
            source_page=page,
            excel_reference=candidate.excel_reference,
            candidate_class=klass,
            defaults={
                "matter": _matter_by_reference(candidate.excel_reference),
                "excel_title": candidate.excel_title,
                "excel_onenote_url": candidate.excel_onenote_url,
                "score": candidate.score,
                "match_signals": candidate.signals,
                "conflicts": candidate.conflicts,
                "explanation": candidate.explanation,
                "import_batch": batch,
            },
        )
        report.candidates_created += int(created)


# -- materials -------------------------------------------------------------


def pending_materialisations() -> list[tuple[MatterSourcePage, LegacySourceResource]]:
    """Every (link, resource) pair that has no Document yet.

    Computed rather than remembered, so a run interrupted anywhere resumes by
    asking the database what is still missing instead of trusting a cursor.
    """
    links = list(
        MatterSourcePage.objects.select_related("matter", "source_page").order_by("created_at")
    )
    done = {
        (row["matter_source_page_id"], row["resource_id"])
        for row in LegacySourceResourceImport.objects.values("matter_source_page_id", "resource_id")
    }
    pending = []
    for link in links:
        for resource in link.source_page.resources.order_by("source_block_ordinal"):
            if (link.pk, resource.pk) not in done:
                pending.append((link, resource))
    return pending


def materialise_resources(
    *,
    archive: OneNoteArchive,
    batch: ImportBatch | None = None,
    limit: int | None = None,
    report: ApplyReport | None = None,
) -> ApplyReport:
    """Copy originals into evidence, one (link, resource) pair at a time.

    A shared page materialises into each Matter that accepted it. That is
    deliberate duplication — the corpus is 4.14 GiB and a correct historical
    relationship is worth more than the bytes — and every copy records the same
    ``resource_key`` and the same source SHA-256, so the duplication reads as
    duplication rather than as different files (Stage-2D brief 28).
    """
    report = report or ApplyReport(batch_id=batch.pk if batch else None)
    pending = pending_materialisations()
    if limit is not None:
        pending = pending[:limit]

    for link, resource in pending:
        try:
            _materialise_one(link, resource, archive=archive, batch=batch, report=report)
        except Exception as error:
            # One file must not cost the other 10,915. Recorded against the
            # exact pair so a retry knows what to do again.
            logger.exception("Materialising %s failed", resource.resource_key)
            report.failures.append(f"{resource.resource_key}: {type(error).__name__}")
            LegacySourceResourceImport.objects.update_or_create(
                matter_source_page=link,
                resource=resource,
                defaults={
                    "state": ResourceImportState.FAILED,
                    "error_code": type(error).__name__,
                    "error_detail": str(error)[:2000],
                    "import_batch": batch,
                },
            )
    return report


@transaction.atomic
def _materialise_one(
    link: MatterSourcePage,
    resource: LegacySourceResource,
    *,
    archive: OneNoteArchive,
    batch: ImportBatch | None,
    report: ApplyReport,
) -> None:
    existing = LegacySourceResourceImport.objects.filter(
        matter_source_page=link, resource=resource
    ).first()
    if existing is not None and existing.state == ResourceImportState.IMPORTED:
        report.materialisations_skipped += 1
        return

    path = Path(archive.pages_root) / link.source_page.page_key / resource.archive_relative_path
    content = path.read_bytes()

    filename = resource.original_filename
    mime_type = mime_type_for(filename)
    document = create_document(
        matter=link.matter,
        title=filename[:400],
        role=role_for(filename),
        provenance_note=(
            f"OneNote: {link.source_page.source_section} → {link.source_page.title[:120]} "
            f"(plokk {resource.source_block_ordinal})"
        ),
    )
    version = add_evidence_version(
        document=document,
        content=content,
        original_filename=filename,
        mime_type=mime_type,
        acquired_at=link.source_page.source_modified_at or link.source_page.first_imported_at,
        source_identifier=f"{link.source_page.page_key}/{resource.resource_key}",
        # Never CLEAN. These files predate any scanner this system will ever
        # run, and saying otherwise would be inventing a control
        # (Stage-2B brief 32).
        malware_scan_state=MalwareScanState.PENDING,
    )

    if version.sha256 != resource.sha256:
        raise ValueError(
            f"{resource.resource_key}: stored SHA-256 {version.sha256} does not match "
            f"the archive's {resource.sha256}"
        )

    if not is_extractable(Path(filename).suffix.lower()):
        # Marked here rather than left for the worker to discover. A format
        # nothing can parse should not sit PENDING in a queue for ever, and the
        # operator-facing reason belongs with the file.
        DocumentVersion.objects.filter(pk=version.pk).update(
            extraction_state=ExtractionState.NOT_APPLICABLE,
            extraction_note=extraction_note_for(filename)[:300],
        )

    LegacySourceResourceImport.objects.update_or_create(
        matter_source_page=link,
        resource=resource,
        defaults={
            "document": document,
            "document_version": version,
            "state": ResourceImportState.IMPORTED,
            "error_code": "",
            "error_detail": "",
            "import_batch": batch,
        },
    )
    report.documents_created += 1
    report.document_bytes += version.size_bytes
