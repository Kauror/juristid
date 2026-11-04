"""Recurring detection: checkpoints that never claim more than was read.

One pipeline serves bootstrap and every later scan. Three job classes share a
budget (requests per run):

* PRIORITY — a Lovable observation named a new or changed locator. A reason to
  look, not proof that anything changed.
* PERIODIC — per-section listings with an overlapping modification window, for
  edits and new files Lovable never mentions.
* FULL — a rotating reconciliation cursor over every in-scope section, page and
  resource inventory, including ARHIIV and closed topics.

FULL always gets a reserved share, so a busy day of priority signals cannot
starve the reconciliation that catches what the cheaper scans miss.

A scope's watermark advances only past what was *completely* read: an
unfinished listing chain, a failed capture or a page still pending retry holds
it back, and every hold is recorded as a gap.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime
from enum import StrEnum

#: Re-read this much before the watermark, so a page whose lastModified lands
#: late or out of order is not skipped.
OVERLAP = timedelta(hours=2)
MAX_RETRY_AFTER_SECONDS = 300
FULL_SHARE = 0.25


class JobKind(StrEnum):
    PRIORITY = "PRIORITY"
    PERIODIC = "PERIODIC"
    FULL = "FULL"


@dataclass(frozen=True)
class Job:
    kind: JobKind
    scope: str  # section id, page id, or reconciliation slot
    reason: str = ""


@dataclass
class ScopeCheckpoint:
    scope: str
    complete_through: datetime | None = None  # everything modified <= this was read and verified
    gaps: list[str] = field(default_factory=list)  # audit trail; open work lives in ``retry``
    retry: dict[str, datetime] = field(default_factory=dict)  # page -> lastModified, unresolved

    @property
    def has_open_work(self) -> bool:
        return bool(self.retry)


@dataclass(frozen=True)
class ScopeScanResult:
    listing_complete: bool
    max_modified_seen: datetime | None
    failed: Mapping[str, datetime] = field(default_factory=dict)  # page -> its lastModified
    resolved: Sequence[str] = ()  # retried pages captured and verified this run


def advance(
    checkpoint: ScopeCheckpoint, result: ScopeScanResult, *, now: datetime
) -> ScopeCheckpoint:
    """Move the watermark no further than the oldest unresolved item allows.

    A failed page joins ``retry`` and stays there until a later run verifies
    it, however old it is, so a failure older than the watermark is not lost
    to the overlap window.
    """
    gaps = list(checkpoint.gaps)
    retry = {page: when for page, when in checkpoint.retry.items() if page not in result.resolved}
    for page, when in result.failed.items():
        retry[page] = when
        gaps.append(f"{now.isoformat()} CAPTURE_FAILED {page}")
    if not result.listing_complete:
        gaps.append(f"{now.isoformat()} LISTING_INCOMPLETE")
        return ScopeCheckpoint(checkpoint.scope, checkpoint.complete_through, gaps, retry)
    if result.max_modified_seen is None:
        return ScopeCheckpoint(checkpoint.scope, checkpoint.complete_through, gaps, retry)
    target = result.max_modified_seen
    if retry:
        target = min(target, min(retry.values()) - timedelta(microseconds=1))
    if checkpoint.complete_through is not None and target < checkpoint.complete_through:
        target = checkpoint.complete_through  # never move backwards; ``retry`` keeps the hole
    return ScopeCheckpoint(checkpoint.scope, target, gaps, retry)


def listing_window_start(checkpoint: ScopeCheckpoint) -> datetime | None:
    """``lastModifiedDateTime ge <this>`` for the next periodic listing."""
    if checkpoint.complete_through is None:
        return None  # never read completely: list everything
    start = checkpoint.complete_through
    if checkpoint.retry:
        start = min(start, min(checkpoint.retry.values()))
    return start - OVERLAP


def plan(
    priority: Sequence[Job],
    periodic: Sequence[Job],
    full: Sequence[Job],
    *,
    budget: int,
    full_cursor: int = 0,
) -> tuple[list[Job], int]:
    """Choose this run's jobs and the FULL cursor for the next run.

    ``full`` is the complete, stable list of reconciliation slots (every section
    and resource slice). FULL keeps a reserved share whenever it has work, and
    the cursor rotates through all slots, so each is reached within
    ``ceil(len(full) / reserved)`` runs regardless of priority traffic.
    """
    if budget <= 0:
        return [], full_cursor
    reserved = min(len(full), max(1, int(budget * FULL_SHARE))) if full else 0
    start = full_cursor % len(full) if full else 0
    rotated = list(full[start:]) + list(full[:start])
    chosen: list[Job] = rotated[:reserved]
    remaining = budget - len(chosen)
    extra_full = 0
    for queue, is_full in ((priority, False), (periodic, False), (rotated[reserved:], True)):
        take = list(queue[:remaining])
        chosen.extend(take)
        remaining -= len(take)
        if is_full:
            extra_full = len(take)
        if remaining <= 0:
            break
    next_cursor = (start + reserved + extra_full) % len(full) if full else 0
    return chosen, next_cursor


def retry_after_seconds(header: str | None, *, attempt: int, now: datetime | None = None) -> float:
    """Honour ``Retry-After`` (seconds or HTTP date), bounded; else exponential backoff."""
    if header:
        value = header.strip()
        if value.isdigit():
            return float(min(int(value), MAX_RETRY_AFTER_SECONDS))
        try:
            when = parsedate_to_datetime(value)
        except (TypeError, ValueError):
            when = None
        if when is not None:
            base = now or datetime.now(UTC)
            return float(max(0.0, min((when - base).total_seconds(), MAX_RETRY_AFTER_SECONDS)))
    return float(min(2**attempt, MAX_RETRY_AFTER_SECONDS))


class RunOutcome(StrEnum):
    RUN_COMPLETE = "RUN_COMPLETE"  # every scope planned for THIS run was fully read
    PARTIAL = "PARTIAL"
    FAILED = "FAILED"
    AUTH_FAILED = "AUTH_FAILED"


def run_outcome(
    *, auth_ok: bool, scopes_planned: int, scopes_complete: int, gaps: int
) -> RunOutcome:
    """A zero-change run is RUN_COMPLETE only if every planned scope was fully read.

    RUN_COMPLETE says nothing about the whole corpus: corpus coverage is the
    FULL cursor having completed a cycle with no scope holding open work.
    """
    if not auth_ok:
        return RunOutcome.AUTH_FAILED
    if scopes_planned == 0 or scopes_complete == 0:
        return RunOutcome.FAILED
    if scopes_complete < scopes_planned or gaps:
        return RunOutcome.PARTIAL
    return RunOutcome.RUN_COMPLETE
