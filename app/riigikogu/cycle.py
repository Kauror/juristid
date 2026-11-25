"""The nightly Riigikogu cycle: stages, each on its own (ADR 0224 §5).

One cycle, run once a night as the ``riigikogu`` step of the coordinated nightly
cycle (ADR 0229, `scripts/deploy/juristid-nightly-cycle.sh --with-riigikogu`;
nothing is installed by this code). Each stage:

* runs in its own `StageRun` with its own status, counts, errors, request count
  and rate-limit count;
* reads a **window** that starts `OVERLAP` before the end of its last successful
  window, so a document registered late is still seen, and a failed night's
  window is read again — a PARTIAL or FAILED run never moves the checkpoint;
* has its own request budget, and stops as PARTIAL when it runs out;
* has its own time limit (`STAGE_TIME_LIMITS`) inside the cycle's
  (`CYCLE_TIME_LIMIT`), and stops as PARTIAL when it is reached — no request,
  retry, back-off or ledger wait runs past it (`client.time_limit`); a stage the
  cycle had no time left to start is recorded PARTIAL with `NOT_RUN`, never
  left without a row;
* never takes another stage down: an exception is that stage's FAILED, recorded,
  and the next stage starts.

The ANALYSIS stage exists and stays DISABLED until full-document analysis is
separately authorised (ADR 0227).
"""

from __future__ import annotations

import datetime as dt
import logging
import uuid as uuid_module
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any

from django.conf import settings
from django.db import connection
from django.utils import timezone

from app.core.errors import DomainError
from app.riigikogu import agendas, amendments, client, invitations, protocols, responses, stenograms
from app.riigikogu.enums import RunStatus, Stage
from app.riigikogu.models import StageRun

logger = logging.getLogger("app.riigikogu")

RUN_LOCK = 222_0002
ALREADY_RUNNING = "Riigikogu kontroll juba käib."
#: Stage order. Bills first: invitations and agendas reuse the bills it stored.
ORDER = (
    Stage.BILLS,
    Stage.INVITATIONS,
    Stage.AGENDAS,
    Stage.PROTOCOLS,
    Stage.STENOGRAMS,
    Stage.RESPONSES,
    Stage.ANALYSIS,
)
BUDGETS: dict[str, int] = {
    Stage.BILLS: 60,
    Stage.INVITATIONS: 250,
    Stage.AGENDAS: 80,
    Stage.PROTOCOLS: 120,
    Stage.STENOGRAMS: 20,
    Stage.RESPONSES: 40,
    Stage.ANALYSIS: 0,
}
#: Wall-clock limits (ADR 0224 §5, ADR 0229). A request costs at most
#: `REQUEST_SECONDS` in a normal night: one path is read at most 11 times a
#: minute (5.45 s apart, `client.PER_PATH_PER_MINUTE`; the door spaces any two
#: calls 3 s apart) plus up to 2 s for the answer. Each stage may spend its whole
#: request budget at that pace and still have `BACKOFF_ALLOWANCE` for one full
#: round of 429 back-offs (5 + 15 + 45 s) — so a normal night never meets a
#: limit, and a bad one (Retry-After up to 120 s each time, a server that drips
#: its answer, a ledger kept busy by another process) stops at it.
REQUEST_SECONDS = 7.5
BACKOFF_ALLOWANCE = dt.timedelta(minutes=2)
STAGE_TIME_LIMITS: dict[str, dt.timedelta] = {
    Stage.BILLS: dt.timedelta(minutes=10),  # 60 × 7.5 s = 7.5 min, + 2
    Stage.INVITATIONS: dt.timedelta(minutes=35),  # 250 × 7.5 s = 31.25 min, + 2
    Stage.AGENDAS: dt.timedelta(minutes=12),  # 80 × 7.5 s = 10 min, + 2
    Stage.PROTOCOLS: dt.timedelta(minutes=18),  # 120 × 7.5 s = 15 min, + 2
    Stage.STENOGRAMS: dt.timedelta(minutes=5),  # 20 × 7.5 s = 2.5 min, + 2
    Stage.RESPONSES: dt.timedelta(minutes=7),  # 40 × 7.5 s = 5 min, + 2
    Stage.ANALYSIS: dt.timedelta(0),
}
#: The whole cycle: the stages' limits (87 min) and 3 minutes for the work between
#: requests. `app.core.nightly_schedule.FINISH_BY["riigikogu"]` is built on it.
CYCLE_TIME_LIMIT = dt.timedelta(minutes=90)
#: A stage is not started with less than this left of the cycle's limit.
MIN_STAGE_TIME = dt.timedelta(minutes=1)
#: A stage the cycle had no time left to start.
NOT_RUN = "ei käivitunud: tsükli ajapiir (90 min) oli täis; loetakse järgmisel ööl"
OVERLAP = dt.timedelta(days=invitations.OVERLAP_DAYS)
STENOGRAM_OVERLAP = dt.timedelta(days=3)


def enabled() -> bool:
    """The cycle reads the Riigikogu only when switched on *and* fetching is on."""
    return bool(getattr(settings, "RIIGIKOGU_MONITORING", False)) and bool(
        getattr(settings, "REGISTER_WATCH_FETCH", False)
    )


def analysis_enabled() -> bool:
    """Full-document model analysis (ADR 0227): never on without a separate authorisation."""
    return bool(getattr(settings, "RIIGIKOGU_DOCUMENT_AI", False))


def checkpoint(stage: str) -> dt.date | None:
    """The end of the stage's last *successful* window."""
    return (
        StageRun.objects.filter(stage=stage, status=RunStatus.SUCCEEDED, window_end__isnull=False)
        .order_by("-window_end")
        .values_list("window_end", flat=True)
        .first()
    )


def window(
    stage: str, *, today: dt.date, first_days: int, overlap: dt.timedelta
) -> tuple[dt.date, dt.date]:
    last = checkpoint(stage)
    start = (last - overlap) if last is not None else today - dt.timedelta(days=first_days)
    return start, today


@contextmanager
def _one_at_a_time() -> Iterator[None]:
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_try_advisory_lock(%s)", [RUN_LOCK])
        (taken,) = cursor.fetchone()
    if not taken:
        raise DomainError(ALREADY_RUNNING)
    try:
        yield
    finally:
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_advisory_unlock(%s)", [RUN_LOCK])


@dataclass(frozen=True)
class Step:
    stage: str
    window: tuple[dt.date, dt.date] | None
    run: Callable[[client.Budget], Any]


def _steps(today: dt.date, now: dt.datetime) -> list[Step]:
    inv = window(
        Stage.INVITATIONS, today=today, first_days=invitations.FIRST_WINDOW_DAYS, overlap=OVERLAP
    )
    steno = window(Stage.STENOGRAMS, today=today, first_days=7, overlap=STENOGRAM_OVERLAP)
    return [
        Step(
            Stage.BILLS,
            (today, today + dt.timedelta(days=amendments.AHEAD_DAYS)),
            lambda b: amendments.read_window(today=today, budget=b),
        ),
        Step(
            Stage.INVITATIONS,
            inv,
            lambda b: invitations.discover(since=inv[0], until=inv[1], budget=b, now=now),
        ),
        Step(
            Stage.AGENDAS,
            (today, today + dt.timedelta(days=agendas.WINDOW_DAYS)),
            lambda b: agendas.read_window(
                start=today, end=today + dt.timedelta(days=agendas.WINDOW_DAYS), budget=b, now=now
            ),
        ),
        Step(
            Stage.PROTOCOLS,
            (today - dt.timedelta(days=protocols.BACK_DAYS), today),
            lambda b: protocols.read_recent(today=today, budget=b),
        ),
        Step(
            Stage.STENOGRAMS,
            steno,
            lambda b: stenograms.read_days(start=steno[0], end=steno[1], budget=b),
        ),
        Step(Stage.RESPONSES, None, lambda b: responses.check(today=today, budget=b)),
    ]


def run(*, today: dt.date | None = None, only: set[str] | None = None) -> list[StageRun]:
    """One cycle. Returns its stage runs; raises only `ALREADY_RUNNING`.

    Ends within `CYCLE_TIME_LIMIT` of its start, give or take the work after a
    stage's last answer: each stage reads until the earlier of its own limit and
    the cycle's, and a stage with no time left is recorded as not run.
    """
    today = today or timezone.localdate()
    now = timezone.now()
    cycle = uuid_module.uuid4()
    done: list[StageRun] = []
    until = client.clock() + CYCLE_TIME_LIMIT.total_seconds()
    with _one_at_a_time():
        if not enabled():
            for stage in ORDER:
                done.append(_closed(cycle, stage, RunStatus.DISABLED, now))
            return done
        for step in _steps(today, now):
            if only and step.stage not in only:
                continue
            if until - client.clock() < MIN_STAGE_TIME.total_seconds():
                done.append(_not_run(cycle, step))
                continue
            limit = STAGE_TIME_LIMITS.get(step.stage, CYCLE_TIME_LIMIT).total_seconds()
            with client.time_limit(min(until, client.clock() + limit)):
                done.append(_run_step(cycle, step))
        if not only or Stage.ANALYSIS in only:
            done.append(
                _closed(
                    cycle,
                    Stage.ANALYSIS,
                    RunStatus.DISABLED if not analysis_enabled() else RunStatus.SUCCEEDED,
                    now,
                )
            )
    return done


def _closed(cycle: Any, stage: str, status: str, now: dt.datetime) -> StageRun:
    return StageRun.objects.create(
        cycle=cycle, stage=stage, status=status, started_at=now, finished_at=now
    )


def _not_run(cycle: Any, step: Step) -> StageRun:
    """PARTIAL, never a missing row: the window was not read, the checkpoint stays."""
    moment = timezone.now()
    return StageRun.objects.create(
        cycle=cycle,
        stage=step.stage,
        status=RunStatus.PARTIAL,
        started_at=moment,
        finished_at=moment,
        window_start=step.window[0] if step.window else None,
        window_end=step.window[1] if step.window else None,
        errors=[{"viga": NOT_RUN, "ajapiir": "tsükkel"}],
    )


def _run_step(cycle: Any, step: Step) -> StageRun:
    record = StageRun.objects.create(
        cycle=cycle,
        stage=step.stage,
        started_at=timezone.now(),
        window_start=step.window[0] if step.window else None,
        window_end=step.window[1] if step.window else None,
    )
    budget = client.Budget(limit=BUDGETS.get(step.stage, 50))
    try:
        counts = step.run(budget)
    except client.OutOfTime as error:
        record.status = RunStatus.PARTIAL
        record.errors = [{"viga": str(error), "ajapiir": "etapp"}]
    except client.BudgetExceeded as error:
        record.status = RunStatus.PARTIAL
        record.errors = [{"viga": str(error)}]
    except client.FetchError as error:
        record.status = RunStatus.FAILED
        record.errors = [{"viga": str(error)[:300]}]
    except Exception as error:  # one stage's defect is that stage's
        logger.exception("riigikogu: stage %s", step.stage)
        record.status = RunStatus.FAILED
        record.errors = [{"viga": f"{type(error).__name__}: {error}"[:300]}]
    else:
        errors = list(getattr(counts, "errors", []) or [])
        record.counts = counts.as_dict() if hasattr(counts, "as_dict") else {}
        record.errors = errors[:50]
        # A run that could not read some of its documents read the window only
        # partly: the checkpoint stays, and tomorrow reads them again.
        record.status = RunStatus.PARTIAL if errors else RunStatus.SUCCEEDED
    record.requests = budget.spent
    record.rate_limited = budget.rate_limited
    record.finished_at = timezone.now()
    record.save()
    return record


# -- is it working ------------------------------------------------------------------

#: No success for this long is stale. A stage starts at most 4 h 50 into the
#: cycle (`nightly_schedule.FINISH_BY["register-watch"]`, a lock wait and
#: `CYCLE_TIME_LIMIT`), and nights are 23–25 h: the next night's start is at most
#: 29 h 50 after the last one's.
STALE_AFTER = dt.timedelta(hours=30)
#: A stage still RUNNING after this long was stopped from outside (killed with
#: its process): the cycle's limit and the step's 10 minutes of slack, with room.
RUNNING_AT_MOST = CYCLE_TIME_LIMIT + dt.timedelta(minutes=30)


@dataclass(frozen=True)
class StageHealth:
    stage: str
    label: str
    state: str
    #: In the page's words: «Pole veel käinud», «Välja lülitatud», «Ebaõnnestus», …
    line: str
    last_success: Any
    last_attempt: Any


def health(now: dt.datetime | None = None) -> list[StageHealth]:
    """Per stage: never «success with zero findings» for a stage that failed or did not run."""
    now = now or timezone.now()
    rows = []
    for stage in ORDER:
        attempt = StageRun.objects.filter(stage=stage).order_by("-started_at").first()
        success = (
            StageRun.objects.filter(stage=stage, status=RunStatus.SUCCEEDED)
            .order_by("-started_at")
            .first()
        )
        label = Stage(stage).label
        if stage == Stage.ANALYSIS and not analysis_enabled():
            state, line = "disabled", "Välja lülitatud (vajab eraldi luba, ADR 0227)"
        elif not enabled():
            state, line = "disabled", "Riigikogu jälgimine on välja lülitatud"
        elif attempt is None and now < first_run_due():
            state, line = "pending", "Esimene kontroll on ööl tulemas"
        elif attempt is None:
            state, line = "never", "Pole veel käinud"
        elif attempt.status == RunStatus.FAILED:
            state, line = "failed", "Viimane kontroll ebaõnnestus"
        elif attempt.status == RunStatus.PARTIAL and _ran_out_of_time(attempt):
            state, line = "partial", _TIME_LINES[_ran_out_of_time(attempt)]
        elif attempt.status == RunStatus.PARTIAL:
            state, line = "partial", "Viimane kontroll luges allikat osaliselt"
        elif attempt.status == RunStatus.RUNNING and attempt.started_at < now - RUNNING_AT_MOST:
            state, line = "failed", "Kontroll katkes"
        elif success is None or success.started_at < now - STALE_AFTER:
            state, line = "stale", "Üle 30 tunni pole ükski kontroll õnnestunud"
        else:
            state, line = "ok", "Korras"
        rows.append(
            StageHealth(
                stage=stage,
                label=label,
                state=state,
                line=line,
                last_success=success,
                last_attempt=attempt,
            )
        )
    return rows


_TIME_LINES = {
    "etapp": "Viimane kontroll jõudis ajapiirini; ülejäänu loetakse järgmisel ööl",
    "tsükkel": "Eelmisel ööl jäi ajapiiri tõttu käivitamata; loetakse järgmisel ööl",
}


def _ran_out_of_time(run: StageRun) -> str:
    """``"etapp"`` or ``"tsükkel"`` when the run stopped at a time limit, else ``""``."""
    for error in run.errors or []:
        if isinstance(error, dict) and error.get("ajapiir") in _TIME_LINES:
            return str(error["ajapiir"])
    return ""


def first_run_due() -> dt.datetime:
    """Until when a cycle that never ran is not late: the Riigikogu step's finish-by in
    the first nightly cycle after this app's tables were made (ADR 0229 §6). Once
    the switch is on, the supervised first run (or that night's step) settles it."""
    from django.db.migrations.recorder import MigrationRecorder

    from app.core import nightly_schedule

    row = (
        MigrationRecorder.Migration.objects.filter(app="riigikogu", name="0001_initial")
        .only("applied")
        .first()
    )
    if row is None or row.applied is None:
        return dt.datetime.min.replace(tzinfo=dt.UTC)
    return nightly_schedule.step_due("riigikogu", row.applied)
