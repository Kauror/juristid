"""QA audit 2026-10-14 (area `stats`): the Statistika Ülevaade strip and rail.

The #440 suite proves the rail and the strip's victory figure count and open
real data only, for the page *without* filters. These probes read the page the
way a department head uses it: with a period and a chip from the filter bar.

* QA-STATS-01 — the rail block headed «Aruandlus <this year>» prints
  «arvamusi välja» for the *selected period* (it is `SUBMISSIONS_SENT` from the
  page context), while its two sibling rows count this calendar year.
* QA-STATS-02 — the rail and the strip's «teemat töövõiduga <aasta>» ignore the
  Statistika filter chips (owner, policy area, …) while the three catalogued
  strip figures obey them, so the page prints «avatud teemat» twice with two
  different values under one chip.
* QA-STATS-11 — the «Inimesed» rows are `MATTERS_BY_OWNER`: every Matter of the
  period (closed and archive rows included); the empty state said «avatud
  teemasid». Fixed in wording; the approved definition is unchanged.
"""

from __future__ import annotations

import html
import re
from datetime import date, timedelta

import pytest
from django.urls import reverse
from django.utils import timezone

from app.matters.enums import MatterOrigin, RecordMode
from app.matters.models import Matter
from app.workflow.enums import Disposition
from tests.synthetic_statistics import _submission
from tests.test_reporting_drillthrough_data_scope import register_total

pytestmark = pytest.mark.django_db

_RAIL_ROW = re.compile(
    r'<a class="railrow" href="(?P<href>[^"]+)">\s*'
    r'<span class="railrow__key">(?P<caption>[^<]+)</span>\s*'
    r'<span class="railrow__value[^"]*">(?P<value>\d+)</span>'
)
_STRIP_FIGURE = re.compile(
    r'<a class="seis__figure" href="(?P<href>[^"]+)">\s*'
    r'<span class="seis__number[^"]*">(?P<value>\d+)</span>\s*'
    r'<span class="seis__caption">(?P<caption>[^<]+)</span>'
)
_RAIL_BLOCK = re.compile(
    r'<section class="railblock" aria-label="(?P<label>[^"]+)">(?P<body>.*?)</section>', re.S
)


@pytest.fixture
def pinned_today(world, monkeypatch):
    monkeypatch.setattr(timezone, "localdate", lambda *args, **kwargs: world.today)
    return world.today


def _shown(pattern: re.Pattern[str], body: str) -> dict[str, tuple[int, str]]:
    return {
        html.unescape(m["caption"]).strip(): (int(m["value"]), html.unescape(m["href"]))
        for m in pattern.finditer(body)
    }


def _page(client, viewer, **params: str) -> str:
    client.force_login(viewer)
    response = client.get(reverse("reporting:overview"), params)
    assert response.status_code == 200
    return response.content.decode()


def _rail_block(body: str, label: str) -> dict[str, tuple[int, str]]:
    for match in _RAIL_BLOCK.finditer(body):
        if html.unescape(match["label"]) == label:
            return _shown(_RAIL_ROW, match["body"])
    raise AssertionError(f"no rail block {label!r}")


# ---------------------------------------------------------------------------
# QA-STATS-01
# ---------------------------------------------------------------------------


@pytest.fixture
def sends_in_two_years(world, pinned_today):
    """One real sent opinion last year and one this year, on a visible Matter."""
    _submission(
        world.native_open, title="QA mullune arvamus", sent_on=date(world.today.year - 1, 5, 5)
    )
    _submission(world.native_open, title="QA tänavune arvamus", sent_on=world.today)
    return world


def test_rail_reporting_block_counts_this_year_by_default(client, sends_in_two_years):
    """Probe (passes): on the default period the block and its heading agree."""
    world = sends_in_two_years
    body = _page(client, world.head)
    block = _rail_block(body, f"Aruandlus {world.today.year}")
    this_year = _rail_block(
        _page(client, world.head, periood=str(world.today.year)), f"Aruandlus {world.today.year}"
    )
    assert block["arvamusi välja"][0] == this_year["arvamusi välja"][0]


@pytest.mark.parametrize("period", ["koik", "eelmine", "viimased5"])
def test_rail_reporting_block_sends_are_the_headings_year_whatever_the_period(
    period, client, sends_in_two_years
):
    world = sends_in_two_years
    heading = f"Aruandlus {world.today.year}"
    expected = _rail_block(_page(client, world.head, periood=str(world.today.year)), heading)
    shown = _rail_block(_page(client, world.head, periood=period), heading)
    # The two sibling rows are always this year (overview_strip._victory_params /
    # _in_force_params use today.year); «arvamusi välja» must be too.
    assert shown["arvamusi välja"][0] == expected["arvamusi välja"][0], (period, shown)


# ---------------------------------------------------------------------------
# QA-STATS-02
# ---------------------------------------------------------------------------


def test_both_avatud_teemat_figures_agree_under_an_owner_chip(client, world, pinned_today):
    body = _page(client, world.head, vastutaja=str(world.sandra.pk))
    strip = _shown(_STRIP_FIGURE, body)
    rail = _rail_block(body, "Praegu")
    assert strip["avatud teemat"][0] == rail["avatud teemat"][0], (strip, rail)


def test_strip_avatud_teemat_obeys_the_owner_chip(client, world, pinned_today):
    """Probe (passes): the catalogued strip figure is narrowed by the chip and opens its list."""
    body = _page(client, world.head, vastutaja=str(world.sandra.pk))
    value, href = _shown(_STRIP_FIGURE, body)["avatud teemat"]
    expected = (
        Matter.objects.visible_to(world.head)
        .real_data()
        .filter(owner=world.sandra, is_open=True, record_mode=RecordMode.FULL)
        .count()
    )
    assert value == expected
    assert register_total(client, href) == value


def test_strip_victory_figure_obeys_the_owner_chip(client, world, pinned_today):
    from app.intelligence.services import add_work_victory_candidate, confirm_work_victory
    from app.workflow.enums import DatePrecision

    # A victory on Sandra's Matter and on Martin's: under «Vastutaja: Sandra»
    # only hers belongs in the figure.
    for matter in (world.native_open, world.native_waiting):
        confirm_work_victory(
            record=add_work_victory_candidate(
                matter=matter,
                title="QA töövõit",
                period_date=date(world.today.year, 1, 1),
                period_end=date(world.today.year, 1, 31),
                date_precision=DatePrecision.MONTH,
                actor=matter.owner,
            ),
            actor=world.head,
        )
    body = _page(client, world.head, vastutaja=str(world.sandra.pk))
    value, href = _shown(_STRIP_FIGURE, body)[f"teemat töövõiduga {world.today.year}"]
    assert value == 1, value
    assert "vastutaja=" in href, href


# ---------------------------------------------------------------------------
# QA-STATS-11 — «Inimesed» is not open Matters
# ---------------------------------------------------------------------------


def test_inimesed_rows_count_closed_matters_too(client, world, pinned_today):
    """A closed Matter moves the «Inimesed» row — by the approved definition.

    `MATTERS_BY_OWNER` is catalogued over «Nähtavad teemad»: every Matter of
    the period, closed and archive rows included. QA-STATS-11 was the wording
    around it — an empty state reading «Ühelgi inimesel ei ole avatud
    teemasid» and no period beside «Praegu». QA remediation G kept the
    definition and fixed the words (the heading names the period, the empty
    state no longer says «avatud»; tests/test_statistika_filter_scope.py), so
    this stays as the guard that the definition was not silently changed.
    """
    before = _rail_block(_page(client, world.head), "Inimesed")
    Matter.objects.create(
        title="QA suletud teema",
        record_mode=RecordMode.FULL,
        origin=MatterOrigin.NATIVE,
        reference_year=world.today.year,
        reference_number=990,
        reporting_year=world.today.year,
        owner=world.sandra,
        is_open=False,
        disposition=Disposition.COMPLETED,
        closed_at=timezone.now() - timedelta(days=1),
        closed_by=world.sandra,
    )
    after = _rail_block(_page(client, world.head), "Inimesed")
    name = world.sandra.get_short_name()  # first-name display (QA naming decision)
    assert after[name][0] == before[name][0] + 1


# ---------------------------------------------------------------------------
# Record identity behind two rail rows (the #440 suite compares totals only)
# ---------------------------------------------------------------------------


def _register_ids(client, url: str) -> set:
    from urllib.parse import parse_qs, urlparse

    parsed = urlparse(url)
    query = {k: v[0] for k, v in parse_qs(parsed.query).items()}
    response = client.get(parsed.path, query)
    assert response.status_code == 200
    paginator = response.context["paginator"]
    ids: set = set()
    for number in paginator.page_range:
        ids |= {row.pk for row in paginator.page(number).object_list}
    return ids


@pytest.mark.parametrize("viewer_name", ["head", "reader"])
def test_rail_rows_open_exactly_the_real_records_they_count(
    viewer_name, client, world, pinned_today
):
    from tests.test_reporting_drillthrough_data_scope import add_test_twins

    twins = {twin.pk for twin in add_test_twins(world)}
    viewer = getattr(world, viewer_name)
    rail = _rail_block(_page(client, viewer), "Praegu")
    base = (
        Matter.objects.visible_to(viewer)
        .real_data()
        .filter(is_open=True, record_mode=RecordMode.FULL)
    )
    expected = {
        "avatud teemat": set(base.values_list("pk", flat=True)),
        "vastutajata": set(base.filter(owner__isnull=True).values_list("pk", flat=True)),
    }
    for caption, ids in expected.items():
        value, href = rail[caption]
        opened = _register_ids(client, href)
        assert opened == ids, caption
        assert value == len(ids), caption
        assert not opened & twins, caption
        # docs/adr/0181: the reader counts and opens the restricted Teema as the head does.
        assert (world.restricted.pk in opened) is (caption == "avatud teemat"), caption
