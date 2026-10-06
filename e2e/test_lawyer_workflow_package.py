"""The lawyer workflow in a real browser: Teema → tagasiside → arvamus → areng.

`tests/test_lawyer_workflow_package.py` holds the rules — the provenance, the
atomicity, the refusals, the visibility boundary — and runs everywhere cheaply.
This file holds the ones only a running page can settle:

* that `Koostan arvamuse` is a box on `Uus teema` whose date becomes the file's
  open step, without anybody typing the sentence (docs/adr/0091 §1);
* that `+ Meile saadetud tagasiside` records a survey summary with **no
  organisation at all** — the case a form can only be proved to accept by filling
  it in and pressing the button (§3.3);
* that the lawyer's own note renders as its own labelled line under what the
  other organisation said, rather than as more of it (§4);
* that `+ Koja arvamus` takes a file, a day and an addressee and puts a sent
  opinion on the file without leaving the Teema (§6);
* that `+ Menetluse areng` records the step, moves `Hetkeseis` and sets
  `Järgmiseks` in one save (§5);
* and that after an opinion has gone out the page says the procedure may
  continue, with controls that are actually reachable (§5.5).

**Everything here happens on a Matter the test creates.** The screenshot suite
opens its own titles, and a chronology that grew while these ran would make a
baseline depend on test order.
"""

from __future__ import annotations

import re
from datetime import date, timedelta

import pytest
from playwright.sync_api import expect

from e2e.conftest import (
    SANDRA,
    choose_organisation,
    chronology,
    create_matter,
    open_add_panel,
    open_kaik_row,
    sign_in,
    start_first_step,
    unique_title,
)

pytestmark = pytest.mark.e2e

MINISTRY = "Näidisministeerium"


def _estonian(on: date) -> str:
    """A day the way the application reads and writes one: `7.9.2026`.

    ISO would still parse — the field accepts both — but a browser test that
    typed ISO would not be exercising what a lawyer types (app/core/dates.py).
    """
    return f"{on.day}.{on.month}.{on.year}"


def _future(days: int) -> str:
    return _estonian(date.today() + timedelta(days=days))


def _past(days: int) -> str:
    return _estonian(date.today() - timedelta(days=days))


def a_new_matter(page, base_url: str) -> str:
    return create_matter(page, base_url, unique_title("Töövoog"))


def panel(page, panel_id: str):
    return page.locator(f"#{panel_id}")


# ---------------------------------------------------------------------------
# §1 — `Arvamuse tähtaeg` on Uus teema, and the step it establishes
# ---------------------------------------------------------------------------
#
# The box was `Koostan arvamuse` under an `arvamus-` prefix. `Uus teema` asked
# the same date twice under two names — that box and `Arvamuse tähtaeg` beside
# `Saabus` — and the lawyers read them as one question, so it is one box:
# `response_deadline`, recording the obligation (docs/adr/0094 §5). It
# established `Koostan arvamuse` too until docs/adr/0133 §8; a new Teema now
# gets no step — only `Soovitatud järgmisena` (docs/adr/0141).


def test_the_deadline_is_the_obligation_and_starts_nothing(page, base_url):
    """One box, one date — the obligation — and a suggestion, with nothing started.

    Until docs/adr/0133 §8 this date also established `Koostan arvamuse` as the
    file's first step. A deadline three months away then filled `PRAEGUNE
    TEGEVUS` while every task before it had nowhere to be, so the date is the
    obligation only: it reads in the header and nothing is current. Since
    docs/adr/0141 no plan is drawn; one step is suggested, and nothing is
    started for anybody.
    """
    sign_in(page, base_url, SANDRA)
    page.goto(f"{base_url}/teemad/uus/")
    page.wait_for_load_state("networkidle")

    prepare_by = _future(8)
    page.fill("#id_title", unique_title("Arvamuse tähtaeg ja plaan"))
    page.fill("#id_response_deadline", prepare_by)
    page.get_by_role("button", name="Loo teema").click()
    page.wait_for_url(re.compile(r"/teemad/[0-9a-f-]{36}/$"))

    current = page.locator("#praegune-tegevus")
    expect(current).not_to_contain_text("Koostan arvamuse")
    expect(current).to_contain_text("Järgmine samm on määramata")
    expect(current.locator(".curact__suggesttext")).to_have_text("Tutvu materjaliga")
    expect(page.locator("#tooplaan")).to_have_count(0)
    expect(page.locator(".metaline").first).to_contain_text(prepare_by)
    start_first_step(page)


def test_the_preparation_box_opens_empty_and_says_what_it_will_create(page, base_url):
    """No default, and the page says what the date is a date *for*.

    A commitment nobody stated is a commitment nobody can be held to, and since
    `Arvamuse tähtaeg` became work an invented one is not even inert
    (docs/adr/0091 §1.2).
    """
    sign_in(page, base_url, SANDRA)
    page.goto(f"{base_url}/teemad/uus/")
    page.wait_for_load_state("networkidle")

    box = page.locator("#id_response_deadline")
    expect(box).to_have_value("")
    expect(page.locator("#arvamuse-tahtaeg")).to_contain_text("Arvamuse tähtaeg")
    # The box directly above it legitimately holds today, which is what makes the
    # assertion above a measurement rather than a page with no dates on it.
    expect(page.locator("#id_received_date")).not_to_have_value("")


def test_a_teema_filed_with_no_preparation_date_has_no_step(page, base_url):
    sign_in(page, base_url, SANDRA)
    a_new_matter(page, base_url)

    expect(page.locator("#praegune-tegevus")).to_contain_text("Järgmine samm on määramata")


# ---------------------------------------------------------------------------
# §2 — `+ Kaasamine` asks about a wait, optionally (docs/adr/0120 §3)
# ---------------------------------------------------------------------------


def test_the_capture_panel_asks_the_reply_by_date_empty(page, base_url):
    """An empty box, no default and no spans.

    docs/adr/0120 §3 narrows docs/adr/0091 §2: the reply-by date is asked again
    so a round can be given a due date in one save (UQ-10), but it opens empty —
    no default, no «today + N» (docs/adr/0132) — and the quick spans stay with
    `Ootan tagasisidet` on a round filed as history.
    """
    sign_in(page, base_url, SANDRA)
    a_new_matter(page, base_url)
    open_add_panel(page, "lisa-kaasamine")

    form = panel(page, "lisa-kaasamine")
    expect(form.get_by_text("Tagasisidet ootame kuni")).to_be_visible()
    expect(form.locator("[name=feedback_deadline]")).to_have_value("")
    expect(form.locator("[data-quickdate]")).to_have_count(0)
    # `Kaasamise kuupäev` above it is unchanged and still opens on today,
    # visibly — the one shape docs/adr/0078 §2 allows a date default to take.
    expect(form.locator("[name=occurred_on]")).not_to_have_value("")


def test_a_reply_by_date_on_the_panel_is_the_open_rounds_due_date(page, base_url):
    """One save: the round is open and its row names the day it asked for.

    docs/adr/0132: the date is a due date on the open round, not what opens it,
    so `Ootan tagasisidet` is not offered and `Lõpeta kaasamine` is.
    """
    sign_in(page, base_url, SANDRA)
    a_new_matter(page, base_url)
    open_add_panel(page, "lisa-kaasamine")
    panel(page, "lisa-kaasamine").locator("[name=audience]").fill("liikmed")
    panel(page, "lisa-kaasamine").locator("[name=feedback_deadline]").fill(_future(7))
    panel(page, "lisa-kaasamine").locator("button[type=submit]").click()
    page.wait_for_load_state("networkidle")

    waiting = page.locator("#ajalugu-loend .uxtl__ms-body").filter(has_text="Kaasamine: liikmed")
    expect(waiting).to_have_count(1)
    open_kaik_row(waiting)
    expect(waiting).to_contain_text("Ootame tagasisidet kuni")
    expect(waiting.get_by_text("Ootan tagasisidet", exact=True)).to_have_count(0)
    expect(waiting.get_by_text("Lõpeta kaasamine", exact=True)).to_have_count(1)


# ---------------------------------------------------------------------------
# §3 — the two feedback chips
# ---------------------------------------------------------------------------


def test_the_launcher_offers_both_feedback_chips(page, base_url):
    sign_in(page, base_url, SANDRA)
    a_new_matter(page, base_url)

    bar = page.locator("#lisa-teemale")
    # One family chip, and the three records behind it asked second. They were
    # three peers of `+ Märge`; grouping them is a presentation change and
    # deliberately not a data change — `Meile saadetud tagasiside` and
    # `Teiste arvamus` are still two provenances of one record and
    # `Koja arvamus` is still a `Submission` (docs/adr/0097 §8, §8.1).
    expect(bar.get_by_text("+ Arvamus / tagasiside", exact=True)).to_be_visible()
    open_add_panel(page, "lisa-arvamus")
    for choice in ("Meile saadetud tagasiside", "Teiste arvamus", "Koja arvamus"):
        expect(bar.get_by_text(choice, exact=True)).to_be_visible()
    # And `+ Menetluse areng` is gone as a word: its ordinary function is
    # `+ Märge`, which writes the same record (docs/adr/0097 §6).
    expect(bar.get_by_text("+ Menetluse areng", exact=True)).to_have_count(0)


def test_feedback_with_no_organisation_is_accepted_on_the_page(page, base_url):
    """**Reversed by docs/adr/0101**, which this test used to assert the other way.

    docs/adr/0091 §3.3 widened the column for a survey of 234 companies with no
    single author, and docs/adr/0095 §4 then took `Allikas` off the creation
    panel — leaving the institution as the one answer to authorship, and this
    page refusing a save without it.

    The owner met that refusal writing down a telephone call (OWNER-01). There
    was nothing truthful to type, so the panel's question had to be answered by
    inventing a name, and the rule meant to keep invented authorship off a
    professional file was manufacturing it. For `RECEIVED` the absence is now
    the record.

    Read in a browser because the refusal was one: the service accepting the
    save proves nothing about a panel that never posts it.
    """
    sign_in(page, base_url, SANDRA)
    a_new_matter(page, base_url)
    open_add_panel(page, "arvamus-tagasiside")

    form = panel(page, "arvamus-tagasiside")
    form.locator("[name=summary]").fill("58 vastust 234 küsitletust; enamik toetab.")
    form.get_by_role("button", name="Salvesta tagasiside").click()
    page.wait_for_load_state("networkidle")

    # Filed, and the headline ends where the author would have begun rather
    # than trailing a colon into nothing.
    expect(chronology(page)).to_contain_text("58 vastust 234 küsitletust")
    expect(chronology(page)).to_contain_text("Meile saadetud tagasiside")
    expect(chronology(page)).not_to_contain_text("Meile saadetud tagasiside:")
    # The panel closed, which is what a save does and a refusal does not.
    expect(panel(page, "arvamus-tagasiside").locator(".field__error")).to_have_count(0)


def test_a_discovered_opinion_with_no_organisation_is_still_refused_on_the_page(page, base_url):
    """The half of docs/adr/0091 §3.3 that docs/adr/0101 deliberately kept.

    A published opinion always has a body that published it, so a `Teiste
    arvamus` row naming nobody is an anonymous claim on a professional file.
    The refusal above moved; this one did not.
    """
    sign_in(page, base_url, SANDRA)
    a_new_matter(page, base_url)
    open_add_panel(page, "arvamus-teiste")

    form = panel(page, "arvamus-teiste")
    form.locator("[name=summary]").fill("Keegi kuskil arvas midagi.")
    form.get_by_role("button", name="Salvesta arvamus").click()

    reopened = panel(page, "arvamus-teiste")
    expect(reopened.locator(".field__error").first).to_be_visible()
    # And what they wrote is still in the box.
    expect(reopened.locator("[name=summary]")).to_have_value("Keegi kuskil arvas midagi.")
    expect(chronology(page)).not_to_contain_text("Teiste arvamus")


def test_neither_feedback_panel_offers_the_source_box_any_more(page, base_url):
    """`Allikas` is a `Muuda` control now, on both panels (docs/adr/0095 §4)."""
    sign_in(page, base_url, SANDRA)
    a_new_matter(page, base_url)

    open_add_panel(page, "arvamus-tagasiside")
    expect(panel(page, "arvamus-tagasiside").locator("[name=source_label]")).to_have_count(0)

    open_add_panel(page, "arvamus-teiste")
    expect(panel(page, "arvamus-teiste").locator("[name=source_label]")).to_have_count(0)


def test_the_member_mark_is_on_the_received_panel_alone_and_is_recorded(page, base_url):
    """`Liige` — ticked by the person filing the answer, and nowhere else.

    The asymmetry is a rule rather than a rendering decision: `+ Teiste arvamus`
    records a position Koda found published somewhere, which was not written to
    Koda at all, so there is no question for the box to answer
    (docs/adr/0095 §4).
    """
    sign_in(page, base_url, SANDRA)
    a_new_matter(page, base_url)

    open_add_panel(page, "arvamus-teiste")
    expect(panel(page, "arvamus-teiste").locator("[name=source_is_member]")).to_have_count(0)

    open_add_panel(page, "arvamus-tagasiside")
    form = panel(page, "arvamus-tagasiside")
    mark = form.locator("[name=source_is_member]")
    expect(mark).to_have_count(1)
    expect(mark).not_to_be_checked()

    choose_organisation(page, "tagasiside")
    # `.chip__input` is a transparent overlay filling the chip (`inset: 0`,
    # `opacity: 0`, `z-index: 1`), so it *is* the click target — clicking the
    # label's text is intercepted by it, by design.
    mark.check()
    expect(mark).to_be_checked()
    form.locator("[name=summary]").fill("Vastasid kirjaga.")
    form.get_by_role("button", name="Salvesta tagasiside").click()

    chronology(page).get_by_text("Meile saadetud tagasiside:").first.wait_for()
    expect(chronology(page)).to_contain_text(f"Meile saadetud tagasiside: {MINISTRY}")


def test_a_named_organisation_reads_under_the_received_heading(page, base_url):
    sign_in(page, base_url, SANDRA)
    a_new_matter(page, base_url)
    open_add_panel(page, "arvamus-tagasiside")

    choose_organisation(page, "tagasiside")
    panel(page, "arvamus-tagasiside").locator("[name=summary]").fill("Vastasid kirjaga.")
    panel(page, "arvamus-tagasiside").get_by_role("button", name="Salvesta tagasiside").click()

    chronology(page).get_by_text("Meile saadetud tagasiside:").first.wait_for()
    expect(chronology(page)).to_contain_text(f"Meile saadetud tagasiside: {MINISTRY}")
    # And **not** under the other heading: the two chips are the distinction.
    expect(chronology(page)).not_to_contain_text("Teiste arvamus:")


# ---------------------------------------------------------------------------
# §4 — the lawyer's note is its own line
# ---------------------------------------------------------------------------


def test_the_lawyer_note_renders_as_its_own_labelled_line(page, base_url):
    """What MKM said and what this office thinks of it are two lines.

    The label is what does the work: a paragraph of Koda's assessment printed
    unlabelled under a headline naming the ministry reads as part of what the
    ministry said, which is the attribution defect with better line spacing
    (docs/adr/0091 §4).

    **Written through `Muuda`**, because docs/adr/0095 §3 took the box off the
    creation panel and left it on the correction form. That makes this test say
    rather more than it did: the note is still asked for, still stored, and
    still rendered apart from the position — and the path a lawyer now takes to
    add one is the one being exercised.
    """
    sign_in(page, base_url, SANDRA)
    a_new_matter(page, base_url)
    open_add_panel(page, "arvamus-teiste")

    form = panel(page, "arvamus-teiste")
    choose_organisation(page, "valine-seisukoht")
    form.locator("[name=summary]").fill("Toetab varianti B.")
    form.get_by_role("button", name="Salvesta arvamus").click()
    chronology(page).get_by_text("Teiste arvamus:").first.wait_for()

    open_kaik_row(chronology(page).locator(".uxtl__ms-body").first)
    chronology(page).get_by_role("button", name="Muuda").first.click()
    correction = chronology(page).locator("form[aria-label='Välise seisukoha parandamine']")
    correction.wait_for(state="visible")
    correction.locator("[name=lawyer_note]").fill("Põhjendus ei arvesta liikmete kulumõjuga.")
    correction.get_by_role("button", name="Salvesta").click()

    chronology(page).get_by_text("Juristi märkus").first.wait_for()
    row = chronology(page).locator("article.uxtl__item").first
    expect(row.locator(".uxtl__mssub")).to_have_text("Toetab varianti B.")
    note = row.locator(".uxtl__msnote")
    expect(note).to_contain_text("Juristi märkus")
    expect(note).to_contain_text("Põhjendus ei arvesta liikmete kulumõjuga.")
    # Two elements, never one: the source's line does not carry this office's.
    expect(row.locator(".uxtl__mssub")).not_to_contain_text("kulumõjuga")


# ---------------------------------------------------------------------------
# §6 — `Koja arvamus`
# ---------------------------------------------------------------------------


def _record_koda_opinion(page, base_url: str, *, sent_on: str, summary: str = "") -> None:
    open_add_panel(page, "arvamus-koja")
    form = panel(page, "arvamus-koja")
    form.locator("input[name=upload]").set_input_files(
        {"name": "koja_arvamus.pdf", "mimeType": "application/pdf", "buffer": b"%PDF-1.4 arvamus"}
    )
    form.locator("[name=sent_on]").fill(sent_on)
    # The addressee through the shared search, not by ticking a chip: since
    # docs/adr/0095 §1 the catalogue is behind «Otsi või lisa asutus…» and an
    # institution outside the answer set is `hidden` until the search reveals
    # it, so `check()` would assert something a person never does.
    choose_organisation(page, "koja-adressaat")
    if summary:
        form.locator("[name=summary]").fill(summary)
    form.get_by_role("button", name="Registreeri arvamus").click()


def test_a_sent_opinion_is_withdrawn_from_its_own_row_after_a_confirmation(page, base_url):
    """UQ-11: `Võta tagasi` is on the opinion in `Teema käik` (docs/adr/0120 §6).

    The chip opens a sentence saying what happens and a confirm button; `Loobu`
    closes it again and writes nothing. Confirmed, the opinion stays on the file
    as sent on its day, «Arvamus tagasi võetud» is added, and the chip is gone.
    """
    sign_in(page, base_url, SANDRA)
    a_new_matter(page, base_url)
    _record_koda_opinion(page, base_url, sent_on=_past(1))

    row = chronology(page).locator("article.uxtl__item").filter(has_text="Arvamus välja").first
    open_kaik_row(row)
    chip = row.locator("summary", has_text="Võta tagasi")
    expect(chip).to_be_visible()
    chip.click()
    confirm = row.get_by_role("button", name="Kinnita tagasivõtmine")
    expect(confirm).to_be_visible()
    expect(row).to_contain_text("midagi ei kustutata")

    row.get_by_role("button", name="Loobu").click()
    expect(confirm).to_be_hidden()
    expect(chronology(page)).not_to_contain_text("Arvamus tagasi võetud")

    chip.click()
    confirm.click()
    page.wait_for_load_state("load")

    expect(chronology(page)).to_contain_text("Arvamus tagasi võetud")
    expect(chronology(page)).to_contain_text("Arvamus välja")
    expect(page.locator("summary", has_text="Võta tagasi")).to_have_count(0)


def test_the_koda_opinion_panel_records_a_sent_opinion_on_the_teema(page, base_url):
    """The step the whole file is about, recorded where the work is.

    It writes the same `Submission` the `Dokumendid` panel writes, through the
    same service — what is new is that a lawyer never leaves the Teema
    (docs/adr/0091 §6).
    """
    sign_in(page, base_url, SANDRA)
    a_new_matter(page, base_url)
    _record_koda_opinion(page, base_url, sent_on=_past(1))

    strip = page.locator(".tl-strip")
    expect(strip).to_contain_text("Koja arvamus")
    expect(strip).to_contain_text(_past(1))


def test_the_koda_opinion_panel_refuses_a_save_with_nothing_in_it(page, base_url):
    """Each missing answer named on its own control, with the rest still typed."""
    sign_in(page, base_url, SANDRA)
    a_new_matter(page, base_url)
    open_add_panel(page, "arvamus-koja")

    form = panel(page, "arvamus-koja")
    form.locator("[name=summary]").fill("Toetame eelnõu.")
    form.locator("[name=sent_on]").fill("")
    form.get_by_role("button", name="Registreeri arvamus").click()

    expect(page.locator("#arvamus-koja")).to_contain_text("Lisa fail, mis välja saadeti.")
    expect(page.locator("#arvamus-koja")).to_contain_text("Vali vähemalt üks adressaat.")
    # And what they typed is still in its box.
    expect(page.locator("#arvamus-koja").locator("[name=summary]")).to_have_value("Toetame eelnõu.")


def test_the_koda_opinion_panel_asks_a_summary_and_no_title(page, base_url):
    """docs/adr/0095 §2, where a lawyer meets it.

    The box that asked for a name is gone from the document — not hidden — and
    what replaces it is a textarea asking what the opinion said.
    """
    sign_in(page, base_url, SANDRA)
    a_new_matter(page, base_url)
    open_add_panel(page, "arvamus-koja")

    form = panel(page, "arvamus-koja")
    expect(form.locator("[name=title]")).to_have_count(0)
    expect(form.locator("textarea[name=summary]")).to_be_visible()
    expect(form).not_to_contain_text("Registreerib, et Koja arvamus on välja saadetud")
    # And the addressee control is the shared searchable one, with the catalogue
    # behind it rather than drawn under it (docs/adr/0095 §1).
    expect(form.locator("#koja-adressaat-valik [data-orgfind-input]")).to_be_visible()


def test_the_opinion_summary_reads_back_on_the_file(page, base_url):
    sign_in(page, base_url, SANDRA)
    a_new_matter(page, base_url)
    written = "Toetame eelnõu, kuid palume kaheaastast üleminekuaega."
    _record_koda_opinion(page, base_url, sent_on=_past(1), summary=written)

    expect(chronology(page)).to_contain_text(written)


def test_the_addressee_opens_on_the_teema_sender(page, base_url):
    """The suggestion docs/adr/0095 §1 put in the control, and it is removable.

    The Teema is filed with `MINISTRY` as `Saatja`, so the panel opens with that
    body already chosen — visibly, where it can be read and cleared, which is
    what separates a default from a stamp (docs/adr/0078 §2).
    """
    sign_in(page, base_url, SANDRA)
    create_matter(page, base_url, unique_title("Adressaat"), sender=MINISTRY)
    open_add_panel(page, "arvamus-koja")

    chosen = panel(page, "arvamus-koja").locator(
        "#koja-adressaat-valik .orgfind__chips .chip", has_text=MINISTRY
    )
    expect(chosen).to_be_visible()
    expect(chosen.locator("input")).to_be_checked()

    chosen.click()
    expect(chosen.locator("input")).not_to_be_checked()


# ---------------------------------------------------------------------------
# §5 — `Menetluse areng`, and §5.5 — the continuation
# ---------------------------------------------------------------------------


def test_a_planned_activity_is_the_marge_and_the_next_step(page, base_url):
    """One sentence, one day ahead, one save — and the page shows both records.

    Before docs/adr/0124 the plan was written twice: once under `Mis juhtus?`
    and again under `Järgmine tegevus` with its own `Millal?`. The day ahead now
    offers `Märgi järgmiseks tegevuseks`, ticked, and the same sentence and day
    become the step.
    """
    sign_in(page, base_url, SANDRA)
    a_new_matter(page, base_url)
    open_add_panel(page, "marge-tavaline")

    form = panel(page, "marge-tavaline")
    form.locator("[name=title]").fill("Vaatan uue versiooni läbi")
    form.locator("[name=occurred_on]").fill(_future(4))
    expect(form.locator("[name=as_next_step]")).to_be_visible()
    expect(form.locator("[name=as_next_step]")).to_be_checked()
    form.get_by_role("button", name="Salvesta", exact=True).click()

    current = page.locator("#praegune-tegevus")
    current.get_by_text("Vaatan uue versiooni läbi").first.wait_for()
    expect(current).to_contain_text(_future(4))
    expect(chronology(page)).to_contain_text("Vaatan uue versiooni läbi")


def test_a_past_activity_offers_no_step_and_sets_none(page, base_url):
    """A day that is not ahead is a record of something done, and nothing else."""
    sign_in(page, base_url, SANDRA)
    a_new_matter(page, base_url)
    open_add_panel(page, "marge-tavaline")

    form = panel(page, "marge-tavaline")
    form.locator("[name=title]").fill("Eelnõu jõudis Riigikokku")
    form.locator("[name=occurred_on]").fill(_past(2))
    expect(form.locator("[name=as_next_step]")).to_be_hidden()
    form.get_by_role("button", name="Salvesta", exact=True).click()

    chronology(page).get_by_text("Eelnõu jõudis Riigikokku").first.wait_for()
    expect(page.locator("#praegune-tegevus")).to_contain_text("Järgmine samm on määramata")


def test_a_step_ahead_with_no_sentence_is_refused_on_the_sentence(page, base_url):
    """A step is its sentence: refused on `Tegevus`, and nothing is written."""
    sign_in(page, base_url, SANDRA)
    a_new_matter(page, base_url)
    open_add_panel(page, "marge-tavaline")

    form = panel(page, "marge-tavaline")
    form.locator("[name=title]").fill("")
    form.locator("[name=occurred_on]").fill(_future(9))
    expect(form.locator("[name=as_next_step]")).to_be_checked()
    form.get_by_role("button", name="Salvesta", exact=True).click()

    expect(page.locator("#marge-tavaline")).to_contain_text("Kirjuta järgmine tegevus.")
    expect(page.locator("#praegune-tegevus")).to_contain_text("Järgmine samm on määramata")


def test_after_a_sent_opinion_the_panel_says_only_that_no_step_is_set(page, base_url):
    """The continuation sentence is retired (docs/adr/0120 §1).

    The owner asked for «Koja arvamus on saadetud. Menetlus võib jätkuda — lisa
    märge.» to go, with nothing in its place: the empty panel is one line.
    """
    sign_in(page, base_url, SANDRA)
    a_new_matter(page, base_url)
    _record_koda_opinion(page, base_url, sent_on=_past(1))

    current = page.locator("#praegune-tegevus")
    expect(current).to_contain_text("Järgmine samm on määramata")
    expect(current).not_to_contain_text("Menetlus võib jätkuda")
    expect(current).not_to_contain_text("Koja arvamus on saadetud")
    expect(current.get_by_role("link", name="lisa märge")).to_have_count(0)


def test_a_step_set_after_the_opinion_takes_the_panel(page, base_url):
    """A file with a plan shows the plan, and no sentence about a dead end."""
    sign_in(page, base_url, SANDRA)
    a_new_matter(page, base_url)
    _record_koda_opinion(page, base_url, sent_on=_past(1))
    expect(page.locator("#praegune-tegevus")).to_contain_text("Järgmine samm on määramata")

    open_add_panel(page, "marge-tavaline")
    form = panel(page, "marge-tavaline")
    form.locator("[name=title]").fill("Vaatan läbi")
    form.locator("[name=occurred_on]").fill(_future(3))
    expect(form.locator("[name=as_next_step]")).to_be_checked()
    form.get_by_role("button", name="Salvesta", exact=True).click()

    current = page.locator("#praegune-tegevus")
    current.get_by_text("Vaatan läbi").first.wait_for()
    expect(current).not_to_contain_text("Järgmine samm on määramata")


# ---------------------------------------------------------------------------
# The whole journey, once
# ---------------------------------------------------------------------------


def test_one_consultation_runs_from_teema_to_the_next_round(page, base_url):
    """Acceptance scenario A and B, in one browser, on one Matter.

    Create with a preparation date; engage without acquiring a wait; record an
    aggregate answer and a ministry's opinion; send Koda's own; then record what
    the procedure did next — all on the same file, with the first opinion intact
    (docs/adr/0091).
    """
    sign_in(page, base_url, SANDRA)
    page.goto(f"{base_url}/teemad/uus/")
    page.wait_for_load_state("networkidle")
    page.fill("#id_title", unique_title("Terve töövoog"))
    page.fill("#id_response_deadline", _future(8))
    page.get_by_role("button", name="Loo teema").click()
    page.wait_for_url(re.compile(r"/teemad/[0-9a-f-]{36}/$"))
    # The deadline is the obligation; the first step is started from the plan
    # (docs/adr/0133 §8).
    start_first_step(page)
    expect(page.locator("#praegune-tegevus")).to_contain_text("Tutvu materjaliga")

    # Kaasamine: a completed act, and no wait acquired by default.
    open_add_panel(page, "lisa-kaasamine")
    kaasamine = panel(page, "lisa-kaasamine")
    kaasamine.locator("[name=audience]").fill("234 tööstusettevõtet")
    # The reply-by box is empty and left so, so filing the round is a completed
    # act and the journey continues without anything landing on a desk
    # (docs/adr/0120 §3).
    expect(kaasamine.locator("[name=feedback_deadline]")).to_have_value("")
    kaasamine.get_by_role("button", name="Salvesta").click()
    chronology(page).get_by_text("234 tööstusettevõtet").first.wait_for()

    # What came back — named, and marked as a member's. `Allikas` is a `Muuda`
    # control since docs/adr/0095 §4, so the creation panel names an
    # institution.
    open_add_panel(page, "arvamus-tagasiside")
    tagasiside = panel(page, "arvamus-tagasiside")
    choose_organisation(page, "tagasiside")
    tagasiside.locator("[name=source_is_member]").check()
    tagasiside.locator("[name=summary]").fill("58 vastust; enamik toetab.")
    tagasiside.get_by_role("button", name="Salvesta tagasiside").click()
    chronology(page).get_by_text("Meile saadetud tagasiside:").first.wait_for()

    # What somebody else said. `Juristi märkus` is a `Muuda` control too, so the
    # one substantive box is `Seisukoht` (docs/adr/0095 §3).
    open_add_panel(page, "arvamus-teiste")
    valine = panel(page, "arvamus-teiste")
    choose_organisation(page, "valine-seisukoht")
    valine.locator("[name=summary]").fill("Toetab varianti B.")
    valine.get_by_role("button", name="Salvesta arvamus").click()
    chronology(page).get_by_text("Teiste arvamus:").first.wait_for()

    # Koda's own opinion.
    _record_koda_opinion(page, base_url, sent_on=_past(3))
    expect(page.locator(".tl-strip")).to_contain_text("Koja arvamus")

    # And the procedure continues on the same file: what happened, then what
    # the lawyer will do about it — one activity each (docs/adr/0124).
    open_add_panel(page, "marge-tavaline")
    areng = panel(page, "marge-tavaline")
    areng.locator("[name=title]").fill("Ministeerium saatis uue eelnõu versiooni")
    areng.locator("[name=occurred_on]").fill(_past(1))
    areng.get_by_role("button", name="Salvesta", exact=True).click()
    chronology(page).get_by_text("Ministeerium saatis uue eelnõu versiooni").first.wait_for()

    open_add_panel(page, "marge-tavaline")
    plan = panel(page, "marge-tavaline")
    plan.locator("[name=title]").fill("Vaatan uue versiooni läbi")
    plan.locator("[name=occurred_on]").fill(_future(4))
    expect(plan.locator("[name=as_next_step]")).to_be_checked()
    plan.get_by_role("button", name="Salvesta", exact=True).click()

    page.locator("#praegune-tegevus").get_by_text("Vaatan uue versiooni läbi").first.wait_for()
    # The first opinion is still on the file: a second round is not a rewrite.
    expect(page.locator(".tl-strip")).to_contain_text("Koja arvamus")
    expect(chronology(page)).to_contain_text("Meile saadetud tagasiside:")
    expect(chronology(page)).to_contain_text("Teiste arvamus:")


def test_a_developments_lawyer_note_reads_on_the_row_under_its_own_label(page, base_url):
    """`Juristi märkus` is offered, saved, and — now — shown.

    The panel asked for this office's reading of the step and no reading surface
    printed it: the read model set it, and the label, the value and the whole
    separation lived in the `Väline seisukoht` partial alone. A lawyer had every
    reason to believe it would appear, because the identical field on two
    neighbouring panels does.

    Asserted after a full reload rather than off the HTMX answer, because what
    was broken was the rendering of the stored record.
    """
    sign_in(page, base_url, SANDRA)
    a_new_matter(page, base_url)
    open_add_panel(page, "marge-tavaline")

    form = panel(page, "marge-tavaline")
    form.locator("[name=title]").fill("Ministeerium saatis parandatud eelnõu")
    form.locator("[name=occurred_on]").fill(_past(3))
    form.get_by_role("button", name="Salvesta", exact=True).click()

    chronology(page).get_by_text("Ministeerium saatis parandatud eelnõu").first.wait_for()

    # `Juristi märkus` through `Muuda`, because `+ Märge` does not ask for one:
    # two text areas on the control a lawyer uses every day, where the second
    # is empty on nearly every save, is a form asking somebody to classify
    # their own sentence before it will take it (docs/adr/0097 §6.2). The
    # editor offers the box on a stored row, and the row renders a note the
    # same whichever surface added it — which is what this test measures.
    row = chronology(page).locator(".uxtl__ms-body").first
    # `.uxtl__edit`, not the accessible name. The button's name is built by
    # `aria-labelledby` from its own word *and* the headline above it, so a
    # chronology of a dozen rows does not offer a dozen buttons all called
    # «Muuda» — which makes an exact name match miss every one of them
    # (`development_row.html`).
    open_kaik_row(row)
    row.locator(".uxtl__edit").first.click()
    editor = page.locator(".uxtl__editform")
    editor.locator("textarea[name=note]").wait_for()
    editor.locator("textarea[name=note]").fill("Muudatused ei arvesta Koja ettepanekut.")
    editor.get_by_role("button", name="Salvesta", exact=True).click()
    page.wait_for_load_state("networkidle")

    page.reload()
    page.wait_for_load_state("networkidle")

    item = chronology(page).locator(
        ".uxtl__item",
        has=page.locator(".uxtl__mswhat", has_text="Ministeerium saatis parandatud eelnõu"),
    )
    note = item.locator(".uxtl__msnote")
    expect(note).to_have_count(1)
    expect(note).to_contain_text("Muudatused ei arvesta Koja ettepanekut.")
    # Under its own label, which is what keeps a colleague from reading Koda's
    # assessment as part of what the ministry said (docs/adr/0091 §4).
    expect(note.locator(".uxtl__msnotelabel")).to_have_text("Juristi märkus")
    # And never folded into the headline.
    expect(item.locator(".uxtl__mswhat")).not_to_contain_text("Muudatused ei arvesta")


def test_a_development_with_no_note_gains_no_empty_note_block(page, base_url):
    """Most steps carry no assessment, and none of them gains a bordered gap."""
    sign_in(page, base_url, SANDRA)
    a_new_matter(page, base_url)
    open_add_panel(page, "marge-tavaline")

    form = panel(page, "marge-tavaline")
    form.locator("[name=title]").fill("Eelnõu jõudis Riigikokku")
    form.locator("[name=occurred_on]").fill(_past(2))
    form.get_by_role("button", name="Salvesta", exact=True).click()

    chronology(page).get_by_text("Eelnõu jõudis Riigikokku").first.wait_for()
    item = chronology(page).locator(
        ".uxtl__item", has=page.locator(".uxtl__mswhat", has_text="Eelnõu jõudis Riigikokku")
    )
    expect(item.locator(".uxtl__msnote")).to_have_count(0)


def test_a_future_development_is_saved_and_reads_eesolev(page, base_url):
    """docs/adr/0121 §3, in the browser: a `Märge` may be dated ahead of today.

    «Riigikogu esimene lugemine toimub …» written down before the sitting is a
    real note. The whole save lands — the note, and the stage the lawyer chose —
    and the row is on Teema käik at once, marked `Eesolev` so it is not read as
    something that already happened. It is somebody else's event and not the
    lawyer's task, so `Märgi järgmiseks tegevuseks` is unticked and no step is
    made (docs/adr/0124 §2).
    """
    sign_in(page, base_url, SANDRA)
    a_new_matter(page, base_url)
    open_add_panel(page, "marge-tavaline")

    form = panel(page, "marge-tavaline")
    form.locator("[name=title]").fill("Riigikogu esimene lugemine")
    form.locator("[name=occurred_on]").fill(_future(12))
    form.locator("[name=as_next_step]").uncheck()
    form.locator("[name=stage]").select_option(label="Riigikogus")
    form.get_by_role("button", name="Salvesta", exact=True).click()

    chronology(page).get_by_text("Riigikogu esimene lugemine").first.wait_for()
    item = chronology(page).locator(
        ".uxtl__item", has=page.locator(".uxtl__mswhat", has_text="Riigikogu esimene lugemine")
    )
    expect(item.locator(".uxtl__msahead")).to_have_text("Eesolev")
    expect(page.locator("#praegune-tegevus")).to_contain_text("Järgmine samm on määramata")


def _file_a_step(page, title: str):
    """One `MatterProceduralDevelopment`, through `+ Märge · Tavaline`."""
    open_add_panel(page, "marge-tavaline")
    form = panel(page, "marge-tavaline")
    form.locator("[name=title]").fill(title)
    form.locator("[name=occurred_on]").fill(_past(3))
    form.get_by_role("button", name="Salvesta", exact=True).click()
    chronology(page).get_by_text(title).first.wait_for()


def _open_the_editor(page):
    """`Muuda` on the one stored step, which is where the four precisions live.

    `.uxtl__edit` rather than the accessible name: the button's name is built
    by `aria-labelledby` from its own word *and* the headline above it, so an
    exact match on «Muuda» finds nothing (`development_row.html`).
    """
    open_kaik_row(chronology(page).locator(".uxtl__ms-body").first)
    chronology(page).locator(".uxtl__ms-body").first.locator(".uxtl__edit").first.click()
    form = page.locator(".uxtl__editform")
    form.wait_for()
    return form


def test_a_future_month_quarter_and_year_are_accepted_too(page, base_url):
    """The period may be wholly ahead at any precision (docs/adr/0121 §3).

    **Driven through `Muuda`**, which is the surface that still offers the four
    precisions: `+ Märge` asks for a day or nothing (docs/adr/0097 §6.1).
    """
    sign_in(page, base_url, SANDRA)
    a_new_matter(page, base_url)
    _file_a_step(page, "Toimunud samm")

    next_year = date.today().year + 1
    for precision, fill in (
        ("Kuu", lambda f: f.locator("[name=areng_month]").select_option(value="12")),
        ("Kvartal", lambda f: f.locator("[name=areng_quarter]").select_option(value="4")),
        ("Aasta", lambda f: None),
    ):
        form = _open_the_editor(page)
        form.locator("[name=title]").fill(f"Tulevane samm, {precision}")
        form.locator("label.precision__chip", has_text=precision).click()
        fill(form)
        form.locator("[name=areng_year]").fill(str(next_year))
        form.get_by_role("button", name="Salvesta", exact=True).click()
        page.wait_for_load_state("networkidle")

        expect(page.locator(".uxtl__editform")).to_have_count(0)
        expect(chronology(page)).to_contain_text(f"Tulevane samm, {precision}")
        expect(chronology(page).locator(".uxtl__msahead").first).to_have_text("Eesolev")
        page.reload()
        page.wait_for_load_state("networkidle")


def test_a_current_month_is_accepted_and_prints_its_period(page, base_url):
    """*septembris* is not evidence of the future, and is not refused as though it were.

    The load-bearing half of the rule. A month covering today, a quarter covering
    today and the current year all begin before it, and rejecting any of them
    would leave a lawyer who knows only the month choosing between an invented
    day and an empty field — the choice docs/adr/0079 exists to remove.
    """
    sign_in(page, base_url, SANDRA)
    a_new_matter(page, base_url)
    _file_a_step(page, "Esialgne sõnastus")

    today = date.today()
    form = _open_the_editor(page)
    form.locator("[name=title]").fill("Ministeerium saatis uue versiooni")
    form.locator("label.precision__chip", has_text="Kuu").click()
    form.locator("[name=areng_month]").select_option(value=str(today.month))
    form.locator("[name=areng_year]").fill(str(today.year))
    form.get_by_role("button", name="Salvesta", exact=True).click()
    page.wait_for_load_state("networkidle")

    item = chronology(page).locator(
        ".uxtl__item",
        has=page.locator(".uxtl__mswhat", has_text="Ministeerium saatis uue versiooni"),
    )
    expect(item).to_have_count(1)
    # The period, never its anchor: `01.09.2026` is a day nobody named.
    expect(item.locator(".uxtl__msdate")).to_contain_text(str(today.year))
    expect(item.locator(".uxtl__msdate")).not_to_contain_text(f"1.{today.month}.{today.year}")
