"""Which `Hetkeseis` normally fits which `Õigusakt` — guidance for `Uus teema`, never a rule.

The owner's approved matrix, after lawyer feedback (docs/adr/0130). When somebody
filing a new Teema ticks an `Õigusakt`, the `Hetkeseis` chips that do not normally
fit any ticked instrument are drawn **dimmed**. That is the whole effect.

What this module is not, stated because each one would be an easy next step:

* **Not validation.** The server accepts every valid combination exactly as it
  did before. Nothing here is imported by a form's `clean`, a service or a
  model, and no combination is refused, normalised or rewritten on save — real
  files do things the matrix calls unusual, and the lawyer who files one is
  right about it.
* **Not a filter.** No stage is hidden or disabled; a dimmed chip is clickable,
  focusable and, once chosen, looks exactly like any other chosen chip.
* **Not inference.** Nothing writes `Matter.stage` or `Matter.track` from this.
  `Õigusakt` describes the instrument and `Menetlusliik` the procedure, and
  neither is derived from the other (docs/adr/0090 §4).
* **Not about Valdkonnad.** No policy area is suggested, dimmed or filtered by
  anything here.

Two visual states only — normal and dimmed. No ranking, no score, no "recommended".

**Several instruments combine by union.** A stage stays normal when it is normal
for *at least one* ticked instrument and is dimmed only when it is atypical for
every one of them. One Teema can legitimately span a directive and the act that
transposes it, or a VTK and the law that follows it, and an intersection would
dim exactly the stages such a file moves through.

**Keys, never labels.** Instruments are `LegalInstrumentType.key`
(`app.taxonomy.legal_instruments`), stages `StageVocabulary.key`
(`app.workflow.reference_stages`). A label can be reworded by the department
without touching this file; a key cannot change without a migration, and
`tests/test_stage_guidance.py` holds this matrix to both vocabularies.

`Määramata` — no stage at all — is always normal, and so is `other` («Muu»):
"not decided yet" and "something else" are honest answers for every instrument.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

#: The stage keys that are never dimmed, whatever is ticked. `Määramata` is the
#: absence of a stage and has no key, so it is not in this set: a chip without a
#: stage key is simply never touched.
#:
#: «Rohkem ei tegele» joined «Muu» with version 3.0 of the vocabulary
#: (docs/adr/0131 §9): Koda deciding to stop is an honest answer for every
#: instrument, not one that fits some procedures and not others.
ALWAYS_TYPICAL_STAGE_KEYS: frozenset[str] = frozenset({"other", "monitoring_stopped"})

_DOMESTIC_LAW_STAGES = frozenset(
    {"idea", "consultation", "government", "parliament", "awaiting_entry", "in_force"}
)

#: ``instrument key -> stage keys that normally fit it``, as the owner approved
#: it (docs/adr/0130 §5). `other` is listed on every row for legibility; it is
#: also guaranteed by :data:`ALWAYS_TYPICAL_STAGE_KEYS`.
TYPICAL_STAGES_BY_INSTRUMENT: Mapping[str, frozenset[str]] = {
    "vtk": frozenset({"idea", "other"}),
    "seadus": _DOMESTIC_LAW_STAGES | {"other"},
    # A regulation is adopted by the government or a minister, not the Riigikogu.
    "maarus": (_DOMESTIC_LAW_STAGES - {"parliament"}) | {"other"},
    "koja-ettepanek": frozenset({"idea", "other"}),
    "strateegia-arengukava-tegevuskava": frozenset({"idea", "consultation", "government", "other"}),
    "muu-siseriiklik": _DOMESTIC_LAW_STAGES | {"other"},
    "eli-konsultatsioon": frozenset({"estonian_eu_position", "eu_procedure", "other"}),
    "direktiiv": frozenset(
        {
            "estonian_eu_position",
            "eu_procedure",
            "awaiting_entry",
            "awaiting_transposition",
            "other",
        }
    ),
    # Directly applicable: an EU regulation is not transposed, so
    # `awaiting_transposition` stays dimmed for it on purpose.
    "el-maarus": frozenset(
        {"estonian_eu_position", "eu_procedure", "awaiting_entry", "in_force", "other"}
    ),
    "muu-eli-dokument": frozenset(
        {
            "estonian_eu_position",
            "eu_procedure",
            "awaiting_entry",
            "in_force",
            "awaiting_transposition",
            "other",
        }
    ),
}


def typical_stage_keys(instrument_keys: Iterable[str]) -> frozenset[str] | None:
    """The stage keys that stay normal for these instruments, or ``None`` for "all of them".

    ``None`` when nothing is ticked — no guidance at all — and also when a ticked
    instrument has no row here. An instrument the matrix does not know (a type
    added to the vocabulary later, or a retired one) contributes *every* stage
    to the union rather than none: guidance that is missing must never read as
    guidance that something is unusual.
    """
    chosen = [key for key in instrument_keys if key]
    if not chosen:
        return None
    typical: set[str] = set(ALWAYS_TYPICAL_STAGE_KEYS)
    for key in chosen:
        row = TYPICAL_STAGES_BY_INSTRUMENT.get(key)
        if row is None:
            return None
        typical |= row
    return frozenset(typical)


def is_stage_dimmed(stage_key: str | None, instrument_keys: Iterable[str]) -> bool:
    """Whether a stage chip is drawn dimmed for this selection of instruments.

    ``stage_key`` is ``None`` or ``""`` for `Määramata`, which is never dimmed.
    The chip's *selected* state is not an input: a chosen chip is drawn as
    chosen whatever this answers, and that precedence is the stylesheet's.
    """
    if not stage_key or stage_key in ALWAYS_TYPICAL_STAGE_KEYS:
        return False
    typical = typical_stage_keys(instrument_keys)
    return typical is not None and stage_key not in typical


def stage_guidance_payload() -> dict[str, Any]:
    """The matrix as the page's script reads it, serialised with `json_script`.

    Plain lists, sorted, so the rendered page is deterministic. The script
    applies the same union rule as :func:`typical_stage_keys`, and the browser
    suite holds the two to each other.
    """
    return {
        "always": sorted(ALWAYS_TYPICAL_STAGE_KEYS),
        "instruments": {
            key: sorted(stages) for key, stages in sorted(TYPICAL_STAGES_BY_INSTRUMENT.items())
        },
    }
