"""How much of the register's counterparty column the reviewed baseline resolves.

A diagnostic, and only that. It reads imported register rows, asks which of them
name an institution the reviewed public manifest actually contains, and reports
the arithmetic. It writes nothing — not a Matter sender, not an addressee, not a
ChangeEvent — because the decision this measurement exists to inform has not
been taken yet: whether historic counterparty strings should become canonical
relationships at all, and under what audit semantics.

Answering that before measuring is how a reference-data change turns into a
silent rewrite of fifteen years of filing.

**The era contract decides direction, and nothing else may.** Between 2019 and
2020 column G stopped meaning ``KELLELT`` and started meaning ``KELLELE`` — who
sent it became who it was sent to. The two are not interchangeable and are never
summed here: a 2014 ministry is a *sender*, a 2021 ministry is an *addressee*,
and a report that merged them would describe a correspondence pattern that never
existed. The direction is read from the reviewed contract for that year's sheet
(``docs/data-contracts/``), never inferred from the header text and never from
the year.

**It matches against the manifest, not the database.** The question is what the
reviewed baseline *would* unlock, and production currently holds zero
organisations — so measuring against the database would return zero and say
nothing. Canonical names and reviewed aliases from
``app.organisations.reference_data`` are the comparison set, and the comparison
is normalised-exact, the same one everything else in the product uses. No
similarity scoring, no substrings, no splitting.

**A compound cell stays one unresolved value.** ``Rahandusministeerium ja
Justiitsministeerium`` does not become two matches merely because a Matter can
now hold several senders. Whether that cell means two institutions, or one
institution and a copy recipient, is a reading of the source that a person has
to make; guessing it here would manufacture relationships from punctuation.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from app.core.text import normalize_for_matching

#: The whole cell resolves to exactly one reviewed canonical name.
EXACT_CANONICAL = "EXACT_CANONICAL"
#: The whole cell resolves to exactly one reviewed alias.
EXACT_ALIAS = "EXACT_ALIAS"
#: The column existed for that year and the cell was empty.
BLANK = "BLANK"
#: The cell resolves to more than one reviewed institution. Never guessed.
AMBIGUOUS = "AMBIGUOUS"
#: No reviewed institution carries this name. Includes every compound value.
UNMATCHED = "UNMATCHED"

CLASSIFICATIONS: tuple[str, ...] = (
    EXACT_CANONICAL,
    EXACT_ALIAS,
    BLANK,
    AMBIGUOUS,
    UNMATCHED,
)

#: Directions, as the era contracts name them. ``source`` is ``KELLELT``,
#: ``addressee`` is ``KELLELE``.
SOURCE = "source"
ADDRESSEE = "addressee"


@dataclass
class CoverageReport:
    snapshot_sha256: str
    references_read: int
    #: Rows whose sheet has no reviewed contract, or whose contract describes no
    #: counterparty column at all. Reported rather than silently dropped: "the
    #: 2014 sheet had no such column" and "the 2014 cell was empty" are different
    #: facts and collapsing them would overstate coverage.
    rows_without_a_counterparty_column: int = 0
    #: (era, direction, classification) -> count
    counts: Counter[tuple[str, str, str]] = field(default_factory=Counter)
    #: normalised unresolved value -> (one raw spelling, occurrences, matters)
    unresolved: dict[str, tuple[str, int, set[Any]]] = field(default_factory=dict)

    # -- aggregates ---------------------------------------------------------

    def total(self, classification: str) -> int:
        return sum(count for (_, _, cls), count in self.counts.items() if cls == classification)

    def by_direction(self, direction: str) -> int:
        return sum(count for (_, d, _), count in self.counts.items() if d == direction)

    @property
    def eras(self) -> tuple[str, ...]:
        return tuple(sorted({era for era, _, _ in self.counts}))

    @property
    def distinct_unresolved(self) -> int:
        return len(self.unresolved)

    @property
    def matters_with_unresolved(self) -> int:
        matters: set[Any] = set()
        for _, _, ids in self.unresolved.values():
            matters |= ids
        return len(matters)

    @property
    def classified(self) -> int:
        return sum(self.counts.values())

    def summary(self) -> dict[str, Any]:
        """Aggregates only. Never the values themselves.

        Raw counterparty strings are register content, and an unresolved one is
        usually the *least* standard spelling in the file — the sort of cell
        that names a person or a company. It stays out of terminal output, out
        of JSON, and out of anything that ends up in a pull request; ``--output``
        is the one way to see it, and that writes a file an operator has chosen.
        """
        return {
            "snapshot_sha256": self.snapshot_sha256,
            "references_read": self.references_read,
            "rows_without_a_counterparty_column": self.rows_without_a_counterparty_column,
            "classified": self.classified,
            "by_era": {
                era: {
                    direction: {
                        cls: self.counts[(era, direction, cls)]
                        for cls in CLASSIFICATIONS
                        if self.counts[(era, direction, cls)]
                    }
                    for direction in (SOURCE, ADDRESSEE)
                    if any(self.counts[(era, direction, cls)] for cls in CLASSIFICATIONS)
                }
                for era in self.eras
            },
            "totals": {cls: self.total(cls) for cls in CLASSIFICATIONS},
            "distinct_unresolved_values": self.distinct_unresolved,
            "matters_with_unresolved_values": self.matters_with_unresolved,
            "matter_relationships_written": 0,
        }

    def unresolved_rows(self) -> list[dict[str, Any]]:
        """The protected operator artifact. Human-review material, never printed."""
        return sorted(
            (
                {
                    "value": raw,
                    "occurrences": occurrences,
                    "matters": len(matters),
                }
                for raw, occurrences, matters in self.unresolved.values()
            ),
            key=lambda row: (-int(row["occurrences"]), str(row["value"])),
        )


def _reference_index() -> tuple[dict[str, set[str]], dict[str, set[str]]]:
    """Normalised canonical names and reviewed aliases, from the manifest.

    Values are *sets* of names so that a normalised form claimed by two reviewed
    entries reports as ambiguous rather than resolving to whichever was iterated
    last. The manifest should never contain such a pair — `reference_data verify`
    fails if it does — and this is the second place that would notice.
    """
    from app.organisations.reference_data import PUBLIC_REFERENCE_ORGANISATIONS

    canonical: dict[str, set[str]] = {}
    aliases: dict[str, set[str]] = {}
    for entry in PUBLIC_REFERENCE_ORGANISATIONS:
        canonical.setdefault(normalize_for_matching(entry.name), set()).add(entry.name)
        for alias in entry.aliases:
            aliases.setdefault(normalize_for_matching(alias), set()).add(entry.name)
    return canonical, aliases


def classify(
    raw: str,
    canonical: dict[str, set[str]],
    aliases: dict[str, set[str]],
) -> str:
    """One whole cell, against the reviewed set. Never split, never scored."""
    normalized = normalize_for_matching(raw)
    if not normalized:
        return BLANK
    hits = canonical.get(normalized)
    if hits:
        return EXACT_CANONICAL if len(hits) == 1 else AMBIGUOUS
    hits = aliases.get(normalized)
    if hits:
        return EXACT_ALIAS if len(hits) == 1 else AMBIGUOUS
    return UNMATCHED


def build_coverage_report(*, snapshot_sha256: str = "") -> CoverageReport:
    """Read one register snapshot and measure it. Writes nothing.

    ``snapshot_sha256`` narrows the read to a single imported register, for the
    reason `select_register_snapshot` documents: `MatterSourceReference` is
    append-only evidence, so a database that has been imported twice holds two
    registers, and measuring across both counts most Matters twice.
    """
    from app.legacy_import.models import MatterSourceReference
    from app.legacy_import.parser import SOURCE_SYSTEM
    from app.legacy_import.source_cells import contracts_by_sheet

    contracts = contracts_by_sheet()
    canonical, aliases = _reference_index()

    references = MatterSourceReference.objects.filter(source_system=SOURCE_SYSTEM)
    if snapshot_sha256:
        references = references.filter(source_snapshot_sha256=snapshot_sha256)

    report = CoverageReport(snapshot_sha256=snapshot_sha256, references_read=0)

    for reference in references.values("matter_id", "source_sheet", "source_row_raw").iterator():
        report.references_read += 1
        contract = contracts.get(reference["source_sheet"])
        if contract is None:
            report.rows_without_a_counterparty_column += 1
            continue

        # The contract names the column *and* its direction. Both are read; the
        # direction is never derived from the year or from the header text.
        column = contract.column_for("source_organisation") or contract.column_for(
            "addressee_organisation"
        )
        if column is None or column.direction not in (SOURCE, ADDRESSEE):
            report.rows_without_a_counterparty_column += 1
            continue

        raw_row = reference["source_row_raw"]
        raw = str((raw_row or {}).get(column.letter, "") or "") if isinstance(raw_row, dict) else ""
        classification = classify(raw, canonical, aliases)
        report.counts[(contract.era, column.direction, classification)] += 1

        if classification in (UNMATCHED, AMBIGUOUS):
            key = normalize_for_matching(raw)
            spelling, occurrences, matters = report.unresolved.get(key, (raw.strip(), 0, set()))
            matters.add(reference["matter_id"])
            report.unresolved[key] = (spelling, occurrences + 1, matters)

    return report
