"""Historical status labels are interpreted per era.

The register's vocabulary changed materially between 2011 and 2026, so the same
`Hetkeseis` text does not necessarily mean the same thing in every year. A
globally unique label would have forced one meaning onto every era
(master specification 2.1, 19.3).
"""

from __future__ import annotations

import pytest
from django.db import IntegrityError, transaction

from app.workflow.enums import Disposition
from app.workflow.models import LegacyStatusMapping, resolve_legacy_status
from tests import factories

pytestmark = pytest.mark.django_db


def test_the_same_label_can_mean_different_things_in_different_eras():
    monitoring = factories.StageFactory(key="jalgimisel", label_et="Jälgimisel")

    LegacyStatusMapping.objects.create(
        raw_label="rohkem pole tegevusi plaanis",
        source_era="2023-2024",
        stage=monitoring,
        reviewed_by="Osakonnajuht",
    )
    LegacyStatusMapping.objects.create(
        raw_label="rohkem pole tegevusi plaanis",
        source_era="2025",
        disposition=Disposition.MONITORING_STOPPED,
        reviewed_by="Osakonnajuht",
    )

    older = resolve_legacy_status("rohkem pole tegevusi plaanis", "2023-2024")
    newer = resolve_legacy_status("rohkem pole tegevusi plaanis", "2025")

    assert older.stage == monitoring
    assert older.disposition == ""
    assert newer.stage is None
    assert newer.disposition == Disposition.MONITORING_STOPPED


def test_an_exact_era_takes_precedence_over_the_generic_mapping():
    # A label the migration does not seed, so this exercises resolution rather
    # than colliding with the reviewed vocabulary.
    label = "ootab täpsustamist"
    stage = factories.StageFactory()
    generic = LegacyStatusMapping.objects.create(raw_label=label, disposition=Disposition.OTHER)
    specific = LegacyStatusMapping.objects.create(raw_label=label, source_era="2026", stage=stage)

    assert resolve_legacy_status(label, "2026") == specific
    assert resolve_legacy_status(label, "2019") == generic
    assert resolve_legacy_status(label) == generic
    assert generic.is_generic is True
    assert specific.is_generic is False


def test_a_label_with_no_mapping_at_all_resolves_to_nothing():
    assert resolve_legacy_status("tundmatu väärtus", "2019") is None


def test_one_interpretation_per_label_per_era():
    LegacyStatusMapping.objects.create(raw_label="menetluses", source_era="2019")
    with pytest.raises(IntegrityError), transaction.atomic():
        LegacyStatusMapping.objects.create(raw_label="menetluses", source_era="2019")


def test_the_same_label_in_another_era_is_accepted():
    LegacyStatusMapping.objects.create(raw_label="menetluses", source_era="2019")
    LegacyStatusMapping.objects.create(raw_label="menetluses", source_era="2020")
    LegacyStatusMapping.objects.create(raw_label="menetluses")
    assert LegacyStatusMapping.objects.filter(raw_label="menetluses").count() == 3


def test_the_generic_mapping_is_itself_unique():
    LegacyStatusMapping.objects.create(raw_label="menetluses")
    with pytest.raises(IntegrityError), transaction.atomic():
        LegacyStatusMapping.objects.create(raw_label="menetluses")


def test_a_label_maps_to_a_stage_or_a_closure_reason_but_never_both():
    stage = factories.StageFactory()
    with pytest.raises(IntegrityError), transaction.atomic():
        LegacyStatusMapping.objects.create(
            raw_label="vastuoluline",
            source_era="2025",
            stage=stage,
            disposition=Disposition.COMPLETED,
        )


# --------------------------------------------------------------------------
# The vocabulary exists twice on purpose: once frozen inside the seed migration,
# once importable for the offline inspector, which has no database. Duplication
# is the cost of a migration that cannot change meaning retroactively; drift is
# what these tests exist to prevent.
# --------------------------------------------------------------------------


def _seed_module():
    import importlib

    return importlib.import_module("app.workflow.migrations.0004_seed_stage_vocabulary")


def test_the_importable_vocabulary_matches_the_seed_migration():
    from app.workflow import vocabulary

    seed = _seed_module()
    assert vocabulary.RAW_LABEL_TO_STAGE == seed.RAW_LABEL_TO_STAGE
    assert vocabulary.RAW_LABEL_TO_DISPOSITION == seed.RAW_LABEL_TO_DISPOSITION


def test_the_controlled_vocabulary_has_the_workbooks_eleven_labels():
    from app.workflow import vocabulary

    assert len(vocabulary.CONTROLLED_LABELS) == 11
    assert "ootan ELi õiguse ülevõtmist" in vocabulary.CONTROLLED_LABELS


def test_the_closure_label_is_not_a_stage():
    from app.workflow import vocabulary

    assert "rohkem pole tegevusi plaanis" not in vocabulary.RAW_LABEL_TO_STAGE
    assert "rohkem pole tegevusi plaanis" in vocabulary.RAW_LABEL_TO_DISPOSITION


def test_free_text_variants_seen_in_the_real_register_are_not_silently_mapped():
    """The real 2024 sheet contains these beside the controlled values.

    `rohkem tegevusi pole` sits one word away from the controlled
    `rohkem pole tegevusi plaanis`. Deciding those are the same value is a
    lawyer's call, and the importer is not allowed to make it.
    """
    from app.workflow import vocabulary

    for variant in (
        "Riigikogus 2. lugemisel",
        "riigikogus 2. lugemisel",
        "kinnitatud",
        "rohkem tegevusi pole",
        "rohkem tegevusi pole plaanis",
    ):
        assert not vocabulary.is_known_label(variant)
        assert resolve_legacy_status(variant, "2023-2024") is None
