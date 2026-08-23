"""Where multiple senders and source-derived activity meet.

Both changes land on the same shared population — `matter_list_queryset` — from
opposite directions. E made the sender side a many-to-many, which fans a Matter
out into one join row per sender. G hung six correlated subqueries off the same
queryset to answer *when did work last happen here*.

Each was tested against a register that did not yet have the other. The failures
worth catching here are the ones that only exist where they overlap: a Matter
appearing twice because a sender join met an annotation, an activity date
computed per sender rather than per Matter, and the register still printing an
import timestamp because the wiring was deferred while the two branches were
open.
"""

from __future__ import annotations

import datetime as dt

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from app.legacy_import.source_pages import (
    LegacySourcePage,
    MatterSourcePage,
    SourceMatchClass,
    SourceMatchMethod,
    SourcePageRole,
    SourceRelationshipKind,
    SourceSystem,
)
from app.matters.activity import ActivityBasis, activity_of
from app.matters.enums import MatterDataClass, MatterOrigin
from app.matters.models import Matter
from app.matters.selectors import matter_list_queryset
from tests import factories

pytestmark = pytest.mark.django_db

REGISTER = reverse("matters:matter_list")


def _at(year: int, month: int = 6, day: int = 15) -> dt.datetime:
    return timezone.make_aware(dt.datetime(year, month, day, 12, 0))


def _imported(**kwargs) -> Matter:
    return factories.ArchiveMatterFactory(origin=MatterOrigin.LEGACY_IMPORT, **kwargs)


def _touched_in(matter: Matter, year: int) -> Matter:
    """Force `updated_at` the way the 2026 cutover left every imported row."""
    Matter.objects.filter(pk=matter.pk).update(updated_at=_at(year, 2, 3))
    matter.refresh_from_db()
    return matter


def _page(key: str, *, created: int, modified: int | None = None) -> LegacySourcePage:
    now = timezone.now()
    return LegacySourcePage.objects.create(
        source_system=SourceSystem.ONENOTE_DESKTOP,
        source_page_id=f"1-{key}",
        page_key=key,
        source_notebook="Näidiskoja õigusloome",
        source_section="ARHIIV näidisvaldkond",
        title=f"Näidisleht {key}",
        page_role=SourcePageRole.MATTER_LIKE,
        capture_id=f"capture-{key}",
        source_created_at=_at(created),
        source_modified_at=_at(modified) if modified else None,
        first_imported_at=now,
        latest_imported_at=now,
    )


def _link(matter: Matter, page: LegacySourcePage) -> MatterSourcePage:
    return MatterSourcePage.objects.create(
        matter=matter,
        source_page=page,
        relationship_kind=SourceRelationshipKind.PRIMARY,
        match_method=SourceMatchMethod.EXCEL_EXACT_PAGE_ID,
        match_class=SourceMatchClass.EXACT,
    )


def rows_for(user) -> list[Matter]:
    return list(matter_list_queryset(user))


def titles_on(response) -> list[str]:
    return [matter.title for matter in response.context["page"].object_list]


# -- A. the list stops reporting import time as activity ---------------------


def test_the_register_row_shows_the_source_date_not_the_import_timestamp(signed_in, specialist):
    """The regression the whole activity module exists for, at the surface.

    Asserted through the rendered register rather than through `activity_of`,
    because the module was correct before this integration and the *page* was
    not — the wiring was deliberately deferred while two branches shared this
    template.
    """
    matter = _imported(title="Ajalooline teema", owner=specialist)
    _link(matter, _page("a", created=2015, modified=2018))
    _touched_in(matter, 2026)

    body = signed_in.get(REGISTER, {"olek": "koik"}).content.decode()

    assert "15.06.2018" in body
    assert "03.02.2026" not in body


def test_a_matter_with_nothing_known_renders_a_dash_not_a_timestamp(signed_in, specialist):
    """No known activity is a real answer, and the import date is not it."""
    matter = _imported(title="Vaikne arhiiviteema", owner=specialist, received_date=None)
    _touched_in(matter, 2026)

    response = signed_in.get(REGISTER, {"olek": "koik"})
    body = response.content.decode()

    assert titles_on(response) == ["Vaikne arhiiviteema"]
    assert activity_of(matter_list_queryset(specialist).get(pk=matter.pk)) is None
    assert "03.02.2026" not in body


def test_a_native_matter_still_falls_back_to_its_own_record(signed_in, specialist):
    """G's documented last resort, and only for records this system owns."""
    matter = factories.MatterFactory(title="Kohapeal loodud", owner=specialist)
    _touched_in(matter, 2026)

    fact = activity_of(matter_list_queryset(specialist).get(pk=matter.pk))

    assert fact is not None
    assert fact.basis == ActivityBasis.NATIVE_RECORD
    assert fact.occurred_on == dt.date(2026, 2, 3)


# -- B, C. senders and the annotation on one queryset ------------------------


def test_a_matter_with_several_senders_is_one_row_with_one_activity_date(signed_in, specialist):
    """The sender join must not fan the row out, or the date out with it."""
    first = factories.OrganisationFactory(name="Aamet")
    second = factories.OrganisationFactory(name="Bliit")
    matter = _imported(
        title="Kahe saatjaga arhiiviteema",
        owner=specialist,
        source_organisations=[first, second],
    )
    _link(matter, _page("b", created=2016, modified=2019))
    _touched_in(matter, 2026)

    rows = [row for row in rows_for(specialist) if row.pk == matter.pk]

    assert len(rows) == 1
    fact = activity_of(rows[0])
    assert fact is not None
    assert fact.occurred_on == dt.date(2019, 6, 15)

    response = signed_in.get(REGISTER, {"olek": "koik"})
    assert titles_on(response) == ["Kahe saatjaga arhiiviteema"]


def test_the_annotation_does_not_multiply_a_matter_by_its_senders(specialist):
    """Six correlated subqueries beside an M2M is where a fan-out hides.

    A `Max()` computed over a fanned-out row set would still return the right
    date, so this asserts the row *count* rather than the value — the duplicate
    is the defect, and it is invisible in the date.
    """
    organisations = [factories.OrganisationFactory(name=f"Asutus {index}") for index in range(4)]
    matter = _imported(title="Neli saatjat", owner=specialist, source_organisations=organisations)
    _link(matter, _page("c", created=2017))

    assert matter.source_organisations.count() == 4
    assert len(rows_for(specialist)) == 1


# -- D. filtering by sender, with the annotation attached --------------------


def test_filtering_by_one_of_several_senders_returns_the_matter_once(signed_in, specialist):
    first = factories.OrganisationFactory(name="Aamet")
    second = factories.OrganisationFactory(name="Bliit")
    matter = _imported(
        title="Filtreeritav teema", owner=specialist, source_organisations=[first, second]
    )
    _link(matter, _page("d", created=2018, modified=2020))

    for organisation in (first, second):
        response = signed_in.get(REGISTER, {"saatja": str(organisation.pk), "olek": "koik"})
        assert titles_on(response) == ["Filtreeritav teema"], organisation.name

    involved = signed_in.get(REGISTER, {"asutus": str(first.pk), "olek": "koik"})
    assert titles_on(involved) == ["Filtreeritav teema"]
    assert activity_of(matter_list_queryset(specialist).get(pk=matter.pk)).occurred_on == dt.date(
        2020, 6, 15
    )


def test_the_annotated_register_page_costs_a_bounded_number_of_queries(signed_in, specialist):
    """Six subqueries for the page, not six per row, and senders do not add any."""

    def build(count: int, offset: int = 0) -> None:
        for index in range(offset, offset + count):
            matter = _imported(title=f"Teema {index}", owner=specialist)
            matter.source_organisations.set(
                [
                    factories.OrganisationFactory(name=f"Saatja {index}a"),
                    factories.OrganisationFactory(name=f"Saatja {index}b"),
                ]
            )
            _link(matter, _page(f"q{index}", created=2015, modified=2019))

    def cost() -> int:
        with CaptureQueriesContext(connection) as captured:
            signed_in.get(REGISTER, {"olek": "koik"}).content.decode()
        return len(captured)

    build(5)
    small = cost()
    build(25, offset=5)

    assert cost() <= small


# -- E. the TEST classification is untouched by either change ----------------


def test_a_test_matter_keeps_its_class_and_stays_out_of_reporting(specialist):
    """Neither sender cardinality nor an activity date is a data-class fact."""
    from app.matters.services import create_matter

    matter = create_matter(
        title="Testandmete teema",
        actor=specialist,
        data_class=MatterDataClass.TEST,
        source_organisations=[
            factories.OrganisationFactory(name="Aamet"),
            factories.OrganisationFactory(name="Bliit"),
        ],
    )

    row = matter_list_queryset(specialist).get(pk=matter.pk)
    assert row.data_class == MatterDataClass.TEST
    assert row.source_organisations.count() == 2
    assert activity_of(row) is not None
    assert matter not in Matter.objects.real_data()


# -- F. G's PolicyArea service composes with E's services module -------------


def test_the_source_derived_policy_area_service_lives_beside_the_sender_service(specialist):
    """Both branches edited `app/matters/services.py`; both survived.

    The conflict resolution is what this pins: G's additive classification write
    and E's plural sender write are in one module, one import list and one
    transaction discipline, and calling either does not disturb the other.
    """
    from app.matters.services import (
        add_source_derived_policy_areas,
        create_matter,
        set_organisations,
    )

    area = factories.PolicyAreaFactory()
    sender = factories.OrganisationFactory(name="Aamet")
    other = factories.OrganisationFactory(name="Bliit")

    matter = create_matter(
        title="Mõlemad teenused", actor=specialist, source_organisations=[sender]
    )
    added = add_source_derived_policy_areas(
        matter=matter, policy_areas=[area], actor=specialist, provenance={"mapping": "test"}
    )

    assert added == [area]
    assert list(matter.policy_areas.all()) == [area]
    assert list(matter.source_organisations.all()) == [sender]

    # And the sender service does not disturb the classification G just added.
    set_organisations(matter=matter, source_organisations=[sender, other], actor=specialist)
    matter.refresh_from_db()
    assert list(matter.policy_areas.all()) == [area]
    assert matter.source_organisations.count() == 2

    # Additive, always: a second run adds nothing and writes nothing.
    assert add_source_derived_policy_areas(matter=matter, policy_areas=[area]) == []


def test_the_sender_field_is_still_gone_from_the_runtime_model():
    """E's one-source-of-truth rule, re-checked after G landed on top of it."""
    names = {field.name for field in Matter._meta.get_fields()}

    assert "source_organisation" not in names
    assert "source_organisations" in names
    assert not hasattr(Matter, "source_organisation")

    through = Matter._meta.get_field("source_organisations").remote_field.through
    assert through.__name__ == "MatterSourceOrganisation"
    assert through._meta.get_field("organisation").remote_field.on_delete.__name__ == "PROTECT"
