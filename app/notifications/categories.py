"""The six kinds of dated obligation a person can be told about (docs/adr/0198).

One registry. Each :class:`Category` states, once:

* its Estonian label, the one sentence the settings page explains it with, and
  the wording of each delivery;
* its defaults — on or off, which advance intervals, whether the day itself is
  a delivery — which apply to anybody who has not chosen otherwise
  (`NotificationPreference` stores explicit choices only, like ADR 0194);
* **its population**, as a function of the reader: :attr:`Category.sources`
  returns the source rows that are *currently* this reader's to be told about —
  outstanding, at a day precision, and resolved to this reader as the
  responsible person. It is built from the canonical selectors of
  `app.matters.work_items` (and, for commencements, ADR 0188's
  `eligibility_q`), so «is this still due, and is it mine» has one answer, read
  by the generator and by every list.

Evaluated as the recipient
--------------------------
The generator asks each population *as the person it would notify*
(`department_workers`, one at a time), so the recipient's own `visible_to`
decides what can reach them, and the lists ask the same function as the
signed-in reader to re-validate every stored row live. Under ADR 0181 every
business reader reads every Matter, so this is the whole department's
population; the point of asking as the recipient is that a narrower rule later
narrows notifications with it, without a second copy of authorization here.

Who is responsible
------------------
``Matter.owner`` for an opinion deadline, a feedback round, a commencement and
an important date. For a step, ``NextAction.responsible`` when it is set and the
account is active, and the Matter's owner otherwise (owner decision D2,
2026-10-30). An unowned Matter, or one whose person is inactive, reaches
nobody's personal list — it stays on the management surfaces, never broadcast.

Day precision only
------------------
A month, a quarter, a half-year or a year has no day to remind anybody on
(ADR 0079). Those rows produce nothing, ever.

Escalation
----------
Only `OPINION_DEADLINE` and `ACTION_DEADLINE` escalate (owner decision D3), and
they use the **operational** overdue definition: `outstanding_response_deadlines`
(an `Arvamuse tähtaeg` under an open Järgmiseks other than a WAIT is not
today's work, ADR 0050) and `lateness.overdue_q` (only DO + DEADLINE can be
late). A review date never escalates and is never called late.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from typing import Any

from django.db.models import Exists, OuterRef, Q, QuerySet

from app.intelligence.models import MatterEffectiveDate
from app.matters.enums import RecordMode
from app.matters.models import Matter, MatterEngagement
from app.notifications.enums import NotificationCategory, NotificationKind, NotificationSource
from app.notifications.models import ALLOWED_OFFSETS
from app.notifications.services import DAY_PRECISIONS, eligibility_q
from app.workflow.enums import ActionKind, ActionStatus, DateSemantics
from app.workflow.lateness import overdue_q
from app.workflow.models import NextAction

#: How long after the day an outstanding source still gets its on-the-day row,
#: and how long after a missed deadline management still gets its one notice,
#: if the worker was down. The same seven days ADR 0188 gives a commencement.
CATCH_UP_DAYS = 7

#: The preference key of the management escalation. Not a category: escalation
#: rows keep their source's category, and this key switches all of them.
ESCALATION_KEY = "OVERDUE_ESCALATION"


def _responsible_q(reader: Any) -> Q:
    """D2: the step's own person when they are active, the Matter's owner otherwise."""
    return Q(responsible=reader, responsible__is_active=True) | (
        (Q(responsible__isnull=True) | Q(responsible__is_active=False))
        & Q(matter__owner=reader, matter__owner__is_active=True)
    )


def _open_steps(reader: Any) -> QuerySet[NextAction]:
    """The Matter's current step, readable by ``reader``, on an open FULL Matter, dated to a day.

    `work_items.dated_actions` reads ``OPEN`` and ``PLANNED`` steps: a planned
    step is future work, not a deadline anybody is held to yet, so only the
    current one notifies.
    """
    from app.matters.work_items import dated_actions

    return dated_actions(reader).filter(status=ActionStatus.OPEN, date_precision__in=DAY_PRECISIONS)


# -- the six populations, each «outstanding, a day, and this reader's» ------


def _opinion_sources(reader: Any) -> QuerySet[Any]:
    from app.matters.work_items import response_obligations

    # The obligation, not the plan: an open Järgmiseks does not answer a
    # ministry (work_items.response_obligations).
    return response_obligations(reader, owner=reader).filter(owner__is_active=True)


def _feedback_sources(reader: Any) -> QuerySet[Any]:
    from app.matters.work_items import dated_feedback_waits

    return dated_feedback_waits(reader, owner=reader).filter(matter__owner__is_active=True)


def _action_sources(reader: Any) -> QuerySet[Any]:
    return (
        _open_steps(reader)
        .filter(kind=ActionKind.DO, date_semantics=DateSemantics.DEADLINE)
        .filter(_responsible_q(reader))
    )


def _commencement_sources(reader: Any) -> QuerySet[Any]:
    """ADR 0188 §2: open and closed Teemad alike; the one eligibility statement.

    **Not while a legacy seven-day check is open for the same date.** A
    `CommencementReminder` of stage ``SEVEN_DAYS`` written before owner decision
    D1 is already the advance prompt for that date; an advance notice beside it
    would show one commencement twice. Neither generated nor shown until that
    row is answered or superseded on the day.
    """
    from app.notifications.enums import ReminderStage, ReminderStatus
    from app.notifications.models import CommencementReminder

    legacy = CommencementReminder.objects.filter(
        effective_date=OuterRef("pk"),
        source_date=OuterRef("date_value"),
        stage=ReminderStage.SEVEN_DAYS,
        status=ReminderStatus.OPEN,
    )
    return (
        MatterEffectiveDate.objects.visible_to(reader)
        .filter(eligibility_q())
        .filter(matter__owner=reader, matter__owner__is_active=True)
        .exclude(Exists(legacy))
    )


def _important_sources(reader: Any) -> QuerySet[Any]:
    from app.matters.work_items import important_deadlines

    # Informational kinds are excluded by `important_deadlines` (ADR 0120).
    return important_deadlines(reader, owner=reader).filter(
        date_precision__in=DAY_PRECISIONS, matter__owner__is_active=True
    )


def _review_sources(reader: Any) -> QuerySet[Any]:
    return (
        _open_steps(reader)
        .filter(
            kind__in=(ActionKind.WAIT, ActionKind.MONITOR),
            date_semantics__in=(DateSemantics.REVIEW_ON, DateSemantics.EXPECTED_AROUND),
        )
        .filter(_responsible_q(reader))
    )


# -- escalation: the operational overdue population, department-wide -------


def _opinion_overdue(reader: Any, today: date) -> QuerySet[Any]:
    from app.matters.work_items import outstanding_response_deadlines

    return outstanding_response_deadlines(reader).filter(response_deadline__lt=today)


def _action_overdue(reader: Any, today: date) -> QuerySet[Any]:
    return (
        NextAction.objects.visible_to(reader)
        .filter(
            status=ActionStatus.OPEN,
            matter__is_open=True,
            matter__record_mode=RecordMode.FULL,
            date_precision__in=DAY_PRECISIONS,
        )
        .filter(overdue_q(today))
    )


# -- what a source currently says, unscoped: only to name a retirement ------


@dataclass(frozen=True)
class SourceState:
    """A source's current day (``None`` if it has none) and who is responsible for it."""

    day: date | None
    responsible_id: Any


def _state_of_matters(ids: list[Any]) -> dict[Any, SourceState]:
    return {
        pk: SourceState(day, owner)
        for pk, day, owner in Matter.objects.filter(pk__in=ids).values_list(
            "pk", "response_deadline", "owner_id"
        )
    }


def _state_of_engagements(ids: list[Any]) -> dict[Any, SourceState]:
    return {
        pk: SourceState(day, owner)
        for pk, day, owner in MatterEngagement.objects.filter(pk__in=ids).values_list(
            "pk", "feedback_deadline", "matter__owner_id"
        )
    }


def _state_of_steps(ids: list[Any]) -> dict[Any, SourceState]:
    out = {}
    for pk, day, precision, responsible, active, owner in NextAction.objects.filter(
        pk__in=ids
    ).values_list(
        "pk",
        "target_date",
        "date_precision",
        "responsible_id",
        "responsible__is_active",
        "matter__owner_id",
    ):
        person = responsible if responsible is not None and active else owner
        out[pk] = SourceState(day if precision in DAY_PRECISIONS else None, person)
    return out


def _state_of_dated_facts(model: Any) -> Callable[[list[Any]], dict[Any, SourceState]]:
    def state(ids: list[Any]) -> dict[Any, SourceState]:
        return {
            pk: SourceState(day if precision in DAY_PRECISIONS else None, owner)
            for pk, day, precision, owner in model.objects.filter(pk__in=ids).values_list(
                "pk", "date_value", "date_precision", "matter__owner_id"
            )
        }

    return state


def _important_state(ids: list[Any]) -> dict[Any, SourceState]:
    from app.intelligence.models import MatterImportantDate

    return _state_of_dated_facts(MatterImportantDate)(ids)


@dataclass(frozen=True)
class Category:
    key: str
    label: str
    #: The one sentence the settings page explains the category with.
    help_text: str
    source_type: str
    default_enabled: bool
    default_offsets: tuple[int, ...]
    default_on_day: bool
    #: Whether the day itself is a delivery of this category. A commencement's
    #: is not: on the day the `CommencementReminder` check is the «Vajab
    #: tegevust» item (owner decision D1).
    supports_on_day: bool
    #: ACTION for an obligation; INFO for a review, which is never late.
    on_day_kind: str
    #: The date field on the source row, and how the source names its Teema.
    date_field: str
    matter_field: str
    #: «Arvamuse tähtaeg 05.11.2026 · 3 päeva pärast».
    date_label: str
    advance_label: str
    on_day_label: str
    sources: Callable[[Any], QuerySet[Any]]
    state: Callable[[list[Any]], dict[Any, SourceState]]
    overdue: Callable[[Any, date], QuerySet[Any]] | None = None
    #: Where on the Teema page the source lives.
    anchor: str = ""

    @property
    def escalates(self) -> bool:
        return self.overdue is not None


CATEGORIES: dict[str, Category] = {
    category.key: category
    for category in (
        Category(
            key=NotificationCategory.OPINION_DEADLINE,
            label="Arvamuse tähtaeg",
            help_text=(
                "Teema arvamuse tähtaeg, kuni arvamus on saadetud "
                "või registris lõpetatuks märgitud."
            ),
            source_type=NotificationSource.MATTER,
            default_enabled=True,
            default_offsets=(7, 3, 1),
            default_on_day=True,
            supports_on_day=True,
            on_day_kind=NotificationKind.ACTION,
            date_field="response_deadline",
            matter_field="pk",
            date_label="Arvamuse tähtaeg",
            advance_label="Arvamuse tähtaeg läheneb",
            on_day_label="Arvamuse tähtaeg",
            sources=_opinion_sources,
            state=_state_of_matters,
            overdue=_opinion_overdue,
        ),
        Category(
            key=NotificationCategory.FEEDBACK_DEADLINE,
            label="Kaasamise tagasiside tähtaeg",
            help_text=(
                "Kuupäev, milleks kaasamisel tagasisidet oodatakse, kuni kaasamine on lõpetatud."
            ),
            source_type=NotificationSource.ENGAGEMENT,
            default_enabled=True,
            default_offsets=(3,),
            default_on_day=True,
            supports_on_day=True,
            on_day_kind=NotificationKind.ACTION,
            date_field="feedback_deadline",
            matter_field="matter_id",
            date_label="Tagasisidet ootame kuni",
            advance_label="Kaasamise tagasiside tähtaeg läheneb",
            on_day_label="Kaasamise tagasiside tähtaeg",
            sources=_feedback_sources,
            state=_state_of_engagements,
        ),
        Category(
            key=NotificationCategory.ACTION_DEADLINE,
            label="Tegevuse tähtaeg",
            help_text=(
                "Tähtajaga tegevus, mille vastutaja oled sina — või teema vastutajana, "
                "kui tegevusel vastutajat ei ole."
            ),
            source_type=NotificationSource.NEXT_ACTION,
            default_enabled=True,
            default_offsets=(3, 1),
            default_on_day=True,
            supports_on_day=True,
            on_day_kind=NotificationKind.ACTION,
            date_field="target_date",
            matter_field="matter_id",
            date_label="Tegevuse tähtaeg",
            advance_label="Tegevuse tähtaeg läheneb",
            on_day_label="Tegevuse tähtaeg",
            sources=_action_sources,
            state=_state_of_steps,
            overdue=_action_overdue,
        ),
        Category(
            key=NotificationCategory.COMMENCEMENT_CHECK,
            label="Jõustumise kontroll",
            help_text=(
                "Meeldetuletus enne teemale kirjutatud täpset jõustumise kuupäeva; "
                "kuupäeval endal palutakse jõustumist kontrollida."
            ),
            source_type=NotificationSource.EFFECTIVE_DATE,
            default_enabled=True,
            default_offsets=(7,),
            default_on_day=False,
            supports_on_day=False,
            on_day_kind=NotificationKind.ACTION,
            date_field="date_value",
            matter_field="matter_id",
            date_label="Kirjas olev jõustumise kuupäev",
            advance_label="Jõustumise kontroll läheneb",
            on_day_label="Kontrolli jõustumist",
            sources=_commencement_sources,
            state=_state_of_dated_facts(MatterEffectiveDate),
            anchor="#joustumine",
        ),
        Category(
            key=NotificationCategory.IMPORTANT_DATE,
            label="Oluline tähtaeg",
            help_text="Teemale lisatud oluline tähtaeg, mis on kirjas täpse päevaga.",
            source_type=NotificationSource.IMPORTANT_DATE,
            default_enabled=False,
            default_offsets=(7, 1),
            default_on_day=True,
            supports_on_day=True,
            on_day_kind=NotificationKind.ACTION,
            date_field="date_value",
            matter_field="matter_id",
            date_label="Oluline tähtaeg",
            advance_label="Oluline tähtaeg läheneb",
            on_day_label="Oluline tähtaeg",
            sources=_important_sources,
            state=_important_state,
        ),
        Category(
            key=NotificationCategory.REVIEW_DATE,
            label="Ülevaatamise kuupäev",
            help_text=(
                "Ootel või jälgitava tegevuse kuupäev, mil on aeg olukord üle vaadata. "
                "See ei ole tähtaeg."
            ),
            source_type=NotificationSource.NEXT_ACTION,
            default_enabled=False,
            default_offsets=(),
            default_on_day=True,
            supports_on_day=True,
            on_day_kind=NotificationKind.INFO,
            date_field="target_date",
            matter_field="matter_id",
            date_label="Ülevaatamise kuupäev",
            advance_label="Ülevaatamise aeg läheneb",
            on_day_label="On aeg üle vaadata",
            sources=_review_sources,
            state=_state_of_steps,
        ),
    )
}

#: Everything a `NotificationPreference.category` may hold.
PREFERENCE_KEYS: frozenset[str] = frozenset({*CATEGORIES, ESCALATION_KEY})

ESCALATION_LABEL = "Osakonna teavitus ületatud tähtajast"
ESCALATION_HELP = (
    "Juhtkonnale üks teade järgmisel hommikul, kui arvamuse või tegevuse tähtaeg jäi täitmata."
)

#: The furthest ahead any interval can reach — the generator's window.
MAX_OFFSET = max(ALLOWED_OFFSETS)


def escalating() -> list[Category]:
    return [category for category in CATEGORIES.values() if category.escalates]
