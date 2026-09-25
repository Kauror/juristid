"""Every authorized result is reachable, and nothing else is (ENG-048, ENG-010).

Global search stopped at fifty results: `MAX_RESULTS` sliced the ranking and
there was no page parameter, so the fifty-first result could not be reached
from `/otsing/` at all. Search is now paginated server-side, with the same
numbered component and the same `leht` parameter as the register.

The second half is the authorization matrix the rewrite has to keep. The
participation half of the visibility rule reaches the collaborators through a
subquery now, not a join, and the substring tiers go through new indexes; a
restricted sentinel must stay invisible to every persona that may not read it,
through every tier, in every count, and on every page.
"""

from __future__ import annotations

from datetime import timedelta
from urllib.parse import urlencode

import pytest
from django.test import Client

from app.search.services import (
    RESULTS_PER_PAGE,
    matching_matter_ids,
    result_count,
    search_documents,
    search_page,
)
from tests import search_corpus

pytestmark = pytest.mark.django_db

#: A word nearly every row carries: the simple configuration keeps stop words,
#: so «ja» reaches every body in the synthetic corpus.
BROAD = "ja"


@pytest.fixture
def corpus(db):
    return search_corpus.build()


def _all_ids(query, user):
    return [pk for pk, *_ in search_documents(query=query, user=user).values_list("pk")]


def _paged_ids(query, user, per_page):
    ids, number = [], 1
    while True:
        found = search_page(query=query, user=user, page_number=number, per_page=per_page)
        ids += [(result.source_kind, result.matter.pk, result.snippet) for result in found.results]
        if not found.page.has_next():
            return ids, found
        number += 1


def test_more_than_a_hundred_and_twenty_results_are_all_reachable_page_by_page(corpus):
    user = corpus.specialist
    total = result_count(query=BROAD, user=user)
    assert total > 120

    pages = [search_page(query=BROAD, user=user, page_number=n) for n in (1, 2, 3)]
    assert [len(page.results) for page in pages[:2]] == [RESULTS_PER_PAGE, RESULTS_PER_PAGE]
    assert all(page.total == total for page in pages)

    ids = []
    number = 1
    while True:
        found = search_page(query=BROAD, user=user, page_number=number)
        ids += [id(result) for result in found.results]
        if not found.page.has_next():
            break
        number += 1
    assert number == -(-total // RESULTS_PER_PAGE)

    # The same rows, in the same order, as the unpaginated ranking — so no row
    # appears twice and none goes missing between pages.
    ranking = _all_ids(BROAD, user)
    paged = []
    for number in range(1, -(-total // RESULTS_PER_PAGE) + 1):
        paged += [
            pk
            for pk, *_ in search_documents(query=BROAD, user=user).values_list("pk")[
                (number - 1) * RESULTS_PER_PAGE : number * RESULTS_PER_PAGE
            ]
        ]
    assert paged == ranking
    assert len(set(paged)) == total


def test_the_same_page_is_the_same_rows_on_every_request(corpus):
    first = [pk for pk, *_ in search_documents(query=BROAD, user=corpus.reader).values_list("pk")]
    for _ in range(3):
        again = [
            pk for pk, *_ in search_documents(query=BROAD, user=corpus.reader).values_list("pk")
        ]
        assert again == first


def test_a_reader_gets_full_pages_with_no_holes_where_restricted_rows_are(corpus):
    """Authorization is in the query being sliced, so a hidden row is not a gap."""
    lawyer_total = result_count(query=BROAD, user=corpus.specialist)
    reader_total = result_count(query=BROAD, user=corpus.reader)
    assert reader_total < lawyer_total

    per_page = 20
    number, sizes = 1, []
    while True:
        found = search_page(query=BROAD, user=corpus.reader, page_number=number, per_page=per_page)
        sizes.append(len(found.results))
        if not found.page.has_next():
            break
        number += 1
    assert all(size == per_page for size in sizes[:-1])
    assert sum(sizes) == reader_total


def test_the_page_links_carry_the_query_and_say_which_page_this_is(corpus):
    client = Client()
    client.force_login(corpus.specialist)
    response = client.get("/otsing/?" + urlencode({"q": BROAD, "leht": 2}))
    html = response.content.decode()

    assert response.status_code == 200
    assert f"?q={BROAD}&amp;leht=3" in html
    assert f"?q={BROAD}&amp;leht=1" in html
    assert 'aria-current="page">2<' in html
    assert f"{RESULTS_PER_PAGE + 1}–{2 * RESULTS_PER_PAGE}" in html
    assert "kuvatud" not in html


@pytest.mark.parametrize(
    "value",
    ["abc", "-1", "0", "1.5", "", "99999999999999999999999", "2'; DROP TABLE x;--", "٢"],
)
def test_a_malformed_page_number_is_a_page_not_an_error(corpus, value):
    client = Client()
    client.force_login(corpus.specialist)
    response = client.get("/otsing/", {"q": BROAD, "leht": value})
    assert response.status_code == 200
    assert b'aria-current="page"' in response.content


def test_a_page_past_the_end_shows_the_last_page_rather_than_nothing(corpus):
    total = result_count(query=BROAD, user=corpus.specialist)
    last = -(-total // RESULTS_PER_PAGE)
    found = search_page(query=BROAD, user=corpus.specialist, page_number=last + 40)
    assert found.page.number == last
    assert found.results


def test_a_short_result_set_has_no_pagination(corpus):
    client = Client()
    client.force_login(corpus.specialist)
    response = client.get("/otsing/", {"q": search_corpus.SENTINELS["engagement_title"]})
    assert response.status_code == 200
    assert b'class="pagination"' not in response.content


# -- the authorization matrix -------------------------------------------------

LAWYERS = ("specialist", "other_specialist", "department_head")
EVERYONE = (*LAWYERS, "reader", "administrator", "department_viewer")

#: Which personas may read each sentinel, and why.
EXPECTED = {
    # Ordinary rows: every authenticated persona, the shared gate included.
    "matter_title": EVERYONE,
    "development_note": EVERYONE,
    "engagement_title": EVERYONE,
    "position_summary": EVERYONE,
    "submission_summary": EVERYONE,
    "entry_body": EVERYONE,
    "fragment_body": EVERYONE,
    "document_title": EVERYONE,
    # A RESTRICTED Matter: the two lawyer roles by role, nobody else.
    "restricted_matter_title": LAWYERS,
    # A RESTRICTED Matter on which the READER collaborates.
    "collaborator_matter_title": (*LAWYERS, "reader"),
    # RESTRICTED children of an ordinary Matter the READER does not work on.
    "restricted_development_note": LAWYERS,
    "restricted_document_title": LAWYERS,
}


def test_every_sentinel_is_found_by_exactly_the_personas_that_may_read_it(corpus):
    personas = search_corpus.personas(corpus)
    for sentinel, allowed in EXPECTED.items():
        word = search_corpus.SENTINELS[sentinel]
        for persona, user in personas.items():
            count = result_count(query=word, user=user)
            page = search_page(query=word, user=user)
            ids = _all_ids(word, user)
            if persona in allowed:
                assert count >= 1, (sentinel, persona)
            else:
                # Nothing: not a row, not a count, not a page.
                assert (count, page.total, page.results, ids) == (0, 0, [], []), (
                    sentinel,
                    persona,
                )


def test_the_restricted_sentinels_do_not_leak_through_any_tier(corpus):
    """Substring, fuzzy, diacritic-free and document-name input alike."""
    restricted = corpus.restricted_matter
    probes = [
        search_corpus.SENTINELS["restricted_matter_title"],
        search_corpus.SENTINELS["restricted_matter_title"].lower()[3:12],  # substring
        search_corpus.SENTINELS["restricted_matter_title"][:-1] + "x",  # fuzzy
        restricted.title,  # exact title
        f"{restricted.reference_year}_{restricted.reference_number}",  # reference
        "piiratud_zqx.pdf",  # restricted document's filename
        "zqxpiiratudmarge",  # diacritic-free
    ]
    for user in (corpus.reader, corpus.administrator):
        for probe in probes:
            assert _all_ids(probe, user) == [], probe
            assert (
                not matching_matter_ids(query=probe, user=user)
                .filter(matter_id=restricted.pk)
                .exists()
            ), probe
        client = Client()
        client.force_login(user)
        for probe in probes:
            body = client.get("/otsing/", {"q": probe}).content.decode()
            # The box echoes the query back; the page must hold no result.
            assert 'class="result"' not in body, probe
            assert "0 vastet" in body, probe
            suggestions = client.get("/otsing/soovitused/", {"q": probe}).json()
            assert suggestions["results"] == [], probe


def test_a_break_glass_grant_opens_restricted_rows_to_the_reader(corpus):
    from app.accounts.services import grant_break_glass

    word = search_corpus.SENTINELS["restricted_development_note"]
    lawyer = result_count(query=word, user=corpus.specialist)
    assert lawyer >= 1
    assert result_count(query=word, user=corpus.reader) == 0
    grant_break_glass(
        user=corpus.reader,
        granted_by=corpus.department_head,
        reason="Sünteetiline juhtum",
        duration=timedelta(hours=1),
    )
    assert result_count(query=word, user=corpus.reader) == lawyer


def test_a_removed_note_and_a_deleted_matter_are_gone_from_every_persona(corpus):
    from django.utils import timezone

    from app.matters.deletion import delete_matter
    from app.matters.models import MatterProceduralDevelopment
    from app.search.indexing import refresh_development

    word = search_corpus.SENTINELS["development_note"]
    note = MatterProceduralDevelopment.objects.get(note__contains=word)
    note.removed_at = timezone.now()
    note.save(update_fields=["removed_at"])
    refresh_development(note)
    for user in search_corpus.personas(corpus).values():
        assert result_count(query=word, user=user) == 0

    doomed = corpus.matters[1]
    title_word = doomed.title.split()[0]
    before = result_count(query=title_word, user=corpus.specialist)
    delete_matter(matter=doomed, actor=corpus.specialist)
    after = result_count(query=title_word, user=corpus.specialist)
    assert after < before
    assert (
        not search_documents(query=title_word, user=corpus.specialist)
        .filter(matter_id=doomed.pk)
        .exists()
    )
