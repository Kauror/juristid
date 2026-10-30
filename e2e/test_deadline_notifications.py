"""Personal deadline notices in a browser (docs/adr/0198).

`tests/test_deadline_notifications.py` proves the rules. This proves what only a
running server shows: the bell fetches its number after the page has drawn,
the one «Teavitused» block on Minu asjad carries a notice and can be worked from
the keyboard, and «Märgi loetuks» and «Peida» are real forms that do different
things.

The deadline is planted through the ORM on a Teema made through the UI, and the
generator is the real management command the `notifications` worker runs.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys

import pytest
from playwright.sync_api import expect

from e2e.conftest import MARTIN, REPOSITORY_ROOT, create_matter, sign_in, unique_title

pytestmark = pytest.mark.e2e

#: Puts an `Arvamuse tähtaeg` three Tallinn days ahead on `E2E_NOTICE_MATTER`:
#: the 3-day interval of the default {7, 3, 1} schedule is owed today.
_PLANT_SCRIPT = (
    "import os;"
    "from datetime import timedelta;"
    "from django.utils import timezone;"
    "from app.matters.models import Matter;"
    "Matter.objects.filter(pk=os.environ['E2E_NOTICE_MATTER'])"
    ".update(response_deadline=timezone.localdate() + timedelta(days=3));"
    "print('ok')"
)

BLOCK = "section[aria-label='Teavitused']"


def _manage(*args: str, **env: str) -> str:
    result = subprocess.run(  # noqa: S603
        [sys.executable, "manage.py", *args],
        cwd=REPOSITORY_ROOT,
        env={**os.environ, "DJANGO_SETTINGS_MODULE": "config.settings", **env},
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert result.returncode == 0, "\n".join(["stdout:", result.stdout, "stderr:", result.stderr])
    return result.stdout


def _planted(page, base_url: str, label: str) -> str:
    title = unique_title(label)
    url = create_matter(page, base_url, title, owner=MARTIN)
    matter_id = re.search(r"/teemad/([0-9a-f-]{36})/", url).group(1)
    _manage("shell", "-c", _PLANT_SCRIPT, E2E_NOTICE_MATTER=matter_id)
    summary = _manage("generate_commencement_reminders")
    assert "Tähtaegade teavitused" in summary
    return title


def test_a_notice_is_counted_read_and_hidden(page, base_url):
    sign_in(page, base_url, MARTIN)
    title = _planted(page, base_url, "Tähtaja teavitus")

    page.goto(f"{base_url}/minu-asjad/")
    block = page.locator(BLOCK)
    row = block.locator("[data-inbox-type='deadline']", has_text=title)
    expect(row).to_contain_text("Arvamuse tähtaeg läheneb")
    expect(row).to_contain_text("3 päeva pärast")
    expect(page.locator(".notifbell__count").first).to_be_attached()

    # The list: read is not hide.
    page.goto(f"{base_url}/teavitused/")
    row = page.locator(".notifrow", has_text=title)
    expect(row).to_have_class(re.compile("notifrow--unread"))
    row.get_by_role("button", name=f"Märgi loetuks: {title}").click()
    page.wait_for_url(f"{base_url}/teavitused/")
    row = page.locator(".notifrow", has_text=title)
    expect(row).to_have_count(1)
    expect(row).not_to_have_class(re.compile("notifrow--unread"))
    expect(row.get_by_role("button", name=re.compile("^Märgi loetuks"))).to_have_count(0)

    row.get_by_role("button", name=f"Peida: {title}").click()
    page.wait_for_url(f"{base_url}/teavitused/")
    expect(page.locator(".messages")).to_contain_text("Teavitus peideti.")
    expect(page.locator(".notifrow", has_text=title)).to_have_count(0)

    # A second run of the generator does not bring it back.
    _manage("generate_commencement_reminders")
    page.goto(f"{base_url}/teavitused/")
    expect(page.locator(".notifrow", has_text=title)).to_have_count(0)


def test_the_minu_asjad_block_works_from_the_keyboard(page, base_url):
    sign_in(page, base_url, MARTIN)
    title = _planted(page, base_url, "Klaviatuuri teavitus")

    page.goto(f"{base_url}/minu-asjad/")
    button = page.locator(BLOCK).get_by_role("button", name=f"Peida: {title}")
    expect(button).to_be_visible()
    button.focus()
    expect(button).to_be_focused()
    page.keyboard.press("Enter")

    # Back on Minu asjad, at the block, and the notice is gone from it.
    page.wait_for_url(re.compile(r"/minu-asjad/#teavitused$"))
    expect(page.locator(f"{BLOCK} [data-inbox-item]", has_text=title)).to_have_count(0)


def test_the_settings_page_saves_and_restores(page, base_url):
    sign_in(page, base_url, MARTIN)
    page.goto(f"{base_url}/teavitused/seaded/")
    email = page.locator("#notif-email")
    expect(email).to_be_disabled()
    expect(page.locator("#notif-email-note")).to_contain_text("isikupõhise sisselogimisega")

    page.locator("#notif-IMPORTANT_DATE-enabled").check()
    page.get_by_role("button", name="Salvesta").click()
    page.wait_for_url(f"{base_url}/teavitused/seaded/")
    expect(page.locator("#notif-IMPORTANT_DATE-enabled")).to_be_checked()

    page.get_by_role("button", name="Taasta vaikimisi").click()
    page.wait_for_url(f"{base_url}/teavitused/seaded/")
    expect(page.locator("#notif-IMPORTANT_DATE-enabled")).not_to_be_checked()
