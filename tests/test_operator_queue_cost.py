"""The two reconciliation queues cost a fixed number of queries.

Both are operator surfaces that render up to 200 rows, and both had the same
two shapes: one ``COUNT`` per class in a dict comprehension, and a
``select_related`` that named every relation the template touches except
``decided_by``.

The second one is the interesting half, because it hid itself. ``decided_by``
is null on a PENDING row, and Django answers a null foreign key without going
to the database — so the default view of the queue was free, and the cost only
appeared once somebody filtered to the rows they had already decided, which is
the moment the page is being used to check work rather than to do it.

These assert a bound rather than an exact number: the point is that the cost
does not grow with the number of rows.
"""

from __future__ import annotations

import pytest
from django.urls import reverse
from django.utils import timezone

from app.legacy_import.opinion_archive import (
    OpinionArchiveBatch,
    OpinionArchiveItem,
    OpinionMatchCandidate,
)
from app.legacy_import.opinion_enums import OpinionCandidateState, OpinionMatchClass
from app.legacy_import.source_pages import (
    CandidateClass,
    CandidateState,
    HistoricalMatchCandidate,
    LegacySourcePage,
)
from tests import factories

pytestmark = pytest.mark.django_db

#: Enough rows that a per-row query is unmistakable, and few enough to stay
#: fast. Both queues slice at 200.
ROWS = 12

#: Page furniture: session, user, the candidate list, its total, the grouped
#: pending counts, and the messages framework. Comfortably above what either
#: view needs and far below one-query-per-row.
QUERY_BUDGET = 12


def _decided_rows(count, make):
    """`count` candidates, every one of them already decided by somebody.

    Decided on purpose. A PENDING row has a null ``decided_by``, which costs
    nothing to render and hides exactly the defect being tested for.
    """
    return [make(index) for index in range(count)]


@pytest.fixture
def opinion_backlog(db, administrator, specialist):
    batch = OpinionArchiveBatch.objects.create(
        archive_file_name="arvamused.zip",
        archive_sha256="a" * 64,
        importer_version="test/1.0.0",
        started_at=timezone.now(),
    )
    matter = factories.MatterFactory(owner=specialist)

    def build(index):
        item = OpinionArchiveItem.objects.create(
            batch=batch,
            archive_sha256="a" * 64,
            archive_relative_path=f"2024/arvamus-{index}.pdf",
            original_filename=f"arvamus-{index}.pdf",
            sha256=f"{index:064d}",
            size_bytes=1024,
            detected_type="application/pdf",
        )
        return OpinionMatchCandidate.objects.create(
            item=item,
            matter=matter,
            batch=batch,
            match_class=OpinionMatchClass.REVIEW_REQUIRED,
            state=OpinionCandidateState.DEFERRED,
            decided_by=specialist,
            decided_at=timezone.now(),
        )

    return _decided_rows(ROWS, build)


@pytest.fixture
def historical_backlog(db, specialist):
    matter = factories.MatterFactory(owner=specialist)

    def build(index):
        page = LegacySourcePage.objects.create(
            source_page_id=f"page-{index}",
            page_key=f"leht-{index}",
            source_notebook="Õigusosakond",
            source_section="2024",
            title=f"Ajalooline leht {index}",
            capture_id="test-capture",
            first_imported_at=timezone.now(),
            latest_imported_at=timezone.now(),
        )
        return HistoricalMatchCandidate.objects.create(
            source_page=page,
            matter=matter,
            candidate_class=CandidateClass.REVIEW_REQUIRED,
            state=CandidateState.REJECTED,
            decided_by=specialist,
            decided_at=timezone.now(),
        )

    return _decided_rows(ROWS, build)


def test_the_opinion_queue_cost_does_not_grow_with_the_backlog(
    client, administrator, opinion_backlog, django_assert_max_num_queries
):
    client.force_login(administrator)
    url = reverse("legacy_import:opinion_queue")

    with django_assert_max_num_queries(QUERY_BUDGET):
        response = client.get(url, {"olek": OpinionCandidateState.DEFERRED})

    assert response.status_code == 200
    assert len(response.context["candidates"]) == ROWS


def test_the_historical_queue_cost_does_not_grow_with_the_backlog(
    client, administrator, historical_backlog, django_assert_max_num_queries
):
    client.force_login(administrator)
    url = reverse("legacy_import:review_queue")

    with django_assert_max_num_queries(QUERY_BUDGET):
        response = client.get(url, {"olek": CandidateState.REJECTED})

    assert response.status_code == 200


def test_every_class_keeps_a_row_in_the_filter_strip(client, administrator, opinion_backlog):
    """A class with nothing pending reads as finished, never as non-existent.

    The grouped query only returns classes that have rows, so the zeroes have
    to be filled in — otherwise replacing seven counts with one would quietly
    delete most of the filter strip.
    """
    client.force_login(administrator)

    response = client.get(reverse("legacy_import:opinion_queue"))

    assert set(response.context["counts"]) == set(OpinionMatchClass.values)


def test_the_historical_filter_strip_keeps_every_class_too(
    client, administrator, historical_backlog
):
    client.force_login(administrator)

    response = client.get(reverse("legacy_import:review_queue"))

    assert set(response.context["counts"]) == {klass.value for klass in CandidateClass}
