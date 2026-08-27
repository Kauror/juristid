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
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from django.db.models import Count, F, Min, QuerySet, Sum
from django.db.models.functions import Coalesce
from django.utils import timezone

from app.search.indexing import RebuildResult, rebuild_all
from app.search.models import SearchRebuildDebt, SearchRebuildReason

#: How much of a failure to keep. The whole traceback belongs in the worker's
#: log; this column exists so an operator running a one-line probe learns *why*
#: without going to find the log, and a database column is a bad place to grow a
#: stack trace.
MAX_RECORDED_ERROR_CHARACTERS = 500


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
        SearchRebuildDebt.objects.filter(pk__in=claimed).update(
            attempts=F("attempts") + 1,
            last_attempt_at=timezone.now(),
            last_error=str(error)[:MAX_RECORDED_ERROR_CHARACTERS],
        )
        raise

    # Only the rows claimed before the rebuild began. Anything marked while it
    # ran survives and is paid off by the next pass.
    cleared, _ = SearchRebuildDebt.objects.filter(pk__in=claimed).delete()
    return ConsumeResult(rebuilt=True, cleared=cleared, result=result)


__all__ = [
    "ConsumeResult",
    "FreshnessStatus",
    "SearchRebuildReason",
    "consume_once",
    "mark_rebuild_owed",
    "outstanding",
    "status",
]
