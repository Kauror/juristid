"""«Majanduspoliitika ülevaade»: which recorded events are proposed (Decision B).

`sources.collect` is the only place an event becomes eligible, so the owner's
rules are asserted against it directly: each real source is included, each
excluded kind is absent, a no-comments answer is not an opinion and an absent
opinion is nothing, the Excel `VÄLJA` date is deduplicated only where the
same send is canonically recorded, and a restricted child is invisible to a
reader who may not see it (docs/adr/0199 §1–§2).
"""

from __future__ import annotations

from datetime import date

import pytest

from app.core.authorization import Scope
from app.core.enums import Visibility
from app.legacy_import.models import ExcelPilotImport
from app.legacy_import.register_semantics import AddresseeCardinality, OpinionSentState
from app.matters.entry_enums import EntryKind
from app.matters.enums import MatterDataClass, WebsiteOverviewKind, WebsiteOverviewStatus
from app.matters.models import MatterExternalPosition, MatterWebsiteOverview
from app.reporting.report_builder import sources
from app.reporting.report_builder.sources import EventType
from app.submissions.enums import SubmissionKind, SubmissionStatus
from tests import factories
from tests.report_builder_support import OCTOBER, at, entry, overview, register, sent

pytestmark = pytest.mark.django_db

START, END = OCTOBER
#: After the period, so every October entry is in the past.
NOW = at(date(2026, 11, 2), 9)


def collect(user, start=START, end=END, now=NOW):
    return sources.collect(user, start, end, now=now)


def kinds(found, matter):
    return [(event.date_label, event.kind_label) for event in found[matter.pk].events]


def test_each_real_source_is_proposed_on_its_own_date(specialist, capture_evidence, organisation):
    opinion = factories.MatterFactory(title="Arvamusega teema")
    meeting = factories.MatterFactory(title="Kohtumisega teema")
    statement = factories.MatterFactory(title="Avaliku kommentaariga teema")
    page = factories.MatterFactory(title="Ülevaatega teema")
    excel = factories.MatterFactory(title="Exceli teema")
    sent(opinion, capture_evidence, day=date(2026, 10, 3), summary="Toetame eelnõu.")
    entry(meeting, day=date(2026, 10, 7), kind=EntryKind.HEARING, organisation=organisation)
    entry(
        statement,
        day=date(2026, 10, 9),
        kind=EntryKind.PUBLIC_STATEMENT,
        body="<p>Intervjuu ERR-ile.</p>",
    )
    overview(page, day=date(2026, 10, 11))
    register(excel, day=date(2026, 10, 13))

    found = collect(specialist)

    assert kinds(found, opinion) == [("3.10.2026", "Ametlik arvamus")]
    assert kinds(found, meeting) == [("7.10.2026", "Istung või kuulamine")]
    assert kinds(found, statement) == [("9.10.2026", "Avalik esinemine või kommentaar")]
    assert kinds(found, page) == [("11.10.2026", sources.OVERVIEW_LABEL)]
    assert kinds(found, excel) == [("13.10.2026", sources.REGISTER_LABEL)]
    assert found[opinion.pk].events[0].source_text == "Toetame eelnõu."
    assert found[excel.pk].events[0].provenance == "leht 2026, rida 14"


def test_the_period_is_inclusive_on_tallinn_days(specialist, capture_evidence):
    matter = factories.MatterFactory()
    entry(matter, day=date(2026, 9, 30), hour=23)
    entry(matter, day=date(2026, 10, 1), hour=0)
    entry(matter, day=date(2026, 10, 31), hour=23)
    entry(matter, day=date(2026, 11, 1), hour=0)

    found = collect(specialist)

    assert [event.date_label for event in found[matter.pk].events] == ["1.10.2026", "31.10.2026"]


def test_a_no_comments_answer_is_labelled_apart_and_an_absent_opinion_is_nothing(
    specialist, capture_evidence
):
    answered = factories.MatterFactory(title="Vastatud")
    silent = factories.MatterFactory(title="Vaikne")
    not_sent = factories.MatterFactory(title="Ei saatnud")
    sent(
        answered, capture_evidence, day=date(2026, 10, 5), kind=SubmissionKind.NO_OBJECTION_RESPONSE
    )
    register(not_sent, day=None, state=OpinionSentState.NOT_SENT)
    register(silent, day=None, state=OpinionSentState.RECORDED_OTHER, row=15)

    found = collect(specialist)

    assert kinds(found, answered) == [("5.10.2026", "Vastus: märkusi ja ettepanekuid ei ole")]
    assert "arvamus" not in found[answered.pk].events[0].kind_label.lower()
    # Nothing is written for anybody: no prefilled «no comments» sentence.
    assert found[answered.pk].events[0].source_text == ""
    assert silent.pk not in found and not_sent.pk not in found


def test_drafts_plans_notes_and_other_peoples_positions_are_never_proposed(
    specialist, capture_evidence, organisation
):
    matter = factories.MatterFactory()
    factories.SubmissionFactory(matter=matter, status=SubmissionStatus.DRAFT, summary="Mustand")
    entry(matter, day=date(2026, 10, 20), kind=EntryKind.NOTE)
    entry(matter, day=date(2026, 10, 21), kind=EntryKind.OTHER)
    planned = entry(matter, day=date(2026, 10, 25), kind=EntryKind.MEETING)
    MatterExternalPosition.objects.create(
        matter=matter, organisation=organisation, provenance="DISCOVERED", summary="Liidu seisukoht"
    )
    overview(
        matter, day=date(2026, 10, 10), kind=WebsiteOverviewKind.NEWS, url="https://news.example/x"
    )
    overview(matter, day=None, url="https://www.koda.ee/et/hetkel-kasil/ilma-kuupaevata")
    MatterWebsiteOverview.objects.create(matter=matter, status=WebsiteOverviewStatus.PLANNED)
    removed = entry(matter, day=date(2026, 10, 12), kind=EntryKind.CALL)
    removed.removed_at = at(date(2026, 10, 13))
    removed.save(update_fields=["removed_at"])

    # Seen from the 22nd, the meeting on the 25th has not happened yet.
    assert matter.pk not in collect(specialist, now=at(date(2026, 10, 22)))
    # Once it has, it is the only thing on the file that counts.
    found = collect(specialist)
    assert [event.ref.id for event in found[matter.pk].events] == [planned.pk]


def test_withdrawn_and_superseded_sends_stay_with_their_later_state_said(
    specialist, capture_evidence
):
    matter = factories.MatterFactory()
    sent(
        matter,
        capture_evidence,
        day=date(2026, 10, 2),
        status=SubmissionStatus.WITHDRAWN,
        title="A",
    )
    sent(
        matter,
        capture_evidence,
        day=date(2026, 10, 4),
        status=SubmissionStatus.SUPERSEDED,
        title="B",
    )

    assert [label for _, label in kinds(collect(specialist), matter)] == [
        "Ametlik arvamus (hiljem tagasi võetud)",
        "Ametlik arvamus (hiljem asendatud)",
    ]


def test_kellele_is_the_addressees_only_and_a_meeting_has_none(specialist, capture_evidence):
    zeta = factories.OrganisationFactory(name="Sotsiaalministeerium")
    alpha = factories.OrganisationFactory(name="Majandus- ja Kommunikatsiooniministeerium")
    copy = factories.OrganisationFactory(name="Riigikogu majanduskomisjon")
    counterpart = factories.OrganisationFactory(name="Kaubanduskoja partner")
    matter = factories.MatterFactory()
    sent(matter, capture_evidence, day=date(2026, 10, 6), addressees=(zeta, alpha), copies=(copy,))
    entry(
        matter,
        day=date(2026, 10, 8),
        organisation=counterpart,
        body="<p>Arutasime <b>eelnõu</b>.</p>",
    )

    send, meeting = collect(specialist)[matter.pk].events

    assert (
        send.recipients_label == "Majandus- ja Kommunikatsiooniministeerium, Sotsiaalministeerium"
    )
    assert meeting.recipients == ()
    assert meeting.source_text == "Kaubanduskoja partner: Arutasime eelnõu."
    assert "<" not in meeting.source_text


def test_several_events_on_one_topic_keep_their_own_date_and_recipient(
    specialist, capture_evidence
):
    first = factories.OrganisationFactory(name="Rahandusministeerium")
    second = factories.OrganisationFactory(name="Justiitsministeerium")
    matter = factories.MatterFactory()
    sent(matter, capture_evidence, day=date(2026, 10, 20), addressees=(second,), title="Teine")
    sent(matter, capture_evidence, day=date(2026, 10, 2), addressees=(first,), title="Esimene")
    entry(matter, day=date(2026, 10, 10), kind=EntryKind.WORKING_GROUP)

    events = collect(specialist)[matter.pk].events

    assert [(e.date_label, e.recipients_label) for e in events] == [
        ("2.10.2026", "Rahandusministeerium"),
        ("10.10.2026", ""),
        ("20.10.2026", "Justiitsministeerium"),
    ]


def test_a_valja_date_on_the_day_of_a_recorded_send_is_the_same_send(specialist, capture_evidence):
    matter = factories.MatterFactory()
    send = sent(matter, capture_evidence, day=date(2026, 10, 14))
    register(matter, day=date(2026, 10, 14))

    assert [event.ref for event in collect(specialist)[matter.pk].events] == [
        sources.EventRef(EventType.SUBMISSION, send.pk)
    ]


def test_the_pilots_placeholder_send_stands_for_its_own_row(specialist, capture_evidence):
    matter = factories.MatterFactory()
    send = sent(matter, capture_evidence, day=date(2026, 9, 1))
    state = register(matter, day=date(2026, 10, 14), sheet="2026", row=33)
    ExcelPilotImport.objects.create(
        matter=matter,
        source_reference=state.source_reference,
        pilot="excel-2026",
        pilot_version="1.1",
        manifest_sha256="2" * 64,
        workbook_sha256="3" * 64,
        source_sheet="2026",
        source_row_number=33,
        source_reference_label="2026/33",
        selection_group="B",
        work_status="ACTIVE",
        submission=send,
    )

    assert matter.pk not in collect(specialist)


def test_a_valja_date_on_another_day_stays_and_is_flagged(specialist, capture_evidence):
    matter = factories.MatterFactory()
    sent(matter, capture_evidence, day=date(2026, 10, 2), title="Koja arvamus eelnõule")
    register(matter, day=date(2026, 10, 16))

    send, valja = collect(specialist)[matter.pk].events

    assert send.ref.type == EventType.SUBMISSION and not send.uncertainty
    assert valja.ref.type == EventType.REGISTER
    assert valja.uncertainty.startswith("Võib korrata arvamust «Koja arvamus eelnõule» (2.10.2026)")


def test_an_unrelated_send_outside_the_period_does_not_suppress_a_distinct_valja_date(
    specialist, capture_evidence
):
    matter = factories.MatterFactory()
    sent(matter, capture_evidence, day=date(2026, 3, 2))
    register(matter, day=date(2026, 10, 16))

    events = collect(specialist)[matter.pk].events

    assert [event.ref.type for event in events] == [EventType.REGISTER]
    assert "2.3.2026" in events[0].uncertainty


def test_valja_kellele_only_when_the_row_and_the_canonical_addressee_agree(specialist):
    ministry = factories.OrganisationFactory(name="Kliimaministeerium")
    agreeing = factories.MatterFactory(addressee_organisation=ministry)
    several = factories.MatterFactory(addressee_organisation=ministry)
    different = factories.MatterFactory(addressee_organisation=ministry)
    register(
        agreeing,
        day=date(2026, 10, 5),
        addressee_raw="Kliimaministeerium",
        cardinality=AddresseeCardinality.SINGLE,
    )
    register(
        several,
        day=date(2026, 10, 5),
        addressee_raw="Kliimaministeerium, Rahandusministeerium",
        cardinality=AddresseeCardinality.MULTIPLE,
    )
    register(
        different,
        day=date(2026, 10, 5),
        addressee_raw="Riigikantselei",
        cardinality=AddresseeCardinality.SINGLE,
    )

    found = collect(specialist)

    assert found[agreeing.pk].events[0].recipients == ("Kliimaministeerium",)
    assert found[several.pk].events[0].recipients == ()
    assert found[different.pk].events[0].recipients == ()


def test_test_data_is_not_reported(specialist, capture_evidence):
    test_matter = factories.MatterFactory(data_class=MatterDataClass.TEST)
    entry(test_matter, day=date(2026, 10, 5))

    assert test_matter.pk not in collect(specialist)


def test_an_administrator_without_a_business_role_is_proposed_nothing(
    administrator, capture_evidence
):
    matter = factories.MatterFactory()
    sent(matter, capture_evidence, day=date(2026, 10, 5))
    entry(matter, day=date(2026, 10, 6))

    assert collect(administrator) == {}


@pytest.fixture
def narrow_scope(monkeypatch):
    """A reader who sees NORMAL records only — the participation half of the rule.

    Since docs/adr/0181 every business reader sees everything, so the narrower
    scope the builders still implement is planted the way
    `tests/test_department_changes_kpis.py` plants it.
    """
    import app.legacy_import.current_state as current_state
    import app.matters.models as matter_models
    import app.submissions.models as submission_models

    def narrow(user):
        return Scope(
            user=user,
            is_authenticated=True,
            sees_restricted_by_role=False,
            break_glass_grant_id=None,
        )

    for module in (matter_models, submission_models, current_state):
        monkeypatch.setattr(module, "scope_for_user", narrow)


def test_a_restricted_child_on_a_visible_topic_is_not_proposed_or_counted(
    specialist, capture_evidence, narrow_scope
):
    owner = factories.UserFactory()
    only_restricted = factories.MatterFactory(owner=owner, title="Ainult piiratud lapsega")
    mixed = factories.MatterFactory(owner=owner, title="Segatud")
    sent(
        only_restricted,
        capture_evidence,
        day=date(2026, 10, 3),
        restricted=True,
        summary="SALAJANE-1",
    )
    entry(only_restricted, day=date(2026, 10, 4), restricted=True, body="<p>SALAJANE-2</p>")
    hidden = sent(
        mixed, capture_evidence, day=date(2026, 10, 9), restricted=True, title="SALAJANE-3"
    )
    register(mixed, day=date(2026, 10, 9))
    restricted_topic = factories.MatterFactory(owner=owner, visibility=Visibility.RESTRICTED)
    entry(restricted_topic, day=date(2026, 10, 5))

    found = collect(specialist)

    assert only_restricted.pk not in found and restricted_topic.pk not in found
    # The invisible send neither suppresses the register date nor names itself.
    (valja,) = found[mixed.pk].events
    assert valja.ref.type == EventType.REGISTER
    assert valja.uncertainty == ""
    assert hidden.pk not in {event.ref.id for event in found[mixed.pk].events}
    # The owner, a participant, sees all of it.
    assert len(collect(owner)[only_restricted.pk].events) == 2


def test_the_end_of_the_period_never_reaches_past_now(specialist):
    matter = factories.MatterFactory()
    now = at(date(2026, 10, 15), 12)
    entry(matter, day=date(2026, 10, 15), hour=11)
    entry(matter, day=date(2026, 10, 15), hour=13)

    events = collect(specialist, now=now)[matter.pk].events

    assert len(events) == 1
    assert events[0].day == date(2026, 10, 15)
