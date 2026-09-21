"""The 2026-09-21 correction round, in the browser that found the defects.

The Python suite proves each rule at the seam that owns it. What only a browser
can show is the half a lawyer meets: that a panel saves where it used to
refuse, that a control is disabled *and* says why, that a fact is on the page as
text rather than in a `title` nobody can reach, and that the keyboard is left
somewhere useful.

Each test files its own Matter. These write Matter-level facts and the seeded
world is shared across a shard, so writing them onto a seeded Matter would be
writing into another file's fixtures.
"""

from __future__ import annotations

import re

import pytest

from e2e.conftest import SANDRA, create_matter, sign_in, unique_title

pytestmark = pytest.mark.e2e


def _patterned(page, base_url: str, prefix: str) -> str:
    """A Matter with a roadmap: an `Õigusakt` and a stage that places it on one.

    `legal_process_rail` returns `None` without both, and the whole section —
    including the `Muuda` panel these tests are about — is then unrendered.
    """
    url = create_matter(
        page, base_url, unique_title(prefix), owner=SANDRA, stage="Kooskõlastusringil"
    )
    page.goto(f"{url}muuda/")
    page.wait_for_load_state("networkidle")
    page.get_by_role("checkbox", name="Seadus", exact=True).check()
    page.get_by_role("button", name="Salvesta").click()
    page.wait_for_url(re.compile(r"/teemad/[0-9a-f-]{36}/$"))
    return url


def _open_feedback(page):
    page.get_by_text("+ Arvamus / tagasiside", exact=True).click()
    page.wait_for_selector("#id_tagasiside_summary")


# ---------------------------------------------------------------------------
# OWNER-01 — the organisation is optional
# ---------------------------------------------------------------------------


def test_feedback_saves_with_no_organisation_at_all(page, base_url):
    """The panel refused until a name was invented; now it does not.

    A lawyer writing down what a member said on the telephone has neither a
    catalogue row nor a collection to name.
    """
    sign_in(page, base_url, SANDRA)
    url = create_matter(page, base_url, unique_title("QA nimetu tagasiside"), owner=SANDRA)

    page.goto(url)
    _open_feedback(page)
    page.fill("#id_tagasiside_summary", "Helistas liige: üleminekuaeg on liiga lühike.")
    page.get_by_role("button", name="Salvesta tagasiside").click()
    page.wait_for_selector("text=Helistas liige")

    assert "Meile saadetud tagasiside" in page.locator("#ajalugu-loend").inner_text()


def test_feedback_saves_with_no_organisation_and_the_member_mark(page, base_url):
    """`Liige` does not depend on naming the member (OWNER-01 scenario B)."""
    sign_in(page, base_url, SANDRA)
    url = create_matter(page, base_url, unique_title("QA liige nimetult"), owner=SANDRA)

    page.goto(url)
    _open_feedback(page)
    page.locator("#id_tagasiside_source_is_member").check()
    page.fill("#id_tagasiside_summary", "Liikme vastuseis, allikas jääb nimetamata.")
    page.get_by_role("button", name="Salvesta tagasiside").click()
    page.wait_for_selector("text=Liikme vastuseis")

    row = page.locator("#ajalugu-loend").inner_text()
    # Stated, and with no empty punctuation where the author would have been.
    assert "Meile saadetud tagasiside · Liige" in row


def test_a_discovered_opinion_still_needs_an_author(page, base_url):
    """The half of the rule that protects the file, kept.

    An unattributed published opinion is an anonymous claim, and the refusal
    for it is unchanged.
    """
    sign_in(page, base_url, SANDRA)
    url = create_matter(page, base_url, unique_title("QA autorita arvamus"), owner=SANDRA)

    page.goto(url)
    page.get_by_text("+ Arvamus / tagasiside", exact=True).click()
    page.get_by_text("Teiste arvamus", exact=True).click()
    page.fill("#id_valine_seisukoht_summary", "Keegi kuskil arvas midagi.")
    page.get_by_role("button", name="Salvesta arvamus").click()

    page.wait_for_selector("text=Vali organisatsioon, kelle seisukoht see on.")


# ---------------------------------------------------------------------------
# QA-014 — `Liige` is shown and can be corrected
# ---------------------------------------------------------------------------


def test_the_member_mark_can_be_taken_off_again(page, base_url):
    """It was saveable and never correctable, so a mistake was permanent."""
    sign_in(page, base_url, SANDRA)
    url = create_matter(page, base_url, unique_title("QA liikme parandus"), owner=SANDRA)

    page.goto(url)
    _open_feedback(page)
    page.locator("#id_tagasiside_source_is_member").check()
    page.fill("#id_tagasiside_summary", "Märgitud liikmeks ekslikult.")
    page.get_by_role("button", name="Salvesta tagasiside").click()
    page.wait_for_selector("text=Märgitud liikmeks ekslikult")
    assert "· Liige" in page.locator("#ajalugu-loend").inner_text()

    row = page.locator("#ajalugu-loend li", has_text="Märgitud liikmeks ekslikult").first
    row.get_by_text("Muuda", exact=True).click()
    page.wait_for_selector("input[name='source_is_member']")
    page.locator("input[name='source_is_member']").uncheck()
    row.get_by_role("button", name="Salvesta").click()
    page.wait_for_timeout(500)

    page.goto(url)
    page.wait_for_load_state("networkidle")
    assert "· Liige" not in page.locator("#ajalugu-loend").inner_text()


# ---------------------------------------------------------------------------
# OWNER-02 — the process link
# ---------------------------------------------------------------------------


def test_the_process_link_is_a_real_link_with_its_control_beside_it(page, base_url):
    """The stored address, opened safely, and `Muuda` where it belongs."""
    sign_in(page, base_url, SANDRA)
    url = create_matter(page, base_url, unique_title("QA menetluse link"), owner=SANDRA)
    address = "https://eelnoud.valitsus.ee/main/mount/docList/qa-1?f=2"

    page.goto(f"{url}muuda/")
    page.wait_for_load_state("networkidle")
    page.fill("input[name='menetlus-url']", address)
    page.fill("input[name='menetlus-label']", "QA eelnõu toimik")
    page.get_by_role("button", name="Salvesta").click()
    page.wait_for_url(re.compile(r"/teemad/[0-9a-f-]{36}/$"))

    card = page.locator("#menetluse-lingid")
    link = card.locator("a").first
    # The stored address, not one rebuilt from the label.
    assert link.get_attribute("href") == address
    assert link.get_attribute("target") == "_blank"
    assert "noopener" in (link.get_attribute("rel") or "")
    assert link.inner_text().startswith("QA eelnõu toimik")

    # `Muuda`, not `Paranda`, and inside the link's own value region.
    assert card.locator(".proclink__value summary.disclosure-chip").inner_text() == "Muuda"
    assert card.get_by_text("Paranda", exact=True).count() == 0


# ---------------------------------------------------------------------------
# QA-005, QA-008, QA-009 — the rail and its editor
# ---------------------------------------------------------------------------


def test_the_rail_draws_one_joustumine_and_says_what_it_is(page, base_url):
    """Two adjacent columns with one name, and the difference in a tooltip."""
    sign_in(page, base_url, SANDRA)
    url = _patterned(page, base_url, "QA joustumine")

    page.goto(url)
    page.get_by_text("+ Märge", exact=True).click()
    page.get_by_text("Jõustumine", exact=True).first.click()
    page.fill("input[name='effective_title']", "QA põhiosa")
    page.fill("input[name='effective_on']", "01.01.2027")
    page.get_by_role("button", name="Salvesta").first.click()
    page.wait_for_timeout(1200)

    page.goto(url)
    page.wait_for_load_state("networkidle")
    rail = page.locator(".lprail")
    nodes = rail.locator(".tl-step__what")
    labels = [nodes.nth(index).inner_text() for index in range(nodes.count())]
    assert labels.count("Jõustumine") == 1
    # As text on the page, not as a `title` a touch screen never delivers.
    assert "QA põhiosa 1.1.2027" in rail.inner_text()


def test_the_current_phase_and_an_anchored_phase_cannot_be_removed(page, base_url):
    """Disabled, and saying which rule it is."""
    sign_in(page, base_url, SANDRA)
    url = _patterned(page, base_url, "QA kaitstud etapid")

    page.goto(url)
    page.get_by_text("+ Märge", exact=True).click()
    page.get_by_text("Jõustumine", exact=True).first.click()
    page.fill("input[name='effective_title']", "QA jõustub")
    page.fill("input[name='effective_on']", "01.01.2027")
    page.get_by_role("button", name="Salvesta").first.click()
    page.wait_for_timeout(1200)

    page.goto(url)
    page.wait_for_load_state("networkidle")
    page.locator(".lprail__edit").click()
    page.wait_for_selector("#menetluse-kulg-muuda form")

    panel = page.locator("#menetluse-kulg-muuda")
    assert panel.locator("input[name='kooskolastus__shown']").is_disabled()
    assert panel.locator("input[name='joustumine__shown']").is_disabled()
    assert "praegune etapp" in panel.inner_text()
    assert "kirjas olev kuupäev" in panel.inner_text()
    # An ordinary future phase is still the lawyer's to remove.
    assert not panel.locator("input[name='riigikogu__shown']").is_disabled()


def test_roadmap_dates_out_of_order_are_refused_with_the_values_kept(page, base_url):
    sign_in(page, base_url, SANDRA)
    url = _patterned(page, base_url, "QA kuupaevade jarjekord")

    page.goto(url)
    page.wait_for_load_state("networkidle")
    page.locator(".lprail__edit").click()
    page.wait_for_selector("#menetluse-kulg-muuda form")
    page.fill("input[name='kooskolastus__date']", "01.12.2026")
    page.fill("input[name='valitsus__date']", "25.09.2026")
    page.locator("#menetluse-kulg-muuda").get_by_role("button", name="Salvesta").click()
    page.wait_for_selector("#menetluse-kulg-muuda .field__error")

    assert (
        "ei saa olla varem"
        in page.locator("#menetluse-kulg-muuda .field__error").first.inner_text()
    )
    # Still in the boxes, so nothing has to be typed again.
    assert page.input_value("input[name='kooskolastus__date']") == "01.12.2026"
    assert page.input_value("input[name='valitsus__date']") == "25.09.2026"


# ---------------------------------------------------------------------------
# QA-018 — the keyboard after a save
# ---------------------------------------------------------------------------


def test_a_save_leaves_the_keyboard_somewhere_useful(page, base_url):
    """An htmx swap destroys the focused element; the browser answers `body`.

    On a file with thirty rows that meant tabbing down from the skip link after
    every single capture.
    """
    sign_in(page, base_url, SANDRA)
    url = create_matter(page, base_url, unique_title("QA fookus"), owner=SANDRA)

    page.goto(url)
    page.get_by_text("+ Märge", exact=True).click()
    page.fill("#id_marge_title", "Ministeerium saatis uue versiooni")
    page.get_by_role("button", name="Salvesta").first.click()
    page.wait_for_selector("text=Ministeerium saatis uue versiooni")

    focused = page.evaluate("() => document.activeElement && document.activeElement.id")
    assert focused == "lisa-teemale"


def test_a_refusal_still_focuses_the_field_that_was_wrong(page, base_url):
    """The behaviour the round must not have traded away."""
    sign_in(page, base_url, SANDRA)
    url = create_matter(page, base_url, unique_title("QA keeldumise fookus"), owner=SANDRA)

    page.goto(url)
    page.get_by_text("+ Märge", exact=True).click()
    page.fill("#id_marge_title", "")
    page.get_by_role("button", name="Salvesta").first.click()
    page.wait_for_selector("text=Kirjuta, mis juhtus.")

    focused = page.evaluate("() => document.activeElement && document.activeElement.id")
    assert focused == "id_marge_title"
