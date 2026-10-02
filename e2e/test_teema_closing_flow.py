"""Closing a Teema, in a real browser — through `Hetkeseis` (docs/adr/0131 §10–§12).

The domain suite proves the rules (`tests/test_stage_episodes.py`); this proves
that a person can perform them.

**There is no `+ Lõpeta teema` any more.** A file ends when its `Hetkeseis`
says so: «Jõustunud» or «Rohkem ei tegele», chosen as `Uus hetkeseis` in
`+ Märge`, and the option says so in its own words — «… — lõpetab teema».
«Jõustumise ootel» does not end it. A closed file is reopened into a stage the
person names, and the stage it ended in stays in `Teema käik` as history.

What only a browser can show: that the choice is in the panel, that one
`Salvesta` closes the file and moves the header, the banner and the workspace
together, and that reopening opens a new period with the old one kept.

Everything here is synthetic.
"""

from __future__ import annotations

import pytest
from playwright.sync_api import expect

from e2e.conftest import (
    KAIK_PERIOD,
    MARTIN,
    close_through_stage,
    create_matter,
    open_composer,
    sign_in,
)

pytestmark = pytest.mark.e2e


def move_stage(page, label: str, title: str = "") -> None:
    open_composer(page)
    if title:
        page.fill("#id_marge_title", title)
    page.select_option("#id_marge_stage", label=label)
    page.locator("#marge-tavaline button[type=submit]").click()
    page.wait_for_load_state("networkidle")


def test_there_is_no_closing_panel_and_the_stage_says_what_ends_the_file(page, base_url):
    sign_in(page, base_url, MARTIN)
    create_matter(page, base_url, "Lõpetamise brauserikatse: valikud", stage="Riigikogus")

    expect(page.locator("#teema-lopeta")).to_have_count(0)
    expect(page.get_by_text("+ Lõpeta teema", exact=True)).to_have_count(0)
    open_composer(page)
    options = page.locator("#id_marge_stage option").all_inner_texts()
    assert "Jõustunud — lõpetab teema" in options
    assert "Rohkem ei tegele — lõpetab teema" in options
    assert "Jõustumise ootel" in options


def test_joustumise_ootel_keeps_the_file_open_and_joustunud_closes_it(page, base_url):
    """Flow E."""
    sign_in(page, base_url, MARTIN)
    create_matter(page, base_url, "Lõpetamise brauserikatse: jõustumine", stage="Riigikogus")

    move_stage(page, "Jõustumise ootel", title="Riigikogu võttis seaduse vastu.")
    expect(page.locator(".badge--state")).to_contain_text("Avatud")
    expect(page.locator("#lisa-teemale")).to_have_count(1)

    move_stage(page, "Jõustunud — lõpetab teema")
    expect(page.locator(".badge--state")).to_contain_text("Suletud")
    expect(page.locator(".banner--closed")).to_contain_text("Lõpetatud või jõustunud")
    expect(page.locator("#lisa-teemale")).to_have_count(0)
    # The work from before stays in its period.
    expect(page.locator("#ajalugu-loend")).to_contain_text("Riigikogu võttis seaduse vastu.")


def test_rohkem_ei_tegele_closes_with_koda_stopping(page, base_url):
    """Flow F."""
    sign_in(page, base_url, MARTIN)
    create_matter(page, base_url, "Lõpetamise brauserikatse: ei tegele", stage="Idee")

    close_through_stage(page, "Rohkem ei tegele", title="Koda otsustas mitte sekkuda.")

    expect(page.locator(".banner--closed")).to_contain_text("Koda lõpetas jälgimise")
    current = page.locator(f"{KAIK_PERIOD}.kaikstage--current")
    expect(current.locator(".kaikstage__stage")).to_have_text("Rohkem ei tegele")


def test_reopening_names_the_stage_and_keeps_the_ended_period(page, base_url):
    """Flow G."""
    sign_in(page, base_url, MARTIN)
    create_matter(page, base_url, "Lõpetamise brauserikatse: taasavamine", stage="Riigikogus")
    move_stage(page, "Jõustunud — lõpetab teema")
    expect(page.locator(".badge--state")).to_contain_text("Suletud")

    banner = page.locator(".banner--closed")
    banner.locator("select[name=stage]").select_option(label="Idee")
    banner.get_by_role("button", name="Ava uuesti").click()
    page.wait_for_load_state("networkidle")

    expect(page.locator(".badge--state")).to_contain_text("Avatud")
    stages = page.locator(f"{KAIK_PERIOD} .kaikstage__stage").all_inner_texts()
    assert stages[:3] == ["Idee", "Jõustunud", "Riigikogus"], stages
    expect(page.locator(f"{KAIK_PERIOD}.kaikstage--current .kaikstage__stage")).to_have_text("Idee")
