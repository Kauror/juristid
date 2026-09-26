"""The browser policy in a real browser (ENG-124).

Three things the server tests cannot see:

* the two legacy queues' filters still submit themselves, now that the
  `onchange="this.form.submit()"` the policy would refuse is gone and
  `app.js`'s `data-autosubmit` does the work;
* the policy is *enforced*, not merely sent: a script the page did not ship
  does not run, and the browser says so in the console;
* that console line is one `the_browser_policy_refuses_nothing` in
  `e2e/conftest.py` recognises — which is what makes every other browser test
  in this suite a check that the policy cost its workflow nothing.
"""

from __future__ import annotations

import re

import pytest
from playwright.sync_api import expect

from e2e.conftest import ADMIN, BROWSER_POLICY_REFUSALS, MARTIN, sign_in

pytestmark = pytest.mark.e2e

QUEUES = {
    "opinion": "/haldus/arvamuste-ulevaatus/",
    "review": "/haldus/ajaloo-ulevaatus/",
}


def test_a_page_arrives_with_both_policies(page, base_url):
    sign_in(page, base_url, MARTIN)
    response = page.goto(f"{base_url}/minu-asjad/")
    assert response is not None and response.status == 200
    headers = response.all_headers()

    policy = headers["content-security-policy"]
    assert "script-src 'self';" in policy
    assert "'unsafe-eval'" not in policy
    assert "frame-ancestors 'none'" in policy
    assert "camera=()" in headers["permissions-policy"]
    assert headers["x-frame-options"] == "DENY"


@pytest.mark.parametrize("field", ["klass", "olek"])
@pytest.mark.parametrize("queue", sorted(QUEUES))
def test_a_queue_filter_submits_itself_without_inline_code(page, base_url, queue, field):
    """Choosing a Klass or an Olek reloads the queue with it, as it always did."""
    sign_in(page, base_url, ADMIN)
    page.goto(f"{base_url}{QUEUES[queue]}")
    select = page.locator(f"select[name='{field}']")
    expect(select).to_have_attribute("data-autosubmit", "")
    assert select.get_attribute("onchange") is None

    current = select.input_value()
    values = select.locator("option").evaluate_all("options => options.map(o => o.value)")
    chosen = next(value for value in values if value != current)

    select.select_option(chosen)

    page.wait_for_url(re.compile(rf"[?&]{field}={re.escape(chosen)}(&|$)"))
    expect(page.locator(f"select[name='{field}']")).to_have_value(chosen)


def test_the_policy_is_enforced_and_the_refusal_is_recognised(
    browser, browser_context_args, base_url
):
    """A script the page did not ship does not run, and the console says why.

    Its own context, deliberately: provoking a refusal is the point here, and
    the suite-wide check watches only the `page` fixture's context.
    """
    context = browser.new_context(**browser_context_args)
    try:
        page = context.new_page()
        console: list[str] = []
        page.on("console", lambda message: console.append(message.text))
        page.goto(f"{base_url}/konto/arendus-sisselogimine/")

        page.evaluate(
            """() => {
                window.__violations = 0;
                document.addEventListener("securitypolicyviolation", () => {
                    window.__violations += 1;
                });
                const script = document.createElement("script");
                script.textContent = "window.__inlineRan = true";
                document.head.appendChild(script);
                const button = document.createElement("button");
                button.setAttribute("onclick", "window.__handlerRan = true");
                document.body.appendChild(button);
                button.click();
            }"""
        )
        page.wait_for_function("() => window.__violations >= 2")

        assert page.evaluate("() => window.__inlineRan === undefined")
        assert page.evaluate("() => window.__handlerRan === undefined")
        refusals = [
            line
            for line in console
            if any(marker in line.lower() for marker in BROWSER_POLICY_REFUSALS)
        ]
        assert len(refusals) >= 2, (
            "the browser refused the injected code but the console lines were not "
            f"recognised, so the suite-wide check could never fail: {console}"
        )
    finally:
        context.close()
