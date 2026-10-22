"""An approved import applies exactly what was approved (docs/adr/0184).

Two findings of the 2026-10-22 round-2 review:

* **csf_566209e1736d4e8706e9eccd** — `import_legacy_register --apply` rebuilt a
  fresh plan from whatever the workbook path, the mapping file and the database
  held at apply time; the dry run's hash bound nothing.
* **csf_ff21dec71f0f7bfecdef6502** — the evidence pilot's package digest covered
  `package.json` only, and the historical import compared a manifest file with
  a claim about it; page text, metadata, blocks, links, XML, resource records
  and the audit's decisions were read without being covered by any approval.

Each test changes one thing the approval must cover, after the plan an
operator would have reviewed, and shows the apply refuses and writes nothing.
Everything is synthetic and built in ``tmp_path``.
"""

from __future__ import annotations

import hashlib
import io
import json
import shutil
import unicodedata
from pathlib import Path

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from app.audit.enums import SecurityEventType
from app.audit.models import SecurityAuditEvent
from app.legacy_import import evidence_pilot, integrity
from app.legacy_import.evidence_pilot import EvidencePilotError, apply_plan, build_plan
from app.legacy_import.integrity import (
    IntegrityError,
    ManifestEntry,
    SourceManifest,
    build_manifest,
    canonical_sha256,
)
from app.legacy_import.models import EvidencePilotRecord, ImportBatch, MatterSourceReference
from app.legacy_import.onenote_archive import ArchiveError, OneNoteArchive
from app.matters.enums import MatterOrigin, RecordMode
from app.matters.models import Matter
from tests import factories, synthetic_historical
from tests.synthetic_register import Row, Sheet, write_workbook
from tests.test_evidence_pilot import _package, _write, _write_archive

pytestmark = pytest.mark.django_db


# ===========================================================================
# The integrity layer itself
# ===========================================================================


def test_the_canonical_digest_ignores_key_order_and_names_rows_by_identity(db):
    user = factories.UserFactory()
    assert canonical_sha256({"a": 1, "b": [user]}) == canonical_sha256({"b": [user], "a": 1})
    other = factories.UserFactory()
    assert canonical_sha256({"b": [user]}) != canonical_sha256({"b": [other]})
    with pytest.raises(TypeError, match="no canonical form"):
        canonical_sha256({"a": object()})


def test_two_names_for_one_file_are_refused():
    nfc = unicodedata.normalize("NFC", "Märkus.txt")
    nfd = unicodedata.normalize("NFD", "Märkus.txt")
    for pair in (("Kiri.pdf", "kiri.pdf"), (nfc, nfd)):
        with pytest.raises(IntegrityError, match="one name on a case-insensitive"):
            SourceManifest(ManifestEntry(name, 1, "0" * 64) for name in pair)


def test_a_verified_read_refuses_changed_unlisted_and_vanished_files(tmp_path):
    root = synthetic_historical.build_archive(tmp_path / "archive")
    manifest = build_manifest(root)
    archive = OneNoteArchive(root, manifest=manifest)
    archive.page("p-exact")  # as approved: reads

    text = next(root.glob("pages/p-exact/captures/*/page.txt"))
    text.write_text(text.read_text(encoding="utf-8") + " muudetud", encoding="utf-8")
    with pytest.raises(IntegrityError, match="changed after the source manifest"):
        archive.page("p-exact")

    text.unlink()
    with pytest.raises(IntegrityError, match="no longer on disk"):
        archive.page("p-exact")

    planted = root / "pages/p-shared/resources/r-planted"
    planted.mkdir(parents=True)
    (planted / "resource.json").write_text("{}", encoding="utf-8")
    with pytest.raises(IntegrityError, match="not in the approved source manifest"):
        archive.page("p-shared")


def test_page_xml_that_does_not_hash_to_its_claim_is_refused(tmp_path):
    root = synthetic_historical.build_archive(tmp_path / "archive")
    archive = OneNoteArchive(root)
    page = archive.page("p-exact")
    assert archive.read_page_xml(page)
    Path(page.xml_path).write_bytes(b"<one:Page>another page</one:Page>")
    with pytest.raises(ArchiveError, match="does not hash to the xmlSha256"):
        archive.read_page_xml(archive.page("p-exact"))


def test_a_manifest_tsv_is_held_to_the_source_path_rule():
    with pytest.raises(IntegrityError, match="unsafe source path"):
        integrity.parse_manifest_tsv(f"../outside.pdf\t1\t{'0' * 64}\n", what="m.tsv")
    with pytest.raises(IntegrityError, match="not path, size and SHA-256"):
        integrity.parse_manifest_tsv("a.pdf\t1\n", what="m.tsv")
    parsed = integrity.parse_manifest_tsv(f"pages\\p\\a.pdf\t3\t{'a' * 64}\r\n", what="m.tsv")
    assert "pages/p/a.pdf" in parsed


# ===========================================================================
# csf_566209e1 — the register import
# ===========================================================================


@pytest.fixture
def register_workbook(tmp_path: Path) -> Path:
    return write_workbook(
        tmp_path / "register.xlsx",
        [
            Sheet(
                2026,
                [
                    Row(reference="2026_1", title="Näidisseaduse eelnõu", owner="Kadri"),
                    Row(reference="2026_2", title="Näidismääruse eelnõu", owner="Kadri"),
                ],
            )
        ],
    )


@pytest.fixture
def kadri(db):
    return factories.UserFactory(display_name="Kadri", upn="kadri@example.invalid")


def _register(path: Path, *args: str) -> str:
    out = io.StringIO()
    call_command(
        "import_legacy_register",
        str(path),
        *args,
        "--report-dir",
        str(path.parent / "reports"),
        stdout=out,
    )
    return out.getvalue()


def _dry_run_digest(path: Path, *args: str) -> str:
    out = _register(path, "--dry-run", *args)
    line = next(line for line in out.splitlines() if "plan sha256" in line)
    return line.split()[-1]


def _apply_register(path: Path, digest: str | None, *args: str) -> str:
    extra = ["--expect-plan-sha256", digest] if digest is not None else []
    return _register(path, "--apply", "--accept-review-rows", *extra, *args)


def _nothing_imported() -> None:
    assert not ImportBatch.objects.exists()
    assert not MatterSourceReference.objects.exists()
    assert not Matter.all_objects.exists()


def test_the_register_apply_needs_the_reviewed_plan_digest(register_workbook, kadri, settings):
    settings.REAL_DATA_ALLOWED = True
    with pytest.raises(CommandError, match="needs --expect-plan-sha256"):
        _apply_register(register_workbook, None)
    _nothing_imported()


def test_a_workbook_changed_after_the_dry_run_is_refused(register_workbook, kadri, settings):
    settings.REAL_DATA_ALLOWED = True
    digest = _dry_run_digest(register_workbook)
    write_workbook(
        register_workbook,
        [Sheet(2026, [Row(reference="2026_1", title="Teine pealkiri", owner="Kadri")])],
    )
    with pytest.raises(CommandError, match="changed since it was approved"):
        _apply_register(register_workbook, digest)
    _nothing_imported()


def test_a_mapping_file_changed_after_the_dry_run_is_refused(register_workbook, kadri, settings):
    settings.REAL_DATA_ALLOWED = True
    mapping = register_workbook.parent / "mapping.toml"
    mapping.write_text('[owners]\n"Kadri" = "kadri@example.invalid"\n', encoding="utf-8")
    digest = _dry_run_digest(register_workbook, "--mapping-file", str(mapping))
    mapping.write_text('[owners]\n"Kadri" = "kadri@example.invalid"\n# muudetud\n', "utf-8")
    with pytest.raises(CommandError, match="changed since it was approved"):
        _apply_register(register_workbook, digest, "--mapping-file", str(mapping))
    _nothing_imported()


def test_a_matter_created_after_the_dry_run_changes_the_plan(register_workbook, kadri, settings):
    """Same bytes, different database: the reviewed decision was a create, not a match."""
    settings.REAL_DATA_ALLOWED = True
    digest = _dry_run_digest(register_workbook)
    factories.MatterFactory(
        title="Näidisseaduse eelnõu",
        reference_year=2026,
        reference_number=1,
        record_mode=RecordMode.ARCHIVE,
        origin=MatterOrigin.LEGACY_IMPORT,
    )
    with pytest.raises(CommandError, match="changed since it was approved"):
        _apply_register(register_workbook, digest)
    assert not ImportBatch.objects.exists()
    assert Matter.all_objects.count() == 1


def test_the_reviewed_plan_applies_and_its_digest_is_recorded(register_workbook, kadri, settings):
    settings.REAL_DATA_ALLOWED = True
    digest = _dry_run_digest(register_workbook)
    _apply_register(register_workbook, digest)

    batch = ImportBatch.objects.get()
    assert f"plan-sha256 {digest}" in batch.notes
    event = SecurityAuditEvent.objects.get(event_type=SecurityEventType.IMPORT_RUN)
    assert event.detail["plan_sha256"] == digest
    assert Matter.all_objects.count() == 2


def test_the_workbook_hash_is_the_hash_of_the_bytes_parsed(register_workbook, monkeypatch):
    """One read: the identity recorded is the identity of what openpyxl loaded."""
    from app.legacy_import import parser

    seen = []
    real = parser.load_workbook

    def record(source, **kwargs):
        seen.append(source)
        return real(source, **kwargs)

    monkeypatch.setattr(parser, "load_workbook", record)
    with parser.RegisterWorkbook(register_workbook) as workbook:
        assert workbook.sha256 == hashlib.sha256(register_workbook.read_bytes()).hexdigest()
    assert len(seen) == 1 and isinstance(seen[0], io.BytesIO)
    assert hashlib.sha256(seen[0].getvalue()).hexdigest() == workbook.sha256


# ===========================================================================
# csf_ff21dec7 — the evidence pilot
# ===========================================================================


@pytest.fixture
def package_root(tmp_path: Path) -> Path:
    root = tmp_path / "package"
    _write_archive(root / evidence_pilot.ARCHIVE_DIRECTORY)
    return _write(root, _package())


@pytest.fixture
def world(db):
    factories.UserFactory(upn="mari@example.invalid", display_name="Mari Näidis")
    factories.UserFactory(upn="jaan@example.invalid", display_name="Jaan Näidis")
    for name in ("Näidisministeerium", "Riigikogu näidiskomisjon"):
        factories.OrganisationFactory(name=name)


@pytest.fixture
def pilot_on(settings):
    settings.EVIDENCE_PILOT_ENABLED = True
    settings.REAL_DATA_ALLOWED = True


def _apply_pilot(plan, digest):
    return apply_plan(
        plan,
        operator_intent=evidence_pilot.OPERATOR_INTENT,
        expect_package_sha256=plan.digest,
        expect_plan_sha256=digest,
        backup_set="sets/20250331T000000Z",
    )


ARCHIVE = evidence_pilot.ARCHIVE_DIRECTORY


def _page_text(root: Path) -> None:
    path = root / ARCHIVE / "pages/p-one/captures/c1/page.txt"
    path.write_text("Lehe tekst muudeti pärast ülevaatust.", encoding="utf-8")


def _resource_record(root: Path) -> None:
    path = root / ARCHIVE / "pages/p-one/resources/r001/resource.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["originalFilename"] = "Teine nimi.pdf"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _blocks(root: Path) -> None:
    path = root / ARCHIVE / "pages/p-one/captures/c1/blocks.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["blocks"][0]["text"] = "Teine lõik"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _skipped_original(root: Path) -> None:
    # r004 is accounted for but never imported, so nothing re-hashed it before.
    (root / ARCHIVE / "pages/p-one/resources/r004/original/tühi.docx").write_bytes(b"PK")


def _unexpected_file(root: Path) -> None:
    (root / ARCHIVE / "pages/p-two/captures/c1/notes.txt").write_text("lisatud", "utf-8")


@pytest.mark.parametrize(
    "change", [_page_text, _resource_record, _blocks, _skipped_original, _unexpected_file]
)
def test_a_package_file_outside_package_json_changed_after_the_plan_is_refused(
    world, package_root, pilot_on, change
):
    plan = build_plan(package_root)
    approved = evidence_pilot.plan_digest(plan)
    package_digest = plan.digest
    change(package_root)

    # The package digest alone — the only approval before — cannot see it.
    assert build_plan(package_root).digest == package_digest
    with pytest.raises(EvidencePilotError, match="changed since it was approved"):
        _apply_pilot(plan, approved)
    assert not Matter.all_objects.exists()
    assert not EvidencePilotRecord.objects.exists()


def test_an_altered_decision_changes_the_plan_digest(world, package_root, pilot_on):
    plan = build_plan(package_root)
    approved = evidence_pilot.plan_digest(plan)
    package = _package()
    package["topics"][0]["title"] = "Muudetud pealkiri"
    _write(package_root, package)
    with pytest.raises(EvidencePilotError, match="changed since it was approved"):
        _apply_pilot(plan, approved)
    assert not Matter.all_objects.exists()


def test_a_catalogue_change_after_the_plan_changes_the_plan_digest(world, package_root, pilot_on):
    plan = build_plan(package_root)
    approved = evidence_pilot.plan_digest(plan)
    factories.UserFactory(upn="mari2@example.invalid", display_name="Mari Teine")
    with pytest.raises(EvidencePilotError, match="changed since it was approved"):
        _apply_pilot(plan, approved)
    assert not Matter.all_objects.exists()


def test_page_xml_that_is_not_its_claim_blocks_validation(world, package_root):
    xml = package_root / ARCHIVE / "pages/p-one/captures/c1/page.source.xml"
    xml.write_bytes(b'<?xml version="1.0"?><one:Page ID="muu"/>')
    problems = build_plan(package_root).problems
    assert any("does not hash to the xmlSha256" in problem for problem in problems)


def test_the_reviewed_plan_applies_records_its_digests_and_reapplies_as_nothing(
    world, package_root, pilot_on
):
    plan = build_plan(package_root)
    approved = evidence_pilot.plan_digest(plan)
    report = _apply_pilot(plan, approved)
    assert not report.already_applied

    batch = ImportBatch.objects.get(source_system=evidence_pilot.SOURCE_SYSTEM)
    assert f"plan-sha256 {approved}" in batch.notes
    assert plan.manifest is not None and plan.manifest.digest() in batch.notes
    # The import's identity in provenance is still the package digest.
    assert set(EvidencePilotRecord.objects.values_list("package_sha256", flat=True)) == {
        plan.digest
    }

    again = build_plan(package_root)
    assert again.database_state == "applied"
    rows = EvidencePilotRecord.objects.count()
    assert _apply_pilot(again, approved).already_applied
    assert EvidencePilotRecord.objects.count() == rows


def test_the_manifest_is_never_written_into_the_package(package_root, tmp_path):
    with pytest.raises(CommandError, match="outside the package"):
        call_command(
            "evidence_pilot",
            "validate",
            "--package",
            str(package_root),
            "--manifest-out",
            str(package_root / "manifest.tsv"),
            stdout=io.StringIO(),
        )
    assert not (package_root / "manifest.tsv").exists()
    out = tmp_path / "record" / "manifest.tsv"
    out.parent.mkdir()
    call_command(
        "evidence_pilot",
        "validate",
        "--package",
        str(package_root),
        "--manifest-out",
        str(out),
        "--json",
        stdout=io.StringIO(),
    )
    lines = out.read_text(encoding="utf-8").splitlines()
    assert len(lines) == len(build_manifest(package_root))
    assert lines[0].count("\t") == 2


# ===========================================================================
# csf_ff21dec7 — the historical corpus import
# ===========================================================================


@pytest.fixture
def corpus(tmp_path):
    return synthetic_historical.build_corpus(tmp_path / "source")


@pytest.fixture
def register(db):
    for reference, title, _ in synthetic_historical.EXACT_MATCHES:
        if reference != "2019_9":
            factories.MatterFactory(
                title=title,
                reference_year=2019,
                reference_number=int(reference.split("_")[1]),
                record_mode=RecordMode.ARCHIVE,
                origin=MatterOrigin.LEGACY_IMPORT,
            )


def _historical(phase: str, corpus: dict, *extra: str) -> str:
    out = io.StringIO()
    call_command(
        "historical_import",
        phase,
        "--excel",
        str(corpus["excel_path"]),
        "--archive",
        str(corpus["archive_root"]),
        "--audit",
        str(corpus["audit_root"]),
        *extra,
        stdout=out,
    )
    return out.getvalue()


def _historical_digest(corpus: dict) -> str:
    line = next(line for line in _historical("plan", corpus).splitlines() if "plan SHA-256" in line)
    return line.split()[-1]


def _plan(corpus):
    from app.legacy_import.historical_plan import build_plan as build_historical_plan

    return build_historical_plan(
        excel_path=corpus["excel_path"],
        archive_root=corpus["archive_root"],
        audit_root=corpus["audit_root"],
    )


def test_an_archive_file_the_audit_manifest_does_not_hold_is_refused(corpus):
    """The manifest file still hashes to the audit's record; the disk does not match it."""
    from app.legacy_import.historical_plan import PlanError

    original = next((corpus["archive_root"] / "pages/p-exact/resources").rglob("*.pdf"))
    original.write_bytes(original.read_bytes() + b"% muudetud\n")
    with pytest.raises(PlanError, match="changed: pages/p-exact/resources"):
        _plan(corpus)


def test_an_unexpected_archive_file_is_refused(corpus):
    from app.legacy_import.historical_plan import PlanError

    (corpus["archive_root"] / "pages/p-exact/extra.txt").write_text("lisa", encoding="utf-8")
    with pytest.raises(PlanError, match=r"not in the manifest: pages/p-exact/extra.txt"):
        _plan(corpus)


def test_a_missing_archive_file_is_refused(corpus):
    from app.legacy_import.historical_plan import PlanError

    shutil.rmtree(corpus["archive_root"] / "pages/p-thin")
    with pytest.raises(PlanError, match="missing: pages/p-thin/"):
        _plan(corpus)


def test_an_altered_audit_decision_changes_the_plan_digest(corpus, register):
    from app.legacy_import.historical_plan import plan_digest

    before = plan_digest(_plan(corpus))
    review = corpus["audit_root"] / "reports/reconciliation/review-required.csv"
    review.write_text(review.read_text(encoding="utf-8-sig").replace("0.", "0.1"), "utf-8-sig")
    assert plan_digest(_plan(corpus)) != before


def test_the_historical_apply_needs_the_reviewed_plan_digest(corpus, register, settings):
    from app.legacy_import.source_pages import LegacySourcePage

    settings.REAL_DATA_ALLOWED = True
    with pytest.raises(CommandError, match="needs --expect-plan-sha256"):
        _historical("apply", corpus)
    with pytest.raises(CommandError, match="changed since it was approved"):
        _historical("apply", corpus, "--expect-plan-sha256", "0" * 64)
    assert not ImportBatch.objects.exists()
    assert not LegacySourcePage.objects.exists()

    digest = _historical_digest(corpus)
    _historical("apply", corpus, "--expect-plan-sha256", digest)
    assert f"plan-sha256 {digest}" in ImportBatch.objects.get().notes


def test_a_register_changed_since_the_plan_is_reported_not_followed(corpus, register):
    """The plan named which Matter each exact link lands on; a different one is not used."""
    from app.legacy_import.historical_apply import apply_structure, open_batch
    from app.legacy_import.source_pages import MatterSourcePage, SourceMatchMethod

    plan = _plan(corpus)
    linked = next(link for link in plan.exact_links if link.matter_id)
    Matter.all_objects.filter(pk=linked.matter_id).delete()
    replacement = factories.MatterFactory(
        title="Asendatud",
        reference_year=2019,
        reference_number=int(linked.excel_reference.split("_")[1]),
        record_mode=RecordMode.ARCHIVE,
        origin=MatterOrigin.LEGACY_IMPORT,
    )
    report = apply_structure(
        plan, batch=open_batch(plan, importer_version="test"), archive=plan.archive
    )

    assert f"exact link {linked.excel_reference}: the register changed since the plan" in (
        report.failures
    )
    assert not MatterSourcePage.objects.filter(
        matter=replacement, match_method=SourceMatchMethod.EXCEL_EXACT_PAGE_ID
    ).exists()
