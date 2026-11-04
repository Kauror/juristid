"""Give every register topic an explicit, auditable OneNote resolution state.

Three values are kept apart for each topic (ADR 0192):

* the **observed locator** — what Lovable's latest verified snapshot says;
* the **validated association** — what this module can prove about that
  locator against the source inventory;
* the **canonical business value** — Juristid's ``MatterSourceReference``,
  which COMPARE deliberately leaves unchanged and Phase 1 never writes.

Only an exact page GUID resolves a topic. A section or notebook link
identifies a container, not a page. A matching title produces a *candidate*
for a reviewer and nothing else. A page that is absent from the inventory, in
the recycle bin or behind a 403 is not thereby deleted; it is held.
"""

from __future__ import annotations

import unicodedata
from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum

from tools.onenote_ingress.inventory import Inventory, InventoryPage
from tools.onenote_ingress.locators import LinkFamily, ParsedLocator, TargetKind, parse_locator


class LinkState(StrEnum):
    EXACT_PAGE = "EXACT_PAGE"
    VERIFIED_ALIAS = "VERIFIED_ALIAS"  # a recorded reviewer decision maps this locator
    MULTIPLE_PAGES = "MULTIPLE_PAGES"
    CONTAINER_ONLY = "CONTAINER_ONLY"
    NO_LINK = "NO_LINK"
    AMBIGUOUS = "AMBIGUOUS"
    PAGE_NOT_FOUND = "PAGE_NOT_FOUND"  # well-formed page GUID absent from a complete inventory
    INVENTORY_INCOMPLETE = "INVENTORY_INCOMPLETE"  # absent, but the inventory has gaps
    IN_RECYCLE_BIN = "IN_RECYCLE_BIN"
    INACCESSIBLE = "INACCESSIBLE"
    OUT_OF_SCOPE = "OUT_OF_SCOPE"
    ERROR = "ERROR"


#: The only states in which a topic has exactly one validated page.
RESOLVED_STATES = frozenset({LinkState.EXACT_PAGE, LinkState.VERIFIED_ALIAS})


class ContentState(StrEnum):
    NOT_ATTEMPTED = "NOT_ATTEMPTED"
    CAPTURED = "CAPTURED"
    VERIFIED = "VERIFIED"
    PARTIAL = "PARTIAL"
    FAILED = "FAILED"
    SOURCE_CHANGED_DURING_READ = "SOURCE_CHANGED_DURING_READ"


@dataclass(frozen=True)
class ApprovedScope:
    """The one notebook Phase 1 may read. Everything else is out of scope."""

    host: str
    site_path: str  # "/sites/<name>"
    notebook_guid: str  # the sourcedoc GUID of the notebook file
    notebook_server_path: str  # "/sites/<name>/SiteAssets/<Notebook name>"


@dataclass(frozen=True)
class CohortMember:
    matter_id: str
    source_id: str  # Lovable topic UUID
    reference: str
    is_open: bool
    observed_link: str
    applied_link: str = ""
    canonical_link: str = ""
    observed_run: str = ""


@dataclass(frozen=True)
class ReviewedAlias:
    """A person decided that this raw locator means this page. Never inferred."""

    raw_locator: str
    page_object_id: str
    reviewer: str
    decided_at: str
    reason: str


@dataclass
class Resolution:
    member: CohortMember
    locator: ParsedLocator
    state: LinkState
    pages: list[InventoryPage] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    title_candidates: list[str] = field(default_factory=list)  # object ids — review only
    content_state: ContentState = ContentState.NOT_ATTEMPTED

    @property
    def resolved(self) -> bool:
        return self.state in RESOLVED_STATES and len(self.pages) == 1


def normalise_title(value: str) -> str:
    """For *candidate generation only*: NFC, casefold, collapsed whitespace."""
    return " ".join(unicodedata.normalize("NFC", value or "").casefold().split())


def _in_scope(locator: ParsedLocator, scope: ApprovedScope) -> bool:
    if locator.host and locator.host != scope.host:
        return False
    if locator.family is LinkFamily.CLIENT_ONLY:
        notebook = scope.notebook_server_path.rstrip("/") + "/"
        return locator.client_path.startswith(notebook)
    if locator.site_path and locator.site_path.lower() != scope.site_path.lower():
        return False
    if locator.notebook_guid:
        return locator.notebook_guid == scope.notebook_guid
    if locator.notebook_path:
        return locator.notebook_path.rstrip("/") == scope.notebook_server_path.rstrip("/")
    return False


def resolve_member(
    member: CohortMember,
    inventory: Inventory,
    scope: ApprovedScope,
    *,
    aliases: Mapping[str, ReviewedAlias] | None = None,
    pages_by_guid: Mapping[str, list[InventoryPage]] | None = None,
    pages_by_title: Mapping[str, list[InventoryPage]] | None = None,
) -> Resolution:
    try:
        return _resolve(member, inventory, scope, aliases or {}, pages_by_guid, pages_by_title)
    except Exception as error:
        return Resolution(
            member=member,
            locator=ParsedLocator(raw=member.observed_link, family=LinkFamily.UNPARSEABLE),
            state=LinkState.ERROR,
            notes=[f"ERROR:{type(error).__name__}"],
        )


def _resolve(
    member: CohortMember,
    inventory: Inventory,
    scope: ApprovedScope,
    aliases: Mapping[str, ReviewedAlias],
    pages_by_guid: Mapping[str, list[InventoryPage]] | None,
    pages_by_title: Mapping[str, list[InventoryPage]] | None,
) -> Resolution:
    locator = parse_locator(member.observed_link)
    resolution = Resolution(member=member, locator=locator, state=LinkState.ERROR)
    if member.canonical_link.strip() != member.observed_link.strip():
        resolution.notes.append("OBSERVED_DIFFERS_FROM_CANONICAL")
    by_guid = pages_by_guid if pages_by_guid is not None else inventory.pages_by_guid()
    by_object = {page.object_id: page for page in inventory.pages}

    if locator.family is LinkFamily.EMPTY:
        resolution.state = LinkState.NO_LINK
        return resolution
    if locator.family is LinkFamily.UNPARSEABLE:
        resolution.state = LinkState.ERROR
        resolution.notes.extend(locator.problems)
        return resolution
    if locator.family in {LinkFamily.OTHER_HOST, LinkFamily.SHAREPOINT_PATH} or not _in_scope(
        locator, scope
    ):
        resolution.state = LinkState.OUT_OF_SCOPE
        resolution.notes.append(f"FAMILY:{locator.family}")
        return resolution
    resolution.notes.extend(f"LOCATOR:{problem}" for problem in locator.problems)
    if "CLIENT_HOST_DIFFERS" in locator.problems:
        resolution.state = LinkState.AMBIGUOUS
        return resolution

    alias = aliases.get(member.observed_link)
    if alias is not None:
        target = by_object.get(alias.page_object_id)
        if target is None:
            resolution.notes.append("ALIAS_TARGET_MISSING")  # the decision stays visible
        elif locator.page_guids and target.page_guid not in locator.page_guids:
            resolution.notes.append("ALIAS_CONFLICTS_GUID")
            resolution.state = LinkState.AMBIGUOUS
            resolution.pages = [
                target,
                *(p for g in sorted(locator.page_guids) for p in by_guid.get(g, [])),
            ]
            return resolution
        else:
            resolution.pages = [target]
            resolution.notes.append(f"ALIAS_BY:{alias.reviewer}")
            resolution.state = (
                LinkState.IN_RECYCLE_BIN if target.in_recycle_bin else LinkState.VERIFIED_ALIAS
            )
            return resolution

    if locator.history_version:
        resolution.notes.append("VERSION_HISTORY_PREFIX")
    if locator.target_kind is not TargetKind.PAGE:
        resolution.state = LinkState.CONTAINER_ONLY
        _title_candidates(resolution, pages_by_title)
        if locator.section_guid and not inventory.sections_by_guid().get(locator.section_guid):
            resolution.notes.append("SECTION_NOT_IN_INVENTORY")
        return resolution

    guids = locator.page_guids
    if len(guids) > 1:
        found = [page for guid in sorted(guids) for page in by_guid.get(guid, [])]
        resolution.pages = found
        resolution.state = LinkState.MULTIPLE_PAGES if len(found) > 1 else LinkState.AMBIGUOUS
        resolution.notes.append("LOCATOR_NAMES_TWO_PAGE_GUIDS")
        return resolution

    (guid,) = guids
    hits = by_guid.get(guid, [])
    if not hits:
        section = inventory.sections_by_guid().get(
            locator.section_guid or locator.client_section_guid, []
        )
        if section and not all(s.pages_available for s in section):
            resolution.state = LinkState.INACCESSIBLE
            resolution.notes.append("SECTION_PAGES_NOT_AVAILABLE")
        else:
            # Absence proves nothing while the inventory itself has gaps.
            resolution.state = (
                LinkState.PAGE_NOT_FOUND
                if inventory.identity_complete
                else LinkState.INVENTORY_INCOMPLETE
            )
            resolution.notes.append("SECTION_PRESENT" if section else "SECTION_ABSENT")
        _title_candidates(resolution, pages_by_title)
        return resolution
    if len(hits) > 1:
        resolution.state = LinkState.MULTIPLE_PAGES
        resolution.pages = hits
        resolution.notes.append("PAGE_GUID_NOT_UNIQUE_IN_INVENTORY")
        return resolution

    (page,) = hits
    resolution.pages = [page]
    if page.in_recycle_bin:
        resolution.state = LinkState.IN_RECYCLE_BIN
        return resolution
    resolution.state = LinkState.EXACT_PAGE
    link_section = locator.section_guid or locator.client_section_guid
    if link_section and page.section_guid and link_section != page.section_guid:
        resolution.notes.append("PAGE_NOW_IN_ANOTHER_SECTION")
    hint, current = normalise_title(locator.title_hint), normalise_title(page.title)
    if hint and hint != current:
        # OneNote shortens the title it writes into a link; a prefix is not a rename.
        resolution.notes.append(
            "TITLE_HINT_TRUNCATED" if current.startswith(hint) else "TITLE_HINT_DIFFERS"
        )
    if page.archived:
        resolution.notes.append("PAGE_IN_ARCHIVE_SECTION")
    if page.page_level > 1:
        resolution.notes.append("SUBPAGE")
    return resolution


def _title_candidates(
    resolution: Resolution, pages_by_title: Mapping[str, list[InventoryPage]] | None
) -> None:
    hint = normalise_title(resolution.locator.title_hint)
    if hint and pages_by_title is not None:
        resolution.title_candidates = [page.object_id for page in pages_by_title.get(hint, [])]


def resolve_cohort(
    members: Iterable[CohortMember],
    inventory: Inventory,
    scope: ApprovedScope,
    *,
    aliases: Mapping[str, ReviewedAlias] | None = None,
) -> list[Resolution]:
    by_guid = inventory.pages_by_guid()
    by_title: dict[str, list[InventoryPage]] = {}
    for page in inventory.pages:
        by_title.setdefault(normalise_title(page.title), []).append(page)
    return [
        resolve_member(
            member,
            inventory,
            scope,
            aliases=aliases,
            pages_by_guid=by_guid,
            pages_by_title=by_title,
        )
        for member in members
    ]


@dataclass(frozen=True)
class CohortTotals:
    topics: int
    open_topics: int
    closed_topics: int
    raw_urls_nonempty: int
    distinct_raw_urls: int
    by_state: dict[str, int]
    by_family: dict[str, int]
    logical_pages_resolved: int
    shared_pages: int  # resolved pages named by more than one topic
    topics_on_shared_pages: int
    accounted: bool  # every topic has exactly one state


def totals(resolutions: list[Resolution], *, expected_source_ids: Iterable[str]) -> CohortTotals:
    """``accounted`` is true only if every expected topic appears exactly once."""
    states = Counter(str(r.state) for r in resolutions)
    families = Counter(str(r.locator.family) for r in resolutions)
    nonempty = [
        r.member.observed_link.strip() for r in resolutions if r.member.observed_link.strip()
    ]
    page_topics = Counter(r.pages[0].object_id for r in resolutions if r.resolved)
    shared = {page for page, count in page_topics.items() if count > 1}
    return CohortTotals(
        topics=len(resolutions),
        open_topics=sum(1 for r in resolutions if r.member.is_open),
        closed_topics=sum(1 for r in resolutions if not r.member.is_open),
        raw_urls_nonempty=len(nonempty),
        distinct_raw_urls=len(set(nonempty)),
        by_state=dict(sorted(states.items())),
        by_family=dict(sorted(families.items())),
        logical_pages_resolved=len(page_topics),
        shared_pages=len(shared),
        topics_on_shared_pages=sum(page_topics[page] for page in shared),
        accounted=_accounted(resolutions, expected_source_ids),
    )


def _accounted(resolutions: list[Resolution], expected_source_ids: Iterable[str]) -> bool:
    expected = list(expected_source_ids)
    seen = [r.member.source_id for r in resolutions]
    return (
        bool(expected)
        and len(seen) == len(set(seen))
        and len(expected) == len(set(expected))
        and set(seen) == set(expected)
    )
