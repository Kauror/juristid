"""What changed in the register since the snapshot production was built from.

Excel stays in operational use while this system is being polished and judged.
That is a deliberate parallel run, not a sync problem, and the distinction
matters: nothing here writes, polls, schedules or reconciles. It answers one
question, on demand, for an operator about to decide whether a newer workbook
carries work that needs to cross the boundary once.

**No permanent synchronisation is built or implied.** Reading a workbook and
saying how it differs from what production holds is a report. A tool that also
applied the difference would be a bridge, and a bridge between a spreadsheet
several people edit and a system of record is the thing the whole cutover exists
to remove.

The baseline is production itself, not a second file
----------------------------------------------------
The obvious design compares two workbooks. The useful one compares a workbook
against ``MatterSourceReference.source_row_raw`` — the immutable per-cell
provenance the approved snapshot was catalogued into — because that is what
production actually believes, and because the approved file will not always be
on the machine running the check. Comparing files would answer "did somebody
edit the spreadsheet"; comparing against provenance answers "does production
disagree with the register", which is the question at cutover.

Two things are read and kept apart, because conflating them is how a delta
audit silently overwrites somebody's work:

* the **register delta** — cells that differ between the workbook and the
  catalogued source row;
* **native writes since the snapshot** — ``ChangeEvent`` rows recorded by a
  person after the catalogue ran. Where both touch one Matter the row is
  reported as ``DUAL_WRITE_CONFLICT`` and nothing is resolved automatically.

Determinism
-----------
Every collection is sorted before it is emitted, so two runs over one workbook
produce byte-identical output and a diff of two reports is a diff of the
register. Nothing here reads the clock except to stamp "when the report ran",
which the caller supplies.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from app.legacy_import.contracts import EraContract, load_contracts
from app.legacy_import.current_state import CurrentRegisterState, RegisterCurrency
from app.legacy_import.models import MatterSourceReference
from app.legacy_import.parser import RegisterWorkbook, SourceRow
from app.legacy_import.register_semantics import (
    detect_continuation,
    has_send_date,
    is_real_row,
    is_terminal_status,
)

#: Bumped when what this report *means* changes, so a stored report can always
#: be read back under the rules that produced it.
DELTA_VERSION = "1.0"

#: Canonical fields the report names explicitly, in the order a reader wants
#: them. Everything else a contract describes is still compared and still
#: reported — under its own canonical name — but these are the ones a cutover
#: decision usually turns on.
HEADLINE_FIELDS: tuple[str, ...] = (
    "matter_reference",
    "title",
    "legal_instrument",
    "received_date",
    "response_deadline",
    "opinion_sent_date",
    "source_organisation",
    "addressee_organisation",
    "owner_name",
    "legacy_status",
    "next_action_text",
)


class DeltaRefused(Exception):
    """The report will not run, rather than run on something unverified."""


# ---------------------------------------------------------------------------
# Comparison normalisation
# ---------------------------------------------------------------------------


def normalise(value: str | None) -> str:
    """The safe half of comparison normalisation, and no more.

    Leading and trailing whitespace and line endings only. Everything a reviewer
    might want normalised away — punctuation inside an instruction, a register
    reference, a person's name, a status word, a negation such as *ei saatnud* —
    is left exactly as the source wrote it, because each of those can change
    what a row means and a comparison that hid one would hide the finding the
    report exists to produce.
    """
    text = (value or "").replace("\r\n", "\n").replace("\r", "\n")
    return text.strip()


# ---------------------------------------------------------------------------
# Result shapes
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FieldDelta:
    sheet: str
    reference: str
    title: str
    column: str
    canonical_field: str
    header: str
    baseline: str
    workbook: str
    #: Whether the literal stored text differs at all.
    raw_differs: bool
    #: Whether it still differs after :func:`normalise`.
    semantic_differs: bool


@dataclass(frozen=True)
class RowDelta:
    sheet: str
    reference: str
    title: str
    status: str  # IDENTICAL | CHANGED | NEW | REMOVED
    row_number: int | None = None
    fields: tuple[FieldDelta, ...] = ()


@dataclass(frozen=True)
class HyperlinkDelta:
    sheet: str
    reference: str
    column: str
    baseline: str
    workbook: str


@dataclass(frozen=True)
class ContinuationDelta:
    sheet: str
    reference: str
    baseline_verdict: str
    baseline_target: str
    workbook_verdict: str
    workbook_target: str


@dataclass(frozen=True)
class NativeWrite:
    reference: str
    event_type: str
    occurred_at: str
    actor: str


@dataclass(frozen=True)
class DualWriteConflict:
    reference: str
    changed_fields: tuple[str, ...]
    native_events: tuple[NativeWrite, ...]


@dataclass
class PortfolioImpact:
    """What the workbook would make current, under the existing rules only.

    Recomputed with :mod:`app.legacy_import.register_semantics`, never with a
    second implementation: a delta report that disagreed with the cutover about
    what "current" means would be worse than no report.
    """

    current: int = 0
    by_sheet: dict[str, int] = field(default_factory=dict)
    drafting: int = 0
    retire: int = 0
    supersede: int = 0
    review: int = 0
    production_current: int = 0
    production_by_sheet: dict[str, int] = field(default_factory=dict)
    production_drafting: int = 0
    would_activate: list[str] = field(default_factory=list)
    would_retire: list[str] = field(default_factory=list)
    would_supersede: list[str] = field(default_factory=list)
    needs_review: list[str] = field(default_factory=list)

    @property
    def totals_match(self) -> bool:
        return self.current == self.production_current and self.drafting == self.production_drafting

    @property
    def identities_match(self) -> bool:
        """Whether the *same* Matters are current, not merely as many.

        Asked separately from :attr:`totals_match` on purpose. Equal counts with
        a swapped membership is the failure a headline figure cannot show, and
        it is the one a cutover most needs to be told about.
        """
        return not (self.would_activate or self.would_retire or self.would_supersede)


@dataclass
class DeltaReport:
    delta_version: str = DELTA_VERSION
    workbook_path: str = ""
    workbook_name: str = ""
    workbook_sha256: str = ""
    workbook_bytes: int = 0
    expected_sha256: str = ""
    sha256_verified: bool = False
    baseline_snapshots: list[str] = field(default_factory=list)
    generated_at: str = ""
    scope_years: list[int] = field(default_factory=list)

    sheets: list[dict[str, Any]] = field(default_factory=list)
    identical: int = 0
    changed: int = 0
    new: int = 0
    removed: int = 0

    rows: list[RowDelta] = field(default_factory=list)
    hyperlinks: list[HyperlinkDelta] = field(default_factory=list)
    continuations: list[ContinuationDelta] = field(default_factory=list)
    portfolio: PortfolioImpact = field(default_factory=PortfolioImpact)
    native_writes: list[NativeWrite] = field(default_factory=list)
    conflicts: list[DualWriteConflict] = field(default_factory=list)
    findings: list[str] = field(default_factory=list)

    @property
    def changed_rows(self) -> list[RowDelta]:
        return [row for row in self.rows if row.status == "CHANGED"]

    @property
    def semantic_field_count(self) -> int:
        return sum(1 for row in self.rows for delta in row.fields if delta.semantic_differs)

    def as_dict(self) -> dict[str, Any]:
        """A JSON-ready mapping. Sorted throughout; see the module docstring."""
        return {
            "delta_version": self.delta_version,
            "workbook": {
                "path": self.workbook_path,
                "name": self.workbook_name,
                "sha256": self.workbook_sha256,
                "bytes": self.workbook_bytes,
                "expected_sha256": self.expected_sha256,
                "sha256_verified": self.sha256_verified,
            },
            "baseline_snapshots": self.baseline_snapshots,
            "generated_at": self.generated_at,
            "scope_years": self.scope_years,
            "sheets": self.sheets,
            "counts": {
                "identical": self.identical,
                "changed": self.changed,
                "new": self.new,
                "removed": self.removed,
                "semantic_fields": self.semantic_field_count,
            },
            "rows": [asdict(row) for row in self.rows if row.status != "IDENTICAL"],
            "hyperlinks": [asdict(item) for item in self.hyperlinks],
            "continuations": [asdict(item) for item in self.continuations],
            "portfolio": {
                **asdict(self.portfolio),
                "totals_match": self.portfolio.totals_match,
                "identities_match": self.portfolio.identities_match,
            },
            "native_writes": [asdict(item) for item in self.native_writes],
            "dual_write_conflicts": [asdict(item) for item in self.conflicts],
            "findings": self.findings,
        }


# ---------------------------------------------------------------------------
# Baseline
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class BaselineRow:
    reference: str
    sheet: str
    row_number: int | None
    cells: dict[str, str]
    hyperlinks: dict[str, str]
    snapshot: str
    matter_id: Any
    matter_title: str


def _reference_of(raw: dict[str, str], contract: EraContract) -> str:
    column = contract.column_for("matter_reference")
    if column is None:
        return ""
    return normalise(raw.get(column.letter, ""))


def load_baseline(sheets: set[str]) -> tuple[dict[str, BaselineRow], list[str], list[str]]:
    """The catalogued source rows production is standing on, keyed by NR.

    The *latest* reference per Matter and sheet wins where several exist: a
    Matter catalogued from two workbooks carries one immutable row per
    catalogue, and the newest is the snapshot production last reconciled
    against. Older ones stay untouched — this function reads provenance, it does
    not tidy it.

    Duplicate references across different Matters are not resolved. They are
    returned in the third value — the ambiguity list — and the caller refuses on
    that list rather than on the wording of a finding, because a row whose
    identity is ambiguous cannot be compared without guessing which Matter it
    belongs to.
    """
    findings: list[str] = []
    ambiguous: list[str] = []
    contracts = {contract.sheet: contract for contract in load_contracts().values()}

    by_reference: dict[str, BaselineRow] = {}
    seen_matter: dict[str, Any] = {}

    references = (
        MatterSourceReference.objects.filter(source_sheet__in=sorted(sheets))
        .select_related("matter")
        .order_by("source_sheet", "source_row_number", "created_at")
    )
    for reference in references.iterator():
        contract = contracts.get(reference.source_sheet)
        if contract is None:
            continue
        raw = reference.source_row_raw or {}
        cells = {key: normalise(value) for key, value in raw.items() if isinstance(value, str)}
        number = _reference_of(cells, contract)
        if not number:
            continue

        previous_matter = seen_matter.get(number)
        if previous_matter is not None and previous_matter != reference.matter_id:
            ambiguous.append(f"Viide {number} osutab kahele erinevale teemale allikakirjetes.")
            continue
        seen_matter[number] = reference.matter_id

        # Later catalogue of the same row replaces the earlier one; the ordering
        # above makes "later" deterministic rather than whatever the planner
        # returned first.
        by_reference[number] = BaselineRow(
            reference=number,
            sheet=reference.source_sheet,
            row_number=reference.source_row_number,
            cells=cells,
            hyperlinks={"B": normalise(reference.onenote_url)} if reference.onenote_url else {},
            snapshot=reference.source_file_name,
            matter_id=reference.matter_id,
            matter_title=reference.matter.title if reference.matter_id else "",
        )

    return by_reference, sorted(set(findings)), sorted(set(ambiguous))


# ---------------------------------------------------------------------------
# The report
# ---------------------------------------------------------------------------


def _workbook_rows(
    workbook: RegisterWorkbook, contract: EraContract
) -> tuple[dict[str, SourceRow], list[str], list[str]]:
    """Business rows of one sheet keyed by NR, plus findings and ambiguities.

    The two lists are separate because they have different consequences. A
    finding is worth printing; an ambiguity stops the comparison, and the caller
    must be able to tell them apart without reading Estonian prose.
    """
    rows: dict[str, SourceRow] = {}
    findings: list[str] = []
    ambiguous: list[str] = []
    title_column = contract.column_for("title")
    reference_column = contract.column_for("matter_reference")
    if title_column is None or reference_column is None:
        return rows, findings, ambiguous

    for row in workbook.rows(contract):
        if row.is_blank:
            continue
        title = row.text(title_column.letter)
        reference = normalise(row.text(reference_column.letter))
        if not is_real_row(reference, title):
            continue
        if not reference:
            findings.append(
                f"{contract.sheet}: real {row.row_number} on pealkiri, kuid puudub NR — "
                "rida ei ole üheselt tuvastatav."
            )
            continue
        if reference in rows:
            ambiguous.append(
                f"{contract.sheet}: viide {reference} esineb kaks korda "
                f"(read {rows[reference].row_number} ja {row.row_number})."
            )
            continue
        rows[reference] = row
    return rows, findings, ambiguous


def build_report(
    *,
    workbook_path: str | Path,
    expected_sha256: str = "",
    scope_years: tuple[int, ...] = (2025, 2026),
    generated_at: datetime | None = None,
) -> DeltaReport:
    """Compare one workbook against what production holds. Reads only.

    ``scope_years`` is the span the current portfolio is recomputed over, and it
    matches the reviewed cutover's own scope: the department stopped recording
    HETKESEIS before 2025, so a blank status on a 2014 row is not a statement
    that the work is live. Every other sheet is still compared cell by cell — it
    is only the portfolio arithmetic that is scoped.
    """
    report = DeltaReport()
    report.generated_at = generated_at.isoformat() if generated_at else ""
    report.scope_years = sorted(scope_years)
    report.expected_sha256 = (expected_sha256 or "").strip().lower()

    path = Path(workbook_path)
    with RegisterWorkbook(path) as workbook:
        report.workbook_path = str(path)
        report.workbook_name = path.name
        report.workbook_sha256 = workbook.sha256
        report.workbook_bytes = workbook.byte_size
        report.sha256_verified = bool(
            report.expected_sha256 and report.expected_sha256 == workbook.sha256
        )
        if report.expected_sha256 and not report.sha256_verified:
            raise DeltaRefused(
                "Töövihiku SHA-256 ei ole oodatud. "
                f"Oodatud {report.expected_sha256}, tegelik {workbook.sha256}."
            )

        contracts = load_contracts()
        by_sheet = {contract.sheet: contract for contract in contracts.values()}
        present = [name for name in workbook.sheet_names if name in by_sheet]

        baseline, baseline_findings, ambiguous = load_baseline(set(present))
        report.findings.extend(baseline_findings)
        report.baseline_snapshots = sorted(
            {row.snapshot for row in baseline.values() if row.snapshot}
        )

        current_rows: dict[str, SourceRow] = {}
        current_contracts: dict[str, EraContract] = {}

        for sheet_name in present:
            contract = by_sheet[sheet_name]
            sheet_rows, findings, sheet_ambiguous = _workbook_rows(workbook, contract)
            report.findings.extend(findings)
            ambiguous.extend(sheet_ambiguous)
            baseline_here = {
                reference: row for reference, row in baseline.items() if row.sheet == sheet_name
            }
            report.sheets.append(
                {
                    "sheet": sheet_name,
                    "baseline_rows": len(baseline_here),
                    "workbook_rows": len(sheet_rows),
                    "delta": len(sheet_rows) - len(baseline_here),
                }
            )
            for reference, row in sheet_rows.items():
                current_rows[reference] = row
                current_contracts[reference] = contract

            _compare_sheet(report, contract, sheet_rows, baseline_here)

    # Refused on a flag the detectors set, never by matching their wording. A
    # message somebody rephrases must not quietly turn a refusal into a report.
    if ambiguous:
        raise DeltaRefused(
            "Ridade identiteet ei ole üheselt määratud; võrdlust ei tehta. "
            + "; ".join(sorted(ambiguous))
        )

    _recompute_portfolio(report, current_rows, current_contracts)
    _collect_native_writes(report, baseline)
    return report


def _compare_sheet(
    report: DeltaReport,
    contract: EraContract,
    sheet_rows: dict[str, SourceRow],
    baseline_here: dict[str, BaselineRow],
) -> None:
    """Classify every row of one sheet. Sorted, so output is deterministic."""
    title_column = contract.column_for("title")
    columns = {column.letter: column for column in contract.columns}

    for reference in sorted(set(sheet_rows) | set(baseline_here)):
        row = sheet_rows.get(reference)
        base = baseline_here.get(reference)
        title = row.text(title_column.letter) if row and title_column else ""

        if row is None:
            report.removed += 1
            report.rows.append(
                RowDelta(
                    sheet=contract.sheet,
                    reference=reference,
                    title=base.matter_title if base else "",
                    status="REMOVED",
                    row_number=base.row_number if base else None,
                )
            )
            continue
        if base is None:
            report.new += 1
            report.rows.append(
                RowDelta(
                    sheet=contract.sheet,
                    reference=reference,
                    title=title,
                    status="NEW",
                    row_number=row.row_number,
                )
            )
            continue

        deltas: list[FieldDelta] = []
        for letter in sorted(columns):
            column = columns[letter]
            workbook_value = row.text(letter)
            baseline_value = base.cells.get(letter, "")
            raw_differs = workbook_value != baseline_value
            semantic_differs = normalise(workbook_value) != normalise(baseline_value)
            if not raw_differs:
                continue
            deltas.append(
                FieldDelta(
                    sheet=contract.sheet,
                    reference=reference,
                    title=title,
                    column=letter,
                    canonical_field=column.canonical_field,
                    header=column.header,
                    baseline=baseline_value,
                    workbook=workbook_value,
                    raw_differs=raw_differs,
                    semantic_differs=semantic_differs,
                )
            )

        _compare_hyperlinks(report, contract, reference, row, base)
        _compare_continuation(report, contract, reference, row, base)

        if deltas:
            report.changed += 1
            report.rows.append(
                RowDelta(
                    sheet=contract.sheet,
                    reference=reference,
                    title=title,
                    status="CHANGED",
                    row_number=row.row_number,
                    fields=tuple(deltas),
                )
            )
        else:
            report.identical += 1


def _compare_hyperlinks(
    report: DeltaReport,
    contract: EraContract,
    reference: str,
    row: SourceRow,
    base: BaselineRow,
) -> None:
    """The TEEMA link, compared by target rather than by visible text.

    Only the title column is compared, because that is the only hyperlink the
    catalogue preserved as a field of its own (``onenote_url``). A row whose
    baseline never recorded one is not reported as *link removed* — the absence
    is in the provenance, not necessarily in the old workbook.
    """
    title_column = contract.column_for("title")
    if title_column is None:
        return
    workbook_target = normalise(row.hyperlinks().get(title_column.letter, ""))
    baseline_target = normalise(base.hyperlinks.get("B", ""))
    if not baseline_target and not workbook_target:
        return
    if baseline_target and workbook_target and baseline_target != workbook_target:
        report.hyperlinks.append(
            HyperlinkDelta(
                sheet=contract.sheet,
                reference=reference,
                column=title_column.letter,
                baseline=baseline_target,
                workbook=workbook_target,
            )
        )
    elif baseline_target and not workbook_target:
        report.hyperlinks.append(
            HyperlinkDelta(
                sheet=contract.sheet,
                reference=reference,
                column=title_column.letter,
                baseline=baseline_target,
                workbook="",
            )
        )


def _compare_continuation(
    report: DeltaReport,
    contract: EraContract,
    reference: str,
    row: SourceRow,
    base: BaselineRow,
) -> None:
    """Explicit `Jätkub teema … all` relationships, added, removed or retargeted.

    Detected with the shared :func:`detect_continuation`, never with a second
    regular expression, so this report cannot disagree with the cutover about
    what counts as a continuation. Nothing is inferred from title similarity.
    """
    column = contract.column_for("next_action_text")
    if column is None:
        return
    workbook_verdict = detect_continuation(row.text(column.letter))
    baseline_verdict = detect_continuation(base.cells.get(column.letter, ""))
    if (
        workbook_verdict.verdict == baseline_verdict.verdict
        and workbook_verdict.reference == baseline_verdict.reference
    ):
        return
    report.continuations.append(
        ContinuationDelta(
            sheet=contract.sheet,
            reference=reference,
            baseline_verdict=baseline_verdict.verdict,
            baseline_target=baseline_verdict.reference,
            workbook_verdict=workbook_verdict.verdict,
            workbook_target=workbook_verdict.reference,
        )
    )


def _recompute_portfolio(
    report: DeltaReport,
    rows: dict[str, SourceRow],
    contracts: dict[str, EraContract],
) -> None:
    """What the workbook says is current, beside what production holds."""
    impact = report.portfolio
    workbook_current: set[str] = set()

    for reference in sorted(rows):
        contract = contracts[reference]
        if contract.year not in set(report.scope_years):
            continue
        row = rows[reference]
        status_column = contract.column_for("legacy_status")
        next_column = contract.column_for("next_action_text")
        sent_column = contract.column_for("opinion_sent_date")

        status = row.text(status_column.letter) if status_column else ""
        if is_terminal_status(status):
            impact.retire += 1
            continue

        continuation = detect_continuation(row.text(next_column.letter) if next_column else "")
        if continuation.supersedes:
            impact.supersede += 1
            continue
        if continuation.needs_review:
            impact.review += 1
            impact.needs_review.append(reference)

        workbook_current.add(reference)
        impact.current += 1
        impact.by_sheet[contract.sheet] = impact.by_sheet.get(contract.sheet, 0) + 1
        if not has_send_date(row.text(sent_column.letter) if sent_column else ""):
            impact.drafting += 1

    states = CurrentRegisterState.objects.select_related("matter").filter(
        source_sheet__in=sorted({contract.sheet for contract in contracts.values()})
    )
    production_current: set[str] = set()
    for state in states:
        matter = state.matter
        reference = f"{matter.reference_year}_{matter.reference_number}"
        if state.currency != RegisterCurrency.CURRENT:
            continue
        production_current.add(reference)
        impact.production_current += 1
        impact.production_by_sheet[state.source_sheet] = (
            impact.production_by_sheet.get(state.source_sheet, 0) + 1
        )
        if not state.opinion_sent_recorded:
            impact.production_drafting += 1

    # The set difference, not the count difference. Equal totals with a swapped
    # membership is exactly the failure a headline figure cannot show.
    impact.would_activate = sorted(workbook_current - production_current)
    impact.would_retire = sorted(production_current - workbook_current)
    impact.would_supersede = sorted(impact.needs_review)
    impact.needs_review = sorted(impact.needs_review)


def _collect_native_writes(report: DeltaReport, baseline: dict[str, BaselineRow]) -> None:
    """Native activity after the catalogue, and where it meets a changed row.

    The cut-off is the newest catalogued source reference rather than a date
    somebody typed: everything before it is the import's own work, everything
    after it is a person's. Nothing is resolved — a conflict is reported and
    left for a human, because native work must never be silently overwritten by
    a spreadsheet.
    """
    from app.audit.models import ChangeEvent

    latest = (
        MatterSourceReference.objects.order_by("-created_at")
        .values_list("created_at", flat=True)
        .first()
    )
    if latest is None:
        return

    changed_by_matter: dict[Any, RowDelta] = {}
    for row in report.changed_rows:
        base = baseline.get(row.reference)
        if base is not None:
            changed_by_matter[base.matter_id] = row

    events = (
        ChangeEvent.objects.filter(occurred_at__gt=latest, matter__isnull=False)
        .select_related("matter", "actor")
        .order_by("occurred_at")
    )
    by_matter: dict[Any, list[NativeWrite]] = {}
    for event in events:
        matter = event.matter
        if matter is None:  # pragma: no cover - excluded by the query above
            continue
        actor = event.actor
        write = NativeWrite(
            reference=f"{matter.reference_year}_{matter.reference_number}",
            event_type=event.event_type,
            occurred_at=event.occurred_at.isoformat(),
            # A persona name where there is one. Under the shared gate this is a
            # selected view rather than a verified person, which is what the
            # audit row itself records; nothing more is claimed here.
            actor=(actor.get_full_name() if actor is not None else ""),
        )
        report.native_writes.append(write)
        by_matter.setdefault(matter.pk, []).append(write)

    for matter_id, row in sorted(changed_by_matter.items(), key=lambda pair: pair[1].reference):
        writes = by_matter.get(matter_id)
        if not writes:
            continue
        report.conflicts.append(
            DualWriteConflict(
                reference=row.reference,
                changed_fields=tuple(sorted({delta.canonical_field for delta in row.fields})),
                native_events=tuple(writes),
            )
        )
