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

Deliberately few. The composed rules themselves — precedence, exact bytes, one
file one Document, removal, cross-user refusal — are proved against the
database in `tests/test_intake_staging.py`, which is already combined code:
#152 was written on top of #151 and its suite runs against both.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest
from playwright.sync_api import expect

from e2e.conftest import SANDRA, sign_in

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


def read_staged_files() -> None:
    """Drain the staged queue in its own process, as the deployment does.

    The same subprocess call `e2e/test_uus_teema_reading.py` makes, and for the
    same reason: pytest's settings mint a fresh temporary storage root, so a
    child inheriting them would look in an empty directory.
    """
    environment = {**os.environ, "DJANGO_SETTINGS_MODULE": "config.settings"}
    result = subprocess.run(
        [sys.executable, "manage.py", "run_intake_reader", "--once", "--limit", "20"],
        cwd=REPOSITORY_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )
    report = "\n".join(["stdout:", result.stdout, "stderr:", result.stderr])
    assert result.returncode == 0, report


def create_through_assisted_intake(page, base_url: str, title: str, pdf: Path) -> str:
    """File a Teema the way the composed product asks for one, and return it.

    The whole #151 + #152 journey: choose a file, let it be staged and read,
    see the suggestions land on the form that is still open, then `Loo teema`.
    Everything after this point is a Matter that arrived through the new path,
    which is what makes the assertions below about #150's forms a composition
    test rather than a repeat of #150's own.
    """
    page.goto(f"{base_url}/teemad/uus/")
    expect(page.get_by_role("heading", name="Uus teema")).to_be_visible()

    page.locator("#id_files").set_input_files(str(pdf))
    expect(page.locator("#intake-panel")).to_have_attribute("data-intake-state", "reading")

    read_staged_files()
    expect(page.locator("#intake-panel")).to_have_attribute(
        "data-intake-state", "ready", timeout=30_000
    )

    page.fill("#id_title", title)
    page.fill("#id_next-text", "Lugeda eelnõu ja koostada arvamus")
    page.locator("#jargmine-tegevus").get_by_role("button", name="+1 nädal").click()
    page.get_by_role("button", name="Loo teema").click()
    page.wait_for_url(re.compile(r"/teemad/[0-9a-f-]{36}/$"))
    return page.url


def test_the_inline_add_forms_still_work_on_a_teema_filed_through_assisted_intake(
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
    where = create_through_assisted_intake(
        page, base_url, "Lugemise kaudu loodud teema, millele lisatakse töövõit", letter_pdf
    )

    # The file really did arrive as evidence: the journey completed rather than
    # quietly degrading to the ordinary submit.
    page.get_by_role("link", name=re.compile(r"^Dokumendid")).click()
    page.wait_for_load_state("networkidle")
    expect(page.get_by_text("kaaskiri.pdf").first).to_be_visible()
    page.goto(where)

    form = page.locator(".factslot")
    # `+ Töövõit` moved into the composer's action row with the 2026-09
    # refinement — one place from which a fact is added — so reaching it opens
    # the composer first (docs/matter-page-refinement.md).
    page.locator("#teema-koostaja summary.uxcomp__collapsed").click()
    page.get_by_role("link", name="+ Töövõit", exact=True).click()
    expect(form).to_be_visible()

    form.get_by_label("Kvartali täpsusega").check()
    expect(form.get_by_label("Kuupäev", exact=True)).to_be_hidden()
    expect(form.get_by_label("Kvartal", exact=True)).to_be_visible()

    # Opening the other one closes this one: the accordion is the render, and a
    # second binding pass has not left two forms on the page.
    page.get_by_role("link", name="+ Jõustumine", exact=True).click()
    expect(page.locator("#faktivorm-joustumine")).to_be_visible()
    expect(page.locator("#faktivorm-toovoit")).to_have_count(0)

    # And back, through a fragment that has now been swapped twice.
    page.get_by_role("link", name="+ Töövõit", exact=True).click()
    expect(page.locator("#faktivorm-toovoit")).to_be_visible()
    form.get_by_label("Kvartali täpsusega").check()
    expect(form.get_by_label("Kuupäev", exact=True)).to_be_hidden()

    form.get_by_label("Töövõit", exact=True).fill("Erisus jäi rakendusmäärusesse")
    form.get_by_label("Kvartal", exact=True).select_option("2")
    form.get_by_label("Aasta", exact=True).fill("2031")
    form.get_by_role("button", name="Salvesta töövõit").click()

    victories = page.get_by_role("region", name="Töövõidud")
    expect(victories.get_by_text("Erisus jäi rakendusmäärusesse")).to_be_visible()
    expect(page.locator(".factslot")).to_have_count(0)
    assert page.url == where


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
    create_through_assisted_intake(
        page, base_url, "Lugemise kaudu loodud teema kahe perioodikontrolliga", letter_pdf
    )

    # The composer's own approximate-period control, open and visible.
    page.locator("summary.uxcomp__collapsed").click()
    page.get_by_role("button", name="+ Oluline tähtaeg").click()
    page.locator("#koostaja-tahtaeg summary", has_text="Ligikaudne aeg").click()
    composer_precision = page.locator("input[name=deadline_precision]").first
    expect(composer_precision).to_be_visible()

    # Now the inline commencement form, and the answer that removes its own
    # date control entirely.
    page.get_by_role("link", name="+ Jõustumine", exact=True).click()
    form = page.locator(".factslot")
    expect(form).to_be_visible()
    form.get_by_label("Jõustub üldises korras").check()

    expect(form.get_by_label("Kuupäev", exact=True)).to_be_hidden()
    # The composer is a different form and keeps its control.
    expect(composer_precision).to_be_visible()
