"""Once Lovable is the register's source, the Excel-era writes refuse (docs/adr/0203).

The register replacement made Lovable the sole source of the operational
register (the ``lovable-register`` cohort). The Excel-era import and write
commands were still gated only by `REAL_DATA_ALLOWED` and their digests — both
of which say a person approved a workbook, neither of which knows the register
no longer comes from one. `app.legacy_import.register_source` is the one rule
they now share.

Each guarded write mode is run here on a world where it would otherwise write,
with every other gate it has opened, and the **whole database** is fingerprinted
before and after: refused in the guard's exact words, nothing written. The
read-only modes run on the same database and still answer. A database with no
register cohort is untouched by the rule — every existing command test runs on
one.
"""

from __future__ import annotations

import io
import re
import uuid
from pathlib import Path

import pytest
from django.core.management import CommandError, call_command
from django.utils import timezone

from app.legacy_import.lovable_pilot import OPERATIONAL_COHORT, REGISTER_COHORT
from app.legacy_import.lovable_sync_models import LovableSyncCohort, LovableSyncState
from app.legacy_import.register_source import (
    excel_era_write_refusal,
    lovable_register_is_source,
    refuse_excel_era_write,
)
from app.matters.models import Matter
from app.workflow.models import NextAction
from tests import factories, synthetic_historical
from tests.synthetic_cutover import FINAL_SNAPSHOT, approve_snapshot, build_world
from tests.synthetic_portfolio import CURRENT_YEAR, build_portfolio
from tests.synthetic_register import era_corpus, write_workbook
from tests.test_excel_pilot_2026 import (  # noqa: F401 - fixtures
    selection,
    workbook,
)
from tests.test_excel_pilot_2026 import world as pilot_world  # noqa: F401 - fixture
from tests.test_historical_cutover import CUTOVER, register_matter
from tests.test_next_action_enrichment import SNAPSHOT, WAIT_QUARTER, _state
from tests.test_operator_commands_dry_run import _selection_toml, changed, fingerprint, run

pytestmark = pytest.mark.django_db


def _cohort(key: str = REGISTER_COHORT, *, retired: bool = False) -> LovableSyncCohort:
    return LovableSyncCohort.objects.create(
        key=key,
        pilot=key,
        package_sha256="c" * 64,
        expected=1,
        enrolled_at=timezone.now(),
        reason="test",
        retired_at=timezone.now() if retired else None,
        retired_reason="superseded" if retired else "",
    )


def _state_in(cohort: str) -> LovableSyncState:
    return LovableSyncState.objects.create(
        matter=factories.MatterFactory(), source_id=uuid.uuid4(), reference="2026_1", cohort=cohort
    )


@pytest.fixture
def lovable_register(db) -> LovableSyncCohort:
    return _cohort()


def refused_as(command: str):
    return pytest.raises(CommandError, match=re.escape(excel_era_write_refusal(command)))


def _digest(report: str, label: str) -> str:
    return next(line for line in report.splitlines() if label in line).split()[-1]


# -- the rule ---------------------------------------------------------------------------


def test_no_register_cohort_means_excel_is_not_refused():
    assert lovable_register_is_source() is False
    refuse_excel_era_write("anything")  # does not raise


def test_an_active_register_cohort_makes_lovable_the_source(lovable_register):
    assert lovable_register_is_source() is True
    with refused_as("anything"):
        refuse_excel_era_write("anything")


def test_a_retired_register_cohort_with_no_states_does_not_block():
    _cohort(retired=True)
    assert lovable_register_is_source() is False


def test_a_register_state_without_its_cohort_row_still_blocks():
    _state_in(REGISTER_COHORT)
    assert lovable_register_is_source() is True


def test_earlier_lovable_cohorts_do_not_block():
    _cohort(OPERATIONAL_COHORT)
    _state_in(OPERATIONAL_COHORT)
    assert lovable_register_is_source() is False


def test_the_refusal_names_the_source_the_command_and_that_nothing_was_written():
    sentence = excel_era_write_refusal("import_legacy_register --apply")
    assert "Lovable (docs/adr/0203)" in sentence
    assert REGISTER_COHORT in sentence
    assert "`import_legacy_register --apply` is an Excel-era write" in sentence
    assert sentence.endswith("nothing was written.")


# -- import_legacy_register ---------------------------------------------------------------


def test_import_legacy_register_apply_is_refused_and_dry_run_still_plans(
    tmp_path: Path, settings, lovable_register
):
    settings.REAL_DATA_ALLOWED = True
    path = write_workbook(tmp_path / "synthetic.xlsx", era_corpus())
    before = fingerprint()

    report = run("import_legacy_register", str(path), dry_run=True, report_dir=str(tmp_path / "a"))
    assert "Dry run: nothing was written to the database." in report
    digest = _digest(report, "plan sha256")

    with refused_as("import_legacy_register --apply"):
        run(
            "import_legacy_register",
            str(path),
            apply=True,
            accept_review_rows=True,
            expect_plan_sha256=digest,
            report_dir=str(tmp_path / "b"),
        )
    assert changed(before) == []
    assert not (tmp_path / "b").exists()


# -- refresh_current_register -------------------------------------------------------------


def test_refresh_current_register_apply_is_refused_before_it_reads_anything(
    tmp_path: Path, lovable_register
):
    before = fingerprint()
    with refused_as("refresh_current_register apply"):
        run("refresh_current_register", "apply", workbook=str(tmp_path / "absent.xlsx"))
    assert changed(before) == []

    # `plan` passes the guard and reaches its own first check.
    with pytest.raises(CommandError, match="No workbook at"):
        run("refresh_current_register", "plan", workbook=str(tmp_path / "absent.xlsx"))


# -- final_register_cutover ----------------------------------------------------------------


def test_final_register_cutover_apply_is_refused_and_dry_run_still_reports(
    monkeypatch, lovable_register
):
    approve_snapshot(monkeypatch, sha256=FINAL_SNAPSHOT)
    build_world()
    before = fingerprint()

    report = run("final_register_cutover", snapshot=FINAL_SNAPSHOT, dry_run=True)
    assert "Dry run: nothing was written to the database." in report

    with refused_as("final_register_cutover --apply"):
        run("final_register_cutover", snapshot=FINAL_SNAPSHOT, apply=True)
    assert changed(before) == []


# -- promote_current_register --------------------------------------------------------------


def test_promote_current_register_apply_is_refused_and_dry_run_still_reports(lovable_register):
    build_portfolio()
    before = fingerprint()

    report = run("promote_current_register", year=CURRENT_YEAR, dry_run=True)
    assert "Dry run: nothing was written to the database." in report

    with refused_as("promote_current_register --apply"):
        run("promote_current_register", year=CURRENT_YEAR, apply=True)
    assert changed(before) == []


# -- historical_cutover_state --------------------------------------------------------------


def test_historical_cutover_state_apply_is_refused_and_dry_run_still_reports(lovable_register):
    old = register_matter(CUTOVER - 3, title="Vana avatud registririda")
    before = fingerprint()

    report = run("historical_cutover_state", cutover_year=CUTOVER, dry_run=True)
    assert "Dry run: nothing was written to the database." in report

    with refused_as("historical_cutover_state --apply"):
        run("historical_cutover_state", cutover_year=CUTOVER, apply=True)
    assert changed(before) == []
    old.refresh_from_db()
    assert old.is_open


# -- register_next_action_enrichment --------------------------------------------------------


def test_next_action_enrichment_apply_is_refused_and_plan_still_runs(specialist, lovable_register):
    _state(text=WAIT_QUARTER, row=1, matter=factories.MatterFactory(owner=specialist))
    before = fingerprint()

    report = run("register_next_action_enrichment", "plan", expect_snapshot_sha256=SNAPSHOT)
    assert "Plan only: nothing was written to the database." in report
    digest = _digest(report, "plan digest")

    with refused_as("register_next_action_enrichment apply"):
        run(
            "register_next_action_enrichment",
            "apply",
            expect_snapshot_sha256=SNAPSHOT,
            expect_plan_sha256=digest,
        )
    assert changed(before) == []
    assert NextAction.objects.count() == 0


# -- backfill_legacy_owners -----------------------------------------------------------------


def test_backfill_legacy_owners_apply_is_refused_and_dry_run_still_reports(lovable_register):
    build_portfolio()
    before = fingerprint()

    report = run("backfill_legacy_owners", dry_run=True)
    assert "Dry run: nothing was written to the database." in report
    digest = _digest(report, "plan sha256")

    with refused_as("backfill_legacy_owners --apply"):
        run("backfill_legacy_owners", apply=True, expect_plan_sha256=digest)
    assert changed(before) == []


# -- excel_pilot_2026 -------------------------------------------------------------------------


def test_excel_pilot_apply_is_refused_and_plan_still_runs(
    workbook,  # noqa: F811
    selection,  # noqa: F811
    pilot_world,  # noqa: F811
    tmp_path: Path,
    lovable_register,
):
    selection_file = tmp_path / "valik.toml"
    selection_file.write_text(_selection_toml(selection), encoding="utf-8")
    manifest_file = tmp_path / "manifest.json"
    before = fingerprint()

    run(
        "excel_pilot_2026",
        "manifest",
        workbook=str(workbook),
        selection=str(selection_file),
        out=str(manifest_file),
    )
    report = run("excel_pilot_2026", "plan", workbook=str(workbook), manifest=str(manifest_file))
    assert "Plan only: nothing was written." in report

    with refused_as("excel_pilot_2026 apply"):
        run(
            "excel_pilot_2026",
            "apply",
            workbook=str(workbook),
            manifest=str(manifest_file),
            operator_intent="excel-pilot-2026",
        )
    assert changed(before) == []


# -- historical_import ------------------------------------------------------------------------


@pytest.fixture
def corpus(tmp_path):
    return synthetic_historical.build_corpus(tmp_path / "source")


def _paths(corpus: dict) -> dict[str, Path]:
    return {
        "excel": corpus["excel_path"],
        "archive": corpus["archive_root"],
        "audit": corpus["audit_root"],
    }


def test_historical_import_apply_is_refused_and_plan_still_runs(corpus, settings, lovable_register):
    settings.REAL_DATA_ALLOWED = True
    before = fingerprint()
    matters = Matter.objects.count()

    report = run("historical_import", "plan", **_paths(corpus))
    digest = _digest(report, "plan SHA-256")

    with refused_as("historical_import apply"):
        run("historical_import", "apply", expect_plan_sha256=digest, **_paths(corpus))
    assert changed(before) == []
    assert Matter.objects.count() == matters


def test_historical_import_materialise_is_refused_and_status_and_verify_still_run(
    corpus, lovable_register
):
    before = fingerprint()

    with refused_as("historical_import materialise"):
        run("historical_import", "materialise", archive=corpus["archive_root"])
    assert changed(before) == []

    assert "Excel Matters" in run("historical_import", "status")
    assert "every check passed" in run("historical_import", "verify")


# -- without the register cohort nothing changes ---------------------------------------------


def test_without_the_register_cohort_an_apply_still_writes():
    """The guard is not a blanket refusal: the same apply writes on an Excel-era database."""
    _cohort(retired=True)
    build_portfolio()
    before = fingerprint()

    out = io.StringIO()
    call_command("promote_current_register", year=CURRENT_YEAR, apply=True, stdout=out)
    assert "matters_matter" in changed(before)
