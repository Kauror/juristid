"""A DONE version whose text is gone is found, not left to drop out of search (ENG-144).

The audit deleted a text version's derivative rows by hand. `extraction_state`
stayed DONE, the search row cascaded away and the hits fell by one, the version
was not in `pending_versions()`, and both integrity checks said nothing was
wrong. `check_derivative_integrity` now asks the question they did not: every
parse that succeeds writes an ACTIVE extracted or recognised text in the same
transaction that says DONE, so a DONE version without one lost it afterwards.
"""

from __future__ import annotations

from io import StringIO

import pytest
from django.core.management import call_command

from app.documents.derivative_integrity import DONE_WITHOUT_TEXT_DERIVATIVE, check_derivatives
from app.documents.enums import DerivativeKind, DerivativeStatus, ExtractionState
from app.documents.extraction.orchestrator import pending_versions
from app.documents.models import DocumentDerivative, DocumentVersion
from tests import synthetic_corpus as corpus

pytestmark = pytest.mark.django_db

TEXT = "text/plain"


@pytest.fixture
def extracted_memo(normal_matter, capture_evidence, extract):
    version = capture_evidence(normal_matter, corpus.memo_txt(), "memo.txt", TEXT)
    extract(version)
    version.refresh_from_db()
    assert version.extraction_state == ExtractionState.DONE
    return version


def _text_rows(version):
    return DocumentDerivative._base_manager.filter(
        version=version, kind__in=[DerivativeKind.EXTRACTED_TEXT, DerivativeKind.OCR_TEXT]
    )


def _lost(report) -> list[str]:
    return [finding.subject for finding in report.by_kind().get(DONE_WITHOUT_TEXT_DERIVATIVE, [])]


def test_an_extracted_version_is_clean(extracted_memo):
    assert _text_rows(extracted_memo).filter(status=DerivativeStatus.ACTIVE).exists()
    assert _lost(check_derivatives(scan_storage=False)) == []


def test_a_done_version_whose_text_was_deleted_is_named(extracted_memo):
    """The audit's reproduction: the rows go, the state stays DONE."""
    _text_rows(extracted_memo).delete()

    extracted_memo.refresh_from_db()
    assert extracted_memo.extraction_state == ExtractionState.DONE
    assert not pending_versions().filter(pk=extracted_memo.pk).exists()

    report = check_derivatives(scan_storage=False)
    assert _lost(report) == [f"version {extracted_memo.pk}"]
    # A kind and an id; the file's name and contents are not the operator's
    # output to carry.
    finding = report.by_kind()[DONE_WITHOUT_TEXT_DERIVATIVE][0]
    assert finding.detail == TEXT
    assert "memo" not in finding.subject + finding.detail


def test_a_text_that_is_only_superseded_counts_as_lost(extracted_memo):
    _text_rows(extracted_memo).update(status=DerivativeStatus.SUPERSEDED)
    assert _lost(check_derivatives(scan_storage=False)) == [f"version {extracted_memo.pk}"]


def test_the_command_exits_non_zero_and_the_rebuild_clears_it(extracted_memo):
    _text_rows(extracted_memo).delete()

    output = StringIO()
    with pytest.raises(SystemExit) as exit_:
        call_command("check_derivative_integrity", "--skip-storage-scan", stdout=output)
    assert exit_.value.code == 1
    assert f"{DONE_WITHOUT_TEXT_DERIVATIVE}: 1" in output.getvalue()

    call_command(
        "rebuild_document_derivatives", "--version-id", str(extracted_memo.pk), stdout=StringIO()
    )

    output = StringIO()
    call_command("check_derivative_integrity", stdout=output)
    assert "No derivative integrity problems found." in output.getvalue()


@pytest.mark.parametrize(
    "state",
    [
        ExtractionState.PENDING,
        ExtractionState.FAILED,
        ExtractionState.NOT_APPLICABLE,
        ExtractionState.INTAKE_READ,
    ],
)
def test_a_version_that_never_promised_text_is_not_a_finding(
    normal_matter, capture_evidence, state
):
    """Only DONE promises a derivative; every other state writes none, by design."""
    version = capture_evidence(normal_matter, corpus.memo_txt(), "memo.txt", TEXT)
    DocumentVersion._base_manager.filter(pk=version.pk).update(extraction_state=state)

    assert _lost(check_derivatives(scan_storage=False)) == []
