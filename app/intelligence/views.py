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

Adding a `Jõustumine` or a `Töövõit` opens inline on the Matter. The same route
answers both shapes: an ordinary request gets the standalone page it always got,
and an HTMX request gets :func:`_facts_fragment` — this app's own fact block,
built from this app's own selector.

That is not the coupling ADR 0018 refused. What it refused was reaching into
``app.matters.views``' context builders to swap a fragment of the Matter
overview; nothing here reads them, and the Matter view still renders this block
by including the same partial. What changed is that a fact section may
re-render *itself* after a write, which is what keeps ordinary work on the page
the work is about (docs/adr/0063, superseding Stage-2G brief 74).

Correcting and cancelling an existing record are still full-page POSTs that
redirect back to the section anchor.
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

from app.core.authorization import may_review_work_victory, may_write_business_content
from app.core.decorators import business_write_required, gate_required, viewer_for
from app.core.errors import DomainError
from app.intelligence import filters, sections, selectors, services
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
    entries = selectors.hydrate_calendar(list(page.object_list), viewer, today)

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
    """Töövõidud, a year at a time.

    One concept, not two. *Töövõidu kandidaat* and *kinnitatud töövõit* stopped
    being a distinction a reader makes when the manual door started recording a
    work victory as one (:func:`add_work_victory`), so this page offers no
    state filter and shows the population that is a work victory. ``?staatus=``
    is read by nothing here: an old link still opens this page, and it opens the
    same page everyone else sees.

    The internal states are untouched — a machine's or an import's proposal is
    still a proposal, and still reaches nobody's Töövõidud until a person
    decides it is one (app/intelligence/selectors.py::VISIBLE_VICTORY_STATUS).

    A list, not a scoreboard. There is no win rate, no ministry ranking and no
    per-lawyer productivity figure, because none of them has a defensible
    denominator or an attribution model behind it (Stage-2G brief 40, 41).
    """
    viewer = viewer_for(request)
    year_param = request.GET.get("aasta", "").strip()
    year: str | int | None = None
    if year_param == selectors.UNKNOWN_PERIOD:
        year = selectors.UNKNOWN_PERIOD
    else:
        # Through the same reader as every other `?aasta=`, so a year outside
        # the supported range drops the filter here too rather than reaching
        # `period_date__year` and raising (CORR-02).
        year = year_from(year_param)

    visible = selectors.VISIBLE_VICTORY_STATUS
    queryset = selectors.work_victories(user=viewer, status=visible, year=year)
    paginator = Paginator(queryset, PAGE_SIZE)
    page = paginator.get_page(request.GET.get("leht"))

    base = {"aasta": year}
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
            "year_options": filters.year_options(
                selectors.work_victory_years(viewer, status=visible),
                current=year,
                base=base,
                extra=(
                    (selectors.UNKNOWN_PERIOD, "Teadmata periood")
                    if selectors.has_any_undated_victory(viewer, status=visible)
                    else None
                ),
            ),
        }
    )
    return render(request, "intelligence/work_victories.html", context)


def _victory_figures(viewer: Any, today: date) -> list[Figure]:
    """One figure: this year's work victories, opening this year's list.

    The second figure counted *kandidaate* — a review queue this page no longer
    has, on a surface where the distinction stopped existing. It is not replaced
    by another number: a figure invented to keep a strip symmetrical is a metric
    nobody asked for, and one honest count is worth more than two.

    No rate, no ministry league table, no per-lawyer figure — none of them has
    a defensible denominator or an attribution model behind it
    (master specification 3.5, Stage-2G brief 40, 41).
    """
    victories = selectors.work_victories(user=viewer, status=selectors.VISIBLE_VICTORY_STATUS)
    return [
        Figure(
            victories.filter(period_date__year=today.year).count(),
            "töövõitu sel aastal",
            f"?aasta={today.year}",
        ),
    ]


# ---------------------------------------------------------------------------
# Writing, from the Matter page
# ---------------------------------------------------------------------------


#: Which add form the Matter page has open, as the fact block's own template
#: reads it. Two values and an empty one, because only one may be open at a
#: time and "none" is the ordinary state (docs/adr/0063).
INLINE_EFFECTIVE_DATE = "joustumine"
INLINE_WORK_VICTORY = "toovoit"

#: ``?vorm=sulge`` — «give me the block with nothing open». Closing writes
#: nothing, so it is a GET, and it is this route rather than a route of its own
#: because a second address would be a second thing to authorize.
FORM_CLOSED = "sulge"

#: How a field's control is named when the form is rendered inside the Matter
#: page rather than on one of its own.
#:
#: Django calls a control `id_<field>`, and the Matter page already carries a
#: composer with a `kind` field on it. Two elements with one id is not a style
#: question: a `<label for>` binds to the *first* of them, so the commencement
#: form's «Jõustumine» radios would have addressed the composer's entry-kind
#: select, and a browser test could not have told them apart either.
#: `app.submissions.forms` takes the same precaution for the three forms
#: Dokumendid renders side by side.
#:
#: Ids only. The field *names* are untouched, so the inline form and the
#: standalone page post exactly the same request to exactly the same view.
INLINE_AUTO_ID = "teemavorm_%s"

#: The words each add surface carries, said once. The standalone page and the
#: inline accordion are two renderings of one form, and a heading that differed
#: between them would be two forms wearing one name.
EFFECTIVE_DATE_HEADING = "Lisa jõustumine"
EFFECTIVE_DATE_SUBMIT = "Salvesta"
EFFECTIVE_DATE_HELP = (
    "Ühel teemal võib olla mitu jõustumist — põhiosa ja hiljem jõustuvad sätted "
    "on eraldi kirjed. Kui kuupäev ei ole veel teada, vali see liik; "
    "kohatäite kuupäeva ei salvestata."
)
WORK_VICTORY_HEADING = "Lisa töövõit"
WORK_VICTORY_SUBMIT = "Salvesta töövõit"
WORK_VICTORY_HELP = "Kirje lisatakse töövõiduna sinu nimel."


def _inline(request: HttpRequest) -> bool:
    """Whether this request wants the fact block rather than a page.

    The HTMX header and nothing else. A query parameter would let a link
    somebody pasted into a browser produce a bare fragment with no shell around
    it, and the standalone page is precisely what that reader should get.
    """
    return request.headers.get("HX-Request") == "true"


def _facts_fragment(
    request: HttpRequest,
    matter: Matter,
    *,
    fact: str = "",
    form: Any = None,
    heading: str = "",
    submit: str = "",
    help_text: str = "",
    action: str = "",
    status: int = 200,
) -> HttpResponse:
    """The Matter's structured facts, with at most one add form open.

    Read here rather than taken from the Matter view: ``matter_intelligence``
    is this app's selector and scopes every child record to this reader, which
    is the same read the Matter page makes and the only read this needs. The
    two authorization answers the block renders controls from are asked again
    for the same reason — a fragment that trusted what a previous page decided
    would be a second, weaker gate (docs/adr/0063).
    """
    return render(
        request,
        "intelligence/partials/matter_facts.html",
        {
            "matter": matter,
            "intelligence": selectors.matter_intelligence(matter, request.user),
            "can_write": may_write_business_content(request.user),
            "can_review_victory": may_review_work_victory(request.user),
            "inline_fact": fact,
            "inline_form": form,
            "inline_heading": heading,
            "inline_submit": submit,
            "inline_help": help_text,
            "inline_action": action,
        },
        status=status,
    )


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
    """`Jõustumine`, inline on the Matter or on this route's own page.

    One route, one form, one service, two renderings. The inline branch is
    reached only by an HTMX request, so every existing caller — a bookmark, a
    deep link, a browser with scripting off, the write-boundary suite — still
    gets the page and the redirect it always got (docs/adr/0063).
    """
    matter = _matter_for(request, matter_id)
    action = reverse("intelligence:add_effective_date", kwargs={"matter_id": matter.pk})
    inline = _inline(request)
    ids: dict[str, Any] = {"auto_id": INLINE_AUTO_ID} if inline else {}

    def open_inline(form: Any, *, status: int = 200) -> HttpResponse:
        return _facts_fragment(
            request,
            matter,
            fact=INLINE_EFFECTIVE_DATE,
            form=form,
            heading=EFFECTIVE_DATE_HEADING,
            submit=EFFECTIVE_DATE_SUBMIT,
            help_text=EFFECTIVE_DATE_HELP,
            action=action,
            status=status,
        )

    if request.method == "POST":
        form = EffectiveDateForm(request.POST, **ids)
        if form.is_valid():
            try:
                services.add_effective_date(
                    matter=matter, actor=request.user, **form.as_service_kwargs()
                )
            except DomainError as error:
                form.add_error(None, str(error))
            else:
                if inline:
                    # No flash message: the record is on the screen the reader
                    # is already looking at, and a banner queued here would
                    # surface on whatever page they opened next.
                    return _facts_fragment(request, matter)
                messages.success(request, "Jõustumine lisatud.")
                return redirect(_matter_anchor(matter, "joustumine"))
        # Refused. The form comes back where it was, with what was typed still
        # in it — 400, which the page's own HTMX configuration swaps
        # (static/js/app.js).
        if inline:
            return open_inline(form, status=400)
        return _render_form(
            request,
            matter,
            form,
            heading=EFFECTIVE_DATE_HEADING,
            action=action,
            submit=EFFECTIVE_DATE_SUBMIT,
            status=400,
        )

    if inline:
        if request.GET.get("vorm") == FORM_CLOSED:
            return _facts_fragment(request, matter)
        return open_inline(EffectiveDateForm(**ids))

    return _render_form(
        request,
        matter,
        EffectiveDateForm(),
        heading=EFFECTIVE_DATE_HEADING,
        action=action,
        submit=EFFECTIVE_DATE_SUBMIT,
        help_text=EFFECTIVE_DATE_HELP,
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

    Like `Jõustumine`, this opens inline on the Matter for an HTMX request and
    keeps its own page for everybody else. What a work victory *is* did not
    move: the same form, the same service, the same confirmed state
    (docs/adr/0063).
    """
    matter = _matter_for(request, matter_id)
    action = reverse("intelligence:add_work_victory", kwargs={"matter_id": matter.pk})
    inline = _inline(request)
    ids: dict[str, Any] = {"auto_id": INLINE_AUTO_ID} if inline else {}

    def open_inline(form: Any, *, status: int = 200) -> HttpResponse:
        return _facts_fragment(
            request,
            matter,
            fact=INLINE_WORK_VICTORY,
            form=form,
            heading=WORK_VICTORY_HEADING,
            submit=WORK_VICTORY_SUBMIT,
            help_text=WORK_VICTORY_HELP,
            action=action,
            status=status,
        )

    if request.method == "POST":
        form = WorkVictoryForm(request.POST, **ids)
        if form.is_valid():
            try:
                services.add_confirmed_work_victory(
                    matter=matter, actor=request.user, **form.as_service_kwargs()
                )
            except DomainError as error:
                form.add_error(None, str(error))
            else:
                if inline:
                    return _facts_fragment(request, matter)
                messages.success(request, "Töövõit lisatud.")
                return redirect(_matter_anchor(matter, "toovoidud"))
        if inline:
            return open_inline(form, status=400)
        return _render_form(
            request,
            matter,
            form,
            heading=WORK_VICTORY_HEADING,
            action=action,
            submit=WORK_VICTORY_SUBMIT,
            status=400,
        )

    if inline:
        if request.GET.get("vorm") == FORM_CLOSED:
            return _facts_fragment(request, matter)
        return open_inline(WorkVictoryForm(**ids))

    return _render_form(
        request,
        matter,
        WorkVictoryForm(),
        heading=WORK_VICTORY_HEADING,
        action=action,
        submit=WORK_VICTORY_SUBMIT,
        help_text=WORK_VICTORY_HELP,
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
