"""The arithmetic of Statistika's first page (`app.reporting.opinion_stats`).

No database: every figure on the page is worked out from two hand-built lists —
`MatterFacts` and `OpinionFact` — against a fixed «today», so what a period
holds, what it is compared with and what each bar says is settled here and
`test_statistika_dashboard` only has to show that the page reads the same lists.
"""

from __future__ import annotations

from datetime import date

import pytest

from app.reporting import opinion_stats as stats
from app.reporting.opinions import SOURCE_IMPORTED, MatterFacts, OpinionFact

TODAY = date(2026, 11, 16)


def topic(
    number: int,
    *,
    year: int | None = 2026,
    received: date | None = None,
    owner: str = "Mari",
    eu: bool = False,
    koda: bool = False,
) -> MatterFacts:
    return MatterFacts(
        matter_id=number,
        reference=f"{year}_{number}" if year else "",
        title=f"Teema {number}",
        year=year,
        received=received,
        owner=owner,
        is_eu=eu,
        koda_initiative=koda,
        instrument="Määramata",
    )


def sent(matter: MatterFacts, on: date, deadline: date | None = None) -> OpinionFact:
    return OpinionFact(matter=matter, sent_on=on, deadline=deadline, source=SOURCE_IMPORTED)


# ---------------------------------------------------------------------------
# Periods
# ---------------------------------------------------------------------------


def test_rolling_twelve_months_end_today_and_compare_with_a_year_earlier():
    period = stats.rolling_period("12", TODAY)

    assert (period.start, period.end) == (date(2025, 11, 17), TODAY)
    assert (period.previous_start, period.previous_end) == (date(2024, 11, 17), date(2025, 11, 16))
    assert period.comparison_label == "Võrdlus sama perioodiga aasta varem"
    assert period.label == "Viimased 12 kuud"
    assert period.key == "12"
    assert period.span == "17.11.2025–16.11.2026"
    assert period.previous_span == "17.11.2024–16.11.2025"


@pytest.mark.parametrize(
    ("key", "start", "previous_start", "previous_end"),
    [
        ("3", date(2026, 8, 17), date(2026, 5, 17), date(2026, 8, 16)),
        ("6", date(2026, 5, 17), date(2025, 11, 17), date(2026, 5, 16)),
    ],
)
def test_rolling_three_and_six_months_compare_with_the_months_just_before(
    key, start, previous_start, previous_end
):
    period = stats.rolling_period(key, TODAY)

    assert (period.start, period.end) == (start, TODAY)
    assert (period.previous_start, period.previous_end) == (previous_start, previous_end)
    assert period.comparison_label == f"Võrdlus vahetult eelnenud {key} kuuga"
    assert period.label == f"Viimased {key} kuud"


@pytest.mark.parametrize(
    ("today", "key", "start", "previous_start", "previous_end"),
    [
        # 31.03 − 3 months = 31.12, a day that exists.
        (date(2026, 3, 31), "3", date(2026, 1, 1), date(2025, 10, 1), date(2025, 12, 31)),
        # 31.05 − 3 months = «31.02», clamped to 28.02.
        (date(2026, 5, 31), "3", date(2026, 3, 1), date(2025, 11, 29), date(2026, 2, 28)),
        # 31.08 − 6 months = «31.02» again.
        (date(2026, 8, 31), "6", date(2026, 3, 1), date(2025, 8, 29), date(2026, 2, 28)),
        # A leap day a year back is 28.02.
        (date(2024, 2, 29), "12", date(2023, 3, 1), date(2022, 3, 1), date(2023, 2, 28)),
    ],
)
def test_a_month_end_is_clamped_to_the_shorter_month(
    today, key, start, previous_start, previous_end
):
    period = stats.rolling_period(key, today)

    assert (period.start, period.end) == (start, today)
    assert (period.previous_start, period.previous_end) == (previous_start, previous_end)


def test_a_period_holds_both_of_its_end_days_and_nothing_outside():
    period = stats.rolling_period("12", TODAY)

    assert period.holds(date(2025, 11, 17)) and period.holds(TODAY)
    assert not period.holds(date(2025, 11, 16))
    assert not period.holds(date(2026, 11, 17))
    assert period.held_before(date(2024, 11, 17)) and period.held_before(date(2025, 11, 16))
    assert not period.held_before(date(2024, 11, 16))
    assert not period.held_before(date(2025, 11, 17))


def test_a_past_year_is_the_whole_calendar_year():
    period = stats.year_period(2024, TODAY)

    assert (period.start, period.end) == (date(2024, 1, 1), date(2024, 12, 31))
    assert (period.previous_start, period.previous_end) == (date(2023, 1, 1), date(2023, 12, 31))
    assert period.key == "aasta"
    assert period.year == 2024
    assert period.label == "2024"
    assert period.comparison_label == "Võrdlus sama perioodiga aasta varem"


def test_the_current_year_ends_today_and_is_compared_with_the_same_days_a_year_earlier():
    period = stats.year_period(2026, TODAY)

    assert (period.start, period.end) == (date(2026, 1, 1), TODAY)
    assert (period.previous_start, period.previous_end) == (date(2025, 1, 1), date(2025, 11, 16))


def test_two_dates_are_compared_with_the_equally_long_span_just_before():
    period = stats.custom_period(date(2026, 3, 1), date(2026, 3, 10))

    assert (period.start, period.end) == (date(2026, 3, 1), date(2026, 3, 10))
    assert (period.previous_start, period.previous_end) == (date(2026, 2, 19), date(2026, 2, 28))
    assert (period.end - period.start) == (period.previous_end - period.previous_start)
    assert period.key == "vali"
    assert period.label == "01.03.2026–10.03.2026"
    assert period.comparison_label == "Võrdlus vahetult eelnenud sama pika perioodiga"


def test_one_day_is_compared_with_the_day_before():
    period = stats.custom_period(TODAY, TODAY)

    assert (period.previous_start, period.previous_end) == (date(2026, 11, 15), date(2026, 11, 15))


# ---------------------------------------------------------------------------
# The URL's period
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "params",
    [
        {},
        {"periood": ""},
        {"periood": "midagi"},
        {"periood": "24"},
        {"periood": "aasta"},
        {"periood": "aasta", "aasta": "kaks tuhat"},
        {"periood": "aasta", "aasta": "2010"},
        {"periood": "aasta", "aasta": "2027"},
        {"periood": "vali"},
        {"periood": "vali", "alates": "2026-13-01", "kuni": "2026-03-10"},
        {"periood": "vali", "alates": "01.03.2026", "kuni": "10.03.2026"},
        {"periood": "vali", "alates": "2026-03-10", "kuni": "2026-03-01"},
        {"periood": "vali", "alates": "2026-11-01", "kuni": "2026-11-17"},
        {"periood": "vali", "alates": "2010-12-31", "kuni": "2011-06-30"},
    ],
)
def test_anything_malformed_is_the_last_twelve_months(params):
    assert stats.parse_period(params, TODAY) == stats.rolling_period("12", TODAY)


@pytest.mark.parametrize("key", ["3", "6", "12"])
def test_the_url_names_a_rolling_period(key):
    assert stats.parse_period({"periood": key}, TODAY) == stats.rolling_period(key, TODAY)


@pytest.mark.parametrize("year", [2011, 2024, 2026])
def test_the_url_names_a_year(year):
    period = stats.parse_period({"periood": "aasta", "aasta": str(year)}, TODAY)

    assert period == stats.year_period(year, TODAY)


def test_the_url_names_two_dates():
    period = stats.parse_period(
        {"periood": "vali", "alates": "2011-01-01", "kuni": "2026-11-16"}, TODAY
    )

    assert period == stats.custom_period(date(2011, 1, 1), TODAY)


# ---------------------------------------------------------------------------
# The four figures
# ---------------------------------------------------------------------------


def test_sent_opinions_are_counted_on_the_day_they_went_out():
    """By `sent_on` alone: the Teema's own year decides nothing."""
    period = stats.rolling_period("12", TODAY)
    old = topic(1, year=2019)
    facts = [
        sent(old, date(2025, 11, 17)),  # the period's first day
        sent(topic(2), TODAY),  # its last
        sent(topic(3), date(2026, 6, 1)),
        sent(topic(4), date(2025, 11, 16)),  # the comparison's last day
        sent(topic(5), date(2024, 11, 17)),  # its first
        sent(topic(6, year=2026), date(2024, 11, 16)),  # in neither
        sent(topic(7), date(2026, 11, 17)),  # tomorrow: in neither
    ]

    found = stats.summary(facts, [], {}, period)

    assert (found.sent, found.sent_before) == (3, 2)
    assert found.sent_diff == 1


def test_lawyers_are_listed_largest_first_then_by_name_with_the_comparison_beside():
    period = stats.rolling_period("12", TODAY)
    mari, anu, zoja, ants = (
        topic(n, owner=name) for n, name in enumerate(["Mari", "anu", "Zoja", "Ants"])
    )
    facts = [
        sent(mari, date(2026, 1, 5)),
        sent(mari, date(2026, 2, 5)),
        sent(mari, date(2026, 3, 5)),
        sent(zoja, date(2026, 1, 5)),
        sent(anu, date(2026, 1, 5)),
        sent(ants, date(2026, 1, 5)),
        # The comparison period.
        sent(mari, date(2025, 1, 5)),
        sent(anu, date(2025, 1, 5)),
        sent(anu, date(2025, 2, 5)),
        sent(topic(9, owner="Lahkunu"), date(2025, 3, 5)),
    ]

    owners = stats.summary(facts, [], {}, period).owners

    assert [(row.owner, row.count, row.previous) for row in owners] == [
        ("Mari", 3, 1),
        ("Ants", 1, 0),
        ("anu", 1, 2),
        ("Zoja", 1, 0),
    ]
    assert [row.diff for row in owners] == [2, 1, -1, 1]


def test_the_on_time_share_is_asked_only_of_opinions_that_had_a_deadline():
    period = stats.rolling_period("12", TODAY)
    facts = [
        sent(topic(1), date(2026, 3, 10), deadline=date(2026, 3, 10)),  # on the day: on time
        sent(topic(2), date(2026, 3, 11), deadline=date(2026, 3, 10)),  # a day late
        sent(topic(3), date(2026, 3, 1), deadline=date(2026, 3, 10)),
        sent(topic(4), date(2026, 3, 1)),  # no deadline: not asked
        sent(topic(5), date(2025, 3, 1), deadline=date(2025, 3, 10)),
        sent(topic(6), date(2025, 3, 1)),
    ]

    found = stats.summary(facts, [], {}, period)

    assert (found.on_time, found.with_deadline) == (2, 3)
    assert found.on_time_share == 67
    assert (found.on_time_before, found.with_deadline_before) == (1, 1)
    assert found.on_time_share_before == 100
    assert found.on_time_diff == -33


def test_without_a_single_deadline_there_is_no_on_time_share():
    period = stats.rolling_period("12", TODAY)

    found = stats.summary([sent(topic(1), date(2026, 3, 1))], [], {}, period)

    assert found.sent == 1
    assert found.with_deadline == 0
    assert found.on_time_share is None
    assert found.on_time_share_before is None
    assert found.on_time_diff is None


def test_received_counts_teemad_by_the_day_they_arrived():
    period = stats.rolling_period("12", TODAY)
    matters = [
        topic(1, received=date(2025, 11, 17)),
        topic(2, received=TODAY),
        topic(3, received=date(2026, 11, 17)),
        topic(4, received=None),
        topic(5, received=date(2025, 11, 16)),
        topic(6, received=date(2024, 11, 17)),
        topic(7, received=date(2025, 5, 5)),
        topic(8, received=date(2024, 11, 16)),
    ]

    found = stats.summary([], matters, {}, period)

    assert (found.received, found.received_before) == (2, 3)
    assert found.received_diff == -1
    assert found.sent == 0


def test_days_given_is_the_median_of_deadline_minus_arrival_for_teemad_received_in_the_period():
    period = stats.rolling_period("12", TODAY)
    matters = [
        topic(1, received=date(2026, 1, 10)),  # 10 days
        topic(2, received=date(2026, 2, 1)),  # 30 days
        topic(3, received=date(2026, 3, 1)),  # 40 days
        topic(4, received=date(2026, 4, 10)),  # deadline before arrival: left out
        topic(5, received=date(2026, 5, 1)),  # no deadline
        topic(6, received=None),  # no arrival
        topic(7, received=date(2026, 6, 1)),  # same day: 0 days counts
        topic(8, received=date(2025, 2, 1)),  # the comparison period: 7 days
        topic(9, received=date(2020, 2, 1)),  # in neither
    ]
    deadlines = {
        1: date(2026, 1, 20),
        2: date(2026, 3, 3),
        3: date(2026, 4, 10),
        4: date(2026, 4, 1),
        6: date(2026, 4, 1),
        7: date(2026, 6, 1),
        8: date(2025, 2, 8),
        9: date(2020, 12, 1),
    }

    found = stats.summary([], matters, deadlines, period)

    # 0, 10, 30, 40
    assert found.days_given == 20
    assert found.days_given_before == 7
    assert found.received == 6


def test_days_given_is_empty_when_no_received_teema_has_both_dates():
    period = stats.rolling_period("12", TODAY)

    found = stats.summary([], [topic(1, received=date(2026, 1, 10))], {}, period)

    assert found.received == 1
    assert found.days_given is None
    assert found.days_given_before is None


# ---------------------------------------------------------------------------
# Month by month
# ---------------------------------------------------------------------------


def test_every_calendar_month_of_the_period_gets_a_bar_the_empty_ones_too():
    period = stats.rolling_period("12", TODAY)

    bars, owners = stats.monthly([], period)

    assert [bar.label for bar in bars] == [
        "nov 25",
        "dets 25",
        "jaan 26",
        "veebr 26",
        "märts 26",
        "apr 26",
        "mai 26",
        "juuni 26",
        "juuli 26",
        "aug 26",
        "sept 26",
        "okt 26",
        "nov 26",
    ]
    assert bars[0].title == "november 2025"
    assert owners == []
    assert all(bar.total == 0 and bar.height == 0 and bar.segments == [] for bar in bars)


def test_the_first_and_last_bar_are_clipped_to_the_period():
    period = stats.rolling_period("12", TODAY)

    bars, _owners = stats.monthly([], period)

    assert (bars[0].start, bars[0].end) == (date(2025, 11, 17), date(2025, 11, 30))
    assert (bars[1].start, bars[1].end) == (date(2025, 12, 1), date(2025, 12, 31))
    assert (bars[3].start, bars[3].end) == (date(2026, 2, 1), date(2026, 2, 28))
    assert (bars[-1].start, bars[-1].end) == (date(2026, 11, 1), TODAY)


def test_a_period_inside_one_month_is_one_bar_with_the_periods_own_days():
    bars, _owners = stats.monthly([], stats.custom_period(date(2026, 3, 5), date(2026, 3, 9)))

    assert [(bar.label, bar.start, bar.end) for bar in bars] == [
        ("märts 26", date(2026, 3, 5), date(2026, 3, 9))
    ]


def test_a_bar_is_stacked_by_lawyer_and_scaled_to_the_tallest_month():
    period = stats.rolling_period("3", TODAY)  # 17.08–16.11.2026
    mari, anu, zoja = topic(1, owner="Mari"), topic(2, owner="anu"), topic(3, owner="Zoja")
    facts = [
        sent(mari, date(2026, 8, 16)),  # the day before the period: not on the page
        sent(mari, date(2026, 8, 20)),
        sent(mari, date(2026, 10, 1)),
        sent(mari, date(2026, 10, 31)),
        sent(anu, date(2026, 10, 15)),
        sent(zoja, date(2026, 10, 15)),
        sent(anu, TODAY),
        sent(anu, date(2026, 11, 17)),  # after the period
    ]

    bars, owners = stats.monthly(facts, period)

    assert owners == ["anu", "Mari", "Zoja"]
    assert [bar.label for bar in bars] == ["aug 26", "sept 26", "okt 26", "nov 26"]
    assert [bar.total for bar in bars] == [1, 0, 4, 1]
    assert [bar.height for bar in bars] == [25.0, 0.0, 100.0, 25.0]
    assert max(bar.height for bar in bars) == 100
    assert [(s.owner, s.count, s.height, s.slot) for s in bars[2].segments] == [
        ("anu", 1, 25.0, 0),
        ("Mari", 2, 50.0, 1),
        ("Zoja", 1, 25.0, 2),
    ]
    assert [(s.owner, s.height) for s in bars[0].segments] == [("Mari", 100.0)]
    assert bars[1].segments == []
    for bar in bars:
        if bar.total:
            assert sum(s.height for s in bar.segments) == pytest.approx(100, abs=0.05)
            assert sum(s.count for s in bar.segments) == bar.total


def test_segments_of_an_uneven_month_still_fill_the_bar():
    period = stats.custom_period(date(2026, 10, 1), date(2026, 10, 31))
    facts = [sent(topic(n, owner=name), date(2026, 10, 5)) for n, name in enumerate("ABC")]

    bars, _owners = stats.monthly(facts, period)

    assert [s.height for s in bars[0].segments] == [33.33, 33.33, 33.33]
    assert sum(s.height for s in bars[0].segments) == pytest.approx(100, abs=0.05)
    assert bars[0].height == 100


# ---------------------------------------------------------------------------
# The long-term trends
# ---------------------------------------------------------------------------


def _trend_world():
    """2023: two Teemad; 2024: none; 2025: one without dates; 2026: one."""
    a = topic(1, year=2023, received=date(2023, 3, 1), eu=True)
    b = topic(2, year=2023, received=date(2023, 5, 1), koda=True)
    c = topic(3, year=2025)
    d = topic(4, year=2026, received=date(2026, 1, 1), eu=True)
    outside = topic(5, year=2019, received=date(2019, 1, 1), eu=True, koda=True)
    unnumbered = topic(6, year=None, received=date(2024, 1, 1), eu=True)
    matters = [a, b, c, d, outside, unnumbered]
    deadlines = {
        1: date(2023, 3, 11),  # 10 days
        2: date(2023, 5, 31),  # 30 days
        4: date(2026, 1, 15),  # 14 days
        5: date(2019, 1, 2),
        6: date(2024, 1, 2),
    }
    facts = [
        sent(b, date(2023, 5, 20), deadline=date(2023, 5, 31)),  # on time
        sent(a, date(2024, 2, 1), deadline=date(2024, 1, 31)),  # a 2023 Teema, sent late in 2024
        sent(c, date(2025, 6, 1)),  # no deadline
        sent(d, date(2026, 2, 1), deadline=date(2026, 2, 1)),
        sent(outside, date(2019, 6, 1), deadline=date(2019, 6, 1)),
    ]
    return facts, matters, deadlines


def _found(first: int = 2023, last: int = 2026) -> dict[str, stats.Trend]:
    facts, matters, deadlines = _trend_world()
    found = stats.trends(facts, matters, deadlines, first=first, last=last, today=TODAY)
    assert [trend.key for trend in found] == ["sent", "days", "ontime", "eu", "koda"]
    return {trend.key: trend for trend in found}


def test_there_are_five_trends_each_with_a_bar_for_every_year():
    found = _found()

    for trend in found.values():
        assert [bar.year for bar in trend.bars] == [2023, 2024, 2025, 2026]
        assert trend.has_data
    assert found["sent"].title == "Välja saadetud arvamused aastate lõikes"
    assert found["days"].title == "Arvamuse esitamiseks antud aeg päevades"
    assert found["days"].note == "mediaan"
    assert found["ontime"].title == "Tähtaegselt esitatud arvamuste osakaal"
    assert found["eu"].title == "Otseselt ELiga seotud teemade osakaal"
    assert found["koda"].title == "Koja omaalgatused aastate lõikes"


def test_the_range_decides_the_years_shown():
    found = _found(first=2025, last=2026)

    assert [bar.year for bar in found["sent"].bars] == [2025, 2026]
    assert [bar.value for bar in found["sent"].bars] == [1, 1]


def test_sent_is_grouped_by_the_year_the_opinion_went_out():
    """The 2023 Teema answered in February 2024 is 2024's opinion."""
    bars = _found()["sent"].bars

    assert [bar.value for bar in bars] == [1, 1, 1, 1]
    assert [bar.text for bar in bars] == ["1", "1", "1", "1"]
    assert bars[1].title == "2024: 1 arvamus"
    assert [bar.height for bar in bars] == [100.0] * 4


def test_days_given_is_grouped_by_the_teemas_year():
    bars = _found()["days"].bars

    assert [bar.value for bar in bars] == [20, None, None, 14]
    assert [bar.text for bar in bars] == ["20", "—", "—", "14"]
    assert bars[0].title == "2023: mediaan 20 päeva, keskmine 20 päeva · 2 teemat"
    assert bars[3].title == "2026: mediaan 14 päeva, keskmine 14 päeva · 1 teema"
    assert [bar.height for bar in bars] == [100.0, 0.0, 0.0, 70.0]


def test_the_on_time_share_is_grouped_by_the_teemas_year_not_the_sending_year():
    """2023's two Teemad: one on time in 2023, one late in 2024 — 50% for 2023,
    and 2024, which has no Teema of its own, has nothing to say."""
    bars = _found()["ontime"].bars

    assert [bar.value for bar in bars] == [50.0, None, None, 100.0]
    assert [bar.text for bar in bars] == ["50%", "—", "—", "100%"]
    assert bars[0].title == "2023: tähtaegselt 50% · 1/2 arvamusest"
    # A share is drawn against 100, not against the best year.
    assert [bar.height for bar in bars] == [50.0, 0.0, 0.0, 100.0]


def test_the_eu_share_and_kodas_initiatives_are_grouped_by_the_teemas_year():
    found = _found()

    assert [bar.value for bar in found["eu"].bars] == [50.0, None, 0.0, 100.0]
    assert [bar.text for bar in found["eu"].bars] == ["50%", "—", "0%", "100%"]
    assert found["eu"].bars[0].title == "2023: ELiga seotud 50% · 1 / 2 teemast"
    assert [bar.value for bar in found["koda"].bars] == [1, None, 0, 0]
    assert [bar.text for bar in found["koda"].bars] == ["1", "—", "0", "0"]
    assert (
        found["koda"].bars[0].title == "2023: Koja omaalgatused 1 · 50% kõigist teemadest (1 / 2)"
    )


def test_a_year_without_data_says_so():
    found = _found()

    for key in ("days", "ontime", "eu", "koda"):
        empty = found[key].bars[1]
        assert empty.year == 2024
        assert empty.value is None
        assert empty.text == "—"
        assert empty.title == "2024: andmed puuduvad"
        assert empty.height == 0


def test_only_the_current_year_is_marked_as_unfinished():
    for trend in _found().values():
        assert [bar.partial for bar in trend.bars] == [False, False, False, True]


def test_trends_with_nothing_to_show_have_no_data():
    found = stats.trends([], [], {}, first=2024, last=2026, today=TODAY)
    by_key = {trend.key: trend for trend in found}

    # No opinion is a count of zero; the other four have nothing to measure.
    assert [bar.value for bar in by_key["sent"].bars] == [0, 0, 0]
    assert [bar.text for bar in by_key["sent"].bars] == ["0", "0", "0"]
    for key in ("days", "ontime", "eu", "koda"):
        assert not by_key[key].has_data
        assert [bar.text for bar in by_key[key].bars] == ["—", "—", "—"]


# ---------------------------------------------------------------------------
# Rekordid
# ---------------------------------------------------------------------------


def _records(facts, matters=(), deadlines=None, *, first=2023, last=2026):
    found = stats.trends(
        list(facts), list(matters), deadlines or {}, first=first, last=last, today=TODAY
    )
    return {record.title: record for record in stats.records(list(facts), found, TODAY)}


YEAR_RECORD = "Välja saadetud arvamuste rekordaasta"
MONTH_RECORD = "Välja saadetud arvamuste rekordkuu"
ON_TIME_RECORD = "Suurima tähtaegselt esitatud arvamuste osakaaluga aasta"
DAYS_RECORD = "Lühima arvamuse esitamiseks antud tähtajaga aasta"
KODA_RECORD = "Koja omaalgatuste rekordaasta"
EU_RECORD = "ELiga otseselt seotud teemade rekordaasta"


def test_nothing_recorded_means_no_records():
    assert _records([]) == {}


def test_a_tie_names_every_year():
    one = topic(1, year=2023)
    found = _records(
        [
            sent(one, date(2023, 3, 5)),
            sent(one, date(2023, 6, 5)),
            sent(one, date(2025, 3, 7)),
            sent(one, date(2025, 9, 7)),
            sent(one, date(2024, 1, 7)),
        ]
    )

    assert (found[YEAR_RECORD].value, found[YEAR_RECORD].when) == ("2 arvamust", "2023, 2025")
    # Five months of one opinion each: three named, the rest counted.
    assert found[MONTH_RECORD].value == "1 arvamus"
    assert found[MONTH_RECORD].when == "märts 2023, juuni 2023, jaanuar 2024 +2"


def test_the_record_month_leaves_out_the_month_still_running():
    one = topic(1)
    facts = [
        sent(one, date(2026, 11, 1)),
        sent(one, date(2026, 11, 2)),
        sent(one, TODAY),
        sent(one, date(2026, 10, 5)),
        sent(one, date(2026, 10, 6)),
    ]

    found = _records(facts)

    # The year record does count the unfinished year …
    assert (found[YEAR_RECORD].value, found[YEAR_RECORD].when) == ("5 arvamust", "2026")
    # … the month record waits for November to end.
    assert (found[MONTH_RECORD].value, found[MONTH_RECORD].when) == (
        "2 arvamust",
        "oktoober 2026",
    )


def test_the_record_month_is_looked_for_only_in_the_years_shown():
    old, new = topic(1, year=2020), topic(2, year=2025)
    facts = [
        sent(old, date(2020, 5, 1)),
        sent(old, date(2020, 5, 2)),
        sent(old, date(2020, 5, 3)),
        sent(new, date(2025, 4, 1)),
    ]

    found = _records(facts)

    assert (found[MONTH_RECORD].value, found[MONTH_RECORD].when) == ("1 arvamus", "aprill 2025")
    assert (found[YEAR_RECORD].value, found[YEAR_RECORD].when) == ("1 arvamus", "2025")


def test_on_time_and_days_records_wait_for_the_year_to_end():
    """2026 is unfinished: its 100% and its 5 days are not records yet, while
    the EU and Koda records — counts of what is already there — may name it."""
    a = topic(1, year=2025, received=date(2025, 1, 1))
    b = topic(2, year=2025, received=date(2025, 2, 1))
    c = topic(3, year=2024, received=date(2024, 1, 1))
    d = topic(4, year=2026, received=date(2026, 1, 1), eu=True, koda=True)
    deadlines = {
        1: date(2025, 1, 21),  # 20 days
        2: date(2025, 2, 21),  # 20 days
        3: date(2024, 1, 31),  # 30 days
        4: date(2026, 1, 6),  # 5 days
    }
    facts = [
        sent(a, date(2025, 1, 20), deadline=date(2025, 1, 21)),
        sent(b, date(2025, 3, 1), deadline=date(2025, 2, 21)),  # late: 2025 is 50%
        sent(c, date(2024, 2, 10), deadline=date(2024, 1, 31)),  # late: 2024 is 0%
        sent(d, date(2026, 1, 5), deadline=date(2026, 1, 6)),  # 2026 is 100%
    ]

    found = _records(facts, [a, b, c, d], deadlines)

    assert (found[ON_TIME_RECORD].value, found[ON_TIME_RECORD].when) == ("50,0%", "2025")
    assert (found[DAYS_RECORD].value, found[DAYS_RECORD].when) == ("20 päeva", "2025")
    assert (found[EU_RECORD].value, found[EU_RECORD].when) == ("100%", "2026")
    assert (found[KODA_RECORD].value, found[KODA_RECORD].when) == ("1", "2026")


def test_only_the_unfinished_year_having_deadlines_leaves_those_records_out():
    d = topic(4, year=2026, received=date(2026, 1, 1))
    facts = [sent(d, date(2026, 1, 5), deadline=date(2026, 1, 6))]

    found = _records(facts, [d], {4: date(2026, 1, 6)})

    assert ON_TIME_RECORD not in found
    assert DAYS_RECORD not in found
    assert YEAR_RECORD in found


def test_tied_completed_years_share_the_on_time_and_days_records():
    a = topic(1, year=2023, received=date(2023, 1, 1))
    b = topic(2, year=2024, received=date(2024, 1, 1))
    deadlines = {1: date(2023, 1, 11), 2: date(2024, 1, 11)}
    facts = [
        sent(a, date(2023, 1, 5), deadline=date(2023, 1, 11)),
        sent(b, date(2024, 1, 5), deadline=date(2024, 1, 11)),
    ]

    found = _records(facts, [a, b], deadlines)

    assert (found[ON_TIME_RECORD].value, found[ON_TIME_RECORD].when) == ("100,0%", "2023, 2024")
    assert (found[DAYS_RECORD].value, found[DAYS_RECORD].when) == ("10 päeva", "2023, 2024")
