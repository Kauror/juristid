"""«Uus asi» in a browser: it appears, it opens, and then it is gone.

The domain rules are proved against the database in
`tests/test_new_assignment_notices.py`. Three things only a real session can
answer, and all three are the point of the feature:

**It survives the hand-over.** Martin assigns; *Sandra* has to see it, in her
own session, on her own page — not in a queryset.

**It is where it was designed to be.** Above Märkmed, measured against the
rendered geometry rather than against the order of two tags in a template.

**It disappears.** Opening the Matter *from* the block is the acknowledgement,
and the whole section — heading included — goes with the last unread row.

Every scenario builds its own state through the ordinary «Uus teema» form, so
nothing here is seeded and the visual world is untouched: the notice exists
because a person in a browser assigned a Matter, which is exactly the trigger
under test (docs/adr/0051).
"""

from __future__ import annotations

import re

import pytest
from playwright.sync_api import expect

from e2e.conftest import MARTIN, SANDRA, sign_in, sign_out

pytestmark = pytest.mark.e2e

MY_WORK = "/minu-asjad/"

#: The rail sections, by the labels they carry for a screen reader. Read that
#: way rather than by CSS class because the accessible name is the contract the
#: page owes a keyboard user, and a class is not.
UUS_ASI = "section[aria-label='Uus asi']"
MARKMED = "section[aria-label='Märkmed']"


def _assign_new_matter(page, base_url: str, title: str, owner_short_name: str) -> str:
    """File a Teema through «Uus teema» and name who is to deal with it."""
    page.goto(f"{base_url}/teemad/uus/")
    page.wait_for_load_state("networkidle")
    page.fill("#id_title", title)
    owners = page.locator("fieldset").filter(has_text="Vastutaja").first
    owners.locator("label").filter(has_text=owner_short_name).first.click()
    page.get_by_role("button", name="Loo teema").click()
    page.wait_for_url(re.compile(r"/teemad/[0-9a-f-]{36}/$"))
    return page.url


def _block(page):
    return page.locator(UUS_ASI)


def _clear_the_block(page, base_url: str) -> None:
    """Open whatever is left in the block, until there is no block.

    The browser suite shares one database, so another scenario's hand-over may
    legitimately be sitting in the same person's queue. Emptying it is what lets
    this file assert *the section is gone* — the actual product rule — rather
    than the weaker "my row is gone".
    """
    for _ in range(20):
        page.goto(f"{base_url}{MY_WORK}")
        page.wait_for_load_state("networkidle")
        if not _block(page).count():
            return
        _block(page).locator("button").first.click()
        page.wait_for_load_state("networkidle")
    raise AssertionError("the Uus asi block never emptied")


def test_an_assigned_matter_appears_opens_and_disappears(page, base_url):
    """The whole loop, in two sessions.

    Martin files a Teema and puts Sandra's name on it. Sandra signs in, finds it
    above her notes, opens it from there, and comes back to a rail that no
    longer has the section on it at all.
    """
    title = "Brauserikatse: uus asi Sandrale"

    sign_in(page, base_url, MARTIN)
    matter_url = _assign_new_matter(page, base_url, title, SANDRA.short_name)
    sign_out(page, base_url)

    sign_in(page, base_url, SANDRA)

    # -- it is there, and it is above Märkmed ----------------------------
    expect(_block(page)).to_have_count(1)
    expect(_block(page).get_by_role("heading", name="Uus asi")).to_be_visible()
    expect(_block(page).get_by_role("button", name=title)).to_be_visible()

    notices = _block(page).bounding_box()
    notes = page.locator(MARKMED).bounding_box()
    assert notices is not None and notes is not None
    assert notices["y"] + notices["height"] <= notes["y"] + 1, (
        "Uus asi must sit above Märkmed on the rail"
    )

    # -- opening it from the block is what acknowledges it ---------------
    _block(page).get_by_role("button", name=title).click()
    page.wait_for_url(re.compile(r"/teemad/[0-9a-f-]{36}/$"))
    assert page.url == matter_url
    expect(page.locator(".matterhead__title")).to_contain_text(title)

    page.goto(f"{base_url}{MY_WORK}")
    page.wait_for_load_state("networkidle")
    expect(page.locator(f"{UUS_ASI} >> text={title}")).to_have_count(0)

    # -- and the section itself goes with the last unread row ------------
    _clear_the_block(page, base_url)
    page.goto(f"{base_url}{MY_WORK}")
    page.wait_for_load_state("networkidle")
    expect(_block(page)).to_have_count(0)
    expect(page.get_by_role("heading", name="Uus asi")).to_have_count(0)
    # The rail is still a rail: nothing was reserved for the absent block.
    expect(page.locator(MARKMED)).to_have_count(1)


def test_assigning_a_matter_to_yourself_still_notifies_you(page, base_url):
    """The requirement that makes the acknowledgement endpoint necessary.

    Saving the form lands Martin *inside* the Matter he just filed. If ordinary
    Matter viewing counted as having seen the notice, this block would already
    be gone by the time he reached Minu asjad — and it must not be.
    """
    title = "Brauserikatse: uus asi iseendale"

    sign_in(page, base_url, MARTIN)
    _assign_new_matter(page, base_url, title, MARTIN.short_name)

    # He is on the Matter page right now, having just rendered it.
    page.goto(f"{base_url}{MY_WORK}")
    page.wait_for_load_state("networkidle")

    expect(_block(page)).to_have_count(1)
    expect(_block(page).get_by_role("button", name=title)).to_be_visible()

    _clear_the_block(page, base_url)
