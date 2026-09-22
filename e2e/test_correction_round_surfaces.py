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


def _record_commencement(page, description: str, day: str) -> None:
    """`+ Märge → Jõustumine`, through the launcher's own radio.

    The subtype is a `.addpick` radio with a `<label class="disclosure-chip">`,
    not a link and not a button. The label is what is clicked, because the radio
    itself is visually hidden — the ring is painted on the label from the
    radio's own focus — and because clicking the label is what a person does
    (`add_to_matter.html`).

    A `get_by_text("Jõustumine")` resolves to the `<option>` in the phase select
    instead, which is not visible and never becomes so.
    """
    page.get_by_text("+ Märge", exact=True).click()
    page.locator('label[for="marge-joustumine-valik"]').click()
    page.fill("input[name='effective_title']", description)
    page.fill("input[name='effective_on']", day)
    page.locator("#marge-joustumine button[type=submit]").first.click()
    page.wait_for_timeout(1200)


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

    row = page.locator("#ajalugu-loend .uxtl__item", has_text="Märgitud liikmeks ekslikult").first
    row.get_by_text("Muuda", exact=True).click()
    # Scoped to the row: the capture panel renders its own `source_is_member`
    # on the same page, and that one is hidden inside a closed disclosure.
    mark = row.locator("input[name='source_is_member']")
    mark.wait_for(state="attached")
    mark.uncheck(force=True)
    row.get_by_role("button", name="Salvesta").click()
    page.wait_for_timeout(500)

    page.goto(url)
    page.wait_for_load_state("networkidle")
    assert "· Liige" not in page.locator("#ajalugu-loend").inner_text()


# ---------------------------------------------------------------------------
# OWNER-02 — the process link
# ---------------------------------------------------------------------------


def test_the_process_link_is_a_real_link_with_its_control_beside_it(page, base_url):
    """The stored address, opened safely, and `Muuda` where it belongs.

    **Half of OWNER-02 was disproved before it was fixed.** The finding
    reported the link as both broken and badly placed. Reproduced on
    `a46fa5a` — this exact scenario, typed into that revision — the address
    stored and the anchor opened it: `procedural_links.html` already rendered
    `<a href="{{ link.url }}">`, and no step of this test failed on the link
    itself. What did fail was the placement: the control sat outside the
    link's own value region and read `Paranda`.

    So this test is written to assert both halves at once. The `href`
    assertions are the *regression guard on a thing that already worked*, kept
    because a finding that names a defect which is not there is exactly how a
    working behaviour gets rewritten by somebody fixing the sentence rather
    than the code; the two below them are the correction.
    """
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
    _record_commencement(page, "QA põhiosa", "01.01.2027")

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
    _record_commencement(page, "QA jõustub", "01.01.2027")

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
    """The behaviour the round must not have traded away.

    The refusal this used was «Kirjuta, mis juhtus.» on an empty `Mis juhtus?`,
    which docs/adr/0105 §4 retired; the one it used next was «Vali järgmise
    tegevuse kuupäev.» on an empty day, which docs/adr/0106 retired in turn — a
    step with no day is an ordinary save now. What is left, and is the honest
    field-scoped refusal, is the other direction: a day with nothing to do on it,
    pinned to the sentence somebody did *not* write.
    """
    sign_in(page, base_url, SANDRA)
    url = create_matter(page, base_url, unique_title("QA keeldumise fookus"), owner=SANDRA)

    page.goto(url)
    page.get_by_text("+ Märge", exact=True).click()
    page.fill("#id_marge_title", "Ministeerium saatis uue versiooni")
    page.fill("#id_marge_next_date", "30.09.2026")
    page.get_by_role("button", name="Salvesta").first.click()
    page.wait_for_selector("text=Kirjuta järgmine tegevus.")

    focused = page.evaluate("() => document.activeElement && document.activeElement.id")
    assert focused == "id_marge_next_text"


def test_a_panel_level_refusal_focuses_the_sentence_that_names_it(page, base_url):
    """docs/adr/0105 §4's refusal, and the focus rule it lands on.

    A `Märge` with no sentence, no file, no stage and no step names no box, so
    `focusFirstRefusal` takes the summary itself rather than guessing a field —
    putting the cursor in `Mis juhtus?` would say the sentence is the missing
    answer when any of four would do (static/js/ux.js).
    """
    sign_in(page, base_url, SANDRA)
    url = create_matter(page, base_url, unique_title("QA tühi märge"), owner=SANDRA)

    page.goto(url)
    page.get_by_text("+ Märge", exact=True).click()
    page.fill("#id_marge_title", "")
    page.get_by_role("button", name="Salvesta").first.click()
    page.wait_for_selector("text=või lisa fail, uus hetkeseis või järgmine tegevus")

    focused = page.evaluate("() => document.activeElement && document.activeElement.className")
    assert "formerror" in (focused or ""), focused


# ---------------------------------------------------------------------------
# OWNER-04 — a mistaken record comes off the file
# ---------------------------------------------------------------------------


def test_a_mistaken_marge_can_be_taken_off_the_file(page, base_url):
    """The act that did not exist: `Muuda` was the only repair for a wrong file."""
    sign_in(page, base_url, SANDRA)
    url = create_matter(page, base_url, unique_title("QA eemaldamine"), owner=SANDRA)

    page.goto(url)
    page.get_by_text("+ Märge", exact=True).click()
    page.fill("#id_marge_title", "Vale teema peale kirjutatud märge")
    page.get_by_role("button", name="Salvesta").first.click()
    page.wait_for_selector("text=Vale teema peale kirjutatud märge")

    row = page.locator(
        "#ajalugu-loend .uxtl__item", has_text="Vale teema peale kirjutatud märge"
    ).first
    row.get_by_text("Kustuta", exact=True).click()
    # The confirmation names what is going and offers a way out.
    page.wait_for_selector("text=Eemaldan selle märke teema käigust")
    row.get_by_role("button", name="Eemalda", exact=True).click()
    page.wait_for_timeout(800)

    page.goto(url)
    page.wait_for_load_state("networkidle")
    assert "Vale teema peale kirjutatud märge" not in page.locator("#ajalugu-loend").inner_text()


def test_the_confirmation_can_be_left_without_removing_anything(page, base_url):
    """`Loobu` closes the disclosure and puts focus back on the chip."""
    sign_in(page, base_url, SANDRA)
    url = create_matter(page, base_url, unique_title("QA loobumine"), owner=SANDRA)

    page.goto(url)
    page.get_by_text("+ Märge", exact=True).click()
    page.fill("#id_marge_title", "See märge jääb alles")
    page.get_by_role("button", name="Salvesta").first.click()
    page.wait_for_selector("text=See märge jääb alles")

    row = page.locator("#ajalugu-loend .uxtl__item", has_text="See märge jääb alles").first
    row.get_by_text("Kustuta", exact=True).click()
    page.wait_for_selector("text=Eemaldan selle märke teema käigust")
    row.get_by_role("button", name="Loobu", exact=True).click()
    page.wait_for_timeout(300)

    assert "See märge jääb alles" in page.locator("#ajalugu-loend").inner_text()
    focused = page.evaluate("() => document.activeElement && document.activeElement.textContent")
    assert "Kustuta" in (focused or "")


def test_the_removal_is_still_in_the_change_log(page, base_url):
    """«Take this off the active file», never «erase that it ever existed»."""
    sign_in(page, base_url, SANDRA)
    url = create_matter(page, base_url, unique_title("QA logi"), owner=SANDRA)

    page.goto(url)
    page.get_by_text("+ Märge", exact=True).click()
    page.fill("#id_marge_title", "Eemaldatav märge logis")
    page.get_by_role("button", name="Salvesta").first.click()
    page.wait_for_selector("text=Eemaldatav märge logis")

    row = page.locator("#ajalugu-loend .uxtl__item", has_text="Eemaldatav märge logis").first
    row.get_by_text("Kustuta", exact=True).click()
    row.get_by_role("button", name="Eemalda", exact=True).click()
    page.wait_for_timeout(800)

    page.goto(f"{url}muudatused/")
    page.wait_for_load_state("networkidle")
    log = page.locator("body").inner_text()
    assert "Märge eemaldatud" in log
    assert "Märge lisatud" in log


# ---------------------------------------------------------------------------
# QA-023 — the Chamber's own opinion is correctable
# ---------------------------------------------------------------------------


def _registered_send(page, base_url: str, prefix: str) -> str:
    """A Matter with one recorded `Koja arvamus`, built the way a lawyer does.

    An opinion file is uploaded and then *registered* as sent, which is what
    puts `Arvamus välja` on the chronology — a draft is not on it, by design
    (`Submission.historically_sent`, docs/adr/0092 §3).
    """
    url = create_matter(page, base_url, unique_title(prefix), owner=SANDRA)

    page.goto(f"{url}dokumendid/")
    page.wait_for_load_state("networkidle")
    page.locator('[data-reveals="lae-dokument"]').first.click()
    page.locator("#lae-dokument select[name=role]").first.wait_for(state="visible")
    page.locator("#lae-dokument input[type=file][name=upload]").first.set_input_files(
        {
            "name": "Koja-arvamus.pdf",
            "mimeType": "application/pdf",
            "buffer": b"%PDF-1.4 arvamus",
        }
    )
    page.locator("#lae-dokument select[name=role]").first.select_option("KODA_SUBMISSION_FINAL")
    page.locator("#lae-dokument button[type=submit]").first.click()
    page.wait_for_load_state("networkidle")

    accordion = page.locator("details.accordion--opinions").first
    if not accordion.evaluate("node => node.open"):
        accordion.locator("summary").first.click()
        page.wait_for_timeout(200)
    page.locator("summary.disclosure__summary").filter(
        has_text="+ Registreeri saatmine"
    ).first.click()
    page.wait_for_selector("#id_saadetud-sent_on")

    page.fill("#id_saadetud-title", "Koja arvamus eelnõule")
    page.fill("#id_saadetud-sent_on", "14.05.2026")
    page.locator("#id_saadetud-recipients").select_option(index=0)
    page.locator("form:has(#id_saadetud-sent_on) button[type=submit]").first.click()
    page.wait_for_load_state("networkidle")
    return url


def test_the_koja_arvamus_row_can_be_corrected_like_every_other(page, base_url):
    """The one chronology row that had no `Muuda` at all (QA-023).

    A wrong send date reaches the outbound register, the process rail and every
    report that counts advocacy, and the only repair was to ask an
    administrator.
    """
    sign_in(page, base_url, SANDRA)
    url = _registered_send(page, base_url, "QA arvamuse parandus")

    page.goto(url)
    page.wait_for_load_state("networkidle")
    row = page.locator("#ajalugu-loend .uxtl__item", has_text="Arvamus välja").first
    assert row.count(), "the chronology holds no recorded send to correct"

    row.get_by_text("Muuda", exact=True).click()
    # Scoped to the row: `+ Koja arvamus` in the launcher renders its own
    # `sent_on` on the same page, so a page-wide selector resolves to two.
    box = row.locator("input[name='sent_on']")
    box.wait_for(state="visible")
    box.fill("15.05.2026")
    row.locator("textarea[name='summary']").fill("Toetame eelnõu pikema üleminekuajaga.")
    row.get_by_role("button", name="Salvesta", exact=True).click()
    page.wait_for_timeout(600)

    page.goto(url)
    page.wait_for_load_state("networkidle")
    chronology = page.locator("#ajalugu-loend").inner_text()
    assert "15.5.2026" in chronology
    assert "Toetame eelnõu pikema üleminekuajaga." in chronology


def test_a_recorded_send_offers_no_kustuta(page, base_url):
    """The one user-created row without one, and it is argued rather than omitted.

    A sent opinion is a letter that left this office; taking it off the file
    would be the record claiming it never went (docs/adr/0102 §4).
    """
    sign_in(page, base_url, SANDRA)
    url = _registered_send(page, base_url, "QA arvamust ei kustuta")

    page.goto(url)
    page.wait_for_load_state("networkidle")
    row = page.locator("#ajalugu-loend .uxtl__item", has_text="Arvamus välja").first

    assert row.get_by_text("Muuda", exact=True).count() == 1
    assert row.get_by_text("Kustuta", exact=True).count() == 0


def test_a_new_organisation_typed_into_the_picker_becomes_a_real_one(page, base_url):
    """OWNER-01's other half: the `+` beside the search box has to work.

    Typing is not creating — the box posts nothing and only `+` writes the
    typed name into the hidden field the save reads — so this presses it, saves,
    reloads, and then looks for the body in the picker on a *second* Matter.
    A name that only round-trips on the page it was typed on would pass a
    shallower test and still leave nothing in the catalogue
    (docs/adr/0073, `resolve_organisation_name`).
    """
    sign_in(page, base_url, SANDRA)
    name = unique_title("QA Liit")
    url = create_matter(page, base_url, unique_title("QA uus organisatsioon"), owner=SANDRA)

    page.goto(url)
    _open_feedback(page)
    picker = page.locator("[data-orgfind]").first
    picker.locator("[data-orgfind-input]").fill(name)
    picker.locator("[data-orgfind-add]").click()
    page.fill("#id_tagasiside_summary", "Uue liidu seisukoht.")
    page.get_by_role("button", name="Salvesta tagasiside").click()
    page.wait_for_selector("text=Uue liidu seisukoht")

    page.goto(url)
    page.wait_for_load_state("networkidle")
    assert name in page.locator("#ajalugu-loend").inner_text()

    # And the catalogue holds it: a second Matter's picker finds it by name.
    second = create_matter(page, base_url, unique_title("QA teine teema"), owner=SANDRA)
    page.goto(second)
    _open_feedback(page)
    picker = page.locator("[data-orgfind]").first
    picker.locator("[data-orgfind-input]").fill(name)
    page.wait_for_timeout(400)
    assert name in picker.inner_text()


# ---------------------------------------------------------------------------
# The owner's round, re-verified in the laid-out page
# ---------------------------------------------------------------------------


def _boxes(locator_a, locator_b) -> tuple[dict, dict]:
    """Two bounding boxes, or a failure that says which control was missing."""
    first = locator_a.bounding_box()
    second = locator_b.bounding_box()
    assert first is not None and second is not None, "a control under test is not laid out"
    return first, second


def _on_one_line(first: dict, second: dict) -> bool:
    """Whether two inline controls share a line, allowing for differing heights.

    Their tops differ by a few pixels even when they are on one line — a 24px
    chip beside a 16px anchor is centred against it — so the test is vertical
    overlap rather than equal `y`.
    """
    return (
        first["y"] < second["y"] + second["height"] and second["y"] < first["y"] + first["height"]
    )


def test_muuda_sits_on_the_line_of_the_address_it_edits(page, base_url):
    """OWNER-02's second half, measured rather than inferred from the markup.

    `procedural_links.html` puts the anchor and the disclosure in one value
    region, which is necessary and was not sufficient: the rail's value cell is
    155px, and a bordered chip measuring 56px beside an unlabelled link's parsed
    host measuring 104px wrapped onto a line of its own. That is the placement
    the owner reported, produced by the fix for it.

    **A link with no label**, because that is the shape that overflows — a short
    label such as «EIS 26-0994» fits either way and would pass this test against
    the defect.
    """
    sign_in(page, base_url, SANDRA)
    url = create_matter(page, base_url, unique_title("QA lingi rida"), owner=SANDRA)
    address = "https://eelnoud.valitsus.ee/main/mount/docList/qa-2?activity=1"

    page.goto(f"{url}muuda/")
    page.wait_for_load_state("networkidle")
    page.fill("input[name='menetlus-url']", address)
    page.get_by_role("button", name="Salvesta").click()
    page.wait_for_url(re.compile(r"/teemad/[0-9a-f-]{36}/$"))

    card = page.locator("#menetluse-lingid")
    link = card.locator(".proclink__value > a").first
    control = card.locator(".proclink__fix > summary").first
    assert link.inner_text().startswith("eelnoud.valitsus.ee")

    link_box, control_box = _boxes(link, control)
    assert _on_one_line(link_box, control_box), (
        f"Muuda wrapped below the link: link {link_box}, control {control_box}"
    )
    assert control_box["x"] >= link_box["x"] + link_box["width"] - 1


def test_kustuta_sits_beside_muuda_in_the_chronology(page, base_url):
    """OWNER-04's placement, which the markup alone cannot show.

    `Kustuta` is a `<details>`, and the HTML tree builder closes an open `p` at
    a `<details>` start tag. Written into `<p class="uxtl__editactions">` it
    left the flex row entirely — a sibling of the paragraph, on a line of its
    own under `Muuda`, with an empty paragraph behind it. The template read
    correct and the response body carried exactly what was written; only the
    laid-out page disagreed, which is why this is measured here and guarded on
    the source by `tests/test_ui_contract.py`.
    """
    sign_in(page, base_url, SANDRA)
    url = create_matter(page, base_url, unique_title("QA nuppude rida"), owner=SANDRA)

    page.goto(url)
    page.get_by_text("+ Märge", exact=True).click()
    page.fill("#id_marge_title", "Märge, mille nupud peavad ühel real olema")
    page.get_by_role("button", name="Salvesta").first.click()
    page.wait_for_selector("text=Märge, mille nupud peavad")

    row = page.locator("#ajalugu-loend .uxtl__item", has_text="Märge, mille nupud peavad").first
    actions = row.locator(".uxtl__editactions").first
    # The disclosure is a child of the row of controls, not its sibling.
    assert actions.locator("> details.uxtl__remove").count() == 1

    muuda_box, kustuta_box = _boxes(
        actions.get_by_role("button", name=re.compile("^Muuda")).first,
        actions.locator("details.uxtl__remove > summary").first,
    )
    assert _on_one_line(muuda_box, kustuta_box), (
        f"Kustuta wrapped below Muuda: Muuda {muuda_box}, Kustuta {kustuta_box}"
    )
    assert kustuta_box["x"] > muuda_box["x"]


def test_a_correction_opened_and_saved_keeps_the_member_mark(page, base_url):
    """OWNER-01's «correctable» half, through `Muuda` and a plain `Salvesta`.

    The editor writes the whole record back, so a box it opens empty is saved
    empty. `Liige` was rendered on the correction form and not opened on the
    record: a lawyer fixing a typo in the summary took the mark off without
    touching it, and the row lost its «· Liige» with no message anywhere.
    """
    sign_in(page, base_url, SANDRA)
    url = create_matter(page, base_url, unique_title("QA liikme parandus"), owner=SANDRA)

    page.goto(url)
    _open_feedback(page)
    page.locator("#id_tagasiside_source_is_member").check()
    page.fill("#id_tagasiside_summary", "Liikme tagasiside, mis vajab pisiparandust.")
    page.get_by_role("button", name="Salvesta tagasiside").click()
    page.wait_for_selector("text=Liikme tagasiside, mis vajab")

    row = page.locator("#ajalugu-loend .uxtl__item", has_text="Liikme tagasiside, mis vajab").first
    assert "· Liige" in row.inner_text()

    row.get_by_role("button", name=re.compile("^Muuda")).first.click()
    # Scoped to the row: the `+ Arvamus / tagasiside` launcher further up the
    # page holds a `summary` box of its own, permanently in the document and
    # hidden by its radio, so an unscoped wait resolves to that one and never
    # sees this form arrive.
    editor = row.locator("form.uxtl__editform")
    editor.locator("textarea[name='summary']").wait_for(state="visible")
    assert editor.locator("input[name='source_is_member']").is_checked()
    editor.get_by_role("button", name="Salvesta", exact=True).first.click()
    page.wait_for_timeout(1000)

    page.goto(url)
    page.wait_for_load_state("networkidle")
    assert (
        "· Liige"
        in page.locator(
            "#ajalugu-loend .uxtl__item", has_text="Liikme tagasiside, mis vajab"
        ).first.inner_text()
    )
