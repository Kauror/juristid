"""`Arvamuse tähtaeg` as a request with a history (historical-regression round).

**One current deadline, owned by `Matter.response_deadline`.** Everything that
stops being current is written once to `MatterResponseDeadline` with what
happened to it, and the current field is cleared or given its successor. The
two never hold the same fact.

What a person can say, and the only places it changes anything:

* **set** a deadline where there is none — a new request, recorded now;
* **move** the current one — the same request with a new date; the old date
  stays readable as moved;
* **replace** it with a new request — the old one ends with an outcome the
  person chose in the same form: answered, declined, or left unanswered
  (superseded). A new request is never an answer to the old one;
* **resolve** it — answered (naming a sent `Koja arvamus`, or explaining in
  words where nothing was sent through Juristid), declined, or withdrawn;
* **clear** it — withdrawn, unless the person says it was answered or declined;
* **reopen a closed file** — the deadline it closed with is carried forward
  only when the person says so; otherwise it ends as `CLOSED`.

**Legacy reading kept.** A deadline set before this module existed, or by an
importer, has no `response_requested_at` and keeps ADR 0059's reading: any sent
opinion or the register's `VÄLJA` discharges it (`work_items._discharge_exists`).
A deadline set through these functions is discharged only by an explicit answer
that names it, so an opinion sent for an earlier request never answers a later
one. Nothing infers a link from dates, filenames or «the latest opinion».

An answer without a sent opinion creates no `Submission` and counts in no
statistic: it is a note on the obligation, nothing more.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import date
from typing import Any

from django.db import transaction
from django.utils import timezone

from app.audit.enums import ChangeEventType
from app.audit.services import record_change_event
from app.core.dates import format_estonian_date
from app.core.errors import DomainError
from app.matters.enums import ResponseDeadlineChange, ResponseDeadlineOutcome
from app.matters.locks import lock_matter_for_write
from app.matters.models import Matter, MatterResponseDeadline

#: The outcomes a person may give a deadline a new request replaces.
REPLACED_OUTCOMES: tuple[str, ...] = (
    ResponseDeadlineOutcome.ANSWERED,
    ResponseDeadlineOutcome.NOT_ANSWERING,
    ResponseDeadlineOutcome.SUPERSEDED,
)

#: The outcomes `Lõpeta tähtaeg` and an emptied field may record.
RESOLVED_OUTCOMES: tuple[str, ...] = (
    ResponseDeadlineOutcome.ANSWERED,
    ResponseDeadlineOutcome.NOT_ANSWERING,
    ResponseDeadlineOutcome.CANCELLED,
)

CHANGE_NEEDS_A_MEANING = "Vali, kas sama küsimise tähtaeg muutus või tuli uus arvamuse küsimine."
REPLACED_NEEDS_AN_OUTCOME = (
    "Uus küsimine asendab praeguse. Vali, mis sai praegusest küsimisest "
    "(vastatud, otsustati mitte vastata või jääb vastamata)."
)
ANSWER_NEEDS_A_BASIS = (
    "Vastatud tähtaja juurde vali saadetud Koja arvamus või kirjuta, kuidas ja kus vastati."
)
STALE_DEADLINE_REFUSAL = "Arvamuse tähtaeg on vahepeal muutunud. Laadi leht uuesti ja vaata üle."
NO_CURRENT_DEADLINE = "Teemal ei ole praegust arvamuse tähtaega."
RESOLVE_NEEDS_AN_OUTCOME = (
    "Tähtaja lõpetamiseks vali: vastatud, otsustati mitte vastata või tühistatud."
)
FOREIGN_SUBMISSION = "Valitud arvamus ei kuulu sellele teemale."
UNSENT_SUBMISSION = "Vastuseks saab valida ainult välja saadetud Koja arvamuse."
UNKNOWN_OUTCOME = "Tundmatu tulemus."


def deadline_revision(matter: Matter) -> str:
    """A token for the current deadline as a form saw it.

    The date and the request time together: moving a deadline keeps the request
    time and changes the date, replacing one changes both, so a tab drawn before
    either is refused rather than ending a deadline it never showed.
    """
    raw = f"{matter.response_deadline or ''}|{matter.response_requested_at or ''}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def _check_revision(locked: Matter, expected_revision: str | None) -> None:
    # An absent token is not a conflict: a caller that never drew the deadline
    # (an importer, a test) states the change directly, as `set_matter_dates`
    # always allowed.
    if expected_revision and deadline_revision(locked) != expected_revision:
        raise DomainError(STALE_DEADLINE_REFUSAL)


def _checked_submission(locked: Matter, submission: Any) -> Any:
    if submission is None:
        return None
    from app.submissions.enums import SubmissionStatus

    if submission.matter_id != locked.pk:
        raise DomainError(FOREIGN_SUBMISSION)
    if submission.status != SubmissionStatus.SENT:
        raise DomainError(UNSENT_SUBMISSION)
    return submission


def _end_current(
    *,
    locked: Matter,
    outcome: str,
    actor: Any,
    submission: Any = None,
    note: str = "",
    next_deadline: date | None = None,
) -> MatterResponseDeadline:
    """Write the current deadline into history. The caller changes the field."""
    if outcome not in ResponseDeadlineOutcome.values:
        raise DomainError(UNKNOWN_OUTCOME)
    note = (note or "").strip()
    if outcome != ResponseDeadlineOutcome.ANSWERED and submission is not None:
        submission = None
    if outcome == ResponseDeadlineOutcome.ANSWERED and submission is None and not note:
        raise DomainError(ANSWER_NEEDS_A_BASIS)
    submission = _checked_submission(locked, submission)
    if locked.response_deadline is None:
        raise DomainError(NO_CURRENT_DEADLINE)
    row = MatterResponseDeadline.objects.create(
        matter=locked,
        deadline=locked.response_deadline,
        requested_at=locked.response_requested_at,
        outcome=outcome,
        submission=submission,
        note=note,
        next_deadline=next_deadline,
        ended_at=timezone.now(),
        ended_by=actor if getattr(actor, "pk", None) else None,
    )
    record_change_event(
        event_type=ChangeEventType.RESPONSE_DEADLINE_ENDED,
        matter=locked,
        actor=actor,
        obj=locked,
        summary=f"Arvamuse tähtaeg {format_estonian_date(row.deadline)}: "
        f"{row.get_outcome_display().lower()}",
        # The date and the outcome; never the opinion, whose visibility is its
        # own (`MatterResponseDeadline.submission`).
        payload={
            "deadline": row.deadline.isoformat(),
            "outcome": outcome,
            "next_deadline": next_deadline.isoformat() if next_deadline else None,
            "has_note": bool(note),
        },
    )
    return row


def _write_field(
    *, locked: Matter, matter: Matter, deadline: date | None, requested_at: Any, actor: Any
) -> None:
    before = locked.response_deadline
    locked.response_deadline = deadline
    locked.response_requested_at = requested_at
    locked.save(update_fields=["response_deadline", "response_requested_at", "updated_at"])
    # The caller's instance reads what was written, as `set_matter_dates` did.
    matter.response_deadline = deadline
    matter.response_requested_at = requested_at
    if before != deadline:
        record_change_event(
            event_type=ChangeEventType.MATTER_DATE_CHANGED,
            matter=locked,
            actor=actor,
            obj=locked,
            payload={
                "deadline_from": before.isoformat() if before else None,
                "deadline_to": deadline.isoformat() if deadline else None,
            },
        )


@transaction.atomic
def change_response_deadline(
    *,
    matter: Matter,
    deadline: date | None,
    actor: Any = None,
    change: str = "",
    previous_outcome: str = "",
    previous_submission: Any = None,
    previous_note: str = "",
    expected_revision: str | None = None,
) -> Matter:
    """Set, move, replace or clear the current `Arvamuse tähtaeg`.

    ``change`` is asked only when a deadline exists and a different date is
    given: `MOVED` keeps the request and records the old date as moved;
    `REPLACED` starts a new request and ends the old one with
    ``previous_outcome``, which the person must choose. Emptying the field ends
    the current deadline as withdrawn, or with the outcome given.
    """
    locked = lock_matter_for_write(matter.pk)
    _check_revision(locked, expected_revision)
    current = locked.response_deadline
    if deadline == current:
        return matter
    now = timezone.now()

    if current is None:
        _write_field(locked=locked, matter=matter, deadline=deadline, requested_at=now, actor=actor)
        return matter

    if deadline is None:
        outcome = previous_outcome or ResponseDeadlineOutcome.CANCELLED
        if outcome not in RESOLVED_OUTCOMES:
            raise DomainError(UNKNOWN_OUTCOME)
        _end_current(
            locked=locked,
            outcome=outcome,
            actor=actor,
            submission=previous_submission,
            note=previous_note,
        )
        _write_field(locked=locked, matter=matter, deadline=None, requested_at=None, actor=actor)
        return matter

    if change == ResponseDeadlineChange.MOVED:
        _end_current(
            locked=locked,
            outcome=ResponseDeadlineOutcome.MOVED,
            actor=actor,
            note=previous_note,
            next_deadline=deadline,
        )
        _write_field(
            locked=locked,
            matter=matter,
            deadline=deadline,
            requested_at=locked.response_requested_at,
            actor=actor,
        )
        return matter

    if change == ResponseDeadlineChange.REPLACED:
        if previous_outcome not in REPLACED_OUTCOMES:
            raise DomainError(REPLACED_NEEDS_AN_OUTCOME)
        _end_current(
            locked=locked,
            outcome=previous_outcome,
            actor=actor,
            submission=previous_submission,
            note=previous_note,
            next_deadline=deadline,
        )
        _write_field(locked=locked, matter=matter, deadline=deadline, requested_at=now, actor=actor)
        return matter

    raise DomainError(CHANGE_NEEDS_A_MEANING)


#: Refused by `+ Lisa → Arvamuse tähtaeg` while a request is still current.
ACTIVE_DEADLINE_EXISTS = (
    "Teemal on juba praegune arvamuse tähtaeg ({date}). Muuda või lõpeta see teema päises."
)
#: The note a pre-tracking deadline is ended with when a new request follows it.
LEGACY_DISCHARGE_NOTE = "Varasema arvestuse järgi vastatud (saadetud arvamus või registri VÄLJA)."


@transaction.atomic
def request_response_deadline(
    *,
    matter: Matter,
    deadline: date,
    actor: Any = None,
    expected_revision: str | None = None,
) -> Matter:
    """`+ Lisa → Arvamuse tähtaeg` — a new request for an opinion on this Matter.

    The ordinary case is a Matter whose earlier request was answered (a sent
    `Koja arvamus` ended it into history) and that is now asked again: the date
    becomes the current deadline with a **fresh** `response_requested_at`, so
    the earlier opinion — linked to the earlier request — never answers this
    one, and the next sent opinion may (`answer_current_deadline_with`).

    **A current request is never overwritten here.** Moving it or replacing it
    are two different facts, and the header's editor is where the person says
    which (`change_response_deadline`); this refuses instead of choosing.

    One exception, and it is a reading the record already makes: a deadline from
    before requests were tracked (no `response_requested_at`) that a sent
    opinion or the register's `VÄLJA` has discharged reads «lõpetatud» in the
    header. It is not current work, so it is written to history as answered —
    with a note saying how it was answered, and no inferred link to any opinion
    — and the new request follows it.
    """
    from app.matters.locks import lock_open_matter_for_business_write
    from app.matters.work_items import response_obligation_of

    locked = lock_open_matter_for_business_write(matter.pk)
    _check_revision(locked, expected_revision)
    if locked.response_deadline is not None:
        legacy_settled = (
            locked.response_requested_at is None
            and not response_obligation_of(locked, actor).is_outstanding
        )
        if not legacy_settled:
            raise DomainError(
                ACTIVE_DEADLINE_EXISTS.format(date=format_estonian_date(locked.response_deadline))
            )
        _end_current(
            locked=locked,
            outcome=ResponseDeadlineOutcome.ANSWERED,
            actor=actor,
            note=LEGACY_DISCHARGE_NOTE,
            next_deadline=deadline,
        )
    _write_field(
        locked=locked, matter=matter, deadline=deadline, requested_at=timezone.now(), actor=actor
    )
    return matter


@transaction.atomic
def resolve_response_deadline(
    *,
    matter: Matter,
    outcome: str,
    actor: Any = None,
    submission: Any = None,
    note: str = "",
    expected_revision: str | None = None,
) -> MatterResponseDeadline:
    """`Lõpeta tähtaeg` — the current deadline was answered, declined or withdrawn."""
    locked = lock_matter_for_write(matter.pk)
    _check_revision(locked, expected_revision)
    if locked.response_deadline is None:
        raise DomainError(NO_CURRENT_DEADLINE)
    if outcome not in RESOLVED_OUTCOMES:
        raise DomainError(RESOLVE_NEEDS_AN_OUTCOME)
    row = _end_current(
        locked=locked, outcome=outcome, actor=actor, submission=submission, note=note
    )
    _write_field(locked=locked, matter=matter, deadline=None, requested_at=None, actor=actor)
    return row


def answer_current_deadline_with(
    *, locked: Matter, matter: Matter, submission: Any, actor: Any, expected_revision: str | None
) -> MatterResponseDeadline:
    """Inside an opinion's own save: the opinion answers the current deadline.

    The caller holds the Matter's lock and the operation; this checks the
    deadline the form named is still the current one and ends it as answered by
    this opinion.
    """
    _check_revision(locked, expected_revision)
    if locked.response_deadline is None:
        raise DomainError(NO_CURRENT_DEADLINE)
    row = _end_current(
        locked=locked,
        outcome=ResponseDeadlineOutcome.ANSWERED,
        actor=actor,
        submission=submission,
    )
    _write_field(locked=locked, matter=matter, deadline=None, requested_at=None, actor=actor)
    return row


def end_deadline_left_by_closure(*, locked: Matter, matter: Matter, actor: Any) -> None:
    """On reopening: the deadline the file closed with is not current work again.

    Called by the reopening act unless the person carried the deadline forward.
    Closing never answered it, so it ends as `CLOSED`, not as answered.
    """
    if locked.response_deadline is None:
        return
    _end_current(locked=locked, outcome=ResponseDeadlineOutcome.CLOSED, actor=actor)
    _write_field(locked=locked, matter=matter, deadline=None, requested_at=None, actor=actor)


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class EndedDeadline:
    """One past `Arvamuse tähtaeg`, as a reader may be shown it."""

    deadline: date
    display: str
    outcome: str
    outcome_label: str
    next_display: str
    note: str
    #: The answering opinion, only when this reader may see it.
    submission: Any
    #: Answered by an opinion that has since been withdrawn, with no words of
    #: the person's own to stand on: the answer needs looking at again.
    needs_review: bool
    ended_at: Any


def ended_deadlines(matter: Matter, user: Any) -> list[EndedDeadline]:
    """The Matter's past deadlines, newest first, for one reader."""
    from app.submissions.enums import SubmissionStatus
    from app.submissions.models import Submission

    rows = list(
        MatterResponseDeadline.objects.filter(matter=matter).order_by("-ended_at", "-created_at")
    )
    linked = {row.submission_id for row in rows if row.submission_id is not None}
    visible = (
        {
            submission.pk: submission
            for submission in Submission.objects.visible_to(user).filter(pk__in=linked)
        }
        if linked
        else {}
    )
    # Whether a linked opinion still stands is a fact about the obligation, read
    # without the reader's scope; only *which* opinion is withheld from a reader
    # who may not see it.
    standing = (
        set(
            Submission.objects.filter(pk__in=linked, status=SubmissionStatus.SENT).values_list(
                "pk", flat=True
            )
        )
        if linked
        else set()
    )
    ended: list[EndedDeadline] = []
    for row in rows:
        withdrawn = row.submission_id is not None and row.submission_id not in standing
        ended.append(
            EndedDeadline(
                deadline=row.deadline,
                display=format_estonian_date(row.deadline),
                outcome=row.outcome,
                outcome_label=row.get_outcome_display(),
                next_display=format_estonian_date(row.next_deadline) if row.next_deadline else "",
                note=row.note,
                submission=visible.get(row.submission_id) if row.submission_id else None,
                # Told only to a reader who may see the opinion: to anybody
                # else a withdrawal would disclose that it existed.
                needs_review=withdrawn and not row.note and row.submission_id in visible,
                ended_at=row.ended_at,
            )
        )
    return ended


def answerable_submissions(matter: Matter, user: Any) -> list[Any]:
    """The sent opinions on this Matter a person may name as an answer."""
    from app.submissions.enums import SubmissionStatus
    from app.submissions.models import Submission

    return list(
        Submission.objects.visible_to(user)
        .filter(matter=matter, status=SubmissionStatus.SENT)
        .prefetch_related("recipients")
        .order_by("-sent_at", "-created_at")
    )
