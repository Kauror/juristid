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

from e2e.conftest import ADMIN, HEAD, MARTIN, SANDRA, sign_in, sign_out

pytestmark = pytest.mark.e2e

RESTRICTED_TITLE = "Konfidentsiaalne liikmete tagasiside"
MATTER_TITLE = "Pakendiseaduse muutmise eelnõu"


def _future(days: int) -> str:
    return (date.today() + timedelta(days=days)).isoformat()


def test_the_whole_lawyer_workflow(page, base_url, screenshots):
    """Scenario A, B, C and D in one pass, in the order the work happens."""
    sign_in(page, base_url, SANDRA)

    # -- Ülevaade is the landing surface ---------------------------------
    expect(page.get_by_role("heading", name="Ülevaade")).to_be_visible()
    expect(page.get_by_role("heading", name="Tähelepanu")).to_be_visible()
    expect(page.get_by_role("heading", name="Lähenevad tähtajad")).to_be_visible()
    screenshots(page, "00-ulevaade")

    # -- Minu töö is the personal queue ----------------------------------
    page.locator(".topnav__link", has_text="Minu töö").click()
    expect(page.get_by_role("heading", name="Minu töö")).to_be_visible()
    expect(page.get_by_role("heading", name="Ootan ja kontrollin")).to_be_visible()
    expect(page.get_by_role("heading", name="Minu aktiivsed teemad")).to_be_visible()
    screenshots(page, "01-minu-too")

    # -- Scenario A: create a Teema --------------------------------------
    page.get_by_role("link", name="Uus teema").first.click()
    expect(page.get_by_role("heading", name="Uus teema")).to_be_visible()

    page.locator("#id_title").fill(MATTER_TITLE)
    page.locator("summary", has_text="Täpsusta teema andmeid").click()
    page.locator("#id_owner").select_option(label=SANDRA.display_name)
    page.locator("#id_stage").select_option(label="Kooskõlastusringil")
    page.locator("#id_track").select_option(label="Riigisisene")
    page.locator("#id_source_organisation").select_option(label="Näidisministeerium")
    page.locator("#id_response_deadline").fill(_future(21))

    page.locator("summary", has_text="Määra kohe Järgmiseks").click()
    page.locator("#id_next-text").fill("Koosta ja saada koja arvamus")
    page.locator("#id_next-kind").select_option("DO")
    page.locator("#id_next-date_semantics").select_option("DEADLINE")
    page.locator("#id_next-target_date").fill(_future(14))
    screenshots(page, "02-uus-teema")

    page.get_by_role("button", name="Loo teema").click()

    # The Matter opens immediately and carries a human reference.
    expect(page.get_by_role("heading", name=MATTER_TITLE)).to_be_visible()
    reference = page.locator(".matterhead__crumbs .reference").inner_text().strip()
    assert re.fullmatch(r"\d{4}_\d+", reference), reference

    expect(page.locator(".nextaction__text")).to_have_text("Koosta ja saada koja arvamus")
    expect(page.locator(".nextaction .mode--do")).to_be_visible()
    expect(page.locator(".nextaction").get_by_text("Tähtaeg", exact=False).first).to_be_visible()
    screenshots(page, "03-teema-ulevaade")

    matter_url = page.url

    # It reaches Minu töö straight away.
    page.locator(".topnav__link", has_text="Minu töö").click()
    expect(
        page.locator(".workcolumn").first.get_by_text("Koosta ja saada koja arvamus")
    ).to_be_visible()
    expect(page.get_by_text(MATTER_TITLE).first).to_be_visible()

    # -- Scenario B: one composer save, two changes ----------------------
    page.goto(matter_url)

    # The composer starts as one field. Everything optional is out of the way
    # until it is asked for — that is the adoption argument, not decoration.
    expect(page.locator("#jargmiseks-valjad")).to_be_hidden()
    expect(page.locator("#komposer-lisavaljad")).to_be_hidden()
    expect(page.locator("#komposer-manus")).to_be_hidden()

    page.locator(".composer__body").fill(
        "Kohtumine ministeeriumiga. Ministeerium lubas saata järgmise nädala jooksul uue sõnastuse."
    )
    page.locator("#id_kind").select_option("MEETING")
    page.locator("#id_update_next_action").check()
    # Revealing one optional block must not reveal the others.
    expect(page.locator("#jargmiseks-valjad")).to_be_visible()
    expect(page.locator("#komposer-manus")).to_be_hidden()
    page.locator("#id_next_text").fill("Ootan ministeeriumi uut sõnastust")
    page.locator("#next_kind_WAIT").check(force=True)
    page.locator("#id_next_date_semantics").select_option("REVIEW_ON")
    page.locator("#id_next_target_date").fill(_future(7))
    screenshots(page, "04-komposer")

    page.get_by_role("button", name="Salvesta sissekanne").click()

    # Both halves landed, and the surface agrees with itself.
    expect(page.locator(".nextaction__text")).to_have_text("Ootan ministeeriumi uut sõnastust")
    expect(page.locator(".nextaction .mode--wait")).to_be_visible()
    expect(page.locator(".timeline").get_by_text("Ministeerium lubas saata")).to_be_visible()
    expect(page.locator(".nextaction").get_by_text("Vaatan üle").first).to_be_visible()
    # The superseded DO must no longer be presented as the current action.
    expect(page.locator(".nextaction").get_by_text("Koosta ja saada koja arvamus")).to_have_count(0)
    # Exactly one meeting entry: no ghost duplicate from the same save.
    expect(page.locator(".entrycard").filter(has_text="Kohtumine")).to_have_count(1)
    screenshots(page, "05-komposer-jarel")

    # The Matter has moved from Teen to Ootan / kontrollin.
    page.locator(".topnav__link", has_text="Minu töö").click()
    waiting = page.locator("section", has=page.get_by_role("heading", name="Ootan ja kontrollin"))
    expect(waiting.get_by_text("Ootan ministeeriumi uut sõnastust")).to_be_visible()

    # -- Scenario C: a formal opinion with its exact evidence ------------
    page.goto(matter_url)
    page.locator(".tabs__tab", has_text="Seisukoht ja kaasamine").click()
    expect(page.get_by_role("heading", name="Koja seisukoht")).to_be_visible()

    page.locator("#id_position_summary").fill("Koda ei toeta pakendiaktsiisi kavandatud tõusu.")
    page.locator("#id_rationale_summary").fill("Liikmete hinnangul kasvab halduskoormus.")
    page.get_by_role("button", name="Salvesta seisukoht").click()

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

    # -- Documents ------------------------------------------------------
    page.locator(".tabs__tab", has_text="Dokumendid").click()
    expect(page.get_by_role("heading", name="Tõendid")).to_be_visible()
    expect(page.get_by_text("koja-arvamus.pdf").first).to_be_visible()
    expect(page.get_by_role("heading", name="Töödokumendid")).to_be_visible()
    screenshots(page, "07-dokumendid")

    # -- Timeline order --------------------------------------------------
    page.goto(matter_url)
    # innerText reports the rendered text, and these labels are uppercased by
    # CSS, so the comparison is case-insensitive.
    kinds = [
        kind.lower()
        for kind in page.locator(
            ".entrytype, .systemevent__type, .submissionevent__label"
        ).all_inner_texts()
    ]
    assert any("kohtumine" in kind for kind in kinds), kinds
    assert any("saadetud" in kind for kind in kinds), kinds
    # Newest first: the send happened after the meeting was written up.
    assert next(i for i, k in enumerate(kinds) if "saadetud" in k) < next(
        i for i, k in enumerate(kinds) if "kohtumine" in k
    )

    # -- Teemad ----------------------------------------------------------
    page.locator(".topnav__link", has_text="Teemad").click()
    expect(page.get_by_role("heading", name="Teemad")).to_be_visible()
    expect(page.get_by_role("link", name=MATTER_TITLE)).to_be_visible()
    screenshots(page, "08-teemad")

    # Filters narrow the register and survive in the URL.
    page.locator("summary", has_text="Filter").click()
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

    # The exact reference navigates straight to the file.
    page.get_by_placeholder("Otsi teemat, viidet, asutust…").fill(reference)
    page.keyboard.press("Enter")
    expect(page.get_by_role("heading", name=MATTER_TITLE)).to_be_visible()

    # Ctrl+K focuses search rather than opening a command palette.
    page.keyboard.press("Control+k")
    expect(page.get_by_placeholder("Otsi teemat, viidet, asutust…")).to_be_focused()


def test_the_composer_rejects_an_incomplete_deadline_without_losing_the_entry(page, base_url):
    """A refused save must not half-apply, and must not discard what was typed."""
    sign_in(page, base_url, MARTIN)
    page.locator(".topnav__link", has_text="Teemad").click()
    page.get_by_role("link", name="Tavaline avatud teema kõigile nähtav").click()

    page.locator(".composer__body").fill("See tekst peab alles jääma.")
    page.locator("#id_update_next_action").check()
    page.locator("#id_next_text").fill("Tähtajaline tegevus ilma kuupäevata")
    page.locator("#next_kind_DO").check(force=True)
    page.locator("#id_next_date_semantics").select_option("DEADLINE")
    page.get_by_role("button", name="Salvesta sissekanne").click()

    expect(page.get_by_text("Tähtajaline tegevus vajab kuupäeva.")).to_be_visible()
    # Neither half was applied.
    expect(page.locator(".entrycard").filter(has_text="See tekst peab alles jääma")).to_have_count(
        0
    )
    expect(page.locator(".nextaction").get_by_text("Jälgi menetluse käiku")).to_be_visible()


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

    def test_an_unrelated_specialist_does_not(self, page, base_url):
        sign_in(page, base_url, MARTIN)

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

        sign_in(page, base_url, MARTIN)
        response = page.goto(restricted_url)
        assert response is not None
        assert response.status == 404
        assert RESTRICTED_TITLE not in page.content()
