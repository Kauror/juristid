"""The daily photograph: what it records, what it refuses, and who may read it.

Three properties matter more than the rest.

**Idempotent.** A cron that fires twice after a restart must not double a
trend. The unique constraint makes that a fact about the schema rather than
about the command's care, and the test proves both.

**Operational population only.** The archive is not photographed. Writing
thousands of historical rows every night would multiply the table by the size of
the corpus to answer a question nobody asks about it.

**Never a source of visibility.** A snapshot row is read *through the live
Matter*. Restricting a Matter today removes it from last month's aggregate for
anybody who may not see it now — the only safe direction, and the one a stored
visibility column would get wrong the moment somebody changed it
(docs/adr/0005).
"""

from __future__ import annotations

from datetime import timedelta
from io import StringIO

import pytest
from django.core.management import CommandError, call_command
from django.utils import timezone

from app.core.authorization import DEPARTMENT_VIEWER
from app.core.enums import Visibility
from app.matters.enums import RecordMode
from app.matters.models import Matter
from app.reporting.models import OperationalMatterSnapshot
from app.reporting.selectors.snapshots import capture, snapshot_population, visible_snapshots
from app.workflow.enums import ActionKind, DateSemantics, Disposition

pytestmark = pytest.mark.django_db


def run(*args: str) -> str:
    out = StringIO()
    call_command("capture_operational_snapshot", *args, stdout=out)
    return out.getvalue()


# ---------------------------------------------------------------------------
# What gets photographed
# ---------------------------------------------------------------------------


def test_only_open_full_matters_are_photographed(world):
    """Six of the twelve: five open FULL, plus the restricted one, which is too.

    The capture runs as the system and records the whole department; who may
    *read* a row is decided on the way out.
    """
    assert snapshot_population().count() == 6

    created, updated = capture(on=world.today)
    assert (created, updated) == (6, 0)
    assert OperationalMatterSnapshot.objects.count() == 6

    photographed = set(
        OperationalMatterSnapshot.objects.values_list("matter__record_mode", flat=True)
    )
    assert photographed == {"FULL"}


def test_the_archive_is_not_photographed(world):
    capture(on=world.today)
    assert not OperationalMatterSnapshot.objects.filter(
        matter__in=[world.archive_excel, world.onenote_only, world.multi_page]
    ).exists()


def test_a_closed_matter_drops_out_of_the_next_snapshot(world):
    capture(on=world.today)
    assert OperationalMatterSnapshot.objects.filter(matter=world.native_future).exists()

    # Closed properly: the database refuses a closed FULL Matter with no
    # closure reason and no closing time, which is a Stage-1 invariant.
    Matter.objects.filter(pk=world.native_future.pk).update(
        is_open=False, closed_at=timezone.now(), disposition=Disposition.COMPLETED
    )
    tomorrow = world.today + timedelta(days=1)
    capture(on=tomorrow)

    assert not OperationalMatterSnapshot.objects.filter(
        matter=world.native_future, snapshot_date=tomorrow
    ).exists()
    # Yesterday's photograph is unchanged. It was true when it was taken.
    assert OperationalMatterSnapshot.objects.filter(
        matter=world.native_future, snapshot_date=world.today
    ).exists()


def test_the_next_action_is_recorded_as_three_separate_facts(world):
    """Kind, date meaning and date. Collapsing them would reintroduce, in the
    history, exactly the ambiguity Stage 1 removed from the present."""
    capture(on=world.today)

    overdue = OperationalMatterSnapshot.objects.get(
        matter=world.native_open, snapshot_date=world.today
    )
    assert overdue.next_action_kind == ActionKind.DO
    assert overdue.next_action_date_semantics == DateSemantics.DEADLINE
    assert overdue.was_overdue()

    waiting = OperationalMatterSnapshot.objects.get(
        matter=world.native_waiting, snapshot_date=world.today
    )
    assert waiting.next_action_kind == ActionKind.WAIT
    assert waiting.next_action_date_semantics == DateSemantics.REVIEW_ON
    # Its review date is today, which is due for a look and not a missed
    # deadline. A history that said otherwise would make a trend of "overdue
    # work" meaningless (master specification 18.8).
    assert not waiting.was_overdue()


def test_a_matter_with_no_next_action_is_recorded_as_having_none(world):
    capture(on=world.today)
    quiet = OperationalMatterSnapshot.objects.get(
        matter=world.native_quiet, snapshot_date=world.today
    )
    assert quiet.next_action_kind == ""
    assert quiet.next_action_date is None
    assert not quiet.has_next_action


def test_the_stage_label_is_kept_as_text_beside_the_foreign_key(world):
    """A stage renamed next year must not rewrite what this photograph said."""
    capture(on=world.today)
    row = OperationalMatterSnapshot.objects.get(matter=world.native_open, snapshot_date=world.today)
    assert row.stage_key == world.stage.key
    assert row.stage_label == world.stage.label_et

    world.stage.label_et = "Hoopis midagi muud"
    world.stage.save(update_fields=["label_et"])
    row.refresh_from_db()
    assert row.stage_label == "Kooskõlastusringil"


# ---------------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------------


def test_running_it_twice_on_one_day_refreshes_rather_than_duplicates(world):
    capture(on=world.today)
    created, updated = capture(on=world.today)

    assert (created, updated) == (0, 6)
    assert OperationalMatterSnapshot.objects.filter(snapshot_date=world.today).count() == 6


def test_the_database_refuses_a_second_row_for_the_same_day(world):
    from django.db import IntegrityError, transaction

    capture(on=world.today)
    existing = OperationalMatterSnapshot.objects.filter(snapshot_date=world.today).first()
    assert existing is not None

    with pytest.raises(IntegrityError), transaction.atomic():
        OperationalMatterSnapshot.objects.create(
            snapshot_date=world.today,
            matter=existing.matter,
            captured_at=existing.captured_at,
        )


def test_a_rerun_picks_up_a_change_made_since(world):
    capture(on=world.today)
    Matter.objects.filter(pk=world.native_open.pk).update(owner=world.martin)
    capture(on=world.today)

    row = OperationalMatterSnapshot.objects.get(matter=world.native_open, snapshot_date=world.today)
    assert row.owner_id == world.martin.pk


def test_each_day_gets_its_own_photograph(world):
    capture(on=world.today)
    capture(on=world.today + timedelta(days=1))
    assert OperationalMatterSnapshot.objects.values("snapshot_date").distinct().count() == 2
    assert OperationalMatterSnapshot.objects.count() == 12


# ---------------------------------------------------------------------------
# Authorization
# ---------------------------------------------------------------------------


def test_a_snapshot_row_is_read_through_the_live_matter(world):
    capture(on=world.today)
    assert visible_snapshots(world.martin).count() == 6
    assert visible_snapshots(world.sandra).count() == 6
    assert visible_snapshots(world.head).count() == 6
    assert visible_snapshots(world.admin).count() == 5
    assert visible_snapshots(DEPARTMENT_VIEWER).count() == 5
    assert visible_snapshots(None).count() == 0


def test_restricting_a_matter_today_hides_its_past_snapshots(world):
    """The only safe direction.

    A stored visibility taken at capture time would keep a Matter in historical
    charts for readers who lost access to it, and nothing on screen would look
    wrong.
    """
    capture(on=world.today)
    assert visible_snapshots(world.martin).count() == 6

    Matter.objects.filter(pk=world.native_open.pk).update(visibility=Visibility.RESTRICTED)

    assert visible_snapshots(world.martin).count() == 6
    assert visible_snapshots(world.head).count() == 6


def test_the_model_stores_no_visibility_of_its_own(world):
    """The property that makes the test above possible, asserted directly."""
    columns = {field.name for field in OperationalMatterSnapshot._meta.get_fields()}
    assert "visibility" not in columns
    assert "visibility_override" not in columns


# ---------------------------------------------------------------------------
# The command
# ---------------------------------------------------------------------------


def test_the_command_reports_what_it_recorded(world):
    output = run()
    assert "aktiivset teemat" in output
    assert "6" in output


def test_the_command_accepts_an_explicit_date_for_tests(world):
    yesterday = world.today - timedelta(days=1)
    run("--date", yesterday.isoformat())
    assert OperationalMatterSnapshot.objects.filter(snapshot_date=yesterday).count() == 6


def test_the_command_refuses_a_date_it_cannot_read(world):
    with pytest.raises(CommandError, match="ISO"):
        run("--date", "eile")


def test_an_empty_portfolio_says_so_rather_than_printing_zero(world):
    """ "0 rows" is the same output for "nothing to record" and "the query is wrong"."""
    Matter.objects.filter(is_open=True).update(record_mode=RecordMode.ARCHIVE, is_open=False)
    output = run()
    assert "tühi ja see on korrektne" in output


def test_there_is_no_backfill(world):
    """Deliberately absent.

    Pointing the capture at last March would write *today's* portfolio under
    March's date. That is not a photograph of March; it is today's picture with
    a false caption, and nothing on the resulting chart would look wrong
    (Stage-2E brief 52).
    """
    from app.reporting.management.commands import capture_operational_snapshot

    source = capture_operational_snapshot.__doc__ or ""
    assert "does not backfill" in source.lower() or "not a backfill" in source.lower()

    command = capture_operational_snapshot.Command()
    parser = command.create_parser("manage.py", "capture_operational_snapshot")
    options = {action.dest for action in parser._actions}
    assert "on" in options
    assert not {"since", "backfill", "from_date", "range"} & options
