"""How a parser says it could not do the job.

Two outcomes, and the difference between them is whether anybody should ever
look again.

``ExtractionFailed`` means *this file should have worked and did not* — a
corrupt PDF, a truncated container, a limit hit. It is worth an operator's
attention and worth retrying after a parser upgrade.

``ExtractionNotApplicable`` means *there is nothing here to extract, by design*
— a ZIP archive, a legacy binary Office file with no safe deterministic reader.
It is a settled state, not a queue item, and dressing it up as a failure would
fill an operator's list with entries that will never change.

Both carry a code for machines and a sentence for people. Neither carries
document content: an error message is written to logs, and logs are the one
place extracted text must never appear (Stage-2B brief 49).
"""

from __future__ import annotations


class ExtractionError(Exception):
    """Base for outcomes a parser reports rather than crashes on."""

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(f"{code}: {detail}")
        self.code = code
        self.detail = detail


class ExtractionFailed(ExtractionError):
    """The file should have been readable and was not."""


class ExtractionNotApplicable(ExtractionError):
    """Nothing to extract, deliberately, and no retry will change that."""
