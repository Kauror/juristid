"""A record's own words, and one transition entered once — in a browser.

`tests/test_record_context.py` and `tests/test_stage_move_dates_its_phase.py`
hold the contract (docs/adr/0127, docs/adr/0128). This file holds what only a
rendered page settles:

* a round's `Veebileht` is a link on its row and `Märkus` a line under it, both
  asked on `+ Kaasamine` and on `Muuda`, while `PRAEGUNE TEGEVUS` goes on
  reading the audience — and a narrow window gains no sideways scroll;
* two titled `Ülevaade / uudis` rows are told apart on their closed lines;
* `Märgi ka menetluse kulgu` appears when `Uus hetkeseis` and the day call for
  it, ticked, naming the phase and the day, hides for a day ahead, and one save
  dates the phase on `Menetluse kulg`; unticked, nothing is dated;
* a `VTK` dated in the past reads as reached, with the points after it in date
  order.

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
    give_first_step,
    open_add_panel,
    open_composer,
    open_hetkeseis,
    open_kaik_row,
    sign_in,
    start_first_step,
    unique_title,
)

pytestmark = pytest.mark.e2e

CAVEAT = (
    "Fail sisaldab kaasamise sihtrühma tööloendit; see ei tõenda, et kõigile loendis "
    "olevatele ettevõtetele kiri saadeti."
)
AUDIENCE = "Õigusteenuse valdkonna Koja liikmed"
PAGE = "https://www.koda.ee/hetkel-kasil/juristieksam"


def _day(offset: int) -> str:
    day = date.today() + timedelta(days=offset)
    return f"{day.day}.{day.month}.{day.year}"


def _new_matter(page, base_url: str, prefix: str, *, stage: str | None = None, law: tuple = ()):
    """A Teema with an open step (so it leaves no «järgmise tegevuseta» row), and
    optionally a `Hetkeseis` and the `Õigusakt` that give it a roadmap."""
    page.goto(f"{base_url}/teemad/uus/")
    page.wait_for_load_state("networkidle")
    page.fill("#id_title", unique_title(prefix))
    if stage is not None:
        open_hetkeseis(page)
        page.get_by_role("radio", name=stage, exact=True).check()
    page.get_by_role("radio", name=SANDRA.short_name, exact=True).check()
    give_first_step(page)
    page.get_by_role("button", name="Loo teema").click()
    page.wait_for_url(re.compile(r"/teemad/[0-9a-f-]{36}/$"))
    start_first_step(page)
    url = page.url
    if law:
        page.goto(f"{url}muuda/")
        page.wait_for_load_state("networkidle")
        for name in law:
            page.get_by_role("checkbox", name=name, exact=True).check()
        page.get_by_role("button", name="Salvesta").click()
        page.wait_for_url(re.compile(r"/teemad/[0-9a-f-]{36}/$"))
    return url


def _post(page, path_end: str, press) -> None:
    with page.expect_response(
        lambda r: r.url.endswith(path_end) and r.request.method == "POST"
    ) as caught:
        press()
    assert caught.value.status == 200, f"refused: {caught.value.status}"
    page.wait_for_load_state("networkidle")


def _no_sideways_scroll(page) -> bool:
    return page.evaluate(
        "() => document.documentElement.scrollWidth <= document.documentElement.clientWidth"
    )


# ---------------------------------------------------------------------------
# FLOW 1 — a round's page and note
# ---------------------------------------------------------------------------


def test_a_round_keeps_its_page_and_note_and_the_work_list_keeps_its_audience(
    page, base_url, screenshots
):
    sign_in(page, base_url, SANDRA)
    _new_matter(page, base_url, "Kaasamise kontekst")

    open_add_panel(page, "kaasamine-alusta")
    form = page.locator("#kaasamine-alusta")
    form.locator("[name=audience]").fill(AUDIENCE)
    # `Ülevaate link` since the owner's round of 2026-10-07 (docs/adr/0142 §C).
    expect(form.get_by_text("Ülevaate link", exact=False)).to_be_visible()
    form.locator("[name=website_url]").fill("www.koda.ee/hetkel-kasil/juristieksam")
    form.locator("[name=engagement_note]").fill(CAVEAT)
    form.locator("[name=feedback_deadline]").fill(_day(5))
    _post(page, "/lisa/kaasamine/", lambda: form.locator("button[type=submit]").click())

    row = page.locator(KAIK_ROW).filter(has_text=f"Kaasamine: {AUDIENCE}")
    expect(row).to_have_count(1)
    open_kaik_row(row)
    link = row.get_by_role("link", name=re.compile(r"^Veebileht"))
    expect(link).to_have_attribute("href", PAGE)
    expect(row.locator(".uxtl__msnote")).to_contain_text("Märkus")
    expect(row.locator(".uxtl__msnote")).to_contain_text(CAVEAT)

    # The waiting line is operational since 2026-10-07: the wait and its day,
    # Smaily / Alchemer where stored, and `Tehtud` — never the audience, the
    # address or the note.
    zone = page.locator("#praegune-tegevus")
    expect(zone).to_contain_text("Ootame tagasisidet")
    expect(zone).not_to_contain_text(AUDIENCE)
    expect(zone).not_to_contain_text("koda.ee")
    expect(zone).not_to_contain_text("tööloendit")
    screenshots(page, "kaasamine-veebileht-markus")

    # `Muuda` offers both again, filled, and saves both.
    row.get_by_role("button", name=re.compile(r"^Muuda")).click()
    edit = row.locator("form").first
    expect(edit.locator("[name=url]")).to_have_value(PAGE)
    expect(edit.locator("[name=note]")).to_have_value(CAVEAT)
    edit.locator("[name=url]").fill("koda.ee/hetkel-kasil/uus")
    edit.locator("[name=note]").fill("Parandatud märkus.")
    with page.expect_response(
        lambda r: r.url.endswith("/muuda/") and r.request.method == "POST"
    ) as caught:
        edit.get_by_role("button", name="Salvesta").click()
    assert caught.value.status == 200
    page.wait_for_load_state("networkidle")
    row = page.locator(KAIK_ROW).filter(has_text=f"Kaasamine: {AUDIENCE}")
    open_kaik_row(row)
    expect(row.get_by_role("link", name=re.compile(r"^Veebileht"))).to_have_attribute(
        "href", "https://koda.ee/hetkel-kasil/uus"
    )
    expect(row.locator(".uxtl__msnote")).to_contain_text("Parandatud märkus.")

    page.set_viewport_size({"width": 375, "height": 800})
    page.reload()
    page.wait_for_load_state("networkidle")
    open_kaik_row(page.locator(KAIK_ROW).filter(has_text=f"Kaasamine: {AUDIENCE}"))
    assert _no_sideways_scroll(page), "the row made the page scroll sideways at 375px"


# ---------------------------------------------------------------------------
# FLOW 2 — two write-ups, told apart
# ---------------------------------------------------------------------------


def _publish(page, title: str, address: str) -> None:
    open_add_panel(page, "lisa-koduleht")
    form = page.locator("#lisa-koduleht")
    form.locator("[name=overview_title]").fill(title)
    form.locator("[name=url]").fill(address)
    _post(
        page,
        "/lisa/koduleht/",
        lambda: form.get_by_role("button", name="Lisa ülevaade / uudis").click(),
    )


def test_two_overviews_are_told_apart_on_their_closed_lines(page, base_url, screenshots):
    sign_in(page, base_url, SANDRA)
    _new_matter(page, base_url, "Kaks ülevaadet")

    _publish(page, "Koja seisukoht VTK kohta", "https://www.koda.ee/uudised/vtk")
    _publish(page, "Koja seisukoht eelnõu kohta", "https://www.koda.ee/uudised/eelnou")

    heads = page.locator(f"{KAIK_ROW} .uxtl__mswhat").all_inner_texts()
    # koda.ee news addresses read `Uudis` since docs/adr/0142 §C.
    assert "Uudis – Koja seisukoht VTK kohta" in heads, heads
    assert "Uudis – Koja seisukoht eelnõu kohta" in heads, heads
    # Closed rows: the toggle is named by the line, so it says which page.
    expect(page.get_by_role("button", name=re.compile("Koja seisukoht VTK kohta"))).to_have_count(1)
    screenshots(page, "kaks-ulevaadet")

    # Renaming one renames its closed line, without a reload.
    row = page.locator(KAIK_ROW).filter(has_text="Koja seisukoht VTK kohta")
    open_kaik_row(row)
    row.get_by_role("button", name=re.compile(r"^Muuda")).click()
    row.locator("[name=title]").fill("Koja seisukoht juristieksami VTK kohta")
    with page.expect_response(
        lambda r: r.url.endswith("/link/") and r.request.method == "POST"
    ) as caught:
        row.get_by_role("button", name="Salvesta").click()
    assert caught.value.status == 200
    expect(
        page.locator(".uxtl__mswhat", has_text="Uudis – Koja seisukoht juristieksami")
    ).to_have_count(1)


# ---------------------------------------------------------------------------
# FLOW 3 / 4 — one transition, entered once; and unticked
# ---------------------------------------------------------------------------


def _offer(page):
    return page.locator("#marge-tavaline [data-phase-date-choice]")


def _rail_step(page, label: str):
    return page.locator(
        ".lprail .tl-strip .tl-step",
        has=page.locator(".tl-step__what", has_text=re.compile(rf"^{re.escape(label)}$")),
    )


def test_a_stage_move_dates_its_phase_with_one_save(page, base_url, screenshots):
    sign_in(page, base_url, SANDRA)
    _new_matter(page, base_url, "Üks üleminek", stage="Idee", law=("Seadus",))

    open_composer(page)
    page.locator("#id_marge_title").fill("Saabus eelnõu kooskõlastusringile")
    expect(_offer(page)).to_be_hidden()
    page.locator("#id_marge_stage").select_option(label="Kooskõlastusringil")

    offer = _offer(page)
    expect(offer).to_be_visible()
    box = offer.get_by_role("checkbox")
    expect(box).to_be_checked()
    expect(offer).to_contain_text(f"Märgi ka menetluse kulgu: Kooskõlastusring {_day(0)}")
    screenshots(page, "marge-menetluse-kulg")

    # A day ahead is a plan: the box goes, and comes back ticked for today.
    date_box = page.locator("#id_marge_occurred_on")
    date_box.fill(_day(3))
    date_box.dispatch_event("change")
    expect(offer).to_be_hidden()
    date_box.fill(_day(0))
    date_box.dispatch_event("change")
    expect(offer).to_be_visible()
    expect(box).to_be_checked()

    _post(
        page,
        "/lisa/marge/",
        lambda: page.locator("#marge-tavaline button[type=submit]").click(),
    )

    # `Hetkeseis` moved, the phase is dated — with no second entry.
    round_ = _rail_step(page, "Kooskõlastusring")
    expect(round_).to_have_class(re.compile(r"tl-step--current"))
    expect(round_).to_contain_text(_day(0))
    # One row in `Teema käik` for the act.
    expect(
        page.locator(KAIK_ROW).filter(has_text="Saabus eelnõu kooskõlastusringile")
    ).to_have_count(1)


def test_unticked_moves_the_stage_and_dates_nothing(page, base_url):
    sign_in(page, base_url, SANDRA)
    _new_matter(page, base_url, "Linnukeseta", stage="Kooskõlastusringil", law=("Seadus",))

    open_composer(page)
    page.locator("#id_marge_title").fill("Eelnõu jõudis valitsusse")
    page.locator("#id_marge_stage").select_option(label="Valitsuses")
    offer = _offer(page)
    expect(offer).to_be_visible()
    offer.get_by_role("checkbox").uncheck()
    _post(
        page,
        "/lisa/marge/",
        lambda: page.locator("#marge-tavaline button[type=submit]").click(),
    )

    government = _rail_step(page, "Valitsuses")
    expect(government).to_have_class(re.compile(r"tl-step--current"))
    expect(government.locator(".tl-step__date")).to_have_count(0)


# ---------------------------------------------------------------------------
# FLOW 5 — a dated VTK, and the points after it
# ---------------------------------------------------------------------------


def test_a_dated_vtk_reads_reached_and_the_points_after_it_keep_order(page, base_url, screenshots):
    sign_in(page, base_url, SANDRA)
    _new_matter(page, base_url, "VTK järjekord", stage="Idee", law=("VTK", "Seadus"))

    page.locator(".lprail__edit").click()
    page.wait_for_selector("#menetluse-kulg-muuda form")
    page.locator("#id_kulg_vtk__date").fill(_day(-14))
    with page.expect_response(
        lambda r: "/menetluse-kulg/" in r.url and r.request.method == "POST"
    ) as caught:
        page.locator("#menetluse-kulg-muuda").get_by_role("button", name="Salvesta").click()
    assert caught.value.status == 200
    page.wait_for_load_state("networkidle")

    open_add_panel(page, "kaasamine-alusta")
    form = page.locator("#kaasamine-alusta")
    form.locator("[name=audience]").fill("liikmed")
    form.locator("[name=occurred_on]").fill(_day(-12))
    form.locator("[name=feedback_deadline]").fill(_day(-9))
    _post(page, "/lisa/kaasamine/", lambda: form.locator("button[type=submit]").click())

    vtk = _rail_step(page, "VTK")
    expect(vtk).to_have_class(re.compile(r"tl-step--recorded"))
    expect(vtk).not_to_contain_text("Tulevikus")
    labels = [
        text.strip() for text in page.locator(".lprail .tl-strip .tl-step__what").all_inner_texts()
    ]
    # The round's reply-by date no longer draws a column (docs/adr/0131 §13);
    # the procedure's own points keep their order around the dated VTK.
    assert "Tagasiside tähtaeg" not in labels, labels
    assert labels.index("VTK") < labels.index("Kooskõlastusring"), labels
    # The current marker is still the `Hetkeseis`'s.
    expect(_rail_step(page, "Algus")).to_have_class(re.compile(r"tl-step--current"))
    screenshots(page, "rail-vtk-jarjekord")
