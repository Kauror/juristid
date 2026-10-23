"""`Saabunud → Lisa`: a refused save keeps the chosen files, in a real browser.

Before 2026-10-23 a refusal of this page — an impossible `Arvamuse tähtaeg`
such as 31.02.2026 — came back with the file area empty and the filename
nowhere on the page, and the corrected `Salvesta` was then stopped by the
browser's own «Please select one or more files.» on the visually hidden,
`required` file input. Nothing the person could see said why (bug-bounty
B-03). The good files of a refused save are now held by the server
(app/documents/pending.py) exactly as on `Uus teema`, named on the re-rendered
form, and filed by the corrected save without a re-pick.

What only a browser answers: that the native `required` check no longer blocks
the corrected save, and that the one press lands on the new Teema with its
document. The server-side rules are pinned in `tests/test_intake.py`.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from playwright.sync_api import expect

from e2e.conftest import MARTIN, sign_in
from e2e.titles import unique_title

pytestmark = pytest.mark.e2e

REPOSITORY_ROOT = Path(__file__).resolve().parent.parent

INTAKE_PATH = "/saabunud/lisa/"


@pytest.fixture
def letter_pdf(tmp_path: Path) -> Path:
    sys.path.insert(0, str(REPOSITORY_ROOT))
    from tests.synthetic_corpus import government_pdf

    path = tmp_path / "hoitud-kaaskiri.pdf"
    path.write_bytes(government_pdf())
    return path


def test_a_refused_saabunud_save_keeps_the_file_and_the_corrected_save_files_it(
    page, base_url, letter_pdf
) -> None:
    sign_in(page, base_url, MARTIN)
    page.set_viewport_size({"width": 1440, "height": 900})
    page.goto(f"{base_url}{INTAKE_PATH}")
    page.wait_for_load_state("networkidle")

    title = unique_title("Saabunud hoitud fail")
    page.set_input_files("input[name='uploads']", str(letter_pdf))
    page.locator("#id_title").fill(title)
    page.locator("#id_response_deadline").fill("31.02.2026")
    page.get_by_role("button", name="Salvesta", exact=True).click()
    page.wait_for_load_state("networkidle")

    # Refused, and the file is named on the page rather than silently gone.
    held = page.locator("#hoitud-saabunud-failid")
    expect(held).to_contain_text(letter_pdf.name)
    expect(held.get_by_role("checkbox")).to_be_checked()

    page.locator("#id_response_deadline").fill("28.02.2026")
    page.get_by_role("button", name="Salvesta", exact=True).click()

    # One press, no re-pick: the browser's `required` check does not stop it.
    page.wait_for_url(lambda url: "/teemad/" in url)
    expect(page.locator("body")).to_contain_text(title)
    expect(page.locator("body")).to_contain_text("1 faili lisatud")
