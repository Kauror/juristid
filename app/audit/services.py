"""The only supported way to write audit history."""

from __future__ import annotations

import uuid
from typing import Any

from django.db import models

from app.audit.models import ChangeEvent, SecurityAuditEvent


def _object_reference(instance: models.Model | None) -> tuple[str, uuid.UUID | None]:
    if instance is None:
        return "", None
    label = f"{instance._meta.app_label}.{instance._meta.object_name}"
    return label, getattr(instance, "pk", None)


def record_change_event(
    *,
    event_type: str,
    matter: Any = None,
    actor: Any = None,
    obj: models.Model | None = None,
    summary: str = "",
    payload: dict[str, Any] | None = None,
) -> ChangeEvent:
    """Record an authoritative business change.

    Call this inside the same transaction as the change it describes.
    """
    object_type, object_id = _object_reference(obj)
    return ChangeEvent.objects.create(
        event_type=event_type,
        matter=matter,
        actor=actor,
        object_type=object_type,
        object_id=object_id,
        summary=summary,
        payload=payload or {},
    )


def record_security_event(
    *,
    event_type: str,
    actor: Any = None,
    subject: models.Model | None = None,
    ip_address: str | None = None,
    user_agent: str = "",
    succeeded: bool = True,
    detail: dict[str, Any] | None = None,
) -> SecurityAuditEvent:
    subject_type, subject_id = _object_reference(subject)
    return SecurityAuditEvent.objects.create(
        event_type=event_type,
        actor=actor,
        subject_type=subject_type,
        subject_id=subject_id,
        ip_address=ip_address,
        user_agent=user_agent[:400],
        succeeded=succeeded,
        detail=detail or {},
    )
