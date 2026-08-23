"""A fictional department, built to exercise every state a statistic can meet.

Every name, title, filename and organisation below is invented. Nothing real
from Koda, its members or the register may appear in this repository, and
nothing does (master specification 5.3, 23.5).

The world is small and deliberate rather than large and plausible. Each record
exists because some metric has a branch that nothing else would reach:

* a **native** open Matter, a **register** archive row, a **OneNote-only**
  archive row whose year came from a page timestamp, and one with no year at
  all — the four ways a Matter relates to the corpus;
* a Matter with **two** source pages, and one with none;
* an **unclassified** Matter, an **ownerless** one, one with **no stage** and one
  with **no next action** — the four "unknown is data" buckets;
* a **restricted** Matter that carries one of everything, so that any aggregate
  which forgets to authorize is off by a number the tests can name;
* six file occurrences covering **imported, still to copy, empty in the source
  and copy failed**, plus a duplicate SHA-256 so occurrences and unique contents
  differ by exactly one;
* extraction versions that are **done, queued, failed, not applicable** and one
  **waiting on a malware scanner**, which is none of those;
* reconciliation candidates in every class the review queue knows.

Years are relative to the day a test runs, so nothing here starts failing in
January.

Two extensions sit at the bottom of this module and are **not** built by
``build_world``: ``add_responsibility_world`` adds register-backed responsibility
and ``add_archive_world`` adds an opinions archive. Both are opt-in because the
suite's expectations are derived by hand from the list above, and because the
shared world's *absence* of an archive is itself the state the empty-archive
tests need (brief 76).
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from typing import Any

from django.utils import timezone

from app.accounts.enums import UserRole
from app.accounts.models import User
from app.core.enums import Visibility
from app.documents.enums import DocumentRole, ExtractionState, MalwareScanState
from app.documents.models import Document, DocumentVersion
from app.legacy_import.source_pages import (
    CandidateClass,
    CandidateState,
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
from app.matters.entry_enums import EntryKind
from app.matters.enums import DataQualityTier, MatterOrigin, RecordMode
from app.matters.models import Entry, Matter, TagAssignment
from app.organisations.models import Organisation, OrganisationType
from app.submissions.enums import RecipientRole, SubmissionKind, SubmissionStatus
from app.submissions.models import Submission, SubmissionRecipient
from app.taxonomy.models import PolicyArea, Tag
from app.workflow.enums import ActionKind, ActionStatus, DateSemantics, Disposition, Track
from app.workflow.models import NextAction, StageVocabulary

NOTEBOOK = "Näidiskoja õigusloome"
SECTION_TAX = "ARHIIV maksud ja toll"
SECTION_ENV = "ARHIIV keskkond"
#: A section that exists *only* on a page behind the restricted Matter, so a
#: filter picker offering it would be naming confidential material to somebody
#: who may not open a single page in it.
SECTION_RESTRICTED = "ARHIIV liikmed"

MINISTRY = "Näidisministeerium"
COMMITTEE = "Näidiskomisjon"
PARTNER = "Näidisliit"

#: The one word that appears only inside restricted material. A test asserting
#: that an unauthorized reader never sees it is more convincing than one
#: asserting a count, because it also covers labels, chips and CSV cells.
RESTRICTED_ONLY_WORD = "hoiustamiskulu"


@dataclass
class World:
    """Named handles onto everything the fixture built."""

    today: date

    sandra: User
    martin: User
    head: User
    admin: User

    ministry: Organisation
    committee: Organisation
    partner: Organisation

    area_tax: PolicyArea
    area_env: PolicyArea
    stage: StageVocabulary
    tag: Tag

    native_open: Matter
    native_quiet: Matter
    native_waiting: Matter
    native_monitor: Matter
    native_future: Matter
    native_closed: Matter
    restricted: Matter
    archive_excel: Matter
    archive_excel_no_source: Matter
    onenote_only: Matter
    onenote_only_unknown_year: Matter
    multi_page: Matter

    page_primary: LegacySourcePage
    page_second: LegacySourcePage
    page_third: LegacySourcePage
    page_ambiguous: LegacySourcePage
    page_restricted: LegacySourcePage

    resources: dict[str, LegacySourceResource] = field(default_factory=dict)
    versions: dict[str, DocumentVersion] = field(default_factory=dict)
    submissions: dict[str, Submission] = field(default_factory=dict)

    # -- years, derived so nothing breaks in January -----------------------

    @property
    def current_year(self) -> int:
        return self.today.year

    @property
    def previous_year(self) -> int:
        return self.today.year - 1

    @property
    def archive_year(self) -> int:
        return self.today.year - 8

    @property
    def visible_to_martin(self) -> list[Matter]:
        """Everything except the restricted Matter Sandra owns."""
        return [
            self.native_open,
            self.native_quiet,
            self.native_waiting,
            self.native_monitor,
            self.native_future,
            self.native_closed,
            self.archive_excel,
            self.archive_excel_no_source,
            self.onenote_only,
            self.onenote_only_unknown_year,
            self.multi_page,
        ]


def _sha(payload: str) -> str:
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _at(day: date) -> datetime:
    return timezone.make_aware(datetime.combine(day, time(9, 0)))


def _user(upn: str, name: str, role: str) -> User:
    return User.objects.create(upn=upn, display_name=name, role=role, is_synthetic=True)


def _version(
    document: Document, *, filename: str, payload: str, **overrides: Any
) -> DocumentVersion:
    """One evidence version, written straight to the table.

    Deliberately not through ``add_evidence_version``: that service stores bytes
    first and needs a temporary evidence directory, which every statistics test
    would then have to request. Nothing here reads the bytes — these rows exist
    to be counted — so the fixture writes the metadata and says so. Tests that
    care about the storage contract live in ``test_evidence_integrity``.
    """
    defaults: dict[str, Any] = {
        "version_number": 1,
        "storage_key": f"synthetic/{document.pk}/{filename}",
        "original_filename": filename,
        "mime_type": "application/octet-stream",
        "size_bytes": len(payload),
        "sha256": _sha(payload),
        "acquired_at": timezone.now(),
        "malware_scan_state": MalwareScanState.CLEAN,
        "extraction_state": ExtractionState.PENDING,
    }
    defaults.update(overrides)
    version = DocumentVersion.objects.create(document=document, **defaults)
    Document.objects.filter(pk=document.pk).update(current_version=version)
    return version


def _document(matter: Matter, title: str, role: str = DocumentRole.LEGACY_MATERIAL) -> Document:
    return Document.objects.create(matter=matter, title=title, role=role)


def _page(
    *,
    key: str,
    title: str,
    section: str,
    created: date,
    order: int,
    ambiguous: bool = False,
    text: str = "",
) -> LegacySourcePage:
    now = timezone.now()
    narrative = text or (
        "Näidisministeerium saatis eelnõu kooskõlastusringile. Koda juhtis "
        "tähelepanu rakendusaja pikkusele ja üleminekusätete puudumisele."
    )
    return LegacySourcePage.objects.create(
        source_system=SourceSystem.ONENOTE_DESKTOP,
        source_page_id=f"1-stat{key}",
        page_key=f"stat-{key}",
        source_notebook=NOTEBOOK,
        source_section=section,
        title=title,
        page_level=2,
        page_order=order,
        page_role=SourcePageRole.MATTER_LIKE,
        source_created_at=_at(created),
        source_modified_at=_at(created),
        capture_id=f"stat-capture-{key}",
        source_xml_sha256=_sha(f"xml-{key}"),
        derived_text=narrative,
        blocks=[{"kind": "TEXT", "ordinal": 1, "text": narrative}],
        reading_order_ambiguous=ambiguous,
        reading_order_strategy="VISUAL_THEN_XML",
        text_characters=len(narrative),
        block_count=1,
        first_imported_at=now,
        latest_imported_at=now,
    )


def _link(matter: Matter, page: LegacySourcePage, method: str) -> MatterSourcePage:
    return MatterSourcePage.objects.create(
        matter=matter,
        source_page=page,
        relationship_kind=SourceRelationshipKind.PRIMARY,
        match_method=method,
        match_class=SourceMatchClass.EXACT,
        source_audit_reference=f"synthetic:{page.page_key}",
    )


def _resource(
    page: LegacySourcePage,
    *,
    key: str,
    filename: str,
    payload: str,
    ordinal: int,
    size: int | None = None,
) -> LegacySourceResource:
    return LegacySourceResource.objects.create(
        source_page=page,
        resource_key=key,
        original_filename=filename,
        source_block_ordinal=ordinal,
        sha256=_sha(payload),
        size_bytes=len(payload) if size is None else size,
        archive_relative_path=f"resources/{key}/original/{filename}",
    )


def _action(
    matter: Matter, *, kind: str, semantics: str, target: date | None, text: str
) -> NextAction:
    return NextAction.objects.create(
        matter=matter,
        text=text,
        kind=kind,
        date_semantics=semantics,
        target_date=target,
        status=ActionStatus.OPEN,
    )


def _submission(
    matter: Matter, *, title: str, sent_on: date | None, kind: str = SubmissionKind.FORMAL_OPINION
) -> Submission:
    """A Submission, with the evidence its SENT state requires.

    The database refuses a sent submission without ``sent_at`` and a final
    version — a sent opinion with no text is an unverifiable claim about what
    Koda argued — so the fixture supplies both rather than working around it.
    """
    if sent_on is None:
        return Submission.objects.create(
            matter=matter, title=title, kind=kind, status=SubmissionStatus.DRAFT
        )

    document = _document(matter, f"{title} (lõplik)", role=DocumentRole.KODA_SUBMISSION_FINAL)
    version = _version(
        document,
        filename=f"{title.lower().replace(' ', '-')}.pdf",
        payload=f"arvamus-{title}",
        extraction_state=ExtractionState.DONE,
    )
    return Submission.objects.create(
        matter=matter,
        title=title,
        kind=kind,
        status=SubmissionStatus.SENT,
        sent_at=_at(sent_on),
        final_version=version,
    )


def build_world(today: date | None = None) -> World:
    """Create the whole synthetic department. One call, one world."""
    today = today or timezone.localdate()
    current = today.year
    previous = current - 1
    archive = current - 8

    sandra = _user("sandra@example.invalid", "Sandra Testjurist", UserRole.SPECIALIST)
    martin = _user("martin@example.invalid", "Martin Testjurist", UserRole.SPECIALIST)
    head = _user("juht@example.invalid", "Testosakonnajuht", UserRole.DEPARTMENT_HEAD)
    admin = _user("admin@example.invalid", "Testadministraator", UserRole.ADMINISTRATOR)

    ministry = Organisation.objects.create(
        name=MINISTRY, organisation_type=OrganisationType.MINISTRY
    )
    committee = Organisation.objects.create(
        name=COMMITTEE, organisation_type=OrganisationType.PARLIAMENT
    )
    partner = Organisation.objects.create(
        name=PARTNER, organisation_type=OrganisationType.ASSOCIATION
    )

    area_tax = PolicyArea.objects.create(key="maksud", name_et="Maksud", sort_order=10)
    area_env = PolicyArea.objects.create(key="keskkond", name_et="Keskkond", sort_order=20)
    stage = StageVocabulary.objects.create(
        key="kooskolastusel", label_et="Kooskõlastusringil", is_provisional=True
    )
    tag = Tag.objects.create(key="halduskoormus", name_et="Halduskoormus")

    # -- native, operational ----------------------------------------------

    native_open = Matter.objects.create(
        title="Pakendiaruandluse katse-eelnõu",
        record_mode=RecordMode.FULL,
        origin=MatterOrigin.NATIVE,
        reference_year=current,
        reference_number=1,
        reporting_year=current,
        owner=sandra,
        stage=stage,
        track=Track.DOMESTIC,
        source_organisation=ministry,
        received_date=date(current, 2, 3),
        response_deadline=today + timedelta(days=10),
    )
    native_open.policy_areas.add(area_tax)
    TagAssignment.objects.create(matter=native_open, tag=tag)

    native_quiet = Matter.objects.create(
        title="Vaikne teema ilma vastutajata",
        record_mode=RecordMode.FULL,
        origin=MatterOrigin.NATIVE,
        reference_year=current,
        reference_number=2,
        reporting_year=current,
        received_date=date(current, 3, 1),
    )

    native_waiting = Matter.objects.create(
        title="Ootel olev keskkonnateema",
        record_mode=RecordMode.FULL,
        origin=MatterOrigin.NATIVE,
        reference_year=current,
        reference_number=3,
        reporting_year=current,
        owner=martin,
        stage=stage,
        track=Track.EU_INITIATIVE,
        addressee_organisation=ministry,
        received_date=date(current, 1, 15),
    )
    native_waiting.policy_areas.add(area_env)

    native_monitor = Matter.objects.create(
        title="Jälgitav strateegia",
        record_mode=RecordMode.FULL,
        origin=MatterOrigin.NATIVE,
        reference_year=current,
        reference_number=4,
        reporting_year=current,
        owner=martin,
        stage=stage,
        track=Track.STRATEGY,
    )

    native_future = Matter.objects.create(
        title="Tulevase tähtajaga teema",
        record_mode=RecordMode.FULL,
        origin=MatterOrigin.NATIVE,
        reference_year=current,
        reference_number=5,
        reporting_year=current,
        owner=sandra,
        stage=stage,
        response_deadline=today + timedelta(days=40),
    )

    native_closed = Matter.objects.create(
        title="Lõpetatud teema eelmisest aastast",
        record_mode=RecordMode.FULL,
        origin=MatterOrigin.NATIVE,
        reference_year=previous,
        reference_number=7,
        reporting_year=previous,
        owner=martin,
        stage=stage,
        is_open=False,
        disposition=Disposition.COMPLETED,
        closed_at=_at(date(previous, 12, 1)),
        closed_by=martin,
    )

    restricted = Matter.objects.create(
        title="Konfidentsiaalne liikmete tagasiside",
        record_mode=RecordMode.FULL,
        origin=MatterOrigin.NATIVE,
        reference_year=current,
        reference_number=6,
        reporting_year=current,
        owner=sandra,
        stage=stage,
        track=Track.DOMESTIC,
        source_organisation=partner,
        visibility=Visibility.RESTRICTED,
        received_date=date(current, 4, 4),
    )
    restricted.policy_areas.add(area_tax)

    # -- archive ----------------------------------------------------------

    archive_excel = Matter.objects.create(
        title="Aktsiisimäärade muutmine registrist",
        record_mode=RecordMode.ARCHIVE,
        origin=MatterOrigin.LEGACY_IMPORT,
        data_quality_tier=DataQualityTier.TIER_3_REGISTER_ARCHIVE,
        source_era=str(archive),
        reference_year=archive,
        reference_number=12,
        reporting_year=archive,
        source_organisation=ministry,
        is_open=False,
    )

    archive_excel_no_source = Matter.objects.create(
        title="Registririda ilma OneNote'i leheta",
        record_mode=RecordMode.ARCHIVE,
        origin=MatterOrigin.LEGACY_IMPORT,
        data_quality_tier=DataQualityTier.TIER_3_REGISTER_ARCHIVE,
        source_era=str(archive),
        reference_year=archive,
        reference_number=13,
        reporting_year=archive,
        is_open=False,
    )

    # The trap this whole module is careful about: `reporting_year` here is the
    # OneNote page's own timestamp, not a register reporting year. The Matter
    # therefore belongs in Teadmata aasta however plausible the number looks.
    onenote_only = Matter.objects.create(
        title="Alkoholiaktsiisi töörühma märkmed",
        record_mode=RecordMode.ARCHIVE,
        origin=MatterOrigin.LEGACY_ONENOTE,
        data_quality_tier=DataQualityTier.TIER_3_REGISTER_ARCHIVE,
        reporting_year=archive,
        is_open=False,
    )

    onenote_only_unknown_year = Matter.objects.create(
        title="Dateerimata OneNote'i teema",
        record_mode=RecordMode.ARCHIVE,
        origin=MatterOrigin.LEGACY_ONENOTE,
        data_quality_tier=DataQualityTier.TIER_4_UNVERIFIED,
        reporting_year=None,
        is_open=False,
    )

    multi_page = Matter.objects.create(
        title="Kahe lähtelehega arhiiviteema",
        record_mode=RecordMode.ARCHIVE,
        origin=MatterOrigin.LEGACY_IMPORT,
        source_era=str(previous),
        reference_year=previous,
        reference_number=44,
        reporting_year=previous,
        addressee_organisation=committee,
        is_open=False,
    )

    # -- next actions ------------------------------------------------------

    _action(
        native_open,
        kind=ActionKind.DO,
        semantics=DateSemantics.DEADLINE,
        target=today - timedelta(days=3),
        text="Saada arvamus ministeeriumile",
    )
    _action(
        native_future,
        kind=ActionKind.DO,
        semantics=DateSemantics.DEADLINE,
        target=today + timedelta(days=30),
        text="Valmista ette seisukoht",
    )
    _action(
        native_waiting,
        kind=ActionKind.WAIT,
        semantics=DateSemantics.REVIEW_ON,
        target=today,
        text="Ootan ministeeriumi vastust",
    )
    _action(
        native_monitor,
        kind=ActionKind.MONITOR,
        semantics=DateSemantics.REVIEW_ON,
        target=today,
        text="Jälgin menetluse käiku",
    )
    _action(
        restricted,
        kind=ActionKind.DO,
        semantics=DateSemantics.DEADLINE,
        target=today - timedelta(days=5),
        text="Konfidentsiaalne järgmine samm",
    )

    # -- entries -----------------------------------------------------------

    Entry.objects.create(
        matter=native_open,
        author=sandra,
        kind=EntryKind.MEETING,
        occurred_at=_at(date(current, 2, 10)),
        body="<p>Kohtumine ministeeriumi esindajatega.</p>",
    )
    Entry.objects.create(
        matter=native_open,
        author=sandra,
        kind=EntryKind.NOTE,
        occurred_at=_at(date(current, 2, 12)),
        body="<p>Sisemine märkus seisukoha kohta.</p>",
    )
    Entry.objects.create(
        matter=restricted,
        author=sandra,
        kind=EntryKind.NOTE,
        occurred_at=_at(date(current, 4, 5)),
        body=f"<p>Liikme konfidentsiaalne {RESTRICTED_ONLY_WORD}.</p>",
    )

    # -- submissions -------------------------------------------------------

    submissions = {
        "first": _submission(native_open, title="Esimene arvamus", sent_on=date(current, 3, 5)),
        "second": _submission(
            native_open,
            title="Taiendav arvamus",
            sent_on=date(current, 5, 6),
            kind=SubmissionKind.SUPPLEMENTARY_OPINION,
        ),
        "previous_year": _submission(
            native_closed, title="Eelmise aasta arvamus", sent_on=date(previous, 9, 9)
        ),
        "restricted": _submission(
            restricted, title="Konfidentsiaalne arvamus", sent_on=date(current, 4, 20)
        ),
        "draft": _submission(native_waiting, title="Koostamisel arvamus", sent_on=None),
    }

    SubmissionRecipient.objects.create(
        submission=submissions["first"], organisation=ministry, role=RecipientRole.ADDRESSEE
    )
    SubmissionRecipient.objects.create(
        submission=submissions["first"], organisation=committee, role=RecipientRole.FOR_INFORMATION
    )
    SubmissionRecipient.objects.create(
        submission=submissions["second"], organisation=committee, role=RecipientRole.ADDRESSEE
    )
    SubmissionRecipient.objects.create(
        submission=submissions["previous_year"],
        organisation=ministry,
        role=RecipientRole.ADDRESSEE,
    )
    SubmissionRecipient.objects.create(
        submission=submissions["restricted"], organisation=partner, role=RecipientRole.ADDRESSEE
    )

    # -- historical corpus -------------------------------------------------

    page_primary = _page(
        key="primary",
        title="Aktsiisimäärade eelnõu",
        section=SECTION_TAX,
        created=date(archive, 5, 4),
        order=1,
    )
    page_second = _page(
        key="second",
        title="Keskkonnatasude eelnõu",
        section=SECTION_ENV,
        created=date(previous, 3, 3),
        order=1,
    )
    page_third = _page(
        key="third",
        title="Keskkonnatasude lisamaterjal",
        section=SECTION_ENV,
        created=date(previous, 4, 4),
        order=2,
    )
    page_ambiguous = _page(
        key="ambiguous",
        title="Töörühma märkmed",
        section=SECTION_TAX,
        created=date(archive, 7, 7),
        order=2,
        ambiguous=True,
    )
    page_restricted = _page(
        key="restricted",
        title="Liikme materjalid",
        section=SECTION_RESTRICTED,
        created=date(current, 4, 4),
        order=3,
        text=f"Konfidentsiaalne {RESTRICTED_ONLY_WORD} liikme kohta.",
    )

    link_primary = _link(archive_excel, page_primary, SourceMatchMethod.EXCEL_EXACT_PAGE_ID)
    link_second = _link(multi_page, page_second, SourceMatchMethod.EXCEL_EXACT_PAGE_ID)
    _link(multi_page, page_third, SourceMatchMethod.REVIEWED_MATCH)
    _link(onenote_only, page_ambiguous, SourceMatchMethod.ONENOTE_ONLY_MATTER)
    link_restricted = _link(restricted, page_restricted, SourceMatchMethod.MANUAL)

    resources = {
        "pdf": _resource(
            page_primary, key="r-pdf", filename="eelnou.pdf", payload="pdf-bytes", ordinal=2
        ),
        "msg": _resource(
            page_primary, key="r-msg", filename="kiri.msg", payload="msg-bytes", ordinal=3
        ),
        "asice": _resource(
            page_primary, key="r-asice", filename="seisukoht.asice", payload="asice", ordinal=4
        ),
        "empty": _resource(
            page_primary, key="r-empty", filename="tyhi.docx", payload="", ordinal=5, size=0
        ),
        "broken": _resource(
            page_primary, key="r-broken", filename="katki.docx", payload="katki", ordinal=6
        ),
        "eml": _resource(
            page_primary, key="r-eml", filename="teade.eml", payload="eml-bytes", ordinal=7
        ),
        # The same bytes as `pdf`, on a different page. Two occurrences, one
        # unique content — which is exactly the pair of numbers the Ajalooline
        # materjal tab has to keep apart.
        "duplicate": _resource(
            page_second, key="r-dup", filename="eelnou-koopia.pdf", payload="pdf-bytes", ordinal=2
        ),
        "bdoc": _resource(
            page_second, key="r-bdoc", filename="allkirjastatud.bdoc", payload="bdoc", ordinal=3
        ),
        "restricted": _resource(
            page_restricted, key="r-conf", filename="liige.pdf", payload="conf", ordinal=2
        ),
    }

    versions: dict[str, DocumentVersion] = {}

    def materialise(link: MatterSourcePage, name: str, **version_kwargs: Any) -> None:
        resource = resources[name]
        document = _document(link.matter, resource.original_filename)
        version = _version(
            document,
            filename=resource.original_filename,
            payload=f"{name}-payload",
            **version_kwargs,
        )
        versions[name] = version
        LegacySourceResourceImport.objects.create(
            matter_source_page=link,
            resource=resource,
            document=document,
            document_version=version,
            state=ResourceImportState.IMPORTED,
        )

    materialise(link_primary, "pdf", extraction_state=ExtractionState.DONE)
    materialise(link_primary, "msg", extraction_state=ExtractionState.PENDING)
    materialise(
        link_primary,
        "asice",
        extraction_state=ExtractionState.NOT_APPLICABLE,
        extraction_note="Allkirjastatud ümbrik. Sisu ei avata ega indekseerita.",
    )
    materialise(link_second, "duplicate", extraction_state=ExtractionState.FAILED)
    materialise(
        link_second,
        "bdoc",
        extraction_state=ExtractionState.NOT_APPLICABLE,
        extraction_note="Allkirjastatud ümbrik.",
    )
    materialise(link_restricted, "restricted", extraction_state=ExtractionState.DONE)

    # A file that is zero bytes *in OneNote itself*. The evidence service
    # refuses to store an empty file — correctly — so there is a record of the
    # attempt with no document behind it, and the state is "empty in the
    # source", never "copying for ever" (main, commit 3888afd).
    LegacySourceResourceImport.objects.create(
        matter_source_page=link_primary,
        resource=resources["empty"],
        document=None,
        document_version=None,
        state=ResourceImportState.SKIPPED,
        error_code="EMPTY_SOURCE",
    )
    # A copy that genuinely failed: the file has bytes and did not arrive.
    LegacySourceResourceImport.objects.create(
        matter_source_page=link_primary,
        resource=resources["broken"],
        document=None,
        document_version=None,
        state=ResourceImportState.FAILED,
        error_code="COPY_FAILED",
    )
    # `eml` gets no import row at all: still to copy.

    # One version waiting on a malware scanner. With REAL_DATA_ALLOWED the
    # extractor may not open it, so it is neither queued nor failed — a
    # distinction the Andmekvaliteet tab has to make (main, commit 34d91b1).
    gated_document = _document(native_open, "Skannimata manus")
    versions["gated"] = _version(
        gated_document,
        filename="skannimata.pdf",
        payload="gated",
        malware_scan_state=MalwareScanState.PENDING,
        extraction_state=ExtractionState.PENDING,
    )

    # -- reconciliation queue ---------------------------------------------

    for candidate_class, page, reference in (
        (CandidateClass.STRONG, page_primary, f"{archive}_12"),
        (CandidateClass.REVIEW_REQUIRED, page_second, f"{previous}_44"),
        (CandidateClass.CONFLICT, page_third, f"{previous}_45"),
        (CandidateClass.UNLINKED_PAGE, page_ambiguous, ""),
        (CandidateClass.BROKEN_EXCEL_LINK, None, f"{archive}_99"),
    ):
        HistoricalMatchCandidate.objects.create(
            source_page=page,
            matter=None,
            excel_reference=reference,
            excel_title="Näidispealkiri auditist",
            candidate_class=candidate_class,
            score=0.5,
            match_signals="sünteetiline",
            state=CandidateState.PENDING,
        )

    return World(
        today=today,
        sandra=sandra,
        martin=martin,
        head=head,
        admin=admin,
        ministry=ministry,
        committee=committee,
        partner=partner,
        area_tax=area_tax,
        area_env=area_env,
        stage=stage,
        tag=tag,
        native_open=native_open,
        native_quiet=native_quiet,
        native_waiting=native_waiting,
        native_monitor=native_monitor,
        native_future=native_future,
        native_closed=native_closed,
        restricted=restricted,
        archive_excel=archive_excel,
        archive_excel_no_source=archive_excel_no_source,
        onenote_only=onenote_only,
        onenote_only_unknown_year=onenote_only_unknown_year,
        multi_page=multi_page,
        page_primary=page_primary,
        page_second=page_second,
        page_third=page_third,
        page_ambiguous=page_ambiguous,
        page_restricted=page_restricted,
        resources=resources,
        versions=versions,
        submissions=submissions,
    )


# ---------------------------------------------------------------------------
# Statistics 2.0 — two opt-in extensions
#
# Deliberately *not* part of ``build_world``. The existing suite reads its
# expectations off the eleven Matters Martin can see, and quietly adding rows to
# the shared world would silently change a dozen assertions that were derived by
# hand rather than by running the code.
#
# It also leaves the shared world in the state the archive tests most need: with
# **no** ``OpinionArchiveItem`` at all, which is what this branch ships into
# before P3 populates production and is exactly where a false "0 arvamust
# saadetud" would appear (brief 60, 76).
# ---------------------------------------------------------------------------


#: A lawyer the register names and this system has no account for. The whole
#: point of the source-responsibility rule is that this string survives.
HISTORICAL_NAME = "Mari Ajalooline"


@dataclass
class ResponsibilityWorld:
    """Handles onto the register-responsibility extension."""

    #: A retired register row whose VASTUTAJA names somebody with no account.
    historical: Matter
    #: Open FULL work the register still names HISTORICAL_NAME on. The case that
    #: must not collapse into "Määramata".
    promoted_named: Matter
    #: Open FULL work whose VASTUTAJA cell is genuinely blank.
    promoted_blank: Matter


def _source_reference(matter: Matter, *, year: int, owner_cell: str) -> Any:
    """One minimal verbatim register row, enough to hang a state off."""
    from app.legacy_import.models import MatterSourceReference

    return MatterSourceReference.objects.create(
        matter=matter,
        source_system="EXCEL_REGISTER",
        source_file_name="Naidisregister.xlsx",
        source_snapshot_sha256=_sha("statistika-register-snapshot"),
        source_sheet=str(year),
        source_row_number=matter.reference_number,
        source_row_raw={"VASTUTAJA": owner_cell},
        source_title=matter.title,
        source_era=str(year),
    )


def _register_state(matter: Matter, *, year: int, owner_cell: str, currency: str) -> None:
    from app.legacy_import.current_state import CurrentRegisterState

    reference = _source_reference(matter, year=year, owner_cell=owner_cell)
    CurrentRegisterState.objects.create(
        matter=matter,
        source_reference=reference,
        source_snapshot_sha256=_sha("statistika-register-snapshot"),
        source_sheet=str(year),
        source_row_number=reference.source_row_number,
        currency=currency,
        status_label="Kooskolastusringil" if currency == "CURRENT" else "Loppenud",
        opinion_sent_recorded=False,
        owner_raw=owner_cell,
        # False on purpose for the named rows: the register names somebody this
        # system has no account for, which is the fact the whole precedence rule
        # exists to preserve (Stage-2F owner resolver).
        owner_resolved=False,
        observed_at=timezone.now(),
    )


def add_responsibility_world(world: World) -> ResponsibilityWorld:
    """Register-backed responsibility, in the three shapes that differ.

    A retired archive row and an open FULL row both naming a colleague with no
    account here, and one open FULL row whose cell is blank. Together they
    separate the two failures the source-responsibility rule exists to prevent:
    a named historical lawyer disappearing into *Määramata*, and a genuinely
    unassigned Matter being given somebody's name.
    """
    from app.legacy_import.current_state import RegisterCurrency

    previous = world.previous_year

    _register_state(
        world.archive_excel,
        year=world.archive_year,
        owner_cell=HISTORICAL_NAME,
        currency=RegisterCurrency.RETIRED,
    )

    promoted_named = Matter.objects.create(
        title="Registrist aktiveeritud teema nimelise vastutajaga",
        record_mode=RecordMode.FULL,
        origin=MatterOrigin.PROMOTED_LEGACY,
        data_quality_tier=DataQualityTier.TIER_1_VERIFIED_ACTIVE,
        source_era=str(previous),
        reference_year=previous,
        reference_number=61,
        reporting_year=previous,
        # No canonical owner: the register names somebody and the resolver could
        # not match them to an account. Exactly the case that must not read as
        # unassigned.
        owner=None,
        stage=world.stage,
        is_open=True,
    )
    _register_state(
        promoted_named,
        year=previous,
        owner_cell=HISTORICAL_NAME,
        currency=RegisterCurrency.CURRENT,
    )

    promoted_blank = Matter.objects.create(
        title="Registrist aktiveeritud teema vastutajata",
        record_mode=RecordMode.FULL,
        origin=MatterOrigin.PROMOTED_LEGACY,
        data_quality_tier=DataQualityTier.TIER_1_VERIFIED_ACTIVE,
        source_era=str(previous),
        reference_year=previous,
        reference_number=62,
        reporting_year=previous,
        owner=None,
        is_open=True,
    )
    _register_state(
        promoted_blank,
        year=previous,
        owner_cell="",
        currency=RegisterCurrency.CURRENT,
    )

    return ResponsibilityWorld(
        historical=world.archive_excel,
        promoted_named=promoted_named,
        promoted_blank=promoted_blank,
    )


#: The archive's own first year. Nothing in the fixture is dated earlier, so a
#: test can assert that no earlier bar was drawn.
ARCHIVE_BASE_YEAR = 2020


@dataclass
class ArchiveWorld:
    """Handles onto the opinion-archive extension.

    ``duplicate_sha`` is the same bytes filed at a second path: two occurrences,
    one distinct file. Every trend here counts the second number and the
    inventory metric counts the first, and a fixture where the two were equal
    could not tell whether the code knew the difference (brief 27, 70).
    """

    batch: Any
    #: sha256 to the binary row, for the links the tests assert on.
    binaries: dict[str, Any]
    #: The file linked to two Matters with different responsibility labels.
    multi_linked_sha: str
    #: The file linked only to the restricted Matter.
    restricted_sha: str
    #: The file with no Matter link at all.
    unlinked_sha: str
    duplicate_sha: str
    #: The most recent filename date in the fixture. Every same-cutoff
    #: comparison is derived from this rather than from today.
    cutoff: date


def _archive_item(batch: Any, *, when: date, title: str, payload: str, path: str = "") -> Any:
    from app.legacy_import.opinion_archive import OpinionArchiveItem

    digest = _sha(payload)
    filename = f"{when.isoformat()} - {MINISTRY} - {title}.pdf"
    return OpinionArchiveItem.objects.create(
        batch=batch,
        archive_sha256=batch.archive_sha256,
        archive_relative_path=path or f"Arvamused/{filename}",
        original_filename=filename,
        sha256=digest,
        size_bytes=len(payload),
        detected_type="application/pdf",
        filename_date=when,
        filename_recipient=MINISTRY,
        filename_title=title,
    )


def _archive_binary(payload: str) -> Any:
    from app.legacy_import.opinion_binary import OpinionArchiveBinary

    digest = _sha(payload)
    return OpinionArchiveBinary.objects.create(
        sha256=digest,
        size_bytes=len(payload),
        mime_type="application/pdf",
        storage_key=f"synthetic/opinion-archive/{digest}",
        source_archive_sha256=_sha("statistika-arvamuste-arhiiv"),
        materialized_at=timezone.now(),
    )


def _archive_link(binary: Any, matter: Matter) -> None:
    from app.legacy_import.opinion_binary import OpinionArchiveMatterLink
    from app.legacy_import.opinion_enums import ArchiveLinkBasis

    OpinionArchiveMatterLink.objects.create(
        binary=binary,
        matter=matter,
        basis=ArchiveLinkBasis.EXACT_BINARY,
        linked_at=timezone.now(),
    )


def add_archive_world(
    world: World,
    *,
    cutoff: date | None = None,
    letters: list[tuple[str, date]] | None = None,
) -> ArchiveWorld:
    """A small opinions archive with every shape a trend has to handle.

    Fixed calendar years rather than years relative to today, because the
    archive's own era boundary is a fixed 2020 and a fixture that drifted past it
    would stop testing the boundary. The comparison cutoff is passed in and
    defaults to a mid-year date, so the same-cutoff test has a partial year to
    measure against a fuller one.

    ``letters`` replaces the default set outright, for the one case the default
    cannot express: an archive whose entire history sits inside a single year,
    so that the previous comparable period is genuinely empty rather than merely
    small (brief 73).
    """
    from app.legacy_import.opinion_archive import OpinionArchiveBatch

    cutoff = cutoff or date(ARCHIVE_BASE_YEAR + 3, 6, 30)
    batch = OpinionArchiveBatch.objects.create(
        archive_sha256=_sha("statistika-arvamuste-arhiiv"),
        archive_file_name="Arvamused-naidis.zip",
        importer_version="synthetic/1",
        started_at=timezone.now(),
    )

    # Two in the base year, one in the next, then a previous year that runs past
    # the cutoff month and a current year that stops at it — which is what makes
    # the same-cutoff comparison assertable.
    letters = letters or [
        ("kiri-a", date(ARCHIVE_BASE_YEAR, 3, 4)),
        ("kiri-b", date(ARCHIVE_BASE_YEAR, 11, 20)),
        ("kiri-c", date(ARCHIVE_BASE_YEAR + 1, 5, 5)),
        # Previous year, inside the comparable window.
        ("kiri-d", date(ARCHIVE_BASE_YEAR + 2, 2, 2)),
        ("kiri-e", date(ARCHIVE_BASE_YEAR + 2, 4, 4)),
        # Previous year, after the cutoff month. Must never enter the
        # comparison's denominator (brief 72).
        ("kiri-f", date(ARCHIVE_BASE_YEAR + 2, 9, 9)),
        ("kiri-g", date(ARCHIVE_BASE_YEAR + 2, 12, 12)),
        # Current year, up to the cutoff.
        ("kiri-h", date(ARCHIVE_BASE_YEAR + 3, 1, 15)),
        ("kiri-i", date(ARCHIVE_BASE_YEAR + 3, 3, 3)),
        ("kiri-j", cutoff),
    ]

    binaries: dict[str, Any] = {}
    for payload, when in letters:
        _archive_item(batch, when=when, title=f"Arvamus {payload}", payload=payload)
        binaries[_sha(payload)] = _archive_binary(payload)

    if not {"kiri-a", "kiri-h", "kiri-i", "kiri-d"} <= {payload for payload, _ in letters}:
        # A caller-supplied set has none of the shapes below, so there is
        # nothing to duplicate and nothing to link.
        return ArchiveWorld(
            batch=batch,
            binaries=binaries,
            multi_linked_sha="",
            restricted_sha="",
            unlinked_sha="",
            duplicate_sha="",
            cutoff=cutoff,
        )

    # The same bytes filed at a second path, dated later. Occurrences: 11.
    # Distinct files: 10. The trend counts the *earlier* date, so this second
    # occurrence must not move a bar (see ``archive._distinct_dates``).
    _archive_item(
        batch,
        when=date(ARCHIVE_BASE_YEAR + 1, 8, 8),
        title="Arvamus kiri-a koopia",
        payload="kiri-a",
        path="Arvamused/koopiad/kiri-a.pdf",
    )

    # One file on two Matters with different responsibility labels, one file on
    # the restricted Matter only, and several with no link at all.
    _archive_link(binaries[_sha("kiri-h")], world.native_open)
    _archive_link(binaries[_sha("kiri-h")], world.native_waiting)
    _archive_link(binaries[_sha("kiri-i")], world.native_open)
    _archive_link(binaries[_sha("kiri-d")], world.restricted)

    return ArchiveWorld(
        batch=batch,
        binaries=binaries,
        multi_linked_sha=_sha("kiri-h"),
        restricted_sha=_sha("kiri-d"),
        unlinked_sha=_sha("kiri-c"),
        duplicate_sha=_sha("kiri-a"),
        cutoff=cutoff,
    )
