"""Every data migration has been run over rows, not only over an empty schema.

The postgres-safety job migrated zero → leaf, stepped a deployment's state back
and forwards again — on an empty database. So the RunPython code it reversed and
re-applied ran over no data at all, and a data migration whose reverse or
forward breaks on real rows passed CI unless somebody had happened to write it
an executor test (ENG-052).

Two proofs now, and this file holds them complete:

* **The populated step-back.** The job seeds the leaf (`seed_e2e_data`) before
  stepping back, so every RunPython migration *inside* the step-back's range
  runs backwards and forwards over rows. The range is read from the workflow.
* **A named populated test** for each RunPython migration the range does not
  reach, or an approved exception with its reason.

A RunPython migration that is in neither — a new one, or one the range was moved
past — fails the build here. The list below cannot drift silently: the guard
compares it with the migrations on disk and with the workflow's own targets.
"""

from __future__ import annotations

import importlib
import re
from pathlib import Path

import pytest
from django.apps import apps as global_apps
from django.db import migrations
from django.db.migrations.loader import MigrationLoader

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"
STEP_BACK = "And forwards again from the state a deployment is in"
SEED_STEP = "A populated database to step back from"
FROM_ZERO = "A fresh database migrates from zero"

#: RunPython migrations the step-back does not reach, and the populated test that
#: proves each one. `(app, migration) -> "path::test"`.
POPULATED_PROOFS: dict[tuple[str, str], str] = {
    ("matters", "0008_multiple_source_organisations"): (
        "tests/test_multiple_senders_migration.py::"
        "test_reversing_refuses_once_a_matter_has_two_senders"
    ),
    ("organisations", "0002_recompute_organisation_keys"): (
        "tests/test_organisation_identity.py::"
        "test_the_key_migration_recomputes_the_derived_columns_both_ways"
    ),
    ("taxonomy", "0002_reference_policy_areas"): (
        "tests/test_migration_data_proof.py::"
        "test_reference_areas_reverse_and_reapply_over_classified_matters"
    ),
    ("taxonomy", "0003_working_policy_area_vocabulary"): (
        "tests/test_reference_data_foundation.py::"
        "test_reverse_never_takes_a_classified_area_with_it"
    ),
    ("workflow", "0004_seed_stage_vocabulary"): (
        "tests/test_migration_data_proof.py::"
        "test_stage_vocabulary_reverses_and_reapplies_over_matters_in_a_stage"
    ),
}

#: RunPython migrations deliberately proved by neither, with the reason. Empty,
#: and adding to it is a reviewed decision rather than a way to go green.
APPROVED_EXCEPTIONS: dict[tuple[str, str], str] = {}


def _runpython_migrations() -> set[tuple[str, str]]:
    loader = MigrationLoader(None, ignore_no_migrations=True)
    found = set()
    for key, migration in loader.disk_migrations.items():
        if not key[0] or not Path(
            importlib.import_module(migration.__module__).__file__
        ).is_relative_to(ROOT / "app"):
            continue
        if any(isinstance(operation, migrations.RunPython) for operation in migration.operations):
            found.add(key)
    return found


def _workflow_step(name: str) -> str:
    text = WORKFLOW.read_text(encoding="utf-8")
    assert name in text, f"CI has no step named {name!r}"
    body = text[text.index(name) :]
    end = body.find("- name:", len(name))
    return body[: end if end > 0 else None]


def _step_back_targets() -> dict[str, int]:
    return {
        match.group(1): int(match.group(2))
        for match in re.finditer(
            r"manage\.py migrate ([a-z_]+) (\d{4})\b", _workflow_step(STEP_BACK)
        )
    }


def _number(name: str) -> int:
    return int(name.split("_", 1)[0])


def test_the_step_back_runs_over_seeded_rows():
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "seed_e2e_data" in _workflow_step(SEED_STEP)
    assert text.index(FROM_ZERO) < text.index(SEED_STEP) < text.index(STEP_BACK), (
        "the seed has to run at the leaf, after migrating from zero and before stepping back"
    )


def test_every_runpython_migration_has_a_populated_proof():
    targets = _step_back_targets()
    assert targets, "the step-back names no targets; every check below would be vacuous"

    unproved = []
    for app, name in sorted(_runpython_migrations()):
        in_range = app in targets and _number(name) > targets[app]
        declared = (app, name) in POPULATED_PROOFS or (app, name) in APPROVED_EXCEPTIONS
        if not in_range and not declared:
            unproved.append(f"{app}.{name}")
    assert not unproved, (
        "these RunPython migrations have never run over rows in CI: "
        f"{', '.join(unproved)}. Move the step-back target below them, or add a populated "
        "test to POPULATED_PROOFS."
    )


def test_the_declared_proofs_are_neither_stale_nor_redundant():
    """A proof for a migration the step-back now covers, or one that no longer
    exists, is a list drifting away from what it describes."""
    targets = _step_back_targets()
    on_disk = _runpython_migrations()
    for app, name in {**POPULATED_PROOFS, **APPROVED_EXCEPTIONS}:
        assert (app, name) in on_disk, f"{app}.{name} is declared but is not a RunPython migration"
        assert not (app in targets and _number(name) > targets[app]), (
            f"{app}.{name} is inside the populated step-back; its declaration is redundant"
        )
    for (app, name), node in POPULATED_PROOFS.items():
        path, test = node.split("::")
        source = (ROOT / path).read_text(encoding="utf-8")
        assert f"def {test}(" in source, f"{app}.{name} names {node}, which does not exist"
    for key, reason in APPROVED_EXCEPTIONS.items():
        assert len(reason.split()) >= 5, f"{key} is excepted without a reason"


# ---------------------------------------------------------------------------
# The populated proofs the step-back cannot provide
# ---------------------------------------------------------------------------


def _migration(module: str):
    return importlib.import_module(module)


@pytest.mark.django_db
def test_stage_vocabulary_reverses_and_reapplies_over_matters_in_a_stage(specialist):
    """`workflow.0004` sits behind `matters.0008`, whose reverse refuses a Matter
    with two senders — so no step-back over realistic rows can reach it. Its
    functions are run directly over a Matter filed in a seeded stage."""
    from app.matters.services import create_matter
    from app.workflow.models import StageVocabulary

    vocabulary = _migration("app.workflow.migrations.0004_seed_stage_vocabulary")
    seeded = [key for key, *_ in vocabulary.STAGES]
    stage = StageVocabulary.objects.get(key=seeded[0])
    matter = create_matter(title="Menetluses teema", actor=specialist)
    type(matter).objects.filter(pk=matter.pk).update(stage=stage)

    vocabulary.unseed(global_apps, None)

    # The stage in use survives with the Matter still in it; the unused ones go.
    matter.refresh_from_db()
    assert matter.stage_id == stage.pk
    assert StageVocabulary.objects.filter(key__in=seeded).count() == 1

    vocabulary.seed(global_apps, None)

    assert StageVocabulary.objects.filter(key__in=seeded).count() == len(seeded)
    matter.refresh_from_db()
    assert matter.stage_id == stage.pk


@pytest.mark.django_db
def test_reference_areas_reverse_and_reapply_over_classified_matters(specialist):
    """`taxonomy.0002` over filed work: a classified area and its Matter survive
    the reverse, and the forward then restores the vocabulary without a clash."""
    from app.matters.services import create_matter
    from app.taxonomy.models import PolicyArea

    reference = _migration("app.taxonomy.migrations.0002_reference_policy_areas")
    keys = [key for key, *_ in reference.BASELINE]
    classified = PolicyArea.objects.filter(key__in=keys).order_by("sort_order").first()
    assert classified is not None
    matter = create_matter(title="Klassifitseeritud teema", actor=specialist)
    matter.policy_areas.add(classified)
    before = PolicyArea.objects.filter(key__in=keys).count()

    reference.unseed(global_apps, None)

    assert PolicyArea.objects.filter(pk=classified.pk).exists()
    assert matter.policy_areas.filter(pk=classified.pk).exists()

    reference.seed(global_apps, None)

    assert PolicyArea.objects.filter(key__in=keys).count() >= before
    assert matter.policy_areas.filter(pk=classified.pk).exists()
