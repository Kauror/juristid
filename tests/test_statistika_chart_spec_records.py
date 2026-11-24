"""UX-008: the shared chart spec and the clickable record cards of `/statistika/`.

The spec (`app.reporting.chart_spec`) names numbers `opinion_stats` has
already worked out; these tests hold it to exactly those numbers, and the page
to exactly the spec's titles and legends. A record card opens what explains
it, and a tie keeps every tied period reachable (overnight development
25.11.2026, docs/adr/0214 §7).
"""

from __future__ import annotations

import datetime as dt
import re
from urllib.parse import parse_qs, urlsplit

import pytest
from django.urls import reverse
from django.utils import timezone
from django.utils.html import escape

from app.legacy_import.lovable_sync_models import OpinionOutcome
from app.reporting import chart_spec
from app.reporting import opinion_stats as stats
from app.reporting.opinions import SOURCE_IMPORTED, MatterFacts, OpinionFact
from tests import factories
from tests.test_statistika_dashboard import _imported

TODAY = dt.date(2026, 11, 16)
_REAL_LOCALDATE = timezone.localdate


def topic(number: int, *, year: int = 2026, owner: str = "Mari", **extra) -> MatterFacts:
    return MatterFacts(
        matter_id=number,
        reference=f"{year}_{number}",
        title=f"Teema {number}",
        year=year,
        received=extra.pop("received", None),
        owner=owner,
        is_eu=extra.pop("eu", False),
        koda_initiative=extra.pop("koda", False),
        instrument="Määramata",
    )


def sent(matter: MatterFacts, on: dt.date, deadline: dt.date | None = None) -> OpinionFact:
    return OpinionFact(matter=matter, sent_on=on, deadline=deadline, source=SOURCE_IMPORTED)


def _records(facts, matters=(), deadlines=None, *, first=2023, last=2026):
    found = stats.trends(
        list(facts), list(matters), deadlines or {}, first=first, last=last, today=TODAY
    )
    return {record.source: record for record in stats.records(list(facts), found, TODAY)}


# ---------------------------------------------------------------------------
# Records keep every period that holds them
# ---------------------------------------------------------------------------


def test_a_record_year_keeps_its_year_and_where_it_was_read_from():
    one = topic(1)
    found = _records([sent(one, dt.date(2026, 3, 5)), sent(one, dt.date(2025, 3, 5))])

    # 2025 and 2026 each hold one: a tie, both kept, oldest first.
    assert [(p.label, p.year, p.month) for p in found["sent"].periods] == [
        ("2025", 2025, None),
        ("2026", 2026, None),
    ]
    assert found["sent"].when == "2025, 2026"


def test_a_tie_of_more_than_three_months_keeps_every_month_not_only_the_three_named():
    one = topic(1, year=2023)
    days = [dt.date(2023, 3, 5), dt.date(2023, 6, 5), dt.date(2024, 1, 7), dt.date(2025, 3, 7)]
    found = _records([sent(one, day) for day in [*days, dt.date(2025, 9, 7)]])

    month = found["month"]
    # The card's line is unchanged: three named and «+2» …
    assert month.when == "märts 2023, juuni 2023, jaanuar 2024 +2"
    # … and the record holds all five, each with its own year and month.
    assert [(p.year, p.month) for p in month.periods] == [
        (2023, 3),
        (2023, 6),
        (2024, 1),
        (2025, 3),
        (2025, 9),
    ]
    assert [p.label for p in month.periods][3:] == ["märts 2025", "september 2025"]


def test_trend_records_name_their_trend():
    a = topic(1, year=2025, received=dt.date(2025, 1, 1), eu=True, koda=True)
    deadlines = {1: dt.date(2025, 1, 21)}
    facts = [sent(a, dt.date(2025, 1, 20), deadline=dt.date(2025, 1, 21))]

    found = _records(facts, [a], deadlines)

    assert set(found) == {"sent", "month", "ontime", "days", "koda", "eu"}
    for key in ("ontime", "days", "koda", "eu"):
        assert [p.year for p in found[key].periods] == [2025]


# ---------------------------------------------------------------------------
# The spec names the results' own numbers
# ---------------------------------------------------------------------------


def _world():
    mari, jaan = topic(1, received=dt.date(2026, 2, 1)), topic(2, owner="Jaan")
    old = topic(3, year=2024, received=dt.date(2024, 1, 1))
    deadlines = {1: dt.date(2026, 2, 11), 3: dt.date(2024, 1, 31)}
    facts = [
        sent(mari, dt.date(2026, 2, 10), deadline=dt.date(2026, 2, 11)),
        sent(mari, dt.date(2026, 5, 10)),
        sent(jaan, dt.date(2026, 5, 12)),
        sent(old, dt.date(2024, 2, 1), deadline=dt.date(2024, 1, 31)),
    ]
    return facts, [mari, jaan, old], deadlines


def test_the_month_spec_holds_each_lawyers_count_and_the_columns_total():
    facts, _matters, _deadlines = _world()
    period = stats.rolling_period("12", TODAY)
    bars, owners = stats.monthly(facts, period)

    spec = chart_spec.monthly(bars, owners, period, "", sent=3)

    assert [series.name for series in spec.series] == owners == ["Jaan", "Mari"]
    assert spec.totals == tuple(bar.total for bar in bars)
    for index, bar in enumerate(bars):
        per_owner = {s.owner: s.count for s in bar.segments}
        assert {series.name: series.values[index] for series in spec.series} == {
            owner: per_owner.get(owner, 0) for owner in owners
        }
        assert sum(series.values[index] or 0 for series in spec.series) == bar.total
    assert spec.categories == tuple(bar.label for bar in bars)
    assert ("Jurist", "Kõik juristid") in spec.filters
    # A rolling year starts mid-month: the spec says so.
    assert any("perioodi piires" in note for note in spec.notes)


def test_the_flow_and_group_specs_hold_the_results_numbers_and_unknown_stays_unknown():
    facts, matters, deadlines = _world()
    period = stats.rolling_period("12", TODAY)
    bars = stats.flow(facts, matters, period)
    rows = stats.by_instrument(facts, matters, deadlines, period)

    flow = chart_spec.flow(bars, period, "Mari")
    table = chart_spec.group("liigid", rows, period, "Mari")

    assert flow.series[0].values == tuple(bar.received for bar in bars)
    assert flow.series[1].values == tuple(bar.sent for bar in bars)
    assert ("Jurist", "Mari") in flow.filters
    assert table.is_table and not flow.is_table
    by_name = {series.name: series for series in table.series}
    assert by_name["Välja saadetud"].values == tuple(row.sent for row in rows)
    assert by_name["Tähtajaks"].values == tuple(row.on_time_share for row in rows)
    assert by_name["Aega vastamiseks"].values == tuple(row.days_given for row in rows)
    # A group with no deadline has no on-time share: None and «—», never 0.
    for value, text in zip(by_name["Tähtajaks"].values, by_name["Tähtajaks"].texts, strict=True):
        assert (value is None) == (text == "—")


def test_a_trend_spec_keeps_a_year_without_data_unknown_and_says_which():
    facts, matters, deadlines = _world()
    found = {
        trend.key: trend
        for trend in stats.trends(facts, matters, deadlines, first=2023, last=2026, today=TODAY)
    }

    days = chart_spec.trend(found["days"], "2023–2026", "")

    assert days.series[0].values == tuple(bar.value for bar in found["days"].bars)
    assert days.series[0].values[0] is None  # 2023: no Teema with both dates
    assert "Andmed puuduvad: 2023, 2025." in days.notes
    assert "Jooksev aasta on pooleli." in days.notes
    assert days.partial == (False, False, False, True)
    assert days.anchor == "trend-days"


# ---------------------------------------------------------------------------
# The page: titles from the spec, record cards that open their period
# ---------------------------------------------------------------------------


@pytest.fixture
def frozen_today(monkeypatch):
    def frozen(value=None, timezone=None):
        return TODAY if value is None else _REAL_LOCALDATE(value, timezone)

    monkeypatch.setattr("django.utils.timezone.localdate", frozen)
    return TODAY


@pytest.fixture
def world(db, frozen_today):
    mari = factories.UserFactory(display_name="Mari Maasikas")
    jaan = factories.UserFactory(display_name="Jaan Tamm")
    may = factories.MatterFactory(owner=mari, title="Näidisseadus mai")
    june = factories.MatterFactory(owner=mari, title="Näidisseadus juuni")
    march = factories.MatterFactory(owner=jaan, title="Näidisseadus märts")
    old = factories.MatterFactory(
        owner=jaan,
        title="Näidisseadus vana",
        reference_year=2025,
        received_date=dt.date(2025, 1, 1),
    )
    _imported(may, OpinionOutcome.SENT, dt.date(2026, 5, 4))
    _imported(june, OpinionOutcome.SENT, dt.date(2026, 6, 1))
    _imported(march, OpinionOutcome.SENT, dt.date(2026, 3, 9))
    _imported(old, OpinionOutcome.SENT, dt.date(2025, 1, 5), request_deadline=dt.date(2025, 1, 11))
    return {"mari": mari, "jaan": jaan}


def _cards(response) -> dict[str, list[str]]:
    """Each record card's title → the links it carries, each one printed on the page."""
    body = response.content.decode()
    section = body[body.index('id="stat-rekordid"') :]
    out = {}
    for item in response.context["record_items"]:
        links = [link["url"] for link in item["links"]]
        for link in links:
            assert f'href="{escape(link)}"' in section, link
        out[item["record"].title] = links
    return out


def test_the_page_reads_its_titles_and_legends_from_the_spec(client, specialist, world):
    client.force_login(specialist)

    response = client.get(reverse("reporting:dashboard"))

    body = response.content.decode()
    charts = response.context["charts"]
    assert list(charts)[:7] == ["kuud", "voog", "liigid", "valdkonnad", "saatjad", "aeg", "otsus"]
    for spec in charts.values():
        assert f'id="{spec.anchor}"' in body, spec.key
        assert spec.title in body
    legend = re.findall(r'statlegend__mark statseries--\w+"></span>([^<]+)</span>', body)
    assert legend[: len(charts["kuud"].series)] == [s.name for s in charts["kuud"].series]


def test_the_flow_columns_print_the_specs_numbers(client, specialist, world):
    client.force_login(specialist)

    response = client.get(reverse("reporting:dashboard"))

    spec = response.context["charts"]["voog"]
    printed = re.findall(r"sisse (\d+), välja (\d+)</span>", response.content.decode())
    assert [(int(a), int(b)) for a, b in printed] == list(
        zip(spec.series[0].values, spec.series[1].values, strict=True)
    )


def test_the_record_year_opens_that_year_and_the_year_holds_the_records_count(
    client, specialist, world
):
    client.force_login(specialist)

    cards = _cards(client.get(reverse("reporting:dashboard")))

    [link] = cards["Välja saadetud arvamuste rekordaasta"]
    assert link == "/statistika/?periood=aasta&aasta=2026#stat-kuud"
    opened = client.get(link.split("#")[0])
    assert opened.context["summary"].sent == 3


def test_a_tied_record_month_links_every_tied_month_each_holding_the_count(
    client, specialist, world
):
    client.force_login(specialist)

    response = client.get(reverse("reporting:dashboard"))
    cards = _cards(response)

    links = cards["Välja saadetud arvamuste rekordkuu"]
    # Four months of one opinion each: every one reachable, none chosen.
    months = [parse_qs(urlsplit(link).query)["alates"][0] for link in links]
    assert months == ["2025-01-01", "2026-03-01", "2026-05-01", "2026-06-01"]
    assert (
        '<summary class="statrecord__toggle" aria-label="Veel 1 sama tulemusega perioodi">+1'
        in (response.content.decode())
    )
    for link in links:
        assert len(client.get(link).context["rows"]) == 1


def test_a_trend_record_opens_the_trend_at_its_year(client, specialist, world):
    client.force_login(specialist)

    response = client.get(reverse("reporting:dashboard") + "?trend=5")
    body = response.content.decode()
    cards = _cards(response)

    [link] = cards["Suurima tähtaegselt esitatud arvamuste osakaaluga aasta"]
    assert link == "/statistika/?trend=5#trend-ontime-2025"
    assert 'id="trend-ontime-2025"' in body
    assert 'id="trend-ontime"' in body


def test_the_lawyer_and_the_period_travel_with_every_record_link(client, specialist, world):
    client.force_login(specialist)

    response = client.get(reverse("reporting:dashboard") + "?periood=6&jurist=Jaan&trend=10")
    cards = _cards(response)

    assert cards
    for title, links in cards.items():
        for link in links:
            query = parse_qs(urlsplit(link).query)
            assert query["jurist"] == ["Jaan"], title
            if "#trend-" in link:
                assert query["periood"] == ["6"] and query["trend"] == ["10"], title
    # Jaan's record year is 2025 or 2026 with one each: a tie, both linked.
    assert len(cards["Välja saadetud arvamuste rekordaasta"]) == 2


def test_record_values_are_unchanged_by_the_links(client, specialist, world):
    client.force_login(specialist)

    response = client.get(reverse("reporting:dashboard"))

    shown = {
        item["record"].title: item["record"].value for item in response.context["record_items"]
    }
    assert shown == {record.title: record.value for record in response.context["records"]}
    assert shown["Välja saadetud arvamuste rekordaasta"] == "3 arvamust"
