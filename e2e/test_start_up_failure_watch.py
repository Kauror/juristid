"""The suite-wide start-up watch really fails a test (JRF-03).

`e2e/conftest.py::no_feature_fails_to_start` fails any browser test whose pages
log `juristid: <feature> failed` — what `safeInit` writes when a feature's
start-up throws (docs/adr/0171). A watch that silently stopped watching would
look exactly like a suite with no start-up errors, so this test makes one
start-up throw, **without** the opt-out marker, and checks the watch caught it.
It then empties the list, so the deliberate failure does not fail this test.
"""

from __future__ import annotations

import pytest

from e2e.conftest import MARTIN, START_UP_FAILURE_PREFIX, sign_in
from e2e.test_startup_failure_isolation import APP_JS_START_UP, _break_start_up

pytestmark = pytest.mark.e2e


def test_an_unmarked_start_up_failure_is_caught(page, base_url, no_feature_fails_to_start):
    sign_in(page, base_url, MARTIN)
    assert no_feature_fails_to_start == [], "the ordinary page already logged a start-up error"

    _break_start_up(page, APP_JS_START_UP)
    page.goto(base_url + "/")
    page.wait_for_load_state("load")
    assert page.evaluate("window.__juristidBrokenStarts.length") >= 1, (
        f"the injected failure never fired: nothing asked for {APP_JS_START_UP!r} any more"
    )
    page.wait_for_function("() => true")  # let the console event reach Python

    caught = list(no_feature_fails_to_start)
    assert any(f"{START_UP_FAILURE_PREFIX}muu-reveal failed" in line for line in caught), caught
    no_feature_fails_to_start.clear()


def test_a_page_the_test_opens_itself_is_watched_too(browser, base_url, no_feature_fails_to_start):
    context = browser.new_context()
    try:
        page = context.new_page()
        sign_in(page, base_url, MARTIN)
        _break_start_up(page, APP_JS_START_UP)
        page.goto(base_url + "/")
        page.wait_for_load_state("load")
        assert page.evaluate("window.__juristidBrokenStarts.length") >= 1
        page.wait_for_function("() => true")
        assert any("muu-reveal failed" in line for line in no_feature_fails_to_start), (
            no_feature_fails_to_start
        )
        no_feature_fails_to_start.clear()
    finally:
        context.close()
