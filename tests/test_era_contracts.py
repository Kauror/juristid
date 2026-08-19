"""The era contracts are data the parser trusts, so they are tested like code."""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

from app.legacy_import.contracts import (
    AUTHORITY_LEVELS,
    CANONICAL_FIELDS,
    ContractError,
    EraContract,
    contract_for_year,
    contracts_directory,
    load_contracts,
)

YEARS = tuple(range(2011, 2027))


def test_every_year_sheet_has_a_contract() -> None:
    contracts = load_contracts()
    assert sorted(contracts) == list(YEARS)


@pytest.mark.parametrize("year", YEARS)
def test_contract_declares_a_reference_and_a_title(year: int) -> None:
    contract = contract_for_year(year)
    assert contract is not None
    assert contract.column_for("matter_reference") is not None
    assert contract.column_for("title") is not None


@pytest.mark.parametrize("year", YEARS)
def test_contract_is_marked_reviewed_and_versioned(year: int) -> None:
    contract = contract_for_year(year)
    assert contract is not None
    assert contract.status == "reviewed"
    assert contract.contract_version
    assert contract.reviewed_by


def test_counterparty_direction_flips_in_2020_and_never_merges() -> None:
    """The single most expensive mistake this import could make.

    ``KELLELT`` is who sent it. ``KELLELE`` is who it was sent to. The headers
    look alike, the meanings are opposite, and no sheet carries both.
    """
    for year in range(2011, 2020):
        contract = contract_for_year(year)
        assert contract is not None
        column = contract.column_for("source_organisation")
        assert column is not None, f"{year} must read KELLELT as the sender"
        assert column.header == "KELLELT"
        assert column.direction == "source"
        assert contract.column_for("addressee_organisation") is None

    for year in range(2020, 2027):
        contract = contract_for_year(year)
        assert contract is not None
        column = contract.column_for("addressee_organisation")
        assert column is not None, f"{year} must read KELLELE as the addressee"
        assert column.header == "KELLELE"
        assert column.direction == "addressee"
        assert contract.column_for("source_organisation") is None


def test_2021_header_row_is_the_second_row() -> None:
    contract = contract_for_year(2021)
    assert contract is not None
    assert contract.header_row == 2, "the 2021 sheet puts a title above its headers"


def test_member_feedback_columns_appear_in_2018_and_stay_deferred() -> None:
    assert contract_for_year(2017) is not None
    assert contract_for_year(2017).column_for("member_feedback_responded") is None

    for year in range(2018, 2027):
        contract = contract_for_year(year)
        assert contract is not None
        for canonical in ("member_feedback_responded", "member_feedback_requested"):
            column = contract.column_for(canonical)
            assert column is not None
            # Consultation is Stage 2C. Reading these now and computing anything
            # from them are different things, and only the first is authorised.
            assert column.authority == "deferred"


def test_status_appears_in_2023_and_next_action_in_2025() -> None:
    for year in range(2011, 2023):
        assert contract_for_year(year).column_for("legacy_status") is None
    for year in range(2023, 2027):
        assert contract_for_year(year).column_for("legacy_status") is not None

    for year in range(2011, 2025):
        assert contract_for_year(year).column_for("next_action_text") is None
    for year in (2025, 2026):
        assert contract_for_year(year).column_for("next_action_text") is not None


def test_the_2022_extra_column_stays_unknown() -> None:
    """It holds 27 values in the real snapshot and nobody knows what they mean."""
    contract = contract_for_year(2022)
    assert contract is not None
    unknown = contract.columns_for("unknown")
    assert len(unknown) == 1
    assert unknown[0].letter == "K"
    assert unknown[0].header == ""
    assert unknown[0].authority == "unknown"
    assert unknown[0].parser == "raw"

    for year in YEARS:
        if year != 2022:
            assert not contract_for_year(year).columns_for("unknown")


def test_the_sent_date_has_no_canonical_home_and_says_so() -> None:
    """`VÄLJA` is a real column with deliberately nowhere to go.

    Submission is the canonical outbound record and SENT requires evidence.
    A date alone would manufacture an opinion nobody can produce.
    """
    for year in YEARS:
        column = contract_for_year(year).column_for("opinion_sent_date")
        assert column is not None
        assert column.authority == "deferred"
        assert "Submission" in column.notes


@pytest.mark.parametrize("year", YEARS)
def test_every_column_uses_the_closed_vocabularies(year: int) -> None:
    contract = contract_for_year(year)
    assert contract is not None
    for column in contract.columns:
        assert column.canonical_field in CANONICAL_FIELDS
        assert column.authority in AUTHORITY_LEVELS


@pytest.mark.parametrize("year", YEARS)
def test_column_letters_are_contiguous_from_A(year: int) -> None:
    """A gap would mean the contract silently skipped a column."""
    contract = contract_for_year(year)
    assert contract is not None
    indexes = sorted(column.index for column in contract.columns)
    assert indexes == list(range(1, len(indexes) + 1))


@pytest.mark.parametrize("year", YEARS)
def test_every_column_documents_its_null_semantics(year: int) -> None:
    """Blank and zero are different facts, and the contract has to say so."""
    contract = contract_for_year(year)
    assert contract is not None
    for column in contract.columns:
        assert column.null_semantics.strip(), f"{year} column {column.letter}"


def test_contract_files_are_plain_toml_readable_without_the_application() -> None:
    """A reviewer must be able to read these without installing anything."""
    for path in sorted(contracts_directory().glob("excel-era-*.toml")):
        tomllib.loads(path.read_text(encoding="utf-8"))


def _write(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "excel-era-2099.toml"
    path.write_text(body, encoding="utf-8")
    return path


CONTRACT_HEAD = """
year = 2099
era = "test"
contract_version = "1.0"
status = "reviewed"
"""

REFERENCE_COLUMN = """
[[column]]
letter = "A"
header = "NR"
canonical_field = "matter_reference"
parser = "human_reference"
authority = "authoritative"
null_semantics = "x"
"""

TITLE_COLUMN = """
[[column]]
letter = "B"
header = "TEEMA"
canonical_field = "title"
parser = "text"
authority = "authoritative"
null_semantics = "x"
"""


def _load(tmp_path: Path, body: str) -> EraContract:
    from app.legacy_import.contracts import _load_one

    return _load_one(_write(tmp_path, body))


def test_a_contract_claiming_both_directions_is_rejected(tmp_path: Path) -> None:
    body = (
        CONTRACT_HEAD
        + REFERENCE_COLUMN
        + TITLE_COLUMN
        + """
[[column]]
letter = "C"
header = "KELLELT"
canonical_field = "source_organisation"
parser = "text"
authority = "optional"
direction = "source"
null_semantics = "x"

[[column]]
letter = "D"
header = "KELLELE"
canonical_field = "addressee_organisation"
parser = "text"
authority = "optional"
direction = "addressee"
null_semantics = "x"
"""
    )
    with pytest.raises(ContractError, match="cannot carry both"):
        _load(tmp_path, body)


def test_a_sender_column_without_the_source_direction_is_rejected(tmp_path: Path) -> None:
    body = (
        CONTRACT_HEAD
        + REFERENCE_COLUMN
        + TITLE_COLUMN
        + """
[[column]]
letter = "C"
header = "KELLELT"
canonical_field = "source_organisation"
parser = "text"
authority = "optional"
direction = "addressee"
null_semantics = "x"
"""
    )
    with pytest.raises(ContractError, match="direction"):
        _load(tmp_path, body)


def test_an_unknown_canonical_field_is_rejected(tmp_path: Path) -> None:
    body = (
        CONTRACT_HEAD
        + REFERENCE_COLUMN
        + TITLE_COLUMN
        + """
[[column]]
letter = "C"
header = "X"
canonical_field = "something_invented"
parser = "text"
authority = "optional"
null_semantics = "x"
"""
    )
    with pytest.raises(ContractError, match="unknown canonical_field"):
        _load(tmp_path, body)


def test_a_column_with_no_meaning_must_be_marked_unknown(tmp_path: Path) -> None:
    body = (
        CONTRACT_HEAD
        + REFERENCE_COLUMN
        + TITLE_COLUMN
        + """
[[column]]
letter = "C"
header = ""
canonical_field = "unknown"
parser = "raw"
authority = "optional"
null_semantics = "x"
"""
    )
    with pytest.raises(ContractError, match='authority must be "unknown"'):
        _load(tmp_path, body)
