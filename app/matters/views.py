"""The work surfaces: Minu töö, Saabunud, Teemad and the Matter page.

Views resolve authorization, parse input through a form, call one service
function and render. They contain no workflow rules.

Two conventions worth knowing:

* Every Matter lookup goes through :func:`get_visible_matter`, which raises 404
  rather than 403 for a Matter the user may not see. A 403 would confirm the
  record exists, which is itself a disclosure on a restricted file.
* HTMX fragments re-render the whole surface they belong to rather than patching
  pieces. One render from one query set cannot disagree with itself, which is
  what keeps `Järgmiseks` and the timeline consistent after a composer save.
"""

from __future__ import annotations

import uuid
from datetime import date, timedelta
from typing import Any

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Q
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_http_methods

from app.accounts.models import User
from app.accounts.selectors import (
    assignable_business_users,
    assignable_including,
    is_person_identifier,
    named_owner_in,
    owner_filter_choices,
)
from app.core.authorization import (
    may_review_work_victory,
    may_write_business_content,
)
from app.core.dates import (
    format_estonian_date,
    parse_flexible_date,
    short_day_month,
    weekday_letter,
)
from app.core.decorators import business_write_required
from app.core.enums import Visibility
from app.core.errors import DomainError
from app.documents.enums import DocumentRole
from app.documents.models import Document
from app.documents.services import link_working_document
from app.documents.uploads import UploadRejected
from app.intelligence.selectors import matter_intelligence
from app.legacy_import.register_display import (
    register_facts_for,
    snapshot_label,
    source_instruction_for,
    source_instructions_for,
)
from app.matters import department_dashboard, register_filters, selectors, work_items
from app.matters import person_work as person_workspace
from app.matters.department_dashboard import SeisFigure
from app.matters.enums import MatterOrigin, RecordMode
from app.matters.forms import (
    BriefSummaryForm,
    CloseMatterForm,
    ComposerForm,
    EngagementForm,
    IncomingIntakeForm,
    MatterCreateForm,
    MatterEditForm,
    MatterFieldForm,
    NextActionForm,
    PersonalNoteForm,
    PositionForm,
    WorkingDocumentForm,
    edit_initial,
)
from app.matters.intake import register_incoming, validate_uploads
from app.matters.models import Matter, MatterAssignmentNotice, MatterEngagement
from app.matters.my_work import (
    HORIZON_PARAM,
    VIEW_PARAM,
    build_my_work,
    horizon_from,
    view_from,
)
from app.matters.services import (
    acknowledge_assignment_notice,
    add_engagement,
    assign_matter,
    change_stage,
    change_track,
    close_matter,
    compose_update,
    create_matter,
    personal_note_for,
    reopen_matter,
    resolve_addressee,
    save_personal_note,
    set_brief_summary,
    set_matter_data_class,
    set_matter_dates,
    set_matter_title,
    set_matter_visibility,
    set_organisations,
    set_policy_area_other,
    set_policy_areas,
    set_position,
    set_tags,
    update_engagement,
)
from app.matters.timeline import (
    TIMELINE_FILTER_ALL,
    TIMELINE_FILTERS,
    collapse_system_runs,
    latest_authored,
    matter_timeline,
)
from app.organisations.models import Organisation
from app.search import services as search_services
from app.submissions import embedded as opinions
from app.submissions.forms import (
    CREATE_PREFIX,
    REGISTER_PREFIX,
    RegisterSentOpinionForm,
    SubmissionCreateForm,
)
from app.submissions.opinions import (
    OPINION_ROLE_FILTER,
    open_drafts,
    opinion_document_ids,
    opinion_documents,
    sent_submission_by_document,
)
from app.taxonomy.models import PolicyArea
from app.taxonomy.vocabulary import selectable_policy_areas
from app.workflow.enums import REVIEW_KINDS, ActionKind, Disposition, Track
from app.workflow.models import NextAction, StageVocabulary
from app.workflow.services import (
    acknowledge_review,
    complete_next_action,
    current_next_action,
    set_next_action_for_new_work,
)

#: How many register rows a page holds, and the sizes a reader may choose
#: instead. Twelve by default: the v2 design puts the Arvamused section under
#: the register, and twenty-five rows made a reader scroll past the whole
#: register to find out that the page had a second half (02-EKRAANID §C).
#:
#: `koik` is a real option and is bounded rather than unlimited — a register of
#: two and a half thousand rows rendered in one response is a page nobody waits
#: for, and the bound is high enough that «kõik» means it for every filtered
#: view anybody actually opens.
PAGE_SIZE = 12
PAGE_SIZE_PARAM = "kaupa"
PAGE_SIZE_CHOICES: tuple[int, ...] = (12, 30, 50)
PAGE_SIZE_ALL = "koik"
PAGE_SIZE_ALL_BOUND = 2000
TIMELINE_PAGE_SIZE = 30


def page_size_from(raw: str | None) -> tuple[int, str]:
    """How many rows this request asks for, and which chip is marked.

    Anything unrecognised falls back to the default rather than raising or
    emptying the list: a hand-edited URL should show the register, not an
    argument about it.
    """
    value = (raw or "").strip()
    if value == PAGE_SIZE_ALL:
        return PAGE_SIZE_ALL_BOUND, PAGE_SIZE_ALL
    if value.isdigit() and int(value) in PAGE_SIZE_CHOICES:
        return int(value), value
    return PAGE_SIZE, str(PAGE_SIZE)


#: The private note's form prefix. It shares a field name with the composer —
#: both are called `body` — and two elements with the same id on one page make
#: every label ambiguous for a screen reader and for a browser test.
NOTE_PREFIX = "markmed"


def get_visible_matter(request: HttpRequest, pk: Any) -> Matter:
    """Fetch a Matter the signed-in user is allowed to read, or 404.

    404 rather than 403 on purpose: distinguishing "forbidden" from "missing"
    tells an unauthorized caller that a restricted Matter with that id exists.
    """
    queryset = (
        Matter.objects.visible_to(request.user)
        .select_related(
            "owner",
            "stage",
            "addressee_organisation",
            "superseded_by",
            # The derived register row. Joined rather than reached for, because
            # the page asks it two separate questions — what the register's own
            # JÄRGMISEKS says, and what it observed around the outreach — and a
            # reverse one-to-one costs a query at the first access even when the
            # answer is that there is no row at all.
            "current_register_state",
        )
        .prefetch_related(
            "source_organisations",
            "policy_areas",
            "tags",
            "collaborators",
            # `Seotud` reads the successor chain in both directions, and the
            # reverse side is a relation rather than a column.
            "supersedes",
        )
    )
    return get_object_or_404(queryset, pk=pk)


# ---------------------------------------------------------------------------
# Minu töö
# ---------------------------------------------------------------------------


@login_required
def my_work(request: HttpRequest) -> HttpResponse:
    """Minu asjad — one chronological answer to "what do I do now".

    Every population on the page is read once, through the shared work model, so
    the strip's counts and the bands under them come from the same list and
    cannot disagree (app/matters/work_items.py).
    """
    return _render_person_work(request, subject=request.user, is_self=True)


@login_required
def person_work(request: HttpRequest, pk: Any) -> HttpResponse:
    """One colleague's desk, for that colleague or for the department head.

    The same page, the same read model and the same template as Minu asjad.
    What differs is three things and they are all in `_render_person_work`: the
    crumb and switcher above the heading, Kiirvaade where the notes are, and the
    notes themselves — which are not fetched here at all
    (app/matters/person_work.py).

    404 rather than 403 for anybody else, and 404 for an id that names nobody:
    the two answers are identical on purpose, so the route cannot be used to
    find out who exists.
    """
    subject = person_workspace.resolve_subject(pk)
    if subject is None or not person_workspace.may_open_person_work(request.user, subject):
        raise Http404("See töölaud ei ole sinu oma.")
    return _render_person_work(request, subject=subject, is_self=subject.pk == request.user.pk)


def _render_person_work(request: HttpRequest, *, subject: Any, is_self: bool) -> HttpResponse:
    today = timezone.localdate()
    horizon = horizon_from(request.GET.get(HORIZON_PARAM), today)
    view = view_from(request.GET.get(VIEW_PARAM))
    work = build_my_work(request.user, today=today, horizon=horizon, subject=subject, view=view)

    context: dict[str, Any] = {
        "today": today,
        "work": work,
        "is_self": is_self,
        "subject": subject,
        # One query for the whole rail, not one per row. The register's own
        # sentence is the context a lawyer needs in order to set a next
        # step, and these Matters are by definition the ones where only the
        # register has anything to say (ADR 0021).
        "source_instructions": source_instructions_for([row.matter for row in work.quiet]),
        # Which approved workbook that wording is a photograph of. Excel is
        # still being edited, so an undated "Excelist" chip invites somebody
        # to act on a sentence that has since moved (ADR 0021).
        "source_snapshot": snapshot_label(),
        "nav_active": "minu_asjad",
    }
    if is_self:
        # Fetched only here, and only for `request.user`. The manager branch
        # does not read it, so the block it feeds is absent from that response
        # rather than hidden in it (01-EHITUSJUHIS §3.5).
        context["scratchpad"] = person_workspace.scratchpad_for(request.user)
        # «Uus asi», under the same rule and for the same reason. A colleague's
        # unread hand-overs are their own workflow state: the department head's
        # branch below does not query them, so there is no section, no heading
        # and no Matter title from this queue anywhere in that response
        # (app/matters/person_work.py, docs/adr/0051).
        context["assignment_notices"] = person_workspace.unread_assignment_notices(request.user)
    else:
        context["switcher"] = person_workspace.build_switcher(subject)
    return render(request, "matters/my_work.html", context)


@login_required
@require_http_methods(["POST"])
def open_assignment_notice(request: HttpRequest, notice_id: Any) -> HttpResponse:
    """Open a newly assigned Matter *from* «Uus asi», and mark it seen.

    A POST rather than a link, and its own route rather than a side effect of
    `matter_detail`, because the two are different facts. Opening the Matter
    says the page was rendered; this says *the recipient acted on the notice*.
    They come apart in the case the product explicitly requires: somebody who
    creates a Teema and puts their own name on it is redirected straight into
    it, and if rendering counted, their own «Uus asi» would clear itself before
    they ever saw Minu asjad (docs/adr/0051).

    Four refusals, all 404 and all in the order that leaks least:

    * a notice that is not this person's does not resolve — `recipient` is in
      the lookup, so user A cannot acknowledge user B's row and cannot learn
      that it exists;
    * a **superseded** notice does not resolve either. The block on the rail is
      gone the moment the file is handed on, but the page somebody already has
      open is not: a browser sitting on Minu asjad from before the reassignment
      still carries the form. That stale POST must not turn a retired receipt
      into a viewed one — `superseded_at` and `viewed_at` are two different
      terminal reasons, and «the file left this desk» must never be recorded as
      «this person looked at it» (docs/adr/0051);
    * the Matter goes through `get_visible_matter` like every other route, so a
      restricted file is 404 here as it is everywhere else. Ownership is not
      authorization;
    * an unknown id is the same 404 as the other three.

    A notice that is merely *already viewed* still resolves, and deliberately.
    That is not a stale form, it is the same live receipt submitted twice — a
    double click, a resend, a back button — and the stamp is one conditional
    UPDATE, so the second POST changes nothing and lands the reader on the
    Matter exactly as the first did.
    """
    notice = get_object_or_404(
        MatterAssignmentNotice.objects.select_related("matter"),
        pk=notice_id,
        recipient=request.user,
        superseded_at__isnull=True,
    )
    matter = get_visible_matter(request, notice.matter_id)
    acknowledge_assignment_notice(notice=notice, actor=request.user)
    return redirect(reverse("matters:matter_detail", kwargs={"pk": matter.pk}))


@login_required
@require_http_methods(["POST"])
def save_scratchpad(request: HttpRequest) -> HttpResponse:
    """Autosave the signed-in person's own notepad.

    `request.user`, and nothing else. There is no subject parameter, this view
    does not read one, and the service it calls has no signature that could
    accept one — so there is no URL, form field or header that widens this to
    another person's notes (03-BACKEND §2).

    Answers the saved timestamp as a fragment, because the only thing the page
    needs back is the meta line under the textarea.
    """
    row = person_workspace.save_scratchpad(request.user, request.POST.get("body", ""))
    return render(request, "matters/partials/scratchpad_meta.html", {"scratchpad": row})


@login_required
@business_write_required
@require_http_methods(["POST"])
def complete_work_item(request: HttpRequest, action_id: Any) -> HttpResponse:
    """Mark one step done from Minu töö, without opening its Matter.

    The whole gesture the ✓ on the row is: the same service the Matter page's
    «✓ Tehtud» calls, with the same authorization, the same refusal and the same
    audit row. What is different is only where the reader ends up — back on the
    list they were working through, with the window they had chosen still in the
    address (`?kuni=`).

    Reached through `visible_to`, so an action restricted below its Matter is a
    404 here exactly as it is everywhere else — and `.open()`, because a step
    somebody has already finished in another tab must not be finished twice
    (design handoff 1e).
    """
    action = get_object_or_404(
        NextAction.objects.visible_to(request.user).open().select_related("matter"),
        pk=action_id,
    )
    # The Matter itself, through the same gate any other route uses. An action
    # is only reachable if its Matter is, and asking twice costs nothing.
    get_visible_matter(request, action.matter_id)

    try:
        complete_next_action(action=action, actor=request.user)
    except DomainError as error:
        messages.error(request, str(error))
    else:
        messages.success(request, "Tegevus on märgitud tehtuks.")

    return redirect(_safe_next(request) or reverse("matters:my_work"))


def _safe_next(request: HttpRequest) -> str:
    """A redirect target, only if it points back at this site.

    The same guard `app.accounts.views` applies to the persona switch: a `next`
    a browser sent is somebody's input until it has been checked.
    """
    candidate = request.POST.get("next") or ""
    if candidate and url_has_allowed_host_and_scheme(
        candidate, allowed_hosts={request.get_host()}, require_https=request.is_secure()
    ):
        return candidate
    return ""


@login_required
def inbox(request: HttpRequest) -> HttpResponse:
    """Saabunud — the triage entry point.

    Deliberately thin: what has arrived and nobody has taken, and what was
    opened lately. Machine intake and an `IntakeItem` queue are later work and
    are not faked here.

    The strip's three figures are counted through the register's own filter
    pipeline over the parameters that *are* their definition, so each number and
    the list its link opens are one query rather than two similar ones
    (`register_population`, 01-EHITUSJUHIS §3.3).
    """
    today = timezone.localdate()
    unassigned_queryset = (
        selectors.matter_list_queryset(request.user)
        .filter(owner__isnull=True, is_open=True)
        .order_by("-created_at")
    )
    unassigned_total = unassigned_queryset.count()
    unassigned = list(unassigned_queryset[:INBOX_LIMIT])
    recent = (
        selectors.matter_list_queryset(request.user)
        .filter(is_open=True)
        .order_by("-created_at")[:10]
    )
    return render(
        request,
        "matters/inbox.html",
        {
            # Four rows on screen and the rest behind «Näita veel N ▾» — the
            # same list, sliced here rather than fetched twice, so the number in
            # the heading and what the accordion opens cannot disagree
            # (02-EKRAANID §F).
            "unassigned": unassigned[:INBOX_PREVIEW],
            "unassigned_rest": unassigned[INBOX_PREVIEW:],
            "unassigned_total": unassigned_total,
            "recent": recent,
            "seis": inbox_figures(request.user, today),
            "intake_form": IncomingIntakeForm(viewer=request.user),
            "source_instructions": source_instructions_for([*unassigned, *recent]),
            "source_snapshot": snapshot_label(),
            "nav_active": "saabunud",
        },
    )


#: How many unassigned rows the table shows before the rest go behind
#: «Näita veel N ▾», and how many the accordion may hold at all
#: (02-EKRAANID §F). The honest total is in the heading either way, and the
#: strip's own figure links to the register for the rest.
INBOX_PREVIEW = 4
INBOX_LIMIT = 25


def inbox_figures(user: Any, today: date) -> list[SeisFigure]:
    """«vastutajata teemat», «loodud sel nädalal», «loodud sel kuul».

    Counted and linked through the same register parameters, so a reader who
    follows a number lands on exactly the rows it counted.
    """
    population = Matter.objects.visible_to(user)

    def figure(key: str, caption: str, tone: str, **params: Any) -> SeisFigure:
        query = {"olek": "avatud", "liik": RecordMode.FULL.value, **params}
        return SeisFigure(
            key=key,
            value=register_filters.register_population(
                user, query, today=today, population=population
            ).count(),
            caption=caption,
            url=department_dashboard.register_url(**query),
            tone=tone,
        )

    week_start = work_items.start_of_iso_week(today)
    return [
        figure("unassigned", "vastutajata teemat", "warning", vastutaja=selectors.MISSING),
        figure(
            "week",
            "loodud sel nädalal",
            "",
            loodud_alates=format_estonian_date(week_start),
            loodud_kuni=format_estonian_date(today),
        ),
        figure(
            "month",
            "loodud sel kuul",
            "",
            loodud_alates=format_estonian_date(today.replace(day=1)),
            loodud_kuni=format_estonian_date(today),
        ),
    ]


@login_required
@business_write_required
@require_http_methods(["GET", "POST"])
def intake(request: HttpRequest) -> HttpResponse:
    """File material that has just arrived, files first.

    The second legitimate way into a Matter. `Uus teema` stays title-first and
    needs nothing but a title; this path starts from the PDF a ministry sent,
    which is how most incoming work actually begins.

    Every upload is validated *before* anything is written, so a rejected file
    cannot leave a half-made Matter behind looking like real work.
    """
    form = IncomingIntakeForm(request.POST or None, viewer=request.user)

    if request.method == "POST":
        files = request.FILES.getlist("uploads")
        if form.is_valid():
            try:
                uploads = validate_uploads(files)
                data = form.cleaned_data
                result = register_incoming(
                    uploads=uploads,
                    title=data.get("title", ""),
                    actor=request.user,
                    owner=data.get("owner"),
                    source_organisations=list(data.get("source_organisations") or []),
                    received_date=data.get("received_date") or timezone.localdate(),
                    response_deadline=data.get("response_deadline"),
                    visibility=data.get("visibility") or Visibility.NORMAL,
                    brief_summary=data.get("brief_summary", ""),
                    handover_note=data.get("handover_note", ""),
                )
            except (DomainError, UploadRejected) as error:
                messages.error(request, str(error))
            else:
                messages.success(
                    request,
                    f"Teema „{result.matter.title}” loodud · {result.documents} faili lisatud.",
                )
                return redirect("matters:matter_detail", pk=result.matter.pk)

        return render(
            request,
            "matters/intake.html",
            {"form": form, "nav_active": "saabunud"},
            status=400,
        )

    return render(request, "matters/intake.html", {"form": form, "nav_active": "saabunud"})


# ---------------------------------------------------------------------------
# Teemad
# ---------------------------------------------------------------------------


#: Human labels for the filter chips. The chip and the query string are built
#: from the same place, so what is on screen cannot disagree with the URL.
FILTER_LABELS = {
    "ulatus": "Ulatus",
    "vastutaja": "Vastutaja",
    "hetkeseis": "Hetkeseis",
    "menetlusliik": "Menetlusliik",
    "valdkond": "Valdkond",
    "silt": "Silt",
    "liik": "Kirje liik",
    # Three dimensions the register gained so that a statistic can open the
    # exact population it counted. Each one is the *same* condition the chart
    # used — imported from `selectors`, not restated — because a drill-through
    # that filters slightly differently from the bar above it is worse than no
    # drill-through at all (Stage-2E brief 38, 66).
    "aasta": "Aruandlusaasta",
    "paritolu": "Päritolu",
    "allikas": "Ajalooline allikas",
    "tegevus": "Järgmine tegevus",
    # The dated-work populations Ülevaade counts. A dimension of its own rather
    # than more values on `tegevus`, because it is not about the open action at
    # all: a passed `Oluline tähtaeg` is late work on a Matter that may carry no
    # action whatsoever (app/matters/work_items.py).
    "too": "Töö seis",
    "too_vastutaja": "Töö vastutaja",
    # The window `?too=tahtaeg-vahemik` reads. Companions to `?too=`, like the
    # responsible above them: they narrow it and select nothing on their own.
    "too_alates": "Töö alates",
    "too_kuni": "Töö kuni",
    # What the register recorded about the drafting step. A dimension of its own
    # rather than a synonym for `tegevus`: a Matter can carry a next action and
    # an unfinished opinion at the same time, and they are different questions
    # (app/matters/register_filters.py, ADR 0021).
    "arvamus": "Arvamus",
    # Two organisation filters, never one. `KELLELT` and `KELLELE` are
    # different facts and the register itself changed which one its single
    # counterparty column meant in 2020, so a combined filter would answer a
    # question nobody asked (Stage-2E brief 27).
    "saatja": "Algataja või saatja",
    "adressaat": "Adressaat",
    # Stage 2E.1. The convenience filter sits *beside* the two precise ones and
    # never replaces them: `asutus` asks "was this body involved at all", which
    # is the question somebody has when they cannot remember which direction a
    # letter went (Stage-2E.1 brief 11F).
    "asutus": "Asutus",
    "loodud_alates": "Loodud alates",
    "loodud_kuni": "Loodud kuni",
    "saabus_alates": "Saabus alates",
    "saabus_kuni": "Saabus kuni",
    "tahtaeg_alates": "Tähtaeg alates",
    "tahtaeg_kuni": "Tähtaeg kuni",
    "materjalid": "Materjalid",
    # Real business data or development data. Sits with the other dimensions
    # rather than beside the status segments, because it narrows the population
    # like any filter and belongs in the same chip row and the same shared URL
    # (Agent-C brief 24, 26).
    "andmed": "Andmed",
    # The closing year, which is not the reporting year. Ülevaade's Aruandlus
    # rail counts what the department finished this year and used to link to
    # every closed Matter there has ever been.
    "suletud": "Suletud aastal",
}

#: What `?materjalid=` reads as in a chip.
MATERIAL_LABELS = {
    selectors.MATERIALS_PRESENT: "Failid olemas",
    selectors.MATERIALS_ABSENT: "Failid puuduvad",
}

#: What `?andmed=` reads as, in the chip and in the control. `Kõik` is the
#: default and therefore never becomes a chip — a chip for the absence of a
#: filter is noise the reader has to learn to ignore.
DATA_CLASS_LABELS = {
    selectors.DATA_CLASS_ALL: "Kõik",
    selectors.DATA_CLASS_REAL: "Päris",
    selectors.DATA_CLASS_TEST: "Test",
}

#: Every filter parameter the register understands, plus the sort and the free
#: text. Used to decide whether `Tühjenda kõik` has anything to clear and to
#: build the hidden inputs the search box carries, so a dimension added to
#: `FILTER_LABELS` is picked up by both without a second list to keep in step.
REGISTER_PARAMS = (*FILTER_LABELS, "olek", "jarjestus", "q")

#: Suffix of a parameter that steers a *control* rather than the population.
#: The organisation chooser's own search box lives inside the filter form, so
#: submitting the form carries whatever was typed into it. That text narrows a
#: list of options and nothing else — it must not survive into a shared link,
#: and `Tühjenda kõik` must not leave it behind looking like a filter that is
#: still applied.
CONTROL_PARAM_SUFFIX = "_otsing"


def _is_control_param(name: str) -> bool:
    return name.endswith(CONTROL_PARAM_SUFFIX)


def _page_size_options(params: Any, current: str) -> list[dict[str, Any]]:
    """The «näita korraga» chips, each carrying the whole address it belongs to."""
    options: list[dict[str, Any]] = []
    for key in (*(str(size) for size in PAGE_SIZE_CHOICES), PAGE_SIZE_ALL):
        query = params.copy()
        query.pop("leht", None)
        query[PAGE_SIZE_PARAM] = key
        options.append(
            {
                "key": key,
                "label": "kõik" if key == PAGE_SIZE_ALL else key,
                "query": query.urlencode(),
                "active": key == current,
            }
        )
    return options


#: How each `?arvamus=` value reads in a chip and in the control.
OPINION_LABELS = {
    register_filters.OPINION_DRAFTING: "Koostamisel",
    register_filters.OPINION_SENT: "Saadetud",
}

#: How each `?tegevus=` value reads in the control and in a filter chip.
#: Wording matters here: a review date that has arrived is "ülevaatus käes",
#: never "hilinenud". Neither option names the stored kind — the classification
#: is not something a reader is asked to hold (ADR 0054).
NEXT_ACTION_LABELS = {
    "puudub": "Puudub",
    "hilinenud": "Tähtaeg möödas",
    selectors.REVIEW_DUE: "Ülevaatus käes",
}

STATUS_SEGMENTS = (
    ("avatud", "Avatud"),
    ("suletud", "Suletud"),
    ("arhiiv", "Arhiiv"),
    ("koik", "Kõik"),
)


def _segment_queryset(user: Any, key: str) -> Any:
    """The population one segment counts, scoped before the count is taken."""
    base = Matter.objects.visible_to(user)
    if key == "avatud":
        return base.filter(is_open=True)
    if key == "suletud":
        return base.filter(is_open=False)
    if key == "arhiiv":
        return base.filter(record_mode=RecordMode.ARCHIVE)
    return base


def _status_options(request: HttpRequest, params: Any) -> list[dict[str, Any]]:
    current = params.get("olek", "avatud")
    options = []
    for key, label in STATUS_SEGMENTS:
        query = params.copy()
        query["olek"] = key
        query.pop("leht", None)
        options.append(
            {
                "key": key,
                "label": label,
                "active": current == key,
                "count": _segment_queryset(request.user, key).count(),
                "query": query.urlencode(),
            }
        )
    return options


def _named_by_pk(model: Any, raw: str) -> Any:
    """Look a row up by primary key without trusting the string.

    `Model.objects.filter(pk="mitte-uuid")` is not an empty result — it is a
    `ValidationError` from the field, and it takes the whole register page down
    with a 500. The register's own *filters* already guard against this by
    parsing the UUID first; the code that renders a filter *chip* did not, so a
    hand-edited or truncated URL crashed the page it was describing.

    Returns None for anything unparseable, and the caller falls back to showing
    the raw value — which is the honest thing to put in a chip for a filter that
    matched nothing.
    """
    try:
        return model.objects.filter(pk=uuid.UUID(raw)).first()
    except (ValueError, AttributeError, TypeError):
        return None


def _filter_display(request: HttpRequest, name: str, value: str) -> str:
    """Show the reader a name, not a primary key.

    The `puudub` sentinel is resolved *first*. Every branch below looks a value
    up by primary key, and `pk="puudub"` is not a failed lookup — it is a
    ValidationError that takes the whole register page down with it. Found by
    the first CI round, on `?vastutaja=puudub`.

    Malformed UUIDs are the same failure one step along, and were found the same
    way: `?asutus=mitte-uuid` 500ed while `?asutus=puudub` did not. Both go
    through `_named_by_pk` now.
    """
    if value == selectors.MISSING:
        return "Määramata"
    if name == "vastutaja":
        # Resolved inside this reader's own authorized register, not against
        # `User.objects`. A crafted `?vastutaja=<uuid>` must not answer with the
        # name of somebody who owns only work this reader may not see — the
        # option list is already bounded that way and the chip has to agree
        # (app/accounts/selectors.py `named_owner_in`, AUTH-003).
        if not is_person_identifier(value):
            # Not an identifier at all, so it names nobody and hiding it would
            # only leave the reader guessing what they had filtered by.
            return value
        person = named_owner_in(Matter.objects.visible_to(request.user), value)
        # The short name, matching the control the filter was chosen from: a
        # chip that reads "Vastutaja: Sandra Näidis" beside a select offering
        # "Sandra" looks like two different filters.
        #
        # An unresolvable value prints as unknown rather than as the raw UUID:
        # echoing the identifier back confirms nothing but reads like a fact.
        return person.get_short_name() if person else "tundmatu"
    if name == "hetkeseis":
        stage = StageVocabulary.objects.filter(key=value).first()
        return stage.label_et if stage else value
    if name == "menetlusliik":
        return dict(Track.choices).get(value, value)
    if name == "valdkond":
        area = PolicyArea.objects.filter(key=value).first()
        return area.name_et if area else value
    if name == "liik":
        return dict(RecordMode.choices).get(value, value)
    if name == "ulatus":
        return "Minu omad" if value == "minu" else "Kõik nähtavad"
    if name == "paritolu":
        labels = dict(MatterOrigin.choices)
        return ", ".join(labels.get(part, part) for part in value.split(","))
    if name == "aasta":
        return "Teadmata aasta" if value == selectors.UNKNOWN_YEAR else value
    if name == "allikas":
        if value == register_filters.SOURCE_SEVERAL:
            return "Mitu lähtelehte"
        return "Olemas" if value == register_filters.SOURCE_PRESENT else "Puudub"
    if name == "tegevus":
        return NEXT_ACTION_LABELS.get(value, value)
    if name == "too":
        return work_items.WORK_POPULATION_LABELS.get(value, value)
    if name == "too_vastutaja":
        person = _named_by_pk(User, value)
        return person.get_short_name() if person else value
    if name == "arvamus":
        return OPINION_LABELS.get(value, value)
    if name in {"saatja", "adressaat", "asutus"}:
        organisation = _named_by_pk(Organisation, value)
        return organisation.name if organisation else value
    if name == "materjalid":
        return MATERIAL_LABELS.get(value, value)
    if name == "andmed":
        return DATA_CLASS_LABELS.get(value, value)
    if (
        name
        in (
            register_filters.WORK_WINDOW_START_PARAM,
            register_filters.WORK_WINDOW_END_PARAM,
        )
        or name in register_filters.DATE_FILTERS
    ):
        # The parameter may carry either form — the control submits Estonian and
        # an older link carries ISO — and the chip reads back the one way this
        # application writes a date. An unparseable value is shown as typed, so
        # a chip above an empty register says what emptied it.
        parsed = parse_flexible_date(value)
        return format_estonian_date(parsed) if parsed else value
    return value


def _active_filters(request: HttpRequest, params: Any) -> list[dict[str, Any]]:
    chips = []
    for name, label in FILTER_LABELS.items():
        value = params.get(name, "")
        if not value or (name == "ulatus" and value == "koik"):
            continue
        if name == "andmed" and value == selectors.DATA_CLASS_ALL:
            continue
        # `?too_vastutaja=` narrows `?too=` and does nothing on its own. A chip
        # for a parameter that changed no rows is a chip that says the list is
        # narrower than it is.
        if name in (
            register_filters.WORK_RESPONSIBLE_PARAM,
            register_filters.WORK_WINDOW_START_PARAM,
            register_filters.WORK_WINDOW_END_PARAM,
        ) and not params.get(register_filters.WORK_PARAM):
            continue
        without = params.copy()
        without.pop(name, None)
        without.pop("leht", None)
        chips.append(
            {
                "name": name,
                "label": label,
                "value": _filter_display(request, name, value),
                "remove_query": without.urlencode(),
            }
        )
    return chips


SORT_FIELDS = {
    "reference": ("-reference_year", "-reference_number"),
    "title": ("title",),
    "updated": ("-updated_at",),
    "deadline": ("response_deadline",),
}


def _wants_fragment(request: HttpRequest) -> bool:
    """Whether this request wants the results block rather than the whole page.

    The register answers on **one** URL. A dedicated fragment route would be the
    house pattern (``matters:timeline_page`` is one), but the live search has to
    push the address it was answered from, and pushing a fragment route would
    leave people with ``/teemad/tulemused/?q=...`` in the address bar and in the
    links they share.

    ``HX-History-Restore-Request`` is the exception that makes Back work. HTMX
    sends it when its history cache has expired and it needs the whole page
    again; answering that with a fragment would replace the document with a bare
    table (Stage-2E.1 brief 7).
    """
    return (
        request.headers.get("HX-Request") == "true"
        and request.headers.get("HX-History-Restore-Request") != "true"
    )


@login_required
def matter_list(request: HttpRequest) -> HttpResponse:
    """The register. Dense, filtered through the URL, paginated server-side.

    Stage 2E.1 puts a search box on it. ``?q=`` narrows the *whole* filtered
    population through the search projection — not the rows already rendered on
    the current page — and composes with every structured filter as an
    intersection, so ``?q=pakend&aasta=2024&vastutaja=...`` means all three at
    once (Stage-2E.1 brief 8, 9, 10).
    """
    params = request.GET
    queryset = selectors.matter_list_queryset(request.user)

    # Applied first, so everything below narrows an already-searched population
    # and the count beside the box is the count of the list under it.
    query = (params.get("q") or "").strip()
    if query:
        queryset = queryset.filter(
            pk__in=search_services.matching_matter_ids(query=query, user=request.user)
        )

    # One call, and it is the same call the Ülevaade cards count through. The
    # register and the KPI above it therefore cannot disagree about what
    # "Arvamusi koostamisel" means — there is one definition, in one place
    # (app/matters/register_filters.py).
    status = params.get("olek", "avatud")
    scope = params.get("ulatus", "koik")
    queryset, date_echo = register_filters.apply_register_filters(queryset, request.user, params)

    sort = params.get("jarjestus", "reference")
    queryset = queryset.order_by(*SORT_FIELDS.get(sort, SORT_FIELDS["reference"]), "-created_at")

    per_page, page_size_key = page_size_from(params.get(PAGE_SIZE_PARAM))
    paginator = Paginator(queryset.distinct(), per_page)
    page = paginator.get_page(params.get("leht"))

    query_without_page = params.copy()
    query_without_page.pop("leht", None)

    # `Tuhjenda koik` returns to the bare register rather than to "everything
    # except the one dimension I forgot to list".
    cleared = params.copy()
    for parameter in REGISTER_PARAMS:
        cleared.pop(parameter, None)
    cleared.pop("leht", None)
    for parameter in [name for name in cleared if _is_control_param(name)]:
        cleared.pop(parameter, None)

    chips = _active_filters(request, params)
    context: dict[str, Any] = {
        "page": page,
        "paginator": paginator,
        "total": paginator.count,
        "query": query,
        "query_string": query_without_page.urlencode(),
        "cleared_query": cleared.urlencode(),
        "has_any_filter": bool(chips or query),
        "source_instructions": source_instructions_for(page.object_list),
        "source_snapshot": snapshot_label(),
        # What the search box submits alongside `q`, so typing narrows the
        # chosen filters rather than silently widening the population.
        "carried_params": [
            (name, value)
            for name, value in params.items()
            if name not in {"q", "leht"} and value and not _is_control_param(name)
        ],
        # The «näita korraga» control. Each option carries the whole current
        # address minus the page number, so choosing a size keeps every filter
        # and lands on the first page of the same population rather than on a
        # page number the smaller size no longer has.
        "page_size": page_size_key,
        "page_size_options": _page_size_options(params, page_size_key),
        "status_options": _status_options(request, params),
        "active_filters": chips,
        # Offered in the narrowing panel as well as reachable from a link: a
        # dimension a figure can set and the panel cannot is one somebody can
        # arrive at and never reproduce (Stage-2E brief 38).
        "work_state_options": list(work_items.WORK_POPULATION_LABELS.items()),
        "filters": {
            "olek": status,
            "ulatus": scope,
            "q": query,
            "asutus": params.get("asutus", ""),
            "materjalid": params.get("materjalid", ""),
            "andmed": params.get("andmed", selectors.DATA_CLASS_ALL),
            **date_echo,
            "vastutaja": params.get("vastutaja", ""),
            "hetkeseis": params.get("hetkeseis", ""),
            "menetlusliik": params.get("menetlusliik", ""),
            "valdkond": params.get("valdkond", ""),
            "silt": params.get("silt", ""),
            "liik": params.get("liik", ""),
            "paritolu": params.get("paritolu", ""),
            "aasta": params.get("aasta", ""),
            "allikas": params.get("allikas", ""),
            "tegevus": params.get("tegevus", ""),
            "arvamus": params.get("arvamus", ""),
            "too": params.get("too", ""),
            "saatja": params.get("saatja", ""),
            "adressaat": params.get("adressaat", ""),
            "jarjestus": sort,
        },
        "nav_active": "teemad",
    }

    # Only on the full page. The chips sit above the filter bar, outside the
    # results region a keystroke swaps, and four extra counts per keystroke
    # would be four queries for something the reader cannot even see move
    # (Stage-2E.1 brief 14).
    context["saved_views"] = register_filters.saved_views(request.user, params)
    # The view *is* the address. «Salvesta praegune filter vaatena» offers this
    # link; there is nothing else to save, and nothing is stored.
    context["current_view_url"] = request.build_absolute_uri()

    if _wants_fragment(request):
        # The whole results surface, not a patched piece of it: one render from
        # one queryset cannot disagree with itself about how many rows there are
        # (the convention this module opens with).
        #
        # Returned before the filter-control options are built. Those populate
        # selects that are not in this fragment, and a keystroke must not pay
        # for a list of organisations nobody is going to see (brief 14).
        return render(request, "matters/partials/register_results.html", context)

    context |= {
        # A filter, not a chooser. `Vastutaja` here describes stored work, so
        # it offers the current department workers *and* everybody who actually
        # owns something in this register — a colleague who left with seventeen
        # files still open is precisely who somebody comes to this control
        # looking for, and the earlier narrowing left them reachable only by
        # typing a UUID into the address bar.
        #
        # Bounded by `visible_to`, and by that alone: the option list must not
        # name somebody who appears only on Matters this reader may not open.
        # Read before the register's own `vastutaja` filter, so selecting a name
        # does not reduce the select to that one name
        # (app/accounts/selectors.py `owner_filter_choices`, docs/adr/0036).
        "owners": owner_filter_choices(Matter.objects.visible_to(request.user)),
        "stages": StageVocabulary.objects.filter(is_active=True).order_by("sort_order"),
        "tracks": Track.choices,
        # The governed vocabulary, so the register's filter offers exactly what
        # Uus teema and the Teema header offer (app/taxonomy/vocabulary.py).
        "policy_areas": selectable_policy_areas(),
        "record_modes": RecordMode.choices,
        "origins": MatterOrigin.choices,
        "next_action_options": list(NEXT_ACTION_LABELS.items()),
        "opinion_options": list(OPINION_LABELS.items()),
        "material_options": sorted(MATERIAL_LABELS.items()),
        # Kõik first: it is the default, and the control should open on the
        # state the page is actually in.
        "data_class_options": [
            (selectors.DATA_CLASS_ALL, DATA_CLASS_LABELS[selectors.DATA_CLASS_ALL]),
            (selectors.DATA_CLASS_REAL, DATA_CLASS_LABELS[selectors.DATA_CLASS_REAL]),
            (selectors.DATA_CLASS_TEST, DATA_CLASS_LABELS[selectors.DATA_CLASS_TEST]),
        ],
        "chosen_organisation": _organisation_or_none(params.get("asutus", "")),
        "organisation_options": _organisation_options(""),
        # Who a row may be handed to, current reader first. The same population
        # the Matter header's own control offers, so the two cannot disagree
        # about who work may be given to (app/accounts/selectors.py, ADR 0036).
        "assignable_people": _assignable_first(request.user),
        # The row control is a write. Drawn by capability, enforced by the route
        # (app/core/decorators.py, `business_write_required`).
        "can_assign_owner": may_write_business_content(request.user),
    }

    # Arvamused, as this page's second section. Built after the fragment branch
    # above has already returned, so a keystroke in the register's search box
    # does not pay for an opinion list nobody is going to see — and the
    # section's own keystrokes go to its own route, never through here.
    #
    # Composed, not re-implemented: every population, every count and the whole
    # archive boundary come from the selectors the standalone Arvamused
    # workspace uses. This page decides where the section sits and nothing else
    # (app/submissions/embedded.py, docs/adr/0047).
    context |= opinions.embedded_context(request)

    return render(request, "matters/matter_list.html", context)


def _assignable_first(reader: Any) -> list[User]:
    """Everybody work may be given to, with the reader at the top.

    "(mina)" first because the commonest triage decision is *I will take this*,
    and a list that made somebody hunt for their own name in an alphabetical
    column would make the two-click gesture a three-click one.
    """
    people = list(assignable_business_users())
    return sorted(people, key=lambda person: (person.pk != getattr(reader, "pk", None),))


#: The register dimensions that name an institution, and what each is called on
#: screen. A parameter outside this mapping is a 404 rather than a field name
#: reflected back into the page.
ORGANISATION_CHOOSER_FIELDS = {
    "asutus": "Asutus",
    "saatja": "Algataja voi saatja",
    "adressaat": "Adressaat",
}

#: How many organisations the chooser offers at a time. Enough to scan, few
#: enough that the response stays small on a catalogue with hundreds of rows.
ORGANISATION_CHOICES = 20


@login_required
@business_write_required
@require_http_methods(["POST"])
def assign_owner(request: HttpRequest, pk: Any) -> HttpResponse:
    """Give an unassigned Matter an owner, from the row it was read on.

    Nothing is done differently here from the header's own owner control: the
    same form validates the choice against `assignable_including`, and the same
    `assign_matter` service writes it, moves the open step that was following
    the previous owner, and records the change event. What is different is only
    where the reader ends up — back on the register, with their filters intact,
    because triaging four unassigned files should not cost four round trips
    through four Matter pages (app/matters/services.py, docs/adr/0036).
    """
    matter = get_visible_matter(request, pk)
    form = MatterFieldForm(request.POST, matter=matter)
    if not form.is_valid():
        messages.error(request, "Vigane väärtus.")
        return redirect(_safe_next(request) or reverse("matters:matter_list"))

    try:
        assign_matter(matter=matter, owner=form.cleaned_data.get("owner"), actor=request.user)
    except DomainError as error:
        messages.error(request, str(error))
    else:
        matter.refresh_from_db()
        messages.success(
            request,
            f"«{matter.title}» vastutaja on {matter.owner.get_short_name()}."
            if matter.owner
            else f"«{matter.title}» on nüüd vastutajata.",
        )

    return redirect(_safe_next(request) or reverse("matters:matter_list"))


def _organisation_options(term: str) -> list[Organisation]:
    """Organisations whose name or recorded alias matches what was typed.

    The canonical catalogue, ordered by name and carrying no counts.
    Deliberately not ordered by usage and deliberately not narrowed to bodies
    that appear on Matters this reader may see: either would make the *order* or
    the *membership* of this list a statement about restricted work
    (Stage-2E.1 brief 13).

    ``Organisation`` is shared reference data that the existing pickers already
    render in full, so listing it here discloses nothing new. Aliases
    participate because they are reviewed data — searching ``MKM`` should find
    the ministry filed under its full name (master specification 14.7).
    """
    catalogue = Organisation.objects.order_by("name")
    text = term.strip()
    if text:
        catalogue = catalogue.filter(
            Q(name__icontains=text) | Q(aliases__alias__icontains=text)
        ).distinct()
    return list(catalogue[:ORGANISATION_CHOICES])


def _organisation_or_none(raw: str) -> Organisation | None:
    """The chosen body, so the chooser keeps showing it after a search."""
    organisation: Organisation | None = _named_by_pk(Organisation, raw)
    return organisation


@login_required
def organisation_choices(request: HttpRequest) -> HttpResponse:
    """The searchable organisation control, re-rendered for what was typed.

    A server-backed chooser rather than a select carrying every institution: the
    real catalogue runs to hundreds, and the alternative the brief rules out — a
    wall of radio buttons — is unusable at that size. No frontend library is
    introduced; this is one HTMX swap of one labelled ``<select>`` (brief 13).
    """
    field = request.GET.get("vali", "asutus")
    if field not in ORGANISATION_CHOOSER_FIELDS:
        raise Http404("Tundmatu vali.")
    term = request.GET.get(f"{field}_otsing", "")
    return render(
        request,
        "matters/partials/organisation_choices.html",
        {
            "field": field,
            "field_label": ORGANISATION_CHOOSER_FIELDS[field],
            "term": term,
            "organisation_options": _organisation_options(term),
            "chosen_organisation": _organisation_or_none(request.GET.get(field, "")),
        },
    )


# ---------------------------------------------------------------------------
# Creating a Teema
# ---------------------------------------------------------------------------


@login_required
@business_write_required
@require_http_methods(["GET", "POST"])
def matter_create(request: HttpRequest) -> HttpResponse:
    """Create a Matter, with the files it arrived with.

    A matter arrives as a title, a document and a person, so all three are
    captured in one step. The files are read and validated *before* anything is
    written: one rejected attachment must not leave a Matter behind carrying the
    other three, which is the failure mode the intake surface already avoids
    (Stage-2E.1 brief 23).
    """
    form = MatterCreateForm(request.POST or None, viewer=request.user)
    # Bound only when somebody actually asked for a next action. Bound
    # unconditionally, a refused save — a missing title, a rejected file —
    # re-rendered the optional Järgmine tegevus block with "See lahter on
    # nõutav." under fields nobody had touched, and opened the disclosure to
    # show them. That reads as "this is mandatory after all", which is the one
    # thing the block must not say (specification 3.8, Agent-UI brief 9.6).
    #
    # **Either half counts.** Reading `next-text` alone was right while the date
    # arrived pre-filled with today, because then a date was never evidence of
    # anything. It is blank now (ADR 0052 §5), so somebody who pressed `Homme`
    # and forgot to write the sentence has plainly asked for a next step — and
    # under the old signal their date would have been silently dropped and the
    # Teema created without it. The form refuses it instead, on the box that is
    # empty.
    #
    # It stays one definition, used both to bind the form and to decide below
    # whether to call the service.
    wants_action = any(
        (request.POST.get(key) or "").strip() for key in ("next-text", "next-target_date")
    )
    action_form = NextActionForm(request.POST if wants_action else None, prefix="next")
    uploads: list[Any] = []
    upload_error = ""

    if request.method == "POST" and form.is_valid():
        try:
            uploads = _read_new_matter_files(request)
        except (DomainError, UploadRejected) as error:
            upload_error = str(error)

        if upload_error or (wants_action and not action_form.is_valid()):
            if upload_error:
                messages.error(request, upload_error)
            return render(
                request,
                "matters/matter_create.html",
                _create_context(request, form, action_form),
                status=400,
            )

        data = form.cleaned_data
        try:
            with transaction.atomic():
                # Resolved *inside* the transaction, and before the Matter, so a
                # brand-new institution and the Teema that names it are one
                # write. A rejected attachment, a refused next action or any
                # other late failure below takes the institution with it rather
                # than leaving an orphan in the catalogue nobody asked for
                # (§6, app/matters/services.py `resolve_addressee`).
                addressee = resolve_addressee(
                    chosen=data.get("addressee_organisation"),
                    typed_name=data.get("addressee_name") or "",
                )
                matter = create_matter(
                    title=data["title"],
                    actor=request.user,
                    owner=data.get("owner"),
                    # Written on the record, not into an Entry. `brief_summary`
                    # answers *what is this*, which no Entry, no position and no
                    # rationale can be made to mean without corrupting it
                    # (app/matters/models.py, Teema redesign §6).
                    brief_summary=data.get("brief_summary") or "",
                    stage=data.get("stage"),
                    track=data.get("track") or "",
                    source_organisations=list(data.get("source_organisations") or []),
                    addressee_organisation=addressee,
                    received_date=data.get("received_date"),
                    response_deadline=data.get("response_deadline"),
                    policy_areas=list(data.get("policy_areas") or []),
                    policy_area_other=data.get("policy_area_other") or "",
                    # Decided here, never read from the form. The control is gone
                    # from the page and an omitted field must not become a blank
                    # value the model would refuse (brief 21).
                    visibility=Visibility.NORMAL,
                    # Unlike visibility, this one *is* on the page — as one
                    # checkbox, unticked. The form turns it into the stored
                    # vocabulary; the service validates it and refuses TEST on
                    # anything not created here (Agent-C brief 15, 16, 17).
                    data_class=form.data_class,
                )
                for upload in uploads:
                    _attach_incoming_file(matter, upload, actor=request.user)

                # The private scratch pad, through its own service, and only when
                # something was typed. An empty note would create a row recording
                # that somebody wrote nothing — the same reason an untouched
                # Järgmine tegevus creates no NextAction below
                # (app/matters/services.py, `save_personal_note`).
                note = (data.get("notes") or "").strip()
                if note:
                    save_personal_note(matter=matter, author=request.user, body=note)

                if wants_action:
                    # The owner is chosen on this same form, so the Matter does not
                    # exist yet when the action form is read and the service's own
                    # fallback to `matter.owner` has nothing to fall back to. Handed
                    # in explicitly, and only as a default: an explicit choice in
                    # the action form still wins (app/matters/forms.py).
                    set_next_action_for_new_work(
                        matter=matter,
                        actor=request.user,
                        **action_form.as_service_kwargs(default_responsible=data.get("owner")),
                    )

        except DomainError as error:
            # An ambiguous typed addressee, or any other rule the services
            # refuse. The transaction is already rolled back by the time
            # this runs, so nothing — least of all a newly created
            # institution — survives the refusal (§6).
            form.add_error(None, str(error))
            return render(
                request,
                "matters/matter_create.html",
                _create_context(request, form, action_form),
                status=400,
            )

        if uploads:
            messages.success(
                request,
                f"Teema „{matter.title}” on loodud koos {len(uploads)} failiga.",
            )
        else:
            messages.success(request, f"Teema „{matter.title}” on loodud.")
        # Straight into the file: creation is the start of work, not the end.
        return redirect("matters:matter_detail", pk=matter.pk)

    # A refused save answers 400, the same as a rejected upload and a malformed
    # `Järgmiseks` a few lines above. The form itself failing validation used to
    # answer 200, which made the three refusals on one page indistinguishable to
    # anything reading the status rather than the HTML.
    status = 400 if request.method == "POST" else 200
    return render(
        request,
        "matters/matter_create.html",
        _create_context(request, form, action_form),
        status=status,
    )


def _create_context(request: HttpRequest, form: Any, action_form: Any) -> dict[str, Any]:
    return {
        "form": form,
        "action_form": action_form,
        "frequent_senders": getattr(form, "frequent_senders", []),
        # `secondary_fields` is gone with the disclosure it fed. The template
        # named the primary fields and looped this tuple for the rest, which was
        # the right shape while the rest were hidden behind "+ Täpsusta teema
        # andmeid". Nothing on the page is hidden now, so every field is placed
        # by name — and a field added to the form later has to be placed
        # deliberately rather than appearing in a panel nobody opened
        # (Uus teema redesign §3).
        "today": timezone.localdate(),
        # `Millal?`, resolved on the server in Europe/Tallinn — the same list
        # and the same helper the Teema composer's chips are built from. Doing
        # the arithmetic in the browser instead would answer in the reader's
        # own timezone, and doing it twice would let the two surfaces drift
        # (`quick_date_choices`, ADR 0052 §4).
        "quick_dates": quick_date_choices(timezone.localdate()),
        "nav_active": "teemad",
    }


def _read_new_matter_files(request: HttpRequest) -> list[Any]:
    """Read and validate every attachment before a single row is written.

    Reading is what validates: `read_upload` enforces the size, the MIME type
    and the signature rules the rest of the system already relies on. Doing all
    of it up front is the whole point — a Matter created with three of four
    files, and an error message about the fourth, is worse than no Matter.
    """
    from app.documents.uploads import read_upload

    files = request.FILES.getlist("files")
    return [read_upload(handle) for handle in files if handle]


def _attach_incoming_file(matter: Any, upload: Any, *, actor: Any) -> None:
    """One uploaded file as one Document with one immutable version.

    Through the ordinary services, so a file arriving with a new Matter is
    subject to the same evidence rules as one uploaded later: same storage, same
    checksum, same immutability trigger, same scan state. Nothing is inferred
    from the filename — not a stage, not a submission, not a date.
    """
    from app.documents.enums import DocumentRole
    from app.documents.services import add_evidence_version, create_document

    document = create_document(
        matter=matter,
        title=upload.filename,
        role=DocumentRole.INCOMING_AUTHORITY,
        created_by=actor,
    )
    add_evidence_version(
        document=document,
        content=upload.content,
        original_filename=upload.filename,
        mime_type=upload.mime_type,
        uploaded_by=actor,
    )


# ---------------------------------------------------------------------------
# The Matter page
# ---------------------------------------------------------------------------


def _timeline_filter(request: HttpRequest) -> str:
    """Which slice of the chronology the reader asked for.

    A query-string value, like every other filter in this product, so a
    filtered chronology is a link somebody can send. An unknown value falls
    back to everything rather than to nothing: a hand-edited URL should show
    too much, never silently hide the file's history.
    """
    value = (request.GET.get("ajajoon") or "").strip()
    known = {key for key, _label in TIMELINE_FILTERS}
    return value if value in known else TIMELINE_FILTER_ALL


def _overview_context(request: HttpRequest, matter: Matter) -> dict[str, Any]:
    timeline_only = _timeline_filter(request)
    items, has_more = matter_timeline(
        matter=matter, user=request.user, limit=TIMELINE_PAGE_SIZE, only=timeline_only
    )
    engagements = selectors.matter_engagements(matter, request.user)
    current_action = current_next_action(matter)
    # The register's own `JÄRGMISEKS`, and only where no structured action
    # exists. Read here rather than in the template so the page cannot start
    # asking the database a question of its own — and read *conditionally*,
    # because on a Matter that has a real next step neither answer is rendered
    # and both are a query for nothing (ADR 0021).
    source_instruction = "" if current_action else source_instruction_for(matter)
    return {
        "matter": matter,
        "current_action": current_action,
        "source_instruction": source_instruction,
        "source_snapshot": snapshot_label() if source_instruction else "",
        "timeline_items": items,
        # What the spine renders: the same items, with adjacent system events
        # folded into one row each. The flat list stays beside it because the
        # closed summary counts lines rather than rows (app/matters/timeline.py).
        "timeline_rows": collapse_system_runs(items),
        # The last thing a colleague actually wrote, for the closed summary. A
        # system row would quote the application back at the reader.
        "timeline_preview": latest_authored(items),
        "timeline_has_more": has_more,
        "timeline_count": len(items) + (1 if has_more else 0),
        "timeline_only": timeline_only,
        "timeline_filters": TIMELINE_FILTERS,
        "composer_form": ComposerForm(matter=matter, viewer=request.user),
        # `summary_form` and `note_form` are deliberately absent: the header
        # context carries them, it is merged over this one, and reading the
        # private note twice per page is two queries for one answer.
        "historical": _historical_context(matter, request.user),
        # Stage 2G's structured facts. Read through their own selector, which
        # scopes them like every other child record.
        "intelligence": matter_intelligence(matter, request.user),
        # `Kaasamine`. Read here for the same reason as everything else on this
        # dict: the template must not be able to start querying.
        "engagements": engagements,
        # What the register observed *around* the outreach: how many members
        # were asked and how many answered, whether the opinion went out, and
        # whether KELLELE named more bodies than the canonical field can hold.
        #
        # Decided in Python because a template cannot tell `None` from `0`, and
        # that distinction is the whole point of the two counts: `{{ value|
        # default:"—" }}` renders a measured zero as a missing one
        # (`register_display.MemberFeedback`).
        "register_facts": register_facts_for(matter),
        # `(record, is_editing)`. One bound form is shared by the add form and
        # every row's edit form, so exactly one of them may render the rejected
        # values — and the row's disclosure and the fields inside it have to
        # agree about which. Decided here rather than compared twice in the
        # template (Kaasamine one-click §7).
        "engagement_rows": [(record, False) for record in engagements],
        "engagement_count": len(engagements),
        "engagement_form": EngagementForm(),
        "engagement_error": "",
        "engagement_editing": None,
        # Collapsed by default, and open on the render that follows a write.
        # Adding a consultation and watching the section it went into fold shut
        # is the one moment a reader needs to see the list (Teema redesign §14).
        "engagement_open": False,
        # The add form's own state, separate from the section's. On a Matter
        # with records it is a disclosure that stays shut until asked for; on
        # one with none there is no disclosure at all and opening the section
        # is the whole gesture. Either way a *refused* add reopens it, and it
        # cannot key on `engagement_error`: a field error — an empty title, an
        # unreadable date — leaves that string empty, which folded the
        # explanation shut on exactly the saves that needed explaining
        # (Kaasamine one-click §3, §7).
        "engagement_add_open": False,
        "can_write": may_write_business_content(request.user),
        "can_review_victory": may_review_work_victory(request.user),
        # «Lükka edasi», with the day each option lands on. Offered only on an
        # exact date: deferring a step recorded as *september 2026* by a day
        # would turn a period somebody deliberately left vague into a day they
        # never named (master specification 3.5).
        "defer_choices": defer_choices(defer_base(current_action, timezone.localdate())),
        "quick_dates": quick_date_choices(timezone.localdate()),
        "can_defer": current_action is not None and not current_action.is_approximate,
        "today": timezone.localdate(),
    }


@login_required
def matter_detail(request: HttpRequest, pk: Any) -> HttpResponse:
    matter = get_visible_matter(request, pk)
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
    return render(request, "matters/matter_detail.html", context)


def _header_context(
    request: HttpRequest, matter: Matter, *, milestones: Any = None
) -> dict[str, Any]:
    return {
        "matter": matter,
        # No `submission_count`. The tab that displayed it is gone, and a count
        # nothing renders is a query nothing needs.
        "document_count": Document.objects.filter(matter=matter).visible_to(request.user).count(),
        "dispositions": Disposition.choices,
        # The inline owner control. Current department workers plus this
        # Matter's own owner, so a file held by a departed colleague still says
        # who holds it and can still be handed to somebody who is here — the
        # same population `MatterFieldForm` validates against, because a select
        # offering more than the form accepts is a save that fails on submit
        # (app/accounts/selectors.py).
        "owners": assignable_including(matter.owner),
        "stages": StageVocabulary.objects.filter(is_active=True).order_by("sort_order"),
        "organisations": Organisation.objects.order_by("name"),
        # Resolved once here rather than read off the Matter inside the loop
        # over every organisation: the sender checkboxes iterate the whole
        # reference table, and a membership test that re-queried per row would
        # be an N+1 nobody notices until the institution list grows.
        "selected_sender_ids": matter.source_organisation_ids,
        "tracks": Track.choices,
        "visibilities": Visibility.choices,
        # No `current_action` either. The header band no longer shows the next
        # step — the Järgmiseks row does — and the overview context reads it
        # once for both.
        # The governed vocabulary plus whatever this Matter already carries, so
        # a file classified years ago under a retired area still shows it and
        # can still be corrected (app/taxonomy/vocabulary.py).
        "policy_area_choices": selectable_policy_areas(),
        "selected_policy_area_ids": {area.pk for area in matter.policy_areas.all()},
        "matter_policy_areas": list(matter.policy_areas.all()),
        # The one deadline the header shows, chosen by the rule in §5.5 rather
        # than by the template picking whichever field is non-empty.
        # `milestones` when the caller has already read them, which the Matter
        # page has: `Olulised tähtajad` renders from the same rows.
        "active_deadline": selectors.active_deadline(matter, request.user, milestones=milestones),
        "summary_form": BriefSummaryForm(initial={"brief_summary": matter.brief_summary}),
        # The rail travels with the header — it is on all three Matter surfaces
        # — so the private note and the write flag are read here rather than
        # three times over.
        # Prefixed, because the composer's own field is called `body` too and
        # two `id="id_body"` on one page break every `for=` on both of them —
        # the composer's textarea was announcing itself as "Tegevuse kirjeldus
        # Märkmed".
        "note_form": PersonalNoteForm(
            prefix=NOTE_PREFIX,
            initial={"body": personal_note_for(matter=matter, author=request.user)},
        ),
        "can_write": may_write_business_content(request.user),
        # The rail renders on every Matter surface, so what the rail reads is
        # read here rather than three times over.
        "opinion_documents": opinion_documents(matter, viewer=request.user),
        "today": timezone.localdate(),
    }


@login_required
def matter_position(request: HttpRequest, pk: Any) -> HttpResponse:
    """The retired per-Matter `Arvamused` address, kept so bookmarks still work.

    What this page was is now two things that already existed. An opinion is a
    file, so it is a row on **Dokumendid** — badged `Arvamus`, carrying the date
    it went out and who it went to, with the send's own details and
    `Võta tagasi` behind the row's `⋯`. Finding an opinion *across* the
    department is still the Arvamused workspace at `/arvamused/`, which this
    change does not touch (docs/adr/0047, docs/adr/0060).

    What it was in between those two was a third copy of the same letter, under
    a heading that repeated the rail above it, with the file's checksum, its
    byte size and the archive importer's match reasoning printed under every
    row — and `Võta tagasi` as the most prominent control on a reading surface.

    **A redirect and not a 404**, because the contract this route owes is that
    an old link still reaches the Matter's opinion material — not that a page
    keeps existing. It lands on Dokumendid filtered to `Arvamus`, so a reader
    who saved this address to see one Matter's opinions still opens exactly
    that; the drafts block and `Seotud arhiivikirjad` render there regardless of
    the filter, so nothing the old page listed is missing from where it lands.

    **Authorization first.** `get_visible_matter` runs before anything is
    reversed, so an unauthorized caller gets the same 404 they always did rather
    than a redirect that confirms the Matter exists — the whole reason that
    helper answers 404 instead of 403.

    Temporary rather than permanent, deliberately: a 301 is cached by the
    browser until it is cleared, and this address should keep passing through a
    view that checks who is asking.
    """
    matter = get_visible_matter(request, pk)
    return redirect(opinions_url(matter))


def opinions_url(matter: Any, *, anchor: str = "") -> str:
    """Dokumendid, showing this Matter's opinions — the one place they live now.

    Every route that used to end on the retired surface ends here: the
    compatibility redirect, the four Submission write actions, and a Submission
    search result. Built in one function so `?roll=` and the anchor cannot drift
    apart across six call sites.

    ``anchor`` is the id of the thing the caller changed, and the caller decides
    which one: `dokument-<uuid>` for a sent opinion, whose file row this filter
    renders, and `arvamus-<uuid>` for a draft, whose row is in the `Arvamused`
    block and is *not* in the filtered table. Passing the wrong one is how a
    redirect lands on an anchor that is not on the page
    (`app/submissions/views.py`).

    Anchors are only ever built from identities the caller resolved under the
    reader's own scope, so one can never name something the page will not
    render.
    """
    page = reverse("matters:matter_documents", kwargs={"pk": matter.pk})
    url = f"{page}?roll={OPINION_ROLE_FILTER}"
    return f"{url}#{anchor}" if anchor else url


def _historical_context(matter: Any, user: Any) -> dict:
    """Historical source material for a Matter, or nothing at all.

    Imported lazily: `app.legacy_import` imports the matters app, and doing this
    at module scope closes the circle. A Matter with no OneNote history gets an
    empty dict and the templates render nothing — a heading over an empty list
    reads as a data-quality problem rather than as an absence (Stage-2D 35).
    """
    from app.legacy_import.historical_views import historical_summary

    return historical_summary(matter, user)


#: How many evidence rows the Dokumendid table renders before it offers
#: "Näita rohkem". A file with forty documents is real; forty rows above the
#: fold is not what somebody opening the tab is looking for.
DOCUMENT_PAGE_SIZE = 12


def _role_filter_choices() -> list[tuple[str, str]]:
    """The Roll filter's vocabulary, with `Arvamus` where the stored role was.

    `KODA_SUBMISSION_FINAL` is substituted rather than added beside, for two
    reasons. It is an implementation label and a lawyer should never read one;
    and it is the *narrower* of the two questions — it cannot express "…or the
    exact file of a sent opinion", which is the other half of what an opinion is
    (`app/submissions/opinions.py`). Offering both would put two options on the
    menu that look like synonyms and are not.

    Substituted in place rather than moved to the front: the rest of the list
    keeps the order it has always had, and reordering a menu is how somebody's
    muscle memory picks the wrong filter.
    """
    return [
        (OPINION_ROLE_FILTER, "Arvamus")
        if value == DocumentRole.KODA_SUBMISSION_FINAL
        else (value, label)
        for value, label in DocumentRole.choices
    ]


def _upload_role_choices() -> list[tuple[str, str]]:
    """The same relabelling for the upload panel, over the *stored* vocabulary.

    The filter may invent a value because it only has to survive a round trip
    through the query string. This select posts a `Document.role`, so every
    value here is a real one and only the words change — which is the whole of
    what this change does to the role: the user reads `Arvamus`, the database
    keeps `KODA_SUBMISSION_FINAL`, and no migration is involved (docs/adr/0060).
    """
    return [
        (value, "Arvamus" if value == DocumentRole.KODA_SUBMISSION_FINAL else label)
        for value, label in DocumentRole.choices
    ]


@login_required
def matter_documents(request: HttpRequest, pk: Any) -> HttpResponse:
    """The file workspace: immutable evidence, then living working references.

    The two are queried together and split in Python — one query, one ordering
    — because `has_working_document` is a property of the row rather than a
    column, and the tab has to be able to say how many of each there are before
    it decides what to render.

    Filename search, role and year are ordinary query-string filters, like the
    register's: a filtered view is a link somebody can send.

    **This is also where a Matter's opinions live** since the separate
    per-Matter Arvamused page was retired. An opinion is a file, so it is a row
    in this table with an `Arvamus` badge, the date it went out and who it went
    to; the send's own details and every write action sit behind that row's `...`
    and in the collapsed `Arvamused` block under the table. What the page does
    *not* do is print the evidence mechanics again — the checksum, the importer's
    match reasoning, the version and the size a second time — which is what made
    the retired surface a third copy of the same letter (docs/adr/0060).
    """
    matter = get_visible_matter(request, pk)
    documents = (
        Document.objects.filter(matter=matter)
        .visible_to(request.user)
        .select_related("current_version", "created_by")
        .prefetch_related("versions")
        .order_by("-created_at")
    )

    term = (request.GET.get("otsi") or "").strip()
    role = (request.GET.get("roll") or "").strip()
    year = (request.GET.get("aasta") or "").strip()

    # Which documents are the Chamber's opinion, asked once for the whole page.
    # The badge, the filter and the per-row send all read this one answer, so no
    # arrangement of them can disagree about what an opinion is.
    opinion_ids = opinion_document_ids(matter, viewer=request.user)
    sends = sent_submission_by_document(matter, viewer=request.user)

    # A saved `?roll=KODA_SUBMISSION_FINAL` link keeps working and is read as
    # the union rather than as the bare role. It returns a strict superset of
    # what it used to — everything the role matched, plus the sent opinions
    # whose file was never reclassified — which is what somebody who saved that
    # link was looking for, and it leaves the menu below agreeing with the URL
    # instead of showing «Roll — kõik» over an active filter.
    if role == DocumentRole.KODA_SUBMISSION_FINAL:
        role = OPINION_ROLE_FILTER

    if term:
        documents = documents.filter(
            Q(title__icontains=term) | Q(current_version__original_filename__icontains=term)
        )
    if role == OPINION_ROLE_FILTER:
        documents = documents.filter(pk__in=opinion_ids)
    elif role in DocumentRole.values:
        documents = documents.filter(role=role)
    if year.isdigit():
        documents = documents.filter(created_at__year=int(year))

    rows = list(documents)
    evidence = [document for document in rows if not document.has_working_document]
    working = [document for document in rows if document.has_working_document]
    show_all = request.GET.get("koik") == "1"
    visible_evidence = evidence if show_all else evidence[:DOCUMENT_PAGE_SIZE]

    for document in visible_evidence:
        # Resolved per row here rather than in the template, so the page cannot
        # start asking the database a question of its own inside a loop.
        document.is_opinion = document.pk in opinion_ids
        document.opinion_send = sends.get(document.pk)
        document.role_label = (
            "Arvamus"
            if document.role == DocumentRole.KODA_SUBMISSION_FINAL
            else document.get_role_display()
        )

    # Opinion files this Matter holds that no canonical Submission accounts for.
    # They are the candidates for «Registreeri saatmine», and the reason that
    # control exists at all: uploading a file as `Arvamus` records that Koda has
    # it, never that Koda sent it, and only a person can close that gap (§18).
    unregistered = [
        document
        for document in opinion_documents(matter, viewer=request.user)
        if document.current_version_id and document.pk not in sends
    ]
    drafts = open_drafts(matter, viewer=request.user)

    # Historical letters already filed onto this Matter. Imported lazily for the
    # same reason `_historical_context` is: `app.legacy_import` imports the
    # matters app, and a module-level import here would close the circle.
    from app.legacy_import.opinion_links import archive_letters_for_matter

    context = _header_context(request, matter)
    context.update(
        {
            "tab": "dokumendid",
            "nav_active": "teemad",
            "evidence_documents": visible_evidence,
            "evidence_total": len(evidence),
            "evidence_hidden": max(len(evidence) - len(visible_evidence), 0),
            "working_documents": working,
            "document_roles": _role_filter_choices(),
            "upload_roles": _upload_role_choices(),
            "opinion_role_filter": OPINION_ROLE_FILTER,
            # Only the years this Matter actually has files from. A dropdown
            # offering ten empty years is a dropdown that teaches people the
            # filter does not work.
            "document_years": sorted({document.created_at.year for document in rows}, reverse=True),
            "document_filters": {"otsi": term, "roll": role, "aasta": year},
            "document_filters_active": bool(term or role or year),
            "working_document_form": WorkingDocumentForm(),
            "can_write": may_write_business_content(request.user),
            "historical": _historical_context(matter, request.user),
            # The opinion management block under the table. Compact, collapsed
            # unless a draft is waiting for somebody, and never a second listing
            # of the sent opinions already in the table above it.
            "opinion_drafts": drafts,
            "unregistered_opinions": unregistered,
            "submission_form": SubmissionCreateForm(prefix=CREATE_PREFIX),
            "register_form": RegisterSentOpinionForm(
                prefix=REGISTER_PREFIX, documents=unregistered
            ),
            # Two different kinds of record, and the weaker one is kept visually
            # apart from the file table. A canonical Submission says Koda sent an
            # opinion; an archive letter says we hold a file somebody judged to
            # concern this Matter, with no date or recipient promoted to a
            # canonical fact. `archive_letters_for_matter` asks `may_read_archive`
            # itself and answers with an empty list where it must, so a reader
            # without the corpus gets no rows, no count and no hint there are any
            # (docs/adr/0028, docs/adr/0056).
            "archive_letters": archive_letters_for_matter(matter, reader=request.user),
        }
    )
    return render(request, "matters/matter_documents.html", context)


# ---------------------------------------------------------------------------
# HTMX actions
# ---------------------------------------------------------------------------


def _render_overview(
    request: HttpRequest,
    matter: Matter,
    status: int = 200,
    *,
    engagement_open: bool = False,
) -> HttpResponse:
    """Re-render the whole overview column.

    One render from one set of queries, so `Järgmiseks` and the timeline can
    never show different pictures of the same save.
    """
    context = _overview_context(request, matter)
    intelligence = context["intelligence"]
    context.update(
        _header_context(
            request,
            matter,
            milestones=[*intelligence.upcoming_dates, *intelligence.past_dates],
        )
    )
    context["engagement_open"] = engagement_open
    return render(request, "matters/partials/overview.html", context, status=status)


@login_required
@business_write_required
@require_http_methods(["POST"])
def compose(request: HttpRequest, pk: Any) -> HttpResponse:
    """The unified composer save. Entry and `Järgmiseks` land together."""
    matter = get_visible_matter(request, pk)
    form = ComposerForm(request.POST, request.FILES, matter=matter, viewer=request.user)

    if not form.is_valid():
        context = _overview_context(request, matter)
        context.update(_header_context(request, matter))
        context["composer_form"] = form
        return render(request, "matters/partials/overview.html", context, status=400)

    try:
        compose_update(matter=matter, author=request.user, **form.as_service_kwargs())
    except (DomainError, UploadRejected) as error:
        context = _overview_context(request, matter)
        context.update(_header_context(request, matter))
        context["composer_form"] = form
        context["composer_error"] = str(error)
        return render(request, "matters/partials/overview.html", context, status=400)

    matter.refresh_from_db()
    return _render_overview(request, matter)


@login_required
@business_write_required
@require_http_methods(["POST"])
def add_engagement_view(request: HttpRequest, pk: Any) -> HttpResponse:
    """Record one `Kaasamine` on this Matter."""
    matter = get_visible_matter(request, pk)
    form = EngagementForm(request.POST)
    if not form.is_valid():
        return _overview_with_engagement_error(request, matter, form)

    try:
        add_engagement(
            matter=matter,
            kind=form.cleaned_data["kind"],
            title=form.cleaned_data["title"],
            url=form.cleaned_data.get("url") or "",
            note=form.cleaned_data.get("note") or "",
            occurred_on=form.cleaned_data.get("occurred_on"),
            actor=request.user,
        )
    except DomainError as error:
        return _overview_with_engagement_error(request, matter, form, str(error))

    return _render_overview(request, matter, engagement_open=True)


@login_required
@business_write_required
@require_http_methods(["POST"])
def update_engagement_view(request: HttpRequest, pk: Any, engagement_id: Any) -> HttpResponse:
    """Correct one `Kaasamine`. There is no delete; a wrong row is edited."""
    matter = get_visible_matter(request, pk)
    # Scoped through the child's own `visible_to`, not fetched by id off the
    # Matter: a record may carry a stricter visibility override than its parent,
    # and reading it any other way would bypass that.
    engagement = get_object_or_404(
        MatterEngagement.objects.visible_to(request.user).filter(matter=matter), pk=engagement_id
    )

    form = EngagementForm(request.POST)
    if not form.is_valid():
        return _overview_with_engagement_error(request, matter, form, editing=engagement.pk)

    try:
        update_engagement(
            engagement=engagement,
            kind=form.cleaned_data["kind"],
            title=form.cleaned_data["title"],
            url=form.cleaned_data.get("url") or "",
            note=form.cleaned_data.get("note") or "",
            occurred_on=form.cleaned_data.get("occurred_on"),
            actor=request.user,
        )
    except DomainError as error:
        return _overview_with_engagement_error(
            request, matter, form, str(error), editing=engagement.pk
        )

    return _render_overview(request, matter, engagement_open=True)


def _overview_with_engagement_error(
    request: HttpRequest,
    matter: Matter,
    form: EngagementForm,
    error: str = "",
    editing: Any = None,
) -> HttpResponse:
    """Re-render the column with the bound form, so nothing typed is lost."""
    context = _overview_context(request, matter)
    context.update(_header_context(request, matter))
    context["engagement_form"] = form
    context["engagement_error"] = error
    context["engagement_editing"] = editing
    context["engagement_rows"] = [
        (record, editing is not None and record.pk == editing) for record in context["engagements"]
    ]
    # The section always, and the add form only when the add is what failed:
    # a refused *edit* belongs in the row it came from, and opening the composer
    # beside it would offer a second, empty answer to the same refusal.
    context["engagement_open"] = True
    context["engagement_add_open"] = editing is None
    return render(request, "matters/partials/overview.html", context, status=400)


@login_required
@business_write_required
@require_http_methods(["POST"])
def set_action(request: HttpRequest, pk: Any) -> HttpResponse:
    matter = get_visible_matter(request, pk)
    form = NextActionForm(request.POST)

    if not form.is_valid():
        context = _overview_context(request, matter)
        context.update(_header_context(request, matter))
        context["action_form"] = form
        return render(request, "matters/partials/overview.html", context, status=400)

    try:
        set_next_action_for_new_work(matter=matter, actor=request.user, **form.as_service_kwargs())
    except DomainError as error:
        context = _overview_context(request, matter)
        context.update(_header_context(request, matter))
        context["composer_error"] = str(error)
        return render(request, "matters/partials/overview.html", context, status=400)

    return _render_overview(request, matter)


def _next_action_row_context(request: HttpRequest, matter: Matter) -> dict[str, Any]:
    """Everything `next_action_row.html` reads, and nothing else.

    A deliberately small slice of `_overview_context`. The Järgmiseks row is the
    one surface that re-renders on its own, and building the whole overview to
    answer it would run the timeline, the engagement list and the intelligence
    selectors for a fragment that shows none of them.
    """
    current_action = current_next_action(matter)
    source_instruction = "" if current_action else source_instruction_for(matter)
    return {
        "matter": matter,
        "current_action": current_action,
        "source_instruction": source_instruction,
        "source_snapshot": snapshot_label() if source_instruction else "",
        "can_write": may_write_business_content(request.user),
        "can_defer": current_action is not None and not current_action.is_approximate,
        "defer_choices": defer_choices(defer_base(current_action, timezone.localdate())),
    }


@login_required
@business_write_required
@require_http_methods(["POST"])
def complete_action(request: HttpRequest, pk: Any, action_id: Any) -> HttpResponse:
    """`✓ Tehtud` — the step is done, and that is the whole save.

    **It writes no entry.** The completion is already evidence: the canonical
    `NEXT_ACTION_COMPLETED` event says who finished what and when, and the
    action itself stays in the history. Manufacturing a note that reads
    "Helistasin ministeeriumisse" would be the application writing a lawyer's
    record for them. Something worth recording goes in the composer, in their
    own words, and that is a separate save (ADR 0052 §7).

    **It swaps the Järgmiseks row and nothing else.** This used to re-render
    `#teema-vaade`, which is the row *and the open composer under it* — so
    finishing a step threw away every unsaved character somebody had typed
    about it, which is exactly the moment they are most likely to be typing.
    The write is persisted before the response either way; the chronology
    catches up on the next render of the page (ADR 0052 §8, §9).
    """
    matter = get_visible_matter(request, pk)
    action = get_object_or_404(
        NextAction.objects.visible_to(request.user), pk=action_id, matter=matter
    )
    try:
        complete_next_action(action=action, actor=request.user)
    except DomainError as error:
        # The refusal comes back inside the row, because the row is what the
        # response replaces. Rendering the whole overview into a target that
        # holds one row would nest the page inside itself.
        context = _next_action_row_context(request, matter)
        context["next_action_error"] = str(error)
        return render(request, "matters/partials/next_action_row.html", context, status=400)
    context = _next_action_row_context(request, matter)
    return render(request, "matters/partials/next_action_row.html", context)


@login_required
@business_write_required
@require_http_methods(["POST"])
def review_action(request: HttpRequest, pk: Any, action_id: Any) -> HttpResponse:
    """Record that a WAIT or MONITOR was checked, and when to check again.

    Reviewing is not completing: the Matter is still waiting on the same thing,
    so the action keeps its identity and only its review date moves.
    """
    matter = get_visible_matter(request, pk)
    action = get_object_or_404(
        NextAction.objects.visible_to(request.user), pk=action_id, matter=matter
    )
    # The box beside "Vaatasin üle" is the Estonian date control like every
    # other one, so `7.9.2026` has to reach here as a date. ISO still parses:
    # this route was posted to with ISO before the control changed.
    raw_date = request.POST.get("next_review_date", "").strip()
    next_review_date = parse_flexible_date(raw_date) if raw_date else None

    try:
        acknowledge_review(action=action, actor=request.user, next_review_date=next_review_date)
    except DomainError as error:
        context = _overview_context(request, matter)
        context.update(_header_context(request, matter))
        context["composer_error"] = str(error)
        return render(request, "matters/partials/overview.html", context, status=400)

    return _render_overview(request, matter)


#: What the composer's «Millal?» row offers. Four spans that cover almost every
#: next step somebody sets from a meeting they have just come back from; the
#: exact box behind «Kuupäev…» covers the rest, and it is the field that is
#: actually submitted either way (design handoff 1d).
QUICK_DATES: tuple[tuple[int, str], ...] = (
    (0, "Täna"),
    (1, "Homme"),
    (7, "+1 nädal"),
    (14, "+2 nädalat"),
)


def quick_date_choices(today: date) -> list[dict[str, Any]]:
    """The quick spans, each carrying the day it resolves to.

    Resolved on the server, in Europe/Tallinn, and delivered on the control. The
    chip shows the actual date once it is chosen — «+1 nädal → N 03.09» — so
    nobody sets a step for a day they did not read. Working it out in the
    browser would answer in the reader's own timezone, which is the whole class
    of defect `app/core/dates.py` exists to prevent.
    """
    return [
        {
            "value": format_estonian_date(today + timedelta(days=days)),
            "label": label,
            "when": f"{weekday_letter(today + timedelta(days=days))} "
            f"{short_day_month(today + timedelta(days=days))}",
        }
        for days, label in QUICK_DATES
    ]


#: What «Lükka edasi» offers, and how far each option moves the date.
DEFER_OPTIONS: tuple[tuple[int, str], ...] = ((1, "+1 päev"), (7, "+1 nädal"))

#: How far the free-date box will accept a deferral. Two years is well past any
#: planning horizon the department has and short of the typo that would file a
#: live instruction in 2226.
DEFER_MAX_DAYS = 730


def defer_base(action: Any, today: date) -> date:
    """The day a deferral counts from.

    **A step already dated in the future moves from its own date.** The pilot
    found this the hard way: a review due 30.09, deferred by a day on 31.08,
    became 01.09 — a date in the *past* relative to the plan it replaced, and
    four weeks earlier than the day the lawyer was looking at. «+1 päev» means
    one day later than the day the step is on, not one day later than the day
    somebody happened to press the button (pilot QA F-05).

    **An overdue step moves from today.** Somebody deferring a deadline that
    passed six days ago means "give me another week", not "make it a day later
    than the day I already missed" (design handoff 1c). `max` is the whole rule:
    the base is the later of today and the date on the step.

    **An undated historical row moves from today**, because there is no other
    day to count from. It is the only case where the base is not a date somebody
    chose, and it is the one case where today is the honest answer.
    """
    target = getattr(action, "target_date", None) if action is not None else None
    return max(today, target) if target else today


def defer_choices(base: date) -> list[dict[str, Any]]:
    """The quick options, with the day each one actually lands on.

    Resolved here, in Europe/Tallinn, and rendered on the control. A chip that
    said only "+1 nädal" would leave the reader to do the arithmetic, and one
    that worked it out in the browser would answer in the reader's timezone
    rather than in the department's.

    `base` is :func:`defer_base` for the step being offered, never today: the
    label and the write have to agree, and a chip reading «+1 päev · K 02.09»
    over a step dated 30.09 is the defect it is supposed to prevent.
    """
    return [
        {
            "days": days,
            "label": label,
            "when": f"{weekday_letter(base + timedelta(days=days))} "
            f"{short_day_month(base + timedelta(days=days))}",
        }
        for days, label in DEFER_OPTIONS
    ]


@login_required
@business_write_required
@require_http_methods(["POST"])
def defer_action(request: HttpRequest, pk: Any, action_id: Any) -> HttpResponse:
    """Move the current step's date, by the service the step's kind requires.

    Two different acts wearing one control. A **DO** carries a commitment Koda
    made, and moving it is a new instruction that supersedes the old one — which
    is what `set_next_action_for_new_work` does, chain and audit row included. A
    **WAIT** or **MONITOR** carries a review date, and moving that is
    acknowledging the review: the Matter is still waiting on the same thing, so
    the action keeps its identity and only its date moves.

    Nothing new is decided here. Both services validate, both write their own
    change event, and both refuse a closed Matter — this view chooses between
    them and computes no business rule of its own (app/workflow/services.py).

    **It swaps the Järgmiseks row and nothing else**, exactly as «✓ Tehtud»
    does. This used to re-render the whole overview column — the row *and the
    open composer under it* — so deferring a step threw away every unsaved
    character somebody had typed about it, which is the same defect ADR 0052 §8
    fixed on the completion control and missed here (pilot QA F-04).

    **It counts from the day the step is on**, not from today. See
    :func:`defer_base` (pilot QA F-05).
    """
    matter = get_visible_matter(request, pk)
    action = get_object_or_404(
        NextAction.objects.visible_to(request.user).open(), pk=action_id, matter=matter
    )

    today = timezone.localdate()
    # The day the delta is added to. Not today: a step already dated in the
    # future moves from its own date, which is the whole of F-05
    # (:func:`defer_base`).
    base = defer_base(action, today)
    raw_days = (request.POST.get("paevad") or "").strip()
    raw_date = (request.POST.get("kuupaev") or "").strip()
    target: date | None = None
    if raw_days:
        try:
            days = int(raw_days)
        except ValueError:
            days = 0
        if 0 < days <= DEFER_MAX_DAYS:
            target = base + timedelta(days=days)
    elif raw_date:
        # A typed date names a day. Nothing is added to it — the box is the
        # answer to "when instead", not to "how much later".
        target = parse_flexible_date(raw_date)

    if target is None or target > base + timedelta(days=DEFER_MAX_DAYS):
        return _next_action_error(request, matter, "Kirjuta kuupäev kujul 7.9.2026.")

    try:
        if action.kind in REVIEW_KINDS:
            acknowledge_review(action=action, actor=request.user, next_review_date=target)
        else:
            # The same person stays responsible. Left to the default it would
            # fall back to the Matter's owner, quietly moving somebody else's
            # instruction onto the owner's queue (app/workflow/services.py,
            # `responsible_for_new_work`).
            set_next_action_for_new_work(
                matter=matter,
                text=action.text,
                kind=action.kind,
                date_semantics=action.date_semantics,
                target_date=target,
                responsible=action.responsible,
                actor=request.user,
            )
    except DomainError as error:
        return _next_action_error(request, matter, str(error))

    context = _next_action_row_context(request, matter)
    return render(request, "matters/partials/next_action_row.html", context)


def _next_action_error(request: HttpRequest, matter: Matter, message: str) -> HttpResponse:
    """The Järgmiseks row again, with the refusal inside it.

    400 with the re-rendered fragment, so somebody pressing a button that could
    not do what it said reads why (static/js/app.js, `responseHandling`). The
    *row*, because the row is what the response replaces: rendering the whole
    overview into a target that holds one row would nest the page inside itself,
    and re-rendering the column is what threw away the open composer
    (pilot QA F-04).
    """
    context = _next_action_row_context(request, matter)
    context["next_action_error"] = message
    return render(request, "matters/partials/next_action_row.html", context, status=400)


FIELD_SERVICES = {
    "owner",
    "stage",
    "track",
    "source_organisations",
    "addressee_organisation",
    "received_date",
    "response_deadline",
    "visibility",
    "policy_area_other",
    "policy_areas",
}


@login_required
@business_write_required
@require_http_methods(["GET", "POST"])
def matter_edit(request: HttpRequest, pk: Any) -> HttpResponse:
    """`Muuda teemat` — the whole record, edited once.

    The inline controls in the header and the rail stay, and remain the right
    tool for changing one field. This page is for the other case: a Matter that
    was filed wrongly, where five facts are wrong at once and five separate
    inline saves is one job pretending to be five (Teema QA §2).

    **One transaction.** Either every field on the form is applied or none is.
    A half-saved correction is worse than a refused one: it leaves a record
    stating a combination of facts nobody chose, and a timeline claiming
    somebody chose it.

    **One service per field.** Nothing here writes a model attribute; every
    value goes through the named service that already owns that fact, so each
    change is audited exactly as it is when made inline, and a title change on
    an unchanged owner writes one event rather than thirteen — every service
    returns early when the value it was given is the value already there.
    """
    matter = get_visible_matter(request, pk)
    if request.method == "GET":
        form = MatterEditForm(initial=edit_initial(matter), matter=matter, viewer=request.user)
        return render(request, "matters/matter_edit.html", _edit_context(request, matter, form))

    form = MatterEditForm(request.POST, matter=matter, viewer=request.user)
    if not form.is_valid():
        # The bound form is re-rendered, so everything typed is still there.
        return render(
            request,
            "matters/matter_edit.html",
            _edit_context(request, matter, form),
            status=400,
        )

    data = form.cleaned_data
    try:
        with transaction.atomic():
            set_matter_title(matter=matter, value=data["title"], actor=request.user)
            set_brief_summary(
                matter=matter, value=data.get("brief_summary") or "", actor=request.user
            )
            assign_matter(matter=matter, owner=data.get("owner"), actor=request.user)
            change_stage(matter=matter, stage=data.get("stage"), actor=request.user)
            change_track(matter=matter, track=data.get("track") or "", actor=request.user)
            # `list(...)` rather than the queryset on both set-valued fields,
            # so an empty POST arrives as "none of them" — a decision somebody
            # made — and never as the sentinel that means "leave them alone"
            # (app/matters/services.py, `_UNSET`).
            # Resolved inside this transaction, and before the Matter is
            # touched. A typed name that names a body nobody has filed against
            # yet becomes an Organisation here; if any service below refuses,
            # the rollback takes that Organisation with it and the Matter keeps
            # the addressee it already had (§6).
            set_organisations(
                matter=matter,
                source_organisations=list(data.get("source_organisations") or []),
                addressee_organisation=resolve_addressee(
                    chosen=data.get("addressee_organisation"),
                    typed_name=data.get("addressee_name") or "",
                ),
                actor=request.user,
            )
            set_matter_dates(
                matter=matter,
                received_date=data.get("received_date"),
                response_deadline=data.get("response_deadline"),
                actor=request.user,
            )
            set_policy_areas(
                matter=matter, policy_areas=list(data.get("policy_areas") or []), actor=request.user
            )
            set_policy_area_other(
                matter=matter, value=data.get("policy_area_other") or "", actor=request.user
            )
            set_tags(matter=matter, tags=list(data.get("tags") or []), actor=request.user)
            set_matter_visibility(
                matter=matter,
                visibility=data.get("visibility") or Visibility.NORMAL,
                actor=request.user,
            )
    except DomainError as error:
        form.add_error(None, str(error))
        matter.refresh_from_db()
        return render(
            request,
            "matters/matter_edit.html",
            _edit_context(request, matter, form),
            status=400,
        )

    messages.success(request, "Teema andmed on salvestatud.")
    return redirect("matters:matter_detail", pk=matter.pk)


def _edit_context(request: HttpRequest, matter: Matter, form: Any) -> dict[str, Any]:
    return {
        "matter": matter,
        "form": form,
        # Named here rather than derived in the template: the page states, in
        # words, which facts about this Matter it will not let anybody change,
        # so their absence reads as a decision rather than as an omission
        # (Teema QA §2.2).
        "immutable_facts": _immutable_facts(matter),
    }


def _immutable_facts(matter: Matter) -> list[tuple[str, str]]:
    """What the edit page shows and refuses to edit.

    Provenance, not fields. Where a record came from is not somebody's to
    decide, and a page that simply omitted that would read as an oversight.

    **No ``display_reference``.** This panel was the last ordinary surface
    printing it, kept on the argument that here it was a labelled fact rather
    than an identity. Review rejected that: `Muuda teemat` is the ordinary
    application, not admin, import tooling or a diagnostic view, and the rule is
    about who is looking rather than about how the value is framed. The
    reference is unchanged system data — ``Matter.display_reference``,
    ``__str__``, exact search, the CSV export and the import tooling all still
    carry it — and no ordinary user is shown it (review of PR #72, §2).

    What stays is provenance a colleague can act on: which kind of record this
    is, and which source row it came from.
    """
    facts: list[tuple[str, str]] = [("Päritolu", matter.get_origin_display())]
    reference = matter.source_references.order_by("created_at").first()
    if reference is not None:
        where = reference.source_file_name or reference.source_system
        if reference.source_row_number:
            where = f"{where}, rida {reference.source_row_number}"
        facts.append(("Algallikas", where))
    return facts


@login_required
@business_write_required
@require_http_methods(["POST"])
def update_field(request: HttpRequest, pk: Any, field: str) -> HttpResponse:
    """Inline header edits. One field, one service call, one re-render."""
    if field not in FIELD_SERVICES:
        raise Http404("Tundmatu väli.")

    matter = get_visible_matter(request, pk)
    surface = _FIELD_SURFACES.get(field, "matters/partials/header.html")
    # The Matter is handed to the form so the owner field accepts the owner
    # already on it. The header renders that owner as the selected option, and
    # without this the control would refuse the value it is displaying: pressing
    # Salvesta on a Matter held by a departed colleague, having changed nothing,
    # would answer "Vigane väärtus." (app/matters/forms.py).
    form = MatterFieldForm(request.POST, matter=matter)
    if not form.is_valid():
        context = _header_context(request, matter)
        context["field_error"] = "Vigane väärtus."
        return render(request, surface, context, status=400)

    value = form.cleaned_data.get(field)
    try:
        if field == "owner":
            assign_matter(matter=matter, owner=value, actor=request.user)
        elif field == "stage":
            change_stage(matter=matter, stage=value, actor=request.user)
        elif field == "track":
            change_track(matter=matter, track=value or "", actor=request.user)
        elif field == "source_organisations":
            # `list(...)` rather than the queryset, so an empty POST arrives as
            # `[]` — "clear every sender" — and never as the `_UNSET` that means
            # "leave them alone" (Agent-E brief 20, 34).
            set_organisations(
                matter=matter, source_organisations=list(value or []), actor=request.user
            )
        elif field == "addressee_organisation":
            set_organisations(matter=matter, addressee_organisation=value, actor=request.user)
        elif field == "received_date":
            set_matter_dates(matter=matter, received_date=value, actor=request.user)
        elif field == "response_deadline":
            set_matter_dates(matter=matter, response_deadline=value, actor=request.user)
        elif field == "visibility":
            set_matter_visibility(
                matter=matter, visibility=value or Visibility.NORMAL, actor=request.user
            )
        elif field == "policy_area_other":
            set_policy_area_other(matter=matter, value=value or "", actor=request.user)
        elif field == "policy_areas":
            # `list(...)` rather than the queryset, for the same reason the
            # sender set uses one: an empty POST means "none of them", which is
            # a decision somebody made, not a field they left alone.
            set_policy_areas(matter=matter, policy_areas=list(value or []), actor=request.user)
    except DomainError as error:
        context = _header_context(request, matter)
        context["field_error"] = str(error)
        return render(request, surface, context, status=400)

    matter.refresh_from_db()
    # Each field re-renders the surface it lives on. `Muu valdkond` sits in the
    # facts rail rather than the header strip, and swapping the header for it
    # would leave the value on screen unchanged while claiming it had saved.
    return render(request, surface, _header_context(request, matter))


#: Fields whose control is not in the header band. `_header_context` already
#: carries everything the rail reads, so one context serves both.
#:
#: The redesign moved four of them: Menetlusliik, Kellelt, Kellele and Saabus
#: are looked-up facts rather than glance facts, so they are edited in the rail
#: where they are now shown. Swapping the header for one of them would leave the
#: value on screen unchanged while claiming it had saved (Teema redesign §22.1).
_FIELD_SURFACES = {
    "policy_area_other": "matters/partials/rail.html",
    "track": "matters/partials/rail.html",
    "source_organisations": "matters/partials/rail.html",
    "addressee_organisation": "matters/partials/rail.html",
    "received_date": "matters/partials/rail.html",
}


@login_required
@business_write_required
@require_http_methods(["POST"])
def set_data_class(request: HttpRequest, pk: Any) -> HttpResponse:
    """Reclassify a Matter as real business data or as development data.

    Its own endpoint rather than another `update_field` case, for one reason:
    this value is rendered in three places at once — the TEST badge in the
    header, the class on the facts rail, and the flag on every register row —
    and `update_field` swaps exactly one surface. Marking a Matter TEST and
    leaving the header still saying nothing is the precise failure this
    classification exists to prevent, so the whole page re-renders instead
    (Agent-C brief 18, 22).
    """
    matter = get_visible_matter(request, pk)
    form = MatterFieldForm(request.POST, matter=matter)
    # `data_class` is `required=False` on that form, because one POST carries
    # one field. An absent value is refused here rather than defaulted: a
    # malformed request must not quietly make a development record real.
    value = form.cleaned_data.get("data_class", "") if form.is_valid() else ""
    try:
        set_matter_data_class(matter=matter, data_class=value, actor=request.user)
    except DomainError as error:
        messages.error(request, str(error))
        return redirect("matters:matter_detail", pk=matter.pk)

    matter.refresh_from_db()
    if matter.is_test_data:
        messages.success(request, "Teema on märgitud testandmeteks.")
    else:
        messages.success(request, "Teema on märgitud pärisandmeteks.")
    return redirect("matters:matter_detail", pk=matter.pk)


@login_required
@business_write_required
@require_http_methods(["POST"])
def update_position(request: HttpRequest, pk: Any) -> HttpResponse:
    """`position_summary` and `rationale_summary`, with **no native UI.**

    Nothing in this application links here any more. There is no separate
    free-text `Koja seisukoht` concept: what the Chamber produced on a Matter is
    the opinion it sent, and that is a file, in `Koja arvamus` on the facts
    rail. The rail block went first, and the reader/editor panel on the
    Arvamused surface went with this change.

    The route is kept rather than deleted, deliberately and on a narrow basis.
    The two columns behind it are not retired — the register cutover and the
    opinion archive both wrote into them, they are still read by
    `app/search/indexing.py`, and dropping the only writer for live, indexed
    data is a data decision rather than a UI one. It also stays inside the
    business-write boundary, where `tests/test_business_write_boundary.py`
    keeps firing every forbidden actor at it: an unreachable route is not an
    unguarded one, and the day something needs to write a position again it
    will find a door that is still locked.

    If that day does not come, retiring the columns, this route, `PositionForm`
    and `set_position` together is one coherent change. Doing it inside a rail
    correction would not have been.
    """
    matter = get_visible_matter(request, pk)
    form = PositionForm(request.POST)
    if form.is_valid():
        set_position(
            matter=matter,
            position_summary=form.cleaned_data["position_summary"],
            rationale_summary=form.cleaned_data["rationale_summary"],
            actor=request.user,
        )
        messages.success(request, "Seisukoht on salvestatud.")
    # The surface this used to return to is retired. Nothing links here any more
    # — the route stays inside the business-write boundary rather than being
    # deleted, because dropping the only writer for live indexed columns is a
    # data decision and this was a UI one — so it lands where the Matter's
    # opinion material now is (docs/adr/0060).
    return redirect(opinions_url(matter))


@login_required
@business_write_required
@require_http_methods(["POST"])
def update_summary(request: HttpRequest, pk: Any) -> HttpResponse:
    """`Lühikokkuvõte`, edited in place under the meta line.

    Its own endpoint rather than another `update_field` case: the summary is a
    paragraph rather than a value on the facts strip, it has its own form and
    its own audit event, and it re-renders the header band it sits in.
    """
    matter = get_visible_matter(request, pk)
    form = BriefSummaryForm(request.POST)
    if not form.is_valid():
        context = _header_context(request, matter)
        context["summary_form"] = form
        context["field_error"] = "Vigane väärtus."
        return render(request, "matters/partials/header.html", context, status=400)

    set_brief_summary(
        matter=matter, value=form.cleaned_data.get("brief_summary") or "", actor=request.user
    )
    matter.refresh_from_db()
    context = _header_context(request, matter)
    context["summary_form"] = BriefSummaryForm(initial={"brief_summary": matter.brief_summary})
    return render(request, "matters/partials/header.html", context)


@login_required
@business_write_required
@require_http_methods(["POST"])
def save_note(request: HttpRequest, pk: Any) -> HttpResponse:
    """Autosave the private `Märkmed` draft.

    Returns 204 and swaps nothing. The person is mid-sentence: replacing the
    textarea they are typing into would move their cursor, and there is nothing
    to show them anyway — the note is theirs, it is not history, and it does not
    appear anywhere else on the page (Teema redesign §22.4).
    """
    matter = get_visible_matter(request, pk)
    form = PersonalNoteForm(request.POST, prefix=NOTE_PREFIX)
    if not form.is_valid():
        return HttpResponse(status=400)
    try:
        save_personal_note(
            matter=matter, author=request.user, body=form.cleaned_data.get("body") or ""
        )
    except DomainError:
        return HttpResponse(status=400)
    return HttpResponse(status=204)


@login_required
@business_write_required
@require_http_methods(["POST"])
def add_working_document(request: HttpRequest, pk: Any) -> HttpResponse:
    """Reference a living SharePoint file from the Dokumendid tab."""
    matter = get_visible_matter(request, pk)
    form = WorkingDocumentForm(request.POST)
    if form.is_valid():
        try:
            link_working_document(
                matter=matter,
                title=form.cleaned_data["title"],
                web_url=form.cleaned_data["web_url"],
                site_path=form.cleaned_data.get("site_path") or "",
                created_by=request.user,
            )
            messages.success(request, "Töödokumendi viide on lisatud.")
        except DomainError as error:
            messages.error(request, str(error))
    else:
        messages.error(request, "Kontrolli töödokumendi nime ja aadressi.")
    return redirect("matters:matter_documents", pk=matter.pk)


@login_required
@business_write_required
@require_http_methods(["POST"])
def close(request: HttpRequest, pk: Any) -> HttpResponse:
    matter = get_visible_matter(request, pk)
    form = CloseMatterForm(request.POST)
    if form.is_valid():
        try:
            close_matter(
                matter=matter,
                disposition=form.cleaned_data["disposition"],
                reason=form.cleaned_data["reason"],
                actor=request.user,
            )
            messages.success(request, "Teema on suletud.")
        except DomainError as error:
            messages.error(request, str(error))
    return redirect("matters:matter_detail", pk=matter.pk)


@login_required
@business_write_required
@require_http_methods(["POST"])
def reopen(request: HttpRequest, pk: Any) -> HttpResponse:
    matter = get_visible_matter(request, pk)
    try:
        reopen_matter(matter=matter, actor=request.user)
        messages.success(request, "Teema on taasavatud.")
    except DomainError as error:
        messages.error(request, str(error))
    return redirect("matters:matter_detail", pk=matter.pk)


@login_required
def timeline_page(request: HttpRequest, pk: Any) -> HttpResponse:
    """Load the next slice of chronology without reloading the page."""
    matter = get_visible_matter(request, pk)
    try:
        offset = max(0, int(request.GET.get("nihe", 0)))
    except ValueError:
        offset = 0

    only = _timeline_filter(request)
    items, has_more = matter_timeline(
        matter=matter, user=request.user, limit=TIMELINE_PAGE_SIZE, offset=offset, only=only
    )
    return render(
        request,
        "matters/partials/timeline_items.html",
        {
            "matter": matter,
            "timeline_rows": collapse_system_runs(items),
            "timeline_has_more": has_more,
            "next_offset": offset + TIMELINE_PAGE_SIZE,
            "timeline_only": only,
        },
    )


def matter_url(matter: Matter) -> str:
    return reverse("matters:matter_detail", kwargs={"pk": matter.pk})


ACTION_KIND_LABELS = dict(ActionKind.choices)
