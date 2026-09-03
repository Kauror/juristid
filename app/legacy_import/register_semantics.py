"""What the maintained register says about a Matter's *current* state.

Two columns carry two different facts, and the interim year-only cutover
conflated neither of them well enough. This module is the one place either is
interpreted.

``HETKESEIS`` says where the external process stands
--------------------------------------------------
It decides whether work is still running. Two labels end it for
current-portfolio purposes and nothing else does:

    jõustunud
    rohkem pole tegevusi plaanis

Everything else in the controlled vocabulary — including ``muu``, which is a
real status and not a gap — leaves the Matter current.

``jõustunud`` needs a word, because ADR 0012 says the opposite about it and both
statements are true. As an *import* interpretation it maps to a stage rather
than a closure: an act commencing is not Koda closing a file, and no
``closed_at`` may be derived from it. As a *current-portfolio* question it is
terminal: once the act is in force there is no drafting step left to schedule.
Those are different questions, and this module answers only the second. Nothing
here produces a disposition or a closure timestamp.

``VÄLJA`` says the opinion was written and sent
-----------------------------------------------
It does **not** say the Matter finished. A populated ``VÄLJA`` on a Matter still
``kooskõlastusringil`` is the ordinary shape of the work: the opinion went out
and the proceeding continues. So it never closes anything and never becomes a
``Submission`` — a SENT submission needs documentary evidence, which a date is
not (ADR 0011, and the 2026 contract's own note on column F).

What it does answer is "is the opinion still being drafted", which is
`is_drafting`: current, and no send date recorded. On the approved snapshot that
is 15 Matters.

Continuation
------------
A register row whose ``JÄRGMISEKS`` says the work moved to another reference is
not a second live Matter, it is the same work counted twice. The detector is
deliberately narrow and requires **both** halves:

* continuation wording (``jätkub`` and its inflections); and
* exactly one Juristid-style ``YYYY_N`` reference.

Either alone is not a continuation. Wording with no reference does not say where
the work went; a reference with no wording is a cross-reference, which the
register uses constantly for related files. Wording with *several* references
cannot say which one, and is reported for review rather than resolved by picking
the first.

Order matters, and the measured snapshot shows why. Twenty-four rows carry both
halves, and twenty-two of them already hold a terminal status — so continuation
is evaluated *after* the status, and removes two further rows rather than
twenty-four. Reversing the two would still reach the same set here and would
stop doing so the first time somebody writes a continuation note on a live row.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

#: The controlled statuses that end current work. Both remain ordinary stage
#: vocabulary elsewhere; neither becomes a disposition or a closure date here.
TERMINAL_STATUS_LABELS: frozenset[str] = frozenset(
    {
        "jõustunud",
        "rohkem pole tegevusi plaanis",
    }
)

#: Continuation wording. Estonian inflects the verb (``jätkub``, ``jätkub
#: teema … all``, ``jätkame``), so the stem is matched rather than a phrase, and
#: the stem alone is never sufficient — see :func:`detect_continuation`.
_CONTINUATION_STEM = re.compile(r"jätku", re.IGNORECASE)

#: A Juristid-style human reference. Anchored on word boundaries so that a date
#: or a longer identifier containing the same digits is not read as one.
_REFERENCE = re.compile(r"\b(20\d{2})_(\d{1,4})\b")


class ContinuationVerdict:
    """Whether a ``JÄRGMISEKS`` text moves the work to another Matter."""

    NONE = "NONE"
    SUPERSEDED = "SUPERSEDED"
    AMBIGUOUS = "AMBIGUOUS"


@dataclass(frozen=True)
class Continuation:
    verdict: str
    reference: str = ""
    reason: str = ""

    @property
    def supersedes(self) -> bool:
        return self.verdict == ContinuationVerdict.SUPERSEDED

    @property
    def needs_review(self) -> bool:
        return self.verdict == ContinuationVerdict.AMBIGUOUS


NO_CONTINUATION = Continuation(verdict=ContinuationVerdict.NONE)


def normalise_status(label: str | None) -> str:
    """A status label reduced to what the vocabulary comparison needs.

    Case and surrounding whitespace only. The label is *not* otherwise
    normalised: these are controlled values, and quietly repairing a misspelled
    one would hide a data-quality finding the register owner should see.
    """
    return (label or "").strip().casefold()


#: Compared after :func:`normalise_status`, so the vocabulary is stated once.
_TERMINAL_NORMALISED: frozenset[str] = frozenset(
    normalise_status(label) for label in TERMINAL_STATUS_LABELS
)


def is_terminal_status(label: str | None) -> bool:
    """Whether this status ends current work. An unknown label does not.

    Failing *open* is the conservative direction here, and it is the opposite of
    the choice authorization makes. Authorization whitelists because showing too
    much is the harm; this decides whether to retire a Matter from somebody's
    work list, where dropping live work is the harm and an extra row on a
    dashboard is not.
    """
    return normalise_status(label) in _TERMINAL_NORMALISED


def detect_continuation(next_action_text: str | None) -> Continuation:
    """Whether this instruction moves the work to a named other Matter.

    Both halves required, and never one. See the module docstring for why each
    half alone is insufficient in this corpus.
    """
    text = (next_action_text or "").strip()
    if not text:
        return NO_CONTINUATION

    has_wording = bool(_CONTINUATION_STEM.search(text))
    references = [f"{year}_{number}" for year, number in _REFERENCE.findall(text)]
    distinct = sorted(set(references))

    if not has_wording:
        # A bare reference is a cross-reference. The register uses them for
        # related and predecessor files constantly, and reading one as "this
        # Matter has moved" would retire live work on the strength of a
        # footnote.
        return NO_CONTINUATION

    if not distinct:
        return Continuation(
            verdict=ContinuationVerdict.AMBIGUOUS,
            reason="Jätkumise sõnastus ilma viiteta: pole öeldud, kuhu töö liikus.",
        )
    if len(distinct) > 1:
        return Continuation(
            verdict=ContinuationVerdict.AMBIGUOUS,
            reason=f"Jätkumise sõnastus mitme viitega ({', '.join(distinct)}).",
        )
    return Continuation(verdict=ContinuationVerdict.SUPERSEDED, reference=distinct[0])


def is_real_row(reference: str | None, title: str | None) -> bool:
    """Whether a register row describes a Matter at all.

    The current sheet is pre-numbered well past the work that exists: the
    approved snapshot carries references up to ``2026_300`` and titles up to
    ``2026_195``, so 105 rows hold a number and nothing else. A reference is not
    a row. The title is what makes it one, which is what the 2026 contract's
    ``null_semantics`` for column B already says.
    """
    return bool((title or "").strip())


def has_send_date(opinion_sent_raw: str | None) -> bool:
    """Whether ``VÄLJA`` records that the opinion went out.

    Presence only. The date itself is preserved as source metadata; nothing in
    the current-portfolio decision reads its value, because *when* the opinion
    was sent says nothing about whether the proceeding is still running.
    """
    return bool((opinion_sent_raw or "").strip())


# ---------------------------------------------------------------------------
# VÄLJA, as three answers rather than two
# ---------------------------------------------------------------------------
#
# `has_send_date` answers the portfolio's question — is the drafting step
# recorded as finished — and answers it from presence alone, which is right and
# stays exactly as it is. What it cannot answer is what the reader of a Matter
# page needs: the 28.08 workbook writes sixteen 2026 rows as **ei saatnud**,
# and a surface that knows only "something is recorded" renders those as a sent
# opinion whose date it failed to parse. That is the opposite of what the
# register said.


class OpinionSentState:
    """What ``VÄLJA`` says, in the three shapes it actually takes.

    Derived and displayable. Deliberately *not* a Submission state and not a
    stage: a formal outbound opinion is a ``Submission`` with immutable final
    evidence, and none of these values may ever produce one (ADR 0011,
    DATA-001).
    """

    #: A parseable date. The opinion went out and the register says when.
    DATE = "DATE"
    #: The register wrote, in words, that Koda did not send one. A decision,
    #: recorded — not a missing value and not an unfinished draft.
    NOT_SENT = "NOT_SENT"
    #: Something else is written that the date parser cannot read. Presence is
    #: recorded; what it means is a data-quality question for the register
    #: owner, and inventing an answer here would be the third wrong reading.
    RECORDED_OTHER = "RECORDED_OTHER"
    #: Nothing is written. Not "no opinion was sent" — not recorded.
    BLANK = "BLANK"


OPINION_SENT_STATES: tuple[str, ...] = (
    OpinionSentState.DATE,
    OpinionSentState.NOT_SENT,
    OpinionSentState.RECORDED_OTHER,
    OpinionSentState.BLANK,
)

#: The two readings that mean *the opinion work on this file is finished*.
#:
#: The register's own convention, confirmed by the product owner as the meaning
#: of column F: a date says the opinion went out on that day, ``ei saatnud``
#: says a decision was taken not to send one, and **both** end the drafting
#: step. A blank cell says the file is still being worked on (ADR 0059).
#:
#: ``RECORDED_OTHER`` is deliberately **not** here, and that omission is the
#: whole reason this is a set rather than ``not BLANK``. Something is written
#: that the parser could not read as a date and that is not one of the
#: ``ei saatnud`` wordings — so it may mean the opinion went out, or that it did
#: not, or something about the file entirely. Treating it as completion would
#: discharge an obligation on the strength of a cell nobody has read; it is a
#: data-quality question for the register owner and is surfaced as one.
#:
#: Still not a ``Submission`` and still incapable of becoming one. This says the
#: work is finished, never *what Koda sent* — that needs immutable final
#: evidence, which a spreadsheet cell is not (ADR 0011, DATA-001).
OPINION_WORK_COMPLETE_STATES: frozenset[str] = frozenset(
    {
        OpinionSentState.DATE,
        OpinionSentState.NOT_SENT,
    }
)

#: The wordings that mean Koda decided not to send. A closed allowlist on the
#: same discipline as the status vocabulary: the 28.08 workbook writes one form
#: sixteen times, and a stem match would also swallow *ei saatnud veel*, which
#: says the opposite about whether the work is finished.
NOT_SENT_FORMS: frozenset[str] = frozenset({"ei saatnud", "ei saadetud", "ei saada"})


def opinion_sent_state(raw: str | None, *, parsed_date: object | None = None) -> str:
    """Which of the four things ``VÄLJA`` is saying.

    ``parsed_date`` is the caller's own parse rather than a second one made
    here: the register's dates are read by :mod:`app.legacy_import.dates`, and
    a module that re-decided what counts as a date would be a second answer to
    a question that already has one.
    """
    text = (raw or "").strip()
    if not text:
        return OpinionSentState.BLANK
    if parsed_date is not None:
        return OpinionSentState.DATE
    if text.casefold() in NOT_SENT_FORMS:
        return OpinionSentState.NOT_SENT
    return OpinionSentState.RECORDED_OTHER


# ---------------------------------------------------------------------------
# KELLELE, when it names more than one body
# ---------------------------------------------------------------------------


class AddresseeCardinality:
    """How many organisations one ``KELLELE`` cell names."""

    BLANK = "BLANK"
    SINGLE = "SINGLE"
    MULTIPLE = "MULTIPLE"


#: What separates two organisations in one cell.
#:
#: Comma, semicolon, slash, and the standalone conjunction ``ning``. **Not**
#: ``ja``, and that omission is the whole rule: the ministries Koda writes to
#: are called *Majandus- ja Kommunikatsiooniministeerium*, *Justiits- ja
#: Digiministeerium*, *Regionaal- ja Põllumajandusministeerium*. Splitting on
#: ``ja`` reads 193 single addressees in the 28.08 workbook as pairs and
#: invents an organisation called *Kommunikatsiooniministeerium* for each one.
#: With this separator set the same workbook yields thirteen genuinely multiple
#: cells, every one of which a person can read as such.
_ADDRESSEE_SEPARATOR = re.compile(r"[,;/]|\bning\b", re.IGNORECASE)


def split_addressees(raw: str | None) -> tuple[str, ...]:
    """The organisations one ``KELLELE`` cell names, in source order.

    Whitespace-trimmed and de-duplicated, and otherwise untouched: an
    abbreviation the register uses (``MKM``) stays the abbreviation, because
    expanding it here would be a resolution decision made in the wrong place.
    """
    text = (raw or "").strip()
    if not text:
        return ()
    parts = [part.strip() for part in _ADDRESSEE_SEPARATOR.split(text)]
    return tuple(dict.fromkeys(part for part in parts if part))


def addressee_cardinality(raw: str | None) -> str:
    """Whether this cell names none, one, or several organisations."""
    parts = split_addressees(raw)
    if not parts:
        return AddresseeCardinality.BLANK
    return AddresseeCardinality.SINGLE if len(parts) == 1 else AddresseeCardinality.MULTIPLE


# ---------------------------------------------------------------------------
# The two member-feedback counts
# ---------------------------------------------------------------------------


def parse_member_count(value: object) -> int | None:
    """A feedback count, or ``None`` when the register recorded none.

    ``None`` and ``0`` are different answers and the whole reason this function
    exists rather than an ``int(... or 0)`` at each call site. A blank cell
    means nobody wrote the number down; a written zero means somebody measured
    and the answer was none. In the 28.08 workbook the 2026 sheet holds 124
    written zeros against 19 blanks in one of these columns, so collapsing them
    would report 124 measured facts as gaps — or, worse, 19 gaps as measured
    zeros (2026 era contract, columns I and J).

    Anything that is not a non-negative whole number is ``None`` as well. A
    count is not a place to guess.
    """
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value >= 0 else None
    if isinstance(value, float):
        return int(value) if value >= 0 and float(value).is_integer() else None

    text = str(value).strip()
    if not text:
        return None
    # A spreadsheet routinely stores an integer as "273.0"; the decimal comma
    # is how it is written when the sheet's locale is Estonian.
    normalised = text.replace(",", ".")
    try:
        number = float(normalised)
    except ValueError:
        return None
    if number < 0 or not number.is_integer():
        return None
    return int(number)
