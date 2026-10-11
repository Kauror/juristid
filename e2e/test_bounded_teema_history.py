"""A busy Teema shows a bounded `Teema käik`, and the whole of it on request (AUD-008, D-2).

A Matter with a long history is planted in the server's own database — more
rows than the grouped read takes by default, across two `Hetkeseis` periods —
then read at a phone, a laptop and a wide screen. At each width:

* the current period is open and complete, the older one says «+»;
* «Näita kogu käik» is offered, and following it shows the oldest row;
* nothing makes the page scroll sideways.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys

import pytest
from playwright.sync_api import expect

from e2e.conftest import (
    REPOSITORY_ROOT,
    SANDRA,
    chronology,
    create_matter,
    document_overflows,
    sign_in,
)
from e2e.titles import unique_title

pytestmark = pytest.mark.e2e

#: Plants `E2E_BUSY_ROWS` notes in the period «Idee» and a third of that in
#: «Kooskõlastus» on the Matter `E2E_BUSY_MATTER`, through the services.
_BUSY_SCRIPT = (
    "import os;"
    "from app.accounts.models import User;"
    "from app.matters.models import Matter;"
    "from app.matters.services import add_entry, change_stage;"
    "from app.workflow.models import StageVocabulary as S;"
    "m = Matter.objects.get(pk=os.environ['E2E_BUSY_MATTER']);"
    "u = m.owner or User.objects.filter(is_active=True).first();"
    "n = int(os.environ['E2E_BUSY_ROWS']);"
    "change_stage(matter=m, stage=S.objects.get(key='idea'), actor=u);"
    "[add_entry(matter=m, author=u, body=f'Vana märge {i:03d}') for i in range(n)];"
    "change_stage(matter=m, stage=S.objects.get(key='consultation'), actor=u);"
    "[add_entry(matter=m, author=u, body=f'Uus märge {i:03d}') for i in range(n // 3)];"
    "print(n)"
)


def _plant_busy_history(page, rows: int = 80) -> None:
    matter_id = re.search(r"/teemad/([0-9a-f-]{36})/", page.url).group(1)
    result = subprocess.run(  # noqa: S603
        [sys.executable, "manage.py", "shell", "-c", _BUSY_SCRIPT],
        cwd=REPOSITORY_ROOT,
        env={
            **os.environ,
            "DJANGO_SETTINGS_MODULE": "config.settings",
            "E2E_BUSY_MATTER": matter_id,
            "E2E_BUSY_ROWS": str(rows),
        },
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )
    assert result.returncode == 0, "\n".join(["stdout:", result.stdout, "stderr:", result.stderr])


@pytest.fixture
def busy_teema(page, base_url):
    sign_in(page, base_url, SANDRA)
    url = create_matter(page, base_url, unique_title("Pika ajalooga teema"))
    _plant_busy_history(page)
    return url


@pytest.mark.parametrize("width", [375, 1024, 1440])
def test_a_busy_history_is_bounded_and_whole_on_request(page, busy_teema, width):
    page.set_viewport_size({"width": width, "height": 900})
    page.goto(busy_teema)
    page.wait_for_load_state("networkidle")

    history = chronology(page)
    expect(history).to_contain_text("Uus märge 000")
    expect(history).not_to_contain_text("Vana märge 000")
    more = page.get_by_role("link", name="Näita kogu käik", exact=True)
    expect(more).to_be_visible()
    assert not document_overflows(page), f"the page scrolls sideways at {width}px"

    more.click()
    page.wait_for_load_state("networkidle")
    expect(page).to_have_url(re.compile(r"[?&]kaik=koik"))
    for stage in page.locator("#ajalugu-loend details.kaikstage").all():
        stage.evaluate("node => { node.open = true; }")
    expect(chronology(page)).to_contain_text("Vana märge 000")
    expect(page.get_by_role("link", name="Näita kogu käik", exact=True)).to_have_count(0)
    assert not document_overflows(page), f"the whole history scrolls sideways at {width}px"
