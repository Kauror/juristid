"""The four simplified `Lisa teemale` panels, at the widths a lawyer uses them at.

`tests/test_teema_composer_simplification.py` settles what each panel asks and
what it writes. This settles the half of docs/adr/0095 that only a running
browser can: that the four panels are usable on a phone, that the organisation
control really searches, and that the controls the panels kept are the ones a
person can actually reach.

**Four widths, because the answers differ.** 375 is the phone the rest of this
suite measures, 420 is the wider phone the composer's two-column rows reflow at,
768 is the tablet where they stop reflowing, and 1440 is the desktop the
workspace's `--layout-workspace-max` caps.
"""

from __future__ import annotations

import pytest
from playwright.sync_api import expect

from e2e.conftest import SANDRA, create_matter, open_add_panel, sign_in, unique_title

pytestmark = pytest.mark.e2e

MINISTRY = "Näidisministeerium"

#: The four panels this round simplified, by the id their launcher radio names.
PANELS = [
    "arvamus-koja",
    "arvamus-teiste",
    "arvamus-tagasiside",
    "lisa-koduleht",
]

WIDTHS = [375, 420, 768, 1440]


def a_matter_with_a_sender(page, base_url: str) -> str:
    """A Teema carrying `MINISTRY` as `Saatja`.

    `+ Koja arvamus` opens its `Adressaadid` on the Teema's senders, so a file
    with none would measure the empty control rather than the one a lawyer
    meets (docs/adr/0095 §1).
    """
    return create_matter(page, base_url, unique_title("Komposiitor"), sender=MINISTRY)


@pytest.mark.parametrize("width", WIDTHS)
def test_no_simplified_panel_makes_the_page_scroll_sideways(page, base_url, width):
    """Measured on the document, with each panel open in turn.

    A panel is only laid out once it is open — `.cx-panel` is collapsed until
    its radio is checked — so a width test that never opened one would measure
    the launcher bar and report nothing about the forms.
    """
    sign_in(page, base_url, SANDRA)
    a_matter_with_a_sender(page, base_url)
    page.set_viewport_size({"width": width, "height": 900})

    for panel_id in PANELS:
        open_add_panel(page, panel_id)
        overflow = page.evaluate(
            "() => document.documentElement.scrollWidth - document.documentElement.clientWidth"
        )
        assert overflow <= 0, f"{panel_id} makes the Teema page scroll sideways at {width}px"


@pytest.mark.parametrize("width", WIDTHS)
def test_every_control_the_panels_kept_stays_inside_the_page(page, base_url, width):
    """Nothing is clipped off the right edge — measured element by element.

    Document overflow is the coarse question and this is the fine one: a control
    that sticks out of a container with `overflow: hidden` costs no scrollbar and
    is still half unreachable, which is how a date picker's calendar button and a
    file control's «Vali failid» both used to disappear.
    """
    sign_in(page, base_url, SANDRA)
    a_matter_with_a_sender(page, base_url)
    page.set_viewport_size({"width": width, "height": 900})

    for panel_id in PANELS:
        open_add_panel(page, panel_id)
        clipped = page.evaluate(
            """(id) => {
                const root = document.getElementById(id);
                const limit = document.documentElement.clientWidth;
                return [...root.querySelectorAll('input, textarea, button, label, .chip')]
                    .filter(node => {
                        const box = node.getBoundingClientRect();
                        return box.width > 0 && box.right > limit + 1;
                    })
                    .map(node => `${node.tagName}#${node.id || ''}.${node.className}`);
            }""",
            panel_id,
        )
        assert not clipped, f"{panel_id} clips {clipped} at {width}px"


@pytest.mark.parametrize("width", WIDTHS)
def test_every_panel_keeps_its_action_button_reachable(page, base_url, width):
    sign_in(page, base_url, SANDRA)
    a_matter_with_a_sender(page, base_url)
    page.set_viewport_size({"width": width, "height": 900})

    for panel_id in PANELS:
        open_add_panel(page, panel_id)
        expect(page.locator(f"#{panel_id} button[type=submit]")).to_be_visible()


@pytest.mark.parametrize("width", [375, 1440])
def test_the_organisation_search_is_usable_on_every_panel_that_has_one(page, base_url, width):
    """Type a fragment, get one match, choose it — on all three pickers.

    The control is `quiet` on each of them since docs/adr/0095, so at rest there
    is a box and no catalogue; a test that only checked the box existed would
    pass on a search that finds nothing.
    """
    sign_in(page, base_url, SANDRA)
    a_matter_with_a_sender(page, base_url)
    page.set_viewport_size({"width": width, "height": 900})

    for panel_id, picker in (
        ("arvamus-teiste", "valine-seisukoht"),
        ("arvamus-tagasiside", "tagasiside"),
        ("arvamus-koja", "koja-adressaat"),
    ):
        open_add_panel(page, panel_id)
        box = page.locator(f"#{picker}-otsi")
        expect(box).to_be_visible()
        box.click()
        box.fill("")
        box.type(MINISTRY[:8], delay=20)
        option = page.locator(f"#{picker}-tulemused").get_by_role(
            "option", name=MINISTRY, exact=True
        )
        expect(option).to_be_visible()
        option.click()
        chosen = page.locator(f"#{picker}-valik .orgfind__chips .chip", has_text=MINISTRY)
        expect(chosen.first).to_be_visible()


def test_the_koja_panel_reads_as_the_four_answers_it_asks(page, base_url):
    """File, day, addressees, summary — and no catalogue and no title box."""
    sign_in(page, base_url, SANDRA)
    a_matter_with_a_sender(page, base_url)
    open_add_panel(page, "arvamus-koja")

    panel = page.locator("#arvamus-koja")
    expect(panel.locator("input[type=file]")).to_have_count(1)
    expect(panel.locator("[name=sent_on]")).to_be_visible()
    expect(panel.locator("#koja-adressaat-otsi")).to_be_visible()
    expect(panel.locator("textarea[name=summary]")).to_be_visible()
    expect(panel.locator("[name=title]")).to_have_count(0)
    # At rest the picker shows what has been chosen and nothing else: the
    # Teema's sender, and no wall behind it (docs/adr/0095 §1).
    visible_chips = panel.locator("#koja-adressaat-valik .orgfind__chips .chip:visible")
    expect(visible_chips).to_have_count(1)
    expect(visible_chips.first).to_contain_text(MINISTRY)
