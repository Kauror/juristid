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


def _file_an_engagement(page, *, occurred_on: str = HELD_ON, reply_by: str = "") -> None:
    """Record one consultation through the real `+ Kaasamine` panel.

    ``reply_by`` defaults to **empty**, and that is deliberate: the panel
    pre-fills `Tagasisidet ootame kuni` with a week out (docs/adr/0085 §2), so a
    helper that left the box alone would file every fixture as a waiting round
    and the correction tests below would be measuring a state they never set.
    Clearing it here is also the shortest proof that the default is an initial
    value and nothing more — the saved row has no deadline at all.
    """
    open_add_panel(page, "lisa-kaasamine")
    page.locator("#lisa-kaasamine input[name=audience]").fill(AUDIENCE)
    page.locator("#lisa-kaasamine input[name=occurred_on]").fill(occurred_on)
    page.locator("#lisa-kaasamine input[name=feedback_deadline]").fill(reply_by)
    with page.expect_response(
        lambda response: "/lisa/kaasamine/" in response.url and response.request.method == "POST"
    ) as caught:
        page.locator("#lisa-kaasamine button[type=submit]").click()
    assert caught.value.status == 200, f"the consultation was refused: {caught.value.status}"
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
    expect(row).to_contain_text("Ootame tagasisidet kuni")
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
    expect(_row(page)).not_to_contain_text("Ootame tagasisidet kuni")


def test_a_closed_teema_offers_no_correction(page, base_url):
    """The rule this feature keeps, and the one difference from a Sissekanne.

    Correcting a consultation is normal business work, so a finished file
    refuses it — the control is not offered and the route refuses a POST that
    reaches it anyway.
    """
    sign_in(page, base_url, MARTIN)
    create_matter(page, base_url, unique_title("Kaasamise paranduse katse: suletud"))
    _file_an_engagement(page)

    open_add_panel(page, "lisa-lopeta")
    page.locator("#lisa-lopeta .uxchip", has_text="Menetlus lõppes").click()
    with page.expect_response(
        lambda response: "/lisa/lopeta/" in response.url and response.request.method == "POST"
    ) as caught:
        page.locator("#lisa-lopeta button[type=submit]").click()
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
# `Lõpeta kaasamine` — the half of docs/adr/0085 only a browser can answer
# ---------------------------------------------------------------------------
#
# The two `Täpsus` scenarios that stood here went with the control they drove.
# `+ Kaasamine` and `Muuda` no longer offer the four precision chips
# (docs/adr/0085 §1), so a browser test that clicked one would be driving markup
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
    """docs/adr/0085 §6, end to end: the wait, the answers, the completed row.

    The disclosure is closed at rest, opens in place, and its save swaps the
    same element the row already is — so the completed record lands where the
    reader is looking, with no reload and no second line in the chronology.
    """
    sign_in(page, base_url, MARTIN)
    create_matter(page, base_url, unique_title("Kaasamise lõpetamise brauserikatse"))
    _file_an_engagement(page, reply_by=REPLY_BY)

    row = _row(page)
    expect(row).to_contain_text("Ootame tagasisidet kuni")
    panel = _finish_panel(page)
    expect(panel).to_have_count(1)
    expect(panel.locator("textarea[name=feedback_received]")).not_to_be_visible()

    panel.get_by_role("button", name="Lõpeta kaasamine", exact=True).click()
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
    _file_an_engagement(page, reply_by=REPLY_BY)

    panel = _finish_panel(page)
    panel.get_by_role("button", name="Lõpeta kaasamine", exact=True).click()
    with page.expect_response(
        lambda response: "/lopeta/" in response.url and response.request.method == "POST"
    ) as caught:
        panel.locator("button[type=submit]").click()
    assert caught.value.status == 200, f"an empty completion was refused: {caught.value.status}"
    page.wait_for_load_state("networkidle")

    row = _row(page)
    expect(row).to_contain_text("Tagasiside ootamine lõpetatud")
    expect(row.locator(".uxtl__finish")).to_have_count(0)
