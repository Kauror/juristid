"""Reading the extraction worker's operator report.

`manage.py extract_pending_documents` drains a queue that belongs to nobody in
particular. Every browser test that files a document adds to it, and the worker
a test shells out to processes whatever is waiting — including the files other
tests deliberately left behind.

That makes the summary line a bad thing to assert on. «FAILED: 7» is a true
statement about the queue and says nothing about the caller: `e2e/
test_uus_teema_files.py` files placeholder bytes that are accepted for
file-handling purposes and are not PDFs, so a correct worker reports them as
`unreadable_pdf`, and any unrelated test that ran the worker afterwards failed
on somebody else's queue entry.

The command already prints the answer. Every failure is named on its own line,
with its filename and its error code, because an operator reading «7 failed»
should not have to go to the database to find out which seven. This module reads
those lines back, so a test can ask the one question that is actually about it —
«did *my* file fail?» — instead of the one that is about the whole machine.

Filenames are compared **exactly**. `abistatud-katse-kaaskiri.pdf` and
`kaaskiri.pdf` are two different files that both exist in this suite, and a
substring check would let each one answer for the other.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

#: How wide `extract_pending_documents` prints a filename before truncating it.
#: Mirrored rather than imported: this module reads the command's *output*, and
#: a test that reached into the command to learn the shape of its own report
#: would agree with it by construction.
REPORTED_FILENAME_WIDTH = 60

#: One named problem: two spaces, the filename, the error code, the note.
#:
#: Deliberately anchored on ASCII alone. The note is separated from the code by
#: an em dash and the report is full of Estonian, and a Windows console hands
#: this text back through whatever code page it feels like — so nothing here
#: depends on a character that a code page could mangle. The summary and queue
#: lines are excluded by the two-space indent, which only a named problem has.
_PROBLEM_LINE = re.compile(r"^ {2}(?P<filename>.+?): (?P<code>[a-z][a-z0-9_]*)(?= |$)")


def problem_files(stdout: str) -> dict[str, str]:
    """Every file the worker named as a problem, mapped to its error code.

    Includes more than ``FAILED``: a pass that loses its claim is reported the
    same way, and from the caller's side «somebody else took my file» is not a
    better outcome than «my file failed». What is *not* here is a file that
    simply worked — the command names problems, and silence is success.
    """
    return {
        match.group("filename"): match.group("code")
        for match in (_PROBLEM_LINE.match(line) for line in stdout.splitlines())
        if match is not None
    }


def failures_among(stdout: str, expected_files: Iterable[str]) -> dict[str, str]:
    """The subset of ``expected_files`` the worker reported as a problem.

    Empty means every file the caller named came through the worker without one
    — which is the whole claim a test is entitled to make about a shared queue.
    """
    problems = problem_files(stdout)
    return {
        filename: problems[filename[:REPORTED_FILENAME_WIDTH]]
        for filename in expected_files
        if filename[:REPORTED_FILENAME_WIDTH] in problems
    }


def assert_expected_files_extracted(
    stdout: str, expected_files: Iterable[str], *, report: str = ""
) -> None:
    """Fail if a file the caller named was reported as a problem.

    Unrelated failures are left alone on purpose. They stay visible in
    ``report``, which is what an operator — or whoever reads the next red build
    — needs to see; they are simply not this test's to fail on.
    """
    expected = list(expected_files)
    failures = failures_among(stdout, expected)
    if not failures:
        return
    named = ", ".join(f"{filename} ({code})" for filename, code in sorted(failures.items()))
    raise AssertionError(
        f"the worker failed a file this test expected to succeed: {named}\n{report or stdout}"
    )
