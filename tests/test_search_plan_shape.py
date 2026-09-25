"""Every search is answerable from the projection's indexes (ENG-010).

A PostgreSQL contract, not a stopwatch. Nothing here reads a timing or a cost:
those move with hardware, statistics and minor versions, and a test that does
is a test that is red for reasons unrelated to the code.

**What is asserted is servability, with sequential scans switched off.** Whether
PostgreSQL *prefers* the indexes is a cost decision it makes from statistics,
and on a CI-sized table the two paths can cost within a few percent of each
other — a planner choosing either is correct, and a test pinning one would be
flaky. Whether it *can* use them is a property of the SQL, and it is the
property the old query lacked: its tiers were ORed together with arms no text
index could serve (the joined Matter title, a `word_similarity` annotation,
`UPPER(x) LIKE`). Denied a sequential scan, PostgreSQL covers such an arm with
whatever index matches its *other* condition — `source_kind IN (…)` through
`search_matter_kind` — which returns every row of those kinds: a full scan
under another name. So the contract is which indexes the projection is reached
through. The new query reaches it through its text indexes and nothing else;
the frozen reference query needs the kind index to cover arms no text index
answers, and is held to that, so this module shows the difference rather than
asserting it.

That the planner does choose the indexes at production scale — and a single
parallel pass for a word in half the corpus, which is the right plan for that
word — is measured on synthetic corpora of 17,000 to 147,000 rows in the
round's benchmark report, not here.

Also asserted, without any setting: nothing de-duplicates rows, because nothing
can multiply them. The READER and administrator path used to join the
collaborators and then `DISTINCT` about 260 columns.
"""

from __future__ import annotations

import json
import random

import pytest
from django.db import connection

from app.accounts.models import User
from app.matters.models import Matter
from app.search.indexing import _recompute_vectors
from app.search.models import INDEX_VERSION, SearchDocument, SearchSourceKind
from app.search.services import matching_matter_ids, search_documents

pytestmark = pytest.mark.django_db

ROWS = 4000
RARE = "zqxharuldanesõna"
WORDS = (
    "seadus seaduse eelnõu arvamus arvamuse tähtaeg tähtaja ministeerium kooskõlastus "
    "ettepanek määrus maks keskkond ettevõte riigihange muudatus kohustus toetus leping "
    "direktiiv menetlus järelevalve aruanne üleminekuaeg halduskoormus pakend aktsiis"
).split()

KINDS = tuple(kind for kind in SearchSourceKind if kind != SearchSourceKind.LEGACY_SOURCE_PAGE)

TEXT_INDEXES = {
    "search_estonian_gin",
    "search_simple_gin",
    "search_title_gin",
    "search_title_trgm",
    "search_identifiers_trgm",
    "search_alias_trgm",
    # Round 6's recall and author tiers (ENG-031, ENG-083).
    "search_folded_gin",
    "search_people_trgm",
}


@pytest.fixture
def projection(db):
    rng = random.Random("plan-shape")  # noqa: S311 - synthetic test text, not a secret
    owner = User.objects.create(
        email="plan@example.invalid",
        upn="plan@example.invalid",
        display_name="Plaan",
        role="SPECIALIST",
    )
    matters = Matter.objects.bulk_create(
        [
            Matter(
                title=f"Plaaniteema {n} {' '.join(rng.sample(WORDS, 4))}",
                owner=owner,
                reference_year=2025,
                reference_number=70000 + n,
                visibility="RESTRICTED" if n % 10 == 0 else "NORMAL",
            )
            for n in range(60)
        ]
    )
    rows = []
    for n in range(ROWS):
        matter = matters[n % len(matters)]
        body = " ".join(rng.choice(WORDS) for _ in range(120))
        if n == ROWS // 2 + 1:  # on an ordinary Matter, so every persona may read it
            body += f" {RARE}"
        rows.append(
            SearchDocument(
                matter=matter,
                # Every kind, in equal parts, as a real projection has them — a
                # kind that matched nothing would be answered, correctly, from
                # the kind index instead. Not real sources: rows without a source
                # object are what the projection's shape allows, and all this
                # needs is rows. The rare word is on a MATTER row, which a
                # READER may read through its Matter alone.
                source_kind=KINDS[n % len(KINDS)]
                if n != ROWS // 2 + 1
                else SearchSourceKind.MATTER,
                title=f"Pealkiri {n} {' '.join(rng.sample(WORDS, 5))}",
                identifiers=f"2025_{n}",
                alias_text=" ".join(rng.sample(WORDS, 3)),
                body_text=body,
                index_version=INDEX_VERSION,
                indexed_at=matter.created_at,
            )
        )
    SearchDocument.objects.bulk_create(rows, batch_size=1000)
    _recompute_vectors(SearchDocument.objects.all())
    with connection.cursor() as cursor:
        # A GIN index takes bulk inserts into a pending list, and the planner
        # costs a scan of the list as a scan of everything in it. Production's
        # autovacuum folds the list in; a test inside one transaction has to
        # ask, or every text index looks as expensive as the table.
        for index in sorted(TEXT_INDEXES):
            cursor.execute("SELECT gin_clean_pending_list(%s::regclass)", [index])
        cursor.execute("ANALYZE search_searchdocument")
        cursor.execute("ANALYZE matters_matter")
    reader = User.objects.create(
        email="plan-reader@example.invalid",
        upn="plan-reader@example.invalid",
        display_name="Lugeja",
        role="READER",
    )
    return owner, reader


def _plan(queryset, *, indexes_first: bool = True) -> dict:
    sql, params = queryset.query.sql_with_params()
    with connection.cursor() as cursor:
        if indexes_first:
            cursor.execute("SET LOCAL enable_seqscan = off")
        try:
            cursor.execute("EXPLAIN (FORMAT JSON) " + sql, params)
            (plan,) = cursor.fetchone()
        finally:
            cursor.execute("SET LOCAL enable_seqscan = on")
    if isinstance(plan, str):
        plan = json.loads(plan)
    return plan[0]["Plan"]


def _nodes(node: dict):
    yield node
    for child in node.get("Plans", []) or []:
        yield from _nodes(child)


def _shape(queryset, *, indexes_first: bool = True) -> tuple[set[str], list[dict]]:
    nodes = list(_nodes(_plan(queryset, indexes_first=indexes_first)))
    return {node["Index Name"] for node in nodes if "Index Name" in node}, nodes


def _projection_indexes() -> set[str]:
    with connection.cursor() as cursor:
        cursor.execute("SELECT indexname FROM pg_indexes WHERE tablename = 'search_searchdocument'")
        return {name for (name,) in cursor.fetchall()}


def _assert_selective(queryset, label: str) -> None:
    indexes, nodes = _shape(queryset)
    scans = [
        node["Node Type"] for node in nodes if node.get("Relation Name") == "search_searchdocument"
    ]
    reached_through = indexes & _projection_indexes()
    assert reached_through, (label, indexes)
    # Only text indexes: a kind or a foreign-key index here would be covering
    # an arm no text index can answer, by reading every row of a kind.
    assert reached_through <= TEXT_INDEXES, (label, reached_through)
    assert "Seq Scan" not in scans, (label, scans)
    # Nothing de-duplicates: no row can appear twice, so nothing needs to.
    _, natural = _shape(queryset, indexes_first=False)
    assert not [node for node in natural if node["Node Type"] == "Unique"], label


@pytest.mark.parametrize("persona", ["lawyer", "reader"])
def test_a_rare_word_is_found_through_the_indexes_for_every_persona(projection, persona):
    owner, reader = projection
    user = owner if persona == "lawyer" else reader
    ranked = search_documents(query=RARE, user=user)
    assert ranked.count() == 1

    _assert_selective(ranked.values_list("pk", "match_tier", "relevance")[:50], "page")
    _assert_selective(ranked.values_list("pk"), "ids")


def test_the_count_and_the_register_filter_are_selective_too(projection):
    owner, reader = projection
    for user in (owner, reader):
        ranked = search_documents(query=RARE, user=user)
        _assert_selective(ranked.order_by(), "count")
        register = Matter.objects.filter(pk__in=matching_matter_ids(query=RARE, user=user))
        indexes, _ = _shape(register)
        assert indexes & TEXT_INDEXES, indexes


def test_the_page_query_carries_no_body_text_through_its_sort(projection):
    """The ranking statement selects ids and sort keys; the body is fetched for
    the page's rows afterwards."""
    owner, _ = projection
    sql, _ = (
        search_documents(query=RARE, user=owner)
        .values_list("pk", "match_tier", "relevance")[:50]
        .query.sql_with_params()
    )
    select = sql.split(" FROM ", 1)[0]
    assert '"body_text"' not in select
    assert '"search_estonian"' not in select.split("CASE", 1)[0]


def test_a_title_typo_is_answered_by_the_trigram_index(projection):
    owner, _ = projection
    indexes, _ = _shape(search_documents(query="Plaaniteemx 17", user=owner).values_list("pk")[:50])
    assert "search_title_trgm" in indexes


def test_the_query_as_it_stood_before_could_only_scan(projection):
    """The frozen pre-ENG-010 query, for contrast: no setting makes it use a
    text index. Denied a sequential scan it reads the whole table through some
    other index instead, which is the same work under another name."""
    from tests.search_reference import reference_search_documents

    owner, reader = projection
    for user in (owner, reader):
        indexes, nodes = _shape(
            reference_search_documents(query=RARE, user=user).values_list("pk")[:50]
        )
        covered_by_something_else = (indexes & _projection_indexes()) - TEXT_INDEXES
        scanned = [
            node
            for node in nodes
            if node.get("Relation Name") == "search_searchdocument"
            and node["Node Type"] == "Seq Scan"
        ]
        assert covered_by_something_else or scanned, indexes
    _, natural = _shape(
        reference_search_documents(query=RARE, user=reader).values_list("pk")[:50],
        indexes_first=False,
    )
    assert [node for node in natural if node["Node Type"] in ("Unique", "HashAggregate")]


def test_every_tsquery_is_a_literal_so_a_scan_does_not_recompute_it(projection):
    """No query-side function a scan would evaluate per row (ADR 0117).

    ``to_tsquery(regconfig, 'literal')`` is immutable and folded at planning;
    ``unaccent`` is only stable, so written into the query it ran for every
    row a broad term's sequential scan tested. Diacritics are folded once, in
    a round trip of their own, before the query is built.
    """
    owner, _ = projection
    sql, params = search_documents(query="tahtaja eelnõu", user=owner).query.sql_with_params()
    assert "unaccent" not in sql
    assert any(isinstance(p, str) and "eelnou" in p for p in params)  # folded, as a literal


def test_folding_is_postgresqls_own_and_cannot_inject_syntax(db):
    """The query side folds with the function the index side folds with.

    Python's decomposition leaves «ß» and «æ» alone; PostgreSQL's ``unaccent``
    does not, and a vector folded one way never matches a query folded the
    other. And a character that folds into punctuation («½» becomes « 1/2»)
    comes back as word runs only, never as tsquery syntax.
    """
    from app.search.services import _folded_words

    assert _folded_words(("Straße", "tähtaja", "Æble")) == ("Strasse", "tahtaja", "AEble")
    assert _folded_words(("½",)) == ("1", "2")
    assert _folded_words(()) == ()
