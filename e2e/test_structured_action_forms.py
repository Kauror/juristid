"""A structured next step opens its canonical form, in a browser (docs/adr/0168, D4).

Three flows the owner reported, each with its cancellation and an invalid save:

1. «Kaasa liikmeid / küsi tagasisidet» → `Alusta` → the full `Alusta kaasamist`
   form under `LISA TEEMALE` → `Salvesta` → «Ootame tagasisidet».
2. «Ootame tagasisidet» → `Tehtud` → the full `Lisa tagasiside` form for that
   round → `Salvesta` → the wait is over.
3. «Saada Koja arvamus» → `Alusta` → the full `Koja arvamus` form → `Salvesta`
   records the sent opinion; opening it alone records nothing.

And without scripting, `Alusta` is the page with the same form opened.

The suite never reads the database (e2e/conftest.py): every state is read off
the page, after a reload where it matters that it was stored.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest
from playwright.sync_api import expect

from e2e.conftest import (
    SANDRA,
    chronology,
    create_matter,
    set_next_step,
    sign_in,
    wait_for_htmx,
)
from e2e.titles import unique_title

pytestmark = pytest.mark.e2e

PDF = b"%PDF-1.4 synthetic structured step"
CONSULT = "Kaasa liikmeid / küsi tagasisidet"
SEND = "Saada Koja arvamus"
RETIRED = "Koonda tagasiside ja kujunda Koja seisukoht"


def _zone(page):
    return page.locator("#praegune-tegevus")


def _suggestion(page):
    return page.locator("#praegune-tegevus #soovitus .curact__suggesttext")


def _waits(page):
    return page.locator("#praegune-tegevus [data-feedback-wait]")


def _dismiss_until(page, title: str) -> None:
    """`×` on each suggestion before ``title``, so ``title`` is suggested."""
    for _ in range(5):
        if _suggestion(page).inner_text().strip() == title:
            return
        _zone(page).get_by_role("button", name="Eemalda soovitus").click()
        wait_for_htmx(page)
    expect(_suggestion(page)).to_have_text(title)


def _alusta(page):
    return page.locator("#soovitus [data-open-canonical]")


def _launched_step(form) -> str:
    return form.locator("input[name=samm]").input_value()


def _reload(page) -> None:
    page.reload()
    page.wait_for_load_state("networkidle")


def _save(page, form, endpoint: str, *, double: bool = False) -> int:
    with page.expect_response(
        lambda r: r.url.endswith(endpoint) and r.request.method == "POST"
    ) as caught:
        button = form.get_by_role("button", name="Salvesta", exact=True)
        if double:
            button.dblclick()
        else:
            button.click()
    wait_for_htmx(page)
    return caught.value.status


def _focused_name(page) -> str:
    return page.evaluate("() => document.activeElement && document.activeElement.name || ''")


def _et(days: int = 0) -> str:
    on = date.today() + timedelta(days=days)
    return f"{on.day}.{on.month}.{on.year}"


# ---------------------------------------------------------------------------
# Flow 1 — «Kaasa liikmeid / küsi tagasisidet» → Alusta kaasamist
# ---------------------------------------------------------------------------


def test_flow_1_kaasa_liikmeid_opens_alusta_kaasamist_and_the_round_waits(page, base_url):
    sign_in(page, base_url, SANDRA)
    create_matter(page, base_url, unique_title("Struktureeritud kaasamine"))
    _dismiss_until(page, CONSULT)
    zone = _zone(page)
    expect(zone.locator("#alusta-samm")).to_have_count(0)  # no simplified editor

    # Opening: the full canonical form, scrolled to, the first field focused.
    _alusta(page).click()
    form = page.locator("#kaasamine-alusta")
    expect(form).to_be_visible()
    expect(page.locator("#lisa-kaasamine-valik")).to_be_checked()
    expect(page.locator("#kaasamine-alusta-valik")).to_be_checked()
    for label in ("Keda kaasad", "Kaasamise kuupäev", "Tagasisidet ootame kuni"):
        expect(form).to_contain_text(label)
    expect(form.locator("[name=smaily_url]")).to_be_visible()
    expect(form.locator("[name=alchemer_url]")).to_be_visible()
    expect(form.locator("input[type=file]")).to_have_count(1)
    assert _focused_name(page) == "audience"
    note = form.locator("[data-launch-note]")
    expect(note).to_be_visible()
    expect(note).to_contain_text(CONSULT)
    assert _launched_step(form)

    # Cancelling: `Loobu` closes it and forgets the step; nothing was written.
    note.get_by_role("link", name="Loobu").click()
    expect(form).to_be_hidden()
    assert _launched_step(form) == ""
    _reload(page)
    expect(_suggestion(page)).to_have_text(CONSULT)
    expect(_waits(page)).to_have_count(0)
    expect(chronology(page).locator(".uxtl__item", has_text="Kaasamine")).to_have_count(0)

    # An invalid save: refused in the form, still naming the step, nothing stored.
    _alusta(page).click()
    form = page.locator("#kaasamine-alusta")
    form.locator("[name=audience]").fill("")
    assert _save(page, form, "/lisa/kaasamine/") == 400
    form = page.locator("#kaasamine-alusta")
    expect(form).to_be_visible()
    expect(form).to_contain_text("Kirjuta, keda kaasad.")
    expect(form.locator("[data-launch-note]")).to_be_visible()
    assert _launched_step(form)
    _reload(page)
    expect(_suggestion(page)).to_have_text(CONSULT)
    expect(_waits(page)).to_have_count(0)

    # The save — pressed twice — records one round and the waiting state.
    _alusta(page).click()
    form = page.locator("#kaasamine-alusta")
    form.locator("[name=audience]").fill("Liikmed (sünteetiline)")
    assert _save(page, form, "/lisa/kaasamine/", double=True) in (200, 400)
    _reload(page)
    expect(_waits(page)).to_have_count(1)
    expect(_waits(page).first).to_contain_text("Ootame tagasisidet")
    expect(chronology(page)).to_contain_text("Liikmed (sünteetiline)")
    # The step is done: the next suggestion is the opinion — not «Koonda …».
    expect(_suggestion(page)).to_have_text(SEND)
    expect(_zone(page)).not_to_contain_text(RETIRED)
    expect(_zone(page)).not_to_contain_text(CONSULT)


# ---------------------------------------------------------------------------
# Flow 2 — «Ootame tagasisidet» → Tehtud → Lisa tagasiside
# ---------------------------------------------------------------------------


def _file_round(page, audience: str) -> None:
    _dismiss_until(page, CONSULT)
    _alusta(page).click()
    form = page.locator("#kaasamine-alusta")
    form.locator("[name=audience]").fill(audience)
    assert _save(page, form, "/lisa/kaasamine/") == 200
    expect(_waits(page)).to_have_count(1)


def test_flow_2_ootame_tagasisidet_tehtud_opens_lisa_tagasiside(page, base_url):
    sign_in(page, base_url, SANDRA)
    create_matter(page, base_url, unique_title("Struktureeritud tagasiside"))
    _file_round(page, "Liikmed (sünteetiline)")
    round_id = _waits(page).first.get_attribute("data-feedback-wait")

    # Opening: the full `Lisa tagasiside` form, this round chosen, caret in it.
    _waits(page).first.get_by_role("link", name="Tehtud").click()
    form = page.locator("#kaasamine-tagasiside")
    expect(form).to_be_visible()
    expect(page.locator("#kaasamine-tagasiside-valik")).to_be_checked()
    expect(form.locator("select[name=engagement]")).to_have_value(round_id)
    expect(form).to_contain_text("Saadud tagasiside")
    expect(form.locator("input[type=file]")).to_have_count(1)
    assert _focused_name(page) == "summary"

    # Cancelling: the chip closes the form; the round is still waiting.
    page.locator('label[for="lisa-kaasamine-valik"]').click()
    expect(form).to_be_hidden()
    _reload(page)
    expect(_waits(page)).to_have_count(1)

    # An invalid save: nothing came back, so nothing is recorded.
    _waits(page).first.get_by_role("link", name="Tehtud").click()
    form = page.locator("#kaasamine-tagasiside")
    form.locator("[name=summary]").fill("")
    assert _save(page, form, "/lisa/kaasamise-tagasiside/") == 400
    expect(page.locator("#kaasamine-tagasiside")).to_be_visible()
    _reload(page)
    expect(_waits(page)).to_have_count(1)

    # The save ends the wait — and only the save.
    _waits(page).first.get_by_role("link", name="Tehtud").click()
    form = page.locator("#kaasamine-tagasiside")
    form.locator("[name=summary]").fill("Kolm liiget vastasid (sünteetiline).")
    assert _save(page, form, "/lisa/kaasamise-tagasiside/") == 200
    expect(_waits(page)).to_have_count(0)
    _reload(page)
    expect(_waits(page)).to_have_count(0)
    expect(chronology(page)).to_contain_text("Kolm liiget vastasid")

    # Leave the Teema with an open step, as every file this suite creates must.
    set_next_step(page, "Kirjuta arvamuse mustand", _et(7))


# ---------------------------------------------------------------------------
# Flow 3 — «Saada Koja arvamus» → Koja arvamus
# ---------------------------------------------------------------------------


def test_flow_3_saada_koja_arvamus_opens_the_opinion_form(page, base_url):
    sign_in(page, base_url, SANDRA)
    create_matter(
        page,
        base_url,
        unique_title("Struktureeritud arvamus"),
        owner=SANDRA,
        sender="Näidisministeerium",
    )
    _dismiss_until(page, SEND)
    sent = chronology(page).locator(".uxtl__item", has_text="Arvamus välja")

    # Opening: the full form, nothing recorded by it.
    _alusta(page).click()
    form = page.locator("#arvamus-koja")
    expect(form).to_be_visible()
    expect(page.locator("#lisa-arvamus-valik")).to_be_checked()
    expect(page.locator("#arvamus-koja-valik")).to_be_checked()
    expect(form.locator("input[name=upload]")).to_have_count(1)
    expect(form.locator("[name=sent_on]")).to_be_visible()
    expect(form.locator("[data-launch-note]")).to_contain_text(SEND)
    _reload(page)
    expect(sent).to_have_count(0)
    expect(_suggestion(page)).to_have_text(SEND)

    # Cancelling.
    _alusta(page).click()
    form = page.locator("#arvamus-koja")
    form.locator("[data-launch-note]").get_by_role("link", name="Loobu").click()
    expect(form).to_be_hidden()
    _reload(page)
    expect(sent).to_have_count(0)
    expect(_suggestion(page)).to_have_text(SEND)

    # An invalid save: no file, so nothing is sent.
    _alusta(page).click()
    form = page.locator("#arvamus-koja")
    assert _save(page, form, "/lisa/koja-arvamus/") == 400
    expect(page.locator("#arvamus-koja")).to_be_visible()
    assert _launched_step(page.locator("#arvamus-koja"))
    _reload(page)
    expect(sent).to_have_count(0)
    expect(_suggestion(page)).to_have_text(SEND)

    # The save records the sent opinion once and finishes the step.
    _alusta(page).click()
    form = page.locator("#arvamus-koja")
    form.locator("input[name=upload]").set_input_files(
        [{"name": "arvamus.pdf", "mimeType": "application/pdf", "buffer": PDF}]
    )
    assert _save(page, form, "/lisa/koja-arvamus/") == 200
    _reload(page)
    expect(sent).to_have_count(1)
    expect(page.locator("#praegune-tegevus #soovitus")).to_have_count(0)
    # The send planned its 30-day check: the Teema has its next step.
    expect(_zone(page)).to_contain_text("Kontrolli, kas adressaat on Koja arvamusele vastanud")


# ---------------------------------------------------------------------------
# Without scripting — the link opens the same form on the server
# ---------------------------------------------------------------------------


def test_without_scripting_alusta_opens_the_form_on_the_server(
    page, base_url, browser, browser_context_args
):
    sign_in(page, base_url, SANDRA)
    url = create_matter(page, base_url, unique_title("Struktureeritud skriptita"))
    _dismiss_until(page, CONSULT)

    context = browser.new_context(**browser_context_args, java_script_enabled=False)
    try:
        scriptless = context.new_page()
        scriptless.goto(f"{base_url}/konto/arendus-sisselogimine/")
        scriptless.get_by_label(SANDRA.display_name, exact=False).check()
        scriptless.get_by_role("button", name="Logi sisse").click()
        scriptless.wait_for_url(f"{base_url}/minu-asjad/")
        scriptless.goto(url)

        scriptless.locator("#soovitus [data-open-canonical]").click()
        scriptless.wait_for_load_state("load")

        assert "ava=kaasamine" in scriptless.url
        form = scriptless.locator("#kaasamine-alusta")
        expect(form).to_be_visible()
        expect(form.locator("[data-launch-note]")).to_be_visible()
        assert form.locator("input[name=samm]").input_value()
    finally:
        context.close()

    # Opening wrote nothing; leave the Teema with an open step.
    _reload(page)
    expect(_suggestion(page)).to_have_text(CONSULT)
    expect(_waits(page)).to_have_count(0)
    set_next_step(page, "Vaata materjal üle", _et(7))
