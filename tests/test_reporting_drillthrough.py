"""Every number opens exactly the records it counted.

This is the promise the whole workspace rests on, and it is the promise that
quietly breaks. A chart and its list are built from similar-looking filters, one
of them gains a condition, and from then on the bar says 201 and the list shows
188 — with nothing on either screen to say which is wrong.

So the assertion here is deliberately end-to-end. It follows the URL the metric
actually rendered, through the real view, with the real authorization, and
compares the count the page reports with the number the card claimed. A test
that re-derived the population in Python would pass while the link was broken.

Three surfaces answer these links, and all three publish `total` in their
template context: the Teemad register, the Statistika submission list and the
Statistika material list.
"""

from __future__ import annotations

from urllib.parse import parse_qs, urlparse

import pytest

from app.reporting import metric_catalogue as keys
from app.reporting.services import compute

pytestmark = pytest.mark.django_db


#: Metrics whose headline value is a count of exactly what its link opens.
#: Metrics that deliberately carry no link, and metrics whose value is a sum of
#: bytes or a percentage, are covered by their own tests instead.
_LINKED_TOTALS = (
    keys.MATTERS_TOTAL,
    keys.ACTIVE_FULL_MATTERS,
    keys.MATTERS_UNCLASSIFIED_POLICY_AREA,
    keys.ACTIVE_FULL_MATTERS_BY_STAGE,
    keys.ACTIVE_FULL_MATTERS_BY_RESPONSIBILITY,
    keys.MATTERS_BY_RESPONSIBILITY,
    keys.MATTERS_WITH_HISTORICAL_SOURCE,
    keys.MATTERS_WITHOUT_HISTORICAL_SOURCE,
    keys.ONENOTE_ONLY_MATTERS,
    keys.MATTERS_WITH_MULTIPLE_SOURCE_PAGES,
    keys.ACTIVE_WITHOUT_NEXT_ACTION,
    keys.ACTIVE_WITHOUT_OWNER,
    keys.ACTIVE_WITHOUT_STAGE,
    keys.OVERDUE_DO_DEADLINE,
    keys.REVIEW_DUE,
    keys.SUBMISSIONS_SENT,
    keys.HISTORICAL_RESOURCE_OCCURRENCES,
    keys.MATERIALISATION_FAILED,
)

#: Metrics whose *segments* each open their own population.
_LINKED_SEGMENTS = (
    keys.MATTERS_BY_REPORTING_YEAR,
    keys.MATTERS_BY_RECORD_MODE,
    keys.MATTERS_BY_ORIGIN,
    keys.MATTERS_BY_STAGE,
    keys.ACTIVE_FULL_MATTERS_BY_STAGE,
    keys.MATTERS_BY_OWNER,
    keys.MATTERS_BY_POLICY_AREA,
    keys.MATTERS_BY_TRACK,
    keys.MATTERS_BY_TAG,
    keys.MATTERS_BY_SOURCE_ORGANISATION,
    keys.MATTERS_BY_ADDRESSEE_ORGANISATION,
    keys.HISTORICAL_SOURCE_COVERAGE_CLASSES,
    keys.HISTORICAL_RESOURCES_BY_TYPE,
    keys.MATERIALISATION_STATUS,
    keys.SUBMISSIONS_SENT_BY_PERIOD,
    keys.SUBMISSIONS_BY_RECIPIENT,
    keys.SUBMISSIONS_BY_KIND,
)


def total_at(client, url: str) -> int:
    """Follow one drill-through link and read the count the page reports."""
    parsed = urlparse(url)
    query = {key: values[0] for key, values in parse_qs(parsed.query).items()}
    response = client.get(parsed.path, query)
    assert response.status_code == 200, f"{url} -> {response.status_code}"
    assert response.context is not None, url
    return int(response.context["total"])


@pytest.mark.parametrize("key", _LINKED_TOTALS)
def test_a_metrics_value_equals_the_population_its_link_opens(
    key, client, world, reporting_context
):
    client.force_login(world.martin)
    result = compute(key, reporting_context(world.martin))
    assert result.drillthrough_url, f"{key} lost its link"
    assert total_at(client, result.drillthrough_url) == result.value, key


@pytest.mark.parametrize("key", _LINKED_SEGMENTS)
def test_every_chart_segment_opens_exactly_what_it_counted(key, client, world, reporting_context):
    client.force_login(world.martin)
    result = compute(key, reporting_context(world.martin))
    assert result.segments, f"{key} produced no segments"

    linked = [segment for segment in result.segments if segment.url]
    assert linked, f"{key} has no linked segment"
    for segment in linked:
        assert total_at(client, segment.url) == segment.value, f"{key} / {segment.label}"


def test_the_same_holds_for_a_reader_who_sees_more(client, world, reporting_context):
    """The invariant is about scoping, so it has to hold at both scopes.

    A link that hard-coded a filter would still agree with its own chart for one
    reader and disagree for another.
    """
    client.force_login(world.head)
    for key in (keys.MATTERS_TOTAL, keys.MATTERS_BY_OWNER, keys.HISTORICAL_RESOURCES_BY_TYPE):
        result = compute(key, reporting_context(world.head))
        if result.drillthrough_url:
            assert total_at(client, result.drillthrough_url) == result.value, key
        for segment in result.segments:
            if segment.url:
                assert total_at(client, segment.url) == segment.value, f"{key}/{segment.label}"


def test_a_period_filter_travels_into_the_drill_through(client, world, reporting_context):
    client.force_login(world.martin)
    result = compute(keys.MATTERS_TOTAL, reporting_context(world.martin, period="kaesolev"))
    assert "aasta=" in result.drillthrough_url
    assert result.value == 6
    assert total_at(client, result.drillthrough_url) == 6


def test_a_corpus_wide_card_does_not_carry_a_year_into_its_link(client, world, reporting_context):
    """A card labelled *kogu korpus* must not open this year's slice.

    The period is still selected in the URL bar; the link simply drops it,
    because the number above it was never narrowed by it.
    """
    client.force_login(world.martin)
    result = compute(
        keys.MATTERS_WITH_HISTORICAL_SOURCE, reporting_context(world.martin, period="kaesolev")
    )
    assert "aasta=" not in result.drillthrough_url
    assert total_at(client, result.drillthrough_url) == result.value == 4


def test_a_section_bar_narrows_the_tab_rather_than_opening_the_file_list(
    client, world, reporting_context
):
    """The bar counts pages; the file list counts occurrences.

    Linking a page count at a file list would open a longer list than the
    number promised, so the bar re-scopes the tab instead — and the page count
    on the re-scoped tab is the number the bar showed.
    """
    from app.reporting.selectors.historical import legacy_source_pages

    client.force_login(world.martin)
    result = compute(keys.LEGACY_SOURCE_PAGES_BY_SECTION, reporting_context(world.martin))
    for segment in result.segments:
        assert "/statistika/ajalooline/" in segment.url
        narrowed = reporting_context(world.martin, section=segment.label)
        assert legacy_source_pages(narrowed).value == segment.value


def test_the_reconciliation_classes_link_into_the_review_queue(client, world, reporting_context):
    """An administrator's queue, filtered to the class the bar counted."""
    client.force_login(world.admin)
    result = compute(keys.RECONCILIATION_BY_CLASS, reporting_context(world.admin))
    assert result.segments
    for segment in result.segments:
        parsed = urlparse(segment.url)
        response = client.get(parsed.path, {"olek": "PENDING", "klass": _klass(segment.url)})
        assert response.status_code == 200
        assert response.context["total"] == segment.value, segment.label


def _klass(url: str) -> str:
    return parse_qs(urlparse(url).query)["klass"][0]


def test_a_metric_that_cannot_link_honestly_carries_no_link(world, reporting_context):
    """Absent rather than wrong.

    Each of these counts something the product has no list of — evidence
    versions, distinct file contents, entries — so a link would have to open
    something else. A link that opens something else is worse than no link,
    because the reader believes it.
    """
    context = reporting_context(world.martin)
    for key in (
        keys.EXTRACTION_SUCCESS,
        keys.EXTRACTION_FAILED,
        keys.EXTRACTION_NOT_APPLICABLE,
        keys.SEARCHABLE_DOCUMENT_COVERAGE,
        keys.HISTORICAL_UNIQUE_BINARY_CONTENTS,
        keys.LEGACY_SOURCE_PAGES,
        keys.ENTRY_COUNT,
        keys.NEW_NATIVE_FULL_MATTERS,
        keys.NEW_NATIVE_FULL_MATTERS_BY_MONTH,
        keys.NEW_NATIVE_MATTERS_BY_RESPONSIBILITY_MONTH,
        keys.NEW_NATIVE_MATTERS_YOY_CHANGE,
        keys.MATTERS_BY_YEAR_AND_RESPONSIBILITY,
        keys.OPINION_ARCHIVE_BY_YEAR,
        keys.OPINION_ARCHIVE_BY_MONTH,
        keys.OPINION_ARCHIVE_YOY_CHANGE,
        keys.OPINION_ARCHIVE_LINK_COVERAGE,
        keys.OPINION_ARCHIVE_LINKED_BY_RESPONSIBILITY,
        keys.OPINION_ARCHIVE_LINKED_BY_MONTH_RESPONSIBILITY,
    ):
        result = compute(key, context)
        assert not result.drillthrough_url, key
        explanation = (
            result.notes or result.definition.notes_et or result.definition.drillthrough_et
        )
        assert explanation, f"{key} is unlinked and says nothing about why"


def test_the_grouped_tail_of_a_chart_is_never_a_link(world, reporting_context):
    """No filter opens "the other twenty-eight groups", so nothing pretends to."""
    from app.reporting.metric_types import Segment
    from app.reporting.selectors.base import top_segments

    rows = [Segment(label=str(index), value=index, url=f"/x?{index}") for index in range(20)]
    trimmed = top_segments(rows, limit=5)
    assert len(trimmed) == 6
    assert trimmed[-1].label == "Muud"
    assert trimmed[-1].url == ""
    assert trimmed[-1].value == sum(range(5, 20))
    assert "15" in trimmed[-1].note
