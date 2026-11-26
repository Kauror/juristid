"""Cumulative request and storage budgets of the MCP write gateway (SEC-014, docs/adr/0234).

Per-call limits (body size, nesting, the worker pool, the call timeout) bound
one request. These bound *many*: how often a principal may prepare or ask for a
commit, how many of its proposals may wait at once, how much it may store per
day, and how many identical refusals are written out as events.

**Principals.** Every budget is counted for each stable key a call has — the
user, the grant, the token family and the client (as a SHA-256 prefix of its
id, never the id itself) — and for the deployment as a whole (``global``).
A token refresh keeps the user, the grant, the client *and* the family, so it
starts no new budget; a new authorization keeps the user, the grant and the
client. Concurrency starts none either: every count is one
``INSERT … ON CONFLICT DO UPDATE … RETURNING`` on `McpWriteCounter`, which
PostgreSQL serialises per row, and the outstanding-proposal and storage checks
run under a per-user transaction-level advisory lock.

**Windows** are fixed (10 minutes and one day, UTC-aligned): a refusal says
exactly when the window ends (``retry_after_seconds``).

**What is never budgeted.** Approving — and with it saving — a change happens
on Juristid's own page (`approval_views`), never here, so no budget can stand
between a person's approval and its execution. A commit call that finds the
caller's own already-saved proposal is counted but never refused: the receipt
of a saved change is always available.

**Repeated refusals** (`record_refusal`): the first `events_per_window()`
identical refusals (same principal, event type, code and reason) in a
10-minute window are appended as `McpWriteEvent` rows as before; the next one
is appended once more, marked ``aggregated``, and every further one only
increments its counter row. The append-only trail never loses a kind of
refusal and never grows without bound; the exact count stays in the counter
(Haldus → Süsteem, `manage.py mcp_writes budgets`). Nothing historical is
removed and no trigger is relaxed.
"""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from django.conf import settings
from django.db import connection, transaction
from django.utils import timezone

from app.mcp_writes.refusals import Refusal, refusal

#: The two window lengths, by the label stored in `McpWriteCounter.period`.
PERIODS: dict[str, timedelta] = {"10m": timedelta(minutes=10), "1d": timedelta(days=1)}

#: The defaults; each is a setting of the same name (config/settings.py).
DEFAULTS: dict[str, Any] = {
    # prepare calls, per user / grant / family / client, and for everybody
    "MCP_WRITE_PREPARE_PER_10M": 30,
    "MCP_WRITE_PREPARE_PER_DAY": 300,
    "MCP_WRITE_PREPARE_GLOBAL_PER_10M": 150,
    "MCP_WRITE_PREPARE_GLOBAL_PER_DAY": 1500,
    # commit calls (a receipt fetch or an «awaiting approval» answer)
    "MCP_WRITE_COMMIT_PER_10M": 60,
    "MCP_WRITE_COMMIT_PER_DAY": 600,
    "MCP_WRITE_COMMIT_GLOBAL_PER_10M": 300,
    # refused calls of one user before every further call waits for the window
    "MCP_WRITE_REFUSED_PER_10M": 30,
    # proposals of one user waiting for approval at once
    "MCP_WRITE_MAX_OUTSTANDING": 25,
    # bytes of arguments, canonical arguments, state and preview stored per day
    "MCP_WRITE_STORED_BYTES_PER_DAY": 8 * 1024 * 1024,
    "MCP_WRITE_STORED_BYTES_GLOBAL_PER_DAY": 64 * 1024 * 1024,
    # identical refusals written out as events per 10-minute window
    "MCP_WRITE_EVENTS_PER_WINDOW": 5,
}


def limit(name: str) -> int:
    return max(0, int(getattr(settings, name, DEFAULTS[name])))


def events_per_window() -> int:
    return max(1, limit("MCP_WRITE_EVENTS_PER_WINDOW"))


# -- principals -----------------------------------------------------------------


def client_key(client_id: Any) -> str:
    """A client's key: a SHA-256 prefix. The id itself (a URL) is never stored here."""
    digest = hashlib.sha256(str(client_id or "").encode("utf-8")).hexdigest()
    return f"client:{digest[:32]}"


def principals(*, user_id: Any, grant_id: Any, family_id: Any, client_id: Any) -> list[str]:
    """The stable keys of one caller, most specific last; never a token."""
    keys = []
    if user_id is not None:
        keys.append(f"user:{user_id}")
    if grant_id is not None:
        keys.append(f"grant:{grant_id}")
    if family_id is not None:
        keys.append(f"family:{family_id}")
    if client_id:
        keys.append(client_key(client_id))
    return keys


def principals_of(access: Any, user: Any = None) -> list[str]:
    return principals(
        user_id=getattr(user, "pk", None) or getattr(access, "user_id", None),
        grant_id=getattr(access, "grant_id", None),
        family_id=getattr(access, "family_id", None),
        client_id=getattr(access, "client_id", ""),
    )


# -- the counter ------------------------------------------------------------------


def window_start(period: str, moment: datetime) -> datetime:
    length = PERIODS[period]
    epoch = datetime(2000, 1, 1, tzinfo=moment.tzinfo)
    steps = (moment - epoch) // length
    return epoch + steps * length


def retry_after(period: str, moment: datetime) -> int:
    end = window_start(period, moment) + PERIODS[period]
    return max(1, int((end - moment).total_seconds() + 0.999))


_UPSERT = (
    "INSERT INTO mcp_writes_mcpwritecounter "
    "(id, principal, bucket, period, period_start, count, first_at, last_at) "
    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s) "
    "ON CONFLICT (principal, bucket, period, period_start) DO UPDATE SET "
    "count = mcp_writes_mcpwritecounter.count + EXCLUDED.count, last_at = EXCLUDED.last_at "
    "RETURNING count"
)


def add(principal: str, bucket: str, period: str, amount: int = 1, *, now: datetime) -> int:
    """Add ``amount`` to one window's counter and answer its new value, atomically."""
    with connection.cursor() as cursor:
        cursor.execute(
            _UPSERT,
            [
                uuid.uuid4(),
                principal[:100],
                bucket[:120],
                period,
                window_start(period, now),
                int(amount),
                now,
                now,
            ],
        )
        (value,) = cursor.fetchone()
    return int(value)


def current(principal: str, bucket: str, period: str, *, now: datetime) -> int:
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT count FROM mcp_writes_mcpwritecounter WHERE principal = %s AND bucket = %s "
            "AND period = %s AND period_start = %s",
            [principal[:100], bucket[:120], period, window_start(period, now)],
        )
        row = cursor.fetchone()
    return int(row[0]) if row else 0


# -- refusals ---------------------------------------------------------------------


@dataclass(frozen=True)
class Exceeded:
    """Which budget ran out: the principal's *kind* (never its id), the bucket, the window."""

    kind: str
    bucket: str
    period: str
    seconds: int


def rate_limited(exceeded: Exceeded) -> Refusal:
    minutes = max(1, round(exceeded.seconds / 60))
    return refusal(
        "rate_limited",
        (
            f"Too many {exceeded.bucket} requests through the AI connection "
            f"({exceeded.kind}, per {exceeded.period}). Nothing was saved or changed. "
            f"Try again in about {minutes} minute{'s' if minutes != 1 else ''}."
        ),
        retry_after_seconds=exceeded.seconds,
        budget=exceeded.bucket,
        scope=exceeded.kind,
    )


def _kind(principal: str) -> str:
    return principal.split(":", 1)[0]


def _charge(
    bucket: str, keys: Iterable[str], limits: dict[str, tuple[int, int]], now: datetime
) -> Exceeded | None:
    """Count one call against every key's windows; the first window over its limit, or None.

    Every window is counted even when an earlier one is already over: the
    counters say how much was *asked*, which is what an operator needs."""
    exceeded: Exceeded | None = None
    for key in keys:
        per_10m, per_day = limits["global" if key == "global" else "principal"]
        for period, allowed in (("10m", per_10m), ("1d", per_day)):
            if allowed <= 0:
                continue
            value = add(key, bucket, period, now=now)
            if value > allowed and exceeded is None:
                exceeded = Exceeded(_kind(key), bucket, period, retry_after(period, now))
    return exceeded


def refused_too_often(keys: list[str], now: datetime) -> Exceeded | None:
    """A user whose refused calls ran past `MCP_WRITE_REFUSED_PER_10M` waits for the window."""
    allowed = limit("MCP_WRITE_REFUSED_PER_10M")
    if allowed <= 0:
        return None
    for key in keys:
        if key.startswith("user:") and current(key, "refused", "10m", now=now) >= allowed:
            return Exceeded("user", "refused", "10m", retry_after("10m", now))
    return None


def admit(action: str, *, access: Any, user: Any, now: datetime | None = None) -> Refusal | None:
    """Charge one ``prepare`` or ``commit`` call; a ``rate_limited`` refusal when over budget.

    Committed on its own (a short transaction of its own), before the work:
    a call is counted whether or not the work then succeeds."""
    now = now or timezone.now()
    keys = principals_of(access, user)
    if action == "prepare":
        limits = {
            "principal": (limit("MCP_WRITE_PREPARE_PER_10M"), limit("MCP_WRITE_PREPARE_PER_DAY")),
            "global": (
                limit("MCP_WRITE_PREPARE_GLOBAL_PER_10M"),
                limit("MCP_WRITE_PREPARE_GLOBAL_PER_DAY"),
            ),
        }
    else:
        limits = {
            "principal": (limit("MCP_WRITE_COMMIT_PER_10M"), limit("MCP_WRITE_COMMIT_PER_DAY")),
            "global": (limit("MCP_WRITE_COMMIT_GLOBAL_PER_10M"), 0),
        }
    with transaction.atomic():
        blocked = refused_too_often(keys, now)
        exceeded = _charge(action, [*keys, "global"], limits, now)
    found = blocked or exceeded
    return rate_limited(found) if found is not None else None


def count_only(action: str, *, access: Any, user: Any, now: datetime | None = None) -> None:
    """Count a call that is never refused (the receipt of a saved change)."""
    now = now or timezone.now()
    with transaction.atomic():
        for key in [*principals_of(access, user), "global"]:
            add(key, action, "10m", now=now)
            add(key, action, "1d", now=now)


# -- what a prepare may keep --------------------------------------------------------


def lock(name: str) -> None:
    """A transaction-level advisory lock on ``name`` (no table privilege needed)."""
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", [name])


def lock_user(user_id: Any) -> None:
    """Serialise one user's outstanding-proposal and storage checks (transaction-level)."""
    lock(f"mcp_writes.user:{user_id}")


def storage_refusal(
    *, user_id: Any, outstanding: int, stored_bytes: int, now: datetime
) -> Refusal | None:
    """Inside the prepare's transaction, under `lock_user`: may one more proposal be kept?

    Answers None and counts the bytes when it may."""
    allowed = limit("MCP_WRITE_MAX_OUTSTANDING")
    if allowed and outstanding >= allowed:
        return refusal(
            "rate_limited",
            (
                f"{outstanding} prepared changes are already waiting for approval. Ask the user "
                "to approve or decline them in Juristid (or let them expire) before preparing "
                "more. Nothing was saved."
            ),
            retry_after_seconds=int(settings.MCP_WRITE_PROPOSAL_SECONDS),
            budget="outstanding",
            scope="user",
        )
    per_user = limit("MCP_WRITE_STORED_BYTES_PER_DAY")
    overall = limit("MCP_WRITE_STORED_BYTES_GLOBAL_PER_DAY")
    key = f"user:{user_id}"
    for principal, allowed_bytes in ((key, per_user), ("global", overall)):
        if allowed_bytes and current(principal, "stored_bytes", "1d", now=now) + stored_bytes > (
            allowed_bytes
        ):
            return rate_limited(
                Exceeded(_kind(principal), "stored_bytes", "1d", retry_after("1d", now))
            )
    add(key, "stored_bytes", "1d", stored_bytes, now=now)
    add("global", "stored_bytes", "1d", stored_bytes, now=now)
    return None


# -- repeated refusals --------------------------------------------------------------


def refusal_bucket(event_type: str, detail: dict[str, Any]) -> str:
    code = str(detail.get("code") or "")
    reason = str(detail.get("reason") or "")
    return f"event:{event_type}:{code}:{reason}"[:120]


def note_refusal(
    *,
    principal: str,
    event_type: str,
    detail: dict[str, Any],
    counts_as_refused: bool = True,
    now: datetime | None = None,
) -> tuple[int, bool]:
    """Count one refused call; answer (occurrence in this window, whether to write its event).

    With ``counts_as_refused`` also counts towards the principal's ``refused``
    budget (`refused_too_often`)."""
    now = now or timezone.now()
    occurrence = add(principal, refusal_bucket(event_type, detail), "10m", now=now)
    if counts_as_refused:
        add(principal, "refused", "10m", now=now)
    return occurrence, occurrence <= events_per_window() + 1


# -- diagnostics ----------------------------------------------------------------------


def snapshot(*, now: datetime | None = None, rows: int = 50) -> dict[str, Any]:
    """What Haldus and `mcp_writes budgets` show: limits and the busiest current windows.

    Principals are shown by kind and a short key (user and grant ids are
    Juristid's own; a client is already a hash prefix); never a token."""
    from app.mcp_writes.models import McpWriteCounter, McpWriteProposal, ProposalStatus

    now = now or timezone.now()
    since = now - PERIODS["1d"]
    counters = list(
        McpWriteCounter.objects.filter(period_start__gte=window_start("1d", since))
        .order_by("-count")
        .values("principal", "bucket", "period", "period_start", "count", "last_at")[:rows]
    )
    aggregated = [
        row
        for row in counters
        if str(row["bucket"]).startswith("event:") and row["count"] > events_per_window() + 1
    ]
    outstanding = McpWriteProposal.objects.filter(
        status=ProposalStatus.PENDING, expires_at__gt=now
    ).count()
    return {
        "limits": {name: limit(name) for name in DEFAULTS},
        "counters": counters,
        "aggregated": aggregated,
        "outstanding": outstanding,
    }


def summary(*, now: datetime | None = None) -> dict[str, Any] | None:
    """Counts only, for Haldus → Süsteemi seis: no principal, no client, no record.

    None when MCP writes are off (the section is then not shown)."""
    from django.db.models import Sum

    from app.mcp_writes.access import writes_enabled
    from app.mcp_writes.models import McpWriteCounter, McpWriteProposal, ProposalStatus

    if not writes_enabled():
        return None
    now = now or timezone.now()
    day = window_start("1d", now)
    since = now - PERIODS["1d"]

    def total(**filters: Any) -> int:
        value = McpWriteCounter.objects.filter(**filters).aggregate(n=Sum("count"))["n"]
        return int(value or 0)

    written = events_per_window() + 1
    suppressed = 0
    for count in McpWriteCounter.objects.filter(
        bucket__startswith="event:", period="10m", period_start__gte=since, count__gt=written
    ).values_list("count", flat=True):
        suppressed += int(count) - written
    return {
        "prepared_today": total(
            principal="global", bucket="prepare", period="1d", period_start=day
        ),
        "commits_today": total(principal="global", bucket="commit", period="1d", period_start=day),
        "refused_last_day": total(
            principal__startswith="user:", bucket="refused", period="10m", period_start__gte=since
        ),
        "rate_limited_last_day": total(
            bucket__contains=":rate_limited:", period="10m", period_start__gte=since
        ),
        "suppressed_last_day": suppressed,
        "outstanding": McpWriteProposal.objects.filter(
            status=ProposalStatus.PENDING, expires_at__gt=now
        ).count(),
        "max_outstanding": limit("MCP_WRITE_MAX_OUTSTANDING"),
        "prepare_per_10m": limit("MCP_WRITE_PREPARE_PER_10M"),
        "prepare_per_day": limit("MCP_WRITE_PREPARE_PER_DAY"),
    }
