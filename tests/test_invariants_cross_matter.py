"""The audit's reproduced HARD invariants that no check yet covered (ENG-144).

`check_domain_invariants` already answered Q02, Q06, Q16, Q17 and Q44 — five of
the nine HARD queries the engineering audit reproduced a violation of. These
are the other four, each a rule every service keeps and no constraint can:

* Q29 — a `DocumentLink` joining a document to a record on another Matter;
* Q43 — a live `Väline seisukoht` answering another Matter's `Kaasamine`;
* Q46 — a `ChangeEvent` about a record, filed under another Matter;
* Q59 — a closed FULL Matter with no `MATTER_CLOSED` behind it.

Each is made here the way the audit made it — around the service, by a
queryset update or a raw row — because the services refuse to make it at all.
Each is also shown *not* firing on what the services write, and Q43 not firing
on the one near miss that is legitimate: a position still answering a round
that was taken off the file (ENG-047).
"""

from __future__ import annotations

import io

import pytest
from django.core.management import call_command
from django.utils import timezone

from app.audit.enums import ChangeEventType
from app.audit.services import record_change_event
from app.core.invariants import EXPLANATIONS, SERVICE_RULES, check_domain_invariants
from app.documents.links import DocumentLink
from app.documents.services import link_document_to_record
from app.matters import services as matter_services
from app.matters.enums import EngagementKind
from app.matters.models import Matter, MatterEngagement, MatterExternalPosition
from tests import factories

pytestmark = pytest.mark.django_db

NEW_KINDS = (
    "document-link-crosses-matter",
    "external-position-crosses-matter",
    "change-event-on-another-matter",
    "response-deadline-submission-crosses-matter",
    "closed-matter-without-closure-event",
)


def _subjects(kind: str) -> list[str]:
    return [f.subject for f in check_domain_invariants().findings if f.kind == kind]


@pytest.fixture
def other_matter(specialist):
    return factories.MatterFactory(owner=specialist)


def test_each_new_kind_is_explained_and_does_not_block_the_migration():
    for kind in NEW_KINDS:
        assert kind in SERVICE_RULES
        assert EXPLANATIONS[kind]


# -- Q29 ---------------------------------------------------------------------------


def test_a_link_the_service_wrote_is_clean(normal_matter, specialist):
    document = factories.DocumentFactory(matter=normal_matter)
    entry = factories.EntryFactory(matter=normal_matter, author=specialist)
    link = link_document_to_record(document=document, record=entry, actor=specialist)

    assert str(link.pk) not in _subjects("document-link-crosses-matter")


def test_a_link_to_another_matters_record_is_named(normal_matter, other_matter, specialist):
    document = factories.DocumentFactory(matter=normal_matter)
    foreign = factories.EntryFactory(matter=other_matter, author=specialist)
    link = DocumentLink.objects.create(document=document, entry=foreign)

    findings = [
        f for f in check_domain_invariants().findings if f.kind == "document-link-crosses-matter"
    ]
    assert [f.subject for f in findings] == [str(link.pk)]
    assert findings[0].detail == (
        f"entry: document matter={normal_matter.pk}, record matter={other_matter.pk}"
    )


# -- Q43 ---------------------------------------------------------------------------


def _engagement(matter, actor) -> MatterEngagement:
    return matter_services.add_engagement(
        matter=matter, kind=EngagementKind.SURVEY, title="Kaasamine", actor=actor
    )


def _position(matter, engagement, actor) -> MatterExternalPosition:
    return matter_services.record_external_position(
        matter=matter,
        organisation=factories.OrganisationFactory(),
        url="https://example.org/seisukoht",
        engagement=engagement,
        actor=actor,
    )


def test_a_position_on_its_own_round_is_clean(normal_matter, specialist):
    position = _position(normal_matter, _engagement(normal_matter, specialist), specialist)
    assert str(position.pk) not in _subjects("external-position-crosses-matter")


def test_a_position_on_another_matters_round_is_named(normal_matter, other_matter, specialist):
    position = _position(normal_matter, _engagement(normal_matter, specialist), specialist)
    foreign = _engagement(other_matter, specialist)
    MatterExternalPosition.objects.filter(pk=position.pk).update(engagement=foreign)

    assert _subjects("external-position-crosses-matter") == [str(position.pk)]


def test_a_position_on_a_round_taken_off_the_file_is_not_a_finding(normal_matter, specialist):
    """Kept on purpose: the position still answered that round (ENG-047)."""
    engagement = _engagement(normal_matter, specialist)
    position = _position(normal_matter, engagement, specialist)
    MatterEngagement.objects.filter(pk=engagement.pk).update(
        removed_at=timezone.now(), removed_by=specialist
    )

    assert str(position.pk) not in _subjects("external-position-crosses-matter")


def test_a_removed_position_is_not_a_finding(normal_matter, other_matter, specialist):
    position = _position(normal_matter, _engagement(normal_matter, specialist), specialist)
    MatterExternalPosition.objects.filter(pk=position.pk).update(
        engagement=_engagement(other_matter, specialist),
        removed_at=timezone.now(),
        removed_by=specialist,
    )

    assert str(position.pk) not in _subjects("external-position-crosses-matter")


# -- Q46 ---------------------------------------------------------------------------


def test_events_the_services_wrote_are_clean(normal_matter, specialist):
    engagement = _engagement(normal_matter, specialist)
    _position(normal_matter, engagement, specialist)

    assert _subjects("change-event-on-another-matter") == []


def test_an_event_about_a_record_filed_under_another_matter_is_named(
    normal_matter, other_matter, specialist
):
    engagement = _engagement(normal_matter, specialist)
    stray = record_change_event(
        event_type=ChangeEventType.ENGAGEMENT_ADDED,
        matter=other_matter,
        actor=specialist,
        obj=engagement,
    )

    findings = [
        f for f in check_domain_invariants().findings if f.kind == "change-event-on-another-matter"
    ]
    assert [f.subject for f in findings] == [str(stray.pk)]
    assert "matters.MatterEngagement" in findings[0].detail
    assert f"filed under matter={other_matter.pk}" in findings[0].detail


def test_an_event_about_a_version_is_read_through_its_document(
    normal_matter, other_matter, specialist, capture_evidence
):
    version = capture_evidence(normal_matter, b"sisu", "memo.txt", "text/plain")
    assert _subjects("change-event-on-another-matter") == []

    stray = record_change_event(
        event_type=ChangeEventType.EVIDENCE_VERSION_ADDED,
        matter=other_matter,
        actor=specialist,
        obj=version,
    )

    assert _subjects("change-event-on-another-matter") == [str(stray.pk)]


# -- the answered deadline's opinion ------------------------------------------------
#
# `resolve_response_deadline` and `answer_current_deadline_with` both take the
# submission through `_checked_submission`, which refuses another Matter's
# opinion; nothing in the schema can. Same family as Q29/Q43/Q46, found by the
# overnight stabilization audit (2026-10-08).


def _sent_opinion(matter, capture_evidence):
    from app.submissions.enums import SubmissionStatus

    version = capture_evidence(
        matter, b"%PDF-1.4 synthetic opinion", "arvamus.pdf", "application/pdf"
    )
    return factories.SubmissionFactory(
        matter=matter,
        status=SubmissionStatus.SENT,
        sent_at=timezone.now(),
        final_version=version,
    )


def _answered_deadline(matter, specialist, capture_evidence):
    from datetime import timedelta

    from app.matters.models import MatterResponseDeadline
    from app.matters.response_deadlines import (
        request_response_deadline,
        resolve_response_deadline,
    )

    request_response_deadline(
        matter=matter, deadline=timezone.localdate() + timedelta(days=7), actor=specialist
    )
    opinion = _sent_opinion(matter, capture_evidence)
    resolve_response_deadline(
        matter=matter, outcome="ANSWERED", actor=specialist, submission=opinion
    )
    return MatterResponseDeadline.objects.get(matter=matter)


def test_a_deadline_answered_by_its_own_opinion_is_clean(
    normal_matter, specialist, capture_evidence
):
    row = _answered_deadline(normal_matter, specialist, capture_evidence)
    assert str(row.pk) not in _subjects("response-deadline-submission-crosses-matter")


def test_a_deadline_answered_by_another_matters_opinion_is_named(
    normal_matter, other_matter, specialist, capture_evidence
):
    from app.matters.models import MatterResponseDeadline

    row = _answered_deadline(normal_matter, specialist, capture_evidence)
    foreign = _sent_opinion(other_matter, capture_evidence)
    MatterResponseDeadline._base_manager.filter(pk=row.pk).update(submission=foreign)

    assert _subjects("response-deadline-submission-crosses-matter") == [str(row.pk)]


# -- Q59 ---------------------------------------------------------------------------


def test_a_closure_through_the_service_is_clean(normal_matter, specialist):
    matter_services.close_matter(matter=normal_matter, disposition="COMPLETED", actor=specialist)
    assert str(normal_matter.pk) not in _subjects("closed-matter-without-closure-event")


def test_a_closure_around_the_service_is_named(normal_matter):
    Matter._base_manager.filter(pk=normal_matter.pk).update(
        is_open=False, disposition="COMPLETED", closed_at=timezone.now()
    )

    assert _subjects("closed-matter-without-closure-event") == [str(normal_matter.pk)]


def test_an_archive_row_is_not_a_person_closure(specialist):
    archive = factories.ArchiveMatterFactory(owner=specialist, is_open=False)
    assert str(archive.pk) not in _subjects("closed-matter-without-closure-event")


# -- the report ----------------------------------------------------------------------


def test_the_command_names_the_kind_and_no_title(normal_matter, other_matter, specialist):
    document = factories.DocumentFactory(matter=normal_matter, title="Salajane pealkiri")
    DocumentLink.objects.create(
        document=document, entry=factories.EntryFactory(matter=other_matter, author=specialist)
    )

    output = io.StringIO()
    with pytest.raises(SystemExit) as exit_:
        call_command("check_domain_invariants", stdout=output)

    assert exit_.value.code == 1
    text = output.getvalue()
    assert "document-link-crosses-matter" in text
    assert EXPLANATIONS["document-link-crosses-matter"] in text
    assert "Salajane" not in text
    assert normal_matter.title not in text
