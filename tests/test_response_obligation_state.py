"""The plan and the obligation are two questions about one date.

What was wrong
--------------

``Arvamuse tähtaeg`` had exactly one predicate behind it, and that predicate was
answering two questions at once.

    outstanding_response_deadlines(user)

*Is this what a lawyer should be working on today?* — the operational plan, and
for that question an open ``Järgmiseks`` rightly outranks the deadline. A file
whose lawyer has written «JÄLGIN, vaatan uuesti üle 09.10» is being monitored,
not missed, and putting it on an overdue list is the defect ADR 0050 fixed.

*Has Koda actually answered?* — the official obligation, and for **that**
question an open ``Järgmiseks`` says nothing at all. A ministry waiting for the
Chamber's opinion is not answered by the Chamber writing itself a note, however
sound the note is. Yet recording that note made the only predicate the product
had say ``False``, so the obligation quietly reported itself as met.

What this file holds
--------------------

The two questions, asked separately, against one another.

``response_obligations``
    discharged only by a ``SENT`` Submission the reader may see, or by a
    ``CURRENT`` register row whose ``VÄLJA`` reads a date or *ei saatnud*.
``outstanding_response_deadlines``
    the same population *minus* the Matters carrying a visible open step —
    which is exactly what it selected before this concept existed.

So a good half of what is asserted here is again what did **not** change. The
current work population, the register's primary date and its sort key, and the
row ``work_items`` emits are all held to their previous behaviour by name, on
the same matrix the new concept is asked to answer differently. This round adds
a reading; it moves no count and no surface.

Every date is relative to today, for the reason `test_response_deadline_work`
gives: a test written around a production date passes for a fortnight and then
fails for reasons nobody can reproduce.
"""

from __future__ import annotations

import hashlib
from datetime import date, timedelta
from typing import Any

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from app.core.dates import format_estonian_date
from app.core.enums import Visibility
from app.legacy_import.current_state import CurrentRegisterState, RegisterCurrency
from app.legacy_import.models import MatterSourceReference
from app.legacy_import.register_semantics import OpinionSentState
from app.matters import register_dates as rd
from app.matters import selectors
from app.matters import work_items as wi
from app.matters.models import Matter
from app.matters.services import close_matter, create_matter
from app.submissions.enums import SubmissionStatus
from app.submissions.services import (
    attach_final_evidence,
    create_submission,
    mark_submission_sent,
)
from app.workflow.enums import ActionKind, ActionStatus, DateSemantics, Disposition
from app.workflow.services import set_next_action
from tests import factories
from tests import synthetic_corpus as corpus

pytestmark = pytest.mark.django_db

PDF = b"%PDF-1.4 arvamus"

TITLE = "Kohustusega teema"


@pytest.fixture
def today() -> date:
    return timezone.localdate()


def _matter(owner, *, deadline, title=TITLE, **kwargs):
    return create_matter(
        title=title,
        owner=owner,
        reference_year=2026,
        response_deadline=deadline,
        **kwargs,
    )


def _mark(
    matter,
    *,
    state: str,
    sent_on: date | None = None,
    currency: str = RegisterCurrency.CURRENT,
) -> CurrentRegisterState:
    """One derived register row saying what ``VÄLJA`` holds for this Matter.

    The same construction `test_valja_completion_semantics` uses, and for the
    same reason: what is under test is how a read model treats a derived row,
    not how the row is derived.
    """
    digest = hashlib.sha256(b"response-obligation-test-snapshot").hexdigest()
    reference = MatterSourceReference.objects.create(
        matter=matter,
        source_system="EXCEL_REGISTER",
        source_file_name="Tood eelnoudega.xlsx",
        source_snapshot_sha256=digest,
        source_sheet="2026",
        source_row_number=matter.reference_number,
        source_row_raw={"VÄLJA": "" if state == OpinionSentState.BLANK else "margitud"},
        source_title=matter.title,
        source_era="2026",
    )
    return CurrentRegisterState.objects.create(
        matter=matter,
        source_reference=reference,
        source_snapshot_sha256=digest,
        source_sheet="2026",
        source_row_number=reference.source_row_number,
        currency=currency,
        status_label="Kooskolastusringil",
        opinion_sent_recorded=state != OpinionSentState.BLANK,
        opinion_sent_state=state,
        opinion_sent_date=sent_on,
        continues_under_reference=("2026_999" if currency == RegisterCurrency.SUPERSEDED else ""),
        observed_at=timezone.now(),
    )


def _send_opinion(matter, actor):
    """Discharge the obligation the way the product does: send the opinion."""
    submission = create_submission(matter=matter, title="Arvamus", actor=actor)
    attach_final_evidence(
        submission=submission,
        content=PDF,
        original_filename="arvamus.pdf",
        mime_type="application/pdf",
        actor=actor,
    )
    submission.refresh_from_db()
    return mark_submission_sent(submission=submission, actor=actor)


def _send_restricted_opinion(matter, capture_evidence, extract):
    """A SENT opinion restricted below an otherwise ordinary Matter.

    Built the way `test_child_existence_visibility` builds it, because the
    question is the same one: whether a record the reader may not open is
    allowed to decide what they are told about the Matter.
    """
    version = capture_evidence(
        matter,
        corpus.text_pdf(["Salajane arvamus"]),
        "salajane.pdf",
        "application/pdf",
        title="Salajane arvamus",
        visibility_override=Visibility.RESTRICTED,
    )
    extract(version)
    return factories.SubmissionFactory(
        matter=matter,
        title="Salajane arvamus",
        status=SubmissionStatus.SENT,
        sent_at=timezone.now(),
        final_version=version,
        visibility_override=Visibility.RESTRICTED,
    )


def _instruct(matter, actor, *, kind=ActionKind.MONITOR, on=None, text="Jalgin"):
    return set_next_action(
        matter=matter,
        text=text,
        kind=kind,
        date_semantics=DateSemantics.REVIEW_ON,
        target_date=on,
        actor=actor,
    )


def _obliged(user, **kwargs) -> set[Any]:
    return set(wi.response_obligations(user, **kwargs).values_list("pk", flat=True))


def _operational(user, **kwargs) -> set[Any]:
    return set(wi.outstanding_response_deadlines(user, **kwargs).values_list("pk", flat=True))


# ---------------------------------------------------------------------------
# 1. The main scenario: the plan moves, the obligation does not
# ---------------------------------------------------------------------------


@pytest.fixture
def instructed_matter(specialist, today):
    """Yesterday's deadline, nothing sent, and a MONITOR step three weeks out.

    The exact shape the two questions answer differently, and the reason this
    concept exists.
    """
    matter = _matter(specialist, deadline=today - timedelta(days=1))
    _instruct(matter, specialist, on=today + timedelta(days=24))
    return matter


def test_the_instruction_is_the_only_operational_row(instructed_matter, specialist, today):
    """`work_items` emits the step and no response-deadline row. Unchanged."""
    items = [
        item
        for item in wi.work_items(specialist, today=today, responsible=specialist)
        if item.matter_id == instructed_matter.pk
    ]

    (item,) = items
    assert item.source_type == wi.SOURCE_NEXT_ACTION
    assert item.when == today + timedelta(days=24)
    assert instructed_matter.pk not in _operational(specialist)


def test_the_register_date_and_its_sort_key_stay_on_the_instruction(
    instructed_matter, specialist, today
):
    """The primary Kuupäev reading and the column the database orders on.

    Both, because they are two readings of one rule and the whole point of
    `register_dates` is that they cannot disagree.
    """
    row = (
        Matter.objects.visible_to(specialist)
        .prefetch_related(selectors.open_action_prefetch(specialist))
        .get(pk=instructed_matter.pk)
    )
    reading = rd.register_date(row, today)

    assert reading is not None
    assert reading.value == today + timedelta(days=24)
    assert reading.meaning != rd.RESPONSE_DEADLINE_LABEL

    sorted_value = (
        rd.annotate_display_date(Matter.objects.visible_to(specialist), specialist)
        .values_list(rd.DISPLAY_DATE, flat=True)
        .get(pk=instructed_matter.pk)
    )
    assert sorted_value == today + timedelta(days=24)


def test_the_obligation_is_outstanding_and_a_day_late(instructed_matter, specialist, today):
    """The other question, asked of the same Matter at the same moment."""
    assert instructed_matter.pk in _obliged(specialist)

    obligation = wi.response_obligation_of(instructed_matter, specialist, today)

    assert obligation.is_outstanding is True
    assert obligation.is_past is True
    assert obligation.is_overdue is True
    assert obligation.days_late == 1
    assert obligation.value == today - timedelta(days=1)
    assert obligation.display == format_estonian_date(today - timedelta(days=1))
    assert obligation.label == rd.RESPONSE_DEADLINE_LABEL


def test_the_stored_deadline_is_never_written(instructed_matter, specialist, today):
    """Neither reading may express itself by touching the column."""
    before = instructed_matter.response_deadline

    _obliged(specialist)
    _operational(specialist)
    wi.response_obligation_of(instructed_matter, specialist, today)

    instructed_matter.refresh_from_db()
    assert instructed_matter.response_deadline == before == today - timedelta(days=1)


# ---------------------------------------------------------------------------
# 2. The discharge matrix
# ---------------------------------------------------------------------------
#
# One table, read twice. The obligation column is the new concept; the
# operational column is what the product already did and must keep doing.


def _outstanding_case(specialist, today, build):
    matter = _matter(specialist, deadline=today - timedelta(days=30))
    build(matter, specialist, today)
    return matter


@pytest.mark.parametrize(
    ("case", "build"),
    [
        pytest.param("nothing-recorded", lambda m, u, t: None, id="nothing-recorded"),
        pytest.param(
            "open-dated-action",
            lambda m, u, t: _instruct(m, u, on=t + timedelta(days=40)),
            id="open-dated-action",
        ),
        pytest.param(
            "open-undated-action",
            lambda m, u, t: _instruct(m, u, on=None),
            id="open-undated-action",
        ),
        pytest.param(
            "current-recorded-other",
            lambda m, u, t: _mark(m, state=OpinionSentState.RECORDED_OTHER),
            id="current-recorded-other",
        ),
        pytest.param(
            "current-blank",
            lambda m, u, t: _mark(m, state=OpinionSentState.BLANK),
            id="current-blank",
        ),
        pytest.param(
            "retired-date",
            lambda m, u, t: _mark(
                m,
                state=OpinionSentState.DATE,
                sent_on=t - timedelta(days=28),
                currency=RegisterCurrency.RETIRED,
            ),
            id="retired-date",
        ),
        pytest.param(
            "superseded-date",
            lambda m, u, t: _mark(
                m,
                state=OpinionSentState.DATE,
                sent_on=t - timedelta(days=28),
                currency=RegisterCurrency.SUPERSEDED,
            ),
            id="superseded-date",
        ),
    ],
)
def test_these_leave_the_obligation_outstanding(specialist, today, case, build):
    """Nothing here is Koda answering. An instruction is a plan, not a response;
    a retired or superseded row speaks for a finished file, not this one; an
    unread cell is not an approved completion state (ADR 0059 §2, §3)."""
    matter = _outstanding_case(specialist, today, build)

    assert matter.pk in _obliged(specialist)
    assert wi.response_obligation_of(matter, specialist, today).is_outstanding is True


@pytest.mark.parametrize(
    ("case", "build"),
    [
        pytest.param(
            "sent-submission",
            lambda m, u, t: _send_opinion(m, u),
            id="sent-submission",
        ),
        pytest.param(
            "current-date",
            lambda m, u, t: _mark(m, state=OpinionSentState.DATE, sent_on=t - timedelta(days=28)),
            id="current-date",
        ),
        pytest.param(
            "current-not-sent",
            lambda m, u, t: _mark(m, state=OpinionSentState.NOT_SENT),
            id="current-not-sent",
        ),
    ],
)
def test_these_discharge_the_obligation(specialist, today, case, build):
    """The two facts that end it: the opinion went out, or the department
    recorded that the opinion step is over."""
    matter = _outstanding_case(specialist, today, build)

    assert matter.pk not in _obliged(specialist)
    obligation = wi.response_obligation_of(matter, specialist, today)
    assert obligation.is_outstanding is False
    # Past, and not late. The register answered it.
    assert obligation.is_past is True
    assert obligation.is_overdue is False
    assert obligation.days_late == 0


def test_an_instruction_does_not_discharge_what_the_register_left_open(specialist, today):
    """The seam in one assertion: suppressed as work, still owed as an answer."""
    matter = _matter(specialist, deadline=today - timedelta(days=200))
    _mark(matter, state=OpinionSentState.BLANK)
    _instruct(matter, specialist, on=today + timedelta(days=40))

    assert matter.pk not in _operational(specialist)
    assert matter.pk in _obliged(specialist)


# ---------------------------------------------------------------------------
# 3. Reader safety
# ---------------------------------------------------------------------------
#
# The SENT evidence side is scoped, so a colleague's restricted opinion may not
# decide what a reader who cannot see it is told about a NORMAL Matter.


def test_a_restricted_opinion_does_not_discharge_it_for_a_reader_who_cannot_see_it(
    specialist, reader, today, capture_evidence, extract
):
    """Removing a row is observable — AUTH-003 applied to the new concept."""
    matter = _matter(specialist, deadline=today - timedelta(days=10))
    assert matter.pk in _obliged(reader)

    _send_restricted_opinion(matter, capture_evidence, extract)

    assert matter.pk in _obliged(reader)
    assert wi.response_obligation_of(matter, reader, today).is_outstanding is True


def test_a_participant_sees_their_own_opinion_discharge_it(
    specialist, today, capture_evidence, extract
):
    """The other half of the contract: a fix that hid the work from the people
    who did it would trade one defect for a worse one."""
    matter = _matter(specialist, deadline=today - timedelta(days=10))

    _send_restricted_opinion(matter, capture_evidence, extract)

    assert matter.pk not in _obliged(specialist)
    assert wi.response_obligation_of(matter, specialist, today).is_outstanding is False


def test_a_restricted_step_cannot_be_inferred_from_the_obligation(specialist, reader, today):
    """The obligation never reads ``NextAction`` at all, so a restricted step
    moves nothing — which is the strongest form of not leaking it."""
    matter = _matter(specialist, deadline=today - timedelta(days=10))
    before = _obliged(reader)

    action = _instruct(matter, specialist, on=today + timedelta(days=40))
    action.visibility_override = Visibility.RESTRICTED
    action.save(update_fields=["visibility_override"])

    assert _obliged(reader) == before
    # And the operational population, which does read it, keeps its own rule:
    # invisible to the reader, so for them the deadline is still today's work.
    assert matter.pk in _operational(reader)
    assert matter.pk not in _operational(specialist)


def test_a_matter_the_reader_may_not_see_yields_no_obligation(specialist, reader, today):
    matter = _matter(
        specialist, deadline=today - timedelta(days=10), visibility=Visibility.RESTRICTED
    )

    assert matter.pk not in _obliged(reader)
    assert wi.response_obligation_of(matter, reader, today).is_outstanding is False
    assert wi.response_obligation_of(matter, specialist, today).is_outstanding is True


# ---------------------------------------------------------------------------
# 4. The annotation and the read say the same thing
# ---------------------------------------------------------------------------


def _matrix(specialist, today):
    """One of every row in the discharge matrix, built once."""
    plain = _matter(specialist, deadline=today - timedelta(days=5), title="Midagi ei ole")
    instructed = _matter(specialist, deadline=today - timedelta(days=5), title="Juhisega")
    _instruct(instructed, specialist, on=today + timedelta(days=40))
    sent = _matter(specialist, deadline=today - timedelta(days=5), title="Saadetud")
    _send_opinion(sent, specialist)
    dated = _matter(specialist, deadline=today - timedelta(days=5), title="Registris kuupaev")
    _mark(dated, state=OpinionSentState.DATE, sent_on=today - timedelta(days=4))
    not_sent = _matter(specialist, deadline=today - timedelta(days=5), title="Ei saatnud")
    _mark(not_sent, state=OpinionSentState.NOT_SENT)
    other = _matter(specialist, deadline=today - timedelta(days=5), title="Loetamatu")
    _mark(other, state=OpinionSentState.RECORDED_OTHER)
    undated = _matter(specialist, deadline=None, title="Tahtajata")
    return [plain, instructed, sent, dated, not_sent, other, undated]


def test_the_annotation_and_the_row_reading_agree_across_the_matrix(specialist, today):
    """`response_obligation_of` reads the annotation; asked without one it
    re-derives the same answer. Both spellings, against each other."""
    matters = _matrix(specialist, today)

    annotated = {
        row.pk: row
        for row in wi.annotate_response_obligation(
            Matter.objects.visible_to(specialist), specialist
        )
    }
    population = _obliged(specialist)

    for matter in matters:
        row = annotated[matter.pk]
        from_annotation = wi.response_obligation_of(row, specialist, today)
        # A fresh instance carries no annotation, so this asks the database.
        from_read = wi.response_obligation_of(Matter.objects.get(pk=matter.pk), specialist, today)

        assert from_annotation == from_read, matter.title
        expected = matter.response_deadline is not None and not getattr(row, wi.DISCHARGED)
        assert from_annotation.is_outstanding is expected, matter.title
        assert (matter.pk in population) is expected, matter.title


def test_a_matter_with_no_deadline_is_never_outstanding(specialist, today):
    matter = _matter(specialist, deadline=None, title="Tahtajata")

    obligation = wi.response_obligation_of(matter, specialist, today)

    assert obligation.value is None
    assert obligation.display == ""
    assert obligation.is_outstanding is False
    assert obligation.is_past is False
    assert obligation.is_overdue is False
    assert obligation.days_late == 0
    assert matter.pk not in _obliged(specialist)


def test_a_future_deadline_is_outstanding_but_not_late(specialist, today):
    matter = _matter(specialist, deadline=today + timedelta(days=7))

    obligation = wi.response_obligation_of(matter, specialist, today)

    assert obligation.is_outstanding is True
    assert obligation.is_past is False
    assert obligation.is_overdue is False
    assert obligation.days_late == 0


# ---------------------------------------------------------------------------
# 5. Query cost
# ---------------------------------------------------------------------------


def _read_every_obligation(user, today):
    rows = wi.annotate_response_obligation(wi.response_obligations(user).order_by("pk"), user)
    return [wi.response_obligation_of(row, user, today) for row in rows]


def test_the_obligation_costs_the_same_however_many_matters_it_holds(specialist, today):
    """Measured against itself rather than against a magic number: one Matter,
    then twenty-one, and the count may not move.

    Two of the twenty already carry a discharge — one a SENT Submission, one a
    ``VÄLJA`` date — so both halves of the predicate are genuinely exercised as
    correlated ``Exists`` inside the one read, and the per-row reading takes its
    answer off the annotation rather than asking again.
    """
    _matter(specialist, deadline=today + timedelta(days=1), title="Uksik kohustus")

    with CaptureQueriesContext(connection) as one:
        assert len(_read_every_obligation(specialist, today)) == 1

    for index in range(22):
        matter = _matter(
            specialist,
            deadline=today + timedelta(days=index + 2),
            title=f"Kohustusega teema {index}",
        )
        if index == 0:
            _send_opinion(matter, specialist)
        if index == 1:
            _mark(matter, state=OpinionSentState.DATE, sent_on=today)

    with CaptureQueriesContext(connection) as many:
        assert len(_read_every_obligation(specialist, today)) == 21

    assert len(many) == len(one)


# ---------------------------------------------------------------------------
# 6. A closed Matter
# ---------------------------------------------------------------------------


def test_a_closed_matter_is_described_honestly_and_queued_nowhere(specialist, today):
    """``open_only=False`` drops the ``is_open`` clause and nothing else.

    Closing a file stops the work. It does not retrospectively answer a ministry,
    and a product that cannot say so has no way to report what was never sent.
    What it must not do is put the closed file back in anybody's queue.
    """
    matter = _matter(specialist, deadline=today - timedelta(days=30))
    close_matter(matter=matter, disposition=Disposition.INITIATIVE_WITHDRAWN, actor=specialist)
    matter.refresh_from_db()

    assert matter.is_open is False
    assert matter.pk not in _obliged(specialist)
    assert matter.pk in _obliged(specialist, open_only=False)

    # Still in no live work population.
    assert matter.pk not in _operational(specialist)
    assert matter.pk not in {item.matter_id for item in wi.work_items(specialist, today=today)}
    assert matter.pk not in wi.work_population_ids(specialist, wi.WORK_OVERDUE, today=today)

    obligation = wi.response_obligation_of(matter, specialist, today)
    assert obligation.is_outstanding is True
    assert obligation.days_late == 30

    matter.refresh_from_db()
    assert matter.response_deadline == today - timedelta(days=30)


def test_open_only_false_drops_the_open_clause_and_nothing_else(specialist, reader, today):
    """An ARCHIVE row and a restricted Matter stay out of both spellings.

    The flag widens the population by exactly one clause. A reading that also
    dropped the record mode would put a decade of imported register rows into a
    report about what Koda still owes, and one that dropped the reader scope
    would be the leak the scope exists to prevent.
    """
    archived = factories.ArchiveMatterFactory(
        owner=specialist,
        response_deadline=today - timedelta(days=30),
    )
    hidden = _matter(
        specialist,
        deadline=today - timedelta(days=30),
        title="Piiratud",
        visibility=Visibility.RESTRICTED,
    )

    for open_only in (True, False):
        assert archived.pk not in _obliged(specialist, open_only=open_only)
        assert hidden.pk not in _obliged(reader, open_only=open_only)
        assert hidden.pk in _obliged(specialist, open_only=open_only)


# ---------------------------------------------------------------------------
# 7. The operational fence
# ---------------------------------------------------------------------------
#
# The whole purpose of this round: the new concept exists and the current work
# surfaces select exactly the rows they selected before.


def test_the_operational_population_is_the_obligation_minus_the_instructed(specialist, today):
    """Stated as the identity the implementation now is, over the full matrix."""
    _matrix(specialist, today)
    extra = _matter(specialist, deadline=today + timedelta(days=3), title="Tulevikus")
    _instruct(extra, specialist, on=None)

    obliged = _obliged(specialist)
    instructed = set(
        Matter.objects.visible_to(specialist)
        .filter(next_actions__status=ActionStatus.OPEN)
        .values_list("pk", flat=True)
    )

    assert _operational(specialist) == obliged - instructed


def test_every_operational_row_is_also_an_obligation(specialist, today):
    """The subset direction, asserted separately: the plan can only ever narrow
    the obligation, never reach a Matter the obligation does not hold."""
    _matrix(specialist, today)

    assert _operational(specialist) <= _obliged(specialist)


def test_the_owner_filter_narrows_both_readings_the_same_way(specialist, other_specialist, today):
    mine = _matter(specialist, deadline=today - timedelta(days=2), title="Minu")
    theirs = _matter(other_specialist, deadline=today - timedelta(days=2), title="Nende")

    assert _obliged(specialist, owner=specialist) == {mine.pk}
    assert _operational(specialist, owner=specialist) == {mine.pk}
    assert _obliged(specialist, owner=other_specialist) == {theirs.pk}
    assert _operational(specialist, owner=other_specialist) == {theirs.pk}


def test_the_work_item_row_is_unchanged_where_no_instruction_exists(specialist, today):
    """The fallback case, end to end: still one response-deadline row, still
    overdue, still carrying the meaning it carried."""
    matter = _matter(specialist, deadline=today - timedelta(days=2))

    (item,) = [
        entry
        for entry in wi.work_items(specialist, today=today, responsible=specialist)
        if entry.matter_id == matter.pk
    ]

    assert item.source_type == wi.SOURCE_RESPONSE_DEADLINE
    assert item.meaning == wi.MEANING_RESPONSE
    assert item.is_overdue is True
    assert item.days_late == 2
