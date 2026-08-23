"""The deterministic reading of one ``JÄRGMISEKS`` sentence.

No database. Every case here is a string in and a verdict out, which is the
point: the rules have to be defensible one sentence at a time, and a rule nobody
can write a test for does not belong in the allowlist.

Every sentence below is invented. None is a Koda register row.
"""

from __future__ import annotations

import datetime as dt

import pytest

from app.legacy_import.register_next_actions import (
    REGISTER_NEXT_ACTION_PARSER_VERSION,
    ReviewReason,
    Verdict,
    parse_instruction,
)
from app.workflow.enums import ActionKind, DatePrecision, DateSemantics

# -- the required example ---------------------------------------------------


def test_a_quarter_expectation_is_a_wait_and_not_a_deadline():
    """The case the whole feature was specified around.

    A waiting verb and a period. Every part of the reading matters: WAIT rather
    than DO, so it can never be reported as late; EXPECTED_AROUND rather than
    DEADLINE, because it is a guess about a ministry's timetable; the 1 April
    anchor at QUARTER precision, so the UI writes "II kvartal 2027" and never a
    day nobody named.
    """
    source = "Ootan eelnõud 2027. aasta 2. kvartalis"
    parsed = parse_instruction(source)

    assert parsed.verdict == Verdict.UNDERSTOOD
    assert parsed.kind == ActionKind.WAIT
    assert parsed.date_semantics == DateSemantics.EXPECTED_AROUND
    assert parsed.target_date == dt.date(2027, 4, 1)
    assert parsed.date_precision == DatePrecision.QUARTER
    assert parsed.source_text == source
    assert parsed.parser_version == REGISTER_NEXT_ACTION_PARSER_VERSION


@pytest.mark.parametrize(
    "source",
    [
        "Ootan eelnõud 2027. aasta 2. kvartalis",
        "Ootan eelnõud 2027. aasta 2. kvartal",
        "Ootan eelnõud II kvartalis 2027",
        "Ootan eelnõud II kvartal 2027",
        "Ootan eelnõud 2. kvartalis 2027",
        "Ootan eelnõud 2. kvartal 2027",
    ],
)
def test_every_reviewed_quarter_form_reaches_the_same_anchor(source):
    """Six spellings the register actually uses, one stored value.

    Roman and arabic, year first and year last, nominative and inessive. If two
    of them normalised differently the same expectation would sort into two
    different quarters.
    """
    parsed = parse_instruction(source)
    assert parsed.verdict == Verdict.UNDERSTOOD
    assert parsed.target_date == dt.date(2027, 4, 1)
    assert parsed.date_precision == DatePrecision.QUARTER


# -- kinds ------------------------------------------------------------------


def test_a_stated_deadline_is_a_deadline():
    parsed = parse_instruction("Esitada arvamus hiljemalt 15.09.2026")
    assert parsed.verdict == Verdict.UNDERSTOOD
    assert parsed.kind == ActionKind.DO
    assert parsed.date_semantics == DateSemantics.DEADLINE
    assert parsed.target_date == dt.date(2026, 9, 15)
    assert parsed.date_precision == DatePrecision.EXACT


def test_the_translative_ending_states_a_deadline_too():
    """``15.09.2026-ks`` is the third way the register writes "by"."""
    parsed = parse_instruction("Esitada arvamus 15.09.2026-ks")
    assert parsed.kind == ActionKind.DO
    assert parsed.date_semantics == DateSemantics.DEADLINE
    assert parsed.target_date == dt.date(2026, 9, 15)


def test_a_monitoring_date_says_when_to_look_again():
    parsed = parse_instruction("Jälgida eelnõu detsembris 2026")
    assert parsed.verdict == Verdict.UNDERSTOOD
    assert parsed.kind == ActionKind.MONITOR
    assert parsed.date_semantics == DateSemantics.REVIEW_ON
    assert parsed.target_date == dt.date(2026, 12, 1)
    assert parsed.date_precision == DatePrecision.MONTH


def test_two_kinds_in_one_sentence_are_not_resolved():
    """A sentence that waits *and* acts states no single next step.

    Choosing either one would put a decision in the work queue that the
    department never made, and choosing by word order would make the answer
    depend on how somebody happened to phrase it.
    """
    parsed = parse_instruction("Ootame ministeeriumi vastust ja saadame oma seisukoha")
    assert parsed.verdict == Verdict.REVIEW_REQUIRED
    assert ReviewReason.AMBIGUOUS_KIND in parsed.review_reasons


def test_a_sentence_with_no_reviewed_verb_is_not_read():
    parsed = parse_instruction("Menetlus jätkub Riigikogus")
    assert parsed.verdict == Verdict.REVIEW_REQUIRED
    assert parsed.review_reasons == (ReviewReason.NO_KIND,)


def test_a_wait_with_no_date_is_still_understood():
    """ "No idea when" is an honest state, and the model supports it.

    Nothing invents a review date to fill the gap: the action carries the kind
    the sentence stated and no date at all.
    """
    parsed = parse_instruction("Ootan vastust")
    assert parsed.verdict == Verdict.UNDERSTOOD
    assert parsed.kind == ActionKind.WAIT
    assert parsed.date_semantics == DateSemantics.EXPECTED_AROUND
    assert parsed.target_date is None


def test_a_dateless_do_is_refused_rather_than_given_a_date():
    """The model would take a dateless DO. The parser will not invent one."""
    parsed = parse_instruction("Esitada arvamus")
    assert parsed.verdict == Verdict.REVIEW_REQUIRED
    assert ReviewReason.DO_WITHOUT_DATE in parsed.review_reasons


def test_a_day_beside_a_do_verb_is_not_assumed_to_be_a_deadline():
    """The distinction the whole ``DateSemantics`` split exists for.

    "Esitada arvamus 15.09.2026" is a deadline if the ministry set one and a
    plan if the lawyer chose it. Only one of those may be reported as missed,
    and the sentence does not say which.
    """
    parsed = parse_instruction("Esitada arvamus 15.09.2026")
    assert parsed.verdict == Verdict.REVIEW_REQUIRED
    assert ReviewReason.DO_DATE_WITHOUT_DEADLINE_WORDING in parsed.review_reasons


def test_a_do_verb_with_an_approximate_period_states_intended_timing():
    """Nobody writes "the deadline is some time in the second half"."""
    parsed = parse_instruction("Koostada seisukoht 2027. aasta 2. poolaastal")
    assert parsed.verdict == Verdict.UNDERSTOOD
    assert parsed.kind == ActionKind.DO
    assert parsed.date_semantics == DateSemantics.EXPECTED_AROUND
    assert parsed.target_date == dt.date(2027, 7, 1)
    assert parsed.date_precision == DatePrecision.HALF_YEAR


def test_deadline_wording_on_a_period_is_refused():
    """A deadline stored at quarter precision would go overdue on 2 April."""
    parsed = parse_instruction("Esitada arvamus tähtajaks II kvartalis 2027")
    assert parsed.verdict == Verdict.REVIEW_REQUIRED
    assert ReviewReason.APPROXIMATE_DEADLINE in parsed.review_reasons


def test_a_spent_deadline_does_not_make_a_later_expectation_into_one():
    """The marker has to precede the date it governs.

    "Tähtaeg möödus, ootan uut versiooni septembris" names a deadline that is
    already gone and a date that is not one. A rule that only asked whether the
    word appeared anywhere would turn the September expectation into an
    overdue-able deadline.
    """
    parsed = parse_instruction("Tähtaeg möödus, ootan uut versiooni septembris 2026")
    assert parsed.verdict == Verdict.UNDERSTOOD
    assert parsed.kind == ActionKind.WAIT
    assert parsed.date_semantics == DateSemantics.EXPECTED_AROUND


def test_a_hedge_is_not_a_waiting_instruction():
    """``oodatavasti`` is a guess about somebody else, not an instruction.

    The reason the vocabulary is a list of forms rather than a stem: ``oota\\w*``
    would swallow this one.
    """
    parsed = parse_instruction("Eelnõu jõuab oodatavasti Riigikokku detsembris 2026")
    assert parsed.verdict == Verdict.REVIEW_REQUIRED
    assert parsed.review_reasons == (ReviewReason.NO_KIND,)


# -- dates ------------------------------------------------------------------


def test_two_plausible_dates_are_never_narrowed_to_one():
    """Not the first, not the last, not the nearest."""
    parsed = parse_instruction("Ootan vastust septembris 2026 ja eelnõu 15.03.2027")
    assert parsed.verdict == Verdict.REVIEW_REQUIRED
    assert ReviewReason.AMBIGUOUS_DATE in parsed.review_reasons


def test_the_same_date_written_twice_is_one_date():
    """Repetition is not ambiguity."""
    parsed = parse_instruction("Ootan vastust 15.09.2026, vastus 15.09.2026")
    assert parsed.verdict == Verdict.UNDERSTOOD
    assert parsed.target_date == dt.date(2026, 9, 15)


@pytest.mark.parametrize(
    ("source", "expected", "precision"),
    [
        ("Ootan vastust 1.2.2027", dt.date(2027, 2, 1), DatePrecision.EXACT),
        ("Ootan vastust 01.02.2027", dt.date(2027, 2, 1), DatePrecision.EXACT),
        ("Ootan vastust 2027-02-01", dt.date(2027, 2, 1), DatePrecision.EXACT),
        ("Ootan vastust septembris 2026", dt.date(2026, 9, 1), DatePrecision.MONTH),
        ("Ootan vastust september 2026", dt.date(2026, 9, 1), DatePrecision.MONTH),
        ("Ootan vastust 2026. aasta septembris", dt.date(2026, 9, 1), DatePrecision.MONTH),
        ("Ootan vastust I poolaasta 2027", dt.date(2027, 1, 1), DatePrecision.HALF_YEAR),
        ("Ootan vastust 2. poolaastal 2027", dt.date(2027, 7, 1), DatePrecision.HALF_YEAR),
        ("Ootan vastust 2027. aastal", dt.date(2027, 1, 1), DatePrecision.YEAR),
    ],
)
def test_the_supported_date_shapes(source, expected, precision):
    parsed = parse_instruction(source)
    assert parsed.verdict == Verdict.UNDERSTOOD
    assert parsed.target_date == expected
    assert parsed.date_precision == precision


def test_a_year_needs_the_word_aasta():
    """A bare four-digit number beside a sentence is not a stated year.

    "2027" could be a reference, a document number or a quantity. Reading it as
    a target is precisely the inference this parser refuses.
    """
    parsed = parse_instruction("Ootan eelnõu 2027")
    assert parsed.verdict == Verdict.UNDERSTOOD
    assert parsed.target_date is None


@pytest.mark.parametrize(
    "source",
    [
        "Ootan eelnõu 5. kvartalis 2027",
        "Ootan eelnõu 3. poolaastal 2027",
        "Ootan eelnõu 31.02.2026",
        # The register writes periods in Roman numerals too — "II kvartalis",
        # "I poolaasta". A guard that only understood the Arabic spelling let
        # these through as though no period had been written at all, and the
        # sentence converted on its verb alone.
        "Ootan eelnõu V kvartalis 2027",
        "Ootan eelnõu III poolaastal 2027",
    ],
)
def test_a_date_that_cannot_exist_stops_the_reading(source):
    """A malformed date is a finding, not an absence.

    Treating "5. kvartal" as "no date mentioned" would convert the sentence on
    its verb alone and silently drop the thing the writer was pointing at.
    """
    parsed = parse_instruction(source)
    assert parsed.verdict == Verdict.REVIEW_REQUIRED
    assert ReviewReason.UNREADABLE_DATE in parsed.review_reasons


def test_a_period_phrase_is_one_date_and_not_two():
    """ "2027. aasta 2. kvartalis" contains its own year phrase.

    Without the containment rule the required example would be rejected as
    carrying two dates — a quarter and a year — which is the failure mode this
    guards.
    """
    parsed = parse_instruction("Ootan eelnõud 2027. aasta 2. kvartalis")
    assert parsed.verdict == Verdict.UNDERSTOOD
    assert parsed.date_precision == DatePrecision.QUARTER


# -- a date that belongs to another clause -----------------------------------


@pytest.mark.parametrize(
    "source",
    [
        "Ootan Riigi Teataja linki, jõustub 1.01.2029",
        "Vastu võetud, muudatused jõustuvad 01.11.2029, ootan linki",
        "Jälgin menetlust, määrus jõustub 2029. aasta 2. kvartalis",
        # Verb before the noun it commences. Estonian allows the inversion and
        # the clause is the same one, so adjacency alone would miss it.
        "Ootan linki, jõustuvad muudatused 01.11.2029",
    ],
)
def test_an_entry_into_force_date_is_not_the_awaited_events_timing(source):
    """When an act takes effect is not when the awaited thing arrives.

    A publication link is expected within weeks of adoption; the act it
    publishes takes effect years later. Storing the second as EXPECTED_AROUND
    would put a claim on the department's own record that the register never
    made — and it is the shape the register writes most often, because it
    records entry into force beside whatever else is happening.
    """
    parsed = parse_instruction(source)
    assert parsed.verdict == Verdict.REVIEW_REQUIRED
    assert ReviewReason.DATE_GOVERNED_BY_ANOTHER_CLAUSE in parsed.review_reasons
    assert parsed.target_date is None


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        # Entry into force with no date of its own. The quarter that follows is
        # separated from the verb by five words and belongs to the wait.
        ("Jõustub üldises korras. Ootan eelnõud 2029. aasta 2. kvartalis", dt.date(2029, 4, 1)),
        # The verb comes after the date it does not govern.
        ("Ootan eelnõud 2029. aasta 2. kvartalis, jõustub üldises korras", dt.date(2029, 4, 1)),
        # No punctuation at all, and the waiting verb has taken the sentence
        # over before the date arrives.
        (
            "Jõustub üldises korras ja ootan eelnõud 2029. aasta 2. kvartalis",
            dt.date(2029, 4, 1),
        ),
    ],
)
def test_entry_into_force_wording_alone_refuses_nothing(source, expected):
    """The test is what the verb governs, not whether it appears.

    A bare precedence test would refuse all of these, where the register simply
    noted the commencement rule before stating what it is waiting for. What ends
    the entry-into-force clause is punctuation, or the next instruction verb
    where the writer left the punctuation out.
    """
    parsed = parse_instruction(source)
    assert parsed.verdict == Verdict.UNDERSTOOD
    assert parsed.kind == ActionKind.WAIT
    assert parsed.target_date == expected


def test_a_dateless_entry_into_force_sentence_still_converts():
    """No date, nothing to misattribute.

    "muudatused jõustuvad üldises korras" names no day, so the wait is read
    exactly as it would be without the clause: honest, and dateless.
    """
    parsed = parse_instruction("Ootan valitsusele saatmist, muudatused jõustuvad üldises korras")
    assert parsed.verdict == Verdict.UNDERSTOOD
    assert parsed.kind == ActionKind.WAIT
    assert parsed.target_date is None


# -- staleness --------------------------------------------------------------


def test_an_approximate_period_is_not_stale_until_its_last_day_passes():
    """II poolaasta 2026 has not passed on 2 July 2026.

    Its stored anchor is 1 July, so an anchor comparison would say it had. The
    question is about the period's *end*.
    """
    parsed = parse_instruction("Ootan eelnõu 2026. aasta 2. poolaastal")
    assert parsed.target_date == dt.date(2026, 7, 1)
    assert parsed.is_stale(dt.date(2026, 7, 2)) is False
    assert parsed.is_stale(dt.date(2026, 12, 31)) is False
    assert parsed.is_stale(dt.date(2027, 1, 1)) is True


def test_an_exact_date_is_stale_the_day_after():
    parsed = parse_instruction("Esitada arvamus hiljemalt 15.09.2026")
    assert parsed.is_stale(dt.date(2026, 9, 15)) is False
    assert parsed.is_stale(dt.date(2026, 9, 16)) is True


def test_a_dateless_reading_is_never_stale():
    parsed = parse_instruction("Ootan vastust")
    assert parsed.is_stale(dt.date(2099, 1, 1)) is False


# -- the empty cell ---------------------------------------------------------


@pytest.mark.parametrize("source", ["", "   ", None])
def test_an_empty_cell_is_its_own_verdict(source):
    """Not a rejection. There was nothing to read."""
    parsed = parse_instruction(source)
    assert parsed.verdict == Verdict.EMPTY
    assert parsed.review_reasons == ()


def test_the_source_sentence_is_preserved_exactly():
    """Matching happens on a copy. What travels onward is the original.

    Only surrounding whitespace is removed — the wording, its spelling and its
    punctuation are the register's and stay the register's.
    """
    source = "  Ootan eelnõud 2027. aasta 2. kvartalis  "
    parsed = parse_instruction(source)
    assert parsed.source_text == source.strip()
    assert parsed.source_text == "Ootan eelnõud 2027. aasta 2. kvartalis"
