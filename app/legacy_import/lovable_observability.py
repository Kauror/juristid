"""The one read service over Lovable observation and synchronisation (docs/adr/0201).

Read-only. Uuendused → Lovable, Haldus → Lovable sünkroonimine, the MCP tools
and `lovable_sync report` all ask these functions and nothing else, so the
same question has the same answer everywhere. Nothing here reaches Lovable,
writes a row or decides anything: it reads what the pilot synchronisation
(docs/adr/0192) and the wider observation (docs/adr/0201) already recorded.

Four kinds of fact are kept apart in every answer, never added together:

* **source events** — Lovable's own audit ledger (`LovableSourceEvent`), only
  where authorised access exists; otherwise ``audit.available`` is False and no
  event is shown or counted;
* **snapshot differences** — the net change between two verified readings
  (pilot nights: `lovable_review`; wider readings: `LovableObservedChange`);
* **reconciliation decisions** — `LovableSyncDecision` (pilot only);
* **applied writes** — decisions with outcome ``APPLIED`` (none so far: APPLY
  has never been switched on).

**Who sees what.** `may_read_business_content` is the caller's precondition
(the views and MCP check it; every function here also returns nothing for a
reader it refuses). A pilot Teema's values and title are shown only when the
Teema passes `Matter.objects.visible_to(reader)`; otherwise it is counted in
``hidden``. A source topic with no approved link to a Juristid Teema
(`MappingStatus.REFERENCE_CANDIDATE` / ``UNMATCHED``) is shown by its source
number, change kind and *field names* only — never its title or values —
until the owner decides a detail policy for it (`UNMATCHED_DETAIL`). Every
list is paginated server-side with a fixed page size and deterministic order.

Every dataclass has ``as_dict()`` giving JSON-safe primitives (ISO strings for
moments and dates), which the MCP tools return unchanged.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import asdict, dataclass, field
from typing import Any

#: Rows per page on every list here; MCP and the pages use the same pages.
PAGE_SIZE = 50
#: The longest window any history question may ask about.
MAX_WINDOW_DAYS = 31
#: How a source topic with no approved Juristid link is shown: field names
#: only. An owner decision may later widen it (docs/adr/0201 §6).
UNMATCHED_DETAIL = "FIELD_NAMES_ONLY"
#: Longest value shown in a list; the full allowed value is in the detail.
SHORT_VALUE = 160


def _jsonable(value: Any) -> Any:
    if isinstance(value, dt.datetime | dt.date):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [_jsonable(v) for v in value]
    if hasattr(value, "hex") and not isinstance(value, bytes | str | int | float):
        return str(value)  # UUID
    return value


class _AsDict:
    def as_dict(self) -> dict[str, Any]:
        return _jsonable(asdict(self))  # type: ignore[call-overload]


# ---------------------------------------------------------------------------
# Shapes
# ---------------------------------------------------------------------------


@dataclass
class Indicator(_AsDict):
    """One number on the overview, with what it counts and over which window."""

    key: str
    label: str
    value: int | None
    #: What is counted, in Estonian, e.g. «piloodi 100 teemast».
    denominator: str
    #: The observation window, in Estonian, e.g. «viimane öö (02.11.2026 kell 00.15)».
    window: str
    #: ``None`` value + a reason when the number cannot be known.
    unavailable_reason: str = ""


@dataclass
class AuditStatus(_AsDict):
    available: bool
    #: `AuditAccess` value: DISABLED, UNAVAILABLE or AVAILABLE.
    access: str
    #: Estonian sentence for the page; never business content.
    message: str
    last_event_at: dt.datetime | None = None
    events_stored: int = 0


@dataclass
class SyncStatus(_AsDict):
    """`get_lovable_sync_status`: the synchronisation and observation at a glance."""

    mode: str
    mode_label: str
    apply_authorised: bool
    cut_over: bool
    observe_enabled: bool
    last_scheduled_at: dt.datetime | None
    last_scheduled_status: str
    last_success_at: dt.datetime | None
    last_failure_at: dt.datetime | None
    #: The failure's code only (``INCONSISTENT``, ``HTTP_401``…), never its text.
    last_failure_code: str
    snapshot_taken_at: dt.datetime | None
    #: FRESH (≤ 26 h), STALE, or NONE.
    snapshot_freshness: str
    observation_taken_at: dt.datetime | None
    observation_freshness: str
    audit: AuditStatus
    cohort_size: int
    #: Source counts from the newest verified wider reading (None if none).
    live_topics: int | None
    operational_topics: int | None
    pending_review: int
    #: Estonian sentences: what is red and why. Empty does not mean «ready».
    warnings: list[str] = field(default_factory=list)
    indicators: list[Indicator] = field(default_factory=list)


@dataclass
class FieldDiff(_AsDict):
    field: str
    label: str
    #: Canonical values, or "" when withheld; ``withheld`` says which.
    before: str
    after: str
    withheld: bool = False
    #: The pilot reconciliation's result for this field this night (pilot only):
    #: a `lovable_review` result key and its Estonian label, and the reason.
    result: str = ""
    result_label: str = ""
    reason: str = ""


@dataclass
class TopicDiff(_AsDict):
    """One source topic's change in one reading (night or wider observation)."""

    source_id: str
    reference: str
    #: `ObservedChangeKind` value.
    kind: str
    population: list[str]
    #: `MappingStatus` value.
    mapping: str
    #: Title only for a pilot Teema the reader may open; "" otherwise.
    title: str
    matter_id: str | None
    matter_url: str
    fields: list[FieldDiff]
    previous_reference: str = ""
    scope_changed: bool = False
    children_only: bool = False
    #: An Estonian summary sentence, built from the field names
    #: («Järgmise tegevuse sõnastus ja kuupäev muutusid koos»). Never AI text.
    summary: str = ""


@dataclass
class Reading(_AsDict):
    """One night (pilot) or one wider reading, with its topics."""

    run_id: str
    #: PILOT_NIGHT or OBSERVATION.
    kind: str
    taken_at: dt.datetime | None
    baseline_taken_at: dt.datetime | None
    status: str
    status_label: str
    #: BASELINE, COMPARED, NO_SNAPSHOT, UNAVAILABLE (as `lovable_review`).
    comparison: str
    message: str
    cohort_size: int | None
    new_topics: int
    changed_topics: int
    missing_topics: int
    changed_fields: int
    #: Outcome key → count (pilot nights: APPLIED, NOT_APPLIED, REVIEW, CONFLICT,
    #: SAME, RESOLVED, NOT_COMPARED, LOCAL, FAILED, UNKNOWN).
    outcomes: dict[str, int]
    topics: list[TopicDiff]
    hidden: int = 0


@dataclass
class Page(_AsDict):
    items: list[Any]
    page: int
    pages: int
    total: int
    page_size: int = PAGE_SIZE


@dataclass
class SourceEventView(_AsDict):
    source_event_id: str
    occurred_at: dt.datetime
    reference: str
    action: str
    action_label: str
    actor_kind: str
    version: int | None
    fields: list[FieldDiff]
    mapping: str
    title: str
    matter_id: str | None
    matter_url: str


@dataclass
class QueueItem(_AsDict):
    """`Ülevaatust ootavad muudatused`: one pilot field still pending."""

    matter_id: str
    reference: str
    title: str
    matter_url: str
    field: str
    label: str
    first_observed_at: dt.datetime | None
    last_observed_at: dt.datetime | None
    age_days: int | None
    last_applied: str
    juristid_now: str
    lovable_now: str
    #: `SyncOutcome` value of the newest decision, and its Estonian label.
    outcome: str
    outcome_label: str
    reason: str
    explanation: str
    #: What a person could do next — never done here.
    suggested_action: str
    #: How many nights recorded a decision for this pending field.
    nights_pending: int


@dataclass
class MatterChanges(_AsDict):
    """`get_lovable_sync_matter_changes`: one linked Teema's retained history."""

    matter_id: str
    reference: str
    title: str
    matter_url: str
    source_id: str
    #: Per field: last applied (A), Juristid now (B), Lovable last observed (C).
    fields: list[dict[str, Any]]
    nights: list[TopicDiff]
    decisions: list[dict[str, Any]]
    source_events: list[SourceEventView]
    pending: list[QueueItem]
    audit: AuditStatus


@dataclass
class FieldReadiness(_AsDict):
    field: str
    label: str
    #: EXACT, APPROXIMATE, NONE.
    mapping: str
    automatic: bool
    service: str
    risks: list[str]
    blocking_reason: str
    #: Simulated outcome key → count over the pilot's latest observation.
    simulated: dict[str, int]
    #: Of the field's source changes in the window, how many no rule covers.
    uncovered_changes: int


@dataclass
class Readiness(_AsDict):
    """`get_lovable_sync_readiness`: what stands between today and APPLY. Never an
    activation: it plans what the *existing* rules would do, writes nothing."""

    generated_at: dt.datetime
    source: dict[str, Any]
    identity: dict[str, Any]
    fields: list[FieldReadiness]
    #: Record-level and field-level coverage, each with numerator/denominator.
    coverage: list[Indicator]
    #: Simulated plan: outcome key → count, and the per-Teema plan rows the
    #: reader may see.
    simulation: dict[str, int]
    simulation_rows: list[dict[str, Any]]
    conflicts: int
    oldest_conflict_days: int | None
    blockers: list[str]
    owner_decisions: list[str]


# ---------------------------------------------------------------------------
# The questions. Every function takes the requesting user first.
# ---------------------------------------------------------------------------


def status(user: Any, *, now: dt.datetime | None = None) -> SyncStatus:
    """The mode, freshness, source counts and outstanding review, with warnings."""
    raise NotImplementedError


def nights(
    user: Any,
    *,
    since: dt.date | None = None,
    until: dt.date | None = None,
    population: str = "PILOT",
    outcome: str = "",
    field_name: str = "",
    reference: str = "",
    text: str = "",
    page: int = 1,
    now: dt.datetime | None = None,
) -> Page:
    """Readings newest first (`Reading` items). ``population`` PILOT gives the
    pilot's scheduled nights (wrapping `lovable_review.nightly_history`);
    OPERATIONAL / ALL_LIVE give wider observation readings filtered to that
    population. Filters narrow the topics inside each reading; a reading with
    no matching topic is still listed (its counts say why)."""
    raise NotImplementedError


def observed_changes(
    user: Any,
    *,
    since: dt.date | None = None,
    until: dt.date | None = None,
    population: str = "ALL_LIVE",
    kind: str = "",
    field_name: str = "",
    mapping: str = "",
    reference: str = "",
    text: str = "",
    page: int = 1,
    now: dt.datetime | None = None,
) -> Page:
    """`TopicDiff` items from wider readings, newest first, with totals by kind
    and mapping in the page's ``items`` order."""
    raise NotImplementedError


def source_events(
    user: Any,
    *,
    since: dt.date | None = None,
    until: dt.date | None = None,
    reference: str = "",
    field_name: str = "",
    page: int = 1,
) -> tuple[AuditStatus, Page]:
    """The audit ledger's events (`SourceEventView`). With no authorised access
    the page is empty and the status says why — never an invented event."""
    raise NotImplementedError


def review_queue(
    user: Any,
    *,
    field_name: str = "",
    outcome: str = "",
    reference: str = "",
    text: str = "",
    page: int = 1,
    now: dt.datetime | None = None,
) -> Page:
    """Pilot fields whose last observed source value differs from the last
    applied one and is not converged (`QueueItem`), oldest first."""
    raise NotImplementedError


def matter_changes(
    user: Any, reference_or_id: str, *, now: dt.datetime | None = None
) -> MatterChanges | None:
    """``None`` for an unknown Teema, one the reader may not open, or one not in
    the pilot — the caller says «not found» for all three alike."""
    raise NotImplementedError


def readiness(user: Any, *, now: dt.datetime | None = None) -> Readiness:
    """The readiness assessment (docs/adr/0201 §7). Writes nothing."""
    raise NotImplementedError
