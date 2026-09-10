"""What the reader actually gets right, measured rather than asserted.

The rest of the test suite asks whether a rule fires. This file asks the
product question: **on a representative envelope, what lands in the form?** The
answers are counted three ways — correct, missed, wrong — and only one of them
is a failure (assisted-intake brief §28).

**A wrong autofill is materially worse than a missed one**, and the assertions
are asymmetric to match. `test_nothing_is_ever_filled_wrongly` is absolute: not
one field, on any case in the corpus, may be filled with something a lawyer
would not have entered. The recall assertions are floors that a change is
allowed to move up and must not move down silently — and if a change trades two
misses for one wrong answer, the first test fails and the trade is refused.

The floors are deliberately *below* the corpus's current score. A number pinned
to exactly today's result is a test that fails on every improvement, which is
how a floor becomes something people delete.
"""

from __future__ import annotations

import pytest

from app.matters.intake_suggestions.evaluation import (
    SCORED_FIELDS,
    Scorecard,
    describe,
    evaluate,
    resolve,
)
from app.matters.intake_suggestions.resolvers import (
    load_organisation_catalogue,
    load_policy_areas,
)
from app.matters.intake_suggestions.types import SuggestedField
from app.organisations.models import Organisation, OrganisationType
from tests.intake_corpus import CASES

pytestmark = pytest.mark.django_db


@pytest.fixture
def catalogue(db):
    """The two organisations the corpus names, and the governed vocabulary.

    Created here rather than in a factory so that the corpus's «sender» values
    are literal names a reader can check against the documents. `Näidisamet`
    exists precisely so that the two-organisation case has something wrong to
    choose.
    """
    Organisation.objects.create(
        name="Näidisministeerium", organisation_type=OrganisationType.MINISTRY
    )
    Organisation.objects.create(name="Näidisamet", organisation_type=OrganisationType.AUTHORITY)
    return load_organisation_catalogue()


@pytest.fixture
def scored(catalogue):
    """Every case in the corpus, run through the real `Uus teema` decision."""
    policy_areas = load_policy_areas()
    organisation_ids = {name: pk for pk, name in Organisation.objects.values_list("id", "name")}
    area_ids = {key: area.pk for key, area in policy_areas.items()}

    card = Scorecard()
    expectations = {}
    for expectation, analysis_input in CASES:
        expectations[expectation.name] = expectation
        resolved = _with_ids(expectation, organisation_ids, area_ids)
        evaluate(
            analysis_input,
            resolved,
            organisations=catalogue,
            policy_areas=policy_areas,
            scorecard=card,
        )
    return card, expectations


def _with_ids(expectation, organisation_ids, area_ids):
    """The corpus's names as the primary keys a control carries.

    `resolve` is in the product module because the same translation is needed
    by the management command; doing it here rather than in the corpus keeps a
    file of invented documents free of any database.
    """
    from dataclasses import replace

    sender = resolve((expectation.sender,) if expectation.sender else (), organisation_ids)
    areas = resolve(expectation.policy_areas, area_ids)
    return replace(
        expectation,
        sender=sender[0] if sender else None,
        policy_areas=areas,
    )


# ---------------------------------------------------------------------------
# Precision, which is not negotiable
# ---------------------------------------------------------------------------


def test_nothing_is_ever_filled_wrongly(scored):
    """The assertion the whole confidence contract exists to satisfy.

    A wrong value on a form is worse than an empty one by a wide margin: an
    empty field is one somebody fills, and a plausible wrong one is a fact that
    gets saved. HIGH may fill, MEDIUM may only suggest, and a conflict fills
    nothing — and this is the only test that can tell whether those three rules
    together actually hold on documents rather than on examples chosen to prove
    them.
    """
    card, expectations = scored
    failures = [describe(outcome, expectations[outcome.case]) for outcome in card.wrong]
    assert not failures, "\n".join(failures)


def test_a_conflict_fills_nothing_even_when_both_sides_are_strong(scored):
    """Two explicit deadlines, two formal headings: the person chooses.

    Named separately from the precision test above because it is the case most
    likely to be broken by a change that *improves* recall — promoting on
    agreement, widening a cue — and the failure would then read as one more
    wrong answer rather than as the rule it broke.
    """
    card, _ = scored
    outcomes = {
        (outcome.case, outcome.field): outcome
        for outcome in card.outcomes
        if outcome.case in ("vastuolulised-tahtajad", "vastuolulised-pealkirjad")
    }
    assert outcomes[("vastuolulised-tahtajad", SuggestedField.RESPONSE_DEADLINE)].filled == ()
    assert outcomes[("vastuolulised-pealkirjad", SuggestedField.TITLE)].filled == ()


def test_an_unreadable_file_fills_nothing_and_raises_nothing(scored):
    """A parser failure is a quiet outcome, not an exception and not a guess."""
    card, _ = scored
    filled = [
        outcome for outcome in card.outcomes if outcome.case == "loetamatu-fail" and outcome.filled
    ]
    assert not filled


def test_an_informal_note_fills_nothing(scored):
    """The corpus needs a case whose right answer is an empty form.

    Without one, a change that raised every confidence would score perfectly.
    """
    card, _ = scored
    filled = [
        outcome
        for outcome in card.outcomes
        if outcome.case == "mitteametlik-kiri" and outcome.filled
    ]
    assert not filled


# ---------------------------------------------------------------------------
# Recall, as floors
# ---------------------------------------------------------------------------

#: The least each field may get right across the corpus. Below today's measured
#: result on purpose: a floor pinned to the exact current number fails on every
#: improvement, and a test that fails on improvements is a test people delete.
RECALL_FLOOR: dict[str, int] = {
    SuggestedField.TITLE: 5,
    SuggestedField.SOURCE_ORGANISATIONS: 6,
    SuggestedField.RESPONSE_DEADLINE: 5,
    SuggestedField.TRACK: 5,
    SuggestedField.POLICY_AREAS: 5,
}


@pytest.mark.parametrize("field_name", SCORED_FIELDS)
def test_each_field_still_fills_itself_often_enough(scored, field_name):
    card, _ = scored
    counts = card.by_field(field_name)
    assert counts["correct"] >= RECALL_FLOOR[field_name], (
        f"{field_name}: {counts['correct']} correct, floor is {RECALL_FLOOR[field_name]}\n"
        + card.row(field_name)
    )


def test_the_corpus_covers_the_shapes_the_brief_names(scored):
    """A floor over a corpus is only as good as the corpus.

    Two of these — a conflict and a file nothing can read — are the cases a
    scorecard is most tempted to drop, because they can only ever score zero
    correct autofills.
    """
    card, _ = scored
    cases = {outcome.case for outcome in card.outcomes}
    for wanted in (
        "ministeeriumi-kaaskiri",
        "eelnou-ilma-kaaskirjata",
        "seletuskiri",
        "kiri-ja-manus",
        "mitu-kuupaeva",
        "kaks-asutust",
        "vastuolulised-tahtajad",
        "pikk-lisa",
        "eli-algatus",
        "loetamatu-fail",
    ):
        assert wanted in cases, wanted


# ---------------------------------------------------------------------------
# The individual defects this round fixed, pinned
# ---------------------------------------------------------------------------


def test_a_long_annex_does_not_decide_the_valdkond(scored):
    """§16 — the false positive this round was written to remove.

    A covering letter about packaging waste beside a twelve-times-repeated
    procurement annex. Before this round the annex's vocabulary counted exactly
    as much as the letter's, and «riigihanked» outscored «keskkond» on an
    envelope no lawyer would have filed under procurement.
    """
    card, _ = scored
    outcome = next(
        outcome
        for outcome in card.outcomes
        if outcome.case == "pikk-lisa" and outcome.field == SuggestedField.POLICY_AREAS
    )
    assert outcome.verdict == "correct", outcome


def test_an_explanatory_memorandum_does_not_fill_the_deadline(scored):
    """§14 — a memorandum quotes the letter's deadline at best."""
    card, _ = scored
    outcome = next(
        outcome
        for outcome in card.outcomes
        if outcome.case == "seletuskiri" and outcome.field == SuggestedField.RESPONSE_DEADLINE
    )
    assert outcome.filled == ()


def test_the_dates_that_are_not_deadlines_are_not_offered_as_one(scored):
    """§14 — five dates in one letter, and only one of them is the answer."""
    card, _ = scored
    outcome = next(
        outcome
        for outcome in card.outcomes
        if outcome.case == "mitu-kuupaeva" and outcome.field == SuggestedField.RESPONSE_DEADLINE
    )
    assert outcome.verdict == "correct", outcome


def test_an_organisation_merely_named_is_not_the_saatja(scored):
    """§13 — the letterhead sends the letter; a body mention does not."""
    card, _ = scored
    outcome = next(
        outcome
        for outcome in card.outcomes
        if outcome.case == "kaks-asutust" and outcome.field == SuggestedField.SOURCE_ORGANISATIONS
    )
    assert outcome.verdict == "correct", outcome


def test_the_eu_case_is_decided_by_process_not_by_instrument(scored):
    """§15 — Track answers *how this arrived*, not *what kind of act it is*."""
    card, _ = scored
    outcome = next(
        outcome
        for outcome in card.outcomes
        if outcome.case == "eli-algatus" and outcome.field == SuggestedField.TRACK
    )
    assert outcome.verdict == "correct", outcome


def test_the_scorecard_prints_something_a_person_can_read(scored):
    """The report itself is a deliverable; an unreadable one is not used."""
    card, _ = scored
    for field_name in SCORED_FIELDS:
        row = card.row(field_name)
        assert field_name in row
        assert "VALE" in row
