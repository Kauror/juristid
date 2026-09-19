"""What only exists once #149, #150, #151 and #152 are on the same branch.

Each of the four was green on its own, and each carries its own suite for what
it changed. None of them could test the thing this file tests, because none of
them had the others' code: `Uus teema` now stages and reads a file (#152) on a
form whose sender control and dropzone were rebuilt (#151), and the Matter it
creates carries add forms that open in place (#150) — and all three bind
through the one `bind()`/`htmx:afterSwap` path in `static/js/app.js`.

The failure this file is here for is the quiet one: a control that works on
each branch and binds twice, or to the wrong form, once the branches are
composed. That cannot be seen in a unit test and it cannot be seen on any one
of the four branches.

**The route changed with docs/adr/0088 and the risk did not.** #152's reading
is withdrawn from `Uus teema`, so a Teema is no longer filed by waiting for
suggestions to land — but the half of #152 that carries the composed risk is
the *staging fragment*, which still uploads the file ahead of the save and
still swaps two regions into the open create form. That swap is what binds
twice or to the wrong form if anything is wrong, so it is still what these
tests file through.

Deliberately few. The composed rules themselves — precedence, exact bytes, one
file one Document, removal, cross-user refusal — are proved against the
database in `tests/test_intake_staging.py`, which is already combined code:
#152 was written on top of #151 and its suite runs against both.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest
from playwright.sync_api import expect

from e2e.conftest import SANDRA, give_first_step, open_add_panel, sign_in

pytestmark = pytest.mark.e2e

REPOSITORY_ROOT = Path(__file__).resolve().parent.parent

LETTER = """Näidisministeerium
Suur-Ameerika 1, Tallinn

Eesti Kaubandus-Tööstuskoda
Meie 05.09.2026 nr 1-4/26/5559-2

Pakendiseaduse rakendusmääruse eelnõu kooskõlastamiseks

Lugupeetud Koja esindajad

Saadame Teile kooskõlastamiseks pakendiseaduse rakendusmääruse eelnõu. Palume
esitada arvamus hiljemalt 18. septembriks 2026.

Lugupidamisega
Mari Näidis
nõunik
"""


@pytest.fixture
def letter_pdf(tmp_path: Path) -> Path:
    sys.path.insert(0, str(REPOSITORY_ROOT))
    from tests.synthetic_corpus import text_pdf

    path = tmp_path / "kaaskiri.pdf"
    path.write_bytes(text_pdf([LETTER]))
    return path


def create_with_a_staged_file(page, base_url: str, title: str, pdf: Path) -> str:
    """File a Teema the way the composed product asks for one, and return it.

    The #151 + #152 journey as it ships: choose a file, let the staging island
    upload it and swap its two regions into the form that is still open, then
    `Loo teema`. Everything after this point is a Matter that arrived through
    that path, which is what makes the assertions below about #150's forms a
    composition test rather than a repeat of #150's own.

    It used to wait for `data-intake-state="reading"` and then drain the reader
    in its own process, because the next thing it looked at was a suggestion.
    Neither is here any more: docs/adr/0088 withdrew the reading from this page,
    the panel never reports `reading`, and there is nothing to wait for. What is
    waited for instead is the staged row — which is the thing these tests
    actually depend on, since it is what proves the file reached the server
    before `Loo teema` and what makes the evidence assertion below meaningful.
    """
    page.goto(f"{base_url}/teemad/uus/")
    expect(page.get_by_role("heading", name="Uus teema")).to_be_visible()

    page.locator("#id_files").set_input_files(str(pdf))
    # The server's own list, written by the staging answer rather than by the
    # browser's preview — so this waits for the upload rather than for the
    # `change` event that started it.
    expect(page.locator("#intake-failid .dropzone__file")).to_have_count(1, timeout=30_000)

    page.fill("#id_title", title)
    give_first_step(page)
    page.get_by_role("button", name="Loo teema").click()
    page.wait_for_url(re.compile(r"/teemad/[0-9a-f-]{36}/$"))
    return page.url


def test_the_inline_add_forms_still_work_on_a_teema_filed_with_a_staged_file(
    page, base_url, letter_pdf
):
    """#150's accordion, on a Matter created by #151 + #152's `Uus teema`.

    The composed risk is binding, not markup. `bindPeriodFields` runs on
    `DOMContentLoaded` and again on every `htmx:afterSwap`, and #152 added
    fragments that swap on the create form while #150 added fragments that swap
    on this one. A listener registered twice makes the precision radios fire
    `sync` twice, which is invisible until it is not — so this asserts the
    control actually narrows, that it narrows on the second open too, and that
    the accordion still holds exactly one form.
    """
    sign_in(page, base_url, SANDRA)
    where = create_with_a_staged_file(
        page, base_url, "Lugemise kaudu loodud teema, millele lisatakse töövõit", letter_pdf
    )

    # The file really did arrive as evidence: the journey completed rather than
    # quietly degrading to the ordinary submit.
    page.get_by_role("link", name=re.compile(r"^Dokumendid")).click()
    page.wait_for_load_state("networkidle")
    expect(page.get_by_text("kaaskiri.pdf").first).to_be_visible()
    page.goto(where)

    # **The fragment route, not the Teema page.** `+ Töövõit` and `+ Jõustumine`
    # are composer panels since the approved target, and the composer's panels
    # are not this accordion (docs/adr/0074 §7, §8). What this test is about —
    # that `bindPeriodFields` still narrows the control on a Matter created
    # through assisted intake, and narrows it on the second open too — is a
    # property of the fragment, which is where it is now asserted.
    page.goto(f"{where.rstrip('/')}/toovoidud/lisa/")
    page.wait_for_load_state("networkidle")
    form = page.locator("form").filter(has=page.get_by_label("Kvartali täpsusega")).first
    expect(form).to_be_visible()

    form.get_by_label("Kvartali täpsusega").check()
    expect(form.get_by_label("Kuupäev", exact=True)).to_be_hidden()
    expect(form.get_by_label("Kvartal", exact=True)).to_be_visible()

    # And the same control on the other fact, opened in its turn, binds once.
    page.goto(f"{where.rstrip('/')}/joustumine/lisa/")
    page.wait_for_load_state("networkidle")
    other = page.locator("form").filter(has=page.get_by_label("Kvartali täpsusega")).first
    other.get_by_label("Kvartali täpsusega").check()
    expect(other.get_by_label("Kuupäev", exact=True)).to_be_hidden()
    expect(other.get_by_label("Kvartal", exact=True)).to_be_visible()

    # And back, through a fragment that has now been swapped twice — and it
    # still saves, which is the end of what this test is about.
    page.goto(f"{where.rstrip('/')}/toovoidud/lisa/")
    page.wait_for_load_state("networkidle")
    form = page.locator("form").filter(has=page.get_by_label("Kvartali täpsusega")).first
    form.get_by_label("Kvartali täpsusega").check()
    expect(form.get_by_label("Kuupäev", exact=True)).to_be_hidden()

    form.get_by_label("Töövõit", exact=True).fill("Erisus jäi rakendusmäärusesse")
    form.get_by_label("Kvartal", exact=True).select_option("2")
    form.get_by_label("Aasta", exact=True).fill("2031")
    form.get_by_role("button", name="Salvesta töövõit").click()
    page.wait_for_load_state("networkidle")

    # The record landed, and the standalone route redirected back to the Matter.
    assert page.url.startswith(where.rstrip("/"))
    expect(page.get_by_text("Erisus jäi rakendusmäärusesse").first).to_be_visible()


def test_an_inline_commencement_does_not_reach_the_composers_own_period_control(
    page, base_url, letter_pdf
):
    """#150's `owner` scoping, with #152's fragments on the same page.

    `bindOnePeriodControl` looks for `#joustumise-liik` inside the control's own
    form rather than anywhere in the bound scope. When the scope is the whole
    document — which is what `DOMContentLoaded` passes, and what
    `htmx:afterSwap` passes for a swap target without `querySelector` — an
    unscoped lookup would let the commencement form's «üldises korras» radio
    hide the composer's «Oluline tähtaeg» fields several hundred pixels above
    it. That is a cross-form defect, so it needs both forms on one real page.
    """
    sign_in(page, base_url, SANDRA)
    create_with_a_staged_file(
        page, base_url, "Lugemise kaudu loodud teema kahe perioodikontrolliga", letter_pdf
    )

    # `+ Oluline tähtaeg`'s own approximate-period control.
    #
    # `Täpsus` is four native radios with `<label>` chips since docs/adr/0079
    # §1. It was three `<button>`s over a hidden field (docs/adr/0074 §11), and
    # the class this used to find it by — `.cx-when` — went with them. What the
    # test is about is unchanged: this control belongs to *this* form, and the
    # commencement form's own radio several hundred pixels below must not reach
    # it.
    open_add_panel(page, "lisa-tahtaeg")
    panel_precision = page.locator("#lisa-tahtaeg label.precision__chip").first
    expect(panel_precision).to_be_visible()

    # Now the inline commencement form, and the answer that removes its own
    # date control entirely. Reached through the fragment route, because the
    # Teema page's own `+ Jõustumine` is a `LISA TEEMALE` panel now and this
    # test is about the *other* form keeping its date control
    # (docs/adr/0074 §7, docs/adr/0075 §2).
    page.goto(f"{page.url.split('#')[0].rstrip('/')}/joustumine/lisa/")
    page.wait_for_load_state("networkidle")
    form = page.locator("form").filter(has=page.get_by_label("Jõustub üldises korras")).first
    expect(form).to_be_visible()
    form.get_by_label("Jõustub üldises korras").check()

    expect(form.get_by_label("Kuupäev", exact=True)).to_be_hidden()
    # The panel is a different form on a different page and keeps its own.
    page.go_back()
    page.wait_for_load_state("networkidle")
    open_add_panel(page, "lisa-tahtaeg")
    expect(page.locator("#lisa-tahtaeg label.precision__chip").first).to_be_visible()
