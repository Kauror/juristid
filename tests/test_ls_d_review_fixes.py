"""Independent review 2026-11-09 of agent D's later-capture work (P2, P3).

P2: a reading held for a person is never the baseline a later capture compares
with, so the same held bytes captured again are held again and the page is never
marked done. P3: the revision ledgers refuse UPDATE in the database; page
readings refuse DELETE too; a Teema's permanent deletion still removes its
file readings with the rest of what it owns. Invented data only.
"""

from __future__ import annotations

import pytest
from django.db import connection, transaction

from app.core.errors import ImmutableRecordError
from app.documents.models import Document
from app.legacy_import import onenote_capture as oc
from app.legacy_import import onenote_coverage as cov
from app.legacy_import.onenote_coverage_models import (
    OneNotePageRevision,
    OneNoteResourceRevision,
    PageState,
    RevisionState,
)
from app.matters.deletion import permanently_delete_matter, plan_matter_deletion
from tests import factories
from tests.test_onenote_capture_changes import FIRST, GUID, T1, T2, build, f, items, run
from tests.test_onenote_capture_import import register_teema

pytestmark = pytest.mark.django_db


@pytest.fixture
def enabled(settings):
    settings.ONENOTE_CAPTURE_IMPORT_ENABLED = True
    settings.REAL_DATA_ALLOWED = True
    return settings


@pytest.fixture
def teema():
    return register_teema("2026_91")


def capture(tmp_path, capture_id, teema, files, **page):
    page = {"tag": "001", "guid": GUID, "matters": [teema], "files": files, **page}
    return build(tmp_path / capture_id, capture_id, [page])


EMPTY = [f("{E1}", "tühi.pdf", T1, data=b"")]
FILLED = [f("{E1}", "tühi.pdf", T1, data=b"filled later")]


def test_held_bytes_captured_again_are_held_again_not_read_as_the_same(tmp_path, teema, enabled):
    run(capture(tmp_path, "cap-1", teema, EMPTY))
    plan2, _ = run(capture(tmp_path, "cap-2", teema, FILLED))
    assert ("{E1}", oc.REVISE, oc.HOLD_BYTES_CHANGED) in items(plan2)
    plan3, report3 = run(capture(tmp_path, "cap-3", teema, FILLED))
    assert ("{E1}", oc.REVISE, oc.HOLD_BYTES_CHANGED) in items(plan3)
    assert plan3.classes[oc.CLASS_CHANGED_BYTES] == 1 and plan3.classes[oc.CLASS_SAME] == 0
    assert report3.revisions == {oc.HOLD_BYTES_CHANGED: 1}
    held = OneNoteResourceRevision.objects.filter(state=RevisionState.HELD)
    assert held.count() == 2 and Document.objects.count() == 0
    # The page is never marked done while the reading waits for a person.
    assert cov.page_states([GUID])[GUID]["state"] == PageState.AWAITING_ASSOCIATION
    assert cov._derived_state(cov._catalogued()[GUID]) == PageState.AWAITING_ASSOCIATION


def test_a_held_reading_of_a_removed_document_stays_pending(tmp_path, teema, enabled):
    run(capture(tmp_path, "cap-1", teema, FIRST[:2]))
    Document.objects.filter(title="vastus.docx").update(removed_at="2026-11-08T00:00:00Z")
    changed = [FIRST[0], f("{B2}", "vastus.docx", T2, data=b"new bytes")]
    run(capture(tmp_path, "cap-2", teema, changed))
    plan3, _ = run(capture(tmp_path, "cap-3", teema, changed))
    assert ("{B2}", oc.REVISE, oc.REVISE_DOCUMENT_REMOVED) in items(plan3)
    assert cov.page_states([GUID])[GUID]["state"] == PageState.AWAITING_ASSOCIATION


def test_a_version_is_the_baseline_and_an_equal_third_capture_is_the_same(tmp_path, teema, enabled):
    run(capture(tmp_path, "cap-1", teema, FIRST[:2]))
    changed = [FIRST[0], f("{B2}", "vastus.docx", T2, data=b"new bytes")]
    run(capture(tmp_path, "cap-2", teema, changed))
    plan3, report3 = run(capture(tmp_path, "cap-3", teema, changed))
    assert plan3.classes[oc.CLASS_SAME] == 2 and report3.batch_id == ""
    assert cov.page_states([GUID])[GUID]["state"] == PageState.IMPORTED


def _changed_page(tmp_path, teema):
    run(capture(tmp_path, "cap-1", teema, FIRST[:2]))
    changed = [FIRST[0], f("{B2}", "vastus.docx", T2, data=b"new bytes")]
    run(capture(tmp_path, "cap-2", teema, changed, text="Uus tekst"))


def test_the_revision_ledgers_refuse_update_in_python_and_in_the_database(tmp_path, teema, enabled):
    _changed_page(tmp_path, teema)
    revision = OneNoteResourceRevision.objects.get()
    page_revision = OneNotePageRevision.objects.get()
    with pytest.raises(ImmutableRecordError):
        revision.save()
    with pytest.raises(ImmutableRecordError):
        page_revision.save()
    with pytest.raises(ImmutableRecordError):
        page_revision.delete()
    for sql in (
        "UPDATE legacy_import_onenoteresourcerevision SET reason = 'x'",
        "UPDATE legacy_import_onenotepagerevision SET title = 'x'",
        "DELETE FROM legacy_import_onenotepagerevision",
    ):
        with pytest.raises(Exception, match="Append-only table"):
            with transaction.atomic(), connection.cursor() as cursor:
                cursor.execute(sql)


def test_a_teemas_permanent_deletion_removes_its_file_readings(tmp_path, teema, enabled):
    _changed_page(tmp_path, teema)
    assert OneNoteResourceRevision.objects.filter(state=RevisionState.VERSIONED).count() == 1
    head = factories.DepartmentHeadFactory()
    permanently_delete_matter(
        matter=teema, actor=head, confirmation=plan_matter_deletion(teema).digest
    )
    assert not OneNoteResourceRevision.objects.exists()
    # The page and its own reading are the source corpus's, not the Teema's.
    assert OneNotePageRevision.objects.count() == 1
