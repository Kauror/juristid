"""UI-06..UI-08 in a real browser: the grouped Täpsem otsing and the status row.

* the panel spans the results' width, its groups lay out in columns, and the
  page never scrolls sideways at 1440, 1024, 768 or 390 px;
* the two disclosures open and shut, and a shut group still lists what it is
  filtering as chips whose `×` removes that one constraint and keeps the rest;
* a retired `?olek=arhiiv` link shows its archive filter as a chip;
* the form still works with JavaScript off: submit narrows, reset clears.

Synthetic seed only (`seed_e2e_data`).
"""

from __future__ import annotations

import re

import pytest
from playwright.sync_api import expect

from e2e.conftest import HEAD, sign_in

ARCHIVE_TITLE = "Arhiiviteema 2014 sünteetiline registrikirje"


def open_panel(page, base_url: str, query: str = "") -> None:
    page.goto(f"{base_url}/teemad/{query}")
    page.wait_for_load_state("networkidle")
    panel = page.locator("#tapsem-otsing")
    if not panel.evaluate("node => node.open"):
        panel.locator("> summary").click()
    expect(panel).to_have_js_property("open", True)


def overflow(page) -> int:
    return page.evaluate(
        "() => document.documentElement.scrollWidth - document.documentElement.clientWidth"
    )


@pytest.mark.parametrize("width", [1440, 1024, 768, 390])
def test_the_panel_uses_the_results_width_without_sideways_scroll(page, base_url, width):
    page.set_viewport_size({"width": width, "height": 900})
    sign_in(page, base_url, HEAD)
    open_panel(page, base_url, "?olek=koik")
    for key in ("asutused", "muud"):
        page.locator(f"#otsing-{key} > summary").click()
        expect(page.locator(f"#otsing-{key}")).to_have_js_property("open", True)

    assert overflow(page) <= 0, f"{width}px scrolls sideways by {overflow(page)}px"
    body = page.locator(".filterpanel__body").bounding_box()
    results = page.locator("#tulemused").bounding_box()
    assert body and results
    # Edge to edge with the results, not a 62rem column with an empty right half.
    assert abs(body["width"] - results["width"]) <= 2, (body, results)
    # Nothing inside sticks out of it.
    sticking_out = page.evaluate(
        """() => {
          const box = document.querySelector('.filterpanel__body').getBoundingClientRect();
          const fields = '.filterpanel__body input, .filterpanel__body select';
          return [...document.querySelectorAll(fields)]
            .filter(el => el.offsetParent !== null && el.type !== 'hidden')
            .filter(el => el.getBoundingClientRect().right > box.right + 1)
            .map(el => el.name);
        }"""
    )
    assert not sticking_out, sticking_out

    def columns(selector: str) -> int:
        return page.evaluate(
            f"() => getComputedStyle(document.querySelector('{selector}'))"
            ".gridTemplateColumns.split(' ').length"
        )

    if width == 1440:
        # Spread across the width: lists four-plus abreast, dates and
        # institutions three-plus.
        assert columns(".filtergroup__grid") >= 5
        assert columns(".filtergroup__dates") >= 4
        assert columns(".filtergroup__orgs") >= 3
    if width == 390:
        assert columns(".filtergroup__dates") == 1
        assert columns(".filtergroup__orgs") == 1

    # The main search box and the institution boxes take their column's width.
    search = page.locator("#teemad-otsing").bounding_box()
    assert search and search["width"] >= results["width"] * 0.6
    org = page.locator("#orgchooser-saatja input[type='search']").bounding_box()
    chooser = page.locator("#orgchooser-saatja").bounding_box()
    # The fieldset's own padding aside, the search box is the chooser's width.
    assert org and chooser and org["width"] >= chooser["width"] * 0.85


def test_a_shut_group_shows_its_constraints_as_removable_chips(page, base_url):
    sign_in(page, base_url, HEAD)
    open_panel(page, base_url, "?olek=koik&liik=ARCHIVE&materjalid=puudub&hetkeseis=puudub")

    group = page.locator("#otsing-muud")
    expect(group).to_have_js_property("open", False)
    expect(group.locator("> summary")).to_contain_text("2 valitud")
    chips = page.locator("#otsing-muud + .filtergroup__chips .filterchip")
    expect(chips).to_have_count(2)
    expect(chips.filter(has_text="Kirje liik")).to_contain_text("Arhiiv")

    # Opened, the controls say it and the chips step aside.
    group.locator("> summary").click()
    expect(page.locator("#otsing-muud + .filtergroup__chips")).to_be_hidden()
    expect(group.locator("input[name='liik'][value='ARCHIVE']")).to_be_checked()
    group.locator("> summary").click()
    expect(page.locator("#otsing-muud + .filtergroup__chips")).to_be_visible()

    # One × removes one constraint; the others stay.
    chips.filter(has_text="Kirje liik").click()
    page.wait_for_load_state("networkidle")
    assert "liik=" not in page.url
    assert "materjalid=puudub" in page.url and "hetkeseis=puudub" in page.url


def test_the_archive_is_a_kirje_liik_and_an_old_link_still_finds_it(page, base_url):
    sign_in(page, base_url, HEAD)
    page.goto(f"{base_url}/teemad/?olek=arhiiv")
    page.wait_for_load_state("networkidle")

    segments = page.locator('.segmented[aria-label="Kirje seis"] .segmented__option')
    expect(segments).to_have_count(3)
    expect(segments).to_have_text(
        [re.compile(r"^\s*Avatud"), re.compile(r"^\s*Suletud"), re.compile(r"^\s*Kõik")]
    )
    expect(page.locator(".table--register")).to_contain_text(ARCHIVE_TITLE)
    chip = page.locator("#teemad-tulemused .filterchip", has_text="Arhiiv (avatud ja suletud)")
    expect(chip).to_be_visible()

    chip.click()
    page.wait_for_load_state("networkidle")
    assert "olek=koik" in page.url and "arhiiv" not in page.url


def test_submit_and_reset_work_without_javascript(browser, browser_context_args, base_url):
    # The suite's own viewport, locale and time zone, with scripting off.
    context = browser.new_context(**browser_context_args, java_script_enabled=False)
    page = context.new_page()
    try:
        sign_in_without_js(page, base_url)
        page.goto(f"{base_url}/teemad/?olek=koik&kaupa=30")
        page.locator("#tapsem-otsing > summary").click()
        page.locator("input[type='checkbox'][name='hetkeseis'][value='puudub']").check()
        page.locator("#tapsem-otsing input[name='saabus_alates']").fill("1.1.2020")
        page.locator("#tapsem-otsing").get_by_role("button", name="Filtreeri").click()
        page.wait_for_load_state("load")
        assert "hetkeseis=puudub" in page.url and "saabus_alates=1.1.2020" in page.url
        assert "kaupa=30" in page.url

        page.locator("#tapsem-otsing > summary").click()
        page.locator("#tapsem-otsing").get_by_role("link", name="Tühjenda kõik").click()
        page.wait_for_load_state("load")
        assert "hetkeseis" not in page.url and "saabus_alates" not in page.url
        assert "kaupa=30" in page.url
    finally:
        context.close()


def sign_in_without_js(page, base_url: str) -> None:
    """The development login is a plain form, so it works with scripting off."""
    page.goto(f"{base_url}/konto/arendus-sisselogimine/")
    page.get_by_label(HEAD.display_name, exact=False).check()
    page.get_by_role("button", name="Logi sisse").click()
    page.wait_for_url(f"{base_url}/minu-asjad/")
