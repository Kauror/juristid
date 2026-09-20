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

from e2e.conftest import SANDRA, create_matter, open_add_panel, sign_in, unique_title

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


def chronology(page):
    return page.locator("#ajalugu-loend")


def choose_organisation(page, picker: str, name: str = MINISTRY) -> None:
    """Answer an organisation control the way a person does: type, then pick.

    The same two steps `e2e/test_unified_organisation_picker.py` uses, and
    deliberately not a `check()` on the radio: the chips are labels whose input is
    clipped, and an institution outside the visible shortlist is `hidden` until
    the search reveals it — so ticking the control directly asserts something the
    person never does and fails on exactly the bodies the search exists for.
    """
    box = page.locator(f"#{picker}-otsi")
    box.click()
    box.fill("")
    box.type(name[:8], delay=20)
    page.locator(f"#{picker}-tulemused").get_by_role("option", name=name, exact=True).click()


# ---------------------------------------------------------------------------
# §1 — `Arvamuse tähtaeg` on Uus teema, and the step it establishes
# ---------------------------------------------------------------------------
#
# The box was `Koostan arvamuse` under an `arvamus-` prefix. `Uus teema` asked
# the same date twice under two names — that box and `Arvamuse tähtaeg` beside
# `Saabus` — and the lawyers read them as one question, so it is one box:
# `response_deadline`, at the end of the form, recording the obligation and
# establishing this step (docs/adr/0094 §5). Every rule below is docs/adr/0091
# §1's; only the key moved.


def test_the_preparation_date_becomes_the_files_first_step(page, base_url):
    """One box, one date, and the step exists — with nobody typing the sentence.

    The whole of lawyer feedback 9 in a browser: before this, the ordinary
    journey was file the Teema, open `Lisa teemale`, choose `+ Järgmine tegevus`,
    and enter the same information a second time.
    """
    sign_in(page, base_url, SANDRA)
    page.goto(f"{base_url}/teemad/uus/")
    page.wait_for_load_state("networkidle")

    prepare_by = _future(8)
    page.fill("#id_title", unique_title("Koostan arvamuse"))
    page.fill("#id_response_deadline", prepare_by)
    page.get_by_role("button", name="Loo teema").click()
    page.wait_for_url(re.compile(r"/teemad/[0-9a-f-]{36}/$"))

    current = page.locator("#praegune-tegevus")
    expect(current).to_contain_text("Koostan arvamuse")
    expect(current).to_contain_text(prepare_by)


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
# §2 — `+ Kaasamine` does not ask about a wait at all
# ---------------------------------------------------------------------------


def test_the_capture_panel_has_no_reply_by_question(page, base_url):
    """Not an empty box — no box, no label, no spans.

    docs/adr/0091 §2 narrows docs/adr/0086 §2 past its default: recording that
    Koda asked somebody something is a completed act, and an empty reply-by box
    is still a question a lawyer has to read, understand and skip on every round
    they file. Opening a wait is `Ootan tagasisidet` on the round's own row.
    """
    sign_in(page, base_url, SANDRA)
    a_new_matter(page, base_url)
    open_add_panel(page, "lisa-kaasamine")

    form = panel(page, "lisa-kaasamine")
    expect(form.locator("[name=feedback_deadline]")).to_have_count(0)
    expect(form.get_by_text("Tagasisidet ootame kuni")).to_have_count(0)
    expect(form.locator("[data-quickdate]")).to_have_count(0)
    # `Kaasamise kuupäev` above it is unchanged and still opens on today,
    # visibly — the one shape docs/adr/0078 §2 allows a date default to take.
    expect(form.locator("[name=occurred_on]")).not_to_have_value("")


def test_the_explicit_wait_is_where_the_spans_went(page, base_url):
    """`Ootan tagasisidet` — one question, and asking still costs one click.

    The spans travelled with the question they answer, which is the half that
    makes the narrowing affordable (docs/adr/0091 §2).
    """
    sign_in(page, base_url, SANDRA)
    a_new_matter(page, base_url)
    open_add_panel(page, "lisa-kaasamine")
    panel(page, "lisa-kaasamine").locator("[name=audience]").fill("liikmed")
    panel(page, "lisa-kaasamine").locator("button[type=submit]").click()
    page.wait_for_load_state("networkidle")

    row = page.locator("#ajalugu-loend .uxtl__ms-body").filter(has_text="Kaasamine: liikmed")
    expect(row).to_have_count(1)
    row.get_by_text("Ootan tagasisidet", exact=True).click()

    box = row.locator("[name=feedback_deadline]")
    expect(box).to_have_value("")
    row.get_by_role("button", name="1 nädal").click()
    expect(box).to_have_value(_future(7))

    row.get_by_role("button", name="Salvesta ootus").click()
    page.wait_for_load_state("networkidle")

    waiting = page.locator("#ajalugu-loend .uxtl__ms-body").filter(has_text="Kaasamine: liikmed")
    expect(waiting).to_contain_text("Ootame tagasisidet kuni")
    expect(waiting.get_by_text("Ootan tagasisidet", exact=True)).to_have_count(0)


# ---------------------------------------------------------------------------
# §3 — the two feedback chips
# ---------------------------------------------------------------------------


def test_the_launcher_offers_both_feedback_chips(page, base_url):
    sign_in(page, base_url, SANDRA)
    a_new_matter(page, base_url)

    bar = page.locator("#lisa-teemale")
    expect(bar.get_by_text("+ Meile saadetud tagasiside", exact=True)).to_be_visible()
    expect(bar.get_by_text("+ Teiste arvamus", exact=True)).to_be_visible()
    expect(bar.get_by_text("+ Koja arvamus", exact=True)).to_be_visible()
    expect(bar.get_by_text("+ Menetluse areng", exact=True)).to_be_visible()


def test_feedback_with_no_organisation_is_refused_on_the_page(page, base_url):
    """`Allikas` is off this panel, so the institution is what answers authorship.

    docs/adr/0091 §3.3 widened the column for a survey of 234 companies with no
    single author, and every row filed that way keeps its label and is corrected
    through `Muuda`. What it cost was a question with two right answers at the
    top of the panel a department fills in several times a week, so the creation
    form names an institution — and the authorship rule is *met* rather than
    relaxed (docs/adr/0095 §4).
    """
    sign_in(page, base_url, SANDRA)
    a_new_matter(page, base_url)
    open_add_panel(page, "arvamus-tagasiside")

    form = panel(page, "arvamus-tagasiside")
    form.locator("[name=summary]").fill("58 vastust 234 küsitletust; enamik toetab.")
    form.get_by_role("button", name="Salvesta tagasiside").click()

    reopened = panel(page, "arvamus-tagasiside")
    expect(reopened.locator(".field__error").first).to_be_visible()
    # And what they wrote is still in the box.
    expect(reopened.locator("[name=summary]")).to_have_value(
        "58 vastust 234 küsitletust; enamik toetab."
    )
    expect(chronology(page)).not_to_contain_text("Meile saadetud tagasiside:")


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
    form.locator("input[type=file]").set_input_files(
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


def test_a_development_records_the_step_the_stage_and_the_next_action(page, base_url):
    """One save, three canonical writes, and the page shows all three.

    Before this, recording «the ministry sent a revised draft» meant a `Märge`
    with no date box, a `Hetkeseis` change in the header, and
    `+ Järgmine tegevus` under the launcher — three saves for one thought.
    """
    sign_in(page, base_url, SANDRA)
    a_new_matter(page, base_url)
    open_add_panel(page, "marge-tavaline")

    form = panel(page, "marge-tavaline")
    form.locator("[name=title]").fill("Ministeerium saatis uue eelnõu versiooni")
    form.locator("[name=occurred_on]").fill(_past(2))
    form.locator("[name=next_text]").fill("Vaatan uue versiooni läbi")
    form.locator("[name=next_date]").fill(_future(4))
    form.get_by_role("button", name="Salvesta", exact=True).click()

    chronology(page).get_by_text("Ministeerium saatis uue eelnõu versiooni").first.wait_for()
    current = page.locator("#praegune-tegevus")
    expect(current).to_contain_text("Vaatan uue versiooni läbi")
    expect(current).to_contain_text(_future(4))


def test_a_half_filled_next_step_is_refused_on_the_empty_control(page, base_url):
    sign_in(page, base_url, SANDRA)
    a_new_matter(page, base_url)
    open_add_panel(page, "marge-tavaline")

    form = panel(page, "marge-tavaline")
    form.locator("[name=title]").fill("Eelnõu jõudis Riigikokku")
    form.locator("[name=next_text]").fill("Vaatan uue teksti läbi")
    form.get_by_role("button", name="Salvesta", exact=True).click()

    expect(page.locator("#marge-tavaline")).to_contain_text("Vali järgmise tegevuse kuupäev.")
    # Nothing was written: the whole save is one transaction.
    expect(chronology(page)).not_to_contain_text("Eelnõu jõudis Riigikokku")


def test_after_a_sent_opinion_the_page_offers_the_continuation(page, base_url):
    """The dead end, closed: a file whose opinion went out says so and says on.

    Two anchors to controls that are already on the page — not a wizard, not a
    suggested step, and nothing created (docs/adr/0091 §5.5).
    """
    sign_in(page, base_url, SANDRA)
    a_new_matter(page, base_url)
    _record_koda_opinion(page, base_url, sent_on=_past(1))

    current = page.locator("#praegune-tegevus")
    expect(current).to_contain_text("Menetlus võib jätkuda")
    link = current.get_by_role("link", name="lisa menetluse areng")
    expect(link).to_be_visible()

    # And the anchor reaches a control that is really there and really opens.
    link.click()
    open_add_panel(page, "marge-tavaline")
    expect(panel(page, "marge-tavaline").locator("[name=title]")).to_be_visible()


def test_the_continuation_is_absent_while_a_step_is_open(page, base_url):
    """A file with a plan is not at a dead end and needs no sentence about it."""
    sign_in(page, base_url, SANDRA)
    a_new_matter(page, base_url)
    _record_koda_opinion(page, base_url, sent_on=_past(1))
    expect(page.locator("#praegune-tegevus")).to_contain_text("Menetlus võib jätkuda")

    open_add_panel(page, "marge-tavaline")
    form = panel(page, "marge-tavaline")
    form.locator("[name=title]").fill("Eelnõu läks Justiitsministeeriumisse")
    form.locator("[name=occurred_on]").fill(_past(1))
    form.locator("[name=next_text]").fill("Vaatan läbi")
    form.locator("[name=next_date]").fill(_future(3))
    form.get_by_role("button", name="Salvesta", exact=True).click()

    current = page.locator("#praegune-tegevus")
    current.get_by_text("Vaatan läbi").first.wait_for()
    expect(current).not_to_contain_text("Menetlus võib jätkuda")


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
    expect(page.locator("#praegune-tegevus")).to_contain_text("Koostan arvamuse")

    # Kaasamine: a completed act, and no wait acquired by default.
    open_add_panel(page, "lisa-kaasamine")
    kaasamine = panel(page, "lisa-kaasamine")
    kaasamine.locator("[name=audience]").fill("234 tööstusettevõtet")
    # No reply-by question here at all, so filing the round is a completed act
    # and the journey continues without anything landing on a desk
    # (docs/adr/0091 §2).
    expect(kaasamine.locator("[name=feedback_deadline]")).to_have_count(0)
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

    # And the procedure continues on the same file.
    open_add_panel(page, "marge-tavaline")
    areng = panel(page, "marge-tavaline")
    areng.locator("[name=title]").fill("Ministeerium saatis uue eelnõu versiooni")
    areng.locator("[name=occurred_on]").fill(_past(1))
    areng.locator("[name=next_text]").fill("Vaatan uue versiooni läbi")
    areng.locator("[name=next_date]").fill(_future(4))
    areng.get_by_role("button", name="Salvesta", exact=True).click()

    chronology(page).get_by_text("Ministeerium saatis uue eelnõu versiooni").first.wait_for()
    expect(page.locator("#praegune-tegevus")).to_contain_text("Vaatan uue versiooni läbi")
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
    row.get_by_role("button", name="Muuda", exact=True).click()
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


def test_a_future_development_is_refused_and_moves_no_stage(page, base_url):
    """The product decision, in the browser: a development records what happened.

    «Riigikogu esimene lugemine toimub 30.09» is a plan, and filing it here used
    to succeed silently — the chronology declined to draw a future row, and the
    stage change saved in the same breath was not declined, so the file read
    «Hetkeseis: Riigikogus» dated to the afternoon somebody typed it, with
    nothing anywhere saying why.

    The whole save is refused now, with the panel open and the answer still in
    it.
    """
    sign_in(page, base_url, SANDRA)
    a_new_matter(page, base_url)
    open_add_panel(page, "marge-tavaline")

    form = panel(page, "marge-tavaline")
    form.locator("[name=title]").fill("Riigikogu esimene lugemine")
    form.locator("[name=occurred_on]").fill(_future(12))
    form.locator("[name=stage]").select_option(label="Riigikogus")
    form.get_by_role("button", name="Salvesta", exact=True).click()
    page.wait_for_load_state("networkidle")

    panel_after = panel(page, "marge-tavaline")
    expect(panel_after).to_contain_text("Menetluse areng ei saa olla tulevikus.")
    # Nothing was written, and that includes the half of the act that used to
    # survive on its own: a standalone `Hetkeseis` row, carrying the day of data
    # entry, for a stage the file had not reached.
    expect(chronology(page)).not_to_contain_text("Riigikogu esimene lugemine")
    expect(chronology(page)).not_to_contain_text("Hetkeseis")
    # The typed answer is still there to be corrected rather than retyped.
    expect(panel_after.locator("[name=title]")).to_have_value("Riigikogu esimene lugemine")


def test_a_future_month_quarter_and_year_are_refused_too(page, base_url):
    """The rule is about the period, not about the day box.

    A lawyer who picks `Kuu` and says *the month after next* has stated something
    as wholly ahead as an exact date does, and the refusal has to reach the
    control they answered it in.
    """
    sign_in(page, base_url, SANDRA)
    a_new_matter(page, base_url)

    next_year = date.today().year + 1
    for precision, fill in (
        ("Kuu", lambda f: f.locator("[name=areng_month]").select_option(value="12")),
        ("Kvartal", lambda f: f.locator("[name=areng_quarter]").select_option(value="4")),
        ("Aasta", lambda f: None),
    ):
        open_add_panel(page, "marge-tavaline")
        form = panel(page, "marge-tavaline")
        form.locator("[name=title]").fill(f"Tulevane samm, {precision}")
        form.locator("label.precision__chip", has_text=precision).click()
        fill(form)
        form.locator("[name=areng_year]").fill(str(next_year))
        form.get_by_role("button", name="Salvesta", exact=True).click()
        page.wait_for_load_state("networkidle")

        expect(panel(page, "marge-tavaline")).to_contain_text(
            "Menetluse areng ei saa olla tulevikus."
        )
        expect(chronology(page)).not_to_contain_text(f"Tulevane samm, {precision}")
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
    open_add_panel(page, "marge-tavaline")

    today = date.today()
    form = panel(page, "marge-tavaline")
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
