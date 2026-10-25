"""The Teema workspace's use cases: one intention, one save.

The page above these functions asks two questions and no others. *What did you
do about the thing you were supposed to do?* — which is `PRAEGUNE TEGEVUS`, and
which has exactly one answer and exactly one button. And *what do you want to
add to this file?* — which is `LISA TEEMALE`, where the person first says what
kind of thing they are recording and only then answers the questions that thing
asks.

This reverses the composer's central claim (docs/adr/0074 §3): that a
professional update is one act, so one form with five optional panels and one
shared `Salvesta` is the honest shape for it. It was a reasonable claim and it
did not survive contact. A single save that could mean *note* **and** *next
step* **and** *deadline* **and** *consultation* **and** *win* **and**
*commencement* **and** *closure* is a save whose meaning the person has to
reconstruct from which boxes they happened to fill in, and the commonest thing
anybody does here — finish the task the page is telling them to finish — had no
operation of its own at all: they wrote a note in one place and pressed
`✓ Tehtud` in another, two saves for one act, in either order, with nothing
tying them together (docs/adr/0075 §2).

So every function here is one business operation, atomic, named after what a
person means by it, and calling the existing domain services underneath. None of
them writes a model field: `complete_next_action`, `set_next_action_for_new_work`,
`add_entry`, `add_engagement`, `add_important_date`, `add_effective_date`,
`add_confirmed_work_victory` and `close_matter` are unchanged and keep their own
invariants, audit rows and authorization. What is new is the orchestration —
that a fact, its files and the links between them land together or not at all.

These operations replaced the single composer save, which wrote any mix of an
entry, a step, a closure and the rest in one transaction. `compose_update`, its
form and its route were retired with ENG-050A2 (docs/adr/0075 §11, amended);
the primitives above are what these operations call.

**Every operation that adds content starts by locking the Matter and refusing a
closed one** (`lock_open_matter_for_business_write`). Not because the page shows
these forms on a closed Matter — it does not — but because a page is not a
boundary. A browser that had the Teema open before somebody else closed it still
has every field and every button, and its POST reaches a server with no memory
of which page it came from. Hiding the forms on a fresh GET is the right thing
to do and it decides nothing (docs/adr/0075 §12, R2-02).

One operation here does not take that guard, and may not.
`correct_matter_website_overview` corrects an address the file already records:
closure means no new business content, never that a fact recorded wrongly must
stay wrong, which is the rule `edit_entry` has kept since docs/adr/0075 §12.
"""

from __future__ import annotations

import uuid as uuid_module
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from django.db import transaction
from django.utils import timezone

from app.audit.operations import composer_operation
from app.core.dates import start_of_local_day
from app.core.derived_visibility import restriction_inherited_from
from app.core.enums import Visibility
from app.core.errors import DomainError
from app.documents.enums import DocumentRole
from app.documents.models import Document
from app.documents.services import capture_supporting_evidence
from app.matters import engagement_replies
from app.matters.entry_enums import EntryKind
from app.matters.enums import (
    EngagementKind,
    ExternalPositionProvenance,
    WebsiteOverviewKind,
    WebsiteOverviewStatus,
)
from app.matters.initiative import is_koda_initiative
from app.matters.locks import (
    lock_open_matter_for_business_write,
    lock_submission_for_evidence_integrity,
)
from app.matters.models import Entry, Matter, MatterEngagement, MatterWebsiteOverview
from app.matters.publication_kind import (
    ENGAGEMENT_OVERVIEW_LINK_IS_NEWS,
    classify_publication_url,
    same_publication_url,
)
from app.matters.services import (
    add_engagement,
    add_entry,
    cancel_website_overview,
    close_matter,
    complete_engagement_feedback,
    correct_website_overview_link,
    plan_website_overview,
    publish_website_overview,
)
from app.workflow.dates import DAY_OUT_OF_RANGE, is_supported_day
from app.workflow.enums import ActionStatus, DatePrecision
from app.workflow.models import NextAction
from app.workflow.services import (
    NEXT_STEP_NEEDS_SENTENCE,
    complete_next_action,
    set_next_action_for_new_work,
)
from app.workflow.stage_flow import is_terminal as is_terminal_stage

#: Refused when the step the form was rendered against is no longer the one that
#: is open. Named because two surfaces print it and a test asserts on it.
STALE_ACTION_REFUSAL = (
    "Praegune tegevus on vahepeal muutunud. Värskenda lehte ja vaata, mis on nüüd pooleli."
)

ALREADY_SAVED = "See vorm on juba salvestatud. Laadi leht uuesti, et näha salvestatut."
ROUND_NOT_OPEN = "Valitud kaasamise tagasiside ootamine on juba lõpetatud või seda ei ole."
ROUND_CHANGED = "Valitud kaasamine on vahepeal muutunud. Laadi leht uuesti ja vali uuesti."


def _spend_once(locked_matter: Matter, token: Any, purpose: str) -> None:
    """Spend a drawn form's token, or refuse a second save of the same form.

    Inside the save's transaction and under the Matter's lock, so two presses
    are serialised and the second finds the first's token; a refusal later in
    the same save rolls the token back with everything else.

    `Koja arvamus` and, since owner decision D3 of 2026-10-17, every compact
    `LISA TEEMALE` door: `Alusta kaasamist`, `Lisa tagasiside`, `Oluline
    tähtaeg`, `Jõustumine`, `Töövõit`, `Muu` and `Kohtumine` (docs/adr/0167,
    amendment of 2026-10-17). ``None`` — a POST that carries no token, which
    no drawn form sends — is not guarded, as before.
    """
    if token is None:
        return
    from django.db import IntegrityError

    from app.matters.models import MatterSaveOnce

    try:
        with transaction.atomic():
            MatterSaveOnce.objects.create(token=token, matter=locked_matter, purpose=purpose)
    except IntegrityError as error:
        raise DomainError(ALREADY_SAVED) from error


def _named_open_rounds(
    locked_matter: Matter, rounds: Sequence[tuple[Any, str]]
) -> list[tuple[MatterEngagement, str]]:
    """The rounds a save names to finish, each still open and as the form drew it.

    Checked under the Matter's lock **before** anything is written. A round of
    another Matter, one already finished or never tracked, or one changed since
    the form was drawn refuses the whole save — never a quiet skip.
    """
    from app.matters.services import engagement_revision_token

    named: list[tuple[MatterEngagement, str]] = []
    for engagement_id, revision in rounds:
        engagement = (
            MatterEngagement.objects.select_for_update(no_key=True)
            .live()
            .filter(matter=locked_matter, pk=engagement_id)
            .first()
        )
        if engagement is None or not engagement.has_open_feedback_wait:
            raise DomainError(ROUND_NOT_OPEN)
        if revision and engagement_revision_token(engagement) != revision:
            raise DomainError(ROUND_CHANGED)
        named.append((engagement, revision))
    return named


#: Refused when one save both names a next step and a `Hetkeseis` that ends the
#: Matter — the closure would cancel the step it was written with (docs/adr/0131 §10).
TERMINAL_STAGE_MAKES_NO_STEP = (
    "Lõpetava hetkeseisuga ei saa järgmist tegevust määrata: teema lõpetatakse."
)


@dataclass
class WorkspaceResult:
    """What one workspace operation wrote.

    A dataclass rather than a tuple: these grow a field when an operation
    learns to write something else,
    and a caller unpacking positionally would silently take the wrong one.
    """

    operation_id: uuid_module.UUID
    entry: Entry | None = None
    action: NextAction | None = None
    record: Any = None
    documents: list[Document] = field(default_factory=list)
    closed: bool = False
    #: Whether a confirmed stage move also dated its phase on `Menetluse kulg`
    #: (`add_procedural_development(date_phase=True)`, docs/adr/0128 §1).
    phase_dated: bool = False
    #: Whether a reply was the first answer to its round and finished the
    #: round's wait (`record_position_reply`, docs/adr/0167 §2).
    wait_finished: bool = False


def _uploads(raw: Any) -> list[Any]:
    """Whatever the form produced, as a list of files.

    ``MultipleFileField`` cleans to a list; a form that was never given files
    cleans to ``None``. Normalising here keeps every caller below from writing
    the same three-line guard.
    """
    if not raw:
        return []
    if isinstance(raw, (list, tuple)):
        return [item for item in raw if item]
    return [raw]


def _named_open_action(*, locked_matter: Matter, action_id: Any) -> NextAction:
    """The open step **this form was drawn against**, under the Matter's lock — or a refusal.

    Every save that finishes the current step names it, and this is the one
    place that name is checked. `Mida tegid?` has always carried a hidden
    `action_id`; `Registreeri arvamus` and `Salvesta ja lõpeta` carry one when
    the person ticks `Märgi praegune tegevus tehtuks` (docs/adr/0126 §2).

    **Named, never found.** A stale tab still showing a step a colleague has
    since finished or replaced would otherwise complete *whatever is open now* —
    a task the author never saw, marked done in their name. So the open step is
    re-read with a row lock inside the caller's transaction, after the Matter
    row, in the order `set_next_action` and `close_matter` take them, and
    anything but the named one is refused outright with nothing written
    (docs/adr/0075 §4).

    **Asked before the caller writes anything.** A Koja arvamus stores its bytes
    in the evidence store as it goes, and a refusal raised after that would roll
    the database back and leave the bytes behind. Holding the Matter's lock from
    here to the completion means the answer cannot change in between: every
    path that moves a step locks the same row.
    """
    current = (
        NextAction.objects.select_for_update(no_key=True)
        .filter(matter=locked_matter, status=ActionStatus.OPEN)
        .first()
    )
    if current is None or str(current.pk) != str(action_id):
        raise DomainError(STALE_ACTION_REFUSAL)
    # Every caller here finishes or rewrites the step generically — `Mida
    # tegid?`, `Muuda`, a ticked `Märgi praegune tegevus tehtuks`. A current
    # `Arvamuse järelkontroll` is finished only with what it found, through
    # `check_opinion_follow_up`, so it is refused here, before a byte is stored
    # (docs/adr/0146 §5).
    if current.follow_up_id is not None:
        from app.workflow.follow_ups import FOLLOW_UP_HAS_ITS_OWN_FORM

        raise DomainError(FOLLOW_UP_HAS_ITS_OWN_FORM)
    return current


def _refuse_an_unconfirmed_closure(
    locked_matter: Matter, stage: Any, *, confirmed: bool | str, finishing: Any = None
) -> bool | str:
    """A save whose `Uus hetkeseis` would close a Matter with live work, asked first.

    `close_matter` asks the same question under the same lock as the save ends;
    asking it here as well means a refusal comes *before* the save stores any
    file, so nothing is left behind in the evidence store (docs/adr/0146 §8,
    docs/adr/0152). ``finishing`` is the step this same save completes, which
    is done rather than outstanding by the time the Matter closes.

    Returns the confirmation to hand on to `close_matter`: ``True`` once the
    person's digest was verified here, under the lock this save keeps until it
    closes, so the step this save finishes is not read there as changed work
    (docs/adr/0152 §7); otherwise what was posted.
    """
    from app.workflow.follow_ups import refuse_unconfirmed_closure

    if locked_matter.is_open and is_terminal_stage(getattr(stage, "key", None)):
        if refuse_unconfirmed_closure(locked_matter, confirmed=confirmed, finishing=finishing):
            return True
    return confirmed


def completion_visibility_override(action: NextAction) -> str:
    """The ``visibility_override`` of the `Entry` that records ``action`` as done.

    That `Entry` is the account of the work done for this exact action, so it is
    never less restricted than the action (docs/adr/0138): an action restricted
    below its Matter passes `RESTRICTED` on, and an action with no stricter
    override passes nothing, so the `Entry` inherits the Matter as it always
    did. Read off the action's own column only — never its text, owner, plan
    step, date or files — and copied at creation, not joined afterwards.
    """
    inherited = action.visibility_override or ""
    return "" if inherited == Visibility.NORMAL else inherited


@transaction.atomic
def complete_current_action(
    *,
    matter: Matter,
    author: Any,
    action_id: Any,
    body: str,
    uploads: Sequence[Any] = (),
    next_text: str = "",
    next_date: Any = None,
    stage: Any = None,
    follow_ups_confirmed: bool | str = False,
) -> WorkspaceResult:
    """`PRAEGUNE TEGEVUS` — what I did, and therefore that the step is done.

    **One operation, not two.** Saving the result of the current task *is* the
    completion of it. There is no `Märgi tehtuks`, no second confirmation and no
    order in which a person can do half of this: either the description, the
    files and the completion all land, or none of them does. The old page had
    them as two independent saves in two places, which is why a Matter could
    carry a note about finishing something beside a step that was still open
    (docs/adr/0075 §3).

    **The action is named by the form, and re-read under a lock.** This is the
    stale-tab case and it is not hypothetical: a lawyer with the same Matter open
    in two tabs finishes the task in one, the other tab is still showing the
    step it replaced, and pressing `Salvesta` there would otherwise complete
    *whatever is open now* — a different task, marked done by somebody who never
    saw it. The Matter row is locked in the same order `set_next_action` and
    `close_matter` lock it, the open action is re-read inside that lock, and the
    operation is refused outright if it is not the one the form was rendered
    against. Refused rather than redirected: there is no writing this result
    against a task nobody chose (docs/adr/0075 §4).

    **`Mida tegid?` is required**, and enforced on the form rather than here
    only because that is where a person sees it; `add_entry` refuses an empty
    body regardless. A file is supplementary evidence and never a substitute for
    the description — filing "arvamus.pdf" as an account of what somebody did is
    the application putting words in a lawyer's mouth (docs/adr/0075 §3).

    **`Järgmine tegevus` — and what comes next, in the same save** (docs/adr/0140
    §2). ``next_text`` is the next action in the person's own words, through
    `set_next_action_for_new_work`, tied to no plan step; empty, and no step is
    opened. Nothing is ever chosen for the person: the plan's next suggestion is
    not started because this one finished (docs/adr/0133 §4).

    **`Uus hetkeseis` — and where the file now stands, in the same save**
    (docs/adr/0140 §3). ``stage`` goes through `stage_transition`, exactly as
    `+ Lisa · Tavaline` moves it: the period current before the move keeps the
    note, its files and the completion, because the work was done there; the
    move writes its own `MATTER_STAGE_CHANGED`; the stage the file already holds
    moves nothing; and a stage that ends the Matter closes it on the way out —
    so it is refused beside a next action, which the closure would cancel
    (`TERMINAL_STAGE_MAKES_NO_STEP`, docs/adr/0131 §10).

    **The note is restricted with the step it finishes** (docs/adr/0138). It
    is that step's completion record, so a step restricted below its Matter
    gives the note the same restriction — and its files then follow the note
    (docs/adr/0137). The next step chosen in the same save is new work and is
    not restricted by this: it is written by the ordinary creation rule.

    **One transaction, checked before it writes.** The named step is re-read
    under the Matter's lock *before* anything is written, so a stale tab refuses
    the whole save and nothing lands half-done. Then the note, its files, the
    completion (which completes a suggested step the action was started from,
    through `complete_next_action`), the new step and the move, in one
    operation, so
    `Teema käik` reads it as one row: what was done, with the next step under it.
    """
    from app.matters.services import stage_transition

    next_text = (next_text or "").strip()
    if is_terminal_stage(getattr(stage, "key", None)) and next_text:
        raise DomainError(TERMINAL_STAGE_MAKES_NO_STEP)
    # The lock, and the question closure answers, through the one helper every
    # operation in this module now uses. This function had its own copy of both
    # from the start; the copy was correct and it was also the only one, which
    # is what R2-02 turned out to be about (app/matters/locks.py).
    #
    # `no_key=True` inside it is not a detail: this transaction locks the Matter
    # and then *inserts rows that point at it* — an `Entry`, a `Document`, a
    # `DocumentVersion`, several `ChangeEvent`s — which is precisely the shape
    # that turns a plain `FOR UPDATE` into a deadlock.
    locked_matter = lock_open_matter_for_business_write(matter.pk)
    current = _named_open_action(locked_matter=locked_matter, action_id=action_id)
    moves_stage = stage is not None and stage.pk != locked_matter.stage_id
    if moves_stage:
        # The step being finished here is done by the time the Matter closes.
        follow_ups_confirmed = _refuse_an_unconfirmed_closure(
            locked_matter, stage, confirmed=follow_ups_confirmed, finishing=current
        )

    with (
        composer_operation() as operation_id,
        stage_transition(
            matter=locked_matter,
            stage=stage if moves_stage else locked_matter.stage,
            actor=author,
            follow_ups_confirmed=follow_ups_confirmed,
        ) as move,
    ):
        result = WorkspaceResult(operation_id=operation_id, closed=move.closes)
        result.entry = add_entry(
            matter=locked_matter,
            body=body,
            author=author,
            kind=EntryKind.NOTE,
            visibility_override=completion_visibility_override(current),
        )
        result.documents = capture_supporting_evidence(
            matter=locked_matter,
            record=result.entry,
            uploads=_uploads(uploads),
            actor=author,
        )
        # A written `Järgmine tegevus` becomes the current action itself, so the
        # earliest planned one is promoted only when none was written
        # (docs/adr/0143). Nor when this save's stage closes the Matter: the
        # closure cancels every planned row on the way out (§A7), and promoting
        # one first wrote a «→ Järgmine samm» under the save that ended the file.
        complete_next_action(
            action=current, actor=author, promote=not next_text and not move.closes
        )
        result.action = (
            set_next_action_for_new_work(
                matter=locked_matter, text=next_text, target_date=next_date, actor=author
            )
            if next_text
            else None
        )
    return result


@transaction.atomic
def complete_planned_action(
    *,
    matter: Matter,
    author: Any,
    action_id: Any,
    body: str,
    uploads: Sequence[Any] = (),
) -> WorkspaceResult:
    """`✓ Tehtud` on a planned row — what happened, and that this action is done.

    The planned counterpart of `complete_current_action` (docs/adr/0144 §1), and
    the same shape: the person says what actually happened — «Kohtumist ei
    toimunud» is an ordinary answer, and there is no separate «jäta ära» — the
    files that go with it are captured, and the action ends COMPLETED, in one
    operation that `Teema käik` reads as one «✓» row.

    **Only that action.** It is named by the form and re-read under the
    Matter's lock before anything is written; the current action, its date and
    every other planned row stay exactly as they were, and nothing is promoted.
    The note is restricted with the action it finishes (docs/adr/0138).
    """
    from app.workflow.services import finish_planned_action, locked_planned_action

    locked_matter = lock_open_matter_for_business_write(matter.pk)
    planned = locked_planned_action(locked_matter, action_id)
    if planned.follow_up_id is not None:
        # A planned `Arvamuse järelkontroll` is finished with what it found,
        # through `check_opinion_follow_up` — refused here before the note or a
        # file is written (docs/adr/0146 §5).
        from app.workflow.follow_ups import FOLLOW_UP_HAS_ITS_OWN_FORM

        raise DomainError(FOLLOW_UP_HAS_ITS_OWN_FORM)
    with composer_operation() as operation_id:
        result = WorkspaceResult(operation_id=operation_id)
        result.entry = add_entry(
            matter=locked_matter,
            body=body,
            author=author,
            kind=EntryKind.NOTE,
            visibility_override=completion_visibility_override(planned),
        )
        result.documents = capture_supporting_evidence(
            matter=locked_matter,
            record=result.entry,
            uploads=_uploads(uploads),
            actor=author,
        )
        result.action = finish_planned_action(action=planned, actor=author)
    return result


#: How a check's note opens, by outcome: the person's typed choice, in their
#: words, so `Teema käik` reads one «✓» row that says what was found. Never a
#: sentence the person did not choose (docs/adr/0146 §5).
CHECK_NOTE_OPENINGS = {
    "RESPONSE_RECEIVED": "Vastus saabunud.",
    "NO_RESPONSE": "Vastust ei ole — kontrollin uuesti {day}.",
    "MONITORING_ENDED": "Lõpetan jälgimise.",
}


def _check_note(outcome: str, body: str, next_check_on: Any) -> str:
    from app.workflow.dates import format_at_precision

    opening = CHECK_NOTE_OPENINGS[outcome].format(
        day=format_at_precision(next_check_on, DatePrecision.EXACT) if next_check_on else ""
    )
    text = (body or "").strip()
    return f"{opening} {text}" if text else opening


@transaction.atomic
def check_opinion_follow_up(
    *,
    matter: Matter,
    author: Any,
    action_id: Any,
    outcome: str,
    body: str = "",
    next_check_on: Any = None,
    uploads: Sequence[Any] = (),
) -> WorkspaceResult:
    """`✓ Tehtud` on an `Arvamuse järelkontroll` — what the check found (docs/adr/0146 §5).

    One operation: the note, its files (the answer that arrived, say), the
    check's completion with its typed outcome and — for «no answer yet» — the
    next check on the day the lawyer chose. `Teema käik` reads one «✓» row with
    the next check under it.

    * the check is named by the form and re-read under the Matter's lock; a
      stale tab is refused with nothing written;
    * the outcome's own questions — the next day, the reason for ending — are
      asked **before** a file is stored;
    * ``body`` is the answer's summary, or the reason monitoring ends (then
      required); the note opens with the outcome the person chose;
    * the note is restricted with the check, as every completion note is
      (docs/adr/0138);
    * nothing closes the Matter and nothing is invented: «Vastus saabunud»
      creates no task, and the next check is never an automatic 30 days.
    """
    from app.workflow.follow_ups import complete_check, locked_check, validate_outcome

    locked_matter = lock_open_matter_for_business_write(matter.pk)
    check = locked_check(locked_matter, action_id)
    validate_outcome(outcome=outcome, next_check_on=next_check_on, reason=body)
    with composer_operation() as operation_id:
        result = WorkspaceResult(operation_id=operation_id)
        result.entry = add_entry(
            matter=locked_matter,
            body=_check_note(outcome, body, next_check_on),
            author=author,
            kind=EntryKind.NOTE,
            visibility_override=completion_visibility_override(check),
        )
        result.documents = capture_supporting_evidence(
            matter=locked_matter,
            record=result.entry,
            uploads=_uploads(uploads),
            actor=author,
        )
        result.action = complete_check(
            action=check,
            outcome=outcome,
            actor=author,
            next_check_on=next_check_on,
            reason=body,
        )
    return result


@transaction.atomic
def change_current_action(*, matter: Matter, actor: Any, action_id: Any, **step: Any) -> NextAction:
    """`Muuda` beside the open step — the same work, said differently.

    The canonical `set_next_action_for_new_work`, which supersedes the open row
    with a new one, and **carries its restriction** (docs/adr/0139): a step
    restricted below its Matter is still restricted after its words change — an
    edit must not show a reader the work they could not see before.

    **And its suggested step** (docs/adr/0141): an action started from
    `Soovitatud järgmisena` is still that work after `Muuda`, so completing the
    replacement still advances the suggestions. Read off the superseded row,
    never inferred from the words.

    Named, like `Mida tegid?`: the editor posts the step it was drawn beside, and
    a step that is no longer the open one refuses with `STALE_ACTION_REFUSAL`
    and writes nothing — otherwise a stale tab would carry *another* step's
    restriction onto words written about the old one.
    """
    locked_matter = lock_open_matter_for_business_write(matter.pk)
    _named_open_action(locked_matter=locked_matter, action_id=action_id)
    return set_next_action_for_new_work(
        matter=locked_matter,
        actor=actor,
        carry_plan_step=True,
        carry_visibility_override=True,
        **step,
    )


@transaction.atomic
def add_unnamed_step(
    *,
    matter: Matter,
    actor: Any,
    text: str,
    target_date: Any = None,
    date_precision: str = DatePrecision.EXACT,
    responsible: Any = None,
) -> NextAction:
    """A step a person adds without naming one: current, planned, or refused.

    The one decision behind `+ Määra järgmine tegevus` on both of its forms —
    the panel drawn with no step open (`set_action` with no `action_id`) and
    the one drawn beside a current step (`add_planned_action_view`). **Made
    under the Matter's lock, off the row as it is now** (AUD-004, AUD-040):

    * no step is open → this one becomes the current step, `DO` / `DEADLINE`
      like all new work;
    * a step is open — a colleague's, another tab's, or one this reader may not
      see — and this one has a day → it is filed as a planned step on that day
      (docs/adr/0143 §A2: a planned action never supersedes the current one);
    * a step is open and this one has no day → refused with
      `STALE_ACTION_REFUSAL` and nothing written, because an undated step
      cannot wait in the plan (`PLANNED_ACTION_NEEDS_DATE`).

    Never a supersession: the open step stays open whoever wrote it. Deciding
    on a read taken before the lock let two saves that both saw «no step» each
    write the current one, the second silently replacing the first.
    """
    from app.workflow.services import add_planned_action

    locked_matter = lock_open_matter_for_business_write(matter.pk)
    open_now = NextAction.objects.filter(matter=locked_matter, status=ActionStatus.OPEN).exists()
    if not open_now:
        return set_next_action_for_new_work(
            matter=locked_matter,
            actor=actor,
            text=text,
            target_date=target_date,
            date_precision=date_precision,
            responsible=responsible,
        )
    if target_date is None:
        raise DomainError(STALE_ACTION_REFUSAL)
    return add_planned_action(
        matter=locked_matter,
        actor=actor,
        text=text,
        target_date=target_date,
        date_precision=date_precision,
        responsible=responsible,
    )


@transaction.atomic
def add_matter_engagement(
    *,
    matter: Matter,
    author: Any,
    audience: str,
    kind: str = EngagementKind.OTHER.value,
    response_count: Any = None,
    smaily_url: str = "",
    alchemer_url: str = "",
    url: str = "",
    note: str = "",
    occurred_on: Any = None,
    occurred_on_precision: str = DatePrecision.EXACT.value,
    feedback_deadline: Any = None,
    feedback_received: str = "",
    uploads: Sequence[Any] = (),
    record_overview: bool = False,
    once_token: Any = None,
) -> WorkspaceResult:
    """`+ Kaasamine` — one consultation, with the replies it produced attached.

    ``url`` is `Veebileht`, the round's public page, and ``note`` is `Märkus`,
    what the person recording it wants said about it — both optional, both the
    columns `add_engagement` has always taken (docs/adr/0127 §2).

    The business model is exactly the one `add_engagement` already keeps:
    `response_count` stays nullable, blank still means *nobody counted* rather
    than *nobody answered*, and no response rate is computed anywhere
    (brief §16).

    The two provider links are optional, external and inert — where the mailing
    and the questionnaire for this round live, kept so a colleague can open them
    later. They do not weaken what makes an engagement an engagement: `audience`
    is still required, and a panel holding two links and no audience is a
    refusal, not a row (docs/adr/0027, amended 2026-09-12).

    **Both dates come from the panel and neither is invented here.**
    ``occurred_on`` used to be stamped with today inside the view, which turned
    a consultation from March into one that happened this afternoon the moment
    somebody wrote it down. The panel now asks `Kaasamise kuupäev`, pre-filled
    with today because that is the common case, and an answer of *blank* is
    stored as blank. ``feedback_deadline`` is `Tagasisidet ootame kuni`:
    optional and undefaulted. **The round is open either way** — one
    `OOTAME TAGASISIDET` work item for the Matter's owner, ended by
    `Lõpeta kaasamine` — and the date only says when it falls due: blank is
    «Tähtaeg määramata», never today and never overdue (docs/adr/0086 §3,
    docs/adr/0120, docs/adr/0132).

    ``kind`` defaults to `Muu` because the panel stopped asking. It stays a
    parameter for the importer and for the shell, which do know which channel a
    round used; what went is the question the panel put to a lawyer, whose
    answer nothing ever read back (docs/adr/0086 §1).

    ``feedback_received`` is `Saadud tagasiside / arvamused`, optional, and it
    completes nothing: a round created carrying both a deadline and some text is
    a wait that is open and already has something written in it. Ending the wait
    is `add_engagement_feedback`, and it is a decision with a name on it
    (docs/adr/0086 §6).

    ``occurred_on_precision`` is `EXACT` for everything this panel writes — it
    asks for a day and offers no other precision. The parameter stays because
    the importer and the register enrichment do carry periods, and because an
    existing approximate row must be able to travel back through the same
    service unchanged (docs/adr/0082, narrowed by docs/adr/0086 §1).

    It finishes no step and touches no plan (docs/adr/0141).

    ``once_token`` is the drawn form's `salvestus`: a second save of the same
    form is refused rather than opening a second round and a second wait
    (docs/adr/0167, amendment of 2026-10-17).
    """
    locked_matter = lock_open_matter_for_business_write(matter.pk)
    _spend_once(locked_matter, once_token, "kaasamine")
    with composer_operation() as operation_id:
        result = WorkspaceResult(operation_id=operation_id)
        result.record = add_engagement(
            matter=locked_matter,
            kind=kind,
            title=audience,
            occurred_on=occurred_on,
            occurred_on_precision=occurred_on_precision,
            response_count=response_count,
            smaily_url=smaily_url,
            alchemer_url=alchemer_url,
            url=url,
            note=note,
            feedback_deadline=feedback_deadline,
            feedback_received=feedback_received,
            actor=author,
        )
        result.documents = capture_supporting_evidence(
            matter=locked_matter,
            record=result.record,
            uploads=_uploads(uploads),
            actor=author,
        )
        if record_overview and url:
            _overview_for_engagement_link(
                matter=locked_matter, url=url, actor=author, engagement=result.record
            )
        return result


def _overview_for_engagement_link(
    *, matter: Matter, url: str, actor: Any, engagement: Any = None
) -> Any:
    """The Ülevaade a `Kaasamine` points at, recorded once (docs/adr/0142 §C).

    `Ülevaate link` is the round's `Hetkel käsil` overview. If the Matter
    already has an Ülevaade at that address it is reused as it is; a plan the
    Matter owes is published at it; otherwise one is recorded — with no
    publication day, since nobody said one. A news address is refused: the
    field asks for an overview, and filing news as one would be the wrong
    record. Inside the caller's transaction, so the round and its overview are
    written together or not at all.

    **Never less restricted than the round** (docs/adr/0162 §2). A round
    restricted below its Matter neither consumes the Matter's unrestricted plan
    nor records an unrestricted overview: the one it records carries the
    round's restriction. An overview already published at that address is a
    record readers already see and is returned untouched — nothing new is
    written about the round.
    """
    if classify_publication_url(url) == WebsiteOverviewKind.NEWS:
        raise DomainError(ENGAGEMENT_OVERVIEW_LINK_IS_NEWS)
    live = MatterWebsiteOverview.objects.live().filter(matter=matter)
    for published in live.filter(status=WebsiteOverviewStatus.PUBLISHED).order_by("-created_at"):
        if same_publication_url(published.url, url) and published.effective_kind != (
            WebsiteOverviewKind.NEWS
        ):
            return published
    restriction = restriction_inherited_from(engagement)
    plans = live.filter(
        status=WebsiteOverviewStatus.PLANNED,
        kind__in=("", WebsiteOverviewKind.OVERVIEW),
    )
    if restriction:
        plans = plans.filter(visibility_override=restriction)
    planned = plans.order_by("-created_at").first()
    if planned is None:
        planned = plan_website_overview(
            matter=matter, actor=actor, kind=WebsiteOverviewKind.OVERVIEW
        )
        if restriction:
            planned.visibility_override = restriction
            planned.save(update_fields=["visibility_override", "updated_at"])
    return publish_website_overview(
        overview=planned,
        url=url,
        published_on=None,
        actor=actor,
        kind=WebsiteOverviewKind.OVERVIEW,
    )


ROUND_TAKES_FILES_WHILE_OPEN = (
    "Faili saab lisada kaasamisele, mille tagasiside ootamine on pooleli."
)


@transaction.atomic
def add_engagement_evidence(
    *, engagement: Any, author: Any, uploads: Sequence[Any] = ()
) -> WorkspaceResult:
    """`+ Lisa fail` — a paper that arrived while the round is still open.

    The files are captured as ordinary evidence and linked to this round, and
    nothing else moves: the round stays open, no step is finished, no summary is
    written (historical regression, UX-006 / F-017). New bytes are a new
    document, never a copy of one the file already holds. Refused on a closed
    Matter and on a round that is not open, under the Matter's lock.
    """
    locked_matter = lock_open_matter_for_business_write(engagement.matter_id)
    current = (
        MatterEngagement.objects.select_for_update(no_key=True)
        .live()
        .filter(pk=engagement.pk, matter=locked_matter)
        .first()
    )
    if current is None or not current.has_open_feedback_wait:
        raise DomainError(ROUND_TAKES_FILES_WHILE_OPEN)
    with composer_operation() as operation_id:
        result = WorkspaceResult(operation_id=operation_id)
        result.record = current
        # A round restricted below its Matter keeps its new files restricted
        # with it — the capture's own rule for every record (docs/adr/0137):
        # never listed, counted or found by somebody who may not see the round.
        result.documents = capture_supporting_evidence(
            matter=locked_matter,
            record=current,
            uploads=_uploads(uploads),
            actor=author,
        )
        return result


@transaction.atomic
def add_engagement_feedback(
    *,
    engagement: Any,
    author: Any,
    feedback_received: str = "",
    uploads: Sequence[Any] = (),
    expected_revision: str | None = None,
    complete_action_id: Any = None,
) -> Any:
    """`Lõpeta kaasamine` — the round is finished, with its answers attached.

    The workspace door onto `complete_engagement_feedback`, and it exists for
    the reason every other function in this module does: the panel writes a
    record **and** whatever files arrived with it, and those two have to be one
    transaction. A completion that committed while its attachment was refused
    would leave a round recorded as answered and the answer itself nowhere
    (docs/adr/0075 §8).

    The closed-Matter rule, the two state refusals and the revision check are
    all the service's, taken under the Matter's row lock; nothing is re-asked
    here. The evidence is captured **after** the completion, so a refused upload
    unwinds a completion that has already been written rather than the other way
    round — the order `add_matter_engagement` already uses.

    Returns the completed engagement rather than a `WorkspaceResult`, because
    the caller swaps that one row back into the chronology and has no use for an
    operation id it cannot render.

    **`complete_action_id` finishes the current step with it, when the person
    says so** (docs/adr/0126 §2). A round that was the step — «Kaasa liikmed ja
    koonda nende seisukohad» — ends here, and asking the lawyer to write a
    second `Mida tegid?` saying the same thing is the duplicate this exists to
    remove. Nothing infers it: the wait on this row is not a `NextAction`, and
    no text, date or kind is compared. The step is the one the form named
    (`_named_open_action`), checked under the Matter's lock before anything is
    written, and finished through `complete_next_action` — **COMPLETED**, never
    superseded. ``None``, the default, leaves every step exactly as it was.

    One operation for the whole act — the closure, its files and the
    completion — so `Teema käik` reads it as one row: the round's own, with the
    finished step under it (docs/adr/0092 §6).
    """
    named: NextAction | None = None
    if complete_action_id is not None:
        # Before the closure writes anything; the lock is held to the end.
        named = _named_open_action(
            locked_matter=lock_open_matter_for_business_write(engagement.matter_id),
            action_id=complete_action_id,
        )
    with composer_operation():
        completed = complete_engagement_feedback(
            engagement=engagement,
            feedback_received=feedback_received,
            actor=author,
            expected_revision=expected_revision,
        )
        capture_supporting_evidence(
            matter=completed.matter,
            record=completed,
            uploads=_uploads(uploads),
            actor=author,
        )
        if named is not None:
            complete_next_action(action=named, actor=author)
    return completed


@transaction.atomic
def add_matter_important_date(
    *,
    matter: Matter,
    author: Any,
    title: str,
    date_value: Any,
    period_end: Any,
    date_precision: str,
    uploads: Sequence[Any] = (),
    once_token: Any = None,
) -> WorkspaceResult:
    """`+ Oluline tähtaeg` — a milestone somebody announced, and its letter.

    Creates no `NextAction`. A date the file has to live with is not an
    instruction to anybody, and the two were separated deliberately long before
    this round (brief §17). ``once_token``: one drawn form, one row
    (docs/adr/0167, amendment of 2026-10-17).
    """
    from app.intelligence.services import add_important_date

    locked_matter = lock_open_matter_for_business_write(matter.pk)
    _spend_once(locked_matter, once_token, "oluline-tahtaeg")
    with composer_operation() as operation_id:
        result = WorkspaceResult(operation_id=operation_id)
        result.record = add_important_date(
            matter=locked_matter,
            actor=author,
            title=title,
            date_value=date_value,
            period_end=period_end,
            date_precision=date_precision,
        )
        result.documents = capture_supporting_evidence(
            matter=locked_matter,
            record=result.record,
            uploads=_uploads(uploads),
            actor=author,
        )
        return result


@transaction.atomic
def add_matter_effective_date(
    *,
    matter: Matter,
    author: Any,
    description: str,
    date_value: Any,
    period_end: Any,
    date_precision: str,
    uploads: Sequence[Any] = (),
    once_token: Any = None,
) -> WorkspaceResult:
    """`+ Jõustumine` — what commences, when it does, and the act itself.

    ``period_end`` is a parameter rather than ``date_value`` repeated. It was
    the repetition while the panel offered nothing but an exact day, and it was
    the *wrong* answer the moment it offered a quarter: a commencement recorded
    as *IV kvartal 2026* would have been stored as a period ending on 1 October
    and read, by everything that asks `has_passed`, as over on its first day
    (docs/adr/0079 §13). The form computes both ends through `bounds_for`, and
    `intelligence.services._check_bounds` refuses a pair that disagrees with its
    own precision.

    ``once_token``: one drawn form, one row (docs/adr/0167, amendment of
    2026-10-17).
    """
    from app.intelligence.services import add_effective_date

    locked_matter = lock_open_matter_for_business_write(matter.pk)
    _spend_once(locked_matter, once_token, "joustumine")
    with composer_operation() as operation_id:
        result = WorkspaceResult(operation_id=operation_id)
        result.record = add_effective_date(
            matter=locked_matter,
            actor=author,
            description=description,
            date_value=date_value,
            period_end=period_end,
            date_precision=date_precision,
        )
        result.documents = capture_supporting_evidence(
            matter=locked_matter,
            record=result.record,
            uploads=_uploads(uploads),
            actor=author,
        )
        return result


#: What `+ Lisa · Muu` answers when it is given no day (the form refuses first).
OTHER_ENTRY_NEEDS_A_DAY = "Sisesta, millal see toimus."

#: What `Kohtumine / arutelu` answers when it is given no day (the form refuses
#: first).
MEETING_ENTRY_NEEDS_A_DAY = "Sisesta kohtumise kuupäev."

#: What `+ Lisa · Muu` answers when its day is after today — the form under
#: `Millal?` and the service alike. Worded as a send dated ahead is refused
#: (`SENT_DATE_IN_THE_FUTURE`): one shape for one rule (JQA-001).
OTHER_ENTRY_DAY_AHEAD = "Toimumise kuupäev ei saa olla tulevikus."

#: What `Kohtumine / arutelu` answers when its day is after today.
MEETING_ENTRY_DAY_AHEAD = "Kohtumise kuupäev ei saa olla tulevikus."


def _refuse_a_day_ahead(day: Any, refusal: str) -> None:
    """A `Muu` or a `Kohtumine` happened, so its day is not after today.

    Both store the stated day in `Entry.occurred_at`, and every readable entry
    is work done on that day to `app.matters.activity` — «Viimane tegevus»,
    «Muutusteta 30 p» and Osakond's weeks. A day ahead would be counted as work
    already done, and the day cannot be corrected afterwards (docs/adr/0097,
    amendments of 2026-10-15 and 2026-10-17). A plan is `Järgmine tegevus` or
    `Oluline tähtaeg`.

    **The Tallinn business day**, `timezone.localdate()`: today is accepted at
    any hour of it, half past midnight included. A datetime is read as its
    Tallinn day (`_local_day`), the day the row is then stored on. ``None`` is
    the missing-day refusal's question, not this one's.
    """
    if day is None:
        return
    if _local_day(day) > timezone.localdate():
        raise DomainError(refusal)


def _local_day(value: Any) -> Any:
    """The Tallinn day a stated `occurred_on` names: a date as it is, a datetime by its day.

    An aware instant is read in the local zone — 00:30 in Tallinn is the
    previous day in UTC — and a naive one by its own date. Done once, ahead of
    the range check and the day-ahead check, so a datetime caller meets the
    same two sentences a date caller does rather than a `TypeError` from
    comparing a date with a datetime (JRE-01). An instant whose local day does
    not exist in Python's calendar (the last hours of year 9999 seen from
    Tallinn) is out of range like any other.
    """
    if not isinstance(value, datetime):
        return value
    if timezone.is_naive(value):
        return value.date()
    try:
        return timezone.localdate(value)
    except OverflowError:
        raise DomainError(DAY_OUT_OF_RANGE) from None


def _add_dated_entry_with_files(
    *,
    matter: Matter,
    author: Any,
    kind: str,
    body: str,
    occurred_on: Any,
    missing_day: str,
    day_ahead: str,
    url: str = "",
    uploads: Sequence[Any] = (),
    once_token: Any = None,
    once_purpose: str = "",
) -> WorkspaceResult:
    """One dated `Entry` and its files, together or not at all.

    The shared half of `+ Lisa · Muu` and `Kohtumine / arutelu`
    (docs/adr/0097, amendments of 2026-10-15 and 2026-10-17): the day is the
    one the person stated, stored as the start of that local day; the entry
    goes through the canonical `add_entry` (body sanitising, change event) and
    the files through `capture_supporting_evidence`, inside the open-Matter
    write lock. The caller's own `@transaction.atomic` makes a refused file
    leave no entry behind. Nothing else is touched.

    The day may not be after today (`_refuse_a_day_ahead`), whoever calls,
    and one drawn form saves once (``once_token``, docs/adr/0167, amendment of
    2026-10-17).
    """
    if occurred_on is None:
        raise DomainError(missing_day)
    # One day for both checks and for the stored row: a datetime caller is read
    # as its Tallinn day here, before the range check compares it (JRE-01).
    occurred_on = _local_day(occurred_on)
    # Asked here as well as by the day box: year 1 becomes Tallinn midnight in
    # year 0 UTC, which the database refuses as a 500 (JQA-008, CORR-02).
    if not is_supported_day(occurred_on):
        raise DomainError(DAY_OUT_OF_RANGE)
    _refuse_a_day_ahead(occurred_on, day_ahead)
    locked_matter = lock_open_matter_for_business_write(matter.pk)
    _spend_once(locked_matter, once_token, once_purpose)
    with composer_operation() as operation_id:
        result = WorkspaceResult(operation_id=operation_id)
        result.entry = add_entry(
            matter=locked_matter,
            body=body,
            author=author,
            kind=kind,
            occurred_at=start_of_local_day(occurred_on),
            url=url,
        )
        result.documents = capture_supporting_evidence(
            matter=locked_matter,
            record=result.entry,
            uploads=_uploads(uploads),
            actor=author,
        )
        return result


@transaction.atomic
def add_matter_other(
    *,
    matter: Matter,
    author: Any,
    description: str,
    occurred_on: Any,
    url: str = "",
    uploads: Sequence[Any] = (),
    once_token: Any = None,
) -> WorkspaceResult:
    """`+ Lisa · Muu` — supporting material that fits no structured kind.

    One `Entry(kind=OTHER)`: the description is its body, the optional link its
    own `url` column, and the files are captured against it through the
    canonical evidence pipeline, all in one transaction — a refused file leaves
    no entry behind (docs/adr/0097, amendment of 2026-10-15).

    **The date is the day the material or event occurred**, which the person
    states (`Millal?`, required). It is stored as the start of that local day
    (`start_of_local_day`), so the chronology shows and orders the row on that
    day — `Entry.occurred_at` is when the work happened, not when it was typed.
    No day is invented: without one the save is refused. **Nor is it after
    today** (`OTHER_ENTRY_DAY_AHEAD`): it is material met or an event that
    happened, and activity counts it as work done on that day (JQA-001).

    Creates no `NextAction`, touches neither `Hetkeseis` nor the plan, opens no
    wait and is no `Submission`. A closed Matter refuses it through the same
    lock every other new business record takes.
    """
    return _add_dated_entry_with_files(
        matter=matter,
        author=author,
        kind=EntryKind.OTHER,
        body=description,
        occurred_on=occurred_on,
        missing_day=OTHER_ENTRY_NEEDS_A_DAY,
        day_ahead=OTHER_ENTRY_DAY_AHEAD,
        url=url,
        uploads=uploads,
        once_token=once_token,
        once_purpose="muu",
    )


@transaction.atomic
def add_matter_meeting(
    *,
    matter: Matter,
    author: Any,
    summary: str,
    occurred_on: Any,
    uploads: Sequence[Any] = (),
    once_token: Any = None,
) -> WorkspaceResult:
    """`+ Koja arvamus / kohtumine · Kohtumine / arutelu` — a meeting held.

    One `Entry(kind=MEETING)` (`Kohtumine`): the short summary is its body —
    sanitised by `add_entry` like every entry body, so who took part is named
    there — dated by the required `Kuupäev` as the start of that local day,
    with optional files through the canonical evidence pipeline, all in one
    transaction (docs/adr/0097, amendment of 2026-10-17). A meeting is one
    held, so a day after today is refused (`MEETING_ENTRY_DAY_AHEAD`, JQA-001).

    No organisation, no participants model, no link. It is not a
    `MatterEngagement` (`EngagementKind.MEETING` is a consultation round's
    kind, not this), opens no feedback wait, creates or completes no
    `NextAction`, changes neither `Hetkeseis`, the plan nor the opinion
    deadline, and is no `Submission`. A closed Matter refuses it through the
    same lock every other new business record takes.
    """
    return _add_dated_entry_with_files(
        matter=matter,
        author=author,
        kind=EntryKind.MEETING,
        body=summary,
        occurred_on=occurred_on,
        missing_day=MEETING_ENTRY_NEEDS_A_DAY,
        day_ahead=MEETING_ENTRY_DAY_AHEAD,
        uploads=uploads,
        once_token=once_token,
        once_purpose="kohtumine",
    )


#: What `Lõpeta teema` posting a reason the panel does not offer is told — the
#: form's own words, because the form refuses it first and this is the same
#: refusal for a caller that skipped the form.
CLOSURE_REASON_NOT_OFFERED = "Vali, miks teema lõpetatakse."


@transaction.atomic
def close_matter_from_workspace(
    *,
    matter: Matter,
    author: Any,
    disposition: str,
    explanation: str = "",
    follow_ups_confirmed: bool | str = False,
    uploads: Sequence[Any] = (),
    once_token: Any = None,
) -> WorkspaceResult:
    """`Lõpeta teema` — Koda is finished with this file, whatever the process does next.

    Restored by docs/adr/0131's amendment of 2026-10-25, and **nothing but
    `close_matter`**: the one closure service, which locks the Matter, refuses
    one that is already closed («Teema on juba suletud.»), asks the digest-bound
    confirmation of any live work under that lock (docs/adr/0152), ends that
    work, and writes `MATTER_CLOSED` in the period that is current.

    What it does **not** do is the point of the round: `Hetkeseis` stays where
    the external process stands, no `Submission` and no `Entry` are written, no
    opinion deadline is marked answered, no follow-up check is planned and no
    `Töövõit` is recorded. The person's explanation is stored as written and
    claims nothing the person did not say.

    **No closed-Matter guard here**, as before §11 (R2-02): this is the act that
    produces the closed state, so a second press is `close_matter`'s refusal.

    **Only the reasons the panel offers** (`CLOSURE_CHOICES`). Every other
    disposition stays `close_matter`'s to write for the callers that mean it —
    «Jõustunud» closing with `COMPLETED`, an import, a successor — and a post
    naming one of them here is refused before anything is locked.

    **The files are this closure's evidence** (docs/adr/0131, closure files
    amendment): the ministry's letter saying the draft was dropped. In order,
    so that nothing irreversible happens before every question is answered:

    1. every file is validated (`read_uploads`) — one unusable file refuses
       the whole closure, and no byte has been stored;
    2. the Matter is locked and the drawn form's token spent (``once_token``,
       docs/adr/0167) — a double press or a second tab is refused;
    3. `close_matter` asks its own questions under that lock — already closed,
       live work unconfirmed — and closes;
    4. only then are the bytes stored, each as a `Document` with its immutable
       version, written inside the same `composer_operation` as the closure.

    That shared `operation_id` is the whole association: `MATTER_CLOSED` and
    each file's `EVIDENCE_VERSION_ADDED` are append-only rows naming the same
    act, written in one transaction, so `Teema käik` reads the files under the
    closing they came with and under no other — after a reopening and a second
    closing too, which is a second operation. No `Entry`, no `DocumentLink`
    and no other record is invented to hold them. A refusal at any step rolls
    everything back; the closure and its files stand or fall together.
    """
    from app.documents.services import capture_closure_evidence, read_uploads
    from app.matters.forms import CLOSURE_CHOICES
    from app.matters.locks import lock_matter_for_write

    if disposition not in {value for value, _label in CLOSURE_CHOICES}:
        raise DomainError(CLOSURE_REASON_NOT_OFFERED)
    accepted = read_uploads(_uploads(uploads))
    locked_matter = lock_matter_for_write(matter.pk)
    _spend_once(locked_matter, once_token, "lopeta")
    with composer_operation() as operation_id:
        close_matter(
            matter=locked_matter,
            disposition=disposition,
            actor=author,
            reason=explanation.strip(),
            follow_ups_confirmed=follow_ups_confirmed,
        )
        result = WorkspaceResult(operation_id=operation_id, closed=True)
        result.documents = capture_closure_evidence(
            matter=locked_matter, accepted=accepted, actor=author
        )
        return result


@transaction.atomic
def add_matter_work_victory(
    *,
    matter: Matter,
    author: Any,
    title: str,
    period_date: Any,
    period_end: Any,
    date_precision: str,
    uploads: Sequence[Any] = (),
    once_token: Any = None,
) -> WorkspaceResult:
    """`+ Töövõit` — what changed, when it belongs, and the evidence for it.

    A win closes nothing and completes nothing. It is its own canonical fact,
    recorded against the period it belongs to rather than the day the file
    finishes, and it goes through the confirmed-victory service because a person
    stating it has already made the judgement a candidate exists to defer
    (docs/adr/0074 §8, brief §19).

    **The period is required here and has no default.** This helper used to send
    none, so a win recorded from the workspace arrived with `period_date` NULL
    and never appeared in `?toovoit=<aasta>` or the reporting rail. The three
    columns are now the caller's to supply, and the caller is a form that asks
    (docs/adr/0079 §10).

    ``once_token``: one drawn form, one win (docs/adr/0167, amendment of
    2026-10-17).
    """
    from app.intelligence.services import add_confirmed_work_victory

    locked_matter = lock_open_matter_for_business_write(matter.pk)
    _spend_once(locked_matter, once_token, "toovoit")
    with composer_operation() as operation_id:
        result = WorkspaceResult(operation_id=operation_id)
        result.record = add_confirmed_work_victory(
            matter=locked_matter,
            actor=author,
            title=title,
            detail="",
            period_date=period_date,
            period_end=period_end,
            date_precision=date_precision,
        )
        result.documents = capture_supporting_evidence(
            matter=locked_matter,
            record=result.record,
            uploads=_uploads(uploads),
            actor=author,
        )
        return result


@transaction.atomic
def add_matter_external_position(
    *,
    matter: Matter,
    author: Any,
    organisation: Any,
    provenance: Any = ExternalPositionProvenance.DISCOVERED.value,
    source_label: str = "",
    url: str = "",
    stated_on: Any = None,
    stated_on_precision: str = DatePrecision.EXACT.value,
    summary: str = "",
    lawyer_note: str = "",
    source_is_member: bool = False,
    engagement: Any = None,
    uploads: Sequence[Any] = (),
    once_token: Any = None,
) -> WorkspaceResult:
    """`+ Kaasamine · Lisa tagasiside` — what somebody else said.

    **One operation behind one form**, which is docs/adr/0091 §3's whole
    architectural claim. «Saadeti Kojale» (`RECEIVED`) and «Avaldatud mujal»
    (`DISCOVERED`) are two professional facts with one shape: an author, a
    source a colleague can open, an optional date at the precision it is known
    to, and an optional note from the lawyer. Four models — `EngagementFeedback`,
    `ExternalPosition`, `AnotherOpinion`, `SurveyFeedback` — would have been four
    sets of validation and four chronology renderings for one set of rules, so
    the distinction is a column, stated on the one form (docs/adr/0170; until
    then two chips, `Meile saadetud tagasiside` and `Teiste arvamus`).

    ``provenance`` is the distinction, ``source_label`` is what an aggregate
    answer with no single author is called, and ``lawyer_note`` is this office's
    own reading of the position — stored beside the source and never inside it
    (docs/adr/0091 §3, §4).

    One operation: the record, its files and the links between them land
    together or not at all. That is the whole reason this module exists, and it
    is load-bearing here in a way it is not for a note — a position whose file
    was refused would be a record claiming a source it does not have
    (docs/adr/0075 §8).

    **The source rule is decided before anything is written.** One of three
    satisfies it — the written `Seisukoht`, a public address, or a file — and
    the third of those is the awkward one: the `DocumentLink` cannot exist until
    the position does, so the service is told how many files are about to be
    captured rather than being handed them. If the capture then refuses one of
    them, `UploadRejected` unwinds this transaction and takes the position with
    it, so a record that promised a file and got none does not survive its own
    save (docs/adr/0084 §3, amended 2026-09-16).

    **The files carry `EXTERNAL_POSITION`, and this is the one workspace
    operation whose uploads are not `OTHER`.** Every other panel here captures
    *supporting evidence for something Koda did*, where the button a file
    arrived through is not a business role and inventing one to record where it
    came from is what the link exists to avoid (brief §23). Here the document is
    the position: a ministry's paper filed against a Matter has a role the
    product already named, and `DocumentRole.EXTERNAL_POSITION` has existed
    since the foundational schema with nothing writing it.

    Takes the lock and the closed-Matter question through the same helper as
    every other operation in this module. A closed Teema renders no launcher,
    and that decides nothing about a POST arriving from a tab that was open
    before somebody else shut the file (R2-02).

    ``once_token`` is `Lisa tagasiside`'s drawn form: a second save of it is
    refused whether or not it names a round — a standalone position too is
    repeated deliberately by opening the form again (docs/adr/0170 §4,
    amendment of 2026-10-17).
    """
    from app.matters.services import record_external_position, record_external_position_document

    files = _uploads(uploads)
    locked_matter = lock_open_matter_for_business_write(matter.pk)
    _spend_once(locked_matter, once_token, "tagasiside")
    with composer_operation() as operation_id:
        result = WorkspaceResult(operation_id=operation_id)
        position = record_external_position(
            matter=locked_matter,
            organisation=organisation,
            provenance=provenance,
            source_label=source_label,
            url=url,
            stated_on=stated_on,
            stated_on_precision=stated_on_precision,
            summary=summary,
            lawyer_note=lawyer_note,
            source_is_member=source_is_member,
            engagement=engagement,
            attachment_count=len(files),
            actor=author,
        )
        result.record = position
        result.documents = capture_supporting_evidence(
            matter=locked_matter,
            record=position,
            uploads=files,
            actor=author,
            role=DocumentRole.EXTERNAL_POSITION,
        )
        for document in result.documents:
            record_external_position_document(position=position, document=document, actor=author)
        return result


@transaction.atomic
def record_position_reply(
    *,
    matter: Matter,
    author: Any,
    provenance: Any,
    organisation: Any = None,
    organisation_name: str = "",
    source_label: str = "",
    url: str = "",
    stated_on: Any = None,
    stated_on_precision: str = DatePrecision.EXACT.value,
    summary: str = "",
    lawyer_note: str = "",
    source_is_member: bool = False,
    engagement: Any = None,
    uploads: Sequence[Any] = (),
    once_token: Any = None,
) -> WorkspaceResult:
    """`+ Kaasamine · Lisa tagasiside` — one position, and the round it answers.

    The whole save behind the form, as one use case rather than as a sequence
    a view composes (JARCH-001): whoever records a reply gets the same
    guarantees as the form does, in the same order, in one transaction.

    1. **The institution.** ``organisation`` is the picker's chosen answer and
       ``organisation_name`` the name somebody typed; which of the two wins is
       `resolve_addressee`'s rule. Neither given is received feedback that
       names nobody — a record since docs/adr/0101, so `resolve_addressee` is
       not asked at all (it would refuse two empty answers). Resolved inside
       this transaction, so any refusal below leaves no new institution
       behind (docs/adr/0073, docs/adr/0084 §4).
    2. **A repeat is refused** when a round is named
       (`engagement_replies.refuse_repeated_reply`, under the Matter's lock):
       a finished round takes later answers, so a second press of `Salvesta`
       must not file the same one twice, while a second organisation's
       identical answer is its own (docs/adr/0167 §2, docs/adr/0170 §4).
    3. **The record** — `add_matter_external_position`, which refuses a
       closed Matter under the same lock and spends the drawn form's
       ``once_token``.
    4. **The wait.** The first answer to an open round finishes its wait
       through the canonical close; a round already finished — by an earlier
       answer, `Lõpeta kaasamine` or somebody else meanwhile — keeps its
       closure and simply gains this answer
       (`engagement_replies.finish_wait_on_reply`, docs/adr/0142 §B,
       docs/adr/0167 §2). A standalone position (no ``engagement``) touches no
       wait. Nothing else is completed: no `NextAction` is inferred from
       feedback.

    The provenance is the caller's stated answer, never inferred here
    (docs/adr/0091 §3.2); the form fixes it at RECEIVED for a linked round.
    """
    from app.matters.services import resolve_addressee

    organisation_name = (organisation_name or "").strip()
    resolved = (
        resolve_addressee(chosen=organisation, typed_name=organisation_name)
        if (organisation is not None or organisation_name)
        else None
    )
    if engagement is not None:
        engagement_replies.refuse_repeated_reply(
            matter=matter,
            engagement=engagement,
            organisation=resolved,
            source_is_member=source_is_member,
            summary=summary,
            url=url,
            stated_on=stated_on,
            uploads=uploads or (),
        )
    result = add_matter_external_position(
        matter=matter,
        author=author,
        organisation=resolved,
        provenance=provenance,
        source_label=source_label,
        url=url,
        stated_on=stated_on,
        stated_on_precision=stated_on_precision,
        summary=summary,
        lawyer_note=lawyer_note,
        source_is_member=source_is_member,
        engagement=engagement,
        uploads=uploads,
        once_token=once_token,
    )
    if engagement is not None:
        result.wait_finished = engagement_replies.finish_wait_on_reply(
            engagement=engagement, actor=author, reply=result.record
        )
    return result


#: A `Koja arvamus` is recorded with the bytes that went out, or not at all.
SENT_OPINION_NEEDS_A_FILE = "Lisa fail, mis välja saadeti."


@transaction.atomic
def add_matter_koda_opinion(
    *,
    matter: Matter,
    author: Any,
    recipients: Sequence[Any],
    sent_on: Any,
    upload: Any = None,
    uploads: Sequence[Any] = (),
    title: str = "",
    summary: str = "",
    complete_action_id: Any = None,
    working_uploads: Sequence[Any] = (),
    stage: Any = None,
    answers_deadline: str | None = None,
    close_rounds: Sequence[tuple[Any, str]] = (),
    once_token: Any = None,
    follow_ups_confirmed: bool | str = False,
) -> WorkspaceResult:
    """`+ Koja arvamus` — the Chamber's opinion went out, with the file that went.

    **`stage` — `Uus hetkeseis`, and the opinion stays in the period it was
    written in** (docs/adr/0131 §5). An opinion on the idea that moves the file
    to its consultation round reads under «Idee» in `Teema käik`, and
    «Kooskõlastusringil» begins after it: the move is made through
    `stage_transition`, which pins the period current before it, and a stage
    that ends the Matter closes it after the send is recorded.

    **Composition, and the fourth caller of a service that already exists.** Koda's
    own opinion is a `Submission` and has been since the foundational schema;
    `register_sent_opinion` is the one act «this file was sent — say so», and
    `register_sent_opinion_on_open_matter` is that act behind the business-write
    boundary. Every rule this touches is still decided where it was:
    `create_submission` validates the kind and writes the creation event,
    `select_final_evidence` takes the Matter and submission locks and runs
    `check_evidence_is_usable` against what those locks protect, and
    `mark_submission_sent` re-runs the evidence check, stamps the supplied day and
    writes the send event. A second opinion about when Koda may claim to have sent
    something would drift from the first (docs/adr/0061 §17, docs/adr/0091 §6).

    What this adds is the half the `Dokumendid` form cannot do: **the bytes and the
    send in one act.** That page asks a lawyer to upload the file, find it again in
    a select and then register it — three round trips describing two states nobody
    was ever in — and on the Teema page there is no file table to upload into at
    all. Here the `Document`, its immutable `DocumentVersion` and the `Submission`
    land in one transaction, so a refused upload leaves no half-registered opinion
    and a refused registration leaves no orphan evidence.

    **Uploading is still not asserting.** `DocumentRole.KODA_SUBMISSION_FINAL` says
    Koda holds these bytes as an opinion; the `Submission` says it was sent, to
    whom and when. The two remain separate records and this function writes both
    because a person pressed one button meaning both — which is what an atomic
    operation is for, and is not the same thing as inferring one from the other
    (docs/adr/0061, docs/adr/0091 §6.2).

    ``sent_on`` is a **day the person supplied**, and this function invents none:
    the service refuses `None` and refuses any precision but `DATE`, which is the
    rule R2-01 put there after a blank box became `timezone.now()` and the outbound
    register reported `Arvamus välja <today>` about letters nobody had dated.

    ``recipients`` is who it actually went to, and this function still defaults
    nothing: what it writes is exactly the list it was handed. The Teema panel
    now *offers* the Matter's senders in its control, where they can be read and
    removed before anything is saved — a form default rather than a service one,
    which is the distinction docs/adr/0091 §6.3's refusal was really about. An
    opinion on the first draft goes to the ministry and one at second reading
    goes to a Riigikogu committee, and neither is assumed here
    (docs/adr/0078 §2, docs/adr/0095 §1).

    **Nothing in this function reads or writes `Matter.source_organisations`.**
    `SubmissionRecipient` and `Saatja` are two facts, and recording an opinion
    leaves the second exactly as it was.

    ``title`` is still optional and the uploaded file's own name is still what a
    blank means — the Teema panel simply no longer offers the box, so that is
    what it always passes. ``summary`` is the new `Kokkuvõte`: what the opinion
    argued, in the lawyer's words, stored on the `Submission` and never read as
    a headline (docs/adr/0095 §2).

    Several per Matter is ordinary. Nothing here is unique on the Matter, nothing
    supersedes an earlier opinion, and no earlier `Submission`, `Document` or
    `DocumentVersion` is touched — a revised opinion is a new letter and new bytes,
    which is what the immutable evidence store is for (docs/adr/0091 §6.4, §7).

    **`complete_action_id` — and sending it was the step** (docs/adr/0126 §2).
    «Vormista ja saada Koja seisukoht» is finished by exactly this save, and
    asking the lawyer to write `Mida tegid?` afterwards would put a second,
    generic record of the same act on the file. When the person ticks
    `Märgi praegune tegevus tehtuks`, the form names the open step it showed,
    and that step — and no other — is checked under the Matter's lock **before**
    the file is stored (`_named_open_action`) and completed through
    `complete_next_action` after the send, in the same transaction and
    operation. It ends COMPLETED, never SUPERSEDED, and the chronology keeps one
    row for the act: «Arvamus välja», with the finished step folded under it.
    Nothing is inferred from the opinion's title, summary, date or recipients;
    unticked (``None``), no step moves.

    **``working_uploads`` — the opinion's working documents** (docs/adr/0129 §2).
    The editable file the letter was drafted in, which the lawyer reuses later:
    each becomes an ordinary `Document` + `DocumentVersion` filed as
    `Töödokument` — the box it was put in says so, nothing is guessed from the
    bytes — and is tied to this exact `Submission` by a `DocumentLink`. They are
    **never evidence**: the send still stands on the one `Saadetud fail` and its
    pinned `final_version`, and nothing here can make a working document satisfy
    it. Restricted with the opinion when the opinion is restricted.

    **Validated before anything is written.** Every working document is read and
    checked before the sent letter's bytes reach the evidence store, so a refused
    DOCX refuses the whole save — no opinion, no letter, no completed step and no
    orphaned bytes (ENG-086). Then one transaction and one operation: the save is
    still one «Arvamus välja» row with these files under it, never a «lisas
    dokumendi» row per file (docs/adr/0092 §5).

    **What else the save finishes, named by the person** (historical regression,
    stage II). ``answers_deadline`` — the current `Arvamuse tähtaeg`'s revision
    as the form drew it — records this opinion as its answer and ends it;
    ``close_rounds`` — ``(round, revision)`` pairs — ends those rounds' waits
    and no others, with no summary invented. Each is checked under the lock
    before a byte is stored, and any refusal refuses the whole save. Nothing is
    chosen for anybody: not the newest deadline, not every round. ``once_token``
    makes a second press of the same drawn form a refusal rather than a second
    opinion. No `Tööplaan` step is named or finished (docs/adr/0141).

    **``uploads`` — every file that went out** (docs/adr/0144 §5). The first is
    the letter — `final_version`, with every guarantee it always had; each further
    one becomes its own `Document` + `DocumentVersion`, filed as the opinion's
    letter too, and is bound to **this same** `Submission` as a further sent file.
    One opinion and one «Arvamus välja» row, never one per file. ``upload`` is the
    one-file shape every earlier caller passes, and still works.
    """
    from datetime import datetime, time

    from app.documents.enums import DocumentRole as _Role
    from app.documents.services import (
        OPINION_WORKING_DOCUMENT_ROLE,
        add_evidence_version,
        capture_accepted_evidence,
        create_document,
        read_uploads,
    )
    from app.documents.uploads import UploadRejected, document_title_for, read_upload
    from app.submissions.enums import SentAtPrecision, SubmissionKind
    from app.submissions.services import register_sent_opinion_on_open_matter

    sent_files = ([upload] if upload is not None else []) + _uploads(uploads)
    if not sent_files:
        raise DomainError(SENT_OPINION_NEEDS_A_FILE)
    if sent_on is None:
        # Stated here as well as in the service, because this is the boundary the
        # panel posts to and «the application picked a day» is the one failure
        # docs/adr/0061's amendment exists to prevent.
        raise DomainError("Saatmise registreerimiseks on vaja saatmise kuupäeva.")

    from app.matters.services import stage_transition

    locked_matter = lock_open_matter_for_business_write(matter.pk)
    named = (
        _named_open_action(locked_matter=locked_matter, action_id=complete_action_id)
        if complete_action_id is not None
        else None
    )
    # **A send that also ends the Matter** (a terminal `Uus hetkeseis`) closes
    # a file that, from this save on, has a check on this very opinion — and
    # perhaps checks on earlier ones. The closure cancels them all, so the save
    # asks for the same confirmation any closure with a pending check does,
    # before a byte is stored (docs/adr/0146 §8).
    if locked_matter.is_open and is_terminal_stage(getattr(stage, "key", None)):
        from app.workflow.follow_ups import refuse_unconfirmed_closure

        # This send schedules a check that the closure would end at once, so
        # the confirmation is always asked — and names whatever else is live
        # (docs/adr/0152). The step it finishes, if it was one, is done.
        # Verified here, under the lock this save keeps, the person's answer
        # is handed on as ``True``: the check this very send schedules is not
        # work a colleague added after the warning (docs/adr/0152 §7).
        if refuse_unconfirmed_closure(
            locked_matter, confirmed=follow_ups_confirmed, finishing=named, will_check=True
        ):
            follow_ups_confirmed = True
    _spend_once(locked_matter, once_token, "koja-arvamus")
    if answers_deadline is not None:
        from app.matters.response_deadlines import (
            NO_CURRENT_DEADLINE,
            STALE_DEADLINE_REFUSAL,
            deadline_revision,
        )

        if locked_matter.response_deadline is None:
            raise DomainError(NO_CURRENT_DEADLINE)
        if answers_deadline and deadline_revision(locked_matter) != answers_deadline:
            raise DomainError(STALE_DEADLINE_REFUSAL)
    rounds = _named_open_rounds(locked_matter, close_rounds)
    with (
        composer_operation() as operation_id,
        stage_transition(
            matter=locked_matter,
            stage=stage if stage is not None else locked_matter.stage,
            actor=author,
            follow_ups_confirmed=follow_ups_confirmed,
        ) as move,
    ):
        result = WorkspaceResult(operation_id=operation_id, closed=move.closes)
        # Read first, so a rejected file refuses before anything is written. The
        # ordinary evidence pipeline — same reader, same scan gate, same checksum,
        # same immutability — and the role is the one the product already has for
        # Koda's own opinion.
        accepted = read_upload(sent_files[0])
        further = [read_upload(item) for item in sent_files[1:]]
        # The working documents too, and before the letter is stored: a refusal
        # here must leave no evidence bytes behind (docs/adr/0129 §6). Prefixed
        # with the box's own name, so a person told «file content does not
        # match its extension» knows it was the DOCX and not the letter.
        try:
            working = read_uploads(_uploads(working_uploads))
        except UploadRejected as error:
            raise UploadRejected(f"Töödokumendid: {error}") from error
        document = create_document(
            matter=locked_matter,
            title=(title or "").strip() or document_title_for(accepted),
            role=_Role.KODA_SUBMISSION_FINAL,
            created_by=author,
        )
        version = add_evidence_version(
            document=document,
            content=accepted.body,
            original_filename=accepted.filename,
            mime_type=accepted.mime_type,
            uploaded_by=author,
        )
        result.documents = [document]
        further_versions = []
        for extra in further:
            # The letter's own role and restriction: these bytes went out with
            # it, and are no less restricted than the opinion that stands on them.
            extra_document = create_document(
                matter=locked_matter,
                title=document_title_for(extra),
                role=_Role.KODA_SUBMISSION_FINAL,
                visibility_override=document.visibility_override,
                created_by=author,
            )
            further_versions.append(
                add_evidence_version(
                    document=extra_document,
                    content=extra.body,
                    original_filename=extra.filename,
                    mime_type=extra.mime_type,
                    uploaded_by=author,
                )
            )
            result.documents.append(extra_document)
        # Midnight in Europe/Tallinn, carried as `SentAtPrecision.DATE` so that no
        # surface ever reads the anchor back as «00:00». The person answered a day
        # and the record says so (app/submissions/enums.py, docs/adr/0079 §2).
        moment = timezone.make_aware(datetime.combine(sent_on, time.min))
        # The Matter is not a parameter: the service derives it from the
        # document's own `matter_id` and re-takes the same row lock, which is
        # free inside one transaction and is what keeps the boundary in one place
        # rather than in every caller (`app/matters/locks.py`).
        result.record = register_sent_opinion_on_open_matter(
            document=document,
            version=version,
            title=document.title,
            # On a Teema the Chamber started itself, what goes out is its
            # proposal or appeal, and is recorded as one; the thirty-day check
            # that every send schedules then asks about the proposal
            # (docs/adr/0151 §5). Every other Teema sends an opinion, as before.
            kind=(
                SubmissionKind.KODA_PROPOSAL
                if is_koda_initiative(locked_matter)
                else SubmissionKind.FORMAL_OPINION
            ),
            actor=author,
            summary=summary,
            recipients=list(recipients),
            sent_at=moment,
            sent_at_precision=SentAtPrecision.DATE,
            further_versions=further_versions,
        )
        result.documents += capture_accepted_evidence(
            matter=locked_matter,
            record=result.record,
            accepted=working,
            actor=author,
            role=OPINION_WORKING_DOCUMENT_ROLE,
        )
        # On a Chamber initiative the send itself has just answered the
        # initiative's own deadline (`answer_initiative_deadline_of`, called by
        # the send wrapper on its own lock of this row — QA-WORKFLOW-04). Read
        # the field back so this save neither answers it a second time nor
        # leaves the caller's instance saying it is still due.
        _read_back_deadline(locked_matter, matter)
        if answers_deadline is not None and locked_matter.response_deadline is not None:
            from app.matters.response_deadlines import answer_current_deadline_with

            answer_current_deadline_with(
                locked=locked_matter,
                matter=matter,
                submission=result.record,
                actor=author,
                expected_revision=answers_deadline or None,
            )
        for engagement, revision in rounds:
            complete_engagement_feedback(
                engagement=engagement, actor=author, expected_revision=revision or None
            )
        if named is not None:
            # Promotes the earliest planned row unless this save's stage closes
            # the Matter: the closure cancels every planned row on the way out
            # (docs/adr/0143 §A7), and promoting one first wrote a
            # «→ Järgmine samm» under the save that ended the file — the rule
            # `complete_current_action` follows.
            result.action = complete_next_action(
                action=named, actor=author, promote=not move.closes
            )
        return result


def _read_back_deadline(locked_matter: Matter, matter: Matter) -> None:
    """The deadline fields as the row now holds them, on both instances."""
    locked_matter.refresh_from_db(fields=["response_deadline", "response_requested_at"])
    matter.response_deadline = locked_matter.response_deadline
    matter.response_requested_at = locked_matter.response_requested_at


#: The fast path names a Teema that is not a Chamber proposal (a forged POST).
SENT_PROPOSAL_NEEDS_AN_INITIATIVE = (
    "Saadetud Koja arvamuseks saab märkida ainult õigusakti «Koja ettepanek või pöördumine» teema."
)
#: The fast path with no file in `Failid` — nothing to stand the send on.
SENT_PROPOSAL_NEEDS_A_FILE = "Saadetud Koja arvamuseks märkimiseks lisa fail, mis välja saadeti."


@transaction.atomic
def record_sent_proposal_on_new_matter(
    *,
    matter: Matter,
    documents: Sequence[Document],
    author: Any,
    recipients: Sequence[Any],
    sent_on: Any,
) -> Any:
    """«Märgi saadetud Koja arvamuseks» — the proposal already went out (2026-10-12).

    `Uus teema` files a Chamber proposal or appeal that has **already been
    sent**, with the files that went, and records the send in the same save
    (docs/adr/0151, amendment of 2026-10-12). This is convenience over the
    existing send, not a second one:

    * **The files are the Teema's documents, once.** The caller has just filed
      them through the ordinary `Uus teema` paths — one `Document` and one
      immutable `DocumentVersion` per file, filed as the Chamber's own letter —
      and they are handed here in the order they were offered. Nothing is
      uploaded, copied or re-read: the first is the letter (`final_version`),
      each further one a further sent file of **this same** `Submission`
      (`bind_further_sent_files`, docs/adr/0144 §5). One package, one record.
    * **The canonical send.** `register_sent_opinion_on_open_matter` — the act
      the Dokumendid «Registreeri saatmine» panel posts to — creates the
      `Submission` as kind `KODA_PROPOSAL`, binds the evidence under the locks,
      stamps the supplied day (`SentAtPrecision.DATE`, Tallinn midnight) and
      writes the creation and send events; then `schedule_follow_up_of`
      schedules the thirty-day check off that recorded day (docs/adr/0146).
      Nothing here writes a status, a date or a check of its own.
    * **A deadline given in the same save is answered by the send**
      (amendment of 2026-10-13): ended as `ANSWERED`, linked to this
      `Submission`, so nothing reads as still owed.
    * **The check is the next work.** Nothing else is current on a Teema created
      a moment ago with no plan, so the check is promoted by the established
      rule (`promote_next_planned_action`, docs/adr/0143 §A4) and `PRAEGUNE
      TEGEVUS` reads «Kontrolli, mis on pöördumisest saanud» — not «Järgmine
      samm on määramata», and no «Tutvu materjaliga» beside it.

    Refused — and with it the whole creation, because this runs inside it —
    when the Teema is not a Chamber initiative or there is no file. The
    service below refuses a missing day, a future day and a send with no
    addressee in its own words.
    """
    from datetime import datetime, time

    from app.submissions.enums import SentAtPrecision, SubmissionKind
    from app.submissions.services import register_sent_opinion_on_open_matter
    from app.workflow.services import promote_next_planned_action

    locked_matter = lock_open_matter_for_business_write(matter.pk)
    if not is_koda_initiative(locked_matter):
        raise DomainError(SENT_PROPOSAL_NEEDS_AN_INITIATIVE)
    package = [document for document in documents if document is not None]
    if not package:
        raise DomainError(SENT_PROPOSAL_NEEDS_A_FILE)
    if sent_on is None:
        raise DomainError("Saatmise registreerimiseks on vaja saatmise kuupäeva.")
    # Read from the rows, not from the instances the caller holds: the version
    # each file was filed with is the exact bytes that went out.
    for document in package:
        document.refresh_from_db(fields=["current_version"])
    versions = [
        document.current_version for document in package if document.current_version is not None
    ]
    if len(versions) != len(package):
        raise DomainError(SENT_PROPOSAL_NEEDS_A_FILE)
    letter = package[0]
    sent = register_sent_opinion_on_open_matter(
        document=letter,
        version=versions[0],
        title=letter.title,
        kind=SubmissionKind.KODA_PROPOSAL,
        actor=author,
        recipients=list(recipients),
        # Midnight in Europe/Tallinn, carried as a day — the same anchor
        # `+ Koja arvamus` writes, so no surface reads «00:00» back
        # (app/submissions/enums.py, docs/adr/0079 §2).
        sent_at=timezone.make_aware(datetime.combine(sent_on, time.min)),
        sent_at_precision=SentAtPrecision.DATE,
        further_versions=versions[1:],
    )
    # **A deadline given in the same save is answered by this send** (owner's
    # decision of 2026-10-13, docs/adr/0151 amendment). The optional
    # `Arvamuse tähtaeg` is the day the proposal was due; it went out, so the
    # deadline ends as answered by exactly this `Submission`. The send wrapper
    # above did that (`answer_initiative_deadline_of`) — the one rule every
    # interactive send on an initiative follows, so a later `+ Koja arvamus`
    # converges on the same state (QA-WORKFLOW-04). A tracked deadline is never
    # discharged implicitly (`work_items._discharge_exists`).
    _read_back_deadline(locked_matter, matter)
    promote_next_planned_action(matter=locked_matter, actor=author)
    return sent


#: What `+ Lisa töödokument` answers for anything but a letter that went out.
OPINION_WORKING_DOCUMENTS_NEED_A_SEND = (
    "Töödokumendi saab lisada ainult välja saadetud Koja arvamusele."
)


@transaction.atomic
def add_opinion_working_documents(
    *,
    submission: Any,
    author: Any,
    uploads: Sequence[Any] = (),
) -> WorkspaceResult:
    """`+ Lisa töödokument` — the DOCX an opinion was drafted in, filed after the send.

    A lawyer registers the signed letter and only then thinks to keep the
    editable file beside it. This files it **under that exact opinion**: an
    ordinary `Document` + `DocumentVersion` with the role `Töödokument`, tied to
    the `Submission` by a `DocumentLink` (docs/adr/0129 §2, §7).

    **Additive, and that is the whole of it** — the act `add_development_evidence`
    performs, on the record a letter is. No second `Koja arvamus`, no change to
    the sent file, the send date, the addressees, the `Kokkuvõte` or the status:
    those are `Muuda` and `Võta tagasi`, acts with their own events. The opinion's
    evidence is not read here, so nothing can replace or detach it, and nothing
    here can make a working document count as what was sent (docs/adr/0129 §5).

    **One operation, and no chronology row of its own.** The files read under
    the opinion's «Arvamus välja» row; the upload is not a «lisas dokumendi» line
    beside it, because attaching the opinion's own working file is maintaining
    that record rather than a new act on the file (`_versions_shown_on_their_record`).

    **Only a letter that went out.** Sent, or withdrawn or superseded since — a
    withdrawn opinion's working file is still that letter's history. A draft is
    refused: it has no row to read the files under, and «the file this opinion
    was drafted in» is not yet a fact about a send.

    **Restricted with the opinion.** A `Submission` restricted below its Matter
    gives its working documents the same restriction, so the files cannot be
    listed to somebody who may not see the letter they belong to — decided by
    the capture itself, as for every record (docs/adr/0137).

    **Refused on a closed Matter**, under the Matter's row lock, the rule every
    addition on this workspace keeps (docs/adr/0076 §2). All or none: one refused
    file refuses the save, before any bytes are stored.
    """
    from app.documents.services import OPINION_WORKING_DOCUMENT_ROLE
    from app.submissions.models import Submission

    files = _uploads(uploads)
    if not files:
        # Stated here as well as on the form: a form is not a boundary, and an
        # operation that wrote nothing and succeeded would be the page telling
        # somebody their file was kept.
        raise DomainError("Vali vähemalt üks fail.")
    locked_matter = lock_open_matter_for_business_write(submission.matter_id)
    # Re-read under the lock and against the locked Matter: «this opinion is on
    # this Teema and went out» is the one claim the link cannot make for itself.
    if not Submission.objects.filter(pk=submission.pk, matter=locked_matter).exists():
        raise DomainError("Seda Koja arvamust ei ole sellel teemal.")
    current = lock_submission_for_evidence_integrity(submission.pk)
    if not Submission.objects.filter(pk=current.pk).historically_sent().exists():
        raise DomainError(OPINION_WORKING_DOCUMENTS_NEED_A_SEND)

    with composer_operation() as operation_id:
        result = WorkspaceResult(operation_id=operation_id)
        result.record = current
        result.documents = capture_supporting_evidence(
            matter=locked_matter,
            record=current,
            uploads=files,
            actor=author,
            role=OPINION_WORKING_DOCUMENT_ROLE,
        )
        return result


@transaction.atomic
def add_procedural_development(
    *,
    matter: Matter,
    author: Any,
    title: str,
    occurred_on: Any = None,
    occurred_on_precision: str = DatePrecision.EXACT.value,
    note: str = "",
    stage: Any = None,
    next_text: str = "",
    next_date: Any = None,
    as_next_step: bool = False,
    date_phase: bool = False,
    uploads: Sequence[Any] = (),
    follow_ups_confirmed: bool | str = False,
) -> WorkspaceResult:
    """`+ Menetluse areng` — the procedure moved, and what the lawyer does about it.

    **`date_phase` — the stage move is also the phase's day, when the person
    says so** (docs/adr/0128 §1). A `Märge` that moves `Hetkeseis` onto a phase
    it alone places the file on — `Kooskõlastusringil` onto `Kooskõlastusring`,
    `Valitsuses`, `Riigikogus` and the two European ones — may carry the ticked
    `Märgi ka menetluse kulgu: <faas> <päev>`, and then the same save writes this
    `Märge`'s day as that phase's roadmap date, so the one transition is entered
    once. Decided on the locked Matter and its stage *before* the move
    (`confirmable_phase`), and written by `record_confirmed_phase_date`, which
    writes nothing for a phase already dated, a day still ahead or a day out of
    the procedure's order. Without the tick, or for any other move, the stage
    moves exactly as it always did and no phase is dated.

    The operation the file had no way to record, and the reason a Matter used to
    end at «Arvamus saadetud» with nothing to press. A ministry sends a revised
    draft; the Chamber reads it; the draft goes to the Ministry of Justice; the
    government approves it; the file reaches the Riigikogu. Each of those is one
    dated step, and recording one used to mean up to three saves in three places —
    a `Märge` with no date box, a `Hetkeseis` change in the header, and
    `+ Järgmine tegevus` under the launcher (lawyer feedback 14, docs/adr/0091 §5).

    **One canonical `MatterProceduralDevelopment`**, beside `MatterEngagement`
    and `MatterExternalPosition`. It was an `Entry` of a new `EntryKind` for one
    round, and the Package D discovery is what retired that: an incoming
    development cannot be projected truthfully from an `Entry`, because the date
    cannot be unknown, the lawyer's note has nowhere to go that is not the
    ministry's own sentence, and a projection would have to parse a title out of
    prose. The model's docstring carries the whole argument.

    **Up to four canonical writes, and one transaction.** The record, its
    evidence, the `Hetkeseis` and the next step. Any one of them failing must
    leave the Matter exactly as it was — a stage that moved with no development
    recorded would be a file claiming to be in the Riigikogu with nothing on it
    saying how it got there, and a `NextAction` written twice by a retried request
    would be work nobody assigned. Ordered as the composer orders its own: the
    record first, then its evidence, then the stage, then the step.

    **Nothing is derived from the title.** No stage is inferred from the words, no
    next step is generated, and a save that names neither changes neither. What a
    person did not answer is not a thing this function decides for them. Nor is
    anything read from or written to a `Menetluse link`: Package B's links are
    references, and a reference is not an event (docs/adr/0091 §5.3, §5.6).

    ``stage`` goes through `change_stage`, which is the canonical service and
    writes its own `MATTER_STAGE_CHANGED` event; a stage equal to the one the file
    already has is that service's own no-op rather than a second audit row. The
    two writes share this operation's identifier, which is what lets a reader —
    and Package D — tie «the file moved to Kooskõlastusringil» to the development
    that moved it without either record holding a copy of the other.

    ``next_text`` and ``next_date`` go through `set_next_action_for_new_work`,
    which is the native boundary: somebody is assigning work today, so the
    departed-owner rule applies exactly as it does on `+ Järgmine tegevus`. A step
    written here supersedes whatever was open, which is `NextAction`'s one-open
    invariant and not a decision this function makes.

    **``as_next_step`` — the activity is itself the next step** (docs/adr/0124).
    `+ Märge` no longer asks for a second sentence and a second date: a lawyer
    writes one activity, dates it, and a day after today offers `Märgi
    järgmiseks tegevuseks`. Ticked, ``title`` and ``occurred_on`` *are*
    ``next_text`` and ``next_date``, and go through the same call above — no
    other step is written, and nothing about the step is copied anywhere a
    later correction of this row would have to keep in step with. Two rules
    hold here and not only in the panel, because a form is not a boundary:

    * **only a day after today makes a step**, on this module's clock
      (`timezone.localdate()`, Europe/Tallinn). A past, today's or empty day
      makes the flag inert — the panel hides it there, and a value left from a
      moment the date was ahead must not quietly write one;
    * **a step is its sentence**: ticked on an ahead day with no ``title`` is
      refused with the refusal every step control gives, before anything is
      written (`NEXT_STEP_NEEDS_SENTENCE`).

    Naming both ``as_next_step`` and ``next_text`` is a caller's mistake — two
    answers to one question — and raises rather than choosing between them.

    **What this refuses is an empty operation, and that is the only thing it
    refuses about content.** ``title`` is optional since docs/adr/0105 §4 — a
    paper that arrived, the file moving to `Riigikogus`, «vaatan uue versiooni
    üle, 25.09» are each a whole record and none of them needs a headline — but a
    press that writes no sentence, no note, no file, no stage change *and* no
    step would leave a dated row on the file saying nothing at all. This is where
    the effects can be seen together, which is why the rule is here rather than
    on any one of the services below (`development_save_says_something`).

    **Decided on the locked Matter, by what the save would write** (ENG-060). A
    stage counts only when it *moves* the file: choosing the one the file already
    has makes `change_stage` write nothing, so «Uus hetkeseis: <the current one>»
    and nothing else is a press with nothing in it. And «the one it already has»
    is read from the row `lock_open_matter_for_business_write` returns, never from
    ``matter`` — the view fetched that before the lock, and a colleague's stage
    change committed in between would make it answer for a moment that has
    passed. So the refusal now queues behind the row lock, which is the price of
    it being right (docs/adr/0105 §4, as amended 2026-09-26).

    **No `Etapp`, and none is inferred** (docs/adr/0105, amended 2026-09-27). A
    `Märge` is a record of what happened, and the owner decided the lawyer is not
    asked to file it under a phase of somebody else's procedure. There is no
    parameter for one, so no caller of this use case — the panel, a crafted POST,
    a script — can place the record on a phase, and nothing is derived in its
    place: not from `Hetkeseis`, the stage chosen here, the `Õigusakt`, the words
    or the date. The row stores the column's empty value. A phase already stored
    on an older row is untouched, and `Muuda` on that row still corrects it
    (`correct_procedural_development`).
    """
    from app.matters.services import (
        DEVELOPMENT_NEEDS_SOMETHING,
        development_save_says_something,
        record_confirmed_phase_date,
        record_procedural_development,
        record_procedural_development_document,
        stage_transition,
    )

    if as_next_step and (next_text or "").strip():
        raise ValueError("as_next_step makes the Märge the step; next_text names another one.")
    # **A stage that ends the Matter makes no step** (docs/adr/0131 §10). The
    # save closes the file, and the closure cancels any open step — so a step
    # written by the same press would be an instruction born cancelled. The
    # flag is inert here exactly as it is on a day that is not ahead.
    closes = is_terminal_stage(getattr(stage, "key", None))
    if closes:
        as_next_step = False
    step_text = next_text
    step_date = next_date
    if as_next_step and occurred_on is not None and occurred_on > timezone.localdate():
        step_text = (title or "").strip()
        step_date = occurred_on
        if not step_text:
            raise DomainError(NEXT_STEP_NEEDS_SENTENCE)
    if closes and (step_text or "").strip():
        raise DomainError(TERMINAL_STAGE_MAKES_NO_STEP)

    locked_matter = lock_open_matter_for_business_write(matter.pk)
    moves_stage = stage is not None and stage.pk != locked_matter.stage_id
    if not development_save_says_something(
        title=title,
        note=note,
        has_files=bool(_uploads(uploads)),
        moves_stage=moves_stage,
        next_text=step_text,
    ):
        raise DomainError(DEVELOPMENT_NEEDS_SOMETHING)
    if moves_stage:
        follow_ups_confirmed = _refuse_an_unconfirmed_closure(
            locked_matter, stage, confirmed=follow_ups_confirmed
        )

    # The phase this move may date, read on the locked row *before* it moves:
    # «forward» is a question about where the file stood.
    pattern, phase_to_date = None, ""
    if (
        moves_stage
        and date_phase
        and occurred_on is not None
        and (occurred_on_precision == DatePrecision.EXACT.value)
    ):
        from app.matters.legal_process import phase_context
        from app.matters.process_phases import confirmable_phase

        facts = phase_context(matter=locked_matter)
        pattern = facts.pattern
        phase_to_date = confirmable_phase(pattern, from_stage=facts.stage_key, to_stage=stage.key)

    with composer_operation() as operation_id:
        result = WorkspaceResult(operation_id=operation_id)
        # **The move is made first and the `Märge` is still the old period's**
        # (docs/adr/0131 §5). `stage_transition` pins the period that was
        # current before the move, so the record, its files and its step are
        # written in it — the work was done there and the stage moves after it.
        # A file with no stage yet binds them to the first period this opens.
        # And a stage that ends the Matter closes it on the way out of the
        # block, after the record it was saved with.
        with stage_transition(
            matter=locked_matter,
            stage=stage if moves_stage else locked_matter.stage,
            actor=author,
            follow_ups_confirmed=follow_ups_confirmed,
        ) as move:
            development = record_procedural_development(
                matter=locked_matter,
                title=title,
                occurred_on=occurred_on,
                occurred_on_precision=occurred_on_precision,
                note=note,
                actor=author,
            )
            result.record = development
            result.documents = capture_supporting_evidence(
                matter=locked_matter,
                record=development,
                uploads=_uploads(uploads),
                actor=author,
            )
            for document in result.documents:
                record_procedural_development_document(
                    development=development, document=document, actor=author
                )
            if phase_to_date:
                result.phase_dated = record_confirmed_phase_date(
                    matter=locked_matter,
                    pattern=pattern,
                    phase_key=phase_to_date,
                    day=occurred_on,
                    actor=author,
                )
            text = (step_text or "").strip()
            if text:
                result.action = set_next_action_for_new_work(
                    matter=locked_matter,
                    text=text,
                    target_date=step_date,
                    actor=author,
                )
        result.closed = move.closes
        return result


@transaction.atomic
def add_development_evidence(
    *,
    development: Any,
    author: Any,
    uploads: Sequence[Any] = (),
) -> WorkspaceResult:
    """`+ Lisa fail` — more evidence for a step the file already records.

    **Additive, and that is the whole of it.** A ministry sends the revised draft
    a fortnight after the development was written up; a colleague finds the
    committee's text in the mailbox. The step itself was recorded correctly and
    is not being corrected — the file simply learns about another paper that
    supports it. So this writes a `Document`, its immutable `DocumentVersion` and
    the `DocumentLink` naming which step these bytes evidence, and touches
    nothing else on the record: not `Sündmus`, not the period, not
    `Juristi märkus`, not `Matter.stage`, and not the `NextAction` the original
    save may have opened. Those are separately correctable facts with their own
    surfaces (`correct_procedural_development`, docs/adr/0091 §5.4).

    **Never a replacement and never a removal.** The files the development
    already carries are not read here, so nothing can detach one, and no
    `DocumentVersion` is superseded: a revised draft is *new* bytes on a new
    document, which is what makes the evidence store immutable rather than merely
    append-mostly (docs/adr/0084 §8).

    **No revision token, deliberately.** Optimistic concurrency exists on this
    record's *correction* because two people editing one sentence is a lost
    update. Two people attaching two different papers to one step is not: both
    links are wanted, `link_document_to_record` is idempotent on the pair, and
    there is no earlier value for a later writer to overwrite. Inventing a token
    here would refuse the second lawyer's file to protect a sentence nobody
    touched — and no evidence capture in this module carries one
    (`capture_supporting_evidence`, `add_engagement_feedback`).

    **Refused on a closed Matter**, under the Matter's row lock rather than by
    whether a page drew a button: a browser that had `Teema käik` open before
    somebody else closed the file still has the control on it, and its POST
    reaches a server with no memory of which page it came from. Reopening is the
    way out and leaves somebody's name on both decisions (docs/adr/0076 §2).

    All or none. `capture_supporting_evidence` catches nothing, so a second file
    being refused unwinds the first along with this transaction — a step claiming
    evidence and holding half of it is the state this ordering exists to make
    unreachable (docs/adr/0075 §8).
    """
    from app.matters.models import MatterProceduralDevelopment
    from app.matters.services import record_procedural_development_document

    locked_matter = lock_open_matter_for_business_write(development.matter_id)
    # Re-read under the lock and against the locked Matter, the same existence
    # check `correct_procedural_development` makes and for the same reason: the
    # instance this arrived with was fetched before the lock, and «this
    # development is on this Teema» is the one claim the link below cannot make
    # for itself.
    try:
        current = MatterProceduralDevelopment.objects.select_for_update(no_key=True).get(
            pk=development.pk, matter=locked_matter
        )
    except MatterProceduralDevelopment.DoesNotExist:
        raise DomainError("Seda menetluse arengut ei ole sellel teemal.") from None

    with composer_operation() as operation_id:
        result = WorkspaceResult(operation_id=operation_id)
        result.record = current
        result.documents = capture_supporting_evidence(
            matter=locked_matter,
            record=current,
            uploads=_uploads(uploads),
            actor=author,
        )
        for document in result.documents:
            # `DOCUMENT_CREATED` and `EVIDENCE_VERSION_ADDED` say bytes arrived on
            # the Matter; neither says they are the ministry's revised draft
            # rather than something else that turned up the same afternoon. The
            # same event `+ Menetluse areng` records for the files that arrive
            # with the step.
            record_procedural_development_document(
                development=current, document=document, actor=author
            )
        return result


@transaction.atomic
def add_external_position_evidence(
    *,
    position: Any,
    author: Any,
    uploads: Sequence[Any] = (),
) -> WorkspaceResult:
    """`+ Lisa fail` — another paper supporting a position the file already holds.

    The act `add_development_evidence` performs, on the other record that
    carries evidence, with the same reasoning and the same refusals. It exists
    because the position panel captured files only at the moment of capture:
    an association that sends its position paper a week after somebody wrote
    down what it said on the telephone had nowhere on the file to put it, and
    `Muuda` deliberately does not take bytes (QA-021, docs/adr/0084 §8).

    **Additive, and that is the whole of it.** The organisation, the date, the
    `Seisukoht`, the address, `Juristi märkus` and `Liige` are untouched: those
    are separately correctable facts with their own surface. The files the
    position already carries are not read here, so nothing can detach one, and
    no `DocumentVersion` is superseded — a revised paper is *new* bytes on a new
    document, which is what makes the evidence store immutable rather than
    append-mostly.

    **No revision token**, for `add_development_evidence`'s reason: two people
    attaching two different papers to one position is not a lost update, both
    links are wanted, and there is no earlier value for a later writer to
    overwrite.

    **Refused on a closed Matter**, under the Matter's row lock rather than by
    whether a page drew a button. All or none: a second file being refused
    unwinds the first with this transaction.
    """
    from app.matters.models import MatterExternalPosition
    from app.matters.services import record_external_position_document

    locked_matter = lock_open_matter_for_business_write(position.matter_id)
    # Re-read under the lock and against the locked Matter: the instance this
    # arrived with was fetched before the lock, and «this position is on this
    # Teema» is the one claim the link below cannot make for itself.
    try:
        current = MatterExternalPosition.objects.select_for_update(no_key=True).get(
            pk=position.pk, matter=locked_matter
        )
    except MatterExternalPosition.DoesNotExist:
        raise DomainError("Seda välist seisukohta ei ole sellel teemal.") from None

    with composer_operation() as operation_id:
        result = WorkspaceResult(operation_id=operation_id)
        result.record = current
        result.documents = capture_supporting_evidence(
            matter=locked_matter,
            record=current,
            uploads=_uploads(uploads),
            actor=author,
        )
        for document in result.documents:
            record_external_position_document(position=current, document=document, actor=author)
        return result


@transaction.atomic
def add_matter_website_overview(
    *,
    matter: Matter,
    author: Any,
    url: str = "",
    published_on: Any = None,
    title: str = "",
    kind: str = "",
) -> WorkspaceResult:
    """`+ Ülevaade / uudis` — a plan, or a page that is already up.

    docs/adr/0081 §1 gave this one button and no fields, because at the moment
    somebody decides a Matter should be written up there is no address and no
    publication date. That holds for the case it describes. What it did not
    cover is the lawyer recording a write-up *after* the page is published, who
    had to file a plan and then publish it from a second control to say a thing
    that was already true (docs/adr/0083).

    So both shapes arrive here. With no address this is the plan, byte for byte
    what it always was. With one — and with or without a publication date, since
    docs/adr/0089 §8 — the record is planned and published inside **one**
    transaction and one `composer_operation`, which is why the
    publication reuses `publish_website_overview` rather than writing a second
    direct-to-`PUBLISHED` path: the address rule, the both-or-neither rule and
    the audit events all stay in the one reviewed place, and the lifecycle is the
    documented `PLANNED → PUBLISHED` rather than a fourth way in.

    **Two audit events, deliberately.** A row created and published in one act
    genuinely passed through both states, and a history saying only «published»
    would lose that somebody planned it at all. `expected_revision` is not
    passed to the publication: the row was created microseconds earlier inside
    this transaction and nothing else can have moved it, so there is no version
    to be stale against.

    Takes the lock and the closed-Matter question through the same helper as
    every other operation in this module: a closed Teema renders no launcher,
    and that decides nothing about a POST arriving from a tab that was open
    before it was closed (R2-02).

    It finishes no step and touches no plan: since docs/adr/0141 `Tööplaan` is
    no longer an active feature, and the overview is only the overview.
    """
    locked_matter = lock_open_matter_for_business_write(matter.pk)
    with composer_operation() as operation_id:
        result = WorkspaceResult(operation_id=operation_id)
        overview = plan_website_overview(matter=locked_matter, actor=author, kind=kind)
        # **The address decides it, and the date does not.** Since
        # docs/adr/0089 §8 a publication whose day is unknown is an ordinary
        # publication, so an address with an empty date box records one rather
        # than falling back to a plan — which would have been the silent shape
        # of the old refusal: somebody pastes a link, no date, and the file says
        # the write-up is still owed.
        if url:
            overview = publish_website_overview(
                overview=overview,
                url=url,
                published_on=published_on,
                actor=author,
                title=title,
            )
        result.record = overview
        return result


@transaction.atomic
def publish_planned_website_overview(
    *,
    matter: Matter,
    author: Any,
    overview: Any,
    url: str,
    published_on: Any,
    expected_revision: str | None = None,
    title: str = "",
) -> WorkspaceResult:
    """`Avalda` — the page is up, and this is its address.

    The guarded half of the pair. Recording a publication is new business
    content, so the Matter is locked and a closed one refuses before anything is
    read — which is what stops a stale tab publishing onto a file somebody
    closed in the meantime.

    Correcting an address that is *already* recorded is the other half, and it
    deliberately does not come through here: `correct_matter_website_overview`
    takes no such lock, because a link recorded wrongly on a closed Matter must
    still be correctable (docs/adr/0081 §5).
    """
    lock_open_matter_for_business_write(matter.pk)
    with composer_operation() as operation_id:
        result = WorkspaceResult(operation_id=operation_id)
        result.record = publish_website_overview(
            overview=overview,
            url=url,
            published_on=published_on,
            actor=author,
            expected_revision=expected_revision,
            title=title,
        )
        return result


@transaction.atomic
def cancel_matter_website_overview(
    *,
    matter: Matter,
    author: Any,
    overview: Any,
    expected_revision: str | None = None,
) -> WorkspaceResult:
    """`Tühista` — the write-up is not going to happen after all.

    Guarded like every other write here. A closure already cancels the plans a
    Matter still owes, in `close_matter`'s own transaction; this is the same act
    performed deliberately on one plan while the file is still open.
    """
    lock_open_matter_for_business_write(matter.pk)
    with composer_operation() as operation_id:
        result = WorkspaceResult(operation_id=operation_id)
        result.record = cancel_website_overview(
            overview=overview, actor=author, expected_revision=expected_revision
        )
        return result


@transaction.atomic
def correct_matter_website_overview(
    *,
    author: Any,
    overview: Any,
    url: str,
    published_on: Any,
    expected_revision: str | None = None,
    title: str | None = None,
) -> WorkspaceResult:
    """`Muuda` — what the file says about an existing page was wrong.

    ``title`` is `None` when the caller did not ask about the `Pealkiri`, and
    then the stored one is left as it is (docs/adr/0127 §1).

    **The one operation in this module that takes no closed-Matter guard, and it
    must not.** Closure means no new business content; it has never meant that a
    fact recorded wrongly must stay wrong. The service underneath refuses
    anything that is not already published, so there is no route from here to
    publishing a plan on a closed file — the transition that creates a
    publication is the guarded one (docs/adr/0075 §12, docs/adr/0081 §5).

    It takes no ``matter`` either, for the same reason `edit_entry` does not: the
    record names its own Matter, and a parameter that could disagree with it is a
    parameter that will.
    """
    with composer_operation() as operation_id:
        result = WorkspaceResult(operation_id=operation_id)
        named: dict[str, Any] = {} if title is None else {"title": title}
        result.record = correct_website_overview_link(
            overview=overview,
            url=url,
            published_on=published_on,
            actor=author,
            expected_revision=expected_revision,
            **named,
        )
        return result


@transaction.atomic
def add_matter_procedural_link(
    *,
    matter: Matter,
    author: Any,
    kind: Any,
    url: Any,
    label: str = "",
) -> WorkspaceResult:
    """`+ Menetluse link` — where the official proceeding on this file lives.

    One row and nothing else: no file is captured, no page is fetched, no
    `Document` is created and no background work is scheduled. The address is
    recorded exactly as it was pasted (docs/adr/0089 §4).

    Takes the lock and the closed-Matter question through the same helper as
    every other operation in this module. A closed Teema renders no launcher,
    and that decides nothing about a POST arriving from a tab that was open
    before somebody else shut the file (R2-02).

    A repeated submission is the service's own concern rather than this one's:
    `record_procedural_link` returns the row that is already there when the
    answers agree, so a double-click writes one row and one audit event.
    """
    from app.matters.services import record_procedural_link

    locked_matter = lock_open_matter_for_business_write(matter.pk)
    with composer_operation() as operation_id:
        result = WorkspaceResult(operation_id=operation_id)
        result.record = record_procedural_link(
            matter=locked_matter,
            kind=kind,
            url=url,
            label=label,
            actor=author,
        )
        return result


@transaction.atomic
def correct_matter_procedural_link(
    *,
    author: Any,
    link: Any,
    kind: Any,
    url: Any,
    label: str = "",
    expected_revision: str | None = None,
) -> WorkspaceResult:
    """`Paranda` — the kind, the name or the address on an existing row was wrong.

    **Takes no closed-Matter guard, and must not.** Closure means no new
    business content; it has never meant that an address recorded wrongly must
    stay wrong. The same exception, for the same reason, as
    `correct_matter_website_overview` (docs/adr/0075 §12, docs/adr/0081 §5).

    It takes no ``matter`` either, for the reason that one does not: the record
    names its own Matter, and a parameter that could disagree with it is a
    parameter that will.
    """
    from app.matters.services import correct_procedural_link

    with composer_operation() as operation_id:
        result = WorkspaceResult(operation_id=operation_id)
        result.record = correct_procedural_link(
            link=link,
            kind=kind,
            url=url,
            label=label,
            actor=author,
            expected_revision=expected_revision,
        )
        return result


# **`close_matter_from_workspace` was retired by docs/adr/0131 §11 and restored
# by its amendment of 2026-10-25** as `Lõpeta teema`, the direct door. A Matter
# also ends when its `Hetkeseis` says so: `+ Märge` and
# `+ Koja arvamus` move the stage through `stage_transition`, which closes the
# Matter through `close_matter_for_terminal_stage` after the act it was saved
# with — the order this use case kept (the win first, the closure last).


def _no_procedural_link() -> None:
    """`edit_matter`'s default: the save answers no `Menetluse link` block."""


def edit_matter(
    *,
    matter: Matter,
    actor: Any,
    expected_revision: str | None,
    title: str,
    brief_summary: str,
    owner: Any,
    source_organisations: Any,
    sender_name: str,
    legal_instruments: Sequence[Any],
    legal_instrument_other: str,
    received_date: Any,
    response_deadline: Any,
    policy_areas: Sequence[Any],
    policy_area_other: str,
    stage: Any,
    follow_ups_confirmed: bool = False,
    deadline_change: str = "",
    previous_outcome: str = "",
    previous_submission: Any = None,
    previous_note: str = "",
    save_procedural_link: Callable[[], None] = _no_procedural_link,
) -> None:
    """`Muuda teemat` — the whole record, corrected in one save (JARCH-001).

    **One transaction.** Either every value is applied or none is: a
    half-saved correction leaves a record stating a combination of facts nobody
    chose, and a timeline claiming somebody chose it. **One `Hetkeseis`
    period**: every correction is written in the period current when the page
    was saved, and the stage change — made last — begins the next one after
    them (`stage_episode_scope`, docs/adr/0131 §5).

    **One service per fact**, each returning early when handed the value
    already there, so each change is audited exactly as it is inline and an
    unchanged field writes nothing. Called in this order, and the order is part
    of the contract:

    1. `guard_matter_revision` — first: the page posts every field it holds, so
       a copy filled before somebody else's save is refused rather than
       written over fresh values (QA-002, `MatterEditConflict`);
    2. the instruments as they stood **before** this save are read, because
       only a change of `Õigusakt` may move `Koja algatus` — so this read must
       come before `set_legal_instruments` (`initiative_after_edit`);
    3. title, `brief_summary`, owner;
    4. the senders — a typed name resolved inside this transaction, so any
       refusal below takes a newly created institution with it. The addressee
       is **not** passed: `_UNSET` keeps a register addressee through every
       save (docs/adr/0097 §4);
    5. `Koja algatus` before the dates, so the date writers judge them against
       the answer this save gives (docs/adr/0151 §2);
    6. `Saabunud`, then `Arvamuse tähtaeg` with what a changed deadline means
       (``deadline_change``, ``previous_outcome``, ``previous_submission``,
       ``previous_note``);
    7. Valdkonnad, then the instruments — through their service, so the
       classification never moves without a change event (task §19);
    8. ``save_procedural_link`` — the caller's `Menetluse link` block, inside
       this transaction, so an address refused there takes the whole
       correction back (`ProceduralLinkConflict`, docs/adr/0089 §7);
    9. **`Hetkeseis` last**, through the one transition every surface uses —
       for a terminal stage on an open Matter, the closure, after every other
       fact has been written to the open file (docs/adr/0131 §6, §10). Ending
       live follow-up work is refused unless ``follow_ups_confirmed``
       (`FollowUpClosureUnconfirmed`, docs/adr/0146 §8).

    **Every fact on the page is a required argument**, because this is a
    whole-record save: a value a caller forgot must not be written as a
    default — a forgotten `received_date` would clear the date. No track, tags
    or visibility: those are off the page, and a value the form does not ask
    for is never written at all (docs/adr/0096 §3, docs/adr/0097 §2, §3).
    Set-valued answers arrive as sequences, so an empty one is «none of them»
    — a decision — and never «leave them alone».
    """
    from app.audit.operations import stage_episode_scope
    from app.matters.initiative import apply_initiative_after_edit
    from app.matters.response_deadlines import change_response_deadline
    from app.matters.services import (
        assign_matter,
        change_stage,
        guard_matter_revision,
        resolve_source_organisations,
        set_brief_summary,
        set_legal_instrument_other,
        set_legal_instruments,
        set_matter_dates,
        set_matter_title,
        set_organisations,
        set_policy_area_other,
        set_policy_areas,
    )

    with transaction.atomic(), stage_episode_scope():
        guard_matter_revision(matter=matter, expected_revision=expected_revision)
        held_instruments = list(matter.legal_instruments.all())
        set_matter_title(matter=matter, value=title, actor=actor)
        set_brief_summary(matter=matter, value=brief_summary, actor=actor)
        assign_matter(matter=matter, owner=owner, actor=actor)
        set_organisations(
            matter=matter,
            source_organisations=resolve_source_organisations(
                chosen=source_organisations, typed_name=sender_name
            ),
            actor=actor,
        )
        apply_initiative_after_edit(
            matter=matter,
            held=held_instruments,
            chosen=list(legal_instruments),
            actor=actor,
        )
        set_matter_dates(matter=matter, received_date=received_date, actor=actor)
        change_response_deadline(
            matter=matter,
            deadline=response_deadline,
            actor=actor,
            change=deadline_change,
            previous_outcome=previous_outcome,
            previous_submission=previous_submission,
            previous_note=previous_note,
        )
        set_policy_areas(matter=matter, policy_areas=list(policy_areas), actor=actor)
        set_policy_area_other(matter=matter, value=policy_area_other, actor=actor)
        set_legal_instruments(matter=matter, legal_instruments=list(legal_instruments), actor=actor)
        set_legal_instrument_other(matter=matter, value=legal_instrument_other, actor=actor)
        save_procedural_link()
        change_stage(
            matter=matter,
            stage=stage,
            actor=actor,
            follow_ups_confirmed=follow_ups_confirmed,
        )
