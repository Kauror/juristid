"""Source-faithful date reading.

Twenty-six sheets of hand-kept spreadsheet contain every way a date can be
written down, including several that are not dates. The rules here are
deliberately narrow and **versioned**: each result carries the identifier of the
rule that produced it, so a future reader can tell how a given value was
interpreted without re-running anything.

Three things this module refuses to do, because each of them would turn a gap
into a fact (master specification 19.3):

* a blank cell never becomes zero, the epoch, or "today";
* an unreadable value never becomes a guess — the parsed date stays ``None``
  and the row carries an anomaly;
* a deadline earlier than the arrival date is never clamped. Negative intervals
  happen in every year of the real register, usually because the deadline was
  already running when the material reached Koda, and flattening them would
  erase the department's most ordinary complaint about how it is consulted.
"""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass
from typing import Any

#: Bumped when a rule changes meaning. Stored per row, so a reinterpretation is
#: visible rather than retroactive.
DATE_PARSER_VERSION = "1.0"

RULE_BLANK = "blank"
RULE_NATIVE = "native-datetime"
RULE_SERIAL_NUMERIC = "excel-serial-numeric"
RULE_SERIAL_STRING = "excel-serial-string"
RULE_ISO = "iso-8601"
RULE_DOTTED = "dotted-dmy"
RULE_SLASHED = "slashed-dmy"
RULE_UNPARSED = "unparsed"

#: Excel's day zero on the 1900 date system. Excel also believes 1900 was a
#: leap year, so serials above 59 are one day ahead of the true calendar; the
#: offset below already accounts for that, which is why it is 1899-12-30 rather
#: than 1899-12-31.
_EXCEL_EPOCH = dt.date(1899, 12, 30)

#: A serial has to land in a range a register could plausibly contain. 20000 is
#: 1954; 60000 is 2064. Outside that, a bare number is a count or a typo, not a
#: date, and treating it as one would invent a year.
_SERIAL_MIN = 20000
_SERIAL_MAX = 60000

_ISO = re.compile(r"^(\d{4})-(\d{1,2})-(\d{1,2})(?:[T ].*)?$")
_DOTTED = re.compile(r"^(\d{1,2})\.(\d{1,2})\.(\d{4})\.?$")
_SLASHED = re.compile(r"^(\d{1,2})/(\d{1,2})/(\d{4})$")
_INTEGERISH = re.compile(r"^\d{4,6}(?:\.0+)?$")


@dataclass(frozen=True)
class ParsedDate:
    """One cell read as a date, with the raw value kept beside the result."""

    raw: str
    value: dt.date | None
    rule: str
    version: str = DATE_PARSER_VERSION

    @property
    def is_blank(self) -> bool:
        return self.rule == RULE_BLANK

    @property
    def failed(self) -> bool:
        """True only when there was something to read and it could not be read."""
        return self.rule == RULE_UNPARSED


def _from_serial(serial: float, rule: str, raw: str) -> ParsedDate:
    if not (_SERIAL_MIN <= serial <= _SERIAL_MAX):
        return ParsedDate(raw=raw, value=None, rule=RULE_UNPARSED)
    return ParsedDate(raw=raw, value=_EXCEL_EPOCH + dt.timedelta(days=int(serial)), rule=rule)


def _safe_date(year: int, month: int, day: int, rule: str, raw: str) -> ParsedDate:
    try:
        return ParsedDate(raw=raw, value=dt.date(year, month, day), rule=rule)
    except ValueError:
        # 31.02.2019 is written down often enough to matter. It is not a date,
        # and the honest answer is that the cell could not be read.
        return ParsedDate(raw=raw, value=None, rule=RULE_UNPARSED)


def parse_date(value: Any, *, raw: str | None = None) -> ParsedDate:
    """Read one cell as a date. Never raises; failure is a result, not an error."""
    text = raw if raw is not None else ("" if value is None else str(value))

    if value is None:
        return ParsedDate(raw=text, value=None, rule=RULE_BLANK)

    if isinstance(value, dt.datetime):
        return ParsedDate(raw=text, value=value.date(), rule=RULE_NATIVE)
    if isinstance(value, dt.date):
        return ParsedDate(raw=text, value=value, rule=RULE_NATIVE)

    # bool is an int subclass; a checkbox is not a date.
    if isinstance(value, bool):
        return ParsedDate(raw=text, value=None, rule=RULE_UNPARSED)

    if isinstance(value, int | float):
        return _from_serial(float(value), RULE_SERIAL_NUMERIC, text)

    if not isinstance(value, str):  # pragma: no cover - openpyxl yields no others
        return ParsedDate(raw=text, value=None, rule=RULE_UNPARSED)

    candidate = value.strip()
    if not candidate:
        return ParsedDate(raw=text, value=None, rule=RULE_BLANK)

    # Pre-2020 sheets store dates as serial numbers that were typed, or pasted,
    # as text. They look like "43831" and are the single most common historical
    # anomaly in this register.
    if _INTEGERISH.fullmatch(candidate):
        return _from_serial(float(candidate), RULE_SERIAL_STRING, text)

    if (match := _ISO.match(candidate)) is not None:
        year, month, day = (int(part) for part in match.groups())
        return _safe_date(year, month, day, RULE_ISO, text)

    if (match := _DOTTED.match(candidate)) is not None:
        day, month, year = (int(part) for part in match.groups())
        return _safe_date(year, month, day, RULE_DOTTED, text)

    if (match := _SLASHED.match(candidate)) is not None:
        day, month, year = (int(part) for part in match.groups())
        return _safe_date(year, month, day, RULE_SLASHED, text)

    return ParsedDate(raw=text, value=None, rule=RULE_UNPARSED)


def response_interval_days(received: dt.date | None, deadline: dt.date | None) -> int | None:
    """Days between arrival and deadline. Negative values are real and kept."""
    if received is None or deadline is None:
        return None
    return (deadline - received).days
