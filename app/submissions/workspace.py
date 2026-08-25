"""The Arvamused workspace: what Koda has written, from the two sources it has.

Until now an opinion could only be reached through the Matter that produced it.
That is the right place to *create* one and the wrong place to *find* one: the
question a lawyer actually asks is "what did we write about this", and answering
it meant knowing which of two and a half thousand register entries to open
first. Statistika grew a drill-through list to serve its own numbers
(app/reporting/views.py, `submissions_list`), which made the list exist but left
it inside a reporting tab, filtered by a reporting context, reachable only from
a chart.

This module is the selector layer for a first-class workspace instead. It reads
two genuinely different things and never blends them:

**Canonical Submissions** — what this system recorded Koda sending. A SENT
submission carries a recipient, a date and immutable final evidence, and every
future opinion count derives from it. Production holds zero, because the
canonical record starts when the department starts working here.

**The historical archive** — 767 letters really sent, held as bytes, catalogued
but never canonicalised. They are evidence of past work, not Submission rows,
and this module does not have a function that turns one into the other. That
conversion is P4's, it is gated, and a workspace that quietly performed it to
make a page look fuller would destroy the only distinction that lets anybody
tell a measured opinion from a filed one.

Authorization is borrowed, never re-derived. Submissions come through
``Submission.objects.visible_to``, which is the same child-visibility rule the
Matter page uses; the archive comes through ``search_archive``, which asks
``may_read_archive`` before it counts anything. Neither boundary is loosened to
make the workspace more complete — see ``docs/adr/0028``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from django.db.models import Q, QuerySet

from app.organisations.models import Organisation
from app.submissions.enums import SubmissionKind, SubmissionStatus
from app.submissions.models import Submission

#: Rows per page. The same figure the archive browse uses, so the two tabs of
#: one workspace do not paginate differently.
PAGE_SIZE = 50

#: The longest search term accepted. Not a security boundary — the database
#: would cope — but an unbounded ``icontains`` over a text column is a way to
#: make one request expensive, and no real query is longer than this.
MAX_QUERY_CHARACTERS = 500


class SubmissionQueryRefused(ValueError):
    """A filter value this workspace will not run rather than guess at."""


@dataclass(frozen=True)
class SentFilters:
    """What the reader narrowed the canonical list to.

    ``status`` defaults to SENT rather than to everything. A draft is somebody's
    unfinished work and belongs on their Matter, not in the department's record
    of what went out; showing both under one heading would make "how many
    opinions did we send" unanswerable from the page that appears to answer it.
    """

    query: str = ""
    year: str = ""
    #: One month inside `year`, as a number. Added so the Ulevaade figure
    #: *N esitatud arvamust augustis* has a real list behind it rather than
    #: sending the reader to a whole year and leaving them to count. Read-only
    #: and only meaningful beside a year (Ulevaade brief 21).
    month: str = ""
    status: str = SubmissionStatus.SENT
    kind: str = ""
    recipient_id: str = ""
    owner_id: str = ""

    @property
    def any_applied(self) -> bool:
        """Whether the reader narrowed anything beyond the default status."""
        return bool(
            self.query
            or self.year
            or self.month
            or self.kind
            or self.recipient_id
            or self.owner_id
            or self.status != SubmissionStatus.SENT
        )


def _matches(term: str) -> Q:
    """Free text over the fields somebody would actually paste.

    ``icontains`` rather than the search vector on purpose. This list is small
    by construction — it is what *this* system recorded sending — and a reader
    typing a reference or half a recipient's name wants a substring, not a
    stemmed match that drops the fragment they were sure about.
    """
    return (
        Q(title__icontains=term)
        | Q(reference__icontains=term)
        | Q(channel__icontains=term)
        | Q(matter__title__icontains=term)
        | Q(recipients__name__icontains=term)
    )


def sent_queryset(user: Any, filters: SentFilters) -> QuerySet[Submission]:
    """Canonical submissions this reader may see, narrowed as asked.

    Authorization first and filters after, always. A filter applied to the
    unscoped manager would decide how many rows exist before asking whether this
    person may see any of them, and the count in the heading would be the tell.
    """
    rows = Submission.objects.visible_to(user)

    status = (filters.status or "").strip().upper()
    if status and status != "KOIK":
        if status not in SubmissionStatus.values:
            raise SubmissionQueryRefused(f"Tundmatu olek: {status}.")
        rows = rows.filter(status=status)

    term = (filters.query or "").strip()
    if term:
        if len(term) > MAX_QUERY_CHARACTERS:
            raise SubmissionQueryRefused(
                f"Otsingusõna on pikem kui {MAX_QUERY_CHARACTERS} tähemärki."
            )
        # `distinct` because the recipient join multiplies a submission by its
        # recipients. Without it a letter to three ministries is three rows.
        rows = rows.filter(_matches(term)).distinct()

    if filters.year:
        if not filters.year.isdigit():
            raise SubmissionQueryRefused("Aasta peab olema arv.")
        # On `sent_at`, not on the Matter's reference year: the question this
        # page answers is when the letter went out, and a 2024 file answered in
        # 2026 belongs in 2026.
        rows = rows.filter(sent_at__year=int(filters.year))

    if filters.month:
        if not filters.month.isdigit() or not 1 <= int(filters.month) <= 12:
            raise SubmissionQueryRefused("Kuu peab olema arv vahemikus 1-12.")
        rows = rows.filter(sent_at__month=int(filters.month))

    if filters.kind:
        if filters.kind not in SubmissionKind.values:
            raise SubmissionQueryRefused(f"Tundmatu liik: {filters.kind}.")
        rows = rows.filter(kind=filters.kind)

    if filters.recipient_id:
        rows = rows.filter(recipient_rows__organisation_id=filters.recipient_id).distinct()

    if filters.owner_id:
        # The Matter's responsible lawyer rather than `sent_by`: "whose opinion
        # is this" is a question about the work, and the person who pressed send
        # may have been covering.
        rows = rows.filter(matter__owner_id=filters.owner_id)

    return rows.select_related("matter", "matter__owner", "final_version").prefetch_related(
        "recipient_rows__organisation"
    )


#: The one `?olek=` value that means "being written now".
#:
#: Named here so the Ülevaade figure and the tab it opens cannot drift apart:
#: the figure counts :func:`drafting`, the destination is
#: :data:`DRAFTING_QUERY`, and the view builds the same ``SentFilters`` from it.
DRAFTING_STATUS = SubmissionStatus.DRAFT
DRAFTING_QUERY = f"olek={SubmissionStatus.DRAFT}"


def drafting(user: Any, visible: QuerySet[Submission] | None = None) -> QuerySet[Submission]:
    """Canonical opinions this reader may see that are still being written.

    The *canonical* domain and only that. The 767 historical letters are held
    bytes catalogued as evidence of past correspondence — they are not
    Submission rows, they are finished rather than in preparation, and the
    conversion that would make some of them canonical is P4's and is gated. So
    an archive-heavy production database contributes exactly zero here, which is
    the honest answer to "how many opinions are we writing" (docs/adr/0028).

    ``visible`` is the already-scoped population, for a caller that has one:
    ``visible_to`` resolves the reader's scope on every call, and a page holding
    the answer already should not buy it twice. Omitted, it is resolved here.

    The condition is the one ``sent_queryset`` applies for ``?olek=DRAFT``, so
    the figure on Ülevaade and the list at ``/arvamused/?olek=DRAFT`` hold the
    same rows — and ``tests/test_overview_drilldowns.py`` asserts that against
    the view rather than trusting this comment.
    """
    rows = Submission.objects.visible_to(user) if visible is None else visible
    return rows.filter(status=DRAFTING_STATUS)


def sent_counts(user: Any) -> dict[str, int]:
    """Headline figures for the workspace, before any filter is applied.

    Computed from the same visible queryset the list uses, so a reader is never
    told about submissions they cannot open.
    """
    visible = Submission.objects.visible_to(user)
    return {
        "sent": visible.filter(status=SubmissionStatus.SENT).count(),
        "draft": visible.filter(status=SubmissionStatus.DRAFT).count(),
        "total": visible.count(),
    }


def sent_years(user: Any) -> list[int]:
    """The years the visible canonical record actually covers.

    Derived rather than a range, so the year control offers what exists instead
    of a decade of empty options.
    """
    values = (
        Submission.objects.visible_to(user)
        .filter(sent_at__isnull=False)
        .dates("sent_at", "year", order="DESC")
    )
    return [value.year for value in values]


def recipient_options(user: Any) -> QuerySet[Organisation]:
    """Organisations that appear on a submission this reader may see.

    Not every Organisation: offering the full reference list would advertise
    fifteen ministries as filters that all return nothing, and would leak the
    shape of restricted correspondence to a reader filtering their way through
    it.
    """
    return (
        Organisation.objects.filter(
            submission_recipient_rows__submission__in=Submission.objects.visible_to(user)
        )
        .distinct()
        .order_by("name")
    )
