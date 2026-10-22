"""Muudatused, in a browser (docs/adr/0159).

What a category *counts* is proved against the database in
`tests/test_department_changes.py`. What only a browser shows is whether the
boundary holds for somebody who types the address, whether the head can reach
the page from where they already are — the *Tehtud* heading — and whether the
page reads on a laptop and on a phone without scrolling sideways.

The restricted case: a READER lent `department.view_management` (ADR 0145)
opens the page and, since docs/adr/0181, reads the restricted Teema exactly as
the head does. The loan is made and taken back around each test that needs it,
so no other browser file meets a READER with manager sections.
"""

from __future__ import annotations

import os
import subprocess
import sys

import pytest
from playwright.sync_api import expect

from e2e.conftest import (
    HEAD,
    READER,
    REPOSITORY_ROOT,
    SANDRA,
    document_overflows,
    sign_in,
    sign_out,
)
from e2e.titles import unique_title

pytestmark = pytest.mark.e2e

PAGE_PATH = "/osakond/muudatused/"

#: Two fresh Teemad, one restricted, each moved to «Idee» by Sandra, so both
#: have a stage change inside every window the page offers.
_PLANT_SCRIPT = (
    "import os;"
    "from app.accounts.models import User;"
    "from app.core.enums import Visibility;"
    "from app.matters.services import change_stage, create_matter;"
    "from app.workflow.models import StageVocabulary as S;"
    "u = User.objects.get(upn='sandra@example.invalid');"
    "idea = S.objects.get(key='idea');"
    "open_one = create_matter(title=os.environ['E2E_OPEN_TITLE'], owner=u, actor=u, stage=None);"
    "hidden = create_matter(title=os.environ['E2E_HIDDEN_TITLE'], owner=u, actor=u, stage=None,"
    " visibility=Visibility.RESTRICTED);"
    "change_stage(matter=open_one, stage=idea, actor=u);"
    "change_stage(matter=hidden, stage=idea, actor=u);"
    "print('ok')"
)

#: Lends (`allow`) or takes back (`''`) the management view for the READER persona.
_LEND_SCRIPT = (
    "import os;"
    "from app.accounts.models import User;"
    "u = User.objects.get(upn='lugeja@example.invalid');"
    "o = dict(u.capability_overrides or {});"
    "v = os.environ['E2E_LEND'];"
    "k = 'department.view_management';"
    "o.update({k: v}) if v else o.pop(k, None);"
    "u.capability_overrides = o;"
    "u.save(update_fields=['capability_overrides']);"
    "print('ok')"
)


def _shell(script: str, **env: str) -> None:
    result = subprocess.run(  # noqa: S603
        [sys.executable, "manage.py", "shell", "-c", script],
        cwd=REPOSITORY_ROOT,
        env={**os.environ, "DJANGO_SETTINGS_MODULE": "config.settings", **env},
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )
    assert result.returncode == 0, "\n".join(["stdout:", result.stdout, "stderr:", result.stderr])


@pytest.fixture
def planted():
    titles = {
        "open": unique_title("Muudatuste avalik teema"),
        "hidden": unique_title("Muudatuste piiratud teema"),
    }
    _shell(_PLANT_SCRIPT, E2E_OPEN_TITLE=titles["open"], E2E_HIDDEN_TITLE=titles["hidden"])
    return titles


@pytest.fixture
def lent_reader():
    _shell(_LEND_SCRIPT, E2E_LEND="allow")
    try:
        yield READER
    finally:
        _shell(_LEND_SCRIPT, E2E_LEND="")


def stage_rows(page):
    return page.locator("section[data-category='hetkeseis'] .uxdg__row")


@pytest.mark.parametrize("width", [1440, 375])
def test_the_head_reaches_the_page_from_tehtud(page, base_url, planted, width):
    page.set_viewport_size({"width": width, "height": 900})
    sign_in(page, base_url, HEAD)
    page.goto(f"{base_url}/osakond/")

    page.locator("section#tehtud").get_by_role("link", name="Muudatused →").click()
    page.wait_for_load_state("networkidle")

    assert page.url.startswith(f"{base_url}{PAGE_PATH}?periood=")
    expect(page.get_by_role("heading", level=1, name="Muudatused")).to_be_visible()
    for label in (
        "Uued teemad",
        "Hetkeseisu muutused",
        "Vastutaja muutused",
        "Järgmised tegevused",
        "Taasavatud teemad",
    ):
        expect(page.get_by_role("heading", level=2, name=label)).to_be_visible()
    row = stage_rows(page).filter(has_text=planted["open"])
    expect(row).to_have_count(1)
    expect(row).to_contain_text("muutis hetkeseisu: määramata → Idee")
    expect(page.locator("[data-outputs]")).to_be_visible()
    expect(page.locator("[data-snapshots]")).to_be_visible()
    expect(page.locator("[data-coverage]")).to_be_visible()
    assert not document_overflows(page)

    href = row.get_attribute("href") or ""
    assert href.startswith("/teemad/")
    row.click()
    page.wait_for_load_state("networkidle")
    assert page.url.startswith(f"{base_url}{href}")
    expect(page.get_by_text(planted["open"]).first).to_be_visible()


def test_the_fortnight_is_a_link_that_stays_selected(page, base_url, planted):
    sign_in(page, base_url, HEAD)
    page.goto(f"{base_url}{PAGE_PATH}")

    page.get_by_role("link", name="14 päeva").click()
    page.wait_for_load_state("networkidle")

    assert page.url.endswith("?periood=14")
    expect(page.locator(".uxchip.is-selected")).to_have_text("14 päeva")


def test_a_specialist_is_refused_and_offered_no_link(page, base_url):
    sign_in(page, base_url, SANDRA)

    response = page.goto(f"{base_url}{PAGE_PATH}")

    assert response is not None and response.status == 404
    page.goto(f"{base_url}/osakond/")
    expect(page.get_by_role("link", name="Muudatused →")).to_have_count(0)


def test_a_restricted_teema_reads_the_same_for_a_reader_lent_the_view(
    page, base_url, planted, lent_reader
):
    sign_in(page, base_url, HEAD)
    page.goto(f"{base_url}{PAGE_PATH}?periood=30")
    expect(stage_rows(page).filter(has_text=planted["hidden"])).to_have_count(1)

    sign_out(page, base_url)
    sign_in(page, base_url, lent_reader)
    response = page.goto(f"{base_url}{PAGE_PATH}?periood=30")

    assert response is not None and response.status == 200
    expect(stage_rows(page).filter(has_text=planted["open"])).to_have_count(1)
    expect(stage_rows(page).filter(has_text=planted["hidden"])).to_have_count(1)


def test_an_empty_window_says_so(page, base_url):
    sign_in(page, base_url, HEAD)

    page.goto(f"{base_url}{PAGE_PATH}?periood=vahemik&alates=1.1.2001&kuni=2.1.2001")

    # Wholly before change recording began: unknown, not «0 muudatust».
    expect(page.locator("[data-changes-unrecorded]")).to_have_text(
        "Sel perioodil muudatusi veel ei salvestatud."
    )
    expect(page.locator("[data-unrecorded]")).to_be_visible()
    expect(page.locator("[data-partial]")).to_have_count(0)
    expect(page.get_by_role("heading", name="Muudatuste arv teadmata")).to_be_visible()
    expect(page.locator(".uxdg__row")).to_have_count(0)


def test_a_reversed_range_is_refused_in_words(page, base_url):
    sign_in(page, base_url, HEAD)

    page.goto(f"{base_url}{PAGE_PATH}?periood=vahemik&alates=10.10.2026&kuni=1.10.2026")

    expect(page.locator("[data-period-error]")).to_contain_text(
        "Vahemiku algus on pärast selle lõppu."
    )
    expect(page.locator("input[name='alates']")).to_have_value("10.10.2026")


# ---- Visual QA round 2c ----------------------------------------------------


_NOT_CLIPPED = """el => {
    const r = el.getBoundingClientRect();
    return {right: r.right, width: window.innerWidth,
            clipped: el.scrollWidth > el.clientWidth + 1};
}"""

_UNCOVERED = """el => {
    const r = el.getBoundingClientRect();
    const hit = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2);
    return el.contains(hit);
}"""


def test_the_change_itself_is_readable_on_a_phone(page, base_url, planted):
    page.set_viewport_size({"width": 375, "height": 900})
    sign_in(page, base_url, HEAD)
    page.goto(f"{base_url}{PAGE_PATH}?periood=30")

    row = stage_rows(page).filter(has_text=planted["open"])
    change = row.locator("[data-change]")
    expect(change).to_have_text("— muutis hetkeseisu: määramata → Idee")
    expect(change).to_be_visible()
    text = row.locator(".uxdg__text").evaluate(_NOT_CLIPPED)
    assert not text["clipped"], "the row text is still cut to one line"
    box = change.evaluate(_NOT_CLIPPED)
    assert box["right"] <= box["width"], box
    assert not document_overflows(page)


@pytest.mark.parametrize("width", [1440, 1024, 375])
def test_a_refused_range_message_is_not_covered(page, base_url, width):
    page.set_viewport_size({"width": width, "height": 900})
    sign_in(page, base_url, HEAD)

    page.goto(f"{base_url}{PAGE_PATH}?periood=vahemik&alates=10.10.2026&kuni=1.10.2026")

    notice = page.locator("[data-period-error]")
    expect(notice).to_be_visible()
    assert notice.evaluate(_UNCOVERED), "something is drawn over the refusal"
    assert not notice.evaluate(_NOT_CLIPPED)["clipped"]
    expect(page.locator("[data-period-inline] input[name='alates']")).to_be_visible()
    expect(page.locator(".uxdg__periodbody")).to_have_count(0)
    assert not document_overflows(page)
