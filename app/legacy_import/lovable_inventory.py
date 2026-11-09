"""The full Lovable inventory: one ledger row for every source topic (brief §5 A1–A3).

A **full reading** (`read_full`: `lovable_observation.take_observation` with
``detail="all"`` — every live topic read twice through the GET-only transport and
compared by canonical hash, three attempts) is compared with what Juristid holds,
and every source topic gets **exactly one** category:

``EXACT``
    One live Teema carries this Lovable UUID through approved provenance: its
    synchronisation state, or a canonical Lovable `MatterSourceReference`. The
    reason says whether it is enrolled in an active cohort.
``LINK_ENRICHMENT``
    A live Teema holds the same reference (``YYYY_N``) and records no Lovable
    identity at all: the same topic, probably, but the link is a person's decision.
``ARCHIVE_UNLINKED``
    No Teema represents the topic, but Juristid's historical archive holds it: a
    `LegacySourcePage` whose exact OneNote page GUID is the page the topic's
    ``onenote_link`` names (`onenote_capture.register_page_guids`).
``ABSENT``
    Proven absent: no live Teema with the UUID, none with the reference, no
    archive page. A UUID or reference that lives only on a tombstone of the
    03.11 replacement (or any earlier deletion) is noted (``TOMBSTONE_UUID``,
    ``TOMBSTONE_REFERENCE``); such a topic is still absent.
``POSSIBLE_DUPLICATE``
    Needs a person: two source topics share a reference; a same-year
    near-identical title (stage-linked pairs excluded); one UUID on several
    Teemad; a reference held by a Teema of another Lovable identity. A
    suggestion only — nothing is merged or linked.
``DELETED_OR_SUPERSEDED``
    A live Lovable-derived Teema whose source topic is no longer in the reading
    (deleted source rows are hidden from the service login). A source
    predecessor superseded through a stage link is **not** this category: it is
    categorised like any topic and flagged ``superseded_in_source``.
``EXCLUDED``
    A documented reason: no valid ``nr`` (``YYYY_N``) or ``aasta`` disagreeing
    with it.

Fuzzy title matching only ever produces candidates (it can move an ``ABSENT``
topic to ``POSSIBLE_DUPLICATE`` — which keeps it out of the backfill — and never
makes anything ``EXACT``). Per row: operational, activity
(`lovable_sync.source_activity`), and for a matched Teema stage, open/closed and
continuation mismatches.

**Reads only.** `build_ledger` writes nothing (no control row, no state, no
cohort, no Teema); only `store_run` writes, and only `LovableInventoryRun`.
"""

from __future__ import annotations

import csv
import datetime as dt
import difflib
import importlib
import io
import re
import time
from collections import Counter, defaultdict
from collections.abc import Callable, Iterable
from dataclasses import asdict, dataclass, field
from typing import Any

from django.db import models

from app.legacy_import.integrity import canonical_sha256
from app.legacy_import.lovable_inventory_models import (
    InventoryDecisionKind,
    InventoryRunStatus,
    InventoryTrigger,
    LovableInventoryDecision,
    LovableInventoryRun,
)
from app.legacy_import.lovable_observation import (
    DETAIL_ALL,
    take_observation,
    verify_observation,
)
from app.legacy_import.lovable_pilot import SOURCE_SYSTEM, parse_reference
from app.legacy_import.lovable_source import SourceError, Transport
from app.legacy_import.lovable_sync import (
    ACTIVITY_MISSING,
    ACTIVITY_ONGOING,
    SyncRefused,
    name_key,
    run_lock,
    source_activity,
    text_value,
)

INVENTORY_VERSION = "lovable-inventory/1"
LEDGER_FORMAT = "juristid.lovable-inventory-ledger"
#: Agent B's machine reader (COMMON interface 1), imported only when present.
READING_MODULE = "app.legacy_import.lovable_reading"


class Category(models.TextChoices):
    EXACT = "EXACT", "Täpne vaste"
    LINK_ENRICHMENT = "LINK_ENRICHMENT", "Sama viide, Lovable'i seos puudub"
    ARCHIVE_UNLINKED = "ARCHIVE_UNLINKED", "Arhiivis olemas, teemaga sidumata"
    ABSENT = "ABSENT", "Puudub"
    POSSIBLE_DUPLICATE = "POSSIBLE_DUPLICATE", "Võimalik topelt"
    DELETED_OR_SUPERSEDED = "DELETED_OR_SUPERSEDED", "Allikas kadunud või asendatud"
    EXCLUDED = "EXCLUDED", "Välja jäetud"


# Reason codes (one per row) — codes, never business text.
EXACT_ENROLLED = "EXACT_ENROLLED"
EXACT_COHORT_RETIRED = "EXACT_COHORT_RETIRED"
EXACT_NOT_ENROLLED = "EXACT_NOT_ENROLLED"
SAME_REFERENCE_NO_IDENTITY = "SAME_REFERENCE_NO_IDENTITY"
ARCHIVE_PAGE_GUID = "ARCHIVE_PAGE_GUID"
PROVEN_ABSENT = "PROVEN_ABSENT"
UUID_ON_SEVERAL_TEEMAD = "UUID_ON_SEVERAL_TEEMAD"
TEEMA_HOLDS_SEVERAL_IDENTITIES = "TEEMA_HOLDS_SEVERAL_IDENTITIES"
SOURCE_REFERENCE_SHARED = "SOURCE_REFERENCE_SHARED"
REFERENCE_ON_SEVERAL_TEEMAD = "REFERENCE_ON_SEVERAL_TEEMAD"
REFERENCE_HELD_BY_OTHER_IDENTITY = "REFERENCE_HELD_BY_OTHER_IDENTITY"
NEAR_IDENTICAL_TITLE = "NEAR_IDENTICAL_TITLE"
SOURCE_MISSING = "SOURCE_MISSING"
NO_VALID_REFERENCE = "NO_VALID_REFERENCE"
REFERENCE_YEAR_MISMATCH = "REFERENCE_YEAR_MISMATCH"

NEAR_IDENTICAL_TEEMA_TITLE = "NEAR_IDENTICAL_TEEMA_TITLE"
#: A person's `LovableInventoryDecision` decided the row.
DECIDED_EXCLUDE = "DECIDED_EXCLUDE"
DECIDED_LINK = "DECIDED_LINK"

# Notes (any number per row).
TOMBSTONE_UUID = "TOMBSTONE_UUID"
TOMBSTONE_REFERENCE = "TOMBSTONE_REFERENCE"
RENUMBERED_IN_SOURCE = "RENUMBERED_IN_SOURCE"
SHARED_SOURCE_REFERENCE = "SHARED_SOURCE_REFERENCE"
DECIDED_NOT_SAME = "DECIDED_NOT_SAME"
#: A decision on an EXACT row is recorded but never overrides approved provenance.
DECISION_NOT_APPLIED = "DECISION_NOT_APPLIED"

#: Candidate bases a person's NOT_SAME can settle: title similarity only, never
#: an identity or reference collision.
TITLE_BASES = frozenset({"NEAR_IDENTICAL_TITLE", "NEAR_IDENTICAL_TEEMA_TITLE"})

# Continuation mismatch codes.
SOURCE_ONLY_CONTINUATION = "SOURCE_ONLY_CONTINUATION"
JURISTID_ONLY_CONTINUATION = "JURISTID_ONLY_CONTINUATION"
SUCCESSOR_DIFFERS = "SUCCESSOR_DIFFERS"

#: A near-identical title: equal once folded, or this similar (and long enough
#: that a short generic title never pairs with another).
NEAR_TITLE_RATIO = 0.92
NEAR_TITLE_MIN_LENGTH = 12

_NON_WORD = re.compile(r"[\W_]+", re.UNICODE)
_DIGITS = re.compile(r"\d+")
_GUID = re.compile(r"[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}")


class InventoryError(Exception):
    """The ledger cannot be built from this document. ``code`` says why."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


# ---------------------------------------------------------------------------
# The ledger
# ---------------------------------------------------------------------------


@dataclass
class LedgerRow:
    source_id: str
    reference: str
    year: int | None
    operational: bool
    activity: str
    category: str
    reason: str
    matter_id: str | None = None
    matter_reference: str = ""
    cohort: str = ""
    #: Suggestions only: ``{"source_id", "reference", "matter_id", "basis"}``.
    candidates: list[dict[str, Any]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    archive_pages: list[str] = field(default_factory=list)
    #: ``None`` where no Teema is matched (not applicable).
    stage_mismatch: bool | None = None
    stage_basis: str = ""
    open_closed_mismatch: bool | None = None
    continuation_mismatch: str | None = None
    superseded_in_source: bool = False
    #: False only for a Juristid-side row whose source topic is gone.
    in_source: bool = True
    #: The person's newest decision on this topic, when one exists:
    #: ``{"id", "kind", "matter_id", "reason"}``.
    decision: dict[str, Any] | None = None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Ledger:
    taken_at: str
    source_sha256: str
    rows: list[LedgerRow]
    version: str = INVENTORY_VERSION
    #: Every decision the ledger read, as ``[id, source_id, kind]``, by id.
    decisions: list[list[str]] = field(default_factory=list)

    @property
    def decisions_sha256(self) -> str:
        return canonical_sha256(self.decisions)

    def check(self) -> None:
        """Every row has exactly one known category; one id is one row; no Teema
        is the exact match of two topics."""
        known = set(Category.values)
        bad = [r.source_id for r in self.rows if r.category not in known]
        if bad:
            raise InventoryError("CATEGORY", f"{len(bad)} row(s) have no known category.")
        ids = Counter(r.source_id for r in self.rows)
        if any(n > 1 for n in ids.values()):
            raise InventoryError("DUPLICATE_ROW", "A source id has more than one ledger row.")
        exact = Counter(r.matter_id for r in self.rows if r.category == Category.EXACT)
        if any(n > 1 for n in exact.values()):
            raise InventoryError("EXACT_TWICE", "A Teema is the exact match of two topics.")

    def counts(self) -> dict[str, Any]:
        """Brief §5 A3: totals, operational vs historical, every category, the
        excluded reasons, duplicates and the mismatches. Sums equal the rows."""
        source = [r for r in self.rows if r.in_source]
        operational = [r for r in source if r.operational]
        historical = [r for r in source if not r.operational]

        def by_category(rows: Iterable[LedgerRow]) -> dict[str, int]:
            found = Counter(r.category for r in rows)
            return {c: found.get(c, 0) for c in Category.values}

        return {
            "rows": len(self.rows),
            "source_topics": len(source),
            "juristid_only": len(self.rows) - len(source),
            "operational": len(operational),
            "historical": len(historical),
            "by_category": by_category(self.rows),
            "by_category_operational": by_category(operational),
            "by_category_historical": by_category(historical),
            "by_reason": dict(sorted(Counter(r.reason for r in self.rows).items())),
            "excluded_by_reason": dict(
                sorted(
                    Counter(r.reason for r in self.rows if r.category == Category.EXCLUDED).items()
                )
            ),
            "activity": dict(sorted(Counter(r.activity for r in source).items())),
            "stage_mismatch": sum(1 for r in self.rows if r.stage_mismatch),
            "open_closed_mismatch": sum(1 for r in self.rows if r.open_closed_mismatch),
            "continuation_mismatch": sum(1 for r in self.rows if r.continuation_mismatch),
            "superseded_in_source": sum(1 for r in source if r.superseded_in_source),
            "tombstone_noted": sum(
                1 for r in self.rows if {TOMBSTONE_UUID, TOMBSTONE_REFERENCE} & set(r.notes)
            ),
            "with_candidates": sum(1 for r in self.rows if r.candidates),
            "decided": dict(
                sorted(Counter(r.decision["kind"] for r in self.rows if r.decision).items())
            ),
        }

    def document(self) -> dict[str, Any]:
        body = {
            "format": LEDGER_FORMAT,
            "version": self.version,
            "taken_at": self.taken_at,
            "source_sha256": self.source_sha256,
            "counts": self.counts(),
            "decisions": self.decisions,
            "decisions_sha256": self.decisions_sha256,
            "rows": [r.as_dict() for r in self.rows],
        }
        return {**body, "sha256": canonical_sha256(body)}

    def sha256(self) -> str:
        return str(self.document()["sha256"])

    def by_category(self, category: str) -> list[LedgerRow]:
        return [r for r in self.rows if r.category == category]


# ---------------------------------------------------------------------------
# What Juristid holds (read only)
# ---------------------------------------------------------------------------


def _page_guid(*values: str) -> str:
    for value in values:
        match = _GUID.search(value or "")
        if match:
            return match.group(0).upper()
    return ""


@dataclass
class _Juristid:
    #: Live Matter pk → (reference, is_open, superseded_by pk, stage key).
    matters: dict[str, dict[str, Any]] = field(default_factory=dict)
    by_reference: dict[tuple[int, int], list[str]] = field(default_factory=dict)
    #: Lovable UUID → live Matter pks recording it (state or Lovable reference).
    holders: dict[str, set[str]] = field(default_factory=dict)
    #: Live Matter pk → every Lovable UUID it records.
    identities: dict[str, set[str]] = field(default_factory=dict)
    #: Lovable UUID → (Matter pk, cohort) of its synchronisation state.
    states: dict[str, tuple[str, str]] = field(default_factory=dict)
    active_cohorts: set[str] = field(default_factory=set)
    #: Live Matter pk → the source stage its import read (`row.hetkeseis`).
    imported_stage: dict[str, str] = field(default_factory=dict)
    tombstone_uuids: set[str] = field(default_factory=set)
    tombstone_references: set[str] = field(default_factory=set)
    #: Upper-case page GUID → archive page pks.
    pages: dict[str, list[str]] = field(default_factory=dict)
    #: Archive page pk → live Matter pks linking it.
    page_links: dict[str, set[str]] = field(default_factory=dict)
    #: Live Teemad with neither a reference nor a Lovable identity (an archive
    #: or OneNote-only Teema): year → [(pk, folded title)].
    unreferenced: dict[int, list[tuple[str, str]]] = field(default_factory=dict)
    #: Lovable UUID → the newest person's decision on it.
    decisions: dict[str, dict[str, Any]] = field(default_factory=dict)
    decision_index: list[list[str]] = field(default_factory=list)


def _read_juristid() -> _Juristid:
    """Everything the ledger compares against, in a handful of SELECTs."""
    from django.db.models.fields.json import KT

    from app.audit.enums import SecurityEventType
    from app.audit.models import SecurityAuditEvent
    from app.legacy_import.lovable_sync import MAPPING_VERSION, expected_cohorts
    from app.legacy_import.lovable_sync_models import LovableSyncState
    from app.legacy_import.models import EvidencePilotRecord, MatterSourceReference
    from app.legacy_import.source_pages import LegacySourcePage, MatterSourcePage
    from app.matters.models import Matter

    out = _Juristid()
    unreferenced: list[Any] = []
    for m in Matter.all_objects.filter(deleted_at__isnull=True).values(
        "pk",
        "reference_year",
        "reference_number",
        "is_open",
        "superseded_by_id",
        "stage__key",
        "title",
        "source_era",
        "received_date",
    ):
        pk = str(m["pk"])
        year, number = m["reference_year"], m["reference_number"]
        out.matters[pk] = {
            "reference": f"{year}_{number}" if year is not None and number is not None else "",
            "is_open": bool(m["is_open"]),
            "superseded_by": str(m["superseded_by_id"]) if m["superseded_by_id"] else None,
            "stage": m["stage__key"] or "",
        }
        if year is not None and number is not None:
            out.by_reference.setdefault((year, number), []).append(pk)
        else:
            unreferenced.append(m)

    def hold(uuid_text: Any, matter_id: Any) -> None:
        if not uuid_text:
            return
        key, pk = str(uuid_text).lower(), str(matter_id)
        if pk in out.matters:
            out.holders.setdefault(key, set()).add(pk)
            out.identities.setdefault(pk, set()).add(key)
        else:
            out.tombstone_uuids.add(key)

    out.active_cohorts = set(expected_cohorts())
    for source_id, matter_id, cohort in LovableSyncState._base_manager.values_list(
        "source_id", "matter_id", "cohort"
    ):
        out.states[str(source_id).lower()] = (str(matter_id), cohort)
        hold(source_id, matter_id)

    sync_prefix = MAPPING_VERSION.split("/")[0]
    references = (
        MatterSourceReference._base_manager.filter(source_system=SOURCE_SYSTEM)
        .annotate(lid=KT("source_row_raw__lovable__id"), stage=KT("source_row_raw__row__hetkeseis"))
        .values_list(
            "matter_id", "lid", "stage", "import_batch_id", "source_contract_version", "created_at"
        )
        .order_by("created_at")
    )
    for matter_id, lid, stage, batch, contract, _created in references:
        hold(lid, matter_id)
        imported = batch is not None and not str(contract or "").startswith(sync_prefix)
        if imported and str(matter_id) in out.matters:
            # The newest import's reading is what the Teema's stage came from.
            out.imported_stage[str(matter_id)] = text_value(stage)

    for lid in (
        EvidencePilotRecord._base_manager.filter(
            record_kind="MATTER", matter__deleted_at__isnull=False
        )
        .annotate(lid=KT("evidence__lovable_id"))
        .values_list("lid", flat=True)
    ):
        if lid:
            out.tombstone_uuids.add(str(lid).lower())

    for reference in (
        SecurityAuditEvent.objects.filter(
            event_type__in=[
                SecurityEventType.MATTER_DELETED,
                SecurityEventType.REFERENCE_RELEASED,
            ]
        )
        .annotate(ref=KT("detail__reference"))
        .values_list("ref", flat=True)
    ):
        if reference:
            out.tombstone_references.add(str(reference))

    for page, page_id, page_key in LegacySourcePage.objects.values_list(
        "pk", "source_page_id", "page_key"
    ):
        guid = _page_guid(page_id, page_key)
        if guid:
            out.pages.setdefault(guid, []).append(str(page))
    for page_pk, matter_id in MatterSourcePage.objects.values_list("source_page_id", "matter_id"):
        if str(matter_id) in out.matters:
            out.page_links.setdefault(str(page_pk), set()).add(str(matter_id))

    for teema in unreferenced:
        pk = str(teema["pk"])
        year = _teema_year(teema["source_era"], teema["received_date"])
        title = _folded_title(teema["title"])
        if out.identities.get(pk) or year is None or not title:
            continue
        out.unreferenced.setdefault(year, []).append((pk, title))

    for decision in LovableInventoryDecision.objects.order_by("created_at", "id").values(
        "id", "source_id", "decision", "matter_id", "reason"
    ):
        decided = str(decision["source_id"]).lower()
        out.decision_index.append([str(decision["id"]), decided, decision["decision"]])
        out.decisions[decided] = {  # append-only: the newest decision stands
            "id": str(decision["id"]),
            "kind": decision["decision"],
            "matter_id": str(decision["matter_id"]) if decision["matter_id"] else None,
            "reason": decision["reason"],
        }
    out.decision_index.sort()
    return out


def decision_index() -> list[list[str]]:
    """Every inventory decision as ``[id, source_id, kind]``, sorted — what a
    ledger and a backfill package bind. Reads only."""
    return sorted(
        [str(pk), str(source_id).lower(), kind]
        for pk, source_id, kind in LovableInventoryDecision.objects.values_list(
            "id", "source_id", "decision"
        )
    )


_YEAR = re.compile(r"^(19|20)\d{2}$")


def _teema_year(source_era: Any, received: Any) -> int | None:
    """A Teema without a reference: its source era's year, else its arrival's."""
    if _YEAR.match(str(source_era or "").strip()):
        return int(str(source_era).strip())
    return received.year if received else None


def _stage_mapping() -> dict[str, str] | None:
    """The reader's verified Lovable-stage → stage-key mapping, when it is in
    this tree (`lovable_reading.STAGE_MAPPING`, agent B); otherwise ``None``."""
    try:
        lovable_reading = importlib.import_module(READING_MODULE)
    except ImportError:
        return None
    mapping = getattr(lovable_reading, "STAGE_MAPPING", None)
    return dict(mapping) if isinstance(mapping, dict) else None


# ---------------------------------------------------------------------------
# Building it
# ---------------------------------------------------------------------------


def _folded_title(raw: Any) -> str:
    return _NON_WORD.sub(" ", name_key(raw)).strip()


def near_identical(a: str, b: str) -> bool:
    """Two folded titles that read as the same — a suggestion, never an identity."""
    if not a or not b:
        return False
    if a == b:
        return True
    if min(len(a), len(b)) < NEAR_TITLE_MIN_LENGTH:
        return False
    if _DIGITS.findall(a) != _DIGITS.findall(b):
        # «… direktiiv 2024/1» and «… 2024/2» differ where it matters.
        return False
    matcher = difflib.SequenceMatcher(None, a, b, autojunk=False)
    return (
        matcher.real_quick_ratio() >= NEAR_TITLE_RATIO
        and matcher.quick_ratio() >= NEAR_TITLE_RATIO
        and matcher.ratio() >= NEAR_TITLE_RATIO
    )


def _require_full_reading(document: dict[str, Any]) -> None:
    try:
        verify_observation(document)
    except SourceError as error:
        raise InventoryError(error.code, str(error)) from error
    detail = document.get("detail") or {}
    missing = [e["id"] for e in document.get("index") or [] if e["id"] not in detail]
    if missing:
        raise InventoryError(
            "NOT_FULL_DETAIL",
            f"{len(missing)} live topic(s) have no detail; the ledger needs a full reading "
            '(take_observation(detail="all")).',
        )


def _activity_bundle(entry: dict[str, Any], values: dict[str, Any]) -> dict[str, Any]:
    """The minimum `source_activity` reads, rebuilt from the canonical detail."""
    return {
        "row": {
            "id": entry["id"],
            "operatiivne_ulatus": bool(entry.get("operational")),
            "hetkeseis": values.get("stage") or "",
        },
        "stage_links": [
            {"prev": pair[0], "next": pair[1]} for pair in values.get("stage_links") or []
        ],
    }


def build_ledger(document: dict[str, Any], *, now: dt.datetime | None = None) -> Ledger:
    """One row per source topic of a full reading, plus one per live Lovable-derived
    Teema whose topic the reading no longer holds. Reads Juristid; writes nothing.

    ``now`` is accepted for the interface (and recorded nowhere): the ledger
    depends only on the reading and the database."""
    del now
    _require_full_reading(document)
    index: list[dict[str, Any]] = sorted(document["index"], key=lambda e: e["id"])
    detail: dict[str, Any] = document["detail"]
    held = _read_juristid()
    mapping = _stage_mapping()

    by_nr: dict[str, list[str]] = defaultdict(list)
    for entry in index:
        by_nr[str(entry.get("nr") or "")].append(entry["id"])
    linked_pairs: set[frozenset[str]] = set()
    for values in detail.values():
        for prev, nxt in values.get("stage_links") or []:
            linked_pairs.add(frozenset((str(prev), str(nxt))))
    exact_matter: dict[str, str] = {}
    for source_id, pks in held.holders.items():
        if len(pks) == 1:
            exact_matter[source_id] = next(iter(pks))

    rows: dict[str, LedgerRow] = {}
    for entry in index:
        source_id = str(entry["id"]).lower()
        values = detail[entry["id"]]
        reference = str(entry.get("nr") or "")
        parsed = parse_reference(reference)
        row = LedgerRow(
            source_id=source_id,
            reference=reference,
            year=entry.get("aasta") if isinstance(entry.get("aasta"), int) else None,
            operational=bool(entry.get("operational")),
            activity=source_activity(_activity_bundle(entry, values)),
            category=Category.ABSENT,
            reason=PROVEN_ABSENT,
            superseded_in_source=any(
                str(prev).lower() == source_id for prev, _n in values.get("stage_links") or []
            ),
        )
        if len(by_nr[reference]) > 1:
            row.notes.append(SHARED_SOURCE_REFERENCE)
        _categorise(row, entry, values, parsed, held, by_nr)
        if row.matter_id:
            _mismatches(row, values, held, mapping, exact_matter)
        if source_id in held.tombstone_uuids:
            row.notes.append(TOMBSTONE_UUID)
        if reference in held.tombstone_references and row.category != Category.EXACT:
            row.notes.append(TOMBSTONE_REFERENCE)
        rows[source_id] = row

    _near_titles(rows, index, detail, linked_pairs)
    _near_teema_titles(rows, index, detail, held)
    _teema_named_twice(rows)
    _apply_decisions(rows, held)

    # A live Lovable-derived Teema whose topic the reading no longer holds.
    seen = set(rows)
    for pk, identities in sorted(held.identities.items()):
        for source_id in sorted(identities - seen):
            matter = held.matters[pk]
            state = held.states.get(source_id)
            rows[source_id] = LedgerRow(
                source_id=source_id,
                reference=matter["reference"],
                year=None,
                operational=False,
                activity=ACTIVITY_MISSING,
                category=Category.DELETED_OR_SUPERSEDED,
                reason=SOURCE_MISSING,
                matter_id=pk,
                matter_reference=matter["reference"],
                cohort=state[1] if state else "",
                in_source=False,
            )
            seen.add(source_id)

    ledger = Ledger(
        taken_at=str(document.get("taken_at") or ""),
        source_sha256=str(document.get("sha256") or ""),
        rows=sorted(rows.values(), key=lambda r: (not r.in_source, r.reference, r.source_id)),
        decisions=held.decision_index,
    )
    ledger.check()
    return ledger


def _candidate(source_id: str, reference: str, matter_id: str | None, basis: str) -> dict[str, Any]:
    return {
        "source_id": source_id,
        "reference": reference,
        "matter_id": matter_id,
        "basis": basis,
    }


def _categorise(
    row: LedgerRow,
    entry: dict[str, Any],
    values: dict[str, Any],
    parsed: tuple[int, int] | None,
    held: _Juristid,
    by_nr: dict[str, list[str]],
) -> None:
    holders = held.holders.get(row.source_id, set())
    if len(holders) > 1:
        row.category, row.reason = Category.POSSIBLE_DUPLICATE, UUID_ON_SEVERAL_TEEMAD
        row.candidates = [
            _candidate(row.source_id, held.matters[pk]["reference"], pk, "SAME_UUID")
            for pk in sorted(holders)
        ]
        return
    if holders:
        pk = next(iter(holders))
        row.category = Category.EXACT
        row.matter_id, row.matter_reference = pk, held.matters[pk]["reference"]
        state = held.states.get(row.source_id)
        if state is None:
            row.reason = EXACT_NOT_ENROLLED
        else:
            row.cohort = state[1]
            row.reason = EXACT_ENROLLED if state[1] in held.active_cohorts else EXACT_COHORT_RETIRED
        if row.matter_reference != row.reference:
            row.notes.append(RENUMBERED_IN_SOURCE)
        return

    year = entry.get("aasta")
    if parsed is None:
        row.category, row.reason = Category.EXCLUDED, NO_VALID_REFERENCE
        return
    if year is not None and str(year) != str(parsed[0]):
        row.category, row.reason = Category.EXCLUDED, REFERENCE_YEAR_MISMATCH
        return

    others = [i for i in by_nr[row.reference] if str(i).lower() != row.source_id]
    if others:
        row.category, row.reason = Category.POSSIBLE_DUPLICATE, SOURCE_REFERENCE_SHARED
        for other in sorted(others):
            pks = held.holders.get(str(other).lower(), set())
            matter = next(iter(pks)) if len(pks) == 1 else None
            row.candidates.append(
                _candidate(str(other).lower(), row.reference, matter, "SHARED_REFERENCE")
            )
        return

    same = held.by_reference.get(parsed, [])
    if same:
        unidentified = [pk for pk in same if not held.identities.get(pk)]
        if len(same) > 1:
            row.category, row.reason = Category.POSSIBLE_DUPLICATE, REFERENCE_ON_SEVERAL_TEEMAD
        elif unidentified:
            row.category, row.reason = Category.LINK_ENRICHMENT, SAME_REFERENCE_NO_IDENTITY
            row.matter_id = unidentified[0]
            row.matter_reference = held.matters[unidentified[0]]["reference"]
            return
        else:
            row.category = Category.POSSIBLE_DUPLICATE
            row.reason = REFERENCE_HELD_BY_OTHER_IDENTITY
        row.candidates = [
            _candidate("", held.matters[pk]["reference"], pk, "SAME_REFERENCE")
            for pk in sorted(same)
        ]
        return

    guids = _link_guids(values.get("onenote_link"))
    pages = sorted({page for guid in guids for page in held.pages.get(guid, [])})
    if pages:
        row.category, row.reason = Category.ARCHIVE_UNLINKED, ARCHIVE_PAGE_GUID
        row.archive_pages = pages
        for page in pages:
            for pk in sorted(held.page_links.get(page, set())):
                row.candidates.append(
                    _candidate("", held.matters[pk]["reference"], pk, "SHARED_ARCHIVE_PAGE")
                )


def _link_guids(link: Any) -> frozenset[str]:
    from app.legacy_import.onenote_capture import register_page_guids

    guids = register_page_guids(str(link or ""))
    # A link naming two pages (its halves disagree) proves neither.
    return guids if len(guids) == 1 else frozenset()


def _mismatches(
    row: LedgerRow,
    values: dict[str, Any],
    held: _Juristid,
    mapping: dict[str, str] | None,
    exact_matter: dict[str, str],
) -> None:
    matter = held.matters[str(row.matter_id)]
    source_stage = text_value(values.get("stage"))
    mapped = None
    if mapping is not None:
        mapped = mapping.get(source_stage, mapping.get(name_key(source_stage)))
    if mapped is not None:
        row.stage_basis = "STAGE_MAPPING"
        row.stage_mismatch = mapped != matter["stage"]
    elif str(row.matter_id) in held.imported_stage:
        # No verified mapping in this tree: the source stage the Teema was read
        # from against the source stage now.
        row.stage_basis = "IMPORTED_SOURCE_STAGE"
        row.stage_mismatch = name_key(held.imported_stage[str(row.matter_id)]) != name_key(
            source_stage
        )
    row.open_closed_mismatch = (row.activity == ACTIVITY_ONGOING) != matter["is_open"]
    successors = {
        str(nxt).lower()
        for prev, nxt in values.get("stage_links") or []
        if str(prev).lower() == row.source_id
    }
    own = matter["superseded_by"]
    if not successors and not own:
        row.continuation_mismatch = ""
    elif successors and not own:
        row.continuation_mismatch = SOURCE_ONLY_CONTINUATION
    elif own and not successors:
        row.continuation_mismatch = JURISTID_ONLY_CONTINUATION
    else:
        mapped_successors = {exact_matter.get(s) for s in successors}
        row.continuation_mismatch = "" if own in mapped_successors else SUCCESSOR_DIFFERS


def _near_titles(
    rows: dict[str, LedgerRow],
    index: list[dict[str, Any]],
    detail: dict[str, Any],
    linked_pairs: set[frozenset[str]],
) -> None:
    """Same-year near-identical titles: candidates on every row that is not
    EXACT, and an ABSENT row becomes POSSIBLE_DUPLICATE (kept out of the backfill)."""
    by_year: dict[Any, list[tuple[str, str]]] = defaultdict(list)
    for entry in index:
        title = _folded_title((detail[entry["id"]] or {}).get("title"))
        if title:
            by_year[entry.get("aasta")].append((str(entry["id"]).lower(), title))
    for topics in by_year.values():
        for i, (a, title_a) in enumerate(topics):
            for b, title_b in topics[i + 1 :]:
                if frozenset((a, b)) in linked_pairs or not near_identical(title_a, title_b):
                    continue
                for me, other in ((a, b), (b, a)):
                    row = rows[me]
                    if row.category == Category.EXACT:
                        continue
                    peer = rows[other]
                    row.candidates.append(
                        _candidate(other, peer.reference, peer.matter_id, "NEAR_IDENTICAL_TITLE")
                    )
                    if row.category == Category.ABSENT:
                        row.category, row.reason = (
                            Category.POSSIBLE_DUPLICATE,
                            NEAR_IDENTICAL_TITLE,
                        )


def _near_teema_titles(
    rows: dict[str, LedgerRow],
    index: list[dict[str, Any]],
    detail: dict[str, Any],
    held: _Juristid,
) -> None:
    """Same-year near-identical titles against live Teemad that hold neither a
    reference nor a Lovable identity (an archive or OneNote-only Teema), so the
    backfill cannot create a second one beside it. Suggestions only: a candidate
    on every row that is not EXACT, and ABSENT becomes POSSIBLE_DUPLICATE."""
    if not held.unreferenced:
        return
    for entry in index:
        row = rows[str(entry["id"]).lower()]
        if row.category == Category.EXACT or not isinstance(entry.get("aasta"), int):
            continue
        title = _folded_title((detail[entry["id"]] or {}).get("title"))
        for pk, teema_title in held.unreferenced.get(entry["aasta"], []):
            if not near_identical(title, teema_title):
                continue
            row.candidates.append(_candidate("", "", pk, "NEAR_IDENTICAL_TEEMA_TITLE"))
            if row.category == Category.ABSENT:
                row.category, row.reason = (
                    Category.POSSIBLE_DUPLICATE,
                    NEAR_IDENTICAL_TEEMA_TITLE,
                )


def _apply_decisions(rows: dict[str, LedgerRow], held: _Juristid) -> None:
    """A person's newest decision on each topic (`LovableInventoryDecision`).

    EXCLUDE → EXCLUDED (``DECIDED_EXCLUDE``, the decision kept on the row);
    LINK → reported as a decided link to the named Teema (LINK_ENRICHMENT,
    ``DECIDED_LINK``), never backfilled; NOT_SAME → the title-similarity
    candidates it settles (that Teema's, or every one when it names none) are
    dropped, and a row they alone made POSSIBLE_DUPLICATE is ABSENT again. An
    identity or reference collision is never settled by a decision, and an
    EXACT row (approved provenance) is never overridden."""
    for source_id, decision in held.decisions.items():
        row = rows.get(source_id)
        if row is None:
            continue
        row.decision = decision
        if row.category in (Category.EXACT, Category.DELETED_OR_SUPERSEDED):
            row.notes.append(DECISION_NOT_APPLIED)
            continue
        kind = decision["kind"]
        if kind == InventoryDecisionKind.EXCLUDE:
            row.category, row.reason = Category.EXCLUDED, DECIDED_EXCLUDE
        elif kind == InventoryDecisionKind.LINK:
            row.category, row.reason = Category.LINK_ENRICHMENT, DECIDED_LINK
            row.matter_id = decision["matter_id"]
            row.matter_reference = (
                held.matters.get(str(decision["matter_id"]), {}).get("reference", "")
                if decision["matter_id"]
                else ""
            )
        elif kind == InventoryDecisionKind.NOT_SAME:
            settled = [
                c
                for c in row.candidates
                if c["basis"] in TITLE_BASES
                and (decision["matter_id"] is None or c["matter_id"] == decision["matter_id"])
            ]
            row.candidates = [c for c in row.candidates if c not in settled]
            row.notes.append(DECIDED_NOT_SAME)
            if (
                row.category == Category.POSSIBLE_DUPLICATE
                and row.reason in TITLE_BASES
                and not any(c["basis"] in TITLE_BASES for c in row.candidates)
            ):
                row.category, row.reason = Category.ABSENT, PROVEN_ABSENT


def _teema_named_twice(rows: dict[str, LedgerRow]) -> None:
    """One Teema recording two live topics' identities: neither is exact."""
    named: dict[str, list[LedgerRow]] = defaultdict(list)
    for row in rows.values():
        if row.category == Category.EXACT and row.matter_id:
            named[row.matter_id].append(row)
    for matter_id, group in named.items():
        if len(group) < 2:
            continue
        for row in group:
            row.category, row.reason = (
                Category.POSSIBLE_DUPLICATE,
                TEEMA_HOLDS_SEVERAL_IDENTITIES,
            )
            row.candidates += [
                _candidate(other.source_id, other.reference, matter_id, "SAME_TEEMA")
                for other in group
                if other is not row
            ]


# ---------------------------------------------------------------------------
# Reading, storing, reporting
# ---------------------------------------------------------------------------


def read_full(
    transport: Transport,
    *,
    now: Callable[[], dt.datetime] | None = None,
    pause: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    """A full, verified reading of every live topic, through the given (GET-only)
    transport, under the synchronisation's run lock so no nightly run reads at
    the same time. Writes nothing; creates no control row."""
    from app.legacy_import.lovable_sync_models import LovableSyncControl

    ctl = LovableSyncControl.objects.filter(pk=1).first()
    if ctl is not None and ctl.cut_over:
        raise SyncRefused("CUT_OVER", "Final cutover is recorded; nothing reads Lovable again.")
    with run_lock():
        document = take_observation(transport, now=now, pause=pause, detail=DETAIL_ALL)
    document.pop("children_rows", None)
    verify_observation(document)
    return document


def latest_inventory() -> LovableInventoryRun | None:
    """The newest stored, successful inventory (for the admin page). Reads only."""
    return (
        LovableInventoryRun.objects.filter(status=InventoryRunStatus.SUCCEEDED)
        .order_by("-created_at")
        .first()
    )


def store_run(ledger: Ledger, *, trigger: str = InventoryTrigger.LEDGER) -> LovableInventoryRun:
    """The one write of this module: the ledger as a `LovableInventoryRun`."""
    document = ledger.document()
    taken = None
    if ledger.taken_at:
        try:
            taken = dt.datetime.fromisoformat(ledger.taken_at)
        except ValueError:
            taken = None
    return LovableInventoryRun.objects.create(
        trigger=trigger,
        status=InventoryRunStatus.SUCCEEDED,
        taken_at=taken,
        source_sha256=ledger.source_sha256,
        reader_version=ledger.version,
        counts=document["counts"],
        rows=document["rows"],
        ledger_sha256=document["sha256"],
    )


def store_failure(code: str, message: str, *, trigger: str) -> LovableInventoryRun:
    return LovableInventoryRun.objects.create(
        trigger=trigger,
        status=InventoryRunStatus.FAILED,
        reader_version=INVENTORY_VERSION,
        error=f"{code}: {message}"[:300],
    )


CSV_COLUMNS = (
    "source_id",
    "reference",
    "year",
    "operational",
    "activity",
    "category",
    "reason",
    "matter_id",
    "matter_reference",
    "cohort",
    "notes",
    "candidates",
    "archive_pages",
    "stage_mismatch",
    "stage_basis",
    "open_closed_mismatch",
    "continuation_mismatch",
    "superseded_in_source",
    "in_source",
)


def ledger_csv(ledger: Ledger) -> str:
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow(CSV_COLUMNS)
    for row in ledger.rows:
        data = row.as_dict()
        data["notes"] = " ".join(row.notes)
        data["archive_pages"] = " ".join(row.archive_pages)
        data["candidates"] = " ".join(
            f"{c['basis']}:{c['reference'] or c['source_id']}" for c in row.candidates
        )
        writer.writerow(["" if data[c] is None else data[c] for c in CSV_COLUMNS])
    return out.getvalue()


def coverage_markdown(ledger: Ledger) -> str:
    """The coverage report (brief §5 A3): the totals, then the per-record ledger.
    References, codes and counts only."""
    counts = ledger.counts()
    lines = [
        "# Lovable register coverage",
        "",
        f"- Reading taken at: {ledger.taken_at}",
        f"- Reading SHA-256: `{ledger.source_sha256}`",
        f"- Ledger SHA-256: `{ledger.sha256()}` ({ledger.version})",
        "",
        "## Totals",
        "",
        f"- Ledger rows: {counts['rows']} (source topics {counts['source_topics']}, "
        f"Juristid-only {counts['juristid_only']})",
        f"- Operational: {counts['operational']}; historical (non-operational): "
        f"{counts['historical']}",
        "",
        "| Category | All | Operational | Historical |",
        "|---|---:|---:|---:|",
    ]
    for category in Category.values:
        lines.append(
            f"| {category} | {counts['by_category'][category]} | "
            f"{counts['by_category_operational'][category]} | "
            f"{counts['by_category_historical'][category]} |"
        )
    lines += [
        "",
        f"- Excluded by reason: {counts['excluded_by_reason'] or 'none'}",
        f"- Reasons: {counts['by_reason']}",
        f"- Source activity: {counts['activity']}",
        f"- Stage mismatch: {counts['stage_mismatch']}; open/closed mismatch: "
        f"{counts['open_closed_mismatch']}; continuation mismatch: "
        f"{counts['continuation_mismatch']}",
        f"- Superseded in source (stage-link predecessor, flagged, not excluded): "
        f"{counts['superseded_in_source']}",
        f"- Absent or other rows noted on a tombstone: {counts['tombstone_noted']}",
        f"- Rows with candidates (suggestions only): {counts['with_candidates']}",
        "",
        "## Ledger",
        "",
        "| Reference | Lovable id | Op. | Activity | Category | Reason | Teema | Notes "
        "| Candidates | Stage ≠ | Open ≠ | Continuation ≠ |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for row in ledger.rows:
        candidates = ", ".join(
            f"{c['basis']} {c['reference'] or c['source_id']}" for c in row.candidates
        )
        lines.append(
            f"| {row.reference} | {row.source_id} | {'yes' if row.operational else ''} | "
            f"{row.activity} | {row.category} | {row.reason} | {row.matter_reference} | "
            f"{' '.join(row.notes)} | {candidates} | {_flag(row.stage_mismatch)} | "
            f"{_flag(row.open_closed_mismatch)} | {row.continuation_mismatch or ''} |"
        )
    return "\n".join(lines) + "\n"


def _flag(value: bool | None) -> str:
    return "" if value is None else ("yes" if value else "no")
