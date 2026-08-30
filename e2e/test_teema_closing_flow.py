"""Closing a Teema, in a real browser.

The domain suite proves the rules; this proves that a person can actually
perform them. Two of the redesign's claims are only true in a browser: that
seven new recipients can be added before a single save, and that removing one
that was typed by mistake does not cost a page load.

Everything here is synthetic. The Matters are created through the UI, the
recipients are invented party names, and the uploaded opinion is a few bytes of
PDF header.
"""

from __future__ import annotations

import re

import pytest
from playwright.sync_api import expect

from e2e.conftest import MARTIN, open_composer, sign_in

pytestmark = pytest.mark.e2e


NEW_RECIPIENTS = [
    "Näidiserakond Alpha",
    "Näidiserakond Beeta",
    "Näidiserakond Gamma",
    "Näidiserakond Delta",
    "Näidiserakond Epsilon",
    "Näidiserakond Zeeta",
    "Näidiserakond Eeta",
]

OPINION_PDF = {
    "name": "Koja_arvamus.pdf",
    "mimeType": "application/pdf",
    "buffer": b"%PDF-1.4 synthetic opinion",
}


def create_matter(page, base_url: str, title: str) -> str:
    page.goto(f"{base_url}/teemad/uus/")
    page.wait_for_load_state("networkidle")
    page.fill("#id_title", title)
    page.get_by_role("button", name="Loo teema").click()
    page.wait_for_url(re.compile(r"/teemad/[0-9a-f-]{36}/$"))
    return page.url


def open_closing_section(page):
    open_composer(page)
    page.locator(".disclosure-chip", has_text="+ Lõpeta teema").click()
    section = page.locator("#koostaja-lopetamine")
    expect(section).to_be_visible()
    page.locator("#id_close_matter").check()
    return section


def add_recipient(page, name: str) -> None:
    box = page.locator("[data-recipient-input]")
    box.fill(name)
    page.locator("[data-recipient-add]").click()


def save_and_expect_ok(page):
    with page.expect_response(
        lambda response: "/sissekanne/" in response.url and response.request.method == "POST"
    ) as caught:
        page.locator("[data-composer-submit]").click()
    saved = caught.value
    assert saved.status == 200, f"the closure save was refused: {saved.status}"
    page.wait_for_load_state("networkidle")
    return saved


def test_the_closing_section_asks_only_the_approved_questions(page, base_url):
    """Everything the redesign removed is gone from the page, not merely from
    the template that happened to render last."""
    sign_in(page, base_url, MARTIN)
    create_matter(page, base_url, "Lihtsustatud lõpetamine brauserikatsest")
    section = open_closing_section(page)

    for name in (
        "closure_reason",
        "successor",
        "final_version",
        "final_title",
        "final_channel",
        "final_reference",
        "victory_title",
        "victory_detail",
    ):
        expect(page.locator(f"[name='{name}']")).to_have_count(0)

    # And what remains is the approved list.
    expect(section.locator("#id_disposition")).to_be_visible()
    expect(section.locator("#id_final_file")).to_be_visible()
    expect(section.locator("#id_final_sent_on")).to_be_visible()
    expect(section.locator("[data-recipient-input]")).to_be_visible()
    expect(section.locator("#id_work_victory_0")).to_be_visible()
    expect(section.locator("#id_work_victory_1")).to_be_visible()
    # The commencement date belongs to "Jah" and is not offered before it.
    expect(section.locator("[data-victory-date]")).to_be_hidden()


def test_closing_with_an_opinion_seven_new_recipients_and_a_victory(page, base_url):
    """The whole approved flow in one save, including the case the old fixed
    checkbox list could not record at all."""
    sign_in(page, base_url, MARTIN)
    url = create_matter(page, base_url, "Seitsme saajaga lõpetamine brauserikatsest")
    section = open_closing_section(page)

    page.locator(".composer__body").fill("Arvamus esitati ja piirmäär tõsteti.")
    page.locator("#id_disposition").select_option("COMPLETED")

    # The final opinion is uploaded here, not chosen from files already on the
    # Matter — this Matter has none, which is the ordinary case at closure.
    section.locator("#id_final_file").set_input_files(files=[OPINION_PDF])
    section.locator("#id_final_sent_on").fill("12.08.2026")

    # An existing institution from the shortlist, if the register has offered
    # one, and then seven that do not exist yet.
    shortlist = section.locator("input[name='final_recipients']")
    if shortlist.count():
        shortlist.first.check()

    for name in NEW_RECIPIENTS:
        add_recipient(page, name)
    expect(section.locator(".recipientadd__item")).to_have_count(7)
    # The box is empty and ready for the next one, never carrying the last.
    expect(section.locator("[data-recipient-input]")).to_have_value("")

    # A mistake is removable without a page load, and another goes in after it.
    section.locator(".recipientadd__item", has_text="Näidiserakond Eeta").locator(
        "[data-recipient-remove]"
    ).click()
    expect(section.locator(".recipientadd__item")).to_have_count(6)
    add_recipient(page, "Näidiserakond Teeta")
    expect(section.locator(".recipientadd__item")).to_have_count(7)

    # Töövõit is an explicit decision, and only "Jah" asks when it commenced.
    expect(section.locator("[data-victory-date]")).to_be_hidden()
    section.locator("#id_work_victory_0").check()
    expect(section.locator("[data-victory-date]")).to_be_visible()
    section.locator("#id_victory_effective_on").fill("01.01.2027")

    save_and_expect_ok(page)
    expect(page.locator(".formerror")).to_have_count(0)
    expect(page.locator(".composer .field__error")).to_have_count(0)

    # The Matter is closed, and the records it wrote render.
    page.goto(url)
    expect(page.locator(".badge--closed")).to_be_visible()
    # The banner quotes the one narrative the save carried.
    expect(page.locator(".banner--closed")).to_contain_text("Arvamus esitati")
    # The sent opinion reaches the rail with the file that went out.
    opinion = page.locator(".railposition__opinion")
    expect(opinion).to_be_visible()
    expect(opinion).to_contain_text("Koja_arvamus.pdf")

    # The commencement is a Jõustumine — the domain's own section for it — and
    # not a period borrowed from the work victory.
    # Rendered the way every date in this application is rendered — the box
    # takes 01.01.2027 and the page says 1.1.2027 (app/core/dates.py).
    expect(page.locator("#joustumine")).to_contain_text("1.1.2027")


def test_a_duplicate_recipient_cannot_be_added_twice(page, base_url):
    sign_in(page, base_url, MARTIN)
    create_matter(page, base_url, "Korduva saajaga lõpetamine brauserikatsest")
    section = open_closing_section(page)

    add_recipient(page, "Näidiserakond Alpha")
    add_recipient(page, "Näidiserakond Alpha")

    expect(section.locator(".recipientadd__item")).to_have_count(1)


def test_closing_without_a_work_victory(page, base_url):
    """The other half of the decision, and the shorter path through the form."""
    sign_in(page, base_url, MARTIN)
    url = create_matter(page, base_url, "Töövõiduta lõpetamine brauserikatsest")
    section = open_closing_section(page)

    page.locator(".composer__body").fill("Koda ei tegele teemaga edasi.")
    page.locator("#id_disposition").select_option("MONITORING_STOPPED")
    section.locator("#id_work_victory_1").check()
    # Saying "Ei" never asks for a commencement date.
    expect(section.locator("[data-victory-date]")).to_be_hidden()

    save_and_expect_ok(page)

    page.goto(url)
    expect(page.locator(".badge--closed")).to_be_visible()
    expect(page.locator(".banner--closed")).to_contain_text("Koda ei tegele teemaga edasi.")


def test_closing_without_an_answer_about_the_work_victory_is_refused(page, base_url):
    """The decision is required, so it cannot be skipped into a silent "no"."""
    sign_in(page, base_url, MARTIN)
    create_matter(page, base_url, "Otsustamata lõpetamine brauserikatsest")
    open_closing_section(page)

    page.locator(".composer__body").fill("Menetlus lõppes.")
    page.locator("#id_disposition").select_option("COMPLETED")

    with page.expect_response(
        lambda response: "/sissekanne/" in response.url and response.request.method == "POST"
    ) as caught:
        page.locator("[data-composer-submit]").click()
    assert caught.value.status == 400
    page.wait_for_load_state("networkidle")

    expect(page.locator("#koostaja-lopetamine")).to_contain_text("Märgi, kas teemast sai töövõit.")
