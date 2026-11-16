"""Statistika's closer look at a period: flow, act types, days given, decisions (phase 2).

Pure arithmetic over hand-built facts — no database. The page that draws these
is exercised in tests/test_statistika_dashboard_closer_look.py.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date

from app.reporting import opinion_stats as stats
from app.reporting.opinions import (
    SOURCE_IMPORTED,
    DeclinedFact,
    MatterFacts,
    OpinionFact,
)

TODAY = date(2026, 11, 16)
YEAR = stats.year_period(2026, TODAY)


def topic(
    number: int,
    *,
    received: date | None = None,
    instrument: str = "Seadus",
    key: str = "seadus",
) -> MatterFacts:
    return MatterFacts(
        matter_id=number,
        reference=f"2026_{number}",
        title=f"Teema {number}",
        year=2026,
        received=received,
        owner="Mari",
        is_eu=False,
        koda_initiative=False,
        instrument=instrument,
        instrument_key=key,
    )


def sent(matter: MatterFacts, on: date, deadline: date | None = None) -> OpinionFact:
    return OpinionFact(matter=matter, sent_on=on, deadline=deadline, source=SOURCE_IMPORTED)


# -- flow ---------------------------------------------------------------------------


def test_flow_counts_arrivals_and_sends_in_their_own_months():
    a = topic(1, received=date(2026, 1, 10))
    b = topic(2, received=date(2026, 1, 20))
    c = topic(3, received=date(2025, 12, 30))  # arrived before the period
    bars = stats.flow([sent(a, date(2026, 2, 3)), sent(c, date(2026, 1, 5))], [a, b, c], YEAR)

    assert [bar.label for bar in bars][:3] == ["jaan 26", "veebr 26", "märts 26"]
    assert len(bars) == 11  # January to November, the month of TODAY
    assert (bars[0].received, bars[0].sent) == (2, 1)
    assert (bars[1].received, bars[1].sent) == (0, 1)
    assert (bars[2].received, bars[2].sent) == (0, 0)


def test_flow_heights_share_one_scale_across_both_series():
    a = topic(1, received=date(2026, 3, 1))
    b = topic(2, received=date(2026, 3, 2))
    c = topic(3, received=date(2026, 3, 3))
    d = topic(4, received=date(2026, 3, 4))
    bars = stats.flow([sent(a, date(2026, 3, 9)), sent(b, date(2026, 3, 9))], [a, b, c, d], YEAR)
    march = bars[2]

    assert march.received_height == 100.0
    assert march.sent_height == 50.0


def test_flow_of_an_empty_period_is_flat():
    bars = stats.flow([], [], YEAR)

    assert all(bar.received == bar.sent == 0 for bar in bars)
    assert all(bar.received_height == bar.sent_height == 0.0 for bar in bars)


# -- by act type --------------------------------------------------------------------


def test_instruments_are_ordered_by_sent_and_carry_their_share():
    law = [topic(n, received=date(2026, 2, 1)) for n in (1, 2, 3)]
    rule = topic(4, received=date(2026, 2, 1), instrument="Määrus", key="maarus")
    facts = [sent(m, date(2026, 2, 10)) for m in law] + [sent(rule, date(2026, 2, 10))]

    rows = stats.by_instrument(facts, [*law, rule], {}, YEAR)

    assert [(row.label, row.sent, row.share) for row in rows] == [
        ("Seadus", 3, 75),
        ("Määrus", 1, 25),
    ]
    assert rows[0].width == 100.0
    assert rows[0].key == "seadus"


def test_an_instruments_on_time_share_reads_only_opinions_with_a_deadline():
    a, b, c = (topic(n, received=date(2026, 2, 1)) for n in (1, 2, 3))
    facts = [
        sent(a, date(2026, 2, 10), deadline=date(2026, 2, 12)),  # on time
        sent(b, date(2026, 2, 20), deadline=date(2026, 2, 12)),  # late
        sent(c, date(2026, 2, 10)),  # no deadline: not in the share
    ]

    (row,) = stats.by_instrument(facts, [a, b, c], {}, YEAR)

    assert row.on_time_share == 50


def test_an_instrument_without_a_dated_opinion_has_no_on_time_share():
    a = topic(1, received=date(2026, 2, 1))

    (row,) = stats.by_instrument([sent(a, date(2026, 2, 10))], [a], {}, YEAR)

    assert row.on_time_share is None
    assert row.days_given is None


def test_days_given_is_the_median_over_the_types_teemad_that_arrived_in_the_period():
    a, b, c = (topic(n, received=date(2026, 3, 1)) for n in (1, 2, 3))
    old = topic(4, received=date(2025, 3, 1))  # arrived outside the period
    deadlines = {
        1: date(2026, 3, 11),  # 10
        2: date(2026, 3, 21),  # 20
        3: date(2026, 4, 30),  # 60
        4: date(2025, 3, 2),
    }

    (row,) = stats.by_instrument([], [a, b, c, old], deadlines, YEAR)

    assert row.days_given == 20
    assert row.received == 3
    assert row.sent == 0


def test_a_type_with_nothing_sent_and_nothing_received_is_left_out():
    quiet = topic(1, received=date(2024, 1, 1), instrument="VTK", key="vtk")

    assert stats.by_instrument([], [quiet], {}, YEAR) == []


# -- days given, in bands -----------------------------------------------------------


def test_pressure_bands_are_inclusive_at_their_upper_bound():
    days = (0, 7, 8, 14, 15, 30, 31, 90)
    matters = [topic(n, received=date(2026, 5, 1)) for n in range(len(days))]
    deadlines = {
        n: date(2026, 5, 1).fromordinal(date(2026, 5, 1).toordinal() + given)
        for n, given in enumerate(days)
    }

    result = stats.pressure(matters, deadlines, YEAR)

    assert [(band.label, band.count) for band in result.bands] == [
        ("kuni 7 päeva", 2),
        ("8–14 päeva", 2),
        ("15–30 päeva", 2),
        ("üle 30 päeva", 2),
    ]
    assert [band.share for band in result.bands] == [25, 25, 25, 25]
    assert result.total == 8
    assert result.without == 0


def test_pressure_leaves_out_teemad_without_both_dates_and_says_how_many():
    with_deadline = topic(1, received=date(2026, 5, 1))
    without = topic(2, received=date(2026, 5, 1))
    negative = topic(3, received=date(2026, 5, 10))
    outside = topic(4, received=date(2025, 5, 1))
    deadlines = {1: date(2026, 5, 6), 3: date(2026, 5, 1), 4: date(2025, 5, 6)}

    result = stats.pressure([with_deadline, without, negative, outside], deadlines, YEAR)

    assert result.total == 1
    assert result.without == 2  # no deadline, and a deadline before arrival
    assert result.bands[0].count == 1


def test_pressure_of_nothing_is_all_zero():
    result = stats.pressure([], {}, YEAR)

    assert result.total == 0
    assert all(band.count == 0 and band.share == 0 for band in result.bands)


# -- decisions ----------------------------------------------------------------------


def test_decisions_count_sends_by_their_day_and_declines_by_theirs():
    a = topic(1)
    facts = [sent(a, date(2026, 4, 1)), sent(a, date(2025, 4, 1))]
    declined = [
        DeclinedFact(a, date(2026, 6, 1), SOURCE_IMPORTED),
        DeclinedFact(a, date(2025, 6, 1), SOURCE_IMPORTED),
        DeclinedFact(a, None, SOURCE_IMPORTED),  # no day: counted in no period
    ]

    result = stats.decisions(facts, declined, YEAR)

    assert (result.sent, result.declined, result.total) == (1, 1, 2)
    assert result.sent_share == 50
    assert result.sent_width == 50.0


def test_decisions_with_nothing_have_no_share():
    result = stats.decisions([], [], YEAR)

    assert result.total == 0
    assert result.sent_share is None
    assert result.sent_width == 0.0


def test_matter_facts_default_to_no_instrument_key():
    assert replace(topic(1), instrument_key="").instrument_key == ""
    assert (
        MatterFacts(
            matter_id=1,
            reference="",
            title="",
            year=None,
            received=None,
            owner="",
            is_eu=False,
            koda_initiative=False,
            instrument="Määramata",
        ).instrument_key
        == ""
    )
