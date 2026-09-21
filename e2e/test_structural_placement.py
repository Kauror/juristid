"""Where things are on the Teema page, asserted structurally rather than visually.

The one-rail round exposed a real gap in the browser lane. Four of its five
changes were *moves* — a control out of a section and into a header, a chip back
into a row, a checkbox into a picker's footer — and a move of that size can sit
under the visual suite's tolerance while changing what the page means. The
screenshots stayed green through changes the owner had asked for and through
changes nobody had.

So this file makes those placements assertions instead of pictures. The split is
deliberate and neither half replaces the other:

* **here**: which region a control is in, what it is a sibling of, what order
  things are drawn in, which words are absent from which section;
* **`e2e/test_ui_regression.py`**: whether the rendered result drifted.

**No guessed geometry.** There is not a pixel or an offset anywhere below, and
there should not be: «`Kustuta` is in the header» is a fact about the document,
and writing it as «within 32px of `Muuda`» would be a test that fails when
somebody changes a gap and passes when somebody moves the control into the
wrong section at the same spacing. The only measurement this file makes is the
one that is genuinely geometric — that nothing overflows sideways.

Everything happens on Matters these tests create, for the reason
`e2e/test_substantive_history.py` gives: the screenshot suite opens its own
titles, and a history that grew while these ran would make a baseline depend on
test order.
"""

from __future__ import annotations

import re
from datetime import date, timedelta

import pytest
from playwright.sync_api import expect

from e2e.conftest import (
    MARTIN,
    open_add_panel,
    open_hetkeseis,
    sign_in,
    unique_title,
)

pytestmark = pytest.mark.e2e


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _estonian(on: date) -> str:
    return f"{on.day}.{on.month}.{on.year}"


def _matter(
    page,
    base_url: str,
    *,
    instrument: str | None = None,
    stage: str | None = None,
    deadline_in: int | None = 7,
) -> str:
    """One Matter through the real form, carrying whatever the test needs.

    `create_matter` in `e2e/conftest.py` answers title, stage, owner and sender;
    this file also needs an `Õigusakt` — without one the rail has no procedure
    to read the file against — and a `Arvamuse tähtaeg`, which is the dated
    point half these tests are about. Rather than widen the shared helper with
    two more keyword arguments used nowhere else, this one files the form.

    **`deadline_in` defaults to a week rather than to nothing**, for the reason
    `e2e/conftest.py` `give_first_step` gives: every Teema this suite leaves
    behind with no open step is a permanent row in the department's «järgmise
    tegevuseta» list, which files running after this one read. The one date the
    form asks for establishes the canonical step, so a file that creates
    Matters pays for each of them here.
    """
    page.goto(f"{base_url}/teemad/uus/")
    page.wait_for_load_state("networkidle")
    page.fill("#id_title", unique_title("Paigutus"))
    if instrument is not None:
        page.get_by_role("checkbox", name=instrument, exact=True).check()
    if stage is not None:
        open_hetkeseis(page)
        page.get_by_role("radio", name=stage, exact=True).check()
    if deadline_in is not None:
        page.fill("#id_response_deadline", _estonian(date.today() + timedelta(days=deadline_in)))
    page.get_by_role("button", name="Loo teema").click()
    page.wait_for_url(re.compile(r"/teemad/[0-9a-f-]{36}/$"))
    return page.url


def _consulting(page, base_url: str, *, deadline_in: int | None = None) -> str:
    """A domestic bill out for consultation. The rail's ordinary shape."""
    return _matter(
        page,
        base_url,
        instrument="Seadus",
        stage="Kooskõlastusringil",
        deadline_in=deadline_in,
    )


def _rail_labels(page) -> list[str]:
    """Every column of `Menetluse kulg`, in the order the document draws them."""
    return [text.strip() for text in page.locator(".tl-step__what").all_inner_texts()]


def _open_kulg_editor(page):
    """`Muuda` beside `Menetluse kulg`, and the panel it swaps in."""
    page.locator(".lprail__edit").click()
    form = page.locator("form.kulgform")
    form.wait_for()
    return form


def _date_a_phase(page, phase_key: str, days: int) -> str:
    """Put an expected date on one future phase, through the real panel.

    **Waits for the day to appear on the rail, not for the network.**
    `networkidle` returns while the swap of `#teema-vaade` is still being
    applied, so a read taken straight afterwards comes back one save behind —
    forever, and silently, because the previous rail is a perfectly valid page.
    That is precisely how the first version of the chronological-anchor test
    below passed against the date it had just replaced (e2e/conftest.py
    `open_add_panel` carries the same reasoning).
    """
    when = _estonian(date.today() + timedelta(days=days))
    form = _open_kulg_editor(page)
    form.locator(f"[name={phase_key}__date]").fill(when)
    form.get_by_role("button", name="Salvesta", exact=True).click()
    page.wait_for_load_state("networkidle")
    expect(page.locator(".tl-strip")).to_contain_text(when)
    expect(page.locator("form.kulgform")).to_have_count(0)
    return when


# ---------------------------------------------------------------------------
# 1 — the header holds both actions on the record
# ---------------------------------------------------------------------------


def test_muuda_and_kustuta_are_in_the_same_header_action_region(page, base_url: str):
    """Both answer «this record is wrong», so they are one errand in one place.

    Asserted as *same parent*, which is the claim. A distance would pass for a
    `Kustuta` moved back into a section of its own that happened to render
    nearby, and fail for a gap somebody widened on purpose.
    """
    sign_in(page, base_url, MARTIN)
    _matter(page, base_url)

    crumbs = page.locator("#teema-pais .matterhead__crumbs")
    expect(crumbs.locator(".matterhead__edit")).to_have_count(1)
    expect(crumbs.locator(".matterhead__delete")).to_have_count(1)
    expect(crumbs.locator(".matterhead__edit")).to_have_text("Muuda teemat")
    expect(crumbs.locator(".matterhead__delete")).to_have_text("Kustuta")
    # Still a link to a confirmation page, never an inline button: a deletion
    # has to be read before it is agreed to (docs/adr/0096 §4).
    expect(crumbs.locator("a.matterhead__delete")).to_have_count(1)


def test_kustuta_is_not_rendered_anywhere_below_the_page(page, base_url: str):
    """The section it used to live in is gone, not merely hidden.

    Scoped past the header, because `Kustuta` *should* appear there — and a
    page-wide «the word is absent» assertion would be a test that can only
    fail.
    """
    sign_in(page, base_url, MARTIN)
    _matter(page, base_url)

    below = page.locator("#teema-vaade")
    expect(below.get_by_role("link", name="Kustuta teema")).to_have_count(0)
    expect(page.locator("#teema-toimingud")).to_have_count(0)
    expect(page.locator("#teema-vaade").get_by_text("Teema toimingud")).to_have_count(0)


# ---------------------------------------------------------------------------
# 2 — `Lõpeta teema` is a peer of the capture chips
# ---------------------------------------------------------------------------


def test_lopeta_is_in_the_same_launcher_region_as_the_capture_chips(page, base_url: str):
    """One row of choices, and closure visibly last inside it.

    Same region and the *same radio group* — which is the part a screenshot
    cannot show. A second group would leave two panels standing open at once,
    and the row's whole contract is that it is a choice until one is picked.
    """
    sign_in(page, base_url, MARTIN)
    _matter(page, base_url)

    row = page.locator("#lisa-teemale .cx-panels").first
    for chip in ("+ Märge", "+ Kaasamine", "+ Arvamus / tagasiside", "+ Ülevaade / uudis"):
        expect(
            row.locator("label.disclosure-chip", has_text=re.compile(rf"^{re.escape(chip)}$"))
        ).to_have_count(1)
    lopeta = row.locator("label.disclosure-chip--last")
    expect(lopeta).to_have_count(1)
    expect(lopeta).to_have_text("+ Lõpeta teema")

    # The same exclusive group as its four neighbours.
    #
    # **Direct children of the outer row only.** `Märke liik` and
    # `Kelle arvamus` are `.cx-panels` too — the same construction one level in
    # — so an unscoped selector collects their radios as well and reports three
    # groups on a correct page.
    groups = page.locator("#lisa-teemale > .cx-panels > input.addpick").evaluate_all(
        "nodes => [...new Set(nodes.map(n => n.name))]"
    )
    assert groups == ["lisa-valik"], groups
    expect(page.locator("#teema-lopeta-valik")).to_have_attribute("name", "lisa-valik")


# ---------------------------------------------------------------------------
# 3 — `Liige` belongs to the source block
# ---------------------------------------------------------------------------


def test_liige_sits_with_the_organisation_picker_and_not_in_a_row_of_its_own(page, base_url: str):
    """A second fact about the *same* answer — who this came from.

    It had a full-width chip row between the picker and the date, so one
    checkbox took the width of the panel and read as a third question. The
    contract is structural: same parent as the picker, after it, before
    `Kuupäev`, and not one of the panel's standalone `.chiprow` questions.
    """
    sign_in(page, base_url, MARTIN)
    _matter(page, base_url)
    open_add_panel(page, "arvamus-tagasiside")

    panel = page.locator("#arvamus-tagasiside")
    mark = panel.locator(".orgpick__mark")
    expect(mark).to_have_count(1)
    expect(mark.locator("[name=source_is_member]")).to_have_count(1)
    # Not a standalone question of its own …
    expect(panel.locator(".chiprow.orgpick__mark")).to_have_count(0)
    # … and structurally part of the source block: the picker's own sibling,
    # drawn after it and before the date this answer carries.
    order = panel.evaluate(
        """node => {
            const picker = node.querySelector('#tagasiside-valik');
            const mark = node.querySelector('.orgpick__mark');
            const dated = node.querySelector('[name=stated_on]');
            return {
                sameParent: picker.parentElement === mark.parentElement,
                afterPicker: !!(picker.compareDocumentPosition(mark)
                    & Node.DOCUMENT_POSITION_FOLLOWING),
                beforeDate: !!(mark.compareDocumentPosition(dated)
                    & Node.DOCUMENT_POSITION_FOLLOWING),
            };
        }"""
    )
    assert order == {"sameParent": True, "afterPicker": True, "beforeDate": True}, order


def test_the_member_mark_is_inside_the_form_that_posts_and_still_saves(page, base_url: str):
    """A move, not a rebuild. Same control, same field, same save.

    The reason this is here and not only in the Python suite: the control was
    **re-parented**, and a checkbox that ends up outside the `<form>` it belongs
    to posts nothing at all while looking exactly right — no refusal, no
    console error, and a record that saves with the box silently unread. That
    the value persists is settled cheaply by
    `tests/test_teema_composer_simplification.py`; that the control is still
    *in* the form, and that a save with it ticked goes through, is what only a
    real submission can show.
    """
    sign_in(page, base_url, MARTIN)
    _matter(page, base_url)
    open_add_panel(page, "arvamus-tagasiside")

    panel = page.locator("#arvamus-tagasiside")
    posting_form = panel.locator("form")
    expect(posting_form.locator(".orgpick__mark [name=source_is_member]")).to_have_count(1)

    box = panel.locator("#tagasiside-otsi")
    box.click()
    box.fill("")
    box.type("Näidismi", delay=20)
    page.locator("#tagasiside-tulemused").get_by_role(
        "option", name="Näidisministeerium", exact=True
    ).click()
    panel.locator(".orgpick__mark input[type=checkbox]").check()
    panel.locator("[name=summary]").fill("Liikmesettevõtte vastus.")
    panel.locator("[name=stated_on]").fill(_estonian(date.today() - timedelta(days=1)))
    panel.get_by_role("button", name="Salvesta tagasiside", exact=True).click()
    page.wait_for_load_state("networkidle")

    # The save went through with the box ticked, and the record is on the file.
    history = page.locator("#ajalugu-loend")
    expect(history.get_by_text("Meile saadetud tagasiside:").first).to_be_visible()
    expect(history).to_contain_text("Näidisministeerium")


# ---------------------------------------------------------------------------
# 4 + 5 — one rail, and the explanatory copy is gone from it
# ---------------------------------------------------------------------------


#: Copy the one-rail round removed from `Menetluse kulg`. Every one of these was
#: a label explaining a picture a lawyer reads in a second.
RETIRED_RAIL_COPY = [
    "Praegu",
    "Kirjas",
    "Teadmata",
    "Võimalik",
    "Kogu võimalik teekond",
    "Ees võib olla",
    "Kirjas olevad kuupäevad",
    "Menetluse tähtajad",
]


@pytest.mark.parametrize("gone", RETIRED_RAIL_COPY)
def test_the_retired_copy_is_absent_from_the_process_region(page, base_url: str, gone: str):
    """**Scoped to the rail, on purpose.**

    `Praegu` is a legitimate word elsewhere on this page — the chronology marks
    the phase the file is standing on with it — so a page-wide assertion would
    be testing the wrong thing and would fail on a correct page. What the round
    removed is these words *from the process region*.
    """
    sign_in(page, base_url, MARTIN)
    _consulting(page, base_url, deadline_in=30)

    expect(page.locator(".lprail")).to_have_count(1)
    expect(page.locator(".lprail")).not_to_contain_text(gone)


def test_nothing_is_left_to_colour_alone(page, base_url: str):
    """The line removing those words must not cross.

    A rail whose four states became four hues would say nothing with the
    stylesheet off, nothing to a screen reader and nothing on a printout. What
    the words said is still in the document (docs/adr/0074 §12.2).
    """
    sign_in(page, base_url, MARTIN)
    _consulting(page, base_url, deadline_in=30)

    rail = page.locator(".lprail")
    expect(rail.locator('[aria-current="step"]')).to_have_count(1)
    assert rail.locator(".visually-hidden", has_text="Tulevikus").count() >= 1


def test_there_is_one_rail_and_the_second_date_strip_is_gone(page, base_url: str):
    """One `.tl-strip`, and the obsolete component absent as a component.

    Counting strips is what a screenshot cannot do: two rails one above the
    other, both plausible, is exactly the rendering that reads as «detailed».
    """
    sign_in(page, base_url, MARTIN)
    _consulting(page, base_url, deadline_in=30)

    expect(page.locator(".tl-strip")).to_have_count(1)
    expect(page.locator('[aria-label="Menetluse kulg"]')).to_have_count(1)
    # The phase rail's own node markup went with the merge …
    expect(page.locator(".lprail__nodes")).to_have_count(0)
    expect(page.locator(".lprail__node")).to_have_count(0)
    # … and the strip carries both kinds on the one row.
    assert page.locator(".tl-step--phase").count() >= 1
    assert page.locator(".tl-step--milestone").count() >= 1


# ---------------------------------------------------------------------------
# 6 — the file action says what it does
# ---------------------------------------------------------------------------


def test_the_chronology_row_offers_lisa_fail_and_never_lisa_toend(page, base_url: str):
    """The owner's word. Somebody attaching a paper is adding a file.

    «Tõend» is what the application does with it afterwards — an immutable,
    hashed, verifiable object — and that concept keeps its name everywhere it
    belongs. It is simply not what the person pressing the button has in mind.
    """
    sign_in(page, base_url, MARTIN)
    _matter(page, base_url)
    headline = "Ministeerium saatis eelnõu"

    open_add_panel(page, "marge-tavaline")
    form = page.locator("#marge-tavaline")
    form.locator("[name=title]").fill(headline)
    form.locator("[name=occurred_on]").fill(_estonian(date.today() - timedelta(days=2)))
    form.get_by_role("button", name="Salvesta", exact=True).click()
    page.wait_for_load_state("networkidle")
    page.get_by_text(headline).first.wait_for()

    row = page.locator("article.uxtl__item").filter(has_text=headline)
    action = row.locator("button.uxtl__edit[id$='-toend']")
    expect(action).to_have_text("+ Lisa fail")
    expect(row).not_to_contain_text("Lisa tõend")

    # And the form it opens speaks the same language.
    action.click()
    opened = page.locator("form[aria-label='Faili lisamine märkele']")
    opened.wait_for()
    expect(opened.get_by_role("button", name="Lisa fail", exact=True)).to_have_count(1)
    expect(opened.get_by_role("button", name="Lisa tõend", exact=True)).to_have_count(0)


# ---------------------------------------------------------------------------
# 8 — where a future deadline lands on the rail
# ---------------------------------------------------------------------------


def test_an_unanchored_deadline_is_drawn_beside_the_current_phase(page, base_url: str):
    """The round's product change, read off the document order.

    A deadline three weeks out used to be drawn after `Valitsuses`,
    `Riigikogus` and `Jõustumine` on a bill still out for consultation, which
    told a lawyer their own answer falls due after the act comes into force.
    """
    sign_in(page, base_url, MARTIN)
    _consulting(page, base_url, deadline_in=30)

    labels = _rail_labels(page)
    current = labels.index("Kooskõlastusring")

    assert labels[current + 1] == "Arvamuse tähtaeg", labels
    assert labels[current + 2 :] == ["Valitsuses", "Riigikogus", "Jõustumine"], labels


def test_a_dated_future_phase_anchors_the_deadline_chronologically(page, base_url: str):
    """An undated phase constrains nothing; a dated one is a real anchor.

    Both directions, on one file, through the panel a lawyer actually uses —
    because the whole point of the rule is that it follows what somebody
    recorded rather than the pattern's shape.
    """
    sign_in(page, base_url, MARTIN)
    _consulting(page, base_url, deadline_in=30)

    # `Valitsuses` expected *after* the deadline: the deadline still reads first.
    _date_a_phase(page, "valitsus", days=45)
    labels = _rail_labels(page)
    assert labels.index("Arvamuse tähtaeg") < labels.index("Valitsuses"), labels

    # Moved to *before* it, and the rail follows.
    _date_a_phase(page, "valitsus", days=15)
    labels = _rail_labels(page)
    assert labels.index("Valitsuses") < labels.index("Arvamuse tähtaeg"), labels
    assert labels.index("Arvamuse tähtaeg") < labels.index("Riigikogus"), labels


@pytest.mark.parametrize("width", [375, 420, 768, 1440])
def test_the_rail_with_a_deadline_beside_the_phase_does_not_overflow(
    page, base_url: str, width: int
):
    """The one genuinely geometric contract in this file.

    A column inserted into the middle of the rail widens the row it is in, and
    `.tl-strip` scrolls itself rather than the document — so the assertion is
    about the *page*, not about the strip.
    """
    page.set_viewport_size({"width": width, "height": 900})
    sign_in(page, base_url, MARTIN)
    _consulting(page, base_url, deadline_in=30)

    expect(page.locator(".tl-strip")).to_have_count(1)
    overflows = page.evaluate(
        "() => document.documentElement.scrollWidth > document.documentElement.clientWidth + 1"
    )
    assert not overflows, f"the document scrolls sideways at {width}px"

    # **The strip absorbs it, and that is the contract rather than «it fits».**
    # A rail of seven columns does not fit a phone and is not meant to: it
    # scrolls itself, which is why the document does not. Asserted as the
    # relationship between the two — the strip is a scroll container, and at a
    # width where it has more columns than room it really is scrolled — and not
    # as a pixel count, because how many columns fit is a font and a locale
    # (docs/adr/0074 §12.2).
    strip = page.evaluate(
        """() => {
            const el = document.querySelector('.tl-strip');
            return {overflowX: getComputedStyle(el).overflowX,
                    scrollable: el.scrollWidth > el.clientWidth + 1};
        }"""
    )
    # Below the stylesheet's own 720px breakpoint the strip is a scroll
    # container; above it the row has room and does not need to be one. The
    # breakpoint is read from the rule rather than guessed, and the widths
    # either side of it are what this parametrisation is for
    # (static/css/app.css, `@media (max-width: 720px)`).
    if width <= 720:
        assert strip["overflowX"] in ("auto", "scroll"), strip
        assert strip["scrollable"], f"the rail should scroll itself at {width}px, not clip"


@pytest.mark.parametrize("width", [375, 420])
def test_the_chronology_row_with_lisa_fail_does_not_overflow(page, base_url: str, width: int):
    """The other half of §E's narrow-width question, on the row that changed.

    `Lisa fail` is shorter than the words it replaces, so the risk is not that
    it grew — it is that a row carrying two actions beside a headline is the
    narrowest thing on the page. Measured as document overflow, which is a real
    contract, rather than as a wrap count, which is a font.
    """
    page.set_viewport_size({"width": width, "height": 900})
    sign_in(page, base_url, MARTIN)
    _matter(page, base_url)
    headline = "Ministeerium saatis eelnõu"

    open_add_panel(page, "marge-tavaline")
    form = page.locator("#marge-tavaline")
    form.locator("[name=title]").fill(headline)
    form.locator("[name=occurred_on]").fill(_estonian(date.today() - timedelta(days=2)))
    form.get_by_role("button", name="Salvesta", exact=True).click()
    page.wait_for_load_state("networkidle")
    page.get_by_text(headline).first.wait_for()

    row = page.locator("article.uxtl__item").filter(has_text=headline)
    expect(row.locator("button.uxtl__edit[id$='-toend']")).to_have_text("+ Lisa fail")
    overflows = page.evaluate(
        "() => document.documentElement.scrollWidth > document.documentElement.clientWidth + 1"
    )
    assert not overflows, f"the chronology row scrolls the document sideways at {width}px"
