"""The access probe judges source identities, not totals (coordinator review of
#658, 2026-11-20).

A population rule (more than 50 and more than 5 % short) let a reading 100
topics short of 2,524 pass, while a night refuses after `MAX_MISSING` (5)
synchronised topics go missing. The probe now compares tonight's reading with
the last trusted reading by identity and child record (`TrustedReading.judge`):

* 2,524 expected, 2,424 visible → not ready;
* the same count with other identities → not ready;
* synchronised topics missing beyond the night's limit → not ready, while new
  topics (and a few deletions within the limit) are ordinary;
* a child table narrowed while the topic index is intact → not ready;
* no trusted reading yet → not proven (`tests/test_lovable_source_security.py`).

DB-free: the trusted reading is built in memory.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

import pytest

from app.legacy_import import lovable_sync
from app.legacy_import.lovable_source import CHILDREN_CHECK, FAIL, IDENTITY_CHECK, PASS
from app.legacy_import.lovable_sync import MAX_MISSING, TrustedReading

TAKEN = dt.datetime(2026, 11, 18, 22, 18, tzinfo=dt.UTC)


def _id(n: int) -> str:
    return f"00000000-0000-4000-8000-{n:012d}"


def _row(n: int, version: int = 1) -> dict[str, Any]:
    return {"id": _id(n), "nr": f"2026_{n}", "version": version, "operatiivne_ulatus": True}


def _bundle(n: int, version: int = 1, *, commencements: int = 1, links=()) -> dict[str, Any]:
    return {
        "row": _row(n, version),
        "commencements": [
            {"id": f"c{n}-{k}", "topic_id": _id(n), "liik": "kuupäev", "kuupaev": "2027-01-01"}
            for k in range(commencements)
        ],
        "responsible_changes": [],
        "stage_links": [{"prev": _id(a), "next": _id(b)} for a, b in links],
    }


def _trusted(live: int, synced: int, **cohort_kwargs: Any) -> TrustedReading:
    return TrustedReading(
        taken_at=TAKEN,
        index=[_row(n) for n in range(live)],
        cohort={_id(n): _bundle(n, **cohort_kwargs) for n in range(synced)},
        cohort_ids=[_id(n) for n in range(synced)],
    )


def _verdicts(trusted: TrustedReading, index, cohort) -> dict[str, str]:
    return {c.name: c.status for c in trusted.judge(index, cohort)}


def test_2524_expected_2424_visible_is_not_ready():
    trusted = _trusted(2524, 2370)
    # The hundred hidden topics are all outside the synchronised cohort: a night
    # would not count them, the population rule (126 at 2,524) would not either.
    index = [_row(n) for n in range(2424)]
    cohort = {_id(n): _bundle(n) for n in range(2370)}
    assert lovable_sync.population_change({"index": trusted.index}, {"index": index}) == ""
    assert _verdicts(trusted, index, cohort)[IDENTITY_CHECK] == FAIL


def test_the_same_count_with_other_identities_is_not_ready():
    trusted = _trusted(2524, 2370)
    swapped = list(range(10)) + list(range(2524, 2524 + 10))  # 10 synced gone, 10 new
    index = [_row(n) for n in range(10, 2524)] + [_row(n) for n in range(2524, 2534)]
    cohort = {_id(n): _bundle(n) for n in range(10, 2370)}
    assert len(index) == len(trusted.index) and swapped
    assert _verdicts(trusted, index, cohort)[IDENTITY_CHECK] == FAIL


@pytest.mark.parametrize(
    ("synced_gone", "new", "status"),
    [
        (0, 40, PASS),  # new topics are the register growing
        (MAX_MISSING, 40, PASS),  # deletions within the night's limit
        (MAX_MISSING + 1, 0, FAIL),  # what the night refuses
        (MAX_MISSING + 1, 400, FAIL),  # new topics never cover for missing ones
    ],
)
def test_missing_synchronised_topics_against_new_ones(synced_gone, new, status):
    trusted = _trusted(200, 150)
    kept = range(synced_gone, 200)
    index = [_row(n) for n in kept] + [_row(n) for n in range(1000, 1000 + new)]
    cohort = {_id(n): _bundle(n) for n in range(synced_gone, 150)}
    verdicts = _verdicts(trusted, index, cohort)
    assert verdicts[IDENTITY_CHECK] == status
    assert verdicts[CHILDREN_CHECK] == PASS


def test_a_narrowed_child_table_under_an_intact_index_is_not_ready():
    trusted = _trusted(20, 20, commencements=2)
    index = [_row(n) for n in range(20)]
    cohort = {_id(n): _bundle(n, commencements=2) for n in range(20)}
    cohort[_id(3)] = _bundle(3, commencements=1)  # a row gone, version unchanged
    verdicts = _verdicts(trusted, index, cohort)
    assert verdicts[IDENTITY_CHECK] == PASS
    assert verdicts[CHILDREN_CHECK] == FAIL


def test_a_child_edit_that_moved_the_version_is_ordinary():
    trusted = _trusted(20, 20, commencements=2)
    index = [_row(n) for n in range(20) if n != 3] + [_row(3, version=2)]
    cohort = {_id(n): _bundle(n, commencements=2) for n in range(20)}
    cohort[_id(3)] = _bundle(3, version=2, commencements=1)
    assert _verdicts(trusted, index, cohort) == {IDENTITY_CHECK: PASS, CHILDREN_CHECK: PASS}


def test_a_stage_link_hidden_while_both_ends_kept_their_versions_is_not_ready():
    trusted = _trusted(10, 10, links=[(1, 2)])
    index = [_row(n) for n in range(10)]
    hidden = {_id(n): _bundle(n) for n in range(10)}
    assert _verdicts(trusted, index, hidden)[CHILDREN_CHECK] == FAIL
    # A link removed by an edit of its other end (that end's version moved) is ordinary.
    edited = [_row(n) for n in range(10) if n != 2] + [_row(2, version=2)]
    moved = {**hidden, _id(2): _bundle(2, version=2)}
    assert _verdicts(trusted, edited, moved)[CHILDREN_CHECK] == PASS
    # A link *added* from the other end is ordinary (seen once in production readings).
    added = {_id(n): _bundle(n, links=[(1, 2), (5, 6)]) for n in range(10)}
    assert _verdicts(trusted, index, added)[CHILDREN_CHECK] == PASS


def test_a_widened_allowlist_is_not_a_hidden_child_row():
    """Independent review 2026-11-20: a release reading one more child column
    adds it to every row; only the columns both readings carry are compared."""
    trusted = _trusted(5, 5, commencements=2)
    index = [_row(n) for n in range(5)]
    cohort = {_id(n): _bundle(n, commencements=2) for n in range(5)}
    for bundle in cohort.values():
        for row in bundle["commencements"]:
            row["tapsustus"] = None
    assert _verdicts(trusted, index, cohort)[CHILDREN_CHECK] == PASS
