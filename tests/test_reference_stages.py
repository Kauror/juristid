"""The reviewed `Hetkeseis` vocabulary, and what version 2.0 did to version 1.0.

Two things are pinned here and they are pinned for different reasons.

The **manifest** is reference data: the ten stages `workflow/0004` read out of
the workbook, the three labels the lawyers reworded, the one stage they added,
and the frozen copy of all of it inside `workflow/0007`. A frozen copy nobody
checks is a copy that silently stops matching.

The **preservation rules** are what makes a vocabulary change safe. No key
moved, no row was deleted, no Matter was reclassified, and the historical
reading of the workbook's own `rohkem pole tegevusi plaanis` is untouched —
each of those is asserted rather than described, because every one of them is a
way this change could have destroyed somebody's record.
"""

from __future__ import annotations

import importlib

import pytest

from app.matters.models import Matter
from app.workflow.enums import Disposition
from app.workflow.models import LegacyStatusMapping, StageVocabulary, resolve_legacy_status
from app.workflow.reference_stages import (
    NEW_STAGE_HELP_V2,
    NEW_STAGE_KEY_V2,
    NEW_STAGE_LABEL_V2,
    REFERENCE_STAGE_KEYS,
    REFERENCE_STAGE_VERSION,
    REFERENCE_STAGES,
    REFERENCE_STAGES_V1,
    REWORDED_STAGE_LABELS_V2,
    STAGE_REVIEW_VERIFIED_ON,
    STAGE_SOURCE_TITLE,
)
from app.workflow.selectors import selectable_stages, stages_including
from app.workflow.vocabulary import RAW_LABEL_TO_DISPOSITION, RAW_LABEL_TO_STAGE

#: `workflow/0007`, reached by name because a migration module is not a valid
#: identifier — the same way `tests/test_reference_legal_instruments.py` reaches
#: for the taxonomy ones.
REVIEW_MIGRATION = importlib.import_module(
    "app.workflow.migrations.0007_lawyer_reviewed_stage_vocabulary"
)

#: Version 1.0 — key, label, sort order, restated by hand. A test that looped
#: over the manifest and asserted it equals itself would pass through any edit.
REVIEWED_V1: tuple[tuple[str, str, int], ...] = (
    ("idea", "Idee", 10),
    ("consultation", "Kooskõlastusringil", 20),
    ("government", "Valitsuses", 30),
    ("parliament", "Riigikogus", 40),
    ("awaiting_entry", "Ootan jõustumist", 50),
    ("in_force", "Jõustunud", 60),
    ("estonian_eu_position", "Eesti seisukoht", 70),
    ("eu_procedure", "ELi menetluses", 80),
    ("awaiting_transposition", "Ootan ELi õiguse ülevõtmist", 90),
    ("other", "Muu", 100),
)

#: Version 2.0 — the eleven the lawyers reviewed on 2026-09-17, in their order.
REVIEWED_V2: tuple[tuple[str, str, int], ...] = (
    ("idea", "Idee", 10),
    ("consultation", "Kooskõlastusringil", 20),
    ("government", "Valitsuses", 30),
    ("parliament", "Riigikogus", 40),
    ("awaiting_entry", "Jõustumise ootel", 50),
    ("in_force", "Jõustunud", 60),
    ("estonian_eu_position", "Eesti seisukoht koostamisel", 70),
    ("eu_procedure", "ELi menetluses", 80),
    ("awaiting_transposition", "ELi õiguse ülevõtmise ootel", 90),
    ("no_further_work", "Rohkem ei tegele", 95),
    ("other", "Muu", 100),
)


# ---------------------------------------------------------------------------
# The manifest
# ---------------------------------------------------------------------------


def test_the_manifest_holds_both_reviewed_versions() -> None:
    assert [(stage.key, stage.label_et, stage.sort_order) for stage in REFERENCE_STAGES_V1] == list(
        REVIEWED_V1
    )
    assert [(stage.key, stage.label_et, stage.sort_order) for stage in REFERENCE_STAGES] == list(
        REVIEWED_V2
    )
    assert REFERENCE_STAGE_KEYS == tuple(key for key, _label, _order in REVIEWED_V2)
    assert REFERENCE_STAGE_VERSION == "2.0"


def test_the_provenance_is_stated() -> None:
    assert "HETKESEIS" in STAGE_SOURCE_TITLE
    assert STAGE_REVIEW_VERIFIED_ON == "2026-09-17"


def test_version_two_retires_nothing() -> None:
    """Every version-1.0 key is still offered. No stage needed retiring.

    The mechanism exists and works — `stages_including` and docs/adr/0032
    §Amendment — and this round simply has no use for it. A vocabulary change
    that quietly dropped a stage would strand every Matter standing in it.
    """
    assert set(key for key, _label, _order in REVIEWED_V1) <= set(REFERENCE_STAGE_KEYS)
    assert set(REFERENCE_STAGE_KEYS) - set(key for key, _l, _o in REVIEWED_V1) == {NEW_STAGE_KEY_V2}


def test_exactly_three_labels_were_reworded_and_no_key_moved() -> None:
    """The compatibility table, as an assertion rather than as prose."""
    v1 = {key: label for key, label, _order in REVIEWED_V1}
    v2 = {key: label for key, label, _order in REVIEWED_V2}
    moved = {key: (v1[key], v2[key]) for key in v1 if v1[key] != v2[key]}
    assert moved == {
        "awaiting_entry": ("Ootan jõustumist", "Jõustumise ootel"),
        "estonian_eu_position": ("Eesti seisukoht", "Eesti seisukoht koostamisel"),
        "awaiting_transposition": ("Ootan ELi õiguse ülevõtmist", "ELi õiguse ülevõtmise ootel"),
    }
    assert REWORDED_STAGE_LABELS_V2 == {key: new for key, (_old, new) in moved.items()}
    # Nothing but the wording: the sort order of every reused stage is its own.
    assert {key: order for key, _label, order in REVIEWED_V1} == {
        key: order for key, _label, order in REVIEWED_V2 if key != NEW_STAGE_KEY_V2
    }


def test_the_new_stage_sits_before_muu_without_renumbering_anything() -> None:
    assert (NEW_STAGE_KEY_V2, NEW_STAGE_LABEL_V2) == ("no_further_work", "Rohkem ei tegele")
    order = [key for key, _label, _o in REVIEWED_V2]
    assert order[-2:] == [NEW_STAGE_KEY_V2, "other"]


def test_the_migration_baseline_is_the_manifest() -> None:
    v1 = {key: label for key, label, _order in REVIEWED_V1}
    assert REVIEW_MIGRATION.REWORDED == {
        key: (v1[key], new) for key, new in REWORDED_STAGE_LABELS_V2.items()
    }
    assert sorted(REVIEW_MIGRATION.SEEDED_KEYS) == sorted(key for key, _l, _o in REVIEWED_V1)
    assert REVIEW_MIGRATION.NEW_KEY == NEW_STAGE_KEY_V2
    assert REVIEW_MIGRATION.NEW_LABEL == NEW_STAGE_LABEL_V2
    assert REVIEW_MIGRATION.NEW_HELP == NEW_STAGE_HELP_V2


# ---------------------------------------------------------------------------
# What the database holds after the migration
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_the_offered_vocabulary_is_the_reviewed_eleven_in_order() -> None:
    offered = list(selectable_stages())
    assert [(stage.key, stage.label_et, stage.sort_order) for stage in offered] == list(REVIEWED_V2)


@pytest.mark.django_db
def test_every_offered_stage_explains_itself() -> None:
    """The tooltip on `Uus teema` reads these, and a chip with none is the one
    a reader is least likely to recognise."""
    for stage in selectable_stages():
        assert stage.help_text.strip(), f"{stage.key!r} offers no explanation"


@pytest.mark.django_db
def test_the_reworded_stages_kept_their_key_row_and_explanation() -> None:
    """A reword is a display change. Nothing a Matter points at may move.

    The department's own description, supplied 2026-08-25 and transcribed by
    `workflow/0006`, survives the rewording of the label above it.
    """
    for key, new_label in REWORDED_STAGE_LABELS_V2.items():
        stage = StageVocabulary.objects.get(key=key)
        assert stage.label_et == new_label
        assert stage.is_active is True
        assert stage.help_text.strip()


@pytest.mark.django_db
def test_the_new_stage_is_not_a_closure_and_says_so() -> None:
    """`Rohkem ei tegele` is a Hetkeseis. Disposition is a separate concept.

    The product keeps stage, disposition and next action apart (AGENTS.md,
    master specification 3.4), and the one stage whose words a reader could
    reasonably take for a closure carries the sentence saying it is not.
    """
    stage = StageVocabulary.objects.get(key=NEW_STAGE_KEY_V2)
    assert stage.label_et == NEW_STAGE_LABEL_V2
    assert stage.is_active is True
    assert stage.is_provisional is False
    assert "ei ole" in stage.help_text or "mitte teema lõpetamine" in stage.help_text
    assert "Lõpeta teema" in stage.help_text
    # Every Menetlusliik: a file of any kind can be one this office stops
    # following, so the stage is not narrowed to a track.
    assert stage.applicable_tracks == []


@pytest.mark.django_db
def test_no_matter_was_moved_onto_the_new_stage() -> None:
    assert not Matter.objects.filter(stage__key=NEW_STAGE_KEY_V2).exists()


@pytest.mark.django_db
def test_the_historical_closure_label_is_still_read_as_a_disposition() -> None:
    """Adding the stage does not revise what the workbook meant.

    `rohkem pole tegevusi plaanis` has been read as `MONITORING_STOPPED` since
    `workflow/0004`, because it says Koda stopped working on the file. A new
    *stage* with neighbouring words is a different claim, and re-pointing the
    historical mapping at it would rewrite a decade of somebody else's filing.
    """
    mapping = resolve_legacy_status("rohkem pole tegevusi plaanis")
    assert mapping is not None
    assert mapping.stage is None
    assert mapping.disposition == Disposition.MONITORING_STOPPED
    assert RAW_LABEL_TO_DISPOSITION == {"rohkem pole tegevusi plaanis": "MONITORING_STOPPED"}
    assert NEW_STAGE_KEY_V2 not in RAW_LABEL_TO_STAGE.values()


@pytest.mark.django_db
def test_the_workbook_spellings_are_untouched_by_the_rewording() -> None:
    """`app.workflow.vocabulary` says what the register wrote, not what we offer.

    The three reworded stages are reached from the workbook by its own spelling
    — «ootan jõustumist» and the rest — and those must go on resolving, or a
    re-import would stop recognising the column it has always read.
    """
    for raw, key in RAW_LABEL_TO_STAGE.items():
        mapping = resolve_legacy_status(raw)
        assert mapping is not None, f"{raw!r} no longer resolves"
        assert mapping.stage is not None
        assert mapping.stage.key == key
    assert RAW_LABEL_TO_STAGE["ootan jõustumist"] == "awaiting_entry"
    assert RAW_LABEL_TO_STAGE["Eesti seisukoht"] == "estonian_eu_position"
    assert RAW_LABEL_TO_STAGE["ootan ELi õiguse ülevõtmist"] == "awaiting_transposition"
    assert LegacyStatusMapping.objects.filter(raw_label="ootan jõustumist").exists()


@pytest.mark.django_db
def test_a_matter_standing_in_a_retired_stage_is_still_offered_it() -> None:
    """The preservation contract PR #231 established, exercised on this round.

    No stage is retired here, so this proves the mechanism rather than a
    retirement: retire one by hand and the Matter holding it keeps it, while no
    other Matter gains it as a choice (docs/adr/0032 §Amendment).
    """
    stage = StageVocabulary.objects.get(key="consultation")
    stage.is_active = False
    stage.save(update_fields=["is_active"])

    assert "consultation" not in [item.key for item in selectable_stages()]
    assert "consultation" in [item.key for item in stages_including(stage)]
    assert "consultation" not in [item.key for item in stages_including(None)]
    assert StageVocabulary.objects.filter(key="consultation").exists()
