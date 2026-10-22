"""Writing the import reports.

Every report this module produces falls into one of three classes, and the split
is physical rather than a matter of care:

**Public aggregate** — ``summary.json`` and ``summary.md``. Written from a
:class:`PublicSummary` and from nothing else: counts, the parser, importer and
contract versions, and a handful of yes/no flags, each under a name this module
allowlists. No file name, no sheet name, no header, no reference number, no
vocabulary term, no cell value — nothing the workbook said. These are the only
files safe to attach to a pull request, upload as a CI artifact, or paste into
a message.

**Operator summary** — ``operator-summary.json`` and ``operator-summary.md``.
The investigative detail a person reconciling the workbook needs: the source
file's name and SHA-256, its sheet names, every structural finding with the
header text it found, duplicate references, the workbook's own vocabulary.
Local only.

**Row-level** — ``rows.csv``, ``anomalies.csv``, ``mapping-gaps.csv`` and
``next-action-candidates.csv``. These reproduce source content because that is
the only way a reviewer can act on them. Local only. Every cell, header
included, goes through `app.core.csv_safety`: these files are opened in Excel,
and a cell the workbook held as ``=HYPERLINK(…)`` would otherwise run there
(csf_1cecc4b5be8fb1a7be4fe125).

The public summary used to be the operator summary under a publishable name: it
carried the workbook's file name, its sheet names, duplicate references, header
cells inside the structural findings, and the vocabulary terms the workbook
used (csf_31caadb347feb9939db2cd4e, docs/adr/0175). Keeping the boundary in a
typed schema — rather than in a reviewer's judgement about which fields of a
dictionary are harmless — is the point: a field nobody allowlisted cannot reach
the public file, however it got into the summary.
"""

from __future__ import annotations

import csv
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.core.csv_safety import csv_safe_row
from app.legacy_import.enums import Anomaly, ProposedRecordMode, RowOutcome

#: Files safe to publish. Anything not named here is assumed to contain source
#: content, and the tests assert that this list stays accurate.
AGGREGATE_REPORTS: tuple[str, ...] = ("summary.json", "summary.md")

#: Source-derived summaries for the operator. Local only.
OPERATOR_REPORTS: tuple[str, ...] = ("operator-summary.json", "operator-summary.md")

ROW_LEVEL_REPORTS: tuple[str, ...] = (
    "rows.csv",
    "anomalies.csv",
    "mapping-gaps.csv",
    "next-action-candidates.csv",
)

#: Every file that stays in ignored local storage.
LOCAL_ONLY_REPORTS: tuple[str, ...] = OPERATOR_REPORTS + ROW_LEVEL_REPORTS

_PUBLIC_BANNER = (
    "Avaldatav koondaruanne: ainult loendid, versioonid ja jah/ei-märgid. "
    "Lähtefaili nimi, lehtede nimed, pealkirjad, viited, sõnavara ja lahtrite "
    "sisu on ainult kohalikes failides operator-summary.* ja *.csv, mida ei "
    "laadita üles, ei lisata Gitti ega saadeta edasi."
)

_LOCAL_ONLY_BANNER = (
    "AINULT KOHALIK. See fail sisaldab lähtefaili nime, lehtede nimesid, "
    "pealkirju, viiteid ja sõnavara. Seda ei laadita üles, ei lisata Gitti ega "
    "saadeta edasi; avaldatav on ainult summary.json ja summary.md."
)


# ---------------------------------------------------------------------------
# The public schema
# ---------------------------------------------------------------------------

#: Stamped on every public summary, so a reader can tell this shape from the
#: old one that still carried source content.
PUBLIC_SUMMARY_SCHEMA = "juristid.legacy-import.public-summary"
PUBLIC_SUMMARY_SCHEMA_VERSION = 1

#: What kind of run produced the summary.
PUBLIC_REPORT_KINDS: frozenset[str] = frozenset({"inspection", "dry-run", "apply"})

#: Every count a public summary may carry. A name outside this set is refused,
#: not dropped: a writer that silently discarded unknown fields would let the
#: next one through under a new name without anybody noticing the schema was
#: being asked to grow.
PUBLIC_COUNTS: frozenset[str] = frozenset(
    {
        # The source file's shape, as numbers.
        "byte_size",
        "sheet_count",
        "year_sheet_count",
        "sheets_without_contract",
        "structure_findings",
        # Offline inspection totals (app/legacy_import/extraction.py `summarize`).
        "rows_below_header",
        "blank_rows",
        "matter_rows",
        "reserved_references",
        "valid_references",
        "hyperlinks",
        "other_hyperlinks",
        "next_action_populated",
        "status_populated",
        "feedback_responded_present",
        "feedback_requested_present",
        "feedback_measured_zero",
        "unknown_column_values",
        "duplicate_references",
        "next_action_candidates",
        "next_action_candidates_deterministic",
        # The workbook's vocabulary sheet against the seeded one, as counts.
        "vocabulary_workbook_labels",
        "vocabulary_known_labels",
        "vocabulary_labels_missing_from_seed",
        "vocabulary_labels_missing_from_workbook",
        "vocabulary_used_labels_not_controlled",
        "vocabulary_labels_with_explanation",
        # A database-aware plan (app/legacy_import/plan_reports.py).
        "rows_considered",
        "review_required",
        "full_candidates",
        "onenote_links_retained",
        "mapping_gap_owners",
        "mapping_gap_organisations",
        "mapping_gap_statuses",
    }
)

#: Yes/no facts about a run that say nothing about what the workbook contains.
PUBLIC_FLAGS: frozenset[str] = frozenset(
    {"accounting_is_complete", "structure_ok", "onenote_content_imported"}
)

#: Breakdowns whose keys are this application's own codes. A key outside the
#: code list is refused: a row's own text can never become a key here.
PUBLIC_CATEGORIES: Mapping[str, frozenset[str]] = {
    "outcomes": frozenset(RowOutcome.values),
    "anomalies": frozenset(Anomaly.values),
    "proposed_record_modes": frozenset(ProposedRecordMode.values),
}

_PUBLIC_FIELDS: frozenset[str] = frozenset(
    {
        "schema",
        "schema_version",
        "report",
        "parser_version",
        "importer_version",
        "contract_versions",
        "counts",
        "categories",
        "flags",
    }
)

#: A version stamp is this application's own: digits, letters and a little
#: punctuation, short. A source string cannot pass for one by accident.
_VERSION = re.compile(r"^[0-9A-Za-z][0-9A-Za-z./_-]{0,39}$")


class PublicSummaryError(ValueError):
    """A public summary was given something its schema does not allow."""


def _count(name: str, value: Any) -> int:
    # `bool` is an `int` to Python and is refused here: a flag belongs in flags.
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise PublicSummaryError(f"Avaldatava kokkuvõtte loend {name!r} ei ole loend.")
    return value


def _version(name: str, value: Any, *, required: bool) -> str:
    if not isinstance(value, str):
        raise PublicSummaryError(f"Avaldatava kokkuvõtte väli {name!r} ei ole versioon.")
    if not value and not required:
        return value
    if not _VERSION.match(value):
        raise PublicSummaryError(f"Avaldatava kokkuvõtte väli {name!r} ei ole versioon.")
    return value


@dataclass(frozen=True)
class PublicSummary:
    """The only thing ``summary.json`` and ``summary.md`` are written from.

    Strict in every direction: an unknown count, flag, category or category
    key is refused rather than dropped, a count must be a non-negative integer,
    a flag a boolean, and a version a short stamp. The mappings are copied and
    sorted on construction, so what is validated is what is written.
    """

    report: str
    parser_version: str
    contract_versions: str
    importer_version: str = ""
    counts: Mapping[str, int] = field(default_factory=dict)
    categories: Mapping[str, Mapping[str, int]] = field(default_factory=dict)
    flags: Mapping[str, bool] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.report, str) or self.report not in PUBLIC_REPORT_KINDS:
            raise PublicSummaryError("Avaldatava kokkuvõtte liik ei ole lubatud.")
        _version("parser_version", self.parser_version, required=True)
        _version("contract_versions", self.contract_versions, required=True)
        _version("importer_version", self.importer_version, required=False)

        if not isinstance(self.counts, Mapping):
            raise PublicSummaryError("Avaldatava kokkuvõtte loendid ei ole tabel.")
        unknown = sorted(set(self.counts) - PUBLIC_COUNTS)
        if unknown:
            raise PublicSummaryError(f"Avaldatav kokkuvõte ei luba loendeid {unknown}.")
        counts = {name: _count(name, self.counts[name]) for name in sorted(self.counts)}

        if not isinstance(self.flags, Mapping):
            raise PublicSummaryError("Avaldatava kokkuvõtte märgid ei ole tabel.")
        unknown = sorted(set(self.flags) - PUBLIC_FLAGS)
        if unknown:
            raise PublicSummaryError(f"Avaldatav kokkuvõte ei luba märke {unknown}.")
        flags: dict[str, bool] = {}
        for name in sorted(self.flags):
            if not isinstance(self.flags[name], bool):
                raise PublicSummaryError(f"Avaldatava kokkuvõtte märk {name!r} ei ole jah/ei.")
            flags[name] = self.flags[name]

        if not isinstance(self.categories, Mapping):
            raise PublicSummaryError("Avaldatava kokkuvõtte jaotused ei ole tabel.")
        unknown = sorted(set(self.categories) - set(PUBLIC_CATEGORIES))
        if unknown:
            raise PublicSummaryError(f"Avaldatav kokkuvõte ei luba jaotusi {unknown}.")
        categories: dict[str, dict[str, int]] = {}
        for group in sorted(self.categories):
            values = self.categories[group]
            if not isinstance(values, Mapping):
                raise PublicSummaryError(f"Avaldatava kokkuvõtte jaotus {group!r} ei ole tabel.")
            # The offending key is not echoed: it is the one thing here that
            # could be source text.
            if set(values) - PUBLIC_CATEGORIES[group]:
                raise PublicSummaryError(
                    f"Avaldatava kokkuvõtte jaotuses {group!r} on võti, mis ei ole rakenduse kood."
                )
            categories[group] = {code: _count(group, values[code]) for code in sorted(values)}

        object.__setattr__(self, "counts", counts)
        object.__setattr__(self, "flags", flags)
        object.__setattr__(self, "categories", categories)

    def to_payload(self) -> dict[str, Any]:
        return {
            "schema": PUBLIC_SUMMARY_SCHEMA,
            "schema_version": PUBLIC_SUMMARY_SCHEMA_VERSION,
            "report": self.report,
            "parser_version": self.parser_version,
            "importer_version": self.importer_version,
            "contract_versions": self.contract_versions,
            "counts": dict(self.counts),
            "categories": {group: dict(values) for group, values in self.categories.items()},
            "flags": dict(self.flags),
        }

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> PublicSummary:
        """Read a payload back, refusing any field the schema does not name."""
        if not isinstance(payload, Mapping):
            raise PublicSummaryError("Avaldatav kokkuvõte ei ole tabel.")
        unknown = sorted(set(payload) - _PUBLIC_FIELDS)
        if unknown:
            raise PublicSummaryError(f"Avaldatav kokkuvõte ei luba välju {unknown}.")
        if payload.get("schema") != PUBLIC_SUMMARY_SCHEMA or (
            payload.get("schema_version") != PUBLIC_SUMMARY_SCHEMA_VERSION
        ):
            raise PublicSummaryError("Avaldatava kokkuvõtte skeem ei ole tuttav.")
        return cls(
            report=payload.get("report", ""),
            parser_version=payload.get("parser_version", ""),
            contract_versions=payload.get("contract_versions", ""),
            importer_version=payload.get("importer_version", ""),
            counts=payload.get("counts", {}),
            categories=payload.get("categories", {}),
            flags=payload.get("flags", {}),
        )

    def markdown(self) -> str:
        """``summary.md``: the same fields as the JSON, and nothing else."""
        lines = [
            "# Registri impordi koondaruanne",
            "",
            f"_{_PUBLIC_BANNER}_",
            "",
            f"- aruanne: {self.report}",
            f"- parseri versioon: {self.parser_version}",
        ]
        if self.importer_version:
            lines.append(f"- importija versioon: {self.importer_version}")
        lines += [f"- lepingute versioon: {self.contract_versions}", "", "## Loendid", ""]
        lines += ["| Näitaja | Arv |", "| --- | ---: |"]
        lines += [f"| {name} | {value} |" for name, value in self.counts.items()]
        for group, values in self.categories.items():
            lines += ["", f"## {group}", "", "| Kood | Ridu |", "| --- | ---: |"]
            lines += [f"| {code} | {value} |" for code, value in values.items()]
        if self.flags:
            lines += ["", "## Märgid", ""]
            lines += [f"- {name}: {'jah' if value else 'ei'}" for name, value in self.flags.items()]
        return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Writing
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class WrittenReports:
    directory: Path
    aggregate: tuple[Path, ...]
    row_level: tuple[Path, ...]
    operator: tuple[Path, ...] = ()

    @property
    def local_only(self) -> tuple[Path, ...]:
        return self.operator + self.row_level

    @property
    def all_paths(self) -> tuple[Path, ...]:
        return self.aggregate + self.operator + self.row_level


def _write_json(path: Path, payload: dict[str, Any]) -> Path:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True, default=str) + "\n",
        encoding="utf-8",
    )
    return path


def _write_csv(path: Path, header: list[str], rows: list[list[Any]]) -> Path:
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        # utf-8-sig because these files are opened in Excel by the people who
        # have to review them, and Excel reads plain UTF-8 as Windows-1252.
        # Every cell through `csv_safe_row`, the header too: the cells are
        # the workbook's own, and Excel is exactly what would run them.
        writer = csv.writer(handle, delimiter=";")
        writer.writerow(csv_safe_row(list(header)))
        writer.writerows(csv_safe_row(list(row)) for row in rows)
    return path


def inspection_markdown(summary: dict[str, Any]) -> str:
    """``operator-summary.md`` for an offline inspection. Local only."""
    source = summary["source"]
    lines = [
        "# Registri impordi ülevaade (ainult kohalik)",
        "",
        f"_{_LOCAL_ONLY_BANNER}_",
        "",
        "## Allikas",
        "",
        f"- fail: `{source['file_name']}`",
        f"- SHA-256: `{source['sha256']}`",
        f"- suurus: {source['byte_size']} baiti",
        f"- parseri versioon: {source['parser_version']}",
        f"- lepingute versioon: {source['contract_versions']}",
        "",
        "## Lehed",
        "",
        "| Leht | Leping | Ridu | Teemaridu | Kehtivaid viiteid | Linke "
        "| Järgmiseks | Seisundeid |",
        "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for sheet in summary["sheets"]:
        counts = sheet.get("counts") or {}
        lines.append(
            "| {} | {} | {} | {} | {} | {} | {} | {} |".format(
                sheet["name"],
                sheet.get("contract_version") or "—",
                counts.get("rows_below_header", 0),
                counts.get("matter_rows", 0),
                counts.get("valid_references", 0),
                counts.get("hyperlinks", 0),
                counts.get("next_action_populated", 0),
                counts.get("status_populated", 0),
            )
        )

    totals = summary["totals"]
    lines += [
        "",
        "## Kokku",
        "",
        f"- teemaridu: {totals['matter_rows']}",
        f"- kehtivaid viiteid: {totals['valid_references']}",
        f"- OneNote'i linke: {totals['hyperlinks']}",
        f"- Järgmiseks täidetud: {totals['next_action_populated']}",
        f"- tundmatuid veeruväärtusi: {totals['unknown_column_values']}",
        "",
        "## Kõrvalekalded",
        "",
        "| Kood | Ridu |",
        "| --- | ---: |",
    ]
    for code, count in sorted(summary["anomalies"].items()):
        lines.append(f"| {code} | {count} |")

    if summary.get("duplicate_references"):
        lines += ["", "## Korduvad viited", ""]
        lines += [f"- {reference}" for reference in summary["duplicate_references"]]

    if summary.get("structure_findings"):
        lines += ["", "## Struktuurileiud", ""]
        for finding in summary["structure_findings"]:
            lines.append(f"- {finding}")

    if summary.get("vocabulary"):
        vocabulary = summary["vocabulary"]
        lines += [
            "",
            "## Kontrollitud sõnastik",
            "",
            f"- töövihikus: {vocabulary['workbook_label_count']}",
            f"- seemnes tuntud: {vocabulary['known_label_count']}",
        ]
        if vocabulary["labels_missing_from_seed"]:
            lines.append("- seemnes puuduvad: " + ", ".join(vocabulary["labels_missing_from_seed"]))
        if vocabulary["labels_missing_from_workbook"]:
            lines.append(
                "- töövihikus puuduvad: " + ", ".join(vocabulary["labels_missing_from_workbook"])
            )

    return "\n".join(lines) + "\n"


def local_only_banner() -> str:
    """The sentence every operator summary opens with."""
    return _LOCAL_ONLY_BANNER


def write_reports(
    directory: Path,
    *,
    public: PublicSummary,
    operator_summary: dict[str, Any],
    operator_markdown: str,
    rows: list[list[Any]],
    row_header: list[str],
    anomalies: list[list[Any]],
    anomaly_header: list[str],
    mapping_gaps: list[list[Any]],
    mapping_gap_header: list[str],
    candidates: list[list[Any]],
    candidate_header: list[str],
) -> WrittenReports:
    if not isinstance(public, PublicSummary):
        # The public files are written from the schema or not at all.
        raise PublicSummaryError("Avaldatav kokkuvõte peab olema PublicSummary.")
    directory.mkdir(parents=True, exist_ok=True)

    aggregate = (
        _write_json(directory / "summary.json", public.to_payload()),
        _write_text(directory / "summary.md", public.markdown()),
    )
    operator = (
        _write_json(
            directory / "operator-summary.json",
            {"local_only": _LOCAL_ONLY_BANNER, **operator_summary},
        ),
        _write_text(directory / "operator-summary.md", operator_markdown),
    )
    row_level = (
        _write_csv(directory / "rows.csv", row_header, rows),
        _write_csv(directory / "anomalies.csv", anomaly_header, anomalies),
        _write_csv(directory / "mapping-gaps.csv", mapping_gap_header, mapping_gaps),
        _write_csv(directory / "next-action-candidates.csv", candidate_header, candidates),
    )
    return WrittenReports(
        directory=directory, aggregate=aggregate, row_level=row_level, operator=operator
    )


def _write_text(path: Path, text: str) -> Path:
    path.write_text(text, encoding="utf-8")
    return path
