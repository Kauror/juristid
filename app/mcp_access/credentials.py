"""Opaque credentials and the personal authenticator's secret.

Nothing here is home-grown cryptography: 256 random bits from the operating
system's CSPRNG (`secrets`), SHA-256 digests, constant-time comparison, RFC 6238
TOTP through `pyotp`, and the MFA key derivation that `app.accounts.mfa`
already uses — under a purpose of this app's own, so a secret sealed here can
never be opened as a personal-sign-in authenticator, or the other way round.

No function here logs, and none returns a raw credential except the ones that
mint it, whose callers hand it straight to the client (a response body, a
redirect to the registered URI) or to the operator's terminal.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import re
import secrets
from dataclasses import dataclass

import pyotp
from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from django.conf import settings

from app.accounts import mfa

#: The HKDF label of the MCP authenticator's secrets. Changing it would make
#: every enrolled MCP authenticator unreadable.
AUTHENTICATOR_PURPOSE = b"mcp-access-totp-secret-encryption-v1"

#: What the person's authenticator app shows as the account's issuer.
AUTHENTICATOR_ISSUER = "Juristid MCP"

#: The shape every opaque credential this app mints has: `token_urlsafe(32)` is
#: 43 characters of base64url. Anything else is refused before a query, so a
#: malformed value cannot be used to time a lookup.
_OPAQUE = re.compile(r"^[A-Za-z0-9_-]{43}$")
_SELECTOR = re.compile(r"^[0-9a-f]{32}$")
_DIGEST = re.compile(r"^[0-9a-f]{64}$")


def new_opaque() -> str:
    """256 random bits, base64url: an authorization code, an access or refresh token."""
    return secrets.token_urlsafe(32)


#: The HKDF label of the key a rotated refresh token's successor is derived
#: under (MCPSEC-09). Not a secret store: the successor is recomputed from the
#: token the client presents, so nothing raw is kept to answer a retry.
SUCCESSOR_PURPOSE = b"mcp-access-refresh-successor-v1"


def _successor_key_material() -> str:
    """`MCP_TOKEN_DERIVATION_KEY`, the successor chain's own key (NEW-01).

    Not `SECRET_KEY`: whoever holds that — a leaked juristid.env, an old
    backup — and one refresh token of a family could compute every later
    token of it offline, and so ride the chain without ever tripping reuse
    detection. A key that exists for this alone, on `web` only (the `mcp`
    process must not hold it, juristid.E046), keeps the two compromises
    separate. `SECRET_KEY` stands in only where `mcp_access.E005` does not
    require the key: tests, a laptop, CI.
    """
    return str(getattr(settings, "MCP_TOKEN_DERIVATION_KEY", "") or "") or settings.SECRET_KEY


def successor_pair(refresh_token: str) -> tuple[str, str]:
    """The access and refresh token a rotation of ``refresh_token`` issues.

    Deterministic under `MCP_TOKEN_DERIVATION_KEY`, so the same presented
    token yields the same pair: a retried refresh can be answered with what
    the first attempt issued, although only digests are stored. 256 bits
    each, base64url without padding — the shape `new_opaque` mints. Changing
    the key costs at most the retry of a rotation less than
    `MCP_REFRESH_GRACE_SECONDS` old.
    """
    key = HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=b"juristid-mcp-access",
        info=SUCCESSOR_PURPOSE,
    ).derive(_successor_key_material().encode("utf-8"))

    def derive(label: bytes) -> str:
        raw = hmac.new(key, label + b"|" + refresh_token.encode("ascii"), hashlib.sha256).digest()
        return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")

    return derive(b"access"), derive(b"refresh")


def digest(raw: str) -> str:
    return hashlib.sha256(raw.encode("ascii", "ignore")).hexdigest()


def opaque_digest(raw: object) -> str | None:
    """The lookup digest of a well-formed opaque credential, or None without a query."""
    if not isinstance(raw, str) or not _OPAQUE.match(raw):
        return None
    return digest(raw)


# -- enrolment links ---------------------------------------------------------


@dataclass(frozen=True)
class NewLink:
    selector: str
    verifier_digest: str
    #: ``<selector>.<verifier>``, for the operator's terminal and nowhere else.
    token: str


def new_link() -> NewLink:
    selector = secrets.token_hex(16)
    verifier = secrets.token_urlsafe(32)
    return NewLink(
        selector=selector, verifier_digest=digest(verifier), token=f"{selector}.{verifier}"
    )


def link_proof(raw: object) -> str:
    """What a session may keep of a link: ``<selector>.<digest>``, or "" when malformed.

    The verifier itself is not kept anywhere, as `app.accounts.tokens.proof_of`.
    """
    if not isinstance(raw, str):
        return ""
    selector, separator, verifier = raw.strip().partition(".")
    if not separator or not _SELECTOR.match(selector) or not _OPAQUE.match(verifier):
        return ""
    return f"{selector}.{digest(verifier)}"


def split_proof(proof: object) -> tuple[str, str] | None:
    if not isinstance(proof, str):
        return None
    selector, separator, verifier_digest = proof.strip().partition(".")
    if not separator or not _SELECTOR.match(selector) or not _DIGEST.match(verifier_digest):
        return None
    return selector, verifier_digest


def same(a: str, b: str) -> bool:
    return hmac.compare_digest(a.encode("ascii", "ignore"), b.encode("ascii", "ignore"))


# -- PKCE (RFC 7636), S256 only -------------------------------------------------

_PKCE_VALUE = re.compile(r"^[A-Za-z0-9._~-]{43,128}$")


def is_pkce_challenge(value: object) -> bool:
    """An S256 challenge is base64url of 32 bytes: exactly 43 characters."""
    return isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_-]{43}", value) is not None


def pkce_matches(verifier: object, challenge: str) -> bool:
    if not isinstance(verifier, str) or not _PKCE_VALUE.match(verifier):
        return False
    computed = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest())
        .rstrip(b"=")
        .decode("ascii")
    )
    return same(computed, challenge)


# -- the personal authenticator -------------------------------------------------


@dataclass(frozen=True)
class Enrolment:
    """What the enrolment page shows. Never stored in this form."""

    secret: str
    uri: str
    qr_svg: str
    #: The secret sealed under this app's purpose — the only form the page
    #: keeps between the GET that shows it and the POST that confirms it.
    sealed: str


def new_enrolment(account_label: str) -> Enrolment:
    return _enrolment(pyotp.random_base32(length=32), account_label)


def resume_enrolment(sealed: object, account_label: str) -> Enrolment | None:
    secret = unseal(sealed)
    return _enrolment(secret, account_label) if secret else None


def _enrolment(secret: str, account_label: str) -> Enrolment:
    uri = pyotp.TOTP(secret, digits=mfa.DIGITS, interval=mfa.INTERVAL).provisioning_uri(
        name=account_label, issuer_name=AUTHENTICATOR_ISSUER
    )
    return Enrolment(secret=secret, uri=uri, qr_svg=mfa.qr_svg(uri), sealed=seal(secret))


#: The HKDF salt of the dedicated MCP key (MCPSEC-07). Not the personal
#: sign-in's: the same key material could never open one as the other.
DEDICATED_KEY_SALT = b"juristid-mcp-access"


def _dedicated_fernet() -> Fernet | None:
    """Fernet under `MCP_AUTHENTICATOR_KEY`, when one is configured (MCPSEC-07).

    Without it the secrets are sealed by `app.accounts.mfa` under the MCP
    purpose, from `LOCAL_AUTH_MFA_ENCRYPTION_KEY or SECRET_KEY` — and on the
    shared-gate stack that is `SECRET_KEY`, so a routine `DJANGO_SECRET_KEY`
    rotation would make every MCP authenticator unreadable. A key of its own
    rotates only when somebody decides to (`mcp_access.E004` requires it on
    real data). The personal sign-in's encryption is not touched by it.
    """
    material = str(getattr(settings, "MCP_AUTHENTICATOR_KEY", "") or "")
    if not material:
        return None
    derived = HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=DEDICATED_KEY_SALT,
        info=AUTHENTICATOR_PURPOSE,
    ).derive(material.encode("utf-8"))
    return Fernet(base64.urlsafe_b64encode(derived))


def seal(secret: str) -> str:
    dedicated = _dedicated_fernet()
    if dedicated is not None:
        return dedicated.encrypt(secret.encode("ascii")).decode("ascii")
    return mfa.seal(secret, purpose=AUTHENTICATOR_PURPOSE)


def unseal(sealed: object) -> str | None:
    """The secret, or None when it is not sealed under the key configured now.

    There is no fallback from the dedicated key to the old one: changing the
    key is a decision to re-enrol everybody (docs/mcp/OPERATIONS.md), never a
    state in which two keys quietly both work.
    """
    if not isinstance(sealed, str) or not sealed:
        return None
    dedicated = _dedicated_fernet()
    if dedicated is not None:
        try:
            return dedicated.decrypt(sealed.encode("ascii")).decode("ascii")
        except (InvalidToken, ValueError):
            return None
    return mfa.unseal(sealed, purpose=AUTHENTICATOR_PURPOSE)


def matching_step(secret: str, code: object, *, after_step: int) -> int | None:
    """The unspent, current (±1 step) time step ``code`` belongs to, if any."""
    return mfa.matching_step(secret, code if isinstance(code, str) else "", after_step=after_step)
