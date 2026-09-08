"""A refused save must not throw away the files it was given.

The reported defect, and the one thing about it that made it expensive: it was
silent. A browser cannot put a file back into a file input — the value of
``<input type="file">`` is settable only to the empty string — so every answer
that re-rendered `Uus teema` came back with the file area empty. Somebody who
chose a file, was told about a valdkond they had ticked without naming,
corrected it and pressed the button again filed a Matter with no documents, and
was told about neither the loss nor the consequence.

The upload had reached the server and been validated by then. It was dropped
because the answer was a page rather than a redirect.

These tests are about the hold that fixes it (`app/documents/pending.py`) and
about the two properties it must not cost: the batch is still all or nothing,
and a held file is still ordinary evidence when it finally lands.
"""

from __future__ import annotations

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse

from app.documents import pending
from app.documents.models import Document, DocumentVersion
from app.documents.uploads import AcceptedUpload
from app.matters.models import Matter
from app.organisations.models import Organisation
from tests import factories
from tests import synthetic_corpus as corpus

pytestmark = pytest.mark.django_db

CREATE = reverse("matters:matter_create")


def upload(name: str, content: bytes, content_type: str = "application/pdf"):
    return SimpleUploadedFile(name, content, content_type=content_type)


def held_keys(response) -> list[str]:
    """The `pending` keys the refused form is carrying back."""
    return [item.key for item in response.context["held_files"]]


# ---------------------------------------------------------------------------
# The holding area itself
# ---------------------------------------------------------------------------


def test_a_held_file_comes_back_byte_for_byte(client, evidence_root):
    session = client.session
    original = AcceptedUpload(
        content=b"%PDF-1.4 hoitud", filename="a.pdf", mime_type="application/pdf"
    )

    [item] = pending.hold(session, [original])
    [resumed] = pending.resume(session, [item.key])

    assert resumed.content == original.content
    assert resumed.filename == "a.pdf"
    assert resumed.mime_type == "application/pdf"


def test_resuming_does_not_consume_the_hold(client, evidence_root):
    """A save can be refused twice. The second refusal must keep the file too."""
    session = client.session
    [item] = pending.hold(
        session,
        [AcceptedUpload(content=b"%PDF-1.4 x", filename="a.pdf", mime_type="application/pdf")],
    )

    assert pending.resume(session, [item.key])
    assert pending.resume(session, [item.key])


def test_a_key_this_session_is_not_holding_reads_nothing(client, evidence_root):
    """The authorization boundary. Guessing a key gets nothing, because the
    lookup is a dictionary on the requesting session and not a query."""
    holder = client.session
    [item] = pending.hold(
        holder,
        [AcceptedUpload(content=b"%PDF-1.4 x", filename="a.pdf", mime_type="application/pdf")],
    )

    stranger: dict = {}
    assert pending.resume(stranger, [item.key]) == []
    assert pending.describe(stranger, [item.key]) == []


def test_release_forgets_the_file_and_deletes_its_bytes(client, evidence_root):
    session = client.session
    [item] = pending.hold(
        session,
        [AcceptedUpload(content=b"%PDF-1.4 x", filename="a.pdf", mime_type="application/pdf")],
    )

    pending.release(session, [item.key])

    assert pending.resume(session, [item.key]) == []
    assert not pending.pending_storage().exists(item.key)


def test_the_size_reads_the_way_the_browser_writes_it(client, evidence_root):
    session = client.session
    [item] = pending.hold(
        session,
        [AcceptedUpload(content=b"x" * 2048, filename="a.pdf", mime_type="application/pdf")],
    )

    assert item.human_size == "2 KB"


# ---------------------------------------------------------------------------
# Through Uus teema
# ---------------------------------------------------------------------------


def test_a_refused_save_holds_the_file_and_the_next_one_files_it(
    signed_in, specialist, evidence_root
):
    """The reported journey, end to end, with the refusal in the middle."""
    refused = signed_in.post(
        CREATE,
        {
            "title": "Keeldumise järel",
            # «Muu» ticked with nothing written beside it: a refusal only the
            # server can make, so the browser sends the file and gets a page.
            "policy_area_other_selected": "on",
            "files": upload("kaaskiri.pdf", corpus.government_pdf()),
        },
    )

    assert refused.status_code == 400
    assert not Matter.objects.filter(title="Keeldumise järel").exists()
    keys = held_keys(refused)
    assert len(keys) == 1
    assert refused.context["held_files"][0].filename == "kaaskiri.pdf"

    signed_in.post(
        CREATE,
        {
            "title": "Keeldumise järel",
            "policy_area_other_selected": "on",
            "policy_area_other": "Ehitus",
            "pending": keys,
        },
    )

    matter = Matter.objects.get(title="Keeldumise järel")
    version = DocumentVersion.objects.get(document__matter=matter)
    assert version.original_filename == "kaaskiri.pdf"
    assert version.size_bytes > 0


def test_a_held_file_becomes_ordinary_evidence(signed_in, specialist, evidence_root):
    """Held is not stored. Nothing about the hold skips the evidence path: the
    Document, its role, its immutable version and its scan state are what they
    would have been had the first save succeeded.
    """
    from app.documents.enums import DocumentRole, MalwareScanState

    refused = signed_in.post(
        CREATE,
        {
            "title": "Tavaline tõend",
            "policy_area_other_selected": "on",
            "files": upload("kaaskiri.pdf", corpus.government_pdf()),
        },
    )
    signed_in.post(
        CREATE,
        {
            "title": "Tavaline tõend",
            "policy_area_other_selected": "on",
            "policy_area_other": "Ehitus",
            "pending": held_keys(refused),
        },
    )

    matter = Matter.objects.get(title="Tavaline tõend")
    document = Document.objects.get(matter=matter)
    version = document.current_version

    assert document.role == DocumentRole.INCOMING_AUTHORITY
    assert version is not None
    assert version.sha256
    assert version.malware_scan_state == MalwareScanState.PENDING


def test_a_held_file_dropped_from_the_form_is_not_filed(signed_in, specialist, evidence_root):
    """Taking a held row off the page stops its key being posted, and that is
    the whole mechanism — there is nothing to tell the server."""
    refused = signed_in.post(
        CREATE,
        {
            "title": "Loobutud",
            "policy_area_other_selected": "on",
            "files": upload("kaaskiri.pdf", corpus.government_pdf()),
        },
    )
    assert held_keys(refused)

    signed_in.post(
        CREATE,
        {
            "title": "Loobutud",
            "policy_area_other_selected": "on",
            "policy_area_other": "Ehitus",
        },
    )

    matter = Matter.objects.get(title="Loobutud")
    assert Document.objects.filter(matter=matter).count() == 0


def test_a_held_file_and_a_newly_chosen_one_are_both_filed(signed_in, specialist, evidence_root):
    refused = signed_in.post(
        CREATE,
        {
            "title": "Kaks korda valitud",
            "policy_area_other_selected": "on",
            "files": upload("esimene.pdf", corpus.government_pdf()),
        },
    )

    signed_in.post(
        CREATE,
        {
            "title": "Kaks korda valitud",
            "policy_area_other_selected": "on",
            "policy_area_other": "Ehitus",
            "pending": held_keys(refused),
            "files": upload("teine.pdf", corpus.government_pdf()),
        },
    )

    matter = Matter.objects.get(title="Kaks korda valitud")
    names = set(
        DocumentVersion.objects.filter(document__matter=matter).values_list(
            "original_filename", flat=True
        )
    )
    assert names == {"esimene.pdf", "teine.pdf"}


def test_the_good_half_of_a_rejected_batch_is_held(signed_in, specialist, evidence_root):
    """The batch is still all or nothing — no Matter is created — but the three
    files that were fine do not have to be found again while the fourth is
    replaced."""
    refused = signed_in.post(
        CREATE,
        {
            "title": "Vigase failiga",
            "files": [
                upload("hea.pdf", corpus.government_pdf()),
                upload("paha.exe", b"MZ", content_type="application/x-msdownload"),
            ],
        },
    )

    assert refused.status_code == 400
    assert not Matter.objects.filter(title="Vigase failiga").exists()
    assert [item.filename for item in refused.context["held_files"]] == ["hea.pdf"]


def test_an_ordinary_visit_offers_no_held_file(signed_in, specialist, evidence_root):
    """A fresh form must not hand somebody an attachment they abandoned an hour
    ago. The keys travel on the refused form, never out of the session."""
    signed_in.post(
        CREATE,
        {
            "title": "Hüljatud",
            "policy_area_other_selected": "on",
            "files": upload("kaaskiri.pdf", corpus.government_pdf()),
        },
    )

    fresh = signed_in.get(CREATE)

    assert fresh.context["held_files"] == []


def test_a_successful_save_ends_the_hold(signed_in, specialist, evidence_root):
    refused = signed_in.post(
        CREATE,
        {
            "title": "Lõpetatud",
            "policy_area_other_selected": "on",
            "files": upload("kaaskiri.pdf", corpus.government_pdf()),
        },
    )
    keys = held_keys(refused)

    signed_in.post(
        CREATE,
        {
            "title": "Lõpetatud",
            "policy_area_other_selected": "on",
            "policy_area_other": "Ehitus",
            "pending": keys,
        },
    )

    for key in keys:
        assert not pending.pending_storage().exists(key)


def test_a_refused_typed_sender_holds_the_file_and_creates_no_organisation(
    signed_in, specialist, evidence_root
):
    """Two rollbacks at once, and they must not fight.

    An ambiguous typed sender refuses the save from inside the transaction, so
    the institution must not survive — and the file must, because the person is
    about to answer the question and press the button again.
    """
    factories.OrganisationFactory(name="Ministeerium")
    factories.OrganisationFactory(name="ministeerium")

    refused = signed_in.post(
        CREATE,
        {
            "title": "Mitmetimõistetav saatja",
            "sender_name": "Ministeerium",
            "files": upload("kaaskiri.pdf", corpus.government_pdf()),
        },
    )

    assert refused.status_code == 400
    assert not Matter.objects.filter(title="Mitmetimõistetav saatja").exists()
    assert Organisation.objects.count() == 2
    assert [item.filename for item in refused.context["held_files"]] == ["kaaskiri.pdf"]
