"""The nightly Riigikogu cycle: stages, each on its own (ADR 0224 §5).

One cycle, run once a night inside the coordinated nightly sequence (the
proposed order is in `deploy/unraid-main/cron/juristid.cron.example`'s comment
and ADR 0224 §5; nothing is installed by this code). Each stage:

* runs in its own `StageRun` with its own status, counts, errors, request count
  and rate-limit count;
* reads a **window** that starts `OVERLAP` before the end of its last successful
  window, so a document registered late is still seen, and a failed night's
  window is read again — a PARTIAL or FAILED run never moves the checkpoint;
* has its own request budget, and stops as PARTIAL when it runs out;
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
    """One cycle. Returns its stage runs; raises only `ALREADY_RUNNING`."""
    today = today or timezone.localdate()
    now = timezone.now()
    cycle = uuid_module.uuid4()
    done: list[StageRun] = []
    with _one_at_a_time():
        if not enabled():
            for stage in ORDER:
                done.append(_closed(cycle, stage, RunStatus.DISABLED, now))
            return done
        for step in _steps(today, now):
            if only and step.stage not in only:
                continue
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

STALE_AFTER = dt.timedelta(hours=30)


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
        elif attempt is None:
            state, line = "never", "Pole veel käinud"
        elif attempt.status == RunStatus.FAILED:
            state, line = "failed", "Viimane kontroll ebaõnnestus"
        elif attempt.status == RunStatus.PARTIAL:
            state, line = "partial", "Viimane kontroll luges allikat osaliselt"
        elif attempt.status == RunStatus.RUNNING and attempt.started_at < now - dt.timedelta(
            hours=3
        ):
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
