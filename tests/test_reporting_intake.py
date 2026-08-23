"""Monthly intake: what arrived, when it arrived, and whose it became.

The one mistake that makes this whole chart worthless is measuring arrival on
``created_at``. Every row in the fixture was written today, and a chart built on
the database timestamp would report the day of the import as the busiest month
in the register's history. The population's clock is ``received_date`` and the
tests below are written to fail loudly if that ever changes.

The second mistake is inventing month precision for imported register rows. The
source gives those a reporting year and nothing finer, so they are excluded by
the definition rather than distributed across twelve months (brief 21, 68).
"""

from __future__ import annotations

from datetime import date

import pytest

from app.matters.enums import MatterOrigin, RecordMode
from app.matters.models import Matter
from app.reporting import metric_catalogue as keys
from app.reporting.metric_types import MetricStatus
from app.reporting.selectors import portfolio, responsibility
from app.reporting.services import compute

pytestmark = pytest.mark.django_db


def segments(viewer, key, reporting_context, **kwargs) -> dict[str, int]:
    result = compute(key, reporting_context(viewer, **kwargs))
    return {segment.label: segment.value for segment in result.segments}


# ---------------------------------------------------------------------------
# The month axis
# ---------------------------------------------------------------------------


def test_intake_is_grouped_by_the_day_the_material_arrived(world, reporting_context):
    """Three native Matters arrived in January, February and March.

    Every one of them was written to the database today, which is exactly the
    trap: a `created_at` axis would put all three in one bar.
    """
    by_month = segments(world.martin, keys.NEW_NATIVE_FULL_MATTERS_BY_MONTH, reporting_context)
    assert by_month == {"Jaan": 1, "Veebr": 1, "Märts": 1}


def test_the_axis_runs_between_the_first_and_last_measured_date(world, reporting_context):
    """It does not run to December. Nothing was measured there (brief 31)."""
    by_month = segments(world.martin, keys.NEW_NATIVE_FULL_MATTERS_BY_MONTH, reporting_context)
    assert "Dets" not in by_month
    assert list(by_month) == ["Jaan", "Veebr", "Märts"]


def test_an_imported_register_row_never_enters_the_intake_metric(world, reporting_context):
    """Its source gives a reporting year, not a month, and none is invented."""
    Matter.objects.create(
        title="Imporditud rida saabumise kuupäevaga",
        record_mode=RecordMode.FULL,
        origin=MatterOrigin.LEGACY_IMPORT,
        reference_year=world.current_year,
        reference_number=71,
        reporting_year=world.current_year,
        received_date=date(world.current_year, 2, 14),
    )
    by_month = segments(world.martin, keys.NEW_NATIVE_FULL_MATTERS_BY_MONTH, reporting_context)
    assert by_month["Veebr"] == 1


def test_an_archive_record_never_enters_it_either(world, reporting_context):
    Matter.objects.create(
        title="Arhiivikirje saabumise kuupäevaga",
        record_mode=RecordMode.ARCHIVE,
        origin=MatterOrigin.NATIVE,
        reference_year=world.current_year,
        reference_number=72,
        reporting_year=world.current_year,
        received_date=date(world.current_year, 2, 15),
    )
    by_month = segments(world.martin, keys.NEW_NATIVE_FULL_MATTERS_BY_MONTH, reporting_context)
    assert by_month["Veebr"] == 1


def test_a_multi_year_selection_carries_the_year_in_every_label(world, reporting_context):
    """Otherwise two Februaries from two years become one bar (brief 22)."""
    Matter.objects.create(
        title="Eelmisel aastal saabunud teema",
        record_mode=RecordMode.FULL,
        origin=MatterOrigin.NATIVE,
        reference_year=world.previous_year,
        reference_number=73,
        reporting_year=world.previous_year,
        owner=world.martin,
        received_date=date(world.previous_year, 2, 20),
    )
    by_month = segments(world.martin, keys.NEW_NATIVE_FULL_MATTERS_BY_MONTH, reporting_context)
    assert f"{world.previous_year}-02" in by_month
    assert f"{world.current_year}-02" in by_month
    assert "Veebr" not in by_month


def test_a_quiet_month_inside_the_window_is_a_measured_zero(world, reporting_context):
    """Different from a month outside the window, which is not drawn at all."""
    Matter.objects.create(
        title="Mais saabunud teema",
        record_mode=RecordMode.FULL,
        origin=MatterOrigin.NATIVE,
        reference_year=world.current_year,
        reference_number=74,
        reporting_year=world.current_year,
        owner=world.martin,
        received_date=date(world.current_year, 5, 5),
    )
    by_month = segments(world.martin, keys.NEW_NATIVE_FULL_MATTERS_BY_MONTH, reporting_context)
    assert by_month["Apr"] == 0
    assert by_month["Mai"] == 1
    assert "Juuni" not in by_month


def test_no_month_bar_carries_a_link_it_could_not_honour(world, reporting_context):
    """The register filters by year, and has no "has an arrival date" filter.

    So neither a bar nor the metric itself links: every list either surface
    could open would be longer than the number above it, which is the one thing
    a drill-through must never be (brief 58).
    """
    result = compute(keys.NEW_NATIVE_FULL_MATTERS_BY_MONTH, reporting_context(world.martin))
    assert all(segment.url == "" for segment in result.segments)
    assert result.drillthrough_url == ""
    assert result.definition.drillthrough_et


def test_intake_declines_when_nothing_carries_an_arrival_date(world, reporting_context):
    """Not a zero: an empty axis with a confident zero on it is a claim."""
    Matter.objects.filter(origin=MatterOrigin.NATIVE).update(received_date=None)
    result = compute(keys.NEW_NATIVE_FULL_MATTERS_BY_MONTH, reporting_context(world.martin))
    assert result.status == MetricStatus.INSUFFICIENT_DATA
    assert result.segments == ()


# ---------------------------------------------------------------------------
# Month × responsibility
# ---------------------------------------------------------------------------


def test_the_month_matrix_places_each_arrival_under_one_name(world, reporting_context):
    """January to Martin, February to Sandra, March to nobody."""
    matrix = compute(
        keys.NEW_NATIVE_MATTERS_BY_RESPONSIBILITY_MONTH, reporting_context(world.martin)
    ).matrix
    assert matrix is not None
    assert [row.label for row in matrix.rows] == ["Jaan", "Veebr", "Märts"]

    def cell(row_label: str, column: str) -> int:
        row = next(row for row in matrix.rows if row.label == row_label)
        return row.cells[matrix.columns.index(column)].value

    assert cell("Jaan", "Martin Testjurist") == 1
    assert cell("Veebr", "Sandra Testjurist") == 1
    assert cell("Märts", responsibility.UNASSIGNED_LABEL) == 1


def test_the_month_matrix_totals_reconcile_in_both_directions(world, reporting_context):
    matrix = compute(
        keys.NEW_NATIVE_MATTERS_BY_RESPONSIBILITY_MONTH, reporting_context(world.martin)
    ).matrix
    assert matrix is not None
    assert sum(row.total for row in matrix.rows) == matrix.grand_total == 3
    assert sum(matrix.column_totals) == matrix.grand_total


def test_a_matter_in_two_policy_areas_is_not_counted_twice(world, reporting_context):
    """The many-to-many joins in the population must not inflate a cell.

    `native_open` already carries a policy area and a confirmed tag; adding a
    second area is what turns a plain `Count` into a wrong number.
    """
    world.native_open.policy_areas.add(world.area_env)
    matrix = compute(
        keys.NEW_NATIVE_MATTERS_BY_RESPONSIBILITY_MONTH, reporting_context(world.martin)
    ).matrix
    assert matrix is not None
    assert matrix.grand_total == 3


def test_the_restricted_matter_is_absent_from_the_intake_matrix(world, reporting_context):
    """It arrived in April and is owned by Sandra."""
    martin = compute(
        keys.NEW_NATIVE_MATTERS_BY_RESPONSIBILITY_MONTH, reporting_context(world.martin)
    ).matrix
    sandra = compute(
        keys.NEW_NATIVE_MATTERS_BY_RESPONSIBILITY_MONTH, reporting_context(world.sandra)
    ).matrix
    assert martin is not None and sandra is not None
    assert martin.grand_total == 3
    assert sandra.grand_total == 4
    assert "Apr" not in [row.label for row in martin.rows]


# ---------------------------------------------------------------------------
# Year on year
# ---------------------------------------------------------------------------


def test_the_matter_comparison_is_cut_at_today_on_both_sides(world, reporting_context):
    result = compute(keys.NEW_NATIVE_MATTERS_YOY_CHANGE, reporting_context(world.martin))
    comparison = result.comparison
    assert comparison is not None
    assert comparison.coverage_cutoff == world.today
    assert comparison.previous_period_label.endswith(
        portfolio.same_day_last_year(world.today).strftime("%Y-%m-%d")
    )


def test_a_leap_day_cutoff_has_a_counterpart_in_a_common_year() -> None:
    """29 February must not roll into the previous year's 1 March."""
    assert portfolio.same_day_last_year(date(2024, 2, 29)) == date(2023, 2, 28)
    assert portfolio.same_day_last_year(date(2024, 3, 1)) == date(2023, 3, 1)


def test_the_matter_comparison_says_it_is_volume_rather_than_performance(world, reporting_context):
    result = compute(keys.NEW_NATIVE_MATTERS_YOY_CHANGE, reporting_context(world.martin))
    joined = " ".join(result.notes).lower()
    assert "mitte tulemuslikkus" in joined


# ---------------------------------------------------------------------------
# Query counts
# ---------------------------------------------------------------------------


def test_a_wider_department_does_not_cost_more_queries(world, reporting_context):
    """The matrix is one grouped query folded in Python, not one per person.

    Measured twice against two different populations rather than pinned to a
    number: what matters is that adding lawyers and years does not add round
    trips (brief 82).
    """
    from django.db import connection
    from django.test.utils import CaptureQueriesContext

    context = reporting_context(world.martin)
    # One warm-up call outside the measurement. Anything a first call caches —
    # a permission lookup, a vocabulary row — would otherwise make the second
    # capture *smaller* and the comparison meaningless.
    compute(keys.MATTERS_BY_YEAR_AND_RESPONSIBILITY, context)
    with CaptureQueriesContext(connection) as first:
        compute(keys.MATTERS_BY_YEAR_AND_RESPONSIBILITY, context)
    baseline = len(first)
    assert baseline > 0

    for index in range(8):
        Matter.objects.create(
            title=f"Lisateema {index}",
            record_mode=RecordMode.FULL,
            origin=MatterOrigin.NATIVE,
            reference_year=world.current_year - index,
            reference_number=200 + index,
            reporting_year=world.current_year - index,
            owner=world.martin if index % 2 else world.sandra,
        )

    with CaptureQueriesContext(connection) as second:
        compute(keys.MATTERS_BY_YEAR_AND_RESPONSIBILITY, context)
    assert len(second) == baseline


def test_the_archive_responsibility_breakdown_is_also_one_pass(
    world, archive_world, reporting_context
):
    """Same guard on the archive side: no query per lawyer or per letter."""
    from django.db import connection
    from django.test.utils import CaptureQueriesContext

    context = reporting_context(world.martin)
    compute(keys.OPINION_ARCHIVE_LINKED_BY_RESPONSIBILITY, context)
    with CaptureQueriesContext(connection) as first:
        compute(keys.OPINION_ARCHIVE_LINKED_BY_RESPONSIBILITY, context)
    baseline = len(first)
    assert baseline > 0

    from tests.synthetic_statistics import _archive_binary, _archive_item, _archive_link

    for index in range(6):
        payload = f"lisakiri-{index}"
        _archive_item(
            archive_world.batch,
            when=date(2023, 4, index + 1),
            title=f"Lisaarvamus {index}",
            payload=payload,
        )
        _archive_link(_archive_binary(payload), world.native_open)

    with CaptureQueriesContext(connection) as second:
        compute(keys.OPINION_ARCHIVE_LINKED_BY_RESPONSIBILITY, context)
    assert len(second) == baseline
