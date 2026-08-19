"""Matching exported pages to the 884 OneNote links the register preserved.

Stage 2A kept every hyperlink out of the workbook verbatim, as immutable source
evidence. This module is the beginning of turning those links into a reviewed
mapping — and the whole design rests on one finding from that stage:

**A OneNote hyperlink is evidence. It is not a primary key.**

The discovery work already proved links in the register can point at the wrong
page: rows were copied, sections were reorganised, and a link that resolves is
not thereby correct. So nothing here merges anything. It produces *candidates*
in confidence order, and a person decides (Stage-2B brief 59).

Five tiers, and the gap between the fourth and the fifth is the important one:

1. an exact page identifier lifted out of the link;
2. an exact canonicalised page URL;
3. an exact Matter reference token present in both;
4. a reviewed deterministic mapping supplied by hand;
5. title and year similarity — **review candidate only**, never automatic.

A tier-5 match is a suggestion for a human. It is deliberately incapable of
becoming a decision on its own, because the cost of the two errors is not
symmetric: an unmatched page waits, and a wrongly matched page puts one
ministry's correspondence into another matter's file where nobody looks for it
again.
"""

from __future__ import annotations

import difflib
import re
import urllib.parse
from dataclasses import dataclass
from enum import StrEnum


class MatchTier(StrEnum):
    PAGE_ID = "PAGE_ID"
    PAGE_URL = "PAGE_URL"
    REFERENCE_TOKEN = "REFERENCE_TOKEN"  # noqa: S105 - a register reference, not a credential
    REVIEWED_MAPPING = "REVIEWED_MAPPING"
    TITLE_SIMILARITY = "TITLE_SIMILARITY"


#: Only these are safe to apply without a person looking. The last tier is
#: absent from this set and must stay absent.
AUTOMATIC_TIERS = frozenset(
    {MatchTier.PAGE_ID, MatchTier.PAGE_URL, MatchTier.REFERENCE_TOKEN, MatchTier.REVIEWED_MAPPING}
)

#: Below this, a title match is not worth a reviewer's attention either.
TITLE_SIMILARITY_FLOOR = 0.72

#: `2019_184`, `2019-184`, `2019 184`. The register's own shape.
_REFERENCE = re.compile(r"\b(19|20)(\d{2})[ _-](\d{1,4})\b")

#: OneNote page ids appear in links as a `page-id` query parameter, and inside
#: `{...}` braces in older desktop links. Both are exact when present.
_PAGE_ID_QUERY = ("page-id", "pageid", "id")
_BRACED_ID = re.compile(r"\{[0-9A-Fa-f-]{20,}\}")


@dataclass(frozen=True)
class Candidate:
    source_reference_id: str
    page_id: str
    tier: MatchTier
    score: float
    automatic: bool
    explanation: str


def page_id_from_link(url: str) -> str:
    """The page identifier a link carries, or an empty string.

    Never a guess. A link with no identifier in it returns nothing and falls
    through to a weaker tier, which is the correct outcome — inventing one from
    a path segment is how a mismatch becomes a certainty.
    """
    if not url:
        return ""
    parsed = urllib.parse.urlsplit(url)
    query = urllib.parse.parse_qs(parsed.query)
    for key in _PAGE_ID_QUERY:
        for name, values in query.items():
            if name.lower() == key and values and values[0].strip():
                return values[0].strip()
    braced = _BRACED_ID.search(url)
    return braced.group(0).strip("{}") if braced else ""


def canonical_page_url(url: str) -> str:
    """Scheme, host, path and the identifying query, lowercased.

    Fragments and tracking parameters differ between a link copied from the
    desktop client and the same page opened in a browser; the rest does not.
    """
    if not url:
        return ""
    parsed = urllib.parse.urlsplit(url.strip())
    query = [
        (name.lower(), value)
        for name, value in urllib.parse.parse_qsl(parsed.query)
        if name.lower() in {"page-id", "pageid", "id", "section-id", "wd"}
    ]
    return urllib.parse.urlunsplit(
        (
            parsed.scheme.lower(),
            (parsed.hostname or "").lower(),
            parsed.path.rstrip("/").lower(),
            urllib.parse.urlencode(sorted(query)),
            "",
        )
    )


def reference_tokens(text: str) -> set[str]:
    """Every `YYYY_NNN` the text contains, normalised to one shape."""
    return {
        f"{century}{year}_{number.lstrip('0') or '0'}"
        for century, year, number in _REFERENCE.findall(text or "")
    }


def title_similarity(left: str, right: str) -> float:
    return difflib.SequenceMatcher(None, (left or "").lower(), (right or "").lower()).ratio()


def build_candidates(
    *,
    source_references: list[dict],
    manifest: list[dict],
    reviewed_mapping: dict[str, str] | None = None,
) -> list[Candidate]:
    """Compare register links to exported pages and rank the possibilities.

    Both inputs are plain dictionaries. This module never touches the database:
    a reconciliation run against a real export happens on a Koda-controlled
    machine, and the code that reads it must not need Django, a connection or a
    settings module to work (Stage-2B brief 60).

    Each source reference produces at most one candidate — its best. Handing a
    reviewer five possibilities per row makes the review the bottleneck.
    """
    reviewed = reviewed_mapping or {}
    by_page_id = {page["page_id"]: page for page in manifest if page.get("page_id")}
    by_url = {}
    for page in manifest:
        for url in (page.get("web_url"), page.get("client_url"), page.get("content_url")):
            canonical = canonical_page_url(url or "")
            if canonical:
                by_url.setdefault(canonical, page)

    candidates: list[Candidate] = []
    for reference in source_references:
        candidate = _best_candidate(reference, by_page_id, by_url, manifest, reviewed)
        if candidate is not None:
            candidates.append(candidate)
    return candidates


def _best_candidate(
    reference: dict,
    by_page_id: dict[str, dict],
    by_url: dict[str, dict],
    manifest: list[dict],
    reviewed: dict[str, str],
) -> Candidate | None:
    identifier = str(reference.get("id", ""))
    link = reference.get("onenote_url", "") or ""
    stored_page_id = (reference.get("onenote_page_id") or "").strip()

    page_id = stored_page_id or page_id_from_link(link)
    if page_id and page_id in by_page_id:
        return Candidate(
            source_reference_id=identifier,
            page_id=page_id,
            tier=MatchTier.PAGE_ID,
            score=1.0,
            automatic=True,
            explanation="The link carries a page identifier that the export contains.",
        )

    canonical = canonical_page_url(link)
    if canonical and canonical in by_url:
        return Candidate(
            source_reference_id=identifier,
            page_id=by_url[canonical]["page_id"],
            tier=MatchTier.PAGE_URL,
            score=1.0,
            automatic=True,
            explanation="The canonicalised link matches an exported page URL exactly.",
        )

    mapped = reviewed.get(identifier)
    if mapped and mapped in by_page_id:
        return Candidate(
            source_reference_id=identifier,
            page_id=mapped,
            tier=MatchTier.REVIEWED_MAPPING,
            score=1.0,
            automatic=True,
            explanation="A person recorded this mapping.",
        )

    tokens = reference_tokens(link) | reference_tokens(reference.get("source_title", ""))
    if tokens:
        for page in manifest:
            page_tokens = reference_tokens(page.get("title", "")) | reference_tokens(
                page.get("web_url", "")
            )
            shared = tokens & page_tokens
            if shared:
                return Candidate(
                    source_reference_id=identifier,
                    page_id=page["page_id"],
                    tier=MatchTier.REFERENCE_TOKEN,
                    score=1.0,
                    automatic=True,
                    explanation=f"Both carry the register reference {sorted(shared)[0]}.",
                )

    title = reference.get("source_title", "")
    if not title:
        return None
    best_page, best_score = None, 0.0
    for page in manifest:
        score = title_similarity(title, page.get("title", ""))
        if score > best_score:
            best_page, best_score = page, score
    if best_page is None or best_score < TITLE_SIMILARITY_FLOOR:
        return None

    return Candidate(
        source_reference_id=identifier,
        page_id=best_page["page_id"],
        tier=MatchTier.TITLE_SIMILARITY,
        score=round(best_score, 3),
        # Never automatic, at any score. A hyperlink in this register has
        # already been observed pointing at the wrong page; a similar title is
        # weaker evidence than that, not stronger.
        automatic=False,
        explanation=f"Titles are {best_score:.0%} similar. Needs review.",
    )


def summarise(candidates: list[Candidate], *, total_references: int) -> dict[str, int]:
    """Counts by tier — the only shape of this that may leave the machine."""
    counts = {tier.value: 0 for tier in MatchTier}
    for candidate in candidates:
        counts[candidate.tier.value] += 1
    counts["unmatched"] = total_references - len(candidates)
    counts["automatic"] = sum(1 for candidate in candidates if candidate.automatic)
    counts["needs_review"] = sum(1 for candidate in candidates if not candidate.automatic)
    return counts
