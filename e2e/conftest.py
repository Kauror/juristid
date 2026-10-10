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
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

import pytest
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

import ci_skip_policy

# Re-exported: every browser file already says `from e2e.conftest import
# unique_title`, and the function moved out only so that a test with no browser
# can hold it to its own rule (`e2e/titles.py`).
from e2e.titles import RESERVED_REGISTER_WORDS, unique_title  # noqa: F401

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


@pytest.hookimpl(trylast=True)
def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Run every `writes_last` test after everything else, in collection order.

    **Last in its file is not last in the run.** pytest runs parametrised items
    in waves: every test's first parameter, then every test's second, and so on
    (see the ordering note in `e2e/test_ui_regression.py`). A scenario defined
    at the foot of the visual suite ran before `teemad-1280`, `teemad-3440` and
    `statistika-3440`, and the Matter it filed moved all three.

    `trylast`, so this reads the order pytest has already settled on — fixture
    reordering included — and only moves the marked items to its end.
    """
    last = [item for item in items if item.get_closest_marker("writes_last")]
    if last:
        items[:] = [item for item in items if not item.get_closest_marker("writes_last")] + last


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


def _missing(message: str) -> None:
    """A skip on a laptop, and a failure in CI.

    In CI a missing variable is a broken workflow, and a skip would have turned
    the whole browser gate green (ENG-051). Locally it means no server was
    started, which is not a defect.
    """
    if ci_skip_policy.enforced():
        pytest.fail(f"{message}: the browser suite cannot run in CI without it", pytrace=False)
    pytest.skip(message)


@pytest.fixture(scope="session")
def base_url() -> str:
    if not BASE_URL:
        _missing("E2E_BASE_URL is not set")
    return BASE_URL.rstrip("/")


@pytest.fixture(scope="session")
def gate_base_url() -> str:
    """The shared-gate server, or a skip (a failure in CI) that names what is missing."""
    if not GATE_BASE_URL or not GATE_PASSWORD:
        _missing("E2E_GATE_BASE_URL and E2E_GATE_PASSWORD are not both set")
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


#: What Chromium writes to the console when a page's own policy refused
#: something, and what htmx writes when `allowEval = false` refused a string.
#: Lower-cased: the wording is the browser's, not ours.
BROWSER_POLICY_REFUSALS = (
    "content security policy",
    "permissions-policy",
    "permissions policy",
    "evaldisallowederror",
)


@pytest.fixture(autouse=True)
def the_browser_policy_refuses_nothing(request):
    """Fail any browser test on whose pages the browser policy refused something.

    Every HTML page is served with a Content-Security-Policy and a
    Permissions-Policy (app/core/browser_policy.py, ENG-124). A refusal does
    not raise: the browser drops the script, the style or the request, writes
    one line to the console, and the feature that needed it simply stops —
    which a test may or may not happen to notice. So every test that drives a
    page is also a check that the policy cost that workflow nothing, and a
    console line is enough to fail it.

    Every page of the test's context is watched, not only the first, because a
    link can open another. A test that means to provoke a refusal builds a
    context of its own (`e2e/test_browser_policy.py`), which this does not see.
    """
    if "page" not in request.fixturenames:
        yield
        return
    page = request.getfixturevalue("page")
    refused: list[str] = []

    def watch(target) -> None:
        target.on(
            "console",
            lambda message: (
                refused.append(f"{message.type}: {message.text}")
                if any(marker in message.text.lower() for marker in BROWSER_POLICY_REFUSALS)
                else None
            ),
        )

    watch(page)
    page.context.on("page", watch)
    yield
    assert not refused, (
        "the browser policy refused something on this test's pages — a workflow "
        "that depends on inline code, an evaluated string or another origin:\n  "
        + "\n  ".join(refused)
    )


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


#: Which family each `LISA TEEMALE` sub-choice lives inside.
#:
#: The launcher is four chips rather than twelve since docs/adr/0097 §8, and the
#: distinctions it used to put on one row are asked second, inside the family
#: that was chosen. So `marge-tahtaeg` is two clicks from a fresh page and
#: `lisa-kaasamine` is one, and a helper that clicked once for both would look
#: at a shut panel and call it a failing feature.
#:
#: A map rather than a rule read off the DOM: what is nested is a product
#: decision, and a test suite that discovered it by walking parents would go on
#: passing if the nesting silently changed.
PANEL_FAMILY = {
    # `+ Lisa` — `Tavaline` left on 2026-10-07; `Arvamuse tähtaeg` is first.
    "marge-arvamuse-tahtaeg": "lisa-marge",
    "marge-tahtaeg": "lisa-marge",
    "marge-joustumine": "lisa-marge",
    "marge-toovoit": "lisa-marge",
    "arvamus-tagasiside": "lisa-arvamus",
    "arvamus-teiste": "lisa-arvamus",
    "arvamus-koja": "lisa-arvamus",
    # `+ Kaasamine` is start, then feedback since docs/adr/0142.
    "kaasamine-alusta": "lisa-kaasamine",
    "kaasamine-tagasiside": "lisa-kaasamine",
}


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

    **Two levels where the panel is a sub-choice.** `PANEL_FAMILY` says which
    chip has to be pressed first; the family is opened by this same function,
    so the parity checking and the HTMX settling below apply to both clicks
    (docs/adr/0097 §8).

    The zone is a choice of ten until one is picked, and picking one closes
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
    save this test suite makes it cross: `+ Lisa tegevus` (once the
    launcher chip) while no step is open, and the `Muuda` disclosure once one
    is. Measured against the previous
    version of this helper, with the save still on the wire: it read the panel
    that was about to be thrown away, called it open, returned in 0.04s, and
    the replacement then arrived closed — so the form was hidden, the next line
    burned its 30s locator timeout and the test failed with a workspace that
    had rendered perfectly (e2e/test_panel_reopen_after_save.py).
    """
    family = PANEL_FAMILY.get(panel_id)
    deadline = time.monotonic() + PANEL_TIMEOUT_MS / 1000
    while True:
        try:
            # **Inside the loop, with everything else.** This read the family
            # once, before the loop, and that is one read of the page taken
            # outside the discipline the rest of this helper keeps.
            #
            # What it cost: a save's swap still on the wire, the family chip
            # checked on the page about to be thrown away, so the check passed
            # and the step was skipped — and the replacement then arrived with
            # the family shut. The sub-choice's own chip lives *inside* that
            # panel, so it was `display: none`; Playwright will not click a
            # hidden control, every pass burned its click timeout, and the
            # helper reported «did not open (open=False)» about a page whose
            # family it had never opened.
            if family is not None and not add_panel_is_open(page, family):
                _open_family(page, family)
            if _open_add_panel_once(page, panel_id):
                return
        except PlaywrightTimeoutError:
            # Whatever was being waited on belonged to a page that is not on
            # the screen any more. There is nothing to recover — look again.
            pass
        if time.monotonic() >= deadline:
            raise AssertionError(
                f"#{panel_id} did not open within {PANEL_TIMEOUT_MS}ms "
                f"(open={add_panel_is_open(page, panel_id)}"
                + (f", {family}={add_panel_is_open(page, family)}" if family else "")
                + ")"
            )


def _open_family(page, family: str) -> None:
    """Open a family panel as the first of two steps, without asking for a form.

    The family is the way to its sub-choice, so what it must show is its chips,
    not a form: `+ Lisa` opens on `Arvamuse tähtaeg`, which on a Matter with a
    current deadline draws a note and no form (2026-10-07). Waiting on a
    visible form there refused a family that was open.
    """
    wait_for_htmx(page)
    page.locator(f"#{family}").wait_for(state="attached", timeout=PANEL_STEP_MS)
    if not add_panel_is_open(page, family):
        add_panel_chip(page, family).click(timeout=PANEL_STEP_MS)
    if not add_panel_is_open(page, family):
        raise PlaywrightTimeoutError(f"#{family} did not open")


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
    # **A visible form, not the first one.** A family panel contains its
    # sub-choices' forms as well as their chips, and only the chosen one is
    # shown — so `form >> nth=0` inside `+ Märge` is `Tavaline`'s, which is
    # hidden whenever somebody has chosen `Oluline tähtaeg`. Waiting on it then
    # times out on a panel that is open, and the helper reports
    # «did not open (open=True)», which is the confusing shape of a right
    # answer to the wrong question (docs/adr/0097 §8).
    panel.locator("form").locator("visible=true").first.wait_for(
        state="visible", timeout=PANEL_STEP_MS
    )
    return True


def close_add_panel(page, panel_id: str) -> None:
    """Press an open choice again, which is how the zone shuts one.

    The browser's own radio group cannot un-check a chosen radio; `ux.js` adds
    that, and it is the one behaviour `open_add_panel` deliberately will not
    perform — that helper looks before it clicks, precisely so a chip which
    arrives chosen is not closed by the act of asking for it (docs/adr/0097
    §8).
    """
    add_panel_chip(page, panel_id).click(timeout=PANEL_STEP_MS)
    wait_for_htmx(page)


def set_next_step(page, text: str, when: str) -> None:
    """Give a Matter its next step, through the one control there is for it.

    `#lisa-jargmine` in `PRAEGUNE TEGEVUS`: `Muuda` while a task is open and
    `+ Lisa tegevus` while none is — the same `next_action_panel.html` either
    way (docs/adr/0126 §1). Until 2026-10-07 a Matter with no task was given
    one through `+ Lisa · Tavaline` with `Märgi järgmiseks tegevuseks` ticked;
    that panel is gone, so this no longer records a `Märge` beside the step.

    `when` is an Estonian date as the box takes it.
    """
    open_next_action_form(page)
    page.locator("#lisa-jargmine [name='text']").fill(text)
    page.locator("#id_target_date").fill(when)
    page.locator("#lisa-jargmine button[type=submit]").first.click()
    page.wait_for_load_state("networkidle")


def open_next_action_form(page) -> None:
    """`Muuda` or `+ Lisa tegevus`, whichever this Matter is showing.

    One form, two hosts: while a step is open it is the `Muuda` disclosure
    beside the task, and once none is it is `+ Lisa tegevus` in the
    same zone (docs/adr/0126 §1). Both are `next_action_panel.html`, with the
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


#: The two classification blocks that have changed shape most often. Selected by
#: the field they hold rather than by a class, because the class is what three
#: rounds have changed and the name is what has not.
#:
#: Both are plain `<fieldset>`s again. They were permanently drawn chip rows,
#: then `<details>` folds (docs/adr/0088 §3), then `chipmenu`s whose panels
#: overlaid the form (docs/adr/0094 §2), and the owner's live audit put them
#: back: a classification a lawyer can read without opening anything is what
#: `Õigusakt` beside them always was (docs/adr/0096 §2).
VALDKONNAD_FIELD = 'fieldset:has(> .chiprow input[name="policy_areas"])'
HETKESEIS_FIELD = 'fieldset:has(> .chiprow input[name="stage"])'


def _reach_classification(page, selector: str) -> None:
    """Bring one classification block into view, and prove it is on screen.

    **It opens nothing**, and the name it is called by is kept deliberately.
    Every caller means «make this vocabulary answerable», which for two rounds
    meant opening a disclosure and now means nothing at all — so the call sites
    keep saying what they mean and this is the one place that knows how much
    work that is today.

    It is not a no-op, though: it asserts that the chips really are visible
    without anything being opened, which is the promise the shape change makes.
    A page that put them back behind a control would fail here, in every file
    that files a Teema, rather than only in the one named after the shape.
    """
    block = page.locator(selector).first
    if not block.count():
        return
    block.scroll_into_view_if_needed()
    block.locator(".chip__input").first.wait_for(state="attached")


def open_valdkond(page) -> None:
    """Make `Valdkonnad` answerable on `Uus teema` — which it already is."""
    _reach_classification(page, VALDKONNAD_FIELD)


#: The checkout, for the `manage.py` a planting helper runs.
REPOSITORY_ROOT = Path(__file__).resolve().parents[1]

#: Sets the Matter `E2E_PAIR_MATTER` to hold the instruments whose labels are
#: `E2E_PAIR_LABELS` (`|`-separated). A literal with its values in the
#: environment, the shape `e2e/test_substantive_history.py` uses for `Etapp`.
_PAIR_SCRIPT = (
    "import os;"
    "from app.matters.models import Matter;"
    "from app.taxonomy.models import LegalInstrumentType as T;"
    "labels = os.environ['E2E_PAIR_LABELS'].split('|');"
    "rows = [T.objects.get(label_et=label) for label in labels];"
    "Matter.objects.get(pk=os.environ['E2E_PAIR_MATTER']).legal_instruments.set(rows);"
    "print(len(rows))"
)


def plant_historical_instruments(page, labels) -> None:
    """Give the Matter open in ``page`` several `Õigusakt`, the way history has them.

    Since the one-instrument rule (docs/adr/0070, amendment of 2026-10-09)
    nothing a person can reach writes two, but a Matter filed before it — or
    imported from a register cell naming `S, M` — holds them, and the pages that
    read such a file still have to read it. So a browser test about how the page
    *reads* a pair plants it in the server's own database, then reloads.
    """
    matter_id = re.search(r"/teemad/([0-9a-f-]{36})/", page.url).group(1)
    result = subprocess.run(  # noqa: S603
        [sys.executable, "manage.py", "shell", "-c", _PAIR_SCRIPT],
        cwd=REPOSITORY_ROOT,
        env={
            **os.environ,
            "DJANGO_SETTINGS_MODULE": "config.settings",
            "E2E_PAIR_MATTER": matter_id,
            "E2E_PAIR_LABELS": "|".join(labels),
        },
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert result.returncode == 0, "\n".join(["stdout:", result.stdout, "stderr:", result.stderr])
    page.reload()
    page.wait_for_load_state("networkidle")


def open_hetkeseis(page) -> None:
    """Make `Hetkeseis` answerable on `Uus teema` — which it already is."""
    _reach_classification(page, HETKESEIS_FIELD)


def give_first_step(page, *, days: int = 7) -> None:
    """Fill `Arvamuse tähtaeg` on an open `Uus teema` — the obligation, not a step.

    Every Teema this suite leaves behind without an open step is a permanent row
    in the department's «järgmise tegevuseta» list, which other files read — so a
    file that creates Matters owes each of them one.

    Until docs/adr/0133 §8 the one date the form asks for also established the
    canonical `Koostan arvamuse` step, so this was the whole of it. It now
    records the obligation only, and the new Teema opens with no current step:
    the caller follows the creation with :func:`start_first_step`, which is
    what a lawyer does next.

    Relative to today rather than a fixed future date: a constant eventually
    becomes a date in the past, and then every Teema this suite files is overdue
    on the surfaces that count lateness — which is a whole shard going red for
    the calendar rather than for the code.
    """
    when = date.today() + timedelta(days=days)
    page.fill("#id_response_deadline", f"{when.day}.{when.month}.{when.year}")


#: The first step `start_first_step` writes. The words the standard `Tööplaan`
#: used to suggest, kept so the files that read them still read a real step.
FIRST_STEP_TEXT = "Tutvu materjaliga"


def start_first_step(page, *, days: int = 7, text: str = FIRST_STEP_TEXT) -> None:
    """Set a new Teema's first step, dated ``days`` from today.

    The browser twin of what a lawyer does on a fresh Teema: `+ Määra järgmine
    tegevus`, the sentence and a day. Until docs/adr/0141 it was `Alusta` on the
    first `Tööplaan` suggestion; the plan is gone and nothing is suggested. It
    leaves the Teema with an open step, dated, so the department's «järgmise
    tegevuseta» list other files read is no longer than it was.
    """
    zone = page.locator("#praegune-tegevus")
    open_next_action_form(page)
    page.locator("#lisa-jargmine [name='text']").fill(text)
    when = date.today() + timedelta(days=days)
    page.locator("#id_target_date").fill(f"{when.day}.{when.month}.{when.year}")
    page.locator("#lisa-jargmine button[type=submit]").first.click()
    wait_for_htmx(page)
    zone.locator("#tehtud").wait_for(state="attached")


def record_marge(
    page,
    title: str,
    *,
    occurred_on: str = "",
    stage: str = "",
    as_next_step: bool = False,
    files: tuple[tuple[str, bytes], ...] = (),
    confirm_follow_up_closure: bool = False,
) -> int:
    """Record a `Märge` the way `+ Lisa · Tavaline` did, now that it has no panel.

    `Tavaline` left `+ Lisa` on 2026-10-07 (docs/adr/0143), and its endpoint,
    `add_note`, still takes the save — every `Märge` already on a file still
    reads, and a test that needs one on the page writes it here. The POST runs
    inside the page, with its session and CSRF token, so it is the same request
    the panel sent; the page is reloaded afterwards so it shows the result.

    ``stage`` is a `Hetkeseis` label as the header's own select lists it. A
    stage that ends the Matter closes it, as the panel's did. Returns the
    response status.

    ``confirm_follow_up_closure`` posts the answer to «this file still has a
    Koja arvamuse järelkontroll — close anyway?» (docs/adr/0146 §8); a file with
    no pending check closes the same with it or without it.
    """
    stage_id = ""
    if stage:
        stage_id = page.evaluate(
            """(label) => {
                const select = document.querySelector('#teema-hetkeseis select[name=stage]');
                const option = select && Array.from(select.options)
                    .find((o) => o.textContent.trim().split(' — ')[0] === label);
                return option ? option.value : '';
            }""",
            stage,
        )
        assert stage_id, f"no Hetkeseis {stage!r} in the header"
    status = page.evaluate(
        """async ({url, title, occurredOn, stage, asNext, files, confirm}) => {
            const data = new FormData();
            data.append('csrfmiddlewaretoken',
                document.querySelector('input[name=csrfmiddlewaretoken]').value);
            data.append('title', title);
            data.append('occurred_on', occurredOn);
            if (stage) data.append('stage', stage);
            if (asNext) data.append('as_next_step', 'on');
            if (confirm) data.append('confirm_follow_up_closure', 'on');
            for (const [name, bytes] of files) {
                data.append('attachments', new File([new Uint8Array(bytes)], name));
            }
            const response = await fetch(url, {
                method: 'POST', body: data, credentials: 'same-origin',
                headers: {'HX-Request': 'true'},
            });
            return response.status;
        }""",
        {
            "url": page.url.split("#")[0].split("?")[0].rstrip("/") + "/lisa/marge/",
            "title": title,
            "occurredOn": occurred_on,
            "stage": stage_id,
            "asNext": as_next_step,
            "files": [[name, list(body)] for name, body in files],
            "confirm": confirm_follow_up_closure,
        },
    )
    page.reload()
    page.wait_for_load_state("networkidle")
    return status


def open_done_form(page) -> None:
    """`✓ Tehtud` — open the completion form beside the current step.

    Behind an explicit control since docs/adr/0133 §4, and since docs/adr/0140
    §1 a toggle — the label of `#tehtud-valik`, which stays on the row. Looked
    at before clicking, so a form a refusal reopened is not shut by the act of
    asking for it: a second press closes it.
    """
    if not page.locator("#tehtud-valik").is_checked():
        page.locator('label[for="tehtud-valik"]').click()
    page.locator("#id_praegune_body").wait_for(state="visible")


def finish_current_action(page, text: str) -> None:
    """Record what was done about the current task, which completes it.

    One operation and one button: there is no `Märgi tehtuks` on this page
    (docs/adr/0075 §3). The form is behind `✓ Tehtud` (docs/adr/0133 §4).
    """
    zone = page.locator("#praegune-tegevus")
    open_done_form(page)
    zone.locator(".composer__body").fill(text)
    zone.locator("#praegune-tegevus-vorm button[type=submit]").click()
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


def create_matter(
    page,
    base_url: str,
    title: str,
    *,
    stage: str | None = None,
    owner: Persona | None = None,
    sender: str | None = None,
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

    `sender` is answered through the picker's own search rather than by ticking
    a chip, because `Uus teema`'s Saatja control is `quiet`: the catalogue is
    behind «Otsi või lisa asutus…» and an unchosen institution is `hidden` until
    the search reveals it, so `check()` would assert something a person never
    does (docs/adr/0088, `organisation_picker.html`). It exists because
    `+ Koja arvamus` opens its `Adressaadid` on the Teema's `Saatja`, and a test
    about that default needs a Teema that has one (docs/adr/0095 §1).
    """
    page.goto(f"{base_url}/teemad/uus/")
    page.wait_for_load_state("networkidle")
    page.fill("#id_title", title)
    if stage is not None:
        # Behind a menu since docs/adr/0094 §2, and it shuts itself again once
        # the radio is picked.
        open_hetkeseis(page)
        page.get_by_role("radio", name=stage, exact=True).check()
    if owner is not None:
        page.get_by_role("radio", name=owner.short_name, exact=True).check()
    if sender is not None:
        box = page.locator("#saatja-otsi")
        box.click()
        box.fill("")
        box.type(sender[:8], delay=20)
        page.locator("#saatja-tulemused").get_by_role("option", name=sender, exact=True).click()
    page.get_by_role("button", name="Salvesta", exact=True).click()
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


#: A `Teema käik` row: the `<article>` itself, which `display: contents` gives
#: no box of its own.
KAIK_ROW = "#ajalugu-loend article.uxtl__item"

#: A `Hetkeseis` period of `Teema käik` (docs/adr/0131 §7): a native `<details>`.
KAIK_PERIOD = "#ajalugu-loend details.kaikstage"


def open_kaik_period(node) -> None:
    """Open the `Hetkeseis` period a row sits in, the way a reader does.

    Since docs/adr/0131 `Teema käik` is grouped by period: the current one is
    open and every earlier one is closed. A row in an earlier period is in the
    page but out of sight until its period is opened — so anything that reads
    or presses inside it opens the period first. A row on the flat chronology
    (a Matter with no period) has no period and is left as it is. Idempotent.
    """
    period = node.locator("xpath=ancestor-or-self::details[contains(@class,'kaikstage')]")
    if not period.count():
        return
    period = period.first
    if period.get_attribute("open") is None:
        period.locator("xpath=./summary").click()


#: The header's one-button answer to a closure refused for a pending check.
FOLLOW_UP_CLOSURE_BUTTON = "Sulge teema ja lõpeta ka järelkontroll"


def close_through_stage(page, stage: str = "Rohkem ei tegele", title: str = "") -> None:
    """End the Matter the one ordinary way there is: a `Hetkeseis` that ends it.

    `+ Lõpeta teema` is gone (docs/adr/0131 §11). «Jõustunud» and «Rohkem ei
    tegele» close the Matter on save. With no ``title`` this is the header's
    own `Hetkeseis` editor; with one, a `Märge` carrying that sentence and the
    stage, through `record_marge` — `+ Lisa · Tavaline`'s save, since that
    panel left on 2026-10-07.

    **A file with a pending `Arvamuse järelkontroll` asks first** (docs/adr/0146
    §8): the header answers with the owner's warning and one button that closes
    past it, which this presses, as a person who meant to close would; the
    `Märge` posts the same confirmation.
    """
    if title:
        record_marge(page, title, stage=stage, confirm_follow_up_closure=True)
    else:
        control = page.locator("#teema-hetkeseis")
        control.locator("summary").click()
        control.locator("select[name=stage]").select_option(label=stage)
        with page.expect_response(
            lambda r: r.request.method == "POST" and "/vali/stage/" in r.url
        ) as answer:
            control.get_by_role("button", name="Salvesta hetkeseisu muudatus").click()
        if answer.value.status == 400:
            confirm = page.get_by_role("button", name=FOLLOW_UP_CLOSURE_BUTTON, exact=True)
            with page.expect_navigation():
                confirm.click()
        page.wait_for_load_state("networkidle")
    page.locator(".banner--closed").wait_for(state="visible")


def open_kaik_row(row) -> None:
    """Open one `Teema käik` row the way a reader does — with its toggle.

    The chronology is an accordion since docs/adr/0074 §14 was amended on
    2026-09-27: every row renders closed, as its one line, and its body, files,
    `Juristi märkus` and `Muuda` / `+ Lisa fail` / `Kustuta` are behind the
    toggle laid over that line. So a test that reads or presses anything inside
    a row opens the row first, exactly as a person has to.

    ``row`` is the row's `article` locator, or anything inside one — a
    `.filter(has_text=…)` on `KAIK_ROW` is the usual shape. Idempotent: an open
    row is left open, and one row opening may close another, which is the
    accordion working.

    A row with nothing behind its line — `Teema loodud`, or a round on a
    closed Teema that offers no control — draws no toggle, because its line is
    all it has. Such a row is already showing everything, so it is left as it
    is rather than pressed.
    """
    article = row.locator("xpath=ancestor-or-self::article[contains(@class,'uxtl__item')]").first
    open_kaik_period(article)
    toggle = article.locator(".uxtl__toggle").first
    toggle.wait_for(state="attached")
    if toggle.is_hidden():
        return
    if toggle.get_attribute("aria-expanded") != "true":
        toggle.click()
    assert toggle.get_attribute("aria-expanded") == "true"


# ---------------------------------------------------------------------------
# Small helpers many browser files share
# ---------------------------------------------------------------------------


def document_overflows(page) -> bool:
    """Whether the document itself scrolls sideways.

    Wide content scrolls inside its own container; the page never does. The
    one-pixel allowance absorbs sub-pixel rounding.
    """
    return page.evaluate(
        "() => document.documentElement.scrollWidth > document.documentElement.clientWidth + 1"
    )


def chronology(page):
    """`Teema käik`, the Matter's chronology."""
    return page.locator("#ajalugu-loend")


def choose_organisation(page, picker: str, name: str = "Näidisministeerium") -> None:
    """Answer an organisation control the way a person does: type, then pick.

    The same two steps `e2e/test_unified_organisation_picker.py` uses, and
    deliberately not a `check()` on the radio: the chips are labels whose input is
    clipped, and an institution outside the visible shortlist is `hidden` until
    the search reveals it — so ticking the control directly asserts something the
    person never does and fails on exactly the bodies the search exists for.
    The default is the seeded ministry.
    """
    box = page.locator(f"#{picker}-otsi")
    box.click()
    box.fill("")
    box.type(name[:8], delay=20)
    page.locator(f"#{picker}-tulemused").get_by_role("option", name=name, exact=True).click()
