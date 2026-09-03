"""Following `Määra` from Minu asjad actually lands on the box that sets a step.

The one behaviour in this wave that no rendered-HTML assertion can reach. The
Python tests prove the link names an id the destination renders; whether the
disclosure then *opens* and where the caret ends up is the browser's answer, and
it is the whole point of the fix.

The defect this replaces: the four next-step controls pointed at `#jargmiseks`,
an id nothing renders. The browser found no target, arrival left the reader at
`scrollY = 0` on a long Matter page, and the composer — a `<details>` closed by
default under the heading `Mis juhtus?` — stayed shut. So the product's most
repeated request, `Järgmise tegevuseta`, had an affordance that went nowhere and
named nothing (UX-003).
"""

from __future__ import annotations

import re

from e2e.conftest import SANDRA, sign_in


def _matter_on_sandras_desk(page, base_url: str, title: str) -> str:
    """A Matter with an owner and no next step, created through the real form.

    Not `conftest.create_matter`: `owner` is `required=False` on Uus teema and
    that helper fills only the title, so the Matter it makes belongs to nobody.
    `Järgmise tegevuseta` is `matters_without_action(user, owner=subject)`, so an
    ownerless Matter reaches no one's desk — which is the honest place for work
    nobody has been given, and useless for this test. The owner chip is picked
    the way the rest of the browser suite picks it (e2e/test_lawyer_workflow.py).

    Nothing fills `#id_next-text`, deliberately: a Matter with a next step is not
    what this block lists.

    **The titles are namespaced on purpose.** The browser world is one database
    shared by every file in the shard, so a Matter created here is in the
    register when the next file runs — and `e2e/test_register_search.py` types
    `Tavaline` and asserts exactly one row. A Matter called «Tavaline teema»
    made that two. Anything created here keeps the `UX-003` prefix and avoids
    the words the neighbouring files search for.
    """
    page.goto(f"{base_url}/teemad/uus/")
    page.wait_for_load_state("networkidle")
    page.locator("#id_title").fill(title)
    page.get_by_role("radio", name=SANDRA.short_name, exact=True).check()
    page.get_by_role("button", name="Loo teema").click()
    page.wait_for_url(re.compile(r"/teemad/[0-9a-f-]{36}/$"))
    return page.url


def test_maara_opens_the_composer_and_puts_the_caret_in_it(page, base_url):
    sign_in(page, base_url, SANDRA)

    matter_url = _matter_on_sandras_desk(page, base_url, "UX-003 koostajasse saabumine")
    matter_path = re.sub(r"^https?://[^/]+", "", matter_url)

    page.goto(f"{base_url}/minu-asjad/")
    page.wait_for_load_state("networkidle")

    cta = page.locator(f'a.quietrow__cta[href="{matter_path}#teema-koostaja"]')
    assert cta.count(), "Minu asjad does not offer Määra for a Matter with no next step"
    cta.first.click()
    page.wait_for_url(re.compile(re.escape(matter_path)))

    # The body is only visible once the disclosure is open, so waiting for it is
    # what makes this free of a race with `DOMContentLoaded` — the assertion
    # below then reports the state rather than the timing.
    page.locator(".composer__body").wait_for(state="visible")

    composer = page.locator("details.uxcomp")
    # Open because somebody asked for it by following a control that says so —
    # not because the page opens it for everybody (ADR 0052 §13, and the pass
    # that closed it again).
    assert composer.evaluate("node => node.open") is True

    # And the caret is in the box, so the next thing typed is the next step.
    assert page.evaluate(
        "() => { const c = document.getElementById('teema-koostaja');"
        " return !!c && c.contains(document.activeElement); }"
    ), "arrival left focus outside the composer"


def test_an_ordinary_matter_visit_leaves_the_composer_shut(page, base_url):
    """The invariant the fix must not cost: closed by default.

    Same page, no fragment. A Matter is read far more often than it is written
    to, and the box that writes to it folds until somebody asks.
    """
    sign_in(page, base_url, SANDRA)
    matter_url = _matter_on_sandras_desk(page, base_url, "UX-003 pärisvaate kontroll")

    page.goto(matter_url)
    page.wait_for_load_state("networkidle")

    composer = page.locator("details.uxcomp")
    composer.wait_for(state="attached")

    assert composer.evaluate("node => node.open") is False
