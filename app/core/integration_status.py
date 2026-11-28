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

#: What one expected cycle came to. «missing» = a cycle was due and left no record at all;
#: «pending» = it has not been due yet; «running» = inside its window with steps still due.
RUNNING, COMPLETED, PARTIAL, FAILED, MISSING, DISABLED, PENDING = (
    "running",
    "completed",
    "partial",
    "failed",
    "missing",
    "disabled",
    "pending",
)
CYCLE_LABELS = {
    RUNNING: "Käib",
    COMPLETED: "Lõpetatud",
    PARTIAL: "Osaline",
    FAILED: "Ebaõnnestunud",
    MISSING: "Tsükkel puudub täielikult",
    DISABLED: "Tahtlikult vahele jäetud",
    PENDING: "Pole veel aeg",
}
_FAILURES = frozenset(
    {nightly_outcome.FAILED, nightly_outcome.INTERRUPTED, nightly_outcome.SKIPPED_UNEXPECTED}
)


@dataclass
class NightStep:
    step: str
    label: str
    record: NightlyStepRecord | None
    expected_by: dt.datetime
    #: «no record» for a step that was due on a night the application has records for.
    missing: bool

    @property
    def problem(self) -> bool:
        return self.record is not None and self.record.outcome in nightly_outcome.PROBLEMS


@dataclass
class Night:
    """One expected cycle (a Tallinn midnight), whether or not it left records."""

    start: dt.datetime
    steps: list[NightStep]
    state: str = PENDING
    #: A person's recovery runs for this night: shown beside the scheduled verdict, never over it.
    recoveries: list[NightlyStepRecord] = field(default_factory=list)
    #: Approximate: model requests started inside the cycle's window (the ledger has no cycle id).
    ai_operations: int = 0
    ai_failed: int = 0
    rk_cycle: str = ""

    @property
    def day(self) -> dt.date:
        return self.start.astimezone(nightly_schedule.TALLINN).date()

    @property
    def label(self) -> str:
        return CYCLE_LABELS[self.state]

    @property
    def problems(self) -> int:
        return sum(1 for s in self.steps if s.problem or s.missing)


@dataclass
class History:
    nights: list[Night]
    #: The first night the application kept records for; nothing before it is reconstructed.
    recording_since: dt.datetime | None
    next_start: dt.datetime


def _slot_day(cycle_start: dt.datetime) -> dt.date:
    """The Tallinn date of the cycle a start belongs to (a start a little before midnight is
    still that midnight's cycle)."""
    return (cycle_start.astimezone(nightly_schedule.TALLINN) + dt.timedelta(hours=6)).date()


def _state(
    rows: dict[str, NightlyStepRecord], steps: list[NightStep], start: dt.datetime, now: dt.datetime
) -> str:
    window_open = now < nightly_schedule.after_start(start, nightly_schedule.CYCLE_COMPLETE_BY)
    if not rows:
        if now < start:
            return PENDING
        return RUNNING if window_open else MISSING
    if window_open and any(s.record is None for s in steps):
        return RUNNING
    outcomes = {r.outcome for r in rows.values()}
    if len(rows) == len(steps) and outcomes == {nightly_outcome.SKIPPED_DELIBERATE}:
        return DISABLED
    if outcomes & _FAILURES:
        return FAILED
    if nightly_outcome.PARTIAL in outcomes or any(s.record is None for s in steps):
        return PARTIAL
    return COMPLETED


def history(nights: int = 30, now: dt.datetime | None = None) -> History:
    """The last ``nights`` EXPECTED cycles since records began, newest first.

    Each night is a Tallinn midnight; a night that left no record is shown as such (state
    «missing»), not skipped. The scheduled verdict of a step is the cycle script's own; a
    person's recovery run is kept apart (`Night.recoveries`). Nothing before the first
    record is reconstructed. AI operations are matched by **time window**, approximately.
    """
    from app.ai_usage.models import AiOperation, Outcome
    from app.riigikogu.models import StageRun

    now = now or timezone.now()
    first = (
        NightlyStepRecord.objects.order_by("cycle_start")
        .values_list("cycle_start", flat=True)
        .first()
    )
    next_start = nightly_schedule.next_cycle_start(now)
    if first is None:
        return History([], None, next_start)
    first_day = _slot_day(first)
    today = now.astimezone(nightly_schedule.TALLINN).date()
    days = [first_day + dt.timedelta(days=n) for n in range((today - first_day).days + 1)]
    days = [d for d in days if nightly_schedule.cycle_start_on(d) <= now][-nights:]
    if not days:
        return History([], first, next_start)
    lo = nightly_schedule.cycle_start_on(days[0]) - dt.timedelta(hours=6)
    scheduled: dict[dt.date, dict[str, NightlyStepRecord]] = {}
    recoveries: dict[dt.date, list[NightlyStepRecord]] = {}
    for row in NightlyStepRecord.objects.filter(cycle_start__gte=lo).order_by("cycle_start"):
        day = _slot_day(row.cycle_start)
        if row.manual:
            recoveries.setdefault(day, []).append(row)
        else:
            scheduled.setdefault(day, {})[row.step] = row
    found: list[Night] = []
    for day in reversed(days):
        slot = nightly_schedule.cycle_start_on(day)
        rows = scheduled.get(day, {})
        start = min((r.cycle_start for r in rows.values()), default=slot)
        steps = []
        for step in nightly_outcome.STEPS:
            due = nightly_schedule.after_start(start, nightly_schedule.FINISH_BY[step])
            record = rows.get(step)
            steps.append(
                NightStep(
                    step,
                    STEP_LABELS[step],
                    record,
                    due,
                    record is None and due < now and bool(rows),
                )
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
                state=_state(rows, steps, start, now),
                recoveries=recoveries.get(day, []),
                ai_operations=ops.count(),
                ai_failed=ops.exclude(
                    outcome__in=(Outcome.SUCCEEDED, Outcome.SUCCEEDED_AFTER_RETRY)
                )
                .exclude(outcome=Outcome.IN_PROGRESS)
                .count(),
                rk_cycle=str(cycle)[:8] if cycle else "",
            )
        )
    return History(found, first, next_start)


# -- per-source freshness -------------------------------------------------------------------------


@dataclass
class Source:
    key: str
    label: str
    cadence: str
    state: str
    line: str
    last_attempt: dt.datetime | None = None
    #: The last check with NO source error and nothing cut short: the only one that makes a
    #: source «fresh». A run that finished with read errors or partial coverage is a
    #: completed run (`last_completed`), not a complete check.
    last_success: dt.datetime | None = None
    #: The newest publication seen at the source and the newest stored here (None = not tracked).
    observed: str = ""
    stored: str = ""
    pending: list[str] = field(default_factory=list)
    limits: list[str] = field(default_factory=list)
    #: The last run that FINISHED, errors or not (see `last_success`). Keyword-only in effect:
    #: it follows the lists so the positional arguments of every `Source(...)` are unchanged.
    last_completed: dt.datetime | None = None


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
        WatchRun.objects.filter(finished_at__isnull=False, errors=[])
        .order_by("-finished_at")
        .first()
    )
    completed = (
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
        last_completed=completed.finished_at if completed else None,
    )
    if not getattr(settings, "REGISTER_WATCH_FETCH", False):
        source.state, source.line = DISABLED, "Välja lülitatud"
        return source
    health = watch_health(now)
    if not health.ok:
        source.state, source.line = ATTENTION, health.problem or health.detail
    else:
        source.state, source.line = _daily(source.last_success, now)
        if completed is not None and completed.errors:
            source.state = ATTENTION
            n = len(completed.errors)
            source.line = f"Viimane käivitus lõppes {n} lugemisveaga: kontroll oli osaline"
    unjudged = WatchFinding.objects.filter(
        status=FindingStatus.PENDING, relevance=Relevance.UNJUDGED
    ).count()
    source.pending.append(f"{unjudged} pakkumist ootab otsust ja on mudeli poolt hindamata")
    if attempt is not None and attempt.errors:
        source.pending.append(f"viimasel käivitusel {len(attempt.errors)} lugemisviga")
    source.limits.append("Allikate avaldamiskuupäeva ei jälgita; vaadatakse viimast lugemist.")
    source.limits.append(
        "Värskus loeb ainult vigadeta käivitust; allikate kaupa katvust käivitus ei salvesta, "
        "vaid vigade loendi."
    )
    return source


def _sweep(now: dt.datetime) -> Source:
    from django.conf import settings

    from app.register_watch.models import CandidateStatus, Strength, SweepRun, TopicCandidate

    attempt = SweepRun.objects.order_by("-started_at").first()
    success = (
        SweepRun.objects.filter(finished_at__isnull=False, errors=[])
        .order_by("-finished_at")
        .first()
    )
    completed = (
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
        last_completed=completed.finished_at if completed else None,
    )
    if not getattr(settings, "REGISTER_SWEEP", False):
        source.state, source.line = DISABLED, "Välja lülitatud"
        return source
    source.state, source.line = _daily(source.last_success, now)
    if completed is not None and completed.errors:
        source.state = ATTENTION
        source.line = (
            f"Viimane käivitus lõppes {len(completed.errors)} lugemisveaga: kontroll oli osaline"
        )
    source.limits.append(
        "Värskus loeb ainult vigadeta käivitust; allikate kaupa katvust käivitus ei salvesta, "
        "vaid vigade loendi."
    )
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


def _backup(now: dt.datetime) -> Source:
    """What the application itself knows of backups: the set each night's sync took, and
    whether it verified to level 2 (`LovableSyncRun.backup_set`, `.verified`).

    Confidentiality (who can read the files) and the off-host copy are facts of the host, not
    of the database: the page says so and points to the read-only assessment script.
    """
    from app.legacy_import.lovable_sync_models import LovableSyncRun, SyncTrigger

    runs = LovableSyncRun.objects.filter(trigger=SyncTrigger.SCHEDULED).exclude(backup_set="")
    latest = runs.order_by("-started_at").first()
    source = Source(
        "backup",
        "Varukoopia (öise sünkroonimise komplekt)",
        "Iga öö enne sünkroonimist; ööpäevane kohalik komplekt",
        UNKNOWN,
        "Komplekti pole veel salvestatud.",
        latest.started_at if latest else None,
        runs.filter(verified=True)
        .order_by("-started_at")
        .values_list("started_at", flat=True)
        .first(),
    )
    if latest is not None:
        source.state, source.line = _daily(source.last_success, now)
        if not latest.verified:
            source.state, source.line = ATTENTION, "Viimane komplekt ei ole 2. tasemel kinnitatud"
    source.limits.append(
        "Konfidentsiaalsus (õigused) ja väline koopia on hosti näitajad: "
        "scripts/deploy/juristid-backup-assess.sh (ainult loeb). Väline koopia: puudub."
    )
    return source


def sources(now: dt.datetime | None = None) -> list[Source]:
    now = now or timezone.now()
    found: list[Source] = []
    for build in (_lovable, _backup, _watch, _sweep, _agenda):
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


__all__: list[Any] = ["History", "Night", "Source", "attention", "history", "sources"]
