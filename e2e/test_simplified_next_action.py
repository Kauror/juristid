"""The approved simplified Teema workflow, driven the way a person drives it.

`tests/test_simplified_next_action.py` proves what gets stored. This file proves
the four states of the approved design and the one behaviour that only exists in
a browser: that pressing `✓ Tehtud` on the current step does not throw away what
somebody has already typed into the composer under it.

That last one is the reason the completion endpoint stopped answering with the
whole column (ADR 0052 §8, §9). It cannot be caught anywhere else — a Django
test client has no unsaved form state to lose.
"""

from __future__ import annotations

import re
from datetime import date, timedelta

import pytest
from playwright.sync_api import expect

from app.core.management.commands.seed_e2e_data import OPEN_TITLE, REVIEW_DUE_TITLE
from e2e.conftest import MARTIN, SANDRA, create_matter, open_composer, open_matter, sign_in

pytestmark = pytest.mark.e2e

#: The four words the composer used to ask for, and the three it used to print
#: in front of a date. None of them belongs on this page any more (ADR 0052).
RETIRED = ("TEEN", "OOTAN", "JÄLGIN", "Ei muuda", "TÄHTAEG", "VAATAN ÜLE", "OODATAV")


def _future(days: int) -> str:
    value = date.today() + timedelta(days=days)
    return f"{value.day}.{value.month}.{value.year}"


def _short(days: int) -> str:
    """The `pp.kk` form the row and the defer chips print."""
    value = date.today() + timedelta(days=days)
    return f"{value.day:02d}.{value.month:02d}"


def set_step(page, text: str, days: int) -> None:
    """Record a next step through the composer, the way the design says."""
    open_composer(page)
    page.locator("[name='next_text']").fill(text)
    page.locator("#id_next_date").fill(_future(days))
    page.locator("[data-composer-submit]").click()
    page.wait_for_load_state("networkidle")
    expect(page.locator(".uxnext__text")).to_have_text(text)


# ---------------------------------------------------------------------------
# STATE A — an existing next action, composer closed
# ---------------------------------------------------------------------------


def test_state_a_the_closed_page_shows_the_step_its_date_and_tehtud(page, base_url):
    sign_in(page, base_url, MARTIN)
    create_matter(page, base_url, "Seisund A brauserikatsest")
    set_step(page, "Vaadata uus eelnõu versioon üle", 22)

    # Composer **open**, which is how a Matter opens since the approved target:
    # recording what happened is the reason this product exists and must not
    # begin with a click (docs/adr/0074 §3).
    assert page.locator("details.uxcomp").evaluate("node => node.open") is True

    row = page.locator(".uxnext")
    expect(row).to_contain_text("Järgmiseks")
    expect(row.locator(".uxnext__text")).to_have_text("Vaadata uus eelnõu versioon üle")
    expect(row.locator(".uxnext__date")).to_be_visible()
    expect(row.get_by_role("button", name="✓ Tehtud")).to_be_visible()

    text = page.inner_text("body")
    for retired in RETIRED:
        assert retired not in text, f"«{retired}» is still on the Teema page"


# ---------------------------------------------------------------------------
# STATE B — the composer open, three questions, the current step still above
# ---------------------------------------------------------------------------


def test_state_b_the_open_composer_asks_three_distinct_questions(page, base_url):
    sign_in(page, base_url, MARTIN)
    create_matter(page, base_url, "Seisund B brauserikatsest")
    set_step(page, "Helistada ministeeriumisse", 5)

    open_composer(page)

    form = page.locator("form[data-composer]")
    expect(form).to_contain_text("Mida tegid või mis juhtus?")
    expect(form).to_contain_text("Järgmiseks")
    expect(form).to_contain_text("Millal?")

    # Three questions, three controls, each reachable and distinct.
    expect(page.locator("textarea.composer__body")).to_be_visible()
    expect(page.locator("[name='next_text']")).to_be_visible()
    expect(page.locator("[data-quickdate]")).to_have_count(4)

    # And the current step is still above it, not replaced by the form.
    expect(page.locator(".uxnext__text")).to_have_text("Helistada ministeeriumisse")
    assert (
        page.locator("details.uxcomp").bounding_box()["y"]
        > page.locator(".uxnext").bounding_box()["y"]
    )

    text = page.inner_text("body")
    for retired in RETIRED:
        assert retired not in text, f"the open composer still offers «{retired}»"


# ---------------------------------------------------------------------------
# STATE C — complete, record the result, set the next step
# ---------------------------------------------------------------------------


def test_state_c_complete_then_record_a_result_and_a_new_step(page, base_url):
    """The whole workflow in one pass, and the three things it must not do.

    The completed step must not become an entry, the entry must not become the
    new step, and the new step must not carry a word of the description.
    """
    sign_in(page, base_url, MARTIN)
    create_matter(page, base_url, "Seisund C brauserikatsest")
    set_step(page, "Helistada ministeeriumisse", 15)

    page.get_by_role("button", name="✓ Tehtud").click()
    page.wait_for_load_state("networkidle")
    expect(page.locator(".uxnext")).to_contain_text("Järgmine samm on määramata")

    open_composer(page)
    page.locator("textarea.composer__body").fill(
        "Ministeerium lubas järgmise versiooni saata nädala lõpuks."
    )
    page.locator("[name='next_text']").fill("Vaadata uus versioon üle")
    page.locator("[data-quickdate]").filter(has_text="+1 nädal").first.click()
    page.locator("[data-composer-submit]").click()
    page.wait_for_load_state("networkidle")

    # The new step is open, and it is only what was typed into `Järgmiseks`.
    row = page.locator(".uxnext")
    expect(row.locator(".uxnext__text")).to_have_text("Vaadata uus versioon üle")
    assert "Ministeerium" not in row.inner_text(), "the description leaked into the step"

    # The result is an entry in the chronology, and the completed step did not
    # write one of its own.
    timeline = page.locator("#ajajoon")
    expect(timeline).to_contain_text("Ministeerium lubas järgmise versiooni")
    assert "Helistasin" not in timeline.inner_text()

    # The first step is finished and gone from the current row, and the
    # chronology says so — it was completed, not superseded.
    assert "Helistada ministeeriumisse" not in row.inner_text()
    expect(timeline).to_contain_text("Helistada ministeeriumisse")


# ---------------------------------------------------------------------------
# STATE D — complete, and stop
# ---------------------------------------------------------------------------


def test_state_d_tehtud_is_a_complete_save_on_its_own(page, base_url):
    """`Saata kiri ministeeriumile` → Tehtud → nothing else.

    Nobody is required to write an entry, set another step, or press Salvesta
    for the completion to be real (ADR 0052 §7, §8).
    """
    sign_in(page, base_url, MARTIN)
    url = create_matter(page, base_url, "Seisund D brauserikatsest")
    set_step(page, "Saata kiri ministeeriumile", 3)

    page.get_by_role("button", name="✓ Tehtud").click()
    page.wait_for_load_state("networkidle")
    expect(page.locator(".uxnext")).to_contain_text("Järgmine samm on määramata")

    # And it survives a reload, which is the only proof that it was persisted
    # rather than swapped away in the browser.
    page.goto(url)
    page.wait_for_load_state("networkidle")
    expect(page.locator(".uxnext")).to_contain_text("Järgmine samm on määramata")
    assert "Saata kiri ministeeriumile" not in page.locator(".uxnext").inner_text()
    # The Teema is still a valid, open Matter with a usable composer.
    expect(page.locator("details.uxcomp")).to_have_count(1)


# ---------------------------------------------------------------------------
# §22 — the unsaved-content regression
# ---------------------------------------------------------------------------


def test_tehtud_does_not_discard_what_is_already_typed_into_the_composer(page, base_url):
    """The reason the completion endpoint stopped swapping `#teema-vaade`.

    A lawyer finishing a step is very often already writing up what came of it.
    Re-rendering the column from the server threw all of it away — the body, the
    next step and the chosen date — at the one moment it was most likely to
    exist (ADR 0052 §9).
    """
    sign_in(page, base_url, MARTIN)
    create_matter(page, base_url, "Salvestamata sisu brauserikatsest")
    set_step(page, "Helistada ministeeriumisse", 4)

    open_composer(page)
    body = "Ministeerium lubas saata uue versiooni nädala lõpuks."
    page.locator("textarea.composer__body").fill(body)
    page.locator("[name='next_text']").fill("Vaadata uus versioon üle")
    page.locator("[data-quickdate]").filter(has_text="+2 nädalat").first.click()
    chosen_date = page.locator("#id_next_date").input_value()
    assert chosen_date, "the quick chip did not fill the field that is submitted"

    page.get_by_role("button", name="✓ Tehtud").click()
    page.wait_for_load_state("networkidle")

    # The completed step is gone from the current row…
    expect(page.locator(".uxnext")).to_contain_text("Järgmine samm on määramata")
    # …and every unsaved value is exactly where it was.
    assert page.locator("details.uxcomp").evaluate("node => node.open") is True
    assert page.locator("textarea.composer__body").input_value() == body
    assert page.locator("[name='next_text']").input_value() == "Vaadata uus versioon üle"
    assert page.locator("#id_next_date").input_value() == chosen_date

    # And saving now writes what was typed, not what was recovered.
    page.locator("[data-composer-submit]").click()
    page.wait_for_load_state("networkidle")
    expect(page.locator(".uxnext__text")).to_have_text("Vaadata uus versioon üle")
    expect(page.locator("#ajajoon")).to_contain_text("Ministeerium lubas saata uue versiooni")


# ---------------------------------------------------------------------------
# Pilot QA F-04 — «✓ Tehtud» never discards what is already typed
#
# «Lükka edasi» left the Järgmiseks row with the approved target: the row is the
# label, the text, the date and two controls, and a second disclosure holding
# four POST buttons and a date box does not belong in the one line on the page
# that has to be readable at a glance (docs/adr/0074 §20).
#
# The route, the service and F-05's counting-from-the-step rule are untouched
# and are asserted in `tests/test_pilot_p1_workflows.py`, which drives them
# directly. F-04 — that finishing a step does not throw away the write-up
# somebody is in the middle of — is already proven above on «✓ Tehtud», the
# control on this row that still does it.
# ---------------------------------------------------------------------------


def test_the_jargmiseks_row_carries_only_the_targets_two_controls(page, base_url):
    """Text, date, `✓ Tehtud`, `Muuda` — and no defer disclosure."""
    sign_in(page, base_url, SANDRA)
    create_matter(page, base_url, "Rea kontrollid brauserikatsest")
    set_step(page, "Saata kiri ministeeriumile", 30)

    row = page.locator(".uxnext")
    expect(row.get_by_role("button", name="✓ Tehtud")).to_be_visible()
    expect(row.get_by_role("button", name="Muuda")).to_be_visible()
    expect(page.locator("summary.uxnext__defersum")).to_have_count(0)
    assert "Lükka edasi" not in row.inner_text()


# ---------------------------------------------------------------------------
# §23 — the quick dates write the field that is submitted
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("label", "days"), [("Täna", 0), ("Homme", 1), ("+1 nädal", 7), ("+2 nädalat", 14)]
)
def test_each_quick_date_writes_the_day_it_names(page, base_url, label, days):
    sign_in(page, base_url, SANDRA)
    open_matter(page, base_url, OPEN_TITLE)
    open_composer(page)

    page.locator("[data-quickdate]").filter(has_text=label).first.click()
    assert page.locator("#id_next_date").input_value() == _future(days)


def test_a_typed_date_still_works_and_the_chips_agree_with_it(page, base_url):
    sign_in(page, base_url, SANDRA)
    open_matter(page, base_url, OPEN_TITLE)
    open_composer(page)

    page.locator("#id_next_date").fill(_future(1))
    page.locator("#id_next_date").dispatch_event("change")
    chip = page.locator("[data-quickdate]").filter(has_text="Homme").first
    expect(chip).to_have_attribute("aria-pressed", "true")


# ---------------------------------------------------------------------------
# §6 — a historical WAIT keeps its meaning and never states it
# ---------------------------------------------------------------------------


def test_a_seeded_wait_reads_as_a_sentence_and_a_date(page, base_url):
    """`Ootame ministeeriumi vastust`, stored as a WAIT/REVIEW_ON with a passed
    review date. The page shows the sentence and the date, and none of the
    vocabulary; the row is not in the overdue state, because waiting is not
    lateness and that domain rule is unchanged."""
    sign_in(page, base_url, SANDRA)
    open_matter(page, base_url, REVIEW_DUE_TITLE)

    row = page.locator(".uxnext")
    expect(row.locator(".uxnext__text")).to_have_text("Ootame ministeeriumi vastust")
    expect(row.locator(".uxnext__date")).to_be_visible()
    expect(row).not_to_have_class(re.compile("uxnext--overdue"))
    expect(row.locator(".uxnext__date--overdue")).to_have_count(0)

    text = row.inner_text()
    for retired in RETIRED:
        assert retired not in text, f"the row still says «{retired}»"

    # And it is finishable from here like anything else — the control is
    # offered, and pressing it is asserted in `tests/test_simplified_next_action`
    # rather than here. This file shares one seeded world with the rest of the
    # browser suite, and a test that completed the seeded WAIT would decide what
    # every later reader of this Matter sees.
    expect(row.get_by_role("button", name="✓ Tehtud")).to_be_visible()


# ---------------------------------------------------------------------------
# §24 — Sildid are gone; Muu valdkond is not
# ---------------------------------------------------------------------------


def test_the_rail_has_no_sildid_card(page, base_url):
    sign_in(page, base_url, SANDRA)
    open_matter(page, base_url, OPEN_TITLE)

    rail = page.locator("aside.rail")
    expect(rail).to_be_visible()
    assert "Sildid" not in rail.inner_text()
    assert "Silte ei ole." not in rail.inner_text()
    expect(rail.locator(".tag")).to_have_count(0)


def test_the_rail_carries_the_four_target_rows_and_no_maintenance_ones(page, base_url):
    """`Teemaviide`, `Menetlusliik`, `Kellelt`, `Kellele` — every one of them a
    question a lawyer asks mid-sentence (TEEMA_TARGET_SPEC §G.1).

    `Muu valdkond`, `Andmeklass` and `Märgi testandmeteks` are retired from this
    page: the first is a correction to how the file was classified and the other
    two are a developer's switch. The columns, the values and the endpoints are
    untouched, and `Muuda teemat` still edits what it edited
    (docs/adr/0074 §17).
    """
    sign_in(page, base_url, MARTIN)
    create_matter(page, base_url, "Raili brauserikatse")

    rail = page.locator("#teema-andmed")
    for row in ("Teemaviide", "Menetlusliik", "Kellelt", "Kellele"):
        expect(rail).to_contain_text(row)
    for gone in ("Muu valdkond", "Andmeklass", "Märgi testandmeteks", "Saabus"):
        assert gone not in rail.inner_text()


# ---------------------------------------------------------------------------
# §25 — responsive
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("width", [1440, 1024, 768, 420])
def test_the_teema_surface_does_not_scroll_sideways(page, base_url, width):
    sign_in(page, base_url, MARTIN)
    create_matter(page, base_url, f"Laiuse {width} brauserikatse")
    set_step(page, "Vaadata pikk ja põhjalik eelnõu versioon veel korra üle", 9)

    page.set_viewport_size({"width": width, "height": 900})
    page.wait_for_load_state("networkidle")

    assert not page.evaluate(
        "() => document.documentElement.scrollWidth > document.documentElement.clientWidth + 1"
    ), f"the Teema page scrolls sideways at {width}px"

    # The step stays scannable: its text and its date are both on the page, and
    # the date has not been pushed out of the row that owns it.
    row = page.locator(".uxnext")
    expect(row.locator(".uxnext__text")).to_be_visible()
    expect(row.locator(".uxnext__date")).to_be_visible()
    assert row.bounding_box()["width"] <= width

    # And the composer's fields use the width they are given rather than
    # standing in a narrow column of their own.
    open_composer(page)
    for control in ("textarea.composer__body", "[name='next_text']"):
        box = page.locator(control).bounding_box()
        assert box["width"] > 0
        assert box["x"] + box["width"] <= width + 1
