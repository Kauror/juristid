"""The synthetic OneNote world UI-12 and UI-13 are read against (no test of its own).

The reference case is a shared dossier linked to several Teemad: many files held
with «Ootab teemaga seostamist», long notes with source headings and nested
lists, prose holding `"` characters, a dated chronology, and a floating file.
It is reproduced here from invented material only, in the desktop capture's own
block shape (`ordinal`, `kind`, `text`, `object_id`, `created`, `modified`,
`outline`, `depth`, `parent`), and imported through the real capture import
(`app.legacy_import.onenote_capture`), so the stored blocks are exactly what an
import writes.

**The prose is stored as the desktop reader stores it.** OneNote keeps a
block's text (`one:T`) as an HTML fragment; the reader strips its tags and
keeps its character references, so a `"` typed in OneNote arrives as `&quot;`
and a literal «&quot;» typed by a person arrives as `&amp;quot;`
(`DOUBLE_QUOTED`, `LITERAL_ENTITY`).

Every Teema, page, file and sentence is invented. `build_world` is shared by
`tests/test_ui12_source_page.py`, `tests/test_ui13_timeline_rows.py` and the
local screenshot seed; it is in a `test_` module so that the suite collects
nothing from it and other modules may import it (it defines no test).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.legacy_import import onenote_capture as oc
from app.matters.models import Matter
from tests.test_onenote_capture_import import (
    _file,
    _guid,
    build_capture,
    register_teema,
    reseal,
)

#: The shared page's Teemad, earliest first; the first continues into the second.
REFERENCES = ("2024_101", "2025_140", "2025_196")
DOSSIER_TITLE = "Kliimakindla majanduse seaduse eelnõu"
SINGLE_TITLE = "Näidisseaduse muutmine 2026"

#: As the reader stores them: OneNote's HTML fragment, tags stripped, references kept.
DOUBLE_QUOTED = (
    "Ministeerium nimetab eelnõu &quot;kliimaseaduseks&quot;, koda on kasutanud "
    "lühendit &quot;KKMS&quot;. Eelnõude infosüsteemis: "
    "https://eelnoud.example.invalid/kliima/2024-0142 (vt ka tabel &amp; lisad)."
)
#: …and as a reader must see it: each reference decoded exactly once.
DOUBLE_QUOTED_SHOWN = (
    'Ministeerium nimetab eelnõu "kliimaseaduseks", koda on kasutanud '
    'lühendit "KKMS". Eelnõude infosüsteemis: '
    "https://eelnoud.example.invalid/kliima/2024-0142 (vt ka tabel & lisad)."
)
#: A person who typed «&quot;» into OneNote: stored `&amp;quot;`, shown «&quot;».
LITERAL_ENTITY = "Märkus: vana süsteem kuvas sõna &amp;quot; otse tekstis."
LITERAL_ENTITY_SHOWN = "Märkus: vana süsteem kuvas sõna &quot; otse tekstis."

REPEATED = "Koda koostas arvamuse koos liikmetega."
LONG_PARAGRAPH = (
    "Majanduskomisjon arutas koja ettepanekuid kahel istungil. Komisjon nõustus "
    "üleminekuaja pikendamisega, kuid jättis alles aruandluskohustuse väiksematele "
    "ettevõtjatele, mille koda oli palunud eelnõust välja jätta. Koja esindaja "
    "selgitas, et kohustus dubleerib olemasolevat keskkonnaaruandlust ning et "
    "mõjuanalüüsis puudub hinnang halduskoormusele."
)

T_2024_03 = "2024-03-12T09:15:00.000Z"
T_2024_05 = "2024-05-20T11:00:00.000Z"
T_2024_09 = "2024-09-18T08:30:00.000Z"
T_2026_07 = "2026-07-27T07:00:00.000Z"
T_2026_08 = "2026-08-10T10:00:00.000Z"


def _text(ordinal: int, text: str, created: str, *, parent: int | None = None, depth: int = 0):
    return {
        "ordinal": ordinal,
        "kind": "TEXT" if text else "BLANK",
        "text": text,
        "object_id": f"t{ordinal}",
        "created": created,
        "modified": created,
        "outline": 0,
        "depth": depth,
        "parent": parent,
    }


def _file_block(guid: str, n: int, created: str | None, *, parent: int | None, floating=False):
    return {
        "ordinal": n,
        "kind": "FILE",
        "text": "",
        "object_id": f"{{{guid}}}{{{n}}}{{B0}}",
        "created": created or "",
        "modified": created or "",
        "outline": -1 if floating else 0,
        "depth": 1 if parent is not None else 0,
        "parent": parent,
    }


#: (block ordinal, filename, OneNote addition time, parent block) of the dossier's files.
DOSSIER_FILES = (
    (2, "Kliimaseaduse_VTK.pdf", T_2024_03, 0),
    (6, "Eelnou_kooskolastus.pdf", T_2024_03, 4),
    (7, "Seletuskiri.docx", T_2024_03, 4),
    (10, "Koja_arvamus_2024-04-02.pdf", T_2024_03, 9),
    (11, "Liikmete_kommentaarid.xlsx", T_2024_03, 9),
    (12, "Tööstusliidu_arvamus.pdf", T_2024_03, 9),
    (17, "Majanduskomisjoni_protokoll.pdf", T_2024_05, 14),
    (25, "Kolmanda_lugemise_tekst.pdf", T_2024_09, 23),
    (26, "Muudatusettepanekute_tabel.xlsx", T_2024_09, 23),
    (27, "Pressiteade.docx", T_2024_09, None),
    (28, "Lõuendil_kokkuvõte.pdf", None, None),
)


def dossier_blocks(guid: str) -> list[dict[str, Any]]:
    """The shared page, in reading order: headings, dated notes, lists and files."""
    t = T_2024_03
    blocks = [
        _text(0, "Taust", t),
        _text(1, DOUBLE_QUOTED, t, parent=0, depth=1),
        _file_block(guid, 2, t, parent=0),
        _text(3, "", t),
        _text(
            4,
            "12.03.2024 - Ministeerium saatis eelnõu kooskõlastusringile. Koda palus "
            "tähtaega pikendada, sest &quot;mõjuanalüüs&quot; puudus.",
            t,
        ),
        _text(5, REPEATED, t),
        _file_block(guid, 6, t, parent=4),
        _file_block(guid, 7, t, parent=4),
        _text(8, "", t),
        _text(9, "Arvamused:", t),
        _file_block(guid, 10, t, parent=9),
        _file_block(guid, 11, t, parent=9),
        _file_block(guid, 12, t, parent=9),
        _text(13, "", t),
        _text(14, "05.2024 - Teine lugemine Riigikogus.", T_2024_05),
        _text(15, REPEATED, T_2024_05),
        _text(16, LONG_PARAGRAPH, T_2024_05),
        _file_block(guid, 17, T_2024_05, parent=14),
        _text(18, "", T_2024_05),
        _text(19, "Järgmised tegevused:", T_2024_05),
        _text(20, "küsida ministeeriumilt uut versiooni", T_2024_05, parent=19, depth=1),
        _text(21, "leppida kokku kohtumine majanduskomisjoniga", T_2024_05, parent=19, depth=1),
        _text(22, "", T_2024_05),
        _text(
            23,
            "18.09.2024 - Eelnõu läks kolmandale lugemisele; koja &quot;punased "
            "jooned&quot; jäid osaliselt arvestamata.",
            T_2024_09,
        ),
        _text(24, LITERAL_ENTITY, T_2024_09),
        _file_block(guid, 25, T_2024_09, parent=23),
        _file_block(guid, 26, T_2024_09, parent=23),
        _file_block(guid, 27, T_2024_09, parent=None),
        _file_block(guid, 28, None, parent=None, floating=True),
    ]
    return blocks


#: (block ordinal, filename, OneNote addition time, parent block) of the single page's files.
SINGLE_FILES = (
    (2, "EELNÕU.pdf", T_2026_07, 0),
    (3, "Seletuskiri_2026.docx", T_2026_07, 0),
    (9, "Kokkuvõte.pdf", T_2026_08, None),
)

NOTE_FIRST = (
    "Näidisamet saatis kooskõlastusringile eelnõu, millega muudetakse näidisseadust "
    "ja sellega seotud kolme määrust."
)
NOTE_SECOND = "Tähtaeg on kolm nädalat; koja &quot;märkuste tabel&quot; on lisatud."
NOTE_SECOND_SHOWN = 'Tähtaeg on kolm nädalat; koja "märkuste tabel" on lisatud.'
SHORT_NOTE = "Koda ei esitanud märkusi."
SUMMARY = "Kokkuvõte: koda leidis, et eelnõu on toetatav."


def single_blocks(guid: str) -> list[dict[str, Any]]:
    """One Teema's page: a long dated note with direct files, a short one, a repeat."""
    return [
        _text(0, f"27.07.2026 - {NOTE_FIRST}", T_2026_07),
        _text(1, NOTE_SECOND, T_2026_07),
        _file_block(guid, 2, T_2026_07, parent=0),
        _file_block(guid, 3, T_2026_07, parent=0),
        _text(4, "", T_2026_07),
        _text(5, f"10.08.2026 - {SHORT_NOTE}", T_2026_08),
        _text(6, "", T_2026_08),
        _text(7, f"08.2026 - {SUMMARY}", T_2026_08),
        # The same sentence again, deliberately: it is the source's, and stays.
        _text(8, SUMMARY, T_2026_08),
        _file_block(guid, 9, T_2026_08, parent=None),
    ]


def _replace_blocks(root: Path, blocks_by_tag: dict[str, list[dict[str, Any]]]) -> None:
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    for page in manifest["pages"]:
        if page["tag"] in blocks_by_tag:
            page["blocks"] = blocks_by_tag[page["tag"]]
    for occurrence in manifest["occurrences"]:
        # The block an occurrence sits in keeps its floating flag and times.
        blocks = blocks_by_tag.get(occurrence["page_tag"], [])
        block = next((b for b in blocks if b["object_id"] == occurrence["block_object_id"]), None)
        if block is not None and not occurrence["floating"]:
            occurrence["block_created"] = block["created"] or None
            occurrence["block_modified"] = block["modified"] or None
    (root / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    reseal(root)


def build_world(corpus: Path, references: tuple[str, ...] = REFERENCES) -> dict[str, Any]:
    """Three Teemad sharing the dossier, the second also holding a page of its own.

    ``corpus`` becomes `HISTORICAL_SOURCE_ROOT`; the caller enables the import
    (`ONENOTE_CAPTURE_IMPORT_ENABLED`, `REAL_DATA_ALLOWED`) beforehand.
    """
    first, second, third = (register_teema(reference) for reference in references)
    for matter, title in zip(
        (first, second, third),
        (
            "Kliimaseaduse väljatöötamiskavatsus",
            "Kliimakindla majanduse seaduse eelnõu",
            "Kliimaseaduse rakendusaktid",
        ),
        strict=True,
    ):
        year, number = matter.lovable_sync_state.reference.split("_")
        Matter.objects.filter(pk=matter.pk).update(
            title=title, reference_year=int(year), reference_number=int(number)
        )
    Matter.objects.filter(pk=first.pk).update(superseded_by=second)
    dossier_guid, single_guid = _guid(0x71), _guid(0x72)
    # A Teema's stored Lovable link names one page, so each page is its own
    # capture, imported in turn: the dossier first, then the second Teema's own.
    captures = [
        (
            "701",
            {
                "tag": "701",
                "guid": dossier_guid,
                "matters": [first, second, third],
                "title": DOSSIER_TITLE,
                "section": "Kliima",
                "files": [
                    _file(n, name, created, floating=created is None, modified=created or T_2024_09)
                    for n, name, created, _p in DOSSIER_FILES
                ],
            },
            dossier_blocks(dossier_guid),
        ),
        (
            "702",
            {
                "tag": "702",
                "guid": single_guid,
                "matters": [second],
                "title": SINGLE_TITLE,
                "section": "Eelnõud 2026",
                "files": [_file(n, name, created) for n, name, created, _p in SINGLE_FILES],
            },
            single_blocks(single_guid),
        ),
    ]
    roots = []
    for tag, page, blocks in captures:
        root = build_capture(corpus / f"onenote-desktop-capture-ui12-{tag}" / "pkg-v2", [page])
        _replace_blocks(root, {tag: blocks})
        plan = oc.build_plan(oc.load_capture(root))
        oc.apply(
            plan,
            oc.select_pages(plan, include_shared=len(page["matters"]) > 1),
            operator_intent=oc.OPERATOR_INTENT,
            expect_manifest_sha256=plan.capture.manifest_sha256,
            expect_plan_sha256=plan.digest,
            backup_set="sets/20261106T000000Z",
            label=f"ui12-synthetic-{tag}",
        )
        roots.append(root)
    root = roots[0]
    return {
        "root": root,
        "teemad": [Matter.objects.get(pk=m.pk) for m in (first, second, third)],
        "dossier_guid": dossier_guid,
        "single_guid": single_guid,
    }
