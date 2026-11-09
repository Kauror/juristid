"""Seal a desktop OneNote capture run into one immutable, hash-pinned package.

Runs on the capture workstation, after ``capture_pages.ps1`` and
``verify.py raw``. Reads only the run directory and the capture-request file
(``manage.py onenote_capture_import worklist --out``); never OneNote, never the
network. Writes a new directory, refusing to overwrite one:

    manifest.json   schema juristid.onenote-desktop-capture/2: pages, occurrences
                    (pre-navigation block times, structural context), counts
    pages/<tag>/    pre.xml, post.xml, receipt.json   (verbatim copies)
    blobs/<sha256>  one copy per distinct original (re-hashed after copying)
    SHA256SUMS      every file above
    SEAL.json       SHA-256 of SHA256SUMS and of manifest.json, with the counts

The importer (``app/legacy_import/onenote_capture.load_capture``) reads the
package only through this seal. Refused, and nothing sealed, when any page:

* was not bound to the page asked for (page ID or title changed during the read);
* still has placeholders (``unresolved_after`` > 0) — "stable for two polls" is not
  completeness (Phase 1 §9c);
* holds a file that was not copied, or whose copy does not hash like its source,
  or whose source changed during the copy.

Dates come from the **pre-navigation** read: opening a page touches text blocks'
``lastModifiedTime`` (Phase 1 §9c), never their ``creationTime``. A floating file
(on the page canvas, outside any outline) has no creation time and stays undated
(D8); its own modification time is kept as ``object_modified``.

Each page's Teemad come from the request's ``register_map`` — the register's
exact page GUIDs. A page the register does not name is sealed with no Teemad: it
is inventoried and never written (the section-by-section process).

Stdout carries counts only.

    python -m tools.onenote_capture.seal RUN_DIR REQUEST.json OUT_DIR CAPTURE_ID
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

from tools.onenote_capture.context import ATTRIBUTED, blocks_from_desktop_xml, context_for

SCHEMA = "juristid.onenote-desktop-capture/2"
NS = "{http://schemas.microsoft.com/office/onenote/2013/onenote}"
_EXT = re.compile(r"\.[a-z0-9]{1,6}")


class SealError(Exception):
    """The run cannot be sealed as it is; nothing was written."""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def stored_name(ordinal: int, preferred_name: str) -> str:
    """The name ``capture_pages.ps1`` gives a copied file: ordinal and a safe extension."""
    ext = Path(preferred_name or "").suffix.lower()
    return f"{ordinal:03d}{ext if _EXT.fullmatch(ext) else '.bin'}"


def _receipts(meta: dict[str, Any]) -> list[dict[str, Any]]:
    rec = meta.get("receipts") or []
    return rec if isinstance(rec, list) else [rec]


def hierarchy_index(path: Path) -> dict[str, dict[str, Any]]:
    """Page ID -> notebook, section groups, section, parent page, times, recycle bin."""
    root = ET.parse(path).getroot()  # noqa: S314
    out: dict[str, dict[str, Any]] = {}

    def walk(node: ET.Element, groups: list[str], notebook: str) -> None:
        for child in node:
            tag = child.tag.replace(NS, "")
            if tag == "Notebook":
                walk(child, [], child.get("name") or "")
            elif tag == "SectionGroup":
                walk(child, [*groups, child.get("name") or ""], notebook)
            elif tag == "Section":
                last: dict[int, str] = {}
                in_bin = child.get("isInRecycleBin") == "true" or "OneNote_RecycleBin" in groups
                for page in child.findall(f"{NS}Page"):
                    level = int(page.get("pageLevel") or 1)
                    last[level] = page.get("ID") or ""
                    out[page.get("ID") or ""] = {
                        "notebook": notebook,
                        "groups": groups,
                        "section": child.get("name") or "",
                        "parent_object_id": last.get(level - 1) if level > 1 else None,
                        "level": level,
                        "title": page.get("name") or "",
                        "created": page.get("dateTime"),
                        "modified": page.get("lastModifiedTime"),
                        "in_recycle_bin": in_bin,
                    }

    walk(root, [], "")
    return out


def _pages_tsv(run: Path) -> list[dict[str, str]]:
    rows = []
    for line in (run / "pages.tsv").read_text(encoding="utf-8-sig").splitlines():
        if not line.strip() or line.startswith("tag\t"):
            continue
        tag, page_id, guid, *rest = [*line.split("\t"), "", ""]
        rows.append(
            {"tag": tag, "page_id": page_id, "guid": guid.strip("{}").upper(), "link": rest[0]}
        )
    return rows


def seal(run: Path, request_path: Path, out: Path, capture_id: str) -> dict[str, Any]:
    run, out = Path(run), Path(out)
    if out.exists():
        raise SealError(f"refusing: {out} exists (a seal is immutable)")
    request = json.loads(Path(request_path).read_text(encoding="utf-8"))
    register = {g.upper(): teemad for g, teemad in (request.get("register_map") or {}).items()}
    hierarchy = hierarchy_index(run / "hierarchy-before.xml")
    pages_out: list[dict[str, Any]] = []
    occurrences: list[dict[str, Any]] = []
    blob_source: dict[str, Path] = {}
    staged: list[tuple[str, Path]] = []
    for row in _pages_tsv(run):
        tag, page_id, guid = row["tag"], row["page_id"], row["guid"]
        base = run / tag
        meta = json.loads((base / "receipt.json").read_text(encoding="utf-8-sig"))
        if not (meta.get("id_bound") and meta.get("title_pre_equals_post")):
            raise SealError(f"refusing: page {tag} binding not proven")
        if int(meta.get("unresolved_after") or 0):
            raise SealError(f"refusing: page {tag} has unresolved placeholders")
        info = hierarchy.get(page_id)
        if info is None:
            raise SealError(f"refusing: page {tag} is not in the hierarchy read")
        pre = (base / "pre.xml").read_text(encoding="utf-8-sig")
        post = (base / "post.xml").read_text(encoding="utf-8-sig")
        pre_times = {
            oe.get("objectID"): (oe.get("creationTime"), oe.get("lastModifiedTime"))
            for oe in ET.fromstring(pre).iter(f"{NS}OE")  # noqa: S314
        }
        blocks = blocks_from_desktop_xml(post)
        page_blocks = [
            {
                "ordinal": b.ordinal,
                "kind": b.kind.name,
                "outline": b.outline,
                "depth": b.depth,
                "parent": b.parent,
                "object_id": b.object_id,
                # The pre-navigation times: opening the page touches modification times.
                "created": pre_times.get(b.object_id, (b.created, b.modified))[0] or b.created,
                "modified": pre_times.get(b.object_id, (b.created, b.modified))[1] or b.modified,
                "text": b.text,
            }
            for b in blocks
        ]
        for name in ("pre.xml", "post.xml", "receipt.json"):
            staged.append((f"pages/{tag}/{name}", base / name))
        teemad = register.get(guid, [])
        pages_out.append(
            {
                "tag": tag,
                "page_object_id": page_id,
                "page_guid": guid,
                "title": info["title"],
                "section": info["section"],
                "archived": bool(info["in_recycle_bin"]),
                "level": info["level"],
                "notebook": info["notebook"],
                "section_groups": info["groups"],
                "parent_object_id": info["parent_object_id"],
                "in_recycle_bin": info["in_recycle_bin"],
                "onenote_hyperlink": row["link"],
                "page_created": info["created"],
                "page_modified": info["modified"],
                "navigate_start": meta.get("navigate_start"),
                "capture_end": meta.get("capture_end"),
                "matters": [
                    {
                        "matter_id": t["matter_id"],
                        "lovable_source_id": t["lovable_source_id"],
                        "reference": t["reference"],
                    }
                    for t in teemad
                ],
                "blocks": page_blocks,
            }
        )
        for r in _receipts(meta):
            if r.get("status") != "CAPTURED" or not (
                r.get("copy_matches_source") and r.get("source_stable")
            ):
                raise SealError(f"refusing: {tag}/{r.get('ordinal')} is not a verified copy")
            src = base / "files" / stored_name(int(r["ordinal"]), r.get("preferred_name") or "")
            digest = sha256_file(src)
            if digest != str(r["sha256"]).lower() or src.stat().st_size != int(r["bytes"]):
                raise SealError(f"refusing: {tag}/{r['ordinal']} bytes differ from the receipt")
            blob_source.setdefault(digest, src)
            floating = bool(r.get("floating"))
            block = next((b for b in blocks if b.object_id == r["object_id"]), None)
            context = (
                context_for(blocks, block.ordinal, capture_id=f"{capture_id}/{tag}")
                if block is not None and block.kind.name == "FILE"
                else []
            )
            if floating:
                created, modified, object_modified = None, None, r.get("oe_modified") or None
            else:
                created, modified = pre_times.get(
                    r["object_id"], (r.get("oe_created"), r.get("oe_modified"))
                )
                created, modified, object_modified = created or None, modified or None, None
            occurrences.append(
                {
                    "occurrence_key": f"{guid}|{r['object_id']}",
                    "page_tag": tag,
                    "page_guid": guid,
                    "ordinal": int(r["ordinal"]),
                    "block_object_id": r["object_id"],
                    "block_ordinal": block.ordinal if block is not None else None,
                    "floating": floating,
                    "filename": r.get("preferred_name") or "",
                    "size": int(r["bytes"]),
                    "sha256": digest,
                    "copied_at": r.get("copied_at"),
                    "block_created": created,
                    "block_modified": modified,
                    "object_modified": object_modified,
                    "context": [
                        {
                            "basis": c.basis.value,
                            "attributed": c.basis in ATTRIBUTED,
                            "block_ordinals": list(c.block_ordinals),
                            "text": list(c.text),
                        }
                        for c in context
                    ],
                }
            )
    if not pages_out:
        raise SealError("refusing: the run holds no page")
    counts = {
        "pages": len(pages_out),
        "occurrences": len(occurrences),
        "distinct_blobs": len(blob_source),
        "dated": sum(1 for o in occurrences if o["block_created"]),
        "floating": sum(1 for o in occurrences if o["floating"]),
        "structural_comment_files": sum(
            1 for o in occurrences if any(c["attributed"] and c["text"] for c in o["context"])
        ),
        "topics": sum(len(p["matters"]) for p in pages_out),
        "shared_pages": sum(1 for p in pages_out if len(p["matters"]) > 1),
    }
    manifest = {
        "schema": SCHEMA,
        "capture_id": capture_id,
        "package": out.name,
        "reader": "ONENOTE_DESKTOP",
        "request_generated_at": request.get("generated_at"),
        "counts": counts,
        "pages": pages_out,
        "occurrences": occurrences,
    }
    # Write only once every check above passed.
    (out / "blobs").mkdir(parents=True)
    for relative, src in staged:
        target = out / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, target)
    for digest, src in blob_source.items():
        shutil.copyfile(src, out / "blobs" / digest)
        if sha256_file(out / "blobs" / digest) != digest:
            raise SealError("refusing: a blob copy does not hash to its name")
    (out / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=1, sort_keys=True), encoding="utf-8"
    )
    write_sums(out)
    seal_doc = {
        "capture_id": capture_id,
        "package": out.name,
        "sha256sums_sha256": sha256_file(out / "SHA256SUMS"),
        "manifest_sha256": sha256_file(out / "manifest.json"),
        "counts": counts,
    }
    (out / "SEAL.json").write_text(json.dumps(seal_doc, indent=1), encoding="utf-8")
    return seal_doc


def write_sums(out: Path) -> None:
    lines = [
        f"{sha256_file(path)}  {path.relative_to(out).as_posix()}"
        for path in sorted(out.rglob("*"))
        if path.is_file() and path.name not in {"SHA256SUMS", "SEAL.json"}
    ]
    (out / "SHA256SUMS").write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")


def main(argv: list[str]) -> int:
    if len(argv) != 4:
        print(__doc__.strip().splitlines()[-1].strip())
        return 64
    try:
        result = seal(Path(argv[0]), Path(argv[1]), Path(argv[2]), argv[3])
    except SealError as error:
        print(str(error))
        return 2
    print(json.dumps(result["counts"], sort_keys=True), "manifest", result["manifest_sha256"][:16])
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
