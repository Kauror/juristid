"""What a lawyer adds to a synchronised Teema that Lovable never owns (LC-J1).

Approval 11.11 §1 «Never touched: Juristid-only notes, documents, submissions,
evidence, OneNote imports, a person's own important or commencement dates, and
the audit trail». `add_everything` writes one of each on a Teema — through the
domain services where one exists, and where none does (a OneNote capture is
written by the desktop importer, not a service) the rows that importer writes,
as `tests/test_ls2_b_source_page_versions.py` builds them. `records_of` returns
every such row of the Teemad *plus* the rows the import itself wrote that are
Juristid's own (the `EvidencePilotRecord` provenance), so a test can hash them
before and after a run (`world.row_hashes`).

Invented values only.
"""

from __future__ import annotations

import datetime as dt
import hashlib
from typing import Any

from django.utils import timezone

T_PAGE = dt.datetime(2024, 3, 1, 9, 0, tzinfo=dt.UTC)
T_LATER = dt.datetime(2026, 11, 7, 12, 0, tzinfo=dt.UTC)


def _batch(label: str) -> Any:
    from app.legacy_import.models import ImportBatch, ReconciliationStatus

    return ImportBatch.objects.create(
        source_system="ONENOTE_DESKTOP_CAPTURE",
        source_file_name=f"{label}-package",
        source_snapshot_sha256=hashlib.sha256(label.encode()).hexdigest(),
        importer_version="test/1",
        contract_version="test/1",
        started_at=timezone.now(),
        reconciliation_status=ReconciliationStatus.COMPLETED,
    )


def _onenote(matter: Any, n: int) -> list[Any]:
    """A OneNote page linked to the Teema, its imported file, a later capture
    (page revision + file revision as a new document version) and a coverage mark."""
    from app.documents.services import add_evidence_version, create_document
    from app.legacy_import.onenote_coverage_models import (
        OneNoteCoverageMark,
        OneNotePageRevision,
        OneNoteResourceRevision,
    )
    from app.legacy_import.source_pages import (
        LegacySourcePage,
        LegacySourceResource,
        LegacySourceResourceImport,
        MatterSourcePage,
    )

    guid = f"00000000-0000-0000-0B01-{n:012d}"
    blocks = [{"ordinal": 0, "kind": "TEXT", "source_kind": "TEXT", "text": "Näidismärge"}]
    first = _batch(f"j1-cap-1-{n}")
    now = timezone.now()
    page = LegacySourcePage.objects.create(
        source_system="ONENOTE_DESKTOP",
        source_page_id="{" + guid + "}",
        page_key=guid,
        source_notebook="Näidismärkmik",
        source_section="Näidissektsioon",
        title=f"Näidisleht {n}",
        page_level=1,
        page_order=n,
        page_role="MATTER_LIKE",
        role_reason="sünteetiline",
        source_created_at=T_PAGE,
        source_modified_at=T_PAGE,
        capture_id=f"j1-cap-1-{n}",
        source_xml_storage_key="",
        source_xml_sha256="1" * 64,
        derived_text="Näidismärge",
        blocks=blocks,
        links=[],
        text_characters=11,
        block_count=1,
        file_count=1,
        file_bytes=8,
        first_imported_at=now,
        latest_imported_at=now,
        import_batch=first,
    )
    link = MatterSourcePage.objects.create(
        matter=matter,
        source_page=page,
        relationship_kind="PRIMARY",
        match_method="LOVABLE_EXACT_PAGE_GUID",
        match_class="EXACT",
        source_audit_reference="kontraktitest",
    )
    v1, v2 = f"%PDF-1.4 v1 {n}".encode(), f"%PDF-1.4 v2 {n}".encode()
    resource = LegacySourceResource.objects.create(
        source_page=page,
        resource_key=f"{{F{n}}}",
        original_filename="Naidis.pdf",
        source_block_ordinal=0,
        sha256=hashlib.sha256(v1).hexdigest(),
        size_bytes=len(v1),
        archive_relative_path=f"j1/{n}",
        source_added_at=T_PAGE,
        source_added_basis="ONENOTE_BLOCK_CREATED",
    )
    document = create_document(matter=matter, title="Naidis.pdf", role="LEGACY_MATERIAL")
    first_version = add_evidence_version(
        document=document, content=v1, original_filename="Naidis.pdf", mime_type="application/pdf"
    )
    imported = LegacySourceResourceImport.objects.create(
        matter_source_page=link,
        resource=resource,
        document=document,
        document_version=first_version,
        state="IMPORTED",
        import_batch=first,
    )
    later = _batch(f"j1-cap-2-{n}")
    revision = OneNotePageRevision.objects.create(
        page=page,
        import_batch=later,
        capture_id=f"j1-cap-2-{n}",
        manifest_sha256=later.source_snapshot_sha256,
        source_xml_storage_key="",
        source_xml_sha256="2" * 64,
        text_signature=f"sig-{n}",
        text_changed=True,
        title=f"Näidisleht {n}",
        source_section="Näidissektsioon",
        source_modified_at=T_LATER,
        derived_text="Näidismärge 2",
        blocks=blocks,
    )
    second_version = add_evidence_version(
        document=document, content=v2, original_filename="Naidis.pdf", mime_type="application/pdf"
    )
    file_revision = OneNoteResourceRevision.objects.create(
        matter_source_page=link,
        resource=resource,
        import_batch=later,
        capture_id=f"j1-cap-2-{n}",
        manifest_sha256=later.source_snapshot_sha256,
        kind="CHANGED_BYTES",
        state="VERSIONED",
        sha256=second_version.sha256,
        size_bytes=second_version.size_bytes,
        source_added_at=T_PAGE,
        block_modified_at=T_LATER,
        document=document,
        document_version=second_version,
    )
    mark = OneNoteCoverageMark.objects.create(
        scope="PAGE", page_guid=guid, state="IMPORTED", source_page=page
    )
    return [
        page,
        link,
        resource,
        document,
        first_version,
        imported,
        revision,
        second_version,
        file_revision,
        mark,
    ]


def add_everything(world: Any, reference: str, n: int, *, other: Any) -> list[Any]:
    """One of each Juristid-only record on ``reference``'s Teema; ``other`` is a
    second Teema (the related one, and the home of the background opinion)."""
    from app.intelligence.services import add_effective_date, add_important_date
    from app.matters.services import save_personal_note
    from app.related_materials.services import add_background_submission, link_related_matters
    from app.workflow.services import add_planned_action
    from tests import factories

    matter = world.matter(reference)
    lawyer = world.person("Uku Näidis")
    day = dt.date(2099, 4, 1)
    background = factories.SubmissionFactory(matter=other)
    relation, _ = link_related_matters(matter=matter, other=other, actor=lawyer)
    material, _ = add_background_submission(matter=matter, submission=background, actor=lawyer)
    records = [
        save_personal_note(matter=matter, author=lawyer, body="Minu oma märge"),
        factories.EntryFactory(matter=matter, author=lawyer),
        factories.SubmissionFactory(matter=matter),
        add_important_date(
            matter=matter, title="Oma tähtaeg", date_value=day, period_end=day, actor=lawyer
        ),
        add_effective_date(
            matter=matter,
            kind="KNOWN_DATE",
            date_value=dt.date(2099, 5, 1),
            period_end=dt.date(2099, 5, 1),
            description="Minu märge jõustumisest",
            actor=lawyer,
        ),
        relation,
        material,
        background,
        add_planned_action(
            matter=matter, text="Helistan ministeeriumi", target_date=day, actor=lawyer
        ),
        *_onenote(matter, n),
    ]
    return records


def import_provenance(matters: list[Any]) -> list[Any]:
    """The import's own records of the Teemad (`EvidencePilotRecord`): Juristid's
    evidence of where each value came from, never the synchronisation's to edit."""
    from app.legacy_import.models import EvidencePilotRecord

    return list(EvidencePilotRecord.objects.filter(matter__in=matters))


def audit_trail() -> list[Any]:
    """Every audit event that exists now: the trail is append-only."""
    from app.audit.models import ChangeEvent

    return list(ChangeEvent.objects.all())
