"""`Tööplaan` in a browser (docs/adr/0133).

`tests/test_work_plan.py` holds the contract. This file holds what only a
rendered page settles: that a lawyer can walk a new law's ordinary course with
the controls the page actually draws —

    A. a new Teema with a far `Arvamuse tähtaeg` opens on five faint suggestions,
       no current step, and the deadline in the header;
    B. `Alusta` makes the first suggestion the one current step, with no day;
    C. `✓ Tehtud` → `Mida tegid?` → `Järgmisena` finishes it and starts the next
       in one save, one `Teema käik` row;
    D. the overview step is finished by publishing the overview from its own form;
    E. the consultation step is finished by asking the members, and the round
       stays open with nothing started after it;
    F. a custom step is inserted, started and finished;
    G. the opinion step is finished by registering the opinion;

and, on Teemas of their own: skipping, repeating, adopting the standard plan on
a file that had none, closing and reopening, and the layout at 1024px.

Each test files its own Matter: these write Matter-level facts and the seeded
world is shared across a shard.
"""

from __future__ import annotations

import re
from datetime import date, timedelta

import pytest
from playwright.sync_api import expect

from e2e.conftest import (
    KAIK_ROW,
    SANDRA,
    close_through_stage,
    create_matter,
    open_done_form,
    open_kaik_row,
    sign_in,
    unique_title,
    wait_for_htmx,
)

pytestmark = pytest.mark.e2e

MINISTRY = "Näidisministeerium"
STANDARD = (
    "Tutvu materjaliga",
    "Koosta kodulehe ülevaade",
    "Kaasa liikmeid / küsi tagasisidet",
    "Koonda tagasiside ja kujunda Koja seisukoht",
    "Saada Koja arvamus",
)


def _day(offset: int) -> str:
    day = date.today() + timedelta(days=offset)
    return f"{day.day}.{day.month}.{day.year}"


def _plan(page):
    return page.locator("#tooplaan")


def _zone(page):
    return page.locator("#praegune-tegevus")


def _row(page, title: str):
    return _plan(page).locator(".workplan__step", has_text=title)


def _new_teema(page, base_url: str, title: str, *, deadline_in: int | None = None) -> str:
    page.goto(f"{base_url}/teemad/uus/")
    page.wait_for_load_state("networkidle")
    page.fill("#id_title", title)
    if deadline_in is not None:
        page.fill("#id_response_deadline", _day(deadline_in))
    page.get_by_role("button", name="Loo teema").click()
    page.wait_for_url(re.compile(r"/teemad/[0-9a-f-]{36}/$"))
    page.wait_for_load_state("networkidle")
    return page.url


def _start_from_plan(page, title: str) -> None:
    """A plan row's one-click `Alusta`."""
    _plan(page).get_by_role("button", name=f"Alusta: {title}").click()
    wait_for_htmx(page)
    expect(_zone(page).locator(".curact__task .curact__text").first).to_have_text(title)


def _done(page, result: str, *, then: str | None = None) -> None:
    """`✓ Tehtud`, the result, and — when given — the next step, in one save."""
    open_done_form(page)
    _zone(page).locator("#id_praegune_body").fill(result)
    if then is not None:
        _zone(page).locator(".curact__next").get_by_label(then, exact=True).check()
    _zone(page).locator("#praegune-tegevus-vorm button[type=submit]").click()
    wait_for_htmx(page)


def _typed_panel(page):
    """The current step's own form, under `PRAEGUNE TEGEVUS`."""
    panel = page.locator("#samm-toiming")
    if panel.get_attribute("open") is None:
        panel.locator("> summary").click()
    return panel


def test_a_new_law_walks_its_ordinary_course(page, base_url, screenshots):
    sign_in(page, base_url, SANDRA)
    title = unique_title("Tööplaani teekond")
    url = _new_teema(page, base_url, title, deadline_in=90)

    # A. Five faint suggestions, nothing current, the deadline the obligation.
    zone = _zone(page)
    expect(zone).to_contain_text("Järgmine samm on määramata")
    expect(zone).not_to_contain_text("Koostan arvamuse")
    expect(zone.locator(".curact__suggest")).to_contain_text("Tutvu materjaliga")
    rows = _plan(page).locator(".workplan__step")
    expect(rows).to_have_count(5)
    for index, step in enumerate(STANDARD):
        expect(rows.nth(index)).to_contain_text(step)
        expect(rows.nth(index).locator(".workplan__state")).to_have_text("Soovitus")
    expect(page.locator(".metaline").first).to_contain_text(_day(90))
    expect(_plan(page)).not_to_contain_text("%")
    screenshots(page, "tooplaan-uus-teema")

    # A suggestion is on nobody's list.
    page.goto(f"{base_url}/minu-asjad/")
    page.wait_for_load_state("networkidle")
    expect(page.locator(".workrow2").filter(has_text=title).filter(has_text="Tutvu")).to_have_count(
        0
    )
    page.goto(url)
    page.wait_for_load_state("networkidle")

    # B. The suggestion's own `Alusta`: the step's words, no day.
    zone.locator("#alusta-samm > summary").click()
    expect(page.locator("#id_alusta_text")).to_have_value("Tutvu materjaliga")
    expect(page.locator("#id_alusta_target_date")).to_have_value("")
    zone.locator("#alusta-samm button[type=submit]").click()
    wait_for_htmx(page)
    expect(zone.locator(".curact__text").first).to_have_text("Tutvu materjaliga")
    expect(zone.locator(".curact__date")).to_have_text("Kuupäev määramata")
    expect(_row(page, "Tutvu materjaliga").locator(".workplan__state")).to_have_text("Praegu")
    # The far deadline stays visible under the current step.
    expect(zone.locator(".curact__owed")).to_contain_text("Arvamuse tähtaeg")
    screenshots(page, "tooplaan-praegune-samm")

    # C. Done, and the next step chosen in the same save.
    open_done_form(page)
    expect(zone.locator(".curact__next")).to_contain_text("Koosta kodulehe ülevaade")
    expect(zone.locator(".curact__next").get_by_label("Praegu ei määra")).to_be_checked()
    screenshots(page, "tooplaan-tehtud-ja-jargmine")
    _done(
        page, "Lugesin materjali läbi ja märkisin olulised kohad.", then="Koosta kodulehe ülevaade"
    )
    expect(zone.locator(".curact__text").first).to_have_text("Koosta kodulehe ülevaade")
    expect(_row(page, "Tutvu materjaliga").locator(".workplan__state")).to_have_text("Tehtud")
    notes = page.locator(KAIK_ROW).filter(has_text="Lugesin materjali läbi")
    expect(notes).to_have_count(1)
    expect(page.locator("#ajalugu-loend")).not_to_contain_text("tööplaan")

    # D. The overview step: its own form, under it. A save with no address is
    #    refused and finishes nothing.
    panel = _typed_panel(page)
    expect(panel.locator("> summary")).to_have_text("+ Ülevaade / uudis")
    panel.locator("[name=url]").fill("")
    panel.get_by_role("button", name="Lisa ülevaade / uudis").click()
    wait_for_htmx(page)
    expect(zone.locator(".curact__text").first).to_have_text("Koosta kodulehe ülevaade")
    panel = _typed_panel(page)
    panel.locator("[name=url]").fill("https://www.koda.ee/uudised/tooplaani-proov")
    panel.get_by_role("button", name="Lisa ülevaade / uudis").click()
    wait_for_htmx(page)
    expect(_row(page, "Koosta kodulehe ülevaade").locator(".workplan__state")).to_have_text(
        "Tehtud"
    )
    expect(zone).to_contain_text("Järgmine samm on määramata")
    expect(zone.locator(".curact__suggest")).to_contain_text("Kaasa liikmeid")
    overview = page.locator(KAIK_ROW).filter(has_text="Ülevaade / uudis")
    expect(overview).to_have_count(1)
    open_kaik_row(overview)
    expect(overview).to_contain_text("Tehtud")
    # The one clause is the `Mida tegid?` note's own; the overview's completion
    # folded under the overview and wrote no row of its own.
    expect(page.locator(KAIK_ROW).filter(has_text="märkis eelmise sammu tehtuks")).to_have_count(1)

    # E. The consultation: asking is the step, collecting is the round.
    _start_from_plan(page, "Kaasa liikmeid / küsi tagasisidet")
    panel = _typed_panel(page)
    panel.locator("[name=audience]").fill("Tööplaani liikmed")
    panel.get_by_role("button", name="Salvesta").click()
    wait_for_htmx(page)
    expect(_row(page, "Kaasa liikmeid").locator(".workplan__state")).to_have_text("Tehtud")
    expect(zone).to_contain_text("Ootame tagasisidet")
    expect(zone.locator(".curact__suggest")).to_contain_text("Koonda tagasiside")
    expect(zone.locator("#tehtud")).to_have_count(0)
    screenshots(page, "tooplaan-kaasamine-avatud")
    round_row = page.locator(KAIK_ROW).filter(has_text="Tööplaani liikmed")
    expect(round_row).to_have_count(1)
    open_kaik_row(round_row)
    expect(round_row).to_contain_text("Tehtud")
    waiting = page.locator("#ajalugu-loend .uxtl__ms-body").filter(has_text="Tööplaani liikmed")
    waiting.get_by_text("Lõpeta kaasamine", exact=True).click()
    waiting.locator("[name=feedback_received]").fill("Kaks liiget vastasid.")
    waiting.get_by_role("button", name="Salvesta ja lõpeta").click()
    wait_for_htmx(page)
    page.reload()
    page.wait_for_load_state("networkidle")
    # Nothing is started for anybody: the next suggestion is there to start.
    expect(zone).to_contain_text("Järgmine samm on määramata")
    expect(zone.locator(".curact__suggest")).to_contain_text("Koonda tagasiside")

    # F. A custom step, put before the opinion, and walked.
    page.locator("#lisa-samm > summary").click()
    page.locator("#id_samm_title").fill("Kohtun ministeeriumiga")
    page.locator("#id_samm_before").select_option(label="Enne: Saada Koja arvamus")
    page.locator("#lisa-samm").get_by_role("button", name="Lisa samm").click()
    wait_for_htmx(page)
    titles = _plan(page).locator(".workplan__title").all_inner_texts()
    assert titles[-2:] == ["Kohtun ministeeriumiga", "Saada Koja arvamus"], titles
    expect(_row(page, "Kohtun ministeeriumiga").locator(".workplan__state")).to_have_text("Plaanis")
    expect(zone).to_contain_text("Järgmine samm on määramata")
    _start_from_plan(page, "Koonda tagasiside ja kujunda Koja seisukoht")
    _done(page, "Koondasin vastused, seisukoht on kujundatud.", then="Kohtun ministeeriumiga")
    _done(page, "Kohtumine toimus, ministeerium kaalub üleminekuaega.", then="Saada Koja arvamus")

    # G. The opinion: registered from the step's own form, no tick to tick.
    panel = _typed_panel(page)
    expect(panel.locator("> summary")).to_have_text("+ Koja arvamus")
    expect(panel.get_by_role("checkbox", name=re.compile("Märgi praegune tegevus"))).to_have_count(
        0
    )
    panel.locator("input[name=upload]").set_input_files(
        {"name": "Koja_arvamus.pdf", "mimeType": "application/pdf", "buffer": b"%PDF-1.4 plaan"}
    )
    panel.locator("[name=sent_on]").fill(_day(0))
    box = page.locator("#samm-adressaat-otsi")
    box.click()
    box.type(MINISTRY[:8], delay=20)
    page.locator("#samm-adressaat-tulemused").get_by_role(
        "option", name=MINISTRY, exact=True
    ).click()
    panel.get_by_role("button", name="Registreeri arvamus").click()
    wait_for_htmx(page)
    expect(_row(page, "Saada Koja arvamus").locator(".workplan__state")).to_have_text("Tehtud")
    expect(zone).to_contain_text("Järgmine samm on määramata")
    sent = page.locator(KAIK_ROW).filter(has_text="Arvamus välja")
    expect(sent).to_have_count(1)
    open_kaik_row(sent)
    expect(sent).to_contain_text("Tehtud")
    screenshots(page, "tooplaan-arvamus-saadetud")


def test_skipping_a_step_clears_it_out_of_the_way(page, base_url):
    sign_in(page, base_url, SANDRA)
    _new_teema(page, base_url, unique_title("Tööplaan: vahele"))

    page.locator("#muuda-plaani > summary").click()
    page.get_by_role("button", name="Jäta vahele: Kaasa liikmeid / küsi tagasisidet").click()
    wait_for_htmx(page)

    expect(_row(page, "Kaasa liikmeid")).to_have_count(0)
    expect(_plan(page).locator(".workplan__step")).to_have_count(4)
    # Restorable in the editor, and nothing in the history says it was skipped.
    page.locator("#muuda-plaani > summary").click()
    expect(page.locator("#muuda-plaani")).to_contain_text("Vahele jäetud")
    expect(page.locator("#ajalugu-loend")).not_to_contain_text("Kaasa liikmeid")
    # The rest stays, and the person can carry straight on.
    _start_from_plan(page, "Koonda tagasiside ja kujunda Koja seisukoht")


def test_a_finished_step_is_repeated_as_a_new_occurrence(page, base_url):
    sign_in(page, base_url, SANDRA)
    _new_teema(page, base_url, unique_title("Tööplaan: kordus"))
    _start_from_plan(page, "Tutvu materjaliga")
    _done(page, "Lugesin esimese versiooni läbi.")

    page.locator("#muuda-plaani > summary").click()
    page.get_by_role("button", name="Korda: Tutvu materjaliga").click()
    wait_for_htmx(page)

    rows = _row(page, "Tutvu materjaliga")
    expect(rows).to_have_count(2)
    expect(rows.nth(0).locator(".workplan__state")).to_have_text("Tehtud")
    expect(rows.nth(1).locator(".workplan__state")).to_have_text("Plaanis")


def test_a_file_with_no_plan_gains_one_only_when_asked(page, base_url):
    """A Teema filed closed gets no plan; reopened, it is a file with none — the
    shape every Matter from before docs/adr/0133 has."""
    sign_in(page, base_url, SANDRA)
    create_matter(page, base_url, unique_title("Tööplaan: varasem"), stage="Rohkem ei tegele")
    banner = page.locator(".banner--closed")
    banner.locator("select[name=stage]").select_option(label="Idee")
    banner.get_by_role("button", name="Ava uuesti").click()
    page.wait_for_load_state("networkidle")

    expect(_plan(page).locator(".workplan__step")).to_have_count(0)
    # An ordinary current step, set the ordinary way.
    page.locator("#lisa-jargmine > summary").click()
    page.locator("#lisa-jargmine [name='text']").fill("Helistan ministeeriumi")
    page.locator("#lisa-jargmine").get_by_role("button", name="Salvesta järgmine samm").click()
    wait_for_htmx(page)

    _plan(page).get_by_role("button", name="+ Lisa tavapärane tööplaan").click()
    wait_for_htmx(page)

    expect(_plan(page).locator(".workplan__step")).to_have_count(5)
    expect(_zone(page).locator(".curact__text").first).to_have_text("Helistan ministeeriumi")
    expect(_plan(page).get_by_role("button", name="+ Lisa tavapärane tööplaan")).to_have_count(0)
    # Nothing was started over the existing step.
    expect(_plan(page).get_by_role("button", name=re.compile("^Alusta"))).to_have_count(0)


def test_closing_completes_nothing_and_reopening_starts_nothing(page, base_url):
    sign_in(page, base_url, SANDRA)
    _new_teema(page, base_url, unique_title("Tööplaan: sulgemine"))
    _start_from_plan(page, "Tutvu materjaliga")

    close_through_stage(page, "Rohkem ei tegele")

    plan = _plan(page)
    expect(plan.locator(".workplan__step")).to_have_count(5)
    expect(plan.locator(".workplan__state", has_text="Tehtud")).to_have_count(0)
    expect(plan.get_by_role("button")).to_have_count(0)
    expect(plan).not_to_contain_text("Muuda plaani")

    banner = page.locator(".banner--closed")
    banner.locator("select[name=stage]").select_option(label="Idee")
    banner.get_by_role("button", name="Ava uuesti").click()
    page.wait_for_load_state("networkidle")

    expect(_zone(page)).to_contain_text("Järgmine samm on määramata")
    expect(_plan(page).locator(".workplan__step")).to_have_count(5)
    expect(_plan(page).get_by_role("button", name="Alusta: Tutvu materjaliga")).to_be_visible()


@pytest.mark.parametrize("width", [1024, 1440])
def test_the_work_centre_holds_its_shape(page, base_url, width, screenshots):
    sign_in(page, base_url, SANDRA)
    page.set_viewport_size({"width": width, "height": 1000})
    _new_teema(page, base_url, unique_title("Tööplaan: laius"))
    page.locator("#lisa-samm > summary").click()
    page.locator("#id_samm_title").fill(
        "Kohtun ministeeriumi ja kahe ettevõtlusorganisatsiooniga, et arutada "
        "üleminekuaega ja rakendusakti sõnastust põhjalikult"
    )
    page.locator("#lisa-samm").get_by_role("button", name="Lisa samm").click()
    wait_for_htmx(page)
    _start_from_plan(page, "Tutvu materjaliga")
    open_done_form(page)
    page.locator("#muuda-plaani > summary").click()

    assert not page.evaluate(
        "() => document.documentElement.scrollWidth > document.documentElement.clientWidth + 1"
    ), f"the Teema page scrolls sideways at {width}px"
    for control in ("#id_praegune_body", "#praegune-tegevus-vorm button[type=submit]", "#tooplaan"):
        box = page.locator(control).bounding_box()
        assert box is not None and box["x"] + box["width"] <= width + 1, control
    screenshots(page, f"tooplaan-{width}")
