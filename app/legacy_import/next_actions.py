"""Deterministic reading of the `JÄRGMISEKS` free text.

The register's next-action column is prose. The same sentence can mean "this is
due on the 14th", "look at this again on the 14th" and "I expect the ministry to
move around the 14th", and the column never said which. That ambiguity is
exactly why Stage 1 split ``NextAction`` into a kind *and* a date meaning, and
it is why nothing here is allowed to write one.

So this module produces **candidates**, not state. A candidate is a proposal
with its evidence attached: the original sentence, the rule that fired, its
version, and what that rule believes. A person — or a reviewed mapping file
prepared by one — decides whether it becomes a NextAction.

The rules are small, literal and few on purpose. A regular expression that
matches a written date is defensible. A heuristic that infers urgency from
Estonian prose is not, and would quietly fill the department's work queue with
confident nonsense. There is no language model here and there will not be one:
the master specification forbids AI writing authoritative state without human
confirmation (21.2), and a work queue nobody believes is worse than none.
"""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass

from app.workflow.enums import ActionKind, DatePrecision, DateSemantics

#: Bumped when a rule's meaning changes. Stored on every candidate.
NEXT_ACTION_RULES_VERSION = "1.0"

#: How much the extractor is willing to claim.
DETERMINISTIC = "deterministic"
REVIEW_REQUIRED = "review_required"

_DATE = r"(\d{1,2})\.(\d{1,2})\.(\d{4})"

# "vaata 01.09.2026 üle", "vaatan 14.03.2026" — a reminder to look again.
# The verb is what carries the meaning, so the trailing particle is optional:
# the register writes it both ways and they say the same thing. Nothing else is
# inferred, and a date introduced by any other verb does not match — "jõustub
# 01.09.2026" states when an act enters into force, which is a fact about the
# world and not an instruction to anybody.
_REVIEW_ON = re.compile(rf"vaa(?:data|ta)\w*[^.]{{0,40}}?{_DATE}", re.IGNORECASE)

# "tähtaeg 14.03.2026" / "tähtajaga 14.03.2026" — a stated deadline.
_DEADLINE = re.compile(rf"tähtaeg\w*\s*:?\s*{_DATE}", re.IGNORECASE)

# "ootan eelnõud 2027. aasta 2. kvartalis" — an expectation about somebody
# else's timetable, known only to the quarter.
_EXPECT_QUARTER = re.compile(
    r"oota\w*\b.*?\b(\d{4})\.\s*aasta\s*(\d)\.\s*kvartal", re.IGNORECASE | re.DOTALL
)

# "ootan ... 2027. aastal" — expectation known only to the year.
_EXPECT_YEAR = re.compile(r"oota\w*\b.*?\b(\d{4})\.\s*aasta(?:l|ks)\b", re.IGNORECASE | re.DOTALL)


@dataclass(frozen=True)
class NextActionCandidate:
    """A proposal about one `JÄRGMISEKS` cell. Never authoritative."""

    source_text: str
    rule_id: str
    rules_version: str
    kind: str
    date_semantics: str
    target_date: dt.date | None
    date_precision: str
    confidence: str
    explanation: str

    @property
    def is_deterministic(self) -> bool:
        return self.confidence == DETERMINISTIC


def _safe_date(year: int, month: int, day: int) -> dt.date | None:
    try:
        return dt.date(year, month, day)
    except ValueError:
        return None


def extract_candidate(text: str) -> NextActionCandidate | None:
    """Read one cell. ``None`` means no rule was confident enough to speak.

    Rules are tried in order of how explicitly the source states its own
    meaning: a named review date beats a named deadline beats an expectation,
    because each of those is progressively more inference.
    """
    source = (text or "").strip()
    if not source:
        return None

    if (match := _REVIEW_ON.search(source)) is not None:
        day, month, year = (int(part) for part in match.groups())
        value = _safe_date(year, month, day)
        if value is not None:
            return NextActionCandidate(
                source_text=source,
                rule_id="review-on-exact",
                rules_version=NEXT_ACTION_RULES_VERSION,
                kind=ActionKind.MONITOR.value,
                date_semantics=DateSemantics.REVIEW_ON.value,
                target_date=value,
                date_precision=DatePrecision.EXACT.value,
                confidence=DETERMINISTIC,
                explanation="Tekst ütleb sõnaselgelt, millal teema uuesti üle vaadata.",
            )

    if (match := _DEADLINE.search(source)) is not None:
        day, month, year = (int(part) for part in match.groups())
        value = _safe_date(year, month, day)
        if value is not None:
            return NextActionCandidate(
                source_text=source,
                rule_id="deadline-exact",
                rules_version=NEXT_ACTION_RULES_VERSION,
                kind=ActionKind.DO.value,
                date_semantics=DateSemantics.DEADLINE.value,
                target_date=value,
                date_precision=DatePrecision.EXACT.value,
                confidence=DETERMINISTIC,
                explanation="Tekst nimetab tähtaega.",
            )

    if (match := _EXPECT_QUARTER.search(source)) is not None:
        year, quarter = int(match.group(1)), int(match.group(2))
        if 1 <= quarter <= 4:
            return NextActionCandidate(
                source_text=source,
                rule_id="expect-quarter",
                rules_version=NEXT_ACTION_RULES_VERSION,
                kind=ActionKind.WAIT.value,
                date_semantics=DateSemantics.EXPECTED_AROUND.value,
                # The first day of the quarter is a storage convention, not a
                # claim: DatePrecision.QUARTER makes the UI render "II kvartal
                # 2027" and never a day nobody committed to.
                target_date=dt.date(year, (quarter - 1) * 3 + 1, 1),
                date_precision=DatePrecision.QUARTER.value,
                confidence=DETERMINISTIC,
                explanation="Tekst ootab midagi nimetatud kvartalis; täpsus on kvartal.",
            )

    if (match := _EXPECT_YEAR.search(source)) is not None:
        year = int(match.group(1))
        return NextActionCandidate(
            source_text=source,
            rule_id="expect-year",
            rules_version=NEXT_ACTION_RULES_VERSION,
            kind=ActionKind.WAIT.value,
            date_semantics=DateSemantics.EXPECTED_AROUND.value,
            target_date=dt.date(year, 1, 1),
            date_precision=DatePrecision.YEAR.value,
            confidence=DETERMINISTIC,
            explanation="Tekst ootab midagi nimetatud aastal; täpsus on aasta.",
        )

    # Something was written and no rule understood it. That is a finding, not a
    # failure: the text stays verbatim in provenance and a person reads it.
    return NextActionCandidate(
        source_text=source,
        rule_id="unrecognised",
        rules_version=NEXT_ACTION_RULES_VERSION,
        kind="",
        date_semantics="",
        target_date=None,
        date_precision="",
        confidence=REVIEW_REQUIRED,
        explanation="Ükski determineeritud reegel ei sobinud; teksti ei teisendata.",
    )
