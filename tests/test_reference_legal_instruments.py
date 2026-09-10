"""The Õigusakt vocabulary, and the one seam that reads the historical column.

Two things are pinned here and they are pinned for different reasons.

The **vocabulary** is reference data: seventeen rows with reviewed keys, labels,
order and active state, seeded by a migration whose baseline is a frozen copy of
the manifest. The tests below hold the copy and the manifest to each other,
because a frozen copy nobody checks is a copy that silently stops matching.

The **mapping** is the part that reads somebody else's decade of filing. Every
distinct spelling the 2026-09 read-only survey found in `Tööd eelnõudega.xlsx`
is listed here with the canonical keys it must produce, and the list is
exhaustive: 58 spellings, 56 mapped and 2 deliberately not. This file is
therefore also the reviewable record of the mapping — a reader who wants to
check what `S, M` becomes reads the parametrisation rather than the alias table.

**No row-level workbook content is committed.** What is below is the distinct
*vocabulary* of the column — the spellings themselves — and never a Matter
title, a counterparty, a date or a row number (task §10).
"""

from __future__ import annotations

import importlib

import pytest

from app.taxonomy.legal_instruments import (
    LEGAL_INSTRUMENT_SOURCE_DISTINCT_SPELLINGS,
    LEGAL_INSTRUMENT_SOURCE_NON_EMPTY,
    LEGAL_INSTRUMENT_SOURCE_TITLE,
    LEGAL_INSTRUMENT_SOURCE_VERIFIED_ON,
    OTHER_LEGAL_INSTRUMENT_KEY,
    REFERENCE_LEGAL_INSTRUMENT_KEYS,
    REFERENCE_LEGAL_INSTRUMENT_TYPES,
    REFERENCE_LEGAL_INSTRUMENT_VERSION,
    UNMAPPABLE_RAW_VALUES,
    canonical_legal_instrument_keys,
)
from app.taxonomy.models import LegalInstrumentType
from app.taxonomy.vocabulary import selectable_legal_instrument_types

#: The seed migration's frozen baseline. Imported by name because a migration
#: module is not a valid identifier, exactly as
#: `tests/test_reference_data_foundation.py` reaches for the PolicyArea ones.
SEED_MIGRATION = importlib.import_module("app.taxonomy.migrations.0006_seed_legal_instrument_types")

# ---------------------------------------------------------------------------
# The reviewed vocabulary
# ---------------------------------------------------------------------------

#: key, label, sort order — the reviewed sequence, restated by hand.
#:
#: Deliberately a second copy rather than a loop over the manifest. A test that
#: iterated `REFERENCE_LEGAL_INSTRUMENT_TYPES` and asserted it equals itself
#: would pass through any edit; this one fails, which is what a reviewed
#: vocabulary needs from its test.
REVIEWED: tuple[tuple[str, str, int], ...] = (
    ("seadus", "Seadus", 10),
    ("maarus", "Määrus", 20),
    ("vtk", "VTK", 30),
    ("eelnou", "Eelnõu", 40),
    ("direktiiv", "Direktiiv", 50),
    ("el-maarus", "EL määrus", 60),
    ("el-teatis", "EL teatis", 70),
    ("konsultatsioon", "Konsultatsioon", 80),
    ("strateegia", "Strateegia", 90),
    ("arengukava", "Arengukava", 100),
    ("tegevuskava", "Tegevuskava", 110),
    ("visioon", "Visioon", 120),
    ("korraldus", "Korraldus", 130),
    ("kaskkiri", "Käskkiri", 140),
    ("ettepanek", "Ettepanek", 150),
    ("kusitlus", "Küsitlus", 160),
    ("muu", "Muu", 200),
)


def test_the_manifest_is_the_reviewed_vocabulary() -> None:
    assert [
        (item.key, item.label_et, item.sort_order) for item in REFERENCE_LEGAL_INSTRUMENT_TYPES
    ] == list(REVIEWED)
    assert REFERENCE_LEGAL_INSTRUMENT_KEYS == tuple(key for key, _label, _order in REVIEWED)
    assert REFERENCE_LEGAL_INSTRUMENT_VERSION == "1.0"


def test_the_provenance_is_stated() -> None:
    """Changing the vocabulary without saying where it came from fails review."""
    assert "ÕIGUSAKT" in LEGAL_INSTRUMENT_SOURCE_TITLE
    assert LEGAL_INSTRUMENT_SOURCE_VERIFIED_ON == "2026-09-10"
    assert LEGAL_INSTRUMENT_SOURCE_NON_EMPTY == 2418
    assert LEGAL_INSTRUMENT_SOURCE_DISTINCT_SPELLINGS == 58


def test_muu_is_last_and_is_a_real_row() -> None:
    """The one deliberate difference from the Valdkonnad control it copies.

    `Muu` is a `LegalInstrumentType`, not a checkbox beside one, because 1130 of
    the register's historical answers are literally «muu» and a vocabulary with
    nowhere to put them would lose the department's own answer (docs/adr/0070).
    """
    assert REFERENCE_LEGAL_INSTRUMENT_KEYS[-1] == OTHER_LEGAL_INSTRUMENT_KEY
    other = REFERENCE_LEGAL_INSTRUMENT_TYPES[-1]
    assert other.label_et == "Muu"
    # A gap before it, so adding a type does not have to renumber anything to
    # keep `Muu` at the end of the row.
    assert other.sort_order - REFERENCE_LEGAL_INSTRUMENT_TYPES[-2].sort_order >= 20


def test_the_migration_baseline_is_the_manifest() -> None:
    """The frozen copy and the manifest agree, or the next change is a guess.

    The migration deliberately holds a literal copy — a historical migration
    that imported today's manifest would replay as something else every time
    the manifest is edited — and this is what keeps the copy honest.
    """
    assert [(key, label, order) for key, label, _description, order in SEED_MIGRATION.BASELINE] == [
        (item.key, item.label_et, item.sort_order) for item in REFERENCE_LEGAL_INSTRUMENT_TYPES
    ]
    assert [description for _key, _label, description, _order in SEED_MIGRATION.BASELINE] == [
        item.description for item in REFERENCE_LEGAL_INSTRUMENT_TYPES
    ]


@pytest.mark.django_db
def test_the_vocabulary_is_seeded_in_reviewed_order() -> None:
    rows = list(LegalInstrumentType.objects.order_by("sort_order", "label_et"))
    assert [(row.key, row.label_et, row.sort_order) for row in rows] == list(REVIEWED)
    assert all(row.is_active for row in rows)
    assert all(row.description.strip() for row in rows), "every type says what falls inside it"


@pytest.mark.django_db
def test_the_offered_vocabulary_is_the_active_one_in_order() -> None:
    offered = list(selectable_legal_instrument_types())
    assert [item.key for item in offered] == list(REFERENCE_LEGAL_INSTRUMENT_KEYS)

    retired = LegalInstrumentType.objects.get(key="visioon")
    retired.is_active = False
    retired.save(update_fields=["is_active"])

    assert "visioon" not in [item.key for item in selectable_legal_instrument_types()]
    # Deactivated, never deleted: the row and every relation on it survive.
    assert LegalInstrumentType.objects.filter(key="visioon").exists()


# ---------------------------------------------------------------------------
# Raw ÕIGUSAKT -> canonical keys
# ---------------------------------------------------------------------------

#: Every distinct spelling the 2026-09 survey found, and what it must become.
#:
#: 58 entries. The counts beside them are not asserted — they are here so a
#: reviewer can see the weight behind each decision without opening the
#: workbook — and the *first* seven groups carry almost all of the 2418 cells.
SPELLINGS: tuple[tuple[str, tuple[str, ...]], ...] = (
    # -- Seadus (1188 cells across every spelling) --------------------------
    ("seadus", ("seadus",)),
    ("Seadus", ("seadus",)),
    ("S", ("seadus",)),
    ("S ", ("seadus",)),
    # -- Määrus -------------------------------------------------------------
    ("määrus", ("maarus",)),
    ("Määrus", ("maarus",)),
    ("Määrus ", ("maarus",)),
    ("määrused", ("maarus",)),
    ("M", ("maarus",)),
    # -- Muu ----------------------------------------------------------------
    ("muu", ("muu",)),
    ("Muu", ("muu",)),
    ("muu ", ("muu",)),
    # -- VTK ----------------------------------------------------------------
    ("VTK", ("vtk",)),
    ("VTK ", ("vtk",)),
    # A draft of a VTK is both, and the multi-select is what lets it say so.
    ("VTK eelnõu", ("vtk", "eelnou")),
    # -- Direktiiv ----------------------------------------------------------
    ("direktiiv", ("direktiiv",)),
    ("D", ("direktiiv",)),
    ("EL direktiiv", ("direktiiv",)),
    ("El direktiiv", ("direktiiv",)),
    ("ELi direktiiv", ("direktiiv",)),
    ("EL direktiivid", ("direktiiv",)),
    # -- EL määrus ----------------------------------------------------------
    ("EL määrus", ("el-maarus",)),
    ("El määrus", ("el-maarus",)),
    ("ELi määrus", ("el-maarus",)),
    ("EL määrused", ("el-maarus",)),
    ("EL M", ("el-maarus",)),
    # A Commission regulation is an EU regulation.
    ("EK määrus", ("el-maarus",)),
    # -- Eelnõu -------------------------------------------------------------
    ("eelnõu", ("eelnou",)),
    ("Eelnõu", ("eelnou",)),
    ("Seaduse muutmise seaduse eelnõu", ("seadus", "eelnou")),
    # -- Konsultatsioon -----------------------------------------------------
    ("EL konsultatsioon", ("konsultatsioon",)),
    ("ELi konsultatsioon", ("konsultatsioon",)),
    ("EK konsultatsioon", ("konsultatsioon",)),
    ("EL avalik konsultatsioon", ("konsultatsioon",)),
    ("EL avalik konsultatsioon ", ("konsultatsioon",)),
    ("avalik konsultatsioon", ("konsultatsioon",)),
    # -- EL teatis ----------------------------------------------------------
    ("EL teatis", ("el-teatis",)),
    ("EK teatis", ("el-teatis",)),
    # -- Strateegia and the rest --------------------------------------------
    ("strateegia", ("strateegia",)),
    ("Strateegia", ("strateegia",)),
    ("EL strateegia", ("strateegia",)),
    ("EK strateegia", ("strateegia",)),
    ("arengukava", ("arengukava",)),
    ("tegevuskava", ("tegevuskava",)),
    ("visioon", ("visioon",)),
    ("korraldus", ("korraldus",)),
    ("VV korraldus", ("korraldus",)),
    ("käskkiri", ("kaskkiri",)),
    ("Käskkiri", ("kaskkiri",)),
    ("ettepanek", ("ettepanek",)),
    ("küsitlus", ("kusitlus",)),
    # -- Combinations: two answers, never one concatenated pseudo-value -----
    ("S, M", ("seadus", "maarus")),
    ("D, M", ("maarus", "direktiiv")),
    ("direktiiv ja määrus", ("maarus", "direktiiv")),
    ("direktiiv+määrus", ("maarus", "direktiiv")),
    ("direktiiv/määrus", ("maarus", "direktiiv")),
    # -- Names no instrument at all (§11) -----------------------------------
    ("EL", ()),
    ("sisendi küsimine VTK ettevalmistamiseks ", ()),
)


@pytest.mark.parametrize(("raw", "expected"), SPELLINGS, ids=[raw for raw, _ in SPELLINGS])
def test_every_workbook_spelling_maps_deterministically(
    raw: str, expected: tuple[str, ...]
) -> None:
    assert canonical_legal_instrument_keys(raw) == expected


def test_the_survey_is_covered_exhaustively() -> None:
    """58 spellings surveyed, 58 spellings listed. Coverage is the whole claim.

    A mapping that is right about the values somebody remembered to test is not
    a mapping anybody can run a backfill from.
    """
    assert len(SPELLINGS) == LEGAL_INSTRUMENT_SOURCE_DISTINCT_SPELLINGS
    assert len({raw for raw, _ in SPELLINGS}) == LEGAL_INSTRUMENT_SOURCE_DISTINCT_SPELLINGS

    mapped = [raw for raw, keys in SPELLINGS if keys]
    unmapped = [raw for raw, keys in SPELLINGS if not keys]
    assert len(mapped) == 56
    assert len(unmapped) == 2
    assert {raw.strip() for raw in unmapped} == set(UNMAPPABLE_RAW_VALUES)
    assert all(reason.strip() for reason in UNMAPPABLE_RAW_VALUES.values())


def test_every_mapped_key_is_in_the_vocabulary() -> None:
    for raw, keys in SPELLINGS:
        for key in keys:
            assert key in REFERENCE_LEGAL_INSTRUMENT_KEYS, f"{raw!r} maps to unknown {key!r}"


@pytest.mark.parametrize(
    "variants",
    [
        ("seadus", "Seadus", "SEADUS", "  seadus  ", "seadus "),
        ("määrus", "Määrus", "MÄÄRUS", "maarus", " määrus "),
        ("VTK", "vtk", "Vtk", "VTK "),
    ],
)
def test_case_whitespace_and_diacritics_do_not_create_concepts(variants: tuple[str, ...]) -> None:
    """The whole reason a canonical vocabulary exists (task §5)."""
    readings = {canonical_legal_instrument_keys(value) for value in variants}
    assert len(readings) == 1, f"{variants} read {readings}"
    assert readings != {()}


def test_the_combined_values_produce_several_keys_in_vocabulary_order() -> None:
    """Order is the vocabulary's, not the cell's, so two spellings of one
    answer compare equal.
    """
    assert canonical_legal_instrument_keys("S, M") == ("seadus", "maarus")
    assert canonical_legal_instrument_keys("M, S") == ("seadus", "maarus")
    assert canonical_legal_instrument_keys("määrus ja seadus") == ("seadus", "maarus")


def test_a_repeated_answer_is_one_key() -> None:
    assert canonical_legal_instrument_keys("seadus, S") == ("seadus",)


def test_blank_reads_as_no_answer_rather_than_as_muu() -> None:
    """Blank means the kind was not written down. `Muu` means it was, and was
    none of the listed kinds. Reading one as the other invents 283 answers.
    """
    for value in ("", "   ", "\n"):
        assert canonical_legal_instrument_keys(value) == ()


def test_an_unrecognised_part_leaves_the_whole_value_unmapped() -> None:
    """Half of a combined answer is a worse record than none of it.

    Nothing downstream could tell a half-read cell from a fully-read one, and a
    reviewer looking at a Matter classified `Seadus` would have no way to know
    the source also said something the parser could not read.
    """
    assert canonical_legal_instrument_keys("seadus, kosmoseharta") == ()
    assert canonical_legal_instrument_keys("kosmoseharta") == ()


def test_the_unmappable_values_keep_their_reason() -> None:
    """`EL` and the input request are not `Muu`, and the file says why.

    `Muu` is an answer — *the kind is some other kind*. Neither of these says
    that: one names a scope and the other names an activity, and recording
    either as `Muu` would put a decision in the department's mouth (§11).
    """
    assert canonical_legal_instrument_keys("EL") == ()
    assert canonical_legal_instrument_keys("sisendi küsimine VTK ettevalmistamiseks") == ()
    assert canonical_legal_instrument_keys("muu") == ("muu",)
