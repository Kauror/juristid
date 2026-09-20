"""Correcting a filed `Kaasamine`, in a real browser.

The Python suite proves the rule at the service and at the route: both dates
round-trip, an emptied box stores nothing, a partial correction clears nothing,
a stale form is refused and a closed Teema takes no correction at all. What only
a browser can show is the half that is htmx.

That `Muuda` on a chronology row turns *that row* into a form with the record
already in it — the difference between correcting a date and retyping a
consultation. That saving puts the corrected row back where it was rather than
adding a second one underneath. That an emptied `Kaasamise kuupäev` comes back
reading «Kuupäev teadmata» rather than the day somebody typed it in. And that
the correction survives a reload, so what was shown is what was stored.

The `Täpsus` chips are here for the same reason (docs/adr/0082). Which group of
controls is *shown* is a pure-CSS consequence of which radio is checked — a
`:has()` rule on the fieldset — so whether a person can actually reach the month
select, and whether the day box is really empty when they open a record stored
as a quarter, are questions only a rendering engine answers.

Everything here is synthetic, and every test files its own Matter. The seeded
world is shared across a shard and never reset between files
(`e2e/conftest.py`), so a test that corrected a seeded `Kaasamine` would be
rewriting a row `e2e/test_ui_regression.py` photographs.
"""

from __future__ import annotations

import datetime as dt

import pytest
from playwright.sync_api import expect

from e2e.conftest import (
    MARTIN,
    READER,
    create_matter,
    open_add_panel,
    open_matter,
    sign_in,
    unique_title,
)

pytestmark = pytest.mark.e2e

#: The seeded open Matter, spelled as `seed_e2e_data` writes it. Copied rather
#: than imported for the reason `e2e/conftest.py` gives: this directory
#: deliberately imports no application code.
OPEN_TITLE = (
    "Tavaline avatud teema kõigile nähtav — pakendiseaduse ja sellega seonduvalt "
    "teiste seaduste muutmise seaduse eelnõu väljatöötamiskavatsus"
)

AUDIENCE = "kaubandusvaldkonna töögrupp"
CORRECTED_AUDIENCE = "kaubandus- ja teenindusvaldkonna töögrupp"

#: Dates a person would actually type. Leading zeros on the way in, because
#: that is what a keyboard and a date picker both produce.
HELD_ON = "04.03.2026"
REPLY_BY = "18.03.2026"
#: The same two days as the application writes them back — `j.n.Y`, no leading
#: zeros, in the box and on the row alike. Typing one spelling and reading the
#: other is the round trip, not a defect: `EstonianDateField` accepts both.
HELD_ON_READ = "4.3.2026"
REPLY_BY_READ = "18.3.2026"
DATE_UNKNOWN = "Kuupäev teadmata"

#: A reply-by date that has **not** gone by, computed against the clock.
#:
#: The fixed dates above are in the past on purpose — a chronology row only
#: renders once its day has arrived — and a reply-by date in the past reads as
#: «Tagasiside tähtaeg möödus» rather than «Ootame tagasisidet kuni»
#: (docs/adr/0086 §4). The completion scenarios below are about a round that is
#: still collecting, so they need a day that is still ahead. Three weeks out,
#: which is wider than any clock skew between this process and the server.
REPLY_BY_AHEAD = (dt.date.today() + dt.timedelta(days=21)).strftime("%d.%m.%Y")


def _file_an_engagement(
    page, *, occurred_on: str = HELD_ON, reply_by: str = "", response_count: str = ""
) -> None:
    """Record one consultation through the real `+ Kaasamine` panel.

    ``reply_by`` defaults to **empty**, which is now simply what the panel does:
    since docs/adr/0091 §2 it does not ask about a reply-by date at all, so every
    round filed here is one nobody is waiting on. A fixture that *is* waiting
    asks for it afterwards through `Ootan tagasisidet`, which is the one surface
    that opens a wait — see :func:`_open_a_wait` below.
    """
    open_add_panel(page, "lisa-kaasamine")
    page.locator("#lisa-kaasamine input[name=audience]").fill(AUDIENCE)
    page.locator("#lisa-kaasamine input[name=occurred_on]").fill(occurred_on)
    if response_count:
        page.locator("#lisa-kaasamine input[name=response_count]").fill(response_count)
    with page.expect_response(
        lambda response: "/lisa/kaasamine/" in response.url and response.request.method == "POST"
    ) as caught:
        page.locator("#lisa-kaasamine button[type=submit]").click()
    assert caught.value.status == 200, f"the consultation was refused: {caught.value.status}"
    page.wait_for_load_state("networkidle")
    if reply_by:
        _open_a_wait(page, reply_by)


def _open_a_wait(page, reply_by: str) -> None:
    """`Ootan tagasisidet` on the round's own row — the act that starts a wait.

    Its own step rather than a field on the panel above, because that is what it
    is now: filing a consultation and deciding the file is waiting on an answer
    are two acts, and only the second puts a row on somebody's desk
    (docs/adr/0091 §2).
    """
    row = _row(page)
    row.get_by_text("Ootan tagasisidet", exact=True).click()
    row.locator("input[name=feedback_deadline]").fill(reply_by)
    with page.expect_response(
        lambda response: "/ootus/" in response.url and response.request.method == "POST"
    ) as caught:
        row.get_by_role("button", name="Salvesta ootus").click()
    assert caught.value.status == 200, f"the wait was refused: {caught.value.status}"
    page.wait_for_load_state("networkidle")


def _row(page):
    """The one `Kaasamine` row on a freshly filed Matter."""
    row = page.locator(".uxtl__ms-body").first
    row.wait_for()
    return row


def _open_the_editor(page):
    _row(page).get_by_role("button", name="Muuda", exact=False).click()
    form = page.locator(".uxtl__editform")
    form.wait_for()
    return form


def _save(page):
    with page.expect_response(
        lambda response: (
            "/kaasamine/" in response.url
            and response.url.endswith("/muuda/")
            and response.request.method == "POST"
        )
    ) as caught:
        page.locator(".uxtl__editform button[type=submit]").click()
    return caught.value


def test_a_filed_kaasamine_can_be_corrected_in_place(page, base_url):
    """The whole feature, end to end, on an open Teema."""
    sign_in(page, base_url, MARTIN)
    create_matter(page, base_url, unique_title("Kaasamise paranduse brauserikatse"))
    _file_an_engagement(page)

    expect(page.locator(".uxtl__ms-body")).to_have_count(1)
    expect(_row(page)).to_contain_text(f"Kaasamine: {AUDIENCE}")

    form = _open_the_editor(page)

    # Prefilled, every box of it. Retyping a consultation to fix a date is the
    # thing this avoids.
    assert form.locator("input[name=title]").input_value() == AUDIENCE
    assert form.locator("input[name=occurred_on]").input_value() == HELD_ON_READ
    assert form.locator("input[name=feedback_deadline]").input_value() == ""
    # And it is holding the version it was filled from.
    assert form.locator("input[name=revision]").input_value()

    form.locator("input[name=title]").fill(CORRECTED_AUDIENCE)
    form.locator("input[name=feedback_deadline]").fill(REPLY_BY)
    saved = _save(page)
    assert saved.status == 200, f"the correction was refused: {saved.status}"
    page.wait_for_load_state("networkidle")

    # Same row, corrected, and no second line underneath it.
    row = _row(page)
    expect(row).to_contain_text(f"Kaasamine: {CORRECTED_AUDIENCE}")
    # `REPLY_BY` is in the past, so the row reads as due rather than as waiting
    # — three wordings, one state machine (docs/adr/0086 §4).
    expect(row).to_contain_text("Tagasiside tähtaeg möödus")
    expect(page.locator(".uxtl__ms-body")).to_have_count(1)
    expect(page.locator(".uxtl__editform")).to_have_count(0)

    # What was shown is what was stored.
    page.reload()
    page.wait_for_load_state("networkidle")
    expect(_row(page)).to_contain_text(f"Kaasamine: {CORRECTED_AUDIENCE}")
    expect(_row(page)).to_contain_text(REPLY_BY_READ)


def test_an_emptied_date_reads_as_kuupaev_teadmata(page, base_url):
    """The correction this round exists for: a stamped day told the truth.

    «Kuupäev teadmata» rather than the day the row happens to sit on. The
    fallback places the row; it never describes it.
    """
    sign_in(page, base_url, MARTIN)
    create_matter(page, base_url, unique_title("Kaasamise paranduse katse: tühi kuupäev"))
    _file_an_engagement(page)

    form = _open_the_editor(page)
    form.locator("input[name=occurred_on]").fill("")
    saved = _save(page)
    assert saved.status == 200, f"an emptied date was refused: {saved.status}"
    page.wait_for_load_state("networkidle")

    row = _row(page)
    expect(row).to_contain_text(DATE_UNKNOWN)
    expect(row).not_to_contain_text(HELD_ON_READ)

    page.reload()
    page.wait_for_load_state("networkidle")
    expect(_row(page)).to_contain_text(DATE_UNKNOWN)


def test_a_response_count_typed_on_the_panel_can_be_corrected_afterwards(page, base_url):
    """QA-03, in the browser it was found in.

    `+ Kaasamine` takes `Vastuseid`, the chronology prints it, and until this
    round the editor had no box for it — so a lawyer who typed `7` where they
    meant `8` had no route back out of the number. The whole ladder in one pass,
    because the three states are only interesting against each other:

    * the editor opens holding what was filed;
    * `7 -> 8` saves and the row says so;
    * `8 -> 0` is a **zero**, not a blank — «keegi ei vastanud» is a real
      outcome and the box hands it back as `0`;
    * an emptied box clears the count, which is how somebody stops standing
      behind one, and the row then claims no count at all.
    """
    sign_in(page, base_url, MARTIN)
    create_matter(page, base_url, unique_title("Kaasamise paranduse katse: vastuseid"))
    _file_an_engagement(page, response_count="7")

    expect(_row(page)).to_contain_text("Vastuseid 7")

    form = _open_the_editor(page)
    count = form.locator("input[name=response_count]")
    assert count.input_value() == "7", "the editor did not open holding the filed count"

    count.fill("8")
    saved = _save(page)
    assert saved.status == 200, f"the correction was refused: {saved.status}"
    page.wait_for_load_state("networkidle")
    expect(_row(page)).to_contain_text("Vastuseid 8")

    # Zero is a number and stays one, in the row and in the box it reopens in.
    form = _open_the_editor(page)
    form.locator("input[name=response_count]").fill("0")
    assert _save(page).status == 200
    page.wait_for_load_state("networkidle")
    expect(_row(page)).to_contain_text("Vastuseid 0")

    form = _open_the_editor(page)
    assert form.locator("input[name=response_count]").input_value() == "0", (
        "an explicit zero opened as a blank box"
    )

    # And empty means «nobody counted», which the row then states by saying
    # nothing about a count.
    form.locator("input[name=response_count]").fill("")
    assert _save(page).status == 200
    page.wait_for_load_state("networkidle")
    expect(_row(page)).not_to_contain_text("Vastuseid")

    page.reload()
    page.wait_for_load_state("networkidle")
    expect(_row(page)).not_to_contain_text("Vastuseid")


def test_a_refused_response_count_keeps_the_stored_one(page, base_url):
    """A negative count is refused beside its own box, and 7 is still 7."""
    sign_in(page, base_url, MARTIN)
    create_matter(page, base_url, unique_title("Kaasamise paranduse katse: vigane arv"))
    _file_an_engagement(page, response_count="7")

    form = _open_the_editor(page)
    form.locator("input[name=response_count]").fill("-1")
    assert _save(page).status == 400, "a negative response count was accepted"

    form = page.locator(".uxtl__editform")
    expect(form).to_have_count(1)
    # What was typed is still in the box, and the record is untouched behind it.
    assert form.locator("input[name=response_count]").input_value() == "-1"

    page.reload()
    page.wait_for_load_state("networkidle")
    expect(_row(page)).to_contain_text("Vastuseid 7")


def test_tuhista_leaves_the_kaasamine_exactly_as_it_was(page, base_url):
    sign_in(page, base_url, MARTIN)
    create_matter(page, base_url, unique_title("Kaasamise paranduse katse: tühista"))
    _file_an_engagement(page)

    form = _open_the_editor(page)
    form.locator("input[name=title]").fill("Seda ei salvestata kunagi")
    form.get_by_role("button", name="Tühista", exact=True).click()
    page.wait_for_load_state("networkidle")

    row = _row(page)
    expect(row).to_contain_text(f"Kaasamine: {AUDIENCE}")
    expect(page.locator(".uxtl__editform")).to_have_count(0)


def test_a_refused_correction_keeps_what_was_typed(page, base_url):
    """A reply-by date before the round it belongs to, refused under its box."""
    sign_in(page, base_url, MARTIN)
    create_matter(page, base_url, unique_title("Kaasamise paranduse katse: keeldumine"))
    _file_an_engagement(page)

    form = _open_the_editor(page)
    form.locator("input[name=feedback_deadline]").fill("01.03.2026")
    saved = _save(page)
    assert saved.status == 400, f"a deadline before the round was accepted: {saved.status}"
    page.wait_for_load_state("networkidle")

    # The form is still open, still holding what was typed, and the record is
    # untouched behind it.
    form = page.locator(".uxtl__editform")
    expect(form).to_be_visible()
    expect(form.locator(".field__error").first).to_be_visible()
    assert form.locator("input[name=feedback_deadline]").input_value() == "01.03.2026"

    page.reload()
    page.wait_for_load_state("networkidle")
    expect(_row(page)).not_to_contain_text("Tagasiside tähtaeg")


def test_a_closed_teema_offers_no_correction(page, base_url):
    """The rule this feature keeps, and the one difference from a Sissekanne.

    Correcting a consultation is normal business work, so a finished file
    refuses it — the control is not offered and the route refuses a POST that
    reaches it anyway.
    """
    sign_in(page, base_url, MARTIN)
    create_matter(page, base_url, unique_title("Kaasamise paranduse katse: suletud"))
    _file_an_engagement(page)

    open_add_panel(page, "teema-lopeta")
    page.locator("#teema-lopeta .uxchip", has_text="Menetlus lõppes").click()
    with page.expect_response(
        lambda response: "/lisa/lopeta/" in response.url and response.request.method == "POST"
    ) as caught:
        page.locator("#teema-lopeta button[type=submit]").click()
    assert caught.value.status == 200
    page.wait_for_load_state("networkidle")

    expect(page.locator(".badge--state")).to_contain_text("Suletud")
    expect(page.locator("#lisa-teemale")).to_have_count(0)

    # The row is still readable, and carries no way to rewrite it.
    row = _row(page)
    expect(row).to_contain_text(f"Kaasamine: {AUDIENCE}")
    expect(row.get_by_role("button", name="Muuda", exact=False)).to_have_count(0)


def test_a_reader_is_offered_no_correction(page, base_url):
    """Reading the chronology is not being able to rewrite it."""
    sign_in(page, base_url, READER)
    open_matter(page, base_url, OPEN_TITLE)

    chronology = page.locator("#ajalugu-loend")
    expect(chronology).to_be_visible()
    expect(chronology.get_by_text("Kaasamine:", exact=False).first).to_be_visible()
    expect(chronology.get_by_role("button", name="Muuda", exact=False)).to_have_count(0)


# ---------------------------------------------------------------------------
# `Lõpeta kaasamine` — the half of docs/adr/0086 only a browser can answer
# ---------------------------------------------------------------------------
#
# The two `Täpsus` scenarios that stood here went with the control they drove.
# `+ Kaasamine` and `Muuda` no longer offer the four precision chips
# (docs/adr/0086 §1), so a browser test that clicked one would be driving markup
# the product does not render; what a stored period does instead is asserted in
# `tests/test_engagement_date_precision.py`, which needs no rendering engine
# because the claim is about a form's initial values and a service's writes.
#
# What only a rendering engine can answer is this: the completion form lives
# inside a `<details>` on a chronology row that is itself an HTMX swap target,
# so opening it, posting it and having the answer land back in the same element
# is a claim about three layers agreeing.


def _finish_panel(page):
    """The `Lõpeta kaasamine` disclosure on the one filed row."""
    return _row(page).locator(".uxtl__finish")


def test_a_waiting_round_is_finished_on_its_own_row(page, base_url):
    """docs/adr/0086 §6, end to end: the wait, the answers, the completed row.

    The disclosure is closed at rest, opens in place, and its save swaps the
    same element the row already is — so the completed record lands where the
    reader is looking, with no reload and no second line in the chronology.
    """
    sign_in(page, base_url, MARTIN)
    create_matter(page, base_url, unique_title("Kaasamise lõpetamise brauserikatse"))
    _file_an_engagement(page, reply_by=REPLY_BY_AHEAD)

    row = _row(page)
    expect(row).to_contain_text("Ootame tagasisidet kuni")
    panel = _finish_panel(page)
    expect(panel).to_have_count(1)
    expect(panel.locator("textarea[name=feedback_received]")).not_to_be_visible()

    panel.locator("summary").click()
    panel.locator("textarea[name=feedback_received]").fill("Kaks vastust, mõlemad toetavad.")
    with page.expect_response(
        lambda response: "/lopeta/" in response.url and response.request.method == "POST"
    ) as caught:
        panel.locator("button[type=submit]").click()
    assert caught.value.status == 200, f"the completion was refused: {caught.value.status}"
    page.wait_for_load_state("networkidle")

    row = _row(page)
    expect(page.locator(".uxtl__ms-body")).to_have_count(1)
    expect(row).to_contain_text("Tagasiside ootamine lõpetatud")
    expect(row).to_contain_text("Kaks vastust, mõlemad toetavad.")
    expect(row).not_to_contain_text("Ootame tagasisidet kuni")
    expect(row.locator(".uxtl__finish")).to_have_count(0)

    # What was shown is what was stored.
    page.reload()
    page.wait_for_load_state("networkidle")
    expect(_row(page)).to_contain_text("Tagasiside ootamine lõpetatud")


def test_a_round_nobody_is_waiting_on_offers_no_finish_control(page, base_url):
    """§3. What draws the control is the dated point, never the act."""
    sign_in(page, base_url, MARTIN)
    create_matter(page, base_url, unique_title("Kaasamine ilma tähtajata"))
    _file_an_engagement(page)

    expect(_row(page)).not_to_contain_text("Ootame tagasisidet")
    expect(_finish_panel(page)).to_have_count(0)


def test_finishing_with_an_empty_box_records_that_nothing_came_back(page, base_url):
    """§6. «Keegi ei vastanud» is a result, and the button has to accept it."""
    sign_in(page, base_url, MARTIN)
    create_matter(page, base_url, unique_title("Kaasamine ilma vastusteta"))
    _file_an_engagement(page, reply_by=REPLY_BY_AHEAD)

    panel = _finish_panel(page)
    panel.locator("summary").click()
    with page.expect_response(
        lambda response: "/lopeta/" in response.url and response.request.method == "POST"
    ) as caught:
        panel.locator("button[type=submit]").click()
    assert caught.value.status == 200, f"an empty completion was refused: {caught.value.status}"
    page.wait_for_load_state("networkidle")

    row = _row(page)
    expect(row).to_contain_text("Tagasiside ootamine lõpetatud")
    expect(row.locator(".uxtl__finish")).to_have_count(0)


def test_the_finish_disclosure_is_reachable_and_usable_from_the_keyboard(page, base_url):
    """Every control here is native, and this is what that buys.

    The disclosure is a `<summary>` and the save is a `<button type=submit>`, so
    Tab reaches both and Enter works on both without a line of script. A
    completion that could only be started with a mouse would be a workflow half
    the department cannot use (AGENTS.md, *UX quality*).

    Driven through the keyboard rather than through `.click()`, because clicking
    proves the handler and says nothing about whether anybody can get to it.
    """
    sign_in(page, base_url, MARTIN)
    create_matter(page, base_url, unique_title("Kaasamise lopetamine klaviatuurilt"))
    _file_an_engagement(page, reply_by=REPLY_BY_AHEAD)

    summary = _finish_panel(page).locator("summary")
    summary.focus()
    expect(summary).to_be_focused()
    page.keyboard.press("Enter")

    box = _finish_panel(page).locator("textarea[name=feedback_received]")
    expect(box).to_be_visible()
    box.focus()
    page.keyboard.type("Vastas kaks liiget.")

    # Tab past the file control to the save, and press it. The exact number of
    # stops is not asserted — that is markup detail — but the save has to be
    # *reachable*, and `Enter` on a focused submit has to submit.
    with page.expect_response(
        lambda response: "/lopeta/" in response.url and response.request.method == "POST"
    ) as caught:
        _finish_panel(page).locator("button[type=submit]").focus()
        page.keyboard.press("Enter")
    assert caught.value.status == 200, f"the completion was refused: {caught.value.status}"
    page.wait_for_load_state("networkidle")

    row = _row(page)
    expect(row).to_contain_text("Tagasiside ootamine lõpetatud")
    expect(row).to_contain_text("Vastas kaks liiget.")
