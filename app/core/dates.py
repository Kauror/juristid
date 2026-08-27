"""How a date is written and read in this application.

One format reaches the reader: ``7.9.2026``. Day, month, year, dots, no leading
zeros — what an Estonian writes on paper, and what the department writes in the
register it is moving off. Nothing here changes storage: the database holds
``DateField`` and the API of every service still takes and returns
``datetime.date`` (specification 3.10).

Why this is not left to the browser
-----------------------------------
``<input type="date">`` renders in the *browser's* locale, not the page's. A
lawyer whose Windows install is US-English got ``mm/dd/yyyy`` on a form whose
every other word is Estonian, and typed ``7.9.2026`` into it — which that
control reads as the 9th of July. No server-side setting can reach that widget,
which is why the input is an ordinary text box with a calendar of our own
(static/js/app.js).

Why input is flexible and output is not
---------------------------------------
Reading accepts every unambiguous way somebody might write the date down,
including ISO — links, bookmarks and saved queries from before this module
carry ``2026-09-07`` and must keep working. Writing produces exactly one form,
so two dates on the same screen are never written two ways.

Nothing here guesses. ``09/07/2026`` is refused rather than resolved: it means
September in one country and July in another, and a system that picks one is
wrong half the time without ever saying so.
"""

from __future__ import annotations

import re
from datetime import date

from django.utils.dateparse import parse_date

#: What ``forms.DateField`` accepts. Order matters only for ambiguity, and none
#: of these are ambiguous with each other: a dotted date is day-first and a
#: dashed one is ISO.
#:
#: ``%d.%m.%Y`` covers both ``07.09.2026`` and ``7.9.2026`` — Python's
#: ``strptime`` accepts an unpadded number for ``%d`` and ``%m``, which is the
#: single reason this list is three entries and not six.
ESTONIAN_DATE_INPUT_FORMATS: tuple[str, ...] = (
    "%d.%m.%Y",
    "%d.%m.%y",
    "%Y-%m-%d",
)

#: What the reader sees, and what a `placeholder` promises.
ESTONIAN_DATE_PLACEHOLDER = "pp.kk.aaaa"

#: The message a refused date carries. Says what to type rather than what was
#: wrong with what was typed: "Enter a valid date" leaves somebody who wrote
#: ``9/7/2026`` no wiser about why.
ESTONIAN_DATE_ERROR = "Kirjuta kuupäev kujul 7.9.2026."

#: `7.9.2026`, `07.09.2026`, `7.9.26`. Anchored, so `7.9.2026x` is refused
#: rather than silently truncated.
_DOTTED = re.compile(r"^(\d{1,2})\.(\d{1,2})\.(\d{2}|\d{4})$")

#: Two-digit years land in the current century. The register runs from 2011 and
#: this application will not outlive 2099; a sliding window would be a rule
#: nobody could predict from the box they typed into.
_CENTURY = 2000


def format_estonian_date(value: date | None) -> str:
    """``7.9.2026``. Empty string for nothing, never the word "None".

    Written by hand rather than with ``strftime``: the directive that drops a
    leading zero is ``%-d`` on Linux and ``%#d`` on Windows, and this codebase
    is developed on one and deployed on the other.
    """
    if value is None:
        return ""
    return f"{value.day}.{value.month}.{value.year}"


def parse_estonian_date(value: str | None) -> date | None:
    """``7.9.2026`` or ``07.09.2026`` to a real date, or None.

    Impossible dates are None, not an approximation: ``31.02.2026`` is somebody
    mistyping, and the 28th is not what they meant.
    """
    if not value:
        return None
    match = _DOTTED.match(value.strip())
    if match is None:
        return None
    day, month, year = (int(part) for part in match.groups())
    if len(match.group(3)) == 2:
        year += _CENTURY
    try:
        return date(year, month, day)
    except ValueError:
        return None


def parse_flexible_date(value: str | None) -> date | None:
    """Estonian first, then ISO. For query parameters, not for form fields.

    Forms declare ``input_formats`` and get this behaviour from Django. A URL
    has no form behind it, and ``?tahtaeg_alates=`` has to read both a link a
    lawyer edited by hand and one the application generated before this module
    existed.
    """
    if not value:
        return None
    stripped = value.strip()
    return parse_estonian_date(stripped) or parse_date(stripped)


#: The one-letter weekday, Monday first. Estonian: esmaspäev, teisipäev,
#: kolmapäev, neljapäev, reede, laupäev, pühapäev.
#:
#: A letter rather than a name because the deadline lists print it in a 60px
#: cell beside the date, where the question it answers is "is that a working
#: day" and nothing more (design handoff 1a).
WEEKDAY_LETTERS: tuple[str, ...] = ("E", "T", "K", "N", "R", "L", "P")


def weekday_letter(value: date | None) -> str:
    return "" if value is None else WEEKDAY_LETTERS[value.weekday()]


def short_day_month(value: date | None) -> str:
    """``28.08``. Zero-padded, because these are read in a column."""
    return "" if value is None else f"{value.day:02d}.{value.month:02d}"


def short_range(start: date | None, end: date | None) -> str:
    """``27.08–31.08``, the way a group header states the window it holds.

    An en dash, which is what a range takes in Estonian typography, and no
    spaces around it — the two dates are one token in a header that is already
    carrying a title and a link.
    """
    if start is None or end is None:
        return ""
    if start == end:
        return short_day_month(start)
    return f"{short_day_month(start)}–{short_day_month(end)}"
