"""Agent E (independent QA): adversarial probes of `apply_document_title_proposals`.

Synthetic data only. Failing tests document defects; nothing is changed.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from django.apps import apps

from app.accounts.enums import UserRole
from app.documents import title_proposals as tp
from app.documents.models import Document, DocumentVersion
from tests import factories
from tests.refusals import refused
from tests.test_tl_d_title_proposals import _apply, _document, _events, _evidence, _plan, _row

pytestmark = pytest.mark.django_db


def _counts():
    return {
        m._meta.label: m._default_manager.count()
        for m in apps.get_models()
        if m._meta.managed and not m._meta.proxy
    }


def test_a_dry_run_writes_no_row_in_any_table(tmp_path, normal_matter, specialist):
    document = _document(normal_matter)
    path = _plan(tmp_path, [_row(document)])
    before = _counts()
    report = tp.run_plan(path, actor_identifier=specialist.upn)
    assert [r.outcome for r in report.results] == [tp.WOULD_RENAME]
    assert _counts() == before
    document.refresh_from_db()
    assert document.title == _row(document)["current_title"]


def test_the_plan_cannot_rename_a_file_or_reach_a_path(tmp_path, normal_matter, specialist):
    document = _document(normal_matter)
    evidence = _evidence(document)
    hostile = _row(
        document,
        proposed="../../etc/passwd <script>alert(1)</script>",
        original_filename="../../../evil.exe",
        storage_key="../../../../outside",
        sha256="0" * 64,
        title="x",
    )
    path = _plan(tmp_path, [hostile])
    report = _apply(path, specialist)
    assert [r.outcome for r in report.results] == [tp.RENAMED]
    document.refresh_from_db()
    # The title is stored as text (escaped on render); nothing else moved.
    assert document.title == "../../etc/passwd <script>alert(1)</script>"
    assert _evidence(document) == evidence
    assert DocumentVersion.objects.filter(document=document).count() == len(evidence)


def test_a_title_with_a_nul_byte_is_a_refused_row_not_a_crash(tmp_path, normal_matter, specialist):
    first = _document(normal_matter)
    second = _document(normal_matter, title="Teine.pdf")
    path = _plan(
        tmp_path,
        [_row(first, proposed="Pealkiri\x00peidetud"), _row(second, proposed="Teine pealkiri")],
    )
    report = _apply(path, specialist)
    outcomes = [r.outcome for r in report.results]
    assert outcomes[0] in (tp.REFUSED_BAD_PROPOSAL, tp.REFUSED_SERVICE)
    assert outcomes[1] == tp.RENAMED


def test_bidi_and_control_characters_in_a_proposal_are_refused(tmp_path, normal_matter, specialist):
    """U+202E can make a display title read as a different filename (fdp.exe)."""
    document = _document(normal_matter)
    path = _plan(tmp_path, [_row(document, proposed="Arvamus‮fdp.exe")])
    report = tp.run_plan(path, actor_identifier=specialist.upn)
    assert report.results[0].outcome == tp.REFUSED_BAD_PROPOSAL


def test_the_digest_binds_the_rows_that_are_applied(
    tmp_path, normal_matter, specialist, monkeypatch
):
    """The digest is taken from one read of the file and the rows from a second
    read: a file swapped between the two applies rows nobody reviewed."""
    document = _document(normal_matter)
    reviewed = _plan(tmp_path, [_row(document, proposed="Ülevaadatud pealkiri")])
    digest = tp.plan_sha256(reviewed)
    swapped = json.dumps(_row(document, proposed="Keegi ei vaadanud seda")) + "\n"
    real_read_text = Path.read_text

    def read_text(self, *args, **kwargs):
        if self == reviewed:
            return swapped
        return real_read_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", read_text)
    tp.run_plan(reviewed, actor_identifier=specialist.upn, apply=True, expect_plan_sha256=digest)
    document.refresh_from_db()
    assert document.title != "Keegi ei vaadanud seda"


@pytest.mark.parametrize("role", [UserRole.ADMINISTRATOR, UserRole.READER])
def test_an_actor_without_business_write_is_refused_before_any_row(tmp_path, normal_matter, role):
    document = _document(normal_matter)
    path = _plan(tmp_path, [_row(document)])
    actor = factories.UserFactory(role=role)
    with refused(tp.ACTOR_MAY_NOT_WRITE):
        tp.run_plan(
            path, actor_identifier=actor.upn, apply=True, expect_plan_sha256=tp.plan_sha256(path)
        )
    document.refresh_from_db()
    assert document.title == _row(document)["current_title"]
    assert _events().count() == 0


def test_the_actor_is_matched_exactly_not_by_case_or_prefix(tmp_path, normal_matter, specialist):
    document = _document(normal_matter)
    path = _plan(tmp_path, [_row(document)])
    for guess in (specialist.upn.upper(), specialist.upn[:-1], f" {specialist.upn}x"):
        if guess.strip() == specialist.upn:
            continue
        with refused(tp.ACTOR_UNKNOWN):
            tp.run_plan(path, actor_identifier=guess)


def test_apply_then_reapply_then_stale_then_closed(tmp_path, specialist):
    from app.matters.services import close_matter
    from app.workflow.enums import Disposition

    matter = factories.MatterFactory(owner=specialist)
    document = _document(matter)
    path = _plan(tmp_path, [_row(document, proposed="Uus pealkiri")])
    assert [r.outcome for r in _apply(path, specialist).results] == [tp.RENAMED]
    assert [r.outcome for r in _apply(path, specialist).results] == [tp.ALREADY_APPLIED]
    assert _events().count() == 1
    event = _events().get()
    assert event.actor_id == specialist.pk
    # A person renames it after the plan: the stale plan is refused.
    Document.objects.filter(pk=document.pk).update(title="Inimese pealkiri")
    second = _plan(
        tmp_path,
        [_row(document, proposed="Masina pealkiri", current_title="Uus pealkiri")],
        "b.jsonl",
    )
    assert [r.outcome for r in _apply(second, specialist).results] == [tp.REFUSED_TITLE_CHANGED]
    other = _document(matter, title="Kolmas.pdf")
    close_matter(matter=matter, disposition=Disposition.COMPLETED, actor=specialist)
    third = _plan(tmp_path, [_row(other, proposed="Suletud teema pealkiri")], "c.jsonl")
    assert [r.outcome for r in _apply(third, specialist).results] == [tp.REFUSED_CLOSED_MATTER]
    assert _events().count() == 1
