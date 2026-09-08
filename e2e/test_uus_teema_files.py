"""The file chosen on Uus teema has to be on Dokumendid a moment later.

The one browser journey that proves it, end to end, through the real chooser
and the real form: choose, create, open the file tab, read the filename. The
suite already had a test that attached a file and stopped at the redirect, so
every way of losing the upload *after* the Matter row was written passed it.

Nothing here mocks the attachment service. A test that stubs
`_attach_incoming_file` proves the view calls it, which was never the part in
doubt.
"""

from __future__ import annotations

import re

import pytest
from playwright.sync_api import expect

from e2e.conftest import MARTIN, sign_in

pytestmark = pytest.mark.e2e


PDF_BYTES = b"%PDF-1.4\n1 0 obj<</Type/Catalog>>endobj\ntrailer<</Root 1 0 R>>\n%%EOF\n"


def open_create(page, base_url: str) -> None:
    page.goto(f"{base_url}/teemad/uus/")
    expect(page.get_by_role("heading", name="Uus teema")).to_be_visible()


def file_row(page, name: str):
    """The Dokumendid row for one filename, by the link that opens it."""
    return page.get_by_role("link", name=name, exact=True)


def name_a_next_step(page) -> None:
    """Say what happens next, as a lawyer filing a real Teema would.

    Not decoration. A Teema this suite leaves behind with no next action joins
    the department's «järgmise tegevuseta» population permanently, and
    `e2e/test_kpi_navigation.py` reads the first twelve rows of that list
    expecting the seeded unassigned Teema to be on it. This file files several
    Matters, so it owes each of them a next step (e2e/conftest.py).
    """
    page.fill("#id_next-text", "Kontrollida, mida fail nõuab")
    page.locator("#jargmine-tegevus").get_by_role("button", name="+1 nädal").click()


def create_with_files(page, base_url: str, title: str, paths: list[str]) -> str:
    open_create(page, base_url)
    page.locator("#id_title").fill(title)
    page.locator("#id_files").set_input_files(paths)
    name_a_next_step(page)
    page.get_by_role("button", name="Loo teema").click()
    page.wait_for_load_state("domcontentloaded")

    complaints = page.locator(".field__error, .formerror, .message--error").all_inner_texts()
    assert not complaints, f"the form refused: {complaints}"

    page.wait_for_url(re.compile(r"/teemad/[0-9a-f-]{36}/$"))
    return page.url


def confirmation(page) -> str:
    """What the save said it did.

    `matter_create` says «koos N failiga» only when `request.FILES` actually
    held them, so this separates "the browser never sent the file" from "the
    file was stored and the tab cannot see it".
    """
    banner = page.locator(".message")
    return banner.first.inner_text() if banner.count() else ""


def open_documents(page) -> None:
    page.get_by_role("link", name=re.compile(r"^Dokumendid")).click()
    page.wait_for_load_state("networkidle")


def test_one_file_chosen_on_uus_teema_is_on_dokumendid(page, base_url, tmp_path):
    """The reported defect, as the person reported it."""
    sign_in(page, base_url, MARTIN)

    attachment = tmp_path / "kaaskiri.pdf"
    attachment.write_bytes(PDF_BYTES)

    create_with_files(page, base_url, "Failiga loodud teema", [str(attachment)])
    said = confirmation(page)
    open_documents(page)

    assert "failiga" in said, f"the save did not report receiving a file: {said!r}"
    expect(page.get_by_text("Sellel teemal ei ole veel dokumente.")).to_have_count(0)
    expect(file_row(page, "kaaskiri.pdf")).to_be_visible()
    expect(page.get_by_role("link", name="Laadi alla kaaskiri.pdf")).to_be_visible()


def test_every_file_chosen_together_arrives_together(page, base_url, tmp_path):
    """Three chosen at once are three Documents, not the first one."""
    sign_in(page, base_url, MARTIN)

    names = ["esimene.pdf", "teine.pdf", "kolmas.pdf"]
    paths = []
    for name in names:
        path = tmp_path / name
        path.write_bytes(PDF_BYTES)
        paths.append(str(path))

    create_with_files(page, base_url, "Kolme failiga teema", paths)
    open_documents(page)

    for name in names:
        expect(file_row(page, name)).to_be_visible()


def test_a_file_taken_back_off_does_not_arrive(page, base_url, tmp_path):
    """The remove control is the other half of the contract."""
    sign_in(page, base_url, MARTIN)

    first = tmp_path / "eemaldatud.pdf"
    second = tmp_path / "alles.pdf"
    first.write_bytes(PDF_BYTES)
    second.write_bytes(PDF_BYTES)

    open_create(page, base_url)
    page.locator("#id_title").fill("Ühe eemaldatud failiga teema")
    page.locator("#id_files").set_input_files([str(first), str(second)])
    expect(page.locator(".dropzone__file")).to_have_count(2)

    page.get_by_role("button", name="Eemalda fail eemaldatud.pdf").click()
    expect(page.locator(".dropzone__file")).to_have_count(1)

    name_a_next_step(page)
    page.get_by_role("button", name="Loo teema").click()
    page.wait_for_url(re.compile(r"/teemad/[0-9a-f-]{36}/$"))
    open_documents(page)

    expect(file_row(page, "alles.pdf")).to_be_visible()
    expect(file_row(page, "eemaldatud.pdf")).to_have_count(0)


# -- the defect ---------------------------------------------------------------


def test_a_refused_save_does_not_throw_the_chosen_file_away(page, base_url, tmp_path):
    """The reported loss, in the journey it actually happened in.

    A browser cannot repopulate a file input, so before this every server-side
    refusal silently emptied it: the person fixed what the page complained
    about, pressed the button again, and filed a Matter with no documents —
    having chosen a file twice as far as they could tell, and been told about
    neither loss. The bytes had reached the server both times.

    This test failed on `main` at the line below that counts the preview rows
    after the refusal. It is the reason `app/documents/pending.py` exists.
    """
    sign_in(page, base_url, MARTIN)

    attachment = tmp_path / "kaotatud.pdf"
    attachment.write_bytes(PDF_BYTES)

    open_create(page, base_url)
    page.locator("#id_title").fill("Keeldumise järel loodud teema")
    page.locator("#id_files").set_input_files([str(attachment)])
    expect(page.locator(".dropzone__file")).to_have_count(1)

    # A refusal only the server can make: «Muu» ticked with nothing written in
    # the box it reveals. The browser has nothing to complain about, so the
    # request goes, and the answer is a re-rendered form.
    page.locator("#id_policy_area_other_selected").check()
    page.get_by_role("button", name="Loo teema").click()
    page.wait_for_load_state("domcontentloaded")
    expect(page.locator(".field__error").first).to_be_visible()

    # The chosen file has to still be on the page after the refusal.
    expect(page.locator(".dropzone__file")).to_have_count(1)
    expect(page.locator(".dropzone__file")).to_contain_text("kaotatud.pdf")

    # Fix what was complained about and save. The file has to arrive.
    page.locator("#id_policy_area_other").fill("Ehitus ja kinnisvara")
    name_a_next_step(page)
    page.get_by_role("button", name="Loo teema").click()
    page.wait_for_url(re.compile(r"/teemad/[0-9a-f-]{36}/$"))
    open_documents(page)

    expect(page.get_by_text("Sellel teemal ei ole veel dokumente.")).to_have_count(0)
    expect(file_row(page, "kaotatud.pdf")).to_be_visible()


# -- the preview row ----------------------------------------------------------


def test_the_preview_says_the_filename_the_size_and_how_to_undo_it(page, base_url, tmp_path):
    """What a person needs before saving, and nothing that never varies.

    The row used to open with `TÕEND`, which is what every row of this list
    always is. A label that reads the same on every line is not information, and
    it was taking the first words of each one. Everything that *does* vary —
    which file, how big, and the way to take it back off while that is still
    free — is untouched, and so is the count beside the legend.
    """
    sign_in(page, base_url, MARTIN)

    attachment = tmp_path / "eelnou.pdf"
    attachment.write_bytes(PDF_BYTES * 40)

    open_create(page, base_url)
    page.locator("#id_files").set_input_files([str(attachment)])

    row = page.locator(".dropzone__file")
    expect(row).to_have_count(1)
    expect(row.locator(".dropzone__name")).to_have_text("eelnou.pdf")
    expect(row.locator(".dropzone__size")).to_contain_text("KB")
    expect(row.get_by_role("button", name="Eemalda fail eelnou.pdf")).to_be_visible()
    expect(page.locator("[data-chipcount-for='id_files']")).to_have_text("1 valitud")

    # No badge in front of the name — not the old one, and not a replacement.
    expect(page.locator(".dropzone__kind")).to_have_count(0)
    for word in ("TÕEND", "FAIL", "DOKUMENT", "MANUS"):
        expect(row).not_to_contain_text(word)


def test_a_second_drop_adds_to_the_selection(page, base_url, tmp_path):
    """Dropping a covering letter and then its annex is two gestures and one
    obvious intention. Assigning the second `FileList` straight onto the input
    silently threw the first away."""
    sign_in(page, base_url, MARTIN)
    open_create(page, base_url)

    def drop(name: str) -> None:
        page.evaluate(
            """(name) => {
                const zone = document.querySelector('.dropzone');
                const transfer = new DataTransfer();
                transfer.items.add(new File([new Uint8Array([37, 80, 68, 70])], name,
                                            {type: 'application/pdf'}));
                zone.dispatchEvent(new DragEvent('drop', {
                    bubbles: true, cancelable: true, dataTransfer: transfer,
                }));
            }""",
            name,
        )

    drop("esimene.pdf")
    expect(page.locator(".dropzone__file")).to_have_count(1)

    drop("teine.pdf")
    expect(page.locator(".dropzone__file")).to_have_count(2)
    expect(page.locator(".dropzone__file").first).to_contain_text("esimene.pdf")
    expect(page.locator(".dropzone__file").nth(1)).to_contain_text("teine.pdf")


def test_a_dropped_file_reaches_dokumendid(page, base_url, tmp_path):
    """The drag-and-drop path, all the way through, not just into the preview."""
    sign_in(page, base_url, MARTIN)
    open_create(page, base_url)

    page.evaluate(
        """() => {
            const zone = document.querySelector('.dropzone');
            const transfer = new DataTransfer();
            transfer.items.add(new File([new Uint8Array([37, 80, 68, 70, 45, 49, 46, 52])],
                                        'lohistatud.pdf', {type: 'application/pdf'}));
            zone.dispatchEvent(new DragEvent('drop', {
                bubbles: true, cancelable: true, dataTransfer: transfer,
            }));
        }"""
    )
    expect(page.locator(".dropzone__file")).to_contain_text("lohistatud.pdf")

    page.locator("#id_title").fill("Lohistatud failiga teema")
    name_a_next_step(page)
    page.get_by_role("button", name="Loo teema").click()
    page.wait_for_url(re.compile(r"/teemad/[0-9a-f-]{36}/$"))
    open_documents(page)

    expect(file_row(page, "lohistatud.pdf")).to_be_visible()
