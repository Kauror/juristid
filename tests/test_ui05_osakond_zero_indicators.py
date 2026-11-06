"""UI-05: three Osakond figures leave the strip at zero, and only those three.

Owner's brief of 6 November 2026 §8: «tähtajaga teemat sel nädalal»,
«vastutajata» and «uut läbi vaatamata» disappear entirely at 0 — figure, `i`
and tooltip, so the flex gap leaves no hole — and come back on their own when
positive, in their existing order. Every other figure keeps Osakond's
show-the-zero rule, and no count, caption or link changes.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import itertools
import re

import pytest
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone

from app.matters import department_dashboard as dd
from tests import factories

pytestmark = pytest.mark.django_db

WEDNESDAY = dt.date(2026, 10, 7)
HIDDEN = ("week", "unassigned", "unreviewed")
ORDER = ["overdue", "week", "unassigned", "unreviewed", "no_action", "sent", "drafting"]


@pytest.fixture
def head():
    return factories.DepartmentHeadFactory()


@pytest.fixture
def frozen(monkeypatch):
    monkeypatch.setattr(timezone, "localdate", lambda *args, **kwargs: WEDNESDAY)
    return WEDNESDAY


def _strip(figures) -> str:
    return render_to_string("components/seis.html", {"figures": figures, "show_zeros": 1})


def _drawn(markup: str) -> list[str]:
    """The captions on the strip, in order."""
    return re.findall(r'<span class="seis__caption">([^<]+)</span>', markup)


def test_exactly_three_figures_are_flagged(head):
    figures = dd.seis_figures(head, WEDNESDAY)

    assert [figure.key for figure in figures] == ORDER
    assert {figure.key for figure in figures if figure.hidden_at_zero} == set(HIDDEN)


@pytest.mark.parametrize("values", list(itertools.product((0, 3), repeat=3)))
def test_every_zero_positive_combination_draws_the_strip_without_a_hole(head, values):
    """All eight combinations of the three, with the other four at zero."""
    base = {figure.key: figure for figure in dd.seis_figures(head, WEDNESDAY)}
    chosen = dict(zip(HIDDEN, values, strict=True))
    figures = [
        dataclasses.replace(figure, value=chosen.get(key, figure.value))
        for key, figure in base.items()
    ]
    markup = _strip(figures)

    expected = [base[key].caption for key in ORDER if key not in HIDDEN or chosen[key]]
    # Order kept; the four others always present, even at zero.
    assert _drawn(markup) == expected
    for key in HIDDEN:
        slug = base[key].help.slug
        if chosen[key]:
            assert f'id="seis-selgitus-{slug}"' in markup
            assert f'aria-describedby="seis-selgitus-{slug}"' in markup
        else:
            # No figure, no `i`, no tooltip: nothing to leave a gap behind.
            assert slug not in markup, f"«{base[key].caption}» drawn at 0"
    # One explanation per drawn figure, and every item well formed.
    assert markup.count('class="seis__info"') == len(expected)
    assert markup.count('<span class="seis__item"') == len(expected)


def test_the_flag_is_osakond_only_and_other_zeros_stay(head):
    """Without `show_zeros` nothing changes; with it only the three go."""
    figures = [dataclasses.replace(figure, value=0) for figure in dd.seis_figures(head, WEDNESDAY)]

    assert _drawn(render_to_string("components/seis.html", {"figures": figures})) == []
    assert len(_drawn(_strip(figures))) == 4


def test_the_counts_and_links_are_untouched(head, frozen):
    """The flag is presentation: a positive figure keeps its value and its list."""
    owner = factories.UserFactory()
    factories.MatterFactory(owner=None, received_date=WEDNESDAY - dt.timedelta(days=1))
    factories.MatterFactory(owner=owner)

    figures = {figure.key: figure for figure in dd.seis_figures(head, WEDNESDAY)}

    assert figures["unassigned"].value == 1
    assert figures["unreviewed"].value == 1
    assert "vastutaja=puudub" in figures["unassigned"].url
    assert "saabus_alates=" in figures["unreviewed"].url
    assert figures["week"].value == 0


def test_the_osakond_page_hides_the_three_at_zero_and_shows_them_when_positive(
    client, head, frozen
):
    client.force_login(head)

    empty = client.get(reverse("matters:department")).content.decode()
    strip = empty.split('<div class="seis">', 1)[1].split('<div class="ovbody">', 1)[0]
    assert "vastutajata</span>" not in strip, "«vastutajata» drawn at 0"
    assert "läbi vaatamata</span>" not in strip, "«uut läbi vaatamata» drawn at 0"
    assert "sel nädalal</span>" not in strip.replace("saadetud sel nädalal", "")
    assert "üle tähtaja</span>" in strip
    assert "järgmise tegevuseta</span>" in strip

    factories.MatterFactory(owner=None, received_date=WEDNESDAY)
    shown = client.get(reverse("matters:department")).content.decode()
    strip = shown.split('<div class="seis">', 1)[1].split('<div class="ovbody">', 1)[0]
    captions = _drawn(strip)
    assert captions.index("vastutajata") < captions.index("uus läbi vaatamata")
    assert captions.index("üle tähtaja") < captions.index("vastutajata")
