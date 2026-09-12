"""`PRAEGUNE TEGEVUS` and `LISA TEEMALE`, driven the way a person drives them.

`tests/test_teema_workspace.py` proves what gets stored. This file proves the
things that only exist in a browser: that one operation's panel closes when
another opens, that a file dropped into a fact's own form arrives under that
fact's own chronology row, that a refusal reopens exactly the panel it came
from, and that the whole zone survives Back, a refresh and a narrow viewport
(docs/adr/0075).
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest
from playwright.sync_api import expect

from e2e.conftest import (
    MARTIN,
    create_matter,
    finish_current_action,
    open_add_panel,
    open_composer,
    open_next_action_form,
    sign_in,
)

pytestmark = pytest.mark.e2e

PDF = b"%PDF-1.4 synthetic content for the browser suite\n"


def _future(days: int) -> str:
    value = date.today() + timedelta(days=days)
    return f"{value.day}.{value.month}.{value.year}"


def _pdf(tmp_path, name: str, marker: bytes = b"") -> str:
    path = tmp_path / name
    path.write_bytes(PDF + marker)
    return str(path)


def set_step(page, text: str, days: int = 7) -> None:
    open_next_action_form(page)
    page.locator("#lisa-jargmine [name='text']").fill(text)
    page.locator("#id_target_date").fill(_future(days))
    page.locator("#lisa-jargmine button[type=submit]").click()
    page.wait_for_load_state("networkidle")
    expect(page.locator(".curact__text")).to_have_text(text)


def chronology(page):
    return page.locator("#ajalugu-loend")


# ---------------------------------------------------------------------------
# The whole loop, end to end
# ---------------------------------------------------------------------------


def test_the_current_action_loop_from_task_to_result_to_the_next_one(page, base_url, tmp_path):
    """Type the result, attach a file, save — the task is done, the chronology
    has it, and the next step is a separate deliberate act (docs/adr/0075 §3,
    §5)."""
    sign_in(page, base_url, MARTIN)
    url = create_matter(page, base_url, "Töölaua brauserikatse: täisring")
    set_step(page, "Vaadata uus eelnõu versioon üle")

    zone = page.locator("#praegune-tegevus")
    zone.locator(".composer__body").fill("Vaatasin versiooni üle ja tegin märkused.")
    zone.locator("input[type=file]").set_input_files(_pdf(tmp_path, "markused.pdf"))
    zone.locator("button[type=submit]").last.click()
    page.wait_for_load_state("networkidle")

    # The task is gone, its result is on the chronology, and the file is under
    # the row that carries it.
    expect(page.locator("#praegune-tegevus")).to_contain_text("Järgmine samm on määramata")
    expect(chronology(page)).to_contain_text("Vaatasin versiooni üle")
    expect(chronology(page).locator("a.uxtl__file", has_text="markused.pdf")).to_have_count(1)

    # `+ Järgmine tegevus` is now available, and nothing opened it for anybody.
    launcher = page.locator("#lisa-jargmine")
    expect(launcher).to_have_count(1)
    assert launcher.evaluate("node => node.open") is False

    set_step(page, "Saata arvamus ministeeriumile", 10)

    # And it survives a refresh.
    page.goto(url)
    page.wait_for_load_state("networkidle")
    expect(page.locator(".curact__text")).to_have_text("Saata arvamus ministeeriumile")
    expect(chronology(page)).to_contain_text("Vaatasin versiooni üle")
    expect(chronology(page).locator("a.uxtl__file", has_text="markused.pdf")).to_have_count(1)


def test_a_marge_while_a_task_is_open_leaves_the_task_alone(page, base_url):
    sign_in(page, base_url, MARTIN)
    create_matter(page, base_url, "Töölaua brauserikatse: märge")
    set_step(page, "Oodata ministeeriumi vastust")

    open_composer(page)
    page.locator("#lisa-marge .composer__body").fill("Ministeerium helistas reedel.")
    page.locator("#lisa-marge button[type=submit]").click()
    page.wait_for_load_state("networkidle")

    expect(chronology(page)).to_contain_text("Ministeerium helistas reedel")
    expect(page.locator(".curact__text")).to_have_text("Oodata ministeeriumi vastust")
    expect(page.locator("#praegune-tegevus").get_by_text("Mida tegid?", exact=True)).to_be_visible()


# ---------------------------------------------------------------------------
# One panel open at a time
# ---------------------------------------------------------------------------


PANELS = (
    "lisa-marge",
    "lisa-kaasamine",
    "lisa-tahtaeg",
    "lisa-joustumine",
    "lisa-toovoit",
    "lisa-lopeta",
)


def test_opening_one_panel_closes_whichever_was_open(page, base_url):
    sign_in(page, base_url, MARTIN)
    create_matter(page, base_url, "Töölaua brauserikatse: üks korraga")

    previous = ""
    for panel_id in PANELS:
        open_add_panel(page, panel_id)
        assert page.locator(f"#{panel_id}").evaluate("node => node.open") is True, panel_id
        if previous:
            assert page.locator(f"#{previous}").evaluate("node => node.open") is False, previous
        previous = panel_id


def test_each_panel_saves_its_own_record_and_nothing_else(page, base_url, tmp_path):
    """One operation per save, proved by what reaches the chronology."""
    sign_in(page, base_url, MARTIN)
    create_matter(page, base_url, "Töölaua brauserikatse: iga oma salvestus")

    open_add_panel(page, "lisa-toovoit")
    page.locator("#lisa-toovoit [name=victory_change]").fill("Üleminekuaeg pikendati")
    page.locator("#lisa-toovoit input[type=file]").set_input_files(
        [_pdf(tmp_path, "toend.pdf"), _pdf(tmp_path, "lisatoend.pdf", b"kaks")]
    )
    page.locator("#lisa-toovoit button[type=submit]").click()
    page.wait_for_load_state("networkidle")

    victory = chronology(page).locator(".uxtl__item", has_text="Töövõit").first
    expect(victory).to_be_visible()
    expect(victory).to_contain_text("Üleminekuaeg pikendati")
    # Both files are under that row, and neither drew a row of its own
    # (brief §25).
    expect(victory.locator("a.uxtl__file")).to_have_count(2)
    expect(chronology(page).get_by_text("lisas dokumendi")).to_have_count(0)
    # The Matter is still open: a win closes nothing.
    expect(page.locator(".badge--state")).to_contain_text("Avatud")

    open_add_panel(page, "lisa-joustumine")
    page.locator("#lisa-joustumine [name=effective_title]").fill("Pakendiseaduse muudatused")
    page.locator("#lisa-joustumine [name=effective_on]").fill(_future(-3))
    page.locator("#lisa-joustumine input[type=file]").set_input_files(
        _pdf(tmp_path, "seadus.pdf", b"kolm")
    )
    page.locator("#lisa-joustumine button[type=submit]").click()
    page.wait_for_load_state("networkidle")

    commencement = (
        chronology(page).locator(".uxtl__item", has_text="Pakendiseaduse muudatused").first
    )
    expect(commencement).to_be_visible()
    expect(commencement.locator("a.uxtl__file", has_text="seadus.pdf")).to_have_count(1)
    # And the earlier win kept its own two files rather than gaining a third.
    expect(
        chronology(page).locator(".uxtl__item", has_text="Töövõit").first.locator("a.uxtl__file")
    ).to_have_count(2)


def test_an_engagement_carries_its_replies_on_its_own_row(page, base_url, tmp_path):
    sign_in(page, base_url, MARTIN)
    create_matter(page, base_url, "Töölaua brauserikatse: kaasamine")

    open_add_panel(page, "lisa-kaasamine")
    page.locator("#lisa-kaasamine .uxchip", has_text="Kirjade voor").click()
    page.locator("#lisa-kaasamine [name=audience]").fill("liikmed")
    page.locator("#lisa-kaasamine [name=response_count]").fill("2")
    page.locator("#lisa-kaasamine input[type=file]").set_input_files(
        [_pdf(tmp_path, "vastus1.pdf"), _pdf(tmp_path, "vastus2.pdf", b"kaks")]
    )
    page.locator("#lisa-kaasamine button[type=submit]").click()
    page.wait_for_load_state("networkidle")

    row = chronology(page).locator(".uxtl__item", has_text="Kaasamine: liikmed").first
    expect(row).to_be_visible()
    expect(row).to_contain_text("Vastuseid 2")
    expect(row.locator("a.uxtl__file")).to_have_count(2)


# ---------------------------------------------------------------------------
# Refusals answer themselves
# ---------------------------------------------------------------------------


def test_a_refused_panel_reopens_itself_and_no_other(page, base_url):
    sign_in(page, base_url, MARTIN)
    create_matter(page, base_url, "Töölaua brauserikatse: keeldumine")

    open_add_panel(page, "lisa-toovoit")
    page.locator("#lisa-toovoit button[type=submit]").click()
    page.wait_for_load_state("networkidle")

    expect(page.locator("#lisa-toovoit")).to_have_attribute("open", "")
    expect(page.locator("#lisa-toovoit")).to_contain_text("Kirjuta, mis muutus")
    for other in ("lisa-marge", "lisa-kaasamine", "lisa-tahtaeg", "lisa-lopeta"):
        assert page.locator(f"#{other}").evaluate("node => node.open") is False, other


def test_a_blank_result_is_refused_beside_the_field_it_belongs_to(page, base_url):
    sign_in(page, base_url, MARTIN)
    create_matter(page, base_url, "Töölaua brauserikatse: tühi tulemus")
    set_step(page, "Saata kiri ministeeriumile")

    page.locator("#praegune-tegevus button[type=submit]").last.click()
    page.wait_for_load_state("networkidle")

    expect(page.locator("#praegune-tegevus")).to_contain_text("Kirjelda, mida tegid.")
    expect(page.locator(".curact__text")).to_have_text("Saata kiri ministeeriumile")


# ---------------------------------------------------------------------------
# Back, refresh, and a quiet console
# ---------------------------------------------------------------------------


def test_back_and_refresh_leave_the_workspace_correct(page, base_url):
    sign_in(page, base_url, MARTIN)
    url = create_matter(page, base_url, "Töölaua brauserikatse: tagasi")
    set_step(page, "Helistada ministeeriumisse")
    finish_current_action(page, "Helistasin ja sain vastuse.")

    page.goto(f"{base_url}/teemad/")
    page.wait_for_load_state("networkidle")
    page.go_back()
    page.wait_for_load_state("networkidle")
    page.goto(url)
    page.wait_for_load_state("networkidle")

    expect(page.locator("#praegune-tegevus")).to_contain_text("Järgmine samm on määramata")
    expect(chronology(page)).to_contain_text("Helistasin ja sain vastuse")


def test_the_workspace_writes_nothing_to_the_console(page, base_url):
    problems: list[str] = []
    page.on(
        "console",
        lambda message: (
            problems.append(message.text) if message.type in {"error", "warning"} else None
        ),
    )
    page.on("pageerror", lambda error: problems.append(str(error)))

    sign_in(page, base_url, MARTIN)
    create_matter(page, base_url, "Töölaua brauserikatse: konsool")
    for panel_id in PANELS:
        open_add_panel(page, panel_id)
    set_step(page, "Kontrollida ministeeriumi vastust")
    finish_current_action(page, "Kontrollisin; vastust ei ole.")

    assert not problems, f"the workspace logged: {problems}"


# ---------------------------------------------------------------------------
# Responsive
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("width", (1440, 1024, 420))
def test_the_workspace_is_operable_at_every_width(page, base_url, width):
    sign_in(page, base_url, MARTIN)
    page.set_viewport_size({"width": width, "height": 900})
    create_matter(page, base_url, f"Töölaua brauserikatse: {width}px")
    set_step(page, "Vaadata pikk ja põhjalik eelnõu versioon veel korra üle")

    assert not page.evaluate(
        "() => document.documentElement.scrollWidth > document.documentElement.clientWidth + 1"
    ), f"the Teema page scrolls sideways at {width}px"

    zone = page.locator("#praegune-tegevus")
    expect(zone.locator(".curact__text")).to_be_visible()
    for control in (".composer__body", ".cx-drop", ".curact__form button[type=submit]"):
        box = zone.locator(control).bounding_box()
        assert box is not None and box["width"] > 0, control
        assert box["x"] + box["width"] <= width + 1, control

    # The launcher wraps rather than pushing the page wider, and an opened
    # mini-form stays inside the viewport (brief §38).
    open_add_panel(page, "lisa-tahtaeg")
    assert not page.evaluate(
        "() => document.documentElement.scrollWidth > document.documentElement.clientWidth + 1"
    ), f"an open panel makes the page scroll sideways at {width}px"
    body = page.locator("#lisa-tahtaeg .cx-panel__body").bounding_box()
    assert body["x"] >= -1 and body["x"] + body["width"] <= width + 1
