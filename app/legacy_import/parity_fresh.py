"""Operator-triggered, fresh-read parity (r98 final, ADR 0238 §fresh).

The stored-snapshot verification (`parity.verifier`) is the default and adds no Lovable
traffic. This module is the explicit second mode: it takes **one new consistent reading** of
Lovable through the same read-only, allowlisted, bounded access the nightly synchronisation
uses (`lovable_source`: GET only, the service login with no staff rights, a fixed table and
column allowlist, a response cap, whole-or-nothing paging, an index-cohort-index-cohort
consistency check retried a bounded number of times) and compares it with Juristid using the
independent oracle. It never calls the synchronisation planner, writes no Teema and nothing to
Lovable, and is **only ever started by a person** (`manage.py lovable_parity --fresh`): no
page load, no schedule, no hook calls it.

* **Snapshot parity vs current-source parity.** The report says which it is
  (`LovableParityReport.source`). A fresh reading is compared with the reading the last
  synchronisation stored, so a Teema edited in Lovable *since* is reported as a legitimate
  source change (CHANGED_SINCE_SYNC, new, gone, reopened, closed), not as a defect.
* **A reading that cannot be made consistent** (Lovable kept changing, an HTTP refusal, a
  gateway error that did not clear) leaves the comparison UNVERIFIED with a code; it is never
  stitched together from parts and never shown as a match.
* **A synchronisation running or finishing during the comparison** changes Juristid under it:
  the comparison is then UNVERIFIED (`SYNC_DURING_COMPARISON`).
"""

from __future__ import annotations

import datetime as dt
import re
from collections.abc import Callable
from typing import Any
from zoneinfo import ZoneInfo

from django.db.models import Count, Max

from app.legacy_import import lovable_source
from app.legacy_import.lovable_sync_models import (
    LovableParityReport,
    LovableSyncRun,
    LovableSyncSnapshot,
    LovableSyncState,
    SyncRunStatus,
    SyncTrigger,
)
from app.legacy_import.parity import verifier

TALLINN = ZoneInfo("Europe/Tallinn")
MAPPING_VERSION = "parity-fresh"
#: The most Teemad one fresh comparison may read: a bound on requests, not a business rule.
MAX_TEEMAD = 5000

_ERROR_CODE = re.compile(r"^[A-Z0-9_]{1,40}$")


def _fingerprint() -> tuple[Any, ...]:
    """What a synchronisation changing Juristid underneath the comparison would move."""
    runs = LovableSyncRun.objects.aggregate(n=Count("id"), last=Max("updated_at"))
    states = LovableSyncState.objects.aggregate(n=Count("id"), last=Max("updated_at"))
    return (
        runs["n"],
        runs["last"],
        states["n"],
        states["last"],
        LovableSyncRun.objects.filter(status=SyncRunStatus.RUNNING).exists(),
    )


def stored_reading() -> tuple[LovableSyncRun, verifier.SourceView] | None:
    run = (
        LovableSyncRun.objects.filter(trigger=SyncTrigger.SCHEDULED, snapshot__isnull=False)
        .order_by("-started_at")
        .first()
    )
    snapshot = LovableSyncSnapshot.objects.filter(run=run).first() if run else None
    if run is None or snapshot is None:
        return None
    return run, verifier.view_from_snapshot(snapshot.document, snapshot.taken_at, snapshot.sha256)


def _failed(
    code: str, *, mode: str, today: dt.date, eligible: int, sample: int | None, seed: str
) -> verifier.Report:
    """A comparison that could not be made: everything it would have compared is UNVERIFIED."""
    counts = dict.fromkeys(verifier.CLASSES, 0)
    counts[verifier.UNVERIFIED] = eligible
    return verifier.Report(
        mode=mode,
        today=today,
        taken_at=None,
        sha256="",
        teemad=0,
        counts=counts,
        mismatches=[],
        held=[],
        unverified=[],
        explained=[],
        source="fresh",
        eligible=eligible,
        sample_size=sample,
        seed=seed,
        error=code,
    )


def run_fresh(
    transport: Any,
    *,
    now: Callable[[], dt.datetime],
    sample: int = 0,
    seed: str = "juristid",
    record: bool = True,
    pause: Callable[[float], None] | None = None,
) -> verifier.Report:
    """One fresh-read verification. Reads Lovable (GET only) and Juristid; writes only the
    report row (`record=True`): counts and (reference, field) pairs, never a value."""
    import time

    moment = now()
    today = moment.astimezone(TALLINN).date()
    known = stored_reading()
    stored_run, stored_view = known if known else (None, None)
    states = {str(s.source_id): s.reference for s in LovableSyncState.objects.all()}
    mode = "sample" if sample else "full"
    before = _fingerprint()
    if before[-1]:
        report = _failed(
            "SYNC_RUNNING",
            mode=mode,
            today=today,
            eligible=len(states),
            sample=sample or None,
            seed=seed,
        )
        return _keep(report, stored_run, moment) if record else report
    try:
        index = lovable_source.read_index(transport)
    except lovable_source.SourceError as error:
        report = _failed(
            _code(error),
            mode=mode,
            today=today,
            eligible=len(states),
            sample=sample or None,
            seed=seed,
        )
        return _keep(report, stored_run, moment) if record else report
    operational_new = {
        str(row["id"]): str(row["nr"])
        for row in index
        if row.get("operatiivne_ulatus") and str(row["id"]) not in states and row.get("nr")
    }
    population = {**states, **operational_new}  # source id -> reference
    if len(population) > MAX_TEEMAD:
        report = _failed(
            "TOO_MANY",
            mode=mode,
            today=today,
            eligible=len(population),
            sample=sample or None,
            seed=seed,
        )
        return _keep(report, stored_run, moment) if record else report
    references: list[str] | None = None
    wanted_ids = sorted(population)
    if sample:
        references = verifier.sample_of(sorted(set(population.values())), sample, seed)
        chosen = set(references)
        wanted_ids = sorted(i for i, ref in population.items() if ref in chosen)
    try:
        document = lovable_source.take_snapshot(
            transport,
            wanted_ids,
            mapping_version=MAPPING_VERSION,
            pause=pause or time.sleep,
        )
        lovable_source.verify_snapshot(document)
    except lovable_source.SourceError as error:
        report = _failed(
            _code(error),
            mode=mode,
            today=today,
            eligible=len(wanted_ids),
            sample=sample or None,
            seed=seed,
        )
        return _keep(report, stored_run, moment) if record else report
    taken = dt.datetime.fromisoformat(document["taken_at"])
    view = verifier.view_from_snapshot(document, taken, document.get("sha256", ""))
    report = verifier.verify(
        view, today=today, references=references, mode=mode, stored=stored_view, source="fresh"
    )
    report.eligible = len(set(population.values()))
    report.sample_size = sample or None
    report.seed = seed if sample else ""
    if _fingerprint() != before:
        # Juristid moved while it was being compared: nothing of it is evidence.
        report = _failed(
            "SYNC_DURING_COMPARISON",
            mode=mode,
            today=today,
            eligible=report.eligible,
            sample=sample or None,
            seed=seed,
        )
    return _keep(report, stored_run, moment, document) if record else report


def _code(error: lovable_source.SourceError) -> str:
    code = str(getattr(error, "code", "") or "SOURCE_ERROR").upper()
    return code if _ERROR_CODE.match(code) else "SOURCE_ERROR"


def _keep(
    report: verifier.Report,
    stored_run: LovableSyncRun | None,
    at: dt.datetime,
    document: dict[str, Any] | None = None,
) -> verifier.Report:
    LovableParityReport.objects.create(
        at=at,
        mode=report.mode,
        source=report.source,
        snapshot_taken_at=report.taken_at,
        snapshot_sha256=report.sha256,
        run_key=stored_run.run_key if stored_run else "",
        teemad=report.teemad,
        sample_size=report.sample_size,
        eligible=report.eligible,
        seed=report.seed,
        counts=report.counts,
        mismatches=[list(p) for p in report.mismatches],
        held=[list(p) for p in report.held],
        unverified=[list(p) for p in report.unverified],
        changed_since_sync=[list(p) for p in report.changed_since_sync],
        source_changes=report.source_changes,
        error=report.error,
    )
    return report
