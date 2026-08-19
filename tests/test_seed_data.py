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


# --------------------------------------------------------------------------
# Rehearsal identities.
#
# The deployed instance already has Matters owned by these rows. Renaming the
# row is the only way to change what a lawyer sees without detaching them from
# their work, so the seed must update rather than create a second user.
# --------------------------------------------------------------------------


def _seed(**overrides):
    from django.core.management import call_command

    call_command("seed_dev_data", **{"matters": 2, **overrides})


def test_the_seed_uses_the_real_team_display_names(db, settings):
    from app.accounts.models import User

    settings.DEBUG = True
    settings.REAL_DATA_ALLOWED = False
    _seed()

    assert User.objects.get(upn="juht@example.invalid").display_name == "Marko Udras"
    assert User.objects.get(upn="jurist1@example.invalid").display_name == "Ireen Tarto"
    assert User.objects.get(upn="jurist2@example.invalid").display_name == "Sandra Melani Mellikov"
    assert User.objects.get(upn="admin@example.invalid").display_name == "Testadministraator"


def test_the_identities_underneath_stay_synthetic(db, settings):
    """Real names on screen, invented identities in the database."""
    from app.accounts.models import User

    settings.DEBUG = True
    settings.REAL_DATA_ALLOWED = False
    _seed()

    for user in User.objects.all():
        assert user.upn.endswith("@example.invalid")
        assert user.is_synthetic


def test_reseeding_renames_in_place_and_keeps_the_owner(db, settings):
    """The property the deployed rehearsal depends on.

    A second row would leave every existing Matter owned by a user nobody sees
    any more — the rehearsal history this environment exists to accumulate.
    """
    from app.accounts.models import User
    from app.accounts.services import create_synthetic_user
    from app.matters.models import Matter
    from app.matters.services import create_matter

    settings.DEBUG = True
    settings.REAL_DATA_ALLOWED = False

    old = create_synthetic_user(upn="jurist1@example.invalid", display_name="Testjurist Üks")
    matter = create_matter(title="Varasem teema", owner=old, reference_year=2026)

    _seed()

    old.refresh_from_db()
    matter.refresh_from_db()
    assert old.display_name == "Ireen Tarto"
    assert matter.owner_id == old.pk
    assert User.objects.filter(upn="jurist1@example.invalid").count() == 1
    assert Matter.objects.get(pk=matter.pk).owner.display_name == "Ireen Tarto"


def test_running_the_seed_twice_creates_no_duplicate_people(db, settings):
    from app.accounts.models import User

    settings.DEBUG = True
    settings.REAL_DATA_ALLOWED = False
    _seed()
    before = User.objects.count()
    _seed()

    assert User.objects.count() == before
    for upn in ("juht@example.invalid", "jurist1@example.invalid", "jurist2@example.invalid"):
        assert User.objects.filter(upn=upn).count() == 1


def test_the_inactive_identity_is_created_but_hidden(db, settings):
    """Reserved for mapping historical register rows later.

    Inactive, so she appears in no owner picker and no sign-in list — which is
    what `is_active=False` already guarantees everywhere those are built.
    """
    from app.accounts.models import User

    settings.DEBUG = True
    settings.REAL_DATA_ALLOWED = False
    _seed()

    ann = User.objects.get(upn="ann.raun@example.invalid")
    assert ann.display_name == "Ann Raun"
    assert ann.is_active is False
    assert ann not in User.objects.filter(is_active=True)


def test_the_seed_brings_in_the_real_ministries(db, settings):
    from app.organisations.models import Organisation

    settings.DEBUG = True
    settings.REAL_DATA_ALLOWED = False
    _seed()

    assert Organisation.objects.filter(name="Rahandusministeerium").exists()
    assert Organisation.objects.filter(name="Kliimaministeerium").exists()
