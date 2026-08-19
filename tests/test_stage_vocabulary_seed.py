"""The seeded `Hetkeseis` vocabulary.

The live workbook carries eleven raw values. Ten are procedural stages; the
eleventh describes closure. Conflating them is the specific mistake this seed
exists to prevent (master specification 3.4, 11.2).
"""

from __future__ import annotations

import pytest

from app.workflow.enums import Disposition
from app.workflow.models import LegacyStatusMapping, StageVocabulary, resolve_legacy_status

pytestmark = pytest.mark.django_db

CANONICAL_KEYS = {
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
}

RAW_WORKBOOK_LABELS = {
    "idee",
    "kooskõlastusringil",
    "valitsuses",
    "Riigikogus",
    "ootan jõustumist",
    "jõustunud",
    "Eesti seisukoht",
    "ELi menetluses",
    "ootan ELi õiguse ülevõtmist",
    "rohkem pole tegevusi plaanis",
    "muu",
}


def test_ten_canonical_stages_are_seeded():
    keys = set(StageVocabulary.objects.values_list("key", flat=True))
    assert CANONICAL_KEYS <= keys


def test_all_eleven_raw_workbook_labels_are_mapped():
    labels = set(LegacyStatusMapping.objects.values_list("raw_label", flat=True))
    assert RAW_WORKBOOK_LABELS <= labels


def test_ten_labels_map_to_stages():
    stage_mappings = LegacyStatusMapping.objects.filter(
        raw_label__in=RAW_WORKBOOK_LABELS, stage__isnull=False
    )
    assert stage_mappings.count() == 10


def test_the_closure_label_is_not_a_stage():
    """`rohkem pole tegevusi plaanis` says Koda stopped, not where the bill is."""
    mapping = resolve_legacy_status("rohkem pole tegevusi plaanis")
    assert mapping is not None
    assert mapping.stage is None
    assert mapping.disposition == Disposition.MONITORING_STOPPED


def test_in_force_is_a_stage_and_does_not_close_anything():
    """An act entering into force does not end Koda's work on the file."""
    mapping = resolve_legacy_status("jõustunud")
    assert mapping.stage is not None
    assert mapping.stage.key == "in_force"
    assert mapping.disposition == ""


def test_every_seeded_mapping_is_generic_so_eras_can_still_diverge():
    """Stage 0's era-aware scheme stays usable: these are fallbacks, not locks."""
    seeded = LegacyStatusMapping.objects.filter(raw_label__in=RAW_WORKBOOK_LABELS)
    assert all(mapping.is_generic for mapping in seeded)


def test_an_era_specific_mapping_still_wins_over_the_seed():
    stage = StageVocabulary.objects.get(key="government")
    LegacyStatusMapping.objects.create(raw_label="muu", source_era="2014", stage=stage)

    assert resolve_legacy_status("muu", "2014").stage.key == "government"
    assert resolve_legacy_status("muu", "2026").stage.key == "other"


def test_stages_are_marked_provisional_until_the_workshop_confirms_them():
    """The list matches the workbook; the wording is still the department's call."""
    seeded = StageVocabulary.objects.filter(key__in=CANONICAL_KEYS)
    assert all(stage.is_provisional for stage in seeded)


def test_stages_have_a_deterministic_display_order():
    ordered = list(
        StageVocabulary.objects.filter(key__in=CANONICAL_KEYS).values_list("key", flat=True)
    )
    assert ordered[0] == "idea"
    assert ordered[-1] == "other"
