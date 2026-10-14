"""QA-COORD-01 (independent QA, 2026-10-14): Back, then Salvesta, files a Teema twice.

A double press and a replayed POST are deduplicated by the form's `intake`
token (one Matter). Browser Back defeats it: `/teemad/uus/` is fetched again
with a *fresh* token while the browser restores the typed title, so the second
press is a new form as far as the server can tell. Reproduced in Chromium on
81a7d6e4: two Matters with the same title, different ids.

Strict xfail until fixed: whichever correction the owner chooses (keep the
original token across history navigation, or warn about an identical title the
same person just filed), this test must then see one Teema — or no second one
without an explicit confirmation — and the marker comes off with the fix.
"""

from __future__ import annotations

import re
from urllib.parse import quote

import pytest

from e2e.conftest import SANDRA, sign_in
from e2e.titles import unique_title

pytestmark = pytest.mark.e2e


@pytest.mark.xfail(
    strict=True,
    reason="QA-COORD-01: Back then Salvesta on Uus teema files a second identical Teema",
)
def test_back_after_filing_a_teema_does_not_file_it_twice(page, base_url):
    sign_in(page, base_url, SANDRA)
    title = unique_title("Tagasi-nupu kordus")

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
    page.get_by_role("button", name="Salvesta", exact=True).click()
    page.wait_for_load_state("networkidle")

    page.goto(f"{base_url}/teemad/?olek=koik&q={quote(title)}")
    page.wait_for_load_state("networkidle")
    links = set(
        page.get_by_role("link", name=title, exact=True).evaluate_all(
            "els => els.map(e => e.getAttribute('href'))"
        )
    )
    assert len(links) == 1, f"one Teema expected, the register lists {sorted(links)} ({first})"
