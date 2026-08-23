"""Reading one ``JÄRGMISEKS`` sentence deterministically, or refusing to.

``CurrentRegisterState.next_action_text`` is prose somebody typed into a
spreadsheet cell. Stage 1 split ``NextAction`` into a *kind* and a *date
meaning* precisely because that cell said all of them at once and recorded
which one it meant nowhere: the same sentence carries "this is due on the 14th",
"look at this again on the 14th" and "I expect the ministry to move around the
14th". ADR 0011 therefore refused to convert the column, and the register's own
instruction has been displayed beside the structured one ever since, labelled
*Excelist*, carrying no kind and no date.

This module does not reverse that decision. It narrows it.

A minority of those sentences say what they mean **in words**, not by
implication: they name a waiting verb, or a monitoring verb, or a stated
deadline. Where the wording is explicit the ambiguity ADR 0011 protects against
is simply absent, and leaving those sentences as untyped source text costs the
department a real work queue for no gain. Everything else stays exactly where it
is — displayed verbatim, converted by nobody.

Three rules make that safe, and they are the whole design.

**Explicit lexis only.** A closed allowlist of word forms, each one testable,
matched on word boundaries. No stemmer that broadens as the corpus grows, no
similarity, no model. If two kind classes fire, or none does, the answer is
*review required*, which is a successful outcome and not a failure.

**Explicit dates only.** A written date is a defensible thing to read. A date
*implied* by an instruction is not, so nothing is inferred from position,
recency or proximity to today. Two plausible dates mean review required —
never the first, the last or the nearest.

**Refusing beats guessing.** Every rejection carries a named reason, so an
operator can see where the source instructions went rather than being handed a
smaller number with no account of the difference.

Nothing here touches the database and nothing here writes. The output is an
immutable reading of one string; :mod:`app.legacy_import.next_action_enrichment`
decides what, if anything, may be done with it.

This module is deliberately separate from
:mod:`app.legacy_import.next_actions`, which reads the *historical* register
during import and produces advisory candidates for a person to look at. That one
speaks about sixteen years of archived rows and is allowed to be vague; this one
speaks about the maintained current register and may only be precise.
"""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass, field

from app.workflow.dates import bounds_for, period_bounds
from app.workflow.enums import ActionKind, DatePrecision, DateSemantics

#: Bumped whenever a rule's *meaning* changes — a new word form, a new date
#: shape, a different verdict for wording that already parsed. Recorded in the
#: plan digest, the operator report and the audit provenance, so two runs that
#: read the same sentence differently can never be mistaken for each other
#: (brief 70).
REGISTER_NEXT_ACTION_PARSER_VERSION = "1.1"


class Verdict:
    """What the parser concluded about one sentence.

    ``REVIEW_REQUIRED`` is not an error. On this corpus most sentences will land
    here, and that is the correct result: the goal is a work queue a lawyer
    believes, not a high conversion rate (brief 72).
    """

    UNDERSTOOD = "UNDERSTOOD"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"
    EMPTY = "EMPTY"


class ReviewReason:
    """Why a sentence was not converted. One named value per distinct cause.

    Named rather than collapsed into a single "no" so the operator report can
    say *where* the source instructions went. "94 of 134 were not converted" is
    not a finding; "61 named no kind, 18 named two, 9 carried two dates" is.
    """

    NO_KIND = "NO_KIND"
    AMBIGUOUS_KIND = "AMBIGUOUS_KIND"
    AMBIGUOUS_DATE = "AMBIGUOUS_DATE"
    UNREADABLE_DATE = "UNREADABLE_DATE"
    #: A DO verb with no date at all. The model would permit a dateless DO, but
    #: only by choosing a date meaning nobody stated (brief 20).
    DO_WITHOUT_DATE = "DO_WITHOUT_DATE"
    #: A DO verb beside an exact day, with nothing in the wording saying whether
    #: that day is a deadline or a plan. Both readings are ordinary and they
    #: differ in whether missing it is a failure.
    DO_DATE_WITHOUT_DEADLINE_WORDING = "DO_DATE_WITHOUT_DEADLINE_WORDING"
    #: Deadline wording attached to a period rather than a day. A deadline in
    #: "II kvartal 2027" would be stored against 1 April and reported as missed
    #: from 2 April, which the source did not say.
    APPROXIMATE_DEADLINE = "APPROXIMATE_DEADLINE"
    #: The only date in the sentence is governed by an entry-into-force
    #: clause. "ootan RT linki, jõustub 1.01.2028" states when the *act*
    #: takes effect, not when the awaited link arrives, and the two are
    #: years apart.
    DATE_GOVERNED_BY_ANOTHER_CLAUSE = "DATE_GOVERNED_BY_ANOTHER_CLAUSE"


# ---------------------------------------------------------------------------
# Kind vocabulary
# ---------------------------------------------------------------------------
#
# Explicit word forms, not stems. `oota\w*` would match `oodatavasti`
# ("presumably"), which is a hedge about somebody else's timetable and not an
# instruction to wait; `jälgi\w*` would match `jälgides` in a subordinate
# clause. Every form below is one a test asserts on, and adding one is a
# reviewed change that bumps the parser version.

#: Waiting on somebody else. First person is the register's own voice; the
#: -da infinitive is how the same instruction is written impersonally.
WAIT_FORMS: tuple[str, ...] = ("ootan", "ootame", "oodata", "ootel")

#: Watching a proceeding without being blocked by it.
MONITOR_FORMS: tuple[str, ...] = ("jälgin", "jälgime", "jälgida")

#: Work Koda itself performs. Three verbs, each in the infinitive and the two
#: present-tense persons the register actually uses. Deliberately short: a wide
#: DO allowlist is how an import fills somebody's task list with sentences that
#: were never tasks (brief 15).
DO_FORMS: tuple[str, ...] = (
    "esitada",
    "esitan",
    "esitame",
    "saata",
    "saadan",
    "saadame",
    "koostada",
    "koostan",
    "koostame",
)


def _forms_pattern(forms: tuple[str, ...]) -> re.Pattern[str]:
    alternation = "|".join(re.escape(form) for form in forms)
    return re.compile(rf"\b(?:{alternation})\b", re.IGNORECASE)


_WAIT = _forms_pattern(WAIT_FORMS)
_MONITOR = _forms_pattern(MONITOR_FORMS)
_DO = _forms_pattern(DO_FORMS)

#: Wording that states a deadline rather than implying one. ``tähtaeg`` and its
#: inflections, ``hiljemalt``, and the translative ``-ks`` written onto a date
#: (``15.09.2026-ks``), which is the third way the register says it.
_DEADLINE_WORD = re.compile(r"\b(?:tähta(?:eg|ja)\w*|hiljemalt)\b", re.IGNORECASE)
_TRANSLATIVE_SUFFIX = re.compile(r"-?ks\b", re.IGNORECASE)

#: Entry into force. The register states it constantly, beside instructions
#: that have nothing to do with it, and it is the one clause whose date is
#: reliably *not* the date the instruction is about: an act adopted this year
#: is published in Riigi Teataja within days and takes effect years later.
#: Explicit forms, on the same closed-allowlist discipline as the kind
#: vocabulary — a `jõustu\w*` stem would also swallow `jõustamiseks` in a
#: purpose clause, which governs nothing.
ENTRY_INTO_FORCE_FORMS: tuple[str, ...] = (
    "jõustub",
    "jõustuvad",
    "jõustus",
    "jõustusid",
    "jõustunud",
    "jõustuma",
    "jõustama",
)

_ENTRY_INTO_FORCE = _forms_pattern(ENTRY_INTO_FORCE_FORMS)

#: What ends the clause a verb governs. Estonian marks a new clause with
#: punctuation far more reliably than with word order, and where the writer
#: left it out, the next instruction verb marks it instead.
_CLAUSE_BREAK = re.compile(r"[.,;:!?]")


# ---------------------------------------------------------------------------
# Date vocabulary
# ---------------------------------------------------------------------------

#: Nominative and inessive for every month. Listed rather than derived: Estonian
#: drops or changes the stem vowel (``juuni`` → ``juunis``, ``oktoober`` →
#: ``oktoobris``), so appending an ``s`` would invent four words that do not
#: exist and miss the four that do.
MONTH_FORMS: dict[str, int] = {
    "jaanuar": 1,
    "jaanuaris": 1,
    "veebruar": 2,
    "veebruaris": 2,
    "märts": 3,
    "märtsis": 3,
    "aprill": 4,
    "aprillis": 4,
    "mai": 5,
    "mais": 5,
    "juuni": 6,
    "juunis": 6,
    "juuli": 7,
    "juulis": 7,
    "august": 8,
    "augustis": 8,
    "september": 9,
    "septembris": 9,
    "oktoober": 10,
    "oktoobris": 10,
    "november": 11,
    "novembris": 11,
    "detsember": 12,
    "detsembris": 12,
}

#: Longest first, so ``jaanuaris`` is not matched as ``jaanuar`` with a stray
#: ``is`` left over.
_MONTH_ALTERNATION = "|".join(
    re.escape(form) for form in sorted(MONTH_FORMS, key=len, reverse=True)
)

_ROMAN_TO_INT: dict[str, int] = {"i": 1, "ii": 2, "iii": 3, "iv": 4}

#: ``2027. aasta`` / ``aastal`` / ``aastaks``. The literal word is required: a
#: bare four-digit number beside a sentence is not a stated year, and reading
#: one as a target is exactly the inference this parser refuses.
_YEAR_WORD = r"(?P<year>\d{4})\.\s*aasta\w*"

_ORDINAL = r"(?:[1-4]|IV|III|II|I)"

_EXACT_DMY = re.compile(r"\b(?P<day>\d{1,2})\.(?P<month>\d{1,2})\.(?P<year>\d{4})\b")
_EXACT_ISO = re.compile(r"\b(?P<year>\d{4})-(?P<month>\d{1,2})-(?P<day>\d{1,2})\b")

_MONTH_THEN_YEAR = re.compile(
    rf"\b(?P<month>{_MONTH_ALTERNATION})\s+(?P<year>\d{{4}})\b", re.IGNORECASE
)
_YEAR_THEN_MONTH = re.compile(rf"\b{_YEAR_WORD}\s+(?P<month>{_MONTH_ALTERNATION})\b", re.IGNORECASE)

_QUARTER_THEN_YEAR = re.compile(
    rf"\b(?P<ordinal>{_ORDINAL})\.?\s*kvartal\w*\s+(?P<year>\d{{4}})\b", re.IGNORECASE
)
_YEAR_THEN_QUARTER = re.compile(
    rf"\b{_YEAR_WORD}\s+(?P<ordinal>{_ORDINAL})\.?\s*kvartal\w*", re.IGNORECASE
)

_HALF_THEN_YEAR = re.compile(
    r"\b(?P<ordinal>[12]|II|I)\.?\s*poolaasta\w*\s+(?P<year>\d{4})\b", re.IGNORECASE
)
_YEAR_THEN_HALF = re.compile(
    rf"\b{_YEAR_WORD}\s+(?P<ordinal>[12]|II|I)\.?\s*poolaasta\w*", re.IGNORECASE
)

_YEAR_ONLY = re.compile(rf"\b{_YEAR_WORD}", re.IGNORECASE)

#: Malformed-period detectors. A written ``5. kvartal`` is not a date this
#: parser may quietly ignore — ignoring it would let a sentence carrying an
#: unreadable period fall through to a dateless verdict and be converted.
#:
#: Roman numerals are matched here as well as Arabic ones. The register writes
#: both — ``II kvartalis``, ``I poolaasta`` — so a guard that only understood
#: ``5. kvartal`` would let ``V kvartalis`` through as though no period had
#: been written at all, which is the exact failure the guard exists to prevent.
_ANY_QUARTER = re.compile(r"\b(?P<ordinal>\d+|[ivx]+)\s*\.?\s*kvartal", re.IGNORECASE)
_ANY_HALF = re.compile(r"\b(?P<ordinal>\d+|[ivx]+)\s*\.?\s*poolaasta", re.IGNORECASE)


@dataclass(frozen=True)
class DateMention:
    """One written date or period, and where in the sentence it was written."""

    start: int
    end: int
    anchor: dt.date
    precision: str

    @property
    def identity(self) -> tuple[dt.date, str]:
        """What makes two mentions the same date rather than two dates."""
        return self.anchor, self.precision


def _safe_date(year: int, month: int, day: int) -> dt.date | None:
    try:
        return dt.date(year, month, day)
    except ValueError:
        return None


def _ordinal_value(raw: str) -> int | None:
    cleaned = raw.strip().casefold()
    if cleaned.isdigit():
        return int(cleaned)
    return _ROMAN_TO_INT.get(cleaned)


def _anchor(
    precision: str,
    *,
    year: int,
    month: int | None = None,
    quarter: int | None = None,
    half: int | None = None,
) -> dt.date | None:
    """The first day of the period, through the one shared convention.

    :func:`app.workflow.dates.bounds_for` is the only place that decides what a
    quarter's stored date is, so a period read here and a period typed on the
    Matter page can never normalise to two different anchors. It raises on a
    year outside the register's plausible range, which is how ``20226`` is
    refused rather than stored.
    """
    try:
        start, _ = bounds_for(precision, year=year, month=month, quarter=quarter, half=half)
    except ValueError:
        return None
    return start


def _scan_dates(text: str) -> tuple[list[DateMention], bool]:
    """Every written date in ``text``, and whether any was written unreadably.

    The second half of the return value is the point of scanning at all. A
    sentence containing ``5. kvartal 2027`` has a date; it simply has one that
    cannot exist. Treating that as "no date mentioned" would convert the
    sentence on its verb alone.
    """
    mentions: list[DateMention] = []
    unreadable = False

    for match in _EXACT_DMY.finditer(text):
        value = _safe_date(
            int(match.group("year")), int(match.group("month")), int(match.group("day"))
        )
        if value is None:
            unreadable = True
            continue
        mentions.append(DateMention(match.start(), match.end(), value, DatePrecision.EXACT.value))

    for match in _EXACT_ISO.finditer(text):
        value = _safe_date(
            int(match.group("year")), int(match.group("month")), int(match.group("day"))
        )
        if value is None:
            unreadable = True
            continue
        mentions.append(DateMention(match.start(), match.end(), value, DatePrecision.EXACT.value))

    for pattern in (_QUARTER_THEN_YEAR, _YEAR_THEN_QUARTER):
        for match in pattern.finditer(text):
            quarter = _ordinal_value(match.group("ordinal"))
            if quarter is None or not 1 <= quarter <= 4:
                unreadable = True
                continue
            anchor = _anchor(
                DatePrecision.QUARTER.value, year=int(match.group("year")), quarter=quarter
            )
            if anchor is None:
                unreadable = True
                continue
            mentions.append(
                DateMention(match.start(), match.end(), anchor, DatePrecision.QUARTER.value)
            )

    for pattern in (_HALF_THEN_YEAR, _YEAR_THEN_HALF):
        for match in pattern.finditer(text):
            half = _ordinal_value(match.group("ordinal"))
            if half is None or half not in (1, 2):
                unreadable = True
                continue
            anchor = _anchor(
                DatePrecision.HALF_YEAR.value, year=int(match.group("year")), half=half
            )
            if anchor is None:
                unreadable = True
                continue
            mentions.append(
                DateMention(match.start(), match.end(), anchor, DatePrecision.HALF_YEAR.value)
            )

    for pattern in (_MONTH_THEN_YEAR, _YEAR_THEN_MONTH):
        for match in pattern.finditer(text):
            month = MONTH_FORMS[match.group("month").casefold()]
            anchor = _anchor(DatePrecision.MONTH.value, year=int(match.group("year")), month=month)
            if anchor is None:
                unreadable = True
                continue
            mentions.append(
                DateMention(match.start(), match.end(), anchor, DatePrecision.MONTH.value)
            )

    for match in _YEAR_ONLY.finditer(text):
        anchor = _anchor(DatePrecision.YEAR.value, year=int(match.group("year")))
        if anchor is None:
            unreadable = True
            continue
        mentions.append(DateMention(match.start(), match.end(), anchor, DatePrecision.YEAR.value))

    # A period phrase contains its own year phrase: "2027. aasta 2. kvartalis"
    # matches both the quarter rule and the year rule. Those are one date
    # written once, not two candidates, so the longer span absorbs the shorter.
    # Without this the required example would be rejected as ambiguous.
    mentions.sort(key=lambda mention: (mention.start, -(mention.end - mention.start)))
    kept: list[DateMention] = []
    for mention in mentions:
        if any(other.start <= mention.start and mention.end <= other.end for other in kept):
            continue
        kept.append(mention)

    # An unreadable period may have produced a spurious bare-year mention from
    # the same phrase ("5. kvartal 2027" carries no "aasta", so normally none —
    # but "2027. aasta 5. kvartalis" does). It is reported as unreadable either
    # way; the flag is what the caller acts on.
    for pattern in (_ANY_QUARTER, _ANY_HALF):
        limit = 4 if pattern is _ANY_QUARTER else 2
        for match in pattern.finditer(text):
            ordinal = _ordinal_value(match.group("ordinal"))
            if ordinal is None or not 1 <= ordinal <= limit:
                unreadable = True

    return kept, unreadable


# ---------------------------------------------------------------------------
# The reading
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ParsedInstruction:
    """One sentence, read. Immutable, and never authoritative on its own.

    ``source_text`` is the sentence exactly as the register holds it. Nothing
    here is a normalised, corrected or shortened version of it: matching happens
    on a copy in memory and the original is what travels onward (brief 73).
    """

    source_text: str
    parser_version: str
    verdict: str
    kind: str = ""
    date_semantics: str = ""
    target_date: dt.date | None = None
    date_precision: str = ""
    review_reasons: tuple[str, ...] = field(default_factory=tuple)
    matched_forms: tuple[str, ...] = field(default_factory=tuple)

    @property
    def is_understood(self) -> bool:
        return self.verdict == Verdict.UNDERSTOOD

    @property
    def period_end(self) -> dt.date | None:
        """The last day the target period covers, or ``None`` without a date.

        Staleness is a question about the *end*: II poolaasta 2026 has not
        passed on 2 July 2026, and comparing the stored 1 July anchor with today
        would say it had (brief 28).
        """
        if self.target_date is None:
            return None
        return period_bounds(self.target_date, self.date_precision)[1]

    def is_stale(self, today: dt.date) -> bool:
        end = self.period_end
        return end is not None and end < today


def _review(source: str, *reasons: str, forms: tuple[str, ...] = ()) -> ParsedInstruction:
    return ParsedInstruction(
        source_text=source,
        parser_version=REGISTER_NEXT_ACTION_PARSER_VERSION,
        verdict=Verdict.REVIEW_REQUIRED,
        review_reasons=tuple(dict.fromkeys(reasons)),
        matched_forms=forms,
    )


def _matched(pattern: re.Pattern[str], text: str) -> tuple[str, ...]:
    return tuple(dict.fromkeys(match.group(0).casefold() for match in pattern.finditer(text)))


def parse_instruction(text: str) -> ParsedInstruction:
    """Read one ``JÄRGMISEKS`` sentence.

    The order is fixed and each step can only narrow: kind first, because a
    sentence whose instruction is unclear cannot be rescued by a clear date;
    then the date, because a date that cannot be read makes an otherwise clear
    instruction unconvertible; then the semantics, which is the only step where
    the two interact.
    """
    source = (text or "").strip()
    if not source:
        return ParsedInstruction(
            source_text="",
            parser_version=REGISTER_NEXT_ACTION_PARSER_VERSION,
            verdict=Verdict.EMPTY,
        )

    wait = _matched(_WAIT, source)
    monitor = _matched(_MONITOR, source)
    do = _matched(_DO, source)
    forms = wait + monitor + do
    classes = [bool(wait), bool(monitor), bool(do)]

    if sum(classes) > 1:
        return _review(source, ReviewReason.AMBIGUOUS_KIND, forms=forms)
    if not any(classes):
        return _review(source, ReviewReason.NO_KIND)

    mentions, unreadable = _scan_dates(source)
    if unreadable:
        return _review(source, ReviewReason.UNREADABLE_DATE, forms=forms)

    distinct = {mention.identity for mention in mentions}
    if len(distinct) > 1:
        return _review(source, ReviewReason.AMBIGUOUS_DATE, forms=forms)
    mention = mentions[0] if mentions else None

    if mention is not None and _governed_by_entry_into_force(source, mention):
        return _review(source, ReviewReason.DATE_GOVERNED_BY_ANOTHER_CLAUSE, forms=forms)

    if wait:
        return _understood(source, ActionKind.WAIT, DateSemantics.EXPECTED_AROUND, mention, forms)
    if monitor:
        # A monitoring date says when to look again; that is what REVIEW_ON
        # means. Without a date the kind is still unambiguous, so the action is
        # honest as "jälgin, kuupäeva pole" — and no review date is invented
        # to fill the gap (brief 17, 18).
        return _understood(source, ActionKind.MONITOR, DateSemantics.REVIEW_ON, mention, forms)

    return _do_reading(source, mention, forms)


def _understood(
    source: str,
    kind: ActionKind,
    semantics: DateSemantics,
    mention: DateMention | None,
    forms: tuple[str, ...],
) -> ParsedInstruction:
    return ParsedInstruction(
        source_text=source,
        parser_version=REGISTER_NEXT_ACTION_PARSER_VERSION,
        verdict=Verdict.UNDERSTOOD,
        kind=kind.value,
        date_semantics=semantics.value,
        target_date=mention.anchor if mention else None,
        date_precision=mention.precision if mention else DatePrecision.EXACT.value,
        matched_forms=forms,
    )


def _do_reading(
    source: str, mention: DateMention | None, forms: tuple[str, ...]
) -> ParsedInstruction:
    """Work Koda performs. The strictest of the three, and deliberately so.

    A DO is the only kind that can be *late*, so calling a date a deadline it
    was never stated to be is the one reading that turns an ordinary plan into a
    false alarm on somebody's work list.
    """
    if mention is None:
        # The model would accept a dateless DO with a non-deadline semantic, but
        # only by asserting a date meaning about a sentence that named none
        # (brief 20).
        return _review(source, ReviewReason.DO_WITHOUT_DATE, forms=forms)

    if _states_a_deadline(source, mention):
        if mention.precision != DatePrecision.EXACT.value:
            return _review(source, ReviewReason.APPROXIMATE_DEADLINE, forms=forms)
        return _understood(source, ActionKind.DO, DateSemantics.DEADLINE, mention, forms)

    if mention.precision == DatePrecision.EXACT.value:
        # A named day beside a DO verb with nothing saying which it is. "Esitada
        # arvamus 15.09.2026" is a deadline if the ministry set one and a plan if
        # the lawyer chose it, and only one of those may be reported as missed.
        return _review(source, ReviewReason.DO_DATE_WITHOUT_DEADLINE_WORDING, forms=forms)

    # An approximate period beside a DO verb states intended timing and cannot
    # state a legal deadline: nobody writes "the deadline is some time in the
    # second quarter". EXPECTED_AROUND is what that sentence says, and it can
    # never be reported as overdue.
    return _understood(source, ActionKind.DO, DateSemantics.EXPECTED_AROUND, mention, forms)


def _governed_by_entry_into_force(source: str, mention: DateMention) -> bool:
    """Whether an entry-into-force verb owns the only date in the sentence.

    The register habitually records when an act takes effect beside an
    instruction that has nothing to do with it: *ootan RT linki, jõustub
    1.01.2028* is a lawyer waiting for a publication link within weeks and an
    act taking effect in two years. Reading that date as the awaited event's
    timing states something the sentence never said.

    The question is what the verb still governs when the date arrives, and two
    things end that: punctuation, and another instruction. *Jõustub üldises
    korras. Ootan eelnõud 2027. aasta 2. kvartalis* names entry into force with
    no date of its own, and the quarter after the full stop belongs to the wait;
    so does the quarter in *jõustub üldises korras ja ootan eelnõud 2027. aasta
    2. kvartalis*, where a waiting verb has taken the sentence over without any
    punctuation to mark it. A bare precedence test would refuse both wrongly.

    What is left in between is the clause the entry-into-force verb owns —
    whether the date follows it immediately or after the noun it commences.
    """
    for match in _ENTRY_INTO_FORCE.finditer(source):
        if match.end() > mention.start:
            continue
        between = source[match.end() : mention.start]
        if _CLAUSE_BREAK.search(between):
            continue
        if _WAIT.search(between) or _MONITOR.search(between) or _DO.search(between):
            continue
        return True
    return False


def _states_a_deadline(source: str, mention: DateMention) -> bool:
    """Whether the wording *says* the date is a deadline.

    Presence alone is not enough: the marker has to precede the date it governs,
    or be the translative ending written onto it. "Tähtaeg möödus, ootan uut
    versiooni septembris" names a deadline that is already spent and a date that
    is not one.
    """
    for match in _DEADLINE_WORD.finditer(source):
        if match.start() < mention.start:
            return True
    return bool(_TRANSLATIVE_SUFFIX.match(source, mention.end))
