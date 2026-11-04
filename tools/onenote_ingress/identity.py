"""Proposed identity, version and access contracts (no tables, no migrations).

Six things are kept apart, because each changes on its own schedule:

``SourcePage``            our stable internal identity for one logical page
``ProviderId``            an ID a reader issued, scoped by reader + notebook
``LocatorObservation``    a URL seen somewhere, when and by which run (append-only)
``Capture``               one immutable read of the page body
``Occurrence``            a resource *on* a page, at a position, in a capture
``Content``               bytes by SHA-256, stored once
``Association``           a reviewed link between a SourcePage (or one occurrence) and a Matter

The rules below are deliberately conservative. They encode what we can
*prove*, not what Microsoft might be doing:

* Same scoped provider ID ⇒ same identity, whatever the URL, section or title
  now says. (Observed for the desktop page GUID across section moves inside
  one notebook; not established for Graph IDs, nor across notebooks.)
* A different provider ID is a *new* identity unless an explicit continuity
  record exists (a reviewer's decision, or a provider-reported move). Equal
  title, equal filename or equal SHA-256 never merges two identities.
* A new resource ID on a page is a new occurrence. A same-named file with new
  bytes is *possibly* a replacement and goes to review; it is never filed as
  version 2 of the old one automatically.
* Losing sight of a page (403, 404, absent from a listing) is never deletion.
  Prior captures stay; the page is held for retry and then review.
"""

from __future__ import annotations

import uuid
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass, field
from enum import StrEnum


@dataclass(frozen=True)
class ProviderId:
    reader: str  # "ONENOTE_DESKTOP" | "GRAPH"
    scope: str  # notebook identity, e.g. the sourcedoc GUID / Graph site+notebook id
    value: str  # verbatim provider ID — never case-folded

    def key(self) -> tuple[str, str, str]:
        return (self.reader, self.scope, self.value)


@dataclass
class SourcePage:
    internal_id: uuid.UUID
    provider_ids: set[ProviderId] = field(default_factory=set)


@dataclass(frozen=True)
class LocatorObservation:
    source_page: uuid.UUID | None  # None until resolved
    raw_locator: str
    observed_at: str
    observed_by: str  # e.g. "lovable-snapshot:<sha256>", "graph-listing:<run>"


class LocatorHistory:
    """Append-only. There is no update or delete, by construction."""

    def __init__(self) -> None:
        self._items: list[LocatorObservation] = []

    def append(self, observation: LocatorObservation) -> None:
        if self._items and self._items[-1] == observation:
            return  # an identical repeat observation is not new history
        self._items.append(observation)

    def __iter__(self):
        return iter(tuple(self._items))

    def __len__(self) -> int:
        return len(self._items)


@dataclass(frozen=True)
class ContinuityEvidence:
    """Why two provider IDs are one page. Only these kinds are accepted."""

    old: ProviderId
    new: ProviderId
    kind: str  # "REVIEWER_DECISION" | "PROVIDER_REPORTED_MOVE"
    recorded_by: str
    recorded_at: str


ACCEPTED_CONTINUITY_KINDS = frozenset({"REVIEWER_DECISION", "PROVIDER_REPORTED_MOVE"})


class Continuity(StrEnum):
    SAME_IDENTITY = "SAME_IDENTITY"  # same scoped provider id; locator may differ
    CHANGED_ID_WITH_EVIDENCE = "CHANGED_ID_WITH_EVIDENCE"
    NEW_OR_COPIED = "NEW_OR_COPIED"


@dataclass(frozen=True)
class ObservedPage:
    provider_id: ProviderId
    locator: str
    title: str
    body_sha256: str = ""


class IdentityRegistry:
    """Maps scoped provider IDs to internal identities, conservatively."""

    def __init__(self, evidence: Iterable[ContinuityEvidence] = ()) -> None:
        self._by_provider: dict[tuple[str, str, str], SourcePage] = {}
        self._evidence = {
            item.new.key(): item for item in evidence if item.kind in ACCEPTED_CONTINUITY_KINDS
        }

    def classify(self, observed: ObservedPage) -> tuple[Continuity, SourcePage]:
        key = observed.provider_id.key()
        known = self._by_provider.get(key)
        if known is not None:
            return Continuity.SAME_IDENTITY, known
        proof = self._evidence.get(key)
        if proof is not None and proof.old.key() in self._by_provider:
            page = self._by_provider[proof.old.key()]
            page.provider_ids.add(observed.provider_id)
            self._by_provider[key] = page
            return Continuity.CHANGED_ID_WITH_EVIDENCE, page
        page = SourcePage(internal_id=uuid.uuid4(), provider_ids={observed.provider_id})
        self._by_provider[key] = page
        return Continuity.NEW_OR_COPIED, page


# -- resources ------------------------------------------------------------------


@dataclass(frozen=True)
class OccurrenceRecord:
    resource_id: str  # provider resource id (scoped by page capture)
    filename: str
    sha256: str
    ordinal: int
    # Desktop reader: the file block's one:OE/@creationTime, verbatim. Moving a page to
    # another section re-issues every object ID but keeps this time (D2 experiment,
    # 2026-11-04), so it carries an occurrence across the move. "" = unknown.
    block_created: str = ""


class ResourceChange(StrEnum):
    UNCHANGED = "UNCHANGED"
    NEW_OCCURRENCE = "NEW_OCCURRENCE"
    SAME_BYTES_NEW_OCCURRENCE = (
        "SAME_BYTES_NEW_OCCURRENCE"  # store bytes once, keep both occurrences
    )
    POSSIBLE_REPLACEMENT_REVIEW = "POSSIBLE_REPLACEMENT_REVIEW"  # same name, new id, new bytes
    CONTENT_CHANGED_SAME_ID = (
        "CONTENT_CHANGED_SAME_ID"  # same id, new bytes: new version of the occurrence
    )
    OCCURRENCE_ABSENT = "OCCURRENCE_ABSENT"  # keep prior capture; never delete
    DUPLICATE_RESOURCE_ID = "DUPLICATE_RESOURCE_ID"  # one id twice in one capture: review
    # Same page, new object id, but exactly one vanished occurrence with the same block
    # creation time and filename: the block was re-issued by a section move (D2).
    SAME_BLOCK_NEW_ID = "SAME_BLOCK_NEW_ID"
    SAME_BLOCK_NEW_ID_CONTENT_CHANGED = "SAME_BLOCK_NEW_ID_CONTENT_CHANGED"  # and new bytes


def classify_resources(
    previous: Iterable[OccurrenceRecord], current: Iterable[OccurrenceRecord]
) -> dict[str, ResourceChange]:
    """Keyed by resource id (``"absent:<id>"`` for ones no longer present).

    Both captures must be of the same source page. A resource id that occurs
    twice in one capture is reported, not collapsed: two occurrences must never
    silently become one. An empty SHA-256 means "bytes not read" and never
    matches known bytes.

    Block continuity: a new id whose (block creation time, filename) matches
    exactly one vanished previous occurrence — and no other current one — is
    that occurrence under a re-issued id, not a new file plus an absent one.
    An ambiguous or unknown block time never matches. A copied page is a
    different source page, so its blocks (which keep the original times) are
    never matched by this rule.
    """
    before_list, after_list = list(previous), list(current)
    out: dict[str, ResourceChange] = {}
    for label, items in (("previous", before_list), ("current", after_list)):
        counts = Counter(item.resource_id for item in items)
        for rid, count in counts.items():
            if count > 1:
                out[f"duplicate-{label}:{rid}"] = ResourceChange.DUPLICATE_RESOURCE_ID
    before = {item.resource_id: item for item in before_list}
    after = {item.resource_id: item for item in after_list}
    known_bytes = {item.sha256 for item in before.values() if item.sha256}
    known_names = {item.filename for item in before.values() if item.filename}

    def block(item: OccurrenceRecord) -> tuple[str, str] | None:
        return (item.block_created, item.filename) if item.block_created and item.filename else None

    vanished = Counter(block(i) for rid, i in before.items() if rid not in after and block(i))
    arrived = Counter(block(i) for rid, i in after.items() if rid not in before and block(i))
    by_block = {block(i): i for rid, i in before.items() if rid not in after and block(i)}
    carried: set[str] = set()
    for rid, item in after.items():
        old = before.get(rid)
        key = block(item)
        if old is None and key and vanished[key] == 1 and arrived[key] == 1:
            prior = by_block[key]
            carried.add(prior.resource_id)
            same = bool(prior.sha256) and prior.sha256 == item.sha256
            out[rid] = (
                ResourceChange.SAME_BLOCK_NEW_ID
                if same
                else ResourceChange.SAME_BLOCK_NEW_ID_CONTENT_CHANGED
            )
        elif old is not None:
            same = bool(old.sha256) and old.sha256 == item.sha256
            out[rid] = ResourceChange.UNCHANGED if same else ResourceChange.CONTENT_CHANGED_SAME_ID
        elif item.sha256 and item.sha256 in known_bytes:
            out[rid] = ResourceChange.SAME_BYTES_NEW_OCCURRENCE
        elif item.filename and item.filename in known_names:
            out[rid] = ResourceChange.POSSIBLE_REPLACEMENT_REVIEW
        else:
            out[rid] = ResourceChange.NEW_OCCURRENCE
    for rid in before.keys() - after.keys() - carried:
        out[f"absent:{rid}"] = ResourceChange.OCCURRENCE_ABSENT
    return out


# -- access -----------------------------------------------------------------------


class AccessState(StrEnum):
    AVAILABLE = "AVAILABLE"
    RETRY_PENDING = "RETRY_PENDING"
    ACCESS_LOST_REVIEW = "ACCESS_LOST_REVIEW"
    NOT_FOUND_REVIEW = "NOT_FOUND_REVIEW"
    UNLISTED_BUT_READABLE = "UNLISTED_BUT_READABLE"  # 200 by id, absent from its section listing


#: Consecutive failed scans before a person is asked to look.
REVIEW_AFTER_FAILURES = 3


@dataclass
class AccessTracker:
    state: AccessState = AccessState.AVAILABLE
    consecutive_failures: int = 0
    last_good_capture: str = ""  # never cleared by a failure
    last_failure: str = ""

    def observe(self, *, status: int | None, listed: bool, capture_id: str = "") -> AccessState:
        """Record one scan's view of the page.

        ``status`` None means the page was not requested this scan (a rotating
        full scan skips most pages); that is not evidence of anything and leaves
        the state alone. A page readable by id but missing from its section's
        listing is its own state — a listing gap, not a loss of access.
        """
        if status is None and listed:
            return self.state
        if status == 200 and listed:
            self.state = AccessState.AVAILABLE
            self.consecutive_failures = 0
            if capture_id:
                self.last_good_capture = capture_id
            return self.state
        if status == 200:
            self.state = AccessState.UNLISTED_BUT_READABLE
            self.last_failure = "readable but not listed"
            if capture_id:
                self.last_good_capture = capture_id
            return self.state
        if status is None:
            return self.state  # not listed and not requested: the next scan decides
        self.consecutive_failures += 1
        self.last_failure = f"status={status} listed={listed}"
        if self.consecutive_failures < REVIEW_AFTER_FAILURES:
            self.state = AccessState.RETRY_PENDING
        elif status in {401, 403}:
            self.state = AccessState.ACCESS_LOST_REVIEW
        else:
            self.state = AccessState.NOT_FOUND_REVIEW
        return self.state

    @property
    def may_promote(self) -> bool:
        """New canonical documents are promoted only from an available source."""
        return self.state is AccessState.AVAILABLE
