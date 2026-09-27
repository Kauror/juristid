"""Do the derivatives the database describes exist, and does anything else?

The derivative store is the evidence store's rebuildable sibling
(docs/adr/0014): renders and downscaled copies made from evidence, under their
own root, and deliberately absent from `EVIDENCE_REFERENCES`, because losing one
costs a rebuild rather than a record. That is why `check_evidence_integrity`
does not look here — and why, until this module, nothing did (ENG-032, ENG-144).

Three questions, all read-only:

* **done-without-text-derivative** — a version is marked DONE and holds no
  ACTIVE text derivative. Every parser that succeeds writes one — extracted or
  recognised text — in the same transaction that says DONE (`_publish`), and a
  parse that finds no text fails rather than succeeding empty. So a DONE
  version without one lost it afterwards: its text has dropped out of search,
  `check_search_integrity` lowered its expectation with it, and
  `pending_versions` never hands a DONE version back (ENG-144). Repaired by
  `rebuild_document_derivatives --version-id`.
* **missing-derivative-object** — a `DocumentDerivative` row names a key the
  store does not hold. A thumbnail that will not load; repaired by
  `rebuild_document_derivatives`, never by editing the row.
* **orphan-derivative-object** — the store holds an object no row names. The
  residue of an interrupted publish, or of a Kustuta teema that deleted its
  renders through the wrong store before ENG-032 was fixed. Removed by
  `prune_orphaned_derivatives`, which applies a grace period this check does
  not need.

Subjects are storage keys and derivative ids, never titles or filenames, for
the same reason `app.documents.integrity` gives: this output goes to terminals,
cron mail and CI logs.
"""

from __future__ import annotations

from typing import Any

from django.db.models import Exists, OuterRef

from app.documents.integrity import UNREADABLE_PREFIX, Finding, IntegrityReport, walk_storage

DONE_WITHOUT_TEXT_DERIVATIVE = "done-without-text-derivative"
MISSING_DERIVATIVE_OBJECT = "missing-derivative-object"
ORPHAN_DERIVATIVE_OBJECT = "orphan-derivative-object"


def referenced_derivative_keys() -> set[str]:
    """Every key a derivative row names, whatever the row's status.

    A superseded or failed row that still names an object is still that
    object's owner — `discard_inactive_derivatives` removes the two together —
    so it is referenced here, and its object is not an orphan.
    """
    from app.documents.models import DocumentDerivative

    return set(
        DocumentDerivative._base_manager.exclude(storage_key="").values_list(
            "storage_key", flat=True
        )
    )


def _done_without_text() -> list[Finding]:
    """DONE versions with no ACTIVE extracted or recognised text. Ids only."""
    from app.documents.enums import DerivativeKind, DerivativeStatus, ExtractionState
    from app.documents.models import DocumentDerivative, DocumentVersion

    text = DocumentDerivative._base_manager.filter(
        version=OuterRef("pk"),
        status=DerivativeStatus.ACTIVE,
        kind__in=[DerivativeKind.EXTRACTED_TEXT, DerivativeKind.OCR_TEXT],
    )
    versions = (
        DocumentVersion._base_manager.filter(extraction_state=ExtractionState.DONE)
        .exclude(Exists(text))
        .order_by("pk")
        .values_list("pk", "mime_type")
    )
    return [
        Finding(kind=DONE_WITHOUT_TEXT_DERIVATIVE, subject=f"version {pk}", detail=mime_type)
        for pk, mime_type in versions
    ]


def check_derivatives(*, scan_storage: bool = True) -> IntegrityReport:
    """Versions against their rows, and rows against the store both ways. Reads only."""
    from app.documents.extraction.orchestrator import derivative_storage
    from app.documents.models import DocumentDerivative

    report = IntegrityReport()
    storage: Any = derivative_storage()

    report.findings.extend(_done_without_text())

    rows = (
        DocumentDerivative._base_manager.exclude(storage_key="")
        .order_by("storage_key")
        .values_list("pk", "storage_key")
    )
    referenced: set[str] = set()
    for pk, key in rows.iterator():
        referenced.add(key)
        report.objects_checked["DocumentDerivative"] = (
            report.objects_checked.get("DocumentDerivative", 0) + 1
        )
        if not storage.exists(key):
            report.findings.append(
                Finding(kind=MISSING_DERIVATIVE_OBJECT, subject=key, detail=f"derivative {pk}")
            )

    if scan_storage:
        keys, unreadable = walk_storage(storage)
        report.objects_seen = len(keys)
        for key in sorted(keys):
            if key not in referenced:
                report.findings.append(Finding(kind=ORPHAN_DERIVATIVE_OBJECT, subject=key))
        for prefix, reason in unreadable:
            report.findings.append(
                Finding(kind=UNREADABLE_PREFIX, subject=prefix or "(root)", detail=reason)
            )
    return report
