"""What each step of the nightly cycle actually did, from its own records (ADR 0229 §4).

An exit code of 0 is not success: the Lovable script exits 0 when it was paused,
not due or locked out, and the register commands exit 0 with registers they could
not read. So the cycle asks this module, once per step, after the step ended
(`manage.py nightly_outcome`), and records the answer, never a log line.

The sources are the steps' own ledgers:

* ``lovable`` — `LovableSyncRun` (trigger SCHEDULED, started since the cycle began):
  its status, error and aggregate counts; with no run, the control row (OFF or cut
  over is deliberate) and the night's run key (already ran tonight is deliberate);
* ``register-watch`` — `WatchRun` (`finished_at`, the ``katkes`` error, errors,
  targets checked, new findings, model failures);
* ``register-sweep`` — `SweepRun` (the same, with sources read and new candidates);
* ``government-agenda`` — it keeps no run ledger, so `watch_government_agenda
  --outcome-json` ends with one machine line of counts, handed over here
  (``reported``);
* ``riigikogu`` — `StageRun` (docs/adr/0224 §5), one row per stage of the cycle
  that began since the step started; the ANALYSIS stage is off under its own
  authorisation (docs/adr/0227) and never decides the outcome.

Outcomes: SUCCEEDED, SUCCEEDED_NO_NEW, PARTIAL (ran, with source errors, model
failures or held writes), SKIPPED_DELIBERATE (off, paused or already done by
design), SKIPPED_UNEXPECTED (no record and no reason: a lock, a missing run),
FAILED, INTERRUPTED (a record that never finished). Counts only, never a title.
"""

from __future__ import annotations

import datetime as dt
import json
from dataclasses import dataclass, field
from typing import Any

SUCCEEDED = "SUCCEEDED"
SUCCEEDED_NO_NEW = "SUCCEEDED_NO_NEW"
PARTIAL = "PARTIAL"
SKIPPED_DELIBERATE = "SKIPPED_DELIBERATE"
SKIPPED_UNEXPECTED = "SKIPPED_UNEXPECTED"
FAILED = "FAILED"
INTERRUPTED = "INTERRUPTED"
OUTCOMES = (
    SUCCEEDED,
    SUCCEEDED_NO_NEW,
    PARTIAL,
    SKIPPED_DELIBERATE,
    SKIPPED_UNEXPECTED,
    FAILED,
    INTERRUPTED,
)
#: The outcomes the host's daily monitor reports (the others are a good night).
PROBLEMS = frozenset({PARTIAL, SKIPPED_UNEXPECTED, FAILED, INTERRUPTED})
STEPS = ("lovable", "register-watch", "riigikogu", "register-sweep", "government-agenda")
#: An unfinished run older than this was stopped from outside (as `WatchRun.aborted`).
ABANDONED_AFTER = dt.timedelta(hours=3)
#: The machine line's prefix, for the host script.
PREFIX = "NIGHTLY-OUTCOME "

#: The supervisor's exit codes (`app.core.nightly_bounded`, ADR 0234): the step was
#: stopped at its execution budget; its previous run was still alive, so it was not
#: started; its process group could not be verified gone.
RC_TIMEOUT = 124
RC_BUSY = 75
RC_STUCK = 70


@dataclass(frozen=True)
class StepOutcome:
    step: str
    outcome: str
    reason: str = ""
    counts: dict[str, int] = field(default_factory=dict)

    def line(self) -> str:
        return PREFIX + json.dumps(
            {
                "step": self.step,
                "outcome": self.outcome,
                "reason": self.reason,
                "counts": self.counts,
            },
            sort_keys=True,
        )


def classify(
    step: str,
    *,
    since: dt.datetime,
    rc: int,
    reported: dict[str, Any] | None = None,
    manual: bool = False,
) -> StepOutcome:
    """``manual``: the cycle was a person's recovery run (`--manual`), whose Lovable
    synchronisation is recorded with trigger MANUAL, not SCHEDULED.

    The supervisor's own exit codes (ADR 0234) decide before the step's record:
    a step stopped at its budget is FAILED (TIMEOUT) even when its record looks
    tidy — it did not finish its night — and a step not started because its
    previous run was still alive is SKIPPED_UNEXPECTED (BUSY). The record's counts
    are kept beside the verdict."""
    if step == "lovable":
        return _lovable(since, rc, manual=manual)
    if step == "register-watch":
        found = _watch(since, rc)
    elif step == "register-sweep":
        found = _sweep(since, rc)
    elif step == "government-agenda":
        found = _agenda(rc, reported)
    elif step == "riigikogu":
        found = _riigikogu(since, rc)
    else:
        raise ValueError(f"unknown step: {step}")
    return _supervised(found, rc)


def _supervised(found: StepOutcome, rc: int) -> StepOutcome:
    """The step's outcome, overridden by what the supervisor's exit code says."""
    if found.outcome == SKIPPED_DELIBERATE:
        return found
    if rc == RC_TIMEOUT:
        return StepOutcome(
            found.step,
            FAILED,
            "TIMEOUT: stopped at its execution budget; the rest is read next night",
            found.counts,
        )
    if rc == RC_BUSY:
        return StepOutcome(
            found.step,
            SKIPPED_UNEXPECTED,
            "BUSY: its previous run was still running in the container; not started",
            found.counts,
        )
    if rc == RC_STUCK:
        return StepOutcome(
            found.step,
            INTERRUPTED,
            "STUCK: its processes could not be verified gone after the budget",
            found.counts,
        )
    return found


def _lovable(since: dt.datetime, rc: int, *, manual: bool = False) -> StepOutcome:
    from django.utils import timezone

    from app.legacy_import import lovable_sync
    from app.legacy_import.lovable_sync_models import (
        LovableSyncRun,
        SyncMode,
        SyncRunStatus,
        SyncTrigger,
    )

    step = "lovable"
    # A recovery (`juristid-nightly-cycle.sh --manual`) runs the sync script with
    # --manual: its record is MANUAL and has no night key, so «already ran tonight»
    # does not apply to it. The newest such run since the cycle began is the
    # recovery's own; the runner's flock keeps an on-demand run from overlapping it.
    trigger = SyncTrigger.MANUAL if manual else SyncTrigger.SCHEDULED
    run = (
        LovableSyncRun.objects.filter(trigger=trigger, started_at__gte=since)
        .order_by("-started_at")
        .first()
    )
    if run is None:
        ctl = lovable_sync.control()
        if ctl.cut_over:
            return StepOutcome(step, SKIPPED_DELIBERATE, "cut over: the nightly sync has ended")
        if ctl.mode == SyncMode.OFF:
            return StepOutcome(step, SKIPPED_DELIBERATE, "the sync is off (control mode OFF)")
        key = lovable_sync.run_key_for(SyncTrigger.SCHEDULED, timezone.now())
        if not manual and LovableSyncRun.objects.filter(run_key=key).exists():
            return StepOutcome(step, SKIPPED_DELIBERATE, f"already ran for {key}")
        if rc != 0:
            return StepOutcome(step, FAILED, f"exit {rc} with no run record")
        return StepOutcome(
            step, SKIPPED_UNEXPECTED, "no run record: the script skipped (a lock or not due)"
        )
    counts = run.counts or {}
    kept = {
        k: int(counts.get(k) or 0)
        for k in (
            "checked",
            "updated",
            "failed",
            "verify_failed",
            "new_topics_created",
            "new_topics_failed",
            "retired",
            "search_refresh_pending",
        )
    }
    if run.status == SyncRunStatus.RUNNING:
        return StepOutcome(step, INTERRUPTED, "the run never finished", kept)
    if run.status == SyncRunStatus.FAILED or rc != 0:
        return StepOutcome(step, FAILED, run.error or f"exit {rc}", kept)
    if run.status == SyncRunStatus.REVIEW_ONLY:
        return StepOutcome(step, PARTIAL, run.error or "held for review: nothing written", kept)
    if kept["failed"] or kept["verify_failed"] or kept["new_topics_failed"]:
        return StepOutcome(step, PARTIAL, "some Teemad failed or did not verify", kept)
    if kept["search_refresh_pending"]:
        return StepOutcome(step, PARTIAL, "search rows left to refresh", kept)
    if not (kept["updated"] or kept["new_topics_created"] or kept["retired"]):
        return StepOutcome(step, SUCCEEDED_NO_NEW, "", kept)
    return StepOutcome(step, SUCCEEDED, "", kept)


def _ledger(
    step: str,
    run: Any,
    rc: int,
    *,
    read: int,
    new: int,
    other_running: bool,
) -> StepOutcome:
    if run is None:
        if other_running:
            return StepOutcome(step, SKIPPED_UNEXPECTED, "another run held the lock")
        if rc != 0:
            return StepOutcome(step, FAILED, f"exit {rc} with no run record")
        return StepOutcome(step, SKIPPED_UNEXPECTED, "no run record")
    errors = list(run.errors or [])
    counts = {
        "read": read,
        "new": new,
        "errors": len(errors),
        "ai_failures": int(run.ai_failures or 0),
    }
    if any(isinstance(e, dict) and e.get("katkes") for e in errors):
        return StepOutcome(step, FAILED, "the run stopped before its end (katkes)", counts)
    if run.finished_at is None:
        return StepOutcome(step, INTERRUPTED, "the run never finished", counts)
    if rc != 0:
        return StepOutcome(step, FAILED, f"exit {rc}", counts)
    out_of_time = any(isinstance(e, dict) and e.get("ajapiir") for e in errors)
    if errors and read == 0:
        why = "read no source before its time limit" if out_of_time else "read no source"
        return StepOutcome(step, FAILED, why, counts)
    if out_of_time:
        # ADR 0234: what was read is kept; the rest is read next night.
        return StepOutcome(step, PARTIAL, "stopped at its time limit; the rest next night", counts)
    if errors or counts["ai_failures"]:
        return StepOutcome(step, PARTIAL, "source errors or model failures", counts)
    if new == 0:
        return StepOutcome(step, SUCCEEDED_NO_NEW, "", counts)
    return StepOutcome(step, SUCCEEDED, "", counts)


def _watch(since: dt.datetime, rc: int) -> StepOutcome:
    from django.conf import settings

    from app.register_watch.models import WatchRun

    if not getattr(settings, "REGISTER_WATCH_FETCH", False):
        return StepOutcome("register-watch", SKIPPED_DELIBERATE, "reading is off")
    run = WatchRun.objects.filter(started_at__gte=since).order_by("-started_at").first()
    other = (
        run is None
        and WatchRun.objects.filter(
            started_at__lt=since, started_at__gte=since - ABANDONED_AFTER, finished_at__isnull=True
        )
        .exclude(errors__contains=[{"katkes": True}])
        .exists()
    )
    return _ledger(
        "register-watch",
        run,
        rc,
        read=int(run.targets_checked) if run else 0,
        new=int(run.findings_new) if run else 0,
        other_running=other,
    )


def _sweep(since: dt.datetime, rc: int) -> StepOutcome:
    from django.conf import settings

    from app.register_watch import candidates
    from app.register_watch.models import SweepRun

    if not candidates.enabled() or not getattr(settings, "REGISTER_WATCH_FETCH", False):
        return StepOutcome("register-sweep", SKIPPED_DELIBERATE, "the sweep is off")
    run = SweepRun.objects.filter(started_at__gte=since).order_by("-started_at").first()
    other = (
        run is None
        and SweepRun.objects.filter(
            started_at__lt=since, started_at__gte=since - ABANDONED_AFTER, finished_at__isnull=True
        )
        .exclude(errors__contains=[{"katkes": True}])
        .exists()
    )
    return _ledger(
        "register-sweep",
        run,
        rc,
        read=int(run.sources_read) if run else 0,
        new=int(run.candidates_new) if run else 0,
        other_running=other,
    )


#: A Riigikogu stage's counts that mean something new was recorded (app/riigikogu/*.Counts).
_RIIGIKOGU_NEW = ("new", "changed", "moved", "cancelled")


def _riigikogu(since: dt.datetime, rc: int) -> StepOutcome:
    """The Riigikogu cycle from its own stage records (integration of #723 and #726).

    `watch_riigikogu` exits 0 when the cycle ran even if a stage failed (the failure
    is in that stage's record), and 1 when another cycle holds its lock.
    """
    from app.riigikogu import cycle
    from app.riigikogu.enums import RunStatus, Stage
    from app.riigikogu.models import StageRun

    step = "riigikogu"
    if not cycle.enabled():
        return StepOutcome(step, SKIPPED_DELIBERATE, "the Riigikogu cycle is off")
    started = StageRun.objects.filter(started_at__gte=since).order_by("-started_at")
    latest = started.first()
    if latest is None:
        if rc == 1:
            return StepOutcome(step, SKIPPED_UNEXPECTED, "another Riigikogu cycle held the lock")
        if rc:
            return StepOutcome(step, FAILED, f"exit {rc} with no run record")
        return StepOutcome(step, SKIPPED_UNEXPECTED, "no run record")
    # One cycle, the one this step started (the newest since the step began; the
    # cycle's advisory lock keeps two from overlapping), judged on every stage a
    # nightly cycle runs (`cycle.ORDER`), not on whatever rows happen to exist.
    this_cycle = list(StageRun.objects.filter(cycle=latest.cycle))
    analysis = [run for run in this_cycle if run.stage == Stage.ANALYSIS]
    runs = [run for run in this_cycle if run.stage != Stage.ANALYSIS]
    expected = [stage for stage in cycle.ORDER if stage != Stage.ANALYSIS]
    missing = [stage for stage in expected if stage not in {run.stage for run in runs}]
    if not cycle.analysis_enabled() and any(run.status != RunStatus.DISABLED for run in analysis):
        return StepOutcome(step, FAILED, "document analysis ran while it is switched off")
    statuses = [run.status for run in runs]
    new = sum(
        int(value)
        for run in runs
        for key, value in (run.counts or {}).items()
        if isinstance(value, int) and any(word in key for word in _RIIGIKOGU_NEW)
    )
    counts = {
        "stages": len(runs),
        "missing": len(missing),
        "failed": statuses.count(RunStatus.FAILED),
        "partial": statuses.count(RunStatus.PARTIAL),
        "new": new,
        "requests": sum(int(run.requests or 0) for run in runs),
    }
    if RunStatus.RUNNING in statuses:
        return StepOutcome(step, INTERRUPTED, "a stage never finished", counts)
    if missing:
        return StepOutcome(step, INTERRUPTED, "stages not recorded: " + ", ".join(missing), counts)
    if all(status == RunStatus.FAILED for status in statuses):
        return StepOutcome(step, FAILED, "every stage failed", counts)
    if counts["failed"] or counts["partial"]:
        return StepOutcome(step, PARTIAL, "a stage failed or read its source in part", counts)
    if rc:
        return StepOutcome(step, FAILED, f"exit {rc}", counts)
    if not new:
        return StepOutcome(step, SUCCEEDED_NO_NEW, "", counts)
    return StepOutcome(step, SUCCEEDED, "", counts)


def _agenda(rc: int, reported: dict[str, Any] | None) -> StepOutcome:
    from app.register_watch import agenda

    step = "government-agenda"
    if not agenda.enabled():
        return StepOutcome(step, SKIPPED_DELIBERATE, "reading is off")
    if reported is None:
        if rc != 0:
            return StepOutcome(step, FAILED, f"exit {rc} with no outcome line")
        return StepOutcome(step, SKIPPED_UNEXPECTED, "no outcome line")
    keys = (
        "stored",
        "judged",
        "errors",
        "ai_failures",
        "not_published",
        "inaccessible",
        "unconfirmed",
        "missing",
    )
    counts = {k: int(reported.get(k) or 0) for k in keys}
    # The session day and status of the week still waiting (r95): evidence, not a count.
    note = str(reported.get("note") or "")[:60]
    where = f" ({note})" if note else ""
    if rc != 0:
        return StepOutcome(step, FAILED, f"exit {rc}", counts)
    if counts["errors"] or counts["ai_failures"]:
        return StepOutcome(step, PARTIAL, "valitsus.ee errors or model failures", counts)
    if counts["missing"]:
        # Friday's cycle was the week's last reading and nothing was stored.
        return StepOutcome(
            step,
            PARTIAL,
            f"this week's agenda missing after its last nightly reading{where}",
            counts,
        )
    if counts["unconfirmed"]:
        return StepOutcome(
            step, PARTIAL, f"valitsus.ee refused the agenda; publication unconfirmed{where}", counts
        )
    if counts["inaccessible"]:
        return StepOutcome(
            step, PARTIAL, f"a past session's agenda address is inaccessible{where}", counts
        )
    if not (counts["stored"] or counts["judged"]):
        if counts["not_published"]:
            return StepOutcome(step, SUCCEEDED_NO_NEW, f"agenda not yet published{where}", counts)
        return StepOutcome(step, SUCCEEDED_NO_NEW, "", counts)
    return StepOutcome(step, SUCCEEDED, "", counts)


# -- what Haldus shows ------------------------------------------------------------------

#: One cache entry per step (the database cache, `core_cache`): the host's cycle
#: records each verdict as it is given, so Haldus shows a timed-out, busy or stuck
#: step that left no run record of its own (ADR 0234). The host's state files stay
#: the cycle's own ledger; this is the application's view of the last night.
CACHE_KEY = "nightly-step:{step}"
REMEMBER_SECONDS = 14 * 24 * 3600


def remember(result: StepOutcome, *, rc: int, seconds: int | None = None, night: str = "") -> None:
    """Keep ``result`` as the step's last verdict (counts and codes, never a title)."""
    from django.core.cache import cache
    from django.utils import timezone

    cache.set(
        CACHE_KEY.format(step=result.step),
        {
            "step": result.step,
            "outcome": result.outcome,
            "reason": result.reason[:200],
            "counts": result.counts,
            "rc": rc,
            "seconds": seconds,
            "night": night[:40],
            "at": timezone.now().isoformat(),
        },
        REMEMBER_SECONDS,
    )


def remembered() -> list[dict[str, Any]]:
    """Every step's last recorded verdict, in the cycle's order; a step never recorded
    is left out. ``problem`` marks the outcomes the daily monitor reports."""
    from django.core.cache import cache

    found = cache.get_many([CACHE_KEY.format(step=step) for step in STEPS])
    rows = []
    for step in STEPS:
        row = found.get(CACHE_KEY.format(step=step))
        if isinstance(row, dict):
            rows.append({**row, "problem": row.get("outcome") in PROBLEMS})
    return rows
