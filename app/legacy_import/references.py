"""The human register reference, ``YYYY_N``.

Every lawyer in the department knows their files by this number, so an imported
Matter keeps the number the register gave it. Juristid never allocates a fresh
reference to a row that already has a valid one — that would break the one
identifier people actually carry in their heads.

The parsing is strict on purpose. ``2026_12a``, ``2026-12`` and ``2026_012`` are
not the same token as ``2026_12``, and quietly accepting them would let two
source rows collapse onto one Matter.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_REFERENCE = re.compile(r"^(\d{4})_(\d{1,6})$")

#: The register starts in 2011 and a reference far outside the sheet range is a
#: typo rather than a year. Kept wide so a future sheet does not need a code
#: change to be readable.
MIN_YEAR = 1990
MAX_YEAR = 2100


@dataclass(frozen=True)
class MatterReference:
    year: int
    number: int

    def __str__(self) -> str:
        return f"{self.year}_{self.number}"


def parse_matter_reference(value: str | None) -> MatterReference | None:
    """Read ``2026_123``. Returns ``None`` for anything else, including blanks."""
    if value is None:
        return None
    match = _REFERENCE.match(str(value).strip())
    if match is None:
        return None

    year_text, number_text = match.groups()
    # A leading zero means the source wrote a different token than the one the
    # integers would round-trip to, so it is not this reference.
    if number_text != str(int(number_text)):
        return None

    year, number = int(year_text), int(number_text)
    if not (MIN_YEAR <= year <= MAX_YEAR) or number < 1:
        return None
    return MatterReference(year=year, number=number)
