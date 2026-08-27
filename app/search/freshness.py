"""Durable search-freshness debt, and the consumer that pays it off.

Search has two classes of invalidation and they need two different answers.

**Bounded fanout** — saving a Matter, an Entry, a Submission, a `Kaasamine`,
publishing a derivative — invalidates a number of search rows bounded by the
write itself. Those are refreshed synchronously, inside the same transaction as
the canonical write, so a committed record and a findable record are the same
event. Nothing here participates in that; see `app/search/signals.py`.

**High fanout** — renaming an Organisation, editing its aliases, renaming a Tag
or a PolicyArea, changing a person's display name — invalidates the indexed text
of every record that names them, which at this corpus is thousands of rows from
one small form submission. Fanning that out inside the request is the failure the
current design deliberately avoided, and it was right to. What it did instead
was nothing: the corpus went stale, `check_search_integrity` would say so if
somebody ran it, and a human was expected to notice and rebuild.

This module is the missing half. The mutation records a durable obligation in
its own transaction; a consumer converges it by running the proven atomic
rebuild; the obligation is cleared only after that rebuild has committed.

Three properties are worth being explicit about, because each was a way this
could have been written wrong.

*The obligation is a row, not a callback.* `transaction.on_commit` would be
enough for a process that never dies. A deploy between the commit and the
callback loses it with nothing anywhere recording that search is now stale —
which is exactly the silent failure the projection cannot afford.

*Claiming is by primary key, and the claim is read before the rebuild starts.*
A mark that arrives while a rebuild is running is not in the claimed set, so the
clear cannot delete it, so the next pass rebuilds again. That costs one
redundant rebuild and it is the direction to be wrong in: the alternative loses a
change that the rebuild may or may not have seen, depending on transaction
snapshots nobody can reason about from the outside.

*Clearing happens after the rebuild commits, never before.* A rebuild that
raises leaves the previous complete index in place — that is `rebuild_all`'s own
guarantee — and leaves the debt outstanding, so the failure is both recoverable
and visible.

Two more properties belong to the failure path rather than the happy one, and
each was a way this leaked or stalled.

*A recorded failure says why and never what.* `describe_failure` is the only
writer of `SearchRebuildDebt.last_error`, and it reads SQLSTATE and schema names
rather than the exception's message — because PostgreSQL composes that message
out of the row that failed, and the row that fails here is a `SearchDocument`.

*The consumer heals its own connection.* `worker_pass` is `consume_once` with
the connection hygiene a long-lived loop has to do for itself, because nothing
sends it `request_started`. Without it a database restart wedged the worker for
good, in a way `restart: unless-stopped` cannot see.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from django.db import connections
from django.db.models import Count, F, Min, QuerySet, Sum
from django.db.models.functions import Coalesce
from django.utils import timezone

from app.search.indexing import RebuildResult, rebuild_all
from app.search.models import SearchRebuildDebt, SearchRebuildReason

logger = logging.getLogger(__name__)

#: A backstop, not the mechanism. `describe_failure` composes a string that is
#: short by construction; this only guarantees that no future addition to it can
#: grow the column without somebody noticing the truncation.
MAX_RECORDED_ERROR_CHARACTERS = 500

#: The only parts of a database error this module will persist, and every one of
#: them is a name that appears in the schema rather than a value that appears in
#: a row.
#:
#: This list is the whole confidentiality argument, so it is an allow-list. The
#: obvious field to reach for — `diag.message_primary`, or simply `str(error)` —
#: is the one that cannot be used: PostgreSQL composes those out of the data that
#: failed. A not-null violation's DETAIL is `Failing row contains (…)` with the
#: projected `title` and `body_text` in it, and a unique violation's is
#: `Key (column)=(value) already exists`. Both of those went into
#: `SearchRebuildDebt.last_error` verbatim, and `check_search_freshness` prints
#: that column to an operator's terminal and a container log.
#:
#: Indexed text is the most confidential material this system holds — a
#: RESTRICTED `Kaasamine`'s note is in it — and the debt table is the one place
#: in the search subsystem that was never meant to hold any of it (docs/adr/0041).
SAFE_DIAGNOSTIC_FIELDS = ("table_name", "column_name", "constraint_name")

#: How far to walk `__cause__` looking for the database error underneath a
#: Django wrapper. Django wraps at one level; a couple more costs nothing and
#: stops a re-raise from hiding the SQLSTATE.
_CAUSE_DEPTH = 4


def describe_failure(error: BaseException) -> str:
    """Everything safe to keep about a failed rebuild, and nothing else.

    Called on the write path of `consume_once`, and the *only* thing that ever
    writes `SearchRebuildDebt.last_error`. It never reads the exception's
    message — see `SAFE_DIAGNOSTIC_FIELDS` for why that rule has to be absolute
    rather than a filter over known-bad strings.

    What comes back is still worth reading. `IntegrityError [23502]
    search_searchdocument.indexed_at` tells an operator which constraint the
    rebuild broke and therefore which change to look at, which is what the
    column was for; the traceback with the message attached is in the worker's
    log, where the audience is a developer rather than a health probe.
    """
    parts = [type(error).__name__]

    diagnostic = None
    candidate: BaseException | None = error
    for _ in range(_CAUSE_DEPTH):
        if candidate is None:
            break
        possible = getattr(candidate, "diag", None)
        if possible is not None and getattr(possible, "sqlstate", None):
            diagnostic = possible
            break
        candidate = candidate.__cause__

    if diagnostic is not None:
        parts.append(f"[{diagnostic.sqlstate}]")
        # `table.column` when both are known, because that reads as one thing;
        # otherwise whichever names PostgreSQL supplied, in a fixed order so two
        # runs of the same failure record the same string.
        table = getattr(diagnostic, "table_name", None)
        column = getattr(diagnostic, "column_name", None)
        if table and column:
            parts.append(f"{table}.{column}")
        else:
            parts.extend(
                str(getattr(diagnostic, field))
                for field in SAFE_DIAGNOSTIC_FIELDS
                if getattr(diagnostic, field, None)
            )

    return " ".join(parts)[:MAX_RECORDED_ERROR_CHARACTERS]


def mark_rebuild_owed(reason: str) -> SearchRebuildDebt:
    """Record, inside the caller's transaction, that the corpus needs rebuilding.

    One INSERT and no reads, so a signal handler can call it on the write path
    without adding a query that scales with anything. Deliberately not
    idempotent: see the class docstring for why deduplicating here would open a
    lost-update window exactly when a mark matters most.

    Called from a handler running inside the business transaction, so a
    mutation that rolls back takes its debt with it and one that commits cannot
    commit without it.
    """
    return SearchRebuildDebt.objects.create(reason=reason)


def outstanding() -> QuerySet[SearchRebuildDebt]:
    return SearchRebuildDebt.objects.all()


@dataclass(frozen=True)
class FreshnessStatus:
    """What an operator or a container probe needs, in one query's worth of rows."""

    owed: int
    oldest_marked_at: datetime | None
    reasons: dict[str, int]
    failed_attempts: int
    last_error: str

    @property
    def is_clear(self) -> bool:
        return self.owed == 0

    def seconds_owed(self, *, now: datetime | None = None) -> float:
        if self.oldest_marked_at is None:
            return 0.0
        return ((now or timezone.now()) - self.oldest_marked_at).total_seconds()


def status() -> FreshnessStatus:
    """Read-only. Never consumes, never repairs, never rebuilds.

    Aggregated in the database rather than by loading the rows. The debt table
    is normally empty and never large, but "normally" is doing work there: a
    bulk writer that touched every alias in the corpus writes a row per alias,
    and this is the query a container healthcheck runs every sixty seconds. Two
    small aggregates and one indexed lookup cost the same whether the table
    holds one row or fifty thousand.
    """
    rows = outstanding()
    totals = rows.aggregate(
        owed=Count("id"),
        oldest=Min("created_at"),
        failed=Coalesce(Sum("attempts"), 0),
    )
    if not totals["owed"]:
        return FreshnessStatus(
            owed=0, oldest_marked_at=None, reasons={}, failed_attempts=0, last_error=""
        )

    reasons = dict(
        rows.values_list("reason").annotate(total=Count("id")).values_list("reason", "total")
    )
    last_error = ""
    if totals["failed"]:
        latest = (
            rows.exclude(attempts=0)
            .exclude(last_error="")
            .order_by("-last_attempt_at")
            .values_list("last_error", flat=True)
            .first()
        )
        last_error = latest or ""

    return FreshnessStatus(
        owed=totals["owed"],
        oldest_marked_at=totals["oldest"],
        reasons=reasons,
        failed_attempts=totals["failed"],
        last_error=last_error,
    )


@dataclass(frozen=True)
class ConsumeResult:
    rebuilt: bool
    cleared: int
    result: RebuildResult | None = None


def consume_once() -> ConsumeResult:
    """Claim every outstanding obligation, rebuild once, clear what was claimed.

    This is the coalescing step, and it is the reason the debt table needs no
    deduplication: a hundred marks become one `rebuild_all`, because the unit of
    repair is the whole corpus rather than a row.

    Not wrapped in a transaction of its own, on purpose. `rebuild_all` owns one,
    and the clear has to happen *after* that transaction commits — inside it,
    a rebuild that failed at the last statement would roll back the delete too,
    which sounds equivalent and is not: it would also roll back the recorded
    attempt, so a rebuild that fails every time would look, from the debt table,
    exactly like one nobody has tried.

    Re-raises what the rebuild raised, after recording the attempt. The caller
    decides whether that is fatal; for the worker it is one bad pass.
    """
    claimed = list(outstanding().order_by("created_at").values_list("pk", flat=True))
    if not claimed:
        return ConsumeResult(rebuilt=False, cleared=0)

    try:
        result = rebuild_all()
    except Exception as error:
        _record_attempt(claimed, error)
        raise

    # Only the rows claimed before the rebuild began. Anything marked while it
    # ran survives and is paid off by the next pass.
    cleared, _ = SearchRebuildDebt.objects.filter(pk__in=claimed).delete()
    return ConsumeResult(rebuilt=True, cleared=cleared, result=result)


def _record_attempt(claimed: list[Any], error: BaseException) -> None:
    """Note the failure on the claimed rows, and never become the failure.

    This runs in an `except` block, so an exception raised *here* replaces the
    one being handled — and the case where that happens is the one where the
    diagnosis matters most. A rebuild interrupted by the database going away
    raises `OperationalError`, this UPDATE then raises `OperationalError` too,
    and the worker logs the failure of its own error handler instead of the
    failure it was handling.

    Nothing is lost by giving up on the note. The debt rows are still there —
    nothing has been cleared — so the obligation, which is the part that must
    survive, survives, and the next pass tries again on a healthy connection.
    """
    try:
        SearchRebuildDebt.objects.filter(pk__in=claimed).update(
            attempts=F("attempts") + 1,
            last_attempt_at=timezone.now(),
            last_error=describe_failure(error),
        )
    except Exception:  # pragma: no cover - only reachable with a dead connection
        logger.exception("Ebaõnnestunud taastamiskatset ei saanud võlareale kirja panna.")


def worker_pass() -> ConsumeResult:
    """One iteration of the refresh worker: reconnect if need be, then consume.

    The connection hygiene is the whole reason this exists as a function rather
    than two lines in the loop. A request/response cycle gets it for free —
    Django sends `request_started`, `close_old_connections` runs, and a
    connection left broken by the previous request is dropped before the next
    one needs it. A management command that loops forever gets none of that, so
    it has to do it itself, and until it did, a PostgreSQL restart wedged the
    worker permanently: every pass raised `OperationalError` against the same
    dead socket, the debt kept growing, and `restart: unless-stopped` never
    fired because the process had not exited. Recovering needed a human to
    restart the container — which is precisely the "a human has to notice"
    failure SEARCH-001 exists to remove.

    `close_if_unusable_or_obsolete` rather than an unconditional
    `connection.close()`: it drops a connection only when it is broken or past
    `CONN_MAX_AGE`, so a worker polling every ten seconds is not opening a new
    PostgreSQL connection every ten seconds.

    It heals *before* the attempt, not after, so nothing depends on the failing
    pass having managed to run its own cleanup — the pass that lost the
    connection may have died anywhere.

    The `in_atomic_block` guard is what `django.db.close_old_connections` can
    do without and this cannot. That helper runs at request boundaries, where
    by construction no transaction is open; closing inside one instead marks
    the connection `closed_in_transaction` and takes the caller's transaction
    down with it. Between passes there is nothing open, so the guard costs
    nothing — and it means a caller that does hold a transaction gets a plain
    `consume_once` rather than a broken one.
    """
    for existing in connections.all(initialized_only=True):
        if not existing.in_atomic_block:
            existing.close_if_unusable_or_obsolete()
    return consume_once()


__all__ = [
    "ConsumeResult",
    "FreshnessStatus",
    "SearchRebuildReason",
    "consume_once",
    "describe_failure",
    "mark_rebuild_owed",
    "outstanding",
    "status",
    "worker_pass",
]
