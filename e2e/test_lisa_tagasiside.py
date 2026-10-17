"""`+ Kaasamine · Lisa tagasiside` in a real browser (docs/adr/0170).

What only a running page can settle:

* the old choices are gone from `+ Arvamus / tagasiside`, which keeps only
  `Koja arvamus`;
* «Saadeti Kojale» and «Avaldatud mujal» each save through HTMX and put the
  position on the chronology, with no provenance chosen in advance;
* `Liige` is shown only beside «Saadeti Kojale», and leaving that answer clears
  it;
* choosing a `Seotud kaasamine` checks «Saadeti Kojale» and disables
  «Avaldatud mujal»; clearing the round gives the choice back; a linked first
  answer ends the round's wait, a standalone one does not;
* received feedback may name nobody (docs/adr/0101); a published position
  must name its organisation;
* any one of text, link or file is a complete record (docs/adr/0084 §3), and
  an entirely empty one is refused with the source sentence;
* a refused save keeps what was typed and says what is missing;
* files go with the position.

Every test writes to a Matter it creates.
"""

from __future__ import annotations

import pytest
from playwright.sync_api import expect

from e2e.conftest import (
    AVALDATUD_MUJAL,
    FEEDBACK_PICKER,
    SAADETI_KOJALE,
    SANDRA,
    add_panel_chip,
    choose_organisation,
    chronology,
    create_matter,
    feedback_form,
    open_add_panel,
    open_feedback_form,
    sign_in,
    unique_title,
    wait_for_htmx,
)

pytestmark = pytest.mark.e2e


def _provenance(form, value: str):
    return form.locator(f'input[name="provenance"][value="{value}"]')


def _member(form):
    return form.locator("[data-feedback-member]")


def _save(page, form) -> None:
    with page.expect_response(lambda r: "/lisa/kaasamise-tagasiside/" in r.url):
        form.get_by_role("button", name="Salvesta", exact=True).click()
    wait_for_htmx(page)


def _start_round(page, audience: str = "Liikmed (e-kiri)") -> None:
    open_add_panel(page, "kaasamine-alusta")
    round_form = page.locator("#kaasamine-alusta")
    round_form.locator("[name=audience]").fill(audience)
    round_form.locator("button[type=submit]").click()
    page.wait_for_load_state("networkidle")
    expect(page.locator("#praegune-ootused")).to_contain_text("Ootame tagasisidet")


@pytest.fixture
def matter(page, base_url):
    sign_in(page, base_url, SANDRA)
    create_matter(page, base_url, unique_title("Lisa tagasiside"))


def test_the_opinion_family_keeps_only_koja_arvamus(page, matter):
    open_add_panel(page, "arvamus-koja")

    family = page.locator("#lisa-arvamus")
    # Two since stage 4: `Koja arvamus` and `Kohtumine / arutelu` (docs/adr/0097,
    # amendment of 2026-10-17). Neither is feedback.
    expect(family.locator("label.disclosure-chip")).to_have_count(2)
    expect(add_panel_chip(page, "arvamus-koja")).to_be_visible()
    expect(add_panel_chip(page, "arvamus-kohtumine")).to_be_visible()
    expect(page.locator("#arvamus-tagasiside, #arvamus-teiste")).to_have_count(0)
    expect(page.get_by_text("Meile saadetud tagasiside", exact=True)).to_have_count(0)
    expect(page.get_by_text("Teiste arvamus", exact=True)).to_have_count(0)


def test_saadeti_kojale_saves_a_member_feedback(page, matter):
    form = open_feedback_form(page)
    expect(_provenance(form, SAADETI_KOJALE)).not_to_be_checked()
    expect(_provenance(form, AVALDATUD_MUJAL)).not_to_be_checked()
    expect(_member(form)).to_be_hidden()

    form.locator(f'label.chip:has(input[value="{SAADETI_KOJALE}"])').click()
    expect(_member(form)).to_be_visible()
    choose_organisation(page, FEEDBACK_PICKER)
    _member(form).locator("label.chip").click()
    form.locator("[name=summary]").fill("Liikmesettevõte toetab muudatust.")
    _save(page, form)

    row = chronology(page).locator("article.uxtl__item", has_text="Liikmesettevõte toetab")
    expect(row).to_have_count(1)
    expect(row).to_contain_text("Meile saadetud tagasiside")


def test_avaldatud_mujal_saves_a_published_position_and_hides_liige(page, matter):
    form = open_feedback_form(page, SAADETI_KOJALE)
    member = _member(form).locator('input[name="source_is_member"]')
    _member(form).locator("label.chip").click()
    expect(member).to_be_checked()

    form.locator(f'label.chip:has(input[value="{AVALDATUD_MUJAL}"])').click()
    expect(_member(form)).to_be_hidden()
    expect(member).not_to_be_checked()

    choose_organisation(page, FEEDBACK_PICKER)
    form.locator("[name=summary]").fill("Liit avaldas vastuseisu oma veebilehel.")
    form.locator("[name=url]").fill("https://example.org/liidu-seisukoht")
    _save(page, form)

    row = chronology(page).locator("article.uxtl__item", has_text="Liit avaldas vastuseisu")
    expect(row).to_have_count(1)
    expect(row).to_contain_text("Teiste arvamus")


def test_a_save_with_no_provenance_is_refused_and_keeps_the_text(page, matter):
    form = open_feedback_form(page)
    choose_organisation(page, FEEDBACK_PICKER)
    form.locator("[name=summary]").fill("Päritoluta seisukoht.")
    _save(page, form)

    form = feedback_form(page)
    expect(form).to_contain_text("Vali, kas seisukoht saadeti Kojale või avaldati mujal.")
    expect(form.locator("[name=summary]")).to_have_value("Päritoluta seisukoht.")
    expect(
        chronology(page).locator("article.uxtl__item", has_text="Päritoluta seisukoht")
    ).to_have_count(0)


def test_a_linked_round_fixes_the_provenance_and_its_answer_ends_the_wait(page, matter):
    _start_round(page)
    form = open_feedback_form(page)
    select = form.locator("[name=engagement]")
    # One open round is still not a guess: nothing is chosen.
    expect(select).to_have_value("")
    expect(_provenance(form, AVALDATUD_MUJAL)).to_be_enabled()

    select.select_option(index=1)
    expect(_provenance(form, SAADETI_KOJALE)).to_be_checked()
    expect(_provenance(form, AVALDATUD_MUJAL)).to_be_disabled()

    select.select_option(value="")
    expect(_provenance(form, AVALDATUD_MUJAL)).to_be_enabled()

    select.select_option(index=1)
    choose_organisation(page, FEEDBACK_PICKER)
    form.locator("[name=summary]").fill("Üksteist liiget vastas.")
    _save(page, form)

    expect(page.locator("#praegune-ootused [data-feedback-wait]")).to_have_count(0)


def test_a_standalone_position_leaves_the_wait_open(page, matter):
    _start_round(page)
    form = open_feedback_form(page, AVALDATUD_MUJAL)
    choose_organisation(page, FEEDBACK_PICKER)
    form.locator("[name=summary]").fill("Ministeerium avaldas oma seisukoha.")
    _save(page, form)

    expect(
        chronology(page).locator("article.uxtl__item", has_text="Ministeerium avaldas")
    ).to_have_count(1)
    expect(page.locator("#praegune-ootused")).to_contain_text("Ootame tagasisidet")


def test_a_file_goes_with_the_position(page, matter, tmp_path):
    upload = tmp_path / "liidu-vastus.pdf"
    upload.write_bytes(b"%PDF-1.4\nsunteetiline vastus")
    form = open_feedback_form(page, SAADETI_KOJALE)
    choose_organisation(page, FEEDBACK_PICKER)
    form.locator("[name=summary]").fill("Liidu kirjalik vastus.")
    form.locator("input[type=file]").first.set_input_files(str(upload))
    _save(page, form)

    row = chronology(page).locator("article.uxtl__item", has_text="Liidu kirjalik vastus")
    expect(row).to_have_count(1)
    expect(row).to_contain_text("liidu-vastus")


def test_saadeti_kojale_with_no_organisation_saves(page, matter):
    """Received feedback may name nobody (docs/adr/0101): a telephone call."""
    form = open_feedback_form(page, SAADETI_KOJALE)
    form.locator("[name=summary]").fill("Helistas liige, nime ei öelnud.")
    _save(page, form)

    row = chronology(page).locator("article.uxtl__item", has_text="Helistas liige, nime")
    expect(row).to_have_count(1)
    expect(row).to_contain_text("Meile saadetud tagasiside")
    # No author, and no colon trailing into where one would have been.
    expect(row).not_to_contain_text("Meile saadetud tagasiside:")
    expect(feedback_form(page).locator(".field__error")).to_have_count(0)


def test_avaldatud_mujal_with_only_a_link_saves(page, matter):
    """A published position may be only its address (docs/adr/0084 §3)."""
    form = open_feedback_form(page, AVALDATUD_MUJAL)
    choose_organisation(page, FEEDBACK_PICKER)
    form.locator("[name=url]").fill("https://example.org/ainult-link")
    _save(page, form)

    row = chronology(page).locator("article.uxtl__item", has_text="Teiste arvamus:")
    expect(row).to_have_count(1)
    expect(row).to_contain_text("Näidisministeerium")
    expect(row.locator('a[href="https://example.org/ainult-link"]')).to_have_count(1)
    expect(feedback_form(page).locator(".field__error")).to_have_count(0)


def test_saadeti_kojale_with_only_a_file_saves(page, matter, tmp_path):
    """A position paper may be only its PDF (docs/adr/0084 §3)."""
    upload = tmp_path / "ainult-fail.pdf"
    upload.write_bytes(b"%PDF-1.4\nsunteetiline seisukoht")
    form = open_feedback_form(page, SAADETI_KOJALE)
    form.locator("input[type=file]").first.set_input_files(str(upload))
    _save(page, form)

    row = chronology(page).locator("article.uxtl__item", has_text="ainult-fail")
    expect(row).to_have_count(1)
    expect(row).to_contain_text("Meile saadetud tagasiside")
    expect(feedback_form(page).locator(".field__error")).to_have_count(0)


def test_an_empty_record_is_refused_with_the_source_sentence(page, matter):
    """Provenance stated and nothing else: no text, no link, no file.

    The refusal names all three sources and keeps what was answered — the
    provenance and the date — so it costs nothing but the missing source.
    """
    form = open_feedback_form(page, SAADETI_KOJALE)
    today = form.locator("[name=stated_on]").input_value()
    assert today, "the date box opens on today"
    rows_before = chronology(page).locator("article.uxtl__item").count()
    _save(page, form)

    form = feedback_form(page)
    # Read off the error itself: the field's help text states the rule too.
    expect(
        form.locator(
            ".field__error",
            has_text="Kirjuta seisukoht või lisa link või fail — vähemalt üks neist on vajalik.",
        )
    ).to_be_visible()
    expect(_provenance(form, SAADETI_KOJALE)).to_be_checked()
    expect(form.locator("[name=stated_on]")).to_have_value(today)
    expect(chronology(page).locator("article.uxtl__item")).to_have_count(rows_before)
