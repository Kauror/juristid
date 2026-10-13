"""Compare a Lovable snapshot with its predecessor and with canonical Juristid. Reads only.

Three questions, answered over stored mirror rows and the **reader-visible**
Matter population, and never written down anywhere:

1. **What changed in Lovable** between the latest snapshot and the one before
   it — rows added, rows removed, and field-level changes, keyed by `nr`
   because Lovable's own row ids are regenerated on every upload.
2. **Which Matter each Lovable row is about** — matched, unmatched or
   ambiguous, by the Teemaviide alone. No title similarity, no owner guess, no
   AI: a deterministic key or nothing (the rule `app.legacy_import.resolution`
   states for the whole migration).
3. **Where Lovable disagrees with canonical** — only for ``COMPARABLE`` fields,
   only on matched rows, and only by an exact rule. Unknown is never a
   conflict: an empty side or a name that does not resolve is reported as a
   coverage limit, not as a difference.

Matching runs over ``Matter.objects.visible_to(user)``. A Matter the reader
cannot read is indistinguishable from no Matter here, exactly as on every other
surface (CONTRACT §3, docs/adr/0005); the coverage block says so in words.

Nothing here writes. There is no promotion path: turning a mirror value into a
canonical fact is a future, separately reviewed decision (docs/adr/0157 §6).
"""

from __future__ import annotations

import datetime as dt
import re
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

from app.core.text import normalize_for_matching
from app.legacy_import.resolution import (
    METHOD_BLANK,
    KnownPeople,
    MappingTables,
    resolve_owner,
)
from app.lovable_mirror import catalogue
from app.lovable_mirror.catalogue import Canonical, MappingClass
from app.lovable_mirror.importer import canonical_json
from app.lovable_mirror.models import SourceFieldValue, SourceRecord, SourceSnapshot
from app.matters.models import Matter

OPERATIVE_TABLE = "topics"
_REFERENCE = re.compile(r"^(\d{4})_(\d{1,6})$")


class MatchClass:
    MATCHED = "MATCHED"
    UNMATCHED = "UNMATCHED"
    AMBIGUOUS = "AMBIGUOUS"

    LABELS = {
        MATCHED: "Seotud",
        UNMATCHED: "Sidumata",
        AMBIGUOUS: "Mitmeti mõistetav",
    }


class MatchReason:
    REFERENCE = "REFERENCE"
    NO_KEY = "NO_KEY"
    NO_READABLE_MATTER = "NO_READABLE_MATTER"
    DUPLICATE_SOURCE_KEY = "DUPLICATE_SOURCE_KEY"

    LABELS = {
        REFERENCE: "Teemaviide kattub",
        NO_KEY: "Lovable'i real puudub kehtiv NR (YYYY_N)",
        NO_READABLE_MATTER: "Selle viitega loetavat teemat ei ole",
        DUPLICATE_SOURCE_KEY: "Sama NR esineb Lovable'is mitmel real",
    }


class Outcome:
    SAME = "SAME"
    DIFFERENT = "DIFFERENT"
    SOURCE_EMPTY = "SOURCE_EMPTY"
    CANONICAL_EMPTY = "CANONICAL_EMPTY"
    NOT_COMPARABLE = "NOT_COMPARABLE"
    SHOWN = "SHOWN"

    LABELS = {
        SAME: "Kattub",
        DIFFERENT: "Erineb",
        SOURCE_EMPTY: "Lovable'is tühi",
        CANONICAL_EMPTY: "Juristides tühi",
        NOT_COMPARABLE: "Ei saa võrrelda",
        SHOWN: "Ainult ülevaatuseks",
    }


# ---------------------------------------------------------------------------
# Reading stored snapshots
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SourceRow:
    table: str
    source_id: str
    key: str
    values: dict[str, Any]

    @property
    def title(self) -> str:
        value = self.values.get("teema")
        return str(value) if value is not None else ""


def _rows(snapshot: SourceSnapshot, table: str) -> list[SourceRow]:
    records = list(
        SourceRecord.objects.filter(snapshot=snapshot, table=table).order_by(
            "stable_key", "source_id"
        )
    )
    values: dict[Any, dict[str, Any]] = defaultdict(dict)
    for value in SourceFieldValue.objects.filter(record__in=records).only(
        "record_id", "field", "value"
    ):
        values[value.record_id][value.field] = value.value
    return [
        SourceRow(table=table, source_id=r.source_id, key=r.stable_key, values=values[r.pk])
        for r in records
    ]


@dataclass(frozen=True)
class OperativeRows:
    rows: list[SourceRow]
    dataset_id: str
    boundary: str


def operative_rows(snapshot: SourceSnapshot) -> OperativeRows:
    """The `topics` rows of the dataset Lovable itself treated as current.

    Lovable keeps every earlier upload's rows in `topics` and flips
    `datasets.is_current`, so the operative population is one dataset's rows,
    not the table. When the bundle cannot say which dataset is current, the
    answer is *none*, with the reason — never the union.
    """
    topics = _rows(snapshot, OPERATIVE_TABLE)
    if not topics:
        return OperativeRows([], "", "Hetktõmmises ei ole tabelit «topics» või see on tühi.")
    present = sorted({str(row.values.get("dataset_id") or "") for row in topics})
    current = [
        row.source_id for row in _rows(snapshot, "datasets") if row.values.get("is_current") is True
    ]
    if len(current) == 1:
        dataset_id = current[0]
    elif not current and len(present) == 1:
        dataset_id = present[0]
    else:
        return OperativeRows(
            [],
            "",
            "Kehtivat Lovable'i andmestikku ei saa üheselt määrata "
            f"(kehtivaks märgitud: {len(current)}, andmestikke ridades: {len(present)}). "
            "Võrdlust ei tehtud.",
        )
    rows = [row for row in topics if str(row.values.get("dataset_id") or "") == dataset_id]
    if not rows:
        return OperativeRows(
            [], dataset_id, "Kehtiva andmestiku kohta ei ole hetktõmmises ühtki rida."
        )
    return OperativeRows(rows, dataset_id, "")


def previous_snapshot(snapshot: SourceSnapshot) -> SourceSnapshot | None:
    """The snapshot exported immediately before this one, by source time."""
    return (
        SourceSnapshot.objects.filter(source_exported_at__lt=snapshot.source_exported_at)
        .order_by("-source_exported_at", "-imported_at", "-id")
        .first()
    )


def latest_snapshot() -> SourceSnapshot | None:
    return SourceSnapshot.objects.order_by("-source_exported_at", "-imported_at", "-id").first()


# ---------------------------------------------------------------------------
# Change between consecutive snapshots
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FieldChange:
    field: str
    mapping_class: str
    before: Any
    after: Any

    @property
    def class_label(self) -> str:
        return str(MappingClass(self.mapping_class).label)


@dataclass(frozen=True)
class RowChange:
    key: str
    title: str
    fields: tuple[FieldChange, ...] = ()


@dataclass
class SnapshotDiff:
    added: list[RowChange] = field(default_factory=list)
    removed: list[RowChange] = field(default_factory=list)
    changed: list[RowChange] = field(default_factory=list)
    unkeyed: int = 0


def _keyed(rows: list[SourceRow]) -> tuple[dict[str, SourceRow], int]:
    groups: dict[str, list[SourceRow]] = defaultdict(list)
    unkeyed = 0
    for row in rows:
        if row.key:
            groups[row.key].append(row)
        else:
            unkeyed += 1
    # A key on two rows cannot be followed across snapshots; it is left out of
    # the diff and counted, rather than paired with whichever row came first.
    keyed = {key: group[0] for key, group in groups.items() if len(group) == 1}
    return keyed, unkeyed + sum(len(g) for g in groups.values() if len(g) > 1)


def diff_rows(before: list[SourceRow], after: list[SourceRow]) -> SnapshotDiff:
    spec = catalogue.TABLES[OPERATIVE_TABLE]
    old, old_unkeyed = _keyed(before)
    new, new_unkeyed = _keyed(after)
    result = SnapshotDiff(unkeyed=old_unkeyed + new_unkeyed)
    for key in sorted(new.keys() - old.keys()):
        result.added.append(RowChange(key=key, title=new[key].title))
    for key in sorted(old.keys() - new.keys()):
        result.removed.append(RowChange(key=key, title=old[key].title))
    for key in sorted(new.keys() & old.keys()):
        a, b = old[key].values, new[key].values
        changes = []
        for name in sorted(a.keys() | b.keys()):
            mapping_class = spec.class_of(name)
            if mapping_class not in catalogue.DIFFED_CLASSES:
                continue
            if canonical_json(a.get(name)) != canonical_json(b.get(name)):
                changes.append(FieldChange(name, mapping_class, a.get(name), b.get(name)))
        if changes:
            result.changed.append(RowChange(key=key, title=new[key].title, fields=tuple(changes)))
    return result


# ---------------------------------------------------------------------------
# Matching and canonical comparison
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FieldComparison:
    field: str
    label: str
    mapping_class: str
    source: str
    canonical: str
    outcome: str

    @property
    def outcome_label(self) -> str:
        return Outcome.LABELS[self.outcome]


@dataclass(frozen=True)
class RowReconciliation:
    row: SourceRow
    match_class: str
    reason: str
    matter: Matter | None = None
    comparisons: tuple[FieldComparison, ...] = ()

    @property
    def match_label(self) -> str:
        return MatchClass.LABELS[self.match_class]

    @property
    def reason_label(self) -> str:
        return MatchReason.LABELS[self.reason]

    @property
    def conflicts(self) -> list[FieldComparison]:
        return [c for c in self.comparisons if c.outcome == Outcome.DIFFERENT]


def parse_reference(value: str) -> tuple[int, int] | None:
    match = _REFERENCE.match(value.strip())
    if not match:
        return None
    return int(match.group(1)), int(match.group(2))


def _text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _date(value: Any) -> dt.date | None:
    text = _text(value)
    try:
        return dt.date.fromisoformat(text[:10]) if len(text) >= 10 else None
    except ValueError:
        return None


def _compare_date(source: Any, canonical: dt.date | None) -> tuple[str, str, str]:
    text = _text(source)
    shown = canonical.isoformat() if canonical else ""
    if not text:
        return text, shown, Outcome.SOURCE_EMPTY
    parsed = _date(text)
    if parsed is None:
        return text, shown, Outcome.NOT_COMPARABLE
    if canonical is None:
        return text, shown, Outcome.CANONICAL_EMPTY
    return text, shown, Outcome.SAME if parsed == canonical else Outcome.DIFFERENT


def _compare(
    mapping: catalogue.FieldMapping, values: dict[str, Any], matter: Matter, people: KnownPeople
) -> FieldComparison:
    raw = values.get(mapping.name)
    label = str(Canonical(mapping.canonical).label)
    source = _text(raw)
    canonical = ""
    outcome = Outcome.SHOWN
    kind = mapping.canonical

    if kind == Canonical.TITLE:
        canonical = matter.title
        known = {normalize_for_matching(t) for t in [matter.title, *matter.alternate_titles]}
        if not source:
            outcome = Outcome.SOURCE_EMPTY
        else:
            outcome = Outcome.SAME if normalize_for_matching(source) in known else Outcome.DIFFERENT
    elif kind == Canonical.RECEIVED_DATE:
        source, canonical, outcome = _compare_date(raw, matter.received_date)
    elif kind == Canonical.RESPONSE_DEADLINE:
        source, canonical, outcome = _compare_date(raw, matter.response_deadline)
    elif kind == Canonical.OWNER:
        canonical = matter.owner.display_name if matter.owner is not None else ""
        resolution = resolve_owner(source, MappingTables.empty(), people)
        if resolution.method == METHOD_BLANK:
            outcome = Outcome.SOURCE_EMPTY
        elif not resolution.resolved:
            outcome = Outcome.NOT_COMPARABLE
        elif matter.owner_id is None:
            outcome = Outcome.CANONICAL_EMPTY
        else:
            outcome = Outcome.SAME if resolution.value.pk == matter.owner_id else Outcome.DIFFERENT
    elif kind == Canonical.LEGAL_INSTRUMENTS:
        canonical = ", ".join(i.label_et for i in matter.legal_instruments.all())
    elif kind == Canonical.STAGE:
        canonical = matter.stage.label_et if matter.stage is not None else ""

    if mapping.mapping_class != MappingClass.COMPARABLE and outcome != Outcome.SHOWN:
        outcome = Outcome.SHOWN  # only COMPARABLE fields may ever disagree
    return FieldComparison(mapping.name, label, mapping.mapping_class, source, canonical, outcome)


#: The columns set beside a matched Matter, in reading order.
COMPARED_FIELDS: tuple[catalogue.FieldMapping, ...] = tuple(
    m
    for m in catalogue.TABLES[OPERATIVE_TABLE].fields
    if m.canonical not in (Canonical.NONE, Canonical.REFERENCE)
)


def match_rows(rows: list[SourceRow], user: Any) -> list[RowReconciliation]:
    groups: dict[str, list[SourceRow]] = defaultdict(list)
    for row in rows:
        groups[row.key].append(row)

    wanted = {ref for key in groups if key and (ref := parse_reference(key))}
    visible: dict[tuple[int, int], Matter] = {}
    if wanted:
        years = {year for year, _ in wanted}
        for matter in (
            Matter.objects.visible_to(user)
            .filter(reference_year__in=years)
            .select_related("owner", "stage")
            .prefetch_related("legal_instruments")
        ):
            pair = (matter.reference_year, matter.reference_number)
            if pair in wanted:
                visible[pair] = matter

    people = KnownPeople.load()
    result: list[RowReconciliation] = []
    for key in sorted(groups):
        group = groups[key]
        reference = parse_reference(key) if key else None
        for row in group:
            if reference is None:
                result.append(RowReconciliation(row, MatchClass.UNMATCHED, MatchReason.NO_KEY))
            elif len(group) > 1:
                result.append(
                    RowReconciliation(row, MatchClass.AMBIGUOUS, MatchReason.DUPLICATE_SOURCE_KEY)
                )
            elif reference not in visible:
                result.append(
                    RowReconciliation(row, MatchClass.UNMATCHED, MatchReason.NO_READABLE_MATTER)
                )
            else:
                matter = visible[reference]
                result.append(
                    RowReconciliation(
                        row,
                        MatchClass.MATCHED,
                        MatchReason.REFERENCE,
                        matter,
                        tuple(_compare(m, row.values, matter, people) for m in COMPARED_FIELDS),
                    )
                )
    return result


# ---------------------------------------------------------------------------
# The report
# ---------------------------------------------------------------------------


@dataclass
class Coverage:
    tables_present: dict[str, int]
    tables_absent: list[str]
    unverified_tables: tuple[str, ...]
    uncatalogued_fields: dict[str, list[str]]
    dataset_boundary: str
    previous_boundary: str
    unkeyed_rows: int
    outcome_counts: dict[str, int]
    visibility_note: str = (
        "Sidumine arvestab ainult teemasid, mida sina näed. Teema, mida sa lugeda ei "
        "tohi, paistab siin sidumata reana — nagu kõikjal mujal rakenduses."
    )
    canonical_note: str = (
        "Lovable'i andmed ei ole kanoonilised. Siit ei kirjutata ühtki Teemat, Arvamust, "
        "Järgmist sammu, dokumenti ega vastutajat."
    )


@dataclass
class ReconciliationReport:
    snapshot: SourceSnapshot
    previous: SourceSnapshot | None
    rows: list[RowReconciliation]
    diff: SnapshotDiff
    coverage: Coverage

    def _of(self, match_class: str) -> list[RowReconciliation]:
        return [r for r in self.rows if r.match_class == match_class]

    @property
    def matched(self) -> list[RowReconciliation]:
        return self._of(MatchClass.MATCHED)

    @property
    def unmatched(self) -> list[RowReconciliation]:
        return self._of(MatchClass.UNMATCHED)

    @property
    def ambiguous(self) -> list[RowReconciliation]:
        return self._of(MatchClass.AMBIGUOUS)

    @property
    def conflicts(self) -> list[RowReconciliation]:
        return [r for r in self.matched if r.conflicts]


def build_report(user: Any, snapshot: SourceSnapshot | None = None) -> ReconciliationReport | None:
    """The whole preview for one reader, or None when nothing has been imported."""
    snapshot = snapshot or latest_snapshot()
    if snapshot is None:
        return None
    current = operative_rows(snapshot)
    previous = previous_snapshot(snapshot)

    previous_boundary = ""
    if previous is None:
        previous_boundary = "Esimene hetktõmmis: varasemaga võrrelda ei ole."
        diff = SnapshotDiff()
        diff.unkeyed = _keyed(current.rows)[1]
    else:
        before = operative_rows(previous)
        if before.boundary:
            previous_boundary = f"Eelmine hetktõmmis: {before.boundary}"
        diff = diff_rows(before.rows, current.rows)

    rows = match_rows(current.rows, user)

    outcome_counts: dict[str, int] = defaultdict(int)
    for row in rows:
        for comparison in row.comparisons:
            if comparison.mapping_class == MappingClass.COMPARABLE:
                outcome_counts[comparison.outcome] += 1

    present = dict(sorted(snapshot.row_counts.items()))
    coverage = Coverage(
        tables_present=present,
        tables_absent=sorted(set(catalogue.TABLES) - set(present)),
        unverified_tables=catalogue.UNVERIFIED_TABLES,
        uncatalogued_fields=dict(snapshot.uncatalogued_fields),
        dataset_boundary=current.boundary,
        previous_boundary=previous_boundary,
        unkeyed_rows=diff.unkeyed,
        outcome_counts={
            Outcome.LABELS[k]: v for k, v in sorted(outcome_counts.items()) if k != Outcome.SAME
        },
    )
    return ReconciliationReport(snapshot, previous, rows, diff, coverage)
