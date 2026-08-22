"""Which canonical rows point at objects in the evidence store.

One place answers this, and everything that reasons about orphans asks here.

Before Stage 2H.2 the answer was spelled `DocumentVersion.objects.values_list(
"storage_key", flat=True)`, written out separately in the integrity checker and
in the pruner. That was correct while `DocumentVersion` was the only thing that
put bytes in the store. The opinion archive now puts them there too — canonical
evidence, immutable, referenced by a row that is not a `DocumentVersion` — and
two independent copies of "what counts as referenced" is exactly the shape of
mistake that ends with a pruner deleting evidence a model is holding.

So the knowledge lives here, once. A new canonical holder of evidence bytes is
added to `EVIDENCE_REFERENCES` and both the reporter and the deleter learn about
it at the same moment.

Derivatives are deliberately absent: they live in their own storage class and
are rebuildable, which is a different safety question with a different answer
(docs/adr/0014).
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class EvidenceReference:
    """One model that keeps canonical bytes in the evidence store."""

    #: Human name for a finding, so a report says which model is holding an
    #: object rather than only that something is.
    label: str
    #: Dotted path, resolved late. The documents app must not import
    #: `legacy_import` at module scope — that app already imports documents.
    model_path: str
    field: str = "storage_key"

    def model(self) -> Any:
        from django.apps import apps

        app_label, model_name = self.model_path.split(".")
        return apps.get_model(app_label, model_name)

    def keys(self) -> Iterator[str]:
        yield from self.model().objects.values_list(self.field, flat=True).iterator()


#: Every canonical holder of evidence bytes. Order is only for readable output.
EVIDENCE_REFERENCES: tuple[EvidenceReference, ...] = (
    EvidenceReference(label="DocumentVersion", model_path="documents.DocumentVersion"),
    EvidenceReference(
        label="OpinionArchiveBinary",
        model_path="legacy_import.OpinionArchiveBinary",
    ),
)


def referenced_storage_keys() -> set[str]:
    """Every evidence key a canonical row currently points at.

    A key held by *any* reference is referenced. There is no per-model view of
    this on purpose: an object is safe to delete only when nothing at all wants
    it, and asking the question one model at a time is how the second model gets
    forgotten.
    """
    keys: set[str] = set()
    for reference in EVIDENCE_REFERENCES:
        keys.update(reference.keys())
    return keys


def holder_of(key: str) -> str:
    """Which model holds this key, for a report that has already found one.

    Deliberately a second query rather than a map built alongside the set: this
    runs once per finding, findings are rare, and the alternative is holding a
    dictionary of every key in the store in memory to answer a question about a
    handful of them.
    """
    for reference in EVIDENCE_REFERENCES:
        if reference.model().objects.filter(**{reference.field: key}).exists():
            return reference.label
    return ""
