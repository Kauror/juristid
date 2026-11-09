"""Requests for a data synchronisation, recorded by the web and run by the host.

Haldus → Andmete sünkroonimine never reaches Lovable and never reads or imports
a OneNote capture itself. A technical administrator's «Sünkrooni nüüd»,
«OneNote'i impordi kontroll» or «Impordi OneNote'i materjalid» records one
`DataSyncRequest`; the host runner (`manage.py data_sync_requests run-next`,
fired by the same cron file as the nightly job) claims it, runs the same engine
the nightly job runs, and records the real result here.

Rules kept below the services as well:

* **one open request per lane** — at most one PENDING or RUNNING request for
  Lovable, and one for OneNote (verification and import share the lane, so an
  import can never run beside the check it depends on). A partial unique index
  on ``lane`` refuses the second, whatever the code above it does;
* **status moves forward only** — PENDING → RUNNING | REFUSED | EXPIRED,
  RUNNING → DONE | FAILED | REFUSED; a finished request never changes again,
  and who asked, what and why never change at all. ``save`` checks it and
  migration 0025's trigger checks it again for ``update()`` and raw SQL;
* **rows are never deleted** — the same trigger refuses a DELETE. A stale
  PENDING request is marked EXPIRED, not removed;
* **a Lovable request is bound to the mode it was confirmed in**
  (``mode_at_request``, LS2-D): the runner refuses it (``MODE_CHANGED``) if
  the mode is different when it is claimed, so nobody confirms a comparison and
  gets an APPLY run.

`DataSyncRunnerBeat` is the host runner's heartbeat: every on-demand firing of
the host script records, per lane, when it last asked and what it was told, so
the page can say plainly whether a server runner exists at all.

``result`` holds codes and counts only: never a title, a value, a file name or a
person's name, so the page can show it to a technical administrator.
"""

from __future__ import annotations

from typing import Any

from django.conf import settings
from django.db import models

from app.core.errors import ImmutableRecordError
from app.core.models import BaseModel


class SyncRequestKind(models.TextChoices):
    LOVABLE_SYNC = "LOVABLE_SYNC", "Lovable'i sünkroonimine"
    ONENOTE_VERIFY = "ONENOTE_VERIFY", "OneNote'i impordi kontroll"
    ONENOTE_IMPORT = "ONENOTE_IMPORT", "OneNote'i materjalide import"


class SyncRequestLane(models.TextChoices):
    LOVABLE = "lovable", "Lovable"
    ONENOTE = "onenote", "OneNote"


class SyncRequestStatus(models.TextChoices):
    PENDING = "PENDING", "Ootab käivitamist"
    RUNNING = "RUNNING", "Käib"
    DONE = "DONE", "Tehtud"
    FAILED = "FAILED", "Ebaõnnestus"
    REFUSED = "REFUSED", "Keelduti"
    EXPIRED = "EXPIRED", "Aegus"


LANE_OF_KIND: dict[str, str] = {
    SyncRequestKind.LOVABLE_SYNC: SyncRequestLane.LOVABLE,
    SyncRequestKind.ONENOTE_VERIFY: SyncRequestLane.ONENOTE,
    SyncRequestKind.ONENOTE_IMPORT: SyncRequestLane.ONENOTE,
}

OPEN_STATUSES = (SyncRequestStatus.PENDING, SyncRequestStatus.RUNNING)
FINISHED_STATUSES = (
    SyncRequestStatus.DONE,
    SyncRequestStatus.FAILED,
    SyncRequestStatus.REFUSED,
    SyncRequestStatus.EXPIRED,
)

#: The only moves a request's status may make. Mirrored by migration 0025's trigger.
ALLOWED_TRANSITIONS: dict[str, frozenset[str]] = {
    SyncRequestStatus.PENDING: frozenset(
        {SyncRequestStatus.RUNNING, SyncRequestStatus.REFUSED, SyncRequestStatus.EXPIRED}
    ),
    SyncRequestStatus.RUNNING: frozenset(
        {SyncRequestStatus.DONE, SyncRequestStatus.FAILED, SyncRequestStatus.REFUSED}
    ),
}

#: Columns written when the request is recorded and never again.
FIXED_FIELDS = (
    "kind",
    "lane",
    "requested_by_id",
    "reason",
    "requested_at",
    "capture_name",
    "approved_plan_sha256",
    "mode_at_request",
)

TRANSITION_REFUSED = "Taotluse olekut ei saa nii muuta: olek liigub ainult edasi."
FIXED_REFUSED = "Taotluse esitaja, liiki, põhjust ega aega ei muudeta."
DELETE_REFUSED = "Sünkroonimise taotlusi ei kustutata."


class DataSyncRequest(BaseModel):
    """One administrator's request for a synchronisation the host must run."""

    kind = models.CharField(max_length=24, choices=SyncRequestKind.choices)
    #: Derived from ``kind`` (`LANE_OF_KIND`); the unit of «one open at a time».
    lane = models.CharField(max_length=16, choices=SyncRequestLane.choices)
    status = models.CharField(
        max_length=16, choices=SyncRequestStatus.choices, default=SyncRequestStatus.PENDING
    )
    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+"
    )
    #: Why, in the administrator's words (required; audit context, not business content).
    reason = models.CharField(max_length=300)
    requested_at = models.DateTimeField()
    claimed_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    #: OneNote only: the capture directory's name under the configured source root.
    capture_name = models.CharField(max_length=120, blank=True)
    #: ONENOTE_IMPORT only: the verified plan digest the import is bound to.
    approved_plan_sha256 = models.CharField(max_length=64, blank=True)
    #: LOVABLE_SYNC only: the mode (COMPARE/APPLY) the confirmation stated.
    mode_at_request = models.CharField(max_length=16, blank=True)
    #: A short code when the request did not finish as asked (``PAUSED``, ``PLAN_CHANGED``).
    outcome_code = models.CharField(max_length=64, blank=True)
    #: Codes and counts only (`sync_admin` writes it); never business text.
    result = models.JSONField(default=dict, blank=True)
    sync_run = models.ForeignKey(
        "legacy_import.LovableSyncRun",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )

    class Meta:
        verbose_name = "Andmete sünkroonimise taotlus"
        ordering = ["-requested_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["lane"],
                condition=models.Q(status__in=["PENDING", "RUNNING"]),
                name="data_sync_one_open_request_per_lane",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(kind="LOVABLE_SYNC", lane="lovable")
                    | models.Q(kind__in=["ONENOTE_VERIFY", "ONENOTE_IMPORT"], lane="onenote")
                ),
                name="data_sync_request_lane_matches_kind",
            ),
            models.CheckConstraint(
                condition=~models.Q(kind="ONENOTE_IMPORT")
                | models.Q(approved_plan_sha256__regex=r"^[0-9a-f]{64}$"),
                name="data_sync_import_names_its_plan",
            ),
            models.CheckConstraint(
                condition=~models.Q(kind="LOVABLE_SYNC")
                | models.Q(mode_at_request__in=["COMPARE", "APPLY"]),
                name="data_sync_lovable_names_its_mode",
            ),
        ]
        indexes = [models.Index(fields=["lane", "status"], name="data_sync_lane_status")]

    def __str__(self) -> str:
        return f"{self.kind} {self.status}"

    @property
    def is_open(self) -> bool:
        return self.status in OPEN_STATUSES

    def save(self, *args: Any, **kwargs: Any) -> None:
        self.lane = LANE_OF_KIND.get(self.kind, self.lane)
        if not self._state.adding:
            stored = type(self).objects.filter(pk=self.pk).values("status", *FIXED_FIELDS).first()
            if stored is not None:
                if any(stored[name] != getattr(self, name) for name in FIXED_FIELDS):
                    raise ImmutableRecordError(FIXED_REFUSED)
                before, after = stored["status"], self.status
                if before != after and after not in ALLOWED_TRANSITIONS.get(before, frozenset()):
                    raise ImmutableRecordError(TRANSITION_REFUSED)
                if before == after and before in FINISHED_STATUSES:
                    raise ImmutableRecordError(TRANSITION_REFUSED)
        super().save(*args, **kwargs)

    def delete(self, *args: Any, **kwargs: Any) -> tuple[int, dict[str, int]]:
        raise ImmutableRecordError(DELETE_REFUSED)


#: A heartbeat older than this means the on-demand runner is not firing.
#: The proposed cron line fires every five minutes.
RUNNER_SILENT_AFTER_MINUTES = 15


class DataSyncRunnerBeat(BaseModel):
    """The host's on-demand runner (`data_sync_requests poll`), last seen per lane.

    Operational metadata, not business data: one row per lane, overwritten by
    every firing. ``answer`` is what the database told the runner (``DUE
    COMPARE``, ``NOT_DUE NO_REQUEST``…); ``problem`` is a host-side reason the
    lane cannot run (``HOST_PAUSED``, ``NO_CREDENTIALS``, ``NOT_PERMITTED``,
    ``NO_CAPTURE_SOURCE``) or ``""``. Codes only, never a path or a secret."""

    lane = models.CharField(max_length=16, choices=SyncRequestLane.choices, unique=True)
    first_seen_at = models.DateTimeField()
    seen_at = models.DateTimeField()
    answer = models.CharField(max_length=64, blank=True)
    problem = models.CharField(max_length=64, blank=True)

    class Meta:
        verbose_name = "Andmete sünkroonimise serveri käivitaja"

    def __str__(self) -> str:
        return f"{self.lane} {self.seen_at:%Y-%m-%dT%H:%M}Z"
