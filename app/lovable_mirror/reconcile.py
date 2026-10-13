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

**Who may see a Lovable row** (docs/adr/0157 §5a). A Lovable row is a copy of
register content, so it is shown only to a reader who could see the canonical
content it stands for, and the rule fails closed:

* Each ``nr`` is first looked up against **every** Matter, tombstones included,
  in one privileged internal step (:func:`source_access`). That answer never
  leaves this module: it decides only whether the row may be shown.
* A row whose ``nr`` belongs to a Matter is shown only if that Matter is in
  ``Matter.objects.visible_to(reader)``. A technical administrator does not
  read RESTRICTED Matters (AGENTS.md, ``app.core.authorization``), so such a
  row is withheld — not shown as «unmatched», which would display its text.
* A row with no Matter at all (or no valid ``nr``) has no canonical visibility
  to inherit; it may be about anything, including restricted work. It is shown
  only to a reader whose scope already reads every RESTRICTED Matter
  (``Scope.sees_all_restricted``). On this ADMINISTRATOR-only page that means
  an administrator under an active break-glass grant; without one the rows are
  withheld.
* A withheld row contributes only to one opaque count —
  «N rida varjatud: puudub õigus vastava teema sisu nägemiseks» — with no
  ``nr``, no title, no field and no match class. Rows about a RESTRICTED Matter
  and rows with no Matter fall into the same count, so the count cannot be used
  to learn that a restricted Matter exists.

Withheld rows are dropped while the report is built, before any template or
JSON writer sees it, so no presentation layer can leak them by mistake.

Nothing here writes. There is no promotion path: turning a mirror value into a
canonical fact is a future, separately reviewed decision (docs/adr/0157 §6).
"""

from __future__ import annotations

import datetime as dt
import re
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from app.core.authorization import scope_for_user
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
    NO_MATTER = "NO_MATTER"
    DUPLICATE_SOURCE_KEY = "DUPLICATE_SOURCE_KEY"

    LABELS = {
        REFERENCE: "Teemaviide kattub",
        NO_KEY: "Lovable'i real puudub kehtiv NR (YYYY_N)",
        NO_MATTER: "Selle viitega teemat ei ole",
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
    """Every stored row of one table, in two queries whatever the row count."""
    records = list(
        SourceRecord.objects.filter(snapshot=snapshot, table=table).order_by(
            "stable_key", "source_id"
        )
    )
    values: dict[Any, dict[str, Any]] = defaultdict(dict)
    for value in SourceFieldValue.objects.filter(
        record__snapshot=snapshot, record__table=table
    ).only("record_id", "field", "value"):
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


#: The one message a withheld row is ever represented by (docs/adr/0157 §5a).
HIDDEN_REASON = "puudub õigus vastava teema sisu nägemiseks"


def hidden_message(count: int) -> str:
    return f"{count} rida varjatud: {HIDDEN_REASON}"


@dataclass(frozen=True)
class SourceAccess:
    """Which Lovable rows one reader may see. Built once per report.

    ``existing`` is the privileged part — which references belong to *any*
    Matter — and is consulted only by :meth:`may_see`; it is never shown and
    never handed to a template. ``readable`` holds the Matters the reader may
    read, already loaded for the comparison.
    """

    existing: frozenset[tuple[int, int]]
    readable: dict[tuple[int, int], Matter]
    sees_unmatched: bool

    def may_see(self, key: str) -> bool:
        reference = parse_reference(key) if key else None
        if reference is not None and reference in self.existing:
            return reference in self.readable
        # No Matter (or no valid key): nothing to inherit visibility from, so
        # only a reader who already reads everything restricted sees the row.
        return self.sees_unmatched


def source_access(keys: Iterable[str], user: Any) -> SourceAccess:
    """Decide visibility for every ``nr`` in a bounded number of queries.

    Two Matter queries (plus the prefetch of legal instruments), whatever the
    number of rows: the privileged existence lookup over ``Matter.all_objects``
    — tombstones included, so a deleted Matter's reference stays withheld
    rather than turning into an «unmatched» row with text — and the reader's
    ``visible_to`` read of only those Matters.
    """
    wanted = {ref for key in keys if key and (ref := parse_reference(key))}
    existing: dict[tuple[int, int], Any] = {}
    if wanted:
        for pk, year, number in Matter.all_objects.filter(
            reference_year__in={year for year, _ in wanted},
            reference_number__in={number for _, number in wanted},
        ).values_list("pk", "reference_year", "reference_number"):
            if year is not None and number is not None and (year, number) in wanted:
                existing[(year, number)] = pk
    readable: dict[tuple[int, int], Matter] = {}
    if existing:
        reference_of = {pk: reference for reference, pk in existing.items()}
        for matter in (
            Matter.objects.visible_to(user)
            .filter(pk__in=list(reference_of))
            .select_related("owner", "stage")
            .prefetch_related("legal_instruments")
        ):
            readable[reference_of[matter.pk]] = matter
    return SourceAccess(
        existing=frozenset(existing),
        readable=readable,
        sees_unmatched=scope_for_user(user).sees_all_restricted,
    )


def match_rows(rows: list[SourceRow], access: SourceAccess) -> tuple[list[RowReconciliation], int]:
    """The rows this reader may see, classified, and how many were withheld."""
    groups: dict[str, list[SourceRow]] = defaultdict(list)
    for row in rows:
        groups[row.key].append(row)

    people: KnownPeople | None = None
    hidden = 0
    result: list[RowReconciliation] = []
    for key in sorted(groups):
        group = groups[key]
        if not access.may_see(key):
            hidden += len(group)
            continue
        reference = parse_reference(key) if key else None
        for row in group:
            if reference is None:
                result.append(RowReconciliation(row, MatchClass.UNMATCHED, MatchReason.NO_KEY))
            elif len(group) > 1:
                result.append(
                    RowReconciliation(row, MatchClass.AMBIGUOUS, MatchReason.DUPLICATE_SOURCE_KEY)
                )
            elif reference not in access.readable:
                # Reached only when no Matter has this reference at all: a
                # Matter the reader cannot read was withheld by `may_see`.
                result.append(RowReconciliation(row, MatchClass.UNMATCHED, MatchReason.NO_MATTER))
            else:
                matter = access.readable[reference]
                if people is None:
                    people = KnownPeople.load()
                result.append(
                    RowReconciliation(
                        row,
                        MatchClass.MATCHED,
                        MatchReason.REFERENCE,
                        matter,
                        tuple(_compare(m, row.values, matter, people) for m in COMPARED_FIELDS),
                    )
                )
    return result, hidden


def redact_diff(diff: SnapshotDiff, access: SourceAccess) -> tuple[SnapshotDiff, int]:
    """The changes this reader may see, and how many were withheld."""
    kept = SnapshotDiff(unkeyed=diff.unkeyed)
    hidden = 0
    for source, target in (
        (diff.added, kept.added),
        (diff.removed, kept.removed),
        (diff.changed, kept.changed),
    ):
        for change in source:
            if access.may_see(change.key):
                target.append(change)
            else:
                hidden += 1
    return kept, hidden


# ---------------------------------------------------------------------------
# The report
# ---------------------------------------------------------------------------


@dataclass
class Coverage:
    tables_present: dict[str, int]
    tables_absent: list[str]
    unmapped_live_tables: tuple[str, ...]
    uncatalogued_fields: dict[str, list[str]]
    dataset_boundary: str
    previous_boundary: str
    unkeyed_rows: int
    outcome_counts: dict[str, int]
    unmatched_withheld: bool = True
    catalogue_status: str = catalogue.CATALOGUE_STATUS_ET
    visibility_note: str = (
        "Lovable'i rea sisu näidatakse ainult siis, kui sa tohid näha teemat, millele "
        "rea NR viitab. Rida, millele ei vasta ühtki teemat, kuvatakse ainult siis, kui "
        "sinu õigused katavad kõik teemad (nt hädaligipääsu ajal). Varjatud read on ainult "
        "kokku loetud — ilma NR-i, "
        "pealkirja ja väljadeta."
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
    #: Current rows withheld from this reader, and withheld snapshot changes.
    hidden_rows: int = 0
    hidden_changes: int = 0

    @property
    def hidden_rows_message(self) -> str:
        return hidden_message(self.hidden_rows) if self.hidden_rows else ""

    @property
    def hidden_changes_message(self) -> str:
        return hidden_message(self.hidden_changes) if self.hidden_changes else ""

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
    before_rows: list[SourceRow] = []
    if previous is None:
        previous_boundary = "Esimene hetktõmmis: varasemaga võrrelda ei ole."
        diff = SnapshotDiff()
        diff.unkeyed = _keyed(current.rows)[1]
    else:
        before = operative_rows(previous)
        before_rows = before.rows
        if before.boundary:
            previous_boundary = f"Eelmine hetktõmmis: {before.boundary}"
        diff = diff_rows(before.rows, current.rows)

    access = source_access([row.key for row in (*current.rows, *before_rows)], user)
    rows, hidden_rows = match_rows(current.rows, access)
    diff, hidden_changes = redact_diff(diff, access)

    outcome_counts: dict[str, int] = defaultdict(int)
    for row in rows:
        for comparison in row.comparisons:
            if comparison.mapping_class == MappingClass.COMPARABLE:
                outcome_counts[comparison.outcome] += 1

    present = dict(sorted(snapshot.row_counts.items()))
    coverage = Coverage(
        tables_present=present,
        tables_absent=sorted(set(catalogue.TABLES) - set(present)),
        unmapped_live_tables=tuple(catalogue.UNMAPPED_LIVE_TABLES),
        unmatched_withheld=not access.sees_unmatched,
        uncatalogued_fields=dict(snapshot.uncatalogued_fields),
        dataset_boundary=current.boundary,
        previous_boundary=previous_boundary,
        unkeyed_rows=diff.unkeyed,
        outcome_counts={
            Outcome.LABELS[k]: v for k, v in sorted(outcome_counts.items()) if k != Outcome.SAME
        },
    )
    return ReconciliationReport(
        snapshot,
        previous,
        rows,
        diff,
        coverage,
        hidden_rows=hidden_rows,
        hidden_changes=hidden_changes,
    )
