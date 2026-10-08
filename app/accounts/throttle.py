"""Throttling for the local sign-in, shared by every worker (docs/adr/0145 §9).

The shape is the shared gate's, which has already been through two concurrency
findings (SEC-01, ENG-070), and the lessons are kept rather than relearned:

* **One step, under the rows' own locks.** The lockout check, the verification
  and the count happen inside one transaction that holds every counter the
  attempt is charged to. Attempts fired in parallel take turns, so the attempt
  after the one that arms a lockout finds it armed and is refused without its
  password ever being checked.
* **Per something, never global.** A global counter is a denial-of-service
  primitive. The narrowest counter — this account from this network — is the
  tight one; the account alone and the network alone carry looser limits that
  only a distributed attack, or one network trying many accounts, reaches.
* **Locks taken in one order.** Several rows per attempt, always sorted by
  (scope, key), so two attempts that share a counter can never each hold the
  row the other is waiting for.
* **Keys are HMACs.** The table names no address and no network, and an
  address that belongs to nobody is counted exactly like one that belongs to
  somebody — a throttle that behaved differently would tell a stranger which
  addresses are colleagues.
"""

from __future__ import annotations

import hashlib
import hmac
import ipaddress
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta

from django.conf import settings
from django.db import transaction
from django.db.models import Q
from django.http import HttpRequest
from django.utils import timezone

from app.accounts.enums import ThrottleScope
from app.accounts.models import AuthenticationThrottle


@dataclass(frozen=True)
class Policy:
    """How many failures a counter tolerates, and what they cost."""

    max_failures: int
    base_seconds: int
    ceiling_seconds: int


def policy_for(scope: str) -> Policy:
    """The configured limits for one counter, from settings.

    Stated in settings rather than here so a deployment can tune them, and so
    the tests can state the numbers they test instead of inheriting them.
    """
    table: dict[str, tuple[str, int, int, int]] = {
        ThrottleScope.SIGN_IN_PAIR: ("LOCAL_AUTH_THROTTLE_PAIR", 5, 60, 900),
        ThrottleScope.SIGN_IN_ACCOUNT: ("LOCAL_AUTH_THROTTLE_ACCOUNT", 20, 300, 3600),
        ThrottleScope.SIGN_IN_NETWORK: ("LOCAL_AUTH_THROTTLE_NETWORK", 30, 300, 3600),
        ThrottleScope.SECOND_FACTOR: ("LOCAL_AUTH_THROTTLE_SECOND_FACTOR", 5, 60, 900),
        ThrottleScope.REAUTHENTICATION: ("LOCAL_AUTH_THROTTLE_REAUTH", 5, 60, 900),
        ThrottleScope.CREDENTIAL_LINK: ("LOCAL_AUTH_THROTTLE_LINK", 20, 300, 3600),
        ThrottleScope.RESET_REQUEST_ACCOUNT: ("LOCAL_AUTH_THROTTLE_RESET_ACCOUNT", 3, 900, 3600),
        ThrottleScope.RESET_REQUEST_NETWORK: ("LOCAL_AUTH_THROTTLE_RESET_NETWORK", 10, 900, 3600),
        ThrottleScope.PASSWORD_CHANGE: ("LOCAL_AUTH_THROTTLE_PASSWORD_CHANGE", 5, 60, 900),
    }
    prefix, failures, base, ceiling = table[ThrottleScope(scope)]
    configured = getattr(settings, prefix, None) or {}
    return Policy(
        max_failures=int(configured.get("max_failures", failures)),
        base_seconds=int(configured.get("base_seconds", base)),
        ceiling_seconds=int(configured.get("ceiling_seconds", ceiling)),
    )


#: How long a failure keeps counting towards the next lockout, and how long a
#: quiet period resets the escalation. A person who mistypes once a week is
#: never one typo away from an hour's wait.
FAILURE_WINDOW = timedelta(hours=1)
ESCALATION_RESET = timedelta(hours=24)


def digest(*parts: str) -> str:
    """An HMAC of what is being counted, so the table holds none of it."""
    message = "\x1f".join(part.strip().casefold() for part in parts)
    return hmac.new(
        settings.SECRET_KEY.encode("utf-8"), message.encode("utf-8"), hashlib.sha256
    ).hexdigest()


def network_of(request: HttpRequest) -> str:
    """The client's network address, for counting and nothing else.

    Behind the Cloudflare tunnel every request arrives from the connector, and
    `CF-Connecting-IP` is the address Cloudflare saw. A client that reaches the
    application directly could set the header to anything — which is why this
    is a rate-limit key, never an identity (docs/adr/0016).
    """
    forwarded = request.META.get("HTTP_CF_CONNECTING_IP") or request.META.get(
        "HTTP_X_FORWARDED_FOR", ""
    )
    raw = (forwarded.split(",")[0].strip() if forwarded else "") or request.META.get(
        "REMOTE_ADDR", ""
    )
    return _counting_unit(raw) or "unknown"


def _counting_unit(raw: str) -> str:
    """The unit one client is counted as: an IPv4 address, or an IPv6 /64.

    An IPv6 subscriber is handed a whole /64 and can rotate through it for
    free, so counting single IPv6 addresses would give one attacker billions of
    fresh counters. The /64 is what one household or one office holds.
    """
    try:
        address = ipaddress.ip_address(raw)
    except ValueError:
        return raw
    if address.version == 6:
        return str(ipaddress.ip_network(f"{address}/64", strict=False))
    return str(address)


def trusted_key(email: str, request: HttpRequest) -> str:
    return digest("trusted", email, network_of(request))


def is_trusted_source(email: str, request: HttpRequest) -> bool:
    from app.accounts.models import TrustedSignInSource

    return TrustedSignInSource.objects.filter(key_digest=trusted_key(email, request)).exists()


def remember_trusted_source(email: str, request: HttpRequest) -> None:
    from app.accounts.models import TrustedSignInSource

    TrustedSignInSource.objects.update_or_create(
        key_digest=trusted_key(email, request), defaults={"last_success_at": timezone.now()}
    )


@dataclass(frozen=True)
class Counter:
    """One counter an attempt is charged to."""

    scope: str
    key: str


def sign_in_counters(request: HttpRequest, email: str) -> list[Counter]:
    """The three counters a password attempt is charged to."""
    network = network_of(request)
    return [
        Counter(ThrottleScope.SIGN_IN_PAIR, digest("pair", email, network)),
        Counter(ThrottleScope.SIGN_IN_ACCOUNT, digest("account", email)),
        Counter(ThrottleScope.SIGN_IN_NETWORK, digest("network", network)),
    ]


def user_counter(scope: str, user_id: object) -> Counter:
    return Counter(scope, digest(str(scope), str(user_id)))


def network_counter(scope: str, request: HttpRequest) -> Counter:
    return Counter(scope, digest(str(scope), network_of(request)))


class Held:
    """The counters one attempt holds locked, and what to do with them."""

    def __init__(self, rows: list[AuthenticationThrottle]) -> None:
        self._rows = rows

    def wait(self, *, ignoring: frozenset[str] = frozenset()) -> int:
        """Seconds until every counter would let this attempt through; 0 if now.

        ``ignoring`` names scopes whose lockout this attempt is exempt from — and
        only their lockout: a failure is still counted against them.
        """
        now = timezone.now()
        return max(
            (row.seconds_remaining(now=now) for row in self._rows if row.scope not in ignoring),
            default=0,
        )

    def fail(self) -> int:
        """Count one failure against every held counter; return the longest wait."""
        now = timezone.now()
        return max((_register_failure(row, now=now) for row in self._rows), default=0)

    def succeed(self) -> None:
        """A correct attempt clears the counters it was charged to.

        Only those. A correct password from this network for this account does
        not forgive the network's failures against *other* accounts: the
        network counter is kept, the pair and the account are cleared.
        """
        clearable = {ThrottleScope.SIGN_IN_PAIR, ThrottleScope.SIGN_IN_ACCOUNT}
        clearable |= {
            ThrottleScope.SECOND_FACTOR,
            ThrottleScope.REAUTHENTICATION,
            ThrottleScope.PASSWORD_CHANGE,
        }
        ids = [row.pk for row in self._rows if row.scope in clearable]
        if ids:
            AuthenticationThrottle.objects.filter(pk__in=ids).delete()


@contextmanager
def holding(counters: list[Counter]) -> Iterator[Held]:
    """Take these counters' rows for the rest of an atomic block.

    Must be used inside `transaction.atomic()` — the locks are what make the
    check-verify-count step one step, and they last exactly as long as the
    transaction does. Rows are created first, in their own savepoints, and
    then locked in a fixed order.
    """
    if not transaction.get_connection().in_atomic_block:  # pragma: no cover - misuse
        raise RuntimeError("throttle counters must be held inside a transaction")
    ordered = sorted({(str(c.scope), c.key) for c in counters})
    for scope, key in ordered:
        AuthenticationThrottle.objects.get_or_create(scope=scope, key_digest=key)
    rows = []
    for scope, key in ordered:
        row = (
            AuthenticationThrottle.objects.select_for_update()
            .filter(scope=scope, key_digest=key)
            .first()
        )
        if row is None:  # deleted by a concurrent success between the two reads
            row, _ = AuthenticationThrottle.objects.get_or_create(scope=scope, key_digest=key)
            row = AuthenticationThrottle.objects.select_for_update().get(pk=row.pk)
        rows.append(row)
    yield Held(rows)


def _register_failure(row: AuthenticationThrottle, *, now: datetime) -> int:
    """One failure, with decay; return the wait it earns. Row must be locked."""
    policy = policy_for(row.scope)
    if row.last_failure_at is not None:
        quiet = now - row.last_failure_at
        if quiet > ESCALATION_RESET:
            row.lockout_cycles = 0
            row.failures = 0
        elif quiet > FAILURE_WINDOW:
            row.failures = 0
    row.failures += 1
    row.last_failure_at = now
    seconds = 0
    if row.failures >= policy.max_failures:
        seconds = min(policy.base_seconds * (2**row.lockout_cycles), policy.ceiling_seconds)
        row.locked_until = now + timedelta(seconds=seconds)
        row.lockout_cycles += 1
        row.failures = 0
    row.save(
        update_fields=[
            "failures",
            "lockout_cycles",
            "locked_until",
            "last_failure_at",
            "updated_at",
        ]
    )
    return seconds


def charge(counters: list[Counter]) -> int:
    """Count one failure against these counters, in a transaction of its own."""
    with transaction.atomic(), holding(counters) as held:
        if held.wait():
            return held.wait()
        return held.fail()


def is_locked(counters: list[Counter]) -> int:
    """Seconds remaining on the longest lockout among these counters, unlocked read."""
    if not counters:
        return 0
    now = timezone.now()
    wanted = Q()
    for counter in counters:
        wanted |= Q(scope=str(counter.scope), key_digest=counter.key)
    rows = AuthenticationThrottle.objects.filter(wanted)
    return max((row.seconds_remaining(now=now) for row in rows), default=0)
