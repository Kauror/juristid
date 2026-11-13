# ruff: noqa: F811 - pytest fixtures imported from the sync suites are used as arguments
"""The Lovable reader against a PostgREST stand-in (audit 2026-11-11 QA-01, C-L-06).

`tests/lovable_postgrest_fake.py` answers `HttpTransport`'s real HTTP requests:
paging, a server ``max-rows`` cap, ``Content-Range``, rows that tie on the sort
key, 401 / 5xx. The rule under test: every row the source counted is read, or
the read refuses with a `SourceError` — a truncated or inconsistent population
is never accepted as a smaller source. GET only, throughout.

DB-free except the last two tests, which run a whole synchronisation through it.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

import pytest

from app.legacy_import import lovable_source, lovable_sync
from app.legacy_import.lovable_source import INDEX_COLUMNS, SourceError
from app.legacy_import.lovable_sync_models import (
    LovableSyncSnapshot,
    SyncRunStatus,
    SyncTrigger,
)
from tests.lovable_postgrest_fake import PostgrestFake, config
from tests.test_lovable_pilot import pilot_on, world  # noqa: F401
from tests.test_lovable_sync import NIGHT, _matter, _writes, cohort  # noqa: F401

T0 = dt.datetime(2099, 1, 1, tzinfo=dt.UTC)


def _topics(n: int) -> list[dict[str, Any]]:
    return [
        {
            "id": f"00000000-0000-0000-0000-{i:012d}",
            "nr": f"2099_{i}",
            "aasta": 2099 if i % 2 else 1999,
            "version": 1,
            "operatiivne_ulatus": "TAIELIK",
        }
        for i in range(1, n + 1)
    ]


def _transport(fake: PostgrestFake, pauses: list[float] | None = None) -> Any:
    log = pauses if pauses is not None else []
    return lovable_source.HttpTransport(config(), opener=fake, pause=log.append)


def _index(fake: PostgrestFake) -> list[dict[str, Any]]:
    return _transport(fake).select("topic", INDEX_COLUMNS, filters={}, order="id")


def _offsets(fake: PostgrestFake) -> list[int]:
    return [int(q["offset"]) for q in fake.reads("topic")]


def test_more_than_one_page_is_read_to_the_end_and_get_only():
    """Regression: QA-01 — 2,600 topics over three pages of 1,000."""
    fake = PostgrestFake({"topic": _topics(2600)})
    rows = _index(fake)
    assert len(rows) == 2600 and len({r["id"] for r in rows}) == 2600
    assert _offsets(fake) == [0, 1000, 2000]
    assert fake.methods == {"POST", "GET"}
    assert all(m == "GET" for m, p, _q in fake.requests if p != "/auth/v1/token")


def test_a_server_cap_below_the_page_size_still_reads_every_row():
    """Regression: QA-01 / C-L-06 — 1,200 topics behind a server ``max-rows`` of
    500. The reader used to stop at the first short page (500 < 1,000) and take
    500 topics for the whole source."""
    fake = PostgrestFake({"topic": _topics(1200)}, max_rows=500)
    rows = _index(fake)
    assert [r["id"] for r in rows] == [r["id"] for r in _topics(1200)]
    assert _offsets(fake) == [0, 500, 1000]


@pytest.mark.parametrize("cap", [1, 7, 999, 1000, 1001])
def test_an_unexpected_server_cap_never_changes_the_result(cap):
    """Regression: QA-01 — LC-S1: the result does not depend on the server cap."""
    expected = _index(PostgrestFake({"topic": _topics(1003)}))
    assert _index(PostgrestFake({"topic": _topics(1003)}, max_rows=cap)) == expected


def test_rows_that_tie_on_the_sort_key_refuse_rather_than_skip():
    """Regression: QA-01 — a sort key with ties lets the database serve the tied
    rows in a different order for every page: one row twice and another never.
    The count still adds up, so only the overlap shows it; it refuses."""
    fake = PostgrestFake({"topic": _topics(60)}, max_rows=10, shuffle_ties=True)
    with pytest.raises(SourceError) as refused:
        _transport(fake).select("topic", INDEX_COLUMNS, filters={}, order="aasta")
    assert refused.value.code == "PAGES_INCONSISTENT"


def test_every_read_the_snapshot_makes_is_ordered_to_a_unique_key():
    """Regression: QA-01 — the reads a run makes end their order on a unique key,
    so shuffled ties, a small server cap and the source's row order change
    nothing: the snapshot is identical (LC-S1)."""
    topics = _topics(120)
    ids = [t["id"] for t in topics]
    full = [{**t, "teema": f"Näidisteema {t['nr']}"} for t in topics]
    commencements = [
        {"id": f"c{i}", "topic_id": ids[i % 7], "liik": "K", "kuupaev": None, "position": 1}
        for i in range(40)
    ]

    def snapshot(**options: Any) -> dict[str, Any]:
        fake = PostgrestFake({"topic": full, "topic_commencement": commencements}, **options)
        return lovable_source.take_snapshot(
            _transport(fake), ids[:60], mapping_version="t", now=lambda: T0, pause=lambda _s: None
        )

    plain = snapshot()
    assert snapshot(max_rows=3, shuffle_ties=True, seed=2099)["sha256"] == plain["sha256"]
    assert snapshot(max_rows=17, shuffle_ties=True, seed=1999)["sha256"] == plain["sha256"]


def test_a_count_that_moves_between_pages_refuses():
    """Regression: QA-01 — ``Content-Range`` reports another total on page two."""
    fake = PostgrestFake({"topic": _topics(1200)}, max_rows=500)
    fake.content_range[("topic", 500)] = "500-999/1199"
    with pytest.raises(SourceError) as refused:
        _index(fake)
    assert refused.value.code == "PAGES_INCONSISTENT"


@pytest.mark.parametrize("change", ["delete", "insert"])
def test_a_row_missing_or_repeated_across_pages_refuses(change):
    """Regression: QA-01 — the source changes between page one and page two: a
    deleted row shifts the rest (one row is never served), an inserted row
    shifts them back (one row is served twice). Either refuses."""
    topics = _topics(1200)
    fake = PostgrestFake({"topic": topics}, max_rows=500)

    def between(table: str, offset: int) -> None:
        if offset == 500:
            if change == "delete":
                del topics[0]
            else:
                topics.insert(0, {**topics[0], "id": "00000000-0000-0000-0000-000000000000"})

    fake.between = between
    with pytest.raises(SourceError) as refused:
        _index(fake)
    assert refused.value.code == "PAGES_INCONSISTENT"


def test_a_page_that_is_not_the_range_it_names_refuses():
    """Regression: QA-01 — the server says rows 400-899 when 500 were asked."""
    fake = PostgrestFake({"topic": _topics(1200)}, max_rows=500)
    fake.content_range[("topic", 500)] = "400-899/1200"
    with pytest.raises(SourceError) as refused:
        _index(fake)
    assert refused.value.code == "PAGES_INCONSISTENT"


def test_a_truncated_page_refuses():
    """Regression: QA-01 — fewer rows in the body than ``Content-Range`` admits."""
    fake = PostgrestFake({"topic": _topics(30)})
    fake.short_pages = True
    with pytest.raises(SourceError) as refused:
        _index(fake)
    assert refused.value.code == "PAGES_INCONSISTENT"


def test_a_count_that_runs_out_early_refuses():
    """Regression: QA-01 — the server counts 1,200 and then serves an empty page."""
    topics = _topics(1200)
    fake = PostgrestFake({"topic": topics}, max_rows=500)
    fake.content_range[("topic", 1000)] = "*/1200"

    def between(table: str, offset: int) -> None:
        if offset == 1000:
            del topics[1000:]

    fake.between = between
    with pytest.raises(SourceError) as refused:
        _index(fake)
    assert refused.value.code == "TRUNCATED"


@pytest.mark.parametrize("header", [None, "0-29/*", "garbage"])
def test_an_answer_without_an_exact_count_refuses(header):
    """Regression: QA-01 — with no exact count the read cannot prove it saw
    every row, so it refuses instead of guessing from the page length."""
    fake = PostgrestFake({"topic": _topics(30)})
    fake.content_range[("topic", 0)] = header  # type: ignore[assignment]
    if header is None:
        original = fake.__call__

        def without(request: Any, timeout: float) -> Any:
            answer = original(request, timeout)
            answer.headers = {}
            return answer

        transport = lovable_source.HttpTransport(config(), opener=without)
    else:
        transport = _transport(fake)
    with pytest.raises(SourceError) as refused:
        transport.select("topic", INDEX_COLUMNS, filters={}, order="id")
    assert refused.value.code == "NO_ROW_COUNT"


def test_an_empty_source_is_an_empty_read():
    fake = PostgrestFake({"topic": []})
    assert _index(fake) == []


def test_filters_and_the_count_are_the_filtered_rows():
    topics = _topics(1200)
    fake = PostgrestFake({"topic": topics}, max_rows=500)
    wanted = [t["id"] for t in topics[::3]]
    rows = _transport(fake).select(
        "topic", INDEX_COLUMNS, filters={"id": "in.(" + ",".join(wanted) + ")"}, order="id"
    )
    assert [r["id"] for r in rows] == sorted(wanted)


def test_an_auth_failure_refuses_at_once():
    """Regression: QA-01 — 401 is the source refusing this caller: no retry."""
    fake = PostgrestFake({"topic": _topics(3)})
    fake.fail = [401]
    pauses: list[float] = []
    with pytest.raises(SourceError) as refused:
        _transport(fake, pauses).select("topic", INDEX_COLUMNS, filters={}, order="id")
    assert refused.value.code == "HTTP_401"
    assert len(fake.reads("topic")) == 1 and pauses == []


def test_a_transient_gateway_error_is_retried_and_then_reads_everything():
    """Regression: QA-01 — 503 twice, then the answer: a GET repeated is harmless."""
    fake = PostgrestFake({"topic": _topics(1200)}, max_rows=500)
    fake.fail = [503, 502]
    pauses: list[float] = []
    rows = _transport(fake, pauses).select("topic", INDEX_COLUMNS, filters={}, order="id")
    assert len(rows) == 1200
    assert pauses == [lovable_source.RETRY_PAUSE_SECONDS, 2 * lovable_source.RETRY_PAUSE_SECONDS]


@pytest.mark.parametrize("statuses", [[503, 503, 503], [500]])
def test_a_lasting_or_internal_server_error_refuses(statuses):
    """Regression: QA-01 — bounded: three gateway errors in a row refuse; a 500
    is not transient and refuses at once."""
    fake = PostgrestFake({"topic": _topics(3)})
    fake.fail = list(statuses)
    with pytest.raises(SourceError) as refused:
        _transport(fake).select("topic", INDEX_COLUMNS, filters={}, order="id")
    assert refused.value.code == f"HTTP_{statuses[0]}"
    assert len(fake.reads("topic")) == len(statuses)


def test_the_retry_never_sends_anything_but_a_get():
    fake = PostgrestFake({"topic": _topics(3)})
    fake.fail = [503]
    _transport(fake).select("topic", INDEX_COLUMNS, filters={}, order="id")
    assert [m for m, p, _q in fake.requests if p != "/auth/v1/token"] == ["GET", "GET"]


# ---------------------------------------------------------------------------
# A whole run through the HTTP reader
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_a_run_through_a_capped_server_equals_the_in_process_reading(cohort):
    """Regression: QA-01 — LC-S1 end to end: the same synchronisation through the
    real transport behind a server cap of 2 rows reads what the in-process
    fake answers directly."""
    fake = PostgrestFake(source=cohort["source"].select, max_rows=2, shuffle_ties=True)
    run = lovable_sync.run_sync(
        trigger=SyncTrigger.MANUAL,
        transport=_transport(fake),
        backup_set="",
        now=lambda: NIGHT,
        pause=lambda _s: None,
    )
    assert run.status in (SyncRunStatus.SUCCEEDED, SyncRunStatus.REVIEW_ONLY), run.error
    direct = lovable_source.take_snapshot(
        cohort["source"],
        sorted(str(s.source_id) for s in lovable_sync.population()),
        mapping_version=lovable_sync.MAPPING_VERSION,
        now=lambda: NIGHT,
        pause=lambda _s: None,
    )
    assert run.snapshot.document["cohort"] == direct["cohort"]
    assert fake.methods == {"POST", "GET"}


@pytest.mark.django_db
def test_a_run_whose_source_is_truncated_refuses_and_writes_nothing(cohort):
    """Regression: QA-01 — a truncated reading fails the run by name; nothing is
    written, nothing stored as a reading, no baseline moves."""
    cohort["source"].edit("2026_501", teema="Uus 501")
    fake = PostgrestFake(source=cohort["source"].select, max_rows=2)
    fake.content_range[("topic", 2)] = "*/9"
    before = _writes()
    run = lovable_sync.run_sync(
        trigger=SyncTrigger.MANUAL,
        transport=_transport(fake),
        backup_set=(NIGHT - dt.timedelta(minutes=5))
        .astimezone(dt.UTC)
        .strftime("sets/%Y%m%dT%H%M%SZ"),
        now=lambda: NIGHT,
        pause=lambda _s: None,
    )
    assert run.status == SyncRunStatus.FAILED
    assert run.error.startswith(("PAGES_INCONSISTENT", "TRUNCATED")), run.error
    assert _writes() == before and _matter("2026_501").title == "Näidisteema 2026_501"
    assert not LovableSyncSnapshot.objects.filter(run=run).exists()


def test_an_edit_between_pages_that_keeps_the_count_is_caught_by_the_double_read():
    """QA-01 (review P3-4): paging by offset cannot see an edit that changes a
    row's content between pages without moving any row; the snapshot's second
    pass can (`take_snapshot` reads index and cohort twice and compares), and
    the reading is taken again rather than stitched together."""
    topics = _topics(30)
    fake = PostgrestFake({"topic": topics}, max_rows=10)
    edits = {"done": False}

    def between(table: str, offset: int) -> None:
        if offset == 10 and not edits["done"]:
            edits["done"] = True
            topics[0]["version"] = 2  # already served on page one

    fake.between = between
    pauses: list[float] = []
    document = lovable_source.take_snapshot(
        _transport(fake), [], mapping_version="t", now=lambda: T0, pause=pauses.append
    )
    assert document["attempts"] == 2 and pauses
    assert document["index"][0]["version"] == 2
