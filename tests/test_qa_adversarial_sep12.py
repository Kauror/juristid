"""Regression tests for the 12 September adversarial UI/workflow QA round.

TESTS ONLY — EVERY TEST HERE IS EXPECTED TO FAIL until the product fixes land.

Each test reproduces a defect found by using the application the way an
ordinary user does, on `main` at 5c0b2a2. None of them encodes a particular
repair: each asserts the *behaviour a user is entitled to*, and leaves the
design of the fix open.

The findings are QA-02, QA-05, QA-06 and QA-07 of that round; QA-01 and QA-08
are browser-visible and live in ``e2e/test_qa_adversarial_sep12.py``.
"""

from __future__ import annotations

import re

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse

from app.documents.enums import DocumentRole
from app.matters.services import close_matter
from tests import factories
from tests import synthetic_corpus as corpus

pytestmark = pytest.mark.django_db

STAGE = reverse("matters:intake_stage")
REGISTER = reverse("matters:matter_list")

PDF = "application/pdf"


def upload(name: str, content: bytes, content_type: str = PDF) -> SimpleUploadedFile:
    return SimpleUploadedFile(name, content, content_type=content_type)


def letter_pdf() -> bytes:
    return corpus.text_pdf(["Näidisministeerium", "Pakendiseaduse muutmise eelnõu"])


# ---------------------------------------------------------------------------
# QA-02 — several refused files, one refusal reported, and it names no file
# ---------------------------------------------------------------------------


def test_qa02_every_refused_intake_file_is_named(signed_in, evidence_root):
    """Choose four files, three of them unusable: the person is told about all three.

    Observed on `main`: the panel reports only the *first* refusal
    («Faililaiend .exe ei ole lubatud…») and never says which file it is
    about. The empty `.pdf` and the `.pdf` whose bytes are not a PDF are
    dropped without a word, so somebody who chose four files, sees one in the
    list and one line of red, cannot tell that two more are missing — and
    finds out one save at a time, which is exactly what
    `_read_new_matter_files`'s own docstring says this design prevents.

    The assertion is not "show three messages". It is that a file the
    application refused is a file the person is told about by name.
    """
    answer = signed_in.post(
        STAGE,
        {
            "files": [
                upload("hea.pdf", letter_pdf()),
                upload("paha.exe", b"MZ\x90\x00", content_type="application/octet-stream"),
                upload("tyhi.pdf", b""),
                upload("vale.pdf", b"See ei ole PDF."),
            ]
        },
    )

    assert answer.status_code == 400
    reported = answer.context["intake_error"]
    assert [row["filename"] for row in answer.context["intake_files"]] == ["hea.pdf"]
    for refused in ("paha.exe", "tyhi.pdf", "vale.pdf"):
        assert refused in reported, (
            f"{refused} was refused and dropped, and the page never says so: {reported!r}"
        )


# ---------------------------------------------------------------------------
# QA-05 — the title a person gives a document is stored and never shown
# ---------------------------------------------------------------------------


def test_qa05_a_document_title_a_person_typed_is_readable_on_the_page(
    signed_in, specialist, evidence_root
):
    """Two uploads of one filename are told apart by the titles their uploader gave.

    Observed on `main`: `Document.title` is saved, reaches the `<title>` element
    and the `aria-label` of the row's icon links — and appears nowhere a sighted
    reader can see it. Both the documents table and the document page print
    `original_filename`. Upload the same letter twice under two titles and the
    list shows two rows that are identical in every visible column: filename,
    role, date and uploader.
    """
    matter = factories.MatterFactory(owner=specialist)
    url = reverse("documents:upload_evidence", kwargs={"matter_id": matter.pk})

    for title in ("Ministeeriumi saatekiri", "Sama fail, parandatud saatekiri"):
        answer = signed_in.post(
            url,
            {
                "upload": upload("kiri.pdf", letter_pdf()),
                "role": DocumentRole.INCOMING_AUTHORITY,
                "title": title,
            },
        )
        assert answer.status_code in (200, 302), answer.status_code

    page = signed_in.get(
        reverse("matters:matter_documents", kwargs={"pk": matter.pk})
    ).content.decode()
    # Strip the attributes that already carry the title, so the assertion is
    # about what is on the page rather than about what a screen reader is told.
    visible = re.sub(r'(?:aria-label|title|alt)="[^"]*"', "", page)
    for title in ("Ministeeriumi saatekiri", "Sama fail, parandatud saatekiri"):
        assert title in visible, (
            f"the title {title!r} the uploader typed is nowhere a reader can see it"
        )


# ---------------------------------------------------------------------------
# QA-06 — a refusal on a closed Matter throws the typed text away
# ---------------------------------------------------------------------------


def test_qa06_a_closed_matter_refusal_keeps_what_the_person_wrote(
    signed_in, specialist, evidence_root
):
    """The stale-tab refusal says «salvesta uuesti» while discarding the note.

    Two tabs, the ordinary way a lawyer works. Tab A closes the Teema; tab B,
    which has a half-written Märge in it, presses Salvesta. The refusal is
    correct, clear and writes nothing — that part is right. What it also does
    is re-render the workspace *without* the closed Matter's add panels, so the
    text is gone from the page, and the message «Kui töö jätkub, taasava teema
    ja salvesta uuesti» asks the person to save something the page is no longer
    holding. A long note has to be retyped from memory.
    """
    matter = factories.MatterFactory(owner=specialist)
    close_matter(matter=matter, disposition="COMPLETED", actor=specialist)

    written = "Ministeerium lubas telefonis, et tähtaega pikendatakse kahe nädala võrra."
    answer = signed_in.post(
        reverse("matters:add_note", kwargs={"pk": matter.pk}),
        {"body": written},
        headers={"HX-Request": "true"},
    )

    body = answer.content.decode()
    assert "suletud" in body.lower(), "the refusal must say the Teema is closed"
    assert matter.entries.count() == 0, "nothing may be written to a closed Matter"
    assert written in body, "the refusal threw away the text it is asking the person to re-save"


# ---------------------------------------------------------------------------
# QA-07 — a year filter it cannot parse answers «no results»
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "typed",
    [
        "2020–2026",  # en dash: what Word and a pasted range produce
        "2026/2026",
        "eelmine aasta",
    ],
)
def test_qa07_a_year_filter_that_cannot_be_read_says_so(signed_in, specialist, typed):
    """`Aasta` is a free-text box, and an unreadable value reads as «no work».

    Observed on `main`: `?aasta=2020-2026` returns every Matter and
    `?aasta=2020–2026` — the same range with the dash a word processor
    substitutes — returns «0 teemat · Selle filtriga teemasid ei leitud».
    Nothing on the page says the filter was not understood, so a lawyer
    checking whether anything is outstanding is told there is nothing, by a
    filter that never ran.

    The assertion is that the page does not answer an unreadable filter with a
    confident empty result; whether it explains, ignores the value or refuses
    is a product decision.
    """
    factories.MatterFactory(owner=specialist, reporting_year=2026)

    page = signed_in.get(REGISTER, {"aasta": typed, "olek": "koik"})
    assert page.status_code == 200

    if page.context["total"] > 0:
        # The filter was understood well enough to keep the Matter. Nothing to say.
        return

    body = page.content.decode().lower()
    assert any(word in body for word in ("ei saanud", "ei mõista", "vigane", "loetav")), (
        f"aasta={typed!r} narrowed the register to nothing and the page reports it as "
        "«teemasid ei leitud» — a filter that never ran, presented as an answer"
    )


# ---------------------------------------------------------------------------
# A count of one is not «1 teemat»
# ---------------------------------------------------------------------------


def test_qa_estonian_counts_of_one_are_nominative(signed_in, specialist):
    """`1 teemat` is the partitive Estonian uses from two upwards.

    Every count on every surface is rendered as `N + partitive`, which is right
    for 0 and for 2+ and wrong for exactly 1: the register header says
    «1 teemat», Osakond says «1 avatud teemat», the documents tab says
    «1 faili» and the chronology says «1 kirjet». A native reader sees the
    slip on the first screen.
    """
    factories.MatterFactory(owner=specialist)

    body = signed_in.get(REGISTER).content.decode()

    assert "1 teemat" not in body, "a count of one takes the nominative: «1 teema»"
