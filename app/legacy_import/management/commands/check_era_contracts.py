"""Validate the era contracts and regenerate their human overview.

    python manage.py check_era_contracts            # validate and rewrite
    python manage.py check_era_contracts --check    # validate, fail if stale

The TOML files are the single source of truth. The Markdown overview is
generated from them and never edited by hand, because two hand-maintained
descriptions of the same parser rules drift apart and then the reviewer reads
the wrong one. CI runs ``--check`` so that editing a contract without
regenerating the overview fails the build rather than quietly diverging.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from django.core.management.base import BaseCommand, CommandError, CommandParser

from app.legacy_import.contracts import (
    CONTRACT_SCHEMA_VERSION,
    ContractError,
    EraContract,
    contracts_directory,
    load_contracts,
)

OVERVIEW = "excel-era-overview.md"

_AUTHORITY_LABELS = {
    "authoritative": "kanooniline",
    "optional": "valikuline",
    "deferred": "edasi lükatud",
    "unknown": "tundmatu",
}


def render_overview(contracts: dict[int, EraContract]) -> str:
    lines = [
        "# Ajastulepingute ülevaade",
        "",
        "**Genereeritud fail.** Ära muuda seda käsitsi — allikaks on samas kaustas",
        "olevad `excel-era-<aasta>.toml` failid ja selle kirjutab uuesti",
        "`python manage.py check_era_contracts`.",
        "",
        f"Lepingu skeemi versioon: `{CONTRACT_SCHEMA_VERSION}`.",
        "",
        "## Ajastud",
        "",
        "| Aasta | Ajastu | Pealkirjarida | Veerge | Vastaspool |",
        "| --- | --- | ---: | ---: | --- |",
    ]
    for year in sorted(contracts):
        contract = contracts[year]
        if contract.column_for("source_organisation") is not None:
            counterparty = "KELLELT (saatja)"
        elif contract.column_for("addressee_organisation") is not None:
            counterparty = "KELLELE (adressaat)"
        else:
            counterparty = "—"
        lines.append(
            f"| {year} | {contract.era} | {contract.header_row} | "
            f"{len(contract.columns)} | {counterparty} |"
        )

    for year in sorted(contracts):
        contract = contracts[year]
        lines += [
            "",
            f"## {year}",
            "",
            contract.notes.strip(),
            "",
            "| Veerg | Pealkiri | Kanooniline väli | Parser | Kaal | Tühja tähendus |",
            "| --- | --- | --- | --- | --- | --- |",
        ]
        for column in contract.columns:
            header = f"`{column.header}`" if column.header else "_(pealkirjata)_"
            lines.append(
                f"| {column.letter} | {header} | `{column.canonical_field}` | "
                f"`{column.parser}` | {_AUTHORITY_LABELS[column.authority]} | "
                f"{' '.join(column.null_semantics.split())} |"
            )
    return "\n".join(lines) + "\n"


class Command(BaseCommand):
    help = "Validate the per-era workbook contracts and regenerate their overview."

    requires_system_checks: list[str] = []
    requires_migrations_checks = False

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument(
            "--check",
            action="store_true",
            help="Fail if the generated overview is out of date instead of rewriting it.",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        try:
            contracts = load_contracts()
        except ContractError as error:
            raise CommandError(str(error)) from error

        missing = [year for year in range(2011, 2027) if year not in contracts]
        if missing:
            raise CommandError(f"No contract for {missing}.")

        rendered = render_overview(contracts)
        path: Path = contracts_directory() / OVERVIEW

        if options["check"]:
            current = path.read_text(encoding="utf-8") if path.exists() else ""
            if current != rendered:
                raise CommandError(
                    f"{OVERVIEW} is out of date. Run "
                    "`python manage.py check_era_contracts` and commit the result."
                )
            self.stdout.write(self.style.SUCCESS(f"{len(contracts)} contracts valid and current."))
            return

        path.write_text(rendered, encoding="utf-8")
        self.stdout.write(
            self.style.SUCCESS(f"{len(contracts)} contracts valid; {OVERVIEW} regenerated.")
        )
