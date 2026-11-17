"""Owner's Statistika round, 2026-11-17.

1. Ajalugu, Andmekvaliteet and Definitsioonid leave Statistika's tab strip and
   are reached from Haldus → Rakendus, where they show only each other.
2. The tab strip sits right under the heading on every Statistika page.
3. The sent-opinions list sorts by any column.
4. A late opinion is marked «!», with the delay in the tooltip.
"""

from __future__ import annotations

import datetime as dt

import pytest
from django.urls import reverse

from app.legacy_import.lovable_sync_models import OpinionOutcome
from tests import factories
from tests.test_statistika_dashboard import _imported

pytestmark = pytest.mark.django_db

TODAY = dt.date(2026, 11, 16)
_REAL = __import__("django.utils.timezone", fromlist=["localdate"]).localdate


@pytest.fixture(autouse=True)
def frozen_today(monkeypatch):
    def frozen(value=None, timezone=None):
        return TODAY if value is None else _REAL(value, timezone)

    monkeypatch.setattr("django.utils.timezone.localdate", frozen)


def _tab_labels(body: str) -> list[str]:
    nav = body[body.index('aria-label="Statistika vaated"') :]
    nav = nav[: nav.index("</nav>")]
    return [part.split("</a>")[0].strip() for part in nav.split('">')[1:] if "</a>" in part]


@pytest.mark.parametrize(
    "route",
    ["reporting:dashboard", "reporting:overview", "reporting:matters", "reporting:activity"],
)
def test_statistika_has_four_tabs_right_under_the_heading(client, specialist, route):
    client.force_login(specialist)

    body = client.get(reverse(route)).content.decode()

    assert _tab_labels(body) == ["Ülevaade", "Seis", "Teemad", "Tegevus"]
    tabs = body.index('aria-label="Statistika vaated"')
    assert body.index('class="pagehead__title"') < tabs
    if 'class="filters"' in body:
        assert tabs < body.index('class="filters"')


@pytest.mark.parametrize(
    "route", ["reporting:historical", "reporting:quality", "reporting:definitions"]
)
def test_the_data_pages_show_only_each_other_and_belong_to_haldus(client, specialist, route):
    client.force_login(specialist)

    response = client.get(reverse(route))

    assert response.status_code == 200
    assert _tab_labels(response.content.decode()) == ["Ajalugu", "Andmekvaliteet", "Definitsioonid"]
    assert response.context["nav_active"] == "haldus"


def test_haldus_links_to_the_three_data_pages(client, specialist):
    client.force_login(specialist)

    body = client.get(reverse("haldus:index")).content.decode()

    for route in ("reporting:historical", "reporting:quality", "reporting:definitions"):
        assert f'href="{reverse(route)}"' in body


def _three(owner):
    a = factories.MatterFactory(owner=owner, title="Beeta teema")
    _imported(a, OpinionOutcome.SENT, dt.date(2026, 6, 10), request_deadline=dt.date(2026, 6, 5))
    b = factories.MatterFactory(owner=owner, title="Alfa teema")
    _imported(b, OpinionOutcome.SENT, dt.date(2026, 7, 1), request_deadline=dt.date(2026, 7, 20))
    c = factories.MatterFactory(owner=owner, title="Gamma teema")
    _imported(c, OpinionOutcome.SENT, dt.date(2026, 5, 2))


def test_the_list_sorts_by_a_column_and_back(client, specialist):
    _three(specialist)
    client.force_login(specialist)
    url = reverse("reporting:opinion_rows")

    default = client.get(url)
    by_title = client.get(url, {"jarjesta": "teema"})
    reversed_title = client.get(url, {"jarjesta": "-teema"})
    by_deadline = client.get(url, {"jarjesta": "-tahtaeg"})

    assert [f.matter.title for f in default.context["rows"]] == [
        "Alfa teema",
        "Beeta teema",
        "Gamma teema",
    ]
    assert [f.matter.title for f in by_title.context["rows"]] == [
        "Alfa teema",
        "Beeta teema",
        "Gamma teema",
    ]
    assert [f.matter.title for f in reversed_title.context["rows"]] == [
        "Gamma teema",
        "Beeta teema",
        "Alfa teema",
    ]
    assert [f.matter.title for f in by_deadline.context["rows"]] == [
        "Alfa teema",
        "Beeta teema",
        "Gamma teema",
    ]
    teema = next(h for h in by_title.context["headers"] if h["label"] == "Teema")
    assert teema["sort"] == "ascending"
    assert "jarjesta=-teema" in teema["url"]
    assert 'aria-sort="ascending"' in by_title.content.decode()


def test_an_unknown_order_falls_back_to_newest_first(client, specialist):
    _three(specialist)
    client.force_login(specialist)

    response = client.get(reverse("reporting:opinion_rows"), {"jarjesta": "-pahatahtlik"})

    assert response.context["rows"][0].matter.title == "Alfa teema"
    assert all(h["sort"] == "" for h in response.context["headers"])


def test_a_late_opinion_is_marked_with_the_delay_in_the_tooltip(client, specialist):
    _three(specialist)
    client.force_login(specialist)

    body = client.get(reverse("reporting:opinion_rows")).content.decode()

    assert "hiljem" not in body
    assert 'title="Saadetud 5 päeva pärast tähtaega (10.06.2026)"' in body
    assert body.count('class="statrows__late"') == 1
