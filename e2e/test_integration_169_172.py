"""What only the four branches *together* can be wrong about.

`#169`, `#170`, `#171` and `#172` were each developed and reviewed against
`main`, so no suite in the repository has ever had more than one of them
loaded at a time. Each is proved on its own — the sender default in
`e2e/test_counterparty_selection.py`, the Õigusakt row in
`e2e/test_oigusakt_row.py`, the discovery filters in
`e2e/test_matter_intelligence.py`, the reader in
`e2e/test_uus_teema_reading.py`. This file asserts only the seams *between*
them, and nothing that any of those four already covers.

Three seams, and the first is the one worth breaking a build over:

* **the reader answers Adressaat.** `#172` ticks Saatja from what it read in a
  file; `#169` turns a single unambiguous Saatja into the Adressaat. Neither
  branch knew the other existed, and the join between them is one synthetic
  `change` event — `fill` in `static/js/app.js` dispatches it, and
  `bindAddresseeDefault`'s handler deliberately does **not** require
  `isTrusted` on `source_organisations`. That asymmetry is load-bearing and
  invisible: the same handler *does* require `isTrusted` on
  `addressee_organisation`, which is what keeps a machine from ever being
  recorded as having made somebody's choice for them.

* **a person still outranks the reader.** `#169`'s manual-override rule is the
  one property a later suggestion could quietly defeat, because a suggestion
  arrives seconds after somebody has answered and re-ticks the very control the
  default is derived from.

* **a body typed on `Uus teema` is findable in Teemad.** `#169` resolves one
  typed spelling into one `Organisation` used for both counterparties; `#171`
  made all three institution filters read one catalogue through one searchable
  control. Composed, they are a lifecycle: a body that did not exist when the
  form was opened is filterable, in both directions, minutes later.

The harnesses are imported rather than rebuilt — the reader's worker drain and
the addressee's chip readers are those files' own, and a second copy of either
would be a second thing to keep true.
"""

from __future__ import annotations

import re
import sys
import uuid
from pathlib import Path

import pytest
from playwright.sync_api import expect

from e2e.conftest import MARTIN, sign_in
from e2e.test_counterparty_selection import (
    ADDRESSEE_DISCLOSURE,
    ADDRESSEE_QUICK,
    MINISTRY,
    _addressee_labels,
    _chosen_addressees,
    _summary,
    open_addressee,
)
from e2e.test_uus_teema_reading import (
    LETTER,
    REPOSITORY_ROOT,
    choose,
    open_create,
    read_staged_files,
    wait_for_reading,
    wait_for_suggestions,
)

pytestmark = pytest.mark.e2e


@pytest.fixture
def letter_pdf(tmp_path: Path) -> Path:
    """The same ministry letter the reader suite uses, built here.

    A fixture is not importable across modules the way a helper is, so this one
    is redefined — but over the imported `LETTER`, so the *text* the reader has
    to recognise stays in one place. What matters for this file is only that the
    letter names «Näidisministeerium» as its sender, which is the body
    `e2e/test_counterparty_selection.py` promotes as `MINISTRY`: the two suites
    happen to be about the same institution, and that is what makes the seam
    testable at all.
    """
    sys.path.insert(0, str(REPOSITORY_ROOT))
    from tests.synthetic_corpus import text_pdf

    path = tmp_path / "kaaskiri.pdf"
    path.write_bytes(text_pdf([LETTER]))
    return path


@pytest.fixture
def second_letter_pdf(tmp_path: Path) -> Path:
    """A second file, so a *second* suggestion round can be provoked.

    The override property is not «a suggestion does not overwrite an answer
    given before it arrived» — the reader suite proves that. It is «a suggestion
    arriving *after* somebody has answered does not overwrite it either», and
    provoking one needs a genuinely new file rather than a re-poll of a queue
    that has already been drained.
    """
    sys.path.insert(0, str(REPOSITORY_ROOT))
    from tests.synthetic_corpus import text_pdf

    path = tmp_path / "teine-kaaskiri.pdf"
    path.write_bytes(text_pdf([LETTER.replace("kooskõlastamiseks", "arvamuse avaldamiseks")]))
    return path


def a_new_name() -> str:
    """A body the catalogue provably does not hold yet.

    Unique per call for the reason `e2e/test_counterparty_selection.py` gives:
    these tests *create* institutions and the browser suite shares one database,
    so a fixed name is in the catalogue from the moment this file has run once —
    and «a body that did not exist when the form was opened» would then be a
    claim about the previous run.
    """
    return f"Näidisamet {uuid.uuid4().hex[:8]}"


def sender_checked_names(page) -> list[str]:
    """Every institution currently ticked as a Saatja, by name."""
    return page.locator("fieldset.senderpick label.chip").evaluate_all(
        "nodes => nodes"
        ".filter(node => { const i = node.querySelector('input'); return i && i.checked; })"
        ".map(node => { const n = node.querySelector('.chip__name');"
        " return (n ? n.textContent : '').replace(/\\s*×$/, '').trim(); })"
    )


def disagree_with_the_default(page) -> str:
    """Answer Adressaat by hand with some body that is not the ministry.

    Not `e2e/test_counterparty_selection.py::_pick_some_addressee`, and the
    difference is the reason this helper exists rather than an import. That one
    reads the **shortlist** only, which is correct for its own file: it never
    has a file being read beside it, so the row it looks at is the one the
    seeded catalogue produces. Here the ministry has already been promoted into
    that shortlist by the reader, and on a database where the catalogue is small
    the shortlist can then hold nothing else at all — everything remaining is
    behind «Vali nimekirjast». A picker that only read the quick row would pass
    on a used database and fail on a fresh one, which is the worst possible
    scheduling for a test of an override rule.

    So it looks where a person looks: the row first, then the list behind the
    door. Returns the name, which is what the summary is asserted against.
    """
    open_addressee(page)
    tail = page.locator(f"{ADDRESSEE_QUICK} details.chipdetails")
    if tail.count() and not tail.evaluate("node => node.open"):
        tail.locator("> summary").click()

    # Read the whole control in one evaluation — the chips in the row and the
    # chips behind the door are the same radio group, and which side of the
    # split a body lands on is a property of the seeded catalogue rather than
    # of anything under test.
    candidates = page.locator(f"{ADDRESSEE_QUICK} label.chip").evaluate_all(
        "nodes => nodes"
        ".filter(node => !node.hasAttribute('data-provisional-addressee'))"
        ".map(node => { const input = node.querySelector('input');"
        " const named = node.querySelector('.chip__name');"
        " return {value: input ? input.value : '',"
        " name: ((named ? named.textContent : node.textContent) || '')"
        ".replace(/\\s*×$/, '').trim()}; })"
    )
    chosen = next(
        (
            item
            for item in candidates
            # «Määramata» is a real radio in this group with an empty value, and
            # choosing it is choosing *no answer* — asserting that no answer
            # survived would assert nothing. The ministry is the body the reader
            # promoted, and choosing it would make «the default was written» and
            # «the answer survived» the same assertion.
            if item["value"].strip() and item["name"] != MINISTRY
        ),
        None,
    )
    assert chosen, f"the Adressaat control offered no second body to disagree with: {candidates!r}"

    # Bound to the *value*, never to the position it was found at. Promotion
    # relocates the chosen body's option to the front of this row, so an
    # `nth(i)` locator starts pointing at a different control the moment the
    # thing under test does its job — and the tail it may point into is closed
    # (`e2e/test_counterparty_selection.py::_pick_some_addressee`).
    page.locator(f'input[name="addressee_organisation"][value="{chosen["value"]}"]').click()
    return chosen["name"]


def open_advanced(page):
    """Open Täpsem otsing on the register and return the panel."""
    panel = page.locator("#tapsem-otsing")
    if not panel.evaluate("node => node.open"):
        panel.locator("summary.filterpanel__trigger").click()
    return panel


def search_chooser(page, field: str, term: str) -> None:
    """Type into one institution control and let HTMX swap its options back.

    The control is a search box that re-renders its own `<select>` from the
    server, so «is this body offered» is a question about what comes back — not
    about what the first unsearched page happened to contain. That distinction
    is the whole of the reported defect `#171` fixed, and a test that read the
    select without searching would not be asking about it.

    Waits on the *node*, not on a duration. `hx-swap="outerHTML"` replaces the
    whole fieldset, so the element held here detaches the moment the answer
    lands — which is independent of the control's own 250ms debounce and of how
    long the server takes. Sleeping instead would be a flake with a schedule.
    """
    fieldset = page.locator(f"#orgchooser-{field}").element_handle()
    page.locator(f"#orgchooser-{field} input[name='{field}_otsing']").fill(term)
    page.wait_for_function("node => !node.isConnected", arg=fieldset, timeout=10_000)


def chooser_option_names(page, field: str) -> list[str]:
    return [
        (text or "").strip()
        for text in page.locator(
            f"#orgchooser-{field} select[name='{field}'] option"
        ).all_text_contents()
    ]


# ---------------------------------------------------------------------------
# Seam 1 — the reader answers Saatja, and #169 answers Adressaat
# ---------------------------------------------------------------------------


def test_a_sender_the_reader_found_becomes_the_default_addressee(
    page, base_url, screenshots, letter_pdf
) -> None:
    """The composed workflow, end to end, exactly as a lawyer meets it.

    The letter names a ministry. `#172` reads it and ticks that ministry in the
    real Saatja control; `#169` must then answer Adressaat with the *same*
    body — and must do so without unfolding the disclosure, because a page that
    opened a section because it had answered a question itself would be
    reacting to its own writing, and it makes no difference to that argument
    whether the answer came from a person or from a file.

    Asserted on the summary as well as on the radio, because the summary is
    what somebody actually sees while the section stays shut: a value set and
    not announced would be the page answering a question silently.
    """
    sign_in(page, base_url, MARTIN)
    open_create(page, base_url)

    # Nothing is answered before the file is read — otherwise the assertion
    # below would be about the seeded world rather than about the reader.
    assert _chosen_addressees(page) == 0, "something was already answered on arrival"
    assert _summary(page) == "Adressaat"

    choose(page, [letter_pdf])
    wait_for_reading(page)
    read_staged_files()
    wait_for_suggestions(page)

    # -- 1. the reader ticked the sender, in the real control --------------
    assert sender_checked_names(page) == [MINISTRY], (
        f"the reader did not leave exactly {MINISTRY!r} ticked as Saatja: "
        f"{sender_checked_names(page)!r}"
    )

    # -- 2. and #169 answered Adressaat with the same body -----------------
    # The join is a synthetic `change` event. If `bindAddresseeDefault` ever
    # grows an `isTrusted` guard on `source_organisations` — as it correctly
    # has on `addressee_organisation` — this is the assertion that fails.
    assert _chosen_addressees(page) == 1, (
        "the sender the reader ticked did not become the addressee"
    )
    assert _addressee_labels(page)[0] == MINISTRY
    assert _summary(page) == f"Adressaat · {MINISTRY}"

    # -- 3. still folded away ----------------------------------------------
    assert not page.locator(ADDRESSEE_DISCLOSURE).evaluate("node => node.open"), (
        "a reader-derived default unfolded the Adressaat disclosure"
    )
    screenshots(page, "42-uus-teema-loetud-saatja-vastab-adressaadile")

    # -- 4. and it saves as one body used twice ----------------------------
    title = f"Pakendiseadus, loetud ja adresseeritud {uuid.uuid4().hex[:8]}"
    page.locator("#id_title").fill(title)
    page.fill("#id_next-text", "Lugeda eelnõu ja koostada arvamus")
    page.locator("#jargmine-tegevus").get_by_role("button", name="+1 nädal").click()
    page.get_by_role("button", name="Loo teema").click()
    page.wait_for_load_state("domcontentloaded")

    complaints = page.locator(".field__error, .formerror, .message--error").all_inner_texts()
    assert not complaints, f"the form refused: {complaints}"
    page.wait_for_url(re.compile(r"/teemad/[0-9a-f-]{36}/$"))

    # Read back off the saved Matter, not off the form that posted it. The
    # ministry has to appear as both counterparties, which is the whole claim.
    body = page.locator("main").inner_text()
    assert body.count(MINISTRY) >= 2, (
        f"the saved Teema does not carry {MINISTRY!r} as both Saatja and Adressaat:\n{body}"
    )


def test_an_addressee_chosen_by_hand_survives_a_later_suggestion(
    page, base_url, letter_pdf, second_letter_pdf
) -> None:
    """The reader may never defeat `#169`'s manual-override rule.

    The dangerous ordering is the one that cannot happen in either branch's own
    suite: a default arrives from a file, a person disagrees with it, and *then*
    a further suggestion lands re-ticking the sender the default came from. In
    `static/js/app.js` the person's click sets `manual` — through the
    `isTrusted` branch — and `refresh()` returns early for ever afterwards. If
    that guard is lost, the second reading round silently restores the
    ministry and somebody's correction disappears between two page loads.
    """
    sign_in(page, base_url, MARTIN)
    open_create(page, base_url)

    choose(page, [letter_pdf])
    wait_for_reading(page)
    read_staged_files()
    wait_for_suggestions(page)
    assert _summary(page) == f"Adressaat · {MINISTRY}"

    # -- the person disagrees ----------------------------------------------
    chosen_name = disagree_with_the_default(page)
    assert _summary(page) == f"Adressaat · {chosen_name}"

    # -- and a second file is read, re-proposing the ministry --------------
    choose(page, [letter_pdf, second_letter_pdf])
    wait_for_reading(page)
    read_staged_files()
    wait_for_suggestions(page)

    # The sender is still the ministry — the reader is entitled to that, since
    # nobody has touched Saatja. The addressee is not, and that is the point.
    assert MINISTRY in sender_checked_names(page), (
        "the second reading round did not re-propose the sender, so this test "
        "no longer provokes the ordering it exists for"
    )
    assert _summary(page) == f"Adressaat · {chosen_name}", (
        "a later suggestion overwrote an addressee somebody had chosen by hand"
    )
    assert _chosen_addressees(page) == 1


# ---------------------------------------------------------------------------
# Seam 2 — Õigusakt is a person's answer, and the reader never touches it
# ---------------------------------------------------------------------------


def test_the_reader_leaves_a_chosen_oigusakt_alone(page, base_url, letter_pdf) -> None:
    """`#172` was developed without `#170`, and must stay that way here.

    Teaching the reader to infer an instrument type is a later focused feature.
    For this release `Õigusakt` is an ordinary user-selected canonical field,
    and the property to hold is negative: a reading round must not clear, alter,
    overwrite or re-render away a selection somebody has already made.

    Chosen *before* the suggestions arrive on purpose. The reader's `FILLABLE`
    list does not name `legal_instruments`, so nothing should reach the control
    — but «nothing reaches it» and «what reaches it is refused» fail
    differently, and only the first is the decision `#172` actually made.
    """
    sign_in(page, base_url, MARTIN)
    open_create(page, base_url)

    choose(page, [letter_pdf])
    wait_for_reading(page)

    instruments = page.locator("fieldset", has=page.locator("#id_legal_instruments_0")).first
    expect(instruments).to_be_visible()
    seadus = instruments.locator("label.chip", has_text="Seadus").locator("input").first
    seadus.check()
    expect(seadus).to_be_checked()

    read_staged_files()
    wait_for_suggestions(page)

    # Untouched by the round that just filled four other controls.
    expect(seadus).to_be_checked()
    checked = instruments.locator("input:checked")
    expect(checked).to_have_count(1)

    # And the reader is demonstrably active on this very form, so the assertion
    # above is about restraint rather than about a suggestion that never came.
    expect(page.locator("#id_response_deadline")).to_have_value("18.9.2026")


# ---------------------------------------------------------------------------
# Seam 3 — a body typed on Uus teema is findable in Teemad
# ---------------------------------------------------------------------------


def test_a_body_typed_as_saatja_is_immediately_filterable_in_teemad(page, base_url) -> None:
    """One catalogue, proved as a lifecycle rather than as a population.

    `#169` resolves a typed Saatja and the Adressaat it derives from it against
    one catalogue inside one transaction, so the spelling becomes **one**
    `Organisation` row used twice. `#171` made all three institution filters
    read that same catalogue through one searchable control — which is what
    makes a body created a minute ago findable at all: it is not in the first
    twenty alphabetically, and before `#171` neither `Saatja` nor `Adressaat`
    had a search box to reach it with.

    Composed, the claim is that no second catalogue exists anywhere between the
    two features. Filtering by the new body as Saatja and as Adressaat must both
    return the Teema, because both relations point at the same row.
    """
    sign_in(page, base_url, MARTIN)
    open_create(page, base_url)

    name = a_new_name()
    title = f"Teema uue asutuse nimel {uuid.uuid4().hex[:8]}"

    # A body that does not exist yet, typed into `Uus saatja`. #169 then offers
    # it as the addressee too, through the provisional chip.
    page.locator("[data-sender-name]").fill(name)
    assert _summary(page) == f"Adressaat · {name}", (
        f"a typed sender did not become the default addressee: {_summary(page)!r}"
    )

    page.locator("#id_title").fill(title)
    page.fill("#id_next-text", "Lugeda eelnõu ja koostada arvamus")
    page.locator("#jargmine-tegevus").get_by_role("button", name="+1 nädal").click()
    page.get_by_role("button", name="Loo teema").click()
    page.wait_for_load_state("domcontentloaded")

    complaints = page.locator(".field__error, .formerror, .message--error").all_inner_texts()
    assert not complaints, f"the form refused: {complaints}"
    page.wait_for_url(re.compile(r"/teemad/[0-9a-f-]{36}/$"))

    # -- offered by all three institution controls -------------------------
    page.goto(f"{base_url}/teemad/?olek=koik")
    page.wait_for_load_state("networkidle")
    open_advanced(page)

    for field in ("saatja", "adressaat", "asutus"):
        search_chooser(page, field, name)
        assert name in chooser_option_names(page, field), (
            f"the {field} control does not offer {name!r}; it offered "
            f"{chooser_option_names(page, field)!r}"
        )

    # -- and each direction returns the Teema ------------------------------
    # By value rather than by label, and re-read per dimension: each submit is a
    # fresh page and the panel comes back closed.
    for field in ("saatja", "adressaat", "asutus"):
        page.goto(f"{base_url}/teemad/?olek=koik")
        page.wait_for_load_state("networkidle")
        open_advanced(page)
        search_chooser(page, field, name)
        select = page.locator(f"#orgchooser-{field} select[name='{field}']")
        value = select.locator("option", has_text=name).first.get_attribute("value")
        assert value, f"the {field} option for {name!r} carries no value"
        select.select_option(value)
        page.locator("#tapsem-otsing").get_by_role("button", name="Filtreeri").click()
        page.wait_for_load_state("networkidle")

        assert f"{field}={value}" in page.url
        expect(page.get_by_role("link", name=title).first).to_be_visible()
