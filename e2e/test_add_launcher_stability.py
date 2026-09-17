"""`LISA TEEMALE` does not move when you use it.

The defect this file exists to keep out, measured on 2026-09-14: the launcher's
choices were `<details>`, and an open one took `flex: 1 1 100%; order: 1`. So
clicking `+ Kaasamine` sent the chip that had just been clicked to the head of
the next line and pushed every other chip along with it — the control moved out
from under the cursor at the exact moment somebody had chosen it, and picking a
second operation moved them all again.

Only a browser can prove the fix, because the claim is geometric: every
launcher control keeps its **bounding box** when a form opens. So that is what
is asserted — x and y of each chip, before and after every click, at desktop and
at phone width, including after an HTMX validation swap.

The markup half (the order, the radio group, the chip/panel split) is
`tests/test_stable_add_launcher.py`. It is cheap and it runs everywhere; this
is the half it cannot see.

**Everything here happens on a Matter the test creates**, because a launcher
that is stable only on a file with no chronology would be a launcher that is
not stable.
"""

from __future__ import annotations

import pytest
from playwright.sync_api import expect

from e2e.conftest import SANDRA, create_matter, sign_in, unique_title

pytestmark = pytest.mark.e2e

#: The canonical order. `+ Järgmine tegevus` is absent while a step is open, and
#: this Matter is new, so all thirteen are here.
#:
#: **Thirteen where there were thirteen**, and the growth is docs/adr/0091's stated
#: cost rather than a slip: the bar is the product's inventory of what can be
#: recorded, and four of the things lawyers do had no chip. `+ Väline seisukoht`
#: became two — `+ Meile saadetud tagasiside` and `+ Teiste arvamus` — over one
#: record and one panel partial, and `+ Koja arvamus` and `+ Menetluse areng`
#: joined them. The geometry contract below is unchanged and is exactly what this
#: file exists to hold: more chips is allowed, a chip that *moves* is not.
CANONICAL = [
    "+ Märge",
    "+ Järgmine tegevus",
    "+ Kaasamine",
    "+ Oluline tähtaeg",
    "+ Jõustumine",
    "+ Töövõit",
    "+ Ülevaade / uudis",
    # `+ Väline seisukoht` became two chips in docs/adr/0091 §3: one record
    # and one panel partial, named by how what it holds reached the file.
    "+ Meile saadetud tagasiside",
    "+ Teiste arvamus",
    "+ Koja arvamus",
    "+ Menetluse areng",
    "+ Menetluse link",
    "+ Lõpeta teema",
]

PANEL_IDS = [
    "lisa-marge",
    "lisa-jargmine",
    "lisa-kaasamine",
    "lisa-tahtaeg",
    "lisa-joustumine",
    "lisa-toovoit",
    "lisa-koduleht",
    "lisa-tagasiside",
    "lisa-valine-seisukoht",
    "lisa-koja-arvamus",
    "lisa-menetluse-areng",
    "lisa-menetluse-link",
    "lisa-lopeta",
]

#: Sub-pixel layout noise from a font metric or a scrollbar is not a jump. The
#: defect being guarded against moved chips by whole lines — tens of pixels.
TOLERANCE = 1.5


def chip_geometry(page) -> list[tuple[str, float, float]]:
    """Every launcher control: its label, and where it sits **inside the zone**.

    Measured against `#lisa-teemale` rather than against the viewport, because
    two of the states being compared are separated by an HTMX swap and a scroll:
    `bounding_box()` is viewport-relative, so a page that scrolled by 117px
    would report a launcher that had not moved as one that had. Offsets inside
    the zone answer the actual claim — that the bar's own geometry does not
    change — and they are what a person watching the chips sees.
    """
    return [
        (label.strip(), float(x), float(y))
        for label, x, y in page.evaluate(
            """() => {
                const zone = document.getElementById('lisa-teemale').getBoundingClientRect();
                return [...document.querySelectorAll(
                    '#lisa-teemale label.disclosure-chip'
                )].map(node => {
                    const box = node.getBoundingClientRect();
                    return [node.innerText, box.x - zone.x, box.y - zone.y];
                });
            }"""
        )
    ]


def assert_unchanged(before, after, what: str) -> None:
    assert [row[0] for row in after] == [row[0] for row in before], (
        f"{what}: the launcher reordered itself"
    )
    for (label, x0, y0), (_, x1, y1) in zip(before, after, strict=True):
        assert abs(x1 - x0) <= TOLERANCE and abs(y1 - y0) <= TOLERANCE, (
            f"{what}: {label} moved from ({x0:.0f}, {y0:.0f}) to ({x1:.0f}, {y1:.0f})"
        )


def open_panel(page, panel_id: str):
    page.locator(f'label[for="{panel_id}-valik"]').click()
    page.wait_for_timeout(80)
    return page.locator(f"#{panel_id}")


def a_new_matter(page, base_url: str) -> str:
    return create_matter(page, base_url, unique_title("Valikuriba"))


# ---------------------------------------------------------------------------
# Desktop
# ---------------------------------------------------------------------------


def test_no_launcher_control_moves_when_a_form_is_opened(page, base_url):
    """The primary regression, chip by chip.

    Eight clicks, and after each one every control is where it was before the
    first — not merely where it was before *that* click, which a launcher that
    drifted one row at a time would also satisfy.
    """
    sign_in(page, base_url, SANDRA)
    a_new_matter(page, base_url)

    resting = chip_geometry(page)
    assert [row[0] for row in resting] == CANONICAL

    for panel_id in PANEL_IDS:
        panel = open_panel(page, panel_id)

        expect(panel).to_be_visible()
        assert_unchanged(resting, chip_geometry(page), f"with {panel_id} open")


def test_the_chosen_chip_is_the_only_one_that_looks_chosen(page, base_url):
    """Blue, from the application's own accent tokens, and exactly one of them.

    Read as a computed colour rather than as a class name: a rule that stopped
    applying would leave the class in place and the chip looking untouched.
    """
    sign_in(page, base_url, SANDRA)
    a_new_matter(page, base_url)

    #: Each chip's own resting colour. `+ Lõpeta teema` is quieter than the eleven
    #: above it on purpose, so one shared "quiet" value would be a colour no
    #: last chip ever has.
    resting = {
        panel_id: page.locator(f'label[for="{panel_id}-valik"]').evaluate(
            "n => getComputedStyle(n).color"
        )
        for panel_id in PANEL_IDS
    }

    open_panel(page, "lisa-kaasamine")
    chosen = page.locator('label[for="lisa-kaasamine-valik"]')
    active = chosen.evaluate("n => getComputedStyle(n).color")

    assert active != resting["lisa-kaasamine"], (
        "the chosen choice is not distinguished from the eleven others"
    )
    accent = page.evaluate(
        "() => getComputedStyle(document.documentElement).getPropertyValue('--accent-link').trim()"
    )
    assert accent, "the accent token this leans on is gone"
    assert chosen.evaluate(
        "(n, want) => { const p = document.createElement('span');"
        " p.style.color = want; document.body.appendChild(p);"
        " const same = getComputedStyle(p).color === getComputedStyle(n).color;"
        " p.remove(); return same; }",
        accent,
    ), "the chosen chip is not the application's accent colour"

    for panel_id in PANEL_IDS:
        if panel_id == "lisa-kaasamine":
            continue
        other = page.locator(f'label[for="{panel_id}-valik"]')
        assert other.evaluate("n => getComputedStyle(n).color") == resting[panel_id], panel_id


def test_the_form_opens_below_the_whole_row_and_only_one_does(page, base_url):
    """Under the *bar*, not under its own chip and not beside it."""
    sign_in(page, base_url, SANDRA)
    a_new_matter(page, base_url)

    open_panel(page, "lisa-marge")
    form_top, bottom_of_bar = page.evaluate(
        """() => {
            const form = document.getElementById('lisa-marge').getBoundingClientRect();
            const bar = [...document.querySelectorAll(
                '#lisa-teemale label.disclosure-chip'
            )].map(node => node.getBoundingClientRect().bottom);
            return [form.top, Math.max(...bar)];
        }"""
    )

    assert form_top >= bottom_of_bar - TOLERANCE, (
        "the form does not open below the last row of chips"
    )

    for panel_id in PANEL_IDS:
        expectation = expect(page.locator(f"#{panel_id}"))
        (
            expectation.to_be_visible()
            if panel_id == "lisa-marge"
            else expectation.not_to_be_visible()
        )


def test_choosing_a_second_operation_replaces_the_form_and_moves_nothing(page, base_url):
    sign_in(page, base_url, SANDRA)
    a_new_matter(page, base_url)
    resting = chip_geometry(page)

    open_panel(page, "lisa-lopeta")
    expect(page.locator("#lisa-lopeta")).to_be_visible()

    open_panel(page, "lisa-marge")
    expect(page.locator("#lisa-marge")).to_be_visible()
    expect(page.locator("#lisa-lopeta")).not_to_be_visible()
    assert_unchanged(resting, chip_geometry(page), "after switching operations")


def test_choosing_the_active_operation_again_closes_it(page, base_url):
    """The one thing the browser's own radio group cannot do, added by `ux.js`."""
    sign_in(page, base_url, SANDRA)
    a_new_matter(page, base_url)
    resting = chip_geometry(page)

    open_panel(page, "lisa-toovoit")
    expect(page.locator("#lisa-toovoit")).to_be_visible()

    open_panel(page, "lisa-toovoit")
    expect(page.locator("#lisa-toovoit")).not_to_be_visible()
    assert_unchanged(resting, chip_geometry(page), "after closing the form again")


def test_the_launcher_is_operable_from_the_keyboard(page, base_url):
    """A radio group, which is a keyboard contract the browser already keeps."""
    sign_in(page, base_url, SANDRA)
    a_new_matter(page, base_url)

    page.locator("#lisa-marge-valik").focus()
    page.keyboard.press("ArrowDown")

    expect(page.locator("#lisa-jargmine")).to_be_visible()
    assert page.evaluate("() => document.activeElement.id") == "lisa-jargmine-valik"


# ---------------------------------------------------------------------------
# The HTMX swap, which is the state the old launcher was worst in
# ---------------------------------------------------------------------------


def test_a_refused_save_leaves_the_launcher_exactly_where_it_was(page, base_url):
    """A validation error replaces `#teema-vaade` wholesale.

    So this is not only "the CSS holds": it is "the server re-renders the same
    bar, with the same operation chosen, in the same places" — and the person
    reading the refusal has not had the page move under them while reading it.
    """
    sign_in(page, base_url, SANDRA)
    a_new_matter(page, base_url)
    resting = chip_geometry(page)

    open_panel(page, "lisa-kaasamine")
    page.locator("#lisa-kaasamine [name=response_count]").fill("3")
    page.locator("#lisa-kaasamine [name=audience]").fill("")
    page.locator("#lisa-kaasamine button[type=submit]").click()
    page.wait_for_load_state("networkidle")

    panel = page.locator("#lisa-kaasamine")
    expect(panel).to_be_visible()
    expect(panel).to_contain_text("Kirjuta, keda kaasati")
    expect(panel.locator("[name=response_count]")).to_have_value("3")
    assert_unchanged(resting, chip_geometry(page), "after a refused save")


# ---------------------------------------------------------------------------
# Narrow
# ---------------------------------------------------------------------------


def test_at_phone_width_the_chips_wrap_and_stay_on_their_rows(page, base_url):
    """The bar may wrap at 375px. What it may not do is re-wrap when a form
    opens: a chip that changes which row it is on has moved as surely as one
    that changes column."""
    sign_in(page, base_url, SANDRA)
    page.set_viewport_size({"width": 375, "height": 812})
    a_new_matter(page, base_url)
    page.wait_for_timeout(120)

    resting = chip_geometry(page)
    rows = sorted({round(row[2]) for row in resting})
    assert len(rows) > 1, (
        "at 375px the thirteen chips fit on one line — retune this test, not the CSS"
    )

    for panel_id in PANEL_IDS:
        panel = open_panel(page, panel_id)
        expect(panel).to_be_visible()

        after = chip_geometry(page)
        assert_unchanged(resting, after, f"at 375px with {panel_id} open")
        assert sorted({round(row[2]) for row in after}) == rows, (
            f"at 375px opening {panel_id} re-wrapped the launcher"
        )

    assert not page.evaluate(
        "() => document.documentElement.scrollWidth > document.documentElement.clientWidth + 1"
    ), "an open panel makes the Teema page scroll sideways at 375px"


# ---------------------------------------------------------------------------
# The two dates `+ Kaasamine` asks for
# ---------------------------------------------------------------------------


def test_the_engagement_panel_shows_both_dates_and_only_one_default(page, base_url):
    """`Kaasamise kuupäev` pre-filled, `Tagasisidet ootame kuni` empty.

    The date used to be stamped by the server with no box on the screen, so what
    is being checked is not that a field exists but that the value a save would
    store is *visible before* the save.
    """
    sign_in(page, base_url, SANDRA)
    a_new_matter(page, base_url)

    open_panel(page, "lisa-kaasamine")
    panel = page.locator("#lisa-kaasamine")

    expect(panel.get_by_text("Kaasamise kuupäev")).to_be_visible()
    assert panel.locator("[name=occurred_on]").input_value(), (
        "the engagement date opens empty, so today is being applied where nobody can see it"
    )
    # And the reply-by date **not at all**, since docs/adr/0091 §2 narrowed
    # docs/adr/0086 §2: a completed act does not acquire a managed wait nobody
    # asked for, and an empty box is still a question. It moved to
    # `Ootan tagasisidet` on the round's own chronology row, with the three spans
    # travelling with it, which is what makes the narrowing affordable.
    expect(panel.locator("[name=feedback_deadline]")).to_have_count(0)
    expect(panel.get_by_text("Tagasisidet ootame kuni")).to_have_count(0)
