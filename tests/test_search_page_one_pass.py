"""The search page reads its total beside its rows, in one pass over the matches (JPERF-006).

The 2026-10-17 audit found word search scanning the whole projection twice for a
common word: a `COUNT(*)` for the total, then the ranking statement for the
page — both a parallel sequential scan of every search row, because a word in
15 % of the corpus is past where any index helps. `search_page` now asks the
ranking statement for ``COUNT(*) OVER ()`` as well, so one scan answers both.

Held here:

* **The same page, for every persona and every page number.** The oracle is the
  code this replaced — `Paginator(_ranked(...)).get_page(...)` — run in the same
  test on the search suite's own corpus, which has restricted Matters, restricted
  children and every persona the authorization rules distinguish. Rows, order,
  tiers, relevance, the total, the page number and the page links must agree,
  including for malformed and out-of-range page numbers.
* **One statement fewer.** The page issues no separate `COUNT(*)` over the
  projection when it has rows to carry the total — a window where an index
  answers the term, a scalar subquery where the planner reads everything — and
  stays within the audit's budget of 10 queries for the whole search view.
"""

from __future__ import annotations

import pytest
from django.core.paginator import Paginator
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from app.search import services
from tests import search_corpus

pytestmark = pytest.mark.django_db

#: PERFORMANCE-AUDIT.md §10: word search ≤ 10 queries.
SEARCH_QUERY_BUDGET = 10

QUERIES = ("seadus", "seaduse eelnõu", "tähtaja", "xylofonimängija")
PAGE_NUMBERS = (None, 1, 2, 3, "2", 99, 0, -1, "abc", 1.5, 2.0, "", "99999999999999999999999")


@pytest.fixture
def corpus(db):
    return search_corpus.build()


def _old_page(query, user, page_number, per_page):
    """`search_page`'s paging as it stood: a `COUNT(*)`, then the slice."""
    term = services.clean_query(query)
    return Paginator(services._ranked(term, user), per_page).get_page(page_number)


def _shape(page):
    return (
        [(pk, tier, round(relevance or 0.0, 6)) for pk, tier, relevance in page.object_list],
        page.number,
        page.paginator.count,
        page.paginator.num_pages,
        page.has_next(),
        page.has_previous(),
    )


@pytest.mark.parametrize("broad", [False, True])
def test_every_page_is_the_page_the_old_paging_drew(corpus, monkeypatch, broad):
    """Both branches: the single pass, and the two scans a broad term keeps."""
    monkeypatch.setattr(services, "_broad", lambda ranked: broad)
    compared = non_empty = 0
    for persona, user in search_corpus.personas(corpus).items():
        for query in QUERIES:
            for per_page in (7, services.RESULTS_PER_PAGE):
                for number in PAGE_NUMBERS:
                    term = services.clean_query(query)
                    new = services._counted_page(term, user, number, per_page)
                    old = _old_page(query, user, number, per_page)
                    assert _shape(new) == _shape(old), (persona, query, per_page, number)
                    compared += 1
                    non_empty += bool(old.object_list)
    assert non_empty > compared // 2


def test_the_rendered_results_are_the_same_for_a_reader_and_a_specialist(corpus):
    """Through `search_page` itself: the presented rows, in order, and the total."""
    for user in (corpus.specialist, corpus.reader):
        for number in (1, 2, 99):
            new = services.search_page(query="seadus", user=user, page_number=number, per_page=7)
            old = _old_page("seadus", user, number, 7)
            assert [r.matter.pk for r in new.results] == [
                services._present(list(old.object_list), term="seadus", user=user)[i].matter.pk
                for i in range(len(old.object_list))
            ]
            assert new.total == old.paginator.count


def test_a_page_with_rows_issues_no_separate_count(corpus, monkeypatch):
    # The corpus is small enough that the planner may scan it whole; the branch
    # under test is the index-answered one.
    monkeypatch.setattr(services, "_scans_sequentially", lambda node, table: False)
    with CaptureQueriesContext(connection) as captured:
        page = services.search_page(query="seadus", user=corpus.specialist, page_number=1)
    assert page.total > 0
    counts = [
        q["sql"]
        for q in captured.captured_queries
        if q["sql"].startswith("SELECT COUNT(*)") and "search_searchdocument" in q["sql"]
    ]
    assert not counts, counts
    assert any("COUNT(" in q["sql"] and "OVER ()" in q["sql"] for q in captured.captured_queries)


def test_a_term_the_planner_reads_the_whole_projection_for_keeps_its_count(corpus, monkeypatch):
    """A plan that scans everything keeps the `COUNT(*)`, inside the one ranking statement."""
    monkeypatch.setattr(services, "_scans_sequentially", lambda node, table: True)
    with CaptureQueriesContext(connection) as captured:
        page = services.search_page(query="seadus", user=corpus.specialist, page_number=1)
    assert page.total > 0
    sql = [q["sql"] for q in captured.captured_queries]
    assert any(q.startswith("EXPLAIN (FORMAT JSON)") for q in sql)
    assert any("(SELECT COUNT(*) FROM (" in q for q in sql)
    assert not any(q.startswith("SELECT COUNT(*)") for q in sql)
    assert not any("OVER ()" in q for q in sql)


@pytest.mark.parametrize("broad", [False, True])
def test_the_query_count_does_not_depend_on_the_plan(client, corpus, monkeypatch, broad):
    """Both branches issue the same statements, so a budget holds whichever the planner picks."""
    monkeypatch.setattr(services, "_scans_sequentially", lambda node, table: broad)
    client.force_login(corpus.specialist)
    with CaptureQueriesContext(connection) as captured:
        assert client.get(reverse("search:search"), {"q": "seadus"}).status_code == 200
    assert len(captured.captured_queries) <= SEARCH_QUERY_BUDGET
    counts = [q for q in captured.captured_queries if q["sql"].startswith("SELECT COUNT(*)")]
    assert not counts


def test_the_search_view_stays_within_its_query_budget(client, corpus):
    client.force_login(corpus.specialist)
    with CaptureQueriesContext(connection) as captured:
        response = client.get(reverse("search:search"), {"q": "seadus"})
    assert response.status_code == 200
    assert len(captured.captured_queries) <= SEARCH_QUERY_BUDGET


def test_the_broad_test_reads_the_planners_scan_of_the_projection():
    """A sequential scan of the projection anywhere in the plan is broad; an index plan is not."""
    table = "search_searchdocument"
    parallel = {
        "Node Type": "Gather",
        "Plans": [{"Node Type": "Seq Scan", "Relation Name": table, "Parallel Aware": True}],
    }
    indexed = {
        "Node Type": "Hash Join",
        "Plans": [
            {"Node Type": "Bitmap Heap Scan", "Relation Name": table, "Plans": []},
            {"Node Type": "Seq Scan", "Relation Name": "matters_matter"},
        ],
    }
    assert services._scans_sequentially(parallel, table)
    assert not services._scans_sequentially(indexed, table)
