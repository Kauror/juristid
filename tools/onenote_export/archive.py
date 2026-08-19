"""The neutral archive format, and why it keeps two copies of every page.

The export is not a Juristid import. It is a self-describing directory that
outlives this tool, this repository and this year's decision about what a Matter
looks like::

    onenote-export/
        manifest.jsonl
        pages/
            {PAGE_ID}/
                metadata.json
                page.html      <- exactly what Graph returned
                page.txt       <- derived, for reading and matching
                attachments/
                    {sha256-prefix}-{filename}

``page.html`` is the source representation and ``page.txt`` is derived from it.
Keeping only the cleaned text would be the same mistake as storing extracted
text instead of a PDF: the tidy version is easier to read and is not the record.
Ten years of a legal department's notes are worth the duplicated bytes
(Stage-2B brief 55).

The manifest is JSON Lines rather than one JSON document, for one practical
reason: an export interrupted after 400 pages leaves 400 complete, parseable
records. A half-written JSON array leaves nothing.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path

EXPORTER_VERSION = "1.0"

#: Filesystem-safe, and short enough to survive Windows path limits inside a
#: deep OneDrive folder. Page ids are long and contain characters that are legal
#: in a URL and not in a filename.
_UNSAFE = re.compile(r"[^A-Za-z0-9._-]+")


def safe_component(value: str, *, limit: int = 120) -> str:
    cleaned = _UNSAFE.sub("-", (value or "").strip()).strip("-")
    return (cleaned or "nimetu")[:limit]


@dataclass
class PageRecord:
    """One page's provenance, kept in full because a later migration needs it.

    The hierarchy is preserved rather than flattened. A future import may use
    the notebook and section a page lived in as evidence for classification —
    or merely as the answer to "where did this come from" — and neither is
    recoverable once the tree has been thrown away (Stage-2B brief 58).
    """

    page_id: str
    title: str
    created_at: str = ""
    last_modified_at: str = ""
    level: int | None = None
    order: int | None = None
    notebook_id: str = ""
    notebook_name: str = ""
    section_group_path: list[str] = field(default_factory=list)
    section_id: str = ""
    section_name: str = ""
    web_url: str = ""
    client_url: str = ""
    content_url: str = ""
    graph_scope: str = ""
    extracted_at: str = ""
    exporter_version: str = EXPORTER_VERSION
    html_sha256: str = ""
    text_characters: int = 0
    attachments: list[dict] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


class Archive:
    """Writes the directory. Knows nothing about Graph."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.pages_root = self.root / "pages"
        self.manifest_path = self.root / "manifest.jsonl"
        self.pages_root.mkdir(parents=True, exist_ok=True)

    def page_directory(self, page_id: str) -> Path:
        directory = self.pages_root / safe_component(page_id)
        (directory / "attachments").mkdir(parents=True, exist_ok=True)
        return directory

    def write_page(self, record: PageRecord, *, html: bytes, text: str) -> Path:
        directory = self.page_directory(record.page_id)
        (directory / "page.html").write_bytes(html)
        (directory / "page.txt").write_text(text, encoding="utf-8")

        record.html_sha256 = hashlib.sha256(html).hexdigest()
        record.text_characters = len(text)
        record.extracted_at = datetime.now(UTC).isoformat()
        (directory / "metadata.json").write_text(
            json.dumps(asdict(record), ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return directory

    def write_attachment(self, page_id: str, *, filename: str, content: bytes) -> dict:
        digest = hashlib.sha256(content).hexdigest()
        name = f"{digest[:12]}-{safe_component(filename)}"
        path = self.page_directory(page_id) / "attachments" / name
        path.write_bytes(content)
        return {
            "filename": filename,
            "stored_as": name,
            "sha256": digest,
            "size_bytes": len(content),
        }

    def append_manifest(self, record: PageRecord) -> None:
        with self.manifest_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(asdict(record), ensure_ascii=False) + "\n")

    def read_manifest(self) -> list[dict]:
        if not self.manifest_path.exists():
            return []
        return [
            json.loads(line)
            for line in self.manifest_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
