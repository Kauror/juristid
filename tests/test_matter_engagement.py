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
from app.matters.forms import ComposerForm
from app.matters.models import Entry, Matter, MatterEngagement
from app.matters.selectors import matter_list_queryset
from app.matters.services import add_engagement, update_engagement
from app.matters.timeline import TIMELINE_EVENT_TYPES, matter_timeline
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


def test_an_engagement_reads_as_a_chronology_milestone(signed_in, specialist):
    """**The standalone section is gone.**

    The approved Teema target removed the block that stood open on every Matter
    to say «none yet» and, when it held something, listed dated facts the
    chronology now carries. An engagement is an event with a date, so it belongs
    in the chronology like every other event (TEEMA_TARGET_SPEC §F,
    docs/adr/0074 §9).
    """
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

    assert 'id="kaasamine"' not in body
    assert "+ Lisa kaasamine" not in body

    # One row, stated once: the audit event contributes no clause beside it.
    chronology = body[body.index('id="ajalugu-loend"') :]
    assert chronology.count("Kaasamine: Ainulaadne kaasamiskutse") == 1
    assert "lisas kaasamise" not in chronology
    # Its kind reads under it, and its stored `WEB_CALL` is one the composer no
    # longer offers — a value the write surface stopped offering is not a value
    # the page stopped rendering (docs/adr/0074 §9).
    assert "Kaasamiskutse veebis" in chronology


def test_an_undated_engagement_is_readable_without_a_manufactured_day(signed_in, specialist):
    """It falls back to when it was recorded rather than inventing a date, and
    it does **not** reach the process strip: an undated record is a real fact
    about the file and not a position in a process (docs/adr/0074 §12)."""
    matter = factories.MatterFactory(owner=specialist)
    add_engagement(
        matter=matter, kind=EngagementKind.EMAIL_CAMPAIGN, title="Teavituskiri", actor=specialist
    )

    body = signed_in.get(
        reverse("matters:matter_detail", kwargs={"pk": matter.pk})
    ).content.decode()

    assert "Kaasamine: Teavituskiri" in body
    strip = body.split("tl-strip")[1].split("</div>")[0] if "tl-strip" in body else ""
    assert "Teavituskiri" not in strip


def test_the_composer_panel_asks_no_date_at_all(signed_in, specialist):
    """The target's `+ Kaasamine` asks `Liik`, `Keda kaasati` and `Vastuseid`.

    An engagement recorded from the composer is work being written down now, and
    takes today in Europe/Tallinn — the same clock `add_entry` stamps with. The
    service still takes `occurred_on`, and the standalone route still asks for
    it, so an old consultation can still be recorded with its real date
    (docs/adr/0074 §9).
    """
    matter = factories.MatterFactory(owner=specialist)
    body = signed_in.get(
        reverse("matters:matter_detail", kwargs={"pk": matter.pk})
    ).content.decode()
    panel = body[body.index('id="lisa-kaasamine"') : body.index('id="lisa-tahtaeg"')]

    assert 'name="kind"' in panel
    assert 'name="audience"' in panel
    assert 'name="response_count"' in panel
    assert 'name="occurred_on"' not in panel


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


def test_the_route_refuses_a_javascript_link(signed_in, specialist):
    """The refusal is the service's and is unchanged. What went with the
    standalone section is the surface that redisplayed the typed value — the
    approved target's composer panel does not ask for a link at all
    (docs/adr/0074 §9)."""
    matter = factories.MatterFactory(owner=specialist)

    response = _post_add(signed_in, matter, url="javascript:alert(1)")

    assert response.status_code == 400
    assert not MatterEngagement.objects.exists()


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


def test_an_engagement_on_a_restricted_matter_does_not_leak(client, specialist, reader):
    restricted = factories.MatterFactory(
        owner=specialist, visibility=Visibility.RESTRICTED, title="Piiratud teema"
    )
    engagement = add_engagement(
        matter=restricted,
        kind=EngagementKind.WEB_CALL,
        title="Salajane kaasamiskutse",
        actor=specialist,
    )

    assert MatterEngagement.objects.visible_to(specialist).filter(pk=engagement.pk).exists()
    assert not MatterEngagement.objects.visible_to(reader).filter(pk=engagement.pk).exists()

    client.force_login(reader)
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
    """Still findable — through the engagement's own search row.

    The text used to be concatenated into the Matter's row. That made a
    RESTRICTED `Kaasamine` searchable by anybody who could open a NORMAL parent,
    because a MATTER row is authorized by the Matter alone, so AUTH-003 moved it
    to a row whose visibility can express the child's own. What a reader can
    *find* is unchanged; which reader can find it is now correct.
    """
    from app.search.indexing import rebuild_all
    from app.search.models import SearchSourceKind
    from app.search.services import search_documents

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
    rebuild_all()

    # Including the vendor's name, which lives only in the link's host — and
    # which PostgreSQL would otherwise read as one unsearchable token.
    for term in ("pakendiküsitlus", "septembrini", "alchemer"):
        rows = list(search_documents(query=term, user=specialist))
        assert rows, f"nothing found for {term!r}"
        assert {row.matter_id for row in rows} == {matter.pk}
        assert SearchSourceKind.ENGAGEMENT in {row.source_kind for row in rows}


def test_the_matter_row_no_longer_carries_engagement_text(specialist):
    """The property that replaced the old ordering test.

    That test asserted `_engagement_text_for` sorted its parts so two rebuilds
    of an unchanged Matter produced identical text. The function is gone: each
    `Kaasamine` is one row now, so there is no concatenation whose order could
    vary — and the invariant worth pinning instead is that the Matter row
    carries none of it (AUTH-003).
    """
    from app.search.indexing import indexable_matters, indexed_text_for, rebuild_all
    from app.search.models import SearchDocument, SearchSourceKind

    matter = factories.MatterFactory(owner=specialist)
    add_engagement(matter=matter, kind=EngagementKind.SURVEY, title="Zeta", actor=specialist)
    add_engagement(matter=matter, kind=EngagementKind.WEB_CALL, title="Alfa", actor=specialist)

    first = indexed_text_for(indexable_matters().get(pk=matter.pk))
    second = indexed_text_for(indexable_matters().get(pk=matter.pk))

    assert first == second
    assert "Alfa" not in first["body_text"]
    assert "Zeta" not in first["body_text"]

    rebuild_all()
    engagement_rows = SearchDocument.objects.filter(source_kind=SearchSourceKind.ENGAGEMENT)
    assert {row.title for row in engagement_rows} == {"Alfa", "Zeta"}
    assert all(row.engagement_id is not None for row in engagement_rows)


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
PANEL = 'id="lisa-kaasamine"'


# The approved Teema target removed the standalone `Kaasamine` section, and with
# it every question about whether that section is open, whether its composer is
# shut, and what its refusals look like. Recording a consultation is the composer
# panel `+ Kaasamine`, and the browser tests for it are in
# `e2e/test_engagement.py` (docs/adr/0074 §9).
#
# What those tests protected that is not about a disclosure — a refusal that is
# never hidden, a save the reader can see, and no write control for somebody who
# may not write — is asserted below on the surfaces that have it.


def test_the_page_offers_one_way_in_and_it_is_lisa_teemale(signed_in, specialist):
    """One entry point. ADR 0031 required that and chose the section; the
    section is gone, so `+ Kaasamine` is it rather than a second one."""
    matter = factories.MatterFactory(owner=specialist)

    body = _rendered(signed_in, matter)

    assert SECTION not in body
    assert PANEL in body
    assert "+ Kaasamine" in body
    assert "+ Lisa kaasamine" not in body


def test_a_refused_engagement_comes_back_in_an_open_panel(signed_in, specialist):
    """A refusal inside a panel nobody can see is a refusal nobody reads."""
    matter = factories.MatterFactory(owner=specialist)

    response = signed_in.post(
        reverse("matters:add_engagement_compact", kwargs={"pk": matter.pk}),
        {"kind": "SURVEY", "audience": "", "response_count": "4"},
        headers={"HX-Request": "true"},
    )
    body = response.content.decode()

    assert response.status_code == 400
    assert not MatterEngagement.objects.exists()
    assert "Kirjuta, keda kaasati" in body
    assert _is_open(body, PANEL)
    # And what was typed came back with it.
    assert 'value="4"' in body


def test_a_reader_reads_the_records_and_gets_no_way_to_write_one(client, specialist):
    """What the section was for survives having no controls: the record reads,
    in the chronology, and the composer that would create one is not rendered
    at all for somebody who may not write business content."""
    reader = factories.ReaderFactory()
    matter = factories.MatterFactory(owner=specialist)
    add_engagement(
        matter=matter, kind=EngagementKind.SURVEY, title="Liikmete küsitlus", actor=specialist
    )
    client.force_login(reader)

    body = _rendered(client, matter)

    assert "Kaasamine: Liikmete küsitlus" in body
    assert PANEL not in body
    assert SECTION not in body
    assert "+ Lisa kaasamine" not in body


# -- the provider pointers (2026-09-12) --------------------------------------
#
# `url` was one address for an engagement that routinely has two — the mailing
# that asked and the questionnaire that collected — so somebody had to drop one
# of them or keep it where nobody can click it. These are pointers and nothing
# else: no provider is contacted, no campaign is created, no response is read
# back (docs/adr/0027, amended).

SMAILY_URL = "https://sendsmaily.net/api/campaigns/9182"
ALCHEMER_URL = "https://survey.alchemer.eu/s3/7710021/pakendiseadus"


def test_an_engagement_with_neither_provider_link_is_exactly_what_it_was(normal_matter, specialist):
    """**A.** The columns are additive and nothing is required to fill them."""
    engagement = add_engagement(
        matter=normal_matter,
        kind=EngagementKind.EMAIL_CAMPAIGN,
        title="Liikmete teavituskiri",
        actor=specialist,
    )

    engagement.refresh_from_db()
    assert engagement.smaily_url == ""
    assert engagement.alchemer_url == ""
    assert engagement.url == ""


@pytest.mark.parametrize("field", ["smaily_url", "alchemer_url"])
def test_one_provider_link_on_its_own_persists_and_reads_back(normal_matter, specialist, field):
    """**B, C.** Either alone, and the other stays empty."""
    other = "alchemer_url" if field == "smaily_url" else "smaily_url"
    value = SMAILY_URL if field == "smaily_url" else ALCHEMER_URL

    engagement = add_engagement(
        matter=normal_matter,
        kind=EngagementKind.SURVEY,
        title="Liikmete küsitlus",
        actor=specialist,
        **{field: value},
    )

    engagement.refresh_from_db()
    assert getattr(engagement, field) == value
    assert getattr(engagement, other) == ""


def test_both_provider_links_are_stored_independently(normal_matter, specialist):
    """**D, H.** Three addresses, three columns, none overwriting another.

    This is the whole reason the two columns exist: one round, a mailing *and* a
    questionnaire, and the generic `url` still meaning what it always meant.
    """
    engagement = add_engagement(
        matter=normal_matter,
        kind=EngagementKind.EMAIL_CAMPAIGN,
        title="Liikmed",
        url=KODA_URL,
        smaily_url=SMAILY_URL,
        alchemer_url=ALCHEMER_URL,
        actor=specialist,
    )

    engagement.refresh_from_db()
    assert (engagement.url, engagement.smaily_url, engagement.alchemer_url) == (
        KODA_URL,
        SMAILY_URL,
        ALCHEMER_URL,
    )


@pytest.mark.parametrize("field", ["smaily_url", "alchemer_url"])
@pytest.mark.parametrize("url", ["javascript:alert(1)", "ftp://example.invalid/f", "kampaania"])
def test_a_provider_link_goes_through_the_same_allow_list(normal_matter, specialist, field, url):
    """**E.** One rule for every address on this record, and no row survives it."""
    with pytest.raises(DomainError):
        add_engagement(
            matter=normal_matter,
            kind=EngagementKind.OTHER,
            title="Muu",
            actor=specialist,
            **{field: url},
        )
    assert not MatterEngagement.objects.exists()


def test_a_provider_link_does_not_make_an_engagement_valid_on_its_own(normal_matter, specialist):
    """**F.** `Keda kaasati` is what identifies the record, links or no links."""
    with pytest.raises(DomainError):
        add_engagement(
            matter=normal_matter,
            kind=EngagementKind.SURVEY,
            title="   ",
            smaily_url=SMAILY_URL,
            actor=specialist,
        )
    assert not MatterEngagement.objects.exists()


def test_an_engagement_recorded_before_the_columns_existed_still_reads(normal_matter, specialist):
    """**G.** No backfill, so every historical row answers `''` — which renders
    as the row it always was rather than as an empty link."""
    add_engagement(
        matter=normal_matter,
        kind=EngagementKind.SURVEY,
        title="Vana küsitlus",
        url=KODA_URL,
        occurred_on=dt.date(2023, 4, 1),
        actor=specialist,
    )

    rows = [
        item
        for item in matter_timeline(matter=normal_matter, user=specialist)[0]
        if item.is_milestone and item.milestone.what.startswith("Kaasamine:")
    ]
    assert len(rows) == 1
    assert rows[0].milestone.links == ()


def test_a_correction_round_trips_the_provider_links(normal_matter, specialist):
    """**I.** The edit path carries them, and says which fields moved."""
    engagement = add_engagement(
        matter=normal_matter, kind=EngagementKind.SURVEY, title="Liikmed", actor=specialist
    )

    update_engagement(
        engagement=engagement,
        smaily_url=SMAILY_URL,
        alchemer_url=ALCHEMER_URL,
        actor=specialist,
    )

    engagement.refresh_from_db()
    assert engagement.smaily_url == SMAILY_URL
    assert engagement.alchemer_url == ALCHEMER_URL
    event = ChangeEvent.objects.filter(event_type=ChangeEventType.ENGAGEMENT_CHANGED).latest(
        "created_at"
    )
    # The field names, and not the addresses. Same rule the generic `url` has
    # had since 0027: the audit says a link changed, the record says to what.
    assert event.payload["fields"] == ["alchemer_url", "smaily_url"]
    assert SMAILY_URL not in str(event.payload)


def test_an_update_that_names_no_provider_link_leaves_both_alone(normal_matter, specialist):
    """`_UNSET`, not `''`. The register importer names only `url`, so a mapping
    refresh must not quietly erase an address somebody typed on the Teema page.
    """
    engagement = add_engagement(
        matter=normal_matter,
        kind=EngagementKind.SURVEY,
        title="Liikmed",
        smaily_url=SMAILY_URL,
        actor=specialist,
    )

    update_engagement(engagement=engagement, url=KODA_URL, actor=specialist)

    engagement.refresh_from_db()
    assert engagement.smaily_url == SMAILY_URL
    assert engagement.url == KODA_URL


def test_the_chronology_names_the_provider_and_never_the_address(signed_in, specialist):
    """**J (read).** `Smaily`, `Alchemer` — and no tracking parameters on the page.

    A campaign URL is mostly a recipient token. It is the anchor's `href`, which
    is where a link goes, and it is not text, which is what a reader and a
    screen reader get read out to them.
    """
    matter = factories.MatterFactory(owner=specialist)
    add_engagement(
        matter=matter,
        kind=EngagementKind.EMAIL_CAMPAIGN,
        title="Liikmed",
        smaily_url=SMAILY_URL,
        alchemer_url=ALCHEMER_URL,
        actor=specialist,
    )

    body = _rendered(signed_in, matter)
    assert f'href="{SMAILY_URL}"' in body
    assert f'href="{ALCHEMER_URL}"' in body
    assert ">Smaily<" in body
    assert ">Alchemer<" in body
    assert 'rel="noopener noreferrer"' in body
    # The address is never printed as copy.
    assert ">" + SMAILY_URL not in body


def test_an_engagement_with_no_links_renders_no_link_row(signed_in, specialist):
    """An empty container is a row that says «Lingid» and then nothing."""
    matter = factories.MatterFactory(owner=specialist)
    add_engagement(matter=matter, kind=EngagementKind.SURVEY, title="Liikmed", actor=specialist)

    body = _rendered(signed_in, matter)
    assert "Kaasamine: Liikmed" in body
    assert "uxtl__links" not in body


def test_a_restricted_engagements_links_do_not_reach_a_reader_who_cannot_see_it(client, specialist):
    """**J (visibility).** The links inherit the engagement's scope exactly.

    Nothing new is needed for this — every read already goes through
    `visible_to` — and that is the point of the test: it proves the new columns
    did not open a second door beside the one that is guarded.
    """
    reader = factories.ReaderFactory()
    matter = factories.MatterFactory(owner=specialist)
    engagement = add_engagement(
        matter=matter,
        kind=EngagementKind.SURVEY,
        title="Salajane küsitlus",
        smaily_url=SMAILY_URL,
        alchemer_url=ALCHEMER_URL,
        actor=specialist,
    )
    MatterEngagement.objects.filter(pk=engagement.pk).update(
        visibility_override=Visibility.RESTRICTED
    )

    client.force_login(reader)
    body = client.get(reverse("matters:matter_detail", kwargs={"pk": matter.pk})).content.decode()

    assert "Salajane küsitlus" not in body
    assert SMAILY_URL not in body
    assert ALCHEMER_URL not in body


def test_the_search_projection_indexes_the_provider_and_not_the_query_string(
    normal_matter, specialist
):
    """The host and its labels, exactly as the generic `url` has always been.

    Never the query string: a campaign address carries recipient ids and
    one-time tokens after the `?`, and indexing those would put somebody's
    unsubscribe key into a search field.
    """
    engagement = add_engagement(
        matter=normal_matter,
        kind=EngagementKind.EMAIL_CAMPAIGN,
        title="Liikmed",
        smaily_url="https://sendsmaily.net/c/9182?token=SECRET-ONE-TIME-KEY",
        alchemer_url=ALCHEMER_URL,
        actor=specialist,
    )

    terms = engagement.link_search_terms
    assert "sendsmaily.net" in terms
    assert "sendsmaily" in terms
    assert "survey.alchemer.eu" in terms
    assert "alchemer" in terms
    assert not any("SECRET-ONE-TIME-KEY" in term for term in terms)
    assert not any("token" in term for term in terms)


def test_the_panel_saves_both_links_through_the_route_a_person_uses(signed_in, specialist):
    """`+ Kaasamine`, end to end."""
    matter = factories.MatterFactory(owner=specialist)

    response = signed_in.post(
        reverse("matters:add_engagement_compact", kwargs={"pk": matter.pk}),
        {
            "kind": "SURVEY",
            "audience": "liikmed",
            "response_count": "",
            "smaily_url": SMAILY_URL,
            "alchemer_url": ALCHEMER_URL,
        },
        headers={"HX-Request": "true"},
    )

    assert response.status_code == 200
    engagement = MatterEngagement.objects.get()
    assert engagement.title == "liikmed"
    assert engagement.smaily_url == SMAILY_URL
    assert engagement.alchemer_url == ALCHEMER_URL


def test_a_typed_link_with_no_audience_is_refused_rather_than_discarded(signed_in, specialist):
    """**F (route), task §6.** The panel reopens, the message names the box, and
    the address the person typed comes back with it.

    Silently dropping a URL somebody pasted is the worst of the three possible
    answers: no row, no error, and no link.
    """
    matter = factories.MatterFactory(owner=specialist)

    response = signed_in.post(
        reverse("matters:add_engagement_compact", kwargs={"pk": matter.pk}),
        {"kind": "SURVEY", "audience": "", "smaily_url": SMAILY_URL},
        headers={"HX-Request": "true"},
    )
    body = response.content.decode()

    assert response.status_code == 400
    assert not MatterEngagement.objects.exists()
    assert "Kirjuta, keda kaasati" in body
    assert _is_open(body, PANEL)
    assert f'value="{SMAILY_URL}"' in body


def test_a_refused_provider_link_says_so_under_its_own_box(signed_in, specialist):
    """The service's sentence, reported where it was typed rather than as a 400."""
    matter = factories.MatterFactory(owner=specialist)

    response = signed_in.post(
        reverse("matters:add_engagement_compact", kwargs={"pk": matter.pk}),
        {"kind": "SURVEY", "audience": "liikmed", "alchemer_url": "javascript:alert(1)"},
        headers={"HX-Request": "true"},
    )
    body = response.content.decode()

    assert response.status_code == 400
    assert not MatterEngagement.objects.exists()
    assert "Link peab algama" in body
    assert 'id="id_alchemer_url_error"' in body


def test_the_composer_counts_a_typed_link_as_attempted_work(specialist):
    """It used to count for nothing: a pasted Smaily address with nothing else
    filled in was answered «Kirjelda tegevust või vali, mida veel salvestada»
    and thrown away with the response."""
    matter = factories.MatterFactory(owner=specialist)
    form = ComposerForm(
        data={"body": "", "engagement_smaily_url": SMAILY_URL},
        matter=matter,
        viewer=specialist,
    )

    assert not form.is_valid()
    # The panel was recognised, so the answer is about the box that is missing
    # rather than about the save being empty.
    assert "engagement_audience" in form.errors
    assert "Kirjelda tegevust" not in str(form.errors)
