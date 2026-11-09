"""Check a desktop capture before it is sealed (``raw``) and before it leaves the
workstation (``sealed``). Exit 0 = continue, 2 = STOP (a person decides).

    python -m tools.onenote_capture.verify raw    RUN_DIR
    python -m tools.onenote_capture.verify sealed SEALED_DIR

``raw`` STOPs on any of (from the 2026-11-04 batch verifier):

* an existing block lost, added or reordered between the pre- and
  post-navigation reads, or its creation time changed;
* a block's text changed, or its kind changed (other than a list item that
  turned out to hold a file);
* a block's modification time changed, unless it is a text block whose content is
  identical and whose new time falls inside the navigation window (the known
  "navigation touch"; dates are always taken from the pre-navigation read);
* the page ID or title binding broken;
* the hierarchy's ``lastModifiedTime`` changed on a page of this run, or on a
  section holding one (possible pending write: ask the owner);
* a placeholder left on a page (0 placeholders is the completeness rule);
* a copied file whose hash differs from its source, or a source that changed
  during the copy; a file not fetched.

``sealed`` re-hashes every file against ``SHA256SUMS``, the seal against both,
each blob against its name, and the manifest's counts against its own rows — the
checks ``load_capture`` repeats on the host.

Stdout carries counts and reason codes only.
"""

from __future__ import annotations

import json
import sys
import xml.etree.ElementTree as ET
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from tools.onenote_capture.seal import NS, _pages_tsv, _receipts, sha256_file


def _blocks(xml: str) -> tuple[dict[str, tuple[Any, ...]], list[str]]:
    root = ET.fromstring(xml)  # noqa: S314
    out: dict[str, tuple[Any, ...]] = {}
    order: list[str] = []
    for oe in root.iter(f"{NS}OE"):
        own = tuple(c.tag.replace(NS, "") for c in oe if c.tag != f"{NS}OEChildren")
        text = oe.find(f"{NS}T")
        content = ET.tostring(text, encoding="unicode") if text is not None else ""
        key = oe.get("objectID") or ""
        out[key] = (own, oe.get("creationTime"), oe.get("lastModifiedTime"), content)
        order.append(key)
    return out, order


def _moment(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _near(stamp: str, start: str, end: str) -> bool:
    try:
        t, a, b = _moment(stamp), _moment(start), _moment(end)
    except (TypeError, ValueError):
        return False
    return a - timedelta(seconds=2) <= t <= b + timedelta(seconds=2)


def _hierarchy(path: Path) -> tuple[dict[str, str | None], dict[str, tuple[str | None, set[str]]]]:
    root = ET.parse(path).getroot()  # noqa: S314
    pages = {p.get("ID") or "": p.get("lastModifiedTime") for p in root.iter(f"{NS}Page")}
    sections = {
        s.get("ID") or "": (
            s.get("lastModifiedTime"),
            {p.get("ID") or "" for p in s.iter(f"{NS}Page")},
        )
        for s in root.iter(f"{NS}Section")
    }
    return pages, sections


def check_raw(run: Path) -> dict[str, Any]:
    run = Path(run)
    stop: list[str] = []
    notes: Counter[str] = Counter()
    summary: Counter[str] = Counter()
    rows = _pages_tsv(run)
    ids = {r["page_id"] for r in rows}
    before, sections_before = _hierarchy(run / "hierarchy-before.xml")
    after, sections_after = _hierarchy(run / "hierarchy-after.xml")
    for page_id, modified in before.items():
        if modified != after.get(page_id):
            if page_id in ids:
                stop.append("HIERARCHY_LM_CHANGED_ON_RUN_PAGE")
            else:
                notes["other_page_lm_changed_by_sync"] += 1
    for section_id, (modified, members) in sections_before.items():
        if modified != sections_after.get(section_id, (None, set()))[0]:
            if members & ids:
                stop.append("SECTION_LM_CHANGED_ON_RUN_SECTION")
            else:
                notes["other_section_lm_changed"] += 1
    for row in rows:
        tag = row["tag"]
        base = run / tag
        try:
            meta = json.loads((base / "receipt.json").read_text(encoding="utf-8-sig"))
            pre = (base / "pre.xml").read_text(encoding="utf-8-sig")
            post = (base / "post.xml").read_text(encoding="utf-8-sig")
        except FileNotFoundError:
            stop.append(f"{tag}:MISSING_OUTPUT")
            continue
        a, order_a = _blocks(pre)
        b, order_b = _blocks(post)
        if [k for k in order_a if k in b] != [k for k in order_b if k in a]:
            stop.append(f"{tag}:BLOCK_ORDER_CHANGED")
        lost = set(a) - set(b)
        if lost:
            stop.append(f"{tag}:BLOCKS_LOST")
        # A placeholder block (an empty OE, or a list item) may turn into a file;
        # any other added block is a change during the read.
        added = [k for k in set(b) - set(a) if "InsertedFile" not in b[k][0]]
        if added:
            stop.append(f"{tag}:BLOCKS_ADDED")
        for key in a.keys() & b.keys():
            if a[key][1] != b[key][1]:
                stop.append(f"{tag}:BLOCK_CREATION_TIME_CHANGED")
            elif a[key][2] != b[key][2]:
                same = a[key][0] == b[key][0] and a[key][3] == b[key][3]
                if (
                    same
                    and "InsertedFile" not in b[key][0]
                    and _near(
                        b[key][2] or "",
                        meta.get("navigate_start") or "",
                        meta.get("capture_end") or "",
                    )
                ):
                    notes["navigation_touch"] += 1
                elif not (a[key][0] in {(), ("List",)} and "InsertedFile" in b[key][0]):
                    stop.append(f"{tag}:BLOCK_MODIFIED_TIME_CHANGED")
            if a[key][3] != b[key][3]:
                stop.append(f"{tag}:TEXT_CHANGED")
            kind_a, kind_b = a[key][0], b[key][0]
            if (
                kind_a
                and kind_a != kind_b
                and not (kind_a == ("List",) and kind_b == ("List", "InsertedFile"))
            ):
                stop.append(f"{tag}:BLOCK_KIND_CHANGED")
        if not meta.get("id_bound") or not meta.get("title_pre_equals_post"):
            stop.append(f"{tag}:BINDING")
        if int(meta.get("unresolved_after") or 0):
            stop.append(f"{tag}:PLACEHOLDERS_LEFT")
        for r in _receipts(meta):
            summary["files"] += 1
            summary["status:" + str(r.get("status"))] += 1
            if r.get("status") != "CAPTURED":
                stop.append(f"{tag}/{r.get('ordinal')}:{r.get('status')}")
            elif not (r.get("copy_matches_source") and r.get("source_stable")):
                stop.append(f"{tag}/{r.get('ordinal')}:HASH")
            else:
                summary["bytes"] += int(r.get("bytes") or 0)
        summary["placeholders_left"] += int(meta.get("unresolved_after") or 0)
        summary["navigated"] += int(bool(meta.get("navigated")))
        summary["pages"] += 1
    return {"stop": stop, "notes": dict(notes), "summary": dict(summary)}


def check_sealed(root: Path) -> dict[str, Any]:
    root = Path(root)
    stop: list[str] = []
    seal = json.loads((root / "SEAL.json").read_text(encoding="utf-8"))
    if sha256_file(root / "SHA256SUMS") != seal.get("sha256sums_sha256"):
        stop.append("SHA256SUMS_NOT_SEALED")
    if sha256_file(root / "manifest.json") != seal.get("manifest_sha256"):
        stop.append("MANIFEST_NOT_SEALED")
    listed: dict[str, str] = {}
    for line in (root / "SHA256SUMS").read_text(encoding="utf-8").splitlines():
        if line.strip():
            digest, _, name = line.partition("  ")
            listed[name] = digest
    on_disk = {
        p.relative_to(root).as_posix()
        for p in root.rglob("*")
        if p.is_file() and p.name not in {"SHA256SUMS", "SEAL.json"}
    }
    if on_disk != set(listed):
        stop.append("TREE_DIFFERS_FROM_SHA256SUMS")
    for name, digest in listed.items():
        path = root / name
        if path.is_file() and sha256_file(path) != digest:
            stop.append("FILE_DIFFERS_FROM_SHA256SUMS")
        if name.startswith("blobs/") and name.split("/", 1)[1] != digest:
            stop.append("BLOB_NOT_NAMED_BY_ITS_HASH")
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    occ = manifest.get("occurrences", [])
    actual = {
        "pages": len(manifest.get("pages", [])),
        "occurrences": len(occ),
        "distinct_blobs": len({o["sha256"] for o in occ}),
        "dated": sum(1 for o in occ if o.get("block_created")),
        "floating": sum(1 for o in occ if o.get("floating")),
    }
    if any(manifest.get("counts", {}).get(k) != v for k, v in actual.items()):
        stop.append("COUNTS_DO_NOT_RECONCILE")
    return {"stop": sorted(set(stop)), "summary": actual}


def main(argv: list[str]) -> int:
    if len(argv) != 2 or argv[0] not in {"raw", "sealed"}:
        print("usage: python -m tools.onenote_capture.verify raw|sealed DIR")
        return 64
    result = check_raw(Path(argv[1])) if argv[0] == "raw" else check_sealed(Path(argv[1]))
    print(json.dumps(result["summary"], sort_keys=True), "STOP" if result["stop"] else "OK")
    for code in sorted(set(result["stop"]))[:20]:
        print(" ", code)
    return 2 if result["stop"] else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
