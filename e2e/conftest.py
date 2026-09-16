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
import re
import time
import uuid
from dataclasses import dataclass

import pytest
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

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
    # Signing in lands on Minu asjad. The development login redirects through
    # `/`, and `/` is the one place the default destination is decided
    # (app/core/views.py::home) — so this waits for where that decision leads
    # rather than repeating it.
    page.wait_for_url(f"{base_url}/minu-asjad/")


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


#: The class HTMX puts on the element it is posting for, and takes off only
#: once the response has been swapped in (`htmx.config.requestClass`, htmx
#: 2.0.4). Its absence is the one honest "the page is the page the server just
#: sent" signal a test has.
HTMX_REQUEST_CLASS = "htmx-request"

#: How long `open_add_panel` keeps looking at the page before it gives up. A
#: swap is tens of milliseconds even on a loaded runner, so reaching the end of
#: this means the panel is not going to open at all — and saying so is better
#: than handing the next line a hidden form and letting it spend the full 30s
#: locator timeout discovering the same thing.
PANEL_TIMEOUT_MS = 10_000

#: One pass's worth of patience. Short on purpose: a pass that finds itself
#: holding a control the swap has since taken away is supposed to go back and
#: look at the page again, not sit on a detached node.
PANEL_STEP_MS = 2_000


def wait_for_htmx(page, timeout: float = PANEL_TIMEOUT_MS) -> None:
    """Wait until no HTMX request is in flight — and so until its swap is done.

    `wait_for_load_state("networkidle")` is not that and cannot be. It is a
    *network* silence, watched in the browser process, while the swap it is
    being used to wait for runs afterwards in the page's own task queue; and it
    returns 500ms after the last byte whether or not the response has been
    applied. Measured on this workspace with the save held back by a route
    handler: `htmx-request` is on the document from the moment `Salvesta` is
    clicked until `#teema-vaade` has been replaced, with no sample in between
    showing one without the other.

    So this is the wait a helper needs before it reads the page: the state to
    watch is HTMX's own, not the clock and not the socket.
    """
    page.wait_for_function(
        f"() => !document.querySelector('.{HTMX_REQUEST_CLASS}')", timeout=timeout
    )


def add_panel_is_open(page, panel_id: str) -> bool:
    """Whether one `LISA TEEMALE` operation is showing its form.

    Two shapes, because `#lisa-jargmine` is two different controls: `Muuda` in
    PRAEGUNE TEGEVUS is still a lone `<details>`, and the launcher's own panels
    are revealed by the radio that names them.

    Read in one evaluation rather than three round-trips. Each round-trip is a
    chance to straddle a swap and describe half of one page and half of
    another — which is precisely the answer this function must never give.
    """
    return bool(
        page.evaluate(
            """(id) => {
                const panel = document.getElementById(id);
                if (!panel) {
                    return false;
                }
                if (panel.tagName === "DETAILS") {
                    return panel.open;
                }
                // The launcher's panels are revealed by `:checked`, so the
                // radio is the state the page itself is reading.
                const pick = document.getElementById(id + "-valik");
                return pick ? pick.checked : panel.getClientRects().length > 0;
            }""",
            panel_id,
        )
    )


def add_panel_chip(page, panel_id: str):
    """The control that opens one `LISA TEEMALE` operation.

    The launcher's chips are `<label>`s for their radios; `Muuda` is a
    `<summary>`. Both are clicked, neither is the element that grows.

    Which one exists is a fact about the Matter as it is *now* — a saved step
    replaces the chip with the disclosure — so the answer is resolved when it
    is asked for and never kept.
    """
    chip = page.locator(f'label[for="{panel_id}-valik"]')
    if chip.count():
        return chip.first
    return page.locator(f"#{panel_id} summary").first


def open_add_panel(page, panel_id: str) -> None:
    """Open one `LISA TEEMALE` operation and wait for its form.

    The zone is a choice of nine until one is picked, and picking one closes
    whichever was open (docs/adr/0075 §2). Every browser test that writes
    anything other than the current action's result goes through here, which is
    what made changing the panels from `<details>` to a radio bar on 2026-09-14
    a change to these two lines rather than to forty files.

    **Both hosts are toggles, so nothing here clicks blindly.** A click is what
    opens a closed panel and what shuts an open one — `ux.js` un-checks a chip
    that is already chosen, and a `<summary>` closes its own `<details>` — so a
    fixed number of clicks is a coin-toss on parity. This looks first, clicks
    only a control it has just seen closed, and then checks that the click did
    what it was for.

    **And it reads the page the server last sent.** A workspace save swaps
    `#teema-vaade` wholesale and `#lisa-jargmine` crosses hosts on exactly the
    save this test suite makes it cross: the launcher chip, while no step is
    open, and the `Muuda` disclosure once one is. Measured against the previous
    version of this helper, with the save still on the wire: it read the panel
    that was about to be thrown away, called it open, returned in 0.04s, and
    the replacement then arrived closed — so the form was hidden, the next line
    burned its 30s locator timeout and the test failed with a workspace that
    had rendered perfectly (e2e/test_panel_reopen_after_save.py).
    """
    deadline = time.monotonic() + PANEL_TIMEOUT_MS / 1000
    while True:
        try:
            if _open_add_panel_once(page, panel_id):
                return
        except PlaywrightTimeoutError:
            # Whatever was being waited on belonged to a page that is not on
            # the screen any more. There is nothing to recover — look again.
            pass
        if time.monotonic() >= deadline:
            raise AssertionError(
                f"#{panel_id} did not open within {PANEL_TIMEOUT_MS}ms "
                f"(open={add_panel_is_open(page, panel_id)})"
            )


def _open_add_panel_once(page, panel_id: str) -> bool:
    """One look at the page as it is now. True once the form is showing.

    Every locator is resolved inside this pass, after the wait that guarantees
    no swap is outstanding — so a pass never mixes what it saw before a swap
    with what it clicks after one.
    """
    wait_for_htmx(page)
    panel = page.locator(f"#{panel_id}")
    panel.wait_for(state="attached", timeout=PANEL_STEP_MS)
    if not add_panel_is_open(page, panel_id):
        add_panel_chip(page, panel_id).click(timeout=PANEL_STEP_MS)
        if not add_panel_is_open(page, panel_id):
            return False
    panel.locator("form").first.wait_for(state="visible", timeout=PANEL_STEP_MS)
    return True


def open_next_action_form(page) -> None:
    """`Muuda` or `+ Järgmine tegevus`, whichever this Matter is showing.

    One form, two hosts: while a step is open it is the `Muuda` disclosure
    beside the task, and once none is it is the launcher chip. Both carry the
    same `#lisa-jargmine` id, which is what lets one helper open either.

    The date box used to sit behind a «Kuupäev…» `<details>` and was opened
    here. It is now the `Täpne päev` group of the `Täpsus` control, shown
    because that chip is the one selected first — so there is nothing to
    disclose, and a test choosing another precision is choosing to fill a
    different control (docs/adr/0079 §1).
    """
    open_add_panel(page, "lisa-jargmine")
    page.locator("#id_target_date").wait_for(state="visible")


#: For a test that needs `Uus teema` to offer what the reader found.
#:
#: The suggestion area is withdrawn from that page by default
#: (docs/adr/0088), and the server this suite drives cannot be reconfigured
#: from a test the way a Django test reconfigures its own. So a scenario about
#: the reading says which deployment it needs, rather than failing over a
#: setting — and says it once, here, because two files ask for it.
#:
#: To run those: start the application with
#: `MATTER_INTAKE_SUGGESTIONS_ENABLED=1` and set `E2E_INTAKE_SUGGESTIONS=1`
#: beside `E2E_BASE_URL`. A skip is visible in pytest's own summary, which is
#: the difference between this and a scenario that quietly stops covering
#: anything.
needs_intake_reading = pytest.mark.skipif(
    not os.environ.get("E2E_INTAKE_SUGGESTIONS"),
    reason=(
        "the document reading is withdrawn from Uus teema (docs/adr/0088); "
        "set E2E_INTAKE_SUGGESTIONS=1 against a server started with "
        "MATTER_INTAKE_SUGGESTIONS_ENABLED=1 to run this"
    ),
)


def open_valdkond(page) -> None:
    """Unfold Valdkonnad on `Uus teema`, which arrives shut.

    The vocabulary moved behind a disclosure when the lawyers' first feedback
    round asked for the creation form to stop sitting permanently open
    (docs/adr/0088 §3). A closed `<details>` keeps its contents in the document
    — every `to_be_attached` and every `evaluate` over the chips still works
    through it — but nobody can *click* what nobody can see, so a test that
    ticks an area opens the field first. That is also what the person does.

    Idempotent, so a test may call it without knowing whether an earlier
    refusal already rendered the disclosure open.
    """
    disclosure = page.locator("[data-valdkond-disclosure]")
    if disclosure.count() and not disclosure.evaluate("node => node.open"):
        disclosure.locator("> summary").click()


def open_composer(page) -> None:
    """`+ Märge` — where something that happened gets written down.

    The composer this replaces asked *what happened* and *what happens next* in
    one form over one `Salvesta`; those are two intentions and two saves now.
    A test that used to type a body into the composer is recording a note, so
    that is what this opens (docs/adr/0075 §2).
    """
    open_add_panel(page, "lisa-marge")
    page.locator("#lisa-marge .composer__body").wait_for(state="visible")


def finish_current_action(page, text: str) -> None:
    """Record what was done about the current task, which completes it.

    One operation and one button: there is no `Märgi tehtuks` on this page
    (docs/adr/0075 §3).
    """
    zone = page.locator("#praegune-tegevus")
    zone.locator(".composer__body").fill(text)
    zone.locator("button[type=submit]").last.click()
    page.wait_for_load_state("networkidle")


def go_to(page, name: str) -> None:
    """Follow a top-bar destination by name, wherever the bar is keeping it.

    Navigation is priority-based: the destinations a lawyer moves between all
    day are always on the bar, and the reading surface — Statistika — is
    inline only above 1560px and behind the "Veel" disclosure below it. A test
    that clicks the link directly is asserting a layout
    decision it does not care about, so it asks for the destination and lets
    this open whatever is in the way.
    """
    navigation = page.get_by_role("navigation", name="Peamine")
    link = navigation.get_by_role("link", name=name, exact=True)
    if not link.count():
        page.locator(".topnav__trigger").click()
    link.click()
    page.wait_for_load_state("networkidle")


def unique_title(prefix: str) -> str:
    """A title no other row in this world can be carrying.

    The browser suite runs against **one seeded database per shard**, shared by
    every file the shard was given and never reset between them
    (`ci_sharding.py`, .github/workflows/ci.yml). So a fixed title is not an
    identity: a file that runs twice against the same world — a rerun, a local
    loop — files a second Matter under the same name, and every locator that
    asks for it by name then resolves to two and raises in strict mode.

    A test that has to find its own row afterwards asks for one of these instead
    of writing a constant. Short on purpose: the register's title column clips,
    and the token has to survive being read back out of a cell.
    """
    return f"{prefix} {uuid.uuid4().hex[:8]}"


def create_matter(
    page,
    base_url: str,
    title: str,
    *,
    stage: str | None = None,
    owner: Persona | None = None,
) -> str:
    """Create a Matter through the real form and return its detail URL.

    Four browser files had this verbatim. It stays a plain function rather than
    becoming a fixture for the same reason `sign_in` and `go_to` do: a fixture
    is implicit, and a test that navigates should say so on the line where it
    navigates.

    `stage` and `owner` are the two columns the register renders as a *link* —
    the value in the row is also the filter that selects it — so a test about
    those links has to be able to file a row that carries one. Both are chips on
    the form, named by what the page shows: the stage's own label, and the
    owner's short name. Left unset they stay unset, which is what the form
    defaults to and what every existing caller goes on getting.
    """
    page.goto(f"{base_url}/teemad/uus/")
    page.wait_for_load_state("networkidle")
    page.fill("#id_title", title)
    if stage is not None:
        page.get_by_role("radio", name=stage, exact=True).check()
    if owner is not None:
        page.get_by_role("radio", name=owner.short_name, exact=True).check()
    page.get_by_role("button", name="Loo teema").click()
    page.wait_for_url(re.compile(r"/teemad/[0-9a-f-]{36}/$"))
    return page.url


def open_matter(page, base_url: str, title: str) -> str:
    """Find a Matter in the register by title and open it, returning its URL.

    **Follows the link rather than clicking it.** The register's table head is
    sticky and can sit over the first row, so a click is a coin-toss that fails
    on a narrow viewport and passes on a wide one. `e2e/test_ui_regression.py`
    carries the same reasoning.

    No default title: this file deliberately imports no application code
    (see the note at the top), and the seeded titles live in
    `app.core.management.commands.seed_e2e_data`. Callers pass their own.
    """
    page.goto(f"{base_url}/teemad/?olek=koik&q={title.split()[0]}")
    page.wait_for_load_state("networkidle")
    link = page.get_by_role("link", name=title, exact=False).first
    assert link.count(), f"the register does not hold {title!r}"
    page.goto(f"{base_url}{link.get_attribute('href')}")
    page.wait_for_load_state("networkidle")
    return page.url
