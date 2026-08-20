"""The historical corpus importer, against a synthetic archive.

Nothing real is read here. `tests/synthetic_historical.py` builds nine invented
OneNote pages and the migration audit that describes them, in a temporary
directory, and every assertion below is about behaviour that decides what
happens to somebody's 2019 case file (Stage-2D brief 78).

The cases are grouped by what they protect:

* **the gate** — a source that changed since the audit ran is refused
* **the plan** — which pages become Matters, and the four reasons one does not
* **the apply** — links, Matters, the review queue, and running it twice
* **materialisation** — bytes, hashes, and formats nothing can parse
* **reading** — the case file's order, and who is allowed to see it
"""

from __future__ import annotations

import hashlib
import shutil
from io import StringIO

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from app.core.enums import Visibility
from app.documents.enums import DocumentRole, ExtractionState
from app.legacy_import.historical_apply import (
    apply_structure,
    materialise_resources,
    open_batch,
    pending_materialisations,
)
from app.legacy_import.historical_audit import AuditError, MigrationAudit
from app.legacy_import.historical_plan import PlanError, build_plan, manifest_sha256
from app.legacy_import.onenote_archive import ArchiveError, OneNoteArchive
from app.legacy_import.source_pages import (
    CandidateClass,
    CandidateState,
    HistoricalMatchCandidate,
    LegacySourcePage,
    LegacySourceResourceImport,
    MatterSourcePage,
    ResourceImportState,
    SourceMatchMethod,
    SourceSystem,
)
from app.matters.enums import MatterOrigin, RecordMode
from app.matters.models import Matter
from tests import factories, synthetic_historical

pytestmark = pytest.mark.django_db


# -- the corpus ------------------------------------------------------------


@pytest.fixture
def corpus(tmp_path):
    return synthetic_historical.build_corpus(tmp_path / "source")


@pytest.fixture
def archive(corpus):
    return OneNoteArchive(corpus["archive_root"])


@pytest.fixture
def plan(corpus):
    return build_plan(
        excel_path=corpus["excel_path"],
        archive_root=corpus["archive_root"],
        audit_root=corpus["audit_root"],
    )


@pytest.fixture
def register(db):
    """The Excel Matters the audit's exact matches name. `2019_9` is absent."""
    return {
        reference: factories.MatterFactory(
            title=title,
            reference_year=2019,
            reference_number=int(reference.split("_")[1]),
            record_mode=RecordMode.ARCHIVE,
            origin=MatterOrigin.LEGACY_IMPORT,
        )
        for reference, title, _ in synthetic_historical.EXACT_MATCHES
        if reference != "2019_9"
    }


@pytest.fixture
def applied(plan, archive, register):
    batch = open_batch(plan, importer_version="test")
    return apply_structure(plan, batch=batch, archive=archive), batch


# -- reading the archive ---------------------------------------------------


def test_a_directory_without_the_marker_is_not_an_archive(tmp_path):
    """The Graph export has no `archive.json`, and importing it is forbidden.

    That export stored one page's HTML under 342 others and reported itself as
    PASS throughout, so the guard is a shape check rather than trust
    (Stage-2D brief 4).
    """
    (tmp_path / "onenote-juristid-export" / "pages").mkdir(parents=True)
    with pytest.raises(ArchiveError):
        OneNoteArchive(tmp_path / "onenote-juristid-export")


def test_blocks_keep_files_in_the_narrative_that_introduces_them(archive):
    page = archive.page("p-exact")
    kinds = [block.kind for block in page.blocks]
    assert kinds == ["TITLE", "TEXT", "TEXT", "FILE_ATTACHMENT", "LIST_ITEM"]

    introduction = page.blocks[2]
    attachment = page.blocks[3]
    assert "Ettepaneku eestikeelne variant" in introduction.text
    assert attachment.resource_key == "r-exact-1"
    assert attachment.ordinal > introduction.ordinal


def test_a_file_block_is_tied_to_its_resource_by_ordinal_not_by_name(archive):
    """Several real pages attach two files with the same name."""
    page = archive.page("p-signed")
    files = [block for block in page.blocks if block.is_file]
    assert [block.resource_key for block in files] == ["r-signed-1", "r-signed-2"]


def test_windows_separators_in_the_archive_resolve_on_this_host(archive):
    page = archive.page("p-exact")
    resource = page.resources[0]
    assert "\\" not in resource.relative_path
    content = archive.read_resource("p-exact", resource)
    assert hashlib.sha256(content).hexdigest() == resource.sha256


def test_the_audit_names_every_report_it_needs(tmp_path):
    (tmp_path / "half-an-audit").mkdir()
    with pytest.raises(AuditError) as error:
        MigrationAudit(tmp_path / "half-an-audit")
    assert "exact-matches.csv" in str(error.value)


# -- the gate --------------------------------------------------------------


def test_a_changed_register_refuses_to_plan(corpus):
    corpus["excel_path"].write_bytes(b"somebody edited the workbook")
    with pytest.raises(PlanError) as error:
        build_plan(
            excel_path=corpus["excel_path"],
            archive_root=corpus["archive_root"],
            audit_root=corpus["audit_root"],
        )
    assert "Excel SHA-256" in str(error.value)


def test_a_changed_archive_refuses_to_plan(corpus):
    """One byte in one attachment, and the manifest digest no longer matches."""
    target = next((corpus["archive_root"] / "pages/p-exact/resources").rglob("*.pdf"))
    target.write_bytes(target.read_bytes() + b"% edited\n")
    synthetic_historical.build_manifest(corpus["archive_root"], corpus["audit_root"])

    with pytest.raises(PlanError) as error:
        build_plan(
            excel_path=corpus["excel_path"],
            archive_root=corpus["archive_root"],
            audit_root=corpus["audit_root"],
        )
    assert "archive manifest" in str(error.value)


def test_the_manifest_digest_is_order_independent(corpus, tmp_path):
    """Sorted lines, LF, no trailing newline — the audit's own convention."""
    manifest = corpus["audit_root"] / "reports/source-integrity/archive-manifest.tsv"
    canonical = manifest_sha256(manifest)

    shuffled = tmp_path / "shuffled.tsv"
    lines = manifest.read_text(encoding="utf-8").strip("\n").split("\n")
    shuffled.write_text("\r\n".join(reversed(lines)) + "\r\n", encoding="utf-8")
    assert manifest_sha256(shuffled) == canonical


def test_the_plan_reconciles_against_the_audits_own_counts(plan):
    assert all("reconciles" in finding for finding in plan.findings)
    assert len(plan.source_pages) == 9
    assert len(plan.exact_links) == 4
    assert plan.resource_count == 6


# -- what becomes a Matter -------------------------------------------------


def test_an_unclaimed_substantive_page_becomes_a_matter(plan):
    becoming = {p.page.page_key for p in plan.onenote_only_matters}
    assert becoming == {"p-onenote-only", "p-signed"}


@pytest.mark.parametrize(
    ("page_key", "reason"),
    [
        ("p-container", "role is CATEGORY_OR_CONTAINER"),
        ("p-untitled", "untitled"),
        ("p-thin", "insubstantial"),
        ("p-candidate", "review candidate"),
        ("p-exact", "already exactly linked"),
    ],
)
def test_a_page_that_does_not_become_a_matter_says_why(plan, page_key, reason):
    page_plan = next(p for p in plan.source_pages if p.page.page_key == page_key)
    assert not page_plan.becomes_matter
    assert reason in page_plan.skip_reason


def test_the_plan_writes_nothing(plan, corpus):
    """A plan is a reading. Nothing in the database, nothing in the archive."""
    assert LegacySourcePage.objects.count() == 0
    assert Matter.objects.count() == 0
    digest = manifest_sha256(corpus["audit_root"] / "reports/source-integrity/archive-manifest.tsv")
    assert digest == plan.manifest_sha256


# -- applying --------------------------------------------------------------


def test_apply_creates_every_page_with_its_provenance(applied):
    report, _ = applied
    assert report.source_pages_created == 9
    assert report.resources_catalogued == 6

    page = LegacySourcePage.objects.get(page_key="p-exact")
    assert page.source_system == SourceSystem.ONENOTE_DESKTOP
    assert page.source_page_id == "1-aaaa1111aaaa1111aaaa1111aaaa1111"
    assert page.source_notebook == synthetic_historical.NOTEBOOK
    assert page.source_xml_sha256
    assert page.source_xml_storage_key


def test_an_exact_match_links_the_page_to_its_register_matter(applied, register):
    link = MatterSourcePage.objects.get(matter=register["2019_1"])
    assert link.source_page.page_key == "p-exact"
    assert link.match_method == SourceMatchMethod.EXCEL_EXACT_PAGE_ID
    assert link.source_audit_reference.startswith("exact-matches.csv:")


def test_one_page_may_belong_to_two_matters(applied, register):
    page = LegacySourcePage.objects.get(page_key="p-shared")
    owners = {link.matter_id for link in MatterSourcePage.objects.filter(source_page=page)}
    assert owners == {register["2019_2"].pk, register["2019_3"].pk}


def test_a_reference_the_register_does_not_contain_is_reported_not_invented(applied):
    report, _ = applied
    assert report.exact_links_unmatched == ["2019_9"]
    assert not Matter.objects.filter(reference_year=2019, reference_number=9).exists()


def test_a_onenote_only_matter_gets_no_register_reference(applied):
    matter = Matter.objects.get(title="Tolliprotseduuride töörühm")
    assert matter.reference_year is None
    assert matter.reference_number is None
    assert matter.origin == MatterOrigin.LEGACY_ONENOTE
    assert matter.record_mode == RecordMode.ARCHIVE
    assert matter.is_open is False


def test_every_undecided_candidate_lands_in_the_review_queue(applied):
    report, _ = applied
    assert report.candidates_created == 3
    assert set(HistoricalMatchCandidate.objects.values_list("candidate_class", flat=True)) == {
        CandidateClass.STRONG,
        CandidateClass.REVIEW_REQUIRED,
        CandidateClass.CONFLICT,
    }
    assert HistoricalMatchCandidate.objects.filter(state=CandidateState.PENDING).count() == 3


def test_applying_twice_changes_nothing(plan, archive, register, applied):
    before = (
        LegacySourcePage.objects.count(),
        MatterSourcePage.objects.count(),
        Matter.objects.count(),
        HistoricalMatchCandidate.objects.count(),
    )
    second = apply_structure(plan, batch=open_batch(plan, importer_version="test"), archive=archive)
    after = (
        LegacySourcePage.objects.count(),
        MatterSourcePage.objects.count(),
        Matter.objects.count(),
        HistoricalMatchCandidate.objects.count(),
    )
    assert before == after
    assert second.source_pages_created == 0
    assert second.source_pages_updated == 9
    assert second.onenote_matters_created == 0


def test_the_reading_order_ambiguity_survives_the_import(applied):
    assert LegacySourcePage.objects.get(page_key="p-ambiguous").reading_order_ambiguous
    assert not LegacySourcePage.objects.get(page_key="p-exact").reading_order_ambiguous


# -- materialising ---------------------------------------------------------


def test_materialising_stores_the_archives_own_bytes(applied, archive):
    report = materialise_resources(archive=archive, batch=applied[1])
    assert report.failures == []

    record = LegacySourceResourceImport.objects.get(resource__resource_key="r-exact-1")
    assert record.state == ResourceImportState.IMPORTED
    assert record.document.role == DocumentRole.LEGACY_MATERIAL
    assert record.document_version.sha256 == record.resource.sha256
    assert record.document_version.original_filename == "ettepanek.pdf"


def test_a_shared_page_materialises_into_both_matters(applied, archive, register):
    materialise_resources(archive=archive, batch=applied[1])
    copies = LegacySourceResourceImport.objects.filter(resource__resource_key="r-shared-1")
    assert copies.count() == 2
    assert {copy.document.matter_id for copy in copies} == {
        register["2019_2"].pk,
        register["2019_3"].pk,
    }
    # Deliberate duplication, readable as such: same source, same digest.
    assert len({copy.document_version.sha256 for copy in copies}) == 1


def test_a_signed_container_is_stored_and_never_parsed(applied, archive):
    """ASiC-E keeps its own bytes. Nothing unpacks it (Stage-2D brief 24)."""
    materialise_resources(archive=archive, batch=applied[1])
    record = LegacySourceResourceImport.objects.get(resource__resource_key="r-signed-1")
    version = record.document_version
    assert version.mime_type == "application/vnd.etsi.asic-e+zip"
    assert version.extraction_state == ExtractionState.NOT_APPLICABLE
    assert version.extraction_note


def test_materialising_resumes_rather_than_restarts(applied, archive):
    first = materialise_resources(archive=archive, batch=applied[1], limit=2)
    assert first.documents_created == 2
    remaining = len(pending_materialisations())

    second = materialise_resources(archive=archive, batch=applied[1])
    assert second.documents_created == remaining
    assert pending_materialisations() == []


def test_a_missing_original_fails_one_file_and_not_the_run(applied, archive, corpus):
    shutil.rmtree(corpus["archive_root"] / "pages/p-exact/resources/r-exact-1/original")
    report = materialise_resources(archive=archive, batch=applied[1])

    assert len(report.failures) == 1
    assert report.documents_created >= 5
    failed = LegacySourceResourceImport.objects.get(resource__resource_key="r-exact-1")
    assert failed.state == ResourceImportState.FAILED
    assert failed.error_code


# -- reading ---------------------------------------------------------------


def test_the_case_file_renders_in_source_order(applied, client, register, archive):
    materialise_resources(archive=archive, batch=applied[1])
    reader = factories.AdministratorFactory()
    client.force_login(reader)

    link = MatterSourcePage.objects.get(matter=register["2019_1"], source_page__page_key="p-exact")
    response = client.get(f"/ajalugu/{link.pk}/")
    assert response.status_code == 200

    body = response.content.decode()
    assert body.index("Ettepaneku eestikeelne variant") < body.index("ettepanek.pdf")
    # The page's own title is the heading, not the first line of the narrative.
    assert body.count("Pakendiseaduse muutmise eelnõu") >= 1
    assert synthetic_historical.ONLY_ON_EXACT_PAGE in body


def test_the_page_xml_is_offered_as_a_download_and_never_rendered(applied, client, register):
    reader = factories.AdministratorFactory()
    client.force_login(reader)
    link = MatterSourcePage.objects.get(matter=register["2019_1"], source_page__page_key="p-exact")

    page_response = client.get(f"/ajalugu/{link.pk}/")
    assert "one:Page" not in page_response.content.decode()

    download = client.get(f"/ajalugu/{link.pk}/lahtefail/")
    assert download.status_code == 200
    assert download["Content-Disposition"].startswith("attachment;")
    assert download["X-Content-Type-Options"] == "nosniff"


def test_a_historical_page_is_no_less_confidential_for_being_old(applied, client, register):
    """Read through the Matter's own scope, like everything else."""
    matter = register["2019_1"]
    matter.visibility = Visibility.RESTRICTED
    matter.owner = factories.UserFactory()
    matter.save(update_fields=["visibility", "owner"])

    link = MatterSourcePage.objects.get(matter=matter, source_page__page_key="p-exact")

    client.force_login(factories.UserFactory())
    assert client.get(f"/ajalugu/{link.pk}/").status_code == 404
    assert client.get(f"/ajalugu/{link.pk}/lahtefail/").status_code == 404

    client.force_login(matter.owner)
    assert client.get(f"/ajalugu/{link.pk}/").status_code == 200


def test_a_reviewer_can_link_a_candidate_and_clicking_twice_is_harmless(applied, client, register):
    reviewer = factories.AdministratorFactory()
    client.force_login(reviewer)
    candidate = HistoricalMatchCandidate.objects.get(candidate_class=CandidateClass.STRONG)
    candidate.matter = register["2019_1"]
    candidate.save(update_fields=["matter"])

    for _ in range(2):
        response = client.post(
            f"/haldus/ajaloo-ulevaatus/{candidate.pk}/",
            {"decision": "link", "note": "sama menetlus"},
        )
        assert response.status_code == 302

    candidate.refresh_from_db()
    assert candidate.state == CandidateState.LINKED
    assert candidate.decided_by_id == reviewer.pk
    assert (
        MatterSourcePage.objects.filter(
            matter=register["2019_1"], source_page=candidate.source_page
        ).count()
        == 1
    )


def test_a_reviewer_can_turn_a_page_into_its_own_matter_once(applied, client):
    client.force_login(factories.AdministratorFactory())
    candidate = HistoricalMatchCandidate.objects.get(candidate_class=CandidateClass.CONFLICT)

    for _ in range(2):
        client.post(f"/haldus/ajaloo-ulevaatus/{candidate.pk}/", {"decision": "create"})

    candidate.refresh_from_db()
    assert candidate.state == CandidateState.MATTER_CREATED
    assert (
        Matter.objects.filter(
            origin=MatterOrigin.LEGACY_ONENOTE, title=candidate.source_page.title
        ).count()
        == 1
    )


def test_the_review_queue_is_admin_work_not_legal_work(applied, client):
    client.force_login(factories.AdministratorFactory())
    response = client.get("/haldus/ajaloo-ulevaatus/")
    assert response.status_code == 200
    assert response.context["total"] == 3
    assert response.context["nav_active"] == "haldus"


def test_a_lawyer_cannot_reach_the_review_queue_by_typing_its_address(applied, client):
    """Unlinked is not the same as unreachable, and this route creates Matters."""
    client.force_login(factories.UserFactory())
    assert client.get("/haldus/ajaloo-ulevaatus/").status_code == 404

    candidate = HistoricalMatchCandidate.objects.first()
    assert (
        client.post(f"/haldus/ajaloo-ulevaatus/{candidate.pk}/", {"decision": "create"}).status_code
        == 404
    )
    candidate.refresh_from_db()
    assert candidate.state == CandidateState.PENDING


def test_a_historical_page_is_searchable_as_its_own_row(applied, register):
    from app.search.models import SearchDocument, SearchSourceKind
    from app.search.services import search

    reader = factories.AdministratorFactory()
    assert SearchDocument.objects.filter(source_kind=SearchSourceKind.LEGACY_SOURCE_PAGE).exists()

    results = search(query=synthetic_historical.ONLY_ON_ONENOTE_ONLY_PAGE, user=reader)
    kinds = {result.source_kind for result in results}
    assert SearchSourceKind.LEGACY_SOURCE_PAGE in kinds


# -- the command -----------------------------------------------------------


def _command_paths(corpus: dict) -> list[str]:
    return [
        "--excel",
        str(corpus["excel_path"]),
        "--archive",
        str(corpus["archive_root"]),
        "--audit",
        str(corpus["audit_root"]),
    ]


def test_the_importer_refuses_to_run_without_the_real_data_gate(corpus, settings):
    """`REAL_DATA_ALLOWED` is the switch, and it is off everywhere but the host."""
    settings.REAL_DATA_ALLOWED = False
    with pytest.raises(CommandError) as error:
        call_command("historical_import", "apply", *_command_paths(corpus))
    assert "REAL_DATA_ALLOWED" in str(error.value)
    assert LegacySourcePage.objects.count() == 0


def test_inspect_and_plan_do_not_need_the_gate(corpus, settings):
    settings.REAL_DATA_ALLOWED = False
    out = StringIO()
    call_command("historical_import", "plan", *_command_paths(corpus), stdout=out)
    assert "Historical corpus plan" in out.getvalue()
    assert LegacySourcePage.objects.count() == 0


def test_a_dry_run_exercises_apply_and_keeps_nothing(corpus, settings, register):
    settings.REAL_DATA_ALLOWED = True
    out = StringIO()
    call_command("historical_import", "dry-run", *_command_paths(corpus), stdout=out)
    assert "source pages" in out.getvalue()
    assert LegacySourcePage.objects.count() == 0
    assert not Matter.objects.filter(origin=MatterOrigin.LEGACY_ONENOTE).exists()
