"""Parser 2.0 — the year the sheet supplies, and the clause that owns a date.

1.2 could see a year-less date and had to refuse it, because "the next 15
September" is a fact about the day somebody read the cell rather than about what
the cell says. That refusal was right with no provenance to hand. It stopped
being the only option once each derived row learned which *sheet of which dated
workbook* it came from: on the 2026 sheet of a workbook taken on 28 August 2026,
*vaata üle 15.09* was written this year about this year, and the omitted year is
the one thing the sheet and the snapshot agree on.

So 2.0 adds one inference and two refinements, and no new vocabulary class:

**Year = sheet year, and only where the snapshot agrees.** Never "the next such
date after today" — a 2026 row reading *vaata 15.07 üle* in an August snapshot
means 15 July 2026, a date that has passed, and the enrichment planner reports it
as stale. It must never become 2027, which the source did not say. On the 2025
sheet of the same workbook nothing is inferred at all: that row is still
maintained, somebody may have edited it in November 2025 or in August 2026, and
the sheet cannot say which.

**A wait and a review of it is one instruction.** *ootan valitsusele saatmist,
vaata üle 15.09* names one date and it says when Koda looks at the wait. WAIT,
because Koda is not the one who has to act; ``REVIEW_ON``, because that is what
the date means. A WAIT is never overdue, so nothing here can put a ministry's
timetable on this department's late list.

**Clause ownership removes a date rather than being defeated by it.** An
entry-into-force clause and a past-tense clause each own their date, and 1.2
only noticed when such a date was the *only* one — so a sentence carrying a live
review date beside a commentary date was refused as ambiguous and both were
thrown away. Filtering first is what lets the review date win *because the other
belongs to another clause*, which is a reading rather than a preference for
first, last or nearest.

Every sentence below is invented. None is a Koda register row.
"""

from __future__ import annotations

import datetime as dt

import pytest

from app.legacy_import.register_next_actions import (
    NO_CONTEXT,
    REGISTER_NEXT_ACTION_PARSER_VERSION,
    ParseContext,
    ReviewReason,
    Verdict,
    parse_instruction,
)
from app.workflow.enums import ActionKind, DatePrecision, DateSemantics

#: The snapshot this module reasons about: taken on 28 August 2026.
SNAPSHOT_DATE = dt.date(2026, 8, 28)
SHEET_2026 = ParseContext(sheet_year=2026, snapshot_date=SNAPSHOT_DATE)
SHEET_2025 = ParseContext(sheet_year=2025, snapshot_date=SNAPSHOT_DATE)


def read(text: str, context: ParseContext = SHEET_2026):
    return parse_instruction(text, context=context)


def reasons(text: str, context: ParseContext = SHEET_2026) -> tuple[str, ...]:
    return read(text, context).review_reasons


def test_the_version_is_recorded_on_every_reading():
    """Two runs that read the same sentence differently must be tellable apart.

    The version travels inside the plan digest, so a rule change cannot be
    mistaken for a source change — which is the whole reason it is a constant
    and not a comment.
    """
    assert REGISTER_NEXT_ACTION_PARSER_VERSION == "2.1"
    assert read("vaata üle septembris").parser_version == "2.1"


# ---------------------------------------------------------------------------
# The year the sheet supplies
# ---------------------------------------------------------------------------


def test_a_yearless_day_on_the_snapshot_year_sheet_is_read():
    parsed = read("Vaatan 07.09 eelnõu seisu üle.")

    assert parsed.verdict == Verdict.UNDERSTOOD
    assert parsed.kind == ActionKind.MONITOR
    assert parsed.date_semantics == DateSemantics.REVIEW_ON
    assert parsed.target_date == dt.date(2026, 9, 7)
    assert parsed.date_precision == DatePrecision.EXACT


def test_the_particle_may_sit_after_the_date():
    """*Vaatan üle 10.09* and *Vaatan 10.09 üle* are the same instruction."""
    for text in ("Vaatan üle 10.09", "Vaatan 10.09 üle"):
        parsed = read(text)
        assert parsed.verdict == Verdict.UNDERSTOOD, text
        assert parsed.target_date == dt.date(2026, 9, 10), text


def test_a_yearless_month_becomes_that_month_of_the_sheet_year():
    parsed = read("vaata üle septembris")

    assert parsed.verdict == Verdict.UNDERSTOOD
    assert parsed.kind == ActionKind.MONITOR
    assert parsed.date_semantics == DateSemantics.REVIEW_ON
    assert parsed.target_date == dt.date(2026, 9, 1)
    assert parsed.date_precision == DatePrecision.MONTH


def test_a_month_that_has_passed_stays_in_its_own_year():
    """Read as written, and reported stale — never rolled into next year.

    The month is over at the snapshot date, and *that is the finding*. Reading
    it as September 2027 would put an instruction on somebody's list that the
    register never wrote, and reading it as "the next September" would make the
    answer depend on the day the command happened to run (brief 13, 14).
    """
    parsed = read("vaata üle juulis")

    assert parsed.verdict == Verdict.UNDERSTOOD
    assert parsed.target_date == dt.date(2026, 7, 1)
    assert parsed.date_precision == DatePrecision.MONTH
    assert parsed.is_stale(SNAPSHOT_DATE) is True


def test_a_yearless_day_already_behind_the_snapshot_is_not_rolled_forward():
    parsed = read("vaata 15.07 üle")

    assert parsed.verdict == Verdict.UNDERSTOOD
    assert parsed.target_date == dt.date(2026, 7, 15)
    assert parsed.target_date.year == 2026
    assert parsed.is_stale(SNAPSHOT_DATE) is True


def test_the_month_boundary_is_the_end_and_not_the_anchor():
    """A month is not stale on its second day.

    ``is_stale`` asks about the period's *end*, so August 2026 is still live on
    28 August. Getting this wrong would report a third of the converted work as
    expired the moment it was created.
    """
    parsed = read("vaata üle augustis")

    assert parsed.target_date == dt.date(2026, 8, 1)
    assert parsed.is_stale(SNAPSHOT_DATE) is False


# ---------------------------------------------------------------------------
# 2025 is stricter, and not because it is older
# ---------------------------------------------------------------------------


def test_the_same_yearless_date_on_the_2025_sheet_is_refused():
    """The sheet and the snapshot disagree, so nothing settles the year.

    A 2025 row is still maintained. Somebody may have typed *15.07* into it in
    November 2025 or in August 2026, and those are two days a year apart; the
    register's writers resolve it by remembering, which is not evidence.
    """
    assert reasons("vaata 15.07 üle", SHEET_2025) == (ReviewReason.DATE_WITHOUT_YEAR,)
    assert reasons("vaata üle septembris", SHEET_2025) == (ReviewReason.DATE_WITHOUT_YEAR,)


def test_a_2025_row_with_a_written_year_is_read_as_written():
    """Strictness is about the *omitted* year, not about the sheet.

    A 2025 row naming 28.09.2026 said 2026. Nothing is inferred, so nothing is
    refused.
    """
    parsed = read("Vaata teema 28.09.2026 uuesti üle.", SHEET_2025)

    assert parsed.verdict == Verdict.UNDERSTOOD
    assert parsed.kind == ActionKind.MONITOR
    assert parsed.target_date == dt.date(2026, 9, 28)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Vaata 28.09.2026 üle.", dt.date(2026, 9, 28)),
        ("Vaata 08.11.2026 üle.", dt.date(2026, 11, 8)),
        ("Vaata 05.01.2027 üle.", dt.date(2027, 1, 5)),
    ],
)
def test_written_years_are_taken_from_the_sentence_on_either_sheet(text, expected):
    for context in (SHEET_2025, SHEET_2026):
        assert read(text, context).target_date == expected


def test_without_a_context_nothing_gains_a_year():
    """The default, and the 1.2 behaviour it preserves.

    A caller that has not thought about provenance must not accidentally
    acquire one — which is why the context is a parameter rather than something
    this module reads from anywhere.
    """
    assert reasons("Vaatan 07.09 eelnõu seisu üle.", NO_CONTEXT) == (
        ReviewReason.DATE_WITHOUT_YEAR,
    )
    assert ParseContext(sheet_year=2026).yearless_year is None
    assert ParseContext(snapshot_date=SNAPSHOT_DATE).yearless_year is None
    assert SHEET_2026.yearless_year == 2026
    assert SHEET_2025.yearless_year is None


def test_an_impossible_yearless_day_is_unreadable_rather_than_dated():
    """29.02 in a year that has no 29 February.

    Each half is plausible and the pair is not, so the sentence carries an
    unreadable date rather than a year-less one — and is refused, not silently
    moved to the 28th.
    """
    assert reasons("vaata 29.02 üle") == (ReviewReason.UNREADABLE_DATE,)
    assert reasons("vaata 32.13 üle") == (ReviewReason.UNREADABLE_DATE,)


# ---------------------------------------------------------------------------
# A wait and a review of it
# ---------------------------------------------------------------------------


def test_a_wait_with_one_review_date_is_one_instruction():
    parsed = read("ootan valitsusele saatmist, vaata üle 15.09")

    assert parsed.verdict == Verdict.UNDERSTOOD
    assert parsed.kind == ActionKind.WAIT
    assert parsed.date_semantics == DateSemantics.REVIEW_ON
    assert parsed.target_date == dt.date(2026, 9, 15)
    assert parsed.date_precision == DatePrecision.EXACT


def test_the_review_date_is_not_the_other_partys_deadline():
    """``REVIEW_ON``, never ``DEADLINE``, and the difference is operational.

    Only a DO with a DEADLINE can be reported overdue. A wait on a ministry
    stored as a deadline would make every ordinary dependency a false alarm,
    which is the failure ``ActionKind`` was split to prevent.
    """
    parsed = read("ootan riigikogule saatmist, vaata üle septembris")

    assert parsed.kind == ActionKind.WAIT
    assert parsed.date_semantics != DateSemantics.DEADLINE
    assert parsed.date_semantics == DateSemantics.REVIEW_ON


def test_the_pair_is_still_refused_when_no_date_settles_it():
    """Two timings and neither written down is the case 1.2 was built for."""
    assert reasons("ootan 2. lugemist riigikogus, vaata üle") == (ReviewReason.WAIT_AND_REVIEW,)


def test_a_third_kind_beside_the_pair_is_ambiguous_again():
    """The pair is read as one instruction only in exactly that shape.

    A wait, a review *and* work Koda must do is three timings, and reading the
    first date as the answer would discard the other two.
    """
    assert reasons("ootan vastust, vaata üle 15.09, esitada arvamus") == (
        ReviewReason.AMBIGUOUS_KIND,
    )


def test_two_actionable_dates_beside_the_pair_are_still_refused():
    """A second date nobody else's clause owns is a genuine ambiguity.

    An external milestone is not commentary — the register writes plenty of
    them — and choosing between it and the review date is not a reading.
    """
    assert reasons(
        "ootan ELis edasiliikumist, vaata üle 15.10, täiskogu 1. lugemine toimub 05.10.2026"
    ) == (ReviewReason.AMBIGUOUS_DATE,)


# ---------------------------------------------------------------------------
# Which clause owns the date
# ---------------------------------------------------------------------------


def test_a_review_date_survives_an_entry_into_force_date_beside_it():
    """The refinement, and the case that motivated it.

    Both dates are real. One is when Koda looks at the file and the other is
    when the amendments start applying, and the sentence says which is which in
    words. 1.2 called that ambiguous and lost the review.
    """
    parsed = read("Vaatan üle 15.09, muudatused jõustuvad 2027. aasta 17. juunil.")

    assert parsed.verdict == Verdict.UNDERSTOOD
    assert parsed.target_date == dt.date(2026, 9, 15)


def test_the_year_first_written_date_is_one_date_and_not_two():
    """*2027. aasta 17. juunil* — the order the register also writes.

    Without this pattern the phrase came apart into a bare year and a bare
    month, so the sentence carried two dates where it wrote one — and the loose
    month then looked year-less enough to take the snapshot year.
    """
    parsed = read("Vaatan üle 12.09, määrus jõustub 2027. aasta 1. juulil.")

    assert parsed.verdict == Verdict.UNDERSTOOD
    assert parsed.target_date == dt.date(2026, 9, 12)


def test_an_entry_into_force_date_alone_is_still_refused():
    """The load-bearing protection, unchanged.

    Filtering the date out must not turn this into a dateless WAIT: the
    sentence has a date, it belongs to the act, and the honest answer is the
    named refusal 1.2 already gave.
    """
    assert reasons("ootan RT linki, jõustub 1.01.2028") == (
        ReviewReason.DATE_GOVERNED_BY_ANOTHER_CLAUSE,
    )
    assert reasons("Vastu võetud, jõustub 1.01.2027, ootan RT linki") == (
        ReviewReason.DATE_GOVERNED_BY_ANOTHER_CLAUSE,
    )


def test_a_historic_note_does_not_become_a_target():
    """*Kooskõlastusringi tähtaeg oli 26.08* is a note about a closed round."""
    parsed = read("Vaatan 21.10 üle, kas eelnõu on jõudnud valitsusse. Tähtaeg oli 26.08.")

    assert parsed.verdict == Verdict.UNDERSTOOD
    assert parsed.target_date == dt.date(2026, 10, 21)


def test_a_sentence_whose_only_date_is_historic_is_refused():
    """Named, so the operator report can say where these instructions went."""
    assert reasons("Kooskõlastusring lõppes 18.08, vaata üle") == (ReviewReason.HISTORIC_DATE,)
    assert reasons("Viimati vaatasin 06.08.2026, vaata üle") == (ReviewReason.HISTORIC_DATE,)


def test_a_date_in_a_relative_clause_is_still_refused():
    """Unchanged from 1.2, and deliberately not folded into the filter.

    The relative clause describes the thing being waited for. Sometimes its
    date is the same week as the wait and sometimes a year off, and the
    sentence says which only to somebody who knows the file.
    """
    assert reasons("ootan ELAKi seisukohta, mille arutelu 27.07.2027") == (
        ReviewReason.DATE_IN_RELATIVE_CLAUSE,
    )


# ---------------------------------------------------------------------------
# What an external date may never become
# ---------------------------------------------------------------------------


def test_someone_elses_deadline_is_a_wait_and_never_an_overdue_deadline():
    """*Ootan RMi vastust hiljemalt 26.09* is Koda waiting, not Koda late.

    Deadline wording is present and it is the ministry's. Only *Koda must do X
    by Y* may become DO + DEADLINE, which is the one combination the work lists
    can report as missed (brief 18).
    """
    parsed = read("Ootan RMi vastust hiljemalt 26.09.")

    assert parsed.verdict == Verdict.UNDERSTOOD
    assert parsed.kind == ActionKind.WAIT
    assert parsed.date_semantics != DateSemantics.DEADLINE


def test_kodas_own_stated_deadline_is_still_a_deadline():
    """The other side of the same rule, so it is visibly not a blanket ban."""
    parsed = read("Esitada arvamus hiljemalt 15.09.2026.")

    assert parsed.kind == ActionKind.DO
    assert parsed.date_semantics == DateSemantics.DEADLINE
    assert parsed.target_date == dt.date(2026, 9, 15)


def test_a_day_beside_a_do_verb_is_still_refused_without_deadline_wording():
    """Unchanged, and the reason the yearless inference does not widen DO.

    A resolved year makes the *date* readable; it says nothing about whether
    that day is a deadline or a plan, and only one of those may be reported as
    missed.
    """
    assert reasons("Esitada arvamus 15.09") == (ReviewReason.DO_DATE_WITHOUT_DEADLINE_WORDING,)
