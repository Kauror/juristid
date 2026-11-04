"""The source inventory: every section and page the approved notebook holds.

Two readers can fill it and they are kept distinguishable:

* ``ONENOTE_DESKTOP`` — ``GetHierarchy(notebook, hsPages)`` plus
  ``GetHyperlinkToObject`` per page, read from the OneNote desktop client on a
  workstation. The hierarchy is one XML document, not a paginated listing, and
  it is in section order, so a subpage's parent *can* be derived from it: the
  nearest preceding page in the same section with a lower ``pageLevel``.
* ``GRAPH`` — ``/sites/{id}/onenote/sections/{id}/pages?pagelevel=true`` with
  ``@odata.nextLink`` continuation. A listing is bounded per response (20 by
  default, at most 100), so completeness is a property of the whole chain, which
  :class:`ListingChain` checks, not of any one response.

Desktop page GUIDs (the ``page-id`` in a client link and the last GUID in a
``wd=target(...)``) are **not** Graph page IDs (memory of the 2026-10 pilot:
"Graph page IDs don't map to desktop GUIDs"). Each identity is therefore
scoped by the reader that issued it.
"""

from __future__ import annotations

import urllib.parse
import xml.etree.ElementTree as ET
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum

from tools.onenote_ingress.locators import parse_locator

ONE_NS = "{http://schemas.microsoft.com/office/onenote/2013/onenote}"

#: The section whose pages a person has filed away. A page there is still in
#: scope, and its Matter is not closed by being there.
ARCHIVE_SECTION_NAMES = frozenset({"ARHIIV"})


#: Gaps about hierarchy shape only; every page is still listed with its identity.
STRUCTURE_ONLY_GAPS = ("SUBPAGE_WITHOUT_PARENT:",)


class Reader(StrEnum):
    ONENOTE_DESKTOP = "ONENOTE_DESKTOP"
    GRAPH = "GRAPH"


@dataclass(frozen=True)
class InventorySection:
    object_id: str  # reader-scoped
    section_guid: str  # the GUID a link's target and client link carry
    name: str
    path: str  # "Group/Section.one" relative to the notebook, case preserved
    in_recycle_bin: bool
    pages_available: bool = True


@dataclass(frozen=True)
class InventoryPage:
    object_id: str  # reader-scoped (COM page ID or Graph page id)
    page_guid: str  # desktop page GUID when the reader exposes one
    section_guid: str
    section_name: str
    title: str
    page_level: int
    order: int  # position within its section
    parent_object_id: str
    created: str
    last_modified: str
    in_recycle_bin: bool

    @property
    def archived(self) -> bool:
        return self.section_name.upper() in ARCHIVE_SECTION_NAMES


@dataclass
class Inventory:
    reader: Reader
    notebook_name: str
    notebook_path: str
    read_started: str
    read_finished: str
    sections: list[InventorySection] = field(default_factory=list)
    pages: list[InventoryPage] = field(default_factory=list)
    gaps: list[str] = field(default_factory=list)

    def pages_by_guid(self) -> dict[str, list[InventoryPage]]:
        index: dict[str, list[InventoryPage]] = {}
        for page in self.pages:
            if page.page_guid:
                index.setdefault(page.page_guid, []).append(page)
        return index

    def sections_by_guid(self) -> dict[str, list[InventorySection]]:
        index: dict[str, list[InventorySection]] = {}
        for section in self.sections:
            if section.section_guid:
                index.setdefault(section.section_guid, []).append(section)
        return index

    @property
    def complete(self) -> bool:
        return not self.gaps

    @property
    def identity_complete(self) -> bool:
        """No gap that could hide a page identity (a parentless subpage cannot)."""
        return not [gap for gap in self.gaps if not gap.startswith(STRUCTURE_ONLY_GAPS)]


def _bool(value: str | None) -> bool:
    return (value or "").lower() == "true"


def from_desktop_hierarchy(
    hierarchy_xml: str,
    links: Mapping[str, str],
    *,
    read_started: str,
    read_finished: str,
) -> Inventory:
    """Build the inventory from ``GetHierarchy`` XML and per-object hyperlinks.

    ``links`` maps a COM object ID to the ``onenote:`` hyperlink the client
    returned for it. A page or section without one is recorded as a gap, never
    skipped silently.
    """
    root = ET.fromstring(hierarchy_xml)  # noqa: S314 - trusted local client output, size-bounded by caller
    inventory = Inventory(
        reader=Reader.ONENOTE_DESKTOP,
        notebook_name=root.get("name", ""),
        notebook_path=root.get("path", ""),
        read_started=read_started,
        read_finished=read_finished,
    )
    notebook_path = inventory.notebook_path

    def walk(node: ET.Element, in_bin: bool) -> None:
        for child in node:
            tag = child.tag.removeprefix(ONE_NS)
            if tag == "SectionGroup":
                walk(child, in_bin or _bool(child.get("isRecycleBin")))
            elif tag == "Section":
                _add_section(child, in_bin)

    def _add_section(node: ET.Element, in_bin: bool) -> None:
        object_id = node.get("ID", "")
        link = parse_locator(links.get(object_id, ""))
        section_guid = link.client_section_guid
        if not section_guid:
            inventory.gaps.append(f"SECTION_WITHOUT_LINK:{object_id}")
        path = node.get("path", "")
        relative = (
            urllib.parse.unquote(path[len(notebook_path) :])
            if path.startswith(notebook_path)
            else path
        )
        available = True
        if node.get("areAllPagesAvailable", "true").lower() == "false" or _bool(node.get("locked")):
            available = False
            inventory.gaps.append(f"SECTION_NOT_FULLY_AVAILABLE:{object_id}")
        if _bool(node.get("encrypted")):
            inventory.gaps.append(f"SECTION_ENCRYPTED:{object_id}")
        inventory.sections.append(
            InventorySection(
                object_id=object_id,
                section_guid=section_guid,
                name=node.get("name", ""),
                path=relative,
                in_recycle_bin=in_bin or _bool(node.get("isInRecycleBin")),
                pages_available=available,
            )
        )
        stack: list[tuple[int, str]] = []
        for order, page in enumerate(p for p in node if p.tag == f"{ONE_NS}Page"):
            page_id = page.get("ID", "")
            level = int(page.get("pageLevel", "1") or 1)
            while stack and stack[-1][0] >= level:
                stack.pop()
            parent = stack[-1][1] if stack and level > 1 else ""
            if level > 1 and not stack:
                inventory.gaps.append(f"SUBPAGE_WITHOUT_PARENT:{page_id}")
            stack.append((level, page_id))
            page_link = parse_locator(links.get(page_id, ""))
            if not page_link.client_page_guid:
                inventory.gaps.append(f"PAGE_WITHOUT_LINK:{page_id}")
            inventory.pages.append(
                InventoryPage(
                    object_id=page_id,
                    page_guid=page_link.client_page_guid,
                    section_guid=section_guid,
                    section_name=node.get("name", ""),
                    title=page.get("name", ""),
                    page_level=level,
                    order=order,
                    parent_object_id=parent,
                    created=page.get("dateTime", ""),
                    last_modified=page.get("lastModifiedTime", ""),
                    in_recycle_bin=in_bin or _bool(page.get("isInRecycleBin")),
                )
            )

    walk(root, False)
    return inventory


# -- Graph listings ------------------------------------------------------------


class ChainError(StrEnum):
    NEXT_LINK_OFF_SCOPE = "NEXT_LINK_OFF_SCOPE"
    NEXT_LINK_REPEATED = "NEXT_LINK_REPEATED"
    DUPLICATE_ID_ACROSS_PAGES = "DUPLICATE_ID_ACROSS_PAGES"
    RESPONSE_FAILED = "RESPONSE_FAILED"
    CHAIN_ABANDONED = "CHAIN_ABANDONED"
    TOO_MANY_RESPONSES = "TOO_MANY_RESPONSES"


@dataclass
class ListingChain:
    """One enumerated collection, followed to its end or marked incomplete.

    A chain is complete only when the last response carried no
    ``@odata.nextLink``, every response succeeded and every continuation stayed
    on the scope's own origin and collection path. A continuation is used
    exactly as the provider returned it — never rebuilt into ``$skip`` or a
    cursor of our own.
    """

    scope_prefix: (
        str  # e.g. "https://graph.microsoft.com/v1.0/sites/{id}/onenote/sections/{sid}/pages"
    )
    max_responses: int = 200
    ids: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    responses: int = 0
    ended: bool = False
    _seen_links: set[str] = field(default_factory=set)

    def accept(self, payload: Mapping[str, object] | None, *, status: int = 200) -> str | None:
        """Record one response; return the continuation to request next, if any."""
        if self.ended:
            raise RuntimeError("chain already ended")
        self.responses += 1
        if status != 200 or payload is None:
            self.errors.append(f"{ChainError.RESPONSE_FAILED}:{status}")
            self.ended = True
            return None
        values = payload.get("value")
        for item in values if isinstance(values, list) else []:
            object_id = str(item.get("id", "")) if isinstance(item, Mapping) else ""
            if object_id in self.ids:
                self.errors.append(f"{ChainError.DUPLICATE_ID_ACROSS_PAGES}:{object_id}")
            self.ids.append(object_id)
        next_link = payload.get("@odata.nextLink")
        if not next_link:
            self.ended = True
            return None
        link = str(next_link)
        prefix = urllib.parse.urlsplit(self.scope_prefix)
        target = urllib.parse.urlsplit(link)
        if (target.scheme, target.netloc, target.path) != (
            prefix.scheme,
            prefix.netloc,
            prefix.path,
        ):
            self.errors.append(ChainError.NEXT_LINK_OFF_SCOPE)
            self.ended = True
            return None
        if link in self._seen_links:
            self.errors.append(ChainError.NEXT_LINK_REPEATED)
            self.ended = True
            return None
        if self.responses >= self.max_responses:
            self.errors.append(ChainError.TOO_MANY_RESPONSES)
            self.ended = True
            return None
        self._seen_links.add(link)
        return link

    def abandon(self) -> None:
        if not self.ended:
            self.errors.append(ChainError.CHAIN_ABANDONED)
            self.ended = True

    @property
    def complete(self) -> bool:
        return self.ended and not self.errors


def reconcile_scans(first: Iterable[str], second: Iterable[str]) -> dict[str, set[str]]:
    """Compare two enumerations of one scope. Differences are findings, not noise."""
    a, b = set(first), set(second)
    return {"only_first": a - b, "only_second": b - a, "both": a & b}
