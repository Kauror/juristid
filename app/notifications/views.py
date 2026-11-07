"""`/teavitused/` — one inbox, its answers, and the person's notification settings.

Thin on purpose: the population is `app.notifications.inbox`, every
commencement answer is a named service in `app.notifications.services`, the
deadline rows' read/hide state is `app.notifications.deadlines`, and the
preferences are `app.notifications.preferences`. This module reads the
request, asks, and redirects.

Three gates, each the one its write belongs to:

* **Reading** (the list, the bell, its panel): `@login_required` like every
  business read (ADR 0181). Behind the shared gate a session with no persona is
  sent to choose one; an administrator without a business role reads an empty
  inbox — every population starts from `visible_to`, which gives that caller
  nothing.
* **«Märgi loetuks», «Märgi lugemata», «Märgi kõik loetuks», «Lükka edasi»
  and the settings** are the signed-in person's own presentation state, gated
  like a remembered block (`accounts:save_disclosure`, ADR 0194):
  `@login_required`, CSRF POST, rows resolved by ``recipient=request.user``
  (another person's row is a 404), no parameter naming anybody else, no
  business service called and no `ChangeEvent` written (docs/adr/0198 §7). On
  a commencement check, reading and postponing are the reader's own state,
  never an answer: the check stays open for everybody else (ADR 0188
  amendment 2026-11-02). «Kontrollitud» was retired on 2026-10-30, «Sulge…»
  and «Peida» on 2026-11-02.
* **The bell's panel** (UI-01) is the same three populations plus their
  history (`app.notifications.feed`), opened and paged by GETs that mark
  nothing. The writes are `app.notifications.acknowledgement`.

**One answer, every surface.** Every write works as a plain form (it
redirects, PRG). Sent by htmx it answers in place instead — the item redrawn
in the panel, or the Minu asjad block redrawn — and names what changed in an
`HX-Trigger` header, which the bell's number, the block and `/teavitused/`
listen for, so every visible count follows without a reload.
"""

from __future__ import annotations

from typing import Any

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import Http404, HttpRequest, HttpResponse, HttpResponseRedirect
from django.shortcuts import render
from django.urls import reverse
from django.views.decorators.http import require_GET, require_http_methods

from app.core.authorization import may_view_department_management
from app.core.dates import format_estonian_date
from app.core.errors import DomainError
from app.notifications import (
    acknowledgement,
    deadlines,
    feed,
    inbox,
    preferences,
    selectors,
    services,
)
from app.notifications.categories import (
    CATEGORIES,
    ESCALATION_HELP,
    ESCALATION_KEY,
    ESCALATION_LABEL,
)
from app.notifications.email import EMAIL_NOT_YET
from app.notifications.enums import NotificationCategory
from app.notifications.forms import DeferForm
from app.notifications.models import ALLOWED_OFFSETS
from app.notifications.templatetags.notifications import block_context

#: Where an answer returns to. A fixed map rather than a `next` URL, so a
#: crafted form cannot turn this into a redirect anywhere else.
RETURN_TO = {
    "teavitused": "notifications:list",
    "minu_asjad": "matters:my_work",
    # The panel needs scripting, so a plain POST from it should not happen; if
    # one does, the list is where its answer is visible.
    "paneel": "notifications:list",
}

#: The fragment a return lands on, so the reader is back at the block they used.
RETURN_ANCHOR = {"minu_asjad": "#teavitused"}

#: What an answer from each surface tells the others (`HX-Trigger`). The bell
#: refetches its number on the first; the Minu asjad block and `/teavitused/`
#: redraw themselves on theirs. A surface is not told about its own answer,
#: which it has already drawn.
CHANGED = "notificationsChanged"
SIDEBAR_STALE = "notificationsSidebarStale"
PAGE_STALE = "notificationsPageStale"
TRIGGERS = {
    "paneel": (CHANGED, SIDEBAR_STALE, PAGE_STALE),
    "minu_asjad": (CHANGED,),
}


def _back(request: HttpRequest, *, anchor: bool = False) -> HttpResponseRedirect:
    key = str(request.POST.get("tagasi", ""))
    name = RETURN_TO.get(key, "notifications:list")
    fragment = RETURN_ANCHOR.get(key, "") if anchor else ""
    return HttpResponseRedirect(reverse(name) + fragment)


def _is_htmx(request: HttpRequest) -> bool:
    return request.headers.get("HX-Request") == "true"


def _source(request: HttpRequest) -> str:
    return str(request.POST.get("tagasi", ""))


def _triggered(response: HttpResponse, source: str) -> HttpResponse:
    response["HX-Trigger"] = ", ".join(TRIGGERS.get(source, (CHANGED,)))
    return response


@login_required
@require_GET
def reminder_list(request: HttpRequest) -> HttpResponse:
    """«Teavitused»: what needs doing, what is for information, what was put off, then the history.

    The sections above the history come from `inbox.inbox_for` — the
    population the bell counts and Minu asjad shows. The history is
    `feed.history_page`: what the reader has read and what was resolved,
    newest first, a page at a time (``?varasemad=<cursor>``). The unowned
    commencement section is built only for `department.view_management`; for
    anybody else it is absent from the response rather than hidden in it.
    """
    user = request.user
    # The boundary first: anything committed after it is either not drawn
    # or, if drawn, left unread by «Märgi kõik loetuks» — never the reverse.
    boundary = acknowledgement.boundary()
    items = inbox.inbox_for(user)
    cursor = feed.Cursor.decode(request.GET.get("varasemad", ""))
    context = {
        "items": items,
        "action_items": [item for item in items if item.section == inbox.SECTION_ACTION],
        "info_items": [item for item in items if item.section == inbox.SECTION_INFO],
        "unowned_items": [item for item in items if item.section == inbox.SECTION_UNOWNED],
        "deferred_rows": selectors.deferred_reminders_for(user),
        "shows_unowned": may_view_department_management(user),
        "history": feed.history_page(user, cursor=cursor),
        "history_continued": cursor is not None,
        "boundary": acknowledgement.encode_boundary(boundary),
        "tagasi": "teavitused",
    }
    return render(request, "notifications/inbox.html", context)


@login_required
@require_GET
def bell_count(request: HttpRequest) -> HttpResponse:
    """The bell's number, fetched after the page has rendered.

    Its own request so that the bell adds no query to any page's own render:
    three `COUNT`s asked once the page has loaded (``hx-trigger="load"``), and
    again when an answer says the inbox changed — the inbox's populations,
    never loaded as rows.
    """
    return render(
        request,
        "notifications/_bell_count.html",
        {"count": inbox.inbox_count(request.user)},
    )


def _panel_context(request: HttpRequest, **extra: Any) -> dict[str, Any]:
    # The boundary before the list, for the reason `reminder_list` gives.
    boundary = acknowledgement.boundary()
    return {
        "page": feed.panel_page(request.user),
        "boundary": acknowledgement.encode_boundary(boundary),
        "unread_total": inbox.inbox_count(request.user),
        **extra,
    }


@login_required
@require_GET
def panel(request: HttpRequest) -> HttpResponse:
    """The bell's panel, or with ``?enne=<cursor>`` its next page. Marks nothing read.

    Opening the panel is reading a list, not reading the notifications in it:
    every item keeps its state until the reader answers it, or presses «Märgi
    kõik loetuks» (owner's brief of 2026-11-06, UI-01).
    """
    if "enne" in request.GET:
        cursor = feed.Cursor.decode(request.GET["enne"])
        if cursor is None:
            return HttpResponse("Vigane leheküljeviide.", status=400)
        page = feed.panel_page(request.user, cursor=cursor)
        return render(request, "notifications/_panel_page.html", {"page": page})
    return render(request, "notifications/_panel.html", _panel_context(request))


@login_required
@require_GET
def block(request: HttpRequest) -> HttpResponse:
    """The Minu asjad «Teavitused» block on its own, for the reader signed in.

    What the block's place on Minu asjad asks for after an answer given in the
    panel. Empty when nothing awaits the reader, exactly like the block itself.
    """
    return render(
        request, "notifications/_minu_asjad_block.html", block_context(request.user, "minu_asjad")
    )


def _inbox_reminder_or_404(request: HttpRequest, pk: Any) -> Any:
    reminder = selectors.readable_inbox_reminder(request.user, pk)
    if reminder is None:
        raise Http404("Meeldetuletust ei leitud.")
    return reminder


def _answer(
    request: HttpRequest,
    kind: str,
    pk: Any,
    *,
    notice: str = "",
    error: str = "",
    title: str = "",
    anchor: bool = False,
) -> HttpResponse:
    """The reply to a read, unread or «Lükka edasi», from whichever surface sent it.

    A plain form: the message and the redirect it always had. From the panel:
    the item redrawn in its new state (`feed.lookup`). From the Minu asjad
    block: the block redrawn, which now leaves out what was answered. A refusal
    is a 400 carrying the same surface with the reason on it.
    """
    source = _source(request)
    if not _is_htmx(request) or source not in TRIGGERS:
        if error:
            messages.error(request, error)
        elif notice:
            messages.success(request, notice)
        return _back(request, anchor=anchor)
    status = 400 if error else 200
    if source == "paneel":
        context = {
            "item": feed.lookup(request.user, kind, pk),
            "notice": notice,
            "error": error,
            "title": title,
        }
        response = render(request, "notifications/_panel_item.html", context, status=status)
    else:
        context = {**block_context(request.user, "minu_asjad"), "error": error}
        response = render(request, "notifications/_minu_asjad_block.html", context, status=status)
    return _triggered(response, source)


@login_required
@require_http_methods(["POST"])
def defer(request: HttpRequest, pk: Any) -> HttpResponse:
    """«Lükka edasi» on a commencement check — for this reader only.

    Like «Märgi loetuks»: a prompt in the reader's own inbox, presentation
    state, no `ChangeEvent`. A colleague or another manager is still shown it.
    """
    reminder = _inbox_reminder_or_404(request, pk)
    title = reminder.effective_date.matter.title
    form = DeferForm(request.POST)
    if not form.is_valid():
        errors = [str(error) for found in form.errors.values() for error in found]
        return _answer(request, inbox.TYPE_COMMENCEMENT, pk, error=" ".join(errors), title=title)
    try:
        until = services.defer_for_reader(
            reminder=reminder, reader=request.user, until=form.cleaned_data["until"]
        )
    except DomainError as refusal:
        return _answer(request, inbox.TYPE_COMMENCEMENT, pk, error=str(refusal), title=title)
    # The stored date, which a seven-day prompt caps at the commencement day.
    return _answer(
        request,
        inbox.TYPE_COMMENCEMENT,
        pk,
        notice=f"Meeldetuletus lükati sinu jaoks edasi kuni {format_estonian_date(until)}.",
        title=title,
    )


@login_required
@require_http_methods(["POST"])
def mark_reminder_read(request: HttpRequest, pk: Any) -> HttpResponse:
    """«Märgi loetuks» on a commencement check: this reader has seen it.

    Only a prompt in the reader's own inbox (their desk, or the unowned ones for
    management) — anything else is a 404. The check stays ``OPEN`` and no
    `ChangeEvent` is written: reading is not checking, and the next owner or
    another manager is still asked.
    """
    reminder = _inbox_reminder_or_404(request, pk)
    services.mark_reminder_read(reminder=reminder, reader=request.user)
    return _answer(request, inbox.TYPE_COMMENCEMENT, reminder.pk, anchor=True)


@login_required
@require_http_methods(["POST"])
def mark_reminder_unread(request: HttpRequest, pk: Any) -> HttpResponse:
    """«Märgi lugemata» on a commencement check: undo this reader's own receipt.

    The same gate as reading it: a prompt still in the reader's own inbox
    population, or a 404. Nothing shared changes.
    """
    reminder = _inbox_reminder_or_404(request, pk)
    services.mark_reminder_unread(reminder=reminder, reader=request.user)
    return _answer(request, inbox.TYPE_COMMENCEMENT, reminder.pk, anchor=True)


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
    """«Märgi loetuks» — the recipient's own row only. Changes nothing about the work.

    The row then leaves the bell, the block and the inbox sections
    (`inbox.deadline_rows`) and stays in the history; the deadline itself is
    untouched and still on the Teema and in the reader's work lists.
    """
    row = _own_row_or_404(request, notification_id)
    deadlines.mark_read(notification=row)
    return _answer(request, inbox.TYPE_DEADLINE, row.pk, anchor=True)


@login_required
@require_http_methods(["POST"])
def mark_unread(request: HttpRequest, notification_id: Any) -> HttpResponse:
    """«Märgi lugemata» — the recipient's own, unhidden row back in their inbox."""
    row = _own_row_or_404(request, notification_id)
    deadlines.mark_unread(notification=row)
    return _answer(request, inbox.TYPE_DEADLINE, row.pk, anchor=True)


# ---------------------------------------------------------------------------
# «Uus asi», read and unread from the panel (ADR 0051)
# ---------------------------------------------------------------------------


def _own_notice_or_404(request: HttpRequest, notice_id: Any) -> Any:
    notice = acknowledgement.resolve(request.user, inbox.TYPE_ASSIGNMENT, notice_id)
    if notice is None:
        raise Http404("Teavitust ei leitud.")
    return notice


@login_required
@require_http_methods(["POST"])
def mark_assignment_read(request: HttpRequest, notice_id: Any) -> HttpResponse:
    """«Märgi loetuks» on «Uus asi» without opening the Teema.

    The same receipt «Ava» leaves (`acknowledge_assignment_notice`): this
    person acted on this notice. Their own receipt only — anybody else's, or
    one handed on, is a 404.
    """
    notice = _own_notice_or_404(request, notice_id)
    acknowledgement.mark_read(request.user, inbox.TYPE_ASSIGNMENT, notice)
    return _answer(request, inbox.TYPE_ASSIGNMENT, notice.pk, anchor=True)


@login_required
@require_http_methods(["POST"])
def mark_assignment_unread(request: HttpRequest, notice_id: Any) -> HttpResponse:
    """«Märgi lugemata» on a read «Uus asi» about a Teema that is still the reader's."""
    notice = _own_notice_or_404(request, notice_id)
    acknowledgement.mark_unread(request.user, inbox.TYPE_ASSIGNMENT, notice)
    return _answer(request, inbox.TYPE_ASSIGNMENT, notice.pk, anchor=True)


# ---------------------------------------------------------------------------
# «Märgi kõik loetuks»
# ---------------------------------------------------------------------------


@login_required
@require_http_methods(["POST"])
def mark_all_read(request: HttpRequest) -> HttpResponse:
    """Everything awaiting the reader that had arrived by ``kuni``, read at once.

    ``kuni`` is the boundary the panel or the list was drawn with; a form
    without a valid one is refused and changes nothing. The reader is the
    session's, never a parameter. From the panel the answer is the panel
    redrawn with a new boundary; from the list, the list (PRG).
    """
    source = _source(request)
    until = acknowledgement.decode_boundary(request.POST.get("kuni"))
    in_panel = _is_htmx(request) and source == "paneel"
    if until is None:
        if in_panel:
            context = _panel_context(request, error=acknowledgement.BOUNDARY_REFUSED)
            response = render(request, "notifications/_panel.html", context, status=400)
            return _triggered(response, source)
        messages.error(request, acknowledgement.BOUNDARY_REFUSED)
        return _back(request)
    result = acknowledgement.mark_all_read(request.user, until=until)
    if in_panel:
        context = _panel_context(request, status=result.message)
        return _triggered(render(request, "notifications/_panel.html", context), source)
    messages.success(request, result.message)
    return _back(request)


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
