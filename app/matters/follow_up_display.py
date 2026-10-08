"""Which opinion a follow-up check is about, as a reader may be told (docs/adr/0146 §7).

A check's own sentence — «Kontrolli, kas adressaat on Koja arvamusele
vastanud» — does not say which letter. One Matter may have several sent
opinions, each with its own check, so every surface that shows a check also
shows its opinion in one compact line: «Koja arvamus 8.10.2026 ·
Rahandusministeerium», with a link to the exact bytes that went out.

**Read through the opinion's own visibility, never copied.** The check is
restricted with its opinion when it is created, so a reader who sees the check
normally sees the opinion too; this does not rely on that. The opinion is read
through `Submission.objects.visible_to`, its letter through
`Document.objects.visible_to`, and a check whose opinion the reader may not see
names nothing — its line is simply absent, never a title or an addressee.

One query for the follow-up rows, one for the opinions with their addressees
and one for the letters, for a whole page — and none on a page without a check.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
from typing import Any

from django.urls import reverse
from django.utils import timezone

#: The label the line opens with. «Koja arvamus» is what the rail, the
#: launcher and the chronology already call a sent opinion of the Chamber.
SUBJECT_LABEL = "Koja arvamus"


@dataclass(frozen=True)
class FollowUpSubject:
    """The opinion one check is about, as far as this reader may know it."""

    submission_id: Any
    title: str
    sent_on: date | None
    addressees: tuple[str, ...]
    #: The `DocumentVersion` that went out, when the reader may open it.
    letter_version_id: Any = None

    @property
    def sent_label(self) -> str:
        from app.core.dates import format_estonian_date

        return format_estonian_date(self.sent_on)

    @property
    def addressee_text(self) -> str:
        return ", ".join(self.addressees)

    @property
    def label(self) -> str:
        """«Koja arvamus 8.10.2026 · Rahandusministeerium, Riigikogu»."""
        head = f"{SUBJECT_LABEL} {self.sent_label}".strip()
        return " · ".join(part for part in (head, self.addressee_text) if part)

    @property
    def letter_url(self) -> str:
        if self.letter_version_id is None:
            return ""
        return reverse("documents:download", kwargs={"pk": self.letter_version_id})


def follow_up_subjects(actions: Iterable[Any], viewer: Any) -> dict[Any, FollowUpSubject]:
    """`{follow_up_id: FollowUpSubject}` for the checks among ``actions`` this reader may read."""
    from app.documents.models import Document
    from app.submissions.models import ADDRESSEE_ROWS, Submission, addressee_prefetch
    from app.workflow.models import OpinionFollowUp

    follow_up_ids = {
        action.follow_up_id
        for action in actions
        if getattr(action, "follow_up_id", None) is not None
    }
    if not follow_up_ids or viewer is None:
        return {}
    submission_of = dict(
        OpinionFollowUp.objects.filter(pk__in=follow_up_ids).values_list("pk", "submission_id")
    )
    submissions = {
        submission.pk: submission
        for submission in Submission.objects.visible_to(viewer)
        .filter(pk__in=set(submission_of.values()))
        .select_related("final_version")
        .prefetch_related(addressee_prefetch())
    }
    letters = {
        submission.final_version.document_id
        for submission in submissions.values()
        if submission.final_version is not None
    }
    openable = set(
        Document.objects.visible_to(viewer).filter(pk__in=letters).values_list("pk", flat=True)
    )
    subjects: dict[Any, FollowUpSubject] = {}
    for follow_up_id, submission_id in submission_of.items():
        submission = submissions.get(submission_id)
        if submission is None:
            continue
        version = submission.final_version
        subjects[follow_up_id] = FollowUpSubject(
            submission_id=submission.pk,
            title=submission.title,
            sent_on=timezone.localdate(submission.sent_at) if submission.sent_at else None,
            addressees=tuple(
                row.organisation.name for row in getattr(submission, ADDRESSEE_ROWS, [])
            ),
            letter_version_id=(
                version.pk if version is not None and version.document_id in openable else None
            ),
        )
    return subjects


def attach_follow_up_subjects(actions: Iterable[Any], viewer: Any) -> None:
    """Give every action a ``follow_up_subject`` — its opinion's line, or ``None``."""
    actions = [action for action in actions if action is not None]
    subjects = follow_up_subjects(actions, viewer)
    for action in actions:
        action.follow_up_subject = subjects.get(getattr(action, "follow_up_id", None))
