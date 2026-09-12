"""Browser-only findings of the 12 September adversarial UI/workflow QA round.

TESTS ONLY — BOTH TESTS HERE ARE EXPECTED TO FAIL until the product fixes land.

Two things a request-level test cannot see: whether the person can *see* the
error the server sent back, and whether a page fits the phone it is read on.
The rest of the round is in ``tests/test_qa_adversarial_sep12.py``.
"""

from __future__ import annotations

import pytest

from e2e.conftest import MARTIN, sign_in, unique_title

pytestmark = pytest.mark.e2e

CREATE_PATH = "/teemad/uus/"


def _document_overflows(page) -> tuple[int, int]:
    return page.evaluate(
        "() => [document.documentElement.clientWidth, document.documentElement.scrollWidth]"
    )


# ---------------------------------------------------------------------------
# QA-01 — the save was refused, and the page does not say so anywhere visible
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("width,height", [(1440, 900), (420, 760)])
def test_qa01_a_refused_save_shows_the_person_why(page, base_url, width, height):
    """Fill in JÄRGMISEKS without a date, press «Loo teema», and see nothing.

    `Järgmiseks` is the last block on `Uus teema` and a step without a date is
    refused. The refusal is correct and the message is good — «Vali järgmise
    tegevuse kuupäev.» — but the 400 re-renders the form scrolled to the top
    with the error roughly 1100px down at 1440x900 and roughly **1250px** down
    at 420x760, no summary at the head of the form, and focus on `<body>`.

    What the person sees is the form they were just looking at, unchanged.
    They pressed Save and nothing happened, and on a phone the explanation is
    two and a half screens below the fold.

    The assertion is only that a refusal is visible: that after pressing Save
    the first error is in the viewport, or something at the top of the form
    says the save failed. Which of the two is a design decision.
    """
    page.set_viewport_size({"width": width, "height": height})
    sign_in(page, base_url, MARTIN)
    page.goto(f"{base_url}{CREATE_PATH}")
    page.wait_for_load_state("networkidle")

    page.get_by_label("Pealkiri", exact=False).fill(unique_title("QA refusal"))
    page.locator("input[name='next-text']").fill("Helista ministeeriumisse")
    page.get_by_role("button", name="Loo teema").click()
    page.wait_for_load_state("networkidle")

    error = page.locator(".field__error").first
    error.wait_for(state="attached")

    in_view = error.evaluate(
        "el => { const r = el.getBoundingClientRect();"
        " return r.top >= 0 && r.bottom <= window.innerHeight; }"
    )
    summarised = page.locator("[role='alert'], .form__errors, .banner--error").count() > 0

    assert in_view or summarised, (
        f"at {width}x{height} the save was refused and nothing on the first screen says so: "
        f"the only error sits at y={error.evaluate('el => el.getBoundingClientRect().top')} "
        f"in a {height}px viewport"
    )


# ---------------------------------------------------------------------------
# QA-08 — Statistika takes the whole page sideways on a phone
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("width", [375, 420])
def test_qa08_statistics_does_not_scroll_the_document_sideways(page, base_url, width):
    """The six-tab nav is 503px wide in a 396px box, and pushes the document.

    Every other page in the product keeps a wide strip inside its own
    scroller — the Teema process strip sets `overflow-x: auto` below 700px for
    exactly this reason. `.tabs` on `/statistika/` is `overflow-x: visible`,
    so at 375px the document's `scrollWidth` is 515 against a 375 client width
    and the whole page, header and all, slides under the thumb.
    """
    page.set_viewport_size({"width": width, "height": 812})
    sign_in(page, base_url, MARTIN)
    page.goto(f"{base_url}/statistika/")
    page.wait_for_load_state("networkidle")

    client_width, scroll_width = _document_overflows(page)

    assert scroll_width <= client_width + 1, (
        f"/statistika/ at {width}px scrolls the document horizontally: "
        f"scrollWidth={scroll_width} against clientWidth={client_width}"
    )
