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
from django.db import transaction
from django.db.models import Count, Exists, OuterRef, Q
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_date
from django.views.decorators.http import require_http_methods

from app.accounts.models import User
from app.core.authorization import may_review_work_victory, may_write_business_content
from app.core.decorators import gate_required, viewer_for
from app.core.enums import Visibility
from app.core.errors import DomainError
from app.documents.enums import DocumentRole
from app.documents.models import Document
from app.documents.uploads import UploadRejected
from app.intelligence.selectors import matter_intelligence
from app.legacy_import.register_display import (
    source_instruction_for,
    source_instructions_for,
)
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
    set_policy_area_other,
    set_position,
)
from app.matters.timeline import matter_timeline
from app.organisations.models import Organisation
from app.search import services as search_services
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
            # One query for the whole table, not one per row. The shared row
            # partial shows the register's own instruction where no structured
            # one exists (ADR 0021).
            "source_instructions": source_instructions_for(active),
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
            "source_instructions": source_instructions_for([*unassigned, *recent]),
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
    "tegevus": "Järgmine tegevus",
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
    "saabus_alates": "Saabus alates",
    "saabus_kuni": "Saabus kuni",
    "tahtaeg_alates": "Tähtaeg alates",
    "tahtaeg_kuni": "Tähtaeg kuni",
    "materjalid": "Materjalid",
}

#: What `?materjalid=` reads as in a chip.
MATERIAL_LABELS = {
    selectors.MATERIALS_PRESENT: "Failid olemas",
    selectors.MATERIALS_ABSENT: "Failid puuduvad",
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


#: What `?allikas=` means. A word rather than a boolean, because `allikas=0`
#: reads as "source number zero" in a URL somebody is editing by hand.
SOURCE_PRESENT = "on"
SOURCE_ABSENT = "puudub"
#: More than one archived page claims this Matter. A separate value rather than
#: a count parameter because it is the only threshold any statistic asks about,
#: and 402 Matters in the real corpus have it (Stage-2D brief 12).
SOURCE_SEVERAL = "mitu"

#: How each `?tegevus=` value reads in a filter chip. Wording matters here: a
#: WAIT whose review date has passed is "ülevaatus käes", never "hilinenud".
#: The two organisation directions, as URL parameters. Never merged: the same
#: register column meant the sender until 2019 and the addressee from 2020.
ORGANISATION_FILTERS = {
    "saatja": "source_organisation",
    "adressaat": "addressee_organisation",
}

NEXT_ACTION_LABELS = {
    "puudub": "Puudub",
    "teen": "Teen",
    "ootan": "Ootan",
    "jalgin": "Jälgin",
    "hilinenud": "Tähtaeg möödas",
    "ootan-ulevaatus": "Ootan — ülevaatus käes",
    "jalgin-ulevaatus": "Jälgin — ülevaatus käes",
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
        person = _named_by_pk(User, value)
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
        labels = dict(MatterOrigin.choices)
        return ", ".join(labels.get(part, part) for part in value.split(","))
    if name == "aasta":
        return "Teadmata aasta" if value == selectors.UNKNOWN_YEAR else value
    if name == "allikas":
        if value == SOURCE_SEVERAL:
            return "Mitu lähtelehte"
        return "Olemas" if value == SOURCE_PRESENT else "Puudub"
    if name == "tegevus":
        return NEXT_ACTION_LABELS.get(value, value)
    if name in {"saatja", "adressaat", "asutus"}:
        organisation = _named_by_pk(Organisation, value)
        return organisation.name if organisation else value
    if name == "materjalid":
        return MATERIAL_LABELS.get(value, value)
    if name in DATE_FILTERS:
        # The stored value is ISO because that is what `<input type=date>`
        # submits; the chip reads it back the way Estonians write dates.
        parsed = parse_date(value)
        return f"{parsed:%d.%m.%Y}" if parsed else value
    return value


#: Which parameters hold a date, and which column each pair narrows. Both ends
#: of each pair are inclusive — see `selectors.date_range_q`.
DATE_FILTERS = {
    "saabus_alates": ("received_date", "start"),
    "saabus_kuni": ("received_date", "end"),
    "tahtaeg_alates": ("response_deadline", "start"),
    "tahtaeg_kuni": ("response_deadline", "end"),
}


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


def _apply_date_filters(queryset: Any, params: Any) -> tuple[Any, dict[str, str]]:
    """``?saabus_alates=`` and its three siblings, as two closed intervals.

    An unreadable date empties the list rather than being ignored, for the same
    reason an unreadable year does: a chip reading "31.02.2024" above the whole
    register is a lie the reader has no way to catch.
    """
    bounds: dict[str, dict[str, Any]] = {}
    echo: dict[str, str] = {}
    for parameter, (field, edge) in DATE_FILTERS.items():
        raw = (params.get(parameter) or "").strip()
        if not raw:
            continue
        echo[parameter] = raw
        parsed = parse_date(raw)
        if parsed is None:
            return queryset.none(), echo
        bounds.setdefault(field, {})[edge] = parsed

    for field, edges in bounds.items():
        queryset = queryset.filter(
            selectors.date_range_q(field, start=edges.get("start"), end=edges.get("end"))
        )
    return queryset, echo


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

    # `puudub` is the one word every dimension uses for "this field is empty",
    # so that the *Määramata* bucket on a chart is a link like any other bar
    # rather than a number the reader has to take on trust (Stage-2E brief 42).
    if owner_id := params.get("vastutaja"):
        if owner_id == selectors.MISSING:
            queryset = queryset.filter(owner__isnull=True)
        else:
            # A hand-edited URL must not reach the database as a malformed UUID.
            try:
                queryset = queryset.filter(owner_id=uuid.UUID(owner_id))
            except ValueError:
                queryset = queryset.none()
    if stage_key := params.get("hetkeseis"):
        if stage_key == selectors.MISSING:
            queryset = queryset.filter(stage__isnull=True)
        else:
            queryset = queryset.filter(stage__key=stage_key)
    if track := params.get("menetlusliik"):
        queryset = queryset.filter(track="" if track == selectors.MISSING else track)
    if area_key := params.get("valdkond"):
        if area_key == selectors.MISSING:
            queryset = queryset.filter(policy_areas__isnull=True)
        else:
            queryset = queryset.filter(policy_areas__key=area_key)
    if tag_key := params.get("silt"):
        queryset = queryset.filter(tags__key=tag_key)
    if mode := params.get("liik"):
        queryset = queryset.filter(record_mode=mode)
    if origin := params.get("paritolu"):
        # Comma-separated, because one statistic legitimately means two origins:
        # a register row is `LEGACY_IMPORT` or an archive row somebody activated
        # (`PROMOTED_LEGACY`), and the bar counting both has to open both.
        queryset = queryset.filter(origin__in=origin.split(","))
    if year := params.get("aasta"):
        queryset = _filter_by_reporting_year(queryset, year)
    if source := params.get("allikas"):
        if source == SOURCE_SEVERAL:
            queryset = queryset.annotate(
                source_page_count=Count("source_pages", distinct=True)
            ).filter(source_page_count__gte=2)
        else:
            has_page = Exists(MatterSourcePage.objects.filter(matter=OuterRef("pk")))
            queryset = queryset.annotate(has_source_page=has_page).filter(
                has_source_page=source == SOURCE_PRESENT
            )
    if action_filter := params.get("tegevus"):
        queryset = selectors.filter_by_next_action(queryset, request.user, action_filter)
    for parameter, field in ORGANISATION_FILTERS.items():
        raw = params.get(parameter)
        if not raw:
            continue
        if raw == selectors.MISSING:
            queryset = queryset.filter(**{f"{field}__isnull": True})
            continue
        try:
            queryset = queryset.filter(**{f"{field}_id": uuid.UUID(raw)})
        except ValueError:
            queryset = queryset.none()
    # The convenience filter, beside the two precise ones rather than instead of
    # them. Nothing stored is collapsed: this is an OR over two columns that
    # keep their separate meanings (Stage-2E.1 brief 11F).
    if involved := params.get("asutus"):
        try:
            queryset = queryset.filter(selectors.organisation_involved_q(uuid.UUID(involved)))
        except ValueError:
            queryset = queryset.none()
    if materials := params.get("materjalid"):
        queryset = selectors.filter_by_materials(queryset, request.user, materials)

    queryset, date_echo = _apply_date_filters(queryset, params)

    sort = params.get("jarjestus", "reference")
    queryset = queryset.order_by(*SORT_FIELDS.get(sort, SORT_FIELDS["reference"]), "-created_at")

    paginator = Paginator(queryset.distinct(), PAGE_SIZE)
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
        # What the search box submits alongside `q`, so typing narrows the
        # chosen filters rather than silently widening the population.
        "carried_params": [
            (name, value)
            for name, value in params.items()
            if name not in {"q", "leht"} and value and not _is_control_param(name)
        ],
        "status_options": _status_options(request, params),
        "active_filters": chips,
        "filters": {
            "olek": status,
            "ulatus": scope,
            "q": query,
            "asutus": params.get("asutus", ""),
            "materjalid": params.get("materjalid", ""),
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
            "saatja": params.get("saatja", ""),
            "adressaat": params.get("adressaat", ""),
            "jarjestus": sort,
        },
        "nav_active": "teemad",
    }

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
        "owners": User.objects.filter(is_active=True).order_by("display_name"),
        "stages": StageVocabulary.objects.filter(is_active=True).order_by("sort_order"),
        "tracks": Track.choices,
        "policy_areas": PolicyArea.objects.filter(is_active=True).order_by("name_et"),
        "record_modes": RecordMode.choices,
        "origins": MatterOrigin.choices,
        "next_action_options": list(NEXT_ACTION_LABELS.items()),
        "material_options": sorted(MATERIAL_LABELS.items()),
        "chosen_organisation": _organisation_or_none(params.get("asutus", "")),
        "organisation_options": _organisation_options(""),
    }
    return render(request, "matters/matter_list.html", context)


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
    action_form = NextActionForm(request.POST or None, prefix="next")
    uploads: list[Any] = []
    upload_error = ""

    if request.method == "POST" and form.is_valid():
        wants_action = bool(request.POST.get("next-text", "").strip())
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
        with transaction.atomic():
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
                policy_area_other=data.get("policy_area_other") or "",
                # Decided here, never read from the form. The control is gone
                # from the page and an omitted field must not become a blank
                # value the model would refuse (brief 21).
                visibility=Visibility.NORMAL,
            )
            for upload in uploads:
                _attach_incoming_file(matter, upload, actor=request.user)

            if wants_action:
                set_next_action(
                    matter=matter, actor=request.user, **action_form.as_service_kwargs()
                )

        if uploads:
            messages.success(
                request,
                f"Teema {matter.display_reference} on loodud koos {len(uploads)} failiga.",
            )
        else:
            messages.success(request, f"Teema {matter.display_reference} on loodud.")
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
        # Named here rather than excluded in the template by listing the primary
        # ones: a field added to the form later should appear *somewhere* by
        # default, and the safe default is the disclosure.
        "secondary_fields": (
            "stage",
            "track",
            "addressee_organisation",
            "response_deadline",
        ),
        "today": timezone.localdate(),
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
        # The register's own `JÄRGMISEKS`, shown only where no structured action
        # exists. Read here rather than in the template so the page cannot start
        # asking the database a question of its own (ADR 0021).
        "source_instruction": source_instruction_for(matter),
        "timeline_items": items,
        "timeline_has_more": has_more,
        "composer_form": ComposerForm(),
        "incoming_documents": _incoming_documents(request, matter),
        "historical": _historical_context(matter, request.user),
        # Stage 2G's structured facts. Read through their own selector, which
        # scopes them like every other child record; the write controls are
        # routed to `app.intelligence` rather than rendered from here, so this
        # view knows nothing about how they are captured.
        "intelligence": matter_intelligence(matter, request.user),
        "can_write": may_write_business_content(request.user),
        "can_review_victory": may_review_work_victory(request.user),
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
            # Why a reconstructed submission says what it says. Prefetched
            # rather than fetched per card: a historical Matter can carry
            # several, and a query per card is a query per card.
            "archive_imports",
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
        submission.archive_import_rows = list(submission.archive_imports.all())
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
    "policy_area_other",
}


@login_required
@require_http_methods(["POST"])
def update_field(request: HttpRequest, pk: Any, field: str) -> HttpResponse:
    """Inline header edits. One field, one service call, one re-render."""
    if field not in FIELD_SERVICES:
        raise Http404("Tundmatu väli.")

    matter = get_visible_matter(request, pk)
    surface = _FIELD_SURFACES.get(field, "matters/partials/header.html")
    form = MatterFieldForm(request.POST)
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
        elif field == "policy_area_other":
            set_policy_area_other(matter=matter, value=value or "", actor=request.user)
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
_FIELD_SURFACES = {"policy_area_other": "matters/partials/rail.html"}


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
