"""`+ Lisa samm` in the browser (docs/adr/0119).

The Python suite proves the rules at the seams that own them — placement, the
precisions, the audit row, the refusals. What only a browser shows is the half a
lawyer meets: that `Muuda` is there on a file with nothing on its rail, that the
disclosure opens and saves through the panel's own `hx-post`, that the step is
on the rail after a reload, and that it can be corrected and taken off again.

Each test files its own Matter: these write Matter-level rows, and the seeded
world is shared across a shard.
"""

from __future__ import annotations

import re

import pytest

from e2e.conftest import SANDRA, create_matter, sign_in, unique_title

pytestmark = pytest.mark.e2e


def _open_panel(page) -> None:
    page.locator(".lprail__edit").click()
    page.wait_for_selector("#menetluse-kulg-muuda form")


def _save_panel(page) -> None:
    """Press `Salvesta` and wait for the POST itself, not for `networkidle`.

    `networkidle` can return before the swap lands, and the rail read after it
    is the one from before the save.
    """
    with page.expect_response(
        lambda r: "/menetluse-kulg/" in r.url and r.request.method == "POST"
    ) as caught:
        page.locator("#menetluse-kulg-muuda").get_by_role("button", name="Salvesta").click()
    assert caught.value.status == 200
    page.wait_for_load_state("networkidle")


def _rail(page) -> list[str]:
    return [
        text.strip() for text in page.locator(".lprail .tl-strip .tl-step__what").all_inner_texts()
    ]


def test_a_file_with_nothing_on_its_rail_adds_edits_and_removes_a_step(page, base_url):
    sign_in(page, base_url, SANDRA)
    url = create_matter(page, base_url, unique_title("QA lisa samm"), owner=SANDRA)
    page.goto(url)
    page.wait_for_load_state("networkidle")
    assert page.locator(".lprail .tl-strip").count() == 0

    _open_panel(page)
    page.locator("#menetluse-kulg-muuda").get_by_text("+ Lisa samm", exact=True).click()
    page.fill("input[name='uus__title']", "Komisjoni istung")
    page.fill("input[name='uus_date']", "12.03.2026")
    _save_panel(page)

    assert _rail(page) == ["Komisjoni istung"]
    page.reload()
    page.wait_for_load_state("networkidle")
    assert _rail(page) == ["Komisjoni istung"]
    assert "12.3.2026" in page.locator(".lprail .tl-strip").inner_text()

    # Corrected in place: open the one-line row, change the name.
    _open_panel(page)
    panel = page.locator("#menetluse-kulg-muuda")
    panel.locator(".kulgform__stepsum").first.click()
    title = panel.locator("input[name$='__title']").first
    title.fill("Majanduskomisjoni istung")
    _save_panel(page)
    page.reload()
    page.wait_for_load_state("networkidle")
    assert _rail(page) == ["Majanduskomisjoni istung"]

    # And taken off again.
    _open_panel(page)
    panel = page.locator("#menetluse-kulg-muuda")
    panel.locator(".kulgform__stepsum").first.click()
    panel.get_by_text("Eemalda samm", exact=True).click()
    _save_panel(page)
    page.reload()
    page.wait_for_load_state("networkidle")
    assert page.locator(".lprail .tl-strip").count() == 0


def test_a_step_placed_between_phases_and_a_removed_phase_takes_nothing_with_it(page, base_url):
    sign_in(page, base_url, SANDRA)
    url = create_matter(
        page,
        base_url,
        unique_title("QA samm faaside vahel"),
        owner=SANDRA,
        stage="Kooskõlastusringil",
    )
    page.goto(f"{url}muuda/")
    page.wait_for_load_state("networkidle")
    page.get_by_role("radio", name="Seadus", exact=True).check()
    page.get_by_role("button", name="Salvesta").click()
    page.wait_for_url(re.compile(r"/teemad/[0-9a-f-]{36}/$"))
    page.wait_for_load_state("networkidle")
    before = _rail(page)
    assert "Kooskõlastusring" in before and "Valitsuses" in before

    _open_panel(page)
    page.locator("#menetluse-kulg-muuda").get_by_text("+ Lisa samm", exact=True).click()
    page.fill("input[name='uus__title']", "Teine kooskõlastusring")
    page.select_option("select[name='uus__after']", label="Pärast: Kooskõlastusring")
    _save_panel(page)

    rail = _rail(page)
    at = rail.index("Kooskõlastusring")
    assert rail[at : at + 3] == ["Kooskõlastusring", "Teine kooskõlastusring", "Valitsuses"]

    # Take a future phase off: only that phase goes.
    _open_panel(page)
    page.locator("input[name='riigikogu__shown']").uncheck()
    _save_panel(page)
    page.reload()
    page.wait_for_load_state("networkidle")
    assert _rail(page) == [label for label in rail if label != "Riigikogus"]
