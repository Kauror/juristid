"""Attaching a historical recipient to a Submission that already exists.

The archive knows who Koda wrote to. It knows it as a string — *Rahandusmin.*,
*Keskkonnaministeerium*, a name that has since been merged into another ministry
— and on the day a letter is filed that string very often resolves to no
canonical ``Organisation`` at all. On the approved corpus that is the common
case, not the exception.

Until now that ended the story. ``_attach_recipient`` returned early, and the
next run found an ``OpinionSubmissionImport`` for the occurrence and stopped
before reaching the recipient at all — so a Submission created before its
recipient could be resolved stayed recipientless permanently, and improving the
reference data changed nothing. The information was not lost so much as
unreachable: it sat in a prose note nothing could query.

This module closes that. Three properties, and each one is a deliberate refusal:

**It never creates an Organisation.** A historical spelling is evidence that
somebody was written to, not evidence that a body exists under that name today.
``Keskkonnaministeerium`` and ``Kliimaministeerium`` look alike and are not the
same ministry, and only exact identity or a reviewed alias may bridge one to the
other (docs/adr/0019, Stage-2H brief 22).

**It never changes history.** ``OpinionSubmissionImport.recipient_raw`` is what
the source said and stays what the source said. Resolution adds a
``SubmissionRecipient`` beside it and updates the *basis* to say how the link
came to be believed. Running it again after the reference data improves again
attaches what is newly resolvable and leaves everything else alone.

**It never creates a Submission.** This is a backfill over rows that exist. A
recipient with no submission to attach to is a finding, not a reason to file
one.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from django.db import transaction

from app.legacy_import.opinion_archive import OpinionSubmissionImport
from app.legacy_import.opinion_enums import RecipientBasis
from app.submissions.enums import RecipientRole


@dataclass
class ResolutionReport:
    """What one backfill pass found and, if applying, what it changed."""

    examined: int = 0
    already_attached: int = 0
    resolved: int = 0
    still_unresolved: int = 0
    missing_raw: int = 0
    #: The distinct source strings nothing could resolve, with how often each
    #: appears. This is the operator's actual work list: it says which reviewed
    #: aliases would unblock the most letters, which a count of failures cannot.
    unresolved_values: dict[str, int] = field(default_factory=dict)
    #: Named, so a run that resolved nothing can be told apart from one that
    #: found nothing to do.
    notes: list[str] = field(default_factory=list)

    @property
    def resolvable(self) -> int:
        return self.resolved


def pending_imports() -> Any:
    """Provenance rows whose Submission has no recipient attached yet.

    Read from the provenance side rather than the Submission side on purpose:
    a Submission with no recipient may simply never have had one recorded, and
    this operation may only speak for the ones the archive filed.
    """
    return (
        OpinionSubmissionImport.objects.select_related("submission")
        .exclude(recipient_raw="")
        .order_by("created_at", "pk")
    )


def resolve_recipients(
    *, apply: bool = False, mappings: Any = None, actor: Any = None
) -> ResolutionReport:
    """Attach every recipient that now resolves exactly. Idempotent.

    ``apply=False`` decides everything and writes nothing, which is the mode an
    operator should run first and the mode the readiness audit uses.

    Re-running after a successful pass is a no-op: a Submission that already
    carries the organisation is counted as ``already_attached`` and skipped
    before resolution is even attempted.
    """
    from app.legacy_import.resolution import MappingTables, resolve_organisation
    from app.submissions.models import SubmissionRecipient

    report = ResolutionReport()
    # Reviewed aliases only, and supplied by the caller. Defaulting to a file
    # this module went looking for would make the answer depend on what happened
    # to be on the operator's disk.
    tables = mappings if mappings is not None else MappingTables.empty()

    for row in pending_imports().iterator():
        report.examined += 1
        raw = (row.recipient_raw or "").strip()
        if not raw:  # pragma: no cover - excluded by the queryset
            report.missing_raw += 1
            continue

        if SubmissionRecipient.objects.filter(submission=row.submission).exists():
            report.already_attached += 1
            continue

        resolution = resolve_organisation(raw, tables)
        if resolution.value is None:
            report.still_unresolved += 1
            report.unresolved_values[raw] = report.unresolved_values.get(raw, 0) + 1
            continue

        report.resolved += 1
        if not apply:
            continue

        with transaction.atomic():
            SubmissionRecipient.objects.get_or_create(
                submission=row.submission,
                organisation=resolution.value,
                defaults={"role": RecipientRole.ADDRESSEE, "note": raw[:200]},
            )
            # The basis moves; `recipient_raw` never does. One says how the link
            # came to be believed, the other is what the source wrote, and a
            # backfill that rewrote the second would destroy the evidence it
            # used to make the first.
            if row.recipient_basis == RecipientBasis.UNRESOLVED:
                row.recipient_basis = RecipientBasis.REVIEWED_MAPPING
                row.save(update_fields=["recipient_basis", "updated_at"])

    if report.examined and not report.resolved:
        report.notes.append(
            "Ükski lahendamata saaja ei vasta täpselt ühelegi organisatsioonile ega "
            "ülevaadatud vastendusele. Uusi organisatsioone siin ei looda."
        )
    return report
