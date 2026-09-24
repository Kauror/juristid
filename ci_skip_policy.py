"""Which skipped tests CI accepts, by name and by reason, and nothing else.

A skip reads as green. In CI that made three kinds of gap look like coverage
(ENG-051):

* tests whose condition depended on the thing under test — a control that was
  removed turned its own regression test into a permanent skip;
* tests whose condition depended on the seed — a shape the seeded world never
  had, so the assertion never ran;
* a missing environment variable, which would have skipped the browser gate
  wholesale and reported it green.

So in CI every skip must be one this list names: a test pattern *and* the exact
reason it states. Anything else fails the run, with the tests and their reasons
printed. Local runs keep skipping freely — a laptop without Tesseract or a
running server is not a defect — unless `JURISTID_ENFORCE_EXPECTED_SKIPS=1`.

The list is short on purpose, and each entry says why the skip is legitimate
rather than a gap. Adding to it is a reviewed decision, not a way to go green.
"""

from __future__ import annotations

import fnmatch
import os
from dataclasses import dataclass


@dataclass(frozen=True)
class ExpectedSkip:
    #: `fnmatch` pattern over the node id.
    node: str
    #: The reason, as the skip states it, or its beginning.
    reason_starts_with: str
    #: Why this skip is legitimate — kept beside it so a reviewer can judge it.
    why: str


EXPECTED_SKIPS: tuple[ExpectedSkip, ...] = (
    ExpectedSkip(
        node="e2e/*",
        reason_starts_with="the document reading is withdrawn from Uus teema (docs/adr/0088)",
        why=(
            "An optional, switched-off capability. ADR 0088 withdrew the reading from the "
            "lawyer-facing form and kept the capability behind "
            "MATTER_INTAKE_SUGGESTIONS_ENABLED, which is off in every deployment. The "
            "server-side reading is covered by pytest with the flag on "
            "(tests/test_intake_staging.py, READING_ON); these browser scenarios are what "
            "a reinstatement runs against a server started with the flag."
        ),
    ),
    ExpectedSkip(
        node="tests/test_test_isolation.py::*",
        reason_starts_with="symlink creation needs a privilege on Windows",
        why="A platform skip. CI runs on Linux, where it never fires.",
    ),
)


def enforced() -> bool:
    """Whether unexpected skips fail this run: in GitHub Actions, or on request."""
    return (
        os.environ.get("GITHUB_ACTIONS") == "true"
        or os.environ.get("JURISTID_ENFORCE_EXPECTED_SKIPS") == "1"
    )


def is_expected(node_id: str, reason: str) -> bool:
    return any(
        fnmatch.fnmatchcase(node_id, entry.node) and reason.startswith(entry.reason_starts_with)
        for entry in EXPECTED_SKIPS
    )


def unexpected(skips: list[tuple[str, str]]) -> list[tuple[str, str]]:
    """The (node id, reason) pairs no entry accepts."""
    return [(node_id, reason) for node_id, reason in skips if not is_expected(node_id, reason)]


def skip_reason(longrepr: object) -> str:
    """The reason pytest recorded for a skipped report."""
    if isinstance(longrepr, tuple) and len(longrepr) == 3:
        message = str(longrepr[2])
    else:
        message = str(longrepr or "")
    return message.removeprefix("Skipped: ")
