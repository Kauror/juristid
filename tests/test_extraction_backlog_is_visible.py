"""A canonical document waiting for extraction is never silent (ADR 0072 §3, D-6).

Nothing deployed extracts canonical documents on its own: the corpus worker is a
deliberate operator run since 2026-09-10. Since D-6, a file read on `Uus teema`
while suggestions are off is promoted `PENDING` — honestly owed — rather than
labelled read. So the deployment's own readiness report names the backlog and
the command that drains it, without failing a release over it.
"""

from __future__ import annotations

import pytest
from django.test import override_settings

from app.core import deployment
from app.documents.enums import ExtractionState
from app.documents.models import DocumentVersion
from app.documents.services import add_evidence_version
from tests import factories

pytestmark = pytest.mark.django_db


def _backlog_warnings(report) -> list[str]:
    return [w for w in report.warnings if "wait for text extraction" in w]


def test_no_backlog_no_warning():
    DocumentVersion.objects.filter(extraction_state=ExtractionState.PENDING).update(
        extraction_state=ExtractionState.DONE
    )
    report = deployment.readiness_report()

    assert _backlog_warnings(report) == []


@override_settings(MATTER_INTAKE_SUGGESTIONS_ENABLED=False)
def test_a_waiting_version_is_named_with_the_command_and_fails_nothing():
    version = add_evidence_version(
        document=factories.DocumentFactory(),
        content=b"%PDF-1.4 ootel",
        original_filename="ootel.pdf",
        mime_type="application/pdf",
    )
    assert version.extraction_state == ExtractionState.PENDING

    report = deployment.readiness_report()

    [warning] = _backlog_warnings(report)
    assert deployment.EXTRACTION_RUN_COMMAND in warning
    assert not any("extraction" in problem for problem in report.problems)
    assert deployment.extraction_backlog()[0] >= 1
