"""The approved Teema workspace redesign, rule by rule.

One module rather than additions scattered through eight, because the redesign
is one decision: a lawyer opens a Matter, understands it above the fold, and
records one professional update without leaving the page. The rules below are
what make that true, and each of them is a thing somebody could quietly undo.

What this does *not* re-test is the domain underneath. `Järgmiseks` semantics,
Submission invariants, evidence immutability and authorization each have their
own suite and are unchanged; what is asserted here is that the new surface
reaches them and does not weaken them.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.urls import reverse
from django.utils import timezone

from app.audit.enums import ChangeEventType
from app.audit.models import ChangeEvent
from app.core.dates import format_estonian_date
from app.core.enums import Visibility
from app.documents.enums import DocumentRole
from app.documents.models import Document
from app.documents.services import add_evidence_version, create_document, link_working_document
from app.intelligence.enums import WorkVictoryStatus
from app.intelligence.models import MatterImportantDate, MatterWorkVictory
from app.matters import selectors
from app.matters.enums import EngagementKind
from app.matters.models import Entry, MatterEngagement, MatterPersonalNote
from app.matters.services import (
    add_engagement,
    close_matter,
    compose_update,
    personal_note_for,
    save_personal_note,
    set_brief_summary,
    set_policy_areas,
)
from app.matters.timeline import matter_timeline
from app.submissions.enums import SubmissionStatus
from app.submissions.models import Submission
from app.workflow.enums import ActionKind, ActionStatus, DatePrecision, DateSemantics, Disposition
from app.workflow.models import NextAction
from app.workflow.services import current_next_action, set_next_action
from tests import factories

pytestmark = pytest.mark.django_db


def _body(response) -> str:
    return response.content.decode()


def _detail(client, matter) -> str:
    return _body(client.get(reverse("matters:matter_detail", kwargs={"pk": matter.pk})))


# ---------------------------------------------------------------------------
# §3 — exactly two tabs
# ---------------------------------------------------------------------------


def test_a_matter_has_exactly_two_tabs(signed_in, specialist):
    matter = factories.MatterFactory(owner=specialist)
    body = _detail(signed_in, matter)

    assert body.count('class="tabs__tab') == 2
    assert ">Teema<" in body
    assert "Dokumendid" in body
    assert "Seisukoht ja kaasamine" not in body


def test_the_removed_furniture_is_gone_for_good(signed_in, specialist):
    """Four permanently empty sections, an intake block and a rail close panel.

    Together they were about half the page of a Matter nobody had touched yet,
    and none of them told the reader anything (Teema redesign §3).
    """
    matter = factories.MatterFactory(owner=specialist)
    body = _detail(signed_in, matter)

    assert "Saabunud materjalid" not in body
    assert "Olulisi tähtaegu pole lisatud." not in body
    assert "Jõustumise infot pole lisatud." not in body
    assert "Töövõite ega kandidaate pole lisatud." not in body
    # Closing is a composer action now, not a box in the facts rail.
    close_action = 'action="' + reverse("matters:close", kwargs={"pk": matter.pk})
    assert close_action not in body


# ---------------------------------------------------------------------------
# §5 — the header
# ---------------------------------------------------------------------------


def test_state_is_legible_without_colour(signed_in, specialist):
    matter = factories.MatterFactory(owner=specialist)
    assert "Avatud" in _detail(signed_in, matter)

    close_matter(matter=matter, disposition=Disposition.COMPLETED, actor=specialist)
    assert "Suletud" in _detail(signed_in, matter)


def test_a_restricted_matter_is_chipped_and_says_nothing_more(signed_in, specialist):
    matter = factories.MatterFactory(owner=specialist, visibility=Visibility.RESTRICTED)
    body = _detail(signed_in, matter)

    assert "Piiratud" in body
    # The chip says *that* it is restricted. Why is not on the page: a reason
    # beside the chip would leak the thing the restriction protects.
    #
    # `lockchip` since the Uus teema round: the badge used to claim the bare
    # `.chip` class, which the create form's own chip control then collided
    # with. Same badge, same pixels, its own name (static/css/app.css).
    assert "lockchip--restricted" in body


def test_a_restricted_matter_is_unreachable_for_an_outsider(client, other_specialist, specialist):
    matter = factories.MatterFactory(owner=specialist, visibility=Visibility.RESTRICTED)
    client.force_login(other_specialist)

    response = client.get(reverse("matters:matter_detail", kwargs={"pk": matter.pk}))

    assert response.status_code == 404


def test_the_header_shows_the_nearest_future_deadline(specialist):
    today = timezone.localdate()
    matter = factories.MatterFactory(owner=specialist, response_deadline=today + timedelta(days=30))
    MatterImportantDate.objects.create(
        matter=matter,
        title="Kooskõlastusringi lõpp",
        date_value=today + timedelta(days=5),
        period_end=today + timedelta(days=5),
    )

    active = selectors.active_deadline(matter, specialist)

    assert active is not None
    assert active.label == "Kooskõlastusringi lõpp"
    assert active.days_remaining == 5
    assert not active.is_past


def test_the_header_falls_back_to_the_nearest_past_deadline(specialist):
    today = timezone.localdate()
    matter = factories.MatterFactory(owner=specialist, response_deadline=today - timedelta(days=40))
    MatterImportantDate.objects.create(
        matter=matter,
        title="Möödunud verstapost",
        date_value=today - timedelta(days=3),
        period_end=today - timedelta(days=3),
    )

    active = selectors.active_deadline(matter, specialist)

    assert active is not None
    assert active.label == "Möödunud verstapost"
    assert active.is_past


def test_the_deadline_slot_disappears_when_there_is_none(signed_in, specialist):
    """No em dash beside a label. The absence is the rendering."""
    matter = factories.MatterFactory(owner=specialist, response_deadline=None)

    body = _detail(signed_in, matter)

    assert selectors.active_deadline(matter, specialist) is None
    assert "metaline__item--deadline" not in body


def test_the_next_action_review_date_is_not_a_header_deadline(specialist):
    """A review date is when to look again. It is not a commitment."""
    matter = factories.MatterFactory(owner=specialist, response_deadline=None)
    set_next_action(
        matter=matter,
        text="Ootan ministeeriumi vastust",
        kind=ActionKind.WAIT,
        date_semantics=DateSemantics.REVIEW_ON,
        target_date=timezone.localdate() + timedelta(days=3),
        actor=specialist,
    )

    assert selectors.active_deadline(matter, specialist) is None


def test_a_cancelled_milestone_is_not_the_active_deadline(specialist):
    from app.intelligence.services import add_important_date, cancel_important_date

    today = timezone.localdate()
    matter = factories.MatterFactory(owner=specialist, response_deadline=None)
    record = add_important_date(
        matter=matter,
        title="Ärajäänud ring",
        date_value=today + timedelta(days=4),
        period_end=today + timedelta(days=4),
        actor=specialist,
    )
    cancel_important_date(record=record, actor=specialist)

    assert selectors.active_deadline(matter, specialist) is None


# ---------------------------------------------------------------------------
# §6 — Lühikokkuvõte
# ---------------------------------------------------------------------------


def test_a_matter_starts_with_no_summary(normal_matter):
    assert normal_matter.brief_summary == ""


def test_the_summary_is_created_edited_and_audited(normal_matter, specialist):
    set_brief_summary(
        matter=normal_matter,
        value="Eelnõu paneks digiplatvormidele kvartaalse aruandluskohustuse.",
        actor=specialist,
    )
    normal_matter.refresh_from_db()
    assert normal_matter.brief_summary.startswith("Eelnõu paneks")

    events = ChangeEvent.objects.filter(
        matter=normal_matter, event_type=ChangeEventType.MATTER_BRIEF_SUMMARY_SET
    )
    assert events.count() == 1
    assert events.first().payload["created"] is True
    # The text itself is deliberately absent from the audit row: a working
    # description somebody rewrites is not history to be copied.
    assert "digiplatvormidele" not in str(events.first().payload)

    set_brief_summary(matter=normal_matter, value="", actor=specialist)
    normal_matter.refresh_from_db()
    assert normal_matter.brief_summary == ""
    assert events.count() == 2


def test_an_unchanged_summary_writes_nothing(normal_matter, specialist):
    set_brief_summary(matter=normal_matter, value="Sama tekst.", actor=specialist)
    set_brief_summary(matter=normal_matter, value="Sama tekst.", actor=specialist)

    assert (
        ChangeEvent.objects.filter(
            matter=normal_matter, event_type=ChangeEventType.MATTER_BRIEF_SUMMARY_SET
        ).count()
        == 1
    )


def test_the_summary_is_edited_inline_without_leaving_the_page(signed_in, normal_matter):
    response = signed_in.post(
        reverse("matters:update_summary", kwargs={"pk": normal_matter.pk}),
        {"brief_summary": "Kaks lauset tavakeeles."},
        headers={"HX-Request": "true"},
    )

    assert response.status_code == 200
    assert "Kaks lauset tavakeeles." in _body(response)
    normal_matter.refresh_from_db()
    assert normal_matter.brief_summary == "Kaks lauset tavakeeles."


def test_the_summary_is_searchable(normal_matter, specialist):
    from app.search.models import SearchDocument, SearchSourceKind

    set_brief_summary(
        matter=normal_matter,
        value="Platvormimajanduse aruandluskohustus puudutab 400 liikmesettevõtet.",
        actor=specialist,
    )

    row = SearchDocument.objects.get(matter=normal_matter, source_kind=SearchSourceKind.MATTER)
    assert "liikmesettevõtet" in row.body_text


def test_the_summary_respects_matter_visibility(client, other_specialist, specialist):
    matter = factories.MatterFactory(owner=specialist, visibility=Visibility.RESTRICTED)
    set_brief_summary(matter=matter, value="Ei tohi lekkida.", actor=specialist)
    client.force_login(other_specialist)

    response = client.get(reverse("matters:matter_detail", kwargs={"pk": matter.pk}))

    assert response.status_code == 404


# ---------------------------------------------------------------------------
# §7 — Valdkonnad
# ---------------------------------------------------------------------------


def test_the_header_offers_the_governed_vocabulary(signed_in, normal_matter):
    from app.taxonomy.vocabulary import selectable_policy_areas

    body = _detail(signed_in, normal_matter)

    for area in selectable_policy_areas():
        assert area.name_et in body


def test_valdkonnad_are_edited_inline_and_audited(signed_in, normal_matter):
    from app.taxonomy.models import PolicyArea

    chosen = PolicyArea.objects.get(key="maksud-ja-toll")

    response = signed_in.post(
        reverse("matters:update_field", kwargs={"pk": normal_matter.pk, "field": "policy_areas"}),
        {"policy_areas": [str(chosen.pk)]},
        headers={"HX-Request": "true"},
    )

    assert response.status_code == 200
    assert list(normal_matter.policy_areas.all()) == [chosen]
    assert ChangeEvent.objects.filter(
        matter=normal_matter, event_type=ChangeEventType.MATTER_POLICY_AREAS_CHANGED
    ).exists()


def test_a_retired_area_stays_on_the_matter_that_carries_it(normal_matter, specialist):
    """Correcting one field must never silently drop an old classification."""
    from app.taxonomy.models import PolicyArea

    retired = PolicyArea.objects.get(key="halduskoormus")
    normal_matter.policy_areas.add(retired)

    assert not retired.is_active
    assert list(normal_matter.policy_areas.all()) == [retired]

    # And the control accepts it back, so a save that only changed the owner
    # does not clear it.
    set_policy_areas(matter=normal_matter, policy_areas=[retired], actor=specialist)
    assert list(normal_matter.policy_areas.all()) == [retired]


def test_valdkonnad_and_sildid_stay_different_vocabularies(signed_in, normal_matter):
    tag = factories.TagFactory(name_et="Käibemaks")
    normal_matter.tags.add(tag)

    body = _detail(signed_in, normal_matter)

    # Sildid are in the rail; Valdkonnad are in the header meta line. Nothing
    # merges the two lists.
    assert "Sildid" in body
    assert "Käibemaks" in body


# ---------------------------------------------------------------------------
# §8 — JÄRGMISEKS
# ---------------------------------------------------------------------------


def test_teen_with_a_passed_deadline_is_overdue(signed_in, specialist):
    matter = factories.MatterFactory(owner=specialist)
    set_next_action(
        matter=matter,
        text="Esitan arvamuse",
        kind=ActionKind.DO,
        date_semantics=DateSemantics.DEADLINE,
        target_date=timezone.localdate() - timedelta(days=2),
        actor=specialist,
    )

    body = _detail(signed_in, matter)

    assert "Tähtaeg möödas" in body
    assert "nextrow__date--overdue" in body


@pytest.mark.parametrize("kind", [ActionKind.WAIT, ActionKind.MONITOR])
def test_a_passed_review_date_is_a_warning_and_never_lateness(signed_in, specialist, kind):
    matter = factories.MatterFactory(owner=specialist)
    action = set_next_action(
        matter=matter,
        text="Ootan arengut",
        kind=kind,
        date_semantics=DateSemantics.REVIEW_ON,
        target_date=timezone.localdate() - timedelta(days=2),
        actor=specialist,
    )

    assert not action.is_overdue()
    assert action.is_due_for_review()

    body = _detail(signed_in, matter)
    assert "Ülevaatus möödas" in body
    assert "Tähtaeg möödas" not in body
    # And the date is labelled as a review, never as a bare deadline.
    assert "vaatan üle" in body


def test_an_approximate_review_date_renders_as_its_period(signed_in, specialist):
    matter = factories.MatterFactory(owner=specialist)
    set_next_action(
        matter=matter,
        text="Jälgin menetluse käiku",
        kind=ActionKind.MONITOR,
        date_semantics=DateSemantics.REVIEW_ON,
        target_date=timezone.localdate().replace(month=9, day=1),
        date_precision=DatePrecision.MONTH,
        actor=specialist,
    )

    body = _detail(signed_in, matter)

    assert "september" in body


def test_a_quarter_renders_as_a_quarter(specialist):
    matter = factories.MatterFactory(owner=specialist)
    action = set_next_action(
        matter=matter,
        text="Jälgin",
        kind=ActionKind.MONITOR,
        date_semantics=DateSemantics.REVIEW_ON,
        target_date=timezone.localdate().replace(month=7, day=1),
        date_precision=DatePrecision.QUARTER,
        actor=specialist,
    )

    assert action.display_date.startswith("III kvartal")


def test_an_open_matter_with_no_action_says_so(signed_in, specialist):
    matter = factories.MatterFactory(owner=specialist)

    body = _detail(signed_in, matter)

    assert "Järgmine samm on määramata" in body
    assert "Määra allpool ↓" in body


def test_an_imported_instruction_is_not_called_missing(signed_in, specialist, monkeypatch):
    """A register row carrying Excel's own sentence is not a broken record."""
    matter = factories.MatterFactory(owner=specialist)
    monkeypatch.setattr(
        "app.matters.views.source_instruction_for", lambda _matter: "Ootame RaM seisukohta."
    )

    body = _detail(signed_in, matter)

    assert "Ootame RaM seisukohta." in body
    assert "Järgmine samm on määramata" not in body


def test_a_closed_matter_refuses_a_new_next_step(signed_in, specialist):
    matter = factories.MatterFactory(owner=specialist)
    close_matter(matter=matter, disposition=Disposition.COMPLETED, actor=specialist)

    body = _detail(signed_in, matter)

    assert "teema on suletud" in body
    assert "Uut sammu ei saa määrata ilma taasavamiseta" in body
    # And the composer is not rendered at all.
    assert 'id="teema-koostaja"' not in body


def test_completing_the_step_goes_through_the_existing_service(signed_in, specialist):
    matter = factories.MatterFactory(owner=specialist)
    action = set_next_action(
        matter=matter,
        text="Esitan arvamuse",
        kind=ActionKind.DO,
        target_date=timezone.localdate() + timedelta(days=2),
        actor=specialist,
    )

    response = signed_in.post(
        reverse("matters:complete_action", kwargs={"pk": matter.pk, "action_id": action.pk}),
        headers={"HX-Request": "true"},
    )

    assert response.status_code == 200
    action.refresh_from_db()
    assert action.status == ActionStatus.COMPLETED
    assert current_next_action(matter) is None


# ---------------------------------------------------------------------------
# §9, §11 — the composer
# ---------------------------------------------------------------------------


def test_the_composer_has_one_description_box_and_no_next_step_field(signed_in, normal_matter):
    body = _detail(signed_in, normal_matter)

    assert "Kirjelda, mis tegid ja mida teed edasi…" in body
    # The one thing that must never come back.
    assert 'name="next_text"' not in body
    assert "Muudan Järgmiseks" not in body
    assert "+ Organisatsioon" not in body


def test_the_description_becomes_the_next_step_verbatim(signed_in, normal_matter, specialist):
    signed_in.post(
        reverse("matters:compose", kwargs={"pk": normal_matter.pk}),
        {
            "body": "<p>Esitan Koja arvamuse Rahandusministeeriumile EIS-i kaudu.</p>",
            "next_kind": ActionKind.DO,
            "next_date": format_estonian_date(timezone.localdate() + timedelta(days=7)),
            "next_precision": DatePrecision.EXACT,
        },
        headers={"HX-Request": "true"},
    )

    action = current_next_action(normal_matter)
    assert action is not None
    assert action.text == "Esitan Koja arvamuse Rahandusministeeriumile EIS-i kaudu."


def test_an_entry_saves_alone(normal_matter, specialist):
    result = compose_update(matter=normal_matter, author=specialist, body="<p>Lihtsalt märkus.</p>")

    assert result.entry is not None
    assert result.action is None
    assert result.closed is False


def test_a_next_step_saves_alone(normal_matter, specialist):
    result = compose_update(
        matter=normal_matter,
        author=specialist,
        next_action={"text": "Ainult samm", "kind": ActionKind.WAIT},
    )

    assert result.entry is None
    assert result.action is not None
    # No empty synthetic entry is manufactured to carry it.
    assert not Entry.objects.filter(matter=normal_matter).exists()


def test_one_save_can_write_everything_at_once(normal_matter, specialist, organisation):
    today = timezone.localdate()
    result = compose_update(
        matter=normal_matter,
        author=specialist,
        body="<p>Kohtumine RaM maksupoliitika osakonnaga.</p>",
        next_action={
            "text": "Esitan arvamuse",
            "kind": ActionKind.DO,
            "target_date": today + timedelta(days=7),
        },
        important_date={
            "title": "Kooskõlastusringi lõpp",
            "date_value": today + timedelta(days=20),
            "period_end": today + timedelta(days=20),
        },
        engagement={
            "kind": EngagementKind.SURVEY,
            "title": "Liikmete küsitlus aruandluskoormuse kohta",
            "occurred_on": today,
        },
    )

    assert result.entry is not None
    assert result.action is not None
    assert result.important_date is not None
    assert result.engagement is not None
    assert result.operation_id is not None


def test_an_invalid_sub_action_rolls_back_the_whole_save(normal_matter, specialist):
    """Atomicity is the substance of the composer, not a technicality."""
    from app.core.errors import DomainError

    before = Entry.objects.filter(matter=normal_matter).count()

    with pytest.raises(DomainError):
        compose_update(
            matter=normal_matter,
            author=specialist,
            body="<p>See ei tohi alles jääda.</p>",
            engagement={"kind": EngagementKind.SURVEY, "title": "   "},
        )

    assert Entry.objects.filter(matter=normal_matter).count() == before
    assert not MatterEngagement.objects.filter(matter=normal_matter).exists()


def test_the_composer_refuses_an_empty_save(normal_matter, specialist):
    from app.core.errors import DomainError

    with pytest.raises(DomainError):
        compose_update(matter=normal_matter, author=specialist)


def test_the_composer_checks_its_own_authorization(client, normal_matter):
    """A unified surface is not a unified rule set."""
    reader = factories.ReaderFactory()
    client.force_login(reader)

    response = client.post(
        reverse("matters:compose", kwargs={"pk": normal_matter.pk}),
        {"body": "<p>Ei tohi salvestuda.</p>"},
    )

    assert response.status_code == 404
    assert not Entry.objects.filter(matter=normal_matter).exists()


# ---------------------------------------------------------------------------
# §11.1 — one save, one human timeline event
# ---------------------------------------------------------------------------


def test_one_composer_save_is_one_timeline_item(normal_matter, specialist):
    today = timezone.localdate()
    compose_update(
        matter=normal_matter,
        author=specialist,
        body="<p>Kohtumine ministeeriumiga.</p>",
        next_action={
            "text": "Esitan arvamuse",
            "kind": ActionKind.DO,
            "target_date": today + timedelta(days=7),
        },
        important_date={
            "title": "Kooskõlastusringi lõpp",
            "date_value": today + timedelta(days=20),
            "period_end": today + timedelta(days=20),
        },
    )

    items, _more = matter_timeline(matter=normal_matter, user=specialist, limit=50)
    saves = [item for item in items if item.is_grouped]

    assert len(saves) == 1
    assert saves[0].summary_sentence == (
        "lisas märkuse, määras järgmise sammu ja lisas olulise tähtaja"
    )


def test_the_underlying_audit_facts_are_all_still_there(normal_matter, specialist):
    """Grouped for a reader, never merged in the record."""
    today = timezone.localdate()
    compose_update(
        matter=normal_matter,
        author=specialist,
        body="<p>Kohtumine.</p>",
        next_action={
            "text": "Esitan arvamuse",
            "kind": ActionKind.DO,
            "target_date": today + timedelta(days=7),
        },
    )

    events = ChangeEvent.objects.filter(matter=normal_matter)
    types = set(events.values_list("event_type", flat=True))

    assert ChangeEventType.ENTRY_ADDED in types
    assert ChangeEventType.NEXT_ACTION_SET in types
    operations = {event.operation_id for event in events if event.operation_id is not None}
    assert len(operations) == 1


def test_unrelated_saves_are_never_grouped(normal_matter, specialist):
    """Two clicks a second apart are two things somebody did."""
    compose_update(matter=normal_matter, author=specialist, body="<p>Esimene.</p>")
    compose_update(matter=normal_matter, author=specialist, body="<p>Teine.</p>")

    items, _more = matter_timeline(matter=normal_matter, user=specialist, limit=50)
    entries = [item for item in items if item.is_entry]

    assert len(entries) == 2
    operations = {
        event.operation_id
        for event in ChangeEvent.objects.filter(
            matter=normal_matter, event_type=ChangeEventType.ENTRY_ADDED
        )
    }
    assert len(operations) == 2


def test_a_standalone_engagement_writes_no_timeline_row(normal_matter, specialist):
    """The section already shows the fact; the narrative is for authored work."""
    add_engagement(
        matter=normal_matter,
        kind=EngagementKind.SURVEY,
        title="Liikmete küsitlus",
        actor=specialist,
    )

    items, _more = matter_timeline(matter=normal_matter, user=specialist, limit=50)

    assert not any(
        item.event and item.event.event_type == ChangeEventType.ENGAGEMENT_ADDED for item in items
    )


def test_the_timeline_paginates(normal_matter, specialist):
    for index in range(40):
        compose_update(matter=normal_matter, author=specialist, body=f"<p>Kirje {index}</p>")

    page, has_more = matter_timeline(matter=normal_matter, user=specialist, limit=30)

    assert len(page) == 30
    assert has_more


def test_the_collapsed_timeline_does_not_render_everything(signed_in, specialist):
    matter = factories.MatterFactory(owner=specialist)
    for index in range(60):
        compose_update(matter=matter, author=specialist, body=f"<p>Kirje {index}</p>")

    body = _detail(signed_in, matter)

    assert "Kirje 59" in body
    assert "Kirje 0</p>" not in body
    assert "Näita varasemaid" in body


# ---------------------------------------------------------------------------
# §14 — Kaasamine
# ---------------------------------------------------------------------------


def test_the_new_engagement_form_offers_exactly_three_types():
    from app.matters.forms import EngagementForm

    labels = [label for _value, label in EngagementForm().fields["kind"].choices]

    assert labels == ["Küsitlus", "Otsepostitus", "Muu"]


def test_a_legacy_engagement_kind_is_still_readable(signed_in, specialist):
    matter = factories.MatterFactory(owner=specialist)
    add_engagement(
        matter=matter,
        kind=EngagementKind.WEB_CALL,
        title="Vana kaasamiskutse",
        actor=specialist,
    )

    body = _detail(signed_in, matter)

    assert "Vana kaasamiskutse" in body
    assert "Kaasamiskutse veebis" in body


def test_the_engagement_line_shows_type_and_date_not_an_invented_count(signed_in, specialist):
    """`MatterEngagement` stores no response count, so none is displayed."""
    matter = factories.MatterFactory(owner=specialist)
    add_engagement(
        matter=matter,
        kind=EngagementKind.SURVEY,
        title="Liikmete küsitlus",
        occurred_on=timezone.localdate(),
        actor=specialist,
    )

    body = _detail(signed_in, matter)
    # Scoped to the collapsed line. The composer's outcome box quotes a survey
    # result as its placeholder, which is copy rather than data.
    line = body[body.index("accordion__summary") : body.index("accordion__body")]

    assert "Küsitlus" in line
    assert "vastajat" not in line
    assert not any(field.name == "response_count" for field in MatterEngagement._meta.get_fields())


def test_an_engagement_can_carry_a_linked_file(normal_matter, specialist):
    record = add_engagement(
        matter=normal_matter,
        kind=EngagementKind.SURVEY,
        title="Liikmete küsitlus",
        url="https://example.invalid/koond.xlsx",
        actor=specialist,
    )

    assert record.url.endswith("koond.xlsx")
    assert record.link_label == "example.invalid"


# ---------------------------------------------------------------------------
# §15, §16, §18 — closure
# ---------------------------------------------------------------------------


def test_closing_from_the_composer_ends_the_open_step(normal_matter, specialist):
    set_next_action(
        matter=normal_matter,
        text="Esitan arvamuse",
        kind=ActionKind.DO,
        target_date=timezone.localdate() + timedelta(days=3),
        actor=specialist,
    )

    result = compose_update(
        matter=normal_matter,
        author=specialist,
        body="<p>Seadus jõustus. Töö lõppes.</p>",
        closure={"disposition": Disposition.COMPLETED, "reason": "Jõustus 01.01.2027."},
    )

    normal_matter.refresh_from_db()
    assert result.closed
    assert not normal_matter.is_open
    assert normal_matter.disposition == Disposition.COMPLETED
    assert current_next_action(normal_matter) is None
    # Ended, never deleted.
    assert NextAction.objects.filter(matter=normal_matter).count() == 1


def test_the_composer_view_closes_a_matter(signed_in, normal_matter, specialist):
    """The whole closure path, through the form the browser actually posts.

    `compose_update` has its own tests, and they pass a Python dict. This one
    goes through `ComposerForm` with the field set a browser sends — every
    optional group present and empty, the closure group filled — because the
    parsing between those two is where a closure can be quietly dropped and
    the save still return 200.
    """
    set_next_action(
        matter=normal_matter,
        text="Esitan arvamuse",
        kind=ActionKind.DO,
        target_date=timezone.localdate() + timedelta(days=3),
        actor=specialist,
    )

    response = signed_in.post(
        reverse("matters:compose", kwargs={"pk": normal_matter.pk}),
        {
            "body": "<p>Menetlus lõppes; töö on tehtud.</p>",
            "kind": "NOTE",
            "attachment_role": DocumentRole.OTHER,
            "next_kind": "",
            "next_date": "",
            "next_precision": DatePrecision.EXACT,
            "next_date_semantics": "",
            "deadline_title": "",
            "deadline_date": "",
            "deadline_precision": DatePrecision.EXACT,
            "engagement_kind": EngagementKind.SURVEY,
            "engagement_title": "",
            "engagement_date": "",
            "engagement_url": "",
            "engagement_note": "",
            "close_matter": "on",
            "disposition": Disposition.COMPLETED,
            "closure_reason": "Seadus jõustus muutmata kujul.",
            "successor": "",
            "final_version": "",
            "final_title": "",
            "final_sent_on": "",
            "final_channel": "",
            "final_reference": "",
            "victory_title": "",
            "victory_detail": "",
        },
        headers={"HX-Request": "true"},
    )

    assert response.status_code == 200, response.content.decode()[:4000]
    normal_matter.refresh_from_db()
    assert not normal_matter.is_open, "the composer save did not close the Matter"
    assert normal_matter.disposition == Disposition.COMPLETED
    assert current_next_action(normal_matter) is None


def test_a_successor_is_a_real_relationship(normal_matter, specialist):
    successor = factories.MatterFactory(owner=specialist)

    compose_update(
        matter=normal_matter,
        author=specialist,
        closure={
            "disposition": Disposition.SUPERSEDED,
            "reason": "Töö jätkub uue eelnõu all.",
            "successor": successor,
        },
    )

    normal_matter.refresh_from_db()
    assert normal_matter.superseded_by == successor
    assert list(successor.supersedes.all()) == [normal_matter]


def test_a_successor_needs_the_disposition_that_asserts_one(normal_matter, specialist):
    from app.core.errors import DomainError

    successor = factories.MatterFactory(owner=specialist)

    with pytest.raises(DomainError):
        close_matter(
            matter=normal_matter,
            disposition=Disposition.COMPLETED,
            successor=successor,
            actor=specialist,
        )


def test_reopening_clears_the_successor(normal_matter, specialist):
    from app.matters.services import reopen_matter

    successor = factories.MatterFactory(owner=specialist)
    close_matter(
        matter=normal_matter,
        disposition=Disposition.SUPERSEDED,
        successor=successor,
        actor=specialist,
    )

    normal_matter.refresh_from_db()
    reopen_matter(matter=normal_matter, actor=specialist)

    normal_matter.refresh_from_db()
    assert normal_matter.is_open
    assert normal_matter.superseded_by is None


def test_joustunud_alone_does_not_close_the_matter(normal_matter, specialist):
    """A stage is where the external process is. Closure is Koda's decision."""
    from app.matters.services import change_stage

    joustunud = factories.StageFactory(key="joustunud", label_et="Jõustunud")
    change_stage(matter=normal_matter, stage=joustunud, actor=specialist)

    normal_matter.refresh_from_db()
    assert normal_matter.is_open
    assert normal_matter.disposition == ""


def test_a_stage_change_never_reopens_a_closed_matter(normal_matter, specialist):
    from app.matters.services import change_stage

    close_matter(matter=normal_matter, disposition=Disposition.COMPLETED, actor=specialist)
    later = factories.StageFactory(key="riigikogus", label_et="Riigikogus")
    change_stage(matter=normal_matter, stage=later, actor=specialist)

    normal_matter.refresh_from_db()
    assert not normal_matter.is_open


def test_a_work_victory_recorded_at_closure_uses_the_existing_domain(normal_matter, specialist):
    result = compose_update(
        matter=normal_matter,
        author=specialist,
        closure={
            "disposition": Disposition.COMPLETED,
            "work_victory": {"title": "Piirmäär tõsteti 2000 euroni", "detail": "RaM nõustus."},
        },
    )

    assert result.work_victory is not None
    record = MatterWorkVictory.objects.get(matter=normal_matter)
    assert record.title == "Piirmäär tõsteti 2000 euroni"
    # The same door the Matter page's own control uses — the composer broadens
    # nobody's authorization.
    assert record.status == WorkVictoryStatus.CONFIRMED


def test_closing_needs_business_write(client, normal_matter):
    reader = factories.ReaderFactory()
    client.force_login(reader)

    response = client.post(
        reverse("matters:close", kwargs={"pk": normal_matter.pk}),
        {"disposition": Disposition.COMPLETED, "reason": ""},
    )

    assert response.status_code == 404
    normal_matter.refresh_from_db()
    assert normal_matter.is_open


# ---------------------------------------------------------------------------
# §17, §20 — the final opinion and the sent strip
# ---------------------------------------------------------------------------


def _evidence(matter, actor, filename="Koja_arvamus.pdf"):
    document = create_document(
        matter=matter, title=filename, role=DocumentRole.KODA_SUBMISSION_FINAL, created_by=actor
    )
    return add_evidence_version(
        document=document,
        content=b"%PDF-1.4 test",
        original_filename=filename,
        mime_type="application/pdf",
        uploaded_by=actor,
    )


def test_a_final_opinion_at_closure_becomes_a_canonical_submission(
    normal_matter, specialist, organisation
):
    version = _evidence(normal_matter, specialist)
    sent_on = timezone.now() - timedelta(days=1)

    result = compose_update(
        matter=normal_matter,
        author=specialist,
        closure={
            "disposition": Disposition.RESPONSE_COMPLETE,
            "final_opinion": {
                "title": "Koja arvamus platvormimajanduse aruandluse kohta",
                "final_version": version,
                "recipients": [organisation],
                "sent_at": sent_on,
                "channel": "EIS",
                "reference": "26-0842",
            },
        },
    )

    submission = result.submission
    assert submission is not None
    assert submission.status == SubmissionStatus.SENT
    assert submission.final_version == version
    assert submission.sent_at == sent_on
    assert submission.channel == "EIS"


def test_a_pdf_alone_never_creates_a_submission(normal_matter, specialist):
    compose_update(
        matter=normal_matter,
        author=specialist,
        body="<p>Sain ministeeriumilt faili.</p>",
        attachment=_upload(),
    )

    assert Document.objects.filter(matter=normal_matter).count() == 1
    assert not Submission.objects.filter(matter=normal_matter).exists()


def test_the_rail_shows_only_a_canonical_sent_opinion(signed_in, specialist, organisation):
    """The sent opinion reaches the main view — once, in the rail.

    The redesign put it in a full-width strip of its own under the position
    block, which said the same thing the position said, a second time and
    further down. Hands-on QA collapsed both into one rail block: what Koda
    argued, and the file that says so (Teema QA §1.2).
    """
    matter = factories.MatterFactory(owner=specialist)
    assert "railposition__opinion" not in _detail(signed_in, matter)

    version = _evidence(matter, specialist)
    compose_update(
        matter=matter,
        author=specialist,
        closure={
            "disposition": Disposition.RESPONSE_COMPLETE,
            "final_opinion": {
                "title": "Koja arvamus",
                "final_version": version,
                "recipients": [organisation],
                "sent_at": timezone.now(),
            },
        },
    )

    body = _detail(signed_in, matter)
    assert "railposition__opinion" in body
    assert "Koja_arvamus.pdf" in body
    # And nothing else on the page says it a second time.
    assert "sentstrip" not in body
    assert body.count("Koja seisukoht") == 1


def test_a_draft_submission_never_reaches_the_rail(signed_in, specialist):
    matter = factories.MatterFactory(owner=specialist)
    factories.SubmissionFactory(matter=matter, title="Mustand", status=SubmissionStatus.DRAFT)

    body = _detail(signed_in, matter)

    assert "railposition__opinion" not in body


# ---------------------------------------------------------------------------
# §22.4 — the private note
# ---------------------------------------------------------------------------


def test_a_note_is_private_to_its_author(normal_matter, specialist, other_specialist):
    save_personal_note(matter=normal_matter, author=specialist, body="RaM kontakt: Liina.")

    assert personal_note_for(matter=normal_matter, author=specialist) == "RaM kontakt: Liina."
    assert personal_note_for(matter=normal_matter, author=other_specialist) == ""


def test_two_people_keep_two_notes(normal_matter, specialist, other_specialist):
    save_personal_note(matter=normal_matter, author=specialist, body="Minu oma.")
    save_personal_note(matter=normal_matter, author=other_specialist, body="Sinu oma.")

    assert MatterPersonalNote.objects.filter(matter=normal_matter).count() == 2
    assert personal_note_for(matter=normal_matter, author=specialist) == "Minu oma."


def test_a_note_autosaves_and_swaps_nothing(signed_in, normal_matter, specialist):
    response = signed_in.post(
        reverse("matters:save_note", kwargs={"pk": normal_matter.pk}),
        {"markmed-body": "Küsi üle, kas kvartaalne sagedus on direktiivi nõue."},
        headers={"HX-Request": "true"},
    )

    assert response.status_code == 204
    assert personal_note_for(matter=normal_matter, author=specialist).startswith("Küsi üle")


def test_a_note_is_not_business_history(normal_matter, specialist):
    before = ChangeEvent.objects.filter(matter=normal_matter).count()

    save_personal_note(matter=normal_matter, author=specialist, body="Mustand.")

    assert ChangeEvent.objects.filter(matter=normal_matter).count() == before
    items, _more = matter_timeline(matter=normal_matter, user=specialist, limit=50)
    assert not any("Mustand." in str(item.entry.body if item.entry else "") for item in items)


def test_another_persons_note_never_reaches_the_page(client, normal_matter, other_specialist):
    save_personal_note(
        matter=normal_matter, author=normal_matter.owner, body="Ainult minu silmadele."
    )
    client.force_login(other_specialist)

    body = _body(client.get(reverse("matters:matter_detail", kwargs={"pk": normal_matter.pk})))

    assert "Ainult minu silmadele." not in body


# ---------------------------------------------------------------------------
# §23 — the Dokumendid tab
# ---------------------------------------------------------------------------


def _upload(name: str = "lisa.pdf"):
    from django.core.files.uploadedfile import SimpleUploadedFile

    return SimpleUploadedFile(name, b"%PDF-1.4 test", content_type="application/pdf")


def test_the_documents_tab_is_empty_and_says_so_once(signed_in, specialist):
    matter = factories.MatterFactory(owner=specialist)

    body = _body(signed_in.get(reverse("matters:matter_documents", kwargs={"pk": matter.pk})))

    assert "Sellel teemal ei ole veel dokumente." in body
    assert body.count("Sellel teemal ei ole veel dokumente.") == 1


def test_the_final_opinion_row_is_marked(signed_in, specialist):
    matter = factories.MatterFactory(owner=specialist)
    _evidence(matter, specialist)

    body = _body(signed_in.get(reverse("matters:matter_documents", kwargs={"pk": matter.pk})))

    assert "★ Lõplik" in body
    assert "doctable__row--final" in body


def test_a_working_reference_is_not_evidence(signed_in, specialist):
    matter = factories.MatterFactory(owner=specialist)
    document = link_working_document(
        matter=matter,
        title="Arvamuse_töödokument.docx",
        web_url="https://example.invalid/sites/oigus/doc.docx",
        site_path="Õigusosakond / KMS 2026",
        created_by=specialist,
    )

    assert document.has_working_document
    assert document.current_version is None
    assert document.role == DocumentRole.WORKING_DOCUMENT

    body = _body(signed_in.get(reverse("matters:matter_documents", kwargs={"pk": matter.pk})))
    assert "SharePoint" in body
    assert "Arvamuse_töödokument.docx" in body


def test_a_working_reference_refuses_a_non_web_address(normal_matter, specialist):
    from app.core.errors import DomainError

    with pytest.raises(DomainError):
        link_working_document(
            matter=normal_matter,
            title="Fail",
            web_url="file:///C:/kohalik.docx",
            created_by=specialist,
        )


def test_the_documents_tab_filters_by_role(signed_in, specialist):
    matter = factories.MatterFactory(owner=specialist)
    _evidence(matter, specialist, filename="lõplik.pdf")
    other = create_document(
        matter=matter,
        title="protokoll.pdf",
        role=DocumentRole.MEMBER_FEEDBACK,
        created_by=specialist,
    )
    add_evidence_version(
        document=other,
        content=b"%PDF-1.4",
        original_filename="protokoll.pdf",
        mime_type="application/pdf",
        uploaded_by=specialist,
    )

    url = reverse("matters:matter_documents", kwargs={"pk": matter.pk})
    body = _body(signed_in.get(f"{url}?roll={DocumentRole.KODA_SUBMISSION_FINAL}"))

    assert "lõplik.pdf" in body
    assert "protokoll.pdf" not in body


def test_a_restricted_document_never_reaches_an_unauthorised_reader(
    client, specialist, other_specialist
):
    """Not masked — absent. Nothing about it is rendered, not even its size."""
    matter = factories.MatterFactory(owner=specialist)
    document = create_document(
        matter=matter,
        title="Liikmesettevõtte selgitused",
        role=DocumentRole.MEMBER_FEEDBACK,
        created_by=specialist,
        visibility_override=Visibility.RESTRICTED,
    )
    add_evidence_version(
        document=document,
        content=b"%PDF-1.4 salajane",
        original_filename="selgitused.pdf",
        mime_type="application/pdf",
        uploaded_by=specialist,
    )

    # A colleague who may read the Matter and is not a participant in it. A
    # collaborator legitimately *does* see a restricted child, so testing with
    # one would prove nothing.
    client.force_login(other_specialist)
    body = _body(client.get(reverse("matters:matter_documents", kwargs={"pk": matter.pk})))

    assert "selgitused.pdf" not in body
    assert "Liikmesettevõtte selgitused" not in body


def test_the_upload_form_asks_for_the_role_before_committing(signed_in, specialist):
    matter = factories.MatterFactory(owner=specialist)

    body = _body(signed_in.get(reverse("matters:matter_documents", kwargs={"pk": matter.pk})))

    action = reverse("documents:upload_evidence", kwargs={"matter_id": matter.pk})
    upload_form = body[body.index(action) : body.index("Salvesta dokument")]
    assert 'name="upload"' in upload_form
    assert 'name="role"' in upload_form


def test_an_attachment_role_chosen_in_the_composer_is_what_is_stored(normal_matter, specialist):
    result = compose_update(
        matter=normal_matter,
        author=specialist,
        body="<p>Ministeeriumi eelnõu.</p>",
        attachment=_upload("eelnou.pdf"),
        attachment_role=DocumentRole.INCOMING_AUTHORITY,
    )

    assert result.document.role == DocumentRole.INCOMING_AUTHORITY


# ---------------------------------------------------------------------------
# §24, §39 — a low-data Matter, and the cost of a large one
# ---------------------------------------------------------------------------


def test_a_low_data_matter_renders_no_empty_sections(signed_in, specialist):
    matter = factories.MatterFactory(owner=specialist, response_deadline=None, brief_summary="")

    body = _detail(signed_in, matter)

    for absence in (
        "Olulisi tähtaegu pole lisatud.",
        "Jõustumise infot pole lisatud.",
        "Töövõite ega kandidaate pole lisatud.",
        "Saabunud materjalid",
    ):
        assert absence not in body
    # And the prompts that replace them are one line each.
    assert "Mida see teema ettevõtjatele tähendab?" in body
    assert "Kaasamist ei ole kirja pandud" in body


def test_the_matter_page_does_not_explode_into_queries(
    signed_in, django_assert_max_num_queries, specialist, organisation
):
    """A page that answers "what is this" must not cost a query per fact."""
    matter = factories.MatterFactory(owner=specialist, source_organisations=[organisation])
    for index in range(12):
        compose_update(matter=matter, author=specialist, body=f"<p>Kirje {index}</p>")
    for index in range(5):
        add_engagement(
            matter=matter,
            kind=EngagementKind.SURVEY,
            title=f"Küsitlus {index}",
            actor=specialist,
        )

    with django_assert_max_num_queries(60):
        signed_in.get(reverse("matters:matter_detail", kwargs={"pk": matter.pk}))
