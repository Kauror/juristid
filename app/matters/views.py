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
from typing import Any

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Exists, OuterRef, Q
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_date
from django.views.decorators.http import require_http_methods

from app.accounts.models import User
from app.core.decorators import gate_required, viewer_for
from app.core.enums import Visibility
from app.core.errors import DomainError
from app.documents.enums import DocumentRole
from app.documents.models import Document
from app.documents.uploads import UploadRejected
from app.legacy_import.source_pages import MatterSourcePage
from app.matters import selectors
from app.matters.dashboard import build_dashboard
from app.matters.entry_enums import EntryKind
from app.matters.enums import MatterOrigin, RecordMode
from app.matters.forms import (
    CloseMatterForm,
    ComposerForm,
    IncomingIntakeForm,
    MatterCreateForm,
    MatterFieldForm,
    NextActionForm,
    PositionForm,
)
from app.matters.intake import register_incoming, validate_uploads
from app.matters.models import Matter
from app.matters.services import (
    assign_matter,
    change_stage,
    change_track,
    close_matter,
    compose_update,
    create_matter,
    reopen_matter,
    set_matter_dates,
    set_matter_visibility,
    set_organisations,
    set_position,
)
from app.matters.timeline import matter_timeline
from app.organisations.models import Organisation
from app.submissions.enums import RecipientRole
from app.submissions.forms import SubmissionCreateForm
from app.submissions.models import Submission
from app.taxonomy.models import PolicyArea
from app.workflow.enums import ActionKind, Disposition, Track
from app.workflow.models import NextAction, StageVocabulary
from app.workflow.services import (
    acknowledge_review,
    complete_next_action,
    current_next_action,
    set_next_action,
)

PAGE_SIZE = 25
TIMELINE_PAGE_SIZE = 30


def get_visible_matter(request: HttpRequest, pk: Any) -> Matter:
    """Fetch a Matter the signed-in user is allowed to read, or 404.

    404 rather than 403 on purpose: distinguishing "forbidden" from "missing"
    tells an unauthorized caller that a restricted Matter with that id exists.
    """
    queryset = (
        Matter.objects.visible_to(request.user)
        .select_related("owner", "stage", "source_organisation", "addressee_organisation")
        .prefetch_related("policy_areas", "tags", "collaborators")
    )
    return get_object_or_404(queryset, pk=pk)


# ---------------------------------------------------------------------------
# Minu töö
# ---------------------------------------------------------------------------


@gate_required
def overview(request: HttpRequest) -> HttpResponse:
    """Ülevaade — the department portfolio, scoped to what this reader may see.

    Deliberately not Minu töö. That page answers "what do I have to do today";
    this one answers "what is the state of the files", which is the question a
    morning department review starts from.

    The one page that renders without a persona. In shared-gate mode somebody
    lands here straight from the password, and the dashboard has to be worth
    looking at before they have said who they are — so it is built for a
    *department* scope rather than borrowed from an arbitrary person's identity.
    That scope sees NORMAL visibility and no participation, which means nothing
    RESTRICTED appears merely because a shared password was typed
    (Stage-2D auth brief 6, app/core/authorization.py).
    """
    return render(
        request,
        "matters/overview_dashboard.html",
        {
            "dashboard": build_dashboard(viewer_for(request)),
            "today": timezone.localdate(),
            "nav_active": "ulevaade",
        },
    )


@login_required
def my_work(request: HttpRequest) -> HttpResponse:
    today = timezone.localdate()
    do_groups = selectors.my_do_groups(request.user, today)
    waiting = selectors.my_waiting_actions(request.user, today)
    attention = selectors.my_attention_items(request.user, today)
    active = selectors.my_active_matters(request.user)[:PAGE_SIZE]

    return render(
        request,
        "matters/my_work.html",
        {
            "today": today,
            "do_groups": do_groups,
            "do_total": sum(group.count for group in do_groups),
            "waiting": waiting,
            "waiting_due": [action for action in waiting if action.is_due_for_review(today)],
            "attention": attention,
            "without_next_action": list(
                selectors.matters_without_next_action(request.user).filter(
                    owner_id=request.user.pk
                )[:20]
            ),
            "active_matters": active,
            "active_count": selectors.my_active_matters(request.user).count(),
            "nav_active": "minu_too",
        },
    )


@login_required
def inbox(request: HttpRequest) -> HttpResponse:
    """Saabunud — the triage entry point.

    Stage 1 keeps this deliberately thin: a strong `Uus teema` action and the
    unassigned open Matters anyone can pick up. Machine intake and an
    `IntakeItem` queue are later work and are not faked here.
    """
    unassigned = (
        selectors.matter_list_queryset(request.user)
        .filter(owner__isnull=True, is_open=True)
        .order_by("-created_at")[:PAGE_SIZE]
    )
    recent = (
        selectors.matter_list_queryset(request.user)
        .filter(is_open=True)
        .order_by("-created_at")[:10]
    )
    return render(
        request,
        "matters/inbox.html",
        {
            "unassigned": unassigned,
            "recent": recent,
            "intake_form": IncomingIntakeForm(),
            "nav_active": "saabunud",
        },
    )


@login_required
@require_http_methods(["GET", "POST"])
def intake(request: HttpRequest) -> HttpResponse:
    """File material that has just arrived, files first.

    The second legitimate way into a Matter. `Uus teema` stays title-first and
    needs nothing but a title; this path starts from the PDF a ministry sent,
    which is how most incoming work actually begins.

    Every upload is validated *before* anything is written, so a rejected file
    cannot leave a half-made Matter behind looking like real work.
    """
    form = IncomingIntakeForm(request.POST or None)

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
                    source_organisation=data.get("source_organisation"),
                    received_date=data.get("received_date") or timezone.localdate(),
                    response_deadline=data.get("response_deadline"),
                    stage=data.get("stage"),
                    track=data.get("track") or "",
                    visibility=data.get("visibility") or Visibility.NORMAL,
                )
            except (DomainError, UploadRejected) as error:
                messages.error(request, str(error))
            else:
                messages.success(
                    request,
                    f"Teema {result.matter.display_reference} loodud · "
                    f"{result.documents} faili lisatud.",
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
}

#: What `?allikas=` means. A word rather than a boolean, because `allikas=0`
#: reads as "source number zero" in a URL somebody is editing by hand.
SOURCE_PRESENT = "on"
SOURCE_ABSENT = "puudub"

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


def _filter_display(request: HttpRequest, name: str, value: str) -> str:
    """Show the reader a name, not a primary key."""
    if name == "vastutaja":
        person = User.objects.filter(pk=value).first()
        return person.display_name if person else value
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
        return dict(MatterOrigin.choices).get(value, value)
    if name == "aasta":
        return "Teadmata aasta" if value == selectors.UNKNOWN_YEAR else value
    if name == "allikas":
        return "Olemas" if value == SOURCE_PRESENT else "Puudub"
    return value


def _active_filters(request: HttpRequest, params: Any) -> list[dict[str, Any]]:
    chips = []
    for name, label in FILTER_LABELS.items():
        value = params.get(name, "")
        if not value or (name == "ulatus" and value == "koik"):
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


def _filter_by_reporting_year(queryset: Any, value: str) -> Any:
    """Apply `?aasta=`, using the same condition the year chart counted with.

    Accepts `YYYY`, `YYYY-YYYY`, or the word that asks for the bucket with no
    usable year. An unreadable value empties the list rather than being ignored:
    a filter chip saying "2O24" above the whole register would be a lie the
    reader has no way to catch.
    """
    if value == selectors.UNKNOWN_YEAR:
        return queryset.filter(selectors.unknown_register_year_q())

    parts = value.split("-")
    try:
        if len(parts) == 1:
            first = last = int(parts[0])
        elif len(parts) == 2:
            first, last = int(parts[0]), int(parts[1])
        else:
            return queryset.none()
    except ValueError:
        return queryset.none()

    if first > last:
        return queryset.none()
    return queryset.filter(selectors.register_year_q(start=first, end=last))


SORT_FIELDS = {
    "reference": ("-reference_year", "-reference_number"),
    "title": ("title",),
    "updated": ("-updated_at",),
    "deadline": ("response_deadline",),
}


@login_required
def matter_list(request: HttpRequest) -> HttpResponse:
    """The register. Dense, filtered through the URL, paginated server-side."""
    params = request.GET
    queryset = selectors.matter_list_queryset(request.user)

    status = params.get("olek", "avatud")
    if status == "avatud":
        queryset = queryset.filter(is_open=True)
    elif status == "suletud":
        queryset = queryset.filter(is_open=False)
    elif status == "arhiiv":
        queryset = queryset.filter(record_mode=RecordMode.ARCHIVE)

    scope = params.get("ulatus", "koik")
    if scope == "minu":
        queryset = queryset.filter(Q(owner=request.user) | Q(collaborators=request.user))

    if owner_id := params.get("vastutaja"):
        # A hand-edited URL must not reach the database as a malformed UUID.
        try:
            queryset = queryset.filter(owner_id=uuid.UUID(owner_id))
        except ValueError:
            queryset = queryset.none()
    if stage_key := params.get("hetkeseis"):
        queryset = queryset.filter(stage__key=stage_key)
    if track := params.get("menetlusliik"):
        queryset = queryset.filter(track=track)
    if area_key := params.get("valdkond"):
        queryset = queryset.filter(policy_areas__key=area_key)
    if tag_key := params.get("silt"):
        queryset = queryset.filter(tags__key=tag_key)
    if mode := params.get("liik"):
        queryset = queryset.filter(record_mode=mode)
    if origin := params.get("paritolu"):
        queryset = queryset.filter(origin=origin)
    if year := params.get("aasta"):
        queryset = _filter_by_reporting_year(queryset, year)
    if source := params.get("allikas"):
        has_page = Exists(MatterSourcePage.objects.filter(matter=OuterRef("pk")))
        queryset = queryset.annotate(has_source_page=has_page).filter(
            has_source_page=source == SOURCE_PRESENT
        )

    sort = params.get("jarjestus", "reference")
    queryset = queryset.order_by(*SORT_FIELDS.get(sort, SORT_FIELDS["reference"]), "-created_at")

    paginator = Paginator(queryset.distinct(), PAGE_SIZE)
    page = paginator.get_page(params.get("leht"))

    query_without_page = params.copy()
    query_without_page.pop("leht", None)

    return render(
        request,
        "matters/matter_list.html",
        {
            "page": page,
            "paginator": paginator,
            "total": paginator.count,
            "query_string": query_without_page.urlencode(),
            "status_options": _status_options(request, params),
            "active_filters": _active_filters(request, params),
            "filters": {
                "olek": status,
                "ulatus": scope,
                "vastutaja": params.get("vastutaja", ""),
                "hetkeseis": params.get("hetkeseis", ""),
                "menetlusliik": params.get("menetlusliik", ""),
                "valdkond": params.get("valdkond", ""),
                "silt": params.get("silt", ""),
                "liik": params.get("liik", ""),
                "paritolu": params.get("paritolu", ""),
                "aasta": params.get("aasta", ""),
                "allikas": params.get("allikas", ""),
                "jarjestus": sort,
            },
            "owners": User.objects.filter(is_active=True).order_by("display_name"),
            "stages": StageVocabulary.objects.filter(is_active=True).order_by("sort_order"),
            "tracks": Track.choices,
            "policy_areas": PolicyArea.objects.filter(is_active=True).order_by("name_et"),
            "record_modes": RecordMode.choices,
            "nav_active": "teemad",
        },
    )


# ---------------------------------------------------------------------------
# Creating a Teema
# ---------------------------------------------------------------------------


@login_required
@require_http_methods(["GET", "POST"])
def matter_create(request: HttpRequest) -> HttpResponse:
    form = MatterCreateForm(request.POST or None)
    action_form = NextActionForm(request.POST or None, prefix="next")

    if request.method == "POST" and form.is_valid():
        wants_action = bool(request.POST.get("next-text", "").strip())
        if wants_action and not action_form.is_valid():
            return render(
                request,
                "matters/matter_create.html",
                {"form": form, "action_form": action_form, "nav_active": "teemad"},
                status=400,
            )

        data = form.cleaned_data
        matter = create_matter(
            title=data["title"],
            actor=request.user,
            owner=data.get("owner"),
            stage=data.get("stage"),
            track=data.get("track") or "",
            source_organisation=data.get("source_organisation"),
            addressee_organisation=data.get("addressee_organisation"),
            received_date=data.get("received_date"),
            response_deadline=data.get("response_deadline"),
            policy_areas=list(data.get("policy_areas") or []),
            visibility=data.get("visibility") or Visibility.NORMAL,
        )

        if wants_action:
            set_next_action(matter=matter, actor=request.user, **action_form.as_service_kwargs())

        messages.success(request, f"Teema {matter.display_reference} on loodud.")
        # Straight into the file: creation is the start of work, not the end.
        return redirect("matters:matter_detail", pk=matter.pk)

    return render(
        request,
        "matters/matter_create.html",
        {"form": form, "action_form": action_form, "nav_active": "teemad"},
    )


# ---------------------------------------------------------------------------
# The Matter page
# ---------------------------------------------------------------------------


#: What arrived from outside, as opposed to what Koda produced. Shown on the
#: overview so an incoming Matter reveals its source material immediately rather
#: than hiding it one tab away.
INCOMING_ROLES = (DocumentRole.INCOMING_AUTHORITY, DocumentRole.ORIGINAL_EMAIL)


def _incoming_documents(request: HttpRequest, matter: Matter) -> list[Document]:
    """Source material for this Matter, authorized like everything else.

    Scoped through ``Document.objects.visible_to`` rather than the Matter's own
    relation, so a document restricted more tightly than its Matter stays
    invisible to someone who may read the Matter itself.
    """
    return list(
        Document.objects.visible_to(request.user)
        .filter(matter=matter, role__in=INCOMING_ROLES)
        .select_related("current_version")
        .order_by("created_at")[:20]
    )


def _overview_context(request: HttpRequest, matter: Matter) -> dict[str, Any]:
    items, has_more = matter_timeline(matter=matter, user=request.user, limit=TIMELINE_PAGE_SIZE)
    return {
        "matter": matter,
        "current_action": current_next_action(matter),
        "timeline_items": items,
        "timeline_has_more": has_more,
        "composer_form": ComposerForm(),
        "incoming_documents": _incoming_documents(request, matter),
        "historical": _historical_context(matter, request.user),
        "today": timezone.localdate(),
    }


@login_required
def matter_detail(request: HttpRequest, pk: Any) -> HttpResponse:
    matter = get_visible_matter(request, pk)
    context = _overview_context(request, matter)
    context.update(_header_context(request, matter))
    context["tab"] = "ulevaade"
    context["nav_active"] = "teemad"
    return render(request, "matters/matter_detail.html", context)


def _header_context(request: HttpRequest, matter: Matter) -> dict[str, Any]:
    return {
        "matter": matter,
        "submission_count": Submission.objects.filter(matter=matter)
        .visible_to(request.user)
        .count(),
        "document_count": Document.objects.filter(matter=matter).visible_to(request.user).count(),
        "dispositions": Disposition.choices,
        "owners": User.objects.filter(is_active=True).order_by("display_name"),
        "stages": StageVocabulary.objects.filter(is_active=True).order_by("sort_order"),
        "organisations": Organisation.objects.order_by("name"),
        "tracks": Track.choices,
        "visibilities": Visibility.choices,
        "current_action": current_next_action(matter),
        "today": timezone.localdate(),
    }


@login_required
def matter_position(request: HttpRequest, pk: Any) -> HttpResponse:
    """Seisukoht ja kaasamine.

    Stage 1 fills the position and the submissions. Consultation (`Kaasamine`)
    is Stage-2 work and is left visibly absent rather than mocked up, because a
    placeholder that looks like a feature is worse than an honest gap.
    """
    matter = get_visible_matter(request, pk)
    submissions = list(
        Submission.objects.filter(matter=matter)
        .visible_to(request.user)
        .select_related("final_version", "sent_by")
        .prefetch_related(
            "recipient_rows__organisation",
            "joint_submitter_rows__organisation",
        )
        .order_by("-sent_at", "-created_at")
    )
    # Split the recipients by role in Python off the prefetch, rather than
    # issuing two more queries per card.
    for submission in submissions:
        rows = list(submission.recipient_rows.all())
        submission.addressee_list = [
            row.organisation for row in rows if row.role == RecipientRole.ADDRESSEE
        ]
        submission.information_list = [
            row.organisation for row in rows if row.role == RecipientRole.FOR_INFORMATION
        ]
        submission.joint_rows = list(submission.joint_submitter_rows.all())
    context = _header_context(request, matter)
    context.update(
        {
            "tab": "seisukoht",
            "nav_active": "teemad",
            "position_form": PositionForm(
                initial={
                    "position_summary": matter.position_summary,
                    "rationale_summary": matter.rationale_summary,
                }
            ),
            "submissions": submissions,
            "submission_form": SubmissionCreateForm(),
        }
    )
    return render(request, "matters/matter_position.html", context)


def _historical_context(matter: Any, user: Any) -> dict:
    """Historical source material for a Matter, or nothing at all.

    Imported lazily: `app.legacy_import` imports the matters app, and doing this
    at module scope closes the circle. A Matter with no OneNote history gets an
    empty dict and the templates render nothing — a heading over an empty list
    reads as a data-quality problem rather than as an absence (Stage-2D 35).
    """
    from app.legacy_import.historical_views import historical_summary

    return historical_summary(matter, user)


@login_required
def matter_documents(request: HttpRequest, pk: Any) -> HttpResponse:
    matter = get_visible_matter(request, pk)
    documents = (
        Document.objects.filter(matter=matter)
        .visible_to(request.user)
        .select_related("current_version", "created_by")
        .prefetch_related("versions")
        .order_by("-created_at")
    )
    context = _header_context(request, matter)
    context.update(
        {
            "tab": "dokumendid",
            "nav_active": "teemad",
            "evidence_documents": [doc for doc in documents if not doc.has_working_document],
            "working_documents": [doc for doc in documents if doc.has_working_document],
            "document_roles": DocumentRole.choices,
            "historical": _historical_context(matter, request.user),
        }
    )
    return render(request, "matters/matter_documents.html", context)


# ---------------------------------------------------------------------------
# HTMX actions
# ---------------------------------------------------------------------------


def _render_overview(request: HttpRequest, matter: Matter, status: int = 200) -> HttpResponse:
    """Re-render the whole overview column.

    One render from one set of queries, so `Järgmiseks` and the timeline can
    never show different pictures of the same save.
    """
    context = _overview_context(request, matter)
    context.update(_header_context(request, matter))
    return render(request, "matters/partials/overview.html", context, status=status)


@login_required
@require_http_methods(["POST"])
def compose(request: HttpRequest, pk: Any) -> HttpResponse:
    """The unified composer save. Entry and `Järgmiseks` land together."""
    matter = get_visible_matter(request, pk)
    form = ComposerForm(request.POST, request.FILES)

    if not form.is_valid():
        context = _overview_context(request, matter)
        context.update(_header_context(request, matter))
        context["composer_form"] = form
        return render(request, "matters/partials/overview.html", context, status=400)

    try:
        compose_update(
            matter=matter,
            author=request.user,
            body=form.cleaned_data.get("body") or "",
            kind=form.cleaned_data.get("kind") or EntryKind.NOTE,
            occurred_at=form.cleaned_data.get("occurred_at"),
            organisation=form.cleaned_data.get("organisation"),
            next_action=form.next_action_kwargs(),
            attachment=form.cleaned_data.get("attachment"),
        )
    except (DomainError, UploadRejected) as error:
        context = _overview_context(request, matter)
        context.update(_header_context(request, matter))
        context["composer_form"] = form
        context["composer_error"] = str(error)
        return render(request, "matters/partials/overview.html", context, status=400)

    matter.refresh_from_db()
    return _render_overview(request, matter)


@login_required
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
        set_next_action(matter=matter, actor=request.user, **form.as_service_kwargs())
    except DomainError as error:
        context = _overview_context(request, matter)
        context.update(_header_context(request, matter))
        context["composer_error"] = str(error)
        return render(request, "matters/partials/overview.html", context, status=400)

    return _render_overview(request, matter)


@login_required
@require_http_methods(["POST"])
def complete_action(request: HttpRequest, pk: Any, action_id: Any) -> HttpResponse:
    matter = get_visible_matter(request, pk)
    action = get_object_or_404(
        NextAction.objects.visible_to(request.user), pk=action_id, matter=matter
    )
    try:
        complete_next_action(action=action, actor=request.user)
    except DomainError as error:
        context = _overview_context(request, matter)
        context.update(_header_context(request, matter))
        context["composer_error"] = str(error)
        return render(request, "matters/partials/overview.html", context, status=400)
    return _render_overview(request, matter)


@login_required
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
    raw_date = request.POST.get("next_review_date", "").strip()
    next_review_date = parse_date(raw_date) if raw_date else None

    try:
        acknowledge_review(action=action, actor=request.user, next_review_date=next_review_date)
    except DomainError as error:
        context = _overview_context(request, matter)
        context.update(_header_context(request, matter))
        context["composer_error"] = str(error)
        return render(request, "matters/partials/overview.html", context, status=400)

    return _render_overview(request, matter)


FIELD_SERVICES = {
    "owner",
    "stage",
    "track",
    "source_organisation",
    "addressee_organisation",
    "received_date",
    "response_deadline",
    "visibility",
}


@login_required
@require_http_methods(["POST"])
def update_field(request: HttpRequest, pk: Any, field: str) -> HttpResponse:
    """Inline header edits. One field, one service call, one re-render."""
    if field not in FIELD_SERVICES:
        raise Http404("Tundmatu väli.")

    matter = get_visible_matter(request, pk)
    form = MatterFieldForm(request.POST)
    if not form.is_valid():
        context = _header_context(request, matter)
        context["field_error"] = "Vigane väärtus."
        return render(request, "matters/partials/header.html", context, status=400)

    value = form.cleaned_data.get(field)
    try:
        if field == "owner":
            assign_matter(matter=matter, owner=value, actor=request.user)
        elif field == "stage":
            change_stage(matter=matter, stage=value, actor=request.user)
        elif field == "track":
            change_track(matter=matter, track=value or "", actor=request.user)
        elif field == "source_organisation":
            set_organisations(matter=matter, source_organisation=value, actor=request.user)
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
    except DomainError as error:
        context = _header_context(request, matter)
        context["field_error"] = str(error)
        return render(request, "matters/partials/header.html", context, status=400)

    matter.refresh_from_db()
    return render(request, "matters/partials/header.html", _header_context(request, matter))


@login_required
@require_http_methods(["POST"])
def update_position(request: HttpRequest, pk: Any) -> HttpResponse:
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
    return redirect("matters:matter_position", pk=matter.pk)


@login_required
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

    items, has_more = matter_timeline(
        matter=matter, user=request.user, limit=TIMELINE_PAGE_SIZE, offset=offset
    )
    return render(
        request,
        "matters/partials/timeline_items.html",
        {
            "matter": matter,
            "timeline_items": items,
            "timeline_has_more": has_more,
            "next_offset": offset + TIMELINE_PAGE_SIZE,
        },
    )


def matter_url(matter: Matter) -> str:
    return reverse("matters:matter_detail", kwargs={"pk": matter.pk})


ACTION_KIND_LABELS = dict(ActionKind.choices)
