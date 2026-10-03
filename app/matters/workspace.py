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
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from django.db import transaction
from django.utils import timezone

from app.audit.operations import composer_operation
from app.core.errors import DomainError
from app.documents.enums import DocumentRole
from app.documents.models import Document
from app.documents.services import capture_supporting_evidence
from app.matters.entry_enums import EntryKind
from app.matters.enums import EngagementKind, ExternalPositionProvenance
from app.matters.locks import (
    lock_open_matter_for_business_write,
    lock_submission_for_evidence_integrity,
)
from app.matters.models import Entry, Matter
from app.matters.services import (
    add_engagement,
    add_entry,
    cancel_website_overview,
    complete_engagement_feedback,
    correct_website_overview_link,
    plan_website_overview,
    publish_website_overview,
)
from app.workflow import plan as work_plan
from app.workflow.enums import ActionStatus, DatePrecision, PlanStepOperation
from app.workflow.models import MatterPlanStep, NextAction
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

#: Refused when a save launched from the current `Tööplaan` step arrives after
#: that step stopped being current — finished, replaced or never started from
#: here. Nothing is written: the record would otherwise finish a step nobody
#: chose (docs/adr/0133 §6).
PLAN_STEP_NOT_CURRENT = (
    "Tööplaani samm ei ole enam praegune tegevus. Värskenda lehte ja vaata, mis on nüüd pooleli."
)

#: Refused when a typed save names a current step whose linked operation is a
#: different one: an overview never finishes `Saada Koja arvamus`.
PLAN_STEP_WRONG_OPERATION = "See salvestus ei tee praeguse tööplaani sammu tööd."

#: `Järgmisena` named the step this very save is finishing.
NEXT_IS_THE_CURRENT_STEP = "Seda sammu märgid praegu tehtuks. Vali järgmiseks mõni teine samm."

#: An overview step is finished by a publication, never by a plan.
OVERVIEW_STEP_NEEDS_A_PUBLICATION = (
    "Kodulehe ülevaate samm on tehtud siis, kui ülevaade on avaldatud. Lisa ülevaate link."
)

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
    return current


def _named_plan_action(
    *, locked_matter: Matter, action_id: Any, plan_step_id: Any, operation: str
) -> NextAction:
    """The open step a typed save was **launched from** — or a refusal, before any write.

    `+ Ülevaade / uudis`, `+ Kaasamine` and `+ Koja arvamus` drawn under the
    current `Tööplaan` step post the action and the plan step they were drawn
    against. That launch is the whole relationship: nothing compares the record
    with the step's words (docs/adr/0133 §6). So every part of it is checked
    here, under the Matter's lock and before the caller writes anything:

    * the Matter is open and locked (the caller's `lock_open_matter_for_business_write`);
    * the named action is still the open one (`_named_open_action`, the stale-tab rule);
    * it belongs to the named plan step — not a different one, not none;
    * that step is on this Matter and its operation is the one this save performs.

    Anything else refuses the whole save. A colleague who finished the step in
    another tab, or swapped it for different work, has made the launch stale;
    writing the record and leaving the step alone would be a guess in the other
    direction, so neither happens and the person decides again.
    """
    current = _named_open_action(locked_matter=locked_matter, action_id=action_id)
    if current.plan_step_id is None or str(current.plan_step_id) != str(plan_step_id):
        raise DomainError(PLAN_STEP_NOT_CURRENT)
    step = MatterPlanStep.objects.filter(pk=current.plan_step_id, matter=locked_matter).first()
    if step is None:
        raise DomainError(PLAN_STEP_NOT_CURRENT)
    if step.operation != operation:
        raise DomainError(PLAN_STEP_WRONG_OPERATION)
    return current


@transaction.atomic
def complete_current_action(
    *,
    matter: Matter,
    author: Any,
    action_id: Any,
    body: str,
    uploads: Sequence[Any] = (),
    next_step_id: Any = None,
    next_text: str = "",
    next_date: Any = None,
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

    **`Järgmisena` — and what comes next, in the same save** (docs/adr/0133 §4).
    Optional, and three answers:

    * ``next_step_id`` — a `Tööplaan` step still ahead. Started through
      `app.workflow.plan` exactly as `Alusta` starts it: the canonical
      `NextAction`, linked to the step, with the step's own words and the day
      in ``next_date`` if one was given and none if not;
    * ``next_text`` — `Muu tegevus`, an ordinary step in the person's words,
      through `set_next_action_for_new_work`, tied to no plan step;
    * neither — `Praegu ei määra`, and no step is opened. Nothing is ever
      chosen for the person: the plan's next suggestion is not started because
      this one finished (docs/adr/0133 §4).

    **One transaction, checked before it writes.** The next step is asked for
    under the Matter's lock *before* the note is written — on this Matter, still
    ahead, not the step being finished — so a stale choice refuses the whole save
    and nothing lands half-done. Then the note, its files, the completion (which
    completes the current plan step through `complete_next_action`) and the new
    step, in one operation, so `Teema käik` reads it as one row: what was done,
    with the next step under it.
    """
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
    following: MatterPlanStep | None = None
    if next_step_id is not None:
        following = work_plan.startable_step(locked_matter, next_step_id)
        if following.pk == current.plan_step_id:
            raise DomainError(NEXT_IS_THE_CURRENT_STEP)
    next_text = (next_text or "").strip()

    with composer_operation() as operation_id:
        result = WorkspaceResult(operation_id=operation_id)
        result.entry = add_entry(
            matter=locked_matter,
            body=body,
            author=author,
            kind=EntryKind.NOTE,
        )
        result.documents = capture_supporting_evidence(
            matter=locked_matter,
            record=result.entry,
            uploads=_uploads(uploads),
            actor=author,
        )
        complete_next_action(action=current, actor=author)
        if following is not None:
            result.action = work_plan.start_checked_step(
                locked_matter=locked_matter,
                step=following,
                actor=author,
                target_date=next_date,
            )
        elif next_text:
            result.action = set_next_action_for_new_work(
                matter=locked_matter, text=next_text, target_date=next_date, actor=author
            )
        else:
            result.action = None
        return result


@transaction.atomic
def change_current_action(*, matter: Matter, actor: Any, action_id: Any, **step: Any) -> NextAction:
    """`Muuda` beside the open step — the same work, said differently.

    The canonical `set_next_action_for_new_work`, which supersedes the open row
    with a new one, and **carries its `Tööplaan` step onto the replacement**:
    changing the words or the day of «Küsin Johnilt seisukohta» does not take it
    out of the plan (docs/adr/0133 §4).

    Named, like `Mida tegid?`: the editor posts the step it was drawn beside, and
    a step that is no longer the open one refuses with `STALE_ACTION_REFUSAL`
    and writes nothing — otherwise a stale tab would carry *another* step's plan
    relation onto words written about the old one.
    """
    locked_matter = lock_open_matter_for_business_write(matter.pk)
    _named_open_action(locked_matter=locked_matter, action_id=action_id)
    return set_next_action_for_new_work(
        matter=locked_matter, actor=actor, carry_plan_step=True, **step
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
    plan_action_id: Any = None,
    plan_step_id: Any = None,
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

    **Started from the current `Tööplaan` step** (``plan_action_id`` and
    ``plan_step_id``, docs/adr/0133 §6): asking the members *is* the step
    «Kaasa liikmeid», so the same save completes it — the action COMPLETED and
    its plan step with it, in this one operation, so `Teema käik` reads one
    row: the round, with `✓ Tehtud` under it. The round itself stays **open**
    (docs/adr/0132): sending the question is done, collecting the answers is
    not, and no «Ootan tagasisidet» step is invented for the wait. Checked under
    the lock before anything is written (`_named_plan_action`).
    """
    locked_matter = lock_open_matter_for_business_write(matter.pk)
    named = (
        _named_plan_action(
            locked_matter=locked_matter,
            action_id=plan_action_id,
            plan_step_id=plan_step_id,
            operation=PlanStepOperation.ENGAGEMENT,
        )
        if plan_action_id is not None
        else None
    )
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
        if named is not None:
            result.action = complete_next_action(action=named, actor=author)
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
) -> WorkspaceResult:
    """`+ Oluline tähtaeg` — a milestone somebody announced, and its letter.

    Creates no `NextAction`. A date the file has to live with is not an
    instruction to anybody, and the two were separated deliberately long before
    this round (brief §17).
    """
    from app.intelligence.services import add_important_date

    locked_matter = lock_open_matter_for_business_write(matter.pk)
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
    """
    from app.intelligence.services import add_effective_date

    locked_matter = lock_open_matter_for_business_write(matter.pk)
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
    """
    from app.intelligence.services import add_confirmed_work_victory

    locked_matter = lock_open_matter_for_business_write(matter.pk)
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
) -> WorkspaceResult:
    """`+ Meile saadetud tagasiside` / `+ Teiste arvamus` — what somebody else said.

    **One operation behind two chips**, which is docs/adr/0091 §3's whole
    architectural claim. `Meile saadetud tagasiside` and `Teiste arvamus` are two
    professional facts with one shape: an author, a source a colleague can open,
    an optional date at the precision it is known to, and an optional note from
    the lawyer. Four models — `EngagementFeedback`, `ExternalPosition`,
    `AnotherOpinion`, `SurveyFeedback` — would have been four sets of validation
    and four chronology renderings for one set of rules, so the distinction is a
    column and the panels are two doors onto this function.

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
    """
    from app.matters.services import record_external_position, record_external_position_document

    files = _uploads(uploads)
    locked_matter = lock_open_matter_for_business_write(matter.pk)
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
def add_matter_koda_opinion(
    *,
    matter: Matter,
    author: Any,
    upload: Any,
    recipients: Sequence[Any],
    sent_on: Any,
    title: str = "",
    summary: str = "",
    complete_action_id: Any = None,
    working_uploads: Sequence[Any] = (),
    stage: Any = None,
    plan_step_id: Any = None,
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

    **``plan_step_id`` — started from `Saada Koja arvamus`** (docs/adr/0133 §6).
    The panel drawn under the current `Tööplaan` step posts the step and its
    action as ``complete_action_id``, with no checkbox: the launch says the
    send is the step. Checked as a typed launch (`_named_plan_action`) rather
    than as a tick, so an action of another step, or an action no longer
    current, refuses before a byte is stored. The completion is the same
    `complete_next_action` either way, and so is everything about the
    `Submission`.
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
    from app.documents.uploads import UploadRejected, read_upload
    from app.submissions.enums import SentAtPrecision
    from app.submissions.services import register_sent_opinion_on_open_matter

    if sent_on is None:
        # Stated here as well as in the service, because this is the boundary the
        # panel posts to and «the application picked a day» is the one failure
        # docs/adr/0061's amendment exists to prevent.
        raise DomainError("Saatmise registreerimiseks on vaja saatmise kuupäeva.")

    from app.matters.services import stage_transition

    locked_matter = lock_open_matter_for_business_write(matter.pk)
    if plan_step_id is not None:
        named: NextAction | None = _named_plan_action(
            locked_matter=locked_matter,
            action_id=complete_action_id,
            plan_step_id=plan_step_id,
            operation=PlanStepOperation.SUBMISSION,
        )
    elif complete_action_id is not None:
        named = _named_open_action(locked_matter=locked_matter, action_id=complete_action_id)
    else:
        named = None
    with (
        composer_operation() as operation_id,
        stage_transition(
            matter=locked_matter,
            stage=stage if stage is not None else locked_matter.stage,
            actor=author,
        ) as move,
    ):
        result = WorkspaceResult(operation_id=operation_id, closed=move.closes)
        # Read first, so a rejected file refuses before anything is written. The
        # ordinary evidence pipeline — same reader, same scan gate, same checksum,
        # same immutability — and the role is the one the product already has for
        # Koda's own opinion.
        accepted = read_upload(upload)
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
            title=(title or "").strip() or accepted.filename,
            role=_Role.KODA_SUBMISSION_FINAL,
            created_by=author,
        )
        version = add_evidence_version(
            document=document,
            content=accepted.content,
            original_filename=accepted.filename,
            mime_type=accepted.mime_type,
            uploaded_by=author,
        )
        result.documents = [document]
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
            actor=author,
            summary=summary,
            recipients=list(recipients),
            sent_at=moment,
            sent_at_precision=SentAtPrecision.DATE,
        )
        result.documents += capture_accepted_evidence(
            matter=locked_matter,
            record=result.record,
            accepted=working,
            actor=author,
            role=OPINION_WORKING_DOCUMENT_ROLE,
            visibility_override=result.record.visibility_override,
        )
        if named is not None:
            result.action = complete_next_action(action=named, actor=author)
        return result


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
    listed to somebody who may not see the letter they belong to.

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
            visibility_override=current.visibility_override,
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
            matter=locked_matter, stage=stage if moves_stage else locked_matter.stage, actor=author
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
    plan_action_id: Any = None,
    plan_step_id: Any = None,
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

    **Started from `Koosta kodulehe ülevaade`** (``plan_action_id`` and
    ``plan_step_id``, docs/adr/0133 §6): the step is done when the write-up is
    *published*, so only a save carrying an address completes it, and then in
    this same operation — one «Ülevaade / uudis» row in `Teema käik` with
    `✓ Tehtud` under it, and no `Mida tegid?` note repeating it. A plan alone
    finishes nothing: an addressless save from the current step is refused
    rather than filed as a plan that leaves the step looking done. An overview
    recorded from `LISA TEEMALE`, or published from a planned row's own
    `Avalda`, is not this launch and completes no step.
    """
    locked_matter = lock_open_matter_for_business_write(matter.pk)
    named = None
    if plan_action_id is not None:
        named = _named_plan_action(
            locked_matter=locked_matter,
            action_id=plan_action_id,
            plan_step_id=plan_step_id,
            operation=PlanStepOperation.WEBSITE_OVERVIEW,
        )
        if not url:
            raise DomainError(OVERVIEW_STEP_NEEDS_A_PUBLICATION)
    with composer_operation() as operation_id:
        result = WorkspaceResult(operation_id=operation_id)
        overview = plan_website_overview(matter=locked_matter, actor=author)
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
        if named is not None:
            result.action = complete_next_action(action=named, actor=author)
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


# **No `close_matter_from_workspace` since docs/adr/0131 §11.** `+ Lõpeta teema`
# was its only caller. A Matter ends when its `Hetkeseis` says so: `+ Märge` and
# `+ Koja arvamus` move the stage through `stage_transition`, which closes the
# Matter through `close_matter_for_terminal_stage` after the act it was saved
# with — the order this use case kept (the win first, the closure last).
