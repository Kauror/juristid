"""`/teavitused/` — one inbox, its answers, and the person's notification settings.

Thin on purpose: the population is `app.notifications.inbox`, every
commencement answer is a named service in `app.notifications.services`, the
deadline rows' read/hide state is `app.notifications.deadlines`, and the
preferences are `app.notifications.preferences`. This module reads the
request, asks, and redirects.

Three gates, each the one its write belongs to:

* **Reading** (the list, the bell): `@login_required` like every business read
  (ADR 0181). Behind the shared gate a session with no persona is sent to
  choose one; an administrator without a business role reads an empty inbox —
  every population starts from `visible_to`, which gives that caller nothing.
* **The commencement answers** («Lükka edasi», «Sulge…») are business writes
  and use the one write gate (`@business_write_required`): a READER or a
  technical administrator gets the same 404 every other business write gives
  them (tests/test_business_write_boundary.py). «Kontrollitud», the third, was
  retired by the owner on 2026-10-30.
* **«Märgi loetuks», «Peida» and the settings** are the signed-in person's own
  presentation state, gated like a remembered block (`accounts:save_disclosure`,
  ADR 0194): `@login_required`, rows resolved by ``recipient=request.user``
  (another person's row is a 404), no parameter naming anybody else, no
  business service called and no `ChangeEvent` written (docs/adr/0198 §7).
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
from app.notifications import deadlines, inbox, preferences, selectors, services
from app.notifications.categories import (
    CATEGORIES,
    ESCALATION_HELP,
    ESCALATION_KEY,
    ESCALATION_LABEL,
)
from app.notifications.email import EMAIL_NOT_YET
from app.notifications.enums import NotificationCategory
from app.notifications.forms import DeferForm, DismissForm
from app.notifications.models import ALLOWED_OFFSETS

#: Where an answer returns to. A fixed map rather than a `next` URL, so a
#: crafted form cannot turn this into a redirect anywhere else.
RETURN_TO = {
    "teavitused": "notifications:list",
    "minu_asjad": "matters:my_work",
}

#: The fragment a return lands on, so the reader is back at the block they used.
RETURN_ANCHOR = {"minu_asjad": "#teavitused"}


def _back(request: HttpRequest, *, anchor: bool = False) -> HttpResponseRedirect:
    key = str(request.POST.get("tagasi", ""))
    name = RETURN_TO.get(key, "notifications:list")
    fragment = RETURN_ANCHOR.get(key, "") if anchor else ""
    return HttpResponseRedirect(reverse(name) + fragment)


def _reminder_or_404(request: HttpRequest, pk: Any) -> Any:
    reminder = selectors.answerable_reminder(request.user, pk)
    if reminder is None:
        raise Http404("Meeldetuletust ei leitud.")
    return reminder


@login_required
@require_GET
def reminder_list(request: HttpRequest) -> HttpResponse:
    """«Teavitused»: what needs doing, what is for information, then what was put off.

    Every item comes from `inbox.inbox_for` — the population the bell counts
    and Minu asjad shows. The unowned commencement section is built only for
    `department.view_management`; for anybody else it is absent from the
    response rather than hidden in it.
    """
    user = request.user
    items = inbox.inbox_for(user)
    context = {
        "items": items,
        "action_items": [item for item in items if item.section == inbox.SECTION_ACTION],
        "info_items": [item for item in items if item.section == inbox.SECTION_INFO],
        "unowned_items": [item for item in items if item.section == inbox.SECTION_UNOWNED],
        "has_commencement": any(item.type == inbox.TYPE_COMMENCEMENT for item in items),
        "deferred_rows": selectors.deferred_reminders_for(user),
        "shows_unowned": may_view_department_management(user),
        "can_answer": may_write_business_content(user),
        "tagasi": "teavitused",
    }
    return render(request, "notifications/inbox.html", context)


@login_required
@require_GET
def bell_count(request: HttpRequest) -> HttpResponse:
    """The bell's number, fetched after the page has rendered.

    Its own request so that the bell adds no query to any page's own render:
    three `COUNT`s asked once the page has loaded (``hx-trigger="load"``) — the
    inbox's populations, never loaded as rows.
    """
    return render(
        request,
        "notifications/_bell_count.html",
        {"count": inbox.inbox_count(request.user)},
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


# ---------------------------------------------------------------------------
# A personal deadline notification's own presentation state
# ---------------------------------------------------------------------------


def _own_row_or_404(request: HttpRequest, notification_id: Any) -> Any:
    row = deadlines.own_row(request.user, notification_id)
    if row is None:
        raise Http404("Teavitust ei leitud.")
    return row


@login_required
@require_http_methods(["POST"])
def mark_read(request: HttpRequest, notification_id: Any) -> HttpResponse:
    """«Märgi loetuks» — the recipient's own row only. Changes nothing about the work."""
    row = _own_row_or_404(request, notification_id)
    deadlines.mark_read(notification=row)
    return _back(request, anchor=True)


@login_required
@require_http_methods(["POST"])
def hide(request: HttpRequest, notification_id: Any) -> HttpResponse:
    """«Peida» — a «Teadmiseks» row of the recipient's own.

    A «Vajab tegevust» row is not hidden: it leaves when its work is done, so
    the request is refused like any unknown row, and nothing is written.
    """
    row = _own_row_or_404(request, notification_id)
    if not deadlines.dismiss(notification=row):
        raise Http404("Seda teavitust ei saa peita.")
    messages.success(request, "Teavitus peideti.")
    return _back(request, anchor=True)


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------


def _settings_rows(choices: preferences.Choices) -> list[dict[str, Any]]:
    rows = []
    for category in CATEGORIES.values():
        chosen = choices.of(category.key)
        rows.append(
            {
                "key": category.key,
                "label": category.label,
                "explanation": category.help_text,
                "enabled": chosen.enabled,
                "offsets": [
                    {"value": offset, "checked": offset in chosen.offsets}
                    for offset in ALLOWED_OFFSETS
                ],
                "supports_on_day": category.supports_on_day,
                "on_day": chosen.on_day,
                "is_commencement": category.key == NotificationCategory.COMMENCEMENT_CHECK,
            }
        )
    return rows


@login_required
@require_http_methods(["GET", "POST"])
def notification_settings(request: HttpRequest) -> HttpResponse:
    """`/teavitused/seaded/` — what the signed-in person wants to be told, and when.

    GET draws the form from the stored choices and the registry defaults; POST
    stores only what differs from the defaults and redirects back (PRG). The
    management escalation's switch is offered, and read, only for a holder of
    `department.view_management`.
    """
    user = request.user
    holds = may_view_department_management(user)
    if request.method == "POST":
        chosen: dict[str, preferences.Preference] = {}
        for key in CATEGORIES:
            chosen[key] = preferences.Preference(
                enabled=request.POST.get(f"{key}-enabled") == "1",
                offsets=preferences.normalise_offsets(request.POST.getlist(f"{key}-offsets")),
                on_day=request.POST.get(f"{key}-on_day") == "1",
            )
        if holds:
            chosen[ESCALATION_KEY] = preferences.Preference(
                enabled=request.POST.get(f"{ESCALATION_KEY}-enabled") == "1",
                offsets=(),
                on_day=False,
            )
        preferences.save_choices(user, chosen, paused=request.POST.get("paused") == "1")
        messages.success(request, "Teavituste seaded on salvestatud.")
        return HttpResponseRedirect(reverse("notifications:settings"))
    choices = preferences.choices_for(user)
    context = {
        "rows": _settings_rows(choices),
        "paused": choices.paused,
        "email_requested": choices.email_requested,
        "email_note": EMAIL_NOT_YET,
        "shows_escalation": holds,
        "escalation": {
            "key": ESCALATION_KEY,
            "label": ESCALATION_LABEL,
            "explanation": ESCALATION_HELP,
            "enabled": choices.of(ESCALATION_KEY).enabled,
        },
    }
    return render(request, "notifications/settings.html", context)


@login_required
@require_http_methods(["POST"])
def reset_settings(request: HttpRequest) -> HttpResponse:
    """«Taasta vaikimisi»: forget the person's explicit choices, and nothing else."""
    preferences.reset_choices(request.user)
    messages.success(request, "Teavituste seaded on taastatud vaikimisi.")
    return HttpResponseRedirect(reverse("notifications:settings"))
