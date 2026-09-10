"""A figure with no destination is a span, on every surface that renders one.

`SeisFigure.url` is documented as legitimately empty — *"empty for a figure the
application cannot open exactly … an honest number beats a link to a different
list"* (`app/matters/department_dashboard.py`). Before Wave 7 only Osakond
honoured that. The other four strips emitted `<a href="{{ figure.url }}">`
unconditionally, so a URL-less figure rendered `<a href="">`: a control that
looks clickable, takes a keyboard tab stop, and reloads the page.

That was not hypothetical. `/statistika/` was serving one. `new_native_full_matters`
sets `url=""` deliberately — the figure is measured on the arrival date, and the
register filters on the reporting year, so linking there would open a population
selected on a different column — and the template linked it anyway.

**Why the visual suite cannot cover this.** `.seis__figure` carries
`text-decoration: none` and `color: inherit`, so `<a href="">` and `<span>` are
pixel-identical. What differs is a cursor and a tab stop. A screenshot records
neither.

**Why the e2e guard did not cover it either.** `e2e/test_statistics.py` asserted
that the count of `.seis__figure[href]` equalled the count of `.seis__figure` —
and `[href]` matches `href=""`. The test passed *because of* the defect it was
written to catch.

So the contract is asserted here, in Python, against the rendered HTML of every
surface that renders the strip. All five now share
`templates/components/seis.html`, which is what makes this one test rather than
five — but the test is written per surface on purpose: the reason they were
allowed to drift is that nothing ever asked them the same question.
"""

from __future__ import annotations

import re

import pytest
from django.urls import reverse

pytestmark = pytest.mark.django_db

#: Every surface that renders the Seis strip, and how to reach it.
#:
#: Jälgimine was the fifth, and it is gone with the pages behind it: `Töövõit`
#: and `Jõustumine` are structured filters on Teemad now and an `Oluline
#: tähtaeg` is its owner's own upcoming work (docs/adr/0071). The two contracts
#: that were written specifically for that strip went with it; the four
#: remaining surfaces keep every rule this file exists for.
STRIPS = [
    ("Osakond", "matters:department", {}),
    ("Minu asjad", "matters:my_work", {}),
    ("Saabunud", "matters:inbox", {}),
    ("Statistika", "reporting:overview", {}),
]

FIGURE = re.compile(r'<(a|span)\s+class="seis__figure"(?:\s+href="([^"]*)")?')


#: A fortnight out, so the seeded upcoming date lands inside every «tähtaeg
#: lähiajal» window the remaining strips count.
NEAR_IN_DAYS = 10


def _seeded(owner, today):
    """Enough work that the strips are not empty and the zeros are not dropped.

    `components/seis.html` drops zeros by design, so a strip counting nothing
    renders *nothing* — and a loop over a page with no figures on it passes
    every contract in this file while asking none of them. That is what the
    fixture is for, and it is why it seeds work rather than only Matters.

    The two `MatterImportantDate` rows are still written, and still through
    `add_important_date` rather than pushed into the table behind it. They no
    longer feed a strip of their own — the Jälgimine surface that counted them
    is retired (docs/adr/0071) — but an important deadline is dated work, so
    Minu asjad's «üle tähtaja» and Osakond's deadline windows both read them.
    One ahead and one already passed, so neither direction is a figure this
    file walks past unasked.
    """
    from datetime import timedelta

    from app.intelligence.services import add_important_date
    from app.matters.services import create_matter

    matters = [
        create_matter(
            title=f"Seisu teema {index}",
            owner=owner,
            reference_year=today.year,
            actor=owner,
            received_date=today,
        )
        for index in range(3)
    ]

    ahead = today + timedelta(days=NEAR_IN_DAYS)
    add_important_date(
        matter=matters[0],
        title="Eelnõu tagasiside tähtaeg",
        date_value=ahead,
        period_end=ahead,
        actor=owner,
    )
    passed = today - timedelta(days=5)
    add_important_date(
        matter=matters[1],
        title="Möödunud kooskõlastustähtaeg",
        date_value=passed,
        period_end=passed,
        actor=owner,
    )
    return matters


@pytest.fixture
def populated(department_head, specialist):
    from django.utils import timezone

    _seeded(specialist, timezone.localdate())
    return department_head


@pytest.mark.parametrize(("label", "route", "params"), STRIPS, ids=[s[0] for s in STRIPS])
def test_no_figure_offers_an_empty_destination(client, populated, label, route, params):
    """The whole contract, asked of every surface in the same words.

    An `<a href="">` reloads the current page. On a strip whose entire promise
    is «this number opens exactly the rows it counted», that is the worst
    available answer: it looks like the promise being kept.
    """
    client.force_login(populated)

    response = client.get(reverse(route), params)
    assert response.status_code == 200, f"{label} did not render"
    body = response.content.decode()

    empty = [m.group(0) for m in FIGURE.finditer(body) if m.group(1) == "a" and not m.group(2)]

    assert not empty, (
        f"{label} renders a figure that links to nowhere: {empty}. A figure the "
        "application cannot open exactly is a <span> — see components/seis.html."
    )


def test_the_statistika_strip_states_a_number_it_cannot_open(client, populated):
    """The live case, named rather than left to a general rule.

    «teemat kõik aastad» counts arrivals by `received_date` while the register
    filters by reporting year, so there is no list that matches it. The figure
    is therefore deliberately linkless, and it must render as a span — which is
    what this asserts, so a future edit that gives it a destination has to
    explain which list that is.
    """
    client.force_login(populated)

    body = client.get(reverse("reporting:overview")).content.decode()
    figures = FIGURE.findall(body)

    assert figures, "the Statistika strip rendered no figures at all"
    assert any(kind == "span" for kind, _ in figures), (
        "no figure on Statistika rendered as a span; if every figure now has an "
        "honest destination, say so here and delete this test"
    )


@pytest.mark.parametrize(("label", "route", "params"), STRIPS, ids=[s[0] for s in STRIPS])
def test_every_link_that_is_offered_is_real(client, populated, label, route, params):
    """The other half: a destination that exists must actually resolve.

    Stated separately from the span rule because the two fail for opposite
    reasons — one is a link that should not exist, this is a link that should
    and does not.

    Three destination shapes are in the product and all three are on-site: a
    path, a filtered read of the current page, and a fragment naming a section
    of it — «30 päeva jooksul» *is* the first section of Jälgimine, and linking
    a figure to the rows already under it is a destination like any other
    (`app/intelligence/views.py`). The fragment case went unasserted until the
    seed above gave that strip a number to print, so it is checked here for
    what it has to be: an id this very page renders. A `#` that scrolls
    nowhere is the same broken promise as an `href=""`.
    """
    client.force_login(populated)

    body = client.get(reverse(route), params).content.decode()
    links = [m.group(2) for m in FIGURE.finditer(body) if m.group(1) == "a"]

    for href in links:
        assert href, f"{label} emitted an anchor with no destination"
        assert href.startswith(("/", "?", "#")), f"{label} figure points off-site: {href}"
        if href.startswith("#"):
            assert f'id="{href[1:]}"' in body, (
                f"{label} figure points at {href}, which this page does not render"
            )


def test_the_component_is_the_only_renderer_of_the_strip():
    """Five copies is how they drifted in the first place.

    A sixth surface that hand-rolls the markup gets the `<a href="">` back
    without anything above noticing, because the tests here walk routes rather
    than templates.
    """
    from pathlib import Path

    templates = Path(__file__).resolve().parent.parent / "templates"
    component = templates / "components" / "seis.html"

    renderers = [
        str(path.relative_to(templates))
        for path in templates.rglob("*.html")
        if 'class="seis__figure"' in path.read_text(encoding="utf-8") and path != component
    ]

    assert not renderers, (
        "a template renders the Seis strip itself instead of including "
        f"components/seis.html: {renderers}"
    )
