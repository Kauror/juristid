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
from datetime import datetime as _datetime
from datetime import time as _time
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
from app.matters.locks import lock_open_matter_for_business_write
from app.matters.models import Entry, Matter
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
from app.workflow.enums import ActionStatus, DatePrecision
from app.workflow.models import NextAction
from app.workflow.services import complete_next_action, set_next_action_for_new_work

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
    audience: str,
    kind: str = EngagementKind.OTHER.value,
    response_count: Any = None,
    smaily_url: str = "",
    alchemer_url: str = "",
    occurred_on: Any = None,
    occurred_on_precision: str = DatePrecision.EXACT.value,
    feedback_deadline: Any = None,
    feedback_received: str = "",
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
            feedback_received=feedback_received,
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
def add_engagement_feedback(
    *,
    engagement: Any,
    author: Any,
    feedback_received: str = "",
    uploads: Sequence[Any] = (),
    expected_revision: str | None = None,
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
    round — the order `add_matter_engagement` and `add_matter_note` already use.

    Returns the completed engagement rather than a `WorkspaceResult`, because
    the caller swaps that one row back into the chronology and has no use for an
    operation id it cannot render.
    """
    completed = complete_engagement_feedback(
        engagement=engagement,
        feedback_received=feedback_received,
        actor=author,
        expected_revision=expected_revision,
    )
    with composer_operation():
        capture_supporting_evidence(
            matter=completed.matter,
            record=completed,
            uploads=_uploads(uploads),
            actor=author,
        )
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
    engagement: Any = None,
    uploads: Sequence[Any] = (),
) -> WorkspaceResult:
    """`+ Meile saadetud tagasiside` / `+ Teiste arvamus` — what somebody else said.

    **One operation behind two chips**, which is docs/adr/0090 §3's whole
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
    (docs/adr/0090 §3, §4).

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
) -> WorkspaceResult:
    """`+ Koja arvamus` — the Chamber's opinion went out, with the file that went.

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
    something would drift from the first (docs/adr/0061 §17, docs/adr/0090 §6).

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
    (docs/adr/0061, docs/adr/0090 §6.2).

    ``sent_on`` is a **day the person supplied**, and this function invents none:
    the service refuses `None` and refuses any precision but `DATE`, which is the
    rule R2-01 put there after a blank box became `timezone.now()` and the outbound
    register reported `Arvamus välja <today>` about letters nobody had dated.

    ``recipients`` is who it actually went to, and is **never defaulted from the
    Matter's sender**. An opinion on the first draft goes to the ministry; one at
    second reading goes to a Riigikogu committee. Assuming the sender would put a
    false recipient on the canonical outbound record of a professional letter
    (docs/adr/0090 §6.3).

    Several per Matter is ordinary. Nothing here is unique on the Matter, nothing
    supersedes an earlier opinion, and no earlier `Submission`, `Document` or
    `DocumentVersion` is touched — a revised opinion is a new letter and new bytes,
    which is what the immutable evidence store is for (docs/adr/0090 §6.4, §7).
    """
    from datetime import datetime, time

    from app.documents.enums import DocumentRole as _Role
    from app.documents.services import add_evidence_version, create_document
    from app.documents.uploads import read_upload
    from app.submissions.enums import SentAtPrecision
    from app.submissions.services import register_sent_opinion_on_open_matter

    if sent_on is None:
        # Stated here as well as in the service, because this is the boundary the
        # panel posts to and «the application picked a day» is the one failure
        # docs/adr/0061's amendment exists to prevent.
        raise DomainError("Saatmise registreerimiseks on vaja saatmise kuupäeva.")

    locked_matter = lock_open_matter_for_business_write(matter.pk)
    with composer_operation() as operation_id:
        result = WorkspaceResult(operation_id=operation_id)
        # Read first, so a rejected file refuses before anything is written. The
        # ordinary evidence pipeline — same reader, same scan gate, same checksum,
        # same immutability — and the role is the one the product already has for
        # Koda's own opinion.
        accepted = read_upload(upload)
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
            recipients=list(recipients),
            sent_at=moment,
            sent_at_precision=SentAtPrecision.DATE,
        )
        return result


@transaction.atomic
def add_procedural_development(
    *,
    matter: Matter,
    author: Any,
    body: str,
    occurred_on: Any,
    stage: Any = None,
    next_text: str = "",
    next_date: Any = None,
    uploads: Sequence[Any] = (),
) -> WorkspaceResult:
    """`+ Menetluse areng` — the procedure moved, and what the lawyer does about it.

    The operation the file had no way to record, and the reason a Matter used to
    end at «Arvamus saadetud» with nothing to press. A ministry sends a revised
    draft; the Chamber reads it; the draft goes to the Ministry of Justice; the
    government approves it; the file reaches the Riigikogu. Each of those is one
    dated step, and recording one used to mean up to three saves in three places —
    a `Märge` with no date box, a `Hetkeseis` change in the header, and
    `+ Järgmine tegevus` under the launcher (lawyer feedback 14, docs/adr/0090 §5).

    **One `Entry`, and no new model.** A procedural development is a dated,
    attributable sentence about something that happened, which is what the
    authored chronology already is. `EntryKind.PROCEDURAL_DEVELOPMENT` is what
    lets a reader tell it from a `Märkus`; `occurred_at` is the day the person
    named, not the day they typed; the files ride on the existing `DocumentLink`.

    **Atomicity is the substance of it.** Three canonical writes happen here and
    any one of them failing must leave the Matter exactly as it was — a stage
    moved with no development recorded would be a file claiming to be in the
    Riigikogu with nothing on it saying how it got there, and a `NextAction`
    written twice by a retried request would be work nobody assigned. Ordered as
    the composer orders its own: the record first, then its evidence, then the
    stage, then the step.

    **Nothing is derived from the sentence.** No stage is inferred from the words,
    no next step is generated, and a save that names neither changes neither. What
    a person did not answer is not a thing this function decides for them
    (docs/adr/0090 §5.3).

    ``stage`` goes through `change_stage`, which is the canonical service and
    writes its own `MATTER_STAGE_CHANGED` event; a stage equal to the one the file
    already has is that service's own no-op rather than a second audit row.

    ``next_text`` and ``next_date`` go through `set_next_action_for_new_work`,
    which is the native boundary: somebody is assigning work today, so the
    departed-owner rule applies exactly as it does on `+ Järgmine tegevus`. A step
    written here supersedes whatever was open, which is `NextAction`'s one-open
    invariant and not a decision this function makes.
    """
    from app.matters.services import add_entry, change_stage

    locked_matter = lock_open_matter_for_business_write(matter.pk)
    with composer_operation() as operation_id:
        result = WorkspaceResult(operation_id=operation_id)
        result.entry = add_entry(
            matter=locked_matter,
            body=body,
            author=author,
            kind=EntryKind.PROCEDURAL_DEVELOPMENT,
            # Midnight in Europe/Tallinn on the day the person named. The
            # chronology reads entries by day, and a development recorded a week
            # later belongs on the day it happened — which is what `occurred_at`
            # has meant since the foundational schema.
            occurred_at=timezone.make_aware(_datetime.combine(occurred_on, _time.min)),
        )
        result.documents = capture_supporting_evidence(
            matter=locked_matter,
            record=result.entry,
            uploads=_uploads(uploads),
            actor=author,
        )
        if stage is not None:
            change_stage(matter=locked_matter, stage=stage, actor=author)
        text = (next_text or "").strip()
        if text:
            result.action = set_next_action_for_new_work(
                matter=locked_matter,
                text=text,
                target_date=next_date,
                actor=author,
            )
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
