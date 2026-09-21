"""Two tabs, one whole record, in a real browser.

`tests/test_whole_record_concurrency.py` proves the boundary at the service and
at the route. What only a browser can show is the half a person actually meets:
that the refusal is *shown* on the page they are looking at, that the values
they typed are still in the boxes after it, and — the one that would make the
guard worse than useless — that the refused page hands back the current token,
so somebody who has read the other side can press `Salvesta` again and land.

Two pages in one browser context, because this is one signed-in lawyer with two
tabs open on one file, which is how the defect was found.

Each test files its own Matter. These write Matter-level facts — owner, title,
the rail's steps — and the seeded world is shared across a shard, so writing
them onto a seeded Matter would be writing into another file's fixtures.
"""

from __future__ import annotations

import re

import pytest

from e2e.conftest import MARTIN, SANDRA, create_matter, sign_in, unique_title

pytestmark = pytest.mark.e2e

#: The sentences the application refuses with, spelled here rather than
#: imported, for the reason this directory always gives: the browser suite
#: imports no application code, so a test cannot pass by agreeing with the
#: implementation about a string neither of them shows anybody.
MATTER_CONFLICT = "Teemat on vahepeal mujal muudetud."
STEPS_CONFLICT = "Menetluse kulgu on vahepeal mujal muudetud."


def _revision(page) -> str:
    """The token the rendered page is holding, read off the hidden field."""
    return page.locator("form.createform input[name='revision']").input_value()


def test_a_stale_muuda_teemat_is_refused_and_keeps_what_was_typed(page, context, base_url):
    """QA-002 — the case the exploratory round walked into.

    Tab A reassigns the file. Tab B, opened before that and holding the owner as
    it then was, edits only the summary. Before this guard, B's save silently
    put the owner back and wrote the reversal to the audit trail as an ordinary
    reassignment.
    """
    sign_in(page, base_url, SANDRA)
    title = unique_title("QA samaaegne")
    matter_url = create_matter(page, base_url, title, owner=SANDRA)

    tab_a = page
    tab_b = context.new_page()
    tab_a.goto(f"{matter_url}muuda/")
    tab_b.goto(f"{matter_url}muuda/")
    tab_a.wait_for_load_state("networkidle")
    tab_b.wait_for_load_state("networkidle")
    assert _revision(tab_a) == _revision(tab_b) != ""

    # A hands the file to a colleague.
    tab_a.get_by_role("radio", name=MARTIN.short_name, exact=True).check()
    tab_a.get_by_role("button", name="Salvesta").click()
    tab_a.wait_for_url(re.compile(r"/teemad/[0-9a-f-]{36}/$"))
    assert MARTIN.short_name in tab_a.locator("#teema-pais").inner_text()

    # B, still holding the page from before that, writes only a summary.
    typed = "Teisest sakist kirjutatud kokkuvõte."
    tab_b.fill("#id_brief_summary", typed)
    tab_b.get_by_role("button", name="Salvesta").click()
    tab_b.wait_for_selector(".formerror")

    # The refusal is on the page, and so is what B typed.
    assert MATTER_CONFLICT in tab_b.locator(".formerror[role=alert]").inner_text()
    assert tab_b.input_value("#id_brief_summary") == typed
    assert "/muuda/" in tab_b.url

    # And the colleague still owns the file.
    tab_a.goto(matter_url)
    tab_a.wait_for_load_state("networkidle")
    assert MARTIN.short_name in tab_a.locator("#teema-pais").inner_text()


def test_the_refused_page_can_be_saved_again_once_the_person_has_looked(page, context, base_url):
    """A conflict has to be resolvable from the page it happened on.

    A guard that refused forever would be a worse defect than the one it
    replaced: the lawyer's only way out would be to retype into a fresh page.
    """
    sign_in(page, base_url, SANDRA)
    title = unique_title("QA teine katse")
    matter_url = create_matter(page, base_url, title, owner=SANDRA)

    tab_a = page
    tab_b = context.new_page()
    tab_a.goto(f"{matter_url}muuda/")
    tab_b.goto(f"{matter_url}muuda/")
    tab_a.wait_for_load_state("networkidle")
    tab_b.wait_for_load_state("networkidle")
    stale = _revision(tab_b)

    tab_a.get_by_role("radio", name=MARTIN.short_name, exact=True).check()
    tab_a.get_by_role("button", name="Salvesta").click()
    tab_a.wait_for_url(re.compile(r"/teemad/[0-9a-f-]{36}/$"))

    typed = "Teisest sakist, teine katse."
    tab_b.fill("#id_brief_summary", typed)
    tab_b.get_by_role("button", name="Salvesta").click()
    tab_b.wait_for_selector(".formerror")

    # The page came back holding the version the refusal was measured against.
    offered = _revision(tab_b)
    assert offered != stale != ""

    # And it says which field moved and to what, so the person can see the side
    # their own page cannot show them. Without this the only obvious next
    # action is «press Salvesta again», which is the silent revert by hand.
    detail = tab_b.locator(".formerror--detail").inner_text()
    assert "Vastutaja" in detail
    assert MARTIN.short_name in detail
    assert SANDRA.short_name in detail

    # Pressing Salvesta again now lands. It is an overwrite — the form still
    # holds this tab's owner — but an informed one, which is the whole
    # difference between this and the defect.
    tab_b.get_by_role("button", name="Salvesta").click()
    tab_b.wait_for_url(re.compile(r"/teemad/[0-9a-f-]{36}/$"))
    assert typed in tab_b.locator("#teema-pais").inner_text()


def test_a_stale_menetluse_kulg_panel_is_refused(page, context, base_url):
    """QA-004 — the same defect on the rail's own editor.

    The panel posts every phase at once, so a copy of it that never saw
    somebody else's date used to overwrite that date with a blank.
    """
    sign_in(page, base_url, SANDRA)
    title = unique_title("QA kulu sakid")
    matter_url = create_matter(page, base_url, title, owner=SANDRA, stage="Kooskõlastusringil")
    # `Muuda` is drawn only where there is a roadmap to tailor, and a roadmap
    # needs both an `Õigusakt` to choose the pattern and something that places
    # the file on it — `legal_process_rail` returns `None` otherwise, and the
    # whole section with it. The stage comes from `create_matter`; the
    # instrument is set here.
    page.goto(f"{matter_url}muuda/")
    page.wait_for_load_state("networkidle")
    page.get_by_role("checkbox", name="Seadus", exact=True).check()
    page.get_by_role("button", name="Salvesta").click()
    page.wait_for_url(re.compile(r"/teemad/[0-9a-f-]{36}/$"))

    def open_panel(target):
        target.goto(matter_url)
        target.wait_for_load_state("networkidle")
        target.locator(".lprail__edit").click()
        target.wait_for_selector("#menetluse-kulg-muuda form")

    tab_a = page
    tab_b = context.new_page()
    open_panel(tab_a)
    open_panel(tab_b)

    tab_a.fill("input[name='valitsus__date']", "01.10.2026")
    tab_a.locator("#menetluse-kulg-muuda").get_by_role("button", name="Salvesta").click()
    # A saved panel closes itself — the whole Teema view is re-rendered and the
    # panel's slot comes back empty. Waiting for `.lprail` alone would match the
    # section that is already on the page, which is a race rather than a wait.
    tab_a.wait_for_selector("#menetluse-kulg-muuda form", state="detached")
    assert "1.10.2026" in tab_a.locator(".lprail").inner_text()

    tab_b.fill("input[name='riigikogu__date']", "02.10.2026")
    tab_b.locator("#menetluse-kulg-muuda").get_by_role("button", name="Salvesta").click()
    tab_b.wait_for_selector("#menetluse-kulg-muuda .formerror")

    assert STEPS_CONFLICT in tab_b.locator("#menetluse-kulg-muuda .formerror").inner_text()
    # What B typed is still in the panel, so the two can be reconciled by eye.
    assert tab_b.input_value("input[name='riigikogu__date']") == "02.10.2026"

    # And A's date survived, which is the whole point.
    tab_a.goto(matter_url)
    tab_a.wait_for_load_state("networkidle")
    rail = tab_a.locator(".lprail").inner_text()
    assert "1.10.2026" in rail
    assert "2.10.2026" not in rail
