"""The client's address: one rule for every audit row and every throttle.

Behind the Cloudflare tunnel every request reaches gunicorn from the
`cloudflared` connector, so ``REMOTE_ADDR`` is the same internal address for
everybody. Cloudflare puts the address it saw in ``CF-Connecting-IP``. Audit
rows used to record ``REMOTE_ADDR`` and the throttles read the header, so the
trail named the tunnel while the lockout named the client (JOPS-008).

The header is believed only where the deployment says a proxy stands in front
of it (``TRUST_CF_CONNECTING_IP``, which follows ``DJANGO_BEHIND_TLS_PROXY``).
That is the same trust ``SECURE_PROXY_SSL_HEADER`` already rests on: the tunnel
is the only way in, so the header was set by Cloudflare and not by the client.
Anywhere else a caller could write the header itself, and ``REMOTE_ADDR`` is the
only address that means anything.

Either value is used only if it parses as an IP address. A header that does not
(a list, a hostname, garbage) is ignored rather than trusted in part, and the
answer falls back to ``REMOTE_ADDR``. ``X-Forwarded-For`` is never read: the
tunnel does not need it and a client can append to it.

The answer is an *address*, for the audit trail and for counting attempts. It is
never identity and never an authorization input (docs/adr/0016).
"""

from __future__ import annotations

import ipaddress

from django.conf import settings
from django.http import HttpRequest

FORWARDED_HEADER = "HTTP_CF_CONNECTING_IP"


def _valid(raw: object) -> str | None:
    if not isinstance(raw, str):
        return None
    try:
        return str(ipaddress.ip_address(raw.strip()))
    except ValueError:
        return None


def client_ip(request: HttpRequest) -> str | None:
    """The client's IP address, or None when there is no valid one."""
    if getattr(settings, "TRUST_CF_CONNECTING_IP", False):
        forwarded = _valid(request.META.get(FORWARDED_HEADER))
        if forwarded is not None:
            return forwarded
    return _valid(request.META.get("REMOTE_ADDR"))
