"""A failed save is told, and a save elsewhere keeps what was typed.

The Teema workspace is one column that most saves replace whole, and two things
went wrong around that in a real browser (ENG-012, ENG-034):

* **a failure that was not a rendered refusal was silent** — a 500, a dropped
  connection, an expired gate or session, a stale CSRF token — and an expired
  gate swapped the password page into the column; the note's autosave went on
  saying «Salvestatud» over text that had not been saved;
* **a save in one panel threw away what was typed in the others** — the
  half-written `Mida tegid?`, an open `+ Märge`, a row's `Muuda`, the note's
  last keystrokes.

Everything here is about what the page does, so it is measured in the page:
which element is on screen, what it says, what the boxes hold, and what went
over the wire. The server's half — the 401 and the marked 403 — is pinned in
`tests/test_htmx_failure_answers.py`.
"""

from __future__ import annotations

import re
from datetime import date, timedelta
from urllib.parse import quote

import pytest
from playwright.sync_api import expect

from e2e.conftest import (
    MARTIN,
    create_matter,
    finish_current_action,
    open_add_panel,
    pass_the_gate,
    set_next_step,
    sign_in,
    wait_for_htmx,
)
from e2e.titles import unique_title

pytestmark = pytest.mark.e2e

FAILURE = "[data-request-failure]"
MARGE_FORM = "#marge-tavaline form"
MARGE_SAVE = "#marge-tavaline button[type=submit]"
COMPOSER = "#praegune-tegevus .composer__body"
NOTE = ".railnote textarea"


def _when(days: int) -> str:
    day = date.today() + timedelta(days=days)
    return f"{day.day}.{day.month}.{day.year}"


def _teema(page, base_url: str, *, step: bool = True) -> str:
    """A fresh Teema of Martin's, with an open step unless asked otherwise."""
    sign_in(page, base_url, MARTIN)
    url = create_matter(page, base_url, unique_title("Salvestuse tõrge"))
    if step:
        set_next_step(page, "Helista ministeeriumi", _when(5))
        wait_for_htmx(page)
    return url


def _gate_teema(page, gate_base_url: str) -> str:
    """The same, behind the shared gate with Martin chosen as the persona."""
    pass_the_gate(page, gate_base_url)
    page.goto(f"{gate_base_url}/osakond/")
    _choose_persona(page, "Martin")
    return create_matter(page, gate_base_url, unique_title("Värava tõrge"))


def _choose_persona(page, name: str) -> None:
    page.locator("#persona-pill").click()
    page.locator("#persona-menu").get_by_role("button", name=name, exact=False).click()
    page.wait_for_load_state("networkidle")


def _duplicate_ids(page) -> list[str]:
    return page.evaluate(
        """() => {
          const seen = new Map();
          document.querySelectorAll('[id]').forEach(el => {
            seen.set(el.id, (seen.get(el.id) || 0) + 1);
          });
          return [...seen].filter(([, n]) => n > 1).map(([id]) => id);
        }"""
    )


def _drop_session(page) -> None:
    """What twelve hours do to the gate, or signing out in another window does."""
    kept = [cookie for cookie in page.context.cookies() if cookie["name"] != "sessionid"]
    page.context.clear_cookies()
    page.context.add_cookies(kept)


def _history(page) -> str:
    return page.locator("#ajajoon").inner_text()


# -- ENG-012: failures are told, in Estonian, beside the form ----------------


@pytest.mark.parametrize("width", [375, 420, 1024, 1440])
def test_a_server_error_is_told_beside_the_form_and_keeps_the_text(page, base_url, width):
    page.set_viewport_size({"width": width, "height": 900})
    _teema(page, base_url, step=False)
    open_add_panel(page, "marge-tavaline")
    page.fill("#id_marge_title", "Ministeerium saatis uue versiooni")

    posts: list[str] = []
    page.route(
        "**/lisa/marge/",
        lambda route: (
            posts.append(route.request.method),
            route.fulfill(status=500, body="<h1>Server Error (500)</h1>"),
        ),
    )
    page.locator(MARGE_SAVE).click()
    notice = page.locator(f"{MARGE_FORM} {FAILURE}")
    expect(notice).to_be_visible()
    expect(notice).to_have_attribute("role", "alert")
    expect(notice).to_contain_text("Salvestamine ebaõnnestus.")
    expect(notice).to_contain_text("Serveris tekkis viga.")
    expect(notice).to_contain_text("Sisestatud tekst on alles")
    assert "500" not in notice.inner_text() and "Server Error" not in notice.inner_text()
    expect(page.locator("#id_marge_title")).to_have_value("Ministeerium saatis uue versiooni")

    # Told once and not sent again on the person's behalf.
    page.wait_for_timeout(1200)
    assert posts == ["POST"]

    # It sits in the flow, inside the page's width, and covers none of the form.
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    box = notice.bounding_box()
    title = page.locator("#id_marge_title").bounding_box()
    assert box is not None and title is not None
    assert box["x"] >= 0 and box["x"] + box["width"] <= width
    assert box["y"] >= title["y"] + title["height"]

    # And a retry that works takes the notice with it.
    page.unroute("**/lisa/marge/")
    page.locator(MARGE_SAVE).click()
    wait_for_htmx(page)
    expect(page.locator(FAILURE)).to_have_count(0)
    assert "Ministeerium saatis uue versiooni" in _history(page)


def test_a_dropped_connection_is_told_and_keeps_the_text(page, base_url):
    _teema(page, base_url, step=False)
    open_add_panel(page, "marge-tavaline")
    page.fill("#id_marge_title", "Kirjutatud ühenduseta")
    page.route("**/lisa/marge/", lambda route: route.abort())
    page.locator(MARGE_SAVE).click()
    notice = page.locator(f"{MARGE_FORM} {FAILURE}")
    expect(notice).to_contain_text("Ühendus serveriga katkes.")
    expect(page.locator("#id_marge_title")).to_have_value("Kirjutatud ühenduseta")


def test_an_expired_gate_keeps_the_column_and_offers_the_way_back(page, gate_base_url):
    _gate_teema(page, gate_base_url)
    open_add_panel(page, "marge-tavaline")
    page.fill("#id_marge_title", "Kirjutatud enne värava aegumist")
    _drop_session(page)

    page.locator(MARGE_SAVE).click()
    notice = page.locator(f"{MARGE_FORM} {FAILURE}")
    expect(notice).to_contain_text("Sisselogimine on aegunud.")
    # The column is still the column: no password form swapped into it.
    expect(page.locator("#teema-vaade")).to_have_count(1)
    expect(page.locator("input[type=password]")).to_have_count(0)
    expect(page.locator("#id_marge_title")).to_have_value("Kirjutatud enne värava aegumist")
    link = notice.get_by_role("link", name="Logi uuesti sisse")
    expect(link).to_have_attribute("href", "/konto/varav/")


def test_an_expired_session_offers_sign_in_back_to_this_page(page, base_url):
    url = _teema(page, base_url, step=False)
    open_add_panel(page, "marge-tavaline")
    page.fill("#id_marge_title", "Kirjutatud enne sessiooni aegumist")
    _drop_session(page)

    page.locator(MARGE_SAVE).click()
    notice = page.locator(f"{MARGE_FORM} {FAILURE}")
    expect(notice).to_contain_text("Sisselogimine on aegunud.")
    path = re.sub(r"^https?://[^/]+", "", url)
    expect(notice.get_by_role("link", name="Logi uuesti sisse")).to_have_attribute(
        "href", f"/konto/arendus-sisselogimine/?next={quote(path, safe='')}"
    )
    expect(page.locator("#id_marge_title")).to_have_value("Kirjutatud enne sessiooni aegumist")


def test_a_persona_change_in_another_tab_is_told_and_saves_nothing(page, gate_base_url):
    _gate_teema(page, gate_base_url)
    open_add_panel(page, "marge-tavaline")
    page.fill("#id_marge_title", "Kirjutatud Martini nimel")

    other = page.context.new_page()
    other.goto(f"{gate_base_url}/osakond/")
    _choose_persona(other, "Sandra")
    other.close()

    page.locator(MARGE_SAVE).click()
    notice = page.locator(f"{MARGE_FORM} {FAILURE}")
    expect(notice).to_contain_text("Leht on aegunud")
    expect(notice.get_by_role("button", name="Laadi leht uuesti")).to_be_visible()
    expect(page.locator("#id_marge_title")).to_have_value("Kirjutatud Martini nimel")

    notice.get_by_role("button", name="Laadi leht uuesti").click()
    page.wait_for_load_state("networkidle")
    assert "Kirjutatud Martini nimel" not in _history(page)


def test_a_failed_note_autosave_never_says_saved(page, base_url):
    _teema(page, base_url, step=False)
    unload_posts: list[str] = []
    page.route("**/markmed/", lambda route: route.fulfill(status=500, body=""))
    box = page.locator(NOTE)
    box.click()
    box.type("Privaatne mõte, mis ei salvestunud", delay=5)

    hint = page.locator("#teema-markme-seis")
    expect(hint).to_have_text("Salvestamata", timeout=5000)
    expect(page.locator(f".railnote {FAILURE}")).to_contain_text("Salvestamine ebaõnnestus.")

    # The server is back: the next keystroke saves, and the line says so again.
    page.unroute("**/markmed/")
    box.type(".", delay=5)
    expect(hint).to_contain_text("Salvestatud", timeout=5000)
    expect(page.locator(FAILURE)).to_have_count(0)
    page.reload()
    expect(page.locator(NOTE)).to_have_value("Privaatne mõte, mis ei salvestunud.")

    # A failure the person leaves the page on is still flushed on the way out.
    page.route(
        "**/markmed/",
        lambda route: (
            unload_posts.append(route.request.method),
            route.fulfill(status=500, body=""),
        ),
    )
    page.locator(NOTE).type(" Lisa.", delay=5)
    expect(hint).to_have_text("Salvestamata", timeout=5000)
    before = len(unload_posts)
    page.goto(f"{base_url}/minu-asjad/")
    page.wait_for_timeout(800)
    assert len(unload_posts) - before == 1


# -- ENG-034: a save elsewhere keeps what was typed --------------------------


def test_a_refused_and_then_a_saved_marge_keep_the_half_written_task(page, base_url):
    _teema(page, base_url)
    page.fill(COMPOSER, "Helistasin, pooleli kirjeldus")

    # A refusal stays inline on its own panel.
    open_add_panel(page, "marge-tavaline")
    page.locator(MARGE_SAVE).click()
    wait_for_htmx(page)
    expect(page.locator("#marge-tavaline .formerror")).to_be_visible()
    expect(page.locator(COMPOSER)).to_have_value("Helistasin, pooleli kirjeldus")

    # A save lands, and the task's box is untouched by it.
    page.fill("#id_marge_title", "Ministeerium vastas")
    page.locator(MARGE_SAVE).click()
    wait_for_htmx(page)
    assert "Ministeerium vastas" in _history(page)
    expect(page.locator(COMPOSER)).to_have_value("Helistasin, pooleli kirjeldus")
    assert _duplicate_ids(page) == []

    # The carried form still posts, with a token the server accepts.
    page.locator("#praegune-tegevus button[type=submit]").last.click()
    wait_for_htmx(page)
    assert "Helistasin, pooleli kirjeldus" in _history(page)


def test_a_task_save_keeps_an_open_marge_draft_open(page, base_url):
    _teema(page, base_url)
    open_add_panel(page, "marge-tavaline")
    page.fill("#id_marge_title", "Märke mustand")
    page.fill(COMPOSER, "Helistasin")
    page.locator("#praegune-tegevus button[type=submit]").last.click()
    wait_for_htmx(page)

    assert "Helistasin" in _history(page)
    expect(page.locator("#id_marge_title")).to_be_visible()
    expect(page.locator("#id_marge_title")).to_have_value("Märke mustand")
    assert page.locator("#lisa-marge-valik").is_checked()
    assert _duplicate_ids(page) == []

    page.locator(MARGE_SAVE).click()
    wait_for_htmx(page)
    assert "Märke mustand" in _history(page)


def test_a_draft_in_a_panel_left_behind_stays_behind(page, base_url):
    """Carried, but not reopened: the person had already switched away from it."""
    _teema(page, base_url)
    open_add_panel(page, "lisa-kaasamine")
    page.locator("#lisa-kaasamine textarea").first.fill("Kaasamise mustand")
    open_add_panel(page, "marge-tavaline")
    page.fill("#id_marge_title", "Teine märge")
    page.locator(MARGE_SAVE).click()
    wait_for_htmx(page)

    assert not page.locator("#lisa-kaasamine-valik").is_checked()
    expect(page.locator("#lisa-kaasamine textarea").first).to_have_value("Kaasamise mustand")


def test_an_open_row_editor_survives_an_unrelated_save(page, base_url):
    _teema(page, base_url, step=False)
    open_add_panel(page, "marge-tavaline")
    page.fill("#id_marge_title", "Rida üks")
    page.locator(MARGE_SAVE).click()
    wait_for_htmx(page)

    page.locator(".uxtl__edit").first.click()
    wait_for_htmx(page)
    editor = page.locator(".uxtl__editform textarea[name=note]")
    editor.fill("Pooleli parandus")
    editor.focus()

    open_add_panel(page, "marge-tavaline")
    page.fill("#id_marge_title", "Rida kaks")
    page.locator(MARGE_SAVE).click()
    wait_for_htmx(page)

    assert "Rida kaks" in _history(page)
    expect(editor).to_have_count(1)
    expect(editor).to_have_value("Pooleli parandus")
    assert _duplicate_ids(page) == []

    # And it is still a working editor: its save lands on its own row.
    page.locator(".uxtl__editform button[type=submit]").first.click()
    wait_for_htmx(page)
    expect(page.locator(".uxtl__editform")).to_have_count(0)
    assert "Pooleli parandus" in _history(page)


def test_the_note_survives_a_save_inside_its_debounce(page, base_url):
    _teema(page, base_url, step=False)
    box = page.locator(NOTE)
    box.click()
    box.type("Kiire märkus", delay=5)
    open_add_panel(page, "marge-tavaline")
    page.fill("#id_marge_title", "Samal ajal")
    page.locator(MARGE_SAVE).click()
    wait_for_htmx(page)

    expect(page.locator(NOTE)).to_have_value("Kiire märkus")
    expect(page.locator("#teema-markme-seis")).to_contain_text("Salvestatud", timeout=5000)
    page.reload()
    expect(page.locator(NOTE)).to_have_value("Kiire märkus")


def test_a_draft_about_a_task_that_moved_on_is_shown_not_reposted(browser, base_url):
    """Another tab finished the task: the composer is not carried onto the next one."""
    context = browser.new_context()
    try:
        page = context.new_page()
        url = _teema(page, base_url)
        page.fill(COMPOSER, "Minu pooleli kirjeldus")

        other = context.new_page()
        other.goto(url)
        other.wait_for_load_state("networkidle")
        finish_current_action(other, "Teises aknas tehtud")
        other.close()

        open_add_panel(page, "marge-tavaline")
        page.fill("#id_marge_title", "Märge pärast seda")
        page.locator(MARGE_SAVE).click()
        wait_for_htmx(page)

        unsaved = page.locator("section.unsaved")
        expect(unsaved).to_contain_text("Salvestamata sisu")
        expect(unsaved).to_contain_text("Minu pooleli kirjeldus")
        expect(page.locator(COMPOSER)).to_have_count(0)
        assert "Minu pooleli kirjeldus" not in _history(page)
        assert _duplicate_ids(page) == []
    finally:
        context.close()


def test_a_clean_workspace_carries_nothing_across_a_save(page, base_url):
    """Nothing typed, nothing kept: every region is the server's fresh one."""
    _teema(page, base_url)
    open_add_panel(page, "lisa-kaasamine")
    open_add_panel(page, "marge-tavaline")
    page.evaluate(
        "() => document.querySelectorAll('[data-draft-host]')"
        ".forEach(el => { el.__before = true; })"
    )
    page.fill("#id_marge_title", "Puhas salvestus")
    page.locator(MARGE_SAVE).click()
    wait_for_htmx(page)

    survivors = page.evaluate(
        "() => [...document.querySelectorAll('[data-draft-host]')].filter(el => el.__before).length"
    )
    assert survivors == 0
    expect(page.locator("[hx-preserve]")).to_have_count(0)
    expect(page.locator("section.unsaved")).to_have_count(0)


def test_one_unload_listener_however_many_swaps(page, base_url):
    page.add_init_script(
        """(() => {
          window.__unloadListeners = 0;
          const add = window.addEventListener;
          window.addEventListener = function (type, ...rest) {
            if (type === 'beforeunload') { window.__unloadListeners += 1; }
            return add.call(this, type, ...rest);
          };
        })()"""
    )
    _teema(page, base_url, step=False)
    for number in range(3):
        open_add_panel(page, "marge-tavaline")
        page.fill("#id_marge_title", f"Salvestus {number}")
        page.locator(MARGE_SAVE).click()
        wait_for_htmx(page)
    assert page.evaluate("window.__unloadListeners") == 1
