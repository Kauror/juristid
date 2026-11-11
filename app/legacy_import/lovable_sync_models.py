"""The nightly Lovable synchronisation's records (docs/adr/0192, 0202).

The tables, kept apart from the importers' immutable provenance:

* `LovableSyncControl` — one row: whether the job may run, and whether it may
  write. Final cutover sets ``cut_over`` and nothing turns it back;
* `LovableSyncRun` — one row per attempt, with counts, never business text;
* `LovableSyncState` — per synchronised Teema, the **last applied** and the
  **last observed** source values, kept apart so a held change stays pending
  until somebody resolves it rather than vanishing the night after it appeared;
* `LovableSyncDecision` — what each run decided about each field and why. The
  values it compares are business content and are shown only through
  `Matter.objects.visible_to`;
* `LovableSyncCohort` — each cohort **enrolled** after the pilot (docs/adr/0202
  §6): the approved package digest whose provenance defines it and how many
  Teemad it must hold. The pilot (`lovable-pilot`, 100 Teemad) is built in and
  has a row only once a rebuild retires it (``retired_at``);
  `LovableSyncState.cohort` says which cohort a state belongs to;
* `LovableSyncMembership` — that a Teema was a member of a cohort, under
  which Lovable id (REL-01, 2026-11): written when its state is seeded, kept
  under the tombstone when the Teema is deleted, never changed;
* `LovableSyncRetirement` — a member that left its cohort with its Teema's
  permanent deletion (audit 2026-11 C-L-04): the cohort's required size is its
  enrolled size less these, and each names the deletion's audit event and a
  membership of the same cohort, Teema and Lovable id;
* `LovableSyncBaselineArchive` — a rebased Teema's baseline and last
  observation exactly as they were before the rebuild re-seeded its state in
  place (docs/adr/0202 §6). Written once and never changed;
  removed only with its Teema.
"""

from __future__ import annotations

from typing import Any

from django.conf import settings
from django.db import models

from app.core.errors import ImmutableRecordError, InvariantViolation
from app.core.models import AppendOnlyModel, BaseModel


class SyncMode(models.TextChoices):
    OFF = "OFF", "Peatatud"
    COMPARE = "COMPARE", "Ainult võrdlus"
    APPLY = "APPLY", "Võrdlus ja ohutud uuendused"


class SyncTrigger(models.TextChoices):
    SCHEDULED = "SCHEDULED", "Öine käivitus"
    MANUAL = "MANUAL", "Käsitsi käivitus"
    CHECK = "CHECK", "Kontrolli muudatusi"


class SyncRunStatus(models.TextChoices):
    RUNNING = "RUNNING", "Käib"
    SUCCEEDED = "SUCCEEDED", "Õnnestus"
    REVIEW_ONLY = "REVIEW_ONLY", "Peatatud ülevaatuseks"
    FAILED = "FAILED", "Ebaõnnestus"


class SyncOutcome(models.TextChoices):
    UNCHANGED = "UNCHANGED", "Muutusteta"
    LOCAL_TEST_CHANGE = "LOCAL_TEST_CHANGE", "Kohalik testmuudatus"
    ELIGIBLE = "ELIGIBLE", "Ohutu uuendus (rakendamata)"
    APPLIED = "APPLIED", "Uuendatud"
    ALREADY_CONVERGED = "ALREADY_CONVERGED", "Juba kooskõlas"
    CONFLICT = "CONFLICT", "Vastuolu"
    REVIEW_REQUIRED = "REVIEW_REQUIRED", "Vajab ülevaatamist"
    SOURCE_MISSING = "SOURCE_MISSING", "Allikas puudub"
    IDENTIFIER_CHANGED = "IDENTIFIER_CHANGED", "Tunnus muutunud"
    FAILED = "FAILED", "Viga"


class LovableSyncControl(models.Model):
    """The one switch row (``pk=1``). Changed only by `lovable_sync control`."""

    id = models.PositiveSmallIntegerField(primary_key=True, default=1, editable=False)
    mode = models.CharField(max_length=16, choices=SyncMode.choices, default=SyncMode.OFF)
    #: Final cutover: Juristid is the operational source of truth and the
    #: synchronisation never runs again. Set once; `save` refuses to clear it.
    cut_over = models.BooleanField(default=False)
    cut_over_at = models.DateTimeField(null=True, blank=True)
    #: Whether the nightly job also reads the whole live register for
    #: observation (docs/adr/0201). Observation writes no business record and
    #: is never a pilot baseline; off until the owner switches it on.
    observe = models.BooleanField(default=False, db_default=False)
    changed_at = models.DateTimeField(null=True, blank=True)
    changed_reason = models.CharField(max_length=300, blank=True)

    class Meta:
        verbose_name = "Lovable sünkroonimise lüliti"
        constraints = [
            models.CheckConstraint(condition=models.Q(id=1), name="lovable_sync_control_singleton"),
            models.CheckConstraint(
                condition=models.Q(cut_over=False) | models.Q(mode="OFF"),
                name="lovable_sync_cut_over_is_off",
            ),
        ]

    def __str__(self) -> str:
        return "cut over" if self.cut_over else self.mode

    def save(self, *args: Any, **kwargs: Any) -> None:
        if not self._state.adding:
            stored = (
                type(self).objects.filter(pk=self.pk).values_list("cut_over", flat=True).first()
            )
            if stored and not self.cut_over:
                raise ImmutableRecordError("Lõplikku üleminekut ei saa tagasi võtta.")
        super().save(*args, **kwargs)


class LovableSyncRun(BaseModel):
    """One attempt. ``run_key`` is the Tallinn calendar date for the nightly run,
    so a second scheduled attempt on the same date refuses instead of doubling."""

    run_key = models.CharField(max_length=40, unique=True)
    trigger = models.CharField(max_length=16, choices=SyncTrigger.choices)
    mode = models.CharField(max_length=16, choices=SyncMode.choices)
    status = models.CharField(
        max_length=16, choices=SyncRunStatus.choices, default=SyncRunStatus.RUNNING
    )
    started_at = models.DateTimeField()
    finished_at = models.DateTimeField(null=True, blank=True)
    snapshot_taken_at = models.DateTimeField(null=True, blank=True)
    snapshot_sha256 = models.CharField(max_length=64, blank=True)
    mapping_version = models.CharField(max_length=40)
    backup_set = models.CharField(max_length=40, blank=True)
    #: Aggregate counts only (`lovable_sync.RunCounts`); never a title or a name.
    counts = models.JSONField(default=dict, blank=True)
    verified = models.BooleanField(default=False)
    #: A short code and a sentence with no business content.
    error = models.CharField(max_length=300, blank=True)

    class Meta:
        verbose_name = "Lovable sünkroonimise käivitus"
        ordering = ["-started_at"]


class LovableSyncSnapshot(BaseModel):
    """The cohort's source rows as one run read them (docs/adr/0192 §4).

    Confidential and kept only as long as the owner's approval allows
    (`SNAPSHOT_RETENTION_DAYS`); `Kontrolli muudatusi` compares against the
    newest one without reaching Lovable."""

    run = models.OneToOneField(LovableSyncRun, on_delete=models.CASCADE, related_name="snapshot")
    taken_at = models.DateTimeField()
    sha256 = models.CharField(max_length=64)
    document = models.JSONField()

    class Meta:
        verbose_name = "Lovable lähteseis"
        ordering = ["-taken_at"]


class LovableSyncState(BaseModel):
    """Per synchronised Teema: A (last applied) and the last observation."""

    matter = models.OneToOneField(
        "matters.Matter", on_delete=models.CASCADE, related_name="lovable_sync_state"
    )
    source_id = models.UUIDField(unique=True)
    reference = models.CharField(max_length=20)
    #: The cohort this Teema was enrolled with (docs/adr/0202 §6): the pilot's
    #: states are ``lovable-pilot`` by default and are never re-seeded.
    cohort = models.CharField(max_length=64, default="lovable-pilot", db_default="lovable-pilot")
    #: Field key → ``{"source": canonical source value, "target": what Juristid
    #: was given for it}``. Advanced field by field, only when a field is applied,
    #: converges, or a person resolves it.
    applied = models.JSONField(default=dict)
    applied_version = models.IntegerField(null=True, blank=True)
    applied_snapshot_sha256 = models.CharField(max_length=64, blank=True)
    #: Canonical source fields as last observed; never a baseline.
    observed = models.JSONField(default=dict, blank=True)
    observed_version = models.IntegerField(null=True, blank=True)
    observed_run = models.ForeignKey(
        LovableSyncRun, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )

    class Meta:
        verbose_name = "Lovable sünkroonimise seis"
        ordering = ["reference"]


class LovableSyncDecision(BaseModel):
    """What one run decided about one field of one Teema."""

    run = models.ForeignKey(LovableSyncRun, on_delete=models.CASCADE, related_name="decisions")
    state = models.ForeignKey(LovableSyncState, on_delete=models.CASCADE, related_name="decisions")
    field = models.CharField(max_length=40)
    outcome = models.CharField(max_length=24, choices=SyncOutcome.choices)
    reason = models.CharField(max_length=60, blank=True)
    baseline = models.JSONField(null=True, blank=True)
    current = models.JSONField(null=True, blank=True)
    source = models.JSONField(null=True, blank=True)
    #: Set only on an applied write: the same source change applied twice is
    #: refused by the database, whatever the code above it does.
    idempotency_key = models.CharField(max_length=64, null=True, blank=True, unique=True)
    #: The administrator who decided a held change (`lovable_sync.resolve_field`
    #: with an actor, brief 2026-11-09 §7); none for a machine's decision or an
    #: operator's server command.
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="+",
    )

    class Meta:
        verbose_name = "Lovable sünkroonimise otsus"
        ordering = ["state__reference", "field"]


class LovableSyncCohort(BaseModel):
    """One cohort enrolled after the pilot (docs/adr/0202 §6).

    Written once by `lovable_sync enroll`, in the transaction that seeds its
    states. Its Teemad are found only through approved provenance —
    `EvidencePilotRecord(pilot=pilot, package_sha256=package_sha256,
    record_kind="MATTER")` — never by a reference number."""

    key = models.CharField(max_length=64, unique=True)
    #: The `EvidencePilotRecord.pilot` whose records define the cohort.
    pilot = models.CharField(max_length=64)
    package_sha256 = models.CharField(max_length=64)
    #: How many Teemad the cohort must hold; a run with any other number stops.
    expected = models.PositiveIntegerField()
    enrolled_at = models.DateTimeField()
    reason = models.CharField(max_length=300)
    #: When a rebuild superseded this cohort (`enroll --supersede`): its Teemad
    #: belong to the rebuilt cohort since, and the size check no longer expects it.
    retired_at = models.DateTimeField(null=True, blank=True)
    retired_reason = models.CharField(max_length=300, blank=True)

    class Meta:
        verbose_name = "Lovable sünkroonimise kohort"
        ordering = ["enrolled_at"]

    def __str__(self) -> str:
        return self.key


class MembershipBasis(models.TextChoices):
    #: Written by `lovable_sync.seed_state` / `reseed_state`, from the cohort's
    #: approved provenance, in the transaction that seeds the state.
    ENROLMENT = "ENROLMENT", "Kanti kohorti seisu loomisel"
    #: Written by the deletion, under the Teema's row lock and before the purge,
    #: for a state seeded before memberships were recorded (migration 0024): only
    #: when the cohort's approved provenance names that Teema under that Lovable id.
    DELETION = "DELETION", "Kinnitati kustutamisel kohordi päritolu järgi"
    #: Written by the synchronisation when it created the Teema from a new
    #: Lovable topic (docs/adr/0213), in the transaction that created it and
    #: seeded its state. A joined member: the cohort's required size counts it.
    NEW_TOPIC = "NEW_TOPIC", "Lisati kohorti uue Lovable'i teemana"


class LovableSyncMembership(AppendOnlyModel):
    """A Teema was a member of a synchronised cohort, under one Lovable id
    (owner brief 2026-11-06 §17, REL-01).

    The synchronisation state says the same while it exists, but the purge of a
    permanent deletion removes the state, the Teema's source references and its
    import provenance with it (docs/adr/0165), so after a deletion nothing else
    says which cohort the Teema was in or which Lovable topic it followed. This
    row does, and stays under the tombstone (an append-only direct child of the
    Matter, like the audit trail). A retirement must name a membership of the
    same cohort, Teema and Lovable id (`LovableSyncRetirement`).

    Written once and never changed. The database refuses an UPDATE or a DELETE,
    and refuses an INSERT unless a live (not deleted) Teema holds a
    synchronisation state of exactly this cohort and Lovable id at that moment
    (migration 0024's triggers), so a membership cannot be invented for a Teema
    that never had a state, nor added to a tombstone afterwards."""

    cohort = models.CharField(max_length=64)
    matter = models.ForeignKey(
        "matters.Matter", on_delete=models.PROTECT, related_name="lovable_sync_memberships"
    )
    #: The Lovable topic the state followed (`LovableSyncState.source_id`).
    source_id = models.UUIDField()
    basis = models.CharField(max_length=16, choices=MembershipBasis.choices)

    class Meta:
        verbose_name = "Lovable sünkroonimise kohordi liikmesus"
        ordering = ["created_at"]
        constraints = [
            models.UniqueConstraint(fields=["cohort", "matter"], name="lovable_sync_member_once"),
        ]

    def __str__(self) -> str:
        return f"{self.cohort}: {self.matter_id}"


class RetirementKind(models.TextChoices):
    #: Recorded by `delete_matter` in the deletion's own transaction.
    MATTER_DELETED = "MATTER_DELETED", "Teema kustutati jäädavalt"
    #: Recorded by `lovable_sync retire` for a deletion made before the recorder
    #: existed; still anchored to that deletion's MATTER_DELETED event.
    OPERATOR = "OPERATOR", "Kanti käsitsi (jäädavalt kustutatud teema)"


#: Refusals of a retirement written below the command layer (2026-11 QA, C-L-04).
RETIREMENT_NEEDS_A_TOMBSTONE = (
    "Kohordist arvatakse välja ainult jäädavalt kustutatud teema: see teema ei ole kustutatud."
)
RETIREMENT_NEEDS_ITS_DELETION_EVENT = (
    "Kohordist väljaarvamine peab viitama selle sama teema kustutamise sündmusele (MATTER_DELETED)."
)
#: REL-01 (2026-11): the deletion must have removed a synchronisation state, and
#: must not be the source replacement, which retires whole cohorts instead.
RETIREMENT_NEEDS_A_REMOVED_STATE = (
    "Kohordist väljaarvamine peab viitama kustutamisele, mis eemaldas teema "
    "sünkroonimise seisu (ja ei olnud allikapõhine asendamine)."
)
#: REL-01 (2026-11): who retired it is who deleted it.
RETIREMENT_NEEDS_THE_DELETIONS_ACTOR = (
    "Kohordist väljaarvamise isik peab olema sama, kes teema jäädavalt kustutas."
)
#: REL-01 (2026-11): neither a similar reference nor a deletion event proves membership.
RETIREMENT_NEEDS_MEMBERSHIP = (
    "Kohordist arvatakse välja ainult teema, mille liikmesust selles kohordis tõendab "
    "kohordi liikmesuse kirje sama Lovable'i tunnusega."
)

#: The purge's own count of the states it removed, in the MATTER_DELETED payload.
STATE_LABEL = "legacy_import.LovableSyncState"
#: The provenance source the replacement names (`app.matters.deletion.SOURCE_REPLACEMENT`).
REPLACEMENT_SOURCE = "LOVABLE_REPLACEMENT"


def removed_a_state(payload: Any) -> bool:
    """Whether a MATTER_DELETED payload is a person's deletion whose purge removed a
    synchronisation state: the same test migration 0024's trigger makes."""
    payload = payload if isinstance(payload, dict) else {}
    provenance = payload.get("provenance")
    if isinstance(provenance, dict) and provenance.get("source") == REPLACEMENT_SOURCE:
        return False
    rows = payload.get("rows")
    count = rows.get(STATE_LABEL) if isinstance(rows, dict) else None
    return isinstance(count, int) and not isinstance(count, bool) and count >= 1


class LovableSyncRetirement(BaseModel):
    """One cohort member that left its cohort because its Teema was permanently
    deleted (docs/adr/0165; audit 2026-11 C-L-04).

    A cohort's required size is its enrolled size less these rows
    (`lovable_sync.cohorts`). A state that disappears **without** one is drift and
    still stops the night (COHORT_SIZE). Every row names the deletion's own
    `MATTER_DELETED` change event, so a retirement exists only beside an
    authorised, audited deletion; the tombstone it points at is never removed.
    Written once and never changed.

    Enforced below the services as well (2026-11 QA, C-L-04; REL-01). An insert
    is refused unless, read from the database:

    * the Teema is a tombstone;
    * the event is that same Teema's MATTER_DELETED event, its purge removed a
      synchronisation state, and it is not the source replacement;
    * the retirement's actor is the deletion's actor;
    * a `LovableSyncMembership` names this cohort, this Teema and this Lovable
      id (``source_id`` is required).

    `save` checks it, so ``objects.create`` cannot forge one, and migration
    0024's BEFORE INSERT trigger checks it again, so ``bulk_create`` and a raw
    INSERT cannot either. The table is append-only in the database (migration
    0023's trigger), so ``QuerySet.update()`` and ``.delete()`` are refused too."""

    cohort = models.CharField(max_length=64)
    matter = models.ForeignKey(
        "matters.Matter", on_delete=models.PROTECT, related_name="lovable_sync_retirements"
    )
    #: The deletion's `MATTER_DELETED` event: who deleted it, when, and how much went.
    deletion_event = models.ForeignKey(
        "audit.ChangeEvent", on_delete=models.PROTECT, related_name="+"
    )
    #: The person who deleted the Teema (the event's actor), or none for a system deletion.
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="+",
    )
    #: The Lovable topic the state followed; unknown for an operator's later entry,
    #: whose state was already gone.
    source_id = models.UUIDField(null=True, blank=True)
    reference = models.CharField(max_length=20, blank=True)
    kind = models.CharField(max_length=16, choices=RetirementKind.choices)
    retired_at = models.DateTimeField()
    reason = models.CharField(max_length=300)

    class Meta:
        verbose_name = "Lovable sünkroonimise kohordist väljaarvamine"
        ordering = ["retired_at"]
        constraints = [
            # A Teema leaves a cohort once: a second retirement is refused by the
            # database, whatever the code above it does.
            models.UniqueConstraint(fields=["matter"], name="lovable_sync_retired_once"),
        ]

    def __str__(self) -> str:
        return f"{self.cohort}: {self.reference or self.matter_id}"

    def save(self, *args: Any, **kwargs: Any) -> None:
        if not self._state.adding:
            raise ImmutableRecordError("Kohordist väljaarvamist ei muudeta.")
        self._refuse_unless_beside_its_deletion()
        super().save(*args, **kwargs)

    def _refuse_unless_beside_its_deletion(self) -> None:
        """The Teema is a tombstone and the event is its own MATTER_DELETED.

        Read from the database, not from the instances handed in: the deletion
        marks its tombstone with ``update()``, so the caller's copy still says
        live, and an instance claiming ``deleted_at`` proves nothing."""
        from django.apps import apps

        from app.audit.enums import ChangeEventType

        matter_model = apps.get_model("matters", "Matter")
        event_model = apps.get_model("audit", "ChangeEvent")
        if not matter_model.all_objects.filter(
            pk=self.matter_id, deleted_at__isnull=False
        ).exists():
            raise InvariantViolation(RETIREMENT_NEEDS_A_TOMBSTONE)
        event = (
            event_model.objects.filter(
                pk=self.deletion_event_id,
                matter_id=self.matter_id,
                event_type=ChangeEventType.MATTER_DELETED,
            )
            .values("actor_id", "payload")
            .first()
        )
        if event is None:
            raise InvariantViolation(RETIREMENT_NEEDS_ITS_DELETION_EVENT)
        if not removed_a_state(event["payload"]):
            raise InvariantViolation(RETIREMENT_NEEDS_A_REMOVED_STATE)
        if event["actor_id"] != self.actor_id:
            raise InvariantViolation(RETIREMENT_NEEDS_THE_DELETIONS_ACTOR)
        if (
            self.source_id is None
            or not LovableSyncMembership.objects.filter(
                cohort=self.cohort, matter_id=self.matter_id, source_id=self.source_id
            ).exists()
        ):
            raise InvariantViolation(RETIREMENT_NEEDS_MEMBERSHIP)

    def delete(self, *args: Any, **kwargs: Any) -> tuple[int, dict[str, int]]:
        raise ImmutableRecordError("Kohordist väljaarvamist ei muudeta.")


class LovableSyncBaselineArchive(BaseModel):
    """A state's baseline and last observation before a rebuild re-seeded it.

    An exact copy of the `LovableSyncState` columns at that moment, so the
    evidence of what the pilot's comparison knew stays readable. The run and
    decision ledger is not copied: it keeps pointing at the same state row."""

    #: Both follow their Teema, as the state and its decisions do: the archive is
    #: evidence of the synchronisation, never a reason to keep a Teema a person
    #: decided to delete permanently.
    state = models.ForeignKey(
        LovableSyncState, on_delete=models.CASCADE, related_name="baseline_archives"
    )
    matter = models.ForeignKey(
        "matters.Matter", on_delete=models.CASCADE, related_name="lovable_baseline_archives"
    )
    cohort_before = models.CharField(max_length=64)
    cohort_after = models.CharField(max_length=64)
    applied = models.JSONField(default=dict)
    applied_version = models.IntegerField(null=True, blank=True)
    applied_snapshot_sha256 = models.CharField(max_length=64, blank=True)
    observed = models.JSONField(default=dict, blank=True)
    observed_version = models.IntegerField(null=True, blank=True)
    observed_run = models.ForeignKey(
        LovableSyncRun, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    archived_at = models.DateTimeField()
    reason = models.CharField(max_length=300)
    #: The rebuild package whose enrolment re-seeded the state.
    package_sha256 = models.CharField(max_length=64)

    class Meta:
        verbose_name = "Lovable sünkroonimise arhiveeritud lähtekoht"
        ordering = ["archived_at"]

    def save(self, *args: Any, **kwargs: Any) -> None:
        if not self._state.adding:
            raise ImmutableRecordError("Arhiveeritud lähtekohta ei muudeta.")
        super().save(*args, **kwargs)


class NewTopicStatus(models.TextChoices):
    #: COMPARE (or a held APPLY night): every check passed, it would be created.
    PLANNED = "PLANNED", "Luuakse rakendamisel"
    #: A check failed or a reading is unsafe: a person decides.
    HELD = "HELD", "Vajab ülevaatamist"
    #: Created by an APPLY night and enrolled (`created_matter`).
    CREATED = "CREATED", "Loodud"
    #: An administrator decided it (`lovable_sync.resolve_new_topic`): not created
    #: by the machine; it is not planned again.
    RESOLVED = "RESOLVED", "Lahendatud käsitsi"
    #: No longer an operational topic without a Teema (it left the scope, was
    #: deleted in Lovable, or a Teema now holds it): kept as history.
    GONE = "GONE", "Enam pole uus"


class LovableSyncNewTopic(BaseModel):
    """One operational Lovable topic that has no Juristid Teema (docs/adr/0213).

    Discovered every run from the reading's index, by its Lovable identity;
    checked (identity, reference, reading, plan) every run; created only by an
    APPLY night and only once — the database refuses a second row for the same
    topic, and a created row names its Teema. Held rows stay until a person
    decides or the topic stops being new. Codes and counts only: the Teema's
    title and wording live in its source reference, never here."""

    source_id = models.UUIDField(unique=True)
    #: Lovable's `nr` as last read: an identifier, never a title.
    reference = models.CharField(max_length=20)
    status = models.CharField(max_length=16, choices=NewTopicStatus.choices)
    reason = models.CharField(max_length=60, blank=True)
    first_seen_at = models.DateTimeField()
    last_seen_at = models.DateTimeField()
    first_seen_run = models.ForeignKey(
        LovableSyncRun, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    last_seen_run = models.ForeignKey(
        LovableSyncRun, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    #: The source row's version the last plan read.
    source_version = models.IntegerField(null=True, blank=True)
    reader_version = models.CharField(max_length=40, blank=True)
    #: The one-topic package the plan built and, once created, the digests the
    #: writer was bound to.
    package_sha256 = models.CharField(max_length=64, blank=True)
    plan_sha256 = models.CharField(max_length=64, blank=True)
    #: Codes only: the held Lovable columns, the stage-link partners' counts.
    detail = models.JSONField(default=dict, blank=True)
    #: SET_NULL so that a permanent deletion's ownership walk never takes this
    #: row with the Teema (a Matter row itself is never removed: 0203 §1).
    created_matter = models.ForeignKey(
        "matters.Matter",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    created_at_run = models.ForeignKey(
        LovableSyncRun, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    decided_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="+",
    )
    decided_at = models.DateTimeField(null=True, blank=True)
    decision_note = models.CharField(max_length=300, blank=True)

    class Meta:
        verbose_name = "Lovable'i uus teema"
        ordering = ["reference"]
        constraints = [
            models.CheckConstraint(
                condition=~models.Q(status="CREATED") | models.Q(created_matter__isnull=False),
                name="lovable_new_topic_created_names_its_teema",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.reference}: {self.status}"


class OpinionOutcome(models.TextChoices):
    """What Lovable's `valja` / `valja_kuupaev` say about a topic's opinion
    (docs/adr/0213 §10, owner brief 2026-11-11)."""

    #: A verified date: the opinion was sent on that day.
    SENT = "SENT", "Arvamus saadetud"
    #: «ei saatnud»: no opinion was sent for this process.
    NOT_SENT = "NOT_SENT", "Arvamust ei saadetud"
    #: The source no longer states either (both fields blank again). Blank is
    #: never «sent»: the earlier statement is superseded, not deleted.
    CLEARED = "CLEARED", "Allikas ei ütle enam midagi"


#: What every page says where a sent opinion's document is not in Juristid.
DOCUMENT_NOT_YET_AVAILABLE = "Arvamuse dokument pole veel saadaval."


class LovableOpinionOutcome(BaseModel):
    """Lovable's statement of whether a Teema's opinion went out, and when.

    **Not a `Submission`, and never one.** A sent `Submission` must carry its
    exact final document (`submissions_sent_requires_timestamp_and_evidence`),
    and Lovable's register holds a date, not a letter: inventing a document,
    a recipient, a signature or a transmission would be the fabricated
    certainty the product refuses (docs/adr/0011). So the sent status lives
    here, beside the import's other source statements, with what the source
    said verbatim; the document is «pole veel saadaval» until an imported
    OneNote file or a person's `+ Koja arvamus` supplies one
    (`DOCUMENT_NOT_YET_AVAILABLE`).

    **One current row per Teema**: the newest. Each change of the source's
    statement — sent on another day, «ei saatnud», cleared — is a new row that
    names the one it supersedes (``previous``), so the history of what Lovable
    said stays readable; a row is never edited. Written only by the
    synchronisation (`lovable_sync._apply_opinion_outcome`), in the transaction
    of the field's `LovableSyncDecision` (APPLIED) and with its run. Removed only
    with its Teema (the purge of a permanent deletion).

    For readers (dashboards, counts): `current_for` / `LovableOpinionOutcome.
    objects.current()` — the newest row per Teema whose outcome is not
    ``CLEARED``. ``sent_on`` is set exactly when ``outcome`` is ``SENT``."""

    matter = models.ForeignKey(
        "matters.Matter", on_delete=models.CASCADE, related_name="lovable_opinion_outcomes"
    )
    #: The Lovable topic (`LovableSyncState.source_id`) the statement is about.
    source_id = models.UUIDField()
    outcome = models.CharField(max_length=16, choices=OpinionOutcome.choices)
    sent_on = models.DateField(null=True, blank=True)
    #: Lovable's `arvamuse_tahtaeg` when the statement was read: the request the
    #: outcome answers (Lovable keeps one per topic). Source only.
    request_deadline = models.DateField(null=True, blank=True)
    #: Lovable's `koja_ettepanek`: a Chamber's own proposal rather than an answer.
    koda_initiative = models.BooleanField(default=False)
    #: The source's own words, verbatim (whitespace collapsed): `valja`,
    #: `valja_kuupaev`, `kellele`.
    source_valja = models.CharField(max_length=200, blank=True)
    source_valja_kuupaev = models.CharField(max_length=40, blank=True)
    addressee_raw = models.CharField(max_length=500, blank=True)
    previous = models.OneToOneField(
        "self", on_delete=models.SET_NULL, null=True, blank=True, related_name="superseded_by"
    )
    run = models.ForeignKey(LovableSyncRun, on_delete=models.PROTECT, related_name="+")
    source_version = models.IntegerField(null=True, blank=True)
    source_snapshot_sha256 = models.CharField(max_length=64)
    recorded_at = models.DateTimeField()

    class Meta:
        verbose_name = "Lovable'i arvamuse saatmise seis"
        ordering = ["matter", "-recorded_at"]
        indexes = [
            models.Index(fields=["matter", "-recorded_at"], name="lovable_outcome_matter_at"),
        ]
        constraints = [
            models.CheckConstraint(
                condition=(
                    models.Q(outcome="SENT", sent_on__isnull=False)
                    | (~models.Q(outcome="SENT") & models.Q(sent_on__isnull=True))
                ),
                name="lovable_outcome_sent_has_its_day",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.matter_id}: {self.outcome}"

    def save(self, *args: Any, **kwargs: Any) -> None:
        if not self._state.adding:
            raise ImmutableRecordError(
                "Lovable'i arvamuse seisu ei muudeta; uus seis on uus kirje."
            )
        super().save(*args, **kwargs)

    @property
    def document_available(self) -> bool:
        """Never from this row: the document comes from an imported file or a
        person's own `Koja arvamus` (`DOCUMENT_NOT_YET_AVAILABLE`)."""
        return False


def current_opinion_outcomes(matter_ids: Any = None) -> dict[Any, LovableOpinionOutcome]:
    """Matter id → its current `LovableOpinionOutcome` (the newest row, unless
    that row is ``CLEARED``). One query; ``matter_ids`` narrows it."""
    rows = LovableOpinionOutcome.objects.order_by("matter_id", "-recorded_at", "-created_at")
    if matter_ids is not None:
        rows = rows.filter(matter_id__in=list(matter_ids))
    out: dict[Any, LovableOpinionOutcome] = {}
    for row in rows.distinct("matter_id"):
        if row.outcome != OpinionOutcome.CLEARED:
            out[row.matter_id] = row
    return out
