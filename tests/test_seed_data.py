"""The synthetic development seed."""

from __future__ import annotations

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from app.accounts.models import User
from app.documents.models import Document, DocumentVersion
from app.legacy_import.models import MatterSourceReference
from app.matters.enums import RecordMode
from app.matters.models import Matter

pytestmark = pytest.mark.django_db


def test_seed_creates_usable_synthetic_data(settings):
    settings.DEBUG = True
    settings.REAL_DATA_ALLOWED = False

    call_command("seed_dev_data", matters=4, verbosity=0)

    assert User.objects.filter(is_synthetic=True).count() == 4
    assert User.objects.filter(is_synthetic=False).count() == 0
    assert Matter.objects.filter(record_mode=RecordMode.FULL).count() == 4
    assert Matter.objects.filter(record_mode=RecordMode.ARCHIVE).count() == 2
    assert Document.objects.count() == 4
    assert DocumentVersion.objects.count() == 4
    assert MatterSourceReference.objects.count() == 2


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
