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

from playwright.sync_api import expect

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
    the words the neighbouring files search for. Those words are listed in
    `e2e/titles.py`, which refuses them outright for a title built through
    `unique_title` — this helper fills `#id_title` itself, so here the rule is
    prose and the prose is the whole of it.
    """
    page.goto(f"{base_url}/teemad/uus/")
    page.wait_for_load_state("networkidle")
    page.locator("#id_title").fill(title)
    page.get_by_role("radio", name=SANDRA.short_name, exact=True).check()
    page.get_by_role("button", name="Loo teema").click()
    page.wait_for_url(re.compile(r"/teemad/[0-9a-f-]{36}/$"))
    return page.url


def test_maara_opens_the_next_step_form_and_puts_the_caret_in_it(page, base_url):
    sign_in(page, base_url, SANDRA)

    matter_url = _matter_on_sandras_desk(page, base_url, "UX-003 koostajasse saabumine")
    own_path = re.sub(r"^https?://[^/]+", "", matter_url)

    page.goto(f"{base_url}/minu-asjad/")
    page.wait_for_load_state("networkidle")

    # **This Matter's row if the block shows it, otherwise the block's first.**
    # The block lists the `RAIL_LIMIT` quietest Matters, oldest first
    # (`my_work.quiet_matters`), and the browser world is one database shared by
    # the whole shard. On 2026-09-26 a reshuffled partition ran
    # `e2e/test_correction_round_surfaces.py` ahead of this file: Sandra had
    # twenty-two quiet Matters, and the one made above — the newest, so the least
    # quiet — was rightly behind «Näita kõiki 22 →». Where `Määra` lands does not
    # depend on which quiet Matter it names; the Matter made above guarantees the
    # block is not empty, and it is still accounted for — listed, or counted
    # behind the block's own «Näita kõiki».
    quiet = page.locator('section.railblock[aria-label="Järgmise tegevuseta"]')
    own = quiet.locator(f'a.quietrow__cta[href="{own_path}#lisa-marge"]')
    if not own.count():
        expect(quiet.locator("a.railblock__more")).to_be_visible()

    # `#lisa-marge`, not `#lisa-jargmine`. These rows are Matters with **no**
    # open step, and `PRAEGUNE TEGEVUS` draws its `Muuda` disclosure only
    # beside a task — so once `+ Järgmine tegevus` left the launcher the old
    # target did not exist on exactly the rows this block lists, and a browser
    # answers a missing fragment by scrolling nowhere. The one ordinary way to
    # set a first step is the optional `Järgmine tegevus` inside `+ Märge`
    # (docs/adr/0097 §8.2).
    cta = own if own.count() else quiet.locator("a.quietrow__cta")
    assert cta.count(), "Minu asjad does not offer Määra for a Matter with no next step"
    href = cta.first.get_attribute("href") or ""
    assert href.endswith("#lisa-marge"), href
    matter_path = href.removesuffix("#lisa-marge")
    cta.first.click()
    page.wait_for_url(re.compile(re.escape(matter_path)))

    # The field is only visible once the panel is open, so waiting for it is
    # what makes this free of a race with `load` — the assertion below then
    # reports the state rather than the timing.
    page.locator("#lisa-marge [name='next_text']").wait_for(state="visible")

    panel = page.locator("#lisa-marge")
    # Open because somebody asked for it by following a control that says so —
    # not because the page opens it for everybody. `LISA TEEMALE` is a choice
    # until one is made (docs/adr/0075 §2).
    #
    # Asked as "is it showing" rather than "does it carry `open`": the panels
    # stopped being `<details>` on 2026-09-14, and what the arrival handler
    # does is choose the radio that reveals this one.
    expect(panel).to_be_visible()
    assert page.locator("#lisa-marge-valik").is_checked()

    # And the caret is in `Mis juhtus?` rather than in the date box above it,
    # which arrives already filled. Same rule as the `L` shortcut: the
    # attribute names the box a person is meant to type in.
    assert page.evaluate(
        "() => { const c = document.getElementById('lisa-marge');"
        " return !!c && c.contains(document.activeElement)"
        " && document.activeElement.hasAttribute('data-composer-focus'); }"
    ), "arrival left the caret outside the box it promised"


def test_an_ordinary_matter_visit_opens_no_panel(page, base_url):
    """The invariant the fix must not cost, in the direction ADR 0075 sets it.

    Same page, no fragment. `LISA TEEMALE` is a *choice* of four families and
    nothing is a form until one is chosen, so an ordinary visit must open none
    of them — and an arrival by fragment must open exactly the one the link
    named. What this test guards is that the two agree: the fix must not make
    either of them special (docs/adr/0075 §2).
    """
    sign_in(page, base_url, SANDRA)
    matter_url = _matter_on_sandras_desk(page, base_url, "UX-003 pärisvaate kontroll")

    page.goto(matter_url)
    page.wait_for_load_state("networkidle")

    panel = page.locator("#lisa-marge")
    panel.wait_for(state="attached")

    expect(panel).not_to_be_visible()
    # And it opens from its own chip, which is what makes it a choice.
    page.locator('label[for="lisa-marge-valik"]').click()
    expect(panel).to_be_visible()
