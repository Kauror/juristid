"""Walking a notebook and writing the archive.

Structure comes from Graph's own hierarchy — notebook, section group, section,
page — and is preserved in the manifest rather than flattened. Page content is
fetched as HTML and reduced to text alongside it, never instead of it.

The one thing worth reading closely is :func:`_resource_urls`. Page HTML names
its images and file attachments by URL, those URLs are the only ones this tool
follows, and following a URL means attaching a Microsoft 365 access token to the
request. So every candidate goes through the allow-list in
:mod:`tools.onenote_export.graph`, and anything that is not a Graph resource
path on a permitted host is skipped and counted rather than fetched
(Stage-2B brief 56).

This module is deliberately import-clean: it does not import Django, and the web
application does not import it. The tool can be missing entirely and Juristid
still starts (Stage-2B brief 51).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path

from tools.onenote_export.archive import Archive, PageRecord
from tools.onenote_export.graph import GRAPH_ROOT, GraphClient, UnsafeUrl, require_safe_url


@dataclass
class ExportSummary:
    """Counts only. Deliberately nothing identifying.

    This is the object whose contents may appear in a pull request. No title, no
    URL, no filename, no company name — an aggregate is reportable and a page
    title is not (Stage-2B brief 60).
    """

    notebooks: int = 0
    section_groups: int = 0
    sections: int = 0
    pages: int = 0
    attachments: int = 0
    attachment_bytes: int = 0
    skipped_resources: int = 0
    pages_with_attachments: int = 0
    html_bytes: int = 0
    text_characters: int = 0
    errors: list[str] = field(default_factory=list)

    def as_text(self) -> str:
        return "\n".join(
            [
                f"  notebooks:            {self.notebooks}",
                f"  section groups:       {self.section_groups}",
                f"  sections:             {self.sections}",
                f"  pages:                {self.pages}",
                f"  pages w/ attachments: {self.pages_with_attachments}",
                f"  attachments:          {self.attachments}",
                f"  attachment bytes:     {self.attachment_bytes}",
                f"  skipped resources:    {self.skipped_resources}",
                f"  page HTML bytes:      {self.html_bytes}",
                f"  derived characters:   {self.text_characters}",
                f"  errors:               {len(self.errors)}",
            ]
        )


class _TextExtractor(HTMLParser):
    """OneNote page HTML into readable text.

    A small hand-rolled parser rather than a dependency: the input is one
    well-formed shape produced by one service, and `html.parser` is in the
    standard library. Script and style contents are dropped rather than folded
    into the text, for the same reason the email parser drops them.
    """

    BLOCK_TAGS = {"p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "table"}
    SKIP_TAGS = {"script", "style"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._parts: list[str] = []
        self._skipping = 0

    def handle_starttag(self, tag: str, attrs: list) -> None:
        if tag in self.SKIP_TAGS:
            self._skipping += 1
        elif tag in self.BLOCK_TAGS:
            self._parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in self.SKIP_TAGS and self._skipping:
            self._skipping -= 1
        elif tag in self.BLOCK_TAGS:
            self._parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self._skipping:
            self._parts.append(data)

    def text(self) -> str:
        joined = "".join(self._parts)
        lines = [" ".join(line.split()) for line in joined.splitlines()]
        out: list[str] = []
        for line in lines:
            if not line and out and not out[-1]:
                continue
            out.append(line)
        return "\n".join(out).strip()


def html_to_text(html: bytes) -> str:
    parser = _TextExtractor()
    parser.feed(html.decode("utf-8", errors="replace"))
    parser.close()
    return parser.text()


_ATTRIBUTE = re.compile(r'(?:src|data-fullres-src|data)\s*=\s*"([^"]+)"', re.IGNORECASE)
_ATTACHMENT_NAME = re.compile(r'data-attachment\s*=\s*"([^"]+)"', re.IGNORECASE)


def _resource_urls(html: bytes) -> list[tuple[str, str]]:
    """Candidate resource URLs and the filenames the page gave them.

    Returns candidates. Whether any of them is fetched is decided by the
    allow-list, not here — keeping extraction and authorization apart means the
    regex can be permissive without being dangerous.
    """
    text = html.decode("utf-8", errors="replace")
    names = _ATTACHMENT_NAME.findall(text)
    urls = _ATTRIBUTE.findall(text)
    out: list[tuple[str, str]] = []
    for index, url in enumerate(dict.fromkeys(urls)):
        name = names[index] if index < len(names) else _name_from_url(url)
        out.append((url, name))
    return out


def _name_from_url(url: str) -> str:
    tail = url.rstrip("/").rsplit("/", 2)
    return tail[-2] if len(tail) >= 2 else "ressurss"


def export_notebooks(
    client: GraphClient,
    archive: Archive,
    *,
    max_pages: int = 20,
    notebook_filter: str = "",
    scope: str = "/me/onenote",
) -> ExportSummary:
    """Export up to ``max_pages`` pages, hierarchy preserved.

    ``max_pages`` has a small default and no "unlimited" value on purpose. Stage
    2B authorises a bounded proof of ten to twenty pages, not a notebook
    download, and a limit that can be switched off is a limit somebody switches
    off (Stage-2B brief 61, 62).
    """
    summary = ExportSummary()
    for notebook in client.paginate(f"{GRAPH_ROOT}{scope}/notebooks"):
        name = notebook.get("displayName", "")
        if notebook_filter and notebook_filter.lower() not in name.lower():
            continue
        summary.notebooks += 1
        _export_notebook(
            client,
            archive,
            notebook=notebook,
            summary=summary,
            max_pages=max_pages,
            scope=scope,
        )
        if summary.pages >= max_pages:
            break
    return summary


def _export_notebook(
    client: GraphClient,
    archive: Archive,
    *,
    notebook: dict,
    summary: ExportSummary,
    max_pages: int,
    scope: str,
) -> None:
    notebook_id = notebook.get("id", "")
    notebook_name = notebook.get("displayName", "")

    for section in client.paginate(f"{GRAPH_ROOT}{scope}/notebooks/{notebook_id}/sections"):
        summary.sections += 1
        _export_section(
            client,
            archive,
            section=section,
            notebook_id=notebook_id,
            notebook_name=notebook_name,
            group_path=[],
            summary=summary,
            max_pages=max_pages,
            scope=scope,
        )
        if summary.pages >= max_pages:
            return

    _export_groups(
        client,
        archive,
        url=f"{GRAPH_ROOT}{scope}/notebooks/{notebook_id}/sectionGroups",
        notebook_id=notebook_id,
        notebook_name=notebook_name,
        group_path=[],
        summary=summary,
        max_pages=max_pages,
        scope=scope,
    )


def _export_groups(
    client: GraphClient,
    archive: Archive,
    *,
    url: str,
    notebook_id: str,
    notebook_name: str,
    group_path: list[str],
    summary: ExportSummary,
    max_pages: int,
    scope: str,
) -> None:
    """Section groups nest, so this recurses — with the path carried down.

    The path is what makes the hierarchy reportable later. A section called
    "2019" means nothing; "Maksundus / Arhiiv / 2019" means something.
    """
    for group in client.paginate(url):
        summary.section_groups += 1
        path = [*group_path, group.get("displayName", "")]
        group_id = group.get("id", "")

        for section in client.paginate(f"{GRAPH_ROOT}{scope}/sectionGroups/{group_id}/sections"):
            summary.sections += 1
            _export_section(
                client,
                archive,
                section=section,
                notebook_id=notebook_id,
                notebook_name=notebook_name,
                group_path=path,
                summary=summary,
                max_pages=max_pages,
                scope=scope,
            )
            if summary.pages >= max_pages:
                return

        _export_groups(
            client,
            archive,
            url=f"{GRAPH_ROOT}{scope}/sectionGroups/{group_id}/sectionGroups",
            notebook_id=notebook_id,
            notebook_name=notebook_name,
            group_path=path,
            summary=summary,
            max_pages=max_pages,
            scope=scope,
        )
        if summary.pages >= max_pages:
            return


def _export_section(
    client: GraphClient,
    archive: Archive,
    *,
    section: dict,
    notebook_id: str,
    notebook_name: str,
    group_path: list[str],
    summary: ExportSummary,
    max_pages: int,
    scope: str,
) -> None:
    section_id = section.get("id", "")
    # `pagelevel=true` is what returns a page's indentation level and order
    # within the section — the only place subpage structure is visible.
    url = f"{GRAPH_ROOT}{scope}/sections/{section_id}/pages?pagelevel=true"

    for page in client.paginate(url):
        if summary.pages >= max_pages:
            return
        try:
            _export_page(
                client,
                archive,
                page=page,
                notebook_id=notebook_id,
                notebook_name=notebook_name,
                group_path=group_path,
                section=section,
                summary=summary,
                scope=scope,
            )
        except Exception as error:  # one page must not end the run
            summary.errors.append(f"{type(error).__name__} on a page in section {section_id}")


def _export_page(
    client: GraphClient,
    archive: Archive,
    *,
    page: dict,
    notebook_id: str,
    notebook_name: str,
    group_path: list[str],
    section: dict,
    summary: ExportSummary,
    scope: str,
) -> None:
    page_id = page.get("id", "")
    content_url = page.get("contentUrl") or f"{GRAPH_ROOT}{scope}/pages/{page_id}/content"
    html = client.get(require_safe_url(content_url), accept="text/html").body
    text = html_to_text(html)

    record = PageRecord(
        page_id=page_id,
        title=page.get("title", ""),
        created_at=page.get("createdDateTime", ""),
        last_modified_at=page.get("lastModifiedDateTime", ""),
        level=page.get("level"),
        order=page.get("order"),
        notebook_id=notebook_id,
        notebook_name=notebook_name,
        section_group_path=list(group_path),
        section_id=section.get("id", ""),
        section_name=section.get("displayName", ""),
        web_url=(page.get("links", {}).get("oneNoteWebUrl", {}) or {}).get("href", ""),
        client_url=(page.get("links", {}).get("oneNoteClientUrl", {}) or {}).get("href", ""),
        content_url=content_url,
        graph_scope=scope,
    )

    attachments: list[dict] = []
    for url, filename in _resource_urls(html):
        try:
            content = client.resource_bytes(url)
        except UnsafeUrl:
            # A URL in the page that is not a Graph resource. Counted so the
            # summary can say the export was incomplete, and never fetched:
            # sending the token there is the failure this guards against.
            summary.skipped_resources += 1
            continue
        except Exception as error:  # one resource must not end the page
            summary.errors.append(f"{type(error).__name__} fetching a resource")
            continue
        stored = archive.write_attachment(page_id, filename=filename, content=content)
        attachments.append(stored)
        summary.attachments += 1
        summary.attachment_bytes += stored["size_bytes"]

    record.attachments = attachments
    archive.write_page(record, html=html, text=text)
    archive.append_manifest(record)

    summary.pages += 1
    summary.html_bytes += len(html)
    summary.text_characters += len(text)
    if attachments:
        summary.pages_with_attachments += 1


def export_to(
    root: Path,
    client: GraphClient,
    *,
    max_pages: int = 20,
    notebook_filter: str = "",
) -> ExportSummary:
    return export_notebooks(
        client, Archive(root), max_pages=max_pages, notebook_filter=notebook_filter
    )
