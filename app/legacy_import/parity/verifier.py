"""A repeatable, read-only comparison of Lovable's own values with what Juristid holds (r98).

The comparison itself is the independent contract oracle (`parity.oracle`): it restates the
owner-approved rules and imports nothing from the synchronisation planner, so a rule the
planner gets wrong is one this can catch. This module only (a) gives the oracle a source
view and (b) names its verdicts in the release brief's words:

=====================  ==========================================================
MATCH                  Juristid holds what Lovable states.
EXPLAINED_DIFFERENCE   A value Juristid cannot represent by the approved contract
                       (oracle NOT_REPRESENTABLE): stated, never counted as a match.
HELD                   A closure held for work only Juristid holds, with the hold
                       recorded: never a match.
MISMATCH               A genuine difference.
UNVERIFIED             Not compared (no Lovable row, no state, a deleted Teema):
                       parity over nothing is no evidence.
=====================  ==========================================================

**The source view** is the reading the nightly cycle already took (`LovableSyncSnapshot`),
so routine verification adds no Lovable traffic. It is evidence about *that* reading: a
Teema edited in Lovable after the snapshot is not a failure of the night before, and the
report says which reading it compared. Nothing is written by `verify`; the command stores
only counts and (reference, field) pairs — never a title, a name or a step's words.
"""

from __future__ import annotations

import datetime as dt
import hashlib
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from app.legacy_import.parity import oracle

MATCH = "MATCH"
EXPLAINED_DIFFERENCE = "EXPLAINED_DIFFERENCE"
HELD = "HELD"
MISMATCH = "MISMATCH"
UNVERIFIED = "UNVERIFIED"
CLASSES = (MATCH, EXPLAINED_DIFFERENCE, HELD, MISMATCH, UNVERIFIED)

_NAMES = {
    oracle.MATCH: MATCH,
    oracle.NOT_REPRESENTABLE: EXPLAINED_DIFFERENCE,
    oracle.HELD: HELD,
    oracle.DIFFERENT: MISMATCH,
    oracle.NOT_COMPARED: UNVERIFIED,
}


@dataclass
class SourceView:
    """What a reading of Lovable holds, in the shape the oracle compares."""

    rows: dict[str, dict[str, Any]] = field(default_factory=dict)
    commencements: list[dict[str, Any]] = field(default_factory=list)
    stage_links: list[dict[str, str]] = field(default_factory=list)
    taken_at: dt.datetime | None = None
    sha256: str = ""


def view_from_snapshot(
    document: dict[str, Any], taken_at: dt.datetime | None = None, sha256: str = ""
) -> SourceView:
    """The stored reading's cohort (and tonight's new topics) as rows, commencements and links."""
    view = SourceView(taken_at=taken_at, sha256=sha256 or str(document.get("sha256") or ""))
    bundles = dict(document.get("cohort") or {})
    bundles.update((document.get("new_topics") or {}).get("cohort") or {})
    for source_id, bundle in bundles.items():
        row = (bundle or {}).get("row")
        if not isinstance(row, dict):
            continue
        sid = str(source_id)
        view.rows[sid] = {**row, "id": sid}
        for item in bundle.get("commencements") or []:
            view.commencements.append({**item, "topic_id": sid})
        for link in bundle.get("stage_links") or []:
            view.stage_links.append(
                {"prev_topic_id": str(link.get("prev")), "next_topic_id": str(link.get("next"))}
            )
    return view


@dataclass
class Report:
    mode: str
    today: dt.date
    taken_at: dt.datetime | None
    sha256: str
    teemad: int
    counts: dict[str, int]
    #: (reference, field) pairs only — never a value.
    mismatches: list[tuple[str, str]]
    held: list[tuple[str, str]]
    unverified: list[tuple[str, str]]
    explained: list[tuple[str, str]]

    @property
    def fields(self) -> int:
        return sum(self.counts.values())

    @property
    def clean(self) -> bool:
        """Nothing wrong *and* nothing unexplained: no mismatch, no unverified Teema."""
        return not self.mismatches and not self.unverified and self.fields > 0


def sample_of(references: list[str], size: int, seed: str) -> list[str]:
    """A reproducible sample: references ordered by a seeded hash, the first ``size``.

    The same seed and population give the same sample on any machine; a different seed a
    different one, so repeated runs with new seeds cover the register without a schedule."""
    ranked = sorted(references, key=lambda r: hashlib.sha256(f"{seed}:{r}".encode()).hexdigest())
    return sorted(ranked[:size])


def verify(
    view: SourceView,
    *,
    today: dt.date,
    references: list[str] | None = None,
    mode: str = "full",
) -> Report:
    """Compare ``view`` with Juristid's rows. Reads only."""
    results = oracle.compare(view, today=today, references=references)
    counts: Counter[str] = Counter(dict.fromkeys(CLASSES, 0))
    pairs: dict[str, list[tuple[str, str]]] = {name: [] for name in CLASSES}
    teemad: set[str] = set()
    for item in results:
        verdict = _NAMES[item.verdict]
        counts[verdict] += 1
        pairs[verdict].append((item.reference, item.field))
        teemad.add(item.reference)
    return Report(
        mode=mode,
        today=today,
        taken_at=view.taken_at,
        sha256=view.sha256,
        teemad=len(teemad),
        counts=dict(counts),
        mismatches=pairs[MISMATCH],
        held=pairs[HELD],
        unverified=pairs[UNVERIFIED],
        explained=pairs[EXPLAINED_DIFFERENCE],
    )
