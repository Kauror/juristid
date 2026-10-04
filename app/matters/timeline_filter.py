"""`Teema käik` narrowed by time and by kind — over the whole visible history.

The historical replay (UX-012) found that on a 60-row file the only way to an
event six months back was to know which `Hetkeseis` the file was in then and
scan that period. This is the one compact tool for it: a date range and a kind,
applied on the server to every row the reader may see — never to the rows a
browser happens to hold — with the count and the periods answering the same
filter.

What it never does:

* **invent a day.** A row known to a month or a quarter is kept when its
  *period* overlaps the range, read off the record's own precision
  (`workflow.dates.period_bounds`), never off the anchor day it sorts on;
* **lose an undated row.** A date range cannot place a row whose day is
  unknown, so it leaves the range — and `Ainult kuupäevata` lists exactly those;
* **move a row.** Each row stays in the period it was written in
  (docs/adr/0131 §7); the filter only hides rows, and the default is no filter.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any

from django.utils import timezone

from app.workflow.dates import period_bounds
from app.workflow.enums import DatePrecision

KIND_ALL = "koik"
KIND_NOTES = "markmed"
KIND_OPINIONS = "arvamused"
KIND_ENGAGEMENTS = "kaasamised"
KIND_OVERVIEWS = "ulevaated"
KIND_DATES = "tahtajad"
KIND_CHANGES = "muudatused"
KIND_UNDATED = "kuupaevata"

#: The kinds the control offers, in its order, with the words it uses.
KINDS: tuple[tuple[str, str], ...] = (
    (KIND_ALL, "Kõik kirjed"),
    (KIND_NOTES, "Märkmed"),
    (KIND_OPINIONS, "Arvamused ja tagasiside"),
    (KIND_ENGAGEMENTS, "Kaasamised"),
    (KIND_OVERVIEWS, "Ülevaated / uudised"),
    (KIND_DATES, "Tähtajad, jõustumised, töövõidud"),
    (KIND_CHANGES, "Hetkeseisu ja sammude muudatused"),
    (KIND_UNDATED, "Ainult kuupäevata kirjed"),
)
KIND_KEYS = frozenset(key for key, _label in KINDS)

#: Each record kind's own date and precision, as ``(date field, precision field)``.
#: A record not named here is dated by the row's own time.
_DATED_FIELDS: dict[str, tuple[str, str]] = {
    "MatterEngagement": ("occurred_on", "occurred_on_precision"),
    "MatterProceduralDevelopment": ("occurred_on", "occurred_on_precision"),
    "MatterExternalPosition": ("stated_on", "stated_on_precision"),
    "MatterImportantDate": ("date_value", "date_precision"),
    "MatterEffectiveDate": ("date_value", "date_precision"),
    "MatterWorkVictory": ("period_date", "date_precision"),
    "MatterWebsiteOverview": ("published_on", ""),
}
#: Record kinds whose missing date means «unknown», not «use the row's time».
_MAY_BE_UNDATED = frozenset(_DATED_FIELDS)


@dataclass(frozen=True)
class TimelineFilter:
    """A reader's question to `Teema käik`. Empty means no filter at all."""

    since: date | None = None
    until: date | None = None
    kind: str = KIND_ALL

    @property
    def is_active(self) -> bool:
        return self.since is not None or self.until is not None or self.kind != KIND_ALL

    @property
    def since_text(self) -> str:
        return _et(self.since)

    @property
    def until_text(self) -> str:
        return _et(self.until)


def _et(value: date | None) -> str:
    return f"{value.day}.{value.month}.{value.year}" if value is not None else ""


def _parse(raw: str | None) -> date | None:
    """An Estonian `pp.kk.aaaa` day, or ``None`` for anything else — never a guess."""
    text = (raw or "").strip()
    if not text:
        return None
    parts = text.replace("/", ".").split(".")
    if len(parts) != 3:
        return None
    try:
        day, month, year = (int(part) for part in parts)
        return date(year, month, day)
    except ValueError:
        return None


def timeline_filter_from(query: Any) -> TimelineFilter:
    """Read `?alates=`, `?kuni=` and `?liik=` off a request's query string."""
    since = _parse(query.get("alates"))
    until = _parse(query.get("kuni"))
    if since is not None and until is not None and since > until:
        since, until = until, since
    kind = (query.get("liik") or KIND_ALL).strip()
    return TimelineFilter(since=since, until=until, kind=kind if kind in KIND_KEYS else KIND_ALL)


def item_kind(item: Any) -> str:
    """Which of the offered kinds a chronology row is."""
    record = item.record
    name = type(record).__name__ if record is not None else ""
    if item.is_entry or name == "MatterProceduralDevelopment":
        return KIND_NOTES
    if name in ("Submission", "MatterExternalPosition"):
        return KIND_OPINIONS
    if name == "MatterEngagement":
        return KIND_ENGAGEMENTS
    if name == "MatterWebsiteOverview":
        return KIND_OVERVIEWS
    if name in ("MatterImportantDate", "MatterEffectiveDate", "MatterWorkVictory"):
        return KIND_DATES
    return KIND_CHANGES


def item_period(item: Any) -> tuple[date, date] | None:
    """The days a row covers, or ``None`` when its day is unknown.

    Read off the record's own date and precision where it has them, so a month
    stays a month; every other row is the day it happened on.
    """
    record = item.record
    name = type(record).__name__ if record is not None else ""
    if name == "MatterWebsiteOverview" and record.is_cancelled and record.cancelled_at is not None:
        # A cancelled plan has no publication day; its row sits on, and
        # prints, the day it was cancelled (`timeline._website_overview_rows`).
        day = timezone.localtime(record.cancelled_at).date()
        return day, day
    fields = _DATED_FIELDS.get(name)
    if fields is not None:
        value = getattr(record, fields[0], None)
        if value is None:
            return None if name in _MAY_BE_UNDATED else _day_of(item)
        precision = getattr(record, fields[1], "") if fields[1] else DatePrecision.EXACT
        return period_bounds(value, precision or DatePrecision.EXACT)
    return _day_of(item)


def _day_of(item: Any) -> tuple[date, date]:
    day = timezone.localtime(item.occurred_at).date()
    return day, day


def keeps(item: Any, chosen: TimelineFilter) -> bool:
    """Whether this row answers the reader's filter."""
    if chosen.kind == KIND_UNDATED:
        return item_period(item) is None
    if chosen.kind != KIND_ALL and item_kind(item) != chosen.kind:
        return False
    if chosen.since is None and chosen.until is None:
        return True
    period = item_period(item)
    if period is None:
        return False
    start, end = period
    if chosen.since is not None and end < chosen.since:
        return False
    return not (chosen.until is not None and start > chosen.until)
