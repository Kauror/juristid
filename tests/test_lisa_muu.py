"""`+ Lisa · Muu` — supporting material on a Teema (docs/adr/0097, amendment of 2026-10-15).

One `Entry(kind=OTHER)`: a required `Kirjeldus`, an optional `Link` in the entry's
own `url` column, and files through the canonical evidence pipeline — saved
together or not at all. It is dated by the moment it is saved, creates no task,
moves no stage or plan, opens no wait and is never a Koja arvamus. Its link and
description are corrected through `edit_entry`, whose revision keeps both earlier
values. The browser half is `e2e/test_lisa_muu.py`.
"""

from __future__ import annotations

import re
from datetime import timedelta

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from django.utils import timezone

from app.audit.enums import ChangeEventType
from app.audit.models import ChangeEvent
from app.documents.models import Document
from app.matters.entry_enums import EntryKind
from app.matters.locks import CLOSED_MATTER_REFUSAL
from app.matters.models import Entry, EntryRevision, MatterEngagement
from app.matters.services import add_entry, edit_entry, entry_revision_token
from app.search.services import search_documents
from app.submissions.models import Submission
from app.workflow.models import NextAction
from tests import factories
from tests.follow_ups import PDF

pytestmark = pytest.mark.django_db


def _pdf(name: str = "taust.pdf", body: bytes = b"") -> SimpleUploadedFile:
    return SimpleUploadedFile(name, PDF + body, content_type="application/pdf")


def _post(client, matter, **data):
    payload = {"description": "", "url": ""}
    payload.update({k: v for k, v in data.items() if k != "files"})
    if data.get("files"):
        payload["attachments"] = data["files"]
    return client.post(
        reverse("matters:add_other", kwargs={"pk": matter.pk}),
        payload,
        headers={"HX-Request": "true"},
    )


def _page(client, matter) -> str:
    return client.get(reverse("matters:matter_detail", kwargs={"pk": matter.pk})).content.decode()


def _others(matter):
    return Entry.objects.filter(matter=matter, kind=EntryKind.OTHER)


# -- the launcher ---------------------------------------------------------------


def test_muu_is_the_fifth_choice_of_plus_lisa(signed_in, specialist):
    matter = factories.MatterFactory(owner=specialist)

    page = _page(signed_in, matter)

    family = page[page.index('id="lisa-marge"') :]
    chips = re.findall(
        r'<label class="disclosure-chip" for="marge-([a-z-]+)-valik">([^<]+)</label>', family
    )
    assert [label for _, label in chips[:5]] == [
        "Arvamuse tähtaeg",
        "Oluline tähtaeg",
        "Jõustumine",
        "Töövõit",
        "Muu",
    ]
    assert 'id="marge-muu"' in page
    assert 'name="description"' in page and 'id="id_muu_url"' in page
    assert "Kirjelda, mida lisad ja miks see on teema jaoks oluline." in page
    # Not the retired generic task form.
    assert "marge-tavaline" not in page


# -- saving ----------------------------------------------------------------------


def test_a_description_alone_saves_one_other_entry_by_its_author(signed_in, specialist):
    matter = factories.MatterFactory(owner=specialist)
    before = timezone.now()

    response = _post(signed_in, matter, description="Ministeeriumi taustamaterjal analüüsiks.")

    assert response.status_code == 200
    entry = _others(matter).get()
    assert entry.author == specialist
    assert "Ministeeriumi taustamaterjal" in entry.body
    assert entry.url == ""
    # Dated by the save itself: the record is of adding the material.
    assert before - timedelta(seconds=1) <= entry.occurred_at <= timezone.now()
    assert ChangeEvent.objects.filter(
        event_type=ChangeEventType.ENTRY_ADDED, object_id=entry.pk
    ).exists()


def test_a_link_is_normalised_stored_and_shown_by_its_host(signed_in, specialist):
    matter = factories.MatterFactory(owner=specialist)

    _post(
        signed_in,
        matter,
        description="Liidu avalik kommentaar.",
        url="  https://www.example.ee/kommentaar?id=7  ",
    )

    entry = _others(matter).get()
    assert entry.url == "https://www.example.ee/kommentaar?id=7"
    page = _page(signed_in, matter)
    assert 'href="https://www.example.ee/kommentaar?id=7"' in page
    assert "example.ee" in page
    assert 'rel="noopener noreferrer"' in page


@pytest.mark.parametrize(
    "bad",
    [
        "javascript:alert(1)",
        "ftp://example.ee/fail",
        "https://",
        "lihtsalt tekst",
        "https://example.ee/" + "a" * 1100,
    ],
)
def test_an_unsafe_or_malformed_link_is_refused_and_nothing_is_saved(signed_in, specialist, bad):
    matter = factories.MatterFactory(owner=specialist)

    response = _post(signed_in, matter, description="Kirjeldus jääb alles.", url=bad)

    assert response.status_code == 400
    assert not _others(matter).exists()
    # What was typed is still in the form.
    assert "Kirjeldus jääb alles." in response.content.decode()


def test_a_description_is_required_even_with_a_link_or_a_file(signed_in, specialist):
    matter = factories.MatterFactory(owner=specialist)

    response = _post(
        signed_in, matter, description="   ", url="https://example.ee/x", files=[_pdf()]
    )

    assert response.status_code == 400
    assert "Kirjelda, mida lisad." in response.content.decode()
    assert not _others(matter).exists()
    assert not Document.objects.filter(matter=matter).exists()


def test_several_files_are_captured_and_listed_under_dokumendid(signed_in, specialist):
    matter = factories.MatterFactory(owner=specialist)

    _post(
        signed_in,
        matter,
        description="Kaks taustadokumenti.",
        files=[_pdf("esimene.pdf", b"1"), _pdf("teine.pdf", b"2")],
    )

    entry = _others(matter).get()
    documents = Document.objects.filter(matter=matter)
    assert documents.count() == 2
    assert {link.entry_id for d in documents for link in d.links.all()} == {entry.pk}
    docs_page = signed_in.get(
        reverse("matters:matter_documents", kwargs={"pk": matter.pk})
    ).content.decode()
    assert "esimene.pdf" in docs_page and "teine.pdf" in docs_page
    # And in the chronology row.
    page = _page(signed_in, matter)
    assert "esimene.pdf" in page and "teine.pdf" in page


def test_a_refused_file_leaves_no_entry_behind(signed_in, specialist):
    matter = factories.MatterFactory(owner=specialist)
    not_a_pdf = SimpleUploadedFile("vale.pdf", b"MZ not a pdf", content_type="application/pdf")

    response = _post(signed_in, matter, description="Fail lükatakse tagasi.", files=[not_a_pdf])

    assert response.status_code == 400
    assert not _others(matter).exists()
    assert not Document.objects.filter(matter=matter).exists()


def test_a_repeated_identical_post_writes_its_own_row(signed_in, specialist):
    """Two saves are two records, as for every other `+ Lisa` choice; nothing merges
    them silently. (The browser's double-submit guard keeps a double click to one.)"""
    matter = factories.MatterFactory(owner=specialist)

    _post(signed_in, matter, description="Sama tekst.")
    _post(signed_in, matter, description="Sama tekst.")

    assert _others(matter).count() == 2


# -- what it must not do -----------------------------------------------------------


def test_saving_changes_no_workflow_state(signed_in, specialist):
    matter = factories.MatterFactory(owner=specialist)
    matter.refresh_from_db()
    stage, deadline, is_open = matter.stage_id, matter.response_deadline, matter.is_open
    actions = NextAction.objects.filter(matter=matter).count()

    _post(signed_in, matter, description="Ei loo tööd.", url="https://example.ee/a", files=[_pdf()])

    matter.refresh_from_db()
    assert (matter.stage_id, matter.response_deadline, matter.is_open) == (
        stage,
        deadline,
        is_open,
    )
    assert NextAction.objects.filter(matter=matter).count() == actions
    assert not MatterEngagement.objects.filter(matter=matter).exists()
    assert not Submission.objects.filter(matter=matter).exists()


def test_a_closed_matter_refuses_a_new_entry(signed_in, specialist):
    matter = factories.MatterFactory(
        owner=specialist, is_open=False, disposition="COMPLETED", closed_at=timezone.now()
    )

    response = _post(signed_in, matter, description="Liiga hilja.")

    assert response.status_code == 400
    assert CLOSED_MATTER_REFUSAL in response.content.decode()
    assert not _others(matter).exists()


def test_a_reader_cannot_add_one(client, reader, specialist):
    matter = factories.MatterFactory(owner=specialist)
    client.force_login(reader)

    response = _post(client, matter, description="Lugeja ei kirjuta.")

    assert response.status_code == 404
    assert not _others(matter).exists()


# -- visibility ----------------------------------------------------------------------


def test_search_finds_it_for_a_lawyer_and_never_for_a_reader_of_a_restricted_matter(
    signed_in, specialist, reader, restricted_matter
):
    _post(signed_in, restricted_matter, description="Konfidentsiaalne taustamaterjal kalmaar.")

    assert search_documents(query="kalmaar", user=specialist).exists()
    assert not search_documents(query="kalmaar", user=reader).exists()


def test_a_restricted_matters_file_is_not_downloadable_by_a_reader(
    client, signed_in, specialist, reader, restricted_matter
):
    _post(signed_in, restricted_matter, description="Piiratud fail.", files=[_pdf()])
    version = Document.objects.get(matter=restricted_matter).current_version
    client.force_login(reader)

    response = client.get(reverse("documents:download", kwargs={"pk": version.pk}))

    assert response.status_code == 404


# -- correction ------------------------------------------------------------------------


def _edit(client, matter, entry, **data):
    return client.post(
        reverse("matters:edit_entry", kwargs={"pk": matter.pk, "entry_id": entry.pk}),
        data,
        headers={"HX-Request": "true"},
    )


def test_correcting_the_link_keeps_the_earlier_one_in_the_revision(signed_in, specialist):
    matter = factories.MatterFactory(owner=specialist)
    _post(signed_in, matter, description="Algne kirjeldus.", url="https://example.ee/vana")
    entry = _others(matter).get()

    response = _edit(
        signed_in,
        matter,
        entry,
        body=entry.body,
        url="https://example.ee/uus",
        revision=entry_revision_token(entry),
    )

    assert response.status_code == 200
    entry.refresh_from_db()
    assert entry.url == "https://example.ee/uus"
    revision = EntryRevision.objects.get(entry=entry)
    assert revision.url == "https://example.ee/vana"
    assert revision.body == entry.body
    assert entry.edit_count == 1


def test_a_stale_correction_is_refused_and_writes_nothing(signed_in, specialist):
    matter = factories.MatterFactory(owner=specialist)
    _post(signed_in, matter, description="Algne.", url="https://example.ee/a")
    entry = _others(matter).get()
    stale = entry_revision_token(entry)
    edit_entry(entry=entry, body="Kolleegi parandus.", actor=specialist, expected_revision=stale)

    response = _edit(
        signed_in, matter, entry, body="Minu parandus.", url="https://example.ee/b", revision=stale
    )

    assert response.status_code == 409
    entry.refresh_from_db()
    assert entry.url == "https://example.ee/a"
    assert "Kolleegi parandus." in entry.body


def test_an_unsafe_corrected_link_is_refused(signed_in, specialist):
    matter = factories.MatterFactory(owner=specialist)
    _post(signed_in, matter, description="Algne.", url="https://example.ee/a")
    entry = _others(matter).get()

    response = _edit(
        signed_in,
        matter,
        entry,
        body=entry.body,
        url="javascript:alert(1)",
        revision=entry_revision_token(entry),
    )

    assert response.status_code == 400
    entry.refresh_from_db()
    assert entry.url == "https://example.ee/a"
    assert not EntryRevision.objects.filter(entry=entry).exists()


# -- every other entry is untouched -----------------------------------------------------


def test_other_entry_kinds_keep_the_body_only_correction_and_an_empty_url(signed_in, specialist):
    matter = factories.MatterFactory(owner=specialist)
    note = add_entry(matter=matter, body="Tavaline märkus.", author=specialist)

    form_html = signed_in.get(
        reverse("matters:edit_entry", kwargs={"pk": matter.pk, "entry_id": note.pk})
    ).content.decode()
    _edit(
        signed_in,
        matter,
        note,
        body="Parandatud märkus.",
        url="https://example.ee/eiri",
        revision=entry_revision_token(note),
    )

    assert 'name="url"' not in form_html
    note.refresh_from_db()
    assert note.url == ""
    assert "Parandatud märkus." in note.body
    assert EntryRevision.objects.get(entry=note).url == ""
