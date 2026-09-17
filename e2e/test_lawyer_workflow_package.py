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
# §1 — `Koostan arvamuse` on Uus teema
# ---------------------------------------------------------------------------


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
    page.fill("#id_arvamus-prepare_by", prepare_by)
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

    box = page.locator("#id_arvamus-prepare_by")
    expect(box).to_have_value("")
    expect(page.locator("#koostan-arvamuse")).to_contain_text("Koostan arvamuse")
    # The box directly above it legitimately holds today, which is what makes the
    # assertion above a measurement rather than a page with no dates on it.
    expect(page.locator("#id_received_date")).not_to_have_value("")


def test_a_teema_filed_with_no_preparation_date_has_no_step(page, base_url):
    sign_in(page, base_url, SANDRA)
    a_new_matter(page, base_url)

    expect(page.locator("#praegune-tegevus")).to_contain_text("Järgmine samm on määramata")


# ---------------------------------------------------------------------------
# §2 — the reply-by box opens empty
# ---------------------------------------------------------------------------


def test_the_reply_by_box_opens_empty_and_the_spans_still_fill_it(page, base_url):
    """The wait is asked for, not given — and asking costs one click.

    docs/adr/0091 §2 narrows docs/adr/0086 §2 on the default alone. What the
    spans do is unchanged, which is the half that makes the narrowing affordable.
    """
    sign_in(page, base_url, SANDRA)
    a_new_matter(page, base_url)
    open_add_panel(page, "lisa-kaasamine")

    box = panel(page, "lisa-kaasamine").locator("[name=feedback_deadline]")
    expect(box).to_have_value("")
    # `Kaasamise kuupäev` above it is unchanged and still opens on today.
    expect(panel(page, "lisa-kaasamine").locator("[name=occurred_on]")).not_to_have_value("")

    panel(page, "lisa-kaasamine").get_by_role("button", name="1 nädal").click()
    expect(box).to_have_value(_future(7))


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


def test_aggregate_feedback_saves_with_no_organisation_at_all(page, base_url):
    """The case that needed the column widened, proved by filling the form in.

    A survey of 234 industrial companies has no single author. Before this the
    panel refused the save, and what that bought was an invented organisation
    called «234 ettevõtet» (docs/adr/0091 §3.3).
    """
    sign_in(page, base_url, SANDRA)
    a_new_matter(page, base_url)
    open_add_panel(page, "lisa-tagasiside")

    form = panel(page, "lisa-tagasiside")
    form.locator("[name=source_label]").fill("Tööstusettevõtete küsitlus")
    form.locator("[name=summary]").fill("58 vastust 234 küsitletust; enamik toetab.")
    form.get_by_role("button", name="Salvesta tagasiside").click()

    chronology(page).get_by_text("Meile saadetud tagasiside:").first.wait_for()
    expect(chronology(page)).to_contain_text(
        "Meile saadetud tagasiside: Tööstusettevõtete küsitlus"
    )


def test_the_received_panel_offers_the_source_box_and_the_other_does_not(page, base_url):
    """`Allikas` is a received-feedback control, and the markup says so.

    A discovered position has an author by definition, and a free text box
    answering «whose position is this» there would be a ninth way of naming an
    institution beside the one shared catalogue (docs/adr/0073).
    """
    sign_in(page, base_url, SANDRA)
    a_new_matter(page, base_url)

    open_add_panel(page, "lisa-tagasiside")
    expect(panel(page, "lisa-tagasiside").locator("[name=source_label]")).to_be_visible()

    open_add_panel(page, "lisa-valine-seisukoht")
    expect(panel(page, "lisa-valine-seisukoht").locator("[name=source_label]")).to_have_count(0)


def test_a_named_organisation_reads_under_the_received_heading(page, base_url):
    sign_in(page, base_url, SANDRA)
    a_new_matter(page, base_url)
    open_add_panel(page, "lisa-tagasiside")

    choose_organisation(page, "tagasiside")
    panel(page, "lisa-tagasiside").locator("[name=summary]").fill("Vastasid kirjaga.")
    panel(page, "lisa-tagasiside").get_by_role("button", name="Salvesta tagasiside").click()

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
    """
    sign_in(page, base_url, SANDRA)
    a_new_matter(page, base_url)
    open_add_panel(page, "lisa-valine-seisukoht")

    form = panel(page, "lisa-valine-seisukoht")
    choose_organisation(page, "valine-seisukoht")
    form.locator("[name=summary]").fill("Toetab varianti B.")
    form.locator("[name=lawyer_note]").fill("Põhjendus ei arvesta liikmete kulumõjuga.")
    form.get_by_role("button", name="Salvesta arvamus").click()

    chronology(page).get_by_text("Teiste arvamus:").first.wait_for()
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


def _record_koda_opinion(page, base_url: str, *, sent_on: str) -> None:
    open_add_panel(page, "lisa-koja-arvamus")
    form = panel(page, "lisa-koja-arvamus")
    form.locator("input[type=file]").set_input_files(
        {"name": "koja_arvamus.pdf", "mimeType": "application/pdf", "buffer": b"%PDF-1.4 arvamus"}
    )
    form.locator("[name=sent_on]").fill(sent_on)
    form.get_by_role("checkbox", name=MINISTRY, exact=True).check()
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
    open_add_panel(page, "lisa-koja-arvamus")

    form = panel(page, "lisa-koja-arvamus")
    form.locator("[name=title]").fill("Koja arvamus eelnõule")
    form.locator("[name=sent_on]").fill("")
    form.get_by_role("button", name="Registreeri arvamus").click()

    expect(page.locator("#lisa-koja-arvamus")).to_contain_text("Lisa fail, mis välja saadeti.")
    expect(page.locator("#lisa-koja-arvamus")).to_contain_text("Vali vähemalt üks adressaat.")
    # And what they typed is still in its box.
    expect(page.locator("#lisa-koja-arvamus").locator("[name=title]")).to_have_value(
        "Koja arvamus eelnõule"
    )


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
    open_add_panel(page, "lisa-menetluse-areng")

    form = panel(page, "lisa-menetluse-areng")
    form.locator("[name=title]").fill("Ministeerium saatis uue eelnõu versiooni")
    form.locator("[name=occurred_on]").fill(_past(2))
    form.locator("[name=next_text]").fill("Vaatan uue versiooni läbi")
    form.locator("[name=next_date]").fill(_future(4))
    form.get_by_role("button", name="Salvesta areng").click()

    chronology(page).get_by_text("Ministeerium saatis uue eelnõu versiooni").first.wait_for()
    current = page.locator("#praegune-tegevus")
    expect(current).to_contain_text("Vaatan uue versiooni läbi")
    expect(current).to_contain_text(_future(4))


def test_a_half_filled_next_step_is_refused_on_the_empty_control(page, base_url):
    sign_in(page, base_url, SANDRA)
    a_new_matter(page, base_url)
    open_add_panel(page, "lisa-menetluse-areng")

    form = panel(page, "lisa-menetluse-areng")
    form.locator("[name=title]").fill("Eelnõu jõudis Riigikokku")
    form.locator("[name=next_text]").fill("Vaatan uue teksti läbi")
    form.get_by_role("button", name="Salvesta areng").click()

    expect(page.locator("#lisa-menetluse-areng")).to_contain_text("Vali järgmise tegevuse kuupäev.")
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
    open_add_panel(page, "lisa-menetluse-areng")
    expect(panel(page, "lisa-menetluse-areng").locator("[name=title]")).to_be_visible()


def test_the_continuation_is_absent_while_a_step_is_open(page, base_url):
    """A file with a plan is not at a dead end and needs no sentence about it."""
    sign_in(page, base_url, SANDRA)
    a_new_matter(page, base_url)
    _record_koda_opinion(page, base_url, sent_on=_past(1))
    expect(page.locator("#praegune-tegevus")).to_contain_text("Menetlus võib jätkuda")

    open_add_panel(page, "lisa-menetluse-areng")
    form = panel(page, "lisa-menetluse-areng")
    form.locator("[name=title]").fill("Eelnõu läks Justiitsministeeriumisse")
    form.locator("[name=occurred_on]").fill(_past(1))
    form.locator("[name=next_text]").fill("Vaatan läbi")
    form.locator("[name=next_date]").fill(_future(3))
    form.get_by_role("button", name="Salvesta areng").click()

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
    page.fill("#id_arvamus-prepare_by", _future(8))
    page.get_by_role("button", name="Loo teema").click()
    page.wait_for_url(re.compile(r"/teemad/[0-9a-f-]{36}/$"))
    expect(page.locator("#praegune-tegevus")).to_contain_text("Koostan arvamuse")

    # Kaasamine: a completed act, and no wait acquired by default.
    open_add_panel(page, "lisa-kaasamine")
    kaasamine = panel(page, "lisa-kaasamine")
    kaasamine.locator("[name=audience]").fill("234 tööstusettevõtet")
    expect(kaasamine.locator("[name=feedback_deadline]")).to_have_value("")
    kaasamine.get_by_role("button", name="Salvesta").click()
    chronology(page).get_by_text("234 tööstusettevõtet").first.wait_for()

    # What came back, with no organisation to invent.
    open_add_panel(page, "lisa-tagasiside")
    tagasiside = panel(page, "lisa-tagasiside")
    tagasiside.locator("[name=source_label]").fill("Tööstusettevõtete küsitlus")
    tagasiside.locator("[name=summary]").fill("58 vastust; enamik toetab.")
    tagasiside.get_by_role("button", name="Salvesta tagasiside").click()
    chronology(page).get_by_text("Meile saadetud tagasiside:").first.wait_for()

    # What somebody else said, with this office's reading kept apart from it.
    open_add_panel(page, "lisa-valine-seisukoht")
    valine = panel(page, "lisa-valine-seisukoht")
    choose_organisation(page, "valine-seisukoht")
    valine.locator("[name=summary]").fill("Toetab varianti B.")
    valine.locator("[name=lawyer_note]").fill("Ei arvesta kulumõjuga.")
    valine.get_by_role("button", name="Salvesta arvamus").click()
    chronology(page).get_by_text("Teiste arvamus:").first.wait_for()

    # Koda's own opinion.
    _record_koda_opinion(page, base_url, sent_on=_past(3))
    expect(page.locator(".tl-strip")).to_contain_text("Koja arvamus")

    # And the procedure continues on the same file.
    open_add_panel(page, "lisa-menetluse-areng")
    areng = panel(page, "lisa-menetluse-areng")
    areng.locator("[name=title]").fill("Ministeerium saatis uue eelnõu versiooni")
    areng.locator("[name=occurred_on]").fill(_past(1))
    areng.locator("[name=next_text]").fill("Vaatan uue versiooni läbi")
    areng.locator("[name=next_date]").fill(_future(4))
    areng.get_by_role("button", name="Salvesta areng").click()

    chronology(page).get_by_text("Ministeerium saatis uue eelnõu versiooni").first.wait_for()
    expect(page.locator("#praegune-tegevus")).to_contain_text("Vaatan uue versiooni läbi")
    # The first opinion is still on the file: a second round is not a rewrite.
    expect(page.locator(".tl-strip")).to_contain_text("Koja arvamus")
    expect(chronology(page)).to_contain_text("Meile saadetud tagasiside:")
    expect(chronology(page)).to_contain_text("Teiste arvamus:")
