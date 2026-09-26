"""Find stored derivative objects that no `DocumentDerivative` row refers to.

A derivative binary — the rendered first page of a PDF, the downscaled copy of
an image — is written before the row that names it, exactly as evidence is:
`_store_binary` saves the object and only then records its key. So the same two
residues exist (a process dying between the two, an outer transaction rolling
back after both) and the same pruning rules apply, grace period first.

A third source made this command necessary rather than tidy. Kustuta teema
deleted every derivative key through the *evidence* store, where the key names
nothing, so the renders of every deleted Teema stayed on disk — in the backups
and on the appdata share — after the user was told the Teema was gone
(ENG-032). The deletion now uses the derivative store; this is how what it left
behind before is found, and how any future residue is.

It is `prune_orphaned_evidence` pointed at the derivative store, and it never
opens the evidence store: a derivative key is not an evidence key, and a pruner
that confused the two could delete evidence. Reporting is the default;
`--delete` removes eligible objects and counts only what actually went.

Derivatives are rebuildable (`rebuild_document_derivatives`), so a mistake here
costs a rebuild rather than a record. The grace period is kept anyway: a render
being published right now looks exactly like an orphan.
"""

from __future__ import annotations

from typing import Any

from app.documents.derivative_integrity import referenced_derivative_keys
from app.documents.extraction.orchestrator import derivative_storage
from app.documents.management.commands import prune_orphaned_evidence


class Command(prune_orphaned_evidence.Command):
    help = (
        "List (or delete) stored derivative objects that no DocumentDerivative row "
        "references and that are older than the grace period. Never touches evidence."
    )

    store_label = "derivative"

    def storage(self) -> Any:
        return derivative_storage()

    def referenced(self) -> set[str]:
        return referenced_derivative_keys()
