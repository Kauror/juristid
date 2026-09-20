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
    """

    OTHER = "OTHER", "Muu tähtaeg"
    TRANSPOSITION_DEADLINE = "TRANSPOSITION_DEADLINE", "ELi õiguse ülevõtmise tähtaeg"


class EventKind(models.TextChoices):
    """What a row in the combined *Olulised tähtajad* calendar came from.

    The two sources stay visibly distinct. A commencement date is not a
    milestone somebody chose to watch, and merging them into one unlabelled
    list would lose the difference the department relies on
    (Stage-2G brief 47, 48).
    """

    IMPORTANT_DATE = "IMPORTANT_DATE", "Tähtaeg"
    EFFECTIVE_DATE = "EFFECTIVE_DATE", "Jõustumine"
