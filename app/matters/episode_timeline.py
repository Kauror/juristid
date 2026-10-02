"""`Teema käik`, grouped under the `Hetkeseis` periods each act was done in (docs/adr/0131 §7).

**The rows are `matter_timeline`'s, unchanged; only where they sit is new.**
Every row — a note, an opinion, a consultation, a file, a step — is placed in
the period that was current when the work was recorded, read off the binding
written at that moment (`ChangeEvent.stage_episode`). Nothing here reads
today's `Matter.stage` to decide where an old row goes: a row moves only if the
record of when it was done moves, and that record is append-only.

Within a period the order is the chronology's own, newest first. Periods are
newest first too, the current one open and the rest closed.

**What has no period is not given one.** Work recorded before periods existed,
and work done while the Matter had no `Hetkeseis`, carries no binding. It is
placed by the moment it was recorded against the moments the periods began —
both immutable system times, never a business date and never the current
stage — and reads under «Varasem tegevus» before the first period, or under
«Hetkeseis määramata» between two.

**The boundary is not a row.** A stage move recorded inside a period's history
would say, in the list, what the period heading already says; a move recorded
since periods exist therefore draws no row of its own, and a `Märge` whose only
content was the move draws none either. Both are still in `Kõik muudatused`.
A move recorded before periods existed keeps its row in «Varasem tegevus»,
where no heading says it.

**The whole history, once.** `matter_timeline` pages by time, and a period's rows
are not a time window — an opinion recorded in this period may carry a date from
the last. So a grouped chronology reads every row of the Matter in one pass and
places each; the flat chronology of a Matter with no periods still pages exactly
as before (ENG-018, ENG-125).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from app.audit.enums import ChangeEventType
from app.audit.models import ChangeEvent
from app.matters.models import Matter, MatterStageEpisode, MatterWebsiteOverview
from app.matters.stage_episodes import episodes_of, period_label
from app.matters.timeline import TIMELINE_FILTER_ALL, TimelineItem, matter_timeline
from app.submissions.models import Submission

#: Every row, in one read: the grouped chronology places rows by period, not by page.
WHOLE_HISTORY = 100_000

#: The heading over work recorded before the Matter's first period.
EARLIER_ACTIVITY_LABEL = "Varasem tegevus"

#: The heading over work recorded while the Matter had no `Hetkeseis`.
NO_STAGE_LABEL = "Hetkeseis määramata"

#: «The caller did not say which step is open» — so the chronology asks itself.
_ASK: Any = object()


@dataclass
class StageGroup:
    """One accordion in `Teema käik`: a period, or work that had none."""

    #: Stable and unique on the page: the period's id, or a word for the rest.
    key: str
    label: str
    period: str = ""
    episode: MatterStageEpisode | None = None
    is_current: bool = False
    #: 2 for the second time this Matter held this stage, and so on; 1 otherwise.
    repeat: int = 1
    is_open: bool = False
    items: list[TimelineItem] = field(default_factory=list)

    @property
    def dom_id(self) -> str:
        return f"etapp-{self.key}"


@dataclass
class EpisodeTimeline:
    """The grouped chronology, newest group first."""

    groups: list[StageGroup]

    @property
    def count(self) -> int:
        return sum(len(group.items) for group in self.groups)


def _event_episode(item: TimelineItem) -> Any:
    """The period a row drawn from audit rows was written in."""
    for event in item.events or ((item.event,) if item.event is not None else ()):
        if event is not None and event.stage_episode_id is not None:
            return event.stage_episode_id
    return None


def _binding_types(record: Any) -> tuple[str, ...]:
    """The audit row that marks when a projected record's row *happened*.

    An opinion's row is its send, an overview's its publication or its
    cancellation, a reviewed win its confirmation — each can be recorded in a
    later period than the record was first written. Every other record's row is
    the record itself, and its earliest event says when that was.
    """
    if isinstance(record, Submission):
        return (ChangeEventType.SUBMISSION_SENT.value,)
    if isinstance(record, MatterWebsiteOverview):
        if record.is_cancelled:
            return (ChangeEventType.WEBSITE_OVERVIEW_CANCELLED.value,)
        return (ChangeEventType.WEBSITE_OVERVIEW_PUBLISHED.value,)
    if getattr(record, "is_cancelled", False):
        return (
            ChangeEventType.IMPORTANT_DATE_CANCELLED.value,
            ChangeEventType.EFFECTIVE_DATE_CANCELLED.value,
        )
    if getattr(record, "confirmed_at", None) is not None:
        return (ChangeEventType.WORK_VICTORY_CONFIRMED.value,)
    return ()


def _record_bindings(matter: Matter, items: list[TimelineItem]) -> dict[Any, Any]:
    """Row index → period, for every row anchored on an entry or a projected record.

    One query. Only the binding column is read off these events, so nothing a
    reader could not already see on the row reaches the page.
    """
    anchors: dict[Any, tuple[int, tuple[str, ...]]] = {}
    for index, item in enumerate(items):
        if item.entry is not None:
            anchors[item.entry.pk] = (index, (ChangeEventType.ENTRY_ADDED.value,))
        elif item.record is not None:
            anchors[item.record.pk] = (index, _binding_types(item.record))
    if not anchors:
        return {}
    found: dict[int, tuple[int, datetime, Any]] = {}
    for object_id, event_type, episode_id, occurred_at in (
        ChangeEvent.objects.filter(matter=matter, object_id__in=list(anchors))
        .order_by("occurred_at", "created_at", "id")
        .values_list("object_id", "event_type", "stage_episode_id", "occurred_at")
    ):
        index, preferred = anchors[object_id]
        rank = preferred.index(event_type) if event_type in preferred else len(preferred)
        held = found.get(index)
        # The preferred event wins; among equals, the earliest.
        if held is None or rank < held[0]:
            found[index] = (rank, occurred_at, episode_id)
    return {index: episode_id for index, (_rank, _at, episode_id) in found.items()}


def _is_boundary(item: TimelineItem) -> bool:
    """A row that only says the stage moved — the period heading says it now.

    Only a move recorded since periods exist, which is exactly one whose event
    carries a period: an older move has no heading saying it and keeps its row.
    """
    event = item.event
    if (
        item.record is None
        and item.entry is None
        and event is not None
        and event.event_type == ChangeEventType.MATTER_STAGE_CHANGED
    ):
        return event.stage_episode_id is not None
    development = item.procedural_development
    if development is not None and item.stage_effect:
        moved_only = (
            not (development.title or "").strip()
            and not (development.note or "").strip()
            and not item.files
            and item.next_step is None
        )
        return moved_only and _event_episode(item) is not None
    return False


def _opened_at(episode: MatterStageEpisode) -> datetime:
    """When a period began, as far as this system knows — for placing work with none."""
    return episode.started_at or episode.created_at


def matter_episode_timeline(
    *,
    matter: Matter,
    user: Any,
    only: str = TIMELINE_FILTER_ALL,
    intelligence: Any = None,
    current_action: Any = _ASK,
    episodes: list[MatterStageEpisode] | None = None,
) -> EpisodeTimeline | None:
    """The chronology grouped by period, or ``None`` for a Matter that has never had one.

    ``None`` keeps a Matter with no periods — one that has never held a
    `Hetkeseis` since periods exist — on the flat, paged chronology it always
    had: one group called «Varasem tegevus» over every row would be a heading
    saying nothing.
    """
    if episodes is None:
        episodes = episodes_of(matter)
    if not episodes:
        return None

    extra: dict[str, Any] = {}
    if current_action is not _ASK:
        extra["current_action"] = current_action
    items, _more = matter_timeline(
        matter=matter,
        user=user,
        limit=WHOLE_HISTORY,
        only=only,
        intelligence=intelligence,
        **extra,
    )
    bound = _record_bindings(matter, items)

    by_episode: dict[Any, list[TimelineItem]] = {episode.pk: [] for episode in episodes}
    # Gap k holds unbound work recorded before episode k began (k = 0 is
    # «Varasem tegevus»); gap len(episodes) holds work after the last one began.
    gaps: dict[int, list[TimelineItem]] = {}
    for index, item in enumerate(items):
        if _is_boundary(item):
            continue
        episode_id = (
            bound.get(index) if (item.entry is not None or item.record is not None) else None
        )
        if episode_id is None and item.entry is None and item.record is None:
            episode_id = _event_episode(item)
        if episode_id is not None and episode_id in by_episode:
            by_episode[episode_id].append(item)
            continue
        position = next(
            (k for k, episode in enumerate(episodes) if _opened_at(episode) > item.created_at),
            len(episodes),
        )
        gaps.setdefault(position, []).append(item)

    groups: list[StageGroup] = []
    seen: dict[Any, int] = {}
    for position, episode in enumerate(episodes):
        if position in gaps:
            groups.append(
                StageGroup(
                    key="varasem" if position == 0 else f"maaramata-{position}",
                    label=EARLIER_ACTIVITY_LABEL if position == 0 else NO_STAGE_LABEL,
                    items=gaps[position],
                )
            )
        seen[episode.stage_id] = seen.get(episode.stage_id, 0) + 1
        groups.append(
            StageGroup(
                key=str(episode.pk),
                label=episode.stage.label_et,
                period=period_label(episode),
                episode=episode,
                is_current=episode.is_current,
                repeat=seen[episode.stage_id],
                items=by_episode[episode.pk],
            )
        )
    if len(episodes) in gaps:
        groups.append(
            StageGroup(
                key=f"maaramata-{len(episodes)}",
                label=NO_STAGE_LABEL,
                items=gaps[len(episodes)],
            )
        )

    groups.reverse()
    # The current period is open; with none current — the stage was cleared —
    # the newest group is, so the place new work lands is always in view.
    current = next((group for group in groups if group.is_current), None)
    (current or groups[0]).is_open = True
    return EpisodeTimeline(groups=groups)
