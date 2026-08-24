"""Parser 1.2 — the grammar it learned, and everything it now refuses.

1.0 read a waiting verb, a monitoring verb and a stated deadline. Measured
against the 159 maintained instructions the register actually holds, that
produced 54 conversions, and reading them showed the reading was often right by
luck: the parser found the one date in the sentence and used it, whether or not
that date belonged to the clause the instruction was in.

So 1.2 does two things at once, and they are the same thing. It **sees more** —
year-less dates, inflected months, a day written with a month name, and the
review instruction the register writes constantly — and, because it sees more,
it **converts less**: 37 rather than 54. A sentence carrying a date the parser
could not previously detect looked dateless and was converted with no date at
all, which is not a smaller claim than a wrong date. It is a different one.

Every sentence below is invented. None is a Koda register row.
"""

from __future__ import annotations

import datetime as dt

import pytest

from app.legacy_import.register_next_actions import (
    ALL_MONTH_FORMS,
    MONTH_STEMS,
    MONTH_SUFFIXES,
    RELATIVE_PRONOUNS,
    REVIEW_FORMS,
    ReviewReason,
    Verdict,
    parse_instruction,
)
from app.workflow.enums import ActionKind, DatePrecision, DateSemantics


def reasons(text: str) -> tuple[str, ...]:
    return parse_instruction(text).review_reasons


# ---------------------------------------------------------------------------
# A. `vaata üle` — the grammar 1.2 learned
# ---------------------------------------------------------------------------


def test_a_review_instruction_with_an_exact_day_is_a_monitor_on_that_day():
    parsed = parse_instruction("Vaata 26.10.2026 üle, kas eelnõu on edasi liikunud.")

    assert parsed.verdict == Verdict.UNDERSTOOD
    assert parsed.kind == ActionKind.MONITOR
    assert parsed.date_semantics == DateSemantics.REVIEW_ON
    assert parsed.target_date == dt.date(2026, 10, 26)


def test_the_particle_may_sit_after_the_thing_being_reviewed():
    """`vaata 13.11 üle` is how the register writes it more often than not.

    Requiring the verb and the particle to be adjacent would have missed exactly
    the sentences that carry a date.
    """
    parsed = parse_instruction("Vaata teema 28.09.2026 uuesti üle.")

    assert parsed.kind == ActionKind.MONITOR
    assert parsed.target_date == dt.date(2026, 9, 28)


def test_a_full_stop_inside_the_date_is_not_a_clause_boundary():
    """The one that made the whole class invisible.

    A generic punctuation break put the end of `Vaata`'s clause inside
    `01.12.2026`, so the particle looked like it belonged to the next sentence
    and the instruction read as no instruction at all.
    """
    parsed = parse_instruction("Vaata 01.12.2026 üle.")

    assert parsed.verdict == Verdict.UNDERSTOOD
    assert parsed.target_date == dt.date(2026, 12, 1)


def test_a_past_tense_look_is_not_an_instruction():
    """No `vaata\\w*` stem. `vaatasin` reports what somebody did in August."""
    assert reasons("viimati vaatasin 01.08.2026") == (ReviewReason.NO_KIND,)


def test_the_verb_alone_without_the_particle_instructs_nobody():
    assert reasons("vaata 01.12.2026") == (ReviewReason.NO_KIND,)


def test_the_particle_in_a_different_clause_does_not_make_an_instruction():
    assert reasons("vaatan seda, üle jäi veel üks küsimus") == (ReviewReason.NO_KIND,)


@pytest.mark.parametrize("form", REVIEW_FORMS)
def test_every_reviewed_review_form_is_recognised(form):
    """The allowlist is enumerable, which is what makes it reviewable."""
    parsed = parse_instruction(f"{form} 26.10.2026 üle")

    assert parsed.verdict == Verdict.UNDERSTOOD
    assert parsed.kind == ActionKind.MONITOR


# ---------------------------------------------------------------------------
# B. WAIT + review — two actionable timings
# ---------------------------------------------------------------------------


def test_a_wait_beside_a_review_is_refused_rather_than_flattened():
    """1.0's worst reading, and the commonest shape in the corpus.

    Eleven maintained rows say this. Each produced a dateless WAIT, and the
    review instruction — the half with the date on it — was discarded without a
    trace.
    """
    assert reasons("ootan 2. lugemist riigikogus, vaata üle septembris") == (
        ReviewReason.WAIT_AND_REVIEW,
    )


def test_the_review_clause_date_is_not_borrowed_by_the_wait():
    """The sharpest form of the same defect: a date from the wrong clause.

    1.0 read this as "waiting, expected around August 2026". The August belongs
    to the review, and what is being waited for has no stated timing at all.
    """
    assert reasons("ootan 2. kooskõlastusringi, Vaata üle augustis 2026") == (
        ReviewReason.WAIT_AND_REVIEW,
    )


def test_order_does_not_change_the_answer():
    assert reasons("vaata üle septembris, ootan 2. lugemist") == (ReviewReason.WAIT_AND_REVIEW,)


# ---------------------------------------------------------------------------
# C. Somebody else's date
# ---------------------------------------------------------------------------


def test_an_entry_into_force_date_is_still_refused():
    """1.1's fix, preserved. An act commencing is not the awaited event."""
    assert reasons("ootan RT linki, jõustub 1.01.2028") == (
        ReviewReason.DATE_GOVERNED_BY_ANOTHER_CLAUSE,
    )


def test_a_date_in_a_relative_clause_describes_the_thing_not_the_waiting():
    """*ootan X, mille arutelu 27.07.2027* — sometimes the same week, sometimes a year.

    The sentence does not say which, and a parser that used the only date it
    could find would be right by luck.
    """
    assert reasons("ootan ELAKi seisukohta, mille arutelu 27.07.2027") == (
        ReviewReason.DATE_IN_RELATIVE_CLAUSE,
    )


def test_a_relative_pronoun_after_the_date_captures_nothing():
    """The guard must not swallow a date the instruction genuinely owns."""
    parsed = parse_instruction(
        "Ootan eelnõud 2027. aasta 2. kvartalis. Ministeerium pole teada andnud, "
        "millal eelnõu tuleb."
    )

    assert parsed.verdict == Verdict.UNDERSTOOD
    assert parsed.target_date == dt.date(2027, 4, 1)


def test_an_instruction_verb_takes_the_sentence_back_from_a_relative_clause():
    parsed = parse_instruction("Saabus kiri, mille kohta ootan vastust 2027. aasta 1. kvartalis")

    assert parsed.verdict == Verdict.UNDERSTOOD
    assert parsed.target_date == dt.date(2027, 1, 1)


@pytest.mark.parametrize("pronoun", RELATIVE_PRONOUNS)
def test_every_reviewed_relative_pronoun_governs(pronoun):
    assert reasons(f"ootan seisukohta, {pronoun} arutelu 27.07.2027") == (
        ReviewReason.DATE_IN_RELATIVE_CLAUSE,
    )


# ---------------------------------------------------------------------------
# D. Inflected months — detection is not permission
# ---------------------------------------------------------------------------


def test_the_month_vocabulary_covers_every_month_in_every_reviewed_case():
    """Enumerable, so a missing form is a failing test rather than a silent gap."""
    assert len(MONTH_STEMS) == 12
    for stem in MONTH_STEMS:
        for suffix in MONTH_SUFFIXES:
            assert f"{stem}{suffix}" in ALL_MONTH_FORMS


def test_a_translative_month_with_a_year_is_read():
    parsed = parse_instruction("Ootan direktiivi ülevõtmist 22. jaanuariks 2029")

    assert parsed.verdict == Verdict.UNDERSTOOD
    assert parsed.target_date == dt.date(2029, 1, 22)
    assert parsed.date_precision == DatePrecision.EXACT


def test_an_adessive_month_with_a_year_is_read():
    parsed = parse_instruction("Ootan muudatusi 1. jaanuaril 2028")

    assert parsed.target_date == dt.date(2028, 1, 1)


def test_an_inflected_month_without_a_year_is_detected_and_refused():
    """Detection is what makes the refusal possible.

    Before 1.2 this sentence looked dateless and became a WAIT with no date —
    which reads as "no timing was written" when a timing plainly was.
    """
    assert reasons("Ootan ELi ideed hiljemalt oktoobriks") == (ReviewReason.DATE_WITHOUT_YEAR,)


# ---------------------------------------------------------------------------
# E. Bare dates
# ---------------------------------------------------------------------------


def test_a_bare_day_and_month_is_refused():
    """`13.11` means "the next 13 November", which is a fact about the reader."""
    assert reasons("Vaata 13.11 üle, mis on teemast saanud") == (ReviewReason.DATE_WITHOUT_YEAR,)


def test_a_bare_month_is_refused():
    assert reasons("ootan uut versiooni oktoobris") == (ReviewReason.DATE_WITHOUT_YEAR,)


def test_a_dated_sentence_is_not_mistaken_for_a_bare_one():
    """The lookahead has to survive the form it exists to exclude."""
    parsed = parse_instruction("ootan RT link 09.07.2026")

    assert parsed.verdict == Verdict.UNDERSTOOD
    assert parsed.target_date == dt.date(2026, 7, 9)


def test_an_ordinal_is_not_a_bare_date():
    """`2. lugemist` is a reading of a bill, not the second of some month."""
    parsed = parse_instruction("ootan 1. lugemise lõpetamist")

    assert parsed.verdict == Verdict.UNDERSTOOD
    assert parsed.target_date is None


def test_a_month_inside_a_full_phrase_is_read_not_refused():
    """`2026. aasta oktoobris` is dated; only the bare form is not."""
    parsed = parse_instruction("Ootan eelnõud 2026. aasta oktoobris")

    assert parsed.verdict == Verdict.UNDERSTOOD
    assert parsed.target_date == dt.date(2026, 10, 1)
    assert parsed.date_precision == DatePrecision.MONTH


def test_an_impossible_bare_day_is_unreadable_rather_than_undated():
    assert reasons("vaata 32.13 üle") == (ReviewReason.UNREADABLE_DATE,)


# ---------------------------------------------------------------------------
# F/G. Several dates, and dates that disagree
# ---------------------------------------------------------------------------


def test_two_dated_instructions_are_refused_never_first_wins():
    assert reasons("Vaata 01.12.2026 üle, kas on uusi arenguid. Viimati vaatasin 01.08.2026.") == (
        ReviewReason.AMBIGUOUS_DATE,
    )


def test_a_second_actionable_timing_is_not_silently_dropped():
    """1.0 took the quarter and never noticed the member-state deadline."""
    assert reasons(
        "Ootan eelnõud 2028. aasta 2. kvartalis. Liikmesriigid peavad direktiivi "
        "jõustama 22. jaanuariks 2029."
    ) == (ReviewReason.AMBIGUOUS_DATE,)


def test_the_same_date_written_twice_is_one_date():
    """Ambiguity is about disagreement, not about repetition."""
    parsed = parse_instruction("Ootan eelnõud 2027. aasta 2. kvartalis, 2027. aasta 2. kvartalis")

    assert parsed.verdict == Verdict.UNDERSTOOD
    assert parsed.target_date == dt.date(2027, 4, 1)


# ---------------------------------------------------------------------------
# The readings that survive, and the corpus shape they produce
# ---------------------------------------------------------------------------


def test_a_dateless_wait_is_still_understood():
    """The largest AUTO family, and it is honest.

    "ootan valitsusele saatmist" states a kind and no timing. Recording that as
    a WAIT with no date says exactly what the register said; inventing a review
    date to fill the gap would not.
    """
    parsed = parse_instruction("ootan valitsusele saatmist")

    assert parsed.verdict == Verdict.UNDERSTOOD
    assert parsed.kind == ActionKind.WAIT
    assert parsed.target_date is None


def test_an_entry_into_force_clause_without_a_date_leaves_the_wait_alone():
    parsed = parse_instruction("ootan valitsusele saatmist, muudatused jõustuvad üldises korras")

    assert parsed.verdict == Verdict.UNDERSTOOD
    assert parsed.kind == ActionKind.WAIT


def test_continuation_wording_is_not_an_ordinary_next_action():
    """Continuation is supersession, and it is decided elsewhere.

    `detect_continuation` owns that reading (app/legacy_import/register_semantics.py).
    This parser must not manufacture a task from it, and it does not: no kind
    verb appears, so the sentence names no instruction.
    """
    assert reasons("Jätkub teema 2026_70 all") == (ReviewReason.NO_KIND,)


def test_the_source_text_is_returned_exactly_as_it_was_given():
    """The parser adds interpretation. It never rewrites evidence."""
    source = "  ootan   valitsusele saatmist, muudatused jõustuvad üldises korras  "
    parsed = parse_instruction(source)

    assert parsed.source_text == source.strip()
    assert "  " in parsed.source_text
