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
STRIPS = [
    ("Osakond", "matters:department", {}),
    ("Minu asjad", "matters:my_work", {}),
    ("Saabunud", "matters:inbox", {}),
    ("Jälgimine", "intelligence:important_dates", {}),
    ("Statistika", "reporting:overview", {}),
]

FIGURE = re.compile(r'<(a|span)\s+class="seis__figure"(?:\s+href="([^"]*)")?')


#: Inside `sections.NEAR_DAYS`, so the seeded upcoming date lands in the window
#: «30 päeva jooksul» counts and in the section that figure points at.
NEAR_IN_DAYS = 10


def _seeded(owner, today):
    """Enough work that the strips are not empty and the zeros are not dropped.

    Ordinary Matters keep four of the five strips alive, and for a while that
    was the whole fixture — which left the fifth one parameterized over a page
    with no figures on it at all. `components/seis.html` drops zeros by design,
    so a Jälgimine strip counting nothing renders *nothing*, and every loop in
    this file iterated an empty list for that surface: each contract passed,
    none of them was asked. The one below, added with this seed, immediately
    found a case no assertion here had ever seen.

    Jälgimine counts `MatterImportantDate`, not Matters, so it needs its own
    seed. Both directions the strip reports are given one — a date still ahead
    and inside the thirty-day window, and one already passed — through
    `add_important_date`, the service the application writes them with, rather
    than a row pushed into the table behind it.
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


def test_the_jalgimine_strip_is_asked_a_question_with_an_answer(client, populated):
    """The seed above is load-bearing, so it is asserted rather than trusted.

    Everything in this file is a loop over five surfaces, and a loop is only as
    good as the world it runs in. Jälgimine counts a table nothing else here
    writes to, so before `_seeded` grew its two `add_important_date` calls the
    parameterized cases walked a page whose strip had been emptied by the
    zero-dropping rule: each contract held over nothing at all. A strip that
    counts nothing cannot link to the wrong list.

    So this pins the world rather than the markup. Delete the seed and the
    cases above stay green while quietly meaning nothing — this one fails and
    says why.

    Read off the view's own `seis` context rather than scraped back out of the
    HTML: the number the strip renders *is* `Figure.value`, and a regex that
    re-derived it would be asserting against its own parse.
    """
    client.force_login(populated)

    response = client.get(reverse("intelligence:important_dates"))
    figures = response.context["seis"]

    assert figures, "the Jälgimine strip rendered no figures at all"
    zeros = [figure.caption for figure in figures if figure.value <= 0]
    assert not zeros, (
        f"the Jälgimine strip counts nothing for {zeros}, so the contracts above "
        "loop over a page with no populations and prove nothing about it. Restore "
        "the MatterImportantDate seed in `_seeded`."
    )


def test_every_jalgimine_figure_opens_something_real(client, populated):
    """The linked half of the contract, on the strip that can now answer it.

    Two of these figures carry a query and one carries a fragment, and the
    difference matters: a `?suund=` is a filtered read of this page, so it is
    asserted the way every other drill-down in the product is — the destination
    holds exactly the number the figure printed. The fragment names a section of
    the page already on screen, so what it promises is that the section is
    there to scroll to.

    An honest destination that happens to be empty is the failure this catches:
    it is the same lie as `<a href="">`, told one navigation later.
    """
    client.force_login(populated)
    route = reverse("intelligence:important_dates")
    figures = client.get(route).context["seis"]

    for figure in figures:
        if figure.url.startswith("#"):
            body = client.get(route).content.decode()
            assert f'id="{figure.url[1:]}"' in body, (
                f"«{figure.caption}» points at {figure.url}, which this page does "
                "not render — the reader clicks and nothing moves"
            )
            continue

        landing = client.get(route + figure.url)
        assert landing.status_code == 200, f"«{figure.caption}» does not resolve"
        assert landing.context["total"] == figure.value, (
            f"«{figure.caption}» says {figure.value} and {figure.url} shows "
            f"{landing.context['total']}"
        )
        assert landing.context["page"].object_list, (
            f"«{figure.caption}» counted {figure.value} and opens an empty list"
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
