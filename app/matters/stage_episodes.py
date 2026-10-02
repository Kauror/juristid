"""`Hetkeseis` periods, read: what a Matter has held, may move to, and when.

The writes live in `app.matters.services` (`change_stage`, `stage_transition`,
`reopen_matter_into_stage`); this module only reads, so a form, a view and the
chronology can all ask it without importing the transaction machinery
(docs/adr/0131).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from django.utils import timezone

from app.matters.models import Matter, MatterStageEpisode
from app.workflow.models import StageVocabulary
from app.workflow.selectors import selectable_stages
from app.workflow.stage_flow import is_terminal, next_stage_keys, reopening_stage_keys


def episodes_of(matter: Matter) -> list[MatterStageEpisode]:
    """Every period this Matter has had, first first, with its stage."""
    return list(
        MatterStageEpisode.objects.filter(matter=matter)
        .select_related("stage")
        .order_by("sequence")
    )


def history_stage_keys(episodes: list[MatterStageEpisode]) -> list[str]:
    """The stages held, newest period first — what `stage_flow` reads as history."""
    return [episode.stage.key for episode in reversed(episodes)]


def _instrument_keys(matter: Matter) -> list[str]:
    return list(matter.legal_instruments.values_list("key", flat=True))


def _in_order(keys: list[str], stages: list[StageVocabulary]) -> list[StageVocabulary]:
    by_key = {stage.key: stage for stage in stages}
    return [by_key[key] for key in keys if key in by_key]


def offered_next_stages(
    matter: Matter, *, episodes: list[MatterStageEpisode] | None = None
) -> list[StageVocabulary]:
    """`+ Märge → Uus hetkeseis`, most likely first (`stage_flow.next_stage_keys`).

    An order over the active vocabulary, never a filter on what may be saved:
    the form still accepts every active stage, so a list drawn a moment before a
    colleague moved the file cannot refuse a correct answer.
    """
    held = episodes if episodes is not None else episodes_of(matter)
    stages = list(selectable_stages())
    keys = next_stage_keys(
        current_key=getattr(matter.stage, "key", None),
        history_keys=history_stage_keys(held),
        instrument_keys=_instrument_keys(matter),
        available_keys=[stage.key for stage in stages],
    )
    return _in_order(keys, stages)


def offered_reopening_stages(
    matter: Matter, *, episodes: list[MatterStageEpisode] | None = None
) -> list[StageVocabulary]:
    """«Ava uuesti» — the stages a closed Matter may be reopened into.

    Never one that ends a Matter, and never `Määramata`
    (`stage_flow.reopening_stage_keys`).
    """
    held = episodes if episodes is not None else episodes_of(matter)
    stages = list(selectable_stages())
    keys = reopening_stage_keys(
        current_key=getattr(matter.stage, "key", None),
        history_keys=history_stage_keys(held),
        instrument_keys=_instrument_keys(matter),
        available_keys=[stage.key for stage in stages],
    )
    return _in_order(keys, stages)


def _month(moment: datetime) -> str:
    """``10.26`` — the month and the year's last two digits, in Tallinn."""
    day = timezone.localtime(moment).date()
    return f"{day.month}.{day.year % 100:02d}"


def period_label(episode: MatterStageEpisode) -> str:
    """The compact period a `Teema käik` heading prints for one `Hetkeseis` period.

    ``5.26–10.26`` across months, ``10.26`` within one, ``alates 10.26`` for the
    period still current. **Only what is known is printed** (docs/adr/0131 §8):
    a period whose start nobody recorded prints ``–10.26`` once it has ended and
    nothing at all while it is still current — never the day of the migration or
    the day the Matter was created dressed up as its start.
    """
    start = _month(episode.started_at) if episode.started_at is not None else ""
    end = _month(episode.ended_at) if episode.ended_at is not None else ""
    if episode.is_current:
        return f"alates {start}" if start else ""
    if start and end:
        return start if start == end else f"{start}–{end}"
    if end:
        return f"–{end}"
    if start:
        return f"{start}–"
    return ""


def stage_choice_label(stage: Any) -> str:
    """What a stage option reads as where choosing it can end the Matter."""
    label = str(getattr(stage, "label_et", stage))
    return f"{label} — lõpetab teema" if is_terminal(getattr(stage, "key", None)) else label
