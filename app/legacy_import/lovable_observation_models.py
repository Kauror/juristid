"""Wider, read-only observation of the Lovable register (docs/adr/0201).

Observation only. Nothing here is a baseline for the pilot's reconciliation
(docs/adr/0192), nothing is eligible for APPLY, and no canonical Juristid
record is written from it. Four kinds of record:

* `LovableObservationRun` — one reading of the whole live register: when,
  whether it verified, aggregate counts only (never business text), and
  whether the source's own change history could be read;
* `LovableObservationSnapshot` — that reading: a bounded index of **every** live
  topic (identity, number, version, operational flag, content hash) and the
  allowlisted fields and child records of the operational and pilot topics.
  Confidential; deleted after `OBSERVATION_RETENTION_DAYS`;
* `LovableObservedChange` — one topic's net difference between a reading and
  the previous verified reading (new, changed, missing, back, renumbered,
  operational scope in or out). A *snapshot difference*, never a source event;
* `LovableSourceEvent` / `LovableSourceEventField` — Lovable's own audit
  ledger (`topic_change_event` / `topic_change_field`), one row per source event,
  collected only when an independently authorised read-only access exists
  (`audit_access`). Disabled until then: no row is ever invented.

Populations (`ObservationScope`) are kept apart everywhere they are counted:
PILOT (the 100 Teemad of docs/adr/0191), OPERATIONAL (live topics with
``operatiivne_ulatus``), ALL_LIVE (every live topic) and SOURCE_HISTORY (the
audit ledger). A count of one is never compared with a count of another
without naming both.
"""

from __future__ import annotations

from django.db import models

from app.core.models import BaseModel

#: The observation document's own schema; bumped when its shape changes.
OBSERVATION_SCHEMA_VERSION = "lovable-observe/1"
#: Raw observation data (snapshots, per-topic changes, audit rows) is kept this
#: long — the 30-day window the owner approved for the pilot's snapshots
#: (docs/adr/0192 §3), applied here pending any longer approval. Run rows and
#: their aggregate counts carry no business text and are kept.
OBSERVATION_RETENTION_DAYS = 30


class ObservationScope(models.TextChoices):
    PILOT = "PILOT", "Piloot (100 teemat)"
    OPERATIONAL = "OPERATIONAL", "Operatiivsed teemad"
    ALL_LIVE = "ALL_LIVE", "Kõik elavad teemad"
    SOURCE_HISTORY = "SOURCE_HISTORY", "Lovable'i muutmisajalugu"


class ObservationRunStatus(models.TextChoices):
    RUNNING = "RUNNING", "Käib"
    SUCCEEDED = "SUCCEEDED", "Õnnestus"
    FAILED = "FAILED", "Ebaõnnestus"


class AuditAccess(models.TextChoices):
    #: Not switched on: no authorised read-only access to the audit ledger exists.
    DISABLED = "DISABLED", "Pole lubatud"
    #: Switched on, but the identity could not read it (refused, or 0 rows where
    #: rows must exist — under row-level security the two look alike).
    UNAVAILABLE = "UNAVAILABLE", "Pole kättesaadav"
    AVAILABLE = "AVAILABLE", "Loetav"


class ObservedChangeKind(models.TextChoices):
    NEW = "NEW", "Uus teema"
    CHANGED = "CHANGED", "Muutunud"
    MISSING = "MISSING", "Kadus lugemisest"
    BACK = "BACK", "Taas lugemises"
    RENUMBERED = "RENUMBERED", "Number muutus"


class MappingStatus(models.TextChoices):
    """How a source topic relates to Juristid. Never decided by title."""

    #: One of the pilot's Teemad, through its approved provenance (`LovableSyncState`).
    PILOT_LINKED = "PILOT_LINKED", "Piloodi teema"
    #: A Juristid Teema carries the same reference (`aasta_nr`), but no approved
    #: provenance links them: a candidate a person must confirm, never a link.
    REFERENCE_CANDIDATE = "REFERENCE_CANDIDATE", "Sama numbriga teema, seos kinnitamata"
    UNMATCHED = "UNMATCHED", "Juristides vastet pole"


class LovableObservationRun(BaseModel):
    """One reading of the live register. ``run_key`` makes a scheduled night once."""

    run_key = models.CharField(max_length=48, unique=True)
    #: `SyncTrigger` values: SCHEDULED or MANUAL (a check never reads the source).
    trigger = models.CharField(max_length=16)
    status = models.CharField(
        max_length=16,
        choices=ObservationRunStatus.choices,
        default=ObservationRunStatus.RUNNING,
    )
    started_at = models.DateTimeField()
    finished_at = models.DateTimeField(null=True, blank=True)
    #: The pilot night this reading accompanied, if any (never its baseline).
    sync_run = models.ForeignKey(
        "legacy_import.LovableSyncRun",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="observations",
    )
    schema_version = models.CharField(max_length=40, default=OBSERVATION_SCHEMA_VERSION)
    taken_at = models.DateTimeField(null=True, blank=True)
    sha256 = models.CharField(max_length=64, blank=True)
    #: The verified reading this one was compared with (``None``: first reading).
    baseline = models.ForeignKey(
        "self", on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    #: Aggregates only (`lovable_observation.ObservationCounts`): never a title,
    #: a name or a value.
    counts = models.JSONField(default=dict, blank=True)
    audit_access = models.CharField(
        max_length=16, choices=AuditAccess.choices, default=AuditAccess.DISABLED
    )
    #: A short code with no business content.
    audit_error = models.CharField(max_length=120, blank=True)
    error = models.CharField(max_length=300, blank=True)

    class Meta:
        verbose_name = "Lovable vaatluse käivitus"
        ordering = ["-started_at"]


class LovableObservationSnapshot(BaseModel):
    """The reading itself. ``document`` holds the index of every live topic and
    the allowlisted detail of operational and pilot topics; nothing excluded by
    docs/adr/0192 §5 (``markmed``, ``mitteametlik_arvamus``) is ever in it."""

    run = models.OneToOneField(
        LovableObservationRun, on_delete=models.CASCADE, related_name="snapshot"
    )
    taken_at = models.DateTimeField()
    sha256 = models.CharField(max_length=64)
    document = models.JSONField()

    class Meta:
        verbose_name = "Lovable vaatluse lähteseis"
        ordering = ["-taken_at"]


class LovableObservedChange(BaseModel):
    """One topic's net difference between two verified readings.

    ``fields`` is a list of ``{"field", "before", "after"}`` with canonical
    values (`lovable_sync.SOURCE_FIELDS` extractors), present only for topics
    whose detail was read (operational or pilot) on both sides; for other
    topics only ``field_names`` is known — that the content hash moved — and
    no value is stored. A value changed A → B → A between readings is *no*
    change here; only the audit ledger can show it.
    """

    run = models.ForeignKey(LovableObservationRun, on_delete=models.CASCADE, related_name="changes")
    source_id = models.UUIDField()
    reference = models.CharField(max_length=20)
    #: The number before a renumbering (``RENUMBERED`` only).
    previous_reference = models.CharField(max_length=20, blank=True)
    kind = models.CharField(max_length=16, choices=ObservedChangeKind.choices)
    in_pilot = models.BooleanField(default=False)
    operational = models.BooleanField(default=False)
    #: ``operatiivne_ulatus`` before → after differs.
    scope_changed = models.BooleanField(default=False)
    #: Whether child records (commencements, responsible changes, stage links)
    #: changed while the topic row did not.
    children_only = models.BooleanField(default=False)
    detailed = models.BooleanField(default=False)
    field_names = models.JSONField(default=list, blank=True)
    fields = models.JSONField(default=list, blank=True)
    version_before = models.IntegerField(null=True, blank=True)
    version_after = models.IntegerField(null=True, blank=True)
    mapping = models.CharField(max_length=24, choices=MappingStatus.choices)
    #: The Juristid Teema: the pilot's linked one, or a reference candidate.
    matter = models.ForeignKey(
        "matters.Matter", on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )

    class Meta:
        verbose_name = "Lovable'is täheldatud muudatus"
        ordering = ["-created_at", "reference"]
        indexes = [
            models.Index(fields=["source_id", "created_at"]),
            models.Index(fields=["run", "kind"]),
        ]
        constraints = [
            models.UniqueConstraint(fields=["run", "source_id"], name="lovable_observed_once"),
        ]


class LovableSourceEvent(BaseModel):
    """One event of Lovable's own audit ledger, as Lovable recorded it.

    Collected only through an authorised read-only path (`AuditAccess.AVAILABLE`).
    ``source_event_id`` is the ledger's own id; the same event read twice is one
    row. The actor is kept only as its *kind* (user / system), never an identity.
    """

    source_event_id = models.CharField(max_length=64, unique=True)
    topic_id = models.UUIDField(db_index=True)
    action = models.CharField(max_length=32)
    actor_kind = models.CharField(max_length=32, blank=True)
    version = models.IntegerField(null=True, blank=True)
    occurred_at = models.DateTimeField(db_index=True)
    run = models.ForeignKey(
        LovableObservationRun, on_delete=models.SET_NULL, null=True, related_name="source_events"
    )
    #: Canonical SHA-256 of the event and its field rows as read.
    sha256 = models.CharField(max_length=64)

    class Meta:
        verbose_name = "Lovable'i lähtesündmus"
        ordering = ["-occurred_at", "-source_event_id"]


class LovableSourceEventField(BaseModel):
    """One field of a source event. ``withheld`` when the field is outside the
    allowlist: that it changed is kept, its values are not."""

    event = models.ForeignKey(LovableSourceEvent, on_delete=models.CASCADE, related_name="fields")
    field = models.CharField(max_length=64)
    old_value = models.TextField(blank=True)
    new_value = models.TextField(blank=True)
    withheld = models.BooleanField(default=False)

    class Meta:
        verbose_name = "Lovable'i lähtesündmuse väli"
        ordering = ["field"]
        constraints = [
            models.UniqueConstraint(fields=["event", "field"], name="lovable_event_field_once"),
        ]


class LovableAuditCursor(models.Model):
    """Where audit ingestion resumes (``pk=1``): the last event fully stored, by
    (``occurred_at``, ``source_event_id``) — never a wall-clock guess."""

    id = models.PositiveSmallIntegerField(primary_key=True, default=1, editable=False)
    last_occurred_at = models.DateTimeField(null=True, blank=True)
    last_event_id = models.CharField(max_length=64, blank=True)
    backfill_complete = models.BooleanField(default=False)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Lovable'i muutmisajaloo kursor"
        constraints = [
            models.CheckConstraint(condition=models.Q(id=1), name="lovable_audit_cursor_singleton"),
        ]

    def __str__(self) -> str:
        return f"audit cursor {self.last_occurred_at} {self.last_event_id}"
