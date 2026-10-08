"""An opinion's working documents, in a browser (JUR-CASE-06/-12, docs/adr/0129).

`tests/test_opinion_working_documents.py` and `tests/test_document_record_context.py`
hold the contract against the database. This file holds what only a rendered
page settles, on the flows the living-dossier QA walked:

    A. a Koja arvamus with a signed `.asice` as `Saadetud fail` and a DOCX as a
       working document, ticked to finish the open step — one «Arvamus välja»
       row, the two files under it in two labelled groups, the step done, the
       next-step control offered, and Dokumendid naming the opinion on the DOCX
       while the two keep their own roles and download as themselves;
    B. two working documents in one save, both under the same opinion;
    C. `+ Lisa töödokument` on an opinion already sent — no second row;
    D. two opinions on one Teema told apart in the rail, each with its own
       working document;
    E. a restricted Teema: the owner reads all of it, a reader reaches none of
       it — not the page, not the file, not a search hit.

Every container is built at test time (`tests/synthetic_containers.py`) and every
Teema is one this file creates.
"""

from __future__ import annotations

import re
from datetime import date, timedelta
from pathlib import Path

import pytest
from playwright.sync_api import expect

from e2e.conftest import (
    KAIK_ROW,
    READER,
    SANDRA,
    create_matter,
    open_add_panel,
    open_kaik_row,
    sign_in,
    sign_out,
    unique_title,
)
from e2e.legacy_opinions import _in_the_server, matter_id_of
from tests.synthetic_containers import signed_container

pytestmark = pytest.mark.e2e

MINISTRY = "Näidisministeerium"
STEP = "Vormista ja saada Koja seisukoht"
OPTION = "Märgi praegune tegevus tehtuks"
CTA = "+ Lisa tegevus"
DOCX = "16 03 2023 arvamus seoses juristieksami seaduse eelnõuga.docx"
DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


def _docx_bytes(name: str) -> bytes:
    """An OOXML-shaped file: the ZIP signature the upload door reads."""
    return b"PK\x03\x04synthetic " + name.encode()


def _day(offset: int) -> date:
    return date.today() + timedelta(days=offset)


def _et(day: date) -> str:
    return f"{day.day}.{day.month}.{day.year}"


def _set_step(page) -> None:
    """`+ Lisa tegevus` — the open step the opinion will finish."""
    cta = page.locator("#praegune-tegevus #lisa-jargmine")
    open_add_panel(page, "lisa-jargmine")
    cta.locator("[name='text']").fill(STEP)
    page.locator("#id_target_date").fill(_et(_day(2)))
    with page.expect_response(
        lambda response: response.url.endswith("/jargmiseks/") and response.request.method == "POST"
    ) as caught:
        cta.get_by_role("button", name="Salvesta", exact=True).click()
    assert caught.value.status == 200, caught.value.status
    page.wait_for_load_state("networkidle")


def _register(
    page,
    *,
    sent: tuple[str, bytes],
    working: list[str],
    sent_on: date,
    finish_step: bool = False,
) -> None:
    """`+ Koja arvamus` with the letter, then its working documents on its row.

    The panel takes only what went out since docs/adr/0144 §5; the working
    documents are added where the product offers them — `+ Lisa töödokument`
    on the sent opinion's own row — right after the send.
    """
    open_add_panel(page, "arvamus-koja")
    form = page.locator("#arvamus-koja")
    expect(form.locator("input[name=working_files]")).to_have_count(0)
    form.locator("input[name=upload]").set_input_files(
        {"name": sent[0], "mimeType": "application/vnd.etsi.asic-e+zip", "buffer": sent[1]}
    )
    form.locator("[name=sent_on]").fill(_et(sent_on))
    if finish_step:
        option = form.get_by_role("checkbox", name=OPTION)
        expect(option).not_to_be_checked()
        option.check()
    with page.expect_response(
        lambda response: (
            response.url.endswith("/lisa/koja-arvamus/") and response.request.method == "POST"
        )
    ) as caught:
        form.get_by_role("button", name="Salvesta", exact=True).click()
    assert caught.value.status == 200, f"the opinion was refused: {caught.value.status}"
    page.wait_for_load_state("networkidle")
    if working:
        _add_working_documents(page, working)


def _add_working_documents(page, names: list[str]) -> None:
    """`+ Lisa töödokument` on the newest sent opinion's row."""
    row = _opinion_rows(page).first
    open_kaik_row(row)
    row.get_by_role("button", name=re.compile(r"^\+ Lisa töödokument")).click()
    picker = page.get_by_role("form", name="Töödokumendi lisamine Koja arvamusele")
    expect(picker).to_be_visible()
    picker.locator("input[type=file]").set_input_files(
        [{"name": name, "mimeType": DOCX_MIME, "buffer": _docx_bytes(name)} for name in names]
    )
    with page.expect_response(
        lambda response: "/lisa-toodokument/" in response.url and response.request.method == "POST"
    ) as caught:
        picker.get_by_role("button", name="Salvesta", exact=True).click()
    assert caught.value.status == 200, caught.value.status
    page.wait_for_load_state("networkidle")


def _opinion_rows(page):
    return page.locator(KAIK_ROW).filter(has_text="Arvamus välja")


def _downloaded(page, link) -> tuple[str, bytes]:
    with page.expect_download() as arrival:
        link.click()
    download = arrival.value
    return download.suggested_filename, Path(download.path()).read_bytes()


def _open_documents(page, matter_url: str) -> None:
    page.goto(f"{matter_url}dokumendid/")
    page.wait_for_load_state("networkidle")


def _document_row(page, name: str):
    return page.locator("table.doctable tbody tr").filter(has_text=name)


# ---------------------------------------------------------------------------
# A. The letter and its working document, in one press
# ---------------------------------------------------------------------------


def test_a_signed_letter_and_its_docx_in_one_opinion(page, base_url, screenshots):
    sign_in(page, base_url, SANDRA)
    url = create_matter(page, base_url, unique_title("Tööfailiga arvamus"), sender=MINISTRY)
    _set_step(page)
    container = signed_container()

    open_add_panel(page, "arvamus-koja")
    # The panel takes only what went out (docs/adr/0144 §5); the working
    # document was added on the sent opinion's row by `_register`.
    expect(page.locator("#arvamus-koja")).not_to_contain_text("Töödokumendid")
    screenshots(page, "toodokument-koja-arvamus-paneel")
    _register(
        page,
        sent=("koda_opinion.asice", container),
        working=[DOCX],
        sent_on=_day(0),
        finish_step=True,
    )

    # Exactly one substantive row, no `Märge`, no «lisas dokumendi».
    expect(_opinion_rows(page)).to_have_count(1)
    expect(page.locator(KAIK_ROW).filter(has_text="lisas dokumendi")).to_have_count(0)
    expect(page.locator(KAIK_ROW).filter(has_text="Märge")).to_have_count(0)
    # The step is done and the zone offers the next one.
    zone = page.locator("#praegune-tegevus")
    expect(zone).to_contain_text("Järgmine samm on määramata")
    expect(zone.locator("#lisa-jargmine > summary")).to_have_text(CTA)

    row = _opinion_rows(page).first
    open_kaik_row(row)
    groups = row.locator(".uxtl__filegroup")
    expect(groups).to_have_count(2)
    expect(groups.nth(0).locator(".uxtl__filegrouplabel")).to_have_text("Saadetud")
    expect(groups.nth(0)).to_contain_text("koda_opinion.asice")
    expect(groups.nth(0)).not_to_contain_text(DOCX)
    expect(groups.nth(1).locator(".uxtl__filegrouplabel")).to_have_text("Töödokumendid")
    expect(groups.nth(1)).to_contain_text(DOCX)
    expect(row).to_contain_text("Tehtud")
    screenshots(page, "toodokument-teema-kaik")

    # Dokumendid: both files, their own roles, and the DOCX names the opinion.
    _open_documents(page, url)
    letter = _document_row(page, "koda_opinion.asice")
    working = _document_row(page, DOCX)
    expect(letter).to_contain_text("Arvamus")
    expect(letter).to_contain_text(f"Saadetud {_et(_day(0))}")
    # No Roll column since 2026-10-07: the badge beside the name says it.
    expect(working.locator(".badge", has_text="Töödokument")).to_be_visible()
    expect(working.locator(".doctable__context")).to_have_text(
        f"Seotud kirje: Koja arvamus · {_et(_day(0))} · {MINISTRY}"
    )
    expect(page.locator("#toodokumendid .accordion__title")).to_have_text("SharePointi viited")
    screenshots(page, "toodokument-dokumendid")

    # Each downloads as itself: the exact container, and the DOCX.
    assert _downloaded(
        page, page.get_by_role("link", name="Tõmba alla koda_opinion.asice", exact=True)
    ) == ("koda_opinion.asice", container)
    assert _downloaded(page, page.get_by_role("link", name=f"Tõmba alla {DOCX}", exact=True)) == (
        DOCX,
        _docx_bytes(DOCX),
    )


# ---------------------------------------------------------------------------
# B. Several working documents in one save
# ---------------------------------------------------------------------------


def test_two_working_documents_belong_to_the_same_opinion(page, base_url):
    sign_in(page, base_url, SANDRA)
    url = create_matter(page, base_url, unique_title("Kaks tööfaili"), sender=MINISTRY)

    _register(
        page,
        sent=("koda_opinion.asice", signed_container()),
        working=["arvamus.docx", "lisa tabel.docx"],
        sent_on=_day(-1),
    )

    expect(_opinion_rows(page)).to_have_count(1)
    row = _opinion_rows(page).first
    open_kaik_row(row)
    working = row.locator(".uxtl__filegroup").filter(has_text="Töödokumendid")
    expect(working.locator(".uxtl__file")).to_have_count(2)

    _open_documents(page, url)
    expected = f"Seotud kirje: Koja arvamus · {_et(_day(-1))} · {MINISTRY}"
    for name in ("arvamus.docx", "lisa tabel.docx"):
        expect(_document_row(page, name).locator(".doctable__context")).to_have_text(expected)


# ---------------------------------------------------------------------------
# C. After the send
# ---------------------------------------------------------------------------


def test_a_working_document_added_after_the_send(page, base_url, screenshots):
    sign_in(page, base_url, SANDRA)
    create_matter(page, base_url, unique_title("Hiljem lisatud tööfail"), sender=MINISTRY)
    _register(page, sent=("koda_opinion.asice", signed_container()), working=[], sent_on=_day(-2))
    rows_before = page.locator(KAIK_ROW).count()

    row = _opinion_rows(page).first
    open_kaik_row(row)
    expect(row.locator(".uxtl__filegroup")).to_have_count(0)
    row.get_by_role("button", name=re.compile(r"^\+ Lisa töödokument")).click()
    picker = page.get_by_role("form", name="Töödokumendi lisamine Koja arvamusele")
    expect(picker).to_be_visible()
    picker.locator("input[type=file]").set_input_files(
        {"name": DOCX, "mimeType": DOCX_MIME, "buffer": _docx_bytes(DOCX)}
    )
    screenshots(page, "toodokument-lisa-hiljem")
    with page.expect_response(
        lambda response: "/lisa-toodokument/" in response.url and response.request.method == "POST"
    ) as caught:
        picker.get_by_role("button", name="Salvesta", exact=True).click()
    assert caught.value.status == 200, caught.value.status
    page.wait_for_load_state("networkidle")

    # The same row, now with the working document; no row was added.
    expect(page.locator(KAIK_ROW)).to_have_count(rows_before)
    expect(_opinion_rows(page)).to_have_count(1)
    row = _opinion_rows(page).first
    open_kaik_row(row)
    expect(row.locator(".uxtl__filegroup").filter(has_text="Töödokumendid")).to_contain_text(DOCX)
    expect(row.locator(".uxtl__filegroup").filter(has_text="Saadetud")).to_contain_text(
        "koda_opinion.asice"
    )


# ---------------------------------------------------------------------------
# D. Two opinions on one Teema
# ---------------------------------------------------------------------------


def test_two_opinions_are_told_apart_in_the_rail_and_on_dokumendid(page, base_url, screenshots):
    sign_in(page, base_url, SANDRA)
    url = create_matter(page, base_url, unique_title("Kaks arvamust"), sender=MINISTRY)
    first_day, second_day = _day(-23), _day(0)

    _register(
        page,
        sent=("koda_opinion.asice", signed_container()),
        working=["esimene.docx"],
        sent_on=first_day,
    )
    _register(
        page,
        sent=("koda_opinion.asice", signed_container(deflated_mimetype=True)),
        working=["teine.docx"],
        sent_on=second_day,
    )

    expect(_opinion_rows(page)).to_have_count(2)
    rail = page.locator("#koja-arvamus")
    lines = rail.locator(".railcard__row--opinion")
    expect(lines).to_have_count(2)
    first = lines.filter(has_text=f"{_et(first_day)} · {MINISTRY}")
    second = lines.filter(has_text=f"{_et(second_day)} · {MINISTRY}")
    expect(first).to_contain_text("esimene.docx")
    expect(first).not_to_contain_text("teine.docx")
    expect(second).to_contain_text("teine.docx")
    expect(second).not_to_contain_text("esimene.docx")
    screenshots(page, "toodokument-rail-kaks-arvamust")

    _open_documents(page, url)
    expect(_document_row(page, "esimene.docx").locator(".doctable__context")).to_contain_text(
        _et(first_day)
    )
    expect(_document_row(page, "teine.docx").locator(".doctable__context")).to_contain_text(
        _et(second_day)
    )


# ---------------------------------------------------------------------------
# E. A restricted Teema
# ---------------------------------------------------------------------------

#: The Teema restricted the way an administrator restricts one — through the
#: canonical service, in the server's own process. The ordinary Teema product
#: has no control for it any more (docs/adr/0096 §3), so this is the honest
#: way to reach the state (`e2e/legacy_opinions.py`).
_RESTRICT_SCRIPT = (
    "import os;"
    "from django.contrib.auth import get_user_model;"
    "from app.core.enums import Visibility;"
    "from app.matters.models import Matter;"
    "from app.matters.services import set_matter_visibility;"
    "m = Matter.objects.get(pk=os.environ['E2E_LEGACY_MATTER']);"
    "u = get_user_model().objects.get(upn=os.environ['E2E_LEGACY_ACTOR']);"
    "set_matter_visibility(matter=m, visibility=Visibility.RESTRICTED, actor=u);"
    "print(m.pk)"
)


def test_a_restricted_teema_leaks_neither_the_file_nor_its_opinion(page, base_url, screenshots):
    sign_in(page, base_url, SANDRA)
    name = "Konfidentsiaalne tööversioon.docx"
    url = create_matter(
        page, base_url, unique_title("Piiratud tööfail"), sender=MINISTRY, owner=SANDRA
    )
    _register(
        page, sent=("koda_opinion.asice", signed_container()), working=[name], sent_on=_day(0)
    )
    _in_the_server(
        _RESTRICT_SCRIPT,
        {"E2E_LEGACY_MATTER": matter_id_of(url), "E2E_LEGACY_ACTOR": SANDRA.upn},
    )

    # The owner still reads all of it.
    _open_documents(page, url)
    working = _document_row(page, name)
    expect(working.locator(".doctable__context")).to_contain_text("Koja arvamus")
    download = page.get_by_role("link", name=f"Tõmba alla {name}", exact=True).get_attribute("href")
    assert download

    sign_out(page, base_url)
    sign_in(page, base_url, READER)
    for address in (url, f"{url}dokumendid/", f"{base_url}{download}"):
        response = page.goto(address)
        assert response is not None and response.status == 404, (
            address,
            response and response.status,
        )
    page.goto(f"{base_url}/otsing/?q=tööversioon")
    page.wait_for_load_state("networkidle")
    expect(page.locator("main")).not_to_contain_text(name)
    expect(page.locator("main")).not_to_contain_text("Koja arvamus ·")
    screenshots(page, "toodokument-piiratud-lugeja")
