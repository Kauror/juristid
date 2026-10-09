"""Closing a Teema, in a real browser — through `Hetkeseis` (docs/adr/0131 §10–§12).

The domain suite proves the rules (`tests/test_stage_episodes.py`); this proves
that a person can perform them.

**There is no `+ Lõpeta teema` any more.** A file ends when its `Hetkeseis`
says so: «Jõustunud» or «Rohkem ei tegele», chosen in the header's `Hetkeseis`
or as `Uus hetkeseis` in `✓ Tehtud` — where the option says so in its own words,
«… — lõpetab teema». «Jõustumise ootel» does not end it. (`Uus hetkeseis` was
also in `+ Lisa · Tavaline` until that panel left on 2026-10-07.) A closed file
is reopened into a stage the person names, and the stage it ended in stays in
`Teema käik` as history.

What only a browser can show: that the choice is in the form, that one
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
    open_done_form,
    sign_in,
    start_first_step,
)

pytestmark = pytest.mark.e2e


def finish_step_moving_stage(page, result: str, label: str) -> None:
    """`✓ Tehtud` with `Uus hetkeseis` — the step done, and the file moved, in one save."""
    open_done_form(page)
    page.locator("#id_praegune_body").fill(result)
    page.select_option("#id_praegune_hetkeseis", label=label)
    page.locator("#praegune-tegevus-vorm button[type=submit]").click()
    page.wait_for_load_state("networkidle")


def test_there_is_no_closing_panel_and_the_stage_says_what_ends_the_file(page, base_url):
    sign_in(page, base_url, MARTIN)
    create_matter(page, base_url, "Lõpetamise brauserikatse: valikud", stage="Riigikogus")

    expect(page.locator("#teema-lopeta")).to_have_count(0)
    expect(page.get_by_text("+ Lõpeta teema", exact=True)).to_have_count(0)
    # `✓ Tehtud`'s `Uus hetkeseis` — the one `Uus hetkeseis` on the page since
    # `+ Lisa · Tavaline`, which offered the same list, left on 2026-10-07.
    start_first_step(page)
    open_done_form(page)
    options = [
        text.strip() for text in page.locator("#id_praegune_hetkeseis option").all_inner_texts()
    ]
    assert "Jõustunud — lõpetab teema" in options
    assert "Rohkem ei tegele — lõpetab teema" in options
    assert "Jõustumise ootel" in options


def test_joustumise_ootel_keeps_the_file_open_and_joustunud_closes_it(page, base_url):
    """Flow E."""
    sign_in(page, base_url, MARTIN)
    create_matter(page, base_url, "Lõpetamise brauserikatse: jõustumine", stage="Riigikogus")

    start_first_step(page)
    finish_step_moving_stage(page, "Riigikogu võttis seaduse vastu.", "Jõustumise ootel")
    expect(page.locator(".badge--state")).to_contain_text("Aktiivne")
    expect(page.locator("#teema-hetkeseis")).to_contain_text("Jõustumise ootel")
    expect(page.locator("#lisa-teemale")).to_have_count(1)

    close_through_stage(page, "Jõustunud")
    expect(page.locator(".badge--state")).to_contain_text("Mitteaktiivne")
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
    close_through_stage(page, "Jõustunud")
    expect(page.locator(".badge--state")).to_contain_text("Mitteaktiivne")

    banner = page.locator(".banner--closed")
    banner.locator("select[name=stage]").select_option(label="Idee")
    banner.get_by_role("button", name="Ava uuesti").click()
    page.wait_for_load_state("networkidle")

    expect(page.locator(".badge--state")).to_contain_text("Aktiivne")
    stages = page.locator(f"{KAIK_PERIOD} .kaikstage__stage").all_inner_texts()
    assert stages[:3] == ["Idee", "Jõustunud", "Riigikogus"], stages
    expect(page.locator(f"{KAIK_PERIOD}.kaikstage--current .kaikstage__stage")).to_have_text("Idee")
