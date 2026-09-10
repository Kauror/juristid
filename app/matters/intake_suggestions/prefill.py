"""What «prefill» means, in one place.

A HIGH-confidence suggestion may appear already filled in an *empty* form
control. It is not saved; the person pressing Salvesta or `Loo teema` is the
confirmation, through the same services every edit goes through. Every rule
below has the same shape (brief §18):

* the canonical value is empty — an existing value is never overwritten,
  however confident the analysis is;
* the field has exactly one HIGH candidate and no conflict — or, for a
  multi-valued field, HIGH candidates and no conflict;
* the candidate is not already the value.

**The title is the one rule that differs by surface, and it is deliberate.**

On an existing Matter no title is ever pre-filled, and the reason is a fact
about the record rather than about the text. The module used to fill the box
when the stored title equalled ``title_from_filename`` of some document the
Matter held, on the theory that such a title was intake's mechanical fallback.
It is not a sound theory: a lawyer may write «Pakendiseaduse muutmise seaduse
eelnõu» and somebody may later attach ``Pakendiseaduse_muutmise_seaduse_eelnõu.pdf``,
and the two strings then match though the title is a person's.

Nothing in the record separates the two cases. ``register_incoming`` chooses
between the typed title and the fallback and stores only the result;
``MATTER_CREATED`` carries the reference, the record mode, the origin and the
data class, and nothing about where the title came from. The absence of a
``MATTER_TITLE_CHANGED`` event proves the title has not been *edited* since
creation, but not that creation invented it. So on `Muuda teemat` the
classification is not made, and a document heading is offered with «Kasuta»
instead (docs/adr/0060, hardening §2.2).

**`Uus teema` is not that surface**, and the argument above does not reach it.
There is no stored title to classify, because there is no Matter: the control
is a box on an unsaved form, and the browser can see the one thing the record
never could — whether the person has typed in it. The rule is therefore
unconditional in the same way, and reads the other way round: a title a person
wrote is never replaced by a machine, *because the browser declines to write
into a control that is not empty and untouched* (static/js/app.js, brief §12).

So `prefill_initial` takes ``allow_title``. `Uus teema` passes it; `Muuda
teemat` does not, and its behaviour is byte-for-byte what it was. Both ask the
same question of the same analysis — exactly one HIGH candidate, no conflict —
so the two surfaces cannot come to disagree about which titles are strong.

A bound form — a submit that failed validation — never reaches this module.
What the person typed is what is re-rendered.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date
from typing import Any

from app.matters.intake_suggestions.analysis import CurrentValues
from app.matters.intake_suggestions.types import IntakeAnalysis, SuggestedField


def prefill_controls(analysis: IntakeAnalysis) -> list[tuple[str, str]]:
    """The ``(control name, control value)`` pairs a pre-fill would write.

    :func:`prefill_initial` answers *which* suggestions may fill a control, and
    hands the answer to a Django form as initial data. `Uus teema` needs the
    same answer as a pair of strings instead, because the form is already on
    the reader's screen and the values arrive afterwards — there is no
    unbound form left to give an initial to.

    So the decision is not made twice. This reads what
    :func:`prefill_initial` decided and translates it into what the control
    actually takes: an organisation's primary key, ``18.9.2026`` rather than
    ``2026-09-18``, a ``Track`` value, a ``PolicyArea`` key. Whether the
    control may be written to at all is the browser's question, asked of the
    live form, and it is answered "only if it is empty and untouched"
    (static/js/app.js, docs/adr/0064).

    Title is present here whenever the analysis it is given was decided with
    ``allow_title``. This function does not make that decision — it reads what
    `prefill_initial` decided — so a caller cannot obtain a title pre-fill by
    calling this one instead.
    """
    pairs: list[tuple[str, str]] = []
    for name, values in analysis.prefilled.items():
        suggestions = analysis.fields.get(name)
        if suggestions is None:  # pragma: no cover - defensive
            continue
        by_value = {candidate.value: candidate for candidate in suggestions.candidates}
        for value in values:
            candidate = by_value.get(value)
            if candidate is not None:
                pairs.append((name, candidate.control_value))
    return pairs


def prefill_initial(
    analysis: IntakeAnalysis,
    *,
    base: dict[str, Any],
    current: CurrentValues,
    allow_title: bool = False,
) -> tuple[dict[str, Any], IntakeAnalysis]:
    """Merge HIGH suggestions into a form's initial values.

    Returns the merged initial and the analysis annotated with what was
    pre-filled, so the panel can mark exactly those candidates «vormil
    eeltäidetud» and offer «Kasuta» on the rest.

    ``allow_title`` is `Uus teema`'s and defaults to off, so the surface that
    edits a saved Matter keeps the behaviour it has: the record cannot tell a
    title a person typed from one intake derived, so it offers rather than
    fills. On the unsaved form there is no such record and no such ambiguity —
    see this module's opening note.
    """
    initial = dict(base)
    prefilled: dict[str, tuple[str, ...]] = {}

    titles = analysis.fields.get(SuggestedField.TITLE)
    if allow_title and titles is not None and not current.title:
        # `prefill_candidate` is the whole rule: exactly one HIGH candidate and
        # no conflict. Two plausible formal titles across an envelope leave the
        # box empty and both on the panel with «Kasuta», which is the answer
        # when the documents themselves disagree about what the file is called
        # (brief §12).
        chosen = titles.prefill_candidate
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
