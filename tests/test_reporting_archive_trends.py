"""The opinions archive as history, and the four claims it must not make.

Read the numbers here as a specification of
``tests/synthetic_statistics.add_archive_world``: ten distinct letters filed as
eleven occurrences, dated 2020 to 2023, with the newest at 30 June 2023.

The four failures under test:

* **counting occurrences as letters.** The fixture files one letter twice on
  purpose, so a trend that counted rows would read eleven where the truth is
  ten (brief 27);
* **treating an absent measurement as a zero.** With no archive at all, the
  metrics decline; with an archive that starts in 2020, no bar is drawn for
  2019 (brief 28, 29);
* **comparing a part year with a whole one.** The cutoff is June, so the
  previous year's September and December letters are outside the comparison
  and the fixture holds two of them to prove it (brief 33, 72);
* **turning archive evidence into a sent opinion.** The archive count and
  ``SUBMISSIONS_SENT`` move independently, and the fixture makes them differ
  (brief 24, 39, 77).
"""

from __future__ import annotations

from datetime import date

import pytest

from app.reporting import metric_catalogue as keys
from app.reporting.metric_types import MetricStatus
from app.reporting.selectors import archive, responsibility
from app.reporting.services import compute
from tests.synthetic_statistics import ARCHIVE_BASE_YEAR

pytestmark = pytest.mark.django_db


def result_of(viewer, key, reporting_context, **kwargs):
    return compute(key, reporting_context(viewer, **kwargs))


def segments(viewer, key, reporting_context, **kwargs) -> dict[str, int]:
    return {
        segment.label: segment.value
        for segment in result_of(viewer, key, reporting_context, **kwargs).segments
    }


# ---------------------------------------------------------------------------
# Before anything has been catalogued
# ---------------------------------------------------------------------------


ARCHIVE_METRICS = (
    keys.OPINION_ARCHIVE_BY_YEAR,
    keys.OPINION_ARCHIVE_BY_MONTH,
    keys.OPINION_ARCHIVE_YOY_CHANGE,
    keys.OPINION_ARCHIVE_LINK_COVERAGE,
    keys.OPINION_ARCHIVE_LINKED_BY_RESPONSIBILITY,
    keys.OPINION_ARCHIVE_LINKED_BY_MONTH_RESPONSIBILITY,
)


@pytest.mark.parametrize("key", ARCHIVE_METRICS)
def test_an_empty_archive_declines_rather_than_reporting_zero(key, world, reporting_context):
    """The state this branch ships into, and the one that misleads hardest.

    A chart reading "0" across a year axis is a confident claim that Koda sent
    nothing. What is true before P3 runs is that nobody has catalogued anything.
    """
    result = compute(key, reporting_context(world.martin))
    assert result.status == MetricStatus.INSUFFICIENT_DATA, key
    assert result.value == 0, key
    assert result.segments == (), key


def test_the_empty_state_says_why_rather_than_implying_silence(world, reporting_context):
    result = compute(keys.OPINION_ARCHIVE_BY_YEAR, reporting_context(world.martin))
    joined = " ".join(result.notes).lower()
    assert "ei ole veel kataloogitud" in joined
    assert "ei tähenda, et arvamusi ei saadetud" in joined


# ---------------------------------------------------------------------------
# The year trend
# ---------------------------------------------------------------------------


def test_the_year_trend_counts_distinct_letters_not_occurrences(
    world, archive_world, reporting_context
):
    """Eleven occurrences, ten letters. The trend reports ten."""
    by_year = segments(world.martin, keys.OPINION_ARCHIVE_BY_YEAR, reporting_context)
    assert by_year == {
        str(ARCHIVE_BASE_YEAR): 2,
        str(ARCHIVE_BASE_YEAR + 1): 1,
        str(ARCHIVE_BASE_YEAR + 2): 4,
        str(ARCHIVE_BASE_YEAR + 3): 3,
    }
    assert sum(by_year.values()) == 10


def test_a_second_copy_of_a_letter_is_dated_by_its_earliest_occurrence(
    world, archive_world, reporting_context
):
    """The duplicate is filed in 2021 and its letter is from 2020.

    Counting the later copy would move a bar between two years, which is a
    quiet way for a filing habit to look like advocacy volume.
    """
    by_year = segments(world.martin, keys.OPINION_ARCHIVE_BY_YEAR, reporting_context)
    assert by_year[str(ARCHIVE_BASE_YEAR)] == 2
    assert by_year[str(ARCHIVE_BASE_YEAR + 1)] == 1


def test_the_occurrence_inventory_still_reports_the_larger_number(
    world, archive_world, reporting_context
):
    """Both numbers are true and the workspace keeps them apart (brief 27)."""
    occurrences = compute(keys.OPINION_ARCHIVE_OCCURRENCES, reporting_context(world.martin))
    distinct = compute(keys.OPINION_ARCHIVE_DISTINCT_BINARIES, reporting_context(world.martin))
    assert occurrences.value == 11
    assert distinct.value == 10


def test_the_year_trend_draws_no_year_before_the_archive_begins(
    world, archive_world, reporting_context
):
    """2019 is not a zero. There is no archive measurement for it (brief 29)."""
    by_year = segments(world.martin, keys.OPINION_ARCHIVE_BY_YEAR, reporting_context)
    assert str(ARCHIVE_BASE_YEAR - 1) not in by_year
    assert str(ARCHIVE_BASE_YEAR - 9) not in by_year


def test_the_archive_and_matter_trends_may_start_in_different_years(
    world, archive_world, reporting_context
):
    """And must: the register begins in 2011 and the archive in 2020.

    Aligning them by inserting archive zeros is the false fact this rule exists
    to refuse (brief 43).
    """
    matters = compute(keys.MATTERS_BY_REPORTING_YEAR, reporting_context(world.martin))
    matter_years = {segment.label for segment in matters.segments}
    archive_years = set(segments(world.martin, keys.OPINION_ARCHIVE_BY_YEAR, reporting_context))
    assert str(world.archive_year) in matter_years
    assert str(world.archive_year) not in archive_years


def test_the_year_trend_says_which_date_it_is_measured_on(world, archive_world, reporting_context):
    """Never *väljasaadetud*. The model's own comment calls it a signal."""
    result = compute(keys.OPINION_ARCHIVE_BY_YEAR, reporting_context(world.martin))
    joined = " ".join(result.notes).lower()
    assert "failinime kuupäev" in joined
    assert "mitte" in joined and "väljasaatmise aeg" in joined
    assert "väljasaadetud arvamused" not in result.definition.label_et.lower()


def test_the_year_trend_states_that_matter_filters_do_not_narrow_it(
    world, archive_world, reporting_context
):
    """An archive occurrence names no Matter, so a Matter filter cannot apply.

    Saying so is the alternative the brief allows to silently applying the
    wrong join (brief 44).
    """
    filtered = compute(
        keys.OPINION_ARCHIVE_BY_YEAR,
        reporting_context(world.martin, stage_key=world.stage.key),
    )
    unfiltered = compute(keys.OPINION_ARCHIVE_BY_YEAR, reporting_context(world.martin))
    assert filtered.value == unfiltered.value
    assert any("teemafiltrid ei kitsenda" in note.lower() for note in filtered.notes)


# ---------------------------------------------------------------------------
# The month trend and the coverage cutoff
# ---------------------------------------------------------------------------


def test_the_month_axis_stops_at_the_last_measured_date(world, archive_world, reporting_context):
    """The newest letter is dated 30 June, so July onward is not drawn.

    Drawing them as zeros would report six months of silence that nobody
    measured (brief 31).
    """
    by_month = segments(
        world.martin,
        keys.OPINION_ARCHIVE_BY_MONTH,
        reporting_context,
        period=str(ARCHIVE_BASE_YEAR + 3),
    )
    assert list(by_month) == ["Jaan", "Veebr", "Märts", "Apr", "Mai", "Juuni"]
    assert by_month["Jaan"] == 1
    assert by_month["Märts"] == 1
    assert by_month["Juuni"] == 1
    # Quiet months inside the measured window *are* measured zeros and are drawn.
    assert by_month["Veebr"] == 0


def test_a_multi_year_month_axis_never_pools_the_januaries(world, archive_world, reporting_context):
    """Two Januaries from two years are two bars, not one (brief 22)."""
    by_month = segments(world.martin, keys.OPINION_ARCHIVE_BY_MONTH, reporting_context)
    labels = list(by_month)
    assert f"{ARCHIVE_BASE_YEAR}-03" in labels
    assert f"{ARCHIVE_BASE_YEAR + 3}-01" in labels
    assert "Jaan" not in labels


def test_the_coverage_cutoff_is_derived_from_the_evidence(world, archive_world):
    assert archive.coverage_cutoff() == archive_world.cutoff == date(ARCHIVE_BASE_YEAR + 3, 6, 30)


def test_the_cutoff_is_visible_beside_the_numbers_that_depend_on_it(
    world, archive_world, reporting_context
):
    result = compute(keys.OPINION_ARCHIVE_BY_YEAR, reporting_context(world.martin))
    assert any("30.06." in note for note in result.notes)


# ---------------------------------------------------------------------------
# Year on year, at the same cutoff
# ---------------------------------------------------------------------------


def test_the_comparison_cuts_both_years_at_the_same_date(world, archive_world, reporting_context):
    """Three letters this year to 30 June; two last year to 30 June.

    The fixture's previous year also holds a September and a December letter.
    They exist precisely to be excluded: a partial year measured against a full
    one reports a collapse every time (brief 33, 72).
    """
    result = compute(keys.OPINION_ARCHIVE_YOY_CHANGE, reporting_context(world.martin))
    comparison = result.comparison
    assert comparison is not None
    assert comparison.current_value == 3
    assert comparison.previous_value == 2
    assert comparison.absolute_change == 1
    assert comparison.coverage_cutoff == archive_world.cutoff


def test_the_comparison_prints_both_windows_rather_than_implying_them(
    world, archive_world, reporting_context
):
    comparison = compute(
        keys.OPINION_ARCHIVE_YOY_CHANGE, reporting_context(world.martin)
    ).comparison
    assert comparison is not None
    assert comparison.current_period_label.startswith("1.1.2023")
    assert comparison.current_period_label.endswith("30.6.2023")
    assert comparison.previous_period_label.startswith("1.1.2022")
    assert comparison.previous_period_label.endswith("30.6.2022")


def test_the_change_is_worded_neutrally(world, archive_world, reporting_context):
    """+1, "1 rohkem". Never "parem", never a colour that has to be seen."""
    comparison = compute(
        keys.OPINION_ARCHIVE_YOY_CHANGE, reporting_context(world.martin)
    ).comparison
    assert comparison is not None
    assert comparison.change_text.startswith("+1")
    assert "1 rohkem" in comparison.change_text
    assert comparison.percent_text == "+50,0%"
    for forbidden in ("parem", "halvem", "kasv", "langus", "edukam"):
        assert forbidden not in comparison.change_text.lower()


def test_a_previous_period_of_zero_yields_a_difference_and_no_percentage(world, reporting_context):
    """A change from nothing has no percentage. It has a difference (brief 73).

    The whole archive here sits inside one year, so the previous comparable
    window is genuinely empty rather than merely small — which is the only way
    to reach the branch that must not print an infinity.
    """
    from tests.synthetic_statistics import add_archive_world

    add_archive_world(
        world,
        cutoff=date(ARCHIVE_BASE_YEAR, 6, 30),
        letters=[
            ("uks", date(ARCHIVE_BASE_YEAR, 2, 2)),
            ("kaks", date(ARCHIVE_BASE_YEAR, 6, 30)),
        ],
    )
    comparison = compute(
        keys.OPINION_ARCHIVE_YOY_CHANGE, reporting_context(world.martin)
    ).comparison
    assert comparison is not None
    assert comparison.current_value == 2
    assert comparison.previous_value == 0
    assert comparison.percent_change is None
    assert comparison.percent_text == ""
    assert comparison.absolute_change == 2


def test_the_matter_comparison_uses_the_same_rule(world, reporting_context):
    """New native Matters, cut at today on both sides (brief 35)."""
    result = compute(keys.NEW_NATIVE_MATTERS_YOY_CHANGE, reporting_context(world.martin))
    comparison = result.comparison
    assert comparison is not None
    assert comparison.coverage_cutoff == world.today
    assert str(world.today.year) in comparison.current_period_label
    assert str(world.today.year - 1) in comparison.previous_period_label


# ---------------------------------------------------------------------------
# Links to Matters
# ---------------------------------------------------------------------------


def test_link_coverage_counts_a_multi_matter_letter_once(world, archive_world, reporting_context):
    """One letter on two Matters is one covered letter, not two (brief 74)."""
    result = compute(keys.OPINION_ARCHIVE_LINK_COVERAGE, reporting_context(world.martin))
    assert result.coverage_denominator == 10
    assert result.coverage_count == 2
    assert result.value == 20


def test_link_coverage_separates_linked_from_unlinked(world, archive_world, reporting_context):
    by_state = segments(world.martin, keys.OPINION_ARCHIVE_LINK_COVERAGE, reporting_context)
    assert by_state["Teemaga seotud"] == 2
    assert by_state["Teemaga sidumata"] == 8


def test_an_unlinked_letter_is_never_called_a_missing_opinion(
    world, archive_world, reporting_context
):
    result = compute(keys.OPINION_ARCHIVE_LINK_COVERAGE, reporting_context(world.martin))
    joined = " ".join([*result.notes, result.definition.notes_et]).lower()
    assert "ei ole puuduv arvamus" in joined
    assert "puuduv arvamus," not in result.definition.label_et.lower()


def test_a_letter_on_two_lawyers_matters_appears_under_both(
    world, archive_world, reporting_context
):
    """And the segments therefore add up to more than the corpus total.

    That is the honest arithmetic of a many-to-many relation. The alternative —
    picking a primary Matter — would file a letter under an arbitrary person
    (brief 37, 75).
    """
    result = compute(keys.OPINION_ARCHIVE_LINKED_BY_RESPONSIBILITY, reporting_context(world.martin))
    by_person = {segment.label: segment.value for segment in result.segments}
    assert by_person["Sandra Testjurist"] == 2
    assert by_person["Martin Testjurist"] == 1
    assert sum(by_person.values()) == 3
    assert result.value == 2


def test_the_multiplicity_is_stated_rather_than_left_to_be_discovered(
    world, archive_world, reporting_context
):
    result = compute(keys.OPINION_ARCHIVE_LINKED_BY_RESPONSIBILITY, reporting_context(world.martin))
    joined = " ".join([*result.notes, result.definition.description_et]).lower()
    assert "mitut teemat" in joined
    assert "ületada" in joined


def test_the_month_matrix_counts_a_letter_once_per_lawyer_per_month(
    world, archive_world, reporting_context
):
    result = compute(
        keys.OPINION_ARCHIVE_LINKED_BY_MONTH_RESPONSIBILITY,
        reporting_context(world.martin),
    )
    matrix = result.matrix
    assert matrix is not None
    assert [row.label for row in matrix.rows] == ["Jaan", "Märts"]
    january = matrix.rows[0]
    assert january.total == 2
    assert matrix.grand_total == 3


def test_a_pending_candidate_is_not_a_link(world, archive_world, reporting_context):
    """A proposal nobody accepted is not coverage (brief 36).

    A PENDING candidate on the one unlinked letter must move nothing: the link
    layer is what the metrics read.
    """
    from app.legacy_import.opinion_archive import OpinionArchiveItem, OpinionMatchCandidate
    from app.legacy_import.opinion_enums import OpinionCandidateState

    before = compute(keys.OPINION_ARCHIVE_LINK_COVERAGE, reporting_context(world.martin)).value
    item = OpinionArchiveItem.objects.filter(sha256=archive_world.unlinked_sha).first()
    assert item is not None
    OpinionMatchCandidate.objects.create(
        item=item,
        matter=world.native_open,
        batch=archive_world.batch,
        match_class="REVIEW_REQUIRED",
        state=OpinionCandidateState.PENDING,
    )
    after = compute(keys.OPINION_ARCHIVE_LINK_COVERAGE, reporting_context(world.martin)).value
    assert after == before


# ---------------------------------------------------------------------------
# Authorization
# ---------------------------------------------------------------------------


def test_a_link_to_a_restricted_matter_never_reaches_another_reader(
    world, archive_world, reporting_context
):
    """Sandra's restricted Matter carries one archive link. Martin sees none of it.

    The failure this catches is the usual one: counting the links and hiding the
    rows leaves the hidden Matter inside the coverage percentage.
    """
    martin = compute(keys.OPINION_ARCHIVE_LINK_COVERAGE, reporting_context(world.martin))
    sandra = compute(keys.OPINION_ARCHIVE_LINK_COVERAGE, reporting_context(world.sandra))
    assert martin.coverage_count == 2
    assert sandra.coverage_count == 3
    assert martin.coverage_denominator == sandra.coverage_denominator == 10


def test_the_restricted_matters_responsibility_does_not_appear(
    world, archive_world, reporting_context
):
    martin = {
        segment.label: segment.value
        for segment in compute(
            keys.OPINION_ARCHIVE_LINKED_BY_RESPONSIBILITY, reporting_context(world.martin)
        ).segments
    }
    sandra = {
        segment.label: segment.value
        for segment in compute(
            keys.OPINION_ARCHIVE_LINKED_BY_RESPONSIBILITY, reporting_context(world.sandra)
        ).segments
    }
    assert martin["Sandra Testjurist"] == 2
    assert sandra["Sandra Testjurist"] == 3
    assert martin["Martin Testjurist"] == sandra["Martin Testjurist"] == 1


def test_the_archive_inventory_itself_is_the_same_for_every_reader(
    world, archive_world, reporting_context
):
    """An unlinked occurrence names no Matter: a filename, a size and a hash.

    It keeps the existing archive reporting rule of being counted for everyone
    (brief 45, 78).
    """
    for viewer in (world.martin, world.sandra):
        assert compute(keys.OPINION_ARCHIVE_BY_YEAR, reporting_context(viewer)).value == 10


# ---------------------------------------------------------------------------
# Archive is not a Submission
# ---------------------------------------------------------------------------


def test_the_archive_trend_and_the_submission_metric_are_independent(
    world, archive_world, reporting_context
):
    """The fixture makes them differ, so a conflation cannot pass unnoticed."""
    archive_total = compute(keys.OPINION_ARCHIVE_BY_YEAR, reporting_context(world.martin)).value
    sent = compute(keys.SUBMISSIONS_SENT, reporting_context(world.martin)).value
    assert archive_total == 10
    assert sent == 3


def test_a_new_canonical_submission_moves_only_the_canonical_metric(
    world, archive_world, reporting_context
):
    from tests.synthetic_statistics import _submission

    before_archive = compute(keys.OPINION_ARCHIVE_BY_YEAR, reporting_context(world.martin)).value
    before_sent = compute(keys.SUBMISSIONS_SENT, reporting_context(world.martin)).value

    _submission(
        world.native_waiting,
        title="Uus kanooniline arvamus",
        sent_on=date(world.current_year, 6, 6),
    )

    assert compute(keys.OPINION_ARCHIVE_BY_YEAR, reporting_context(world.martin)).value == (
        before_archive
    )
    assert compute(keys.SUBMISSIONS_SENT, reporting_context(world.martin)).value == (
        before_sent + 1
    )


def test_every_archive_metric_says_it_is_not_a_sent_submission(
    world, archive_world, reporting_context
):
    for key in (
        keys.OPINION_ARCHIVE_YOY_CHANGE,
        keys.OPINION_ARCHIVE_LINKED_BY_RESPONSIBILITY,
    ):
        result = compute(key, reporting_context(world.martin))
        joined = " ".join(result.notes).lower()
        assert "ei ole kanooniline saadetud arvamus" in joined, key


def test_no_archive_metric_labels_itself_as_a_send_date(world, archive_world):
    """`filename_date` is a matching signal, and every label says so (brief 26)."""
    for key in ARCHIVE_METRICS:
        from app.reporting.metric_catalogue import definition

        spec = definition(key)
        label = spec.label_et.lower()
        assert "väljasaatmise" not in label
        assert "välja saadetud" not in label
        assert "saadetud arvamused" not in label


def test_the_unassigned_label_is_shared_with_the_matter_metrics(
    world, archive_world, reporting_context
):
    """One string, so a reader does not meet two spellings of the same bucket."""
    assert responsibility.UNASSIGNED_LABEL == "Määramata"
