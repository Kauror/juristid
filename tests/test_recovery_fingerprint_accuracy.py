"""`recovery_fingerprint --compare` says "different" only when the register is.

Two ways it was wrong, both in the one command whose job is to tell an operator
whether a restore brought the register back.

ENG-118: five tables that are a projection or a session's state were counted as
canonical, because a model is canonical until it is written into one of two
lists. A derivative rebuild between two fingerprints read as
`DocumentTextFragment: 2 -> 1`, one mistyped gate password as
`SharedGateThrottle: 0 -> 1`.

ENG-116: `migration_leaves` came from the migration graph on disk, so it
described the code in the process rather than the restored database. A restore
with a migration missing compared clean.
"""

from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path

import pytest
from django.apps import apps
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import models
from django.db.migrations.recorder import MigrationRecorder
from django.utils import timezone

from app.core import deployment
from tests import factories

pytestmark = pytest.mark.django_db

RECLASSIFIED = {
    "documents.DocumentTextFragment": deployment.REBUILDABLE_MODELS,
    "legacy_import.CurrentRegisterState": deployment.REBUILDABLE_MODELS,
    "accounts.SharedGateThrottle": deployment.OPERATIONAL_MODELS,
    "matters.MatterIntakeSession": deployment.OPERATIONAL_MODELS,
    "matters.MatterIntakeFile": deployment.OPERATIONAL_MODELS,
}


def _label(model: type[models.Model]) -> str:
    return f"{model._meta.app_label}.{model.__name__}"


def _fingerprint(tmp_path: Path) -> Path:
    out = tmp_path / "before.json"
    call_command("recovery_fingerprint", "--out", str(out), "--skip-evidence-bytes")
    return out


def _compare(out: Path) -> None:
    call_command("recovery_fingerprint", "--compare", str(out), "--skip-evidence-bytes")


# -- ENG-118: classification ------------------------------------------------


@pytest.mark.parametrize("label", sorted(RECLASSIFIED))
def test_each_derived_or_session_table_is_classified_and_not_canonical(label: str) -> None:
    assert label in RECLASSIFIED[label]
    assert label not in deployment.canonical_model_labels()
    known = set(deployment.rebuildable_model_labels()) | set(deployment.operational_model_labels())
    assert label in known, "classified under a label no installed model has"


def test_a_table_that_cascades_from_a_projection_is_not_canonical() -> None:
    """The rule the fragments broke, held for every future child.

    A row that is deleted whenever its rebuildable parent is deleted cannot be
    anything its parent is not. Counted as canonical, it turns every rebuild of
    the parent into "rows lost" in a restore comparison.
    """
    rebuildable = set(deployment.REBUILDABLE_MODELS)
    not_canonical = rebuildable | set(deployment.OPERATIONAL_MODELS)
    offenders = []
    for model in apps.get_models():
        for field in model._meta.get_fields():
            if not field.concrete or not (field.many_to_one or field.one_to_one):
                continue
            parent = field.related_model
            if parent is None or _label(parent) not in rebuildable:
                continue
            cascades = field.remote_field.on_delete is models.CASCADE
            if cascades and _label(model) not in not_canonical:
                offenders.append(f"{_label(model)}.{field.name} -> {_label(parent)}")
    assert offenders == []


def test_the_cascade_rule_would_have_caught_the_fragments() -> None:
    """The guard above is not vacuous: the fragments are a child it inspects."""
    from app.documents.derivatives import DocumentTextFragment

    field = DocumentTextFragment._meta.get_field("derivative")
    assert _label(field.related_model) in deployment.REBUILDABLE_MODELS
    assert field.remote_field.on_delete is models.CASCADE


def test_a_failed_gate_attempt_between_fingerprints_is_not_drift(tmp_path: Path) -> None:
    from app.accounts.models import SharedGateThrottle

    out = _fingerprint(tmp_path)
    SharedGateThrottle.objects.create(
        client_key="a" * 64, failures=1, last_failure_at=timezone.now()
    )
    _compare(out)


def test_an_open_uus_teema_form_between_fingerprints_is_not_drift(tmp_path: Path) -> None:
    from app.matters.staging import MatterIntakeSession

    owner = factories.UserFactory()
    out = _fingerprint(tmp_path)
    MatterIntakeSession.objects.create(owner=owner, expires_at=timezone.now() + timedelta(hours=1))
    _compare(out)


def test_a_derivative_rebuild_between_fingerprints_is_not_drift(tmp_path: Path) -> None:
    """The false positive most likely to happen in practice (ENG-118)."""
    from app.documents.derivatives import DocumentDerivative, DocumentTextFragment
    from app.documents.enums import DerivativeKind, DerivativeStatus, LocatorKind
    from app.documents.services import add_evidence_version, create_document

    document = create_document(matter=factories.MatterFactory(), title="Tõend")
    version = add_evidence_version(
        document=document,
        content=b"%PDF-1.4 synthetic",
        original_filename="toend.pdf",
        mime_type="application/pdf",
    )
    derivative = DocumentDerivative.objects.create(
        version=version,
        kind=DerivativeKind.EXTRACTED_TEXT,
        generator="test",
        generator_version="1",
        status=DerivativeStatus.ACTIVE,
        fragment_count=2,
        character_count=4,
    )
    for ordinal in (1, 2):
        DocumentTextFragment.objects.create(
            derivative=derivative,
            ordinal=ordinal,
            text="lk",
            locator_kind=LocatorKind.PAGE,
            locator={"page": ordinal},
            locator_label=f"lk {ordinal}",
            character_count=2,
        )
    out = _fingerprint(tmp_path)

    # What a restore is documented as allowed to leave empty: every derivative,
    # and with them, by CASCADE, every fragment.
    DocumentDerivative.objects.all().delete()
    assert not DocumentTextFragment.objects.exists()
    _compare(out)


def test_a_fingerprint_that_counted_them_as_canonical_still_compares(tmp_path: Path) -> None:
    """The deploy that introduces this compares against a file the old build wrote."""
    out = _fingerprint(tmp_path)
    earlier = json.loads(out.read_text(encoding="utf-8"))
    for label in RECLASSIFIED:
        earlier["canonical_counts"][label] = 7
    out.write_text(json.dumps(earlier), encoding="utf-8")
    _compare(out)


def test_reclassifying_did_not_stop_the_check_noticing_a_lost_matter(tmp_path: Path) -> None:
    spare = factories.MatterFactory()
    out = _fingerprint(tmp_path)
    spare.delete()
    with pytest.raises(CommandError) as failure:
        _compare(out)
    assert "matters.Matter" in str(failure.value)


# -- ENG-116: the schema is read from the database ---------------------------


def test_a_fully_migrated_database_has_the_code_leaves_applied() -> None:
    """So a fingerprint written by an earlier build still means the same thing."""
    state = deployment.migration_state()
    assert state.applied_leaves == state.leaves


def test_a_restore_missing_a_migration_fails_the_comparison(tmp_path: Path) -> None:
    """The audit's reproduction: `workflow` one migration back, compared clean.

    The applied row is removed inside the test's transaction, which is exactly
    what a database that never ran the migration looks like to Django.
    """
    out = _fingerprint(tmp_path)
    state = deployment.migration_state()
    leaf = next(leaf for leaf in state.leaves if leaf.startswith("workflow."))
    app_label, name = leaf.split(".", 1)
    MigrationRecorder.Migration.objects.filter(app=app_label, name=name).delete()

    after = deployment.migration_state()
    assert leaf not in after.applied_leaves
    assert leaf in after.leaves, "the code still has it; the database does not"
    with pytest.raises(CommandError) as failure:
        _compare(out)
    assert "migration_leaves" in str(failure.value)


def test_the_fingerprint_records_what_the_database_applied_not_what_the_code_has(
    tmp_path: Path,
) -> None:
    """The other direction the on-disk leaves got wrong.

    A restore onto a build carrying one more migration than the database has
    applied is ordinary — `deployment_readiness` is what says it must migrate —
    and comparing the code's leaves reported it as schema drift. The fingerprint
    names the database's own last applied migration.
    """
    leaf = next(
        leaf for leaf in deployment.migration_state().leaves if leaf.startswith("workflow.")
    )
    app_label, name = leaf.split(".", 1)
    MigrationRecorder.Migration.objects.filter(app=app_label, name=name).delete()

    fingerprint = json.loads(_fingerprint(tmp_path).read_text(encoding="utf-8"))
    assert leaf not in fingerprint["migration_leaves"]
    assert any(item.startswith("workflow.") for item in fingerprint["migration_leaves"])
    assert fingerprint["migrations_consistent"] is False
