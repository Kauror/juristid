"""Parser 2.1 — one more review verb, and a comma inside one instruction.

2.0 settled the hard questions: which year a bare *15.09* means, which clause
owns a date, and when a wait and a review of it are one instruction. 2.1 adds
nothing to any of that. It closes two small holes the 1 September workbook made
visible, and both are the same *kind* of hole — a sentence that states an
instruction and a date perfectly clearly, and that the parser could not see
because of how it was spelled.

**``küsi`` is a review verb.** *küsi 05.10 üle* is the register asking a
ministry again on a named day. That is a review of a wait, not work Koda owes
anybody, and it takes the particle exactly like *vaata* does — which is what
keeps bare *küsi*, the register's word for asking about the substance of a file,
out of the work queue.

**A comma between the verb and the particle is a typo when only a date sits
there.** *Vaata, 10.11 üle* is one instruction somebody mistyped, not two
clauses. Punctuation decides clause ownership everywhere else in this module and
still does: the exception admits a date and separators and nothing lexical at
all, because a real second clause has words in it.

What 2.1 deliberately does **not** do is reopen
``test_two_actionable_dates_beside_the_pair_are_still_refused``. A review date
beside an external milestone stays refused; that is a reviewed decision in 2.0
and not a gap (docs/adr/0053).

Every sentence below is invented. None is a Koda register row.
"""

from __future__ import annotations

import datetime as dt

from app.legacy_import.register_next_actions import (
    REGISTER_NEXT_ACTION_PARSER_VERSION,
    ParseContext,
    ReviewReason,
    Verdict,
    parse_instruction,
)
from app.workflow.enums import ActionKind, DatePrecision, DateSemantics

SNAPSHOT_DATE = dt.date(2026, 9, 1)
SHEET_2026 = ParseContext(sheet_year=2026, snapshot_date=SNAPSHOT_DATE)
SHEET_2025 = ParseContext(sheet_year=2025, snapshot_date=SNAPSHOT_DATE)


def read(text: str, context: ParseContext = SHEET_2026):
    return parse_instruction(text, context=context)


def reasons(text: str, context: ParseContext = SHEET_2026) -> tuple[str, ...]:
    return read(text, context).review_reasons


def test_the_version_moved():
    """A new word form changes what a sentence means, so the identity moves."""
    assert REGISTER_NEXT_ACTION_PARSER_VERSION == "2.1"


# ---------------------------------------------------------------------------
# küsi
# ---------------------------------------------------------------------------


def test_kysi_with_the_particle_is_a_review_on_that_day():
    reading = read("küsi 05.10 üle, kas menetlus on edenenud")
    assert reading.verdict == Verdict.UNDERSTOOD
    assert reading.kind == ActionKind.MONITOR
    assert reading.date_semantics == DateSemantics.REVIEW_ON
    assert reading.target_date == dt.date(2026, 10, 5)
    assert reading.date_precision == DatePrecision.EXACT


def test_kysi_takes_the_sheet_year_like_every_other_review():
    """2.1 adds a verb, not a second way of resolving a year."""
    assert read("küsi 05.10 üle").target_date == dt.date(2026, 10, 5)
    assert reasons("küsi 05.10 üle", SHEET_2025) == (ReviewReason.DATE_WITHOUT_YEAR,)


def test_kysi_without_the_particle_is_not_an_instruction_to_review():
    """Bare *küsi* is "ask" — about the file, not about looking at it again."""
    assert reasons("küsi ministeeriumilt 05.10 selgitust") == (ReviewReason.NO_KIND,)


def test_kysida_and_kysime_read_the_same_way():
    for form in ("küsida 12.11 üle", "küsime 12.11 üle"):
        reading = read(form)
        assert reading.verdict == Verdict.UNDERSTOOD, form
        assert reading.target_date == dt.date(2026, 11, 12), form


def test_a_wait_and_a_kysi_review_is_still_one_instruction():
    """The 2.0 pairing rule takes the new verb without being widened."""
    reading = read("ootan ministeeriumi vastust, küsi 20.09 üle")
    assert reading.kind == ActionKind.WAIT
    assert reading.date_semantics == DateSemantics.REVIEW_ON
    assert reading.target_date == dt.date(2026, 9, 20)


# ---------------------------------------------------------------------------
# The comma inside one instruction
# ---------------------------------------------------------------------------


def test_a_comma_before_the_date_does_not_split_the_instruction():
    reading = read("Vaata, 10.11 üle, kas akt on avaldatud")
    assert reading.verdict == Verdict.UNDERSTOOD
    assert reading.kind == ActionKind.MONITOR
    assert reading.target_date == dt.date(2026, 11, 10)
    assert reading.date_precision == DatePrecision.EXACT


def test_the_exception_holds_for_a_fully_written_date_too():
    assert read("Vaata, 10.11.2026 üle, kas akt on avaldatud").target_date == dt.date(2026, 11, 10)


def test_a_word_between_the_verb_and_the_particle_is_still_a_clause_break():
    """The whole protection: a real second clause has words in it.

    Without this the exception would assemble one instruction out of two
    sentences that merely happen to sit beside each other.
    """
    assert reasons("Vaata, kas eelnõu liigub, ja anna üle 10.11") == (ReviewReason.NO_KIND,)


def test_two_dates_between_the_verb_and_the_particle_are_not_a_typo():
    assert reasons("Vaata, 10.11 ja 12.11 üle") == (ReviewReason.NO_KIND,)


def test_a_full_stop_still_separates_two_sentences():
    """Only the comma is forgiven, and only over a date."""
    assert reasons("Vaata. 10.11 anti üle") == (ReviewReason.NO_KIND,)


# ---------------------------------------------------------------------------
# What 2.1 left exactly where it was
# ---------------------------------------------------------------------------


def test_a_review_date_beside_an_external_milestone_is_still_refused():
    """2.0's reviewed decision, restated here so 2.1 cannot quietly undo it."""
    assert reasons("ootan komisjoni edasiliikumist, vaata üle 15.10, istung toimub 05.10.2026") == (
        ReviewReason.AMBIGUOUS_DATE,
    )


def test_two_competing_periods_are_still_refused():
    assert reasons("Ootan eelnõud oktoobris. Eelnõu valmib 2026. aasta 2. poolaastal.") == (
        ReviewReason.AMBIGUOUS_DATE,
    )


def test_an_entry_into_force_date_is_still_not_an_instruction():
    assert reasons("ootan RT linki, jõustub 1.01.2028") == (
        ReviewReason.DATE_GOVERNED_BY_ANOTHER_CLAUSE,
    )


def test_a_past_date_is_still_not_a_target():
    assert reasons("Kooskõlastusringi tähtaeg oli 26.08") == (ReviewReason.NO_KIND,)


def test_a_stale_review_date_stays_in_its_own_year():
    """Never rolled forward: a passed 2026 date is a passed 2026 date."""
    reading = read("vaata 15.07 üle")
    assert reading.target_date == dt.date(2026, 7, 15)
    assert reading.is_stale(SNAPSHOT_DATE)
