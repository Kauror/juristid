"""Dokumendid counts and slices in the database (ENG-125).

The tab loaded every document of the Matter — evidence and working references
— and then kept twelve rows of evidence. The rows shown, the count, the «Näita
rohkem» remainder and the year filter are unchanged; what changed is that the
default page reads twelve evidence rows, not all of them.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from app.documents.models import Document
from app.matters.views import DOCUMENT_PAGE_SIZE
from tests import factories

pytestmark = pytest.mark.django_db

EVIDENCE = 60
WORKING = 3


@pytest.fixture
def dense(specialist):
    matter = factories.MatterFactory(owner=specialist)
    moment = datetime(2023, 12, 31, 23, 30, tzinfo=UTC)
    for index in range(EVIDENCE):
        document = factories.DocumentFactory(matter=matter, title=f"Tõend {index:03d}")
        # Spread over years, one of them on the UTC new-year edge.
        Document.objects.filter(pk=document.pk).update(
            created_at=moment - timedelta(days=13 * index)
        )
    for index in range(WORKING):
        factories.DocumentFactory(
            matter=matter, title=f"Töödokument {index}", sharepoint_item_id=f"item-{index}"
        )
    return matter


def _page(client, matter, **params):
    return client.get(reverse("matters:matter_documents", kwargs={"pk": matter.pk}), params)


def _expected_evidence(matter):
    return list(
        Document.objects.filter(matter=matter, sharepoint_item_id="")
        .order_by("-created_at")
        .values_list("pk", flat=True)
    )


def test_the_default_page_shows_the_newest_twelve_and_counts_the_rest(client, specialist, dense):
    client.force_login(specialist)
    response = _page(client, dense)

    shown = [document.pk for document in response.context["evidence_documents"]]
    assert shown == _expected_evidence(dense)[:DOCUMENT_PAGE_SIZE]
    assert response.context["evidence_total"] == EVIDENCE
    assert response.context["evidence_hidden"] == EVIDENCE - DOCUMENT_PAGE_SIZE
    assert len(response.context["working_documents"]) == WORKING


def test_show_all_shows_every_evidence_row_in_the_same_order(client, specialist, dense):
    client.force_login(specialist)
    response = _page(client, dense, koik="1")

    shown = [document.pk for document in response.context["evidence_documents"]]
    assert shown == _expected_evidence(dense)
    assert response.context["evidence_hidden"] == 0


def test_the_year_filter_offers_the_years_the_rows_have(client, specialist, dense):
    client.force_login(specialist)
    response = _page(client, dense)

    # In the active zone, the year the filter applies and the row prints: the
    # fixture's UTC new-year edge (31.12.2023 23:30 UTC) is 2024 in Tallinn.
    expected = sorted(
        {
            timezone.localtime(document.created_at).year
            for document in Document.objects.filter(matter=dense)
        },
        reverse=True,
    )
    assert response.context["document_years"] == expected
    assert 2024 in expected


def test_the_year_offered_is_the_year_the_filter_finds(client, specialist):
    """A file uploaded 1.01.2026 at 00:30 in Tallinn is a 2026 file, offered and found.

    The options were read in UTC (2025) while `?aasta=` filters in Tallinn
    (2026): the offered year found nothing and the file's own year was not
    offered (bounty A-03).
    """
    matter = factories.MatterFactory(owner=specialist)
    document = factories.DocumentFactory(matter=matter, title="Uusaasta öö dokument")
    Document.objects.filter(pk=document.pk).update(
        created_at=datetime(2025, 12, 31, 22, 30, tzinfo=UTC)
    )
    client.force_login(specialist)

    assert _page(client, matter).context["document_years"] == [2026]
    found = _page(client, matter, aasta="2026").context["evidence_documents"]
    assert [row.pk for row in found] == [document.pk]
    assert list(_page(client, matter, aasta="2025").context["evidence_documents"]) == []


def test_the_default_page_does_not_load_every_document(client, specialist, dense):
    """The evidence rows arrive through one LIMITed query; none reads them all."""
    client.force_login(specialist)
    with CaptureQueriesContext(connection) as queries:
        _page(client, dense)

    reads = [
        query["sql"]
        for query in queries.captured_queries
        if re.search(r'FROM "documents_document"\s', query["sql"])
        and '"documents_document"."title"' in query["sql"]
    ]
    # The evidence rows: `sharepoint_item_id = ''`, and not the working
    # references' `NOT (... = '')`.
    evidence_reads = [
        sql
        for sql in reads
        if "sharepoint_item_id\" = ''" in sql
        and 'NOT ("documents_document"."sharepoint_item_id"' not in sql
    ]
    assert evidence_reads, reads
    assert all(f"LIMIT {DOCUMENT_PAGE_SIZE}" in sql for sql in evidence_reads), evidence_reads
