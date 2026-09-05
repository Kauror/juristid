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

**The title is never pre-filled at all**, and that is a deliberate retreat
from what this module first did. It used to fill the box when the stored
title equalled ``title_from_filename`` of some document the Matter held, on
the theory that such a title was intake's mechanical fallback. It is not a
sound theory. A lawyer may write «Pakendiseaduse muutmise seaduse eelnõu»
and somebody may later attach ``Pakendiseaduse_muutmise_seaduse_eelnõu.pdf``,
and the two strings then match though the title is a person's.

Nothing in the record separates the two cases. ``register_incoming`` chooses
between the typed title and the fallback and stores only the result;
``MATTER_CREATED`` carries the reference, the record mode, the origin and the
data class, and nothing about where the title came from. The absence of a
``MATTER_TITLE_CHANGED`` event proves the title has not been *edited* since
creation, but not that creation invented it.

So the classification is not made. A document heading is offered with
«Kasuta» and the person's title stays until they choose otherwise, which
costs one click on the ordinary intake path and makes the product's rule
unconditional: a title a person wrote can never be replaced by a machine,
because no title is (docs/adr/0060, hardening §2.2).

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

    # No title branch, on purpose. See this module's opening note: the record
    # cannot separate a title a person typed from the one intake derived, so
    # the strongest heading is offered rather than filled in.

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
