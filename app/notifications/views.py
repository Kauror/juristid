"""`/teavitused/` — the compact list of «Kontrolli jõustumist» and its two answers.

«Kontrollitud», the third, was retired by the owner on 2026-10-30.

Thin on purpose: the population is `app.notifications.selectors`, every answer
is a named service in `app.notifications.services`, and this module only reads
the request, asks, and redirects.

The reading routes are `@login_required` like every business read (ADR 0181):
behind the shared gate a session with no persona is sent to choose one, and an
administrator without a business role reads an empty page — the selectors start
from `visible_to`, which gives that caller nothing.

The answering routes are business writes and use the one write gate
(`@business_write_required`): a READER or a technical administrator gets the
same 404 every other business write gives them (tests/test_business_write_boundary.py).
"""

from __future__ import annotations

from typing import Any

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import Http404, HttpRequest, HttpResponse, HttpResponseRedirect
from django.shortcuts import render
from django.urls import reverse
from django.views.decorators.http import require_GET, require_http_methods

from app.core.authorization import may_view_department_management, may_write_business_content
from app.core.dates import format_estonian_date
from app.core.decorators import business_write_required
from app.core.errors import DomainError
from app.notifications import selectors, services
from app.notifications.forms import DeferForm, DismissForm

#: Where an answer returns to. A fixed map rather than a `next` URL, so a
#: crafted form cannot turn this into a redirect anywhere else.
RETURN_TO = {
    "teavitused": "notifications:list",
    "minu_asjad": "matters:my_work",
}


def _back(request: HttpRequest) -> HttpResponseRedirect:
    name = RETURN_TO.get(str(request.POST.get("tagasi", "")), "notifications:list")
    return HttpResponseRedirect(reverse(name))


def _reminder_or_404(request: HttpRequest, pk: Any) -> Any:
    reminder = selectors.answerable_reminder(request.user, pk)
    if reminder is None:
        raise Http404("Meeldetuletust ei leitud.")
    return reminder


@login_required
@require_GET
def reminder_list(request: HttpRequest) -> HttpResponse:
    """Everything the reader is asked to check now, then what they put off.

    The unowned section is built only for `department.view_management`: the
    selector returns nothing for anybody else, so the section is absent from
    their response rather than hidden in it.
    """
    user = request.user
    context = {
        "rows": selectors.open_reminders_for(user),
        "deferred_rows": selectors.deferred_reminders_for(user),
        "unowned_rows": selectors.department_unowned_reminders(user),
        "shows_unowned": may_view_department_management(user),
        "can_answer": may_write_business_content(user),
        "tagasi": "teavitused",
    }
    return render(request, "notifications/reminder_list.html", context)


@login_required
@require_GET
def bell_count(request: HttpRequest) -> HttpResponse:
    """The bell's number, fetched after the page has rendered.

    Its own request so that adding the bell to the top bar adds no query to any
    page's own render: the count is one `COUNT` asked once the page has loaded
    (``hx-trigger="load"``), not one more statement in the query budget of every
    page the bar is drawn on.
    """
    return render(
        request,
        "notifications/_bell_count.html",
        {"count": selectors.unresolved_count(request.user)},
    )


@login_required
@business_write_required
@require_http_methods(["POST"])
def defer(request: HttpRequest, pk: Any) -> HttpResponse:
    reminder = _reminder_or_404(request, pk)
    form = DeferForm(request.POST)
    if not form.is_valid():
        for errors in form.errors.values():
            for error in errors:
                messages.error(request, str(error))
        return _back(request)
    try:
        deferred = services.defer_reminder(
            reminder=reminder, actor=request.user, until=form.cleaned_data["until"]
        )
    except DomainError as refusal:
        messages.error(request, str(refusal))
    else:
        # The stored date, which a seven-day prompt caps at the commencement day.
        messages.success(
            request,
            f"Meeldetuletus lükati edasi kuni {format_estonian_date(deferred.deferred_until)}.",
        )
    return _back(request)


@login_required
@business_write_required
@require_http_methods(["POST"])
def dismiss(request: HttpRequest, pk: Any) -> HttpResponse:
    reminder = _reminder_or_404(request, pk)
    form = DismissForm(request.POST)
    if not form.is_valid():
        messages.error(request, services.DISMISS_NEEDS_REASON)
        return _back(request)
    try:
        services.dismiss_reminder(
            reminder=reminder, actor=request.user, reason=form.cleaned_data["reason"]
        )
    except DomainError as refusal:
        messages.error(request, str(refusal))
    else:
        messages.success(request, "Meeldetuletus suleti.")
    return _back(request)
