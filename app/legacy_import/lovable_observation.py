"""The wider, read-only observation of the Lovable register (docs/adr/0201).

One run reads **every live topic** Lovable's API shows the service login (deleted
topics are hidden by row-level security) and the child tables, twice, and keeps
the reading only when both passes agree (`take_observation`, like
`lovable_source.take_snapshot`). The reading is compared with the previous
verified reading and each topic's net difference becomes a
`LovableObservedChange`. Nothing here writes a `LovableSyncState`,
`LovableSyncDecision`, `LovableSyncSnapshot` or any Matter, and a reading is
never a pilot baseline.

The document (format ``juristid.lovable-observation``):

* ``index`` — one entry per live topic: ``id``, ``nr``, ``aasta``, ``version``,
  ``operational`` and three canonical hashes: ``row_sha256`` (its allowlisted
  row), ``children_sha256`` (its commencements, responsible changes, stage links
  and related-topic links) and ``sha256`` over both;
* ``detail`` — for topics that are operational or in the pilot cohort only, the
  canonical values through `lovable_sync.SOURCE_FIELDS` (the same extractors the
  pilot compares with) plus `OBSERVE_FIELDS`. Other topics keep the index entry
  and nothing else.

Lovable's own audit ledger is a separate, **disabled** capability
(``LOVABLE_AUDIT_ENABLED``): with it off nothing is requested from the ledger and
the run says ``audit_access=DISABLED``; with it on and the identity refused, it
says ``UNAVAILABLE`` and no event is ever inferred. Field values are requested
only for allowlisted field names, filtered at the source; other names are read
without their values.

Retention is its own job (`prune_observations`, ``lovable_sync
prune-observations``), run every night whatever the observation's state; the
observation run itself deletes nothing.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import re
import time
import uuid
from collections import Counter
from collections.abc import Callable, Iterable
from typing import Any

from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone

from app.legacy_import.integrity import canonical_sha256
from app.legacy_import.lovable_observation_models import (
    OBSERVATION_RETENTION_DAYS,
    OBSERVATION_SCHEMA_VERSION,
    AuditAccess,
    LovableAuditCursor,
    LovableObservationRun,
    LovableObservationSnapshot,
    LovableObservedChange,
    LovableRetentionRun,
    LovableSourceEvent,
    LovableSourceEventField,
    MappingStatus,
    ObservationRunStatus,
    ObservedChangeKind,
    RetentionRunStatus,
)
from app.legacy_import.lovable_source import (
    AUDIT_FIELD_NAME_SELECT,
    AUDIT_FIELD_NEVER,
    AUDIT_FIELD_VALUE_SELECT,
    AUDIT_TABLES,
    AUDIT_VALUE_COLUMNS,
    AUDIT_VALUE_FIELDS,
    CONSISTENCY_ATTEMPTS,
    ID_CHUNK,
    LOVABLE_PROJECT,
    OBSERVE_TABLES,
    OBSERVE_TOPIC_COLUMNS,
    SourceError,
    Transport,
    audit_name_filter,
    audit_value_filter,
    check_audit_query,
    only_columns,
)
from app.legacy_import.lovable_sync import (
    SOURCE_FIELDS,
    TALLINN,
    SyncRefused,
    canonical_source,
    control,
    day_value,
    run_lock,
    text_value,
)
from app.legacy_import.lovable_sync_models import (
    LovableSyncControl,
    LovableSyncRun,
    LovableSyncState,
    SyncTrigger,
)

logger = logging.getLogger("app.legacy_import.lovable")

OBSERVATION_FORMAT = "juristid.lovable-observation"

#: Child tables read whole, page by page — no filter built from anything.
CHILD_TABLES: dict[str, str] = {
    "topic_commencement": "topic_id,position,id",
    "topic_responsible_change": "topic_id,id",
    "topic_stage_link": "prev_topic_id,next_topic_id",
    "topic_link": "topic_id,linked_topic_id",
}

#: Canonical values the observation keeps beyond the pilot's `SOURCE_FIELDS`,
#: from what only the wider reading reads.
OBSERVE_FIELDS: dict[str, Callable[[dict[str, Any]], Any]] = {
    "commencements_managed": lambda b: bool(b["row"].get("joustumised_hallatud")),
    "responsible_changes": lambda b: sorted(
        [
            text_value(r.get("vana_vastutaja")),
            text_value(r.get("uus_vastutaja")),
            day_value(r.get("alates")) or "",
        ]
        for r in b.get("responsible_changes") or []
    ),
    "stage_links": lambda b: sorted([s["prev"], s["next"]] for s in b.get("stage_links") or []),
    "topic_links": lambda b: sorted(b.get("topic_links") or []),
}

#: Ledger field names whose values may be requested and kept
#: (`lovable_source.AUDIT_VALUE_FIELDS`); any other field is read by *name*
#: only and kept as changed with its values withheld.
AUDIT_FIELD_ALLOWLIST = AUDIT_VALUE_FIELDS
#: A ledger value is kept up to this many characters of its canonical JSON
#: text; a longer one is cut and its field row marked ``truncated``.
AUDIT_VALUE_LIMIT = 2000
#: Audit events per page; pages per ordinary run, and per ``--backfill`` run.
AUDIT_PAGE_SIZE = 500
AUDIT_PAGES_PER_RUN = 20
AUDIT_BACKFILL_PAGES = 400
#: Each run reads again from this long before the cursor, so an event that
#: became visible late (committed after a later one was read) is still picked
#: up; events already stored are skipped by their source id. An event that
#: appears more than this far behind the cursor is still missed (ADR 0201 §4).
AUDIT_OVERLAP = dt.timedelta(hours=48)
#: Ledger actions that may carry no field rows. Any other action — an edit
#: (``muutmine``) or anything unknown — without a visible field row is
#: incomplete, and the reader stops before it (fail closed).
AUDIT_ACTIONS_WITHOUT_FIELDS = frozenset({"lisamine", "kustutamine", "taastamine"})
_NIL_UUID = "00000000-0000-0000-0000-000000000000"

_REFERENCE = re.compile(r"(\d{4})_(\d+)")
_UUID = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------


class _Meter:
    """Counts what the reading cost: logical reads, rows, and — from the HTTP
    transport's own counters when it has them — requests and bytes."""

    def __init__(self, transport: Any) -> None:
        self.transport = transport
        self.selects = 0
        self.rows = 0
        self.json_bytes = 0
        self._requests0 = getattr(transport, "requests", None)
        self._bytes0 = getattr(transport, "bytes_read", None)

    def select(
        self, table: str, columns: tuple[str, ...], *, filters: dict[str, str], order: str
    ) -> list[dict[str, Any]]:
        if table not in OBSERVE_TABLES or not set(columns) <= set(OBSERVE_TABLES[table]):
            raise SourceError("NOT_ALLOWLISTED", f"Reading {table} is not allowlisted.")
        rows = only_columns(
            self.transport.select(table, columns, filters=filters, order=order),
            columns,
            table=table,
        )
        self.selects += 1
        self.rows += len(rows)
        if self._bytes0 is None:
            self.json_bytes += len(json.dumps(rows, default=str).encode())
        return rows

    def counts(self) -> dict[str, int]:
        requests = getattr(self.transport, "requests", None)
        read = getattr(self.transport, "bytes_read", None)
        return {
            "rows_read": self.rows,
            "requests": (
                requests - self._requests0
                if isinstance(requests, int) and isinstance(self._requests0, int)
                else self.selects
            ),
            "bytes": (
                read - self._bytes0
                if isinstance(read, int) and isinstance(self._bytes0, int)
                else self.json_bytes
            ),
        }


def _read_register(meter: _Meter) -> dict[str, Any]:
    """Every live topic and every child row, as bundles keyed by topic id."""
    topics = meter.select("topic", OBSERVE_TOPIC_COLUMNS, filters={}, order="id")
    bundles: dict[str, dict[str, Any]] = {
        str(row["id"]): {
            "row": row,
            "commencements": [],
            "responsible_changes": [],
            "stage_links": [],
            "topic_links": [],
        }
        for row in topics
    }
    children = {
        table: meter.select(table, OBSERVE_TABLES[table], filters={}, order=order)
        for table, order in CHILD_TABLES.items()
    }
    for row in children["topic_commencement"]:
        if str(row.get("topic_id")) in bundles:
            bundles[str(row["topic_id"])]["commencements"].append(row)
    for row in children["topic_responsible_change"]:
        if str(row.get("topic_id")) in bundles:
            bundles[str(row["topic_id"])]["responsible_changes"].append(row)
    for row in children["topic_stage_link"]:
        pair = (str(row.get("prev_topic_id")), str(row.get("next_topic_id")))
        for side in set(pair):
            if side in bundles:
                bundles[side]["stage_links"].append({"prev": pair[0], "next": pair[1]})
    for row in children["topic_link"]:
        a, b = str(row.get("topic_id")), str(row.get("linked_topic_id"))
        if a in bundles:
            bundles[a]["topic_links"].append(b)
        if b in bundles and b != a:
            bundles[b]["topic_links"].append(a)
    return {
        "bundles": bundles,
        "children_rows": {table: len(rows) for table, rows in children.items()},
    }


def _children_key(bundle: dict[str, Any]) -> dict[str, Any]:
    """The bundle's child records in an order that does not depend on paging."""

    def ordered(rows: list[Any]) -> list[Any]:
        return sorted(rows, key=lambda r: json.dumps(r, sort_keys=True, default=str))

    return {
        "commencements": ordered(bundle["commencements"]),
        "responsible_changes": ordered(bundle["responsible_changes"]),
        "stage_links": ordered(bundle["stage_links"]),
        "topic_links": sorted(set(bundle["topic_links"])),
    }


def _int(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def observed_values(bundle: dict[str, Any]) -> dict[str, Any]:
    """Canonical values of one topic: the pilot's extractors, then the wider ones."""
    values = canonical_source(bundle)
    values.update({key: extract(bundle) for key, extract in OBSERVE_FIELDS.items()})
    return values


def _document(
    reading: dict[str, Any], *, detail_ids: set[str], taken_at: dt.datetime, attempts: int
) -> dict[str, Any]:
    index = []
    detail: dict[str, Any] = {}
    for topic_id in sorted(reading["bundles"]):
        bundle = reading["bundles"][topic_id]
        row = bundle["row"]
        row_hash = canonical_sha256(row)
        children_hash = canonical_sha256(_children_key(bundle))
        operational = bool(row.get("operatiivne_ulatus"))
        index.append(
            {
                "id": topic_id,
                "nr": str(row.get("nr") or ""),
                "aasta": row.get("aasta"),
                "version": _int(row.get("version")),
                "operational": operational,
                "row_sha256": row_hash,
                "children_sha256": children_hash,
                "sha256": canonical_sha256([row_hash, children_hash]),
            }
        )
        if operational or topic_id in detail_ids:
            detail[topic_id] = observed_values(bundle)
    document = {
        "format": OBSERVATION_FORMAT,
        "schema": OBSERVATION_SCHEMA_VERSION,
        "project": LOVABLE_PROJECT,
        "taken_at": taken_at.isoformat(),
        "attempts": attempts,
        "index": index,
        "detail": detail,
    }
    document["sha256"] = canonical_sha256(document)
    return document


def take_observation(
    transport: Transport,
    *,
    pilot_ids: Iterable[str] = (),
    now: Callable[[], dt.datetime] | None = None,
    pause: Callable[[float], None] = time.sleep,
    meter: _Meter | None = None,
) -> dict[str, Any]:
    """One consistent reading of the whole live register.

    Read everything, read everything again; any difference in the canonical
    hash is an edit made while reading and the whole reading is repeated, at
    most `CONSISTENCY_ATTEMPTS` times, then ``INCONSISTENT``."""
    clock = now or (lambda: dt.datetime.now(dt.UTC))
    meter = meter or _Meter(transport)
    detail_ids = {str(i) for i in pilot_ids}
    for attempt in range(1, CONSISTENCY_ATTEMPTS + 1):
        taken_at = clock()
        first = _read_register(meter)
        second = _read_register(meter)
        if canonical_sha256(first["bundles"]) == canonical_sha256(second["bundles"]):
            document = _document(first, detail_ids=detail_ids, taken_at=taken_at, attempts=attempt)
            document["children_rows"] = first["children_rows"]
            # `children_rows` is counted, not part of what the digest covers.
            return document
        if attempt < CONSISTENCY_ATTEMPTS:
            pause(5 * attempt)
    raise SourceError("INCONSISTENT", "Lovable changed while it was read, on every attempt.")


def _body(document: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in document.items() if k not in ("sha256", "children_rows")}


def verify_observation(document: dict[str, Any]) -> None:
    if (
        document.get("format") != OBSERVATION_FORMAT
        or document.get("schema") != OBSERVATION_SCHEMA_VERSION
    ):
        raise SourceError("SNAPSHOT_FORMAT", "The document is not a Lovable observation.")
    if canonical_sha256(_body(document)) != document.get("sha256"):
        raise SourceError("SNAPSHOT_DIGEST", "The observation's digest does not match it.")


# ---------------------------------------------------------------------------
# Comparing
# ---------------------------------------------------------------------------


def _reference_matters() -> dict[tuple[int, int], Any]:
    from app.matters.models import Matter

    return {
        (m.reference_year, m.reference_number): m
        for m in Matter.objects.exclude(reference_year__isnull=True).only(
            "id", "reference_year", "reference_number"
        )
    }


class _Mapper:
    """Pilot link through approved provenance, else a same-reference candidate,
    else unmatched. Never by title."""

    def __init__(self) -> None:
        self.pilot = {
            str(source_id): matter_id
            for source_id, matter_id in LovableSyncState.objects.values_list(
                "source_id", "matter_id"
            )
        }
        self.references = _reference_matters()

    def __call__(self, source_id: str, reference: str) -> tuple[str, Any]:
        if source_id in self.pilot:
            return MappingStatus.PILOT_LINKED, self.pilot[source_id]
        match = _REFERENCE.fullmatch(reference or "")
        if match is not None:
            matter = self.references.get((int(match.group(1)), int(match.group(2))))
            if matter is not None:
                return MappingStatus.REFERENCE_CANDIDATE, matter.pk
        return MappingStatus.UNMATCHED, None


def _field_diffs(before: dict[str, Any], after: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {"field": key, "before": before.get(key), "after": after.get(key)}
        for key in (*SOURCE_FIELDS, *OBSERVE_FIELDS)
        if before.get(key) != after.get(key)
    ]


def compare(
    run: LovableObservationRun,
    document: dict[str, Any],
    baseline: dict[str, Any],
    older: list[dict[str, Any]],
    *,
    pilot_ids: set[str],
) -> list[LovableObservedChange]:
    """The net difference of each topic between ``baseline`` and ``document``."""
    mapper = _Mapper()
    now_index = {e["id"]: e for e in document["index"]}
    then_index = {e["id"]: e for e in baseline["index"]}
    seen_before: set[str] = set()
    for doc in older:
        seen_before |= {e["id"] for e in doc.get("index") or []}
    now_detail, then_detail = document.get("detail") or {}, baseline.get("detail") or {}
    changes = []

    def change(source_id: str, entry: dict[str, Any], kind: str, **extra: Any) -> None:
        mapping, matter_id = mapper(source_id, entry["nr"])
        changes.append(
            LovableObservedChange(
                run=run,
                source_id=uuid.UUID(source_id),
                reference=entry["nr"][:20],
                kind=kind,
                in_pilot=source_id in pilot_ids,
                operational=bool(entry.get("operational")),
                mapping=mapping,
                matter_id=matter_id,
                **extra,
            )
        )

    for source_id, entry in now_index.items():
        before = then_index.get(source_id)
        if before is None:
            kind = ObservedChangeKind.BACK if source_id in seen_before else ObservedChangeKind.NEW
            change(
                source_id,
                entry,
                kind,
                version_after=entry.get("version"),
                detailed=source_id in now_detail,
            )
            continue
        renumbered = before["nr"] != entry["nr"]
        if not renumbered and before["sha256"] == entry["sha256"]:
            continue
        detailed = source_id in now_detail and source_id in then_detail
        fields = _field_diffs(then_detail[source_id], now_detail[source_id]) if detailed else []
        change(
            source_id,
            entry,
            ObservedChangeKind.RENUMBERED if renumbered else ObservedChangeKind.CHANGED,
            previous_reference=before["nr"][:20] if renumbered else "",
            scope_changed=bool(before.get("operational")) != bool(entry.get("operational")),
            children_only=(
                before["row_sha256"] == entry["row_sha256"]
                and before["children_sha256"] != entry["children_sha256"]
            ),
            detailed=detailed,
            field_names=[f["field"] for f in fields],
            fields=fields,
            version_before=before.get("version"),
            version_after=entry.get("version"),
        )
    for source_id, before in then_index.items():
        if source_id not in now_index:
            change(
                source_id,
                before,
                ObservedChangeKind.MISSING,
                version_before=before.get("version"),
            )
    return changes


# ---------------------------------------------------------------------------
# The run
# ---------------------------------------------------------------------------


def run_key_for(trigger: str, moment: dt.datetime) -> str:
    local = moment.astimezone(TALLINN)
    if trigger == SyncTrigger.SCHEDULED:
        return f"observe:{local.date().isoformat()}"
    return f"observe-manual:{local:%Y-%m-%dT%H:%M:%S}"


def set_observe(enabled: bool, *, reason: str) -> LovableSyncControl:
    """Switch the wider observation on or off (`lovable_sync control --observe`)."""
    with transaction.atomic():
        row = LovableSyncControl.objects.select_for_update().get_or_create(pk=1)[0]
        if row.cut_over and enabled:
            raise SyncRefused("CUT_OVER", "Final cutover is recorded; nothing reads Lovable again.")
        row.observe, row.changed_at, row.changed_reason = enabled, timezone.now(), reason[:300]
        row.save()
    return row


def baseline_runs() -> Any:
    """Verified readings (SUCCEEDED runs with a stored snapshot), newest first."""
    return LovableObservationRun.objects.filter(
        status=ObservationRunStatus.SUCCEEDED, snapshot__isnull=False
    ).order_by("-started_at")


def run_observation(
    *,
    trigger: str,
    transport: Transport,
    now: Callable[[], dt.datetime] | None = None,
    pause: Callable[[float], None] | None = None,
    backfill: bool = False,
) -> LovableObservationRun:
    """One observation run, end to end. Refuses (`SyncRefused`) before a ledger
    row exists; after that, never raises: a failure is a FAILED run."""
    clock = now or timezone.now
    if trigger not in (SyncTrigger.SCHEDULED, SyncTrigger.MANUAL):
        raise SyncRefused("UNKNOWN_TRIGGER", "An observation is scheduled or manual.")
    ctl = control()
    if ctl.cut_over:
        raise SyncRefused("CUT_OVER", "Final cutover is recorded; nothing reads Lovable again.")
    if not ctl.observe:
        raise SyncRefused("OBSERVE_OFF", "The wider observation is switched off.")
    started = clock()
    key = run_key_for(trigger, started)
    if LovableObservationRun.objects.filter(run_key=key).exists():
        raise SyncRefused("DUPLICATE", f"Observation {key} already exists.")

    with run_lock():
        try:
            with transaction.atomic():
                run = LovableObservationRun.objects.create(
                    run_key=key,
                    trigger=trigger,
                    started_at=started,
                    sync_run=LovableSyncRun.objects.filter(
                        run_key=started.astimezone(TALLINN).date().isoformat()
                    ).first(),
                )
        except IntegrityError as error:
            raise SyncRefused("DUPLICATE", f"Observation {key} already exists.") from error
        meter = _Meter(transport)
        try:
            _execute(run, meter=meter, clock=clock, pause=pause or time.sleep)
        except (SourceError, SyncRefused) as error:
            _fail(run, error.code, str(error), clock, meter)
            return run
        except Exception as error:
            logger.error("Lovable observation %s failed (%s)", run.pk, type(error).__name__)
            _fail(run, "INTERNAL", type(error).__name__, clock, meter)
            return run
        try:
            ingest_audit(run, transport, backfill=backfill, now=clock())
        except Exception as error:  # the audit never fails the reading
            logger.error("Lovable audit ingestion failed (%s)", type(error).__name__)
            run.audit_access, run.audit_error = AuditAccess.UNAVAILABLE, "INTERNAL"
            run.save(update_fields=["audit_access", "audit_error", "updated_at"])
        run.finished_at = clock()
        run.counts["seconds"] = round((run.finished_at - run.started_at).total_seconds(), 1)
        run.save(update_fields=["counts", "finished_at", "updated_at"])
        return run


def _fail(
    run: LovableObservationRun,
    code: str,
    message: str,
    clock: Callable[[], dt.datetime],
    meter: _Meter,
) -> None:
    run.status = ObservationRunStatus.FAILED
    run.error = f"{code}: {message}"[:300]
    run.finished_at = clock()
    run.counts = {
        **meter.counts(),
        "seconds": round((run.finished_at - run.started_at).total_seconds(), 1),
    }
    run.audit_access = AuditAccess.DISABLED
    run.save(
        update_fields=["status", "error", "finished_at", "counts", "audit_access", "updated_at"]
    )


def _execute(
    run: LovableObservationRun,
    *,
    meter: _Meter,
    clock: Callable[[], dt.datetime],
    pause: Callable[[float], None],
) -> None:
    pilot_ids = {str(i) for i in LovableSyncState.objects.values_list("source_id", flat=True)}
    tick = time.monotonic()
    document = take_observation(
        meter.transport, pilot_ids=pilot_ids, now=clock, pause=pause, meter=meter
    )
    read_seconds = round(time.monotonic() - tick, 2)
    children_rows = document.pop("children_rows", {})
    verify_observation(document)

    baseline_run = None
    baseline_doc = None
    older: list[dict[str, Any]] = []
    for candidate in baseline_runs().exclude(pk=run.pk).select_related("snapshot"):
        try:
            verify_observation(candidate.snapshot.document)
        except SourceError:
            continue
        if baseline_run is None:
            baseline_run, baseline_doc = candidate, candidate.snapshot.document
        else:
            older.append(candidate.snapshot.document)

    index = document["index"]
    counts: dict[str, Any] = {
        "comparison": "BASELINE" if baseline_doc is None else "COMPARED",
        "live": len(index),
        "operational": sum(1 for e in index if e["operational"]),
        "pilot_expected": len(pilot_ids),
        "pilot_found": sum(1 for e in index if e["id"] in pilot_ids),
        "detailed": len(document["detail"]),
        "attempts": document["attempts"],
        "children_rows": children_rows,
        "read_seconds": read_seconds,
        **meter.counts(),
    }
    with transaction.atomic():
        changes: list[LovableObservedChange] = []
        if baseline_doc is not None:
            changes = compare(run, document, baseline_doc, older, pilot_ids=pilot_ids)
            LovableObservedChange.objects.bulk_create(changes)
        counts["by_kind"] = dict(Counter(str(c.kind) for c in changes))
        counts["by_mapping"] = dict(Counter(str(c.mapping) for c in changes))
        counts["changed_fields"] = sum(len(c.field_names) for c in changes)
        counts["scope_changed"] = sum(1 for c in changes if c.scope_changed)
        counts["children_only"] = sum(1 for c in changes if c.children_only)
        taken_at = dt.datetime.fromisoformat(document["taken_at"])
        LovableObservationSnapshot.objects.create(
            run=run, taken_at=taken_at, sha256=document["sha256"], document=document
        )
        run.status = ObservationRunStatus.SUCCEEDED
        run.taken_at = taken_at
        run.sha256 = document["sha256"]
        run.baseline = baseline_run
        run.counts = counts
        run.error = ""
        run.save(
            update_fields=[
                "status",
                "taken_at",
                "sha256",
                "baseline",
                "counts",
                "error",
                "updated_at",
            ]
        )


# ---------------------------------------------------------------------------
# Lovable's audit ledger (disabled until an approved read path exists)
# ---------------------------------------------------------------------------


def _ledger_value(value: Any) -> tuple[str, bool]:
    """A JSONB value as canonical JSON text, bounded at `AUDIT_VALUE_LIMIT`;
    the second item says whether it was cut."""
    if value is None:
        return "", False
    text = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    return text[:AUDIT_VALUE_LIMIT], len(text) > AUDIT_VALUE_LIMIT


def _after(moment: dt.datetime, event_id: str) -> dict[str, str]:
    """Strictly after (``moment``, ``event_id``): later in time, or the same
    moment with a greater id. Built only from stored values, never caller text."""
    if not _UUID.fullmatch(event_id):
        raise SourceError("AUDIT_CURSOR", "The stored audit cursor is not an event id.")
    at = moment.astimezone(dt.UTC).isoformat()
    return {"or": f'(occurred_at.gt."{at}",and(occurred_at.eq."{at}",id.gt.{event_id.lower()}))'}


def _cursor_filter(cursor: LovableAuditCursor) -> dict[str, str]:
    """Where a run starts reading: `AUDIT_OVERLAP` before the stored cursor
    (every event at or after that moment), or the beginning of the ledger."""
    if cursor.last_occurred_at is None or not cursor.last_event_id:
        return {}
    if not _UUID.fullmatch(cursor.last_event_id):
        raise SourceError("AUDIT_CURSOR", "The stored audit cursor is not an event id.")
    return _after(cursor.last_occurred_at - AUDIT_OVERLAP, _NIL_UUID)


def _refused(error: SourceError) -> bool:
    return error.code in ("HTTP_401", "HTTP_403") or error.postgrest_code == "42501"


def ingest_audit(
    run: LovableObservationRun,
    transport: Any,
    *,
    backfill: bool = False,
    now: dt.datetime | None = None,
) -> None:
    """Read Lovable's audit ledger forward, page by page (docs/adr/0201 §4).

    Disabled (the default): nothing is requested. A run starts `AUDIT_OVERLAP`
    before the stored cursor, so a late-visible event is still picked up; an
    event already stored is skipped (idempotent). Each page's new events and
    their field rows are stored, and the cursor advanced to the last
    *complete* event, in one transaction, so an interrupted backfill resumes
    after the last committed page. Field rows the identity cannot see
    (``FIELDS_NOT_VISIBLE``) or an edit with none (``INCOMPLETE_EVENT``) stop
    the reading there: nothing incomplete is stored and the cursor does not
    pass it. Events older than the retention window are not kept.
    """
    counts = {"pages": 0, "events_new": 0, "events_seen": 0, "events_expired": 0}
    counts.update({"late": 0, "fields": 0, "withheld": 0, "truncated": 0})
    if not getattr(settings, "LOVABLE_AUDIT_ENABLED", False):
        run.audit_access, run.audit_error = AuditAccess.DISABLED, ""
        run.save(update_fields=["audit_access", "audit_error", "updated_at"])
        return
    moment = now or timezone.now()
    cutoff = moment - dt.timedelta(days=OBSERVATION_RETENTION_DAYS)
    cursor, _ = LovableAuditCursor.objects.get_or_create(pk=1)
    first_ever = cursor.last_occurred_at is None and not cursor.backfill_complete
    previous = (
        (cursor.last_occurred_at, cursor.last_event_id.lower())
        if cursor.last_occurred_at is not None and cursor.last_event_id
        else None
    )
    page_cap = AUDIT_BACKFILL_PAGES if backfill else AUDIT_PAGES_PER_RUN
    access, error_code = AuditAccess.AVAILABLE, ""
    try:
        filters = _cursor_filter(cursor)
        for _ in range(page_cap):
            events = transport.select_page(
                "topic_change_event",
                AUDIT_TABLES["topic_change_event"],
                filters=filters,
                order="occurred_at,id",
                limit=AUDIT_PAGE_SIZE,
            )
            events = only_columns(
                events, AUDIT_TABLES["topic_change_event"], table="topic_change_event"
            )
            if not events:
                if first_ever and counts["pages"] == 0:
                    access, error_code = AuditAccess.UNAVAILABLE, "NO_ROWS_VISIBLE"
                else:
                    cursor.backfill_complete = True
                    cursor.save(update_fields=["backfill_complete", "updated_at"])
                break
            stopped = _store_page(run, transport, events, cursor, cutoff, counts, previous)
            counts["pages"] += 1
            if stopped:
                access, error_code = AuditAccess.UNAVAILABLE, stopped
                break
            last = events[-1]
            filters = _after(_parse_moment(last.get("occurred_at")), str(last["id"]))
            if len(events) < AUDIT_PAGE_SIZE:
                cursor.backfill_complete = True
                cursor.save(update_fields=["backfill_complete", "updated_at"])
                break
    except SourceError as error:
        # A refusal (401/403/42501) or any other failure to read: the ledger is
        # unavailable tonight, and no event is inferred from anything else.
        access = AuditAccess.UNAVAILABLE
        error_code = f"{error.code} {error.postgrest_code}".strip()
        if _refused(error):
            error_code = f"REFUSED {error_code}"
    run.audit_access, run.audit_error = access, error_code[:120]
    run.counts = {**run.counts, "audit": counts}
    run.save(update_fields=["audit_access", "audit_error", "counts", "updated_at"])


def _parse_moment(raw: Any) -> dt.datetime:
    try:
        moment = dt.datetime.fromisoformat(str(raw))
    except ValueError:
        raise SourceError("AUDIT_SHAPE", "An audit event has no readable time.") from None
    if moment.tzinfo is None:
        raise SourceError("AUDIT_SHAPE", "An audit event's time has no zone.")
    return moment


def _misbehaving() -> SourceError:
    return SourceError(
        "SOURCE_FILTER_IGNORED",
        "Lovable answered a ledger read outside its filter; nothing from the page is kept.",
    )


def _read_fields(transport: Any, ids: list[str], *, values: bool) -> list[dict[str, Any]]:
    """The field rows of ``ids``: with values, only for allowlisted field
    names (filtered at the source); without, the other names only. Every
    answer is checked against its own filter, so a source that ignored one
    fails the page (``SOURCE_FILTER_IGNORED``) and nothing of it is kept."""
    columns = AUDIT_FIELD_VALUE_SELECT if values else AUDIT_FIELD_NAME_SELECT
    wanted = set(ids)
    rows: list[dict[str, Any]] = []
    for start in range(0, len(ids), ID_CHUNK):
        chunk = ids[start : start + ID_CHUNK]
        filters = {
            "event_id": "in.(" + ",".join(chunk) + ")",
            "field": audit_value_filter() if values else audit_name_filter(),
        }
        check_audit_query("topic_change_field", columns, filters)
        answer = transport.select(
            "topic_change_field", columns, filters=filters, order="event_id,field"
        )
        for row in answer:
            if not isinstance(row, dict):
                raise SourceError("RESPONSE_SHAPE", "topic_change_field answered a non-row.")
            name = text_value(row.get("field"))
            on_allowlist = name in AUDIT_VALUE_FIELDS and name not in AUDIT_FIELD_NEVER
            if (
                str(row.get("event_id")).lower() not in wanted
                or on_allowlist != values
                or (not values and any(c in row for c in AUDIT_VALUE_COLUMNS))
            ):
                raise _misbehaving()
        rows += only_columns(answer, columns, table="topic_change_field")
    return rows


def _store_page(
    run: LovableObservationRun,
    transport: Any,
    events: list[dict[str, Any]],
    cursor: LovableAuditCursor,
    cutoff: dt.datetime,
    counts: dict[str, int],
    previous: tuple[dt.datetime, str] | None,
) -> str:
    """Store one page's new, complete events and advance the cursor to the last
    complete one. Returns ``""``, or the code that stopped the reading."""
    for event in events:
        if not _UUID.fullmatch(str(event.get("id") or "")):
            raise SourceError("AUDIT_SHAPE", "An audit event has no id.")
        if not _UUID.fullmatch(str(event.get("topic_id") or "")):
            raise SourceError("AUDIT_SHAPE", "An audit event has no topic.")
    moments = {str(e["id"]).lower(): _parse_moment(e.get("occurred_at")) for e in events}
    ids = list(moments)
    existing = set(
        LovableSourceEvent.objects.filter(source_event_id__in=ids).values_list(
            "source_event_id", flat=True
        )
    )
    fresh = [
        e
        for e in events
        if str(e["id"]).lower() not in existing and moments[str(e["id"]).lower()] >= cutoff
    ]
    fresh_ids = [str(e["id"]).lower() for e in fresh]
    valued = _read_fields(transport, fresh_ids, values=True) if fresh_ids else []
    named = _read_fields(transport, fresh_ids, values=False) if fresh_ids else []
    by_event: dict[str, list[tuple[dict[str, Any], bool]]] = {}
    for row, withheld in [(r, False) for r in valued] + [(r, True) for r in named]:
        by_event.setdefault(str(row.get("event_id")).lower(), []).append((row, withheld))

    def needs_fields(event: dict[str, Any]) -> bool:
        return text_value(event.get("action")) not in AUDIT_ACTIONS_WITHOUT_FIELDS

    # Under row-level security a refused field read is `200 []`: a page whose
    # edits have no field row at all cannot be told from a denied read.
    if any(needs_fields(e) for e in fresh) and not valued and not named:
        return "FIELDS_NOT_VISIBLE"

    stopped = ""
    complete: tuple[dt.datetime, str] | None = None
    with transaction.atomic():
        for event in events:
            event_id = str(event["id"]).lower()
            occurred = moments[event_id]
            if event_id in existing:
                counts["events_seen"] += 1
            elif occurred < cutoff:
                counts["events_expired"] += 1
            elif needs_fields(event) and not by_event.get(event_id):
                stopped = "INCOMPLETE_EVENT"
                break
            else:
                _store_event(run, event, occurred, by_event.get(event_id, []), counts)
                if previous is not None and (occurred, event_id) < previous:
                    counts["late"] += 1
            complete = (occurred, event_id)
        if complete is not None and (
            cursor.last_occurred_at is None
            or complete > (cursor.last_occurred_at, cursor.last_event_id.lower())
        ):
            cursor.last_occurred_at, cursor.last_event_id = complete
            cursor.save(update_fields=["last_occurred_at", "last_event_id", "updated_at"])
    return stopped


def _store_event(
    run: LovableObservationRun,
    event: dict[str, Any],
    occurred: dt.datetime,
    rows: list[tuple[dict[str, Any], bool]],
    counts: dict[str, int],
) -> None:
    rows = sorted(rows, key=lambda pair: str(pair[0].get("field")))
    stored = LovableSourceEvent.objects.create(
        source_event_id=str(event["id"]).lower(),
        topic_id=uuid.UUID(str(event["topic_id"])),
        action=text_value(event.get("action"))[:32],
        actor_kind=text_value(event.get("actor_kind"))[:32],
        version=event.get("version") if isinstance(event.get("version"), int) else None,
        occurred_at=occurred,
        run=run,
        sha256=canonical_sha256({"event": event, "fields": [row for row, _w in rows]}),
    )
    kept: list[LovableSourceEventField] = []
    names: set[str] = set()
    for row, named_only in rows:
        name = text_value(row.get("field"))[:64]
        if not name or name in names:
            continue
        names.add(name)
        withheld = named_only or name in AUDIT_FIELD_NEVER or name not in AUDIT_FIELD_ALLOWLIST
        old, old_cut = ("", False) if withheld else _ledger_value(row.get("old_value"))
        new, new_cut = ("", False) if withheld else _ledger_value(row.get("new_value"))
        kept.append(
            LovableSourceEventField(
                event=stored,
                field=name,
                old_value=old,
                new_value=new,
                withheld=withheld,
                truncated=old_cut or new_cut,
            )
        )
        counts["withheld"] += withheld
        counts["truncated"] += old_cut or new_cut
    LovableSourceEventField.objects.bulk_create(kept)
    counts["fields"] += len(kept)
    counts["events_new"] += 1


# ---------------------------------------------------------------------------
# Retention (independent of any reading) and status
# ---------------------------------------------------------------------------


def retention_key_for(trigger: str, moment: dt.datetime) -> str:
    local = moment.astimezone(TALLINN)
    if trigger == SyncTrigger.SCHEDULED:
        return f"prune:{local.date().isoformat()}"
    return f"prune-manual:{local:%Y-%m-%dT%H:%M:%S}"


def _prune(now: dt.datetime) -> dict[str, Any]:
    """Raw observation data older than the retention goes; the newest verified
    snapshot always stays, and run rows with their counts are kept."""
    cutoff = now - dt.timedelta(days=OBSERVATION_RETENTION_DAYS)
    keep = baseline_runs().values_list("snapshot__pk", flat=True).first()
    stale = LovableObservationSnapshot.objects.filter(taken_at__lt=cutoff)
    kept_baseline = keep is not None and stale.filter(pk=keep).exists()
    if keep is not None:
        stale = stale.exclude(pk=keep)
    _, snapshots = stale.delete()
    _, changes = LovableObservedChange.objects.filter(created_at__lt=cutoff).delete()
    _, events = LovableSourceEvent.objects.filter(occurred_at__lt=cutoff).delete()
    return {
        "snapshots": snapshots.get(LovableObservationSnapshot._meta.label, 0),
        "changes": changes.get(LovableObservedChange._meta.label, 0),
        "events": events.get(LovableSourceEvent._meta.label, 0),
        "kept_baseline": kept_baseline,
    }


def prune_observations(
    *, trigger: str = SyncTrigger.SCHEDULED, now: dt.datetime | None = None
) -> LovableRetentionRun:
    """The retention job (`lovable_sync prune-observations`, docs/adr/0201 §8):
    its own ledger row, every night, whether the observation is on, off, paused
    or failing and whether Lovable can be reached — it never reads Lovable. A
    scheduled night is once (``prune:<Tallinn date>``; a second firing is
    refused ``DUPLICATE``). Once its row exists it never raises: a failure is a
    FAILED run with a code."""
    if trigger not in (SyncTrigger.SCHEDULED, SyncTrigger.MANUAL):
        raise SyncRefused("UNKNOWN_TRIGGER", "A pruning is scheduled or manual.")
    started = now or timezone.now()
    key = retention_key_for(trigger, started)
    if LovableRetentionRun.objects.filter(run_key=key).exists():
        raise SyncRefused("DUPLICATE", f"Pruning {key} already exists.")
    try:
        with transaction.atomic():
            run = LovableRetentionRun.objects.create(
                run_key=key, started_at=started, status=RetentionRunStatus.FAILED
            )
    except IntegrityError as error:
        raise SyncRefused("DUPLICATE", f"Pruning {key} already exists.") from error
    try:
        with transaction.atomic():
            run.counts = _prune(started)
        run.status, run.error = RetentionRunStatus.SUCCEEDED, ""
    except Exception as error:
        code = str(getattr(error, "code", "") or "INTERNAL")
        logger.error("Lovable observation pruning failed (%s %s)", code, type(error).__name__)
        run.status = RetentionRunStatus.FAILED
        run.error = f"{code}: {type(error).__name__}"[:300]
    run.finished_at = started if now is not None else timezone.now()
    run.save(update_fields=["status", "error", "counts", "finished_at", "updated_at"])
    return run


def observation_status() -> dict[str, Any]:
    """For `lovable_sync observe-status`. Reads only — creates no control row."""
    ctl = LovableSyncControl.objects.filter(pk=1).first()
    runs = LovableObservationRun.objects.order_by("-started_at")
    cursor = LovableAuditCursor.objects.filter(pk=1).first()

    def brief(run: LovableObservationRun | None) -> dict[str, Any] | None:
        if run is None:
            return None
        return {
            "run_key": run.run_key,
            "trigger": run.trigger,
            "status": run.status,
            "started_at": run.started_at,
            "finished_at": run.finished_at,
            "taken_at": run.taken_at,
            "sha256": run.sha256,
            "audit_access": run.audit_access,
            "audit_error": run.audit_error,
            "error": run.error,
            "counts": run.counts,
        }

    return {
        "observe": bool(ctl and ctl.observe),
        "cut_over": bool(ctl and ctl.cut_over),
        "audit_enabled": bool(getattr(settings, "LOVABLE_AUDIT_ENABLED", False)),
        "audit_cursor": (
            {
                "last_occurred_at": cursor.last_occurred_at,
                "backfill_complete": cursor.backfill_complete,
            }
            if cursor
            else None
        ),
        "last_run": brief(runs.first()),
        "last_success": brief(runs.filter(status=ObservationRunStatus.SUCCEEDED).first()),
        "events_stored": LovableSourceEvent.objects.count(),
        "last_retention": (
            {
                "run_key": retention.run_key,
                "status": retention.status,
                "started_at": retention.started_at,
                "finished_at": retention.finished_at,
                "error": retention.error,
                "counts": retention.counts,
            }
            if (retention := LovableRetentionRun.objects.order_by("-started_at").first())
            else None
        ),
    }
