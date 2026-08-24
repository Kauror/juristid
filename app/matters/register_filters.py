"""The register's filter pipeline, as one function both the list and the KPIs use.

Why this module exists
----------------------
Every number on Ülevaade is a promise that a list exists behind it. Before this
module the promise was kept by writing the same condition twice — once as a
count on the dashboard, once as a ``?`` parameter on the register — and two
similar conditions written in two places is how a card reading *15* opens a list
of fourteen.

Here the card *is* the query string. ``summary_cards`` states its filters as the
parameters the register understands, and its count is produced by running those
parameters through :func:`apply_register_filters` — the same call
``matters:matter_list`` makes to build the page the card links to. The two
cannot disagree, because there is only one of them (master specification 18.9).

Two rules carry over unchanged from the view this was lifted out of.

**Authorization before arithmetic.** The queryset handed in has already been
through ``Matter.objects.visible_to``. Nothing here widens it: every branch adds
a condition, and the two that consult a child table (``?materjalid=``,
``?tegevus=``) scope that table to the same reader rather than reading it raw.

**An unreadable value empties the list rather than being ignored.** A filter
chip reading "31.02.2024" above the whole register is a lie the reader has no
way to catch. The one deliberate exception is ``?andmed=``, whose own default is
"no restriction".
"""

from __future__ import annotations

import uuid
from datetime import date
from typing import Any

from django.db.models import Count, Exists, OuterRef, Q, QuerySet

from app.legacy_import.source_pages import MatterSourcePage
from app.matters import selectors
from app.matters.enums import RecordMode
from app.matters.models import Matter

#: What `?allikas=` means. A word rather than a boolean, because `allikas=0`
#: reads as "source number zero" in a URL somebody is editing by hand.
SOURCE_PRESENT = "on"
SOURCE_ABSENT = "puudub"
#: More than one archived page claims this Matter. A separate value rather than
#: a count parameter because it is the only threshold any statistic asks about,
#: and 402 Matters in the real corpus have it (Stage-2D brief 12).
SOURCE_SEVERAL = "mitu"

#: What `?arvamus=` selects: the drafting step, as the register recorded it.
#:
#: `koostamisel` is the condition behind the *Arvamusi koostamisel* card, and it
#: lives here rather than in the dashboard so the card's count and the list it
#: opens are one query. The lifecycle half of the question — open, FULL, visible
#: — is asked by the ordinary parameters beside it, and `dashboard` is the
#: authority on why the register's VÄLJA column answers this and
#: `Submission.sent_at` does not (ADR 0021).
OPINION_DRAFTING = "koostamisel"
OPINION_SENT = "saadetud"

#: The two organisation directions, as URL parameters. Never merged: the same
#: register column meant the sender until 2019 and the addressee from 2020.
#: URL parameter -> the lookup path that answers it, and whether the path
#: crosses a many-to-many join. The sender side does, so its filters need an
#: explicit `.distinct()`: a Matter sent by two bodies would otherwise come back
#: twice from `?saatja=`, and — more quietly — the `visible_to` scope only adds
#: `.distinct()` when the reader is actually restricted, so a department head
#: would see the duplicate row nobody else did (Agent-E brief 39, 43).
ORGANISATION_FILTERS = {
    "saatja": ("source_organisations", True),
    "adressaat": ("addressee_organisation", False),
}

#: Which parameters hold a date, and which column each pair narrows. Both ends
#: of each pair are inclusive — see `selectors.date_range_q`.
DATE_FILTERS = {
    "saabus_alates": ("received_date", "start"),
    "saabus_kuni": ("received_date", "end"),
    "tahtaeg_alates": ("response_deadline", "start"),
    "tahtaeg_kuni": ("response_deadline", "end"),
}


def filter_by_reporting_year(queryset: Any, value: str) -> Any:
    """Apply `?aasta=`, using the same condition the year chart counted with.

    Accepts `YYYY`, `YYYY-YYYY`, or the word that asks for the bucket with no
    usable year. An unreadable value empties the list rather than being ignored:
    a filter chip saying "2O24" above the whole register would be a lie the
    reader has no way to catch.
    """
    if value == selectors.UNKNOWN_YEAR:
        return queryset.filter(selectors.unknown_register_year_q())

    parts = value.split("-")
    try:
        if len(parts) == 1:
            first = last = int(parts[0])
        elif len(parts) == 2:
            first, last = int(parts[0]), int(parts[1])
        else:
            return queryset.none()
    except ValueError:
        return queryset.none()

    if first > last:
        return queryset.none()
    return queryset.filter(selectors.register_year_q(start=first, end=last))


def apply_date_filters(queryset: Any, params: Any) -> tuple[Any, dict[str, str]]:
    """``?saabus_alates=`` and its three siblings, as two closed intervals.

    An unreadable date empties the list rather than being ignored, for the same
    reason an unreadable year does: a chip reading "31.02.2024" above the whole
    register is a lie the reader has no way to catch.

    The parameter is read the way an Estonian writes a date — `7.9.2026` — and
    ISO is still accepted, because every link, bookmark and saved query written
    before the date system was made Estonian carries ISO and must keep working
    (app/core/dates.py).

    The echo is what the control redisplays, and it is the *parsed* date
    rendered Estonian rather than the raw string. A URL carrying ISO therefore
    shows `7.9.2026` in the box above the results, which is the point: one date
    format reaches the reader regardless of what reached the server.
    """
    from app.core.dates import format_estonian_date, parse_flexible_date

    bounds: dict[str, dict[str, Any]] = {}
    echo: dict[str, str] = {}
    for parameter, (field, edge) in DATE_FILTERS.items():
        raw = (params.get(parameter) or "").strip()
        if not raw:
            continue
        parsed = parse_flexible_date(raw)
        if parsed is None:
            # The raw text is echoed back so the reader can see what was
            # refused, rather than an empty box above an empty register.
            echo[parameter] = raw
            return queryset.none(), echo
        echo[parameter] = format_estonian_date(parsed)
        bounds.setdefault(field, {})[edge] = parsed

    for field, edges in bounds.items():
        queryset = queryset.filter(
            selectors.date_range_q(field, start=edges.get("start"), end=edges.get("end"))
        )
    return queryset, echo


def filter_by_opinion_state(queryset: QuerySet[Matter], value: str) -> QuerySet[Matter]:
    """Apply `?arvamus=`: has the drafting step been recorded as finished.

    Read from the derived ``CurrentRegisterState`` row, and only about the one
    fact that table is authoritative for. ``opinion_sent_recorded`` asks whether
    the register *wrote* anything in VÄLJA, not whether what it wrote parses as
    a date. Those differ on fourteen current Matters in the approved snapshot,
    and reading the parsed date's nullability reported all fourteen as
    unfinished work (app/matters/dashboard.py).
    """
    from app.legacy_import.current_state import RegisterCurrency

    if value not in {OPINION_DRAFTING, OPINION_SENT}:
        return queryset.none()
    return queryset.filter(
        current_register_state__currency=RegisterCurrency.CURRENT,
        current_register_state__opinion_sent_recorded=value == OPINION_SENT,
    )


def apply_register_filters(
    queryset: QuerySet[Matter],
    user: Any,
    params: Any,
    *,
    today: date | None = None,
) -> tuple[QuerySet[Matter], dict[str, str]]:
    """Narrow an already-authorized register queryset by every `?` parameter.

    Returns the queryset and the date echo the controls redisplay. Free text
    (`?q=`) is applied by the view *before* this, so that everything here
    narrows an already-searched population and the count beside the search box
    is the count of the list under it.
    """
    status = params.get("olek", "avatud")
    if status == "avatud":
        queryset = queryset.filter(is_open=True)
    elif status == "suletud":
        queryset = queryset.filter(is_open=False)
    elif status == "arhiiv":
        queryset = queryset.filter(record_mode=RecordMode.ARCHIVE)

    if params.get("ulatus", "koik") == "minu":
        queryset = queryset.filter(Q(owner=user) | Q(collaborators=user))

    # `puudub` is the one word every dimension uses for "this field is empty",
    # so that the *Määramata* bucket on a chart is a link like any other bar
    # rather than a number the reader has to take on trust (Stage-2E brief 42).
    if owner_id := params.get("vastutaja"):
        if owner_id == selectors.MISSING:
            queryset = queryset.filter(owner__isnull=True)
        else:
            # A hand-edited URL must not reach the database as a malformed UUID.
            try:
                queryset = queryset.filter(owner_id=uuid.UUID(owner_id))
            except ValueError:
                queryset = queryset.none()
    if stage_key := params.get("hetkeseis"):
        if stage_key == selectors.MISSING:
            queryset = queryset.filter(stage__isnull=True)
        else:
            queryset = queryset.filter(stage__key=stage_key)
    if track := params.get("menetlusliik"):
        queryset = queryset.filter(track="" if track == selectors.MISSING else track)
    if area_key := params.get("valdkond"):
        if area_key == selectors.MISSING:
            queryset = queryset.filter(policy_areas__isnull=True)
        else:
            queryset = queryset.filter(policy_areas__key=area_key)
    if tag_key := params.get("silt"):
        queryset = queryset.filter(tags__key=tag_key)
    if mode := params.get("liik"):
        queryset = queryset.filter(record_mode=mode)
    if origin := params.get("paritolu"):
        # Comma-separated, because one statistic legitimately means two origins:
        # a register row is `LEGACY_IMPORT` or an archive row somebody activated
        # (`PROMOTED_LEGACY`), and the bar counting both has to open both.
        queryset = queryset.filter(origin__in=origin.split(","))
    if year := params.get("aasta"):
        queryset = filter_by_reporting_year(queryset, year)
    if source := params.get("allikas"):
        if source == SOURCE_SEVERAL:
            queryset = queryset.annotate(
                source_page_count=Count("source_pages", distinct=True)
            ).filter(source_page_count__gte=2)
        else:
            has_page = Exists(MatterSourcePage.objects.filter(matter=OuterRef("pk")))
            queryset = queryset.annotate(has_source_page=has_page).filter(
                has_source_page=source == SOURCE_PRESENT
            )
    if action_filter := params.get("tegevus"):
        queryset = selectors.filter_by_next_action(queryset, user, action_filter, today)
    if opinion := params.get("arvamus"):
        queryset = filter_by_opinion_state(queryset, opinion)
    for parameter, (field, many) in ORGANISATION_FILTERS.items():
        raw = params.get(parameter)
        if not raw:
            continue
        if raw == selectors.MISSING:
            # "No sender recorded" over a plural relation is still one condition:
            # the outer join produces a single null row for a Matter with no
            # link at all, so no duplicate is possible here.
            queryset = queryset.filter(**{f"{field}__isnull": True})
            continue
        try:
            queryset = queryset.filter(**{f"{field}__id": uuid.UUID(raw)})
        except ValueError:
            queryset = queryset.none()
        else:
            if many:
                queryset = queryset.distinct()
    # The convenience filter, beside the two precise ones rather than instead of
    # them. Nothing stored is collapsed: this is an OR over two columns that
    # keep their separate meanings (Stage-2E.1 brief 11F).
    if involved := params.get("asutus"):
        try:
            queryset = queryset.filter(
                selectors.organisation_involved_q(uuid.UUID(involved))
            ).distinct()
        except ValueError:
            queryset = queryset.none()
    if materials := params.get("materjalid"):
        queryset = selectors.filter_by_materials(queryset, user, materials)
    # After every authorization-bearing filter above, and applied to the scoped
    # queryset rather than the raw table. Data class narrows what is already
    # visible; it never decides what is visible (Agent-C brief 14, 50).
    queryset = selectors.filter_by_data_class(
        queryset, params.get("andmed", selectors.DATA_CLASS_ALL)
    )

    return apply_date_filters(queryset, params)


def register_population(user: Any, params: Any, *, today: date | None = None) -> QuerySet[Matter]:
    """The exact rows ``matters:matter_list?<params>`` would page through.

    ``.distinct()`` because that is what the register paginates, and a count
    taken without it would exceed the list by however many join rows a plural
    filter produced — which is precisely the "card says 15, list shows 14"
    failure this module exists to make impossible.

    Deliberately *not* ``matter_list_queryset``: that annotates six correlated
    subqueries so a row can render *viimane tegevus*, and a KPI renders no rows
    at all. The visibility scope is identical, which is the part that has to be.
    """
    queryset, _ = apply_register_filters(Matter.objects.visible_to(user), user, params, today=today)
    return queryset.distinct()
