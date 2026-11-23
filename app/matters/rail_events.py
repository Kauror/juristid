"""What happened inside each phase of «Menetluse kulg» (owner, 2026-11-23).

Learned from the Riigikogu's own «Menetluskäik»: a phase is not one point but a
stretch with things happening in it. Three additions to the strip, all read off
what the page already has:

1. **Event dots.** Each phase carries the dated acts of `Tegevused` done while
   the file was in a `Hetkeseis` of that phase — a `Menetluse areng`, a sent
   opinion, a `Kaasamine`, a `Märge` — drawn as small dots on the connector
   after the phase, each with its date and what it was in a tooltip.
2. **Year markers.** The year is written above the first item of each year
   on the strip, so a long strip says where 2026 begins without every date
   repeating it.
3. **The phase opens its acts.** «N tegevust» under a phase links to that
   period's group in `Tegevused` (`#etapp-<key>`), which the page opens and
   scrolls to: the decisions, files and links are there already.

Nothing here reads the database: the groups are the page's own
`EpisodeTimeline`, so the dots are exactly the rows the reader may see.
"""

from __future__ import annotations

import dataclasses
from typing import Any

from django.utils import timezone

from app.matters.episode_timeline import EpisodeTimeline, is_period_boundary
from app.matters.legal_process import RailStep

#: At most this many dots per phase; the link says how many there are.
MAX_DOTS = 8


def _what(item: Any) -> str:
    """A short name for one act, for the dot's tooltip."""
    development = item.procedural_development
    if development is not None:
        return str(getattr(development, "title", "") or "Menetluse areng")
    if item.submission is not None:
        return "Koja arvamus saadetud"
    position = item.external_position
    if position is not None:
        return str(getattr(position, "title", "") or "Väline seisukoht")
    if item.milestone is not None:
        return str(getattr(item.milestone, "what", "") or "")
    if item.is_entry:
        return item.entry_gist or str(getattr(item.entry, "title", "") or "Märge")
    return ""


@dataclasses.dataclass(frozen=True)
class PhaseActs:
    anchor: str
    count: int
    dots: tuple[tuple[str, str], ...]


def acts_by_phase(timeline: EpisodeTimeline | None, pattern: Any) -> dict[str, PhaseActs]:
    """Each phase's acts, from the page's grouped `Tegevused`, newest group's anchor."""
    if timeline is None or pattern is None:
        return {}
    gathered: dict[str, list[tuple[Any, str]]] = {}
    anchors: dict[str, str] = {}
    for group in timeline.groups:
        episode = group.episode
        stage = getattr(episode, "stage", None)
        if stage is None:
            continue
        phase = pattern.phase_for_stage(stage.key)
        if not phase:
            continue
        # Groups come newest first: the first seen is the latest period.
        anchors.setdefault(phase, group.dom_id)
        for item in group.items:
            if is_period_boundary(item):
                continue
            what = _what(item)
            if not what:
                continue
            gathered.setdefault(phase, []).append((item.occurred_at, what))
    result: dict[str, PhaseActs] = {}
    for phase, acts in gathered.items():
        acts.sort(key=lambda pair: pair[0])
        dots = tuple(
            (f"{timezone.localtime(when):%d.%m.%Y}" if when else "", what)
            for when, what in acts[-MAX_DOTS:]
        )
        result[phase] = PhaseActs(anchor=anchors[phase], count=len(acts), dots=dots)
    return result


def with_acts_and_years(steps: list[RailStep], acts: dict[str, PhaseActs]) -> list[RailStep]:
    """The strip's steps with their phase acts and a year where the year changes."""
    drawn: list[RailStep] = []
    year = None
    for step in steps:
        changes: dict[str, Any] = {}
        if step.sort_on is not None and step.sort_on.year != year:
            year = step.sort_on.year
            changes["year_label"] = str(year)
        phase = acts.get(step.key) if step.is_phase else None
        if phase is not None:
            changes["acts_anchor"] = phase.anchor
            changes["acts_count"] = phase.count
            changes["acts"] = phase.dots
        drawn.append(dataclasses.replace(step, **changes) if changes else step)
    return drawn
