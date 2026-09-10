"""Scoring the reader against documents whose right answers are written down.

A rule vocabulary improves on evidence or it improves on anecdote, and the
difference is a corpus. This module is the scoring half: given an envelope, the
catalogues, and what a lawyer would have entered, it says for each field
whether the reader **autofilled it correctly, missed it, or filled it wrongly**.

**The three outcomes are not equally bad and the module refuses to average
them.** A missed autofill costs a person ten seconds of typing on a field they
were going to look at anyway. A *wrong* autofill puts a plausible false value
into a record, and the whole design of this feature — HIGH may fill, MEDIUM may
only suggest, a conflict fills nothing — exists to make the second rare at the
cost of the first (assisted-intake brief §28). So `Scorecard` reports the three
separately and computes precision before recall; there is no single number to
optimise, on purpose.

What is scored is **what would actually be written into a control**, not what
the analyser thought. That means `prefill_initial` with the `Uus teema` rules,
including the title branch, so a change to the confidence contract shows up
here as a moved number rather than as a passing unit test about candidates
nobody would have been offered.

The corpus itself lives with the tests (`tests/intake_corpus.py`), because it
is a set of invented documents rather than a piece of the product, and because
it must be free to grow without a migration or a release. This module never
imports it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.matters.intake_suggestions.analysis import CurrentValues, analyse
from app.matters.intake_suggestions.input import AnalysisInput
from app.matters.intake_suggestions.prefill import prefill_initial
from app.matters.intake_suggestions.resolvers import OrganisationCatalogue
from app.matters.intake_suggestions.types import IntakeAnalysis, SuggestedField
from app.taxonomy.models import PolicyArea

#: The five fields `Uus teema` can be filled in from a document. Scored in the
#: order the form asks them, so a scorecard reads down the page.
SCORED_FIELDS: tuple[str, ...] = (
    SuggestedField.TITLE,
    SuggestedField.SOURCE_ORGANISATIONS,
    SuggestedField.RESPONSE_DEADLINE,
    SuggestedField.TRACK,
    SuggestedField.POLICY_AREAS,
)


@dataclass(frozen=True)
class Expectation:
    """What a lawyer would have entered, for one envelope.

    ``None`` means *nothing should be autofilled* — which is a real expected
    answer and the one several cases exist to check. It is distinct from an
    empty tuple on `policy_areas`, which means the same thing for a
    multi-valued field.
    """

    name: str
    title: str | None = None
    sender: str | None = None
    deadline: str | None = None
    track: str | None = None
    policy_areas: tuple[str, ...] = ()
    #: Why this case is in the corpus. Printed beside a failure, because a
    #: scorecard nobody can interpret is a number nobody acts on.
    about: str = ""

    def wanted(self, field_name: str) -> tuple[str, ...]:
        if field_name == SuggestedField.TITLE:
            return (self.title,) if self.title else ()
        if field_name == SuggestedField.SOURCE_ORGANISATIONS:
            return (self.sender,) if self.sender else ()
        if field_name == SuggestedField.RESPONSE_DEADLINE:
            return (self.deadline,) if self.deadline else ()
        if field_name == SuggestedField.TRACK:
            return (self.track,) if self.track else ()
        return self.policy_areas


@dataclass(frozen=True)
class FieldOutcome:
    """One field of one case: what was wanted, what was filled, what that is."""

    case: str
    field: str
    wanted: tuple[str, ...]
    filled: tuple[str, ...]

    @property
    def verdict(self) -> str:
        if not self.filled:
            return "missed" if self.wanted else "correctly-empty"
        if not self.wanted:
            return "wrong"
        if set(self.filled) <= set(self.wanted):
            # A subset counts as correct for a multi-valued field: filling two
            # of three right Valdkonnad has put nothing false on the form, and
            # the third is one click. Filling a fourth that is not wanted is a
            # different thing and lands in `wrong` above.
            return "correct" if self.filled else "missed"
        return "wrong"


@dataclass
class Scorecard:
    outcomes: list[FieldOutcome] = field(default_factory=list)

    def add(self, outcome: FieldOutcome) -> None:
        self.outcomes.append(outcome)

    def of(self, verdict: str) -> list[FieldOutcome]:
        return [outcome for outcome in self.outcomes if outcome.verdict == verdict]

    @property
    def wrong(self) -> list[FieldOutcome]:
        """The list that must stay empty. Everything else is a trade-off."""
        return self.of("wrong")

    @property
    def correct(self) -> list[FieldOutcome]:
        return self.of("correct")

    @property
    def missed(self) -> list[FieldOutcome]:
        return self.of("missed")

    def by_field(self, field_name: str) -> dict[str, int]:
        counts = {"correct": 0, "missed": 0, "wrong": 0, "correctly-empty": 0}
        for outcome in self.outcomes:
            if outcome.field == field_name:
                counts[outcome.verdict] += 1
        return counts

    def row(self, field_name: str) -> str:
        counts = self.by_field(field_name)
        offered = counts["correct"] + counts["wrong"]
        precision = f"{100 * counts['correct'] // offered:>3}%" if offered else "  —"
        return (
            f"{field_name:<22} õige={counts['correct']:>3}  puudu={counts['missed']:>3}  "
            f"VALE={counts['wrong']:>3}  tühi-õigesti={counts['correctly-empty']:>3}  "
            f"täpsus={precision}"
        )


def evaluate(
    analysis_input: AnalysisInput,
    expectation: Expectation,
    *,
    organisations: OrganisationCatalogue,
    policy_areas: dict[str, PolicyArea],
    scorecard: Scorecard,
) -> IntakeAnalysis:
    """Run one envelope through the real `Uus teema` decision and score it.

    Returns the annotated analysis as well as scoring it, so a caller
    inspecting a single case can see the candidates and their evidence rather
    than only the verdict.
    """
    analysis = analyse(
        analysis_input,
        organisations=organisations,
        policy_areas=policy_areas,
        current=CurrentValues(),
    )
    _initial, decided = prefill_initial(
        analysis, base={}, current=CurrentValues(), allow_title=True
    )
    for field_name in SCORED_FIELDS:
        scorecard.add(
            FieldOutcome(
                case=expectation.name,
                field=field_name,
                wanted=expectation.wanted(field_name),
                filled=tuple(decided.prefilled_values(field_name)),
            )
        )
    return decided


def describe(outcome: FieldOutcome, expectation: Expectation) -> str:
    """One failure, in a line somebody can act on."""
    return (
        f"{outcome.case} / {outcome.field}: ootasin {outcome.wanted or '—'}, "
        f"sain {outcome.filled or '—'}" + (f"  ({expectation.about})" if expectation.about else "")
    )


def resolve(values: tuple[str, ...], lookup: dict[str, Any]) -> tuple[str, ...]:
    """Corpus names into the primary keys a form control actually carries.

    The corpus says «Näidisministeerium» and «keskkond» because those are what
    a person reviewing it can check; the analyser proposes an Organisation id
    and a PolicyArea id, because those are what the control submits. Resolved
    at scoring time rather than written into the corpus, so a corpus file has
    no database in it.
    """
    return tuple(str(lookup[value]) for value in values if value in lookup)
