"""The indexed search answers exactly what the old query answered (ENG-010).

ENG-010 rewrote *how* search finds its rows — every tier now a predicate an
index can serve, the ranking statement slim, participation by subquery instead
of a join and a `DISTINCT` — and was not allowed to change *what* it finds. This
module holds that: over one synthetic corpus with every source kind, restricted
Matters and restricted children, it runs the query as it stood before the
rewrite (`tests/search_reference.py`, frozen at the Round-5 main) and the query
as it stands now, for every persona and every query below, and requires the same
rows, in the same order, in the same tiers, with the same relevance.

The query list is the golden corpus the round asked for: references in three
spellings, exact and inflected titles, organisation names and abbreviations,
tags and tag aliases, diacritic-free input, child titles, document names and
filenames, words that live only in a child's body, fuzzy typos, the three
restricted sentinels, operators, punctuation, LIKE metacharacters and input a
keyboard does not produce.
"""

from __future__ import annotations

import pytest

from app.search.services import (
    RESULTS_PER_PAGE,
    result_count,
    search_documents,
    search_page,
)
from tests import search_corpus
from tests.search_reference import reference_search_documents

STATIC_QUERIES = (
    # references, in every spelling a lawyer types
    "2021_9001",
    "2021-9001",
    "2021 9001",
    "2026_9902",
    "2031_1",
    # titles: exact words, inflections, phrases
    "Pakendiseadus",
    "pakendiseaduse",
    "Tarbijakaitseseadus muutmise",
    "seaduse eelnõu",
    "Riigihangete seadus",
    "Käibemaksuseadus täiendamise",
    "Jäätmeseadus ja seadus",
    # organisations and their names
    "MKM",
    "Rahandusministeerium",
    "Kliimaministeerium",
    "Põllumajandustootjate Liit",
    "pollumajandustootjate",
    "EMTA",
    "Tööandjate",
    # taxonomy
    "pakendid",
    "pakendiaruandlus",
    "Maksundus",
    "Tööõigus",
    # child titles, document names, filenames, references
    "Liikmete küsitlus",
    "Koja arvamus",
    "Seletuskiri",
    "seletuskiri_maks_8.docx",
    "lisa_zqx.pdf",
    "7-1/2024/15",
    "Ministeerium saatis",
    # body text of every kind
    "halduskoormust",
    "tähtaja",
    "üleminekuaega",
    "kooskõlastas",
    "riikliku",
    # fuzzy typos
    "Tarbijakaitseseadsu",
    "Kliimasedus",
    "Pakendiseadus muutmise seaduse eelnõu (pakendi kord)x",
    # sentinels, one per source kind, restricted ones included
    *search_corpus.SENTINELS.values(),
    # operators and punctuation
    '"seaduse eelnõu"',
    "seadus -maks",
    "seadus or maks",
    "50%",
    "a_b",
    "o'Brien",
    "(",
    "ž",
    "õäöü",
    "🙂 seadus",
    "100\\%",
    "%_%",
    "ma",
    "xylofonimängija",
)


@pytest.fixture
def corpus(db):
    return search_corpus.build()


def _ranked(queryset):
    return [(pk, tier, round(relevance, 6)) for pk, tier, relevance in queryset]


@pytest.mark.django_db
def test_every_persona_gets_exactly_the_old_answer_to_every_query(corpus):
    queries = (*STATIC_QUERIES, corpus.exact_title, corpus.exact_title.upper())
    assert len(queries) >= 40

    compared = 0
    non_empty = 0
    for persona, user in search_corpus.personas(corpus).items():
        for query in queries:
            old = _ranked(
                reference_search_documents(query=query, user=user).values_list(
                    "pk", "match_tier", "relevance"
                )
            )
            new = _ranked(
                search_documents(query=query, user=user).values_list(
                    "pk", "match_tier", "relevance"
                )
            )
            assert new == old, (persona, query)
            assert result_count(query=query, user=user) == len(old), (persona, query)
            compared += 1
            non_empty += bool(old)
    # The comparison has to have compared something: most queries find rows,
    # for every persona that is allowed to see them.
    assert non_empty > compared // 2


@pytest.mark.django_db
def test_the_pages_are_the_ranked_answer_cut_into_pieces(corpus):
    """Concatenated pages equal the full ranking — no row twice, none missing."""
    for user in (corpus.specialist, corpus.reader):
        full = [
            pk
            for pk, _, _ in reference_search_documents(query="seadus", user=user).values_list(
                "pk", "match_tier", "relevance"
            )
        ]
        assert len(full) > 10
        paged = []
        number = 1
        while True:
            page = search_page(query="seadus", user=user, page_number=number, per_page=7)
            paged += list(page.results)
            if not page.page.has_next():
                break
            number += 1
        assert page.total == len(full)
        assert len(paged) == len(full)
    assert RESULTS_PER_PAGE == 50
