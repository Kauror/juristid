"""The intake reader keeps reading while `Uus teema` offers nothing, and says so.

Audit finding AUD-025 and product decision D-6 (docs/adr/0072, amendment
2026-10-10). The reader runs whether or not the form shows what it found
(docs/adr/0088), and two things followed from that which were not true:

* a staged file the reader understood was promoted ``INTAKE_READ`` — *read on
  the form, the answers went onto it, nothing owed* — even when nothing went
  onto the form. That state is terminal: `pending_versions` never offers it, so
  the document was never extracted and never searchable by content, on the
  strength of a reading nobody received. With the suggestions off it is now
  promoted ``PENDING``, owed like any other upload;
* the reader's full extracted text stayed on the staged row after the bytes
  were gone, until somebody ran `prune_intake_staging` — which nothing
  schedules. It is now blanked when the session is consumed, and the reader
  blanks it for sessions that expired unconsumed.

And DX-6: the document page printed the raw enum ``INTAKE_READ``.
"""

from __future__ import annotations

from datetime import timedelta
from io import StringIO

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone

from app.documents.enums import ExtractionState
from app.documents.extraction.orchestrator import pending_versions
from app.documents.models import DocumentVersion
from app.documents.services import add_evidence_version
from app.matters import intake_staging
from app.matters.staging import MatterIntakeFile, MatterIntakeSession
from tests import synthetic_corpus as corpus

pytestmark = pytest.mark.django_db

CREATE = reverse("matters:matter_create")
STAGE = reverse("matters:intake_stage")
PDF = "application/pdf"

#: The switch the create form reads (`create_form_suggestions_offered`). Off is
#: the shipped default, so the tests without this run as production does.
READING_ON = override_settings(MATTER_INTAKE_SUGGESTIONS_ENABLED=True)
READING_OFF = override_settings(MATTER_INTAKE_SUGGESTIONS_ENABLED=False)

LETTER = """Näidisministeerium

Eesti Kaubandus-Tööstuskoda

Pakendiseaduse muutmise seaduse eelnõu kooskõlastamiseks

Palume esitada arvamus hiljemalt 18. septembriks 2026.
"""


def upload(name: str = "kaaskiri.pdf") -> SimpleUploadedFile:
    return SimpleUploadedFile(name, corpus.text_pdf([LETTER]), content_type=PDF)


def stage(client, *files) -> MatterIntakeSession:
    """Upload through the real route, the way the browser does."""
    response = client.post(STAGE, {"files": list(files)})
    assert response.status_code in (200, 400), response.status_code
    session = response.context["intake_session"]
    assert session is not None
    return session


def run_reader() -> str:
    out = StringIO()
    call_command("run_intake_reader", "--once", stdout=out)
    return out.getvalue()


def staged_read(client) -> MatterIntakeSession:
    """One letter staged and read by the real reader, text and all."""
    session = stage(client, upload())
    run_reader()
    staged = MatterIntakeFile.objects.get(session=session)
    assert staged.extraction_state == ExtractionState.DONE
    assert staged.text and staged.text_character_count > 0
    return session


def create_from(client, session: MatterIntakeSession, title: str) -> DocumentVersion:
    response = client.post(CREATE, {"title": title, "intake": str(session.pk)})
    assert response.status_code == 302, response.status_code
    return DocumentVersion.objects.get(document__matter__title=title)


# ---------------------------------------------------------------------------
# The promoted state says what happened on the form
# ---------------------------------------------------------------------------


@READING_OFF
def test_a_file_read_for_nobody_is_promoted_pending_and_owed(signed_in, evidence_root):
    """Suggestions off: the reader read it, nobody saw it, so nothing is settled.

    Through the real promotion path — staged, read by `run_intake_reader`,
    promoted by `Loo teema` — and the proof that it is owed is the corpus
    queue's own answer, not the column alone.
    """
    session = staged_read(signed_in)

    version = create_from(signed_in, session, "Loetud kellelegi")

    assert version.extraction_state == ExtractionState.PENDING
    assert version.extraction_note == ""
    assert pending_versions().filter(pk=version.pk).exists()


@READING_ON
def test_a_file_read_for_the_form_is_still_intake_read(signed_in, evidence_root):
    """Suggestions on: unchanged — read on the form, nothing owed, never re-read."""
    session = staged_read(signed_in)

    version = create_from(signed_in, session, "Loetud vormile")

    assert version.extraction_state == ExtractionState.INTAKE_READ
    assert not pending_versions().filter(pk=version.pk).exists()


def test_the_mapping_depends_on_the_form_only_for_done():
    """FAILED and NOT_APPLICABLE are verdicts about the bytes, not the form."""
    promoted = intake_staging.promoted_extraction_state
    for offered in (True, False):
        assert promoted(ExtractionState.FAILED, suggestions_offered=offered) == (
            ExtractionState.FAILED
        )
        assert promoted(ExtractionState.NOT_APPLICABLE, suggestions_offered=offered) == (
            ExtractionState.NOT_APPLICABLE
        )
        assert promoted(ExtractionState.PENDING, suggestions_offered=offered) == (
            ExtractionState.PENDING
        )
    assert promoted(ExtractionState.DONE, suggestions_offered=True) == ExtractionState.INTAKE_READ
    assert promoted(ExtractionState.DONE, suggestions_offered=False) == ExtractionState.PENDING

    # Asked of the setting when the caller has not already asked.
    with READING_OFF:
        assert promoted(ExtractionState.DONE) == ExtractionState.PENDING
    with READING_ON:
        assert promoted(ExtractionState.DONE) == ExtractionState.INTAKE_READ


# ---------------------------------------------------------------------------
# The extracted text lives as long as the session, not as long as the row
# ---------------------------------------------------------------------------


@READING_OFF
def test_creating_the_teema_clears_the_staged_text(signed_in, evidence_root):
    """`consume_session` drops the reading with the bytes; the row stays."""
    session = staged_read(signed_in)
    create_from(signed_in, session, "Tekst kaob")

    staged = MatterIntakeFile.objects.get(session=session)
    assert staged.text == []
    assert staged.text_character_count == 0
    assert staged.email_metadata is None
    # What happened is kept; only what was read is gone.
    assert staged.extraction_state == ExtractionState.DONE
    assert MatterIntakeSession.objects.get(pk=session.pk).consumed_at is not None


def test_consume_session_clears_mail_headers_too(signed_in, evidence_root):
    session = staged_read(signed_in)
    MatterIntakeFile.objects.filter(session=session).update(
        email_metadata={"from": "mari.naidis@naidisministeerium.invalid"}
    )

    intake_staging.consume_session(session)

    staged = MatterIntakeFile.objects.get(session=session)
    assert staged.email_metadata is None
    assert staged.text == []


def test_the_sweep_clears_expired_sessions_and_leaves_live_ones(signed_in, evidence_root):
    """Expired and never consumed: text blanked, row and stamps untouched."""
    expired = staged_read(signed_in)
    live = staged_read(signed_in)
    assert expired.pk != live.pk
    MatterIntakeSession.objects.filter(pk=expired.pk).update(
        expires_at=timezone.now() - timedelta(minutes=1)
    )

    cleared = intake_staging.clear_expired_intake_text()

    assert cleared == 1
    gone = MatterIntakeFile.objects.get(session=expired)
    assert gone.text == []
    assert gone.text_character_count == 0
    assert gone.email_metadata is None
    assert gone.extraction_state == ExtractionState.DONE
    assert MatterIntakeSession.objects.filter(pk=expired.pk, consumed_at__isnull=True).exists()

    kept = MatterIntakeFile.objects.get(session=live)
    assert kept.text
    assert kept.text_character_count > 0

    # Nothing left to clear writes nothing and says so.
    assert intake_staging.clear_expired_intake_text() == 0


def test_the_running_reader_sweeps_expired_text(signed_in, evidence_root):
    """The sweep is wired into the one process that is always running."""
    expired = staged_read(signed_in)
    MatterIntakeSession.objects.filter(pk=expired.pk).update(
        expires_at=timezone.now() - timedelta(minutes=1)
    )

    run_reader()

    assert MatterIntakeFile.objects.get(session=expired).text == []


def test_prune_still_deletes_what_the_sweep_only_blanked(signed_in, evidence_root):
    """The operator's command is unchanged: rows of stale sessions go."""
    expired = staged_read(signed_in)
    MatterIntakeSession.objects.filter(pk=expired.pk).update(
        expires_at=timezone.now() - timedelta(minutes=1)
    )
    intake_staging.clear_expired_intake_text()

    report = intake_staging.sweep_stale_sessions()

    assert report.sessions == 1
    assert not MatterIntakeSession.objects.filter(pk=expired.pk).exists()


# ---------------------------------------------------------------------------
# DX-6 — the label, not the enum
# ---------------------------------------------------------------------------


@READING_ON
def test_the_document_page_names_intake_read_in_words(signed_in, evidence_root, specialist):
    session = staged_read(signed_in)
    first = create_from(signed_in, session, "Kaks versiooni")
    assert first.extraction_state == ExtractionState.INTAKE_READ
    # The version table is drawn once there is more than one version.
    add_evidence_version(
        document=first.document,
        content=corpus.text_pdf(["Teine versioon"]),
        original_filename="kaaskiri-2.pdf",
        mime_type=PDF,
        uploaded_by=specialist,
    )

    response = signed_in.get(reverse("documents:document_detail", kwargs={"pk": first.document.pk}))

    assert response.status_code == 200
    body = response.content.decode()
    assert ExtractionState.INTAKE_READ.label in body
    assert "INTAKE_READ" not in body
