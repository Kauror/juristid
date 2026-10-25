"""Finding your way back, to one row, and to what you just saved — in a browser.

* **Back to the list.** A searched, paged Teemad list, a Teema opened from it,
  and the browser's own Back returns to the same address, the same search, the
  same page and the same scroll position; «Tagasi tulemustesse» on the Teema
  goes to the same address.
* **A row's own link.** «Kopeeri link» on a row far behind the newest rows
  copies an address that, opened fresh, lands on that row — open, in its
  period, and shown.
* **The row a save wrote** is shown for a moment after the server answered.
* **Leaving with unsaved text** asks first; leaving with nothing typed, or after
  the text was saved, does not.

The server half is `tests/test_navigation_qol.py`.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys

import pytest
from playwright.sync_api import expect

from e2e.conftest import (
    REPOSITORY_ROOT,
    SANDRA,
    accept_leave_prompts,
    chronology,
    create_matter,
    open_add_panel,
    open_kaik_row,
    sign_in,
    wait_for_htmx,
)
from e2e.titles import unique_title

pytestmark = pytest.mark.e2e

#: Plants `E2E_BUSY_ROWS` notes in «Idee» and a third of that in
#: «Kooskõlastus» on `E2E_BUSY_MATTER`, through the services — the same
#: history `e2e/test_bounded_teema_history.py` reads.
_BUSY_SCRIPT = (
    "import os;"
    "from app.accounts.models import User;"
    "from app.matters.models import Matter;"
    "from app.matters.services import add_entry, change_stage;"
    "from app.workflow.models import StageVocabulary as S;"
    "m = Matter.objects.get(pk=os.environ['E2E_BUSY_MATTER']);"
    "u = m.owner or User.objects.filter(is_active=True).first();"
    "n = int(os.environ['E2E_BUSY_ROWS']);"
    "change_stage(matter=m, stage=S.objects.get(key='idea'), actor=u);"
    "[add_entry(matter=m, author=u, body=f'Vana märge {i:03d}') for i in range(n)];"
    "change_stage(matter=m, stage=S.objects.get(key='consultation'), actor=u);"
    "[add_entry(matter=m, author=u, body=f'Uus märge {i:03d}') for i in range(n // 3)];"
    "print(n)"
)


def _plant_busy_history(page, rows: int = 80) -> None:
    matter_id = re.search(r"/teemad/([0-9a-f-]{36})/", page.url).group(1)
    result = subprocess.run(  # noqa: S603
        [sys.executable, "manage.py", "shell", "-c", _BUSY_SCRIPT],
        cwd=REPOSITORY_ROOT,
        env={
            **os.environ,
            "DJANGO_SETTINGS_MODULE": "config.settings",
            "E2E_BUSY_MATTER": matter_id,
            "E2E_BUSY_ROWS": str(rows),
        },
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )
    assert result.returncode == 0, "\n".join(["stdout:", result.stdout, "stderr:", result.stderr])


# -- back to the list ---------------------------------------------------------------


def test_back_returns_to_the_same_search_page_and_scroll(page, base_url):
    sign_in(page, base_url, SANDRA)
    word = unique_title("Tagasitee").split()[-1]
    # One more than a page of the register (12), all found by one word.
    for number in range(13):
        create_matter(page, base_url, f"Tagasitee {word} {number:02d}")

    page.set_viewport_size({"width": 1280, "height": 600})
    page.goto(f"{base_url}/teemad/")
    page.wait_for_load_state("networkidle")
    page.fill("#teemad-otsing", word)
    expect(page).to_have_url(re.compile(r"[?&]q="), timeout=10_000)
    wait_for_htmx(page)
    results = page.locator("#teemad-tulemused")
    results.locator("a[href*='leht=2']").first.click()
    page.wait_for_load_state("networkidle")
    expect(page).to_have_url(re.compile(r"leht=2"))
    list_url = page.url
    page.mouse.wheel(0, 400)
    page.wait_for_function("window.scrollY > 0")
    scrolled = page.evaluate("window.scrollY")

    row = results.locator("a[href^='/teemad/']").filter(has_text="Tagasitee").first
    row.click()
    page.wait_for_url(re.compile(r"/teemad/[0-9a-f-]{36}/$"))
    page.wait_for_load_state("networkidle")

    # The quiet link names the list it came from, exactly.
    back_link = page.get_by_role("link", name="← Tagasi tulemustesse")
    expect(back_link).to_be_visible()
    assert base_url + back_link.get_attribute("href") == list_url

    page.go_back()
    page.wait_for_load_state("networkidle")
    assert page.url == list_url
    expect(page.locator("#teemad-otsing")).to_have_value(word)
    expect(results.locator("tbody tr")).to_have_count(1)
    page.wait_for_function("y => Math.abs(window.scrollY - y) < 40", arg=scrolled)

    # The link itself goes to the same list.
    page.go_forward()
    page.wait_for_load_state("networkidle")
    page.get_by_role("link", name="← Tagasi tulemustesse").click()
    page.wait_for_load_state("networkidle")
    assert page.url == list_url
    expect(page.locator("#teemad-otsing")).to_have_value(word)


def test_a_teema_opened_directly_offers_no_way_back(page, base_url):
    sign_in(page, base_url, SANDRA)
    url = create_matter(page, base_url, unique_title("Otselink"))

    page.goto(url)
    page.wait_for_load_state("networkidle")

    expect(page.get_by_role("link", name="← Tagasi tulemustesse")).to_have_count(0)


# -- a row's own link ---------------------------------------------------------------


def test_a_copied_row_link_lands_on_an_old_row(page, base_url):
    page.context.grant_permissions(["clipboard-read", "clipboard-write"], origin=base_url)
    sign_in(page, base_url, SANDRA)
    url = create_matter(page, base_url, unique_title("Kirje link"))
    _plant_busy_history(page)

    # The oldest note is behind the bounded read; the whole history shows it.
    page.goto(f"{url}?kaik=koik")
    page.wait_for_load_state("networkidle")
    row = chronology(page).locator("article.uxtl__item").filter(has_text="Vana märge 000")
    row.evaluate("node => node.closest('details.kaikstage').open = true")
    row.locator(".uxtl__toggle").click()
    expect(row.get_by_role("button", name="Kopeeri link")).to_be_visible()
    # Held by its address, not its name: the name changes once it has copied.
    copy = row.locator("button[data-copy-href]")
    copy.click()
    expect(copy).to_have_text("Link kopeeritud")
    copied = page.evaluate("navigator.clipboard.readText()")
    key = row.get_attribute("data-kaik-rida")
    assert (
        copied.startswith(url) and f"kirje={key}" in copied and copied.endswith(f"#kaik-{key}-rida")
    )

    # Opened fresh, at the default bounded read: the row is drawn, open and shown.
    page.goto(f"{base_url}/minu-asjad/")
    page.goto(copied)
    page.wait_for_load_state("networkidle")
    landed = page.locator(f'article.uxtl__item[data-kaik-rida="{key}"]')
    expect(landed).to_contain_text("Vana märge 000")
    expect(landed.locator(".uxtl__toggle")).to_have_attribute("aria-expanded", "true")
    # `article` is `display: contents`; its panel is what is on screen.
    expect(landed.locator(".uxtl__body")).to_be_in_viewport()
    expect(landed).to_have_class(re.compile(r"uxtl__item--saved"))
    expect(landed).not_to_have_class(re.compile(r"uxtl__item--saved"), timeout=6_000)


# -- the row a save wrote -------------------------------------------------------------


def _open_muu(page):
    open_add_panel(page, "lisa-marge")
    # The radio sits under its chip, so it is chosen the way a keyboard does.
    page.locator("#marge-muu-valik").focus()
    page.keyboard.press("Space")
    form = page.locator("#marge-muu form")
    expect(form).to_be_visible()
    return form


def test_a_saved_row_is_shown_for_a_moment(page, base_url):
    sign_in(page, base_url, SANDRA)
    create_matter(page, base_url, unique_title("Salvestuse esiletõst"))

    form = _open_muu(page)
    form.locator('[name="description"]').fill("Esiletõstetud taustamaterjal.")
    form.get_by_role("button", name="Salvesta", exact=True).click()
    wait_for_htmx(page)

    saved = (
        chronology(page)
        .locator("article.uxtl__item")
        .filter(has_text="Esiletõstetud taustamaterjal.")
    )
    expect(saved).to_have_class(re.compile(r"uxtl__item--saved"))
    # Only that row, and the marker is consumed.
    expect(chronology(page).locator("article.uxtl__item--saved")).to_have_count(1)
    expect(page.locator("[data-salvestatud]")).to_have_count(0)
    expect(saved).not_to_have_class(re.compile(r"uxtl__item--saved"), timeout=6_000)


def test_a_row_corrected_in_place_is_shown_for_a_moment(page, base_url):
    sign_in(page, base_url, SANDRA)
    create_matter(page, base_url, unique_title("Paranduse esiletõst"))
    form = _open_muu(page)
    form.locator('[name="description"]').fill("Esialgne kirjeldus.")
    form.get_by_role("button", name="Salvesta", exact=True).click()
    wait_for_htmx(page)
    row = chronology(page).locator("article.uxtl__item").filter(has_text="Esialgne kirjeldus.")
    expect(row).not_to_have_class(re.compile(r"uxtl__item--saved"), timeout=6_000)

    open_kaik_row(row)
    row.get_by_role("button", name="Muuda", exact=True).click()
    box = row.locator(".uxtl__editform textarea").first
    box.wait_for()
    # Opening the editor is not a save.
    expect(row).not_to_have_class(re.compile(r"uxtl__item--saved"))
    box.fill("Parandatud kirjeldus.")
    row.locator(".uxtl__editform button[type=submit]").click()
    wait_for_htmx(page)

    corrected = (
        chronology(page).locator("article.uxtl__item").filter(has_text="Parandatud kirjeldus.")
    )
    expect(corrected).to_have_class(re.compile(r"uxtl__item--saved"))
    expect(page.locator("[data-salvestatud]")).to_have_count(0)


def test_a_refused_save_shows_no_row(page, base_url):
    sign_in(page, base_url, SANDRA)
    create_matter(page, base_url, unique_title("Keeldutud salvestus"))

    form = _open_muu(page)
    form.locator('[name="url"]').fill("https://www.example.ee/taust")
    form.get_by_role("button", name="Salvesta", exact=True).click()
    wait_for_htmx(page)

    expect(page.locator("#marge-muu form")).to_contain_text("Kirjelda, mida lisad.")
    expect(page.locator("article.uxtl__item--saved")).to_have_count(0)


# -- leaving with unsaved text -------------------------------------------------------


def _dialogs(page) -> list[str]:
    """Record every dialog's type and answer «stay» — the person who stays."""
    seen: list[str] = []

    def answer(dialog):
        seen.append(dialog.type)
        dialog.dismiss()

    page.on("dialog", answer)
    page._stay_answer = answer  # for `_stop_staying`
    return seen


def _stop_staying(page) -> None:
    page.remove_listener("dialog", page._stay_answer)


def _go_to_minu_asjad(page) -> None:
    page.get_by_role("navigation", name="Peamine").get_by_role(
        "link", name="Minu asjad", exact=True
    ).click()


def test_leaving_with_typed_text_asks_first(page, base_url):
    sign_in(page, base_url, SANDRA)
    url = create_matter(page, base_url, unique_title("Salvestamata tekst"))
    seen = _dialogs(page)

    form = _open_muu(page)
    form.locator('[name="description"]').fill("Pool lauset, mida pole salvestatud")
    _go_to_minu_asjad(page)
    page.wait_for_timeout(500)

    # Asked, and staying kept the text where it was.
    assert seen == ["beforeunload"]
    assert page.url == url
    expect(page.locator("#marge-muu form [name='description']")).to_have_value(
        "Pool lauset, mida pole salvestatud"
    )

    # Answering «leave» leaves (`accept_leave_prompts`, for tests that listen).
    _stop_staying(page)
    accept_leave_prompts(page)
    _go_to_minu_asjad(page)
    page.wait_for_url(f"{base_url}/minu-asjad/")


def test_leaving_after_a_refused_save_asks_first(page, base_url):
    """A refusal re-renders the form with the text in it, which the swap's own
    test reads as unchanged — but none of it was saved."""
    sign_in(page, base_url, SANDRA)
    url = create_matter(page, base_url, unique_title("Keeldutud ja lahkumine"))
    seen = _dialogs(page)

    form = _open_muu(page)
    form.locator('[name="url"]').fill("https://www.example.ee/keeldutud")
    form.get_by_role("button", name="Salvesta", exact=True).click()
    wait_for_htmx(page)
    expect(page.locator("#marge-muu form")).to_contain_text("Kirjelda, mida lisad.")
    _go_to_minu_asjad(page)
    page.wait_for_timeout(500)

    assert seen == ["beforeunload"]
    assert page.url == url
    expect(page.locator("#marge-muu form [name='url']")).to_have_value(
        "https://www.example.ee/keeldutud"
    )

    # The corrected save goes through, and then leaving asks nothing.
    form = page.locator("#marge-muu form")
    form.locator('[name="description"]').fill("Nüüd kirjeldusega.")
    form.get_by_role("button", name="Salvesta", exact=True).click()
    wait_for_htmx(page)
    expect(chronology(page)).to_contain_text("Nüüd kirjeldusega.")
    _go_to_minu_asjad(page)
    page.wait_for_url(f"{base_url}/minu-asjad/")
    assert seen == ["beforeunload"]


def test_downloading_a_file_does_not_ask(page, base_url):
    sign_in(page, base_url, SANDRA)
    create_matter(page, base_url, unique_title("Fail ja mustand"))
    seen = _dialogs(page)

    form = _open_muu(page)
    form.locator('[name="description"]').fill("Taust failiga.")
    form.locator('input[type="file"]').set_input_files(
        [{"name": "taust.pdf", "mimeType": "application/pdf", "buffer": b"%PDF-1.4 taust"}]
    )
    form.get_by_role("button", name="Salvesta", exact=True).click()
    wait_for_htmx(page)
    form = _open_muu(page)
    form.locator('[name="description"]').fill("Pooleli järgmine kirje")
    row = chronology(page).locator("article.uxtl__item").filter(has_text="Taust failiga.")
    open_kaik_row(row)
    with page.expect_download():
        row.locator("a.uxtl__file", has_text="taust.pdf").click()

    assert seen == []
    expect(page.locator("#marge-muu form [name='description']")).to_have_value(
        "Pooleli järgmine kirje"
    )


def test_leaving_asks_nothing_when_nothing_is_unsaved(page, base_url):
    sign_in(page, base_url, SANDRA)
    url = create_matter(page, base_url, unique_title("Puhas lahkumine"))
    seen = _dialogs(page)

    # A clean page: a link, Back and a reload all just go.
    page.get_by_role("navigation", name="Peamine").get_by_role(
        "link", name="Minu asjad", exact=True
    ).click()
    page.wait_for_url(f"{base_url}/minu-asjad/")
    page.go_back()
    page.wait_for_url(url)
    page.wait_for_load_state("networkidle")

    # Text that was saved is not unsaved: a reload after the save just goes.
    form = _open_muu(page)
    form.locator('[name="description"]').fill("Salvestatud enne lahkumist.")
    form.get_by_role("button", name="Salvesta", exact=True).click()
    wait_for_htmx(page)
    expect(chronology(page)).to_contain_text("Salvestatud enne lahkumist.")
    page.reload()
    page.wait_for_load_state("networkidle")

    assert seen == []
