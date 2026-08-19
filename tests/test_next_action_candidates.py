"""The `JÄRGMISEKS` candidate extractor.

The rules are allowed to be narrow. They are not allowed to be wrong, and they
are never allowed to write state — which is why the strongest tests here are the
ones asserting that ordinary prose produces no proposal at all.
"""

from __future__ import annotations

import datetime as dt

import pytest

from app.legacy_import.next_actions import (
    DETERMINISTIC,
    REVIEW_REQUIRED,
    extract_candidate,
)
from app.workflow.enums import ActionKind, DatePrecision, DateSemantics


def test_an_explicit_review_date_becomes_a_monitor_candidate() -> None:
    candidate = extract_candidate("vaata 01.09.2026 üle, 07.08.2026 seisuga")
    assert candidate is not None
    assert candidate.kind == ActionKind.MONITOR
    assert candidate.date_semantics == DateSemantics.REVIEW_ON
    assert candidate.target_date == dt.date(2026, 9, 1)
    assert candidate.date_precision == DatePrecision.EXACT
    assert candidate.confidence == DETERMINISTIC


def test_the_trailing_particle_is_optional_because_the_verb_carries_the_meaning() -> None:
    candidate = extract_candidate("vaatan 14.03.2026")
    assert candidate is not None
    assert candidate.kind == ActionKind.MONITOR
    assert candidate.target_date == dt.date(2026, 3, 14)


def test_a_stated_deadline_becomes_a_do_candidate() -> None:
    candidate = extract_candidate("Tähtaeg 14.03.2026, vastus tuleb esitada.")
    assert candidate is not None
    assert candidate.kind == ActionKind.DO
    assert candidate.date_semantics == DateSemantics.DEADLINE
    assert candidate.target_date == dt.date(2026, 3, 14)


def test_a_quarter_expectation_keeps_its_imprecision() -> None:
    """A guess about a ministry's timetable must not be stored as a day."""
    candidate = extract_candidate("Ootan eelnõud 2027. aasta 2. kvartalis.")
    assert candidate is not None
    assert candidate.kind == ActionKind.WAIT
    assert candidate.date_semantics == DateSemantics.EXPECTED_AROUND
    assert candidate.date_precision == DatePrecision.QUARTER
    assert candidate.target_date == dt.date(2027, 4, 1)


def test_a_year_expectation_keeps_its_imprecision() -> None:
    candidate = extract_candidate("Ootan otsust 2028. aastal.")
    assert candidate is not None
    assert candidate.date_precision == DatePrecision.YEAR


def test_an_entry_into_force_date_is_not_an_instruction_to_anybody() -> None:
    """`jõustub 01.09.2026` is a fact about the world, not a next action.

    It is the most common dated phrase in the real column, and reading it as a
    review reminder would fill the work queue with things nobody has to do.
    """
    candidate = extract_candidate("Seadus jõustub 01.09.2026.")
    assert candidate is not None
    assert candidate.confidence == REVIEW_REQUIRED
    assert candidate.kind == ""
    assert candidate.target_date is None


@pytest.mark.parametrize(
    "text",
    [
        "Räägime läbi ja vaatame, mis edasi saab.",
        "Kohtumine ministeeriumis, seejärel otsustame.",
        "Ootan ministeeriumi vastust.",
        "Jälgin.",
    ],
)
def test_ordinary_prose_produces_no_proposal(text: str) -> None:
    candidate = extract_candidate(text)
    assert candidate is not None
    assert candidate.confidence == REVIEW_REQUIRED
    assert candidate.target_date is None


def test_the_original_text_is_always_carried_with_the_proposal() -> None:
    source = "vaata 01.09.2026 üle"
    candidate = extract_candidate(source)
    assert candidate is not None
    assert candidate.source_text == source
    assert candidate.rule_id and candidate.rules_version


def test_an_empty_cell_produces_nothing_at_all() -> None:
    assert extract_candidate("") is None
    assert extract_candidate("   ") is None


def test_an_impossible_date_does_not_become_a_candidate_date() -> None:
    candidate = extract_candidate("vaata 31.02.2026 üle")
    assert candidate is not None
    assert candidate.confidence == REVIEW_REQUIRED
