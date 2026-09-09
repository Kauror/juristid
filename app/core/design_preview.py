"""The Teema design-refinement preview surface.

**Preview-only. Not the Matter page, not a route this product ships.**

`design-handoff/matter-page-refinement` proposes a rearrangement of the Teema
page: four blocks in the main column instead of seven, three distinguishable
grounds, forward-looking dates raised under the action zone, `Seotud materjalid`
moved into the rail. It is a presentation study. Before anything in production
is touched, the product owner has to be able to open it in a real browser, at a
real width, on a Matter that is actually populated, and say whether that is the
page they want.

That is all this module is for.

**Why it reads through `app.matters.views`.** A preview that built its own
context would be a second answer to every question the Matter page already
answers — which deadline is active, which step is current, what the reader may
write — and the first time the two disagreed the preview would be showing a page
that cannot exist. So the two private context builders are imported and used
unchanged: the preview renders *the Matter page's own data* in a different
arrangement, which is exactly what a presentation study is.

**Why the route fails closed.** It is registered only under `DEBUG` and refuses
again in the view, because a design study rendering business content is a
surface nobody reviewed for authorization, visibility or audit. Two gates rather
than one: the URLconf keeps it out of production's routing table entirely, and
the view refuses even if some future settings change lets it through.

Nothing here writes. The view is `GET`-only, the extra context is derived from
records the Matter page has already read, and the preview's own stylesheet and
script are scoped under one root class.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import render
from django.utils import timezone
from django.views.decorators.http import require_GET

TEMPLATE = "design_preview/matter_refinement.html"

#: How many candidate relations the rail's `Lisa` disclosure shows. The design
#: draws five and names five more; production's own default is five, so the two
#: already agree and nothing here decides open question Q8.
SUGGESTION_LIMIT = 5


def _refuse_outside_development() -> None:
    """404 unless this is a development configuration holding synthetic data.

    404 and not 403, the convention every other gated surface in this
    application follows: a 403 would confirm the address exists.

    Both halves are load-bearing. `DEBUG` alone would be enough today —
    `app/core/checks.py` already refuses to boot with `REAL_DATA_ALLOWED` and
    `DEBUG` together — but this route's whole safety argument is "it cannot
    reach real data", and stating that condition here means the guard survives
    a change to that check.
    """
    if not settings.DEBUG or settings.REAL_DATA_ALLOWED:
        raise Http404("Disainieelvaade on ainult arenduskeskkonna vaade.")


@dataclass(frozen=True)
class PreviewFactRow:
    """One row of the refinement's `Olulised tähtajad`.

    A rendered answer, not a model. `distance` is the muted qualifier the design
    puts beside the date — `.factrow__dist`, which the handoff itself calls a
    new element — and it is computed here rather than in the template so the
    template cannot start doing arithmetic on dates.
    """

    date_line: str
    distance: str
    title: str
    is_approximate: bool


def _distance(period_end: date, today: date) -> str:
    """«N p», the same reading the header band already gives a future deadline.

    Only for a date still ahead of us. The design shows future rows only, and
    what a past one says is open question Q2 — so this returns nothing for one
    rather than inventing a form of words for a state the design does not draw.
    """
    days = (period_end - today).days
    return f"{days} p" if days >= 0 else ""


def _deadline_rows(intelligence: Any, today: date) -> list[PreviewFactRow]:
    """The facts panel's first section, in the order the design draws it.

    Upcoming `Olulised tähtajad`, then commencements. The second half is the
    handoff's open question Q1 — whether the panel absorbs `Jõustumine` — and Q3
    — whether `jõustub` is a constant, a derived qualifier or sample text. This
    reproduces the row the prototype shows so the product owner can look at it
    and answer both. It decides neither: nothing in the application changes, the
    production `Jõustumine` section is untouched, and the reconciliation report
    lists the row as unresolved.
    """
    rows = [
        PreviewFactRow(
            date_line=record.display_date,
            distance=_distance(record.period_end, today),
            title=record.title,
            is_approximate=record.is_approximate,
        )
        for record in intelligence.upcoming_dates
    ]
    rows += [
        PreviewFactRow(
            date_line=record.display_when,
            # The design's own word, in the design's own slot. See Q3.
            distance="jõustub",
            title=record.description,
            is_approximate=record.is_approximate,
        )
        for record in intelligence.effective_dates
        if not record.is_cancelled
    ]
    return rows


def _note_saved_at(matter: Any, reader: Any) -> Any:
    """When this reader's private note was last written, or nothing.

    The design's `Märkmed` card shows one settled line and no save button. What
    it says before the first save, while saving and after a failure is open
    question Q6, so the line is rendered only where there is a real saved time
    to render and is simply absent otherwise.
    """
    from app.matters.models import MatterPersonalNote

    if reader is None or not getattr(reader, "is_authenticated", False):
        return None
    record = MatterPersonalNote.objects.filter(matter=matter, author=reader).first()
    return timezone.localtime(record.updated_at) if record is not None else None


@login_required
@require_GET
def matter_refinement(request: HttpRequest, pk: Any) -> HttpResponse:
    """The Matter, in the refinement's arrangement. Reads only."""
    from app.matters.views import _header_context, _overview_context, get_visible_matter
    from app.related_materials import engine

    _refuse_outside_development()

    matter = get_visible_matter(request, pk)

    # The Matter page's own two context builders, unchanged and in the same
    # order the real view calls them in. Everything the refinement renders
    # comes from here; the keys added below are presentation, not new reads of
    # the database beyond the two the design's rail needs.
    context = _overview_context(request, matter)
    intelligence = context["intelligence"]
    context.update(
        _header_context(
            request,
            matter,
            milestones=[*intelligence.upcoming_dates, *intelligence.past_dates],
        )
    )
    context["tab"] = "teema"
    context["nav_active"] = "teemad"

    today = context["today"]
    context["preview_deadline_rows"] = _deadline_rows(intelligence, today)
    context["preview_note_saved_at"] = _note_saved_at(matter, request.user)
    # Computed on the ordinary render rather than only when the disclosure is
    # opened, because the design puts the search and the suggestions inside one
    # control and a preview that had to be clicked twice to show its own
    # populated state would not be showing it. Production's own laziness is
    # untouched — see `app/related_materials/views.py`.
    context["preview_suggestions"] = engine.suggestions_for(
        matter, request.user, limit=SUGGESTION_LIMIT
    )
    return render(request, TEMPLATE, context)
