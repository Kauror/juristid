"""The AI usage ledger: operational metadata only (r98, ADR 0236).

One **operation** is one logical request the application made of the model (a
batch of findings to judge, a page of candidates, an agenda). It has zero or more
**attempts**: the physical HTTP requests the client made for it (the SDK retries
inside one `messages.create`). Nothing here stores a prompt, an answer, a document,
a header or a provider's error text — every column is an allow-listed fact.

Unknown stays unknown: a token column is NULL when the figure was not measured,
and `usage_status` says why. A timed-out attempt is not "0 tokens".
"""

from __future__ import annotations

import uuid

from django.db import models


class Outcome(models.TextChoices):
    IN_PROGRESS = "IN_PROGRESS", "Pooleli"
    SUCCEEDED = "SUCCEEDED", "Õnnestus"
    SUCCEEDED_AFTER_RETRY = "SUCCEEDED_AFTER_RETRY", "Õnnestus kordusega"
    INCOMPLETE = "INCOMPLETE", "Vastus pooleli (peatati)"
    UNREADABLE = "UNREADABLE", "Vastus ei olnud loetav"
    FAILED = "FAILED", "Ebaõnnestus"
    SKIPPED_NO_TIME = "SKIPPED_NO_TIME", "Jäi tegemata: aeg täis"
    INTERRUPTED = "INTERRUPTED", "Katkestatud (tulemus teadmata)"


#: Operations whose answer was usable.
GOOD = (Outcome.SUCCEEDED, Outcome.SUCCEEDED_AFTER_RETRY)


class AttemptOutcome(models.TextChoices):
    OK = "OK", "Vastus saadud"
    HTTP_ERROR = "HTTP_ERROR", "HTTP-viga"
    RATE_LIMITED = "RATE_LIMITED", "Päringupiir (429)"
    TIMEOUT = "TIMEOUT", "Ajalõpp"
    CONNECTION_ERROR = "CONNECTION_ERROR", "Ühendusviga"
    INTERRUPTED = "INTERRUPTED", "Katkestatud"
    UNKNOWN = "UNKNOWN", "Teadmata"


class UsageStatus(models.TextChoices):
    MEASURED = "MEASURED", "Mõõdetud"
    UNAVAILABLE = "UNAVAILABLE", "Teadmata"
    NOT_APPLICABLE = "NOT_APPLICABLE", "Ei kohaldu"


class Service(models.TextChoices):
    WATCH_FINDINGS = "watch_findings", "Registrite jälgimine: leiud"
    CANDIDATES = "candidates", "Uute eelnõude otsing"
    GOVERNMENT_AGENDA = "government_agenda", "Valitsuse päevakord"
    RIIGIKOGU_DOCUMENTS = "riigikogu_documents", "Riigikogu dokumendianalüüs"


class AiOperation(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    service = models.CharField(max_length=40, choices=Service.choices, db_index=True)
    provider = models.CharField(max_length=40, default="anthropic")
    requested_model = models.CharField(max_length=80)
    #: An opaque id of the nightly run or command that made the request; "" when none.
    run_ref = models.CharField(max_length=64, blank=True)
    started_at = models.DateTimeField(db_index=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    #: The latest instant a live request could still be running (the step's own deadline
    #: plus its grace, or the client's worst case). Past it with no result, the operation
    #: is interrupted, never "still running"; before it, it is not touched.
    expected_end_at = models.DateTimeField(null=True, blank=True)
    outcome = models.CharField(max_length=24, choices=Outcome.choices, default=Outcome.IN_PROGRESS)
    #: Items put to the model, and rows of its answer / rows the caller accepted.
    items_presented = models.PositiveIntegerField(null=True, blank=True)
    items_classified = models.PositiveIntegerField(null=True, blank=True)
    #: A kind from `recorder.ERROR_KINDS`, never a message.
    error_kind = models.CharField(max_length=40, blank=True)

    class Meta:
        indexes = [models.Index(fields=["service", "started_at"])]
        ordering = ["-started_at"]

    def __str__(self) -> str:
        return f"{self.service} {self.started_at:%Y-%m-%d %H:%M} {self.outcome}"


class AiAttempt(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    operation = models.ForeignKey(AiOperation, on_delete=models.CASCADE, related_name="attempts")
    sequence = models.PositiveSmallIntegerField()
    started_at = models.DateTimeField(db_index=True)
    ended_at = models.DateTimeField(null=True, blank=True)
    duration_ms = models.PositiveIntegerField(null=True, blank=True)
    outcome = models.CharField(max_length=20, choices=AttemptOutcome.choices)
    http_status = models.PositiveSmallIntegerField(null=True, blank=True)
    #: "" none, "retried" (a later attempt followed), "final".
    retry_class = models.CharField(max_length=12, blank=True)
    #: The provider's request id header (opaque), when the client saw it.
    provider_request_id = models.CharField(max_length=80, blank=True)
    requested_model = models.CharField(max_length=80)
    actual_model = models.CharField(max_length=80, blank=True)
    usage_status = models.CharField(
        max_length=16, choices=UsageStatus.choices, default=UsageStatus.UNAVAILABLE
    )
    input_tokens = models.PositiveIntegerField(null=True, blank=True)
    output_tokens = models.PositiveIntegerField(null=True, blank=True)
    cache_read_tokens = models.PositiveIntegerField(null=True, blank=True)
    cache_creation_tokens = models.PositiveIntegerField(null=True, blank=True)
    stop_reason = models.CharField(max_length=40, blank=True)
    #: Explicit provider evidence only (a `fallback_message` iteration): True served by a
    #: fallback, False reported without one, None when the response said nothing about it.
    #: A different answering model alone is not evidence (aliases resolve to dated ids).
    fallback_observed = models.BooleanField(null=True, blank=True)
    #: When a server-side fallback model served the answer (`usage.iterations` carries a
    #: `fallback_message` entry): that model and its own reported tokens. The top-level
    #: tokens are then not attributable to one model, so the cost is unknown.
    fallback_model = models.CharField(max_length=80, blank=True)
    fallback_input_tokens = models.PositiveIntegerField(null=True, blank=True)
    fallback_output_tokens = models.PositiveIntegerField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["operation", "sequence"], name="ai_attempt_seq_unique")
        ]
        indexes = [models.Index(fields=["actual_model", "started_at"])]
        ordering = ["started_at", "sequence"]

    def __str__(self) -> str:
        return f"{self.operation_id}#{self.sequence}"


class AiPrice(models.Model):
    """A list price per million tokens, effective from a date (never edited in place:
    a price change is a new row, so an earlier estimate keeps its revision)."""

    provider = models.CharField(max_length=40, default="anthropic")
    model = models.CharField(max_length=80)
    tier = models.CharField(max_length=40, blank=True)
    currency = models.CharField(max_length=3, default="USD")
    input_per_mtok = models.DecimalField(max_digits=12, decimal_places=6)
    output_per_mtok = models.DecimalField(max_digits=12, decimal_places=6)
    cache_read_per_mtok = models.DecimalField(
        max_digits=12, decimal_places=6, null=True, blank=True
    )
    cache_creation_per_mtok = models.DecimalField(
        max_digits=12, decimal_places=6, null=True, blank=True
    )
    effective_from = models.DateField()
    effective_to = models.DateField(null=True, blank=True)
    source_url = models.URLField(max_length=300)
    verified_on = models.DateField()

    class Meta:
        indexes = [models.Index(fields=["provider", "model", "effective_from"])]
        ordering = ["model", "-effective_from"]

    def __str__(self) -> str:
        return f"{self.model} {self.effective_from}"


class AiFxRate(models.Model):
    """Units of ``currency`` per 1 EUR on a date, from a named source."""

    currency = models.CharField(max_length=3)
    on_date = models.DateField()
    per_eur = models.DecimalField(max_digits=14, decimal_places=6)
    source = models.CharField(max_length=200)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["currency", "on_date"], name="ai_fx_currency_day")
        ]

    def __str__(self) -> str:
        return f"{self.currency} {self.on_date}"


class AiLedgerGap(models.Model):
    """A request whose usage could not be saved: the dashboard says its figures are incomplete."""

    at = models.DateTimeField(db_index=True)
    service = models.CharField(max_length=40)
    kind = models.CharField(max_length=40)

    def __str__(self) -> str:
        return f"{self.service} {self.kind}"
