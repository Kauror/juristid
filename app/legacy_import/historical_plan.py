"""Deciding what the historical import will do, before it does any of it.

A plan is a pure function of three inputs — the Excel snapshot's hash, the
OneNote archive, and the migration audit — and it writes nothing. That
separation is what makes the dry run meaningful: apply executes *this* object,
so a dry run that rolls back has exercised the same decisions the real run will
make, rather than a second implementation that happens to agree today
(Stage-2D brief 44, 45).

Three decisions the planner makes and the reasoning behind each.

**Exact links are applied; everything else is queued.** A page GUID that matches
the GUID inside an Excel hyperlink is identity, not similarity. A 0.82 title
score is a suggestion, and the cost of the two mistakes is not symmetric: an
unmatched page waits in a queue, while a wrongly matched page files one
ministry's correspondence into another matter where nobody looks for it again.

**A OneNote page becomes a Matter only if it looks like one.** Five conditions,
all required. `ARHIIV → Alkohol, tubakas` is a level-1 page with 58 children,
seven characters of text and no files — a drawer, and turning drawers into
Matters would bury the 731 real ones.

**A OneNote-only Matter gets no register reference.** `reference_year` and
`reference_number` stay null. Minting `2019_9001` for something that never had a
register entry would put fiction in the one column the whole product treats as
identity (Stage-2D brief 16).
"""

from __future__ import annotations

import hashlib
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from app.legacy_import.historical_audit import Candidate, MigrationAudit, PageProfile
from app.legacy_import.onenote_archive import ArchivePage, OneNoteArchive

#: Only a page the audit called MATTER_LIKE may become a Matter on its own.
MATTER_ELIGIBLE_ROLE = "MATTER_LIKE"

#: And only if it carries something. A MATTER_LIKE page with no files and two
#: sentences is a note somebody started and abandoned; importing it as a Matter
#: adds a row nobody will ever open.
MINIMUM_SUBSTANCE_CHARACTERS = 200


class PlanError(RuntimeError):
    """The sources are not what the plan was told to expect."""


@dataclass
class SourcePagePlan:
    page: ArchivePage
    profile: PageProfile
    becomes_matter: bool = False
    matter_title: str = ""
    skip_reason: str = ""


@dataclass
class ExactLinkPlan:
    excel_reference: str
    page_key: str
    audit_row: str


@dataclass
class HistoricalPlan:
    """Everything the apply will do, decided and countable."""

    excel_sha256: str
    manifest_sha256: str
    archive_root: Path
    audit_root: Path

    source_pages: list[SourcePagePlan] = field(default_factory=list)
    exact_links: list[ExactLinkPlan] = field(default_factory=list)
    candidates: list[Candidate] = field(default_factory=list)

    resource_count: int = 0
    resource_bytes: int = 0
    resources_by_extension: dict[str, int] = field(default_factory=dict)
    unsupported_resource_count: int = 0

    warnings: list[str] = field(default_factory=list)
    findings: list[str] = field(default_factory=list)

    @property
    def onenote_only_matters(self) -> list[SourcePagePlan]:
        return [plan for plan in self.source_pages if plan.becomes_matter]

    @property
    def skipped_pages(self) -> list[SourcePagePlan]:
        return [plan for plan in self.source_pages if plan.skip_reason]

    def summary(self) -> dict[str, object]:
        roles: dict[str, int] = defaultdict(int)
        for plan in self.source_pages:
            roles[plan.profile.role] += 1
        classes: dict[str, int] = defaultdict(int)
        for candidate in self.candidates:
            classes[candidate.candidate_class] += 1
        return {
            "source_pages": len(self.source_pages),
            "exact_links": len(self.exact_links),
            "onenote_only_matters": len(self.onenote_only_matters),
            "candidates": len(self.candidates),
            "candidates_by_class": dict(classes),
            "pages_by_role": dict(roles),
            "resources": self.resource_count,
            "resource_bytes": self.resource_bytes,
            "unsupported_resources": self.unsupported_resource_count,
            "warnings": len(self.warnings),
        }

    def as_text(self) -> str:
        summary = self.summary()
        lines = [
            "Historical corpus plan",
            f"  archive              {self.archive_root}",
            f"  audit                {self.audit_root}",
            f"  Excel SHA-256        {self.excel_sha256[:16]}…",
            f"  archive manifest     {self.manifest_sha256[:16]}…",
            "",
            f"  source pages         {summary['source_pages']}",
            f"  exact links          {summary['exact_links']}",
            f"  OneNote-only Matters {summary['onenote_only_matters']}",
            f"  review candidates    {summary['candidates']} {summary['candidates_by_class']}",
            f"  resources            {summary['resources']} ({summary['resource_bytes']:,} bytes)",
            f"  unsupported formats  {summary['unsupported_resources']}",
            f"  pages by role        {summary['pages_by_role']}",
            f"  warnings             {summary['warnings']}",
        ]
        if self.warnings:
            lines.append("")
            lines.append("  Warnings:")
            lines.extend(f"    {warning}" for warning in self.warnings[:20])
            if len(self.warnings) > 20:
                lines.append(f"    … and {len(self.warnings) - 20} more")
        return "\n".join(lines)


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def manifest_sha256(manifest: Path) -> str:
    """The archive manifest's canonical digest.

    Sorted lines joined with LF and no trailing newline — reproduced here rather
    than trusted, because "the hash the audit printed" and "the hash of what is
    on this disk" are the two things this check exists to compare.
    """
    lines = manifest.read_text(encoding="utf-8").replace("\r\n", "\n").strip("\n").split("\n")
    return hashlib.sha256("\n".join(sorted(lines)).encode("utf-8")).hexdigest()


def build_plan(
    *,
    excel_path: Path,
    archive_root: Path,
    audit_root: Path,
    expected_excel_sha256: str = "",
    expected_manifest_sha256: str = "",
) -> HistoricalPlan:
    """Read the sources and decide. Writes nothing, anywhere."""
    archive = OneNoteArchive(archive_root)
    audit = MigrationAudit(audit_root)
    baseline = audit.baseline()

    excel_digest = file_sha256(excel_path)
    manifest_path = audit_root / "reports/source-integrity/archive-manifest.tsv"
    archive_digest = manifest_sha256(manifest_path) if manifest_path.is_file() else ""

    plan = HistoricalPlan(
        excel_sha256=excel_digest,
        manifest_sha256=archive_digest,
        archive_root=archive_root,
        audit_root=audit_root,
    )

    # -- the gate ---------------------------------------------------------
    #
    # Compared against both the expectation the operator passed in *and* the
    # audit's own record. An archive that changed since the audit ran would
    # reconcile against nothing, and the failure would surface as thousands of
    # missing files halfway through an apply.
    for label, actual, expected in (
        ("Excel", excel_digest, expected_excel_sha256 or baseline.excel_sha256),
        ("archive manifest", archive_digest, expected_manifest_sha256 or baseline.manifest_sha256),
    ):
        if expected and actual != expected:
            raise PlanError(
                f"{label} SHA-256 does not match. Expected {expected}, found {actual}. "
                "Refusing to plan an import against a source that has changed."
            )

    profiles = audit.page_profiles()
    exact_by_page: dict[str, list[str]] = defaultdict(list)
    for match in audit.exact_matches():
        plan.exact_links.append(
            ExactLinkPlan(
                excel_reference=match.excel_reference,
                page_key=match.page_key,
                audit_row=match.audit_row,
            )
        )
        exact_by_page[match.page_key].append(match.excel_reference)

    plan.candidates = audit.candidates()
    # A page with any candidate at all could still turn out to belong to an
    # Excel Matter, so it must not become a Matter of its own in the meantime —
    # that would create the duplicate the review exists to prevent.
    pages_with_candidates = {
        candidate.page_key for candidate in plan.candidates if candidate.page_key
    }

    extensions: dict[str, int] = defaultdict(int)
    for page in archive.pages():
        profile = profiles.get(page.page_key)
        if profile is None:
            plan.warnings.append(f"{page.page_key}: archived but absent from the audit summary")
            profile = PageProfile(
                page_key=page.page_key,
                section=page.section,
                title=page.title,
                page_order=page.page_order,
                page_level=page.level,
                parent_page="",
                child_count=0,
                role="UNCLEAR",
                role_reason="not present in the audit",
                text_characters=len(page.derived_text),
                block_count=len(page.blocks),
                file_count=page.file_count,
                file_bytes=page.file_bytes,
                reference_tokens="",
            )

        page_plan = SourcePagePlan(page=page, profile=profile)
        _decide_matter(page_plan, exact_by_page, pages_with_candidates)
        plan.source_pages.append(page_plan)

        for resource in page.resources:
            plan.resource_count += 1
            plan.resource_bytes += resource.size_bytes
            extension = Path(resource.original_filename).suffix.lower() or "(none)"
            extensions[extension] += 1
            if not resource.is_captured:
                plan.warnings.append(
                    f"{page.page_key}/{resource.resource_key}: {resource.download_status}"
                )

    plan.resources_by_extension = dict(sorted(extensions.items(), key=lambda kv: -kv[1]))

    from app.legacy_import.historical_materials import is_extractable

    plan.unsupported_resource_count = sum(
        count for extension, count in extensions.items() if not is_extractable(extension)
    )

    _reconcile_against_baseline(plan, baseline)
    return plan


def _decide_matter(
    plan: SourcePagePlan,
    exact_by_page: dict[str, list[str]],
    pages_with_candidates: set[str],
) -> None:
    """Five conditions, all required, in the order that explains a refusal best."""
    profile = plan.profile
    key = profile.page_key

    if profile.role != MATTER_ELIGIBLE_ROLE:
        plan.skip_reason = f"role is {profile.role}"
        return
    if key in exact_by_page:
        plan.skip_reason = f"already exactly linked to {', '.join(exact_by_page[key][:3])}"
        return
    if key in pages_with_candidates:
        # It may yet belong to an Excel Matter. Creating a Matter now and
        # linking the page later would leave two records of one thing, and
        # nothing to say which is the real one.
        plan.skip_reason = "has a review candidate; a decision may attach it to an Excel Matter"
        return
    if not plan.page.title.strip():
        plan.skip_reason = "untitled"
        return
    if profile.file_count == 0 and profile.text_characters < MINIMUM_SUBSTANCE_CHARACTERS:
        plan.skip_reason = (
            f"insubstantial: {profile.file_count} files, {profile.text_characters} characters"
        )
        return

    plan.becomes_matter = True
    plan.matter_title = plan.page.title.strip()


def _reconcile_against_baseline(plan: HistoricalPlan, baseline) -> None:
    """Compare what was planned to what the audit said, and say so either way.

    Not assertions: the baseline numbers are sanity checks, not business rules,
    and hard-coding them would make the importer refuse a corrected audit
    (Stage-2D brief 3).
    """
    checks = (
        ("source pages", len(plan.source_pages), baseline.pages),
        ("resources", plan.resource_count, baseline.resources),
        ("resource bytes", plan.resource_bytes, baseline.resource_bytes),
        ("exact links", len(plan.exact_links), baseline.reconciliation.get("EXACT", 0)),
    )
    for label, planned, expected in checks:
        if expected and planned != expected:
            plan.findings.append(f"{label}: planned {planned:,}, audit baseline says {expected:,}")
        else:
            plan.findings.append(f"{label}: {planned:,} — reconciles")
