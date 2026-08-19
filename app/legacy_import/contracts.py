"""Per-era workbook contracts.

The register is not one schema. Between 2011 and 2026 the sheet gained and lost
columns, moved its header row, and — the expensive one — changed what the
counterparty column *means* without changing much else. A parser that guessed
at any of that would produce plausible, wrong history.

So every year sheet has a reviewed contract file under
``docs/data-contracts/`` and **no sheet is parsed without one**. The contracts
are TOML because they are read by people as often as by code, and because
``tomllib`` is in the standard library, so a reviewer never has to install
anything to check what the importer believes.

There is one source of truth. The Markdown overview in the same directory is
generated from these files by ``check_era_contracts``; it is never edited by
hand, because two hand-maintained descriptions of the same rules diverge.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from django.conf import settings

#: Bumped when the *meaning* of a contract file changes, not when a year is
#: added. Recorded on every ImportBatch and MatterSourceReference so a row can
#: always be traced back to the rules that produced it.
CONTRACT_SCHEMA_VERSION = "1.0"


class ContractError(Exception):
    """A contract file is missing, malformed or contradicts itself."""


# --------------------------------------------------------------------------
# Closed vocabularies. A contract that uses a value outside these fails to
# load, rather than silently describing a mapping the parser cannot perform.
# --------------------------------------------------------------------------

#: What a column means in the canonical model. ``unknown`` is a real, honest
#: answer and appears in the 2022 contract.
CANONICAL_FIELDS: frozenset[str] = frozenset(
    {
        "matter_reference",
        "title",
        "legal_instrument",
        "received_date",
        "response_deadline",
        "opinion_sent_date",
        "source_organisation",
        "addressee_organisation",
        "owner_name",
        "member_feedback_responded",
        "member_feedback_requested",
        "legacy_status",
        "next_action_text",
        "unknown",
    }
)

#: How the cell is read. Every parser preserves the raw value regardless.
PARSERS: frozenset[str] = frozenset(
    {"human_reference", "text", "date", "count", "status_label", "raw"}
)

#: How much weight the column carries.
#:
#: ``authoritative`` — the importer writes it to a canonical field;
#: ``optional``      — written when present, absent is normal;
#: ``deferred``      — a real field with no canonical home yet; raw only;
#: ``unknown``       — semantics not established; raw only, and a review finding.
AUTHORITY_LEVELS: frozenset[str] = frozenset({"authoritative", "optional", "deferred", "unknown"})

#: Which organisation column this is. Never inferred from the header text.
DIRECTIONS: frozenset[str] = frozenset({"", "source", "addressee"})

CONTRACT_STATUSES: frozenset[str] = frozenset({"reviewed", "provisional"})


@dataclass(frozen=True)
class ColumnContract:
    letter: str
    header: str
    canonical_field: str
    parser: str
    authority: str
    direction: str
    meaning: str
    null_semantics: str
    notes: str

    @property
    def index(self) -> int:
        """1-based column index, derived from the letter."""
        value = 0
        for character in self.letter:
            value = value * 26 + (ord(character) - ord("A") + 1)
        return value

    @property
    def is_written_to_canonical_model(self) -> bool:
        return self.authority in {"authoritative", "optional"}


@dataclass(frozen=True)
class EraContract:
    """The reviewed reading of one year sheet."""

    year: int
    sheet: str
    era: str
    header_row: int
    contract_version: str
    status: str
    reviewed_by: str
    notes: str
    columns: tuple[ColumnContract, ...]
    source_path: Path

    def column_for(self, canonical_field: str) -> ColumnContract | None:
        for column in self.columns:
            if column.canonical_field == canonical_field:
                return column
        return None

    def columns_for(self, canonical_field: str) -> tuple[ColumnContract, ...]:
        return tuple(c for c in self.columns if c.canonical_field == canonical_field)

    @property
    def expected_headers(self) -> dict[int, str]:
        """Column index to the exact header string the sheet must carry."""
        return {column.index: column.header for column in self.columns}

    @property
    def last_contracted_index(self) -> int:
        return max((column.index for column in self.columns), default=0)


def contracts_directory() -> Path:
    return Path(settings.BASE_DIR) / "docs" / "data-contracts"


def _require(raw: dict[str, Any], key: str, path: Path) -> Any:
    if key not in raw:
        raise ContractError(f"{path.name}: required key {key!r} is missing.")
    return raw[key]


def _load_one(path: Path) -> EraContract:
    try:
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as error:  # pragma: no cover - message pass-through
        raise ContractError(f"{path.name}: {error}") from error

    year = int(_require(raw, "year", path))
    status = str(raw.get("status", "provisional"))
    if status not in CONTRACT_STATUSES:
        raise ContractError(f"{path.name}: unknown status {status!r}.")

    header_row = int(raw.get("header_row", 1))
    if header_row < 1:
        raise ContractError(f"{path.name}: header_row must be at least 1.")

    columns: list[ColumnContract] = []
    seen_letters: set[str] = set()
    for entry in _require(raw, "column", path):
        letter = str(_require(entry, "letter", path)).strip().upper()
        if letter in seen_letters:
            raise ContractError(f"{path.name}: column {letter} is described twice.")
        seen_letters.add(letter)

        canonical_field = str(_require(entry, "canonical_field", path))
        if canonical_field not in CANONICAL_FIELDS:
            raise ContractError(f"{path.name}: unknown canonical_field {canonical_field!r}.")

        parser = str(_require(entry, "parser", path))
        if parser not in PARSERS:
            raise ContractError(f"{path.name}: unknown parser {parser!r}.")

        authority = str(_require(entry, "authority", path))
        if authority not in AUTHORITY_LEVELS:
            raise ContractError(f"{path.name}: unknown authority {authority!r}.")

        direction = str(entry.get("direction", ""))
        if direction not in DIRECTIONS:
            raise ContractError(f"{path.name}: unknown direction {direction!r}.")

        columns.append(
            ColumnContract(
                letter=letter,
                header=str(_require(entry, "header", path)),
                canonical_field=canonical_field,
                parser=parser,
                authority=authority,
                direction=direction,
                meaning=str(entry.get("meaning", "")),
                null_semantics=str(entry.get("null_semantics", "")),
                notes=str(entry.get("notes", "")),
            )
        )

    contract = EraContract(
        year=year,
        sheet=str(raw.get("sheet", str(year))),
        era=str(_require(raw, "era", path)),
        header_row=header_row,
        contract_version=str(_require(raw, "contract_version", path)),
        status=status,
        reviewed_by=str(raw.get("reviewed_by", "")),
        notes=str(raw.get("notes", "")),
        columns=tuple(columns),
        source_path=path,
    )
    _check_internal_consistency(contract)
    return contract


def _check_internal_consistency(contract: EraContract) -> None:
    """Rules that hold for every era, whatever the columns turn out to be."""
    name = contract.source_path.name

    references = contract.columns_for("matter_reference")
    if len(references) != 1:
        raise ContractError(f"{name}: exactly one matter_reference column is required.")
    titles = contract.columns_for("title")
    if len(titles) != 1:
        raise ContractError(f"{name}: exactly one title column is required.")

    # The distinction the whole migration turns on. A contract may describe a
    # sender column or an addressee column, never one column claiming both, and
    # never a direction that disagrees with the canonical field it feeds.
    for column in contract.columns:
        if column.canonical_field == "source_organisation" and column.direction != "source":
            raise ContractError(
                f"{name}: column {column.letter} feeds the sender but is not "
                f'marked direction = "source".'
            )
        if column.canonical_field == "addressee_organisation" and column.direction != "addressee":
            raise ContractError(
                f"{name}: column {column.letter} feeds the addressee but is not "
                f'marked direction = "addressee".'
            )
    if contract.columns_for("source_organisation") and contract.columns_for(
        "addressee_organisation"
    ):
        raise ContractError(
            f"{name}: a single sheet cannot carry both KELLELT and KELLELE; "
            "they are different facts and no year uses both."
        )

    for column in contract.columns:
        if column.authority == "unknown" and column.canonical_field != "unknown":
            raise ContractError(
                f"{name}: column {column.letter} is marked unknown but claims to feed "
                f"{column.canonical_field!r}."
            )
        if column.canonical_field == "unknown" and column.authority != "unknown":
            raise ContractError(
                f"{name}: column {column.letter} has no established meaning, so its "
                'authority must be "unknown".'
            )


@lru_cache(maxsize=1)
def load_contracts() -> dict[int, EraContract]:
    """Every reviewed era contract, keyed by year."""
    directory = contracts_directory()
    contracts: dict[int, EraContract] = {}
    for path in sorted(directory.glob("excel-era-[0-9][0-9][0-9][0-9].toml")):
        contract = _load_one(path)
        if contract.year in contracts:
            raise ContractError(f"Two contracts describe {contract.year}.")
        contracts[contract.year] = contract
    if not contracts:
        raise ContractError(f"No era contracts found in {directory}.")
    return contracts


def contract_for_year(year: int) -> EraContract | None:
    return load_contracts().get(year)


def contract_versions() -> str:
    """A single stamp identifying the contract set used for one run."""
    contracts = load_contracts()
    span = f"{min(contracts)}-{max(contracts)}"
    return f"{CONTRACT_SCHEMA_VERSION}/{span}"
