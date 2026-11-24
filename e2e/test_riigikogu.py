"""Monitooring → Riigikogu and the Teema's Riigikogu sections in a browser (ADR 0224).

The fictional Riigikogu (`manage.py seed_riigikogu_demo`) is planted in the
server's own database once, as `plant_historical_instruments` plants its pair: the
browser test itself never touches the database. The demo makes its own two Teemad,
so no other test's Teema gains a bill.
"""

from __future__ import annotations

import os
import subprocess
import sys

import pytest

from e2e.conftest import HEAD, REPOSITORY_ROOT, open_disclosure, sign_in

pytestmark = pytest.mark.writes_last


@pytest.fixture(scope="module", autouse=True)
def fictional_riigikogu() -> None:
    result = subprocess.run(
        [sys.executable, "manage.py", "seed_riigikogu_demo"],
        cwd=REPOSITORY_ROOT,
        env={**os.environ, "DJANGO_SETTINGS_MODULE": "config.settings"},
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def _open(page, base_url: str) -> None:
    sign_in(page, base_url, HEAD)
    page.goto(f"{base_url}/monitooring/riigikogu/")
    page.wait_for_load_state("networkidle")


def test_the_riigikogu_tab_sits_in_monitooring_and_invitations_come_first(page, base_url):
    _open(page, base_url)
    tabs = page.get_by_role("navigation", name="Monitooringu osad")
    assert tabs.get_by_role("link", name="Riigikogu").first.get_attribute("aria-current") == "page"
    headings = page.locator("h2").all_inner_texts()
    assert headings.index("Kojale saadetud kutsed") < headings.index("Komisjonides eesolev")
    assert page.get_by_text("Tähtaeg vajab kontrollimist").first.is_visible()
    assert page.get_by_text("Riigikogu kutsus koda arvamust avaldama").first.is_visible()


def test_a_doubtful_deadline_is_checked_from_its_own_form(page, base_url):
    _open(page, base_url)
    row = page.locator(".rkinv--check").first
    open_disclosure(row.locator("details.rkinv__more").first)
    assert row.get_by_label("Kuupäev kirjast").is_visible()
    assert row.get_by_role("button", name="Kinnita tähtaeg").is_visible()


def test_the_committee_filter_switches_between_our_teemad_and_all(page, base_url):
    _open(page, base_url)
    page.get_by_role("link", name="Kõik komisjonid").click()
    page.wait_for_load_state("networkidle")
    assert "komisjonid=koik" in page.url
    assert page.get_by_role("link", name="Kõik komisjonid").get_attribute("aria-current") == "true"
    assert not page.get_by_text("Info ja muud küsimused").count()


@pytest.mark.parametrize("width", [375, 1280])
def test_the_page_has_no_horizontal_overflow(page, base_url, width):
    page.set_viewport_size({"width": width, "height": 900})
    _open(page, base_url)
    overflow = page.evaluate(
        "document.documentElement.scrollWidth - document.documentElement.clientWidth"
    )
    assert overflow <= 0


def test_dark_mode_keeps_the_doubtful_state_readable(page, base_url):
    page.emulate_media(color_scheme="dark")
    _open(page, base_url)
    color = page.locator(".rkinv__status--check").first.evaluate("e => getComputedStyle(e).color")
    background = page.evaluate("getComputedStyle(document.body).backgroundColor")
    assert color != background
