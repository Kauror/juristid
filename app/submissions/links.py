"""What one opinion is classified by, and where it was written up — read side.

Four selectors, and they exist so that the edit surface and the two reading
surfaces cannot disagree about the same two questions. The form builds its
choices from the same queryset the service resolves a POST against, and the
reciprocal list on an `Ülevaade / uudis` reads the same relation the opinion's
own row does (docs/adr/0093).

**Everything here is scoped on the side that carries visibility.** A
`Submission` and a `MatterWebsiteOverview` are both `VisibilityInheritingModel`
children and either may be restricted below its Matter. The relation between
them grants nothing: a restricted overview is not offered, not counted and not
named to somebody who may not see it, and a restricted opinion contributes no
row to the list under an overview they can (AUTH-003, docs/adr/0093 §4).

**Nothing here infers anything.** No candidate is proposed because a URL looks
like the opinion's title, because a publication date sits near a send date, or
because two records share a tag. The lists are «every one on this file this
reader may see» and nothing narrower or cleverer; the choosing is a person's.
"""

from __future__ import annotations

from typing import Any

from django.db.models import Q, QuerySet

from app.submissions.models import Submission


def selectable_tags(submission: Submission) -> QuerySet:
    """Every `Märksõna` this opinion may be given today, plus the ones it has.

    The bound-value union `MatterEditForm` uses to keep a departed colleague a
    valid owner, for the same reason. The working vocabulary is
    `Tag.is_active`; a tag this Submission is **already** classified by stays in
    the list however the vocabulary has moved since, so re-saving the form cannot
    silently drop a classification because somebody deprecated or merged a word
    months later (docs/adr/0093 §1).

    A queryset rather than a list, so a `ModelMultipleChoiceField` can take it as
    its `queryset=` and validate against exactly what it rendered.

    Ordered by name, like every other Silt control in the product.
    """
    from app.taxonomy.models import Tag

    return (
        Tag.objects.filter(Q(is_active=True) | Q(submission_assignments__submission=submission))
        # The `OR` reaches across a join, so a tag assigned to this Submission
        # *and* still active would otherwise arrive twice — once from each branch
        # — and be offered as two identical chips. `distinct()` on a queryset whose
        # ordering is a column of its own table is safe; what is not safe is
        # `DISTINCT` over an ordering the projection does not select, which is
        # HAF-01 and is deliberately avoided by ordering on `name_et`.
        .distinct()
        .order_by("name_et")
    )


def selectable_website_overviews(submission: Submission, *, viewer: Any) -> QuerySet:
    """Every `Ülevaade / uudis` on this opinion's own file that this reader may see.

    **Scoped to the Matter on the query, not checked afterwards.** The same-Matter
    invariant is a rule about which rows exist to be chosen, so the candidate list
    is built from `submission.matter` and a foreign overview is structurally
    unreachable rather than merely refused — which is the shape
    `opinion_documents_queryset` already gives the equivalent rule for evidence.

    **Every state.** `Plaanis` is a real record of a write-up this file owes, and
    naming the opinion it will cover is exactly the moment somebody knows which
    one that is; `Tühistatud` is a real record of a plan that was dropped. No
    status restriction is invented here (docs/adr/0093 §2).

    Ordered oldest-first so the list does not reshuffle under a reader when a
    publication date is corrected — the ordering `planned_website_overviews`
    chooses, and for the same reason.
    """
    from app.matters.models import MatterWebsiteOverview

    return (
        MatterWebsiteOverview.objects.filter(matter_id=submission.matter_id)
        .visible_to(viewer)
        .order_by("created_at", "id")
    )


def linked_website_overviews(submission: Submission, *, viewer: Any) -> list[Any]:
    """The write-ups linked to this opinion, as this reader may see them.

    Scoped through the overview's own `visible_to`, so a restricted one linked by
    a colleague contributes no row, no count and no address here. A reader who can
    see the opinion has not thereby been told what else is on the file.
    """
    return list(
        selectable_website_overviews(submission, viewer=viewer).filter(submissions=submission)
    )


def linked_submissions_by_overview(matter: Any, *, user: Any) -> dict[Any, list[LinkedOpinion]]:
    """For each `Ülevaade / uudis` on this Matter, the opinions this reader may see.

    One query for a whole page rather than one per row, because both callers —
    the planned strip and the chronology — draw a list of overviews and would
    otherwise ask per row.

    **The scoping is on `Submission`,** which is the record being named. A
    restricted opinion linked to a NORMAL overview is absent from the dictionary
    entirely, so the surface renders no row for it and no «1 arvamus» count that
    would say it exists (docs/adr/0093 §4).

    Keyed by overview identity; an overview with no visible linked opinion is
    simply not a key, which is what lets a template ask `.get` and render nothing.
    """
    grouped: dict[Any, list[LinkedOpinion]] = {}
    rows = (
        Submission.objects.filter(matter=matter, website_overview_links__isnull=False)
        .visible_to(user)
        .values_list("website_overview_links__website_overview_id", "pk", "title", "kind")
        # Newest send first, with a deterministic tie break so two letters sent on
        # one day do not swap places between reads. A draft has no `sent_at` and
        # sorts last, which is the order `Meta.ordering` already gives.
        .order_by("-sent_at", "-created_at", "-id")
    )
    for overview_id, pk, title, kind in rows:
        grouped.setdefault(overview_id, []).append(LinkedOpinion(pk=pk, title=title, kind=kind))
    return grouped


class LinkedOpinion:
    """One linked opinion as a reciprocal list prints it: a title and nothing else.

    Deliberately not the `Submission` instance. What an `Ülevaade / uudis` row
    needs is «which letters this write-up covers», and handing a template the
    whole record invites a second question — its evidence, its recipients, its
    status — to be asked from a loop that has not scoped for it. A value object
    with three read-only fields cannot grow one by accident.
    """

    __slots__ = ("kind", "pk", "title")

    def __init__(self, *, pk: Any, title: str, kind: str) -> None:
        self.pk = pk
        self.title = title
        self.kind = kind

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"LinkedOpinion({self.title!r})"
