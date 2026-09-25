"""Estonian lawyer queries reach the row they mean (ENG-031).

The corpus and the reason for every case are in `tests/estonian_recall_corpus.py`.
This module holds each case to its expected outcome — found, or, for the named
residuals, not found — so a change that loses a case, or quietly starts
finding a residual, is a red build that says which query moved.
"""

from __future__ import annotations

import pytest

from app.search.services import search_documents
from tests import estonian_recall_corpus as corpus

pytestmark = pytest.mark.django_db


def test_the_corpus_is_large_enough_to_mean_something():
    assert len(corpus.CASES) >= 40
    kinds = {case.target for case in corpus.CASES}
    assert {"development", "engagement", "position", "submission", "document", "entry"} <= kinds


def test_every_query_reaches_exactly_what_it_should():
    world = corpus.build()
    moved = [
        f"{case.query!r} → {case.target}: expected "
        f"{'found' if case.found else 'not found'} ({case.why})"
        for case, hit in corpus.outcomes(world, search_documents)
        if hit != case.found
    ]
    assert moved == []
