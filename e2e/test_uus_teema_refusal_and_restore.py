"""A refused `Uus teema` and the Back that follows it, on one page.

Two fixes landed on `Uus teema` from separate branches and they meet on the
same save.

One made the optional `Menetluse link` block empty-permitted, so a save refused
for a reason somewhere else no longer prints «Menetluse link vajab
veebiaadressi.» under an address box nobody typed in. The other made the
`Sarnased teemad` region refresh itself when the browser puts a filled form
back on the screen, so pressing Back after filing a Teema no longer shows
«nothing resembles this» about a subject filed a moment ago.

Each has its own coverage — `tests/test_procedural_links_on_uus_teema.py` and
the restore tests in `e2e/test_matter_form_ux.py` — and each passed on its own
branch. Neither walks the path that contains both, which is the ordinary one: a
save is refused, the person fixes the real problem, files the Teema, and presses
Back. This module walks it once and asserts that neither fix undid the other.
"""

from __future__ import annotations

from uuid import uuid4

from playwright.sync_api import expect

from e2e.conftest import MARTIN, sign_in

CREATE_PATH = "/teemad/uus/"
SIMILAR_REGION = "#sarnased-teemad"
SIMILAR_SECTION = ".draftsimilar"

#: The refusal the optional block must never print for somebody who did not use
#: it. Written out rather than matched loosely, because a test that looked for
#: «Menetluse» alone would pass on the block's own summary.
LINK_REFUSAL = "Menetluse link vajab veebiaadressi."


def _coined_subject() -> str:
    """A subject the register cannot already hold, two distinctive terms long.

    The same construction `e2e/test_matter_form_ux.py` uses and for the same
    reason: letters only, so the text splitter cannot leave the title carrying
    a single subject term and drop it under the engine's threshold.
    """
    token = uuid4().hex[:10].translate(str.maketrans("0123456789", "gjklmnprst"))
    return f"Katselise {token}seaduse muutmise eelnõu"


def _settle(page) -> None:
    """Past the 600ms debounce, then let the request land."""
    page.wait_for_timeout(800)
    page.wait_for_load_state("networkidle")


def _create_form(page, base_url) -> None:
    page.goto(f"{base_url}{CREATE_PATH}")
    page.wait_for_load_state("networkidle")


def _draft_requests(page) -> list[str]:
    """Every suggestion request the page makes from now on, in order."""
    seen: list[str] = []
    page.on(
        "request",
        lambda request: (
            seen.append(request.url) if "/teemad/uus/sarnased/" in request.url else None
        ),
    )
    return seen


def test_a_refused_save_and_the_back_that_follows_it(page, base_url):
    """The whole path, in the order somebody actually walks it."""
    sign_in(page, base_url, MARTIN)
    _create_form(page, base_url)
    subject = _coined_subject()

    # A refusal that belongs to the title, with the link block never touched.
    page.fill("#id_title", "")
    _settle(page)
    page.click("button:has-text('Loo teema')")
    page.wait_for_load_state("networkidle")

    assert "/teemad/uus/" in page.url, "the blank-title save was not refused"
    assert LINK_REFUSAL not in page.content(), "an untouched procedural link block was refused"
    # The block is open by default now, so what it must not do is carry a
    # refusal it does not own — the same rule the fold served, reached from the
    # other side. A visible block reporting errors nobody caused is a
    # permanently mandatory-looking panel (docs/adr/0094 §3).
    assert page.locator("#menetluse-link .field__error").count() == 0, (
        "the untouched block shows a refusal it does not own"
    )
    assert page.locator("#menetluse-link").is_visible(), (
        "the block a lawyer is meant to type into on arrival is not on screen"
    )

    # Fix the real problem and file the Teema.
    page.fill("#id_title", subject)
    _settle(page)
    expect(page.locator(SIMILAR_SECTION)).to_have_count(0)

    page.click("button:has-text('Loo teema')")
    page.wait_for_load_state("networkidle")
    assert "/teemad/uus/" not in page.url, "the Teema was not created"

    # Back, with nothing touched: the warning comes back by itself, once.
    asked = _draft_requests(page)
    page.go_back()
    page.wait_for_load_state("networkidle")
    expect(page.locator("#id_title")).to_have_value(subject)
    _settle(page)

    assert len(asked) == 1, f"the restore asked {len(asked)} times, not once"
    expect(page.locator(SIMILAR_SECTION)).to_be_visible()
    assert subject in page.locator(SIMILAR_REGION).inner_text(), (
        "the Teema just created is not named in the restored warning"
    )
    # And the restored page still prints no refusal under a block nobody used.
    assert LINK_REFUSAL not in page.content()

    # Forward and back again is one more refresh, not a storm.
    page.go_forward()
    page.wait_for_load_state("networkidle")
    page.go_back()
    page.wait_for_load_state("networkidle")
    _settle(page)
    assert len(asked) == 2, f"back/forward/back asked {len(asked)} times, not twice"
