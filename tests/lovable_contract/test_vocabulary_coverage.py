"""LC-V1 — vocabulary coverage (audit §D.2, D.4 I-1): no database, milliseconds.

Every value Lovable's form or `save_topic` can write (`vocabulary.py`, read at
Lovable `fe9cc1d8`) is in exactly one of: mapped, or intentionally held with
the decision that says so. A value still waiting for the owner (LV-01 «Muu»,
LV-02 «Teadmata», LV-03 the opinion and link forms) is a strict xfail: it
turns green — and so fails the strict marker — the day its row in
`vocabulary.TREATMENT` records a decision, which is exactly when this test
must be revisited.

For a decided row, the engine is checked against the decision (the engine may
be imported here: this is the check, not the oracle).
"""

from __future__ import annotations

import datetime as dt

import pytest

from tests.lovable_contract import vocabulary
from tests.lovable_contract.vocabulary import HELD, MAPPED, PENDING, TREATMENT

CASES = [(v, value) for v in vocabulary.VOCABULARIES for value in vocabulary.values_of(v)]


def test_every_lovable_value_has_exactly_one_treatment():
    """LC-V1: the table covers each vocabulary exactly, and nothing else."""
    assert set(TREATMENT) == set(CASES)
    assert len(CASES) == len(set(CASES))
    assert {treatment for treatment, _ in TREATMENT.values()} <= {MAPPED, HELD, PENDING}
    assert vocabulary.LOVABLE_COMMIT == "fe9cc1d8"


def _engine_treatment(kind: str, value: str) -> tuple[str, str]:
    """What the engine does with one value, in the table's words."""
    from app.legacy_import.lovable_reading import STAGE_MAPPING, commencement_reading
    from app.legacy_import.lovable_sync import desired_outcome
    from app.taxonomy.legal_instruments import current_legal_instrument_keys

    if kind == "oigusakt":
        keys = current_legal_instrument_keys(value)
        return (MAPPED, keys[0]) if len(keys) == 1 else (HELD, "")
    if kind == "hetkeseis":
        stage = STAGE_MAPPING.get(value)
        return (MAPPED, stage) if stage else (HELD, "")
    if kind == "c_liik":
        kinds = set()
        for status in ("Riigikogus", "jõustunud"):
            read = commencement_reading({"liik": value, "kuupaev": "2099-01-01"}, status=status)
            kinds.add(read["kind"])
        kinds.discard("")
        return (MAPPED, "|".join(sorted(kinds))) if kinds else (HELD, "")
    if kind == "valja":
        text, _meaning = vocabulary.OPINION_STATUS_FORMS[value]
        desired, _hold = desired_outcome(
            {"sent": text, "sent_date": None, "addressee": ""}, today=dt.date(2099, 1, 1)
        )
        return (MAPPED, desired["outcome"]) if desired else (HELD, "")
    if kind == "onenote_scheme":
        # The engine's rule is inline in `plan_matter` (no pure reader to call):
        # it is exercised end to end by LC-U1 (`test_lc_lifecycle.py`). Here the
        # rule as the engine's own field policy states it.
        from app.legacy_import.lovable_sync import FIELD_POLICY

        assert "https" in FIELD_POLICY["onenote_link"].explanation
        return (MAPPED, "MatterSourceReference") if value == "https" else (HELD, "")
    raise AssertionError(kind)


def _params():
    for kind, value in CASES:
        treatment, detail = TREATMENT[(kind, value)]
        marks = (
            [pytest.mark.xfail(strict=True, reason=f"pending owner decision: {detail}")]
            if treatment == PENDING
            else []
        )
        yield pytest.param(kind, value, id=f"{kind}:{value}", marks=marks)


@pytest.mark.parametrize(("kind", "value"), list(_params()))
def test_each_lovable_value_is_mapped_or_intentionally_held(kind, value):
    """LC-V1: a decided value is treated by the engine exactly as decided.

    Contract: audit §D.2 LC-V1; approval 11.11 §1 (the step-type and stage rules
    are fixed, «never guessed»). A pending value fails here by design: no
    decision is recorded for it yet (LV-01, LV-02, LV-03)."""
    treatment, detail = TREATMENT[(kind, value)]
    assert treatment != PENDING, detail
    engine = _engine_treatment(kind, value)
    if treatment == MAPPED:
        assert engine == (MAPPED, detail)
    else:
        assert engine[0] == HELD, engine


@pytest.mark.parametrize(
    "kind_value",
    [k for k, (t, _) in TREATMENT.items() if t == PENDING],
    ids=lambda k: f"{k[0]}:{k[1]}",
)
def test_a_pending_value_is_held_today_never_guessed(kind_value):
    """LC-V1 / LC-U1: until the owner decides, the engine holds the value
    (fail closed) — it never maps it to something the owner did not choose."""
    kind, value = kind_value
    assert _engine_treatment(kind, value)[0] == HELD
