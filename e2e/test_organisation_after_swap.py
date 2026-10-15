"""A new organisation added through `+` survives the save — after an htmx swap too.

The bug (owner's round, 2026-10-08): a lawyer types a body the catalogue does
not hold, presses `+`, sees it as a blue chip, saves — and the chip vanishes
with «Vali …». It only happened once the panel had arrived through an htmx
swap: DOMParser parses `<noscript>` with scripting off, so the picker's
fallback text box became a live, empty control posted after the hidden
carrier, and the server read the empty one (docs/adr/0144 §4).

Each journey here first makes the Teema column re-render through htmx, then
adds a new organisation, saves, reloads and finds it on the record. An existing
organisation is still chosen the ordinary way.
"""

from __future__ import annotations

import pytest
from playwright.sync_api import expect

from e2e.conftest import (
    AVALDATUD_MUJAL,
    FEEDBACK_PICKER,
    SANDRA,
    choose_organisation,
    chronology,
    create_matter,
    feedback_form,
    open_add_panel,
    open_feedback_form,
    sign_in,
    start_first_step,
    wait_for_htmx,
)
from e2e.titles import unique_title

pytestmark = pytest.mark.e2e

PDF = b"%PDF-1.4 synthetic opinion"


def _swapped_teema(page, base_url: str) -> str:
    """A Teema whose column has already been re-rendered by an htmx save."""
    sign_in(page, base_url, SANDRA)
    url = create_matter(page, base_url, unique_title("Uus organisatsioon pärast vahetust"))
    start_first_step(page, text="Loe eelnõu läbi")
    wait_for_htmx(page)
    return url


def _add_new(picker, name: str) -> None:
    picker.locator("[data-orgfind-input]").fill(name)
    picker.locator("[data-orgfind-add]").click()
    expect(picker.locator(".orgfind__chips")).to_contain_text(name)


def test_a_new_organisation_in_lisa_tagasiside_survives_the_save(page, base_url):
    """«Teiste arvamus» is `Lisa tagasiside` · «Avaldatud mujal» since docs/adr/0170."""
    url = _swapped_teema(page, base_url)
    name = unique_title("QA Uus Liit")

    panel = open_feedback_form(page, AVALDATUD_MUJAL)
    _add_new(panel.locator("[data-orgfind]").first, name)
    panel.locator("[name=summary]").fill("Liidu seisukoht eelnõule.")
    with page.expect_response(lambda r: r.request.method == "POST") as caught:
        panel.get_by_role("button", name="Salvesta", exact=True).click()
    assert caught.value.status == 200, caught.value.status
    wait_for_htmx(page)

    page.goto(url)
    page.wait_for_load_state("networkidle")
    expect(chronology(page)).to_contain_text(name)


def test_a_new_addressee_in_koja_arvamus_survives_the_save(page, base_url):
    url = _swapped_teema(page, base_url)
    name = unique_title("QA Komisjon")

    open_add_panel(page, "arvamus-koja")
    form = page.locator("#arvamus-koja")
    form.locator("input[name=upload]").set_input_files(
        {"name": "arvamus.pdf", "mimeType": "application/pdf", "buffer": PDF}
    )
    _add_new(form.locator("[data-orgfind]").first, name)
    with page.expect_response(
        lambda r: r.url.endswith("/lisa/koja-arvamus/") and r.request.method == "POST"
    ) as caught:
        form.get_by_role("button", name="Salvesta", exact=True).click()
    assert caught.value.status == 200, caught.value.status
    wait_for_htmx(page)

    page.goto(url)
    page.wait_for_load_state("networkidle")
    expect(page.locator("#koja-arvamus")).to_contain_text(name)


def test_a_second_save_after_a_refusal_still_keeps_the_new_organisation(page, base_url):
    """A refused save re-renders the panel through htmx, and the chip must hold."""
    url = _swapped_teema(page, base_url)
    name = unique_title("QA Teine Liit")

    panel = open_feedback_form(page, AVALDATUD_MUJAL)
    _add_new(panel.locator("[data-orgfind]").first, name)
    # Refused: no summary yet.
    with page.expect_response(lambda r: r.request.method == "POST") as caught:
        panel.get_by_role("button", name="Salvesta", exact=True).click()
    assert caught.value.status == 400, caught.value.status
    wait_for_htmx(page)
    panel = feedback_form(page)
    expect(panel.locator(".orgfind__chips")).to_contain_text(name)

    panel.locator("[name=summary]").fill("Teine katse.")
    panel.get_by_role("button", name="Salvesta", exact=True).click()
    wait_for_htmx(page)

    page.goto(url)
    page.wait_for_load_state("networkidle")
    expect(chronology(page)).to_contain_text(name)


def test_an_existing_organisation_is_still_chosen_from_the_list(page, base_url):
    url = _swapped_teema(page, base_url)

    panel = open_feedback_form(page, AVALDATUD_MUJAL)
    choose_organisation(page, FEEDBACK_PICKER)
    panel.locator("[name=summary]").fill("Ministeeriumi seisukoht.")
    panel.get_by_role("button", name="Salvesta", exact=True).click()
    wait_for_htmx(page)

    page.goto(url)
    page.wait_for_load_state("networkidle")
    expect(chronology(page)).to_contain_text("Näidisministeerium")
