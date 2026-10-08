"""Account e-mail: invitations and reset links — and, by default, sending none.

**Nothing leaves this application unless two separate decisions have both been
taken** (docs/adr/0145 §10):

1. `ACCOUNT_EMAIL_DELIVERY_ENABLED` is on — and a system check refuses it in
   any mode except `local_password` (`juristid.E036`), so a shared-gate
   deployment cannot be switched into sending account mail by an environment
   variable alone;
2. `EMAIL_BACKEND` names a real transport. The default is
   `DeliveryDisabledBackend` below, which sends nothing at all, so even a stray
   `send_mail()` elsewhere in the code reaches nobody.

When delivery is off, the caller is told so and the link it was going to send
is invalidated at once (`app.accounts.administration`): an unsent link is a
live credential in nobody's inbox, and there is no reason to keep one.

The link is put into the message body and nowhere else. It is not logged, not
written to the audit (which records *that* a message was or was not sent, to
whom, and why) and never shown to the administrator who caused it — the
administrator must not be able to set somebody else's password.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from django.conf import settings
from django.core.mail import EmailMessage, get_connection
from django.core.mail.backends.base import BaseEmailBackend
from django.template.loader import render_to_string
from django.urls import reverse

from app.accounts.enums import AuthMode, CredentialTokenPurpose
from app.accounts.models import User
from app.audit.enums import SecurityEventType
from app.audit.services import record_security_event

logger = logging.getLogger(__name__)


class DeliveryDisabledBackend(BaseEmailBackend):
    """The default e-mail backend: accepts every message and delivers none.

    Django's own default is SMTP to localhost, which on a container host is a
    message to whatever happens to listen on port 25. This is the safe
    default instead. It logs a count — never a recipient, a subject or a body.
    """

    def send_messages(self, email_messages: Any) -> int:
        count = len(list(email_messages or []))
        if count:
            logger.info("E-mail delivery is disabled; %d message(s) were not sent.", count)
        return 0


@dataclass(frozen=True)
class Delivery:
    delivered: bool
    #: "sent", "disabled", "no_link_base", "no_recipient" or "transport_error".
    reason: str

    @property
    def message_et(self) -> str:
        return {
            "sent": "E-kiri saadeti.",
            "disabled": "E-kirjade saatmine on selles keskkonnas välja lülitatud. "
            "Linki ei saadetud ega kuvata, ja see tühistati.",
            "no_link_base": "E-kirja ei saadetud: rakenduse avalik aadress on seadistamata. "
            "Link tühistati.",
            "no_recipient": "E-kirja ei saadetud: kontol puudub e-posti aadress. Link tühistati.",
            "transport_error": "E-kirja saatmine ebaõnnestus. Link tühistati; proovi hiljem "
            "uuesti.",
        }.get(self.reason, "E-kirja ei saadetud.")


def delivery_enabled() -> bool:
    """Both switches on, in the one mode account mail belongs to."""
    from app.accounts.shared_gate import current_mode

    return bool(getattr(settings, "ACCOUNT_EMAIL_DELIVERY_ENABLED", False)) and (
        current_mode() == AuthMode.LOCAL_PASSWORD
    )


#: Which page each purpose's link opens, and which message template says so.
_LINK_ROUTE = {
    CredentialTokenPurpose.ACTIVATION: "accounts:activate",
    CredentialTokenPurpose.PASSWORD_RESET: "accounts:reset_password",
}
_TEMPLATE = {
    CredentialTokenPurpose.ACTIVATION: "accounts/email/activation",
    CredentialTokenPurpose.PASSWORD_RESET: "accounts/email/password_reset",
}

#: The query parameter a link carries its token in. A query string rather than
#: a path segment because the production access log records the path and
#: never the query (`--access-logformat`, ENG-071); and the page swaps it into
#: the session and redirects at once, so it does not stay in the address bar,
#: the history or a Referer (`app.accounts.local_views`).
LINK_PARAMETER = "kood"


def build_link(purpose: str, token: str) -> str:
    base = (getattr(settings, "ACCOUNT_LINK_BASE_URL", "") or "").rstrip("/")
    return f"{base}{reverse(_LINK_ROUTE[CredentialTokenPurpose(purpose)])}?{LINK_PARAMETER}={token}"


def send_credential_link(
    *, user: User, purpose: str, token: str, actor: User | None, expires_at: Any
) -> Delivery:
    """Send ``user`` their one-time link, if this deployment sends mail at all.

    Always audited, with the outcome and never the link.
    """
    recipient = (user.email or user.upn or "").strip()
    if not delivery_enabled():
        delivery = Delivery(False, "disabled")
    elif not (getattr(settings, "ACCOUNT_LINK_BASE_URL", "") or "").strip():
        delivery = Delivery(False, "no_link_base")
    elif "@" not in recipient:
        delivery = Delivery(False, "no_recipient")
    else:
        delivery = _send(
            user=user, recipient=recipient, purpose=purpose, token=token, expires_at=expires_at
        )

    record_security_event(
        event_type=SecurityEventType.ACCOUNT_EMAIL_DELIVERY,
        actor=actor,
        subject=user,
        succeeded=delivery.delivered,
        detail={"purpose": str(purpose), "outcome": delivery.reason},
    )
    return delivery


def _send(*, user: User, recipient: str, purpose: str, token: str, expires_at: Any) -> Delivery:
    context = {
        "user": user,
        "link": build_link(purpose, token),
        "expires_at": expires_at,
        "application_name": settings.APPLICATION_NAME,
    }
    template = _TEMPLATE[CredentialTokenPurpose(purpose)]
    subject = " ".join(render_to_string(f"{template}_subject.txt", context).split())
    body = render_to_string(f"{template}.txt", context)
    message = EmailMessage(
        subject=subject,
        body=body,
        from_email=getattr(settings, "ACCOUNT_EMAIL_FROM", None) or settings.DEFAULT_FROM_EMAIL,
        to=[recipient],
        connection=get_connection(fail_silently=False),
    )
    try:
        sent = message.send(fail_silently=False)
    except Exception:
        # The exception is not logged with its message: an SMTP error can echo
        # the envelope, and the envelope names a person.
        logger.warning("An account e-mail could not be delivered.")
        return Delivery(False, "transport_error")
    return Delivery(bool(sent), "sent" if sent else "disabled")
