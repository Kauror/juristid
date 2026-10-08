"""Shared harness for the `Arvamuse järelkontroll` tests (docs/adr/0146).

Every helper sends an opinion **through the interactive path a person uses** —
`+ Koja arvamus`, `Märgi saadetuks`, `Registreeri saatmine` — so a test that
asserts something about a follow-up gets one that was scheduled the way the
product schedules it, and never a row planted beside the send.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from django.utils import timezone

from app.documents.enums import DocumentRole
from app.documents.services import add_evidence_version, create_document
from app.matters import workspace
from app.submissions.models import Submission
from app.submissions.services import (
    attach_final_evidence,
    create_submission,
    mark_submission_sent_on_open_matter,
    register_sent_opinion_on_open_matter,
)
from app.workflow.enums import ActionStatus
from app.workflow.models import NextAction, OpinionFollowUp

PDF = b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF"


def day(offset: int) -> dt.date:
    return timezone.localdate() + dt.timedelta(days=offset)


def et(value: dt.date) -> str:
    """`8.10.2026` — how a day is typed into, and printed by, the product."""
    return f"{value.day}.{value.month}.{value.year}"


def pdf(name: str = "arvamus.pdf") -> SimpleUploadedFile:
    return SimpleUploadedFile(name, PDF, content_type="application/pdf")


def send_koja_arvamus(matter: Any, actor: Any, recipients: list[Any], sent_on: dt.date, **kw: Any):
    """`+ Koja arvamus` — the workspace operation the panel posts to."""
    result = workspace.add_matter_koda_opinion(
        matter=matter,
        author=actor,
        recipients=recipients,
        sent_on=sent_on,
        upload=pdf(),
        summary="Sünteetiline arvamus.",
        **kw,
    )
    return result.record


def mark_sent(matter: Any, actor: Any, addressees: list[Any], **kw: Any) -> Submission:
    """`Märgi saadetuks` on a draft — the act that means *now*."""
    draft = create_submission(
        matter=matter, title=kw.pop("title", "Täiendav arvamus"), actor=actor, **kw
    )
    attach_final_evidence(
        submission=draft,
        content=PDF,
        original_filename="taiendav.pdf",
        mime_type="application/pdf",
        actor=actor,
    )
    draft.refresh_from_db()
    return mark_submission_sent_on_open_matter(submission=draft, actor=actor, addressees=addressees)


def register_sent(matter: Any, actor: Any, recipients: list[Any], sent_on: dt.date, **kw: Any):
    """`Registreeri saatmine` — a file already on the Matter, recorded as sent on a day."""
    document = create_document(
        matter=matter,
        title="Saadetud kiri",
        role=DocumentRole.KODA_SUBMISSION_FINAL,
        created_by=actor,
    )
    version = add_evidence_version(
        document=document,
        content=PDF + str(id(document)).encode(),
        original_filename="kiri.pdf",
        mime_type="application/pdf",
        uploaded_by=actor,
    )
    moment = timezone.make_aware(dt.datetime.combine(sent_on, dt.time.min))
    from app.submissions.enums import SentAtPrecision

    return register_sent_opinion_on_open_matter(
        document=document,
        version=version,
        title=kw.pop("title", "Registreeritud arvamus"),
        actor=actor,
        recipients=recipients,
        sent_at=moment,
        sent_at_precision=SentAtPrecision.DATE,
        **kw,
    )


def follow_up_of(submission: Submission) -> OpinionFollowUp:
    return OpinionFollowUp.objects.get(submission=submission)


def active_check(submission: Submission) -> NextAction:
    return NextAction.objects.get(
        follow_up__submission=submission,
        status__in=(ActionStatus.OPEN, ActionStatus.PLANNED),
    )


def checks_of(submission: Submission) -> list[NextAction]:
    return list(NextAction.objects.filter(follow_up__submission=submission).order_by("created_at"))


def post(client: Any, name: str, matter: Any, data: dict[str, Any], **kwargs: Any):
    return client.post(
        reverse(f"matters:{name}", kwargs={"pk": matter.pk, **kwargs}),
        data,
        headers={"HX-Request": "true"},
    )
