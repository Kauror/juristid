"""One-time links for activation and password reset (docs/adr/0145 §7).

A link is `<selector>.<verifier>`. The selector — 128 random bits, hex — finds
the row; the verifier — 256 random bits — proves the holder has the link, and
only its SHA-256 digest is ever stored. Both come from `secrets`, the
operating system's CSPRNG. Nothing here is home-grown cryptography: random
bytes, a standard digest and a constant-time comparison.

What the rest of the module holds, and why:

* **Single use.** `consume` takes the row's lock, checks it is still usable,
  compares the verifier and marks it used — one step, so two submissions of the
  same link cannot both succeed.
* **Superseded.** Issuing a link invalidates every older outstanding link of
  the same purpose for that person, so "send it again" never leaves two live
  credentials in two mailboxes.
* **Short-lived and configurable.** `LOCAL_AUTH_ACTIVATION_LINK_SECONDS` and
  `LOCAL_AUTH_RESET_LINK_SECONDS`.
* **Never logged.** No function here logs, and none returns the link except
  `issue`, whose only caller hands it straight to the mail abstraction.
"""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets
from dataclasses import dataclass
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from app.accounts.enums import CredentialTokenPurpose
from app.accounts.models import AccountCredentialToken, User

#: The shape a link must have before the database is asked about it. Anything
#: else is refused without a query, so a malformed value cannot be used to
#: measure how long a lookup takes.
_SELECTOR = re.compile(r"^[0-9a-f]{32}$")
_VERIFIER = re.compile(r"^[A-Za-z0-9_-]{40,64}$")


@dataclass(frozen=True)
class IssuedLink:
    record: AccountCredentialToken
    #: The secret the recipient's link carries. Exists in memory long enough to
    #: be put into one e-mail and nowhere else.
    token: str


def lifetime(purpose: str) -> timedelta:
    if purpose == CredentialTokenPurpose.ACTIVATION:
        return timedelta(seconds=int(settings.LOCAL_AUTH_ACTIVATION_LINK_SECONDS))
    return timedelta(seconds=int(settings.LOCAL_AUTH_RESET_LINK_SECONDS))


def _digest(verifier: str) -> str:
    return hashlib.sha256(verifier.encode("ascii")).hexdigest()


def _split(raw: str) -> tuple[str, str] | None:
    selector, separator, verifier = (raw or "").strip().partition(".")
    if not separator or not _SELECTOR.match(selector) or not _VERIFIER.match(verifier):
        return None
    return selector, verifier


@transaction.atomic
def issue(*, user: User, purpose: str, issued_by: User | None = None) -> IssuedLink:
    """A fresh link for ``purpose``, superseding any that was still outstanding."""
    invalidate_outstanding(user=user, purpose=purpose, reason="superseded")
    selector = secrets.token_hex(16)
    verifier = secrets.token_urlsafe(32)
    now = timezone.now()
    record = AccountCredentialToken.objects.create(
        user=user,
        purpose=purpose,
        selector=selector,
        verifier_digest=_digest(verifier),
        expires_at=now + lifetime(purpose),
        issued_by=issued_by,
    )
    return IssuedLink(record=record, token=f"{selector}.{verifier}")


def peek(raw: str, *, purpose: str) -> AccountCredentialToken | None:
    """The usable link this value names, without spending it.

    For the GET that shows the set-password form: somebody opening a stale or
    spent link should be told so before they type a new password into a form
    that cannot accept it.
    """
    parts = _split(raw)
    if parts is None:
        return None
    selector, verifier = parts
    record = (
        AccountCredentialToken.objects.select_related("user")
        .filter(selector=selector, purpose=purpose)
        .first()
    )
    if record is None or not hmac.compare_digest(record.verifier_digest, _digest(verifier)):
        return None
    return record if record.is_usable_at(timezone.now()) else None


def consume(raw: str, *, purpose: str) -> AccountCredentialToken | None:
    """Spend the link, once. ``None`` for every way it can be wrong.

    Must run inside the caller's transaction, so that spending the link and
    the change it authorises — a password set, an account activated — commit
    or roll back together. A link marked used for a password that was then
    refused would be a link lost for nothing.
    """
    if not transaction.get_connection().in_atomic_block:  # pragma: no cover - misuse
        raise RuntimeError("a credential link is consumed inside the change it authorises")
    parts = _split(raw)
    if parts is None:
        return None
    selector, verifier = parts
    record = (
        AccountCredentialToken.objects.select_for_update()
        .filter(selector=selector, purpose=purpose)
        .first()
    )
    if record is None or not hmac.compare_digest(record.verifier_digest, _digest(verifier)):
        return None
    now = timezone.now()
    if not record.is_usable_at(now):
        return None
    record.used_at = now
    record.save(update_fields=["used_at", "updated_at"])
    return record


def invalidate_outstanding(*, user: User, purpose: str | None = None, reason: str) -> int:
    """End every unused, unexpired link this person holds (of one purpose, if given)."""
    outstanding = AccountCredentialToken.objects.filter(
        user=user, used_at__isnull=True, invalidated_at__isnull=True
    )
    if purpose is not None:
        outstanding = outstanding.filter(purpose=purpose)
    return outstanding.update(
        invalidated_at=timezone.now(), invalidation_reason=reason[:64], updated_at=timezone.now()
    )


def has_outstanding(*, user: User, purpose: str) -> bool:
    return AccountCredentialToken.objects.filter(
        user=user,
        purpose=purpose,
        used_at__isnull=True,
        invalidated_at__isnull=True,
        expires_at__gt=timezone.now(),
    ).exists()
