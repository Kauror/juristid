"""Files arrive whole, or not at all, and one bad file does not sink the rest.

Five findings about the doors bytes come through, each proven on the code before
it was fixed:

* **ENG-033** — one unusable e-mail attachment (an empty `.msg` stream, a
  machine-generated name longer than 400 characters) failed the whole message:
  no body text, no sibling attachment, and `--force` failing the same way.
* **ENG-086** — a workspace form given [good.pdf, bad.html] refused the save
  but left good.pdf's bytes in the evidence store with no row.
* **ENG-087** — two uploads into one `Uus teema` form raced to one ordinal and
  the loser was a 500 with its bytes already written; nothing ever swept an
  object whose row did not commit.
* **ENG-088** — a filename with decomposed Estonian letters (as a Mac writes
  it) was not found by the Dokumendid filter and was not marked as a twin.
* **ENG-089** — an empty file on Dokumendid was answered «Vali fail ja roll.».
"""

from __future__ import annotations

import hashlib
import threading
import time
import unicodedata
from email.message import EmailMessage
from pathlib import Path
from typing import Any

import pytest
from django.conf import settings
from django.contrib.messages import get_messages
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError, connection
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from app.documents.derivatives import EmailAttachmentLink
from app.documents.enums import DerivativeKind, DerivativeStatus, DocumentRole
from app.documents.filenames import FILENAME_MAX_LENGTH, canonical_filename
from app.documents.models import Document, DocumentDerivative, DocumentVersion
from app.documents.uploads import UploadRejected
from app.matters import intake_staging
from app.matters.models import Entry
from app.matters.staging import MatterIntakeFile, MatterIntakeSession
from tests import synthetic_corpus as corpus

EML = "message/rfc822"
MSG = "application/vnd.ms-outlook"

NFC_NAME = unicodedata.normalize("NFC", "Arvamus õigusloome šoti žürii.pdf")
NFD_NAME = unicodedata.normalize("NFD", NFC_NAME)
LONG_STEM = "Eelnõu seletuskirja lisa " * 40  # far past 400 characters


def _files_under(root: Path) -> set[Path]:
    return {path for path in root.rglob("*") if path.is_file()}


def _evidence_files() -> set[Path]:
    return _files_under(Path(settings.EVIDENCE_ROOT))


def _referenced_keys() -> set[str]:
    return set(DocumentVersion.objects.values_list("storage_key", flat=True))


def assert_no_orphan_evidence() -> None:
    root = Path(settings.EVIDENCE_ROOT)
    on_disk = {str(path.relative_to(root)) for path in _evidence_files()}
    assert on_disk <= _referenced_keys(), f"orphan evidence objects: {on_disk - _referenced_keys()}"


def _pdf(name: str, words: str = "Lisa") -> SimpleUploadedFile:
    return SimpleUploadedFile(name, corpus.text_pdf([words]), content_type="application/pdf")


# ---------------------------------------------------------------------------
# The filename rule itself
# ---------------------------------------------------------------------------


def test_a_short_name_is_only_normalised():
    assert canonical_filename(NFD_NAME) == NFC_NAME
    assert canonical_filename("") == ""


def test_a_long_name_keeps_its_extension_and_says_it_was_shortened():
    bounded = canonical_filename(f"{LONG_STEM}.pdf")
    assert len(bounded) == FILENAME_MAX_LENGTH
    assert bounded.endswith("….pdf")
    assert bounded.startswith("Eelnõu seletuskirja lisa")


def test_a_long_name_without_a_usable_extension_is_still_bounded():
    bounded = canonical_filename("x" * 1000)
    assert len(bounded) == FILENAME_MAX_LENGTH
    assert canonical_filename("a." + "b" * 900).endswith("…")


def test_the_cut_never_strands_a_combining_mark():
    # A base letter with a mark NFC has no precomposed form for.
    text = ("a" * 398) + "q̃" + "zzz.bin_but_not_an_extension_at_all"
    bounded = canonical_filename(text)
    assert len(bounded) <= FILENAME_MAX_LENGTH
    assert not unicodedata.combining(bounded[-2]) or bounded[-3] != "a"
    assert unicodedata.normalize("NFC", bounded) == bounded


# ---------------------------------------------------------------------------
# ENG-033 — an e-mail is a container
# ---------------------------------------------------------------------------


def _attachment_names(matter: Any) -> list[str]:
    return sorted(
        matter.documents.filter(role=DocumentRole.EMAIL_ATTACHMENT).values_list(
            "current_version__original_filename", flat=True
        )
    )


def _body_is_indexed(version: DocumentVersion) -> bool:
    derivative = DocumentDerivative.objects.filter(
        version=version, kind=DerivativeKind.EXTRACTED_TEXT, status=DerivativeStatus.ACTIVE
    ).first()
    if derivative is None:
        return False
    return any(
        corpus.ONLY_IN_EMAIL_BODY in fragment.text for fragment in derivative.fragments.all()
    )


@pytest.mark.django_db
def test_msg_with_one_pdf_is_extracted(normal_matter, capture_evidence, extract):
    version = capture_evidence(normal_matter, corpus.outlook_msg(), "kiri.msg", MSG)

    report = extract(version)

    assert report.state == "DONE"
    assert _body_is_indexed(version)
    assert _attachment_names(normal_matter) == ["lisa-1.pdf"]


@pytest.mark.django_db
def test_msg_with_an_empty_attachment_keeps_its_body_and_its_pdf(
    normal_matter, capture_evidence, extract
):
    message = corpus.outlook_msg(extra_attachments=(("tuhi.pdf", b""),))
    version = capture_evidence(normal_matter, message, "kiri.msg", MSG)
    files_before = _evidence_files()

    report = extract(version)

    assert report.state == "DONE"
    assert _body_is_indexed(version)
    assert _attachment_names(normal_matter) == ["lisa-1.pdf"]
    assert not DocumentVersion.objects.filter(size_bytes=0).exists()
    # One new object — the PDF — and nothing for the empty part.
    assert len(_evidence_files() - files_before) == 1
    assert_no_orphan_evidence()


def _eml(*parts: tuple[str, bytes]) -> bytes:
    message = EmailMessage()
    message["Subject"] = "Katse"
    message["From"] = "keegi@naidis.invalid"
    message["To"] = "oigus@koda.invalid"
    message.set_content(f"Sisu {corpus.ONLY_IN_EMAIL_BODY}")
    for filename, payload in parts:
        message.add_attachment(payload, maintype="application", subtype="pdf", filename=filename)
    return message.as_bytes()


@pytest.mark.django_db
def test_eml_with_an_empty_attachment_keeps_its_body_and_its_pdf(
    normal_matter, capture_evidence, extract
):
    message = _eml(("lisa-1.pdf", corpus.text_pdf(["Lisa"])), ("tuhi.pdf", b""))
    version = capture_evidence(normal_matter, message, "kiri.eml", EML)

    report = extract(version)

    assert report.state == "DONE"
    assert _body_is_indexed(version)
    assert _attachment_names(normal_matter) == ["lisa-1.pdf"]
    assert_no_orphan_evidence()


@pytest.mark.django_db
@pytest.mark.parametrize("container", ["msg", "eml"])
def test_an_overlong_attachment_name_is_bounded_and_the_message_survives(
    container, normal_matter, capture_evidence, extract
):
    long_name = f"{LONG_STEM}.pdf"
    pdf = corpus.text_pdf(["Pikk nimi"])
    if container == "msg":
        message = corpus.outlook_msg(extra_attachments=((long_name, pdf),))
        version = capture_evidence(normal_matter, message, "kiri.msg", MSG)
    else:
        message = _eml(("lisa-1.pdf", corpus.text_pdf(["Lisa"])), (long_name, pdf))
        version = capture_evidence(normal_matter, message, "kiri.eml", EML)

    report = extract(version)

    assert report.state == "DONE"
    assert _body_is_indexed(version)
    stored = DocumentVersion.objects.get(
        document__role=DocumentRole.EMAIL_ATTACHMENT, sha256__isnull=False, size_bytes=len(pdf)
    )
    assert len(stored.original_filename) == FILENAME_MAX_LENGTH
    assert stored.original_filename.endswith(".pdf")
    assert stored.original_filename.startswith("Eelnõu seletuskirja lisa")
    assert stored.document.title == stored.original_filename
    # The link row keeps what the message declared, as far as it fits.
    link = EmailAttachmentLink.objects.get(attachment_version=stored)
    assert link.declared_filename.startswith("Eelnõu seletuskirja lisa")
    assert_no_orphan_evidence()


@pytest.mark.django_db
def test_a_forced_re_extraction_gives_the_same_result(normal_matter, capture_evidence, extract):
    message = corpus.outlook_msg(extra_attachments=(("tuhi.pdf", b""), (f"{LONG_STEM}.pdf", b"")))
    version = capture_evidence(normal_matter, message, "kiri.msg", MSG)

    first = extract(version)
    names_after_first = _attachment_names(normal_matter)
    second = extract(version, force=True)

    assert first.state == second.state == "DONE"
    assert _attachment_names(normal_matter) == names_after_first == ["lisa-1.pdf"]
    assert EmailAttachmentLink.objects.filter(parent_version=version).count() == 1
    assert_no_orphan_evidence()


@pytest.mark.django_db
def test_an_attachment_attached_to_a_nfd_name_is_stored_in_nfc(
    normal_matter, capture_evidence, extract
):
    message = _eml((NFD_NAME, corpus.text_pdf(["Lisa"])))
    version = capture_evidence(normal_matter, message, "kiri.eml", EML)

    extract(version)

    assert _attachment_names(normal_matter) == [NFC_NAME]


# ---------------------------------------------------------------------------
# ENG-086 — every workspace file is checked before any is written
# ---------------------------------------------------------------------------


def _bad_html() -> SimpleUploadedFile:
    return SimpleUploadedFile("halb.html", b"<html>ei</html>", content_type="text/html")


@pytest.mark.django_db
@pytest.mark.parametrize("order", ["good_first", "bad_first"])
def test_a_mixed_selection_writes_nothing(order, client, specialist, normal_matter):
    client.force_login(specialist)
    files = [_pdf("hea.pdf"), _bad_html()]
    if order == "bad_first":
        files.reverse()
    documents_before = Document.objects.count()
    entries_before = Entry.objects.count()

    response = client.post(
        reverse("matters:add_note", kwargs={"pk": normal_matter.pk}),
        {"body": "<p>Märge failidega.</p>", "attachments": files},
    )

    assert response.status_code == 400
    assert "halb.html" in response.content.decode()
    assert Document.objects.count() == documents_before
    assert Entry.objects.count() == entries_before
    assert not DocumentVersion.objects.exists()
    assert _evidence_files() == set()


@pytest.mark.django_db
def test_every_refused_file_is_named_at_once(normal_matter, specialist, evidence_root):
    from app.documents.services import capture_supporting_evidence

    entry_like = normal_matter  # the record is never reached
    with pytest.raises(UploadRejected) as refused:
        capture_supporting_evidence(
            matter=normal_matter,
            record=entry_like,
            uploads=[
                _pdf("hea.pdf"),
                _bad_html(),
                SimpleUploadedFile("tuhi.pdf", b"", content_type="application/pdf"),
            ],
            actor=specialist,
        )

    message = str(refused.value)
    assert "halb.html" in message and "tuhi.pdf" in message
    assert "hea.pdf" not in message
    assert _evidence_files() == set()


# ---------------------------------------------------------------------------
# ENG-087 — staging takes turns, cleans up after itself, and is swept whole
# ---------------------------------------------------------------------------


def _staging_root() -> Path:
    return Path(settings.PENDING_UPLOAD_ROOT) / intake_staging.STORAGE_PREFIX


def _accepted(name: str) -> Any:
    from app.documents.uploads import read_upload

    return read_upload(_pdf(name, f"Staged {name}"))


@pytest.mark.django_db
def test_a_row_that_fails_to_insert_takes_its_bytes_with_it(specialist, evidence_root, monkeypatch):
    session = intake_staging.open_session(owner=specialist)
    real_create = MatterIntakeFile.objects.create

    def failing_create(**kwargs: Any) -> Any:
        raise IntegrityError("injected")

    monkeypatch.setattr(MatterIntakeFile.objects, "create", failing_create)
    with pytest.raises(IntegrityError):
        intake_staging.stage_uploads(
            owner=specialist, uploads=[_accepted("a.pdf")], session=session
        )
    monkeypatch.setattr(MatterIntakeFile.objects, "create", real_create)

    assert _files_under(_staging_root()) == set()


@pytest.mark.django_db
def test_the_sweep_removes_an_object_no_row_names(specialist, evidence_root):
    session = intake_staging.open_session(owner=specialist)
    intake_staging.stage_uploads(owner=specialist, uploads=[_accepted("a.pdf")], session=session)
    # What a rollback after the write leaves: bytes under the session's prefix
    # and no row for them.
    stray = _staging_root() / str(session.pk) / "rowless-object"
    stray.write_bytes(b"%PDF-1.4 orphan")
    other = intake_staging.open_session(owner=specialist)
    intake_staging.stage_uploads(owner=specialist, uploads=[_accepted("b.pdf")], session=other)

    MatterIntakeSession.objects.filter(pk=session.pk).update(expires_at="2000-01-01T00:00:00Z")
    report = intake_staging.sweep_stale_sessions()

    assert report.sessions == 1
    assert not (_staging_root() / str(session.pk)).exists()
    # The other, live session's bytes are untouched: the sweep is scoped to
    # sessions the database already chose.
    assert len(_files_under(_staging_root() / str(other.pk))) == 1


@pytest.mark.django_db(transaction=True, serialized_rollback=True)
def test_two_uploads_into_one_form_both_land(specialist, evidence_root, monkeypatch):
    """The first holds its lock until the second is seen waiting on it."""
    from tests.test_evidence_concurrency import TIMEOUT, Runner

    session = intake_staging.open_session(owner=specialist)
    stage = reverse("matters:intake_stage")
    real_each = intake_staging._stage_each
    first_thread: dict[str, int] = {}
    holding = threading.Event()
    second_done = threading.Event()

    def stage_each(*args: Any, **kwargs: Any) -> Any:
        result = real_each(*args, **kwargs)
        if threading.get_ident() == first_thread.get("ident") and not holding.is_set():
            holding.set()
            deadline = time.monotonic() + TIMEOUT
            while not second_done.is_set() and time.monotonic() < deadline:
                with connection.cursor() as cursor:
                    cursor.execute("SELECT pg_stat_clear_snapshot()")
                    cursor.execute(
                        "SELECT count(*) FROM pg_stat_activity WHERE datname = current_database()"
                        " AND wait_event_type = 'Lock' AND pid <> pg_backend_pid()"
                    )
                    if cursor.fetchone()[0]:
                        break
                time.sleep(0.02)
        return result

    monkeypatch.setattr(intake_staging, "_stage_each", stage_each)
    statuses: dict[str, int] = {}

    def post(label: str, name: str) -> None:
        client = Client()
        client.force_login(specialist)
        statuses[label] = client.post(
            stage, {"intake": str(session.pk), "files": [_pdf(name, f"Staged {name}")]}
        ).status_code

    def first() -> None:
        first_thread["ident"] = threading.get_ident()
        post("first", "esimene.pdf")

    def second() -> None:
        assert holding.wait(TIMEOUT)
        try:
            post("second", "teine.pdf")
        finally:
            second_done.set()

    one = Runner(first).start()
    two = Runner(second).start()
    assert one.join() is None and two.join() is None

    assert statuses == {"first": 200, "second": 200}
    rows = list(MatterIntakeFile.objects.filter(session=session).order_by("ordinal"))
    assert [row.ordinal for row in rows] == [1, 2]
    assert [row.original_filename for row in rows] == ["esimene.pdf", "teine.pdf"]
    root = Path(settings.PENDING_UPLOAD_ROOT)
    on_disk = {str(path.relative_to(root)) for path in _files_under(_staging_root())}
    assert on_disk == {row.storage_key for row in rows}
    for row in rows:
        stored = (root / row.storage_key).read_bytes()
        assert f"Staged {row.original_filename}".encode("cp1252") in stored


# ---------------------------------------------------------------------------
# ENG-088 — one name, whichever way it was spelled
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_a_new_upload_is_stored_in_nfc(client, specialist, normal_matter):
    client.force_login(specialist)
    client.post(
        reverse("documents:upload_evidence", kwargs={"matter_id": normal_matter.pk}),
        {"role": DocumentRole.OTHER, "upload": _pdf(NFD_NAME)},
    )
    version = DocumentVersion.objects.get()
    assert version.original_filename == NFC_NAME
    assert version.document.title == NFC_NAME


def _store_verbatim(matter: Any, name: str, actor: Any) -> Document:
    """A row as written before this fix: the name exactly as the sender spelled it.

    Inserted directly, because the service now normalises and the immutability
    trigger (rightly) refuses to change the name afterwards.
    """
    from django.core.files.base import ContentFile

    from app.core.ids import uuid7
    from app.documents.services import create_document, evidence_storage

    document = create_document(matter=matter, title=name, role=DocumentRole.OTHER, created_by=actor)
    content = corpus.text_pdf([name])
    version_id = uuid7()
    key = evidence_storage().save(f"verbatim/{version_id}", ContentFile(content))
    version = DocumentVersion.objects.create(
        id=version_id,
        document=document,
        version_number=1,
        storage_key=key,
        original_filename=name,
        mime_type="application/pdf",
        size_bytes=len(content),
        sha256=hashlib.sha256(content).hexdigest(),
        uploaded_by=actor,
        acquired_at=timezone.now(),
    )
    Document.objects.filter(pk=document.pk).update(title=name, current_version=version)
    document.refresh_from_db()
    return document


@pytest.mark.django_db
def test_an_old_nfd_name_is_found_by_a_typed_term(client, specialist, normal_matter, evidence_root):
    old = _store_verbatim(normal_matter, NFD_NAME, specialist)
    assert DocumentVersion.objects.get(document=old).original_filename == NFD_NAME
    client.force_login(specialist)

    page = client.get(
        reverse("matters:matter_documents", kwargs={"pk": normal_matter.pk}),
        {"otsi": unicodedata.normalize("NFC", "õigusloome")},
    )

    assert page.status_code == 200
    assert [document.pk for document in page.context["evidence_documents"]] == [old.pk]


@pytest.mark.django_db
def test_nfc_and_nfd_spellings_are_marked_as_twins(
    client, specialist, normal_matter, evidence_root
):
    _store_verbatim(normal_matter, NFD_NAME, specialist)
    _store_verbatim(normal_matter, NFC_NAME, specialist)
    client.force_login(specialist)

    page = client.get(reverse("matters:matter_documents", kwargs={"pk": normal_matter.pk}))

    hints = [
        getattr(document, "duplicate_hint", None) for document in page.context["evidence_documents"]
    ]
    assert all(hints), hints


# ---------------------------------------------------------------------------
# ENG-089 — an empty file is called empty
# ---------------------------------------------------------------------------


@pytest.mark.django_db
@pytest.mark.parametrize("route", ["upload", "add_version"])
def test_an_empty_file_is_refused_as_empty(route, client, specialist, normal_matter, evidence_root):
    client.force_login(specialist)
    empty = SimpleUploadedFile("tyhi.pdf", b"", content_type="application/pdf")
    if route == "upload":
        url = reverse("documents:upload_evidence", kwargs={"matter_id": normal_matter.pk})
        data = {"role": DocumentRole.INCOMING_AUTHORITY, "upload": empty}
        documents_before = 0
    else:
        existing = _store_verbatim(normal_matter, "olemas.pdf", specialist)
        url = reverse("documents:add_version", kwargs={"pk": existing.pk})
        data = {"upload": empty}
        documents_before = 1
    files_before = _evidence_files()

    response = client.post(url, data)

    assert response.status_code == 302
    said = [str(message) for message in get_messages(response.wsgi_request)]
    assert said == ["Tühja faili ei saa tõendina salvestada."]
    assert Document.objects.count() == documents_before
    assert DocumentVersion.objects.count() == documents_before
    assert _evidence_files() == files_before


@pytest.mark.django_db
def test_no_file_at_all_still_asks_for_one(client, specialist, normal_matter, evidence_root):
    client.force_login(specialist)
    response = client.post(
        reverse("documents:upload_evidence", kwargs={"matter_id": normal_matter.pk}),
        {"role": DocumentRole.INCOMING_AUTHORITY},
    )
    said = [str(message) for message in get_messages(response.wsgi_request)]
    assert said == ["Vali fail ja roll."]
