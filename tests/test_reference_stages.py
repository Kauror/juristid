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


def test_version_two_is_the_same_ten_keys() -> None:
    """Nothing retired and nothing added — three labels moved, and that is all.

    The retirement mechanism exists and works (`stages_including`,
    docs/adr/0032 §Amendment) and this round has no use for it. A vocabulary
    change that quietly dropped a stage would strand every Matter standing in
    it; one that quietly added a *disposition* as a stage would put two
    different questions in one column.
    """
    assert set(REFERENCE_STAGE_KEYS) == {key for key, _label, _order in REVIEWED_V1}
    assert len(REFERENCE_STAGE_KEYS) == 10


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
def test_the_offered_vocabulary_is_the_reviewed_ten_in_order() -> None:
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
def test_rohkem_ei_tegele_is_a_disposition_and_never_a_stage() -> None:
    """The boundary ADR 0032 draws, asserted where it could have been crossed.

    The feedback asked for «Rohkem ei tegele» as a Hetkeseis. `Hetkeseis` says
    where the *external* process stands; *Koda has stopped working on this* is a
    statement about this office, and the product models it as
    `Disposition.MONITORING_STOPPED`. A stage meaning the second would put two
    questions in one column and leave every surface reading it unable to tell
    which had been answered.

    The workbook has agreed since 2011: its own `rohkem pole tegevusi plaanis`
    is read as that disposition by `workflow/0004` and not as a stage.
    """
    assert not StageVocabulary.objects.filter(key="no_further_work").exists()
    labels = set(StageVocabulary.objects.values_list("label_et", flat=True))
    assert "Rohkem ei tegele" not in labels

    mapping = resolve_legacy_status("rohkem pole tegevusi plaanis")
    assert mapping is not None
    assert mapping.stage is None
    assert mapping.disposition == Disposition.MONITORING_STOPPED
    assert RAW_LABEL_TO_DISPOSITION == {"rohkem pole tegevusi plaanis": "MONITORING_STOPPED"}
    assert "no_further_work" not in RAW_LABEL_TO_STAGE.values()


@pytest.mark.django_db
def test_the_concept_already_has_a_lawyer_facing_action() -> None:
    """«Koda ei tegele edasi» on `Lõpeta teema`, «Loobuti» in the composer.

    Implemented against disposition rather than Hetkeseis, which is the point:
    there was no gap for this round to fill, only a boundary to leave alone.
    """
    from app.matters.forms import CLOSURE_CHOICES, COMPOSER_CLOSURE_CHOICES

    assert (Disposition.MONITORING_STOPPED.value, "Koda ei tegele edasi") in CLOSURE_CHOICES
    assert (Disposition.MONITORING_STOPPED.value, "Loobuti") in COMPOSER_CLOSURE_CHOICES


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
