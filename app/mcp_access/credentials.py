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


def seal(secret: str) -> str:
    return mfa.seal(secret, purpose=AUTHENTICATOR_PURPOSE)


def unseal(sealed: object) -> str | None:
    if not isinstance(sealed, str) or not sealed:
        return None
    return mfa.unseal(sealed, purpose=AUTHENTICATOR_PURPOSE)


def matching_step(secret: str, code: object, *, after_step: int) -> int | None:
    """The unspent, current (±1 step) time step ``code`` belongs to, if any."""
    return mfa.matching_step(secret, code if isinstance(code, str) else "", after_step=after_step)
