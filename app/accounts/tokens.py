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
  credentials in two mailboxes. Issuing takes the account's row lock first, so
  two issues for one person take turns rather than both invalidating nothing
  and both inserting, and a partial unique index holds "one live link per
  person and purpose" whatever the code does (docs/adr/0180 §5).
* **Bound to the account's credential state.** A link records a stamp of the
  account's password hash, login address and active flag at issue
  (`credential_stamp`) and is good only while it is unchanged, checked when it
  is opened and again, under the locks, when it is spent. A password change
  moves the stamp, so a link minted by a delayed mail job after the change —
  or any link from before it — sets nothing. A job that was queued under one
  stamp refuses to issue under another (`issue(expected_stamp=…)`). Not the
  `security_epoch`: that also moves on every role and permission change, and an
  invitation must survive the administrator adjusting the new colleague's
  rights before they activate.
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


_DIGEST = re.compile(r"^[0-9a-f]{64}$")


def proof_of(raw: str) -> str:
    """What a page may keep of a link once it has arrived: `<selector>.<digest>`.

    The verifier itself is not kept anywhere — not in the database, and not in
    the session either, which is a database table like any other. Its SHA-256
    is what the link's row stores, so comparing the two proves the holder had
    the link without anybody being able to turn the stored form back into one.
    A malformed link yields "" and is refused without a query.
    """
    selector, separator, verifier = (raw or "").strip().partition(".")
    if not separator or not _SELECTOR.match(selector) or not _VERIFIER.match(verifier):
        return ""
    return f"{selector}.{_digest(verifier)}"


def _split(proof: str) -> tuple[str, str] | None:
    selector, separator, digest = (proof or "").strip().partition(".")
    if not separator or not _SELECTOR.match(selector) or not _DIGEST.match(digest):
        return None
    return selector, digest


class StaleIssuance(Exception):
    """The account's credentials moved since the link was asked for.

    Raised by `issue` when ``expected_stamp`` no longer matches: a password was
    set or changed, the login address changed, the account switched off,
    between the request and the job that would have minted the link. Nothing
    was written.
    """


def credential_stamp(user: User) -> str:
    """A digest of what a one-time link is a credential *for*.

    The password hash (any set, change or clear moves it), the login address
    (a link mailed to an address the account no longer signs in with) and the
    active flag. Derived, not a counter, so no code path that changes a
    credential can forget to bump it.
    """
    material = f"{user.password}{user.upn}{int(user.is_active)}"
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


@transaction.atomic
def issue(
    *,
    user: User,
    purpose: str,
    issued_by: User | None = None,
    expected_stamp: str | None = None,
) -> IssuedLink:
    """A fresh link for ``purpose``, superseding any that was still outstanding.

    **Under the account's row lock**, taken before anything is invalidated. Two
    issues for one person used to run invalidate-then-insert side by side: each
    invalidated the links committed before it, neither saw the other's, and
    both inserted — two live links in two mailboxes. Now the second waits for
    the first to commit and then supersedes it; the partial unique index
    ``accounts_token_one_live_per_purpose`` is the backstop.

    The lock is also what makes ``expected_stamp`` mean something: the stamp is
    read under it, so a password change either committed before this read (and
    the link is refused with `StaleIssuance`) or waits for this transaction and
    then invalidates the link it issued.
    """
    locked = (
        User.objects.select_for_update().only("pk", "password", "upn", "is_active").get(pk=user.pk)
    )
    stamp = credential_stamp(locked)
    if expected_stamp is not None and stamp != expected_stamp:
        raise StaleIssuance("the account's credentials changed after the link was requested")
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
        credential_stamp=stamp,
    )
    return IssuedLink(record=record, token=f"{selector}.{verifier}")


def is_current(record: AccountCredentialToken, holder: User) -> bool:
    """Whether the link was issued under the account's present credentials."""
    return hmac.compare_digest(record.credential_stamp, credential_stamp(holder))


def peek(proof: str, *, purpose: str) -> AccountCredentialToken | None:
    """The usable link this value names, without spending it.

    For the GET that shows the set-password form: somebody opening a stale or
    spent link should be told so before they type a new password into a form
    that cannot accept it.
    """
    parts = _split(proof)
    if parts is None:
        return None
    selector, digest = parts
    record = (
        AccountCredentialToken.objects.select_related("user")
        .filter(selector=selector, purpose=purpose)
        .first()
    )
    if record is None or not hmac.compare_digest(record.verifier_digest, digest):
        return None
    if not is_current(record, record.user):
        return None
    return record if record.is_usable_at(timezone.now()) else None


def consume(proof: str, *, purpose: str) -> AccountCredentialToken | None:
    """Spend the link, once. ``None`` for every way it can be wrong.

    Must run inside the caller's transaction, so that spending the link and
    the change it authorises — a password set, an account activated — commit
    or roll back together. A link marked used for a password that was then
    refused would be a link lost for nothing.
    """
    if not transaction.get_connection().in_atomic_block:  # pragma: no cover - misuse
        raise RuntimeError("a credential link is consumed inside the change it authorises")
    parts = _split(proof)
    if parts is None:
        return None
    selector, digest = parts
    # The account first, then the link: the order `issue` takes them in, so a
    # link being spent and one being issued for the same person cannot each
    # hold the lock the other is waiting for.
    owner_id = (
        AccountCredentialToken.objects.filter(selector=selector, purpose=purpose)
        .values_list("user_id", flat=True)
        .first()
    )
    if owner_id is None:
        return None
    holder = User.objects.select_for_update().filter(pk=owner_id).first()
    record = (
        AccountCredentialToken.objects.select_for_update()
        .filter(selector=selector, purpose=purpose)
        .first()
    )
    if holder is None or record is None or not hmac.compare_digest(record.verifier_digest, digest):
        return None
    now = timezone.now()
    if not record.is_usable_at(now) or not is_current(record, holder):
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
