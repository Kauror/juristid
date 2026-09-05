"""What «prefill» means, in one place.

On a GET of the assisted review page, a HIGH-confidence suggestion may
appear already filled in an *empty* form control. It is not saved; the
person pressing Salvesta is the confirmation, through the same services
every edit goes through. Everything below is a rule about when the control
may be pre-filled, and each rule has the same shape (brief §18):

* the Matter's canonical value is empty — an existing value is never
  overwritten, however confident the analysis is;
* the field has exactly one HIGH candidate and no conflict — or, for a
  multi-valued field, HIGH candidates and no conflict;
* the candidate is not already the Matter's value.

The title is the one exception with a second condition: it is pre-filled
only when the stored title is the mechanical filename fallback intake wrote
(`app.matters.intake.title_from_filename`). A title a person typed stays,
and the content title is offered beside it (brief §19).

A bound form — a submit that failed validation — never reaches this module.
What the person typed is what is re-rendered.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date
from typing import Any

from app.matters.intake_suggestions.analysis import CurrentValues
from app.matters.intake_suggestions.types import IntakeAnalysis, SuggestedField


def prefill_initial(
    analysis: IntakeAnalysis, *, base: dict[str, Any], current: CurrentValues
) -> tuple[dict[str, Any], IntakeAnalysis]:
    """Merge HIGH suggestions into the edit form's initial values.

    Returns the merged initial and the analysis annotated with what was
    pre-filled, so the panel can mark exactly those candidates «vormil
    eeltäidetud» and offer «Kasuta» on the rest.
    """
    initial = dict(base)
    prefilled: dict[str, tuple[str, ...]] = {}

    title = analysis.fields.get(SuggestedField.TITLE)
    if title is not None and current.title_is_mechanical:
        chosen = title.prefill_candidate
        if chosen is not None:
            initial["title"] = chosen.value
            prefilled[SuggestedField.TITLE] = (chosen.value,)

    senders = analysis.fields.get(SuggestedField.SOURCE_ORGANISATIONS)
    if senders is not None and not current.source_organisation_ids:
        chosen = senders.prefill_candidate
        if chosen is not None:
            initial["source_organisations"] = [chosen.value]
            prefilled[SuggestedField.SOURCE_ORGANISATIONS] = (chosen.value,)

    deadline = analysis.fields.get(SuggestedField.RESPONSE_DEADLINE)
    if deadline is not None and current.response_deadline is None:
        chosen = deadline.prefill_candidate
        if chosen is not None:
            initial["response_deadline"] = date.fromisoformat(chosen.value)
            prefilled[SuggestedField.RESPONSE_DEADLINE] = (chosen.value,)

    track = analysis.fields.get(SuggestedField.TRACK)
    if track is not None and not current.track:
        chosen = track.prefill_candidate
        if chosen is not None:
            initial["track"] = chosen.value
            prefilled[SuggestedField.TRACK] = (chosen.value,)

    areas = analysis.fields.get(SuggestedField.POLICY_AREAS)
    if areas is not None and not current.policy_area_ids:
        chosen_many = areas.prefill_candidates
        if chosen_many:
            initial["policy_areas"] = [candidate.value for candidate in chosen_many]
            prefilled[SuggestedField.POLICY_AREAS] = tuple(c.value for c in chosen_many)

    fields = {
        name: replace(
            suggestions,
            candidates=tuple(
                replace(candidate, prefilled=candidate.value in prefilled.get(name, ()))
                for candidate in suggestions.candidates
            ),
        )
        for name, suggestions in analysis.fields.items()
    }
    return initial, replace(analysis, fields=fields, prefilled=prefilled)
