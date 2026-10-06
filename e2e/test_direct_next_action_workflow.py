"""The real-time lawyer loop, in a browser (docs/adr/0126).

`tests/test_direct_next_action_workflow.py` holds the whole contract. This file
holds what only a rendered page settles: that a lawyer can walk the loop with
the controls the page actually draws —

    A. a Teema with no step offers `+ Määra järgmine tegevus`;
    B. the step is set there, directly — no `Märge` is filed for it;
    C. `PRAEGUNE TEGEVUS` shows it (late, when its day has gone);
    D. Minu asjad lists it;
    E. a Koja arvamus is registered, with `Märgi praegune tegevus tehtuks`
       ticked — the box is offered unticked and names the step;
    F. the step is finished and the zone offers the next one;
    G. `Teema käik` has «Arvamus välja» with the step under it, and no second,
       generic row for the same act;
    H. the following step is set the same way;
    I. it reads on `PRAEGUNE TEGEVUS`, Minu asjad, the Teemad register and
       Osakond —

and that the two controls hold their shape at a narrow width.

Each test files its own Matter: these write Matter-level facts and the seeded
world is shared across a shard.
"""

from __future__ import annotations

from datetime import date, timedelta
from urllib.parse import quote

import pytest
from playwright.sync_api import expect

from e2e.conftest import (
    HEAD,
    KAIK_ROW,
    SANDRA,
    create_matter,
    open_add_panel,
    open_kaik_row,
    sign_in,
    unique_title,
)

pytestmark = pytest.mark.e2e

CTA = "+ Määra järgmine tegevus"
OPTION = "Märgi praegune tegevus tehtuks"
STEP = "Vormista ja saada Koja seisukoht"
FOLLOWING = "Kontrolli menetluse seisu ja uusi materjale"
MINISTRY = "Näidisministeerium"


def _day(offset: int) -> str:
    """A day relative to today, as the Estonian date box takes it."""
    day = date.today() + timedelta(days=offset)
    return f"{day.day}.{day.month}.{day.year}"


def _set_directly(page, text: str, when: str) -> None:
    """`+ Määra järgmine tegevus`, the way a lawyer uses it."""
    cta = page.locator("#praegune-tegevus #lisa-jargmine")
    expect(cta.locator("> summary")).to_have_text(CTA)
    open_add_panel(page, "lisa-jargmine")
    cta.locator("[name='text']").fill(text)
    page.locator("#id_target_date").fill(when)
    with page.expect_response(
        lambda response: response.url.endswith("/jargmiseks/") and response.request.method == "POST"
    ) as caught:
        cta.get_by_role("button", name="Salvesta järgmine samm").click()
    assert caught.value.status == 200, f"the step was refused: {caught.value.status}"
    page.wait_for_load_state("networkidle")


def _choose_recipient(page) -> None:
    box = page.locator("#koja-adressaat-otsi")
    box.click()
    box.fill("")
    box.type(MINISTRY[:8], delay=20)
    page.locator("#koja-adressaat-tulemused").get_by_role(
        "option", name=MINISTRY, exact=True
    ).click()


def _register_opinion(page, *, finish_step: bool) -> None:
    open_add_panel(page, "arvamus-koja")
    form = page.locator("#arvamus-koja")
    form.locator("input[name=upload]").set_input_files(
        {"name": "Koja_arvamus.pdf", "mimeType": "application/pdf", "buffer": b"%PDF-1.4 arvamus"}
    )
    form.locator("[name=sent_on]").fill(_day(0))
    # The picker opens on the Teema's `Saatja`, and this Teema has none.
    _choose_recipient(page)
    option = form.get_by_role("checkbox", name=OPTION)
    expect(option).to_have_count(1)
    expect(option).not_to_be_checked()
    if finish_step:
        option.check()
    with page.expect_response(
        lambda response: (
            response.url.endswith("/lisa/koja-arvamus/") and response.request.method == "POST"
        )
    ) as caught:
        form.get_by_role("button", name="Registreeri arvamus").click()
    assert caught.value.status == 200, f"the opinion was refused: {caught.value.status}"
    page.wait_for_load_state("networkidle")


def test_the_whole_loop_from_no_step_to_the_following_one(page, base_url, screenshots):
    sign_in(page, base_url, SANDRA)
    title = unique_title("Reaalaja töövoog")
    url = create_matter(page, base_url, title, owner=SANDRA)

    # A. No step: one compact line, and the direct control under it.
    zone = page.locator("#praegune-tegevus")
    expect(zone).to_contain_text("Järgmine samm on määramata")
    expect(zone.locator("#lisa-jargmine > summary")).to_have_text(CTA)
    expect(zone.locator("label.uxcomp__q")).to_have_count(0)

    # B. The step, set directly — yesterday, so it is late as soon as it exists.
    _set_directly(page, STEP, _day(-1))

    # C. The zone states it, late, with `Muuda` beside it. The direct control
    # stays, and now plans a dated future action beside it (docs/adr/0143).
    zone = page.locator("#praegune-tegevus")
    expect(zone.locator(".curact__text")).to_have_text(STEP)
    expect(zone.locator(".curact__date--overdue")).to_have_count(1)
    expect(zone.locator("#lisa-jargmine > summary")).to_have_text("Muuda")
    expect(zone.locator("#lisa-planeeritud > summary")).to_have_text(CTA)
    # Nothing was filed as a `Märge` to make it.
    expect(page.locator(KAIK_ROW).filter(has_text=STEP)).to_have_count(0)
    screenshots(page, "otsene-samm-praegune-tegevus")

    # D. Minu asjad lists it.
    page.goto(f"{base_url}/minu-asjad/")
    page.wait_for_load_state("networkidle")
    expect(page.locator("main")).to_contain_text(STEP)

    # E. The real event: the opinion goes out, and the box finishes the step.
    page.goto(url)
    page.wait_for_load_state("networkidle")
    open_add_panel(page, "arvamus-koja")
    label = page.locator("#arvamus-koja label.cx-check--action")
    expect(label).to_contain_text(STEP)
    screenshots(page, "otsene-samm-koja-arvamus-valik")
    _register_opinion(page, finish_step=True)

    # F. Finished, and the zone offers what comes next — no wizard, no new step.
    zone = page.locator("#praegune-tegevus")
    expect(zone).to_contain_text("Järgmine samm on määramata")
    expect(zone.locator("#lisa-jargmine > summary")).to_have_text(CTA)
    expect(zone).not_to_contain_text(STEP)

    # G. One row for one act: «Arvamus välja», with the finished step under it.
    sent = page.locator(KAIK_ROW).filter(has_text="Arvamus välja")
    expect(sent).to_have_count(1)
    open_kaik_row(sent)
    expect(sent).to_contain_text("Tehtud")
    expect(sent).to_contain_text(STEP)
    expect(page.locator("#ajalugu-loend")).not_to_contain_text("märkis eelmise sammu tehtuks")
    # No note was written to say the same thing again.
    expect(page.locator("#ajalugu-loend article[id^='sissekanne-']")).to_have_count(0)
    screenshots(page, "otsene-samm-teema-kaik")

    # H. The following step, through the same control.
    _set_directly(page, FOLLOWING, _day(6))

    # I. Every work surface reads it.
    expect(page.locator("#praegune-tegevus .curact__text")).to_have_text(FOLLOWING)
    page.goto(f"{base_url}/minu-asjad/")
    page.wait_for_load_state("networkidle")
    expect(page.locator("main")).to_contain_text(FOLLOWING)
    expect(page.locator("main")).not_to_contain_text(STEP)

    page.goto(f"{base_url}/teemad/?olek=avatud&q={quote(title)}")
    page.wait_for_load_state("networkidle")
    row = page.locator("main tr", has_text=title)
    expect(row.first).to_contain_text(FOLLOWING)

    sign_in(page, base_url, HEAD)
    page.goto(f"{base_url}/osakond/")
    page.wait_for_load_state("networkidle")
    expect(page.locator("main")).to_contain_text(title)


def test_an_opinion_without_the_tick_leaves_the_step_open(page, base_url):
    sign_in(page, base_url, SANDRA)
    create_matter(page, base_url, unique_title("Arvamus, samm jääb"), owner=SANDRA)
    _set_directly(page, STEP, _day(3))

    _register_opinion(page, finish_step=False)

    zone = page.locator("#praegune-tegevus")
    expect(zone.locator(".curact__text")).to_have_text(STEP)
    expect(page.locator(KAIK_ROW).filter(has_text="Arvamus välja")).to_have_count(1)


def test_a_file_with_no_step_offers_no_completion_box(page, base_url):
    """The box is never drawn where there is nothing to finish."""
    sign_in(page, base_url, SANDRA)
    create_matter(page, base_url, unique_title("Arvamus ilma sammuta"), owner=SANDRA)

    open_add_panel(page, "arvamus-koja")
    expect(page.locator("#arvamus-koja").get_by_role("checkbox", name=OPTION)).to_have_count(0)


@pytest.mark.parametrize("width", [390, 1024])
def test_the_two_controls_hold_their_shape_at_a_narrow_width(page, base_url, width, screenshots):
    """No sideways scroll, and the step's sentence wraps inside the label."""
    sign_in(page, base_url, SANDRA)
    create_matter(page, base_url, unique_title("Kitsas vaade"), owner=SANDRA)
    page.set_viewport_size({"width": width, "height": 900})
    page.reload()
    page.wait_for_load_state("networkidle")

    open_add_panel(page, "lisa-jargmine")
    expect(page.locator("#lisa-jargmine [name='text']")).to_be_visible()
    screenshots(page, f"otsene-samm-kitsas-{width}")
    long_step = STEP + " ning kooskõlasta see eelnevalt kõigi asjaomaste osakondadega"
    page.locator("#lisa-jargmine [name='text']").fill(long_step)
    page.locator("#id_target_date").fill(_day(4))
    page.locator("#lisa-jargmine").get_by_role("button", name="Salvesta järgmine samm").click()
    page.wait_for_load_state("networkidle")

    open_add_panel(page, "arvamus-koja")
    label = page.locator("#arvamus-koja label.cx-check--action")
    expect(label).to_contain_text(long_step)
    screenshots(page, f"otsene-samm-valik-kitsas-{width}")
    overflow = page.evaluate(
        "document.documentElement.scrollWidth - document.documentElement.clientWidth"
    )
    assert overflow <= 0, f"the page scrolls sideways by {overflow}px at {width}px"
