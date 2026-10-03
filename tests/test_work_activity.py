"""One work-activity vocabulary, aggregated three ways (RULE-02, docs/adr/0134).

`app.matters.activity` owns what counts as substantive work on a Matter. Three
surfaces aggregate it differently, and each is asserted here against the same
acts:

* **«Viimane tegevus»** — the latest qualifying activity (`activity_for_matter`);
* **«Muutusteta 30 p»** — whether that latest activity is older than 30 days
  (`work_items.quiet_matters`);
* **Osakond «Teemades muudatusi · eelmine nädal»** — whether the Matter had *at
  least one* qualifying activity in a calendar window
  (`department_dashboard._matters_changed_by_owner`).

Every case starts from a Matter whose last work was an entry 60 days ago, then
does one act today. What moves, and what deliberately does not, is the
contract. Record mutation (`updated_at`), Teema käik's event list and
Statistika's «Tegevus» are other questions and are not asserted to agree.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Callable
from typing import Any

import pytest
from django.utils import timezone

from app.audit.enums import ChangeEventType
from app.audit.services import record_change_event
from app.core.enums import Visibility
from app.documents.models import Document
from app.documents.services import add_evidence_version, create_document
from app.matters import work_items as wi
from app.matters.activity import ActivityBasis, activity_for_matter
from app.matters.department_dashboard import _matters_changed_by_owner
from app.matters.enums import EngagementKind
from app.matters.models import (
    Entry,
    Matter,
    MatterEngagement,
    MatterExternalPosition,
    MatterProceduralDevelopment,
    MatterWebsiteOverview,
)
from app.matters.services import (
    add_engagement,
    add_entry,
    change_stage,
    close_matter,
    plan_website_overview,
    publish_website_overview,
    record_external_position,
    record_procedural_development,
    set_matter_title,
)
from app.submissions.models import Submission
from app.workflow.enums import DatePrecision, Disposition
from app.workflow.models import NextAction
from app.workflow.services import complete_next_action, set_next_action
from tests import factories
from tests.test_dokumendid_opinion_block_retired import _sent_opinion

pytestmark = pytest.mark.django_db

OLD = -60
URL = "https://www.koda.ee/uudised/naide"


def _today() -> dt.date:
    return timezone.localdate()


def _day(offset: int) -> dt.date:
    return _today() + dt.timedelta(days=offset)


@pytest.fixture
def matter(specialist):
    """Last work 60 days ago: quiet, and not changed in any recent window."""
    matter = factories.MatterFactory(owner=specialist)
    factories.EntryFactory(
        matter=matter, author=specialist, occurred_at=timezone.now() + dt.timedelta(days=OLD)
    )
    return matter


def _last(matter, user) -> tuple[int | None, str]:
    fact = activity_for_matter(Matter.all_objects.get(pk=matter.pk), user)
    if fact is None:
        return None, ""
    return (fact.occurred_on - _today()).days, fact.basis


def _quiet(matter, user) -> bool:
    return matter.pk in set(wi.quiet_matters(user, _today()))


def _osakond(matter, user, start: dt.date, end: dt.date) -> bool:
    return _matters_changed_by_owner(user, start, end).get(matter.owner_id, 0) > 0


def _osakond_today(matter, user) -> bool:
    return _osakond(matter, user, _today(), _today())


# -- the acts ---------------------------------------------------------------------


def _title_edit(matter, actor, **_) -> None:
    set_matter_title(matter=matter, value="Uus pealkiri", actor=actor)


def _human_action(matter, actor, **_) -> None:
    set_next_action(matter=matter, text="Helistan ministeeriumi", actor=actor)


def _human_action_completed(matter, actor, **_) -> None:
    action = set_next_action(matter=matter, text="Helistan", actor=actor)
    NextAction.objects.filter(pk=action.pk).update(
        created_at=timezone.now() + dt.timedelta(days=OLD)
    )
    complete_next_action(action=NextAction.objects.get(pk=action.pk), actor=actor)


def _machine_action(matter, actor, **_) -> None:
    set_next_action(matter=matter, text="Registrist", actor=None)


def _entry(matter, actor, **_) -> None:
    add_entry(matter=matter, body="Märkus", author=actor)


def _entry_removed(matter, actor, **_) -> None:
    entry = add_entry(matter=matter, body="Vale teema", author=actor)
    Entry.objects.filter(pk=entry.pk).update(removed_at=timezone.now())


def _submission(matter, actor, organisation, **_) -> None:
    _sent_opinion(matter, actor=actor, organisation=organisation)


def _kaasamine_today(matter, actor, **_) -> None:
    add_engagement(
        matter=matter,
        kind=EngagementKind.EMAIL_CAMPAIGN,
        title="Küsitlus",
        occurred_on=_today(),
        actor=actor,
    )


def _kaasamine_undated(matter, actor, **_) -> None:
    add_engagement(matter=matter, kind=EngagementKind.EMAIL_CAMPAIGN, title="Küsitlus", actor=actor)


def _import_touch(matter, actor, **_) -> None:
    record_change_event(
        event_type=ChangeEventType.IMPORT_APPLIED,
        matter=matter,
        actor=None,
        obj=matter,
        summary="import",
    )
    Matter.objects.filter(pk=matter.pk).update(updated_at=timezone.now())


def _stage_change(matter, actor, **_) -> None:
    change_stage(matter=matter, stage=factories.StageFactory(), actor=actor)


def _document_upload(matter, actor, **_) -> None:
    document = create_document(
        matter=matter, title="fail.pdf", role="INCOMING_AUTHORITY", created_by=actor
    )
    add_evidence_version(
        document=document,
        content=b"%PDF-1.4 fail",
        original_filename="fail.pdf",
        mime_type="application/pdf",
        uploaded_by=actor,
    )


def _marge_today(matter, actor, **_) -> None:
    record_procedural_development(
        matter=matter, title="Saatsin ministeeriumile kirja", occurred_on=_today(), actor=actor
    )


def _marge_backdated(matter, actor, **_) -> None:
    record_procedural_development(
        matter=matter, title="Kohtusin ministeeriumiga", occurred_on=_day(-10), actor=actor
    )


def _marge_future(matter, actor, **_) -> None:
    record_procedural_development(
        matter=matter, title="Saadan ministeeriumile kirja", occurred_on=_day(5), actor=actor
    )


def _overview_published_known(matter, actor, **_) -> None:
    overview = plan_website_overview(matter=matter, actor=actor)
    publish_website_overview(overview=overview, url=URL, published_on=_day(-2), actor=actor)


def _overview_published_unknown(matter, actor, **_) -> None:
    overview = plan_website_overview(matter=matter, actor=actor)
    publish_website_overview(overview=overview, url=URL, published_on=None, actor=actor)


def _overview_planned(matter, actor, **_) -> None:
    plan_website_overview(matter=matter, actor=actor)


def _position_known(matter, actor, organisation, **_) -> None:
    record_external_position(
        matter=matter,
        organisation=organisation,
        url=URL,
        stated_on=_day(-1),
        actor=actor,
    )


def _position_unknown(matter, actor, organisation, **_) -> None:
    record_external_position(matter=matter, organisation=organisation, url=URL, actor=actor)


def _closure(matter, actor, **_) -> None:
    close_matter(matter=matter, disposition=Disposition.COMPLETED, actor=actor)


#: (case, act, last activity in days from today, basis, quiet, counted by Osakond today)
#: `OLD` means the act moved nothing and the 60-day entry is still the answer.
CASES: list[tuple[str, Callable[..., Any], int, str, bool, bool]] = [
    ("1 title edit", _title_edit, OLD, ActivityBasis.ENTRY, True, False),
    ("2 human action set", _human_action, 0, ActivityBasis.NEXT_ACTION, False, True),
    (
        "3 human action completed",
        _human_action_completed,
        0,
        ActivityBasis.NEXT_ACTION,
        False,
        True,
    ),
    ("4 machine action set", _machine_action, OLD, ActivityBasis.ENTRY, True, False),
    ("5 entry written", _entry, 0, ActivityBasis.ENTRY, False, True),
    ("6 entry written then removed", _entry_removed, OLD, ActivityBasis.ENTRY, True, False),
    ("7 submission sent", _submission, 0, ActivityBasis.SUBMISSION, False, True),
    ("8 kaasamine dated today", _kaasamine_today, 0, ActivityBasis.ENGAGEMENT, False, True),
    ("9 kaasamine undated", _kaasamine_undated, OLD, ActivityBasis.ENTRY, True, False),
    ("10 import touch", _import_touch, OLD, ActivityBasis.ENTRY, True, False),
    ("11 standalone hetkeseis", _stage_change, OLD, ActivityBasis.ENTRY, True, False),
    ("12 standalone document", _document_upload, OLD, ActivityBasis.ENTRY, True, False),
    ("13 marge done today", _marge_today, 0, "DEVELOPMENT", False, True),
    ("14 marge backdated", _marge_backdated, -10, "DEVELOPMENT", False, False),
    ("15 marge ahead", _marge_future, OLD, ActivityBasis.ENTRY, True, False),
    (
        "16 overview published, day known",
        _overview_published_known,
        -2,
        "WEBSITE_OVERVIEW",
        False,
        False,
    ),
    (
        "17 overview published, day unknown",
        _overview_published_unknown,
        OLD,
        ActivityBasis.ENTRY,
        True,
        False,
    ),
    ("16b overview only planned", _overview_planned, OLD, ActivityBasis.ENTRY, True, False),
    ("18 position, day known", _position_known, -1, "EXTERNAL_POSITION", False, False),
    ("19 position, day unknown", _position_unknown, OLD, ActivityBasis.ENTRY, True, False),
]


@pytest.mark.parametrize(
    ("act", "days", "basis", "quiet", "counted"),
    [case[1:] for case in CASES],
    ids=[case[0] for case in CASES],
)
def test_each_surface_reads_the_same_vocabulary(
    matter,
    specialist,
    department_head,
    organisation,
    evidence_root,
    act,
    days,
    basis,
    quiet,
    counted,
):
    act(matter, specialist, organisation=organisation)

    assert _last(matter, department_head) == (days, basis)
    assert _quiet(matter, department_head) is quiet
    assert _osakond_today(matter, department_head) is counted


def test_a_closure_is_still_the_last_work(matter, specialist, department_head):
    """Case 20: unchanged by this decision. A closed Matter is off the open quiet list."""
    _closure(matter, specialist)

    assert _last(matter, department_head) == (0, ActivityBasis.CLOSURE)
    assert _osakond_today(matter, department_head)


def test_a_dated_record_counts_in_the_window_of_its_own_day(matter, specialist, department_head):
    """Backdated `+ Märge`, a published day and a stated day count where they happened."""
    _marge_backdated(matter, specialist)
    assert _osakond(matter, department_head, _day(-10), _day(-10))
    assert not _osakond_today(matter, department_head)


def test_a_marge_dated_ahead_never_becomes_work_when_its_day_arrives(
    matter, specialist, department_head
):
    """Recorded eight days ago for three days ago: a plan then, and still not proof now.

    ADR 0124: a day after the day it was recorded made the `Märge` information
    or a plan. The calendar reaching that day says nothing about whether it
    happened; a step it set counts through the step.
    """
    development = record_procedural_development(
        matter=matter, title="Saadan kirja", occurred_on=_day(-3), actor=specialist
    )
    MatterProceduralDevelopment.objects.filter(pk=development.pk).update(
        created_at=timezone.now() + dt.timedelta(days=-8)
    )

    assert _last(matter, department_head) == (OLD, ActivityBasis.ENTRY)
    assert _quiet(matter, department_head)
    assert not _osakond(matter, department_head, _day(-3), _day(-3))


# -- the window asks for existence, not for the latest ------------------------------


def test_last_week_still_counts_when_there_is_also_work_today(matter, specialist, department_head):
    """A Matter worked on last week *and* today was worked on last week.

    «Viimane tegevus» is today and the Matter is not quiet — and Osakond's
    previous-week count still includes it, which a «latest activity is in the
    window» reading would not.
    """
    add_entry(
        matter=matter,
        body="Eelmisel nädalal",
        author=specialist,
        occurred_at=timezone.now() + dt.timedelta(days=-8),
    )
    add_entry(matter=matter, body="Täna", author=specialist)

    assert _last(matter, department_head) == (0, ActivityBasis.ENTRY)
    assert not _quiet(matter, department_head)
    assert _osakond(matter, department_head, _day(-8), _day(-8))


# -- a period is never a day -------------------------------------------------------


def test_an_approximate_record_moves_last_activity_but_no_bounded_window(
    matter, specialist, department_head
):
    """`period_in_window`: a window bounded in days never holds a period (docs/adr/0122 §2).

    A `+ Märge` remembered as «last month», recorded today, is work done — its
    anchor orders «Viimane tegevus» and its precision is how it is printed — but
    no day in last week is a day anybody named, so Osakond does not count it.
    """
    first_of_last_month = (_today().replace(day=1) - dt.timedelta(days=1)).replace(day=1)
    record_procedural_development(
        matter=matter,
        title="Kohtumine ministeeriumis",
        occurred_on=first_of_last_month,
        occurred_on_precision=DatePrecision.MONTH,
        actor=specialist,
    )

    fact = activity_for_matter(matter, department_head)
    assert fact.basis == "DEVELOPMENT"
    assert fact.occurred_on == first_of_last_month
    assert fact.date_precision == DatePrecision.MONTH
    assert not _osakond(matter, department_head, first_of_last_month, first_of_last_month)


# -- authorization -----------------------------------------------------------------


def _restrict(model: Any, matter) -> None:
    if model is Submission:
        # The sent binary may not be less restricted than the letter (database
        # trigger), so the evidence document is restricted first.
        Document.objects.filter(versions__finalised_submissions__matter=matter).update(
            visibility_override=Visibility.RESTRICTED
        )
    model.objects.filter(matter=matter).update(visibility_override=Visibility.RESTRICTED)


HIDDEN: list[tuple[str, Callable[..., Any], Any]] = [
    ("marge", _marge_today, MatterProceduralDevelopment),
    ("overview", _overview_published_known, MatterWebsiteOverview),
    ("position", _position_known, MatterExternalPosition),
    ("submission", _submission, Submission),
    ("kaasamine", _kaasamine_today, MatterEngagement),
]


@pytest.mark.parametrize(
    ("act", "model"), [case[1:] for case in HIDDEN], ids=[case[0] for case in HIDDEN]
)
def test_a_hidden_record_moves_nothing_for_the_reader(
    matter, specialist, reader, organisation, evidence_root, act, model
):
    act(matter, specialist, organisation=organisation)
    _restrict(model, matter)
    window = (_day(-2), _today())

    assert _last(matter, reader) == (OLD, ActivityBasis.ENTRY)
    assert _quiet(matter, reader)
    assert not _osakond(matter, reader, *window)
    # The specialist may read it, so for them it is the last work.
    assert _last(matter, specialist)[0] != OLD
    assert _osakond(matter, specialist, *window)


def test_a_hidden_next_action_moves_nothing_for_the_reader(matter, specialist, reader):
    _human_action(matter, specialist)
    _restrict(NextAction, matter)

    assert _last(matter, reader) == (OLD, ActivityBasis.ENTRY)
    assert _quiet(matter, reader)
    assert not _osakond_today(matter, reader)
