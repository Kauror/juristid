"""Phone popovers stay on screen, and Dokumendid's row actions are there to use.

ENG-099. With every control closed, no page overflowed at phone width — the QA
sweep measured it that way. Opened, three popovers ran off the right edge and
widened the page (the saved-view link box by 73 px at 375, «Veel» by 66 px, the
sender picker by 264 px on a long ministry name), and Statistika's period switch
clipped «Kõik aastad» behind its own rounded corners.

ENG-098. Dokumendid's actions were invisible until the row was hovered — so on a
phone they were never there — the download and manage links were 6 and 10 px
glyphs, and every «Ava» was called «Ava».

Measured in the browser, at the widths the audit named, with each control
actually opened.
"""

from __future__ import annotations

import re

import pytest
from playwright.sync_api import expect

from app.core.management.commands.seed_e2e_data import OPEN_TITLE
from e2e.conftest import HEAD, MARTIN, create_matter, open_matter, sign_in, wait_for_htmx
from e2e.titles import unique_title

pytestmark = pytest.mark.e2e

WIDTHS = (320, 375, 420, 768)
ROUNDING = 1

OPENED = """([details, box]) => {
  const d = document.querySelector(details);
  d.open = true;
  const r = document.querySelector(box).getBoundingClientRect();
  return {overflow: document.scrollingElement.scrollWidth - window.innerWidth,
          left: r.left, right: r.right, width: window.innerWidth, height: r.height};
}"""

CLOSE = "(details) => { document.querySelector(details).open = false; }"


def _on_screen(reading: dict, what: str) -> None:
    assert reading["overflow"] <= ROUNDING, (what, reading)
    assert reading["left"] >= -ROUNDING, (what, reading)
    assert reading["right"] <= reading["width"] + ROUNDING, (what, reading)
    assert reading["height"] > 0, (what, reading)


def test_the_saved_view_box_and_the_more_menu_stay_on_screen(page, base_url):
    sign_in(page, base_url, MARTIN)
    for width in WIDTHS:
        page.set_viewport_size({"width": width, "height": 800})
        page.goto(f"{base_url}/teemad/")
        page.wait_for_load_state("networkidle")
        for details, box in (
            ("details.uxviews__save", ".uxviews__savebody"),
            ("details.topnav__more", ".topnav__menu"),
        ):
            _on_screen(page.evaluate(OPENED, [details, box]), f"{details} at {width}")
            page.evaluate(CLOSE, details)


def test_a_long_sender_name_does_not_widen_the_page(page, base_url):
    sign_in(page, base_url, MARTIN)
    page.set_viewport_size({"width": 1440, "height": 900})
    create_matter(page, base_url, unique_title("Pikk saatja"))
    picker = page.locator("#teema-saatja-muuda")
    picker.locator("summary").click()
    name = (
        "Majandus- ja Kommunikatsiooniministeeriumi Ettevõtluse ja Innovatsiooni "
        "Sihtasutuse Nõukogu Pikaajalise Arengukava Ettevalmistuskomisjon "
        + unique_title("Nõukogu")
    )
    picker.locator('input[name="sender_name"]').fill(name)
    picker.get_by_role("button", name="Salvesta saatjate muudatus").click()
    wait_for_htmx(page)
    url = page.url

    for width in WIDTHS:
        page.set_viewport_size({"width": width, "height": 800})
        page.goto(url)
        page.wait_for_load_state("networkidle")
        reading = page.evaluate(
            OPENED, ["#teema-saatja-muuda", "#teema-saatja-muuda fieldset.inlineform__group"]
        )
        _on_screen(reading, f"sender picker at {width}")
        # The whole name is still there, wrapped rather than cut.
        assert page.locator("#teema-saatja-muuda .checkitem__name", has_text=name[:40]).count() == 1


def test_every_period_is_visible_on_a_phone(page, base_url):
    sign_in(page, base_url, HEAD)
    for width in WIDTHS:
        page.set_viewport_size({"width": width, "height": 800})
        page.goto(f"{base_url}/statistika/")
        page.wait_for_load_state("networkidle")
        readings = page.evaluate(
            """() => {
              const nav = document.querySelector('nav.segmented[aria-label="Periood"]');
              const n = nav.getBoundingClientRect();
              return {overflow: document.scrollingElement.scrollWidth - window.innerWidth,
                      options: [...nav.querySelectorAll('a')].map(a => {
                        const r = a.getBoundingClientRect();
                        return {label: a.textContent.trim(), left: r.left, right: r.right,
                                navLeft: n.left, navRight: n.right, width: window.innerWidth};
                      })};
            }"""
        )
        assert readings["overflow"] <= ROUNDING, (width, readings)
        labels = [option["label"] for option in readings["options"]]
        assert "Kõik aastad" in labels, labels
        for option in readings["options"]:
            assert option["left"] >= option["navLeft"] - ROUNDING, (width, option)
            assert option["right"] <= option["navRight"] + ROUNDING, (width, option)
            assert option["right"] <= option["width"] + ROUNDING, (width, option)


ACTIONS = """() => {
  const rows = [...document.querySelectorAll('.doctable tbody tr')]
      .filter(row => row.querySelector('.doctable__actions'));
  return rows.map(row => {
    const actions = row.querySelector('.doctable__actions');
    return {
      opacity: getComputedStyle(actions).opacity,
      targets: [...actions.querySelectorAll('a, summary')].map(el => {
        const r = el.getBoundingClientRect();
        return {w: r.width, h: r.height};
      }),
    };
  });
}"""


def _documents(page, base_url):
    url = open_matter(page, base_url, OPEN_TITLE)
    page.goto(url.rstrip("/") + "/dokumendid/")
    page.wait_for_load_state("networkidle")


@pytest.mark.parametrize("hover", ["hover", "none"])
def test_document_actions_are_visible_at_rest_and_big_enough(page, base_url, hover):
    sign_in(page, base_url, MARTIN)
    width = 1440 if hover == "hover" else 375
    page.set_viewport_size({"width": width, "height": 900})
    if hover == "none":
        # A touch device: no hover, a coarse pointer.
        page.context.new_cdp_session(page).send(
            "Emulation.setEmulatedMedia",
            {
                "features": [
                    {"name": "hover", "value": "none"},
                    {"name": "pointer", "value": "coarse"},
                ]
            },
        )
    _documents(page, base_url)
    page.mouse.move(0, 0)

    rows = page.evaluate(ACTIONS)
    assert rows, "the seeded Teema has no documents; this test tests nothing"
    for row in rows:
        assert float(row["opacity"]) == 1.0, row
        for target in row["targets"]:
            assert target["w"] >= 24 - 0.5 and target["h"] >= 24 - 0.5, row


def test_each_open_link_names_its_document(page, base_url):
    sign_in(page, base_url, MARTIN)
    page.set_viewport_size({"width": 1440, "height": 900})
    _documents(page, base_url)

    shown = page.locator(".doctable__actions a", has_text="Ava")
    named = page.locator(".doctable__actions").get_by_role("link", name=re.compile(r"^Ava \S"))
    assert shown.count() > 0, "no «Ava» on the seeded Teema; this test tests nothing"
    expect(named).to_have_count(shown.count())
    names = named.evaluate_all("links => links.map(a => a.getAttribute('aria-label'))")
    assert len(set(names)) == len(names), names
