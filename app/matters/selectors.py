"""Read models for the work surfaces.

Every function here scopes by authorization **before** filtering, ordering,
counting or slicing. A restricted Matter therefore cannot influence a count, a
page boundary or an attention flag, which is the failure mode a UI-level hide
would not catch (master specification 5.2).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

from django.db.models import Exists, OuterRef, Prefetch, Q, QuerySet
from django.utils import timezone

from app.core.dates import format_estonian_date
from app.matters.activity import annotate_last_activity
from app.matters.enums import REGISTER_YEAR_ORIGINS, MatterDataClass, RecordMode
from app.matters.models import Matter
from app.submissions.enums import SubmissionStatus
from app.submissions.models import Submission
from app.workflow.enums import REVIEW_KINDS, ActionKind, ActionStatus, DateSemantics
from app.workflow.models import NextAction

HORIZON_DAYS = 7

#: The URL value that means "no usable reporting year". A word rather than a
#: blank, so that `?aasta=` (a cleared filter) and `?aasta=teadmata` (a
#: deliberate ask for the unknown bucket) cannot be confused.
UNKNOWN_YEAR = "teadmata"

#: The URL value that means "this field is empty". One word across every
#: dimension, because *Vastutaja määramata* is a real bucket on every chart and
#: a bucket you cannot click is a bucket the reader has to take on trust
#: (master specification 18.9, Stage-2E brief 42).
MISSING = "puudub"

#: What `?tegevus=` selects. Each value is a condition on the Matter's *open*
#: next action, and each one exists because some statistic counts exactly it.
#:
#: The rule the whole set rests on is Stage 1's: only DO + DEADLINE can be
#: overdue. `ulevaatus` is due for a look, and is never described as late,
#: because an ordinary dependency on a ministry is not a failure
#: (master specification 18.8).
#:
#: There is deliberately no filter for the stored kind on its own. `teen`,
#: `ootan`, `jalgin` and the two per-kind review values were here until the
#: classification stopped being a user-facing concept; what survived is the
#: pair of conditions a reader can actually act on — a deadline that has
#: passed, and a review date that has arrived — with the kind doing its work
#: inside them rather than in the URL (ADR 0054).
#: What `?materjalid=` selects. Words rather than a boolean for the same reason
#: `?allikas=` uses them: `materjalid=0` reads as "material number zero" in a URL
#: somebody is editing by hand.
MATERIALS_PRESENT = "on"
MATERIALS_ABSENT = "puudub"

#: What `?andmed=` selects. Words rather than the stored REAL/TEST tokens,
#: because every other filter in this register speaks Estonian in the URL, and a
#: link somebody pastes into a chat should read as a sentence.
#:
#: `koik` is the default while the department is still building the system: a
#: developer looking for the test matter they created ten seconds ago must find
#: it in the register, and a filter that silently hid it would teach them the
#: record had not saved. Reporting is where REAL becomes the default, and that
#: is a different surface with a different question (Agent-C brief 14, 24).
DATA_CLASS_ALL = "koik"
DATA_CLASS_REAL = "paris"
DATA_CLASS_TEST = "test"

DATA_CLASS_FILTERS: dict[str, str] = {
    DATA_CLASS_REAL: MatterDataClass.REAL,
    DATA_CLASS_TEST: MatterDataClass.TEST,
}


def filter_by_data_class(queryset: QuerySet[Matter], value: str) -> QuerySet[Matter]:
    """Apply `?andmed=`, after authorization has already narrowed the rows.

    Called on an already-scoped queryset, never on the raw table: data class is
    not an authorization dimension and must not be able to widen one
    (brief 14, 50).

    An unreadable value falls back to the whole population rather than emptying
    it. That is the opposite of what `?tegevus=` does, and deliberately: an
    unknown *condition* should show nothing rather than everything, but this
    parameter's own default is "no restriction", so a typo landing on the
    default is the honest answer rather than a blank register.
    """
    stored = DATA_CLASS_FILTERS.get(value)
    if stored is None:
        return queryset
    return queryset.filter(data_class=stored)


#: A review date that has arrived, whatever kind carries it.
REVIEW_DUE = "ulevaatus"

NEXT_ACTION_FILTERS: tuple[str, ...] = (
    MISSING,
    "hilinenud",
    REVIEW_DUE,
)


def register_year_q(*, start: int, end: int) -> Q:
    """Matters whose reporting year is a *register* year inside the span.

    Both the year chart and the register's own `?aasta=` filter build their
    query here, which is the only reason the bar and the list it opens can be
    asserted to agree. Two similar conditions written in two places is how a
    count and its drill-through start disagreeing (Stage-2E brief 66).
    """
    return Q(
        reporting_year__gte=start,
        reporting_year__lte=end,
        origin__in=REGISTER_YEAR_ORIGINS,
    )


def unknown_register_year_q() -> Q:
    """Matters with no reporting year, or one that is not a register year.

    The exact complement of :func:`register_year_q` over all years, so the two
    buckets partition the population and nothing falls between them.
    """
    return Q(reporting_year__isnull=True) | ~Q(origin__in=REGISTER_YEAR_ORIGINS)


def date_range_q(field: str, *, start: date | None, end: date | None) -> Q:
    """A closed interval on one date column, either end optional.

    **Both ends are inclusive.** A lawyer who asks for 01.01–31.01 means
    January, and a `kuni` that quietly excluded the 31st would drop the busiest
    day of the month from a deadline report without saying so. The column is a
    `DateField`, so there is no time component for an inclusive bound to lose —
    the reasoning that makes `Period.end_datetime` exclusive does not apply here
    (app/reporting/context.py).

    Missing endpoints are open ends rather than errors: "everything since March"
    is a question people ask.
    """
    condition = Q()
    if start is not None:
        condition &= Q(**{f"{field}__gte": start})
    if end is not None:
        condition &= Q(**{f"{field}__lte": end})
    return condition


def organisation_involved_q(organisation_id: Any) -> Q:
    """Either direction: this body sent it, or Koda answered it.

    A *query* convenience and nothing more. `KELLELT` and `KELLELE` remain two
    separate stored facts with two separate precise filters, because the
    register itself changed which one its single counterparty column meant in
    2020 and collapsing them would answer a question nobody asked
    (Stage-2E brief 27, Stage-2E.1 brief 11F).

    Nothing here writes, merges or rewrites either column.
    """
    return Q(source_organisations__id=organisation_id) | Q(
        addressee_organisation_id=organisation_id
    )


def filter_by_materials(queryset: QuerySet[Matter], user: Any, value: str) -> QuerySet[Matter]:
    """Matters that do or do not carry a file this reader may open.

    Scoped through ``Document.objects.visible_to`` rather than the Matter's own
    relation. A document can be restricted below its Matter, and answering
    "failid olemas" from the raw table would tell somebody that material they
    cannot open exists — the same leak the search projection is careful about
    one level up (docs/adr/0014).

    One ``EXISTS`` subquery for the whole page rather than a count per row.
    """
    from app.documents.models import Document

    if value not in {MATERIALS_PRESENT, MATERIALS_ABSENT}:
        return queryset.none()
    documents = Document.objects.visible_to(user).filter(matter=OuterRef("pk"))
    annotated = queryset.annotate(has_material=Exists(documents))
    return annotated.filter(has_material=value == MATERIALS_PRESENT)


def _open_action_condition(value: str, today: date) -> Q:
    """The `NextAction` condition behind one `?tegevus=` value.

    The stored kind is still what decides both conditions — it simply decides
    them here rather than being offered as a filter of its own.
    """
    open_now = Q(status=ActionStatus.OPEN)
    if value == "hilinenud":
        return open_now & Q(
            kind=ActionKind.DO,
            date_semantics=DateSemantics.DEADLINE,
            target_date__lt=today,
        )
    if value == REVIEW_DUE:
        return open_now & Q(
            kind__in=REVIEW_KINDS,
            target_date__isnull=False,
            target_date__lte=today,
        )
    return open_now


def filter_by_next_action(
    queryset: QuerySet[Matter], user: Any, value: str, today: date | None = None
) -> QuerySet[Matter]:
    """Apply `?tegevus=`, through the same authorization the statistic used.

    The subquery is ``NextAction.objects.visible_to(user)`` rather than the raw
    table, because an action can carry a restriction its Matter does not. A
    statistic that counted authorized *actions* and a list that counted Matters
    with *any* action would disagree on exactly the rows it matters most about
    (Stage-2E brief 66).

    One open action per Matter is a database constraint, which is what makes the
    action count and the Matter count the same number.
    """
    if value not in NEXT_ACTION_FILTERS:
        return queryset.none()

    today = today or timezone.localdate()
    actions = NextAction.objects.visible_to(user).filter(
        _open_action_condition(value, today), matter=OuterRef("pk")
    )
    annotated = queryset.annotate(matches_action=Exists(actions))
    return annotated.filter(matches_action=value != MISSING)


def open_action_prefetch(user: Any) -> Prefetch:
    """Attach the one open action this reader may see, without a query per row.

    Scoped, and the scoping is the point rather than a precaution. A
    `NextAction` is a `VisibilityInheritingModel`: it can be restricted below
    the Matter it belongs to, and this prefetch decorates rows of a register
    whose Matters are visible by definition. Unscoped, it printed a restricted
    step's text and the colleague responsible for it onto a row anybody could
    read — the exact condition `work_items.no_next_action_q` already warns about
    two files away, where it says such an action "is invisible to most readers"
    (AUTH-003).
    """
    return Prefetch(
        "next_actions",
        queryset=NextAction.objects.visible_to(user)
        .filter(status=ActionStatus.OPEN)
        .select_related("responsible"),
        to_attr="open_actions",
    )


def matter_list_queryset(user: Any) -> QuerySet[Matter]:
    """The base register query: authorized, with everything a row displays.

    ``annotate_last_activity`` is applied here rather than in each of the four
    views that render the shared row partial. *Viimane tegevus* is part of what
    a row displays, and a surface that forgot the annotation would not render a
    wrong date — `activity_of` refuses to guess — it would raise. Putting it at
    the one place every one of those surfaces already comes through is what
    makes forgetting impossible (Agent-G brief 63, ADR 0026).

    Six correlated subqueries, evaluated once for the page, not per row.
    """
    return annotate_last_activity(
        Matter.objects.visible_to(user)
        .select_related("owner", "stage", "addressee_organisation")
        .prefetch_related(open_action_prefetch(user), "source_organisations", "policy_areas"),
        user,
    )


def matter_engagements(matter: Matter, user: Any) -> list[Any]:
    """The `Kaasamine` records of one Matter, scoped to this reader.

    Ordered by the model, evaluated once, and `select_related` on the author so
    a section with five rows costs one query rather than six. The template
    iterates this list and never asks the database a question of its own
    (Agent-F brief 55).
    """
    from app.matters.models import MatterEngagement

    return list(
        MatterEngagement.objects.filter(matter=matter).visible_to(user).select_related("created_by")
    )


@dataclass(frozen=True)
class ActiveDeadline:
    """The one deadline the Matter header shows.

    A rendered answer rather than a model: `label` is what the date *is*,
    `display` is how it reads at the precision it was recorded to, and
    `days_remaining` is how the header prints " · N p". Nothing here is stored.
    """

    label: str
    value: date
    display: str
    is_past: bool
    days_remaining: int

    @property
    def is_today(self) -> bool:
        return self.days_remaining == 0 and not self.is_past


def active_deadline(
    matter: Matter,
    user: Any,
    today: date | None = None,
    milestones: Any = None,
) -> ActiveDeadline | None:
    """The single most relevant deadline, or nothing at all.

    Two rules, in order (Teema redesign §5.5):

    1. the nearest deadline still ahead of us;
    2. failing that, the nearest one behind us.

    And when the Matter has neither, this returns ``None`` and the header slot
    disappears rather than printing an em dash. A row of labels with no values
    is what made the old header read as a data-quality problem.

    **Two sources, deliberately.** ``Matter.response_deadline`` is the date by
    which Koda's own opinion is due, and it is the commonest deadline on the
    whole register. ``MatterImportantDate`` is everything else the department
    watches. A rule that read only one of them would leave most Matters looking
    undated.

    **Not the ``NextAction`` date.** That is what Koda does next; a review date
    on a WAIT is not a deadline and must never be rendered as one
    (master specification 18.8).

    Cancelled milestones are excluded: an expectation somebody called off is
    history, not a commitment. A period is "past" only once its **last** day is
    behind us, which is why an approximate date is compared on ``period_end``.

    ``milestones`` is for the one caller that has already read them: the Matter
    page renders `Olulised tähtajad` from the same rows, and asking the same
    table the same question twice per request is a query for an answer already
    in memory. Anything passed here must already be authorization-scoped —
    which is why the parameter is not the default.
    """
    from app.intelligence.enums import FactStatus
    from app.intelligence.models import MatterImportantDate

    day = today or timezone.localdate()
    candidates: list[tuple[date, date, str, str]] = []

    if matter.response_deadline is not None:
        candidates.append(
            (
                matter.response_deadline,
                matter.response_deadline,
                "Arvamuse tähtaeg",
                format_estonian_date(matter.response_deadline),
            )
        )

    if milestones is None:
        milestones = (
            MatterImportantDate.objects.filter(matter=matter, status=FactStatus.ACTIVE)
            .visible_to(user)
            .only("date_value", "period_end", "date_precision", "title")
        )
    for record in milestones:
        if record.status != FactStatus.ACTIVE:
            continue
        candidates.append((record.date_value, record.period_end, record.title, record.display_date))

    if not candidates:
        return None

    upcoming = [row for row in candidates if row[1] >= day]
    pool = upcoming or candidates
    # Nearest first among the upcoming; nearest *last* among the past, which is
    # the most recent one — the same "closest to today" rule read backwards.
    anchor_value, period_end, label, display = (
        min(pool, key=lambda row: (row[0], row[1]))
        if upcoming
        else max(pool, key=lambda row: (row[1], row[0]))
    )
    return ActiveDeadline(
        label=label,
        value=anchor_value,
        display=display,
        is_past=period_end < day,
        days_remaining=(anchor_value - day).days,
    )


def current_action_of(matter: Matter, user: Any = None) -> NextAction | None:
    """Read the prefetched open action, falling back to a query if absent.

    The fallback needs the reader for the same reason the prefetch does: it
    answers the same question by a different route, and a route that skipped
    the check would be the leak the prefetch just closed, reachable whenever a
    caller forgot to prefetch. Without a reader it returns nothing rather than
    guessing — a missing action reads as "none set", which is safe, while a
    wrong one is not.
    """
    prefetched = getattr(matter, "open_actions", None)
    if prefetched is not None:
        return prefetched[0] if prefetched else None
    if user is None:
        return None
    return (
        NextAction.objects.visible_to(user).filter(matter=matter, status=ActionStatus.OPEN).first()
    )


def visible_actions(user: Any) -> QuerySet[NextAction]:
    return (
        NextAction.objects.visible_to(user)
        .filter(status=ActionStatus.OPEN)
        .select_related(
            "matter",
            "matter__stage",
            "matter__owner",
            "responsible",
        )
    )


@dataclass(frozen=True)
class WorkGroup:
    """One band of the work timeline, ordered by when it needs attention."""

    key: str
    label: str
    actions: list[NextAction]

    @property
    def count(self) -> int:
        return len(self.actions)


def my_work_timeline(user: Any, today: date | None = None) -> list[WorkGroup]:
    """Everything assigned to one person, in one list, ordered by its date.

    The redesign split this in two: DO actions banded by urgency on the left,
    WAIT and MONITOR in a separate column on the right. Hands-on QA rejected
    the split, and the reason is the one organising question this page exists to
    answer:

        **when do I need to care about this again?**

    That question has one answer per action and it is a date, whatever the mode.
    A ministry's answer expected on Thursday and an opinion due on Thursday are
    both Thursday's problem, and putting them in two columns made a lawyer read
    two lists and merge them in their head.

    So: one chronological list of every open action this person is responsible
    for, in five bands.

    **The mode still means what it meant.** The date says *where* the action
    belongs in time; the chip says *what the date means*, and the presentation
    layer keeps them apart — only DO + DEADLINE is ever described as late.
    Unifying the list is not unifying the vocabulary (master specification
    18.8).

    **The old banding had a hole**, which is the other half of the reported
    defect. `overdue`, `today` and `soon` each required `date_semantics =
    DEADLINE`, while `later` required a date beyond the horizon or none at all —
    so a DO carrying any other semantics, dated inside the next seven days, was
    in no band at all and vanished from the page. The register's own parser
    produces exactly that combination: a DO whose source names a vague month is
    recorded as an expectation, not a deadline
    (app/legacy_import/register_next_actions.py). Banding now reads the date and
    nothing else.
    """
    today = today or timezone.localdate()
    horizon = today + timedelta(days=HORIZON_DAYS)

    mine = visible_actions(user).filter(responsible=user)

    dated = mine.filter(target_date__isnull=False)
    past = list(dated.filter(target_date__lt=today).order_by("target_date"))
    now = list(dated.filter(target_date=today).order_by("matter__title"))
    soon = list(
        dated.filter(target_date__gt=today, target_date__lte=horizon).order_by("target_date")
    )
    later = list(dated.filter(target_date__gt=horizon).order_by("target_date"))
    undated = list(mine.filter(target_date__isnull=True).order_by("matter__title"))

    return [
        WorkGroup("passed", "Tähtaeg või ülevaatus möödas", past),
        WorkGroup("today", "Täna", now),
        WorkGroup("soon", f"Järgmise {HORIZON_DAYS} päeva jooksul", soon),
        WorkGroup("later", "Hiljem", later),
        WorkGroup("undated", "Kuupäevata", undated),
    ]


def overdue_count(actions: Sequence[NextAction], today: date | None = None) -> int:
    """How many of these are genuinely late.

    Only DO + DEADLINE. The page's summary line has to be able to say "two are
    late" without counting a ministry that has not replied yet, which is not
    lateness and must never be presented as it (master specification 18.8).
    """
    day = today or timezone.localdate()
    return sum(1 for action in actions if action.is_overdue(day))


def matters_without_next_action(user: Any) -> QuerySet[Matter]:
    """Open FULL Matters carrying no current instruction.

    This is the one attention state that cannot be derived from a date, which is
    exactly why it needs a query of its own: without it a Matter simply stops
    appearing anywhere and goes quiet (design handoff, recommendation 1).
    """
    has_open_action = NextAction.objects.filter(matter=OuterRef("pk"), status=ActionStatus.OPEN)
    return (
        matter_list_queryset(user)
        .filter(is_open=True, record_mode=RecordMode.FULL)
        .annotate(has_action=Exists(has_open_action))
        .filter(has_action=False)
    )


@dataclass(frozen=True)
class AttentionItem:
    """A deterministic, actionable data-quality problem.

    Nothing speculative appears here. A warning a lawyer cannot act on, or
    disagrees with, teaches them to ignore the panel.
    """

    key: str
    label: str
    matter: Matter
    detail: str = ""


def my_attention_items(user: Any, today: date | None = None) -> list[AttentionItem]:
    # Imported here rather than at module scope: `app.legacy_import` imports the
    # matters app, so a top-level import would close the circle. The same reason
    # `register_display.source_instructions_for` defers its own model import.
    from app.legacy_import.current_state import RegisterCurrency
    from app.legacy_import.register_display import source_instructions_for

    today = today or timezone.localdate()
    items: list[AttentionItem] = []

    # Annotated like every other population that reaches a row showing
    # *viimane tegevus*. `matters_without_next_action` inherits it from
    # `matter_list_queryset`; this one builds its own query, so it says so here
    # rather than rendering an import timestamp beside rows that do not
    # (ADR 0026).
    owned = annotate_last_activity(
        Matter.objects.visible_to(user)
        .filter(owner=user, is_open=True, record_mode="FULL")
        .select_related("stage"),
        user,
    )

    # "Nothing to do next" means neither a structured action nor a sentence the
    # register left behind. Flagging on the structured action alone would put
    # every Matter whose JÄRGMISEKS still carries the instruction on this
    # panel — 185 of the current portfolio on the approved snapshot — and a
    # warning that fires on almost everything is one nobody reads.
    #
    # The source instruction is *not* promoted to an action by being counted
    # here. It only decides whether the Matter has gone quiet.
    without_action = list(matters_without_next_action(user).filter(owner=user)[:200])
    source_texts = source_instructions_for(without_action)
    for matter in without_action[:50]:
        if source_texts.get(matter.pk):
            continue
        items.append(
            AttentionItem(
                key="no_next_action",
                label="Järgmiseks puudub",
                matter=matter,
                detail="Aktiivsel teemal ei ole järgmist tegevust ega registri juhist.",
            )
        )

    # Deliberately no "vastutaja puudub" here. It is a real attention state and
    # it is not a *personal* one: an unowned Matter is on nobody's list by
    # definition, so putting it on everybody's would make each lawyer's panel
    # grow with work that is not theirs. Saabunud already leads with exactly
    # that population, and Osakonna töö carries it for the department head.

    # No HETKESEIS on live work. Read from the derived register state rather
    # than guessed from the stage: a blank status column is a recorded gap in
    # the register, and `stage` is this system's own vocabulary, which a Matter
    # can legitimately carry while the register says nothing.
    for matter in owned.filter(
        current_register_state__currency=RegisterCurrency.CURRENT,
        current_register_state__status_label="",
    )[:50]:
        items.append(
            AttentionItem(
                key="no_status",
                label="Hetkeseis puudub",
                matter=matter,
                detail="Jooksval teemal ei ole registris hetkeseisu märgitud.",
            )
        )

    # A response deadline that has passed with nothing sent. Only flagged where
    # the question is meaningful: the Matter is still open and a deadline was
    # actually recorded.
    sent_submission = Submission.objects.filter(matter=OuterRef("pk"), status=SubmissionStatus.SENT)
    overdue_response = (
        owned.filter(response_deadline__lt=today)
        .annotate(has_sent=Exists(sent_submission))
        .filter(has_sent=False)
    )
    for matter in overdue_response[:50]:
        items.append(
            AttentionItem(
                key="deadline_without_submission",
                label="Tähtaeg möödas, arvamust ei ole saadetud",
                matter=matter,
                detail=(f"Arvamuse tähtaeg oli {format_estonian_date(matter.response_deadline)}."),
            )
        )

    # Deliberately no "overdue NextAction" item. `my_work_timeline` already leads
    # Minu töö with a *Tähtaeg möödas* band built from exactly those actions,
    # and repeating them here would make the same task look like two problems.

    return items


@dataclass(frozen=True)
class AttentionGroup:
    """One Matter and every reason it needs attention.

    Grouping happens here rather than in the template because the panel now
    produces several kinds of reason, and one Matter can legitimately carry
    three of them — no owner recorded, no status, and a deadline gone by. Three
    separate rows for one file would make the panel look three times as bad as
    it is and leave the reader to work out that they are the same Matter.
    """

    matter: Matter
    items: tuple[AttentionItem, ...]

    @property
    def labels(self) -> tuple[str, ...]:
        return tuple(item.label for item in self.items)


def group_attention(items: list[AttentionItem]) -> list[AttentionGroup]:
    """One entry per Matter, reasons in the order they were detected.

    Insertion-ordered rather than sorted: the detection order is already the
    order the panel wants — a missing next step first, then a missing status,
    then a passed deadline — and re-sorting would put the tidiest problem at the
    top of somebody's day.
    """
    grouped: dict[Any, list[AttentionItem]] = {}
    matters: dict[Any, Matter] = {}
    for item in items:
        grouped.setdefault(item.matter.pk, []).append(item)
        matters.setdefault(item.matter.pk, item.matter)
    return [
        AttentionGroup(matter=matters[key], items=tuple(values)) for key, values in grouped.items()
    ]


def my_active_matters(user: Any) -> QuerySet[Matter]:
    """The signed-in person's open portfolio.

    Called inventory, never workload: a count of open files says nothing about
    effort, and the specification forbids presenting it as if it did
    (master specification 7.2, 18.8).

    FULL only, like every other current-work surface. Until Stage 2F this was
    the one selector that did not say so, and it did not matter because
    imported archive rows had no owner and so matched nobody. Restoring the
    register's owners makes it matter a great deal: without this filter, every
    lawyer's Minu töö would fill with a decade of archive records the moment
    the backfill runs. An archive row is history, not a work queue.
    """
    return (
        matter_list_queryset(user)
        .filter(Q(owner=user) | Q(collaborators=user), is_open=True, record_mode=RecordMode.FULL)
        .distinct()
        .order_by("-updated_at")
    )
