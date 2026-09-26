"""A `Märge` whose only answer is the stage the file already has is refused (ENG-060).

`tests/test_an_empty_marge_is_not_stored.py` proves nothing is stored, on the
service, the panel's route and under a concurrent stage change. This proves what
only a browser can: that the person who pressed `Salvesta` reads why, inside the
panel they pressed it in, with the cursor on the sentence rather than somewhere
else on a long page — at a phone's width as well as a desktop's — and that
`Teema käik` gained no row.

A real stage change first, so the second save is genuinely «the current stage,
and nothing else»: the file is created with none, and the first `Märge` moves it.
"""

from __future__ import annotations

import pytest
from playwright.sync_api import expect

from e2e.conftest import MARTIN, create_matter, open_add_panel, sign_in, wait_for_htmx
from e2e.titles import unique_title

pytestmark = pytest.mark.e2e

#: `app.matters.services.DEVELOPMENT_NEEDS_SOMETHING`, written out: this module
#: runs against a server, not against the application's import path.
REFUSAL = "Kirjuta, mis juhtus, või lisa fail, uus hetkeseis või järgmine tegevus."


def _save_stage_only(page, value: str) -> None:
    open_add_panel(page, "marge-tavaline")
    page.locator("#marge-tavaline select[name=stage]").select_option(value=value)
    page.locator("#marge-tavaline button[type=submit]").click()
    wait_for_htmx(page)


@pytest.mark.parametrize("width", [375, 1440])
def test_the_current_stage_alone_is_refused_in_the_panel_it_was_chosen_in(page, base_url, width):
    page.set_viewport_size({"width": width, "height": 900})
    sign_in(page, base_url, MARTIN)
    create_matter(page, base_url, unique_title("Tühi märge"))

    # A real change: the file moves, and that alone is a whole save.
    open_add_panel(page, "marge-tavaline")
    option = page.locator("#marge-tavaline select[name=stage] option:not([value=''])").first
    value = option.get_attribute("value") or ""
    label = (option.text_content() or "").strip()
    assert value, "the Märge panel offers no stage to choose"
    page.locator("#marge-tavaline select[name=stage]").select_option(value=value)
    page.locator("#marge-tavaline button[type=submit]").click()
    wait_for_htmx(page)
    expect(page.locator("#teema-hetkeseis .metaline__value")).to_have_text(label)
    history = page.locator("#ajalugu-loend article.uxtl__item")
    rows = history.count()
    assert rows >= 1

    # The same stage again, and nothing else: nothing would be written.
    _save_stage_only(page, value)

    panel = page.locator("#marge-tavaline")
    refusal = panel.locator(".formerror[role=alert]")
    expect(refusal).to_have_text(REFUSAL)
    # Focus-safe: the summary takes the cursor, and is on screen to read.
    expect(refusal).to_be_focused()
    expect(refusal).to_be_in_viewport()
    # The panel kept what was chosen, so the person can add the missing half.
    expect(panel.locator("select[name=stage]")).to_have_value(value)
    # Not colour alone: the refusal is words, announced as an alert.
    assert (refusal.text_content() or "").strip() == REFUSAL

    # Nothing reached the file.
    expect(history).to_have_count(rows)
    expect(page.locator("#teema-hetkeseis .metaline__value")).to_have_text(label)
