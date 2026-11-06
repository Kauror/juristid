"""UI-10 in a real browser: `✓ Tehtud | Vaatasin üle | Muuda` never move.

What only a running page can settle (the markup and the stale-revision refusal
are `tests/test_ui10_current_action_toolbar.py`):

* all three panels open at once, each by its own button, and each button says
  so (`aria-expanded`);
* opening and closing them moves none of the three buttons — their bounding
  boxes are compared before and after every toggle — and does not scroll;
* the panels stack under the step in the toolbar's order, whatever order they
  were opened in;
* text typed into a panel is still there after it is closed and opened again,
  and nothing was saved by doing so;
* the focus stays on the button that was pressed, and Escape in a panel closes
  that panel and gives the focus back to its button;
* after `Vaatasin üle` is saved, the `Muuda` panel that was drawn from the
  older revision is not kept as if nothing had happened: its words are shown
  as «Salvestamata sisu» and the panel is drawn afresh.

**On a Matter this file creates**, with a waiting step written in a subprocess
the way `e2e/test_review_waiting_step.py` writes one, so the seeded world is
left alone.
"""

from __future__ import annotations

import os
import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path

import pytest
from playwright.sync_api import expect

from e2e.conftest import SANDRA, create_matter, sign_in, unique_title, wait_for_htmx

pytestmark = pytest.mark.e2e

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]

#: A `WAIT` due in a week on the Matter named by `E2E_UI10_MATTER`.
_WAIT_SCRIPT = (
    "import os, datetime;"
    "from django.utils import timezone;"
    "from app.matters.models import Matter;"
    "from app.workflow.services import set_next_action;"
    "m = Matter.objects.get(pk=os.environ['E2E_UI10_MATTER']);"
    "set_next_action(matter=m, text='Ootame ministeeriumi vastust', kind='WAIT',"
    " date_semantics='REVIEW_ON',"
    " target_date=timezone.localdate() + datetime.timedelta(days=7),"
    " responsible=m.owner, actor=m.owner)"
)

#: How many entries and open steps the Matter has — «nothing was saved».
_STATE_SCRIPT = (
    "import os;"
    "from app.matters.models import Entry;"
    "from app.workflow.models import NextAction;"
    "m = os.environ['E2E_UI10_MATTER'];"
    "a = NextAction.objects.get(matter_id=m, status='OPEN');"
    "print(Entry.objects.filter(matter_id=m).count(), a.pk, a.text, a.target_date)"
)

PANELS = ("tehtud", "vaatasin-ule", "lisa-jargmine")


def _in_the_server(script: str, matter_id: str) -> str:
    result = subprocess.run(  # noqa: S603
        [sys.executable, "manage.py", "shell", "-c", script],
        cwd=REPOSITORY_ROOT,
        env={
            **os.environ,
            "DJANGO_SETTINGS_MODULE": "config.settings",
            "E2E_UI10_MATTER": matter_id,
        },
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert result.returncode == 0, "\n".join(["stdout:", result.stdout, "stderr:", result.stderr])
    lines = result.stdout.strip().splitlines()
    return lines[-1].strip() if lines else ""


def _a_waiting_matter(page, base_url: str) -> tuple[str, str]:
    url = create_matter(page, base_url, unique_title("UI-10 tööriistariba"), owner=SANDRA)
    matter_id = url.rstrip("/").rsplit("/", 1)[-1]
    _in_the_server(_WAIT_SCRIPT, matter_id)
    page.goto(url)
    page.wait_for_load_state("networkidle")
    return url, matter_id


def _button(page, panel: str):
    return page.locator(f'#praegune-tegevus [data-curact-toggle][aria-controls="{panel}"]')


def _boxes(page) -> list[dict]:
    """Where the three buttons are, rounded to the pixel, and the scroll."""
    return page.evaluate(
        """(ids) => ({
            scroll: Math.round(window.scrollY),
            boxes: ids.map((id) => {
                const b = document.querySelector(
                    `#praegune-tegevus [data-curact-toggle][aria-controls="${id}"]`
                ).getBoundingClientRect();
                return [b.x, b.y, b.width, b.height].map(Math.round);
            }),
        })""",
        list(PANELS),
    )


def _in_two_weeks() -> str:
    value = date.today() + timedelta(days=14)
    return f"{value.day}.{value.month}.{value.year}"


@pytest.mark.parametrize("width", [1440, 390])
def test_all_three_open_together_and_no_button_moves(page, base_url, width):
    sign_in(page, base_url, SANDRA)
    page.set_viewport_size({"width": width, "height": 900})
    _a_waiting_matter(page, base_url)

    labels = [_button(page, panel).inner_text() for panel in PANELS]
    assert labels == ["✓ Tehtud", "Vaatasin üle", "Muuda"]
    resting = _boxes(page)

    # Opened in the reverse of the toolbar's order, one at a time; after every
    # press all three buttons are exactly where they were and nothing scrolled.
    for panel in reversed(PANELS):
        _button(page, panel).click()
        expect(_button(page, panel)).to_have_attribute("aria-expanded", "true")
        expect(page.locator(f"#{panel}")).to_be_visible()
        assert _boxes(page) == resting, f"opening #{panel} moved the toolbar"
        # The focus is still on the button that was pressed.
        expect(_button(page, panel)).to_be_focused()

    for panel in PANELS:
        expect(_button(page, panel)).to_have_attribute("aria-expanded", "true")
        expect(page.locator(f"#{panel}")).to_be_visible()

    # The panels stack under the step in the toolbar's order, not in the order
    # they were opened, and every one of them is below the toolbar.
    tops = [page.locator(f"#{panel}").bounding_box()["y"] for panel in PANELS]
    assert tops == sorted(tops)
    toolbar = page.locator("#praegune-tegevus .curact__tools").bounding_box()
    assert tops[0] >= toolbar["y"] + toolbar["height"]

    # Closing them, in yet another order, moves nothing either.
    for panel in ("vaatasin-ule", "tehtud", "lisa-jargmine"):
        _button(page, panel).click()
        expect(_button(page, panel)).to_have_attribute("aria-expanded", "false")
        expect(page.locator(f"#{panel}")).to_be_hidden()
        assert _boxes(page) == resting, f"closing #{panel} moved the toolbar"

    overflow = page.evaluate(
        "() => document.documentElement.scrollWidth - document.documentElement.clientWidth"
    )
    assert overflow <= 1, f"the page scrolls sideways by {overflow}px at {width}"


def test_typed_text_survives_toggling_and_nothing_is_saved(page, base_url):
    sign_in(page, base_url, SANDRA)
    _url, matter_id = _a_waiting_matter(page, base_url)
    before = _in_the_server(_STATE_SCRIPT, matter_id)

    for panel in PANELS:
        _button(page, panel).click()
    page.locator("#id_praegune_body").fill("Helistasin ministeeriumi")
    page.locator("#vaatasin-ule").get_by_label("Järgmine ülevaatus").fill(_in_two_weeks())
    page.locator("#lisa-jargmine [name='text']").fill("Ootame vastust (parandamisel)")

    # Closed and opened again, twice: what was typed is still in every box.
    for _ in range(2):
        for panel in PANELS:
            _button(page, panel).click()
            expect(page.locator(f"#{panel}")).to_be_hidden()
        for panel in PANELS:
            _button(page, panel).click()
            expect(page.locator(f"#{panel}")).to_be_visible()

    expect(page.locator("#id_praegune_body")).to_have_value("Helistasin ministeeriumi")
    expect(page.locator("#vaatasin-ule").get_by_label("Järgmine ülevaatus")).to_have_value(
        _in_two_weeks()
    )
    expect(page.locator("#lisa-jargmine [name='text']")).to_have_value(
        "Ootame vastust (parandamisel)"
    )
    # Opening and closing is not saving.
    assert _in_the_server(_STATE_SCRIPT, matter_id) == before


def test_escape_closes_only_its_own_panel_and_returns_the_focus(page, base_url):
    sign_in(page, base_url, SANDRA)
    _a_waiting_matter(page, base_url)
    for panel in PANELS:
        _button(page, panel).click()

    page.locator("#lisa-jargmine [name='text']").focus()
    page.keyboard.press("Escape")

    expect(page.locator("#lisa-jargmine")).to_be_hidden()
    expect(_button(page, "lisa-jargmine")).to_have_attribute("aria-expanded", "false")
    expect(_button(page, "lisa-jargmine")).to_be_focused()
    # The other two stay open: this is not an accordion.
    expect(page.locator("#tehtud")).to_be_visible()
    expect(page.locator("#vaatasin-ule")).to_be_visible()

    # And the keyboard opens it again from the button.
    page.keyboard.press("Enter")
    expect(page.locator("#lisa-jargmine")).to_be_visible()
    expect(_button(page, "lisa-jargmine")).to_be_focused()


def test_a_review_save_does_not_leave_an_obsolete_muuda_behind(page, base_url):
    sign_in(page, base_url, SANDRA)
    _url, matter_id = _a_waiting_matter(page, base_url)
    for panel in ("vaatasin-ule", "lisa-jargmine"):
        _button(page, panel).click()
    page.locator("#lisa-jargmine [name='text']").fill("Ootame vastust (vana seisu järgi)")
    review = page.locator("#vaatasin-ule")
    review.get_by_label("Järgmine ülevaatus").fill(_in_two_weeks())

    review.get_by_role("button", name="Salvesta", exact=True).click()
    wait_for_htmx(page)

    zone = page.locator("#praegune-tegevus")
    expect(zone.locator(".curact__date")).to_contain_text(_in_two_weeks())
    # The saved panel is done and shut.
    expect(page.locator("#vaatasin-ule")).to_be_hidden()
    expect(_button(page, "vaatasin-ule")).to_have_attribute("aria-expanded", "false")
    # `Muuda` was drawn from the step before the review: it is drawn afresh
    # from the new revision, and the sentence typed into it is shown, read-only,
    # as unsaved — never posted against the changed step.
    expect(page.locator("#lisa-jargmine [name='text']")).to_have_value(
        "Ootame ministeeriumi vastust"
    )
    unsaved = page.locator(".unsaved")
    expect(unsaved).to_contain_text("Ootame vastust (vana seisu järgi)")
    # The toolbar is the toolbar it was: three buttons, in order.
    labels = [_button(page, panel).inner_text() for panel in PANELS]
    assert labels == ["✓ Tehtud", "Vaatasin üle", "Muuda"]
    state = _in_the_server(_STATE_SCRIPT, matter_id)
    assert "Ootame ministeeriumi vastust" in state
    assert "(vana seisu järgi)" not in state


def test_a_save_elsewhere_keeps_open_panels_open(page, base_url):
    """A save that does not change the step — `+ Lisa tegevus` — closes nothing above it."""
    sign_in(page, base_url, SANDRA)
    _a_waiting_matter(page, base_url)
    for panel in ("tehtud", "lisa-jargmine"):
        _button(page, panel).click()

    page.locator('label[for="lisa-planeeritud-valik"]').click()
    planned = page.locator("#lisa-planeeritud")
    planned.locator("[name=text]").fill("Saada meeldetuletus")
    planned.locator("[name=target_date]").fill(_in_two_weeks())
    planned.get_by_role("button", name="Salvesta", exact=True).click()
    wait_for_htmx(page)

    expect(page.locator("#praegune-tegevus")).to_contain_text("Saada meeldetuletus")
    for panel in ("tehtud", "lisa-jargmine"):
        expect(page.locator(f"#{panel}")).to_be_visible()
        expect(_button(page, panel)).to_have_attribute("aria-expanded", "true")
    expect(page.locator("#vaatasin-ule")).to_be_hidden()
