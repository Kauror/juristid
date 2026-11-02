"""«Kontrolli jõustumist» in a browser: the lifecycle a lawyer sees (docs/adr/0188).

`tests/test_commencement_reminders*.py` prove the rules. This proves the pieces
that exist only on a running server: the bell fetches its number after the page
has rendered, the Minu asjad block carries the prompt, the list's «Märgi
loetuks» is a real form that takes it off the reader's list without answering
it, and nothing on the way says the act entered into force. «Kontrollitud»
(2026-10-30) and «Sulge…» (2026-11-02) are retired, so the row offers neither.

The commencement is planted through the server's own services and the generator
is the real management command — exactly what the `notifications` worker runs —
because there is no button that generates a reminder, by design. The date is
today: the check itself is generated on the day (owner decision D1,
docs/adr/0198); the advance reminder is a deadline notice, tested beside it.
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

#: Records a commencement on today's Tallinn date on the Matter
#: `E2E_REMINDER_MATTER`, through `add_effective_date`.
_PLANT_SCRIPT = (
    "import os;"
    "from datetime import timedelta;"
    "from django.utils import timezone;"
    "from app.accounts.models import User;"
    "from app.intelligence.services import add_effective_date;"
    "from app.matters.models import Matter;"
    "day = timezone.localdate();"
    "add_effective_date(matter=Matter.objects.get(pk=os.environ['E2E_REMINDER_MATTER']),"
    " date_value=day, period_end=day, description='E2E põhiosa jõustub',"
    " actor=User.objects.get(upn='martin@example.invalid'));"
    "print('ok')"
)


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


def test_a_commencement_prompt_is_seen_read_and_gone(page, base_url):
    sign_in(page, base_url, MARTIN)
    title = unique_title("Jõustumise meeldetuletus")
    url = create_matter(page, base_url, title, owner=MARTIN)
    matter_id = re.search(r"/teemad/([0-9a-f-]{36})/", url).group(1)

    _manage("shell", "-c", _PLANT_SCRIPT, E2E_REMINDER_MATTER=matter_id)
    summary = _manage("generate_commencement_reminders")
    assert "Jõustumise meeldetuletused" in summary

    # Minu asjad carries the block, and the bell its number after the page loads.
    # Attached rather than visible: on this branch the bell sits among the
    # secondary destinations, which at desktop width are inside «Veel»; the
    # integrator moves it beside the theme toggle (docs/adr/0188 §6).
    page.goto(f"{base_url}/minu-asjad/")
    # One «Teavitused» block since docs/adr/0198; the check is one of its items.
    block = page.locator("section[aria-label='Teavitused']")
    expect(block).to_contain_text("Kontrolli jõustumist")
    expect(block).to_contain_text(title)
    expect(page.locator(".notifbell__count").first).to_be_attached()

    # The list: the row, its link to the Teema's Jõustumine section, no verdict.
    page.goto(f"{base_url}/teavitused/")
    row = page.locator(".notifrow", has_text=title)
    # Two lines: the title and the date. The description is on the Teema page.
    expect(row).not_to_contain_text("E2E põhiosa jõustub")
    expect(row).to_contain_text("Jõustumise kuupäev")
    expect(row.get_by_role("link", name=title)).to_have_attribute(
        "href", re.compile("#joustumine$")
    )
    expect(page.locator("main")).not_to_contain_text("jõustus")

    expect(row.get_by_role("button", name="Kontrollitud")).to_have_count(0)
    expect(row).not_to_contain_text("Ametlik allikas on märkimata")
    expect(row.locator("summary", has_text="Sulge")).to_have_count(0)
    # «Lükka edasi» opens its date field directly under the item.
    row.locator("summary", has_text="Lükka edasi").click()
    expect(row.get_by_label("Kuni")).to_be_visible()
    row.get_by_role("button", name=f"Märgi loetuks: {title}").click()
    page.wait_for_url(f"{base_url}/teavitused/")
    expect(page.locator(".notifrow", has_text=title)).to_have_count(0)

    # A second run of the generator does not bring it back.
    _manage("generate_commencement_reminders")
    page.goto(f"{base_url}/minu-asjad/")
    expect(page.locator(".notifrow", has_text=title)).to_have_count(0)
