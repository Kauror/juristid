"""Retiring a proposal the reconciliation no longer believes.

A candidate's identity is ``(item, matter, match_class)``, so when a rerun sees
new evidence and reclassifies the same occurrence it writes a *new* row and
leaves the old one behind. That stranded row is the problem this module exists
for. It cannot honestly become APPLIED, because it produced nothing; it must not
become REJECTED, because no person rejected it; and it must not be deleted,
because it is the record of what the reconciliation believed at the time.

So it is superseded, and it says by what.

Three rules hold the mechanism to that narrow job:

**Only PENDING is superseded.** APPLIED means a canonical Submission exists and
points at this row; the five human states mean somebody looked and answered.
Overwriting either would make the queue's history a function of how many times
the importer happened to run — the exact failure the state was introduced to
prevent.

**Supersession is a pointer, never a deletion.** Signals, conflicts, the
explanation and the batch that produced them all stay readable. The archive
detail page shows the retired proposal underneath the one that replaced it,
because "we used to think this" is often the most useful thing on the page.

**Nothing is superseded by something older, or by itself.** The chain has a
direction and it is checked rather than assumed, so the queue cannot be walked
into a loop by a rerun with a clock skew.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from django.db import models, transaction
from django.utils import timezone

from app.core.errors import DomainError
from app.legacy_import.opinion_archive import OpinionMatchCandidate
from app.legacy_import.opinion_enums import (
    SUPERSEDABLE_STATES,
    OpinionCandidateState,
    OpinionMatchClass,
)

#: How far a chain is followed when checking for a loop. Real chains are one
#: link long — a superseded row is not PENDING, so it cannot be superseded
#: again — and this only bounds the walk if a row is ever edited by hand.
MAX_CHAIN = 32


class SupersessionRefused(DomainError):
    """The retirement was not recorded, and the reason is worth reading."""


@dataclass
class SupersessionReport:
    considered: int = 0
    superseded: int = 0
    already: int = 0
    refused: int = 0
    findings: list[str] = field(default_factory=list)

    def as_text(self) -> str:
        rows = [
            ("vaadatud", self.considered),
            ("asendatud", self.superseded),
            ("juba asendatud", self.already),
            ("keeldutud", self.refused),
        ]
        lines = [f"  {label:<32} {value:>12}" for label, value in rows]
        lines.extend(f"  leid: {finding}" for finding in self.findings)
        return "\n".join(lines)


@transaction.atomic
def supersede_candidate(
    *,
    superseded: OpinionMatchCandidate,
    replacement: OpinionMatchCandidate,
    reason: str,
    actor: Any = None,
) -> OpinionMatchCandidate:
    """Record that one proposal replaces another. Idempotent.

    Called twice with the same pair it changes nothing and returns the same
    row, so a retried request and a rerun importer behave alike.
    """
    if superseded.pk == replacement.pk:
        raise SupersessionRefused("Kandidaat ei saa asendada iseennast.")
    if superseded.item_id != replacement.item_id:
        raise SupersessionRefused(
            "Asendada saab ainult sama arhiivikirje kohta käivat ettepanekut."
        )
    if not reason.strip():
        # A retirement with no reason is indistinguishable from an accident six
        # months later, and this row's whole value is that it explains itself.
        raise SupersessionRefused("Asendamise põhjus on kohustuslik.")

    if superseded.state == OpinionCandidateState.SUPERSEDED:
        if superseded.superseded_by_id == replacement.pk:
            return superseded
        raise SupersessionRefused("Kandidaat on juba mõne teise ettepanekuga asendatud.")
    if superseded.state not in SUPERSEDABLE_STATES:
        raise SupersessionRefused(
            "Asendada saab ainult ootel ettepanekut; rakendatud ja ülevaataja otsustatud "
            "read jäävad puutumata."
        )
    if replacement.state == OpinionCandidateState.SUPERSEDED:
        raise SupersessionRefused("Asendaja on ise juba asendatud.")
    if _would_loop(superseded=superseded, replacement=replacement):
        raise SupersessionRefused("Asendamine tekitaks ringviite.")

    superseded.state = OpinionCandidateState.SUPERSEDED
    superseded.superseded_by = replacement
    superseded.superseded_at = timezone.now()
    superseded.supersession_reason = reason.strip()
    if actor is not None and getattr(actor, "is_authenticated", False):
        # Only when a person did it. An importer sweep leaves `decided_by`
        # empty, because writing a system actor there would make the queue's
        # "who decided this" column lie in the one place it must not.
        superseded.decided_by = actor
        superseded.decided_at = superseded.superseded_at
    superseded.save(
        update_fields=[
            "state",
            "superseded_by",
            "superseded_at",
            "supersession_reason",
            "decided_by",
            "decided_at",
            "updated_at",
        ]
    )
    return superseded


def _would_loop(*, superseded: OpinionMatchCandidate, replacement: OpinionMatchCandidate) -> bool:
    """Whether following the replacement's own chain arrives back here."""
    seen: set[Any] = {superseded.pk}
    current: Any = replacement
    for _ in range(MAX_CHAIN):
        if current is None:
            return False
        if current.pk in seen:
            return True
        seen.add(current.pk)
        current = current.superseded_by
    return True


def sweep_superseded(*, dry_run: bool = False) -> SupersessionReport:
    """Retire pending proposals that a later run has already answered.

    Two shapes qualify, and nothing else does:

    * the same file and the same Matter under a *different* class — the
      reconciliation reclassified its own proposal;
    * a file previously recorded as UNMATCHED that a later run tied to a
      Matter — the case that produced most of the stranded rows, because
      "we found nothing" is exactly the belief new evidence overturns.

    A file that gained a *second* Matter is not swept: two live proposals on
    one letter is the multi-Matter case, and it belongs in front of a person
    rather than resolved by whichever row is newer.

    ``dry_run`` wraps the whole pass and rolls it back, so the counts are the
    counts a real run would produce rather than an estimate of them.
    """
    if not dry_run:
        return _sweep()
    with transaction.atomic():
        report = _sweep()
        transaction.set_rollback(True)
    return report


def _sweep() -> SupersessionReport:
    report = SupersessionReport()
    pending = (
        OpinionMatchCandidate.objects.filter(state=OpinionCandidateState.PENDING)
        .select_related("item")
        .order_by("created_at", "pk")
    )
    for candidate in pending.iterator():
        report.considered += 1
        replacement = _replacement_for(candidate)
        if replacement is None:
            continue
        try:
            supersede_candidate(
                superseded=candidate,
                replacement=replacement,
                reason=(
                    "Hilisem võrdlusjooks liigitas sama arhiivikirje ümber "
                    f"({candidate.match_class} → {replacement.match_class})."
                ),
            )
        except SupersessionRefused as error:
            report.refused += 1
            report.findings.append(str(error))
            continue
        report.superseded += 1
    return report


def _replacement_for(candidate: OpinionMatchCandidate) -> OpinionMatchCandidate | None:
    """The newer proposal that has already answered this one, if there is one."""
    newer = (
        OpinionMatchCandidate.objects.filter(item_id=candidate.item_id)
        .exclude(pk=candidate.pk)
        .exclude(state=OpinionCandidateState.SUPERSEDED)
        .filter(created_at__gt=candidate.created_at)
        .order_by("-created_at", "-pk")
    )
    if candidate.matter_id is not None:
        return newer.filter(matter_id=candidate.matter_id).first()
    if candidate.match_class == OpinionMatchClass.UNMATCHED:
        return newer.filter(matter_id__isnull=False).first()
    return None


def superseded_findings() -> list[str]:
    """Aggregate integrity checks over the supersession chain."""
    findings: list[str] = []

    dangling = OpinionMatchCandidate.objects.filter(
        state=OpinionCandidateState.SUPERSEDED, superseded_by__isnull=True
    ).count()
    if dangling:
        findings.append(f"{dangling} asendatud kandidaadil puudub asendaja")

    unreasoned = OpinionMatchCandidate.objects.filter(
        state=OpinionCandidateState.SUPERSEDED, supersession_reason=""
    ).count()
    if unreasoned:
        findings.append(f"{unreasoned} asendatud kandidaadil puudub põhjus")

    # A pointer set on a row that is not in the SUPERSEDED state: the two ways
    # of saying the same thing have come apart, and a queue filtering on state
    # would show a retired proposal as live.
    inconsistent = (
        OpinionMatchCandidate.objects.filter(superseded_by__isnull=False)
        .exclude(state=OpinionCandidateState.SUPERSEDED)
        .count()
    )
    if inconsistent:
        findings.append(f"{inconsistent} kandidaadil on asendaja, kuid olek ei ole ASENDATUD")

    crossed = (
        OpinionMatchCandidate.objects.filter(superseded_by__isnull=False)
        .exclude(superseded_by__item_id=models.F("item_id"))
        .count()
    )
    if crossed:
        findings.append(f"{crossed} kandidaadi asendaja käib teise arhiivikirje kohta")

    return findings
