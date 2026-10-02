"""The reviewed `Hetkeseis` vocabulary, and what version 2.0 did to version 1.0.

Two things are pinned here and they are pinned for different reasons.

The **manifest** is reference data: the ten stages `workflow/0004` read out of
the workbook, the three labels the lawyers reworded, and the frozen copy of both
inside `workflow/0007`. A frozen copy nobody checks is a copy that silently
stops matching.

The **preservation rules** are what makes a vocabulary change safe. No key
moved, no row was deleted, no row was added, no Matter was reclassified, and
`rohkem pole tegevusi plaanis` is still read as a *disposition* — each of those
is asserted rather than described, because every one of them is a way this
change could have destroyed somebody's record or blurred a boundary the product
draws on purpose.

**Version 3.0 (docs/adr/0131 §9)** adds one row, `monitoring_stopped` —
«Rohkem ei tegele» — and moves nothing else. The register's own words are still
read as the disposition they always were: the new stage is a current product
decision, never a rereading of history.
"""

from __future__ import annotations

import importlib

import pytest

from app.workflow.enums import Disposition
from app.workflow.models import LegacyStatusMapping, StageVocabulary, resolve_legacy_status
from app.workflow.reference_stages import (
    REFERENCE_STAGE_KEYS,
    REFERENCE_STAGE_VERSION,
    REFERENCE_STAGES,
    REFERENCE_STAGES_V1,
    REFERENCE_STAGES_V2,
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
#: `workflow/0010`, which adds version 3.0's one row.
MONITORING_STOPPED_MIGRATION = importlib.import_module(
    "app.workflow.migrations.0010_monitoring_stopped_stage"
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

#: Version 2.0 — the same ten, three of them reworded, on 2026-09-17.
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
    ("other", "Muu", 100),
)

#: Version 3.0 — version 2.0 unchanged and «Rohkem ei tegele» last, 2026-10-02.
REVIEWED_V3: tuple[tuple[str, str, int], ...] = (
    *REVIEWED_V2,
    ("monitoring_stopped", "Rohkem ei tegele", 110),
)


# ---------------------------------------------------------------------------
# The manifest
# ---------------------------------------------------------------------------


def test_the_manifest_holds_both_reviewed_versions() -> None:
    assert [(stage.key, stage.label_et, stage.sort_order) for stage in REFERENCE_STAGES_V1] == list(
        REVIEWED_V1
    )
    assert [(stage.key, stage.label_et, stage.sort_order) for stage in REFERENCE_STAGES_V2] == list(
        REVIEWED_V2
    )
    assert [(stage.key, stage.label_et, stage.sort_order) for stage in REFERENCE_STAGES] == list(
        REVIEWED_V3
    )
    assert REFERENCE_STAGE_KEYS == tuple(key for key, _label, _order in REVIEWED_V3)
    assert REFERENCE_STAGE_VERSION == "3.0"


def test_the_provenance_is_stated() -> None:
    assert "HETKESEIS" in STAGE_SOURCE_TITLE
    assert STAGE_REVIEW_VERIFIED_ON == "2026-09-17"


def test_version_three_keeps_the_ten_and_adds_one() -> None:
    """Nothing retired; version 3.0 adds «Rohkem ei tegele» and nothing else.

    The retirement mechanism exists and works (`stages_including`,
    docs/adr/0032 §Amendment) and no round has used it. A vocabulary change that
    quietly dropped a stage would strand every Matter standing in it.
    """
    assert set(REFERENCE_STAGE_KEYS) == {key for key, _label, _order in REVIEWED_V1} | {
        "monitoring_stopped"
    }
    assert len(REFERENCE_STAGE_KEYS) == 11


def test_the_monitoring_stopped_migration_is_the_manifest() -> None:
    """`workflow/0010`'s frozen copy agrees with the manifest, and touches `workflow` alone."""
    assert MONITORING_STOPPED_MIGRATION.KEY == "monitoring_stopped"
    assert MONITORING_STOPPED_MIGRATION.LABEL == "Rohkem ei tegele"
    assert MONITORING_STOPPED_MIGRATION.SORT_ORDER == 110
    assert MONITORING_STOPPED_MIGRATION.Migration.dependencies == [
        ("workflow", "0009_next_action_values_are_checked")
    ]


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
    # Nothing but the wording: every sort order is its version-1.0 one.
    assert {key: order for key, _label, order in REVIEWED_V1} == {
        key: order for key, _label, order in REVIEWED_V2
    }


def test_the_migration_baseline_is_the_manifest() -> None:
    v1 = {key: label for key, label, _order in REVIEWED_V1}
    assert REVIEW_MIGRATION.REWORDED == {
        key: (v1[key], new) for key, new in REWORDED_STAGE_LABELS_V2.items()
    }
    assert sorted(REVIEW_MIGRATION.SEEDED_KEYS) == sorted(key for key, _l, _o in REVIEWED_V1)
    # The migration touches `workflow` and nothing else, in both directions: a
    # data migration that queries another app in its *reverse* asks it of
    # whatever state that app happens to be rewound to, which CI proved.
    assert REVIEW_MIGRATION.Migration.dependencies == [
        ("workflow", "0006_stage_help_from_the_department")
    ]


# ---------------------------------------------------------------------------
# What the database holds after the migration
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_the_offered_vocabulary_is_the_reviewed_eleven_in_order() -> None:
    offered = list(selectable_stages())
    assert [(stage.key, stage.label_et, stage.sort_order) for stage in offered] == list(REVIEWED_V3)


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
def test_rohkem_ei_tegele_is_a_stage_now_and_history_is_not_reread() -> None:
    """docs/adr/0131 §9 supersedes the boundary ADR 0032 drew here — going forward only.

    «Rohkem ei tegele» is a current stage, `monitoring_stopped`, and choosing it
    closes the Matter with `Disposition.MONITORING_STOPPED`. The workbook's own
    `rohkem pole tegevusi plaanis` is still read by `workflow/0004` as that
    disposition and not as the stage: an import that suddenly found the stage in
    2016 rows would be inventing a fact about 2016.
    """
    stage = StageVocabulary.objects.get(key="monitoring_stopped")
    assert (stage.label_et, stage.sort_order, stage.is_active) == ("Rohkem ei tegele", 110, True)
    assert stage.help_text.strip()

    mapping = resolve_legacy_status("rohkem pole tegevusi plaanis")
    assert mapping is not None
    assert mapping.stage is None
    assert mapping.disposition == Disposition.MONITORING_STOPPED
    assert RAW_LABEL_TO_DISPOSITION == {"rohkem pole tegevusi plaanis": "MONITORING_STOPPED"}
    assert "monitoring_stopped" not in RAW_LABEL_TO_STAGE.values()


@pytest.mark.django_db
def test_the_disposition_vocabulary_is_unchanged_beside_it() -> None:
    """The closure vocabulary is not retired with `Lõpeta teema` (docs/adr/0131 §11)."""
    from app.matters.forms import CLOSURE_CHOICES

    assert (Disposition.MONITORING_STOPPED.value, "Koda ei tegele edasi") in CLOSURE_CHOICES
    assert Disposition.MONITORING_STOPPED.label == "Koda lõpetas jälgimise"


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
