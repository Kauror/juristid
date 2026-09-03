"""What a ``VÄLJA`` mark does to an ``Arvamuse tähtaeg`` (CUR-001, ADR 0059).

What was wrong
--------------

The work model knew two ways a response deadline could stop being work — a
``SENT`` Submission, and an open ``NextAction`` — and neither of them is how the
department actually records that an opinion is finished. It writes that in the
register, in column ``VÄLJA``, and had been doing so for sixteen years.

So the product said two things at once about the same 204 register rows: fifteen
opinions in preparation on *Arvamusi koostamisel*, and eighty-seven response
deadlines overdue. Seventy-seven of the eighty-seven were files whose register
row already said the opinion work was over.

What this file holds
--------------------

The three readings of column F and what each one does:

``DATE``            the opinion went out that day     → discharges
``NOT_SENT``        a decision not to send one        → discharges
``BLANK``           still being worked on             → does **not**
``RECORDED_OTHER``  a cell nobody has read            → does **not**

A good half of what is asserted here is again what did *not* happen: no
``Submission`` appears, no opinion statistic moves, no column is written, and a
deadline the register discharged comes straight back the moment a newer snapshot
blanks the cell — which is the property that makes discharging on a spreadsheet
cell safe at all.

The fourth reading is the one with no production rows behind it, and it is
tested hardest. ``RECORDED_OTHER`` is the difference between asking *is anything
written* and asking *is the work finished*, and it is the only guard standing
between the two.

Every date is relative to today, for the reason
`test_response_deadline_work` gives: a test written around a production date
passes for a fortnight and then fails for reasons nobody can reproduce.
"""

from __future__ import annotations

import hashlib
from datetime import date, timedelta
from typing import Any

import pytest
from django.urls import reverse
from django.utils import timezone

from app.core.enums import Visibility
from app.legacy_import.current_state import CurrentRegisterState, RegisterCurrency
from app.legacy_import.models import MatterSourceReference
from app.legacy_import.register_semantics import (
    OPINION_WORK_COMPLETE_STATES,
    OpinionSentState,
)
from app.matters import overview as ov
from app.matters import selectors
from app.matters import work_items as wi
from app.matters.services import create_matter
from app.submissions.enums import SubmissionStatus
from app.submissions.models import Submission
from app.submissions.services import (
    attach_final_evidence,
    create_submission,
    mark_submission_sent,
)
from app.workflow.enums import ActionKind, DateSemantics
from app.workflow.services import set_next_action

pytestmark = pytest.mark.django_db

PDF = b"%PDF-1.4 arvamus"

TITLE = "Registri margisega teema"


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
    """One register row saying what ``VÄLJA`` holds for this Matter.

    Built directly rather than by running a cutover, because what is under test
    is how the *read model* treats a derived row — not how the row is derived,
    which `test_current_register_refresh` already owns.

    ``opinion_sent_recorded`` is set from the state rather than passed, because
    the two are one cell read twice and the database refuses to let them
    disagree (``legacy_register_opinion_sent_presence_agrees``). Deriving it
    here means a test cannot accidentally construct a row production could
    never hold.

    ``continues_under_reference`` follows the currency for the same reason: a
    ``SUPERSEDED`` row that names no successor is rejected by
    ``legacy_register_continuation_only_when_superseded``, and every other
    currency is rejected if it names one.
    """
    digest = hashlib.sha256(b"cur-001-test-snapshot").hexdigest()
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


def _outstanding(user) -> set[Any]:
    return set(wi.outstanding_response_deadlines(user).values_list("pk", flat=True))


def _is_outstanding(matter, user) -> bool:
    return matter.pk in _outstanding(user)


# ---------------------------------------------------------------------------
# The premise
# ---------------------------------------------------------------------------


def test_only_two_of_the_four_readings_mean_the_work_is_finished():
    """Asserted rather than assumed: the whole file rests on this set.

    ``RECORDED_OTHER`` being absent is the decision — a non-blank cell is not
    the test, an approved completion state is (ADR 0059 §2).
    """
    assert OPINION_WORK_COMPLETE_STATES == {OpinionSentState.DATE, OpinionSentState.NOT_SENT}
    assert OpinionSentState.RECORDED_OTHER not in OPINION_WORK_COMPLETE_STATES
    assert OpinionSentState.BLANK not in OPINION_WORK_COMPLETE_STATES


# ---------------------------------------------------------------------------
# The four readings
# ---------------------------------------------------------------------------


def test_a_blank_valja_leaves_the_deadline_outstanding(specialist, today):
    """Case 1. Nothing recorded, nothing sent, nobody instructed: real work."""
    matter = _matter(specialist, deadline=today - timedelta(days=30))
    _mark(matter, state=OpinionSentState.BLANK)

    assert _is_outstanding(matter, specialist) is True


def test_a_sent_submission_still_discharges_the_deadline(specialist, today):
    """Case 2. The pre-existing rule, unchanged by this record."""
    matter = _matter(specialist, deadline=today - timedelta(days=30))
    _mark(matter, state=OpinionSentState.BLANK)
    _send_opinion(matter, specialist)

    assert _is_outstanding(matter, specialist) is False


def test_a_valja_date_discharges_the_deadline(specialist, today):
    """Case 3. The register says the opinion went out; no Submission needed."""
    matter = _matter(specialist, deadline=today - timedelta(days=30))
    _mark(matter, state=OpinionSentState.DATE, sent_on=today - timedelta(days=28))

    assert not Submission.objects.filter(matter=matter).exists()
    assert _is_outstanding(matter, specialist) is False


def test_ei_saatnud_discharges_the_deadline(specialist, today):
    """Case 4. A decision not to send finishes the work as surely as sending.

    This is the half a date-only rule would strand: no opinion will ever be
    sent, so no action a lawyer could take would ever clear the alarm.
    """
    matter = _matter(specialist, deadline=today - timedelta(days=30))
    _mark(matter, state=OpinionSentState.NOT_SENT)

    assert _is_outstanding(matter, specialist) is False


def test_an_unreadable_valja_mark_does_not_discharge_the_deadline(specialist, today):
    """Case 5. The guard that makes this a state set rather than a boolean.

    ``opinion_sent_recorded`` is **true** on this row — something is written —
    and the deadline stays outstanding anyway. A rule keyed on presence would
    fail here, which is exactly why it is not the rule.
    """
    matter = _matter(specialist, deadline=today - timedelta(days=30))
    state = _mark(matter, state=OpinionSentState.RECORDED_OTHER)

    assert state.opinion_sent_recorded is True
    assert _is_outstanding(matter, specialist) is True


def test_an_open_next_action_still_outranks_everything(specialist, today):
    """Case 6. ADR 0050's precedence, unmoved by ADR 0059."""
    matter = _matter(specialist, deadline=today - timedelta(days=200))
    _mark(matter, state=OpinionSentState.BLANK)
    set_next_action(
        matter=matter,
        text="Jalgin",
        kind=ActionKind.MONITOR,
        date_semantics=DateSemantics.REVIEW_ON,
        target_date=today + timedelta(days=40),
        actor=specialist,
    )

    assert _is_outstanding(matter, specialist) is False


# ---------------------------------------------------------------------------
# What the register may not do
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "state", [OpinionSentState.DATE, OpinionSentState.NOT_SENT, OpinionSentState.RECORDED_OTHER]
)
def test_a_valja_mark_never_creates_a_submission(specialist, today, state):
    """Case 7. No reading of column F may produce canonical outbound evidence."""
    matter = _matter(specialist, deadline=today - timedelta(days=30))
    _mark(matter, state=state, sent_on=today - timedelta(days=28))

    # Read the population, because a lazy queryset that never ran would pass
    # this assertion without ever exercising the rule.
    _outstanding(specialist)

    assert Submission.objects.filter(matter=matter).count() == 0


def test_a_valja_mark_does_not_move_canonical_opinion_statistics(specialist, today):
    """Case 8. Opinion counts come from ``Submission`` and only from it.

    Counted across the whole database rather than on the Matter, because the
    failure this guards is a rule that quietly makes a register row *look* like
    a sent opinion to something that aggregates.
    """
    before = Submission.objects.filter(status=SubmissionStatus.SENT).count()

    discharged = _matter(specialist, deadline=today - timedelta(days=30), title="Kuupaevaga")
    _mark(discharged, state=OpinionSentState.DATE, sent_on=today - timedelta(days=28))
    refused = _matter(specialist, deadline=today - timedelta(days=30), title="Ei saatnud")
    _mark(refused, state=OpinionSentState.NOT_SENT)

    assert _is_outstanding(discharged, specialist) is False
    assert _is_outstanding(refused, specialist) is False
    assert Submission.objects.filter(status=SubmissionStatus.SENT).count() == before


def test_the_deadline_column_is_never_written(specialist, today):
    """The stored fact survives being discharged, exactly as ADR 0050 requires."""
    due = today - timedelta(days=30)
    matter = _matter(specialist, deadline=due)
    _mark(matter, state=OpinionSentState.DATE, sent_on=today - timedelta(days=28))

    assert _is_outstanding(matter, specialist) is False
    matter.refresh_from_db()
    assert matter.response_deadline == due


# ---------------------------------------------------------------------------
# Which register row may speak
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("state", sorted(OPINION_WORK_COMPLETE_STATES))
@pytest.mark.parametrize(
    "currency",
    [RegisterCurrency.RETIRED, RegisterCurrency.SUPERSEDED, RegisterCurrency.REVIEW_REQUIRED],
)
def test_a_register_row_that_is_not_current_discharges_nothing(specialist, today, state, currency):
    """Case 10. Thousands of retired rows carry a completion mark.

    They describe a finished file rather than live work, and a Matter the
    application still holds open must not be discharged by one.
    """
    matter = _matter(specialist, deadline=today - timedelta(days=30))
    _mark(matter, state=state, sent_on=today - timedelta(days=28), currency=currency)

    assert _is_outstanding(matter, specialist) is True


def test_a_matter_with_no_register_row_gets_no_discharge(specialist, today):
    """Case 11. A file created here has no ``VÄLJA`` to speak for it."""
    matter = _matter(specialist, deadline=today - timedelta(days=30))

    assert not CurrentRegisterState.objects.filter(matter=matter).exists()
    assert _is_outstanding(matter, specialist) is True


# ---------------------------------------------------------------------------
# The source of truth is the snapshot, and it can change its mind
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("state", sorted(OPINION_WORK_COMPLETE_STATES))
def test_a_newer_snapshot_blanking_the_cell_restores_the_deadline(specialist, today, state):
    """Case 9. The property that makes this rule safe to apply at all.

    A discharge is a *reading*, not a write, so withdrawing the source
    withdraws the discharge on the very next read — no backfill, no operator
    step, and nothing to reconcile. Asserted by rewriting the derived row the
    way a refresh does, and re-asking.
    """
    matter = _matter(specialist, deadline=today - timedelta(days=30))
    row = _mark(matter, state=state, sent_on=today - timedelta(days=28))
    assert _is_outstanding(matter, specialist) is False

    row.opinion_sent_state = OpinionSentState.BLANK
    row.opinion_sent_recorded = False
    row.opinion_sent_date = None
    row.save(update_fields=["opinion_sent_state", "opinion_sent_recorded", "opinion_sent_date"])

    assert _is_outstanding(matter, specialist) is True
    matter.refresh_from_db()
    assert matter.response_deadline is not None


# ---------------------------------------------------------------------------
# Authorization
# ---------------------------------------------------------------------------


def test_the_new_rule_cannot_disclose_a_restricted_matter(specialist, reader, today):
    """Case 16. The subquery is reader-blind and may only ever remove a row.

    A RESTRICTED Matter is invisible to a READER before the rule and after it,
    whichever way the register reads — so the difference between the two
    populations discloses nothing.
    """
    matter = _matter(
        specialist, deadline=today - timedelta(days=30), visibility=Visibility.RESTRICTED
    )
    _mark(matter, state=OpinionSentState.BLANK)

    assert matter.pk in _outstanding(specialist)
    assert matter.pk not in _outstanding(reader)

    CurrentRegisterState.objects.filter(matter=matter).update(
        opinion_sent_state=OpinionSentState.DATE, opinion_sent_recorded=True
    )

    assert matter.pk not in _outstanding(specialist)
    assert matter.pk not in _outstanding(reader)


# ---------------------------------------------------------------------------
# Ülevaade: the count and the list behind it
# ---------------------------------------------------------------------------


def test_the_area_rail_marks_the_same_rows_the_work_model_does(specialist, today, stage):
    """Case 12, by identity rather than by count.

    Three Matters in one Valdkond, all with a passed deadline, one discharged
    by a date, one by *ei saatnud* and one outstanding. The caret's rows must
    mark exactly the row the shared work model calls late — the divergence that
    let this rail show 192 üle against a canonical 87.
    """
    from app.taxonomy.models import PolicyArea

    area = PolicyArea.objects.first()
    assert area is not None, "the reference taxonomy is expected to be loaded"

    outstanding = _matter(specialist, deadline=today - timedelta(days=30), title="Koostamisel")
    _mark(outstanding, state=OpinionSentState.BLANK)
    dated = _matter(specialist, deadline=today - timedelta(days=30), title="Kuupaevaga")
    _mark(dated, state=OpinionSentState.DATE, sent_on=today - timedelta(days=28))
    refused = _matter(specialist, deadline=today - timedelta(days=30), title="Ei saatnud")
    _mark(refused, state=OpinionSentState.NOT_SENT)
    for matter in (outstanding, dated, refused):
        matter.policy_areas.add(area)

    items = wi.work_items(specialist, today=today)
    page = ov.build_overview(specialist, today=today, items=items, show_empty_areas=True)
    rows = [row for row in page.areas if row.key == area.key]
    assert rows, "the seeded area should carry rows"
    lines = {line.matter.pk: line.is_overdue for line in rows[0].matters}

    model_says = {
        item.matter_id
        for item in items
        if item.source_type == wi.SOURCE_RESPONSE_DEADLINE and item.is_overdue
    }

    assert lines.get(outstanding.pk) is True
    assert lines.get(dated.pk) is False
    assert lines.get(refused.pk) is False
    assert {pk for pk, late in lines.items() if late} == model_says & set(lines)


# ---------------------------------------------------------------------------
# The Matter header (UX-010)
# ---------------------------------------------------------------------------


def test_the_header_calls_an_outstanding_deadline_late(specialist, today):
    """Case 13. A real missed deadline still says so."""
    matter = _matter(specialist, deadline=today - timedelta(days=30))
    _mark(matter, state=OpinionSentState.BLANK)

    deadline = selectors.active_deadline(matter, specialist, today=today)

    assert deadline is not None
    assert deadline.is_past is True
    assert deadline.is_overdue is True
    assert deadline.days_late == 30


@pytest.mark.parametrize(
    ("state", "phrase"),
    [
        (OpinionSentState.DATE, "väljasaadetuks"),
        (OpinionSentState.NOT_SENT, "arvamust ei saadetud"),
    ],
)
def test_the_header_does_not_call_a_discharged_deadline_late(
    client, specialist, today, state, phrase
):
    """Cases 14 and 15. No «p üle», and the reason stated on the page.

    Both halves in one test on purpose: a page that silently stopped calling a
    file late would pass the first assertion and be worse than the bug, because
    a lawyer would have no way to find out why.
    """
    matter = _matter(specialist, deadline=today - timedelta(days=30))
    _mark(matter, state=state, sent_on=today - timedelta(days=28))

    deadline = selectors.active_deadline(matter, specialist, today=today)
    assert deadline is not None
    assert deadline.is_past is True
    assert deadline.is_overdue is False

    client.force_login(specialist)
    response = client.get(reverse("matters:matter_detail", kwargs={"pk": matter.pk}))
    body = response.content.decode()

    assert response.status_code == 200
    assert "p üle" not in body
    assert phrase in body
    # The register fact, never an evidence claim: Koda cannot show the document.
    assert "Koja arvamus saadetud" not in body


def test_a_canonical_submission_silences_the_weaker_register_sentence(client, specialist, today):
    """Where Koda can show what it sent, the register's date is not repeated.

    Two sentences about one event, one of which is evidence and one of which is
    a spreadsheet cell, is how a reader ends up trusting the wrong one.
    """
    matter = _matter(specialist, deadline=today - timedelta(days=30))
    _mark(matter, state=OpinionSentState.DATE, sent_on=today - timedelta(days=28))
    _send_opinion(matter, specialist)

    client.force_login(specialist)
    response = client.get(reverse("matters:matter_detail", kwargs={"pk": matter.pk}))
    body = response.content.decode()

    assert response.status_code == 200
    assert "registris märgitud väljasaadetuks" not in body


def test_an_important_date_keeps_its_own_lateness(specialist, today):
    """CUR-001 is about ``response_deadline``. Nothing discharges a milestone.

    The Matter's own deadline is discharged here and the milestone behind it is
    still late — which is the boundary, stated as a case rather than as a
    comment.
    """
    from app.intelligence.services import add_important_date

    matter = _matter(specialist, deadline=today - timedelta(days=90))
    _mark(matter, state=OpinionSentState.DATE, sent_on=today - timedelta(days=88))
    add_important_date(
        matter=matter,
        title="Konsultatsioon lopeb",
        date_value=today - timedelta(days=10),
        period_end=today - timedelta(days=10),
        actor=specialist,
    )

    deadline = selectors.active_deadline(matter, specialist, today=today)

    assert deadline is not None
    assert deadline.label == "Konsultatsioon lopeb"
    assert deadline.is_overdue is True
    assert deadline.days_late == 10
