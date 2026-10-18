"""Claims the code makes about itself, held by tests instead of by comments.

The 2026-10-17 audit (JARCH-008) found comments asserting invariants that had
quietly stopped being true, and one of them justified an unsafe shortcut. So
the claims that matter are tests here:

* `correct_external_position` documents that it «has exactly one caller»,
  and that is why it names every field on every save. The day a second caller
  appears, this file says so (G01).
* ADR 0102's chokepoint: every removable family's `visible_to` leaves removed
  rows out. Reads that have no reader are the exception the ADR now names.

And the two integrity rules `check_domain_invariants` could not ask
(JDATA-003): a `DISCOVERED` position that answers a round (docs/adr/0170 §3),
and a reply that is less restricted than its `RESTRICTED` round
(docs/adr/0162 §1). Each is asked only of rows created after the release that
shipped its refusal. A row older than that is history, and the live read still
protects it.
"""

from __future__ import annotations

import ast
import datetime as dt
from pathlib import Path

import pytest
from django.apps import apps
from django.db.migrations.recorder import MigrationRecorder

from app.core.enums import Visibility
from app.core.invariants import (
    EXPLANATIONS,
    RULE_SINCE_MIGRATION,
    SERVICE_RULES,
    check_domain_invariants,
)
from app.core.models import RemovableRecord
from app.matters.enums import EngagementKind
from app.matters.models import MatterEngagement, MatterExternalPosition
from tests import factories

APP = Path(__file__).resolve().parents[1] / "app"

DISCOVERED_ANSWER = "external-position-discovered-answers-round"
OPEN_REPLY = "external-position-reply-less-restricted-than-round"


# =========================================================================
# Claims
# =========================================================================


def _production_callers(name: str) -> list[str]:
    found = []
    for path in APP.rglob("*.py"):
        if "migrations" in path.parts:
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Call):
                func = node.func
                called = func.id if isinstance(func, ast.Name) else getattr(func, "attr", "")
                if called == name:
                    found.append(f"{path.relative_to(APP).as_posix()}:{node.lineno}")
    return found


def test_correct_external_position_has_exactly_one_caller() -> None:
    """The docstring's «exactly one caller, a correction form that renders every
    box» is why every field is named on every save. A second caller has to
    revisit that, and the provenance sentinel (JDATA-002), before it lands."""
    callers = _production_callers("correct_external_position")
    assert len(callers) == 1, callers
    assert callers[0].startswith("matters/views.py:"), callers


def _removable_families() -> list[type]:
    return [model for model in apps.get_models() if issubclass(model, RemovableRecord)]


@pytest.mark.django_db
@pytest.mark.parametrize("reader", ["specialist", "reader", "head"])
def test_every_removable_reader_chokepoint_leaves_removed_rows_out(reader) -> None:
    """docs/adr/0102 §3, for every family the table names, at the SQL it runs."""
    person = {
        "specialist": factories.UserFactory,
        "reader": factories.ReaderFactory,
        "head": factories.DepartmentHeadFactory,
    }[reader]()
    families = _removable_families()
    assert len(families) >= 8
    for model in families:
        sql = str(model.objects.visible_to(person).query)
        assert f'"{model._meta.db_table}"."removed_at" IS NULL' in sql, model


# =========================================================================
# The two reply rules (JDATA-003)
# =========================================================================


def test_both_rules_are_registered_and_explained() -> None:
    for kind in (DISCOVERED_ANSWER, OPEN_REPLY):
        assert kind in SERVICE_RULES
        assert EXPLANATIONS[kind]
        app_label, name = RULE_SINCE_MIGRATION[kind]
        assert (APP / app_label / "migrations" / f"{name}.py").exists(), kind


@pytest.fixture
def scene(db, specialist):
    matter = factories.MatterFactory(owner=specialist)
    organisation = factories.OrganisationFactory()
    round_ = MatterEngagement.objects.create(
        matter=matter, kind=EngagementKind.SURVEY, title="Sünteetiline ring"
    )
    restricted = MatterEngagement.objects.create(
        matter=matter,
        kind=EngagementKind.SURVEY,
        title="Sünteetiline piiratud ring",
        visibility_override=Visibility.RESTRICTED,
    )
    return matter, organisation, round_, restricted


def _position(matter, organisation, *, engagement=None, provenance="RECEIVED", **extra):
    """Written past the services, as an import or a data fix could."""
    return MatterExternalPosition.objects.create(
        matter=matter,
        organisation=organisation,
        provenance=provenance,
        summary="Sünteetiline seisukoht",
        engagement=engagement,
        **extra,
    )


def _flagged(kind: str) -> set[str]:
    return {f.subject for f in check_domain_invariants().findings if f.kind == kind}


def _backdate(row, boundary_kind: str) -> None:
    app_label, name = RULE_SINCE_MIGRATION[boundary_kind]
    applied = MigrationRecorder.Migration.objects.get(app=app_label, name=name).applied
    type(row).objects.filter(pk=row.pk).update(created_at=applied - dt.timedelta(days=1))


def test_a_new_discovered_answer_to_a_round_is_reported(scene) -> None:
    matter, organisation, round_, _restricted = scene
    wrong = _position(matter, organisation, engagement=round_, provenance="DISCOVERED")
    assert str(wrong.pk) in _flagged(DISCOVERED_ANSWER)


@pytest.mark.parametrize(
    "shape",
    ["received-answer", "discovered-standalone", "legacy-answer", "removed", "historical"],
)
def test_what_the_discovered_rule_leaves_alone(scene, shape) -> None:
    matter, organisation, round_, _restricted = scene
    if shape == "received-answer":
        row = _position(matter, organisation, engagement=round_)
    elif shape == "discovered-standalone":
        row = _position(matter, organisation, provenance="DISCOVERED")
    elif shape == "legacy-answer":
        row = _position(matter, organisation, engagement=round_, provenance="LEGACY")
    elif shape == "removed":
        row = _position(matter, organisation, engagement=round_, provenance="DISCOVERED")
        MatterExternalPosition.objects.filter(pk=row.pk).update(
            removed_at=row.created_at + dt.timedelta(minutes=1)
        )
    else:
        # Filed before ADR 0170's release: legal then, and kept (0170 §3).
        row = _position(matter, organisation, engagement=round_, provenance="DISCOVERED")
        _backdate(row, DISCOVERED_ANSWER)
    assert str(row.pk) not in _flagged(DISCOVERED_ANSWER)


def test_a_new_reply_less_restricted_than_its_round_is_reported(scene) -> None:
    matter, organisation, _round, restricted = scene
    open_reply = _position(matter, organisation, engagement=restricted)
    assert str(open_reply.pk) in _flagged(OPEN_REPLY)


@pytest.mark.parametrize("shape", ["restricted-reply", "normal-round", "historical", "removed"])
def test_what_the_restriction_rule_leaves_alone(scene, shape) -> None:
    matter, organisation, round_, restricted = scene
    if shape == "restricted-reply":
        row = _position(
            matter, organisation, engagement=restricted, visibility_override=Visibility.RESTRICTED
        )
    elif shape == "normal-round":
        row = _position(matter, organisation, engagement=round_)
    elif shape == "historical":
        # Filed before ADR 0162 §1 copied the restriction: the live read
        # covers it (0162 §3), and it is not rewritten.
        row = _position(matter, organisation, engagement=restricted)
        _backdate(row, OPEN_REPLY)
    else:
        row = _position(matter, organisation, engagement=restricted)
        MatterExternalPosition.objects.filter(pk=row.pk).update(
            removed_at=row.created_at + dt.timedelta(minutes=1)
        )
    assert str(row.pk) not in _flagged(OPEN_REPLY)


def test_before_the_release_is_migrated_the_rules_report_nothing(scene) -> None:
    """The deploy runbook runs the checker against the still-unmigrated
    database: no row there was written under rules it has not got yet."""
    matter, organisation, round_, restricted = scene
    _position(matter, organisation, engagement=round_, provenance="DISCOVERED")
    _position(matter, organisation, engagement=restricted)
    for kind in (DISCOVERED_ANSWER, OPEN_REPLY):
        app_label, name = RULE_SINCE_MIGRATION[kind]
        MigrationRecorder.Migration.objects.filter(app=app_label, name=name).delete()
    assert not _flagged(DISCOVERED_ANSWER)
    assert not _flagged(OPEN_REPLY)
