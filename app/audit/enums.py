from __future__ import annotations

from django.db import models


class ChangeEventType(models.TextChoices):
    """Authoritative business changes shown in the professional timeline.

    Stage 0 seeds only the events the foundational schema can already produce;
    Stage 1 adds the workflow events (stage change, next action, submission).
    """

    MATTER_CREATED = "MATTER_CREATED", "Teema loodud"
    MATTER_ASSIGNED = "MATTER_ASSIGNED", "Teema määratud"
    MATTER_VISIBILITY_CHANGED = "MATTER_VISIBILITY_CHANGED", "Nähtavus muudetud"
    MATTER_CLOSED = "MATTER_CLOSED", "Teema suletud"
    MATTER_REOPENED = "MATTER_REOPENED", "Teema taasavatud"
    DOCUMENT_CREATED = "DOCUMENT_CREATED", "Dokument loodud"
    EVIDENCE_VERSION_ADDED = "EVIDENCE_VERSION_ADDED", "Tõendiversioon lisatud"
    TAG_ASSIGNED = "TAG_ASSIGNED", "Silt lisatud"
    TAG_REMOVED = "TAG_REMOVED", "Silt eemaldatud"
    IMPORT_APPLIED = "IMPORT_APPLIED", "Import rakendatud"


class SecurityEventType(models.TextChoices):
    """Access, permission and administrative trace, separate from the timeline."""

    BREAK_GLASS_GRANTED = "BREAK_GLASS_GRANTED", "Hädaligipääs antud"
    BREAK_GLASS_REVOKED = "BREAK_GLASS_REVOKED", "Hädaligipääs tühistatud"
    BREAK_GLASS_USED = "BREAK_GLASS_USED", "Hädaligipääsu kasutati"
    RESTRICTED_RECORD_ACCESSED = "RESTRICTED_RECORD_ACCESSED", "Piiratud kirjet vaadati"
    DOCUMENT_DOWNLOADED = "DOCUMENT_DOWNLOADED", "Dokument alla laaditud"
    EXPORT_GENERATED = "EXPORT_GENERATED", "Väljavõte koostatud"
    IMPORT_RUN = "IMPORT_RUN", "Import käivitatud"
    ROLE_CHANGED = "ROLE_CHANGED", "Roll muudetud"
    AUTHENTICATION_SUCCEEDED = "AUTHENTICATION_SUCCEEDED", "Sisselogimine õnnestus"
    AUTHENTICATION_FAILED = "AUTHENTICATION_FAILED", "Sisselogimine ebaõnnestus"
