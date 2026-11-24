"""«Minu vaated» in a browser (UX-007, docs/adr/0222): save, open, rename, remove.

The server half — canonical queries, the limit, privacy, counts through the one
pipeline — is `tests/test_saved_register_views.py`. Here: a person names the
filter they are looking at, the chip appears after the four predefined ones and
opens the same rows, and the list under «Minu vaated ▾» renames and removes it
with a confirmation. A colleague does not see it.
"""

from __future__ import annotations

import re

import pytest
from playwright.sync_api import expect

from e2e.conftest import MARTIN, SANDRA, open_disclosure, sign_in
from e2e.titles import unique_title

pytestmark = pytest.mark.e2e

STRIP = ".uxviews"


def _save_current(page, name: str) -> None:
    save = page.locator("details.uxviews__save")
    open_disclosure(save)
    save.get_by_label("Vaate nimi").fill(name)
    save.get_by_role("button", name="Salvesta").click()


def test_a_named_view_is_saved_opened_renamed_and_removed(page, base_url):
    sign_in(page, base_url, SANDRA)
    name = unique_title("Suletud ja minu")
    page.goto(f"{base_url}/teemad/?ulatus=minu&olek=suletud&leht=1")

    _save_current(page, name)

    page.wait_for_url(re.compile(r"/teemad/\?olek=suletud&ulatus=minu$"))
    expect(page.get_by_text(f"Vaade «{name}» on salvestatud.")).to_be_visible()
    chip = page.locator(f"{STRIP} a.uxchip--mine", has_text=name)
    expect(chip).to_have_attribute("aria-current", "true")
    count = int(chip.inner_text().rsplit("·", 1)[1])
    expect(page.locator(".pagehead__context")).to_contain_text(str(count))

    # Elsewhere in the register, the chip brings the view back.
    page.goto(f"{base_url}/teemad/")
    page.locator(f"{STRIP} a.uxchip--mine", has_text=name).click()
    page.wait_for_url(re.compile(r"/teemad/\?olek=suletud&ulatus=minu$"))

    mine = page.locator("details.uxviews__mine")
    open_disclosure(mine)
    item = mine.locator(".uxviews__item", has_text=name)
    renamed = f"{name} (uus)"
    item.get_by_label(f"Vaate «{name}» uus nimi").fill(renamed)
    item.get_by_role("button", name=re.compile("^Nimeta ümber")).click()
    expect(page.get_by_text(f"Vaade «{name}» on nüüd «{renamed}».")).to_be_visible()
    expect(page.locator(f"{STRIP} a.uxchip--mine", has_text=renamed)).to_be_visible()

    mine = page.locator("details.uxviews__mine")
    open_disclosure(mine)
    item = mine.locator(".uxviews__item", has_text=renamed)
    confirm = item.locator("details.uxviews__remove")
    open_disclosure(confirm)
    expect(confirm.get_by_text("Teemad jäävad alles.")).to_be_visible()
    confirm.get_by_role("button", name="Jah, eemalda").click()

    expect(page.get_by_text(f"Vaade «{renamed}» on eemaldatud.")).to_be_visible()
    expect(page.locator(f"{STRIP} a.uxchip--mine", has_text=renamed)).to_have_count(0)


def test_a_view_is_its_owners_alone(page, base_url):
    sign_in(page, base_url, SANDRA)
    name = unique_title("Ainult Sandrale")
    page.goto(f"{base_url}/teemad/?olek=koik&vastutaja=puudub")
    _save_current(page, name)
    expect(page.locator(f"{STRIP} a.uxchip--mine", has_text=name)).to_be_visible()

    sign_in(page, base_url, MARTIN)
    page.goto(f"{base_url}/teemad/")

    expect(page.get_by_text(name)).to_have_count(0)
