"""The Stage-1 workflow, end to end in a real browser.

One scenario walks the whole path a lawyer takes with a new draft: create,
assign, stage, `Järgmiseks`, entry, submission, evidence, send, then find it
again. The others hold the lines that matter — atomic composer saves, and a
restricted Matter that stays unreachable from every direction.

Screenshots are captured at 1440px along the way and uploaded as CI artifacts,
because the machine that writes this code cannot open a browser.
"""

from __future__ import annotations

import re
from datetime import date, timedelta

import pytest
from playwright.sync_api import expect

from e2e.conftest import (
    ADMIN,
    HEAD,
    MARTIN,
    READER,
    SANDRA,
    open_composer,
    sign_in,
    sign_out,
)

pytestmark = pytest.mark.e2e

RESTRICTED_TITLE = "Konfidentsiaalne liikmete tagasiside"
MATTER_TITLE = "Pakendiseaduse muutmise eelnõu"


def _future(days: int) -> str:
    """A date the way the application now reads and writes one: `7.9.2026`.

    ISO would still parse — the field accepts both — but a browser test that
    typed ISO would not be exercising what a lawyer types
    (app/core/dates.py).
    """
    on = date.today() + timedelta(days=days)
    return f"{on.day}.{on.month}.{on.year}"


def test_the_whole_lawyer_workflow(page, base_url, screenshots):
    """Scenario A, B, C and D in one pass, in the order the work happens."""
    sign_in(page, base_url, SANDRA)

    # -- Ülevaade is the landing surface ---------------------------------
    expect(page.get_by_role("heading", name="Ülevaade")).to_be_visible()
    # Not `exact`: the heading carries its count, so its accessible name is
    # "Tähtajad" followed by a number that changes with the seeded data.
    expect(page.get_by_role("heading", name="Tähtajad")).to_be_visible()
    expect(page.get_by_role("heading", name="Vajab sekkumist")).to_be_visible()
    screenshots(page, "00-ulevaade")

    # -- Minu töö is the personal queue ----------------------------------
    page.locator(".topnav__link", has_text="Minu töö").click()
    expect(page.get_by_role("heading", name="Minu töö")).to_be_visible()
    # One dated list, whatever the mode. `Järgmise tegevuseta` is beside it
    # and deliberately not in it: an absence has no position in time.
    #
    # `Minu aktiivsed teemad` is gone with the rebuild. Minu töö answers what
    # to do next; browsing the portfolio is what Teemad is for, and a register
    # table at the foot of the page made it a second, worse register.
    expect(page.get_by_role("heading", name="Ootan ja kontrollin")).to_have_count(0)
    expect(page.get_by_role("heading", name="Järgmise tegevuseta")).to_be_visible()
    screenshots(page, "01-minu-too")

    # -- Scenario A: create a Teema --------------------------------------
    page.get_by_role("link", name="Uus teema").first.click()
    expect(page.get_by_role("heading", name="Uus teema")).to_be_visible()

    page.locator("#id_title").fill(MATTER_TITLE)

    # Owner and sender are visible choices, not selects — and nothing on this
    # page is behind a disclosure any more, which is the Uus teema redesign:
    # the whole form is on screen at load and this walk never clicks to reveal
    # a field (Uus teema redesign §3).
    page.get_by_role("radio", name=SANDRA.short_name, exact=True).check()
    # A checkbox, not a radio: a matter may have arrived from several bodies
    # (Wave-2 multiple senders, ADR 0025).
    page.get_by_role("checkbox", name="Näidisministeerium").check()

    # Hetkeseis and Menetlusliik are visible radio chips, not dropdowns: each
    # holds one value, and the control says so (Agent-UI brief 5.1).
    page.get_by_role("radio", name="Kooskõlastusringil", exact=True).check()
    page.get_by_role("radio", name="Riigisisene", exact=True).check()
    page.locator("#id_response_deadline").fill(_future(21))

    page.locator("#id_next-text").fill("Koosta ja saada koja arvamus")
    # The kind is a card, and the date meaning is derived from it rather than
    # asked as a second question. DEADLINE is what DO derives to
    # (app/workflow/enums.py, `default_date_semantics`).
    page.locator('input[name="next-kind"][value="DO"]').check()
    page.locator("#id_next-target_date").fill(_future(14))
    screenshots(page, "02-uus-teema")

    page.get_by_role("button", name="Loo teema").click()

    # The Matter opens immediately, named by its title.
    #
    # The crumb is one level and stops at "Teemad". It used to end in the
    # technical reference — `2026_10` — which named the record and not the
    # subject, and the subject is the <h1> directly beneath it (human QA §8).
    expect(page.get_by_role("heading", name=MATTER_TITLE)).to_be_visible()
    crumbs = page.locator(".matterhead__crumbs")
    expect(crumbs.get_by_role("link", name="Teemad")).to_be_visible()
    assert not re.search(r"\d{4}_\d+", crumbs.inner_text()), crumbs.inner_text()

    expect(page.locator(".uxnext__text")).to_have_text("Koosta ja saada koja arvamus")
    expect(page.locator(".uxnext .modechip--do")).to_be_visible()
    # A TEEN date is a deadline and prints as a bare date — the row says which
    # of the three meanings it carries by the chip beside it, not by a label
    # repeated on every line (Teema redesign §8.2).
    expect(page.locator(".uxnext__flag")).to_be_visible()
    screenshots(page, "03-teema-ulevaade")

    matter_url = page.url

    # It reaches Minu töö straight away.
    #
    # Somewhere in the one list, not in a nominated band: the list is banded by
    # date now, so which band a step lands in depends on the date it was given
    # and on what else this persona is carrying. What must be true is that the
    # step is on the page, in a row, saying which mode it is (Teema QA §3).
    page.locator(".topnav__link", has_text="Minu töö").click()
    row = page.locator(".workrow2").filter(has_text="Koosta ja saada koja arvamus")
    expect(row).to_have_count(1)
    expect(row.locator(".mode--do")).to_be_visible()
    expect(page.get_by_text(MATTER_TITLE).first).to_be_visible()

    # -- Scenario B: one composer save, two changes ----------------------
    page.goto(matter_url)

    # The composer is one field and three chips. Everything else is out of the
    # way until it is asked for — that is the adoption argument, not decoration.
    expect(page.locator("#koostaja-manus")).to_be_hidden()
    expect(page.locator("#koostaja-tahtaeg")).to_be_hidden()
    expect(page.locator("#koostaja-lopetamine")).to_be_hidden()
    # And `+ Kaasamine` is not among them. Kaasamine has exactly one path — its
    # own section, with the fields the record actually needs — and a second,
    # thinner one in the composer was two ways to create the same thing
    # (Teema QA §8).
    expect(page.locator("#koostaja-kaasamine")).to_have_count(0)
    expect(page.locator(".disclosure-chip", has_text="+ Kaasamine")).to_have_count(0)

    # There is no second box asking for the next step's wording. The
    # description *is* the next step, which is the redesign's central claim.
    expect(page.locator("[name='next_text']")).to_have_count(0)

    open_composer(page)
    page.locator(".composer__body").fill("Ootan ministeeriumi uut sõnastust")
    page.locator("#next_kind_WAIT").check(force=True)
    # Revealing one optional block must not reveal the others.
    page.locator(".disclosure-chip", has_text="+ Manus").click()
    expect(page.locator("#koostaja-manus")).to_be_visible()
    expect(page.locator("#koostaja-lopetamine")).to_be_hidden()
    page.locator("#id_kind").select_option("MEETING")
    page.locator("#id_next_date").fill(_future(7))
    screenshots(page, "04-komposer")

    page.locator("[data-composer-submit]").click()

    # Both halves landed, and the surface agrees with itself.
    expect(page.locator(".uxnext__text")).to_have_text("Ootan ministeeriumi uut sõnastust")
    expect(page.locator(".uxnext .modechip--wait")).to_be_visible()
    # A WAIT date is a review date and is labelled as one, never as a deadline.
    expect(page.locator(".uxnext__flag")).to_contain_text("vaatan üle")
    # The superseded DO must no longer be presented as the current action.
    expect(page.locator(".uxnext").get_by_text("Koosta ja saada koja arvamus")).to_have_count(0)

    # The chronology is collapsed by default: a Matter opens on what to do
    # next, not on its history.
    timeline = page.locator("#ajajoon")
    expect(timeline).not_to_have_attribute("open", "")
    timeline.locator(".accordion__head").click()
    # One professional update, one line — and the sentence says what was done.
    expect(timeline.get_by_text("lisas märkuse ja määras järgmise sammu")).to_be_visible()
    expect(page.locator(".uxtl__body").filter(has_text="Ootan ministeeriumi")).to_have_count(1)
    screenshots(page, "05-komposer-jarel")

    # The Matter is now OOTAN — and it is still in the one list, banded by its
    # date rather than moved to a column of its own. The row carries the mode
    # chip that says what the date means (Teema QA §3).
    page.locator(".topnav__link", has_text="Minu töö").click()
    expect(page.get_by_role("heading", name="Ootan ja kontrollin")).to_have_count(0)
    row = page.locator(".workrow2").filter(has_text="Ootan ministeeriumi uut sõnastust")
    expect(row).to_have_count(1)
    expect(row.locator(".mode--wait")).to_be_visible()

    # -- Scenario C: a formal opinion with its exact evidence ------------
    #
    # The rail states the position and links to where it is written. A 300px
    # read-first column has no room for two textareas and no business holding
    # them (Teema QA §1).
    page.goto(matter_url)
    page.locator("#koja-seisukoht").get_by_role("link", name="Lisa seisukoht").click()
    page.locator("#id_position_summary").fill("Koda ei toeta pakendiaktsiisi kavandatud tõusu.")
    page.locator("#id_rationale_summary").fill("Liikmete hinnangul kasvab halduskoormus.")
    page.locator(".positionpanel").get_by_role("button", name="Salvesta seisukoht").click()

    # Saved, and read back from the rail on the main view.
    page.goto(matter_url)
    expect(page.locator(".railposition__text")).to_contain_text("Koda ei toeta")
    # Exactly one Koja seisukoht on the page. The full-width block the redesign
    # put in the main column is gone, not duplicated into the rail.
    expect(page.locator("#koja-seisukoht")).to_have_count(1)
    expect(page.locator(".positionblock")).to_have_count(0)

    # The formal Submission workflow is a quiet link in the rail, never a tab.
    # `Loe edasi` now, because there is a position to read on.
    page.locator("#koja-seisukoht .railposition__more").click()
    page.locator("summary", has_text="Uus arvamus").click()
    page.locator("#id_title").fill("Koja arvamus pakendiseaduse eelnõule")
    page.locator("#id_kind").select_option("FORMAL_OPINION")
    page.locator("#id_recipients").select_option(label="Näidisministeerium")
    page.get_by_role("button", name="Loo arvamus").click()

    expect(page.locator(".submission__title")).to_have_text("Koja arvamus pakendiseaduse eelnõule")
    expect(page.locator(".badge--draft")).to_be_visible()

    # Sending without evidence is not offered: the control is the upload.
    expect(page.get_by_role("button", name="Märgi saadetuks")).to_have_count(0)

    page.get_by_label("Lõplik saadetud fail").set_input_files(
        files=[
            {
                "name": "koja-arvamus.pdf",
                "mimeType": "application/pdf",
                "buffer": b"%PDF-1.4 synthetic final opinion",
            }
        ]
    )
    page.get_by_role("button", name="Lisa lõplik tõend").click()
    expect(page.get_by_text("koja-arvamus.pdf").first).to_be_visible()

    page.get_by_role("button", name="Märgi saadetuks").click()
    expect(page.locator(".badge--sent")).to_be_visible()
    expect(page.locator(".submission__meta").get_by_text("Saadetud")).to_be_visible()
    expect(page.get_by_text("koja-arvamus.pdf").first).to_be_visible()
    screenshots(page, "06-seisukoht-ja-kaasamine")

    # A second submission under the same Matter is ordinary, not a workaround.
    page.locator("summary", has_text="Uus arvamus").click()
    page.locator("#id_title").fill("Täiendav arvamus komisjonile")
    page.locator("#id_kind").select_option("SUPPLEMENTARY_OPINION")
    page.get_by_role("button", name="Loo arvamus").click()
    expect(
        page.locator(".submission__title", has_text="Täiendav arvamus komisjonile")
    ).to_be_visible()
    # Two submissions under one Matter is the ordinary case, not a workaround.
    expect(page.locator(".submission")).to_have_count(2)

    # -- The sent opinion reaches the main view --------------------------
    #
    # Once, in the rail beside the position it argued. The separate sent-opinion
    # strip in the main column said the same thing a second time (Teema QA §1.2).
    page.goto(matter_url)
    opinion = page.locator(".railposition__opinion")
    expect(opinion).to_be_visible()
    expect(opinion.get_by_text("koja-arvamus.pdf")).to_be_visible()
    expect(page.locator(".sentstrip")).to_have_count(0)

    # -- Documents ------------------------------------------------------
    page.locator(".tabs__tab", has_text="Dokumendid").click()
    expect(page.get_by_role("heading", name="Failid")).to_be_visible()
    expect(page.get_by_text("koja-arvamus.pdf").first).to_be_visible()
    # Working references are an accordion, closed, and visibly not evidence.
    working = page.locator("#toodokumendid")
    expect(working).not_to_have_attribute("open", "")
    expect(working.get_by_text("Töödokumendid")).to_be_visible()
    screenshots(page, "07-dokumendid")

    # -- Timeline order --------------------------------------------------
    page.goto(matter_url)
    page.locator("#ajajoon .accordion__head").click()
    # innerText reports the rendered text, and these labels are uppercased by
    # CSS, so the comparison is case-insensitive.
    kinds = [
        kind.lower()
        for kind in page.locator(".uxtl__did, .uxtl__kind, .systemevent__type").all_inner_texts()
    ]
    assert any("märkuse" in kind for kind in kinds), kinds
    assert any("saadetud" in kind for kind in kinds), kinds
    # Newest first: the send happened after the meeting was written up.
    assert next(i for i, k in enumerate(kinds) if "saadetud" in k) < next(
        i for i, k in enumerate(kinds) if "märkuse" in k
    )

    # -- Teemad ----------------------------------------------------------
    page.locator(".topnav__link", has_text="Teemad").click()
    expect(page.get_by_role("heading", name="Teemad")).to_be_visible()
    expect(page.get_by_role("link", name=MATTER_TITLE)).to_be_visible()
    screenshots(page, "08-teemad")

    # Filters narrow the register and survive in the URL. The disclosure is
    # called `Täpsem otsing` since Stage 2E.1 — it now holds the date ranges and
    # the institution chooser as well, and "+ Filter" undersold it.
    page.locator("summary", has_text="Täpsem otsing").click()
    page.locator("select[name='ulatus']").select_option("minu")
    page.locator("select[name='hetkeseis']").select_option("consultation")
    page.get_by_role("button", name="Filtreeri").click()
    expect(page.get_by_role("link", name=MATTER_TITLE)).to_be_visible()
    assert "ulatus=minu" in page.url
    assert "hetkeseis=consultation" in page.url
    # Active filters render as removable chips built from the same query string.
    expect(page.locator(".filterchip").first).to_be_visible()

    # Removing a chip clears exactly that filter and leaves the rest.
    page.locator(".filterchip", has_text="Ulatus").click()
    assert "ulatus=minu" not in page.url
    assert "hetkeseis=consultation" in page.url

    page.locator(".filterchip", has_text="Hetkeseis").click()
    assert "hetkeseis=consultation" not in page.url

    # FULL and ARCHIVE coexist in the same register. An archive row carries no
    # stage, so it only appears once the stage filter is gone.
    page.get_by_role("link", name=re.compile("^Kõik")).click()
    expect(page.locator(".badge--archive").first).to_be_visible()

    # -- Scenario D: find it again ---------------------------------------
    page.get_by_placeholder("Otsi teemat, viidet, asutust…").fill("pakendiseaduse")
    page.keyboard.press("Enter")
    expect(page.get_by_role("heading", name="Otsing")).to_be_visible()
    # More than one row now, and that is the Stage-2B change: the entry and the
    # sent opinion written earlier in this test are indexed too. Each result
    # says which kind of thing it is, so a mixed list stays readable.
    expect(page.get_by_role("link", name=re.compile("Pakendiseaduse")).first).to_be_visible()
    expect(page.locator(".badge--source").first).to_be_visible()
    screenshots(page, "09-otsing")

    page.get_by_role("link", name=re.compile("Pakendiseaduse")).first.click()
    expect(page.get_by_role("heading", name=MATTER_TITLE)).to_be_visible()

    # There used to be a second search here, typing this Matter's technical
    # reference and expecting the redirect straight to the file. The reference
    # was read a moment earlier out of `Muuda teemat` — and that panel no longer
    # shows it, because `Muuda teemat` is the ordinary application. Keeping the
    # value on a page so that a browser test could scrape it would have been the
    # same leak wearing a different hat.
    #
    # Nothing about exact-reference search changed, and none of its coverage
    # went with the step. It is a property of the search layer and is proved
    # against the database, where the reference legitimately lives:
    # `tests/test_search_authorization.py` asserts the 302 and its target, and
    # `tests/test_work_surface_cleanup.py` asserts the register answers a
    # reference typed into this same box. What this file is for — that the box
    # in the bar takes a lawyer from a query to a file — is the scenario
    # directly above (review of PR #72, §4).
    page.goto(f"{base_url}/ulevaade/")
    page.wait_for_url(f"{base_url}/ulevaade/")

    # Ctrl+K focuses search rather than opening a command palette.
    #
    # `app.js` is loaded with `defer`, so its keydown listener is attached after
    # parsing and before DOMContentLoaded. Waiting for that state is therefore
    # the exact guarantee this needs: a heading can be visible mid-parse, while
    # the handler that makes this shortcut work does not exist yet.
    page.wait_for_load_state("domcontentloaded")
    page.keyboard.press("Control+k")
    expect(page.get_by_placeholder("Otsi teemat, viidet, asutust…")).to_be_focused()


def test_the_composer_rejects_an_incomplete_deadline_without_losing_the_entry(page, base_url):
    """A refused save must not half-apply, and must not discard what was typed."""
    sign_in(page, base_url, MARTIN)
    page.locator(".topnav__link", has_text="Teemad").click()
    page.get_by_role("link", name="Tavaline avatud teema kõigile nähtav").click()

    open_composer(page)
    page.locator(".composer__body").fill("See tekst peab alles jääma.")
    # No date meaning chosen: TEEN derives to *Tähtaeg*, and a deadline with no
    # date is still the one combination the server refuses. Left unstated on
    # purpose — this is the path a lawyer who never opens the disclosure takes,
    # and it is why the period control is the one date box in the product that
    # does *not* pre-fill with today: a default would answer this refusal with a
    # deadline nobody chose (app/matters/forms.py, `_precision_fields`).
    page.locator("#next_kind_DO").check(force=True)
    expect(page.locator("#id_next_date")).to_have_value("")
    page.locator("[data-composer-submit]").click()

    expect(page.get_by_text("Tähtajaline tegevus vajab kuupäeva.")).to_be_visible()
    # Neither half was applied.
    expect(page.locator(".uxtl__body").filter(has_text="See tekst peab alles jääma")).to_have_count(
        0
    )
    expect(page.locator(".uxnext").get_by_text("Jälgi menetluse käiku")).to_be_visible()


class TestRestrictedMatterIsUnreachable:
    """Scenario E, from every direction a user could try."""

    def test_the_owner_sees_it(self, page, base_url):
        sign_in(page, base_url, SANDRA)
        page.locator(".topnav__link", has_text="Teemad").click()
        expect(page.get_by_role("link", name=RESTRICTED_TITLE)).to_be_visible()

    def test_the_department_head_sees_it(self, page, base_url):
        sign_in(page, base_url, HEAD)
        page.locator(".topnav__link", has_text="Teemad").click()
        expect(page.get_by_role("link", name=RESTRICTED_TITLE)).to_be_visible()

    def test_a_reader_does_not(self, page, base_url):
        sign_in(page, base_url, READER)

        # Not on Ülevaade — not in its attention list, and not in its counts.
        # Signing in lands here now, which makes this the stronger check: a
        # restricted Matter must not reach a total either.
        expect(page.get_by_role("heading", name="Ülevaade")).to_be_visible()
        expect(page.get_by_text("Konfidentsiaalne järgmine samm")).to_have_count(0)
        expect(page.get_by_text(RESTRICTED_TITLE)).to_have_count(0)

        # Not in the register.
        page.locator(".topnav__link", has_text="Teemad").click()
        expect(page.get_by_role("link", name=RESTRICTED_TITLE)).to_have_count(0)

        # Not in search, and no snippet leaks. `not_to_contain_text` retries
        # until the navigation settles; reading `page.content()` outright races
        # the redirect and reports a Playwright error instead of a verdict.
        page.get_by_placeholder("Otsi teemat, viidet, asutust…").fill("konfidentsiaalne")
        page.keyboard.press("Enter")
        expect(page.get_by_text("Vasteid ei leitud", exact=False)).to_be_visible()
        expect(page.locator("body")).not_to_contain_text(RESTRICTED_TITLE)
        expect(page.locator("body")).not_to_contain_text("pakendiaktsiisi")

    def test_a_technical_administrator_does_not_either(self, page, base_url):
        """Administering the system is not permission to read the content."""
        sign_in(page, base_url, ADMIN)
        page.locator(".topnav__link", has_text="Teemad").click()
        expect(page.get_by_role("link", name=RESTRICTED_TITLE)).to_have_count(0)

        page.get_by_placeholder("Otsi teemat, viidet, asutust…").fill("konfidentsiaalne")
        page.keyboard.press("Enter")
        expect(page.get_by_role("heading", name="Otsing")).to_be_visible()
        expect(page.locator("body")).not_to_contain_text(RESTRICTED_TITLE)

    def test_the_direct_url_is_not_reachable(self, page, base_url):
        """Guessing the address must behave exactly like the record not existing."""
        sign_in(page, base_url, SANDRA)
        page.locator(".topnav__link", has_text="Teemad").click()
        page.get_by_role("link", name=RESTRICTED_TITLE).click()
        restricted_url = page.url
        sign_out(page, base_url)

        sign_in(page, base_url, READER)
        response = page.goto(restricted_url)
        assert response is not None
        assert response.status == 404
        assert RESTRICTED_TITLE not in page.content()
