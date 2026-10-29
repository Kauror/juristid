"""Scenario F: a remembered block one test changes does not reach the next test.

Remembered blocks (docs/adr/0194) are server-side, per person, and the browser
suite's personas are shared by every file in a shard. FULL run 40686621037
failed four shards because one test left «Dokumendi andmed» or «Vaata
tabelina» in a state the next one did not expect — and since the shards
rebalance whenever the timing table or the file set changes, the failure
moved between unrelated commits (audit 2026-10-28, E-03).

`sign_in` now resets the persona to the registered defaults
(`e2e/conftest.py::reset_disclosures`), so every test that signs in starts
from them. These two prove it with the same persona: the first changes a
remembered block and proves the change *persisted* on the server; the second
starts from the registered default. Each also holds on its own, in either
order and wherever the shard boundary falls — the second does not depend on
the first having run, and the first does not depend on a clean persona.

They write only presentation state for the shared persona, which `sign_in`
puts back for whichever test comes next. Production persistence is unchanged:
nothing here bypasses the product's own save and reset endpoints.
"""

from __future__ import annotations

import pytest
from playwright.sync_api import expect

from e2e.conftest import MARTIN, close_disclosure, sign_in

pytestmark = pytest.mark.e2e

#: Minu asjad's own statistics: open by default (app/accounts/disclosures.py).
STATISTICS = 'details[data-disclosure="my_work.statistics"]'


def _statistics(page):
    block = page.locator(STATISTICS)
    expect(block).to_have_count(1)
    return block


def test_f1_a_test_that_closes_a_remembered_block_changes_it_on_the_server(page, base_url):
    sign_in(page, base_url, MARTIN)
    block = _statistics(page)
    # The start is the default whatever ran before this test.
    expect(block).to_have_attribute("open", "")
    expect(block).to_have_attribute("data-disclosure-saved", "1")

    close_disclosure(block)
    # Saved means the endpoint answered 204 (static/js/disclosures.js).
    expect(block).to_have_attribute("data-disclosure-saved", "0")

    page.reload()
    page.wait_for_load_state("networkidle")
    # The server draws it closed now: this is the persistent state the next
    # test would have inherited before sign-in reset it.
    expect(_statistics(page)).not_to_have_attribute("open", "")


def test_f2_the_same_persona_starts_the_next_test_at_the_registered_default(page, base_url):
    sign_in(page, base_url, MARTIN)
    block = _statistics(page)
    expect(block).to_have_attribute("open", "")
    expect(block).to_have_attribute("data-disclosure-saved", "1")
