"""Which e-mail addresses may become a sign-in identity (docs/adr/0145 §4).

**One normal form.** Lower case, surrounding space removed, Unicode NFKC —
applied identically when an account is created, when a person signs in, when a
reset is requested and when an address is compared for duplicates. Two spellings
of one address are one identity, and the database's unique `upn` enforces it.

**Only the Chamber's own domain**, by default `koda.ee`
(`ACCOUNT_ALLOWED_EMAIL_DOMAINS`). An administrator who holds the delegation
right may approve **one** external address as an exception, with a written
reason that the security audit keeps beside who approved it. Approving one
address says nothing about its domain: the next address there needs its own
decision.

**No ambiguous aliases.** A `+tag` in the local part is refused: it reaches
the same mailbox as the address without it, so allowing it would allow two
accounts for one person — exactly the silent duplicate the unique column exists
to prevent. Non-ASCII addresses are refused for the same reason: an
internationalised local part has more than one spelling.
"""

from __future__ import annotations

import unicodedata

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import validate_email

INVALID_ADDRESS = "Sisesta kehtiv e-posti aadress."
NON_ASCII_ADDRESS = "E-posti aadress võib sisaldada ainult ladina tähti, numbreid ja tavamärke."
SUBADDRESS = "Aadress ei tohi sisaldada «+» märki: see oleks sama postkasti teine nimi."
DOMAIN_NOT_ALLOWED = (
    "Lubatud on ainult Koja aadressid ({domains}). Välise aadressi jaoks on vaja "
    "haldusõiguste andja erandit koos põhjusega."
)


def normalise(raw: str) -> str:
    return unicodedata.normalize("NFKC", raw or "").strip().lower()


def allowed_domains() -> tuple[str, ...]:
    configured = getattr(settings, "ACCOUNT_ALLOWED_EMAIL_DOMAINS", None) or ("koda.ee",)
    return tuple(normalise(domain).lstrip("@") for domain in configured if domain)


def domain_of(address: str) -> str:
    return normalise(address).rpartition("@")[2]


def is_allowed_domain(address: str) -> bool:
    return domain_of(address) in allowed_domains()


def clean(raw: str) -> str:
    """The normal form of a well-formed, unambiguous address, or a refusal.

    Says nothing about the domain; `is_allowed_domain` is asked separately,
    because an external address is a decision rather than a typo.
    """
    address = normalise(raw)
    if not address.isascii():
        raise ValidationError(NON_ASCII_ADDRESS, code="non_ascii")
    try:
        validate_email(address)
    except ValidationError as error:
        raise ValidationError(INVALID_ADDRESS, code="invalid") from error
    local, _, _ = address.rpartition("@")
    if "+" in local:
        raise ValidationError(SUBADDRESS, code="subaddress")
    return address


def domain_refusal() -> str:
    return DOMAIN_NOT_ALLOWED.format(domains=", ".join(f"@{d}" for d in allowed_domains()))


def looks_like_an_address(raw: str) -> bool:
    """Whether a typed sign-in value is safe to write into the audit.

    Somebody who types their password into the e-mail field by mistake must not
    find it in the security log. An address-shaped value is recorded; anything
    else is recorded as malformed and not at all.
    """
    try:
        clean(raw)
    except ValidationError:
        return False
    return len(raw) <= 320
