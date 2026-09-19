"""What only the four branches *together* can be wrong about.

`#169`, `#170`, `#171` and `#172` were each developed and reviewed against
`main`, so no suite in the repository has ever had more than one of them
loaded at a time. Each is proved on its own — the Õigusakt row in
`e2e/test_oigusakt_row.py`, the discovery filters in
`e2e/test_matter_intelligence.py`, the reader in
`e2e/test_uus_teema_reading.py`. This file asserts only the seams *between*
them, and nothing that any of those already covers.

**Two seams, where there were three.** The first two were both about `#169`'s
sender→addressee default: the reader answering Adressaat through Saatja, and a
person's own answer outranking a suggestion that arrived afterwards. That
default is withdrawn with the question it served — `Uus teema` no longer asks
who Koda answers (docs/adr/0090 §5) — and the two seam tests went with it,
together with `e2e/test_counterparty_selection.py`, which owned the property in
full.

What remains:

* **the reader leaves a chosen `Õigusakt` alone.** `#172` fills four controls
  and must not touch a fifth somebody has already answered.

* **a body typed on `Uus teema` is findable in Teemad.** `#169` resolves one
  typed spelling into one `Organisation` row inside one transaction; `#171` made
  all three institution filters read that same catalogue through one searchable
  control. Composed, they are a lifecycle: a body that did not exist when the
  form was opened is filterable minutes later — as a Saatja, and through the
  combined `Asutus` filter, which is what «one catalogue» means.

The reader's harness is imported rather than rebuilt: the worker drain is that
file's own, and a second copy would be a second thing to keep true.
"""

from __future__ import annotations

import re
import sys
import uuid
from pathlib import Path

import pytest
from playwright.sync_api import expect

from e2e.conftest import MARTIN, give_first_step, needs_intake_reading, sign_in
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
    letter names «Näidisministeerium» as its sender.
    """
    sys.path.insert(0, str(REPOSITORY_ROOT))
    from tests.synthetic_corpus import text_pdf

    path = tmp_path / "kaaskiri.pdf"
    path.write_bytes(text_pdf([LETTER]))
    return path


def a_new_name() -> str:
    """A body the catalogue provably does not hold yet.

    Unique per call, because this test *creates* an institution and the browser
    suite shares one database — so a fixed name is in the catalogue from the
    moment this file has run once, and «a body that did not exist when the form
    was opened» would then be a claim about the previous run.
    """
    return f"Näidisamet {uuid.uuid4().hex[:8]}"


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
# Seam 2 — Õigusakt is a person's answer, and the reader never touches it
# ---------------------------------------------------------------------------


@needs_intake_reading
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

    `#169` resolves a typed Saatja against one catalogue inside the save's own
    transaction, so the spelling becomes **one** `Organisation` row. `#171` made
    all three institution filters read that same catalogue through one
    searchable control — which is what makes a body created a minute ago
    findable at all: it is not in the first twenty alphabetically, and before
    `#171` `Saatja` had no search box to reach it with.

    Composed, the claim is that no second catalogue exists anywhere between the
    two features. Filtering by the new body as `Saatja` and through the combined
    `Asutus` filter must both return the Teema.

    `Adressaat` is not asserted here any more: the Teema is created with a
    sender and no recipient, because `Uus teema` stopped asking
    (docs/adr/0090 §5). The `Adressaat` *filter* is unchanged and is
    `tests/test_organisation_pool.py`'s.
    """
    sign_in(page, base_url, MARTIN)
    open_create(page, base_url)

    name = a_new_name()
    title = f"Teema uue asutuse nimel {uuid.uuid4().hex[:8]}"

    # A body that does not exist yet, named through the one Saatja control:
    # type it, then press the `+` that says «this is a body you do not have»
    # (docs/adr/0073).
    box = page.locator("#saatja-otsi")
    box.click()
    box.fill(name)
    page.locator("#saatja-valik [data-orgfind-add]").click()
    expect(page.locator("#saatja-valik [data-orgfind-provisional]")).to_be_visible()

    page.locator("#id_title").fill(title)
    give_first_step(page)
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
    # Every one of the three offers it, because there is one catalogue behind
    # all three. Only the two that are true of this Teema are then *applied*.

    # -- and each direction returns the Teema ------------------------------
    # By value rather than by label, and re-read per dimension: each submit is a
    # fresh page and the panel comes back closed.
    for field in ("saatja", "asutus"):
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
