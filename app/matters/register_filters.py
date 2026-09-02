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
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any
from urllib.parse import urlencode

from django.db.models import Count, Exists, OuterRef, Q, QuerySet
from django.utils import timezone

from app.core.dates import format_estonian_date, parse_flexible_date
from app.legacy_import.source_pages import MatterSourcePage
from app.matters import selectors
from app.matters.enums import RecordMode
from app.matters.models import Matter
from app.workflow.dates import year_from

#: The register's results region. Every link that lands a reader on rows
#: rather than on the filter panel above them carries it.
#:
#: Here rather than in either page that uses it, because both did: Ülevaade
#: owned it, Osakonna töö copied it, and the merged page inherited two
#: definitions of one string (docs/adr/0049, DUP-04).
RESULTS_ANCHOR = "#tulemused"

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

#: What `?too=` selects: a named population of the shared dated-work read model
#: (:mod:`app.matters.work_items`), as Matters.
#:
#: It exists because four Ülevaade figures count *work* rather than open
#: actions. "12 üle tähtaja" includes an ``Oluline tähtaeg`` whose day has
#: passed, and an important deadline is not a NextAction — so `?tegevus=` could
#: never express it, and the link opened a list shorter than the number above
#: it. A list shorter than its own count reads as a bug in the count.
#:
#: Nothing new is measured here. The values are the read model's own
#: populations, resolved by the read model's own function, so the figure and the
#: list are one selector called twice (master specification 18.9).
WORK_PARAM = "too"

#: Narrows `?too=` to the work one person is responsible for, which is a
#: different question from who owns the Matter — and deliberately so: a
#: NextAction belongs to whoever must do it, an ``Oluline tähtaeg`` to the
#: Matter's owner, and Ülevaade prints the two side by side rather than summing
#: them (master specification 18.1). `?vastutaja=` answers the ownership half;
#: this answers the responsibility half.
WORK_RESPONSIBLE_PARAM = "too_vastutaja"

#: The window `?too=tahtaeg-vahemik` reads, both ends inclusive. Companions to
#: `?too=` exactly as `?too_vastutaja=` is: they narrow it and do nothing on
#: their own. Separate from `?tahtaeg_alates=`, which filters a Matter's own
#: `Arvamuse tähtaeg` column — a different fact from the dated work model, and
#: conflating them would give the same words two meanings in one URL
#: (app/matters/work_items.py).
WORK_WINDOW_START_PARAM = "too_alates"
WORK_WINDOW_END_PARAM = "too_kuni"

#: The year a Matter was closed in. Its own parameter rather than `?aasta=`,
#: which is the *reporting* year — the year the file belongs to, not the year
#: the department finished it (Ülevaade's Aruandlus rail asks the second).
CLOSED_YEAR_PARAM = "suletud"

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
    # When the record was opened here, as distinct from when the material
    # arrived. Saabunud counts «loodud sel nädalal» and «loodud sel kuul», and a
    # figure with no list behind it is a figure this product does not print — so
    # the pair exists to give those two numbers the population they name
    # (01-EHITUSJUHIS §3.3). `created_at` is a timestamp, so it is compared on
    # the local date, exactly as `sent_at` is on the department digest.
    "loodud_alates": ("created_at__date", "start"),
    "loodud_kuni": ("created_at__date", "end"),
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


def filter_by_work_state(
    queryset: QuerySet[Matter],
    user: Any,
    value: str,
    responsible_raw: str = "",
    today: date | None = None,
    window_start_raw: str = "",
    window_end_raw: str = "",
) -> QuerySet[Matter]:
    """Apply `?too=`, through the read model Ülevaade counted with.

    The population is resolved by :func:`app.matters.work_items.work_population_ids`
    and nothing is re-derived here — which is what makes "Ülevaade says 12" and
    "the register lists 12" the same statement rather than two similar ones.

    An unrecognised value empties the list rather than being ignored, for the
    reason every other filter in this module does: a chip above the whole
    register is a lie the reader has no way to catch.
    """
    from app.matters import work_items as wi

    if value not in wi.WORK_POPULATION_LABELS:
        return queryset.none()

    items: list[Any] | None = None
    responsible: Any = wi.ANY_PERSON
    if responsible_raw:
        responsible = None
        if responsible_raw != selectors.MISSING:
            try:
                responsible = uuid.UUID(responsible_raw)
            except ValueError:
                return queryset.none()
        items = [
            item
            for item in wi.work_items(user, today=today)
            if (item.responsible.pk if item.responsible is not None else None) == responsible
        ]
    # An unreadable window empties the list rather than being ignored, for the
    # same reason an unreadable date does anywhere else in this module: a chip
    # above the whole register is a lie the reader has no way to catch.
    window: tuple[date, date | None] | None = None
    if window_start_raw or window_end_raw:
        start = parse_flexible_date(window_start_raw) if window_start_raw else today
        end = parse_flexible_date(window_end_raw) if window_end_raw else None
        if start is None or (window_end_raw and end is None):
            return queryset.none()
        window = (start or (today or timezone.localdate()), end)

    ids = wi.work_population_ids(
        user, value, today=today, items=items, responsible=responsible, window=window
    )
    return queryset.filter(pk__in=ids)


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
    if closed_year := params.get(CLOSED_YEAR_PARAM):
        # `closed_at`, not the reporting year. "Suletud teemasid 2026" on the
        # Aruandlus rail counts the files the department *finished* this year,
        # which is a different question from which year a file belongs to — a
        # 2024 consultation closed in 2026 is one of 2026's completions.
        # An unreadable year empties the list rather than being ignored, so the
        # rows never contradict the chip above them. A year outside the
        # supported range is unreadable in the same way, and used to pass this
        # test and raise inside the ORM instead (CORR-02).
        year = year_from(closed_year)
        if year is None:
            queryset = queryset.none()
        else:
            queryset = queryset.filter(closed_at__year=year)
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
    if work_state := params.get(WORK_PARAM):
        queryset = filter_by_work_state(
            queryset,
            user,
            work_state,
            (params.get(WORK_RESPONSIBLE_PARAM) or "").strip(),
            today,
            (params.get(WORK_WINDOW_START_PARAM) or "").strip(),
            (params.get(WORK_WINDOW_END_PARAM) or "").strip(),
        )
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


def as_text(params: Any) -> dict[str, str]:
    """A parameter mapping as the register reads one: every value a string.

    The view is handed a ``QueryDict``, where every value already is one. A KPI
    counting *through* this pipeline is handed a plain dict it built itself, and
    a UUID or an integer in there reaches code that reasonably calls ``.strip()``
    or ``.split(",")`` on it and raises. Normalising once here means a caller can
    write ``{"suletud": year}`` and get the same answer as the URL it links to
    — which is the whole point of counting through the same call.
    """
    return {
        key: "" if value is None else str(value)
        for key, value in (params.items() if hasattr(params, "items") else ())
    }


def register_population(
    user: Any,
    params: Any,
    *,
    today: date | None = None,
    population: QuerySet[Matter] | None = None,
) -> QuerySet[Matter]:
    """The exact rows ``matters:matter_list?<params>`` would page through.

    ``.distinct()`` because that is what the register paginates, and a count
    taken without it would exceed the list by however many join rows a plural
    filter produced — which is precisely the "card says 15, list shows 14"
    failure this module exists to make impossible.

    Deliberately *not* ``matter_list_queryset``: that annotates six correlated
    subqueries so a row can render *viimane tegevus*, and a KPI renders no rows
    at all. The visibility scope is identical, which is the part that has to be.

    ``population`` is an authorized queryset the caller has already resolved,
    for the one shape that asks this question several times about one reader.
    ``visible_to`` asks the database whether this person holds a break-glass
    grant, so four calls to it for four figures on one page is four identical
    lookups. It must be ``Matter.objects.visible_to(user)`` or a narrowing of
    it; anything wider would be this function counting rows the reader may not
    see (app/core/authorization.py).
    """
    base = Matter.objects.visible_to(user) if population is None else population
    queryset, _ = apply_register_filters(base, user, as_text(params), today=today)
    return queryset.distinct()


# ---------------------------------------------------------------------------
# Saved views
#
# The register has always kept its whole state in the URL: a narrowing is a link
# somebody can bookmark, paste into a chat and reach with Back (master
# specification 7.4). A saved view is therefore not a stored object — it is a
# *named* URL, and naming four of them removes the four filter combinations
# everybody rebuilds by hand every morning (design handoff 2d).
#
# Nothing here introduces persistence. There is no model, no user-preferences
# table and no session state: adding one would give the product a second place
# where "what am I looking at" lives, and the first place already survives a
# refresh, a colleague and a browser restart.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SavedView:
    """One named narrowing, its count, and the link that reproduces it."""

    key: str
    label: str
    params: dict[str, str]
    count: int
    active: bool
    #: Rendered in the warning tint. Reserved for a view whose whole point is
    #: that it should be empty, so a number beside it is a number to act on.
    warn: bool = False

    @property
    def query(self) -> str:
        return urlencode(self.params)


def _month_bounds(today: date) -> tuple[date, date]:
    """The first and last day of ``today``'s month."""
    first = today.replace(day=1)
    last = date(
        first.year + (1 if first.month == 12 else 0),
        1 if first.month == 12 else first.month + 1,
        1,
    ) - timedelta(days=1)
    return first, last


def saved_view_definitions(today: date) -> list[tuple[str, str, dict[str, str], bool]]:
    """The four approved views, as the register parameters that define them.

    The parameters *are* the definition. The count is produced by running
    exactly these through the register's own filter pipeline and the link is
    exactly these as a query string, so a chip reading *12* opens twelve rows —
    the same discipline every figure on Ülevaade already keeps
    (app/matters/dashboard.py, master specification 18.9).
    """
    first, last = _month_bounds(today)
    return [
        ("minu", "Minu avatud", {"olek": "avatud", "ulatus": "minu"}, False),
        (
            "kuu",
            "Tähtaeg sel kuul",
            {
                "olek": "avatud",
                "tahtaeg_alates": format_estonian_date(first),
                "tahtaeg_kuni": format_estonian_date(last),
            },
            False,
        ),
        (
            "vastutajata",
            "Vastutajata",
            {"olek": "avatud", "vastutaja": selectors.MISSING},
            True,
        ),
        ("osakond", "Kogu osakond", {"olek": "avatud"}, False),
    ]


def _current_view_params(params: Any) -> dict[str, str]:
    """What the reader is looking at now, comparably to a view's definition.

    `?leht=` is pagination rather than a narrowing, and an absent `olek` means
    `avatud` — that is the register's own default, so a bare `/teemad/` and
    `/teemad/?olek=avatud` are the same view and must not light up two
    different chips.
    """
    current = {
        str(key): str(value)
        for key, value in (params.items() if hasattr(params, "items") else ())
        if value and str(key) != "leht"
    }
    current.setdefault("olek", "avatud")
    return current


def saved_views(user: Any, params: Any, *, today: date | None = None) -> list[SavedView]:
    """The chip strip above the filters, counted for this reader.

    Four counts, whatever the register holds — the query cost does not grow with
    the number of rows, the number of colleagues or the page somebody is on.
    """
    today = today or timezone.localdate()
    current = _current_view_params(params)
    # Resolved once for all four. `visible_to` asks the database about
    # break-glass grants, and four chips are not four different readers.
    population = Matter.objects.visible_to(user)
    return [
        SavedView(
            key=key,
            label=label,
            params=view_params,
            count=register_population(
                user, view_params, today=today, population=population
            ).count(),
            active=current == view_params,
            warn=warn,
        )
        for key, label, view_params, warn in saved_view_definitions(today)
    ]
