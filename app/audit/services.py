"""The only supported way to write audit history."""

from __future__ import annotations

import uuid
from typing import Any

from django.apps import apps
from django.db import models

from app.audit.models import ChangeEvent, SecurityAuditEvent
from app.audit.operations import current_operation_id, pin_stage_episode, pinned_stage_episode

#: «Nobody said» for ``stage_episode``, so ``None`` can mean «no period».
_FROM_CONTEXT: Any = object()


def _object_reference(instance: models.Model | None) -> tuple[str, uuid.UUID | None]:
    if instance is None:
        return "", None
    label = f"{instance._meta.app_label}.{instance._meta.object_name}"
    return label, getattr(instance, "pk", None)


def current_stage_episode_id(matter_id: Any) -> uuid.UUID | None:
    """The `Hetkeseis` period a write on this Matter belongs to right now.

    The period this save pinned, if it pinned one; otherwise the Matter's
    current period, which is then pinned for the rest of the save — so a stage
    move later in the same save does not split it (docs/adr/0131 §5). ``None``
    when the Matter has no current period, which is never pinned: a save that
    then opens the Matter's first period binds to it (`pin_stage_episode`).
    """
    pinned = pinned_stage_episode(matter_id)
    if pinned is not None:
        return pinned
    episode = apps.get_model("matters", "MatterStageEpisode")
    current = (
        episode.objects.filter(matter_id=matter_id, is_current=True)
        .values_list("pk", flat=True)
        .first()
    )
    if current is None:
        return None
    return pin_stage_episode(matter_id, current)


def record_change_event(
    *,
    event_type: str,
    matter: Any = None,
    actor: Any = None,
    obj: models.Model | None = None,
    summary: str = "",
    payload: dict[str, Any] | None = None,
    stage_episode: Any = _FROM_CONTEXT,
) -> ChangeEvent:
    """Record an authoritative business change.

    Call this inside the same transaction as the change it describes.

    ``stage_episode`` is which `Hetkeseis` period the change was made in, and a
    caller names it only when it knows better than the context: the stage
    move itself opens the period it moves to, and a closure or reopening made
    by a stage belongs to the period that stage began. Everybody else leaves it
    alone and the period is read off the save (`current_stage_episode_id`).
    """
    object_type, object_id = _object_reference(obj)
    if stage_episode is not _FROM_CONTEXT:
        episode_id = getattr(stage_episode, "pk", stage_episode)
    elif matter is None:
        episode_id = None
    else:
        episode_id = current_stage_episode_id(getattr(matter, "pk", matter))
    return ChangeEvent.objects.create(
        event_type=event_type,
        matter=matter,
        actor=actor,
        object_type=object_type,
        object_id=object_id,
        summary=summary,
        payload=payload or {},
        # Read from the execution context rather than taken as an argument, so
        # that a service three calls deep inside a composer save cannot forget
        # to pass it on (app/audit/operations.py).
        operation_id=current_operation_id(),
        stage_episode_id=episode_id,
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
