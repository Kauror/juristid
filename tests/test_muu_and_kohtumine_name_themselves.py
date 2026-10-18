"""A `Muu` and a `Kohtumine` name themselves wherever their files are read.

* **JQA-007** — Dokumendid's «Seotud kirje» line names an entry by its own kind:
  a meeting's protocol reads «Kohtumine · <day>», a `Muu`'s file «Muu · <day>»,
  and a note's «Märkus · <day>» as before (`app.matters.document_context`).
* **JUX-011** — the closed chronology line of a meeting or a `Muu` saved with a
  file says what the person did: «… lisas kohtumise ja lisas dokumendi», not
  «Sandra KOHTUMINE lisas dokumendi», which read as the meeting adding a file.
  One with no file still reads as its badge alone, like a note.
* **D13** — the `Muu` link-correction form says in one line that a correction is
  kept in the history and that a corrected Teema cannot be permanently deleted
  (docs/adr/0097, docs/adr/0165).
"""

from __future__ import annotations

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse

from app.documents.models import Document
from app.matters.document_context import related_records
from app.matters.entry_enums import EntryKind
from app.matters.models import Entry
from app.matters.services import add_entry
from app.matters.timeline import matter_timeline
from tests import factories
from tests.follow_ups import PDF

pytestmark = pytest.mark.django_db

DAY = "1.10.2026"

CREATORS = {
    EntryKind.MEETING: ("add_meeting", {"summary": "Kohtusime ministeeriumiga."}),
    EntryKind.OTHER: ("add_other", {"description": "Taustamaterjal analüüsiks.", "url": ""}),
}

#: The one line `Muuda` on a `Muu` link shows (owner decision D13).
CORRECTION_NOTE = (
    "Parandus jääb muudatuste ajalukku; parandatud sissekandega teemat ei saa jäädavalt kustutada."
)


def _pdf(name: str) -> SimpleUploadedFile:
    return SimpleUploadedFile(name, PDF + name.encode(), content_type="application/pdf")


def _create(client, matter, kind, *, with_file: bool = True) -> Entry:
    route, data = CREATORS[kind]
    payload = {"occurred_on": DAY, **data}
    if with_file:
        payload["attachments"] = [_pdf(f"{kind.lower()}.pdf")]
    response = client.post(
        reverse(f"matters:{route}", kwargs={"pk": matter.pk}),
        payload,
        headers={"HX-Request": "true"},
    )
    assert response.status_code == 200, response.content[:400]
    return Entry.objects.get(matter=matter, kind=kind)


def _row(matter, user, entry):
    items, _ = matter_timeline(matter=matter, user=user, limit=50)
    (row,) = [item for item in items if item.is_entry and item.entry.pk == entry.pk]
    return row


# -- JQA-007: Dokumendid «Seotud kirje» -----------------------------------------


@pytest.mark.parametrize(
    ("kind", "label"), [(EntryKind.MEETING, "Kohtumine"), (EntryKind.OTHER, "Muu")]
)
def test_the_seotud_kirje_line_names_the_entry_by_its_kind(signed_in, specialist, kind, label):
    matter = factories.MatterFactory(owner=specialist)
    _create(signed_in, matter, kind)
    (document,) = Document.objects.filter(matter=matter)

    (line,) = related_records([document], viewer=specialist)[document.pk]

    assert line.kind == label
    assert str(line) == f"{label} · 1.10.2026"


def test_the_documents_page_prints_the_kind_beside_the_file(signed_in, specialist):
    matter = factories.MatterFactory(owner=specialist)
    _create(signed_in, matter, EntryKind.MEETING)

    page = signed_in.get(
        reverse("matters:matter_documents", kwargs={"pk": matter.pk})
    ).content.decode()

    assert "Kohtumine · 1.10.2026" in page
    assert "Märkus · 1.10.2026" not in page


def test_a_note_s_file_still_reads_markus(specialist):
    matter = factories.MatterFactory(owner=specialist)
    note = add_entry(matter=matter, body="Tavaline märkus.", author=specialist)

    from app.matters.document_context import _context_of

    assert _context_of("entry", note, {}).kind == "Märkus"


# -- JUX-011: the closed chronology line ------------------------------------------


@pytest.mark.parametrize(
    ("kind", "clause"),
    [(EntryKind.MEETING, "lisas kohtumise"), (EntryKind.OTHER, "lisas materjali")],
)
def test_a_meeting_or_muu_with_a_file_says_what_was_added(signed_in, specialist, kind, clause):
    matter = factories.MatterFactory(owner=specialist)
    entry = _create(signed_in, matter, kind)

    assert _row(matter, specialist, entry).besides_the_note == f"{clause} ja lisas dokumendi"
    page = signed_in.get(reverse("matters:matter_detail", kwargs={"pk": matter.pk}))
    assert f'<span class="uxtl__did">{clause} ja lisas dokumendi</span>' in page.content.decode()


@pytest.mark.parametrize("kind", [EntryKind.MEETING, EntryKind.OTHER])
def test_one_with_no_file_reads_as_its_badge_alone(signed_in, specialist, kind):
    matter = factories.MatterFactory(owner=specialist)
    entry = _create(signed_in, matter, kind, with_file=False)

    assert _row(matter, specialist, entry).besides_the_note == ""


def test_a_note_keeps_the_bare_clause(specialist):
    """A note is what an authored row is: no «lisas märkuse» on its line."""
    note = add_entry(matter=factories.MatterFactory(owner=specialist), body="x", author=specialist)
    row = _row(note.matter, specialist, note)
    assert row.besides_the_note == ""


# -- D13: the Muu link-correction note --------------------------------------------


def test_the_muu_link_correction_form_says_what_a_correction_does(signed_in, specialist):
    matter = factories.MatterFactory(owner=specialist)
    entry = _create(signed_in, matter, EntryKind.OTHER, with_file=False)

    form = signed_in.get(
        reverse("matters:edit_entry", kwargs={"pk": matter.pk, "entry_id": entry.pk})
    ).content.decode()

    assert 'name="url"' in form
    assert CORRECTION_NOTE in form


def test_a_note_s_correction_form_has_no_such_line(signed_in, specialist):
    matter = factories.MatterFactory(owner=specialist)
    note = add_entry(matter=matter, body="Tavaline märkus.", author=specialist)

    form = signed_in.get(
        reverse("matters:edit_entry", kwargs={"pk": matter.pk, "entry_id": note.pk})
    ).content.decode()

    assert CORRECTION_NOTE not in form
