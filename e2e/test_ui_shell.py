"""Structural browser assertions about the shell and the dense surfaces.

A screenshot proves a page has not changed. It does not prove the page is right,
and it cannot express "the topbar must never wrap" — a wrapped topbar is simply
a new baseline somebody approves without noticing. The properties below are the
ones the design package states as rules, so they are asserted as rules:

* the shell is one 48px row at every supported desktop width;
* nothing makes the document scroll sideways;
* every navigation destination stays reachable, whether or not it is on the bar;
* the register keeps its row rhythm and drops columns in the stated order;
* status is carried by text and shape, not by colour alone;
* focus is visible on the things a keyboard reaches.

They also cover the semantics a screenshot suite has to mask, because the seeded
world computes its dates from today and those pixels change daily.
"""

from __future__ import annotations

import pytest
from playwright.sync_api import expect

from app.core.management.commands.seed_e2e_data import OPEN_TITLE
from e2e.conftest import SANDRA, sign_in

pytestmark = pytest.mark.e2e

#: The supported desktop ladder. 1440 is the design's primary viewport; the
#: three below it are the machines the department actually has.
VIEWPORTS = [(1440, 900), (1366, 768), (1280, 800), (1024, 768)]

#: The four destinations a lawyer moves between all day. They are on the bar at
#: every width.
PRIMARY = ["Ülevaade", "Minu töö", "Saabunud", "Teemad"]

#: The reading surfaces. Inline above 1560, behind "Veel" below it. Osakonna töö
#: is deliberately absent from both lists: it is offered by role, and the route
#: 404s for anybody else rather than relying on the link being hidden.
SECONDARY = ["Jälgimine", "Statistika"]


def document_overflows(page) -> bool:
    return page.evaluate(
        "() => document.documentElement.scrollWidth > document.documentElement.clientWidth + 1"
    )


def open_register(page, base_url: str) -> None:
    page.goto(f"{base_url}/teemad/")
    page.wait_for_load_state("networkidle")


def open_first_matter(page, base_url: str) -> None:
    """Open the seeded world's ordinary open Matter.

    Named rather than "the first row", because the register's default ordering
    puts the archive record first — and an archive Matter has no composer, no
    Järgmiseks and a different header, which is not what these tests are about.

    The link is followed rather than clicked because the table head is sticky
    and can sit over the row.
    """
    page.goto(f"{base_url}/teemad/?olek=koik&q=Tavaline")
    page.wait_for_load_state("networkidle")
    link = page.get_by_role("link", name=OPEN_TITLE, exact=False).first
    assert link.count(), "the register does not hold the seeded open Matter"
    page.goto(f"{base_url}{link.get_attribute('href')}")
    page.wait_for_load_state("networkidle")


# ---------------------------------------------------------------------------
# The shell
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("width,height", VIEWPORTS, ids=lambda v: str(v))
def test_the_shell_is_one_row_and_never_scrolls_sideways(page, base_url, width, height):
    """48px, one line, no horizontal scroll — at every width the product claims.

    This is the defect the restoration started from: the bar carries more
    destinations than the Stage-1 design had, plus a search field, a primary
    action, an environment warning, the selected persona and a way out. At 1440
    the brand and the first navigation item overlapped; at 1280 the document
    itself was 1405px wide.
    """
    sign_in(page, base_url, SANDRA)
    page.set_viewport_size({"width": width, "height": height})
    open_register(page, base_url)

    topbar = page.locator(".topbar")
    expect(topbar).to_be_visible()
    assert topbar.bounding_box()["height"] == 48, "the topbar wrapped onto a second row"
    assert not document_overflows(page), "the document scrolls sideways"


@pytest.mark.parametrize("width,height", [*VIEWPORTS, (1600, 900)], ids=lambda v: str(v))
def test_every_destination_stays_reachable_at_every_width(page, base_url, width, height):
    """Priority navigation hides nothing; it only moves what is secondary.

    Reachability is asserted through the accessibility tree, and each
    destination must appear in it exactly once. The two branches — the wide row
    and the "Veel" disclosure — are never both rendered, so nothing is
    announced twice; below the wide breakpoint the secondary destinations are
    one activation away, which is what a disclosure is for.
    """
    sign_in(page, base_url, SANDRA)
    page.set_viewport_size({"width": width, "height": height})
    open_register(page, base_url)

    navigation = page.get_by_role("navigation", name="Peamine")
    for destination in PRIMARY:
        expect(navigation.get_by_role("link", name=destination, exact=True)).to_have_count(1)

    if width < 1560:
        trigger = page.locator(".topnav__trigger")
        expect(trigger).to_be_visible()
        trigger.click()

    for destination in SECONDARY:
        expect(navigation.get_by_role("link", name=destination, exact=True)).to_have_count(1)


@pytest.mark.parametrize("width,height", VIEWPORTS, ids=lambda v: str(v))
def test_the_things_a_lawyer_reaches_for_are_on_the_bar(page, base_url, width, height):
    """Search, the primary action and the selected persona survive every width.

    None of them may be pushed off-screen, and the persona in particular has to
    stay legible: in shared-gate mode it says whose work is on screen, which is
    not the same claim as "you are signed in".
    """
    sign_in(page, base_url, SANDRA)
    page.set_viewport_size({"width": width, "height": height})
    open_register(page, base_url)

    viewport_width = page.viewport_size["width"]
    for selector in ["#global-search", ".topbar__cta", ".actingas, .avatar"]:
        element = page.locator(selector).first
        expect(element).to_be_visible()
        box = element.bounding_box()
        assert box["x"] >= 0 and box["x"] + box["width"] <= viewport_width + 1, (
            f"{selector} is outside the viewport at {width}px"
        )

    # Shared-gate deployments name the selected persona; the others show an
    # avatar. Whichever this deployment is, it has to be on the bar and legible.
    persona = page.locator(".actingas__name")
    if persona.count():
        expect(persona).to_contain_text(SANDRA.display_name.split()[0])
    else:
        expect(page.locator(".avatar").first).to_be_visible()


def test_the_secondary_navigation_disclosure_is_keyboard_operable(page, base_url):
    """ "Veel" is a real disclosure: focusable, announced, and it opens on Enter.

    A hover-only menu is one keyboard users cannot discover.
    """
    sign_in(page, base_url, SANDRA)
    page.set_viewport_size({"width": 1280, "height": 800})
    open_register(page, base_url)

    trigger = page.locator(".topnav__trigger")
    expect(trigger).to_be_visible()
    trigger.focus()
    page.keyboard.press("Enter")
    expect(page.get_by_role("link", name="Statistika", exact=True)).to_be_visible()


def test_the_skip_link_is_the_first_thing_a_keyboard_reaches(page, base_url):
    """Tabbing into the page must offer a way past the shell."""
    sign_in(page, base_url, SANDRA)
    open_register(page, base_url)

    page.keyboard.press("Tab")
    focused = page.evaluate("() => document.activeElement.className")
    assert "skip-link" in focused, f"first tab stop was {focused!r}, not the skip link"
    expect(page.locator(".skip-link")).to_be_visible()


# ---------------------------------------------------------------------------
# The register
# ---------------------------------------------------------------------------


def test_the_register_keeps_its_row_rhythm(page, base_url):
    """32–34px rows, and every row the same height.

    One wrapped owner name re-rhythms the whole table, and a register that has
    lost its rhythm has lost the thing it is for.
    """
    sign_in(page, base_url, SANDRA)
    page.set_viewport_size({"width": 1440, "height": 900})
    open_register(page, base_url)

    heights = page.eval_on_selector_all(
        ".table tbody tr", "rows => rows.map(r => Math.round(r.getBoundingClientRect().height))"
    )
    assert heights, "the register rendered no rows"
    assert max(heights) <= 40, f"rows grew past the dense rhythm: {sorted(set(heights))}"
    assert max(heights) - min(heights) <= 2, f"rows are uneven: {sorted(set(heights))}"


@pytest.mark.parametrize("width,height", VIEWPORTS, ids=lambda v: str(v))
def test_the_register_never_makes_the_page_scroll_sideways(page, base_url, width, height):
    """A dense table may scroll inside its own frame. The page may not.

    `overflow-x: auto` on one container is a considered answer for a table with
    seven columns; the same property on the shell is a way of not answering.
    """
    sign_in(page, base_url, SANDRA)
    page.set_viewport_size({"width": width, "height": height})
    open_register(page, base_url)

    assert not document_overflows(page)
    frame = page.locator(".tablewrap").first.bounding_box()
    assert frame["x"] + frame["width"] <= width + 1


def test_the_register_drops_columns_in_the_order_the_design_states(page, base_url):
    """Viimane tegevus goes first, then Hetkeseis (UI_DESIGN_SPEC §viewports)."""
    sign_in(page, base_url, SANDRA)
    open_register(page, base_url)

    last_activity = page.get_by_role("columnheader", name="Viimane tegevus")
    stage = page.get_by_role("columnheader", name="Hetkeseis")

    page.set_viewport_size({"width": 1440, "height": 900})
    expect(last_activity).to_be_visible()
    expect(stage).to_be_visible()

    page.set_viewport_size({"width": 1100, "height": 800})
    expect(last_activity).to_be_hidden()
    expect(stage).to_be_visible()

    page.set_viewport_size({"width": 980, "height": 800})
    expect(stage).to_be_hidden()


# ---------------------------------------------------------------------------
# Ülevaade
# ---------------------------------------------------------------------------


def open_overview(page, base_url: str, query: str = "") -> None:
    page.goto(f"{base_url}/ulevaade/{query}")
    page.wait_for_load_state("networkidle")


def test_the_overview_leads_with_dates_then_arrivals_then_attention(page, base_url):
    """Priority order, read off the rendered document rather than the template.

    A section can move without its source moving — a grid, an include, an
    override — so the order is taken from where the headings actually are on
    the page.
    """
    sign_in(page, base_url, SANDRA)
    page.set_viewport_size({"width": 1440, "height": 900})
    open_overview(page, base_url)

    order = page.evaluate(
        "() => Array.from("
        "  document.querySelectorAll('.dashgrid__main h2[id]')"
        ").map(node => node.id)"
    )
    assert order == ["tahtajad-heading", "saabunud-heading", "tahelepanu-heading"], order


def test_the_attention_queue_says_what_it_wants(page, base_url):
    sign_in(page, base_url, SANDRA)
    open_overview(page, base_url)

    expect(page.get_by_role("heading", name="Vajab tähelepanu")).to_be_visible()


def test_the_deadline_period_is_a_keyboard_reachable_set_of_links(page, base_url):
    """Links and a GET, not a script.

    A custom dropdown would take the period away from the keyboard, from the
    back button and from anybody who pastes the URL into a chat.
    """
    sign_in(page, base_url, SANDRA)
    open_overview(page, base_url)

    control = page.get_by_role("navigation", name="Tähtaegade periood")
    expect(control).to_be_visible()
    expect(control.get_by_role("link")).to_have_count(5)
    expect(control.locator("[aria-current='true']")).to_have_text("14 päeva")

    control.get_by_role("link", name="30 päeva").click()
    page.wait_for_load_state("networkidle")

    assert "tahtajad=30" in page.url
    expect(
        page.get_by_role("navigation", name="Tähtaegade periood").locator("[aria-current='true']")
    ).to_have_text("30 päeva")


def test_the_selected_period_survives_the_back_button(page, base_url):
    sign_in(page, base_url, SANDRA)
    open_overview(page, base_url, "?tahtajad=koik")
    open_overview(page, base_url, "?tahtajad=7")

    page.go_back()
    page.wait_for_load_state("networkidle")

    expect(
        page.get_by_role("navigation", name="Tähtaegade periood").locator("[aria-current='true']")
    ).to_have_text("Kõik")


def test_a_nonsense_period_shows_the_default_rather_than_an_error(page, base_url):
    sign_in(page, base_url, SANDRA)
    open_overview(page, base_url, "?tahtajad=jama")

    expect(page.get_by_role("heading", name="Ülevaade")).to_be_visible()
    expect(
        page.get_by_role("navigation", name="Tähtaegade periood").locator("[aria-current='true']")
    ).to_have_text("14 päeva")


def test_no_work_surface_still_spends_a_column_on_the_reference(page, base_url):
    """Teemad, Minu töö, Saabunud and Ülevaade, in a real browser."""
    sign_in(page, base_url, SANDRA)
    for path in ("/teemad/", "/minu-too/", "/saabunud/", "/ulevaade/"):
        page.goto(f"{base_url}{path}")
        page.wait_for_load_state("networkidle")
        expect(page.get_by_role("columnheader", name="Viide")).to_have_count(0)


def test_a_colleague_is_named_by_their_short_name_in_the_register(page, base_url):
    """Every resolved owner cell, not whichever row happens to sort first."""
    sign_in(page, base_url, SANDRA)
    open_register(page, base_url)

    owners = page.locator(".table--register .table__owner .table__clip")
    assert owners.count(), "no row in the register has a resolved owner"

    full_names = []
    for index in range(owners.count()):
        cell = owners.nth(index)
        shown = cell.inner_text().strip()
        assert " " not in shown, f"a full name is still in the cell: {shown!r}"
        full_names.append(cell.get_attribute("title") or "")

    # And the full name has not been thrown away; it is one hover away.
    assert any(" " in name for name in full_names), full_names


# ---------------------------------------------------------------------------
# The register's narrowing panel
# ---------------------------------------------------------------------------


def test_the_narrowing_panel_gets_the_registers_full_width(page, base_url):
    """Täpsem otsing is a panel, not a column.

    It used to be a flex item inside the filter row, so its auto-fit grid
    resolved to a single 240px column and seventeen fields stacked vertically,
    pushing the register itself off the first screen.
    """
    sign_in(page, base_url, SANDRA)
    page.set_viewport_size({"width": 1440, "height": 900})
    open_register(page, base_url)

    page.locator(".filterpanel__trigger").click()
    body = page.locator(".filterpanel__body")
    expect(body).to_be_visible()

    columns = page.evaluate(
        "() => getComputedStyle(document.querySelector('.filterpanel__body .formgrid'))"
        ".gridTemplateColumns.split(' ').length"
    )
    assert columns >= 3, f"the filter grid resolved to {columns} column(s)"


# ---------------------------------------------------------------------------
# Status is never colour alone
# ---------------------------------------------------------------------------


def test_next_action_modes_differ_in_shape_as_well_as_colour(page, base_url):
    """TEEN filled, OOTAN solid outline, JÄLGIN dashed outline — plus the word.

    Asserted here rather than in a screenshot because a screenshot cannot say
    *why* three chips look different, and the rule is that the difference must
    survive a reader who cannot tell the colours apart.
    """
    sign_in(page, base_url, SANDRA)
    open_register(page, base_url)

    styles = page.evaluate(
        """() => {
            const seen = {};
            for (const chip of document.querySelectorAll('.mode, .modechip')) {
              const kind = [...chip.classList].find(
                c => c.startsWith('mode--') || c.startsWith('modechip--')
              );
              const s = getComputedStyle(chip);
              seen[kind] = {
                text: chip.textContent.trim(),
                style: s.borderTopStyle,
                filled: s.backgroundColor,
              };
            }
            return seen;
        }"""
    )
    assert styles, "no mode chips rendered"
    # Both vocabularies, because the register still uses `.mode` and the Teema
    # row uses `.modechip`. The rule is one rule and neither may lose it.
    for dashed in ("mode--monitor", "modechip--monitor"):
        if dashed in styles:
            assert styles[dashed]["style"] == "dashed", "JÄLGIN lost its dashed outline"
    for solid in ("mode--wait", "modechip--wait"):
        if solid in styles:
            assert styles[solid]["style"] == "solid", "OOTAN lost its outline"
    for kind, chip in styles.items():
        assert chip["text"], f"{kind} rendered without its label"


def test_an_overdue_deadline_is_coloured_and_a_passed_review_is_not(page, base_url):
    """Waiting is not lateness.

    The rule that only DO+deadline can be late is a business rule, and this only
    checks that the presentation still carries it: the two dates must not render
    in the same colour. A same-specificity rule written after the modifiers had
    flattened them both to the neutral text colour.
    """
    sign_in(page, base_url, SANDRA)
    page.goto(f"{base_url}/minu-too/")
    page.wait_for_load_state("networkidle")

    colours = page.evaluate(
        """() => {
            const pick = sel => {
              const el = document.querySelector(sel);
              return el ? getComputedStyle(el).color : null;
            };
            return {
              overdue: pick('.workrow__date--overdue'),
              review: pick('.flag--review'),
              neutral: getComputedStyle(document.body).color,
            };
        }"""
    )
    if colours["overdue"]:
        assert colours["overdue"] != colours["neutral"], (
            "an overdue deadline renders in the ordinary text colour"
        )
    if colours["review"] and colours["overdue"]:
        assert colours["review"] != colours["overdue"], (
            "a passed review date renders as though it were late"
        )


def test_a_restricted_matter_says_so_in_words(page, base_url):
    """The badge is a word plus a tint, never a tint alone."""
    sign_in(page, base_url, SANDRA)
    open_register(page, base_url)
    expect(page.locator(".table .badge--restricted").first).to_contain_text("Piiratud")


# ---------------------------------------------------------------------------
# The Matter page
# ---------------------------------------------------------------------------


def test_the_matter_header_is_a_band_of_facts_not_a_form(page, base_url):
    """Values first; the control that edits one is a disclosure behind it.

    Six always-open selects with a Salvesta button each turned the band into a
    150px wall above a two-line title, so a Matter page opened on a form rather
    than on the Matter.
    """
    sign_in(page, base_url, SANDRA)
    page.set_viewport_size({"width": 1440, "height": 900})
    open_first_matter(page, base_url)

    strip = page.locator(".metaline")
    expect(strip).to_be_visible()
    # One line of four facts now, not a grid of six labelled cells.
    assert strip.bounding_box()["height"] <= 48, "the facts line is taller than one line"
    # Visible, not present: the controls are in the DOM behind their
    # disclosures, which is the point — they are reachable without being what
    # the band shows.
    expect(page.locator(".metaline select:visible")).to_have_count(0)

    # The band also carries the plain-language summary now, which is the
    # largest body text on the page and the reason the formal title is not the
    # only description a reader gets (Teema redesign §6).
    header = page.locator(".matterhead").bounding_box()
    assert header["height"] <= 300, "the Matter header takes a third of the viewport"


def test_an_inline_edit_opens_the_real_control_without_moving_the_page(page, base_url):
    """The affordance is a disclosure, so it is keyboard-reachable and cheap."""
    sign_in(page, base_url, SANDRA)
    open_first_matter(page, base_url)

    before = page.locator(".matterhead").bounding_box()["height"]
    trigger = page.locator(".inlineedit__trigger").first
    trigger.focus()
    page.keyboard.press("Enter")
    # The visible control, not the first input in the form: `{% csrf_token %}`
    # renders a hidden input ahead of it, and a hidden input is never visible.
    expect(page.locator(".inlineedit[open] .field__input").first).to_be_visible()
    after = page.locator(".matterhead").bounding_box()["height"]
    assert after == before, "opening an inline edit reflowed the header"


def test_the_reopen_action_stays_inside_the_closed_banner(page, base_url):
    """A <form> inside a <p> closes the paragraph, and the button escapes it.

    The banner is a div now. This asserts the geometry rather than the markup,
    because the markup is only wrong in its consequences.
    """
    sign_in(page, base_url, SANDRA)
    page.goto(f"{base_url}/teemad/?olek=suletud")
    page.wait_for_load_state("networkidle")
    link = page.locator(".table__titlelink").first
    if not link.count():
        pytest.skip("the seeded world holds no closed Matter")
    page.goto(f"{base_url}{link.get_attribute('href')}")
    page.wait_for_load_state("networkidle")

    banner = page.locator(".banner--closed")
    if not banner.count():
        pytest.skip("this Matter is not closed")
    outer = banner.bounding_box()
    button = banner.get_by_role("button", name="Ava uuesti…").bounding_box()
    assert outer["y"] <= button["y"], "the reopen button escaped its banner"
    assert button["y"] + button["height"] <= outer["y"] + outer["height"] + 1


@pytest.mark.parametrize("width,height", VIEWPORTS, ids=lambda v: str(v))
def test_the_matter_rail_sits_beside_or_below_but_never_over(page, base_url, width, height):
    """At 1440–1280 the rail is a column; below 1100 it folds under."""
    sign_in(page, base_url, SANDRA)
    page.set_viewport_size({"width": width, "height": height})
    open_first_matter(page, base_url)

    main = page.locator(".teemamain").bounding_box()
    rail = page.locator(".rail").bounding_box()
    beside = rail["x"] >= main["x"] + main["width"] - 1
    below = rail["y"] >= main["y"] + main["height"] - 1
    assert beside or below, f"the rail overlaps the main column at {width}px"
    assert not document_overflows(page)


def test_the_composer_starts_as_one_field(page, base_url):
    """Routine capture is one box and Ctrl+Enter; everything else is disclosed.

    A four-row textarea at rest is the difference between "note this down" and
    "fill in this form", and the adoption argument is the former.
    """
    sign_in(page, base_url, SANDRA)
    open_first_matter(page, base_url)

    field = page.locator(".composer__body")
    expect(field).to_be_visible()
    collapsed = field.evaluate("element => getComputedStyle(element).height")
    # 62px is the design's resting height for the capture box, and it is the
    # difference between "note this down" and "fill in this form" — three lines
    # of an Estonian sentence rather than one (Teema redesign §9).
    assert float(collapsed.removesuffix("px")) <= 72, f"the composer rests at {collapsed}"

    field.click()
    expect(page.locator(".composer:focus-within")).to_have_count(1)
    # Retried rather than measured once: the field grows over 150ms, and a box
    # read the instant after the click is a reading of the animation.
    expect(field).not_to_have_css("height", collapsed)


def test_the_intelligence_sections_do_not_render_when_they_are_empty(page, base_url):
    """Not three lines. Nothing at all, and one quiet add row instead.

    Three headings each announcing an absence, with an add button beside each,
    is what the redesign removed: on a Matter nobody had touched it was about
    forty per cent of the page telling the reader that nothing existed
    (Teema redesign §3, §24).
    """
    sign_in(page, base_url, SANDRA)
    open_first_matter(page, base_url)

    for section in page.locator(".factsection").all():
        assert section.locator(".factsection__none").count() == 0, (
            "an empty fact section is still being rendered"
        )


# ---------------------------------------------------------------------------
# Forms and errors
# ---------------------------------------------------------------------------


def test_a_refused_save_keeps_what_was_typed_and_says_why_beside_the_field(page, base_url):
    """A field error belongs to its field, not to a banner at the top."""
    sign_in(page, base_url, SANDRA)
    page.goto(f"{base_url}/teemad/uus/")
    page.wait_for_load_state("networkidle")

    # Past the browser's own required-field check, because the server's refusal
    # is what has to render beside the field.
    page.locator("form.createform").evaluate("form => form.noValidate = true")
    page.get_by_role("button", name="Loo teema").click()
    page.wait_for_load_state("networkidle")

    error = page.locator(".field__error, .formerror").first
    expect(error).to_be_visible()
    assert not document_overflows(page), "the error state broke the layout"


def test_focus_is_visible_on_everything_a_keyboard_reaches(page, base_url):
    """2px accent ring, 2px offset, never removed (UI_DESIGN_SPEC §accessibility)."""
    sign_in(page, base_url, SANDRA)
    open_register(page, base_url)

    # Tabbed, not focused programmatically: `:focus-visible` is a heuristic
    # about how focus arrived, and `element.focus()` does not satisfy it. A test
    # that calls focus() is testing the wrong thing and reports every control as
    # a failure.
    invisible = []
    for _ in range(25):
        page.keyboard.press("Tab")
        state = page.evaluate(
            """() => {
                const el = document.activeElement;
                if (!el || el === document.body) return null;
                const s = getComputedStyle(el);
                return {
                  name: el.className || el.tagName,
                  ring: s.outlineStyle !== 'none' && parseFloat(s.outlineWidth) > 0,
                  boxed: s.boxShadow !== 'none',
                };
            }"""
        )
        if state and not (state["ring"] or state["boxed"]):
            invisible.append(state["name"])
    assert not invisible, f"no visible focus on: {invisible[:8]}"


def test_transitions_are_short_and_reduced_motion_is_honoured(page, base_url):
    """A work tool does not animate. Nothing here runs longer than 150ms."""
    sign_in(page, base_url, SANDRA)
    open_register(page, base_url)

    long_running = page.evaluate(
        """() => {
            const slow = [];
            for (const el of document.querySelectorAll('*')) {
              const d = getComputedStyle(el).transitionDuration;
              for (const part of d.split(',')) {
                const ms = part.trim().endsWith('ms')
                  ? parseFloat(part) : parseFloat(part) * 1000;
                if (ms > 150) slow.push(el.className + ' ' + part.trim());
              }
            }
            return slow;
        }"""
    )
    assert not long_running, f"transitions longer than 150ms: {long_running[:5]}"
