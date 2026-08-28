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

#: A second server, on the same database, running `AUTH_MODE=shared_gate`.
#:
#: The persona switcher only exists in that mode — the routes 404 in the other
#: two, because there is no list of people somebody may become when the
#: deployment authenticates an individual. The rest of the browser suite runs
#: against the synthetic sign-in, so the choice was between converting every
#: existing test to the gate or standing up one more `runserver` beside it. One
#: more server is a step in the workflow; converting the suite would have made
#: every unrelated test depend on a password (docs/adr/0034).
GATE_BASE_URL = os.environ.get("E2E_GATE_BASE_URL", "")
GATE_PASSWORD = os.environ.get("E2E_GATE_PASSWORD", "")

pytestmark = pytest.mark.skipif(not BASE_URL, reason="E2E_BASE_URL is not set")


@dataclass(frozen=True)
class Persona:
    upn: str
    display_name: str

    @property
    def short_name(self) -> str:
        """What the ordinary work UI calls this person.

        Mirrors `User.get_short_name`, because these tests have no database
        access on purpose and a browser test that looked the answer up in the
        model could not notice the page disagreeing with it.
        """
        return self.display_name.split(" ")[0]


# Mirrors e2e/seed_e2e.py. Kept as data rather than looked up, so a browser test
# never has database access and therefore cannot mask an authorization bug by
# reading around the UI.
SANDRA = Persona("sandra@example.invalid", "Sandra Testjurist")
MARTIN = Persona("martin@example.invalid", "Martin Testjurist")
HEAD = Persona("juht@example.invalid", "Testosakonnajuht")
ADMIN = Persona("admin@example.invalid", "Testadministraator")
#: The viewer who may not read the department's restricted work. Since
#: docs/adr/0042 a lawyer may, so a specialist can no longer play this part.
READER = Persona("lugeja@example.invalid", "Testlugeja")


@pytest.fixture(scope="session")
def base_url() -> str:
    if not BASE_URL:
        pytest.skip("E2E_BASE_URL is not set")
    return BASE_URL.rstrip("/")


@pytest.fixture(scope="session")
def gate_base_url() -> str:
    """The shared-gate server, or a skip that names what is missing."""
    if not GATE_BASE_URL or not GATE_PASSWORD:
        pytest.skip("E2E_GATE_BASE_URL and E2E_GATE_PASSWORD are not both set")
    return GATE_BASE_URL.rstrip("/")


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


def pass_the_gate(page, gate_base_url: str) -> None:
    """Type the department password, and land on the dashboard behind it.

    The gate is authentication and the persona is not, which is the whole point
    of the mode — so a persona test starts here, past the door and with nobody
    selected, exactly as a visitor does (docs/adr/0016).
    """
    page.goto(f"{gate_base_url}/konto/varav/")
    page.get_by_label("Parool", exact=False).fill(GATE_PASSWORD)
    page.get_by_role("button", name="Sisene").click()
    page.wait_for_load_state("networkidle")


def navigation_targets(page) -> set[str]:
    """Every destination the main navigation offers, by href.

    Presence rather than visibility, because the bar is priority-based: the
    reading destinations are laid out inline above 1560px and folded into the
    "Veel" disclosure below it, so a visibility assertion at 1440px would be
    asserting a layout decision instead of the access rule it means to check.
    """
    links = page.locator("nav[aria-label='Peamine'] a")
    return {links.nth(index).get_attribute("href") or "" for index in range(links.count())}


def sign_out(page, base_url: str) -> None:
    page.get_by_role("button", name="Välju").click()
    page.wait_for_load_state("networkidle")


def go_to(page, name: str) -> None:
    """Follow a top-bar destination by name, wherever the bar is keeping it.

    Navigation is priority-based: the four destinations a lawyer moves between
    all day are always on the bar, and the reading surfaces — Jälgimine,
    Statistika, Osakonna töö — are inline only above 1560px and behind the
    "Veel" disclosure below it. A test that clicks the link directly is
    asserting a layout decision it does not care about, so it asks for the
    destination and lets this open whatever is in the way.
    """
    navigation = page.get_by_role("navigation", name="Peamine")
    link = navigation.get_by_role("link", name=name, exact=True)
    if not link.count():
        page.locator(".topnav__trigger").click()
    link.click()
    page.wait_for_load_state("networkidle")
