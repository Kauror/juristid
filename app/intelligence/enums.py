"""Vocabulary for the three structured Matter facts.

None of these is a `Tag`. Taxonomy answers *what a Matter is about*; these
answer *what is expected to happen to it, when it takes effect, and whether the
department judged the result a win*. They carry dates, provenance, review state
and their own audit trail, and a tag carries none of that (Stage-2G brief 2).
"""

from __future__ import annotations

from django.db import models


class FactStatus(models.TextChoices):
    """Whether a recorded expectation still stands.

    Nothing is deleted when a plan changes. A cancelled deadline is what the
    department believed at the time, and a consultation round that was called
    off is itself part of the history of the file (Stage-2G brief 5, 33).
    """

    ACTIVE = "ACTIVE", "Kehtiv"
    CANCELLED = "CANCELLED", "Tühistatud"
    SUPERSEDED = "SUPERSEDED", "Asendatud"


class EffectiveDateKind(models.TextChoices):
    """What is actually known about when an act comes into force.

    Three different states of knowledge, kept apart so that none of them has to
    be stored as a fabricated date. "Jõustub üldises korras" is a real legal
    statement, not a missing value, and an act whose date has not been decided
    yet is not an act that comes into force on 01.01.1970
    (Stage-2G brief 12, 14).
    """

    KNOWN_DATE = "KNOWN_DATE", "Teadaolev kuupäev"
    GENERAL_ORDER = "GENERAL_ORDER", "Jõustub üldises korras"
    UNKNOWN = "UNKNOWN", "Kuupäev täpsustamisel"


class WorkVictoryStatus(models.TextChoices):
    """Where a claimed `Töövõit` stands in review.

    A candidate is somebody's proposal that this was a win. It becomes a
    confirmed work victory only when a person deliberately says so — never as a
    side effect of editing the wording, and never automatically from an outcome
    the system inferred (master specification 3.5, Stage-2G brief 20, 41).
    """

    CANDIDATE = "CANDIDATE", "Töövõidu kandidaat"
    CONFIRMED = "CONFIRMED", "Kinnitatud töövõit"
    NOT_REALIZED = "NOT_REALIZED", "Ei realiseerunud"


class ImportantDateKind(models.TextChoices):
    """What *kind* of milestone an `Oluline tähtaeg` is, where the answer matters.

    Two values, and deliberately not a taxonomy of everything a department
    watches. `Oluline tähtaeg` is free text by design — «komisjoni istung»,
    «eelnõu avalikustamine», «uus versioon lubatud» — and turning that column into
    a classification exercise would make ordinary capture slower for no gain
    (AGENTS.md, Stage-2G brief 6).

    `TRANSPOSITION_DEADLINE` earns a value because one surface has to
    **recognise** it rather than read it: `Menetluse kulg` says what may come
    next, and on a directive the transposition deadline is the one recorded future
    date that belongs beside the `Ülevõtmine` phase. The alternative was searching
    the title for «ülevõtmine», which is the prose-matching this product refuses
    everywhere else — a deadline named «direktiivi rakendamise kuupäev» would be
    missed and one named «ülevõtmise arutelu» would be wrongly claimed.

    `OTHER` is the default and stays the overwhelming majority. **Nothing is
    backfilled**: a row written before this column existed reads `OTHER` because
    nobody has said otherwise, which is exactly what it means.

    `PLANNED_COMMENCEMENT` — «Eelnõus kavandatud jõustumine» — earns its value
    the same way (docs/adr/0120, amendment of 2026-10-19): a commencement a
    *draft* proposes is a date the department watches, not a commencement
    (`MatterEffectiveDate` would read «Jõustus» once it passed, adopted or not)
    and not what Koda does next. The next-step rule has to recognise it rather
    than read «jõustumine» out of a title, so it is a kind, and it is one of the
    `INFORMATIONAL_IMPORTANT_DATE_KINDS` below.

    `ESTIMATED_COMMENCEMENT` — «Eeldatav jõustumine (allika hinnang)» — is the
    same kind of fact from a different source (docs/adr/0120, amendment of
    2026-10-20): a commencement somebody *estimated* — a tracker, a colleague,
    a news item — rather than a date a draft itself states. It is kept apart
    from `PLANNED_COMMENCEMENT` so that a record never attributes an estimate to
    the bill's text; both are informational.
    """

    OTHER = "OTHER", "Muu tähtaeg"
    TRANSPOSITION_DEADLINE = "TRANSPOSITION_DEADLINE", "ELi õiguse ülevõtmise tähtaeg"
    PLANNED_COMMENCEMENT = "PLANNED_COMMENCEMENT", "Eelnõus kavandatud jõustumine"
    ESTIMATED_COMMENCEMENT = "ESTIMATED_COMMENCEMENT", "Eeldatav jõustumine (allika hinnang)"


#: **Milestones that inform, as opposed to milestones that are the file's next
#: step** (docs/adr/0120 §2, amendment of 2026-10-19).
#:
#: An `Oluline tähtaeg` a lawyer records as «Ootan ministeeriumi tagasisidet,
#: 1.10.2026» says what the file is waiting for, and ADR 0120 §2 lets it stand in
#: for a missing `Järgmiseks`. A milestone of a kind listed here describes an
#: event outside the department's work — a commencement a draft proposes or a
#: source estimates — and
#: therefore **never** does: it is never the next step, it never keeps a Matter
#: out of a «järgmine tegevus puudub» population, and its period ending is not
#: lateness. It stays a dated fact on the Teema like any other milestone.
#:
#: Decided by the stored `kind` and nothing else — never the title. `OTHER` (the
#: default, and every existing row) and `TRANSPOSITION_DEADLINE` keep ADR 0120's
#: behaviour unchanged; moving a kind into this set is a product decision.
INFORMATIONAL_IMPORTANT_DATE_KINDS: frozenset[str] = frozenset(
    {ImportantDateKind.PLANNED_COMMENCEMENT.value, ImportantDateKind.ESTIMATED_COMMENCEMENT.value}
)


def is_informational_milestone(kind: str | None) -> bool:
    """Whether an `Oluline tähtaeg` of this kind only informs (never a next step, never late)."""
    return (kind or ImportantDateKind.OTHER) in INFORMATIONAL_IMPORTANT_DATE_KINDS


class EventKind(models.TextChoices):
    """What a row in the combined *Olulised tähtajad* calendar came from.

    The two sources stay visibly distinct. A commencement date is not a
    milestone somebody chose to watch, and merging them into one unlabelled
    list would lose the difference the department relies on
    (Stage-2G brief 47, 48).
    """

    IMPORTANT_DATE = "IMPORTANT_DATE", "Tähtaeg"
    EFFECTIVE_DATE = "EFFECTIVE_DATE", "Jõustumine"
