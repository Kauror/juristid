"""The owner's questions, asked the way a competent agent would ask them (docs/adr/0185 §2).

Each test is one question from the brief. It makes the tool calls a careful
ChatGPT would make — through the real MCP application, as a colleague's real
token, over the synthetic department in `tests/mcp_eval_world.py` — and checks
that the answer holds every fact a grounded reply needs **and labels it
correctly**: the right record, its source and link, its chronology, what kind of
date it is, and what is missing said as missing rather than invented.

What a model then writes is not under test; what it is given is. A question
the tools cannot answer fails here, not in a colleague's conversation.

Read-only throughout, so one world serves the module (`committed_world`); the
last test runs the whole tool set again over connections opened read-only, as
the `mcp` process opens them in production.
"""

from __future__ import annotations

import json
import re
from datetime import timedelta
from typing import Any

import pytest
from django.core.management import call_command
from django.db import connections
from django.test import override_settings

from app.mcp_server.runtime import ReaderRuntime
from tests import mcp_eval_world as w

pytestmark = pytest.mark.django_db

DATABASE_CACHE = {
    "default": {
        "BACKEND": "django.core.cache.backends.db.DatabaseCache",
        "LOCATION": "core_cache",
    }
}


@pytest.fixture(scope="module")
def world(django_db_setup, django_db_blocker, tmp_path_factory):
    with (
        pytest.MonkeyPatch.context() as patch,
        w.committed_world(django_db_blocker, tmp_path_factory.mktemp("mcp-eval"), patch) as built,
    ):
        # The database cache production uses, created outside any test's
        # transaction so the worker threads' connections can see it (DDL that
        # outlives the flush, as `tests/test_authentication_attempts.py`'s does).
        with django_db_blocker.unblock(), override_settings(CACHES=DATABASE_CACHE):
            call_command("createcachetable", verbosity=0)
        yield built


@pytest.fixture
def mcp(world) -> w.Mcp:
    return world.mcp("marko")


def _ids(rows: list[dict[str, Any]]) -> list[str]:
    return [row["id"] for row in rows]


def _days(world: w.EvalWorld, offset: int) -> str:
    return (world.today + timedelta(days=offset)).isoformat()


# -- 1. «Where are we with topic X?» -----------------------------------------------------


def test_q1_where_are_we_with_a_matter(world, mcp):
    from app.workflow.models import StageVocabulary

    matter = world.matters["wait"]
    # The colleague types the reference with a hyphen; the register writes it with «_».
    snapshot = mcp.ok("get_matter", {"matter": world.reference("wait").replace("_", "-")})

    assert snapshot["id"] == world.matter_id("wait")
    assert snapshot["reference"] == world.reference("wait")
    assert snapshot["title"] == matter.title
    assert snapshot["is_open"] is True
    assert snapshot["stage"] == {
        "key": "consultation",
        "label": StageVocabulary.objects.get(key="consultation").label_et,
    }
    assert snapshot["owner"] == "Marko Mets"
    assert snapshot["source_organisations"] == ["Kliimaministeerium"]

    action = snapshot["current_next_action"]
    assert action["kind"] == "WAIT"
    assert action["text"] == w.WAITING_FOR
    assert snapshot["waiting_for"] == w.WAITING_FOR
    assert action["date"]["semantics"] == "REVIEW_ON"
    assert action["date"]["date"] == _days(world, 7)
    # A review date is somebody's plan, and a WAIT cannot be «late».
    assert action["date"]["certainty"] == "planned"
    assert action["is_overdue"] is False

    deadline = snapshot["opinion_deadline"]
    assert deadline["date"] == _days(world, 14)
    assert deadline["certainty"] == "confirmed"
    assert deadline["still_owed"] is True
    assert deadline["days_remaining"] == 14
    # The deadline and the WAIT are two facts with two dates, never merged.
    assert deadline["date"] != action["date"]["date"]
    assert snapshot["missing"] == []
    assert snapshot["url"] == f"{w.ORIGIN}/teemad/{matter.pk}/"

    # The same snapshot read through `fetch`, as deep research would, says the same.
    text = mcp.ok("fetch", {"id": world.matter_id("wait")})["text"]
    assert f"Current next action: WAIT (Ootan) — {w.WAITING_FOR}" in text
    assert f"Opinion deadline: {deadline['display']} (confirmed)" in text
    assert "Vastutaja (owner): Marko Mets" in text


# -- 2. «What happened most recently?» ---------------------------------------------------


def test_q2_what_happened_most_recently(world, mcp):
    activity = mcp.ok("get_matter_activity", {"matter": world.reference("wait")})
    items = activity["items"]
    dates = [item["date"]["date"] for item in items]

    assert dates == sorted(dates, reverse=True), "newest first, by the date things happened"
    assert all(item["date"]["date"] and item["date"]["precision"] for item in items)
    latest = items[0]
    assert latest["id"] == f"entry:{world.records['wait_recent_entry'].pk}"
    assert latest["date"]["date"] == _days(world, -2)
    assert "uus versioon tuleb järgmisel kuul" in latest["text"]
    assert latest["url"].endswith(f"#sissekanne-{world.records['wait_recent_entry'].pk}")

    kinds = [item["kind"] for item in items]
    assert kinds == ["entry", "development", "engagement", "entry", "entry"]
    development = items[1]
    assert development["id"] == f"dev:{world.records['wait_development'].pk}"
    assert development["date"]["certainty"] == "confirmed"

    # The consultation's answer is one fetch away from its activity row.
    engagement = mcp.ok("fetch", {"id": items[2]["id"]})
    assert "Feedback received: Liikmed toetavad pikemat üleminekuaega" in engagement["text"]
    assert engagement["metadata"]["open_feedback_wait"] is False


# -- 3. «What are we waiting for?» -------------------------------------------------------


def test_q3_what_are_we_waiting_for(world, mcp):
    found = mcp.ok("search_records", {"query": "Kliimaministeerium"})
    assert world.matter_id("wait") in _ids(found["results"])

    waits = {}
    for key in ("wait", "do", "monitor", "packaging"):
        snapshot = mcp.ok("get_matter", {"matter": world.reference(key)})
        action = snapshot["current_next_action"]
        waits[key] = (action and action["kind"], snapshot["waiting_for"])

    assert waits == {
        "wait": ("WAIT", w.WAITING_FOR),
        # A DO is our own work, not something we wait for.
        "do": ("DO", None),
        # Watching a procedure is not waiting for an answer either.
        "monitor": ("MONITOR", None),
        "packaging": (None, None),
    }


# -- 4. «Who is responsible?» ------------------------------------------------------------


def test_q4_who_is_responsible(world, mcp):
    owners = {
        key: mcp.ok("get_matter", {"matter": world.reference(key)})["owner"]
        for key in ("wait", "do", "monitor", "closed")
    }
    assert owners == {
        "wait": "Marko Mets",
        "do": "Marko Mets",
        "monitor": "Liis Kask",
        "closed": "Liis Kask",
    }
    do = mcp.ok("get_matter", {"matter": world.reference("do")})["current_next_action"]
    assert (do["kind"], do["text"], do["responsible"]) == ("DO", w.DO_TEXT, "Marko Mets")
    assert do["date"]["semantics"] == "DEADLINE"
    # The window of what Marko is responsible for, where the register names it.
    commencements = mcp.ok("get_upcoming_commencements", {})["commencements"]
    assert {row["owner"] for row in commencements} == {"Marko Mets"}


# -- 5. «Which opinions have we submitted?» ----------------------------------------------


def test_q5_which_opinions_have_we_submitted(world, mcp):
    opinions = mcp.ok("search_records", {"query": "arvamus", "source_kinds": ["SUBMISSION"]})
    assert set(_ids(opinions["results"])) == {
        f"sub:{world.records['packaging_opinion'].pk}",
        f"sub:{world.records['income_opinion'].pk}",
    }
    for row in opinions["results"]:
        assert row["source_date"]["basis"] == "sent"
        assert row["source_date"]["certainty"] == "confirmed"

    # The official «no comments» reply is a send, and is never an opinion.
    monitor = mcp.ok("get_matter", {"matter": world.reference("monitor")})
    [reply] = monitor["submissions_sent"]
    assert reply["id"] == f"sub:{world.records['no_objection'].pk}"
    assert reply["kind"] == "NO_OBJECTION_RESPONSE"
    assert reply["is_opinion"] is False
    assert reply["is_official_no_comments_reply"] is True
    assert monitor["counts"]["opinions_sent"] == 0
    assert monitor["counts"]["no_comments_replies_sent"] == 1

    fetched = mcp.ok("fetch", {"id": reply["id"]})
    assert fetched["metadata"]["is_opinion"] is False
    assert "it is not an opinion" in fetched["text"]

    do = mcp.ok("get_matter", {"matter": world.reference("do")})
    [sent] = do["submissions_sent"]
    assert (sent["kind"], sent["is_opinion"], sent["recipients"]) == (
        "FORMAL_OPINION",
        True,
        ["Rahandusministeerium"],
    )
    assert sent["sent"]["certainty"] == "confirmed"


@pytest.mark.xfail(
    strict=True,
    reason=(
        "EVAL-001: a search row for an official «no comments» reply is labelled «Arvamus» "
        "(opinion) — readers.search_records/result_title take the label from the search "
        "projection's SOURCE_LABELS, which has one label for every SUBMISSION kind."
    ),
)
def test_q5_a_no_comments_reply_found_by_search_is_not_labelled_an_opinion(world, mcp):
    found = mcp.ok(
        "search_records", {"query": "märkusi ja ettepanekuid", "source_kinds": ["SUBMISSION"]}
    )
    [row] = [r for r in found["results"] if r["id"] == f"sub:{world.records['no_objection'].pk}"]
    assert row["source_label"] != "Arvamus"
    assert "Arvamus:" not in row["title"]


# -- 6. «What have we said previously about Pakendiseadus?» -----------------------------


def test_q6_what_have_we_said_about_the_packaging_act(world, mcp):
    results = mcp.ok("search", {"query": "pakendiseadus"})["results"]
    found = _ids(results)
    assert f"sub:{world.records['packaging_opinion'].pk}" in found
    assert f"hist:{world.records['historical_page'].pk}" in found
    assert world.matter_id("packaging") in found
    assert world.matter_id("packaging_waste") in found

    rows = mcp.ok(
        "search_records",
        {"query": "pakendiseadus", "source_kinds": ["SUBMISSION", "LEGACY_SOURCE_PAGE"]},
    )["results"]
    # Across matters: the current Teema's opinion and a closed Teema's OneNote page.
    assert {row["matter"]["id"] for row in rows} == {
        world.matter_id("packaging"),
        world.matter_id("closed"),
    }
    by_kind = {row["source_kind"]: row for row in rows}
    assert "pakendiaktsiisi" in by_kind["SUBMISSION"]["snippet"]
    assert by_kind["LEGACY_SOURCE_PAGE"]["source_date"]["basis"] == "onenote_page_created"

    said = mcp.ok("fetch", {"id": by_kind["SUBMISSION"]["id"]})
    assert "Koda vastustab pakendiaktsiisi tõstmist" in said["text"]
    assert said["metadata"]["is_opinion"] is True


# -- 7. «Find the historical document» ---------------------------------------------------


def test_q7_find_the_historical_document(world, mcp):
    link = world.records["historical_page"]
    results = mcp.ok("search", {"query": "pakendiseaduse arutelu 2019"})["results"]
    assert results[0]["id"] == f"hist:{link.pk}"

    page = mcp.ok("fetch", {"id": results[0]["id"]})
    assert "Koda on varem vastu olnud pakendiettevõtjate tagatisraha kohustuse" in page["text"]
    assert page["url"] == f"{w.ORIGIN}/ajalugu/{link.pk}/"
    assert page["title"].startswith(world.reference("closed"))
    provenance = page["metadata"]["provenance"]
    assert provenance["source"] == "OneNote (historical)"
    assert (provenance["notebook"], provenance["section"]) == ("Õigusosakond", "Keskkond")
    assert provenance["onenote_created_at"].startswith(str(link.source_page.source_created_at.year))
    assert "not dates of the events" in provenance["dates_note"]
    assert page["metadata"]["matter"]["id"] == world.matter_id("closed")
    assert page["metadata"]["truncated"] is False


# -- 8. «Which acts approach jõustumine?» ------------------------------------------------


def test_q8_which_acts_approach_commencement(world, mcp):
    window = mcp.ok("get_upcoming_commencements", {})
    assert (window["from"], window["to"]) == (_days(world, 0), _days(world, 29))

    [confirmed] = window["commencements"]
    assert confirmed["id"] == f"eff:{world.records['confirmed_commencement'].pk}"
    assert confirmed["kind"] == "KNOWN_DATE"
    assert confirmed["date"]["date"] == _days(world, 10)
    assert confirmed["date"]["certainty"] == "confirmed"
    assert confirmed["timing"] == "upcoming"
    assert confirmed["matter"]["id"] == world.matter_id("commencement")
    assert confirmed["source_url"].startswith("https://www.riigiteataja.ee/")

    informational = {row["id"]: row for row in window["informational"]}
    planned = informational[f"date:{world.records['planned_commencement'].pk}"]
    estimated = informational[f"date:{world.records['estimated_commencement'].pk}"]
    assert (planned["kind"], planned["date"]["certainty"]) == ("PLANNED_COMMENCEMENT", "planned")
    assert (estimated["kind"], estimated["date"]["certainty"]) == (
        "ESTIMATED_COMMENCEMENT",
        "estimated",
    )
    assert "must not be reported as facts" in window["informational_note"]
    # The past commencement is not in a window that starts today.
    past_id = f"eff:{world.records['past_commencement'].pk}"
    assert past_id not in json.dumps(window)

    behind = mcp.ok(
        "get_upcoming_commencements", {"from": _days(world, -30), "to": _days(world, -1)}
    )
    [past] = behind["commencements"]
    assert past["id"] == past_id
    assert past["timing"] == "past"
    assert "not whether the act took effect" in past["timing_note"]
    fetched = mcp.ok("fetch", {"id": past_id})
    for answer in (window, behind, fetched):
        flat = json.dumps(answer, ensure_ascii=False).lower()
        assert "entered into force" not in flat
        assert "jõustus" not in flat


# -- 9. «Topics related to Rahandusministeerium» -----------------------------------------


def test_q9_topics_related_to_the_ministry_of_finance(world, mcp):
    rows = mcp.ok("search_records", {"query": "Rahandusministeerium"})["results"]
    matters = {row["matter"]["id"] for row in rows}
    assert matters == {world.matter_id("do"), world.matter_id("monitor")}
    assert {row["matter"]["title"] for row in rows} == {
        "Tulumaksuseaduse muutmise eelnõu",
        "Riigieelarve seaduse muutmise eelnõu",
    }
    # And only those: the Climate Ministry's Teemad are not «related» by accident.
    assert world.matter_id("wait") not in matters


# -- 10. «What changed since last month?» ------------------------------------------------


def test_q10_what_changed_since_last_month(world, mcp):
    since = _days(world, -30)
    activity = mcp.ok("get_matter_activity", {"matter": world.reference("wait"), "since": since})
    assert activity["since"] == since
    dates = [item["date"]["date"] for item in activity["items"]]
    assert dates and all(day >= since for day in dates)
    assert f"entry:{world.records['wait_old_entry'].pk}" not in _ids(activity["items"])
    assert _ids(activity["items"]) == [
        f"entry:{world.records['wait_recent_entry'].pk}",
        f"dev:{world.records['wait_development'].pk}",
        f"eng:{world.records['engagement'].pk}",
        f"entry:{world.records['wait_survey_entry'].pk}",
    ]
    only_entries = mcp.ok(
        "get_matter_activity",
        {"matter": world.reference("wait"), "since": since, "kind": "entries"},
    )
    assert {item["kind"] for item in only_entries["items"]} == {"entry"}


# -- across the questions --------------------------------------------------------------


def test_an_inflected_query_finds_what_the_base_form_finds(mcp, world):
    base = _ids(mcp.ok("search", {"query": "pakendiseadus"})["results"])
    inflected = _ids(mcp.ok("search", {"query": "pakendiseaduse"})["results"])
    for key in ("packaging", "packaging_waste"):
        assert world.matter_id(key) in base
        assert world.matter_id(key) in inflected


def test_near_identical_titles_stay_two_matters(mcp, world):
    rows = mcp.ok(
        "search_records", {"query": "pakendiseaduse muutmise eelnõu", "source_kinds": ["MATTER"]}
    )["results"]
    titles = {row["id"]: row["matter"]["title"] for row in rows}
    assert titles[world.matter_id("packaging")] == "Pakendiseaduse muutmise eelnõu"
    assert (
        titles[world.matter_id("packaging_waste")]
        == "Pakendiseaduse ja jäätmeseaduse muutmise eelnõu"
    )
    one = mcp.ok("get_matter", {"matter": world.reference("packaging")})
    other = mcp.ok("get_matter", {"matter": world.reference("packaging_waste")})
    assert one["id"] != other["id"] and one["reference"] != other["reference"]


@pytest.mark.parametrize("spelling", ["{y}_{n}", "{y}-{n}", " {y}_{n} ", "{y} {n}"])
def test_every_reference_spelling_opens_the_same_matter(mcp, world, spelling):
    matter = world.matters["wait"]
    reference = spelling.format(y=matter.reference_year, n=matter.reference_number)
    assert mcp.ok("get_matter", {"matter": reference})["id"] == world.matter_id("wait")


def test_a_reference_search_puts_that_matter_first(mcp, world):
    for typed in (world.reference("do"), world.reference("do").replace("_", "-")):
        results = mcp.ok("search", {"query": typed})["results"]
        assert results[0]["id"] == world.matter_id("do"), typed


def test_the_long_document_is_read_whole_page_by_page(world, mcp):
    from django.conf import settings

    document = world.records["long_document"]
    listed = mcp.ok("get_matter_documents", {"matter": world.reference("packaging_waste")})
    [row] = listed["documents"]
    assert (row["fragment_count"], row["text_available"]) == (w.LONG_DOCUMENT_PAGES, True)
    assert row["fetch_id"] == f"doc:{document.pk}"

    seen: list[str] = []
    texts: dict[str, str] = {}
    next_id: str | None = row["fetch_id"]
    parts = 0
    while next_id is not None:
        part = mcp.ok("fetch", {"id": next_id})
        parts += 1
        assert len(part["text"]) <= settings.MCP_FETCH_MAX_CHARS
        assert part["url"] == f"{w.ORIGIN}/dokumendid/{document.pk}/"
        metadata = part["metadata"]
        pieces = w.document_pieces(part["text"])
        assert metadata["first_locator"] == pieces[0][0]
        assert metadata["last_locator"] == pieces[-1][0]
        assert metadata["fragments_total"] == w.LONG_DOCUMENT_PAGES
        for label, text in pieces:
            seen.append(label)
            texts[label] = texts.get(label, "") + text
        next_id = metadata["next_id"]
        assert metadata["truncated"] is (next_id is not None)
        assert parts < 20
    assert parts >= 2
    assert seen == [f"lk {n}" for n in range(1, w.LONG_DOCUMENT_PAGES + 1)], "each page once"
    assert texts == {f"lk {n}": page for n, page in enumerate(world.long_document_pages, start=1)}

    # A search hit inside the document opens at its page.
    [hit] = mcp.ok("search", {"query": w.NEEDLE})["results"]
    assert hit["id"].startswith("frag:")
    assert hit["title"].endswith(f"lk {w.NEEDLE_PAGE}")
    opened = mcp.ok("fetch", {"id": hit["id"]})
    assert opened["metadata"]["locator"] == f"lk {w.NEEDLE_PAGE}"
    assert w.document_pieces(opened["text"])[0][0] == f"lk {w.NEEDLE_PAGE}"
    assert w.NEEDLE in opened["text"]


def test_a_missing_next_action_is_reported_missing_not_invented(world, mcp):
    snapshot = mcp.ok("get_matter", {"matter": world.reference("packaging")})
    assert snapshot["current_next_action"] is None
    assert snapshot["waiting_for"] is None
    assert snapshot["planned_actions"] == []
    assert {"current_next_action", "stage", "opinion_deadline"} <= set(snapshot["missing"])
    text = mcp.ok("fetch", {"id": world.matter_id("packaging")})["text"]
    assert "Current next action: none recorded" in text
    assert "Hetkeseis (stage): not recorded" in text
    assert "Not recorded: current_next_action" in text


def test_a_closed_matter_says_it_is_closed_and_how(world, mcp):
    snapshot = mcp.ok("get_matter", {"matter": world.reference("closed")})
    assert snapshot["is_open"] is False
    assert snapshot["disposition"]["key"] == "MONITORING_STOPPED"
    assert snapshot["closed_on"] == _days(world, 0)
    assert snapshot["counts"]["historical_pages"] == 1


def _every_answer(world: w.EvalWorld, mcp: w.Mcp) -> list[tuple[str, dict[str, Any]]]:
    calls: list[tuple[str, dict[str, Any]]] = [
        ("search", {"query": "pakendiseadus"}),
        ("search", {"query": "Kliimaministeerium"}),
        ("search_records", {"query": "eelnõu"}),
        ("search_records", {"query": "pakend", "source_kinds": ["DOCUMENT_FRAGMENT"]}),
        ("get_upcoming_commencements", {}),
        ("get_upcoming_commencements", {"from": _days(world, -60), "to": _days(world, 60)}),
    ]
    for key in world.matters:
        reference = world.reference(key)
        calls += [
            ("get_matter", {"matter": reference}),
            ("get_matter_activity", {"matter": reference}),
            ("get_matter_documents", {"matter": reference}),
            ("fetch", {"id": world.matter_id(key)}),
        ]
    answers = list(zip([name for name, _ in calls], mcp.batch(calls), strict=True))
    ids = set()
    for name, result in answers:
        if name in ("search", "search_records"):
            ids |= set(_ids(w.structured(result, name)["results"]))
    fetched = mcp.batch([("fetch", {"id": source_id}) for source_id in sorted(ids)])
    return answers + [("fetch", result) for result in fetched]


URL_PATH = re.compile(
    r"^/(teemad/[0-9a-f-]{36}/(dokumendid/(\?roll=arvamus)?)?(#sissekanne-[0-9a-f-]{36})?"
    r"|dokumendid/[0-9a-f-]{36}/|ajalugu/[0-9a-f-]{36}/)$"
)


def test_every_url_is_an_absolute_link_to_the_right_page(world, mcp):
    checked = 0
    for name, result in _every_answer(world, mcp):
        answer = w.structured(result, name)
        for url in w.urls_in(answer):
            if url.startswith("https://www.riigiteataja.ee/"):
                continue  # a commencement's official source, recorded as such
            assert url.startswith(w.ORIGIN + "/"), (name, url)
            path = url.removeprefix(w.ORIGIN)
            assert URL_PATH.match(path), (name, url)
            for secret in (world.token("marko"), world.connections["marko"].refresh, "kood="):
                assert secret not in url
            checked += 1
    assert checked > 100


def test_every_answer_is_bounded(world, mcp):
    from django.conf import settings

    for name, result in _every_answer(world, mcp):
        size = len(json.dumps(result, ensure_ascii=False).encode())
        assert size < 64 * 1024, (name, size)
        if name == "fetch":
            assert len(w.structured(result, name)["text"]) <= settings.MCP_FETCH_MAX_CHARS
        if name == "search":
            assert len(w.structured(result, name)["results"]) <= 20


def test_a_reader_colleague_gets_the_same_answers(world):
    """docs/adr/0181: every business user reads the same register."""
    as_marko = world.mcp("marko").ok("get_matter", {"matter": world.reference("wait")})
    as_kati = world.mcp("kati").ok("get_matter", {"matter": world.reference("wait")})
    assert as_kati == as_marko


def test_an_account_that_is_now_an_administrator_gets_nothing(world):
    former = world.mcp("former")
    listed = former.rpc("tools/list", {})
    assert listed.status_code == 401
    assert "search" not in listed.text
    called = former.rpc(
        "tools/call", {"name": "get_matter", "arguments": {"matter": world.reference("wait")}}
    )
    assert called.status_code == 401
    for value in (world.matters["wait"].title, w.WAITING_FOR, "Marko"):
        assert value not in called.text


# -- the whole pass again, as the `mcp` process reads ------------------------------------


READ_ONLY_OPTIONS = "-c default_transaction_read_only=on -c statement_timeout=60000"


@pytest.fixture
def read_only_connections(monkeypatch, settings):
    """Every connection opened from here on is read-only, as role `mcp` opens them.

    The worker threads open a connection per call from these settings
    (`app.mcp_server.runtime`), and the cache is the database cache production
    uses, so neither a reader nor the token check can write anything anywhere.
    """
    database = connections.settings["default"]
    monkeypatch.setitem(
        database, "OPTIONS", {**database.get("OPTIONS", {}), "options": READ_ONLY_OPTIONS}
    )
    # Django's own setting_changed receiver rebuilds the cache handler.
    settings.CACHES = DATABASE_CACHE


def test_the_whole_tool_set_runs_on_read_only_connections(world, mcp, read_only_connections):
    import anyio
    from django.db import InternalError, connection

    runtime = ReaderRuntime(max_concurrent_calls=1, timeout_seconds=30)

    def probe() -> tuple[str, str]:
        with connection.cursor() as cursor:
            cursor.execute("SHOW default_transaction_read_only")
            setting = cursor.fetchone()[0]
            try:
                with connection.cursor() as inner:
                    inner.execute("INSERT INTO core_cache VALUES ('probe', 'x', now())")
            except InternalError as refused:
                return setting, str(refused)
            return setting, "the write went through"

    setting, refusal = anyio.run(runtime.run, probe)
    assert setting == "on"
    assert "read-only transaction" in refusal

    before = w.row_counts()
    listed = mcp.rpc("tools/list", {})
    assert listed.status_code == 200
    assert {tool["name"] for tool in listed.json()["result"]["tools"]} == w.TOOL_NAMES
    answers = _every_answer(world, mcp)
    assert {name for name, _ in answers} == w.TOOL_NAMES
    for name, result in answers:
        w.structured(result, name)
    assert w.row_counts() == before
