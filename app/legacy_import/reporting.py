"""Writing the import reports.

Every report this module produces falls into one of two classes, and the split
is physical rather than a matter of care:

**Aggregate** — ``summary.json`` and ``summary.md``. Counts, checksums, sheet
structure, anomaly tallies. No titles, no names, no URLs, no cell contents.
These are safe to attach to a pull request, upload as a CI artifact, or paste
into a message.

**Row-level** — ``rows.csv``, ``anomalies.csv``, ``mapping-gaps.csv`` and
``next-action-candidates.csv``. These reproduce source content because that is
the only way a reviewer can act on them, and they therefore stay in ignored
local storage. For the real register they never leave the operator's machine.

Keeping the boundary in the file layout rather than in a reviewer's judgement is
the point: it is much harder to accidentally attach the wrong file than to
accidentally include the wrong field.
"""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

#: Files safe to publish. Anything not named here is assumed to contain source
#: content, and the tests assert that this list stays accurate.
AGGREGATE_REPORTS: tuple[str, ...] = ("summary.json", "summary.md")

ROW_LEVEL_REPORTS: tuple[str, ...] = (
    "rows.csv",
    "anomalies.csv",
    "mapping-gaps.csv",
    "next-action-candidates.csv",
)

_LOCAL_ONLY_BANNER = (
    "Selles kaustas on kaks liiki faile. summary.json ja summary.md sisaldavad "
    "ainult koondarve ja struktuuri. Ülejäänud CSV-failid sisaldavad lähteridade "
    "sisu ning need jäävad ainult kohalikku ignoreeritud hoidlasse: neid ei "
    "laadita üles, ei lisata Gitti ega saadeta edasi."
)


@dataclass(frozen=True)
class WrittenReports:
    directory: Path
    aggregate: tuple[Path, ...]
    row_level: tuple[Path, ...]

    @property
    def all_paths(self) -> tuple[Path, ...]:
        return self.aggregate + self.row_level


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
        writer = csv.writer(handle, delimiter=";")
        writer.writerow(header)
        writer.writerows(rows)
    return path


def inspection_markdown(summary: dict[str, Any]) -> str:
    source = summary["source"]
    lines = [
        "# Registri impordi ülevaade",
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


def write_reports(
    directory: Path,
    *,
    summary: dict[str, Any],
    markdown: str,
    rows: list[list[Any]],
    row_header: list[str],
    anomalies: list[list[Any]],
    anomaly_header: list[str],
    mapping_gaps: list[list[Any]],
    mapping_gap_header: list[str],
    candidates: list[list[Any]],
    candidate_header: list[str],
) -> WrittenReports:
    directory.mkdir(parents=True, exist_ok=True)

    aggregate = (
        _write_json(directory / "summary.json", summary),
        _write_text(directory / "summary.md", markdown),
    )
    row_level = (
        _write_csv(directory / "rows.csv", row_header, rows),
        _write_csv(directory / "anomalies.csv", anomaly_header, anomalies),
        _write_csv(directory / "mapping-gaps.csv", mapping_gap_header, mapping_gaps),
        _write_csv(directory / "next-action-candidates.csv", candidate_header, candidates),
    )
    return WrittenReports(directory=directory, aggregate=aggregate, row_level=row_level)


def _write_text(path: Path, text: str) -> Path:
    path.write_text(text, encoding="utf-8")
    return path
