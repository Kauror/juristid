"""A cell that a spreadsheet would run instead of reading.

Excel, LibreOffice and Google Sheets all treat a cell whose first character is
``=``, ``+``, ``-`` or ``@`` as a formula rather than as text. The application
writes several CSV exports of person-supplied strings — Teema titles, next-step
descriptions, work-victory statements — so a title typed as ``=HYPERLINK(...)``
is emitted verbatim and evaluated by whatever opens the file. Adversarial QA
found exactly that on 12 September (QA-15).

The reproduction needed an internal author to type it, so this is not a route in
from outside; it is defence in depth, and it is cheap. What makes it worth doing
anyway is where these files go: an export is pasted into a board paper, mailed to
a colleague, opened on a machine that is not this one, and by then nothing about
its provenance is visible.

**It is neutralised, never dropped and never truncated.** A leading apostrophe is
the convention every one of those applications understands as «this cell is
text»; the stored data is untouched, the cell still reads as what was typed, and
the export still diffs cleanly against the one before it.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any

#: The characters a spreadsheet reads as «a formula starts here».
#:
#: ``-`` is on the list and is the reason this function has to look at the value
#: as well as at its first character: a genuinely negative number begins with
#: one, and prefixing that would turn a numeric column into text.
FORMULA_PREFIXES = frozenset("=+-@")

#: Control characters a spreadsheet skips before deciding what the cell begins
#: with. A tab, a carriage return or a newline in front of ``=`` hides the
#: formula from a naive first-character check and from nothing else.
#:
#: Named for the record; :func:`_leading` looks through a wider class. Any
#: whitespace (a space, a no-break space, a vertical tab) and any control or
#: format character (NUL, DEL, a zero-width space, a byte-order mark) in front
#: of a formula character is looked through, because whether an application
#: trims it on import is that application's setting — LibreOffice's «Trim
#: spaces», a sheet's paste of text — and not something an export can know
#: (csf_1cecc4b5be8fb1a7be4fe125).
CONTROL_PREFIXES = "\t\r\n"

#: Unicode categories looked through in front of a formula character: control
#: characters (C0, DEL, C1) and format characters (zero-width space, BOM).
_INVISIBLE_CATEGORIES = frozenset({"Cc", "Cf"})

#: A value that only looks dangerous.
#:
#: A negative number and a negative decimal — in either the Estonian or the
#: machine spelling of the separator — are the shapes that legitimately start
#: with ``-``. Anything else beginning with one is text, including the
#: ``-cmd``-style content QA tested with.
_NUMERIC = re.compile(r"^[-+]?\d+(?:[.,]\d+)?$")


def _leading(value: str) -> str:
    """The value from the first character a spreadsheet could read as content."""
    index = 0
    while index < len(value) and (
        value[index].isspace() or unicodedata.category(value[index]) in _INVISIBLE_CATEGORIES
    ):
        index += 1
    return value[index:]


def csv_safe(value: Any) -> Any:
    """One cell, made inert if a spreadsheet would otherwise execute it.

    Non-strings pass through untouched, so an `int`, a `date` or a `Decimal` in a
    column that is meant to be numeric stays numeric — `csv.writer` renders them
    and nothing here has made them text.

    A string is prefixed with a single apostrophe when, after any leading
    whitespace, control or format characters (:func:`_leading`), it begins with
    one of :data:`FORMULA_PREFIXES` *and* is not simply a number.
    """
    if not isinstance(value, str) or not value:
        return value
    stripped = _leading(value)
    if not stripped or stripped[0] not in FORMULA_PREFIXES:
        return value
    if _NUMERIC.match(stripped):
        return value
    return f"'{value}"


def csv_safe_row(row: list[Any]) -> list[Any]:
    """Every cell of one row. Applied at the writer, not at each call site.

    One place, because a per-column decision is a decision somebody adding a
    column next year has to remember to make — and the column they forget will be
    the free-text one.
    """
    return [csv_safe(cell) for cell in row]
