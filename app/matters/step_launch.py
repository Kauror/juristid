"""A structured next step opens its canonical form (docs/adr/0168).

`Järgmisena?` suggests the Matter's next standard step, and `PRAEGUNE TEGEVUS`
lists the waits and the current step. Where a step's work is a record this
application already has a form for, the step's own control opens **that form**
under `LISA TEEMALE` rather than a simplified one of its own:

* «Kaasa liikmeid / küsi tagasisidet» → `+ Kaasamine · Alusta kaasamist`;
* «Saada Koja arvamus» → `+ Koja arvamus / kohtumine · Koja arvamus`;
* «Koosta kodulehe ülevaade» → `+ Ülevaade / uudis`;
* «Ootame tagasisidet» → `+ Kaasamine · Lisa tagasiside`, with the round chosen.

**Opening writes nothing.** The control is a link: with scripting it opens the
section, scrolls to it and focuses the first field (`static/js/ux.js`
`data-open-canonical`); without scripting it is the Teema page with
``?ava=<form>`` (and ``samm`` / ``kaasamine``), which opens the same section on
the server. Only the form's own `Salvesta` writes, through the form's own view
and service, every rule of which is unchanged.

**The step that opened the form is the one it finishes.** A form opened from a
step carries the step's id as ``samm``. The view checks it under the Matter's
lock before the record is written (`app.workflow.plan.launched_step`: on this
Matter, of this form's operation, still ahead) and, in the same transaction,
marks that step done by the record (`fulfil_launched_step`). A second press
finds the step done and is refused with nothing written. A `Lisa tagasiside`
names its round by the form's own `Seotud kaasamine` field, which already is the link.

That sequence — lock the step, save the record, fulfil the step, in one
transaction — is :func:`launch`, the one helper every canonical form's view
calls (JARCH-001), so a fourth form cannot get the order wrong.

Apart from :func:`launch`, this module only maps steps to forms and reads the
two request shapes; it decides nothing about the records themselves.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol

from django.db import transaction
from django.http import Http404, HttpRequest
from django.shortcuts import get_object_or_404

from app.workflow import plan as work_plan
from app.workflow.enums import PlanStepOperation
from app.workflow.models import MatterPlanStep

#: The request parameter that carries the originating step, GET and POST.
STEP_PARAM = "samm"
#: The request parameter that names which canonical form to open (GET only).
OPEN_PARAM = "ava"
#: The round a `Lisa tagasiside` is opened for (GET only).
ROUND_PARAM = "kaasamine"


@dataclass(frozen=True)
class CanonicalForm:
    """One `LISA TEEMALE` form a structured step is done through."""

    #: ``?ava=`` value.
    slug: str
    #: The `workspace_forms` key, which is also the refusal key.
    form_key: str
    #: The launcher chip's panel, and the sub-choice inside it ("" for none).
    family: str
    choice: str
    #: The step operation it finishes, or "" for the feedback form.
    operation: str = ""

    @property
    def panel(self) -> str:
        """The element that holds the form: the anchor a link jumps to."""
        return self.choice or self.family


ENGAGEMENT_FORM = CanonicalForm(
    slug="kaasamine",
    form_key="add_engagement_form",
    family="lisa-kaasamine",
    choice="kaasamine-alusta",
    operation=PlanStepOperation.ENGAGEMENT,
)
OPINION_FORM = CanonicalForm(
    slug="koja-arvamus",
    form_key="koda_opinion_form",
    family="lisa-arvamus",
    choice="arvamus-koja",
    operation=PlanStepOperation.SUBMISSION,
)
OVERVIEW_FORM = CanonicalForm(
    slug="ulevaade",
    form_key="website_overview_form",
    family="lisa-koduleht",
    choice="",
    operation=PlanStepOperation.WEBSITE_OVERVIEW,
)
FEEDBACK_FORM = CanonicalForm(
    slug="tagasiside",
    form_key="engagement_reply_form",
    family="lisa-kaasamine",
    choice="kaasamine-tagasiside",
)

#: The structured operations with a canonical form, by operation.
FORMS_BY_OPERATION: dict[str, CanonicalForm] = {
    form.operation: form for form in (ENGAGEMENT_FORM, OPINION_FORM, OVERVIEW_FORM)
}
FORMS_BY_SLUG: dict[str, CanonicalForm] = {
    form.slug: form for form in (ENGAGEMENT_FORM, OPINION_FORM, OVERVIEW_FORM, FEEDBACK_FORM)
}


@dataclass(frozen=True)
class Launch:
    """A step's control that opens its canonical form, as a template draws it."""

    form: CanonicalForm
    step: MatterPlanStep

    @property
    def query(self) -> str:
        """The no-script address: the page, the form opened, the step named."""
        return f"?{OPEN_PARAM}={self.form.slug}&{STEP_PARAM}={self.step.pk}#{self.form.panel}"


def launch_for_step(step: MatterPlanStep | None) -> Launch | None:
    """The canonical form ``step`` is done through, or ``None`` for a generic step."""
    if step is None:
        return None
    form = FORMS_BY_OPERATION.get(step.operation)
    return Launch(form=form, step=step) if form is not None else None


def _uuid_or_none(raw: Any) -> uuid.UUID | None:
    try:
        return uuid.UUID(str(raw))
    except (TypeError, ValueError, AttributeError):
        return None


def posted_step_id(request: HttpRequest, matter: Any) -> Any:
    """The originating step a canonical form's POST names, or ``None``.

    Empty is the ordinary case: the form was opened from its chip. A value
    that is not a step of **this** Matter answers 404 — never a step found by
    id alone (AUTH-003). Whether it may still be finished, and by this form, is
    asked under the lock by `app.workflow.plan.launched_step`.
    """
    raw = (request.POST.get(STEP_PARAM) or "").strip()
    if not raw:
        return None
    step_id = _uuid_or_none(raw)
    if step_id is None:
        raise Http404
    return get_object_or_404(MatterPlanStep.objects.only("pk"), pk=step_id, matter=matter).pk


class _SavedRecord(Protocol):
    """What a canonical form's save returns: at least the record it wrote."""

    @property
    def record(self) -> Any: ...


def launch[Saved: _SavedRecord](
    *,
    matter: Any,
    step_id: Any,
    operation: str,
    actor: Any,
    save: Callable[[], Saved],
    fulfils: Callable[[Any], bool] | None = None,
) -> Saved:
    """Save a canonical form's record and finish the step that opened it (docs/adr/0168 §3).

    One transaction, in this order:

    1. **the step**, when the form names one (``step_id`` from
       :func:`posted_step_id`): the Matter locked, then the step checked under
       that lock — on this Matter, of ``operation``, still ahead
       (`app.workflow.plan.lock_launched_step`). A refusal writes nothing. No
       step named is the ordinary case — the form was opened from its own
       chip — and nothing about any step changes;
    2. **the record** — ``save()``, the form's own canonical service, every
       rule of which is unchanged. Called here rather than before, so its
       arguments are evaluated inside this transaction: a typed institution
       resolved while they are, or a round named, is rolled back with any
       refusal;
    3. **the step done by that record** (`fulfil_launched_step`), unless
       ``fulfils`` says the record is not the work the step asked for — a news
       item does not finish «Koosta kodulehe ülevaade»
       (`publication_kind.overview_fulfils_step`).

    A second press of a form opened from a step finds the step done at 1 and is
    refused with nothing written. Returns what ``save`` returned.
    """
    with transaction.atomic():
        launched = work_plan.lock_launched_step(matter, step_id, operation)
        result = save()
        if launched is not None and (fulfils is None or fulfils(result.record)):
            work_plan.fulfil_launched_step(launched=launched, record=result.record, actor=actor)
    return result


def _open_step(matter: Any, raw: Any, form: CanonicalForm) -> MatterPlanStep | None:
    """The step a launch names, when it is this Matter's, of this form and still ahead."""
    step_id = _uuid_or_none(raw)
    if step_id is None or not form.operation:
        return None
    step = MatterPlanStep.objects.filter(matter=matter, pk=step_id).first()
    if step is None or step.operation != form.operation or not step.is_open:
        return None
    return step


def launched_context(matter: Any, form_key: str, raw_step: Any) -> dict[str, Any] | None:
    """What a refused save carries back: the form still names the step it came from."""
    form = next((form for form in FORMS_BY_OPERATION.values() if form.form_key == form_key), None)
    if form is None:
        return None
    step = _open_step(matter, raw_step, form)
    return {"form": form.form_key, "step": step} if step is not None else None


def apply_open_request(request: HttpRequest, matter: Any, context: dict[str, Any]) -> None:
    """``?ava=…`` — open the named canonical form on a full page load.

    The no-script half of every step control above: the same section the
    script opens, chosen on the server. Unknown values, a step that is not
    this Matter's or no longer ahead, and a round that is not open for this
    reader are ignored — the page is simply drawn as usual. Nothing is written.
    """
    form = FORMS_BY_SLUG.get(request.GET.get(OPEN_PARAM) or "")
    if form is None or not context.get("can_write") or not matter.is_open:
        return
    context["open_panel"] = form.family
    context["open_choice"] = form.choice
    if form.choice:
        family = "kaasamine_choice" if form.family == "lisa-kaasamine" else "arvamus_choice"
        context[family] = form.choice
    if form is FEEDBACK_FORM:
        wanted = _uuid_or_none(request.GET.get(ROUND_PARAM))
        rounds = context.get("feedback_waits") or []
        if wanted is not None and any(round_.pk == wanted for round_ in rounds):
            from app.matters.forms import FeedbackForm

            # The page's own institution reading, handed on: the form drawn for
            # the ordinary page has already read the catalogue, the ranking and
            # the spellings, and reading them again for the same control was
            # the «Tehtud» path's extra cost (2026-10-17 audit, JPERF-001).
            drawn = context.get("engagement_reply_form")
            context["engagement_reply_form"] = FeedbackForm(
                matter=matter,
                viewer=request.user,
                open_rounds=rounds,
                choices=getattr(drawn, "organisation_choices", None),
                initial={"engagement": str(wanted)},
            )
        return
    step = _open_step(matter, request.GET.get(STEP_PARAM), form)
    if step is not None:
        context["launch"] = {"form": form.form_key, "step": step}
