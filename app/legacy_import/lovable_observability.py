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
(`MappingStatus.REFERENCE_CANDIDATE` / ``UNMATCHED``) is, by default
(`unlinked_detail()` → `AGGREGATE`), never an item anywhere — no number, no
timestamp, no action, no field name, no candidate link — only counted in
``unlinked`` (`UnlinkedSummary`) until the owner decides a detail policy
(docs/adr/0201 §6). The owner-approvable alternative `FIELD_NAMES`
(``LOVABLE_UNLINKED_DETAIL``) lists it by source number, change kind and
field *names*; never its title or values either way. Every list is paginated
server-side with a fixed page size and deterministic order.

Every dataclass has ``as_dict()`` giving JSON-safe primitives (ISO strings for
moments and dates), which the MCP tools return unchanged.
"""

from __future__ import annotations

import datetime as dt
import re
import uuid
from collections import Counter
from dataclasses import asdict, dataclass, field
from typing import Any

from django.conf import settings
from django.db.models import Count, F, Max, Min, Q
from django.urls import reverse
from django.utils import timezone

from app.core.authorization import may_read_business_content
from app.legacy_import import lovable_review, lovable_sync
from app.legacy_import.lovable_observation_models import (
    OBSERVATION_RETENTION_DAYS,
    AuditAccess,
    LovableObservationRun,
    LovableObservationSnapshot,
    LovableObservedChange,
    LovableRetentionRun,
    LovableSourceEvent,
    LovableSourceEventField,
    MappingStatus,
    ObservationRunStatus,
    ObservationScope,
    ObservedChangeKind,
    RetentionRunStatus,
)
from app.legacy_import.lovable_pilot import SOURCE_SYSTEM
from app.legacy_import.lovable_source import SourceError, verify_snapshot
from app.legacy_import.lovable_sync import FIELDS, TALLINN
from app.legacy_import.lovable_sync_labels import (
    LOVABLE_COHORT_LABELS,
    LOVABLE_FIELD_LABELS,
    LOVABLE_REASONS,
    LOVABLE_SIMULATION_LABELS,
    LOVABLE_SOURCE_ACTIONS,
    LOVABLE_SOURCE_COLUMNS,
)
from app.legacy_import.lovable_sync_models import (
    LovableSyncControl,
    LovableSyncDecision,
    LovableSyncRun,
    LovableSyncSnapshot,
    LovableSyncState,
    SyncMode,
    SyncOutcome,
    SyncRunStatus,
    SyncTrigger,
)

#: Rows per page on every list here; MCP and the pages use the same pages.
PAGE_SIZE = 50
#: The longest window any history question may ask about.
MAX_WINDOW_DAYS = 31
#: How a source topic with no approved Juristid link (REFERENCE_CANDIDATE or
#: UNMATCHED) is shown (docs/adr/0201 §6). AGGREGATE: counts only, never an item.
AGGREGATE = "AGGREGATE"
#: The owner-approvable alternative: number, change kind and field names per
#: topic (and a REFERENCE_CANDIDATE's link) — never a title or a value.
FIELD_NAMES = "FIELD_NAMES"
#: The default until the owner decides; ``settings.LOVABLE_UNLINKED_DETAIL``
#: (``JURISTID_LOVABLE_UNLINKED_DETAIL``) selects the policy.
UNMATCHED_DETAIL = AGGREGATE
#: Said wherever unlinked changes are only counted.
UNLINKED_NOTE = (
    "{n} {noun} teemades, millel pole Juristides kinnitatud vastet; "
    "üksikasju ei näidata, kuni omanik on nähtavuse otsustanud."
)
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
class UnlinkedSummary(_AsDict):
    """Changes of source topics with no approved Juristid link, counted only.

    Under `AGGREGATE` these topics are never items; this says how many there
    were, by change kind (source events: by action), by mapping and by field
    name — counts across all of them, never one topic's number, time or link.
    Reference and text filters never narrow it, so a search cannot probe for
    one topic. Empty (``total`` 0, ``policy`` FIELD_NAMES) when the owner has
    chosen `FIELD_NAMES` and the topics are listed instead."""

    policy: str = AGGREGATE
    total: int = 0
    kind: dict[str, int] = field(default_factory=dict)
    mapping: dict[str, int] = field(default_factory=dict)
    field: dict[str, int] = field(default_factory=dict)
    #: The Estonian sentence for the page («N muudatust teemades, …»), or "".
    note: str = ""


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
    #: Shown instead of the number when the value is a yes/no (e.g. «ei»).
    display: str = ""


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
    #: `observation_health`: the same verdict `production_status` reports in its
    #: ``lovable_observation`` row (codes only).
    observation_health_ok: bool = True
    observation_health_detail: str = ""
    #: The newest successful pruning of raw observation data, and FRESH/STALE/NONE.
    retention_last_success_at: dt.datetime | None = None
    retention_freshness: str = "NONE"
    #: Per synchronised cohort (docs/adr/0202 §6), keyed by cohort: ``expected``,
    #: ``states`` now, and the newest reconciled run's ``checked``, ``review``,
    #: ``import_held``, ``stopped_active`` and ``activity``. Counts only.
    cohorts: dict[str, dict[str, Any]] = field(default_factory=dict)
    #: Whether the population is other than the pilot alone (the pages then show
    #: the per-cohort rows).
    per_cohort: bool = False


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
    #: In a list, a value longer than `SHORT_VALUE` is cut for display and this
    #: is True; the stored value is in `matter_changes` (the detail).
    truncated: bool = False
    #: The *stored* value itself is incomplete: Lovable's audit value was longer
    #: than `lovable_observation.AUDIT_VALUE_LIMIT` and was cut when Juristid
    #: copied it (`LovableSourceEventField.truncated`). Not even the detail has
    #: the original. Independent of `truncated`.
    source_truncated: bool = False


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
    #: The pilot run's `SyncMode` ("" for a wider reading), for its note.
    mode: str = ""
    #: Changes of unlinked source topics in this reading, counted only.
    unlinked: UnlinkedSummary = field(default_factory=UnlinkedSummary)


@dataclass
class Page(_AsDict):
    items: list[Any]
    page: int
    pages: int
    total: int
    page_size: int = PAGE_SIZE
    #: Items left out because their Teema is not one the reader may open: a
    #: count only, never a reference or a title.
    hidden: int = 0
    #: Totals over every page (not just this one), e.g. ``{"kind": {"NEW": 3},
    #: "mapping": {"PILOT_LINKED": 2}}`` — counts, never identities.
    totals: dict[str, dict[str, int]] = field(default_factory=dict)
    #: Changes of unlinked source topics, counted only (`AGGREGATE`).
    unlinked: UnlinkedSummary = field(default_factory=UnlinkedSummary)


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
# Constants of this read service
# ---------------------------------------------------------------------------

#: Readings (nights or wider readings) per page of `nights`.
NIGHTS_PAGE = 10
#: The overview's «in the period» indicators look back this many days.
STATUS_PERIOD_DAYS = 7
#: Decisions listed in one Teema's detail (newest first).
MATTER_DECISIONS = 200

PILOT_NIGHT = "PILOT_NIGHT"
OBSERVATION = "OBSERVATION"

FRESH, STALE, NONE = "FRESH", "STALE", "NONE"

#: A pilot night's result keys (`lovable_review`), from a `SyncOutcome` too.
_RESULT_OF_OUTCOME: dict[str, str] = {
    SyncOutcome.ELIGIBLE: lovable_review.NOT_APPLIED,
    SyncOutcome.REVIEW_REQUIRED: lovable_review.REVIEW,
    SyncOutcome.CONFLICT: lovable_review.CONFLICT,
    SyncOutcome.ALREADY_CONVERGED: lovable_review.SAME,
    SyncOutcome.LOCAL_TEST_CHANGE: lovable_review.LOCAL,
    SyncOutcome.FAILED: lovable_review.FAILED,
    SyncOutcome.APPLIED: lovable_review.APPLIED,
}

#: Fields the reconciliation compares with a Juristid value (B). The others are
#: compared source against source only.
_COMPARED_WITH_JURISTID = ("title", "owner", "next_action_text", "next_action_date", "onenote_link")
NOT_COMPARED_IN_JURISTID = "Juristides seda välja Lovable'iga ei võrrelda."

#: What a person could do next with a pending field, by the decision's reason.
#: Advice only: nothing here does it.
SUGGESTED_ACTIONS = {
    lovable_sync.TEXT_CHANGED: (
        "Ava teema ja otsusta uus järgmine tegevus (tee/oota/jälgi). Rea märgib lahendatuks haldur."
    ),
    lovable_sync.WITH_TEXT: (
        "Kuupäev käib koos uue sõnastusega: otsusta järgmine tegevus teemas. "
        "Rea märgib lahendatuks haldur."
    ),
    lovable_sync.HELD: (
        "Seda välja ei uuendata automaatselt: kanna muudatus vajadusel teemasse käsitsi. "
        "Rea märgib lahendatuks haldur."
    ),
    lovable_sync.MATTER_CLOSED: (
        "Teema on Juristides suletud: kontrolli, kas Lovable'i muudatus puudutab seda teemat. "
        "Rea märgib lahendatuks haldur."
    ),
    lovable_sync.NO_BASELINE: (
        "Viimati rakendatud väärtus pole teada: võrdle teemat Lovable'iga ja "
        "teata haldurile, milline väärtus kehtib."
    ),
    lovable_sync.UNRESOLVED_OWNER: (
        "Vastutaja nimi ei vasta üheselt ühele kasutajale: määra vastutaja teemas käsitsi. "
        "Rea märgib lahendatuks haldur."
    ),
    lovable_sync.NOT_ASSIGNABLE: (
        "Sellele inimesele ei saa praegu tööd määrata: määra vastutaja teemas käsitsi. "
        "Rea märgib lahendatuks haldur."
    ),
    lovable_sync.TITLE_REVIEWED: (
        "Pealkiri vaadati impordil üle: otsusta, kas Lovable'i uus pealkiri kehtib. "
        "Rea märgib lahendatuks haldur."
    ),
    lovable_sync.TITLE_EMPTY: "Lovable'i pealkiri on tühi: jäta Juristide pealkiri alles.",
    lovable_sync.NO_STEP: (
        "Juristides pole Lovable'ist tulnud tegevust: lisa tegevus teemas käsitsi. "
        "Rea märgib lahendatuks haldur."
    ),
    lovable_sync.NOT_SOURCE_STEP: (
        "Juristide tegevus pole Lovable'i sõnastus: muuda kuupäeva vajadusel käsitsi. "
        "Rea märgib lahendatuks haldur."
    ),
    lovable_sync.DATE_CLEARED: (
        "Lovable'is kustutati kuupäev: otsusta, kas tegevus jääb tähtajaga. "
        "Rea märgib lahendatuks haldur."
    ),
    lovable_sync.DATE_PAST: (
        "Uus kuupäev on möödas: otsusta teemas uus tähtaeg. Rea märgib lahendatuks haldur."
    ),
    lovable_sync.NOT_EXACT: (
        "Tegevuse kuupäev pole täpne päev: muuda kuupäeva vajadusel käsitsi. "
        "Rea märgib lahendatuks haldur."
    ),
    lovable_sync.LINK_NOT_HTTPS: "Link pole https-aadress: kontrolli linki Lovable'is.",
    lovable_sync.IMPORT_HELD: (
        "Import jättis selle välja ülevaatamiseks: võrdle teemat Lovable'iga ja kanna vajalik "
        "teemasse käsitsi. Rea märgib lahendatuks haldur."
    ),
}
_SUGGESTED_BY_OUTCOME: dict[str, str] = {
    SyncOutcome.CONFLICT: (
        "Juristides ja Lovable'is on erinev väärtus: võrdle neid teemas ja otsusta, kumb kehtib. "
        "Rea märgib lahendatuks haldur."
    ),
    SyncOutcome.ELIGIBLE: (
        "Ohutu uuendus: rakendub alles siis, kui omanik lubab APPLY-režiimi; "
        "seni võib selle teemas käsitsi teha."
    ),
}
_SUGGESTED_DEFAULT = "Vaata muudatus teemas üle. Rea märgib lahendatuks haldur."

AUDIT_MESSAGES = {
    AuditAccess.DISABLED: (
        "Lovable'i muutmisajalugu pole kättesaadav: selle lugemiseks pole omaniku kinnitatud "
        "lugemisõigust. Näidatakse ainult lugemiste vahelisi erinevusi."
    ),
    AuditAccess.UNAVAILABLE: (
        "Lovable'i muutmisajalugu pole kättesaadav: lugemine on sisse lülitatud, kuid ajalugu "
        "ei õnnestunud lugeda{code}. Näidatakse ainult lugemiste vahelisi erinevusi."
    ),
    AuditAccess.AVAILABLE: "Lovable'i muutmisajalugu on loetav.",
}
AUDIT_NOT_AVAILABLE_SHORT = "Lovable'i muutmisajalugu pole kättesaadav."
NO_OBSERVATION = "Laiemat lugemist pole veel tehtud."
#: Each night re-decides every pilot field still differing from what was last
#: applied, so a night's tally is the whole open backlog, not that night's change.
PENDING_TALLY_UNIT = (
    "välja viimase võrdluse seisuga (kõik seni lahendamata erinevused, "
    "mitte ainult selle öö muudatused)"
)

OBSERVATION_MESSAGES = {
    "BASELINE": "Esimene laiem lugemine: see on lähtepunkt. Muudatusi näeb alates järgmisest.",
    "COMPARED_EMPTY": "Lugemiste vahel ei tuvastatud muudatusi.",
    "FAILED": "Laiem lugemine ebaõnnestus ({code}); muudatusi ei saa näidata.",
    "RUNNING": "Laiem lugemine käib või katkes; muudatusi ei saa näidata.",
}


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------


def _reads(user: Any) -> bool:
    return bool(may_read_business_content(user))


def unlinked_detail() -> str:
    """The policy for unlinked source topics (docs/adr/0201 §6): `AGGREGATE`
    unless ``settings.LOVABLE_UNLINKED_DETAIL`` names `FIELD_NAMES`. Anything
    else — a typo included — is the safe default."""
    raw = str(getattr(settings, "LOVABLE_UNLINKED_DETAIL", UNMATCHED_DETAIL) or "")
    return FIELD_NAMES if raw.strip().upper() in (FIELD_NAMES, "FIELD_NAMES_ONLY") else AGGREGATE


def _aggregate_only() -> bool:
    return unlinked_detail() == AGGREGATE


def unlinked_note(total: int, *, events: bool = False) -> str:
    """«N muudatust teemades, millel pole Juristides kinnitatud vastet; …»."""
    if not total:
        return ""
    if events:
        noun = "sündmus" if total == 1 else "sündmust"
    else:
        noun = "muudatus" if total == 1 else "muudatust"
    return UNLINKED_NOTE.format(n=total, noun=noun)


def _change_field_names(names: Any, values: Any) -> set[str]:
    found = {str(n) for n in (names or [])}
    found |= {str(v.get("field")) for v in (values or []) if isinstance(v, dict)}
    return found


def _unlinked_summary(rows: list[tuple[str, str, Any, Any]]) -> UnlinkedSummary:
    """Counts over (kind, mapping, field_names, fields) of unlinked changes."""
    if not _aggregate_only():
        return UnlinkedSummary(policy=FIELD_NAMES)
    kinds: Counter[str] = Counter()
    mappings: Counter[str] = Counter()
    names: Counter[str] = Counter()
    for kind, mapping, field_names, values in rows:
        kinds[kind] += 1
        mappings[mapping] += 1
        names.update(_change_field_names(field_names, values))
    total = sum(kinds.values())
    return UnlinkedSummary(
        policy=AGGREGATE,
        total=total,
        kind=dict(sorted(kinds.items())),
        mapping=dict(sorted(mappings.items())),
        field=dict(sorted(names.items())),
        note=unlinked_note(total),
    )


def _now(now: dt.datetime | None) -> dt.datetime:
    return now or timezone.now()


def _local_day(moment: dt.datetime) -> dt.date:
    return moment.astimezone(TALLINN).date()


def _local(moment: dt.datetime | None) -> str:
    if moment is None:
        return ""
    return moment.astimezone(TALLINN).strftime("%d.%m.%Y kell %H.%M")


def _window(
    since: dt.date | None, until: dt.date | None, now: dt.datetime
) -> tuple[dt.datetime, dt.datetime]:
    """[start, end) in Tallinn days, never longer than `MAX_WINDOW_DAYS`."""
    last = until or _local_day(now)
    first = since or last - dt.timedelta(days=MAX_WINDOW_DAYS)
    if first > last:
        first, last = last, first
    if (last - first).days > MAX_WINDOW_DAYS:
        first = last - dt.timedelta(days=MAX_WINDOW_DAYS)
    start = dt.datetime.combine(first, dt.time.min, tzinfo=TALLINN)
    end = dt.datetime.combine(last + dt.timedelta(days=1), dt.time.min, tzinfo=TALLINN)
    return start, end


def _norm_ref(raw: str) -> str:
    """``2026/243``, ``2026-243`` and ``2026_243`` are one reference."""
    text = (raw or "").strip()
    match = re.fullmatch(r"(\d{4})\s*[_/\-]\s*(\d+)", text)
    return f"{match.group(1)}_{int(match.group(2))}" if match else text


def _cut(text: str) -> tuple[str, bool]:
    if len(text) <= SHORT_VALUE:
        return text, False
    return text[: SHORT_VALUE - 1].rstrip() + "…", True


def _label(name: str) -> str:
    return LOVABLE_FIELD_LABELS.get(name, name)


def _matter_url(pk: Any) -> str:
    return reverse("matters:matter_detail", kwargs={"pk": pk}) if pk else ""


def _pages(total: int, size: int) -> int:
    return max(1, (total + size - 1) // size)


def _page(
    items: list[Any],
    page: int,
    *,
    size: int = PAGE_SIZE,
    hidden: int = 0,
    totals: dict[str, dict[str, int]] | None = None,
) -> Page:
    pages = _pages(len(items), size)
    number = min(max(1, page), pages)
    chunk = items[(number - 1) * size : number * size]
    return Page(
        items=chunk,
        page=number,
        pages=pages,
        total=len(items),
        page_size=size,
        hidden=hidden,
        totals=totals or {},
    )


def _empty(size: int = PAGE_SIZE) -> Page:
    return Page(items=[], page=1, pages=1, total=0, page_size=size)


def _hours_since(moment: dt.datetime | None, now: dt.datetime) -> int | None:
    if moment is None:
        return None
    return max(0, int((now - moment).total_seconds() // 3600))


def _freshness(moment: dt.datetime | None, now: dt.datetime) -> str:
    if moment is None:
        return NONE
    return FRESH if now - moment <= lovable_sync.STALE_AFTER else STALE


def _failure_code(error: str) -> str:
    return (error or "").split(":")[0].strip()


def _ref_order(reference: str) -> tuple[int, int, str]:
    year, _, number = (reference or "").partition("_")
    if year.isdigit() and number.isdigit():
        return (-int(year), -int(number), reference)
    return (0, 0, reference)


_FIELD_POSITION = {name: i for i, name in enumerate(lovable_review.FIELD_ORDER)}


def _field_order(name: str) -> int:
    return _FIELD_POSITION.get(name, len(_FIELD_POSITION))


def _value(name: str, value: Any) -> str:
    """A canonical source value as text, the wider reading's list fields too."""
    if name == "responsible_changes" and isinstance(value, list):
        parts = [
            " → ".join(str(p) for p in item[:2] if p)
            + (
                f" ({lovable_review.shown('received', item[2])})"
                if len(item) > 2 and item[2]
                else ""
            )
            for item in value
            if isinstance(item, list)
        ]
        return "; ".join(p for p in parts if p) or "—"
    if name in ("stage_links", "topic_links") and isinstance(value, list):
        return f"{len(value)} seost" if value else "—"
    return lovable_review.shown(name, value)


def _shown(name: str, value: Any) -> str:
    """A canonical value, or a Juristid step (`lovable_sync._action_dict`), as text."""
    if isinstance(value, dict):
        parts = [
            lovable_review.shown("next_action_date", value.get("target_date"))
            if value.get("target_date")
            else "",
            str(value.get("text") or ""),
        ]
        return " · ".join(p for p in parts if p) or "—"
    return lovable_review.shown(name, value)


# ---------------------------------------------------------------------------
# Summary sentences (built from field names — never from values, never AI text)
# ---------------------------------------------------------------------------

_FIELD_SENTENCES = {
    "title": "Pealkiri muutus",
    "owner": "Vastutaja muutus",
    "stage": "Hetkeseis muutus",
    "onenote_link": "OneNote'i link muutus",
    "opinion_deadline": "Arvamuse tähtaeg muutus",
    "addressee": "Adressaat («Kellele») muutus",
    "koda_initiative": "Koja ettepaneku märge muutus",
    "legal_act": "Õigusakt muutus",
    "received": "Saabumise kuupäev muutus",
    "commencements": "Lovable'i jõustumise hinnangud muutusid",
    "operational_scope": "Operatiivne ulatus muutus",
}


def summary_sentence(
    field_names: list[str] | set[str],
    *,
    kind: str = ObservedChangeKind.CHANGED,
    previous_reference: str = "",
    reference: str = "",
    scope_changed: bool = False,
    children_only: bool = False,
) -> str:
    """One Estonian sentence for a topic's change, from field names only."""
    names = set(field_names)
    parts: list[str] = []
    if kind == ObservedChangeKind.NEW:
        parts.append("Lovable'is on uus teema")
    elif kind == ObservedChangeKind.MISSING:
        parts.append("Teema kadus Lovable'i lugemisest")
    elif kind == ObservedChangeKind.BACK:
        parts.append("Teema on taas Lovable'i lugemises")
    elif kind == lovable_review.ENROLLED:
        parts.append("Teema lisandus võrdlusesse (Lovable'is ei muutunud midagi)")
    elif kind == ObservedChangeKind.RENUMBERED:
        parts.append(
            f"Lovable'i number muutus: {previous_reference} → {reference}"
            if previous_reference
            else "Lovable'i number muutus"
        )
    if "next_action_text" in names and "next_action_date" in names:
        parts.append("Järgmise tegevuse sõnastus ja kuupäev muutusid koos")
    elif "next_action_text" in names:
        parts.append("Järgmise tegevuse sõnastus muutus")
    elif "next_action_date" in names:
        parts.append("Järgmise tegevuse kuupäev muutus")
    if "sent" in names and "sent_date" in names:
        parts.append("Lovable'i «Välja» märge ja kuupäev muutusid")
    elif "sent" in names:
        parts.append("Lovable'i «Välja» märge muutus")
    elif "sent_date" in names:
        parts.append("Lovable'i «Välja» kuupäev muutus")
    for name in sorted(names, key=_field_order):
        sentence = _FIELD_SENTENCES.get(name)
        if sentence and not (name == "operational_scope" and scope_changed):
            parts.append(sentence)
    known = set(_FIELD_SENTENCES) | {"next_action_text", "next_action_date", "sent", "sent_date"}
    other = sorted(n for n in names if n not in known)
    if other:
        parts.append("Muutus: " + ", ".join(_label(n) for n in other))
    if scope_changed:
        parts.append("Operatiivne ulatus muutus")
    if children_only:
        parts.append("Muutusid ainult seotud kirjed (jõustumised, vastutajad, etapiseosed)")
    return "; ".join(parts) + "." if parts else ""


# ---------------------------------------------------------------------------
# Who may see which Teema
# ---------------------------------------------------------------------------


@dataclass
class _Sight:
    """The pilot's states, and which of their Teemad this reader may open."""

    states: dict[str, LovableSyncState]  # by source id
    by_matter: dict[Any, LovableSyncState]
    visible: dict[Any, Any]  # matter pk → Matter
    live: set[Any]


def _sight(user: Any) -> _Sight:
    from app.matters.models import Matter

    states = list(
        LovableSyncState.objects.select_related("matter", "matter__owner", "observed_run")
    )
    ids = [s.matter_id for s in states]
    live = {s.matter_id for s in states if getattr(s.matter, "deleted_at", None) is None}
    visible: dict[Any, Any] = {}
    if _reads(user) and ids:
        allowed = set(
            Matter.objects.visible_to(user).filter(pk__in=ids).values_list("pk", flat=True)
        )
        visible = {s.matter_id: s.matter for s in states if s.matter_id in allowed}
    return _Sight(
        states={str(s.source_id): s for s in states},
        by_matter={s.matter_id: s for s in states},
        visible={pk: m for pk, m in visible.items() if pk in live},
        live=live,
    )


def _visible_matter_ids(user: Any) -> Any:
    from app.matters.models import Matter

    return Matter.objects.visible_to(user).values("pk")


# ---------------------------------------------------------------------------
# The audit ledger's availability
# ---------------------------------------------------------------------------


def _audit() -> AuditStatus:
    run = (
        LovableObservationRun.objects.exclude(status=ObservationRunStatus.RUNNING)
        .order_by("-started_at")
        .only("audit_access", "audit_error")
        .first()
    )
    access = run.audit_access if run is not None else AuditAccess.DISABLED
    if access not in AuditAccess.values:
        access = AuditAccess.DISABLED
    if access != AuditAccess.AVAILABLE:
        code = f" ({run.audit_error})" if run is not None and run.audit_error else ""
        message = AUDIT_MESSAGES[AuditAccess(access)].format(code=code)
        return AuditStatus(available=False, access=access, message=message)
    stats = LovableSourceEvent.objects.aggregate(n=Count("pk"), last=Max("occurred_at"))
    return AuditStatus(
        available=True,
        access=access,
        message=AUDIT_MESSAGES[AuditAccess.AVAILABLE],
        last_event_at=stats["last"],
        events_stored=stats["n"] or 0,
    )


# ---------------------------------------------------------------------------
# Operational health of the wider observation and its retention (monitoring)
# ---------------------------------------------------------------------------

#: A wider reading still RUNNING after this long is stuck.
OBSERVATION_RUNNING_LIMIT = dt.timedelta(hours=2)


@dataclass
class ObservationHealth(_AsDict):
    """`production_status`'s ``lovable_observation`` row, and the overview's.

    ``problems`` are short codes with counts only — never business text."""

    ok: bool
    detail: str
    observe: bool
    problems: list[str]
    last_success_at: dt.datetime | None
    retention_last_success_at: dt.datetime | None
    retention_last_status: str
    retention_failure_code: str


def _observation_data_exists() -> bool:
    return (
        LovableObservationSnapshot.objects.exists()
        or LovableObservedChange.objects.exists()
        or LovableSourceEvent.objects.exists()
    )


def observation_health(*, now: dt.datetime | None = None) -> ObservationHealth:
    """Is the wider observation (when on) and its retention job healthy?

    Red when observation is ON and: it has never run; the newest reading
    FAILED; a reading has been RUNNING over `OBSERVATION_RUNNING_LIMIT`; no
    reading SUCCEEDED within `lovable_sync.STALE_AFTER` (26 h); or the audit
    ledger is expected (``LOVABLE_AUDIT_ENABLED``, or the newest finished
    reading tried it) and the newest finished reading could not read it. Red,
    whether observation is on or off, when raw observation data exists and the
    newest retention run FAILED or none SUCCEEDED within 26 h — paused
    observation must not stop the 30-day limit. Reads only: the control row is
    looked up, never created (no `lovable_sync.control()`)."""
    moment = _now(now)
    ctl = LovableSyncControl.objects.filter(pk=1).first()
    observe = bool(ctl and ctl.observe)
    runs = LovableObservationRun.objects.order_by("-started_at")
    newest = runs.first()
    success = runs.filter(status=ObservationRunStatus.SUCCEEDED).first()
    success_at = (success.taken_at or success.started_at) if success else None
    problems: list[str] = []
    if observe:
        if newest is None:
            problems.append("observation on but never ran")
        elif newest.status == ObservationRunStatus.FAILED:
            problems.append(f"last observation failed ({_failure_code(newest.error) or '?'})")
        elif (
            newest.status == ObservationRunStatus.RUNNING
            and moment - newest.started_at > OBSERVATION_RUNNING_LIMIT
        ):
            problems.append("an observation has been running over 2 hours")
        if newest is not None and (
            success is None or moment - success.started_at > lovable_sync.STALE_AFTER
        ):
            problems.append("no successful observation in the last 26 hours")
        finished = runs.exclude(status=ObservationRunStatus.RUNNING).first()
        if finished is not None:
            expected = bool(getattr(settings, "LOVABLE_AUDIT_ENABLED", False)) or (
                finished.audit_access == AuditAccess.UNAVAILABLE
            )
            if expected and finished.audit_access != AuditAccess.AVAILABLE:
                code = f", {finished.audit_error}" if finished.audit_error else ""
                problems.append(f"audit history {finished.audit_access}{code}")
    retention = LovableRetentionRun.objects.order_by("-started_at")
    last_prune = retention.first()
    good_prune = retention.filter(status=RetentionRunStatus.SUCCEEDED).first()
    if _observation_data_exists():
        if last_prune is not None and last_prune.status == RetentionRunStatus.FAILED:
            problems.append(f"retention failed ({_failure_code(last_prune.error) or '?'})")
        elif good_prune is None or moment - good_prune.started_at > lovable_sync.STALE_AFTER:
            problems.append("no successful retention run in the last 26 hours")
    if problems:
        detail = "; ".join(problems)
    elif observe:
        detail = f"on, last success {success.run_key}" if success else "on"
    else:
        detail = "off"
    return ObservationHealth(
        ok=not problems,
        detail=detail,
        observe=observe,
        problems=problems,
        last_success_at=success_at,
        retention_last_success_at=good_prune.started_at if good_prune else None,
        retention_last_status=last_prune.status if last_prune else "",
        retention_failure_code=_failure_code(last_prune.error)
        if last_prune is not None and last_prune.status == RetentionRunStatus.FAILED
        else "",
    )


# ---------------------------------------------------------------------------
# Juristid now (B), for many Teemad in a constant number of queries
# ---------------------------------------------------------------------------


def current_values_bulk(matters: list[Any]) -> dict[Any, dict[str, Any]]:
    """`lovable_sync.current_values` for many Teemad at once — the same answer
    (asserted in tests), in two queries instead of two per Teema."""
    from app.legacy_import.models import MatterSourceReference
    from app.workflow.enums import ActionStatus
    from app.workflow.models import NextAction

    ids = [m.pk for m in matters]
    links: dict[Any, str] = {}
    for matter_id, url in (
        MatterSourceReference.objects.filter(matter_id__in=ids, source_system=SOURCE_SYSTEM)
        .order_by("matter_id", "-created_at")
        .values_list("matter_id", "onenote_url")
    ):
        links.setdefault(matter_id, url or "")
    steps: dict[Any, Any] = {}
    for action in NextAction.objects.filter(matter_id__in=ids, status=ActionStatus.OPEN):
        steps.setdefault(action.matter_id, action)
    return {
        m.pk: {
            "title": lovable_sync.text_value(m.title),
            "owner": str(m.owner_id) if m.owner_id else None,
            "onenote_link": links.get(m.pk, ""),
            "next_action": lovable_sync._action_dict(steps.get(m.pk)),
            "is_open": m.is_open and getattr(m, "deleted_at", None) is None,
        }
        for m in matters
    }


def _juristid_shown(name: str, matter: Any, b: dict[str, Any]) -> str:
    if name not in _COMPARED_WITH_JURISTID:
        return NOT_COMPARED_IN_JURISTID
    if name == "title":
        return b["title"] or "—"
    if name == "owner":
        owner = getattr(matter, "owner", None)
        return owner.display_name if owner is not None else "—"
    step = b.get("next_action")
    if name == "next_action_text":
        return (step or {}).get("text") or "—"
    if name == "next_action_date":
        return lovable_review.shown(name, (step or {}).get("target_date"))
    return b.get("onenote_link") or "—"


# ---------------------------------------------------------------------------
# The review queue's core: which pilot fields are still pending
# ---------------------------------------------------------------------------

_SETTLED = (SyncOutcome.ALREADY_CONVERGED, SyncOutcome.APPLIED)


@dataclass
class _Pending:
    state: LovableSyncState
    field: str
    applied: Any
    observed: Any
    newest: dict[str, Any] | None
    first_at: dt.datetime | None
    last_at: dt.datetime | None
    nights: int


def _decided(pairs: set[tuple[Any, str]]) -> Any:
    """Every night's decision on these (state, field) pairs. Checks are not nights."""
    return LovableSyncDecision.objects.filter(
        state_id__in=sorted({state for state, _ in pairs}, key=str),
        field__in=sorted({name for _, name in pairs}),
    ).exclude(run__trigger=SyncTrigger.CHECK)


def _newest_decisions(pairs: set[tuple[Any, str]]) -> dict[tuple[Any, str], dict[str, Any]]:
    """The newest decision per (state, field): one row per pair, however many nights."""
    if not pairs:
        return {}
    rows = (
        _decided(pairs)
        .order_by("state_id", "field", "-run__started_at", "-created_at")
        .distinct("state_id", "field")
        .values(
            "state_id",
            "field",
            "outcome",
            "reason",
            "source",
            "run_id",
            run_started=F("run__started_at"),
        )
    )
    return {
        (r["state_id"], r["field"]): dict(r) for r in rows if (r["state_id"], r["field"]) in pairs
    }


def _sources_since_settled(
    pairs: set[tuple[Any, str]],
) -> dict[tuple[Any, str], list[tuple[Any, dt.datetime]]]:
    """Per (state, field), after its last settled decision (`_SETTLED`): each
    distinct source value and the first night it was decided on. One row per
    distinct value, not per night — a field held for months is still one row."""
    if not pairs:
        return {}
    # The last settled decision per pair: few rows (settling is rare).
    last_settled = {
        (r["state_id"], r["field"]): (r["run_started"], r["created_at"])
        for r in _decided(pairs)
        .filter(outcome__in=_SETTLED)
        .order_by("state_id", "field", "-run__started_at", "-created_at")
        .distinct("state_id", "field")
        .values("state_id", "field", "created_at", run_started=F("run__started_at"))
    }
    # Pairs never settled share one condition per field; a settled pair its own.
    unsettled: dict[str, list[Any]] = {}
    condition = Q()
    for state, name in pairs:
        if (state, name) not in last_settled:
            unsettled.setdefault(name, []).append(state)
            continue
        at, created = last_settled[(state, name)]
        condition |= Q(state_id=state, field=name) & (
            Q(run__started_at__gt=at) | Q(run__started_at=at, created_at__gt=created)
        )
    for name, states in unsettled.items():
        condition |= Q(field=name, state_id__in=states)
    rows = (
        _decided(pairs)
        .filter(condition)
        .values("state_id", "field", "source")
        .annotate(first=Min("run__started_at"))
        .order_by()
    )
    out: dict[tuple[Any, str], list[tuple[Any, dt.datetime]]] = {}
    for row in rows:
        key = (row["state_id"], row["field"])
        if key in pairs:
            out.setdefault(key, []).append((row["source"], row["first"]))
    return out


def _nights_since(firsts: dict[tuple[Any, str], dt.datetime]) -> dict[tuple[Any, str], int]:
    """How many nights decided on each pair from its first moment on. Pairs that
    share a field and a first night — the usual case: a held field is first seen
    the same night across a cohort — share one condition, so the query stays
    small; the answer is one count per pair."""
    if not firsts:
        return {}
    groups: dict[tuple[str, dt.datetime], list[Any]] = {}
    for (state, name), first in firsts.items():
        groups.setdefault((name, first), []).append(state)
    condition = Q()
    for (name, first), states in groups.items():
        condition |= Q(field=name, state_id__in=states, run__started_at__gte=first)
    rows = (
        _decided(set(firsts))
        .filter(condition)
        .values("state_id", "field")
        .annotate(n=Count("run_id", distinct=True))
        .order_by()
    )
    return {(r["state_id"], r["field"]): r["n"] for r in rows}


def _pending_fields(states: list[LovableSyncState]) -> list[_Pending]:
    """Every field whose last applied source differs from the last observed one,
    or that the import holds for review, unless its newest decision settled it.

    A bounded number of queries whose rows do not grow with the nights: the
    newest decision per pair, the distinct source values since the last settled
    decision, and a count of nights (audit 2026-11-03 P2-5) — the same answers
    as reading every night's row, without reading them."""
    candidates: list[tuple[LovableSyncState, str, Any, Any]] = []
    for state in states:
        observed = state.observed or {}
        # The import's own review hold waits for a person even when Lovable has
        # not changed since (docs/adr/0202 §6).
        held = lovable_sync.held_by_import(state.applied)
        for name in FIELDS:
            if name not in observed:
                continue
            applied = (state.applied.get(name) or {}).get("source")
            if name not in held and lovable_review._key(name, applied) == lovable_review._key(
                name, observed[name]
            ):
                continue
            candidates.append((state, name, applied, observed[name]))
    newest_of = _newest_decisions({(s.pk, name) for s, name, *_ in candidates})
    open_pairs = {
        (s.pk, name)
        for s, name, *_ in candidates
        if (newest_of.get((s.pk, name)) or {}).get("outcome") not in _SETTLED
    }
    sources = _sources_since_settled(open_pairs)
    firsts: dict[tuple[Any, str], dt.datetime] = {}
    for state, name, _applied, observed in candidates:
        key = (state.pk, name)
        values = sources.get(key)
        if key not in open_pairs or not values:
            continue
        want = lovable_review._key(name, observed)
        matching = [at for value, at in values if lovable_review._key(name, value) == want]
        firsts[key] = min(matching) if matching else min(at for _, at in values)
    nights = _nights_since(firsts)
    out: list[_Pending] = []
    for state, name, applied, observed in candidates:
        key = (state.pk, name)
        if key not in open_pairs:
            continue
        newest = newest_of.get(key)
        last_at = newest["run_started"] if newest is not None else None
        if last_at is None and state.observed_run_id is not None:
            last_at = state.observed_run.started_at if state.observed_run else None
        first_at = firsts.get(key)
        out.append(
            _Pending(
                state=state,
                field=name,
                applied=applied,
                observed=observed,
                newest=newest,
                first_at=first_at if first_at is not None else last_at,
                last_at=last_at,
                nights=nights.get(key, 0) if first_at is not None else 0,
            )
        )
    return out


def _queue_item(
    pending: _Pending, matter: Any, b: dict[str, Any], today: dt.date, *, full: bool
) -> QueueItem:
    newest = pending.newest or {}
    outcome = str(newest.get("outcome") or "")
    reason = str(newest.get("reason") or "")
    outcome_label = SyncOutcome(outcome).label if outcome in SyncOutcome.values else "Otsus puudub"
    name = pending.field

    def value(text: str) -> str:
        return text if full else _cut(text)[0]

    if reason in SUGGESTED_ACTIONS:
        suggested = SUGGESTED_ACTIONS[reason]
    else:
        suggested = _SUGGESTED_BY_OUTCOME.get(outcome, _SUGGESTED_DEFAULT)
    return QueueItem(
        matter_id=str(matter.pk),
        reference=pending.state.reference,
        title=matter.title,
        matter_url=_matter_url(matter.pk),
        field=name,
        label=_label(name),
        first_observed_at=pending.first_at,
        last_observed_at=pending.last_at,
        age_days=(today - _local_day(pending.first_at)).days if pending.first_at else None,
        last_applied=value(lovable_review.shown(name, pending.applied)),
        juristid_now=value(_juristid_shown(name, matter, b)),
        lovable_now=value(lovable_review.shown(name, pending.observed)),
        outcome=outcome,
        outcome_label=outcome_label,
        reason=reason,
        explanation=LOVABLE_REASONS.get(reason, "") or outcome_label,
        suggested_action=suggested,
        nights_pending=pending.nights,
    )


def _visible_queue(
    user: Any, sight: _Sight, now: dt.datetime, *, full: bool = False
) -> tuple[list[QueueItem], int]:
    states = [s for s in sight.states.values() if s.matter_id in sight.live]
    pending = _pending_fields(states)
    shown = [p for p in pending if p.state.matter_id in sight.visible]
    hidden = len(pending) - len(shown)
    current = current_values_bulk([sight.visible[m] for m in {p.state.matter_id for p in shown}])
    today = _local_day(now)
    items = [
        _queue_item(
            p, sight.visible[p.state.matter_id], current[p.state.matter_id], today, full=full
        )
        for p in shown
    ]
    far = dt.datetime.max.replace(tzinfo=dt.UTC)
    items.sort(
        key=lambda i: (
            i.first_observed_at or far,
            -_ref_order(i.reference)[0],
            -_ref_order(i.reference)[1],
            _field_order(i.field),
        )
    )
    return items, hidden


# ---------------------------------------------------------------------------
# Pilot nights → readings
# ---------------------------------------------------------------------------


def _baselines() -> dict[Any, LovableSyncRun | None]:
    """Each scheduled run's baseline, as `lovable_review.nightly_history` chooses it."""
    scheduled = list(
        LovableSyncRun.objects.filter(trigger=SyncTrigger.SCHEDULED).order_by("-started_at")
    )
    with_snapshot = set(
        LovableSyncSnapshot.objects.filter(run__trigger=SyncTrigger.SCHEDULED).values_list(
            "run_id", flat=True
        )
    )
    usable = {r.pk for r in scheduled if r.pk in with_snapshot and lovable_sync.is_baseline_run(r)}
    out: dict[Any, LovableSyncRun | None] = {}
    for position, run in enumerate(scheduled):
        out[run.pk] = next((r for r in scheduled[position + 1 :] if r.pk in usable), None)
    return out


def _pilot_topic(topic: Any, state: LovableSyncState, *, full: bool) -> TopicDiff:
    fields = []
    for change in topic.fields:
        before, cut_before = (change.before, False) if full else _cut(change.before)
        after, cut_after = (change.after, False) if full else _cut(change.after)
        fields.append(
            FieldDiff(
                field=change.name,
                label=change.label,
                before=before,
                after=after,
                result=change.result,
                result_label=change.result_label,
                reason=change.note,
                truncated=cut_before or cut_after,
            )
        )
    kind = topic.kind
    reference, previous = state.reference, ""
    if topic.renumbered is not None:
        kind = ObservedChangeKind.RENUMBERED
        previous, reference = topic.renumbered
    return TopicDiff(
        source_id=str(state.source_id),
        reference=reference,
        kind=kind,
        population=[str(ObservationScope.PILOT)],
        mapping=MappingStatus.PILOT_LINKED,
        title=topic.title,
        matter_id=str(state.matter_id),
        matter_url=_matter_url(state.matter_id),
        fields=fields,
        previous_reference=previous,
        summary=summary_sentence(
            [f.field for f in fields],
            kind=kind,
            previous_reference=previous,
            reference=reference,
        ),
    )


def _pilot_readings(user: Any, now: dt.datetime, *, full: bool) -> list[Reading]:
    if not _reads(user):
        return []
    history = lovable_review.nightly_history(user, show_all=True, now=now)
    if not history.nights:
        return []
    baselines = _baselines()
    by_matter = {s.matter_id: s for s in LovableSyncState.objects.all()}
    readings = []
    for night in history.nights:
        run = night.run
        topics = [
            _pilot_topic(t, by_matter[t.matter_id], full=full)
            for t in night.topics
            if t.matter_id in by_matter
        ]
        outcomes = Counter(f.result for t in topics for f in t.fields)
        baseline = baselines.get(run.pk)
        readings.append(
            Reading(
                run_id=str(run.pk),
                kind=PILOT_NIGHT,
                taken_at=run.snapshot_taken_at or run.started_at,
                baseline_taken_at=(
                    (baseline.snapshot_taken_at or baseline.started_at) if baseline else None
                ),
                status=run.status,
                status_label=night.status_label,
                comparison=night.comparison,
                message=" ".join(p for p in (night.warning, night.message) if p),
                cohort_size=(run.counts or {}).get("checked"),
                new_topics=0,
                # A Teema entering the comparison (`ENROLLED`) is not a change.
                changed_topics=sum(1 for t in topics if t.kind != lovable_review.ENROLLED)
                + night.hidden,
                missing_topics=sum(1 for t in topics if t.kind == ObservedChangeKind.MISSING),
                changed_fields=sum(len(t.fields) for t in topics),
                outcomes=dict(sorted(outcomes.items())),
                topics=topics,
                hidden=night.hidden,
                mode=run.mode,
            )
        )
    return readings


def _outcome_key(outcome: str) -> str:
    return _RESULT_OF_OUTCOME.get(outcome, outcome) if outcome else ""


def _topic_matches(
    topic: TopicDiff, *, outcome: str, field_name: str, reference: str, text: str
) -> bool:
    if field_name and not any(f.field == field_name for f in topic.fields):
        return False
    if outcome and not any(f.result == outcome for f in topic.fields):
        return False
    if reference and reference not in (topic.reference, topic.previous_reference):
        return False
    if text:
        needle = text.casefold()
        # Only what this reader is shown: a withheld value or a hidden title is
        # not in the topic, so it can never match.
        haystack = [topic.reference, topic.previous_reference, topic.title, topic.summary]
        haystack += [v for f in topic.fields if not f.withheld for v in (f.before, f.after)]
        if not any(needle in (h or "").casefold() for h in haystack):
            return False
    return True


# ---------------------------------------------------------------------------
# Wider readings → topics
# ---------------------------------------------------------------------------


def _population_q(population: str) -> Q:
    if population == ObservationScope.OPERATIONAL:
        # Operational on either side: now, or until this reading (scope moved).
        return Q(operational=True) | Q(scope_changed=True)
    if population == ObservationScope.PILOT:
        return Q(in_pilot=True)
    return Q()


def _seen_q(user: Any) -> Q:
    """A pilot topic only when its Teema is one the reader may open (and is not
    deleted); a topic with no approved link always (field names only)."""
    return ~Q(mapping=MappingStatus.PILOT_LINKED) | Q(
        mapping=MappingStatus.PILOT_LINKED, matter_id__in=_visible_matter_ids(user)
    )


def _observed_topic(change: LovableObservedChange, visible: set[Any], *, full: bool) -> TopicDiff:
    population: list[str] = [ObservationScope.ALL_LIVE]
    if change.operational or change.scope_changed:
        population.append(ObservationScope.OPERATIONAL)
    if change.in_pilot:
        population.append(ObservationScope.PILOT)
    linked = (
        change.mapping == MappingStatus.PILOT_LINKED
        and change.matter_id is not None
        and change.matter_id in visible
    )
    names = [str(n) for n in (change.field_names or [])]
    values = {
        str(item.get("field")): item for item in (change.fields or []) if isinstance(item, dict)
    }
    for name in values:
        if name not in names:
            names.append(name)
    names.sort(key=_field_order)
    fields = []
    for name in names:
        if linked and name in values:
            before = _value(name, values[name].get("before"))
            after = _value(name, values[name].get("after"))
            cut_b = cut_a = False
            if not full:
                (before, cut_b), (after, cut_a) = _cut(before), _cut(after)
            fields.append(FieldDiff(name, _label(name), before, after, truncated=cut_b or cut_a))
        else:
            # Field names only (`UNMATCHED_DETAIL`), never a value.
            fields.append(FieldDiff(name, _label(name), "", "", withheld=True))
    matter_id: str | None = None
    if linked or (
        # The candidate's link identifies the source record: `FIELD_NAMES` only.
        not _aggregate_only()
        and change.mapping == MappingStatus.REFERENCE_CANDIDATE
        and change.matter_id is not None
        and change.matter_id in visible
    ):
        matter_id = str(change.matter_id)
    return TopicDiff(
        source_id=str(change.source_id),
        reference=change.reference,
        kind=change.kind,
        population=population,
        mapping=change.mapping,
        title=change.matter.title if linked and change.matter is not None else "",
        matter_id=matter_id,
        matter_url=_matter_url(matter_id),
        fields=fields,
        previous_reference=change.previous_reference,
        scope_changed=change.scope_changed,
        children_only=change.children_only,
        summary=summary_sentence(
            names,
            kind=change.kind,
            previous_reference=change.previous_reference,
            reference=change.reference,
            scope_changed=change.scope_changed,
            children_only=change.children_only,
        ),
    )


def _visible_set(user: Any, matter_ids: set[Any]) -> set[Any]:
    from app.matters.models import Matter

    if not matter_ids or not _reads(user):
        return set()
    return set(
        Matter.objects.visible_to(user).filter(pk__in=matter_ids).values_list("pk", flat=True)
    )


def _changes_q(
    user: Any,
    *,
    population: str,
    kind: str = "",
    field_name: str = "",
    mapping: str = "",
    reference: str = "",
    text: str = "",
) -> Q:
    q = _population_q(population) & _seen_q(user)
    if kind:
        q &= Q(kind=kind)
    if mapping:
        q &= Q(mapping=mapping)
    if field_name:
        q &= Q(field_names__contains=[field_name]) | Q(fields__contains=[{"field": field_name}])
    if reference:
        q &= Q(reference=reference) | Q(previous_reference=reference)
    if text:
        # A title matches only for a linked pilot Teema the reader may open
        # (`_seen_q` already left the others out); a source topic with no
        # approved link is matched by its number only.
        q &= (
            Q(reference__icontains=text)
            | Q(previous_reference__icontains=text)
            | Q(mapping=MappingStatus.PILOT_LINKED, matter__title__icontains=text)
        )
    return q


def _observation_reading(
    run: LovableObservationRun,
    changes: list[LovableObservedChange],
    hidden: int,
    visible: set[Any],
    population: str,
    *,
    full: bool,
    unlinked: list[LovableObservedChange] | None = None,
) -> Reading:
    """``changes`` become topics; ``unlinked`` (`AGGREGATE`) are only counted —
    in the reading's totals and in ``Reading.unlinked``, never as topics."""
    unlinked = unlinked or []
    counts = run.counts or {}
    if run.status == ObservationRunStatus.FAILED:
        comparison = "NO_SNAPSHOT"
        message = OBSERVATION_MESSAGES["FAILED"].format(code=_failure_code(run.error) or "?")
    elif run.status == ObservationRunStatus.RUNNING:
        comparison, message = "NO_SNAPSHOT", OBSERVATION_MESSAGES["RUNNING"]
    elif run.baseline_id is None:
        comparison, message = "BASELINE", OBSERVATION_MESSAGES["BASELINE"]
    else:
        comparison = "COMPARED"
        message = "" if changes or hidden or unlinked else OBSERVATION_MESSAGES["COMPARED_EMPTY"]
    topics = [_observed_topic(c, visible, full=full) for c in changes]
    summary = _unlinked_summary([(c.kind, c.mapping, c.field_names, c.fields) for c in unlinked])
    kinds = Counter(c.kind for c in [*changes, *unlinked])
    size_key = "operational" if population == ObservationScope.OPERATIONAL else "live"
    size = counts.get(size_key)
    return Reading(
        run_id=str(run.pk),
        kind=OBSERVATION,
        taken_at=run.taken_at or run.started_at,
        baseline_taken_at=(run.baseline.taken_at or run.baseline.started_at)
        if run.baseline_id and run.baseline
        else None,
        status=run.status,
        status_label=ObservationRunStatus(run.status).label
        if run.status in ObservationRunStatus.values
        else run.status,
        comparison=comparison,
        message=message,
        cohort_size=size if isinstance(size, int) else None,
        new_topics=kinds.get(ObservedChangeKind.NEW, 0),
        changed_topics=len(changes) + hidden + len(unlinked),
        missing_topics=kinds.get(ObservedChangeKind.MISSING, 0),
        changed_fields=sum(len(t.fields) for t in topics) + sum(summary.field.values()),
        outcomes=dict(sorted(kinds.items())),
        topics=topics,
        hidden=hidden,
        unlinked=summary,
    )


# ---------------------------------------------------------------------------
# The questions. Every function takes the requesting user first.
# ---------------------------------------------------------------------------


def _indicator(
    key: str,
    label: str,
    value: int | None,
    denominator: str,
    window: str,
    reason: str = "",
) -> Indicator:
    return Indicator(key, label, value, denominator, window, reason if value is None else "")


def status(user: Any, *, now: dt.datetime | None = None) -> SyncStatus:
    """The mode, freshness, source counts and outstanding review, with warnings.

    Reads only — it never calls `lovable_sync.control()` (which creates the
    switch row) or `sync_health()`/`due()` (which call it). Everything here is a
    count or a time, never a title or a value, so it answers a technical
    administrator too; the period's pilot changes are counted through
    `lovable_review` and so only as far as the reader may see them (the rest are
    counted as hidden)."""
    moment = _now(now)
    ctl = LovableSyncControl.objects.filter(pk=1).first()
    mode = ctl.mode if ctl is not None else SyncMode.OFF
    cut_over = bool(ctl and ctl.cut_over)
    observe = bool(ctl and ctl.observe)
    nightly = LovableSyncRun.objects.exclude(trigger=SyncTrigger.CHECK).order_by("-started_at")
    last_run = nightly.first()
    last_scheduled = nightly.filter(trigger=SyncTrigger.SCHEDULED).first()
    last_success = nightly.filter(status=SyncRunStatus.SUCCEEDED).first()
    last_failure = nightly.filter(status=SyncRunStatus.FAILED).first()
    snapshot_at = lovable_sync.comparable_snapshots().values_list("taken_at", flat=True).first()
    observation = (
        LovableObservationRun.objects.filter(status=ObservationRunStatus.SUCCEEDED)
        .order_by("-started_at")
        .first()
    )
    last_observation = LovableObservationRun.objects.order_by("-started_at").first()
    observation_at = (observation.taken_at or observation.started_at) if observation else None
    audit = _audit()
    cohort = LovableSyncState.objects.count()
    obs_counts = (observation.counts or {}) if observation else {}
    live = obs_counts.get("live") if isinstance(obs_counts.get("live"), int) else None
    operational = (
        obs_counts.get("operational") if isinstance(obs_counts.get("operational"), int) else None
    )
    states = list(
        LovableSyncState.objects.select_related("matter", "observed_run").filter(
            matter__deleted_at__isnull=True
        )
    )
    pending = len(_pending_fields(states))

    warnings: list[str] = []
    if cut_over:
        warnings.append("Lõplik üleminek on tehtud: sünkroonimine ei käivitu enam.")
    elif mode == SyncMode.OFF:
        warnings.append("Sünkroonimine on peatatud (režiim «Peatatud»).")
    if last_run is None:
        if mode != SyncMode.OFF:
            warnings.append("Sünkroonimine on sisse lülitatud, kuid pole kordagi käivitunud.")
    elif last_run.status == SyncRunStatus.FAILED:
        warnings.append(
            f"Viimane käivitus ({_local(last_run.started_at)}) ebaõnnestus: "
            f"{_failure_code(last_run.error) or 'teadmata'}."
        )
    elif last_run.status == SyncRunStatus.REVIEW_ONLY:
        warnings.append(
            f"Viimane käivitus peatati ülevaatuseks ({_failure_code(last_run.error)}); "
            "Juristidesse ei kirjutatud."
        )
    elif last_run.status == SyncRunStatus.RUNNING and moment - last_run.started_at > dt.timedelta(
        hours=1
    ):
        warnings.append("Käivitus algas üle tunni aja tagasi ega lõppenud.")
    if (
        mode != SyncMode.OFF
        and last_run is not None
        and (last_success is None or moment - last_success.started_at > lovable_sync.STALE_AFTER)
    ):
        warnings.append("Viimase 26 tunni jooksul pole ühtegi õnnestunud käivitust.")
    if _freshness(snapshot_at, moment) == STALE:
        warnings.append(
            f"Viimane võrreldav Lovable'i lugemine on vanem kui 26 tundi ({_local(snapshot_at)})."
        )
    elif snapshot_at is None and last_run is not None:
        warnings.append("Võrreldavat Lovable'i lugemist pole.")
    expected = lovable_sync.expected_cohorts()
    held_by_cohort = Counter(LovableSyncState.objects.values_list("cohort", flat=True))
    for key, wanted in expected.items():
        if held_by_cohort.get(key, 0) != wanted:
            warnings.append(
                f"{_cohort_name(key)}: sünkroonitavaid teemasid on "
                f"{held_by_cohort.get(key, 0)}, mitte {wanted}."
            )
    if not observe:
        warnings.append("Laiem vaatlus (kõik Lovable'i teemad) on välja lülitatud.")
    elif last_observation is None:
        warnings.append(NO_OBSERVATION)
    else:
        if last_observation.status == ObservationRunStatus.FAILED:
            warnings.append(
                f"Viimane laiem lugemine ebaõnnestus: {_failure_code(last_observation.error)}."
            )
        if _freshness(observation_at, moment) != FRESH:
            warnings.append("Õnnestunud laiem lugemine on vanem kui 26 tundi või puudub.")
    if not audit.available:
        warnings.append(audit.message)
    health = observation_health(now=moment)
    if (
        observe
        and last_observation is not None
        and last_observation.status == ObservationRunStatus.RUNNING
        and moment - last_observation.started_at > OBSERVATION_RUNNING_LIMIT
    ):
        warnings.append("Laiem lugemine algas üle kahe tunni tagasi ega lõppenud.")
    if health.retention_failure_code:
        warnings.append(
            "Vaatluse toorandmete kustutamine (säilitustähtaeg "
            f"{OBSERVATION_RETENTION_DAYS} päeva) ebaõnnestus: {health.retention_failure_code}."
        )
    elif any(p.startswith("no successful retention") for p in health.problems):
        warnings.append(
            "Vaatluse toorandmete kustutamine (säilitustähtaeg "
            f"{OBSERVATION_RETENTION_DAYS} päeva) pole viimase 26 tunni jooksul õnnestunud."
        )

    indicators = _status_indicators(
        user,
        moment,
        mode=mode,
        apply_authorised=mode == SyncMode.APPLY and not cut_over,
        last_scheduled=last_scheduled,
        last_success=last_success,
        last_run=last_run,
        snapshot_at=snapshot_at,
        observation=observation,
        audit=audit,
        cohort=cohort,
        expected=expected,
        live=live,
        operational=operational,
    )
    indicators.insert(
        8,
        _indicator(
            "retention_freshness",
            "Toorandmete viimane kustutamine",
            _hours_since(health.retention_last_success_at, moment),
            f"tundi tagasi (piir 26 tundi; säilitustähtaeg {OBSERVATION_RETENTION_DAYS} päeva)",
            _local(health.retention_last_success_at) or "—",
            "Õnnestunud kustutamist pole olnud.",
        ),
    )
    cohorts = _cohort_status(expected, held_by_cohort, last_run)
    per_cohort = set(cohorts) != {lovable_sync.PILOT_COHORT}
    if per_cohort:
        # Per cohort once the population is not the pilot alone (enrolled, or
        # rebuilt): the pilot alone reads as before.
        indicators += [_cohort_indicator(key, part, last_run) for key, part in cohorts.items()]
    return SyncStatus(
        mode=mode,
        mode_label=SyncMode(mode).label,
        apply_authorised=mode == SyncMode.APPLY and not cut_over,
        cut_over=cut_over,
        observe_enabled=observe,
        last_scheduled_at=last_scheduled.started_at if last_scheduled else None,
        last_scheduled_status=last_scheduled.status if last_scheduled else "",
        last_success_at=last_success.started_at if last_success else None,
        last_failure_at=last_failure.started_at if last_failure else None,
        last_failure_code=_failure_code(last_failure.error) if last_failure else "",
        snapshot_taken_at=snapshot_at,
        snapshot_freshness=_freshness(snapshot_at, moment),
        observation_taken_at=observation_at,
        observation_freshness=_freshness(observation_at, moment),
        audit=audit,
        cohort_size=cohort,
        live_topics=live,
        operational_topics=operational,
        pending_review=pending,
        warnings=warnings,
        indicators=indicators,
        observation_health_ok=health.ok,
        observation_health_detail=health.detail,
        retention_last_success_at=health.retention_last_success_at,
        retention_freshness=_freshness(health.retention_last_success_at, moment),
        cohorts=cohorts,
        per_cohort=per_cohort,
    )


def _cohort_indicator(key: str, part: dict[str, Any], last_run: LovableSyncRun | None) -> Indicator:
    """One cohort's size, with the newest run's review and activity counts."""
    unit = f"{part['expected']} teemast"
    if part.get("checked") is not None:
        unit += (
            f"; viimases võrdluses vajab ülevaatamist {part.get('review') or 0}"
            f" (neist impordil ootele jäetud {part.get('import_held') or 0}),"
            f" Lovable'is lõppenud, kuid Juristides avatud {part.get('stopped_active') or 0}"
        )
    return _indicator(
        "cohort_" + key.replace("-", "_"),
        f"{part['label']}: sünkroonitavad teemad",
        part["states"],
        unit,
        _local(last_run.started_at) if last_run is not None else "praegu",
    )


def _cohort_name(key: str) -> str:
    """A cohort in the pages' words: «Piloot», «Aktiivne register»."""
    return LOVABLE_COHORT_LABELS.get(key, key)


def _population_phrase(expected: dict[str, int], *, partitive: bool = False) -> str:
    """The synchronised population as a denominator. With only the pilot it reads
    as it always has («piloodi 100 teemast»); with enrolled cohorts it names the
    total («246 sünkroonitavast teemast»)."""
    total = sum(expected.values())
    if list(expected) == [lovable_sync.PILOT_COHORT]:
        return f"piloodi {total} teemat" if partitive else f"piloodi {total} teemast"
    return f"{total} sünkroonitavat teemat" if partitive else f"{total} sünkroonitavast teemast"


def _cohort_status(
    expected: dict[str, int], held: Counter[str], last_run: LovableSyncRun | None
) -> dict[str, dict[str, Any]]:
    """Per cohort: its size now and the newest reconciled run's counts."""
    by_cohort = (
        ((last_run.counts or {}).get("by_cohort") or {})
        if last_run is not None and lovable_sync.run_reconciled(last_run)
        else {}
    )
    out: dict[str, dict[str, Any]] = {}
    for key, wanted in expected.items():
        part = by_cohort.get(key) or {}
        out[key] = {
            "label": _cohort_name(key),
            "expected": wanted,
            "states": held.get(key, 0),
            "checked": part.get("checked"),
            "review": part.get("review"),
            "import_held": part.get("import_held"),
            "stopped_active": part.get("stopped_active"),
            "activity": dict(part.get("activity") or {}),
        }
    return out


def _source_size(
    key: str,
    label: str,
    noun: str,
    observed: int | None,
    observation: LovableObservationRun | None,
    last_run: LovableSyncRun | None,
    count_key: str,
) -> Indicator:
    """A source size from the newest wider reading, else from the pilot reading's
    topic index (every pilot night reads the index of all live topics)."""
    if observation is not None:
        return _indicator(
            key,
            label,
            observed,
            f"{noun} viimases laiemas lugemises",
            _local(observation.taken_at or observation.started_at),
            NO_OBSERVATION,
        )
    counted = (last_run.counts or {}).get(count_key) if last_run is not None else None
    return _indicator(
        key,
        label,
        counted if isinstance(counted, int) else None,
        f"{noun} piloodi lugemise teemaindeksis",
        _local(last_run.started_at) if last_run is not None else "—",
        NO_OBSERVATION,
    )


def _status_indicators(
    user: Any,
    moment: dt.datetime,
    *,
    mode: str,
    apply_authorised: bool,
    last_scheduled: LovableSyncRun | None,
    last_success: LovableSyncRun | None,
    last_run: LovableSyncRun | None,
    snapshot_at: dt.datetime | None,
    observation: LovableObservationRun | None,
    audit: AuditStatus,
    cohort: int,
    expected: dict[str, int],
    live: int | None,
    operational: int | None,
) -> list[Indicator]:
    start = moment - dt.timedelta(days=STATUS_PERIOD_DAYS)
    period = f"viimased {STATUS_PERIOD_DAYS} päeva ({_local(start)} – {_local(moment)})"
    no_obs = NO_OBSERVATION
    out = [
        _indicator(
            "latest_scheduled",
            "Viimane öine käivitus",
            _hours_since(last_scheduled.started_at if last_scheduled else None, moment),
            "tundi tagasi"
            + (
                f"; seis «{SyncRunStatus(last_scheduled.status).label}»"
                if last_scheduled is not None
                else ""
            ),
            _local(last_scheduled.started_at) if last_scheduled else "—",
            "Öist käivitust pole olnud.",
        ),
        _indicator(
            "latest_success",
            "Viimane õnnestunud käivitus",
            _hours_since(last_success.started_at if last_success else None, moment),
            "tundi tagasi (piir 26 tundi)",
            _local(last_success.started_at) if last_success else "—",
            "Õnnestunud käivitust pole olnud.",
        ),
        Indicator(
            "mode",
            f"Režiim: {SyncMode(mode).label}",
            1 if apply_authorised else 0,
            "Juristidesse kirjutamine lubatud",
            "praegu",
            display="jah" if apply_authorised else "ei",
        ),
        _indicator(
            "snapshot_freshness",
            "Piloodi viimane võrreldav lugemine",
            _hours_since(snapshot_at, moment),
            "tundi tagasi (värske kuni 26 tundi)",
            _local(snapshot_at) or "—",
            "Võrreldavat lugemist pole.",
        ),
        _indicator(
            "audit_freshness",
            "Lovable'i muutmisajaloo viimane sündmus",
            _hours_since(audit.last_event_at, moment) if audit.available else None,
            "tundi tagasi",
            _local(audit.last_event_at) or "—",
            AUDIT_NOT_AVAILABLE_SHORT if not audit.available else "Sündmusi pole salvestatud.",
        ),
        _indicator(
            "pilot_coverage",
            "Sünkroonitavad teemad",
            cohort,
            _population_phrase(expected),
            "praegu",
        ),
        _source_size(
            "operational_observed",
            "Operatiivsed teemad Lovable'is",
            "operatiivset teemat",
            operational,
            observation,
            last_run,
            "source_operational",
        ),
        _source_size(
            "live_indexed",
            "Kõik elavad teemad Lovable'is",
            "elavat teemat",
            live,
            observation,
            last_run,
            "source_live",
        ),
    ]

    # Pilot nights in the period (snapshot differences; never added to the wider ones).
    readings = [
        r
        for r in _pilot_readings(user, moment, full=True)
        if r.taken_at is not None and r.taken_at >= start
    ]
    pilot_changed = sum(r.changed_topics for r in readings)
    pilot_fields = sum(r.changed_fields for r in readings)
    hidden = sum(r.hidden for r in readings)
    out.append(
        _indicator(
            "pilot_topics_changed",
            "Muutunud piloodi teemad (öised võrdlused)",
            pilot_changed if _reads(user) else None,
            f"teemamuutust {len(readings)} öö võrdluses"
            f" ({_population_phrase(expected, partitive=True)})",
            period,
            "Lugejal pole ärisisu lugemise õigust.",
        )
    )
    obs_changes = LovableObservedChange.objects.filter(
        run__status=ObservationRunStatus.SUCCEEDED, run__started_at__gte=start
    )
    out.append(
        _indicator(
            "observed_topics_changed",
            "Muutunud teemad (laiemad lugemised)",
            obs_changes.count() if observation is not None else None,
            "teemamuutust laiemates lugemistes (kõik elavad teemad)",
            period,
            no_obs,
        )
    )
    out.append(
        _indicator(
            "source_events",
            "Lovable'i muutmisajaloo sündmused",
            LovableSourceEvent.objects.filter(occurred_at__gte=start).count()
            if audit.available
            else None,
            "sündmust Lovable'i enda muutmisajaloos",
            period,
            audit.message,
        )
    )
    out.append(
        _indicator(
            "changed_fields",
            "Muutunud väljad lugemiste vahel",
            pilot_fields if _reads(user) else None,
            "välja muutust piloodi öistes võrdlustes"
            + (f" (lisaks {hidden} peidetud teema muudatust, välju pole loetud)" if hidden else ""),
            period,
            "Lugejal pole ärisisu lugemise õigust.",
        )
    )

    # Reconciliation decisions of the newest night (a count per outcome).
    newest = last_run if last_run is not None and lovable_sync.run_reconciled(last_run) else None
    tally: Counter[str] = Counter()
    if newest is not None:
        tally = Counter(
            dict(
                LovableSyncDecision.objects.filter(run=newest)
                .values_list("outcome")
                .annotate(n=Count("pk"))
                .values_list("outcome", "n")
            )
        )
    night_window = _local(newest.started_at) if newest else "—"
    for key, outcome, label in (
        ("eligible", SyncOutcome.ELIGIBLE, "Ohutud uuendused (rakendamata)"),
        ("review_required", SyncOutcome.REVIEW_REQUIRED, "Vajab ülevaatamist"),
        ("conflicts", SyncOutcome.CONFLICT, "Vastuolud"),
        ("already_converged", SyncOutcome.ALREADY_CONVERGED, "Juba kooskõlas"),
    ):
        out.append(
            _indicator(
                key,
                label,
                tally.get(outcome, 0) if newest is not None else None,
                PENDING_TALLY_UNIT,
                night_window,
                "Võrdlust pole veel salvestatud.",
            )
        )
    out.append(
        _indicator(
            "applied",
            "Rakendatud kirjutused",
            LovableSyncDecision.objects.filter(outcome=SyncOutcome.APPLIED).count(),
            "Juristidesse kirjutatud välja kokku"
            + ("" if apply_authorised else " (APPLY-režiim pole lubatud)"),
            "kogu aeg",
        )
    )
    run_counts = (last_run.counts or {}) if last_run is not None else {}
    out.append(
        _indicator(
            "unmatched_operational",
            "Operatiivsed teemad, mida Juristides pole",
            run_counts.get("operational_not_in_juristid") if run_counts else None,
            "operatiivset teemat väljaspool pilooti, millel pole sama numbriga Juristide teemat",
            night_window,
            "Võrdlust pole veel salvestatud.",
        )
    )
    out.append(
        _indicator(
            "new_source_topics",
            "Uued teemad Lovable'is",
            run_counts.get("new_since_previous") if run_counts else None,
            "uut teemat eelmise piloodi lugemisega võrreldes (kõik elavad teemad)",
            night_window,
            "Võrdlust pole veel salvestatud.",
        )
    )
    kinds = Counter(
        dict(obs_changes.values_list("kind").annotate(n=Count("pk")).values_list("kind", "n"))
    )
    for key, kind, label in (
        ("missing", ObservedChangeKind.MISSING, "Lugemisest kadunud teemad"),
        ("restored", ObservedChangeKind.BACK, "Lugemisse naasnud teemad"),
        ("renumbered", ObservedChangeKind.RENUMBERED, "Numbri muutnud teemad"),
    ):
        out.append(
            _indicator(
                key,
                label,
                kinds.get(kind, 0) if observation is not None else None,
                "teemat laiemates lugemistes (kõik elavad teemad)",
                period,
                no_obs,
            )
        )
    return out


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
    full: bool = False,
) -> Page:
    """Readings newest first (`Reading` items). ``population`` PILOT gives the
    pilot's scheduled nights (wrapping `lovable_review.nightly_history`);
    OPERATIONAL / ALL_LIVE give wider observation readings filtered to that
    population. Filters narrow the topics inside each reading; a reading with
    no matching topic is still listed (its counts say why).

    `NIGHTS_PAGE` readings per page. In a list a value longer than
    `SHORT_VALUE` is cut (``FieldDiff.truncated``); `matter_changes` has it
    uncut. Under `AGGREGATE` a wider reading's unlinked topics are counted in
    ``Reading.unlinked`` (and in its totals), never listed."""
    if not _reads(user):
        return _empty(NIGHTS_PAGE)
    moment = _now(now)
    start, end = _window(since, until, moment)
    reference = _norm_ref(reference)
    if population == ObservationScope.PILOT:
        wanted = _outcome_key(outcome)
        readings = []
        for reading in _pilot_readings(user, moment, full=full):
            taken = reading.taken_at
            if taken is None or not (start <= taken < end):
                continue
            reading.topics = [
                t
                for t in reading.topics
                if _topic_matches(
                    t, outcome=wanted, field_name=field_name, reference=reference, text=text
                )
            ]
            readings.append(reading)
        return _page(readings, page, size=NIGHTS_PAGE)

    runs_qs = LovableObservationRun.objects.filter(
        started_at__gte=start, started_at__lt=end
    ).order_by("-started_at")
    total = runs_qs.count()
    pages = _pages(total, NIGHTS_PAGE)
    number = min(max(1, page), pages)
    runs = list(
        runs_qs.select_related("baseline")[(number - 1) * NIGHTS_PAGE : number * NIGHTS_PAGE]
    )
    base = Q(run__in=runs) & _population_q(population)
    every = list(
        LovableObservedChange.objects.filter(base)
        .select_related("matter")
        .order_by("run_id", "reference", "source_id")
    )
    visible = _visible_set(user, {c.matter_id for c in every if c.matter_id is not None})
    by_run: dict[Any, list[LovableObservedChange]] = {}
    unlinked: dict[Any, list[LovableObservedChange]] = {}
    hidden: Counter[Any] = Counter()
    aggregate = _aggregate_only()
    for change in every:
        if aggregate and change.mapping != MappingStatus.PILOT_LINKED:
            # Counted only; the field filter (a fixed vocabulary) narrows the
            # count, a reference or text filter never does.
            if not field_name or field_name in _change_field_names(
                change.field_names, change.fields
            ):
                unlinked.setdefault(change.run_id, []).append(change)
            continue
        if change.mapping == MappingStatus.PILOT_LINKED and (
            change.matter_id is None
            or change.matter_id not in visible
            or getattr(change.matter, "deleted_at", None) is not None
        ):
            if change.matter is not None and change.matter.deleted_at is None:
                hidden[change.run_id] += 1
            continue
        by_run.setdefault(change.run_id, []).append(change)
    readings = []
    for run in runs:
        reading = _observation_reading(
            run,
            by_run.get(run.pk, []),
            hidden[run.pk],
            visible,
            population,
            full=full,
            unlinked=unlinked.get(run.pk, []),
        )
        reading.topics = [
            t
            for t in reading.topics
            if _topic_matches(t, outcome="", field_name=field_name, reference=reference, text=text)
        ]
        reading.topics.sort(key=lambda t: _ref_order(t.reference))
        readings.append(reading)
    return Page(items=readings, page=number, pages=pages, total=total, page_size=NIGHTS_PAGE)


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
    full: bool = False,
) -> Page:
    """`TopicDiff` items from wider readings, newest first, with totals by kind
    and mapping in the page's ``items`` order.

    Totals are in ``Page.totals`` (``{"kind": {...}, "mapping": {...}}``) over
    every page; ``Page.hidden`` counts pilot topics whose Teema the reader may
    not open. Under `AGGREGATE` an unlinked topic is never an item: it is
    counted in ``Page.unlinked`` (narrowed by kind, mapping and field, never by
    reference or text)."""
    if not _reads(user):
        return _empty()
    moment = _now(now)
    start, end = _window(since, until, moment)
    reference = _norm_ref(reference)
    window = LovableObservedChange.objects.filter(
        run__status=ObservationRunStatus.SUCCEEDED,
        run__started_at__gte=start,
        run__started_at__lt=end,
    )
    hidden = window.filter(_population_q(population)).exclude(_seen_q(user))
    hidden = hidden.filter(matter__deleted_at__isnull=True)
    qs = window.filter(
        _changes_q(
            user,
            population=population,
            kind=kind,
            field_name=field_name,
            mapping=mapping,
            reference=reference,
            text=text,
        )
    )
    unlinked = UnlinkedSummary(policy=FIELD_NAMES)
    if _aggregate_only():
        qs = qs.filter(mapping=MappingStatus.PILOT_LINKED)
        rest = window.filter(_population_q(population)).exclude(mapping=MappingStatus.PILOT_LINKED)
        if kind:
            rest = rest.filter(kind=kind)
        if mapping:
            rest = rest.filter(mapping=mapping)
        if field_name:
            rest = rest.filter(
                Q(field_names__contains=[field_name]) | Q(fields__contains=[{"field": field_name}])
            )
        unlinked = _unlinked_summary(
            list(rest.values_list("kind", "mapping", "field_names", "fields"))
        )
    total = qs.count()
    pages = _pages(total, PAGE_SIZE)
    number = min(max(1, page), pages)
    rows = list(
        qs.select_related("matter").order_by("-run__started_at", "reference", "source_id")[
            (number - 1) * PAGE_SIZE : number * PAGE_SIZE
        ]
    )
    visible = _visible_set(user, {c.matter_id for c in rows if c.matter_id is not None})
    totals = {
        "kind": dict(qs.values_list("kind").annotate(n=Count("pk")).values_list("kind", "n")),
        "mapping": dict(
            qs.values_list("mapping").annotate(n=Count("pk")).values_list("mapping", "n")
        ),
    }
    return Page(
        items=[_observed_topic(c, visible, full=full) for c in rows],
        page=number,
        pages=pages,
        total=total,
        page_size=PAGE_SIZE,
        hidden=hidden.count(),
        totals={k: dict(sorted(v.items())) for k, v in totals.items()},
        unlinked=unlinked,
    )


def _event_views(
    user: Any, events: list[LovableSourceEvent], sight: _Sight, *, full: bool
) -> list[SourceEventView]:
    """Events with their topic's mapping. Values only for a pilot Teema the
    reader may open; otherwise field names (and withheld flags) only."""
    ids = {e.topic_id for e in events}
    others: dict[Any, LovableObservedChange] = {}
    unknown = [i for i in ids if str(i) not in sight.states]
    if unknown:
        for change in (
            LovableObservedChange.objects.filter(source_id__in=unknown)
            .order_by("source_id", "-created_at")
            .only("source_id", "reference", "mapping", "matter_id")
        ):
            others.setdefault(change.source_id, change)
    candidates = _visible_set(
        user, {c.matter_id for c in others.values() if c.matter_id is not None}
    )
    views = []
    for event in events:
        state = sight.states.get(str(event.topic_id))
        if state is not None:
            matter = sight.visible.get(state.matter_id)
            mapping, reference = str(MappingStatus.PILOT_LINKED), state.reference
            title = matter.title if matter is not None else ""
            matter_id = str(state.matter_id) if matter is not None else None
            open_values = matter is not None
        else:
            seen = others.get(event.topic_id)
            mapping = seen.mapping if seen is not None else str(MappingStatus.UNMATCHED)
            if mapping == MappingStatus.PILOT_LINKED:
                mapping = str(MappingStatus.UNMATCHED)  # not a pilot state any more
            reference = seen.reference if seen is not None else ""
            title, open_values = "", False
            matter_id = (
                str(seen.matter_id)
                if not _aggregate_only()
                and seen is not None
                and mapping == MappingStatus.REFERENCE_CANDIDATE
                and seen.matter_id in candidates
                else None
            )
        fields = []
        for row in event.fields.all():
            canonical = LOVABLE_SOURCE_COLUMNS.get(row.field, row.field)
            label = _label(canonical) if row.field in LOVABLE_SOURCE_COLUMNS else row.field
            if row.withheld or not open_values:
                fields.append(FieldDiff(row.field, label, "", "", withheld=True))
                continue
            before, after = row.old_value or "—", row.new_value or "—"
            cut_b = cut_a = False
            if not full:
                (before, cut_b), (after, cut_a) = _cut(before), _cut(after)
            fields.append(
                FieldDiff(
                    row.field,
                    label,
                    before,
                    after,
                    truncated=cut_b or cut_a,
                    source_truncated=bool(row.truncated),
                )
            )
        views.append(
            SourceEventView(
                source_event_id=event.source_event_id,
                occurred_at=event.occurred_at,
                reference=reference,
                action=event.action,
                action_label=LOVABLE_SOURCE_ACTIONS.get(event.action.lower(), event.action),
                actor_kind=event.actor_kind,
                version=event.version,
                fields=fields,
                mapping=mapping,
                title=title,
                matter_id=matter_id,
                matter_url=_matter_url(matter_id),
            )
        )
    return views


def _hidden_sources(sight: _Sight) -> list[str]:
    """Pilot source ids whose Teema the reader may not open, or that is deleted."""
    return [sid for sid, s in sight.states.items() if s.matter_id not in sight.visible]


def source_events(
    user: Any,
    *,
    since: dt.date | None = None,
    until: dt.date | None = None,
    reference: str = "",
    field_name: str = "",
    page: int = 1,
    now: dt.datetime | None = None,
) -> tuple[AuditStatus, Page]:
    """The audit ledger's events (`SourceEventView`). With no authorised access
    the page is empty and the status says why — never an invented event.

    ``field_name`` is a Lovable column (``jargmiseks``) or a canonical field
    (``next_action_text``). Events of a pilot Teema the reader may not open are
    counted in ``Page.hidden``, never listed. Under `AGGREGATE` an event of a
    topic outside the pilot is never listed either: ``Page.unlinked`` counts
    them by action and field (the field filter narrows that count, the
    reference filter never does)."""
    audit = _audit()
    if not audit.available or not _reads(user):
        return audit, _empty()
    moment = _now(now)
    start, end = _window(since, until, moment)
    sight = _sight(user)
    aggregate = _aggregate_only()
    window = LovableSourceEvent.objects.filter(occurred_at__gte=start, occurred_at__lt=end)
    columns: list[str] = []
    if field_name:
        columns = [c for c, f in LOVABLE_SOURCE_COLUMNS.items() if f == field_name] or [field_name]
        window = window.filter(fields__field__in=columns).distinct()
    qs = window
    unlinked = UnlinkedSummary(policy=FIELD_NAMES)
    if aggregate:
        pilot_ids = [s.source_id for s in sight.states.values()]
        qs = qs.filter(topic_id__in=pilot_ids)
        unlinked = _unlinked_events(window.exclude(topic_id__in=pilot_ids))
    reference = _norm_ref(reference)
    if reference:
        ids = {str(s.source_id) for s in sight.states.values() if s.reference == reference}
        ids |= {
            str(i)
            for i in LovableObservedChange.objects.filter(
                Q(reference=reference) | Q(previous_reference=reference)
            ).values_list("source_id", flat=True)
        }
        qs = qs.filter(topic_id__in=ids)
    hidden_ids = _hidden_sources(sight)
    hidden = qs.filter(topic_id__in=hidden_ids).count() if hidden_ids else 0
    shown = qs.exclude(topic_id__in=hidden_ids) if hidden_ids else qs
    total = shown.count()
    pages = _pages(total, PAGE_SIZE)
    number = min(max(1, page), pages)
    events = list(
        shown.order_by("-occurred_at", "-source_event_id").prefetch_related("fields")[
            (number - 1) * PAGE_SIZE : number * PAGE_SIZE
        ]
    )
    return audit, Page(
        items=_event_views(user, events, sight, full=False),
        page=number,
        pages=pages,
        total=total,
        page_size=PAGE_SIZE,
        hidden=hidden,
        unlinked=unlinked,
    )


def _unlinked_events(events: Any) -> UnlinkedSummary:
    """Source events of unlinked topics, counted by action and by field."""
    ids = events.values("pk")
    actions = dict(
        events.order_by()
        .values_list("action")
        .annotate(n=Count("pk", distinct=True))
        .values_list("action", "n")
    )
    names = dict(
        LovableSourceEventField.objects.filter(event_id__in=ids)
        .order_by()
        .values_list("field")
        .annotate(n=Count("pk"))
        .values_list("field", "n")
    )
    total = events.order_by().values("pk").distinct().count()
    return UnlinkedSummary(
        policy=AGGREGATE,
        total=total,
        kind=dict(sorted(actions.items())),
        mapping={},
        field=dict(sorted(names.items())),
        note=unlinked_note(total, events=True),
    )


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
    applied one and is not converged (`QueueItem`), oldest first.

    One pending field is one item however many nights repeated it
    (``nights_pending`` says how many). ``Page.hidden`` counts pending fields
    of Teemad the reader may not open."""
    if not _reads(user):
        return _empty()
    moment = _now(now)
    items, hidden = _visible_queue(user, _sight(user), moment)
    reference = _norm_ref(reference)
    needle = text.casefold()
    selected = [
        i
        for i in items
        if (not field_name or i.field == field_name)
        and (not outcome or i.outcome == outcome)
        and (not reference or i.reference == reference)
        and (
            not needle
            or any(
                needle in v.casefold()
                for v in (i.reference, i.title, i.lovable_now, i.juristid_now, i.last_applied)
            )
        )
    ]
    totals = {
        "field": dict(sorted(Counter(i.field for i in items).items())),
        "outcome": dict(sorted(Counter(i.outcome for i in items).items())),
    }
    return _page(selected, page, hidden=hidden, totals=totals)


def _resolve_state(user: Any, reference_or_id: str) -> LovableSyncState | None:
    from app.matters.models import Matter

    raw = (reference_or_id or "").strip()
    try:
        matter_pk: Any = uuid.UUID(raw)
    except ValueError:
        matter_pk = None
    states = LovableSyncState.objects.select_related("matter", "matter__owner", "observed_run")
    if matter_pk is not None:
        state = states.filter(matter_id=matter_pk).first()
    else:
        state = states.filter(reference=_norm_ref(raw)).first()
    if state is None or getattr(state.matter, "deleted_at", None) is not None:
        return None
    if not Matter.objects.visible_to(user).filter(pk=state.matter_id).exists():
        return None
    return state


def matter_changes(
    user: Any, reference_or_id: str, *, now: dt.datetime | None = None
) -> MatterChanges | None:
    """``None`` for an unknown Teema, one the reader may not open, or one not in
    the pilot — the caller says «not found» for all three alike.

    Values here are never cut for display: this is the detail the lists point
    to. A source event's value is still incomplete where Juristid's copy was
    cut at ingestion — ``FieldDiff.source_truncated`` says so."""
    if not _reads(user):
        return None
    moment = _now(now)
    state = _resolve_state(user, reference_or_id)
    if state is None:
        return None
    matter = state.matter
    b = current_values_bulk([matter])[matter.pk]
    pending = _pending_fields([state])
    pending_names = {p.field for p in pending}
    fields = []
    for name in FIELDS:
        applied = (state.applied.get(name) or {}).get("source")
        observed = (state.observed or {}).get(name)
        fields.append(
            {
                "field": name,
                "label": _label(name),
                "automatic": name in lovable_sync.AUTO_FIELDS,
                "last_applied": lovable_review.shown(name, applied),
                "juristid_now": _juristid_shown(name, matter, b),
                "lovable_observed": lovable_review.shown(name, observed)
                if name in (state.observed or {})
                else "—",
                "pending": name in pending_names,
            }
        )
    night_topics = [
        t
        for r in _pilot_readings(user, moment, full=True)
        for t in r.topics
        if t.matter_id == str(matter.pk)
    ]
    decisions = []
    for d in (
        LovableSyncDecision.objects.filter(state=state)
        .select_related("run")
        .order_by("-run__started_at", "field")[:MATTER_DECISIONS]
    ):
        reason = d.reason or ""
        decisions.append(
            {
                "run_at": d.run.started_at,
                "run_key": d.run.run_key,
                "trigger": d.run.trigger,
                "trigger_label": SyncTrigger(d.run.trigger).label
                if d.run.trigger in SyncTrigger.values
                else d.run.trigger,
                "field": d.field,
                "label": _label(d.field),
                "outcome": d.outcome,
                "outcome_label": SyncOutcome(d.outcome).label
                if d.outcome in SyncOutcome.values
                else d.outcome,
                "reason": reason,
                "reason_label": LOVABLE_REASONS.get(reason, "")
                or (
                    "Lahendatud käsitsi"
                    if reason.startswith(lovable_sync.RESOLVED_PREFIX)
                    else reason
                ),
                "baseline": _shown(d.field, d.baseline),
                "current": _shown(d.field, d.current),
                "source": _shown(d.field, d.source),
            }
        )
    audit = _audit()
    events: list[SourceEventView] = []
    if audit.available:
        sight = _Sight(
            states={str(state.source_id): state},
            by_matter={matter.pk: state},
            visible={matter.pk: matter},
            live={matter.pk},
        )
        rows = list(
            LovableSourceEvent.objects.filter(topic_id=state.source_id)
            .order_by("-occurred_at", "-source_event_id")
            .prefetch_related("fields")[:PAGE_SIZE]
        )
        events = _event_views(user, rows, sight, full=True)
    today = _local_day(moment)
    queue = [_queue_item(p, matter, b, today, full=True) for p in pending]
    queue.sort(key=lambda i: (i.first_observed_at is None, i.first_observed_at, i.field))
    return MatterChanges(
        matter_id=str(matter.pk),
        reference=state.reference,
        title=matter.title,
        matter_url=_matter_url(matter.pk),
        source_id=str(state.source_id),
        fields=fields,
        nights=night_topics,
        decisions=decisions,
        source_events=events,
        pending=queue,
        audit=audit,
    )


# ---------------------------------------------------------------------------
# Readiness (docs/adr/0201 §7)
# ---------------------------------------------------------------------------

#: What the existing rules do with each field (`lovable_sync.FIELD_POLICY`), and
#: why a held one is held. Static: a change here is a change to docs/adr/0192 §5
#: as amended by docs/adr/0213, never made by this module; a test keeps it in
#: step with FIELD_POLICY.
FIELD_RULES: dict[str, dict[str, Any]] = {
    "title": {
        "mapping": "EXACT",
        "service": "set_matter_title(provenance=…)",
        "risks": ["Ainult kui import säilitas allika pealkirja; üle vaadatud pealkiri jääb alles."],
        "blocking": "",
    },
    "owner": {
        "mapping": "EXACT",
        "service": "assign_matter(provenance=…)",
        "risks": [
            "Nimi peab vastama täpselt ühele aktiivsele kasutajale, kellele saab tööd määrata.",
            "Vastutaja vahetus viib kaasa avatud tegevuse.",
        ],
        "blocking": "",
    },
    "next_action_date": {
        "mapping": "EXACT",
        "service": "set_next_action (sama sõnastus, liik ja vastutaja; carry_*)",
        "risks": [
            "Ainult kui sõnastus ei muutunud ja tegevus on piloodi täpse kuupäevaga tegevus.",
            "Möödunud või kustutatud kuupäev jääb inimesele.",
        ],
        "blocking": "",
    },
    "onenote_link": {
        "mapping": "EXACT",
        "service": "uus MatterSourceReference (vana jääb alles)",
        "risks": ["Ainult https-link; eemaldatud link jääb inimesele."],
        "blocking": "",
    },
    "next_action_text": {
        "mapping": "NONE",
        "service": "",
        "risks": ["Uus sõnastus on uus tegevus; vana liiki ei kanta üle."],
        "blocking": "Tegevuse liigi (tee/oota/jälgi) otsustab inimene.",
    },
    "stage": {
        "mapping": "CONDITIONAL",
        "service": "change_stage(origin=IMPORTED)",
        "risks": [
            "Ainult täpse sildivastavuse (lovable_reading.STAGE_MAPPING) kaudu avatud teemal, "
            "mis on veel viimati rakendatud etapis.",
            "Tööd lõpetav etapp (TERMINAL_STAGE_LABELS) jääb alati inimesele.",
        ],
        "blocking": "",
    },
    "opinion_deadline": {
        "mapping": "CONDITIONAL",
        "service": "request_response_deadline (uus küsimine)",
        "risks": [
            "Ainult kui praegust küsimist pole, midagi pole saadetud, see pole Koja algatus "
            "ja päev pole möödas; olemasolevat tähtaega ei muudeta.",
        ],
        "blocking": "",
    },
    "sent": {
        "mapping": "NONE",
        "service": "",
        "risks": ["Automaatne kandmine looks Juristidesse ekslikult välja saadetud arvamuse."],
        "blocking": "Lovable'i «Välja» märge pole saadetud arvamus (Submission).",
    },
    "sent_date": {
        "mapping": "NONE",
        "service": "",
        "risks": ["Automaatne kandmine looks Juristidesse ekslikult välja saadetud arvamuse."],
        "blocking": "Lovable'i «Välja» kuupäev pole saadetud arvamus (Submission).",
    },
    "addressee": {
        "mapping": "APPROXIMATE",
        "service": "",
        "risks": ["Vaba tekst, mitte organisatsioon."],
        "blocking": "Adressaadi vastendusreeglit pole kinnitatud.",
    },
    "koda_initiative": {
        "mapping": "APPROXIMATE",
        "service": "",
        "risks": ["Märge muudaks teema päritolu."],
        "blocking": "Koja ettepaneku vastendusreeglit pole kinnitatud.",
    },
    "legal_act": {
        "mapping": "APPROXIMATE",
        "service": "",
        "risks": ["Vaba tekst, mitte kinnitatud õigusakt."],
        "blocking": "Õigusakti vastendusreeglit pole kinnitatud.",
    },
    "received": {
        "mapping": "APPROXIMATE",
        "service": "",
        "risks": ["Saabumise kuupäev on teema ajaloo osa."],
        "blocking": "Saabumise kuupäeva vastendusreeglit pole kinnitatud.",
    },
    "commencements": {
        "mapping": "CONDITIONAL",
        "service": "add_effective_date / add_important_date (ainult lisandunud)",
        "risks": [
            "Ainult üheselt loetava liigiga lisandunud jõustumine; eeldatav jääb hinnanguks "
            "(docs/adr/0120). Muudetud või eemaldatud jõustumine jääb inimesele.",
        ],
        "blocking": "",
    },
    "operational_scope": {
        "mapping": "NONE",
        "service": "",
        "risks": ["Ulatus on Lovable'i tööjärjekorra mõiste."],
        "blocking": "Operatiivse ulatuse vastet Juristides pole.",
    },
}

OWNER_DECISIONS = [
    "Kas ja kui palju võib näidata Juristidega sidumata Lovable'i teemade muudatusi "
    "(praegu ainult koondarvud; valikuna number, muutuse liik ja väljade nimed).",
    "Kas laiema vaatluse toorandmeid võib hoida kauem kui 30 päeva.",
    "Lovable'i muutmisajaloo lugemisõigus: `lovable_sync access-sql` päringute heakskiit ja "
    "tulemuse kirja panek.",
    "Kinnitatud vastendus Lovable'i hetkeseisu ja Juristide etapisõnastiku vahel.",
    "Kas kogu registri (mitte ainult operatiivsete teemade) välju võib lugeda.",
    "Sama numbriga teemade (seos kinnitamata) seostamise kord: kes kinnitab ja kuidas.",
    "APPLY-režiimi lubamine pärast võrdluse ülevaatamist (docs/adr/0192 §10).",
]

_CATEGORY_OF_OUTCOME: dict[str, str] = {
    SyncOutcome.ELIGIBLE: "SAFE_UPDATE_CANDIDATE",
    SyncOutcome.LOCAL_TEST_CHANGE: "LOCAL_KEPT",
    SyncOutcome.CONFLICT: "CONFLICT",
    SyncOutcome.ALREADY_CONVERGED: "CONVERGED",
    SyncOutcome.UNCHANGED: "UNCHANGED",
}


def _is_operational(row: dict[str, Any]) -> bool:
    """An index entry's operational flag: ``operational`` in a wider reading's
    index, ``operatiivne_ulatus`` in the pilot snapshot's."""
    return bool(row.get("operational", row.get("operatiivne_ulatus")))


def simulation_category(decision: lovable_sync.FieldDecision) -> str:
    if decision.outcome == SyncOutcome.REVIEW_REQUIRED:
        if decision.reason == lovable_sync.NO_BASELINE:
            return "MISSING_BASELINE"
        if decision.reason == lovable_sync.HELD:
            return "UNSUPPORTED_FIELD"
        return "MANUAL_REVIEW"
    return _CATEGORY_OF_OUTCOME.get(decision.outcome, "MANUAL_REVIEW")


def _index_rows() -> tuple[str, list[dict[str, Any]], dict[str, Any] | None]:
    """The newest index of every live topic, and the newest comparable pilot
    snapshot document (verified) — both read from Juristid's own tables."""
    pilot_doc: dict[str, Any] | None = None
    newest = lovable_sync.comparable_snapshots().first()
    if newest is not None:
        try:
            verify_snapshot(newest.document)
            pilot_doc = newest.document
        except SourceError:
            pilot_doc = None
    observed = (
        LovableObservationSnapshot.objects.filter(run__status=ObservationRunStatus.SUCCEEDED)
        .order_by("-taken_at")
        .first()
    )
    if observed is not None and isinstance((observed.document or {}).get("index"), list):
        return "OBSERVATION", list(observed.document["index"]), pilot_doc
    if pilot_doc is not None:
        return "PILOT", list(pilot_doc.get("index") or []), pilot_doc
    return "", [], pilot_doc


def readiness(user: Any, *, now: dt.datetime | None = None) -> Readiness:
    """The readiness assessment (docs/adr/0201 §7). Writes nothing.

    Counts are given to every caller (they name nothing); the per-Teema plan
    rows only for Teemad the reader may open. The simulation runs the existing
    `lovable_sync.plan_matter` — pure: it reads, and returns a plan — over the
    newest comparable pilot snapshot, with Juristid's current values read in
    bulk; no apply, record, observe or run path is ever called."""
    from app.matters.models import Matter

    moment = _now(now)
    today = _local_day(moment)
    sight = _sight(user)
    ctl = LovableSyncControl.objects.filter(pk=1).first()
    mode = ctl.mode if ctl is not None else SyncMode.OFF
    audit = _audit()
    states = sorted(sight.states.values(), key=lambda s: s.reference)
    index_from, index, pilot_doc = _index_rows()
    cohort_ids = {str(s.source_id) for s in states}

    # --- A. the source -----------------------------------------------------
    last_run = (
        LovableSyncRun.objects.exclude(trigger=SyncTrigger.CHECK).order_by("-started_at").first()
    )
    snapshot = lovable_sync.comparable_snapshots().only("taken_at", "sha256", "run").first()
    obs = LovableObservationRun.objects.order_by("-started_at").first()
    obs_ok = (
        LovableObservationRun.objects.filter(status=ObservationRunStatus.SUCCEEDED)
        .order_by("-started_at")
        .first()
    )
    obs_counts = (obs_ok.counts or {}) if obs_ok else {}
    cohort = (pilot_doc or {}).get("cohort") or {}
    child_rows = sum(
        len(b.get(k) or [])
        for b in cohort.values()
        for k in ("commencements", "responsible_changes", "stage_links")
    )
    live_n, detailed_n = obs_counts.get("live"), obs_counts.get("detailed")
    source = {
        "pilot": {
            "last_run_at": last_run.started_at if last_run else None,
            "last_run_status": last_run.status if last_run else "",
            "last_run_failure_code": _failure_code(last_run.error)
            if last_run and last_run.status == SyncRunStatus.FAILED
            else "",
            "snapshot_taken_at": snapshot.taken_at if snapshot else None,
            "snapshot_freshness": _freshness(snapshot.taken_at if snapshot else None, moment),
            "snapshot_verified": pilot_doc is not None,
            "snapshot_sha256_present": bool(snapshot and snapshot.sha256),
            "topics_in_snapshot": len(cohort),
            "topics_with_child_records": sum(
                1
                for b in cohort.values()
                if any(b.get(k) for k in ("commencements", "responsible_changes", "stage_links"))
            ),
            "child_records": child_rows,
        },
        "observation": {
            "enabled": bool(ctl and ctl.observe),
            "last_run_at": obs.started_at if obs else None,
            "last_run_status": obs.status if obs else "",
            "last_run_failure_code": _failure_code(obs.error)
            if obs and obs.status == ObservationRunStatus.FAILED
            else "",
            "last_success_at": (obs_ok.taken_at or obs_ok.started_at) if obs_ok else None,
            "freshness": _freshness(
                (obs_ok.taken_at or obs_ok.started_at) if obs_ok else None, moment
            ),
            "sha256_present": bool(obs_ok and obs_ok.sha256),
            "has_baseline": bool(obs_ok and obs_ok.baseline_id),
            "live": live_n if isinstance(live_n, int) else None,
            "operational": obs_counts.get("operational")
            if isinstance(obs_counts.get("operational"), int)
            else None,
            "detailed": detailed_n if isinstance(detailed_n, int) else None,
            "index_only": live_n - detailed_n
            if isinstance(live_n, int) and isinstance(detailed_n, int)
            else None,
        },
        "audit": {"available": audit.available, "access": audit.access, "message": audit.message},
    }

    # --- B. identity ---------------------------------------------------------
    known_refs = set(
        Matter.objects.exclude(reference_year__isnull=True).values_list(
            "reference_year", "reference_number"
        )
    )
    operational_rows = [r for r in index if _is_operational(r)]
    linked = unmatched = candidates = 0
    for row in operational_rows:
        if str(row.get("id")) in cohort_ids:
            linked += 1
            continue
        match = re.fullmatch(r"(\d{4})_(\d+)", str(row.get("nr") or ""))
        if match and (int(match.group(1)), int(match.group(2))) in known_refs:
            candidates += 1
        else:
            unmatched += 1
    numbers = Counter(str(r.get("nr") or "") for r in index if r.get("nr"))
    juristid_dupes = (
        Matter.all_objects.exclude(reference_year__isnull=True)
        .values("reference_year", "reference_number")
        .annotate(n=Count("pk"))
        .filter(n__gt=1)
        .count()
    )
    newest_obs_kinds: Counter[str] = Counter()
    if obs_ok is not None:
        newest_obs_kinds = Counter(
            dict(
                LovableObservedChange.objects.filter(run=obs_ok)
                .values_list("kind")
                .annotate(n=Count("pk"))
                .values_list("kind", "n")
            )
        )
    run_counts = (last_run.counts or {}) if last_run else {}
    identity = {
        "index_from": index_from,
        "index_topics": len(index),
        "pilot_linked": len(states),
        "pilot_deleted_in_juristid": sum(1 for s in states if s.matter_id not in sight.live),
        "pilot_closed_in_juristid": sum(
            1 for s in states if s.matter_id in sight.live and not s.matter.is_open
        ),
        "operational": len(operational_rows),
        "operational_pilot_linked": linked,
        "operational_reference_candidates": candidates,
        "operational_unmatched": unmatched,
        "duplicate_source_numbers": sum(1 for n in numbers.values() if n > 1),
        "duplicate_juristid_references": juristid_dupes,
        "renumbered": newest_obs_kinds.get(ObservedChangeKind.RENUMBERED, 0),
        "missing": newest_obs_kinds.get(ObservedChangeKind.MISSING, 0),
        "back": newest_obs_kinds.get(ObservedChangeKind.BACK, 0),
        "new": newest_obs_kinds.get(ObservedChangeKind.NEW, 0),
        "pilot_missing_last_night": run_counts.get("missing"),
        "pilot_identifier_changed_last_night": run_counts.get("identifier_changed"),
        "manual_confirmation": candidates,
    }

    # --- E. the simulation (pure planning; nothing is written) ---------------
    simulation: Counter[str] = Counter()
    per_field: dict[str, Counter[str]] = {name: Counter() for name in FIELDS}
    rows: list[dict[str, Any]] = []
    resolver = lovable_sync.OwnerResolver(states)
    if pilot_doc is not None and states:
        current = current_values_bulk([s.matter for s in states])
        for state in states:
            plan = lovable_sync.plan_matter(
                state,
                cohort.get(str(state.source_id)),
                resolver=resolver,
                today=today,
                current=current[state.matter_id],
            )
            matter = sight.visible.get(state.matter_id)
            if plan.status:
                simulation["UNKNOWN_SOURCE"] += 1
                if matter is not None:
                    rows.append(_sim_row(state, matter, "*", "UNKNOWN_SOURCE", plan.status, ""))
                continue
            for decision in plan.decisions:
                category = simulation_category(decision)
                simulation[category] += 1
                if decision.field in per_field:
                    per_field[decision.field][category] += 1
                if matter is not None and category != "UNCHANGED":
                    rows.append(
                        _sim_row(
                            state,
                            matter,
                            decision.field,
                            category,
                            decision.outcome,
                            decision.reason,
                        )
                    )
    rows.sort(key=lambda r: (_ref_order(r["reference"]), _field_order(r["field"])))

    # Source changes no rule covers, over the window of the wider readings.
    start, _end = _window(None, None, moment)
    uncovered: Counter[str] = Counter()
    for in_pilot, mapping, names, values in LovableObservedChange.objects.filter(
        run__status=ObservationRunStatus.SUCCEEDED, run__started_at__gte=start
    ).values_list("in_pilot", "mapping", "field_names", "fields"):
        found = {str(n) for n in (names or [])} | {
            str(v.get("field")) for v in (values or []) if isinstance(v, dict)
        }
        pilot = in_pilot and mapping == MappingStatus.PILOT_LINKED
        for name in found:
            if not pilot or name not in lovable_sync.AUTO_FIELDS:
                uncovered[name] += 1

    # --- C. fields -----------------------------------------------------------
    fields = []
    for name in FIELDS:
        rule = FIELD_RULES[name]
        fields.append(
            FieldReadiness(
                field=name,
                label=_label(name),
                mapping=rule["mapping"],
                automatic=name in lovable_sync.AUTO_FIELDS,
                service=rule["service"],
                risks=list(rule["risks"]),
                blocking_reason=rule["blocking"],
                simulated=dict(sorted(per_field[name].items())),
                uncovered_changes=uncovered.get(name, 0),
            )
        )

    # --- D. coverage -----------------------------------------------------------
    live_states = [s for s in states if s.matter_id in sight.live]
    pending = _pending_fields(live_states)
    conflicts = [p for p in pending if (p.newest or {}).get("outcome") == SyncOutcome.CONFLICT]
    oldest = min((p.first_at for p in conflicts if p.first_at), default=None)
    oldest_days = (today - _local_day(oldest)).days if oldest else None
    coverage = _coverage(
        states,
        resolver,
        index=index,
        cohort_ids=cohort_ids,
        last_run=last_run,
        conflicts=len(conflicts),
        oldest_days=oldest_days,
        uncovered=sum(uncovered.values()),
        when=_local(moment),
    )

    # --- blockers ------------------------------------------------------------------
    blockers: list[str] = []
    if mode != SyncMode.APPLY:
        blockers.append(
            f"Omanik pole APPLY-režiimi kunagi lubanud (praegu «{SyncMode(mode).label}»)."
        )
    blockers.append(
        "Lovable'i teenusidentiteedi kirjutamiskeeld pole tõendatud: `lovable_sync access-probe` "
        "teeb nüüd ainult GET-päringuid; `lovable_sync access-sql` päringud tuleb omanikul "
        "Lovable'is käivitada ja tulemus kirja panna."
    )
    if not audit.available:
        blockers.append(audit.message)
    blockers.append("Hetkeseisu jaoks pole kinnitatud etapisõnastiku vastendust.")
    if pilot_doc is None:
        blockers.append(
            "Võrreldavat ja kontrollitud piloodi lugemist pole; simulatsiooni ei tehtud."
        )
    elif _freshness(snapshot.taken_at if snapshot else None, moment) != FRESH:
        blockers.append("Viimane võrreldav piloodi lugemine on vanem kui 26 tundi.")
    if last_run is not None and last_run.status == SyncRunStatus.FAILED:
        blockers.append(f"Viimane käivitus ebaõnnestus ({_failure_code(last_run.error)}).")
    held_by_cohort = Counter(s.cohort for s in states)
    for key, wanted in lovable_sync.expected_cohorts().items():
        if held_by_cohort.get(key, 0) != wanted:
            blockers.append(
                f"{_cohort_name(key)}: teemasid on {held_by_cohort.get(key, 0)}, mitte {wanted}."
            )
    if conflicts:
        blockers.append(f"Lahendamata vastuolusid: {len(conflicts)}.")
    if not (ctl and ctl.observe):
        blockers.append("Laiem vaatlus on välja lülitatud: kogu registri muutusi ei nähta.")

    return Readiness(
        generated_at=moment,
        source=source,
        identity=identity,
        fields=fields,
        coverage=coverage,
        simulation=dict(sorted(simulation.items())),
        simulation_rows=rows if _reads(user) else [],
        conflicts=len(conflicts),
        oldest_conflict_days=oldest_days,
        blockers=blockers,
        owner_decisions=list(OWNER_DECISIONS),
    )


def _sim_row(
    state: LovableSyncState, matter: Any, name: str, category: str, outcome: str, reason: str
) -> dict[str, Any]:
    return {
        "reference": state.reference,
        "title": matter.title,
        "matter_id": str(matter.pk),
        "matter_url": _matter_url(matter.pk),
        "field": name,
        "label": _label(name),
        "category": category,
        "category_label": LOVABLE_SIMULATION_LABELS.get(category, category),
        "outcome": outcome,
        "reason": reason,
        "reason_label": LOVABLE_REASONS.get(reason, ""),
    }


def _coverage(
    states: list[LovableSyncState],
    resolver: lovable_sync.OwnerResolver,
    *,
    index: list[dict[str, Any]],
    cohort_ids: set[str],
    last_run: LovableSyncRun | None,
    conflicts: int,
    oldest_days: int | None,
    uncovered: int,
    when: str,
) -> list[Indicator]:
    n = len(states)
    expected = lovable_sync.expected_cohorts()
    operational = [r for r in index if _is_operational(r)]
    linked = sum(1 for r in operational if str(r.get("id")) in cohort_ids)
    names: dict[str, bool] = {}
    with_owner = exact_owner = 0
    for state in states:
        name = (state.observed or {}).get("owner") or (state.applied.get("owner") or {}).get(
            "source"
        )
        if not name:
            continue
        with_owner += 1
        key = lovable_sync.name_key(name)
        if key not in names:
            names[key] = resolver.resolve(name)[0] is not None
        exact_owner += names[key]
    title_kept = sum(
        1
        for s in states
        if (s.applied.get("title") or {}).get("target")
        == (s.applied.get("title") or {}).get("source")
    )
    date_movable = sum(
        1
        for s in states
        if ((s.applied.get("next_action") or {}).get("treatment") == "SOURCE_NEXT_STEP")
        and (
            ((s.applied.get("next_action") or {}).get("target") or {}).get("date_precision")
            == "EXACT"
        )
    )
    pilot = f"piloodi {n} teemast"
    out = [
        _indicator("pilot_coverage", "Piloodi katvus", n, _population_phrase(expected), when),
        _indicator(
            "operational_source_coverage",
            "Operatiivsed teemad piloodis",
            linked if index else None,
            f"{len(operational)} operatiivsest teemast",
            when,
            "Lovable'i teemade indeksit pole.",
        ),
        _indicator(
            "owner_mapping",
            "Vastutaja täpne vaste",
            exact_owner,
            f"{with_owner} vastutajaga piloodi teemast",
            when,
        ),
        _indicator(
            "title_mapping",
            "Pealkiri allika sõnastuses",
            title_kept,
            pilot,
            when,
        ),
        _indicator(
            "next_action_text_mapping",
            "Järgmise tegevuse sõnastus",
            0,
            f"{pilot} (sõnastust ei kanta kunagi automaatselt)",
            when,
        ),
        _indicator(
            "next_action_date_mapping",
            "Järgmise tegevuse kuupäev",
            date_movable,
            f"{pilot} (piloodi täpse kuupäevaga allika tegevus)",
            when,
        ),
        _indicator("stage_mapping", "Hetkeseis", 0, f"{pilot} (kinnitatud vastendust pole)", when),
        _indicator(
            "commencement_mapping",
            "Jõustumise hinnangud",
            0,
            f"{pilot} (hinnang pole kinnitatud õigusmõju)",
            when,
        ),
        _indicator(
            "new_topic_mapping",
            "Uued Lovable'i teemad",
            0,
            "uut teemat (uut teemat ei seota kunagi automaatselt)",
            when,
        ),
    ]
    tally: dict[str, int] = {}
    if last_run is not None:
        tally = dict(
            LovableSyncDecision.objects.filter(run=last_run)
            .values_list("outcome")
            .annotate(c=Count("pk"))
            .values_list("outcome", "c")
        )
    night = _local(last_run.started_at) if last_run else "—"
    for outcome in (
        SyncOutcome.ELIGIBLE,
        SyncOutcome.LOCAL_TEST_CHANGE,
        SyncOutcome.CONFLICT,
        SyncOutcome.REVIEW_REQUIRED,
        SyncOutcome.ALREADY_CONVERGED,
        SyncOutcome.APPLIED,
        SyncOutcome.SOURCE_MISSING,
        SyncOutcome.IDENTIFIER_CHANGED,
        SyncOutcome.FAILED,
    ):
        out.append(
            _indicator(
                f"outcome_{outcome.lower()}",
                str(outcome.label),
                tally.get(outcome, 0) if last_run is not None else None,
                PENDING_TALLY_UNIT,
                night,
                "Võrdlust pole veel salvestatud.",
            )
        )
    out.append(
        _indicator(
            "unresolved_conflicts",
            "Lahendamata vastuolud",
            conflicts,
            "ootel välja" + (f"; vanim {oldest_days} päeva" if oldest_days is not None else ""),
            "praegu",
        )
    )
    out.append(
        _indicator(
            "uncovered_source_changes",
            "Reeglita allikamuudatused",
            uncovered,
            "välja muutust laiemates lugemistes, mida ükski automaatne reegel ei kata",
            f"viimased {MAX_WINDOW_DAYS} päeva",
        )
    )
    return out
