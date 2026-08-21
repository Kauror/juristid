"""Filter controls, built where they can be tested.

Every control on these pages is a link, and every link carries the whole filter
state rather than only the part it changes. Building the query string here — not
in a template — is what stops the chip on screen from disagreeing with the URL
underneath it, which is the failure Stage 2E paid for on the register
(``app.matters.views.FILTER_LABELS``).
"""

from __future__ import annotations

from typing import Any

from django.http import QueryDict


def _query(base: dict[str, Any], **changes: Any) -> str:
    """The current filter state with some values replaced or dropped.

    ``None`` removes a parameter. ``leht`` is always dropped: changing a filter
    and landing on page 4 of a shorter list is how a control appears to do
    nothing.
    """
    params = QueryDict(mutable=True)
    merged = {**base, **changes}
    for key, value in merged.items():
        if value is None or value == "" or key == "leht":
            continue
        params[key] = str(value)
    return params.urlencode()


def options(
    choices: tuple[tuple[str, str], ...],
    *,
    parameter: str,
    current: str,
    base: dict[str, Any],
) -> list[dict[str, Any]]:
    """One segmented control's worth of links."""
    return [
        {
            "key": key,
            "label": label,
            "active": current == key,
            # Present and None rather than absent, so a template can tell "this
            # control has no counts" from "this option counts zero". A missing
            # dictionary key resolves to the empty string in a template, which
            # reads as neither.
            "count": None,
            "query": _query(base, **{parameter: key}),
        }
        for key, label in choices
    ]


def year_options(
    years: list[int],
    *,
    current: Any,
    base: dict[str, Any],
    parameter: str = "aasta",
    all_label: str = "Kõik aastad",
    extra: tuple[str, str] | None = None,
) -> list[dict[str, Any]]:
    """Every year the authorized records mention, newest first, plus "all".

    ``extra`` adds one non-year bucket — *Teadmata periood* on the work-victory
    page. It is a separate option rather than a year, because a record with no
    period is not a record from some particular year (Stage-2G brief 27).
    """
    entries: list[dict[str, Any]] = [
        {
            "key": "",
            "label": all_label,
            "active": current in (None, ""),
            "count": None,
            "query": _query(base, **{parameter: None}),
        }
    ]
    entries.extend(
        {
            "key": str(year),
            "label": str(year),
            "active": str(current) == str(year),
            "count": None,
            "query": _query(base, **{parameter: year}),
        }
        for year in years
    )
    if extra is not None:
        key, label = extra
        entries.append(
            {
                "key": key,
                "label": label,
                "active": str(current) == key,
                "count": None,
                "query": _query(base, **{parameter: key}),
            }
        )
    return entries
