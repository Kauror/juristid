"""Moving through a select is not choosing from it (ENG-035).

On Chromium/Windows a *closed* select fires `change` for every ArrowDown,
ArrowUp and type-ahead letter. The header's `Vastutaja` and `Hetkeseis` used to
commit on `change`, so one arrow key reassigned the Teema — a `MATTER_ASSIGNED`
event, a notice in a colleague's Minu asjad, the open step moved to them — or
recorded a stage the file had not reached. The Dokumendid filters reloaded the
page at every keystroke.

Now a business write commits on its own `Salvesta` only, and a read-only filter
commits a pointer choice at once but a keyboard walk only on Enter or on
leaving the control.

**Platform-independent on purpose.** Whether a key press on a closed select
changes its value depends on the platform the browser believes it is on. Each
walk below presses the real key, and where this runner's Chromium did not move
the selection itself, it moves it the way Chromium/Windows does — one option,
then `change` — so the assertion is about what the page does with a keyboard
`change`, whichever platform the suite runs on.
"""

from __future__ import annotations

import re

import pytest
from playwright.sync_api import expect

from e2e.conftest import MARTIN, create_matter, sign_in
from e2e.titles import unique_title

pytestmark = pytest.mark.e2e

#: What the page is allowed to do between a key press and the next assertion.
SETTLE_MS = 800


def _posts_to(page, fragment: str) -> list[str]:
    seen: list[str] = []
    page.on(
        "request",
        lambda request: (
            seen.append(request.url)
            if request.method == "POST" and fragment in request.url
            else None
        ),
    )
    return seen


def _walk(select, key: str) -> None:
    """One keyboard step through a closed select, as Chromium/Windows takes it."""
    before = select.input_value()
    select.press(key)
    if select.input_value() == before:
        select.evaluate(
            """(el) => {
                el.selectedIndex = (el.selectedIndex + 1) % el.options.length;
                el.dispatchEvent(new Event("input", { bubbles: true }));
                el.dispatchEvent(new Event("change", { bubbles: true }));
            }"""
        )


def _header_select(page, label: str):
    control = page.locator(f'#teema-pais details:has(select[aria-label="{label}"])')
    if not control.evaluate("el => el.open"):
        control.locator(".inlineedit__trigger").click()
    select = control.locator(f'select[aria-label="{label}"]')
    expect(select).to_be_visible()
    select.focus()
    return control, select


@pytest.mark.parametrize(
    ("label", "field", "save"),
    [
        ("Vastutaja", "owner", "Salvesta vastutaja muudatus"),
        ("Hetkeseis", "stage", "Salvesta hetkeseisu muudatus"),
    ],
)
def test_a_keyboard_walk_through_a_business_select_writes_nothing(
    page, base_url, label, field, save
):
    sign_in(page, base_url, MARTIN)
    create_matter(page, base_url, unique_title("Klaviatuuriga valik"), owner=MARTIN)
    posts = _posts_to(page, f"/vali/{field}/")

    control, select = _header_select(page, label)
    rendered = select.input_value()
    for key in ("ArrowDown", "ArrowDown", "ArrowUp", "s"):
        _walk(select, key)
        page.wait_for_timeout(SETTLE_MS)
        assert posts == [], f"{key} on {label} posted {posts}"

    # The trigger still says what the record says: nothing was committed.
    chosen = select.input_value()
    assert chosen != rendered, "the walk never moved the selection; the test proved nothing"
    expect(control).to_have_js_property("open", True)

    control.get_by_role("button", name=save).click()
    page.wait_for_load_state("networkidle")
    assert len(posts) == 1, posts


def test_a_pointer_choice_on_a_business_select_still_needs_salvesta(page, base_url):
    """A mouse choice is not a decision on its own either: one rule for everybody."""
    sign_in(page, base_url, MARTIN)
    create_matter(page, base_url, unique_title("Hiirega valik"), owner=MARTIN)
    posts = _posts_to(page, "/vali/owner/")

    control, select = _header_select(page, "Vastutaja")
    select.select_option(index=0)
    page.wait_for_timeout(SETTLE_MS)
    assert posts == []

    control.get_by_role("button", name="Salvesta vastutaja muudatus").click()
    page.wait_for_load_state("networkidle")
    assert len(posts) == 1
    expect(page.locator("#teema-pais")).to_contain_text("Määramata")


def test_a_document_filter_waits_for_enter_but_not_for_a_pointer(page, base_url):
    sign_in(page, base_url, MARTIN)
    url = create_matter(page, base_url, unique_title("Dokumentide filter"))
    page.goto(f"{url}dokumendid/")
    page.wait_for_load_state("networkidle")
    # `Aasta` lists the years documents carry, and `Roll — kõik` — the filter
    # this used to walk — left the toolbar on 2026-10-07: one file gives the
    # year filter a value to walk to.
    page.evaluate("document.getElementById('lae-dokument').hidden = false")
    upload = page.locator("#lae-dokument form")
    upload.locator("input[name=upload]").set_input_files(
        {"name": "aasta.pdf", "mimeType": "application/pdf", "buffer": b"%PDF-1.4\naasta"}
    )
    with page.expect_navigation():
        upload.get_by_role("button", name="Salvesta dokument").click()
    page.goto(f"{url}dokumendid/")
    page.wait_for_load_state("networkidle")
    navigations: list[str] = []
    page.on("framenavigated", lambda frame: navigations.append(frame.url))

    select = page.locator("select[name=aasta]")
    select.focus()
    _walk(select, "ArrowDown")
    page.wait_for_timeout(SETTLE_MS)
    assert navigations == [], navigations
    chosen = select.input_value()
    assert chosen, "the walk never moved the selection"

    with page.expect_navigation():
        select.press("Enter")
    assert re.search(rf"[?&]aasta={re.escape(chosen)}(&|$)", page.url), page.url

    # A pointer choice commits at once, exactly as it always did.
    other = page.locator("select[name=aasta]")
    values = other.locator("option").evaluate_all("options => options.map(o => o.value)")
    # Any value but the one now chosen: re-picking it would change nothing.
    others = [value for value in values if value != chosen]
    with page.expect_navigation():
        other.select_option(others[0])
    assert f"aasta={others[0]}" in page.url
