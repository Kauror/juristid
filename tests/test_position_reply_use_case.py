"""`workspace.record_position_reply` — the reply use case, without a view (JARCH-001).

The rules `Lisa tagasiside` keeps (docs/adr/0167 §2) used to be composed in
`views._record_external_position`, so a second caller — a command, a seed, a
future door — had to restate them or go without. These tests call the use case
directly, so whatever calls it gets the same guarantees as the form:

* the first answer to an open round finishes its wait;
* a later answer is recorded and the round keeps its closure;
* an identical repeat is refused, and writes nothing;
* a standalone position touches no wait;
* a closed Matter is refused, and a typed institution does not survive it.
"""

from __future__ import annotations

import pytest

from app.audit.enums import ChangeEventType
from app.audit.models import ChangeEvent
from app.matters import workspace
from app.matters.engagement_replies import REPLY_ALREADY_RECORDED
from app.matters.enums import EngagementKind, ExternalPositionProvenance
from app.matters.locks import CLOSED_MATTER_REFUSAL
from app.matters.models import MatterExternalPosition
from app.matters.services import add_engagement, close_matter
from app.organisations.models import Organisation
from app.workflow.enums import Disposition
from tests.follow_ups import day
from tests.refusals import refused

pytestmark = pytest.mark.django_db

RECEIVED = ExternalPositionProvenance.RECEIVED.value


def _round(matter, actor):
    return add_engagement(
        matter=matter,
        kind=EngagementKind.SURVEY,
        title="Liikmete küsitlus",
        occurred_on=day(-5),
        feedback_deadline=day(5),
        actor=actor,
    )


def _reply(matter, actor, round_=None, summary="Liige toetab.", **extra):
    return workspace.record_position_reply(
        matter=matter,
        author=actor,
        provenance=RECEIVED,
        summary=summary,
        engagement=round_,
        **extra,
    )


def _closed_events(round_) -> int:
    return ChangeEvent.objects.filter(
        event_type=ChangeEventType.ENGAGEMENT_FEEDBACK_CLOSED, object_id=round_.pk
    ).count()


def test_the_first_answer_finishes_the_wait(normal_matter, specialist):
    round_ = _round(normal_matter, specialist)

    result = _reply(normal_matter, specialist, round_)

    round_.refresh_from_db()
    assert result.wait_finished is True
    assert result.record.engagement_id == round_.pk
    assert round_.feedback_closed_at is not None
    assert round_.feedback_closed_by_id == specialist.pk
    assert _closed_events(round_) == 1


def test_a_later_answer_keeps_the_closure(normal_matter, specialist, other_specialist):
    round_ = _round(normal_matter, specialist)
    _reply(normal_matter, specialist, round_, summary="Esimene vastus.")
    round_.refresh_from_db()
    closed = (round_.feedback_closed_at, round_.feedback_closed_by_id)

    result = _reply(normal_matter, other_specialist, round_, summary="Teine vastus.")

    round_.refresh_from_db()
    assert result.wait_finished is False
    assert (round_.feedback_closed_at, round_.feedback_closed_by_id) == closed
    assert _closed_events(round_) == 1
    assert sorted(
        MatterExternalPosition.objects.filter(engagement=round_).values_list("summary", flat=True)
    ) == ["Esimene vastus.", "Teine vastus."]


def test_an_identical_repeat_is_refused_and_writes_nothing(normal_matter, specialist):
    round_ = _round(normal_matter, specialist)
    _reply(
        normal_matter,
        specialist,
        round_,
        stated_on=day(-1),
        organisation_name="Sünteetiline Kordusliit",
    )
    organisations = Organisation.objects.count()

    # The typed name resolves to the institution the first answer created, so
    # this is the same author, words and day: a repeated submit.
    with refused(REPLY_ALREADY_RECORDED):
        _reply(
            normal_matter,
            specialist,
            round_,
            stated_on=day(-1),
            organisation_name="Sünteetiline Kordusliit",
        )

    assert MatterExternalPosition.objects.filter(engagement=round_).count() == 1
    assert Organisation.objects.count() == organisations


def test_the_same_words_from_another_author_are_a_second_answer(normal_matter, specialist):
    round_ = _round(normal_matter, specialist)
    _reply(normal_matter, specialist, round_, stated_on=day(-1))

    _reply(
        normal_matter,
        specialist,
        round_,
        stated_on=day(-1),
        organisation_name="Sünteetiline Teine Liit",
    )

    assert MatterExternalPosition.objects.filter(engagement=round_).count() == 2


def test_a_standalone_position_touches_no_wait(normal_matter, specialist):
    round_ = _round(normal_matter, specialist)

    result = _reply(normal_matter, specialist, None)

    round_.refresh_from_db()
    assert result.wait_finished is False
    assert result.record.engagement_id is None
    assert round_.feedback_closed_at is None
    assert _closed_events(round_) == 0


def test_a_closed_matter_is_refused_and_leaves_no_institution(normal_matter, specialist):
    close_matter(
        matter=normal_matter,
        disposition=Disposition.COMPLETED,
        actor=specialist,
        reason="QA",
        follow_ups_confirmed=True,
    )
    normal_matter.refresh_from_db()

    with refused(CLOSED_MATTER_REFUSAL):
        _reply(normal_matter, specialist, None, organisation_name="Sünteetiline Hilisliit")

    assert not MatterExternalPosition.objects.filter(matter=normal_matter).exists()
    assert not Organisation.objects.filter(name="Sünteetiline Hilisliit").exists()
