"""The register's `?q=` searches once per request (QRY-06).

`matching_matter_ids` used to be composed into the register as a subquery, so
the search — a scan of the whole projection — ran inside every statement that
read the register: the rows, the count beside the box, and on an empty page
`_matches_elsewhere` a third time. The register now reads the matched ids once
and narrows by them. Same population and the same authorization: the search is
scoped to the reader, and so is the register it narrows.
"""

from __future__ import annotations

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from app.core.enums import Visibility
from app.matters.services import close_matter
from app.workflow.enums import Disposition
from tests import factories
from tests.test_register_search import indexed, titles_on, total_of

pytestmark = pytest.mark.django_db


def _search_statements(captured) -> list[str]:
    return [q["sql"] for q in captured if '"search_searchdocument"' in q["sql"]]


def _get(client, **params):
    with CaptureQueriesContext(connection) as captured:
        response = client.get(reverse("matters:matter_list"), params)
    return response, _search_statements(captured)


def test_a_page_with_matches_searches_once(signed_in, specialist):
    for title in ("Pakendiseaduse muudatus", "Pakendiaruande kord", "Maksukorralduse seadus"):
        indexed(factories.MatterFactory(owner=specialist, title=title))

    response, searches = _get(signed_in, q="pakend", olek="koik")

    assert sorted(titles_on(response)) == ["Pakendiaruande kord", "Pakendiseaduse muudatus"]
    assert total_of(response) == 2
    assert len(searches) == 1, searches


def test_an_empty_page_asks_elsewhere_without_searching_again(signed_in, specialist):
    """`_matches_elsewhere` reads the same ids: a closed match, an open-only view."""
    closed = factories.MatterFactory(owner=specialist, title="Pakendiseaduse muudatus")
    close_matter(matter=closed, disposition=Disposition.COMPLETED, actor=specialist)
    indexed(closed)

    response, searches = _get(signed_in, q="pakend", olek="avatud")

    assert titles_on(response) == []
    assert len(searches) == 1, searches


def test_a_reader_still_reaches_only_what_they_may_see(client, reader, specialist, administrator):
    """The ids come from the reader's own search, so what they may not see stays out.

    Since docs/adr/0181 the READER may see the restricted match too; an outsider
    (ADMINISTRATOR without a grant) reaches neither."""
    indexed(factories.MatterFactory(owner=specialist, title="Pakendiseaduse muudatus"))
    indexed(
        factories.MatterFactory(
            owner=specialist, title="Pakendi salajane töö", visibility=Visibility.RESTRICTED
        )
    )
    client.force_login(reader)

    response, searches = _get(client, q="pakend", olek="koik")

    assert sorted(titles_on(response)) == ["Pakendi salajane töö", "Pakendiseaduse muudatus"]
    assert total_of(response) == 2
    assert len(searches) == 1

    client.force_login(administrator)
    response, searches = _get(client, q="pakend", olek="koik")

    assert titles_on(response) == []
    assert total_of(response) == 0
    assert len(searches) <= 1
