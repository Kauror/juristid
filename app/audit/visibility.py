"""Which `ChangeEvent` rows a reader may be shown.

A `ChangeEvent` is an audit fact about *something*, and that something is not
always the Matter. `EVIDENCE_VERSION_ADDED` carries a filename;
`NEXT_ACTION_SET` carries the step's text; `IMPORTANT_DATE_ADDED` carries a
milestone's description. Every one of those subjects is a
`VisibilityInheritingModel` — it has a `visibility_override` that can make it
stricter than the Matter it hangs off.

So `ChangeEvent.objects.filter(matter=matter)` is not a visibility rule. It is a
*parent* visibility rule applied to rows whose subjects have their own, and the
gap between the two is a leak: a reader properly refused a restricted document
could still read its filename out of the timeline, because the row describing it
was selected by the Matter alone (AUTH-003).

This module is the one place that closes that gap, and it deliberately holds no
rule of its own. `_child_families` says which event types are *about* a child and how to reach
that child's Matter; the predicate itself is `child_visibility_q`, which is
`app.core.authorization` as it is everywhere else.

Two properties worth stating because both are load-bearing:

**The population is built inside the boundary.** The child filter is part of the
query, matched on `ChangeEvent.object_id`, rather than a check applied to rows
already fetched. There is no moment at which a forbidden row exists in memory
waiting to be filtered, and no second code path that could forget to filter it.

**Nothing is copied.** The child's restriction is read live, so restricting a
document removes it from the timeline on the next request rather than the next
reindex — the same property `docs/adr/0013` argues for in the search projection.

An event type absent from the map is treated as Matter-level, which is correct
for `MATTER_CREATED`, `MATTER_CLOSED` and their siblings: the Matter genuinely is
the subject. It is also the reason a *new* child event family must be added here
deliberately — and `tests/test_child_projection_visibility.py` fails if one is
carried by a surface without being classified.
"""

from __future__ import annotations

from typing import Any

from django.db.models import Q, QuerySet

from app.audit.enums import ChangeEventType
from app.audit.models import ChangeEvent
from app.core.authorization import apply as apply_scope
from app.core.authorization import child_visibility_q, scope_for_user


def _child_families() -> tuple[tuple[tuple[str, ...], Any, dict[str, str]], ...]:
    """Event types about a child, the model that holds it, and how to reach the
    Matter from that model.

    Imported lazily. `app.audit` is imported by the services of every app that
    records history, so a module-level import of those apps' models here would
    close a ring.

    The third element is what `child_visibility_q` needs to build the predicate
    without a per-model call: the path from the row to its Matter, and the
    column carrying its own restriction. For every family but one those are the
    defaults. ``EVIDENCE_VERSION_ADDED`` is the exception and the reason this is
    data rather than a method call — the event's ``obj`` is a
    ``DocumentVersion``, which has no visibility of its own, so both paths run
    one join further out through the document that does.
    """
    from app.documents.models import Document, DocumentVersion
    from app.intelligence.models import (
        MatterEffectiveDate,
        MatterImportantDate,
        MatterWorkVictory,
    )
    from app.matters.models import Entry, MatterEngagement
    from app.submissions.models import Submission
    from app.workflow.models import NextAction

    direct: dict[str, str] = {}
    through_document = {
        "parent_prefix": "document__matter__",
        "override_field": "document__visibility_override",
    }

    return (
        (
            (ChangeEventType.ENTRY_ADDED, ChangeEventType.ENTRY_EDITED),
            Entry,
            direct,
        ),
        (
            (
                ChangeEventType.NEXT_ACTION_SET,
                ChangeEventType.NEXT_ACTION_COMPLETED,
                ChangeEventType.NEXT_ACTION_REVIEWED,
                ChangeEventType.NEXT_ACTION_CANCELLED,
            ),
            NextAction,
            direct,
        ),
        (
            (
                ChangeEventType.SUBMISSION_CREATED,
                ChangeEventType.SUBMISSION_SENT,
                ChangeEventType.SUBMISSION_WITHDRAWN,
                ChangeEventType.SUBMISSION_SUPERSEDED,
                ChangeEventType.SUBMISSION_RECIPIENTS_CHANGED,
            ),
            Submission,
            direct,
        ),
        ((ChangeEventType.DOCUMENT_CREATED,), Document, direct),
        ((ChangeEventType.EVIDENCE_VERSION_ADDED,), DocumentVersion, through_document),
        (
            (
                ChangeEventType.IMPORTANT_DATE_ADDED,
                ChangeEventType.IMPORTANT_DATE_CHANGED,
                ChangeEventType.IMPORTANT_DATE_CANCELLED,
            ),
            MatterImportantDate,
            direct,
        ),
        (
            (
                ChangeEventType.EFFECTIVE_DATE_ADDED,
                ChangeEventType.EFFECTIVE_DATE_CHANGED,
                ChangeEventType.EFFECTIVE_DATE_CANCELLED,
            ),
            MatterEffectiveDate,
            direct,
        ),
        (
            (ChangeEventType.ENGAGEMENT_ADDED, ChangeEventType.ENGAGEMENT_CHANGED),
            MatterEngagement,
            direct,
        ),
        (
            (
                ChangeEventType.WORK_VICTORY_PROPOSED,
                ChangeEventType.WORK_VICTORY_CHANGED,
                ChangeEventType.WORK_VICTORY_CONFIRMED,
                ChangeEventType.WORK_VICTORY_REJECTED,
            ),
            MatterWorkVictory,
            direct,
        ),
    )


def child_event_types() -> frozenset[str]:
    """Every event type this module knows to be about a child."""
    return frozenset(
        event_type for event_types, _, _ in _child_families() for event_type in event_types
    )


def scope_change_events(events: QuerySet[ChangeEvent], user: Any) -> QuerySet[ChangeEvent]:
    """Narrow a `ChangeEvent` queryset to rows this reader may be shown.

    The caller has already decided *which* events are interesting — a timeline
    filters to its own vocabulary, a feed to its own — and has already bounded
    them to Matters the reader may open. This adds the part neither of those
    does: for a row about a child, the child must be visible too.

    Matter-level events pass through untouched. That is not a gap: the Matter is
    genuinely their subject, and the caller's own Matter filter is the whole
    answer for them.

    **The scope is resolved once.** `visible_to` looks up whether this person
    holds a break-glass grant every time it is called, and there are nine
    families here — so the obvious spelling of this function put nine identical
    lookups on every page that renders a timeline, and took the Matter page from
    38 queries to 47. Resolving once and building each predicate from that scope
    is the same rule at a ninth of the cost, and is what
    `app.matters.activity.annotate_last_activity` already documents for the same
    reason.
    """
    scope = scope_for_user(user)
    known = child_event_types()

    eligible = ~Q(event_type__in=known)
    for event_types, model, paths in _child_families():
        population = apply_scope(model._default_manager.all(), child_visibility_q(scope, **paths))
        eligible |= Q(
            event_type__in=event_types,
            object_id__in=population.values("pk"),
        )

    return events.filter(eligible)
