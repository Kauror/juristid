"""What the browser keeps of a signed-in page, and what Back brings back.

htmx 2.0.4 copied every page it pushed into history — the whole `<body>` — into
localStorage under `htmx-history-cache`. A lawyer's register search therefore
left its rows, restricted titles included, at rest in the browser profile,
where they outlived signing out and were still there for whoever signed in next
on the same machine. Back then restored that copy without asking the server,
with the page's controls dead (ENG-009).

The rule these hold is the one the server already keeps with `no-store`: a
signed-in page is not stored by anything. No snapshot is written, one left by
an older version is removed, signing out clears the origin's storage, and
Back/Forward asks the server again — as whoever is signed in now.

Nothing here files, edits or deletes a record: every test only reads the seeded
world, so it can share a shard with anything.
"""

from __future__ import annotations

import json
import re
from urllib.parse import quote_plus

import pytest
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import expect

from app.core.management.commands.seed_e2e_data import RESTRICTED_TITLE
from e2e.conftest import READER, SANDRA, pass_the_gate, sign_in, sign_out, wait_for_htmx

pytestmark = pytest.mark.e2e

#: The key htmx 2.0.x keeps its history snapshots under.
HISTORY_KEY = "htmx-history-cache"

FIRST = "Konfidentsiaalne"
SECOND = "liikmete"


STORAGE = """() => {
    const out = {};
    for (const area of [window.localStorage, window.sessionStorage]) {
        for (let i = 0; i < area.length; i++) {
            const key = area.key(i);
            out[key] = area.getItem(key);
        }
    }
    return out;
}"""


def kept(page) -> dict[str, str]:
    """Everything this origin holds in localStorage and sessionStorage.

    Asked again if a navigation replaced the page mid-question: Back onto an
    entry of the same document is answered by htmx with a reload, which can
    start just after Playwright considers the Back finished.
    """
    for _ in range(5):
        try:
            return page.evaluate(STORAGE)
        except PlaywrightError as error:
            if "Execution context was destroyed" not in str(error):
                raise
            page.wait_for_load_state("load")
    raise AssertionError("the page never stopped navigating")


def holds(page, text: str) -> bool:
    return any(text in value for value in kept(page).values())


def search_register(page, text: str) -> None:
    """Type into the register's live search and wait for the address it pushes."""
    page.fill("#teemad-otsing", text)
    page.wait_for_url(re.compile(rf"/teemad/\?(.*&)?q={re.escape(quote_plus(text))}(&|$)"))
    wait_for_htmx(page)


def two_register_searches(page, base_url: str) -> None:
    """The register, as somebody who may read restricted work, searched twice.

    Twice because htmx saves the page it is leaving when it pushes the next
    address: the second search is what used to write the first to storage.
    """
    sign_in(page, base_url, SANDRA)
    page.goto(f"{base_url}/teemad/")
    search_register(page, FIRST)
    expect(page.get_by_text(RESTRICTED_TITLE).first).to_be_visible()
    search_register(page, SECOND)


def test_a_register_search_leaves_nothing_in_the_browser(page, base_url):
    two_register_searches(page, base_url)

    assert HISTORY_KEY not in kept(page)
    assert not holds(page, RESTRICTED_TITLE)


def test_back_asks_the_server_again_and_the_page_still_works(page, base_url):
    """Back is a fresh, authorised page — and every control on it is live.

    The restored snapshot came back with an empty search box, a header search
    that asked nothing and a copy button that did nothing, because the copy
    carried the markers of the bindings of the page it was taken from.
    """
    page.context.grant_permissions(["clipboard-read", "clipboard-write"])
    two_register_searches(page, base_url)

    with page.expect_request(
        lambda request: (
            request.resource_type == "document" and f"q={quote_plus(FIRST)}" in request.url
        )
    ):
        page.go_back()
    page.wait_for_load_state("networkidle")

    expect(page).to_have_url(re.compile(rf"q={re.escape(quote_plus(FIRST))}"))
    expect(page.locator("#teemad-otsing")).to_have_value(FIRST)
    expect(page.get_by_text(RESTRICTED_TITLE).first).to_be_visible()
    assert HISTORY_KEY not in kept(page)

    # The header search is bound: it opens its suggestions while typing.
    page.locator("#global-search").click()
    page.locator("#global-search").press_sequentially("sünteetiline", delay=30)
    expect(page.locator("#global-search-results")).to_be_visible()
    page.keyboard.press("Escape")

    # And the register's own copy-link control answers.
    page.get_by_text("+ Salvesta praegune filter vaatena").click()
    copy = page.locator("[data-copy-from='teemad-vaate-link']")
    copy.click()
    expect(copy).to_have_text("Kopeeritud")

    # Forward is the same promise in the other direction.
    page.go_forward()
    page.wait_for_load_state("networkidle")
    expect(page.locator("#teemad-otsing")).to_have_value(SECOND)
    assert HISTORY_KEY not in kept(page)


def test_a_later_session_on_the_same_profile_cannot_bring_it_back(page, base_url):
    """Sign out, sign in as somebody who may not read it, and walk Back."""
    two_register_searches(page, base_url)

    sign_out(page, base_url)
    assert HISTORY_KEY not in kept(page)
    assert not holds(page, RESTRICTED_TITLE)

    sign_in(page, base_url, READER)
    page.goto(f"{base_url}/teemad/")
    expect(page.get_by_text(RESTRICTED_TITLE)).to_have_count(0)

    for _ in range(8):
        page.go_back()
        page.wait_for_load_state("load")
        assert not holds(page, RESTRICTED_TITLE)
        if f"q={quote_plus(FIRST)}" in page.url:
            break
    else:
        raise AssertionError("Back never reached the earlier session's searches")

    # The earlier session's search, answered by the server for the reader now
    # signed in: the register renders, and the restricted row is not in it.
    expect(page.locator("#teemad-otsing")).to_have_value(FIRST)
    page.wait_for_load_state("networkidle")
    expect(page.get_by_text(RESTRICTED_TITLE)).to_have_count(0)
    assert not holds(page, RESTRICTED_TITLE)


def test_what_an_older_version_left_behind_is_removed(page, base_url):
    """A zero cache size stops new copies; the ones already on disk go too."""
    sign_in(page, base_url, SANDRA)
    left_behind = json.dumps([{"url": "/teemad/", "content": RESTRICTED_TITLE, "title": "Teemad"}])
    page.evaluate(
        "([key, value]) => window.localStorage.setItem(key, value)", [HISTORY_KEY, left_behind]
    )
    assert HISTORY_KEY in kept(page)

    page.goto(f"{base_url}/minu-asjad/")

    assert HISTORY_KEY not in kept(page)


def test_signing_out_clears_what_was_left_behind(page, base_url):
    """Signing out clears the origin's storage even before the next page runs."""
    sign_in(page, base_url, SANDRA)
    page.evaluate("() => window.localStorage.setItem('juristid-e2e-probe', 'kept')")
    assert "juristid-e2e-probe" in kept(page)

    with page.expect_response(
        lambda response: response.request.method == "POST" and "/konto/valju" in response.url
    ) as signed_out:
        sign_out(page, base_url)

    # `all_headers`, because Playwright leaves security headers out of `headers`.
    assert signed_out.value.all_headers().get("clear-site-data") == '"storage"'
    assert "juristid-e2e-probe" not in kept(page)


def test_behind_the_shared_gate_signing_out_leaves_nothing(page, gate_base_url):
    """The mode the real-data instance runs in: a password, then a persona.

    Signing out closes the gate as well, so the next page is the gate's own —
    and nothing of the register the persona searched is left for it to find.
    """
    pass_the_gate(page, gate_base_url)
    page.goto(f"{gate_base_url}/osakond/")
    page.locator("#persona-pill").click()
    page.locator("#persona-menu").get_by_role("button", name="Sandra", exact=False).click()
    page.wait_for_load_state("networkidle")
    page.goto(f"{gate_base_url}/teemad/")
    search_register(page, FIRST)
    expect(page.get_by_text(RESTRICTED_TITLE).first).to_be_visible()
    search_register(page, SECOND)
    assert HISTORY_KEY not in kept(page)

    sign_out(page, gate_base_url)

    expect(page.get_by_label("Parool", exact=False)).to_be_visible()
    assert HISTORY_KEY not in kept(page)
    assert not holds(page, RESTRICTED_TITLE)
