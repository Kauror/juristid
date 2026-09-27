"""The Teema page cleanup, in a real browser, at the widths it is read at.

`tests/test_teema_page_cleanup.py` proves what the server sends. This proves
what only a layout engine can say about it:

* `Kuupäev` under `+ Märge` and under `+ Arvamus / tagasiside` is the width of a
  date, its calendar button stays on the same row, and nothing pushes the page
  sideways at 1440, 768 or 375;
* `+ Lõpeta teema` opens straight onto its chips, with no heading row above them;
* `Menetluse kulg` on a file that sent three opinions draws one solid run ending
  at the current phase, and the browser resolves it that way;
* `Teema käik` draws «Arvamus välja» semibold and a `Märge` regular;
* neither section's heading takes any room, and both are still headings.

Everything here is synthetic.
"""

from __future__ import annotations

import re

import pytest
from playwright.sync_api import expect

from e2e.conftest import (
    SANDRA,
    open_add_panel,
    open_hetkeseis,
    sign_in,
    unique_title,
)

pytestmark = pytest.mark.e2e

MINISTRY = "Näidisministeerium"

#: Three opinions a season apart, all behind us on any day this suite runs, so
#: the rail they draw does not depend on the calendar.
OPINION_DAYS = ("10.02.2025", "15.05.2025", "01.09.2025")

WIDTHS = (1440, 768, 375)


def choose_organisation(page, picker: str, name: str = MINISTRY) -> None:
    """Through the picker's own search, as `e2e/test_substantive_history.py` does."""
    box = page.locator(f"#{picker}-otsi")
    box.click()
    box.fill("")
    box.type(name[:8], delay=20)
    page.locator(f"#{picker}-tulemused").get_by_role("option", name=name, exact=True).click()


def a_procedure_matter(page, base_url: str, label: str) -> str:
    """A `Seadus` on `Kooskõlastusringil`, filed through the real form.

    The shape the removed `Etapp` select rendered on, and the one whose rail has
    a current phase with phases ahead of it.
    """
    page.goto(f"{base_url}/teemad/uus/")
    page.wait_for_load_state("networkidle")
    page.fill("#id_title", unique_title(label))
    page.get_by_role("checkbox", name="Seadus", exact=True).check()
    open_hetkeseis(page)
    page.get_by_role("radio", name="Kooskõlastusringil", exact=True).check()
    page.get_by_role("button", name="Loo teema").click()
    page.wait_for_url(re.compile(r"/teemad/[0-9a-f-]{36}/$"))
    page.wait_for_load_state("networkidle")
    return page.url


def record_koja_arvamus(page, *, sent_on: str) -> None:
    """One sent `Koja arvamus`, through `+ Arvamus / tagasiside`."""
    open_add_panel(page, "arvamus-koja")
    form = page.locator("#arvamus-koja")
    form.locator("input[type=file]").set_input_files(
        {"name": f"arvamus-{sent_on}.pdf", "mimeType": "application/pdf", "buffer": b"%PDF-1.4"}
    )
    form.locator("[name=sent_on]").fill(sent_on)
    choose_organisation(page, "koja-adressaat")
    form.get_by_role("button", name="Registreeri arvamus").click()
    page.wait_for_load_state("networkidle")
    expect(page.locator("#ajalugu-loend")).to_contain_text(sent_on.lstrip("0").replace(".0", "."))


def a_matter_with_three_opinions(page, base_url: str) -> str:
    url = a_procedure_matter(page, base_url, "Kolm arvamust")
    for day in OPINION_DAYS:
        record_koja_arvamus(page, sent_on=day)
    return url


def assert_no_sideways_scroll(page) -> None:
    overflow = page.evaluate("document.documentElement.scrollWidth - window.innerWidth")
    assert overflow <= 0, f"the page scrolls sideways by {overflow}px"


def date_geometry(label) -> dict:
    """The label's width, and whether the calendar button shares the box's row."""
    return label.evaluate(
        """el => {
          const box = el.querySelector('input').getBoundingClientRect();
          const trigger = el.querySelector('.datepicker__trigger').getBoundingClientRect();
          const own = el.getBoundingClientRect();
          return {
            width: own.width,
            sameRow: Math.abs(trigger.top + trigger.height / 2 - (box.top + box.height / 2)) <= 4,
            inside: trigger.right <= own.right + 1,
            panel: el.closest('.cx-panel__body').getBoundingClientRect().width,
          };
        }"""
    )


# ---------------------------------------------------------------------------
# The panels: `+ Märge`, `+ Arvamus / tagasiside`, `+ Lõpeta teema`
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("width", WIDTHS)
def test_the_panels_are_compact_and_fit(page, base_url, width):
    sign_in(page, base_url, SANDRA)
    page.set_viewport_size({"width": width, "height": 900})
    a_procedure_matter(page, base_url, f"Paneelid {width}")

    # `+ Märge`: no `Etapp`, and `Kuupäev` the width of a date.
    open_add_panel(page, "marge-tavaline")
    marge = page.locator("#marge-tavaline")
    expect(marge.locator("[name=process_phase]")).to_have_count(0)
    expect(marge).not_to_contain_text("Etapp")
    geometry = date_geometry(marge.locator("label.cx-f--solo"))
    assert geometry["width"] <= 161, geometry
    assert geometry["sameRow"] and geometry["inside"], geometry
    assert_no_sideways_scroll(page)

    # `+ Arvamus / tagasiside` opens on `Meile saadetud tagasiside`.
    open_add_panel(page, "arvamus-tagasiside")
    feedback = page.locator("#arvamus-tagasiside")
    geometry = date_geometry(feedback.locator("label.cx-f--solo"))
    assert geometry["width"] <= 161, geometry
    if width > 375:
        # It used to run nearly the whole panel.
        assert geometry["width"] < geometry["panel"] / 2, geometry
    assert geometry["sameRow"] and geometry["inside"], geometry
    assert_no_sideways_scroll(page)

    # `+ Lõpeta teema`: straight onto the chips, then `Lõppsõna`.
    open_add_panel(page, "teema-lopeta")
    closing = page.locator("#teema-lopeta")
    body_top = closing.locator(".cx-panel__body").evaluate("el => el.getBoundingClientRect().top")
    first_chip = closing.locator(".uxchip").first
    chip_top = first_chip.evaluate("el => el.getBoundingClientRect().top")
    # The body's own padding and nothing else: no label row above the chips.
    assert chip_top - body_top <= 20, (body_top, chip_top)
    expect(closing.locator(".uxchip")).to_have_count(3)
    expect(closing.locator("[name=closing_words]")).to_be_visible()
    expect(closing).not_to_contain_text("Teema läheb arhiivi")
    assert_no_sideways_scroll(page)


# ---------------------------------------------------------------------------
# `Menetluse kulg`, `Teema käik` and the two headings, on the owner's case
# ---------------------------------------------------------------------------


def reaches(page) -> list[float]:
    values = page.locator(".lprail .tl-step").evaluate_all(
        "nodes => nodes.map(n => getComputedStyle(n).getPropertyValue('--tl-reach').trim())"
    )
    return [float(value.rstrip("%")) / 100 for value in values]


def test_three_opinions_draw_one_run_and_the_page_reads_quietly(page, base_url):
    sign_in(page, base_url, SANDRA)
    a_matter_with_three_opinions(page, base_url)
    page.locator("#ajalugu-loend").get_by_text("Arvamus välja").first.wait_for()

    rail = page.locator(".lprail")
    expect(rail.locator(".tl-step--current")).to_have_count(1)
    expect(rail.locator(".tl-step--milestone").filter(has_text="Koja arvamus")).to_have_count(3)

    # One run: muted, solid, then muted — resolved by the browser, not read off
    # the markup.
    shape = "".join("0" if r == 0 else "1" if r == 1 else "p" for r in reaches(page))
    assert re.fullmatch(r"0*1+p?0*", shape), shape
    kinds = rail.locator(".tl-step").evaluate_all(
        "nodes => nodes.map(n => n.classList.contains('tl-step--current') ? 'C' :"
        " n.classList.contains('tl-step--milestone') ? 'M' : 'P')"
    )
    current = kinds.index("C")
    assert shape[current:].strip("0") == "", (kinds, shape)
    assert set(shape[kinds.index("M") : current]) == {"1"}, (kinds, shape)

    # `Teema käik`: the opinion heavier than a note.
    open_add_panel(page, "marge-tavaline")
    page.locator("#marge-tavaline [name=title]").fill("Rääkisin ministeeriumiga")
    page.locator("#marge-tavaline").get_by_role("button", name="Salvesta", exact=True).click()
    page.wait_for_load_state("networkidle")
    history = page.locator("#ajalugu-loend")
    note = history.locator("article.uxtl__item").filter(has_text="Rääkisin ministeeriumiga")
    expect(note).to_have_count(1)

    def weight(row) -> int:
        return int(
            row.locator(".uxtl__mswhat").first.evaluate("el => getComputedStyle(el).fontWeight")
        )

    opinion = history.locator("article.uxtl__item--primary").filter(has_text="Arvamus välja").first
    assert weight(opinion) == 600
    assert weight(note) == 400
    expect(note).to_have_class(re.compile(r"\buxtl__item--secondary\b"))
    expect(note.get_by_role("button", name=re.compile("Muuda"))).to_be_visible()

    # Neither heading takes room, and both are still level-two headings.
    for name in ("Menetluse kulg", "Teema käik"):
        heading = page.get_by_role("heading", name=name, level=2)
        expect(heading).to_have_count(1)
        box = heading.bounding_box()
        assert box is not None and box["width"] <= 1 and box["height"] <= 1, (name, box)
    expect(page.locator("#ajajoon .uxtl__count")).to_be_visible()

    for width in (768, 375):
        page.set_viewport_size({"width": width, "height": 900})
        page.wait_for_timeout(100)
        assert_no_sideways_scroll(page)
        expect(rail.locator(".tl-step--current")).to_have_count(1)
