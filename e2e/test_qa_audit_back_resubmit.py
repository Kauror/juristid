"""QA-COORD-01 (independent QA, 2026-10-14): Back, then Salvesta, files a Teema twice.

A double press and a replayed POST are deduplicated by the form's `intake`
token (one Matter). Browser Back defeats it: `/teemad/uus/` is fetched again
with a *fresh* token while the browser restores the typed title, so the second
press is a new form as far as the server can tell. Reproduced in Chromium on
81a7d6e4: two Matters with the same title, different ids.

Fixed in the QA remediation round (docs/adr/0166 §2): the restored form posts
the tab's earlier tokens (`intake_earlier`), and a Teema the same person
just filed under the same title is reported instead of filed again — «Ava see»
or «Loo siiski uus». The second test proves a deliberate second Teema with the
same words is still one click away.
"""

from __future__ import annotations

import re
from urllib.parse import quote

import pytest
from playwright.sync_api import expect

from e2e.conftest import SANDRA, sign_in
from e2e.titles import unique_title

pytestmark = pytest.mark.e2e


def _register_links(page, base_url, title):
    page.goto(f"{base_url}/teemad/?olek=koik&q={quote(title)}")
    page.wait_for_load_state("networkidle")
    return set(
        page.get_by_role("link", name=title, exact=True).evaluate_all(
            "els => els.map(e => e.getAttribute('href'))"
        )
    )


def _file_then_go_back(page, base_url, title):
    page.goto(f"{base_url}/teemad/uus/")
    page.wait_for_load_state("networkidle")
    page.fill("#id_title", title)
    page.get_by_role("button", name="Salvesta", exact=True).click()
    page.wait_for_url(re.compile(r"/teemad/[0-9a-f-]{36}/$"))
    first = page.url

    page.go_back()
    page.wait_for_load_state("networkidle")
    # The precondition of the defect: the browser hands the typed title back.
    assert page.input_value("#id_title") == title
    return first


def test_back_after_filing_a_teema_does_not_file_it_twice(page, base_url):
    sign_in(page, base_url, SANDRA)
    title = unique_title("Tagasi-nupu kordus")
    first = _file_then_go_back(page, base_url, title)

    with page.expect_request(
        lambda request: request.method == "POST" and request.url.endswith("/teemad/uus/")
    ) as sent:
        page.get_by_role("button", name="Salvesta", exact=True).click()
    page.wait_for_load_state("networkidle")
    # The restored form named the tab's earlier tokens (`bindCreateFormHistory`,
    # added at `formdata` so Chromium's restoration is left alone).
    assert 'name="intake_earlier"' in (sent.value.post_data or "")
    # Not filed again: the form says why and offers the Teema that exists.
    expect(page.locator("#sama-pealkiri")).to_be_visible()
    assert page.input_value("#id_title") == title

    links = _register_links(page, base_url, title)
    assert len(links) == 1, f"one Teema expected, the register lists {sorted(links)} ({first})"


def test_a_deliberate_second_teema_with_the_same_title_is_one_click_away(page, base_url):
    sign_in(page, base_url, SANDRA)
    title = unique_title("Teadlik kordus")
    first = _file_then_go_back(page, base_url, title)

    page.get_by_role("button", name="Salvesta", exact=True).click()
    page.wait_for_load_state("networkidle")
    notice = page.locator("#sama-pealkiri")
    expect(notice).to_be_visible()
    expect(notice).to_contain_text("Sama pealkirjaga teema loodi just")
    expect(notice.get_by_role("link", name="Ava see")).to_have_attribute(
        "href", re.compile(re.escape(first.split("/teemad/")[1]))
    )

    page.get_by_role("button", name="Loo siiski uus", exact=True).click()
    page.wait_for_url(re.compile(r"/teemad/[0-9a-f-]{36}/$"))
    assert page.url != first

    assert len(_register_links(page, base_url, title)) == 2
