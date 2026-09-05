"""Which documents on a Matter are the Chamber's opinion — one answer, one place.

`Arvamus` is a **business** classification, and `Document.role` is only one of
the two ways a file acquires it. The other is the record of the send itself: a
`Submission` bound to the exact bytes it went out as. Neither alone is the
answer, and asking only the first is a defect this application has already
shipped once — a Matter whose opinion had been sent rendered
`Koja arvamused 1 · Saadetud` in one column and `Arvamust ei ole lisatud` in the
rail beside it, offering to add a second copy of a letter the Chamber had
already posted (UX-005).

So the union is:

* a `Document` whose role is ``KODA_SUBMISSION_FINAL`` — somebody classified
  this file as the Chamber's opinion, which says nothing about whether it was
  sent; **or**
* a `Document` one of whose versions is the ``final_version`` of a **SENT**
  `Submission` on this Matter — the Chamber sent these exact bytes, whatever
  the file is otherwise classified as.

Deduplicated by document, because a file that qualifies both ways is one file.

**SENT and not merely bound.** A draft's `final_version` is a text somebody is
preparing; badging it `Arvamus` beside the sent ones would be UX-005 pointing
the other way, asserting a send that has not happened. A withdrawn submission's
evidence keeps whatever role it carries and loses the Submission branch, which
is right: the withdrawal is a fact about the act, not about the file.

**`Document.role` is never rewritten to compensate.** A letter that arrived from
a ministry is a `Saabunud ametlik dokument` whether or not somebody later relied
on those bytes, and promoting it would falsify one true fact to answer a
question asked in the wrong place. `Submission` stays the canonical record of
what went out (docs/adr/0061, `app/submissions/services.py`).

Everything here goes through `visible_to` on **both** sides. A `Document`
carries its own visibility override and may be more restricted than the Matter
it sits on, and a filename is frequently the most telling thing about a file —
naming one is a disclosure whether or not the bytes are refused (AUTH-003 §21).
A visible `Submission` is therefore not authority to name its evidence, which is
why the document queryset is scoped as well as the submission queryset.
"""

from __future__ import annotations

from typing import Any

from django.db.models import Q

from app.documents.enums import DocumentRole
from app.documents.models import Document
from app.submissions.enums import RecipientRole, SubmissionStatus
from app.submissions.models import Submission

#: The query-string value the Dokumendid role filter uses for the union above.
#:
#: A word rather than the stored enum, because the thing being filtered for is
#: not a stored value: `KODA_SUBMISSION_FINAL` cannot express "…or the evidence
#: of a sent opinion", and offering it would also print an implementation label
#: on a lawyer's screen. Lower-case Estonian, like every other filter value in
#: this product, so a filtered view stays a link somebody can send.
OPINION_ROLE_FILTER = "arvamus"


def sent_opinion_submissions(matter: Any, *, viewer: Any) -> Any:
    """The SENT submissions on this Matter this reader may see, with evidence.

    One queryset, reused by the document union and by the per-row metadata, so
    the two can never disagree about which sends exist.
    """
    return (
        Submission.objects.filter(
            matter=matter,
            status=SubmissionStatus.SENT,
            final_version__isnull=False,
        )
        .visible_to(viewer)
        .select_related("final_version")
    )


def opinion_documents_queryset(matter: Any, *, viewer: Any) -> Any:
    """Every visible document on this Matter that is the Chamber's opinion.

    Scoped to the Matter on both sides. `attach_final_evidence` already refuses
    evidence belonging to another Matter; saying so here as well makes a
    cross-Matter document structurally unreachable rather than merely unwritten.
    """
    sent_evidence_documents = sent_opinion_submissions(matter, viewer=viewer).values_list(
        "final_version__document_id", flat=True
    )
    return (
        Document.objects.filter(matter=matter)
        .filter(Q(role=DocumentRole.KODA_SUBMISSION_FINAL) | Q(pk__in=sent_evidence_documents))
        .visible_to(viewer)
    )


def opinion_documents(matter: Any, *, viewer: Any) -> list[Document]:
    """The union as a list, newest first — what the facts rail reads."""
    return list(
        opinion_documents_queryset(matter, viewer=viewer)
        .select_related("current_version")
        .order_by("-created_at")
    )


def opinion_document_ids(matter: Any, *, viewer: Any) -> set[Any]:
    """Just the identities, for deciding which rows of a file table are opinions.

    A set rather than a list: the Dokumendid table asks this question once per
    row, and a membership test against a list is the sort of thing that is free
    until a Matter has ninety files.
    """
    return set(opinion_documents_queryset(matter, viewer=viewer).values_list("pk", flat=True))


def sent_submission_by_document(matter: Any, *, viewer: Any) -> dict[Any, Submission]:
    """The send each opinion document is the evidence of, keyed by document.

    The **most recent** send where a document is somehow the evidence of two,
    which the domain permits and nothing prevents: a Matter may resend the same
    text, and the row can only carry one date. Ordering is explicit rather than
    inherited from `Meta.ordering`, because "whichever the default ordering
    happened to put last" is not a rule anybody could reason about.

    Recipients and co-signatories come off two prefetches rather than four
    queries per row. Addressee and `teadmiseks` are split rather than flattened,
    because only the addressees answer the question a reporting count asks —
    who Koda formally wrote to — and the file row shows exactly those, with the
    rest kept for the send's own details behind it (`app/submissions/models.py`).
    """
    rows = sent_opinion_submissions(matter, viewer=viewer).prefetch_related(
        "recipient_rows__organisation", "joint_submitter_rows__organisation"
    )
    by_document: dict[Any, Submission] = {}
    for submission in rows.order_by("sent_at", "created_at"):
        recipient_rows = list(submission.recipient_rows.all())
        submission.addressee_list = [
            row.organisation for row in recipient_rows if row.role == RecipientRole.ADDRESSEE
        ]
        # `Teadmiseks` and the co-signatories are not on the row — they are in
        # the send's own details behind it. They are real facts about the letter
        # and this is where they stayed reachable when the page that printed
        # them was retired (docs/adr/0061 §14).
        submission.information_list = [
            row.organisation for row in recipient_rows if row.role == RecipientRole.FOR_INFORMATION
        ]
        submission.joint_rows = list(submission.joint_submitter_rows.all())
        by_document[submission.final_version.document_id] = submission
    return by_document


def open_drafts(matter: Any, *, viewer: Any) -> list[Submission]:
    """Opinions still being prepared — the only submissions with work left.

    Listed apart from the file table and only while they exist, because a draft
    is an action somebody owes rather than a file the Matter holds. Once a draft
    is sent its evidence becomes an ordinary `Arvamus` row and this block stops
    mentioning it: one opinion must not appear twice on one page, which is the
    duplication the retired surface existed to create (docs/adr/0061 §5).
    """
    return list(
        Submission.objects.filter(matter=matter, status=SubmissionStatus.DRAFT)
        .visible_to(viewer)
        .select_related("final_version")
        .order_by("-created_at")
    )
