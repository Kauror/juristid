"""Three generated department views, and the write surfaces on the Matter page.

Reads are ``@gate_required`` and authorize as ``viewer_for(request)``, exactly
like Statistika: in shared-gate mode somebody arrives before choosing a persona,
and the department scope — NORMAL visibility, no participation — is what they
may see. Writes are ``@login_required`` **and** check a business role, because
the shared-gate sentinel is not a person and must never become an audit actor
(Stage-2D auth brief 6, Stage-2G brief 29).

Every Matter lookup goes through :func:`_matter_for` and raises 404 rather than
403 for a Matter the reader may not see, following the convention
``app.matters.views`` established: a 403 would confirm that a restricted record
with that id exists.

Adding one of these facts is a full-page POST that redirects back to the Matter,
rather than an HTMX fragment swap. The Matter overview is rendered by
``app.matters.views`` from its own context builders, and swapping a fragment of
it from here would mean this app reaching into that one's rendering — a coupling
worth more than the half-second it saves (Stage-2G brief 74).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_http_methods

from app.core.authorization import may_review_work_victory
from app.core.decorators import business_write_required, gate_required, viewer_for
from app.core.errors import DomainError
from app.intelligence import filters, sections, selectors, services
from app.intelligence.enums import WorkVictoryStatus
from app.intelligence.forms import (
    EffectiveDateForm,
    ImportantDateForm,
    ReasonForm,
    WorkVictoryForm,
)
from app.intelligence.models import (
    MatterEffectiveDate,
    MatterImportantDate,
    MatterWorkVictory,
)
from app.matters.models import Matter
from app.workflow.dates import year_from

PAGE_SIZE = 50

#: The columns each Jälgimine table carries, and the class that sizes each one.
#: In the view rather than in the template because the section partial is shared
#: and a template cannot build a list literal to hand it (02-EKRAANID §D).
DEADLINE_COLUMNS = (
    ("Kuupäev", "table__date"),
    ("Tähtaeg", "table__title"),
    ("Teema", "table__matter"),
    ("Vastutaja", "table__owner"),
)
EFFECTIVE_COLUMNS = (
    ("Jõustub", "table__date"),
    ("Tähtaeg", "table__title"),
    ("Teema", "table__matter"),
    ("Vastutaja", "table__owner"),
)
VICTORY_COLUMNS = (
    ("Kuupäev", "table__date"),
    ("Mis muutus", "table__title"),
    ("Teema", "table__matter"),
    ("Tõend", "table__action"),
)

#: The tab strip over the three generated views. One navigation item and three
#: tabs rather than three top-level items, which is the shape Statistika already
#: uses and the shell already styles (Stage-2G brief 38).
TABS: tuple[tuple[str, str, str], ...] = (
    ("tahtajad", "Olulised tähtajad", "intelligence:important_dates"),
    ("joustumised", "Jõustuvad aktid", "intelligence:effective_dates"),
    ("toovoidud", "Töövõidud", "intelligence:work_victories"),
)


#: The heading each tab carries. One navigation item, three destinations, and
#: each page names itself rather than every one of them being headed
#: «Jälgimine» with the answer in a tab strip below it (02-EKRAANID §D).
TAB_TITLES: dict[str, str] = {key: label for key, label, _ in TABS}


def _shell(request: HttpRequest, tab: str) -> dict[str, Any]:
    return {
        "tabs": TABS,
        "active_tab": tab,
        "page_title": TAB_TITLES[tab],
        "nav_active": "jalgimine",
        "today": timezone.localdate(),
        "query_string": request.GET.urlencode(),
    }


def _year_param(request: HttpRequest) -> int | None:
    """``?aasta=`` as an integer, or nothing.

    A value that is not a year is dropped rather than raised on: a URL somebody
    hand-edited should show the unfiltered page, not a stack trace.

    ``99999`` is not a year either, and used to be treated as one — it passed
    ``isdigit()``, reached the ORM and raised, which is precisely the stack
    trace the sentence above promises not to produce (CORR-02).
    """
    return year_from(request.GET.get("aasta"))


def _matter_for(request: HttpRequest, matter_id: Any) -> Matter:
    return get_object_or_404(Matter.objects.visible_to(request.user), pk=matter_id)


def _matter_anchor(matter: Matter, anchor: str) -> str:
    return f"{reverse('matters:matter_detail', kwargs={'pk': matter.pk})}#{anchor}"


# ---------------------------------------------------------------------------
# Olulised tähtajad
# ---------------------------------------------------------------------------


@gate_required
def important_dates(request: HttpRequest) -> HttpResponse:
    """The department's watch list, generated from every Matter's own records.

    Nobody maintains this page. It is the union of what the lawyers recorded on
    their Matters, and a commencement date appears here labelled `Jõustumine`
    while still living in exactly one table (Stage-2G brief 9, 47).
    """
    viewer = viewer_for(request)
    today = timezone.localdate()
    direction = request.GET.get("suund", selectors.UPCOMING)
    if direction not in dict(selectors.DIRECTIONS):
        direction = selectors.UPCOMING
    sources = request.GET.get("allikad", selectors.SOURCE_ALL)
    if sources not in dict(selectors.CALENDAR_SOURCES):
        sources = selectors.SOURCE_ALL
    year = _year_param(request)

    rows = selectors.calendar_rows(
        user=viewer, today=today, direction=direction, year=year, sources=sources
    )
    paginator = Paginator(rows, PAGE_SIZE)
    page = paginator.get_page(request.GET.get("leht"))
    entries = selectors.hydrate_calendar(list(page.object_list), viewer)

    base = {"suund": direction, "allikad": sources, "aasta": year}
    context = _shell(request, "tahtajad")
    context.update(
        {
            "page": page,
            "total": paginator.count,
            "sections": (
                sections.split_near_and_later(
                    entries,
                    today,
                    near_label="Järgmised 30 päeva",
                    later_label="Hiljem",
                )
                if direction == selectors.UPCOMING
                else sections.one_section("valitud", dict(selectors.DIRECTIONS)[direction], entries)
            ),
            "seis": _deadline_figures(viewer, today),
            "columns": DEADLINE_COLUMNS,
            "direction_options": filters.options(
                selectors.DIRECTIONS, parameter="suund", current=direction, base=base
            ),
            "source_options": filters.options(
                selectors.CALENDAR_SOURCES, parameter="allikad", current=sources, base=base
            ),
            "year_options": filters.year_options(
                selectors.important_date_years(viewer), current=year, base=base
            ),
            "context_label": dict(selectors.DIRECTIONS)[direction],
        }
    )
    return render(request, "intelligence/important_dates.html", context)


@dataclass(frozen=True)
class Figure:
    """One number on a Jälgimine strip, and the list it opens.

    Every figure here carries a destination, and the destination is the same
    query the number was counted with. A figure whose population this page
    cannot express is not printed at all — that is the rule, not a shortfall
    (01-EHITUSJUHIS §3.3, docs/design-v2-compatibility.md DS-16).
    """

    value: int
    caption: str
    url: str
    tone: str = ""


def _deadline_figures(viewer: Any, today: date) -> list[Figure]:
    """Olulised tähtajad: what is watched, what has passed, what is near.

    «30 päeva jooksul» points at the first section of this very page, because
    that section *is* the population it counts. A link into a list somebody can
    see without moving is still a link to exactly the rows the number came from.
    """
    horizon = today + timedelta(days=sections.NEAR_DAYS)
    # Read once. `calendar_rows` is a union, which Django will not let a caller
    # filter further, so the near window is counted in Python over the rows
    # already fetched rather than by asking the database a third time.
    ahead = list(selectors.calendar_rows(user=viewer, today=today, direction=selectors.UPCOMING))
    passed = selectors.calendar_rows(user=viewer, today=today, direction=selectors.PAST).count()
    near = sum(1 for row in ahead if row["date_value"] is not None and row["date_value"] <= horizon)
    upcoming = len(ahead)
    return [
        Figure(upcoming, "tähtaega jälgimisel", f"?suund={selectors.UPCOMING}"),
        Figure(passed, "üle tähtaja", f"?suund={selectors.PAST}", "danger"),
        Figure(near, "30 päeva jooksul", "#jalgimine-lahedal", "warning"),
    ]


# ---------------------------------------------------------------------------
# Jõustuvad aktid
# ---------------------------------------------------------------------------


@gate_required
def effective_dates(request: HttpRequest) -> HttpResponse:
    viewer = viewer_for(request)
    today = timezone.localdate()
    direction = request.GET.get("suund", selectors.HORIZON)
    if direction not in dict(selectors.EFFECTIVE_DIRECTIONS):
        direction = selectors.HORIZON
    year = _year_param(request)

    queryset = selectors.effective_dates(user=viewer, today=today, direction=direction, year=year)
    paginator = Paginator(queryset, PAGE_SIZE)
    page = paginator.get_page(request.GET.get("leht"))
    entries = [selectors.EffectiveDateEntry(record) for record in page.object_list]

    base = {"suund": direction, "aasta": year}
    context = _shell(request, "joustumised")
    context.update(
        {
            "page": page,
            "total": paginator.count,
            # The undated ones are grouped separately, never sorted into a
            # chronology they have no place on.
            "sections": _effective_sections(viewer, today, direction, entries),
            "seis": _effective_figures(viewer, today),
            "columns": EFFECTIVE_COLUMNS,
            "undated": list(page.object_list) if direction == selectors.UNDATED else [],
            "undated_count": selectors.undated_effective_count(viewer),
            "direction": direction,
            "undated_key": selectors.UNDATED,
            "direction_options": filters.options(
                selectors.EFFECTIVE_DIRECTIONS, parameter="suund", current=direction, base=base
            ),
            "year_options": filters.year_options(
                selectors.effective_date_years(viewer), current=year, base=base
            ),
            "horizon_end": selectors.horizon_end(today),
            "context_label": dict(selectors.EFFECTIVE_DIRECTIONS)[direction],
        }
    )
    return render(request, "intelligence/effective_dates.html", context)


def _effective_sections(
    viewer: Any, today: date, direction: str, entries: list[Any]
) -> list[sections.Section]:
    """«Jõustub varsti» and «Jõustunud», or the one window a filter asked for.

    The design's two sections are not two halves of one list: they are two
    directions in time, and the default view shows both because the question
    somebody opens this page with is «mis on tulemas ja mis on juba jõustunud»
    (02-EKRAANID §D). A reader who has asked for `Möödunud` or a year has
    already said which window they want, and splitting that answer again is the
    page arguing with them.
    """
    if direction == selectors.UNDATED:
        return []
    if direction != selectors.HORIZON:
        return sections.one_section(
            "valitud", dict(selectors.EFFECTIVE_DIRECTIONS)[direction], entries
        )

    horizon = today + timedelta(days=sections.SOON_DAYS)
    soon = [
        entry
        for entry in entries
        if entry.effective_date.date_value is not None
        and entry.effective_date.date_value <= horizon
    ]
    in_force = [
        selectors.EffectiveDateEntry(record)
        for record in selectors.effective_dates(
            user=viewer, today=today, direction=selectors.PAST
        ).filter(date_value__year=today.year)
    ]
    built: list[sections.Section] = []
    if soon:
        built.append(sections.Section(key="lahedal", label="Jõustub varsti", rows=soon))
    if in_force:
        built.append(sections.Section(key="joustunud", label="Jõustunud", rows=in_force))
    return built


def _effective_figures(viewer: Any, today: date) -> list[Figure]:
    """Jõustuvad aktid: what is being watched, what lands soon, what has landed.

    A commencement is **not** anybody's deadline. Nothing here creates a
    NextAction and nothing here appears in a «Järgmiseks» list: an act coming
    into force is a fact about the world, not an instruction to a lawyer
    (02-EKRAANID §D, 03-BACKEND §6).
    """
    horizon = today + timedelta(days=sections.SOON_DAYS)
    watched = selectors.effective_dates(
        user=viewer, today=today, direction=selectors.HORIZON
    ).count()
    soon = (
        selectors.effective_dates(user=viewer, today=today, direction=selectors.HORIZON)
        .filter(date_value__lte=horizon)
        .count()
    )
    in_force = (
        selectors.effective_dates(user=viewer, today=today, direction=selectors.PAST)
        .filter(date_value__year=today.year)
        .count()
    )
    return [
        Figure(watched, "jõustumist jälgimisel", f"?suund={selectors.HORIZON}"),
        Figure(
            soon, f"jõustub {sections.SOON_DAYS} päeva jooksul", "#jalgimine-lahedal", "warning"
        ),
        Figure(
            in_force,
            "jõustunud sel aastal",
            f"?suund={selectors.PAST}&aasta={today.year}",
        ),
    ]


# ---------------------------------------------------------------------------
# Töövõidud
# ---------------------------------------------------------------------------


@gate_required
def work_victories(request: HttpRequest) -> HttpResponse:
    """Claimed and confirmed work victories, filterable by period and state.

    A list, not a scoreboard. There is no win rate, no ministry ranking and no
    per-lawyer productivity figure, because none of them has a defensible
    denominator or an attribution model behind it (Stage-2G brief 40, 41).
    """
    viewer = viewer_for(request)
    status = request.GET.get("staatus", "")
    if status not in WorkVictoryStatus.values:
        status = ""
    year_param = request.GET.get("aasta", "").strip()
    year: str | int | None = None
    if year_param == selectors.UNKNOWN_PERIOD:
        year = selectors.UNKNOWN_PERIOD
    else:
        # Through the same reader as every other `?aasta=`, so a year outside
        # the supported range drops the filter here too rather than reaching
        # `period_date__year` and raising (CORR-02).
        year = year_from(year_param)

    queryset = selectors.work_victories(user=viewer, status=status, year=year)
    paginator = Paginator(queryset, PAGE_SIZE)
    page = paginator.get_page(request.GET.get("leht"))

    counts = selectors.work_victory_counts(viewer)
    base = {"staatus": status, "aasta": year}
    status_choices: tuple[tuple[str, str], ...] = (("", "Kõik"), *WorkVictoryStatus.choices)
    context = _shell(request, "toovoidud")
    context.update(
        {
            "page": page,
            "total": paginator.count,
            "sections": sections.by_year(
                list(page.object_list),
                period_of=lambda row: row.period_date.year if row.period_date else None,
                unknown_label="Teadmata periood",
            ),
            "seis": _victory_figures(viewer, timezone.localdate()),
            "columns": VICTORY_COLUMNS,
            "status": status,
            "status_options": [
                {**option, "count": counts.get(option["key"])}
                for option in filters.options(
                    status_choices, parameter="staatus", current=status, base=base
                )
            ],
            "year_options": filters.year_options(
                selectors.work_victory_years(viewer),
                current=year,
                base=base,
                extra=(
                    (selectors.UNKNOWN_PERIOD, "Teadmata periood")
                    if selectors.has_any_undated_victory(viewer)
                    else None
                ),
            ),
            "context_label": (
                dict(WorkVictoryStatus.choices)[status] if status else "Kõik töövõidud"
            ),
        }
    )
    return render(request, "intelligence/work_victories.html", context)


def _victory_figures(viewer: Any, today: date) -> list[Figure]:
    """Töövõidud: this year and this month, and nothing that ranks anybody.

    Counts of a stored state, each opening the same list it was counted from.
    No rate, no ministry league table, no per-lawyer figure — none of them has
    a defensible denominator or an attribution model behind it
    (master specification 3.5, Stage-2G brief 40, 41).
    """
    confirmed = selectors.work_victories(user=viewer, status=WorkVictoryStatus.CONFIRMED)
    claimed = selectors.work_victories(user=viewer, status=WorkVictoryStatus.CANDIDATE)
    return [
        Figure(
            confirmed.filter(period_date__year=today.year).count(),
            "töövõitu sel aastal",
            f"?staatus={WorkVictoryStatus.CONFIRMED}&aasta={today.year}",
        ),
        Figure(
            claimed.count(),
            dict(WorkVictoryStatus.choices)[WorkVictoryStatus.CANDIDATE].lower(),
            f"?staatus={WorkVictoryStatus.CANDIDATE}",
            "warning",
        ),
    ]


# ---------------------------------------------------------------------------
# Writing, from the Matter page
# ---------------------------------------------------------------------------


def _render_form(
    request: HttpRequest,
    matter: Matter,
    form: Any,
    *,
    heading: str,
    action: str,
    submit: str,
    status: int = 200,
    help_text: str = "",
) -> HttpResponse:
    return render(
        request,
        "intelligence/fact_form.html",
        {
            "matter": matter,
            "form": form,
            "heading": heading,
            "form_action": action,
            "submit_label": submit,
            "help_text": help_text,
            "nav_active": "teemad",
        },
        status=status,
    )


@login_required
@business_write_required
@require_http_methods(["GET", "POST"])
def add_important_date(request: HttpRequest, matter_id: Any) -> HttpResponse:
    matter = _matter_for(request, matter_id)
    action = reverse("intelligence:add_important_date", kwargs={"matter_id": matter.pk})

    if request.method == "POST":
        form = ImportantDateForm(request.POST)
        if form.is_valid():
            try:
                services.add_important_date(
                    matter=matter, actor=request.user, **form.as_service_kwargs()
                )
            except DomainError as error:
                form.add_error(None, str(error))
            else:
                messages.success(request, "Oluline tähtaeg lisatud.")
                return redirect(_matter_anchor(matter, "olulised-tahtajad"))
        return _render_form(
            request,
            matter,
            form,
            heading="Lisa oluline tähtaeg",
            action=action,
            submit="Salvesta",
            status=400,
        )

    return _render_form(
        request,
        matter,
        ImportantDateForm(),
        heading="Lisa oluline tähtaeg",
        action=action,
        submit="Salvesta",
        help_text=(
            "Oodatav sündmus, mida osakond jälgib. Kui täpne kuupäev ei ole teada, "
            "vali kvartal, poolaasta või aasta — süsteem ei tekita päeva juurde."
        ),
    )


@login_required
@business_write_required
@require_http_methods(["GET", "POST"])
def edit_important_date(request: HttpRequest, matter_id: Any, pk: Any) -> HttpResponse:
    matter = _matter_for(request, matter_id)
    record = get_object_or_404(
        MatterImportantDate.objects.visible_to(request.user), pk=pk, matter=matter
    )
    action = reverse(
        "intelligence:edit_important_date", kwargs={"matter_id": matter.pk, "pk": record.pk}
    )

    if request.method == "POST":
        form = ImportantDateForm(request.POST)
        if form.is_valid():
            try:
                services.update_important_date(
                    record=record, actor=request.user, **form.as_service_kwargs()
                )
            except DomainError as error:
                form.add_error(None, str(error))
            else:
                messages.success(request, "Oluline tähtaeg muudetud.")
                return redirect(_matter_anchor(matter, "olulised-tahtajad"))
        return _render_form(
            request,
            matter,
            form,
            heading="Muuda olulist tähtaega",
            action=action,
            submit="Salvesta",
            status=400,
        )

    return _render_form(
        request,
        matter,
        ImportantDateForm.from_record(record),
        heading="Muuda olulist tähtaega",
        action=action,
        submit="Salvesta",
        help_text="Varasem väärtus jääb muudatuste ajalukku.",
    )


@login_required
@business_write_required
@require_http_methods(["GET", "POST"])
def cancel_important_date(request: HttpRequest, matter_id: Any, pk: Any) -> HttpResponse:
    matter = _matter_for(request, matter_id)
    record = get_object_or_404(
        MatterImportantDate.objects.visible_to(request.user), pk=pk, matter=matter
    )
    action = reverse(
        "intelligence:cancel_important_date", kwargs={"matter_id": matter.pk, "pk": record.pk}
    )

    if request.method == "POST":
        form = ReasonForm(request.POST)
        if form.is_valid():
            try:
                services.cancel_important_date(
                    record=record,
                    actor=request.user,
                    reason=form.cleaned_data.get("reason") or "",
                )
            except DomainError as error:
                form.add_error(None, str(error))
            else:
                messages.success(request, "Oluline tähtaeg märgitud tühistatuks.")
                return redirect(_matter_anchor(matter, "olulised-tahtajad"))
        return _render_form(
            request,
            matter,
            form,
            heading=f"Tühista tähtaeg: {record.display_date}",
            action=action,
            submit="Tühista tähtaeg",
            status=400,
        )

    return _render_form(
        request,
        matter,
        ReasonForm(),
        heading=f"Tühista tähtaeg: {record.display_date}",
        action=action,
        submit="Tühista tähtaeg",
        help_text=(
            "Kirje ei kustu. See jääb teemale nähtavaks tühistatuna, sest ka ärajäänud "
            "ootus on osa teema ajaloost."
        ),
    )


@login_required
@business_write_required
@require_http_methods(["GET", "POST"])
def add_effective_date(request: HttpRequest, matter_id: Any) -> HttpResponse:
    matter = _matter_for(request, matter_id)
    action = reverse("intelligence:add_effective_date", kwargs={"matter_id": matter.pk})

    if request.method == "POST":
        form = EffectiveDateForm(request.POST)
        if form.is_valid():
            try:
                services.add_effective_date(
                    matter=matter, actor=request.user, **form.as_service_kwargs()
                )
            except DomainError as error:
                form.add_error(None, str(error))
            else:
                messages.success(request, "Jõustumine lisatud.")
                return redirect(_matter_anchor(matter, "joustumine"))
        return _render_form(
            request,
            matter,
            form,
            heading="Lisa jõustumine",
            action=action,
            submit="Salvesta",
            status=400,
        )

    return _render_form(
        request,
        matter,
        EffectiveDateForm(),
        heading="Lisa jõustumine",
        action=action,
        submit="Salvesta",
        help_text=(
            "Ühel teemal võib olla mitu jõustumist — põhiosa ja hiljem jõustuvad sätted "
            "on eraldi kirjed. Kui kuupäev ei ole veel teada, vali see liik; "
            "kohatäite kuupäeva ei salvestata."
        ),
    )


@login_required
@business_write_required
@require_http_methods(["GET", "POST"])
def edit_effective_date(request: HttpRequest, matter_id: Any, pk: Any) -> HttpResponse:
    matter = _matter_for(request, matter_id)
    record = get_object_or_404(
        MatterEffectiveDate.objects.visible_to(request.user), pk=pk, matter=matter
    )
    action = reverse(
        "intelligence:edit_effective_date", kwargs={"matter_id": matter.pk, "pk": record.pk}
    )

    if request.method == "POST":
        form = EffectiveDateForm(request.POST)
        if form.is_valid():
            try:
                services.update_effective_date(
                    record=record, actor=request.user, **form.as_service_kwargs()
                )
            except DomainError as error:
                form.add_error(None, str(error))
            else:
                messages.success(request, "Jõustumine muudetud.")
                return redirect(_matter_anchor(matter, "joustumine"))
        return _render_form(
            request,
            matter,
            form,
            heading="Muuda jõustumist",
            action=action,
            submit="Salvesta",
            status=400,
        )

    return _render_form(
        request,
        matter,
        EffectiveDateForm.from_record(record),
        heading="Muuda jõustumist",
        action=action,
        submit="Salvesta",
        help_text="Muudatus liigutab kirjet ka Jõustuvate aktide lehel — teist loendit ei ole.",
    )


@login_required
@business_write_required
@require_http_methods(["GET", "POST"])
def cancel_effective_date(request: HttpRequest, matter_id: Any, pk: Any) -> HttpResponse:
    matter = _matter_for(request, matter_id)
    record = get_object_or_404(
        MatterEffectiveDate.objects.visible_to(request.user), pk=pk, matter=matter
    )
    action = reverse(
        "intelligence:cancel_effective_date", kwargs={"matter_id": matter.pk, "pk": record.pk}
    )

    if request.method == "POST":
        form = ReasonForm(request.POST)
        if form.is_valid():
            try:
                services.cancel_effective_date(
                    record=record,
                    actor=request.user,
                    reason=form.cleaned_data.get("reason") or "",
                )
            except DomainError as error:
                form.add_error(None, str(error))
            else:
                messages.success(request, "Jõustumine märgitud tühistatuks.")
                return redirect(_matter_anchor(matter, "joustumine"))
        return _render_form(
            request,
            matter,
            form,
            heading=f"Tühista jõustumine: {record.display_when}",
            action=action,
            submit="Tühista jõustumine",
            status=400,
        )

    return _render_form(
        request,
        matter,
        ReasonForm(),
        heading=f"Tühista jõustumine: {record.display_when}",
        action=action,
        submit="Tühista jõustumine",
        help_text="Kirje jääb teemale nähtavaks tühistatuna.",
    )


@login_required
@business_write_required
@require_http_methods(["GET", "POST"])
def add_work_victory(request: HttpRequest, matter_id: Any) -> HttpResponse:
    """A colleague writes down a work victory, and it is one.

    The person filling this in has already made the judgement — they opened a
    Matter they may write to and stated that Koda achieved something. Saving
    that as a candidate for somebody else to approve asked them to seek
    agreement with a decision they had just made, and left every manual entry
    unconfirmed until it arrived.

    ``may_review_work_victory`` still gates the *review* of a machine or
    imported candidate, which is a judgement about somebody else's proposal.
    """
    matter = _matter_for(request, matter_id)
    action = reverse("intelligence:add_work_victory", kwargs={"matter_id": matter.pk})

    if request.method == "POST":
        form = WorkVictoryForm(request.POST)
        if form.is_valid():
            try:
                services.add_confirmed_work_victory(
                    matter=matter, actor=request.user, **form.as_service_kwargs()
                )
            except DomainError as error:
                form.add_error(None, str(error))
            else:
                messages.success(request, "Töövõit lisatud.")
                return redirect(_matter_anchor(matter, "toovoidud"))
        return _render_form(
            request,
            matter,
            form,
            heading="Lisa töövõit",
            action=action,
            submit="Salvesta töövõit",
            status=400,
        )

    return _render_form(
        request,
        matter,
        WorkVictoryForm(),
        heading="Lisa töövõit",
        action=action,
        submit="Salvesta töövõit",
        help_text=(
            "Kirje salvestub kinnitatud töövõiduna sinu nimel. Eraldi kinnitamist ei ole vaja."
        ),
    )


@login_required
@business_write_required
@require_http_methods(["GET", "POST"])
def edit_work_victory(request: HttpRequest, matter_id: Any, pk: Any) -> HttpResponse:
    matter = _matter_for(request, matter_id)
    record = get_object_or_404(
        MatterWorkVictory.objects.visible_to(request.user), pk=pk, matter=matter
    )
    action = reverse(
        "intelligence:edit_work_victory", kwargs={"matter_id": matter.pk, "pk": record.pk}
    )

    if request.method == "POST":
        form = WorkVictoryForm(request.POST)
        if form.is_valid():
            try:
                services.update_work_victory(
                    record=record, actor=request.user, **form.as_service_kwargs()
                )
            except DomainError as error:
                form.add_error(None, str(error))
            else:
                messages.success(request, "Töövõidu kirje muudetud.")
                return redirect(_matter_anchor(matter, "toovoidud"))
        return _render_form(
            request,
            matter,
            form,
            heading="Muuda töövõidu kirjet",
            action=action,
            submit="Salvesta",
            status=400,
        )

    return _render_form(
        request,
        matter,
        WorkVictoryForm.from_record(record),
        heading="Muuda töövõidu kirjet",
        action=action,
        submit="Salvesta",
        help_text="Sõnastuse muutmine ei muuda kirje seisu.",
    )


@login_required
@require_http_methods(["GET", "POST"])
def confirm_work_victory(request: HttpRequest, matter_id: Any, pk: Any) -> HttpResponse:
    """Deliberate, and department-head only.

    A confirmed work victory is the Chamber's own claim about its influence, so
    the decision belongs with the person answerable for it — and it is a
    separate act from editing the wording, never a side effect of one
    (Stage-2G brief 25, 53).
    """
    matter = _matter_for(request, matter_id)
    if not may_review_work_victory(request.user):
        raise PermissionDenied("Töövõitu saab kinnitada ainult osakonnajuht.")
    record = get_object_or_404(
        MatterWorkVictory.objects.visible_to(request.user), pk=pk, matter=matter
    )

    if request.method == "POST":
        try:
            services.confirm_work_victory(record=record, actor=request.user)
        except DomainError as error:
            messages.error(request, str(error))
        else:
            messages.success(request, "Töövõit kinnitatud.")
        return redirect(_matter_anchor(matter, "toovoidud"))

    return render(
        request,
        "intelligence/confirm_work_victory.html",
        {
            "matter": matter,
            "record": record,
            "nav_active": "teemad",
            "form_action": reverse(
                "intelligence:confirm_work_victory",
                kwargs={"matter_id": matter.pk, "pk": record.pk},
            ),
        },
    )


@login_required
@require_http_methods(["GET", "POST"])
def reject_work_victory(request: HttpRequest, matter_id: Any, pk: Any) -> HttpResponse:
    matter = _matter_for(request, matter_id)
    if not may_review_work_victory(request.user):
        raise PermissionDenied("Töövõidu otsuse saab teha ainult osakonnajuht.")
    record = get_object_or_404(
        MatterWorkVictory.objects.visible_to(request.user), pk=pk, matter=matter
    )
    action = reverse(
        "intelligence:reject_work_victory", kwargs={"matter_id": matter.pk, "pk": record.pk}
    )

    if request.method == "POST":
        form = ReasonForm(request.POST)
        if form.is_valid():
            try:
                services.reject_work_victory(
                    record=record,
                    actor=request.user,
                    reason=form.cleaned_data.get("reason") or "",
                )
            except DomainError as error:
                form.add_error(None, str(error))
            else:
                messages.success(request, "Märgitud mitterealiseerunuks.")
                return redirect(_matter_anchor(matter, "toovoidud"))
        return _render_form(
            request,
            matter,
            form,
            heading="Märgi mitterealiseerunuks",
            action=action,
            submit="Märgi mitterealiseerunuks",
            status=400,
        )

    return _render_form(
        request,
        matter,
        ReasonForm(),
        heading="Märgi mitterealiseerunuks",
        action=action,
        submit="Märgi mitterealiseerunuks",
        help_text="Kirje jääb alles. Osakond näeb, mida loodeti ja mis ei õnnestunud.",
    )
