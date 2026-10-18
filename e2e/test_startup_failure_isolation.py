"""A feature whose start-up throws does not stop the page telling a refusal.

`static/js/app.js` is one function run once per page, and `static/js/ux.js`
another. Until 2026-10-18 an exception while one feature started stopped
everything registered after it: in app.js that included the notice a failed
save puts beside its form, and in ux.js a binder that threw after a swap
skipped the refusal focus that follows the binders (JARCH-004). Every feature
that does work while it starts now goes through `safeInit`, and the handlers
for a refused or failed request are registered before any feature starts
(docs/adr/0171).

The failure is injected from the test, not through a hook in the shipped code:
an init script makes `querySelectorAll` throw for exactly one selector that one
feature asks for while it starts. Each test first checks that the injected
failure really happened, so renaming the selector cannot turn the test into one
that passes without testing anything.
"""

from __future__ import annotations

import json

import pytest
from playwright.sync_api import expect

from e2e.conftest import MARTIN, create_matter, open_add_panel, sign_in, wait_for_htmx
from e2e.titles import unique_title

pytestmark = pytest.mark.e2e

#: The first load-time page scan in app.js (`muu-reveal`), which runs on every
#: page. Before the fix everything after it in the file was never registered.
APP_JS_START_UP = "#valdkond-muu input[type=checkbox]"
#: The first binder of ux.js's `bindAll` (`bindPublicationPreview`), which runs
#: on load and after every swap, just before the refusal focus.
UX_JS_BINDER = "[data-publication-form]"

TOOVOIT_FORM = "#marge-toovoit form"
TOOVOIT_SAVE = "#marge-toovoit button[type=submit]"
TOOVOIT_FIELD = "#id_victory_change"
TOOVOIT_ROUTE = "**/lisa/toovoit/"
FAILURE = "[data-request-failure]"


#: Installed before the page's own scripts run; `__SELECTOR__` is replaced.
_BREAK = """
(() => {
  const broken = __SELECTOR__;
  window.__juristidBrokenStarts = [];
  for (const proto of [Document.prototype, Element.prototype]) {
    const original = proto.querySelectorAll;
    proto.querySelectorAll = function (wanted) {
      if (wanted === broken) {
        window.__juristidBrokenStarts.push(wanted);
        throw new Error("injected start-up failure: " + wanted);
      }
      return original.apply(this, arguments);
    };
  }
})();
"""


def _break_start_up(page, selector: str) -> None:
    """From the next navigation on, `querySelectorAll(selector)` throws."""
    page.add_init_script(_BREAK.replace("__SELECTOR__", json.dumps(selector)))


def _teema_with_broken_start_up(page, base_url: str, selector: str) -> list[str]:
    """A fresh Teema, reloaded with one feature's start-up throwing.

    The Teema is created first, with every script intact, so what is measured
    is the Teema page alone. Returns the console's error lines, collected from
    the reload on.
    """
    sign_in(page, base_url, MARTIN)
    url = create_matter(page, base_url, unique_title("Käivitusviga"))
    errors: list[str] = []
    page.on(
        "console",
        lambda message: errors.append(message.text) if message.type == "error" else None,
    )
    _break_start_up(page, selector)
    page.goto(url)
    page.wait_for_load_state("load")
    assert page.evaluate("window.__juristidBrokenStarts.length") >= 1, (
        f"the injected failure never fired: nothing asked for {selector!r} any more"
    )
    return errors


def test_a_failing_app_js_feature_leaves_refusals_and_failures_told(page, base_url):
    errors = _teema_with_broken_start_up(page, base_url, APP_JS_START_UP)

    # A refused save shows its reason in place.
    open_add_panel(page, "marge-toovoit")
    page.locator(TOOVOIT_SAVE).click()
    wait_for_htmx(page)
    expect(page.locator("#marge-toovoit .field__error")).to_have_text("Kirjuta, mis muutus.")

    # A save the server fails is told beside its form, with the text kept.
    page.fill(TOOVOIT_FIELD, "Ministeerium saatis uue versiooni")
    page.route(
        TOOVOIT_ROUTE,
        lambda route: route.fulfill(status=500, body="<h1>Server Error (500)</h1>"),
    )
    page.locator(TOOVOIT_SAVE).click()
    notice = page.locator(f"{TOOVOIT_FORM} {FAILURE}")
    expect(notice).to_be_visible()
    expect(notice).to_contain_text("Salvestamine ebaõnnestus.")
    expect(page.locator(TOOVOIT_FIELD)).to_have_value("Ministeerium saatis uue versiooni")
    page.unroute(TOOVOIT_ROUTE)

    # And the failure that started it is logged under the feature's name.
    assert any("juristid: muu-reveal failed" in line for line in errors), errors


def test_a_failing_ux_js_binder_leaves_the_refusal_its_focus(page, base_url):
    errors = _teema_with_broken_start_up(page, base_url, UX_JS_BINDER)

    open_add_panel(page, "marge-muu")
    form = page.locator("#marge-muu form")
    form.locator("[name=description]").fill("Materjal, mille päev on puudu.")
    with page.expect_response(
        lambda r: r.url.endswith("/lisa/muu/") and r.request.method == "POST"
    ) as answer:
        form.get_by_role("button", name="Salvesta", exact=True).click()
    wait_for_htmx(page)
    assert answer.value.status == 400

    form = page.locator("#marge-muu form")
    expect(form).to_contain_text("Sisesta, millal see toimus.")
    expect(form.locator("[name=occurred_on]")).to_be_focused()
    assert any("juristid: bindPublicationPreview failed" in line for line in errors), errors
