"""`Kaasamine` — recording how members and stakeholders were asked.

The record is small on purpose, so most of what is worth testing is where it
touches something that already exists: the activity date G derived, the sender
relation E made plural, the TEST classification, the search projection and the
purge planner. Those seams are where a five-field model can do damage.

The two rules this file is most careful about:

* an undated engagement must not become "today" in *Viimane tegevus*, which
  would reintroduce the import-timestamp mistake by a different door;
* adding an engagement must write no `Entry`, so one action cannot become two
  records that later disagree.
"""

from __future__ import annotations

import datetime as dt

import pytest
from django.db import IntegrityError, connection, transaction
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from app.audit.enums import ChangeEventType
from app.audit.models import ChangeEvent
from app.core.enums import Visibility
from app.core.errors import DomainError
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
from app.matters.enums import EngagementKind, MatterDataClass, MatterOrigin
from app.matters.models import Entry, Matter, MatterEngagement
from app.matters.selectors import matter_list_queryset
from app.matters.services import add_engagement, update_engagement
from app.matters.timeline import TIMELINE_EVENT_TYPES
from tests import factories

pytestmark = pytest.mark.django_db

KODA_URL = "https://www.koda.ee/kaasamine/pakendiseadus"


def _at(year: int, month: int = 6, day: int = 15) -> dt.datetime:
    from django.utils import timezone

    return timezone.make_aware(dt.datetime(year, month, day, 12, 0))


def _imported(**kwargs) -> Matter:
    return factories.ArchiveMatterFactory(origin=MatterOrigin.LEGACY_IMPORT, **kwargs)


def _page(key: str, *, created: int, modified: int | None = None) -> LegacySourcePage:
    from django.utils import timezone

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


def events_of(engagement: MatterEngagement, event_type: str) -> int:
    return ChangeEvent.objects.filter(
        matter=engagement.matter, event_type=event_type, object_id=engagement.pk
    ).count()


def fact_for(matter: Matter, user):
    return activity_of(matter_list_queryset(user).get(pk=matter.pk))


# -- the record --------------------------------------------------------------


def test_an_engagement_records_every_field_it_was_given(normal_matter, specialist):
    engagement = add_engagement(
        matter=normal_matter,
        kind=EngagementKind.WEB_CALL,
        title="Liikmete kaasamiskutse",
        url=KODA_URL,
        note="Saadeti toiduainetööstuse liikmetele.",
        occurred_on=dt.date(2026, 9, 15),
        actor=specialist,
    )

    engagement.refresh_from_db()
    assert engagement.matter == normal_matter
    assert engagement.kind == EngagementKind.WEB_CALL
    assert engagement.title == "Liikmete kaasamiskutse"
    assert engagement.url == KODA_URL
    assert engagement.note == "Saadeti toiduainetööstuse liikmetele."
    assert engagement.occurred_on == dt.date(2026, 9, 15)
    assert engagement.created_by == specialist


def test_a_campaign_with_no_durable_link_is_valid(normal_matter, specialist):
    """The commonest real record: a mailing whose platform has no share URL."""
    engagement = add_engagement(
        matter=normal_matter,
        kind=EngagementKind.EMAIL_CAMPAIGN,
        title="Liikmete teavituskiri",
        actor=specialist,
    )

    assert engagement.url == ""
    assert engagement.occurred_on is None
    assert engagement.note == ""


@pytest.mark.parametrize(
    "url",
    [
        "javascript:alert(1)",
        "data:text/html,<script>x</script>",
        "ftp://example.invalid/f",
        "file:///etc/passwd",
    ],
)
def test_a_link_that_is_not_http_is_refused(normal_matter, specialist, url):
    with pytest.raises(DomainError):
        add_engagement(
            matter=normal_matter, kind=EngagementKind.OTHER, title="Muu", url=url, actor=specialist
        )
    assert not MatterEngagement.objects.exists()


@pytest.mark.parametrize("url", ["http://example.invalid/a", "https://example.invalid/a?b=1"])
def test_http_and_https_are_accepted(normal_matter, specialist, url):
    engagement = add_engagement(
        matter=normal_matter,
        kind=EngagementKind.SURVEY,
        title="Küsitlus",
        url=url,
        actor=specialist,
    )
    assert engagement.url == url


def test_a_title_is_required_by_the_service_and_by_the_database(normal_matter, specialist):
    with pytest.raises(DomainError):
        add_engagement(
            matter=normal_matter, kind=EngagementKind.OTHER, title="   ", actor=specialist
        )

    with pytest.raises(IntegrityError), transaction.atomic():
        MatterEngagement.objects.create(matter=normal_matter, kind=EngagementKind.OTHER, title="")


def test_an_unknown_kind_is_refused(normal_matter, specialist):
    with pytest.raises(DomainError):
        add_engagement(matter=normal_matter, kind="SENDSMAILY", title="Kampaania", actor=specialist)


def test_one_matter_carries_several_engagements_newest_dated_first(normal_matter, specialist):
    add_engagement(
        matter=normal_matter,
        kind=EngagementKind.WEB_CALL,
        title="Veebikutse",
        occurred_on=dt.date(2026, 3, 1),
        actor=specialist,
    )
    add_engagement(
        matter=normal_matter,
        kind=EngagementKind.EMAIL_CAMPAIGN,
        title="Kiri",
        occurred_on=dt.date(2026, 5, 1),
        actor=specialist,
    )
    add_engagement(
        matter=normal_matter, kind=EngagementKind.SURVEY, title="Küsitlus", actor=specialist
    )

    titles = [record.title for record in normal_matter.engagements.all()]
    # Newest first, and the undated one last rather than first: nothing here
    # may read as though it happened today.
    assert titles == ["Kiri", "Veebikutse", "Küsitlus"]


# -- editing -----------------------------------------------------------------


def test_editing_changes_the_same_record_and_files_one_event(normal_matter, specialist):
    engagement = add_engagement(
        matter=normal_matter,
        kind=EngagementKind.WEB_CALL,
        title="Vale pealkiri",
        occurred_on=dt.date(2026, 1, 1),
        actor=specialist,
    )

    update_engagement(
        engagement=engagement,
        kind=EngagementKind.SURVEY,
        title="Õige pealkiri",
        url=KODA_URL,
        note="Täpsustus",
        occurred_on=dt.date(2026, 2, 2),
        actor=specialist,
    )

    engagement.refresh_from_db()
    assert engagement.kind == EngagementKind.SURVEY
    assert engagement.title == "Õige pealkiri"
    assert engagement.url == KODA_URL
    assert engagement.occurred_on == dt.date(2026, 2, 2)
    assert MatterEngagement.objects.count() == 1
    assert events_of(engagement, ChangeEventType.ENGAGEMENT_CHANGED) == 1


def test_resubmitting_the_same_values_writes_nothing(normal_matter, specialist):
    engagement = add_engagement(
        matter=normal_matter,
        kind=EngagementKind.WEB_CALL,
        title="Kaasamiskutse",
        url=KODA_URL,
        note="Märkus",
        occurred_on=dt.date(2026, 4, 4),
        actor=specialist,
    )
    engagement.refresh_from_db()
    before = engagement.updated_at

    update_engagement(
        engagement=engagement,
        kind=EngagementKind.WEB_CALL,
        title="Kaasamiskutse",
        url=KODA_URL,
        note="Märkus",
        occurred_on=dt.date(2026, 4, 4),
        actor=specialist,
    )

    engagement.refresh_from_db()
    assert engagement.updated_at == before
    assert events_of(engagement, ChangeEventType.ENGAGEMENT_CHANGED) == 0


def test_the_change_event_names_the_fields_without_copying_the_note(normal_matter, specialist):
    """A long note must not be duplicated into the audit table (brief 26)."""
    note = "Pikk selgitus. " * 40
    engagement = add_engagement(
        matter=normal_matter,
        kind=EngagementKind.WEB_CALL,
        title="Kutse",
        occurred_on=dt.date(2026, 1, 1),
        actor=specialist,
    )

    update_engagement(
        engagement=engagement, note=note, occurred_on=dt.date(2026, 6, 6), actor=specialist
    )

    event = ChangeEvent.objects.filter(
        event_type=ChangeEventType.ENGAGEMENT_CHANGED, object_id=engagement.pk
    ).get()
    assert event.payload["fields"] == ["note", "occurred_on"]
    assert event.payload["occurred_on_from"] == "2026-01-01"
    assert event.payload["occurred_on_to"] == "2026-06-06"
    assert "Pikk selgitus" not in str(event.payload)


# -- what it must not do -----------------------------------------------------


def test_adding_an_engagement_writes_exactly_one_event_and_no_entry(normal_matter, specialist):
    """One action, one record. An `Entry` here would be a second version of it."""
    engagement = add_engagement(
        matter=normal_matter, kind=EngagementKind.WEB_CALL, title="Kutse", actor=specialist
    )

    assert events_of(engagement, ChangeEventType.ENGAGEMENT_ADDED) == 1
    assert not Entry.objects.filter(matter=normal_matter).exists()
    assert not ChangeEvent.objects.filter(
        matter=normal_matter, event_type=ChangeEventType.ENTRY_ADDED
    ).exists()
    assert not ChangeEvent.objects.filter(
        matter=normal_matter, event_type=ChangeEventType.SUBMISSION_CREATED
    ).exists()


def test_engagement_events_stay_out_of_the_professional_timeline(normal_matter, specialist):
    """The section already shows the fact; the narrative is for authored work."""
    add_engagement(
        matter=normal_matter, kind=EngagementKind.WEB_CALL, title="Kutse", actor=specialist
    )

    assert ChangeEventType.ENGAGEMENT_ADDED not in TIMELINE_EVENT_TYPES
    assert ChangeEventType.ENGAGEMENT_CHANGED not in TIMELINE_EVENT_TYPES


def test_the_model_carries_no_data_class_of_its_own():
    """TEST-ness is a property of the Matter, never of a child (brief 15)."""
    names = {field.name for field in MatterEngagement._meta.get_fields()}
    assert "data_class" not in names
    assert "removed_at" not in names
    assert "status" not in names


def test_the_author_cannot_be_deleted_out_of_the_record(normal_matter, specialist):
    from django.db.models import ProtectedError

    add_engagement(
        matter=normal_matter, kind=EngagementKind.WEB_CALL, title="Kutse", actor=specialist
    )
    with pytest.raises(ProtectedError):
        specialist.delete()


def test_the_engagement_is_owned_by_its_matter_and_not_by_the_organisation_table():
    """CASCADE from the Matter, PROTECT on the person who recorded it.

    Asserted on the schema rather than by deleting a Matter, because a Matter
    that has been written to cannot be deleted at all: `ChangeEvent.matter` is
    PROTECT and the audit trail is append-only. That is the product's rule, not
    something this feature may work around.
    """
    matter_fk = MatterEngagement._meta.get_field("matter")
    author_fk = MatterEngagement._meta.get_field("created_by")

    assert matter_fk.remote_field.on_delete.__name__ == "CASCADE"
    assert author_fk.remote_field.on_delete.__name__ == "PROTECT"
    # Nothing here points at reference data, which is what keeps the purge
    # planner from ever reaching an Organisation or a PolicyArea through it.
    related = {
        field.related_model._meta.label
        for field in MatterEngagement._meta.get_fields()
        if field.is_relation and getattr(field, "concrete", False) and field.related_model
    }
    assert related == {"matters.Matter", "accounts.User"}


# -- activity ----------------------------------------------------------------


def test_a_dated_engagement_becomes_the_matters_last_activity(specialist):
    matter = _imported(owner=specialist, received_date=None)
    _link(matter, _page("a", created=2019, modified=2020))
    add_engagement(
        matter=matter,
        kind=EngagementKind.WEB_CALL,
        title="Kaasamiskutse",
        occurred_on=dt.date(2021, 4, 4),
        actor=specialist,
    )

    fact = fact_for(matter, specialist)
    assert fact is not None
    assert fact.occurred_on == dt.date(2021, 4, 4)
    assert fact.basis == ActivityBasis.ENGAGEMENT


def test_an_undated_engagement_does_not_move_the_last_activity(specialist):
    """Somebody entering a 2019 consultation today must not stamp it today.

    This is the import-timestamp mistake arriving by a different door, and it is
    the single most important assertion in this file (brief 29).
    """
    matter = _imported(owner=specialist, received_date=None)
    _link(matter, _page("b", created=2019, modified=2020))
    add_engagement(
        matter=matter, kind=EngagementKind.EMAIL_CAMPAIGN, title="Kiri", actor=specialist
    )

    fact = fact_for(matter, specialist)
    assert fact is not None
    assert fact.occurred_on == dt.date(2020, 6, 15)
    assert fact.basis == ActivityBasis.ONENOTE_MODIFIED


def test_a_later_entry_still_beats_an_earlier_engagement(specialist):
    """Latest fact wins. Engagement gets no priority of its own (brief 30)."""
    from app.matters.services import add_entry

    matter = _imported(owner=specialist, received_date=None)
    add_engagement(
        matter=matter,
        kind=EngagementKind.SURVEY,
        title="Küsitlus",
        occurred_on=dt.date(2023, 5, 5),
        actor=specialist,
    )
    add_entry(matter=matter, body="<p>Kohtumine.</p>", author=specialist, occurred_at=_at(2024))

    fact = fact_for(matter, specialist)
    assert fact.occurred_on == dt.date(2024, 6, 15)
    assert fact.basis == ActivityBasis.ENTRY


def test_the_activity_annotation_costs_no_query_per_engagement(signed_in, specialist):
    for index in range(15):
        matter = _imported(title=f"Teema {index}", owner=specialist)
        for step in range(3):
            add_engagement(
                matter=matter,
                kind=EngagementKind.WEB_CALL,
                title=f"Kutse {index}-{step}",
                occurred_on=dt.date(2024, 1, 1 + step),
                actor=specialist,
            )

    rows = list(matter_list_queryset(specialist))
    with CaptureQueriesContext(connection) as captured:
        facts = [activity_of(row) for row in rows]

    assert len(facts) == 15
    assert all(fact is not None for fact in facts)
    assert len(captured) == 0


# -- the seam with multiple senders -----------------------------------------


def test_a_matter_with_two_senders_and_two_engagements_is_one_row(signed_in, specialist):
    first = factories.OrganisationFactory(name="Aamet")
    second = factories.OrganisationFactory(name="Bliit")
    matter = _imported(
        title="Kahe saatjaga kaasamine", owner=specialist, source_organisations=[first, second]
    )
    add_engagement(
        matter=matter,
        kind=EngagementKind.WEB_CALL,
        title="Kutse",
        occurred_on=dt.date(2026, 2, 2),
        actor=specialist,
    )
    add_engagement(
        matter=matter,
        kind=EngagementKind.SURVEY,
        title="Küsitlus",
        occurred_on=dt.date(2026, 3, 3),
        actor=specialist,
    )

    rows = [row for row in matter_list_queryset(specialist) if row.pk == matter.pk]
    assert len(rows) == 1
    assert activity_of(rows[0]).occurred_on == dt.date(2026, 3, 3)

    body = signed_in.get(
        reverse("matters:matter_detail", kwargs={"pk": matter.pk})
    ).content.decode()
    assert body.count("Kutse</span>") + body.count("Kutse</a>") == 1
    assert "Aamet" in body and "Bliit" in body


# -- the page ----------------------------------------------------------------


def test_the_section_renders_every_engagement_once(signed_in, specialist):
    matter = factories.MatterFactory(owner=specialist)
    add_engagement(
        matter=matter,
        kind=EngagementKind.WEB_CALL,
        title="Ainulaadne kaasamiskutse",
        url=KODA_URL,
        note="Vastuseid ootame 15. septembrini.",
        occurred_on=dt.date(2026, 9, 1),
        actor=specialist,
    )

    body = signed_in.get(
        reverse("matters:matter_detail", kwargs={"pk": matter.pk})
    ).content.decode()

    assert "Kaasamine" in body
    # One row. The title also appears in the edit form's prefilled input, which
    # is correct, so the row count is what this asserts rather than a substring.
    assert body.count('class="factrow"') == 1
    assert "Ainulaadne kaasamiskutse" in body
    assert "Vastuseid ootame" in body
    assert 'rel="noopener noreferrer"' in body
    # The host, not the tracking URL, is what the row prints beside the title.
    assert "www.koda.ee" in body


def test_an_undated_engagement_says_so_rather_than_showing_a_blank(signed_in, specialist):
    matter = factories.MatterFactory(owner=specialist)
    add_engagement(
        matter=matter, kind=EngagementKind.EMAIL_CAMPAIGN, title="Teavituskiri", actor=specialist
    )

    body = signed_in.get(
        reverse("matters:matter_detail", kwargs={"pk": matter.pk})
    ).content.decode()
    assert "Kuupäev teadmata" in body


def test_the_add_form_does_not_prefill_todays_date(signed_in, specialist):
    """The record may be about a consultation from years ago (brief 38)."""
    from django.utils import timezone

    matter = factories.MatterFactory(owner=specialist)
    body = signed_in.get(
        reverse("matters:matter_detail", kwargs={"pk": matter.pk})
    ).content.decode()

    assert 'name="occurred_on"' in body
    assert timezone.localdate().isoformat() not in body


def test_a_matter_page_costs_no_query_per_engagement(signed_in, specialist):
    matter = factories.MatterFactory(owner=specialist)
    url = reverse("matters:matter_detail", kwargs={"pk": matter.pk})

    def cost() -> int:
        with CaptureQueriesContext(connection) as captured:
            signed_in.get(url).content.decode()
        return len(captured)

    add_engagement(matter=matter, kind=EngagementKind.WEB_CALL, title="Üks", actor=specialist)
    small = cost()
    for index in range(10):
        add_engagement(
            matter=matter, kind=EngagementKind.SURVEY, title=f"Küsitlus {index}", actor=specialist
        )

    assert cost() <= small


# -- writing through the page ------------------------------------------------


def _post_add(client, matter, **data):
    """Post the add form, using a kind the *form* offers.

    `WEB_CALL` is still a valid stored value and every historical row carrying
    it still reads correctly — but the creation control offers three options
    now, and a form that accepted a fourth would be a form that does not mean
    what it shows (Teema redesign §14).
    """
    payload = {"kind": EngagementKind.SURVEY, "title": "Kaasamiskutse", **data}
    return client.post(reverse("matters:add_engagement", kwargs={"pk": matter.pk}), payload)


def test_the_add_form_refuses_a_kind_it_does_not_offer(signed_in, specialist):
    """The three approved options are the vocabulary, not a suggestion."""
    matter = factories.MatterFactory(owner=specialist)

    response = _post_add(signed_in, matter, kind=EngagementKind.WEB_CALL)

    assert response.status_code == 400
    assert not MatterEngagement.objects.filter(matter=matter).exists()


def test_a_legacy_kind_stays_creatable_through_the_service(specialist):
    """An importer, a migration or a correction is not the creation form."""
    matter = factories.MatterFactory(owner=specialist)

    record = add_engagement(
        matter=matter, kind=EngagementKind.WEB_CALL, title="Vana kutse", actor=specialist
    )

    assert record.kind == EngagementKind.WEB_CALL


def test_adding_through_the_page_saves_the_record(signed_in, specialist):
    matter = factories.MatterFactory(owner=specialist)

    response = _post_add(signed_in, matter, url=KODA_URL, occurred_on="2026-09-15", note="Märkus")

    assert response.status_code == 200
    engagement = MatterEngagement.objects.get(matter=matter)
    assert engagement.title == "Kaasamiskutse"
    assert engagement.occurred_on == dt.date(2026, 9, 15)


def test_the_page_refuses_a_javascript_link_and_keeps_what_was_typed(signed_in, specialist):
    matter = factories.MatterFactory(owner=specialist)

    response = _post_add(signed_in, matter, url="javascript:alert(1)")

    assert response.status_code == 400
    assert not MatterEngagement.objects.exists()
    assert "http" in response.content.decode()


def test_editing_through_the_page_updates_the_record(signed_in, specialist):
    matter = factories.MatterFactory(owner=specialist)
    engagement = add_engagement(
        matter=matter, kind=EngagementKind.WEB_CALL, title="Enne", actor=specialist
    )

    response = signed_in.post(
        reverse(
            "matters:update_engagement",
            kwargs={"pk": matter.pk, "engagement_id": engagement.pk},
        ),
        {"kind": EngagementKind.SURVEY, "title": "Pärast", "url": "", "note": ""},
    )

    assert response.status_code == 200
    engagement.refresh_from_db()
    assert engagement.title == "Pärast"
    assert engagement.kind == EngagementKind.SURVEY


# -- authorization -----------------------------------------------------------


def test_a_reader_cannot_add_or_edit(client, specialist):
    reader = factories.ReaderFactory()
    matter = factories.MatterFactory(owner=specialist)
    engagement = add_engagement(
        matter=matter, kind=EngagementKind.WEB_CALL, title="Kutse", actor=specialist
    )
    client.force_login(reader)

    assert _post_add(client, matter).status_code == 404
    assert (
        client.post(
            reverse(
                "matters:update_engagement",
                kwargs={"pk": matter.pk, "engagement_id": engagement.pk},
            ),
            {"kind": EngagementKind.SURVEY, "title": "Muudetud"},
        ).status_code
        == 404
    )
    engagement.refresh_from_db()
    assert engagement.title == "Kutse"


def test_the_write_control_follows_the_central_policy(client, specialist):
    """Exercised through the authorization API, not by naming roles here."""
    from app.core.authorization import may_write_business_content

    reader = factories.ReaderFactory()
    assert may_write_business_content(specialist) is True
    assert may_write_business_content(reader) is False


def test_an_engagement_on_a_restricted_matter_does_not_leak(client, specialist, other_specialist):
    restricted = factories.MatterFactory(
        owner=other_specialist, visibility=Visibility.RESTRICTED, title="Piiratud teema"
    )
    engagement = add_engagement(
        matter=restricted,
        kind=EngagementKind.WEB_CALL,
        title="Salajane kaasamiskutse",
        actor=other_specialist,
    )

    assert MatterEngagement.objects.visible_to(other_specialist).filter(pk=engagement.pk).exists()
    assert not MatterEngagement.objects.visible_to(specialist).filter(pk=engagement.pk).exists()

    client.force_login(specialist)
    detail = client.get(reverse("matters:matter_detail", kwargs={"pk": restricted.pk}))
    assert detail.status_code == 404
    assert (
        client.post(
            reverse(
                "matters:update_engagement",
                kwargs={"pk": restricted.pk, "engagement_id": engagement.pk},
            ),
            {"kind": EngagementKind.SURVEY, "title": "Muudetud"},
        ).status_code
        == 404
    )


# -- search ------------------------------------------------------------------


def test_a_matter_is_found_through_its_engagement_text(specialist):
    from app.search.indexing import indexable_matters, refresh_matters
    from app.search.services import search_matters

    matter = factories.MatterFactory(title="Pakendiseaduse teema", owner=specialist)
    add_engagement(
        matter=matter,
        kind=EngagementKind.SURVEY,
        title="Ainulaadne pakendiküsitlus",
        url="https://survey.alchemer.example/s3/123456/pakend?utm=x",
        note="Vastuseid ootame septembrini.",
        actor=specialist,
    )
    add_engagement(
        matter=matter, kind=EngagementKind.WEB_CALL, title="Teine kaasamiskutse", actor=specialist
    )
    refresh_matters(indexable_matters().filter(pk=matter.pk))

    # Including the vendor's name, which lives only in the link's host — and
    # which a single `host` token would have made unfindable.
    for term in ("Ainulaadne", "pakendiküsitlus", "alchemer"):
        hits = [hit.matter.pk for hit in search_matters(query=term, user=specialist)]
        assert hits.count(matter.pk) == 1, term


def test_the_projection_does_not_depend_on_relation_order(specialist):
    from app.search.indexing import indexable_matters, indexed_text_for

    matter = factories.MatterFactory(owner=specialist)
    add_engagement(matter=matter, kind=EngagementKind.SURVEY, title="Zeta", actor=specialist)
    add_engagement(matter=matter, kind=EngagementKind.WEB_CALL, title="Alfa", actor=specialist)

    first = indexed_text_for(indexable_matters().get(pk=matter.pk))
    second = indexed_text_for(indexable_matters().get(pk=matter.pk))

    assert first == second
    assert "Alfa" in first["body_text"] and "Zeta" in first["body_text"]
    assert first["body_text"].index("Alfa") < first["body_text"].index("Zeta")


# -- TEST data and the purge planner ----------------------------------------


def test_an_engagement_under_a_test_matter_stays_out_of_reporting(specialist):
    matter = factories.MatterFactory(owner=specialist, data_class=MatterDataClass.TEST)
    add_engagement(matter=matter, kind=EngagementKind.WEB_CALL, title="Testkutse", actor=specialist)

    assert matter.engagements.count() == 1
    assert matter not in Matter.objects.real_data()
    # Operationally visible, exactly like every other child of a TEST Matter.
    assert MatterEngagement.objects.visible_to(specialist).count() == 1


def test_the_purge_planner_treats_an_engagement_as_matter_owned(specialist):
    from app.matters.purge import build_purge_plan

    matter = factories.MatterFactory(owner=specialist, data_class=MatterDataClass.TEST)
    add_engagement(matter=matter, kind=EngagementKind.WEB_CALL, title="Testkutse", actor=specialist)

    plan = build_purge_plan([])

    assert plan.count_of(MatterEngagement._meta.label) == 1
    assert plan.evidence == ()
    labels = {group.label for group in plan.owned}
    assert "organisations.Organisation" not in labels
    assert "taxonomy.PolicyArea" not in labels


def test_a_real_matters_engagement_is_not_in_a_test_purge_plan(specialist):
    from app.matters.purge import build_purge_plan

    real = factories.MatterFactory(owner=specialist)
    add_engagement(matter=real, kind=EngagementKind.WEB_CALL, title="Päris", actor=specialist)

    plan = build_purge_plan([])
    assert plan.count_of(MatterEngagement._meta.label) == 0


# -- the section's interaction state -----------------------------------------
#
# Two clicks to add the first `Kaasamine`: one to open the section, one to open
# a `+ Lisa kaasamine` disclosure standing alone in an otherwise empty body. The
# middle state showed nothing the header had not already said, and the click
# that reached it was already an expression of the intent to add something.
#
# What the server owes the page is a shape, not a script: with no records the
# add form is rendered in the section body directly, so opening the section
# *is* opening the form, with JavaScript on or off (Kaasamine one-click §3–§5).


def _rendered(client, matter) -> str:
    return client.get(reverse("matters:matter_detail", kwargs={"pk": matter.pk})).content.decode()


def _opening_tag(body: str, marker: str) -> str:
    """The one opening tag carrying `marker`, from its `<` to its own `>`."""
    at = body.index(marker)
    return body[body.rindex("<", 0, at) : body.index(">", at) + 1]


def _is_open(body: str, marker: str) -> bool:
    """Whether the `<details>` carrying `marker` renders with `open`."""
    import re as _re

    return _re.search(r"\bopen\b", _opening_tag(body, marker)) is not None


SECTION = 'id="kaasamine"'
COMPOSER = "data-engagement-composer"
ADD_FORM = "data-engagement-add\n"


def test_with_nothing_recorded_the_section_is_closed_and_the_form_is_its_body(
    signed_in, specialist
):
    """One click, and the thing that opens is the form (§28.1, §28.2, §28.3)."""
    matter = factories.MatterFactory(owner=specialist)

    body = _rendered(signed_in, matter)

    # Closed on arrival: the empty state is not a section that opens itself.
    assert not _is_open(body, SECTION)
    assert 'data-engagement-count="0"' in body
    # And nothing between the section and the form. The disclosure is absent
    # from the DOM rather than present-and-open, because a control whose only
    # state is "open" is a control that is only ever clicked to no effect.
    assert COMPOSER not in body
    assert "+ Lisa kaasamine" not in body
    assert ADD_FORM in body


def test_with_a_record_the_section_is_closed_and_so_is_the_composer(signed_in, specialist):
    """Opening it shows the records; the form waits to be asked for (§28.4, §28.5)."""
    matter = factories.MatterFactory(owner=specialist)
    add_engagement(
        matter=matter,
        kind=EngagementKind.SURVEY,
        title="Liikmete küsitlus",
        occurred_on=dt.date(2026, 7, 1),
        actor=specialist,
    )

    body = _rendered(signed_in, matter)

    assert not _is_open(body, SECTION)
    assert 'data-engagement-count="1"' in body
    assert "Liikmete küsitlus" in body
    # The composer is here and shut, and it is what the explicit add action
    # opens — the records are what the reader opened the section for.
    assert COMPOSER in body
    assert not _is_open(body, COMPOSER)
    assert "+ Lisa kaasamine" in body


def test_the_add_action_is_a_button_the_keyboard_reaches(signed_in, specialist):
    """A span inside the summary is only the disclosure's toggle (§13, §15)."""
    matter = factories.MatterFactory(owner=specialist)

    body = _rendered(signed_in, matter)

    tag = _opening_tag(body, "data-engagement-add-trigger")
    assert tag.startswith("<button")
    assert 'type="button"' in tag


def test_a_refused_add_leaves_the_section_and_the_form_open(signed_in, specialist):
    """The reason for a refusal must not be behind a disclosure (§28.9)."""
    matter = factories.MatterFactory(owner=specialist)
    add_engagement(matter=matter, kind=EngagementKind.SURVEY, title="Olemasolev", actor=specialist)

    response = _post_add(signed_in, matter, title="")
    body = response.content.decode()

    assert response.status_code == 400
    assert _is_open(body, SECTION)
    assert _is_open(body, COMPOSER)


def test_a_refused_add_keeps_what_was_typed(signed_in, specialist):
    """`_overview_with_engagement_error` re-renders "so nothing typed is lost".

    The hand-rendered add form read its values from `record`, which is `None`
    when adding, so a refused save came back with the title box empty and the
    date box empty — every field the person had filled in wiped by the
    explanation of why it was refused (Kaasamine one-click §7).
    """
    matter = factories.MatterFactory(owner=specialist)

    response = _post_add(
        signed_in,
        matter,
        title="Liikmete kaasamiskutse",
        url="javascript:alert(1)",
        note="Vastuseid ootame septembrini.",
        occurred_on="15.9.2026",
    )
    body = response.content.decode()

    assert response.status_code == 400
    assert 'value="Liikmete kaasamiskutse"' in body
    assert "Vastuseid ootame septembrini." in body
    assert 'value="15.9.2026"' in body


def test_an_unreadable_date_says_so_where_it_was_typed(signed_in, specialist):
    """The one field the browser will not police on its way out (§7)."""
    matter = factories.MatterFactory(owner=specialist)

    response = _post_add(signed_in, matter, occurred_on="32.13.2026")
    body = response.content.decode()

    assert response.status_code == 400
    assert not MatterEngagement.objects.filter(matter=matter).exists()
    assert 'class="field__error"' in body
    assert 'value="32.13.2026"' in body


def test_a_refused_edit_opens_its_own_row_and_not_the_composer(signed_in, specialist):
    """A refusal belongs where it came from, not beside a second empty answer."""
    matter = factories.MatterFactory(owner=specialist)
    engagement = add_engagement(
        matter=matter, kind=EngagementKind.SURVEY, title="Enne", actor=specialist
    )

    response = signed_in.post(
        reverse(
            "matters:update_engagement", kwargs={"pk": matter.pk, "engagement_id": engagement.pk}
        ),
        {"kind": EngagementKind.SURVEY, "title": ""},
    )
    body = response.content.decode()

    assert response.status_code == 400
    assert _is_open(body, SECTION)
    assert not _is_open(body, COMPOSER)


def test_a_saved_engagement_leaves_the_section_open_and_the_composer_shut(signed_in, specialist):
    """The reader must see the record they just made (§28.8)."""
    matter = factories.MatterFactory(owner=specialist)

    response = _post_add(signed_in, matter, title="Uus kaasamiskutse")
    body = response.content.decode()

    assert response.status_code == 200
    assert _is_open(body, SECTION)
    assert "Uus kaasamiskutse" in body
    # One record now, so the composer exists again — and it is shut, because the
    # emptied form is not what the save was for.
    assert COMPOSER in body
    assert not _is_open(body, COMPOSER)


def test_a_reader_who_cannot_write_gets_no_form_in_either_state(client, specialist):
    """The empty state renders a form, not a form for everybody."""
    reader = factories.ReaderFactory()
    matter = factories.MatterFactory(owner=specialist)
    client.force_login(reader)

    body = _rendered(client, matter)

    assert "Kaasamist ei ole kirja pandud" in body
    assert ADD_FORM not in body
    assert "data-engagement-add-trigger" not in body
