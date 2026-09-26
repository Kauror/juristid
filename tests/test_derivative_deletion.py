"""Kustuta teema removes a Teema's renders too, and leftovers can be found (ENG-032).

Deletion collected both the evidence keys and the derivative keys of a Teema,
then deleted all of them through the *evidence* store. A derivative key names
nothing there, so every delete "succeeded" silently and the first-page render of
each PDF and the downscaled copy of each image stayed on disk — in the backups
and on the appdata share — after the page promised «kogu selle sisu
jäädavalt». Nothing could find them afterwards either: both integrity tools look
at the evidence store only.
"""

from __future__ import annotations

import os
import time
from io import StringIO

import pytest
from django.core.files.base import ContentFile
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import transaction

from app.documents.derivative_integrity import (
    MISSING_DERIVATIVE_OBJECT,
    ORPHAN_DERIVATIVE_OBJECT,
    check_derivatives,
)
from app.documents.enums import DerivativeStatus
from app.documents.extraction.orchestrator import derivative_storage
from app.documents.models import DocumentDerivative
from app.documents.services import evidence_storage
from app.matters.deletion import delete_matter
from tests import factories
from tests import synthetic_corpus as corpus

pytestmark = pytest.mark.django_db

PDF = "application/pdf"
PNG = "image/png"


def _rendered(version) -> list[str]:
    keys = list(
        DocumentDerivative.objects.filter(version=version, status=DerivativeStatus.ACTIVE)
        .exclude(storage_key="")
        .values_list("storage_key", flat=True)
    )
    assert keys, "extraction stored no derivative binary; this test tests nothing"
    return keys


@pytest.fixture
def rendered_pdf(normal_matter, capture_evidence, extract):
    version = capture_evidence(normal_matter, corpus.government_pdf(), "kaaskiri.pdf", PDF)
    extract(version)
    return version


@pytest.fixture
def rendered_image(normal_matter, capture_evidence, extract, monkeypatch):
    # OCR stands in for itself: whether this machine has Tesseract is not what
    # is under test, and without it the image parser stops before it stores
    # the downscaled copy this test needs.
    from app.documents.extraction import text

    monkeypatch.setattr(text, "ocr_is_available", lambda: True)
    monkeypatch.setattr(text, "ocr_engine_version", lambda: "test")
    monkeypatch.setattr(text, "recognise_image", lambda content, limits: corpus.ONLY_IN_OCR_IMAGE)
    version = capture_evidence(normal_matter, corpus.scanned_png(), "skaneering.png", PNG)
    extract(version)
    return version


def _delete(matter, django_capture_on_commit_callbacks) -> None:
    with django_capture_on_commit_callbacks(execute=True):
        delete_matter(matter=matter)


# -- the deletion ----------------------------------------------------------------


def test_deleting_a_teema_removes_its_pdf_render(
    normal_matter, rendered_pdf, django_capture_on_commit_callbacks
):
    keys = _rendered(rendered_pdf)
    evidence_key = rendered_pdf.storage_key
    assert all(derivative_storage().exists(key) for key in keys)

    _delete(normal_matter, django_capture_on_commit_callbacks)

    assert not DocumentDerivative.objects.filter(storage_key__in=keys).exists()
    assert not any(derivative_storage().exists(key) for key in keys), "the render survived"
    assert not evidence_storage().exists(evidence_key)


def test_deleting_a_teema_removes_its_image_copy(
    normal_matter, rendered_image, django_capture_on_commit_callbacks
):
    keys = _rendered(rendered_image)

    _delete(normal_matter, django_capture_on_commit_callbacks)

    assert not any(derivative_storage().exists(key) for key in keys), "the copy survived"


def test_each_key_goes_to_its_own_store(
    normal_matter, rendered_pdf, django_capture_on_commit_callbacks, monkeypatch
):
    """Evidence keys to the evidence store, derivative keys to the derivative
    store, and neither store is asked about the other's keys."""
    from app.documents import services
    from app.documents.extraction import orchestrator

    asked: dict[str, list[str]] = {"evidence": [], "derivative": []}

    def spying(label, factory):
        def make():
            store = factory()
            original = store.delete

            def delete(key):
                asked[label].append(key)
                return original(key)

            store.delete = delete
            return store

        return make

    monkeypatch.setattr(services, "evidence_storage", spying("evidence", services.evidence_storage))
    monkeypatch.setattr(
        orchestrator, "derivative_storage", spying("derivative", orchestrator.derivative_storage)
    )
    derivative_keys = _rendered(rendered_pdf)

    _delete(normal_matter, django_capture_on_commit_callbacks)

    assert rendered_pdf.storage_key in asked["evidence"]
    assert sorted(asked["derivative"]) == sorted(derivative_keys)
    assert not set(asked["evidence"]) & set(derivative_keys)
    assert rendered_pdf.storage_key not in asked["derivative"]


def test_another_teemas_render_survives(
    normal_matter,
    rendered_pdf,
    capture_evidence,
    extract,
    specialist,
    django_capture_on_commit_callbacks,
):
    other = factories.MatterFactory(owner=specialist)
    kept = capture_evidence(other, corpus.government_pdf(), "teine.pdf", PDF)
    extract(kept)
    kept_keys = _rendered(kept)

    _delete(normal_matter, django_capture_on_commit_callbacks)

    assert all(derivative_storage().exists(key) for key in kept_keys)
    assert evidence_storage().exists(kept.storage_key)


def test_a_deletion_that_rolls_back_deletes_no_bytes(
    normal_matter, rendered_pdf, django_capture_on_commit_callbacks
):
    """Bytes go only after the commit: a rolled-back deletion still has its rows,
    so it must still have what they point at."""
    keys = _rendered(rendered_pdf)

    class Abandoned(Exception):
        pass

    with django_capture_on_commit_callbacks(execute=True) as callbacks:
        with pytest.raises(Abandoned), transaction.atomic():
            delete_matter(matter=normal_matter)
            raise Abandoned

    assert callbacks == []
    assert all(derivative_storage().exists(key) for key in keys)
    assert evidence_storage().exists(rendered_pdf.storage_key)
    assert DocumentDerivative.objects.filter(storage_key__in=keys).count() == len(keys)


# -- finding what is left over -------------------------------------------------


def test_the_check_names_an_orphan_and_a_row_whose_object_is_gone(rendered_pdf):
    derivative_storage().save("stray/leftover.png", ContentFile(b"\x89PNG stray"))
    lost = DocumentDerivative.objects.filter(version=rendered_pdf).exclude(storage_key="").first()
    derivative_storage().delete(lost.storage_key)

    report = check_derivatives()
    grouped = report.by_kind()

    assert [finding.subject for finding in grouped[ORPHAN_DERIVATIVE_OBJECT]] == [
        "stray/leftover.png"
    ]
    assert [finding.subject for finding in grouped[MISSING_DERIVATIVE_OBJECT]] == [lost.storage_key]


def test_the_check_is_clean_when_rows_and_store_agree(rendered_pdf):
    output = StringIO()
    call_command("check_derivative_integrity", stdout=output)
    assert "No derivative integrity problems found." in output.getvalue()


def test_the_check_command_exits_non_zero_on_a_finding(rendered_pdf):
    derivative_storage().save("stray/leftover.png", ContentFile(b"\x89PNG stray"))
    output = StringIO()
    with pytest.raises(SystemExit) as exit_:
        call_command("check_derivative_integrity", stdout=output)
    assert exit_.value.code == 1
    assert "orphan-derivative-object: 1" in output.getvalue()


def _age(storage, key: str, hours: float) -> None:
    moment = time.time() - hours * 3600
    os.utime(storage.path(key), (moment, moment))


def test_the_prune_removes_only_a_confirmed_derivative_orphan(rendered_pdf, settings):
    derivatives = derivative_storage()
    orphan = derivatives.save("stray/old.png", ContentFile(b"\x89PNG old"))
    recent = derivatives.save("stray/new.png", ContentFile(b"\x89PNG new"))
    _age(derivatives, orphan, settings.EVIDENCE_ORPHAN_GRACE_HOURS + 5)
    referenced = _rendered(rendered_pdf)
    for key in referenced:
        _age(derivatives, key, settings.EVIDENCE_ORPHAN_GRACE_HOURS + 5)
    # An unreferenced *evidence* object, old enough to be pruned by the other
    # command. This one must never touch it.
    evidence = evidence_storage()
    evidence_orphan = evidence.save("stray/evidence.pdf", ContentFile(b"%PDF-1.4 stray"))
    _age(evidence, evidence_orphan, settings.EVIDENCE_ORPHAN_GRACE_HOURS + 5)

    report = StringIO()
    call_command("prune_orphaned_derivatives", stdout=report)
    assert derivatives.exists(orphan), "reporting deleted something"
    assert f"eligible\t{orphan}" in report.getvalue()
    assert f"within-grace\t{recent}" in report.getvalue()

    deleted = StringIO()
    call_command("prune_orphaned_derivatives", "--delete", stdout=deleted)

    assert "Deleted 1 orphaned object(s)." in deleted.getvalue()
    assert not derivatives.exists(orphan)
    assert derivatives.exists(recent), "a render inside the grace period was deleted"
    assert all(derivatives.exists(key) for key in referenced)
    assert evidence.exists(evidence_orphan), "the derivative pruner touched evidence"
    assert evidence.exists(rendered_pdf.storage_key)


def test_the_prune_keeps_the_grace_floor(rendered_pdf):
    with pytest.raises(CommandError, match="Derivative bytes are written before"):
        call_command("prune_orphaned_derivatives", "--delete", "--grace-hours", "0")


def test_the_evidence_pruner_still_speaks_about_evidence(rendered_pdf):
    output = StringIO()
    call_command("prune_orphaned_evidence", stdout=output)
    assert "No unreferenced evidence objects found." in output.getvalue()
