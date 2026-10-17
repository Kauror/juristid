"""The 2026-10 UX round (#453–#456) on one Teema, in a real browser.

The Django half is tests/test_ux_round_integration.py; this is its core path and
one failure-and-recovery path, driven the way a lawyer drives them:

* `+ Lisa · Arvamuse tähtaeg` sets the opinion deadline, and `Alusta
  kaasamist`'s `Tagasisidet ootame kuni` carries it as the calendar's mark
  (stage 1) without choosing it;
* a round with a feedback deadline waits; `Lisa tagasiside` linked to it ends
  the wait; another body's published opinion, a `Muu` with a link and a file,
  and a meeting with files leave `PRAEGUNE TEGEVUS` exactly as it was;
* `+ Koja arvamus`, ticked as the answer to the deadline, settles it in the
  header;
* `Teema käik` shows every record newest first by its own day, and
  `Dokumendid` lists every file;
* a refused `Muu` (a link carrying credentials) keeps what was typed and adds
  no row; the corrected save goes through.

Every test writes to a Matter it creates. State is read off the page only.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest
from playwright.sync_api import expect

from e2e.conftest import (
    AVALDATUD_MUJAL,
    FEEDBACK_PICKER,
    SANDRA,
    choose_organisation,
    chronology,
    create_matter,
    open_add_panel,
    open_feedback_form,
    sign_in,
    wait_for_htmx,
)
from e2e.titles import unique_title

pytestmark = pytest.mark.e2e

PDF = b"%PDF-1.4 synthetic integration material"
MINISTRY = "Näidisministeerium"


def _et(on: date) -> str:
    return f"{on.day}.{on.month}.{on.year}"


def _file(name: str) -> dict:
    return {"name": name, "mimeType": "application/pdf", "buffer": PDF + name.encode()}


def _save(page, form, path: str) -> int:
    with page.expect_response(
        lambda r: r.url.endswith(path) and r.request.method == "POST"
    ) as answer:
        form.get_by_role("button", name="Salvesta", exact=True).click()
    wait_for_htmx(page)
    return answer.value.status


def _reload(page) -> None:
    page.reload()
    page.wait_for_load_state("networkidle")


def _row(page, text: str):
    """The record's own row: a round's row also lists the answers linked to it."""
    return (
        chronology(page)
        .locator("article.uxtl__item", has_text=text)
        .filter(has_not_text="Kaasamine: Liikmed (integratsioon)")
    )


def test_one_teema_through_the_whole_round(page, base_url):
    today = date.today()
    opinion_due = today + timedelta(days=30)
    sign_in(page, base_url, SANDRA)
    create_matter(
        page, base_url, unique_title("Integratsioon eelnõu"), owner=SANDRA, sender=MINISTRY
    )

    # -- `+ Lisa · Arvamuse tähtaeg` -------------------------------------------
    open_add_panel(page, "marge-arvamuse-tahtaeg")
    form = page.locator("#marge-arvamuse-tahtaeg form")
    form.locator("[name=response_deadline_date]").fill(_et(opinion_due))
    assert _save(page, form, "/lisa/arvamuse-tahtaeg/") == 200
    expect(page.locator("#teema-pais")).to_contain_text(f"Arvamuse tähtaeg {_et(opinion_due)}")

    # -- stage 1: the feedback calendar marks it, choosing nothing -------------
    open_add_panel(page, "kaasamine-alusta")
    round_form = page.locator("#kaasamine-alusta")
    feedback_due = round_form.locator('input[name="feedback_deadline"]')
    expect(feedback_due).to_have_attribute("data-datepicker-mark", _et(opinion_due))
    expect(feedback_due).to_have_attribute("data-datepicker-mark-label", "Arvamuse tähtaeg")
    expect(feedback_due).to_have_value("")

    # -- a round with a feedback deadline --------------------------------------
    round_form.locator("[name=audience]").fill("Liikmed (integratsioon)")
    round_form.locator("[name=occurred_on]").fill(_et(today - timedelta(days=20)))
    feedback_due.fill(_et(today + timedelta(days=10)))
    assert _save(page, round_form, "/lisa/kaasamine/") == 200
    expect(page.locator("#praegune-ootused [data-feedback-wait]")).to_have_count(1)

    # -- a reply linked to it ends the wait -------------------------------------
    form = open_feedback_form(page)
    form.locator("[name=engagement]").select_option(index=1)
    choose_organisation(page, FEEDBACK_PICKER, MINISTRY)
    form.locator("[name=summary]").fill("Integratsiooni liikmevastus: toetame.")
    form.locator("[name=stated_on]").fill(_et(today - timedelta(days=15)))
    assert _save(page, form, "/lisa/kaasamise-tagasiside/") == 200
    expect(page.locator("#praegune-ootused [data-feedback-wait]")).to_have_count(0)

    _reload(page)
    work_before = page.locator("#praegune-tegevus").inner_text()

    # -- another body's published opinion ---------------------------------------
    form = open_feedback_form(page, AVALDATUD_MUJAL)
    choose_organisation(page, FEEDBACK_PICKER, MINISTRY)
    form.locator("[name=summary]").fill("Integratsiooni avalik seisukoht mujal.")
    form.locator("[name=stated_on]").fill(_et(today - timedelta(days=12)))
    assert _save(page, form, "/lisa/kaasamise-tagasiside/") == 200

    # -- `+ Lisa · Muu` with a link and a file ----------------------------------
    open_add_panel(page, "marge-muu")
    form = page.locator("#marge-muu form")
    form.locator("[name=description]").fill("Integratsiooni taustamaterjal.")
    form.locator("[name=occurred_on]").fill(_et(today - timedelta(days=10)))
    form.locator("[name=url]").fill("https://www.example.ee/integratsioon")
    form.locator('input[type="file"]').set_input_files([_file("int-taust.pdf")])
    assert _save(page, form, "/lisa/muu/") == 200

    # -- `Kohtumine / arutelu` with files ----------------------------------------
    open_add_panel(page, "arvamus-kohtumine")
    form = page.locator("#arvamus-kohtumine form")
    form.locator("[name=occurred_on]").fill(_et(today - timedelta(days=7)))
    form.locator("[name=summary]").fill("Integratsiooni kohtumine ministeeriumiga.")
    form.locator('input[type="file"]').set_input_files(
        [_file("int-memo.pdf"), _file("int-esitlus.pdf")]
    )
    assert _save(page, form, "/lisa/kohtumine/") == 200

    _reload(page)
    assert page.locator("#praegune-tegevus").inner_text() == work_before

    # -- `+ Koja arvamus`, the answer to the Arvamuse tähtaeg --------------------
    open_add_panel(page, "arvamus-koja")
    form = page.locator("#arvamus-koja")
    form.locator("input[name=upload]").set_input_files([_file("int-koja-arvamus.pdf")])
    form.locator("[name=sent_on]").fill(_et(today - timedelta(days=2)))
    form.locator("[name=summary]").fill("Integratsiooni Koja arvamus.")
    form.locator('label[for="id_koja_arvamus_vastab"]').click()
    expect(form.locator("#id_koja_arvamus_vastab")).to_be_checked()
    assert _save(page, form, "/lisa/koja-arvamus/") == 200

    _reload(page)
    header = page.locator("#teema-pais")
    expect(header.locator(".metaline__value--settled")).to_contain_text(_et(opinion_due))

    # -- Teema käik: every record, on its own day, newest first -----------------
    for text, days in (
        ("Integratsiooni liikmevastus", 15),
        ("Integratsiooni avalik seisukoht", 12),
        ("Integratsiooni taustamaterjal", 10),
        ("Integratsiooni kohtumine", 7),
    ):
        row = _row(page, text)
        expect(row).to_have_count(1)
        expect(row.locator("time.uxtl__time, .uxtl__msdate").first).to_have_text(
            _et(today - timedelta(days=days))
        )
    expect(_row(page, "Integratsiooni liikmevastus")).to_contain_text("Meile saadetud tagasiside")
    expect(_row(page, "Integratsiooni liikmevastus")).to_contain_text("Vastus kaasamisele")
    # The round's own row: its wait ended, and it lists the one linked answer.
    round_row = chronology(page).locator(
        "article.uxtl__item", has_text="Kaasamine: Liikmed (integratsioon)"
    )
    expect(round_row).to_have_count(1)
    expect(round_row).to_contain_text("Tagasiside ootamine lõpetatud")
    expect(round_row).to_contain_text("Seotud seisukohti 1")
    expect(_row(page, "Integratsiooni avalik seisukoht")).to_contain_text("Teiste arvamus")
    expect(_row(page, "Integratsiooni taustamaterjal").locator("a.uxtl__link")).to_have_attribute(
        "href", "https://www.example.ee/integratsioon"
    )
    texts = (
        chronology(page)
        .locator("article.uxtl__item")
        .evaluate_all("els => els.map(e => e.textContent)")
    )

    def at(words: str) -> int:
        return next(i for i, t in enumerate(texts) if words in t)

    assert (
        at("Integratsiooni kohtumine")
        < at("Integratsiooni taustamaterjal")
        < at("Integratsiooni avalik seisukoht")
        < at("Integratsiooni liikmevastus")
    ), texts
    days = (
        chronology(page)
        .locator("article.uxtl__item time.uxtl__time")
        .evaluate_all("els => els.map(e => e.getAttribute('datetime').slice(0, 10))")
    )
    assert days == sorted(days, reverse=True), days

    # -- Dokumendid lists every file ---------------------------------------------
    page.goto(page.url.split("#")[0].rstrip("/") + "/dokumendid/")
    page.wait_for_load_state("networkidle")
    for name in ("int-taust.pdf", "int-memo.pdf", "int-esitlus.pdf", "int-koja-arvamus.pdf"):
        expect(page.get_by_text(name).first).to_be_visible()


def test_a_refused_muu_keeps_what_was_typed_and_the_corrected_save_goes_through(page, base_url):
    sign_in(page, base_url, SANDRA)
    create_matter(page, base_url, unique_title("Integratsioon taastumine"))
    rows_before = chronology(page).locator("article.uxtl__item").count()
    work_before = page.locator("#praegune-tegevus").inner_text()
    day = _et(date.today() - timedelta(days=4))

    open_add_panel(page, "marge-muu")
    form = page.locator("#marge-muu form")
    form.locator("[name=description]").fill("Taastumise taustamaterjal.")
    form.locator("[name=occurred_on]").fill(day)
    form.locator("[name=url]").fill("https://kasutaja:parool@example.ee/taust")
    assert _save(page, form, "/lisa/muu/") == 400

    form = page.locator("#marge-muu form")
    expect(form).to_be_visible()
    expect(form.locator("#id_muu_url_error")).to_have_text(
        "Link ei tohi sisaldada kasutajanime ega parooli."
    )
    expect(form.locator("[name=description]")).to_have_value("Taastumise taustamaterjal.")
    expect(form.locator("[name=occurred_on]")).to_have_value(day)
    expect(form.locator("[name=url]")).to_have_value("https://kasutaja:parool@example.ee/taust")
    expect(chronology(page).locator("article.uxtl__item")).to_have_count(rows_before)

    form.locator("[name=url]").fill("https://example.ee/taust")
    assert _save(page, form, "/lisa/muu/") == 200

    _reload(page)
    row = _row(page, "Taastumise taustamaterjal.")
    expect(row).to_have_count(1)
    expect(row.locator("a.uxtl__link")).to_have_attribute("href", "https://example.ee/taust")
    assert page.locator("#praegune-tegevus").inner_text() == work_before
