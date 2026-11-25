"""«Minu vaated» in a browser (UX-007, docs/adr/0222): save, open, rename, remove.

The server half — canonical queries, the limit, privacy, counts through the one
pipeline — is `tests/test_saved_register_views.py`. Here: a person names the
filter they are looking at, the chip appears after the four predefined ones and
opens the same rows, and the list under «Minu vaated ▾» renames and removes it
with a confirmation. A colleague does not see it. Twenty views keep the start
of the register short: six chips on a desktop, three on a phone, the rest
under «Minu vaated».
"""

from __future__ import annotations

import os
import re
import subprocess
import sys

import pytest
from playwright.sync_api import expect

from e2e.conftest import (
    MARTIN,
    READER,
    REPOSITORY_ROOT,
    SANDRA,
    close_disclosure,
    document_overflows,
    open_disclosure,
    sign_in,
)
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


#: Replaces the `READER` persona's views with twenty (or none), through the
#: service, in the server's own database — saving twenty through the page
#: would test the form twenty times and nothing more.
_PLANT_VIEWS_SCRIPT = (
    "import os;"
    "from django.http import QueryDict;"
    "from app.accounts.models import User;"
    "from app.matters import saved_views;"
    "from app.matters.models import SavedRegisterView;"
    "reader = User.objects.get(upn='lugeja@example.invalid');"
    "SavedRegisterView.objects.filter(user=reader).delete();"
    "count = int(os.environ['E2E_VIEW_COUNT']);"
    "[saved_views.save_view(user=reader, name=f'Minu vaade {n + 1:02d}',"
    " params=QueryDict(f'q=n{n}&olek=koik' if n % 4 == 0 else f'aasta={2000 + n}&olek=koik'))"
    " for n in range(count)];"
    "print(count)"
)


def _plant_views(count: int) -> None:
    result = subprocess.run(  # noqa: S603
        [sys.executable, "manage.py", "shell", "-c", _PLANT_VIEWS_SCRIPT],
        cwd=REPOSITORY_ROOT,
        env={
            **os.environ,
            "DJANGO_SETTINGS_MODULE": "config.settings",
            "E2E_VIEW_COUNT": str(count),
        },
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert result.returncode == 0, "\n".join(["stdout:", result.stdout, "stderr:", result.stderr])


@pytest.fixture
def twenty_views():
    _plant_views(20)
    yield
    _plant_views(0)


def _visible_own_chips(page) -> int:
    return page.locator(f"{STRIP} a.uxchip--mine").evaluate_all(
        "chips => chips.filter(chip => chip.offsetParent !== null).length"
    )


def test_twenty_views_on_a_desktop_six_chips_and_the_rest_in_minu_vaated(
    page, base_url, twenty_views
):
    sign_in(page, base_url, READER)
    page.goto(f"{base_url}/teemad/")

    assert _visible_own_chips(page) == 6
    mine = page.locator("details.uxviews__mine")
    # What the reader sees: the other width's «+N» is not drawn.
    summary = re.sub(r"\s+", " ", mine.locator(":scope > summary").inner_text())
    assert summary.strip() == "Minu vaated · +14 ▾", summary
    open_disclosure(mine)
    expect(mine.locator(".uxviews__item")).to_have_count(20)
    expect(mine.locator(".uxviews__item").last).to_contain_text("Minu vaade 20")
    close_disclosure(mine)
    expect(mine.locator(".uxviews__minebody")).to_be_hidden()


def test_twenty_views_on_a_phone_three_chips_and_the_list_starts_soon(page, base_url, twenty_views):
    page.set_viewport_size({"width": 375, "height": 812})
    sign_in(page, base_url, READER)
    page.goto(f"{base_url}/teemad/")

    assert _visible_own_chips(page) == 3
    mine = page.locator("details.uxviews__mine")
    # What the reader sees: the other width's «+N» is not drawn.
    summary = re.sub(r"\s+", " ", mine.locator(":scope > summary").inner_text())
    assert summary.strip() == "Minu vaated · +17 ▾", summary
    strip = page.locator(STRIP).bounding_box()
    assert strip is not None
    # Four predefined chips, three own, «Minu vaated» and «Salvesta»: a few
    # lines, not a screenful (twenty chips were about 600 px here).
    assert strip["height"] <= 260, strip
    assert not document_overflows(page)

    open_disclosure(mine)
    body = mine.locator(".uxviews__minebody")
    expect(body).to_be_visible()
    expect(mine.locator(".uxviews__item")).to_have_count(20)
    box = body.bounding_box()
    assert box is not None and box["x"] >= 0 and box["x"] + box["width"] <= 375
    mine.locator(".uxviews__itemlink", has_text="Minu vaade 12").click()
    page.wait_for_url(re.compile(r"/teemad/\?aasta=2011&olek=koik$"))
