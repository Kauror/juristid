"""Estonian signed containers are ordinary evidence (JUR-CASE-01, docs/adr/0126).

The defect, as the living-dossier QA met it: a ministry's `.bdoc` was refused on
`Uus teema` with «Faililaiend .bdoc ei ole lubatud», and the Chamber's own sent
`.asice` was chosen without complaint in `+ Koja arvamus` and refused only after
`Registreeri arvamus` — so the only way to keep the original was to wrap it in a
ZIP, which keeps a ZIP rather than the evidence.

Four things are held here:

* **the door** — `read_upload` takes `.asice` and `.bdoc` as themselves, reads
  the one thing that identifies a container, and refuses a file that is not
  one; everything it accepted and refused before, it still does;
* **the evidence** — through every surface that captures a file, the version
  stored is the bytes, the name and the extension that arrived, and nothing is
  unpacked out of it;
* **the sent opinion** — `+ Koja arvamus` registers an opinion whose evidence is
  the container, and a refused container leaves neither a `Submission` nor a
  `Document` behind (evidence before SENT);
* **the picker** — every file input the application renders offers exactly the
  list the server accepts.

Every container is built at test time (`tests/synthetic_containers.py`); none
is a real signature.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import re
from email.message import EmailMessage
from pathlib import Path
from typing import Any

import pytest
from django import forms
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from django.utils import timezone

from app.documents.enums import DocumentRole, ExtractionState
from app.documents.models import Document, DocumentDerivative, DocumentVersion
from app.documents.services import ALLOWED_EVIDENCE_MIME_TYPES, evidence_storage
from app.documents.uploads import (
    EXTENSION_MIME_TYPES,
    SIGNED_CONTAINER_MIME_TYPE,
    UPLOAD_ACCEPT,
    UploadRejected,
    read_upload,
    starts_like_signed_container,
)
from app.legacy_import.historical_materials import mime_type_for
from app.matters import intake_extraction
from app.matters.models import Matter
from app.matters.staging import MatterIntakeFile
from app.organisations.models import Organisation, OrganisationType
from app.submissions.enums import SubmissionStatus
from app.submissions.models import Submission
from tests.synthetic_containers import (
    EXTENDED_TIMESTAMP,
    plain_zip,
    signed_container,
)

ROOT = Path(__file__).resolve().parent.parent

#: The three shapes the Chamber's own historical containers come in, measured
#: read-only over all 1,049 of them on 2026-10-01: 1,042 standard, one `.bdoc`
#: with an extra field in its first header, six `.asice` with that first entry
#: deflated by a streaming encoder.
REAL_WORLD_SHAPES = {
    "standard": {},
    "extra field": {"mimetype_extra": EXTENDED_TIMESTAMP},
    "deflated, data descriptor": {"deflated_mimetype": True},
}

#: A day the opinion went out: in the past for good, because a send is never in
#: the future (ENG-043).
SENT_ON = dt.date(2026, 9, 8)


def upload(name: str, content: bytes, content_type: str = "application/octet-stream") -> Any:
    # The browser's declared type is deliberately wrong: the extension and the
    # bytes decide, never this (app/documents/uploads.py).
    return SimpleUploadedFile(name, content, content_type=content_type)


def container_refusal(extension: str) -> str:
    return (
        f"Faili sisu ei vasta laiendile {extension}: see ei ole digiallkirjastatud ümbrik. "
        "Kontrolli, kas fail on terve ja õiget tüüpi."
    )


def stored_bytes(version: DocumentVersion) -> bytes:
    with evidence_storage().open(version.storage_key, "rb") as handle:
        return handle.read()


def assert_is_the_file(version: DocumentVersion, *, name: str, content: bytes) -> None:
    """The version is the file that arrived: its name, its type, its bytes."""
    assert version.original_filename == name
    assert version.mime_type == SIGNED_CONTAINER_MIME_TYPE
    assert version.size_bytes == len(content)
    assert version.sha256 == hashlib.sha256(content).hexdigest()
    assert stored_bytes(version) == content


def assert_nothing_unpacked(matter: Matter) -> None:
    """A container is one Document with no children and no derived text."""
    assert not Document.objects.filter(matter=matter, role=DocumentRole.EMAIL_ATTACHMENT).exists()
    assert not DocumentDerivative.objects.filter(version__document__matter=matter).exists()


@pytest.fixture
def ministry(db):
    return Organisation.objects.create(
        name="Näidisministeerium", organisation_type=OrganisationType.MINISTRY
    )


# ---------------------------------------------------------------------------
# The door
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("extension", [".asice", ".bdoc"])
@pytest.mark.parametrize("shape", REAL_WORLD_SHAPES)
def test_a_signed_container_is_accepted_as_itself(extension, shape):
    content = signed_container(**REAL_WORLD_SHAPES[shape])

    accepted = read_upload(upload(f"VTK pakett{extension}", content))

    assert accepted.mime_type == SIGNED_CONTAINER_MIME_TYPE
    assert accepted.filename == f"VTK pakett{extension}"
    assert accepted.content == content


def test_the_extension_is_read_whatever_its_case():
    content = signed_container()

    assert read_upload(upload("ARVAMUS.ASICE", content)).mime_type == SIGNED_CONTAINER_MIME_TYPE
    assert read_upload(upload("Kiri.Bdoc", content)).mime_type == SIGNED_CONTAINER_MIME_TYPE


@pytest.mark.parametrize(
    "content",
    [
        pytest.param(b"%PDF-1.4 a letter, not a container", id="a PDF renamed"),
        pytest.param(plain_zip(), id="an ordinary ZIP"),
        pytest.param(
            signed_container(first_entry="[Content_Types].xml", media_type=b"<x/>"),
            id="a Word file",
        ),
        pytest.param(
            signed_container(media_type=b"application/vnd.oasis.opendocument.text"),
            id="an OpenDocument file",
        ),
        pytest.param(
            signed_container(media_type=b"application/vnd.etsi.asic-s+zip"),
            id="an ASiC-S container",
        ),
        pytest.param(signed_container()[:24], id="a truncated container"),
        pytest.param(b"MZ\x90\x00" + b"\x00" * 64, id="an executable"),
    ],
)
@pytest.mark.parametrize("extension", [".asice", ".bdoc"])
def test_a_file_that_is_not_a_signed_container_is_refused_in_words(extension, content):
    with pytest.raises(UploadRejected) as refusal:
        read_upload(upload(f"arvamus{extension}", content))

    assert str(refusal.value) == container_refusal(extension)


def test_a_deflated_first_entry_that_does_not_inflate_is_refused():
    """The deflated shape is read by inflating it; garbage there is a refusal."""
    content = bytearray(signed_container(deflated_mimetype=True))
    # Past the 30-byte local header and the entry's name: the deflated data.
    data = 30 + len(b"mimetype")
    content[data : data + 8] = b"\xff" * 8

    assert not starts_like_signed_container(bytes(content))
    with pytest.raises(UploadRejected):
        read_upload(upload("arvamus.asice", bytes(content)))


@pytest.mark.parametrize("name", ["vana.ddoc", "pitser.asics", "programm.exe", "nimeta"])
def test_a_format_nobody_decided_on_is_still_refused(name):
    """`.ddoc` stays the archive's alone; the allowlist did not become a denylist."""
    with pytest.raises(UploadRejected) as refusal:
        read_upload(upload(name, signed_container()))

    message = str(refusal.value)
    assert message.startswith("Faililaiend ")
    assert "ei ole lubatud" in message
    # The refusal lists what *is* allowed, and that now includes both.
    assert ".asice" in message and ".bdoc" in message


#: Every format the door took before this change, with bytes that pass its check.
ORDINARY_FILES = [
    ("kiri.pdf", b"%PDF-1.4 kiri", "application/pdf"),
    ("andmed.csv", b"a;b\n1;2\n", "text/csv"),
    (
        "eelnou.docx",
        b"PK\x03\x04 docx",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ),
    (
        "tabel.xlsx",
        b"PK\x03\x04 xlsx",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ),
    (
        "slaidid.pptx",
        b"PK\x03\x04 pptx",
        "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    ),
    ("vana.doc", b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1 doc", "application/msword"),
    ("vana.xls", b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1 xls", "application/vnd.ms-excel"),
    ("kiri.msg", b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1 msg", "application/vnd.ms-outlook"),
    ("kiri.eml", b"Subject: tere\r\n\r\nsisu", "message/rfc822"),
    ("pilt.png", b"\x89PNG\r\n\x1a\n pilt", "image/png"),
    ("pilt.jpg", b"\xff\xd8\xff pilt", "image/jpeg"),
    ("pilt.jpeg", b"\xff\xd8\xff pilt", "image/jpeg"),
    ("markmed.txt", b"tekst", "text/plain"),
    ("pakett.zip", plain_zip(), "application/zip"),
]


def test_the_ordinary_list_is_every_format_but_the_two_containers():
    """So the test below cannot quietly skip a format the door accepts."""
    covered = {name[name.rindex(".") :] for name, _, _ in ORDINARY_FILES}
    assert covered == set(EXTENSION_MIME_TYPES) - {".asice", ".bdoc"}


@pytest.mark.parametrize(
    ("name", "content", "mime_type"),
    ORDINARY_FILES,
    ids=[name for name, _, _ in ORDINARY_FILES],
)
def test_the_formats_accepted_before_are_accepted_exactly_as_before(name, content, mime_type):
    accepted = read_upload(upload(name, content))

    assert (accepted.filename, accepted.mime_type, accepted.content) == (name, mime_type, content)


def test_a_mislabelled_ordinary_file_is_refused_exactly_as_before():
    with pytest.raises(UploadRejected) as refusal:
        read_upload(upload("kiri.pdf", b"MZ not a pdf"))

    assert str(refusal.value) == (
        "Faili sisu ei vasta selle laiendile. Kontrolli, kas fail on terve ja õiget tüüpi."
    )


def test_one_media_type_for_both_extensions_and_the_archive_agrees():
    """`.asice` and `.bdoc` are one container; the importer reads the same table."""
    assert EXTENSION_MIME_TYPES[".asice"] == SIGNED_CONTAINER_MIME_TYPE
    assert EXTENSION_MIME_TYPES[".bdoc"] == SIGNED_CONTAINER_MIME_TYPE
    assert SIGNED_CONTAINER_MIME_TYPE in ALLOWED_EVIDENCE_MIME_TYPES
    # The historical importer's answers are unchanged by the move.
    assert mime_type_for("seisukoht.asice") == SIGNED_CONTAINER_MIME_TYPE
    assert mime_type_for("allkirjastatud.BDOC") == SIGNED_CONTAINER_MIME_TYPE
    assert mime_type_for("vana.ddoc") == "application/x-ddoc"


# ---------------------------------------------------------------------------
# The evidence, through every surface that captures a file
# ---------------------------------------------------------------------------


def test_lae_dokument_stores_the_container_and_hands_back_the_same_bytes(signed_in, normal_matter):
    content = signed_container()

    signed_in.post(
        reverse("documents:upload_evidence", kwargs={"matter_id": normal_matter.pk}),
        {"upload": upload("VTK pakett.asice", content), "role": DocumentRole.INCOMING_AUTHORITY},
    )

    version = DocumentVersion.objects.get(document__matter=normal_matter)
    assert_is_the_file(version, name="VTK pakett.asice", content=content)
    assert_nothing_unpacked(normal_matter)

    # Shown with its own name on Dokumendid, and downloadable as itself.
    page = signed_in.get(reverse("matters:matter_documents", kwargs={"pk": normal_matter.pk}))
    assert "VTK pakett.asice" in page.content.decode()
    download = signed_in.get(reverse("documents:download", kwargs={"pk": version.pk}))
    assert download.status_code == 200
    assert b"".join(download.streaming_content) == content
    assert download["Content-Type"] == SIGNED_CONTAINER_MIME_TYPE
    disposition = download["Content-Disposition"]
    assert disposition.startswith("attachment")
    assert "VTK%20pakett.asice" in disposition or "VTK pakett.asice" in disposition


def test_a_refused_container_writes_nothing(signed_in, normal_matter):
    signed_in.post(
        reverse("documents:upload_evidence", kwargs={"matter_id": normal_matter.pk}),
        {"upload": upload("pakett.bdoc", plain_zip()), "role": DocumentRole.INCOMING_AUTHORITY},
    )

    assert not Document.objects.filter(matter=normal_matter).exists()


def test_uus_teema_files_a_bdoc_chosen_with_the_form(signed_in, ministry):
    """The page without scripting: the files travel with `Loo teema` itself."""
    content = signed_container()

    signed_in.post(
        reverse("matters:matter_create"),
        {"title": "VTK pakett ministeeriumilt", "files": [upload("vtk_package.bdoc", content)]},
    )

    matter = Matter.objects.get(title="VTK pakett ministeeriumilt")
    version = DocumentVersion.objects.get(document__matter=matter)
    assert_is_the_file(version, name="vtk_package.bdoc", content=content)
    assert version.document.role == DocumentRole.INCOMING_AUTHORITY


def test_uus_teema_stages_reads_and_files_a_container_without_opening_it(signed_in, ministry):
    """The page with scripting: staged on choice, read, promoted on `Loo teema`.

    The refusal the QA screenshot shows came from this route. Now nothing is
    refused, the reader settles the container as a format nothing opens rather
    than a failure, and the version promoted is the staged bytes.
    """
    content = signed_container(deflated_mimetype=True)
    kaaskiri = b"%PDF-1.4 kaaskiri"

    answer = signed_in.post(
        reverse("matters:intake_stage"),
        {"files": [upload("Kaaskiri.pdf", kaaskiri), upload("vtk_package.bdoc", content)]},
    )

    assert answer.status_code == 200
    assert not answer.context["intake_errors"]
    session = answer.context["intake_session"]
    staged = MatterIntakeFile.objects.get(session=session, original_filename="vtk_package.bdoc")
    assert staged.mime_type == SIGNED_CONTAINER_MIME_TYPE

    intake_extraction.drain(limit=10)
    staged.refresh_from_db()
    assert staged.extraction_state == ExtractionState.NOT_APPLICABLE

    signed_in.post(
        reverse("matters:matter_create"), {"title": "VTK pakett", "intake": str(session.pk)}
    )

    matter = Matter.objects.get(title="VTK pakett")
    version = DocumentVersion.objects.get(
        document__matter=matter, original_filename="vtk_package.bdoc"
    )
    assert_is_the_file(version, name="vtk_package.bdoc", content=content)
    assert version.extraction_state == ExtractionState.NOT_APPLICABLE
    assert Document.objects.filter(matter=matter).count() == 2
    assert_nothing_unpacked(matter)


def test_saabunud_files_a_container(signed_in):
    content = signed_container()

    signed_in.post(
        reverse("matters:intake"),
        {"title": "Saabunud allkirjastatud kiri", "uploads": [upload("kiri.asice", content)]},
    )

    version = DocumentVersion.objects.get(original_filename="kiri.asice")
    assert_is_the_file(version, name="kiri.asice", content=content)


def test_a_marge_takes_a_container_as_its_evidence(normal_matter, specialist):
    from app.matters.workspace import add_procedural_development

    content = signed_container(mimetype_extra=EXTENDED_TIMESTAMP)

    result = add_procedural_development(
        matter=normal_matter,
        author=specialist,
        title="Ministeerium saatis allkirjastatud vastuse",
        occurred_on=timezone.localdate() - dt.timedelta(days=2),
        uploads=[upload("vastus.bdoc", content)],
    )

    (document,) = result.documents
    assert_is_the_file(document.current_version, name="vastus.bdoc", content=content)


def test_a_new_version_of_a_document_may_be_the_signed_container(signed_in, normal_matter):
    """A PDF draft, then the signed letter: the same Document, a new version."""
    draft = b"%PDF-1.4 kavand"
    signed_in.post(
        reverse("documents:upload_evidence", kwargs={"matter_id": normal_matter.pk}),
        {"upload": upload("kiri.pdf", draft), "role": DocumentRole.INCOMING_AUTHORITY},
    )
    document = Document.objects.get(matter=normal_matter)
    content = signed_container()

    signed_in.post(
        reverse("documents:add_version", kwargs={"pk": document.pk}),
        {"upload": upload("kiri.asice", content)},
    )

    document.refresh_from_db()
    assert document.versions.count() == 2
    assert_is_the_file(document.current_version, name="kiri.asice", content=content)


def test_an_emails_signed_attachment_is_kept_as_itself(
    normal_matter, specialist, capture_evidence, extract
):
    """The same allowlist decides an e-mail's attachments, so the container a
    ministry attached is stored beside its message rather than skipped as an
    unknown type. Nothing inside it is opened."""
    content = signed_container()
    message = EmailMessage()
    message["Subject"] = "Allkirjastatud vastus"
    message["From"] = "keegi@naidisministeerium.invalid"
    message["To"] = "oigus@koda.invalid"
    message.set_content("Allkirjastatud vastus on lisatud.")
    message.add_attachment(
        content, maintype="application", subtype="octet-stream", filename="vastus.asice"
    )
    email = capture_evidence(
        normal_matter,
        message.as_bytes(),
        "kiri.eml",
        "message/rfc822",
        uploaded_by=specialist,
        created_by=specialist,
    )

    report = extract(email)

    assert report.state == ExtractionState.DONE
    assert report.attachments == 1
    attachment = DocumentVersion.objects.get(
        document__matter=normal_matter, document__role=DocumentRole.EMAIL_ATTACHMENT
    )
    assert_is_the_file(attachment, name="vastus.asice", content=content)


# ---------------------------------------------------------------------------
# The sent opinion
# ---------------------------------------------------------------------------


def koda_opinion(client: Any, matter: Matter, file: Any, ministry: Organisation) -> Any:
    return client.post(
        reverse("matters:add_koda_opinion", kwargs={"pk": matter.pk}),
        {
            "upload": file,
            "recipients": [str(ministry.pk)],
            "sent_on": f"{SENT_ON.day}.{SENT_ON.month}.{SENT_ON.year}",
            "summary": "Toetame eelnõu.",
        },
    )


@pytest.mark.parametrize("extension", [".asice", ".bdoc"])
def test_koja_arvamus_is_registered_with_the_container_as_its_sent_evidence(
    signed_in, normal_matter, ministry, extension
):
    content = signed_container()

    koda_opinion(signed_in, normal_matter, upload(f"Koja arvamus{extension}", content), ministry)

    submission = Submission.objects.get(matter=normal_matter)
    assert submission.status == SubmissionStatus.SENT
    assert timezone.localtime(submission.sent_at).date() == SENT_ON
    assert list(submission.recipients.all()) == [ministry]
    # Evidence before SENT: the send names exactly the bytes that went out.
    assert submission.final_version is not None
    assert submission.final_version.document.role == DocumentRole.KODA_SUBMISSION_FINAL
    assert_is_the_file(submission.final_version, name=f"Koja arvamus{extension}", content=content)
    assert_nothing_unpacked(normal_matter)


def test_a_koja_arvamus_with_a_false_container_is_refused_and_leaves_nothing(
    signed_in, normal_matter, ministry
):
    """No opinion is SENT without its evidence, and refused evidence is none."""
    answer = koda_opinion(
        signed_in, normal_matter, upload("Koja arvamus.asice", plain_zip()), ministry
    )

    assert answer.status_code == 400
    assert container_refusal(".asice") in answer.content.decode()
    assert not Submission.objects.filter(matter=normal_matter).exists()
    assert not Document.objects.filter(matter=normal_matter).exists()


# ---------------------------------------------------------------------------
# The picker offers what the server accepts
# ---------------------------------------------------------------------------

_FILE_INPUT = re.compile(r"<input\b[^>]*\btype=\"file\"[^>]*>", re.DOTALL)


def test_the_accept_list_is_the_allowlist():
    offered = UPLOAD_ACCEPT.split(",")

    assert offered == sorted(EXTENSION_MIME_TYPES)
    assert {".asice", ".bdoc"} <= set(offered)


def test_every_file_input_written_in_a_template_offers_the_allowlist():
    """A literal `<input type="file">` takes its `accept` from the tag, never a copy."""
    found = 0
    for template in (ROOT / "templates").rglob("*.html"):
        for control in _FILE_INPUT.findall(template.read_text(encoding="utf-8")):
            found += 1
            assert 'accept="{% upload_accept %}"' in control, f"{template.name}: {control}"
    # The five hand-written pickers: Uus teema, Saabunud, Lae dokument, a new
    # version, and a draft opinion's file. A guard that matched nothing would
    # pass by finding nothing.
    assert found >= 5


def _application_forms() -> list[type[forms.Form]]:
    import app.documents.views
    import app.matters.forms
    import app.submissions.forms  # noqa: F401 - imported for their Form classes

    seen: list[type[forms.Form]] = []
    pending: list[type[forms.Form]] = [forms.Form]
    while pending:
        for subclass in pending.pop().__subclasses__():
            if subclass.__module__.startswith("app.") and subclass not in seen:
                seen.append(subclass)
            pending.append(subclass)
    return seen


def test_every_file_field_a_form_renders_offers_the_allowlist():
    found = []
    for form in _application_forms():
        for name, field in form.base_fields.items():
            if isinstance(field, forms.FileField):
                found.append(f"{form.__name__}.{name}")
                assert field.widget.attrs.get("accept") == UPLOAD_ACCEPT, f"{form.__name__}.{name}"
    assert "KodaOpinionForm.upload" in found
    assert "MatterProgressForm.attachments" in found


@pytest.mark.parametrize("route", ["uus_teema", "teema", "saabunud"])
def test_the_rendered_pickers_offer_exactly_the_allowlist(signed_in, normal_matter, route):
    """What the browser actually receives: every file input on the page."""
    url = {
        "uus_teema": reverse("matters:matter_create"),
        "teema": reverse("matters:matter_detail", kwargs={"pk": normal_matter.pk}),
        "saabunud": reverse("matters:intake"),
    }[route]

    body = signed_in.get(url).content.decode()

    controls = _FILE_INPUT.findall(body)
    assert controls, f"{route}: no file input rendered"
    for control in controls:
        accept = re.search(r'\baccept="([^"]*)"', control)
        assert accept is not None, f"{route}: {control}"
        assert accept.group(1) == UPLOAD_ACCEPT


def test_the_koja_arvamus_picker_offers_a_signed_container(signed_in, normal_matter):
    body = signed_in.get(reverse("matters:matter_detail", kwargs={"pk": normal_matter.pk}))

    control = re.search(r'<input[^>]*id="id_koja_arvamus_fail"[^>]*>', body.content.decode())
    assert control is not None
    accept = re.search(r'\baccept="([^"]*)"', control.group(0))
    assert accept is not None
    assert {".asice", ".bdoc"} <= set(accept.group(1).split(","))
