"""When a OneNote attachment entered the source, kept apart from when we saw it.

Extends ADR 0092 §6 (amendment of 2026-10-20), it does not replace it. That
rule already dates an imported file by the `creationTime` of the outline
element holding it (`LegacySourceResource.source_added_at`,
`ONENOTE_BLOCK_CREATED`), groups files per Europe/Tallinn day of that time,
says «lisamise kuupäev teadmata» when there is none, and marks a file older
than its page (`MOVED_CONTENT`, «lisatud enne praeguse lehe loomist»).

What Phase 1 evidence adds (Phase 1 report, "Historical file dates"):

* The time belongs to the **occurrence** (the outline element), not to the
  bytes now inside it. 4 of the 16 sampled files with an internal date carry
  one *later* than their block's `creationTime`, and each of those blocks was
  modified after that date. A file can be replaced inside an existing block,
  so a version after the first is never dated by the block's creation.
* Graph output HTML carries no attachment-level time (documented attribute
  tables for `object`/`img`/`p`). A Graph-only occurrence has no source
  addition date unless a *verified* mapping ties it to a desktop observation.
* The page XML's root ``lastModifiedTime`` is **not** a page modification
  time: in the D1 pilot it became the moment of navigation on all 10 pages
  while the hierarchy's page time and every block time stayed unchanged.
  ``PAGE_MODIFIED`` is read from the hierarchy (or Graph metadata) only.
* A floating object (on the page canvas, outside any outline) has no
  ``creationTime`` and stays undated. Its own ``lastModifiedTime`` is kept as
  source metadata (``ONENOTE_OBJECT_MODIFIED``) and may be shown on its own;
  it is never an addition date, a bound on one, or a chronology day (owner
  decision D8, 2026-11-04). Such a file stays eligible for import.
* A scanner that first sees a file has seen it, nothing more. The most it may
  record is the interval between the last complete scan without it and the
  first complete scan with it — a detection window, never a source date.

Seven times are kept separately (`OccurrenceDates`), each with its basis,
precision and the capture it came from; none is ever substituted for another.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from enum import StrEnum
from zoneinfo import ZoneInfo

TALLINN = ZoneInfo("Europe/Tallinn")

#: Display strings, reused from app/matters/timeline.py so the two stay one language.
UNKNOWN_ADDITION = "Lisamise kuupäev teadmata"
PREDATES_PAGE = "lisatud enne praeguse lehe loomist"
CONTENT_MAY_BE_NEWER = "faili sisu võib olla hiljem uuendatud"
DETECTED_WINDOW = "Juristid märkas faili vahemikus {start}–{end}"
#: Source metadata only (D8): shown apart from the chronology, never as "lisatud".
OBJECT_MODIFIED = "OneNote'i objekti viimane muutmine: {value}"

#: A block modified this long after its creation may hold newer bytes.
CONTENT_DRIFT = timedelta(hours=1)


class DateBasis(StrEnum):
    ONENOTE_BLOCK_CREATED = "ONENOTE_BLOCK_CREATED"  # one:OE/@creationTime (desktop XML)
    ONENOTE_BLOCK_MODIFIED = "ONENOTE_BLOCK_MODIFIED"  # one:OE/@lastModifiedTime
    # A floating one:InsertedFile's own @lastModifiedTime (it has no creationTime). D8.
    ONENOTE_OBJECT_MODIFIED = "ONENOTE_OBJECT_MODIFIED"
    FILE_INTERNAL_METADATA = (
        "FILE_INTERNAL_METADATA"  # OOXML core, PDF info, MSG sent, ASiC signing
    )
    PAGE_CREATED = "PAGE_CREATED"
    PAGE_MODIFIED = "PAGE_MODIFIED"
    FIRST_DETECTED = "FIRST_DETECTED"  # technical: a Juristid scan
    BYTES_VERIFIED = "BYTES_VERIFIED"  # technical
    PROMOTED = "PROMOTED"  # technical: entered Documents


TECHNICAL_BASES = frozenset(
    {DateBasis.FIRST_DETECTED, DateBasis.BYTES_VERIFIED, DateBasis.PROMOTED}
)


class Precision(StrEnum):
    SECOND = "SECOND"  # the desktop XML gives whole seconds, UTC ("…T09:39:26.000Z")
    INTERVAL = "INTERVAL"  # a bounded window, not an instant
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class SourceTime:
    value: datetime  # timezone-aware, stored UTC
    basis: DateBasis
    reader: str  # "ONENOTE_DESKTOP" | "GRAPH" | "FILE" | "JURISTID"
    capture_ref: str  # capture id the value was read from
    precision: Precision = Precision.SECOND
    detail: str = ""  # e.g. which file-internal field

    def __post_init__(self) -> None:
        if self.value.tzinfo is None:
            raise ValueError("source times must be timezone-aware; a naive time has no basis")


@dataclass(frozen=True)
class Window:
    not_before: datetime | None  # None: no complete earlier scan proves absence
    not_after: datetime

    @property
    def bounded(self) -> bool:
        return self.not_before is not None


@dataclass
class OccurrenceDates:
    """Everything known about one attachment occurrence's times, each kept apart."""

    block_created: list[SourceTime] = field(default_factory=list)  # one per verified observation
    block_modified: list[SourceTime] = field(default_factory=list)
    file_internal: list[SourceTime] = field(default_factory=list)  # plausibility only
    object_modified: SourceTime | None = None  # floating object only; display metadata (D8)
    page_created: SourceTime | None = None
    page_modified: SourceTime | None = None
    first_detected: Window | None = None
    bytes_verified_at: datetime | None = None
    promoted_at: datetime | None = None


class Qualifier(StrEnum):
    PREDATES_PAGE = "PREDATES_PAGE"  # MOVED_CONTENT: older than the page it is on now
    CONTENT_MAY_BE_NEWER = "CONTENT_MAY_BE_NEWER"
    SOURCE_TIME_CHANGED = "SOURCE_TIME_CHANGED"  # verified observations disagree
    NO_VERIFIED_SOURCE_TIME = "NO_VERIFIED_SOURCE_TIME"
    DETECTION_WINDOW_ONLY = "DETECTION_WINDOW_ONLY"


@dataclass(frozen=True)
class ChronologyDate:
    """What the Teema chronology may say about when an occurrence entered OneNote."""

    value: datetime | None
    basis: DateBasis | None
    qualifiers: tuple[Qualifier, ...] = ()
    window: Window | None = None

    @property
    def tallinn_day(self) -> date | None:
        return tallinn_day(self.value) if self.value else None

    @property
    def label(self) -> str:
        return UNKNOWN_ADDITION if self.value is None else ""


def tallinn_day(moment: datetime) -> date:
    """The Europe/Tallinn calendar day of a UTC instant, daylight saving included."""
    if moment.tzinfo is None:
        raise ValueError("naive datetime")
    return moment.astimezone(TALLINN).date()


def chronology_date(dates: OccurrenceDates) -> ChronologyDate:
    """The occurrence's historical date, or an honest absence of one.

    Only a verified block creation time is a source addition date. Disagreeing
    verified observations make it unknown (provenance before coverage); the
    first value is never silently preferred. Page dates, file metadata, a
    floating object's own modification time and Juristid's own times are never
    used, even as a fallback.
    """
    if dates.object_modified is not None and (
        dates.object_modified.basis is not DateBasis.ONENOTE_OBJECT_MODIFIED
    ):
        raise ValueError("object_modified holds only ONENOTE_OBJECT_MODIFIED")
    values = {item.value for item in dates.block_created}
    qualifiers: list[Qualifier] = []
    if len(values) > 1:
        qualifiers.append(Qualifier.SOURCE_TIME_CHANGED)
        return ChronologyDate(None, None, tuple(qualifiers), dates.first_detected)
    if not values:
        qualifiers.append(Qualifier.NO_VERIFIED_SOURCE_TIME)
        if dates.first_detected is not None:
            qualifiers.append(Qualifier.DETECTION_WINDOW_ONLY)
        return ChronologyDate(None, None, tuple(qualifiers), dates.first_detected)
    (created,) = values
    if dates.page_created is not None and created < dates.page_created.value:
        qualifiers.append(Qualifier.PREDATES_PAGE)
    latest_modified = max((item.value for item in dates.block_modified), default=None)
    newer_inside = any(item.value > created for item in dates.file_internal)
    if newer_inside or (latest_modified is not None and latest_modified - created > CONTENT_DRIFT):
        qualifiers.append(Qualifier.CONTENT_MAY_BE_NEWER)
    return ChronologyDate(created, DateBasis.ONENOTE_BLOCK_CREATED, tuple(qualifiers))


# -- detection windows ----------------------------------------------------------


@dataclass(frozen=True)
class ScanObservation:
    completed_at: datetime
    complete: bool  # the scope's listing and the page capture were both complete
    present: bool  # the occurrence was in that capture


def detection_window(observations: Sequence[ScanObservation]) -> Window | None:
    """Bound when Juristid first *noticed* an occurrence. Not when it was added.

    The lower bound is the last *complete* observation without it before the
    first one with it; an incomplete scan proves nothing about absence, so it
    never narrows the window.
    """
    ordered = sorted(observations, key=lambda item: item.completed_at)
    first_seen = next((item for item in ordered if item.present), None)
    if first_seen is None:
        return None
    earlier_absent = [
        item
        for item in ordered
        if item.completed_at < first_seen.completed_at and item.complete and not item.present
    ]
    return Window(
        earlier_absent[-1].completed_at if earlier_absent else None, first_seen.completed_at
    )


# -- versions ------------------------------------------------------------------------


@dataclass(frozen=True)
class VersionObservation:
    capture_id: str
    captured_at: datetime
    sha256: str
    block_modified: datetime | None


@dataclass(frozen=True)
class VersionDate:
    sha256: str
    ordinal: int  # 1 = the bytes first captured for this occurrence
    window: Window | None  # for ordinal > 1: when the bytes changed, bounded by captures
    present_by: datetime | None  # the block's lastModifiedTime when first captured (upper bound)


def version_dates(observations: Iterable[VersionObservation]) -> list[VersionDate]:
    """One entry per distinct binary of an occurrence, in the order captured.

    Version 1 inherits nothing here: its source date is the occurrence's
    chronology date. A later version is dated only by the interval between the
    last capture that still held the old bytes and the first that held the new
    ones (tightened by the block's modification time); never by the block's
    creation, which belongs to the occurrence.
    """
    ordered = sorted(observations, key=lambda item: item.captured_at)
    out: list[VersionDate] = []
    previous: VersionObservation | None = None
    for item in ordered:
        if previous is not None and item.sha256 == previous.sha256:
            previous = item
            continue
        if previous is None:
            out.append(VersionDate(item.sha256, 1, None, item.block_modified))
        else:
            upper = item.captured_at
            if (
                item.block_modified is not None
                and previous.captured_at < item.block_modified < upper
            ):
                upper = item.block_modified
            out.append(
                VersionDate(
                    item.sha256,
                    len(out) + 1,
                    Window(previous.captured_at, upper),
                    item.block_modified,
                )
            )
        previous = item
    return out


# -- the Teema chronology (proposal; app/matters/timeline.py stays the implementation) ------


@dataclass(frozen=True)
class OccurrenceVersion:
    key: str  # stable: <source page identity>/<occurrence id>/<sha256>
    document_id: str
    visible: bool  # already decided by Document.objects.visible_to(reader)
    chronology: ChronologyDate
    version: int = 1
    version_window: Window | None = None


@dataclass(frozen=True)
class ChronologyRow:
    group: str  # "day:YYYY-MM-DD" | "undated" | "updated:YYYY-MM-DD" | "updated-window"
    anchor: datetime | None
    members: tuple[str, ...]
    qualifiers: tuple[Qualifier, ...]


def chronology_rows(
    items: Iterable[OccurrenceVersion], *, stored_at: datetime
) -> list[ChronologyRow]:
    """Group visible occurrence-versions into Teema chronology rows.

    Permission first (ADR 0092 §7): invisible items are dropped before anything
    is grouped, counted or dated. Keys are de-duplicated, so a repeat ingestion
    draws the same rows. First versions read on their Tallinn day of addition;
    undated ones on one honest undated row (anchored where they were stored, as
    today); later versions on the day their change window fits in, or a row of
    their own when the window spans days. The technical audit chronology is
    separate and untouched.
    """
    seen: dict[str, OccurrenceVersion] = {}
    for item in items:
        if item.visible:
            seen.setdefault(item.key, item)
    groups: dict[str, list[OccurrenceVersion]] = {}
    anchors: dict[str, datetime | None] = {}
    for item in seen.values():
        anchor: datetime | None
        if item.version > 1:
            window = item.version_window
            if (
                window
                and window.not_before
                and tallinn_day(window.not_before) == tallinn_day(window.not_after)
            ):
                group, anchor = (
                    f"updated:{tallinn_day(window.not_after).isoformat()}",
                    window.not_after,
                )
            else:
                group, anchor = "updated-window", window.not_after if window else None
        elif item.chronology.value is not None:
            group = f"day:{tallinn_day(item.chronology.value).isoformat()}"
            anchor = item.chronology.value
        else:
            group, anchor = "undated", stored_at
        groups.setdefault(group, []).append(item)
        held = anchors.get(group)
        anchors[group] = anchor if held is None or (anchor is not None and anchor < held) else held
    rows = []
    for group, members in groups.items():
        qualifiers = sorted({q for m in members for q in m.chronology.qualifiers})
        rows.append(
            ChronologyRow(
                group, anchors[group], tuple(sorted(m.key for m in members)), tuple(qualifiers)
            )
        )
    return sorted(rows, key=lambda row: (row.anchor or stored_at, row.group))


# -- across readers ---------------------------------------------------------------------


class ReferenceDecision(StrEnum):
    APPLIED = "APPLIED"
    MAPPING_NOT_VERIFIED = "MAPPING_NOT_VERIFIED"
    BYTES_DIFFER = "BYTES_DIFFER"
    NOT_A_BLOCK_TIME = "NOT_A_BLOCK_TIME"


def apply_reference_time(
    dates: OccurrenceDates,
    desktop_time: SourceTime,
    *,
    mapping_verified: bool,
    graph_sha256: str,
    desktop_sha256: str,
) -> ReferenceDecision:
    """Give a Graph occurrence (which has no attachment time) a desktop block time.

    Only through a verified occurrence mapping and only when both readers hold
    the same bytes — otherwise the desktop time may belong to another file or
    another version, and the occurrence stays undated.
    """
    if (
        desktop_time.basis is not DateBasis.ONENOTE_BLOCK_CREATED
        or desktop_time.reader != "ONENOTE_DESKTOP"
    ):
        return ReferenceDecision.NOT_A_BLOCK_TIME
    if not mapping_verified:
        return ReferenceDecision.MAPPING_NOT_VERIFIED
    if not graph_sha256 or graph_sha256 != desktop_sha256:
        return ReferenceDecision.BYTES_DIFFER
    if desktop_time not in dates.block_created:
        dates.block_created.append(desktop_time)
    return ReferenceDecision.APPLIED
