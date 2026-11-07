"""UI-04: the rail calendar opens itself in the large panel (owner's brief of 2026-11-06 §7).

The panel is the existing one (`kalendri_aken=1`, owner's review of
2026-10-30); nothing here adds a calendar. What is new is where it is opened
from: the *active* «4 nädalat» or «Kuu» and the KALENDER heading. Both go to
`Calendar.expand_query`: the page's own address, the same view and period,
with only the panel switched on. So the scope (this desk, a colleague's desk,
Osakond and its `vaade`) and every other parameter of the page carry over.
"""

from __future__ import annotations

import re
from datetime import date

import pytest
from django.http import QueryDict
from django.urls import reverse
from django.utils import timezone

from app.matters import calendar as cal
from tests import factories

pytestmark = pytest.mark.django_db

TODAY = date(2026, 11, 6)
MY_WORK = reverse("matters:my_work")
OSAKOND = "/osakond/"


@pytest.fixture
def frozen(monkeypatch):
    monkeypatch.setattr(timezone, "localdate", lambda *args, **kwargs: TODAY)
    return TODAY


def build(user, subject, **params):
    query = QueryDict(mutable=True)
    for key, value in params.items():
        query[key] = value

    def query_for(changes):
        merged = query.copy()
        for key, value in changes.items():
            if value is None:
                merged.pop(key, None)
            else:
                merged[key] = value
        return "?" + merged.urlencode()

    return cal.build_calendar(user, subject, params=query, today=TODAY, query_for=query_for)


def links(calendar):
    return {link.key: link for link in calendar.views}


# ---------------------------------------------------------------------------
# The built calendar
# ---------------------------------------------------------------------------


def test_the_active_four_weeks_opens_the_same_period_in_the_panel(specialist):
    calendar = build(specialist, specialist, kalendri_aeg="2026-12-07")
    active = links(calendar)[cal.VIEW_WEEKS]

    assert active.is_current and active.expands
    assert active.query == calendar.expand_query
    assert "kalendri_aken=1" in active.query
    # The displayed period, not today's: navigation position is kept.
    assert "kalendri_aeg=2026-12-07" in active.query
    assert "kalender=" not in active.query


def test_the_active_month_opens_the_same_month_in_the_panel(specialist):
    calendar = build(specialist, specialist, kalender="kuu", kalendri_aeg="2027-02")
    active = links(calendar)[cal.VIEW_MONTH]

    assert active.expands
    assert "kalender=kuu" in active.query
    assert "kalendri_aeg=2027-02" in active.query
    assert "kalendri_aken=1" in active.query


def test_the_default_four_weeks_expand_without_a_period(specialist):
    calendar = build(specialist, specialist)

    assert calendar.expand_query == "?kalendri_aken=1"


def test_an_inactive_control_still_switches_the_rail_view(specialist):
    calendar = build(specialist, specialist, kalendri_aeg="2026-12-07")
    month = links(calendar)[cal.VIEW_MONTH]

    assert not month.is_current and not month.expands
    assert "kalendri_aken" not in month.query
    assert "kalender=kuu" in month.query


@pytest.mark.parametrize("view", [cal.VIEW_QUARTER, cal.VIEW_YEAR])
def test_quarter_and_year_keep_their_own_behaviour(specialist, view):
    """Always in the panel already: no enlargement, and their links never carry the panel flag."""
    calendar = build(specialist, specialist, kalender=view)

    assert calendar.expand_query == ""
    assert not any(link.expands for link in calendar.views)
    assert "kalendri_aken" not in links(calendar)[view].query


@pytest.mark.parametrize("view", [cal.VIEW_WEEKS, cal.VIEW_MONTH])
def test_inside_the_panel_nothing_expands_again(specialist, view):
    calendar = build(specialist, specialist, kalender=view, kalendri_aken="1")

    assert calendar.is_wide
    assert calendar.expand_query == ""
    assert not any(link.expands for link in calendar.views)


def test_the_page_parameters_ride_along(specialist):
    calendar = build(specialist, specialist, kuni="koik", vaade="sekkumist", kalender="kuu")

    assert "kuni=koik" in calendar.expand_query
    assert "vaade=sekkumist" in calendar.expand_query


# ---------------------------------------------------------------------------
# The pages
# ---------------------------------------------------------------------------


def _expand_href(body, element_id):
    button = r'<button type="button" class="wcal__heading"\s+hx-get="([^"]+)"'
    match = re.search(rf'{button} hx-target="#{element_id}"', body)
    assert match, "the heading is a button that opens the panel"
    return match.group(1).replace("&amp;", "&")


def test_minu_asjad_heading_is_a_button_to_the_panel(client, specialist, frozen):
    client.force_login(specialist)

    body = client.get(MY_WORK, {"kalender": "kuu", "kalendri_aeg": "2026-12"}).content.decode()

    href = _expand_href(body, "minu-kalender")
    assert href.startswith(MY_WORK + "?")
    assert "kalendri_aken=1" in href and "kalendri_aeg=2026-12" in href
    assert 'aria-describedby="minu-kalender-suurem"' in body
    assert 'id="minu-kalender-suurem"' in body
    # The active control is the same request.
    assert (
        re.search(r'class="wcal__view is-active"\s+href="([^"]+)"', body)
        .group(1)
        .replace("&amp;", "&")
        == href
    )


def test_the_panel_opened_from_the_rail_is_the_existing_dialog(client, specialist, frozen):
    client.force_login(specialist)

    response = client.get(
        MY_WORK,
        {"kalender": "kuu", "kalendri_aeg": "2026-12", "kalendri_aken": "1"},
        HTTP_HX_REQUEST="true",
        HTTP_HX_TARGET="minu-kalender",
    )
    body = response.content.decode()

    assert body.lstrip().startswith("<section")
    assert 'role="dialog"' in body and "Kalender · Detsember 2026" in body
    # No heading button inside the open panel: it is open already.
    assert "wcal__heading" not in body


def test_a_colleagues_desk_enlarges_as_that_colleagues_calendar(client, department_head, frozen):
    colleague = factories.UserFactory()
    client.force_login(department_head)
    page = reverse("matters:person_work", kwargs={"pk": colleague.pk})

    body = client.get(page).content.decode()

    assert _expand_href(body, "minu-kalender").startswith(page + "?")


@pytest.mark.parametrize("scope", ["osakond", "valdkonniti"])
def test_the_department_calendar_enlarges_as_the_department(client, specialist, frozen, scope):
    client.force_login(specialist)

    body = client.get(OSAKOND, {"vaade": scope, "kalender": "kuu"}).content.decode()

    href = _expand_href(body, "osakonna-kalender")
    assert href.startswith(OSAKOND + "?")
    assert f"vaade={scope}" in href and "kalender=kuu" in href and "kalendri_aken=1" in href
    assert MY_WORK not in href

    swapped = client.get(
        href, HTTP_HX_REQUEST="true", HTTP_HX_TARGET="osakonna-kalender"
    ).content.decode()
    assert 'id="osakonna-kalender"' in swapped and 'id="minu-kalender"' not in swapped
    assert 'role="dialog"' in swapped
    assert re.search(r'id="wcal-panel-title" tabindex="-1">Osakonna kalender · ', swapped)
