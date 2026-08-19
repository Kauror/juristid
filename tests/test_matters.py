"""Matter identity, record modes and the facts the archive must be allowed to omit."""

from __future__ import annotations

import pytest
from django.db import IntegrityError, transaction

from app.audit.enums import ChangeEventType
from app.audit.models import ChangeEvent
from app.core.errors import DomainError
from app.matters.enums import MatterOrigin, RecordMode
from app.matters.models import Matter, MatterReferenceSequence
from app.workflow.enums import Disposition
from tests import factories

pytestmark = pytest.mark.django_db


# -- human reference --------------------------------------------------------


def test_reference_allocation_increments_per_year():
    from app.matters.services import allocate_matter_reference

    assert allocate_matter_reference(2026) == (2026, 1)
    assert allocate_matter_reference(2026) == (2026, 2)
    assert allocate_matter_reference(2027) == (2027, 1)
    assert MatterReferenceSequence.objects.get(pk=2026).last_number == 2


def test_display_reference_is_derived_not_stored():
    matter = factories.MatterFactory(reference_year=2026, reference_number=7)
    assert matter.display_reference == "2026_7"
    assert not any(field.name == "display_reference" for field in Matter._meta.fields)


def test_reference_can_be_parsed_back():
    assert Matter.parse_reference("2026_7") == (2026, 7)
    assert Matter.parse_reference("mitte viide") is None


def test_human_reference_is_unique():
    factories.MatterFactory(reference_year=2026, reference_number=1)
    with pytest.raises(IntegrityError), transaction.atomic():
        factories.MatterFactory(reference_year=2026, reference_number=1)


def test_reference_year_and_number_must_be_set_together():
    with pytest.raises(IntegrityError), transaction.atomic():
        factories.MatterFactory(reference_year=2026, reference_number=None)


def test_several_archive_matters_may_have_no_reference_at_all():
    factories.ArchiveMatterFactory()
    factories.ArchiveMatterFactory()
    assert Matter.objects.filter(reference_year__isnull=True).count() == 2


# -- record modes -----------------------------------------------------------


def test_full_and_archive_matters_coexist_in_one_model(specialist):
    full = factories.MatterFactory(owner=specialist)
    archive = factories.ArchiveMatterFactory()

    assert Matter.objects.count() == 2
    assert full in Matter.objects.full_records()
    assert archive in Matter.objects.archive_records()
    assert archive.origin == MatterOrigin.LEGACY_IMPORT


def test_archive_matters_may_leave_every_modern_field_unknown():
    archive = factories.ArchiveMatterFactory()
    assert archive.received_date is None
    assert archive.response_deadline is None
    assert archive.stage is None
    assert archive.owner is None
    assert archive.track == ""


# -- source and addressee ---------------------------------------------------


def test_source_and_addressee_are_independent_facts():
    """`KELLELT` and `KELLELE` are different columns and different meanings."""
    sender = factories.OrganisationFactory(name="Näidisministeerium")
    recipient = factories.OrganisationFactory(name="Näidisamet")

    matter = factories.MatterFactory(source_organisation=sender, addressee_organisation=recipient)
    assert matter.source_organisation != matter.addressee_organisation
    assert list(sender.matters_as_source.all()) == [matter]
    assert list(recipient.matters_as_addressee.all()) == [matter]
    assert sender.matters_as_addressee.count() == 0


def test_either_direction_may_be_unknown():
    only_source = factories.MatterFactory(
        source_organisation=factories.OrganisationFactory(), addressee_organisation=None
    )
    only_addressee = factories.MatterFactory(
        source_organisation=None, addressee_organisation=factories.OrganisationFactory()
    )
    assert only_source.addressee_organisation is None
    assert only_addressee.source_organisation is None


# -- creation service -------------------------------------------------------


def test_creating_a_matter_needs_only_a_title(specialist):
    from app.matters.services import create_matter

    matter = create_matter(title="Ainult pealkiri", actor=specialist)
    assert matter.display_reference.endswith("_1")
    assert matter.owner is None
    assert matter.is_open is True


def test_creating_a_matter_records_a_change_event(specialist):
    from app.matters.services import create_matter

    matter = create_matter(title="Uus teema", actor=specialist, owner=specialist)
    event = ChangeEvent.objects.get(matter=matter)
    assert event.event_type == ChangeEventType.MATTER_CREATED
    assert event.actor == specialist


def test_a_matter_without_a_title_is_refused():
    from app.matters.services import create_matter

    with pytest.raises(DomainError):
        create_matter(title="   ")


def test_empty_title_is_refused_by_the_database_too():
    with pytest.raises(IntegrityError), transaction.atomic():
        factories.MatterFactory(title="")


# -- closure ----------------------------------------------------------------


def test_an_open_matter_cannot_carry_a_closure_reason():
    with pytest.raises(IntegrityError), transaction.atomic():
        factories.MatterFactory(is_open=True, disposition=Disposition.COMPLETED)


def test_a_closed_full_matter_needs_a_reason_and_a_timestamp():
    from django.utils import timezone

    with pytest.raises(IntegrityError), transaction.atomic():
        factories.MatterFactory(is_open=False, record_mode=RecordMode.FULL)

    matter = factories.MatterFactory(
        is_open=False,
        record_mode=RecordMode.FULL,
        disposition=Disposition.COMPLETED,
        closed_at=timezone.now(),
    )
    assert matter.is_open is False


def test_a_closed_archive_matter_is_not_forced_to_invent_a_reason():
    """Historical rows must not be given a closure reason nobody recorded."""
    archive = factories.ArchiveMatterFactory(is_open=False)
    assert archive.disposition == ""
    assert archive.closed_at is None
