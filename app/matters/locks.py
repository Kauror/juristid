"""The one row every write to the final-evidence invariant serialises on.

A Submission's final evidence must never be less restricted than the Submission
itself, and must stay a version of a Document in the Submission's own Matter.
Both sides of that comparison are *derived* from the Matter's visibility
(docs/adr/0005), so three different rows can falsify it — and until DATA-002
nothing made two writers touching different ones take turns.

`submissions/migrations/0002` and `0005` refuse every single-row route to the
bad state, and `app.submissions.services.check_evidence_is_usable` refuses it
with a sentence rather than a database error. Neither closes the concurrent
case. A `BEFORE UPDATE` trigger evaluates against the snapshot its own
statement can see, so two transactions — one binding final evidence, one
relaxing the Matter — can each look at a database in which the other has not
committed, each pass its own check, and both commit. That is write skew, and
the only cure is for both to serialise on the same row.

That row is the **Matter**, because it is the one record both operations
already have in hand: the evidence being bound belongs to it, and the
visibility being relaxed is its own column.

Lock order, everywhere these rows are locked together
-----------------------------------------------------

    Matter  →  Submission  →  Document

Take a prefix of that order or the whole of it, never a suffix before a prefix.
`app.documents.services.add_evidence_version` locks a Document on its own,
which is a suffix taken alone and therefore safe; what would not be safe is a
path that locked a Document and then a Matter.

Lock *strength* is the other half of the discipline, and it applies at **every**
level of that order, not only the first. Each row here is taken
`FOR NO KEY UPDATE` — exactly what a plain `UPDATE` of the row takes by itself —
rather than `FOR UPDATE`. The two conflict with each other, so the writers this
exists to serialise still take turns; but neither conflicts with the
`FOR KEY SHARE` that every insert of a row *referencing* the locked one
acquires.

That is load-bearing twice over.

On the **Matter**: an audit event, a Submission and a Document all carry a
`matter_id`, so `FOR UPDATE` here would put a Matter lock in the path of writers
that hold a Document lock and are only inserting a child row — reintroducing the
Document → Matter edge this module exists to keep out of the graph.

On the **Submission**: a targeted search refresh runs from `post_save` inside
the writing transaction and takes the rebuild gate's shared side
(`app.search.indexing`), while a rebuild holds that gate exclusively and inserts
`SearchDocument` rows carrying `submission_id`. Under `FOR UPDATE` those two
deadlock — the rebuild waiting for the row, the binder waiting for the gate —
and PostgreSQL aborts one of them. `FOR NO KEY UPDATE` still serialises two
binders against each other and against a send, and lets the projection row
through.
"""

from __future__ import annotations

from typing import Any

from app.matters.models import Matter
from app.submissions.models import Submission

#: The global order, as table names, for anything that needs to state it.
EVIDENCE_INTEGRITY_LOCK_ORDER = (
    "matters_matter",
    "submissions_submission",
    "documents_document",
)


def lock_matter_for_evidence_integrity(matter_id: Any) -> Matter:
    """Serialise on a Matter, and return the row as it is under the lock.

    Callers must use the returned instance, not the one they arrived with. A
    transaction that waited here was, by definition, waiting for someone else's
    write to that row, so anything it read beforehand may now be false — and a
    lock that only delays a stale check closes nothing.

    Must be called inside `transaction.atomic`; Django refuses `FOR NO KEY
    UPDATE` outside one, which is the behaviour we want rather than a lock that
    is released before the check it protects has run.
    """
    return Matter.objects.select_for_update(no_key=True).get(pk=matter_id)


def lock_submission_for_evidence_integrity(submission_pk: Any) -> Submission:
    """Second step of the order, and the same strength as the first.

    Serialises the binders against each other and against a send, without
    standing in the way of rows that merely reference the Submission — its
    recipients, and the search projection a rebuild is re-inserting. See the
    module docstring for why the weaker mode is the correct one here.
    """
    return Submission.objects.select_for_update(no_key=True).get(pk=submission_pk)
