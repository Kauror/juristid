"""`Vaatasin üle` in a real browser: from Minu asjad to a review, and a typo fix
that leaves a waiting step waiting (ENG-021).

What only a running page can settle:

* that Minu asjad's «Vaatasin üle…» on a ripe review **lands on a control** —
  the disclosure beside the step opens and the caret is in its date box — rather
  than on a zone that offered only `Muuda` and completion;
* that saving it through HTMX moves the step's date, keeps the step, and writes
  one review into the history;
* that `Muuda` on the same step, with only the sentence changed, leaves it a
  waiting step: the review control is still beside it and the zone is not late.

The service and route rules — the kind carried through an edit, the refusals,
the second POST — are `tests/test_wait_monitor_review_path.py`, which is cheap
and runs everywhere.

**On a Matter this file creates.** The native forms create only plans, so the
waiting step is written by `set_next_action` in a subprocess, the way
`e2e/test_website_overview.py` plans a write-up. The seeded «Ootamise ülevaatuse
aeg on käes» is left alone: `e2e/test_department_page.py` and
`e2e/test_kpi_navigation.py` read it, and a review here would move it.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path

import pytest
from playwright.sync_api import expect

from e2e.conftest import (
    SANDRA,
    create_matter,
    open_next_action_form,
    sign_in,
    unique_title,
    wait_for_htmx,
)

pytestmark = pytest.mark.e2e

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]

#: A `WAIT` whose review date was yesterday, on the Matter named by
#: `E2E_REVIEW_MATTER`. A literal with the id in the environment, so the
#: `subprocess.run` below has no constructed arguments — the shape
#: `e2e/test_website_overview.py` already uses.
_WAIT_SCRIPT = (
    "import os, datetime;"
    "from django.utils import timezone;"
    "from app.matters.models import Matter;"
    "from app.workflow.services import set_next_action;"
    "m = Matter.objects.get(pk=os.environ['E2E_REVIEW_MATTER']);"
    "set_next_action(matter=m, text='Ootame ministeeriumi vastust', kind='WAIT',"
    " date_semantics='REVIEW_ON',"
    " target_date=timezone.localdate() - datetime.timedelta(days=1),"
    " responsible=m.owner, actor=m.owner)"
)

#: How many reviews the history holds for that Matter.
_COUNT_SCRIPT = (
    "import os;"
    "from app.audit.models import ChangeEvent;"
    "print(ChangeEvent.objects.filter(matter_id=os.environ['E2E_REVIEW_MATTER'],"
    " event_type='NEXT_ACTION_REVIEWED').count())"
)


def _in_the_server(script: str, matter_id: str) -> str:
    """Run ``script`` against the server's own database.

    `DJANGO_SETTINGS_MODULE` is forced for the reason `run_worker` gives: pytest
    sets `config.test_settings` for itself and a child would inherit it.
    """
    result = subprocess.run(  # noqa: S603
        [sys.executable, "manage.py", "shell", "-c", script],
        cwd=REPOSITORY_ROOT,
        env={
            **os.environ,
            "DJANGO_SETTINGS_MODULE": "config.settings",
            "E2E_REVIEW_MATTER": matter_id,
        },
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    report = "\n".join(["stdout:", result.stdout, "stderr:", result.stderr])
    assert result.returncode == 0, report
    return result.stdout.strip()


def _a_waiting_matter(page, base_url: str) -> tuple[str, str, str]:
    """A Matter on Sandra's desk whose one step waits, due since yesterday."""
    title = unique_title("ENG-021 ülevaatus")
    url = create_matter(page, base_url, title, owner=SANDRA)
    matter_id = url.rstrip("/").rsplit("/", 1)[-1]
    _in_the_server(_WAIT_SCRIPT, matter_id)
    return title, url, matter_id


def _in_two_weeks() -> str:
    value = date.today() + timedelta(days=14)
    return f"{value.day}.{value.month}.{value.year}"


def test_vaatasin_ule_lands_on_the_review_and_records_it_once(page, base_url):
    sign_in(page, base_url, SANDRA)
    title, matter_url, matter_id = _a_waiting_matter(page, base_url)
    matter_path = re.sub(r"^https?://[^/]+", "", matter_url)

    page.goto(f"{base_url}/minu-asjad/")
    page.wait_for_load_state("networkidle")

    # The row for this Matter, and nothing page-wide: the browser world is
    # shared, and other files' ripe reviews are on this page too.
    row = page.locator("[data-workrow]").filter(has_text=title)
    expect(row).to_have_count(1)
    expect(row).to_have_class(re.compile(r"workrow2--review"))
    row.locator("summary.rowmenu__trigger").click()
    link = row.get_by_role("link", name="Vaatasin üle…")
    expect(link).to_have_attribute("href", f"{matter_path}#vaatasin-ule")
    link.click()
    page.wait_for_url(re.compile(re.escape(matter_path)))

    # Arrival opened the panel and put the caret in its date box.
    panel = page.locator("#vaatasin-ule")
    box = panel.get_by_label("Järgmine ülevaatus")
    expect(box).to_be_visible()
    expect(panel).to_have_attribute("open", "")
    expect(box).to_be_focused()

    box.fill(_in_two_weeks())
    panel.get_by_role("button", name="Salvesta ülevaatus").click()
    wait_for_htmx(page)

    zone = page.locator("#praegune-tegevus")
    expect(zone.locator(".curact__text")).to_have_text("Ootame ministeeriumi vastust")
    expect(zone.locator(".curact__date")).to_contain_text(_in_two_weeks())
    expect(zone).not_to_have_class(re.compile("curact--overdue"))
    # Still a step that waits: the review is offered again, shut.
    expect(page.locator("#vaatasin-ule")).to_have_count(1)
    expect(page.locator("#vaatasin-ule")).not_to_have_attribute("open", "")

    assert _in_the_server(_COUNT_SCRIPT, matter_id) == "1"

    # And the row is no longer ripe: it was reviewed, and its next review is
    # a fortnight away.
    page.goto(f"{base_url}/minu-asjad/")
    page.wait_for_load_state("networkidle")
    row = page.locator("[data-workrow]").filter(has_text=title)
    expect(row).to_have_count(1)
    expect(row).not_to_have_class(re.compile(r"workrow2--review"))


def test_a_typo_fix_leaves_a_waiting_step_waiting(page, base_url):
    sign_in(page, base_url, SANDRA)
    _title, matter_url, _matter_id = _a_waiting_matter(page, base_url)
    page.goto(matter_url)
    page.wait_for_load_state("networkidle")

    open_next_action_form(page)
    page.locator("#lisa-jargmine [name='text']").fill("Ootame ministeeriumi vastust (parandatud)")
    page.locator("#lisa-jargmine button[type=submit]").first.click()
    wait_for_htmx(page)

    zone = page.locator("#praegune-tegevus")
    expect(zone.locator(".curact__text")).to_have_text("Ootame ministeeriumi vastust (parandatud)")
    # Yesterday's review date is still a review that has come round, not a
    # deadline one day late: no overdue state, no day count, and the review
    # control — drawn only beside a step that waits — is still there.
    expect(zone).not_to_have_class(re.compile("curact--overdue"))
    expect(zone.locator(".curact__date--overdue")).to_have_count(0)
    expect(zone.locator("#vaatasin-ule")).to_have_count(1)


@pytest.mark.parametrize("width", [375, 420, 1024, 1440])
def test_the_review_control_fits_every_width(page, base_url, width):
    """Opened, at phone and desktop widths: nothing scrolls sideways, and the
    date box and the save are both reachable."""
    sign_in(page, base_url, SANDRA)
    _title, matter_url, _matter_id = _a_waiting_matter(page, base_url)
    page.set_viewport_size({"width": width, "height": 900})
    page.goto(f"{matter_url}#vaatasin-ule")
    page.wait_for_load_state("networkidle")

    panel = page.locator("#vaatasin-ule")
    expect(panel.get_by_label("Järgmine ülevaatus")).to_be_visible()
    expect(panel.get_by_role("button", name="Salvesta ülevaatus")).to_be_visible()
    overflow = page.evaluate(
        "() => document.documentElement.scrollWidth - document.documentElement.clientWidth"
    )
    assert overflow <= 1, f"the page scrolls sideways by {overflow}px at {width}"
