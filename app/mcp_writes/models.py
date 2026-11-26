"""Proposed, approved and executed MCP writes (docs/adr/0189; CONTRACT §5).

Three tables, all this app's own:

* `McpWriteProposal` — one change an AI client *prepared*: the operation, the
  canonical arguments (ids resolved, nothing left to interpret), their digest,
  the state the change was judged against, the preview the person approves,
  and what became of it. Written by the write gateway (`mcp-writer`): created
  at prepare, moved on at commit.
* `McpWriteApproval` — the person's own approval of one proposal, on
  Juristid's approval page, with their personal authenticator. Written by
  `web` only, once per proposal, and never changed (a database trigger says
  so): the gateway's role may read it and nothing else.
* `McpWriteEvent` — what happened, appended and never changed (trigger).
  ``detail`` holds codes and ids only — never record text, a token or a code.

The target Matter is kept as its id, not a foreign key: a proposal must not
hold a Matter back from the deletion and purge machinery that walks every
relation pointing at one (app/matters/purge.py), and a proposal is evidence of
what was asked, not part of the Matter.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from django.db import models

from app.core.models import AppendOnlyModel, BaseModel


class ProposalStatus(models.TextChoices):
    PENDING = "PENDING", "Ootab kinnitamist"
    APPROVED = "APPROVED", "Kinnitatud"
    EXECUTED = "EXECUTED", "Salvestatud"
    DECLINED = "DECLINED", "Tagasi lükatud"
    EXPIRED = "EXPIRED", "Aegunud"
    FAILED = "FAILED", "Ebaõnnestus"
    STALE = "STALE", "Vananenud"


#: The statuses a proposal never leaves.
FINAL_STATUSES = frozenset(
    {
        ProposalStatus.EXECUTED,
        ProposalStatus.DECLINED,
        ProposalStatus.EXPIRED,
        ProposalStatus.FAILED,
        ProposalStatus.STALE,
    }
)


class ApprovalMethod(models.TextChoices):
    TOTP = "totp", "Autentija kood"
    RECENT_TOTP = "recent_totp", "Hiljutine autentija kood samas seansis"


class McpWriteEventType(models.TextChoices):
    PREPARED = "PREPARED", "Muudatus ette valmistatud"
    PREPARE_REFUSED = "PREPARE_REFUSED", "Ettevalmistamisest keelduti"
    APPROVED = "APPROVED", "Kinnitatud"
    APPROVAL_REFUSED = "APPROVAL_REFUSED", "Kinnitamisest keelduti"
    DECLINED = "DECLINED", "Tagasi lükatud"
    COMMITTED = "COMMITTED", "Salvestatud"
    COMMIT_REPLAYED = "COMMIT_REPLAYED", "Korduv salvestamispäring"
    COMMIT_REFUSED = "COMMIT_REFUSED", "Salvestamisest keelduti"
    FAILED = "FAILED", "Salvestamine ebaõnnestus"
    EXPIRED = "EXPIRED", "Aegunud"
    #: The client fetched the receipt of a change the approval page saved
    #: (format 2): the first fetch. Later ones are COMMIT_REPLAYED.
    RECEIPT_DELIVERED = "RECEIPT_DELIVERED", "Tulemus edastatud kliendile"
    #: An outstanding proposal of an earlier approval format, retired by a
    #: migration rather than ever approved or executed under new rules.
    FORMAT_RETIRED = "FORMAT_RETIRED", "Vananenud vorming, aegunud"


#: The approval/binding format this code writes and accepts (SEC-009, ADR 0230).
#:
#: * 1 — r94 and earlier: the gateway executed at ``commit`` once an approval
#:   row matched SHA-256(operation, canonical arguments, user, grant). Never
#:   approved, executed or reinterpreted by format-2 code: migration
#:   ``0002`` retires every outstanding format-1 proposal.
#: * 2 — the approval page re-derives the change from the stored arguments with
#:   trusted code, shows *that* preview, binds the approval to the binding
#:   digest of everything shown and checked, and executes it in the same
#:   transaction. The gateway only prepares and reports.
CURRENT_FORMAT = 2


class McpWriteProposal(BaseModel):
    """One change an AI client prepared for its person to approve.

    ``id`` is public — it is in the approval link and the client's commit call
    — and is a uuid4, not the time-sortable uuid7 every other table uses: a
    proposal id must not be guessable from the moment it was made. Knowing it
    is still worth nothing: the approval page answers only the proposal's own
    person behind the gate, and the gateway answers only its own token.

    ``arguments_digest`` is the SHA-256 of the canonical JSON of the operation,
    the canonical arguments, the user and the grant
    (`app.mcp_writes.proposals.arguments_digest`). The approval stores the
    digest it approved; the commit recomputes it from the stored arguments and
    refuses if either differs — a row edited after approval approves nothing.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    #: `CURRENT_FORMAT` for a proposal this code prepared. The column default
    #: is 1 on purpose: a row an older release (or a rolled-back one) writes
    #: without naming a format is format 1, which this code never approves.
    format_version = models.PositiveSmallIntegerField(default=1, verbose_name="vorming")
    user = models.ForeignKey(
        "accounts.User", on_delete=models.PROTECT, related_name="+", verbose_name="kasutaja"
    )
    grant = models.ForeignKey(
        "mcp_access.McpAccessGrant",
        on_delete=models.PROTECT,
        related_name="+",
        verbose_name="MCP ligipääs",
    )
    #: The token family the prepare call came with. Null only when the
    #: verification did not report one.
    token_family_id = models.UUIDField(null=True, blank=True, verbose_name="võtmepere")
    client_id = models.CharField(max_length=500, verbose_name="klient")
    operation = models.CharField(max_length=64, verbose_name="toiming")
    #: The client's arguments as the operation's model validated them (format
    #: 2). The approval page derives the change from these again with trusted
    #: code; it never shows ``preview`` or trusts ``canonical_arguments``.
    arguments = models.JSONField(default=dict, verbose_name="argumendid")
    canonical_arguments = models.JSONField(default=dict, verbose_name="kanoonilised argumendid")
    arguments_digest = models.CharField(max_length=64, verbose_name="argumentide räsi")
    target_matter_id = models.UUIDField(null=True, blank=True, db_index=True, verbose_name="teema")
    target_record_type = models.CharField(
        max_length=100, blank=True, default="", verbose_name="kirje liik"
    )
    target_record_id = models.CharField(
        max_length=100, blank=True, default="", verbose_name="kirje"
    )
    state = models.JSONField(default=dict, verbose_name="olek ettevalmistamisel")
    preview = models.JSONField(default=dict, verbose_name="eelvaade")
    status = models.CharField(
        max_length=16,
        choices=ProposalStatus.choices,
        default=ProposalStatus.PENDING,
        verbose_name="olek",
    )
    expires_at = models.DateTimeField(verbose_name="aegub")
    executed_at = models.DateTimeField(null=True, blank=True, verbose_name="salvestatud")
    result = models.JSONField(default=dict, blank=True, verbose_name="tulemus")
    failure_code = models.CharField(
        max_length=32, blank=True, default="", verbose_name="ebaõnnestumise kood"
    )
    correlation_id = models.UUIDField(verbose_name="päringu tunnus")

    class Meta:
        verbose_name = "MCP muudatuse ettepanek"
        verbose_name_plural = "MCP muudatuste ettepanekud"
        indexes = [
            models.Index(fields=["user", "status"], name="mcp_write_user_status"),
            models.Index(fields=["status", "expires_at"], name="mcp_write_status_expiry"),
        ]

    def __str__(self) -> str:
        return f"MCP muudatus {self.pk} ({self.operation}, {self.status})"

    def is_expired_at(self, moment: datetime) -> bool:
        return self.expires_at <= moment


class McpWriteApproval(AppendOnlyModel):
    """The person's approval of one proposal, with their own authenticator.

    One per proposal (the one-to-one is the constraint). ``user``, ``grant``
    and ``arguments_digest`` are what was approved, copied from the proposal as
    the person saw it; the commit compares all three with the proposal again.
    ``expires_at`` bounds how long the approval waits for its commit
    (`MCP_WRITE_APPROVAL_SECONDS`).
    """

    proposal = models.OneToOneField(
        McpWriteProposal,
        on_delete=models.PROTECT,
        related_name="approval",
        verbose_name="ettepanek",
    )
    user = models.ForeignKey(
        "accounts.User", on_delete=models.PROTECT, related_name="+", verbose_name="kasutaja"
    )
    grant = models.ForeignKey(
        "mcp_access.McpAccessGrant",
        on_delete=models.PROTECT,
        related_name="+",
        verbose_name="MCP ligipääs",
    )
    arguments_digest = models.CharField(max_length=64, verbose_name="argumentide räsi")
    #: Format 2 (SEC-009): what the person saw and approved, as the approval
    #: page derived it — written by `web` only, so it is evidence the gateway
    #: cannot rewrite. ``binding_digest`` is `review.binding_digest` over the
    #: proposal's identity, operation, arguments, canonical arguments, target,
    #: user, grant, client, token family, state, preview and expiry.
    format_version = models.PositiveSmallIntegerField(default=1, verbose_name="vorming")
    binding_digest = models.CharField(
        max_length=64, blank=True, default="", verbose_name="sidumise räsi"
    )
    canonical_arguments = models.JSONField(
        default=dict, blank=True, verbose_name="kinnitatud kanoonilised argumendid"
    )
    state = models.JSONField(default=dict, blank=True, verbose_name="kinnitatud olek")
    preview = models.JSONField(default=dict, blank=True, verbose_name="kinnitatud eelvaade")
    approved_at = models.DateTimeField(verbose_name="kinnitatud")
    expires_at = models.DateTimeField(verbose_name="kehtib kuni")
    method = models.CharField(max_length=16, choices=ApprovalMethod.choices, verbose_name="viis")
    ip_address = models.GenericIPAddressField(null=True, blank=True, verbose_name="IP-aadress")

    class Meta:
        verbose_name = "MCP muudatuse kinnitus"
        verbose_name_plural = "MCP muudatuste kinnitused"

    def __str__(self) -> str:
        return f"MCP kinnitus {self.proposal_id}"

    def is_expired_at(self, moment: datetime) -> bool:
        return self.expires_at <= moment


class McpWriteEvent(AppendOnlyModel):
    """What happened to an MCP write, appended and never changed (a database trigger says so)."""

    event_type = models.CharField(
        max_length=32, choices=McpWriteEventType.choices, verbose_name="sündmus"
    )
    succeeded = models.BooleanField(default=True, verbose_name="õnnestus")
    proposal = models.ForeignKey(
        McpWriteProposal,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="events",
        verbose_name="ettepanek",
    )
    user = models.ForeignKey(
        "accounts.User",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="+",
        verbose_name="kasutaja",
    )
    grant = models.ForeignKey(
        "mcp_access.McpAccessGrant",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="+",
        verbose_name="MCP ligipääs",
    )
    client_id = models.CharField(max_length=500, blank=True, default="", verbose_name="klient")
    operation = models.CharField(max_length=64, blank=True, default="", verbose_name="toiming")
    correlation_id = models.UUIDField(null=True, blank=True, verbose_name="päringu tunnus")
    #: Codes and ids only: a refusal code, a field name, a record id.
    detail = models.JSONField(default=dict, blank=True, verbose_name="üksikasjad")

    class Meta:
        verbose_name = "MCP muudatuse sündmus"
        verbose_name_plural = "MCP muudatuste sündmused"
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.event_type} {self.created_at:%Y-%m-%d %H:%M}"


class McpWriteCounter(models.Model):
    """One fixed-window counter of the write gateway's budgets (SEC-014, ADR 0230).

    ``principal`` is a stable key — ``user:<id>``, ``grant:<id>``,
    ``family:<id>``, ``client:<sha-256 prefix of the client id>`` or
    ``global`` — never a token, so a refreshed token counts against the same
    rows. ``bucket`` names what is counted: ``prepare``, ``commit``,
    ``refused``, ``stored_bytes``, or ``event:<type>:<code>:<reason>`` for the
    aggregation of repeated identical refusals. ``period`` is ``10m`` or ``1d``
    and ``period_start`` the window's start. Incremented with one
    ``INSERT … ON CONFLICT DO UPDATE … RETURNING`` (`app.mcp_writes.budgets`),
    so concurrent calls never lose a count.

    Not an audit table: it is the operational tally beside the append-only
    events, and how an aggregated refusal stays observable after its first
    `MCP_WRITE_EVENTS_PER_WINDOW` occurrences were written as events.
    """

    #: A uuid, not a serial: the gateway's role is granted no sequence.
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    principal = models.CharField(max_length=100, verbose_name="tegutseja võti")
    bucket = models.CharField(max_length=120, verbose_name="mõõdetav")
    period = models.CharField(max_length=8, verbose_name="aken")
    period_start = models.DateTimeField(verbose_name="akna algus")
    count = models.BigIntegerField(default=0, verbose_name="arv")
    first_at = models.DateTimeField(verbose_name="esimene")
    last_at = models.DateTimeField(verbose_name="viimane")

    class Meta:
        verbose_name = "MCP muudatuste loendur"
        verbose_name_plural = "MCP muudatuste loendurid"
        constraints = [
            models.UniqueConstraint(
                fields=["principal", "bucket", "period", "period_start"],
                name="mcp_write_counter_one_window",
            )
        ]
        indexes = [models.Index(fields=["period_start"], name="mcp_write_counter_start")]

    def __str__(self) -> str:
        return f"{self.principal} {self.bucket} {self.period}@{self.period_start:%Y-%m-%d %H:%M}"
