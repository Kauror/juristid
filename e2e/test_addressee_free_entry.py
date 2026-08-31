"""Adressaat, typed on the Teema form, in a real browser.

The rules themselves — reuse an exact or alias match, create a genuinely new
body, refuse an ambiguous spelling, and do all three inside the save's own
transaction — are pinned in `tests/test_addressee_free_entry.py` against the
database. What only a browser can answer is whether the workflow those rules
exist for is actually available on the page: whether somebody who cannot find
the institution in the list can name it and save, without leaving the half-filled
Teema to go and add it under Asutused first.

And one thing a screenshot cannot answer either. The narrow-window regression is
a *measurement*: when the paired row stops being a pair, the Adressaat field has
to take the width of the stacked row rather than the 18rem cap that belonged to
the compact checkbox beside it. So the assertion is on bounding boxes, not on
how the page looks.
"""

from __future__ import annotations

import pytest
from playwright.sync_api import expect

from e2e.conftest import MARTIN, sign_in

pytestmark = pytest.mark.e2e

CREATE_PATH = "/teemad/uus/"

#: The seeded ministry, and the seeded open Teema. Both mirror
#: `app/core/management/commands/seed_e2e_data.py`, kept as data because these
#: tests have no database access on purpose.
MINISTRY = "Näidisministeerium"
OPEN_TITLE = (
    "Tavaline avatud teema kõigile nähtav — pakendiseaduse ja sellega seonduvalt "
    "teiste seaduste muutmise seaduse eelnõu väljatöötamiskavatsus"
)

#: Named once so a rerun against a database an earlier run already touched
#: reuses the same institutions instead of inventing new ones.
TYPED = "Riigikogu näidiskomisjon"
REPLACEMENT = "Näidisameti õigusosakond"


def create_form(page, base_url) -> None:
    page.goto(f"{base_url}{CREATE_PATH}")
    page.wait_for_load_state("networkidle")


def file_teema(page, base_url, *, title: str, addressee: str) -> None:
    """Fill in `Uus teema` with a typed addressee and save it.

    The next step is filled in too, and that is not incidental. A Teema filed
    with no next action joins the department's «järgmise tegevuseta» population
    permanently, and `e2e/test_kpi_navigation.py` reads the first page of that
    list — twelve rows — expecting the seeded unassigned Teema to be on it. Two
    Matters from this file were enough to push it off. Every Teema the browser
    suite leaves behind is somebody else's fixture, so this one says what
    happens next, which is what a lawyer filing a real one does anyway.
    """
    create_form(page, base_url)
    page.fill("#id_title", title)
    page.fill("#id_addressee_name", addressee)
    page.fill("#id_next-text", "Kontrollida, kas adressaat vastas")
    # `Millal?` is required with the sentence now, and the quick span is how a
    # date is nearly always chosen (ADR 0052 addendum). The chip carries the day
    # the server resolved, so nothing here does date arithmetic.
    page.locator("#jargmine-tegevus").get_by_role("button", name="+1 nädal").click()
    page.get_by_role("button", name="Loo teema").click()
    page.wait_for_load_state("networkidle")


def open_edit(page, base_url) -> None:
    """From a Teema page, follow `Muuda` to the edit form."""
    page.get_by_role("link", name="Muuda", exact=False).first.click()
    page.wait_for_load_state("networkidle")


def checked_addressee(page) -> str:
    """The label of the currently selected Adressaat chip."""
    return page.locator('input[name="addressee_organisation"]:checked').evaluate(
        "node => node.closest('label').innerText.trim()"
    )


# ---------------------------------------------------------------------------
# A and C — naming an institution that is not in the catalogue, then replacing
# it with another one that is not either
# ---------------------------------------------------------------------------


def test_an_addressee_can_be_named_on_uus_teema_and_replaced_on_muuda_teemat(page, base_url):
    """The whole workflow on one Teema: type it, save, correct it, save.

    Not "leave Teema, create Organisation, return, find it again, save" — the
    workflow the removed helper sentence used to describe.

    One Teema for both halves rather than two, and deliberately: every Matter
    the browser suite leaves behind is a permanent row in somebody else's
    paginated list, and this file is not entitled to more of them than the thing
    it proves needs.

    The second half is what decides the precedence rule. `Muuda teemat`'s chip
    group always carries the addressee the Matter already has, so if the chip
    won, an addressee could never be replaced by typing.
    """
    sign_in(page, base_url, MARTIN)
    file_teema(page, base_url, title="Brauseris kirjutatud adressaat", addressee=TYPED)

    # The Teema that came back carries it, in the rail where the addressee lives.
    expect(page.locator(".railcard__value", has_text=TYPED).first).to_be_visible()

    # And reopening the form shows it as a *chosen organisation*, not as text
    # left in a box: it became a row in the catalogue, and the chip group is
    # what the catalogue is rendered as.
    open_edit(page, base_url)
    assert checked_addressee(page) == TYPED
    expect(page.locator("#id_addressee_name")).to_have_value("")

    # Now replace it with a body that is not in the catalogue either.
    page.fill("#id_addressee_name", REPLACEMENT)
    page.get_by_role("button", name="Salvesta").first.click()
    page.wait_for_load_state("networkidle")

    expect(page.locator(".railcard__value", has_text=REPLACEMENT).first).to_be_visible()

    open_edit(page, base_url)
    assert checked_addressee(page) == REPLACEMENT
    expect(page.locator("#id_addressee_name")).to_have_value("")


# ---------------------------------------------------------------------------
# B — naming one that is
# ---------------------------------------------------------------------------


def test_typing_a_name_that_already_exists_reuses_it(page, base_url):
    """A typed name that already names an institution *is* that institution.

    No database access here, so the duplicate check is made where a duplicate
    would show: the chip group renders one radio per organisation, so a second
    `Näidisministeerium` in the catalogue would be a second chip reading the
    same thing.
    """
    sign_in(page, base_url, MARTIN)
    file_teema(page, base_url, title="Olemasolev asutus brauserist", addressee=MINISTRY)

    expect(page.locator(".railcard__value", has_text=MINISTRY).first).to_be_visible()

    open_edit(page, base_url)
    assert checked_addressee(page) == MINISTRY
    named = page.locator('input[name="addressee_organisation"]').evaluate_all(
        "(nodes, name) => nodes.filter("
        "  node => node.closest('label').innerText.trim() === name).length",
        MINISTRY,
    )
    assert named == 1, f"the catalogue holds {named} institutions called {MINISTRY!r}"


def test_selecting_a_chip_clears_a_name_typed_beside_it(page, base_url):
    """Enhancement, and an honest page.

    The server resolves a typed name ahead of the selected chip, because on
    `Muuda teemat` the chip group always carries the addressee the Matter
    already has. Somebody who types a name and then picks an existing chip has
    plainly chosen the chip, so the box empties in front of them rather than
    quietly outranking what they just clicked.
    """
    sign_in(page, base_url, MARTIN)
    create_form(page, base_url)

    page.fill("#id_addressee_name", "Midagi pooleli kirjutatud")
    page.locator('input[name="addressee_organisation"]').nth(1).check()

    expect(page.locator("#id_addressee_name")).to_have_value("")


# ---------------------------------------------------------------------------
# D — the sentence that described the old workflow
# ---------------------------------------------------------------------------


def test_the_obsolete_addressee_sentence_is_gone_from_both_forms(page, base_url):
    """Removed rather than reworded — the control beside the chips says what can
    be done, and a paragraph explaining a field is a field that needed one.

    Read from the served markup rather than from `inner_text`, because the
    sentence used to live inside a closed disclosure and a rendered-text
    assertion would pass merely because nobody had opened it.

    The analogous Saatja sentence must stay — sender auto-creation is a separate
    decision nobody has taken — and `tests/test_addressee_free_entry.py` holds
    that assertion, where the catalogue size is controlled and the sender
    disclosure is guaranteed to render.
    """
    obsolete = "Kui adressaati siin ei ole"

    sign_in(page, base_url, MARTIN)

    # A Matter the seeded world already holds, rather than one more filed here.
    # This test needs an edit page, not a record, and every Teema the browser
    # suite leaves behind is one more row in somebody else's paginated list.
    page.goto(f"{base_url}/teemad/?olek=koik&q={OPEN_TITLE.split()[0]}")
    page.wait_for_load_state("networkidle")
    link = page.get_by_role("link", name=OPEN_TITLE, exact=False).first
    assert link.count(), "the register does not hold the seeded open Teema"
    page.goto(f"{base_url}{link.get_attribute('href')}")
    page.wait_for_load_state("networkidle")

    open_edit(page, base_url)
    assert obsolete not in page.content()

    create_form(page, base_url)
    assert obsolete not in page.content()


# ---------------------------------------------------------------------------
# E — the narrow-window regression, measured
# ---------------------------------------------------------------------------


def _box(page, selector: str) -> dict:
    box = page.locator(selector).first.bounding_box()
    assert box is not None, f"{selector} has no box"
    return box


#: The Adressaat fieldset, addressed through the one input only it contains.
#: Robust against the chips, the legend and the disclosure all moving, and it
#: cannot accidentally match the Andmeklass field beside it — which is the whole
#: subject of the measurements below.
ADDRESSEE_FIELD = "fieldset.field:has(#id_addressee_name)"


def _ensure_long_tail(page, base_url) -> None:
    """Make sure `Vali nimekirjast` is on the page before measuring it.

    The seeded world has few enough institutions that every one of them fits in
    the frequent shortlist and there is no long tail at all. One Teema with an
    addressee is enough to change that: the shortlist becomes "bodies this
    department has answered", and everything else moves into the disclosure.
    """
    create_form(page, base_url)
    assert page.locator(f"{ADDRESSEE_FIELD} details.chipdetails").count(), (
        "no long tail on the Adressaat control: the workflow test above files a "
        "Teema with an addressee, which is what moves the rest of the catalogue "
        "out of the frequent shortlist and into the disclosure"
    )


@pytest.mark.parametrize("width", [1024, 768, 420])
def test_the_addressee_field_takes_the_stacked_row_width(page, base_url, width):
    """The regression, as a measurement rather than as a screenshot.

    Under 1080px the paired rows stop being pairs, and the narrow breakpoint
    used to cap *every* field in the `--class` pair at 18rem — a rule that
    belonged to the compact Andmeklass checkbox and caught Adressaat because it
    sat in the same row. Stacked, that left a ~288px panel with the rest of the
    row empty to its right.

    A field is compact because of what it holds, never because of what it sits
    beside. So: the Adressaat field fills its stacked row.

    Adressaat no longer shares a row with anything — the checkbox moved up into
    the Vastutaja/Saatja row — so the row is addressed through the field it
    holds rather than by the pair modifier it used to carry. The measurement is
    unchanged, and it is the one that has to keep passing: whatever the row is
    called, a stacked Adressaat spends all of it.
    """
    sign_in(page, base_url, MARTIN)
    page.set_viewport_size({"width": width, "height": 900})
    create_form(page, base_url)

    field = _box(page, ADDRESSEE_FIELD)
    row = _box(page, f".createform__row:has({ADDRESSEE_FIELD})")

    assert field["width"] > 18 * 16 + 1, (
        f"Adressaat is still capped near 18rem at {width}px: {field['width']}px"
    )
    assert abs(field["width"] - row["width"]) <= 2, (
        f"Adressaat is {field['width']}px inside a {row['width']}px stacked row at {width}px"
    )


@pytest.mark.parametrize("width", [1024, 768, 420])
def test_the_open_long_tail_uses_the_addressee_width(page, base_url, width):
    """`Vali nimekirjast`, opened, on a stacked row.

    `.chipdetails` is `inline-block` so that a closed disclosure is a chip among
    chips, and shrink-to-fit is right there. Open and stacked it is wrong: the
    panel ends up a narrow island in a field with the whole row to spend, which
    is the second half of the supplied screenshot.
    """
    sign_in(page, base_url, MARTIN)
    page.set_viewport_size({"width": width, "height": 900})
    _ensure_long_tail(page, base_url)

    disclosure = f"{ADDRESSEE_FIELD} details.chipdetails"
    assert page.locator(disclosure).count(), "the long tail is not on this page"
    page.locator(f"{disclosure} > summary").click()

    field = _box(page, ADDRESSEE_FIELD)
    panel = _box(page, f"{disclosure} .chipdetails__body")

    assert panel["width"] >= field["width"] * 0.9, (
        f"the opened long tail is {panel['width']}px inside a {field['width']}px field at {width}px"
    )


@pytest.mark.parametrize("width", [1024, 768, 420])
def test_naming_an_institution_never_takes_the_page_sideways(page, base_url, width):
    sign_in(page, base_url, MARTIN)
    page.set_viewport_size({"width": width, "height": 900})
    _ensure_long_tail(page, base_url)
    page.locator(f"{ADDRESSEE_FIELD} details.chipdetails > summary").click()
    page.fill("#id_addressee_name", "Väga pika nimega näidisasutuse õigusosakond")

    overflows = page.evaluate(
        "() => document.documentElement.scrollWidth > document.documentElement.clientWidth + 1"
    )
    assert not overflows, f"the Adressaat control scrolls the page sideways at {width}px"
