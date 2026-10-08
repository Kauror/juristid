"""The second factor: an authenticator app, and recovery codes (docs/adr/0145 §8).

TOTP is RFC 6238, computed by `pyotp` — a small, widely used implementation of
the standard — and nothing here computes an HMAC-based one-time password by
hand. What this module adds is everything around the algorithm that a library
cannot know about this application:

* **The secret is encrypted at rest** with Fernet (`cryptography`, already a
  dependency) under a key derived from `LOCAL_AUTH_MFA_ENCRYPTION_KEY`, which
  lives in the deployment's environment. A database backup on its own enrols
  nobody's phone.
* **Replay is refused.** The accepted time step is recorded and only later
  steps are accepted, so a code somebody watched being typed is spent.
* **Confirmation before activation.** A device is stored unconfirmed when the
  QR code is shown and becomes the account's second factor only when a code
  from it has been typed back.
* **Recovery codes are shown once** and stored as salted digests; each works
  once.
* **Throttled** per account through `app.accounts.throttle`, by the callers.

An e-mail is never a second factor here: whoever can read the mailbox can
already reset the password, so a code sent to the same mailbox proves nothing
the password did not.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import io
import secrets
from dataclasses import dataclass

import pyotp
import segno
from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from django.conf import settings
from django.db import transaction
from django.utils import timezone

from app.accounts.models import RecoveryCode, TotpDevice, User

#: RFC 6238 defaults, which every authenticator app understands.
DIGITS = 6
INTERVAL = 30
#: One step either side: a phone a few seconds out, or a code typed just as it
#: rolled over, still works. Wider would widen the window a watched code is
#: worth something in.
TOLERANCE_STEPS = 1

RECOVERY_CODE_COUNT = 10
#: 12 characters of base-32: 60 bits each. Grouped 4-4-4 for reading aloud.
_RECOVERY_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
_RECOVERY_LENGTH = 12

ISSUER = "Koda Õigusloome"


# -- the encryption key -------------------------------------------------------


def _fernet() -> Fernet:
    """Fernet keyed from the deployment's MFA key, through HKDF.

    A dedicated key rather than `SECRET_KEY` itself, because the two rotate for
    different reasons: rotating the Django secret is routine (it was done on
    2026-09-10) and must not silently unenrol every authenticator. Where no
    dedicated key is configured — a laptop, CI — `SECRET_KEY` stands in, under
    its own HKDF label; `juristid.E033` refuses that on a real-data
    local-password deployment.
    """
    material = (
        getattr(settings, "LOCAL_AUTH_MFA_ENCRYPTION_KEY", "") or settings.SECRET_KEY
    ).encode("utf-8")
    derived = HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=b"juristid-local-auth",
        info=b"totp-secret-encryption-v1",
    ).derive(material)
    return Fernet(base64.urlsafe_b64encode(derived))


def _encrypt(secret: str) -> str:
    return _fernet().encrypt(secret.encode("ascii")).decode("ascii")


def _decrypt(token: str) -> str | None:
    try:
        return _fernet().decrypt(token.encode("ascii")).decode("ascii")
    except (InvalidToken, ValueError):
        # A device enrolled under a key that is no longer configured cannot
        # verify anything. Answering "no" is the only safe reading; the
        # runbook's key-rotation section is the remedy.
        return None


# -- the device -----------------------------------------------------------------


def confirmed_device(user: User) -> TotpDevice | None:
    device = TotpDevice.objects.filter(user=user).first()
    return device if device is not None and device.is_confirmed else None


def has_second_factor(user: object) -> bool:
    if getattr(user, "pk", None) is None:
        return False
    return TotpDevice.objects.filter(user_id=user.pk, confirmed_at__isnull=False).exists()  # type: ignore[attr-defined]


@dataclass(frozen=True)
class Enrolment:
    """What the enrolment page shows: never stored in this form."""

    secret: str
    uri: str
    qr_svg: str
    #: The secret encrypted under the MFA key — the only form the enrolment
    #: page keeps between the GET that shows the code and the POST that
    #: confirms it. The session table is a database table like any other, and
    #: a plaintext secret there would undo the encryption on `TotpDevice`.
    sealed: str


def begin_enrolment(user: User) -> Enrolment:
    """A fresh secret for this person's next authenticator. Writes nothing.

    Nothing is stored until a code proves the phone has the secret. Until then a
    confirmed device the account already has stays its second factor —
    replacing a phone must not open a window in which the account has none.
    """
    secret = pyotp.random_base32(length=32)
    return _enrolment(user, secret)


def resume_enrolment(user: User, sealed: str) -> Enrolment | None:
    """The same enrolment again, from its sealed form, to redisplay the page."""
    secret = _decrypt(sealed or "")
    return _enrolment(user, secret) if secret else None


def _enrolment(user: User, secret: str) -> Enrolment:
    uri = pyotp.TOTP(secret, digits=DIGITS, interval=INTERVAL).provisioning_uri(
        name=user.upn, issuer_name=ISSUER
    )
    return Enrolment(secret=secret, uri=uri, qr_svg=_qr_svg(uri), sealed=_encrypt(secret))


def _qr_svg(uri: str) -> str:
    """The provisioning URI as inline SVG — no image request, no third party."""
    buffer = io.BytesIO()
    segno.make(uri, error="m").save(
        buffer, kind="svg", scale=4, border=2, xmldecl=False, svgns=True, dark="#000", light="#fff"
    )
    return buffer.getvalue().decode("utf-8")


def _matching_step(secret: str, code: str, *, after_step: int) -> int | None:
    """The time step ``code`` belongs to, if it is current and not yet spent."""
    # ASCII digits only: `str.isdigit` also accepts «١٢٣٤٥٦», which then reaches
    # `hmac.compare_digest` as non-ASCII text and raises instead of refusing.
    cleaned = "".join(ch for ch in (code or "") if ch in "0123456789")
    if len(cleaned) != DIGITS:
        return None
    totp = pyotp.TOTP(secret, digits=DIGITS, interval=INTERVAL)
    now = timezone.now()
    current = totp.timecode(now)
    matched = None
    # Every candidate is compared, whichever matches, so the time taken does
    # not say which neighbour a code belonged to.
    for offset in range(-TOLERANCE_STEPS, TOLERANCE_STEPS + 1):
        step = current + offset
        expected = totp.generate_otp(step)
        if hmac.compare_digest(expected, cleaned) and step > after_step and matched is None:
            matched = step
    return matched


@transaction.atomic
def confirm_enrolment(user: User, *, sealed: str, code: str) -> bool:
    """Make the sealed secret this person's second factor, if ``code`` proves the phone has it.

    Replaces a device the account already had, in one write: the old phone
    stops working at the moment the new one starts, never before.
    """
    secret = _decrypt(sealed or "")
    if secret is None:
        return False
    step = _matching_step(secret, code, after_step=0)
    if step is None:
        return False
    device = TotpDevice.objects.select_for_update().filter(user=user).first()
    now = timezone.now()
    if device is None:
        TotpDevice.objects.create(
            user=user, encrypted_secret=_encrypt(secret), confirmed_at=now, last_used_step=step
        )
    else:
        device.encrypted_secret = _encrypt(secret)
        device.confirmed_at = now
        device.last_used_step = step
        device.save(
            update_fields=["encrypted_secret", "confirmed_at", "last_used_step", "updated_at"]
        )
    return True


@transaction.atomic
def verify_totp(user: User, code: str) -> bool:
    """Whether ``code`` is a current, unspent code from this person's device.

    Spends it on success: the step is recorded under the device's lock, so the
    same code — or an earlier one — is refused from then on, in any worker.
    """
    device = (
        TotpDevice.objects.select_for_update().filter(user=user, confirmed_at__isnull=False).first()
    )
    if device is None:
        return False
    secret = _decrypt(device.encrypted_secret)
    if secret is None:
        return False
    step = _matching_step(secret, code, after_step=device.last_used_step)
    if step is None:
        return False
    device.last_used_step = step
    device.save(update_fields=["last_used_step", "updated_at"])
    return True


@transaction.atomic
def remove_second_factor(user: User) -> bool:
    """Delete the device and every recovery code. True if there was anything."""
    removed_devices, _ = TotpDevice.objects.filter(user=user).delete()
    removed_codes, _ = RecoveryCode.objects.filter(user=user).delete()
    return bool(removed_devices or removed_codes)


# -- recovery codes ------------------------------------------------------------


def _new_code() -> str:
    raw = "".join(secrets.choice(_RECOVERY_ALPHABET) for _ in range(_RECOVERY_LENGTH))
    return "-".join(raw[index : index + 4] for index in range(0, _RECOVERY_LENGTH, 4))


def _canonical_code(code: str) -> str:
    return "".join(ch for ch in (code or "").upper() if ch in _RECOVERY_ALPHABET)


def _code_digest(salt: str, code: str) -> str:
    return hashlib.sha256(f"{salt}:{_canonical_code(code)}".encode("ascii", "ignore")).hexdigest()


@transaction.atomic
def generate_recovery_codes(user: User) -> list[str]:
    """Replace this person's recovery codes. The plaintext is returned once."""
    RecoveryCode.objects.filter(user=user).delete()
    codes = [_new_code() for _ in range(RECOVERY_CODE_COUNT)]
    RecoveryCode.objects.bulk_create(
        [
            RecoveryCode(
                user=user,
                salt=(salt := secrets.token_hex(16)),
                code_digest=_code_digest(salt, code),
            )
            for code in codes
        ]
    )
    return codes


@transaction.atomic
def use_recovery_code(user: User, code: str) -> bool:
    """Spend one recovery code. Every unused code is compared, then at most one is spent."""
    canonical = _canonical_code(code)
    if len(canonical) != _RECOVERY_LENGTH:
        return False
    candidates = list(
        RecoveryCode.objects.select_for_update().filter(user=user, used_at__isnull=True)
    )
    matched = None
    for candidate in candidates:
        if hmac.compare_digest(candidate.code_digest, _code_digest(candidate.salt, canonical)):
            matched = matched or candidate
    if matched is None:
        return False
    matched.used_at = timezone.now()
    matched.save(update_fields=["used_at", "updated_at"])
    return True


def remaining_recovery_codes(user: User) -> int:
    return RecoveryCode.objects.filter(user=user, used_at__isnull=True).count()
