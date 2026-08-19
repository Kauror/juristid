"""Browser-test fixtures.

These run against a real Django server on real PostgreSQL 18, because the things
they are here to prove — that the composer saves atomically and that a
restricted Matter is unreachable — are properties of the running system, not of
a mocked one.

The suite is skipped unless E2E_BASE_URL is set, so an ordinary `pytest` run
does not require a browser.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

import pytest

BASE_URL = os.environ.get("E2E_BASE_URL", "")
SCREENSHOT_DIR = os.environ.get("E2E_SCREENSHOT_DIR", "artifacts/screenshots")
DESKTOP_VIEWPORT = {"width": 1440, "height": 900}

pytestmark = pytest.mark.skipif(not BASE_URL, reason="E2E_BASE_URL is not set")


@dataclass(frozen=True)
class Persona:
    upn: str
    display_name: str


# Mirrors e2e/seed_e2e.py. Kept as data rather than looked up, so a browser test
# never has database access and therefore cannot mask an authorization bug by
# reading around the UI.
SANDRA = Persona("sandra@example.invalid", "Sandra Testjurist")
MARTIN = Persona("martin@example.invalid", "Martin Testjurist")
HEAD = Persona("juht@example.invalid", "Testosakonnajuht")
ADMIN = Persona("admin@example.invalid", "Testadministraator")


@pytest.fixture(scope="session")
def base_url() -> str:
    if not BASE_URL:
        pytest.skip("E2E_BASE_URL is not set")
    return BASE_URL.rstrip("/")


@pytest.fixture(scope="session")
def browser_context_args(browser_context_args: dict) -> dict:
    """Desktop-first: this is a tool for people with two monitors."""
    return {
        **browser_context_args,
        "viewport": DESKTOP_VIEWPORT,
        "locale": "et-EE",
        "timezone_id": "Europe/Tallinn",
    }


@pytest.fixture
def screenshots():
    """Save a named 1440px screenshot into the CI artifact directory."""
    import pathlib

    directory = pathlib.Path(SCREENSHOT_DIR)
    directory.mkdir(parents=True, exist_ok=True)

    def take(page, name: str) -> None:
        # The sticky header paints across the middle of a full-page capture and
        # hides the content behind it. Pinning it for the shot gives a complete,
        # reviewable image; the page itself is untouched.
        page.add_style_tag(content=(".topbar, .table thead th { position: static !important; }"))
        page.screenshot(path=str(directory / f"{name}.png"), full_page=True)

    return take


def sign_in(page, base_url: str, persona: Persona) -> None:
    """Sign in through the development login page.

    Production uses Entra; this is the only synthetic path and it exists solely
    so the browser suite can exercise real sessions.
    """
    page.goto(f"{base_url}/konto/arendus-sisselogimine/")
    page.get_by_label(persona.display_name, exact=False).check()
    page.get_by_role("button", name="Logi sisse").click()
    # Signing in lands on Ülevaade: the first question on opening the
    # application is what is happening across the department, and the personal
    # queue is one click away.
    page.wait_for_url(f"{base_url}/ulevaade/")


def sign_out(page, base_url: str) -> None:
    page.get_by_role("button", name="Välju").click()
    page.wait_for_load_state("networkidle")
