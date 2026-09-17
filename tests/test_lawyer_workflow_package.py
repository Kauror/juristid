"""One lawyer workflow, from `Teema` to the next `Koja arvamus` (docs/adr/0091).

The five workflow items from the first lawyer test, and the rules a screenshot
cannot show:

* `Koostan arvamuse` is created from a date the person typed, **never** from one
  the application picked; a blank box creates nothing; a refused save leaves no
  orphan step and keeps the date on the form; a retry leaves exactly one; and a
  page answered with both first-step boxes is refused rather than silently losing
  half of what somebody wrote (§1);
* a new `Kaasamine` opens no wait unless somebody asks for one, and every
  historical #227 wait keeps its deadline, its work item and its completion (§2);
* `Meile saadetud tagasiside` and `Teiste arvamus` are one record told apart by an
  explicit `provenance`, never by a linked round, a named organisation, a URL or a
  file; aggregate feedback needs no invented organisation; `LEGACY` is what the
  existing corpus says and nothing is backfilled (§3);
* the lawyer's own note is never the source's words — not in the record, not on
  the chronology row, not in the audit payload and not in the search projection
  (§4);
* `Menetluse areng` is an `Entry` that may carry a stage and a step, atomically
  (§5);
* `Koja arvamus` is a `Submission` through the service `Dokumendid` already posts
  to, several per Matter, with recipients that are not the `Saatja` (§6);
* and none of it leaks across the visibility boundary (§10).
"""

from __future__ import annotations

import datetime as dt

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError, connection, transaction
from django.urls import reverse
from django.utils import timezone

from app.audit.enums import ChangeEventType
from app.audit.models import ChangeEvent
from app.core.enums import Visibility
from app.core.errors import DomainError
from app.documents.enums import DocumentRole
from app.documents.links import DocumentLink
from app.documents.models import Document
from app.matters import work_items
from app.matters.enums import EngagementKind, ExternalPositionProvenance
from app.matters.models import (
    EXTERNAL_POSITION_LEGACY_HEADLINE,
    Entry,
    MatterExternalPosition,
    MatterProceduralDevelopment,
)
from app.matters.services import (
    DEVELOPMENT_NEEDS_TITLE,
    EXTERNAL_POSITION_LABEL_IS_RECEIVED_ONLY,
    EXTERNAL_POSITION_NEEDS_AUTHOR_OR_LABEL,
    EXTERNAL_POSITION_NEEDS_ORGANISATION,
    EXTERNAL_POSITION_NEEDS_SOURCE,
    EXTERNAL_POSITION_PROVENANCE_NOT_SELECTABLE,
    ProceduralDevelopmentConflict,
    add_engagement,
    close_matter,
    correct_external_position,
    correct_procedural_development,
    development_revision,
    external_position_revision,
)
from app.matters.timeline import (
    DEVELOPMENT_DATE_UNKNOWN,
    LAWYER_NOTE_LABEL,
    development_milestone,
    external_position_milestone,
    matter_timeline,
)
from app.matters.views import TWO_FIRST_STEPS_REFUSAL
from app.matters.workspace import (
    add_matter_external_position,
    add_matter_koda_opinion,
    add_procedural_development,
)
from app.search.indexing import rebuild_all
from app.search.models import SearchDocument
from app.submissions.enums import SentAtPrecision, SubmissionStatus
from app.submissions.models import Submission
from app.workflow.enums import ActionKind, ActionStatus, DatePrecision, DateSemantics
from app.workflow.models import NextAction
from app.workflow.services import (
    OPINION_PREPARATION_TEXT,
    establish_opinion_preparation_action,
)
from tests import factories

pytestmark = pytest.mark.django_db

PREPARE_BY = dt.date(2026, 9, 25)
ENGAGED_ON = dt.date(2026, 9, 19)
SENT_ON = dt.date(2026, 9, 17)


def _pdf(name: str = "arvamus.pdf", body: bytes = b"%PDF-1.4 sisu") -> SimpleUploadedFile:
    return SimpleUploadedFile(name, body, content_type="application/pdf")


def _xlsx(name: str = "Kaasamise vastused.xlsx") -> SimpleUploadedFile:
    return SimpleUploadedFile(
        name,
        b"PK\x03\x04 vastused",
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


def _events(matter, event_type):
    return ChangeEvent.objects.filter(matter=matter, event_type=event_type)


def _estonian(day: dt.date) -> str:
    """The day as `EstonianDateInput` writes it — unpadded, which is the trap.

    The widget renders `17.9.2026` and `%d.%m.%Y` produces `17.09.2026`. Both are
    accepted on the way *in*, so a test that spelled the padded form would pass on
    nine days out of ten and fail for the rest of the month — the shape of an
    assertion that is right about the rule and wrong about the string. The
    repository's other date tests build it exactly this way.
    """
    return f"{day.day}.{day.month}.{day.year}"


def _rendered_value(body: str, name: str) -> str:
    """What one named input on the page is actually holding.

    Parsed out of the tag rather than matched as a hand-written attribute string:
    `value` is not always beside `name`, the order is Django's to change, and a
    test that asserted on the order would fail for a reason that is not the rule
    it is about.

    Returns `""` for a box with no `value` at all, which is the same answer as an
    empty one — and is exactly what an undefaulted date box renders as.
    """
    import re

    match = re.search(rf'<input[^>]*name="{re.escape(name)}"[^>]*>', body)
    assert match is not None, f"no input named {name} on the page"
    value = re.search(r'value="([^"]*)"', match.group(0))
    return value.group(1) if value else ""


@pytest.fixture
def ministry(db):
    return factories.OrganisationFactory(name="Majandus- ja Kommunikatsiooniministeerium")


@pytest.fixture
def association(db):
    return factories.OrganisationFactory(name="Metallitööstuse Liit")


@pytest.fixture
def committee(db):
    return factories.OrganisationFactory(name="Riigikogu majanduskomisjon")


def _received(matter, actor, **kwargs):
    """`+ Meile saadetud tagasiside`, through the door a person uses."""
    kwargs.setdefault("organisation", None)
    return add_matter_external_position(
        matter=matter,
        author=actor,
        provenance=ExternalPositionProvenance.RECEIVED.value,
        **kwargs,
    ).record


def _discovered(matter, actor, organisation, **kwargs):
    """`+ Teiste arvamus`, through the door a person uses."""
    return add_matter_external_position(
        matter=matter,
        author=actor,
        organisation=organisation,
        provenance=ExternalPositionProvenance.DISCOVERED.value,
        **kwargs,
    ).record


# ---------------------------------------------------------------------------
# §1 — `Koostan arvamuse`
# ---------------------------------------------------------------------------


def test_the_initial_action_is_created_from_the_supplied_date(normal_matter, specialist):
    action = establish_opinion_preparation_action(
        matter=normal_matter, prepare_by=PREPARE_BY, actor=specialist
    )

    assert action.text == OPINION_PREPARATION_TEXT
    assert action.target_date == PREPARE_BY
    assert action.status == ActionStatus.OPEN
    # An ordinary step, and the combination ADR 0052 §3 says a native one always
    # has: the date means «the day this gets done».
    assert action.kind == ActionKind.DO
    assert action.date_semantics == DateSemantics.DEADLINE
    assert action.date_precision == DatePrecision.EXACT
    # Owned by whoever carries the file, through the ordinary default.
    assert action.responsible_id == normal_matter.owner_id
    assert action.created_by_id == specialist.pk


def test_the_initial_action_refuses_to_invent_a_date(normal_matter, specialist):
    """No date, no step. Not today, not +7, and not an undated commitment."""
    with pytest.raises(DomainError):
        establish_opinion_preparation_action(
            matter=normal_matter, prepare_by=None, actor=specialist
        )

    assert not NextAction.objects.filter(matter=normal_matter).exists()


def test_the_initial_action_is_idempotent_against_an_equivalent_step(normal_matter, specialist):
    """A retried request leaves one step, one history and one audit row."""
    first = establish_opinion_preparation_action(
        matter=normal_matter, prepare_by=PREPARE_BY, actor=specialist
    )
    again = establish_opinion_preparation_action(
        matter=normal_matter, prepare_by=PREPARE_BY, actor=specialist
    )

    assert again.pk == first.pk
    assert NextAction.objects.filter(matter=normal_matter).count() == 1
    # Not superseded-and-replaced either: the first step is untouched, so the
    # chronology does not show somebody changing their mind about nothing.
    first.refresh_from_db()
    assert first.status == ActionStatus.OPEN
    assert first.replaced_by_id is None
    assert _events(normal_matter, ChangeEventType.NEXT_ACTION_SET).count() == 1


def test_a_different_date_is_a_real_replacement(normal_matter, specialist):
    """Moving the day is changing the plan, and the history says so."""
    first = establish_opinion_preparation_action(
        matter=normal_matter, prepare_by=PREPARE_BY, actor=specialist
    )
    second = establish_opinion_preparation_action(
        matter=normal_matter, prepare_by=PREPARE_BY + dt.timedelta(days=3), actor=specialist
    )

    assert second.pk != first.pk
    first.refresh_from_db()
    assert first.status == ActionStatus.SUPERSEDED
    assert first.replaced_by_id == second.pk
    # Still exactly one open step, which is the invariant Minu asjad rests on.
    assert NextAction.objects.filter(matter=normal_matter, status=ActionStatus.OPEN).count() == 1


def test_only_one_open_step_can_exist_even_if_a_caller_tries(normal_matter, specialist):
    """The database, not just the service: `workflow_one_open_action_per_matter`."""
    establish_opinion_preparation_action(
        matter=normal_matter, prepare_by=PREPARE_BY, actor=specialist
    )
    with pytest.raises(IntegrityError), transaction.atomic():
        NextAction.objects.create(
            matter=normal_matter,
            text=OPINION_PREPARATION_TEXT,
            target_date=PREPARE_BY,
            status=ActionStatus.OPEN,
        )
    connection.close()


def test_the_initial_action_reaches_the_owners_work_surfaces(normal_matter, specialist):
    establish_opinion_preparation_action(
        matter=normal_matter, prepare_by=PREPARE_BY, actor=specialist
    )

    items = work_items.work_items(specialist, responsible=specialist)
    assert any(item.text == OPINION_PREPARATION_TEXT for item in items)


def test_a_restricted_step_is_not_visible_to_a_reader_who_may_not_see_it(
    restricted_matter, specialist, reader
):
    """The ordinary child-visibility rule, asserted on the new writer."""
    establish_opinion_preparation_action(
        matter=restricted_matter, prepare_by=PREPARE_BY, actor=specialist
    )

    assert not NextAction.objects.visible_to(reader).filter(matter=restricted_matter).exists()
    assert NextAction.objects.visible_to(specialist).filter(matter=restricted_matter).exists()


# ---------------------------------------------------------------------------
# §1.3 — the creation page
# ---------------------------------------------------------------------------


def _create_payload(**extra):
    payload = {
        "title": "Pakendiseaduse muutmise eelnõu",
        "received_date": "17.09.2026",
    }
    payload.update(extra)
    return payload


def test_uus_teema_creates_the_initial_action_from_the_date_box(client, specialist):
    client.force_login(specialist)
    response = client.post(
        reverse("matters:matter_create"),
        _create_payload(**{"owner": str(specialist.pk), "arvamus-prepare_by": "25.09.2026"}),
    )

    assert response.status_code == 302
    action = NextAction.objects.get(status=ActionStatus.OPEN)
    assert action.text == OPINION_PREPARATION_TEXT
    assert action.target_date == PREPARE_BY
    assert action.responsible_id == specialist.pk


def test_uus_teema_creates_nothing_when_the_date_box_is_empty(client, specialist):
    client.force_login(specialist)
    response = client.post(
        reverse("matters:matter_create"), _create_payload(**{"arvamus-prepare_by": ""})
    )

    assert response.status_code == 302
    assert not NextAction.objects.exists()


def test_a_refused_create_leaves_no_step_and_keeps_the_typed_date(client, specialist):
    """Scenario H: the Matter is refused, so the step must not exist either."""
    client.force_login(specialist)
    response = client.post(
        reverse("matters:matter_create"),
        # No title: the one refusal `MatterCreateForm` makes on its own.
        {"title": "", "arvamus-prepare_by": "25.09.2026"},
    )

    assert response.status_code == 400
    assert not NextAction.objects.exists()
    from app.matters.models import Matter

    assert not Matter.objects.exists()
    # And the date is still in the box, because a browser cannot retype it for
    # somebody.
    assert "25.09.2026" in response.content.decode()


def test_correcting_the_refusal_and_saving_once_leaves_exactly_one_step(client, specialist):
    client.force_login(specialist)
    client.post(reverse("matters:matter_create"), {"title": "", "arvamus-prepare_by": "25.09.2026"})
    response = client.post(
        reverse("matters:matter_create"), _create_payload(**{"arvamus-prepare_by": "25.09.2026"})
    )

    assert response.status_code == 302
    assert NextAction.objects.count() == 1


def test_answering_both_first_step_boxes_is_refused_and_writes_nothing(client, specialist):
    """One open step per Matter, so one of the two may be answered."""
    client.force_login(specialist)
    response = client.post(
        reverse("matters:matter_create"),
        _create_payload(
            **{
                "arvamus-prepare_by": "25.09.2026",
                "next-text": "Vaatan eelnõu läbi",
                "next-target_date": "20.09.2026",
            }
        ),
    )

    assert response.status_code == 400
    assert TWO_FIRST_STEPS_REFUSAL in response.content.decode()
    from app.matters.models import Matter

    assert not Matter.objects.exists()
    assert not NextAction.objects.exists()


def test_the_free_text_first_step_still_works_on_its_own(client, specialist):
    """The regression the refusal above must not have caused."""
    client.force_login(specialist)
    response = client.post(
        reverse("matters:matter_create"),
        _create_payload(**{"next-text": "Vaatan eelnõu läbi", "next-target_date": "20.09.2026"}),
    )

    assert response.status_code == 302
    action = NextAction.objects.get()
    assert action.text == "Vaatan eelnõu läbi"


def test_the_date_box_is_empty_on_a_fresh_form(client, specialist):
    """No default, so nothing on this page proposes a commitment.

    Read out of the rendered tag rather than asserted as the *absence* of a
    hand-written attribute string: a `not in` over a string the page never
    contains in any state is an assertion that cannot fail, which is the one
    shape a guard must not have.
    """
    client.force_login(specialist)
    body = client.get(reverse("matters:matter_create")).content.decode()

    assert "Koostan arvamuse" in body
    assert _rendered_value(body, "arvamus-prepare_by") == ""
    # The `Saabus` box legitimately holds today, which is what makes the line
    # above a real measurement rather than a page with no dates on it at all.
    assert _rendered_value(body, "received_date") == _estonian(timezone.localdate())


# ---------------------------------------------------------------------------
# §2 — a new `Kaasamine` asks for its wait
# ---------------------------------------------------------------------------


def test_the_reply_by_box_opens_empty(client, specialist, normal_matter):
    """The whole of §2: the wait is asked for, not given.

    Asserted on the form's own `initial` and on the *rendered* box, because those
    are two different claims: the first is the contract and the second is what a
    lawyer sees. The rendered check reads the box's `value` out of the page with a
    regex rather than matching a hand-written attribute string, because attribute
    order is Django's to change and says nothing about the rule.
    """
    from app.matters.forms import CompactEngagementForm

    assert CompactEngagementForm().fields["feedback_deadline"].initial is None

    client.force_login(specialist)
    body = client.get(
        reverse("matters:matter_detail", kwargs={"pk": normal_matter.pk})
    ).content.decode()

    assert "Tagasisidet ootame kuni" in body
    assert _rendered_value(body, "feedback_deadline") == ""


def test_the_engagement_date_still_opens_on_today(client, specialist, normal_matter):
    """Narrowed for the reply-by date only. `Kaasamise kuupäev` is unchanged."""
    from app.matters.forms import CompactEngagementForm

    assert CompactEngagementForm().fields["occurred_on"].initial is timezone.localdate

    client.force_login(specialist)
    body = client.get(
        reverse("matters:matter_detail", kwargs={"pk": normal_matter.pk})
    ).content.decode()

    assert _rendered_value(body, "occurred_on") == _estonian(timezone.localdate())


def test_a_new_engagement_creates_no_waiting_work_item(client, specialist, normal_matter):
    client.force_login(specialist)
    response = client.post(
        reverse("matters:add_engagement_compact", kwargs={"pk": normal_matter.pk}),
        {"audience": "234 tööstusettevõtet", "occurred_on": "19.09.2026"},
    )

    assert response.status_code == 200
    engagement = normal_matter.engagements.get()
    assert engagement.feedback_deadline is None
    items = work_items.work_items(specialist, responsible=specialist)
    assert not any(item.matter.pk == normal_matter.pk for item in items)


def test_a_deliberately_set_deadline_still_opens_the_wait(client, specialist, normal_matter):
    """ADR 0086 §3 is narrowed on the default and on nothing else."""
    client.force_login(specialist)
    client.post(
        reverse("matters:add_engagement_compact", kwargs={"pk": normal_matter.pk}),
        {
            "audience": "liikmed",
            "occurred_on": "19.09.2026",
            "feedback_deadline": "30.09.2026",
        },
    )

    engagement = normal_matter.engagements.get()
    assert engagement.feedback_deadline == dt.date(2026, 9, 30)
    items = work_items.work_items(specialist, responsible=specialist)
    assert any(item.matter.pk == normal_matter.pk for item in items)


def test_a_historical_wait_is_preserved_readable_and_completable(client, specialist, normal_matter):
    """Scenario C. An existing #227 round is untouched by this round."""
    engagement = add_engagement(
        matter=normal_matter,
        kind=EngagementKind.OTHER,
        title="liikmed",
        occurred_on=dt.date(2026, 9, 1),
        feedback_deadline=dt.date(2026, 9, 22),
        actor=specialist,
    )

    # It still draws its wait.
    items = work_items.work_items(specialist, responsible=specialist)
    assert any(item.matter.pk == normal_matter.pk for item in items)

    # And it is still completable through the route that completes one.
    client.force_login(specialist)
    from app.matters.services import engagement_revision_token

    response = client.post(
        reverse(
            "matters:complete_engagement_feedback",
            kwargs={"pk": normal_matter.pk, "engagement_id": engagement.pk},
        ),
        {
            "feedback_received": "Liikmed toetasid.",
            "revision": engagement_revision_token(engagement),
        },
    )
    assert response.status_code == 200
    engagement.refresh_from_db()
    assert engagement.feedback_closed_at is not None
    assert engagement.feedback_deadline == dt.date(2026, 9, 22)


def test_an_unrelated_correction_does_not_erase_a_historical_deadline(specialist, normal_matter):
    """`update_engagement`'s `_UNSET` sentinel, asserted against §2's promise."""
    from app.matters.services import engagement_revision_token, update_engagement

    engagement = add_engagement(
        matter=normal_matter,
        kind=EngagementKind.OTHER,
        title="liikmed",
        occurred_on=dt.date(2026, 9, 1),
        feedback_deadline=dt.date(2026, 9, 22),
        actor=specialist,
    )
    update_engagement(
        engagement=engagement,
        title="liikmed ja töögrupp",
        actor=specialist,
        expected_revision=engagement_revision_token(engagement),
    )

    engagement.refresh_from_db()
    assert engagement.title == "liikmed ja töögrupp"
    assert engagement.feedback_deadline == dt.date(2026, 9, 22)


# ---------------------------------------------------------------------------
# §3 — provenance
# ---------------------------------------------------------------------------


def test_received_feedback_records_its_provenance(normal_matter, specialist, association):
    position = _received(
        normal_matter, specialist, organisation=association, summary="Toetame eelnõu."
    )

    assert position.provenance == ExternalPositionProvenance.RECEIVED
    assert position.is_received is True
    assert position.author_label == "Metallitööstuse Liit"
    assert position.kind_label == "Meile saadetud tagasiside"


def test_a_discovered_opinion_records_its_provenance(normal_matter, specialist, ministry):
    position = _discovered(
        normal_matter, specialist, ministry, summary="Toetab varianti B.", stated_on=SENT_ON
    )

    assert position.provenance == ExternalPositionProvenance.DISCOVERED
    assert position.is_received is False
    assert position.kind_label == "Teiste arvamus"


def test_aggregate_feedback_needs_no_organisation(normal_matter, specialist, evidence_root):
    """Scenario A. No invented «234 ettevõtet» and no arbitrary respondent."""
    position = _received(
        normal_matter,
        specialist,
        source_label="Tööstusettevõtete küsitlus",
        uploads=[_xlsx()],
    )

    assert position.organisation_id is None
    assert position.source_label == "Tööstusettevõtete küsitlus"
    assert position.author_label == "Tööstusettevõtete küsitlus"
    # File-first: the spreadsheet is the whole substantive source.
    assert position.summary == ""
    assert position.url == ""
    assert position.document_links.count() == 1


def test_a_file_only_record_is_named_by_its_filename(normal_matter, specialist, evidence_root):
    """§9 of the brief: the file's own name, never an opaque identifier."""
    position = _received(
        normal_matter,
        specialist,
        source_label="Küsitlus",
        uploads=[_xlsx("Kaasamise vastused.xlsx")],
    )

    document = Document.objects.get(links__external_position=position)
    assert document.title == "Kaasamise vastused.xlsx"
    assert document.current_version.original_filename == "Kaasamise vastused.xlsx"


def test_received_feedback_with_neither_author_nor_label_is_refused(normal_matter, specialist):
    with pytest.raises(DomainError) as refusal:
        _received(normal_matter, specialist, summary="Keegi ütles midagi.")

    assert str(refusal.value) == EXTERNAL_POSITION_NEEDS_AUTHOR_OR_LABEL
    assert not MatterExternalPosition.objects.filter(matter=normal_matter).exists()


def test_a_discovered_opinion_without_an_organisation_is_refused(normal_matter, specialist):
    with pytest.raises(DomainError) as refusal:
        add_matter_external_position(
            matter=normal_matter,
            author=specialist,
            organisation=None,
            provenance=ExternalPositionProvenance.DISCOVERED.value,
            summary="Keegi arvab midagi.",
        )

    assert str(refusal.value) == EXTERNAL_POSITION_NEEDS_ORGANISATION


def test_a_discovered_opinion_may_not_borrow_the_source_label(normal_matter, specialist, ministry):
    """`Allikas` is a received-feedback column, not a ninth way to name a body."""
    with pytest.raises(DomainError) as refusal:
        _discovered(
            normal_matter,
            specialist,
            ministry,
            summary="Toetab.",
            source_label="Avalik konsultatsioon",
        )

    assert str(refusal.value) == EXTERNAL_POSITION_LABEL_IS_RECEIVED_ONLY


def test_legacy_is_refused_as_an_answer(normal_matter, specialist, ministry):
    """No new record may be filed as unspecified, whatever a POST claims."""
    with pytest.raises(DomainError) as refusal:
        add_matter_external_position(
            matter=normal_matter,
            author=specialist,
            organisation=ministry,
            provenance=ExternalPositionProvenance.LEGACY.value,
            summary="Toetab.",
        )

    assert str(refusal.value) == EXTERNAL_POSITION_PROVENANCE_NOT_SELECTABLE


def test_the_database_refuses_an_unauthored_row(normal_matter, ministry):
    """`matters_external_position_author_or_label`, under the service."""
    with pytest.raises(IntegrityError), transaction.atomic():
        MatterExternalPosition.objects.create(
            matter=normal_matter,
            organisation=None,
            provenance=ExternalPositionProvenance.RECEIVED,
            summary="Midagi.",
        )
    connection.close()


def test_the_database_refuses_a_label_on_a_discovered_row(normal_matter, ministry):
    """`matters_external_position_label_is_received`."""
    with pytest.raises(IntegrityError), transaction.atomic():
        MatterExternalPosition.objects.create(
            matter=normal_matter,
            organisation=ministry,
            provenance=ExternalPositionProvenance.DISCOVERED,
            source_label="Küsitlus",
            summary="Midagi.",
        )
    connection.close()


def test_a_legacy_row_reads_under_its_original_heading(normal_matter, ministry):
    """§3.4. Nothing about the existing corpus changed, so nothing about it reads
    differently — and it is emphatically not headed «Täpsustamata»."""
    position = MatterExternalPosition.objects.create(
        matter=normal_matter,
        organisation=ministry,
        summary="Toetab eelnõu.",
    )

    assert position.provenance == ExternalPositionProvenance.LEGACY
    assert position.is_received is False
    assert position.kind_label == EXTERNAL_POSITION_LEGACY_HEADLINE
    assert external_position_milestone(position).what.startswith("Väline seisukoht: ")


def test_a_legacy_row_keeps_its_provenance_through_a_correction(
    normal_matter, specialist, ministry, association
):
    """The `None` sentinel: a correction that did not ask does not answer."""
    position = MatterExternalPosition.objects.create(
        matter=normal_matter, organisation=ministry, summary="Toetab."
    )
    corrected = correct_external_position(
        position=position,
        organisation=association,
        url="",
        stated_on=None,
        stated_on_precision=DatePrecision.EXACT.value,
        summary="Toetab osaliselt.",
        lawyer_note="",
        source_label="",
        provenance=None,
        engagement=None,
        actor=specialist,
        expected_revision=external_position_revision(position),
    )

    assert corrected.provenance == ExternalPositionProvenance.LEGACY
    assert corrected.organisation_id == association.pk


def test_provenance_is_not_inferred_from_a_linked_round(normal_matter, specialist, ministry):
    """§3.2. A ministry answering a round Koda ran is still a discovered opinion
    when that is what the person filed it as."""
    engagement = add_engagement(
        matter=normal_matter,
        kind=EngagementKind.OTHER,
        title="liikmed",
        occurred_on=ENGAGED_ON,
        actor=specialist,
    )
    position = _discovered(
        normal_matter, specialist, ministry, summary="Toetab.", engagement=engagement
    )

    assert position.engagement_id == engagement.pk
    assert position.provenance == ExternalPositionProvenance.DISCOVERED


def test_received_feedback_needs_no_engagement(normal_matter, specialist, association):
    """Scenario F. Unsolicited feedback is recordable with no round behind it."""
    position = _received(
        normal_matter, specialist, organisation=association, summary="Kirjutasime omal algatusel."
    )

    assert position.engagement_id is None


def test_feedback_may_carry_no_date_at_all(normal_matter, specialist, association, evidence_root):
    """Scenario D. Nothing substitutes the upload day, `created_at` or today."""
    position = _received(
        normal_matter, specialist, organisation=association, uploads=[_pdf("vastus.pdf")]
    )

    assert position.stated_on is None
    assert position.stated_on_precision == DatePrecision.EXACT
    # The audit timestamp exists and is internal; it is not the business date.
    assert position.created_at is not None
    assert external_position_milestone(position).display_date == "Kuupäev teadmata"


def test_the_two_provenances_are_recorded_in_the_audit_trail(
    normal_matter, specialist, association
):
    _received(normal_matter, specialist, organisation=association, summary="Toetame.")

    event = _events(normal_matter, ChangeEventType.EXTERNAL_POSITION_RECORDED).get()
    assert event.payload["provenance"] == ExternalPositionProvenance.RECEIVED.value
    assert event.payload["has_source_label"] is False
    assert event.summary == "Metallitööstuse Liit"


def test_an_aggregate_records_its_label_as_the_event_summary(
    normal_matter, specialist, evidence_root
):
    _received(normal_matter, specialist, source_label="Küsitlus", uploads=[_xlsx()])

    event = _events(normal_matter, ChangeEventType.EXTERNAL_POSITION_RECORDED).get()
    assert event.summary == "Küsitlus"
    assert event.payload["organisation"] is None
    assert event.payload["has_source_label"] is True


def test_moving_the_provenance_is_recorded_when_a_caller_does_it(
    normal_matter, specialist, ministry
):
    """Not reachable from the correction form, and audited for the callers that can."""
    position = _discovered(normal_matter, specialist, ministry, summary="Toetab.")
    correct_external_position(
        position=position,
        organisation=ministry,
        url="",
        stated_on=None,
        stated_on_precision=DatePrecision.EXACT.value,
        summary="Toetab.",
        lawyer_note="",
        source_label="",
        provenance=ExternalPositionProvenance.RECEIVED.value,
        engagement=None,
        actor=specialist,
        expected_revision=external_position_revision(position),
    )

    event = _events(normal_matter, ChangeEventType.EXTERNAL_POSITION_CORRECTED).get()
    assert event.payload["provenance_from"] == ExternalPositionProvenance.DISCOVERED.value
    assert event.payload["provenance_to"] == ExternalPositionProvenance.RECEIVED.value


# ---------------------------------------------------------------------------
# §4 — the lawyer's note is not the source's words
# ---------------------------------------------------------------------------


def test_the_lawyer_note_is_stored_apart_from_the_position(normal_matter, specialist, ministry):
    position = _discovered(
        normal_matter,
        specialist,
        ministry,
        summary="Toetab varianti B.",
        lawyer_note="Nende põhjendus ei arvesta liikmete kulumõjuga.",
    )

    assert position.summary == "Toetab varianti B."
    assert position.lawyer_note == "Nende põhjendus ei arvesta liikmete kulumõjuga."
    assert "kulumõjuga" not in position.summary


def test_the_chronology_renders_the_note_on_its_own_labelled_line(
    normal_matter, specialist, ministry
):
    """Scenario E. The two are distinguishable, and the label is what does it."""
    position = _discovered(
        normal_matter,
        specialist,
        ministry,
        summary="Toetab varianti B.",
        lawyer_note="Nende põhjendus ei arvesta liikmete kulumõjuga.",
    )
    milestone = external_position_milestone(position)

    assert milestone.sub == "Toetab varianti B."
    assert milestone.own_note == "Nende põhjendus ei arvesta liikmete kulumõjuga."
    assert milestone.own_note_label == LAWYER_NOTE_LABEL
    # Never concatenated into the source's own line.
    assert "kulumõjuga" not in milestone.sub
    assert "kulumõjuga" not in milestone.what


def test_the_teema_page_prints_the_note_under_its_label(
    client, specialist, normal_matter, ministry
):
    _discovered(
        normal_matter,
        specialist,
        ministry,
        summary="Toetab varianti B.",
        lawyer_note="Nende põhjendus ei arvesta liikmete kulumõjuga.",
    )
    client.force_login(specialist)
    body = client.get(
        reverse("matters:matter_detail", kwargs={"pk": normal_matter.pk})
    ).content.decode()

    assert LAWYER_NOTE_LABEL in body
    assert "Nende põhjendus ei arvesta liikmete kulumõjuga." in body


def test_the_note_is_not_a_source(normal_matter, specialist, association):
    """A record of Koda's opinion of something nobody can read is nothing."""
    with pytest.raises(DomainError) as refusal:
        _received(
            normal_matter,
            specialist,
            organisation=association,
            lawyer_note="Nende seisukoht on nõrk.",
        )

    assert str(refusal.value) == EXTERNAL_POSITION_NEEDS_SOURCE


def test_the_audit_payload_records_that_a_note_exists_and_never_its_text(
    normal_matter, specialist, ministry
):
    _discovered(
        normal_matter,
        specialist,
        ministry,
        summary="Toetab.",
        lawyer_note="Nende põhjendus ei arvesta liikmete kulumõjuga.",
    )

    event = _events(normal_matter, ChangeEventType.EXTERNAL_POSITION_RECORDED).get()
    assert event.payload["has_lawyer_note"] is True
    assert "kulumõjuga" not in str(event.payload)
    assert "kulumõjuga" not in (event.summary or "")


def test_the_note_is_not_projected_into_search(normal_matter, specialist, ministry, evidence_root):
    """§9. It is this office's assessment of a third party, and disclosure is a
    decision rather than a convenience."""
    _discovered(
        normal_matter,
        specialist,
        ministry,
        summary="Toetab varianti B.",
        lawyer_note="Nende põhjendus ei arvesta liikmete kulumõjuga.",
    )
    rebuild_all()

    haystack = " ".join(
        SearchDocument.objects.filter(matter=normal_matter).values_list("body_text", flat=True)
    )
    assert "kulumõjuga" not in haystack


# ---------------------------------------------------------------------------
# §5 — `Menetluse areng`
# ---------------------------------------------------------------------------


def test_a_development_is_a_canonical_record(normal_matter, specialist):
    result = add_procedural_development(
        matter=normal_matter,
        author=specialist,
        title="Ministeerium saatis uue eelnõu versiooni",
        occurred_on=dt.date(2026, 10, 12),
    )

    development = result.record
    assert isinstance(development, MatterProceduralDevelopment)
    assert development.title == "Ministeerium saatis uue eelnõu versiooni"
    assert development.occurred_on == dt.date(2026, 10, 12)
    assert development.occurred_on_precision == DatePrecision.EXACT
    assert development.created_by_id == specialist.pk
    assert result.action is None
    # It is not an `Entry`, and writes none: one act must not become two records
    # that can disagree (docs/adr/0091 §5).
    assert not Entry.objects.filter(matter=normal_matter).exists()


def test_a_development_may_carry_no_date_at_all(normal_matter, specialist):
    """The requirement that retired the `Entry`: the date may honestly be unknown.

    A step learned about from a third party months later has no day anybody could
    defend, and `Entry.occurred_at` is `NOT NULL`.
    """
    development = add_procedural_development(
        matter=normal_matter,
        author=specialist,
        title="Valitsus kiitis eelnõu heaks",
    ).record

    assert development.occurred_on is None
    assert development.occurred_on_precision == DatePrecision.EXACT
    assert development.display_date == ""
    assert development_milestone(development).display_date == DEVELOPMENT_DATE_UNKNOWN


def test_a_development_may_be_dated_to_a_period(normal_matter, specialist):
    development = add_procedural_development(
        matter=normal_matter,
        author=specialist,
        title="Eelnõu jõudis Riigikokku",
        occurred_on=dt.date(2026, 10, 1),
        occurred_on_precision=DatePrecision.MONTH.value,
    ).record

    assert development.has_approximate_date is True
    # Never the anchor: `01.10.2026` is a day nobody named (docs/adr/0079 §2).
    assert "01.10" not in development.display_date
    assert development_milestone(development).display_date == development.display_date


def test_the_database_refuses_a_precision_on_an_undated_development(normal_matter):
    with pytest.raises(IntegrityError), transaction.atomic():
        MatterProceduralDevelopment.objects.create(
            matter=normal_matter,
            title="Midagi juhtus",
            occurred_on=None,
            occurred_on_precision=DatePrecision.MONTH,
        )
    connection.close()


def test_a_development_keeps_the_lawyer_note_out_of_the_event(normal_matter, specialist):
    """The second requirement the `Entry` could not hold: two fields, two authors."""
    development = add_procedural_development(
        matter=normal_matter,
        author=specialist,
        title="Ministeerium saatis uue eelnõu versiooni",
        occurred_on=dt.date(2026, 10, 12),
        note="Uus versioon ei arvesta meie ettepanekut.",
    ).record

    assert development.title == "Ministeerium saatis uue eelnõu versiooni"
    assert development.note == "Uus versioon ei arvesta meie ettepanekut."
    milestone = development_milestone(development)
    assert milestone.what == "Menetluse areng: Ministeerium saatis uue eelnõu versiooni"
    assert milestone.own_note == "Uus versioon ei arvesta meie ettepanekut."
    assert milestone.own_note_label == LAWYER_NOTE_LABEL
    assert "ettepanekut" not in milestone.what

    # And the audit payload says a note exists without holding it.
    event = _events(normal_matter, ChangeEventType.PROCEDURAL_DEVELOPMENT_RECORDED).get()
    assert event.payload["has_note"] is True
    assert "ettepanekut" not in str(event.payload)
    assert event.summary == "Ministeerium saatis uue eelnõu versiooni"


def test_a_development_without_a_title_is_refused(normal_matter, specialist):
    with pytest.raises(DomainError) as refusal:
        add_procedural_development(
            matter=normal_matter, author=specialist, title="   ", occurred_on=dt.date(2026, 10, 12)
        )

    assert str(refusal.value) == DEVELOPMENT_NEEDS_TITLE
    assert not MatterProceduralDevelopment.objects.filter(matter=normal_matter).exists()


def test_a_development_may_carry_its_files(normal_matter, specialist, evidence_root):
    result = add_procedural_development(
        matter=normal_matter,
        author=specialist,
        title="Ministeerium saatis uue eelnõu versiooni",
        occurred_on=dt.date(2026, 10, 12),
        uploads=[_pdf("eelnou_v2.pdf")],
    )

    assert len(result.documents) == 1
    # Through the seventh typed `DocumentLink` column, which is what says these
    # bytes are the evidence for *this* step (docs/adr/0091 §5).
    link = DocumentLink.objects.get(procedural_development=result.record)
    assert link.document.matter_id == normal_matter.pk
    assert link.document.current_version is not None
    assert (
        _events(normal_matter, ChangeEventType.PROCEDURAL_DEVELOPMENT_DOCUMENT_LINKED).count() == 1
    )


def test_a_development_may_move_the_stage_and_set_the_next_step(normal_matter, specialist):
    stage = factories.StageFactory(label_et="Kooskõlastusringil", is_active=True)
    result = add_procedural_development(
        matter=normal_matter,
        author=specialist,
        title="Ministeerium saatis uue eelnõu versiooni",
        occurred_on=dt.date(2026, 10, 12),
        stage=stage,
        next_text="Vaatan uue versiooni läbi",
        next_date=dt.date(2026, 10, 16),
    )

    normal_matter.refresh_from_db()
    assert normal_matter.stage_id == stage.pk
    assert result.action is not None
    assert result.action.text == "Vaatan uue versiooni läbi"
    assert result.action.target_date == dt.date(2026, 10, 16)
    assert _events(normal_matter, ChangeEventType.MATTER_STAGE_CHANGED).count() == 1
    # The stage is **not** copied onto the development: `Matter.stage` is where
    # the file stands, and a second copy is a second thing that can disagree. What
    # ties them together is the operation identifier both writes share.
    assert not hasattr(result.record, "stage_id")


def test_a_development_changes_neither_when_neither_is_named(normal_matter, specialist):
    stage = factories.StageFactory(label_et="Kooskõlastusringil", is_active=True)
    normal_matter.stage = stage
    normal_matter.save(update_fields=["stage"])

    result = add_procedural_development(
        matter=normal_matter,
        author=specialist,
        title="Ministeerium teatas, et eelnõu viibib",
        occurred_on=dt.date(2026, 10, 12),
    )

    normal_matter.refresh_from_db()
    assert normal_matter.stage_id == stage.pk
    assert result.action is None
    assert not NextAction.objects.filter(matter=normal_matter).exists()


def test_a_refused_upload_rolls_back_the_whole_development(
    normal_matter, specialist, evidence_root
):
    """§5.4. No stage moved with the development absent, and no orphan step."""
    from app.documents.uploads import UploadRejected

    stage = factories.StageFactory(label_et="Kooskõlastusringil", is_active=True)
    bad = SimpleUploadedFile("pahavara.exe", b"MZ", content_type="application/x-msdownload")

    with pytest.raises((UploadRejected, DomainError)):
        add_procedural_development(
            matter=normal_matter,
            author=specialist,
            title="Ministeerium saatis uue eelnõu versiooni",
            occurred_on=dt.date(2026, 10, 12),
            stage=stage,
            next_text="Vaatan uue versiooni läbi",
            next_date=dt.date(2026, 10, 16),
            uploads=[bad],
        )

    normal_matter.refresh_from_db()
    assert normal_matter.stage_id != stage.pk
    assert not MatterProceduralDevelopment.objects.filter(matter=normal_matter).exists()
    assert not NextAction.objects.filter(matter=normal_matter).exists()


def test_a_development_is_refused_on_a_closed_matter(normal_matter, specialist):
    from app.workflow.enums import Disposition

    close_matter(matter=normal_matter, disposition=Disposition.RESPONSE_COMPLETE, actor=specialist)

    with pytest.raises(DomainError):
        add_procedural_development(
            matter=normal_matter,
            author=specialist,
            title="Midagi juhtus",
            occurred_on=dt.date(2026, 10, 12),
        )


def test_a_development_is_corrected_not_deleted(normal_matter, specialist):
    development = add_procedural_development(
        matter=normal_matter,
        author=specialist,
        title="Ministeerium saatis uue eelnõu",
        occurred_on=dt.date(2026, 10, 12),
    ).record

    corrected = correct_procedural_development(
        development=development,
        title="Ministeerium saatis uue eelnõu versiooni",
        occurred_on=dt.date(2026, 10, 13),
        occurred_on_precision=DatePrecision.EXACT.value,
        note="Vaatan üle.",
        actor=specialist,
        expected_revision=development_revision(development),
    )

    assert corrected.title == "Ministeerium saatis uue eelnõu versiooni"
    assert corrected.occurred_on == dt.date(2026, 10, 13)
    assert MatterProceduralDevelopment.objects.filter(matter=normal_matter).count() == 1
    event = _events(normal_matter, ChangeEventType.PROCEDURAL_DEVELOPMENT_CORRECTED).get()
    assert "title" in event.payload["fields"]
    assert event.payload["occurred_on_to"] == "2026-10-13"


def test_a_stale_correction_writes_nothing(normal_matter, specialist):
    development = add_procedural_development(
        matter=normal_matter,
        author=specialist,
        title="Ministeerium saatis uue eelnõu",
        occurred_on=dt.date(2026, 10, 12),
    ).record
    stale = development_revision(development)
    correct_procedural_development(
        development=development,
        title="Esimene parandus",
        occurred_on=dt.date(2026, 10, 12),
        occurred_on_precision=DatePrecision.EXACT.value,
        note="",
        actor=specialist,
        expected_revision=stale,
    )

    with pytest.raises(ProceduralDevelopmentConflict):
        correct_procedural_development(
            development=development,
            title="Teine parandus",
            occurred_on=dt.date(2026, 10, 12),
            occurred_on_precision=DatePrecision.EXACT.value,
            note="",
            actor=specialist,
            expected_revision=stale,
        )

    development.refresh_from_db()
    assert development.title == "Esimene parandus"


def test_a_development_reaches_the_chronology(normal_matter, specialist):
    """Dated in the **past**, because the chronology reads newest-first and means
    *past* — `test_a_future_dated_development_is_not_history_yet` is the other
    half, and the two together are why the fixed dates elsewhere in this file are
    safe to leave alone."""
    add_procedural_development(
        matter=normal_matter,
        author=specialist,
        title="Eelnõu jõudis Riigikokku",
        occurred_on=timezone.localdate() - dt.timedelta(days=3),
    )

    items, _ = matter_timeline(matter=normal_matter, user=specialist, limit=50)
    headlines = [item.milestone.what for item in items if item.milestone is not None]
    assert "Menetluse areng: Eelnõu jõudis Riigikokku" in headlines
    # Projected from the record, so the audit events draw no row of their own.
    assert headlines.count("Menetluse areng: Eelnõu jõudis Riigikokku") == 1


def test_a_future_dated_development_is_not_history_yet(normal_matter, specialist):
    """A step somebody expects is not a step that happened.

    The rule `MatterEngagement` and `MatterExternalPosition` both follow, asserted
    here because this record is the one a lawyer is most likely to date forward —
    «the committee sits on the 12th» is a thing they know in advance.
    """
    add_procedural_development(
        matter=normal_matter,
        author=specialist,
        title="Riigikogu komisjon arutab eelnõu",
        occurred_on=timezone.localdate() + dt.timedelta(days=21),
    )

    items, _ = matter_timeline(matter=normal_matter, user=specialist, limit=50)
    headlines = [item.milestone.what for item in items if item.milestone is not None]
    assert not any("Riigikogu komisjon" in headline for headline in headlines)
    # The record is on the file and readable; it is the *chronology* that waits.
    assert MatterProceduralDevelopment.objects.filter(matter=normal_matter).count() == 1


def test_the_development_route_records_everything_in_one_post(client, specialist, normal_matter):
    stage = factories.StageFactory(label_et="Riigikogus", is_active=True)
    client.force_login(specialist)
    response = client.post(
        reverse("matters:add_development", kwargs={"pk": normal_matter.pk}),
        {
            "title": "Eelnõu jõudis Riigikokku",
            "occurred_on": "12.10.2026",
            "areng_precision": "EXACT",
            "stage": str(stage.pk),
            "next_text": "Vaatan uue teksti läbi",
            "next_date": "16.10.2026",
        },
    )

    assert response.status_code == 200
    normal_matter.refresh_from_db()
    assert normal_matter.stage_id == stage.pk
    development = MatterProceduralDevelopment.objects.get(matter=normal_matter)
    assert development.title == "Eelnõu jõudis Riigikokku"
    assert NextAction.objects.get(matter=normal_matter).text == "Vaatan uue teksti läbi"


def test_a_half_filled_next_step_is_refused_on_the_empty_control(client, specialist, normal_matter):
    client.force_login(specialist)
    response = client.post(
        reverse("matters:add_development", kwargs={"pk": normal_matter.pk}),
        {
            "title": "Eelnõu jõudis Riigikokku",
            "occurred_on": "12.10.2026",
            "areng_precision": "EXACT",
            "next_text": "Vaatan uue teksti läbi",
        },
    )

    assert response.status_code == 400
    assert "Vali järgmise tegevuse kuupäev." in response.content.decode()
    assert not MatterProceduralDevelopment.objects.filter(matter=normal_matter).exists()


def test_the_development_route_accepts_an_empty_date(client, specialist, normal_matter):
    """The `Entry` design refused this; the canonical record is what allows it."""
    client.force_login(specialist)
    response = client.post(
        reverse("matters:add_development", kwargs={"pk": normal_matter.pk}),
        {"title": "Valitsus kiitis eelnõu heaks", "occurred_on": "", "areng_precision": "EXACT"},
    )

    assert response.status_code == 200
    assert MatterProceduralDevelopment.objects.get(matter=normal_matter).occurred_on is None


def test_a_restricted_development_does_not_leak(normal_matter, specialist, reader):
    development = add_procedural_development(
        matter=normal_matter,
        author=specialist,
        title="Eelnõu jõudis Riigikokku",
        occurred_on=timezone.localdate() - dt.timedelta(days=3),
        note="Meie ettepanekut ei arvestatud.",
    ).record
    development.visibility_override = Visibility.RESTRICTED
    development.save(update_fields=["visibility_override"])

    assert not MatterProceduralDevelopment.objects.visible_to(reader).exists()
    items, _ = matter_timeline(matter=normal_matter, user=reader, limit=50)
    rendered = " ".join(
        f"{item.milestone.what} {item.milestone.own_note}"
        for item in items
        if item.milestone is not None
    )
    assert "Riigikokku" not in rendered
    assert "ettepanekut" not in rendered


# ---------------------------------------------------------------------------
# §6 — `Koja arvamus`
# ---------------------------------------------------------------------------


def test_a_koda_opinion_is_a_sent_submission_with_its_exact_evidence(
    normal_matter, specialist, ministry, evidence_root
):
    result = add_matter_koda_opinion(
        matter=normal_matter,
        author=specialist,
        upload=_pdf("koja_arvamus.pdf"),
        recipients=[ministry],
        sent_on=dt.date(2026, 9, 29),
        title="Koja arvamus pakendiseaduse eelnõule",
    )

    submission = result.record
    assert submission.status == SubmissionStatus.SENT
    assert submission.sent_at_precision == SentAtPrecision.DATE
    assert timezone.localtime(submission.sent_at).date() == dt.date(2026, 9, 29)
    assert submission.title == "Koja arvamus pakendiseaduse eelnõule"
    assert list(submission.recipients.all()) == [ministry]
    # The exact bytes, under the role the product already has for an opinion.
    document = result.documents[0]
    assert document.role == DocumentRole.KODA_SUBMISSION_FINAL
    assert submission.final_version_id == document.current_version.pk


def test_the_title_falls_back_to_the_filename(normal_matter, specialist, ministry, evidence_root):
    result = add_matter_koda_opinion(
        matter=normal_matter,
        author=specialist,
        upload=_pdf("Koja arvamus.pdf"),
        recipients=[ministry],
        sent_on=dt.date(2026, 9, 29),
    )

    assert result.record.title == "Koja arvamus.pdf"


def test_a_koda_opinion_refuses_to_invent_the_send_date(
    normal_matter, specialist, ministry, evidence_root
):
    with pytest.raises(DomainError):
        add_matter_koda_opinion(
            matter=normal_matter,
            author=specialist,
            upload=_pdf(),
            recipients=[ministry],
            sent_on=None,
        )

    assert not Submission.objects.filter(matter=normal_matter).exists()
    assert not Document.objects.filter(matter=normal_matter).exists()


def test_several_koda_opinions_per_matter(
    normal_matter, specialist, ministry, committee, evidence_root
):
    """Scenario B. No one-opinion limit, and nothing overwritten."""
    first = add_matter_koda_opinion(
        matter=normal_matter,
        author=specialist,
        upload=_pdf("arvamus_1.pdf", b"%PDF-1.4 esimene"),
        recipients=[ministry],
        sent_on=dt.date(2026, 9, 29),
    ).record
    second = add_matter_koda_opinion(
        matter=normal_matter,
        author=specialist,
        upload=_pdf("arvamus_2.pdf", b"%PDF-1.4 teine"),
        recipients=[committee],
        sent_on=dt.date(2026, 11, 3),
    ).record

    assert first.pk != second.pk
    assert (
        Submission.objects.filter(matter=normal_matter, status=SubmissionStatus.SENT).count() == 2
    )
    # The first opinion's evidence is untouched: different documents, different
    # versions, different bytes.
    assert first.final_version_id != second.final_version_id
    first.refresh_from_db()
    assert first.status == SubmissionStatus.SENT
    assert Document.objects.filter(matter=normal_matter).count() == 2


def test_the_recipient_is_not_the_matters_sender(
    normal_matter, specialist, ministry, committee, evidence_root
):
    """§6.3. A later opinion goes where it went, not where the file came from."""
    normal_matter.source_organisations.add(ministry)

    submission = add_matter_koda_opinion(
        matter=normal_matter,
        author=specialist,
        upload=_pdf(),
        recipients=[committee],
        sent_on=dt.date(2026, 11, 3),
    ).record

    assert list(submission.recipients.all()) == [committee]


def test_a_draft_does_not_become_sent_by_a_file_arriving(
    normal_matter, specialist, ministry, evidence_root
):
    """§6.2. Uploading is not asserting; a draft stays a draft."""
    draft = factories.SubmissionFactory(matter=normal_matter, status=SubmissionStatus.DRAFT)

    add_matter_koda_opinion(
        matter=normal_matter,
        author=specialist,
        upload=_pdf(),
        recipients=[ministry],
        sent_on=dt.date(2026, 9, 29),
    )

    draft.refresh_from_db()
    assert draft.status == SubmissionStatus.DRAFT
    assert draft.sent_at is None


def test_a_koda_opinion_is_refused_on_a_closed_matter(
    normal_matter, specialist, ministry, evidence_root
):
    from app.workflow.enums import Disposition

    close_matter(matter=normal_matter, disposition=Disposition.RESPONSE_COMPLETE, actor=specialist)

    with pytest.raises(DomainError):
        add_matter_koda_opinion(
            matter=normal_matter,
            author=specialist,
            upload=_pdf(),
            recipients=[ministry],
            sent_on=dt.date(2026, 9, 29),
        )


def test_the_koda_opinion_route_requires_a_file_a_date_and_an_addressee(
    client, specialist, normal_matter
):
    client.force_login(specialist)
    response = client.post(reverse("matters:add_koda_opinion", kwargs={"pk": normal_matter.pk}), {})

    body = response.content.decode()
    assert response.status_code == 400
    assert "Lisa fail, mis välja saadeti." in body
    assert "Vali vähemalt üks adressaat." in body
    assert not Submission.objects.filter(matter=normal_matter).exists()


def test_the_koda_opinion_route_refuses_a_future_send_date(
    client, specialist, normal_matter, ministry, evidence_root
):
    client.force_login(specialist)
    tomorrow = (timezone.localdate() + dt.timedelta(days=1)).strftime("%d.%m.%Y")
    response = client.post(
        reverse("matters:add_koda_opinion", kwargs={"pk": normal_matter.pk}),
        {
            "upload": _pdf(),
            "recipients": [str(ministry.pk)],
            "sent_on": tomorrow,
        },
    )

    assert response.status_code == 400
    assert "Saatmise kuupäev ei saa olla tulevikus." in response.content.decode()
    assert not Submission.objects.filter(matter=normal_matter).exists()


# ---------------------------------------------------------------------------
# §5.5 — the continuation after a Submission
# ---------------------------------------------------------------------------


def test_a_sent_opinion_with_no_open_step_offers_the_continuation(
    client, specialist, normal_matter, ministry, evidence_root
):
    add_matter_koda_opinion(
        matter=normal_matter,
        author=specialist,
        upload=_pdf(),
        recipients=[ministry],
        sent_on=dt.date(2026, 9, 29),
    )
    client.force_login(specialist)
    body = client.get(
        reverse("matters:matter_detail", kwargs={"pk": normal_matter.pk})
    ).content.decode()

    assert "Menetlus võib jätkuda" in body
    assert "#lisa-menetluse-areng" in body


def test_a_matter_with_no_sent_opinion_offers_nothing_of_the_kind(
    client, specialist, normal_matter
):
    client.force_login(specialist)
    body = client.get(
        reverse("matters:matter_detail", kwargs={"pk": normal_matter.pk})
    ).content.decode()

    assert "Menetlus võib jätkuda" not in body


def test_the_continuation_creates_no_work(
    client, specialist, normal_matter, ministry, evidence_root
):
    """It is a sentence and two anchors. Nothing is proposed and nothing is due."""
    add_matter_koda_opinion(
        matter=normal_matter,
        author=specialist,
        upload=_pdf(),
        recipients=[ministry],
        sent_on=dt.date(2026, 9, 29),
    )

    assert not NextAction.objects.filter(matter=normal_matter).exists()
    items = work_items.work_items(specialist, responsible=specialist)
    assert not any(item.matter.pk == normal_matter.pk for item in items)


def test_a_restricted_submission_puts_no_continuation_on_the_page(
    client, specialist, normal_matter, reader, ministry, evidence_root
):
    """AUTH-003. The sentence is derived from a scoped read, so it is scoped."""
    submission = add_matter_koda_opinion(
        matter=normal_matter,
        author=specialist,
        upload=_pdf(),
        recipients=[ministry],
        sent_on=dt.date(2026, 9, 29),
    ).record
    # Restricting the submission means restricting its evidence first: the
    # database refuses final evidence less restricted than the submission it
    # belongs to, which is ADR 0040's invariant and is not relaxed for this
    # round. Doing it the other way round is what the trigger exists to catch.
    document = Document.objects.get(matter=normal_matter)
    document.visibility_override = Visibility.RESTRICTED
    document.save(update_fields=["visibility_override"])
    submission.visibility_override = Visibility.RESTRICTED
    submission.save(update_fields=["visibility_override"])

    client.force_login(reader)
    body = client.get(
        reverse("matters:matter_detail", kwargs={"pk": normal_matter.pk})
    ).content.decode()

    assert "Menetlus võib jätkuda" not in body


# ---------------------------------------------------------------------------
# §10 — permissions
# ---------------------------------------------------------------------------


def test_a_restricted_matters_feedback_does_not_leak(
    client, restricted_matter, specialist, reader, association
):
    """Scenario G. Nothing about the record reaches a reader who may not see it."""
    _received(
        restricted_matter,
        specialist,
        organisation=association,
        summary="Toetame eelnõu.",
        lawyer_note="Nende arvutus on optimistlik.",
        source_label="",
    )

    assert not MatterExternalPosition.objects.visible_to(reader).exists()
    assert MatterExternalPosition.objects.visible_to(specialist).count() == 1

    client.force_login(reader)
    response = client.get(reverse("matters:matter_detail", kwargs={"pk": restricted_matter.pk}))
    assert response.status_code == 404


def test_a_restricted_child_does_not_leak_through_the_chronology(
    normal_matter, specialist, reader, ministry
):
    """A visible Matter carrying a restricted position shows the reader nothing
    of it — not the organisation, not the position, not the lawyer's note."""
    position = _discovered(
        normal_matter,
        specialist,
        ministry,
        summary="Toetab varianti B.",
        lawyer_note="Nende põhjendus on nõrk.",
    )
    position.visibility_override = Visibility.RESTRICTED
    position.save(update_fields=["visibility_override"])

    items, _ = matter_timeline(matter=normal_matter, user=reader, limit=50)
    rendered = " ".join(
        f"{item.milestone.what} {item.milestone.sub} {item.milestone.own_note}"
        for item in items
        if item.milestone is not None
    )
    assert "Toetab varianti B." not in rendered
    assert "nõrk" not in rendered
    assert ministry.name not in rendered


def test_both_feedback_routes_refuse_a_reader_who_may_not_write(
    client, normal_matter, reader, association
):
    client.force_login(reader)
    for route in ("matters:add_received_feedback", "matters:add_external_position"):
        response = client.post(
            reverse(route, kwargs={"pk": normal_matter.pk}),
            {"organisation": str(association.pk), "summary": "Midagi."},
        )
        assert response.status_code == 404, route
    assert not MatterExternalPosition.objects.exists()


def test_the_koda_opinion_and_development_routes_refuse_a_reader(
    client, normal_matter, reader, ministry
):
    client.force_login(reader)
    for route in ("matters:add_koda_opinion", "matters:add_development"):
        response = client.post(reverse(route, kwargs={"pk": normal_matter.pk}), {})
        assert response.status_code == 404, route
    assert not Submission.objects.exists()
    assert not Entry.objects.exists()
