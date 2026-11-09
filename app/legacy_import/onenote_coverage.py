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

from app.legacy_import.onenote_coverage_models import (
    CoverageScope,
    OneNoteCoverageMark,
    PageState,
)

__all__ = [
    "PageState",
    "WorkItem",
    "coverage_summary",
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
        )
        .values_list(
            "pk",
            "page_key",
            "capture_id",
            "source_section",
            "import_batch__source_system",
            "live_links",
            "held",
        )
    )
    for pk, key, capture_id, section, system, links, held in rows:
        if not key.lower().startswith("p-"):
            continue
        guid = _guid(key[2:])
        out[guid] = CataloguedPage(
            pk, guid, capture_id, section, system == BATCH_SOURCE_SYSTEM, links, held
        )
    return out


def _derived_state(page: CataloguedPage | None) -> str:
    if page is None:
        return PageState.NOT_INSPECTED
    if page.linked > 1 or page.review_holds:
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
    """Append the marks a capture step established: page key -> state."""
    by_state: dict[str, list[str]] = {}
    guids = {p.page_key: p.page_guid for p in capture.pages}
    for key, state in outcomes.items():
        if key in guids:
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
    imports = Counter(
        (state, code)
        for state, code in LegacySourceResourceImport.objects.filter(
            matter_source_page__matter_id__in=matter_ids
        ).values_list("state", "error_code")
    )
    revisions = Counter(
        OneNoteResourceRevision.objects.filter(
            matter_source_page__matter_id__in=matter_ids
        ).values_list("state", flat=True)
    )
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
