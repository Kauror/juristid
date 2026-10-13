"""Jõustumised as a presentation of the Teemad register (docs/adr/0158).

ADR 0071 retired the department-wide «Jõustuvad aktid» page and made
`Jõustumine` a dimension of the register. The owner's decision of 2026-10-13
keeps that: the commencement calendar and list are an *alternative view of the
same filtered register*, reachable only while a Jõustumine filter is active,
and kept in the URL. These tests pin both halves — what the view shows, and
that the ordinary register is exactly what it was.

Every date here is fixed and «today» is pinned through
``app.intelligence.commencements.today``, so a bucket boundary cannot move with
the calendar the suite happens to run on.
"""

from __future__ import annotations

import datetime
from urllib.parse import parse_qs

import pytest
from django.http import QueryDict
from django.urls import reverse
from django.utils import timezone

from app.core.enums import Visibility
from app.intelligence import commencements
from app.intelligence.enums import EffectiveDateKind, FactStatus
from app.matters import register_filters
from app.workflow.enums import DatePrecision, Disposition
from tests import factories

pytestmark = pytest.mark.django_db

REGISTER = reverse("matters:matter_list")
TODAY = datetime.date(2026, 10, 13)


@pytest.fixture(autouse=True)
def pinned_today(monkeypatch):
    monkeypatch.setattr(commencements, "today", lambda: TODAY)
    return TODAY


def commence(matter, start=None, end=None, *, precision=DatePrecision.EXACT, **extra):
    """One commencement. ``end`` defaults to ``start``: a day is its own period."""
    kind = extra.pop("kind", EffectiveDateKind.KNOWN_DATE if start else EffectiveDateKind.UNKNOWN)
    return factories.EffectiveDateFactory(
        matter=matter,
        kind=kind,
        date_value=start,
        period_end=(end or start) if start else None,
        date_precision=precision,
        **extra,
    )


def quarter(year: int, number: int):
    first = datetime.date(year, 3 * number - 2, 1)
    last_month = 3 * number
    last = (
        datetime.date(year + 1, 1, 1)
        if last_month == 12
        else datetime.date(year, last_month + 1, 1)
    ) - datetime.timedelta(days=1)
    return first, last


def listing(client, **params):
    return client.get(
        REGISTER, {"joustumine": "on", "olek": "koik", "vaade": "joustumised", **params}
    )


def rows_in(response) -> dict[str, list[str]]:
    """Each bucket's descriptions, read from what the template renders from."""
    return {
        bucket.key: [entry.record.description for entry in bucket.entries]
        for bucket in response.context["commencement_buckets"]
    }


def every_description(response) -> list[str]:
    return [d for rows in rows_in(response).values() for d in rows]


# ---------------------------------------------------------------------------
# A. the ordinary register is unchanged
# ---------------------------------------------------------------------------


def test_the_bare_register_offers_no_commencement_switch(signed_in):
    factories.MatterFactory(title="Tavaline")
    response = signed_in.get(REGISTER)

    assert "commencement_switch" not in response.context
    body = response.content.decode()
    assert "registerview" not in body
    assert "Jõustumiste kalender" not in body
    assert [m.title for m in response.context["page"].object_list] == ["Tavaline"]


def test_a_commencement_filter_keeps_the_matter_table_and_adds_only_the_switch(signed_in):
    matter = factories.MatterFactory(title="Jõustuv")
    commence(matter, datetime.date(2026, 11, 1), description="põhiosa")

    response = signed_in.get(REGISTER, {"joustumine": "on"})

    assert response.context["commencement_view"] == ""
    assert [m.title for m in response.context["page"].object_list] == ["Jõustuv"]
    body = response.content.decode()
    assert 'class="table table--register"' in body
    assert [option["label"] for option in response.context["commencement_switch"]] == [
        "Teemad",
        "Jõustumiste loend",
        "Jõustumiste kalender",
    ]


@pytest.mark.parametrize(
    "params",
    [
        {"vaade": "joustumised"},
        {"vaade": "kalender", "joustumine": "puudub"},
        {"vaade": "joustumised", "joustumine": "puudub", "joustub_alates": "1.1.2026"},
        {"vaade": "midagi", "joustumine": "on"},
    ],
)
def test_the_view_is_never_a_page_of_its_own(signed_in, params):
    """`?vaade=` means nothing without a commencement to present: the register
    renders its table, exactly as it would without the parameter."""
    factories.MatterFactory(title="Tavaline")

    response = signed_in.get(REGISTER, params)

    assert not response.context.get("commencement_view")
    assert "commencement_buckets" not in response.context
    assert "commencement_calendar" not in response.context


def test_the_retired_destination_still_redirects_to_the_register(signed_in):
    """docs/adr/0071 and 0155: the standalone page stays retired."""
    for path in ("/jalgimine/joustumised/", "/joustuvad-aktid/"):
        response = signed_in.get(path)
        assert response.status_code == 302
        assert response["Location"] == f"{REGISTER}?joustumine=on&olek=koik"


# ---------------------------------------------------------------------------
# B. the list
# ---------------------------------------------------------------------------


def test_several_commencements_on_one_matter_are_several_rows(signed_in):
    matter = factories.MatterFactory(title="Etappidena")
    commence(matter, datetime.date(2026, 10, 20), description="põhiosa")
    commence(matter, datetime.date(2027, 1, 1), description="osad sätted")
    commence(matter, kind=EffectiveDateKind.GENERAL_ORDER, description="rakendusmäärus")

    response = listing(signed_in)

    assert rows_in(response) == {
        "jargmised": ["põhiosa"],
        "hiljem": ["osad sätted"],
        "teadmata": ["rakendusmäärus"],
    }
    assert response.context["commencement_total"] == 3
    # The register still counts the file once.
    assert response.context["total"] == 1


@pytest.mark.parametrize("status", [FactStatus.CANCELLED, FactStatus.SUPERSEDED])
def test_cancelled_and_superseded_are_not_listed(signed_in, status):
    matter = factories.MatterFactory()
    commence(matter, datetime.date(2026, 11, 1), description="kehtiv")
    commence(matter, datetime.date(2026, 11, 2), description="mitte", status=status)

    assert every_description(listing(signed_in)) == ["kehtiv"]


def test_a_quarter_is_written_as_a_quarter_and_never_as_a_day(signed_in):
    matter = factories.MatterFactory()
    first, last = quarter(2027, 3)
    commence(matter, first, last, precision=DatePrecision.QUARTER, description="kvartal")

    body = listing(signed_in).content.decode()

    assert "III kvartal 2027" in body
    assert "kvartali täpsusega" in body
    assert "1.7.2027" not in body
    assert "01.07.2027" not in body


@pytest.mark.parametrize(
    ("start", "end", "precision", "bucket"),
    [
        # A day: inside today … today+30 inclusive, both ends.
        (TODAY, None, DatePrecision.EXACT, "jargmised"),
        (TODAY + datetime.timedelta(days=30), None, DatePrecision.EXACT, "jargmised"),
        (TODAY + datetime.timedelta(days=31), None, DatePrecision.EXACT, "hiljem"),
        (TODAY - datetime.timedelta(days=1), None, DatePrecision.EXACT, "moodunud"),
        # A month wholly inside the next 30 days is in it; one that is not, is not.
        (
            datetime.date(2026, 11, 1),
            datetime.date(2026, 11, 30),
            DatePrecision.MONTH,
            "hiljem",
        ),
        # A quarter that has begun is not past and not «within 30 days».
        (*quarter(2026, 4), DatePrecision.QUARTER, "hiljem"),
        # A quarter is past only once its last day is.
        (*quarter(2026, 3), DatePrecision.QUARTER, "moodunud"),
        (None, None, DatePrecision.EXACT, "teadmata"),
    ],
)
def test_bucket_rules(start, end, precision, bucket):
    record = factories.EffectiveDateFactory.build(
        kind=EffectiveDateKind.KNOWN_DATE if start else EffectiveDateKind.UNKNOWN,
        date_value=start,
        period_end=(end or start) if start else None,
        date_precision=precision,
    )
    assert commencements.bucket_of(record, TODAY) == bucket


def test_a_month_wholly_inside_the_next_thirty_days_is_in_them():
    """1 November with a 30-day window reaching 1 December: the whole of
    November is inside, so it is there by containment."""
    record = factories.EffectiveDateFactory.build(
        date_value=datetime.date(2026, 11, 1),
        period_end=datetime.date(2026, 11, 30),
        date_precision=DatePrecision.MONTH,
    )
    assert commencements.bucket_of(record, datetime.date(2026, 11, 1)) == "jargmised"


def test_a_running_period_says_so(signed_in):
    first, last = quarter(2026, 4)
    commence(factories.MatterFactory(), first, last, precision=DatePrecision.QUARTER)

    body = listing(signed_in).content.decode()

    assert "IV kvartal 2026" in body
    assert "periood kestab" in body


def test_the_window_lists_only_the_commencements_inside_it(signed_in):
    """The Matter is in the register because one of its commencements is in
    the window; the other one is not, and must not be listed under it."""
    matter = factories.MatterFactory(title="Kaks jõustumist")
    commence(matter, datetime.date(2026, 11, 5), description="sees")
    commence(matter, datetime.date(2027, 3, 1), description="väljas")
    first, last = quarter(2026, 4)
    commence(matter, first, last, precision=DatePrecision.QUARTER, description="kvartal")

    response = listing(signed_in, joustub_alates="1.11.2026", joustub_kuni="30.11.2026")

    assert every_description(response) == ["sees"]


def test_an_unreadable_window_lists_nothing(signed_in):
    commence(factories.MatterFactory(), datetime.date(2026, 11, 5))

    response = listing(signed_in, joustub_alates="31.02.2026")

    assert response.context["commencement_buckets"] == []
    assert "Selle valikuga jõustumisi ei leitud." in response.content.decode()


def test_sorting_by_date_descending_and_by_teema(signed_in):
    commence(factories.MatterFactory(title="B teema"), datetime.date(2027, 2, 1), description="b")
    commence(factories.MatterFactory(title="A teema"), datetime.date(2027, 3, 1), description="a")
    commence(factories.MatterFactory(title="C teema"), datetime.date(2027, 1, 1), description="c")

    assert rows_in(listing(signed_in))["hiljem"] == ["c", "b", "a"]
    descending = listing(signed_in, joustumiste_jarjestus="kuupaev_desc")
    assert rows_in(descending)["hiljem"] == ["a", "b", "c"]
    by_matter = listing(signed_in, joustumiste_jarjestus="teema")
    assert rows_in(by_matter)["hiljem"] == ["a", "b", "c"]


def test_the_sort_link_keeps_every_filter(signed_in, specialist):
    commence(factories.MatterFactory(owner=specialist), datetime.date(2027, 2, 1))

    response = listing(signed_in, vastutaja=str(specialist.pk))
    query = parse_qs(response.context["commencement_sort_date_query"])

    assert query["vastutaja"] == [str(specialist.pk)]
    assert query["joustumiste_jarjestus"] == ["kuupaev_desc"]
    assert query["vaade"] == ["joustumised"]


def test_the_empty_state(signed_in):
    response = listing(signed_in)

    assert response.context["commencement_total"] == 0
    assert "Selle valikuga jõustumisi ei leitud." in response.content.decode()


# ---------------------------------------------------------------------------
# C. other register dimensions narrow it, through the register
# ---------------------------------------------------------------------------


def test_responsible_lawyer_and_policy_area_narrow_the_commencements(
    signed_in, specialist, other_specialist
):
    area = factories.PolicyAreaFactory()
    mine = factories.MatterFactory(owner=specialist)
    mine.policy_areas.add(area)
    commence(mine, datetime.date(2027, 1, 1), description="minu")
    theirs = factories.MatterFactory(owner=other_specialist)
    commence(theirs, datetime.date(2027, 1, 1), description="kolleegi")

    by_owner = listing(signed_in, vastutaja=str(specialist.pk))
    assert every_description(by_owner) == ["minu"]

    by_area = listing(signed_in, valdkond=area.key)
    assert every_description(by_area) == ["minu"]


def test_the_register_status_still_applies(signed_in):
    closed = factories.MatterFactory(
        is_open=False, disposition=Disposition.COMPLETED, closed_at=timezone.now()
    )
    commence(closed, datetime.date(2027, 1, 1), description="suletud teema")

    open_only = signed_in.get(REGISTER, {"joustumine": "on", "vaade": "joustumised"})
    assert every_description(open_only) == []
    assert open_only.context["commencement_coverage"]["open_only"] is True

    assert every_description(listing(signed_in)) == ["suletud teema"]


def test_the_coverage_note_counts_the_files_without_a_record(signed_in):
    commence(factories.MatterFactory(), datetime.date(2027, 1, 1))
    factories.MatterFactory(title="Ilma kirjeta")
    factories.MatterFactory(title="Samuti ilma")

    response = listing(signed_in)
    coverage = response.context["commencement_coverage"]

    assert coverage["missing"] == 2
    missing = parse_qs(coverage["missing_query"])
    assert missing["joustumine"] == ["puudub"]
    assert "vaade" not in missing
    body = response.content.decode()
    assert "Kuupäevi ei tuletata pealkirjast" in body


def test_a_title_that_says_joustumine_is_not_a_commencement(signed_in):
    factories.MatterFactory(title="Seadus jõustub 1.1.2027")

    assert listing(signed_in).context["commencement_total"] == 0


# ---------------------------------------------------------------------------
# D. visibility — scoped before anything is counted
# ---------------------------------------------------------------------------


def test_a_restricted_commencement_on_a_readable_matter_is_not_shown(client, reader):
    matter = factories.MatterFactory(title="Avalik teema")
    commence(matter, datetime.date(2027, 1, 1), description="avalik")
    commence(
        matter,
        datetime.date(2027, 1, 2),
        description="salajane",
        visibility_override=Visibility.RESTRICTED,
    )
    client.force_login(reader)

    response = listing(client)
    assert every_description(response) == ["avalik"]
    assert response.context["commencement_total"] == 1
    assert "salajane" not in response.content.decode()

    calendar_view = client.get(
        REGISTER, {"joustumine": "on", "olek": "koik", "vaade": "kalender", "kuu": "2027-01"}
    )
    assert "salajane" not in calendar_view.content.decode()


def test_a_restricted_matters_commencements_never_reach_a_non_participant(client, reader):
    hidden = factories.MatterFactory(title="Salajane teema", visibility=Visibility.RESTRICTED)
    commence(hidden, datetime.date(2027, 1, 1), description="peidus")
    client.force_login(reader)

    response = listing(client)

    assert response.context["commencement_total"] == 0
    assert response.context["commencement_coverage"]["missing"] == 0
    assert "peidus" not in response.content.decode()


def test_a_participant_sees_their_own_restricted_commencement(client, specialist):
    mine = factories.MatterFactory(visibility=Visibility.RESTRICTED, owner=specialist)
    commence(mine, datetime.date(2027, 1, 1), description="minu salajane")
    client.force_login(specialist)

    assert every_description(listing(client)) == ["minu salajane"]


# ---------------------------------------------------------------------------
# E. the calendar
# ---------------------------------------------------------------------------


def calendar_of(client, **params):
    return client.get(REGISTER, {"joustumine": "on", "olek": "koik", "vaade": "kalender", **params})


def cell(response, day: datetime.date):
    for week in response.context["commencement_calendar"].weeks:
        for item in week:
            if item.day == day:
                return item
    raise AssertionError(day)


def test_a_day_goes_on_its_cell_and_a_period_never_does(signed_in):
    matter = factories.MatterFactory()
    commence(matter, datetime.date(2027, 1, 31), description="päev")
    commence(
        matter,
        datetime.date(2027, 1, 1),
        datetime.date(2027, 1, 31),
        precision=DatePrecision.MONTH,
        description="kuu",
    )
    first, last = quarter(2027, 1)
    commence(matter, first, last, precision=DatePrecision.QUARTER, description="kvartal")
    commence(matter, kind=EffectiveDateKind.GENERAL_ORDER, description="üldine")

    response = calendar_of(signed_in, kuu="2027-01")
    calendar_ = response.context["commencement_calendar"]

    assert [e.record.description for e in cell(response, datetime.date(2027, 1, 31)).entries] == [
        "päev"
    ]
    # The anchor day of the month and of the quarter is empty.
    assert cell(response, datetime.date(2027, 1, 1)).entries == []
    assert [e.record.description for e in calendar_.month_entries] == ["kuu"]
    assert [e.record.description for e in calendar_.wider_entries] == ["kvartal"]
    assert calendar_.undated_count == 1
    body = response.content.decode()
    assert "I kvartal 2027" in body
    assert "võib jõustuda ka sel kuul" in body


def test_month_edges(signed_in):
    """31 March 2027 is a Wednesday, so 1 April is drawn in March's last week
    row — outside the month, and empty there."""
    matter = factories.MatterFactory()
    commence(matter, datetime.date(2027, 3, 31), description="märtsi viimane")
    commence(matter, datetime.date(2027, 4, 1), description="aprilli esimene")

    march = calendar_of(signed_in, kuu="2027-03")
    assert [e.record.description for e in cell(march, datetime.date(2027, 3, 31)).entries] == [
        "märtsi viimane"
    ]
    april_first = cell(march, datetime.date(2027, 4, 1))
    assert not april_first.in_month
    assert april_first.entries == []

    april = calendar_of(signed_in, kuu="2027-04")
    assert [e.record.description for e in cell(april, datetime.date(2027, 4, 1)).entries] == [
        "aprilli esimene"
    ]
    assert cell(april, datetime.date(2027, 3, 31)).entries == []


def test_the_week_starts_on_monday(signed_in):
    response = calendar_of(signed_in, kuu="2027-02")
    weeks = response.context["commencement_calendar"].weeks

    assert all(week[0].day.weekday() == 0 for week in weeks)
    assert response.context["commencement_calendar"].label == "Veebruar 2027"


def test_month_navigation(signed_in):
    response = calendar_of(signed_in, kuu="2027-01", vastutaja="puudub")

    previous = parse_qs(response.context["commencement_previous_month"])
    following = parse_qs(response.context["commencement_next_month"])
    assert previous["kuu"] == ["2026-12"]
    assert following["kuu"] == ["2027-02"]
    # Navigation carries the narrowing with it.
    assert previous["vastutaja"] == ["puudub"]
    assert parse_qs(response.context["commencement_this_month"])["kuu"] == ["2026-10"]


@pytest.mark.parametrize(
    ("params", "expected"),
    [
        ({}, datetime.date(2026, 10, 1)),
        ({"kuu": "2027-13"}, datetime.date(2026, 10, 1)),
        ({"kuu": "jama"}, datetime.date(2026, 10, 1)),
        ({"joustub_alates": "15.3.2027"}, datetime.date(2027, 3, 1)),
        ({"joustub_kuni": "15.5.2027"}, datetime.date(2027, 5, 1)),
    ],
)
def test_the_month_the_calendar_opens_on(signed_in, params, expected):
    response = calendar_of(signed_in, **params)

    assert response.context["commencement_calendar"].month == expected


def test_a_day_narrows_the_register_to_itself(signed_in):
    matter = factories.MatterFactory(title="Septembris")
    commence(matter, datetime.date(2027, 9, 27), description="põhiosa")
    commence(factories.MatterFactory(title="Hiljem"), datetime.date(2027, 9, 28))

    response = calendar_of(signed_in, kuu="2027-09")
    query = parse_qs(cell(response, datetime.date(2027, 9, 27)).query)

    assert query["vaade"] == ["joustumised"]
    assert query["joustub_alates"] == ["27.9.2027"]
    assert query["joustub_kuni"] == ["27.9.2027"]
    assert "kuu" not in query

    narrowed = signed_in.get(REGISTER, QueryDict(cell(response, datetime.date(2027, 9, 27)).query))
    assert every_description(narrowed) == ["põhiosa"]
    assert narrowed.context["total"] == 1


def test_the_month_title_opens_the_month_as_a_window(signed_in):
    response = calendar_of(signed_in, kuu="2027-02")
    query = parse_qs(response.context["commencement_month_query"])

    assert query["joustub_alates"] == ["1.2.2027"]
    assert query["joustub_kuni"] == ["28.2.2027"]
    assert query["vaade"] == ["joustumised"]


def test_the_calendar_says_when_a_month_is_empty(signed_in):
    response = calendar_of(signed_in, kuu="2030-06")

    assert "Juuni 2030: selle valikuga jõustumisi ei ole." in response.content.decode()


# ---------------------------------------------------------------------------
# F. the URL is the whole state — chips, Tühjenda kõik, live search
# ---------------------------------------------------------------------------


def test_tuhjenda_koik_clears_the_presentation_too(signed_in):
    response = calendar_of(signed_in, kuu="2027-01")

    assert response.context["cleared_query"] == ""


def test_removing_the_last_commencement_chip_removes_the_view(signed_in):
    response = calendar_of(signed_in, kuu="2027-01", joustub_alates="1.1.2027")
    chips = {
        chip["name"]: parse_qs(chip["remove_query"]) for chip in response.context["active_filters"]
    }

    # `joustumine=on` stays behind `joustub_alates`'s × — the view survives.
    assert chips["joustub_alates"]["vaade"] == ["kalender"]
    # Without `joustumine` the window is still a commencement filter.
    assert chips["joustumine"]["vaade"] == ["kalender"]

    only_on = calendar_of(signed_in, kuu="2027-01")
    chip = next(c for c in only_on.context["active_filters"] if c["name"] == "joustumine")
    remaining = parse_qs(chip["remove_query"])
    assert "vaade" not in remaining
    assert "kuu" not in remaining
    assert remaining["olek"] == ["koik"]


def test_live_search_carries_the_view_and_renders_it_in_the_fragment(signed_in):
    commence(factories.MatterFactory(title="Pakend"), datetime.date(2027, 1, 1), description="p")

    response = signed_in.get(
        REGISTER,
        {"joustumine": "on", "olek": "koik", "vaade": "joustumised"},
        HTTP_HX_REQUEST="true",
    )

    carried = dict(response.context["carried_params"])
    assert carried["vaade"] == "joustumised"
    assert next(t.name for t in response.templates) == "matters/partials/register_results.html"
    assert "Jõustumiste loend" in response.content.decode()


def test_the_panel_keeps_the_view_when_narrowing(signed_in):
    body = listing(signed_in).content.decode()

    assert '<input type="hidden" name="vaade" value="joustumised">' in body


def test_switch_links_keep_every_filter_and_drop_the_presentations_own(signed_in):
    response = calendar_of(signed_in, kuu="2027-01", valdkond="x")
    switch = {
        option["label"]: parse_qs(option["query"])
        for option in response.context["commencement_switch"]
    }

    assert "vaade" not in switch["Teemad"]
    assert "kuu" not in switch["Teemad"]
    assert switch["Teemad"]["valdkond"] == ["x"]
    assert switch["Jõustumiste loend"]["vaade"] == ["joustumised"]


# ---------------------------------------------------------------------------
# G. a commencement is not anybody's deadline
# ---------------------------------------------------------------------------


def test_a_commencement_never_reaches_minu_asjad(signed_in, specialist):
    matter = factories.MatterFactory(owner=specialist)
    commence(
        matter,
        datetime.date.today() + datetime.timedelta(days=2),
        description="Ainulaadne jõustumise kirjeldus",
    )

    body = signed_in.get(reverse("matters:my_work")).content.decode()

    assert "Ainulaadne jõustumise kirjeldus" not in body
    assert not matter.next_actions.exists()


def test_the_window_condition_is_one_rule():
    """The register filter and the listing read the same condition."""
    condition = register_filters.commencement_window_q(
        datetime.date(2026, 7, 1), datetime.date(2026, 8, 31)
    )
    assert "date_value__gte" in str(condition)
    assert "period_end__lte" in str(condition)


def test_tallinn_decides_what_today_is(monkeypatch):
    """23:30 UTC on 31 January is already 1 February in Tallinn."""
    monkeypatch.undo()
    instant = datetime.datetime(2027, 1, 31, 23, 30, tzinfo=datetime.UTC)
    monkeypatch.setattr(timezone, "now", lambda: instant)

    assert commencements.today() == datetime.date(2027, 2, 1)
