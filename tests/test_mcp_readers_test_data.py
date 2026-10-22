"""A TEST Matter says so wherever its title is printed (MCPSEC-16).

The production register holds TEST Matters made while developing the system
(`MatterDataClass.TEST`). `search` returns only ``id``, ``title`` and ``url``,
so the title is the one place a client can learn that a hit is not real work:
it, and every title built from the Matter's heading, starts with «[TEST] ».
"""

from __future__ import annotations

import pytest

from app.matters.enums import MatterDataClass
from app.mcp_server import readers
from app.search.indexing import rebuild_all
from tests import factories

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def public_origin(settings):
    settings.JURISTID_PUBLIC_ORIGIN = "https://juristid.example.test"


@pytest.fixture
def matters(specialist):
    real = factories.MatterFactory(owner=specialist, title="Jäätmeseaduse päris muutmine")
    test = factories.MatterFactory(
        owner=specialist, title="Jäätmeseaduse proovimuutmine", data_class=MatterDataClass.TEST
    )
    factories.EntryFactory(
        matter=test, author=specialist, body="<p>Jäätmeseaduse proovisissekanne.</p>"
    )
    rebuild_all()
    return real, test


def test_search_titles_mark_test_matters_and_their_records(matters, specialist):
    real, test = matters
    results = readers.search_results(specialist, "jäätmeseaduse")["results"]
    titles = {row["id"]: row["title"] for row in results}
    assert titles[f"matter:{test.pk}"].startswith("[TEST] ")
    assert not titles[f"matter:{real.pk}"].startswith("[TEST]")
    test_children = [title for key, title in titles.items() if key.startswith("entry:")]
    assert test_children and all(title.startswith("[TEST] ") for title in test_children)


def test_records_and_fetch_titles_mark_test_matters(matters, specialist):
    _real, test = matters
    rows = readers.search_records(specialist, "jäätmeseaduse")["results"]
    test_rows = [row for row in rows if row["matter"]["is_test_data"]]
    assert test_rows and all(row["title"].startswith("[TEST] ") for row in test_rows)
    assert readers.fetch_source(specialist, f"matter:{test.pk}")["title"].startswith("[TEST] ")
