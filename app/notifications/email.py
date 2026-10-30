"""E-mail delivery of notifications — deliberately not built (docs/adr/0198 §5).

A person may say they would like e-mail (`NotificationSettings.email_requested`),
and that wish is stored. Nothing is sent, and nothing here calls a mail backend:
under the shared gate an account is a persona somebody *chose*, not a verified
person (ADR 0016, 0180), so an address on it is not an address anybody proved is
theirs. Delivery needs an individual sign-in mode **and** an explicit setting,
and even then this function only answers whether delivery would be allowed — the
provider is a later, separate decision.
"""

from __future__ import annotations

from typing import Any

from django.conf import settings

from app.accounts.enums import AuthMode

#: The modes in which an account is a verified individual.
INDIVIDUAL_MODES: frozenset[str] = frozenset(
    {AuthMode.CLOUDFLARE_ACCESS.value, AuthMode.LOCAL_PASSWORD.value}
)

#: Said by the settings page beside the disabled control.
EMAIL_NOT_YET = "E-posti teavitused lisanduvad isikupõhise sisselogimisega."


def email_delivery_allowed() -> bool:
    mode = str(getattr(settings, "AUTH_MODE", "") or "").strip().lower()
    return mode in INDIVIDUAL_MODES and bool(getattr(settings, "NOTIFICATION_EMAIL_ENABLED", False))


def deliver_email(user: Any, item: Any) -> bool:
    """Refuse. ``False`` always while delivery is not allowed — and, until a
    provider is chosen, also when it is: no mail backend is called from here."""
    if not email_delivery_allowed():
        return False
    # No provider is wired (docs/adr/0198 §5). Deliberately no send_mail here.
    return False
