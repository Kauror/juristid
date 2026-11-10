"""How far the OneNote material of the operational Teemad has got (owner brief §8 D1, D5).

Read-only except for ``mark_pages`` / ``record_capture_marks``, which append
progress marks (``OneNoteCoverageMark``) and write nothing else.

**Operational coverage, not the archive.** The pages counted here are the pages
the live register Teemad (``lovable-register``) name by an exact desktop page
GUID, plus any page Juristid already catalogued. Juristid does not hold the
notebook's page or section list, so it never claims that the archive — every
section, every page — is covered: ``coverage_summary()["archive_complete"]`` is
always ``False``, and sections are reported only as far as a section mark (the
section-by-section process, brief §10) has recorded them.

**State of a page.** Its latest mark; with none, what the records prove:

* no catalogued page row → ``NOT_INSPECTED``;
* a row whose page is linked by several live Teemad, or one of whose files is
  held for a person's decision → ``AWAITING_ASSOCIATION``;
* otherwise → ``IMPORTED``.

**What to inspect next** (``worklist``) is never the whole notebook: the
operational pages with no capture, the pages of Teemad whose Lovable record or
OneNote link changed since the last capture, a link Lovable now names but the
register has not accepted, and pages a mark sends back for inspection.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from django.db import transaction
from django.utils import timezone

from app.core.errors import DomainError
from app.legacy_import.onenote_coverage_models import (
    FINAL_HOLD_DECISIONS,
    CoverageScope,
    HoldDecisionKind,
    HoldScope,
    OneNoteCoverageMark,
    OneNoteHoldDecision,
    PageState,
)

__all__ = [
    "HoldDecisionRefused",
    "HoldNotFound",
    "PageState",
    "WorkItem",
    "coverage_summary",
    "decide_hold",
    "held_counts",
    "held_item",
    "held_items",
    "item_token",
    "mark_pages",
    "mark_section",
    "page_states",
    "record_capture_marks",
    "register_page_map",
    "worklist",
]

#: Latest marks that send a page back to the worklist.
REINSPECT_STATES = frozenset({PageState.NOT_INSPECTED, PageState.NEW_MATERIAL, PageState.FAILED})
#: Latest marks that say a page has been read since a trigger.
SETTLED_STATES = frozenset(
    {
        PageState.NO_CHANGES,
        PageState.CAPTURE_PREPARED,
        PageState.VERIFIED,
        PageState.IMPORTED,
        PageState.AWAITING_ASSOCIATION,
    }
)

#: Worklist reasons.
REASON_NO_CAPTURE = "NO_CAPTURE"
REASON_LOVABLE_CHANGED = "LOVABLE_RECORD_CHANGED"
REASON_LINK_CHANGED = "LOVABLE_LINK_CHANGED"
REASON_OBSERVED_LINK = "OBSERVED_LINK_NOT_APPLIED"
REASON_MARKED = "MARKED_FOR_REINSPECTION"


def _guid(value: str) -> str:
    return (value or "").strip().strip("{}").upper()


# -- the register's pages ----------------------------------------------------------


@dataclass(frozen=True)
class RegisterTeema:
    matter_id: str
    lovable_source_id: str
    reference: str
    is_open: bool


@dataclass
class RegisterMap:
    """Page GUID -> the live register Teemad whose stored link names exactly that page."""

    pages: dict[str, list[RegisterTeema]] = field(default_factory=dict)
    teemad: int = 0
    open_teemad: int = 0
    without_page_link: int = 0
    ambiguous_link: int = 0
    #: matter id -> its one page GUID.
    page_of: dict[str, str] = field(default_factory=dict)


def register_page_map() -> RegisterMap:
    """Read the register's own OneNote links (applied, else the source reference).

    The same reading the capture plan checks (``onenote_capture._stored_onenote_link``
    and ``register_page_guids``): a link names a page only through the last GUID
    of its ``wd=target(…)`` or the ``page-id`` of its client half; a link naming
    two pages names none here.
    """
    from app.legacy_import.lovable_sync_models import LovableSyncState
    from app.legacy_import.models import MatterSourceReference
    from app.legacy_import.onenote_capture import REGISTER_COHORT, register_page_guids

    states = list(
        LovableSyncState.objects.filter(cohort=REGISTER_COHORT, matter__deleted_at__isnull=True)
        .select_related("matter")
        .only("matter_id", "source_id", "reference", "applied", "matter__is_open")
        .order_by("reference")
    )
    fallback: dict[str, str] = {}
    for matter_id, url in (
        MatterSourceReference.objects.filter(
            matter_id__in=[s.matter_id for s in states], source_system="LOVABLE"
        )
        .exclude(onenote_url="")
        .order_by("created_at", "id")
        .values_list("matter_id", "onenote_url")
    ):
        fallback[str(matter_id)] = url  # the latest wins, as in the capture plan
    out = RegisterMap()
    for state in states:
        out.teemad += 1
        out.open_teemad += int(state.matter.is_open)
        guids = register_page_guids(_applied_link(state) or fallback.get(str(state.matter_id), ""))
        if not guids:
            out.without_page_link += 1
            continue
        if len(guids) > 1:
            out.ambiguous_link += 1
            continue
        (guid,) = guids
        out.pages.setdefault(guid, []).append(
            RegisterTeema(
                str(state.matter_id), str(state.source_id), state.reference, state.matter.is_open
            )
        )
        out.page_of[str(state.matter_id)] = guid
    return out


def _applied_link(state: Any) -> str:
    applied = (state.applied or {}).get("onenote_link")
    if isinstance(applied, dict):
        halves = [str(applied.get(k) or "").strip() for k in ("source", "target")]
        return " ".join(h for h in halves if h)
    return str(applied or "")


# -- states ------------------------------------------------------------------------


def _latest_marks(scope: str = CoverageScope.PAGE) -> dict[str, OneNoteCoverageMark]:
    latest: dict[str, OneNoteCoverageMark] = {}
    for mark in OneNoteCoverageMark.objects.filter(scope=scope).order_by(
        "recorded_at", "created_at"
    ):
        latest[mark.page_guid if scope == CoverageScope.PAGE else mark.section] = mark
    return latest


@dataclass(frozen=True)
class CataloguedPage:
    page_id: Any
    guid: str
    capture_id: str
    section: str
    by_capture: bool
    linked: int
    review_holds: int
    #: Held items on the page an administrator settled (``OneNoteHoldDecision``).
    settled: int = 0


def _catalogued() -> dict[str, CataloguedPage]:
    """Every desktop page row Juristid holds, by GUID, with what blocks its completion."""
    from django.db.models import Count, Q

    from app.legacy_import.onenote_capture import (
        BATCH_SOURCE_SYSTEM,
        HOLD_SHARED_PAGE,
        REVIEW_HOLDS,
    )
    from app.legacy_import.source_pages import (
        LegacySourcePage,
        ResourceImportState,
        SourceSystem,
    )

    out: dict[str, CataloguedPage] = {}
    rows = (
        LegacySourcePage.objects.filter(source_system=SourceSystem.ONENOTE_DESKTOP)
        .annotate(
            live_links=Count(
                "matter_links",
                filter=Q(matter_links__matter__deleted_at__isnull=True),
                distinct=True,
            ),
            held=Count(
                "resources__imports",
                filter=Q(
                    resources__imports__state=ResourceImportState.SKIPPED,
                    resources__imports__error_code__in=sorted({HOLD_SHARED_PAGE, *REVIEW_HOLDS}),
                    resources__imports__matter_source_page__matter__deleted_at__isnull=True,
                ),
                distinct=True,
            ),
            # A later capture's reading held for a person (OneNoteResourceRevision
            # HELD) stays pending until resolved; no capture ever accepts it.
            held_readings=Count(
                "resources__revisions",
                filter=Q(
                    resources__revisions__state="HELD",
                    resources__revisions__matter_source_page__matter__deleted_at__isnull=True,
                ),
                distinct=True,
            ),
        )
        .values_list(
            "pk",
            "page_key",
            "capture_id",
            "source_section",
            "import_batch__source_system",
            "live_links",
            "held",
            "held_readings",
        )
    )
    settled = _settled_by_page()
    for pk, key, capture_id, section, system, links, held_rows, held_readings in rows:
        held = held_rows + held_readings - settled.get(pk, 0)
        if not key.lower().startswith("p-"):
            continue
        guid = _guid(key[2:])
        out[guid] = CataloguedPage(
            pk,
            guid,
            capture_id,
            section,
            system == BATCH_SOURCE_SYSTEM,
            links,
            held,
            settled.get(pk, 0),
        )
    return out


def _settled_by_page() -> Counter[Any]:
    """Page id -> held rows and readings on it that a final decision settled."""
    from app.legacy_import.onenote_capture import HOLD_SHARED_PAGE, REVIEW_HOLDS
    from app.legacy_import.onenote_coverage_models import OneNoteResourceRevision
    from app.legacy_import.source_pages import LegacySourceResourceImport, ResourceImportState

    rows, readings = _settled_holds()
    out: Counter[Any] = Counter()
    if rows:
        out.update(
            LegacySourceResourceImport.objects.filter(
                pk__in=rows,
                state=ResourceImportState.SKIPPED,
                error_code__in=sorted({HOLD_SHARED_PAGE, *REVIEW_HOLDS}),
                matter_source_page__matter__deleted_at__isnull=True,
            ).values_list("resource__source_page_id", flat=True)
        )
    if readings:
        out.update(
            OneNoteResourceRevision.objects.filter(
                pk__in=readings,
                state="HELD",
                matter_source_page__matter__deleted_at__isnull=True,
            ).values_list("resource__source_page_id", flat=True)
        )
    return out


def _derived_state(page: CataloguedPage | None) -> str:
    if page is None:
        return PageState.NOT_INSPECTED
    if page.review_holds:
        return PageState.AWAITING_ASSOCIATION
    # A shared page waits for its files' association — until a person settled them.
    if page.linked > 1 and not page.settled:
        return PageState.AWAITING_ASSOCIATION
    return PageState.IMPORTED


def page_states(guids: list[str] | None = None) -> dict[str, dict[str, Any]]:
    """Page GUID -> {state, basis (MARK|RECORDS), at, capture_id} for these pages
    (default: every operational page and every catalogued page)."""
    catalogued = _catalogued()
    marks = _latest_marks()
    if guids is None:
        guids = sorted(set(register_page_map().pages) | set(catalogued))
    out = {}
    for raw in guids:
        guid = _guid(raw)
        mark = marks.get(guid)
        if mark is not None:
            out[guid] = {
                "state": mark.state,
                "basis": "MARK",
                "at": mark.recorded_at,
                "capture_id": mark.capture_id,
                "reason": mark.reason,
            }
        else:
            row = catalogued.get(guid)
            out[guid] = {
                "state": _derived_state(row),
                "basis": "RECORDS",
                "at": None,
                "capture_id": row.capture_id if row else "",
                "reason": "",
            }
    return out


# -- marks -------------------------------------------------------------------------


def mark_pages(
    guids: list[str],
    state: str,
    *,
    actor: Any = None,
    reason: str = "",
    capture_id: str = "",
    manifest_sha256: str = "",
) -> int:
    """Append one mark per page. Only a progress record: no business row changes."""
    if state not in PageState.values:
        raise ValueError(f"not a page state: {state!r}")
    from app.legacy_import.source_pages import LegacySourcePage, SourceSystem

    clean = sorted({_guid(g) for g in guids if _guid(g)})
    rows = {
        _guid(key[2:]): pk
        for pk, key in LegacySourcePage.objects.filter(
            source_system=SourceSystem.ONENOTE_DESKTOP,
            page_key__in=[f"p-{g.lower()}" for g in clean],
        ).values_list("pk", "page_key")
    }
    now = timezone.now()
    with transaction.atomic():
        OneNoteCoverageMark.objects.bulk_create(
            [
                OneNoteCoverageMark(
                    scope=CoverageScope.PAGE,
                    page_guid=g,
                    state=state,
                    capture_id=capture_id[:100],
                    manifest_sha256=manifest_sha256,
                    source_page_id=rows.get(g),
                    actor=actor if getattr(actor, "pk", None) else None,
                    reason=reason[:300],
                    recorded_at=now,
                )
                for g in clean
            ]
        )
    return len(clean)


def mark_section(
    section: str,
    state: str,
    *,
    actor: Any = None,
    reason: str = "",
    capture_id: str = "",
    manifest_sha256: str = "",
) -> None:
    if state not in PageState.values:
        raise ValueError(f"not a page state: {state!r}")
    if not section.strip():
        raise ValueError("name the section")
    OneNoteCoverageMark.objects.create(
        scope=CoverageScope.SECTION,
        section=section.strip()[:300],
        state=state,
        capture_id=capture_id[:100],
        manifest_sha256=manifest_sha256,
        actor=actor if getattr(actor, "pk", None) else None,
        reason=reason[:300],
    )


def record_capture_marks(
    capture: Any,
    outcomes: dict[str, str],
    *,
    actor: Any = None,
    reason: str = "",
) -> int:
    """Append the marks a capture step established: page key -> state.

    A page that still holds something for a person — a shared page, a file held
    for review, or a later reading held (``OneNoteResourceRevision`` HELD) — is
    never marked done: ``IMPORTED`` or ``NO_CHANGES`` becomes
    ``AWAITING_ASSOCIATION`` (independent review 2026-11-09, P2).
    """
    by_state: dict[str, list[str]] = {}
    guids = {p.page_key: p.page_guid for p in capture.pages}
    catalogued = _catalogued()
    for key, state in outcomes.items():
        if key not in guids:
            continue
        row = catalogued.get(_guid(guids[key]))
        if state in {PageState.IMPORTED, PageState.NO_CHANGES} and row is not None:
            if _derived_state(row) == PageState.AWAITING_ASSOCIATION:
                state = PageState.AWAITING_ASSOCIATION
        by_state.setdefault(state, []).append(guids[key])
    return sum(
        mark_pages(
            pages,
            state,
            actor=actor,
            reason=reason,
            capture_id=capture.capture_id,
            manifest_sha256=capture.manifest_sha256,
        )
        for state, pages in sorted(by_state.items())
    )


# -- the summary -------------------------------------------------------------------


def _last_capture() -> dict[str, Any] | None:
    import json

    from app.legacy_import.models import ImportBatch
    from app.legacy_import.onenote_capture import BATCH_SOURCE_SYSTEM

    batch = (
        ImportBatch.objects.filter(source_system=BATCH_SOURCE_SYSTEM)
        .order_by("-started_at", "-created_at")
        .first()
    )
    if batch is None:
        return None
    try:
        notes = json.loads(batch.notes or "{}")
    except ValueError:
        notes = {}
    return {
        "batch_id": str(batch.pk),
        "capture_id": notes.get("capture_id", ""),
        "package": batch.source_file_name,
        "started_at": batch.started_at,
        "finished_at": batch.finished_at,
        "status": batch.reconciliation_status,
    }


def _settled_holds() -> tuple[set[Any], set[Any]]:
    """(import row ids, revision ids) an administrator's final decision settled.

    A shared page's file is one item: its decision settles exactly the held rows
    it was shown (``observed["rows"]``). A Teema linked to the page afterwards has a
    row no decision saw; it stays open (``_shared_decisions``).
    """
    final = OneNoteHoldDecision.objects.filter(final=True)
    rows = set(final.exclude(resource_import=None).values_list("resource_import_id", flat=True))
    for decisions in _shared_decisions().values():
        for _key, seen in decisions:
            rows.update(seen)
    readings = set(final.exclude(revision=None).values_list("revision_id", flat=True))
    return rows, readings


def _shared_decisions(resource_ids: Any = None) -> dict[Any, list[tuple[str, set[Any]]]]:
    """Resource id -> [(item key, held row ids it settled)] of final shared-file decisions."""
    import uuid

    final = OneNoteHoldDecision.objects.filter(final=True, scope=HoldScope.SHARED_FILE)
    if resource_ids is not None:
        final = final.filter(resource_id__in=list(resource_ids))
    out: dict[Any, list[tuple[str, set[Any]]]] = {}
    for item_key, resource_id, observed in final.order_by("decided_at", "created_at").values_list(
        "item_key", "resource_id", "observed"
    ):
        seen = {uuid.UUID(r["id"]) for r in (observed or {}).get("rows", []) if r.get("id")}
        out.setdefault(resource_id, []).append((item_key, seen))
    return out


def coverage_summary() -> dict[str, Any]:
    """Operational coverage, the other catalogued pages, sections, the last capture.

    Every count is read from the records it names, so a page of the UI that shows
    them agrees with the database.
    """
    from django.db.models import Count

    from app.legacy_import.onenote_coverage_models import (
        OneNotePageRevision,
        OneNoteResourceRevision,
        RevisionState,
    )
    from app.legacy_import.source_pages import (
        LegacySourceResourceImport,
        ResourceImportState,
    )

    register = register_page_map()
    catalogued = _catalogued()
    marks = _latest_marks()
    operational = set(register.pages)

    def state_of(guid: str) -> str:
        mark = marks.get(guid)
        return mark.state if mark is not None else _derived_state(catalogued.get(guid))

    op_states = Counter(state_of(g) for g in operational)
    other = set(catalogued) - operational
    captured = {g for g in operational if g in catalogued}
    captured_by = Counter(catalogued[g].capture_id for g in captured)
    teemad_with_page = sum(len(v) for v in register.pages.values())
    teemad_captured = sum(len(register.pages[g]) for g in captured)
    open_without_capture = sum(
        1 for g in operational - captured for t in register.pages[g] if t.is_open
    )

    matter_ids = [t.matter_id for v in register.pages.values() for t in v]
    # A held item an administrator settled (``OneNoteHoldDecision``) is no longer
    # held: it is counted under ``held_decided`` instead. An association turned its
    # chosen Teema's row into an import, so that one already counts as imported.
    settled_rows, settled_readings = _settled_holds()
    imports = Counter(
        (state, code)
        for pk, state, code in LegacySourceResourceImport.objects.filter(
            matter_source_page__matter_id__in=matter_ids
        ).values_list("pk", "state", "error_code")
        if not (state == ResourceImportState.SKIPPED and pk in settled_rows)
    )
    held_decided = Counter(
        code
        for code in LegacySourceResourceImport.objects.filter(
            matter_source_page__matter_id__in=matter_ids,
            pk__in=settled_rows,
            state=ResourceImportState.SKIPPED,
        ).values_list("error_code", flat=True)
    )
    revisions = Counter(
        state
        for pk, state in OneNoteResourceRevision.objects.filter(
            matter_source_page__matter_id__in=matter_ids
        ).values_list("pk", "state")
        if not (state == RevisionState.HELD and pk in settled_readings)
    )
    readings_decided = OneNoteResourceRevision.objects.filter(
        matter_source_page__matter_id__in=matter_ids, pk__in=settled_readings
    ).count()
    section_marks = _latest_marks(CoverageScope.SECTION)
    sections_seen = Counter(catalogued[g].section for g in captured)
    return {
        "generated_at": timezone.now(),
        "archive_complete": False,
        "archive_note": (
            "Juristid ei hoia OneNote'i märkmiku lehtede ega sektsioonide täielikku loendit; "
            "katvus on mõõdetud registri teemade täpsete lehelinkide ja juba kataloogitud "
            "lehtede kaupa."
        ),
        "operational": {
            "teemad": register.teemad,
            "open_teemad": register.open_teemad,
            "teemad_with_exact_page": teemad_with_page,
            "teemad_without_page_link": register.without_page_link,
            "teemad_with_ambiguous_link": register.ambiguous_link,
            "teemad_with_captured_page": teemad_captured,
            "teemad_without_capture": teemad_with_page - teemad_captured,
            "open_teemad_without_capture": open_without_capture,
            "pages": len(operational),
            "shared_pages": sum(1 for v in register.pages.values() if len(v) > 1),
            "pages_captured": len(captured),
            "pages_not_captured": len(operational - captured),
            "captured_by": dict(captured_by),
            "states": {s: op_states.get(s, 0) for s in PageState.values},
            "attachments_imported": imports.get((ResourceImportState.IMPORTED, ""), 0),
            "attachments_held": {
                code: n
                for (state, code), n in sorted(imports.items())
                if state == ResourceImportState.SKIPPED
            },
            "attachments_failed": sum(
                n for (state, _c), n in imports.items() if state == ResourceImportState.FAILED
            ),
            "new_versions": revisions.get(RevisionState.VERSIONED, 0),
            "revisions_held": revisions.get(RevisionState.HELD, 0),
            "revisions_recorded": revisions.get(RevisionState.RECORDED, 0),
            # Settled by an administrator's decision (live-sync round 2).
            "attachments_held_decided": dict(sorted(held_decided.items())),
            "revisions_held_decided": readings_decided,
            "page_revisions": OneNotePageRevision.objects.filter(
                page_id__in=[catalogued[g].page_id for g in captured]
            ).count(),
        },
        "other_pages": {
            "pages": len(other),
            "states": dict(Counter(state_of(g) for g in other)),
            "linked_to_no_live_teema": sum(1 for g in other if catalogued[g].linked == 0),
        },
        "sections": {
            "with_operational_pages": len(sections_seen),
            "marked": {
                name: {"state": m.state, "at": m.recorded_at, "capture_id": m.capture_id}
                for name, m in sorted(section_marks.items())
            },
            "not_covered": sorted(
                name
                for name, m in section_marks.items()
                if m.state not in {PageState.IMPORTED, PageState.NO_CHANGES}
            ),
            "never_inventoried": "unknown",
        },
        "marks": OneNoteCoverageMark.objects.aggregate(n=Count("id"))["n"],
        "last_capture": _last_capture(),
    }


# -- the worklist ------------------------------------------------------------------


@dataclass(frozen=True)
class WorkItem:
    page_guid: str
    reasons: tuple[str, ...]
    teemad: tuple[RegisterTeema, ...]
    state: str
    since: datetime | None

    def as_dict(self) -> dict[str, Any]:
        return {
            "page_guid": self.page_guid,
            "reasons": list(self.reasons),
            "teemad": [t.__dict__ for t in self.teemad],
            "state": self.state,
        }


def _default_since() -> datetime | None:
    """The start of the latest capture batch that did not fail: changes after it."""
    from app.legacy_import.models import ImportBatch, ReconciliationStatus
    from app.legacy_import.onenote_capture import BATCH_SOURCE_SYSTEM

    batch = (
        ImportBatch.objects.filter(source_system=BATCH_SOURCE_SYSTEM)
        .exclude(reconciliation_status=ReconciliationStatus.FAILED)
        .order_by("-started_at")
        .first()
    )
    return batch.started_at if batch is not None else None


def _changed_since(since: datetime | None) -> tuple[dict[str, datetime], dict[str, set[str]]]:
    """Teemad whose Lovable *source value* moved after ``since``.

    Per (Teema, field): a decision in the window whose ``source`` differs from the
    one before it (the latest before the window, else the previous one in it). A
    pending difference re-reported night after night is not a change. Returns
    (matter id -> latest change time, matter id -> GUIDs named by changed links).
    """
    from app.legacy_import.lovable_sync_models import LovableSyncDecision, SyncOutcome
    from app.legacy_import.onenote_capture import register_page_guids

    window = LovableSyncDecision.objects.all()
    if since is not None:
        window = window.filter(created_at__gt=since)
    rows = list(
        window.order_by("created_at", "pk").values_list(
            "state__matter_id", "state_id", "field", "source", "outcome", "created_at"
        )
    )
    previous: dict[tuple[Any, str], Any] = {}
    if since is not None and rows:
        for state_id, name, source in (
            LovableSyncDecision.objects.filter(
                created_at__lte=since, state_id__in={r[1] for r in rows}
            )
            .order_by("state_id", "field", "-created_at")
            .distinct("state_id", "field")
            .values_list("state_id", "field", "source")
        ):
            previous[(state_id, name)] = source
    changed: dict[str, datetime] = {}
    links: dict[str, set[str]] = {}
    for matter_id, state_id, name, source, outcome, at in rows:
        key = (state_id, name)
        if key in previous:
            moved = previous[key] != source
        else:
            moved = outcome != SyncOutcome.UNCHANGED
        previous[key] = source
        if not moved or matter_id is None:
            continue
        mid = str(matter_id)
        changed[mid] = at  # rows are in time order: the latest change wins
        if name == "onenote_link":
            value = source.get("source") if isinstance(source, dict) else source
            links.setdefault(mid, set()).update(register_page_guids(str(value or "")))
    return changed, links


def worklist(*, since: datetime | None = None) -> list[WorkItem]:
    """The pages to inspect next, never the whole notebook.

    ``since`` defaults to the start of the latest capture that did not fail.
    """
    from app.legacy_import.lovable_sync_models import LovableSyncState
    from app.legacy_import.onenote_capture import REGISTER_COHORT, register_page_guids

    if since is None:
        since = _default_since()
    register = register_page_map()
    catalogued = _catalogued()
    marks = _latest_marks()
    reasons: dict[str, set[str]] = {}
    trigger: dict[str, datetime | None] = {}

    def add(guid: str, reason: str, at: datetime | None = None) -> None:
        reasons.setdefault(guid, set()).add(reason)
        last = trigger.get(guid)
        if at is not None and (last is None or at > last):
            trigger[guid] = at  # the page must be read after the latest change

    for guid in register.pages:
        if guid not in catalogued:
            add(guid, REASON_NO_CAPTURE)
    changed, links = _changed_since(since)
    for matter_id, changed_at in changed.items():
        own_page = register.page_of.get(matter_id)
        if own_page:
            add(own_page, REASON_LOVABLE_CHANGED, changed_at)
        for linked_page in links.get(matter_id, set()):
            add(linked_page, REASON_LINK_CHANGED, changed_at)
    for state in LovableSyncState.objects.filter(
        cohort=REGISTER_COHORT, matter__deleted_at__isnull=True
    ).only("matter_id", "observed", "applied"):
        observed = (state.observed or {}).get("onenote_link")
        if not isinstance(observed, str) or not observed:
            continue
        observed_pages = register_page_guids(observed)
        if len(observed_pages) == 1 and observed_pages != register_page_guids(_applied_link(state)):
            add(next(iter(observed_pages)), REASON_OBSERVED_LINK)
    for marked_page, marked in marks.items():
        if marked.state in REINSPECT_STATES:
            add(marked_page, REASON_MARKED, marked.recorded_at)

    out = []
    for guid, why in sorted(reasons.items()):
        mark = marks.get(guid)
        at = trigger.get(guid)
        # A page read (captured, verified, imported, found unchanged) after what
        # triggered it is done — except a page that was never captured at all.
        if (
            mark is not None
            and mark.state in SETTLED_STATES
            and why <= {REASON_LOVABLE_CHANGED, REASON_LINK_CHANGED, REASON_OBSERVED_LINK}
            and (at is None or mark.recorded_at >= at)
        ):
            continue
        out.append(
            WorkItem(
                page_guid=guid,
                reasons=tuple(sorted(why)),
                teemad=tuple(register.pages.get(guid, ())),
                state=mark.state if mark is not None else _derived_state(catalogued.get(guid)),
                since=since,
            )
        )
    return out


def capture_request(items: list[WorkItem]) -> dict[str, Any]:
    """The file the desktop capture reads: the worklist pages and, for every page the
    register names exactly, the Teemad the sealer attaches (the capture's ``matters``).

    Identity only — page GUIDs, Teema and Lovable ids, references; no titles or text.
    """
    register = register_page_map()
    return {
        "schema": "juristid.onenote-capture-request/1",
        "generated_at": timezone.now().isoformat(),
        "pages": [item.as_dict() for item in items],
        "register_map": {
            guid: [t.__dict__ for t in teemad] for guid, teemad in sorted(register.pages.items())
        },
    }


# -- held readings: a person's review (live-sync round 2, agent C) ----------------------
#
# The importer holds what it cannot decide (``onenote_capture``): a file on a page
# several Teemad link to, a later reading whose bytes or block time changed, a file
# whose identity across captures is not proven. An administrator sees one held item
# at a time (Haldus → Andmete sünkroonimine → OneNote'i ootel kirjed) and records ONE
# audited decision on it:
#
# * ``ASSOCIATE`` — a shared page's file belongs to one named Teema (a reason is
#   mandatory). The file becomes that Teema's Document through the import path
#   (``create_document`` + ``add_evidence_version``, no actor, so the chronology files
#   it on its OneNote day), and that Teema's held row now names it; the other Teemad
#   keep theirs, settled by the same decision. Never every Teema.
# * ``ACCEPT_REVISION`` — a held later reading with other bytes becomes the next
#   DocumentVersion of the Document the Teema already has; the earlier version, its
#   bytes and hash are never touched.
# * ``REJECT`` — the proposed association or reading is not Juristid's.
# * ``KEEP_HOLDING`` — waits for evidence; may be recorded again.
#
# The captured rows — page, resource, revision — are never altered; every decision is
# an append-only ``OneNoteHoldDecision`` with actor, time, reason and the item as it
# was seen. A decision is refused for an item that changed since it was shown (its
# token), for one already settled, and for one on a Teema the viewer may not open.

HOLD_CATEGORY_OWNERSHIP = "OWNERSHIP"
HOLD_CATEGORY_BYTES = "CHANGED_BYTES"
HOLD_CATEGORY_METADATA = "CHANGED_METADATA"
HOLD_CATEGORY_OTHER = "OTHER"
HOLD_CATEGORY_LABELS = {
    HOLD_CATEGORY_OWNERSHIP: "Kuuluvus ebaselge (jagatud leht)",
    HOLD_CATEGORY_BYTES: "Faili sisu muutunud",
    HOLD_CATEGORY_METADATA: "Metaandmed muutunud",
    HOLD_CATEGORY_OTHER: "Muu põhjus",
}

#: Why an item is held, in words, per the importer's code.
HOLD_REASON_LABELS = {
    "SHARED_PAGE_AWAITING_REVIEW": "Leht on seotud mitme teemaga; faili kuuluvus pole tõendatud.",
    "MOVED_BLOCK_BYTES_CHANGED": "Plokk on teisaldatud ja selle baidid on muutunud.",
    "BYTES_CHANGED_UNDER_SAME_BLOCK": "Sama ploki baidid on muutunud.",
    "DOCUMENT_REMOVED_SINCE_IMPORT": "Baidid on muutunud, kuid dokument on teemalt eemaldatud.",
    "BLOCK_TIME_CHANGED_UNDER_SAME_BLOCK": "Ploki loomise aeg on muutunud.",
    "COMMENT_CHANGED_SINCE_IMPORT": "Faili märkus on muutunud.",
    "AMBIGUOUS_EARLIER_BLOCK": "Mitu sama aja ja nimega faili; varasem vaste pole üheselt teada.",
    "RESEMBLES_EARLIER_FILE_UNPROVEN": "Sarnaneb varasema failiga, kuid aeg seda ei tõenda.",
    "SAME_FILE_ON_ANOTHER_PAGE_OF_TEEMA": "Sama fail on selle teema teisel lehel.",
    "EARLIER_CAPTURE_NOT_IMPORTED": "Varasema hõive rida; selle teema jaoks importimata.",
    "EMPTY_FILE": "Fail on 0-baidine.",
    "LARGER_THAN_EVIDENCE_LIMIT": "Fail on suurem kui tõendi ülempiir.",
}

_SHARED = "SHARED_PAGE_AWAITING_REVIEW"
_HOLD_CATEGORY = {
    _SHARED: HOLD_CATEGORY_OWNERSHIP,
    "MOVED_BLOCK_BYTES_CHANGED": HOLD_CATEGORY_BYTES,
    "BYTES_CHANGED_UNDER_SAME_BLOCK": HOLD_CATEGORY_BYTES,
    "DOCUMENT_REMOVED_SINCE_IMPORT": HOLD_CATEGORY_BYTES,
    "BLOCK_TIME_CHANGED_UNDER_SAME_BLOCK": HOLD_CATEGORY_METADATA,
    "COMMENT_CHANGED_SINCE_IMPORT": HOLD_CATEGORY_METADATA,
}

#: Import-row codes a person reviews. ``CONTINUES_EARLIER_BLOCK`` is not one: the
#: earlier decision stands for a proven pure move.
REVIEWED_IMPORT_CODES = frozenset(HOLD_REASON_LABELS) - {
    "BYTES_CHANGED_UNDER_SAME_BLOCK",
    "DOCUMENT_REMOVED_SINCE_IMPORT",
    "BLOCK_TIME_CHANGED_UNDER_SAME_BLOCK",
    "COMMENT_CHANGED_SINCE_IMPORT",
}

#: Refusals (the tests name them through ``tests.refusals.refused``).
HOLD_REASON_REQUIRED = "Kirjuta otsuse põhjendus (vähemalt 10 märki)."
HOLD_ALREADY_DECIDED = "Selle kirje kohta on otsus juba tehtud."
HOLD_STALE = "Kirje on pärast avamist muutunud; ava see uuesti ja otsusta värske seisu järgi."
HOLD_NOT_OFFERED = "Seda otsust selle kirje kohta teha ei saa."
HOLD_NOT_A_CANDIDATE = "Fail saab olla seotud ainult ühe selle lehega seotud teemaga."
HOLD_NO_DOCUMENT = "Teemal pole selle faili dokumenti, mille järgmine versioon see lugemine oleks."
HOLD_ALREADY_CURRENT = "Dokumendi praegune versioon on juba täpselt need baidid."
HOLD_SUPERSEDED = "Sama faili kohta on hilisem lugemine; otsusta selle järgi."
HOLD_BYTES_UNAVAILABLE = (
    "Algse faili baite ei leitud suletud kogumist või need ei vasta kataloogile; "
    "midagi ei kirjutatud."
)
HOLD_NOT_EVIDENCE = "Tühja või ülempiirist suuremat faili ei saa dokumendiks lisada."
HOLD_TEEMA_GONE = "Teema on vahepeal eemaldatud."

_MIN_REASON = 10


class HoldDecisionRefused(DomainError):
    """A decision the item does not allow; ``code`` goes to the audit row."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class HoldNotFound(Exception):
    """No such held item for this viewer (the view answers 404)."""


@dataclass
class HoldTeema:
    matter_id: str
    reference: str
    title: str
    is_open: bool
    link_id: str
    #: The held import row on this link (``None`` for a held later reading).
    row: Any = None


@dataclass
class HeldItem:
    key: str
    scope: str
    reason: str
    page: Any
    resource: Any
    teemad: list[HoldTeema]
    rows: list[Any] = field(default_factory=list)
    revision: Any = None
    decisions: list[Any] = field(default_factory=list)
    newer_revision: bool = False
    #: Filled for one item's page only (``held_item``).
    bytes_available: bool | None = None
    bytes_note: str = ""
    version_target: Any = None

    @property
    def category(self) -> str:
        return _HOLD_CATEGORY.get(self.reason, HOLD_CATEGORY_OTHER)

    @property
    def category_label(self) -> str:
        return HOLD_CATEGORY_LABELS[self.category]

    @property
    def reason_label(self) -> str:
        return HOLD_REASON_LABELS.get(self.reason, self.reason)

    @property
    def detail(self) -> str:
        if self.revision is not None:
            return str(self.revision.detail or "")
        return str(self.rows[0].error_detail or "") if self.rows else ""

    @property
    def final(self) -> Any:
        return next((d for d in self.decisions if d.final), None)

    @property
    def context_available(self) -> bool:
        if self.revision is not None:
            return bool(self.revision.context)
        return bool(self.page.blocks)

    @property
    def original(self) -> dict[str, Any]:
        """The identity Juristid already holds: the catalogued (first) reading."""
        r = self.resource
        return {
            "capture_id": self.page.capture_id,
            "resource_key": r.resource_key,
            "filename": r.original_filename,
            "sha256": r.sha256,
            "size": r.size_bytes,
            "added_at": r.source_added_at,
        }

    @property
    def later(self) -> dict[str, Any] | None:
        """The later capture's identity, for a held later reading."""
        v = self.revision
        if v is None:
            return None
        return {
            "capture_id": v.capture_id,
            "manifest_sha256": v.manifest_sha256,
            "kind": v.get_kind_display(),
            "sha256": v.sha256,
            "size": v.size_bytes,
            "added_at": v.source_added_at,
            "modified_at": v.block_modified_at,
        }

    @property
    def options(self) -> list[str]:
        if self.final is not None:
            return []
        if self.scope == HoldScope.SHARED_FILE:
            return [
                HoldDecisionKind.ASSOCIATE,
                HoldDecisionKind.REJECT,
                HoldDecisionKind.KEEP_HOLDING,
            ]
        if self.scope == HoldScope.HELD_REVISION and self.revision.kind == "CHANGED_BYTES":
            return [
                HoldDecisionKind.ACCEPT_REVISION,
                HoldDecisionKind.REJECT,
                HoldDecisionKind.KEEP_HOLDING,
            ]
        return [HoldDecisionKind.REJECT, HoldDecisionKind.KEEP_HOLDING]


def _hold_querysets() -> tuple[Any, Any]:
    from app.legacy_import.onenote_coverage_models import OneNoteResourceRevision, RevisionState
    from app.legacy_import.source_pages import LegacySourceResourceImport, ResourceImportState

    rows = LegacySourceResourceImport.objects.filter(
        state=ResourceImportState.SKIPPED,
        error_code__in=sorted(REVIEWED_IMPORT_CODES),
        matter_source_page__matter__deleted_at__isnull=True,
    ).select_related("resource__source_page", "matter_source_page__matter", "import_batch")
    revisions = OneNoteResourceRevision.objects.filter(
        state=RevisionState.HELD, matter_source_page__matter__deleted_at__isnull=True
    ).select_related("resource__source_page", "matter_source_page__matter")
    return rows, revisions


def _teema(link: Any, row: Any = None) -> HoldTeema:
    matter = link.matter
    return HoldTeema(
        matter_id=str(matter.pk),
        reference=str(getattr(matter, "display_reference", "") or ""),
        title=matter.title,
        is_open=bool(matter.is_open),
        link_id=str(link.pk),
        row=row,
    )


def _collect_items(*, key: str | None = None, lock: bool = False) -> list[HeldItem]:
    """Every held item (or the one named ``key``) with its decisions; no visibility yet."""
    import uuid

    from app.legacy_import.onenote_coverage_models import OneNoteResourceRevision

    rows, revisions = _hold_querysets()
    if key is not None:
        kind, _, raw = key.partition(":")
        try:
            # A shared file reopened for a Teema linked after a decision is
            # ``shared:<resource>:<n>`` (``_shared_key``).
            pk = uuid.UUID(raw.split(":", 1)[0])
        except ValueError:
            return []
        if kind == "shared":
            rows, revisions = rows.filter(resource_id=pk, error_code=_SHARED), revisions.none()
        elif kind == "import":
            rows, revisions = rows.filter(pk=pk).exclude(error_code=_SHARED), revisions.none()
        elif kind == "revision":
            rows, revisions = rows.none(), revisions.filter(pk=pk)
        else:
            return []
        if lock:
            rows = rows.select_for_update(of=("self",))
            revisions = revisions.select_for_update(of=("self",))
    items: dict[str, HeldItem] = {}
    ordered = list(
        rows.order_by("resource__source_page__page_key", "resource__source_block_ordinal")
    )
    settled = _shared_decisions({r.resource_id for r in ordered if r.error_code == _SHARED})
    for row in ordered:
        shared = row.error_code == _SHARED
        item_key = _shared_key(row, settled) if shared else f"import:{row.pk}"
        item = items.get(item_key)
        if item is None:
            item = items[item_key] = HeldItem(
                key=item_key,
                scope=HoldScope.SHARED_FILE if shared else HoldScope.HELD_IMPORT,
                reason=row.error_code,
                page=row.resource.source_page,
                resource=row.resource,
                teemad=[],
            )
        item.rows.append(row)
        item.teemad.append(_teema(row.matter_source_page, row))
    for revision in revisions.order_by("created_at"):
        item_key = f"revision:{revision.pk}"
        items[item_key] = HeldItem(
            key=item_key,
            scope=HoldScope.HELD_REVISION,
            reason=revision.reason,
            page=revision.resource.source_page,
            resource=revision.resource,
            teemad=[_teema(revision.matter_source_page)],
            revision=revision,
        )
    if key is not None:
        items = {k: v for k, v in items.items() if k == key}
    if not items:
        return []
    for decision in OneNoteHoldDecision.objects.filter(item_key__in=list(items)).order_by(
        "decided_at", "created_at"
    ):
        items[decision.item_key].decisions.append(decision)
    readings = [i for i in items.values() if i.revision is not None]
    if readings:
        newest: dict[tuple[Any, Any], Any] = {}
        for link_id, resource_id, created in OneNoteResourceRevision.objects.filter(
            resource_id__in={i.revision.resource_id for i in readings}
        ).values_list("matter_source_page_id", "resource_id", "created_at"):
            known = newest.get((link_id, resource_id))
            newest[(link_id, resource_id)] = created if known is None else max(known, created)
        for item in readings:
            v = item.revision
            item.newer_revision = newest[(v.matter_source_page_id, v.resource_id)] > v.created_at
    return list(items.values())


def _shared_key(row: Any, settled: dict[Any, list[tuple[str, set[Any]]]]) -> str:
    """The item a shared page's held row belongs to.

    The row a final decision saw belongs to that decision's item; a row no decision
    saw (a Teema linked to the page later) is a new, open item of the same file.
    """
    decisions = settled.get(row.resource_id, [])
    for item_key, seen in decisions:
        if row.pk in seen:
            return item_key
    base = f"shared:{row.resource_id}"
    return f"{base}:{len(decisions)}" if decisions else base


def _visible_matters(viewer: Any, items: list[HeldItem]) -> set[str]:
    from app.matters.models import Matter

    ids = {t.matter_id for i in items for t in i.teemad}
    if viewer is None or not ids:
        return set()
    return {
        str(pk)
        for pk in Matter.objects.visible_to(viewer).filter(pk__in=ids).values_list("pk", flat=True)
    }


def held_counts() -> dict[str, Any]:
    """Counts only (no Teema named): open items per category, settled ones per decision."""
    items = _collect_items()
    open_items = [i for i in items if i.final is None]
    return {
        "open": len(open_items),
        "open_by_category": dict(Counter(i.category for i in open_items)),
        "open_shared_rows": sum(
            len(i.rows) for i in open_items if i.scope == HoldScope.SHARED_FILE
        ),
        "decided": len(items) - len(open_items),
        "decided_by_decision": dict(
            Counter(str(i.final.decision) for i in items if i.final is not None)
        ),
    }


def held_items(
    *, viewer: Any, category: str = "", decided: bool = False
) -> tuple[list[HeldItem], int, dict[str, int]]:
    """(items the viewer may open, how many more are on Teemad it may not, open per category)."""
    items = _collect_items()
    counts = Counter(i.category for i in items if i.final is None)
    chosen = [
        i
        for i in items
        if (i.final is not None) == decided and (not category or i.category == category)
    ]
    visible = _visible_matters(viewer, chosen)
    shown = [i for i in chosen if all(t.matter_id in visible for t in i.teemad)]
    return shown, len(chosen) - len(shown), dict(counts)


def _version_target(item: HeldItem) -> Any:
    """The live Document a held later reading would be the next version of, or None."""
    from app.legacy_import.source_pages import LegacySourceResourceImport, ResourceImportState

    v = item.revision
    if v is None:
        return None
    row = (
        LegacySourceResourceImport.objects.filter(
            matter_source_page_id=v.matter_source_page_id,
            resource_id=v.resource_id,
            state=ResourceImportState.IMPORTED,
            document__isnull=False,
        )
        .select_related("document")
        .first()
    )
    document = row.document if row is not None else None
    if document is None or document.removed_at is not None:
        return None
    if str(document.matter_id) != item.teemad[0].matter_id:
        return None
    return document


def _sealed_bytes(item: HeldItem, *, read: bool) -> tuple[bytes | None, str]:
    """The original bytes from the sealed capture (``read``), or only whether they are there.

    Found as the originals view finds them (``source_originals``): by scanning the
    read-only corpus for the seal of the row's own capture, never by a stored path.
    """
    import hashlib

    from app.legacy_import import source_originals

    if item.revision is not None:
        v = item.revision
        package = source_originals.find_package(
            capture_id=v.capture_id, manifest_sha256=v.manifest_sha256
        )
        if package is None:
            return None, "package_missing"
        if v.sha256 not in package.blobs:
            return None, "blob_not_sealed"
        path = package.root / "blobs" / v.sha256
        if path.is_symlink() or not path.is_file() or path.stat().st_size != v.size_bytes:
            return None, "blob_missing"
        if not read:
            return None, ""
        content = path.read_bytes()
        if len(content) != v.size_bytes or hashlib.sha256(content).hexdigest() != v.sha256:
            return None, "hash_mismatch"
        return content, ""
    row = item.rows[0]
    try:
        if read:
            with source_originals.open_original(item.resource, row) as handle:
                return handle.read(), ""
        batch = source_originals.check_recorded(item.resource, row)
        package = source_originals.find_package(
            capture_id=source_originals._capture_of(row),
            manifest_sha256=batch.source_snapshot_sha256,
        )
    except source_originals.OriginalUnavailable as error:
        return None, error.outcome
    if package is None:
        return None, "package_missing"
    if item.resource.sha256 not in package.blobs:
        return None, "blob_not_sealed"
    return None, ""


def held_item(key: str, *, viewer: Any) -> HeldItem:
    """One held item, with its bytes' availability and its version target.

    Raises ``HoldNotFound`` for no such item, or one on a Teema the viewer may not open.
    """
    items = _collect_items(key=key)
    if not items:
        raise HoldNotFound(key)
    item = items[0]
    visible = _visible_matters(viewer, [item])
    if not all(t.matter_id in visible for t in item.teemad):
        raise HoldNotFound(key)
    _content, note = _sealed_bytes(item, read=False)
    item.bytes_available, item.bytes_note = not note, note
    item.version_target = _version_target(item)
    return item


def item_token(item: HeldItem) -> str:
    """What a decision is bound to: the item's rows, states and decisions as shown."""
    from app.legacy_import.integrity import canonical_sha256

    return canonical_sha256(
        {
            "key": item.key,
            "rows": sorted(
                [str(r.pk), r.state, r.error_code, str(r.document_id or "")] for r in item.rows
            ),
            "revision": [str(item.revision.pk), item.revision.state] if item.revision else None,
            "newer_revision": item.newer_revision,
            "decisions": [str(d.pk) for d in item.decisions],
        }
    )


def _observed(item: HeldItem) -> dict[str, Any]:
    r = item.resource
    v = item.revision
    return {
        "scope": str(item.scope),
        "category": item.category,
        "reason": item.reason,
        "page_key": item.page.page_key,
        "section": item.page.source_section,
        "resource_key": r.resource_key,
        "filename": r.original_filename,
        "sha256": r.sha256,
        "size": r.size_bytes,
        "source_added_at": r.source_added_at.isoformat() if r.source_added_at else None,
        "rows": [
            {
                "id": str(row.pk),
                "matter_id": str(row.matter_source_page.matter_id),
                "state": row.state,
                "error_code": row.error_code,
                "error_detail": row.error_detail,
            }
            for row in item.rows
        ],
        "revision": None
        if v is None
        else {
            "id": str(v.pk),
            "capture_id": v.capture_id,
            "manifest_sha256": v.manifest_sha256,
            "kind": v.kind,
            "state": v.state,
            "sha256": v.sha256,
            "size": v.size_bytes,
        },
        "teemad": [t.matter_id for t in item.teemad],
        "bytes_available": item.bytes_available,
    }


def _review_note(item: HeldItem, reason: str) -> str:
    """The document's provenance: where the file sits and that a person associated it."""
    page = item.page
    r = item.resource
    location = " → ".join(
        part for part in (page.source_section_group, page.source_section, page.title[:160]) if part
    )
    lines = [f"OneNote: {location}"]
    if r.source_added_at:
        lines.append(f"Lisatud OneNote'i: {timezone.localtime(r.source_added_at):%d.%m.%Y %H:%M}")
    else:
        lines.append("Lisamise kuupäev teadmata.")
    lines.append(f"Seotud selle teemaga halduri otsusega: {reason[:300]}")
    lines.append(f"Allikas: {page.capture_id}")
    return "\n".join(lines)


def _promote(item: HeldItem, *, teema: HoldTeema, reason: str, content: bytes) -> tuple[Any, Any]:
    """Make the Document (``ASSOCIATE``) or its next version (``ACCEPT_REVISION``)
    through the ordinary import path: no actor, the catalogued hash expected."""
    from pathlib import Path

    from django.conf import settings

    from app.documents.enums import ExtractionState
    from app.documents.services import add_evidence_version, create_document
    from app.legacy_import.historical_materials import (
        extraction_note_for,
        is_extractable,
        mime_type_for,
        role_for,
    )
    from app.matters.locks import lock_matters_in_order

    limit = int(getattr(settings, "MAX_EVIDENCE_UPLOAD_BYTES", 100 * 1024 * 1024))
    if not content or len(content) > limit:
        raise HoldDecisionRefused("NOT_EVIDENCE", HOLD_NOT_EVIDENCE)
    matter = next(iter(lock_matters_in_order(teema.matter_id).values()), None)
    if matter is None or matter.deleted_at is not None:
        raise HoldDecisionRefused("TEEMA_GONE", HOLD_TEEMA_GONE)
    r = item.resource
    name = (r.original_filename or "").strip()[:500] or f"onenote-{r.source_block_ordinal}.bin"
    extractable = is_extractable(Path(name).suffix.lower())
    v = item.revision
    if v is None:
        document = create_document(
            matter=matter,
            title=name[:400],
            role=role_for(name),
            provenance_note=_review_note(item, reason),
        )
        expected = r.sha256
        source_path, identifier = r.archive_relative_path, f"{item.page.page_key}/{r.resource_key}"
    else:
        document = item.version_target
        expected = v.sha256
        source_path = v.archive_relative_path
        identifier = f"{item.page.page_key}/{r.resource_key}@{v.capture_id}"
    version = add_evidence_version(
        document=document,
        content=content,
        original_filename=name,
        mime_type=mime_type_for(name),
        expected_sha256=expected,
        source_path=source_path,
        source_identifier=identifier[:400],
        extraction_state=ExtractionState.PENDING if extractable else ExtractionState.NOT_APPLICABLE,
        extraction_note="" if extractable else extraction_note_for(name)[:300],
    )
    # ``expected_sha256`` makes add_evidence_version refuse other bytes before it stores them.
    return document, version


def decide_hold(
    key: str,
    *,
    actor: Any,
    viewer: Any,
    decision: str,
    reason: str,
    token: str,
    link_id: str = "",
    client: dict[str, Any] | None = None,
) -> Any:
    """Record one administrator's decision on one held item. Returns the decision row.

    Raises ``HoldNotFound`` (no such item for this viewer) or ``HoldDecisionRefused``
    (named refusal; nothing written but its audit row).
    """
    from django.db import IntegrityError

    from app.legacy_import.source_pages import LegacySourceResourceImport, ResourceImportState
    from app.matters.locks import lock_matters_in_order

    user = getattr(actor, "user", actor)
    reason = (reason or "").strip()
    document = version = None
    try:
        if len(reason) < _MIN_REASON:
            raise HoldDecisionRefused("REASON_REQUIRED", HOLD_REASON_REQUIRED)
        with transaction.atomic():
            # The Teemad first, in the global order a permanent deletion also takes
            # (Matter before its rows), then the held rows; the token proves nothing
            # moved between the two reads.
            peek = _collect_items(key=key)
            if peek:
                lock_matters_in_order(*sorted({t.matter_id for t in peek[0].teemad}))
            items = _collect_items(key=key, lock=True)
            if not items:
                raise HoldNotFound(key)
            item = items[0]
            visible = _visible_matters(viewer, [item])
            if not all(t.matter_id in visible for t in item.teemad):
                raise HoldNotFound(key)
            if item.final is not None:
                raise HoldDecisionRefused("ALREADY_DECIDED", HOLD_ALREADY_DECIDED)
            if not token or token != item_token(item):
                raise HoldDecisionRefused("STALE_ITEM", HOLD_STALE)
            if decision not in item.options:
                raise HoldDecisionRefused("NOT_OFFERED", HOLD_NOT_OFFERED)
            teema = item.teemad[0]
            if decision == HoldDecisionKind.ASSOCIATE:
                found = [t for t in item.teemad if t.link_id == link_id]
                if len(found) != 1:
                    raise HoldDecisionRefused("NOT_A_CANDIDATE", HOLD_NOT_A_CANDIDATE)
                teema = found[0]
            if decision == HoldDecisionKind.ACCEPT_REVISION:
                if item.newer_revision:
                    raise HoldDecisionRefused("SUPERSEDED", HOLD_SUPERSEDED)
                item.version_target = _version_target(item)
                if item.version_target is None:
                    raise HoldDecisionRefused("NO_DOCUMENT", HOLD_NO_DOCUMENT)
                current = item.version_target.current_version
                if current is not None and current.sha256 == item.revision.sha256:
                    raise HoldDecisionRefused("ALREADY_CURRENT", HOLD_ALREADY_CURRENT)
            if decision in (HoldDecisionKind.ASSOCIATE, HoldDecisionKind.ACCEPT_REVISION):
                content, note = _sealed_bytes(item, read=True)
                item.bytes_available = content is not None
                if content is None:
                    raise HoldDecisionRefused("BYTES_UNAVAILABLE:" + note, HOLD_BYTES_UNAVAILABLE)
                document, version = _promote(item, teema=teema, reason=reason, content=content)
            else:
                item.bytes_available = None
            row = teema.row
            if decision == HoldDecisionKind.ASSOCIATE:
                # Juristid's association, not the capture: the chosen Teema's held row
                # now names its Document (what the chronology, the page view and the
                # verifier read). The row as it was is kept verbatim in ``observed``.
                moved = LegacySourceResourceImport.objects.filter(
                    pk=row.pk, state=ResourceImportState.SKIPPED, document__isnull=True
                ).update(
                    state=ResourceImportState.IMPORTED,
                    document=document,
                    document_version=version,
                    error_code="",
                    error_detail="",
                )
                if moved != 1:
                    raise HoldDecisionRefused("STALE_ITEM", HOLD_STALE)
            final = decision in FINAL_HOLD_DECISIONS
            try:
                with transaction.atomic():
                    record = OneNoteHoldDecision.objects.create(
                        item_key=item.key,
                        scope=item.scope,
                        decision=decision,
                        final=final,
                        resource=item.resource,
                        resource_import=row
                        if item.scope == HoldScope.HELD_IMPORT
                        else (row if decision == HoldDecisionKind.ASSOCIATE else None),
                        revision=item.revision,
                        matter_source_page_id=teema.link_id
                        if (
                            item.scope != HoldScope.SHARED_FILE
                            or decision == HoldDecisionKind.ASSOCIATE
                        )
                        else None,
                        reason=reason,
                        actor=user,
                        item_token=token,
                        observed=_observed(item),
                        document=document,
                        document_version=version,
                    )
            except IntegrityError as error:
                raise HoldDecisionRefused("ALREADY_DECIDED", HOLD_ALREADY_DECIDED) from error
            if final:
                _mark_page_if_settled(item.page, user)
    except BaseException as failure:
        if version is not None:
            # Every refusal comes before the bytes are stored; anything failing after
            # rolls the version row back, so its stored object must go too.
            from app.documents.services import _discard_stored_object, evidence_storage

            _discard_stored_object(evidence_storage(), version.storage_key)
        if isinstance(failure, HoldDecisionRefused):
            _audit_hold(user, key, decision, client, succeeded=False, code=failure.code)
        raise
    _audit_hold(
        user,
        key,
        decision,
        client,
        succeeded=True,
        subject=document,
        scope=str(item.scope),
        hold_reason=item.reason,
    )
    return record


def _audit_hold(
    user: Any,
    key: str,
    decision: str,
    client: dict[str, Any] | None,
    *,
    succeeded: bool,
    subject: Any = None,
    **detail: Any,
) -> None:
    from app.audit.enums import SecurityEventType
    from app.audit.services import record_security_event

    record_security_event(
        event_type=SecurityEventType.IMPORT_RUN,
        actor=user,
        subject=subject,
        succeeded=succeeded,
        ip_address=(client or {}).get("ip_address"),
        user_agent=str((client or {}).get("user_agent") or ""),
        detail={
            "action": "ONENOTE_HOLD_DECIDED" if succeeded else "ONENOTE_HOLD_DECISION_REFUSED",
            "item": key,
            "decision": str(decision)[:40],
            **{k: v for k, v in detail.items() if v not in (None, "")},
        },
    )


def _mark_page_if_settled(page: Any, user: Any) -> None:
    """A page that waited for association and has no open held item left is imported.

    Appends one progress mark (the page's state is its latest mark); nothing else.
    """
    from app.legacy_import.onenote_coverage_models import OneNoteResourceRevision, RevisionState
    from app.legacy_import.source_pages import LegacySourceResourceImport, ResourceImportState

    rows, readings = _settled_holds()
    live = {"matter_source_page__matter__deleted_at__isnull": True}
    if (
        LegacySourceResourceImport.objects.filter(
            resource__source_page=page,
            state=ResourceImportState.SKIPPED,
            error_code__in=sorted(REVIEWED_IMPORT_CODES),
            **live,
        )
        .exclude(pk__in=rows)
        .exists()
        or OneNoteResourceRevision.objects.filter(
            resource__source_page=page, state=RevisionState.HELD, **live
        )
        .exclude(pk__in=readings)
        .exists()
    ):
        return
    guid = _guid(page.page_key[2:]) if page.page_key.lower().startswith("p-") else ""
    mark = _latest_marks().get(guid) if guid else None
    if mark is not None and mark.state == PageState.AWAITING_ASSOCIATION:
        mark_pages(
            [guid],
            PageState.IMPORTED,
            actor=user,
            reason="Ootel kirjed on halduri otsustega lahendatud.",
        )
