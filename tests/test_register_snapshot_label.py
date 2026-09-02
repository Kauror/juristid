"""Which workbook the register text on screen says it came from.

CORR-01. The label exists because the instruction a lawyer reads on a work list
is a *photograph* of a spreadsheet several people are still editing. "uuri 21.08
ministeeriumilt" is not wrong, it is from the 21st, and the reader has to be
able to tell which of the two files they are looking at.

It was choosing by accident. `snapshot_label` took an unordered `.first()`, so
it inherited `CurrentRegisterState.Meta.ordering` — `-source_sheet, matter` —
and the winner was the row with the highest reference number on the highest
sheet. That is not a fact about which workbook is current. The old docstring
asserted the table only ever holds one snapshot; `register_refresh` says the
opposite in as many words, because a Matter the newer workbook no longer names
"keeps its old row, legitimately". So after any refresh that retires a row, the
caption could name the **older** workbook while every current row on screen came
from the newer one.

That is not a hypothetical failure mode in this codebase. It is the one
`opinion_plan.register_snapshot_sha256` records having already had in
production, from an unordered `.first()` over the same kind of column: *"in
production, the older of two snapshots, while the matcher read both."*

There was no test. That is why it survived a review of the module next door.

What the label promises now
---------------------------
It names the workbook the most recent *finished* import read, provided this
table actually holds rows from it. Where it cannot tell, it says nothing —
because a caption naming the wrong one of two workbooks is worse than no
caption, and worse than the honest silence the function already used for a
digest nobody approved.

What it still does not promise
------------------------------
Exactness per row. Where two workbooks are present a retired row keeps text from
the older one and is captioned with the newer; the label is one chip for a page.
Making it exact is a design decision about the chip, not a defect fix, and is
recorded in the module rather than smuggled in here.
"""

from __future__ import annotations

import datetime as dt

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from app.legacy_import.current_state import CurrentRegisterState, RegisterCurrency
from app.legacy_import.final_cutover import ReviewedSnapshot
from app.legacy_import.models import ReconciliationStatus, latest_finished_snapshot
from app.legacy_import.parser import SOURCE_SYSTEM
from app.legacy_import.register_display import snapshot_label
from tests import factories

pytestmark = pytest.mark.django_db

OLD = "a" * 64
NEW = "b" * 64
UNREVIEWED = "c" * 64

OLD_LABEL = "sünteetiline 21.08"
NEW_LABEL = "sünteetiline 28.08"


def approve_both(monkeypatch) -> None:
    """Both workbooks approved, so the label is never withheld for that reason."""
    monkeypatch.setattr(
        "app.legacy_import.final_cutover.REVIEWED_SNAPSHOTS",
        (
            ReviewedSnapshot(
                sha256=OLD,
                label=OLD_LABEL,
                current_years=frozenset({2026}),
                snapshot_date=dt.date(2026, 8, 21),
            ),
            ReviewedSnapshot(
                sha256=NEW,
                label=NEW_LABEL,
                current_years=frozenset({2026}),
                snapshot_date=dt.date(2026, 8, 28),
            ),
        ),
    )


def finished_import(snapshot: str, *, day: int, status: str = ReconciliationStatus.COMPLETED):
    return factories.ImportBatchFactory(
        source_system=SOURCE_SYSTEM,
        source_snapshot_sha256=snapshot,
        reconciliation_status=status,
        started_at=dt.datetime(2026, 8, day, 12, 0, tzinfo=dt.UTC),
    )


def derived_row(
    *,
    snapshot: str,
    number: int,
    currency: str = RegisterCurrency.CURRENT,
    sheet: str = "2026",
) -> CurrentRegisterState:
    """One derived register row, the way the cutover writes them."""
    matter = factories.MatterFactory(reference_year=2026, reference_number=number)
    reference = factories.MatterSourceReferenceFactory(
        matter=matter,
        source_sheet=sheet,
        source_row_number=number,
        source_snapshot_sha256=snapshot,
    )
    return CurrentRegisterState.objects.create(
        matter=matter,
        source_reference=reference,
        source_snapshot_sha256=snapshot,
        source_sheet=sheet,
        source_row_number=number,
        currency=currency,
        next_action_text="Uuri ministeeriumilt",
        observed_at=timezone.now(),
    )


# ---------------------------------------------------------------------------
# The defect
# ---------------------------------------------------------------------------


def test_two_workbooks_are_named_by_chronology_not_by_reference_number(monkeypatch):
    """The regression. `Meta.ordering` used to decide this, and it decided wrong.

    The retired row carries the *higher* reference number, so `-source_sheet,
    matter` sorts it first and the old implementation returned the workbook it
    came from — the older one — while every current row came from the newer.
    """
    approve_both(monkeypatch)
    finished_import(OLD, day=21)
    finished_import(NEW, day=28)

    # Retired by the newer workbook, keeping its old row. Deliberately the
    # highest reference number, which is what the stale ordering ranked first.
    derived_row(snapshot=OLD, number=300, currency=RegisterCurrency.RETIRED)
    # Current work, from the newer workbook.
    derived_row(snapshot=NEW, number=17)

    assert snapshot_label() == NEW_LABEL


def test_the_newest_import_wins_however_the_rows_are_numbered(monkeypatch):
    """Same two workbooks, reference numbers the other way round."""
    approve_both(monkeypatch)
    finished_import(OLD, day=21)
    finished_import(NEW, day=28)

    derived_row(snapshot=OLD, number=4, currency=RegisterCurrency.RETIRED)
    derived_row(snapshot=NEW, number=290)

    assert snapshot_label() == NEW_LABEL


def test_a_running_or_failed_import_does_not_make_a_workbook_current(monkeypatch):
    """A half-written snapshot is not a reading of the register.

    The newer workbook's import never finished, so the older one is still the
    most recent *finished* reading and the label must say so.
    """
    approve_both(monkeypatch)
    finished_import(OLD, day=21)
    finished_import(NEW, day=28, status=ReconciliationStatus.RUNNING)
    finished_import(NEW, day=29, status=ReconciliationStatus.FAILED)

    derived_row(snapshot=OLD, number=4)
    derived_row(snapshot=NEW, number=17, currency=RegisterCurrency.RETIRED)

    assert snapshot_label() == OLD_LABEL


def test_two_workbooks_and_no_finished_import_says_nothing(monkeypatch):
    """Silence rather than a guess: naming the wrong one of two is the defect."""
    approve_both(monkeypatch)
    derived_row(snapshot=OLD, number=4)
    derived_row(snapshot=NEW, number=17)

    assert snapshot_label() == ""


def test_a_finished_import_of_a_workbook_this_table_does_not_hold_says_nothing(monkeypatch):
    """The chronology must be narrowed by what is actually derived here.

    A newer register was imported but the cutover has not been re-run, so no
    `CurrentRegisterState` row came from it. Naming it would caption text that
    did not come from it.
    """
    approve_both(monkeypatch)
    finished_import(UNREVIEWED, day=29)

    derived_row(snapshot=OLD, number=4)
    derived_row(snapshot=NEW, number=17)

    assert snapshot_label() == ""


# ---------------------------------------------------------------------------
# The behaviour that must not have changed
# ---------------------------------------------------------------------------


def test_one_workbook_is_named_without_asking_the_chronology(monkeypatch):
    """The common case, and it must not depend on an ImportBatch existing.

    An older deployment, a fixture, or a database restored before the batch
    table meant anything: one digest present is unambiguous, so there is nothing
    to refuse and nothing to look up.
    """
    approve_both(monkeypatch)
    derived_row(snapshot=NEW, number=17)
    derived_row(snapshot=NEW, number=18)

    assert snapshot_label() == NEW_LABEL


def test_nothing_derived_yet_says_nothing(monkeypatch):
    approve_both(monkeypatch)
    assert snapshot_label() == ""


def test_a_digest_nobody_approved_says_nothing(monkeypatch):
    """Unchanged: better silent than naming a workbook the reviewed list
    has never heard of."""
    approve_both(monkeypatch)
    derived_row(snapshot=UNREVIEWED, number=17)

    assert snapshot_label() == ""


def test_the_label_does_not_join_and_sort_the_whole_table_to_read_a_constant(monkeypatch):
    """`Meta.ordering` is `-source_sheet, matter`, so the old `.first()` joined
    `matters_matter` and sorted the register on every surface that renders this
    — four of them — to read what the function itself calls a constant.

    The query count alone does not catch that: it was one query before and one
    after. What changed is what that query does, so this asserts on the SQL.
    """
    approve_both(monkeypatch)
    for number in range(5):
        derived_row(snapshot=NEW, number=100 + number)

    with CaptureQueriesContext(connection) as captured:
        assert snapshot_label() == NEW_LABEL

    statements = [query["sql"] for query in captured.captured_queries]
    assert len(statements) == 1, statements
    sql = statements[0].lower()
    assert "order by" not in sql, sql
    assert "matters_matter" not in sql, sql
    assert "distinct" in sql, sql


# ---------------------------------------------------------------------------
# The chronology itself
# ---------------------------------------------------------------------------


def test_latest_finished_snapshot_prefers_the_most_recent_finished_import():
    finished_import(OLD, day=21)
    finished_import(NEW, day=28)
    assert latest_finished_snapshot(SOURCE_SYSTEM) == NEW


def test_latest_finished_snapshot_counts_completed_with_gaps():
    """The gap is source rows that did not become Matters, not doubt about the
    rows that did."""
    finished_import(OLD, day=21)
    finished_import(NEW, day=28, status=ReconciliationStatus.COMPLETED_WITH_GAPS)
    assert latest_finished_snapshot(SOURCE_SYSTEM) == NEW


def test_latest_finished_snapshot_ignores_unfinished_imports():
    finished_import(OLD, day=21)
    finished_import(NEW, day=28, status=ReconciliationStatus.RUNNING)
    finished_import(UNREVIEWED, day=29, status=ReconciliationStatus.FAILED)
    assert latest_finished_snapshot(SOURCE_SYSTEM) == OLD


def test_two_imports_in_the_same_instant_still_resolve_to_one_digest():
    """The primary-key tie-break, so this is never the database's choice."""
    first = finished_import(OLD, day=21)
    second = finished_import(NEW, day=21)
    assert first.started_at == second.started_at

    chosen = latest_finished_snapshot(SOURCE_SYSTEM)
    assert chosen in {OLD, NEW}
    assert chosen == latest_finished_snapshot(SOURCE_SYSTEM)


def test_latest_finished_snapshot_is_empty_when_nothing_finished():
    assert latest_finished_snapshot(SOURCE_SYSTEM) == ""
    finished_import(OLD, day=21, status=ReconciliationStatus.RUNNING)
    assert latest_finished_snapshot(SOURCE_SYSTEM) == ""


def test_latest_finished_snapshot_is_scoped_to_its_source_system():
    finished_import(OLD, day=21)
    factories.ImportBatchFactory(
        source_system="mingi-teine-susteem",
        source_snapshot_sha256=NEW,
        reconciliation_status=ReconciliationStatus.COMPLETED,
        started_at=dt.datetime(2026, 8, 29, 12, 0, tzinfo=dt.UTC),
    )
    assert latest_finished_snapshot(SOURCE_SYSTEM) == OLD
