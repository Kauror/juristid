"""Security/resilience review 2026-10-29, assessor C, workstream 1: the Lovable pull.

Finding C-02 (P3, CONFIRMED): a NUL character (U+0000) in any string Lovable
returns — a cohort topic's title, or merely the ``nr`` of a topic *outside* the
cohort, which the index read still carries — survives
`app.legacy_import.lovable_sync.text_value` (``str.split`` does not split on NUL)
and `lovable_source.only_columns`, and is rejected by PostgreSQL when the
snapshot is stored as ``jsonb`` (``\\u0000 cannot be converted to text``). The
whole night then fails as ``INTERNAL: DataError``: nothing is compared, nothing
says which topic or field, and every following night fails the same way until
somebody edits Lovable. Intended fix: refuse (or strip) U+0000 — and ideally the
other C0 controls — at the transport boundary (`only_columns` / `take_snapshot`)
with a `SourceError` that names the table and the row id, so the ledger says
what happened and one bad row cannot silence the comparison of the other 99.

The two tests below FAIL on main 359e34bf. The third PASSES: it records that a
NUL does not reach a Teema through the review page's own path either way, so
the exposure is availability and diagnosability, not integrity.
"""

from __future__ import annotations

import pytest

from app.legacy_import.lovable_sync_models import SyncRunStatus
from tests.test_lovable_sync import (  # noqa: F401 - fixtures by name
    _row,
    _run,
    cohort,
    pilot_on,
    world,
)

# transaction=True: production stores the snapshot in autocommit, so the ledger
# row survives the failed INSERT and reads ``INTERNAL: DataError``. Inside the
# default per-test transaction the same failure surfaces one step later as a
# TransactionManagementError, which would misreport the production symptom.
pytestmark = pytest.mark.django_db(transaction=True, serialized_rollback=True)


def test_a_nul_in_one_cohort_title_does_not_fail_the_whole_night_c02(cohort):  # noqa: F811
    cohort["source"].edit("2026_501", teema="Näidisteema 2026_501\x00")
    run = _run(cohort)
    # Desired: the night is not lost — either the row is refused with a named
    # reason, or the character is dropped and that topic is compared like any other.
    assert not run.error.startswith("INTERNAL"), run.error
    assert run.status != SyncRunStatus.FAILED or "2026_501" in run.error


def test_a_nul_in_a_topic_outside_the_cohort_does_not_fail_the_night_c02(cohort):  # noqa: F811
    """The index read covers every live topic, so a row the job never synchronises
    can still stop the job."""
    cohort["source"].put(
        "ffffffff-ffff-4fff-8fff-ffffffffffff", _row("2026_999", nr="2026_999\x00"), version=1
    )
    run = _run(cohort)
    assert not run.error.startswith("INTERNAL"), run.error
    assert run.status != SyncRunStatus.FAILED


def test_other_c0_controls_are_stored_and_do_not_break_the_night(cohort):  # noqa: F811
    """PASSES: a C0 control other than NUL (here U+0001) and a bidi override
    (U+202E) survive `text_value` and are stored in the snapshot, and would be
    written into a title in APPLY mode as-is. Juristid's own title path does not
    strip them either (`set_matter_title` only trims and caps), so this is parity,
    not a new hole — recorded so the fix for C-02 is sized consciously."""
    cohort["source"].edit("2026_501", teema="Näidisteema\x01 ‮2026_501")
    run = _run(cohort)
    assert run.status != SyncRunStatus.FAILED, run.error
