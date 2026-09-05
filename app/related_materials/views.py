"""«Seotud materjalid» over HTTP: one read fragment, one picker, six decisions.

The section on the Matter page is rendered by `app.matters.views` from the
selectors, with suggestions closed. Everything that costs more than two queries
happens here, when somebody asks:

* ``section`` — GET. The whole section, with suggestions computed when
  ``?avatud=1`` is present. An HTMX request gets the fragment and swaps it over
  the section it came from; a plain request gets a small page of its own, so
  the feature works with scripting off. **Read only.** Nothing is written by
  opening, expanding, paging or showing the hidden candidates.
* ``picker`` — GET, writers only. Five Matters for «Lisa seotud teema», through
  the same authorized ranking the header search uses.
* The six POST routes — ``login_required``, ``business_write_required``, POST
  only, CSRF. Each resolves its target through the population the caller may
  see, hands the resolved objects to `services`, and answers with the section
  re-rendered (HTMX) or a redirect back to the section (plain form). A target
  the caller may not see is a 404, exactly like a Matter they may not open:
  no route here confirms that a restricted record exists (docs/adr/0037).
"""

from __future__ import annotations

import uuid
from typing import Any

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_GET, require_http_methods

from app.core.authorization import may_write_business_content
from app.core.decorators import business_write_required
from app.core.errors import DomainError
from app.legacy_import.opinion_access import may_read_archive
from app.legacy_import.opinion_binary import OpinionArchiveBinary
from app.matters.models import Matter
from app.related_materials import engine, selectors, services
from app.search.services import clean_query, search_matters
from app.submissions.models import Submission

SECTION_TEMPLATE = "related_materials/partials/section.html"
PAGE_TEMPLATE = "related_materials/section_page.html"
PICKER_TEMPLATE = "related_materials/partials/picker_results.html"

#: How many Matters «Lisa seotud teema» offers for one query. The header
#: dropdown shows five; one more is fetched so «is there more» costs no count.
PICKER_LIMIT = 5
MIN_PICKER_CHARACTERS = 2

#: The POST vocabulary for a candidate's kind.
KIND_MATTER = "teema"
KIND_SUBMISSION = "arvamus"
KIND_ARCHIVE = "arhiiv"

SECTION_ANCHOR = "#seotud-materjalid"


# ---------------------------------------------------------------------------
# Resolution
# ---------------------------------------------------------------------------


def _visible_matter(request: HttpRequest, pk: Any) -> Matter:
    """The Matter the section is about, or 404 — never 403."""
    queryset = (
        Matter.objects.visible_to(request.user)
        .select_related("addressee_organisation", "stage", "superseded_by")
        .prefetch_related("tags", "policy_areas", "source_organisations")
    )
    return get_object_or_404(queryset, pk=pk)


def _uuid(value: Any) -> uuid.UUID:
    try:
        return uuid.UUID(str(value or ""))
    except ValueError as error:
        raise Http404("Sellist kirjet ei ole.") from error


def _target_matter(viewer: Any, value: Any) -> Matter:
    return get_object_or_404(Matter.objects.visible_to(viewer), pk=_uuid(value))


def _target_submission(viewer: Any, value: Any) -> Submission:
    return get_object_or_404(
        Submission.objects.visible_to(viewer).select_related("matter"), pk=_uuid(value)
    )


def _target_binary(viewer: Any, value: Any) -> OpinionArchiveBinary:
    if not may_read_archive(viewer):
        raise Http404("Sellist kirjet ei ole.")
    return get_object_or_404(OpinionArchiveBinary, pk=_uuid(value))


def _is_htmx(request: HttpRequest) -> bool:
    return request.headers.get("HX-Request") == "true"


def _flag(source: Any, name: str) -> bool:
    return source.get(name) == "1"


def _limit(source: Any) -> int:
    try:
        return int(source.get("piir") or engine.DEFAULT_LIMIT)
    except (TypeError, ValueError):
        return engine.DEFAULT_LIMIT


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


def _picker_results(request: HttpRequest, matter: Matter, query: str) -> list[Matter]:
    """Matters for «Lisa seotud teema»: the header search's own ranking, minus
    this Matter and the ones already related to it."""
    if len(query) < MIN_PICKER_CHARACTERS:
        return []
    already = {item.other.pk for item in selectors.confirmed_relations(matter, request.user)} | {
        matter.pk
    }
    found = search_matters(query=query, user=request.user, limit=PICKER_LIMIT + len(already) + 1)
    return [result.matter for result in found if result.matter.pk not in already][:PICKER_LIMIT]


def section_context(
    request: HttpRequest,
    matter: Matter,
    *,
    open_suggestions: bool,
    include_hidden: bool,
    limit: int,
    query: str = "",
    notice: str = "",
    notice_is_error: bool = False,
) -> dict[str, Any]:
    can_write = may_write_business_content(request.user)
    suggestions = None
    if open_suggestions:
        suggestions = engine.suggestions_for(
            matter, request.user, limit=limit, include_hidden=include_hidden
        )
    return {
        "matter": matter,
        "can_write": can_write,
        "related_materials": selectors.related_materials_for(matter, request.user),
        "suggestions": suggestions,
        "suggestions_open": open_suggestions,
        "hidden_shown": include_hidden,
        "suggestion_limit": limit,
        "picker_query": query if can_write else "",
        "picker_results": _picker_results(request, matter, query) if can_write and query else [],
        "notice": notice,
        "notice_is_error": notice_is_error,
        "empty_message": engine.EMPTY_MESSAGE,
        "max_limit": engine.MAX_LIMIT,
    }


def _after_write(
    request: HttpRequest, matter: Matter, notice: str, *, is_error: bool = False
) -> HttpResponse:
    """The section again, with suggestions open and a line saying what happened.

    An HTMX caller gets the fragment — 400 on a refused decision so the page
    swaps the explanation in rather than discarding it (static/js/app.js). A
    plain form gets a message and a redirect back to the section.
    """
    if _is_htmx(request):
        context = section_context(
            request,
            matter,
            open_suggestions=True,
            include_hidden=_flag(request.POST, "peidetud"),
            limit=_limit(request.POST),
            notice=notice,
            notice_is_error=is_error,
        )
        return render(request, SECTION_TEMPLATE, context, status=400 if is_error else 200)
    (messages.error if is_error else messages.success)(request, notice)
    return redirect(reverse("matters:matter_detail", kwargs={"pk": matter.pk}) + SECTION_ANCHOR)


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------


@login_required
@require_GET
def section(request: HttpRequest, pk: Any) -> HttpResponse:
    """The section, and the suggestions when asked for. Writes nothing."""
    matter = _visible_matter(request, pk)
    context = section_context(
        request,
        matter,
        open_suggestions=_flag(request.GET, "avatud"),
        include_hidden=_flag(request.GET, "peidetud"),
        limit=_limit(request.GET),
        query=clean_query(request.GET.get("q") or ""),
    )
    template = SECTION_TEMPLATE if _is_htmx(request) else PAGE_TEMPLATE
    return render(request, template, context)


@login_required
@business_write_required
@require_GET
def picker(request: HttpRequest, pk: Any) -> HttpResponse:
    """Matters for «Lisa seotud teema». A write affordance, so writers only."""
    matter = _visible_matter(request, pk)
    query = clean_query(request.GET.get("q") or "")
    return render(
        request,
        PICKER_TEMPLATE,
        {
            "matter": matter,
            "can_write": True,
            "picker_query": query,
            "picker_results": _picker_results(request, matter, query),
            "hidden_shown": False,
            "suggestion_limit": engine.DEFAULT_LIMIT,
        },
    )


# ---------------------------------------------------------------------------
# Decisions
# ---------------------------------------------------------------------------


@login_required
@business_write_required
@require_http_methods(["POST"])
def link(request: HttpRequest, pk: Any) -> HttpResponse:
    """«Seo teemaga», from a suggestion or from the picker: the same service."""
    matter = _visible_matter(request, pk)
    other = _target_matter(request.user, request.POST.get("teema"))
    try:
        _, created = services.link_related_matters(matter=matter, other=other, actor=request.user)
    except DomainError as error:
        return _after_write(request, matter, str(error), is_error=True)
    return _after_write(
        request, matter, "Teemad on seotud." if created else "Teemad olid juba seotud."
    )


@login_required
@business_write_required
@require_http_methods(["POST"])
def unlink(request: HttpRequest, pk: Any) -> HttpResponse:
    """«Eemalda seos». Withdraws the relation; dismisses nothing."""
    matter = _visible_matter(request, pk)
    other = _target_matter(request.user, request.POST.get("teema"))
    try:
        removed = services.unlink_related_matters(matter=matter, other=other, actor=request.user)
    except DomainError as error:
        return _after_write(request, matter, str(error), is_error=True)
    return _after_write(request, matter, "Seos on eemaldatud." if removed else "Seost ei olnud.")


def _background_target(request: HttpRequest) -> dict[str, Any]:
    kind = request.POST.get("liik")
    value = request.POST.get("kandidaat")
    if kind == KIND_SUBMISSION:
        return {"submission": _target_submission(request.user, value)}
    if kind == KIND_ARCHIVE:
        return {"archive_binary": _target_binary(request.user, value)}
    raise Http404("Sellist materjali liiki ei ole.")


@login_required
@business_write_required
@require_http_methods(["POST"])
def add_background(request: HttpRequest, pk: Any) -> HttpResponse:
    """«Lisa taustmaterjaliks». Reads the source; writes only the selection."""
    matter = _visible_matter(request, pk)
    target = _background_target(request)
    try:
        if "submission" in target:
            _, created = services.add_background_submission(
                matter=matter, submission=target["submission"], actor=request.user
            )
        else:
            _, created = services.add_background_archive_material(
                matter=matter, binary=target["archive_binary"], actor=request.user
            )
    except DomainError as error:
        return _after_write(request, matter, str(error), is_error=True)
    notice = "Taustmaterjal on lisatud." if created else "See materjal on juba taustmaterjal."
    return _after_write(request, matter, notice)


@login_required
@business_write_required
@require_http_methods(["POST"])
def remove_background(request: HttpRequest, pk: Any) -> HttpResponse:
    """«Eemalda» on a background row. Leaves any archive link exactly where it was."""
    matter = _visible_matter(request, pk)
    target = _background_target(request)
    try:
        removed = services.remove_background_material(matter=matter, actor=request.user, **target)
    except DomainError as error:
        return _after_write(request, matter, str(error), is_error=True)
    notice = "Taustmaterjal on eemaldatud." if removed else "Seda taustmaterjali ei olnud."
    return _after_write(request, matter, notice)


def _candidate_target(request: HttpRequest, matter: Matter) -> dict[str, Any]:
    """The candidate a dismissal or restore names, through the caller's own view."""
    kind = request.POST.get("liik")
    value = request.POST.get("kandidaat")
    if kind == KIND_MATTER:
        return {"candidate_matter": _target_matter(request.user, value)}
    if kind == KIND_SUBMISSION:
        return {"candidate_submission": _target_submission(request.user, value)}
    if kind == KIND_ARCHIVE:
        return {"candidate_archive_binary": _target_binary(request.user, value)}
    raise Http404("Sellist soovituse liiki ei ole.")


@login_required
@business_write_required
@require_http_methods(["POST"])
def dismiss(request: HttpRequest, pk: Any) -> HttpResponse:
    """«Ei ole seotud». Durable for the Matter, reversible from «Näita peidetud»."""
    matter = _visible_matter(request, pk)
    candidate = _candidate_target(request, matter)
    try:
        services.dismiss_related_suggestion(matter=matter, actor=request.user, **candidate)
    except DomainError as error:
        return _after_write(request, matter, str(error), is_error=True)
    return _after_write(request, matter, "Soovitus on peidetud.")


@login_required
@business_write_required
@require_http_methods(["POST"])
def restore(request: HttpRequest, pk: Any) -> HttpResponse:
    """«Taasta soovitus»: the candidate is eligible again."""
    matter = _visible_matter(request, pk)
    candidate = _candidate_target(request, matter)
    try:
        restored = services.restore_related_suggestion(
            matter=matter, actor=request.user, **candidate
        )
    except DomainError as error:
        return _after_write(request, matter, str(error), is_error=True)
    notice = "Soovitus on taastatud." if restored else "See soovitus ei olnud peidetud."
    return _after_write(request, matter, notice)
