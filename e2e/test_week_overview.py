"""«Selle nädala ülevaade» in a real browser (r97).

What the Python suite cannot show: a figure's link opens exactly the rows it counted
and the page survives Back / Forward / refresh; removing a preset leaves saved state
alone; a chart column of Minu statistika is a real, keyboard-operable link; the
department's records are the head's (403 for anybody else); a genuinely restricted
Teema — the seeded «Konfidentsiaalne …», owned by Sandra — never reaches Martin
through any of it; and the seven-day table stacks on a phone.
"""

from __future__ import annotations

import re

from playwright.sync_api import Page, expect

from e2e.conftest import HEAD, MARTIN, SANDRA, create_matter, sign_in
from e2e.titles import unique_title

RESTRICTED = "Konfidentsiaalne"
PRESETS = (
    "tehtud",
    "ule-vaadatud",
    "edasi-lukatud",
    "tahtaega-muudetud",
    "asendatud",
    "tuhistatud",
    "tegemata",
    "saadetud-arvamused",
    "uued-teemad",
    "taitmata-nouded",
    "tahtaja-ulatanud",
    "kuupaev-saabunud",
    "eesolevad-joustumised",
    "ligikaudsed-joustumised",
)


def overview(page: Page):
    return page.get_by_label("Nädala ülevaade", exact=True).first


def test_a_figure_opens_exactly_its_rows_and_back_forward_work(page: Page, base_url: str) -> None:
    sign_in(page, base_url, MARTIN)
    expect(page.get_by_role("heading", name=re.compile("^Selle nädala ülevaade"))).to_be_visible()
    figure = overview(page).get_by_role("link", name=re.compile(r"^Tegemata:")).first
    counted = int(re.search(r":\s*(\d+)", figure.get_attribute("aria-label") or "").group(1))  # type: ignore[union-attr]
    figure.click()
    expect(page).to_have_url(re.compile(r"/minu-asjad/nadal/\?naitaja=tegemata"))
    expect(page.get_by_role("heading", level=1)).to_have_text(re.compile("^Tegemata"))
    expect(page.locator("table.wk__table tbody tr")).to_have_count(counted)

    page.go_back()
    expect(page).to_have_url(re.compile(r"/minu-asjad/$"))
    page.go_forward()
    expect(page).to_have_url(re.compile(r"naitaja=tegemata"))
    page.reload()
    expect(page.locator("table.wk__table tbody tr")).to_have_count(counted)


def test_removing_a_preset_returns_to_the_page_and_leaves_saved_state_alone(
    page: Page, base_url: str
) -> None:
    sign_in(page, base_url, MARTIN)
    page.goto(f"{base_url}/teemad/?olek=avatud")
    before_url = page.url
    before_state = page.evaluate(
        "JSON.stringify([localStorage, document.cookie.split(';').length])"
    )
    page.goto(f"{base_url}/minu-asjad/nadal/?naitaja=tehtud")
    page.get_by_role("link", name="Eemalda eelseadistus").click()
    expect(page).to_have_url(re.compile(r"/minu-asjad/$"))
    page.reload()
    expect(page).to_have_url(re.compile(r"/minu-asjad/$"))  # nothing came back
    page.goto(f"{base_url}/teemad/?olek=avatud")
    assert page.url == before_url
    assert page.evaluate("JSON.stringify([localStorage, document.cookie.split(';').length])") == (
        before_state
    )


def test_the_head_sees_the_department_overview_and_nobody_else_does(
    page: Page, base_url: str
) -> None:
    sign_in(page, base_url, HEAD)
    page.goto(f"{base_url}/osakond/")
    expect(page.get_by_role("heading", name=re.compile("^Selle nädala ülevaade"))).to_be_visible()
    expect(page.get_by_text(re.compile("Järgmised 7 päeva:"))).to_be_visible()
    done = overview(page).get_by_role("link", name=re.compile(r"^Tehtud:")).first
    done.click()
    expect(page).to_have_url(re.compile(r"ulatus=osakond"))
    expect(page.get_by_role("heading", level=1)).to_have_text(re.compile("osakond"))

    sign_in(page, base_url, MARTIN)
    page.goto(f"{base_url}/osakond/")
    expect(page.get_by_role("heading", name=re.compile("^Selle nädala ülevaade"))).to_have_count(0)
    response = page.goto(f"{base_url}/minu-asjad/nadal/?naitaja=tehtud&ulatus=osakond")
    assert response is not None and response.status == 403


def test_a_restricted_teema_never_reaches_martin_through_the_overview(
    page: Page, base_url: str
) -> None:
    sign_in(page, base_url, SANDRA)
    page.goto(f"{base_url}/minu-asjad/")
    expect(page.get_by_text(RESTRICTED).first).to_be_visible()  # the fixture is real

    sign_in(page, base_url, MARTIN)
    for path in ("/minu-asjad/", *(f"/minu-asjad/nadal/?naitaja={kind}" for kind in PRESETS)):
        page.goto(f"{base_url}{path}&alates=2011-01-01" if "?" in path else f"{base_url}{path}")
        assert RESTRICTED not in page.content(), path


def test_a_chart_column_opens_its_days_and_clearing_keeps_the_period(
    page: Page, base_url: str
) -> None:
    sign_in(page, base_url, MARTIN)
    create_matter(page, base_url, unique_title("Valitud tulp"), owner=MARTIN)
    page.goto(f"{base_url}/minu-asjad/statistika/?naitaja=uued")
    column = page.locator("a.mystats__colbtn").first
    expect(column).to_be_visible()
    column.focus()
    page.keyboard.press("Enter")  # a real link: the keyboard opens it
    expect(page).to_have_url(re.compile(r"valik=\d{4}-\d{2}-\d{2}"))
    expect(page.locator("a.mystats__colbtn.is-selected")).to_have_count(1)
    page.get_by_role("link", name="Eemalda valik").click()
    expect(page).not_to_have_url(re.compile("valik="))
    assert "periood=" in page.url and "naitaja=uued" in page.url


def test_the_seven_day_table_stacks_on_a_phone_and_nothing_overflows(
    page: Page, base_url: str
) -> None:
    sign_in(page, base_url, MARTIN)
    page.set_viewport_size({"width": 390, "height": 900})
    page.goto(f"{base_url}/minu-asjad/")
    table = page.locator("table.wo__table").first
    expect(table).to_be_visible()
    assert table.locator("tbody tr").first.evaluate("el => getComputedStyle(el).display") == "grid"
    widths = page.evaluate(
        "() => [document.documentElement.scrollWidth, document.documentElement.clientWidth]"
    )
    assert widths[0] <= widths[1] + 1, widths
    page.set_viewport_size({"width": 1440, "height": 900})


def test_the_figures_are_reachable_and_visibly_focused_from_the_keyboard(
    page: Page, base_url: str
) -> None:
    sign_in(page, base_url, MARTIN)
    page.goto(f"{base_url}/minu-asjad/")
    reached = None
    for _ in range(120):
        page.keyboard.press("Tab")
        info = page.evaluate(
            "() => { const a = document.activeElement; return a && a.closest('.wo__fig')"
            " ? [a.getAttribute('aria-label'), getComputedStyle(a).outlineStyle,"
            " getComputedStyle(a).outlineWidth] : null }"
        )
        if info:
            reached = info
            break
    assert reached is not None, "no weekly figure is reachable by Tab"
    assert reached[0] and ":" in reached[0]  # an accessible name that states the count
    assert reached[1] != "none" and reached[2] != "0px"


def test_a_long_title_is_whole_to_a_keyboard_and_a_touch_and_opens_its_teema(
    page: Page, base_url: str
) -> None:
    sign_in(page, base_url, MARTIN)
    page.goto(f"{base_url}/minu-asjad/")
    link = page.locator(".wo a", has_text="Tavaline avatud teema").first
    name = link.inner_text()
    assert name.endswith(
        "väljatöötamiskavatsus"
    )  # the whole title is the link text; CSS only clamps
    link.focus()
    page.keyboard.press("Enter")
    expect(page.get_by_role("heading", level=1)).to_contain_text("väljatöötamiskavatsus")
