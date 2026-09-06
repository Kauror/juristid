"""Two people press «Seo teemaga» at once, and one relation exists afterwards.

Real transactions on real PostgreSQL, because the guarantee under test is the
database's: the pair is canonicalised before the insert and the unique
constraint decides the race, while `get_or_create` turns the loser's constraint
violation back into a read rather than into an error somebody sees.
"""

from __future__ import annotations

import threading

import pytest
from django.db import connections

from app.related_materials import services
from app.related_materials.models import (
    MatterBackgroundMaterial,
    MatterRelation,
    RelatedSuggestionDismissal,
)
from tests import factories

pytestmark = pytest.mark.django_db(transaction=True, serialized_rollback=True)


def _run_together(*targets):
    """Start every callable on the same signal; fail on the first exception."""
    barrier = threading.Barrier(len(targets))
    errors: list[BaseException] = []

    def wrapped(target):
        def run() -> None:
            try:
                barrier.wait(timeout=15)
                target()
            except BaseException as error:  # reported after every thread joins
                errors.append(error)
            finally:
                connections.close_all()

        return run

    threads = [threading.Thread(target=wrapped(target)) for target in targets]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
    assert not errors, errors


def test_two_simultaneous_links_of_one_pair_leave_one_row():
    first_user = factories.UserFactory()
    second_user = factories.UserFactory()
    first = factories.MatterFactory(owner=first_user, reference_year=2099, reference_number=1)
    second = factories.MatterFactory(owner=first_user, reference_year=2099, reference_number=2)

    _run_together(
        lambda: services.link_related_matters(matter=first, other=second, actor=first_user),
        lambda: services.link_related_matters(matter=second, other=first, actor=second_user),
    )

    assert MatterRelation.objects.count() == 1


def test_two_simultaneous_dismissals_leave_one_row():
    user = factories.UserFactory()
    first = factories.MatterFactory(owner=user, reference_year=2099, reference_number=3)
    second = factories.MatterFactory(owner=user, reference_year=2099, reference_number=4)

    _run_together(
        lambda: services.dismiss_related_suggestion(
            matter=first, actor=user, candidate_matter=second
        ),
        lambda: services.dismiss_related_suggestion(
            matter=first, actor=user, candidate_matter=second
        ),
    )

    assert RelatedSuggestionDismissal.objects.count() == 1


def test_two_simultaneous_background_selections_leave_one_row():
    user = factories.UserFactory()
    source = factories.MatterFactory(owner=user, reference_year=2099, reference_number=5)
    current = factories.MatterFactory(owner=user, reference_year=2099, reference_number=6)
    opinion = factories.SubmissionFactory(matter=source, title="Arvamus")

    _run_together(
        lambda: services.add_background_submission(matter=current, submission=opinion, actor=user),
        lambda: services.add_background_submission(matter=current, submission=opinion, actor=user),
    )

    assert MatterBackgroundMaterial.objects.count() == 1
