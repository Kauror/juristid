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
    from app.matters.models import (
        Entry,
        MatterEngagement,
        MatterExternalPosition,
        MatterProceduralDevelopment,
        MatterProceduralLink,
    )
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
            (
                ChangeEventType.ENGAGEMENT_ADDED,
                ChangeEventType.ENGAGEMENT_CHANGED,
                ChangeEventType.ENGAGEMENT_FEEDBACK_CLOSED,
            ),
            MatterEngagement,
            direct,
        ),
        (
            (
                ChangeEventType.EXTERNAL_POSITION_RECORDED,
                ChangeEventType.EXTERNAL_POSITION_CORRECTED,
                ChangeEventType.EXTERNAL_POSITION_SOURCE_CHANGED,
                ChangeEventType.EXTERNAL_POSITION_DOCUMENT_LINKED,
            ),
            MatterExternalPosition,
            direct,
        ),
        (
            # `Menetluse link`. Both payloads carry the address itself — which
            # is the whole content of the record and the reason the events are
            # worth having — so an event about a link restricted below its
            # Matter would put that address in front of a reader properly
            # refused the link. Neither event is in `TIMELINE_EVENT_TYPES`, so
            # no surface renders one today; it is classified here anyway,
            # because the leak arrives the day one does and the classification
            # is what this module exists to require (docs/adr/0089 §11,
            # AUTH-003).
            (
                ChangeEventType.PROCEDURAL_LINK_RECORDED,
                ChangeEventType.PROCEDURAL_LINK_CORRECTED,
            ),
            MatterProceduralLink,
            direct,
        ),
        (
            (
                ChangeEventType.PROCEDURAL_DEVELOPMENT_RECORDED,
                ChangeEventType.PROCEDURAL_DEVELOPMENT_CORRECTED,
                ChangeEventType.PROCEDURAL_DEVELOPMENT_DOCUMENT_LINKED,
            ),
            MatterProceduralDevelopment,
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


#: Event types whose subject genuinely **is** the Matter, and which are therefore
#: safe to render to anybody the Matter itself is visible to.
#:
#: **An explicit vocabulary, because the default here is the wrong default for a
#: rendering surface.** :func:`scope_change_events` lets an unclassified event
#: type through — correctly, because for `MATTER_CREATED` and its siblings the
#: Matter really is the subject, and the caller's own Matter filter is the whole
#: answer. That rule is safe for a *caller that names its own vocabulary*, which
#: every surface did until `Kõik muudatused` asked for «everything». «Everything»
#: plus "unknown means Matter-level" is "unknown means allowed", and the day a
#: new child family is added it becomes "unknown means leaked"
#: (docs/adr/0092 §11, AUTH-003).
#:
#: So the change log renders this set, unioned with the child families
#: :func:`child_event_types` has classified, and nothing else. A family that is
#: neither is absent from the page until somebody decides which of the two it is.
#: `tests/test_substantive_matter_history.py` fails if that stops being true.
#:
#: **Six families are deliberately not here**, and each is a concrete leak rather
#: than a precaution:
#:
#: * `MATTER_RELATION_ADDED` / `MATTER_RELATION_REMOVED` — the summary names the
#:   *other* Matter. A reader refused a RESTRICTED related Matter with a 404 could
#:   read its title here.
#: * `BACKGROUND_MATERIAL_ADDED` / `BACKGROUND_MATERIAL_REMOVED` — the summary
#:   names a `Document` or a foreign `Submission`, each of which carries its own
#:   override.
#: * `WEBSITE_OVERVIEW_PLANNED` / `_PUBLISHED` / `_CANCELLED` /
#:   `_LINK_CORRECTED` — `MatterWebsiteOverview` is a
#:   `VisibilityInheritingModel` and the summary carries what it holds.
#:
#: None of the three has a visibility classifier in :func:`_child_families`
#: today. Classifying them is the right fix and it is a change to *that* map, not
#: to this one; until somebody makes it, the audit page is incomplete rather than
#: unsafe, which is the trade this vocabulary exists to take.
#:
#: `TAG_ASSIGNED` and `TAG_REMOVED` are here because a `Tag` is department
#: reference data with no visibility of its own. `IMPORT_APPLIED` and the four
#: cutover events name an operation over the Matter, never a child.
MATTER_LEVEL_EVENT_TYPES: frozenset[str] = frozenset(
    {
        ChangeEventType.MATTER_CREATED,
        ChangeEventType.MATTER_ASSIGNED,
        ChangeEventType.MATTER_TITLE_CHANGED,
        ChangeEventType.MATTER_STAGE_CHANGED,
        ChangeEventType.MATTER_TRACK_CHANGED,
        ChangeEventType.MATTER_ORGANISATION_CHANGED,
        ChangeEventType.MATTER_DATE_CHANGED,
        ChangeEventType.MATTER_POSITION_UPDATED,
        ChangeEventType.MATTER_BRIEF_SUMMARY_SET,
        ChangeEventType.MATTER_POLICY_AREAS_CHANGED,
        ChangeEventType.MATTER_POLICY_AREA_OTHER_SET,
        ChangeEventType.MATTER_LEGAL_INSTRUMENTS_CHANGED,
        ChangeEventType.MATTER_LEGAL_INSTRUMENT_OTHER_SET,
        ChangeEventType.MATTER_VISIBILITY_CHANGED,
        ChangeEventType.MATTER_DATA_CLASS_CHANGED,
        ChangeEventType.MATTER_CLOSED,
        ChangeEventType.MATTER_REOPENED,
        ChangeEventType.MATTER_PROMOTED,
        ChangeEventType.MATTER_HISTORICAL_CUTOVER_CLOSED,
        ChangeEventType.MATTER_REGISTER_CUTOVER_RETIRED,
        ChangeEventType.MATTER_REGISTER_CUTOVER_ACTIVATED,
        ChangeEventType.MATTER_SOURCE_FIELDS_REFRESHED,
        ChangeEventType.TAG_ASSIGNED,
        ChangeEventType.TAG_REMOVED,
        ChangeEventType.IMPORT_APPLIED,
    }
)


def change_log_event_types() -> frozenset[str]:
    """The whole vocabulary `Kõik muudatused` may render.

    Explicitly-safe Matter-level types, plus the child families
    :func:`_child_families` knows how to scope — and nothing else. Both halves
    are enumerated rather than derived by exclusion, so a new event family
    reaches that page only when somebody adds it to one of them.
    """
    return MATTER_LEVEL_EVENT_TYPES | child_event_types()


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
    holds a break-glass grant every time it is called, and there are ten
    families here — so the obvious spelling of this function put one identical
    lookup per family on every page that renders a timeline, and took the Matter
    page from 38 queries to 47. Resolving once and building each predicate from
    that scope is the same rule at a fraction of the cost, and is what
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
