"""The MCP readers: who may call them, `search`, `search_records` and source ids (docs/adr/0185).

Every reader answers an authorized internal user (docs/adr/0181) — a
SPECIALIST, a DEPARTMENT_HEAD and a READER alike — and answers everybody else
exactly as it answers an id that names nothing: `NotFound`, before anything is
read. These tests hold that for every reader, then the two search tools'
contracts: ChatGPT's `{"results":[{"id","title","url"}]}`, the full rows of
`search_records` with filtering done before pagination, and ids that `fetch`
accepts back.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from app.core.authorization import DEPARTMENT_VIEWER
from app.mcp_server import ids, readers
from app.mcp_server.readers import InvalidArgument, NotFound
from app.search.indexing import rebuild_all
from tests import factories

pytestmark = pytest.mark.django_db

ORIGIN = "https://juristid.example.test"


@pytest.fixture(autouse=True)
def public_origin(settings):
    settings.JURISTID_PUBLIC_ORIGIN = ORIGIN


@pytest.fixture
def world(specialist):
    packaging = factories.MatterFactory(
        owner=specialist,
        title="Pakendiseaduse muutmise eelnõu",
        received_date=timezone.localdate(),
    )
    regulation = factories.MatterFactory(
        owner=specialist, title="Pakendiseaduse rakendusmääruse muutmine"
    )
    unrelated = factories.MatterFactory(owner=specialist, title="Tulumaksuseaduse muutmine")
    entry = factories.EntryFactory(
        matter=packaging,
        author=specialist,
        body="<p>Kohtusime ministeeriumiga pakendiaruandluse teemal.</p>",
    )
    rebuild_all()
    return {
        "packaging": packaging,
        "regulation": regulation,
        "unrelated": unrelated,
        "entry": entry,
    }


# ---------------------------------------------------------------------------
# Who may call a reader at all
# ---------------------------------------------------------------------------


def _every_reader(world: dict[str, Any]) -> list[tuple[str, Any]]:
    matter = str(world["packaging"].pk)
    return [
        ("search", lambda user: readers.search_results(user, "pakendiseadus")),
        ("search_records", lambda user: readers.search_records(user, "pakendiseadus")),
        (
            "fetch",
            lambda user: readers.fetch_source(user, f"matter:{world['packaging'].pk}"),
        ),
        ("get_matter", lambda user: readers.matter_snapshot(user, matter)),
        ("get_matter_activity", lambda user: readers.matter_activity(user, matter)),
        ("get_matter_documents", lambda user: readers.matter_documents(user, matter)),
        ("get_upcoming_commencements", lambda user: readers.upcoming_commencements(user)),
    ]


@pytest.mark.parametrize("role", ["specialist", "department_head", "reader"])
def test_every_business_role_reads_every_reader(world, role, request) -> None:
    user = request.getfixturevalue(role)
    for name, call in _every_reader(world):
        answer = call(user)
        assert isinstance(answer, dict), name
        # JSON-ready as it stands: the server sends it without a custom encoder.
        assert json.loads(json.dumps(answer)) == answer, name


@pytest.mark.parametrize("who", ["administrator", "superuser", "inactive", "gate", "anonymous"])
def test_nobody_else_reads_anything(world, who, request) -> None:
    from django.contrib.auth.models import AnonymousUser

    users = {
        "administrator": lambda: request.getfixturevalue("administrator"),
        "superuser": lambda: request.getfixturevalue("superuser"),
        "inactive": lambda: factories.UserFactory(is_active=False),
        "gate": lambda: DEPARTMENT_VIEWER,
        "anonymous": AnonymousUser,
    }
    user = users[who]()
    for name, call in _every_reader(world):
        with pytest.raises(NotFound, match="No such record"):
            call(user)
        assert name


def test_a_refusal_carries_no_business_text(world, administrator) -> None:
    with pytest.raises(NotFound) as refusal:
        readers.matter_snapshot(administrator, world["packaging"].display_reference)
    assert world["packaging"].title not in str(refusal.value)
    assert world["packaging"].display_reference not in str(refusal.value)


def test_no_reader_writes_anything(world, specialist) -> None:
    with CaptureQueriesContext(connection) as captured:
        for _, call in _every_reader(world):
            call(specialist)
        readers.fetch_source(specialist, f"entry:{world['entry'].pk}")
    writes = [
        query["sql"]
        for query in captured.captured_queries
        if query["sql"].lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE"))
    ]
    assert writes == []


# ---------------------------------------------------------------------------
# Source ids
# ---------------------------------------------------------------------------


def test_an_id_round_trips() -> None:
    pk = uuid.uuid4()
    assert ids.parse_source_id(f"doc:{pk}@7") == ids.SourceId("doc", pk, 7)
    assert str(ids.SourceId("doc", pk, 7)) == f"doc:{pk}@7"
    assert ids.parse_source_id(f"hist:{pk}").ordinal is None


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "nonsense",
        "user:0192c0de-0000-7000-8000-000000000000",
        "doc:not-a-uuid",
        "doc:0192C0DE-0000-7000-8000-000000000000",
        "doc:{0192c0de-0000-7000-8000-000000000000}",
        "doc:0192c0de-0000-7000-8000-000000000000@-1",
        "doc:0192c0de-0000-7000-8000-000000000000@x",
        "doc:0192c0de-0000-7000-8000-000000000000@99999999999",
        "doc:0192c0de-0000-7000-8000-000000000000" + "@1" * 30,
        12,
        None,
    ],
)
def test_a_crafted_id_is_an_invalid_argument(world, specialist, raw) -> None:
    with pytest.raises(InvalidArgument):
        readers.fetch_source(specialist, raw)


def test_an_unknown_uuid_and_another_kinds_uuid_are_not_found(world, specialist) -> None:
    entry = world["entry"]
    with pytest.raises(NotFound, match="No such record"):
        readers.fetch_source(specialist, f"entry:{uuid.uuid4()}")
    # A Matter's uuid under the entry prefix, and the entry's under the document one.
    with pytest.raises(NotFound, match="No such record"):
        readers.fetch_source(specialist, f"entry:{world['packaging'].pk}")
    with pytest.raises(NotFound, match="No such record"):
        readers.fetch_source(specialist, f"doc:{entry.pk}")


def test_a_deleted_matter_and_its_records_are_not_found(world, specialist) -> None:
    from app.matters.models import Matter

    matter = world["packaging"]
    entry = world["entry"]
    Matter.all_objects.filter(pk=matter.pk).update(deleted_at=timezone.now(), deleted_by=specialist)
    for call in (
        lambda: readers.fetch_source(specialist, f"matter:{matter.pk}"),
        lambda: readers.fetch_source(specialist, f"entry:{entry.pk}"),
        lambda: readers.matter_snapshot(specialist, str(matter.pk)),
        lambda: readers.matter_snapshot(specialist, matter.display_reference),
    ):
        with pytest.raises(NotFound, match="No such record"):
            call()


@pytest.mark.parametrize("value", ["", "   ", "2026", "teema 225", "x" * 200, 7])
def test_a_matter_argument_that_names_nothing_is_invalid(world, specialist, value) -> None:
    with pytest.raises(InvalidArgument, match="matter must be"):
        readers.matter_snapshot(specialist, value)


def test_a_matter_is_found_by_uuid_id_and_any_reference_spelling(world, specialist) -> None:
    matter = world["packaging"]
    year, number = matter.reference_year, matter.reference_number
    for spelling in (
        str(matter.pk),
        f"matter:{matter.pk}",
        f"{year}_{number}",
        f"{year}-{number}",
        f"{year} {number}",
    ):
        assert readers.matter_snapshot(specialist, spelling)["title"] == matter.title
    with pytest.raises(NotFound, match="No such record"):
        readers.matter_snapshot(specialist, f"{year}_{number + 999}")


# ---------------------------------------------------------------------------
# search
# ---------------------------------------------------------------------------


def test_search_answers_the_company_knowledge_shape(world, specialist) -> None:
    answer = readers.search_results(specialist, "pakendiseadus")
    assert set(answer) == {"results"}
    assert answer["results"]
    for row in answer["results"]:
        assert set(row) == {"id", "title", "url"}
        assert row["url"].startswith(ORIGIN + "/")
        ids.parse_source_id(row["id"])
    titles = [row["title"] for row in answer["results"]]
    packaging = world["packaging"]
    assert f"{packaging.display_reference} · {packaging.title}" in titles


def test_an_inflected_query_finds_the_base_form(world, specialist) -> None:
    """«eelnõule» (allative) finds «eelnõu» — the Estonian stemming the page has."""
    answer = readers.search_results(specialist, "eelnõule")
    assert f"matter:{world['packaging'].pk}" in [row["id"] for row in answer["results"]]


def test_similar_titles_stay_distinct_results(world, specialist) -> None:
    answer = readers.search_results(specialist, "pakendiseadus")
    found = {row["id"] for row in answer["results"]}
    assert f"matter:{world['packaging'].pk}" in found
    assert f"matter:{world['regulation'].pk}" in found
    assert f"matter:{world['unrelated'].pk}" not in found
    assert len(found) == len(answer["results"])


def test_a_child_result_names_its_matter_and_its_source(world, specialist) -> None:
    answer = readers.search_results(specialist, "pakendiaruandluse")
    entry = world["entry"]
    row = next(row for row in answer["results"] if row["id"] == f"entry:{entry.pk}")
    assert row["title"].startswith(f"{world['packaging'].display_reference} · ")
    assert "Sissekanne" in row["title"]
    assert row["url"].endswith(f"#sissekanne-{entry.pk}")


def test_search_is_capped_at_twenty(specialist) -> None:
    matter = factories.MatterFactory(owner=specialist, title="Kvoodid")
    for index in range(25):
        factories.EntryFactory(matter=matter, body=f"<p>Kalakvoodi arutelu {index}</p>")
    rebuild_all()
    answer = readers.search_results(specialist, "kalakvoodi", limit=50)
    assert len(answer["results"]) == 20


@pytest.mark.parametrize("query", ["", "   ", "\x00\x00", "x" * 501, None, ["a"]])
def test_an_unusable_query_is_refused_rather_than_answered_empty(world, specialist, query) -> None:
    with pytest.raises(InvalidArgument, match="query must"):
        readers.search_results(specialist, query)


def test_search_stays_within_its_query_budget(world, specialist, django_assert_max_num_queries):
    with django_assert_max_num_queries(40):
        readers.search_results(specialist, "pakendiseadus")


# ---------------------------------------------------------------------------
# search_records
# ---------------------------------------------------------------------------


def test_a_record_row_carries_the_whole_result(world, specialist) -> None:
    answer = readers.search_records(specialist, "pakendiaruandluse")
    assert answer["total"] >= 1
    entry = world["entry"]
    row = next(row for row in answer["results"] if row["id"] == f"entry:{entry.pk}")
    assert row["source_kind"] == "ENTRY"
    assert row["source_label"] == "Sissekanne"
    assert row["matter"]["id"] == f"matter:{world['packaging'].pk}"
    assert row["matter"]["reference"] == world["packaging"].display_reference
    assert row["matter"]["url"].startswith(ORIGIN)
    assert "«" in row["snippet"] and "⦑" not in row["snippet"]
    assert row["source_date"]["date"] == timezone.localdate(entry.occurred_at).isoformat()
    assert row["source_date"]["certainty"] == "confirmed"
    assert row["match_kind"]
    assert isinstance(row["rank"], float)


def test_records_page_through_every_result_once(specialist) -> None:
    matter = factories.MatterFactory(owner=specialist, title="Kvoodid")
    for index in range(25):
        factories.EntryFactory(matter=matter, body=f"<p>Kalakvoodi arutelu {index}</p>")
    rebuild_all()
    first = readers.search_records(specialist, "kalakvoodi", source_kinds=["ENTRY"])
    second = readers.search_records(specialist, "kalakvoodi", source_kinds=["ENTRY"], page=2)
    third = readers.search_records(specialist, "kalakvoodi", source_kinds=["ENTRY"], page=3)
    assert first["total"] == second["total"] == 25
    assert (len(first["results"]), len(second["results"]), len(third["results"])) == (20, 5, 0)
    assert first["has_more"] and not second["has_more"]
    seen = [row["id"] for row in first["results"] + second["results"]]
    assert len(set(seen)) == 25


def test_kind_and_matter_filters_apply_before_counting(world, specialist) -> None:
    everything = readers.search_records(specialist, "pakendiseaduse")
    matters_only = readers.search_records(specialist, "pakendiseaduse", source_kinds=["matter"])
    assert {row["source_kind"] for row in matters_only["results"]} == {"MATTER"}
    assert matters_only["total"] == len(matters_only["results"])
    assert matters_only["total"] <= everything["total"]

    regulation = world["regulation"]
    scoped = readers.search_records(
        specialist, "pakendiseaduse", matter=regulation.display_reference
    )
    assert scoped["total"] >= 1
    assert {row["matter"]["id"] for row in scoped["results"]} == {f"matter:{regulation.pk}"}


@pytest.mark.parametrize(
    ("arguments", "message"),
    [
        ({"page": 0}, "page must be between"),
        ({"page": 11}, "page must be between"),
        ({"page": True}, "page must be a whole number"),
        ({"page": "2"}, "page must be a whole number"),
        ({"source_kinds": ["NOPE"]}, "Unknown source kind"),
        ({"source_kinds": "MATTER"}, "source_kinds must be a list"),
        ({"source_kinds": []}, "at least one kind"),
        ({"matter": "nonsense"}, "matter must be"),
    ],
)
def test_bad_record_arguments_are_refused(world, specialist, arguments, message) -> None:
    with pytest.raises(InvalidArgument, match=message):
        readers.search_records(specialist, "pakendiseaduse", **arguments)


def test_a_matter_filter_naming_an_invisible_matter_is_not_found(world, specialist) -> None:
    with pytest.raises(NotFound, match="No such record"):
        readers.search_records(specialist, "pakendiseaduse", matter=str(uuid.uuid4()))


def test_every_record_id_is_fetchable(world, specialist) -> None:
    answer = readers.search_records(specialist, "pakendiseaduse")
    for row in answer["results"]:
        fetched = readers.fetch_source(specialist, row["id"])
        assert fetched["id"] == row["id"]


def test_records_stay_within_their_query_budget(world, specialist, django_assert_max_num_queries):
    with django_assert_max_num_queries(40):
        readers.search_records(specialist, "pakendiseaduse")
