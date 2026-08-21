from __future__ import annotations

from django.db import models


class ChangeEventType(models.TextChoices):
    """Authoritative business changes shown in the professional timeline.

    Stage 0 seeds only the events the foundational schema can already produce;
    Stage 1 adds the workflow events (stage change, next action, submission).
    """

    MATTER_CREATED = "MATTER_CREATED", "Teema loodud"
    MATTER_ASSIGNED = "MATTER_ASSIGNED", "Teema määratud"
    MATTER_STAGE_CHANGED = "MATTER_STAGE_CHANGED", "Hetkeseis muudetud"
    MATTER_TRACK_CHANGED = "MATTER_TRACK_CHANGED", "Menetlusliik muudetud"
    MATTER_ORGANISATION_CHANGED = "MATTER_ORGANISATION_CHANGED", "Asutus muudetud"
    MATTER_DATE_CHANGED = "MATTER_DATE_CHANGED", "Kuupäev muudetud"
    MATTER_POSITION_UPDATED = "MATTER_POSITION_UPDATED", "Seisukohta täiendatud"
    MATTER_VISIBILITY_CHANGED = "MATTER_VISIBILITY_CHANGED", "Nähtavus muudetud"
    MATTER_CLOSED = "MATTER_CLOSED", "Teema suletud"
    MATTER_REOPENED = "MATTER_REOPENED", "Teema taasavatud"
    # An archive register record activated as current work. Distinct from
    # MATTER_CREATED — nothing was created, the identity and the provenance are
    # the ones the register already had — and from MATTER_REOPENED, which is
    # about a closure being undone (master specification 19.4).
    MATTER_PROMOTED = "MATTER_PROMOTED", "Arhiivikirjest aktiivne teema"
    NEXT_ACTION_SET = "NEXT_ACTION_SET", "Järgmiseks määratud"
    NEXT_ACTION_COMPLETED = "NEXT_ACTION_COMPLETED", "Järgmiseks tehtud"
    NEXT_ACTION_CANCELLED = "NEXT_ACTION_CANCELLED", "Järgmiseks tühistatud"
    NEXT_ACTION_REVIEWED = "NEXT_ACTION_REVIEWED", "Järgmiseks üle vaadatud"
    ENTRY_ADDED = "ENTRY_ADDED", "Sissekanne lisatud"
    ENTRY_EDITED = "ENTRY_EDITED", "Sissekannet muudetud"
    SUBMISSION_CREATED = "SUBMISSION_CREATED", "Arvamus loodud"
    SUBMISSION_SENT = "SUBMISSION_SENT", "Arvamus välja saadetud"
    SUBMISSION_WITHDRAWN = "SUBMISSION_WITHDRAWN", "Arvamus tagasi võetud"
    SUBMISSION_SUPERSEDED = "SUBMISSION_SUPERSEDED", "Arvamus asendatud"
    SUBMISSION_RECIPIENTS_CHANGED = "SUBMISSION_RECIPIENTS_CHANGED", "Arvamuse saajad muudetud"
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
    # -- the shared gate ---------------------------------------------------
    #
    # Kept distinct from AUTHENTICATION_SUCCEEDED on purpose. Passing a shared
    # password is not somebody signing in; recording it as if it were would put
    # a claim in the audit trail that the deployment cannot support
    # (docs/adr/0016).
    SHARED_GATE_PASSED = "SHARED_GATE_PASSED", "Jagatud parool sisestati"
    SHARED_GATE_CLOSED = "SHARED_GATE_CLOSED", "Jagatud seanss lõpetati"
    PERSONA_SELECTED = "PERSONA_SELECTED", "Kasutajavaade valiti"
