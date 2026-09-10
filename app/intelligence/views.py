"""The write surfaces for the three structured Matter facts.

There were three generated department views here as well — Olulised tähtajad,
Jõustuvad aktid and Töövõidud — and they are gone as destinations. A Teema is
found in one place now: `Töövõit` and `Jõustumine` are structured filters on the
register, and an `Oluline tähtaeg` is the Matter owner's own upcoming work in
Minu asjad. Nothing about the facts themselves changed — same models, same rows,
same history, same forms below — only where a reader consumes them
(docs/adr/0067). ``app.intelligence.urls`` answers every old address.

Writes are ``@login_required`` **and** check a business role, because the
shared-gate sentinel is not a person and must never become an audit actor
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
the work is about (docs/adr/0065, superseding Stage-2G brief 74).

Correcting and cancelling an existing record are still full-page POSTs that
redirect back to the section anchor.
"""

from __future__ import annotations

from typing import Any

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_http_methods

from app.core.authorization import may_review_work_victory, may_write_business_content
from app.core.decorators import business_write_required
from app.core.errors import DomainError
from app.intelligence import selectors, services
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


def _matter_for(request: HttpRequest, matter_id: Any) -> Matter:
    return get_object_or_404(Matter.objects.visible_to(request.user), pk=matter_id)


def _matter_anchor(matter: Matter, anchor: str) -> str:
    return f"{reverse('matters:matter_detail', kwargs={'pk': matter.pk})}#{anchor}"


# ---------------------------------------------------------------------------
# The three reading pages, and where they went
# ---------------------------------------------------------------------------
#
# `important_dates`, `effective_dates` and `work_victories` used to live here:
# three generated department-wide lists behind one navigation item, each
# showing the Matters that carry one structured fact.
#
# They are gone as destinations. Discovery of a Teema happens in one place now
# — the register — so `Töövõit` and `Jõustumine` are structured filters on
# Teemad, and an `Oluline tähtaeg` is the Matter owner's own upcoming work and
# appears in Minu asjad, which it already did (docs/adr/0067).
#
# Nothing about the *facts* moved. The models, the rows, their history and the
# write surfaces below are untouched; what moved is where a reader consumes
# them. The old addresses are answered by `app.intelligence.urls`, which sends
# each one to the destination that now answers its question.


# ---------------------------------------------------------------------------
# Writing, from the Matter page
# ---------------------------------------------------------------------------


#: Which add form the Matter page has open, as the fact block's own template
#: reads it. Two values and an empty one, because only one may be open at a
#: time and "none" is the ordinary state (docs/adr/0065).
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
    would be a second, weaker gate (docs/adr/0065).
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
    gets the page and the redirect it always got (docs/adr/0065).
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
    (docs/adr/0065).
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
