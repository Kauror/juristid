"""`Submission` — one outbound written advocacy action.

This is the canonical record of what Koda actually sent, and the entity every
future opinion count is derived from. There is deliberately no
`Matter.opinion_sent_date`: a Matter can produce several submissions, and a
single column on the Matter could only ever record one of them
(master specification 11.2, 18.2).
"""

from __future__ import annotations

from django.conf import settings
from django.db import models

from app.core.authorization import apply as apply_scope
from app.core.authorization import child_visibility_q, scope_for_user
from app.core.enums import Visibility
from app.core.models import VisibilityInheritingModel
from app.submissions.enums import SubmissionKind, SubmissionStatus


class SubmissionQuerySet(models.QuerySet):
    def visible_to(self, user: object | None) -> SubmissionQuerySet:
        return apply_scope(self, child_visibility_q(scope_for_user(user)))

    def sent(self) -> SubmissionQuerySet:
        return self.filter(status=SubmissionStatus.SENT)


class Submission(VisibilityInheritingModel):
    matter = models.ForeignKey(
        "matters.Matter",
        on_delete=models.CASCADE,
        related_name="submissions",
        verbose_name="teema",
    )
    kind = models.CharField(
        max_length=40,
        choices=SubmissionKind.choices,
        default=SubmissionKind.FORMAL_OPINION,
        db_index=True,
        verbose_name="liik",
    )
    title = models.CharField(max_length=400, verbose_name="pealkiri")
    status = models.CharField(
        max_length=16,
        choices=SubmissionStatus.choices,
        default=SubmissionStatus.DRAFT,
        db_index=True,
        verbose_name="olek",
    )

    recipients = models.ManyToManyField(
        "organisations.Organisation",
        blank=True,
        related_name="received_submissions",
        verbose_name="adressaadid",
    )
    joint_submitters = models.ManyToManyField(
        "organisations.Organisation",
        blank=True,
        related_name="joint_submissions",
        verbose_name="kaasesitajad",
        help_text="Teised organisatsioonid, kelle nimel pöördumine ühiselt esitati.",
    )

    sent_at = models.DateTimeField(null=True, blank=True, db_index=True, verbose_name="saadetud")
    channel = models.CharField(
        max_length=200,
        blank=True,
        verbose_name="kanal",
        help_text="Näiteks EIS, e-post või dokumendiregistri viide.",
    )
    reference = models.CharField(max_length=200, blank=True, verbose_name="viide")

    # The exact binary that was sent. Immutable once captured; a correction is a
    # new version, and a new decision is a new Submission.
    final_version = models.ForeignKey(
        "documents.DocumentVersion",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="finalised_submissions",
        verbose_name="lõplik tõend",
    )
    working_document = models.ForeignKey(
        "documents.Document",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="draft_submissions",
        verbose_name="töödokument",
    )

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="created_submissions",
    )
    sent_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="sent_submissions",
    )
    notes = models.TextField(blank=True, verbose_name="märkused")

    objects = SubmissionQuerySet.as_manager()

    class Meta:
        verbose_name = "väljasaadetud arvamus"
        verbose_name_plural = "väljasaadetud arvamused"
        ordering = ["-sent_at", "-created_at"]
        constraints = [
            models.CheckConstraint(
                condition=~models.Q(title=""),
                name="submissions_title_required",
            ),
            # A sent submission without its exact final text is an unverifiable
            # claim about what Koda argued. The database refuses to hold one.
            models.CheckConstraint(
                condition=(
                    ~models.Q(status=SubmissionStatus.SENT)
                    | models.Q(sent_at__isnull=False, final_version__isnull=False)
                ),
                name="submissions_sent_requires_timestamp_and_evidence",
            ),
            models.CheckConstraint(
                condition=models.Q(
                    visibility_override__in=["", Visibility.NORMAL, Visibility.RESTRICTED]
                ),
                name="submissions_visibility_vocabulary",
            ),
        ]
        indexes = [
            models.Index(fields=["matter", "-sent_at"], name="submissions_matter_sent"),
            models.Index(fields=["status", "-sent_at"], name="submissions_status_sent"),
        ]

    def __str__(self) -> str:
        return self.title

    def parent_visibility(self) -> str:
        return self.matter.visibility

    @property
    def is_sent(self) -> bool:
        return self.status == SubmissionStatus.SENT

    @property
    def has_final_evidence(self) -> bool:
        return self.final_version_id is not None
