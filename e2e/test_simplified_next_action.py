"""The approved simplified Teema workflow, driven the way a person drives it.

`tests/test_simplified_next_action.py` proves what gets stored. This file proves
the states of the approved design in a browser.

**What this file used to prove is gone, and its going is the point.** It was
built around one behaviour that only existed in a browser: that pressing
`✓ Tehtud` on the current step did not throw away what somebody had already
typed into the composer under it (ADR 0052 §8, §9). There is no `✓ Tehtud` and
no composer under it any more. Describing what was done about the current task
*is* completing it — one form, one button, one save — so the state that could be
lost does not exist (docs/adr/0075 §3).

What this file proves instead is the shape that replaced it: that the completion
is one operation, that a note leaves the open step alone, and that the quick
dates, the historical kinds and the responsive rules the previous round settled
all still hold.
"""

from __future__ import annotations

import re
from datetime import date, timedelta

import pytest
from playwright.sync_api import expect

from app.core.management.commands.seed_e2e_data import OPEN_TITLE, REVIEW_DUE_TITLE
from e2e.conftest import (
    MARTIN,
    SANDRA,
    create_matter,
    finish_current_action,
    open_composer,
    open_matter,
    open_next_action_form,
    sign_in,
)

pytestmark = pytest.mark.e2e

#: The four words the composer used to ask for, and the three it used to print
#: in front of a date. None of them belongs on this page any more (ADR 0052).
RETIRED = ("TEEN", "OOTAN", "JÄLGIN", "Ei muuda", "TÄHTAEG", "VAATAN ÜLE", "OODATAV")


def _future(days: int) -> str:
    value = date.today() + timedelta(days=days)
    return f"{value.day}.{value.month}.{value.year}"


def set_step(page, text: str, days: int) -> None:
    """Record a next step, the way the design says: its own panel, its own save."""
    open_next_action_form(page)
    page.locator("#lisa-jargmine [name='text']").fill(text)
    page.locator("#id_target_date").fill(_future(days))
    page.locator("#lisa-jargmine button[type=submit]").click()
    page.wait_for_load_state("networkidle")
    expect(page.locator(".curact__text")).to_have_text(text)


# ---------------------------------------------------------------------------
# STATE A — an existing next action, and one way to finish it
# ---------------------------------------------------------------------------


def test_state_a_the_page_shows_the_step_its_date_and_one_way_to_finish_it(page, base_url):
    sign_in(page, base_url, MARTIN)
    create_matter(page, base_url, "Seisund A brauserikatsest")
    set_step(page, "Vaadata uus eelnõu versioon üle", 22)

    zone = page.locator("#praegune-tegevus")
    expect(zone.locator(".curact__text")).to_have_text("Vaadata uus eelnõu versioon üle")
    expect(zone.locator(".curact__date")).to_be_visible()
    # One question, one button, and no second control that completes without an
    # answer to it (docs/adr/0075 §3).
    expect(zone.get_by_text("Mida tegid?", exact=True)).to_be_visible()
    expect(zone.locator(".curact__form button[type=submit]")).to_have_count(1)
    expect(zone.get_by_role("button", name="✓ Tehtud")).to_have_count(0)
    expect(zone.get_by_role("button", name="Märgi tehtuks")).to_have_count(0)

    text = zone.inner_text()
    for retired in RETIRED:
        assert retired not in text, f"the zone still says «{retired}»"


# ---------------------------------------------------------------------------
# STATE B — recording the result is what completes the step
# ---------------------------------------------------------------------------


def test_saving_the_result_completes_the_step_in_one_save(page, base_url):
    """**The behaviour this file exists for, in the shape that replaced the old
    one.** Nothing is completed without a description, and no second save is
    needed to write one (docs/adr/0075 §3)."""
    sign_in(page, base_url, MARTIN)
    url = create_matter(page, base_url, "Ühe salvestuse brauserikatse")
    set_step(page, "Helistada ministeeriumisse", 3)

    finish_current_action(page, "Helistasin; uus versioon tuleb reedel.")

    # The step is gone from the zone, and the result is in the chronology.
    expect(page.locator("#praegune-tegevus")).to_contain_text("Järgmine samm on määramata")
    expect(page.locator("#ajalugu-loend")).to_contain_text("uus versioon tuleb reedel")
    # And no new step was opened on anybody's behalf (docs/adr/0075 §5).
    expect(page.get_by_text("+ Järgmine tegevus")).to_have_count(1)

    page.goto(url)
    page.wait_for_load_state("networkidle")
    expect(page.locator("#praegune-tegevus")).to_contain_text("Järgmine samm on määramata")
    assert "Helistada ministeeriumisse" not in page.locator("#praegune-tegevus").inner_text()


def test_a_blank_result_is_refused_and_the_step_stays_open(page, base_url):
    """A file is supplementary evidence, never the description, and a step
    marked done with nothing said about it records only a button press."""
    sign_in(page, base_url, MARTIN)
    create_matter(page, base_url, "Tühja tulemuse brauserikatse")
    set_step(page, "Saata kiri ministeeriumile", 5)

    page.locator("#praegune-tegevus button[type=submit]").last.click()
    page.wait_for_load_state("networkidle")

    expect(page.get_by_text("Kirjelda, mida tegid.")).to_be_visible()
    expect(page.locator(".curact__text")).to_have_text("Saata kiri ministeeriumile")


def test_a_marge_records_what_happened_and_leaves_the_step_open(page, base_url):
    """The other half of the split: something happened, and it is *not* the
    current task finishing (docs/adr/0075 §2)."""
    sign_in(page, base_url, MARTIN)
    create_matter(page, base_url, "Märkme brauserikatse")
    set_step(page, "Oodata ministeeriumi vastust", 9)

    open_composer(page)
    page.locator("#lisa-marge .composer__body").fill("Ministeerium helistas vahepeal.")
    page.locator("#lisa-marge button[type=submit]").click()
    page.wait_for_load_state("networkidle")

    expect(page.locator("#ajalugu-loend")).to_contain_text("Ministeerium helistas vahepeal")
    expect(page.locator(".curact__text")).to_have_text("Oodata ministeeriumi vastust")


# ---------------------------------------------------------------------------
# STATE C — the zone's controls
# ---------------------------------------------------------------------------


def test_the_current_action_zone_carries_only_the_targets_controls(page, base_url):
    """The task, its date, `Mida tegid?` and `Muuda` — and no defer disclosure."""
    sign_in(page, base_url, SANDRA)
    create_matter(page, base_url, "Rea kontrollid brauserikatsest")
    set_step(page, "Saata kiri ministeeriumile", 30)

    zone = page.locator("#praegune-tegevus")
    # `Muuda` is a native `<summary>` rather than a button, which is what
    # keeps it operable with scripting off (brief §33).
    expect(zone.get_by_text("Muuda", exact=True)).to_be_visible()
    expect(zone.get_by_text("Mida tegid?", exact=True)).to_be_visible()
    expect(page.locator("summary.uxnext__defersum")).to_have_count(0)
    assert "Lükka edasi" not in zone.inner_text()
    # And the launcher does not offer a second way to set the same one step.
    expect(page.get_by_text("+ Järgmine tegevus")).to_have_count(0)


# ---------------------------------------------------------------------------
# §23 — the quick dates write the field that is submitted
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("label", "days"), [("Täna", 0), ("Homme", 1), ("+1 nädal", 7), ("+2 nädalat", 14)]
)
def test_each_quick_date_writes_the_day_it_names(page, base_url, label, days):
    sign_in(page, base_url, SANDRA)
    open_matter(page, base_url, OPEN_TITLE)
    open_next_action_form(page)

    page.locator("#lisa-jargmine [data-quickdate]").filter(has_text=label).first.click()
    assert page.locator("#id_target_date").input_value() == _future(days)


def test_a_typed_date_still_works_and_the_chips_agree_with_it(page, base_url):
    sign_in(page, base_url, SANDRA)
    open_matter(page, base_url, OPEN_TITLE)
    open_next_action_form(page)

    page.locator("#id_target_date").fill(_future(1))
    page.locator("#id_target_date").dispatch_event("change")
    chip = page.locator("#lisa-jargmine [data-quickdate]").filter(has_text="Homme").first
    expect(chip).to_have_attribute("aria-pressed", "true")


# ---------------------------------------------------------------------------
# §6 — a historical WAIT keeps its meaning and never states it
# ---------------------------------------------------------------------------


def test_a_seeded_wait_reads_as_a_sentence_and_a_date(page, base_url):
    """`Ootame ministeeriumi vastust`, stored as a WAIT/REVIEW_ON with a passed
    review date. The page shows the sentence and the date, and none of the
    vocabulary; the zone is not in the overdue state, because waiting is not
    lateness and that domain rule is unchanged."""
    sign_in(page, base_url, SANDRA)
    open_matter(page, base_url, REVIEW_DUE_TITLE)

    zone = page.locator("#praegune-tegevus")
    expect(zone.locator(".curact__text")).to_have_text("Ootame ministeeriumi vastust")
    expect(zone.locator(".curact__date")).to_be_visible()
    expect(zone).not_to_have_class(re.compile("curact--overdue"))
    expect(zone.locator(".curact__date--overdue")).to_have_count(0)

    text = zone.inner_text()
    for retired in RETIRED:
        assert retired not in text, f"the zone still says «{retired}»"

    # And it is finishable from here like anything else — the box is offered,
    # and filling it in is asserted on Matters this file creates rather than
    # here. This file shares one seeded world with the rest of the browser
    # suite, and a test that completed the seeded WAIT would decide what every
    # later reader of this Matter sees.
    expect(zone.get_by_text("Mida tegid?", exact=True)).to_be_visible()


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
    # the date has not been pushed out of the zone that owns it.
    zone = page.locator("#praegune-tegevus")
    expect(zone.locator(".curact__text")).to_be_visible()
    expect(zone.locator(".curact__date")).to_be_visible()
    assert zone.bounding_box()["width"] <= width

    # The completion box, its file control and its save are all reachable at
    # every width, and none of them leaves the viewport (brief §38).
    for control in (
        "#praegune-tegevus .composer__body",
        "#praegune-tegevus .cx-drop",
        "#praegune-tegevus .curact__form button[type=submit]",
    ):
        box = page.locator(control).bounding_box()
        assert box is not None and box["width"] > 0, control
        assert box["x"] + box["width"] <= width + 1, control

    # And an opened `LISA TEEMALE` form uses the width it is given rather than
    # standing in a narrow column of its own.
    open_composer(page)
    box = page.locator("#lisa-marge .composer__body").bounding_box()
    assert box["width"] > 0
    assert box["x"] + box["width"] <= width + 1
