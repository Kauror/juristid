"""The opinion archive in a browser.

The database suite already proves the boundary from every angle it can reach.
What only a browser proves is the part a database test cannot: that a reader
who may can *get to* a held letter through the interface and read it in the
tab, and that one who may not, following the same URL, is refused rather than
shown a smaller list.

That distinction is the whole reason the archive has its own boundary. The
corpus is real outgoing correspondence with no Matter to inherit a restriction
from, so the refusal has to be the same in the list, the detail page, the header
figures and the file itself — and four surfaces agreeing is exactly the sort of
claim that is easy to believe and hard to verify without opening them.
"""

from __future__ import annotations

import re
from urllib.parse import parse_qs, urlparse

import pytest
from playwright.sync_api import expect

from app.core.management.commands.seed_e2e_data import (
    ARCHIVE_LETTERS,
    OPEN_TITLE,
    RESTRICTED_TITLE,
    archive_letter_sha,
)
from e2e.conftest import ADMIN, HEAD, MARTIN, READER, go_to, navigation_targets, sign_in, sign_out

pytestmark = pytest.mark.e2e

LINKED_TITLE = ARCHIVE_LETTERS[0][0]
UNLINKED_TITLE = ARCHIVE_LETTERS[1][0]
LINKED_SHA = archive_letter_sha(0)

ARCHIVE_PATH = "/haldus/arvamuste-arhiiv/"


def open_archive(page, base_url):
    page.goto(f"{base_url}{ARCHIVE_PATH}")
    expect(page.get_by_role("heading", name="Arvamuste arhiiv")).to_be_visible()


# -- the boundary ------------------------------------------------------------


def test_a_reader_following_the_url_is_refused(page, base_url):
    """Refused, not shown an empty archive.

    An empty list would tell a reader the corpus is empty, which is a different
    and untrue statement about what Koda holds.

    READER rather than a specialist since docs/adr/0056 — the same substitution
    ADR 0042 already forced on the rest of these tests, arriving here now that
    the archive uses that set too. A specialist can no longer play this part
    because these are the department's own letters.
    """
    sign_in(page, base_url, READER)
    response = page.goto(f"{base_url}{ARCHIVE_PATH}")
    assert response is not None
    assert response.status == 403


def test_a_specialist_reaches_the_archive(page, base_url):
    """The other side of the same boundary, which only a browser proves.

    A specialist following the URL gets the corpus, not a 403 and not a smaller
    list — the half of docs/adr/0056 that the department actually asked for.
    """
    sign_in(page, base_url, MARTIN)
    response = page.goto(f"{base_url}{ARCHIVE_PATH}")
    assert response is not None
    assert response.status == 200
    expect(page.get_by_role("heading", name="Arvamuste arhiiv")).to_be_visible()


def test_an_administrator_reaches_the_archive_from_the_queue(page, base_url):
    """Through the interface, not by knowing the URL.

    The archive was unreachable from anywhere before this stage, which is most
    of why nobody could read the letters.
    """
    sign_in(page, base_url, ADMIN)
    page.goto(f"{base_url}/haldus/arvamuste-ulevaatus/")
    page.get_by_role("link", name="Ava arhiiv").click()
    expect(page.get_by_role("heading", name="Arvamuste arhiiv")).to_be_visible()


# -- finding a letter --------------------------------------------------------


def test_the_coverage_strip_precedes_the_search_box(page, base_url, screenshots):
    """Two thirds of this corpus has no Matter, and the page says so first."""
    sign_in(page, base_url, ADMIN)
    open_archive(page, base_url)
    screenshots(page, "arvamuste-arhiiv")

    context = page.locator(".pagehead__context").first
    expect(context).to_contain_text("kirja")
    expect(context).to_contain_text("teemaga seotud")


def test_a_letter_is_found_by_its_title(page, base_url):
    sign_in(page, base_url, ADMIN)
    open_archive(page, base_url)

    page.get_by_label("Otsi arhiivist").fill("näidisseaduse")
    page.get_by_role("button", name="Otsi arhiivist").click()

    expect(page.get_by_role("link", name=LINKED_TITLE)).to_be_visible()
    expect(page.get_by_role("link", name=UNLINKED_TITLE)).to_have_count(0)


def test_a_letter_is_found_by_its_contents(page, base_url):
    """The reason the second pass and the text extraction exist at all."""
    sign_in(page, base_url, ADMIN)
    open_archive(page, base_url)

    page.get_by_label("Otsi arhiivist").fill("eelnõu")
    page.get_by_role("button", name="Otsi arhiivist").click()
    expect(page.get_by_role("link", name=LINKED_TITLE)).to_be_visible()


def test_a_pasted_hash_finds_the_letter_it_names(page, base_url):
    sign_in(page, base_url, ADMIN)
    open_archive(page, base_url)

    page.get_by_label("Otsi arhiivist").fill(LINKED_SHA)
    page.get_by_role("button", name="Otsi arhiivist").click()
    expect(page.get_by_role("link", name=LINKED_TITLE)).to_be_visible()


def test_an_overlong_query_says_it_was_refused(page, base_url):
    sign_in(page, base_url, ADMIN)
    page.goto(f"{base_url}{ARCHIVE_PATH}?q={'a' * 501}")
    expect(page.locator(".notice")).to_contain_text("tähemärki")
    expect(page.locator(".empty")).to_have_count(0)


# -- reading one --------------------------------------------------------------


def open_letter(page, base_url, title):
    open_archive(page, base_url)
    page.get_by_role("link", name=title).first.click()
    expect(page.get_by_role("heading", name=title)).to_be_visible()


def test_the_detail_page_says_what_the_letter_is_tied_to(page, base_url, screenshots):
    sign_in(page, base_url, ADMIN)
    open_letter(page, base_url, LINKED_TITLE)
    screenshots(page, "arvamuste-arhiiv-kiri")

    expect(page.get_by_role("heading", name="Seotud teemad")).to_be_visible()
    expect(page.get_by_role("heading", name="Esinemised arhiivis")).to_be_visible()


def test_the_link_form_says_it_is_not_an_opinion(page, base_url):
    """A reviewer who thinks they recorded a sent opinion has been misled."""
    sign_in(page, base_url, ADMIN)
    open_letter(page, base_url, UNLINKED_TITLE)

    expect(page.locator(".form__hint")).to_contain_text("ei loo arvamust")


def test_the_letter_opens_in_the_browser(page, base_url):
    sign_in(page, base_url, ADMIN)
    open_letter(page, base_url, LINKED_TITLE)

    link = page.get_by_role("link", name="Ava kiri")
    href = link.get_attribute("href") or ""
    assert "/fail/" in href, href

    response = page.request.get(f"{base_url}{href}")
    assert response.status == 200
    assert response.headers["content-type"].startswith("application/pdf")
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["content-disposition"].startswith("inline")
    assert "script-src 'none'" in response.headers["content-security-policy"]


def test_the_download_offers_the_hash_rather_than_the_archive_name(page, base_url):
    """The ZIP's names carry recipients and subjects; a header is the wrong
    place for a reader to learn who a letter was to."""
    sign_in(page, base_url, ADMIN)
    open_letter(page, base_url, LINKED_TITLE)

    href = page.get_by_role("link", name="Laadi alla").get_attribute("href") or ""
    response = page.request.get(f"{base_url}{href}")
    disposition = response.headers["content-disposition"]
    assert disposition.startswith("attachment")
    assert LINKED_SHA[:16] in disposition
    assert "Näidisministeerium" not in disposition


# -- the register boundary, in a browser --------------------------------------


def test_the_archive_names_the_matter_it_may_and_not_the_one_it_may_not(page, base_url):
    """The property the whole P3.3 boundary rests on, checked on the screen.

    The seeded letter is filed onto two Matters: one ordinary and one
    RESTRICTED. The administrator may open every letter Koda holds — reading
    the corpus is a decision about the corpus — and may not read a restricted
    register entry. So the page has to name the first, refuse the second, and
    still not pretend the letter is unfiled (docs/adr/0028).

    A database test proves the queryset; only a browser proves what is rendered.
    """
    sign_in(page, base_url, ADMIN)
    open_letter(page, base_url, LINKED_TITLE)

    section = page.locator("section").filter(has=page.get_by_role("heading", name="Seotud teemad"))
    body = section.first.inner_text()

    # A prefix, because the link truncates a realistically long title.
    assert OPEN_TITLE[:60] in body
    assert RESTRICTED_TITLE not in body
    # Present but unnamed, rather than absent. "This letter concerns nothing"
    # would be a false statement about the archive rather than a discreet one.
    assert "ei kuvata" in body
    assert "Ükski teema ei ole selle kirjaga seotud" not in body


def test_the_administrator_is_offered_no_way_into_the_restricted_matter(page, base_url):
    """No link to follow, either. A URL is an identity as much as a title is.

    The letter is filed onto two Matters and the section offers exactly one
    destination: withholding a title while leaving a working link beside it
    would be a boundary that only looks like one.
    """
    sign_in(page, base_url, ADMIN)
    open_letter(page, base_url, LINKED_TITLE)

    section = page.locator("section").filter(has=page.get_by_role("heading", name="Seotud teemad"))
    expect(section.first.locator("a[href*='/teemad/']")).to_have_count(1)


# -- what did not widen -------------------------------------------------------


def test_reading_the_archive_does_not_depend_on_which_door_was_answered(page, base_url):
    """This deployment authenticates individuals, and the head reads here too.

    The old rule made the head's access a shared-gate concession and narrowed
    to the administrator alone where identity is real — which would have taken
    the archive from the whole department the day Cloudflare Access replaced
    the shared password. `ARCHIVE_READERS` is now one set asked in every mode,
    and this is that claim made where the deployment authenticates individuals
    (docs/adr/0056).
    """
    sign_in(page, base_url, HEAD)
    response = page.goto(f"{base_url}{ARCHIVE_PATH}")
    assert response is not None
    assert response.status == 200


def test_neither_arvamused_nor_the_archive_is_a_destination_on_the_bar(page, base_url):
    """Two cleanups, one assertion, and both still true.

    ADR 0044 took «Arvamuste arhiiv» off the bar: it and «Arvamused» were two
    items a reader had to tell apart before they could pick one, and the
    distinction — real and load-bearing — belongs inside the workspace as a
    captioned tab rather than in the navigation.

    ADR 0047 then took «Arvamused» off too. An arvamus is an outcome of a teema
    rather than a parallel place to be, so the whole workspace is a section of
    the Teemad page and the bar carries Teemad.

    Removed, not hidden, and for both personas: the refusal above is still the
    boundary, both routes are unchanged, and the tests below follow the
    interface to them.
    """
    for persona in (MARTIN, ADMIN):
        sign_in(page, base_url, persona)
        # By href, not by visible name: below 1560px the reading destinations
        # live inside a closed <details>, whose contents are out of the
        # accessibility tree. Asserting by role there would be asserting a
        # layout decision this test does not care about (e2e/conftest.py).
        targets = navigation_targets(page)
        assert ARCHIVE_PATH not in targets, targets
        assert "/arvamused/" not in targets, targets
        assert "/teemad/" in targets, targets
        sign_out(page, base_url)


def test_the_teemad_section_is_how_a_reader_reaches_the_archive(page, base_url):
    """Teemad → Arvamused → Arhiiv, without knowing a URL.

    The path both removed bar items used to be. It has to work for the persona
    who may read the corpus, or the consolidation took a destination away
    instead of moving it.

    The tab is a plain link that carries the whole address and lands on
    `#arvamused`, so this also covers the part a database test cannot: that
    somebody who scrolled to the section is not thrown back to the top of a
    register they have already read past.
    """
    sign_in(page, base_url, ADMIN)
    go_to(page, "Teemad")
    section = page.locator("#arvamused")
    expect(section.get_by_role("heading", name="Arvamused")).to_be_visible()

    section.get_by_role("link", name=re.compile(r"^Arhiiv")).click()
    page.wait_for_load_state("networkidle")
    expect(section.get_by_role("link", name=re.compile(r"^Arhiiv"))).to_have_attribute(
        "aria-current", "true"
    )
    expect(section.get_by_text(UNLINKED_TITLE)).to_be_visible()


def test_the_section_hands_over_to_the_full_workspace(page, base_url):
    """«Vaata kõiki arvamusi» — the bounded section's way out.

    The section shows twelve rows and no pager; the destination it links to is
    the workspace that was not deleted, with its own filters and its own paging.
    If this link stops working the consolidation has quietly removed a surface.
    """
    sign_in(page, base_url, ADMIN)
    go_to(page, "Teemad")
    page.locator("#arvamused").get_by_role(
        "link", name=re.compile(r"^Vaata kõiki arvamusi")
    ).click()
    page.wait_for_load_state("networkidle")

    assert "/arvamused/" in page.url
    expect(page.get_by_role("heading", name="Arvamused", level=1)).to_be_visible()


def test_the_two_searches_on_the_teemad_page_stay_apart(page, base_url):
    """The hard requirement, in the one place a database test cannot check it.

    Both boxes are on one page and both are wired to HTMX. What only a browser
    proves is that typing in the opinion box swaps the opinion results and
    leaves the register's rows — and its address — alone; a fragment route
    misrouted or a target id mistyped would swap the wrong region, and the
    server-side tests would still pass.
    """
    sign_in(page, base_url, ADMIN)
    go_to(page, "Teemad")
    register_rows = page.locator("#teemad-tulemused tbody tr").count()
    address = page.url

    page.locator("#arvamused-otsing").fill("mitteesinevsona")
    page.wait_for_timeout(600)
    page.wait_for_load_state("networkidle")

    # The opinion side answered...
    expect(page.locator("#arvamused-tulemused")).to_contain_text("0 vastet")
    # ...and the register kept its rows.
    assert page.locator("#teemad-tulemused tbody tr").count() == register_rows
    # The address gained the opinion query and stayed on the page it belongs to.
    # It is emphatically not unchanged: an address that lagged the screen is
    # the failure `test_the_live_opinion_search_keeps_the_address_honest` below
    # exists to catch.
    assert page.url.startswith(address.split("?")[0])
    assert "arvamus_q=mitteesinevsona" in page.url
    assert "/arvamused/plokk/" not in page.url


def test_the_opinion_tab_does_not_undo_a_live_teemad_search(page, base_url):
    """The stale-href failure, and the only place it is reachable.

    The register's live search swaps `#teemad-tulemused` and pushes a new
    address. The opinion tab strip is outside that region, so the `?q=` the
    server baked into its hrefs is the one the page was *rendered* with —
    following it would navigate to the old address and silently undo the search
    the reader had just typed. `static/js/app.js` rebuilds the href from the
    live address on click; with JavaScript off there is no live search for it to
    go stale from, so nothing needs rebuilding there.

    Only a browser can show this: server-side the page is always freshly
    rendered and the href is always right, which is exactly why it went
    unnoticed until the section was driven by hand.
    """
    sign_in(page, base_url, ADMIN)
    go_to(page, "Teemad")

    page.locator("#teemad-otsing").fill("pakendiseaduse")
    page.wait_for_timeout(600)
    page.wait_for_load_state("networkidle")
    narrowed = page.locator("#teemad-tulemused tbody tr").count()
    expect(page.locator(".registercount")).to_contain_text("pakendiseaduse")

    page.locator("#arvamused").get_by_role("link", name=re.compile(r"^Arhiiv")).click()
    page.wait_for_load_state("networkidle")

    # The archive opened...
    expect(
        page.locator("#arvamused").get_by_role("link", name=re.compile(r"^Arhiiv"))
    ).to_have_attribute("aria-current", "true")
    # ...and the register is still holding the search that was typed into it.
    assert "q=pakendiseaduse" in page.url
    expect(page.locator(".registercount")).to_contain_text("pakendiseaduse")
    assert page.locator("#teemad-tulemused tbody tr").count() == narrowed


def test_the_administrative_archive_url_still_opens(page, base_url):
    """The direct route is untouched; only the link to it left the bar."""
    sign_in(page, base_url, ADMIN)
    open_archive(page, base_url)


# -- what the workspace promises ----------------------------------------------


def test_the_workspace_says_a_link_is_not_a_sent_opinion(page, base_url):
    """Load-bearing copy. Filing a letter is not filing an opinion."""
    sign_in(page, base_url, ADMIN)
    open_archive(page, base_url)
    expect(page.get_by_text("Arenduse arhiivitööruum")).to_be_visible()


def test_the_unlinked_shortcut_narrows_to_the_review_workload(page, base_url):
    """523 of 767 in production, and the reason anybody opens this page."""
    sign_in(page, base_url, ADMIN)
    open_archive(page, base_url)

    # Scoped to the filter strip: the seeded unlinked letter is *called*
    # "Sidumata näidiskiri", so an unscoped name match finds the row as well as
    # the tab.
    filters = page.get_by_role("navigation", name="Seotuse järgi")
    filters.get_by_role("link", name="Sidumata", exact=False).click()
    expect(page.get_by_role("link", name=UNLINKED_TITLE)).to_be_visible()
    expect(page.get_by_role("link", name=LINKED_TITLE)).to_have_count(0)


# -- the address a live opinion search leaves behind ---------------------------


def opinion_search(page, text: str):
    """Type into «Otsi arvamustest» and wait for the section to answer.

    Through the box rather than the URL, because what is being checked is what
    the browser is left holding after a live search — which is precisely what a
    server-side test cannot see.
    """
    box = page.locator("#arvamused-otsing")
    box.fill(text)
    # Past the 250ms debounce, then past the request it fires.
    page.wait_for_timeout(600)
    page.wait_for_load_state("networkidle")


def opinion_row_count(page) -> int:
    """How many rows the Arvamused section is showing, whichever tab is open.

    Two shapes since the v2 rebuild: `Saadetud` draws two-line `submission`
    cards, `Arhiiv` keeps its table because its four columns are the same four
    facts on every row (02-EKRAANID §C).
    """
    results = page.locator("#arvamused-tulemused")
    return results.locator(".submission").count() + results.locator("tbody tr").count()


def test_the_live_opinion_search_keeps_the_address_honest(page, base_url):
    """One invariant, in the one place it can actually be broken.

    What is on screen, what the address bar says, and what a colleague gets if
    the URL is copied must be the same thing. The live opinion search answers
    from `/arvamused/plokk/` and swaps a fragment — so without the
    `HX-Push-Url` the server returns, the screen would move and the address
    would silently stay behind, and every link shared from that page would be
    missing the opinion search the sender was looking at.

    Six things, in the order somebody would hit them (docs/adr/0047).
    """
    sign_in(page, base_url, ADMIN)
    go_to(page, "Teemad")

    # 1. A teemad search is active, live, and in the address.
    page.locator("#teemad-otsing").fill("pakendiseaduse")
    page.wait_for_timeout(600)
    page.wait_for_load_state("networkidle")
    assert "q=pakendiseaduse" in page.url
    teemad_rows = page.locator("#teemad-tulemused tbody tr").count()
    assert teemad_rows, "the register matched nothing, so this proves nothing"

    # 2. A live Arvamused search on top of it.
    opinion_search(page, "ministeeriumile")
    opinion_rows = opinion_row_count(page)
    assert opinion_rows, "the opinion search matched nothing, so this proves nothing"

    # 3. Both parameters are in the visible /teemad/ URL, and the fragment
    #    route is not.
    address = urlparse(page.url)
    parameters = parse_qs(address.query)
    assert address.path == "/teemad/", page.url
    assert parameters.get("q") == ["pakendiseaduse"], page.url
    assert parameters.get("arvamus_q") == ["ministeeriumile"], page.url
    assert "/arvamused/plokk/" not in page.url

    # 4. Copying that URL reproduces both result states.
    copied = page.url
    page.goto(copied)
    page.wait_for_load_state("networkidle")
    assert page.locator("#teemad-tulemused tbody tr").count() == teemad_rows
    assert opinion_row_count(page) == opinion_rows
    expect(page.locator(".registercount")).to_contain_text("pakendiseaduse")
    expect(page.locator("#arvamused-otsing")).to_have_value("ministeeriumile")
    expect(page.locator("#teemad-otsing")).to_have_value("pakendiseaduse")

    # 5. Clearing the opinion box removes `arvamus_q` and leaves `q` alone.
    opinion_search(page, "")
    cleared = parse_qs(urlparse(page.url).query)
    assert "arvamus_q" not in cleared, page.url
    assert cleared.get("q") == ["pakendiseaduse"], page.url
    assert page.locator("#teemad-tulemused tbody tr").count() == teemad_rows

    # 6. And the address never became the fragment's own at any point.
    assert "/arvamused/plokk/" not in page.url


def test_back_returns_to_the_previous_opinion_search(page, base_url):
    """Pushed, not replaced — so Back steps through it like any other search.

    htmx does the pushing, from the header the server returns, which is why its
    own history handling stays in step. A hand-rolled `pushState` beside htmx's
    bookkeeping is the version of this that restores the wrong DOM.
    """
    sign_in(page, base_url, ADMIN)
    go_to(page, "Teemad")

    opinion_search(page, "ministeeriumile")
    assert "arvamus_q=ministeeriumile" in page.url
    found = opinion_row_count(page)

    page.go_back()
    page.wait_for_load_state("networkidle")

    assert "arvamus_q" not in page.url, page.url
    assert page.url.endswith("/teemad/") or "arvamus_q" not in page.url
    # The section came back unfiltered rather than showing a stale answer.
    assert opinion_row_count(page) >= found


def test_the_register_search_is_not_disturbed_by_the_opinion_push(page, base_url):
    """The push must carry the register's *current* state, not the rendered one.

    The opinion form's hidden inputs are what the server composes the pushed
    address from, and they are outside the region the register's live search
    swaps — so stale ones would push the old `?q=` over a correct address bar,
    which is worse than not pushing at all. `static/js/app.js` resyncs them on
    every htmx push; this is what proves it.
    """
    sign_in(page, base_url, ADMIN)
    go_to(page, "Teemad")

    page.locator("#teemad-otsing").fill("pakendiseaduse")
    page.wait_for_timeout(600)
    page.wait_for_load_state("networkidle")
    narrowed = page.locator("#teemad-tulemused tbody tr").count()

    opinion_search(page, "ministeeriumile")

    parameters = parse_qs(urlparse(page.url).query)
    assert parameters.get("q") == ["pakendiseaduse"], page.url
    assert parameters.get("arvamus_q") == ["ministeeriumile"], page.url
    # And the register's own rows never moved.
    assert page.locator("#teemad-tulemused tbody tr").count() == narrowed
