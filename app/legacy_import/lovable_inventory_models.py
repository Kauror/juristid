"""The Lovable inventory's own records (`app.legacy_import.lovable_inventory`).

Neither is a business record and neither has any effect on one:

* `LovableInventoryRun` — one stored ledger: which full reading it was built
  from (its time and digest), the reader version, the counts (brief §5 A3) and
  the per-topic ledger rows — identities, numbers and codes only, never a title
  or other source wording — with the ledger's own digest. Written only by
  ``lovable_inventory read|ledger --store``.
* `LovableInventoryDecision` — a person's decision on one ambiguous ledger row
  (the same topic as that Teema / not the same / exclude), with the reason and
  who decided. Append-only. It is an instruction for a later, separately
  approved step; nothing reads it to link, merge, create or change a Teema.

The Matter a decision names is a plain UUID, not a foreign key: a decision must
never take part in a Teema's deletion or replacement (docs/adr/0165, 0203).
"""

from __future__ import annotations

from django.conf import settings
from django.db import models

from app.core.models import AppendOnlyModel, BaseModel


class InventoryRunStatus(models.TextChoices):
    SUCCEEDED = "SUCCEEDED", "Õnnestus"
    FAILED = "FAILED", "Ebaõnnestus"


class InventoryTrigger(models.TextChoices):
    #: ``lovable_inventory read --store``: a fresh reading through the GET-only transport.
    READ = "READ", "Lovable'i lugemine"
    #: ``lovable_inventory ledger --document F --store``: a saved reading, offline.
    LEDGER = "LEDGER", "Salvestatud lugemine"


class LovableInventoryRun(BaseModel):
    """One stored inventory ledger. Counts and codes; no business text."""

    trigger = models.CharField(max_length=16, choices=InventoryTrigger.choices)
    status = models.CharField(
        max_length=16, choices=InventoryRunStatus.choices, default=InventoryRunStatus.SUCCEEDED
    )
    #: When the reading was taken (the reading's own ``taken_at``).
    taken_at = models.DateTimeField(null=True, blank=True)
    #: The reading's digest (`lovable_observation.verify_observation`).
    source_sha256 = models.CharField(max_length=64, blank=True)
    #: `lovable_inventory.INVENTORY_VERSION` that built the ledger.
    reader_version = models.CharField(max_length=40)
    counts = models.JSONField(default=dict, blank=True)
    #: One compact entry per ledger row (`LedgerRow.as_dict`).
    rows = models.JSONField(default=list, blank=True)
    ledger_sha256 = models.CharField(max_length=64, blank=True)
    error = models.CharField(max_length=300, blank=True)

    class Meta:
        verbose_name = "Lovable'i inventuur"
        verbose_name_plural = "Lovable'i inventuurid"
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.trigger} {self.status} {self.taken_at}"


class InventoryDecisionKind(models.TextChoices):
    LINK = "LINK", "Sama teema kui see Juristide teema"
    NOT_SAME = "NOT_SAME", "Ei ole sama teema"
    EXCLUDE = "EXCLUDE", "Jäta inventuurist välja"


class LovableInventoryDecision(AppendOnlyModel):
    """A person's decision on one ambiguous inventory row. Never applied by itself."""

    run = models.ForeignKey(
        LovableInventoryRun,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="decisions",
    )
    source_id = models.UUIDField()
    reference = models.CharField(max_length=20, blank=True)
    decision = models.CharField(max_length=16, choices=InventoryDecisionKind.choices)
    #: The Teema the person means (LINK / NOT_SAME), as a plain id — no relation.
    matter_id = models.UUIDField(null=True, blank=True)
    reason = models.TextField()
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="lovable_inventory_decisions",
    )
    decided_at = models.DateTimeField()

    class Meta:
        verbose_name = "Lovable'i inventuuri otsus"
        verbose_name_plural = "Lovable'i inventuuri otsused"
        ordering = ["created_at"]
        constraints = [
            models.CheckConstraint(
                condition=~models.Q(reason=""), name="lovable_inventory_decision_has_reason"
            ),
        ]

    def __str__(self) -> str:
        return f"{self.decision} {self.reference or self.source_id}"
