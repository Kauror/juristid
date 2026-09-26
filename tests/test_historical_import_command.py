"""`manage.py historical_import`, as an operator runs it (ENG-038).

`tests/test_historical_corpus.py` proves the services beneath the command and
the ENG-007 completeness gate. This file is about the command layer itself:
which phase runs, what it prints, what it writes to the batch, and — the thing
a script and a tired operator both read — whether it exits non-zero. Every
source here is the invented corpus from `tests/synthetic_historical.py`.

Every `verify` detector is shown to fire on a state that can actually occur.
Two used to be unable to (a GROUP BY that a unique constraint pre-empts, and a
NULL check on a non-null foreign key); the tests at the end pin that they were
impossible, and that what replaced them is not.
"""

from __future__ import annotations

import json
from io import StringIO
from pathlib import Path

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import IntegrityError, transaction

from app.documents.models import DocumentVersion
from app.legacy_import.models import ImportBatch
from app.legacy_import.source_pages import (
    HistoricalMatchCandidate,
    LegacySourcePage,
    LegacySourceResource,
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


# -- the corpus and the command --------------------------------------------


@pytest.fixture
def corpus(tmp_path):
    return synthetic_historical.build_corpus(tmp_path / "source")


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
def gate_open(settings):
    settings.REAL_DATA_ALLOWED = True


def _paths(corpus: dict) -> list[str]:
    return [
        "--excel",
        str(corpus["excel_path"]),
        "--archive",
        str(corpus["archive_root"]),
        "--audit",
        str(corpus["audit_root"]),
    ]


def _run(*args: str) -> tuple[int, str]:
    """Exit status and everything the command said, as an operator sees it.

    `call_command` raises the `CommandError` that `manage.py` turns into exit
    status 1, so that is what 1 means here.
    """
    out = StringIO()
    try:
        call_command("historical_import", *args, stdout=out, stderr=StringIO())
    except CommandError as error:
        return 1, f"{out.getvalue()}\n{error}"
    return 0, out.getvalue()


def _table(out: str) -> dict[str, int]:
    """`status`'s two-column table, label → number."""
    rows = {}
    for line in out.splitlines():
        if not line.startswith("  "):
            continue
        label, _, value = line.strip().rpartition(" ")
        if value.replace(",", "").isdigit():
            rows[label.strip()] = int(value.replace(",", ""))
    return rows


@pytest.fixture
def applied(corpus, register, gate_open):
    rc, out = _run("apply", *_paths(corpus))
    assert rc == 0, out
    return out


@pytest.fixture
def materialised(applied, corpus):
    rc, out = _run("materialise", "--archive", str(corpus["archive_root"]))
    assert rc == 0, out
    return out


# -- bad input and the plan ------------------------------------------------


def test_an_unknown_phase_is_refused_by_the_parser():
    with pytest.raises(CommandError, match="invalid choice"):
        call_command("historical_import", "import-everything")


def test_a_source_path_that_does_not_exist_is_refused(corpus, tmp_path):
    missing = tmp_path / "nowhere.xlsx"
    rc, out = _run(
        "plan",
        "--excel",
        str(missing),
        "--archive",
        str(corpus["archive_root"]),
        "--audit",
        str(corpus["audit_root"]),
    )
    assert rc == 1
    assert f"excel: {missing} does not exist." in out


def test_no_source_path_and_no_configured_root_is_refused(settings):
    settings.HISTORICAL_SOURCE_ROOT = ""
    rc, out = _run("plan")
    assert rc == 1
    assert "No excel path. Pass --excel or set HISTORICAL_SOURCE_ROOT." in out

    rc, out = _run("materialise")
    assert rc == 1
    assert "No archive path." in out


def test_a_plan_error_surfaces_as_a_command_error_and_writes_nothing(corpus, register, gate_open):
    """`PlanError` from `build_plan` is the command's failure, not a traceback."""
    rc, out = _run("apply", *_paths(corpus), "--expect-excel-sha256", "0" * 64)

    assert rc == 1
    assert "Excel SHA-256 does not match" in out
    assert "Refusing to plan" in out
    assert not ImportBatch.objects.exists()
    assert not LegacySourcePage.objects.exists()


def test_plan_prints_every_finding_and_writes_its_report(corpus, tmp_path, settings):
    settings.REAL_DATA_ALLOWED = False
    report = tmp_path / "out" / "plan.json"

    rc, out = _run("plan", *_paths(corpus), "--report", str(report))

    assert rc == 0, out
    for label in ("source pages", "resources", "resource bytes", "exact links"):
        assert f"  {label}: " in out and "reconciles" in out
    payload = json.loads(report.read_text(encoding="utf-8"))
    assert payload["warnings"] == 0
    assert not ImportBatch.objects.exists()


def test_an_unreconciled_plan_is_refused_and_nothing_is_written(corpus, register, gate_open):
    """A plan whose counts disagree with the audit baseline never reaches apply."""
    summary = Path(corpus["audit_root"]) / "assistant-pack/source-summary.json"
    payload = json.loads(summary.read_text(encoding="utf-8"))
    payload["onenote"]["pages"] = 99
    summary.write_text(json.dumps(payload), encoding="utf-8")
    matters_before = Matter.objects.count()

    for phase in ("apply", "dry-run"):
        rc, out = _run(phase, *_paths(corpus))

        assert rc == 1, phase
        assert "The plan does not reconcile with the audit baseline" in out
        assert "source pages: planned 9, audit baseline says 99" in out
        assert "Refusing to import." in out
        # The findings that do reconcile are not what it refuses over.
        assert "exact links: 4 — reconciles" not in out

    assert not ImportBatch.objects.exists()
    assert not LegacySourcePage.objects.exists()
    assert not MatterSourcePage.objects.exists()
    assert not HistoricalMatchCandidate.objects.exists()
    assert Matter.objects.count() == matters_before


# -- apply -----------------------------------------------------------------


def test_apply_records_what_it_did_on_the_batch_and_says_the_same(
    corpus, register, gate_open, tmp_path
):
    report = tmp_path / "apply.json"

    rc, out = _run("apply", *_paths(corpus), "--report", str(report))

    assert rc == 0, out
    batch = ImportBatch.objects.get()
    assert f"Import batch {batch.pk}" in out
    assert batch.finished_at is not None
    assert batch.source_row_count == 9

    # The batch's accounting is the database's, not the report's own tally.
    onenote_only = Matter.objects.filter(origin=MatterOrigin.LEGACY_ONENOTE)
    exact = MatterSourcePage.objects.filter(match_method=SourceMatchMethod.EXCEL_EXACT_PAGE_ID)
    assert batch.created_matter_count == onenote_only.count() == 2
    assert batch.matched_count == exact.count() == 3
    assert batch.unmatched_count == 1  # 2019_9 is not in the register

    # And what it printed is the same accounting.
    assert "source pages          9 new, 0 refreshed" in out
    assert f"resources catalogued  {LegacySourceResource.objects.count()}" in out
    assert "exact links           3 (1 references not found)" in out
    assert "OneNote-only Matters  2" in out
    assert f"review candidates     {HistoricalMatchCandidate.objects.count()}" in out
    assert "failures              0" in out
    assert "1 exact link(s) name no Matter in the register" in out
    assert "Structure imported." in out

    payload = json.loads(report.read_text(encoding="utf-8"))
    assert payload["batch_id"] == str(batch.pk)
    assert payload["onenote_matters_created"] == 2
    assert payload["exact_links_unmatched"] == ["2019_9"]
    assert payload["failures"] == []


def test_a_second_apply_refreshes_and_creates_nothing(applied, corpus):
    counts = (
        LegacySourcePage.objects.count(),
        Matter.objects.count(),
        MatterSourcePage.objects.count(),
        HistoricalMatchCandidate.objects.count(),
    )

    rc, out = _run("apply", *_paths(corpus))

    assert rc == 0, out
    assert "source pages          0 new, 9 refreshed" in out
    assert "OneNote-only Matters  0" in out
    second = ImportBatch.objects.order_by("-created_at").first()
    assert ImportBatch.objects.count() == 2
    assert (second.created_matter_count, second.matched_count) == (0, 0)
    assert second.unmatched_count == 1
    assert counts == (
        LegacySourcePage.objects.count(),
        Matter.objects.count(),
        MatterSourcePage.objects.count(),
        HistoricalMatchCandidate.objects.count(),
    )


# -- materialise -----------------------------------------------------------


def test_materialise_copies_every_waiting_file_and_says_so(applied, corpus):
    waiting = sum(link.source_page.resources.count() for link in MatterSourcePage.objects.all())

    rc, out = _run("materialise", "--archive", str(corpus["archive_root"]))

    assert rc == 0, out
    assert f"{waiting} file(s) waiting. Copying all." in out
    assert f"documents materialised {waiting} " in out
    assert "failures              0" in out
    assert "Every file is in." in out
    imported = LegacySourceResourceImport.objects.filter(state=ResourceImportState.IMPORTED)
    assert imported.count() == waiting == LegacySourceResourceImport.objects.count()

    # Resumable: a second run finds nothing to do, and still succeeds.
    rc, out = _run("materialise", "--archive", str(corpus["archive_root"]))
    assert rc == 0, out
    assert "0 file(s) waiting" in out
    assert "Every file is in." in out


def test_a_limited_run_stops_early_and_says_what_is_left(applied, corpus):
    rc, out = _run("materialise", "--archive", str(corpus["archive_root"]), "--limit", "2")

    assert rc == 0, out
    assert "Copying 2." in out
    assert LegacySourceResourceImport.objects.count() == 2
    assert "still waiting. Re-run to continue." in out
    assert "Every file is in." not in out


def test_a_file_whose_bytes_changed_fails_the_run_and_is_not_in(applied, corpus):
    """The SHA-256 check at materialisation, reported through the exit status."""
    original = (
        Path(corpus["archive_root"]) / "pages/p-onenote-only/resources/r-only-1/original"
    ) / "protokoll.pdf"
    original.write_bytes(b"%PDF-1.4\n% not what the audit hashed\n")

    rc, out = _run("materialise", "--archive", str(corpus["archive_root"]))

    assert rc == 1
    assert "failures              1" in out
    assert "p-onenote-only/r-only-1: ValueError" in out
    assert "1 failure(s) this run met." in out
    assert "Every file is in." not in out
    record = LegacySourceResourceImport.objects.get(resource__resource_key="r-only-1")
    assert record.state == ResourceImportState.FAILED
    assert record.document_id is None
    # Every other file did go in: one file does not cost the run.
    assert (
        LegacySourceResourceImport.objects.filter(state=ResourceImportState.IMPORTED).count()
        == LegacySourceResourceImport.objects.count() - 1
    )

    rc, out = _run("verify")
    assert rc == 1
    assert "p-onenote-only/r-only-1: ValueError" in out


# -- status ----------------------------------------------------------------


def test_status_on_an_empty_database_is_all_zeroes():
    rc, out = _run("status")

    assert rc == 0, out
    table = _table(out)
    assert table["source pages"] == 0
    assert table["materialised documents"] == 0
    assert table["still to materialise"] == 0
    assert "Failed, not in:" not in out


def test_status_reports_what_the_database_holds(materialised):
    rc, out = _run("status")

    assert rc == 0, out
    table = _table(out)
    assert table == {
        "Excel Matters": Matter.objects.filter(origin=MatterOrigin.LEGACY_IMPORT).count(),
        "OneNote-only Matters": 2,
        "source pages": 9,
        "Matter ↔ page links": MatterSourcePage.objects.count(),
        "catalogued resources": LegacySourceResource.objects.count(),
        "materialised documents": LegacySourceResourceImport.objects.filter(
            state=ResourceImportState.IMPORTED
        ).count(),
        "failed, not in": 0,
        "empty in the source": 0,
        "still to materialise": 0,
        "pending review": HistoricalMatchCandidate.objects.filter(state="PENDING").count(),
        "awaiting extraction": DocumentVersion.objects.filter(extraction_state="PENDING").count(),
    }
    assert table["materialised documents"] > 0
    assert table["pending review"] == len(synthetic_historical.CANDIDATES)


# -- verify ----------------------------------------------------------------


def test_verify_passes_a_clean_import(materialised):
    rc, out = _run("verify")

    assert rc == 0, out
    assert "every check passed" in out


def _verify_fails_with(message: str) -> str:
    rc, out = _run("verify")
    assert rc == 1, out
    assert message in out, out
    assert "verification problem(s)." in out
    assert "every check passed" not in out
    return out


def test_verify_fails_on_a_onenote_only_matter_with_a_register_reference(materialised):
    matter = Matter.objects.filter(origin=MatterOrigin.LEGACY_ONENOTE).first()
    Matter.objects.filter(pk=matter.pk).update(reference_year=1999, reference_number=1)

    _verify_fails_with(f"{matter.pk}: OneNote-only Matter carries a register reference")


def test_verify_fails_on_a_onenote_only_matter_without_its_page(materialised):
    link = MatterSourcePage.objects.filter(
        match_method=SourceMatchMethod.ONENOTE_ONLY_MATTER
    ).first()
    MatterSourcePage.objects.filter(pk=link.pk).update(match_method=SourceMatchMethod.MANUAL)

    _verify_fails_with(f"{link.matter_id}: 0 primary source pages, expected 1")


def test_verify_fails_on_a_document_whose_bytes_differ_from_the_archive(materialised):
    resource = LegacySourceResource.objects.get(resource_key="r-only-1")
    LegacySourceResource.objects.filter(pk=resource.pk).update(sha256="0" * 64)

    _verify_fails_with("1 document(s) whose SHA-256 differs from the archive")


def test_verify_fails_on_a_file_recorded_as_imported_with_no_document(materialised):
    """IMPORTED is what `status` counts as in; the SHA check used to skip it."""
    record = LegacySourceResourceImport.objects.get(resource__resource_key="r-only-1")
    LegacySourceResourceImport.objects.filter(pk=record.pk).update(
        document=None, document_version=None
    )

    _verify_fails_with("1 file(s) recorded as imported with no stored document")


def test_verify_fails_on_a_page_from_a_source_that_may_not_be_imported(materialised):
    LegacySourcePage.objects.filter(page_key="p-thin").update(
        source_system=SourceSystem.ONENOTE_GRAPH_INVALID
    )

    _verify_fails_with("1 source page(s) from a source that may not be imported: p-thin")


def test_verify_fails_on_a_page_that_became_two_onenote_only_matters(materialised):
    """The duplicate the schema permits: unique per (Matter, page), not per page."""
    page = LegacySourcePage.objects.get(page_key="p-onenote-only")
    twin = factories.MatterFactory(
        title="Tolliprotseduuride töörühm",
        reference_year=None,
        reference_number=None,
        record_mode=RecordMode.ARCHIVE,
        origin=MatterOrigin.LEGACY_ONENOTE,
    )
    MatterSourcePage.objects.create(
        matter=twin, source_page=page, match_method=SourceMatchMethod.ONENOTE_ONLY_MATTER
    )

    out = _verify_fails_with("p-onenote-only: became 2 OneNote-only Matters, expected 1")
    # Each Matter on its own still looks right, which is why this check exists.
    assert "primary source pages" not in out


def test_verify_fails_on_a_file_filed_under_a_page_it_is_not_attached_to(materialised):
    record = LegacySourceResourceImport.objects.get(resource__resource_key="r-only-1")
    elsewhere = LegacySourceResource.objects.get(resource_key="r-cand-1")
    LegacySourceResourceImport.objects.filter(pk=record.pk).update(resource=elsewhere)

    _verify_fails_with("1 imported file(s) filed under a page they are not attached to")


# -- the two detectors that could not fire -----------------------------------


def test_a_desktop_page_cannot_be_imported_twice_so_verify_does_not_ask(materialised):
    """`legacy_one_row_per_source_page` answers before any GROUP BY could."""
    page = LegacySourcePage.objects.get(page_key="p-thin")
    with (
        pytest.raises(IntegrityError, match="legacy_one_row_per_source_page"),
        transaction.atomic(),
    ):
        LegacySourcePage.objects.filter(page_key="p-exact").update(
            source_page_id=page.source_page_id
        )


def test_a_link_cannot_lose_its_page_so_verify_does_not_ask(materialised):
    """`MatterSourcePage.source_page` is NOT NULL and PROTECTed."""
    link = MatterSourcePage.objects.first()
    with pytest.raises(IntegrityError, match="source_page_id"), transaction.atomic():
        MatterSourcePage.objects.filter(pk=link.pk).update(source_page=None)
