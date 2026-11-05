"""Reviewed document-title proposals reach the database only through `Muuda nime`.

`apply_document_title_proposals` (app/documents/title_proposals.py):

* a dry run is the default and writes nothing;
* an applied row goes through `rename_document`: `Document.title` changes, one
  `DOCUMENT_TITLE_CHANGED` event records ``from`` and ``to`` and the actor, and
  the evidence filename, bytes, checksum and versions do not move;
* a second run is a no-op (``ALREADY_APPLIED``), not a second event;
* a title changed since the plan is refused, also when it changes between the
  dry-run reading and the locked write;
* only ``APPROVE`` + ``EXPLICIT_TITLE`` rows are eligible;
* closed Matters, removed documents, unknown or read-only actors, a malformed
  plan and a digest that is not the reviewed one are all refused.

All data is synthetic.
"""

from __future__ import annotations

import json
import uuid
from io import StringIO
from pathlib import Path

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from app.accounts.enums import UserRole
from app.audit.enums import ChangeEventType
from app.audit.models import ChangeEvent
from app.documents import title_proposals as tp
from app.documents.enums import DocumentRole
from app.documents.models import Document, DocumentVersion
from app.documents.services import add_evidence_version, remove_document
from app.matters.services import close_matter
from app.workflow.enums import Disposition
from tests import factories
from tests.refusals import refused

pytestmark = pytest.mark.django_db

FILENAME = "Seletuskiri (3 lk).pdf"


def _document(matter, *, title: str = FILENAME) -> Document:
    document = factories.DocumentFactory(
        matter=matter, role=DocumentRole.LEGACY_MATERIAL, title=title
    )
    add_evidence_version(
        document=document,
        content=b"%PDF-1.4\nsunteetiline sisu",
        original_filename=FILENAME,
        mime_type="application/pdf",
    )
    document.refresh_from_db()
    return document


def _evidence(document) -> list[tuple]:
    return list(
        DocumentVersion.objects.filter(document=document)
        .order_by("version_number")
        .values_list("original_filename", "sha256", "size_bytes", "mime_type", "storage_key")
    )


def _row(document, *, proposed: str = "Näidisseaduse muutmise seletuskiri", **over) -> dict:
    row = {
        "document_id": str(document.pk),
        "current_title": document.title,
        "proposed_title": proposed,
        "recommendation": "APPROVE",
        "suggestion_kind": "EXPLICIT_TITLE",
        "evidence_level": "A",
    }
    row.update(over)
    return row


def _plan(tmp_path: Path, rows: list[dict], name: str = "plan.jsonl") -> Path:
    path = tmp_path / name
    path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), "utf-8")
    return path


def _events():
    return ChangeEvent.objects.filter(event_type=ChangeEventType.DOCUMENT_TITLE_CHANGED)


def _apply(path: Path, actor) -> tp.ApplyReport:
    return tp.run_plan(
        path, actor_identifier=actor.upn, apply=True, expect_plan_sha256=tp.plan_sha256(path)
    )


def test_a_dry_run_predicts_and_writes_nothing(tmp_path, normal_matter, specialist):
    document = _document(normal_matter)
    path = _plan(tmp_path, [_row(document)])

    report = tp.run_plan(path, actor_identifier=specialist.upn)

    assert not report.applied
    assert [r.outcome for r in report.results] == [tp.WOULD_RENAME]
    document.refresh_from_db()
    assert document.title == FILENAME
    assert not _events().exists()


def test_applying_changes_the_title_only_and_audits_once(tmp_path, normal_matter, specialist):
    document = _document(normal_matter)
    before = _evidence(document)
    path = _plan(tmp_path, [_row(document, proposed="  Näidisseaduse   seletuskiri ")])

    report = _apply(path, specialist)

    assert [r.outcome for r in report.results] == [tp.RENAMED]
    document.refresh_from_db()
    assert document.title == "Näidisseaduse seletuskiri"
    assert document.current_version.original_filename == FILENAME
    assert _evidence(document) == before
    (event,) = _events()
    assert event.object_id == document.pk
    assert event.actor == specialist
    assert event.payload == {"from": FILENAME, "to": "Näidisseaduse seletuskiri"}


def test_a_second_run_is_a_no_op(tmp_path, normal_matter, specialist):
    document = _document(normal_matter)
    path = _plan(tmp_path, [_row(document)])

    _apply(path, specialist)
    again = _apply(path, specialist)

    assert [r.outcome for r in again.results] == [tp.ALREADY_APPLIED]
    assert _events().count() == 1


def test_a_title_changed_since_the_plan_is_refused(tmp_path, normal_matter, specialist):
    document = _document(normal_matter)
    path = _plan(tmp_path, [_row(document)])
    Document.objects.filter(pk=document.pk).update(title="Kellegi uus pealkiri")

    dry = tp.run_plan(path, actor_identifier=specialist.upn)
    applied = _apply(path, specialist)

    assert [r.outcome for r in dry.results] == [tp.REFUSED_TITLE_CHANGED]
    assert [r.outcome for r in applied.results] == [tp.REFUSED_TITLE_CHANGED]
    document.refresh_from_db()
    assert document.title == "Kellegi uus pealkiri"
    assert not _events().exists()


def test_the_comparison_is_repeated_under_the_lock(
    tmp_path, normal_matter, specialist, monkeypatch
):
    """A change landing after the unlocked reading is still caught before the write."""
    document = _document(normal_matter)
    path = _plan(tmp_path, [_row(document)])
    real_predict = tp._predict

    def predict_then_someone_edits(row, actor):
        result = real_predict(row, actor)
        Document.objects.filter(pk=row.document_id).update(title="Samal ajal muudetud")
        return result

    monkeypatch.setattr(tp, "_predict", predict_then_someone_edits)

    report = _apply(path, specialist)

    assert [r.outcome for r in report.results] == [tp.REFUSED_TITLE_CHANGED]
    document.refresh_from_db()
    assert document.title == "Samal ajal muudetud"
    assert not _events().exists()


def test_only_approved_explicit_rows_are_eligible(tmp_path, normal_matter, specialist):
    review = _document(normal_matter)
    contextual = _document(normal_matter)
    path = _plan(
        tmp_path,
        [
            _row(review, recommendation="REVIEW"),
            _row(contextual, suggestion_kind="CONTEXTUAL_DESCRIPTION"),
        ],
    )

    report = _apply(path, specialist)

    assert [r.outcome for r in report.results] == [
        tp.SKIPPED_NOT_APPROVED,
        tp.SKIPPED_NOT_EXPLICIT,
    ]
    assert not _events().exists()


def test_closed_matter_removed_missing_and_empty_proposals_are_refused(tmp_path, specialist):
    closed_matter = factories.MatterFactory(owner=specialist)
    on_closed = _document(closed_matter)
    close_matter(matter=closed_matter, disposition=Disposition.COMPLETED, actor=specialist)
    open_matter = factories.MatterFactory(owner=specialist)
    removed = _document(open_matter)
    remove_document(document=removed, actor=specialist)
    blank = _document(open_matter)
    ghost = {**_row(blank), "document_id": str(uuid.uuid4())}
    path = _plan(
        tmp_path,
        [_row(on_closed), _row(removed), ghost, _row(blank, proposed="   ")],
    )

    dry = tp.run_plan(path, actor_identifier=specialist.upn)
    applied = _apply(path, specialist)

    expected = [
        tp.REFUSED_CLOSED_MATTER,
        tp.REFUSED_REMOVED,
        tp.REFUSED_NOT_FOUND,
        tp.REFUSED_BAD_PROPOSAL,
    ]
    assert [r.outcome for r in dry.results] == expected
    assert [r.outcome for r in applied.results] == expected
    assert not _events().exists()


def test_one_refusal_does_not_undo_the_other_rows(tmp_path, normal_matter, specialist):
    first = _document(normal_matter)
    moved = _document(normal_matter)
    path = _plan(tmp_path, [_row(first), _row(moved)])
    Document.objects.filter(pk=moved.pk).update(title="Muudetud")

    report = _apply(path, specialist)

    assert [r.outcome for r in report.results] == [tp.RENAMED, tp.REFUSED_TITLE_CHANGED]
    first.refresh_from_db()
    assert first.title == "Näidisseaduse muutmise seletuskiri"
    assert _events().count() == 1


def test_applying_needs_the_reviewed_digest(tmp_path, normal_matter, specialist):
    document = _document(normal_matter)
    path = _plan(tmp_path, [_row(document)])

    with refused(tp.PLAN_DIGEST_MISMATCH):
        tp.run_plan(path, actor_identifier=specialist.upn, apply=True, expect_plan_sha256="")
    with refused(tp.PLAN_DIGEST_MISMATCH):
        tp.run_plan(path, actor_identifier=specialist.upn, apply=True, expect_plan_sha256="0" * 64)
    document.refresh_from_db()
    assert document.title == FILENAME


def test_the_actor_must_exist_and_may_write(tmp_path, normal_matter):
    document = _document(normal_matter)
    path = _plan(tmp_path, [_row(document)])
    reader = factories.UserFactory(role=UserRole.READER)
    inactive = factories.UserFactory(is_active=False)

    with refused(tp.ACTOR_UNKNOWN):
        tp.run_plan(path, actor_identifier="")
    with refused(tp.ACTOR_UNKNOWN):
        tp.run_plan(path, actor_identifier="pole.olemas@example.invalid")
    with refused(tp.ACTOR_UNKNOWN):
        tp.run_plan(path, actor_identifier=inactive.upn)
    with refused(tp.ACTOR_MAY_NOT_WRITE):
        tp.run_plan(path, actor_identifier=reader.upn)


def test_a_malformed_plan_is_refused_whole(tmp_path, normal_matter, specialist):
    document = _document(normal_matter)
    incomplete = _row(document)
    del incomplete["current_title"]
    twice = _plan(tmp_path, [_row(document), _row(document)], "twice.jsonl")
    missing = _plan(tmp_path, [incomplete], "missing.jsonl")
    garbled = tmp_path / "garbled.jsonl"
    garbled.write_text("{not json\n", "utf-8")

    with refused(tp.PLAN_DUPLICATE_DOCUMENT):
        tp.run_plan(twice, actor_identifier=specialist.upn)
    with refused(tp.PLAN_ROW_INCOMPLETE):
        tp.run_plan(missing, actor_identifier=specialist.upn)
    with refused(tp.PLAN_UNREADABLE):
        tp.run_plan(garbled, actor_identifier=specialist.upn)


def test_the_command_dry_runs_by_default_and_applies_with_the_digest(
    tmp_path, normal_matter, specialist
):
    document = _document(normal_matter)
    path = _plan(tmp_path, [_row(document)])
    digest = tp.plan_sha256(path)

    out = StringIO()
    call_command(
        "apply_document_title_proposals",
        "--plan", str(path), "--only-approved", "--actor", specialist.upn, "--show-rows",
        stdout=out,
    )  # fmt: skip
    assert "DRY RUN" in out.getvalue()
    assert digest in out.getvalue()
    assert f"{tp.WOULD_RENAME}" in out.getvalue()
    document.refresh_from_db()
    assert document.title == FILENAME

    out = StringIO()
    call_command(
        "apply_document_title_proposals",
        "--plan", str(path), "--only-approved", "--actor", specialist.upn,
        "--apply", "--expect-plan-sha256", digest,
        stdout=out,
    )  # fmt: skip
    assert "APPLIED" in out.getvalue()
    document.refresh_from_db()
    assert document.title == "Näidisseaduse muutmise seletuskiri"
    assert _events().count() == 1


def test_the_command_requires_only_approved_and_reports_refusals(
    tmp_path, normal_matter, specialist
):
    document = _document(normal_matter)
    path = _plan(tmp_path, [_row(document)])

    with pytest.raises(CommandError, match="--only-approved"):
        call_command(
            "apply_document_title_proposals", "--plan", str(path), "--actor", specialist.upn
        )
    with pytest.raises(CommandError, match=tp.PLAN_DIGEST_MISMATCH):
        call_command(
            "apply_document_title_proposals",
            "--plan", str(path), "--only-approved", "--actor", specialist.upn, "--apply",
        )  # fmt: skip
    document.refresh_from_db()
    assert document.title == FILENAME
