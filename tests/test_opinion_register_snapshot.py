"""Which register an opinion reconciliation reads.

`MatterSourceReference` is write-once evidence, so re-importing a newer Excel
adds a second reference for a Matter rather than replacing the first. That is
the provenance model working: nothing is deleted, and both readings stay
auditable.

The reconciliation, though, has to read *one* register. When it read the union
of every imported snapshot, each Matter appeared twice under the same date and
addressee, the matcher's "exactly one register row" test counted two, and the
Matter competed with itself. In production that turned 249 STRICT_MULTI_SIGNAL
occurrences into REVIEW_REQUIRED and left exactly one automatic proposal — while
the plan header still named a single Excel SHA, and named the *older* one,
because the header and the rows were two independent lookups.

So these tests are about one property: the snapshot the plan names and the
snapshot the matcher reads are the same snapshot, chosen once, and choosing it
never deletes the other.

Everything is synthetic.
"""

from __future__ import annotations

import datetime

import pytest

from app.core.enums import Visibility
from app.legacy_import.models import MatterSourceReference, ReconciliationStatus
from app.legacy_import.opinion_apply import OpinionApplyError, require_unchanged_sources
from app.legacy_import.opinion_enums import OpinionMatchClass
from app.legacy_import.opinion_plan import (
    OpinionPlanError,
    build_plan,
    load_register_rows,
    register_snapshot_sha256,
    select_register_snapshot,
)
from app.legacy_import.parser import SOURCE_SYSTEM
from tests import factories
from tests import synthetic_opinions as syn

pytestmark = pytest.mark.django_db

OLD = "a" * 64
NEW = "b" * 64

#: The register row and the archive file below share a date, an addressee and
#: the distinctive word "näidisregistri" — the three exact signals that make a
#: STRICT_MULTI_SIGNAL. Kept identical across snapshots on purpose: the point is
#: that two agreeing observations must not cancel each other out.
TITLE = "Näidisregistri seaduse muutmise seadus"
RECIPIENT = "Näidisministeerium"
SENT = "2024-04-10"


def finished_import(snapshot: str, *, day: int) -> None:
    """A completed register import of one snapshot, on a known day."""
    factories.ImportBatchFactory(
        source_system=SOURCE_SYSTEM,
        source_snapshot_sha256=snapshot,
        reconciliation_status=ReconciliationStatus.COMPLETED,
        started_at=datetime.datetime(2026, 8, day, 12, 0, tzinfo=datetime.UTC),
    )


def observe(matter, snapshot: str, *, year: int, number: int, title: str, sent: str | None):
    """One immutable register observation of a Matter, under one snapshot."""
    factories.MatterSourceReferenceFactory(
        matter=matter,
        source_system=SOURCE_SYSTEM,
        source_snapshot_sha256=snapshot,
        source_sheet=str(year),
        source_row_number=number,
        source_row_raw={
            "A": f"{year}_{number}",
            "B": title,
            "F": sent or "",
            "G": RECIPIENT,
        },
    )
    return matter


def register_matter(*, year: int = 2024, number: int = 21, title: str = TITLE):
    return factories.ArchiveMatterFactory(
        reference_year=year, reference_number=number, title=title, visibility=Visibility.NORMAL
    )


def archive_with_one_letter(tmp_path):
    """An archive file that is a STRICT_MULTI_SIGNAL for the Matter above."""
    item = syn.opinion(
        date=SENT,
        recipient=RECIPIENT,
        title="Arvamus näidisregistri seaduse muutmise kohta",
    )
    return syn.write_archive(tmp_path / "Opinions.zip", [item]), item


# ---------------------------------------------------------------------------
# Selection
# ---------------------------------------------------------------------------


def test_the_newest_finished_import_is_the_current_register():
    matter = register_matter()
    observe(matter, OLD, year=2024, number=21, title=TITLE, sent=SENT)
    observe(matter, NEW, year=2024, number=21, title=TITLE, sent=SENT)
    finished_import(OLD, day=21)
    finished_import(NEW, day=22)

    assert select_register_snapshot() == NEW
    assert register_snapshot_sha256() == NEW, "the gate and the plan must agree"


def test_an_import_that_has_not_finished_is_not_a_register():
    """A half-written snapshot is not a reading of the register."""
    matter = register_matter()
    observe(matter, OLD, year=2024, number=21, title=TITLE, sent=SENT)
    observe(matter, NEW, year=2024, number=21, title=TITLE, sent=SENT)
    finished_import(OLD, day=21)
    factories.ImportBatchFactory(
        source_system=SOURCE_SYSTEM,
        source_snapshot_sha256=NEW,
        reconciliation_status=ReconciliationStatus.RUNNING,
        started_at=datetime.datetime(2026, 8, 22, 12, 0, tzinfo=datetime.UTC),
    )

    assert select_register_snapshot() == OLD


def test_an_import_finished_with_gaps_is_still_a_register():
    """The gap is rows that did not become Matters, not doubt about those that did."""
    matter = register_matter()
    observe(matter, NEW, year=2024, number=21, title=TITLE, sent=SENT)
    factories.ImportBatchFactory(
        source_system=SOURCE_SYSTEM,
        source_snapshot_sha256=NEW,
        reconciliation_status=ReconciliationStatus.COMPLETED_WITH_GAPS,
        started_at=datetime.datetime(2026, 8, 22, 12, 0, tzinfo=datetime.UTC),
    )

    assert select_register_snapshot() == NEW


def test_several_snapshots_and_no_finished_import_is_refused_rather_than_guessed():
    """Fails closed. Which register is current is not a question to answer by picking."""
    matter = register_matter()
    observe(matter, OLD, year=2024, number=21, title=TITLE, sent=SENT)
    observe(matter, NEW, year=2024, number=21, title=TITLE, sent=SENT)

    with pytest.raises(OpinionPlanError, match="snapshots"):
        select_register_snapshot()


def test_one_snapshot_needs_no_batch_to_be_unambiguous():
    matter = register_matter()
    observe(matter, OLD, year=2024, number=21, title=TITLE, sent=SENT)

    assert select_register_snapshot() == OLD


def test_a_snapshot_this_database_never_imported_is_an_error_not_a_heading():
    matter = register_matter()
    observe(matter, OLD, year=2024, number=21, title=TITLE, sent=SENT)

    with pytest.raises(OpinionPlanError, match="No register was imported"):
        select_register_snapshot("c" * 64)


# ---------------------------------------------------------------------------
# One Matter, two observations
# ---------------------------------------------------------------------------


def test_two_observations_of_one_matter_are_one_register_row():
    """The old observation stays in the database. It just is not this reading."""
    matter = register_matter()
    observe(matter, OLD, year=2024, number=21, title=TITLE, sent=SENT)
    observe(matter, NEW, year=2024, number=21, title=TITLE, sent=SENT)
    finished_import(OLD, day=21)
    finished_import(NEW, day=22)

    rows = load_register_rows(snapshot_sha256=select_register_snapshot())

    assert [row.matter_id for row in rows] == [matter.pk]
    assert MatterSourceReference.objects.filter(matter=matter).count() == 2, "nothing deleted"


def test_a_second_agreeing_snapshot_does_not_cost_the_match(tmp_path):
    """The production regression, in one test.

    Two snapshots that say exactly the same thing about one Matter must not
    turn its strongest match class into a review queue entry. Before the fix
    this occurrence came back REVIEW_REQUIRED with a title conflict, because
    the Matter's two observations were counted as two competing register rows.
    """
    matter = register_matter()
    observe(matter, OLD, year=2024, number=21, title=TITLE, sent=SENT)
    finished_import(OLD, day=21)
    archive, _ = archive_with_one_letter(tmp_path)

    before = build_plan(archive_path=archive, kodadash_path=None)
    assert before.proposals[0].match_class == OpinionMatchClass.STRICT_MULTI_SIGNAL, (
        "the premise: one snapshot files this letter without asking anybody"
    )

    observe(matter, NEW, year=2024, number=21, title=TITLE, sent=SENT)
    finished_import(NEW, day=22)

    after = build_plan(archive_path=archive, kodadash_path=None)

    assert after.proposals[0].match_class == OpinionMatchClass.STRICT_MULTI_SIGNAL
    assert after.excel_sha256 == NEW
    assert len(after.submissions) == len(before.submissions) == 1


# ---------------------------------------------------------------------------
# The plan names the register it read
# ---------------------------------------------------------------------------


def test_the_plan_reports_the_snapshot_it_actually_read(tmp_path):
    matter = register_matter()
    observe(matter, OLD, year=2024, number=21, title=TITLE, sent=SENT)
    observe(matter, NEW, year=2024, number=21, title=TITLE, sent=SENT)
    finished_import(OLD, day=21)
    finished_import(NEW, day=22)
    archive, _ = archive_with_one_letter(tmp_path)

    plan = build_plan(archive_path=archive, kodadash_path=None)

    assert plan.excel_sha256 == NEW
    assert plan.summary()["excel_sha256"] == NEW


def test_an_explicit_snapshot_pins_the_rows_and_not_just_the_report(tmp_path):
    """The argument existed before this change and only decorated the heading.

    Asserted through a Matter that exists in one snapshot and not the other, so
    a plan that ignored the pin would still report the right SHA and quietly
    classify against the wrong register.
    """
    only_old = register_matter(year=2023, number=7, title="Vana registri seaduse muutmine")
    shared = register_matter()
    observe(only_old, OLD, year=2023, number=7, title="Vana registri seaduse muutmine", sent=SENT)
    observe(shared, OLD, year=2024, number=21, title=TITLE, sent=SENT)
    observe(shared, NEW, year=2024, number=21, title=TITLE, sent=SENT)
    finished_import(OLD, day=21)
    finished_import(NEW, day=22)
    archive, _ = archive_with_one_letter(tmp_path)

    plan = build_plan(archive_path=archive, kodadash_path=None, excel_sha256=OLD)

    assert plan.excel_sha256 == OLD
    assert {row.matter_id for row in load_register_rows(snapshot_sha256=OLD)} == {
        only_old.pk,
        shared.pk,
    }


def test_a_matter_missing_from_the_selected_snapshot_is_not_borrowed_from_an_older_one():
    """A coherent register is one register.

    Falling back per Matter would blend two snapshots and then name one of them
    in the report, which is the defect this change exists to remove — in a
    quieter form.
    """
    retired = register_matter(year=2023, number=7, title="Vana registri seaduse muutmine")
    carried = register_matter()
    observe(retired, OLD, year=2023, number=7, title="Vana registri seaduse muutmine", sent=SENT)
    observe(carried, OLD, year=2024, number=21, title=TITLE, sent=SENT)
    observe(carried, NEW, year=2024, number=21, title=TITLE, sent=SENT)
    finished_import(OLD, day=21)
    finished_import(NEW, day=22)

    rows = load_register_rows(snapshot_sha256=select_register_snapshot())

    assert [row.matter_id for row in rows] == [carried.pk]
    assert retired.pk not in {row.matter_id for row in rows}


# ---------------------------------------------------------------------------
# The source gate
# ---------------------------------------------------------------------------


def test_a_plan_is_refused_once_a_newer_register_has_been_imported(tmp_path):
    """Why the gate has to share the selection rule.

    Two independent answers to "which register is current" would let a plan
    reviewed against one snapshot be applied after another was imported.
    """
    matter = register_matter()
    observe(matter, OLD, year=2024, number=21, title=TITLE, sent=SENT)
    finished_import(OLD, day=21)
    archive, _ = archive_with_one_letter(tmp_path)

    plan = build_plan(archive_path=archive, kodadash_path=None)
    require_unchanged_sources(plan)

    observe(matter, NEW, year=2024, number=21, title=TITLE, sent=SENT)
    finished_import(NEW, day=22)

    with pytest.raises(OpinionApplyError, match="different Excel snapshot"):
        require_unchanged_sources(plan)
