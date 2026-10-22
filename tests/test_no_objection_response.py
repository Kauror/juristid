"""Koda's official «no comments» answer is a send, not an opinion (docs/adr/0174).

«Oleme eelnõuga tutvunud ning täiendavaid ettepanekuid ei ole» — sent by
e-mail or letter — answers a consultation request. It must end the request,
keep the e-mail itself as evidence (no signed PDF needed), be visible and
searchable as what it is, never read as an «arvamus välja», and never schedule
a thirty-day follow-up. A substantive opinion keeps every rule it had.
"""

from __future__ import annotations

import datetime as dt

import pytest
from django.utils import timezone

from app.documents.enums import DocumentRole
from app.documents.services import add_evidence_version, create_document
from app.matters import department_dashboard
from app.matters import work_items as wi
from app.matters.enums import ResponseDeadlineOutcome
from app.matters.my_work import person_stats
from app.matters.process_timeline import NO_OBJECTION_SENT_LABEL, SENT_LABEL
from app.matters.response_deadlines import (
    request_response_deadline,
    resolve_response_deadline,
)
from app.matters.timeline import NO_OBJECTION_MILESTONE, SUBMISSION_MILESTONE, submission_milestone
from app.reporting.context import ReportingContext, parse_period
from app.reporting.overview_strip import _no_objection_answers
from app.reporting.selectors.submissions import sent_submissions
from app.submissions.enums import (
    NOT_AN_OPINION_KINDS,
    SentAtPrecision,
    SubmissionKind,
    SubmissionStatus,
)
from app.submissions.services import (
    register_sent_opinion,
    register_sent_opinion_on_open_matter,
)
from app.workflow.models import OpinionFollowUp
from tests import factories

pytestmark = pytest.mark.django_db

NO_OBJECTION = SubmissionKind.NO_OBJECTION_RESPONSE
TODAY = timezone.localdate()
#: An Outlook message: the OLE compound-file signature a `.msg` carries.
MSG = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 504 + b"Oleme eelnoga tutvunud"


def _email(matter, actor, name="Vastus ministeeriumile.msg"):
    document = create_document(
        matter=matter,
        title="Koja vastus e-kirjaga",
        role=DocumentRole.KODA_SUBMISSION_FINAL,
        created_by=actor,
    )
    version = add_evidence_version(
        document=document,
        content=MSG + str(document.pk).encode(),
        original_filename=name,
        mime_type="application/vnd.ms-outlook",
        uploaded_by=actor,
    )
    return document, version


def _moment(day: dt.date) -> dt.datetime:
    return timezone.make_aware(dt.datetime.combine(day, dt.time.min))


def _answer(matter, actor, ministry, *, kind=NO_OBJECTION, interactive=True, day=None):
    document, version = _email(matter, actor)
    register = register_sent_opinion_on_open_matter if interactive else register_sent_opinion
    return register(
        document=document,
        version=version,
        title="Vastus: märkusi ja ettepanekuid ei ole",
        kind=kind,
        actor=actor,
        recipients=[ministry],
        sent_at=_moment(day or TODAY),
        sent_at_precision=SentAtPrecision.DATE,
    )


@pytest.fixture
def ministry():
    return factories.OrganisationFactory(name="Sünteetiline Kliimaministeerium")


def test_the_kind_exists_and_is_not_an_opinion():
    assert NO_OBJECTION.value in NOT_AN_OPINION_KINDS
    assert NO_OBJECTION.label == "Vastus: märkusi ja ettepanekuid ei ole"
    # Never filed as «informal» merely because it was an e-mail.
    assert SubmissionKind.INFORMAL_WRITTEN_RESPONSE.value not in NOT_AN_OPINION_KINDS
    assert SubmissionKind.FORMAL_OPINION.value not in NOT_AN_OPINION_KINDS


def test_an_email_is_enough_evidence_and_the_send_is_real(specialist, ministry):
    matter = factories.MatterFactory(owner=specialist)
    sent = _answer(matter, specialist, ministry, day=TODAY - dt.timedelta(days=1))

    assert sent.status == SubmissionStatus.SENT
    assert sent.kind == NO_OBJECTION
    assert sent.final_version.original_filename.endswith(".msg")
    assert timezone.localtime(sent.sent_at).date() == TODAY - dt.timedelta(days=1)
    assert [row.organisation for row in sent.recipient_rows.all()] == [ministry]


def test_it_answers_a_tracked_request_like_any_send(specialist, ministry):
    matter = factories.MatterFactory(owner=specialist)
    request_response_deadline(
        matter=matter, deadline=TODAY + dt.timedelta(days=5), actor=specialist
    )
    assert wi.response_obligation_of(matter, specialist, TODAY).is_outstanding

    sent = _answer(matter, specialist, ministry, interactive=False)
    resolve_response_deadline(
        matter=matter, outcome=ResponseDeadlineOutcome.ANSWERED, submission=sent, actor=specialist
    )

    matter.refresh_from_db()
    assert not wi.response_obligation_of(matter, specialist, TODAY).is_outstanding
    assert not wi.response_deadline_is_outstanding(matter, specialist)


def test_an_untracked_deadline_is_discharged_by_it_too(specialist, ministry):
    matter = factories.MatterFactory(
        owner=specialist, response_deadline=TODAY - dt.timedelta(days=2)
    )
    assert wi.response_obligation_of(matter, specialist, TODAY).is_overdue

    _answer(matter, specialist, ministry)

    assert not wi.response_obligation_of(matter, specialist, TODAY).is_outstanding


def test_no_follow_up_is_scheduled_for_it_but_still_for_an_opinion(specialist, ministry):
    matter = factories.MatterFactory(owner=specialist)
    answer = _answer(matter, specialist, ministry)
    assert not OpinionFollowUp.objects.filter(submission=answer).exists()

    other = factories.MatterFactory(owner=specialist)
    opinion = _answer(other, specialist, ministry, kind=SubmissionKind.FORMAL_OPINION)
    assert OpinionFollowUp.objects.filter(submission=opinion).exists()


def test_it_is_never_counted_as_an_opinion_sent(specialist, department_head, ministry):
    matter = factories.MatterFactory(owner=specialist)
    _answer(matter, specialist, ministry)
    other = factories.MatterFactory(owner=specialist)
    _answer(other, specialist, ministry, kind=SubmissionKind.FORMAL_OPINION)

    # Osakond and Minu asjad: «arvamust välja».
    assert department_dashboard.sent_this_week(department_head, TODAY).count() == 1
    stats = person_stats(department_head, specialist, TODAY)
    assert {row.label: row.value for row in stats.year}["arvamusi välja"] == 1

    # Statistika: the opinion count, and the answer by its kind's name.
    context = ReportingContext(
        viewer=department_head, period=parse_period("koik", TODAY), today=TODAY, now=timezone.now()
    )
    assert sent_submissions(context).count() == 1
    assert sent_submissions(context, kind=NO_OBJECTION).count() == 1
    figure = _no_objection_answers(context, TODAY)
    assert figure is not None and figure.value == 1
    assert f"arvamus={NO_OBJECTION.value}" in figure.url


def test_the_figure_is_absent_when_there_is_none(department_head):
    context = ReportingContext(
        viewer=department_head, period=parse_period("koik", TODAY), today=TODAY, now=timezone.now()
    )
    assert _no_objection_answers(context, TODAY) is None


def test_the_chronology_and_the_rail_name_it_as_an_answer(specialist, ministry):
    matter = factories.MatterFactory(owner=specialist)
    answer = _answer(matter, specialist, ministry)
    milestone = submission_milestone(answer, [ministry.name])
    assert milestone.what == NO_OBJECTION_MILESTONE == "Vastus välja"
    assert NO_OBJECTION.label in milestone.sub and ministry.name in milestone.sub

    opinion = _answer(
        factories.MatterFactory(owner=specialist),
        specialist,
        ministry,
        kind=SubmissionKind.FORMAL_OPINION,
    )
    assert submission_milestone(opinion, []).what == SUBMISSION_MILESTONE
    assert NO_OBJECTION_SENT_LABEL == "Koja vastus" != SENT_LABEL


def test_a_restricted_answer_is_counted_for_every_reader_and_no_outsider(
    specialist, reader, administrator, ministry
):
    """Since docs/adr/0181 a READER counts it as the owner does; an administrator not."""
    from app.core.enums import Visibility

    matter = factories.MatterFactory(owner=specialist, visibility=Visibility.RESTRICTED)
    _answer(matter, specialist, ministry)

    def count(viewer) -> int:
        context = ReportingContext(
            viewer=viewer, period=parse_period("koik", TODAY), today=TODAY, now=timezone.now()
        )
        return sent_submissions(context, kind=NO_OBJECTION).count()

    assert count(reader) == count(specialist) == 1
    assert count(administrator) == 0
