"""`Õigusakt -> Hetkeseis` guidance on `Uus teema` (docs/adr/0130 §4–§7).

The helper is pure — keys in, a verdict out — so most of this file needs no
database. What it holds down:

* the owner-approved matrix, row by row, by stable key;
* two states only, and `Määramata` and `Muu` never dimmed;
* several instruments combine by **union**, never intersection;
* an instrument the matrix has no row for dims nothing;
* the matrix names only keys the two vocabularies actually have, and covers
  every instrument `Uus teema` offers.

That this is guidance and not validation — the server accepting an atypical
combination — is `tests/test_uus_teema_guided_intake.py`, which owns the
create path.
"""

from __future__ import annotations

import json

import pytest

from app.taxonomy.legal_instruments import OFFERED_LEGAL_INSTRUMENT_KEYS
from app.workflow.reference_stages import REFERENCE_STAGE_KEYS
from app.workflow.stage_guidance import (
    ALWAYS_TYPICAL_STAGE_KEYS,
    TYPICAL_STAGES_BY_INSTRUMENT,
    is_stage_dimmed,
    stage_guidance_payload,
    typical_stage_keys,
)

STAGES = REFERENCE_STAGE_KEYS
MAARAMATA = None


def dimmed(*instruments: str) -> set[str]:
    return {stage for stage in STAGES if is_stage_dimmed(stage, instruments)}


# ---------------------------------------------------------------------------
# The matrix keeps to the vocabularies it is written against
# ---------------------------------------------------------------------------


def test_the_expected_keys_are_the_current_vocabularies():
    """The keys the owner's matrix was written against are the ones main has.

    If either vocabulary gains or loses a key, this fails first and the matrix
    is revisited deliberately rather than going quietly out of date.
    """
    assert set(OFFERED_LEGAL_INSTRUMENT_KEYS) == {
        "vtk",
        "seadus",
        "maarus",
        "koja-ettepanek",
        "strateegia-arengukava-tegevuskava",
        "muu-siseriiklik",
        "eli-konsultatsioon",
        "direktiiv",
        "el-maarus",
        "muu-eli-dokument",
    }
    assert set(STAGES) == {
        "idea",
        "consultation",
        "government",
        "parliament",
        "awaiting_entry",
        "in_force",
        "estonian_eu_position",
        "eu_procedure",
        "awaiting_transposition",
        "other",
        # Version 3.0 (docs/adr/0131 §9): never dimmed, like «Muu».
        "monitoring_stopped",
    }


def test_monitoring_stopped_is_never_dimmed():
    """«Rohkem ei tegele» fits every instrument: Koda may stop on any file."""
    assert "monitoring_stopped" in ALWAYS_TYPICAL_STAGE_KEYS
    for instrument in OFFERED_LEGAL_INSTRUMENT_KEYS:
        assert not is_stage_dimmed("monitoring_stopped", [instrument])


def test_every_offered_instrument_has_a_row_and_nothing_else_does():
    assert set(TYPICAL_STAGES_BY_INSTRUMENT) == set(OFFERED_LEGAL_INSTRUMENT_KEYS)


def test_every_stage_named_is_a_real_stage_key():
    named = set(ALWAYS_TYPICAL_STAGE_KEYS).union(*TYPICAL_STAGES_BY_INSTRUMENT.values())
    assert named <= set(STAGES)


# ---------------------------------------------------------------------------
# The owner-approved matrix, row by row
# ---------------------------------------------------------------------------

EU_ONLY = {"estonian_eu_position", "eu_procedure", "awaiting_transposition"}
EVERYTHING_BUT = lambda *normal: set(STAGES) - set(normal) - ALWAYS_TYPICAL_STAGE_KEYS  # noqa: E731


@pytest.mark.parametrize(
    ("instrument", "expected_dimmed"),
    [
        ("vtk", EVERYTHING_BUT("idea")),
        ("seadus", EU_ONLY),
        ("maarus", EU_ONLY | {"parliament"}),
        ("koja-ettepanek", EVERYTHING_BUT("idea")),
        (
            "strateegia-arengukava-tegevuskava",
            EVERYTHING_BUT("idea", "consultation", "government"),
        ),
        ("muu-siseriiklik", EU_ONLY),
        ("eli-konsultatsioon", EVERYTHING_BUT("estonian_eu_position", "eu_procedure")),
        (
            "direktiiv",
            EVERYTHING_BUT(
                "estonian_eu_position", "eu_procedure", "awaiting_entry", "awaiting_transposition"
            ),
        ),
        (
            "el-maarus",
            EVERYTHING_BUT("estonian_eu_position", "eu_procedure", "awaiting_entry", "in_force"),
        ),
        (
            "muu-eli-dokument",
            EVERYTHING_BUT(
                "estonian_eu_position",
                "eu_procedure",
                "awaiting_entry",
                "in_force",
                "awaiting_transposition",
            ),
        ),
    ],
)
def test_the_owner_approved_matrix(instrument, expected_dimmed):
    assert dimmed(instrument) == expected_dimmed


def test_no_instrument_dims_nothing():
    assert dimmed() == set()
    assert typical_stage_keys([]) is None
    assert typical_stage_keys([""]) is None


def test_vtk():
    assert not is_stage_dimmed("idea", ["vtk"])
    assert is_stage_dimmed("parliament", ["vtk"])
    assert not is_stage_dimmed("other", ["vtk"])


def test_seadus():
    assert not is_stage_dimmed("parliament", ["seadus"])
    assert is_stage_dimmed("eu_procedure", ["seadus"])


def test_maarus():
    assert not is_stage_dimmed("government", ["maarus"])
    assert is_stage_dimmed("parliament", ["maarus"])


def test_eli_konsultatsioon():
    assert not is_stage_dimmed("estonian_eu_position", ["eli-konsultatsioon"])
    assert not is_stage_dimmed("eu_procedure", ["eli-konsultatsioon"])
    assert is_stage_dimmed("government", ["eli-konsultatsioon"])


def test_direktiiv():
    assert not is_stage_dimmed("eu_procedure", ["direktiiv"])
    assert not is_stage_dimmed("awaiting_transposition", ["direktiiv"])
    assert is_stage_dimmed("parliament", ["direktiiv"])


def test_an_eu_regulation_is_not_transposed():
    """Directly applicable — the owner said so explicitly."""
    assert not is_stage_dimmed("in_force", ["el-maarus"])
    assert is_stage_dimmed("awaiting_transposition", ["el-maarus"])


def test_muu_eli_dokument():
    assert not is_stage_dimmed("awaiting_transposition", ["muu-eli-dokument"])


# ---------------------------------------------------------------------------
# Union, never intersection
# ---------------------------------------------------------------------------


def test_a_directive_and_the_law_transposing_it_combine_by_union():
    chosen = ["direktiiv", "seadus"]

    assert not is_stage_dimmed("eu_procedure", chosen)
    assert not is_stage_dimmed("parliament", chosen)
    assert not is_stage_dimmed("awaiting_transposition", chosen)


def test_a_vtk_and_the_law_that_follows_it_combine_by_union():
    assert dimmed("vtk", "seadus") == dimmed("seadus") == EU_ONLY


def test_a_stage_is_dimmed_only_when_atypical_for_every_instrument():
    # `parliament` is atypical for a regulation and typical for a law.
    assert is_stage_dimmed("parliament", ["maarus"])
    assert not is_stage_dimmed("parliament", ["maarus", "seadus"])
    # Order does not matter, and neither does a repeated key.
    assert dimmed("seadus", "direktiiv") == dimmed("direktiiv", "seadus", "seadus")


def test_union_is_the_union_of_the_rows():
    for first in OFFERED_LEGAL_INSTRUMENT_KEYS:
        for second in OFFERED_LEGAL_INSTRUMENT_KEYS:
            assert dimmed(first, second) == dimmed(first) & dimmed(second)


# ---------------------------------------------------------------------------
# What is never dimmed
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("instrument", OFFERED_LEGAL_INSTRUMENT_KEYS)
def test_muu_is_normal_for_every_instrument(instrument):
    assert not is_stage_dimmed("other", [instrument])


def test_muu_is_normal_for_every_combination():
    for first in OFFERED_LEGAL_INSTRUMENT_KEYS:
        for second in OFFERED_LEGAL_INSTRUMENT_KEYS:
            assert not is_stage_dimmed("other", [first, second])


@pytest.mark.parametrize("instrument", OFFERED_LEGAL_INSTRUMENT_KEYS)
def test_maaramata_is_normal_for_every_instrument(instrument):
    assert not is_stage_dimmed(MAARAMATA, [instrument])
    assert not is_stage_dimmed("", [instrument])


def test_an_instrument_without_a_row_dims_nothing():
    """Missing guidance must never read as guidance that something is unusual."""
    assert dimmed("eelnou") == set()
    assert dimmed("eelnou", "vtk") == set()
    assert typical_stage_keys(["eelnou"]) is None


# ---------------------------------------------------------------------------
# What the page's script receives
# ---------------------------------------------------------------------------


def test_the_payload_is_the_matrix_and_is_deterministic():
    payload = stage_guidance_payload()

    assert json.dumps(payload) == json.dumps(stage_guidance_payload())
    assert payload["always"] == ["monitoring_stopped", "other"]
    assert {key: set(stages) for key, stages in payload["instruments"].items()} == {
        key: set(stages) for key, stages in TYPICAL_STAGES_BY_INSTRUMENT.items()
    }
    # Keys only — the payload carries no label a reword could break.
    assert "Riigikogus" not in json.dumps(payload, ensure_ascii=False)


# ---------------------------------------------------------------------------
# The conservative prefill (docs/adr/0130, amendment of 2026-10-09)
# ---------------------------------------------------------------------------


def test_only_an_instrument_with_exactly_one_meaningful_stage_chooses_one():
    """VTK and the Chamber's own proposal are ideas; nothing else is decided by
    its instrument alone, so nothing else is chosen."""
    from app.workflow.stage_guidance import prefill_stage_by_instrument

    assert prefill_stage_by_instrument() == {"koja-ettepanek": "idea", "vtk": "idea"}


def test_the_prefill_travels_to_uus_teema_only():
    from app.workflow.stage_guidance import STAGE_PREFILL_NOTE

    assert "prefill" not in stage_guidance_payload()
    created = stage_guidance_payload(prefill=True)
    assert created["prefill"] == {"koja-ettepanek": "idea", "vtk": "idea"}
    assert created["prefill_note"] == STAGE_PREFILL_NOTE
    # Everything else is the same payload.
    assert {key: value for key, value in created.items() if not key.startswith("prefill")} == (
        stage_guidance_payload()
    )
