"""An e-mail filed on Uus teema is filed as «Algne e-kiri» (ENG-066) — read on the
document's own page since Dokumendid lost its Roll column (2026-10-07).

The role is the one piece of classification the ordinary workflow shows about
an incoming file: the Dokumendid table prints it beside the filename. It used
to depend on the path the file took. Staged through the script it was «Algne
e-kiri»; posted with the form — scripting off, staging unavailable — it was
«Saabunud ametlik dokument», because that step wrote one role for every file.

Both paths are driven here through the real chooser and the real form, and both
must print the same word in the file's own row. Nothing is mocked: a test that
stubs the attachment step proves the view calls it, which was never in doubt.

Each Teema gets its own title from `unique_title`, because the browser world is
shared by every file in a shard and a fixed title stops being an identity the
second time it is filed.
"""

from __future__ import annotations

import re

import pytest
from playwright.sync_api import expect

from e2e.conftest import MARTIN, give_first_step, sign_in, start_first_step, unique_title

pytestmark = pytest.mark.e2e

#: An RFC 822 message. `read_upload` checks no signature for `.eml`; the
#: extension is what `role_for` reads.
EML_BYTES = (
    b"From: Mari Naidis <mari.naidis@naidisministeerium.invalid>\r\n"
    b"To: koda@naidiskoda.invalid\r\n"
    b"Subject: Eelnou kooskolastamiseks\r\n"
    b"\r\n"
    b"Saadame eelnou kooskolastamiseks.\r\n"
)
PDF_BYTES = b"%PDF-1.4\n1 0 obj<</Type/Catalog>>endobj\ntrailer<</Root 1 0 R>>\n%%EOF\n"


def file_a_teema_with(
    page, base_url: str, title: str, paths: list[str], *, start: bool = True
) -> str:
    """Uus teema, the files chosen, «Loo teema», and the Teema it opened.

    ``start=False`` leaves the Teema with no first step, for a page with
    scripting off: `+ Lisa tegevus` is a workspace save, and those are
    HTMX posts.
    """
    page.goto(f"{base_url}/teemad/uus/")
    expect(page.get_by_role("heading", name="Uus teema")).to_be_visible()
    page.locator("#id_title").fill(title)
    page.locator("#id_files").set_input_files(paths)
    # A Teema this file leaves behind owes the department a next step
    # (e2e/conftest.py `give_first_step`); the date box is a plain input, so
    # this works with scripting off as well.
    give_first_step(page)
    page.get_by_role("button", name="Loo teema").click()
    page.wait_for_url(re.compile(r"/teemad/[0-9a-f-]{36}/$"))
    url = page.url
    if start:
        start_first_step(page)
    return url


def document_row(page, filename: str):
    """The Dokumendid row for one file, by the link that opens it — and only it."""
    return page.locator("tr").filter(has=page.get_by_role("link", name=filename, exact=True))


def role_of(page, filename: str) -> str:
    """The role a document is filed under, read on its own page.

    Dokumendid no longer prints a role column (owner's round, 2026-10-07); the
    document's page still states it, and the row links there from `⋯`.
    """
    href = (
        document_row(page, filename).locator("a", has_text="Dokumendi leht").get_attribute("href")
    )
    assert href, f"no document page link on the {filename} row"
    body = page.context.new_page()
    try:
        body.goto(href if href.startswith("http") else page.url.split("/teemad/")[0] + href)
        body.wait_for_load_state("networkidle")
        return body.locator("body").inner_text()
    finally:
        body.close()


def open_documents(page) -> None:
    page.goto(page.url.rstrip("/") + "/dokumendid/")
    expect(page.locator("table.doctable")).to_be_visible()


def write_files(tmp_path) -> tuple[str, str]:
    email = tmp_path / "kiri.eml"
    email.write_bytes(EML_BYTES)
    memo = tmp_path / "memo.pdf"
    memo.write_bytes(PDF_BYTES)
    return str(email), str(memo)


def test_an_email_chosen_on_uus_teema_is_the_original_email(page, base_url, tmp_path):
    """The ordinary path: scripting on, the file staged as it is chosen."""
    sign_in(page, base_url, MARTIN)
    email, memo = write_files(tmp_path)

    file_a_teema_with(page, base_url, unique_title("Kiri skriptiga"), [email, memo])
    open_documents(page)

    assert "Algne e-kiri" in role_of(page, "kiri.eml")
    assert "Saabunud ametlik dokument" in role_of(page, "memo.pdf")


def test_an_email_posted_with_the_form_is_the_original_email_too(
    page, base_url, browser, browser_context_args, tmp_path
):
    """The path the defect lived on: no script, the file in the form post itself."""
    email, memo = write_files(tmp_path)

    # The suite's own viewport, locale and time zone, with scripting off.
    context = browser.new_context(**browser_context_args, java_script_enabled=False)
    try:
        scriptless = context.new_page()
        # The development sign-in is an ordinary form post, so it needs no
        # script (e2e/test_substantive_history.py does the same).
        scriptless.goto(f"{base_url}/konto/arendus-sisselogimine/")
        scriptless.get_by_label(MARTIN.display_name, exact=False).check()
        scriptless.get_by_role("button", name="Logi sisse").click()
        scriptless.wait_for_url(f"{base_url}/minu-asjad/")

        url = file_a_teema_with(
            scriptless, base_url, unique_title("Kiri skriptita"), [email, memo], start=False
        )
        open_documents(scriptless)

        assert "Algne e-kiri" in role_of(scriptless, "kiri.eml")
        assert "Saabunud ametlik dokument" in role_of(scriptless, "memo.pdf")
    finally:
        context.close()

    # The Teema leaves the department with an open step like every other file
    # this suite files; setting it is a workspace save, so a scripted page does it.
    sign_in(page, base_url, MARTIN)
    page.goto(url)
    start_first_step(page)
