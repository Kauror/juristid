"""A local divergence on a synchronised Teema, for the tests of what the night does to one.

Since TL-01 (audit 2026-11-11) a person can no longer change a field Lovable
owns on a synchronised Teema (`app.matters.source_ownership`). The night's
restore rule still matters: for divergences made before the lock, by a path
with no person, or by an operator. The sync tests make such a divergence
inside `divergence_from_before_the_lock()`, which lifts the guard for the
edits inside it, and for nothing else.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from unittest import mock

import pytest

from app.matters import source_ownership


@contextmanager
def divergence_from_before_the_lock() -> Iterator[None]:
    """Edits inside are let through the TL-01 guard as if no person made them.

    Patches the guard's own person test, never `is_source_owned`: modules that
    import that name (the Teema views) would keep a mock bound for good if they
    were first imported inside the patch."""
    with mock.patch.object(source_ownership, "_is_person", return_value=False):
        yield


@pytest.fixture(autouse=True)
def lock_lifted_for_the_engine_tests() -> Iterator[None]:
    """Imported by the modules that test the *synchronisation engine* — what the
    night does to a divergence however it arose — not the TL-01 lock, which
    `tests/test_source_owned_fields.py` tests. Autouse wherever it is imported."""
    with divergence_from_before_the_lock():
        yield
