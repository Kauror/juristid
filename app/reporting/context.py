"""The filter state, parsed from the URL once and passed down.

Every Statistika surface is a pure function of this object. Nothing reads
``request.GET`` below this module, which is what makes the filters survive a
refresh, a Back button and a pasted link without three views agreeing to
remember the same thing.

Two decisions worth stating.

**The viewer, not the user.** ``ReportingContext.viewer`` is whatever
``core.decorators.viewer_for`` returned — a signed-in person, or the department
sentinel when the shared gate has been passed and no persona chosen. It is the
only thing the selectors authorize against, and it is never ``request.user``
directly, because with no persona selected that is anonymous and would render
an empty department (Stage-2D auth brief 6).

**Period is not one column.** ``Period`` carries a span of years and nothing
else. Which *field* that span is compared against is a property of the metric,
not of the filter, and is resolved by the metric's ``TimeBasis``. A single
"date" filter that quietly chose a column would give four different answers to
the same question depending on which page you asked it from (brief 14).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field, replace
from datetime import date, datetime, time
from typing import Any

from django.http import HttpRequest
from django.utils import timezone

from app.core.decorators import viewer_for

#: URL parameter names. Estonian and ASCII: a filter people paste into chat
#: must not depend on how their client encodes ``ä``.
PARAM_PERIOD = "periood"
PARAM_RECORD_MODE = "liik"
PARAM_ORIGIN = "paritolu"
PARAM_OWNER = "vastutaja"
PARAM_POLICY_AREA = "valdkond"
PARAM_STAGE = "hetkeseis"
PARAM_TRACK = "menetlusliik"
PARAM_TAG = "silt"
PARAM_SECTION = "sektsioon"
PARAM_FILE_TYPE = "failityyp"

PERIOD_CURRENT = "kaesolev"
PERIOD_PREVIOUS = "eelmine"
PERIOD_LAST_FIVE = "viimased5"
PERIOD_ALL = "koik"

#: What the period selector offers. The default is the current year: the
#: question a lawyer opens this page with is about now, and the archive-wide
#: metrics ignore the period anyway and say so on their own cards, so the
#: default cannot quietly hide the corpus (brief 59).
DEFAULT_PERIOD = PERIOD_CURRENT


@dataclass(frozen=True)
class Period:
    """A span of whole years, or all of them.

    Whole years rather than arbitrary dates because the register's own
    reporting identity is a year, and offering a day-precision filter over a
    year-precision fact would invite a false sense of precision. A day-range
    picker is a later decision (docs/open-decisions.md).
    """

    key: str
    label: str
    start_year: int | None
    end_year: int | None

    @property
    def is_all(self) -> bool:
        return self.start_year is None and self.end_year is None

    @property
    def years(self) -> list[int]:
        if self.start_year is None or self.end_year is None:
            return []
        return list(range(self.start_year, self.end_year + 1))

    @property
    def start_date(self) -> date | None:
        return None if self.start_year is None else date(self.start_year, 1, 1)

    @property
    def end_date(self) -> date | None:
        return None if self.end_year is None else date(self.end_year, 12, 31)

    def start_datetime(self) -> datetime | None:
        start = self.start_date
        if start is None:
            return None
        return timezone.make_aware(datetime.combine(start, time.min))

    def end_datetime(self) -> datetime | None:
        """Exclusive upper bound: midnight at the start of the following year.

        Exclusive rather than ``23:59:59`` because an opinion sent at
        ``23:59:59.4`` on 31 December is a fact, and an inclusive bound built
        from a truncated second silently drops it.
        """
        if self.end_year is None:
            return None
        return timezone.make_aware(datetime.combine(date(self.end_year + 1, 1, 1), time.min))

    def contains_year(self, year: int | None) -> bool:
        if year is None:
            return False
        if self.start_year is None or self.end_year is None:
            return True
        return self.start_year <= year <= self.end_year


def period_options(today: date) -> list[Period]:
    """The four quick filters, resolved against the day the page is rendered."""
    year = today.year
    return [
        Period(PERIOD_CURRENT, "Käesolev aasta", year, year),
        Period(PERIOD_PREVIOUS, "Eelmine aasta", year - 1, year - 1),
        Period(PERIOD_LAST_FIVE, "Viimased 5 aastat", year - 4, year),
        Period(PERIOD_ALL, "Kõik aastad", None, None),
    ]


def parse_period(raw: str, today: date) -> Period:
    """Read one period from the URL, falling back rather than failing.

    Also accepts an explicit ``YYYY`` or ``YYYY-YYYY``, which is what a
    drill-through link from a year bar produces. A value that cannot be read is
    the default, not an error page: a statistics URL is something people edit by
    hand and forward to each other.
    """
    value = (raw or "").strip()
    options = {option.key: option for option in period_options(today)}
    if value in options:
        return options[value]

    parts = value.split("-")
    try:
        if len(parts) == 1 and parts[0]:
            single = int(parts[0])
            return Period(str(single), str(single), single, single)
        if len(parts) == 2 and all(parts):
            first, last = int(parts[0]), int(parts[1])
            if first <= last:
                return Period(f"{first}-{last}", f"{first}–{last}", first, last)
    except ValueError:
        pass

    return options[DEFAULT_PERIOD]


def _uuid_or_none(raw: str) -> uuid.UUID | None:
    """A hand-edited URL must not reach the database as a malformed UUID."""
    try:
        return uuid.UUID(raw)
    except (ValueError, AttributeError, TypeError):
        return None


@dataclass(frozen=True)
class ReportingContext:
    """Who is asking, over what period, narrowed how."""

    viewer: Any
    period: Period
    today: date
    now: datetime

    record_mode: str = ""
    origin: str = ""
    owner_id: uuid.UUID | None = None
    #: Set when the URL named an owner that could not be read as a UUID. The
    #: selectors then return an empty population rather than silently ignoring
    #: the filter and showing a total that contradicts the visible chips.
    owner_unreadable: bool = False
    policy_area_key: str = ""
    stage_key: str = ""
    track: str = ""
    tag_key: str = ""
    section: str = ""
    file_type: str = ""

    #: Populated by ``filters.describe`` for the chip strip; kept on the context
    #: so a template never has to re-derive what is active.
    chips: tuple[tuple[str, str, str], ...] = field(default_factory=tuple)

    def with_period(self, period: Period) -> ReportingContext:
        return replace(self, period=period)

    @property
    def has_matter_narrowing(self) -> bool:
        """Whether anything beyond the period narrows the Matter population."""
        return bool(
            self.record_mode
            or self.origin
            or self.owner_id
            or self.owner_unreadable
            or self.policy_area_key
            or self.stage_key
            or self.track
            or self.tag_key
        )

    def query_params(self, **overrides: str) -> dict[str, str]:
        """The active filters as URL parameters, ready to be re-encoded.

        Empty values are dropped so a shared link carries only what was chosen,
        and an override of ``""`` removes a dimension — which is how the chip's
        remove control is built.
        """
        params: dict[str, str] = {PARAM_PERIOD: self.period.key}
        if self.record_mode:
            params[PARAM_RECORD_MODE] = self.record_mode
        if self.origin:
            params[PARAM_ORIGIN] = self.origin
        if self.owner_id is not None:
            params[PARAM_OWNER] = str(self.owner_id)
        if self.policy_area_key:
            params[PARAM_POLICY_AREA] = self.policy_area_key
        if self.stage_key:
            params[PARAM_STAGE] = self.stage_key
        if self.track:
            params[PARAM_TRACK] = self.track
        if self.tag_key:
            params[PARAM_TAG] = self.tag_key
        if self.section:
            params[PARAM_SECTION] = self.section
        if self.file_type:
            params[PARAM_FILE_TYPE] = self.file_type

        for key, value in overrides.items():
            if value:
                params[key] = value
            else:
                params.pop(key, None)
        return params


def from_request(request: HttpRequest, *, today: date | None = None) -> ReportingContext:
    """Build the context for one request. The only reader of ``request.GET``."""
    params = request.GET
    now = timezone.now()
    today = today or timezone.localdate()

    raw_owner = params.get(PARAM_OWNER, "").strip()
    owner_id = _uuid_or_none(raw_owner) if raw_owner else None

    return ReportingContext(
        viewer=viewer_for(request),
        period=parse_period(params.get(PARAM_PERIOD, ""), today),
        today=today,
        now=now,
        record_mode=params.get(PARAM_RECORD_MODE, "").strip(),
        origin=params.get(PARAM_ORIGIN, "").strip(),
        owner_id=owner_id,
        owner_unreadable=bool(raw_owner) and owner_id is None,
        policy_area_key=params.get(PARAM_POLICY_AREA, "").strip(),
        stage_key=params.get(PARAM_STAGE, "").strip(),
        track=params.get(PARAM_TRACK, "").strip(),
        tag_key=params.get(PARAM_TAG, "").strip(),
        section=params.get(PARAM_SECTION, "").strip(),
        file_type=params.get(PARAM_FILE_TYPE, "").strip().upper(),
    )
