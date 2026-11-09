"""Haldus → Andmete sünkroonimine: the page's read model, its requests and their runner.

Three parts, kept apart:

* **the read model** (`lovable_panel`, `onenote_panel`, `differences`,
  `history`) — counts and times read from the records the engines already keep
  (`LovableSyncRun`, `LovableSyncDecision`, the read service
  `lovable_observability`, Agent A's inventory and Agent D's OneNote coverage
  where they exist). Reads only: it never calls `lovable_sync.control()`,
  `sync_health()` or `due()`, which create the control row, so a GET or HEAD of
  the page writes nothing. Every count is taken from the rows it counts, so the
  page agrees with the ledger;
* **the request API** (`request_lovable_sync`, `request_onenote_verify`,
  `request_onenote_import`, `resolve_difference`) — what the page's POSTs call.
  Each records a `DataSyncRequest` (or the engine's own resolution) and one
  audit row. None of them reaches Lovable or reads a capture: the web process
  only records what was asked;
* **the runner** (`run_next`) — what `manage.py data_sync_requests run-next`
  calls on the host: claims the oldest PENDING request of a lane under
  ``select_for_update(skip_locked=True)``, runs the same engine the nightly job
  runs (`lovable_sync.run_sync(trigger=MANUAL)` with a fresh source read, or the
  OneNote capture check/import), and records the real result.

Business content: a pending difference is named only for a Teema the reader may
open (`Matter.objects.visible_to`); the others are counted, never named. Nothing
here stores or shows a title, a value or a file name in a request's result.
"""

from __future__ import annotations

import datetime as dt
import inspect
import os
import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast

from django.conf import settings
from django.core.paginator import Paginator
from django.db import DatabaseError, IntegrityError, transaction
from django.db.models import Count
from django.utils import timezone

from app.audit.enums import SecurityEventType
from app.audit.services import record_security_event
from app.core.errors import DomainError
from app.legacy_import.lovable_sync_labels import LOVABLE_FIELD_LABELS, LOVABLE_REASONS
from app.legacy_import.lovable_sync_models import (
    LovableSyncControl,
    LovableSyncDecision,
    LovableSyncRun,
    SyncMode,
    SyncOutcome,
    SyncRunStatus,
    SyncTrigger,
)
from app.legacy_import.sync_requests_models import (
    OPEN_STATUSES,
    DataSyncRequest,
    SyncRequestKind,
    SyncRequestLane,
    SyncRequestStatus,
)

#: A PENDING request nobody claimed for this long is marked EXPIRED.
PENDING_EXPIRES_AFTER = dt.timedelta(hours=2)
#: A RUNNING request whose runner has been silent for this long is marked FAILED.
RUNNING_LOST_AFTER = dt.timedelta(hours=6)
#: Runs per page of «Vaata ajalugu».
HISTORY_PAGE_SIZE = 20
#: Requests listed beside each panel.
RECENT_REQUESTS = 5

_CAPTURE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,119}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")

# -- refusals (the page shows these words; tests name them) -------------------------

NEEDS_REASON = "Kirjuta lühidalt, miks seda praegu vaja on."
LANE_BUSY = "Üks taotlus selles reas juba ootab või käib; uus saab esitada, kui see on lõppenud."
SYNC_PAUSED = "Lovable'i sünkroonimine on peatatud (režiim «Peatatud»); käivitust ei saa taotleda."
SYNC_CUT_OVER = "Lõplik üleminek on tehtud: Lovable'i sünkroonimine ei käivitu enam."
MODE_CHANGED = "Sünkroonimise režiim muutus pärast kinnituse avamist. Vaata kinnitus uuesti üle."
BAD_CAPTURE_NAME = (
    "Kogumi nimi peab olema üks kaustanimi seadistatud lähtekaustas "
    "(tähed, numbrid, punkt, sidekriips, alakriips)."
)
NOT_VERIFIED = "Seda kogumit pole edukalt kontrollitud; importida saab ainult kontrollitud kogumit."
PLAN_CHANGED = (
    "Impordiplaan on pärast kontrolli muutunud või kontrollitud on uuemat seisu. "
    "Kontrolli kogumit uuesti ja impordi siis."
)
NOT_RESOLVABLE = (
    "Seda erinevust ei saa siin otsustada (teema pole sulle avatud või otsus on juba tehtud)."
)
DIFFERENCES_UNAVAILABLE = "Erinevuste otsustamine pole selles versioonis saadaval."


class SyncRequestRefused(DomainError):
    """A request the page may not record. ``code`` goes to the audit row."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


# ---------------------------------------------------------------------------
# Estonian words for codes
# ---------------------------------------------------------------------------

#: Why a run or a request did not finish as asked: the ledger's code in the page's words.
FAILURE_REASONS: dict[str, str] = {
    "COHORT_SIZE": "Sünkroonitavate teemade arv ei vastanud oodatule; midagi ei kirjutatud.",
    "COHORT_MISSING": "Liiga palju sünkroonitavaid teemasid puudus lähteandmetest.",
    "INCONSISTENT": "Lovable'i andmed muutusid lugemise ajal; järjepidevat seisu ei saadud.",
    "NO_SOURCE": "Lähteühendust polnud (volitused puudu või vigased).",
    "NO_SNAPSHOT": "Võrdluseks polnud ühtegi kontrollitud lähteseisu.",
    "PAUSED": "Sünkroonimine oli peatatud.",
    "CUT_OVER": "Lõplik üleminek on tehtud; sünkroonimine ei käivitu enam.",
    "DUPLICATE": "Sama käivitus oli juba olemas.",
    "LOCKED": "Teine sünkroonimine käis samal ajal.",
    "CHECK_TOO_SOON": "Kontroll tehti vähem kui minut tagasi.",
    "SIGN_IN_FAILED": "Lovable'i sisselogimine ebaõnnestus.",
    "TOO_MANY_ROWS": "Lovable andis oodatust rohkem ridu.",
    "RESPONSE_SHAPE": "Lovable'i vastus polnud oodatud kujul.",
    "RESPONSE_NOT_JSON": "Lovable'i vastus polnud loetav.",
    "RESPONSE_TOO_LARGE": "Lovable'i vastus oli liiga suur.",
    "SNAPSHOT_DIGEST": "Salvestatud lähteseisu kontrollsumma ei klappinud.",
    "SNAPSHOT_FORMAT": "Salvestatud lähteseis polnud oodatud kujul.",
    "INTERNAL": "Juristide sisemine viga; vaata serveri logi.",
    "EXPIRED_UNCLAIMED": "Serveri käivitaja ei võtnud taotlust kahe tunni jooksul üles.",
    "RUNNER_LOST": "Serveri käivitaja ei lõpetanud taotlust kuue tunni jooksul.",
    "NO_CAPTURE_ROOT": "OneNote'i kogumite lähtekaust pole serveris seadistatud.",
    "CAPTURE_NOT_FOUND": "Sellenimelist kogumit lähtekaustas pole.",
    "NOT_AVAILABLE": "See toiming pole selles versioonis veel saadaval.",
    "NOT_VERIFIED": "Kogumi kontroll ei läbinud.",
    "PLAN_CHANGED": "Impordiplaan oli pärast kontrolli muutunud; midagi ei imporditud.",
    "CAPTURE_ERROR": "Kogumit ei saanud lugeda või see ei läbinud kontrolli.",
    "IMPORT_FAILED": "Import katkes; juba kirjutatud lehed jäid, katkenud leht võeti tagasi.",
}


def failure_reason(code: str) -> str:
    code = (code or "").split(":")[0].strip()
    if not code:
        return ""
    if code.startswith("HTTP_"):
        return f"Lovable vastas veaga ({code.removeprefix('HTTP_')})."
    return FAILURE_REASONS.get(code, f"Tõrge (kood {code}).")


# ---------------------------------------------------------------------------
# Small readers
# ---------------------------------------------------------------------------


def _control() -> LovableSyncControl | None:
    """The switch row as it is — never created here (`lovable_sync.control` would)."""
    return LovableSyncControl.objects.filter(pk=1).first()


def current_mode() -> str:
    ctl = _control()
    if ctl is None:
        return SyncMode.OFF
    return SyncMode.OFF if ctl.cut_over else str(ctl.mode)


def int_counts(raw: Any) -> dict[str, int]:
    """The integer counts of a mapping, nothing else (no text can leak through)."""
    if not isinstance(raw, dict):
        return {}
    return {
        str(k): int(v)
        for k, v in raw.items()
        if isinstance(v, int) and not isinstance(v, bool) and re.fullmatch(r"[a-z][a-z0-9_]*", k)
    }


def code_counts(raw: Any) -> dict[str, int]:
    """`int_counts` keyed by codes (``EXACT``, ``IMPORTED``): upper case allowed."""
    if not isinstance(raw, dict):
        return {}
    return {
        str(k): int(v)
        for k, v in raw.items()
        if isinstance(v, int)
        and not isinstance(v, bool)
        and re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", str(k))
    }


def _as_mapping(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, dict):
        return value
    for name in ("as_dict", "summary", "counts"):
        attr = getattr(value, name, None)
        if callable(attr):
            try:
                got = attr()
            except TypeError:
                continue
            if isinstance(got, dict):
                return got
        elif isinstance(attr, dict):
            return attr
    return dict(getattr(value, "__dict__", {}) or {})


def _attr(item: Any, *names: str, default: Any = None) -> Any:
    for name in names:
        if isinstance(item, dict) and name in item:
            return item[name]
        if hasattr(item, name):
            return getattr(item, name)
    return default


def _shown(value: Any) -> str:
    """A value as the page shows it: «—» for nothing, words for yes/no."""
    if value is None or value == "":
        return "—"
    if isinstance(value, bool):
        return "jah" if value else "ei"
    if isinstance(value, dict):
        parts = [str(v) for k, v in value.items() if k != "id" and v not in (None, "")]
        return " · ".join(parts) or "—"
    if isinstance(value, list | tuple):
        return "; ".join(_shown(v) for v in value) or "—"
    return str(value)


@dataclass(frozen=True)
class RequestView:
    """One request, in the page's words."""

    request: DataSyncRequest
    kind_label: str
    status_label: str
    status_tone: str  # ok | waiting | problem
    reason_text: str
    #: A PENDING request past its expiry: shown as stale; the next request expires it.
    stale: bool
    expires_at: dt.datetime | None
    counts: dict[str, int]


def _tone(status: str, stale: bool) -> str:
    if status == SyncRequestStatus.DONE:
        return "ok"
    if status in (SyncRequestStatus.PENDING, SyncRequestStatus.RUNNING) and not stale:
        return "waiting"
    return "problem"


def request_view(item: DataSyncRequest, *, now: dt.datetime | None = None) -> RequestView:
    moment = now or timezone.now()
    expires = (
        item.requested_at + PENDING_EXPIRES_AFTER
        if item.status == SyncRequestStatus.PENDING
        else None
    )
    stale = expires is not None and moment > expires
    result = item.result if isinstance(item.result, dict) else {}
    return RequestView(
        request=item,
        kind_label=SyncRequestKind(item.kind).label,
        status_label="Aegunud (ootab märkimist)" if stale else SyncRequestStatus(item.status).label,
        status_tone=_tone(item.status, stale),
        reason_text=failure_reason(item.outcome_code),
        stale=stale,
        expires_at=expires,
        counts=int_counts(result.get("counts")),
    )


def _recent(lane: str, now: dt.datetime) -> list[RequestView]:
    rows = DataSyncRequest.objects.filter(lane=lane).select_related("requested_by", "sync_run")
    return [request_view(r, now=now) for r in rows.order_by("-requested_at")[:RECENT_REQUESTS]]


def open_request(lane: str) -> DataSyncRequest | None:
    return (
        DataSyncRequest.objects.filter(lane=lane, status__in=OPEN_STATUSES)
        .order_by("-requested_at")
        .first()
    )


# ---------------------------------------------------------------------------
# Lovable panel
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CoverageRow:
    label: str
    total: int
    operational: int
    historical: int


@dataclass
class Coverage:
    """Agent A's newest inventory, in counts (interface 2): `LovableInventoryRun.counts`
    as `Ledger.counts()` builds it — ``rows``, ``operational``, ``historical`` and
    ``by_category`` / ``by_category_operational`` / ``by_category_historical``."""

    available: bool
    note: str = ""
    taken_at: dt.datetime | None = None
    #: Ledger rows (every source topic plus Juristid-only rows).
    total: int | None = None
    operational: int | None = None
    historical: int | None = None
    rows: list[CoverageRow] = field(default_factory=list)


INVENTORY_MISSING = "Inventuuri pole veel tehtud."


def inventory_coverage() -> Coverage:
    """`lovable_inventory.latest_inventory()` in counts, or «not yet» when there is none."""
    from app.legacy_import import lovable_inventory

    try:
        with transaction.atomic():
            run = lovable_inventory.latest_inventory()
    except DatabaseError:
        return Coverage(available=False, note=INVENTORY_MISSING)
    if run is None:
        return Coverage(available=False, note=INVENTORY_MISSING)
    counts = run.counts if isinstance(run.counts, dict) else {}
    every = code_counts(counts.get("by_category"))
    operational = code_counts(counts.get("by_category_operational"))
    historical = code_counts(counts.get("by_category_historical"))
    rows = [
        CoverageRow(
            label=str(label),
            total=every.get(value, 0),
            operational=operational.get(value, 0),
            historical=historical.get(value, 0),
        )
        for value, label in lovable_inventory.Category.choices
    ]
    top = int_counts(counts)
    return Coverage(
        available=True,
        taken_at=run.taken_at or run.created_at,
        total=top.get("rows", sum(r.total for r in rows)),
        operational=top.get("operational"),
        historical=top.get("historical"),
        rows=rows,
    )


@dataclass
class LovablePanel:
    mode: str
    mode_label: str
    cut_over: bool
    ok: bool
    warnings: list[str]
    #: The newest verified source reading a run kept as a baseline.
    last_observation_at: dt.datetime | None
    last_apply: LovableSyncRun | None
    last_failure: LovableSyncRun | None
    last_failure_reason: str
    #: The run whose counts the panel shows (the newest finished, non-check run).
    run: LovableSyncRun | None
    source_live: int | None
    source_operational: int | None
    outside_cohort: int | None
    operational_not_in_juristid: int | None
    cohort_size: int
    coverage: Coverage
    new_topics_created: int | None
    new_topics_held: int | None
    new_topics_seen: int | None
    updated_teemad: int | None
    #: Field-level, counted from `LovableSyncDecision` rows of ``run``.
    decisions: dict[str, int]
    import_held_fields: int | None
    pending_fields: int
    open_request: DataSyncRequest | None
    recent: list[RequestView]


def decision_counts(run: LovableSyncRun | None) -> dict[str, int]:
    """Outcome → number of field decisions the run recorded. One query."""
    counts = dict.fromkeys(SyncOutcome.values, 0)
    if run is None:
        return counts
    for outcome, n in (
        LovableSyncDecision.objects.filter(run=run)
        .values_list("outcome")
        .annotate(n=Count("pk"))
        .values_list("outcome", "n")
    ):
        counts[str(outcome)] = int(n)
    return counts


def _reference_run() -> LovableSyncRun | None:
    return (
        LovableSyncRun.objects.exclude(trigger=SyncTrigger.CHECK)
        .filter(status__in=(SyncRunStatus.SUCCEEDED, SyncRunStatus.REVIEW_ONLY))
        .order_by("-started_at")
        .first()
    )


def _count(counts: dict[str, Any], key: str) -> int | None:
    value = counts.get(key)
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def lovable_panel(user: Any, *, now: dt.datetime | None = None) -> LovablePanel:
    from app.legacy_import import lovable_observability as obs

    moment = now or timezone.now()
    status = obs.status(user, now=moment)
    ctl = _control()
    runs = LovableSyncRun.objects.exclude(trigger=SyncTrigger.CHECK)
    run = _reference_run()
    last_apply = (
        runs.filter(mode=SyncMode.APPLY, status=SyncRunStatus.SUCCEEDED)
        .order_by("-started_at")
        .first()
    )
    last_failure = runs.filter(status=SyncRunStatus.FAILED).order_by("-started_at").first()
    counts = (run.counts or {}) if run is not None else {}
    raw_cohorts = counts.get("by_cohort")
    by_cohort: dict[str, Any] = raw_cohorts if isinstance(raw_cohorts, dict) else {}
    held_fields: int | None = None
    if run is not None:
        held_fields = 0
        for part in by_cohort.values():
            if isinstance(part, dict):
                held_fields += int(part.get("import_held_fields") or 0)
    mode = current_mode()
    return LovablePanel(
        mode=mode,
        mode_label=SyncMode(mode).label,
        cut_over=bool(ctl and ctl.cut_over),
        ok=not status.warnings,
        warnings=list(status.warnings),
        last_observation_at=status.snapshot_taken_at,
        last_apply=last_apply,
        last_failure=last_failure,
        last_failure_reason=failure_reason(last_failure.error) if last_failure else "",
        run=run,
        source_live=_count(counts, "source_live") if run is not None else status.live_topics,
        source_operational=_count(counts, "source_operational")
        if run is not None
        else status.operational_topics,
        outside_cohort=_count(counts, "outside_cohort"),
        operational_not_in_juristid=_count(counts, "operational_not_in_juristid"),
        cohort_size=status.cohort_size,
        coverage=inventory_coverage(),
        new_topics_created=_count(counts, "new_topics_created"),
        new_topics_held=_count(counts, "new_topics_held"),
        new_topics_seen=_count(counts, "new_topics_seen"),
        updated_teemad=_count(counts, "updated"),
        decisions=decision_counts(run),
        import_held_fields=held_fields,
        pending_fields=status.pending_review,
        open_request=open_request(SyncRequestLane.LOVABLE),
        recent=_recent(SyncRequestLane.LOVABLE, moment),
    )


# ---------------------------------------------------------------------------
# OneNote panel
# ---------------------------------------------------------------------------

ONENOTE_MISSING = "OneNote'i katvuse ülevaadet ei õnnestunud lugeda."

#: `onenote_coverage.coverage_summary()["operational"]` (interface 5), in the page's
#: order and words: the operational Teemad's exact pages and what came of them.
ONENOTE_COUNTS: tuple[tuple[str, str], ...] = (
    ("teemad_with_exact_page", "Teemasid täpse lehelingiga"),
    ("pages", "Operatiivseid lehti"),
    ("pages_captured", "Neist kogutud ja kontrollitud"),
    ("pages_not_captured", "Kogumata lehti"),
    ("open_teemad_without_capture", "Avatud teemasid ilma kogumita"),
    ("shared_pages", "Mitme teema ühiseid lehti"),
    ("attachments_imported", "Imporditud manuseid"),
    ("new_versions", "Muutunud failide uusi versioone"),
    ("revisions_held", "Muutusi ootel"),
    ("attachments_failed", "Ebaõnnestunud importe"),
    ("teemad_without_page_link", "Teemasid ilma lehelingita"),
    ("teemad_with_ambiguous_link", "Mitmetähendusliku lingiga teemasid"),
)

#: Why an attachment was catalogued but not filed (`onenote_capture` hold codes).
ONENOTE_HOLDS: dict[str, str] = {
    "SHARED_PAGE_AWAITING_REVIEW": "Ühisleht, ootab seose otsust",
    "EMPTY_FILE": "Tühi fail",
    "LARGER_THAN_EVIDENCE_LIMIT": "Liiga suur fail",
    "EARLIER_CAPTURE_NOT_IMPORTED": "Varasem kogum importimata",
    "CONTINUES_EARLIER_BLOCK": "Vahele jäetud (sama fail juba olemas)",
    "MOVED_BLOCK_BYTES_CHANGED": "Liigutatud ja muutunud, ootab otsust",
    "AMBIGUOUS_EARLIER_BLOCK": "Mitmetähenduslik, ootab otsust",
    "RESEMBLES_EARLIER_FILE_UNPROVEN": "Sarnane varasemaga, ootab otsust",
    "SAME_FILE_ON_ANOTHER_PAGE_OF_TEEMA": "Sama fail teema teisel lehel",
}


@dataclass
class OneNotePanel:
    available: bool
    note: str
    #: `onenote_coverage.coverage_summary()["archive_note"]`: what «coverage» measures.
    scope_note: str
    last_capture: dict[str, Any]
    counts: list[tuple[str, int | None]]
    per_state: list[tuple[str, int]]
    held: list[tuple[str, int]]
    other_pages: int | None
    other_states: list[tuple[str, int]]
    sections_with_pages: int | None
    sections_not_covered: list[str]
    sections_not_covered_count: int | None
    open_request: DataSyncRequest | None
    recent: list[RequestView]
    #: The newest successful verification per capture name, importable when its
    #: plan is still the newest one checked for that capture.
    verified: list[RequestView]
    capture_root_configured: bool


def capture_root() -> str:
    return str(
        getattr(settings, "ONENOTE_CAPTURE_ROOT", "")
        or os.environ.get("JURISTID_ONENOTE_CAPTURE_ROOT", "")
    )


def _coverage_summary() -> dict[str, Any] | None:
    from app.legacy_import import onenote_coverage

    try:
        with transaction.atomic():
            return onenote_coverage.coverage_summary()
    except DatabaseError:
        return None


def _state_labels() -> dict[str, str]:
    from app.legacy_import.onenote_coverage_models import PageState

    return {str(v): str(label) for v, label in PageState.choices}


def latest_verifications() -> list[DataSyncRequest]:
    """Per capture name, the newest finished ONENOTE_VERIFY request — whatever it found."""
    seen: dict[str, DataSyncRequest] = {}
    for item in DataSyncRequest.objects.filter(
        kind=SyncRequestKind.ONENOTE_VERIFY,
        status__in=(SyncRequestStatus.DONE, SyncRequestStatus.FAILED, SyncRequestStatus.REFUSED),
    ).order_by("-finished_at", "-requested_at"):
        seen.setdefault(item.capture_name, item)
    return list(seen.values())


def _is_verified(item: DataSyncRequest) -> bool:
    result = item.result if isinstance(item.result, dict) else {}
    return (
        item.status == SyncRequestStatus.DONE
        and result.get("verified") is True
        and bool(_SHA256.fullmatch(str(result.get("plan_sha256") or "")))
    )


def _part(data: dict[str, Any], key: str) -> dict[str, Any]:
    value = data.get(key)
    return value if isinstance(value, dict) else {}


def onenote_panel(*, now: dt.datetime | None = None) -> OneNotePanel:
    """D's `coverage_summary()` as the page shows it: every number is the summary's own."""
    moment = now or timezone.now()
    summary = _coverage_summary()
    labels = _state_labels()
    data = summary or {}
    operational = _part(data, "operational")
    other = _part(data, "other_pages")
    sections = _part(data, "sections")
    last = _part(data, "last_capture")
    states = code_counts(operational.get("states"))
    not_covered = sections.get("not_covered")
    names = [str(n) for n in not_covered] if isinstance(not_covered, list) else []
    verified = [request_view(r, now=moment) for r in latest_verifications() if _is_verified(r)]
    return OneNotePanel(
        available=summary is not None,
        note="" if summary is not None else ONENOTE_MISSING,
        scope_note=str(data.get("archive_note") or ""),
        last_capture={
            "name": str(last.get("capture_id") or ""),
            "started_at": last.get("started_at"),
            "finished_at": last.get("finished_at"),
            "status": str(last.get("status") or ""),
        }
        if last
        else {},
        counts=[(label, _count(operational, key)) for key, label in ONENOTE_COUNTS],
        per_state=[(labels.get(k, k), n) for k, n in states.items()],
        held=[
            (ONENOTE_HOLDS.get(code, code), n)
            for code, n in code_counts(operational.get("attachments_held")).items()
        ],
        other_pages=_count(other, "pages"),
        other_states=[(labels.get(k, k), n) for k, n in code_counts(other.get("states")).items()],
        sections_with_pages=_count(sections, "with_operational_pages"),
        sections_not_covered=names,
        sections_not_covered_count=len(names) if isinstance(not_covered, list) else None,
        open_request=open_request(SyncRequestLane.ONENOTE),
        recent=_recent(SyncRequestLane.ONENOTE, moment),
        verified=verified,
        capture_root_configured=bool(capture_root()),
    )


# ---------------------------------------------------------------------------
# «Vaata erinevusi»
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DifferenceRow:
    matter: Any
    reference: str
    field: str
    field_label: str
    accepted: str
    juristid: str
    source: str
    explanation: str
    since: dt.datetime | None
    resolvable: bool


@dataclass
class Differences:
    rows: list[DifferenceRow]
    #: Pending differences on Teemad the reader may not open: counted, never named.
    hidden: int
    page: int
    pages: int
    total: int
    #: ``engine`` (B's `pending_differences`) or ``ledger`` (the read service's queue).
    source: str


def _engine_differences() -> Callable[..., Any] | None:
    from app.legacy_import import lovable_sync

    found = getattr(lovable_sync, "pending_differences", None)
    return found if callable(found) else None


def _explanation(field_name: str, reason: str, given: str) -> str:
    if given:
        return given
    from app.legacy_import import lovable_sync

    policy = getattr(lovable_sync, "FIELD_POLICY", {}) or {}
    entry = policy.get(field_name) if isinstance(policy, dict) else None
    words = _attr(entry, "explanation", "label", default="") if entry is not None else ""
    return str(words or LOVABLE_REASONS.get(reason, "") or "")


def differences(user: Any, *, page: int = 1) -> Differences:
    """Pending differences: A (accepted), B (Juristid), C (Lovable) and why.

    Named only for a Teema ``user`` may open; the rest are counted in ``hidden``."""
    from app.legacy_import import lovable_observability as obs
    from app.legacy_import.lovable_sync import RESOLVABLE_FIELDS as RESOLVABLE
    from app.matters.models import Matter

    engine = _engine_differences()
    if engine is not None:
        items = list(engine(visible_to=user))
        visible = set(Matter.objects.visible_to(user).values_list("pk", flat=True))
        rows: list[DifferenceRow] = []
        hidden = 0
        for item in items:
            matter = _attr(item, "matter")
            matter_id = getattr(matter, "pk", None) or _attr(item, "matter_id")
            if matter is None or matter_id not in visible:
                hidden += 1
                continue
            name = str(_attr(item, "field", "field_name", default=""))
            reason = str(_attr(item, "reason", default="") or "")
            rows.append(
                DifferenceRow(
                    matter=matter,
                    reference=str(_attr(item, "reference", default="") or matter.display_reference),
                    field=name,
                    field_label=LOVABLE_FIELD_LABELS.get(name, name),
                    accepted=_shown(_attr(item, "accepted", "baseline", "a")),
                    juristid=_shown(_attr(item, "juristid", "current", "b")),
                    source=_shown(_attr(item, "source", "lovable", "c")),
                    explanation=_explanation(
                        name, reason, str(_attr(item, "explanation", default="") or "")
                    ),
                    since=_attr(item, "since", "first_observed_at"),
                    resolvable=name in RESOLVABLE
                    and _attr(item, "kind", default="FIELD") == "FIELD",
                )
            )
        # The engine leaves out what this reader may not open; the server's own
        # view counts everything, so the rest is counted here — never named.
        hidden = max(hidden, len(engine(visible_to=None)) - len(rows))
        paginator = Paginator(rows, obs.PAGE_SIZE)
        chosen = paginator.get_page(page)
        return Differences(
            rows=list(chosen.object_list),
            hidden=hidden,
            page=chosen.number,
            pages=paginator.num_pages,
            total=len(rows),
            source="engine",
        )
    queue = obs.review_queue(user, page=page)
    shown = list(queue.items)
    ids = {item.matter_id for item in shown}
    matters = {str(m.pk): m for m in Matter.objects.visible_to(user).filter(pk__in=ids)}
    rows = [
        DifferenceRow(
            matter=matters[item.matter_id],
            reference=item.reference,
            field=item.field,
            field_label=item.label,
            accepted=item.last_applied,
            juristid=item.juristid_now,
            source=item.lovable_now,
            explanation=item.explanation,
            since=item.first_observed_at,
            resolvable=item.field in RESOLVABLE,
        )
        for item in shown
        if item.matter_id in matters
    ]
    # A reader the service refuses (a technical administrator with no persona)
    # gets an empty page: every pending field is then hidden, and counted.
    hidden = queue.hidden
    if not queue.total and not hidden:
        hidden = obs.status(user).pending_review
    return Differences(
        rows=rows,
        hidden=hidden,
        page=queue.page,
        pages=queue.pages,
        total=queue.total,
        source="ledger",
    )


def difference_for(user: Any, reference: str, field_name: str) -> DifferenceRow | None:
    """The one pending difference a decision page confirms, whatever page it is on."""
    page = 1
    while True:
        found = differences(user, page=page)
        for row in found.rows:
            if row.reference == reference and row.field == field_name:
                return row
        if page >= found.pages:
            return None
        page += 1


# ---------------------------------------------------------------------------
# «Vaata ajalugu»
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class HistoryRow:
    run: LovableSyncRun
    reason: str
    counts: dict[str, int]
    applied: int
    conflicts: int
    review: int


HISTORY_COUNTS = ("checked", "unchanged", "updated", "conflicts", "review", "failed", "missing")


def history(*, page: int = 1) -> Any:
    """Every run, newest first, a page at a time, with the counts the ledger kept."""
    paginator = Paginator(LovableSyncRun.objects.order_by("-started_at"), HISTORY_PAGE_SIZE)
    chosen = paginator.get_page(page)
    runs = list(chosen.object_list)
    tallies: dict[Any, dict[str, int]] = {}
    for run_id, outcome, n in (
        LovableSyncDecision.objects.filter(run__in=runs)
        .values_list("run_id", "outcome")
        .annotate(n=Count("pk"))
        .values_list("run_id", "outcome", "n")
    ):
        tallies.setdefault(run_id, {})[str(outcome)] = int(n)
    rows: list[HistoryRow] = [
        HistoryRow(
            run=run,
            reason=failure_reason(run.error) if run.status != SyncRunStatus.SUCCEEDED else "",
            counts={k: v for k, v in int_counts(run.counts).items() if k in HISTORY_COUNTS},
            applied=tallies.get(run.pk, {}).get(SyncOutcome.APPLIED, 0),
            conflicts=tallies.get(run.pk, {}).get(SyncOutcome.CONFLICT, 0),
            review=tallies.get(run.pk, {}).get(SyncOutcome.REVIEW_REQUIRED, 0),
        )
        for run in runs
    ]
    chosen.object_list = cast(Any, rows)
    return chosen


def request_history(*, page: int = 1) -> Any:
    paginator = Paginator(
        DataSyncRequest.objects.select_related("requested_by", "sync_run").order_by(
            "-requested_at"
        ),
        HISTORY_PAGE_SIZE,
    )
    chosen = paginator.get_page(page)
    moment = timezone.now()
    views = [request_view(r, now=moment) for r in chosen.object_list]
    chosen.object_list = cast(Any, views)
    return chosen


# ---------------------------------------------------------------------------
# The request API
# ---------------------------------------------------------------------------


def _user_of(actor: Any) -> Any:
    return getattr(actor, "user", actor)


def _audit(
    *,
    actor: Any,
    action: str,
    subject: Any = None,
    succeeded: bool = True,
    client: dict[str, Any] | None = None,
    **detail: Any,
) -> None:
    record_security_event(
        event_type=SecurityEventType.IMPORT_RUN,
        actor=_user_of(actor),
        subject=subject,
        succeeded=succeeded,
        ip_address=(client or {}).get("ip_address"),
        user_agent=str((client or {}).get("user_agent") or ""),
        detail={"action": action, **{k: v for k, v in detail.items() if v not in (None, "")}},
    )


def _refuse(
    actor: Any, kind: str, code: str, message: str, client: dict[str, Any] | None
) -> SyncRequestRefused:
    _audit(
        actor=actor,
        action="DATA_SYNC_REQUEST_REFUSED",
        succeeded=False,
        client=client,
        kind=kind,
        code=code,
    )
    return SyncRequestRefused(code, message)


def expire_stale(*, now: dt.datetime | None = None) -> int:
    """Mark PENDING requests nobody claimed in time EXPIRED. Never deletes a row."""
    moment = now or timezone.now()
    expired = 0
    for item in DataSyncRequest.objects.filter(
        status=SyncRequestStatus.PENDING, requested_at__lt=moment - PENDING_EXPIRES_AFTER
    ):
        item.status = SyncRequestStatus.EXPIRED
        item.finished_at = moment
        item.outcome_code = "EXPIRED_UNCLAIMED"
        item.save(update_fields=["status", "finished_at", "outcome_code", "updated_at"])
        expired += 1
    return expired


#: The partial unique index that allows one open request per lane (migration 0027).
OPEN_PER_LANE = "data_sync_one_open_request_per_lane"


def constraint_of(error: IntegrityError) -> str:
    """The name of the constraint the database said was violated, or ""."""
    diag = getattr(error.__cause__, "diag", None)
    return str(getattr(diag, "constraint_name", "") or "")


def record_check(
    actor: Any, *, run: LovableSyncRun | None, code: str = "", client: dict[str, Any] | None
) -> None:
    """«Kontrolli muudatusi» from the page: one audit row naming who asked and what came of it."""
    _audit(
        actor=actor,
        action="LOVABLE_CHECK_REQUESTED",
        subject=run,
        succeeded=run is not None,
        client=client,
        run=str(run.pk) if run is not None else None,
        code=code,
    )


def _record(
    actor: Any,
    *,
    kind: str,
    reason: str,
    now: dt.datetime,
    client: dict[str, Any] | None,
    audit: dict[str, Any] | None = None,
    **fields: Any,
) -> DataSyncRequest:
    expire_stale(now=now)
    try:
        with transaction.atomic():
            item = DataSyncRequest(
                kind=kind,
                requested_by=_user_of(actor),
                reason=reason,
                requested_at=now,
                **fields,
            )
            item.save()
    except IntegrityError as error:
        if constraint_of(error) != OPEN_PER_LANE:
            raise
        raise _refuse(actor, kind, "LANE_BUSY", LANE_BUSY, client) from error
    _audit(
        actor=actor,
        action="DATA_SYNC_REQUESTED",
        subject=item,
        client=client,
        kind=kind,
        request=str(item.pk),
        **(audit or {}),
    )
    return item


def _reason(actor: Any, kind: str, raw: str, client: dict[str, Any] | None) -> str:
    reason = " ".join((raw or "").split())[:300]
    if not reason:
        raise _refuse(actor, kind, "NO_REASON", NEEDS_REASON, client)
    return reason


def request_lovable_sync(
    actor: Any,
    reason: str,
    *,
    mode_seen: str | None = None,
    now: dt.datetime | None = None,
    client: dict[str, Any] | None = None,
) -> DataSyncRequest:
    """«Sünkrooni nüüd»: record a LOVABLE_SYNC request for the host runner.

    ``mode_seen`` is the mode the confirmation stated; a different current mode
    refuses, so nobody confirms one thing and gets another."""
    kind = SyncRequestKind.LOVABLE_SYNC
    moment = now or timezone.now()
    text = _reason(actor, kind, reason, client)
    ctl = _control()
    if ctl is not None and ctl.cut_over:
        raise _refuse(actor, kind, "CUT_OVER", SYNC_CUT_OVER, client)
    mode = current_mode()
    if mode == SyncMode.OFF:
        raise _refuse(actor, kind, "PAUSED", SYNC_PAUSED, client)
    if mode_seen is not None and mode_seen != mode:
        raise _refuse(actor, kind, "MODE_CHANGED", MODE_CHANGED, client)
    return _record(actor, kind=kind, reason=text, now=moment, client=client, audit={"mode": mode})


def valid_capture_name(raw: str) -> str | None:
    name = (raw or "").strip()
    if not _CAPTURE_NAME.fullmatch(name) or ".." in name:
        return None
    return name


def request_onenote_verify(
    actor: Any,
    capture_name: str,
    reason: str,
    *,
    now: dt.datetime | None = None,
    client: dict[str, Any] | None = None,
) -> DataSyncRequest:
    """«OneNote'i impordi kontroll»: the host verifies a named capture and records its check."""
    kind = SyncRequestKind.ONENOTE_VERIFY
    moment = now or timezone.now()
    name = valid_capture_name(capture_name)
    if name is None:
        raise _refuse(actor, kind, "BAD_CAPTURE_NAME", BAD_CAPTURE_NAME, client)
    text = _reason(actor, kind, reason, client)
    return _record(actor, kind=kind, reason=text, now=moment, client=client, capture_name=name)


def verified_plan(capture_name: str) -> str | None:
    """The plan digest of the newest verification of this capture, if it passed."""
    newest = (
        DataSyncRequest.objects.filter(
            kind=SyncRequestKind.ONENOTE_VERIFY,
            capture_name=capture_name,
            status__in=(
                SyncRequestStatus.DONE,
                SyncRequestStatus.FAILED,
                SyncRequestStatus.REFUSED,
            ),
        )
        .order_by("-finished_at", "-requested_at")
        .first()
    )
    if newest is None or not _is_verified(newest):
        return None
    return str(newest.result["plan_sha256"])


def request_onenote_import(
    actor: Any,
    capture_name: str,
    plan_sha256: str,
    reason: str,
    *,
    now: dt.datetime | None = None,
    client: dict[str, Any] | None = None,
) -> DataSyncRequest:
    """«Impordi OneNote'i materjalid»: bound to the verified plan's digest.

    Refused unless the newest check of this capture passed and found exactly
    this plan. The runner verifies again before it writes."""
    kind = SyncRequestKind.ONENOTE_IMPORT
    moment = now or timezone.now()
    name = valid_capture_name(capture_name)
    if name is None:
        raise _refuse(actor, kind, "BAD_CAPTURE_NAME", BAD_CAPTURE_NAME, client)
    text = _reason(actor, kind, reason, client)
    digest = (plan_sha256 or "").strip().lower()
    approved = verified_plan(name)
    if approved is None:
        raise _refuse(actor, kind, "NOT_VERIFIED", NOT_VERIFIED, client)
    if digest != approved:
        raise _refuse(actor, kind, "PLAN_CHANGED", PLAN_CHANGED, client)
    return _record(
        actor,
        kind=kind,
        reason=text,
        now=moment,
        client=client,
        capture_name=name,
        approved_plan_sha256=digest,
    )


def resolvable(user: Any, reference: str, field_name: str) -> Any:
    """The state of a pending difference this reader may decide, or None."""
    from app.legacy_import.lovable_sync import RESOLVABLE_FIELDS
    from app.legacy_import.lovable_sync_models import LovableSyncState
    from app.matters.models import Matter

    if field_name not in RESOLVABLE_FIELDS:
        return None
    state = (
        LovableSyncState.objects.select_related("matter")
        .filter(reference=reference, matter__deleted_at__isnull=True)
        .first()
    )
    if state is None or field_name not in (state.observed or {}):
        return None
    if not Matter.objects.visible_to(user).filter(pk=state.matter_id).exists():
        return None
    return state


def resolve_difference(
    actor: Any,
    user: Any,
    *,
    reference: str,
    field_name: str,
    note: str,
    client: dict[str, Any] | None = None,
) -> LovableSyncDecision:
    """An administrator's decision on one pending field, through the engine's own
    `resolve_field` (the baseline takes Lovable's value; the Teema keeps
    Juristid's). Audited; refused for a Teema the reader may not open."""
    from app.legacy_import import lovable_sync

    kind = "LOVABLE_FIELD_RESOLVED"
    text = " ".join((note or "").split())[:50]
    if not text:
        raise _refuse(actor, kind, "NO_REASON", NEEDS_REASON, client)
    state = resolvable(user, reference, field_name)
    if state is None:
        raise _refuse(actor, kind, "NOT_RESOLVABLE", NOT_RESOLVABLE, client)
    resolve = lovable_sync.resolve_field
    kwargs: dict[str, Any] = {"reference": reference, "field_name": field_name, "note": text}
    if "actor" in inspect.signature(resolve).parameters:
        kwargs["actor"] = _user_of(actor)
    try:
        decision = resolve(**kwargs)
    except lovable_sync.SyncRefused as error:
        raise _refuse(actor, kind, error.code, NOT_RESOLVABLE, client) from error
    _audit(
        actor=actor,
        action=kind,
        subject=state.matter,
        client=client,
        field=field_name,
        decision=str(decision.pk),
    )
    return decision


# ---------------------------------------------------------------------------
# The runner (host side)
# ---------------------------------------------------------------------------

NOTHING = "NOTHING_PENDING"


@dataclass(frozen=True)
class RunNext:
    """What `run_next` did: ``request`` is None when nothing was pending."""

    request: DataSyncRequest | None
    code: str

    @property
    def failed(self) -> bool:
        return self.request is not None and self.request.status in (
            SyncRequestStatus.FAILED,
            SyncRequestStatus.REFUSED,
        )


def _lovable_run_active() -> bool:
    """Whether a Lovable synchronisation holds the engine's advisory run lock now."""
    from django.db import connection

    from app.legacy_import.lovable_sync import LOCK_KEY, LOCK_NAMESPACE

    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_try_advisory_lock(%s, %s)", [LOCK_NAMESPACE, LOCK_KEY])
        free = bool(cursor.fetchone()[0])
        if free:
            cursor.execute("SELECT pg_advisory_unlock(%s, %s)", [LOCK_NAMESPACE, LOCK_KEY])
    return not free


def recover_lost(lane: str, *, now: dt.datetime) -> int:
    """RUNNING requests whose runner went silent are marked FAILED (RUNNER_LOST).

    A Lovable request is never declared lost while a synchronisation holds the
    engine's run lock: a live run is not lost, however long it takes. If a
    runner nevertheless finishes after its request was marked lost, `_finish`
    records the late result in the audit trail instead of failing."""
    if lane == SyncRequestLane.LOVABLE and _lovable_run_active():
        return 0
    lost = 0
    for item in DataSyncRequest.objects.filter(
        lane=lane, status=SyncRequestStatus.RUNNING, claimed_at__lt=now - RUNNING_LOST_AFTER
    ):
        _finish(item, SyncRequestStatus.FAILED, "RUNNER_LOST", {}, now=now)
        lost += 1
    return lost


def claim_next(lane: str, *, now: dt.datetime) -> DataSyncRequest | None:
    """The oldest PENDING request of the lane, marked RUNNING. Skips a locked row."""
    with transaction.atomic():
        item = (
            DataSyncRequest.objects.select_for_update(skip_locked=True)
            .filter(lane=lane, status=SyncRequestStatus.PENDING)
            .order_by("requested_at")
            .first()
        )
        if item is None:
            return None
        item.status = SyncRequestStatus.RUNNING
        item.claimed_at = now
        item.save(update_fields=["status", "claimed_at", "updated_at"])
        return item


def _finish(
    item: DataSyncRequest,
    status: str,
    code: str,
    result: dict[str, Any],
    *,
    now: dt.datetime,
    run: LovableSyncRun | None = None,
) -> DataSyncRequest:
    stored = DataSyncRequest.objects.filter(pk=item.pk).values_list("status", flat=True).first()
    if stored is not None and stored != item.status:
        # Finished elsewhere meanwhile (marked lost): the record stays as it is,
        # and the real outcome is kept in the audit trail.
        _audit(
            actor=item.requested_by,
            action="DATA_SYNC_FINISHED_LATE",
            subject=item,
            succeeded=status == SyncRequestStatus.DONE,
            kind=item.kind,
            request=str(item.pk),
            status=status,
            recorded_status=stored,
            code=code,
            run=str(run.pk) if run is not None else None,
        )
        item.refresh_from_db()
        return item
    item.status = status
    item.outcome_code = code[:64]
    item.result = result
    item.finished_at = now
    fields = ["status", "outcome_code", "result", "finished_at", "updated_at"]
    if run is not None:
        item.sync_run = run
        fields.append("sync_run")
    item.save(update_fields=fields)
    _audit(
        actor=item.requested_by,
        action="DATA_SYNC_FINISHED",
        subject=item,
        succeeded=status == SyncRequestStatus.DONE,
        kind=item.kind,
        request=str(item.pk),
        status=status,
        code=code,
        run=str(run.pk) if run is not None else None,
    )
    return item


def _run_lovable(
    item: DataSyncRequest,
    *,
    transport_factory: Callable[[], Any] | None,
    backup_set: str,
    now: Callable[[], dt.datetime],
) -> DataSyncRequest:
    from app.legacy_import import lovable_sync

    if transport_factory is None:
        return _finish(item, SyncRequestStatus.FAILED, "NO_SOURCE", {}, now=now())
    try:
        transport = transport_factory()
    except Exception as error:  # the credentials file, the configuration
        code = str(getattr(error, "code", "") or "NO_SOURCE")
        return _finish(item, SyncRequestStatus.FAILED, code, {}, now=now())
    try:
        run = lovable_sync.run_sync(
            trigger=SyncTrigger.MANUAL, transport=transport, backup_set=backup_set, now=now
        )
    except lovable_sync.SyncRefused as error:
        return _finish(item, SyncRequestStatus.REFUSED, error.code, {}, now=now())
    counts = int_counts(run.counts)
    result = {"run_status": run.status, "mode": run.mode, "counts": counts}
    if run.status == SyncRunStatus.FAILED:
        code = run.error.split(":")[0] or "INTERNAL"
        return _finish(item, SyncRequestStatus.FAILED, code, result, now=now(), run=run)
    code = run.error.split(":")[0] if run.status == SyncRunStatus.REVIEW_ONLY else ""
    return _finish(item, SyncRequestStatus.DONE, code, result, now=now(), run=run)


def capture_check_result(check: Any) -> dict[str, Any]:
    """D's `CaptureCheck`, as codes and counts: never a file name or a problem's text."""
    data = _as_mapping(check)
    plan = str(_attr(check, "plan_sha256", default="") or data.get("plan_sha256") or "")
    manifest = str(_attr(check, "manifest_sha256", default="") or data.get("manifest_sha256") or "")
    problems = _attr(check, "problems", default=data.get("problems"))
    problem_count = (
        len(problems)
        if isinstance(problems, list | tuple)
        else (problems if isinstance(problems, int) else 0)
    )
    ok = _attr(check, "ok", "verified", default=data.get("ok", data.get("verified")))
    verified = bool(ok) and problem_count == 0 and bool(_SHA256.fullmatch(plan))
    counts = int_counts(data.get("summary")) or int_counts(data.get("counts")) or int_counts(data)
    return {
        "verified": verified,
        "plan_sha256": plan if _SHA256.fullmatch(plan) else "",
        "manifest_sha256": manifest if _SHA256.fullmatch(manifest) else "",
        "problems": problem_count,
        "counts": counts,
    }


def _capture_path(item: DataSyncRequest, root: str | None) -> Path | None:
    base = root or capture_root()
    if not base:
        return None
    return Path(base) / item.capture_name


def _run_onenote(
    item: DataSyncRequest, *, root: str | None, now: Callable[[], dt.datetime]
) -> DataSyncRequest:
    from app.legacy_import import onenote_capture

    verify = getattr(onenote_capture, "verify_capture", None)
    apply = getattr(onenote_capture, "apply_capture", None)
    if not callable(verify) or (
        item.kind == SyncRequestKind.ONENOTE_IMPORT and not callable(apply)
    ):
        return _finish(item, SyncRequestStatus.REFUSED, "NOT_AVAILABLE", {}, now=now())
    if valid_capture_name(item.capture_name) is None:
        return _finish(item, SyncRequestStatus.REFUSED, "CAPTURE_NOT_FOUND", {}, now=now())
    path = _capture_path(item, root)
    if path is None:
        return _finish(item, SyncRequestStatus.REFUSED, "NO_CAPTURE_ROOT", {}, now=now())
    if not path.is_dir():
        return _finish(item, SyncRequestStatus.REFUSED, "CAPTURE_NOT_FOUND", {}, now=now())
    try:
        check = capture_check_result(verify(path))
    except Exception as error:
        code = str(getattr(error, "code", "") or "CAPTURE_ERROR")
        return _finish(item, SyncRequestStatus.FAILED, code, {}, now=now())
    if item.kind == SyncRequestKind.ONENOTE_VERIFY:
        status = SyncRequestStatus.DONE if check["verified"] else SyncRequestStatus.FAILED
        return _finish(item, status, "" if check["verified"] else "NOT_VERIFIED", check, now=now())
    # ONENOTE_IMPORT: the capture is checked again, and must still give the approved plan.
    if not check["verified"]:
        return _finish(item, SyncRequestStatus.REFUSED, "NOT_VERIFIED", check, now=now())
    if check["plan_sha256"] != item.approved_plan_sha256:
        return _finish(item, SyncRequestStatus.REFUSED, "PLAN_CHANGED", check, now=now())
    if not callable(apply):
        return _finish(item, SyncRequestStatus.REFUSED, "NOT_AVAILABLE", check, now=now())
    try:
        report = apply(
            path, approved_plan_sha256=item.approved_plan_sha256, actor=item.requested_by
        )
    except Exception as error:
        code = str(getattr(error, "code", "") or "IMPORT_FAILED")
        return _finish(item, SyncRequestStatus.FAILED, code, check, now=now())
    result = {**check, "counts": int_counts(_as_mapping(report)) or check["counts"]}
    return _finish(item, SyncRequestStatus.DONE, "", result, now=now())


def run_next(
    lane: str,
    *,
    transport_factory: Callable[[], Any] | None = None,
    backup_set: str = "",
    capture_root_override: str | None = None,
    now: Callable[[], dt.datetime] | None = None,
) -> RunNext:
    """Claim and run the oldest PENDING request of ``lane``; record its real result."""
    clock = now or timezone.now
    moment = clock()
    expire_stale(now=moment)
    recover_lost(lane, now=moment)
    item = claim_next(lane, now=moment)
    if item is None:
        return RunNext(None, NOTHING)
    _audit(
        actor=item.requested_by,
        action="DATA_SYNC_STARTED",
        subject=item,
        kind=item.kind,
        request=str(item.pk),
    )
    try:
        if item.lane == SyncRequestLane.LOVABLE:
            done = _run_lovable(
                item, transport_factory=transport_factory, backup_set=backup_set, now=clock
            )
        else:
            done = _run_onenote(item, root=capture_root_override, now=clock)
    except Exception:
        item.refresh_from_db()
        if item.status == SyncRequestStatus.RUNNING:
            _finish(item, SyncRequestStatus.FAILED, "INTERNAL", {}, now=clock())
        raise
    return RunNext(done, done.status)


def lanes() -> Iterable[str]:
    return SyncRequestLane.values
