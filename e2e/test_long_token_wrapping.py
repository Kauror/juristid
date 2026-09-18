"""A pasted link in a note must not take the page sideways.

Overnight QA (QA-10) filed one ordinary note on one ordinary Matter — a
sentence and the EUR-Lex address it refers to — and the Teema page acquired
505px of horizontal scroll at 375px. Nothing was wrong with the note. An
EUR-Lex address carrying its query string and a tracking parameter is one
179-character word, `.richtext` had no rule for breaking a word that cannot
fit, and every ancestor between it and `<html>` is `overflow-x: visible`, so
the unbreakable width propagated all the way out to the document.

**The assertion is the page, not the stylesheet.** A test that read
`overflow-wrap` off the computed style would prove the rule is written down and
nothing about whether it works: the defect is a width, so this file measures
widths. `document.documentElement.scrollWidth <= clientWidth` is the invariant a
reader actually has, and the `.richtext` element's own `scrollWidth` is measured
beside it so that a failure says which of the two moved.

**And it measures at four widths**, because the defect scaled with the viewport
rather than appearing at one of them: 505px of overflow at 375, 460 at 420, 132
at 768 and none at 1440. 1440 is the control — the token is shorter than the
desktop content column, so a desktop layout that changed would mean this rule
reached further than the word it was written for.

The second test is the other half of the contract. Wrapping mid-word is the
last resort and must stay that way: ordinary prose has to go on breaking at
spaces, and a sentence with room to spare has to go on occupying one line.
"""

from __future__ import annotations

import pytest

from e2e.conftest import MARTIN, create_matter, open_composer, sign_in, unique_title

pytestmark = pytest.mark.e2e

#: The paste from the QA reproduction, verbatim in shape: a sentence, a space,
#: and then 179 characters with nothing in them a browser may break at.
PASTED_LINK = (
    "Vaata eelnõu siit: https://eur-lex.europa.eu/legal-content/ET/TXT/HTML/"
    "?uri=CELEX%3A52026PC0142&qid=1758170000000&from=ET&locale=et"
    "&someVeryLongTrackingParameter=abcdefghijklmnopqrstuvwxyz0123456789"
)

#: The token on its own, for the assertion that wrapping did not eat any of it.
UNBREAKABLE = PASTED_LINK.split(" ")[-1]

#: An ordinary sentence of the kind these notes are mostly made of. No token in
#: it is longer than the narrowest column the product renders, so every line
#: break it takes at any width is available at a space.
#:
#: Short enough to fit the 558px content column on one line, and long enough to
#: need two in the 317px one — so the comparison below covers prose that wraps
#: and prose that does not, which are different code paths in a line breaker.
ORDINARY_PROSE = "Ministeerium saatis eelnõu teisele kooskõlastusringile."

#: 375 is the QA viewport and the narrowest the product claims; 420 is the
#: width the responsive brief names; 768 is the tablet breakpoint; 1440 is the
#: desktop control.
WIDTHS = (375, 420, 768, 1440)

#: Sub-pixel layout means a document can report one more pixel of scroll than
#: content, so the existing sweeps allow exactly one. Copied rather than
#: imported for the reason `e2e/conftest.py` gives about seeded titles:
#: `e2e/test_integration_420px.py` owns the same convention and neither file
#: should be able to change the other's tolerance by accident.
ROUNDING = 1


def _file_a_note(page, text: str) -> None:
    """Write one note through the real composer, the way a lawyer would."""
    open_composer(page)
    page.locator("#lisa-marge .composer__body").fill(text)
    page.locator("#lisa-marge button[type=submit]").click()
    page.wait_for_load_state("networkidle")


def _matter_carrying_the_paste(page, base_url: str) -> str:
    """A Matter with the pasted link on it, and an ordinary note beside it.

    Both notes on one Matter on purpose: the control is only worth anything if
    it is measured on the very page where the token forced a wrap, under the
    same column width and the same cascade.

    **Filed with an owner**, which costs nothing here and keeps this file out of
    the `vastutaja=puudub` register. `e2e/test_ux_pass.py` asserts against the
    one seeded unassigned Matter and sorts after this file, so a shard holding
    both would walk its row further down the list for every Matter this file
    leaves ownerless — the contamination its own docstring records having been
    bitten by once already (ci_sharding.py, and the note at
    `test_an_owner_can_be_set_from_the_register_row`).
    """
    sign_in(page, base_url, MARTIN)
    url = create_matter(page, base_url, unique_title("Pikk link"), owner=MARTIN)
    _file_a_note(page, ORDINARY_PROSE)
    _file_a_note(page, PASTED_LINK)
    page.goto(url)
    page.wait_for_load_state("networkidle")
    return url


def _measure(page, width: int) -> dict:
    """The document's own width against its content, and every authored body."""
    page.set_viewport_size({"width": width, "height": 812})
    # A viewport change is not a layout: read after the browser has done one.
    page.wait_for_timeout(250)
    return page.evaluate(
        """() => {
            const de = document.documentElement;
            return {
                docClientWidth: de.clientWidth,
                docScrollWidth: de.scrollWidth,
                bodies: [...document.querySelectorAll('.richtext')].map(el => {
                    const box = el.getBoundingClientRect();
                    return {
                        text: (el.textContent || '').trim(),
                        clientWidth: el.clientWidth,
                        scrollWidth: el.scrollWidth,
                        clientHeight: el.clientHeight,
                        scrollHeight: el.scrollHeight,
                        right: box.right,
                        left: box.left,
                    };
                }),
            };
        }"""
    )


def _the_pasted_body(bodies: list[dict]) -> dict:
    """The authored body holding the token, found by the token itself."""
    carrying = [body for body in bodies if UNBREAKABLE in body["text"]]
    assert carrying, "the note holding the pasted link is not on the page"
    return carrying[0]


def test_a_pasted_link_in_a_note_never_scrolls_the_page_sideways(page, base_url):
    """QA-10, at every width the product claims to support.

    Three assertions per width, in the order a failure is easiest to read:
    the document does not scroll sideways; the body holding the token has no
    hidden width of its own; and the body is inside the viewport rather than
    merely attached to it. The middle one is what localises a regression — a
    document that grew while every `.richtext` still fits means something other
    than this rule moved.
    """
    _matter_carrying_the_paste(page, base_url)

    for width in WIDTHS:
        measured = _measure(page, width)
        overflow = measured["docScrollWidth"] - measured["docClientWidth"]
        assert overflow <= ROUNDING, (
            f"a pasted link gives the Teema page {overflow}px of horizontal "
            f"scroll at {width}px "
            f"(scrollWidth {measured['docScrollWidth']}, "
            f"clientWidth {measured['docClientWidth']})"
        )

        body = _the_pasted_body(measured["bodies"])
        assert body["scrollWidth"] <= body["clientWidth"] + ROUNDING, (
            f"the note holding the pasted link is {body['scrollWidth']}px wide "
            f"inside a {body['clientWidth']}px column at {width}px"
        )
        assert body["left"] >= -ROUNDING and body["right"] <= width + ROUNDING, (
            f"the note holding the pasted link sits at "
            f"{body['left']}..{body['right']} in a {width}px viewport"
        )


def test_the_whole_pasted_link_stays_readable_at_375px(page, base_url):
    """Wrapping is not hiding: every character is still on the screen.

    The alternative fixes for this defect — an ellipsis, a clip, a nested
    horizontal scroller — all satisfy "the page does not scroll" while taking
    the address away from the person who pasted it. The address is the content,
    so this asserts it survives: the whole token in the text, and a body whose
    rendered height is its full height rather than a window onto more.
    """
    _matter_carrying_the_paste(page, base_url)
    measured = _measure(page, 375)
    body = _the_pasted_body(measured["bodies"])

    assert UNBREAKABLE in body["text"], "wrapping lost part of the pasted link"
    assert body["scrollHeight"] <= body["clientHeight"] + ROUNDING, (
        f"the note is {body['scrollHeight']}px of content in a "
        f"{body['clientHeight']}px box — something is clipped"
    )
    assert "…" not in body["text"] and "..." not in body["text"], (
        "the pasted link has been truncated rather than wrapped"
    )


def test_ordinary_prose_still_breaks_only_at_spaces(page, base_url):
    """The control: a word is broken only when it cannot fit any other way.

    Two ways of saying the same thing, because either alone is weak.

    First, directly: a sentence with room to spare occupies one line. At 1440
    the content column is 558px and this sentence needs about 430, so a layout
    that put it on two would be breaking prose that did not need breaking.

    Second, by comparison with the layout the browser produced before the rule
    existed. The counterfactual is constructed on the page — the same element,
    the same text, the same column, with word-breaking returned to its default
    — and the two heights must match exactly. A height that matches at four
    widths is a sentence taking the same line breaks, which is the outcome
    «normal prose is untouched» actually means. Heights rather than pixels:
    `e2e/test_ui_regression.py` owns pixels, and it owns them under a font
    rasterisation this assertion must not depend on.
    """
    _matter_carrying_the_paste(page, base_url)

    for width in WIDTHS:
        measured = _measure(page, width)
        prose = [
            body for body in measured["bodies"] if body["text"].startswith("Ministeerium saatis")
        ]
        assert prose, f"the ordinary note is not on the page at {width}px"

        comparison = page.evaluate(
            """() => {
                const el = [...document.querySelectorAll('.richtext')]
                    .find(n => (n.textContent || '').trim().startsWith('Ministeerium saatis'));
                const now = el.getBoundingClientRect().height;
                // The same element as it laid out before the wrapping rule:
                // `normal` is what `.richtext` computed to on main.
                const previous = el.style.overflowWrap;
                el.style.overflowWrap = 'normal';
                void el.offsetWidth;
                const before = el.getBoundingClientRect().height;
                el.style.overflowWrap = previous;
                const line = parseFloat(getComputedStyle(el).lineHeight);
                return {now, before, line};
            }"""
        )
        assert abs(comparison["now"] - comparison["before"]) < 0.5, (
            f"ordinary prose reflowed at {width}px: {comparison['before']}px "
            f"tall before the wrapping rule, {comparison['now']}px after — the "
            f"rule is reaching words that could already break at a space"
        )

        if width == 1440:
            assert comparison["now"] < comparison["line"] * 1.5, (
                f"an ordinary sentence with room to spare is "
                f"{comparison['now']}px tall against a {comparison['line']}px "
                f"line — prose is being broken where it need not be"
            )
