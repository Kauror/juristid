"""Who may connect an AI client, and the OAuth state of every connection (docs/adr/0185 §3–§4).

Every table here is additive and belongs to this app alone: no other app's
table gains a column, and nothing here is written by the `mcp` process, whose
database connections are read-only. That process only ever *reads* a token, its
grant and its account (`app.mcp_access.verification`).

No secret is stored in a form that could be used. Authorization codes, access
and refresh tokens and enrolment-link verifiers are kept as the SHA-256 of 256
random bits — enough that the digest is the lookup key and nothing can be
guessed from it — and the personal authenticator's TOTP secret is encrypted
under the MFA key with a purpose of its own (`app.accounts.mfa.seal`).
"""

from __future__ import annotations

import uuid
from datetime import datetime

from django.db import models

from app.core.models import AppendOnlyModel, BaseModel


class GrantStatus(models.TextChoices):
    PENDING_ENROLMENT = "PENDING_ENROLMENT", "Ootab autentija seadistamist"
    ACTIVE = "ACTIVE", "Aktiivne"
    REVOKED = "REVOKED", "Tühistatud"


class TokenKind(models.TextChoices):
    ACCESS = "ACCESS", "Ligipääsuvõti"
    REFRESH = "REFRESH", "Uuendusvõti"


class McpAccessEventType(models.TextChoices):
    GRANT_CREATED = "GRANT_CREATED", "Ligipääs antud"
    GRANT_REFUSED = "GRANT_REFUSED", "Ligipääsu andmisest keelduti"
    ENROLMENT_LINK_ISSUED = "ENROLMENT_LINK_ISSUED", "Seadistuslink väljastatud"
    ENROLMENT_COMPLETED = "ENROLMENT_COMPLETED", "Autentija seadistatud"
    ENROLMENT_REFUSED = "ENROLMENT_REFUSED", "Seadistamisest keelduti"
    AUTHORIZATION_GRANTED = "AUTHORIZATION_GRANTED", "Ühendus lubatud"
    AUTHORIZATION_DENIED = "AUTHORIZATION_DENIED", "Kasutaja keeldus"
    AUTHORIZATION_REFUSED = "AUTHORIZATION_REFUSED", "Ühenduse lubamisest keelduti"
    GRANT_LOCKED = "GRANT_LOCKED", "Ligipääs ajutiselt lukus"
    GRANT_UNLOCKED = "GRANT_UNLOCKED", "Lukk eemaldatud"
    TOKEN_ISSUED = "TOKEN_ISSUED", "Võtmed väljastatud"
    TOKEN_REFRESHED = "TOKEN_REFRESHED", "Võtmed uuendatud"
    TOKEN_REFUSED = "TOKEN_REFUSED", "Võtmete väljastamisest keelduti"
    REFRESH_REUSE_DETECTED = "REFRESH_REUSE_DETECTED", "Uuendusvõtit kasutati korduvalt"
    TOKEN_REVOKED = "TOKEN_REVOKED", "Võti tühistatud"
    GRANT_REVOKED = "GRANT_REVOKED", "Ligipääs tühistatud"


class McpAccessGrant(BaseModel):
    """An operator's decision that this account may connect an AI client.

    Made only by `manage.py mcp_access grant`, never because a token, an e-mail
    address or a role looks right. It starts PENDING_ENROLMENT and becomes
    ACTIVE when the person has enrolled their authenticator; REVOKED is final —
    a new decision is a new grant, enrolled afresh.

    ``failed_attempts`` and ``locked_until`` count wrong authenticator codes at
    the authorization step. The lock refuses *authorizing*, never a token that
    was already issued: ending those is what revocation is for.
    """

    user = models.ForeignKey(
        "accounts.User",
        on_delete=models.PROTECT,
        related_name="mcp_access_grants",
        verbose_name="kasutaja",
    )
    status = models.CharField(
        max_length=24,
        choices=GrantStatus.choices,
        default=GrantStatus.PENDING_ENROLMENT,
        verbose_name="olek",
    )
    #: Null for a grant an operator made from the command line, where there is
    #: no request user; ``created_via`` then says who and where.
    created_by = models.ForeignKey(
        "accounts.User",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="+",
        verbose_name="andis",
    )
    created_via = models.CharField(max_length=300, blank=True, default="", verbose_name="päritolu")
    activated_at = models.DateTimeField(null=True, blank=True, verbose_name="aktiveeritud")
    revoked_at = models.DateTimeField(null=True, blank=True, verbose_name="tühistatud")
    revoke_reason = models.CharField(
        max_length=300, blank=True, default="", verbose_name="tühistamise põhjus"
    )
    failed_attempts = models.PositiveIntegerField(default=0, verbose_name="valed koodid järjest")
    locked_until = models.DateTimeField(null=True, blank=True, verbose_name="lukus kuni")

    class Meta:
        verbose_name = "MCP ligipääs"
        verbose_name_plural = "MCP ligipääsud"
        constraints = [
            # At most one grant per person that is not over: two live grants
            # would be two authenticators, and revoking "the" grant would leave
            # the other one working.
            models.UniqueConstraint(
                fields=["user"],
                condition=~models.Q(status=GrantStatus.REVOKED),
                name="mcp_access_one_open_grant_per_user",
            ),
        ]

    def __str__(self) -> str:
        return f"MCP ligipääs {self.user_id} ({self.status})"

    @property
    def is_active(self) -> bool:
        return self.status == GrantStatus.ACTIVE

    def is_locked_at(self, moment: datetime) -> bool:
        return self.locked_until is not None and self.locked_until > moment


class McpAuthenticator(BaseModel):
    """The personal TOTP authenticator a grant was enrolled with.

    Deliberately not `accounts.TotpDevice`: that is the dormant personal
    sign-in's second factor, and enrolling one here must neither create nor
    replace it. The secret is sealed under its own purpose
    (`app.accounts.mfa.seal`), so the two can never be swapped for each other.
    ``last_used_step`` is the TOTP time step of the last accepted code; only a
    later step is accepted, so a watched code is spent.
    """

    grant = models.OneToOneField(
        McpAccessGrant,
        on_delete=models.PROTECT,
        related_name="authenticator",
        verbose_name="ligipääs",
    )
    encrypted_secret = models.TextField(verbose_name="krüpteeritud saladus")
    last_used_step = models.BigIntegerField(default=0, verbose_name="viimane kasutatud samm")
    confirmed_at = models.DateTimeField(verbose_name="kinnitatud")

    class Meta:
        verbose_name = "MCP autentija"
        verbose_name_plural = "MCP autentijad"

    def __str__(self) -> str:
        return f"MCP autentija {self.grant_id}"


class McpEnrolmentLink(BaseModel):
    """A one-time link that lets the grant's person enrol their authenticator.

    The shape of `app.accounts.tokens`: ``<selector>.<verifier>``, the selector
    finds the row and only the SHA-256 of the verifier is stored. Issuing a new
    link invalidates the outstanding one, and a partial unique index holds "one
    live link per grant" whatever the code does.
    """

    grant = models.ForeignKey(
        McpAccessGrant,
        on_delete=models.PROTECT,
        related_name="enrolment_links",
        verbose_name="ligipääs",
    )
    selector = models.CharField(max_length=32, unique=True, verbose_name="valija")
    verifier_digest = models.CharField(max_length=64, verbose_name="tõendi räsi")
    expires_at = models.DateTimeField(verbose_name="aegub")
    used_at = models.DateTimeField(null=True, blank=True, verbose_name="kasutatud")
    invalidated_at = models.DateTimeField(null=True, blank=True, verbose_name="kehtetuks tehtud")
    issued_via = models.CharField(max_length=300, blank=True, default="", verbose_name="päritolu")

    class Meta:
        verbose_name = "MCP seadistuslink"
        verbose_name_plural = "MCP seadistuslingid"
        constraints = [
            models.UniqueConstraint(
                fields=["grant"],
                condition=models.Q(used_at__isnull=True, invalidated_at__isnull=True),
                name="mcp_access_one_live_link_per_grant",
            ),
        ]

    def __str__(self) -> str:
        return f"MCP seadistuslink {self.selector[:6]}…"

    def is_usable_at(self, moment: datetime) -> bool:
        return self.used_at is None and self.invalidated_at is None and self.expires_at > moment


class McpAuthorizationCode(BaseModel):
    """A one-time OAuth authorization code (RFC 6749 §4.1, PKCE S256 only).

    Bound to everything the token request must repeat: client, redirect URI,
    the PKCE challenge, scope and resource, and the account's security epoch at
    the moment the person typed their code. ``family_id`` names the tokens it
    was exchanged for, so a second presentation can revoke them.
    """

    digest = models.CharField(max_length=64, unique=True, verbose_name="räsi")
    grant = models.ForeignKey(
        McpAccessGrant,
        on_delete=models.PROTECT,
        related_name="authorization_codes",
        verbose_name="ligipääs",
    )
    user = models.ForeignKey(
        "accounts.User", on_delete=models.PROTECT, related_name="+", verbose_name="kasutaja"
    )
    client_id = models.CharField(max_length=500, verbose_name="klient")
    redirect_uri = models.CharField(max_length=1000, verbose_name="tagasisuunamise aadress")
    code_challenge = models.CharField(max_length=128, verbose_name="PKCE väljakutse")
    scope = models.CharField(max_length=200, verbose_name="ulatus")
    resource = models.CharField(max_length=1000, verbose_name="ressurss")
    user_epoch = models.PositiveIntegerField(verbose_name="kasutaja turvaversioon")
    expires_at = models.DateTimeField(verbose_name="aegub")
    used_at = models.DateTimeField(null=True, blank=True, verbose_name="kasutatud")
    family_id = models.UUIDField(null=True, blank=True, verbose_name="võtmepere")

    class Meta:
        verbose_name = "MCP autoriseerimiskood"
        verbose_name_plural = "MCP autoriseerimiskoodid"

    def __str__(self) -> str:
        return f"MCP autoriseerimiskood {self.pk}"


class McpToken(BaseModel):
    """An opaque OAuth access or refresh token, kept only as its SHA-256.

    ``family_id`` ties every token one authorization produced: the first access
    and refresh pair and every rotation after it. Reusing a rotated refresh
    token revokes the whole family. ``user_epoch`` is the account's
    ``security_epoch`` at issue: a role change, a deactivation or any other
    bump makes every outstanding token fail its next check.

    ``last_used_at`` is written only by the token endpoint on `web`; the `mcp`
    process cannot write and never tries to.
    """

    kind = models.CharField(max_length=8, choices=TokenKind.choices, verbose_name="liik")
    digest = models.CharField(max_length=64, unique=True, verbose_name="räsi")
    family_id = models.UUIDField(default=uuid.uuid4, db_index=True, verbose_name="võtmepere")
    grant = models.ForeignKey(
        McpAccessGrant, on_delete=models.PROTECT, related_name="tokens", verbose_name="ligipääs"
    )
    user = models.ForeignKey(
        "accounts.User", on_delete=models.PROTECT, related_name="+", verbose_name="kasutaja"
    )
    client_id = models.CharField(max_length=500, verbose_name="klient")
    #: Space-separated, as OAuth writes it.
    scopes = models.CharField(max_length=200, verbose_name="ulatus")
    resource = models.CharField(max_length=1000, verbose_name="ressurss")
    user_epoch = models.PositiveIntegerField(verbose_name="kasutaja turvaversioon")
    issued_at = models.DateTimeField(verbose_name="väljastatud")
    expires_at = models.DateTimeField(verbose_name="aegub")
    revoked_at = models.DateTimeField(null=True, blank=True, verbose_name="tühistatud")
    revoke_reason = models.CharField(
        max_length=64, blank=True, default="", verbose_name="tühistamise põhjus"
    )
    replaced_by = models.OneToOneField(
        "self",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="replaces",
        verbose_name="asendaja",
    )
    last_used_at = models.DateTimeField(null=True, blank=True, verbose_name="viimati kasutatud")

    class Meta:
        verbose_name = "MCP võti"
        verbose_name_plural = "MCP võtmed"
        indexes = [models.Index(fields=["grant", "revoked_at"], name="mcp_token_grant_revoked")]

    def __str__(self) -> str:
        return f"MCP {self.kind} {self.pk}"

    @property
    def scope_set(self) -> frozenset[str]:
        return frozenset(part for part in self.scopes.split() if part)


class McpAccessEvent(AppendOnlyModel):
    """What happened to MCP access, appended and never changed (a database trigger says so).

    Grant, enrolment, authorization, token issue, refresh, reuse detection,
    revocation — each with the account, the client, the outcome and the
    client's address (`app.core.client_ip`). ``detail`` holds reasons and
    identifiers, never a code, a token, a verifier or a secret.
    """

    event_type = models.CharField(
        max_length=32, choices=McpAccessEventType.choices, verbose_name="sündmus"
    )
    succeeded = models.BooleanField(default=True, verbose_name="õnnestus")
    user = models.ForeignKey(
        "accounts.User",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="+",
        verbose_name="kasutaja",
    )
    grant = models.ForeignKey(
        McpAccessGrant,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="events",
        verbose_name="ligipääs",
    )
    client_id = models.CharField(max_length=500, blank=True, default="", verbose_name="klient")
    ip_address = models.GenericIPAddressField(null=True, blank=True, verbose_name="IP-aadress")
    #: ``web`` for a request, or the operator's command and where it ran.
    provenance = models.CharField(max_length=300, blank=True, default="", verbose_name="päritolu")
    detail = models.JSONField(default=dict, blank=True, verbose_name="üksikasjad")

    class Meta:
        verbose_name = "MCP ligipääsu sündmus"
        verbose_name_plural = "MCP ligipääsu sündmused"
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.event_type} {self.created_at:%Y-%m-%d %H:%M}"
