"""The browser suite's worker helper, held to the queue it actually drains.

`e2e/test_document_content.py::run_worker` shells out to
`extract_pending_documents`, which processes *the whole pending queue* — not the
files of whichever test called it. It used to assert `"FAILED" not in stdout`,
which is a claim about the machine rather than about the caller, and on
2026-09-09 it did what such a claim eventually does:

    uv run pytest e2e/test_uus_teema_files.py e2e/test_assisted_intake.py \
        -p no:randomly

`test_uus_teema_files.py` files placeholder bytes that are accepted for
file-handling purposes and are not PDFs. The worker correctly reported seven of
them as `unreadable_pdf`, the assisted-intake test's own two files both
succeeded, and the assisted-intake test failed anyway.

These tests run the real management command against real rows and read its real
output, because the thing under test is an agreement between two pieces of code
about the shape of a report. A hand-written stdout string would keep passing the
day the command started printing something else.
"""

from __future__ import annotations

import io

import pytest
from django.core.management import call_command

from app.documents.enums import ExtractionState
from tests import synthetic_corpus as corpus
from tests.extraction_report import (
    assert_expected_files_extracted,
    failures_among,
    problem_files,
)

pytestmark = pytest.mark.django_db

PDF = "application/pdf"


def drain(limit: int = 20) -> str:
    """Run the worker exactly as the browser helper's subprocess does."""
    out = io.StringIO()
    call_command("extract_pending_documents", limit=limit, stdout=out, no_color=True)
    return out.getvalue()


def test_an_unrelated_failure_does_not_fail_the_caller(normal_matter, capture_evidence) -> None:
    """The defect, in the smallest form that still contains it.

    Two files in one queue: somebody else's, which fails as it should, and the
    caller's, which does not. The caller is entitled to a pass.
    """
    unrelated = capture_evidence(normal_matter, corpus.corrupt_pdf(), "unrelated-invalid.pdf", PDF)
    expected = capture_evidence(normal_matter, corpus.government_pdf(), "expected-valid.pdf", PDF)

    stdout = drain()

    # The unrelated file failed, and the report still says so out loud. Scoping
    # the assertion must not quieten the report an operator reads.
    assert problem_files(stdout) == {"unrelated-invalid.pdf": "unreadable_pdf"}
    unrelated.refresh_from_db()
    assert unrelated.extraction_state == ExtractionState.FAILED

    # The caller's file came through, and the helper says so.
    expected.refresh_from_db()
    assert expected.extraction_state == ExtractionState.DONE
    assert failures_among(stdout, ["expected-valid.pdf"]) == {}
    assert_expected_files_extracted(stdout, ["expected-valid.pdf"], report=stdout)


def test_a_failure_the_caller_owns_still_fails_it(normal_matter, capture_evidence) -> None:
    """The other direction, which is the reason not to simply drop the check."""
    capture_evidence(normal_matter, corpus.corrupt_pdf(), "expected-valid.pdf", PDF)

    stdout = drain()

    with pytest.raises(AssertionError) as error:
        assert_expected_files_extracted(stdout, ["expected-valid.pdf"], report=stdout)

    # Named, with its error code: a red build that only says "something failed"
    # sends somebody to the database to find out what.
    assert "expected-valid.pdf" in str(error.value)
    assert "unreadable_pdf" in str(error.value)


def test_a_name_that_merely_contains_another_is_a_different_file(
    normal_matter, capture_evidence
) -> None:
    """Both of these exist in the browser suite, and one fails while the other does not.

    `kaaskiri.pdf` comes from `e2e/test_uus_teema_files.py` as a placeholder;
    `abistatud-katse-kaaskiri.pdf` is the assisted-intake letter. A substring
    check would let each one answer for the other, in both directions.
    """
    capture_evidence(normal_matter, corpus.corrupt_pdf(), "kaaskiri.pdf", PDF)
    capture_evidence(normal_matter, corpus.government_pdf(), "abistatud-katse-kaaskiri.pdf", PDF)

    stdout = drain()

    assert failures_among(stdout, ["abistatud-katse-kaaskiri.pdf"]) == {}
    assert failures_among(stdout, ["kaaskiri.pdf"]) == {"kaaskiri.pdf": "unreadable_pdf"}


def test_the_summary_line_is_not_read_as_a_failed_file(normal_matter, capture_evidence) -> None:
    """«FAILED: 1» is the count that started all this, and it names no file."""
    capture_evidence(normal_matter, corpus.corrupt_pdf(), "unrelated-invalid.pdf", PDF)

    stdout = drain()

    assert "FAILED" in stdout, stdout
    assert set(problem_files(stdout)) == {"unrelated-invalid.pdf"}


def test_a_worker_that_processed_nothing_names_no_files() -> None:
    """The zero-work guard is still `run_worker`'s to make, not this module's.

    Scoping the failure check must not accidentally take over the other
    assertion: an empty queue produces no problem lines, so a report-reading
    helper would happily call it a success. That is why
    ``assert "Töödeldud 0 faili" not in result.stdout`` stays.
    """
    stdout = drain()

    assert "Töödeldud 0 faili" in stdout, stdout
    assert problem_files(stdout) == {}
    assert_expected_files_extracted(stdout, ["expected-valid.pdf"], report=stdout)
