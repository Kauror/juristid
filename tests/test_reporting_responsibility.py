"""The responsibility dimension, and the two ways it is easy to get wrong.

The first failure is silent and expensive: grouping by ``Matter.owner`` and
reporting every colleague the register names but this system has no account for
as *Määramata*. The register is certain a named person is responsible; the only
uncertainty is whether that name resolves to a login, which is a different
question and not the one a portfolio chart asks (Stage-2F owner resolver,
brief 15, 67).

The second is the opposite: giving a genuinely unassigned Matter somebody's
name because a nearby record had one.

Everything else here is arithmetic — that a matrix's rows, columns and grand
total agree with the population, that the year axis carries no invented years,
and that a restricted Matter moves none of it.
"""

from __future__ import annotations

import pytest

from app.core.authorization import DEPARTMENT_VIEWER
from app.reporting import metric_catalogue as keys
from app.reporting.metric_types import MetricStatus
from app.reporting.selectors import responsibility
from app.reporting.services import compute
from tests.synthetic_statistics import HISTORICAL_NAME

pytestmark = pytest.mark.django_db


def segments(viewer, key, reporting_context, **kwargs) -> dict[str, int]:
    result = compute(key, reporting_context(viewer, **kwargs))
    return {segment.label: segment.value for segment in result.segments}


def matrix_of(viewer, key, reporting_context, **kwargs):
    return compute(key, reporting_context(viewer, **kwargs)).matrix


# ---------------------------------------------------------------------------
# The precedence rule itself
# ---------------------------------------------------------------------------


def test_the_precedence_prefers_the_source_name_over_the_account() -> None:
    """No database needed: this is the whole rule, in one function."""
    assert responsibility.label_for("Mari Ajalooline", "Sandra Testjurist") == "Mari Ajalooline"


def test_a_matter_with_no_source_name_falls_back_to_its_owner() -> None:
    assert responsibility.label_for("", "Sandra Testjurist") == "Sandra Testjurist"
    assert responsibility.label_for(None, "Sandra Testjurist") == "Sandra Testjurist"


def test_only_a_matter_with_neither_is_unassigned() -> None:
    assert responsibility.label_for("", "") == responsibility.UNASSIGNED_LABEL
    assert responsibility.label_for(None, None) == responsibility.UNASSIGNED_LABEL


def test_a_source_name_is_kept_whole_rather_than_truncated() -> None:
    """Not the first word. "Mari" and "Mari Ajalooline" are different people."""
    assert responsibility.label_for("  Mari Ajalooline  ", None) == "Mari Ajalooline"


# ---------------------------------------------------------------------------
# Active portfolio
# ---------------------------------------------------------------------------


def test_active_by_stage_counts_only_open_full_matters(world, reporting_context):
    """Open FULL only: a closed matter and an archive row are not active work.

    Martin sees five open FULL Matters and one closed one, plus five archive
    rows. All five open ones carry the fixture's stage except `native_quiet`.
    """
    result = compute(keys.ACTIVE_FULL_MATTERS_BY_STAGE, reporting_context(world.martin))
    assert result.value == 5
    by_stage = {segment.label: segment.value for segment in result.segments}
    assert by_stage["Kooskõlastusringil"] == 4
    assert by_stage["Hetkeseis määramata"] == 1


def test_active_by_stage_never_includes_the_archive(world, reporting_context):
    """The archive rows have no stage, and must not appear as unassigned work."""
    result = compute(keys.ACTIVE_FULL_MATTERS_BY_STAGE, reporting_context(world.martin))
    assert sum(segment.value for segment in result.segments) == 5
    # `MATTERS_BY_STAGE` over the whole corpus counts eleven, five of them
    # unassigned archive rows. The two metrics answer different questions and
    # the difference is the point of adding the second one.
    corpus = compute(keys.MATTERS_BY_STAGE, reporting_context(world.martin))
    assert corpus.value == 11


def test_the_active_stage_segments_open_only_the_active_population(
    client, world, reporting_context
):
    """Each bar links into the register with the same narrowing it counted."""
    client.force_login(world.martin)
    result = compute(keys.ACTIVE_FULL_MATTERS_BY_STAGE, reporting_context(world.martin))
    for segment in result.segments:
        assert "olek=avatud" in segment.url
        assert "liik=FULL" in segment.url
        assert "aasta=" not in segment.url


def test_a_named_historical_lawyer_is_never_reported_as_unassigned(
    world, responsibility_world, reporting_context
):
    """The failure this whole module exists to prevent.

    ``promoted_named`` has no ``Matter.owner`` — the resolver could not match
    the register's name to an account. Grouping by owner would file it under
    *Määramata*; the source name is what the register is certain about.
    """
    by_person = segments(
        world.martin, keys.ACTIVE_FULL_MATTERS_BY_RESPONSIBILITY, reporting_context
    )
    assert by_person[HISTORICAL_NAME] == 1
    assert responsibility_world.promoted_named.owner_id is None


def test_a_blank_source_cell_is_the_only_thing_that_becomes_unassigned(
    world, responsibility_world, reporting_context
):
    """Two ownerless open Matters: `native_quiet` and `promoted_blank`."""
    by_person = segments(
        world.martin, keys.ACTIVE_FULL_MATTERS_BY_RESPONSIBILITY, reporting_context
    )
    assert by_person[responsibility.UNASSIGNED_LABEL] == 2


def test_current_staff_appear_under_their_own_display_name(
    world, responsibility_world, reporting_context
):
    by_person = segments(
        world.martin, keys.ACTIVE_FULL_MATTERS_BY_RESPONSIBILITY, reporting_context
    )
    assert by_person["Sandra Testjurist"] == 2
    assert by_person["Martin Testjurist"] == 2


def test_the_responsibility_segments_add_up_to_the_population(
    world, responsibility_world, reporting_context
):
    result = compute(keys.ACTIVE_FULL_MATTERS_BY_RESPONSIBILITY, reporting_context(world.martin))
    assert sum(segment.value for segment in result.segments) == result.value == 7


def test_responsibility_segments_are_alphabetical_with_unassigned_last(
    world, responsibility_world, reporting_context
):
    """A neutral, stable order. Never by count: that is a league table."""
    result = compute(keys.ACTIVE_FULL_MATTERS_BY_RESPONSIBILITY, reporting_context(world.martin))
    labels = [segment.label for segment in result.segments]
    assert labels[-1] == responsibility.UNASSIGNED_LABEL
    assert labels[:-1] == sorted(labels[:-1])


def test_a_responsibility_segment_carries_no_link(world, responsibility_world, reporting_context):
    """The register filters on the resolved owner; this counts the source name.

    A link would open a list that disagrees with the number above it, which is
    the one thing every other segment on these pages is built never to do.
    """
    result = compute(keys.ACTIVE_FULL_MATTERS_BY_RESPONSIBILITY, reporting_context(world.martin))
    assert all(segment.url == "" for segment in result.segments)


# ---------------------------------------------------------------------------
# Year × responsibility
# ---------------------------------------------------------------------------


def test_the_year_matrix_reconciles_with_the_matter_population(
    world, responsibility_world, reporting_context
):
    """Grand total, row totals and column totals all agree with each other."""
    matrix = matrix_of(world.martin, keys.MATTERS_BY_YEAR_AND_RESPONSIBILITY, reporting_context)
    assert matrix is not None
    assert sum(row.total for row in matrix.rows) == matrix.grand_total
    assert sum(matrix.column_totals) == matrix.grand_total
    # Eleven visible Matters plus the two the extension added.
    assert matrix.grand_total == 13


def test_the_year_matrix_keeps_the_historical_name_as_its_own_column(
    world, responsibility_world, reporting_context
):
    matrix = matrix_of(world.martin, keys.MATTERS_BY_YEAR_AND_RESPONSIBILITY, reporting_context)
    assert matrix is not None
    assert HISTORICAL_NAME in matrix.columns
    row = next(row for row in matrix.rows if row.label == str(world.archive_year))
    cell = row.cells[matrix.columns.index(HISTORICAL_NAME)]
    assert cell.value == 1


def test_the_year_matrix_draws_no_year_the_population_has_nothing_in(
    world, responsibility_world, reporting_context
):
    """A row of zeros for 2013 would say the department did nothing that year."""
    matrix = matrix_of(world.martin, keys.MATTERS_BY_YEAR_AND_RESPONSIBILITY, reporting_context)
    assert matrix is not None
    labels = {row.label for row in matrix.rows}
    assert str(world.current_year - 3) not in labels


def test_the_year_matrix_keeps_the_unknown_year_as_a_marked_row(
    world, responsibility_world, reporting_context
):
    """The two OneNote-only Matters have no register year and are not dropped."""
    matrix = matrix_of(world.martin, keys.MATTERS_BY_YEAR_AND_RESPONSIBILITY, reporting_context)
    assert matrix is not None
    unknown = next(row for row in matrix.rows if row.is_unknown)
    assert unknown.label == "Teadmata aasta"
    assert unknown.total == 2


def test_the_year_matrix_columns_are_alphabetical_with_unassigned_last(
    world, responsibility_world, reporting_context
):
    matrix = matrix_of(world.martin, keys.MATTERS_BY_YEAR_AND_RESPONSIBILITY, reporting_context)
    assert matrix is not None
    assert matrix.columns[-1] == responsibility.UNASSIGNED_LABEL
    assert list(matrix.columns[:-1]) == sorted(matrix.columns[:-1])


def test_a_wide_matrix_folds_its_tail_into_a_labelled_column() -> None:
    """No silent cap. The fold is a column that says how many names it holds.

    Built directly rather than through a fixture with fourteen lawyers in it:
    what is under test is the folding rule, not the query.
    """
    counts = {year: {f"Jurist {index:02d}": 1 for index in range(15)} for year in (2020, 2021)}
    matrix = responsibility.matrix(
        row_header="Aasta",
        rows=[(2020, "2020"), (2021, "2021")],
        counts=counts,
    )
    assert responsibility.OTHER_COLUMN_LABEL in matrix.columns
    assert len(matrix.columns) == responsibility.MATRIX_COLUMN_LIMIT + 1
    assert "3" in matrix.folded_note
    # Folding moves counts; it never loses them.
    assert matrix.grand_total == 30


# ---------------------------------------------------------------------------
# Authorization
# ---------------------------------------------------------------------------


def test_the_restricted_matter_moves_no_responsibility_total(
    world, responsibility_world, reporting_context
):
    """Sandra owns one restricted Matter; Martin may not see it.

    Every aggregate that groups by a person has to be off by exactly one
    between the two readers, and by nothing else.
    """
    martin = compute(keys.ACTIVE_FULL_MATTERS_BY_RESPONSIBILITY, reporting_context(world.martin))
    sandra = compute(keys.ACTIVE_FULL_MATTERS_BY_RESPONSIBILITY, reporting_context(world.sandra))
    assert sandra.value == martin.value + 1

    by_martin = {segment.label: segment.value for segment in martin.segments}
    by_sandra = {segment.label: segment.value for segment in sandra.segments}
    assert by_sandra["Sandra Testjurist"] == by_martin["Sandra Testjurist"] + 1
    assert by_sandra[responsibility.UNASSIGNED_LABEL] == by_martin[responsibility.UNASSIGNED_LABEL]


def test_the_department_scope_never_sees_the_restricted_matter(
    world, responsibility_world, reporting_context
):
    department = compute(
        keys.ACTIVE_FULL_MATTERS_BY_RESPONSIBILITY, reporting_context(DEPARTMENT_VIEWER)
    )
    martin = compute(keys.ACTIVE_FULL_MATTERS_BY_RESPONSIBILITY, reporting_context(world.martin))
    assert department.value == martin.value


def test_the_year_matrix_is_scoped_before_it_is_grouped(
    world, responsibility_world, reporting_context
):
    martin = matrix_of(world.martin, keys.MATTERS_BY_YEAR_AND_RESPONSIBILITY, reporting_context)
    sandra = matrix_of(world.sandra, keys.MATTERS_BY_YEAR_AND_RESPONSIBILITY, reporting_context)
    assert martin is not None and sandra is not None
    assert sandra.grand_total == martin.grand_total + 1


# ---------------------------------------------------------------------------
# Wording
# ---------------------------------------------------------------------------


def test_no_responsibility_metric_calls_itself_a_workload(world, reporting_context):
    """A count of files is inventory. The label may deny it; it may not claim it."""
    for key in (
        keys.MATTERS_BY_RESPONSIBILITY,
        keys.ACTIVE_FULL_MATTERS_BY_RESPONSIBILITY,
        keys.MATTERS_BY_YEAR_AND_RESPONSIBILITY,
        keys.NEW_NATIVE_MATTERS_BY_RESPONSIBILITY_MONTH,
    ):
        result = compute(key, reporting_context(world.martin))
        text = " ".join(
            [
                result.definition.label_et,
                result.definition.description_et,
                result.definition.notes_et,
                *result.notes,
            ]
        ).lower()
        for forbidden in ("edetabel", "produktiivsus", "parim jurist", "tulemuslikkuse mõõt"):
            assert forbidden not in text, f"{key}: {forbidden}"
        for hedged in ("töökoormus", "tulemuslikkus"):
            if hedged in text:
                assert "ei ole" in text or "mitte" in text, f"{key}: {hedged}"


def test_an_empty_active_portfolio_declines_rather_than_reporting_zero_people(
    world, reporting_context
):
    """With the owner filter set to nobody the population is empty, not wrong."""
    result = compute(
        keys.ACTIVE_FULL_MATTERS_BY_RESPONSIBILITY,
        reporting_context(world.martin, owner_unreadable=True),
    )
    assert result.value == 0
    assert result.segments == ()
    assert result.status == MetricStatus.AVAILABLE
