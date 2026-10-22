"""The operator import and cutover commands, run as an operator runs them, write nothing.

Each command's planner and applier are proved at the service layer by its own
test file, but the commands' `handle()` — the argument parsing, the
`--dry-run`/`--apply` branch, the refusals and the report — had never run in CI
(audit 2026-10-17, JTEST-008: 21–32 % covered). A dry run that reached the
applier through a mistyped option would be the operator's worst surprise, and
nothing would have said so.

So each command is called through `call_command(..., dry_run=True)` on the
smallest world its own tests use, and the **whole database** is fingerprinted
before and after: every table's every row, not a count of the tables the author
expected to change. Each test also proves the world gave the command something
to do — the report names work it *would* do, and the same world under
`--apply` (or the service the command calls) does change the database — so a
dry run over an empty world cannot pass for the right reason.

`excel_pilot_2026` has no `--dry-run`: its no-write modes are `manifest` and
`plan` (docs/adr/0148), so those are what run here.
"""

from __future__ import annotations

import io

import pytest
from django.core.management import call_command
from django.db import connection

from app.legacy_import import excel_pilot
from tests.synthetic_cutover import FINAL_SNAPSHOT, approve_snapshot, build_world
from tests.synthetic_portfolio import CURRENT_YEAR, build_portfolio
from tests.test_excel_pilot_2026 import (  # noqa: F401 - fixtures
    selection,
    workbook,
)
from tests.test_excel_pilot_2026 import world as pilot_world  # noqa: F401 - fixture
from tests.test_historical_cutover import CUTOVER, register_matter

pytestmark = pytest.mark.django_db


def fingerprint() -> dict[str, str]:
    """Every table's every row, as one digest per table."""
    digests = {}
    with connection.cursor() as cursor:
        for table in sorted(connection.introspection.table_names()):
            quoted = connection.ops.quote_name(table)
            cursor.execute(
                f"SELECT md5(coalesce(string_agg(t::text, '|' ORDER BY t::text), '')) "  # noqa: S608
                f"FROM {quoted} AS t"
            )
            digests[table] = cursor.fetchone()[0]
    return digests


def changed(before: dict[str, str]) -> list[str]:
    after = fingerprint()
    return sorted(table for table in after if after[table] != before.get(table))


def run(*arguments: str, **options) -> str:
    out = io.StringIO()
    call_command(*arguments, stdout=out, stderr=io.StringIO(), **options)
    return out.getvalue()


def _figure(report: str, label: str) -> int:
    """The number a report prints after ``label``."""
    line = next(line for line in report.splitlines() if line.strip().startswith(label))
    return int(line.split()[-1])


# -- promote_current_register ----------------------------------------------------------


def test_promote_current_register_dry_run_writes_nothing():
    build_portfolio()
    before = fingerprint()

    report = run("promote_current_register", year=CURRENT_YEAR, dry_run=True)

    assert changed(before) == []
    assert "Dry run: nothing was written to the database." in report
    assert _figure(report, "would promote") > 0

    run("promote_current_register", year=CURRENT_YEAR, apply=True)
    assert "matters_matter" in changed(before)


def test_promote_current_register_dry_run_of_an_unreviewed_year_writes_nothing():
    build_portfolio()
    before = fingerprint()

    report = run("promote_current_register", year=CURRENT_YEAR - 1, dry_run=True)

    assert changed(before) == []
    assert "is analysis only" in report


# -- final_register_cutover ------------------------------------------------------------


def test_final_register_cutover_dry_run_writes_nothing(monkeypatch):
    approve_snapshot(monkeypatch, sha256=FINAL_SNAPSHOT)
    build_world()
    before = fingerprint()

    report = run("final_register_cutover", snapshot=FINAL_SNAPSHOT, dry_run=True)

    assert changed(before) == []
    assert "Dry run: nothing was written to the database." in report

    run("final_register_cutover", snapshot=FINAL_SNAPSHOT, apply=True)
    assert changed(before) != []


def test_final_register_cutover_dry_run_of_an_unreviewed_snapshot_writes_nothing():
    build_world()
    before = fingerprint()

    report = run("final_register_cutover", snapshot=FINAL_SNAPSHOT, dry_run=True)

    assert changed(before) == []
    assert "This snapshot is analysis only." in report


# -- backfill_legacy_owners -----------------------------------------------------------


def test_backfill_legacy_owners_dry_run_writes_nothing():
    build_portfolio()
    before = fingerprint()

    report = run("backfill_legacy_owners", dry_run=True)

    assert changed(before) == []
    assert "Dry run: nothing was written to the database." in report
    assert _figure(report, "would update") > 0

    # The apply writes only the reviewed plan: the digest the dry run printed.
    digest = next(line for line in report.splitlines() if "plan sha256" in line).split()[-1]
    run("backfill_legacy_owners", apply=True, expect_plan_sha256=digest)
    assert "matters_matter" in changed(before)


# -- historical_cutover_state ---------------------------------------------------------


def test_historical_cutover_state_dry_run_writes_nothing():
    old = register_matter(CUTOVER - 3, title="Vana avatud registririda")
    register_matter(CUTOVER, title="Selle aasta registririda")
    before = fingerprint()

    report = run("historical_cutover_state", cutover_year=CUTOVER, dry_run=True)

    assert changed(before) == []
    assert "Dry run: nothing was written to the database." in report

    run("historical_cutover_state", cutover_year=CUTOVER, apply=True)
    assert "matters_matter" in changed(before)
    old.refresh_from_db()
    assert not old.is_open


# -- excel_pilot_2026 -----------------------------------------------------------------


def test_excel_pilot_manifest_and_plan_write_nothing(workbook, selection, pilot_world, tmp_path):  # noqa: F811
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

    assert changed(before) == []
    assert "Plan only: nothing was written." in report
    # The plan is a real one: the service the apply mode calls writes it.
    manifest = excel_pilot.read_manifest(manifest_file)
    assert manifest == excel_pilot.build_manifest(excel_pilot.read_pilot_sheet(workbook), selection)
    assert excel_pilot.build_plan(workbook, manifest).problems == []


def _selection_toml(chosen) -> str:
    """The reviewed selection as the operator writes it for `load_selection`."""
    lines = [f'snapshot_date = "{chosen.snapshot_date.isoformat()}"', ""]
    for group, entries in (("group_b", chosen.group_b), ("group_c", chosen.group_c)):
        for reference, reason in entries:
            lines += [f"[[{group}]]", f'reference = "{reference}"', f'reason = "{reason}"', ""]
    return "\n".join(lines)
