"""Package A1 (2026-11-25) in a browser: one-line recent activities and a folded «Varasemad».

`tests/test_a1_compact_activity_and_history.py` proves the rules on the server.
This proves what only a running page shows:

* UX-003 — the date and the act sit on the title's line, straight after it, not
  at the far edge; twenty rows show before «Näita veel»; a long title gives way
  with an ellipsis while the date stays whole; a phone wraps instead;
* UX-004 — «Varasemad (N)» is closed for a reader who has not chosen, remembers
  an explicit open or close across a reload, opens for the next page of the
  history and for a link to ``#varasemad`` without remembering that, keeps its
  state when the page redraws itself, and opening it marks nothing read.

The data is planted for Martin through the product's own services, as the
bell's own browser tests do: «Uus asi» hand-overs from Sandra, read by Martin,
and entries on a Teema of his. Every title is unique to the test.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys

import pytest
from playwright.sync_api import expect

from e2e.conftest import (
    MARTIN,
    REPOSITORY_ROOT,
    close_disclosure,
    open_disclosure,
    sign_in,
    unique_title,
    wait_for_htmx,
)

pytestmark = pytest.mark.e2e

HISTORY = 'details[data-disclosure="notifications.history"]'
ACTIVITY = 'section[aria-label="Viimased tegevused"]'

#: ``E2E_COUNT`` Teemad handed by Sandra to Martin, each «Uus asi» then read by
#: Martin, so each is in his history.
_HISTORY_SCRIPT = "\n".join(
    [
        "import os",
        "from app.accounts.models import User",
        "from app.matters.models import MatterAssignmentNotice",
        "from app.matters.services import assign_matter, create_matter",
        "from app.notifications import acknowledgement, inbox",
        "martin = User.objects.get(upn='martin@example.invalid')",
        "sandra = User.objects.get(upn='sandra@example.invalid')",
        "for number in range(int(os.environ['E2E_COUNT'])):",
        "    title = f\"{os.environ['E2E_PREFIX']} {number:02}\"",
        "    matter = create_matter(title=title, actor=sandra, data_class='TEST')",
        "    assign_matter(matter=matter, owner=martin, actor=sandra)",
        "    notice = MatterAssignmentNotice.objects.get(matter=matter, recipient=martin)",
        "    acknowledgement.mark_read(martin, inbox.TYPE_ASSIGNMENT, notice)",
        "print('ok')",
    ]
)

#: One Teema of Martin's titled ``E2E_PREFIX`` with ``E2E_COUNT`` entries by him.
_ACTIVITY_SCRIPT = "\n".join(
    [
        "import os",
        "from app.accounts.models import User",
        "from app.matters.services import add_entry, assign_matter, create_matter",
        "martin = User.objects.get(upn='martin@example.invalid')",
        "matter = create_matter(title=os.environ['E2E_PREFIX'], actor=martin, data_class='TEST')",
        "assign_matter(matter=matter, owner=martin, actor=martin)",
        "for number in range(int(os.environ['E2E_COUNT'])):",
        "    add_entry(matter=matter, body=f'<p>Märge {number}</p>', author=martin)",
        "print('ok')",
    ]
)


def _run(script: str, count: int, prefix: str) -> None:
    result = subprocess.run(  # noqa: S603
        [sys.executable, "manage.py", "shell", "-c", script],
        cwd=REPOSITORY_ROOT,
        env={
            **os.environ,
            "DJANGO_SETTINGS_MODULE": "config.settings",
            "E2E_COUNT": str(count),
            "E2E_PREFIX": prefix,
        },
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )
    assert result.returncode == 0, "\n".join(["stdout:", result.stdout, "stderr:", result.stderr])


def _history(page):
    block = page.locator(HISTORY)
    expect(block).to_have_count(1)
    return block


def _bell(page) -> str:
    badge = page.locator("[data-notifbell] .notifbell__count")
    return badge.inner_text() if badge.count() else "0"


# ---------------------------------------------------------------------------
# UX-004 «Varasemad»
# ---------------------------------------------------------------------------


def test_varasemad_is_closed_counts_its_items_and_remembers_a_choice(page, base_url):
    _run(_HISTORY_SCRIPT, 3, unique_title("A1 varasem"))
    sign_in(page, base_url, MARTIN)
    page.goto(f"{base_url}/teavitused/")
    block = _history(page)
    bell = _bell(page)

    expect(block).not_to_have_attribute("open", "")
    expect(block.locator("summary")).to_have_text(re.compile(r"Varasemad\s*\(\d+\)"))
    expect(block.locator(".notifrow").first).to_be_hidden()

    open_disclosure(block)
    expect(block).to_have_attribute("data-disclosure-saved", "1")
    expect(block.locator(".notifrow").first).to_be_visible()
    # Opening the history reads it; it marks nothing.
    assert _bell(page) == bell

    page.reload()
    expect(_history(page)).to_have_attribute("open", "")

    close_disclosure(_history(page))
    expect(_history(page)).to_have_attribute("data-disclosure-saved", "0")
    page.reload()
    expect(_history(page)).not_to_have_attribute("open", "")
    assert _bell(page) == bell


def test_the_next_page_opens_it_without_remembering_that(page, base_url):
    _run(_HISTORY_SCRIPT, 17, unique_title("A1 lehekülg"))
    sign_in(page, base_url, MARTIN)
    page.goto(f"{base_url}/teavitused/")
    block = _history(page)
    expect(block).not_to_have_attribute("open", "")
    more = block.locator("a.notifsection__more", has_text="Näita varasemaid")
    href = more.get_attribute("href")
    assert href and "varasemad=" in href

    page.goto(f"{base_url}/teavitused/{href}")
    block = _history(page)
    expect(block).to_have_attribute("open", "")
    expect(block).to_have_attribute("data-disclosure-shown", "1")
    expect(block.get_by_role("link", name="Tagasi uuemate juurde")).to_be_visible()
    page.wait_for_load_state("networkidle")
    # The browser's own toggle for the drawn-open block was not saved.
    expect(block).to_have_attribute("data-disclosure-saved", "0")

    page.goto(f"{base_url}/teavitused/")
    expect(_history(page)).not_to_have_attribute("open", "")


def test_a_link_to_varasemad_opens_it_and_a_redraw_keeps_it(page, base_url):
    _run(_HISTORY_SCRIPT, 2, unique_title("A1 viide"))
    sign_in(page, base_url, MARTIN)
    page.goto(f"{base_url}/teavitused/#varasemad")
    block = _history(page)
    expect(block).to_have_attribute("open", "")
    expect(block).to_have_attribute("data-disclosure-saved", "0")

    # The page redraws itself when the bell's panel changes something.
    page.evaluate("htmx.trigger(document.body, 'notificationsPageStale')")
    wait_for_htmx(page)
    page.wait_for_load_state("networkidle")
    expect(_history(page)).to_have_attribute("open", "")
    expect(_history(page)).to_have_attribute("data-disclosure-saved", "0")

    page.goto(f"{base_url}/teavitused/")
    expect(_history(page)).not_to_have_attribute("open", "")


# ---------------------------------------------------------------------------
# UX-003 «Viimased tegevused»
# ---------------------------------------------------------------------------


def _first_row(page, title: str):
    row = page.locator(f"{ACTIVITY} a.actline", has_text=title).first
    expect(row).to_be_visible()
    return row


def test_the_date_and_act_follow_the_title_on_one_line(page, base_url):
    title = unique_title("A1 tegevus")
    _run(_ACTIVITY_SCRIPT, 1, title)
    page.set_viewport_size({"width": 1440, "height": 900})
    sign_in(page, base_url, MARTIN)
    row = _first_row(page, title)
    name = row.locator(".actline__matter").bounding_box()
    meta = row.locator(".actline__meta").bounding_box()
    line = row.bounding_box()
    assert name and meta and line

    # Same line: the two boxes overlap vertically.
    assert abs((name["y"] + name["height"] / 2) - (meta["y"] + meta["height"] / 2)) < 6
    # Straight after the title, not at the far edge of the row.
    assert 0 <= meta["x"] - (name["x"] + name["width"]) < 16
    assert meta["x"] + meta["width"] < line["x"] + line["width"] - 40
    # One line high.
    assert line["height"] < 30


def test_twenty_rows_show_before_naita_veel(page, base_url):
    _run(_ACTIVITY_SCRIPT, 25, unique_title("A1 kakskümmend"))
    sign_in(page, base_url, MARTIN)
    feed = page.locator(ACTIVITY)
    shown = feed.locator(".foldout__body > a.actline")
    expect(shown).to_have_count(20)
    expect(feed.locator("details.pw-more > summary")).to_contain_text("Näita veel")


def test_a_long_title_gives_way_and_the_date_stays_whole(page, base_url):
    title = unique_title("A1 " + "väga pika pealkirjaga seaduseelnõu " * 8)
    _run(_ACTIVITY_SCRIPT, 1, title)
    page.set_viewport_size({"width": 1440, "height": 900})
    sign_in(page, base_url, MARTIN)
    row = _first_row(page, title[:40])
    expect(row).to_have_attribute("title", title)
    clipped = row.locator(".actline__matter").evaluate(
        "node => node.scrollWidth > node.clientWidth"
    )
    assert clipped
    meta = row.locator(".actline__meta").bounding_box()
    line = row.bounding_box()
    assert meta and line
    assert meta["x"] + meta["width"] <= line["x"] + line["width"] + 1


def test_a_phone_wraps_the_row_instead_of_cutting_it(page, base_url):
    title = unique_title("A1 telefon " + "pikk pealkiri " * 6)
    _run(_ACTIVITY_SCRIPT, 1, title)
    page.set_viewport_size({"width": 375, "height": 812})
    sign_in(page, base_url, MARTIN)
    row = _first_row(page, title[:30])
    clipped = row.locator(".actline__matter").evaluate(
        "node => node.scrollWidth > node.clientWidth"
    )
    assert not clipped
    width = page.evaluate("document.documentElement.scrollWidth")
    assert width <= 375
