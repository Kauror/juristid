"""How well the deadline reader reads, measured (ADR 0223 §5).

The evaluation set (`tests/fixtures/riigikogu/deadline_cases.json`) holds:

* the date-bearing sentences of every invitation letter to koda the Riigikogu's
  register held on 24.11.2026 (2016–2026), with e-mail addresses, phone numbers
  and personal names replaced, each case's truth reviewed by hand (overrides
  carry a note saying why the reader is wrong there);
* synthetic cases for what the register does not happen to contain: a cancelled
  invitation, an extension, a weekday, a year transition, an unreadable file.

`measure` scores the reader against the truth. A date the reader *accepts*
(FOUND) that is not the truth is the error that matters: it is counted on its
own and must be zero. A date the reader sends for checking is not accepted, so it
costs coverage, never correctness.
"""

from __future__ import annotations

import datetime as dt
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.riigikogu import deadlines
from app.riigikogu.enums import DeadlineKind, ExtractionStatus

CASES = (
    Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "riigikogu" / "deadline_cases.json"
)


@dataclass
class Score:
    cases: int = 0
    #: The truth has a definite date of this kind that the reader should accept.
    extractable: int = 0
    correct: int = 0
    #: FOUND by the reader, and not the truth (a wrong day or time, or no date in truth).
    wrong_accepted: int = 0
    #: Sent for checking where the truth is a definite date: safe, but not covered.
    flagged: int = 0
    missed: int = 0
    #: The truth itself needs checking, and the reader said so.
    doubt_kept: int = 0
    #: The truth needs checking, and the reader accepted a date anyway.
    doubt_lost: int = 0
    ambiguous: int = 0
    failures: list[dict[str, Any]] = field(default_factory=list)

    @property
    def coverage(self) -> float:
        return self.correct / self.extractable if self.extractable else 1.0

    @property
    def precision(self) -> float:
        accepted = self.correct + self.wrong_accepted
        return self.correct / accepted if accepted else 1.0


def load(path: Path = CASES) -> list[dict[str, Any]]:
    return json.loads(path.read_text(encoding="utf-8"))


def _truth(case: dict[str, Any], kind: str) -> dict[str, Any] | None:
    return next((d for d in case["truth"]["dates"] if d["kind"] == kind), None)


def _same(found: deadlines.FoundDate, truth: dict[str, Any]) -> bool:
    if found.day is None or found.day.isoformat() != truth["day"]:
        return False
    wanted = truth.get("time")
    return wanted is None or (found.time is not None and found.time.strftime("%H:%M") == wanted)


def measure(
    cases: list[dict[str, Any]], kind: str = DeadlineKind.OPINION_REQUEST, split: str | None = None
) -> Score:
    score = Score()
    for case in cases:
        if split and case.get("split") != split:
            continue
        score.cases += 1
        letter = dt.date.fromisoformat(case["letter_date"]) if case.get("letter_date") else None
        result = deadlines.extract(case["text"], letter_date=letter)
        found = result.date_of(kind)
        truth = _truth(case, kind)
        accepted = found is not None and found.status == ExtractionStatus.FOUND
        if case.get("ambiguous"):
            score.ambiguous += 1
        if truth is not None and truth["status"] == ExtractionStatus.FOUND and truth.get("day"):
            score.extractable += 1
            if accepted and found is not None and _same(found, truth):
                score.correct += 1
            elif accepted:
                score.wrong_accepted += 1
                score.failures.append(
                    {
                        "id": case["id"],
                        "why": "vale kuupäev",
                        "got": str(found.day if found else ""),
                    }
                )
            elif found is not None:
                score.flagged += 1
                score.failures.append({"id": case["id"], "why": "saadetud kontrolli"})
            else:
                score.missed += 1
                score.failures.append({"id": case["id"], "why": "leidmata"})
        elif truth is not None:
            # The truth is «vajab kontrollimist».
            if accepted:
                score.doubt_lost += 1
                score.wrong_accepted += 1
                score.failures.append({"id": case["id"], "why": "kahtlane kuupäev aktsepteeritud"})
            else:
                score.doubt_kept += 1
        elif accepted:
            score.wrong_accepted += 1
            score.failures.append(
                {
                    "id": case["id"],
                    "why": "kuupäev, mida kirjas pole",
                    "got": str(found.day if found else ""),
                }
            )
    return score
