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
