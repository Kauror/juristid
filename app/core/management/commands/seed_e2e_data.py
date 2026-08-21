"""A deterministic world for the browser suite.

Named, stable personas and exactly the records the E2E scenarios assert on. The
browser tests have no database access on purpose: if they could query around the
interface, an authorization bug in the interface would not fail them.

Synthetic only, and refuses to run anywhere that claims to hold real data.
"""

from __future__ import annotations

import hashlib
from datetime import date, timedelta
from typing import Any

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from app.accounts.enums import UserRole
from app.accounts.models import User
from app.accounts.services import create_synthetic_user
from app.core.enums import Visibility
from app.matters.entry_enums import EntryKind
from app.matters.enums import DataQualityTier, MatterOrigin, RecordMode
from app.matters.models import Matter
from app.matters.services import add_entry, create_matter
from app.organisations.models import Organisation, OrganisationType
from app.taxonomy.models import PolicyArea
from app.workflow.enums import ActionKind, DateSemantics, Track
from app.workflow.models import StageVocabulary
from app.workflow.services import set_next_action

PERSONAS = [
    ("sandra@example.invalid", "Sandra Testjurist", UserRole.SPECIALIST, False),
    ("martin@example.invalid", "Martin Testjurist", UserRole.SPECIALIST, False),
    ("juht@example.invalid", "Testosakonnajuht", UserRole.DEPARTMENT_HEAD, False),
    ("admin@example.invalid", "Testadministraator", UserRole.ADMINISTRATOR, True),
]

MINISTRY = "Näidisministeerium"
RESTRICTED_TITLE = "Konfidentsiaalne liikmete tagasiside"
OPEN_TITLE = "Tavaline avatud teema kõigile nähtav"
ARCHIVE_TITLE = "Arhiiviteema 2014 sünteetiline registrikirje"

#: The historical world. One page attached to a normal Matter, so the case-file
#: rendering has something to render, and one undecided candidate, so the
#: reconciliation queue is not empty (Stage-2D brief 79).
HISTORICAL_PAGE_TITLE = "Pakendiseaduse muutmise eelnõu 2019"
HISTORICAL_INTRODUCTION = "Ettepaneku eestikeelne variant:"
#: A signed container, not a PDF. Two reasons: it is the commonest thing on a
#: real 2019 page, and it exercises the path where a file is marked unparseable
#: at import time rather than leaving the extraction worker to discover that a
#: synthetic stub is not a readable document (Stage-2D brief 24).
HISTORICAL_FILENAME = "seisukoht-2019.asice"
CANDIDATE_PAGE_TITLE = "Alkoholiaktsiisi töörühma märkmed"

#: The Statistika world. Three records the tabs need in order to say anything
#: at all: something genuinely late, something formally sent, and an
#: unclassified Matter so the *Klassifitseerimata* bucket is not empty
#: (Stage-2E brief 69).
OVERDUE_TITLE = "Tähtaja ületanud sünteetiline teema"
SUBMISSION_TITLE = "Sünteetiline arvamus ministeeriumile"
#: A signed container, not a PDF, and for two reasons. It is what a Chamber
#: opinion actually goes out as; and a synthetic stub with a `.pdf` extension is
#: a *broken* PDF, which the extraction worker correctly reports as a failure —
#: putting a false entry in the Andmekvaliteet queue the browser suite asserts
#: is empty. Found by the first CI round.
SUBMISSION_FILENAME = "arvamus-2026.asice"


class Command(BaseCommand):
    help = "Create the deterministic synthetic world the Playwright suite asserts against."

    @transaction.atomic
    def handle(self, *args: Any, **options: Any) -> None:
        if settings.REAL_DATA_ALLOWED:
            raise CommandError("REAL_DATA_ALLOWED is set; refusing to seed synthetic data.")

        people = {}
        for upn, name, role, is_staff in PERSONAS:
            person = User.objects.filter(upn=upn).first()
            if person is None:
                person = create_synthetic_user(
                    upn=upn, display_name=name, role=role, is_staff=is_staff
                )
            people[upn] = person

        sandra = people["sandra@example.invalid"]
        martin = people["martin@example.invalid"]

        ministry, _ = Organisation.objects.get_or_create(
            name=MINISTRY, defaults={"organisation_type": OrganisationType.MINISTRY}
        )
        area, _ = PolicyArea.objects.get_or_create(
            key="keskkond", defaults={"name_et": "Keskkond", "sort_order": 10}
        )
        stage = StageVocabulary.objects.get(key="consultation")

        if Matter.objects.filter(title=RESTRICTED_TITLE).exists():
            self.stdout.write("E2E world already present.")
            return

        # A restricted Matter Sandra owns. Martin must never reach it, and the
        # administrator must not reach it either.
        restricted = create_matter(
            title=RESTRICTED_TITLE,
            actor=sandra,
            owner=sandra,
            stage=stage,
            track=Track.DOMESTIC,
            source_organisation=ministry,
            visibility=Visibility.RESTRICTED,
        )
        add_entry(
            matter=restricted,
            body="<p>Liikme konfidentsiaalne seisukoht pakendiaktsiisi kohta.</p>",
            author=sandra,
            kind=EntryKind.NOTE,
        )
        set_next_action(
            matter=restricted,
            text="Konfidentsiaalne järgmine samm",
            kind=ActionKind.DO,
            date_semantics=DateSemantics.DEADLINE,
            target_date=date.today() + timedelta(days=4),
            actor=sandra,
        )

        # A normal Matter, so the register is not empty for Martin.
        visible = create_matter(
            title=OPEN_TITLE,
            actor=martin,
            owner=martin,
            stage=stage,
            track=Track.DOMESTIC,
            source_organisation=ministry,
        )
        visible.policy_areas.add(area)
        add_entry(
            matter=visible,
            body="<p>Avalik sissekanne, mida kõik näevad.</p>",
            author=martin,
            occurred_at=timezone.now() - timedelta(days=1),
        )
        set_next_action(
            matter=visible,
            text="Jälgi menetluse käiku",
            kind=ActionKind.MONITOR,
            date_semantics=DateSemantics.REVIEW_ON,
            target_date=date.today() + timedelta(days=30),
            actor=martin,
        )

        # An archive row, so the register proves FULL and ARCHIVE coexist.
        create_matter(
            title=ARCHIVE_TITLE,
            assign_reference=False,
            record_mode=RecordMode.ARCHIVE,
            origin=MatterOrigin.LEGACY_IMPORT,
            data_quality_tier=DataQualityTier.TIER_3_REGISTER_ARCHIVE,
            source_era="2014",
            reporting_year=2014,
        )

        self._historical_world(visible)
        self._statistics_world(visible, martin, ministry)
        self.stdout.write(self.style.SUCCESS("E2E world ready."))

    def _statistics_world(self, visible: Matter, martin: Any, ministry: Any) -> None:
        """The records the Statistika tabs assert on.

        Kept to three, and each one is there because a tab would otherwise show
        an honest but untestable emptiness: a metric with no records at all
        reports *insufficient data*, which is correct and proves nothing about
        the page (Stage-2E brief 69).
        """
        from app.submissions.services import (
            attach_final_evidence,
            create_submission,
            mark_submission_sent,
        )

        overdue = create_matter(
            title=OVERDUE_TITLE,
            actor=martin,
            owner=martin,
            track=Track.DOMESTIC,
            source_organisation=ministry,
            received_date=date.today() - timedelta(days=40),
        )
        set_next_action(
            matter=overdue,
            text="Saada arvamus ministeeriumile",
            kind=ActionKind.DO,
            date_semantics=DateSemantics.DEADLINE,
            target_date=date.today() - timedelta(days=3),
            actor=martin,
        )

        submission = create_submission(
            matter=visible,
            title=SUBMISSION_TITLE,
            actor=martin,
            recipients=[ministry],
        )
        attach_final_evidence(
            submission=submission,
            content=bytes([80, 75, 3, 4]) + b" synthetic e2e signed opinion",
            original_filename=SUBMISSION_FILENAME,
            mime_type="application/vnd.etsi.asic-e+zip",
            actor=martin,
        )
        mark_submission_sent(submission=submission, actor=martin, channel="EIS")

    def _historical_world(self, matter: Matter) -> None:
        """A OneNote page, its file, and one decision nobody has made yet.

        Built through the ORM rather than through the importer. The importer has
        its own suite against a synthetic archive; what the browser suite is for
        is whether a lawyer can read a 2019 case file and whether an operator can
        settle a match — and neither of those needs 4 GiB of source material to
        be true (Stage-2D brief 79).
        """
        from app.documents.enums import DocumentRole, ExtractionState, MalwareScanState
        from app.documents.models import DocumentVersion
        from app.documents.services import add_evidence_version, create_document
        from app.legacy_import.source_pages import (
            CandidateClass,
            HistoricalMatchCandidate,
            LegacySourcePage,
            LegacySourceResource,
            LegacySourceResourceImport,
            MatterSourcePage,
            ResourceImportState,
            SourceMatchClass,
            SourceMatchMethod,
            SourcePageRole,
            SourceRelationshipKind,
            SourceSystem,
        )

        if LegacySourcePage.objects.filter(title=HISTORICAL_PAGE_TITLE).exists():
            return

        content = bytes([80, 75, 3, 4]) + b" synthetic e2e ASiC-E container"
        now = timezone.now()
        blocks = [
            {
                "kind": "TEXT",
                "ordinal": 1,
                "text": (
                    "Näidisministeerium saatis eelnõu kooskõlastusringile. "
                    "Koda juhtis tähelepanu rakendusaja pikkusele."
                ),
            },
            {"kind": "TEXT", "ordinal": 2, "text": HISTORICAL_INTRODUCTION},
            {"kind": "FILE_ATTACHMENT", "ordinal": 3, "resource_key": "e2e-resource-1"},
            {
                "kind": "LIST_ITEM",
                "ordinal": 4,
                "depth": 1,
                "text": "Ministeeriumi vastus saabus kuu hiljem.",
            },
        ]
        page = LegacySourcePage.objects.create(
            source_system=SourceSystem.ONENOTE_DESKTOP,
            source_page_id="1-e2e0000000000000000000000000001",
            page_key="e2e-page-1",
            source_notebook="Näidiskoja õigusloome",
            source_section="ARHIIV 2019",
            title=HISTORICAL_PAGE_TITLE,
            page_level=2,
            page_order=1,
            page_role=SourcePageRole.MATTER_LIKE,
            role_reason="sünteetiline e2e maailm",
            source_created_at=now,
            source_modified_at=now,
            capture_id="e2e-capture-1",
            source_xml_sha256="0" * 64,
            derived_text=" ".join(str(block.get("text", "")) for block in blocks),
            blocks=blocks,
            links=[{"url": "https://eelnoud.example.invalid/19-0001", "displayText": "EIS"}],
            reading_order_strategy="VISUAL_THEN_XML",
            text_characters=200,
            block_count=len(blocks),
            file_count=1,
            file_bytes=len(content),
            first_imported_at=now,
            latest_imported_at=now,
        )
        resource = LegacySourceResource.objects.create(
            source_page=page,
            resource_key="e2e-resource-1",
            original_filename=HISTORICAL_FILENAME,
            resource_kind="FILE_ATTACHMENT",
            source_block_ordinal=3,
            sha256=hashlib.sha256(content).hexdigest(),
            size_bytes=len(content),
            archive_relative_path="resources/e2e-resource-1/original/" + HISTORICAL_FILENAME,
        )
        link = MatterSourcePage.objects.create(
            matter=matter,
            source_page=page,
            relationship_kind=SourceRelationshipKind.PRIMARY,
            match_method=SourceMatchMethod.EXCEL_EXACT_PAGE_ID,
            match_class=SourceMatchClass.EXACT,
            source_audit_reference="exact-matches.csv:e2e",
        )

        document = create_document(
            matter=matter,
            title=HISTORICAL_FILENAME,
            role=DocumentRole.LEGACY_MATERIAL,
            provenance_note="OneNote: ARHIIV 2019 → " + HISTORICAL_PAGE_TITLE,
        )
        version = add_evidence_version(
            document=document,
            content=content,
            original_filename=HISTORICAL_FILENAME,
            mime_type="application/vnd.etsi.asic-e+zip",
            acquired_at=now,
            source_identifier="e2e-page-1/e2e-resource-1",
            malware_scan_state=MalwareScanState.PENDING,
        )
        # Nothing will ever parse a signed container, so it is marked here
        # rather than left PENDING in a queue for ever.
        DocumentVersion.objects.filter(pk=version.pk).update(
            extraction_state=ExtractionState.NOT_APPLICABLE,
            extraction_note="Allkirjastatud ümbrik. Sisu ei avata ega indekseerita.",
        )
        LegacySourceResourceImport.objects.create(
            matter_source_page=link,
            resource=resource,
            document=document,
            document_version=version,
            state=ResourceImportState.IMPORTED,
        )

        candidate_page = LegacySourcePage.objects.create(
            source_system=SourceSystem.ONENOTE_DESKTOP,
            source_page_id="1-e2e0000000000000000000000000002",
            page_key="e2e-page-2",
            source_notebook="Näidiskoja õigusloome",
            source_section="ARHIIV 2019",
            title=CANDIDATE_PAGE_TITLE,
            page_level=2,
            page_order=2,
            page_role=SourcePageRole.MATTER_LIKE,
            source_created_at=now,
            source_modified_at=now,
            capture_id="e2e-capture-2",
            source_xml_sha256="1" * 64,
            derived_text="Töörühma märkmed alkoholiaktsiisi kohta.",
            blocks=[{"kind": "TEXT", "ordinal": 1, "text": "Töörühma märkmed."}],
            reading_order_strategy="VISUAL_THEN_XML",
            text_characters=40,
            block_count=1,
            first_imported_at=now,
            latest_imported_at=now,
        )
        HistoricalMatchCandidate.objects.create(
            source_page=candidate_page,
            matter=matter,
            excel_reference="2019_44",
            excel_title="Alkoholiaktsiisi eelnõu",
            candidate_class=CandidateClass.REVIEW_REQUIRED,
            score=0.44,
            match_signals="pealkirja osaline kattuvus; sama aasta",
            explanation="review-required.csv:2019_44",
        )
        # A second candidate, in a class nothing decides, and on a *different*
        # page. Two things depend on that: the review-required one above is
        # settled by `test_an_operator_settles_a_match`, so the statistics suite
        # needs one no other test consumes; and that test finds its card by the
        # page title, so a second card carrying the same title makes its
        # locator ambiguous. Two register rows pointing at one page is what a
        # CONFLICT is, so this is where it belongs anyway.
        HistoricalMatchCandidate.objects.create(
            source_page=page,
            matter=matter,
            excel_reference="2019_45",
            excel_title="Alkoholiaktsiisi eelnõu, teine rida",
            candidate_class=CandidateClass.CONFLICT,
            score=0.51,
            match_signals="kaks rida osutavad samale lehele",
            conflicts="2019_44 vs 2019_45",
            explanation="conflicts.csv:2019_45",
        )
