"""`Uus teema` as a lighter intake, with `Õigusakt -> Hetkeseis` guidance (docs/adr/0130).

What only a running page can settle:

* **A** — the new order, measured by where things paint; no `Millest teema
  räägib`, no `Nimetus`; every `Hetkeseis` chip normal before anything is ticked;
* **B** — `Seadus` dims the EU-only stages and leaves the domestic ones normal;
  a dimmed `ELi menetluses` is still clickable, looks chosen once chosen, saves,
  and is exactly what the Teema then holds;
* **C** — `ELi konsultatsioon` dims the domestic stages;
* **D** — one instrument at a time: choosing another re-dims at once, and a
  historical pair (planted, as no page can make one now) still guides by
  **union** on `Muuda teemat`;
* **E** — changing the instruments re-dims immediately and never clears the
  chosen stage;
* **G** — the dimming is not colour alone: a dotted edge, and one sentence in
  the explanation the radio is described by — chosen or not;
* **F** — one realistic Teema through every section, files included — a signed
  container among them, so moving `Failid` cannot quietly regress
  docs/adr/0125.

The matrix itself is `tests/test_stage_guidance.py`; the server's acceptance of
every combination is `tests/test_uus_teema_guided_intake.py`. Every Teema here
is synthetic and filed by this file, and each is given its first step so it
does not join the «järgmise tegevuseta» list other files read.
"""

from __future__ import annotations

import re

import pytest
from playwright.sync_api import expect

from app.workflow.stage_guidance import ATYPICAL_STAGE_NOTE
from e2e.conftest import (
    MARTIN,
    give_first_step,
    plant_historical_instruments,
    sign_in,
    start_first_step,
    unique_title,
)
from tests.synthetic_containers import signed_container

pytestmark = pytest.mark.e2e

PDF = b"%PDF-1.4\n1 0 obj<</Type/Catalog>>endobj\ntrailer<</Root 1 0 R>>\n%%EOF\n"
EIS_URL = "https://eelnoud.valitsus.ee/main/mount/docList/qa-0130"

STAGE_KEYS = (
    "idea",
    "consultation",
    "government",
    "parliament",
    "awaiting_entry",
    "in_force",
    "estonian_eu_position",
    "eu_procedure",
    "awaiting_transposition",
    "other",
)


def _open(page, base_url: str) -> None:
    sign_in(page, base_url, MARTIN)
    page.goto(f"{base_url}/teemad/uus/")
    page.wait_for_load_state("networkidle")


def _dimmed(page) -> set[str]:
    """The stage keys whose chip is drawn dimmed right now."""
    return set(
        page.evaluate(
            """() => Array.from(document.querySelectorAll('input[name="stage"][data-stage-key]'))
                .filter(i => i.closest('.chip').classList.contains('chip--atypical'))
                .map(i => i.getAttribute('data-stage-key'))"""
        )
    )


def _instrument(page, name: str):
    return page.get_by_role("radio", name=name, exact=True)


def _no_instrument(page):
    """«Määramata» in the `Õigusakt` row — the radio that takes the answer back."""
    return page.locator('input[name="legal_instruments"][value=""]')


def _stage(page, name: str):
    return page.get_by_role("radio", name=name, exact=True)


def _chosen_stage(page) -> str:
    return page.evaluate(
        """() => { const i = document.querySelector('input[name="stage"]:checked');
                   return i ? (i.getAttribute('data-stage-key') || '') : null; }"""
    )


def _name_colour(page, stage_name: str) -> str:
    return _stage(page, stage_name).evaluate(
        "i => getComputedStyle(i.closest('.chip').querySelector('.chip__name')).color"
    )


# ---------------------------------------------------------------------------
# A — the page
# ---------------------------------------------------------------------------


def test_a_the_order_and_what_is_no_longer_asked(page, base_url, screenshots):
    _open(page, base_url)

    def box(selector: str) -> dict:
        found = page.locator(selector).first.bounding_box()
        assert found is not None, selector
        return found

    title = box("#id_title")
    sender = box("#saatja-otsi")
    owner = box('input[name="owner"]')
    deadline = box("#id_response_deadline")
    link = box('input[name="menetlus-url"]')
    received = box("#id_received_date")
    oigusakt = box('input[name="legal_instruments"]')
    valdkonnad = box('input[name="policy_areas"]')
    hetkeseis = box('input[name="stage"]')
    notes = box("#id_notes")
    files = box("#failid")
    button = box(".createform__actions")

    # Saatja left, Vastutaja right, on one row.
    assert sender["x"] < owner["x"]
    assert abs(sender["y"] - owner["y"]) < 40, (sender, owner)
    # Tähtaeg | Menetluse link | Saabus, on one row, the link the widest.
    assert deadline["x"] < link["x"] < received["x"]
    assert abs(deadline["y"] - link["y"]) < 10 and abs(link["y"] - received["y"]) < 10
    assert link["width"] > deadline["width"] and link["width"] > received["width"]
    # And down the page, in the owner's order.
    # Hetkeseis directly under the Õigusakt that guides it, then Valdkond
    # (docs/adr/0130, amendment of 2026-10-02).
    rows = [title, sender, deadline, oigusakt, hetkeseis, valdkonnad, notes, files, button]
    tops = [row["y"] for row in rows]
    assert tops == sorted(tops), tops

    expect(page.get_by_text("Millest teema räägib")).to_have_count(0)
    expect(page.locator('[name="brief_summary"]')).to_have_count(0)
    expect(page.locator('[name="menetlus-label"]')).to_have_count(0)
    expect(page.locator("#menetluse-link")).not_to_contain_text("Nimetus")
    expect(page.get_by_label("Menetluse link")).to_be_visible()

    # «Valdkond», in the singular, directly after Hetkeseis — and still a
    # group of checkboxes (docs/adr/0130, amendment of 2026-10-02).
    valdkond = page.locator('fieldset:has(input[name="policy_areas"]) > legend')
    assert (valdkond.text_content() or "").split()[0] == "Valdkond"
    expect(page.get_by_text("Valdkonnad", exact=True)).to_have_count(0)
    assert page.locator('input[name="policy_areas"][type="checkbox"]').count() > 1

    # Nothing ticked: nothing dimmed.
    assert _dimmed(page) == set()
    screenshots(page, "uus-teema-guided-default")


# ---------------------------------------------------------------------------
# B — a domestic instrument, and a deliberately atypical stage
# ---------------------------------------------------------------------------


def test_b_seadus_dims_the_eu_stages_and_a_dimmed_stage_still_saves(page, base_url, screenshots):
    _open(page, base_url)
    title = unique_title("Juhis Seadus ELi menetluses")
    page.fill("#id_title", title)
    # `Määramata` arrives chosen: the colour a chosen chip has.
    expect(_stage(page, "Määramata")).to_be_checked()
    chosen_colour = _name_colour(page, "Määramata")

    _instrument(page, "Seadus").check()

    assert _dimmed(page) == {"estonian_eu_position", "eu_procedure", "awaiting_transposition"}
    for normal in ("Kooskõlastusringil", "Valitsuses", "Riigikogus"):
        expect(_stage(page, normal)).to_be_enabled()
    normal_colour = _name_colour(page, "Riigikogus")
    dim_colour = _name_colour(page, "ELi menetluses")
    assert normal_colour != dim_colour
    # The dimmed words are `--text-atypical`, the token the 2026-10-02
    # amendment introduced — quieter than `--text-muted`, and AA on the page
    # since docs/adr/0147's amendment of 2026-10-09.
    assert dim_colour == page.evaluate(
        """() => { const probe = document.createElement('span');
                   probe.style.color = 'var(--text-atypical)';
                   document.body.appendChild(probe);
                   const c = getComputedStyle(probe).color; probe.remove(); return c; }"""
    )
    screenshots(page, "uus-teema-guided-seadus")

    # Dimmed is not disabled: it is in the tab order and it takes a click.
    atypical = _stage(page, "ELi menetluses")
    expect(atypical).to_be_enabled()
    assert atypical.get_attribute("tabindex") in (None, "0")
    atypical.check()
    expect(atypical).to_be_checked()
    # The ordinary chosen look wins over dimming.
    assert _name_colour(page, "ELi menetluses") == chosen_colour
    assert _name_colour(page, "ELi menetluses") != dim_colour
    expect(page.locator(".field__error")).to_have_count(0)

    give_first_step(page)
    page.get_by_role("button", name="Salvesta", exact=True).click()
    page.wait_for_url(re.compile(r"/teemad/[0-9a-f-]{36}/$"))
    start_first_step(page)
    assert not page.locator(".field__error, .formerror").all_inner_texts()

    # Stored exactly as chosen: the correction page holds the same answers.
    page.goto(page.url + "muuda/")
    page.wait_for_load_state("networkidle")
    expect(_stage(page, "ELi menetluses")).to_be_checked()
    expect(_instrument(page, "Seadus")).to_be_checked()


# ---------------------------------------------------------------------------
# C — a European instrument
# ---------------------------------------------------------------------------


def test_c_an_eu_consultation_dims_the_domestic_stages(page, base_url, screenshots):
    _open(page, base_url)

    _instrument(page, "ELi konsultatsioon").check()

    dimmed = _dimmed(page)
    assert "estonian_eu_position" not in dimmed
    assert "eu_procedure" not in dimmed
    assert "parliament" in dimmed
    assert dimmed == set(STAGE_KEYS) - {"estonian_eu_position", "eu_procedure", "other"}
    screenshots(page, "uus-teema-guided-eli-konsultatsioon")


# ---------------------------------------------------------------------------
# D — union
# ---------------------------------------------------------------------------


def test_d_one_instrument_at_a_time_and_a_historical_pair_by_union(page, base_url):
    _open(page, base_url)

    _instrument(page, "ELi direktiiv").check()
    assert "parliament" in _dimmed(page)
    assert "awaiting_transposition" not in _dimmed(page)

    # Choosing `Seadus` replaces the directive: the EU stages dim instead.
    _instrument(page, "Seadus").check()
    expect(_instrument(page, "ELi direktiiv")).not_to_be_checked()
    dimmed = _dimmed(page)
    assert "parliament" not in dimmed
    assert {"eu_procedure", "awaiting_transposition"} <= dimmed

    # A Matter filed before the rule with both keeps them, and «Jäta alles»
    # guides by their union on `Muuda teemat` (docs/adr/0070, 0130).
    page.fill("#id_title", unique_title("Juhis ajalooline paar"))
    give_first_step(page)
    page.get_by_role("button", name="Salvesta", exact=True).click()
    page.wait_for_url(re.compile(r"/teemad/[0-9a-f-]{36}/$"))
    start_first_step(page)
    plant_historical_instruments(page, ["ELi direktiiv", "Seadus"])
    page.goto(page.url + "muuda/")
    page.wait_for_load_state("networkidle")
    expect(page.locator('input[name="legal_instruments"][value="jata-alles"]')).to_be_checked()
    dimmed = _dimmed(page)
    for normal in ("eu_procedure", "awaiting_transposition", "parliament"):
        assert normal not in dimmed, (normal, dimmed)


# ---------------------------------------------------------------------------
# E — changing the instruments never clears the stage
# ---------------------------------------------------------------------------


def test_e_changing_the_instruments_redims_and_keeps_the_choice(page, base_url):
    _open(page, base_url)

    _instrument(page, "Seadus").check()
    _stage(page, "Riigikogus").check()
    assert "parliament" not in _dimmed(page)

    # Swap the instrument: Riigikogus becomes atypical, and stays chosen.
    _no_instrument(page).check()
    assert _dimmed(page) == set()
    _instrument(page, "ELi määrus").check()
    assert "parliament" in _dimmed(page)
    assert "awaiting_transposition" in _dimmed(page)
    assert _chosen_stage(page) == "parliament"
    expect(_stage(page, "Riigikogus")).to_be_checked()

    # And back to nothing chosen: nothing dimmed, choice unchanged.
    _no_instrument(page).check()
    assert _dimmed(page) == set()
    assert _chosen_stage(page) == "parliament"

    # Valdkonnad never change appearance because of an Õigusakt.
    _instrument(page, "VTK").check()
    extra = page.evaluate(
        """() => Array.from(document.querySelectorAll('input[name="policy_areas"]'))
            .filter(i => i.closest('.chip').classList.contains('chip--atypical')).length"""
    )
    assert extra == 0


# ---------------------------------------------------------------------------
# G — not colour alone
# ---------------------------------------------------------------------------


def _edge(page, stage_name: str) -> str:
    return _stage(page, stage_name).evaluate(
        "i => getComputedStyle(i.closest('.chip').querySelector('.chip__name')).borderTopStyle"
    )


def _description(page, stage_name: str) -> str:
    """What a screen reader reads after the radio's name."""
    return _stage(page, stage_name).evaluate(
        "i => document.getElementById(i.getAttribute('aria-describedby')).textContent.trim()"
    )


def test_g_a_dimmed_stage_says_so_without_colour(page, base_url):
    """docs/adr/0130, amendment of 2026-10-09 («not colour alone»).

    The words got brighter (AA with a margin), so the difference a glance needs
    is carried by the outline's line style; and a keyboard or screen-reader user,
    who never sees either, hears it in the explanation. A chosen atypical stage
    keeps both, so a deliberate exception stays recognisable without being
    refused.
    """
    _open(page, base_url)
    assert _edge(page, "Riigikogus") == "solid"
    assert ATYPICAL_STAGE_NOTE not in _description(page, "Riigikogus")

    _instrument(page, "ELi määrus").check()
    assert "parliament" in _dimmed(page)
    assert _edge(page, "Riigikogus") == "dotted"
    assert _description(page, "Riigikogus").endswith(ATYPICAL_STAGE_NOTE)
    # A stage that fits keeps its solid edge and its plain explanation.
    assert _edge(page, "Jõustunud") == "solid"
    assert ATYPICAL_STAGE_NOTE not in _description(page, "Jõustunud")

    # Chosen anyway: the chosen look, the dotted edge, and the words, which the
    # bubble shows on focus.
    _stage(page, "Riigikogus").check()
    expect(_stage(page, "Riigikogus")).to_be_checked()
    assert _edge(page, "Riigikogus") == "dotted"
    _stage(page, "Riigikogus").focus()
    bubble = page.locator("#" + _stage(page, "Riigikogus").get_attribute("aria-describedby"))
    expect(bubble).to_be_visible()
    expect(bubble.locator(".stagehelp__note")).to_have_text(ATYPICAL_STAGE_NOTE)

    # Nothing chosen again: both cues go, the choice stays.
    _no_instrument(page).check()
    assert _edge(page, "Riigikogus") == "solid"
    assert ATYPICAL_STAGE_NOTE not in _description(page, "Riigikogus")
    expect(_stage(page, "Riigikogus")).to_be_checked()


# ---------------------------------------------------------------------------
# F — one realistic Teema through every section
# ---------------------------------------------------------------------------


def test_f_a_full_creation_with_files(page, base_url, screenshots):
    _open(page, base_url)
    title = unique_title("QA juhitud sisestus täielik")
    page.fill("#id_title", title)

    sender = page.locator("#saatja-otsi")
    sender.click()
    sender.type("Näidismin", delay=20)
    page.locator("#saatja-tulemused").get_by_role("option").first.click()
    page.get_by_role("radio", name=MARTIN.short_name, exact=True).check()

    give_first_step(page, days=14)
    page.fill('input[name="menetlus-url"]', EIS_URL)
    _instrument(page, "ELi direktiiv").check()
    # Several Valdkond values, as before — the heading is singular, the field
    # is not.
    page.locator('input[name="policy_areas"]').nth(0).check()
    page.locator('input[name="policy_areas"]').nth(1).check()
    expect(page.locator('[data-chipcount-for="policy_areas"]')).to_contain_text("2")
    _stage(page, "ELi menetluses").check()
    page.fill("#id_notes", "Sünteetiline QA märge.")
    page.locator("#id_files").set_input_files(
        [
            {"name": "QA kaaskiri.pdf", "mimeType": "application/pdf", "buffer": PDF},
            {
                "name": "QA pakett.asice",
                "mimeType": "application/octet-stream",
                "buffer": signed_container(deflated_mimetype=True),
            },
        ]
    )
    expect(page.locator("#intake-failid .dropzone__file")).to_have_count(2)
    screenshots(page, "uus-teema-guided-full")

    page.get_by_role("button", name="Salvesta", exact=True).click()
    page.wait_for_url(re.compile(r"/teemad/[0-9a-f-]{36}/$"))
    start_first_step(page)
    assert not page.locator(".field__error, .formerror").all_inner_texts()

    body = page.locator("main").inner_text()
    assert title in body
    expect(page.locator(f'a[href="{EIS_URL}"]').first).to_be_attached()

    matter_url = page.url
    page.get_by_role("link", name=re.compile(r"^Dokumendid")).click()
    page.wait_for_load_state("networkidle")
    expect(page.get_by_role("link", name="QA kaaskiri.pdf", exact=True)).to_be_visible()
    expect(page.get_by_role("link", name="QA pakett.asice", exact=True)).to_be_visible()

    # Both Valdkond values were stored, and the atypical stage as chosen.
    page.goto(matter_url + "muuda/")
    page.wait_for_load_state("networkidle")
    expect(page.locator('input[name="policy_areas"]:checked')).to_have_count(2)
    expect(_stage(page, "ELi menetluses")).to_be_checked()
