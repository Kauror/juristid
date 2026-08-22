"""Which Matters an archive file belongs to — plural, on purpose.

One letter can legitimately concern several Matters. The corpus has bundles
where four earlier opinions were resent together, cover letters that answer two
consultations at once, and annexes filed under a different reference from the
letter they accompany. The reconciliation queue cannot express any of that: a
candidate names one Matter, and choosing it means choosing against the others.

`OpinionArchiveMatterLink` is the place where the answer may be *both*. It is a
weaker statement than a Submission and deliberately so — it says "this evidence
concerns this Matter", not "the Chamber sent this opinion to that ministry on
that date". Only the canonical Submission says the latter, only the apply path
creates one, and nothing here ever will.

What this module refuses is as important as what it does:

* **it never creates a Matter.** A link points at a Matter somebody opened. An
  archive file naming an organisation is not authority to invent a register
  entry for it;
* **it never removes a link that a Submission stands on.** A reviewer may undo
  their own judgement; undoing the record of a filed opinion is a different act
  with a different bar, and it is not available from this surface;
* **it never derives a link from resemblance.** Every automatic basis is an
  exact identity — the same bytes, or a Submission that already exists.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from django.db import models, transaction
from django.utils import timezone

from app.core.errors import DomainError
from app.legacy_import.opinion_archive import OpinionArchiveItem, OpinionSubmissionImport
from app.legacy_import.opinion_binary import OpinionArchiveBinary, OpinionArchiveMatterLink
from app.legacy_import.opinion_enums import (
    AUTOMATIC_MATCH_CLASSES,
    ArchiveLinkBasis,
    OpinionCandidateState,
)

#: Bases a person may not withdraw from this screen. Both are statements about
#: something that exists elsewhere — bytes filed against a Matter, or a
#: canonical Submission — so removing the link here would leave the page
#: disagreeing with the register rather than correcting it.
PROTECTED_BASES: frozenset[str] = frozenset({ArchiveLinkBasis.APPLIED_SUBMISSION})


class LinkRefused(DomainError):
    """The relationship was not recorded, and the reason is worth reading."""


@dataclass
class LinkReport:
    considered: int = 0
    created: int = 0
    unchanged: int = 0
    findings: list[str] = field(default_factory=list)

    def as_text(self) -> str:
        rows = [
            ("vaadatud", self.considered),
            ("uusi seoseid", self.created),
            ("juba olemas", self.unchanged),
        ]
        lines = [f"  {label:<32} {value:>12}" for label, value in rows]
        lines.extend(f"  leid: {finding}" for finding in self.findings)
        return "\n".join(lines)


@transaction.atomic
def link_matter(
    *,
    binary: OpinionArchiveBinary,
    matter: Any,
    basis: str,
    actor: Any = None,
    item: Any = None,
    candidate: Any = None,
    note: str = "",
) -> tuple[OpinionArchiveMatterLink, bool]:
    """Record that this file concerns this Matter. Idempotent.

    Returns the link and whether it was created, so a caller can tell "added"
    from "already said". A second click adds nothing and, importantly, does not
    downgrade an existing reviewed link to a derived one.
    """
    if matter is None:
        raise LinkRefused("Seos vajab olemasolevat teemat.")
    if basis not in ArchiveLinkBasis.values:
        raise LinkRefused("Tundmatu seose alus.")
    if basis == ArchiveLinkBasis.REVIEWED and not _is_person(actor):
        # The basis is a claim about how the link came to be believed. A
        # REVIEWED row with nobody behind it would put a reviewer's authority
        # on a machine's guess.
        raise LinkRefused("Ülevaatusel kinnitatud seos vajab kasutajat.")

    existing = OpinionArchiveMatterLink.objects.filter(binary=binary, matter=matter).first()
    if existing is not None:
        if existing.basis != ArchiveLinkBasis.REVIEWED and basis == ArchiveLinkBasis.REVIEWED:
            # A person confirming what the machine derived is an upgrade worth
            # recording; the reverse never happens, because a later derivation
            # cannot un-review a decision.
            existing.basis = ArchiveLinkBasis.REVIEWED
            existing.linked_by = actor if _is_person(actor) else None
            existing.linked_at = timezone.now()
            if note.strip():
                existing.note = note.strip()
            existing.save(update_fields=["basis", "linked_by", "linked_at", "note", "updated_at"])
        return existing, False

    link = OpinionArchiveMatterLink.objects.create(
        binary=binary,
        matter=matter,
        item=item,
        candidate=candidate,
        basis=basis,
        linked_by=actor if _is_person(actor) else None,
        linked_at=timezone.now(),
        note=note.strip(),
    )
    return link, True


@transaction.atomic
def unlink_matter(*, binary: OpinionArchiveBinary, matter: Any, actor: Any = None) -> None:
    """Withdraw a relationship a person recorded, and only that."""
    link = OpinionArchiveMatterLink.objects.filter(binary=binary, matter=matter).first()
    if link is None:
        return
    if link.basis in PROTECTED_BASES:
        raise LinkRefused(
            "Seos tugineb kanoonilisele arvamusele. Eemalda see arvamuse juurest, mitte arhiivist."
        )
    if not _is_person(actor):
        raise LinkRefused("Seose eemaldamine vajab kasutajat.")
    link.delete()


def derive_links() -> LinkReport:
    """Create the links that follow from evidence already accepted.

    Two derivations, both exact:

    * a candidate in an **automatic** match class — the classes an apply may
      act on without a person, every one of them resting on an exact identity
      rather than a resemblance;
    * an **applied** Submission — the file is demonstrably filed against that
      Matter, so the archive page saying otherwise would simply be out of date.

    Nothing here touches a REVIEWED link, and nothing here removes anything.
    Running it twice changes nothing.
    """
    from app.legacy_import.opinion_archive import OpinionMatchCandidate

    report = LinkReport()

    automatic = (
        OpinionMatchCandidate.objects.filter(
            matter__isnull=False,
            match_class__in=AUTOMATIC_MATCH_CLASSES,
            item__binary__isnull=False,
        )
        .exclude(state=OpinionCandidateState.SUPERSEDED)
        .select_related("item", "item__binary", "matter")
    )
    for candidate in automatic.iterator():
        report.considered += 1
        _record(
            report,
            binary=candidate.item.binary,
            matter=candidate.matter,
            basis=ArchiveLinkBasis.EXACT_BINARY,
            item=candidate.item,
            candidate=candidate,
            note=f"Klass {candidate.match_class}.",
        )

    applied = OpinionSubmissionImport.objects.filter(item__binary__isnull=False).select_related(
        "item", "item__binary", "submission", "submission__matter"
    )
    for record in applied.iterator():
        report.considered += 1
        _record(
            report,
            binary=record.item.binary,
            matter=record.submission.matter,
            basis=ArchiveLinkBasis.APPLIED_SUBMISSION,
            item=record.item,
            candidate=record.candidate,
            note="Kanoonilise arvamuse kaudu.",
        )
    return report


def _record(report: LinkReport, *, binary: Any, matter: Any, basis: str, **extra: Any) -> None:
    try:
        _, created = link_matter(binary=binary, matter=matter, basis=basis, **extra)
    except LinkRefused as error:
        report.findings.append(str(error))
        return
    if created:
        report.created += 1
    else:
        report.unchanged += 1


def links_for(binary: OpinionArchiveBinary) -> list[OpinionArchiveMatterLink]:
    """Every Matter this file is believed to concern, reviewed ones first."""
    return list(
        OpinionArchiveMatterLink.objects.filter(binary=binary)
        .select_related("matter", "linked_by", "candidate")
        .order_by("basis", "matter__title")
    )


def occurrences_for(binary: OpinionArchiveBinary) -> list[OpinionArchiveItem]:
    """Every path in the archive that holds these exact bytes."""
    return list(
        OpinionArchiveItem.objects.filter(binary=binary)
        .select_related("batch")
        .prefetch_related("metadata_rows")
        .order_by("archive_relative_path")
    )


def _is_person(actor: Any) -> bool:
    return actor is not None and bool(getattr(actor, "is_authenticated", False))


def link_findings() -> list[str]:
    """Aggregate integrity checks over archive-to-Matter relationships."""
    findings: list[str] = []

    reviewed_without_person = OpinionArchiveMatterLink.objects.filter(
        basis=ArchiveLinkBasis.REVIEWED, linked_by__isnull=True
    ).count()
    if reviewed_without_person:
        findings.append(f"{reviewed_without_person} ülevaatusel kinnitatud seosel puudub kinnitaja")

    crossed = (
        OpinionArchiveMatterLink.objects.filter(item__isnull=False)
        .exclude(item__binary_id=models.F("binary_id"))
        .count()
    )
    if crossed:
        findings.append(f"{crossed} seose arhiivikirje ei kuulu selle baidi juurde")

    return findings
