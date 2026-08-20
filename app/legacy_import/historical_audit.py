"""Reading the migration audit.

The audit is the reconciliation authority for this import. It compared 2,455
Excel Matters against 755 archived pages and produced five classes of answer,
and the classes are not opinions — they are how much evidence there is:

* **EXACT** (776) — the Excel hyperlink's page GUID matches an archived page's
  own GUID. Deterministic, applied automatically.
* **STRONG** (76), **REVIEW_REQUIRED** (456), **CONFLICT** (3) — a plausible
  candidate that is not safe to accept. Queued for a person.
* **UNMATCHED** (1,144 Matters, 148 pages) — no candidate at all. The Excel
  Matters import as register-only history; the OneNote pages become their own
  Matters where they look like matters.

This module reads those CSVs and nothing else. It does not re-derive matches:
re-implementing the scoring here would produce a second opinion that could
disagree with the reviewed one, and the whole point of the audit is that its
answers were checked (Stage-2D brief 13, 14).

The role classification comes from the audit too. `ARHIIV → Alkohol, tubakas`
is a level-1 page with 58 children, seven characters and no files — a drawer,
not a legislative matter — and the archive itself has no opinion about that
(Stage-2D brief 7, 8).
"""

from __future__ import annotations

import csv
from dataclasses import dataclass, field
from pathlib import Path


class AuditError(RuntimeError):
    """The audit directory is not the shape this importer expects."""


#: Every file this importer requires. Named up front so a missing one is a
#: clear message rather than an empty result set that silently imports nothing.
REQUIRED_REPORTS = (
    "reports/reconciliation/exact-matches.csv",
    "reports/reconciliation/strong-matches.csv",
    "reports/reconciliation/review-required.csv",
    "reports/reconciliation/conflicts.csv",
    "reports/reconciliation/unmatched-onenote.csv",
    "reports/onenote/onenote-summary.csv",
    "reports/onenote/page-links.csv",
    "assistant-pack/source-summary.json",
)


@dataclass(frozen=True)
class ExactMatch:
    excel_reference: str
    excel_title: str
    excel_onenote_url: str
    page_key: str
    page_id: str
    audit_row: str


@dataclass(frozen=True)
class Candidate:
    excel_reference: str
    excel_title: str
    excel_onenote_url: str
    page_key: str
    page_id: str
    candidate_class: str
    score: float
    signals: str
    conflicts: str
    explanation: str


@dataclass(frozen=True)
class PageProfile:
    """What the audit judged one archived page to be."""

    page_key: str
    section: str
    title: str
    page_order: int
    page_level: int
    parent_page: str
    child_count: int
    role: str
    role_reason: str
    text_characters: int
    block_count: int
    file_count: int
    file_bytes: int
    reference_tokens: str
    hyperlink: str = ""


@dataclass
class AuditBaseline:
    excel_sha256: str
    excel_matters: int
    excel_hyperlinks: int
    manifest_sha256: str
    pages: int
    resources: int
    resource_bytes: int
    sections: int
    reconciliation: dict[str, int] = field(default_factory=dict)


class MigrationAudit:
    """The audit output directory, read once."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        missing = [name for name in REQUIRED_REPORTS if not (self.root / name).is_file()]
        if missing:
            raise AuditError(
                f"{self.root} is missing {len(missing)} required report(s): "
                + ", ".join(missing[:4])
            )

    # -- the baseline ------------------------------------------------------

    def baseline(self) -> AuditBaseline:
        import json

        payload = json.loads(
            (self.root / "assistant-pack/source-summary.json").read_text(encoding="utf-8")
        )
        excel = payload.get("excel", {})
        onenote = payload.get("onenote", {})
        return AuditBaseline(
            excel_sha256=excel.get("sha256", ""),
            excel_matters=int(excel.get("matters") or 0),
            excel_hyperlinks=int(excel.get("onenoteHyperlinks") or 0),
            manifest_sha256=onenote.get("manifestSha256", ""),
            pages=int(onenote.get("pages") or 0),
            resources=int(onenote.get("resources") or 0),
            resource_bytes=int(onenote.get("bytes") or 0),
            sections=int(onenote.get("sections") or 0),
            reconciliation=dict(payload.get("reconciliation") or {}),
        )

    # -- reconciliation ----------------------------------------------------

    def exact_matches(self) -> list[ExactMatch]:
        return [
            ExactMatch(
                excel_reference=row["excel_ref"].strip(),
                excel_title=row.get("excel_title", "").strip(),
                excel_onenote_url=row.get("excel_onenote_url", "").strip(),
                page_key=row["onenote_page_key"].strip(),
                page_id=row.get("onenote_page_id", "").strip(),
                audit_row=f"exact-matches.csv:{row['excel_ref'].strip()}",
            )
            for row in self._rows("reports/reconciliation/exact-matches.csv")
            if row.get("excel_ref") and row.get("onenote_page_key")
        ]

    def candidates(self) -> list[Candidate]:
        """Everything a person still has to decide about, in one list."""
        out: list[Candidate] = []
        for name, klass in (
            ("strong-matches.csv", "STRONG"),
            ("review-required.csv", "REVIEW_REQUIRED"),
            ("conflicts.csv", "CONFLICT"),
        ):
            for row in self._rows(f"reports/reconciliation/{name}"):
                if not row.get("excel_ref"):
                    continue
                out.append(
                    Candidate(
                        excel_reference=row["excel_ref"].strip(),
                        excel_title=row.get("excel_title", "").strip(),
                        excel_onenote_url=row.get("excel_onenote_url", "").strip(),
                        page_key=(row.get("onenote_page_key") or "").strip(),
                        page_id=(row.get("onenote_page_id") or "").strip(),
                        candidate_class=klass,
                        score=_number(row.get("score")),
                        signals=(row.get("match_signals") or "").strip(),
                        conflicts=(row.get("conflicts") or "").strip(),
                        explanation=(row.get("explanation") or "").strip(),
                    )
                )
        return out

    def unmatched_pages(self) -> list[PageProfile]:
        """Archived pages no Excel Matter claims."""
        return [
            self._profile(row) for row in self._rows("reports/reconciliation/unmatched-onenote.csv")
        ]

    def page_profiles(self) -> dict[str, PageProfile]:
        """Every archived page, by archive key."""
        links = self._hyperlinks()
        profiles = {}
        for row in self._rows("reports/onenote/onenote-summary.csv"):
            profile = self._profile(row)
            key = profile.page_key
            profiles[key] = PageProfile(**{**profile.__dict__, "hyperlink": links.get(key, "")})
        return profiles

    def _hyperlinks(self) -> dict[str, str]:
        out = {}
        for row in self._rows("reports/onenote/page-links.csv"):
            key = (row.get("page_key") or "").strip()
            link = (row.get("onenote_hyperlink") or row.get("hyperlink") or "").strip()
            if key and link:
                out[key] = link
        return out

    def _profile(self, row: dict) -> PageProfile:
        return PageProfile(
            page_key=(row.get("page_key") or "").strip(),
            section=(row.get("section") or "").strip(),
            title=(row.get("page_title") or "").strip(),
            page_order=_integer(row.get("page_order")),
            page_level=_integer(row.get("page_level")) or 1,
            parent_page=(row.get("parent_page") or "").strip(),
            child_count=_integer(row.get("child_count")),
            role=(row.get("page_role") or "UNCLEAR").strip(),
            role_reason=(row.get("role_reason") or "").strip(),
            text_characters=_integer(row.get("text_characters")),
            block_count=_integer(row.get("block_count")),
            file_count=_integer(row.get("file_count")),
            file_bytes=_integer(row.get("file_bytes")),
            reference_tokens=(row.get("reference_tokens") or "").strip(),
        )

    def _rows(self, relative: str) -> list[dict]:
        path = self.root / relative
        if not path.is_file():
            return []
        # utf-8-sig: the audit's CSVs open in Excel, so they carry a BOM, and a
        # BOM on the first header turns `page_key` into `﻿page_key`.
        with path.open(encoding="utf-8-sig", newline="") as handle:
            return list(csv.DictReader(handle))


def _integer(raw: object) -> int:
    try:
        return int(str(raw).strip() or 0)
    except (TypeError, ValueError):
        return 0


def _number(raw: object) -> float:
    try:
        return float(str(raw).strip() or 0)
    except (TypeError, ValueError):
        return 0.0
