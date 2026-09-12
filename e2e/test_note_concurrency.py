"""Two tabs, one note, in a real browser.

The Python suite proves the boundary at the service and at the route. What only a
browser can show is the half that is htmx: that the refusal is *shown* rather than
dropped, that the person's own words are still in the box they are looking at
after it, and — the one that would break the ordinary case — that the new version
token actually reaches the hidden field, so a second save from the same tab does
not arrive holding the version the first one replaced and conflict with itself
(docs/adr/0077, adversarial QA 2026-09-12 QA-09).

Two pages in one browser context, because this is one signed-in person with two
tabs open, which is how a lawyer reads a file while writing about it — not two
people and not two sessions.

The seeded open Matter is only *read*; what these tests write is the private note
on it, which no visual baseline photographs and no other test reads.
"""

from __future__ import annotations

import pytest

from e2e.conftest import MARTIN, open_matter, sign_in

pytestmark = pytest.mark.e2e

#: The seeded open Matter, spelled as `seed_e2e_data` writes it. Copied rather
#: than imported for the reason `e2e/conftest.py` gives: this directory
#: deliberately imports no application code.
OPEN_TITLE = (
    "Tavaline avatud teema kõigile nähtav — pakendiseaduse ja sellega seonduvalt "
    "teiste seaduste muutmise seaduse eelnõu väljatöötamiskavatsus"
)

#: Long enough that «retype it from memory» is the wrong answer, which is the
#: whole reason this behaviour exists.
_A = "A: ministeerium lubas telefonis, et tähtaega pikendatakse kahe nädala võrra."
_B = "B: küsi Liinalt, kas see on direktiivi nõue või meie oma tõlgendus."


def _texts() -> tuple[str, str]:
    """One pair of notes nobody else in this run has written.

    These tests write the same private note as each other, and the autosave fires
    on `input changed` — so filling a box with the value it already holds produces
    no event, no save, and a test that silently asserts nothing. The seeded
    database is shared across a shard, so the nonce has to be per call rather than
    per module (see `e2e/conftest.py` on the shared world).
    """
    import uuid

    nonce = uuid.uuid4().hex[:8]
    return f"{_A} [{nonce}]", f"{_B} [{nonce}]"


#: The autosave fires 900 ms after typing stops on the Teema rail and 800 ms on
#: Minu asjad. This is that plus the round trip, with room to spare — a flake here
#: would read as a concurrency defect, which is the last thing worth guessing at.
SETTLE_MS = 1600


def test_a_stale_second_tab_is_told_and_overwrites_nothing(browser, base_url):
    """The finding itself, end to end.

    A and B both hold the note as it was. A saves. B types and its autosave fires.
    """
    a_text, b_text = _texts()
    context = browser.new_context()
    try:
        a = context.new_page()
        b = context.new_page()
        sign_in(a, base_url, MARTIN)
        open_matter(a, base_url, OPEN_TITLE)
        b.goto(a.url)
        b.wait_for_load_state("networkidle")

        box_a = a.locator("textarea.railnote__area")
        box_b = b.locator("textarea.railnote__area")
        box_a.wait_for()
        box_b.wait_for()

        box_a.fill(a_text)
        a.wait_for_timeout(SETTLE_MS)
        assert "Salvestatud" in a.locator("#teema-markme-seis").inner_text()

        box_b.fill(b_text)
        b.wait_for_timeout(SETTLE_MS)

        # B is told, in the slot the successful save also writes to — so the
        # answer is where B was already looking for it.
        hint = b.locator("#teema-markme-seis").inner_text()
        assert "teises aknas" in hint.lower(), hint

        # B's own words are still in B's box. This is the whole point: nothing
        # swapped the textarea, so the caret and the selection are where they were.
        assert box_b.input_value() == b_text

        # And A's version is readable in B, without having replaced B's.
        conflict = b.locator("#teema-markme-konflikt")
        assert conflict.is_visible()
        assert a_text in conflict.inner_text()

        # A's note is what is stored.
        a.reload()
        a.wait_for_load_state("networkidle")
        assert a.locator("textarea.railnote__area").input_value() == a_text
    finally:
        context.close()


def test_the_stale_tab_can_save_again_after_reloading(browser, base_url):
    """The way out has to actually be a way out.

    A reload is what the sentence tells the person to do, so a reload has to leave
    the box working — otherwise the refusal is a dead end rather than a warning.
    """
    a_text, b_text = _texts()
    context = browser.new_context()
    try:
        a = context.new_page()
        b = context.new_page()
        sign_in(a, base_url, MARTIN)
        open_matter(a, base_url, OPEN_TITLE)
        b.goto(a.url)
        b.wait_for_load_state("networkidle")

        a.locator("textarea.railnote__area").fill(a_text)
        a.wait_for_timeout(SETTLE_MS)
        b.locator("textarea.railnote__area").fill(b_text)
        b.wait_for_timeout(SETTLE_MS)
        assert "teises aknas" in b.locator("#teema-markme-seis").inner_text().lower()

        b.reload()
        b.wait_for_load_state("networkidle")
        box = b.locator("textarea.railnote__area")
        # The reloaded box holds the stored version, which is A's.
        assert box.input_value() == a_text
        # And the conflict block is gone, because there is nothing left to resolve.
        assert b.locator("#teema-markme-konflikt").inner_text().strip() == ""

        box.fill(f"{a_text} {b_text}")
        b.wait_for_timeout(SETTLE_MS)
        assert "Salvestatud" in b.locator("#teema-markme-seis").inner_text()
    finally:
        context.close()


def test_consecutive_saves_from_one_tab_do_not_conflict_with_themselves(page, base_url):
    """The ordinary case, which is what the token swap exists to keep working.

    Three saves from one box. The response replaces the hidden revision out of
    band; if that swap did not land, the second save would arrive holding the
    version the first replaced — and the fix would have broken typing.
    """
    sign_in(page, base_url, MARTIN)
    open_matter(page, base_url, OPEN_TITLE)
    box = page.locator("textarea.railnote__area")
    box.wait_for()

    run = _texts()[0][-9:-1]
    seen: list[str] = []
    for number in range(1, 4):
        box.fill(f"Salvestus number {number} [{run}].")
        page.wait_for_timeout(SETTLE_MS)
        hint = page.locator("#teema-markme-seis").inner_text()
        assert "Salvestatud" in hint, f"save {number} was refused: {hint}"
        assert "teises aknas" not in hint.lower()
        token = page.locator("#id_markmed-revision").get_attribute("value")
        assert token, f"save {number} left the hidden revision empty"
        seen.append(token)

    # Every save moved the token on, which is what makes the next one legal.
    assert len(set(seen)) == 3, seen

    page.reload()
    page.wait_for_load_state("networkidle")
    assert page.locator("textarea.railnote__area").input_value() == f"Salvestus number 3 [{run}]."


def test_the_desk_pad_behaves_the_same_way(browser, base_url):
    """Minu asjad's own `Märkmed`, which shares the contract and not the model.

    It is the pad most likely to be open in a second tab all day, so the two-tab
    case matters here at least as much as it does on a Teema.
    """
    a_text, b_text = _texts()
    context = browser.new_context()
    try:
        a = context.new_page()
        b = context.new_page()
        sign_in(a, base_url, MARTIN)
        a.goto(f"{base_url}/minu-asjad/")
        a.wait_for_load_state("networkidle")
        b.goto(f"{base_url}/minu-asjad/")
        b.wait_for_load_state("networkidle")

        a.locator("#pw-note-body").fill(a_text)
        a.wait_for_timeout(SETTLE_MS)

        box_b = b.locator("#pw-note-body")
        box_b.fill(b_text)
        b.wait_for_timeout(SETTLE_MS)

        assert "teises aknas" in b.locator("#pw-note-meta").inner_text().lower()
        assert box_b.input_value() == b_text
        conflict = b.locator("#pw-note-conflict")
        assert conflict.is_visible()
        assert a_text in conflict.inner_text()

        a.reload()
        a.wait_for_load_state("networkidle")
        assert a.locator("#pw-note-body").input_value() == a_text
    finally:
        context.close()


def test_the_desk_pad_saves_consecutively(page, base_url):
    """The ordinary case on the other pad, for the same reason."""
    sign_in(page, base_url, MARTIN)
    page.goto(f"{base_url}/minu-asjad/")
    page.wait_for_load_state("networkidle")
    box = page.locator("#pw-note-body")
    box.wait_for()

    run = _texts()[0][-9:-1]
    seen: list[str] = []
    for number in range(1, 4):
        box.fill(f"Meeldetuletus {number} [{run}].")
        page.wait_for_timeout(SETTLE_MS)
        meta = page.locator("#pw-note-meta").inner_text()
        assert "teises aknas" not in meta.lower(), f"save {number} was refused: {meta}"
        token = page.locator("#pw-note-revision").get_attribute("value")
        assert token, f"save {number} left the hidden revision empty"
        seen.append(token)

    assert len(set(seen)) == 3, seen

    page.reload()
    page.wait_for_load_state("networkidle")
    assert page.locator("#pw-note-body").input_value() == f"Meeldetuletus 3 [{run}]."
