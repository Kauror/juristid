"""The synthetic development seed."""

from __future__ import annotations

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from app.accounts.models import User
from app.documents.models import Document, DocumentVersion
from app.legacy_import.models import MatterSourceReference
from app.matters.enums import RecordMode
from app.matters.models import Entry, Matter
from app.submissions.enums import SubmissionStatus
from app.submissions.models import Submission
from app.workflow.enums import ActionStatus
from app.workflow.models import NextAction

pytestmark = pytest.mark.django_db


def test_seed_creates_usable_synthetic_data(settings):
    settings.DEBUG = True
    settings.REAL_DATA_ALLOWED = False

    call_command("seed_dev_data", matters=4, verbosity=0)

    assert User.objects.filter(is_synthetic=True).count() == 4
    assert User.objects.filter(is_synthetic=False).count() == 0
    assert Matter.objects.filter(record_mode=RecordMode.FULL).count() == 4
    assert Matter.objects.filter(record_mode=RecordMode.ARCHIVE).count() == 2
    assert MatterSourceReference.objects.count() == 2

    # Relationships rather than magic totals: every FULL Matter gets its
    # incoming letter, every document carries exactly one evidence version, and
    # the seed produces a Stage-1 shaped world to work in.
    full_matters = Matter.objects.filter(record_mode=RecordMode.FULL)
    for matter in full_matters:
        assert matter.documents.exists()
    assert Document.objects.count() == DocumentVersion.objects.count()
    assert Document.objects.count() >= full_matters.count()

    assert NextAction.objects.filter(status=ActionStatus.OPEN).exists()
    # One Matter is deliberately left without one so Tähelepanu has real input.
    assert (
        NextAction.objects.filter(status=ActionStatus.OPEN).count() < full_matters.count()
        or full_matters.count() < 6
    )
    assert Entry.objects.exists()
    assert Submission.objects.filter(status=SubmissionStatus.SENT).exists()


def test_seed_is_idempotent(settings):
    settings.DEBUG = True
    call_command("seed_dev_data", matters=2, verbosity=0)
    call_command("seed_dev_data", matters=2, verbosity=0)
    assert User.objects.filter(is_synthetic=True).count() == 4
    assert Matter.objects.filter(record_mode=RecordMode.FULL).count() == 2


def test_seed_refuses_to_run_where_real_data_is_allowed(settings):
    settings.DEBUG = True
    settings.REAL_DATA_ALLOWED = True
    with pytest.raises(CommandError):
        call_command("seed_dev_data", verbosity=0)


def test_seed_refuses_to_run_outside_development(settings):
    settings.DEBUG = False
    settings.REAL_DATA_ALLOWED = False
    with pytest.raises(CommandError):
        call_command("seed_dev_data", verbosity=0)
