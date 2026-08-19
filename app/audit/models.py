"""Two append-only histories, deliberately separate.

``ChangeEvent`` is the authoritative business change history that the
professional timeline renders. ``SecurityAuditEvent`` is the access and
administration trace. The master specification forbids a fourth field-history
subsystem, so nothing else records history.
"""

from __future__ import annotations

from django.conf import settings
from django.db import models
from django.utils import timezone

from app.audit.enums import ChangeEventType, SecurityEventType
from app.core.models import AppendOnlyModel


class ChangeEvent(AppendOnlyModel):
    matter = models.ForeignKey(
        "matters.Matter",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="change_events",
        verbose_name="teema",
    )
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="change_events",
        verbose_name="tegija",
    )
    event_type = models.CharField(max_length=64, choices=ChangeEventType.choices, db_index=True)
    occurred_at = models.DateTimeField(default=timezone.now, db_index=True)
    object_type = models.CharField(
        max_length=100,
        blank=True,
        help_text="app_label.ModelName kirjest, mida sündmus puudutab.",
    )
    object_id = models.UUIDField(null=True, blank=True)
    summary = models.TextField(blank=True)
    # Free-text business content stays in its own record; the payload carries
    # identifiers and small scalar before/after values, not copied narrative.
    payload = models.JSONField(default=dict, blank=True)

    class Meta:
        verbose_name = "muudatussündmus"
        verbose_name_plural = "muudatussündmused"
        ordering = ["-occurred_at", "-created_at"]
        indexes = [
            models.Index(fields=["matter", "-occurred_at"], name="audit_change_matter_time"),
        ]

    def __str__(self) -> str:
        return f"{self.event_type} @ {self.occurred_at:%Y-%m-%d %H:%M}"


class SecurityAuditEvent(AppendOnlyModel):
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="security_events",
        verbose_name="tegija",
    )
    event_type = models.CharField(max_length=64, choices=SecurityEventType.choices, db_index=True)
    occurred_at = models.DateTimeField(default=timezone.now, db_index=True)
    subject_type = models.CharField(max_length=100, blank=True)
    subject_id = models.UUIDField(null=True, blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.CharField(max_length=400, blank=True)
    succeeded = models.BooleanField(default=True)
    # Never contains document bytes or business narrative.
    detail = models.JSONField(default=dict, blank=True)

    class Meta:
        verbose_name = "turvasündmus"
        verbose_name_plural = "turvasündmused"
        ordering = ["-occurred_at", "-created_at"]
        indexes = [
            models.Index(fields=["actor", "-occurred_at"], name="audit_security_actor_time"),
        ]

    def __str__(self) -> str:
        return f"{self.event_type} @ {self.occurred_at:%Y-%m-%d %H:%M}"
