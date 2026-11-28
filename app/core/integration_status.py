"""Nightly history and per-source freshness, read from the records the steps already keep (r98).

Nothing here decides what «success» is: step outcomes are `nightly_outcome`'s (stored in
`NightlyStepRecord`), the register watch's verdict is `watch.watch_health`, the Riigikogu
stages' is `riigikogu.cycle.health`. This module only lays those records side by side,
per night and per source, and says what is *not* known. It makes no request and writes
nothing; a source's last check is read from its own run ledger.

Freshness is never one threshold: the daily steps are late 36 h after their last success;
the Government's agenda is a weekly publication and is judged by its own check states; the
Riigikogu's stages by their own rules.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from typing import Any

from django.utils import timezone

from app.core import nightly_outcome, nightly_schedule
from app.core.models import NightlyStepRecord

#: A daily step with no success for this long is stale (a night plus the slack of its allowance).
DAILY_STALE_AFTER = dt.timedelta(hours=36)
#: A stored agenda older than this, with the weekly check not explaining it, is stale.
AGENDA_STALE_AFTER = dt.timedelta(days=9)

OK, EXPECTED, ATTENTION, DISABLED, UNKNOWN = "ok", "expected", "attention", "disabled", "unknown"

STEP_LABELS = {
    "lovable": "Lovable'i sünkroonimine",
    "government-agenda": "Valitsuse päevakord",
    "register-watch": "Registrite jälgimine",
    "riigikogu": "Riigikogu kontroll",
    "register-sweep": "Uute eelnõude otsing",
}


# -- nightly history ------------------------------------------------------------------------------


@dataclass
class NightStep:
    step: str
    label: str
    record: NightlyStepRecord | None
    expected_by: dt.datetime
    #: «no record» for a night the application has records for but not for this step.
    missing: bool

    @property
    def problem(self) -> bool:
        return self.record is not None and self.record.outcome in nightly_outcome.PROBLEMS


@dataclass
class Night:
    start: dt.datetime
    steps: list[NightStep]
    ai_operations: int = 0
    ai_failed: int = 0
    rk_cycle: str = ""

    @property
    def day(self) -> dt.date:
        return self.start.astimezone(nightly_schedule.TALLINN).date()

    @property
    def problems(self) -> int:
        return sum(1 for s in self.steps if s.problem or s.missing)


def history(nights: int = 30, now: dt.datetime | None = None) -> list[Night]:
    """The last ``nights`` cycles that left at least one record, newest first.

    A cycle is one `cycle_start`; the cycle script writes the same start for each of its
    steps, so the correlation is the script's own, not inferred. AI operations are matched
    to a night by **time window** (the cycle's start to its latest allowed finish) because
    the ledger has no cycle id — the page says so. Nights before records existed are absent.
    """
    from app.ai_usage.models import AiOperation, Outcome
    from app.riigikogu.models import StageRun

    now = now or timezone.now()
    starts = list(
        NightlyStepRecord.objects.order_by("-cycle_start")
        .values_list("cycle_start", flat=True)
        .distinct()[:nights]
    )
    found: list[Night] = []
    for start in starts:
        rows = {r.step: r for r in NightlyStepRecord.objects.filter(cycle_start=start)}
        steps = []
        for step in nightly_outcome.STEPS:
            due = nightly_schedule.after_start(start, nightly_schedule.FINISH_BY[step])
            record = rows.get(step)
            steps.append(
                NightStep(step, STEP_LABELS[step], record, due, record is None and due < now)
            )
        end = start + nightly_schedule.CYCLE_COMPLETE_BY
        ops = AiOperation.objects.filter(started_at__gte=start, started_at__lt=end)
        cycle = (
            StageRun.objects.filter(started_at__gte=start, started_at__lt=end)
            .values_list("cycle", flat=True)
            .first()
        )
        found.append(
            Night(
                start,
                steps,
                ai_operations=ops.count(),
                ai_failed=ops.exclude(
                    outcome__in=(Outcome.SUCCEEDED, Outcome.SUCCEEDED_AFTER_RETRY)
                )
                .exclude(outcome=Outcome.IN_PROGRESS)
                .count(),
                rk_cycle=str(cycle)[:8] if cycle else "",
            )
        )
    return found


# -- per-source freshness -------------------------------------------------------------------------


@dataclass
class Source:
    key: str
    label: str
    cadence: str
    state: str
    line: str
    last_attempt: dt.datetime | None = None
    last_success: dt.datetime | None = None
    #: The newest publication seen at the source and the newest stored here (None = not tracked).
    observed: str = ""
    stored: str = ""
    pending: list[str] = field(default_factory=list)
    limits: list[str] = field(default_factory=list)


def _daily(last_success: dt.datetime | None, now: dt.datetime) -> tuple[str, str]:
    if last_success is None:
        return UNKNOWN, "Õnnestunud kontrolli pole veel salvestatud."
    if now - last_success > DAILY_STALE_AFTER:
        return ATTENTION, "Viimasest õnnestunud kontrollist on üle 36 tunni."
    return OK, "Värske"


def _watch(now: dt.datetime) -> Source:
    from django.conf import settings

    from app.register_watch.models import FindingStatus, Relevance, WatchFinding, WatchRun
    from app.register_watch.watch import watch_health

    attempt = WatchRun.objects.order_by("-started_at").first()
    success = (
        WatchRun.objects.filter(finished_at__isnull=False)
        .exclude(errors__contains=[{"katkes": True}])
        .order_by("-finished_at")
        .first()
    )
    source = Source(
        "register-watch",
        STEP_LABELS["register-watch"],
        "Iga öö (1 h pärast tsükli algust)",
        OK,
        "",
        attempt.started_at if attempt else None,
        success.finished_at if success else None,
    )
    if not getattr(settings, "REGISTER_WATCH_FETCH", False):
        source.state, source.line = DISABLED, "Välja lülitatud"
        return source
    health = watch_health(now)
    if not health.ok:
        source.state, source.line = ATTENTION, health.problem or health.detail
    else:
        source.state, source.line = _daily(source.last_success, now)
    unjudged = WatchFinding.objects.filter(
        status=FindingStatus.PENDING, relevance=Relevance.UNJUDGED
    ).count()
    source.pending.append(f"{unjudged} pakkumist ootab otsust ja on mudeli poolt hindamata")
    if attempt is not None and attempt.errors:
        source.pending.append(f"viimasel käivitusel {len(attempt.errors)} lugemisviga")
    source.limits.append("Allikate avaldamiskuupäeva ei jälgita; vaadatakse viimast lugemist.")
    return source


def _sweep(now: dt.datetime) -> Source:
    from django.conf import settings

    from app.register_watch.models import CandidateStatus, Strength, SweepRun, TopicCandidate

    attempt = SweepRun.objects.order_by("-started_at").first()
    success = (
        SweepRun.objects.filter(finished_at__isnull=False)
        .exclude(errors__contains=[{"katkes": True}])
        .order_by("-finished_at")
        .first()
    )
    source = Source(
        "register-sweep",
        STEP_LABELS["register-sweep"],
        "Iga öö (tsükli viimane samm)",
        OK,
        "",
        attempt.started_at if attempt else None,
        success.finished_at if success else None,
    )
    if not getattr(settings, "REGISTER_SWEEP", False):
        source.state, source.line = DISABLED, "Välja lülitatud"
        return source
    source.state, source.line = _daily(source.last_success, now)
    waiting = TopicCandidate.objects.filter(status=CandidateStatus.NEW)
    source.pending.append(
        f"{waiting.count()} uut kandidaati ootab inimese otsust, "
        f"neist {waiting.filter(strength=Strength.UNJUDGED).count()} mudeli poolt hindamata"
    )
    return source


def _agenda(now: dt.datetime) -> Source:
    from app.register_watch.models import (
        AgendaCheckState,
        AgendaItem,
        AgendaItemStatus,
        GovernmentAgenda,
        GovernmentAgendaCheck,
        Strength,
    )

    attempt = GovernmentAgendaCheck.objects.order_by("-created_at").first()
    discovered = (
        GovernmentAgendaCheck.objects.filter(discovery_ok=True).order_by("-created_at").first()
    )
    stored = GovernmentAgenda.objects.order_by("-session_date").first()
    observed = (
        GovernmentAgendaCheck.objects.filter(session_date__isnull=False)
        .order_by("-session_date")
        .first()
    )
    source = Source(
        "government-agenda",
        STEP_LABELS["government-agenda"],
        "Iga öö; päevakord ilmub nädalas kord (nädala viimane katse on reede öösel)",
        OK,
        "Korras",
        attempt.created_at if attempt else None,
        discovered.created_at if discovered else None,
        observed.session_date.isoformat() if observed and observed.session_date else "",
        stored.session_date.isoformat() if stored else "",
    )
    explained: dict[str, str] = {
        AgendaCheckState.NOT_FOUND: EXPECTED,
        AgendaCheckState.NOT_PUBLISHED: EXPECTED,
        AgendaCheckState.STORED: OK,
        AgendaCheckState.ALREADY_STORED: OK,
        AgendaCheckState.REVISED: OK,
    }
    if attempt is None:
        source.state, source.line = UNKNOWN, "Kontrolle pole veel salvestatud."
    else:
        source.state = explained.get(str(attempt.state), ATTENTION)
        source.line = attempt.get_state_display()
        if attempt.missing:
            source.state, source.line = ATTENTION, "Nädala päevakord puudub (viimane katse)"
    if (
        observed is not None
        and observed.session_date
        and (stored is None or observed.session_date > stored.session_date)
    ):
        source.state = ATTENTION
        source.line = "Allikas näitas uuemat istungit, kui on salvestatud"
    if stored is not None and now - stored.created_at > AGENDA_STALE_AFTER and source.state == OK:
        source.state, source.line = ATTENTION, "Salvestatud päevakord on üle 9 päeva vana"
    open_items = AgendaItem.objects.filter(
        status=AgendaItemStatus.PENDING, source_removed_at__isnull=True
    )
    source.pending.append(
        f"{open_items.count()} päevakorrapunkti ootab otsust, "
        f"neist {open_items.filter(strength=Strength.UNJUDGED).count()} hindamata"
    )
    source.limits.append(
        "«Viimane õnnestunud kontroll» on viimane kord, kui valitsus.ee loendid loeti."
    )
    return source


def _riigikogu(now: dt.datetime) -> list[Source]:
    from app.riigikogu import cycle

    rows = []
    for stage in cycle.health(now):
        state = {
            "ok": OK,
            "disabled": DISABLED,
            "pending": EXPECTED,
            "never": UNKNOWN,
        }.get(stage.state, ATTENTION)
        rows.append(
            Source(
                f"riigikogu-{stage.stage}",
                f"Riigikogu · {stage.label}",
                "Iga öö; etapi enda reeglite järgi (ADR 0224)",
                state,
                stage.line,
                getattr(stage.last_attempt, "started_at", None),
                getattr(stage.last_success, "started_at", None),
            )
        )
    return rows


def _lovable(now: dt.datetime) -> Source:
    from app.legacy_import.lovable_sync_models import LovableSyncRun, SyncRunStatus, SyncTrigger

    runs = LovableSyncRun.objects.filter(trigger=SyncTrigger.SCHEDULED)
    attempt = runs.order_by("-started_at").first()
    success = runs.filter(status=SyncRunStatus.SUCCEEDED).order_by("-started_at").first()
    source = Source(
        "lovable",
        STEP_LABELS["lovable"],
        "Iga öö kell 00:00 (Tallinn)",
        OK,
        "",
        attempt.started_at if attempt else None,
        success.started_at if success else None,
    )
    source.state, source.line = _daily(source.last_success, now)
    if attempt is not None and attempt.status != SyncRunStatus.SUCCEEDED:
        source.state = ATTENTION
        source.line = f"Viimane öine käivitus: {attempt.get_status_display()}"
    source.limits.append(
        "Sõltumatu võrdlus Lovable'iga on eraldi kontroll; see leht ei väida kooskõla."
    )
    return source


def sources(now: dt.datetime | None = None) -> list[Source]:
    now = now or timezone.now()
    found: list[Source] = []
    for build in (_lovable, _watch, _sweep, _agenda):
        try:
            found.append(build(now))
        except Exception:
            name = getattr(build, "__name__", "?").lstrip("_")
            found.append(Source(name, name, "", UNKNOWN, "Seisu ei õnnestunud lugeda."))
    try:
        found.extend(_riigikogu(now))
    except Exception:
        found.append(Source("riigikogu", "Riigikogu", "", UNKNOWN, "Seisu ei õnnestunud lugeda."))
    return found


def attention(items: list[Source]) -> list[Source]:
    return [s for s in items if s.state in {ATTENTION, UNKNOWN}]


__all__: list[Any] = ["Night", "Source", "attention", "history", "sources"]
