"""UX-001 (2026-11-25) in a browser: «Menetluse kulg» as one track with popovers.

`tests/test_rail_ux001.py` holds the read model and the markup. This holds what
only a rendered page shows:

* every dot sits on one line, and each item's segment ends exactly at the next
  dot's centre — at 1440, 1920 and 3440 px, with long and short text side by side;
* a long description never moves a dot or a neighbour;
* the popover opens on hover and stays open while the pointer is inside it, opens
  on keyboard focus, opens on a tap, closes on Escape and a click elsewhere,
  stays inside the window, and ends with «Allikas»;
* no ✕ on the rail; `Muuda` lists the removals;
* on a phone the rail is a vertical list with no horizontal page overflow.

The Teema is planted for Martin through the product's services: the
`Riigikogus` phase, three expected commencements over several years (one with a
very long note) and two commencements, one with a published source address.
"""

from __future__ import annotations

import os
import subprocess
import sys
from itertools import pairwise

import pytest
from playwright.sync_api import expect

from e2e.conftest import MARTIN, REPOSITORY_ROOT, sign_in, unique_title

pytestmark = pytest.mark.e2e

_PLANT = "\n".join(
    [
        "import os",
        "from datetime import date, timedelta",
        "from django.utils import timezone",
        "from app.accounts.models import User",
        "from app.intelligence.enums import FactStatus, ImportantDateKind",
        "from app.intelligence.models import MatterEffectiveDate, MatterImportantDate",
        "from app.matters.models import Matter",
        "from app.matters.services import assign_matter, change_stage, create_matter",
        "from app.workflow.enums import DatePrecision, Track",
        "from app.workflow.models import StageVocabulary",
        "martin = User.objects.get(upn='martin@example.invalid')",
        "m = create_matter(title=os.environ['E2E_PREFIX'], actor=martin, data_class='TEST')",
        "assign_matter(matter=m, owner=martin, actor=martin)",
        "Matter.objects.filter(pk=m.pk).update(track=Track.DOMESTIC.value)",
        "m.refresh_from_db()",
        "change_stage(matter=m, stage=StageVocabulary.objects.get(key='parliament'), actor=martin)",
        "y = timezone.localdate().year",
        "long = 'Ministeeriumi seletuskirja kohaselt jõustub üldises korras ' * 8",
        "for when, note in ((date(y + 1, 1, 1), long), (date(y + 1, 7, 1), 'Osa hiljem'),"
        " (date(y + 2, 3, 1), 'Rakendusaktide järel')):",
        "    MatterImportantDate.objects.create(matter=m, title='Eeldatav jõustumine',"
        " kind=ImportantDateKind.ESTIMATED_COMMENCEMENT, date_value=when, period_end=when,"
        " date_precision=DatePrecision.EXACT, status=FactStatus.ACTIVE, note=note,"
        " created_by=martin)",
        "source = 'https://www.riigiteataja.ee/akt/000000000'",
        "for when, text, url in ((date(y + 3, 1, 1), 'põhiosa', source),"
        " (date(y + 3, 7, 1), 'osad sätted', '')):",
        "    MatterEffectiveDate.objects.create(matter=m, date_value=when, period_end=when,"
        " date_precision=DatePrecision.EXACT, description=text, source_url=url,"
        " status=FactStatus.ACTIVE, created_by=martin)",
        "print(m.pk)",
    ]
)


@pytest.fixture
def teema_url(base_url) -> str:
    result = subprocess.run(  # noqa: S603
        [sys.executable, "manage.py", "shell", "-c", _PLANT],
        cwd=REPOSITORY_ROOT,
        env={
            **os.environ,
            "DJANGO_SETTINGS_MODULE": "config.settings",
            "E2E_PREFIX": unique_title("A2 menetluse kulg"),
        },
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )
    assert result.returncode == 0, "\n".join(["stdout:", result.stdout, "stderr:", result.stderr])
    pk = result.stdout.strip().splitlines()[-1]
    return f"{base_url}/teemad/{pk}/"


_GEOMETRY = """
() => Array.from(document.querySelectorAll('.tl-strip > .tl-step')).map((li) => {
  const dot = li.querySelector(':scope > .tl-step__dot').getBoundingClientRect();
  const box = li.getBoundingClientRect();
  const seg = getComputedStyle(li, '::before');
  return {
    dotX: dot.left + dot.width / 2,
    dotY: dot.top + dot.height / 2,
    left: box.left, right: box.right, top: box.top, bottom: box.bottom,
    segDisplay: seg.display,
    segLeft: parseFloat(seg.left), segRight: parseFloat(seg.right),
    segTop: parseFloat(seg.top), segBottom: parseFloat(seg.bottom),
  };
})
"""


def _open_teema(page, base_url, url, width, height=1000):
    page.set_viewport_size({"width": width, "height": height})
    sign_in(page, base_url, MARTIN)
    page.goto(url)
    expect(page.locator(".tl-strip > .tl-step").first).to_be_visible()


@pytest.mark.parametrize("width", [1440, 1920, 3440])
def test_one_track_every_dot_on_it_every_segment_meeting_the_next_dot(
    page, base_url, teema_url, width
):
    _open_teema(page, base_url, teema_url, width)
    items = page.evaluate(_GEOMETRY)
    assert len(items) >= 5

    first_y = items[0]["dotY"]
    for item in items:
        assert abs(item["dotY"] - first_y) <= 0.5, items
    for here, there in pairwise(items):
        start = here["left"] + here["segLeft"]
        end = here["right"] - here["segRight"]
        assert abs(start - here["dotX"]) <= 0.5
        assert abs(end - there["dotX"]) <= 0.5, (here, there)
    assert items[-1]["segDisplay"] == "none"
    # The strip scrolls itself, never the page.
    assert page.evaluate("document.documentElement.scrollWidth") <= width


def test_a_long_description_is_one_line_and_moves_nothing(page, base_url, teema_url):
    _open_teema(page, base_url, teema_url, 1440)
    note = page.locator(".tl-step__sum .tl-step__note").first
    clipped = note.evaluate("node => node.scrollWidth > node.clientWidth")
    assert clipped
    heights = page.evaluate(
        "() => Array.from(document.querySelectorAll('.tl-step__sum .tl-step__note'))"
        ".map((n) => n.getBoundingClientRect().height)"
    )
    assert max(heights) < 20


def test_hover_opens_the_popover_and_it_stays_while_the_pointer_is_inside(
    page, base_url, teema_url
):
    _open_teema(page, base_url, teema_url, 1440)
    item = page.locator(".tl-step--tip").filter(has_text="Eeldatav jõustumine").first
    tip = item.locator(".tl-tip")

    item.hover()
    expect(tip).to_be_visible()
    text = tip.inner_text()
    assert "Ministeeriumi seletuskirja" in text
    assert text.strip().splitlines()[-1].startswith("Allikas:")

    tip.hover()
    expect(tip).to_be_visible()
    box = tip.bounding_box()
    assert box and box["x"] >= 0 and box["x"] + box["width"] <= 1440

    page.mouse.move(5, 5)
    expect(tip).to_be_hidden()


def test_a_click_opens_it_escape_and_an_outside_click_close_it(page, base_url, teema_url):
    _open_teema(page, base_url, teema_url, 1440)
    page.mouse.move(0, 0)
    joustumine = page.locator(".tl-step--tip").filter(has_text="Jõustumine").last
    summary = joustumine.locator(".tl-step__sum")
    tip = joustumine.locator(".tl-tip")

    summary.click()
    expect(tip).to_be_visible()
    link = tip.get_by_role("link", name="riigiteataja.ee")
    expect(link).to_have_attribute("href", "https://www.riigiteataja.ee/akt/000000000")
    expect(link).to_have_attribute("target", "_blank")
    expect(link).to_have_attribute("rel", "noopener noreferrer")
    facts = tip.locator(".tl-tip__fact")
    expect(facts).to_have_count(2)
    expect(facts.nth(1)).not_to_contain_text("riigiteataja")

    page.keyboard.press("Escape")
    expect(tip).to_be_hidden()

    summary.click()
    expect(tip).to_be_visible()
    page.mouse.click(5, 5)
    expect(tip).to_be_hidden()


def test_keyboard_focus_opens_it(page, base_url, teema_url):
    _open_teema(page, base_url, teema_url, 1440)
    summaries = page.locator(".tl-step__sum")
    summaries.first.focus()
    page.keyboard.press("Tab")
    expect(summaries.nth(1).locator("xpath=..").locator(".tl-tip")).to_be_visible()


def test_no_removal_on_the_rail_and_muuda_lists_them(page, base_url, teema_url):
    _open_teema(page, base_url, teema_url, 1440)
    expect(page.locator(".tl-strip .uxtl__remove")).to_have_count(0)

    page.locator("button.lprail__edit").click()
    removals = page.locator(".kulgform__removals")
    expect(removals).to_be_visible()
    expect(removals.locator(".uxtl__icon--remove")).to_have_count(3)


@pytest.mark.parametrize("width", [375, 320])
def test_a_phone_draws_a_vertical_track_with_no_page_overflow(page, base_url, teema_url, width):
    _open_teema(page, base_url, teema_url, width, 812)
    items = page.evaluate(_GEOMETRY)
    xs = {round(item["dotX"]) for item in items}
    assert len(xs) == 1, xs
    for here, there in pairwise(items):
        end = here["bottom"] - here["segBottom"]
        assert abs(end - there["dotY"]) <= 0.5, (here, there)
        assert there["dotY"] > here["dotY"]
    assert page.evaluate("document.documentElement.scrollWidth") <= width


def test_a_tap_opens_the_popover_on_a_touch_screen(browser, base_url, teema_url):
    context = browser.new_context(
        viewport={"width": 375, "height": 812}, has_touch=True, is_mobile=True
    )
    page = context.new_page()
    try:
        sign_in(page, base_url, MARTIN)
        page.goto(teema_url)
        summary = page.locator(".tl-step__sum").first
        summary.tap()
        tip = summary.locator("xpath=..").locator(".tl-tip")
        expect(tip).to_be_visible()
        box = tip.bounding_box()
        assert box and box["x"] >= 0 and box["x"] + box["width"] <= 375
    finally:
        context.close()
