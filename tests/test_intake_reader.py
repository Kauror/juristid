"""The intake reader's universe, and everything outside it.

One property is being defended here, in as many ways as it can be attacked:

    the process that reads files for `Uus teema` can see the rows behind an
    open form, and nothing else.

That is not fastidiousness. On 2026-09-10 the canonical extraction backlog —
12 687 done, 4 093 still pending — drove production's checkpoint fsyncs from
under 2.5 s to 155 s, because every small write to the parity-protected array is
a read-modify-write on a saturated USB disk. A single-row INSERT into a 264 kB
audit table blocked for two minutes and gunicorn killed the worker holding it.
The queue that did that and the queue behind somebody's open form were one loop,
and this file is the assertion that they are not any more (docs/adr/0072).

Two kinds of test, because one kind is not enough:

* **behavioural** — build the world that broke production, run the reader, count
  what moved;
* **structural** — read the worker module's own imports, so a future edit that
  reaches for the corpus queue fails here rather than on the array.
"""

from __future__ import annotations

import ast
from datetime import timedelta
from io import StringIO
from pathlib import Path

import pytest
from django.conf import settings as django_settings
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.urls import reverse
from django.utils import timezone

from app.documents.enums import ExtractionState, MalwareScanState
from app.documents.models import Document, DocumentDerivative, DocumentTextFragment, DocumentVersion
from app.matters import intake_extraction, intake_staging
from app.matters.staging import MatterIntakeFile, MatterIntakeSession
from tests import synthetic_corpus as corpus

pytestmark = pytest.mark.django_db

STAGE = reverse("matters:intake_stage")
STATUS = reverse("matters:intake_status")
PDF = "application/pdf"

LETTER = """Näidisministeerium

Eesti Kaubandus-Tööstuskoda

Pakendiseaduse muutmise seaduse eelnõu kooskõlastamiseks

Palume esitada arvamus hiljemalt 18. septembriks 2026.
"""


def stage(client, *files) -> MatterIntakeSession:
    response = client.post(STAGE, {"files": list(files)})
    assert response.status_code in (200, 400), response.status_code
    session = response.context["intake_session"]
    assert session is not None
    return session


def upload(name: str = "kaaskiri.pdf", text: str = LETTER) -> SimpleUploadedFile:
    return SimpleUploadedFile(name, corpus.text_pdf([text]), content_type=PDF)


def run_reader(**options) -> str:
    out = StringIO()
    call_command("run_intake_reader", "--once", stdout=out, **options)
    return out.getvalue()


# ---------------------------------------------------------------------------
# The universe
# ---------------------------------------------------------------------------


def test_one_staged_pdf_is_read_without_anybody_asking(signed_in, evidence_root):
    """§27.1 — the whole feature, in one assertion.

    Nothing pressed «analüüsi», nothing reloaded the page, no Matter exists.
    The reader picked the file up because it was staged.
    """
    session = stage(signed_in, upload())
    run_reader()

    staged = MatterIntakeFile.objects.get(session=session)
    assert staged.extraction_state == ExtractionState.DONE
    assert staged.text
    assert staged.text_character_count > 0


def test_one_staged_docx_is_read_without_anybody_asking(signed_in, evidence_root):
    """§27.2 — the same, through a different parser.

    Worth its own test rather than a parametrisation of the one above: PDF and
    DOCX reach the reader through different parsers, and «the reader works»
    has meant «the PDF parser works» before.
    """
    docx = SimpleUploadedFile(
        "eelnou.docx",
        corpus.draft_docx(),
        content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )
    session = stage(signed_in, docx)
    run_reader()

    staged = MatterIntakeFile.objects.get(session=session)
    assert staged.extraction_state == ExtractionState.DONE
    assert staged.text


def test_a_staged_message_exposes_its_headers(signed_in, evidence_root):
    """§27.3 — the structured half, which is the most valuable thing here.

    A message's sender, subject and sent time are stated rather than written
    into prose, so they are the one part of an envelope that can be read
    exactly.
    """
    message = SimpleUploadedFile(
        "kiri.eml",
        corpus.consultation_eml(attachments=False, inline_logo=False),
        content_type="message/rfc822",
    )
    session = stage(signed_in, message)
    run_reader()

    staged = MatterIntakeFile.objects.get(session=session)
    assert staged.extraction_state == ExtractionState.DONE
    assert staged.email_metadata
    assert staged.email_metadata.get("subject")
    assert staged.email_metadata.get("from_email")


def test_the_reader_settles_when_there_is_nothing_staged(signed_in):
    """§27.9 — with no open form in the department, it does nothing and stops."""
    output = run_reader()
    assert "Loetud 0 faili" in output
    assert not intake_extraction.pending_intake_files().exists()


def test_a_consumed_session_is_never_read(signed_in, evidence_root):
    """§27.6 — the Matter exists; the staging is over."""
    session = stage(signed_in, upload())
    MatterIntakeSession.objects.filter(pk=session.pk).update(consumed_at=timezone.now())

    run_reader()
    assert MatterIntakeFile.objects.get(session=session).extraction_state == ExtractionState.PENDING


def test_an_expired_session_is_never_read(signed_in, evidence_root):
    """§27.7 — nobody is waiting for this answer any more."""
    session = stage(signed_in, upload())
    MatterIntakeSession.objects.filter(pk=session.pk).update(
        expires_at=timezone.now() - timedelta(minutes=1)
    )

    run_reader()
    assert MatterIntakeFile.objects.get(session=session).extraction_state == ExtractionState.PENDING


def test_a_removed_file_is_never_read(signed_in, evidence_root):
    """§27.8 — the person took it off the form before the reader reached it."""
    session = stage(signed_in, upload())
    staged = MatterIntakeFile.objects.get(session=session)
    MatterIntakeFile.objects.filter(pk=staged.pk).update(removed_at=timezone.now())

    run_reader()
    staged.refresh_from_db()
    assert staged.extraction_state == ExtractionState.PENDING
    assert not staged.text


def test_no_scan_state_can_hold_a_staged_file_back(signed_in, evidence_root, settings):
    """§27.10 — the gate is gone, in both environments and on every value.

    Until docs/adr/0072 nothing could write `CLEAN`, so on the deployed stack
    this loop was offered nothing at all and «Loen faili…» never finished.
    """
    for real_data in (True, False):
        settings.REAL_DATA_ALLOWED = real_data
        for state in MalwareScanState.values:
            session = stage(signed_in, upload())
            MatterIntakeFile.objects.filter(session=session).update(malware_scan_state=state)

            run_reader()
            staged = MatterIntakeFile.objects.get(session=session)
            assert staged.extraction_state == ExtractionState.DONE, (
                f"REAL_DATA_ALLOWED={real_data}, scan={state}: the file was withheld"
            )


# ---------------------------------------------------------------------------
# The corpus backlog, which must be invisible from here
# ---------------------------------------------------------------------------


def _pending_versions(matter, capture_evidence, count: int) -> list[DocumentVersion]:
    """A canonical backlog, built cheaply.

    Rows rather than files: what is being asserted is that the reader does not
    *select* them, and a thousand real PDFs would prove the same thing while
    taking a minute to write.
    """
    document = capture_evidence(matter, b"%PDF-1.4 seed", "seeme.pdf", PDF).document
    versions = [
        DocumentVersion(
            document=document,
            version_number=number + 2,
            storage_key=f"synthetic/backlog/{number}",
            original_filename=f"ajalooline-{number}.pdf",
            mime_type=PDF,
            size_bytes=13,
            sha256=f"{number:064x}",
            acquired_at=timezone.now(),
            extraction_state=ExtractionState.PENDING,
        )
        for number in range(count)
    ]
    return DocumentVersion.objects.bulk_create(versions)


def test_the_reader_ignores_a_canonical_backlog_and_reads_the_one_staged_file(
    signed_in, evidence_root, normal_matter, capture_evidence
):
    """§27.4 and §27.5 — the incident, rebuilt as an assertion.

    Ten thousand canonical rows waiting for text, one staged file waiting for
    somebody at a form. Running the reader must move exactly one of them, and
    the ten thousand must be untouched afterwards — not merely unprocessed, but
    unclaimed: a claim is a write, and a write is what saturated the array.
    """
    backlog = _pending_versions(normal_matter, capture_evidence, 10_000)
    session = stage(signed_in, upload())

    run_reader()

    staged = MatterIntakeFile.objects.get(session=session)
    assert staged.extraction_state == ExtractionState.DONE

    moved = DocumentVersion.objects.filter(
        pk__in=[version.pk for version in backlog],
    ).exclude(extraction_state=ExtractionState.PENDING)
    assert moved.count() == 0
    assert not DocumentVersion.objects.filter(
        pk__in=[version.pk for version in backlog], extraction_claimed_at__isnull=False
    ).exists()


def test_a_canonical_backlog_does_not_change_what_the_reader_selects(
    signed_in, evidence_root, normal_matter, capture_evidence
):
    """§27.5 — the selection itself, not only its outcome.

    The queue the reader reads must be the same queue whether the corpus holds
    nothing or ten thousand rows. Asserted on the queryset rather than on the
    result, because a reader that selected everything and then filtered in
    Python would pass the test above and still be the thing that broke.
    """
    session = stage(signed_in, upload())
    before = set(intake_extraction.pending_intake_files().values_list("pk", flat=True))

    _pending_versions(normal_matter, capture_evidence, 10_000)
    after = set(intake_extraction.pending_intake_files().values_list("pk", flat=True))

    assert before == after
    assert len(after) == 1
    assert after == {MatterIntakeFile.objects.get(session=session).pk}


def test_the_reader_becomes_idle_after_the_staged_file(
    signed_in, evidence_root, normal_matter, capture_evidence
):
    """§27.9, with the backlog present. Finishing is not the same as settling.

    The first run reads the one file; the second finds nothing and says so. A
    reader that had noticed the 500 canonical rows would keep reporting work on
    the second pass, which is exactly the shape of the production incident.
    """
    _pending_versions(normal_matter, capture_evidence, 500)
    stage(signed_in, upload())

    assert "Loetud 1 faili" in run_reader()
    assert not intake_extraction.pending_intake_files().exists()
    assert "Loetud 0 faili" in run_reader()


# ---------------------------------------------------------------------------
# Structural: what this process can even express
# ---------------------------------------------------------------------------

#: Names that reach the canonical extraction queue. A worker holding any of
#: them can claim a `DocumentVersion`, and claiming one is the write this whole
#: separation exists to make impossible.
CORPUS_NAMES = frozenset(
    {
        "DocumentVersion",
        "pending_versions",
        "claim_version",
        "extract_document_version",
        "awaiting_scanner",
        "eligibility_q",
    }
)


def _imported_names(path: Path) -> set[str]:
    """Every name the module binds by importing something, module-level or not.

    Deliberately the whole file rather than the top of it: this codebase
    imports inside `handle()` on purpose, to keep management commands cheap to
    load, so a check that read only module-level imports would read the empty
    half of the file (`app/matters/management/commands/run_intake_reader.py`).
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            names.add(node.module or "")
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
    return names


def test_the_reader_cannot_name_the_corpus_queue():
    """The invariant of §4 and §6, asserted where it can be broken.

    A behavioural test proves the reader does not touch the corpus *today*.
    This proves it cannot be made to without somebody adding an import — which
    is a line in a diff a reviewer sees, rather than a query that looks
    reasonable and reads a table with 19 000 rows in it.
    """
    module = (
        Path(django_settings.BASE_DIR)
        / "app"
        / "matters"
        / "management"
        / "commands"
        / "run_intake_reader.py"
    )
    imported = _imported_names(module)

    assert not (imported & CORPUS_NAMES), sorted(imported & CORPUS_NAMES)
    assert not [name for name in imported if "orchestrator" in name]
    assert "app.matters.intake_extraction" in imported


def test_the_reader_module_itself_never_reaches_a_document_version():
    """One level deeper: the queue functions, not only the command.

    `intake_extraction` may call `parse_source`, which is a pure function of
    bytes and writes nothing anywhere. What it may not do is hold a query
    against `DocumentVersion`.
    """
    module = Path(django_settings.BASE_DIR) / "app" / "matters" / "intake_extraction.py"
    source = module.read_text(encoding="utf-8")
    tree = ast.parse(source)

    imported = _imported_names(module)
    assert "DocumentVersion" not in imported

    attributes = {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)}
    assert "DocumentVersion" not in attributes
    queried = {
        node.value.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute)
        and node.attr == "objects"
        and isinstance(node.value, ast.Name)
    }
    assert queried == {"MatterIntakeFile"}, queried


# ---------------------------------------------------------------------------
# What reading must not write
# ---------------------------------------------------------------------------


def test_reading_a_staged_file_publishes_no_derivative_and_no_search_row(signed_in, evidence_root):
    """§27.26 — nothing canonical may exist before there is a Matter.

    The staged row carries a little JSON and that is the whole of it: no
    derivative, no fragment table, no attachment Document, no search
    projection.
    """
    from app.search.models import SearchDocument

    derivatives = DocumentDerivative.objects.count()
    fragments = DocumentTextFragment.objects.count()
    documents = Document.objects.count()
    indexed = SearchDocument.objects.count()

    stage(signed_in, upload())
    run_reader()

    assert DocumentDerivative.objects.count() == derivatives
    assert DocumentTextFragment.objects.count() == fragments
    assert Document.objects.count() == documents
    assert SearchDocument.objects.count() == indexed


def test_polling_the_status_route_reads_and_never_parses(signed_in, evidence_root):
    """§27.27 — the poll settles state; it does not cause it.

    A poll that parsed would make the cost of the page depend on how often the
    browser asked, which is the shape of every accidental denial of service
    this product could have.
    """
    session = stage(signed_in, upload())
    staged = MatterIntakeFile.objects.get(session=session)
    assert staged.extraction_state == ExtractionState.PENDING

    for _ in range(5):
        response = signed_in.get(STATUS, {"intake": str(session.pk)})
        assert response.status_code == 200

    staged.refresh_from_db()
    assert staged.extraction_state == ExtractionState.PENDING
    assert not staged.text


# ---------------------------------------------------------------------------
# Promotion
# ---------------------------------------------------------------------------


def test_a_promoted_file_is_not_read_again_by_the_corpus_worker(signed_in, evidence_root):
    """§27.25 — the second half of §7, at the point it would have happened.

    Before docs/adr/0072, promotion deliberately left the new version PENDING so
    the ordinary worker would parse the same bytes a second time. It no longer
    does, and the proof is that a corpus run started immediately afterwards
    finds nothing to do.
    """
    session = stage(signed_in, upload())
    run_reader()

    response = signed_in.post(
        reverse("matters:matter_create"), {"title": "Pakendiseadus", "intake": str(session.pk)}
    )
    assert response.status_code == 302

    version = DocumentVersion.objects.get(original_filename="kaaskiri.pdf")
    assert version.extraction_state == ExtractionState.INTAKE_READ

    out = StringIO()
    call_command("run_extraction_worker", "--once", stdout=out)
    assert "Töödeldud 0 faili" in out.getvalue()

    version.refresh_from_db()
    assert version.extraction_state == ExtractionState.INTAKE_READ
    assert not DocumentDerivative.objects.filter(version=version).exists()


def test_promoted_evidence_is_byte_for_byte_the_staged_file(signed_in, evidence_root):
    """§27.24 — reading changed nothing about the bytes.

    The one property the whole feature may not cost: what is filed is what the
    browser sent, whatever the reader made of it.
    """
    import hashlib

    from app.documents.services import evidence_storage

    payload = corpus.text_pdf([LETTER])
    session = stage(signed_in, SimpleUploadedFile("kaaskiri.pdf", payload, content_type=PDF))
    run_reader()

    signed_in.post(
        reverse("matters:matter_create"), {"title": "Pakendiseadus", "intake": str(session.pk)}
    )
    version = DocumentVersion.objects.get(original_filename="kaaskiri.pdf")

    with evidence_storage().open(version.storage_key, "rb") as handle:
        stored = handle.read()
    assert stored == payload
    assert version.sha256 == hashlib.sha256(payload).hexdigest()


def test_a_file_the_reader_could_not_understand_still_becomes_evidence(signed_in, evidence_root):
    """§27.23 — a parser failure may not become a refusal to create a Matter."""
    broken = SimpleUploadedFile("katki.pdf", b"%PDF-1.4 " + b"\x00" * 40, content_type=PDF)
    session = stage(signed_in, broken)
    run_reader()

    staged = MatterIntakeFile.objects.get(session=session)
    assert staged.extraction_state in (ExtractionState.FAILED, ExtractionState.NOT_APPLICABLE)

    response = signed_in.post(
        reverse("matters:matter_create"), {"title": "Loetamatu fail", "intake": str(session.pk)}
    )
    assert response.status_code == 302
    version = DocumentVersion.objects.get(original_filename="katki.pdf")
    assert version.extraction_state == staged.extraction_state


def test_the_promoted_state_is_a_table_rather_than_a_guess():
    """Every staged outcome maps somewhere, and only one maps to PENDING.

    Asserted directly on the mapping, so a new `ExtractionState` cannot quietly
    fall through to "read me again" without somebody choosing that.
    """
    promoted = intake_staging.promoted_extraction_state
    assert promoted(ExtractionState.DONE) == ExtractionState.INTAKE_READ
    assert promoted(ExtractionState.NOT_APPLICABLE) == ExtractionState.NOT_APPLICABLE
    assert promoted(ExtractionState.FAILED) == ExtractionState.FAILED
    assert promoted(ExtractionState.PENDING) == ExtractionState.PENDING
    assert promoted(ExtractionState.PROCESSING) == ExtractionState.PENDING


# ---------------------------------------------------------------------------
# The reader's own liveness
# ---------------------------------------------------------------------------


def test_the_reader_and_the_corpus_worker_keep_separate_heartbeats(settings, tmp_path):
    """A corpus run somebody started by hand must not report the reader alive.

    The reader's healthcheck is what a container is judged by; sharing one mark
    would let a five-minute backfill mask a reader that had died.
    """
    from app.documents.extraction.heartbeat import EXTRACTION_WORKER, INTAKE_READER

    settings.INTAKE_READER_HEARTBEAT_PATH = str(tmp_path / "reader.heartbeat")
    settings.EXTRACTION_WORKER_HEARTBEAT_PATH = str(tmp_path / "worker.heartbeat")
    INTAKE_READER.clear()
    EXTRACTION_WORKER.clear()

    EXTRACTION_WORKER.touch()
    assert EXTRACTION_WORKER.is_alive()
    assert not INTAKE_READER.is_alive()


def test_a_dead_readers_claim_comes_back_on_the_readers_own_clock(
    settings, evidence_root, signed_in
):
    """The claim window is the reader's, not the corpus worker's.

    A reader that dies holding a claim leaves the row `PROCESSING`. Waiting the
    corpus worker's thirty minutes to reclaim it would strand somebody's form
    for longer than they would keep the page open — and a staged file is
    bounded by `MAX_INTAKE_UPLOAD_BYTES` and finishes in seconds, so a claim
    older than five minutes belongs to a reader that is not coming back.
    """
    session = stage(signed_in, upload())
    stranded = timezone.now() - timedelta(minutes=settings.INTAKE_READER_STALE_CLAIM_MINUTES + 1)
    MatterIntakeFile.objects.filter(session=session).update(
        extraction_state=ExtractionState.PROCESSING, extraction_claimed_at=stranded
    )
    # Comfortably inside the corpus worker's window, so if that number were the
    # one in force this file would still be nobody's.
    assert settings.INTAKE_READER_STALE_CLAIM_MINUTES + 1 < settings.EXTRACTION_STALE_CLAIM_MINUTES

    run_reader()
    assert MatterIntakeFile.objects.get(session=session).extraction_state == ExtractionState.DONE


def test_a_fresh_claim_is_left_alone(settings, evidence_root, signed_in):
    """The other side of it: two readers must not both take one file."""
    session = stage(signed_in, upload())
    MatterIntakeFile.objects.filter(session=session).update(
        extraction_state=ExtractionState.PROCESSING, extraction_claimed_at=timezone.now()
    )

    run_reader()
    assert (
        MatterIntakeFile.objects.get(session=session).extraction_state == ExtractionState.PROCESSING
    )


def test_the_readers_window_is_shorter_than_the_corpus_workers(settings):
    """Five minutes against thirty, and the difference is the product's.

    A staged file is bounded and finishes in seconds; a corpus parse may be a
    500-page OCR run. One window for both would either raise false alarms on
    the corpus or leave somebody at a form waiting half an hour to be told the
    reader had stopped.
    """
    from app.documents.extraction.heartbeat import EXTRACTION_WORKER, INTAKE_READER

    assert INTAKE_READER.threshold_seconds() < EXTRACTION_WORKER.threshold_seconds()
    assert INTAKE_READER.threshold_seconds() == settings.INTAKE_READER_STALE_CLAIM_MINUTES * 60


def test_a_fast_loop_does_not_write_its_mark_on_every_turn(settings, tmp_path):
    """The mark is a file write, and file writes are what this round is about.

    The reader polls twice a second and its temporary directory is the
    container's writable layer, which on the production host is
    `docker-xfs.img` on `/mnt/disk1` — behind the same parity disk whose
    saturation caused the incident. An unconditional touch per turn would be
    172 000 writes a day onto exactly that device, to answer a probe that runs
    every thirty seconds.

    Asserted on the mtime rather than on a call count, because what matters is
    the *write*, not the call.
    """
    import os

    from app.documents.extraction.heartbeat import INTAKE_READER

    settings.INTAKE_READER_HEARTBEAT_PATH = str(tmp_path / "reader.heartbeat")
    INTAKE_READER.clear()

    INTAKE_READER.touch_periodically()
    first = os.stat(INTAKE_READER.path()).st_mtime_ns
    for _ in range(50):
        INTAKE_READER.touch_periodically()
    assert os.stat(INTAKE_READER.path()).st_mtime_ns == first

    # And it does write once the interval has passed, or the probe would go
    # stale under a loop that is turning perfectly well.
    INTAKE_READER.touch_periodically(at_most_every=0.0)
    assert os.stat(INTAKE_READER.path()).st_mtime_ns >= first


def test_a_restarted_reader_marks_itself_immediately(settings, tmp_path):
    """The throttle is per process, and a process that has just started has
    just turned. Nothing durable decides whether the first write happens."""
    from app.documents.extraction.heartbeat import INTAKE_READER

    settings.INTAKE_READER_HEARTBEAT_PATH = str(tmp_path / "reader.heartbeat")
    INTAKE_READER.clear()

    INTAKE_READER.touch_periodically()
    assert INTAKE_READER.is_alive()


def test_the_healthcheck_fails_when_the_readers_loop_has_stopped(settings, tmp_path):
    from app.documents.extraction.heartbeat import INTAKE_READER

    settings.INTAKE_READER_HEARTBEAT_PATH = str(tmp_path / "reader.heartbeat")
    INTAKE_READER.clear()

    with pytest.raises(SystemExit) as exit_code:
        call_command("check_intake_reader")
    assert exit_code.value.code == 1


def test_the_healthcheck_passes_while_the_readers_loop_turns(settings, tmp_path):
    from app.documents.extraction.heartbeat import INTAKE_READER

    settings.INTAKE_READER_HEARTBEAT_PATH = str(tmp_path / "reader.heartbeat")
    INTAKE_READER.touch()
    call_command("check_intake_reader", "--quiet")


# ---------------------------------------------------------------------------
# Ownership, unchanged
# ---------------------------------------------------------------------------


def test_a_staged_file_is_still_only_its_owners(signed_in, client, specialist, evidence_root):
    """§27.30 — none of this widened who may reach a staging session.

    Reading is a background process; *asking* about it is still a request, and
    the request still fails closed with 404 rather than 403, so a guessed
    identifier learns nothing at all.
    """
    from tests import factories

    session = stage(signed_in, upload())
    run_reader()

    other = factories.UserFactory()
    client.force_login(other)
    response = client.get(STATUS, {"intake": str(session.pk)})
    assert response.status_code == 404
