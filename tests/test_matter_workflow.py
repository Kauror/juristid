"""Matter lifecycle service functions, and the queries behind the surfaces.

Also the N+1 guard: Minu töö and Teemad must not issue a query per row, because
the register is meant to stay usable at years of scale.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from app.audit.enums import ChangeEventType
from app.audit.models import ChangeEvent
from app.core.errors import DomainError
from app.matters import selectors
from app.matters.models import Matter
from app.matters.services import (
    assign_matter,
    change_stage,
    change_track,
    close_matter,
    create_matter,
    reopen_matter,
    set_matter_dates,
    set_organisations,
    set_position,
)
from app.workflow.enums import Track
from app.workflow.services import set_next_action
from tests import factories

pytestmark = pytest.mark.django_db


def _days(offset: int):
    return timezone.localdate() + timedelta(days=offset)


# -- creation ---------------------------------------------------------------


def test_only_a_title_is_required(specialist):
    matter = create_matter(title="Pakendiseaduse muutmise eelnõu", actor=specialist)
    assert matter.display_reference
    assert matter.owner is None
    assert matter.stage is None
    assert matter.is_open is True


def test_creation_assigns_a_human_reference(specialist):
    first = create_matter(title="Esimene", actor=specialist)
    second = create_matter(title="Teine", actor=specialist)
    year = timezone.localdate().year
    assert first.reference_year == year
    assert second.reference_number == first.reference_number + 1


def test_creation_is_audited(specialist):
    matter = create_matter(title="Uus", actor=specialist)
    assert ChangeEvent.objects.filter(
        matter=matter, event_type=ChangeEventType.MATTER_CREATED
    ).exists()


def test_an_empty_title_is_refused(specialist):
    with pytest.raises(DomainError):
        create_matter(title="   ", actor=specialist)


# -- ownership and stage ----------------------------------------------------


def test_assigning_an_owner_is_recorded(normal_matter, specialist, other_specialist):
    assign_matter(matter=normal_matter, owner=other_specialist, actor=specialist)
    normal_matter.refresh_from_db()
    assert normal_matter.owner == other_specialist

    event = ChangeEvent.objects.filter(event_type=ChangeEventType.MATTER_ASSIGNED).latest(
        "created_at"
    )
    assert event.payload["to_name"] == other_specialist.display_name


def test_assigning_the_same_owner_writes_nothing(normal_matter, specialist):
    before = ChangeEvent.objects.count()
    assign_matter(matter=normal_matter, owner=normal_matter.owner, actor=specialist)
    assert ChangeEvent.objects.count() == before


def test_changing_stage_is_recorded_with_both_labels(normal_matter, specialist, stage):
    change_stage(matter=normal_matter, stage=stage, actor=specialist)
    normal_matter.refresh_from_db()
    assert normal_matter.stage == stage

    event = ChangeEvent.objects.get(event_type=ChangeEventType.MATTER_STAGE_CHANGED)
    assert event.payload["to_label"] == stage.label_et


def test_changing_track_is_validated(normal_matter, specialist):
    change_track(matter=normal_matter, track=Track.DOMESTIC, actor=specialist)
    normal_matter.refresh_from_db()
    assert normal_matter.track == Track.DOMESTIC

    with pytest.raises(DomainError):
        change_track(matter=normal_matter, track="NONSENSE", actor=specialist)


# -- institutions -----------------------------------------------------------


def test_source_and_addressee_are_independent(normal_matter, specialist):
    """KELLELT and KELLELE are different facts and are never unified."""
    sender = factories.OrganisationFactory(name="Kliimaministeerium")
    recipient = factories.OrganisationFactory(name="Rahandusministeerium")

    set_organisations(matter=normal_matter, source_organisations=[sender], actor=specialist)
    set_organisations(matter=normal_matter, addressee_organisation=recipient, actor=specialist)
    normal_matter.refresh_from_db()

    assert list(normal_matter.source_organisations.all()) == [sender]
    assert normal_matter.addressee_organisation == recipient


def test_setting_one_institution_leaves_the_other_alone(normal_matter, specialist):
    sender = factories.OrganisationFactory()
    recipient = factories.OrganisationFactory()
    set_organisations(
        matter=normal_matter,
        source_organisations=[sender],
        addressee_organisation=recipient,
        actor=specialist,
    )

    set_organisations(matter=normal_matter, source_organisations=[], actor=specialist)
    normal_matter.refresh_from_db()
    assert not normal_matter.source_organisations.exists()
    assert normal_matter.addressee_organisation == recipient


# -- dates and position -----------------------------------------------------


def test_dates_are_recorded_and_clearable(normal_matter, specialist):
    set_matter_dates(
        matter=normal_matter,
        received_date=_days(-5),
        response_deadline=_days(10),
        actor=specialist,
    )
    normal_matter.refresh_from_db()
    assert normal_matter.received_date == _days(-5)

    set_matter_dates(matter=normal_matter, response_deadline=None, actor=specialist)
    normal_matter.refresh_from_db()
    assert normal_matter.response_deadline is None
    assert normal_matter.received_date == _days(-5)


def test_position_and_rationale_are_separate(normal_matter, specialist):
    set_position(
        matter=normal_matter,
        position_summary="Koda ei toeta pakendiaktsiisi tõusu.",
        rationale_summary="Liikmete hinnangul kasvab halduskoormus.",
        actor=specialist,
    )
    normal_matter.refresh_from_db()
    assert "pakendiaktsiisi" in normal_matter.position_summary
    assert "halduskoormus" in normal_matter.rationale_summary


# -- closure ----------------------------------------------------------------


def test_closing_requires_a_known_disposition(normal_matter, specialist):
    with pytest.raises(DomainError):
        close_matter(matter=normal_matter, disposition="MADE_UP", actor=specialist)


def test_closing_and_reopening_round_trip(normal_matter, specialist):
    close_matter(matter=normal_matter, disposition="COMPLETED", actor=specialist, reason="Valmis")
    normal_matter.refresh_from_db()
    assert normal_matter.is_open is False
    assert normal_matter.closed_at is not None
    assert normal_matter.closed_by == specialist

    reopen_matter(matter=normal_matter, actor=specialist)
    normal_matter.refresh_from_db()
    assert normal_matter.is_open is True
    assert normal_matter.disposition == ""
    assert normal_matter.closed_at is None

    types = set(
        ChangeEvent.objects.filter(matter=normal_matter).values_list("event_type", flat=True)
    )
    assert {ChangeEventType.MATTER_CLOSED, ChangeEventType.MATTER_REOPENED} <= types


def test_a_stage_change_does_not_close_the_matter(normal_matter, specialist):
    """`jõustunud` is where the process is, not whether Koda is finished."""
    from app.workflow.models import StageVocabulary

    in_force = StageVocabulary.objects.get(key="in_force")
    change_stage(matter=normal_matter, stage=in_force, actor=specialist)
    normal_matter.refresh_from_db()
    assert normal_matter.is_open is True


# -- query cost -------------------------------------------------------------


def test_the_register_does_not_query_per_row(signed_in, specialist):
    """A page of 25 rows must cost a fixed number of queries, not 25 more."""
    for index in range(25):
        matter = factories.MatterFactory(owner=specialist, title=f"Teema {index}")
        set_next_action(
            matter=matter, text=f"Tegevus {index}", actor=specialist, target_date=_days(3)
        )

    url = reverse("matters:matter_list")
    with CaptureQueriesContext(connection) as first:
        signed_in.get(url)

    for index in range(25, 50):
        matter = factories.MatterFactory(owner=specialist, title=f"Teema {index}")
        set_next_action(
            matter=matter, text=f"Tegevus {index}", actor=specialist, target_date=_days(3)
        )

    with CaptureQueriesContext(connection) as second:
        signed_in.get(url)

    # Same page size, twice the data: the query count must not follow the rows.
    assert len(second) == len(first)


def test_my_work_query_count_is_bounded(signed_in, specialist):
    for index in range(20):
        matter = factories.MatterFactory(owner=specialist, title=f"Teema {index}")
        set_next_action(
            matter=matter, text=f"Tegevus {index}", actor=specialist, target_date=_days(2)
        )

    with CaptureQueriesContext(connection) as captured:
        response = signed_in.get(reverse("matters:my_work"))

    assert response.status_code == 200
    # Generous, but far below one-per-row: this catches a regression into N+1,
    # not a precise budget.
    assert len(captured) < 40


def test_matter_detail_query_count_is_bounded(signed_in, specialist):
    from app.matters.services import add_entry

    matter = factories.MatterFactory(owner=specialist)
    set_next_action(matter=matter, text="Tegevus", actor=specialist, target_date=_days(2))
    for index in range(30):
        add_entry(matter=matter, body=f"<p>Sissekanne {index}</p>", author=specialist)

    with CaptureQueriesContext(connection) as captured:
        response = signed_in.get(reverse("matters:matter_detail", kwargs={"pk": matter.pk}))

    assert response.status_code == 200
    assert len(captured) < 40


def test_selectors_reuse_the_prefetched_open_action(specialist):
    matter = factories.MatterFactory(owner=specialist)
    set_next_action(matter=matter, text="Tegevus", actor=specialist, target_date=_days(1))

    rows = list(selectors.matter_list_queryset(specialist))
    with CaptureQueriesContext(connection) as captured:
        for row in rows:
            selectors.current_action_of(row)
    assert len(captured) == 0


def test_matter_str_survives_a_missing_reference():
    archive = factories.ArchiveMatterFactory(title="Ajalooline rida")
    assert "Ajalooline rida" in str(archive)
    assert archive.display_reference == ""


def test_visible_to_is_the_only_supported_read(specialist):
    factories.MatterFactory(owner=specialist)
    assert Matter.objects.visible_to(specialist).count() == 1
