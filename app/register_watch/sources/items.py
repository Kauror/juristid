from __future__ import annotations

import datetime as dt
from dataclasses import dataclass


@dataclass(frozen=True)
class Item:
    """One document or step a source reports, in the source's own words."""

    external_id: str
    title: str
    occurred_on: dt.date | None
    url: str = ""
    detail: str = ""


def parse_day(value: object) -> dt.date | None:
    """``2026-09-07`` or ``2026-09-07T14:00:00…`` to a date; anything else to ``None``."""
    if not isinstance(value, str) or len(value) < 10:
        return None
    try:
        return dt.date.fromisoformat(value[:10])
    except ValueError:
        return None
