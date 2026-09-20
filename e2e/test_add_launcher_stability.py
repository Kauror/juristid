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

#: The top-level row, in the canonical order: the four families, then the one
#: control that ends a file rather than adding to it.
#:
#: **Four families where there were thirteen chips.** The bar grew to thirteen
#: because it was the product's inventory of record types, and the lawyer
#: standing in front of it does not have a record type in mind — so it asks what
#: *kind of thing* is being recorded and the rest is asked second, inside the
#: family chosen (docs/adr/0097 §8).
#:
#: `+ Lõpeta teema` is back at the end of it (docs/adr/0099 §5), which makes it
#: this file's business again: it is in the row, so it has to hold still like
#: everything else in the row, and opening it must not shift the four.
#:
#: The geometry contract itself is unchanged and is exactly what this file
#: exists to hold: fewer chips is allowed, more chips is allowed, a chip that
#: *moves* is not.
CANONICAL = [
    "+ Märge",
    "+ Kaasamine",
    "+ Arvamus / tagasiside",
    "+ Ülevaade / uudis",
    "+ Lõpeta teema",
]

#: The top-level panels, which is what the chips above open.
PANEL_IDS = [
    "lisa-marge",
    "lisa-kaasamine",
    "lisa-arvamus",
    "lisa-koduleht",
    "teema-lopeta",
]

#: The sub-choices, and which family each is inside. Opening one of these is the
#: nested case: it must move none of the four chips above it either.
SUBCHOICES = [
    ("lisa-marge", "marge-tavaline"),
    ("lisa-marge", "marge-tahtaeg"),
    ("lisa-marge", "marge-joustumine"),
    ("lisa-marge", "marge-toovoit"),
    ("lisa-arvamus", "arvamus-tagasiside"),
    ("lisa-arvamus", "arvamus-teiste"),
    ("lisa-arvamus", "arvamus-koja"),
]

#: Sub-pixel layout noise from a font metric or a scrollbar is not a jump. The
#: defect being guarded against moved chips by whole lines — tens of pixels.
TOLERANCE = 1.5


def chip_geometry(page) -> list[tuple[str, float, float]]:
    """Every launcher control: its label, and where it sits **inside the zone**.

    The **top-level** chips only — a direct-child selector, not a descendant
    one. The sub-choices inside `+ Märge` and `+ Arvamus / tagasiside` appear
    and disappear with their family, which is what they are for; counting them
    here would make this file assert that a control which is supposed to come
    and go does not (docs/adr/0097 §8).

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
                    '#lisa-teemale > .cx-panels > label.disclosure-chip'
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
    """Open one choice, and never close it by pressing it again.

    **A chip is a toggle.** `ux.js` un-checks a radio that is already chosen,
    so a blind click on a chip that arrives *checked* shuts its panel. That
    never mattered while every chip arrived unchecked; two of them arrive
    chosen now, because a family panel that opened on more chips and no form
    would be an extra click on every visit — `Tavaline` and
    `Meile saadetud tagasiside` (docs/adr/0097 §8).

    So this looks before it clicks, which is what `e2e/conftest.py`'s own
    `open_add_panel` has always done.
    """
    radio = page.locator(f"#{panel_id}-valik")
    if not radio.is_checked():
        press_chip(page, panel_id)
    return page.locator(f"#{panel_id}")


def press_chip(page, panel_id: str):
    """Press a chip whatever state it is in — which is how one is *closed*.

    `open_panel` above will not do this, on purpose. Separating the two is the
    whole of the fix for a toggle that arrives chosen: asking for it must be a
    no-op, and closing it must still be one click.
    """
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

    One click per chip, and after each one every control is where it was before
    the first — not merely where it was before *that* click, which a launcher
    that drifted one row at a time would also satisfy.

    `+ Lõpeta teema` is among them. Its panel is the tallest in the row and it
    is the one chip whose own rule (`--last`) gives it different spacing, which
    makes it the likeliest to push the row around (docs/adr/0099 §5).
    """
    sign_in(page, base_url, SANDRA)
    a_new_matter(page, base_url)

    resting = chip_geometry(page)
    assert [row[0] for row in resting] == CANONICAL

    for panel_id in PANEL_IDS:
        panel = open_panel(page, panel_id)

        expect(panel).to_be_visible()
        assert_unchanged(resting, chip_geometry(page), f"with {panel_id} open")


def test_no_launcher_control_moves_when_a_sub_choice_is_opened(page, base_url):
    """The nested case, which is new and is where the old defect would return.

    A family's panel is a `.cx-panels` of its own, one level in, and every rule
    that lays the outer row out is written `.cx-panels > …` rather than against
    a depth — so it reaches this group unchanged, and so a sub-choice's form
    takes `order: 1` inside its *family* rather than inside the launcher. If it
    did not, choosing `Oluline tähtaeg` would push `+ Kaasamine` sideways
    (docs/adr/0097 §8).
    """
    sign_in(page, base_url, SANDRA)
    a_new_matter(page, base_url)
    resting = chip_geometry(page)

    for family, choice in SUBCHOICES:
        open_panel(page, family)
        panel = open_panel(page, choice)

        expect(panel).to_be_visible()
        assert_unchanged(resting, chip_geometry(page), f"with {choice} open")


def test_the_chosen_chip_is_the_only_one_that_looks_chosen(page, base_url):
    """Blue, from the application's own accent tokens, and exactly one of them.

    Read as a computed colour rather than as a class name: a rule that stopped
    applying would leave the class in place and the chip looking untouched.
    """
    sign_in(page, base_url, SANDRA)
    a_new_matter(page, base_url)

    #: Each chip's own resting colour, read per chip rather than as one shared
    #: value: a rule that gave one of them a different quiet colour would
    #: otherwise read as the chosen state.
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
        "the chosen choice is not distinguished from the three others"
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
    # The **top-level** bar, by a direct-child selector. A descendant one now
    # reaches the sub-choice chips inside the panel that just opened, which are
    # below the form's own top by construction — so the measurement would be
    # asking whether a form opens below the chips it contains
    # (docs/adr/0097 §8).
    form_top, bottom_of_bar = page.evaluate(
        """() => {
            const form = document.getElementById('lisa-marge').getBoundingClientRect();
            const bar = [...document.querySelectorAll(
                '#lisa-teemale > .cx-panels > label.disclosure-chip'
            )].map(node => node.getBoundingClientRect().bottom);
            return [form.top, Math.max(...bar)];
        }"""
    )

    assert form_top >= bottom_of_bar - TOLERANCE, (
        "the form does not open below the last row of chips"
    )

    # One family open, and the rest shut. `marge-tavaline` is inside the open
    # one and is visible with it — that is the nesting, not a second open form
    # (docs/adr/0097 §8).
    shown = {"lisa-marge", "marge-tavaline"}
    for panel_id in PANEL_IDS + [choice for _, choice in SUBCHOICES]:
        expectation = expect(page.locator(f"#{panel_id}"))
        (expectation.to_be_visible() if panel_id in shown else expectation.not_to_be_visible())


def test_choosing_a_second_operation_replaces_the_form_and_moves_nothing(page, base_url):
    sign_in(page, base_url, SANDRA)
    a_new_matter(page, base_url)
    resting = chip_geometry(page)

    open_panel(page, "lisa-koduleht")
    expect(page.locator("#lisa-koduleht")).to_be_visible()

    open_panel(page, "lisa-marge")
    expect(page.locator("#lisa-marge")).to_be_visible()
    expect(page.locator("#lisa-koduleht")).not_to_be_visible()
    assert_unchanged(resting, chip_geometry(page), "after switching operations")


def test_choosing_the_active_operation_again_closes_it(page, base_url):
    """The one thing the browser's own radio group cannot do, added by `ux.js`."""
    sign_in(page, base_url, SANDRA)
    a_new_matter(page, base_url)
    resting = chip_geometry(page)

    open_panel(page, "lisa-kaasamine")
    expect(page.locator("#lisa-kaasamine")).to_be_visible()

    press_chip(page, "lisa-kaasamine")
    expect(page.locator("#lisa-kaasamine")).not_to_be_visible()
    assert_unchanged(resting, chip_geometry(page), "after closing the form again")


def test_the_launcher_is_operable_from_the_keyboard(page, base_url):
    """A radio group, which is a keyboard contract the browser already keeps."""
    sign_in(page, base_url, SANDRA)
    a_new_matter(page, base_url)

    page.locator("#lisa-marge-valik").focus()
    page.keyboard.press("ArrowDown")

    expect(page.locator("#lisa-kaasamine")).to_be_visible()
    assert page.evaluate("() => document.activeElement.id") == "lisa-kaasamine-valik"


def test_a_family_and_its_sub_choices_are_separate_keyboard_groups(page, base_url):
    """Arrowing inside `+ Märge` must not arrow *out* of it.

    The families share `lisa-valik` and each family's choices are a group of
    their own, so the browser's own radio behaviour is what keeps the two
    levels apart — press Down on `Oluline tähtaeg` and you reach `Jõustumine`,
    never `+ Kaasamine` (docs/adr/0097 §8).
    """
    sign_in(page, base_url, SANDRA)
    a_new_matter(page, base_url)

    open_panel(page, "lisa-marge")
    page.locator("#marge-tahtaeg-valik").focus()
    page.keyboard.press("ArrowDown")

    assert page.evaluate("() => document.activeElement.id") == "marge-joustumine-valik"
    expect(page.locator("#lisa-marge")).to_be_visible()


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
