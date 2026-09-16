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

**Every operation that adds content starts by locking the Matter and refusing a
closed one** (`lock_open_matter_for_business_write`). Not because the page shows
these forms on a closed Matter — it does not — but because a page is not a
boundary. A browser that had the Teema open before somebody else closed it still
has every field and every button, and its POST reaches a server with no memory
of which page it came from. Hiding the forms on a fresh GET is the right thing
to do and it decides nothing (docs/adr/0075 §12, R2-02).

Two operations here do not take that guard, and neither of them may.
`close_matter_from_workspace` is the act that *produces* the closed state, so
refusing it on a closed Matter is `close_matter`'s own job — it takes the same
row itself and answers «Teema on juba suletud.». And
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

from app.audit.operations import composer_operation
from app.core.errors import DomainError
from app.documents.enums import DocumentRole
from app.documents.models import Document
from app.documents.services import capture_supporting_evidence
from app.matters.entry_enums import EntryKind
from app.matters.locks import lock_open_matter_for_business_write
from app.matters.models import Entry, Matter
from app.matters.services import (
    add_engagement,
    add_entry,
    cancel_website_overview,
    close_matter,
    correct_website_overview_link,
    plan_website_overview,
    publish_website_overview,
)
from app.workflow.enums import ActionStatus, DatePrecision
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
    locked_matter = lock_open_matter_for_business_write(matter.pk)
    with composer_operation() as operation_id:
        result = WorkspaceResult(operation_id=operation_id)
        result.entry = add_entry(
            matter=locked_matter, body=body, author=author, kind=EntryKind.NOTE
        )
        result.documents = capture_supporting_evidence(
            matter=locked_matter,
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
    smaily_url: str = "",
    alchemer_url: str = "",
    occurred_on: Any = None,
    occurred_on_precision: str = DatePrecision.EXACT.value,
    feedback_deadline: Any = None,
    uploads: Sequence[Any] = (),
) -> WorkspaceResult:
    """`+ Kaasamine` — one consultation, with the replies it produced attached.

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
    stored as blank. ``feedback_deadline`` is `Tagasisidet ootame kuni` and is
    optional, undefaulted and inert — it is a record of what was asked of other
    people, not a task for this office.

    ``occurred_on_precision`` is the panel's `Täpsus` answer, and ``occurred_on``
    is then the anchor of the period it names — the same normalisation
    `+ Oluline tähtaeg` and `+ Jõustumine` go through. `Tagasisidet ootame kuni`
    takes no precision: docs/adr/0082 widened the first date and left the second
    on docs/adr/0079 §11's exact-day list.
    """
    locked_matter = lock_open_matter_for_business_write(matter.pk)
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
            feedback_deadline=feedback_deadline,
            actor=author,
        )
        result.documents = capture_supporting_evidence(
            matter=locked_matter,
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
    url: str = "",
    stated_on: Any = None,
    stated_on_precision: str = DatePrecision.EXACT.value,
    summary: str = "",
    engagement: Any = None,
    uploads: Sequence[Any] = (),
) -> WorkspaceResult:
    """`+ Väline seisukoht` — what another organisation said, and where to read it.

    One operation: the record, its files and the links between them land
    together or not at all. That is the whole reason this module exists, and it
    is load-bearing here in a way it is not for a note — a position whose file
    was refused would be a record claiming a source it does not have
    (docs/adr/0075 §8).

    **The source rule is decided before anything is written.** The
    `DocumentLink` cannot exist until the position does, so the service is told
    how many files are about to be captured rather than being handed them; if
    the capture then refuses one of them, `UploadRejected` unwinds this
    transaction and takes the position with it. Neither half can survive without
    the other (docs/adr/0084 §3).

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
            url=url,
            stated_on=stated_on,
            stated_on_precision=stated_on_precision,
            summary=summary,
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
def add_matter_website_overview(
    *,
    matter: Matter,
    author: Any,
    url: str = "",
    published_on: Any = None,
) -> WorkspaceResult:
    """`+ Ülevaade / uudis` — a plan, or a page that is already up.

    docs/adr/0081 §1 gave this one button and no fields, because at the moment
    somebody decides a Matter should be written up there is no address and no
    publication date. That holds for the case it describes. What it did not
    cover is the lawyer recording a write-up *after* the page is published, who
    had to file a plan and then publish it from a second control to say a thing
    that was already true (docs/adr/0083).

    So both shapes arrive here. With neither argument this is the plan, byte for
    byte what it always was. With both, the record is planned and published
    inside **one** transaction and one `composer_operation`, which is why the
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
    """
    locked_matter = lock_open_matter_for_business_write(matter.pk)
    with composer_operation() as operation_id:
        result = WorkspaceResult(operation_id=operation_id)
        overview = plan_website_overview(matter=locked_matter, actor=author)
        if url and published_on is not None:
            overview = publish_website_overview(
                overview=overview,
                url=url,
                published_on=published_on,
                actor=author,
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
) -> WorkspaceResult:
    """`Paranda link` — what the file says about an existing page was wrong.

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
        result.record = correct_website_overview_link(
            overview=overview,
            url=url,
            published_on=published_on,
            actor=author,
            expected_revision=expected_revision,
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

    **No closed-Matter guard, deliberately.** Every other operation in this
    module takes `lock_open_matter_for_business_write` first; this one is the
    act that produces the closed state, so refusing it on a closed Matter is
    `close_matter`'s own job — it locks the same row and answers «Teema on juba
    suletud.» Adding the guard here would say the same thing twice and in the
    wrong sentence (R2-02).
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
