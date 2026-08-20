"""A fictional OneNote archive and migration audit, generated at test time.

The real corpus is 755 pages, 10,916 files and 4.14 GiB of members' legal work.
None of it may enter this repository, and none of it does: everything the
historical tests read is built here, in a temporary directory, from the shapes
described below (Stage-2D brief 50, 78).

The point of building the *archive* rather than mocking the reader is that the
interesting bugs in this importer are layout bugs. A file block whose ordinal
does not match its resource, a page whose capture directory is named something
else, a manifest whose digest is computed over unsorted lines — a mock answers
all of those correctly and proves nothing.

Nine pages, chosen so that every branch the planner can take is a page somebody
could point at::

    exactly-linked      claimed by one Excel Matter through its page GUID
    shared-page         claimed by two — the same file, two register rows
    onenote-only        substantive, unclaimed: becomes a Matter of its own
    container-page      a drawer ("Alkohol, tubakas"): 7 characters, 58 children
    untitled-page       substantive but nameless
    thin-page           titled, no files, 40 characters
    candidate-page      substantive and unclaimed, but a review is pending
    ambiguous-page      raw XML order and visual order disagreed
    signed-container    one .asice nothing will ever parse

Every name is invented; `.invalid` cannot resolve, by RFC.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path

NOTEBOOK = "Näidiskoja õigusloome"
SECTION = "ARHIIV 2019"

#: One word per page, so a search or rendering test can prove *which* page
#: answered rather than merely that something did.
ONLY_ON_EXACT_PAGE = "kaubaaluste"
ONLY_ON_SHARED_PAGE = "vahearuandlus"
ONLY_ON_ONENOTE_ONLY_PAGE = "hoiustamiskulu"
ONLY_ON_AMBIGUOUS_PAGE = "üleminekutähtaeg"

#: Enough characters that the planner's substance test passes without the
#: fixture having to carry a page of invented Estonian.
_SUBSTANCE = (
    "Näidisministeerium saatis eelnõu kooskõlastusringile. Koda esitas oma "
    "seisukoha tähtaegselt ning juhtis tähelepanu rakendusaja pikkusele, "
    "halduskoormusele ja üleminekusätete puudumisele. Vastuseks lubati "
    "eelnõu teksti täpsustada enne Riigikogule esitamist. "
)


@dataclass
class ResourceSpec:
    resource_key: str
    filename: str
    content: bytes
    ordinal: int
    kind: str = "FILE_ATTACHMENT"
    download_status: str = "CAPTURED"

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.content).hexdigest()


@dataclass
class PageSpec:
    page_key: str
    page_id: str
    title: str
    blocks: list[dict]
    role: str = "MATTER_LIKE"
    level: int = 2
    parent_page: str = ""
    child_count: int = 0
    section: str = SECTION
    ambiguous: bool = False
    links: list[dict] = field(default_factory=list)
    resources: list[ResourceSpec] = field(default_factory=list)
    capture_id: str = "c0000001"

    @property
    def text(self) -> str:
        return "\n".join(b.get("text", "") for b in self.blocks if b.get("text"))

    @property
    def file_bytes(self) -> int:
        return sum(len(r.content) for r in self.resources)


def _text(ordinal: int, body: str, *, kind: str = "TEXT", depth: int = 0) -> dict:
    return {"type": kind, "sourceOrdinal": ordinal, "text": body, "depth": depth}


def _file(ordinal: int) -> dict:
    return {"type": "FILE_ATTACHMENT", "sourceOrdinal": ordinal, "text": ""}


def _pdf(marker: str) -> bytes:
    """A tiny but genuinely-shaped PDF. Bytes, so the SHA-256 check is real."""
    lines = [
        "%PDF-1.4",
        "% " + marker,
        "1 0 obj<</Type/Catalog>>endobj",
        "trailer<</Root 1 0 R>>",
        "%%EOF",
        "",
    ]
    return "\n".join(lines).encode("utf-8")


def page_specs() -> list[PageSpec]:
    """The nine pages, in the order the story above lists them."""
    return [
        PageSpec(
            page_key="p-exact",
            page_id="1-aaaa1111aaaa1111aaaa1111aaaa1111",
            title="Pakendiseaduse muutmise eelnõu",
            blocks=[
                _text(1, "Pakendiseaduse muutmise eelnõu", kind="TITLE"),
                _text(2, _SUBSTANCE + "Eraldi käsitleti " + ONLY_ON_EXACT_PAGE + " arvestust."),
                _text(3, "Ettepaneku eestikeelne variant:"),
                _file(4),
                _text(5, "Ministeeriumi vastus saabus kuu hiljem.", kind="LIST_ITEM", depth=1),
            ],
            links=[{"url": "https://eelnoud.example.invalid/19-0001", "displayText": "EIS"}],
            resources=[
                ResourceSpec("r-exact-1", "ettepanek.pdf", _pdf(ONLY_ON_EXACT_PAGE), 4),
            ],
        ),
        PageSpec(
            page_key="p-shared",
            page_id="1-bbbb2222bbbb2222bbbb2222bbbb2222",
            title="Jäätmeseaduse ja pakendiseaduse ühine kooskõlastus",
            blocks=[
                _text(1, _SUBSTANCE + "Küsimuseks jäi " + ONLY_ON_SHARED_PAGE + "."),
                _file(2),
            ],
            resources=[
                ResourceSpec("r-shared-1", "seisukoht.pdf", _pdf(ONLY_ON_SHARED_PAGE), 2),
            ],
        ),
        PageSpec(
            page_key="p-onenote-only",
            page_id="1-cccc3333cccc3333cccc3333cccc3333",
            title="Tolliprotseduuride töörühm",
            blocks=[
                _text(1, _SUBSTANCE + "Arutati " + ONLY_ON_ONENOTE_ONLY_PAGE + " jaotust."),
                _file(2),
            ],
            resources=[
                ResourceSpec("r-only-1", "protokoll.pdf", _pdf(ONLY_ON_ONENOTE_ONLY_PAGE), 2),
            ],
        ),
        PageSpec(
            page_key="p-container",
            page_id="1-dddd4444dddd4444dddd4444dddd4444",
            title="Alkohol, tubakas",
            blocks=[_text(1, "Alkohol")],
            role="CATEGORY_OR_CONTAINER",
            level=1,
            child_count=58,
        ),
        PageSpec(
            page_key="p-untitled",
            page_id="1-eeee5555eeee5555eeee5555eeee5555",
            title="",
            blocks=[_text(1, _SUBSTANCE)],
        ),
        PageSpec(
            page_key="p-thin",
            page_id="1-ffff6666ffff6666ffff6666ffff6666",
            title="Märkmed",
            blocks=[_text(1, "Helistada ministeeriumi nõunikule.")],
        ),
        PageSpec(
            page_key="p-candidate",
            page_id="1-aaaa7777aaaa7777aaaa7777aaaa7777",
            title="Alkoholiaktsiisi eelnõu märkused",
            blocks=[_text(1, _SUBSTANCE), _file(2)],
            resources=[ResourceSpec("r-cand-1", "markused.pdf", _pdf("markused"), 2)],
        ),
        PageSpec(
            page_key="p-ambiguous",
            page_id="1-bbbb8888bbbb8888bbbb8888bbbb8888",
            title="Vabas vormis kanvaa",
            blocks=[
                _text(1, _SUBSTANCE + "Tähtajaks jäi " + ONLY_ON_AMBIGUOUS_PAGE + "."),
                _text(2, "Teine tekstikast, mis paigutuses asub esimesest vasakul."),
            ],
            ambiguous=True,
        ),
        PageSpec(
            page_key="p-signed",
            page_id="1-cccc9999cccc9999cccc9999cccc9999",
            title="Allkirjastatud seisukoht",
            blocks=[_text(1, _SUBSTANCE), _file(2), _file(3)],
            resources=[
                ResourceSpec("r-signed-1", "seisukoht.asice", b"PK\x03\x04 ASiC-E container", 2),
                ResourceSpec("r-signed-2", "vorm.xltx", b"PK\x03\x04 template", 3),
            ],
        ),
    ]


#: Which Excel references the audit says claim which page. `2019_9` is
#: deliberately absent from the register the tests import, so the "a reference
#: the register does not contain is reported, not invented" path has a case.
EXACT_MATCHES = (
    ("2019_1", "Pakendiseaduse muutmise eelnõu", "p-exact"),
    ("2019_2", "Jäätmeseaduse kooskõlastus", "p-shared"),
    ("2019_3", "Pakendiaruandluse tähtajad", "p-shared"),
    ("2019_9", "Registris puuduv rida", "p-exact"),
)

#: Class, Excel reference, page key, score.
CANDIDATES = (
    ("STRONG", "2019_4", "p-candidate", 0.91),
    ("REVIEW_REQUIRED", "2019_5", "p-candidate", 0.44),
    ("CONFLICT", "2019_6", "p-ambiguous", 0.62),
)

_PROFILE_COLUMNS = (
    "page_key",
    "section",
    "page_title",
    "page_order",
    "page_level",
    "parent_page",
    "child_count",
    "page_role",
    "role_reason",
    "text_characters",
    "block_count",
    "file_count",
    "file_bytes",
    "reference_tokens",
)


def _csv(path: Path, columns: tuple[str, ...], rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [",".join(columns)]
    for row in rows:
        cells = []
        for column in columns:
            value = str(row.get(column, ""))
            if any(character in value for character in ',"\n'):
                value = '"' + value.replace('"', '""') + '"'
            cells.append(value)
        lines.append(",".join(cells))
    # utf-8-sig, because the real audit's CSVs open in Excel and carry a BOM —
    # and a reader that forgets that reads the first column name as "﻿page_key".
    path.write_text("\n".join(lines) + "\n", encoding="utf-8-sig")


def build_archive(root: Path, pages: list[PageSpec] | None = None) -> Path:
    """Write a OneNote desktop archive under `root` and return it."""
    pages = pages if pages is not None else page_specs()
    root.mkdir(parents=True, exist_ok=True)
    (root / "archive.json").write_text(
        json.dumps({"archiveVersion": "desktop/1", "notebook": NOTEBOOK}, ensure_ascii=False),
        encoding="utf-8",
    )

    for order, spec in enumerate(pages, start=1):
        directory = root / "pages" / spec.page_key
        capture = directory / "captures" / spec.capture_id
        capture.mkdir(parents=True, exist_ok=True)

        xml = (
            '<?xml version="1.0"?><one:Page xmlns:one="http://schemas.microsoft.com/office/'
            'onenote/2013/onenote" ID="'
            + spec.page_id
            + '"><one:Title>'
            + spec.title
            + "</one:Title></one:Page>"
        ).encode("utf-8")
        (capture / "page.source.xml").write_bytes(xml)
        (capture / "page.txt").write_text(spec.text, encoding="utf-8")
        (capture / "blocks.json").write_text(
            json.dumps(
                {
                    "blocks": spec.blocks,
                    "readingOrder": "VISUAL_THEN_XML",
                    "readingOrderAmbiguous": spec.ambiguous,
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        (capture / "links.json").write_text(
            json.dumps({"links": spec.links}, ensure_ascii=False), encoding="utf-8"
        )

        (directory / "page.json").write_text(
            json.dumps(
                {
                    "archivePageKey": spec.page_key,
                    "title": spec.title,
                    "level": spec.level,
                    "observedPageOrder": order,
                    "createdDateTime": "2019-03-04T09:12:00Z",
                    "lastModifiedDateTime": "2019-05-21T14:40:00Z",
                    "source": {"pageId": spec.page_id},
                    "section": {"displayName": spec.section},
                    "originalSource": {
                        "notebookName": NOTEBOOK,
                        "sectionName": spec.section,
                        "sectionGroupPath": "",
                        "pageTitle": spec.title,
                    },
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        (directory / "current.json").write_text(
            json.dumps(
                {"captureId": spec.capture_id, "xmlSha256": hashlib.sha256(xml).hexdigest()},
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        for resource in spec.resources:
            holder = directory / "resources" / resource.resource_key
            original = holder / "original"
            original.mkdir(parents=True, exist_ok=True)
            (original / resource.filename).write_bytes(resource.content)
            # Backslashes on purpose: the real archive was written on Windows,
            # and the reader has to normalise them.
            windows_path = f"resources\\{resource.resource_key}\\original\\{resource.filename}"
            (holder / "resource.json").write_text(
                json.dumps(
                    {
                        "archiveResourceKey": resource.resource_key,
                        "originalFilename": resource.filename,
                        "resourceKind": resource.kind,
                        "sourceOrdinal": resource.ordinal,
                        "sha256": resource.sha256,
                        "sizeBytes": len(resource.content),
                        "originalPath": windows_path,
                        "inlineOrFile": "file",
                        "downloadStatus": resource.download_status,
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
    return root


def build_manifest(archive_root: Path, audit_root: Path) -> str:
    """Hash every archived file into the manifest, and return its digest."""
    rows = []
    for path in sorted(archive_root.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(archive_root).as_posix()
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        rows.append(f"{relative}\t{path.stat().st_size}\t{digest}")

    manifest = audit_root / "reports/source-integrity/archive-manifest.tsv"
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text("\n".join(rows) + "\n", encoding="utf-8")
    return hashlib.sha256("\n".join(sorted(rows)).encode("utf-8")).hexdigest()


def build_audit(
    audit_root: Path,
    archive_root: Path,
    *,
    pages: list[PageSpec] | None = None,
    excel_sha256: str = "",
    manifest_sha256_override: str = "",
) -> Path:
    """Write the migration audit reports that describe `archive_root`."""
    pages = pages if pages is not None else page_specs()
    audit_root.mkdir(parents=True, exist_ok=True)
    manifest_digest = manifest_sha256_override or build_manifest(archive_root, audit_root)

    exact_rows = [
        {
            "excel_ref": reference,
            "excel_title": title,
            "excel_onenote_url": "onenote:///archive#page-id={" + key + "}",
            "onenote_page_key": key,
            "onenote_page_id": next(p.page_id for p in pages if p.page_key == key),
        }
        for reference, title, key in EXACT_MATCHES
    ]
    _csv(
        audit_root / "reports/reconciliation/exact-matches.csv",
        ("excel_ref", "excel_title", "excel_onenote_url", "onenote_page_key", "onenote_page_id"),
        exact_rows,
    )

    candidate_columns = (
        "excel_ref",
        "excel_title",
        "excel_onenote_url",
        "onenote_page_key",
        "onenote_page_id",
        "score",
        "match_signals",
        "conflicts",
        "explanation",
    )
    by_class: dict[str, list[dict]] = {"STRONG": [], "REVIEW_REQUIRED": [], "CONFLICT": []}
    for klass, reference, key, score in CANDIDATES:
        by_class[klass].append(
            {
                "excel_ref": reference,
                "excel_title": "Register " + reference,
                "excel_onenote_url": "",
                "onenote_page_key": key,
                "onenote_page_id": next(p.page_id for p in pages if p.page_key == key),
                "score": score,
                "match_signals": "pealkirja kattuvus; sama aasta",
                "conflicts": "kaks registrikirjet sama lehe kohta" if klass == "CONFLICT" else "",
                "explanation": "automaatne hinnang",
            }
        )
    for klass, name in (
        ("STRONG", "strong-matches.csv"),
        ("REVIEW_REQUIRED", "review-required.csv"),
        ("CONFLICT", "conflicts.csv"),
    ):
        _csv(audit_root / ("reports/reconciliation/" + name), candidate_columns, by_class[klass])

    profiles = [
        {
            "page_key": spec.page_key,
            "section": spec.section,
            "page_title": spec.title,
            "page_order": order,
            "page_level": spec.level,
            "parent_page": spec.parent_page,
            "child_count": spec.child_count,
            "page_role": spec.role,
            "role_reason": "sünteetiline profiil",
            "text_characters": len(spec.text),
            "block_count": len(spec.blocks),
            "file_count": len(spec.resources),
            "file_bytes": spec.file_bytes,
            "reference_tokens": "",
        }
        for order, spec in enumerate(pages, start=1)
    ]
    _csv(audit_root / "reports/onenote/onenote-summary.csv", _PROFILE_COLUMNS, profiles)

    claimed = {row["onenote_page_key"] for row in exact_rows}
    _csv(
        audit_root / "reports/reconciliation/unmatched-onenote.csv",
        _PROFILE_COLUMNS,
        [row for row in profiles if row["page_key"] not in claimed],
    )
    _csv(
        audit_root / "reports/onenote/page-links.csv",
        ("page_key", "onenote_hyperlink"),
        [
            {
                "page_key": spec.page_key,
                "onenote_hyperlink": "onenote:///archive#page-id={" + spec.page_key + "}",
            }
            for spec in pages
        ],
    )

    summary = {
        "excel": {"sha256": excel_sha256, "matters": 9, "onenoteHyperlinks": 4},
        "onenote": {
            "manifestSha256": manifest_digest,
            "pages": len(pages),
            "resources": sum(len(spec.resources) for spec in pages),
            "bytes": sum(spec.file_bytes for spec in pages),
            "sections": 1,
        },
        "reconciliation": {
            "EXACT": len(exact_rows),
            "STRONG": len(by_class["STRONG"]),
            "REVIEW_REQUIRED": len(by_class["REVIEW_REQUIRED"]),
            "CONFLICT": len(by_class["CONFLICT"]),
        },
    }
    path = audit_root / "assistant-pack/source-summary.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return audit_root


def build_corpus(base: Path, *, excel_bytes: bytes = b"synthetic register") -> dict:
    """Archive, audit and a stand-in register file, ready for `build_plan`."""
    archive_root = build_archive(base / "onenote-desktop-archive")
    excel_path = base / "register.xlsx"
    excel_path.write_bytes(excel_bytes)
    audit_root = build_audit(
        base / "migration-audit",
        archive_root,
        excel_sha256=hashlib.sha256(excel_bytes).hexdigest(),
    )
    return {
        "archive_root": archive_root,
        "audit_root": audit_root,
        "excel_path": excel_path,
        "excel_sha256": hashlib.sha256(excel_bytes).hexdigest(),
    }
