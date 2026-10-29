"""What changed in Lovable from one night to the next (docs/adr/0192 §11).

Read-only. The page `Uuendused → Lovable` asks two different questions of the
records the nightly synchronisation already keeps, and this module keeps them
apart:

1. **What changed in Lovable?** One night's stored snapshot against the snapshot
   of the nightly run before it — source against source, through the same
   canonical extractors the reconciliation uses (`SOURCE_FIELDS`), so a
   re-spaced sentence or a re-written date is not a change. A held change that
   stays pending for five nights changed in Lovable once, and is shown once.
2. **What did Juristid do with it?** The run's own `LovableSyncDecision` rows.
   "Uuendatud" is said only where an `APPLIED` decision exists: that row is
   written in the same transaction as the write it records, and a Teema whose
   write failed is rolled back whole, row included (§6). "Juristides juba sama"
   is said only where the reconciliation recorded that Juristid converged
   (`ALREADY_CONVERGED`); an operator's resolution is "Lahendatud käsitsi". A
   field the night never compared — its topic was re-numbered or missing, or
   the run stopped before it recorded anything — is "Ei võrreldud", never
   "sama" (audit 2026-10-28 B-01, C-08). A night whose run failed is never the
   baseline the next night is compared with.

Only scheduled runs are nights. A manual run and a `Kontrolli muudatusi` check
are not on this page (Haldus shows them); a manual run's write is still found
and named when it carried a change the next night shows. Nothing here reaches
Lovable, writes a row, or reads a field ADR 0192 excludes: the snapshot never
held `markmed` or `mitteametlik_arvamus`, and only `SOURCE_FIELDS` are compared.

Every Teema passes `Matter.objects.visible_to(reader)`. One that does not is
neither named nor linked nor counted on its own: the night says only how many
it held back. A deleted (tombstoned) Teema is left out entirely, as every other
list leaves it (docs/adr/0096).
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from typing import Any

from django.utils import timezone

from app.legacy_import.lovable_source import SourceError, verify_snapshot
from app.legacy_import.lovable_sync import (
    RESOLVED_PREFIX,
    SNAPSHOT_RETENTION_DAYS,
    SOURCE_FIELDS,
    TALLINN,
    VERIFY_MISMATCH,
    VERIFY_STOPPED,
    is_baseline_run,
    name_key,
    run_reconciled,
)
from app.legacy_import.lovable_sync_labels import LOVABLE_FIELD_LABELS, LOVABLE_REASONS
from app.legacy_import.lovable_sync_models import (
    LovableSyncDecision,
    LovableSyncRun,
    LovableSyncSnapshot,
    LovableSyncState,
    SyncMode,
    SyncOutcome,
    SyncRunStatus,
    SyncTrigger,
)

#: Nights shown before «Näita vanemaid».
DEFAULT_NIGHTS = 7

#: The order a Teema's changed fields are listed in: how a lawyer reads a row.
FIELD_ORDER = (
    "title",
    "owner",
    "stage",
    "next_action_text",
    "next_action_date",
    "opinion_deadline",
    "received",
    "sent",
    "sent_date",
    "addressee",
    "legal_act",
    "koda_initiative",
    "operational_scope",
    "commencements",
    "onenote_link",
)

#: What Juristid did with a change, in the page's words.
APPLIED = "APPLIED"
NOT_APPLIED = "NOT_APPLIED"
REVIEW = "REVIEW"
CONFLICT = "CONFLICT"
SAME = "SAME"
RESOLVED = "RESOLVED"
NOT_COMPARED = "NOT_COMPARED"
LOCAL = "LOCAL"
FAILED = "FAILED"
UNKNOWN = "UNKNOWN"

RESULT_LABELS = {
    APPLIED: "Uuendatud",
    NOT_APPLIED: "Rakendamata",
    REVIEW: "Vajab ülevaatamist",
    CONFLICT: "Vastuolu",
    SAME: "Juristides juba sama",
    RESOLVED: "Lahendatud käsitsi",
    NOT_COMPARED: "Ei võrreldud",
    LOCAL: "Juristides muudetud",
    FAILED: "Viga",
    UNKNOWN: "Teadmata",
}

_FROM_OUTCOME = {
    SyncOutcome.ELIGIBLE: NOT_APPLIED,
    SyncOutcome.REVIEW_REQUIRED: REVIEW,
    SyncOutcome.CONFLICT: CONFLICT,
    SyncOutcome.ALREADY_CONVERGED: SAME,
    SyncOutcome.LOCAL_TEST_CHANGE: LOCAL,
    SyncOutcome.FAILED: FAILED,
}

STATUS_LABELS = {
    SyncRunStatus.SUCCEEDED: "Õnnestus",
    SyncRunStatus.REVIEW_ONLY: "Peatatud: Juristidesse ei kirjutatud",
    SyncRunStatus.FAILED: "Ebaõnnestus",
    SyncRunStatus.RUNNING: "Käib",
}

#: Why an eligible change was not written, by the run's own record.
_HELD_BY_RUN = {
    "CIRCUIT_BREAKER": "Liiga palju muudatusi korraga; öö jäi kirjutamata.",
    "NO_VERIFIED_BACKUP": "Värsket kontrollitud varukoopiat polnud; öö jäi kirjutamata.",
}
COMPARE_ONLY_NOTE = "Võrdlusrežiim: Juristidesse ei kirjutata."
VERIFY_STOPPED_NOTE = (
    "Jäi kirjutamata: selle öö varasema teema tagasilugemine ebaõnnestus ja öö peatati."
)
VERIFY_FAILED_NOTE = "Kirjutati, kuid tagasilugemine ei vastanud kirjutatule; vaata teema üle."
WRITE_FAILED_NOTE = "Teema kirjutamine ebaõnnestus; Juristides ei muudetud midagi."
#: Why a field was not compared, by the topic's own recorded `*` decision.
NOT_COMPARED_NOTES = {
    "IDENTIFIER_CHANGED": "Teema number muutus Lovable'is; välju ei võrreldud.",
    "SOURCE_MISSING": "Teemat polnud Lovable'i lugemises; välju ei võrreldud.",
}
NOT_RECONCILED_NOTE = "Öö katkes enne Juristidega võrdlemist ({code})."
NOT_RECONCILED = (
    "Sünkroonimine katkes pärast Lovable'i lugemist: muudatusi ei võrreldud Juristidega "
    "ja seda lugemist ei kasutata järgmise öö võrdluses."
)
VERIFY_STOPPED_NIGHT = (
    "Öö peatati: kirjutatud teema tagasilugemine ei vastanud kirjutatule, järgmisi teemasid "
    "ei kirjutatud. Seda lugemist ei kasutata järgmise öö võrdluses."
)
UNEXPLAINED_NOTE = "Selle öö võrdlus ei salvestanud selle välja kohta otsust."

NO_CHANGES = "Lovable'is ei tuvastatud muudatusi."
FIRST_READING = "Esimene öine lugemine: see on lähtepunkt. Muudatusi näeb alates järgmisest ööst."
NOT_READ = "Sel ööl Lovable'i andmeid ei loetud, seega ei saa öelda, kas midagi muutus."
EXPIRED = "Eelmise öö andmeid enam ei hoita, seega võrdlust ei saa näidata."
NOT_VERIFIABLE = "Öö salvestatud andmed ei läbinud kontrolli, seega võrdlust ei saa näidata."


# ---------------------------------------------------------------------------
# Values as a reader sees them
# ---------------------------------------------------------------------------


def _day(value: str) -> str:
    try:
        return dt.date.fromisoformat(value).strftime("%d.%m.%Y")
    except ValueError:
        return value


def shown(name: str, value: Any) -> str:
    """One canonical source value as text. Never markup: the template escapes it."""
    if value is None or value == "" or value == []:
        return "—"
    if isinstance(value, bool):
        return "Jah" if value else "Ei"
    if name == "commencements":
        parts = []
        for kind, day, note in value:
            text = " ".join(p for p in (kind, _day(day) if day else "") if p) or "—"
            parts.append(f"{text} ({note})" if note else text)
        return "; ".join(parts)
    text = str(value)
    if text.startswith("?"):
        return text[1:]
    if len(text) == 10 and text[4] == "-" and text[7] == "-":
        return _day(text)
    return text


def _key(name: str, value: Any) -> Any:
    """What two values are compared as: the reconciliation's canonical form, and
    for a name Lovable's own `raw_norm` (case and diacritics are not a change)."""
    if name == "owner":
        return name_key(value)
    return value


def canonical(bundle: dict[str, Any]) -> dict[str, Any]:
    return {name: SOURCE_FIELDS[name](bundle) for name in FIELD_ORDER}


# ---------------------------------------------------------------------------
# The page's shapes
# ---------------------------------------------------------------------------


@dataclass
class FieldChange:
    name: str
    label: str
    before: str
    after: str
    result: str
    result_label: str
    note: str = ""


@dataclass
class TopicChange:
    reference: str
    title: str
    matter_id: Any
    #: CHANGED (fields moved), MISSING (gone from the reading), BACK (in it again).
    kind: str = "CHANGED"
    fields: list[FieldChange] = field(default_factory=list)
    #: Lovable's `nr` before → after, when the topic was re-numbered there.
    renumbered: tuple[str, str] | None = None


@dataclass
class Night:
    run: LovableSyncRun
    started_local: str
    status_label: str
    failed: bool
    mode_note: str
    #: BASELINE, COMPARED, NO_SNAPSHOT (this night read nothing), UNAVAILABLE
    #: (the earlier snapshot is gone or does not verify).
    comparison: str
    message: str = ""
    #: Said above the changes when the run did not reconcile (`run_reconciled`).
    warning: str = ""
    baseline_local: str = ""
    topics: list[TopicChange] = field(default_factory=list)
    hidden: int = 0

    @property
    def changed_topics(self) -> int:
        return len(self.topics)

    @property
    def changed_fields(self) -> int:
        return sum(len(t.fields) for t in self.topics)

    @property
    def applied(self) -> int:
        return sum(1 for t in self.topics for f in t.fields if f.result == APPLIED)

    @property
    def attention(self) -> int:
        return sum(
            1 for t in self.topics for f in t.fields if f.result in (REVIEW, CONFLICT, FAILED)
        ) + sum(1 for t in self.topics if t.kind != "CHANGED" or t.renumbered)


@dataclass
class History:
    nights: list[Night]
    older: int
    showing_all: bool
    retention_days: int = SNAPSHOT_RETENTION_DAYS


# ---------------------------------------------------------------------------
# Building it
# ---------------------------------------------------------------------------


def _local(moment: dt.datetime | None) -> str:
    if moment is None:
        return ""
    return moment.astimezone(TALLINN).strftime("%d.%m.%Y kell %H.%M")


def _document(snapshot: LovableSyncSnapshot | None) -> dict[str, Any] | None:
    if snapshot is None:
        return None
    try:
        verify_snapshot(snapshot.document)
    except SourceError:
        return None
    return snapshot.document


def _elsewhere(run: LovableSyncRun, night: LovableSyncRun) -> str:
    """Where a write or a convergence happened, when not in the night itself."""
    if run.pk == night.pk:
        return ""
    if run.trigger == SyncTrigger.MANUAL:
        return f"Käsitsi käivitusel {_local(run.started_at)}."
    return f"Käivitusel {_local(run.started_at)}."


def _result(
    name: str,
    after: Any,
    *,
    decision: LovableSyncDecision | None,
    topic: LovableSyncDecision | None,
    applied_in: LovableSyncRun | None,
    converged_in: LovableSyncRun | None,
    resolved_at: dt.datetime | None,
    night: LovableSyncRun,
    reconciled: bool,
) -> tuple[str, str]:
    """One changed field's answer, from recorded evidence only.

    ``topic`` is the Teema's own ``*`` decision that night (SOURCE_MISSING,
    IDENTIFIER_CHANGED, FAILED), ``reconciled`` whether the run recorded its
    reconciliation at all. With no evidence the answer is never «sama»."""
    topic_outcome = topic.outcome if topic is not None else ""
    if applied_in is not None:
        if topic is not None and topic.reason == VERIFY_MISMATCH:
            return FAILED, VERIFY_FAILED_NOTE
        return APPLIED, _elsewhere(applied_in, night)
    if resolved_at is not None:
        return RESOLVED, f"Lahendati {_local(resolved_at)}; Juristides jäi oma väärtus."
    if topic_outcome in NOT_COMPARED_NOTES:
        return NOT_COMPARED, NOT_COMPARED_NOTES[topic_outcome]
    if not reconciled:
        code = (night.error or "").split(":")[0] or night.status
        return NOT_COMPARED, NOT_RECONCILED_NOTE.format(code=code)
    if decision is None:
        if converged_in is not None:
            return SAME, _elsewhere(converged_in, night)
        # Not recorded, with no write, convergence or resolution to explain it:
        # the night gives no evidence either way, so it is not said to be «sama».
        return UNKNOWN, UNEXPLAINED_NOTE
    outcome = SyncOutcome(decision.outcome)
    if outcome == SyncOutcome.ELIGIBLE and topic_outcome == SyncOutcome.FAILED:
        return FAILED, WRITE_FAILED_NOTE
    result = _FROM_OUTCOME.get(outcome, UNKNOWN)
    if result == NOT_APPLIED:
        if decision.reason == VERIFY_STOPPED:
            return result, VERIFY_STOPPED_NOTE
        if night.mode == SyncMode.COMPARE:
            return result, COMPARE_ONLY_NOTE
        held = _HELD_BY_RUN.get((night.error or "").split(":")[0])
        return result, held or ""
    return result, LOVABLE_REASONS.get(decision.reason, "")


def _matching(
    rows: list[tuple[Any, Any]], name: str, value: Any, *, after: dt.datetime | None = None
) -> Any:
    """The first recorded (when, source) whose source is ``value``."""
    for when, source in rows:
        moment = when.started_at if isinstance(when, LovableSyncRun) else when
        if after is not None and moment < after:
            continue
        if _key(name, source) == _key(name, value):
            return when
    return None


def _compare(
    night: Night,
    before: dict[str, Any],
    after: dict[str, Any],
    *,
    states: dict[str, LovableSyncState],
    visible: dict[Any, Any],
    live: set[Any],
    decisions: dict[tuple[Any, str], LovableSyncDecision],
    applied: dict[tuple[Any, str], list[tuple[LovableSyncRun, Any]]],
    converged: dict[tuple[Any, str], list[tuple[LovableSyncRun, Any]]],
    resolutions: dict[tuple[Any, str], list[tuple[dt.datetime, Any]]],
) -> None:
    run = night.run
    reconciled = run_reconciled(run)
    for source_id in sorted(set(before) | set(after)):
        state = states.get(source_id)
        if state is None or state.matter_id not in live:
            continue  # not a cohort Teema any more, or deleted in Juristid
        matter = visible.get(state.matter_id)
        old, new = before.get(source_id), after.get(source_id)
        topic = TopicChange(
            reference=state.reference,
            title=matter.title if matter is not None else "",
            matter_id=state.matter_id,
        )
        if old is None or new is None:
            topic.kind = "MISSING" if new is None else "BACK"
        else:
            old_nr, new_nr = str(old["row"].get("nr") or ""), str(new["row"].get("nr") or "")
            if old_nr != new_nr:
                topic.renumbered = (old_nr, new_nr)
            was, now = canonical(old), canonical(new)
            for name in FIELD_ORDER:
                if _key(name, was[name]) == _key(name, now[name]):
                    continue
                result, note = _result(
                    name,
                    now[name],
                    decision=decisions.get((state.pk, name)),
                    topic=decisions.get((state.pk, "*")),
                    applied_in=_matching(applied.get((state.pk, name), []), name, now[name]),
                    converged_in=_matching(converged.get((state.pk, name), []), name, now[name]),
                    resolved_at=_matching(
                        resolutions.get((state.pk, name), []),
                        name,
                        now[name],
                        after=run.started_at,
                    ),
                    night=run,
                    reconciled=reconciled,
                )
                topic.fields.append(
                    FieldChange(
                        name=name,
                        label=LOVABLE_FIELD_LABELS.get(name, name),
                        before=shown(name, was[name]),
                        after=shown(name, now[name]),
                        result=result,
                        result_label=RESULT_LABELS[result],
                        note=note,
                    )
                )
            if not topic.fields and topic.renumbered is None:
                continue
        if matter is None:
            night.hidden += 1
            continue
        night.topics.append(topic)
    night.topics.sort(key=lambda t: _reference_order(t.reference))


def _reference_order(reference: str) -> tuple[int, int, str]:
    year, _, number = reference.partition("_")
    if year.isdigit() and number.isdigit():
        return (-int(year), -int(number), reference)
    return (0, 0, reference)


def nightly_history(
    user: Any, *, show_all: bool = False, now: dt.datetime | None = None
) -> History:
    """The scheduled nights, newest first, each compared with the night before it."""
    from app.matters.models import Matter

    moment = now or timezone.now()
    window = moment - dt.timedelta(days=SNAPSHOT_RETENTION_DAYS)
    scheduled = list(
        LovableSyncRun.objects.filter(trigger=SyncTrigger.SCHEDULED).order_by("-started_at")
    )
    in_window = [r for r in scheduled if r.started_at >= window]
    shown_runs = in_window if show_all else in_window[:DEFAULT_NIGHTS]
    older = len(in_window) - len(shown_runs)
    if not shown_runs:
        return History(nights=[], older=0, showing_all=show_all)

    # Each shown night's baseline: the nearest earlier scheduled run that read
    # Lovable *and did not fail* — a failed run's reading is shown for its own
    # night, but is never what the next night is measured against.
    with_snapshot = set(
        LovableSyncSnapshot.objects.filter(run__in=scheduled).values_list("run_id", flat=True)
    )
    snapshot_runs = {r.pk for r in scheduled if r.pk in with_snapshot and is_baseline_run(r)}
    baseline_of: dict[Any, LovableSyncRun | None] = {}
    for position, run in enumerate(scheduled):
        if run not in shown_runs:
            continue
        baseline_of[run.pk] = next(
            (r for r in scheduled[position + 1 :] if r.pk in snapshot_runs), None
        )
    needed = {r.pk for r in shown_runs} | {b.pk for b in baseline_of.values() if b is not None}
    snapshots = {s.run_id: s for s in LovableSyncSnapshot.objects.filter(run_id__in=needed)}

    states = {str(s.source_id): s for s in LovableSyncState.objects.all()}
    matter_ids = {s.matter_id for s in states.values()}
    live = set(Matter.objects.filter(pk__in=matter_ids).values_list("pk", flat=True))
    visible = {m.pk: m for m in Matter.objects.visible_to(user).filter(pk__in=matter_ids)}
    decisions: dict[Any, dict[tuple[Any, str], LovableSyncDecision]] = {
        r.pk: {} for r in shown_runs
    }
    for d in (
        LovableSyncDecision.objects.filter(run__in=shown_runs)
        .exclude(outcome=SyncOutcome.APPLIED)
        .exclude(reason__startswith=RESOLVED_PREFIX)
    ):
        decisions[d.run_id][(d.state_id, d.field)] = d
    # An operator's resolution (`lovable_sync resolve`) is recorded on whichever
    # run last observed the field; it answers for the night the change appeared.
    resolutions: dict[tuple[Any, str], list[tuple[dt.datetime, Any]]] = {}
    for d in LovableSyncDecision.objects.filter(
        outcome=SyncOutcome.ALREADY_CONVERGED, reason__startswith=RESOLVED_PREFIX
    ).order_by("created_at"):
        resolutions.setdefault((d.state_id, d.field), []).append((d.created_at, d.source))
    oldest = min(
        [b.started_at for b in baseline_of.values() if b is not None]
        + [r.started_at for r in shown_runs]
    )
    applied_rows = (
        LovableSyncDecision.objects.filter(outcome=SyncOutcome.APPLIED, run__started_at__gt=oldest)
        .exclude(run__trigger=SyncTrigger.CHECK)
        .select_related("run")
        .order_by("run__started_at")
    )
    converged_rows = (
        LovableSyncDecision.objects.filter(
            outcome=SyncOutcome.ALREADY_CONVERGED, run__started_at__gt=oldest
        )
        .exclude(run__trigger=SyncTrigger.CHECK)
        .exclude(reason__startswith=RESOLVED_PREFIX)
        .select_related("run")
        .order_by("run__started_at")
    )

    nights = []
    for run in shown_runs:
        night = Night(
            run=run,
            started_local=_local(run.started_at),
            status_label=STATUS_LABELS.get(SyncRunStatus(run.status), run.status),
            failed=run.status == SyncRunStatus.FAILED,
            mode_note=COMPARE_ONLY_NOTE if run.mode == SyncMode.COMPARE else "",
            comparison="COMPARED",
        )
        nights.append(night)
        stored = snapshots.get(run.pk)
        after = _document(stored)
        if after is None:
            night.comparison = "NO_SNAPSHOT"
            night.message = NOT_READ if stored is None else NOT_VERIFIABLE
            continue
        baseline = baseline_of.get(run.pk)
        if baseline is None:
            earlier = [r for r in scheduled if r.started_at < run.started_at]
            if any(r.started_at < window for r in earlier):
                # An earlier night existed beyond the retention: its reading is gone.
                night.comparison = "UNAVAILABLE"
                night.message = EXPIRED
            else:
                # No earlier night ever read Lovable: this one is the starting point.
                night.comparison = "BASELINE"
                night.message = FIRST_READING
            continue
        before = _document(snapshots.get(baseline.pk))
        night.baseline_local = _local(baseline.started_at)
        if before is None:
            night.comparison = "UNAVAILABLE"
            night.message = NOT_VERIFIABLE
            continue
        window_applied: dict[tuple[Any, str], list[tuple[LovableSyncRun, Any]]] = {}
        for row in applied_rows:
            if baseline.started_at < row.run.started_at <= run.started_at:
                window_applied.setdefault((row.state_id, row.field), []).append(
                    (row.run, row.source)
                )
        window_converged: dict[tuple[Any, str], list[tuple[LovableSyncRun, Any]]] = {}
        for row in converged_rows:
            if baseline.started_at < row.run.started_at <= run.started_at:
                window_converged.setdefault((row.state_id, row.field), []).append(
                    (row.run, row.source)
                )
        if not run_reconciled(run):
            night.warning = NOT_RECONCILED
        elif (run.error or "").startswith("VERIFY"):
            night.warning = VERIFY_STOPPED_NIGHT
        _compare(
            night,
            before.get("cohort") or {},
            after.get("cohort") or {},
            states=states,
            visible=visible,
            live=live,
            decisions=decisions[run.pk],
            applied=window_applied,
            converged=window_converged,
            resolutions=resolutions,
        )
        if not night.topics and not night.hidden:
            night.message = NO_CHANGES
    return History(nights=nights, older=older, showing_all=show_all)
