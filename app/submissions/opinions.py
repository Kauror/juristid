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

from dataclasses import dataclass
from typing import Any

from django.db.models import Q

from app.documents.enums import DocumentRole
from app.documents.models import Document
from app.submissions.enums import RecipientRole, SubmissionStatus
from app.submissions.models import ADDRESSEE_ROWS, Submission, addressee_prefetch

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


@dataclass(frozen=True)
class RailOpinion:
    """One line of the `Koja arvamus` rail card: the letter, and what tells it apart.

    ``label`` is the send it is the letter of, as a reader distinguishes two
    opinions on one file — the day it went and who it went to, plus «tagasi
    võetud» or «asendatud» on one that no longer stands. Empty for an opinion
    file no visible send accounts for, which reads as it always did.

    ``working`` is that send's working documents (docs/adr/0129 §9), read
    through `DocumentLink.visible_to`, so a reader is never shown a working file
    of a letter or a document they may not see.
    """

    document: Document
    label: str = ""
    working: tuple[Document, ...] = ()


def opinion_rail(matter: Any, *, viewer: Any) -> list[RailOpinion]:
    """The rail's opinions, each told apart by its send — UQ-28, docs/adr/0129 §9.

    The **same files** `opinion_documents` lists, in the same order: this adds
    to each line and removes none. Two opinions on one file used to be two bare
    filenames — `koda_opinion.asice` and `koda_opinion.asice` — and the rail
    could not say which letter was which without opening both.

    The label comes from the send whose `final_version` is that file — any
    send that **went out**, as `historically_sent` defines it, so a withdrawn
    letter says so instead of reading like one that stands. Several sends of one
    file (a resend of the same text) read as the latest. Both sides scoped:
    the Submission through its own `visible_to`, so a restricted send names no
    day and no addressee; the working documents through the link's.

    Two queries for the sends and their addressees, one for the working
    documents, and **none at all** on a Matter with no opinion — the commonest
    page by far.
    """
    from app.core.dates import format_estonian_date
    from app.documents.links import DocumentLink
    from app.matters.timeline import submission_chronology_day

    documents = opinion_documents(matter, viewer=viewer)
    if not documents:
        return []

    by_document: dict[Any, Submission] = {}
    sends = (
        Submission.objects.filter(
            matter=matter, final_version__document_id__in=[document.pk for document in documents]
        )
        .visible_to(viewer)
        .historically_sent()
        .select_related("final_version")
        .prefetch_related(addressee_prefetch())
        .order_by("sent_at", "created_at", "pk")
    )
    for submission in sends:
        by_document[submission.final_version.document_id] = submission

    working: dict[Any, list[Document]] = {}
    if by_document:
        for link in (
            DocumentLink.objects.filter(
                submission_id__in=[submission.pk for submission in by_document.values()]
            )
            .visible_to(viewer)
            .select_related("document__current_version")
            .order_by("created_at", "pk")
        ):
            working.setdefault(link.submission_id, []).append(link.document)

    lines = []
    for document in documents:
        submission = by_document.get(document.pk)
        if submission is None:
            lines.append(RailOpinion(document=document))
            continue
        parts = [format_estonian_date(submission_chronology_day(submission))]
        addressees = getattr(submission, ADDRESSEE_ROWS, [])
        if addressees:
            parts.append(", ".join(row.organisation.name for row in addressees))
        if submission.status != SubmissionStatus.SENT:
            parts.append(str(submission.get_status_display()).lower())
        lines.append(
            RailOpinion(
                document=document,
                label=" · ".join(parts),
                working=tuple(working.get(submission.pk, ())),
            )
        )
    return lines


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
        "recipient_rows__organisation", "joint_submitter_rows__organisation", "tags"
    )
    # The linked write-ups, scoped on the *overview* side and read once for the
    # whole page rather than per row. A `prefetch_related("website_overviews")`
    # would be shorter and wrong: it would follow the relation without asking
    # `visible_to`, and a restricted overview would arrive on a row a reader may
    # see (docs/adr/0093 §4). `tags` above carries no visibility of its own — a
    # governed vocabulary row is reference data — so the plain prefetch is right
    # there and only there.
    from app.matters.models import MatterWebsiteOverview
    from app.submissions.models import SubmissionWebsiteOverviewLink

    overviews_by_submission: dict[Any, list[Any]] = {}
    visible_overviews = MatterWebsiteOverview.objects.filter(matter=matter).visible_to(viewer)
    for link in (
        SubmissionWebsiteOverviewLink.objects.filter(
            submission__matter=matter, website_overview__in=visible_overviews
        )
        .select_related("website_overview")
        # Read off the link table rather than off the overviews with a join back,
        # so an overview covering two letters arrives once per letter instead of
        # twice per letter. Oldest first, the ordering the strip and the picker
        # both use, so one file's three answers cannot appear in three orders.
        .order_by("website_overview__created_at", "website_overview__id")
    ):
        overviews_by_submission.setdefault(link.submission_id, []).append(link.website_overview)

    by_document: dict[Any, Submission] = {}
    for submission in rows.order_by("sent_at", "created_at"):
        # Named apart from the model's own `tags` / `website_overviews` so a
        # template reading them cannot silently fall through to an unscoped
        # relation when this decoration is not the one that ran.
        submission.metadata_tags = list(submission.tags.all())
        submission.metadata_overviews = overviews_by_submission.get(submission.pk, [])
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


def unregistered_opinion_documents(matter: Any, *, viewer: Any) -> list[Document]:
    """The candidates for «Registreeri saatmine» — one list, one definition.

    An opinion file on this Matter that has a stored binary and that **no**
    Submission has ever been bound to: not a send, which would make the
    registration a duplicate; not a draft, whose evidence has its own operation
    (`Märgi saadetuks`) and must not acquire a second, parallel SENT record of
    the same bytes; and — since the Dokumendid creation surface was retired —
    not a withdrawn or superseded send either.

    **What is left is the stranded upload and nothing else.** A file filed
    through `Lae dokument` as `Arvamus` before that choice was taken off the
    menu, which only this registration can turn into a canonical send. A
    withdrawn opinion's file is not stranded: it was sent, the withdrawal is a
    fact about the act, and offering to register those bytes again would put the
    retired workflow back in front of a record that is complete. A later send of
    the same text is `Lisa teemale → Koja arvamus`, like any other send
    (docs/adr/0061, amendment of 2026-09-27).

    Bound by *any* Submission rather than by one this reader may see, because
    the answer only ever removes a candidate: a send somebody cannot see still
    accounts for the file, and offering it would invite a duplicate the service
    then refuses.

    Computed here rather than twice, because it was twice: the Dokumendid page
    built it to render the select and the route rebuilt it to resolve what came
    back, and a candidate rule that lives in two list comprehensions is a rule
    that gets fixed in one of them (R2-01).

    This is the *read* model. It decides what the page offers, never what the
    application accepts — `register_sent_opinion` re-establishes the same rule
    against the database, because a browser submits whatever it likes.
    """
    ever_bound = set(
        Submission.objects.filter(matter=matter, final_version__isnull=False).values_list(
            "final_version__document_id", flat=True
        )
    )
    return [
        document
        for document in opinion_documents(matter, viewer=viewer)
        if document.current_version_id and document.pk not in ever_bound
    ]


def open_drafts(matter: Any, *, viewer: Any) -> list[Submission]:
    """Opinions still being prepared — the only submissions with work left.

    Listed apart from the file table and only while they exist, because a draft
    is an action somebody owes rather than a file the Matter holds. Once a draft
    is sent its evidence becomes an ordinary `Arvamus` row and this block stops
    mentioning it: one opinion must not appear twice on one page, which is the
    duplication the retired surface existed to create (docs/adr/0061 §5).

    Nothing in the interface starts a draft any more — `+ Uus arvamus` was
    retired with the Dokumendid block, and a new opinion is recorded sent, in one
    act, from `Lisa teemale → Koja arvamus`. So this list is the drafts that were
    started before that, and it is what keeps them finishable rather than
    stranded (docs/adr/0061, amendment of 2026-09-27).
    """
    return list(
        Submission.objects.filter(matter=matter, status=SubmissionStatus.DRAFT)
        .visible_to(viewer)
        .select_related("final_version")
        # What the draft's `Märgi saadetuks` opens on (ENG-041).
        .prefetch_related(addressee_prefetch())
        .order_by("-created_at")
    )
