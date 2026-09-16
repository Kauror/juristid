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
    DOMESTIC_LEGAL_INSTRUMENT_KEYS,
    EU_LEGAL_INSTRUMENT_KEYS,
    LEGAL_INSTRUMENT_SOURCE_DISTINCT_SPELLINGS,
    LEGAL_INSTRUMENT_SOURCE_NON_EMPTY,
    LEGAL_INSTRUMENT_SOURCE_TITLE,
    LEGAL_INSTRUMENT_SOURCE_VERIFIED_ON,
    OFFERED_LEGAL_INSTRUMENT_KEYS,
    OTHER_LEGAL_INSTRUMENT_KEY,
    OTHER_LEGAL_INSTRUMENT_KEYS,
    REFERENCE_LEGAL_INSTRUMENT_KEYS,
    REFERENCE_LEGAL_INSTRUMENT_TYPES_V1,
    REFERENCE_LEGAL_INSTRUMENT_VERSION,
    RETIRED_LEGAL_INSTRUMENT_KEYS_V2,
    UNMAPPABLE_RAW_VALUES,
    canonical_legal_instrument_keys,
)
from app.taxonomy.models import LegalInstrumentType
from app.taxonomy.vocabulary import selectable_legal_instrument_types

#: The seed migration's frozen baseline. Imported by name because a migration
#: module is not a valid identifier, exactly as
#: `tests/test_reference_data_foundation.py` reaches for the PolicyArea ones.
SEED_MIGRATION = importlib.import_module("app.taxonomy.migrations.0006_seed_legal_instrument_types")

#: Version 2.0's migration, reached the same way.
REVIEW_MIGRATION = importlib.import_module(
    "app.taxonomy.migrations.0008_lawyer_reviewed_legal_instruments"
)

# ---------------------------------------------------------------------------
# The reviewed vocabulary
# ---------------------------------------------------------------------------

#: Version 1.0 — key, label, sort order, restated by hand.
#:
#: Deliberately a second copy rather than a loop over the manifest. A test that
#: iterated the manifest and asserted it equals itself would pass through any
#: edit; this one fails, which is what a reviewed vocabulary needs from its
#: test.
REVIEWED_V1: tuple[tuple[str, str, int], ...] = (
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

#: Version 2.0 — the ten types the lawyers reviewed on 2026-09-17, in the order
#: they gave, restated by hand for the reason version 1.0 is.
REVIEWED_V2: tuple[tuple[str, str, int], ...] = (
    ("vtk", "VTK", 10),
    ("seadus", "Seadus", 20),
    ("maarus", "Määrus", 30),
    ("koja-ettepanek", "Koja ettepanek või pöördumine", 40),
    ("strateegia-arengukava-tegevuskava", "Strateegia, arengukava või tegevuskava", 50),
    ("muu-siseriiklik", "Muu siseriiklik", 60),
    ("eli-konsultatsioon", "ELi konsultatsioon", 70),
    ("direktiiv", "ELi direktiiv", 80),
    ("el-maarus", "ELi määrus", 90),
    ("muu-eli-dokument", "Muu ELi dokument", 100),
)


def test_the_manifest_holds_both_reviewed_versions() -> None:
    assert [
        (item.key, item.label_et, item.sort_order) for item in REFERENCE_LEGAL_INSTRUMENT_TYPES_V1
    ] == list(REVIEWED_V1)
    assert OFFERED_LEGAL_INSTRUMENT_KEYS == tuple(key for key, _label, _order in REVIEWED_V2)
    assert REFERENCE_LEGAL_INSTRUMENT_VERSION == "2.0"


def test_the_whole_vocabulary_is_version_one_plus_the_new_rows() -> None:
    """Retiring a type never removes it from what the module knows.

    `REFERENCE_LEGAL_INSTRUMENT_TYPES` means *every row*, which is what the
    alias table and `canonical_legal_instrument_keys` need: a historical
    `ÕIGUSAKT` cell reading «strateegia» must go on resolving to the retired
    concept it has always meant.
    """
    assert set(REFERENCE_LEGAL_INSTRUMENT_KEYS) == {
        *(key for key, _label, _order in REVIEWED_V1),
        *(key for key, _label, _order in REVIEWED_V2),
    }
    assert len(REFERENCE_LEGAL_INSTRUMENT_KEYS) == 22
    assert set(OFFERED_LEGAL_INSTRUMENT_KEYS) <= set(REFERENCE_LEGAL_INSTRUMENT_KEYS)


def test_the_retired_twelve_are_exactly_what_version_two_stopped_offering() -> None:
    """The compatibility table, as an assertion rather than as prose."""
    v1 = {key for key, _label, _order in REVIEWED_V1}
    assert set(RETIRED_LEGAL_INSTRUMENT_KEYS_V2) == v1 - set(OFFERED_LEGAL_INSTRUMENT_KEYS)
    assert len(RETIRED_LEGAL_INSTRUMENT_KEYS_V2) == 12


def test_the_two_reused_rows_keep_their_key_and_change_only_their_label() -> None:
    """`Direktiiv` and `EL määrus` are the reviewed concepts under a clearer name.

    Reused rather than replaced because each is objectively identical — a
    directive has no domestic equivalent and «EL määrus» and «ELi määrus» are
    the same three words — so a new row beside either would split one
    classification in two. `Konsultatsioon` is deliberately not among them: it
    covers a domestic public consultation, which `ELi konsultatsioon` does not.
    """
    v1 = {key: label for key, label, _order in REVIEWED_V1}
    v2 = {key: label for key, label, _order in REVIEWED_V2}
    reused = {key for key in v2 if key in v1}
    assert reused == {"vtk", "seadus", "maarus", "direktiiv", "el-maarus"}
    assert {key for key in reused if v1[key] != v2[key]} == {"direktiiv", "el-maarus"}
    assert v2["direktiiv"] == "ELi direktiiv"
    assert v2["el-maarus"] == "ELi määrus"
    assert "konsultatsioon" in RETIRED_LEGAL_INSTRUMENT_KEYS_V2


def test_the_two_groups_partition_the_offered_vocabulary() -> None:
    """Siseriiklik or ELiga seotud, for every offered type and for nothing else.

    What makes `derived_track` safe is that the two sets cover the ten offered
    types exactly: a retired version-1.0 row is in neither, so a Matter carrying
    one derives no track rather than a guessed one.
    """
    assert DOMESTIC_LEGAL_INSTRUMENT_KEYS.isdisjoint(EU_LEGAL_INSTRUMENT_KEYS)
    assert DOMESTIC_LEGAL_INSTRUMENT_KEYS | EU_LEGAL_INSTRUMENT_KEYS == set(
        OFFERED_LEGAL_INSTRUMENT_KEYS
    )
    assert len(DOMESTIC_LEGAL_INSTRUMENT_KEYS) == 6
    assert len(EU_LEGAL_INSTRUMENT_KEYS) == 4
    retired = set(RETIRED_LEGAL_INSTRUMENT_KEYS_V2)
    assert retired.isdisjoint(DOMESTIC_LEGAL_INSTRUMENT_KEYS | EU_LEGAL_INSTRUMENT_KEYS)


def test_every_offered_eu_type_says_so_in_its_own_label() -> None:
    """The whole reason Menetlusliik stopped being a second question.

    A lawyer picking `ELi direktiiv` can see that they have answered
    «ELiga seotud»; one picking `Direktiiv` could not (docs/adr/0089 §4).
    """
    labels = {key: label for key, label, _order in REVIEWED_V2}
    for key in EU_LEGAL_INSTRUMENT_KEYS:
        assert labels[key].startswith("ELi ") or labels[key].endswith("ELi dokument")
    for key in DOMESTIC_LEGAL_INSTRUMENT_KEYS:
        assert "ELi" not in labels[key]


def test_the_provenance_is_stated() -> None:
    """Changing the vocabulary without saying where it came from fails review."""
    assert "ÕIGUSAKT" in LEGAL_INSTRUMENT_SOURCE_TITLE
    assert LEGAL_INSTRUMENT_SOURCE_VERIFIED_ON == "2026-09-10"
    assert LEGAL_INSTRUMENT_SOURCE_NON_EMPTY == 2418
    assert LEGAL_INSTRUMENT_SOURCE_DISTINCT_SPELLINGS == 58


def test_muu_is_a_real_row_and_version_two_has_three_of_them() -> None:
    """The one deliberate difference from the Valdkonnad control it copies.

    `Muu` is a `LegalInstrumentType`, not a checkbox beside one, because 1130 of
    the register's historical answers are literally «muu» and a vocabulary with
    nowhere to put them would lose the department's own answer (docs/adr/0070).

    Version 2.0 splits the answer along the one axis it cares about, so what
    reveals the free-text box is a *set* of rows — and version 1.0's `Muu` is
    still in it, because Matters filed before the review carry it
    (docs/adr/0089 §3).
    """
    assert REFERENCE_LEGAL_INSTRUMENT_TYPES_V1[-1].key == OTHER_LEGAL_INSTRUMENT_KEY
    assert REFERENCE_LEGAL_INSTRUMENT_TYPES_V1[-1].label_et == "Muu"
    assert OTHER_LEGAL_INSTRUMENT_KEYS == {
        OTHER_LEGAL_INSTRUMENT_KEY,
        "muu-siseriiklik",
        "muu-eli-dokument",
    }
    # The two version-2.0 escape hatches are offered; version 1.0's is not.
    assert OTHER_LEGAL_INSTRUMENT_KEYS - set(OFFERED_LEGAL_INSTRUMENT_KEYS) == {
        OTHER_LEGAL_INSTRUMENT_KEY
    }
    # Each is last in its own group, so an answer meaning "none of these" reads
    # after the kinds it is none of.
    assert OFFERED_LEGAL_INSTRUMENT_KEYS[-1] == "muu-eli-dokument"
    assert OFFERED_LEGAL_INSTRUMENT_KEYS[5] == "muu-siseriiklik"


def test_the_migration_baselines_are_the_manifest() -> None:
    """Each frozen copy and its manifest agree, or the next change is a guess.

    A migration deliberately holds a literal copy — one that imported today's
    manifest would replay as something else every time the manifest is edited —
    and this is what keeps both copies honest.
    """
    assert [(key, label, order) for key, label, _description, order in SEED_MIGRATION.BASELINE] == [
        (item.key, item.label_et, item.sort_order) for item in REFERENCE_LEGAL_INSTRUMENT_TYPES_V1
    ]
    assert [description for _key, _label, description, _order in SEED_MIGRATION.BASELINE] == [
        item.description for item in REFERENCE_LEGAL_INSTRUMENT_TYPES_V1
    ]

    assert sorted(REVIEW_MIGRATION.WITHDRAWN) == sorted(RETIRED_LEGAL_INSTRUMENT_KEYS_V2)
    v1 = {key: label for key, label, _order in REVIEWED_V1}
    assert REVIEW_MIGRATION.WITHDRAWN == {key: v1[key] for key in RETIRED_LEGAL_INSTRUMENT_KEYS_V2}

    new_rows = [(key, label, order) for key, label, _description, order in REVIEW_MIGRATION.NEW]
    by_key = {key: (key, label, order) for key, label, order in REVIEWED_V2}
    assert new_rows == [by_key[key] for key, _label, _order, _sort in REVIEW_MIGRATION.NEW]

    # Both directions of every reword, and both sort orders of every move.
    v2 = {key: label for key, label, _order in REVIEWED_V2}
    order_v1 = {key: order for key, _label, order in REVIEWED_V1}
    order_v2 = {key: order for key, _label, order in REVIEWED_V2}
    for key, (old_label, _old_text, new_label, _new_text) in REVIEW_MIGRATION.RELABELLED.items():
        assert old_label == v1[key]
        assert new_label == v2[key]
    for key, (old_order, new_order) in REVIEW_MIGRATION.RENUMBERED.items():
        assert old_order == order_v1[key]
        assert new_order == order_v2[key]


@pytest.mark.django_db
def test_every_version_one_row_is_still_there_after_the_review() -> None:
    """Deactivated, never deleted, never renamed and never remapped.

    The twelve keep their row, their key and their description; the two reused
    ones keep their key and change only what the label says. A `Matter` filed
    under any of the seventeen still points at the row it always pointed at.
    """
    rows = {row.key: row for row in LegalInstrumentType.objects.all()}
    for key, label, _order in REVIEWED_V1:
        assert key in rows, f"{key!r} was deleted by the review"
        assert rows[key].description.strip()
        if key in RETIRED_LEGAL_INSTRUMENT_KEYS_V2:
            assert rows[key].label_et == label, "a retired row is never renamed"
            assert rows[key].is_active is False
        else:
            assert rows[key].is_active is True


@pytest.mark.django_db
def test_the_offered_vocabulary_is_the_reviewed_ten_in_order() -> None:
    offered = list(selectable_legal_instrument_types())
    assert [(item.key, item.label_et, item.sort_order) for item in offered] == list(REVIEWED_V2)
    assert all(item.description.strip() for item in offered)


@pytest.mark.django_db
def test_retiring_a_type_keeps_its_row() -> None:
    retired = LegalInstrumentType.objects.get(key="vtk")
    retired.is_active = False
    retired.save(update_fields=["is_active"])

    assert "vtk" not in [item.key for item in selectable_legal_instrument_types()]
    # Deactivated, never deleted: the row and every relation on it survive.
    assert LegalInstrumentType.objects.filter(key="vtk").exists()


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
