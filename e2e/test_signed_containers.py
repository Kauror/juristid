"""Signed containers in a real browser (JUR-CASE-01, docs/adr/0125).

`tests/test_signed_containers.py` holds the door, the evidence and the
alignment against the database. This file holds what only a running page can
settle, on the two surfaces the living-dossier QA reported:

* that `Uus teema`'s chooser offers `.asice` and `.bdoc`, takes them, shows no
  refusal, and files them as the Teema's documents under their own names;
* that `+ Koja arvamus` takes an `.asice` as `Saadetud fail` before the save and
  registers the opinion with it;
* that the file then downloads as itself — the same name, the same bytes;
* and that a file which only *calls* itself a container is refused in words a
  lawyer can read, on both surfaces, with nothing registered.

Every container is built at test time (`tests/synthetic_containers.py`), and
every Matter is one this file creates.
"""

from __future__ import annotations

import re
from datetime import date, timedelta
from pathlib import Path

import pytest
from playwright.sync_api import expect

from e2e.conftest import SANDRA, give_first_step, open_add_panel, sign_in, unique_title
from tests.synthetic_containers import plain_zip, signed_container

pytestmark = pytest.mark.e2e

MINISTRY = "Näidisministeerium"
PDF = b"%PDF-1.4\n1 0 obj<</Type/Catalog>>endobj\ntrailer<</Root 1 0 R>>\n%%EOF\n"
NOT_A_CONTAINER = "see ei ole digiallkirjastatud ümbrik"
NOT_ALLOWED = "ei ole lubatud"


def _past(days: int) -> str:
    on = date.today() - timedelta(days=days)
    return f"{on.day}.{on.month}.{on.year}"


def _new_teema(page, base_url: str, title: str) -> str:
    """A Teema filed through `Uus teema` with its first step.

    Not `create_matter`: every Teema this suite leaves behind owes an open step,
    or it joins the «järgmise tegevuseta» list other files read
    (`give_first_step`, e2e/conftest.py).
    """
    page.goto(f"{base_url}/teemad/uus/")
    page.wait_for_load_state("networkidle")
    page.locator("#id_title").fill(title)
    give_first_step(page)
    page.get_by_role("button", name="Loo teema").click()
    page.wait_for_url(re.compile(r"/teemad/[0-9a-f-]{36}/$"))
    return page.url


def _offered(page, selector: str) -> set[str]:
    """The extensions a file input's chooser offers."""
    accept = page.locator(selector).get_attribute("accept") or ""
    return set(accept.split(","))


def _open_documents(page) -> None:
    page.get_by_role("link", name=re.compile(r"^Dokumendid")).click()
    page.wait_for_load_state("networkidle")


def _downloaded(page, name: str) -> tuple[str, bytes]:
    """Press `Laadi alla <name>` and return what the browser saved."""
    with page.expect_download() as arrival:
        page.get_by_role("link", name=f"Laadi alla {name}", exact=True).click()
    download = arrival.value
    return download.suggested_filename, Path(download.path()).read_bytes()


def _choose_ministry(page) -> None:
    box = page.locator("#koja-adressaat-otsi")
    box.click()
    box.fill("")
    box.type(MINISTRY[:8], delay=20)
    page.locator("#koja-adressaat-tulemused").get_by_role(
        "option", name=MINISTRY, exact=True
    ).click()


def _register_koja_arvamus(page, name: str, content: bytes):
    """Fill `+ Koja arvamus` with one file and press `Registreeri arvamus`.

    The save is an HTMX swap, so the answer is waited for by its request rather
    than by text that an earlier opinion could already be showing
    (e2e/test_lawyer_workflow_package.py).
    """
    open_add_panel(page, "arvamus-koja")
    form = page.locator("#arvamus-koja")
    form.locator("input[type=file]").set_input_files(
        {"name": name, "mimeType": "application/vnd.etsi.asic-e+zip", "buffer": content}
    )
    # Chosen and shown before anything is sent: no complaint from the page.
    expect(form.locator("[data-filedrop-text]")).to_have_text(name)
    form.locator("[name=sent_on]").fill(_past(1))
    _choose_ministry(page)
    with page.expect_response(re.compile(r"/lisa/koja-arvamus/$")) as answer:
        form.get_by_role("button", name="Registreeri arvamus").click()
    page.wait_for_load_state("domcontentloaded")
    return answer.value


# ---------------------------------------------------------------------------
# Uus teema
# ---------------------------------------------------------------------------


def test_uus_teema_takes_a_bdoc_and_an_asice_and_files_them_as_themselves(
    page, base_url, screenshots
):
    sign_in(page, base_url, SANDRA)
    bdoc = signed_container()
    asice = signed_container(deflated_mimetype=True)

    page.goto(f"{base_url}/teemad/uus/")
    assert {".asice", ".bdoc", ".pdf"} <= _offered(page, "#id_files")

    title = unique_title("Allkirjastatud pakett")
    page.locator("#id_title").fill(title)
    page.locator("#id_files").set_input_files(
        [
            {"name": "Kaaskiri.pdf", "mimeType": "application/pdf", "buffer": PDF},
            {"name": "vtk_package.bdoc", "mimeType": "application/octet-stream", "buffer": bdoc},
            {"name": "VTK lisa.asice", "mimeType": "application/octet-stream", "buffer": asice},
        ]
    )

    # All three staged, and no refusal anywhere on the form.
    staged = page.locator("#intake-failid .dropzone__file")
    expect(staged).to_have_count(3)
    expect(page.locator("#intake-failid")).to_contain_text("vtk_package.bdoc")
    expect(page.locator("#intake-failid")).to_contain_text("VTK lisa.asice")
    expect(page.locator(".intakepanel__state--warn")).to_have_count(0)
    expect(page.get_by_text(NOT_ALLOWED)).to_have_count(0)
    screenshots(page, "signed-containers-uus-teema")

    give_first_step(page)
    page.get_by_role("button", name="Loo teema").click()
    page.wait_for_url(re.compile(r"/teemad/[0-9a-f-]{36}/$"))
    complaints = page.locator(".field__error, .formerror, .message--error").all_inner_texts()
    assert not complaints, f"the form refused: {complaints}"

    _open_documents(page)
    expect(page.get_by_role("link", name="vtk_package.bdoc", exact=True)).to_be_visible()
    expect(page.get_by_role("link", name="VTK lisa.asice", exact=True)).to_be_visible()
    screenshots(page, "signed-containers-dokumendid")

    assert _downloaded(page, "vtk_package.bdoc") == ("vtk_package.bdoc", bdoc)
    assert _downloaded(page, "VTK lisa.asice") == ("VTK lisa.asice", asice)


def test_uus_teema_names_a_file_that_only_calls_itself_a_container(page, base_url, screenshots):
    sign_in(page, base_url, SANDRA)

    page.goto(f"{base_url}/teemad/uus/")
    page.locator("#id_title").fill(unique_title("Vale ümbrik"))
    page.locator("#id_files").set_input_files(
        [
            {"name": "Kaaskiri.pdf", "mimeType": "application/pdf", "buffer": PDF},
            {"name": "pakett.bdoc", "mimeType": "application/octet-stream", "buffer": plain_zip()},
        ]
    )

    warning = page.locator(".intakepanel__state--warn")
    expect(warning).to_be_visible()
    expect(warning).to_contain_text("pakett.bdoc")
    expect(warning).to_contain_text(NOT_A_CONTAINER)
    expect(page.locator("#intake-failid .dropzone__file")).to_have_count(1)
    screenshots(page, "signed-containers-uus-teema-refusal")


# ---------------------------------------------------------------------------
# + Koja arvamus
# ---------------------------------------------------------------------------


def test_koja_arvamus_registers_an_asice_as_the_file_that_went_out(page, base_url, screenshots):
    sign_in(page, base_url, SANDRA)
    _new_teema(page, base_url, unique_title("Allkirjastatud arvamus"))
    content = signed_container()

    open_add_panel(page, "arvamus-koja")
    assert {".asice", ".bdoc"} <= _offered(page, "#id_koja_arvamus_fail")

    answer = _register_koja_arvamus(page, "Koja arvamus.asice", content)

    assert answer.status == 200, answer.status
    expect(page.locator("#arvamus-koja")).not_to_contain_text(NOT_ALLOWED)
    expect(page.locator("#ajalugu-loend")).to_contain_text("Arvamus välja")
    expect(page.locator(".tl-strip")).to_contain_text("Koja arvamus")
    screenshots(page, "signed-containers-koja-arvamus")

    _open_documents(page)
    expect(page.get_by_role("link", name="Koja arvamus.asice", exact=True)).to_be_visible()
    assert _downloaded(page, "Koja arvamus.asice") == ("Koja arvamus.asice", content)


def test_koja_arvamus_refuses_a_false_container_in_words_and_registers_nothing(
    page, base_url, screenshots
):
    sign_in(page, base_url, SANDRA)
    _new_teema(page, base_url, unique_title("Vale arvamuse ümbrik"))

    answer = _register_koja_arvamus(page, "Koja arvamus.asice", plain_zip())

    assert answer.status == 400, answer.status
    expect(page.locator("#arvamus-koja")).to_contain_text(NOT_A_CONTAINER)
    expect(page.locator("#ajalugu-loend")).not_to_contain_text("Arvamus välja")
    screenshots(page, "signed-containers-koja-arvamus-refusal")
