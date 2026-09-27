"""A public write service that writes a record and its audit row owns the transaction (ENG-130).

`add_engagement`, `record_external_position` and `record_procedural_development`
each created a record and then wrote its `ChangeEvent`, with no transaction of
their own: they were atomic only because every current caller happened to wrap
them. A management command or an importer calling one in autocommit would have
committed a record whose audit row failed. And `set_recipients` had lost its
boundary to a helper inserted between it and its decorator.

The tests inject a failure at the audit write and call each service directly —
inside the test's transaction, as any caller's would be — and require that the
record the service created is not left behind.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest
from django.conf import settings
from django.db import transaction

from app.matters import services as matter_services
from app.matters.enums import EngagementKind
from app.matters.models import (
    MatterEngagement,
    MatterExternalPosition,
    MatterProceduralDevelopment,
)
from app.submissions import services as submission_services
from app.submissions.models import SubmissionRecipient
from tests import factories

pytestmark = pytest.mark.django_db


class AuditUnavailable(Exception):
    pass


@pytest.fixture
def failing_audit(monkeypatch):
    def refuse(*args, **kwargs):
        raise AuditUnavailable

    def install(module):
        monkeypatch.setattr(module, "record_change_event", refuse)

    return install


def _call_and_expect_nothing_left(call, model, matter):
    before = model.objects.filter(matter=matter).count()
    with pytest.raises(AuditUnavailable), transaction.atomic():
        # The outer atomic stands in for any caller's transaction: if the
        # service had no boundary of its own, the record would survive the
        # failure inside it until the caller rolled back.
        try:
            call()
        finally:
            assert model.objects.filter(matter=matter).count() == before, (
                "the record outlived its failed audit row inside the service"
            )


@pytest.mark.parametrize(
    ("model", "call"),
    [
        (
            MatterEngagement,
            lambda matter, actor: matter_services.add_engagement(
                matter=matter, kind=EngagementKind.SURVEY, title="Küsitlus", actor=actor
            ),
        ),
        (
            MatterExternalPosition,
            lambda matter, actor: matter_services.record_external_position(
                matter=matter,
                organisation=factories.OrganisationFactory(),
                summary="Seisukoht",
                actor=actor,
            ),
        ),
        (
            MatterProceduralDevelopment,
            lambda matter, actor: matter_services.record_procedural_development(
                matter=matter, title="Samm", actor=actor
            ),
        ),
    ],
    ids=["add_engagement", "record_external_position", "record_procedural_development"],
)
def test_a_failed_audit_row_takes_the_record_with_it(
    normal_matter, specialist, failing_audit, model, call
):
    failing_audit(matter_services)
    _call_and_expect_nothing_left(lambda: call(normal_matter, specialist), model, normal_matter)


def test_a_failed_audit_row_takes_the_recipient_changes_with_it(
    normal_matter, specialist, failing_audit
):
    submission = factories.SubmissionFactory(matter=normal_matter)
    kept = factories.OrganisationFactory()
    submission_services.set_recipients(submission=submission, addressees=[kept], actor=specialist)
    before = sorted(
        SubmissionRecipient.objects.filter(submission=submission).values_list(
            "organisation_id", flat=True
        )
    )

    failing_audit(submission_services)
    with pytest.raises(AuditUnavailable), transaction.atomic():
        try:
            submission_services.set_recipients(
                submission=submission,
                addressees=[factories.OrganisationFactory()],
                actor=specialist,
            )
        finally:
            after = sorted(
                SubmissionRecipient.objects.filter(submission=submission).values_list(
                    "organisation_id", flat=True
                )
            )
            assert after == before, "the recipient rows changed without their audit row"


def test_a_successful_nested_call_still_commits_with_its_caller(normal_matter, specialist):
    """The workspace wrappers call these inside their own transaction; a nested
    boundary is a savepoint, and the save still lands."""
    from app.matters.workspace import add_procedural_development

    result = add_procedural_development(matter=normal_matter, author=specialist, title="Samm")

    assert MatterProceduralDevelopment.objects.filter(pk=result.record.pk).exists()


# -- the census that keeps it so ------------------------------------------------------

WRITES = {"create", "save", "update", "delete", "bulk_create", "get_or_create", "update_or_create"}

#: Public functions that write and audit without a boundary, on purpose.
#: ``record_change_event`` *is* the audit write: one statement.
OWN_BOUNDARY_NOT_NEEDED = {"app/audit/services.py::record_change_event"}


def test_every_public_service_that_writes_and_audits_is_atomic():
    """The shape that went missing twice: a record write plus a `ChangeEvent`
    in one public function with neither `@transaction.atomic` nor
    `with transaction.atomic()`."""
    unguarded = []
    root = Path(settings.BASE_DIR)
    for path in sorted((root / "app").rglob("*.py")):
        if "migrations" in path.parts:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in tree.body:
            if not isinstance(node, ast.FunctionDef) or node.name.startswith("_"):
                continue
            key = f"{path.relative_to(root).as_posix()}::{node.name}"
            if key in OWN_BOUNDARY_NOT_NEEDED:
                continue
            source = ast.unparse(node)
            if "record_change_event(" not in source:
                continue
            writes = any(
                isinstance(call, ast.Call)
                and isinstance(call.func, ast.Attribute)
                and call.func.attr in WRITES
                for call in ast.walk(node)
            )
            decorated = any("atomic" in ast.unparse(d) for d in node.decorator_list)
            if writes and not decorated and "transaction.atomic(" not in source:
                unguarded.append(key)
    assert unguarded == []
