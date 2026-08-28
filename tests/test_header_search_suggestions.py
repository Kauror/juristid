"""The header search's live suggestions, and the navigation cleanup beside it.

Two changes, one surface. The bar offered two answers to one question —
«Arvamused» and «Arvamuste arhiiv» — and the second is now a tab inside the
first; and the compact field, which could only ever take somebody to a results
page, now offers the five matters it would have listed there.

What is asserted here is the boundary and the bound, in that order. The
suggestion endpoint is a second door onto the same search, and a second door is
where an authorization rule gets re-implemented slightly differently: so every
case `tests/test_search_authorization.py` makes about the results page is made
again here against the endpoint. If the two ever disagree, one of them is
wrong, and the wrong one is whichever leaks.

The bound is the other half. This endpoint answers a keystroke, so a query
count that grows with the corpus is not a slow page — it is a department typing
into a field and a database doing a hundred round trips a second. The cost is
measured against eight matching matters and against twenty-eight, and asserted
to be the same.
"""

from __future__ import annotations

import json

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from app.core.enums import Visibility
from app.legacy_import.opinion_access import may_read_archive
from app.matters.services import create_matter
from app.organisations.models import Organisation, OrganisationType
from app.search.indexing import rebuild_all
from app.search.models import INDEX_VERSION
from app.search.views import MIN_SUGGESTION_CHARACTERS, SUGGESTION_LIMIT
from tests import factories

pytestmark = pytest.mark.django_db

#: The word every matter below is titled with, so one query reaches all of them.
SUBJECT = "pakendiseaduse"

RESTRICTED_TITLE = f"Salajane {SUBJECT} konfidentsiaalne eelnõu"
OPEN_TITLE = f"Avalik {SUBJECT} muutmise eelnõu"


def suggest(client, query: str):
    """Ask the endpoint, and hand back the decoded payload."""
    response = client.get(reverse("search:suggestions"), {"q": query})
    assert response.status_code == 200, response.status_code
    assert response["Content-Type"].startswith("application/json")
    return json.loads(response.content)


def titles(payload) -> list[str]:
    return [result["title"] for result in payload["results"]]


@pytest.fixture
def world(db, specialist, other_specialist):
    """One restricted matter, one open one, and nothing else that matches."""
    ministry = Organisation.objects.create(
        name="Salaministeerium", organisation_type=OrganisationType.MINISTRY
    )
    restricted = create_matter(
        title=RESTRICTED_TITLE,
        owner=specialist,
        reference_year=2026,
        visibility=Visibility.RESTRICTED,
        addressee_organisation=ministry,
    )
    visible = create_matter(title=OPEN_TITLE, owner=other_specialist, reference_year=2026)
    rebuild_all()
    return {"restricted": restricted, "visible": visible, "ministry": ministry}


@pytest.fixture
def crowd(db, specialist):
    """Eight matters one query reaches, so the five-row bound is observable."""
    for index in range(8):
        create_matter(
            title=f"{OPEN_TITLE} number {index}",
            owner=specialist,
            reference_year=2026,
        )
    rebuild_all()


# -- the bound ---------------------------------------------------------------


def test_a_single_character_is_not_searched(world, client, specialist) -> None:
    """Below the threshold nothing is asked, and the answer says so.

    Not "no results": one character matches most of the corpus and ranks it by
    nothing anybody typed, so the endpoint declines rather than answering
    badly.
    """
    client.force_login(specialist)
    assert MIN_SUGGESTION_CHARACTERS == 2
    payload = suggest(client, "p")
    assert payload["results"] == []
    assert payload["has_more"] is False
    assert payload["all_url"] == ""


def test_whitespace_does_not_reach_the_threshold(world, client, specialist) -> None:
    """`" p "` is one character with decoration, not two."""
    client.force_login(specialist)
    assert suggest(client, "  p  ")["results"] == []


def test_two_characters_are_searched(world, client, specialist) -> None:
    client.force_login(specialist)
    payload = suggest(client, SUBJECT[:2])
    assert payload["query"] == SUBJECT[:2]


def test_never_more_than_five_results(crowd, client, specialist) -> None:
    client.force_login(specialist)
    assert SUGGESTION_LIMIT == 5
    assert len(suggest(client, SUBJECT)["results"]) == 5


def test_a_full_dropdown_offers_the_way_to_the_rest(crowd, client, specialist) -> None:
    client.force_login(specialist)
    payload = suggest(client, SUBJECT)
    assert payload["has_more"] is True
    assert payload["all_url"] == f"{reverse('search:search')}?q={SUBJECT}"


def test_a_short_result_set_does_not_claim_there_is_more(world, client, reader) -> None:
    """One visible matter, nothing else in the corpus that matches."""
    client.force_login(reader)
    payload = suggest(client, SUBJECT)
    assert titles(payload) == [OPEN_TITLE]
    assert payload["has_more"] is False


def test_a_matching_entry_the_dropdown_cannot_show_counts_as_more(
    world, client, reader, other_specialist
) -> None:
    """The full page is wider than this list, and says so honestly.

    The dropdown offers matters. An entry that matches the same query is a
    result this reader would find on the results page and not here, so the way
    to that page is offered — the one case where "more" is not simply "a sixth
    matter".

    Read as the READER, for whom the restricted matter does not exist at all:
    that keeps the visible corpus at one matter, so the flag can only have come
    from the entry.
    """
    factories.EntryFactory(
        matter=world["visible"],
        author=other_specialist,
        body=f"<p>Märkus {SUBJECT} kohta.</p>",
    )
    rebuild_all()
    client.force_login(reader)
    payload = suggest(client, SUBJECT)
    assert titles(payload) == [OPEN_TITLE]
    assert payload["has_more"] is True


# -- where a suggestion goes -------------------------------------------------


def test_a_suggestion_points_at_its_own_matter(world, client, other_specialist) -> None:
    client.force_login(other_specialist)
    payload = suggest(client, SUBJECT)
    expected = reverse("matters:matter_detail", kwargs={"pk": world["visible"].pk})
    assert payload["results"][0]["url"] == expected


def test_the_secondary_line_is_facts_the_matter_already_carries(
    world, client, other_specialist
) -> None:
    """Owner, addressee and stage — and no invented filler."""
    stage = factories.StageFactory(label_et="Kooskõlastusring")
    matter = world["visible"]
    matter.stage = stage
    matter.addressee_organisation = world["ministry"]
    matter.save(update_fields=["stage", "addressee_organisation"])
    rebuild_all()

    client.force_login(other_specialist)
    context = suggest(client, SUBJECT)["results"][0]["context"]
    assert context == f"{other_specialist.display_name} · Salaministeerium · Kooskõlastusring"


def test_a_matter_with_nothing_to_say_says_nothing(client, specialist) -> None:
    """An archive row has no owner, no addressee and no stage.

    The row is a title and an empty second line rather than a row of
    separators, because " · · " is the dropdown inventing punctuation to look
    complete.
    """
    factories.ArchiveMatterFactory(title=f"Arhiivikirje {SUBJECT} kohta")
    rebuild_all()
    client.force_login(specialist)
    assert suggest(client, SUBJECT)["results"][0]["context"] == ""


def test_the_identifier_stays_out_of_the_dropdown(world, client, other_specialist) -> None:
    """A matter is named by its title here as it is everywhere else.

    `2026_184` was taken off every surface deliberately; a new one is not the
    place to bring it back (docs/adr/0036).
    """
    client.force_login(other_specialist)
    body = json.dumps(suggest(client, SUBJECT))
    assert world["visible"].display_reference not in body


# -- the boundary ------------------------------------------------------------


def test_the_owner_of_a_restricted_matter_finds_it(world, client, specialist) -> None:
    client.force_login(specialist)
    assert RESTRICTED_TITLE in titles(suggest(client, SUBJECT))


def test_a_department_specialist_finds_a_restricted_matter(world, client, other_specialist) -> None:
    """The current product rule: a lawyer reads the department's work.

    Asserted here rather than assumed, because this endpoint is the newest
    place it could be got wrong (docs/adr/0042).
    """
    client.force_login(other_specialist)
    assert RESTRICTED_TITLE in titles(suggest(client, SUBJECT))


def test_a_department_head_finds_a_restricted_matter(world, client, department_head) -> None:
    client.force_login(department_head)
    assert RESTRICTED_TITLE in titles(suggest(client, SUBJECT))


def test_a_reader_does_not(world, client, reader) -> None:
    """A colleague outside the legal business scope sees the open matter only.

    And the count beside it agrees: `has_more` must not go true because of
    something they may not have, or the flag becomes the disclosure the title
    was not.
    """
    client.force_login(reader)
    payload = suggest(client, SUBJECT)
    assert titles(payload) == [OPEN_TITLE]
    assert payload["has_more"] is False
    assert "Salaministeerium" not in json.dumps(payload)


def test_a_technical_administrator_gains_nothing_here(world, client, administrator) -> None:
    """Technical administration is not business access (specification 5.2).

    The archive is the one thing an ADMINISTRATOR may read that a lawyer may
    not, and it is deliberately not what this endpoint answers with.
    """
    client.force_login(administrator)
    assert titles(suggest(client, SUBJECT)) == [OPEN_TITLE]


def test_a_superuser_alone_gains_nothing_here(world, client, superuser) -> None:
    client.force_login(superuser)
    assert RESTRICTED_TITLE not in titles(suggest(client, SUBJECT))


def test_an_anonymous_caller_is_sent_to_sign_in(world, client) -> None:
    """Exactly what the results page does, so the two cannot disagree.

    In shared-gate mode this is also the session that has passed the door and
    chosen no persona: `login_required`, not `gate_required`, so the dropdown
    can never answer a question the full search would refuse.
    """
    response = client.get(reverse("search:suggestions"), {"q": SUBJECT})
    assert response.status_code == 302
    assert "sisselogimine" in response["Location"] or "konto" in response["Location"]


def test_a_restricted_matters_reference_does_not_resolve_for_a_reader(
    world, client, reader
) -> None:
    """No navigation shortcut either. A hit would confirm the file exists."""
    client.force_login(reader)
    assert suggest(client, world["restricted"].display_reference)["results"] == []


def test_restricting_a_matter_takes_effect_without_reindexing(world, client, reader) -> None:
    """Visibility is read from the live row, never from the projection.

    The projection is not touched between the two calls: a matter restricted a
    second ago must be gone from the next keystroke's suggestions, without
    waiting for `rebuild_search_index` (docs/adr/0005, 0013).
    """
    from app.matters.services import set_matter_visibility

    client.force_login(reader)
    assert titles(suggest(client, SUBJECT)) == [OPEN_TITLE]

    set_matter_visibility(matter=world["visible"], visibility=Visibility.RESTRICTED)
    assert suggest(client, SUBJECT)["results"] == []


# -- the same search, not a second one ---------------------------------------


def test_the_dropdown_agrees_with_the_results_page_order(crowd, client, specialist) -> None:
    """The five rows are the first five of the page, in the page's order.

    Not "roughly the same results". The endpoint calls the same ranked
    queryset, so anything else here would mean a second ranking had been
    written (app/search/services.py).
    """
    client.force_login(specialist)
    dropdown = titles(suggest(client, SUBJECT))

    page = client.get(reverse("search:search"), {"q": SUBJECT})
    from app.search.models import SearchSourceKind

    on_page = [
        row["result"].matter.title
        for row in page.context["rows"]
        if row["result"].source_kind == SearchSourceKind.MATTER
    ]
    assert dropdown == on_page[:SUGGESTION_LIMIT]


def test_the_index_contract_is_untouched() -> None:
    """This endpoint reads the projection; it does not change what is in it.

    A changed `INDEX_VERSION` would mean every deployment needs a rebuild
    before search works again — an expensive consequence for a dropdown, and
    the reason it is asserted rather than assumed (docs/adr/0038).
    """
    assert INDEX_VERSION == "AUTH003.1"


def test_a_refused_query_produces_no_results_rather_than_an_error(
    world, client, specialist
) -> None:
    """The service refuses an over-long query; the endpoint must not 500."""
    client.force_login(specialist)
    payload = suggest(client, "a" * 501)
    assert payload["results"] == []
    assert payload["query"] == ""


# -- the cost ----------------------------------------------------------------


def _statements_for(client, query: str) -> list[str]:
    with CaptureQueriesContext(connection) as captured:
        client.get(reverse("search:suggestions"), {"q": query})
    return [entry["sql"] for entry in captured]


def test_the_cost_does_not_grow_with_the_corpus(crowd, client, specialist) -> None:
    """Eight matching matters or twenty-eight — the same statements.

    The shape, not a magic number. A dropdown that costs a query per row is a
    per-keystroke N+1 against a corpus-scale register, and every fact each row
    prints — owner, addressee, stage — is a join that would have produced one.
    Both measurements below fill the dropdown, so they run the same code path
    and any difference between them is growth.
    """
    client.force_login(specialist)
    # Warmed first: session, user and any per-process caching should not be
    # part of the comparison.
    client.get(reverse("search:suggestions"), {"q": SUBJECT})
    small = _statements_for(client, SUBJECT)

    for index in range(20):
        create_matter(title=f"{OPEN_TITLE} lisa {index}", owner=specialist, reference_year=2026)
    rebuild_all()
    large = _statements_for(client, SUBJECT)

    assert len(large) == len(small), f"{len(small)} statements → {len(large)}"


def test_the_endpoint_reads_the_search_index_at_most_twice(world, client, reader) -> None:
    """A ceiling, so "did not grow" cannot be satisfied by being uniformly bad.

    Two at most, and only in the case that needs both: the ranked five, and the
    bounded `EXISTS` that decides whether the full page holds anything this
    list does not.
    """
    client.force_login(reader)
    client.get(reverse("search:suggestions"), {"q": SUBJECT})
    statements = _statements_for(client, SUBJECT)
    index_reads = [sql for sql in statements if "search_searchdocument" in sql]
    assert len(index_reads) <= 2, index_reads
    assert len(statements) <= 8, statements


def test_a_query_below_the_threshold_touches_the_search_index_not_at_all(
    world, client, specialist
) -> None:
    client.force_login(specialist)
    client.get(reverse("search:suggestions"), {"q": SUBJECT})
    assert not [sql for sql in _statements_for(client, "p") if "search_searchdocument" in sql]


# -- the fallback ------------------------------------------------------------


def _search_form(response) -> str:
    body = response.content.decode()
    start = body.index('<form class="searchfield"')
    return body[start : body.index("</form>", start)]


def test_the_field_is_still_a_plain_get_form(client, specialist) -> None:
    """With no JavaScript at all, typing and pressing Enter reaches the page.

    The suggestions are bound onto this form by `static/js/app.js`; they are
    not what makes it work. A change that made the endpoint the only path to
    search would pass every test above and leave the application unusable in a
    browser with scripting off (master specification 17.7).
    """
    client.force_login(specialist)
    form = _search_form(client.get(reverse("matters:overview")))
    assert 'method="get"' in form
    assert f'action="{reverse("search:search")}"' in form
    assert 'name="q"' in form
    assert 'type="submit"' in form


def test_the_static_markup_does_not_claim_a_listbox_it_cannot_open(client, specialist) -> None:
    """The combobox roles are added by the script, not written into the page.

    `role="combobox" aria-expanded="false"` served to a browser that will never
    run the script describes behaviour the page does not have, and a screen
    reader would announce a collapsed listbox that can never open.
    """
    client.force_login(specialist)
    form = _search_form(client.get(reverse("matters:overview")))
    assert 'role="combobox"' not in form
    assert "aria-expanded" not in form
    # The container and the endpoint are there for the script to find.
    assert 'id="global-search-results"' in form
    assert f'data-live-search="{reverse("search:suggestions")}"' in form


# -- the navigation ----------------------------------------------------------


def navigation_of(response) -> str:
    """The main navigation's markup, and nothing else on the page."""
    body = response.content.decode()
    start = body.index('<nav class="topnav"')
    return body[start : body.index("</nav>", start)]


def test_the_bar_offers_arvamused(client, specialist) -> None:
    client.force_login(specialist)
    navigation = navigation_of(client.get(reverse("matters:overview")))
    assert ">Arvamused<" in navigation
    assert reverse("submissions:sent") in navigation


def test_the_bar_no_longer_offers_a_separate_archive(client, administrator) -> None:
    """Removed for the reader who could see it, not hidden from the rest.

    An ADMINISTRATOR is the persona `may_read_archive` admits, so this is the
    session that used to get the second item. If the link is gone for them it
    is gone.
    """
    assert may_read_archive(administrator)
    client.force_login(administrator)
    navigation = navigation_of(client.get(reverse("matters:overview")))
    assert "Arvamuste arhiiv" not in navigation
    assert reverse("legacy_import:opinion_archive_browse") not in navigation


def test_the_workspace_still_has_both_tabs(client, administrator) -> None:
    """Saadetud and Arhiiv, which is where the archive now lives."""
    client.force_login(administrator)
    body = client.get(reverse("submissions:sent")).content.decode()
    assert ">Saadetud" in body
    assert ">Arhiiv" in body
    assert reverse("submissions:archive") in body


def test_the_arhiiv_tab_reaches_the_archive(client, administrator) -> None:
    client.force_login(administrator)
    response = client.get(reverse("submissions:archive"))
    assert response.status_code == 200
    assert response.context["active_tab"] == "arhiiv"


def test_the_administrative_archive_route_still_works(client, administrator) -> None:
    """The direct URL is unchanged; only the link to it left the bar."""
    client.force_login(administrator)
    response = client.get(reverse("legacy_import:opinion_archive_browse"))
    assert response.status_code == 200
    assert "Arvamuste arhiiv" in response.content.decode()


def test_the_archive_still_refuses_a_reader_who_may_not_open_it(client, specialist) -> None:
    """Nothing about access moved with the link."""
    assert not may_read_archive(specialist)
    client.force_login(specialist)
    assert client.get(reverse("legacy_import:opinion_archive_browse")).status_code == 403
    assert client.get(reverse("submissions:archive")).status_code == 403
