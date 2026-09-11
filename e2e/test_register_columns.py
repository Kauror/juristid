"""Teemad's column headings, in a browser.

The unit tests hold the rule and the ordering. What only a browser can answer is
whether the controls are *reachable*: whether a menu opens, whether the arrow is
on screen rather than scrolled off the end of its column, whether Back returns
to the previous ordering, and whether typing into the search box quietly drops
the filter somebody chose from a heading.

Three habits run through the file.

**Read the dates off the page.** An ordering that agrees with a selector and
disagrees with the table is the exact failure the Kuupäev heading exists to
prevent, so the assertions parse the rendered column rather than asking the
server what it thinks it sorted.

**Locate controls by class, not by accessible name.** Every register filter
`<select>` in Täpsem otsing wraps inside its `<label>`, so its computed name is
the legend plus every option's text (docs/adr/0071). The headings do not have
that problem, but their own names carry the ", filter on aktiivne" suffix when
one is applied, so an `exact=True` name match would pass in one state and fail
in the other.

**Assert the geometry.** «VIIMANE TEGEVUS» is wider than its column used to be,
which is why the arrow that says which way it is sorted has a test of its own.
"""

from __future__ import annotations

import re

import pytest
from playwright.sync_api import expect

from e2e.conftest import SANDRA, sign_in

pytestmark = pytest.mark.e2e

REGISTER = "/teemad/"

#: The three headings that open a menu, and the four that do not need one.
FILTER_HEADINGS = ["Hetkeseis", "Vastutaja", "Järgmiseks"]
SORT_HEADINGS = ["Kuupäev", "Viimane tegevus"]


def open_register(page, base_url: str, query: str = "") -> None:
    page.goto(f"{base_url}{REGISTER}{query}")
    page.wait_for_load_state("networkidle")


def document_overflows(page) -> bool:
    return page.evaluate(
        "() => document.documentElement.scrollWidth > document.documentElement.clientWidth + 1"
    )


def column_index(page, heading: str) -> int:
    headings = page.locator(".table--register thead th")
    for index in range(headings.count()):
        if headings.nth(index).inner_text().strip().startswith(heading.upper()):
            return index
    raise AssertionError(f"no register column called {heading!r}")


def column_text(page, heading: str) -> list[str]:
    """One column's rendered cells, top to bottom."""
    index = column_index(page, heading)
    cells = page.locator(f".table--register tbody tr td:nth-child({index + 1})")
    return [cells.nth(row).inner_text().strip() for row in range(cells.count())]


DATE = re.compile(r"(\d{1,2})\.(\d{1,2})\.(\d{4})")


def dates_in(cells: list[str]) -> list[tuple[int, int, int] | None]:
    """Each cell as a sortable day, or ``None`` where the row shows no date.

    A cell can carry more than a date — «TÄHTAEG 4.9.2026» — and an approximate
    step carries no day at all («III kvartal 2026»), which reads as None here
    and is asserted separately.
    """
    found = []
    for cell in cells:
        match = DATE.search(cell)
        found.append(
            (int(match.group(3)), int(match.group(2)), int(match.group(1))) if match else None
        )
    return found


def monotonic(days: list[tuple[int, int, int] | None], *, ascending: bool) -> bool:
    """Whether the dated rows are in order and the undated ones are after them."""
    dated = [day for day in days if day is not None]
    if days[: len(dated)] != dated:
        return False  # something without a date came before something with one
    return dated == sorted(dated, reverse=not ascending)


def heading(page, label: str):
    """One heading's control, by the column it sits in."""
    return (
        page.locator(".table--register thead th").nth(column_index(page, label)).locator(".colhead")
    )


# ---------------------------------------------------------------------------
# 1-2. The controls are here, and only here
# ---------------------------------------------------------------------------


def test_the_register_renders_all_five_interactive_headings(page, base_url):
    sign_in(page, base_url, SANDRA)
    open_register(page, base_url)

    for label in FILTER_HEADINGS:
        control = heading(page, label)
        expect(control).to_have_count(1)
        assert "colhead--filter" in (control.get_attribute("class") or ""), label
    for label in SORT_HEADINGS:
        control = heading(page, label)
        expect(control).to_have_count(1)
        assert "colhead--sort" in (control.get_attribute("class") or ""), label


def test_saabunud_does_not_get_the_registers_controls(page, base_url):
    """The row partial is shared; the interaction is not."""
    sign_in(page, base_url, SANDRA)
    page.goto(f"{base_url}/saabunud/")
    page.wait_for_load_state("networkidle")

    expect(page.locator(".table--register")).not_to_have_count(0)
    expect(page.locator(".colhead")).to_have_count(0)
    expect(page.locator(".table__quietlink")).to_have_count(0)


# ---------------------------------------------------------------------------
# 3-5. Hetkeseis
# ---------------------------------------------------------------------------


def test_the_stage_heading_opens_its_menu(page, base_url):
    sign_in(page, base_url, SANDRA)
    open_register(page, base_url)
    control = heading(page, "Hetkeseis")

    expect(control.locator(".colhead__menu")).to_be_hidden()
    control.locator("summary").click()

    expect(control.locator(".colhead__menu")).to_be_visible()
    expect(control.get_by_role("link", name="Kõik", exact=True)).to_be_visible()


def test_choosing_a_stage_filters_the_register_and_marks_the_heading(page, base_url):
    sign_in(page, base_url, SANDRA)
    open_register(page, base_url, "?olek=koik")
    before = page.locator(".table--register tbody tr").count()

    control = heading(page, "Hetkeseis")
    control.locator("summary").click()
    control.get_by_role("link", name="Kooskõlastusringil", exact=True).click()
    page.wait_for_load_state("networkidle")

    assert "hetkeseis=consultation" in page.url
    assert "olek=koik" in page.url, "the status somebody had chosen was dropped"
    stages = set(column_text(page, "Hetkeseis"))
    assert stages == {"Kooskõlastusringil"}, stages
    assert page.locator(".table--register tbody tr").count() < before
    # The ordinary chip, above the table, where it can be removed.
    expect(page.locator(".filterchip", has_text="Hetkeseis")).to_have_count(1)
    assert "is-filtered" in (heading(page, "Hetkeseis").get_attribute("class") or "")


def test_a_rows_stage_applies_the_same_filter(page, base_url):
    sign_in(page, base_url, SANDRA)
    open_register(page, base_url, "?olek=koik")

    index = column_index(page, "Hetkeseis")
    page.locator(f".table--register tbody tr td:nth-child({index + 1}) a").first.click()
    page.wait_for_load_state("networkidle")

    assert "hetkeseis=" in page.url
    assert set(column_text(page, "Hetkeseis")) == {"Kooskõlastusringil"}


# ---------------------------------------------------------------------------
# 6-7. Vastutaja
# ---------------------------------------------------------------------------


def test_choosing_a_responsible_person_filters_the_register(page, base_url):
    sign_in(page, base_url, SANDRA)
    open_register(page, base_url, "?olek=koik")

    control = heading(page, "Vastutaja")
    control.locator("summary").click()
    expect(control.get_by_role("link", name="Määramata", exact=True)).to_be_visible()
    control.get_by_role("link", name=SANDRA.short_name, exact=True).click()
    page.wait_for_load_state("networkidle")

    assert "vastutaja=" in page.url
    assert set(column_text(page, "Vastutaja")) == {SANDRA.short_name}
    expect(page.locator(".filterchip", has_text="Vastutaja")).to_have_count(1)
    assert "is-filtered" in (heading(page, "Vastutaja").get_attribute("class") or "")


def test_a_rows_owner_applies_the_filter(page, base_url):
    sign_in(page, base_url, SANDRA)
    open_register(page, base_url, "?olek=koik")

    index = column_index(page, "Vastutaja")
    owner = page.locator(f".table--register tbody tr td:nth-child({index + 1}) a.table__quietlink")
    chosen = owner.first.inner_text().strip()
    owner.first.click()
    page.wait_for_load_state("networkidle")

    assert "vastutaja=" in page.url
    assert set(column_text(page, "Vastutaja")) == {chosen}


# ---------------------------------------------------------------------------
# 8. Järgmiseks
# ---------------------------------------------------------------------------


def test_the_next_action_heading_filters_by_work_state(page, base_url):
    """The states `?tegevus=` already understands, never the sentence's letters."""
    sign_in(page, base_url, SANDRA)
    open_register(page, base_url, "?olek=koik")

    control = heading(page, "Järgmiseks")
    control.locator("summary").click()
    for label in ("Puudub", "Tähtaeg möödas", "Ülevaatus käes"):
        expect(control.get_by_role("link", name=label, exact=True)).to_be_visible()
    control.get_by_role("link", name="Puudub", exact=True).click()
    page.wait_for_load_state("networkidle")

    assert "tegevus=puudub" in page.url
    steps = column_text(page, "Järgmiseks")
    assert steps, "no rows have no next step"
    for step in steps:
        assert "Järgmine samm puudub" in step or "Excelist" in step, step


# ---------------------------------------------------------------------------
# 9-11. Kuupäev
# ---------------------------------------------------------------------------


def test_kuupaev_first_activation_puts_the_nearest_date_first(page, base_url):
    sign_in(page, base_url, SANDRA)
    open_register(page, base_url, "?olek=koik")

    heading(page, "Kuupäev").click()
    page.wait_for_load_state("networkidle")

    assert "jarjestus=kuupaev_asc" in page.url
    cell = page.locator(".table--register thead th").nth(column_index(page, "Kuupäev"))
    assert cell.get_attribute("aria-sort") == "ascending"
    days = dates_in(column_text(page, "Kuupäev"))
    assert monotonic(days, ascending=True), column_text(page, "Kuupäev")


def test_kuupaev_second_activation_puts_the_latest_date_first(page, base_url):
    sign_in(page, base_url, SANDRA)
    open_register(page, base_url, "?olek=koik&jarjestus=kuupaev_asc")

    heading(page, "Kuupäev").click()
    page.wait_for_load_state("networkidle")

    assert "jarjestus=kuupaev_desc" in page.url
    cell = page.locator(".table--register thead th").nth(column_index(page, "Kuupäev"))
    assert cell.get_attribute("aria-sort") == "descending"
    days = dates_in(column_text(page, "Kuupäev"))
    assert monotonic(days, ascending=False), column_text(page, "Kuupäev")


def test_a_third_activation_returns_to_the_registers_own_order(page, base_url):
    sign_in(page, base_url, SANDRA)
    open_register(page, base_url, "?olek=koik")
    default = column_text(page, "Pealkiri")

    heading(page, "Kuupäev").click()
    page.wait_for_load_state("networkidle")
    heading(page, "Kuupäev").click()
    page.wait_for_load_state("networkidle")
    heading(page, "Kuupäev").click()
    page.wait_for_load_state("networkidle")

    assert "jarjestus" not in page.url
    assert column_text(page, "Pealkiri") == default
    cell = page.locator(".table--register thead th").nth(column_index(page, "Kuupäev"))
    assert cell.get_attribute("aria-sort") == "none"


# ---------------------------------------------------------------------------
# 12-15. Viimane tegevus
# ---------------------------------------------------------------------------


def test_viimane_tegevus_first_activation_puts_the_newest_first(page, base_url):
    sign_in(page, base_url, SANDRA)
    open_register(page, base_url, "?olek=koik")

    heading(page, "Viimane tegevus").click()
    page.wait_for_load_state("networkidle")

    assert "jarjestus=viimane_uusim" in page.url
    cell = page.locator(".table--register thead th").nth(column_index(page, "Viimane tegevus"))
    # Newest first is a descending date column, whatever the click order is.
    assert cell.get_attribute("aria-sort") == "descending"
    days = dates_in(column_text(page, "Viimane tegevus"))
    assert monotonic(days, ascending=False), column_text(page, "Viimane tegevus")


def test_viimane_tegevus_second_activation_puts_the_oldest_first(page, base_url):
    sign_in(page, base_url, SANDRA)
    open_register(page, base_url, "?olek=koik&jarjestus=viimane_uusim")

    heading(page, "Viimane tegevus").click()
    page.wait_for_load_state("networkidle")

    assert "jarjestus=viimane_vanim" in page.url
    days = dates_in(column_text(page, "Viimane tegevus"))
    assert monotonic(days, ascending=True), column_text(page, "Viimane tegevus")


@pytest.mark.parametrize(
    ("column", "query"),
    [
        ("Kuupäev", "?olek=koik&jarjestus=kuupaev_asc"),
        ("Kuupäev", "?olek=koik&jarjestus=kuupaev_desc"),
        ("Viimane tegevus", "?olek=koik&jarjestus=viimane_uusim"),
        ("Viimane tegevus", "?olek=koik&jarjestus=viimane_vanim"),
    ],
)
def test_a_row_with_no_date_is_always_last(page, base_url, column, query):
    """In both directions. PostgreSQL's own default would open «hiliseim enne»
    on a page of em dashes."""
    sign_in(page, base_url, SANDRA)
    open_register(page, base_url, query)

    cells = column_text(page, column)
    days = dates_in(cells)
    undated = [index for index, day in enumerate(days) if day is None]
    if not undated:
        pytest.skip(f"no row in the seeded world is missing a {column}")
    assert undated == list(range(len(days) - len(undated), len(days))), cells


# ---------------------------------------------------------------------------
# 16-19. The state is the address, and it survives everything
# ---------------------------------------------------------------------------


def test_typing_in_the_search_box_keeps_the_heading_filter_and_the_sort(page, base_url):
    sign_in(page, base_url, SANDRA)
    open_register(page, base_url, "?olek=koik&hetkeseis=consultation&jarjestus=kuupaev_asc")

    page.locator("#teemad-otsing").fill("pakendi")
    page.wait_for_load_state("networkidle")
    page.wait_for_timeout(600)

    assert "hetkeseis=consultation" in page.url
    assert "jarjestus=kuupaev_asc" in page.url
    assert "is-filtered" in (heading(page, "Hetkeseis").get_attribute("class") or "")
    cell = page.locator(".table--register thead th").nth(column_index(page, "Kuupäev"))
    assert cell.get_attribute("aria-sort") == "ascending"


def test_tapsem_otsing_opens_on_the_state_a_heading_set(page, base_url):
    sign_in(page, base_url, SANDRA)
    open_register(page, base_url, "?olek=koik")

    control = heading(page, "Hetkeseis")
    control.locator("summary").click()
    control.get_by_role("link", name="Kooskõlastusringil", exact=True).click()
    page.wait_for_load_state("networkidle")
    page.locator(".filterpanel__trigger").click()

    assert page.locator("select[name='hetkeseis']").input_value() == "consultation"


def test_the_panel_does_not_undo_a_sort_chosen_from_a_heading(page, base_url):
    """The Järjestus select has to know about the orderings the headings set."""
    sign_in(page, base_url, SANDRA)
    open_register(page, base_url, "?olek=koik&jarjestus=kuupaev_desc")
    page.locator(".filterpanel__trigger").click()

    assert page.locator("select[name='jarjestus']").input_value() == "kuupaev_desc"

    page.locator(".filterpanel__body button[type='submit']").click()
    page.wait_for_load_state("networkidle")

    assert "jarjestus=kuupaev_desc" in page.url


def test_removing_the_chip_clears_the_heading_too(page, base_url):
    sign_in(page, base_url, SANDRA)
    open_register(page, base_url, "?olek=koik&hetkeseis=consultation")
    assert "is-filtered" in (heading(page, "Hetkeseis").get_attribute("class") or "")

    page.locator(".filterchip", has_text="Hetkeseis").first.click()
    page.wait_for_load_state("networkidle")

    assert "hetkeseis" not in page.url
    assert "is-filtered" not in (heading(page, "Hetkeseis").get_attribute("class") or "")


def test_back_and_forward_reproduce_the_table(page, base_url):
    sign_in(page, base_url, SANDRA)
    open_register(page, base_url, "?olek=koik")
    default = column_text(page, "Pealkiri")

    heading(page, "Kuupäev").click()
    page.wait_for_load_state("networkidle")
    sorted_titles = column_text(page, "Pealkiri")
    assert sorted_titles != default

    page.go_back()
    page.wait_for_load_state("networkidle")
    assert column_text(page, "Pealkiri") == default

    page.go_forward()
    page.wait_for_load_state("networkidle")
    assert column_text(page, "Pealkiri") == sorted_titles


# ---------------------------------------------------------------------------
# 20. Geometry and the keyboard
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("width", [1440, 1280, 1024, 768])
def test_the_heading_row_stays_inside_its_columns(page, base_url, width):
    """Including the arrow. «VIIMANE TEGEVUS» is 93px of uppercase in what used
    to be an 82px content box, so the part that ran off the end was the
    indicator saying which way the column is sorted."""
    sign_in(page, base_url, SANDRA)
    page.set_viewport_size({"width": width, "height": 900})
    open_register(page, base_url, "?olek=koik&jarjestus=viimane_uusim")

    clipped = page.evaluate("""() => {
      const bad = [];
      for (const th of document.querySelectorAll('.table--register thead th')) {
        const control = th.firstElementChild;
        if (!control || !control.classList.contains('colhead')) continue;
        const style = getComputedStyle(th);
        const box = th.clientWidth
          - parseFloat(style.paddingLeft) - parseFloat(style.paddingRight);
        if (box > 0 && control.scrollWidth > box) {
          bad.push(th.className + ': ' + control.scrollWidth + ' in ' + box);
        }
      }
      return bad;
    }""")

    assert clipped == [], clipped
    assert not document_overflows(page)


def test_a_filter_menu_is_reachable_and_dismissable_from_the_keyboard(page, base_url):
    sign_in(page, base_url, SANDRA)
    open_register(page, base_url, "?olek=koik")
    control = heading(page, "Vastutaja")

    control.locator("summary").focus()
    page.keyboard.press("Enter")
    expect(control.locator(".colhead__menu")).to_be_visible()

    page.keyboard.press("Escape")
    expect(control.locator(".colhead__menu")).to_be_hidden()
    assert page.evaluate("() => document.activeElement.tagName.toLowerCase()") == "summary"


def test_only_one_heading_menu_is_open_at_a_time(page, base_url):
    sign_in(page, base_url, SANDRA)
    open_register(page, base_url, "?olek=koik")

    heading(page, "Hetkeseis").locator("summary").click()
    heading(page, "Vastutaja").locator("summary").click()

    expect(page.locator(".colhead--filter[open]")).to_have_count(1)
