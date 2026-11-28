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


#: What changed at the source between the stored reading (the last synchronisation's) and a
#: fresh one. Not a verdict: a Teema edited in Lovable after the night is *expected* to differ
#: until the next synchronisation, and is reported as that, never as a defect.
CHANGED, NEW, GONE, REOPENED, CLOSED = "changed", "new", "gone", "reopened", "closed"
CHANGE_KINDS = (CHANGED, NEW, GONE, REOPENED, CLOSED)
CHANGED_SINCE_SYNC = "CHANGED_SINCE_SYNC"


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
    #: «snapshot» (the stored reading) or «fresh» (a reading taken for this verification).
    source: str = "snapshot"
    #: Differences explained by a source change since the synchronisation (fresh mode only):
    #: (reference, field) pairs, kept out of ``mismatches`` and counted separately.
    changed_since_sync: list[tuple[str, str]] = field(default_factory=list)
    #: kind → references (fresh mode, when the synchronisation's reading is known).
    source_changes: dict[str, list[str]] = field(default_factory=dict)
    #: The population a sample was drawn from, and the sample's size (None = not sampled).
    eligible: int = 0
    sample_size: int | None = None
    seed: str = ""
    #: Why the comparison could not be made, as a code (never a message).
    error: str = ""

    @property
    def fields(self) -> int:
        return sum(v for k, v in self.counts.items() if k in CLASSES)

    @property
    def clean(self) -> bool:
        """Nothing wrong *and* nothing unexplained: no mismatch, no unverified Teema, no held
        closure, no unexplained source change, and something actually compared. A clean SAMPLE
        is not a clean register: `complete` says whether the whole population was read."""
        return (
            not self.error
            and not self.mismatches
            and not self.unverified
            and not self.held
            and not self.changed_since_sync
            and self.fields > 0
        )

    @property
    def complete(self) -> bool:
        """The whole eligible population was compared (not a sample, not a refused reading)."""
        return self.mode == "full" and not self.error and self.fields > 0


def sample_of(references: list[str], size: int, seed: str) -> list[str]:
    """A reproducible sample: references ordered by a seeded hash, the first ``size``.

    The same seed and population give the same sample on any machine; a different seed a
    different one, so repeated runs with new seeds cover the register without a schedule."""
    ranked = sorted(references, key=lambda r: hashlib.sha256(f"{seed}:{r}".encode()).hexdigest())
    return sorted(ranked[:size])


def _canonical(value: Any) -> str:
    import json

    return json.dumps(value, sort_keys=True, default=str)


def _closed(row: dict[str, Any]) -> bool:
    return " ".join(str(row.get("hetkeseis") or "").split()) in oracle.FINISHED


def classify_changes(
    stored: SourceView, fresh: SourceView, references: list[str] | None = None
) -> dict[str, list[str]]:
    """kind → references: what differs between two readings of Lovable (no Juristid data).

    ``references`` limits the look to a sample. «gone» is a Teema present in the stored
    reading and absent from a reading that was complete and consistent: retired or deleted at
    the source since."""
    by_nr_stored = {str(r["nr"]): (sid, r) for sid, r in stored.rows.items() if r.get("nr")}
    by_nr_fresh = {str(r["nr"]): (sid, r) for sid, r in fresh.rows.items() if r.get("nr")}
    wanted = set(references) if references is not None else set(by_nr_stored) | set(by_nr_fresh)
    found: dict[str, list[str]] = {kind: [] for kind in CHANGE_KINDS}

    def children(view: SourceView, sid: str) -> str:
        return _canonical(
            [
                sorted(_canonical(c) for c in view.commencements if c.get("topic_id") == sid),
                sorted(
                    _canonical(link)
                    for link in view.stage_links
                    if sid in (link.get("prev_topic_id"), link.get("next_topic_id"))
                ),
            ]
        )

    for reference in sorted(wanted):
        before, after = by_nr_stored.get(reference), by_nr_fresh.get(reference)
        if before and not after:
            found[GONE].append(reference)
        elif after and not before:
            found[NEW].append(reference)
        elif before and after:
            (sid_b, row_b), (sid_a, row_a) = before, after
            if _canonical(row_b) != _canonical(row_a) or children(stored, sid_b) != children(
                fresh, sid_a
            ):
                found[CHANGED].append(reference)
                if _closed(row_b) and not _closed(row_a):
                    found[REOPENED].append(reference)
                elif _closed(row_a) and not _closed(row_b):
                    found[CLOSED].append(reference)
    return found


def verify(
    view: SourceView,
    *,
    today: dt.date,
    references: list[str] | None = None,
    mode: str = "full",
    stored: SourceView | None = None,
    source: str = "snapshot",
) -> Report:
    """Compare ``view`` with Juristid's rows. Reads only.

    With ``stored`` (the synchronisation's own reading) a fresh ``view`` can tell a source
    change since that night from a defect: a Teema whose Lovable side moved is reported as
    CHANGED_SINCE_SYNC (it is expected to differ until the next synchronisation); one whose
    Lovable side is unchanged and still differs stays a MISMATCH."""
    results = oracle.compare(view, today=today, references=references)
    counts: Counter[str] = Counter(dict.fromkeys(CLASSES, 0))
    pairs: dict[str, list[tuple[str, str]]] = {name: [] for name in CLASSES}
    teemad: set[str] = set()
    for item in results:
        verdict = _NAMES[item.verdict]
        counts[verdict] += 1
        pairs[verdict].append((item.reference, item.field))
        teemad.add(item.reference)
    changed_pairs: list[tuple[str, str]] = []
    changes: dict[str, list[str]] = {}
    if stored is not None:
        changes = classify_changes(stored, view, references)
        moved = set(changes[CHANGED]) | set(changes[GONE]) | set(changes[NEW])
        kept: list[tuple[str, str]] = []
        for pair in pairs[MISMATCH]:
            (changed_pairs if pair[0] in moved else kept).append(pair)
        counts[MISMATCH] -= len(changed_pairs)
        pairs[MISMATCH] = kept
        counts[CHANGED_SINCE_SYNC] = len(changed_pairs)
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
        source=source,
        changed_since_sync=changed_pairs,
        source_changes={k: v for k, v in changes.items() if v},
    )
