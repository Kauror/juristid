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

`compose_update` is untouched and still exported; it is simply no longer what
the Teema page posts to (docs/adr/0075 §11).
"""

from __future__ import annotations

import uuid as uuid_module
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from django.db import transaction

from app.audit.operations import composer_operation
from app.core.errors import DomainError
from app.documents.models import Document
from app.documents.services import capture_supporting_evidence
from app.matters.entry_enums import EntryKind
from app.matters.models import Entry, Matter
from app.matters.services import add_engagement, add_entry, close_matter
from app.workflow.enums import ActionStatus
from app.workflow.models import NextAction
from app.workflow.services import complete_next_action

#: Refused when the step the form was rendered against is no longer the one that
#: is open. Named because two surfaces print it and a test asserts on it.
STALE_ACTION_REFUSAL = (
    "Praegune tegevus on vahepeal muutunud. Värskenda lehte ja vaata, mis on nüüd pooleli."
)


@dataclass
class WorkspaceResult:
    """What one workspace operation wrote.

    A dataclass rather than a tuple for the reason :class:`ComposerResult` is
    one: these grow a field when an operation learns to write something else,
    and a caller unpacking positionally would silently take the wrong one.
    """

    operation_id: uuid_module.UUID
    entry: Entry | None = None
    action: NextAction | None = None
    record: Any = None
    documents: list[Document] = field(default_factory=list)
    closed: bool = False


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


@transaction.atomic
def complete_current_action(
    *,
    matter: Matter,
    author: Any,
    action_id: Any,
    body: str,
    uploads: Sequence[Any] = (),
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
    """
    # `no_key=True` on both, and that is not a detail. This transaction locks
    # the Matter and then *inserts rows that point at it* — an `Entry`, a
    # `Document`, a `DocumentVersion`, several `ChangeEvent`s — which is
    # precisely the shape that turns a plain `FOR UPDATE` into a deadlock: an
    # FK-referencing insert takes `FOR KEY SHARE` on the parent, and `FOR
    # UPDATE` conflicts with it while `FOR NO KEY UPDATE` does not. The weaker
    # mode still conflicts with itself and with the plain `FOR UPDATE` that
    # `set_next_action` and `close_matter` take, so this is serialised against
    # both of them exactly as it must be (app/matters/locks.py, PR #80).
    locked_matter = Matter.objects.select_for_update(no_key=True).get(pk=matter.pk)
    if not locked_matter.is_open:
        raise DomainError("Suletud teemal ei saa tegevust lõpetada.")

    current = (
        NextAction.objects.select_for_update(no_key=True)
        .filter(matter=locked_matter, status=ActionStatus.OPEN)
        .first()
    )
    if current is None or str(current.pk) != str(action_id):
        raise DomainError(STALE_ACTION_REFUSAL)

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
        result.action = complete_next_action(action=current, actor=author)
        return result


@transaction.atomic
def add_matter_note(
    *,
    matter: Matter,
    author: Any,
    body: str,
    uploads: Sequence[Any] = (),
) -> WorkspaceResult:
    """`+ Märge` — something happened, and it is not the current task finishing.

    The route for "the ministry rang to say the new version comes on Friday"
    while the step that is open stays open. It writes an `Entry` and its
    evidence and touches `Järgmiseks` in no way at all: not completing it, not
    superseding it, not creating one. That separation is the whole reason this
    operation exists beside the one above (docs/adr/0075 §7).
    """
    with composer_operation() as operation_id:
        result = WorkspaceResult(operation_id=operation_id)
        result.entry = add_entry(matter=matter, body=body, author=author, kind=EntryKind.NOTE)
        result.documents = capture_supporting_evidence(
            matter=matter,
            record=result.entry,
            uploads=_uploads(uploads),
            actor=author,
        )
        return result


@transaction.atomic
def add_matter_engagement(
    *,
    matter: Matter,
    author: Any,
    kind: str,
    audience: str,
    response_count: Any = None,
    occurred_on: Any = None,
    uploads: Sequence[Any] = (),
) -> WorkspaceResult:
    """`+ Kaasamine` — one consultation, with the replies it produced attached.

    The business model is exactly the one `add_engagement` already keeps, and
    this round deliberately does not touch it: `response_count` stays nullable,
    blank still means *nobody counted* rather than *nobody answered*, and no
    response rate is computed anywhere (brief §16).
    """
    with composer_operation() as operation_id:
        result = WorkspaceResult(operation_id=operation_id)
        result.record = add_engagement(
            matter=matter,
            kind=kind,
            title=audience,
            occurred_on=occurred_on,
            response_count=response_count,
            actor=author,
        )
        result.documents = capture_supporting_evidence(
            matter=matter,
            record=result.record,
            uploads=_uploads(uploads),
            actor=author,
        )
        return result


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

    with composer_operation() as operation_id:
        result = WorkspaceResult(operation_id=operation_id)
        result.record = add_important_date(
            matter=matter,
            actor=author,
            title=title,
            date_value=date_value,
            period_end=period_end,
            date_precision=date_precision,
        )
        result.documents = capture_supporting_evidence(
            matter=matter,
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
    date_precision: str,
    uploads: Sequence[Any] = (),
) -> WorkspaceResult:
    """`+ Jõustumine` — what commences, the day it does, and the act itself."""
    from app.intelligence.services import add_effective_date

    with composer_operation() as operation_id:
        result = WorkspaceResult(operation_id=operation_id)
        result.record = add_effective_date(
            matter=matter,
            actor=author,
            description=description,
            date_value=date_value,
            period_end=date_value,
            date_precision=date_precision,
        )
        result.documents = capture_supporting_evidence(
            matter=matter,
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
    uploads: Sequence[Any] = (),
) -> WorkspaceResult:
    """`+ Töövõit` — what changed, and the evidence that it did.

    A win closes nothing and completes nothing. It is its own canonical fact,
    recorded on the day it happened rather than on the day the file finishes,
    and it goes through the confirmed-victory service because a person stating
    it has already made the judgement a candidate exists to defer
    (docs/adr/0074 §8, brief §19).
    """
    from app.intelligence.services import add_confirmed_work_victory

    with composer_operation() as operation_id:
        result = WorkspaceResult(operation_id=operation_id)
        result.record = add_confirmed_work_victory(
            matter=matter,
            actor=author,
            title=title,
            detail="",
        )
        result.documents = capture_supporting_evidence(
            matter=matter,
            record=result.record,
            uploads=_uploads(uploads),
            actor=author,
        )
        return result


@transaction.atomic
def close_matter_from_workspace(
    *,
    matter: Matter,
    author: Any,
    disposition: str,
    closing_words: str = "",
) -> WorkspaceResult:
    """`+ Lõpeta teema` — two questions, and nothing invented from them.

    The simplified closure of docs/adr/0074 §10, unchanged: how it ended, an
    optional last word, and no claim that an opinion was sent, that a win was
    won or that anything commenced. `close_matter` still cancels the open step
    through `end_open_action_for_closure`, which is the one place that decides
    what a closure does to `Järgmiseks` (brief §20).

    No file control. Closure gained no attachment requirement in this round, and
    giving it one would quietly reintroduce the final-evidence precondition the
    previous round removed.
    """
    with composer_operation() as operation_id:
        result = WorkspaceResult(operation_id=operation_id)
        close_matter(
            matter=matter,
            disposition=disposition,
            actor=author,
            reason=closing_words.strip(),
        )
        result.closed = True
        return result
