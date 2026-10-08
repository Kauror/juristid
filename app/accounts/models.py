"""Application identity.

The custom user model exists from migration 0001 and reserves the immutable
Microsoft Entra object identifier from the start, so production identity can be
switched on without a user-table rewrite (master specification 16.2).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from typing import Any

from django.contrib.auth.base_user import AbstractBaseUser, BaseUserManager
from django.contrib.auth.models import PermissionsMixin
from django.db import models
from django.utils import timezone
from django.utils.crypto import salted_hmac

from app.accounts.enums import (
    AccountStatus,
    CredentialTokenPurpose,
    ProvisioningState,
    ThrottleScope,
    UserRole,
)
from app.core.errors import InvariantViolation
from app.core.models import BaseModel


class UserManager(BaseUserManager["User"]):
    use_in_migrations = False

    def _create_user(self, upn: str, password: str | None, **extra: Any) -> User:
        if not upn:
            raise ValueError("A user principal name (UPN) is required.")
        upn = upn.strip().lower()
        extra.setdefault("email", upn if "@" in upn else "")
        user = self.model(upn=upn, **extra)
        if password:
            user.set_password(password)
        else:
            user.set_unusable_password()
        user.save(using=self._db)
        return user

    def create_user(self, upn: str, password: str | None = None, **extra: Any) -> User:
        extra.setdefault("role", UserRole.SPECIALIST)
        extra.setdefault("is_staff", False)
        extra.setdefault("is_superuser", False)
        return self._create_user(upn, password, **extra)

    def create_superuser(self, upn: str, password: str | None = None, **extra: Any) -> User:
        extra.setdefault("role", UserRole.ADMINISTRATOR)
        extra["is_staff"] = True
        extra["is_superuser"] = True
        extra.setdefault("display_name", upn)
        return self._create_user(upn, password, **extra)


class User(AbstractBaseUser, PermissionsMixin, BaseModel):
    entra_object_id = models.UUIDField(
        null=True,
        blank=True,
        unique=True,
        verbose_name="Entra objekti ID",
        help_text="Muutumatu Microsoft Entra ID. Tühi ainult sünteetilistel arenduskasutajatel.",
    )
    upn = models.CharField(
        max_length=320,
        unique=True,
        verbose_name="kasutajanimi (UPN)",
    )
    email = models.EmailField(blank=True, verbose_name="e-post")
    display_name = models.CharField(max_length=200, verbose_name="kuvatav nimi")
    role = models.CharField(
        max_length=32,
        choices=UserRole.choices,
        default=UserRole.SPECIALIST,
        verbose_name="roll",
    )
    is_active = models.BooleanField(default=True, verbose_name="aktiivne")
    is_staff = models.BooleanField(
        default=False,
        verbose_name="tehniline haldusligipääs",
        help_text="Annab ligipääsu Django haldusliidesele, mitte piiratud sisule.",
    )
    is_synthetic = models.BooleanField(
        default=False,
        verbose_name="sünteetiline arenduskasutaja",
        help_text="Ainult isoleeritud arenduskeskkonnas, mitte päris andmetega keskkonnas.",
    )
    date_joined = models.DateTimeField(default=timezone.now, verbose_name="loodud")

    # -- the account lifecycle (docs/adr/0145 §5) ----------------------------
    #
    # Every column below is additive and defaults to what an existing account
    # already is: ACTIVATED, no overrides, no local password, never signed in
    # here. The rows that existed before this lifecycle need no data migration
    # and no rewrite, and nothing about them changes until somebody decides it.
    provisioning_state = models.CharField(
        max_length=16,
        choices=ProvisioningState.choices,
        default=ProvisioningState.ACTIVATED,
        verbose_name="konto seis",
        help_text="Uus konto ootab kinnitust, siis kutset; kasutatavaks teeb selle ainult "
        "inimene ise, oma parooli seades.",
    )
    capability_overrides = models.JSONField(
        default=dict,
        blank=True,
        verbose_name="isiklikud õigused",
        help_text="Rolli vaikimisi õigustest erinevad load ja keelud. Muudetakse ainult "
        "kasutajate halduse kaudu.",
    )
    #: Bumped whenever something about this account changes that every open
    #: session must answer for afresh — a password, a role, a capability, a
    #: deactivation. Folded into the session's authentication hash, so a bump
    #: ends every session the account has, in every worker, on its next
    #: request (see `_get_session_auth_hash`).
    security_epoch = models.PositiveIntegerField(default=0, verbose_name="turvaversioon")
    local_password_set_at = models.DateTimeField(
        null=True, blank=True, verbose_name="isiklik parool seatud"
    )
    last_authenticated_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name="viimane isiklik sisselogimine",
        help_text="Ainult isikliku parooliga sisselogimine. Jagatud värava taga kasutaja "
        "valimine ei ole sisselogimine ega muuda seda.",
    )
    created_by = models.ForeignKey(
        "self",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="accounts_created",
        verbose_name="lõi",
    )
    approved_at = models.DateTimeField(null=True, blank=True, verbose_name="kinnitatud")
    approved_by = models.ForeignKey(
        "self",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="accounts_approved",
        verbose_name="kinnitas",
    )
    #: An address outside the allowed domains, approved for this one account.
    #: Never a domain: approving one address says nothing about its neighbours.
    email_exception_reason = models.TextField(
        blank=True, verbose_name="välise aadressi erandi põhjus"
    )
    email_exception_approved_at = models.DateTimeField(
        null=True, blank=True, verbose_name="välise aadressi erand kinnitatud"
    )
    email_exception_approved_by = models.ForeignKey(
        "self",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="email_exceptions_approved",
        verbose_name="välise aadressi erandi kinnitas",
    )

    objects = UserManager()

    USERNAME_FIELD = "upn"
    EMAIL_FIELD = "email"
    REQUIRED_FIELDS = ["display_name"]

    class Meta:
        verbose_name = "kasutaja"
        verbose_name_plural = "kasutajad"
        ordering = ["display_name"]
        constraints = [
            # A synthetic development account can never carry a real identity.
            models.CheckConstraint(
                condition=~models.Q(is_synthetic=True, entra_object_id__isnull=False),
                name="accounts_user_synthetic_has_no_entra_identity",
            ),
            # Saving an account never makes it usable (docs/adr/0145 §5). An
            # account still waiting for approval or for its owner to accept the
            # invitation is never active, in any authentication mode — the
            # Cloudflare branch signs in *any* active account whose address
            # matches, so this is the rule that keeps a half-made account from
            # being a seat. Enforced here because `QuerySet.update`, a shell
            # session and the Django admin's checkbox all bypass `save()`.
            models.CheckConstraint(
                condition=~models.Q(
                    is_active=True,
                    provisioning_state__in=[
                        ProvisioningState.PENDING,
                        ProvisioningState.INVITED,
                    ],
                ),
                name="accounts_user_unactivated_is_inactive",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.display_name} ({self.upn})"

    @property
    def initials(self) -> str:
        """Two letters for the avatar. Falls back to the UPN for odd names."""
        parts = [part for part in self.display_name.split() if part]
        if len(parts) >= 2:
            return (parts[0][:1] + parts[-1][:1]).upper()
        if parts:
            return parts[0][:2].upper()
        return self.upn[:2].upper()

    def get_full_name(self) -> str:
        return self.display_name

    def get_short_name(self) -> str:
        return self.display_name.split(" ")[0] if self.display_name else self.upn

    @property
    def account_status(self) -> str:
        """Pending, invited, active or switched off — derived, never stored."""
        if self.provisioning_state == ProvisioningState.PENDING:
            return AccountStatus.PENDING
        if self.provisioning_state == ProvisioningState.INVITED:
            return AccountStatus.INVITED
        return AccountStatus.ACTIVE if self.is_active else AccountStatus.DISABLED

    @property
    def account_status_label(self) -> str:
        return AccountStatus(self.account_status).label

    @property
    def has_local_password(self) -> bool:
        """Whether this person has ever set a password of their own here."""
        return self.local_password_set_at is not None and self.has_usable_password()

    def _get_session_auth_hash(self, secret: str | None = None) -> str:
        """Django's session hash, with the security epoch folded in.

        Django already ends every other session when a password changes: each
        session stores this hash at sign-in and `get_user` compares it on every
        request. Folding `security_epoch` into the same hash extends that one
        mechanism to the other changes an open session must not survive — a
        role, a capability, a deactivation, a second factor removed — with no
        second mechanism to keep in step (docs/adr/0145 §8).

        **At epoch 0 the hash is byte-for-byte Django's own.** Every account
        that exists today is at 0, so deploying this changes no stored session
        and signs nobody out — the shared gate's persona sessions included. Only
        an account whose epoch has actually been bumped gets the new form.
        """
        key_salt = "django.contrib.auth.models.AbstractBaseUser.get_session_auth_hash"
        value = (
            self.password
            if not self.security_epoch
            else f"{self.password}\x1f{self.security_epoch}"
        )
        return salted_hmac(key_salt, value, secret=secret, algorithm="sha256").hexdigest()

    def save(self, *args: Any, **kwargs: Any) -> None:
        self.upn = self.upn.strip().lower()
        if self.is_active and self.provisioning_state in (
            ProvisioningState.PENDING,
            ProvisioningState.INVITED,
        ):
            # The database refuses this too (`accounts_user_unactivated_is_
            # inactive`); saying so here gives the caller a sentence rather
            # than an IntegrityError.
            raise InvariantViolation(
                "An account that has not been activated by its owner cannot be switched on."
            )
        if not self._state.adding:
            previously = (
                type(self)
                .objects.filter(pk=self.pk)
                .values_list("entra_object_id", flat=True)
                .first()
            )
            if previously is not None and previously != self.entra_object_id:
                raise InvariantViolation(
                    "entra_object_id is immutable once assigned; create a new user instead."
                )
        super().save(*args, **kwargs)


class BreakGlassGrantQuerySet(models.QuerySet):
    def active_at(self, moment: datetime) -> BreakGlassGrantQuerySet:
        return self.filter(
            revoked_at__isnull=True,
            starts_at__lte=moment,
            expires_at__gt=moment,
        )


class BreakGlassGrant(BaseModel):
    """Time-bounded emergency access to RESTRICTED content.

    Support work that genuinely needs restricted business content uses one of
    these instead of a permanently privileged account. Every grant, and every
    use of one, is written to the security audit trail.
    """

    user = models.ForeignKey(
        "accounts.User",
        on_delete=models.CASCADE,
        related_name="break_glass_grants",
        verbose_name="kasutaja",
    )
    granted_by = models.ForeignKey(
        "accounts.User",
        on_delete=models.PROTECT,
        related_name="break_glass_grants_given",
        verbose_name="andis",
    )
    reason = models.TextField(verbose_name="põhjus")
    starts_at = models.DateTimeField(verbose_name="algab")
    expires_at = models.DateTimeField(verbose_name="lõpeb")
    revoked_at = models.DateTimeField(null=True, blank=True, verbose_name="tühistatud")
    revoked_by = models.ForeignKey(
        "accounts.User",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="break_glass_grants_revoked",
        verbose_name="tühistas",
    )

    objects = BreakGlassGrantQuerySet.as_manager()

    class Meta:
        verbose_name = "hädaligipääs"
        verbose_name_plural = "hädaligipääsud"
        ordering = ["-created_at"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(expires_at__gt=models.F("starts_at")),
                name="accounts_breakglass_expires_after_start",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.user_id} {self.starts_at:%Y-%m-%d} – {self.expires_at:%Y-%m-%d}"

    def is_active_at(self, moment: datetime) -> bool:
        return self.revoked_at is None and self.starts_at <= moment < self.expires_at

    @property
    def grant_id(self) -> uuid.UUID:
        return self.id


class SharedGateThrottle(BaseModel):
    """Failed shared-password attempts, per client, across every worker.

    A table rather than a cache entry. The cache would be shared between
    gunicorn workers and would survive a restart, but it is also *evictable* —
    Django's database cache culls a third of its rows when it grows past
    `MAX_ENTRIES`, and a lockout an attacker can flush by making noise is not a
    lockout (Stage-2D auth brief 9).

    Scoped to one client key and never global. A global counter would mean one
    attacker can lock the department out of its own system: a denial-of-service
    primitive wearing a control's clothes. Escalation is capped for the same
    reason — long enough that guessing stops being viable, bounded so nothing
    becomes permanent.
    """

    client_key = models.CharField(
        max_length=64,
        unique=True,
        verbose_name="kliendi tunnus",
        help_text="Aadressi räsi, mitte aadress ise.",
    )
    failures = models.PositiveIntegerField(default=0, verbose_name="ebaõnnestumisi")
    lockout_cycles = models.PositiveIntegerField(default=0, verbose_name="lukustusi")
    locked_until = models.DateTimeField(null=True, blank=True, verbose_name="lukus kuni")
    last_failure_at = models.DateTimeField(null=True, blank=True, verbose_name="viimane katse")

    class Meta:
        verbose_name = "jagatud värava piirang"
        verbose_name_plural = "jagatud värava piirangud"
        ordering = ["-last_failure_at"]

    def __str__(self) -> str:
        return f"{self.client_key[:12]}… {self.failures} failure(s)"

    def seconds_remaining(self, *, now: datetime | None = None) -> int:
        now = now or timezone.now()
        if self.locked_until is None or self.locked_until <= now:
            return 0
        return int((self.locked_until - now).total_seconds()) + 1

    def register_failure(
        self,
        *,
        max_attempts: int,
        base_seconds: int,
        ceiling_seconds: int,
        now: datetime | None = None,
    ) -> int:
        """Count one wrong password and return the wait it earns, in seconds.

        Each completed lockout cycle doubles the next one, so a scripted attack
        slows geometrically while a person who mistyped waits five minutes once.

        **Call this on a row held under its own lock.** The arithmetic is a
        read-modify-write over four columns and there is no version check under
        it, so two callers holding the same row unlocked will each read the same
        counter and each write the same value — which is not a slow lockout but
        no lockout at all (SEC-01). `app.accounts.shared_gate.record_failure` is
        the caller that takes the lock; a second caller must do the same rather
        than reach for this on an instance it happens to be holding.
        """
        now = now or timezone.now()
        self.failures += 1
        self.last_failure_at = now

        seconds = 0
        if self.failures >= max_attempts:
            seconds = min(base_seconds * (2**self.lockout_cycles), ceiling_seconds)
            self.locked_until = now + timedelta(seconds=seconds)
            self.lockout_cycles += 1
            # The attempt counter restarts; the cycle counter does not. That is
            # what makes the *next* lockout longer than this one.
            self.failures = 0

        self.save(
            update_fields=[
                "failures",
                "lockout_cycles",
                "locked_until",
                "last_failure_at",
                "updated_at",
            ]
        )
        return seconds


# ---------------------------------------------------------------------------
# Local authentication (docs/adr/0145). Dormant unless AUTH_MODE=local_password.
# ---------------------------------------------------------------------------


class AccountCredentialToken(BaseModel):
    """One one-time link: account activation or a password reset.

    **The link is never stored.** It is a public *selector*, which finds the
    row, and a secret *verifier*, of which only a SHA-256 digest is kept. A
    copy of this table — a backup, a support export, a stolen dump — therefore
    activates nothing: the verifier has 256 bits of entropy, so its digest
    cannot be walked back, and nothing else here is a credential.

    Single use, short-lived and superseded: `used_at` is set under the row's
    lock by the one request that spends it, a newer link of the same purpose
    invalidates every older one, and a password change or deactivation
    invalidates them all (`app.accounts.tokens`).
    """

    user = models.ForeignKey(
        "accounts.User",
        on_delete=models.PROTECT,
        related_name="credential_tokens",
        verbose_name="kasutaja",
    )
    purpose = models.CharField(
        max_length=32, choices=CredentialTokenPurpose.choices, verbose_name="eesmärk"
    )
    selector = models.CharField(max_length=32, unique=True, verbose_name="valija")
    verifier_digest = models.CharField(max_length=64, verbose_name="kontrollväärtuse räsi")
    expires_at = models.DateTimeField(verbose_name="aegub")
    used_at = models.DateTimeField(null=True, blank=True, verbose_name="kasutatud")
    invalidated_at = models.DateTimeField(null=True, blank=True, verbose_name="tühistatud")
    invalidation_reason = models.CharField(
        max_length=64, blank=True, verbose_name="tühistamise põhjus"
    )
    issued_by = models.ForeignKey(
        "accounts.User",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="credential_tokens_issued",
        verbose_name="väljastas",
    )

    class Meta:
        verbose_name = "ühekordne link"
        verbose_name_plural = "ühekordsed lingid"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["user", "purpose"], name="accounts_token_user_purpose"),
        ]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(expires_at__gt=models.F("created_at")),
                name="accounts_token_expires_after_issue",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.get_purpose_display()} · {self.user_id}"

    def is_usable_at(self, moment: datetime) -> bool:
        return self.used_at is None and self.invalidated_at is None and moment < self.expires_at


class TotpDevice(BaseModel):
    """A person's authenticator app: one per account.

    The shared secret has to be readable to verify a code, so it cannot be
    hashed like a password. It is stored **encrypted** with a key that lives in
    the deployment's environment and not in the database
    (`LOCAL_AUTH_MFA_ENCRYPTION_KEY`, `app.accounts.mfa`), so the table on its
    own — in a backup, say — enrols nobody's phone.

    `last_used_step` is the replay guard. A TOTP code is valid for its 30-second
    step and the neighbours either side; recording the step a code was accepted
    for, and accepting only later ones, means a code somebody watched being
    typed is worth nothing a second time (RFC 6238 §5.2).
    """

    user = models.OneToOneField(
        "accounts.User",
        on_delete=models.PROTECT,
        related_name="totp_device",
        verbose_name="kasutaja",
    )
    encrypted_secret = models.TextField(verbose_name="krüpteeritud saladus")
    confirmed_at = models.DateTimeField(null=True, blank=True, verbose_name="kinnitatud")
    last_used_step = models.BigIntegerField(default=0, verbose_name="viimati kasutatud samm")

    class Meta:
        verbose_name = "autentimisrakendus"
        verbose_name_plural = "autentimisrakendused"

    def __str__(self) -> str:
        return f"TOTP · {self.user_id}"

    @property
    def is_confirmed(self) -> bool:
        return self.confirmed_at is not None


class RecoveryCode(BaseModel):
    """One single-use recovery code, stored as a salted digest only.

    Shown to its owner once, when generated, and never again. The codes carry
    60 bits of randomness each, so a salted SHA-256 is enough to make a stolen
    table useless without making every sign-in pay a password hash per code.
    """

    user = models.ForeignKey(
        "accounts.User",
        on_delete=models.PROTECT,
        related_name="recovery_codes",
        verbose_name="kasutaja",
    )
    salt = models.CharField(max_length=32, verbose_name="sool")
    code_digest = models.CharField(max_length=64, verbose_name="koodi räsi")
    used_at = models.DateTimeField(null=True, blank=True, verbose_name="kasutatud")

    class Meta:
        verbose_name = "taastekood"
        verbose_name_plural = "taastekoodid"
        ordering = ["created_at"]

    def __str__(self) -> str:
        return f"taastekood · {self.user_id}"


class AuthenticationThrottle(BaseModel):
    """Failed attempts against one counter, shared by every worker.

    The same reasoning as `SharedGateThrottle` and deliberately not the same
    table: that one guards the shared door production runs today, and nothing
    in this dormant feature touches it. A table rather than the cache because
    the database cache is *evictable* — a lockout an attacker can flush by
    making noise is not a lockout (docs/adr/0016).

    Keyed by a scope and an HMAC of the thing being counted — an address typed
    into the form, a network, an account — so the table holds no address or
    network in the clear, and an address that matches no account is counted
    exactly like one that does: the throttle is not an oracle for who works
    here.

    The counter decays. A failure older than the window does not count towards
    the next lockout, and the escalation resets after a quiet day, so a person
    who mistypes once a week is never one typo from an hour's wait.
    """

    scope = models.CharField(max_length=32, choices=ThrottleScope.choices, verbose_name="ulatus")
    key_digest = models.CharField(max_length=64, verbose_name="tunnuse räsi")
    failures = models.PositiveIntegerField(default=0, verbose_name="ebaõnnestumisi")
    lockout_cycles = models.PositiveIntegerField(default=0, verbose_name="lukustusi")
    locked_until = models.DateTimeField(null=True, blank=True, verbose_name="lukus kuni")
    last_failure_at = models.DateTimeField(null=True, blank=True, verbose_name="viimane katse")

    class Meta:
        verbose_name = "autentimise piirang"
        verbose_name_plural = "autentimise piirangud"
        ordering = ["-last_failure_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["scope", "key_digest"], name="accounts_auththrottle_scope_key"
            ),
        ]

    def __str__(self) -> str:
        return f"{self.scope} {self.key_digest[:12]}… {self.failures}"

    def seconds_remaining(self, *, now: datetime | None = None) -> int:
        now = now or timezone.now()
        if self.locked_until is None or self.locked_until <= now:
            return 0
        return int((self.locked_until - now).total_seconds()) + 1
