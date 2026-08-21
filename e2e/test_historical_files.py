"""Historical materials in a browser: the filename does the useful thing.

The allow-list itself is exhaustively tested against the database in
`tests/test_inline_files.py` — every format the brief names, in both directions,
plus the case where the extension and the declared MIME type disagree. What
only a browser proves is the part that made the owner complain: that clicking a
filename reaches the material rather than a page *about* the material, and that
"reach" means the right thing for each kind of file (Stage-2E.1 brief 11, 13).

The seeded page carries two materials on purpose. A signed container, which can
only ever be saved, and a text file, which can safely be shown. One page holding
only one of them could not demonstrate a difference.
"""

from __future__ import annotations

import re
import uuid

import pytest
from playwright.sync_api import expect

from app.core.management.commands.seed_e2e_data import (
    HISTORICAL_FILENAME as DOWNLOAD_FILENAME,
)
from app.core.management.commands.seed_e2e_data import (
    HISTORICAL_INLINE_FILENAME as INLINE_FILENAME,
)
from app.core.management.commands.seed_e2e_data import (
    OPEN_TITLE,
)
from e2e.conftest import MARTIN, sign_in

pytestmark = pytest.mark.e2e


def open_case_file(page, base_url):
    """Reach the historical page through the interface, as a reader would."""
    page.goto(f"{base_url}/teemad/?olek=koik")
    page.get_by_role("link", name=re.compile(re.escape(OPEN_TITLE))).first.click()

    historical = page.get_by_role("link", name="Vaata ajaloolist materjali")
    expect(historical).to_be_visible()
    historical.click()
    expect(page.locator(".casefile__name").first).to_be_visible()


def material(page, filename):
    return page.locator(".casefile__name", has_text=filename).first


# -- what a filename does ----------------------------------------------------


def test_a_readable_file_opens_rather_than_leading_to_a_page_about_it(page, base_url, screenshots):
    sign_in(page, base_url, MARTIN)
    open_case_file(page, base_url)
    screenshots(page, "ajalooline-materjal")

    link = material(page, INLINE_FILENAME)
    href = link.get_attribute("href") or ""
    assert "/ava/" in href, href
    # A new tab, and never one that can reach back into this one.
    expect(link).to_have_attribute("target", "_blank")
    expect(link).to_have_attribute("rel", "noopener")


def test_a_signed_container_saves_instead(page, base_url):
    """Nothing opens an ASiC-E. Offering to try would be a worse answer."""
    sign_in(page, base_url, MARTIN)
    open_case_file(page, base_url)

    link = material(page, DOWNLOAD_FILENAME)
    href = link.get_attribute("href") or ""
    assert "/ava/" not in href, href
    assert "/t%C3%B5end/" in href or "/tõend/" in href, href
    expect(link).to_have_attribute("download", "")


def test_provenance_is_still_one_click_away(page, base_url):
    """Document detail remains reachable; it is simply no longer the only way
    to obtain the file (brief 13)."""
    sign_in(page, base_url, MARTIN)
    open_case_file(page, base_url)

    expect(page.get_by_role("link", name="Päritolu").first).to_be_visible()


# -- what the routes actually return -----------------------------------------


def test_opening_a_text_file_returns_it_inline_and_inert(page, base_url):
    """The headers are the security boundary, so they are asserted here."""
    sign_in(page, base_url, MARTIN)
    open_case_file(page, base_url)

    href = material(page, INLINE_FILENAME).get_attribute("href") or ""
    response = page.request.get(f"{base_url}{href}")

    assert response.status == 200
    headers = {name.lower(): value for name, value in response.headers.items()}
    assert headers["content-type"].startswith("text/plain")
    assert headers["content-disposition"].startswith("inline")
    assert headers["x-content-type-options"] == "nosniff"
    assert "script-src 'none'" in headers["content-security-policy"]
    assert "object-src 'none'" in headers["content-security-policy"]


def test_asking_to_open_a_container_hands_over_the_download(page, base_url):
    """The reader asked to see a file. "Here it is, saved instead" beats an
    error page."""
    sign_in(page, base_url, MARTIN)
    open_case_file(page, base_url)

    href = material(page, DOWNLOAD_FILENAME).get_attribute("href") or ""
    # The download route with `/ava/` appended is what the open route is.
    response = page.request.get(f"{base_url}{href.rstrip('/')}/ava/")

    assert response.status == 200
    disposition = {k.lower(): v for k, v in response.headers.items()}["content-disposition"]
    assert disposition.startswith("attachment")


def test_the_file_actually_downloads_when_clicked(page, base_url):
    sign_in(page, base_url, MARTIN)
    open_case_file(page, base_url)

    with page.expect_download() as download:
        material(page, DOWNLOAD_FILENAME).click()
    assert download.value.suggested_filename == DOWNLOAD_FILENAME


# -- what the page must not contain ------------------------------------------


def test_no_storage_path_ever_reaches_the_markup(page, base_url):
    """The application route is the authorization boundary. A signed storage
    URL in the page would route around it (brief 12)."""
    sign_in(page, base_url, MARTIN)
    open_case_file(page, base_url)

    markup = page.content()
    for marker in ("blob.core.windows.net", "X-Amz-Signature", "sig=", "/evidence/"):
        assert marker not in markup, marker


def test_an_unknown_file_is_a_404_rather_than_a_denial(page, base_url):
    """404, not 403: distinguishing "forbidden" from "missing" tells an
    unauthorized caller that the file exists."""
    sign_in(page, base_url, MARTIN)
    response = page.request.get(f"{base_url}/dokumendid/tõend/{uuid.uuid4()}/ava/")
    assert response.status == 404
