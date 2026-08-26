"""Who the controls actually offer, in a browser.

What the rule *means* is proved against the database in
`tests/test_department_workers.py`, and every crafted POST is refused in
`tests/test_work_assignment_eligibility.py`. Three things only a rendered page
answers, and all three have shipped wrong before:

**A template that assembles its own list.** A view can hand the narrowed
population to the context and a template can iterate something else — the
seeded world has an administrator in it precisely so a page that quietly widened
the list would be visible.

**A control that refuses its own value.** The Teema header renders the Matter's
owner as the selected option. If the endpoint behind it does not accept that
person, pressing *Salvesta* having changed nothing is a refusal, and no unit
test that posts a payload by hand notices which option the page was showing.

**A chip row that lost a row.** Vastutaja on `Uus teema` is radios styled as
chips, so removing a name changes the layout rather than the contents of a
closed select.

The seeded world is `app/core/management/commands/seed_e2e_data.py`: four
personas — two specialists, a department head and an administrator — plus one
departed colleague who still owns an open Matter.
"""

from __future__ import annotations

import pytest
from playwright.sync_api import expect

from app.core.management.commands.seed_e2e_data import FORMER_NAME, FORMER_OWNER_TITLE
from e2e.conftest import ADMIN, HEAD, MARTIN, SANDRA, sign_in

pytestmark = pytest.mark.e2e

CREATE_PATH = "/teemad/uus/"


def _option_names(page, selector: str) -> list[str]:
    """The words in a select, read from the DOM rather than from the layout.

    `all_inner_texts()` answers "" for anything not being rendered, and two of
    these controls live inside a closed `<details>` or a collapsed panel. A case
    that read those would pass against a list of empty strings, which is the
    quietest way for an assertion about a population to stop asserting anything.
    """
    return page.locator(selector).evaluate(
        "select => Array.from(select.options).map(option => option.textContent.trim())"
    )


def _open_matter(page, base_url: str, title: str) -> None:
    """Open a named Matter from the register, following the link rather than
    clicking it: the table head is sticky and can sit over the first row."""
    page.goto(f"{base_url}/teemad/?olek=koik&q={title.split()[0]}")
    page.wait_for_load_state("networkidle")
    link = page.get_by_role("link", name=title, exact=False).first
    assert link.count(), f"the register does not hold {title!r}"
    page.goto(f"{base_url}{link.get_attribute('href')}")
    page.wait_for_load_state("networkidle")


# ---------------------------------------------------------------------------
# Uus teema
# ---------------------------------------------------------------------------


def test_the_vastutaja_chips_name_the_department_and_not_the_administrator(page, base_url):
    """The colleagues, by the short name the rest of the product uses.

    Asserted against the labels rather than the count, so the case says which
    people are missing when it fails rather than that a number moved.
    """
    sign_in(page, base_url, MARTIN)
    page.goto(f"{base_url}{CREATE_PATH}")
    page.wait_for_load_state("networkidle")

    group = page.locator("fieldset").filter(has_text="Vastutaja").first
    labels = [text.strip() for text in group.locator("label").all_inner_texts()]

    assert SANDRA.short_name in labels
    assert MARTIN.short_name in labels
    assert HEAD.short_name in labels
    assert ADMIN.short_name not in labels
    assert FORMER_NAME.split()[0] not in labels


def test_there_is_no_fourth_chip_at_all(page, base_url):
    """Every radio is one of the three colleagues, and there are three radios.

    The stronger half of the case above, which only says the administrator is
    not *named*. This says nothing else is offered either — an unlabelled chip,
    or one drawn from a second list, is still a chip a keyboard reaches.
    """
    sign_in(page, base_url, MARTIN)
    page.goto(f"{base_url}{CREATE_PATH}")
    page.wait_for_load_state("networkidle")

    group = page.locator("fieldset").filter(has_text="Vastutaja").first
    names = [text.strip() for text in group.locator(".chip__name").all_inner_texts()]

    assert sorted(names) == sorted({SANDRA.short_name, MARTIN.short_name, HEAD.short_name})
    assert group.locator("input[name='owner']").count() == len(names)


# ---------------------------------------------------------------------------
# The Teema header — the control that must not refuse what it is showing
# ---------------------------------------------------------------------------


def test_the_header_still_names_the_departed_owner_it_is_showing(page, base_url):
    sign_in(page, base_url, HEAD)
    _open_matter(page, base_url, FORMER_OWNER_TITLE)

    options = _option_names(page, "select[aria-label='Vastutaja']")

    assert FORMER_NAME.split()[0] in options
    assert SANDRA.short_name in options
    assert ADMIN.short_name not in options


def test_saving_the_header_owner_unchanged_is_a_save_and_not_a_refusal(page, base_url):
    """The whole reason the population is a union rather than a replacement."""
    sign_in(page, base_url, HEAD)
    _open_matter(page, base_url, FORMER_OWNER_TITLE)

    page.locator("summary", has_text=FORMER_NAME.split()[0]).first.click()
    page.get_by_role("button", name="Salvesta vastutaja muudatus").click()
    page.wait_for_load_state("networkidle")

    expect(page.locator(".formerror")).to_have_count(0)
    expect(page.locator(".metaline__item").filter(has_text="Vastutaja")).to_contain_text(
        FORMER_NAME.split()[0]
    )


# ---------------------------------------------------------------------------
# Filters — narrowed chooser, unchanged rows
# ---------------------------------------------------------------------------


def test_a_new_step_on_a_departed_colleagues_matter_is_refused_on_the_page(page, base_url):
    """Correction 1, where a person actually meets it.

    The composer carries no Vastutaja control, so this Matter's next step would
    have defaulted to the colleague who left — into the one queue nobody opens.
    The refusal has to be visible and it has to say what can be done about it,
    which is a sentence about the Teema's owner and not about the step.
    """
    sign_in(page, base_url, MARTIN)
    _open_matter(page, base_url, FORMER_OWNER_TITLE)

    page.locator(".composer__body").fill("Ootan ministeeriumi vastust.")
    page.locator("#next_kind_WAIT").check(force=True)
    page.locator("[data-composer-submit]").click()
    page.wait_for_load_state("networkidle")

    expect(page.get_by_text("ei ole enam aktiivne osakonna töötaja")).to_be_visible()


def test_the_register_filter_offers_the_department_and_not_the_administrator(page, base_url):
    """And the departed colleague, on the bare register, with no filter applied.

    `Vastutaja` here describes stored work rather than handing any out, so the
    list is the department plus whoever actually owns something in it. The
    seeded former colleague owns an open Matter, so they belong on it; the
    administrator owns nothing, so existing as an account does not put them
    there. That pair is the whole distinction, read off one rendered select.
    """
    sign_in(page, base_url, MARTIN)
    page.goto(f"{base_url}/teemad/")
    page.wait_for_load_state("networkidle")
    page.locator(".filterpanel__trigger").click()

    options = _option_names(page, "select[name='vastutaja']")

    assert SANDRA.short_name in options
    assert HEAD.short_name in options
    assert ADMIN.short_name not in options
    assert any(FORMER_NAME.split()[0] in option for option in options), options


def test_a_register_filtered_on_a_departed_colleague_still_finds_their_work(page, base_url):
    """The chooser narrowed. The register did not.

    Reached through the department table's own link, which is how such a URL
    actually occurs — a page that lists a departed colleague's open work and
    offers to open it.
    """
    sign_in(page, base_url, HEAD)
    page.goto(f"{base_url}/osakonna-too/")
    page.wait_for_load_state("networkidle")

    row = page.get_by_role("row").filter(has_text=FORMER_NAME).first
    row.locator("td a").first.click()
    page.wait_for_load_state("networkidle")

    expect(page.locator("select[name='vastutaja']")).to_contain_text(FORMER_NAME.split()[0])
