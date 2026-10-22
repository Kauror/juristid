"""The Statistika rail counts and opens real data only, like the rest of the page.

The rail's seven figures — Praegu's five register populations and the year's
«teemat töövõiduga» and «teemat jõustunud aktiga» — were counted and linked in
the Teemad register's own default, `?andmed=koik`, so they were the only
figures on Statistika that counted TEST Matters, and their links opened them.
Production had no TEST Matter on the day this was found, which is the only
reason «avatud teemat» in the rail and the same caption in the strip agreed.

Every native Matter of the shared Statistika world gets a TEST twin here
(`add_test_twins`, from the #435 drill-through test), and the two facts the
twins do not copy — a confirmed work victory and a commencement already in
force — are planted on a real Matter, on the restricted one and on their twins.
Then, for two readers, one of whom may not see the restricted Matter:

* every rail link carries `andmed=paris` exactly once;
* every rail figure equals the total of the register list its link opens,
  through the real view;
* the TEST twins change no rail figure;
* and, so the above can fail at all, every row meets a TEST twin: the same
  link without `andmed=` opens a longer list.

The strip's «teemat töövõiduga <aasta>» is the same register population as the
rail's and is held to the same rules, and the rendered page is read as well:
the number printed, the href behind it, and the list that href opens.

Against origin/main 70f102e9's `overview_strip.py` ten of these thirteen tests
fail — no `andmed` on any link, the twins moving every figure, no link meeting
a twin, the page's two «avatud teemat» disagreeing — while "figure equals its
list" passes, because the count and the list both included TEST. That is why
the scope is changed in the params both are built from, and never in the URL
alone.
"""

from __future__ import annotations

import html
import re
from datetime import date, timedelta

import pytest
from django.urls import reverse
from django.utils import timezone

from app.intelligence.enums import EffectiveDateKind
from app.intelligence.services import (
    add_effective_date,
    add_work_victory_candidate,
    confirm_work_victory,
)
from app.matters.department_dashboard import RESULTS_ANCHOR
from app.matters.models import Matter
from app.reporting import overview_strip
from app.reporting.selectors.base import REGISTER_DATA_SCOPE
from app.workflow.enums import DatePrecision
from tests.test_reporting_drillthrough_data_scope import _query, add_test_twins, register_total

pytestmark = pytest.mark.django_db

#: Every rail row, by caption. A row added to the rail without being added here
#: fails `test_the_rail_is_the_seven_rows_this_test_knows`.
RAIL_CAPTIONS = (
    "avatud teemat",
    "üle tähtaja",
    f"tähtaeg {overview_strip.DEADLINE_WINDOW_DAYS} p jooksul",
    "vastutajata",
    "järgmise tegevuseta",
    "teemat töövõiduga",
    "teemat jõustunud aktiga",
)


@pytest.fixture
def pinned_today(world, monkeypatch):
    """The register view reads the day for itself; make it the world's day."""
    monkeypatch.setattr(timezone, "localdate", lambda *args, **kwargs: world.today)
    return world.today


def _twin_of(matter: Matter) -> Matter:
    return Matter.objects.get(title=f"{matter.title} (TEST)")


def _plant_year_facts(world, matters: list[Matter]) -> None:
    """A confirmed victory this year and a commencement in force since yesterday."""
    in_force = world.today - timedelta(days=1)
    for matter in matters:
        confirm_work_victory(
            record=add_work_victory_candidate(
                matter=matter,
                title="Üleminekuaeg pikendati",
                period_date=date(world.today.year, 1, 1),
                period_end=date(world.today.year, 1, 31),
                date_precision=DatePrecision.MONTH,
                actor=matter.owner,
            ),
            actor=world.head,
        )
        add_effective_date(
            matter=matter,
            kind=EffectiveDateKind.KNOWN_DATE,
            date_value=in_force,
            period_end=in_force,
            date_precision=DatePrecision.EXACT,
            description="põhiosa",
            actor=matter.owner,
        )


@pytest.fixture
def test_matters_present(world, pinned_today):
    """The world, its TEST twins, and the year facts on real, restricted and TEST."""
    add_test_twins(world)
    originals = [world.native_open, world.restricted]
    _plant_year_facts(world, originals + [_twin_of(m) for m in originals])
    return world


def _rail(viewer, today: date):
    return [figure for block in overview_strip.rail(viewer, today, {}) for figure in block.rows]


def test_the_rail_is_the_seven_rows_this_test_knows(world, pinned_today):
    assert tuple(figure.caption for figure in _rail(world.head, pinned_today)) == RAIL_CAPTIONS


@pytest.mark.parametrize("viewer_name", ["head", "reader"])
def test_every_rail_link_carries_the_data_scope_once(viewer_name, world, pinned_today):
    for figure in _rail(getattr(world, viewer_name), pinned_today):
        assert _query(figure.url).get("andmed") == [REGISTER_DATA_SCOPE], figure.caption
        assert figure.url.count("andmed=") == 1, figure.url


@pytest.mark.parametrize("viewer_name", ["head", "reader"])
def test_every_rail_figure_equals_the_register_list_it_opens(
    viewer_name, client, test_matters_present
):
    world = test_matters_present
    viewer = getattr(world, viewer_name)
    client.force_login(viewer)
    for figure in _rail(viewer, world.today):
        assert register_total(client, figure.url) == figure.value, (figure.caption, figure.url)


def test_the_restricted_matter_counts_only_for_a_reader_who_may_see_it(test_matters_present):
    """The rail is authorized before it is scoped.

    Since docs/adr/0181 the head and a READER count the restricted Matter alike;
    an administrator without a business role counts nothing.
    """
    world = test_matters_present
    head = {f.caption: f.value for f in _rail(world.head, world.today)}
    reader = {f.caption: f.value for f in _rail(world.reader, world.today)}
    outsider = {f.caption: f.value for f in _rail(world.admin, world.today)}
    for caption in ("avatud teemat", "üle tähtaja", "teemat töövõiduga", "teemat jõustunud aktiga"):
        assert head[caption] == reader[caption], caption
        assert head[caption] > 0, caption
        assert outsider[caption] == 0, caption


@pytest.mark.parametrize("viewer_name", ["head", "reader"])
def test_test_matters_change_no_rail_figure(viewer_name, world, pinned_today):
    viewer = getattr(world, viewer_name)
    originals = [world.native_open, world.restricted]
    _plant_year_facts(world, originals)
    before = [(f.caption, f.value) for f in _rail(viewer, world.today)]

    add_test_twins(world)
    _plant_year_facts(world, [_twin_of(m) for m in originals])
    after = [(f.caption, f.value) for f in _rail(viewer, world.today)]
    assert after == before


def test_every_rail_row_meets_a_test_twin(client, test_matters_present):
    """Without `andmed=` each link opens more — so the parity above is not vacuous."""
    world = test_matters_present
    client.force_login(world.head)
    new_year = world.today == date(world.today.year, 1, 1)
    for figure in _rail(world.head, world.today):
        if new_year and figure.caption == "teemat jõustunud aktiga":
            continue  # on 1 January nothing this year has taken effect yet
        assert figure.value > 0, figure.caption
        assert register_total(client, figure.url, andmed="") > figure.value, figure.caption


# ---------------------------------------------------------------------------
# The strip's «teemat töövõiduga <aasta>» — the same register population
# ---------------------------------------------------------------------------


def _strip_victory(viewer, today: date):
    """The strip's only register-counted figure; the other three are catalogued."""
    [figure] = overview_strip.strip({}, viewer, today, "kõik aastad")
    assert figure.caption == f"teemat töövõiduga {today.year}"
    return figure


@pytest.mark.parametrize("viewer_name", ["head", "reader"])
def test_the_strip_victory_figure_counts_and_opens_real_data_only(
    viewer_name, client, test_matters_present
):
    world = test_matters_present
    viewer = getattr(world, viewer_name)
    figure = _strip_victory(viewer, world.today)
    assert _query(figure.url).get("andmed") == [REGISTER_DATA_SCOPE]
    assert figure.url.count("andmed=") == 1
    assert figure.url.endswith(RESULTS_ANCHOR)

    client.force_login(viewer)
    assert register_total(client, figure.url) == figure.value
    # One real victory Matter and the restricted one besides — for the head and,
    # since docs/adr/0181, for a READER alike; their TEST twins are in the list
    # only when the scope is taken off.
    assert figure.value == 2
    assert register_total(client, figure.url, andmed="") == 2 * figure.value


def test_the_strip_victory_figure_ignores_test_matters(world, pinned_today):
    originals = [world.native_open, world.restricted]
    _plant_year_facts(world, originals)
    before = _strip_victory(world.head, world.today).value
    add_test_twins(world)
    _plant_year_facts(world, [_twin_of(m) for m in originals])
    assert _strip_victory(world.head, world.today).value == before


# ---------------------------------------------------------------------------
# The rendered page: the number a reader sees, and where its link goes
# ---------------------------------------------------------------------------

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


def _displayed(pattern: re.Pattern[str], body: str) -> dict[str, tuple[int, str]]:
    """Caption -> (printed number, href), for one of the two bands."""
    return {
        html.unescape(match["caption"]).strip(): (int(match["value"]), html.unescape(match["href"]))
        for match in pattern.finditer(body)
    }


def test_the_statistika_page_shows_real_figures_that_open_their_lists(client, test_matters_present):
    """Read off the rendered page, then followed through the register view."""
    world = test_matters_present
    client.force_login(world.head)
    response = client.get(reverse("reporting:overview"))
    assert response.status_code == 200
    body = response.content.decode()
    rail = _displayed(_RAIL_ROW, body)
    strip = _displayed(_STRIP_FIGURE, body)

    expected = [(rail, figure) for figure in _rail(world.head, world.today)]
    expected.append((strip, _strip_victory(world.head, world.today)))
    for shown, figure in expected:
        assert figure.value > 0, figure.caption
        value, href = shown[figure.caption]
        assert value == figure.value, figure.caption
        assert href == figure.url, figure.caption
        assert href.count("andmed=") == 1 and href.endswith(RESULTS_ANCHOR), href
        assert register_total(client, href) == value, (figure.caption, href)
        assert register_total(client, href, andmed="") > value, figure.caption

    # «avatud teemat» is printed twice: by the catalogue in the strip and by the
    # rail. Both count real data now, so the page says one thing about it.
    assert strip["avatud teemat"][0] == rail["avatud teemat"][0]
