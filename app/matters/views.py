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

import unicodedata
import uuid
from collections.abc import Sequence
from datetime import date, timedelta
from typing import Any

from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import F, Q
from django.http import Http404, HttpRequest, HttpResponse, HttpResponseRedirect, QueryDict
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_http_methods

from app.accounts.models import User
from app.accounts.selectors import (
    assignable_business_users,
    assignable_including,
    is_person_identifier,
    named_owner_in,
    owner_filter_choices,
)
from app.audit.models import ChangeEvent
from app.audit.visibility import change_log_event_types, scope_change_events
from app.core.authorization import (
    may_review_work_victory,
    may_write_business_content,
)
from app.core.dates import (
    add_months,
    format_estonian_date,
    parse_flexible_date,
    read_flexible_date,
    short_day_month,
    weekday_letter,
)
from app.core.decorators import business_write_required
from app.core.enums import Visibility
from app.core.errors import DomainError
from app.core.request_params import bounded_int, safe_local_path
from app.documents import pending as pending_uploads
from app.documents.enums import DocumentRole, ExtractionState
from app.documents.filenames import NFC
from app.documents.models import Document
from app.documents.pending import human_size
from app.documents.services import link_working_document
from app.documents.uploads import UploadRejected
from app.intelligence.selectors import (
    VISIBLE_VICTORY_STATUS,
    matter_intelligence,
)
from app.intelligence.selectors import work_victory_years as victory_years
from app.legacy_import.register_display import (
    register_facts_for,
    snapshot_label,
    source_instruction_for,
    source_instructions_for,
)
from app.matters import (
    activity,
    department_dashboard,
    intake_staging,
    legal_process,
    register_dates,
    register_filters,
    selectors,
    work_items,
    workspace,
)
from app.matters import person_work as person_workspace
from app.matters.deletion import delete_matter, plan_matter_deletion
from app.matters.department_dashboard import SeisFigure
from app.matters.enums import EngagementKind, MatterOrigin, RecordMode
from app.matters.forms import (
    ENGAGEMENT_UNCHANGED,
    BriefSummaryForm,
    CloseMatterForm,
    CompactClosureForm,
    CompactEffectiveDateForm,
    CompactEngagementForm,
    CompactImportantDateForm,
    CompactWebsiteOverviewForm,
    CompactWorkVictoryForm,
    CompleteCurrentActionForm,
    ComposerForm,
    DevelopmentEvidenceForm,
    EngagementFeedbackForm,
    EngagementForm,
    EngagementWaitForm,
    EntryEditForm,
    ExternalPositionEditForm,
    ExternalPositionEvidenceForm,
    IncomingIntakeForm,
    KodaOpinionForm,
    MatterCreateForm,
    MatterEditForm,
    MatterFieldForm,
    MatterLinkForm,
    MatterProgressForm,
    NextActionForm,
    OtherOpinionForm,
    PersonalNoteForm,
    PositionForm,
    ProceduralDevelopmentEditForm,
    ProceduralLinkCreateForm,
    ProceduralLinkEditForm,
    ReceivedFeedbackForm,
    TimelineStepsForm,
    WebsiteOverviewLinkForm,
    WorkingDocumentForm,
    development_period_initial,
    edit_initial,
    external_position_period_initial,
    matter_edit_conflict_changes,
    period_initial,
    read_organisation_choices,
    visible_engagements_of,
)
from app.matters.intake import register_incoming, validate_uploads
from app.matters.intake_suggestions import (
    CurrentValues,
    SuggestedField,
    analyse_intake,
    analyse_matter,
    create_form_suggestions_offered,
    prefill_controls,
    prefill_initial,
)
from app.matters.legal_process import (
    KODA_STOPPED_LABEL,
    legal_process_rail,
    matter_rail,
)
from app.matters.models import (
    Entry,
    Matter,
    MatterAssignmentNotice,
    MatterEngagement,
    MatterExternalPosition,
    MatterProceduralDevelopment,
    MatterProceduralLink,
    MatterWebsiteOverview,
)
from app.matters.my_work import (
    HORIZON_PARAM,
    VIEW_PARAM,
    build_my_work,
    horizon_from,
    view_from,
)
from app.matters.process_timeline import SENT_LABEL, process_steps
from app.matters.removal import (
    RecordRemovalConflict,
    kind_for,
    remove_matter_record,
)
from app.matters.services import (
    GUARDED_MATTER_FIELDS,
    EngagementEditConflict,
    EntryEditConflict,
    ExternalPositionConflict,
    MatterEditConflict,
    MatterFieldConflict,
    PersonalNoteConflict,
    ProceduralDevelopmentConflict,
    ProceduralLinkConflict,
    TimelineStepsConflict,
    WebsiteOverviewConflict,
    acknowledge_assignment_notice,
    assign_matter,
    change_stage,
    close_matter,
    compose_update,
    correct_engagement,
    correct_external_position,
    correct_procedural_development,
    correct_procedural_link,
    create_matter,
    edit_entry,
    engagement_revision_token,
    entry_revision_token,
    guard_matter_field_revision,
    guard_matter_revision,
    matter_field_revision,
    matter_revision_token,
    open_engagement_feedback_wait,
    personal_note_record,
    personal_note_revision,
    record_engagement,
    record_procedural_link,
    reopen_matter,
    resolve_addressee,
    resolve_source_organisations,
    save_personal_note,
    set_brief_summary,
    set_legal_instrument_other,
    set_legal_instruments,
    set_matter_data_class,
    set_matter_dates,
    set_matter_title,
    set_organisations,
    set_policy_area_other,
    set_policy_areas,
    set_position,
    set_timeline_steps,
    timeline_steps_revision_token,
)
from app.matters.timeline import (
    TIMELINE_FILTER_ALL,
    TIMELINE_FILTERS,
    development_milestone,
    engagement_milestone,
    external_position_milestone,
    matter_timeline,
)
from app.organisations.models import Organisation
from app.related_materials.selectors import related_materials_for
from app.search import services as search_services
from app.submissions import embedded as opinions
from app.submissions.forms import (
    CREATE_PREFIX,
    REGISTER_PREFIX,
    RegisterSentOpinionForm,
    SentOpinionEditForm,
    SubmissionCreateForm,
)
from app.submissions.opinions import (
    OPINION_ROLE_FILTER,
    open_drafts,
    opinion_document_ids,
    opinion_documents,
    sent_submission_by_document,
    unregistered_opinion_documents,
)
from app.taxonomy.legal_instruments import OTHER_LEGAL_INSTRUMENT_KEYS
from app.taxonomy.models import PolicyArea
from app.taxonomy.vocabulary import selectable_policy_areas
from app.workflow.enums import REVIEW_KINDS, ActionKind, Disposition, Track
from app.workflow.models import NextAction, StageVocabulary
from app.workflow.selectors import stages_including
from app.workflow.services import (
    acknowledge_review,
    complete_next_action,
    establish_opinion_preparation_action,
    set_next_action_for_new_work,
)

#: `TWO_FIRST_STEPS_REFUSAL` stood here, and is retired with the question that
#: needed it.
#:
#: `Uus teema` used to offer two ways to give a Matter its first step — a
#: free-text `Järgmiseks` panel and a `Koostan arvamuse` date — and a Matter has
#: one open `NextAction`, so only one of the two could become it. Dropping either
#: silently would have left somebody who answered both with one of their two
#: facts missing and nothing said about it, so the save was refused and named the
#: choice (docs/adr/0091 §1.3).
#:
#: `Järgmiseks` is off the creation page altogether and `Arvamuse tähtaeg` is the
#: one date it asks, so there is no second answer left to collide with and
#: nothing for the sentence to say (docs/adr/0094 §5, §6). The rule underneath is
#: untouched and still enforced where it always was, in the database:
#: `workflow_one_open_action_per_matter`.

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
        # Which stored version the box is being filled from, so a second tab's
        # autosave can be refused rather than allowed to overwrite it (QA-09).
        context["scratchpad_revision"] = person_workspace.scratchpad_revision(context["scratchpad"])
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

    **A stale autosave is refused, not applied.** Minu asjad is the page most
    likely to be open in a second tab all day, and this pad files no history — so
    a last-write-wins overwrite left nothing anywhere to recover the overwritten
    version from. 409 with the newer text beside the box, the person's own words
    untouched in it, and no write at all (QA-09, `_note_conflict`'s sibling).
    """
    try:
        row = person_workspace.save_scratchpad(
            request.user,
            request.POST.get("body", ""),
            expected_revision=request.POST.get("revision", ""),
        )
    except person_workspace.ScratchpadConflict as conflict:
        return render(
            request,
            "matters/partials/scratchpad_conflict.html",
            {
                "scratchpad_conflict": str(conflict),
                "scratchpad_server": conflict.current,
            },
            status=409,
        )
    return render(
        request,
        "matters/partials/scratchpad_meta.html",
        {
            "scratchpad": row,
            # `_saved_`, not `scratchpad_revision`: the page's own context carries
            # that name for the form's hidden field, and one name for both put two
            # elements with the same id on the first render (QA-09).
            "scratchpad_saved_revision": person_workspace.scratchpad_revision(row),
        },
    )


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

    return HttpResponseRedirect(_safe_next(request) or reverse("matters:my_work"))


def _safe_next(request: HttpRequest) -> str:
    """A redirect target, only if it points back at this site.

    The same guard `app.accounts.views` applies to the persona switch: a `next`
    a browser sent is somebody's input until it has been checked.
    """
    # A path on this site and nothing else: `/teemad/`, never a host (even
    # this one), `//elsewhere`, `teemad/` or a bare URL name, which
    # `redirect()` would resolve as a view (ENG-046). Callers hand the answer to
    # `HttpResponseRedirect`, never to `redirect()`.
    return safe_local_path(
        request.POST.get("next"), host=request.get_host(), require_https=request.is_secure()
    )


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
                    sender_name=data.get("sender_name") or "",
                    received_date=data.get("received_date") or timezone.localdate(),
                    response_deadline=data.get("response_deadline"),
                    # Decided here, never read from the form — the same rule
                    # `matter_create` follows one screen over. The control is
                    # gone from this page and the field is gone from
                    # `IncomingIntakeForm`, so a crafted `visibility=` reaches
                    # nothing (docs/adr/0096 §3).
                    visibility=Visibility.NORMAL,
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
    # The two structured facts Teemad became the discovery surface for when the
    # separate Tähtajad destination was retired. Properties of a Matter, so they
    # narrow the register like any other dimension rather than opening a list of
    # their own (docs/adr/0071).
    "toovoit": "Töövõit",
    "joustumine": "Jõustumine",
    "joustub_alates": "Jõustub alates",
    "joustub_kuni": "Jõustub kuni",
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
#:
#: `hilinenud` reads «Üle aja» and not «Tähtaeg möödas». This filter selects
#: exactly one thing — an open `DO` + `DEADLINE` whose own date has passed
#: (`selectors._open_action_condition`) — and that date is the lawyer's own
#: plan, which the product no longer calls a tähtaeg anywhere it is displayed.
#: The register's *mixed* deadline populations keep the word, because they
#: genuinely hold `Arvamuse tähtaeg` and `Oluline tähtaeg` as well
#: (`work_items.WORK_POPULATION_LABELS`, docs/adr/0054 §Amendment).
NEXT_ACTION_LABELS = {
    "puudub": "Puudub",
    "hilinenud": "Üle aja",
    selectors.REVIEW_DUE: "Ülevaatus käes",
}

#: How each `?toovoit=` value reads in the control and in a chip. A year is not
#: in here: it is offered as its own option list, and read back as itself.
VICTORY_LABELS = {
    register_filters.FACT_PRESENT: "Töövõiduga",
    register_filters.FACT_ABSENT: "Ilma töövõiduta",
}

#: The same for `?joustumine=`. Two words rather than a checkbox, because the
#: absence is a question people genuinely ask of this register — «millised aktid
#: ei ole veel jõustumas».
COMMENCEMENT_LABELS = {
    register_filters.FACT_PRESENT: "Jõustumisega",
    register_filters.FACT_ABSENT: "Ilma jõustumiseta",
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


#: The sorts that order on a stored column, as the column names to order by.
#:
#: Every one of these four is an address somebody may already hold. `reference`
#: is what a bare `/teemad/` means and what the Järjestus control posts;
#: `deadline` orders on `Matter.response_deadline` and is **not** the Kuupäev
#: column — the column shows the open step's own date whenever the visible step
#: has one, and the two coincide on only some of the register. Neither value's
#: meaning changes here: the interactive headings add values beside these rather
#: than redefining them, because a bookmark that silently starts answering a
#: different question is worse than one that stops working (brief 18).
SORT_FIELDS = {
    "reference": ("-reference_year", "-reference_number"),
    "title": ("title",),
    "updated": ("-updated_at",),
    "deadline": ("response_deadline",),
}

#: The register's ordering when nobody has chosen one. Newest reference first —
#: the department's own filing order, and the list a lawyer expects to open on.
#: A clickable heading does not change it: the two new columns are sortable, not
#: the new default (brief 19).
DEFAULT_SORT = "reference"

#: What the **Kuupäev** heading sorts by, in the order its activations cycle
#: through. The key is the effective date the row displays, never a hidden
#: column beside it (app/matters/register_dates.py).
DATE_SORT_ASC = "kuupaev_asc"
DATE_SORT_DESC = "kuupaev_desc"

#: What the **Viimane tegevus** heading sorts by. Named for the column rather
#: than for `?tegevus=`, which is a different dimension one column to the left:
#: `?tegevus=` narrows by the *next* step and this orders by the *last* thing
#: that happened, and one vocabulary answering both would be two questions
#: wearing one word (app/matters/views.py `FILTER_LABELS`).
ACTIVITY_SORT_NEWEST = "viimane_uusim"
ACTIVITY_SORT_OLDEST = "viimane_vanim"

#: How each sort reads in the Järjestus control. Every value the register
#: understands is offered there, including the four a heading sets: a sort a
#: heading can reach and the panel cannot is a state somebody arrives at and
#: then loses the moment they submit the panel — the select would post its first
#: option and silently undo their ordering (brief 16, 20).
SORT_LABELS = {
    # `Vaikimisi`, not `Viide`: the reference is no longer anywhere on this page,
    # so an option naming it asked somebody to sort by a value they cannot see.
    # The value behind it is untouched (review of PR #72, §18).
    "reference": "Vaikimisi",
    # Named for what it sorts by. `?jarjestus=updated` orders on
    # `Matter.updated_at` — when the row was last *written* — which the Viimane
    # tegevus column deliberately does not show (ADR 0026).
    "updated": "Viimati muudetud",
    # Named for the column it reads rather than for the word «Tähtaeg», which
    # now belongs to two different sorts on one page: this one orders on the
    # Matter's own `Arvamuse tähtaeg`, and the Kuupäev pair below orders on
    # whatever the row is actually showing.
    "deadline": "Arvamuse tähtaeg",
    "title": "Pealkiri",
    DATE_SORT_ASC: "Kuupäev — varaseim enne",
    DATE_SORT_DESC: "Kuupäev — hiliseim enne",
    ACTIVITY_SORT_NEWEST: "Viimane tegevus — uusim enne",
    ACTIVITY_SORT_OLDEST: "Viimane tegevus — vanim enne",
}


# ---------------------------------------------------------------------------
# The register's interactive column headings
#
# Hetkeseis, Vastutaja and Järgmiseks filter; Kuupäev and Viimane tegevus sort.
# Nothing here is a second filter system: a heading writes the same `?`
# parameter Täpsem otsing writes, produces the same chip, and reads its own
# active state back out of the address — so the panel, the chips and the heading
# cannot disagree, because there is only one of them (docs/adr/0071).
#
# The *option lists* are deliberately not built here. Hetkeseis offers
# `stages`, Vastutaja offers `owners` and Järgmiseks offers
# `next_action_options` — the same three context variables the Täpsem otsing
# panel's own selects are rendered from. A heading with its own catalogue would
# be a second population to keep in step, which is exactly the defect the
# organisation chooser cost a round to find (docs/adr/0071, Asutus pool).
# ---------------------------------------------------------------------------

#: What a register link is, reused rather than redefined — so a heading, a value
#: inside a row and a sort arrow cannot each invent their own idea of which
#: parameters survive (app/matters/register_filters.py).
register_query = register_filters.register_query

#: Each filtering heading: the word the column has always carried, and how its
#: trigger is named to assistive technology. The visible label is *inside* the
#: accessible name, so the two cannot drift apart and a voice user asking for
#: «Hetkeseis» reaches the control they can see (WCAG 2.5.3).
#:
#: The heading's own word, not the chip's. `?tegevus=` appears above the table
#: as «Järgmine tegevus» because a chip has to say which dimension it is; the
#: column it belongs to has been «Järgmiseks» since the register was drawn, and
#: renaming a column to match a chip would be this change editing a product
#: decision it was not asked about.
#:
#: The third entry is the column's own width class, which stays with the
#: heading: `table-layout: fixed` takes the register's geometry from the head
#: row, so a `<th>` that lost its class would resize the column under it
#: (static/css/app.css, `.table--register`).
COLUMN_FILTERS = (
    ("hetkeseis", "Hetkeseis", "table__stage", "filtreeri hetkeseisu järgi"),
    ("vastutaja", "Vastutaja", "table__owner", "filtreeri vastutaja järgi"),
    ("tegevus", "Järgmiseks", "table__action", "filtreeri järgmise tegevuse järgi"),
)


#: Each sortable heading, as the label it carries and the two orderings its
#: activations cycle through. A cycle entry is (parameter value, what
#: ``aria-sort`` calls the resulting order, what the link that reaches it does).
#:
#: **`aria-sort` describes the column, not the click.** Kuupäev opens on
#: *varaseim enne* and that is an ascending column; Viimane tegevus opens on
#: *uusim enne*, which is the same first click and a **descending** column. A
#: heading that announced "ascending" while showing the newest date at the top
#: would be telling a screen-reader user the opposite of what is on the screen
#: (WAI-ARIA 1.2, `aria-sort`).
#:
#: The two open differently on purpose: earliest-first is what somebody means by
#: sorting a column of things still to come, and newest-first is what they mean
#: by sorting a column of things that have already happened.
SORT_COLUMNS: dict[str, tuple[str, tuple[str, str, str], tuple[str, str, str]]] = {
    "kuupaev": (
        "Kuupäev",
        (DATE_SORT_ASC, "ascending", "järjesta varaseim enne"),
        (DATE_SORT_DESC, "descending", "järjesta hiliseim enne"),
    ),
    "viimane": (
        "Viimane tegevus",
        (ACTIVITY_SORT_NEWEST, "descending", "järjesta uusim enne"),
        (ACTIVITY_SORT_OLDEST, "ascending", "järjesta vanim enne"),
    ),
}


def _sort_column(
    params: Any,
    current: str,
    label: str,
    first: tuple[str, str, str],
    second: tuple[str, str, str],
) -> dict[str, Any]:
    """One sortable heading: where its next activation goes, and where it is now.

    Three states, in one cycle. Unsorted, then this column one way, then the
    other, then back to the register's own order — so the heading that turned
    the ordering on is also the heading that turns it off, and nobody has to
    find the Järjestus control to undo a click (brief 9, 12).

    ``aria-sort`` carries the same three states to a screen reader, which is the
    only reason the arrow beside the label may stay decorative.
    """
    following: str | None
    if current == first[0]:
        direction, following, hint = first[1], second[0], second[2]
    elif current == second[0]:
        direction, following, hint = second[1], None, "taasta vaikimisi järjestus"
    else:
        direction, following, hint = "none", first[0], first[2]
    return {
        "label": label,
        "direction": direction,
        "hint": hint,
        "query": register_query(params, jarjestus=following),
    }


def register_columns(params: Any) -> dict[str, Any]:
    """What the interactive headings need, beside the option lists they share.

    Built before the fragment branch in :func:`matter_list`, because the live
    search replaces the results region and the table's own head is inside it: a
    heading that lost its menu or its sort arrow on the first keystroke would be
    a control that works until you use the control beside it (brief 21).
    """
    sort = params.get("jarjestus", DEFAULT_SORT)
    return {
        "column_filters": {
            name: {
                "value": params.get(name, ""),
                "label": label,
                "cell": cell,
                "accessible": accessible,
                # What *Kõik* points at: this address minus this one dimension.
                "clear_query": register_query(params, **{name: None}),
            }
            for name, label, cell, accessible in COLUMN_FILTERS
        },
        "column_sorts": {
            key: _sort_column(params, sort, label, first, second)
            for key, (label, first, second) in SORT_COLUMNS.items()
        },
    }


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
    if name == register_filters.VICTORY_PARAM:
        # A year reads as the year. It is neither of the two words, and echoing
        # it back as typed is what makes «Töövõit: 2024» a chip somebody can act
        # on rather than a key.
        return VICTORY_LABELS.get(value, value)
    if name == register_filters.COMMENCEMENT_PARAM:
        return COMMENCEMENT_LABELS.get(value, value)
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
            register_filters.COMMENCEMENT_START_PARAM,
            register_filters.COMMENCEMENT_END_PARAM,
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


def _without_dimension(params: Any, name: str) -> str:
    """The same address, minus one narrowing dimension. The chip `×` contract.

    Every `×` in the chip row means the same thing — *stop narrowing by this
    one* — so every `×` is built here. It was not always: the free-text chip
    carried `cleared_query`, the address `Tühjenda kõik` uses, and therefore
    removed `q`, every structured filter and the sort together (R2-04). A
    control that says `Otsing: eelnõu ×` and silently clears `Hetkeseis`,
    `Vastutaja` and `Järjestus` is not a slower way to reach the same place;
    it is a different answer, and the reader has no way to see it happen.

    `leht` goes with it for the reason every other filter change drops it:
    widening the population renumbers the pages, so page 4 of the narrower
    list addresses rows that are no longer there. The page size (`kaupa`) and
    every dimension the reader did not click stay exactly as they were.
    """
    without = params.copy()
    without.pop(name, None)
    without.pop("leht", None)
    return without.urlencode()


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
        chips.append(
            {
                "name": name,
                "label": label,
                "value": _filter_display(request, name, value),
                "remove_query": _without_dimension(params, name),
            }
        )
    return chips


def _ordered(queryset: Any, sort: str, user: Any) -> Any:
    """The register in the chosen order, with the derived keys it needs.

    Two rules the headings rest on.

    **A missing date is last in both directions.** PostgreSQL's own default puts
    NULLs last ascending and *first* descending, so "latest first" would have
    opened on every row that has no date at all — a page of em dashes, above the
    rows somebody clicked the heading to see. ``nulls_last=True`` on both sides
    says what the column means instead: rows with a known date, in order, then
    the rows without one.

    **The derived keys are annotated only when they are ordered on.** Each costs
    a correlated subquery (`register_display_date`) or re-reads seven of them
    (`last_activity_on`), and the register's ordinary page reads neither — the
    row renders both facts from what
    ``selectors.matter_list_queryset`` has already loaded.

    ``-created_at`` stays the final tie-break for every sort, so two rows sharing
    a date have one stable order rather than whatever the planner returns
    (brief 22: a page boundary that moves between requests loses rows).
    """
    chosen: tuple[Any, ...]
    if sort in (DATE_SORT_ASC, DATE_SORT_DESC):
        queryset = register_dates.annotate_display_date(queryset, user)
        key = F(register_dates.DISPLAY_DATE)
        chosen = (key.asc(nulls_last=True) if sort == DATE_SORT_ASC else key.desc(nulls_last=True),)
    elif sort in (ACTIVITY_SORT_NEWEST, ACTIVITY_SORT_OLDEST):
        queryset = activity.annotate_activity_date(queryset)
        key = F(activity.ACTIVITY_DATE)
        chosen = (
            key.desc(nulls_last=True) if sort == ACTIVITY_SORT_NEWEST else key.asc(nulls_last=True),
        )
    else:
        chosen = SORT_FIELDS.get(sort, SORT_FIELDS[DEFAULT_SORT])
    return queryset.order_by(*chosen, "-created_at")


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


#: What the empty register offers when the matches are simply somewhere else.
#:
#: `/teemad/?q=QA hiline` answered `0 teemat otsingule „QA hiline”` while the
#: filter chips on the same page read `Avatud · 17 · Suletud · 1 · Arhiiv · 1`
#: and the global search found the file immediately. The scoping is right — the
#: register opens on open work and must not silently widen itself — but a
#: dead-end empty state on a page that already knows the answer is not
#: (QA-019).
ELSEWHERE_HINT = "Vasteid leidub ka teistes olekutes."

#: The label on the link that widens the status filter. `Kõik`, because that is
#: the segment it switches to and the word already on the chip beside it — a
#: second name for one destination is a second thing to learn.
ELSEWHERE_LINK = "Kuva kõik olekud"


def _matches_elsewhere(request: HttpRequest, params: Any, queryset: Any) -> dict[str, Any] | None:
    """Whether this same search finds anything under a wider status, and where.

    **Only on an empty page, and only when a status filter is narrowing.** The
    count is one extra `COUNT(*)` over a queryset that is already built, run at
    most once per request and never when there are rows to show — so the
    ordinary register pays nothing for it.

    **Scoped before counted, like every other figure here.** ``queryset``
    arrives from `matter_list_queryset` through `visible_to` and through the
    free-text projection, so a restricted Matter cannot make the sentence
    appear. A count computed before authorization would be exactly the leak
    `_segment_queryset` is careful about: «there is something you cannot see»
    is a disclosure.

    It does **not** widen the filter on the reader's behalf. It says a wider
    view has matches and offers the address; pressing it is their decision.
    """
    if params.get("olek", "avatud") == "koik":
        return None
    widened = params.copy()
    widened["olek"] = "koik"
    widened.pop("leht", None)
    total = register_filters.apply_register_filters(queryset, request.user, widened)[0]
    count = total.distinct().count()
    if not count:
        return None
    return {"count": count, "query": widened.urlencode(), "label": ELSEWHERE_LINK}


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
    # Held before the status filter narrows it, so an empty answer can ask the
    # one further question that is worth asking: «are the matches simply in
    # another status» (`_matches_elsewhere`, QA-019). Nothing is evaluated here.
    before_status = queryset
    queryset, date_echo = register_filters.apply_register_filters(queryset, request.user, params)

    # The database orders, and it orders before the page boundary is drawn. A
    # sort applied to `page.object_list` would order the twenty-five rows that
    # happened to land on this page and call the result a sorted register
    # (brief 26).
    sort = params.get("jarjestus", DEFAULT_SORT)
    queryset = _ordered(queryset, sort, request.user)

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
        # The free-text chip's own `×`, built like every sibling chip's rather
        # than aliased to `Tühjenda kõik` (R2-04).
        "cleared_search_query": _without_dimension(params, "q"),
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
        # `None` unless the page is empty *and* a status filter is narrowing —
        # the one case where the register knows something useful it was not
        # asked. One extra `COUNT(*)`, never on a page with rows.
        "matches_elsewhere": (
            _matches_elsewhere(request, params, before_status) if not page.object_list else None
        ),
        "elsewhere_hint": ELSEWHERE_HINT,
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
            "toovoit": params.get(register_filters.VICTORY_PARAM, ""),
            "joustumine": params.get(register_filters.COMMENCEMENT_PARAM, ""),
            "too": params.get("too", ""),
            "saatja": params.get("saatja", ""),
            "adressaat": params.get("adressaat", ""),
            "jarjestus": sort,
        },
        "nav_active": "teemad",
        # This page's table is the interactive one. The partial is shared with
        # Saabunud, which keeps its static headings and its plain cells: a
        # filtering control on a surface that has no filter parameters to write
        # would be a control that changes nothing (brief 23).
        "register_interactive": True,
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
        #
        # These three are read *above* the fragment branch, unlike the option
        # lists below it, because the Vastutaja, Hetkeseis and Järgmiseks
        # headings are inside the results region the live search replaces. Two
        # small queries per keystroke — a vocabulary table of nine rows and one
        # union over owners — buy a heading that still has its menu after
        # somebody types (brief 21).
        "owners": owner_filter_choices(Matter.objects.visible_to(request.user)),
        "stages": StageVocabulary.objects.filter(is_active=True).order_by("sort_order"),
        "next_action_options": list(NEXT_ACTION_LABELS.items()),
        **register_columns(params),
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
        # Returned before the rest of the filter-control options are built.
        # Those populate selects that are not in this fragment, and a keystroke
        # must not pay for a list of organisations nobody is going to see
        # (brief 14). The three the column headings share are above, because
        # the headings *are* in this fragment.
        return render(request, "matters/partials/register_results.html", context)

    context |= {
        "tracks": Track.choices,
        # The governed vocabulary, so the register's filter offers exactly what
        # Uus teema and the Teema header offer (app/taxonomy/vocabulary.py).
        "policy_areas": selectable_policy_areas(),
        "record_modes": RecordMode.choices,
        "origins": MatterOrigin.choices,
        # Every ordering the register understands, including the four a column
        # heading sets. Built from one mapping rather than written out in the
        # template, so a sort the headings can reach and the panel cannot is not
        # expressible (brief 16).
        "sort_options": list(SORT_LABELS.items()),
        "opinion_options": list(OPINION_LABELS.items()),
        "victory_options": list(VICTORY_LABELS.items()),
        "commencement_options": list(COMMENCEMENT_LABELS.items()),
        # The years a work victory this reader may see was actually recorded
        # for, so choosing one shows something. Scoped by the same selector the
        # retired page used, over the same visible population — a year built
        # from rows the register cannot show would be a filter that empties the
        # page it is attached to (app/intelligence/selectors.py).
        "victory_years": victory_years(request.user, status=VISIBLE_VICTORY_STATUS),
        "material_options": sorted(MATERIAL_LABELS.items()),
        # Kõik first: it is the default, and the control should open on the
        # state the page is actually in.
        "data_class_options": [
            (selectors.DATA_CLASS_ALL, DATA_CLASS_LABELS[selectors.DATA_CLASS_ALL]),
            (selectors.DATA_CLASS_REAL, DATA_CLASS_LABELS[selectors.DATA_CLASS_REAL]),
            (selectors.DATA_CLASS_TEST, DATA_CLASS_LABELS[selectors.DATA_CLASS_TEST]),
        ],
        # One per dimension. The three controls are one partial over one
        # catalogue, and each has to offer and redisplay the body *it* filters
        # by (docs/adr/0071).
        "organisation_choosers": _organisation_choosers(params),
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
#:
#: These strings are the legends the Täpsem otsing panel renders, read from here
#: rather than written into the template beside the include. The panel and the
#: HTMX fragment are the same partial, so two spellings of one legend meant the
#: control silently renamed itself the first time somebody typed into it — it
#: opened as «Asutus (saatja või adressaat)» and came back as «Asutus»
#: (docs/adr/0071).
ORGANISATION_CHOOSER_FIELDS = {
    "asutus": "Asutus (saatja või adressaat)",
    "saatja": "Saatja / algataja",
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
        return HttpResponseRedirect(_safe_next(request) or reverse("matters:matter_list"))

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

    return HttpResponseRedirect(_safe_next(request) or reverse("matters:matter_list"))


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


def _organisation_choosers(params: Any) -> list[dict[str, Any]]:
    """The three institution controls the Täpsem otsing panel renders.

    One catalogue, one partial, three dimensions — built here so the panel and
    the HTMX fragment cannot drift apart in what they offer, what they call
    themselves or what they redisplay (docs/adr/0071).

    **The typed term is read from the parameters, not assumed empty.** With
    scripting on, the search box swaps itself through
    :func:`organisation_choices` and this never matters. With scripting off — or
    before HTMX has loaded, or after it has failed — pressing Enter in that box
    submits the enclosing filter form, which lands back here carrying
    ``?asutus_otsing=kliima``. Rendering the unsearched first page in answer to
    that would be the control ignoring what somebody just typed into it, and
    since the unsearched page is the first twenty bodies alphabetically, the
    institution they were looking for is precisely the one it would not show.

    ``_otsing`` is a control parameter: `_is_control_param` keeps it out of the
    chips, out of `Tühjenda kõik` and out of the search box's carried inputs, so
    reading it here does not turn it into a filter.
    """
    choosers: list[dict[str, Any]] = []
    for field, label in ORGANISATION_CHOOSER_FIELDS.items():
        term = (params.get(f"{field}{CONTROL_PARAM_SUFFIX}") or "").strip()
        chosen = params.get(field, "")
        choosers.append(
            {
                "field": field,
                "field_label": label,
                "term": term,
                "organisation_options": _organisation_options(term),
                "chosen_organisation": _organisation_or_none(chosen),
                "chosen_value": chosen,
            }
        )
    return choosers


@login_required
def organisation_choices(request: HttpRequest) -> HttpResponse:
    """The searchable organisation control, re-rendered for what was typed.

    A server-backed chooser rather than a select carrying every institution: the
    real catalogue runs to hundreds, and the alternative the brief rules out — a
    wall of radio buttons — is unusable at that size. No frontend library is
    introduced; this is one HTMX swap of one labelled ``<select>`` (brief 13).

    All three institution dimensions come through here, and the context is built
    by the same function the full page builds it with — so a search cannot
    answer with a differently-labelled control, a different catalogue or a
    forgotten selection (:func:`_organisation_choosers`).
    """
    field = request.GET.get("vali", "asutus")
    if field not in ORGANISATION_CHOOSER_FIELDS:
        raise Http404("Tundmatu vali.")
    chooser = next(item for item in _organisation_choosers(request.GET) if item["field"] == field)
    return render(request, "matters/partials/organisation_choices.html", chooser)


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

    **A refusal keeps the files.** It did not, and that was the reported defect:
    a browser cannot put a file back into a file input, so every answer that
    re-rendered this form came back with the file area empty. Somebody who chose
    a file, was told about a valdkond they had ticked without naming, corrected
    it and pressed the button again filed a Matter with no documents — having
    been told about neither the loss nor its consequence. The bytes had reached
    the server and been validated by then; they were dropped because the answer
    was a page rather than a redirect.

    So the validated uploads are held for the length of the refusal, the
    re-rendered form carries the keys naming them, and the next attempt resumes
    them. They are unioned with anything chosen again, in the order they were
    first offered, and they go through `_attach_incoming_file` exactly as they
    would have the first time (app/documents/pending.py).

    **And the files are read before the Teema exists.** Where the browser can
    upload, choosing a file stages it (`intake_stage`), the worker reads it
    behind the same scan gate as every other file, and what the rules find
    appears on this form while it is still being filled in. What arrives here
    is then a session identifier rather than bytes: `promote_intake_files`
    turns each staged file into one Document with one immutable version, inside
    this transaction, from the exact bytes the browser sent
    (app/matters/intake_staging.py, docs/adr/0064).

    Three paths into the same place, and they compose. Staged files first, then
    what a refusal is holding, then anything chosen again — the order somebody
    offered them in. With scripting off the first is simply empty and the page
    behaves exactly as it did before.
    """
    form = MatterCreateForm(request.POST or None, viewer=request.user)
    # **No second form for the first step, and no second date.**
    #
    # Two blocks used to be bound conditionally here: a free-text `Järgmiseks`
    # panel and a `Koostan arvamuse` date box, each with its own prefix, each
    # bound only when somebody had answered it — because an unconditionally bound
    # optional form prints "See lahter on nõutav." under controls nobody touched,
    # which reads as "this is mandatory after all" (specification 3.8).
    #
    # Neither is on the page. `Järgmiseks` is answered where a lawyer changes a
    # plan, in the Teema composer, and the first step now comes from
    # `Arvamuse tähtaeg` — `MatterCreateForm.response_deadline`, a field on the
    # form above, so there is nothing extra to bind and nothing that can refuse
    # on its own (docs/adr/0094 §5, §6).
    #
    # Which also closes the collision the two blocks had with each other: a
    # Matter has one open step, so answering both had to be a refusal. One
    # question cannot disagree with itself.
    # `Menetluse link`, bound *and* empty-permitted. Bound, because the block has
    # to come back holding what was typed into it when the save is refused for a
    # reason somewhere else — and it is on screen from the first render now, so
    # there is more of it to lose (docs/adr/0094 §3). Empty-permitted, because a
    # bound form validates and this one has nothing to validate until somebody
    # types an address: without it every save that had not used the block came
    # back with «Menetluse link vajab veebiaadressi.» under a box nobody had
    # touched. `ProceduralLinkCreateForm.has_changed` is where that is settled,
    # off the same `wants_link` this line reads (docs/adr/0089 §13, QA-01).
    procedural_form = ProceduralLinkCreateForm(request.POST or None, prefix="menetlus")
    wants_procedural_link = procedural_form.wants_link
    uploads: list[Any] = []
    upload_refusals: tuple[str, ...] = ()
    # What an earlier refusal is holding, and which of it this attempt still
    # wants. A key the session is not holding — a stale form, a second tab, a
    # swept object — is simply not there; it is never a reason to refuse a save
    # somebody can otherwise complete (app/documents/pending.py).
    held_keys: list[str] = []
    chosen: list[Any] = []
    # The staging this attempt names, if it is this person's and still open.
    # Somebody else's, an expired one and one a Matter has already consumed all
    # read as absent, and absent is simply "no staged files" rather than a
    # refusal: the person can still file the Teema, which is the point
    # (app/matters/intake_staging.py, task §23).
    intake_session = (
        _requested_intake_session(request, request.POST) if request.method == "POST" else None
    )
    if request.method == "POST":
        asked = [key for key in request.POST.getlist("pending") if key]
        # Described first, so the keys carried forward are the ones this session
        # is genuinely holding and a stale form does not keep re-offering a file
        # that is no longer there.
        held_keys = [item.key for item in pending_uploads.describe(request.session, asked)]
        resumed = pending_uploads.resume(request.session, held_keys)
        chosen, upload_refusals = _read_new_matter_files(request)
        # Held first, then chosen: the order somebody offered them in. A file
        # picked again after a refusal is a second file and is filed as one —
        # the preview lists both, so nothing is deduplicated behind their back.
        uploads = [*resumed, *chosen]

        refused = bool(upload_refusals) or not form.is_valid()
        # `Arvamuse tähtaeg` is a field on `form`, so a badly typed date is
        # already counted above and comes back in its own box. There is no
        # separate next-step form left to validate (docs/adr/0094 §5).
        #
        # Validated *before* anything is written, like every other half of this
        # save: a refused address must not leave a Teema behind carrying the
        # rest. What was typed comes back in the box, because the form travels
        # bound into `_create_context` (docs/adr/0089 §13).
        if wants_procedural_link and not procedural_form.is_valid():
            refused = True

        if refused:
            # One message per refused file. The messages block renders each as
            # its own paragraph, so four chosen files with three problems reads
            # as three named problems rather than as one unnamed one (QA-02).
            for refusal in upload_refusals:
                messages.error(request, refusal)
            # Everything that passed validation survives the refusal, including
            # the good half of a batch whose other half was rejected. Files
            # already held stay held — `resume` reads without consuming — so
            # only what arrived on this request has to be added.
            newly_held = pending_uploads.hold(request.session, chosen)
            held_keys = [*held_keys, *(item.key for item in newly_held)]
            return render(
                request,
                "matters/matter_create.html",
                _create_context(
                    request,
                    form,
                    procedural_form=procedural_form,
                    held_keys=held_keys,
                    # Always a token on the re-rendered form, so the corrected
                    # save is protected like the first one (ENG-074).
                    intake_session=intake_session
                    or intake_staging.open_session(owner=request.user),
                ),
                status=400,
            )

    if request.method == "POST" and form.is_valid():
        data = form.cleaned_data
        promoted: list[Any] = []
        try:
            with transaction.atomic():
                # **This form, exactly once** (ENG-074). The form's session is
                # its one-time token, consumed here — first, before any row is
                # written or any reference number allocated — by one
                # conditional UPDATE. A second submission of the same form
                # waits for the first on that row, finds it consumed, and is
                # answered below with the Teema the first one created: no
                # second Teema, no second reference, no file promoted twice.
                # A POST naming no session of this person's carries no token
                # and is not a replay of anything (app/matters/intake_staging.py).
                claim = intake_staging.claim_for_create(
                    owner=request.user, session_id=request.POST.get("intake")
                )
                if claim.replayed:
                    raise _FormAlreadySubmitted(claim.session)

                # Resolved *inside* the transaction, and before the Matter, so a
                # brand-new institution and the Teema that names it are one
                # write. A rejected attachment, a refused next action or any
                # other late failure below takes the institution with it rather
                # than leaving an orphan in the catalogue nobody asked for
                # (§6, app/matters/services.py `resolve_source_organisations`).
                #
                # **Only the sender side.** `Uus teema` no longer asks Adressaat
                # and no longer answers it from Saatja, so a Matter is created
                # with no addressee and gains one when Koda decides who to send
                # to (docs/adr/0090 §5).
                senders = resolve_source_organisations(
                    chosen=data.get("source_organisations"),
                    typed_name=data.get("sender_name") or "",
                )
                instruments = list(data.get("legal_instruments") or [])
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
                    # **Not asked and not derived.** `Menetlusliik` is a
                    # statement about the *procedure*, and no `Õigusakt` type
                    # entails one: a `Seadus` transposing a directive is a
                    # domestic instrument on a transposition track. Writing
                    # `DOMESTIC` from the type would reduce a seven-value
                    # classification to a domestic/EU boolean and be wrong about
                    # exactly the files that matter. It is answered where it is
                    # known — by a person, on `Muuda teemat` and in the Teema
                    # rail, both of which offer the whole vocabulary
                    # (docs/adr/0090 §4).
                    track="",
                    source_organisations=senders,
                    received_date=data.get("received_date"),
                    response_deadline=data.get("response_deadline"),
                    policy_areas=list(data.get("policy_areas") or []),
                    policy_area_other=data.get("policy_area_other") or "",
                    # Handed to the service as part of the creation operation
                    # rather than written onto the Matter afterwards. One
                    # transaction, one MATTER_CREATED event, and no path where
                    # a Teema exists for an instant carrying a classification
                    # nobody chose (task §17, §19).
                    legal_instruments=instruments,
                    legal_instrument_other=data.get("legal_instrument_other") or "",
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
                # Staged first, because they were chosen first. Each becomes
                # one Document with one immutable version from the bytes the
                # browser sent, verified against the checksum recorded when
                # they arrived — nothing is re-uploaded and nothing is rebuilt
                # from extracted text (`promote_intake_files`).
                if claim.session is not None:
                    intake_staging.record_created_matter(session=claim.session, matter=matter)
                if intake_session is not None:
                    promoted = intake_staging.promote_intake_files(
                        session=intake_session, matter=matter, actor=request.user
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

                # `Menetluse link`, inside this same transaction and only where
                # somebody typed an address. **Atomic with the Teema**: a refusal
                # anywhere below takes the link with it, and a link the service
                # refuses takes the Teema with it, so there is no path on which a
                # Matter exists carrying half of what was submitted
                # (docs/adr/0089 §13).
                #
                # `record_procedural_link` rather than the workspace wrapper,
                # like `create_matter` and `save_personal_note` directly above:
                # the wrapper's job is the closed-Matter lock, and this Matter is
                # being created open inside this transaction.
                #
                # A retry after a refusal creates a *different* Teema, so there
                # is no duplicate to collapse here — and the per-Matter
                # uniqueness stands behind a double-submitted create either way.
                if wants_procedural_link:
                    link = procedural_form.cleaned_data
                    record_procedural_link(
                        matter=matter,
                        # Not asked, and not guessed either. `Uus teema` takes an
                        # address and an optional name for it; what the row is
                        # filed under is the enum's own value for a link nobody
                        # has classified, and the reasoning is on the constant
                        # (`ProceduralLinkCreateForm.STORED_KIND`,
                        # docs/adr/0094 §3).
                        kind=ProceduralLinkCreateForm.STORED_KIND,
                        url=link.get("url"),
                        label=link.get("label") or "",
                        actor=request.user,
                    )

                # The Teema's first step, from the one date the page asks for.
                #
                # `Arvamuse tähtaeg` does two things and this is the second: the
                # obligation is on the Matter, written by `create_matter` above from
                # the same `response_deadline`, and the step a lawyer will actually
                # see on their Minu asjad is established here. One box, one date,
                # both facts — which is what stopped this page asking the same
                # question twice under two names (docs/adr/0094 §5).
                #
                # There was a `Järgmiseks` call directly above this one, writing a
                # free-text first step through `set_next_action_for_new_work`. It is
                # gone with the panel: a first step is `Koostan arvamuse` on the
                # capture path, and a different plan is stated in the composer,
                # where changing one is an act with its own audit row
                # (docs/adr/0094 §6).
                #
                # **Inside this transaction**, which is the whole of docs/adr/0091
                # §1.3. A Teema that saved while its first step did not would be a
                # file the lawyer believes has a plan and every work surface says
                # has none; a step that saved while the Teema did not would be an
                # instruction attached to nothing. Either way the person is not
                # told. One transaction, so a refusal leaves neither and the
                # entered date comes back on the form.
                #
                # The service is idempotent against an equivalent open step, so a
                # retried POST cannot produce a second one (§1.3, Scenario H).
                #
                # `None` when the box was left empty, and that creates nothing: not
                # today, not the arrival date, not an undated commitment
                # (docs/adr/0078 §2, docs/adr/0091 §1.2).
                prepare_by = data.get("response_deadline")
                if prepare_by is not None:
                    establish_opinion_preparation_action(
                        matter=matter,
                        prepare_by=prepare_by,
                        actor=request.user,
                        # The owner chosen a few rows up on this same form. A
                        # default only — `responsible_for_new_work` still refuses to
                        # file new work on a departed colleague (ADR 0036 §5).
                        responsible=data.get("owner"),
                    )

        except _FormAlreadySubmitted as replay:
            return _answer_repeated_create(request, replay.session)
        except DomainError as error:
            # An ambiguous typed sender or addressee, or any other rule the
            # services refuse. The transaction is already rolled back by the
            # time this runs, so nothing — least of all a newly created
            # institution — survives the refusal (§6).
            #
            # The files do, though, and they have to: this is the refusal
            # somebody is most likely to hit twice while they work out which
            # institution the catalogue means, and it is no more a reason to
            # take their attachment away than a mistyped valdkond is.
            form.add_error(None, str(error))
            newly_held = pending_uploads.hold(request.session, chosen)
            return render(
                request,
                "matters/matter_create.html",
                _create_context(
                    request,
                    form,
                    procedural_form=procedural_form,
                    held_keys=[*held_keys, *(item.key for item in newly_held)],
                    # Always a token on the re-rendered form, so the corrected
                    # save is protected like the first one (ENG-074).
                    intake_session=intake_session
                    or intake_staging.open_session(owner=request.user),
                ),
                status=400,
            )

        # The save survived, so the hold is over. Released after the commit
        # rather than inside it: deleting the held bytes is not part of the
        # business operation, and a delete that failed must not take a written
        # Matter with it.
        pending_uploads.release(request.session, held_keys)
        # And the staging is over. After the commit for the same reason: the
        # Teema is written, its evidence is written, and a storage backend
        # having a bad minute must not take either with it. Anything left
        # behind is the sweeper's (`consume_session`).
        if intake_session is not None:
            intake_staging.consume_session(intake_session)

        filed = len(uploads) + len(promoted)
        if filed:
            messages.success(
                request,
                f"Teema „{matter.title}” on loodud koos {filed} failiga.",
            )
        else:
            messages.success(request, f"Teema „{matter.title}” on loodud.")
        # Straight into the file: creation is the start of work, not the end.
        return redirect("matters:matter_detail", pk=matter.pk)

    # GET only. Every POST above returns: refused ones answer 400 from the
    # branch that holds their files, and a successful one redirects. A refusal
    # answering 200 used to make the three refusals on this page
    # indistinguishable to anything reading the status rather than the HTML.
    #
    # Each rendered form carries its own session: the staging for its files and
    # its one-time submission token (ENG-074, `intake_staging.open_session`).
    return render(
        request,
        "matters/matter_create.html",
        _create_context(
            request,
            form,
            procedural_form=procedural_form,
            intake_session=intake_staging.open_session(owner=request.user),
        ),
        status=200,
    )


class _FormAlreadySubmitted(Exception):
    """Raised inside `matter_create`'s transaction to leave it writing nothing."""

    def __init__(self, session: Any) -> None:
        super().__init__("form already submitted")
        self.session = session


#: What a repeated submission of an already saved `Uus teema` form is told.
CREATE_FORM_ALREADY_SUBMITTED = "See vorm on juba salvestatud — teist teemat ei loodud."


def _answer_repeated_create(request: HttpRequest, session: Any) -> HttpResponse:
    """The Teema the first submission created, not a second one.

    Where the first submission's Teema is still readable to this person, the
    answer is the page the first submission would have landed on, with a
    sentence saying why; otherwise the register. Never an error page — a
    retried request is an ordinary event, and what the person wanted exists.
    """
    messages.info(request, CREATE_FORM_ALREADY_SUBMITTED)
    matter_id = getattr(session, "matter_id", None)
    if (
        matter_id is not None
        and Matter.objects.visible_to(request.user).filter(pk=matter_id).exists()
    ):
        return redirect("matters:matter_detail", pk=matter_id)
    return redirect("matters:matter_list")


def _create_context(
    request: HttpRequest,
    form: Any,
    *,
    # **Keyword-only, all of them, and that is the point.**
    #
    # `Uus teema` grew two optional blocks in one round — `Koostan arvamuse` from
    # docs/adr/0091 and `Menetluse link` from docs/adr/0089 — and while they were
    # positional the merge that brought them together handed each block the
    # other's form. Both were `Any`, so nothing complained; what a person saw was
    # a refused save with everything they had typed gone from the page. One of
    # those two forms is left and it is still keyword-only, because the reason was
    # never the count.
    procedural_form: Any = None,
    held_keys: list[str] | None = None,
    intake_session: Any = None,
) -> dict[str, Any]:
    return {
        # `Menetluse link`. Bound on a refusal and unbound on a GET, like the
        # main form itself, so an address somebody pasted survives a rejected file
        # or a mistyped valdkond — the property the whole refusal path on this
        # page exists to keep (docs/adr/0089 §13).
        #
        # The partial reads it under the same name the Teema page's launcher
        # panel reads its own form by, because the two are the same block asking
        # the same question and a second spelling would be a second place for the
        # template to drift.
        "procedural_link_form": procedural_form,
        # The form's own answers, so a refused save's redisplay does not propose
        # a sender over one the person has already given. On a GET the form is
        # unbound and `answered_on` is empty, which is what it was before
        # (R2-03, `app.matters.intake_suggestions.analysis.CurrentValues`).
        **_intake_context(intake_session, answered=CurrentValues.answered_on(form)),
        "form": form,
        # The files a refusal is holding, described for the page: the same
        # filename and size the browser's own preview shows, plus the key the
        # next attempt carries them back on. Empty on every GET, so a fresh form
        # never offers somebody an attachment they abandoned an hour ago
        # (app/documents/pending.py).
        "held_files": pending_uploads.describe(request.session, held_keys or []),
        # `action_form` and `opinion_action_form` were here, and both are gone with
        # the blocks they drew. `Järgmiseks` is not a question `Uus teema` asks any
        # more, and `Arvamuse tähtaeg` is a field on `form` — so the page has one
        # form for the Teema and one for the link, and nothing on it can be handed
        # somebody else's (docs/adr/0094 §5, §6).
        "frequent_senders": getattr(form, "frequent_senders", []),
        # `secondary_fields` is gone with the disclosure it fed. The template
        # named the primary fields and looped this tuple for the rest, which was
        # the right shape while the rest were hidden behind "+ Täpsusta teema
        # andmeid". Nothing on the page is hidden now, so every field is placed
        # by name — and a field added to the form later has to be placed
        # deliberately rather than appearing in a panel nobody opened
        # (Uus teema redesign §3).
        "today": timezone.localdate(),
        # `quick_dates` was here, for the `Millal?` chip row inside `Järgmiseks`.
        # There is no such row on this page: the one date it asks is typed, and
        # `Täna` / `Homme` / `+1 nädal` / `+2 nädalat` belong to the composer,
        # which still gets them from `quick_date_choices` in Europe/Tallinn
        # (docs/adr/0094 §6, ADR 0052 §4).
        # Which offered suggestions the person had explicitly chosen, handed
        # back untouched so a save refused for some *other* reason does not also
        # forget their decisions.
        #
        # **Echoed, never interpreted.** The server does not read this, does not
        # validate it against the analysis and does not store it: whether a
        # suggestion is in use is a fact about an unsaved form, and the browser
        # is the only thing that knows it. Deriving it here from «does the field
        # equal the suggestion» is exactly the guess this exists to avoid — a
        # person may have typed the same words by hand
        # (docs/adr/0064 amended 2026-09-12, static/js/app.js).
        "suggestion_state": _echoed_suggestion_state(request),
        "nav_active": "teemad",
    }


#: Long enough for five fields of chosen values and their baselines, short
#: enough that a hand-made POST cannot make the next render enormous. Over the
#: limit the page comes back with no remembered choices, which is the behaviour
#: before this existed and costs a click.
SUGGESTION_STATE_LIMIT = 4000


def _echoed_suggestion_state(request: HttpRequest) -> str:
    """The browser's own unsaved selection state, on its way back to it."""
    if request.method != "POST":
        return ""
    value = request.POST.get("suggestion_state", "")
    return value if len(value) <= SUGGESTION_STATE_LIMIT else ""


# ---------------------------------------------------------------------------
# Uus teema: reading the files while the form is still open
# ---------------------------------------------------------------------------
#
# Three small routes and one fragment. Selecting a file on `Uus teema` uploads
# it before the Teema exists, the extraction worker reads it through exactly the
# parsers and the scan gate every other file goes through, and what the rules
# find appears on the form the person is still filling in (docs/adr/0064).
#
# What none of them do is create business data. No Matter, no Document, no
# version, no audit event, no search row, no Organisation. The only thing that
# exists after any of them is one person's staging session, which expires.


#: How each staged file's reading state reads on the page, and which tone it
#: takes. Words a person filing a Teema can act on, never the stored state:
#: PENDING, PROCESSING and «Teksti eraldamine ei kohaldu» are how the extraction
#: system talks to an operator, and on this surface they are noise at best
#: (task §5, `app.documents.preview` is the operator-facing vocabulary).
INTAKE_STATE_LABELS: dict[str, tuple[str, str]] = {
    ExtractionState.PENDING: ("Loen faili…", "waiting"),
    ExtractionState.PROCESSING: ("Loen faili…", "waiting"),
    ExtractionState.DONE: ("Loetud", "ok"),
    ExtractionState.FAILED: ("Ei saanud lugeda", "warn"),
    ExtractionState.NOT_APPLICABLE: ("Sisu ei loeta", "quiet"),
}


def _intake_context(
    session: Any, *, errors: Sequence[str] = (), answered: CurrentValues | None = None
) -> dict[str, Any]:
    """Everything the intake fragment renders, decided here rather than there.

    One read of the staged files answers all three questions the page asks —
    what is on the form, whether anything is still being read, and what the
    rules found — so the template judges nothing and a poll costs one pass.

    **The analysis runs only once something has been read.** While every file is
    still PENDING there is provably nothing to suggest, and running the analyser
    anyway would load the organisation catalogue and the policy vocabulary on
    every poll to produce an empty answer.
    """
    # Whether this page may say anything at all about what is *in* the files.
    #
    # Read once and consulted three times below, because the withdrawal has to
    # be one decision: a panel suppressed while the prefill markers still
    # rendered would be exactly the "invisible automatic form mutation behind a
    # hidden UI" the product decision forbids, and a reading state printed with
    # nothing to report at the end of it is a spinner that promises something
    # that is never coming (docs/adr/0091).
    offered = create_form_suggestions_offered()

    files = intake_staging.live_files(session) if session is not None else []
    rows = []
    for staged in files:
        label, tone = INTAKE_STATE_LABELS.get(staged.extraction_state, ("Loen faili…", "waiting"))
        rows.append(
            {
                "id": staged.pk,
                "filename": staged.original_filename,
                "size": human_size(staged.size_bytes),
                # «Loen faili…» / «Loetud» describe a reading whose result this
                # page no longer shows. With suggestions withdrawn the staged
                # row says what the browser's own preview and the held-file list
                # say — a name, a size and a way to drop it — because that is
                # all that is true about it here (task §1, §6).
                "label": label if offered else "",
                "tone": tone if offered else "quiet",
            }
        )

    if not files:
        state = "empty"
    elif offered and any(staged.is_reading for staged in files):
        state = "reading"
    else:
        # Never `reading` with the suggestions withdrawn, and that is what stops
        # the poll rather than a second switch in the browser: `schedule()` asks
        # again only while the panel says `reading`, so a page that never says
        # it stages its files, renders their rows once and then leaves the
        # network alone (static/js/app.js).
        state = "ready"

    assisted = None
    prefill: list[tuple[str, str]] = []
    if offered and any(staged.extraction_state == ExtractionState.DONE for staged in files):
        assisted = analyse_intake(session)
        # The same pre-fill decision `Muuda teemat` makes, asked here so that
        # the two surfaces cannot drift apart about which confidence may fill a
        # control. What differs is only who applies it: there the GET renders it
        # into an unbound form, here the browser writes it into a live one and
        # only where the person has not (task §10, §11).
        #
        # **And the analysis the panel renders is the unannotated one.** That is
        # the whole of the difference and it is deliberate. `prefill_initial`
        # marks the candidates it chose so the edit page can print «vormil
        # eeltäidetud» beside exactly those — true there, because that page
        # filled the control itself. Here the server proposes and the *browser*
        # decides, and it declines wherever somebody has already typed. Printing
        # «vormil eeltäidetud» over a box holding a person's own value would be
        # the page stating something it cannot know, and it would take away the
        # «Kasuta» they would need to change their mind.
        #
        # So every candidate is offered, and the button says what happened: it
        # is bound to the live control, so one the browser filled reads as
        # chosen and one it declined reads as available (static/js/app.js,
        # `bindSuggestionUse`).
        # `allow_title=True`, and only here. On `Uus teema` the browser can
        # see what no saved record can — whether the person has typed in the
        # Pealkiri box — so a strong formal title may fill an empty untouched
        # one, and may never touch anything else
        # (app/matters/intake_suggestions/prefill.py, task §12).
        #
        # `answered` is what the *bound* form already says, and it is empty on
        # every GET and every poll. The browser is the right judge of a live
        # control it can see somebody typing into; it is not the judge of a page
        # that has just been re-rendered, because its own record of what has
        # been touched went with the old document. A refused save used to come
        # back proposing a sender over the one the person had typed and
        # committed through `+`, and the next save then persisted both (R2-03).
        _initial, decided = prefill_initial(
            assisted,
            base={},
            current=answered or CurrentValues(),
            allow_title=True,
        )
        prefill = prefill_controls(decided)

    unreadable = ""
    if offered and files and not any(staged.is_reading for staged in files) and assisted is None:
        # Every file finished and none of them produced text. Said as one calm
        # sentence rather than as a per-file error: what a person needs to know
        # is that the automatic help is not coming and that nothing is lost
        # (task §21).
        unreadable = "Faili sisu ei õnnestunud automaatselt lugeda."

    return {
        "intake_session": session,
        "intake_files": rows,
        "intake_state": state,
        "intake_prefill": prefill,
        # Every refused file, one line each. A tuple rather than one joined
        # sentence, because the panel prints them as separate lines and the
        # create form hands each to `messages.error` on its own (QA-02).
        "intake_errors": tuple(errors),
        "intake_unreadable": unreadable,
        "assisted": assisted,
        # The template renders the reading's own furniture — the stalled and
        # abandoned sentences, the «Jätka ilma automaatse lugemiseta» button —
        # on every visit and lets CSS decide which is on screen. With the
        # reading withdrawn none of those states is reachable, so the markup
        # for them is not rendered either: a page that cannot stall must not
        # carry a sentence about stalling (`intake_panel.html`).
        "intake_suggestions_offered": offered,
    }


def _intake_fragment(
    request: HttpRequest, session: Any, *, errors: Sequence[str] = (), status: int = 200
) -> HttpResponse:
    return render(
        request,
        "matters/partials/intake_region.html",
        _intake_context(session, errors=errors),
        status=status,
    )


def _requested_intake_session(request: HttpRequest, source: Any) -> Any:
    """The staging session this request names, if it is this person's and open.

    Fail-closed in one place. Somebody else's session, an expired one and one a
    Matter has already consumed all read as absent, and the caller answers 404 —
    the refusal this application gives for a record somebody may not touch, so
    that a guessed identifier learns nothing, not even whether it named a row
    (`app.core.decorators`, task §23).
    """
    return intake_staging.get_session(
        owner=request.user, session_id=(source.get("intake") or "").strip()
    )


@login_required
@business_write_required
@require_http_methods(["POST"])
def intake_stage(request: HttpRequest) -> HttpResponse:
    """Keep the files somebody just chose, so the extractor may read them.

    The same validator the save path uses, on the same bytes: `read_upload`
    checks the size, the extension allowlist and the content signature, and a
    file that fails it is refused here exactly as it would be at `Loo teema`.
    Nothing is parsed in this request — the worker does that, behind the scan
    gate, in its own process (docs/adr/0014, docs/adr/0064).
    """
    session = _requested_intake_session(request, request.POST)
    accepted, refusals = _read_new_matter_files(request)
    if not accepted and session is None:
        # Nothing to keep and nothing to keep it in. Answered rather than
        # refused, so the page can show why the file was not taken.
        return _intake_fragment(request, None, errors=refusals, status=400 if refusals else 200)

    result = intake_staging.stage_uploads(owner=request.user, uploads=accepted, session=session)
    # Both, when there are both: the files this request could not read, and
    # whatever the staging service then refused — a limit on how many may be
    # held at once is a different problem from a file that is not a PDF, and a
    # person holding one of each has to be told about both.
    return _intake_fragment(
        request,
        result.session,
        errors=refusals + ((result.refusal,) if result.refusal else ()),
        status=400 if refusals else 200,
    )


@login_required
@business_write_required
@require_http_methods(["GET"])
def intake_status(request: HttpRequest) -> HttpResponse:
    """Where the reading has got to, and what has been found so far.

    A read, and small: the staged rows and whatever the rules have to say. It
    carries no file bytes and re-reads none — the point of polling this rather
    than anything else is that it costs a couple of indexed queries whatever
    the files weigh (task §22, §37).
    """
    session = _requested_intake_session(request, request.GET)
    if session is None:
        raise Http404
    return _intake_fragment(request, session)


@login_required
@business_write_required
@require_http_methods(["POST"])
def intake_remove(request: HttpRequest) -> HttpResponse:
    """Take one staged file back off the form.

    Answered with the same fragment as everything else here, so the list, the
    suggestions and what `Loo teema` would promote are recomputed together and
    cannot disagree. A file removed while it was being read stops contributing
    immediately: the analysis reads the live set (task §17).
    """
    session = _requested_intake_session(request, request.POST)
    if session is None:
        raise Http404
    if not intake_staging.remove_file(session=session, file_id=(request.POST.get("fail") or "")):
        raise Http404
    return _intake_fragment(request, session)


def _read_new_matter_files(request: HttpRequest) -> tuple[list[Any], tuple[str, ...]]:
    """Read and validate every attachment before a single row is written.

    Reading is what validates: `read_upload` enforces the size, the MIME type
    and the signature rules the rest of the system already relies on. Doing all
    of it up front is the whole point — a Matter created with three of four
    files, and an error message about the fourth, is worse than no Matter.

    Returns what passed *and* **every** refusal, each naming the file it is
    about. It used to return the first one only, and unnamed: somebody who chose
    four files, two of them unusable, saw one file in the list and one line of
    red that did not say which file it meant — so they could not tell that a
    second one had been dropped, and found out about it one save at a time. That
    is precisely the failure this function reads everything up front to prevent,
    and stopping at the first refusal reintroduced it by a different door
    (adversarial QA 2026-09-12, QA-02).

    The good half of a batch still survives. The caller still refuses the save;
    what changes is that the person is told about all of what they have to
    replace, at once.
    """
    from app.documents.uploads import read_upload

    accepted: list[Any] = []
    refusals: list[str] = []
    for handle in request.FILES.getlist("files"):
        if not handle:
            continue
        try:
            accepted.append(read_upload(handle))
        except (DomainError, UploadRejected) as error:
            # The filename first, because a refusal that does not name its file
            # is unactionable the moment there is more than one. An em dash
            # rather than a colon: the reason is already a sentence of its own.
            refusals.append(f"{handle.name} — {error}")
    return accepted, tuple(refusals)


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
    # One scoped read of the structured facts, shared by the chronology, the
    # process strip and whatever else asks. Built before the timeline rather than
    # beside it so the three surfaces cannot ask differently scoped questions
    # about one Matter — the rule `matter_intelligence` itself was written for.
    intelligence = matter_intelligence(matter, request.user)
    # `selectors.current_action_of`, not `workflow.services.current_next_action`.
    # The service answers "which action is open on this Matter" for the domain,
    # which is a question about the file; a *page* asks "which action may this
    # reader see", and a `NextAction` can be restricted below the Matter it
    # hangs off. Read unscoped, the Järgmiseks row printed a restricted step's
    # text and date to anybody who could open the Matter — the same leak
    # `open_action_prefetch` closed on the register row (AUTH-003).
    #
    # Read before the chronology and handed to it, because the history has to
    # know which step is open in order *not* to print it a second time, and two
    # differently scoped answers to that on one page would be a row appearing or
    # vanishing for reasons a reader could not see (docs/adr/0092 §8).
    current_action = selectors.current_action_of(matter, request.user)
    # The file's pattern and current stage, resolved **once** for both surfaces.
    phases = legal_process.phase_context(matter=matter)
    items, has_more = matter_timeline(
        matter=matter,
        user=request.user,
        limit=TIMELINE_PAGE_SIZE,
        only=timeline_only,
        intelligence=intelligence,
        current_action=current_action,
    )
    # Each `Kaasamine` row on the chronology gets its own `Lõpeta kaasamine`
    # form, with its own ids and its own revision token. Here rather than in the
    # template because a template must not build a form, and on the record
    # rather than in this dict because there are many rows (`attach_feedback_form`).
    for item in items:
        if item.is_engagement:
            attach_wait_form(attach_feedback_form(item.record))
    # The register's own `JÄRGMISEKS`, and only where no structured action
    # exists. Read here rather than in the template so the page cannot start
    # asking the database a question of its own — and read *conditionally*,
    # because on a Matter that has a real next step neither answer is rendered
    # and both are a query for nothing (ADR 0021).
    source_instruction = "" if current_action else source_instruction_for(matter)
    # Built before the dict because two entries read it: the strip renders them
    # and `opinion_sent` below asks whether one of them is a sent opinion. Calling
    # `process_steps` twice would be two reads of the same scoped question.
    steps = process_steps(matter=matter, user=request.user, intelligence=intelligence)
    # Built once and read twice: the rail draws its nodes and `matter_rail`
    # merges the dated points into them.
    rail = legal_process_rail(matter=matter, user=request.user, context=phases)
    return {
        # `Menetluse kulg` — where the external procedure stands, which one to
        # three phases may follow, and the dated points the file actually holds.
        # A deterministic read-only projection over `Hetkeseis`, the explicit
        # stage history and — to choose the pattern — `Menetlusliik` or the
        # reviewed `Õigusakt` grouping. `None` where no pattern can be chosen or
        # where the file records nothing that places it on one, and the section
        # is then not rendered at all (app/matters/legal_process.py).
        "legal_process": rail,
        "legal_process_stopped_label": KODA_STOPPED_LABEL,
        # **The one rail.** Phases and the dated points the file holds, in one
        # ordered list — the second strip is gone and its content is here, still
        # read by `process_timeline.process_steps` and handed over rather than
        # read again (app/matters/legal_process.py `matter_rail`).
        "rail_steps": matter_rail(matter=matter, user=request.user, rail=rail, milestones=steps),
        "matter": matter,
        "current_action": current_action,
        "source_instruction": source_instruction,
        "source_snapshot": snapshot_label() if source_instruction else "",
        "timeline_items": items,
        # `Teema käik` — the file's course above the chronology that explains
        # it: `Tagasiside tähtaeg`, `Koja arvamus`, `Arvamuse tähtaeg`,
        # `Jõustumine`, `Lõpetatud`, the pattern's own phases, and nothing else.
        # `Alustatud` was the sixth until docs/adr/0100 §1 retired it.
        #
        # The same `intelligence` the chronology reads is handed over rather
        # than re-read, because `Jõustumine` is a structured fact with its own
        # visibility and the two surfaces must not ask differently scoped
        # questions about one Matter. `Oluline tähtaeg`
        # is deliberately not among them and keeps its own section
        # (app/matters/process_timeline.py, docs/adr/0074 §12).
        "process_steps": steps,
        # **Whether an opinion has gone out, and therefore whether the file needs
        # a sentence about what happens next.**
        #
        # The dead end the first lawyer test found: a Matter whose opinion was
        # sent and whose step was finished read «Järgmine samm on määramata» and
        # offered nothing that looked like a continuation, so the procedure
        # carrying on elsewhere — a revised draft, a committee, an adoption — had
        # no obvious home and lawyers opened new Matters for it (lawyer
        # feedback 14, docs/adr/0091 §5.5).
        #
        # Read off the strip that is already built rather than as a query of its
        # own: `process_steps` has just resolved every SENT `Submission` this
        # reader may see, and asking the database the same question again would be
        # a second read for an answer already in hand. It is `visible_to`-scoped
        # there, so a submission restricted below the Matter draws no column here
        # and puts no sentence on the page either (AUTH-003).
        "opinion_sent": any(step.label == SENT_LABEL for step in steps),
        # No `timeline_rows` and no `timeline_preview`. The approved target has
        # two row kinds and no folded system runs, and its `Ajajoon` head is the
        # label and the count — the preview sentence and the duplicated current
        # step are gone from it (docs/adr/0074 §16).
        "timeline_has_more": has_more,
        "timeline_count": len(items) + (1 if has_more else 0),
        "timeline_only": timeline_only,
        "timeline_filters": TIMELINE_FILTERS,
        # The eight workspace forms, unbound. `PRAEGUNE TEGEVUS` takes one and
        # `LISA TEEMALE` takes the rest; a refused save replaces exactly one of
        # them with its bound self and opens that panel alone
        # (docs/adr/0075 §2, `workspace_forms`).
        **workspace_forms(current_action, matter=matter, viewer=request.user, phases=phases),
        # The superseded composer, still built for the endpoint that still
        # accepts it. Nothing on this page renders it any more
        # (docs/adr/0075 §11).
        "composer_form": ComposerForm(matter=matter, viewer=request.user),
        # `summary_form` and `note_form` are deliberately absent: the header
        # context carries them, it is merged over this one, and reading the
        # private note twice per page is two queries for one answer.
        "historical": _historical_context(matter, request.user),
        # Stage 2G's structured facts. Read through their own selector, which
        # scopes them like every other child record.
        "intelligence": intelligence,
        # Seotud materjalid: the confirmed relations and chosen background,
        # scoped to this reader. Two queries; the suggestions are not read
        # here at all (app/related_materials/selectors.py).
        "related_materials": related_materials_for(matter, request.user),
        # What the register observed *around* the outreach: how many members
        # were asked and how many answered, whether the opinion went out, and
        # whether KELLELE named more bodies than the canonical field can hold.
        #
        # Decided in Python because a template cannot tell `None` from `0`, and
        # that distinction is the whole point of the two counts: `{{ value|
        # default:"—" }}` renders a measured zero as a missing one
        # (`register_display.MemberFeedback`).
        "register_facts": register_facts_for(matter, request.user),
        # The standing `Kaasamine` section's state is gone with the section.
        # `engagement_rows`, `engagement_count`, `engagement_form`,
        # `engagement_editing`, `engagement_open` and `engagement_add_open`
        # described a list-plus-composer that docs/adr/0074 §9 folded into
        # `+ Kaasamine` and the chronology; the three templates reading them
        # had not been included by anything since, and they are removed with
        # this round rather than left to render an `EngagementForm` whose new
        # `Tagasisidet ootame kuni` box they know nothing about. A consultation
        # is read on its chronology row and corrected in place there
        # (`matters/partials/engagement_row.html`).
        # `Ülevaated / uudised`, and only the ones this Matter still owes.
        #
        # Read here for the reason everything else on this dict is: the template
        # must not be able to start querying. Planned rows only — a published
        # overview reads in the chronology, off the record that holds it, and a
        # strip that listed both would state one fact in two places
        # (docs/adr/0081 §4).
        #
        # The strip renders nothing at all when this is empty, which is the
        # ordinary case: there are no permanently visible empty sections on this
        # page (TEEMA_TARGET_SPEC §F).
        # `(record, form)`, one pair per planned row. The form is this row's own
        # — its ids are derived from the record's id, because a Matter may owe
        # several write-ups and two controls sharing an id is enough to make a
        # label reach the wrong box — and it carries the row's revision token, so
        # a publication cannot land on a plan that has moved on
        # (`_planned_website_overview_rows`).
        "planned_website_overviews": _planned_website_overview_rows(matter, request.user),
        # What a refused `Avalda` came back as. Empty on an ordinary render; a
        # refusal replaces the pair for its own row and fills these two
        # (`_website_overview_refusal`).
        "website_overview_open": "",
        "website_overview_error": "",
        # `Menetluse lingid` — where this Matter's official proceedings live.
        #
        # Read here for the reason everything else on this dict is: the template
        # must not be able to start querying. Every row, not a filtered subset,
        # because a procedural link has no lifecycle and no state that would
        # make one of them not worth showing — a Matter carries the addresses
        # it carries (docs/adr/0089 §7).
        #
        # The card renders nothing at all when this is empty, which is the
        # ordinary case on a Matter nobody has recorded one for: there are no
        # permanently visible empty sections on this page, and the one compact
        # add affordance is the launcher's own chip (TEEMA_TARGET_SPEC §F).
        #
        # `(record, form)`, one pair per row. The form is this row's own — its
        # ids are derived from the record's id, because a Matter may carry
        # several links and two controls sharing an id is enough to make a label
        # reach the wrong box — and it carries the row's revision token, so a
        # correction cannot land on a link that has moved on
        # (`_procedural_link_rows`).
        "procedural_links": _procedural_link_rows(matter, request.user),
        # What a refused `Paranda` came back as. Empty on an ordinary render; a
        # refusal replaces the pair for its own row and fills these two
        # (`_procedural_link_refusal`).
        "procedural_link_open": "",
        "procedural_link_error": "",
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
        # The official `Arvamuse tähtaeg`, where `PRAEGUNE TEGEVUS` is showing a
        # plan instead of it. An open `Järgmiseks` is the current work and stays
        # primary (docs/adr/0050); what it never said is whether Koda has
        # answered, and until an opinion goes out or the register records the
        # opinion work as finished it has not (PR #205).
        #
        # The primary date this is measured against is the open step's own, and
        # the Matter's deadline where there is no step — because then the header
        # metaline directly above is already stating that date in full, and a
        # second line would be the same day twice
        # (`work_items.secondary_response_obligation`).
        #
        # An approximate step never suppresses it. *Plaanis IV kvartal 2026*
        # anchors on 1 October, and a Matter whose `Arvamuse tähtaeg` is that
        # day would otherwise lose its official line to a number nobody put on
        # the screen (docs/adr/0079 §12).
        # `Tagasisidet ootame kuni`'s quick spans, resolved to real days here so
        # the chips can print the date each one lands on.
        "feedback_deadline_choices": feedback_deadline_choices(timezone.localdate()),
        # The consultation rounds this file is still waiting on, newest deadline
        # last. Read here rather than in the template, like everything else on
        # this dict, and read through the child's own `visible_to`: a restricted
        # round must not put a line on `PRAEGUNE TEGEVUS` for a reader who may
        # not open it (AUTH-003, docs/adr/0086 §4).
        #
        # Beside the open step rather than instead of it. Both are true, both
        # are the reader's, and a page that showed one of them would be choosing
        # which of two facts about their day to withhold.
        "feedback_waits": list(
            work_items.open_feedback_waits(request.user)
            .filter(matter=matter)
            .order_by("feedback_deadline", "pk")
        ),
        "response_obligation": work_items.secondary_response_obligation(
            matter,
            request.user,
            primary_date=(
                current_action.target_date
                if current_action is not None
                else matter.response_deadline
            ),
            primary_is_approximate=(current_action is not None and current_action.is_approximate),
        ),
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


def _legal_instrument_line(matter: Matter) -> list[str]:
    """`Õigusakt` as the rail reads it: the labels, with `Muu` carrying its text.

    **The fact this application asked for and never once read back.** `Uus
    teema` asks which instrument a Matter concerns, `Muuda teemat` corrects the
    answer, the search index carries it and `Seotud materjalid` matches on it —
    and no surface a lawyer *reads* a Matter from showed it at all. Somebody
    could file a Matter as a `Määrus`, reopen it the next morning, and find
    nothing on the page saying they had (post-QA R2-08).

    `Muu` is a real vocabulary row here rather than a checkbox beside one
    (docs/adr/0070 §8), so `legal_instrument_other` is folded *onto* that row —
    `["Määrus", "Muu: rohepöörde tegevuskava"]` — rather than appended after it
    as a fourth nameless value. Deciding that here keeps the vocabulary's own
    key out of a template, which is the one place a rename would not be found.

    Free text with no `Muu` row is a state both forms refuse and no importer
    produces. It is still rendered, on a row of its own, because a rendering
    that silently drops a stored value because its shape was unexpected is how
    a data problem becomes invisible.

    Ordered by the vocabulary's `sort_order`, so two Matters carrying the same
    pair list them the same way round. Read here rather than in the template for
    the reason `matter_policy_areas` is: the rail renders on all three Matter
    surfaces, and `.all()` inside a loop would be a query per render of each.
    """
    other = (matter.legal_instrument_other or "").strip()
    labels: list[str] = []
    matched = False
    for instrument in matter.legal_instruments.all():
        # Onto the *first* `Muu` row only. There are three of them now — version
        # 1.0's `Muu` and the reviewed `Muu siseriiklik` and `Muu ELi dokument`
        # — and `legal_instrument_other` is one column, so a Matter carrying two
        # of them would otherwise read as having said the same sentence twice
        # (docs/adr/0090 §3).
        if instrument.key in OTHER_LEGAL_INSTRUMENT_KEYS and other and not matched:
            labels.append(f"{instrument.label_et}: {other}")
            matched = True
        else:
            labels.append(instrument.label_et)
    if other and not matched:
        labels.append(f"Muu: {other}")
    return labels


def _header_context(
    request: HttpRequest, matter: Matter, *, milestones: Any = None
) -> dict[str, Any]:
    # One read for the private note: its body fills the box and its `updated_at`
    # fills `Salvestatud HH:mm`.
    note_record = personal_note_record(matter=matter, author=request.user)
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
        # The offered vocabulary plus whatever this Matter already stands in,
        # for the reason the owner list above is widened: a select offering less
        # than the form accepts refuses the value it is displaying, and
        # `Hetkeseis` being optional that refusal would read as a cleared stage
        # rather than as an error (app/workflow/selectors.py, docs/adr/0032
        # §Amendment).
        "stages": stages_including(matter.stage),
        "organisations": Organisation.objects.order_by("name"),
        # Resolved once here rather than read off the Matter inside the loop
        # over every organisation: the sender checkboxes iterate the whole
        # reference table, and a membership test that re-queried per row would
        # be an N+1 nobody notices until the institution list grows.
        "selected_sender_ids": matter.source_organisation_ids,
        # No `tracks` and no `visibilities`. The rail rows that read them are
        # gone with the questions the two Teema forms stopped asking, and a
        # vocabulary handed to a template that draws no control is how a
        # withdrawn question survives its own removal (docs/adr/0097 §3).
        # No `current_action` either. The header band no longer shows the next
        # step — the Järgmiseks row does — and the overview context reads it
        # once for both.
        # The governed vocabulary plus whatever this Matter already carries, so
        # a file classified years ago under a retired area still shows it and
        # can still be corrected (app/taxonomy/vocabulary.py).
        "policy_area_choices": selectable_policy_areas(),
        "selected_policy_area_ids": {area.pk for area in matter.policy_areas.all()},
        # What each inline whole-value editor was rendered against, posted back
        # with it and checked under the Matter lock (ENG-028).
        "policy_areas_revision": matter_field_revision(matter, "policy_areas"),
        "senders_revision": matter_field_revision(matter, "source_organisations"),
        "summary_revision": matter_field_revision(matter, "brief_summary"),
        "matter_policy_areas": list(matter.policy_areas.all()),
        "matter_legal_instruments": _legal_instrument_line(matter),
        # The one deadline the header shows, chosen by the rule in §5.5 rather
        # than by the template picking whichever field is non-empty.
        # `milestones` when the caller has already read them, which the Matter
        # page has: `Olulised tähtajad` renders from the same rows.
        # **The header's `Tähtaeg` is `Arvamuse tähtaeg`, and only that.** The
        # approved target reads `Saabus` and `Tähtaeg` as a pair — when it
        # arrived, when Koda's answer is due — so the slot cannot be filled by
        # whichever `MatterImportantDate` happens to be nearest
        # (TEEMA_TARGET_SPEC §B, docs/adr/0074 §2).
        #
        # `active_deadline` is untouched and still answers the broader question
        # for the surfaces that want it; `milestones` is still passed so it costs
        # no second query where it is read.
        "active_deadline": selectors.active_deadline(matter, request.user, milestones=milestones),
        "response_deadline": selectors.response_deadline_of(matter, request.user),
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
            initial={
                "body": note_record.body if note_record is not None else "",
                # Which version this box was filled from, so the autosave can be
                # refused rather than allowed to overwrite a newer one (QA-09).
                "revision": personal_note_revision(note_record),
            },
        ),
        # When this reader's own note was last written, for the `Salvestatud
        # HH:mm` hint. `None` on a Matter they have never made a note on, and the
        # hint renders nothing at all rather than a placeholder.
        "note_saved_at": note_record.updated_at if note_record is not None else None,
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
    change does not touch (docs/adr/0047, docs/adr/0061).

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


#: What each Dokumendid filter is called on the chip that removes it.
DOCUMENT_FILTER_LABELS: dict[str, str] = {
    "otsi": "Otsing",
    "roll": "Roll",
    "aasta": "Aasta",
}


def _document_filter_chips(params: Any, *, roll_labels: dict[str, str]) -> list[dict[str, str]]:
    """The active Dokumendid filters, each with the link that takes it off.

    **A count under a heading has to say what it counted.** Attaching evidence to
    an opinion redirects to this page carrying `?roll=arvamus`, so `Failid` came
    back reading «1 faili» on a Matter holding nine — technically the filtered
    result count, and visually indistinguishable from the total. Nobody had
    asked for a filter; the redirect applied one on their behalf, and the only
    thing on the page admitting it was a `<select>` further up (post-QA R2-13).

    The filter is not removed: landing on the opinion you just filed, with the
    other eight files out of the way, is what the redirect is *for*. What is
    added is the sentence saying so, in the chip language the register already
    uses — name the filter, name its value, and make the chip itself the way
    back (`_active_filters`, templates/matters/partials/register_results.html).

    `roll` is displayed through the menu's own labels, so the chip reads
    «Roll: Arvamus» rather than «Roll: arvamus» or, worse, the stored
    `KODA_SUBMISSION_FINAL` a saved link may still carry.
    """
    chips: list[dict[str, str]] = []
    for name, label in DOCUMENT_FILTER_LABELS.items():
        value = (params.get(name) or "").strip()
        if not value:
            continue
        without = params.copy()
        without.pop(name, None)
        chips.append(
            {
                "name": name,
                "label": label,
                "value": roll_labels.get(value, value) if name == "roll" else value,
                "remove_query": without.urlencode(),
            }
        )
    return chips


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


#: Roles a person may not file a *new* upload as, however many the model holds.
#:
#: `OUTCOME_EVIDENCE` — «Tulemuse tõend» — is a claim about what happened to a
#: proposal after Koda wrote about it, and a file is almost never that at the
#: moment somebody is uploading it. On the menu it read as a plausible tenth
#: option beside nine descriptions of what a file *is*, and picking it filed a
#: document under an assertion nobody had made.
#:
#: An exclusion from a menu and nothing else. The value stays in
#: :class:`~app.documents.enums.DocumentRole`, documents already carrying it
#: stay valid and render their stored label everywhere they always did, the
#: `Roll` filter above still offers it so those documents remain findable, and
#: no migration is involved.
UPLOAD_ROLES_NOT_OFFERED: frozenset[str] = frozenset({DocumentRole.OUTCOME_EVIDENCE})


def _upload_role_choices() -> list[tuple[str, str]]:
    """The same relabelling for the upload panel, over the *stored* vocabulary.

    The filter may invent a value because it only has to survive a round trip
    through the query string. This select posts a `Document.role`, so every
    value here is a real one and only the words change — which is the whole of
    what this change does to the role: the user reads `Arvamus`, the database
    keeps `KODA_SUBMISSION_FINAL`, and no migration is involved (docs/adr/0061).

    Narrower than the filter in one respect: `UPLOAD_ROLES_NOT_OFFERED` is
    dropped here and nowhere else, so a role that is no longer a sensible thing
    to *choose* is still a role a stored document may *have*.
    """
    return [
        (value, "Arvamus" if value == DocumentRole.KODA_SUBMISSION_FINAL else label)
        for value, label in DocumentRole.choices
        if value not in UPLOAD_ROLES_NOT_OFFERED
    ]


def _document_display_name(document: Any) -> str:
    """What the row prints as the file's name — the title, or the filename.

    The same choice the template makes, made once here so the duplicate check
    and the rendering cannot disagree about which string is on the page.
    """
    if document.title:
        return str(document.title)
    version = document.current_version
    return str(version.original_filename) if version is not None else ""


def _mark_duplicate_names(documents: Sequence[Any]) -> None:
    """Give rows that print the same name something that tells them apart.

    Two different files both called `dup.txt` rendered as four cells identical
    in every visible column — name, role, date, person — and the stored bytes
    were correct and distinct all along. The defect was display ambiguity, so
    the fix is display (QA-016).

    **Nothing is rejected.** Identical filenames and identical bytes are both
    legitimate evidence acts: a ministry sends the same annex twice, a
    colleague files a copy under a second step. This only makes them
    distinguishable.

    **Human-readable, and nothing internal.** The upload's own clock time, which
    a person recognises from their mail client and their download folder, and
    the file's size — never a storage key, a UUID or a path. An ordinal is the
    last resort and is used only where two rows are otherwise identical down to
    the minute, because «(1)» says nothing about the file and is worth printing
    only when the alternative is silence.

    Set on rows the page already holds, so it costs no query: the duplicate
    check reads the same list that is about to be rendered.
    """
    seen: dict[str, list[Any]] = {}
    for document in documents:
        # Keyed in NFC: the same name spelled with decomposed letters is the
        # same name on the page, and exactly the twin this exists to mark
        # (ENG-088).
        key = unicodedata.normalize("NFC", _document_display_name(document))
        seen.setdefault(key, []).append(document)

    for rows in seen.values():
        if len(rows) < 2:
            continue
        marks: list[str] = []
        for document in rows:
            parts = [timezone.localtime(document.created_at).strftime("%H:%M")]
            version = document.current_version
            if version is not None and version.size_bytes:
                parts.append(human_size(version.size_bytes))
            marks.append(" · ".join(parts))
        for index, (document, mark) in enumerate(zip(rows, marks, strict=True), start=1):
            # An ordinal only where the mark itself repeats — two files of the
            # same size uploaded in the same minute.
            document.duplicate_hint = mark if marks.count(mark) == 1 else f"{mark} · {index}."


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
    the retired surface a third copy of the same letter (docs/adr/0061).

    The table is four columns: `Fail`, `Roll`, `Kuupäev`, `Lisas`. `Versioon`
    and `Maht` went the same way as the mechanics above, and the per-version
    prefetch went with them: nothing on this page reads a document's history any
    more, so asking for every version of every row was a query bought for a cell
    that is not rendered. The history is on the document's own page, which loads
    it for the one document a reader is looking at.
    """
    matter = get_visible_matter(request, pk)
    documents = (
        Document.objects.filter(matter=matter)
        .visible_to(request.user)
        .select_related("current_version", "created_by")
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
        # Both sides in NFC (ENG-088). The term as typed, and the stored names
        # at read time: a file named on a Mac holds `õ` as two code points, and
        # that row cannot be rewritten.
        needle = unicodedata.normalize("NFC", term)
        documents = documents.annotate(
            title_nfc=NFC("title"),
            filename_nfc=NFC("current_version__original_filename"),
        ).filter(Q(title_nfc__icontains=needle) | Q(filename_nfc__icontains=needle))
    if role == OPINION_ROLE_FILTER:
        documents = documents.filter(pk__in=opinion_ids)
    elif role in DocumentRole.values:
        documents = documents.filter(role=role)
    # A year PostgreSQL can compare with, or no year filter. `isdigit()` alone
    # let `999999999999` through to a `bigint out of range` (ENG-046); the
    # chip below names the year only when it was applied.
    applied_year = bounded_int(year, default=0, minimum=1, maximum=9999)
    year = str(applied_year) if applied_year else ""
    if applied_year:
        documents = documents.filter(created_at__year=applied_year)

    # The filters as they were actually applied, which is what the chips name
    # and what their removal links rebuild. `koik` is deliberately not carried:
    # taking a filter off starts the list again from the top, the same way the
    # register drops `leht` (`_active_filters`).
    applied_filters = QueryDict(mutable=True)
    for name, value in (("otsi", term), ("roll", role), ("aasta", year)):
        if value:
            applied_filters[name] = value

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
    _mark_duplicate_names(visible_evidence)

    # Opinion files this Matter holds that no Submission accounts for at all.
    # They are the candidates for «Registreeri saatmine», and the reason that
    # control exists at all: uploading a file as `Arvamus` records that Koda has
    # it, never that Koda sent it, and only a person can close that gap (§18).
    #
    # The rule lives in `unregistered_opinion_documents` rather than here,
    # because it also lived in `app/submissions/views.py` — and a candidate rule
    # written as two list comprehensions is one that gets fixed in one of them.
    # A draft's own final evidence is excluded by it: that file's correct
    # operation is `Märgi saadetuks` on the draft below, and offering it here as
    # well produced a second, parallel SENT Submission for the same bytes, on a
    # Matter that then read `1 koostamisel` beside a sent opinion of the same
    # text (R2-01).
    unregistered = unregistered_opinion_documents(matter, viewer=request.user)
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
            # Built from the *applied* values rather than from `request.GET`, so
            # the chip says what actually narrowed the table: a saved
            # `?roll=KODA_SUBMISSION_FINAL` link was read as the opinion union
            # above, and a chip echoing the raw parameter would name a filter
            # the page is not running (post-QA R2-13).
            "document_filter_chips": _document_filter_chips(
                applied_filters, roll_labels=dict(_role_filter_choices())
            ),
            "working_document_form": WorkingDocumentForm(),
            "can_write": may_write_business_content(request.user),
            # Two different questions, and this page has to ask both.
            #
            # `can_write` is about the **reader**: may this person record
            # business content anywhere. `can_add_content` is about the
            # **Matter** as well: a closed teema accepts no new canonical
            # content from normal interactive work — no upload, no new opinion,
            # no registered send, and no advancing of a draft that is sitting
            # there. Reopening it is how work continues, and the banner above
            # the table offers exactly that (docs/adr/0075 §12).
            #
            # It hides controls; it decides nothing. Every route behind them
            # takes the Matter's row lock and refuses a closed one on its own,
            # because the browser that posts may be holding a page from before
            # the closure (`app/submissions/services.py`,
            # `app/documents/services.py`).
            #
            # Reading is untouched: the files, the sent opinions, their
            # details, `Ava`, `↓` and the archive letters are all still here.
            "can_add_content": may_write_business_content(request.user) and matter.is_open,
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
    header_out_of_band: bool = False,
) -> HttpResponse:
    """Re-render the whole overview column.

    One render from one set of queries, so `Järgmiseks` and the timeline can
    never show different pictures of the same save.

    ``header_out_of_band`` appends the header band to the same response, marked
    `hx-swap-oob`, for the one save that changes the header wholesale — a
    closure, which moves the state badge. Every other answer carries two small
    out-of-band fragments instead, `Hetkeseis` and the `Dokumendid` count: the
    two header facts a column save can move, since `+ Märge` may set the stage
    and any save may carry a file (ENG-091). The rest of the header is left
    alone, because re-rendering it would rebuild five inline editors on every
    note somebody writes (docs/adr/0074 §10).
    """
    # The instance a view fetched before its service ran is not the row the
    # service locked and wrote: a `+ Märge` that moved the stage wrote it on
    # `locked_matter`, and this one would render the stage it had before.
    matter.refresh_from_db(fields=["stage"])
    context = _overview_context(request, matter)
    intelligence = context["intelligence"]
    context.update(
        _header_context(
            request,
            matter,
            milestones=[*intelligence.upcoming_dates, *intelligence.past_dates],
        )
    )
    body = render_to_string("matters/partials/overview.html", context, request=request)
    if header_out_of_band:
        context["header_out_of_band"] = True
        body += render_to_string("matters/partials/header.html", context, request=request)
    else:
        # The two facts in the header a column save can move: `Hetkeseis`, which
        # `+ Märge` may set, and the `Dokumendid` count, which any save carrying
        # a file raises. Each is its own small out-of-band fragment, so the
        # header states what the save just stored without the header's inline
        # editors being rebuilt under the person (ENG-091).
        context["stage_out_of_band"] = True
        context["tabs_out_of_band"] = True
        context["tab"] = "teema"
        body += render_to_string("matters/partials/header_stage.html", context, request=request)
        body += render_to_string("matters/partials/tabs.html", context, request=request)
    return HttpResponse(body, status=status)


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
    # **The header follows a closure out of band.**
    #
    # The composer swaps `#teema-vaade`, which is the action row, the chronology
    # and the rail — and deliberately not the header band, because a save that
    # only wrote a note has no business re-rendering the title, the metaline and
    # its five inline editors. A closure is the one thing this save does that the
    # header states: the state badge says `Avatud`, and it kept saying it beside
    # a Matter that had just been archived. A page showing contradictory state
    # after its own save is the defect HTMX swaps exist to avoid
    # (implementation brief §57, docs/adr/0074 §10).
    #
    # Out of band rather than by widening the target: `#teema-vaade` is what the
    # form must own, and a response that also replaced the header would re-render
    # every inline editor on every note somebody writes.
    return _render_overview(request, matter, header_out_of_band=not matter.is_open)


def _refused_overview(request: HttpRequest, matter: Matter) -> HttpResponse:
    """400, and the overview column as it actually stands."""
    context = _overview_context(request, matter)
    context.update(_header_context(request, matter))
    return render(request, "matters/partials/overview.html", context, status=400)


@login_required
@business_write_required
@require_http_methods(["POST"])
def add_engagement_view(request: HttpRequest, pk: Any) -> HttpResponse:
    """Record one `Kaasamine` on this Matter. A compatibility door.

    What a person uses is `+ Kaasamine`, whose refusals come back inside the
    panel they were typed in (`_workspace_refusal`). This route survives for
    the browsers still posting to it, and the standing `Kaasamine` section that
    used to render its bound form went with docs/adr/0074 §9 — so a refusal
    here is 400 and the column as it actually stands, which tells a stale tab
    that the save did not land rather than showing it a success it did not get.
    """
    matter = get_visible_matter(request, pk)
    form = EngagementForm(request.POST)
    if not form.is_valid():
        return _refused_overview(request, matter)

    try:
        record_engagement(
            matter=matter,
            # The vocabulary's own default, because this form no longer asks.
            # `Liik` was a classification nothing read back, and a door that
            # kept writing `Küsitlus` on rounds nobody described that way would
            # be the one surface still manufacturing the fact the panel stopped
            # asking for (docs/adr/0086 §1).
            kind=EngagementKind.OTHER.value,
            title=form.cleaned_data["title"],
            url=form.cleaned_data.get("url") or "",
            smaily_url=form.cleaned_data.get("smaily_url") or "",
            alchemer_url=form.cleaned_data.get("alchemer_url") or "",
            note=form.cleaned_data.get("note") or "",
            # The **resolved** date. On this route it is always the day box or
            # nothing, because a create has no stored period to preserve
            # (`app/matters/forms.py`, `EngagementForm.clean`).
            occurred_on=form.cleaned_data.get("occurred_on_value"),
            occurred_on_precision=form.cleaned_data["occurred_on_precision"],
            feedback_deadline=form.cleaned_data.get("feedback_deadline"),
            feedback_received=form.cleaned_data.get("feedback_received") or "",
            # Named here for the reason this door exists to serve: the form it
            # posts now carries `Vastuseid`, and a route that rendered a box and
            # dropped what was typed into it would be the defect QA-03 fixed,
            # one surface along.
            response_count=form.cleaned_data.get("response_count"),
            actor=request.user,
        )
    except DomainError:
        return _refused_overview(request, matter)

    return _render_overview(request, matter)


#: How `Tühista` asks for a `Kaasamine` row back in its read state.
#:
#: The same two constants `edit_entry_view` uses, under their own names because
#: the two rows are two swap targets and a shared spelling would suggest one
#: control opens both (`ENTRY_READ_PARAM`).
ENGAGEMENT_READ_PARAM = "vaade"
ENGAGEMENT_READ_VALUE = "lugemine"
ENGAGEMENT_READ_QUERY = f"?{ENGAGEMENT_READ_PARAM}={ENGAGEMENT_READ_VALUE}"


def _engagement_edit_form(engagement: MatterEngagement, data: Any = None) -> EngagementForm:
    """One engagement's correction form, with ids nothing else can share.

    Field *names* stay what the POST handler and its tests read; the ids, and
    the `<label for>` that follows them, are per record — a reader may open two
    chronology rows at once, and two elements sharing an id is enough to make a
    label reach the wrong box (`_entry_edit_form` names the same defect).

    **Filled from the record, every field of it.** An editor that opened empty,
    or with today in the date box, would be asking somebody to retype what is
    already on the screen and inviting them to save a change they did not mean.
    A bound form ignores `initial`, so a refused save still comes back carrying
    what was typed.
    """
    auto_id = f"id_kaasamine_{engagement.pk}_%s"
    # `record=` on both branches, and on the bound one it is load-bearing: it is
    # what tells `EngagementForm.clean` that this row is dated to a period, and
    # therefore that an empty day box means «leave it alone» rather than «clear
    # it». A bound form built without it would quietly destroy the period on
    # every refused-and-resubmitted save (docs/adr/0086 §1).
    if data is not None:
        return EngagementForm(data, auto_id=auto_id, record=engagement)
    return EngagementForm(
        initial={
            "title": engagement.title,
            "url": engagement.url,
            "smaily_url": engagement.smaily_url,
            "alchemer_url": engagement.alchemer_url,
            "note": engagement.note,
            # `Kaasamise kuupäev`, and **only** when it is a day. A record dated
            # to a month, a quarter or a year opens with this box empty and its
            # period stated in words beside it: the stored anchor is a place in
            # a sort and not a day anybody named, so handing back `01.10.2025`
            # would invite somebody to re-save an invented day
            # (docs/adr/0079 §2, docs/adr/0086 §1).
            "occurred_on": (None if engagement.has_approximate_date else engagement.occurred_on),
            # `Vastuseid`, as stored and only as stored. A row counted at zero
            # opens holding `0` and a row nobody counted opens blank, because
            # those are two different facts and a blank box defaulted to zero
            # would invent the second one on somebody's behalf (QA-03).
            "response_count": engagement.response_count,
            "feedback_deadline": engagement.feedback_deadline,
            "feedback_received": engagement.feedback_received,
            "revision": engagement_revision_token(engagement),
        },
        auto_id=auto_id,
        record=engagement,
    )


def _engagement_for_correction(
    request: HttpRequest, matter: Matter, engagement_id: Any
) -> MatterEngagement:
    """The engagement this request may correct, or a 404.

    Scoped through the child's own `visible_to` and not fetched by id off the
    Matter: a `Kaasamine` may carry a stricter visibility override than its
    parent, and reading it any other way would bypass that. A restricted
    consultation inside a Matter somebody may see is therefore indistinguishable
    here from one that does not exist, which is the contract the rest of the
    product keeps (AUTH-003, docs/adr/0038).
    """
    return get_object_or_404(
        MatterEngagement.objects.visible_to(request.user).filter(matter=matter), pk=engagement_id
    )


def _engagement_feedback_form(
    engagement: MatterEngagement, data: Any = None, files: Any = None
) -> EngagementFeedbackForm:
    """One round's `Lõpeta kaasamine` form, with ids nothing else can share.

    Per record, exactly as `_engagement_edit_form` is: a chronology may show
    several waiting rounds at once, and two controls sharing an id is enough to
    make a label reach the wrong textarea.

    Filled from the record, because a round may already carry feedback somebody
    typed when they created it — an empty box would invite them to overwrite
    their own words with nothing. A bound form ignores `initial`, so a refused
    completion comes back carrying what was typed.

    **``files`` is not optional in practice, and the parameter exists because
    leaving it out was silent.** This form declares `attachments`, and a Django
    form bound with `data` alone never sees an upload — `cleaned_data` holds an
    empty list, the service is handed nothing, and a PDF a member sent in is
    discarded with no error and no row anywhere. `Lõpeta kaasamine` was the one
    file-bearing form in the product bound without `request.FILES`. The binding
    is done here rather than at the call site so the answer cannot go missing
    again on a second caller.
    """
    auto_id = f"id_kaasamine_{engagement.pk}_tagasiside_%s"
    form = (
        EngagementFeedbackForm(data, files, auto_id=auto_id)
        if data is not None
        else EngagementFeedbackForm(
            initial={
                "feedback_received": engagement.feedback_received,
                "revision": engagement_revision_token(engagement),
            },
            auto_id=auto_id,
        )
    )
    # The file control's id, per record, **overriding the widget's own**.
    # `workspace_attachments` puts a fixed `id` in the widget's attrs precisely
    # because six of these render on one page, and an explicit attrs id beats
    # `auto_id` in `BoundField.id_for_label` — so a Matter waiting on three
    # rounds would otherwise render three inputs called
    # `id_kaasamine_tagasiside_failid` and the second `<label for>` would open
    # the first round's picker. Mutating `fields` is safe: Django deep-copies
    # `base_fields` per instance (docs/adr/0075 §2).
    form.fields["attachments"].widget.attrs["id"] = f"id_kaasamine_{engagement.pk}_tagasiside"
    return form


def attach_wait_form(
    engagement: MatterEngagement, *, form: EngagementWaitForm | None = None
) -> MatterEngagement:
    """Hang this round's `Ootan tagasisidet` form on the record, or nothing.

    On the record rather than in the context, for the reason
    :func:`attach_feedback_form` gives: the chronology renders many rounds from
    one loop, and a single context variable would give every row the same form
    with the same ids and the same revision token.

    ``None`` for a round that is **already** waiting and for one somebody has
    finished — starting a wait is an act you take once, and offering it on a row
    that has one would be offering to move a deadline somebody else set. That is a
    correction, and it lives on `Muuda` (docs/adr/0091 §2).

    The `row_id` is the record's own primary key, so two waiting-eligible rounds
    on one page do not share `id_ootus_feedback_deadline` — the duplicate-id
    defect docs/adr/0086 §6 names for the completion form.
    """
    offered = engagement.feedback_deadline is None and engagement.feedback_closed_at is None
    engagement.wait_form = (  # type: ignore[attr-defined]
        (
            form
            if form is not None
            else EngagementWaitForm(
                # The version this row was rendered from, so a save whose record
                # has moved on since can be refused rather than silently
                # overwriting it — the token `_engagement_feedback_form` seeds
                # for the same reason.
                initial={"revision": engagement_revision_token(engagement)},
                row_id=f"-{engagement.pk}",
            )
        )
        if offered
        else None
    )
    return engagement


def attach_feedback_form(
    engagement: MatterEngagement,
    *,
    form: EngagementFeedbackForm | None = None,
    open_panel: bool = False,
) -> MatterEngagement:
    """Hang this round's `Lõpeta kaasamine` form on the record, or nothing.

    **On the record rather than in the context, because the chronology renders
    many of them.** A Matter may be running three consultations at once, and
    `matters/partials/engagement_row.html` is included once per row from inside
    a loop — so a single context variable would give every row the same form,
    with the same ids and the same revision token, and pressing `Lõpeta` on the
    second round would post the first one's version (docs/adr/0086 §6).

    One name, set by both callers: the page render walks its timeline items
    through here, and the fragment view does the same for the one row it is
    answering, so a row cannot mean different things on the two paths.

    ``None`` for a round that is not waiting — a wait nobody opened and one
    somebody finished both get no control, and the template asks this rather
    than re-deriving the state.
    """
    engagement.feedback_form = (  # type: ignore[attr-defined]
        form
        if form is not None
        else (_engagement_feedback_form(engagement) if engagement.has_open_feedback_wait else None)
    )
    engagement.feedback_form_open = open_panel  # type: ignore[attr-defined]
    return engagement


def _engagement_row(
    request: HttpRequest,
    matter: Matter,
    engagement: MatterEngagement,
    *,
    form: EngagementForm | None = None,
    feedback_form: EngagementFeedbackForm | None = None,
    feedback_open: bool = False,
    wait_form: EngagementWaitForm | None = None,
    error: str = "",
    conflict: MatterEngagement | None = None,
    status: int = 200,
) -> HttpResponse:
    """The corrected `Kaasamine` back in place, or the form that could not save.

    One renderer for both, because they swap the same element: `Muuda` replaces
    the milestone's text region with the form, and every answer replaces it
    again — with the corrected record, or with the form still open and what the
    person typed still in it. The `<article>` around it, its 12 px dot, its
    spine and its attached files are never in the response, so a correction
    cannot move the row or turn into a second line in the chronology.

    The milestone is rebuilt through `engagement_milestone`, the same function
    the chronology itself renders from, so a corrected row cannot come back
    worded differently from the way it will read on the next page load.
    """
    return render(
        request,
        "matters/partials/engagement_row.html",
        {
            "matter": matter,
            # `Lõpeta kaasamine` rides on the record, exactly as it does on the
            # page render. A refused completion comes back bound and asks for
            # its own panel to reopen (`attach_feedback_form`).
            "engagement": attach_wait_form(
                attach_feedback_form(engagement, form=feedback_form, open_panel=feedback_open),
                form=wait_form,
            ),
            "milestone": engagement_milestone(engagement),
            "engagement_edit_form": form,
            "engagement_edit_error": error,
            "engagement_conflict": conflict,
            "engagement_conflict_milestone": (
                engagement_milestone(conflict) if conflict is not None else None
            ),
            "engagement_read_query": ENGAGEMENT_READ_QUERY,
            # `Ootan tagasisidet`'s quick spans, resolved to real days here so
            # each chip can print the one it lands on. Also on the workspace
            # dict, because the row renders on both paths and a span that
            # existed on only one of them would be a control that appears and
            # disappears as the page is swapped (docs/adr/0086 §2).
            "feedback_deadline_choices": feedback_deadline_choices(timezone.localdate()),
        },
        status=status,
    )


@login_required
@business_write_required
@require_http_methods(["GET", "POST"])
def update_engagement_view(request: HttpRequest, pk: Any, engagement_id: Any) -> HttpResponse:
    """`Muuda` on a `Kaasamine` — a wrong value on a round that really happened.

    A round that never happened at all is `Kustuta`, which is a different act
    with its own route and its own audit event (`remove_record_view`).

    GET opens the form in the chronology row; POST saves it. One route, because
    they are one interaction and the second is only reachable from the first —
    the shape `edit_entry_view` already uses for a Sissekanne.

    **Refused on a closed Matter, deliberately, and unlike an entry
    correction.** `edit_entry` is available on a closed file because rewriting
    the wording of a narrative touches no canonical fact. A `Kaasamine`
    correction moves the dates, the channel, the audience and the links of a
    structured record that the chronology, the register's activity date and the
    search projection all read, so it is normal interactive business work and
    a finished file refuses it. Reopening is the way out, and it leaves
    somebody's name on both decisions (docs/adr/0075 §12, docs/adr/0076 §2).
    The rule is enforced under the Matter's row lock inside
    `correct_engagement`, never by whether this page rendered a button: the
    browser that posts may be holding a page from before the closure.

    Behind `business_write_required` and nothing narrower, like every other
    correction: a wrong date on a colleague's consultation is the department's
    problem and not that colleague's alone (docs/adr/0042). A reader gets the
    decorator's 404 — the same answer the route gives for a Matter that does
    not exist, so a refusal describes no surface.
    """
    matter = get_visible_matter(request, pk)
    engagement = _engagement_for_correction(request, matter, engagement_id)

    if request.method == "GET":
        # `Tühista`. Leaving edit mode is a re-read rather than a client-side
        # hide: the boxes may be holding values that were never saved, and the
        # only honest way out of them is to fetch what the record actually says.
        # Nothing is written on this path — it is a GET, and it takes no lock.
        if request.GET.get(ENGAGEMENT_READ_PARAM) == ENGAGEMENT_READ_VALUE:
            return _engagement_row(request, matter, engagement)
        return _engagement_row(request, matter, engagement, form=_engagement_edit_form(engagement))

    form = _engagement_edit_form(engagement, request.POST)
    if not form.is_valid():
        # Bound, so what the person typed is still in the boxes — and the
        # engagement is untouched, so nothing was lost either way.
        return _engagement_row(request, matter, engagement, form=form, status=400)

    try:
        corrected = correct_engagement(
            engagement=engagement,
            # No `kind`. The editor stopped offering it, so `_UNSET` leaves
            # whatever is stored exactly as it is — a historical `Kaasamiskutse
            # veebis` keeps saying so (docs/adr/0086 §1).
            title=form.cleaned_data["title"],
            url=form.cleaned_data.get("url") or "",
            smaily_url=form.cleaned_data.get("smaily_url") or "",
            alchemer_url=form.cleaned_data.get("alchemer_url") or "",
            note=form.cleaned_data.get("note") or "",
            # Both dates, named explicitly on every save, so an emptied box
            # clears the column. `update_engagement`'s `_UNSET` sentinel is what
            # protects a field a caller does *not* name — the importer and the
            # register refresh rely on it — and naming a field is how this form
            # says «I am the editor of this value» (docs/adr/0078 §3).
            #
            # `occurred_on` is the date the save *results in*, which for a
            # record dated to a period and left alone is the period it already
            # had — the form resolves that, because only it knows what the
            # empty day box was showing (`EngagementForm.clean`).
            occurred_on=form.cleaned_data.get("occurred_on_value"),
            occurred_on_precision=form.cleaned_data["occurred_on_precision"],
            feedback_deadline=form.cleaned_data.get("feedback_deadline"),
            feedback_received=form.cleaned_data.get("feedback_received") or "",
            # `Vastuseid`, named on every save for the same reason both dates
            # are: naming a field is how this form says «I am the editor of this
            # value», and it is the only way an emptied box can clear a count
            # somebody no longer stands behind. `None` here is «keegi ei
            # lugenud» and `0` is «keegi ei vastanud» — the service keeps them
            # apart, and `_UNSET` still protects every caller that names neither
            # (`update_engagement`, QA-03).
            response_count=form.cleaned_data.get("response_count"),
            actor=request.user,
            expected_revision=form.cleaned_data.get("revision") or "",
        )
    except EngagementEditConflict as conflict:
        # 409, and nothing was written. The form stays open holding this
        # person's values, and the version that beat them arrives beside it to
        # read — neither is chosen for them. The hidden token is **not**
        # advanced: adopting the newer one here would be this view deciding that
        # the next submit may overwrite what the other writer saved, which is
        # the defect with one more step in it (`edit_entry_view`, QA-09).
        return _engagement_row(
            request,
            matter,
            engagement,
            form=form,
            error=str(conflict),
            conflict=conflict.current,
            status=409,
        )
    except DomainError as error:
        # A closed Matter lands here, and so does any refusal the service
        # makes. The sentence goes into the form that is still open rather
        # than into a panel this row does not have.
        return _engagement_row(request, matter, engagement, form=form, error=str(error), status=400)

    return _engagement_row(request, matter, corrected)


@login_required
@business_write_required
@require_http_methods(["POST"])
def open_engagement_wait_view(request: HttpRequest, pk: Any, engagement_id: Any) -> HttpResponse:
    """`Ootan tagasisidet` — start this round waiting, as its own act.

    The other half of the change that took the reply-by date off `+ Kaasamine`.
    Filing a consultation and deciding the file is waiting on an answer are two
    acts, and only the second one puts a row on somebody's desk — so only the
    second one asks a question (lawyer feedback 11, docs/adr/0091 §2).

    Deliberately the same shape as `complete_engagement_feedback_view` beside it:
    one row is the swap target, the refusal comes back into that row with what was
    typed still in it, and the closed-Matter rule, the state refusals and the
    revision check are all the service's, taken under the Matter's row lock.
    """
    matter = get_visible_matter(request, pk)
    engagement = _engagement_for_correction(request, matter, engagement_id)
    form = EngagementWaitForm(request.POST, row_id=f"-{engagement.pk}")
    if not form.is_valid():
        return _engagement_row(request, matter, engagement, wait_form=form, status=400)
    try:
        open_engagement_feedback_wait(
            engagement=engagement,
            deadline=form.cleaned_data["feedback_deadline"],
            actor=request.user,
            expected_revision=form.cleaned_data.get("revision") or "",
        )
    except EngagementEditConflict as conflict:
        return _engagement_row(
            request,
            matter,
            engagement,
            wait_form=form,
            error=str(conflict),
            conflict=conflict.current,
            status=409,
        )
    except DomainError as error:
        return _engagement_row(
            request, matter, engagement, wait_form=form, error=str(error), status=400
        )
    engagement.refresh_from_db()
    return _engagement_row(request, matter, engagement)


@login_required
@business_write_required
@require_http_methods(["POST"])
def complete_engagement_feedback_view(
    request: HttpRequest, pk: Any, engagement_id: Any
) -> HttpResponse:
    """`Lõpeta kaasamine` — the round stopped waiting, and here is what came back.

    POST only. The form it posts is rendered inside the chronology row by
    `_engagement_row`, which is also every answer's swap target — saved, refused
    or conflicted, the reader is looking at one element and the answer lands in
    it (docs/adr/0086 §6).

    **Every rule is the service's.** A closed Matter, a round nobody is waiting
    on, a wait somebody already finished and a stale revision are all refused
    under the Matter's row lock inside `complete_engagement_feedback`, never by
    whether this page drew a button: the browser that posts may be holding a
    page from before the closure, or from before a colleague finished the same
    round.

    Behind `business_write_required` and nothing narrower, like every other
    correction on this record: an unanswered consultation is the department's
    problem and not one lawyer's (docs/adr/0042). A reader gets the decorator's
    404.

    The files ride with the decision, through the ordinary evidence path, so an
    answer that arrived as a PDF is attached to the round it answers rather than
    to the Matter in general — and a refused upload unwinds the completion with
    it, because `add_engagement_feedback` writes both in one transaction.
    """
    matter = get_visible_matter(request, pk)
    engagement = _engagement_for_correction(request, matter, engagement_id)
    form = _engagement_feedback_form(engagement, request.POST, request.FILES)
    if not form.is_valid():
        return _engagement_row(
            request, matter, engagement, feedback_form=form, feedback_open=True, status=400
        )

    try:
        completed = workspace.add_engagement_feedback(
            engagement=engagement,
            author=request.user,
            feedback_received=form.cleaned_data.get("feedback_received") or "",
            uploads=form.cleaned_data["attachments"],
            expected_revision=form.cleaned_data.get("revision") or "",
        )
    except EngagementEditConflict as conflict:
        # 409, and nothing was written — not the words, not the timestamp. The
        # form stays open holding this person's text, the version that beat them
        # arrives beside it to read, and the hidden token is **not** advanced:
        # adopting it here would be this view deciding that the next submit may
        # overwrite what the other writer saved (`update_engagement_view`).
        return _engagement_row(
            request,
            matter,
            engagement,
            feedback_form=form,
            feedback_open=True,
            error=str(conflict),
            conflict=conflict.current,
            status=409,
        )
    except (DomainError, UploadRejected) as error:
        # A closed Matter lands here, and so does a wait that is already
        # finished or was never opened. The sentence goes into the panel that is
        # still open rather than into a page-level banner this row does not have.
        return _engagement_row(
            request,
            matter,
            engagement,
            feedback_form=form,
            feedback_open=True,
            error=str(error),
            status=400,
        )

    return _engagement_row(request, matter, completed)


@login_required
@business_write_required
@require_http_methods(["POST"])
def set_action(request: HttpRequest, pk: Any) -> HttpResponse:
    matter = get_visible_matter(request, pk)
    form = NextActionForm(
        request.POST,
        periods=True,
        current=selectors.current_action_of(matter, request.user),
    )

    # Both refusals through `_workspace_refusal`, which is where every other
    # workspace save's already went. This view hand-rolled its own render, and
    # the difference stopped being cosmetic when `+ Järgmine tegevus` left the
    # launcher: `#lisa-jargmine` is now drawn **beside an open task and nowhere
    # else**, so a refusal on a Matter with no open step named a panel the page
    # does not render and the sentence — or the field error — was printed
    # inside nothing. The shared helper falls back to the workspace-level slot
    # for exactly that case, and brings a closed Matter's header with it
    # (docs/adr/0097 §8.2).
    if not form.is_valid():
        return _workspace_refusal(request, matter, key="action_form", form=form)

    try:
        set_next_action_for_new_work(matter=matter, actor=request.user, **form.as_service_kwargs())
    except DomainError as error:
        return _workspace_refusal(request, matter, key="action_form", form=form, error=str(error))

    return _render_overview(request, matter)


def _next_action_row_context(request: HttpRequest, matter: Matter) -> dict[str, Any]:
    """Everything `next_action_row.html` reads, and nothing else.

    A deliberately small slice of `_overview_context`. The Järgmiseks row is the
    one surface that re-renders on its own, and building the whole overview to
    answer it would run the timeline, the engagement list and the intelligence
    selectors for a fragment that shows none of them.

    Scoped like `_overview_context`, and for the same reason: this fragment
    renders the same row, so a second reader of the same fact must not answer a
    different question about it.
    """
    current_action = selectors.current_action_of(matter, request.user)
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
    #
    # Empty means "no next review date" and is an ordinary answer. A value that
    # is not a day is refused with nothing written: read as empty, `31.02.2026`
    # used to *clear* the date somebody was trying to set (ENG-046).
    reading = read_flexible_date(request.POST.get("next_review_date"))

    try:
        if reading.invalid:
            raise DomainError("Kirjuta kuupäev kujul 7.9.2026.")
        acknowledge_review(action=action, actor=request.user, next_review_date=reading.value)
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


#: What `Tagasisidet ootame kuni` offers beside its box.
#:
#: Three spans and a calendar, which is what a consultation round actually asks
#: for. They are spelled as the periods themselves — `1 nädal`, not `+1 nädal` —
#: because the question above them is «until when», not «how much later»
#: (docs/adr/0086 §2).
#:
#: `1 kuu` is a **calendar** month and is therefore not in this tuple: a span in
#: days cannot say «the 31st of January plus one month», and the whole point of
#: offering a month is that somebody who picks it means the same day next month
#: (`app.core.dates.add_months`).
FEEDBACK_DEADLINE_SPANS: tuple[tuple[int, str], ...] = (
    (7, "1 nädal"),
    (14, "2 nädalat"),
)


def feedback_deadline_choices(today: date) -> list[dict[str, Any]]:
    """The reply-by spans, each carrying the day it resolves to.

    Resolved on the server, in Europe/Tallinn, and delivered on the control —
    the contract :func:`quick_date_choices` keeps and for the same reason:
    working it out in the browser would answer in the reader's own timezone,
    which is the class of defect `app/core/dates.py` exists to prevent.

    They write into the date box beside them and store nothing of their own, so
    the server sees one value however it was chosen and the panel works with the
    chips ignored entirely — including with scripting off, where the box is
    pre-filled and typing over it is the whole interaction
    (`static/js/ux.js`, `bindQuickDates`).
    """
    spans = [(today + timedelta(days=days), label) for days, label in FEEDBACK_DEADLINE_SPANS]
    spans.append((add_months(today, 1), "1 kuu"))
    return [
        {
            "value": format_estonian_date(when),
            "label": label,
            "when": f"{weekday_letter(when)} {short_day_month(when)}",
        }
        for when, label in spans
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
    "source_organisations",
    "received_date",
    "response_deadline",
    # `visibility` is deliberately absent, so this endpoint answers 404 for it.
    # The ordinary Teema UI does not ask who may see a Matter, and a field left
    # in this set would be an accepted POST parameter behind a control nobody
    # draws — which is exactly the shape the removal exists to close
    # (docs/adr/0096 §3).
    #
    # `track` and `addressee_organisation` joined it on 2026-09-20, with the
    # rail rows that posted to them. Removed from the *set*, not merely from
    # the templates: a branch left standing behind a control nobody is offered
    # is a working write path reachable by a crafted POST, and «the button is
    # gone» is not an answer to that. `update_field` 404s on a field it does
    # not name, so the two addresses are now simply not routes (docs/adr/0097
    # §3, §4).
    #
    # Nothing about the stored columns changed. The importers, the register
    # refresh and the cutover all still write both.
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
    # `Menetluse link` is a Matter fact and is answered on this page, so it
    # rides in the same `<form>` element under its own prefix — exactly the
    # arrangement `Uus teema` uses, for the same reason: two form classes,
    # namespaced POST keys, and neither able to collide with a field name in the
    # other (docs/adr/0089 §13, docs/adr/0097 §5).
    link = _primary_procedural_link(matter)
    if request.method == "GET":
        form = MatterEditForm(initial=edit_initial(matter), matter=matter, viewer=request.user)
        return render(
            request,
            "matters/matter_edit.html",
            _edit_context(request, matter, form, MatterLinkForm(link=link, prefix="menetlus")),
        )

    form = MatterEditForm(request.POST, matter=matter, viewer=request.user)
    link_form = MatterLinkForm(request.POST, link=link, prefix="menetlus")
    # Both, and `and` after both have run rather than short-circuiting: a page
    # refused for a wrong title must still come back with the address refusal
    # under the address box, and `is_valid()` is what fills `errors` at all.
    forms_valid = form.is_valid()
    forms_valid = link_form.is_valid() and forms_valid
    if not forms_valid:
        # The bound forms are re-rendered, so everything typed is still there.
        return render(
            request,
            "matters/matter_edit.html",
            _edit_context(request, matter, form, link_form),
            status=400,
        )

    data = form.cleaned_data
    try:
        with transaction.atomic():
            # First, and inside the transaction: this page posts every field it
            # holds, so a copy filled before somebody else's save would
            # otherwise write thirteen stale values over thirteen fresh ones
            # and call it an edit. The guard takes the Matter row and compares
            # the committed version, never the instance this request arrived
            # with (QA-002, `guard_matter_revision`).
            guard_matter_revision(matter=matter, expected_revision=data.get("revision") or None)
            set_matter_title(matter=matter, value=data["title"], actor=request.user)
            set_brief_summary(
                matter=matter, value=data.get("brief_summary") or "", actor=request.user
            )
            assign_matter(matter=matter, owner=data.get("owner"), actor=request.user)
            change_stage(matter=matter, stage=data.get("stage"), actor=request.user)
            # No `change_track`. `Menetlusliik` is off this page, so there is no
            # cleaned value to pass and the service is not called with a default
            # either — a Matter's track survives every save of this form
            # untouched (docs/adr/0097 §3).
            # `list(...)` rather than the queryset on both set-valued fields,
            # so an empty POST arrives as "none of them" — a decision somebody
            # made — and never as the sentinel that means "leave them alone"
            # (app/matters/services.py, `_UNSET`).
            # Resolved inside this transaction, and before the Matter is
            # touched. A typed name that names a body nobody has filed against
            # yet becomes an Organisation here; if any service below refuses,
            # the rollback takes that Organisation with it.
            #
            # **`addressee_organisation` is not passed, and that is the whole of
            # what its removal means here.** The parameter defaults to `_UNSET`,
            # which is the sentinel for «leave this alone» — so a Matter that
            # carries an addressee from the register keeps it through every save
            # of this form. Passing `None` would have been the defect: it is a
            # decision, and it would clear a fact nobody was offered the chance
            # to state (docs/adr/0097 §4).
            set_organisations(
                matter=matter,
                source_organisations=resolve_source_organisations(
                    chosen=data.get("source_organisations"),
                    typed_name=data.get("sender_name") or "",
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
            # Through the service, never `matter.legal_instruments.set(...)`
            # here: a canonical classification that moved without a change
            # event is a correction the audit trail cannot answer for, and the
            # service is what writes one (app/matters/services.py, task §19).
            set_legal_instruments(
                matter=matter,
                legal_instruments=list(data.get("legal_instruments") or []),
                actor=request.user,
            )
            set_legal_instrument_other(
                matter=matter, value=data.get("legal_instrument_other") or "", actor=request.user
            )
            # No `set_tags` call and no `set_matter_visibility` call.
            # `Sildid` and `Nähtavus` are gone from this page and from
            # `MatterEditForm`, so there is no cleaned value to pass on and
            # neither service is called with a default either: a crafted
            # `tags=` or `visibility=RESTRICTED` in this POST changes nothing,
            # and a Matter's tags and its visibility survive every save of this
            # form untouched (docs/adr/0096 §3, docs/adr/0097 §2).
            _save_procedural_link(matter=matter, form=link_form, actor=request.user)
    except MatterEditConflict as conflict:
        # Somebody else changed this Matter between the page opening and this
        # save. Refused rather than applied, and the page comes back with every
        # value this person typed still in it, so the two versions are
        # reconciled by a person rather than by whoever pressed `Salvesta` last.
        #
        # The token is refreshed to the version the refusal was measured
        # against, so a second press — by somebody who has now read the other
        # side — saves against what is stored rather than bouncing forever.
        #
        # Caught before `DomainError`, which it subclasses: the generic handler
        # would answer 400 and lose the status this deserves.
        retry = request.POST.copy()
        retry["revision"] = matter_revision_token(conflict.current)
        form = MatterEditForm(retry, matter=matter, viewer=request.user)
        form.is_valid()
        form.add_error(None, str(conflict))
        # What actually moved, stated beside the refusal. Without it the page
        # comes back showing this person's own stale values, and the only
        # obvious next action is to press `Salvesta` again — which is the
        # silent revert the guard exists to prevent, performed by hand.
        changes = matter_edit_conflict_changes(current=conflict.current, submitted=data)
        matter.refresh_from_db()
        context = _edit_context(request, matter, form, link_form)
        context["conflict_changes"] = changes
        return render(request, "matters/matter_edit.html", context, status=409)
    except ProceduralLinkConflict as conflict:
        # Somebody else corrected the same address between this page being
        # opened and being saved. Refused rather than overwritten, and the
        # refusal lands under the address box rather than at the top of a page
        # whose other twelve fields were fine — the rule `correct_procedural_link`
        # enforces and `procedural_links.html` reports on its own card.
        #
        # Caught *before* `DomainError`, which it subclasses: the generic
        # handler below would otherwise answer a stale-copy conflict with 400
        # and a non-field error, losing both the status and the place.
        link_form.add_error(None, str(conflict))
        matter.refresh_from_db()
        return render(
            request,
            "matters/matter_edit.html",
            _edit_context(request, matter, form, link_form),
            status=409,
        )
    except DomainError as error:
        form.add_error(None, str(error))
        matter.refresh_from_db()
        return render(
            request,
            "matters/matter_edit.html",
            _edit_context(request, matter, form, link_form),
            status=400,
        )

    messages.success(request, "Teema andmed on salvestatud.")
    return redirect("matters:matter_detail", pk=matter.pk)


@login_required
@business_write_required
@require_http_methods(["GET"])
def matter_edit_assisted(request: HttpRequest, pk: Any) -> HttpResponse:
    """`Kontrolli dokumendist leitud andmeid` — the edit page, with suggestions.

    The same form as `matter_edit`, rendered once more with what the
    extraction system already read off this Matter's documents beside it: a
    proposed title, sender, deadline, Menetlusliik and Valdkonnad, each with
    the excerpt it came from, and the facts that have no field of their own —
    who wrote, from which address, under which reference.

    **A read.** This view accepts only GET and writes nothing: no Matter
    field, no Organisation, no taxonomy row, no audit event. A high-confidence
    suggestion may appear already filled into an *empty* control; it stays
    unsaved. The form posts to `matter_edit` exactly as it does from the plain
    edit page, so saving is the existing write path with the existing audit
    trail, and a refused save re-renders what the person typed without
    re-running the analysis over it (docs/adr/0060).

    **The same authorization, twice.** The Matter through `get_visible_matter`,
    and the documents — inside the analyser — through
    ``Document.objects.visible_to``, so a restricted annex on a normal Matter
    contributes no evidence to a reader who may not open it.

    That second gate changes nothing on *this* surface today, and the honest
    reading of it is worth writing down: the page is behind
    `business_write_required`, and the two roles that hold business write —
    SPECIALIST and DEPARTMENT_HEAD — are exactly the two in
    `ROLES_WITH_RESTRICTED_ACCESS`, so everybody who can open this page already
    reads every restricted document on the Matter. The gate is what keeps the
    surface correct the day a role may edit without reading restricted
    material, and `build_analysis_input` is tested through a READER, who is the
    only viewer the visible set actually differs for.
    """
    matter = get_visible_matter(request, pk)
    analysis = analyse_matter(matter, request.user)
    current = CurrentValues.of(matter)
    initial, analysis = prefill_initial(analysis, base=edit_initial(matter), current=current)
    suggested = analysis.fields.get(SuggestedField.SOURCE_ORGANISATIONS)
    suggested_senders = (
        list(
            Organisation.objects.filter(
                pk__in=[candidate.value for candidate in suggested.offered]
            ).order_by("name")
        )
        if suggested is not None and suggested.offered
        else []
    )
    form = MatterEditForm(
        initial=initial, matter=matter, viewer=request.user, suggested_senders=suggested_senders
    )
    context = _edit_context(
        request,
        matter,
        form,
        # Unbound, and the assisted review proposes nothing about it: the
        # extraction reads facts off a document, and where the proceeding lives
        # is not one of them. The block renders holding whatever the Matter
        # already has (app/matters/intake_suggestions).
        MatterLinkForm(link=_primary_procedural_link(matter), prefix="menetlus"),
    )
    context["assisted"] = analysis
    return render(request, "matters/matter_edit.html", context)


@login_required
@business_write_required
@require_http_methods(["GET", "POST"])
def matter_delete(request: HttpRequest, pk: Any) -> HttpResponse:
    """`Kustuta teema` — a page that asks, and a POST that does it.

    **A route of its own, not a second button on the edit form.** The question
    "should this record exist" is a different question from "are these facts
    right", and a destructive control inside the form that saves is one stray
    Enter away from the wrong submit. The GET renders what would be removed;
    the POST removes it. There is no third way in — `matters:matter_edit`
    cannot delete, whatever it is sent (docs/adr/0096 §4.3).

    **GET writes nothing.** `plan_matter_deletion` is a read: it walks the
    ownership graph, counts the rows and evidence objects, and collects every
    reason the deletion would refuse. A blocked Matter shows the reasons and no
    final button, and the POST refuses independently — the page is presentation
    and `delete_matter` is the boundary.

    **The same authorization as editing, and no more.** This is what the owner
    asked for: deleting a Teema is a thing the application's users do, not an
    administrator's privilege. `business_write_required` is the cohort —
    SPECIALIST and DEPARTMENT_HEAD, the same two `Muuda teemat` requires — and
    `get_visible_matter` is the record-level gate, so a restricted Matter is a
    404 to somebody who may not see it rather than a refusal that confirms it
    exists. A crafted POST from a READER or an ADMINISTRATOR is refused by the
    decorator before this body runs (app/core/decorators.py, docs/adr/0096
    §4.2).

    **Nothing stronger was invented.** There is no existing canonical
    permission over destructive Matter operations to reuse: closing a Matter,
    which is the nearest thing the product had, is the same business-write
    cohort. `ROLES_WITH_WORK_VICTORY_REVIEW` is the only narrower set in the
    codebase and it is about claiming influence, not about data
    (app/core/authorization.py).
    """
    matter = get_visible_matter(request, pk)

    if request.method == "POST":
        # **No plan is built before the POST.** `delete_matter` builds its own
        # under the row lock, which is the only one that decides anything — one
        # taken out here would be a second walk of the same graph whose answer
        # nothing may act on.
        try:
            delete_matter(matter=matter, actor=request.user)
        except DomainError as error:
            # The refusal, on the page that offered the button, with a fresh
            # plan behind it — a blocker that appeared while somebody read the
            # confirmation is exactly the case this branch exists for.
            return render(
                request,
                "matters/matter_delete.html",
                _delete_context(matter, plan_matter_deletion(matter), error=str(error)),
                status=400,
            )
        messages.success(request, "Teema kustutati.")
        # The register, never the Matter's own address: that URL answers 404
        # now, and redirecting a successful deletion to a 404 would read as a
        # failure (docs/adr/0096 §4.5).
        return redirect("matters:matter_list")

    return render(
        request, "matters/matter_delete.html", _delete_context(matter, plan_matter_deletion(matter))
    )


def _delete_context(matter: Matter, plan: Any, error: str = "") -> dict[str, Any]:
    """What the confirmation page reads.

    The counts are named per business record rather than per table. A person
    deciding whether to destroy a file needs to know it holds four entries and
    two opinions; `matters.MatterSourceOrganisation` is a join row and telling
    them about it would bury the sentence that matters.
    """
    labelled = [
        (label, plan.count_of(model))
        for label, model in DELETION_SUMMARY_ROWS
        if plan.count_of(model)
    ]
    return {
        "matter": matter,
        "plan": plan,
        "summary": labelled,
        "evidence_objects": len(plan.evidence_keys),
        "delete_error": error,
    }


#: What the confirmation page names, in the order it names them. A shortlist of
#: the records a lawyer would recognise, deliberately not the whole inventory:
#: the page's job is to make the size of the act legible, not to print a schema.
DELETION_SUMMARY_ROWS: tuple[tuple[str, str], ...] = (
    ("sissekannet", "matters.Entry"),
    ("järgmist tegevust", "workflow.NextAction"),
    ("kaasamist", "matters.MatterEngagement"),
    ("välist seisukohta", "matters.MatterExternalPosition"),
    # `märget`, not «menetluse arengut». The launcher calls this record a
    # `Märge` and has never called it anything else to a lawyer; the
    # confirmation screen for a destructive act is the last place to introduce
    # a second name for the thing being destroyed (QA-012).
    ("märget", "matters.MatterProceduralDevelopment"),
    ("ülevaadet või uudist", "matters.MatterWebsiteOverview"),
    ("arvamust", "submissions.Submission"),
    ("dokumenti", "documents.Document"),
)


def _primary_procedural_link(matter: Matter) -> Any:
    """The one `Menetluse link` `Muuda teemat` puts in a box, or `None`.

    Oldest first, so the address a Matter was filed with is the one the page
    offers to correct — and so the choice is stable across renders rather than
    depending on the order a query happened to come back in.

    A Matter carrying several keeps every one of them; the rest are read and
    corrected on the Teema page's `Menetluse lingid` card, which has held a
    `Paranda` per row since docs/adr/0089 §6.
    """
    return matter.procedural_links.order_by("created_at", "pk").first()


def _save_procedural_link(*, matter: Matter, form: Any, actor: Any) -> None:
    """Record or correct this Matter's `Menetluse link`, or do nothing.

    Three cases, and the third is the ordinary one:

    * the Matter has a link and the form holds an address — a correction,
      through `correct_procedural_link` under the revision the page was drawn
      from, so a stale copy is refused rather than allowed to overwrite a newer
      one;
    * the Matter has none and somebody typed an address — a new row under
      `STORED_KIND`, exactly as `Uus teema` files one;
    * nobody answered the block — nothing at all. No row, no event, no empty
      record (docs/adr/0089 §7).

    Called inside `matter_edit`'s transaction, so an address the service refuses
    takes the whole correction back with it. A page that saved twelve fields and
    then reported that the thirteenth was wrong would have left the record in a
    state nobody chose.
    """
    data = getattr(form, "cleaned_data", None) or {}
    url = (data.get("url") or "").strip()
    if form.link is not None:
        if not url:
            # Refused in `MatterLinkForm.clean`, so this is unreachable through
            # the page. Belt and braces for a caller constructing the form by
            # hand: there is no deletion of a procedural link, here or anywhere
            # (docs/adr/0084 §8).
            return
        correct_procedural_link(
            link=form.link,
            # The kind this row already carries, not `STORED_KIND`. A correction
            # of the address must not silently reclassify a link somebody
            # deliberately filed as `EIS` — the kind is stated on the Teema
            # page's own `Paranda`, which is the only control that offers the
            # vocabulary (docs/adr/0094 §3, docs/adr/0097 §5).
            kind=form.link.kind,
            url=url,
            label=data.get("label") or "",
            actor=actor,
            expected_revision=data.get("revision") or "",
        )
        return
    if form.wants_link:
        # The **workspace** wrapper, not `record_procedural_link` directly.
        #
        # Recording an address is new business content, and new business
        # content is refused on a closed Teema — a rule that lives in
        # `lock_open_matter_for_business_write` rather than in whether a page
        # drew a control, so that it holds against a POST from a tab that was
        # open before somebody else shut the file (R2-02).
        #
        # `matter_create` calls the service directly and is right to: the
        # Matter it has just filed is open by construction. This page is the
        # one that can be looking at a finished file. Correcting an existing
        # address stays direct for the opposite reason — closure has never
        # meant that an address recorded wrongly must stay wrong.
        workspace.add_matter_procedural_link(
            matter=matter,
            author=actor,
            kind=MatterLinkForm.STORED_KIND,
            url=url,
            label=data.get("label") or "",
        )


def _edit_context(
    request: HttpRequest, matter: Matter, form: Any, link_form: Any
) -> dict[str, Any]:
    return {
        "matter": matter,
        "form": form,
        # Under the name `procedural_link_create.html` reads, because it is the
        # same block: `Uus teema` and `Muuda teemat` draw one control from one
        # partial, which is what stops `Link` and `Nimetus` drifting apart
        # between the page somebody files from and the page they correct from
        # (docs/adr/0097 §1, §5).
        "procedural_link_form": link_form,
        # Named here rather than derived in the template: the page states, in
        # words, which facts about this Matter it will not let anybody change,
        # so their absence reads as a decision rather than as an omission
        # (Teema QA §2.2).
        "immutable_facts": _immutable_facts(matter),
        # Whether the page may offer the assisted review at all: only a Matter
        # with material to read has anything to be read. The count is the
        # same scoped read the header makes for the Dokumendid tab.
        "has_documents": Document.objects.filter(matter=matter).visible_to(request.user).exists(),
        # **No `can_delete` here, deliberately.** Reaching this page *is* the
        # permission: `business_write_required` plus `get_visible_matter` is the
        # cohort that may delete, and it is the same cohort that may edit — which
        # is the answer the product asked for, because deletion is not an
        # administrator's privilege but what somebody does about a Teema that
        # should not exist (docs/adr/0096 §4.2).
        #
        # A flag that is always true is a decision point that is not one, and
        # somebody would eventually read it as the gate. The gate is the delete
        # route, which re-authorises and re-checks every blocker.
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
        with transaction.atomic():
            if field in GUARDED_MATTER_FIELDS:
                # The whole-value set editors check what they showed against
                # the locked row, and hold that lock through the write and its
                # search refresh (ENG-028, ENG-029). The token is the field's own
                # revision, rendered into the form beside the checkboxes.
                matter = guard_matter_field_revision(
                    matter=matter, field=field, expected=request.POST.get("revision")
                )
            _apply_inline_field(request, matter, field, value, form)
    except MatterFieldConflict as conflict:
        # Refused, with the surface re-rendered from what is stored now, so the
        # person sees the colleague's value where theirs would have gone and
        # decides again — rather than having silently reverted it. 409, which
        # the page swaps in like a validation answer (static/js/app.js).
        matter = conflict.current
        context = _header_context(request, matter)
        context["field_error"] = str(conflict)
        return render(request, surface, context, status=409)
    except DomainError as error:
        context = _header_context(request, matter)
        context["field_error"] = str(error)
        return render(request, surface, context, status=400)

    matter.refresh_from_db()
    # Each field re-renders the surface it lives on. `Muu valdkond` sits in the
    # facts rail rather than the header strip, and swapping the header for it
    # would leave the value on screen unchanged while claiming it had saved.
    return render(request, surface, _header_context(request, matter))


def _apply_inline_field(
    request: HttpRequest, matter: Matter, field: str, value: Any, form: Any
) -> None:
    """The one service call an inline header or rail field stands for."""
    if field == "owner":
        assign_matter(matter=matter, owner=value, actor=request.user)
    elif field == "stage":
        change_stage(matter=matter, stage=value, actor=request.user)
    elif field == "source_organisations":
        # `list(...)` rather than the queryset, so an empty POST arrives as
        # `[]` — "clear every sender" — and never as the `_UNSET` that means
        # "leave them alone" (Agent-E brief 20, 34).
        #
        # Wrapped, because two writes have to survive or fail together: a
        # typed name may create an institution, and `set_organisations`
        # refusing afterwards must not leave it in the catalogue. Every
        # other branch here is one service call and already atomic in
        # itself (docs/adr/0063).
        with transaction.atomic():
            set_organisations(
                matter=matter,
                source_organisations=resolve_source_organisations(
                    chosen=list(value or []),
                    typed_name=form.cleaned_data.get("sender_name") or "",
                ),
                actor=request.user,
            )
    elif field == "received_date":
        set_matter_dates(matter=matter, received_date=value, actor=request.user)
    elif field == "response_deadline":
        set_matter_dates(matter=matter, response_deadline=value, actor=request.user)
    elif field == "policy_area_other":
        set_policy_area_other(matter=matter, value=value or "", actor=request.user)
    elif field == "policy_areas":
        # `list(...)` rather than the queryset, for the same reason the
        # sender set uses one: an empty POST means "none of them", which is
        # a decision somebody made, not a field they left alone.
        set_policy_areas(matter=matter, policy_areas=list(value or []), actor=request.user)


#: Fields whose control is not in the header band. `_header_context` already
#: carries everything the rail reads, so one context serves both.
#:
#: The redesign moved four of them: Menetlusliik, Saatja, Kellele and Saabus
#: are looked-up facts rather than glance facts, so they are edited in the rail
#: where they are now shown. Swapping the header for one of them would leave the
#: value on screen unchanged while claiming it had saved (Teema redesign §22.1).
_FIELD_SURFACES = {
    # **`policy_area_other` renders the header now.** It is read in the
    # `Valdkond` slot beside the canonical areas — the filing decision and the
    # words qualifying it are one answer and belong in one place — so a save
    # that swapped the rail would leave the value on screen unchanged while
    # claiming it had saved, which is the exact failure this mapping exists to
    # prevent (post-QA R2-07, `templates/matters/partials/header.html`).
    "policy_area_other": "matters/partials/header.html",
    "track": "matters/partials/rail.html",
    "source_organisations": "matters/partials/rail.html",
    "addressee_organisation": "matters/partials/rail.html",
    # **`received_date` renders the header now.** It moved into the metaline
    # with the approved target, so the surface it re-renders has to move with
    # it — a control that swaps `#teema-pais` with the rail replaces the header
    # band with a rail and the value it just wrote disappears
    # (docs/adr/0074 §2, templates/matters/partials/header.html).
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
    # opinion material now is (docs/adr/0061).
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

    try:
        with transaction.atomic():
            # The summary this form was opened on, checked against the locked
            # row: a stale tab must not replace a colleague's newer paragraph
            # with an edit of the older one (ENG-028).
            matter = guard_matter_field_revision(
                matter=matter, field="brief_summary", expected=request.POST.get("revision")
            )
            set_brief_summary(
                matter=matter,
                value=form.cleaned_data.get("brief_summary") or "",
                actor=request.user,
            )
    except MatterFieldConflict as conflict:
        # The paragraph above the box shows what is stored now; the box keeps
        # what this person typed, and the token moves to the stored version so
        # that pressing Salvesta again — having read both — is a decision
        # rather than another refusal. Nothing they wrote is thrown away.
        matter = conflict.current
        context = _header_context(request, matter)
        context["summary_form"] = form
        context["summary_revision"] = matter_field_revision(matter, "brief_summary")
        context["summary_open"] = True
        context["field_error"] = str(conflict)
        return render(request, "matters/partials/header.html", context, status=409)
    matter.refresh_from_db()
    context = _header_context(request, matter)
    context["summary_form"] = BriefSummaryForm(initial={"brief_summary": matter.brief_summary})
    return render(request, "matters/partials/header.html", context)


@login_required
@business_write_required
@require_http_methods(["POST"])
def save_note(request: HttpRequest, pk: Any) -> HttpResponse:
    """Autosave the private `Märkmed` draft, and say when it landed.

    **The textarea is never swapped.** The person is mid-sentence, and replacing
    the box they are typing into would move their cursor. What comes back is the
    hint beside it — `Salvestatud 14:16` — which is the approved target's whole
    feedback for this control now that it has no save button
    (TEEMA_TARGET_SPEC §G.4, docs/adr/0074 §18).

    It used to answer 204 and swap nothing, on the reasoning that there was
    nothing to show. There was, and the target names it: a box that saves
    silently and has no button is a box a person cannot tell has saved.

    A refusal still answers 400 and htmx still swaps nothing on it, so a failed
    save leaves the previous time in place rather than claiming one that did not
    happen. The note is still private, still one row per author, and still
    writes no `ChangeEvent` (app/matters/services.py).
    """
    matter = get_visible_matter(request, pk)
    form = PersonalNoteForm(request.POST, prefix=NOTE_PREFIX)
    if not form.is_valid():
        return HttpResponse(status=400)
    try:
        record = save_personal_note(
            matter=matter,
            author=request.user,
            body=form.cleaned_data.get("body") or "",
            expected_revision=form.cleaned_data.get("revision") or "",
        )
    except PersonalNoteConflict as conflict:
        return _note_conflict(request, conflict)
    except DomainError:
        return HttpResponse(status=400)
    return render(
        request,
        "matters/partials/note_saved.html",
        {
            "note_saved_at": record.updated_at,
            "note_revision": personal_note_revision(record),
            # The hidden field's own name and id, so the out-of-band swap that
            # moves the token forward addresses the element the form rendered
            # rather than a spelling of the prefix copied into a template.
            "note_revision_name": f"{NOTE_PREFIX}-revision",
            "note_revision_id": f"id_{NOTE_PREFIX}-revision",
        },
    )


def _note_conflict(request: HttpRequest, conflict: PersonalNoteConflict) -> HttpResponse:
    """Somebody else's newer note, and the one this tab could not save.

    **409, and nothing was written.** The status is the honest one for a
    conflict, and `app.js`'s `htmx:beforeSwap` allows it through so that the
    answer is actually shown — dropping it would leave the box looking as though
    the autosave had worked.

    What comes back is deliberately *not* the textarea. The person's own words
    stay exactly where they are, cursor included; what arrives is the hint slot
    carrying the sentence, and, out of band, the newer version to read. The
    hidden revision is **not** updated: adopting the newer token here would be
    this view deciding that the next keystroke may overwrite what the other tab
    saved, which is the defect with one more step in it (QA-09).
    """
    return render(
        request,
        "matters/partials/note_conflict.html",
        {
            "note_conflict": str(conflict),
            "note_server_body": conflict.current.body,
            "note_server_saved_at": conflict.current.pk and conflict.current.updated_at,
        },
        status=409,
    )


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
    # Bounded, not only non-negative: `?nihe=999999999999` was a `bigint out of
    # range` in the SQL `OFFSET` (ENG-046). Past the end is the first slice,
    # as any other unreadable offset is.
    offset = bounded_int(request.GET.get("nihe"), default=0)

    only = _timeline_filter(request)
    items, has_more = matter_timeline(
        matter=matter, user=request.user, limit=TIMELINE_PAGE_SIZE, offset=offset, only=only
    )
    return render(
        request,
        "matters/partials/timeline_items.html",
        {
            "matter": matter,
            "timeline_items": items,
            "timeline_has_more": has_more,
            "next_offset": offset + TIMELINE_PAGE_SIZE,
            "timeline_only": only,
        },
    )


#: How many technical change rows `Kõik muudatused` shows at a time.
#:
#: Larger than the chronology's page, because this is a log somebody scans for a
#: particular write rather than a history they read — and every row is one line.
CHANGE_LOG_PAGE_SIZE = 100

#: The furthest `?nihe=` this page will honour, and the reason it has a ceiling
#: at all.
#:
#: `OFFSET` is a 64-bit signed integer in PostgreSQL, and `?nihe=` arrives as
#: text from a URL somebody can type. Handing `2**63` straight to a slice earns a
#: `DataError` from the driver and an HTTP 500 from a read-only audit page —
#: a malformed query string is a bad request, not a server fault.
#:
#: A *clamp* rather than a rejection, because there is nothing on the far side
#: of this number for anybody: a million rows is beyond the longest history this
#: product will hold by two orders of magnitude, and a reader who reached the end
#: of the log by pressing «Näita varasemaid» can never arrive here. Clamping
#: shows the last honourable page; refusing would show an error to somebody who
#: mistyped a digit.
CHANGE_LOG_MAX_OFFSET = 1_000_000


def _change_log_offset(raw: Any) -> int:
    """`?nihe=` as a number this page can put in a SQL `OFFSET`.

    Text that is not a number, a negative number and a number past
    :data:`CHANGE_LOG_MAX_OFFSET` all resolve to a page that exists. Nothing
    here raises, because every one of those is a URL a person can type and none
    of them is a fault of the server's.
    """
    try:
        offset = int(raw)
    except (TypeError, ValueError):
        return 0
    return min(max(0, offset), CHANGE_LOG_MAX_OFFSET)


@login_required
def matter_changes(request: HttpRequest, pk: Any) -> HttpResponse:
    """`Kõik muudatused` — the technical change history, read-only.

    **The other half of docs/adr/0092.** `Teema käik` reads as a professional
    case history because it projects canonical acts and leaves field-level
    writes out; this is where those writes stay readable, so the primary history
    was made legible without auditability being traded away (§11).

    **Permission-safe by construction, not by filtering afterwards.**
    `scope_change_events` is applied to the queryset, so a row about a child this
    reader may not see is never in the population: it cannot be counted, cannot
    shift the pagination and cannot appear as a gap. That is the same chokepoint
    the chronology reads through, and the reason this page could be added at all
    — an audit surface that needed a new authorization story would not have been
    worth the risk of getting one wrong (AUTH-003, app/audit/visibility.py).

    **And fail-closed about which families it asks for.** `change_log_event_types`
    is an explicit vocabulary: Matter-level types somebody has classified as safe
    at Matter visibility, plus the child families `scope_change_events` knows how
    to scope. It exists because this is the first surface that wanted
    «everything», and `scope_change_events` passes an *unclassified* family
    through as Matter-level — which is right for `MATTER_CREATED` and wrong for
    `MATTER_RELATION_ADDED`, whose summary names another Matter this reader may
    be refused with a 404. Unknown therefore means absent here, not allowed, and
    a family arrives on this page when somebody classifies it rather than when
    somebody adds it (docs/adr/0092 §11).

    **No payloads and no identifiers.** When, who, which kind of change, and the
    summary the write recorded. `ChangeEvent.payload` is not rendered, no primary
    key is printed and no `operation_id` is: an operation identifier is a fact
    about how something was written, not something a lawyer has any use for.

    `SecurityAuditEvent` is a different record with different readers and is not
    reachable from here (master specification 16.5).
    """
    matter = get_visible_matter(request, pk)
    offset = _change_log_offset(request.GET.get("nihe", 0))

    # **The database does the skipping.** The queryset is sliced to this window
    # and no other, so page four costs four hundred rows less than it used to:
    # the previous spelling fetched `offset + 101` rows and threw the first
    # `offset` of them away in Python, which made the tenth page ten times the
    # work of the first for the same hundred lines on screen.
    #
    # One row past the page, and that row is the whole of `has_more` — a second
    # `COUNT(*)` over a scoped population to answer a yes/no question is a query
    # this page does not need (`timeline_page` pages the chronology the same way).
    page = list(
        scope_change_events(
            ChangeEvent.objects.filter(matter=matter, event_type__in=change_log_event_types()),
            request.user,
        )
        .select_related("actor")
        .order_by("-occurred_at", "-created_at", "-id")[offset : offset + CHANGE_LOG_PAGE_SIZE + 1]
    )
    has_more = len(page) > CHANGE_LOG_PAGE_SIZE
    del page[CHANGE_LOG_PAGE_SIZE:]
    return render(
        request,
        "matters/matter_changes.html",
        {
            "matter": matter,
            "changes": page,
            "has_more": has_more,
            "offset": offset,
            "next_offset": min(offset + CHANGE_LOG_PAGE_SIZE, CHANGE_LOG_MAX_OFFSET),
            "previous_offset": max(0, offset - CHANGE_LOG_PAGE_SIZE),
        },
    )


#: How `Tühista` asks for the row back in its read state.
#:
#: Named once and handed to the template as `entry_read_query`, rather than
#: spelled in both places. The view reads the parameter and the button writes
#: it, and a query string written twice is two places for one of them to be
#: changed alone — after which the cancel button silently reopens the editor
#: instead of leaving it.
ENTRY_READ_PARAM = "vaade"
ENTRY_READ_VALUE = "lugemine"
ENTRY_READ_QUERY = f"?{ENTRY_READ_PARAM}={ENTRY_READ_VALUE}"

#: The same question, for the `Ülevaade / uudis` link a chronology row shows.
#: Its own constants rather than the entry's reused, so the two controls can
#: never be tied to each other by a value one of them changes.
WEBSITE_OVERVIEW_READ_PARAM = "vaade"
WEBSITE_OVERVIEW_READ_VALUE = "lugemine"
WEBSITE_OVERVIEW_READ_QUERY = f"?{WEBSITE_OVERVIEW_READ_PARAM}={WEBSITE_OVERVIEW_READ_VALUE}"


def _entry_edit_form(entry: Entry, data: Any = None) -> EntryEditForm:
    """One entry's edit form, with ids nothing else on the page can share.

    Field *names* stay `body` and `revision` — the POST handler and its tests
    read one spelling — while the ids, and the `<label for>` that follows them,
    are per row. Two rows in edit mode at once is an ordinary thing to do on a
    page whose whole subject is a list, and two elements sharing an id is enough
    to make a label reach the wrong box (`workspace_attachments` in
    `app/matters/forms.py` names the same defect, on the same page).
    """
    auto_id = f"id_sissekanne_{entry.pk}_%s"
    if data is not None:
        return EntryEditForm(data, auto_id=auto_id)
    return EntryEditForm(
        initial={"body": entry.body, "revision": entry_revision_token(entry)},
        auto_id=auto_id,
    )


def _entry_for_correction(request: HttpRequest, matter: Matter, entry_id: Any) -> Entry:
    """The entry this request is allowed to correct, or a 404.

    Scoped through the child's own `visible_to` and not fetched by id off the
    Matter, exactly as `update_engagement_view` does it: an entry may carry a
    stricter visibility override than its parent, and reading it any other way
    would bypass that. A restricted entry inside a Matter somebody may see is
    therefore indistinguishable here from an entry that does not exist, which is
    the contract the rest of the product keeps (AUTH-003).
    """
    return get_object_or_404(
        Entry.objects.visible_to(request.user).filter(matter=matter), pk=entry_id
    )


def _entry_row(
    request: HttpRequest,
    matter: Matter,
    entry: Entry,
    *,
    form: EntryEditForm | None = None,
    error: str = "",
    conflict: Entry | None = None,
    status: int = 200,
) -> HttpResponse:
    """The corrected entry back in place, or the form that could not save.

    One renderer for both, because they swap the same element: `Muuda` replaces
    the body region with the form, and every answer replaces it again — with
    the corrected text, or with the form still open and what the person typed
    still in it. The row itself, its dot, its time, its files and its next-step
    pill are never in the response, so nothing can move them.
    """
    return render(
        request,
        "matters/partials/entry_body.html",
        {
            "matter": matter,
            "entry": entry,
            "entry_edit_form": form,
            "entry_edit_error": error,
            "entry_conflict": conflict,
            # The `muudetud` marker lives in the meta line above this element, so
            # a correction has to reach it out of band. Rendered on every answer
            # and not only the successful one: it is idempotent, and a response
            # that left it out after a refusal would be indistinguishable from
            # one that meant to clear it.
            "entry_edit_swap_marker": True,
            "entry_read_query": ENTRY_READ_QUERY,
        },
        status=status,
    )


@login_required
@business_write_required
@require_http_methods(["GET", "POST"])
def edit_entry_view(request: HttpRequest, pk: Any, entry_id: Any) -> HttpResponse:
    """`Muuda` — correct what an already-filed Sissekanne says.

    **Available on a closed Matter too, and that is the point.** Closure means
    no new business work: no note, no next step, no consultation, no upload,
    every one of those still refused by its own route's row lock. It has never
    meant that a fact recorded wrongly in 2023 must stay wrong. Correcting one
    creates no `Entry`, moves nothing in the chronology, reopens nothing and
    leaves `is_open`, `closed_at` and `disposition` exactly as they were —
    `edit_entry` does not so much as read them (docs/adr/0075 §12,
    tests/test_entry_correction.py).

    Behind `business_write_required` and nothing narrower. A colleague who may
    author business content may correct it; this is not an owner-only, a
    head-only or a creator-only capability, because a typo in a colleague's
    entry is the department's problem and not that colleague's alone
    (docs/adr/0042, `may_write_business_content`). A
    reader gets the decorator's 404 — the same answer the route gives for a
    Matter that does not exist, so a refusal describes no surface.

    GET opens the form; POST saves it. One route, because they are one
    interaction and the second is only reachable from the first.
    """
    matter = get_visible_matter(request, pk)
    entry = _entry_for_correction(request, matter, entry_id)

    if request.method == "GET":
        # `Tühista`. Leaving edit mode is a re-read rather than a client-side
        # hide: the box may be holding text that was never saved, and the only
        # honest way out of it is to go and fetch what the record actually says.
        # Nothing is written on this path — it is a GET, and it takes no lock.
        if request.GET.get(ENTRY_READ_PARAM) == ENTRY_READ_VALUE:
            return _entry_row(request, matter, entry)
        return _entry_row(request, matter, entry, form=_entry_edit_form(entry))

    form = _entry_edit_form(entry, request.POST)
    if not form.is_valid():
        # The form comes back bound, so what the person typed is still in the
        # box — and the entry is untouched, so nothing was lost either way.
        return _entry_row(request, matter, entry, form=form, status=400)

    try:
        edit_entry(
            entry=entry,
            body=form.cleaned_data["body"],
            actor=request.user,
            expected_revision=form.cleaned_data.get("revision") or "",
        )
    except EntryEditConflict as conflict:
        # 409, and nothing was written. The form stays open holding this
        # person's words, and the version that beat them arrives beside it to
        # read — neither is chosen for them. The hidden token is **not**
        # advanced: adopting the newer one here would be this view deciding that
        # the next submit may overwrite what the other writer saved, which is
        # the defect with one more step in it (`_note_conflict`, QA-09).
        return _entry_row(
            request,
            matter,
            entry,
            form=form,
            error=str(conflict),
            conflict=conflict.current,
            status=409,
        )
    except DomainError as error:
        return _entry_row(request, matter, entry, form=form, error=str(error), status=400)

    entry.refresh_from_db()
    return _entry_row(request, matter, entry)


# ---------------------------------------------------------------------------
# `Ülevaade / uudis`
# ---------------------------------------------------------------------------
#
# Four routes for four things that happen to one record: a plan is recorded, it
# is published, it is dropped, or an address already on the file turns out to be
# wrong. Three of them are new business content and take the closed-Matter lock
# in `app.matters.workspace`; the fourth is a correction and deliberately does
# not (docs/adr/0081 §5).


def _website_overview_for(
    request: HttpRequest, matter: Matter, overview_id: Any
) -> MatterWebsiteOverview:
    """The overview this request may act on, or a 404.

    Scoped through the child's own `visible_to` rather than fetched by id off
    the Matter, exactly as `_entry_for_correction` and `update_engagement_view`
    do it: the record carries its own `visibility_override`, and reading it any
    other way would bypass that. A restricted overview inside a Matter somebody
    may see is indistinguishable here from one that does not exist (AUTH-003).
    """
    return get_object_or_404(
        MatterWebsiteOverview.objects.visible_to(request.user).filter(matter=matter),
        pk=overview_id,
    )


def _website_overview_link_form(
    overview: MatterWebsiteOverview, data: Any = None
) -> WebsiteOverviewLinkForm:
    """One overview's address form, with ids nothing else on the page can share.

    Field *names* stay `url`, `published_on` and `revision` — the POST handler
    and its tests read one spelling — while the ids, and the `<label for>` that
    follows them, are per row. A Matter may owe or hold several overviews, and
    two elements sharing an id is enough to make a label reach the wrong box
    (`_entry_edit_form`, `workspace_attachments`).

    **A planned row is given no `published_on` initial, and that is not an
    oversight.** Passing `None` explicitly would override the field's own
    default and leave the box empty; leaving it out lets today through, which is
    what somebody recording a publication almost always wants — visibly, in a
    box they can change or clear (docs/adr/0078 §2). A published row is filled
    from what it actually says, because there the form is a correction.
    """
    auto_id = f"id_kodulehe_ulevaade_{overview.pk}_%s"
    if data is not None:
        return WebsiteOverviewLinkForm(data, auto_id=auto_id)
    initial: dict[str, Any] = {"revision": overview.revision_token}
    if overview.is_published:
        initial["url"] = overview.url
        initial["published_on"] = overview.published_on
    return WebsiteOverviewLinkForm(initial=initial, auto_id=auto_id)


def _planned_website_overview_rows(
    matter: Matter,
    user: Any,
    *,
    bound_for: Any = None,
    form: WebsiteOverviewLinkForm | None = None,
) -> list[tuple[MatterWebsiteOverview, WebsiteOverviewLinkForm]]:
    """Each planned overview with the form that publishes it.

    Paired here rather than in the template for the reason the retired
    `engagement_rows` was (docs/adr/0074 §9, removed with the standing
    `Kaasamine` section): exactly one row may render a *bound* form — the one a
    refusal came back for — and deciding that in the template would mean
    comparing ids in three places. Every other row gets its own unbound form,
    so a refused `Avalda` on one plan cannot put somebody's typed address into
    the box beside another.
    """
    rows = []
    for record in selectors.planned_website_overviews(matter, user):
        if form is not None and bound_for is not None and str(record.pk) == str(bound_for):
            rows.append((record, form))
        else:
            rows.append((record, _website_overview_link_form(record)))
    return rows


def _website_overview_refusal(
    request: HttpRequest,
    matter: Matter,
    *,
    overview: MatterWebsiteOverview | None,
    form: Any = None,
    error: str = "",
    status: int = 400,
) -> HttpResponse:
    """Re-render the column with the strip's own refusal in the row it came from.

    Deliberately **not** `_workspace_refusal`. That helper answers a refusal by
    reopening a launcher panel, and these two operations have no launcher panel:
    `Avalda` and `Tühista` live on a planned row in the strip, which is rendered
    from the Matter's own records rather than from a chip somebody clicked. A
    refusal routed through the panel machinery would set `open_panel` to the
    empty string and the sentence would render nowhere at all.

    The bound form and the refused row's id travel together, so the strip
    reopens exactly the disclosure the words were typed into and puts them back
    in it — the same promise every other refused save on this page keeps.

    **There is no separate «the version that beat you» block here**, and there
    does not need to be. The strip is re-rendered from `_overview_context`, which
    has just re-read the records, so the row beside the refusal already *is* what
    the file now says — published by the other tab, or gone from the planned list
    entirely — and the sentence explains why this save did not land. Nothing was
    written. The stale token deliberately stays in the bound form: advancing it
    would be this view deciding that the next submit may overwrite what the other
    writer saved (`edit_entry_view`, QA-09).
    """
    context = _overview_context(request, matter)
    context.update(_header_context(request, matter))
    if overview is not None and form is not None:
        # A refused `Avalda`: the disclosure reopens on this row and the form
        # comes back bound, so what was typed is still in the boxes.
        context["website_overview_open"] = str(overview.pk)
        context["planned_website_overviews"] = _planned_website_overview_rows(
            matter, request.user, bound_for=overview.pk, form=form
        )
    # A refused `Tühista` has no form and therefore no panel to reopen — opening
    # `Avalda` to explain why a cancellation failed would answer a refusal by
    # offering a different operation. Its sentence goes under the list instead,
    # which is where the strip prints a refusal no row owns.
    context["website_overview_error"] = error
    body = render_to_string("matters/partials/overview.html", context, request=request)
    return HttpResponse(body, status=status)


@login_required
@business_write_required
@require_http_methods(["POST"])
def add_website_overview(request: HttpRequest, pk: Any) -> HttpResponse:
    """`+ Ülevaade / uudis` — a page that is already published.

    The panel's two boxes record a publication. The address is required and the
    day opens on today, so an empty save is a refusal naming the address rather
    than a plan filed by somebody who thought they had cancelled
    (docs/adr/0095 §5).

    **Plans are not gone.** `plan_website_overview` still writes one, every
    stored plan still reads on the file, and `Avalda` and `Tühista` still act on
    it. What this endpoint no longer does is create one from a blank form.

    A refusal comes back through `_workspace_refusal` with the form still bound,
    so an address somebody pasted is still in the box — losing it would cost
    them the one fact they opened the panel to record. The closed-Matter refusal
    is the service's, answered under the row lock, because a POST may arrive
    from a tab that was open before somebody else shut the file (R2-02).
    """
    matter = get_visible_matter(request, pk)
    form = CompactWebsiteOverviewForm(request.POST)
    if not form.is_valid():
        return _workspace_refusal(request, matter, key="website_overview_form", form=form)
    publication = form.cleaned_data.get("publication")
    try:
        workspace.add_matter_website_overview(
            matter=matter,
            author=request.user,
            url=publication[0] if publication else "",
            published_on=publication[1] if publication else None,
        )
    except DomainError as error:
        return _workspace_refusal(
            request, matter, key="website_overview_form", form=form, error=str(error)
        )
    return _render_overview(request, matter)


@login_required
@business_write_required
@require_http_methods(["POST"])
def publish_website_overview_view(request: HttpRequest, pk: Any, overview_id: Any) -> HttpResponse:
    """`Avalda` — the page is up, and this is where it is.

    New business content, so a closed Matter refuses it under the lock rather
    than by not rendering the control. The address goes through
    `normalize_overview_news_url` on the form *and* in the service: the first is
    where a person sees the refusal beside what they typed, and the second is
    what a crafted POST meets.
    """
    matter = get_visible_matter(request, pk)
    overview = _website_overview_for(request, matter, overview_id)
    # The row's own form, so a refusal comes back with this row's ids rather
    # than Django's defaults: the strip draws every other planned row with its
    # own form, and a bare `WebsiteOverviewLinkForm` here put a second
    # `id_url` and `id_published_on` beside them, which sent the labels and
    # the date helper to the wrong row (ENG-093). The field names are the
    # same, so what is read from the POST is unchanged.
    form = _website_overview_link_form(overview, data=request.POST)
    if not form.is_valid():
        return _website_overview_refusal(request, matter, overview=overview, form=form)
    try:
        workspace.publish_planned_website_overview(
            matter=matter,
            author=request.user,
            overview=overview,
            url=form.cleaned_data["url"],
            published_on=form.cleaned_data["published_on"],
            expected_revision=form.cleaned_data.get("revision") or None,
        )
    except WebsiteOverviewConflict as conflict:
        return _website_overview_refusal(
            request,
            matter,
            overview=overview,
            form=form,
            error=str(conflict),
            status=409,
        )
    except DomainError as error:
        return _website_overview_refusal(
            request, matter, overview=overview, form=form, error=str(error)
        )
    return _render_overview(request, matter)


@login_required
@business_write_required
@require_http_methods(["POST"])
def cancel_website_overview_view(request: HttpRequest, pk: Any, overview_id: Any) -> HttpResponse:
    """`Tühista` — the write-up is not going to happen after all.

    The row stays on the file as a cancelled plan and reads in the chronology.
    Nothing is deleted here, and a published overview is refused: a record
    claiming a page was never published would be the file disagreeing with the
    website (docs/adr/0081 §1).
    """
    matter = get_visible_matter(request, pk)
    overview = _website_overview_for(request, matter, overview_id)
    try:
        workspace.cancel_matter_website_overview(
            matter=matter,
            author=request.user,
            overview=overview,
            expected_revision=request.POST.get("revision") or None,
        )
    except WebsiteOverviewConflict as conflict:
        return _website_overview_refusal(
            request,
            matter,
            overview=overview,
            error=str(conflict),
            status=409,
        )
    except DomainError as error:
        return _website_overview_refusal(request, matter, overview=overview, error=str(error))
    return _render_overview(request, matter)


def _website_overview_link_row(
    request: HttpRequest,
    matter: Matter,
    overview: MatterWebsiteOverview,
    *,
    form: WebsiteOverviewLinkForm | None = None,
    error: str = "",
    conflict: MatterWebsiteOverview | None = None,
    status: int = 200,
) -> HttpResponse:
    """The published overview's link region, read-only or in correction mode.

    One renderer for both, because they swap the same element: `Paranda link`
    replaces the link with the form, and every answer replaces it again — with
    the corrected address, or with the form still open and what the person typed
    still in it. The chronology row around it, its dot, its date and its
    position are never in the response, so a correction cannot move the line
    (`_entry_row`, which this mirrors exactly).
    """
    return render(
        request,
        "matters/partials/website_overview_link.html",
        {
            "matter": matter,
            "overview": overview,
            "website_overview_link_form": form,
            "website_overview_link_error": error,
            "website_overview_link_conflict": conflict,
            "website_overview_read_query": WEBSITE_OVERVIEW_READ_QUERY,
            # The milestone's date cell rides along, out of band. Set here and
            # nowhere else: the chronology includes this same element on every
            # ordinary render, and emitting the cell there would print the day
            # twice inside the row that already shows it (docs/adr/0089 §10).
            "website_overview_oob": True,
        },
        status=status,
    )


@login_required
@business_write_required
@require_http_methods(["GET", "POST"])
def correct_website_overview_view(request: HttpRequest, pk: Any, overview_id: Any) -> HttpResponse:
    """`Paranda link` — what the file says about an existing page was wrong.

    **Available on a closed Matter too, and that is the point.** Closure means
    no new business content: no new plan, no publication, no cancellation, each
    refused by its own route's row lock. It has never meant that an address
    recorded wrongly must stay wrong. Correcting one creates no record, moves
    nothing in the chronology, reopens nothing and leaves `is_open`, `closed_at`
    and `disposition` exactly as they were — `correct_website_overview_link`
    does not so much as read them (docs/adr/0075 §12, docs/adr/0081 §5).

    It also cannot become a way to publish a plan on a closed file: the service
    refuses anything whose status, read under the lock, is not already
    `Avaldatud`.

    Behind `business_write_required` and nothing narrower, for the reason
    `edit_entry_view` gives: a wrong address on the file is the department's
    problem and not its author's alone (docs/adr/0042).

    GET opens the form; POST saves it. One route, because they are one
    interaction and the second is only reachable from the first.
    """
    matter = get_visible_matter(request, pk)
    overview = _website_overview_for(request, matter, overview_id)

    if request.method == "GET":
        # `Tühista`. Leaving correction mode is a re-read rather than a
        # client-side hide: the boxes may be holding an address that was never
        # saved, and the only honest way out is to fetch what the record says.
        if request.GET.get(WEBSITE_OVERVIEW_READ_PARAM) == WEBSITE_OVERVIEW_READ_VALUE:
            return _website_overview_link_row(request, matter, overview)
        return _website_overview_link_row(
            request, matter, overview, form=_website_overview_link_form(overview)
        )

    form = _website_overview_link_form(overview, request.POST)
    if not form.is_valid():
        return _website_overview_link_row(request, matter, overview, form=form, status=400)

    try:
        workspace.correct_matter_website_overview(
            author=request.user,
            overview=overview,
            url=form.cleaned_data["url"],
            published_on=form.cleaned_data["published_on"],
            expected_revision=form.cleaned_data.get("revision") or None,
        )
    except WebsiteOverviewConflict as conflict:
        # 409, and nothing was written. The form stays open holding this
        # person's address, and the version that beat them arrives beside it to
        # read. The hidden token is **not** advanced: adopting the newer one
        # here would be this view deciding that the next submit may overwrite
        # what the other writer saved (`edit_entry_view`, QA-09).
        return _website_overview_link_row(
            request,
            matter,
            overview,
            form=form,
            error=str(conflict),
            conflict=conflict.current,
            status=409,
        )
    except DomainError as error:
        return _website_overview_link_row(
            request, matter, overview, form=form, error=str(error), status=400
        )

    overview.refresh_from_db()
    return _website_overview_link_row(request, matter, overview)


def matter_url(matter: Matter) -> str:
    return reverse("matters:matter_detail", kwargs={"pk": matter.pk})


ACTION_KIND_LABELS = dict(ActionKind.choices)


# ---------------------------------------------------------------------------
# The Teema workspace: PRAEGUNE TEGEVUS and LISA TEEMALE
# ---------------------------------------------------------------------------
#
# Eight endpoints where there was one. Each takes one form, calls one use case
# and re-renders the column; a refused save comes back with its own panel open,
# its own errors, and every other panel shut and untouched. That last part is
# the point of splitting them: a single global save meant an invalid `Töövõit`
# could refuse a note somebody had also typed, and a valid one could write a
# closure they had merely opened (docs/adr/0075 §2).


#: Which controls a refusal has to reopen, per operation: the top-level family
#: and, where the family asks a second question, the choice inside it.
#:
#: A refusal reopens exactly the path it came from — reopening the whole group
#: would answer a refusal by offering six other forms, and reopening none would
#: print the error inside a panel nobody can see (brief §33).
#:
#: **Two levels since docs/adr/0097 §8**, because `LISA TEEMALE` is four choices
#: rather than twelve. `+ Oluline tähtaeg` is no longer a chip; it is
#: `Märke liik · Oluline tähtaeg` inside `+ Märge`, so a refused deadline has to
#: reopen both or the person is looking at the wrong form — or at no form at
#: all, holding a sentence about a panel that is shut.
#:
#: An empty second element means the family asks no further question and its own
#: panel is the form.
WORKSPACE_PANELS: dict[str, tuple[str, str]] = {
    # `+ Märge` — one visible family, four truthful record types underneath.
    "progress_form": ("lisa-marge", "marge-tavaline"),
    "important_date_form": ("lisa-marge", "marge-tahtaeg"),
    "effective_date_form": ("lisa-marge", "marge-joustumine"),
    "work_victory_form": ("lisa-marge", "marge-toovoit"),
    # `+ Kaasamine` — one question, no sub-choice.
    "add_engagement_form": ("lisa-kaasamine", ""),
    # `+ Arvamus / tagasiside` — grouped on screen, three distinct records.
    "received_feedback_form": ("lisa-arvamus", "arvamus-tagasiside"),
    "external_position_form": ("lisa-arvamus", "arvamus-teiste"),
    "koda_opinion_form": ("lisa-arvamus", "arvamus-koja"),
    # `+ Ülevaade / uudis` — one question, no sub-choice.
    "website_overview_form": ("lisa-koduleht", ""),
    # `TEEMA TOIMINGUD` — not content, and not in the launcher at all. Its panel
    # lives in its own section and is named here so a refused closure still
    # reopens the control it came from (docs/adr/0097 §9).
    "closure_form": ("teema-lopeta", ""),
    # `PRAEGUNE TEGEVUS` → `Muuda`, which is not in the launcher either.
    "action_form": ("lisa-jargmine", ""),
}


#: Which sub-choice each family falls back to when no refusal names one of its
#: own. Both are the ordinary case rather than the first alphabetically: a
#: `Märge` is usually just a note, and most of what reaches a department is
#: somebody answering it.
WORKSPACE_DEFAULT_CHOICES: dict[str, str] = {
    "marge_choice": "marge-tavaline",
    "arvamus_choice": "arvamus-tagasiside",
}

#: Which family each sub-choice belongs to, so a refusal can be routed to the
#: one variable that owns it.
WORKSPACE_CHOICE_FAMILY: dict[str, str] = {
    "marge-tavaline": "marge_choice",
    "marge-tahtaeg": "marge_choice",
    "marge-joustumine": "marge_choice",
    "marge-toovoit": "marge_choice",
    "arvamus-tagasiside": "arvamus_choice",
    "arvamus-teiste": "arvamus_choice",
    "arvamus-koja": "arvamus_choice",
}


def _workspace_choices(open_choice: str) -> dict[str, str]:
    """One variable per sub-choice group, each naming one of that group's own ids.

    The template cannot do this with `open_choice` alone, and the failure is
    quiet rather than loud. `open_choice` is global to the page: when a refusal
    comes from `+ Arvamus / tagasiside` it names one of *that* family's
    children, so every `if open_choice == "marge-…"` test in the `+ Märge` group
    is false at once and that group renders with **no radio checked**. Opening
    `+ Märge` afterwards then shows four chips and no form — nothing errors,
    nothing logs, and the page is simply missing a control.

    So each group is told which of *its* ids is chosen, and the answer is always
    one of them (docs/adr/0097 §8.3).
    """
    choices = dict(WORKSPACE_DEFAULT_CHOICES)
    family = WORKSPACE_CHOICE_FAMILY.get(open_choice)
    if family is not None:
        choices[family] = open_choice
    return choices


def workspace_forms(
    current_action: Any = None,
    *,
    matter: Any = None,
    viewer: Any = None,
    phases: Any = None,
) -> dict[str, Any]:
    """One unbound form per write intention, for an ordinary render.

    Built here rather than in the template because a template that constructed
    its own forms would be a template deciding what may be written.

    `NextActionForm` is prefilled from the open step, because the control that
    renders it while one exists is `Muuda` — an editor, and an editor that opens
    empty is asking somebody to retype what is already on the screen. With no
    open step the same form is `+ Järgmine tegevus` and has nothing to prefill
    from. A bound form ignores `initial` either way, so a refused save still
    comes back carrying what was typed (brief §9, §15).

    **The institution catalogue is read once for the whole bar.** Three of these
    forms ask the same question over the same catalogue, and each of them used to
    answer it with four queries of its own — which was the right trade while
    `+ Väline seisukoht` was the only such panel and is not one when there are
    three: the page went from 38 queries to 49 and blew the budget
    `tests/test_teema_redesign.py` holds. One `read_organisation_choices` here,
    handed to each control, and every shortlist is still sliced per control in
    Python exactly as before (docs/adr/0091 §3.5).
    """
    organisations = read_organisation_choices(viewer)
    # And this Matter's consultations, for the two `Seotud kaasamine` controls.
    # Same reasoning as the catalogue above: one read for two identical selects.
    # The *queryset* each field validates against is still set per form, so
    # nothing about the authorization boundary is shared or weakened (AUTH-003).
    engagements = visible_engagements_of(matter, viewer)
    return {
        "current_action_form": CompleteCurrentActionForm(),
        # `+ Märge · Tavaline`. What happened, when, optionally the stage it
        # moves the file to and the next thing the lawyer will do about it —
        # one atomic operation over three canonical services.
        #
        # This one key replaces two: `matter_note_form` (an `Entry`) and
        # `development_form` (a `MatterProceduralDevelopment`). They were two
        # chips asking the same question with different amounts of ceremony, and
        # `MatterProgressForm` says at length why the survivor writes the
        # structured record rather than the prose one (docs/adr/0097 §6).
        "progress_form": MatterProgressForm(phases=phases),
        # The period travels with the text. Reopening the editor on `Täpne
        # päev` / `01.10.2026` for a step recorded as *oktoober 2026* would
        # invite somebody to save the invented day back, which is the whole
        # defect arriving through the edit path (docs/adr/0079 §3).
        "action_form": NextActionForm(
            periods=True,
            current=current_action,
            initial=(
                {
                    "text": current_action.text,
                    **period_initial(
                        "next",
                        current_action.target_date,
                        current_action.date_precision,
                        date_field="target_date",
                    ),
                }
                if current_action is not None
                else None
            ),
        ),
        "add_engagement_form": CompactEngagementForm(),
        "important_date_form": CompactImportantDateForm(),
        "effective_date_form": CompactEffectiveDateForm(),
        "work_victory_form": CompactWorkVictoryForm(),
        # `+ Ülevaade / uudis`. Two optional boxes and no kind selector: an
        # empty submit is the plan, both boxes filled record a page that is
        # already up, and which of the two kinds of publication it is, is what
        # the address says (docs/adr/0083, docs/adr/0085 §1). Built here like
        # the other seven so that a refusal comes back through the same
        # machinery.
        "website_overview_form": CompactWebsiteOverviewForm(),
        # `Menetluse link` is **not** built here any more, because it is not a
        # thing that happened to this Matter. It is where the proceeding the
        # Matter is about is taking place — a fact about the file, in the same
        # way its `Saatja` and its `Õigusakt` are — and it is asked on
        # `Uus teema`, corrected on `Muuda teemat` and read on the rail
        # (`MatterLinkForm`, docs/adr/0097 §5).
        # `+ Väline seisukoht`. The one form here that has to be told which
        # Matter it is on and who is looking: `Organisatsioon` is ranked by the
        # institutions *this reader's* visible Matters involve, and
        # `Seotud kaasamine` offers this Matter's consultations as this reader
        # may see them. Both are queryset-level, so a crafted POST naming a
        # round on another file is refused by the field rather than by the
        # template not having drawn it (docs/adr/0084 §4).
        # `+ Meile saadetud tagasiside` and `+ Teiste arvamus`. Two instances of
        # one form class with the provenance fixed on each, because they are two
        # professional facts with one shape — an author, a source, an optional
        # date, an optional note from the lawyer — and four models would have been
        # four sets of validation for one set of rules (docs/adr/0091 §3).
        #
        # Both have to be told which Matter they are on and who is looking:
        # `Organisatsioon` is ranked by the institutions *this reader's* visible
        # Matters involve, and `Seotud kaasamine` offers this Matter's
        # consultations as this reader may see them. Both are queryset-level, so a
        # crafted POST naming a round on another file is refused by the field
        # rather than by the template not having drawn it (docs/adr/0084 §4).
        "received_feedback_form": ReceivedFeedbackForm(
            matter=matter, viewer=viewer, choices=organisations, engagements=engagements
        ),
        "external_position_form": OtherOpinionForm(
            matter=matter, viewer=viewer, choices=organisations, engagements=engagements
        ),
        # `+ Koja arvamus`. The one panel here that writes a `Submission` rather
        # than a Matter child: Koda's own opinion is what the product has always
        # called a submission, and this is a second door onto it rather than a
        # second record of it (docs/adr/0091 §6).
        "koda_opinion_form": KodaOpinionForm(matter=matter, viewer=viewer, choices=organisations),
        # `Lõpeta teema`, which is no longer one of these at all: closing a
        # Matter is an operation on the record rather than content added to it,
        # so it renders under `TEEMA TOIMINGUD` and not in the launcher. It is
        # still built here because the refusal machinery is shared
        # (docs/adr/0097 §9).
        "closure_form": CompactClosureForm(),
        "open_panel": "",
        "open_choice": "",
        **_workspace_choices(""),
        "workspace_error": "",
    }


#: Fields that are never the person's own words, and so never worth handing
#: back in a recovery block.
#:
#: A hidden identifier, the CSRF token, a choice made from a fixed list — none of
#: those is retyped from memory, and a block that printed them would bury the
#: paragraph that is.
UNSAVED_CONTENT_SKIP: frozenset[str] = frozenset({"action_id", "attachments"})


def unsaved_content(form: Any) -> list[tuple[str, str]]:
    """What somebody typed into a form whose panel is not coming back.

    A stale-tab refusal on a **closed** Matter is correct and writes nothing —
    that boundary is not in question and is not moved. What it also did was
    re-render the workspace without the closed Matter's add panels, so the text
    went with them, while the message asked the person to reopen the Teema and
    «salvesta uuesti» something the page was no longer holding. A long note had
    to be retyped from memory (adversarial QA 2026-09-12, QA-06).

    So the words come back as **content**, not as a form: a labelled, read-only
    block they can copy. Deliberately not re-rendered controls — a `Salvesta`
    beside a closed Teema would be the page offering a write the boundary is
    there to refuse, and somebody would press it.

    Read off `form.data`, not `cleaned_data`: the refusal may be *why* there is no
    cleaned value, and what has to survive is what they typed rather than what
    validated. Bound forms only; an unbound one has nothing of anybody's in it.
    """
    if not getattr(form, "is_bound", False):
        return []
    recovered: list[tuple[str, str]] = []
    for name, field in form.fields.items():
        if name in UNSAVED_CONTENT_SKIP or isinstance(field, forms.ChoiceField):
            continue
        raw = form.data.get(form.add_prefix(name), "")
        if not isinstance(raw, str) or not raw.strip():
            continue
        recovered.append((str(field.label or name), raw.strip()))
    return recovered


def _workspace_refusal(
    request: HttpRequest,
    matter: Matter,
    *,
    key: str,
    form: Any,
    error: str = "",
) -> HttpResponse:
    """Re-render the column with one bound form, so nothing typed is lost.

    400 rather than 200, like every other refused write on this page, and the
    bound form goes back under its own key so the panel that failed is the panel
    that shows why.

    **Unless that panel is not on the page any more.** The column is re-rendered
    from the Matter as it is *now*, and three stale-tab refusals arrive at a
    page that no longer holds the form they came from: `PRAEGUNE TEGEVUS` →
    `Salvesta` after a colleague finished the step and set no new one — the
    fresh column has no open step, so no completion form and no paragraph to
    print the sentence in; `Muuda` beside that step, for the same reason and as
    of docs/adr/0097 §8.2 — the next-step editor is drawn *beside a task* and
    the launcher chip that used to draw it without one is gone, so a Matter
    with no open step now renders no `#lisa-jargmine` at all; and any
    `LISA TEEMALE` save, `+ Lõpeta teema` included, after the Matter was closed
    elsewhere, where `overview.html` renders no launcher at all.

    Put the sentence in the panel and the browser
    swaps in a 400 that looks exactly like somebody else's successful save,
    with what the person typed gone and not a word about why. So the refusal
    goes to the workspace-level slot `overview.html` already keeps for «a
    refusal no panel owns», and it is shown there, above the fresh state.

    `is_open` is re-read for that decision rather than trusted from the
    instance the view fetched before the lock: the closure this refusal is
    about may have committed between the two.

    **And a closed Matter's header rides along**, out of band, exactly as it
    does on the one successful save that closes a Matter
    (`close_from_workspace`). The refusal is telling the stale tab that the
    file is shut; leaving its header saying `Avatud` beside a column with no
    workspace would be the page contradicting itself about the one fact the
    refusal is about (docs/adr/0074 §10).
    """
    matter.refresh_from_db(fields=["is_open"])
    context = _overview_context(request, matter)
    context.update(_header_context(request, matter))
    context[key] = form
    # Both of the forms that live beside the current task, not just the
    # completion box. `action_form` joined it when `+ Järgmine tegevus` left the
    # launcher: its panel is `Muuda` inside `PRAEGUNE TEGEVUS`, which
    # `current_action.html` draws only under `{% elif current_action %}`
    # (docs/adr/0097 §8.2).
    needs_current_action = {"current_action_form", "action_form"}
    panel_is_rendered = matter.is_open and (
        key not in needs_current_action or context["current_action"] is not None
    )
    if not error and not panel_is_rendered:
        # A *validation* refusal whose panel is not on the fresh column.
        #
        # `error` is a service's sentence and has always had this fallback; a
        # form error had none, because until docs/adr/0097 §8.2 every form
        # whose panel could vanish was refused by a service rather than by
        # itself. `Muuda` is now the only host of the next-step form, so a
        # stale tab posting it on a Matter whose step somebody else finished
        # arrives here with `form.errors` and nothing to print them in — a 400
        # that looks exactly like somebody else's successful save.
        #
        # The first sentence, because these forms refuse one thing at a time
        # and a list of every message would be a paragraph about a form the
        # reader cannot see. What they typed comes back below it either way.
        error = next(
            (
                str(message)
                for messages in form.errors.values()
                for message in messages
                if str(message).strip()
            ),
            "",
        )
    if error and not panel_is_rendered:
        context["composer_error"] = error
        context["workspace_error"] = ""
        context["open_panel"] = ""
        context["open_choice"] = ""
        context.update(_workspace_choices(""))
    else:
        context["workspace_error"] = error
        # Both halves, and the template checks each against its own radio: the
        # family is what puts the person back in `+ Märge`, and the choice is
        # what puts them back on `Oluline tähtaeg` rather than on the ordinary
        # note (docs/adr/0097 §8).
        family, choice = WORKSPACE_PANELS.get(key, ("", ""))
        context["open_panel"] = family
        context["open_choice"] = choice
        context.update(_workspace_choices(choice))
    if not panel_is_rendered:
        # The panel that held their words is not on the fresh column, so the
        # words come back beside the refusal instead — read-only, and labelled
        # as unsaved (QA-06).
        context["unsaved_content"] = unsaved_content(form)
    body = render_to_string("matters/partials/overview.html", context, request=request)
    if not matter.is_open:
        context["header_out_of_band"] = True
        body += render_to_string("matters/partials/header.html", context, request=request)
    return HttpResponse(body, status=400)


@login_required
@business_write_required
@require_http_methods(["POST"])
def complete_current_action(request: HttpRequest, pk: Any) -> HttpResponse:
    """`PRAEGUNE TEGEVUS` → `Salvesta`. The result is written and the step is done.

    **One operation.** There is no `Märgi tehtuks` on this page any more and no
    second confirmation: describing what was done about the current task *is*
    completing it, which is what a lawyer means by finishing something and what
    the two-save version could never guarantee (docs/adr/0075 §3).

    The action is fetched through `visible_to` before anything else, so an
    identifier naming a step this reader may not see answers 404 rather than
    confirming that it exists — the same rule `complete_action` follows
    (AUTH-003). Whether it is still *the current one* is a different question,
    asked inside the service under a row lock (docs/adr/0075 §4).
    """
    matter = get_visible_matter(request, pk)
    form = CompleteCurrentActionForm(request.POST, request.FILES)
    if not form.is_valid():
        return _workspace_refusal(request, matter, key="current_action_form", form=form)

    action = get_object_or_404(
        NextAction.objects.visible_to(request.user),
        pk=form.cleaned_data["action_id"],
        matter=matter,
    )
    try:
        workspace.complete_current_action(
            matter=matter,
            author=request.user,
            action_id=action.pk,
            body=form.cleaned_data["body"],
            uploads=form.cleaned_data["attachments"],
        )
    except (DomainError, UploadRejected) as error:
        return _workspace_refusal(
            request, matter, key="current_action_form", form=form, error=str(error)
        )
    return _render_overview(request, matter)


@login_required
@business_write_required
@require_http_methods(["POST"])
def add_note(request: HttpRequest, pk: Any) -> HttpResponse:
    """`+ Märge · Tavaline` — something happened on this file, and this says what.

    **One endpoint where there were two.** This route and `add_development`
    asked the same question with different amounts of ceremony, and the second
    is gone: `+ Menetluse areng` is retired as a user-facing concept, its
    ordinary function is this panel, and the route that served its chip is
    removed rather than left reachable behind no button (docs/adr/0097 §6).

    Up to four canonical writes in one transaction: the
    `MatterProceduralDevelopment`, its files, the `Hetkeseis` and the next step.
    A refusal anywhere leaves the Matter exactly as it was — a stage that moved
    without the note that moved it would be a file claiming to be in the
    Riigikogu with nothing saying how it got there
    (`workspace.add_procedural_development`, docs/adr/0091 §5).

    **The `Hetkeseis` and the next step are optional and never inferred.**
    Nothing reads the sentence and concludes anything from it; a save naming
    neither changes neither; nothing is read from or written to a
    `Menetluse link`.
    """
    matter = get_visible_matter(request, pk)
    # The same pattern the panel was drawn from, so the select's options on the
    # way in and the values accepted on the way back are one decision. A Matter
    # whose `Õigusakt` chooses no procedure has no field at all, and a crafted
    # `process_phase` on one reaches a form that never cleaned it
    # (`app.matters.forms.attach_phase_choices`).
    form = MatterProgressForm(
        request.POST, request.FILES, phases=legal_process.phase_context(matter=matter)
    )
    if not form.is_valid():
        return _workspace_refusal(request, matter, key="progress_form", form=form)
    try:
        workspace.add_procedural_development(
            matter=matter,
            author=request.user,
            title=form.cleaned_data["title"],
            # Always a day or nothing, and always `EXACT`: this panel has no
            # `Täpsus` control to read. An emptied box is «kuupäev teadmata»
            # rather than a refusal, which is the one thing the four-way
            # precision group bought that a lawyer writing up this morning's
            # events ever needed (docs/adr/0097 §6.1).
            occurred_on=form.cleaned_data.get("occurred_on_value"),
            occurred_on_precision=form.cleaned_data["occurred_on_precision"],
            # `Juristi märkus` is not asked here, so nothing is passed and the
            # column stores "". Historical rows keep theirs and
            # `ProceduralDevelopmentEditForm` still offers the box on a record
            # that has one (docs/adr/0097 §6.2).
            note="",
            # `Etapp` — which part of the procedure this step belongs to, as the
            # person left it in the select. Pre-selected from the file's own
            # `Hetkeseis` and visible beside the date box, so a step being filed
            # from 2019 is not silently taking today's phase; empty is an ordinary
            # answer and the row reads in the chronology exactly the same way.
            # What the value places is a node on `Menetluse kulg`
            # (`app.matters.legal_process.recorded_phases`, docs/adr/0105 §1).
            process_phase=form.cleaned_data.get("process_phase") or "",
            stage=form.cleaned_data.get("stage"),
            next_text=form.cleaned_data.get("next_text") or "",
            next_date=form.cleaned_data.get("next_date"),
            uploads=form.cleaned_data["attachments"],
        )
    except (DomainError, UploadRejected) as error:
        return _workspace_refusal(request, matter, key="progress_form", form=form, error=str(error))
    return _render_overview(request, matter)


@login_required
@business_write_required
@require_http_methods(["POST"])
def add_engagement_compact(request: HttpRequest, pk: Any) -> HttpResponse:
    """`+ Kaasamine` — one consultation, what it asked for, and what came back.

    **Both dates come off the form, and neither is invented here.** This view
    used to pass `timezone.localdate()` for `occurred_on` no matter what,
    because the panel had no date box — so a consultation from March, written
    down in September, was stored as a September consultation. The panel asks
    both dates now, each pre-filled with a plausible answer and each clearable,
    and what the person left in a box is what is stored. An emptied
    `Kaasamise kuupäev` stores nothing; an emptied `Tagasisidet ootame kuni`
    opens no wait (docs/adr/0086 §2).
    """
    matter = get_visible_matter(request, pk)
    form = CompactEngagementForm(request.POST, request.FILES)
    if not form.is_valid():
        return _workspace_refusal(request, matter, key="add_engagement_form", form=form)
    try:
        workspace.add_matter_engagement(
            matter=matter,
            author=request.user,
            audience=form.cleaned_data["audience"],
            response_count=form.cleaned_data.get("response_count"),
            smaily_url=form.cleaned_data.get("smaily_url") or "",
            alchemer_url=form.cleaned_data.get("alchemer_url") or "",
            occurred_on=form.cleaned_data.get("occurred_on_value"),
            occurred_on_precision=form.cleaned_data["occurred_on_precision"],
            # **No `feedback_deadline`.** This panel stopped asking, and the
            # service keeps the parameter for the importer, the shell and the
            # explicit `Ootan tagasisidet` act — none of which is this form
            # (docs/adr/0091 §2).
            feedback_received=form.cleaned_data.get("feedback_received") or "",
            uploads=form.cleaned_data["attachments"],
        )
    except (DomainError, UploadRejected) as error:
        return _workspace_refusal(
            request, matter, key="add_engagement_form", form=form, error=str(error)
        )
    return _render_overview(request, matter)


@login_required
@business_write_required
@require_http_methods(["POST"])
def add_important_date(request: HttpRequest, pk: Any) -> HttpResponse:
    """`+ Oluline tähtaeg` — a milestone somebody announced, and its letter."""
    matter = get_visible_matter(request, pk)
    form = CompactImportantDateForm(request.POST, request.FILES)
    if not form.is_valid():
        return _workspace_refusal(request, matter, key="important_date_form", form=form)
    try:
        workspace.add_matter_important_date(
            matter=matter,
            author=request.user,
            uploads=form.cleaned_data["attachments"],
            **form.cleaned_data["important_date_kwargs"],
        )
    except (DomainError, UploadRejected) as error:
        return _workspace_refusal(
            request, matter, key="important_date_form", form=form, error=str(error)
        )
    return _render_overview(request, matter)


@login_required
@business_write_required
@require_http_methods(["POST"])
def add_effective_date(request: HttpRequest, pk: Any) -> HttpResponse:
    """`+ Jõustumine` — what commences, the day it does, and the act itself."""
    matter = get_visible_matter(request, pk)
    form = CompactEffectiveDateForm(request.POST, request.FILES)
    if not form.is_valid():
        return _workspace_refusal(request, matter, key="effective_date_form", form=form)
    try:
        workspace.add_matter_effective_date(
            matter=matter,
            author=request.user,
            uploads=form.cleaned_data["attachments"],
            **form.cleaned_data["effective_date_kwargs"],
        )
    except (DomainError, UploadRejected) as error:
        return _workspace_refusal(
            request, matter, key="effective_date_form", form=form, error=str(error)
        )
    return _render_overview(request, matter)


@login_required
@business_write_required
@require_http_methods(["POST"])
def add_work_victory(request: HttpRequest, pk: Any) -> HttpResponse:
    """`+ Töövõit` — what changed, with the evidence that it did.

    Closes nothing and completes nothing. A win is its own canonical fact.
    """
    matter = get_visible_matter(request, pk)
    form = CompactWorkVictoryForm(request.POST, request.FILES)
    if not form.is_valid():
        return _workspace_refusal(request, matter, key="work_victory_form", form=form)
    try:
        workspace.add_matter_work_victory(
            matter=matter,
            author=request.user,
            uploads=form.cleaned_data["attachments"],
            **form.cleaned_data["work_victory_kwargs"],
        )
    except (DomainError, UploadRejected) as error:
        return _workspace_refusal(
            request, matter, key="work_victory_form", form=form, error=str(error)
        )
    return _render_overview(request, matter)


# ---------------------------------------------------------------------------
# `Väline seisukoht`
# ---------------------------------------------------------------------------


def _external_position_for_correction(
    request: HttpRequest, matter: Matter, position_id: Any
) -> MatterExternalPosition:
    """The position this request may correct, or a 404.

    Scoped through the child's own `visible_to` and not fetched by id off the
    Matter: a `Väline seisukoht` may carry a stricter visibility override than
    its parent, and reading it any other way would bypass that. A restricted
    position inside a Matter somebody may see is therefore indistinguishable
    here from one that does not exist, which is the contract the rest of the
    product keeps (AUTH-003, docs/adr/0038).
    """
    return get_object_or_404(
        MatterExternalPosition.objects.visible_to(request.user)
        .filter(matter=matter)
        # Not `engagement`: the round a position answered has its own
        # visibility and removal, and is read through them where it is shown
        # (`_answered_engagement`, ENG-047).
        .select_related("organisation", "matter"),
        pk=position_id,
    )


def _answered_engagement(request: HttpRequest, position: MatterExternalPosition) -> Any:
    """The round this position answered, as this reader may see it, or ``None``.

    Through `MatterEngagement.visible_to`, which leaves out a round restricted
    below the Matter and one taken off the file — never through the foreign
    key, which does neither (ENG-047).
    """
    if position.engagement_id is None:
        return None
    return (
        MatterEngagement.objects.visible_to(request.user).filter(pk=position.engagement_id).first()
    )


def _external_position_edit_form(
    request: HttpRequest, position: MatterExternalPosition, data: Any = None
) -> ExternalPositionEditForm:
    """The correction form for one position, opened on what the record says.

    `auto_id` is derived from the record's primary key because a chronology may
    show several of these and two controls sharing an id is enough to make a
    `<label for>` reach the wrong box — the same reason
    `_website_overview_link_form` derives its own.

    An approximate date reopens on its own chip with the day box left empty: the
    stored anchor is a place in a sort, not a day to hand back to somebody to
    re-save (docs/adr/0079 §2, `external_position_period_initial`).

    **Every field this form renders is opened on what the record holds.** The
    correction writes back the whole record — `correct_external_position` is
    handed each value, not a diff — so a box this function forgets is a box that
    comes up empty and is *saved* empty by somebody who only came to fix a typo
    in the summary. `Liige` was the one that mattered: the owner asked for the
    mark to be correctable through the supported edit flow, and an editor that
    silently clears it is the opposite of correctable (OWNER-01, QA-014).
    `Allikas` and `Juristi märkus` are the same field on the same form and are
    opened here for the same reason — both were reachable by this route and
    neither survived a no-op save.
    """
    auto_id = f"id_valine_seisukoht_{position.pk}_%s"
    if data is not None:
        return ExternalPositionEditForm(data, auto_id=auto_id, record=position, viewer=request.user)
    return ExternalPositionEditForm(
        initial={
            "organisation": position.organisation_id,
            "url": position.url,
            "summary": position.summary,
            "lawyer_note": position.lawyer_note,
            # Both are dropped from the form on a discovered position, and
            # `initial` for a field that is not there is simply ignored — so
            # this says «open on the record» once rather than asking the same
            # question the form already answered in `__init__`.
            "source_label": position.source_label,
            "source_is_member": position.source_is_member,
            "engagement": position.engagement_id,
            **external_position_period_initial(position),
            "revision": position.revision_token,
        },
        auto_id=auto_id,
        record=position,
        viewer=request.user,
    )


def _external_position_row(
    request: HttpRequest,
    matter: Matter,
    position: MatterExternalPosition,
    *,
    form: ExternalPositionEditForm | None = None,
    evidence_form: ExternalPositionEvidenceForm | None = None,
    error: str = "",
    conflict: MatterExternalPosition | None = None,
    status: int = 200,
) -> HttpResponse:
    """The corrected position back in place, the form that failed, or the picker.

    One renderer for both, because they swap the same element: `Muuda` replaces
    the milestone's text region with the form, and every answer replaces it
    again — with the corrected record, or with the form still open and what the
    person typed still in it. The `<article>` around it, its 12 px dot, its
    spine and its attached files are never in the response, so a correction
    cannot move the row or turn into a second line in the chronology
    (`_engagement_row`, which this deliberately mirrors).

    The milestone is rebuilt through `external_position_milestone`, the same
    function the chronology itself renders from, so a corrected row cannot come
    back worded differently from the way it will read on the next page load.

    ``evidence_form`` is the third thing this region can hold: the `+ Lisa fail`
    picker, which a position did not have at all until QA-021 — files could only
    arrive with the capture, so a position paper that turned up a week later had
    nowhere on the file to go. It is a separate argument rather than a mode flag
    for the reason `_development_row` gives: the two forms are two acts, and a
    single «the row is in edit mode» is the shape in which somebody later gives
    the correction an upload field. Only one is ever passed. A successful
    capture does not come back through here — the file it added is rendered by
    the chronology around this element, so that answer is the whole column.
    """
    return render(
        request,
        "matters/partials/external_position_row.html",
        {
            "matter": matter,
            "position": position,
            "milestone": external_position_milestone(
                position, engagement=_answered_engagement(request, position)
            ),
            "external_position_edit_form": form,
            "external_position_evidence_form": evidence_form,
            "external_position_edit_error": error,
            "external_position_conflict_milestone": (
                external_position_milestone(
                    conflict, engagement=_answered_engagement(request, conflict)
                )
                if conflict is not None
                else None
            ),
            "external_position_read_query": ENGAGEMENT_READ_QUERY,
            # One picker per record, because a chronology may hold several of
            # these and the shared organisation control derives every id it
            # writes — the search box, the results list, the status region —
            # from this one string. Two of them sharing it would put duplicate
            # ids in the document and make a `<label for>` reach the wrong
            # control (docs/adr/0073, `organisation_picker.html`).
            "external_position_picker_id": f"valine-seisukoht-{position.pk}",
        },
        status=status,
    )


def _record_external_position(
    request: HttpRequest, pk: Any, *, form_class: Any, key: str
) -> HttpResponse:
    """`+ Meile saadetud tagasiside` and `+ Teiste arvamus`, through one function.

    Two chips, two forms and one operation, which is docs/adr/0091 §3's claim
    stated in code: the panels differ in which questions they ask and in which
    provenance they write, and nothing else about the act differs. A second view
    would have been a second place for the organisation resolution, the refusal
    handling and the closed-Matter boundary to drift.

    **The provenance comes off the form class, never off the request.** Neither
    form declares a `provenance` field, so there is nothing for a browser to post
    and nothing a crafted POST can move: `+ Teiste arvamus` cannot be made to file
    received feedback by adding a parameter. It is the same reasoning
    `NextActionForm` gives for having no `kind` — a classification the page
    decides is not a value the endpoint accepts (ADR 0052 §1).

    The institution is answered either by the picker's radio group or by the
    name somebody typed into it, and which of the two wins is
    `resolve_addressee`'s rule, shared rather than restated: a typed name is a
    deliberate act and beats a chip that was merely left selected. Resolution
    happens inside the save's own transaction, so a refusal further down leaves
    no institution behind (docs/adr/0073, docs/adr/0084 §4).

    The institution is answered either by the picker's radio group or by the name
    somebody typed into it, and which of the two wins is `resolve_addressee`'s
    rule, shared rather than restated: a typed name is a deliberate act and beats
    a chip that was merely left selected. Resolution happens inside the save's own
    transaction, so a refusal further down leaves no institution behind
    (docs/adr/0073, docs/adr/0084 §4).

    **Received feedback may name no institution at all**, and that is the one
    place the two panels diverge here: `resolve_addressee` is asked only when
    something was chosen or typed, because calling it with two empty answers
    would refuse an aggregate survey result for having no author when `Allikas`
    is exactly the author it has (docs/adr/0091 §3.3).

    A refusal comes back through `_workspace_refusal` with the form still bound,
    so the link somebody pasted and the explanation they wrote are still in
    their boxes — losing them would cost the person the record they opened the
    panel to make. The closed-Matter refusal is the service's, answered under
    the Matter's row lock, because a POST may arrive from a tab that was open
    before somebody else shut the file (R2-02).
    """
    matter = get_visible_matter(request, pk)
    form = form_class(request.POST, request.FILES, matter=matter, viewer=request.user)
    if not form.is_valid():
        return _workspace_refusal(request, matter, key=key, form=form)
    chosen = form.cleaned_data.get("organisation")
    typed = (form.cleaned_data.get("organisation_name") or "").strip()
    try:
        workspace.add_matter_external_position(
            matter=matter,
            author=request.user,
            # `None` where neither half of the picker was answered. The form has
            # already refused that on `+ Teiste arvamus` — a published opinion
            # with no author is an anonymous claim — so reaching here with
            # nothing is received feedback that names nobody, which since
            # docs/adr/0101 is a record rather than a gap: a lawyer writing down
            # what a member said on the telephone has neither a catalogue row
            # nor a collection to name (OWNER-01).
            organisation=(
                resolve_addressee(chosen=chosen, typed_name=typed)
                if (chosen is not None or typed)
                else None
            ),
            provenance=form_class.provenance,
            # **The three answers neither panel asks any more**, and each of
            # them is a `.get` on a field the form does not declare — so this is
            # not a view choosing to ignore a value, it is a value that has
            # nowhere to arrive. A POST carrying `source_label`, `lawyer_note`
            # or `engagement` to either endpoint reaches a form that never
            # cleaned it, and what reaches the service is the empty answer
            # below. `Muuda` still asks all three, and every stored row keeps
            # what it has (docs/adr/0095 §5).
            source_label=form.cleaned_data.get("source_label") or "",
            url=form.cleaned_data.get("url") or "",
            # The resolved anchor and its precision. On these two panels that is
            # the day box itself at `EXACT`: the period fields are not on the
            # form, so a crafted `..._precision=QUARTER` is read by nothing
            # (`ExternalPositionFieldsMixin.offers_precision`).
            stated_on=form.cleaned_data.get("stated_on_value"),
            stated_on_precision=form.cleaned_data["stated_on_precision"],
            summary=form.cleaned_data.get("summary") or "",
            lawyer_note=form.cleaned_data.get("lawyer_note") or "",
            # `Liige`, and only from the panel that asks it. `+ Teiste arvamus`
            # declares no such field, so this is `None` there — and the service
            # refuses a `True` on that provenance in any case, because the box
            # being absent and the value being refused are two separate defences
            # (docs/adr/0095 §4).
            source_is_member=bool(form.cleaned_data.get("source_is_member")),
            engagement=form.cleaned_data.get("engagement"),
            uploads=form.cleaned_data["attachments"],
        )
    except (DomainError, UploadRejected) as error:
        return _workspace_refusal(request, matter, key=key, form=form, error=str(error))
    return _render_overview(request, matter)


def _procedural_link_for(
    request: HttpRequest, matter: Matter, link_id: Any
) -> MatterProceduralLink:
    """The procedural link this request may act on, or a 404.

    Scoped through the child's own `visible_to` rather than fetched by id off
    the Matter, exactly as `_website_overview_for` and `_entry_for_correction`
    do it: the record carries its own `visibility_override`, and reading it any
    other way would bypass that. A restricted link inside a Matter somebody may
    see is indistinguishable here from one that does not exist (AUTH-003).
    """
    return get_object_or_404(
        MatterProceduralLink.objects.visible_to(request.user).filter(matter=matter),
        pk=link_id,
    )


def _procedural_link_rows(
    matter: Matter,
    user: Any,
    *,
    bound_for: Any = None,
    form: Any = None,
) -> list[tuple[MatterProceduralLink, Any]]:
    """Each recorded link with the form that corrects it.

    Paired here rather than in the template for `_planned_website_overview_rows`'
    reason: exactly one row may render a *bound* form — the one a refusal came
    back for — and deciding that in the template would mean comparing ids in
    three places. Every other row gets its own unbound form filled from the
    record, so a refused correction on one link cannot put somebody's typed
    address into the box beside another.
    """
    rows: list[tuple[MatterProceduralLink, Any]] = []
    for record in selectors.procedural_links(matter, user):
        if form is not None and bound_for is not None and str(record.pk) == str(bound_for):
            rows.append((record, form))
        else:
            rows.append((record, ProceduralLinkEditForm(link=record)))
    return rows


def _procedural_link_refusal(
    request: HttpRequest,
    matter: Matter,
    *,
    link: MatterProceduralLink,
    form: Any,
    error: str = "",
    status: int = 400,
) -> HttpResponse:
    """Re-render the workspace with one correction disclosure reopened on its row.

    Deliberately **not** `_workspace_refusal`, for the reason
    `_website_overview_refusal` is not: that helper reopens a *launcher* panel,
    and a correction has none — it lives on a row in the rail card, rendered
    from the Matter's own records rather than from a chip somebody clicked.

    The bound form and the refused row's id travel together, so the card reopens
    exactly the disclosure the words were typed into and puts them back in it.
    The stale token deliberately stays in the bound form: advancing it would be
    this view deciding that the next submit may overwrite what the other writer
    saved (`edit_entry_view`, QA-09).
    """
    context = _overview_context(request, matter)
    context.update(_header_context(request, matter))
    context["procedural_link_open"] = str(link.pk)
    context["procedural_links"] = _procedural_link_rows(
        matter, request.user, bound_for=link.pk, form=form
    )
    context["procedural_link_error"] = error
    body = render_to_string("matters/partials/overview.html", context, request=request)
    return HttpResponse(body, status=status)


@login_required
@business_write_required
@require_http_methods(["POST"])
def correct_procedural_link_view(request: HttpRequest, pk: Any, link_id: Any) -> HttpResponse:
    """`Paranda` — the kind, the name or the address on a recorded link was wrong.

    **Allowed on a closed Matter**, like an `Ülevaade / uudis` link correction
    and unlike a `Väline seisukoht`'s. Closure means no new business content; it
    has never meant that an address recorded wrongly must stay wrong, and a
    procedural link carries no date, no organisation and no chronology row for a
    correction to move — it is a pointer, and a pointer to the wrong page is
    simply wrong (docs/adr/0075 §12, docs/adr/0081 §5, docs/adr/0089 §6).

    **There is no route that deletes one**, on an open Matter or a closed one.

    A conflict answers 409 with the form still open on this person's values; the
    card beside it has just been re-read, so what the other writer saved is
    already on the page.
    """
    matter = get_visible_matter(request, pk)
    link = _procedural_link_for(request, matter, link_id)
    form = ProceduralLinkEditForm(request.POST, link=link)
    if not form.is_valid():
        return _procedural_link_refusal(request, matter, link=link, form=form)
    try:
        workspace.correct_matter_procedural_link(
            author=request.user,
            link=link,
            kind=form.cleaned_data.get("kind"),
            url=form.cleaned_data.get("url"),
            label=form.cleaned_data.get("label") or "",
            expected_revision=form.cleaned_data.get("revision") or "",
        )
    except ProceduralLinkConflict as conflict:
        return _procedural_link_refusal(
            request, matter, link=link, form=form, error=str(conflict), status=409
        )
    except DomainError as error:
        return _procedural_link_refusal(request, matter, link=link, form=form, error=str(error))
    return _render_overview(request, matter)


@login_required
@business_write_required
@require_http_methods(["POST"])
def add_received_feedback(request: HttpRequest, pk: Any) -> HttpResponse:
    """`+ Meile saadetud tagasiside` — somebody gave this to Koda."""
    return _record_external_position(
        request, pk, form_class=ReceivedFeedbackForm, key="received_feedback_form"
    )


@login_required
@business_write_required
@require_http_methods(["POST"])
def add_external_position(request: HttpRequest, pk: Any) -> HttpResponse:
    """`+ Teiste arvamus` — Koda recorded somebody else's position from elsewhere.

    The route keeps its name. It is what docs/adr/0084's panel posted to, the
    browser lane and the visual baselines reach it by that name, and renaming a
    working endpoint because a chip's label changed would be churn with a
    migration attached (docs/adr/0091 §3.5).
    """
    return _record_external_position(
        request, pk, form_class=OtherOpinionForm, key="external_position_form"
    )


@login_required
@business_write_required
@require_http_methods(["GET", "POST"])
def update_external_position_view(request: HttpRequest, pk: Any, position_id: Any) -> HttpResponse:
    """`Muuda` on a `Väline seisukoht` — a wrong value on a position really held.

    A position this file should never have carried is `Kustuta`, which is a
    different act with its own route and its own audit event
    (`remove_record_view`).

    GET opens the form in the chronology row; POST saves it. One route, because
    they are one interaction and the second is only reachable from the first —
    the shape `update_engagement_view` and `edit_entry_view` already use.

    **Refused on a closed Matter**, like a `Kaasamine` correction and unlike an
    entry's. Correcting a position moves the organisation, the date, the source
    and the relation of a structured record the chronology reads, so it is
    normal interactive business work and a finished file refuses it; reopening
    is the way out and leaves somebody's name on both decisions. The rule is
    enforced under the Matter's row lock inside `correct_external_position`,
    never by whether this page rendered a button (docs/adr/0084 §8).

    **There is no route that deletes one**, on an open Matter or a closed one.
    A mistaken position is corrected, because what the file recorded and who
    recorded it is part of the file — the rule `MatterEngagement` has kept since
    it was written.
    """
    matter = get_visible_matter(request, pk)
    position = _external_position_for_correction(request, matter, position_id)

    if request.method == "GET":
        # `Tühista`. Leaving edit mode is a re-read rather than a client-side
        # hide: the boxes may be holding values that were never saved, and the
        # only honest way out of them is to fetch what the record actually says.
        if request.GET.get(ENGAGEMENT_READ_PARAM) == ENGAGEMENT_READ_VALUE:
            return _external_position_row(request, matter, position)
        return _external_position_row(
            request, matter, position, form=_external_position_edit_form(request, position)
        )

    form = _external_position_edit_form(request, position, request.POST)
    if not form.is_valid():
        return _external_position_row(request, matter, position, form=form, status=400)

    try:
        corrected = correct_external_position(
            position=position,
            organisation=resolve_addressee(
                chosen=form.cleaned_data.get("organisation"),
                typed_name=form.cleaned_data.get("organisation_name") or "",
            ),
            url=form.cleaned_data.get("url") or "",
            stated_on=form.cleaned_data.get("stated_on_value"),
            stated_on_precision=form.cleaned_data["stated_on_precision"],
            summary=form.cleaned_data.get("summary") or "",
            lawyer_note=form.cleaned_data.get("lawyer_note") or "",
            # `Allikas` only where the record may have one — the box is not on
            # the form otherwise, and passing a value the record's provenance
            # forbids is what `_external_position_authorship` refuses.
            source_label=form.cleaned_data.get("source_label") or "",
            # `Liige`, on the same terms. `None` where the form does not render
            # the box, so a correction through that shape cannot clear a mark;
            # `bool(...)` where it does, because an unticked box is a decision
            # and not an absence (QA-014).
            source_is_member=(
                bool(form.cleaned_data.get("source_is_member"))
                if "source_is_member" in form.fields
                else None
            ),
            # **Not asked and not moved.** `None` is the sentinel for «this form
            # did not render the control», which is what keeps a `LEGACY` row's
            # unspecified provenance through a correction and what stops one press
            # turning received feedback into a discovered opinion
            # (docs/adr/0091 §3.4, §3.5).
            provenance=None,
            # `Seotud kaasamine` as the person left it. «Jääb samaks» is what
            # the form offers — and pre-selects — when the stored round is one
            # this reader may not see, so a correction of the date or the text
            # leaves that relation exactly as it was instead of clearing it
            # because the round was not in the list (ENG-047).
            engagement=(
                position.engagement
                if form.cleaned_data.get("engagement") == ENGAGEMENT_UNCHANGED
                else form.cleaned_data.get("engagement")
            ),
            actor=request.user,
            expected_revision=form.cleaned_data.get("revision") or "",
        )
    except ExternalPositionConflict as conflict:
        # 409, and nothing was written — not the metadata and not the source.
        # The form stays open holding this person's values and the version that
        # beat them arrives beside it to read; neither is chosen for them. The
        # hidden token is **not** advanced: adopting the newer one here would be
        # this view deciding that the next submit may overwrite what the other
        # writer saved (QA-09).
        return _external_position_row(
            request,
            matter,
            position,
            form=form,
            error=str(conflict),
            conflict=conflict.current,
            status=409,
        )
    except DomainError as error:
        # A closed Matter lands here, and so does any refusal the service makes.
        # The sentence goes into the form that is still open rather than into a
        # panel this row does not have.
        return _external_position_row(
            request, matter, position, form=form, error=str(error), status=400
        )

    return _external_position_row(request, matter, corrected)


def _koda_opinion_recipients(form: Any) -> list[Any]:
    """The addressees this save names: the ones ticked, then the one typed.

    Two intentions, one control. The picker's search box posts nothing at all;
    `+` moves what was typed into `recipient_name`, and only that says «this is a
    body you do not have». Which of the two a save means is therefore decidable
    here rather than guessable, and it is `resolve_addressee` — shared with
    `Saatja`, `Adressaat` and both feedback panels — that decides what the name
    means: reuse an exact or alias match, create only a genuinely new body,
    refuse a spelling that names two (docs/adr/0073).

    **De-duplicated**, because a name that resolves to a body the person also
    ticked is one addressee rather than two. `set_recipients` would collapse it
    in any case; doing it here keeps what this function returns honest about how
    many bodies the letter went to.
    """
    chosen = list(form.cleaned_data.get("recipients") or [])
    typed = (form.cleaned_data.get("recipient_name") or "").strip()
    if not typed:
        return chosen
    named = resolve_addressee(chosen=None, typed_name=typed)
    if named is not None and not any(row.pk == named.pk for row in chosen):
        chosen.append(named)
    return chosen


@login_required
@business_write_required
@require_http_methods(["POST"])
def add_koda_opinion(request: HttpRequest, pk: Any) -> HttpResponse:
    """`+ Koja arvamus` — the Chamber's opinion went out, recorded where the work is.

    **A second door onto the one canonical act**, and deliberately not a second
    implementation of it. `workspace.add_matter_koda_opinion` composes the same
    `register_sent_opinion_on_open_matter` the `Dokumendid` panel posts to, so the
    kind validation, the evidence checks, the two row locks, the send event and the
    outbound statistics are all exactly where they were (docs/adr/0061 §17,
    docs/adr/0091 §6).

    `UploadRejected` and `DomainError` both come back through
    `_workspace_refusal` with the form still bound. A file cannot be put back into
    a file input by a browser, so the person reloads that one control — but the
    date, the title and the addressees they chose are still on the page, which is
    the difference between correcting a refusal and starting again (QA-02).
    """
    matter = get_visible_matter(request, pk)
    form = KodaOpinionForm(request.POST, request.FILES, matter=matter, viewer=request.user)
    if not form.is_valid():
        return _workspace_refusal(request, matter, key="koda_opinion_form", form=form)
    try:
        workspace.add_matter_koda_opinion(
            matter=matter,
            author=request.user,
            upload=form.cleaned_data["upload"],
            # The bodies chosen, plus at most one somebody named through the
            # picker's `+`. `resolve_addressee` is asked only when there is a
            # name — the same rule the feedback panels use, and it runs inside
            # the save's own transaction, so a refused upload leaves no
            # institution behind (docs/adr/0073, docs/adr/0095 §1).
            recipients=_koda_opinion_recipients(form),
            sent_on=form.cleaned_data["sent_on"],
            # **No title from this panel.** `Pealkiri` is not asked any more, so
            # the blank that `add_matter_koda_opinion` has always answered with
            # the uploaded file's own name is what it gets — a truthful identity
            # somebody chose, rather than a headline cut out of the summary
            # (docs/adr/0095 §2).
            summary=form.cleaned_data.get("summary") or "",
        )
    except (DomainError, UploadRejected) as error:
        return _workspace_refusal(
            request, matter, key="koda_opinion_form", form=form, error=str(error)
        )
    return _render_overview(request, matter)


# ---------------------------------------------------------------------------
# Correcting a recorded `Marge`
# ---------------------------------------------------------------------------


def _development_for_correction(
    request: HttpRequest, matter: Matter, development_id: Any
) -> MatterProceduralDevelopment:
    """The development this request may correct, or a 404.

    Scoped through the child's own `visible_to` and not fetched by id off the
    Matter: a `Menetluse areng` may carry a stricter visibility override than its
    parent, and reading it any other way would bypass that. A restricted
    development inside a Matter somebody may see is therefore indistinguishable
    here from one that does not exist — the same answer, in the same shape, to a
    GET of the form and to a POST guessing the UUID, so that neither can be used
    to learn that the row is there (AUTH-003, docs/adr/0038,
    `_external_position_for_correction`).
    """
    return get_object_or_404(
        MatterProceduralDevelopment.objects.visible_to(request.user)
        .filter(matter=matter)
        .select_related("matter"),
        pk=development_id,
    )


def _development_edit_form(
    request: HttpRequest, development: MatterProceduralDevelopment, data: Any = None
) -> ProceduralDevelopmentEditForm:
    """The correction form for one development, opened on what the record says.

    `auto_id` is derived from the record's primary key because a chronology may
    show several of these and two controls sharing an id is enough to make a
    `<label for>` reach the wrong box — the same reason
    `_external_position_edit_form` derives its own.

    **The date reopens on the record and never on today.** An undated development
    opens with an empty box, not with the day somebody is reading the page; an
    approximate one reopens on its own chip with the day box left empty, because
    the stored anchor is a place in a sort and not a day to hand back to somebody
    to re-save (docs/adr/0079 §2, `development_period_initial`).
    """
    auto_id = f"id_menetluse_areng_{development.pk}_%s"
    # The Matter's own pattern, so `Etapp` offers this file's procedure and not a
    # generic list — and so the record's stored phase survives a reclassification
    # that took it out of the pattern (`attach_phase_choices`).
    phases = legal_process.phase_context(matter=development.matter)
    if data is not None:
        return ProceduralDevelopmentEditForm(
            data, auto_id=auto_id, record=development, phases=phases
        )
    return ProceduralDevelopmentEditForm(
        initial={
            "title": development.title,
            "note": development.note,
            # The phase the record actually holds, never the file's current one:
            # this is a correction form, and reopening a step filed under `VTK` on
            # today's `Riigikogus` would be one `Salvesta` away from re-filing it
            # (the rule the day box above keeps, for the same reason).
            "process_phase": development.process_phase,
            # Explicit, and not merely absent. `development_period_initial`
            # returns `{}` for a row with no date at all, so a bare `**` would
            # leave the day box to whatever default it could find — which is the
            # mistake `external_position_period_initial` carries its own `None`
            # to prevent. This form declares no `initial` on the field either, so
            # this says the same thing a second time rather than trusting two
            # declarations to stay apart (docs/adr/0078 §2).
            "occurred_on": None,
            **development_period_initial(development),
            "revision": development.revision_token,
        },
        auto_id=auto_id,
        record=development,
        phases=phases,
    )


def _development_row(
    request: HttpRequest,
    matter: Matter,
    development: MatterProceduralDevelopment,
    *,
    form: ProceduralDevelopmentEditForm | None = None,
    evidence_form: DevelopmentEvidenceForm | None = None,
    error: str = "",
    conflict: MatterProceduralDevelopment | None = None,
    status: int = 200,
) -> HttpResponse:
    """The development back in place, the form that could not save, or the picker.

    One renderer for both, because they swap the same element: `Muuda` replaces
    the milestone's text region with the form, and every answer replaces it
    again — with the corrected record, or with the form still open and what the
    person typed still in it. The `<article>` around it, its 12 px dot, its spine
    and its attached files are never in the response, so a correction cannot move
    the row and cannot turn into a second line in the chronology
    (`_external_position_row`, which this deliberately mirrors).

    The milestone is rebuilt through `development_milestone`, the same function
    the chronology itself renders from, so a corrected row cannot come back
    worded differently from the way it will read on the next page load — and so
    that `Juristi märkus` keeps its own label and its own attribution after a
    correction exactly as it has after an initial save (docs/adr/0091 §4).

    ``evidence_form`` is the third thing this region can hold: the `+ Lisa fail`
    picker. It is a separate argument rather than a mode flag because the two
    forms are two acts — one changes what the row says and one adds a paper to
    what it says — and a single «the row is in edit mode» would be the shape in
    which somebody later gave the correction an upload field. Only one of them is
    ever passed; they replace the same region, so a row cannot be correcting and
    capturing at once. A successful capture does **not** come back through here:
    the file it added is rendered by the chronology around this element, so that
    answer is the whole column (`add_development_evidence_view`).
    """
    return render(
        request,
        "matters/partials/development_row.html",
        {
            "matter": matter,
            "development": development,
            "milestone": development_milestone(development),
            "development_edit_form": form,
            "development_evidence_form": evidence_form,
            "development_edit_error": error,
            "development_conflict_milestone": (
                development_milestone(conflict) if conflict is not None else None
            ),
            "development_read_query": ENGAGEMENT_READ_QUERY,
        },
        status=status,
    )


def _timeline_steps_form(
    request: HttpRequest, matter: Matter, data: Any = None
) -> TimelineStepsForm:
    """The `Muuda kulgu` panel, opened on what this file actually says.

    The pattern, the stored rows, which phase the file is standing on and which
    phases carry a canonical dated fact — read once and handed to the form, so
    the panel offers this file's own procedure and refuses the two removals
    that would leave the rail saying something untrue.

    **A date box on every phase now.** The panel used to withhold one wherever a
    `Märge` already dated the phase, because the rail borrowed that date. It no
    longer does — the rail is the roadmap and `Teema käik` is the history — so
    withholding the box would leave a phase with no date and no way to give it
    one (QA-006, QA-007).
    """
    from app.matters.models import MatterTimelineStep

    phases = legal_process.phase_context(matter=matter)
    rows = {
        row.phase_key: row
        for row in MatterTimelineStep.objects.filter(matter=matter).visible_to(request.user)
    }
    return TimelineStepsForm(
        data,
        phases=phases,
        rows=rows,
        current_phase=phases.current_phase,
        anchored=legal_process.anchored_phase_keys(matter=matter, user=request.user),
        revision=timeline_steps_revision_token(matter),
    )


@login_required
@business_write_required
@require_http_methods(["POST"])
def remove_record_view(request: HttpRequest, pk: Any, kind: str, record_id: Any) -> HttpResponse:
    """`Kustuta` on a user-created block of `Teema käik`.

    One route for all eight removable families, because the act is one act: two
    columns are set on the canonical record, one audit event is written in that
    family's own words, and the projection follows. What differs between them —
    the model, the event, the word the confirmation uses — is `removal.REMOVABLE`
    rather than eight copies of this function (OWNER-04, docs/adr/0102).

    **POST only, and the record is fetched through its own `visible_to`.** A
    reader never reaches here at all (`business_write_required`), and a writer
    who may open the Matter but not the child gets the same 404 a guessed
    identifier gets — the rule `_development_for_correction` states, for the
    reason it states it: a distinguishable refusal is how somebody learns that
    a restricted row is there.

    **No GET, and therefore no confirmation page.** The confirmation is a
    native disclosure in the row — it names what is going and offers `Loobu` —
    so there is nothing for a GET to render and no second address a stale tab
    can sit on (`templates/matters/partials/row_remove.html`).

    **The whole Teema view is re-rendered, header included.** Taking a row off
    the file can change the rail, the phase a later row is grouped under, the
    `Järgmiseks` beside it and — for an `Oluline tähtaeg` — the deadline the
    header states. Swapping only the row would leave four surfaces describing a
    record that is not there.
    """
    matter = get_visible_matter(request, pk)
    try:
        removable = kind_for(kind)
    except DomainError:
        raise Http404 from None
    # Through the child's own chokepoint, so a restricted row inside a Matter
    # this writer may see is indistinguishable from one that does not exist.
    record = get_object_or_404(
        removable.rows.visible_to(request.user).filter(matter=matter), pk=record_id
    )

    try:
        remove_matter_record(
            matter_id=matter.pk,
            kind_key=kind,
            record_id=record.pk,
            actor=request.user,
            expected_revision=request.POST.get("revision") or None,
        )
    except RecordRemovalConflict as conflict:
        return _removal_refusal(request, matter, str(conflict), status=409)
    except DomainError as error:
        # A closed Matter lands here, and so does any refusal the service makes.
        return _removal_refusal(request, matter, str(error), status=400)

    matter.refresh_from_db()
    return _render_overview(request, matter, header_out_of_band=True)


def _removal_refusal(
    request: HttpRequest, matter: Matter, error: str, *, status: int
) -> HttpResponse:
    """Say why the row is still there, on the page the row is on.

    The refusal reuses the workspace's own unowned-error line rather than
    inventing a banner for this one act: it is the slot that exists for exactly
    this — a server refusal no open panel owns — and the disclosure the button
    sat in has closed by the time the answer arrives.
    """
    context = _overview_context(request, matter)
    context.update(_header_context(request, matter))
    context["composer_error"] = error
    return render(request, "matters/partials/overview.html", context, status=status)


def _timeline_steps_refusal(
    request: HttpRequest,
    matter: Matter,
    form: Any,
    *,
    status: int,
    error: str = "",
) -> HttpResponse:
    """Put a refused `Muuda kulgu` back where the person is looking.

    **The success path and the refusal path have different targets, and that is
    not a detail.** A saved panel re-renders the whole Teema view, because
    hiding a phase changes the rail and dating one changes what the rail says
    about every other — so the form declares `hx-target="#teema-vaade"`. A
    *refusal* re-renders the panel, and swapping a panel into that target
    replaces the entire page with a bare form: the rail, the chronology and the
    launcher all gone, and the only way back a manual reload.

    `HX-Retarget` says so per response rather than per form, which is the only
    place the distinction exists. Without it the 400 path had been quietly
    doing the same thing since it was written; the conflict path would have
    joined it.
    """
    response = render(
        request,
        "matters/partials/timeline_steps_form.html",
        {"matter": matter, "timeline_steps_form": form, "timeline_steps_error": error},
        status=status,
    )
    response["HX-Retarget"] = "#menetluse-kulg-muuda"
    response["HX-Reswap"] = "innerHTML"
    return response


@login_required
@business_write_required
@require_http_methods(["GET", "POST"])
def timeline_steps_view(request: HttpRequest, pk: Any) -> HttpResponse:
    """`Muuda kulgu` — open the panel, or save it.

    One small panel that swaps itself in place, like every other correction on
    this page. A GET opens it; a POST saves and re-renders the whole Teema view,
    because hiding a phase changes the rail, and on a file whose phases carry
    dates it changes what the rail says about every one of them.
    """
    matter = get_visible_matter(request, pk)
    if request.method == "GET":
        return render(
            request,
            "matters/partials/timeline_steps_form.html",
            {"matter": matter, "timeline_steps_form": _timeline_steps_form(request, matter)},
        )
    form = _timeline_steps_form(request, matter, request.POST)
    if not form.is_valid():
        return _timeline_steps_refusal(request, matter, form, status=400)
    try:
        set_timeline_steps(
            matter=matter,
            steps=form.steps(),
            actor=request.user,
            # What the panel was opened on. Checked under the Matter's row lock
            # inside the service, so two panels open on the same rail cannot
            # both post the whole set and have the second one win silently
            # (QA-004, `timeline_steps_revision_token`).
            expected_revision=form.cleaned_data.get("revision") or None,
        )
    except TimelineStepsConflict as conflict:
        # Refused rather than overwritten, and the panel comes back holding
        # what this person typed: a conflict somebody cannot see their own side
        # of is a conflict they cannot resolve. 409 rather than 400 — the shape
        # `matter_edit` uses for the same situation — because nothing about
        # what they submitted was wrong.
        #
        # Caught before `DomainError`, which it subclasses, so neither the
        # status nor the place is lost to the generic handler below.
        retry = request.POST.copy()
        retry["revision"] = timeline_steps_revision_token(matter)
        form = _timeline_steps_form(request, matter, retry)
        form.is_valid()
        return _timeline_steps_refusal(request, matter, form, status=409, error=str(conflict))
    except DomainError as error:
        return _timeline_steps_refusal(request, matter, form, status=400, error=str(error))
    return _render_overview(request, matter)


@login_required
@business_write_required
@require_http_methods(["GET", "POST"])
def update_development_view(request: HttpRequest, pk: Any, development_id: Any) -> HttpResponse:
    """`Muuda` on a `Menetluse areng` — a wrong value on a step that happened.

    A step that never happened on this file is `Kustuta`, which is a different
    act with its own route and its own audit event (`remove_record_view`).

    GET opens the form in the chronology row; POST saves it. One route, because
    they are one interaction and the second is only reachable from the first —
    the shape `update_external_position_view`, `update_engagement_view` and
    `edit_entry_view` already use.

    **It corrects this record and nothing beside it.** A `+ Menetluse areng` save
    may have moved `Matter.stage` and set a `Järgmiseks` in the same atomic
    operation; correcting the sentence it recorded does not rewind either of
    them. Those are separate canonical facts with their own correction surfaces,
    and what ties the original three writes together is the `operation_id` they
    share — not a claim that this row owns them. Nor are the development's files
    touched: they are not re-posted here, so a correction can neither detach one
    nor replace an immutable `DocumentVersion` (docs/adr/0084 §8,
    docs/adr/0091 §5.4, docs/adr/0092 §6).

    **Refused on a closed Matter**, like a `Kaasamine` correction and unlike an
    entry's. Every field on this record is substantive — what happened, when, and
    what this office made of it — so correcting one is normal interactive
    business work and a finished file refuses it; reopening is the way out and
    leaves somebody's name on both decisions. The rule is enforced under the
    Matter's row lock inside `correct_procedural_development`, never by whether
    this page rendered a button (docs/adr/0076 §2, docs/adr/0084 §8).
    """
    matter = get_visible_matter(request, pk)
    development = _development_for_correction(request, matter, development_id)

    if request.method == "GET":
        # `Tühista`. Leaving edit mode is a re-read rather than a client-side
        # hide: the boxes may be holding values that were never saved, and the
        # only honest way out of them is to fetch what the record actually says.
        if request.GET.get(ENGAGEMENT_READ_PARAM) == ENGAGEMENT_READ_VALUE:
            return _development_row(request, matter, development)
        return _development_row(
            request, matter, development, form=_development_edit_form(request, development)
        )

    form = _development_edit_form(request, development, request.POST)
    if not form.is_valid():
        return _development_row(request, matter, development, form=form, status=400)

    try:
        corrected = correct_procedural_development(
            development=development,
            title=form.cleaned_data["title"],
            # The resolved anchor and its precision, not the day box: `Kuu`,
            # `Kvartal` and `Aasta` leave that box empty on purpose, and an
            # emptied one is «kuupäev teadmata» rather than a refusal.
            occurred_on=form.cleaned_data.get("occurred_on_value"),
            occurred_on_precision=form.cleaned_data["occurred_on_precision"],
            note=form.cleaned_data.get("note") or "",
            # `None` where this Matter's procedure offers no phases at all, so a
            # title correction on such a record leaves the column alone rather
            # than silently clearing it (`correct_procedural_development`).
            process_phase=(
                form.cleaned_data.get("process_phase") or ""
                if "process_phase" in form.fields
                else None
            ),
            actor=request.user,
            expected_revision=form.cleaned_data.get("revision") or "",
        )
    except ProceduralDevelopmentConflict as conflict:
        # 409, and nothing was written. The form stays open holding this person's
        # values and the version that beat them arrives beside it to read;
        # neither is chosen for them. The hidden token is **not** advanced:
        # adopting the newer one here would be this view deciding that the next
        # submit may overwrite what the other writer saved (QA-09).
        return _development_row(
            request,
            matter,
            development,
            form=form,
            error=str(conflict),
            conflict=conflict.current,
            status=409,
        )
    except DomainError as error:
        # A closed Matter lands here, and so does any refusal the service makes.
        # The sentence goes into the form that is still open rather than into a
        # panel this row does not have.
        return _development_row(
            request, matter, development, form=form, error=str(error), status=400
        )

    return _development_row(request, matter, corrected)


@login_required
@business_write_required
@require_http_methods(["GET", "POST"])
def add_development_evidence_view(
    request: HttpRequest, pk: Any, development_id: Any
) -> HttpResponse:
    """`+ Lisa fail` — another paper supporting a step the file already records.

    GET opens the picker in the chronology row; POST captures what was chosen.
    One route, because they are one interaction and the second is only reachable
    from the first — the shape `update_development_view` beside it already uses,
    and the picker replaces the same region, so `Muuda` and this cannot both be
    open on one row.

    **It adds, and it does not touch the record.** `Sündmus`, the period,
    `Juristi märkus`, the `Hetkeseis` the original save may have moved and the
    `Järgmiseks` it may have opened are all left exactly as that operation left
    them; the files the development already carries are not re-posted, so nothing
    here can detach one or supersede an immutable `DocumentVersion`. This is not
    `Muuda` widened — it is the act `Muuda` deliberately does not perform
    (`ProceduralDevelopmentEditForm`, docs/adr/0084 §8, docs/adr/0091 §5.4).

    **The answer is the whole column, not the row**, and that is the one place
    this differs from the correction beside it. A correction changes words inside
    the row and the row is the honest swap target; an addition puts a *file*
    under the row, and the file list is rendered by the chronology around it
    (`timeline_items.html`, `timeline._with_linked_files`). Coming back with the
    row alone would answer a successful upload with a row that looks exactly as
    it did before. So this returns the re-rendered column, which is what every
    other addition on this workspace returns and what makes the new file appear
    where a reader is already looking.

    A refusal takes the same route for the same reason, through
    `_workspace_refusal`: the column comes back with the sentence above it. There
    is nothing to preserve in the form — a browser will not repopulate a file
    picker whatever the server sends — so the usual «bring their words back»
    concern does not arise here, and a refusal that left a stale picker open
    would be the page suggesting the choice survived.

    **Every rule is the service's**, taken under the Matter's row lock rather
    than decided by whether this page drew a button: a closed Matter, a
    development that is not on this Teema, and an upload the evidence rules
    refuse are all answered inside `add_development_evidence`. The browser that
    posts may be holding a page from before somebody else closed the file.

    The development is resolved through its **own** `visible_to` scope before
    anything else, so a restricted step inside a Matter this reader may see is
    indistinguishable here from one that does not exist — the same answer, in the
    same shape, to a GET of the picker and to a POST guessing the UUID
    (AUTH-003, `_development_for_correction`).
    """
    matter = get_visible_matter(request, pk)
    development = _development_for_correction(request, matter, development_id)

    if request.method == "GET":
        # `Tühista`. Leaving the picker is a re-read rather than a client-side
        # hide, exactly as it is for the correction: the row is rendered from
        # what the record actually says.
        if request.GET.get(ENGAGEMENT_READ_PARAM) == ENGAGEMENT_READ_VALUE:
            return _development_row(request, matter, development)
        return _development_row(
            request,
            matter,
            development,
            evidence_form=DevelopmentEvidenceForm(record=development),
        )

    form = DevelopmentEvidenceForm(request.POST, request.FILES, record=development)
    if not form.is_valid():
        return _evidence_refusal(request, matter, key="development_evidence_form", form=form)

    try:
        workspace.add_development_evidence(
            development=development,
            author=request.user,
            uploads=form.cleaned_data["attachments"],
        )
    except (DomainError, UploadRejected) as error:
        return _evidence_refusal(
            request, matter, key="development_evidence_form", form=form, error=str(error)
        )

    return _render_overview(request, matter)


def _evidence_refusal(
    request: HttpRequest, matter: Matter, *, key: str, form: Any, error: str = ""
) -> HttpResponse:
    """Re-render the column with a refused `+ Lisa fail` back on its own row.

    Deliberately **not** `_workspace_refusal`, for the reason
    `_website_overview_refusal` gives: that helper answers by reopening a
    `LISA TEEMALE` panel, and this act has none. Routed through it, the refused
    form became a page-wide variable — so every row of the same kind drew the
    picker, all with one id, all with the same error — and the sentence, owned
    by no panel, landed under `PRAEGUNE TEGEVUS` (ENG-036).

    Here the form goes back under ``key`` and carries its own record
    (`RecordEvidenceForm.record`); the row template draws the picker only where
    that record is the row's. The sentence is the field's own error — the
    service's refusal is added to the field — so it prints under the picker it
    is about, associated with it, and nowhere else. Nothing about the record is
    stored on the model to get there.

    A closed Matter's header rides along out of band, as it does for every
    refusal that tells a stale tab the file was shut (`_workspace_refusal`).
    """
    if error:
        form.add_error("attachments", error)
    matter.refresh_from_db(fields=["is_open"])
    context = _overview_context(request, matter)
    context.update(_header_context(request, matter))
    context[key] = form
    body = render_to_string("matters/partials/overview.html", context, request=request)
    if not matter.is_open:
        context["header_out_of_band"] = True
        body += render_to_string("matters/partials/header.html", context, request=request)
    return HttpResponse(body, status=400)


@login_required
@business_write_required
@require_http_methods(["GET", "POST"])
def add_external_position_evidence_view(
    request: HttpRequest, pk: Any, position_id: Any
) -> HttpResponse:
    """`+ Lisa fail` — another paper supporting a position the file already holds.

    The act `add_development_evidence_view` performs, on the other record that
    carries evidence, and it exists for the reason QA-021 found: the position
    panel took files only at the moment of capture, and `Muuda` deliberately
    does not take bytes — so an association that sent its position paper a week
    after somebody wrote down what it said on the telephone had nowhere on the
    file to put it.

    GET opens the picker in the chronology row; POST captures what was chosen.
    One route, because they are one interaction and the second is only reachable
    from the first. The picker replaces the same region `Muuda` does, so a row
    cannot be correcting and capturing at once.

    **The answer is the whole column, not the row.** A correction changes words
    inside the row; an addition puts a *file* under it, and the file list is
    rendered by the chronology around the row. Coming back with the row alone
    would answer a successful upload with a row that looks exactly as it did
    before. A refusal takes the same route through `_workspace_refusal`: a
    browser will not repopulate a file picker whatever the server sends, so
    there is nothing to preserve and a stale picker left open would suggest the
    choice survived.

    Every rule is the service's, taken under the Matter's row lock rather than
    decided by whether this page drew a button, and the position is resolved
    through its **own** `visible_to` scope first — so a restricted position
    inside a Matter this reader may see is indistinguishable from one that does
    not exist (AUTH-003, `_external_position_for_correction`).
    """
    matter = get_visible_matter(request, pk)
    position = _external_position_for_correction(request, matter, position_id)

    if request.method == "GET":
        # `Tühista`. Leaving the picker is a re-read rather than a client-side
        # hide, exactly as it is for the correction beside it.
        if request.GET.get(ENGAGEMENT_READ_PARAM) == ENGAGEMENT_READ_VALUE:
            return _external_position_row(request, matter, position)
        return _external_position_row(
            request,
            matter,
            position,
            evidence_form=ExternalPositionEvidenceForm(record=position),
        )

    form = ExternalPositionEvidenceForm(request.POST, request.FILES, record=position)
    if not form.is_valid():
        return _evidence_refusal(request, matter, key="external_position_evidence_form", form=form)

    try:
        workspace.add_external_position_evidence(
            position=position,
            author=request.user,
            uploads=form.cleaned_data["attachments"],
        )
    except (DomainError, UploadRejected) as error:
        return _evidence_refusal(
            request, matter, key="external_position_evidence_form", form=form, error=str(error)
        )

    return _render_overview(request, matter)


def _sent_opinion_for_correction(request: HttpRequest, matter: Matter, submission_id: Any) -> Any:
    """The recorded send this request may correct, or a 404.

    Scoped through the submission's own `visible_to` and not fetched by id off
    the Matter: a `Submission` may carry a stricter visibility override than its
    parent, and reading it any other way would bypass that. Restricted to the
    rows the chronology actually draws — `historically_sent()` — so the address
    of a draft is a 404 rather than a form whose every save the service would
    refuse (`_external_position_for_correction`, docs/adr/0092 §3).
    """
    from app.submissions.models import Submission

    return get_object_or_404(
        Submission.objects.visible_to(request.user)
        .historically_sent()
        .filter(matter=matter)
        .select_related("matter"),
        pk=submission_id,
    )


def _sent_opinion_row(
    request: HttpRequest,
    matter: Matter,
    submission: Any,
    *,
    form: SentOpinionEditForm | None = None,
    error: str = "",
    conflict: Any = None,
    status: int = 200,
) -> HttpResponse:
    """The corrected opinion back in place, or the form that could not save.

    One renderer for both, because they swap the same element — the shape
    `_external_position_row` and `_development_row` already use, and the reason
    a correction cannot move the row or turn into a second line in the
    chronology.

    The milestone is rebuilt through `submission_milestone`, the same function
    the chronology itself renders from, so a corrected row cannot come back
    worded differently from the way it will read on the next page load.
    """
    from app.matters.timeline import submission_milestone
    from app.submissions.services import addressees_of

    def milestone_of(record: Any) -> Any:
        return submission_milestone(
            record, [organisation.name for organisation in addressees_of(record)]
        )

    return render(
        request,
        "matters/partials/submission_row.html",
        {
            "matter": matter,
            "submission": submission,
            "milestone": milestone_of(submission),
            "sent_opinion_edit_form": form,
            "sent_opinion_edit_error": error,
            "sent_opinion_conflict_milestone": (
                milestone_of(conflict) if conflict is not None else None
            ),
            "sent_opinion_read_query": ENGAGEMENT_READ_QUERY,
            # `can_write_business_content` is not set here: it is a context
            # processor, so every render with a request already carries it —
            # and the three sibling row renderers rely on the same thing
            # (`app/core/context_processors.py`).
        },
        status=status,
    )


def _sent_opinion_edit_form(
    request: HttpRequest, submission: Any, data: Any = None
) -> SentOpinionEditForm:
    """The correction form for one recorded send, opened on what the record says.

    The organisation catalogue is the field's **queryset**, which is what
    validates a posted id — so a crafted POST naming a body the catalogue does
    not hold is refused by the field itself, before the service is reached.
    """
    from app.organisations.models import Organisation
    from app.submissions.services import addressees_of, sent_opinion_revision

    if data is not None:
        return SentOpinionEditForm(
            data, organisations=Organisation.objects.all(), record=submission
        )
    return SentOpinionEditForm(
        organisations=Organisation.objects.all(),
        record=submission,
        initial={
            "sent_on": timezone.localtime(submission.sent_at).date(),
            "kind": submission.kind,
            "summary": submission.summary,
            "recipients": addressees_of(submission),
            "revision": sent_opinion_revision(submission),
        },
    )


@login_required
@business_write_required
@require_http_methods(["GET", "POST"])
def update_sent_opinion_view(request: HttpRequest, pk: Any, submission_id: Any) -> HttpResponse:
    """`Muuda` on a recorded `Koja arvamus` — the row that had no correction.

    GET opens the form in the chronology row; POST saves it. One route, because
    they are one interaction and the second is only reachable from the first —
    the shape `update_external_position_view` and `update_development_view`
    already use.

    **It corrects what the file says about a send, never the send.** The
    evidence, the status and `sent_by` are not on the form and cannot be
    reached: a letter whose text was wrong is a new letter, and un-sending or
    withdrawing are acts with their own services and their own meaning
    (`correct_sent_opinion`, docs/adr/0084 §8).

    **No open-Matter requirement**, which is the existing contract rather than
    an exception invented here: `withdraw_submission` corrects a recorded send
    on a closed file, and `SubmissionMetadataForm` edits an opinion's own
    metadata on one. Every rule is still the service's, taken under the
    Matter's lock.
    """
    from app.submissions.enums import SentAtPrecision
    from app.submissions.services import SentOpinionConflict, correct_sent_opinion
    from app.submissions.views import as_midnight

    matter = get_visible_matter(request, pk)
    submission = _sent_opinion_for_correction(request, matter, submission_id)

    if request.method == "GET":
        # `Tühista`. Leaving edit mode is a re-read rather than a client-side
        # hide: the boxes may be holding values that were never saved.
        if request.GET.get(ENGAGEMENT_READ_PARAM) == ENGAGEMENT_READ_VALUE:
            return _sent_opinion_row(request, matter, submission)
        return _sent_opinion_row(
            request, matter, submission, form=_sent_opinion_edit_form(request, submission)
        )

    form = _sent_opinion_edit_form(request, submission, request.POST)
    if not form.is_valid():
        return _sent_opinion_row(request, matter, submission, form=form, status=400)

    # The box shows the stored send as a local day. Where the day that comes
    # back is that same day, the send time is not what this save is about: the
    # stored instant and its precision stay exactly as recorded. `Märgi
    # saadetuks` records the moment with TIMESTAMP precision, and every
    # unrelated correction used to overwrite it with local midnight at DATE
    # precision (ENG-024). Only a day the person actually changed is re-anchored.
    if form.cleaned_data["sent_on"] == timezone.localtime(submission.sent_at).date():
        sent_at, sent_at_precision = submission.sent_at, submission.sent_at_precision
    else:
        # A day, stored as aware midnight with `DATE` precision, so the UI
        # never reads the anchor back as «00:00» — the rule
        # `RegisterSentOpinionForm` established for the same field.
        sent_at = as_midnight(form.cleaned_data["sent_on"])
        sent_at_precision = SentAtPrecision.DATE

    try:
        correct_sent_opinion(
            submission=submission,
            sent_at=sent_at,
            sent_at_precision=sent_at_precision,
            summary=form.cleaned_data.get("summary") or "",
            kind=form.cleaned_data["kind"],
            addressees=list(form.cleaned_data["recipients"]),
            actor=request.user,
            expected_revision=form.cleaned_data.get("revision") or "",
        )
    except SentOpinionConflict as conflict:
        # 409, and nothing was written. The form stays open holding this
        # person's values and the version that beat them arrives beside it to
        # read; neither is chosen for them, and the hidden token is not
        # advanced.
        return _sent_opinion_row(
            request,
            matter,
            submission,
            form=form,
            error=str(conflict),
            conflict=conflict.current,
            status=409,
        )
    except DomainError as error:
        return _sent_opinion_row(
            request, matter, submission, form=form, error=str(error), status=400
        )

    submission.refresh_from_db()
    return _sent_opinion_row(request, matter, submission)


@login_required
@business_write_required
@require_http_methods(["POST"])
def close_from_workspace(request: HttpRequest, pk: Any) -> HttpResponse:
    """`+ Lõpeta teema` — two questions, and the header follows out of band.

    The workspace swaps `#teema-vaade`, which is deliberately not the header
    band: re-rendering it on every note would rebuild five inline editors. A
    closure is the one write here that the header states — the state badge said
    `Avatud` beside an archived Matter until this was added — so the header
    rides along on this response and on no other (docs/adr/0074 §10).
    """
    matter = get_visible_matter(request, pk)
    form = CompactClosureForm(request.POST)
    if not form.is_valid():
        return _workspace_refusal(request, matter, key="closure_form", form=form)
    try:
        workspace.close_matter_from_workspace(
            matter=matter,
            author=request.user,
            disposition=form.cleaned_data["disposition"],
            closing_words=form.cleaned_data.get("closing_words") or "",
        )
    except DomainError as error:
        return _workspace_refusal(request, matter, key="closure_form", form=form, error=str(error))

    matter.refresh_from_db()
    return _render_overview(request, matter, header_out_of_band=not matter.is_open)
