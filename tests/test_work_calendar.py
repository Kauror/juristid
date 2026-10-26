"""Kalender on Minu asjad — one calendar of dated work and recorded commencements.

docs/adr/0158, amendment of 2026-10-26. The calendar is a read-only presentation
of two canonical sources: the shared work model and active `MatterEffectiveDate`
rows. The failures that matter are the quiet ones — a quarter drawn on its first
day, one record counted twice, a commencement dressed as a missed deadline, a
restricted record reaching a dot or a count — so most tests read the built
calendar rather than the HTML.

«Today» is pinned: Monday 26 October 2026, the brief's own example.
"""

from __future__ import annotations

import datetime
from datetime import date

import pytest
from django.db import connection
from django.http import QueryDict
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from app.core.enums import Visibility
from app.intelligence.enums import FactStatus
from app.matters import calendar as cal
from app.matters.my_work import build_my_work
from app.workflow.enums import (
    ActionKind,
    ActionStatus,
    DatePrecision,
    DateSemantics,
    Disposition,
)
from tests import factories

pytestmark = pytest.mark.django_db

TODAY = date(2026, 10, 26)
MY_WORK = reverse("matters:my_work")


def build(user, subject=None, today=TODAY, **params):
    query = QueryDict(mutable=True)
    for key, value in params.items():
        query[key] = value
    return cal.build_calendar(
        user,
        subject or user,
        params=query,
        today=today,
        query_for=lambda changes: "?" + "&".join(f"{k}={v}" for k, v in changes.items() if v),
    )


def action(
    matter,
    when,
    *,
    responsible=None,
    precision=DatePrecision.EXACT,
    text="Tegevus",
    kind=ActionKind.DO,
    semantics=DateSemantics.DEADLINE,
    status=ActionStatus.OPEN,
):
    return factories.NextActionFactory(
        matter=matter,
        text=text,
        kind=kind,
        date_semantics=semantics,
        target_date=when,
        date_precision=precision,
        responsible=responsible or matter.owner,
        status=status,
    )


def commence(matter, start, end=None, *, precision=DatePrecision.EXACT, **extra):
    return factories.EffectiveDateFactory(
        matter=matter,
        date_value=start,
        period_end=end or start,
        date_precision=precision,
        **extra,
    )


def day_events(calendar, day):
    for week in calendar.weeks or [w for grid in calendar.months for w in grid.weeks]:
        for cell in week.days:
            if cell.day == day and cell.in_period:
                return cell.events
    raise AssertionError(f"{day} is not on this calendar")


def placed_keys(calendar):
    """Every event key as the calendar places it: on days, in month lists, in periods."""
    keys = []
    weeks = calendar.weeks or [w for grid in calendar.months for w in grid.weeks]
    for week in weeks:
        for cell in week.days:
            keys += [event.key for event in cell.events]
    for grid in calendar.months:
        keys += [event.key for event in grid.month_events]
    keys += [event.key for event in calendar.periods]
    return keys


# ---------------------------------------------------------------------------
# A. the four views and their periods
# ---------------------------------------------------------------------------


def test_the_default_is_four_whole_weeks_from_the_previous_monday(specialist):
    calendar = build(specialist)

    assert calendar.view == cal.VIEW_WEEKS
    assert (calendar.start, calendar.end) == (date(2026, 10, 19), date(2026, 11, 15))
    assert len(calendar.weeks) == 4
    assert [week.days[0].day.weekday() for week in calendar.weeks] == [0, 0, 0, 0]
    assert [week.days[-1].day.weekday() for week in calendar.weeks] == [6, 6, 6, 6]
    todays = [cell for week in calendar.weeks for cell in week.days if cell.is_today]
    assert [cell.day for cell in todays] == [TODAY]
    assert calendar.shows_today is False


@pytest.mark.parametrize(
    ("view", "raw", "start", "end"),
    [
        ("kuu", "2026-02", date(2026, 2, 1), date(2026, 2, 28)),
        ("kuu", "2028-02", date(2028, 2, 1), date(2028, 2, 29)),
        ("kvartal", "2026-K4", date(2026, 10, 1), date(2026, 12, 31)),
        ("kvartal", "2027-K1", date(2027, 1, 1), date(2027, 3, 31)),
        ("aasta", "2027", date(2027, 1, 1), date(2027, 12, 31)),
        # A Wednesday in the address still starts the weeks on its Monday.
        ("4n", "2026-12-30", date(2026, 12, 28), date(2027, 1, 24)),
    ],
)
def test_each_view_reads_its_period_from_the_address(specialist, view, raw, start, end):
    calendar = build(specialist, kalender=view, kalendri_aeg=raw)

    assert (calendar.start, calendar.end) == (start, end)
    assert calendar.shows_today is not (start <= TODAY <= end)


@pytest.mark.parametrize("raw", ["", "2026-13", "abc", "1800-01", "2026-K5", "2026-1"])
def test_an_unreadable_period_falls_back_to_the_current_one(specialist, raw):
    calendar = build(specialist, kalender="kuu", kalendri_aeg=raw)

    assert calendar.start == date(2026, 10, 1)


def test_an_unknown_view_is_the_default(specialist):
    assert build(specialist, kalender="nadal").view == cal.VIEW_WEEKS


@pytest.mark.parametrize(
    ("view", "raw", "previous", "following"),
    [
        ("kuu", "2026-01", "2025-12", "2026-02"),
        ("kuu", "2026-12", "2026-11", "2027-01"),
        ("kvartal", "2026-K1", "2025-K4", "2026-K2"),
        ("kvartal", "2026-K4", "2026-K3", "2027-K1"),
        ("aasta", "2026", "2025", "2027"),
        ("4n", "2026-12-14", "2026-11-16", "2027-01-11"),
    ],
)
def test_previous_and_next_cross_month_quarter_and_year(specialist, view, raw, previous, following):
    calendar = build(specialist, kalender=view, kalendri_aeg=raw)

    assert f"kalendri_aeg={previous}" in calendar.previous_query
    assert f"kalendri_aeg={following}" in calendar.next_query


def test_a_leap_february_has_its_twenty_ninth(specialist):
    calendar = build(specialist, kalender="kuu", kalendri_aeg="2028-02")
    days = [cell.day for week in calendar.months[0].weeks for cell in week.days if cell.in_period]

    assert days[-1] == date(2028, 2, 29)
    assert len(days) == 29


def test_the_quarter_shows_three_months_and_the_year_twelve(specialist):
    assert len(build(specialist, kalender="kvartal").months) == 3
    year = build(specialist, kalender="aasta")
    assert [tile.month.month for tile in year.year_months] == list(range(1, 13))
    assert year.weeks == [] and year.months == []


def test_today_returns_to_the_current_period_and_the_default_writes_no_parameter(specialist):
    calendar = build(specialist, kalender="kuu", kalendri_aeg="2027-05")

    assert "kalendri_aeg=2026-10" in calendar.today_query
    four_weeks = next(link for link in calendar.views if link.key == cal.VIEW_WEEKS)
    assert four_weeks.query == "?"


# ---------------------------------------------------------------------------
# B. the two sources, and what each record means
# ---------------------------------------------------------------------------


def test_work_and_commencements_share_a_day_and_stay_distinct(specialist):
    matter = factories.MatterFactory(owner=specialist, title="Kaubandusseadus")
    action(matter, date(2026, 11, 12), text="Saadan arvamuse")
    commence(matter, date(2026, 11, 12), description="põhiosa")

    events = day_events(build(specialist), date(2026, 11, 12))

    assert [event.category for event in events] == [cal.CATEGORY_WORK, cal.CATEGORY_COMMENCEMENT]
    work, commencement = events
    assert work.meaning == "PLAANIS" and work.text == "Saadan arvamuse"
    assert commencement.meaning == "Kirjas olev jõustumise kuupäev"
    assert commencement.url.endswith("#joustumine")
    assert not commencement.is_overdue


def test_several_commencements_of_one_matter_are_several_events(specialist):
    matter = factories.MatterFactory(owner=specialist)
    commence(matter, date(2026, 11, 2), description="põhiosa")
    commence(matter, date(2026, 11, 2), description="§ 12")
    commence(matter, date(2026, 11, 9), description="register kaob")

    calendar = build(specialist)

    assert [e.text for e in day_events(calendar, date(2026, 11, 2))] == ["põhiosa", "§ 12"]
    assert [e.text for e in day_events(calendar, date(2026, 11, 9))] == ["register kaob"]
    assert calendar.commencement_count == 3


def test_cancelled_and_superseded_commencements_are_not_shown(specialist):
    matter = factories.MatterFactory(owner=specialist)
    commence(matter, date(2026, 11, 3), description="kehtiv")
    commence(matter, date(2026, 11, 3), description="tühistatud", status=FactStatus.CANCELLED)
    commence(matter, date(2026, 11, 3), description="asendatud", status=FactStatus.SUPERSEDED)

    assert [e.text for e in day_events(build(specialist), date(2026, 11, 3))] == ["kehtiv"]


def test_a_closed_matters_commencement_is_shown_and_its_work_is_not(specialist):
    matter = factories.MatterFactory(
        owner=specialist,
        is_open=False,
        disposition=Disposition.COMPLETED,
        closed_at=timezone.now(),
    )
    commence(matter, date(2026, 11, 4), description="pärast sulgemist")
    action(matter, date(2026, 11, 4), text="suletud teema töö")

    assert [e.text for e in day_events(build(specialist), date(2026, 11, 4))] == [
        "pärast sulgemist"
    ]


def test_only_the_subjects_matters_and_work_are_on_their_calendar(specialist, other_specialist):
    mine = factories.MatterFactory(owner=specialist)
    theirs = factories.MatterFactory(owner=other_specialist)
    commence(theirs, date(2026, 11, 5), description="kolleegi jõustumine")
    action(theirs, date(2026, 11, 5), text="kolleegi töö")
    # A step on my Matter that a colleague is responsible for is theirs.
    action(mine, date(2026, 11, 5), text="kolleegi samm minu teemal", responsible=other_specialist)

    assert day_events(build(specialist), date(2026, 11, 5)) == []
    texts = [e.text for e in day_events(build(other_specialist), date(2026, 11, 5))]
    assert sorted(texts) == ["kolleegi jõustumine", "kolleegi samm minu teemal", "kolleegi töö"]


def test_a_planned_action_is_on_the_calendar_and_says_so(specialist):
    matter = factories.MatterFactory(owner=specialist)
    action(matter, date(2026, 11, 6), text="hiljem", status=ActionStatus.PLANNED)

    (event,) = day_events(build(specialist), date(2026, 11, 6))
    assert event.status == "planeeritud tegevus"
    assert f"#planeeritud-tehtud-{event.item.object_id}" in event.url


def test_overdue_work_is_late_and_a_ripe_review_and_a_past_commencement_are_not(specialist):
    matter = factories.MatterFactory(owner=specialist)
    late_day = date(2026, 10, 20)
    action(matter, late_day, text="hilinenud")
    other = factories.MatterFactory(owner=specialist)
    action(
        other,
        late_day,
        text="ootan vastust",
        kind=ActionKind.WAIT,
        semantics=DateSemantics.REVIEW_ON,
    )
    commence(factories.MatterFactory(owner=specialist), late_day, description="möödunud")

    events = {e.text: e for e in day_events(build(specialist), late_day)}

    assert events["hilinenud"].is_overdue and events["hilinenud"].status == "üle tähtaja"
    assert not events["ootan vastust"].is_overdue
    assert events["ootan vastust"].status == "ülevaatamise aeg on käes"
    assert not events["möödunud"].is_overdue
    assert events["möödunud"].status == "Kirjas olev kuupäev on möödunud"


def test_an_open_step_suppresses_its_files_response_deadline_as_the_work_model_does(specialist):
    """No duplicate: the calendar places exactly what `work_items` holds."""
    matter = factories.MatterFactory(owner=specialist, response_deadline=date(2026, 11, 10))
    action(matter, date(2026, 11, 10), text="Koostan arvamuse")

    events = day_events(build(specialist), date(2026, 11, 10))

    assert [e.text for e in events] == ["Koostan arvamuse"]


def test_every_record_is_placed_exactly_once(specialist):
    matter = factories.MatterFactory(owner=specialist, response_deadline=date(2026, 11, 11))
    commence(matter, date(2026, 11, 1), date(2026, 11, 30), precision=DatePrecision.MONTH)
    commence(matter, date(2026, 10, 1), date(2026, 12, 31), precision=DatePrecision.QUARTER)
    commence(matter, date(2026, 11, 2), description="päev")
    action(
        factories.MatterFactory(owner=specialist),
        date(2026, 11, 1),
        precision=DatePrecision.MONTH,
        text="kuu jooksul",
    )

    for view, raw in (("4n", ""), ("kuu", "2026-11"), ("kvartal", "2026-K4")):
        calendar = build(specialist, kalender=view, kalendri_aeg=raw)
        keys = placed_keys(calendar)
        assert len(keys) == len(set(keys)) == len(calendar.events), view


# ---------------------------------------------------------------------------
# C. precision — no invented day
# ---------------------------------------------------------------------------


def test_a_month_is_listed_under_its_month_and_never_on_a_day(specialist):
    matter = factories.MatterFactory(owner=specialist)
    commence(
        matter,
        date(2026, 11, 1),
        date(2026, 11, 30),
        precision=DatePrecision.MONTH,
        description="novembris",
    )

    month = build(specialist, kalender="kuu", kalendri_aeg="2026-11")
    assert [e.text for e in month.months[0].month_events] == ["novembris"]
    assert day_events(month, date(2026, 11, 1)) == []
    assert month.periods == []

    weeks = build(specialist)
    assert day_events(weeks, date(2026, 11, 1)) == []
    assert [e.text for e in weeks.periods] == ["novembris"]
    assert weeks.periods[0].display_date == "november 2026"


def test_a_quarter_is_a_period_once_and_never_its_first_day(specialist):
    matter = factories.MatterFactory(owner=specialist)
    commence(
        matter,
        date(2026, 10, 1),
        date(2026, 12, 31),
        precision=DatePrecision.QUARTER,
        description="IV kvartal",
    )

    quarter = build(specialist, kalender="kvartal", kalendri_aeg="2026-K4")
    assert [e.text for e in quarter.periods] == ["IV kvartal"]
    assert all(grid.month_events == [] for grid in quarter.months)
    assert day_events(quarter, date(2026, 10, 1)) == []
    assert quarter.commencement_count == 1

    year = build(specialist, kalender="aasta", kalendri_aeg="2026")
    assert sum(tile.commencement_count for tile in year.year_months) == 0
    assert [e.text for e in year.periods] == ["IV kvartal"]


def test_a_half_year_is_not_the_first_of_january(specialist):
    matter = factories.MatterFactory(owner=specialist)
    commence(
        matter,
        date(2027, 1, 1),
        date(2027, 6, 30),
        precision=DatePrecision.HALF_YEAR,
        description="I poolaasta",
    )

    year = build(specialist, kalender="aasta", kalendri_aeg="2027")
    january = year.year_months[0]

    assert january.commencement_count == 0
    assert [e.display_date for e in year.periods] == ["I poolaasta 2027"]
    january_month = build(specialist, kalender="kuu", kalendri_aeg="2027-01")
    assert day_events(january_month, date(2027, 1, 1)) == []
    assert [e.text for e in january_month.periods] == ["I poolaasta"]


def test_a_year_precision_record_reaches_a_later_window(specialist):
    """A year anchors on 1 January; the bounded read still finds it in December."""
    matter = factories.MatterFactory(owner=specialist)
    action(matter, date(2026, 1, 1), precision=DatePrecision.YEAR, text="aasta jooksul")

    december = build(specialist, kalender="kuu", kalendri_aeg="2026-12")

    assert [e.text for e in december.periods] == ["aasta jooksul"]


def test_an_inferred_day_is_on_its_day_with_its_qualification(specialist):
    matter = factories.MatterFactory(owner=specialist)
    commence(matter, date(2026, 11, 13), precision=DatePrecision.INFERRED, description="tuletatud")

    (event,) = day_events(build(specialist), date(2026, 11, 13))
    assert event.precision_label == "tuletatud tekstist"


def test_the_year_counts_days_and_months_in_their_month_once(specialist):
    matter = factories.MatterFactory(owner=specialist)
    commence(matter, date(2026, 11, 2))
    commence(matter, date(2026, 11, 1), date(2026, 11, 30), precision=DatePrecision.MONTH)
    action(matter, date(2026, 11, 20))
    action(factories.MatterFactory(owner=specialist), date(2026, 3, 3))

    tiles = {tile.month.month: tile for tile in build(specialist, kalender="aasta").year_months}

    assert (tiles[11].work_count, tiles[11].commencement_count) == (1, 2)
    assert (tiles[3].work_count, tiles[3].commencement_count) == (1, 0)
    assert sum(tile.total for tile in tiles.values()) == 4
    assert "kalender=kuu" in tiles[11].query and "kalendri_aeg=2026-11" in tiles[11].query


# ---------------------------------------------------------------------------
# D. a busy day
# ---------------------------------------------------------------------------


def test_a_busy_day_shows_three_dots_and_the_rest_as_plus_n(specialist):
    for n in range(5):
        commence(
            factories.MatterFactory(owner=specialist, title=f"Teema {n}"), date(2026, 11, 16 - 3)
        )

    cell = next(
        c for week in build(specialist).weeks for c in week.days if c.day == date(2026, 11, 13)
    )

    assert len(cell.dots) == 3
    assert cell.more == 2
    assert len(cell.events) == 5
    assert cell.label == "13.11.2026: 5 jõustumist"


# ---------------------------------------------------------------------------
# E. authorization — the reader is the principal
# ---------------------------------------------------------------------------


def test_an_account_without_business_read_sees_nothing_of_a_colleagues_desk(
    specialist, administrator
):
    matter = factories.MatterFactory(owner=specialist)
    commence(matter, date(2026, 11, 3))
    action(matter, date(2026, 11, 3))

    calendar = build(administrator, subject=specialist)

    assert calendar.events == []
    assert all(
        tile.total == 0 for tile in build(administrator, specialist, kalender="aasta").year_months
    )


def test_restricted_records_follow_the_readers_scope(specialist, reader, administrator):
    readable = factories.MatterFactory(owner=specialist, title="Avalik")
    commence(
        readable,
        date(2026, 11, 3),
        description="piiratud kirje",
        visibility_override=Visibility.RESTRICTED,
    )
    hidden = factories.MatterFactory(owner=specialist, visibility=Visibility.RESTRICTED)
    commence(hidden, date(2026, 11, 3), description="piiratud teema")
    action(hidden, date(2026, 11, 3), text="piiratud töö")

    # docs/adr/0181: every business reader reads them …
    texts = sorted(e.text for e in build(reader, subject=specialist).events)
    assert texts == ["piiratud kirje", "piiratud teema", "piiratud töö"]
    # … and an account with no business read sees no dot and no count.
    year = build(administrator, subject=specialist, kalender="aasta")
    assert year.events == [] and year.periods == []
    assert all(tile.total == 0 for tile in year.year_months)


def test_a_colleagues_page_shows_their_calendar_to_the_department_head(
    client, specialist, department_head
):
    commence(
        factories.MatterFactory(owner=specialist, title="Kolleegi teema"),
        date(2026, 11, 3),
        description="kolleegi jõustumine",
    )
    client.force_login(department_head)

    response = client.get(
        reverse("matters:person_work", kwargs={"pk": specialist.pk}),
        {"kalender": "kuu", "kalendri_aeg": "2026-11"},
    )

    assert response.status_code == 200
    assert "kolleegi jõustumine" in response.content.decode()
    assert response.context["calendar"].events[0].matter.owner == specialist


def test_a_colleague_cannot_open_another_desk_or_its_calendar(client, specialist, other_specialist):
    client.force_login(other_specialist)
    url = reverse("matters:person_work", kwargs={"pk": specialist.pk})

    assert client.get(url).status_code == 404
    assert (
        client.get(url, HTTP_HX_REQUEST="true", HTTP_HX_TARGET="minu-kalender").status_code == 404
    )


# ---------------------------------------------------------------------------
# F. the page
# ---------------------------------------------------------------------------


def test_minu_asjad_renders_the_calendar_below_the_statistics(client, specialist):
    client.force_login(specialist)
    body = client.get(MY_WORK).content.decode()

    assert 'id="minu-kalender"' in body
    assert body.index("Minu statistika") < body.index('id="minu-kalender"')
    for label in ("4 nädalat", "Kuu", "Kvartal", "Aasta"):
        assert f">{label}</a>" in body


def test_the_calendar_swap_returns_only_the_calendar(client, specialist):
    client.force_login(specialist)

    response = client.get(
        MY_WORK, {"kalender": "kuu"}, HTTP_HX_REQUEST="true", HTTP_HX_TARGET="minu-kalender"
    )

    body = response.content.decode()
    assert body.lstrip().startswith("<section")
    assert 'id="minu-kalender"' in body
    assert "Minu statistika" not in body


def test_a_history_restore_gets_the_whole_page(client, specialist):
    client.force_login(specialist)

    response = client.get(
        MY_WORK,
        {"kalender": "kuu"},
        HTTP_HX_REQUEST="true",
        HTTP_HX_TARGET="minu-kalender",
        HTTP_HX_HISTORY_RESTORE_REQUEST="true",
    )

    assert "Minu statistika" in response.content.decode()


def test_calendar_links_keep_the_pages_own_parameters(client, specialist):
    client.force_login(specialist)

    response = client.get(MY_WORK, {"kuni": "koik", "vaade": "sekkumist", "kalender": "kuu"})
    calendar = response.context["calendar"]

    for query in (calendar.previous_query, calendar.next_query, *(v.query for v in calendar.views)):
        assert "kuni=koik" in query and "vaade=sekkumist" in query, query


def test_the_wide_views_open_a_labelled_panel(client, specialist):
    client.force_login(specialist)

    for view in ("kvartal", "aasta"):
        body = client.get(MY_WORK, {"kalender": view}).content.decode()
        assert 'role="dialog"' in body and 'aria-labelledby="wcal-panel-title"' in body
        assert "data-wcal-closelink" in body

    assert 'role="dialog"' not in client.get(MY_WORK, {"kalender": "kuu"}).content.decode()


def test_the_calendar_changes_no_work_figure(specialist):
    """Commencements reach no band, no count and no figure (ADR 0158 §6)."""
    matter = factories.MatterFactory(owner=specialist)
    action(matter, date(2026, 10, 20), text="hilinenud")
    before = build_my_work(specialist, today=TODAY)
    commence(matter, date(2026, 10, 20), description="möödunud jõustumine")
    commence(matter, date(2026, 10, 27), description="tulev jõustumine")
    after = build_my_work(specialist, today=TODAY)

    assert [(f.key, f.value) for f in before.seis] == [(f.key, f.value) for f in after.seis]
    assert before.overdue == after.overdue == 1
    assert [b.total for b in before.bands] == [b.total for b in after.bands]


# ---------------------------------------------------------------------------
# G. bounded reads
# ---------------------------------------------------------------------------


def _queries(user, **params):
    with CaptureQueriesContext(connection) as captured:
        calendar = build(user, **params)
    return len(captured), calendar


@pytest.mark.parametrize("view", ["4n", "kuu", "kvartal", "aasta"])
def test_the_statement_count_does_not_grow_with_the_records(specialist, view):
    first = factories.MatterFactory(owner=specialist)
    commence(first, TODAY)
    action(first, TODAY)
    small, _ = _queries(specialist, kalender=view)
    for n in range(40):
        matter = factories.MatterFactory(owner=specialist)
        commence(matter, TODAY + datetime.timedelta(days=n % 20))
        action(matter, TODAY + datetime.timedelta(days=n % 20))

    large, calendar = _queries(specialist, kalender=view)

    assert calendar.events
    assert large == small
    assert large <= 12


def test_old_overdue_work_outside_the_window_is_not_read(specialist):
    matter = factories.MatterFactory(owner=specialist)
    action(matter, date(2024, 3, 1), text="ammu hilinenud")

    assert build(specialist).events == []
    assert build(specialist, kalender="aasta").events == []


def test_a_period_anchored_before_the_supported_years_does_not_break_the_page(specialist):
    """The year-wide pre-filter reads a 1989 anchor for a 1990 window (review of #550)."""
    matter = factories.MatterFactory(owner=specialist)
    action(matter, date(1989, 1, 1), precision=DatePrecision.YEAR, text="ammune aasta")

    for view, raw in (("kuu", "1990-01"), ("4n", "1990-01-01"), ("aasta", "1990")):
        build(specialist, kalender=view, kalendri_aeg=raw)
