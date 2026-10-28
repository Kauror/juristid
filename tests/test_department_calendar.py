"""Osakond's calendar — Minu asjad's calendar, department-wide, one colour per person.

`calendar.build_calendar(reader, None, …)`: the same periods, navigation and
placement rules over the work model unnarrowed, each work dot in the colour of
the person *responsible* for it (`WorkItem.responsible`), commencements in their
own neutral diamond. What can go wrong quietly — a person's colour that moves
between requests, an action coloured by the Matter's owner instead of its
responsible person, a name in the legend from a row the reader may not see, a
restricted record reaching a dot or a count, a query per person or per day — is
asserted on the built calendar; the page and its HTMX swaps on the HTML.

«Today» is pinned: Monday 26 October 2026, the personal calendar's own example.
"""

from __future__ import annotations

import sys
from datetime import date, timedelta

import pytest
from django.db import connection
from django.http import QueryDict
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from app.core import authorization
from app.core.enums import Visibility
from app.matters import calendar as cal
from app.matters import work_items as wi
from app.matters.department import build_department
from app.workflow.enums import ActionKind, DatePrecision, DateSemantics
from tests import factories

pytestmark = pytest.mark.django_db

TODAY = date(2026, 10, 26)
PAGE = "/osakond/"


@pytest.fixture
def frozen(monkeypatch):
    """The view reads `timezone.localdate()` itself; hold it on `TODAY`."""
    monkeypatch.setattr(timezone, "localdate", lambda *args, **kwargs: TODAY)
    return TODAY


def build(user, today=TODAY, items=None, **params):
    query = QueryDict(mutable=True)
    for key, value in params.items():
        query[key] = value
    return cal.build_calendar(
        user,
        None,
        params=query,
        today=today,
        query_for=lambda changes: "?" + "&".join(f"{k}={v}" for k, v in changes.items() if v),
        items=items,
    )


def action(matter, when, *, responsible=None, text="Pikk järgmise sammu kirjeldus", **extra):
    return factories.NextActionFactory(
        matter=matter,
        text=text,
        kind=extra.pop("kind", ActionKind.DO),
        date_semantics=extra.pop("semantics", DateSemantics.DEADLINE),
        target_date=when,
        date_precision=extra.pop("precision", DatePrecision.EXACT),
        responsible=responsible or matter.owner,
        **extra,
    )


def day_events(calendar, day):
    for week in calendar.weeks or [w for grid in calendar.months for w in grid.weeks]:
        for cell in week.days:
            if cell.day == day and cell.in_period:
                return cell.events
    raise AssertionError(f"{day} is not on this calendar")


def narrowed_to_normal(monkeypatch, user):
    """Make ``user`` a reader of NORMAL content only, with no participation.

    Since docs/adr/0181 no real role reads NORMAL without RESTRICTED: a business
    reader reads both, everybody else neither. The narrowing every `visible_to`
    applies is what this file guards, so the narrowed scope is built by hand
    (`Scope(..., sees_restricted_by_role=False)`, as tests/test_scoped_counting.py
    does) and handed to every module that imported `scope_for_user` by name.
    """
    original = authorization.scope_for_user

    def scope(candidate):
        if getattr(candidate, "pk", None) == user.pk:
            return authorization.Scope(
                user=candidate,
                is_authenticated=True,
                sees_restricted_by_role=False,
                break_glass_grant_id=None,
            )
        return original(candidate)

    for module in list(sys.modules.values()):
        if getattr(module, "scope_for_user", None) is original:
            monkeypatch.setattr(module, "scope_for_user", scope)


# ---------------------------------------------------------------------------
# A. colours
# ---------------------------------------------------------------------------


def test_a_persons_colour_is_a_stable_function_of_their_id():
    people = [factories.UserFactory() for _ in range(25)]

    first = [cal.person_colour(person) for person in people]
    # Another instance of the same row — another request, another page.
    again = [cal.person_colour(type(person).objects.get(pk=person.pk)) for person in people]

    assert first == again
    assert all(1 <= slot <= cal.PERSON_COLOURS for slot in first)
    # A hash, not a constant: twenty-five people do not all land on one colour.
    assert len(set(first)) > 3


def test_the_palette_is_defined_for_both_themes():
    from pathlib import Path

    tokens = Path("static/css/tokens.css").read_text(encoding="utf-8")
    for slot in range(1, cal.PERSON_COLOURS + 1):
        assert tokens.count(f"--calendar-person-{slot}:") == 2, slot
    assert tokens.count("--calendar-person-none:") == 2


def test_several_people_and_events_on_one_day(specialist, other_specialist):
    day = date(2026, 10, 28)
    action(factories.MatterFactory(owner=specialist, title="A"), day)
    action(factories.MatterFactory(owner=specialist, title="B"), day)
    action(factories.MatterFactory(owner=other_specialist, title="C"), day)

    events = day_events(build(specialist), day)

    # One dot per dated activity: the same person twice is two dots, one colour.
    assert [event.person for event in events].count(specialist) == 2
    assert [event.person for event in events].count(other_specialist) == 1
    classes = {event.person.pk: event.person_class for event in events}
    assert classes[specialist.pk] == f"p{cal.person_colour(specialist)}"
    assert classes[other_specialist.pk] == f"p{cal.person_colour(other_specialist)}"


def test_an_action_is_coloured_by_its_responsible_person_not_the_matters_owner(
    specialist, other_specialist
):
    matter = factories.MatterFactory(owner=specialist, title="Kellegi teise samm")
    action(matter, date(2026, 10, 29), responsible=other_specialist)

    calendar = build(specialist)
    (event,) = day_events(calendar, date(2026, 10, 29))

    assert event.person == other_specialist
    assert event.person_class == f"p{cal.person_colour(other_specialist)}"
    assert [person.name for person in calendar.people] == [other_specialist.display_name]


def test_a_deadline_is_its_owners_by_the_work_models_rule(specialist):
    matter = factories.MatterFactory(owner=specialist)
    factories.ImportantDateFactory(matter=matter, date_value=date(2026, 10, 30), title="Tähtaeg")

    (event,) = day_events(build(specialist), date(2026, 10, 30))

    assert event.person == specialist


def test_nobody_responsible_is_neutral_and_invents_nobody(specialist):
    matter = factories.MatterFactory(owner=None, title="Omanikuta teema")
    factories.ImportantDateFactory(matter=matter, date_value=date(2026, 10, 30), title="Tähtaeg")

    calendar = build(specialist)
    (event,) = day_events(calendar, date(2026, 10, 30))

    assert event.person is None
    assert event.person_class == "none"
    assert event.person_label == cal.NO_PERSON_LABEL
    assert [(p.name, p.colour) for p in calendar.people] == [(cal.NO_PERSON_LABEL, "none")]


def test_a_commencement_is_nobodys_colour(specialist):
    matter = factories.MatterFactory(owner=specialist)
    factories.EffectiveDateFactory(matter=matter, date_value=date(2026, 10, 30))

    calendar = build(specialist)
    (event,) = day_events(calendar, date(2026, 10, 30))

    assert event.category == cal.CATEGORY_COMMENCEMENT
    assert event.person is None
    assert calendar.people == []


def test_the_legend_holds_only_people_with_a_row_in_the_shown_period(specialist, other_specialist):
    action(factories.MatterFactory(owner=specialist), date(2026, 10, 28))
    # Outside the four weeks (19.10–15.11): no dot, so no legend name.
    action(factories.MatterFactory(owner=other_specialist), date(2026, 12, 20))

    assert [p.name for p in build(specialist).people] == [specialist.display_name]
    december = build(specialist, kalender="kuu", kalendri_aeg="2026-12")
    assert [p.name for p in december.people] == [other_specialist.display_name]
    # The year view counts per month and colours nothing.
    assert build(specialist, kalender="aasta").people == []


# ---------------------------------------------------------------------------
# B. authorization
# ---------------------------------------------------------------------------


def test_a_restricted_matter_and_a_restricted_child_contribute_nothing(
    monkeypatch, specialist, other_specialist
):
    """No dot, no count, no legend name — for a reader narrowed to NORMAL content."""
    outsider = factories.UserFactory(display_name="Piiratud vastutaja")
    narrowed_reader = factories.UserFactory()

    hidden = factories.MatterFactory(
        owner=other_specialist, title="Piiratud teema", visibility=Visibility.RESTRICTED
    )
    action(hidden, date(2026, 10, 28), responsible=outsider)
    factories.EffectiveDateFactory(matter=hidden, date_value=date(2026, 10, 28))

    readable = factories.MatterFactory(owner=specialist, title="Avalik teema")
    action(
        readable,
        date(2026, 10, 28),
        responsible=outsider,
        visibility_override=Visibility.RESTRICTED,
        text="piiratud samm",
    )
    factories.EffectiveDateFactory(
        matter=readable,
        date_value=date(2026, 10, 28),
        visibility_override=Visibility.RESTRICTED,
    )

    # A business reader sees them all (docs/adr/0181) …
    everything = build(specialist)
    assert len(day_events(everything, date(2026, 10, 28))) == 4
    assert "Piiratud vastutaja" in [p.name for p in everything.people]

    # … the NORMAL-only reader sees none of them, anywhere.
    narrowed_to_normal(monkeypatch, narrowed_reader)
    for view in ("4n", "kuu", "kvartal", "aasta"):
        calendar = build(narrowed_reader, kalender=view)
        assert calendar.events == [], view
        assert calendar.people == [], view
        assert calendar.counts_label == "sündmusi ei ole", view
        assert all(tile.total == 0 for tile in calendar.year_months), view

    # Through the page's own read of the work model, the same.
    page = build_department(narrowed_reader, today=TODAY, calendar_query_for=lambda changes: "?")
    assert page.calendar is not None
    assert page.calendar.events == []
    assert page.calendar.people == []


def test_an_account_without_business_read_sees_no_calendar_content(specialist, administrator):
    action(factories.MatterFactory(owner=specialist), date(2026, 10, 28))
    factories.EffectiveDateFactory(
        matter=factories.MatterFactory(owner=specialist), date_value=date(2026, 10, 28)
    )

    for view in ("4n", "kuu", "kvartal", "aasta"):
        calendar = build(administrator, kalender=view)
        assert calendar.events == [] and calendar.people == [], view


# ---------------------------------------------------------------------------
# C. what the calendar holds
# ---------------------------------------------------------------------------


def test_it_is_the_whole_department_unnarrowed(specialist, other_specialist, department_head):
    action(factories.MatterFactory(owner=specialist, title="Minu"), date(2026, 10, 28))
    action(factories.MatterFactory(owner=other_specialist, title="Kolleegi"), date(2026, 10, 29))
    factories.EffectiveDateFactory(
        matter=factories.MatterFactory(owner=other_specialist, title="Jõustub"),
        date_value=date(2026, 10, 30),
        description="jõustumine",
    )

    for reader in (specialist, department_head):
        titles = sorted(str(event.matter.title) for event in build(reader).events)
        assert titles == ["Jõustub", "Kolleegi", "Minu"], reader


def test_the_pages_own_items_give_the_same_calendar_as_a_bounded_read(specialist, other_specialist):
    action(factories.MatterFactory(owner=specialist), date(2026, 10, 28))
    action(factories.MatterFactory(owner=other_specialist), date(2026, 11, 3))
    action(factories.MatterFactory(owner=other_specialist), date(2025, 3, 1), text="ammu")
    action(
        factories.MatterFactory(owner=other_specialist),
        date(2026, 1, 1),
        precision=DatePrecision.YEAR,
        text="aasta",
    )

    items = wi.work_items(specialist, today=TODAY)
    for view in ("4n", "kuu", "kvartal", "aasta"):
        shared = build(specialist, items=items, kalender=view)
        bounded = build(specialist, kalender=view)
        assert [e.key for e in shared.events] == [e.key for e in bounded.events], view


def test_an_approximate_date_is_never_a_day(specialist):
    matter = factories.MatterFactory(owner=specialist)
    action(matter, date(2026, 11, 1), precision=DatePrecision.MONTH, text="novembris")
    action(
        factories.MatterFactory(owner=specialist),
        date(2026, 10, 1),
        precision=DatePrecision.QUARTER,
        text="IV kvartal",
    )

    month = build(specialist, kalender="kuu", kalendri_aeg="2026-11")
    assert day_events(month, date(2026, 11, 1)) == []
    assert [e.text for e in month.months[0].month_events] == ["novembris"]
    assert [e.text for e in month.periods] == ["IV kvartal"]
    weeks = build(specialist)
    assert sorted(e.text for e in weeks.periods) == ["IV kvartal", "novembris"]


@pytest.mark.parametrize(
    ("view", "raw", "start", "end"),
    [
        ("kuu", "2028-02", date(2028, 2, 1), date(2028, 2, 29)),
        ("kvartal", "2026-K4", date(2026, 10, 1), date(2026, 12, 31)),
        ("aasta", "2027", date(2027, 1, 1), date(2027, 12, 31)),
        ("4n", "2026-12-30", date(2026, 12, 28), date(2027, 1, 24)),
    ],
)
def test_leap_days_and_year_boundaries(specialist, view, raw, start, end):
    action(factories.MatterFactory(owner=specialist), date(2028, 2, 29), text="liigpäev")
    action(factories.MatterFactory(owner=specialist), date(2027, 1, 1), text="uusaasta")

    calendar = build(specialist, kalender=view, kalendri_aeg=raw)

    assert (calendar.start, calendar.end) == (start, end)
    texts = {event.text for event in calendar.events}
    assert ("liigpäev" in texts) is (start <= date(2028, 2, 29) <= end)
    assert ("uusaasta" in texts) is (start <= date(2027, 1, 1) <= end)


# ---------------------------------------------------------------------------
# D. the page and its swaps
# ---------------------------------------------------------------------------


def _rail(body: str) -> str:
    return body.split('class="ovbody__rail"')[1]


@pytest.mark.parametrize("scope", ["osakond", "valdkonniti"])
def test_the_calendar_is_first_in_the_rail_on_both_scopes(client, specialist, frozen, scope):
    client.force_login(specialist)
    body = client.get(PAGE, {"vaade": scope}).content.decode()

    rail = _rail(body)
    assert 'id="osakonna-kalender"' in rail
    assert 'id="minu-kalender"' not in body
    first_block = rail.split('<section class="railblock')[1]
    assert 'id="osakonna-kalender"' in first_block
    for label in ("4 nädalat", "Kuu", "Kvartal", "Aasta"):
        assert f">{label}</a>" in rail


@pytest.mark.parametrize("view", ["4n", "kuu", "kvartal", "aasta"])
@pytest.mark.parametrize("scope", ["osakond", "valdkonniti"])
def test_every_view_swaps_alone_and_keeps_the_scope(client, specialist, frozen, view, scope):
    action(factories.MatterFactory(owner=specialist, title="Kalendri teema"), TODAY)
    client.force_login(specialist)

    response = client.get(
        PAGE,
        {"vaade": scope, "kalender": view},
        HTTP_HX_REQUEST="true",
        HTTP_HX_TARGET="osakonna-kalender",
    )

    assert response.status_code == 200
    body = response.content.decode()
    assert body.lstrip().startswith("<section")
    assert 'id="osakonna-kalender"' in body
    assert 'hx-target="#osakonna-kalender"' in body
    assert "Vajab sekkumist" not in body and "Aruandlus" not in body
    calendar = response.context["calendar"]
    assert calendar.is_department and calendar.view == view
    for query in (calendar.previous_query, calendar.next_query, *(v.query for v in calendar.views)):
        assert f"vaade={scope}" in query, query
    if view != "aasta":
        assert "Kalendri teema" in body


def test_a_history_restore_gets_the_whole_page(client, specialist, frozen):
    client.force_login(specialist)

    response = client.get(
        PAGE,
        {"kalender": "kuu"},
        HTTP_HX_REQUEST="true",
        HTTP_HX_TARGET="osakonna-kalender",
        HTTP_HX_HISTORY_RESTORE_REQUEST="true",
    )

    assert "Aruandlus" in response.content.decode()


def test_the_personal_calendars_target_does_not_swap_the_department_page(
    client, specialist, frozen
):
    client.force_login(specialist)

    response = client.get(
        PAGE, {"kalender": "kuu"}, HTTP_HX_REQUEST="true", HTTP_HX_TARGET="minu-kalender"
    )

    assert "Aruandlus" in response.content.decode()


def test_calendar_links_keep_the_pages_other_parameters(client, department_head, frozen):
    client.force_login(department_head)

    response = client.get(PAGE, {"periood": "kvartal", "kalender": "kuu"})
    calendar = response.context["page"].calendar

    for query in (calendar.previous_query, calendar.next_query, *(v.query for v in calendar.views)):
        assert query.startswith(PAGE + "?"), query
        assert "periood=kvartal" in query and "vaade=osakond" in query, query


def test_the_popover_entry_is_person_then_teema_then_meaning(
    client, specialist, other_specialist, frozen
):
    matter = factories.MatterFactory(owner=specialist, title="Kompaktne teema")
    action(
        matter,
        date(2026, 10, 28),
        responsible=other_specialist,
        text="See pikk järgmise sammu tekst ei kuulu osakonna kalendrisse",
    )
    client.force_login(specialist)

    body = client.get(PAGE).content.decode()
    rail = _rail(body)

    assert "See pikk järgmise sammu tekst" not in rail
    entry = rail.split("wcal__event--compact")[1].split("</li>")[0]
    assert other_specialist.display_name in entry
    assert entry.index(other_specialist.display_name) < entry.index("Kompaktne teema")
    assert " — " in entry
    assert f'href="{reverse("matters:matter_detail", kwargs={"pk": matter.pk})}' in entry
    assert wi.MEANING_DEADLINE in entry
    # Day popover: the heading states the day; the entry does not repeat it.
    assert "28.10.2026" not in entry
    # The legend names the person beside the colour.
    legend = rail.split('class="wcal__people"')[1].split("</ul>")[0]
    assert other_specialist.display_name in legend
    assert f"wcal__dot--p{cal.person_colour(other_specialist)}" in legend


def test_an_overdue_row_keeps_its_state_in_words(client, specialist, frozen):
    action(factories.MatterFactory(owner=specialist, title="Hilinenud"), date(2026, 10, 20))
    client.force_login(specialist)

    rail = _rail(client.get(PAGE).content.decode())
    entry = rail.split("wcal__event--compact")[1].split("</li>")[0]

    assert "üle tähtaja" in entry
    assert "is-overdue" in entry


def test_the_minu_asjad_calendar_keeps_its_own_entries(client, specialist, frozen):
    """The compact format is the department's; the personal calendar keeps its text."""
    action(
        factories.MatterFactory(owner=specialist, title="Isiklik teema"),
        date(2026, 10, 28),
        text="Isikliku kalendri samm",
    )
    client.force_login(specialist)

    body = client.get(reverse("matters:my_work")).content.decode()

    assert "Isikliku kalendri samm" in body
    assert "wcal__event--compact" not in body
    assert "wcal__people" not in body


# ---------------------------------------------------------------------------
# E. dates inside a day popover (requirement 2: both calendars)
# ---------------------------------------------------------------------------


def _day_popover(body: str, day: date) -> str:
    """The day's popover — after its trigger, so neither the trigger's name nor a
    dot's ``title`` is counted as a date the popover repeats."""
    label = f'aria-label="{day:%d.%m.%Y}:'
    return body.split(label)[1].split('class="wcal__pop"')[1].split("</details>")[0]


@pytest.mark.parametrize("path", ["matters:my_work", "matters:department"])
def test_a_day_popover_does_not_repeat_its_day_under_each_entry(client, specialist, frozen, path):
    # An `Oluline tähtaeg` rather than a step: a step's «Nihuta kuupäeva» form
    # is a nested disclosure that carries its own date field.
    factories.ImportantDateFactory(
        matter=factories.MatterFactory(owner=specialist, title="Esimene"),
        date_value=date(2026, 10, 28),
    )
    factories.EffectiveDateFactory(
        matter=factories.MatterFactory(owner=specialist, title="Teine"),
        date_value=date(2026, 10, 28),
    )
    client.force_login(specialist)

    popover = _day_popover(client.get(reverse(path)).content.decode(), date(2026, 10, 28))

    assert "Esimene" in popover and "Teine" in popover
    # Once — the heading — and never again under an entry.
    assert popover.count("28.10.2026") == 1


@pytest.mark.parametrize("path", ["matters:my_work", "matters:department"])
def test_an_inferred_day_keeps_its_qualification_in_the_popover(client, specialist, frozen, path):
    factories.EffectiveDateFactory(
        matter=factories.MatterFactory(owner=specialist, title="Tuletatud"),
        date_value=date(2026, 10, 28),
        date_precision=DatePrecision.INFERRED,
    )
    client.force_login(specialist)

    popover = _day_popover(client.get(reverse(path)).content.decode(), date(2026, 10, 28))

    assert "tuletatud tekstist" in popover


@pytest.mark.parametrize("path", ["matters:my_work", "matters:department"])
def test_the_lists_under_the_grid_keep_their_dates(client, specialist, frozen, path):
    action(
        factories.MatterFactory(owner=specialist, title="Kuu jooksul"),
        date(2026, 11, 1),
        precision=DatePrecision.MONTH,
    )
    action(
        factories.MatterFactory(owner=specialist, title="Kvartali jooksul"),
        date(2026, 10, 1),
        precision=DatePrecision.QUARTER,
    )
    client.force_login(specialist)

    body = client.get(
        reverse(path), {"kalender": "kuu", "kalendri_aeg": "2026-11"}
    ).content.decode()
    month_list = body.split("kuu jooksul, päev teadmata")[1].split("</ul>")[0]
    period_list = body.split("Pikem periood, päev teadmata")[1].split("</ul>")[0]

    assert "november 2026" in month_list
    assert "IV kvartal 2026" in period_list


# ---------------------------------------------------------------------------
# F. bounded reads
# ---------------------------------------------------------------------------


def _calendar_queries(user, **params):
    with CaptureQueriesContext(connection) as captured:
        calendar = build(user, **params)
    return len(captured), calendar


@pytest.mark.parametrize("view", ["4n", "kuu", "kvartal", "aasta"])
def test_the_statement_count_does_not_grow_with_people_or_days(specialist, view):
    action(factories.MatterFactory(owner=specialist), TODAY)
    small, _ = _calendar_queries(specialist, kalender=view)

    people = [factories.UserFactory() for _ in range(8)]
    for n in range(48):
        owner = people[n % len(people)]
        matter = factories.MatterFactory(owner=owner)
        action(matter, TODAY + timedelta(days=n % 5), responsible=people[(n + 3) % len(people)])
        factories.EffectiveDateFactory(matter=matter, date_value=TODAY + timedelta(days=n % 5))

    large, calendar = _calendar_queries(specialist, kalender=view)

    # 26.–30.10 is inside every default period.
    assert len(calendar.events) == 97
    if view != "aasta":
        assert len(calendar.people) == 9
    assert large == small
    assert large <= 14


def test_the_page_adds_no_second_work_read(client, specialist, frozen, monkeypatch):
    """The rail's calendar is built from `build_department`'s one read of the work model."""
    calls = []
    original = wi.work_items

    def counted(*args, **kwargs):
        calls.append(kwargs)
        return original(*args, **kwargs)

    monkeypatch.setattr(wi, "work_items", counted)
    client.force_login(specialist)

    response = client.get(PAGE)

    assert response.status_code == 200
    assert response.context["page"].calendar is not None
    assert len(calls) == 1
