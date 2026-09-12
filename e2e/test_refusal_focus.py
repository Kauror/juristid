"""A refused save takes the person to the thing that needs fixing.

The reported experience: `Uus teema`, `Järgmiseks` filled in with no date,
press `Loo teema`, and the page comes back looking exactly as it did. The save
had been refused correctly and said so — eleven blocks down, below the fold,
where nobody was looking. On a long form a correct refusal that nobody can see
is indistinguishable from a button that does nothing.

Only a browser can answer this. The server tests prove the refusal happens and
that the message is rendered beside its field; what is in doubt is whether the
person ends up looking at it, and that is a question about scroll position,
focus and a sticky header — none of which exist in a test client.

The rule, in one sentence: after a refusal, the first invalid control in reading
order is opened if it is hidden, scrolled to below the bar, and focused. And on
a save that succeeded, nothing moves at all (static/js/ux.js
`focusFirstRefusal`).
"""

from __future__ import annotations

import re

import pytest
from playwright.sync_api import expect

from e2e.conftest import MARTIN, SANDRA, sign_in

pytestmark = pytest.mark.e2e

#: `.topbar` is `position: sticky` and exactly this tall. The stylesheet clears
#: it with `scroll-margin-top`; this is the number that clearance has to beat.
TOPBAR_HEIGHT = 48


def create_form(page, base_url) -> None:
    page.goto(f"{base_url}/teemad/uus/")
    page.wait_for_load_state("networkidle")


def focused_id(page) -> str:
    return page.evaluate("() => document.activeElement && document.activeElement.id")


def box_of(locator) -> dict:
    box = locator.bounding_box()
    assert box is not None, "the control has no box at all — it is still hidden"
    return box


def assert_in_view(page, locator, what: str) -> None:
    """Inside the viewport, and not underneath the bar that floats over it."""
    box = box_of(locator)
    height = page.viewport_size["height"]
    assert box["y"] >= TOPBAR_HEIGHT, (
        f"{what} sits at y={box['y']}, underneath the {TOPBAR_HEIGHT}px sticky bar"
    )
    assert box["y"] + box["height"] <= height, (
        f"{what} sits at y={box['y']} with a {height}px viewport — still below the fold"
    )


def test_a_refusal_below_the_fold_brings_the_person_to_it(page, base_url):
    """**A.** The real case: `Järgmiseks` with no date, near the bottom.

    Three separate claims, and the defect satisfied none of them: the control
    that is wrong has the cursor, it is inside the viewport, and it is not
    hidden behind the sticky bar.
    """
    sign_in(page, base_url, MARTIN)
    create_form(page, base_url)

    page.fill("#id_title", "Kuupäevata samm, mis tuleb ise üles leida")
    page.fill("#id_next-text", "Vaadata uus eelnõu versioon üle")
    # Proving the field really is out of sight to start with: this is the whole
    # premise, and a shorter page would make the test vacuous.
    page.evaluate("() => window.scrollTo(0, 0)")
    assert page.locator("#id_next-target_date").bounding_box()["y"] > page.viewport_size["height"]

    page.get_by_role("button", name="Loo teema").click()
    page.wait_for_load_state("networkidle")

    expect(page.locator("#jargmine-tegevus")).to_contain_text("Vali järgmise tegevuse kuupäev.")
    assert focused_id(page) == "id_next-target_date", focused_id(page)
    assert_in_view(page, page.locator("#id_next-target_date"), "the refused date box")


def test_the_disclosure_holding_the_refused_control_is_opened(page, base_url):
    """**B.** A control inside a closed `<details>` has no box and no focus.

    «Kuupäev…» is a real disclosure, so the exact date is reachable with
    scripting off. Whatever closed it, a refusal about the box inside it has to
    open it — otherwise the page scrolls to a summary and the explanation is
    still hidden.
    """
    sign_in(page, base_url, MARTIN)
    create_form(page, base_url)

    page.fill("#id_title", "Suletud paneeli taga peituv viga")
    page.fill("#id_next-text", "Vaadata uus eelnõu versioon üle")
    page.get_by_role("button", name="Loo teema").click()
    page.wait_for_load_state("networkidle")

    disclosure = page.locator("#jargmine-tegevus details.uxcomp__date")
    assert disclosure.evaluate("node => node.open"), "the date disclosure came back closed"
    expect(page.locator("#id_next-target_date")).to_be_visible()
    assert focused_id(page) == "id_next-target_date"


def test_the_cursor_goes_to_the_control_that_is_wrong_not_the_first_one(page, base_url):
    """The previous behaviour, which was almost right and therefore worse.

    Focusing the form's *first* control scrolled somebody to the top of a long
    form to look at a box that was perfectly fine, while the refusal stayed
    where it was. On this page the first control is `Pealkiri`, and `Pealkiri`
    is correct.
    """
    sign_in(page, base_url, MARTIN)
    create_form(page, base_url)

    page.fill("#id_title", "Pealkiri on korras")
    page.fill("#id_next-text", "Vaadata uus eelnõu versioon üle")
    page.get_by_role("button", name="Loo teema").click()
    page.wait_for_load_state("networkidle")

    assert focused_id(page) != "id_title"
    expect(page.locator("#id_title")).to_have_value("Pealkiri on korras")


def test_a_save_that_worked_does_not_take_the_cursor(page, base_url):
    """**D.** Nothing moves on a page that has nothing to correct.

    This is the half that makes the rule safe to bind on every load: a focus
    hijack on an ordinary arrival is worse than the defect being fixed, because
    it happens on every page instead of after a mistake.
    """
    sign_in(page, base_url, MARTIN)
    create_form(page, base_url)

    page.fill("#id_title", "Teema, mis salvestub esimese korraga")
    page.get_by_role("button", name="Loo teema").click()
    page.wait_for_url(re.compile(r"/teemad/[0-9a-f-]{36}/$"))

    # `<body>` is where a fresh document leaves the cursor.
    assert page.evaluate("() => document.activeElement === document.body")
    assert page.locator(".field__error").count() == 0


def test_an_ordinary_page_load_keeps_the_focus_it_always_had(page, base_url):
    """The same claim from the other direction: arriving is not correcting.

    `Uus teema` puts the cursor in `Pealkiri` on arrival, and that is its own
    behaviour and none of this rule's business. What is asserted is that the
    refusal rule leaves it exactly there — a page with nothing wrong on it has
    nothing for this to do, and a rule bound on every load that moved the cursor
    anyway would be a worse defect than the one being fixed, because it would
    happen every time instead of after a mistake.
    """
    sign_in(page, base_url, MARTIN)
    create_form(page, base_url)

    assert page.locator(".field__error, .formerror").count() == 0
    assert focused_id(page) == "id_title", focused_id(page)


def test_a_form_level_refusal_focuses_the_message_and_not_a_guessed_field(page, gate_base_url):
    """**C.** A refusal that names no field must not choose one.

    The shared gate's wrong password is the clean case: one `role="alert"`
    paragraph, one password box, and nothing connecting them. Guessing the box
    would put the cursor somewhere the message is not about; the message takes
    it instead, which is why the script makes it focusable at the moment it
    needs to be.
    """
    page.goto(f"{gate_base_url}/konto/varav/")
    page.wait_for_load_state("networkidle")
    page.fill('input[name="password"]', "vale-parool-mis-kindlasti-ei-sobi")
    page.get_by_role("button").first.click()
    page.wait_for_load_state("networkidle")

    summary = page.locator(".formerror")
    expect(summary).to_be_visible()
    assert page.locator(".field__error").count() == 0, "this case is only about form-level errors"
    assert page.evaluate(
        "() => document.activeElement && document.activeElement.classList.contains('formerror')"
    ), "the error summary did not take the cursor"
    # Focusable to script, and still out of the tab order: a refusal should not
    # add a stop between two fields for everybody who keyboards past it.
    assert summary.get_attribute("tabindex") == "-1"


@pytest.mark.parametrize("width", [420, 1024, 1440])
def test_the_refused_control_clears_the_bar_at_every_width(page, base_url, width):
    """The clearance is `scroll-margin-top`, so it is one number in the
    stylesheet rather than a viewport coordinate computed in a script — and it
    has to hold where the layout reflows, not only where it was written."""
    sign_in(page, base_url, SANDRA)
    page.set_viewport_size({"width": width, "height": 900})
    create_form(page, base_url)

    page.fill("#id_title", f"Vigane vorm laiusel {width}")
    page.fill("#id_next-text", "Vaadata uus eelnõu versioon üle")
    page.get_by_role("button", name="Loo teema").click()
    page.wait_for_load_state("networkidle")

    assert focused_id(page) == "id_next-target_date", focused_id(page)
    assert_in_view(page, page.locator("#id_next-target_date"), f"the refused box at {width}px")
