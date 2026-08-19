"""Application identity.

The custom user model exists from migration 0001 and reserves the immutable
Microsoft Entra object identifier from the start, so production identity can be
switched on without a user-table rewrite (master specification 16.2).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from django.contrib.auth.base_user import AbstractBaseUser, BaseUserManager
from django.contrib.auth.models import PermissionsMixin
from django.db import models
from django.utils import timezone

from app.accounts.enums import UserRole
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

    def save(self, *args: Any, **kwargs: Any) -> None:
        self.upn = self.upn.strip().lower()
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
