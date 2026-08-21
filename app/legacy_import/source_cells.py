"""Reading a stored source row back through its era's contract.

``MatterSourceReference.source_row_raw`` keeps every cell of the register row
verbatim, keyed by column *letter*. That is the right shape for provenance —
a header corrected in a later snapshot must not change what was stored — and
the wrong shape for asking a question like "who did this row say was
responsible", because the letter differs between years.

The era contract is the bridge, and it is the only one. ``VASTUTAJA`` is column
H on the current sheet and is not column H in every year; ``HETKESEIS`` and
``JÄRGMISEKS`` do not exist before 2023 and 2025 at all. Every read here goes
through :func:`app.legacy_import.contracts.load_contracts`, so a later
correction to a contract changes what these functions see, and no caller has to
know a column letter.

Nothing here writes, and nothing re-opens a workbook.
"""

from __future__ import annotations

from app.legacy_import.contracts import EraContract, load_contracts
from app.legacy_import.models import MatterSourceReference


def contracts_by_sheet() -> dict[str, EraContract]:
    """Era contracts keyed by the sheet name they describe.

    By sheet rather than by era: an era spans several years (``2011-2017``) and
    each contract describes one of them, so the era alone cannot say which
    column layout a row was read under. The sheet name is what the source
    reference actually stored, and every contract's sheet is unique.
    """
    return {contract.sheet: contract for contract in load_contracts().values()}


def source_cell(
    reference: MatterSourceReference,
    contracts: dict[str, EraContract],
    canonical_field: str,
) -> str | None:
    """One canonical field's raw text for one source reference, or ``None``.

    ``None`` means the row cannot be asked this question: no contract for that
    sheet, or a year whose contract describes no such column. An empty string
    means the column was found and the cell was blank. Collapsing those two
    would turn "the register did not have this concept in 2014" into "the 2014
    row left it empty", which are different facts about different things.
    """
    contract = contracts.get(reference.source_sheet)
    if contract is None:
        return None
    column = contract.column_for(canonical_field)
    if column is None:
        return None
    raw = reference.source_row_raw
    if not isinstance(raw, dict):
        return None
    return str(raw.get(column.letter, "") or "")
