"""DOCX, XLSX and PPTX: read as data, never opened as programs.

All three are ZIP containers full of XML, and all three are read here with
libraries that parse that XML directly. Nothing invokes Word, Excel,
PowerPoint or LibreOffice; nothing evaluates a formula; nothing resolves an
embedded object or a linked image. A document that wants to run something on
open gets exactly the same treatment as one that does not, which is the only
version of this that is defensible (Stage-2B brief 15).

Locators differ per format because the formats genuinely know different things:

* PPTX knows its slide numbers, so a match reports `slaid 12`.
* XLSX knows its sheet names and cell references, so a match reports the sheet
  and the range.
* DOCX knows **neither**. Word paginates at render time against a specific
  printer and font set; the file does not contain page boundaries. So a DOCX
  match reports a section, a paragraph range or a table, and never a page. An
  invented `lk 4` sends a lawyer to the wrong place in a 90-page draft, and one
  such experience costs more trust than the locator ever earns
  (Stage-2B brief 16).
"""

from __future__ import annotations

import io
from typing import IO, Any

from app.documents.enums import LocatorKind
from app.documents.extraction.base import (
    DerivativePayload,
    Fragment,
    ParseResult,
    SourceFile,
    normalise,
    registry,
)
from app.documents.extraction.errors import ExtractionFailed
from app.documents.extraction.limits import (
    Limits,
    TableBudget,
    current_limits,
    guard_character_budget,
    guard_office_container,
)

#: How many paragraphs share one DOCX fragment. Small enough that a locator is
#: specific, large enough that a 400-paragraph draft is not 400 index rows.
DOCX_PARAGRAPHS_PER_FRAGMENT = 40


class DocxParser:
    name = "docx"
    version = "1"
    mime_types = frozenset(
        {"application/vnd.openxmlformats-officedocument.wordprocessingml.document"}
    )

    def parse(self, source: SourceFile) -> ParseResult:
        """Read a DOCX within budgets charged before python-docx builds anything big.

        python-docx parses every XML part it loads into a tree at open, and
        then builds one Python object per paragraph, row and cell it is asked
        about — so a package whose central directory was within the
        decompression limits could still ask for gigabytes of tree, and a
        table whose cells declare enormous spans for millions of cell objects
        (csf_eb1ed7815b093aba8872620b, docs/adr/0182). Now, in order:

        1. the package's directory is preflighted and its members bounded
           (`guard_office_container`, as before);
        2. every part python-docx would parse as XML is charged, by its
           uncompressed size, against a per-part and a whole-package budget
           *before* python-docx opens the package (:func:`guard_office_xml_parts`);
        3. body paragraphs are counted on the parsed tree before one paragraph
           object exists, and read in a stream;
        4. table rows and the grid cells each row expands to (spans and
           vertical merges included) are charged to the walk budget before the
           row's cells are listed, and a merged cell's text is read once;
        5. headers and footers are resolved without python-docx's recursive
           inheritance walk, which could exhaust the stack and also *wrote* a
           new header part into the document it was reading.

        Every refusal is an `ExtractionFailed` with its own code; nothing is
        truncated. The fragments, locators and metadata are exactly what the
        previous parser produced for every document within the limits.
        """
        import docx

        limits = current_limits()
        handle = io.BytesIO(source.content)
        guard_office_xml_parts(handle, guard_office_container(handle, limits), limits)

        try:
            document = docx.Document(handle)
        except Exception as error:
            raise ExtractionFailed(
                "unreadable_docx", "DOCX-i ei õnnestunud avada; fail võib olla rikutud."
            ) from error

        from docx.oxml.ns import qn
        from docx.text.paragraph import Paragraph

        fragments: list[Fragment] = []
        total = 0
        ordinal = 0

        body_element = document.element.body
        paragraph_count = sum(1 for _ in body_element.iterchildren(qn("w:p")))
        if paragraph_count > limits.max_docx_paragraphs:
            raise ExtractionFailed(
                "docx_paragraph_limit",
                f"DOCX-is on {paragraph_count} lõiku, lubatud on {limits.max_docx_paragraphs}. "
                "Originaal on alles.",
            )

        container = document._body
        first_index = 1
        buffer: list[str] = []
        buffered = 0
        for index, element in enumerate(body_element.iterchildren(qn("w:p")), start=1):
            text = normalise(Paragraph(element, container).text)
            if text:
                buffer.append(text)
                buffered += len(text)
                # The joined block is at least this long: refused before it is built.
                guard_character_budget(total + buffered, limits)
            if len(buffer) >= DOCX_PARAGRAPHS_PER_FRAGMENT or index == paragraph_count:
                if buffer:
                    body = "\n".join(buffer)
                    total += len(body)
                    guard_character_budget(total, limits)
                    ordinal += 1
                    fragments.append(
                        Fragment(
                            text=body,
                            locator_kind=LocatorKind.SECTION,
                            locator={"paragraph_from": first_index, "paragraph_to": index},
                            locator_label=f"lõigud {first_index}–{index}",
                        )
                    )
                    buffer = []
                    buffered = 0
                first_index = index + 1

        tables = document.tables
        budget = TableBudget(limits)
        for number, table in enumerate(tables, start=1):
            body = _table_text(table, budget, total, limits, number)
            if not body:
                continue
            total += len(body)
            guard_character_budget(total, limits)
            fragments.append(
                Fragment(
                    text=body,
                    locator_kind=LocatorKind.SECTION,
                    locator={"table": number},
                    locator_label=f"tabel {number}",
                )
            )

        # Headers and footers carry the file reference and the ministry's own
        # registration number more often than they carry anything else, and
        # those are exactly what somebody searches for.
        sections = document.element.sectPr_lst
        resolver = _HeaderFooterResolver(document)
        for number, sectPr in enumerate(sections, start=1):
            for label, text in (
                ("päis", resolver.text(sectPr, header=True)),
                ("jalus", resolver.text(sectPr, header=False)),
            ):
                body = text
                if not body:
                    continue
                total += len(body)
                guard_character_budget(total, limits)
                fragments.append(
                    Fragment(
                        text=body,
                        locator_kind=LocatorKind.SECTION,
                        locator={"section": number, "part": label},
                        locator_label=f"{label} (osa {number})",
                    )
                )

        if not fragments:
            raise ExtractionFailed("no_text", "DOCX-ist ei leitud teksti.")

        return ParseResult(
            derivatives=(
                DerivativePayload(
                    fragments=tuple(fragments),
                    metadata={
                        "paragraph_count": paragraph_count,
                        "table_count": len(tables),
                        "section_count": len(sections),
                        # Said explicitly so nobody later reads the absence of a
                        # page locator as a parser that forgot.
                        "pagination": "not-available-in-format",
                    },
                ),
            )
        )


#: Members every Office library parses as XML by their name alone: the package
#: index and every relationship part (python-docx, python-pptx and openpyxl all
#: read ``*.rels`` without consulting a content type), plus anything named XML.
_XML_NAME_SUFFIXES = (".xml", ".rels", ".vml")
_CONTENT_TYPES_MEMBER = "[Content_Types].xml"


def _is_xml_content_type(content_type: str) -> bool:
    lowered = content_type.lower().split(";")[0].strip()
    return lowered.endswith("xml")


def guard_office_xml_parts(handle: IO[bytes], members: list[Any], limits: Limits) -> None:
    """Charge every member an Office library may parse as XML, before any is parsed.

    python-docx, python-pptx and openpyxl build an XML tree (or, for openpyxl's
    shared strings and styles, a whole in-memory structure) from every such
    part they load, and a tree is several times its XML in memory
    (csf_eb1ed7815b093aba8872620b, docs/adr/0182). Each one is charged by its
    uncompressed size against ``max_office_xml_part_bytes``, and all of them
    together against ``max_office_xml_bytes``.

    **Which members.** Everything named ``*.xml``, ``*.rels`` or ``*.vml`` —
    relationship parts are parsed by name, whatever ``[Content_Types].xml``
    says, and ``/_rels/.rels`` has no extension for a content-type lookup to
    find. Every other member is charged too unless the package *declares* it a
    non-XML type (an image, a font, an embedded binary): a part renamed
    ``.png`` but declared XML is charged, and a member nobody declared is
    charged rather than guessed at.

    **Which sizes.** The central directory's uncompressed sizes, which the
    preflight has already bounded and which `zipfile` enforces while inflating
    (a member that inflates past its declared size fails its CRC rather than
    growing), so the budget bounds what a parser can be handed rather than
    trusting a claim. The one member read here, the content-type index, is
    read through a bounded read as well.
    """
    import zipfile

    from docx.opc.packuri import PackURI
    from docx.opc.pkgreader import _ContentTypeMap

    sizes = {info.filename: info.file_size for info in members if not info.is_dir()}
    content_types = None
    declared = sizes.get(_CONTENT_TYPES_MEMBER)
    if declared is not None:
        _charge_xml_part(declared, limits)
        try:
            with zipfile.ZipFile(handle) as archive, archive.open(_CONTENT_TYPES_MEMBER) as part:
                index = part.read(limits.max_office_xml_part_bytes + 1)
            _charge_xml_part(len(index), limits)
            content_types = _ContentTypeMap.from_xml(index)
        except ExtractionFailed:
            raise
        except Exception:
            # An index the libraries cannot read either: every member is
            # charged, and the library reports the broken package itself.
            content_types = None
        finally:
            handle.seek(0)

    total = 0
    for name, size in sizes.items():
        if not name.lower().endswith(_XML_NAME_SUFFIXES) and content_types is not None:
            try:
                content_type = str(content_types[PackURI(f"/{name}")])
            except (KeyError, ValueError):
                content_type = ""
            if content_type and not _is_xml_content_type(content_type):
                continue
        _charge_xml_part(size, limits)
        total += size
        if total > limits.max_office_xml_bytes:
            raise ExtractionFailed(
                "xml_total_limit",
                f"Office-paketi XML-osad on kokku üle {limits.max_office_xml_bytes} baiti. "
                "Originaal on alles.",
            )


def _charge_xml_part(size: int, limits: Limits) -> None:
    if size > limits.max_office_xml_part_bytes:
        raise ExtractionFailed(
            "xml_part_limit",
            f"Office-paketi XML-osa on {size} baiti, lubatud on "
            f"{limits.max_office_xml_part_bytes}. Originaal on alles.",
        )


def _table_text(table: Any, budget: TableBudget, total: int, limits: Limits, number: int) -> str:
    """One table's text: each row's non-empty cells joined with " | ".

    The cells of a row are what python-docx's ``_Row.cells`` gives — one per
    layout-grid column, a horizontally spanned cell repeated across its span, a
    vertically merged continuation standing for the cell it continues — but
    worked out from the XML here, a row at a time and without recursion,
    and charged to ``budget`` *before* the row is expanded. ``_Row.cells``
    builds the whole expansion first and resolves each vertical merge by
    walking up the table again, so a row of cells each declaring a span of a
    million columns, or a column merged down ten thousand rows, cost all of
    that before any limit applied. A merged cell's text is read once.
    """
    from docx.table import _Cell

    where = f"DOCX-i tabel {number}"
    # Keyed by the element itself, which keeps its proxy alive: an id() of an
    # lxml proxy is reused once the proxy is freed.
    texts: dict[Any, str] = {}
    rows: list[str] = []
    characters = 0
    above: dict[int, Any] | None = None
    for row_number, tr in enumerate(table._tbl.tr_lst, start=1):
        offset = max(0, tr.grid_before)
        roots: dict[int, Any] = {}
        expanded: list[Any] = []
        width = 0
        for tc in tr.tc_lst:
            span = _span(tc)
            if tc.vMerge == "continue":
                # python-docx raises ValueError here (no row above, or no cell
                # starting at this column in it); so does this.
                if above is None or offset not in above:
                    raise ValueError("no w:tc above to continue")
                root = above[offset]
            else:
                root = tc
            roots.setdefault(offset, root)
            root_span = _span(root)
            width += root_span
            expanded.append((root, root_span))
            offset += span
        above = roots
        # The row's width in grid cells, charged before a single cell is listed.
        budget.visit(row_number, width, where)

        cells: list[str] = []
        line_length = 0
        for root, root_span in expanded:
            text = texts.get(root)
            if text is None:
                text = normalise(_Cell(root, table).text)
                texts[root] = text
            if not text:
                continue
            for _ in range(root_span):
                line_length += len(text) + (3 if cells else 0)
                guard_character_budget(total + characters + line_length, limits)
                cells.append(text)
        if cells:
            line = " | ".join(cells)
            characters += len(line) + (1 if rows else 0)
            rows.append(line)
    return "\n".join(rows)


def _span(tc: Any) -> int:
    """A cell's width in grid columns; a declared span below one is one, as Word reads it.

    python-docx would list such a cell zero times and leave the next cell at
    the same column; Word draws it one column wide, and so does this, rather
    than failing the whole document over one malformed attribute.
    """
    return max(1, int(tc.grid_span))


class _HeaderFooterResolver:
    """Which header and footer each section shows, as python-docx decides it.

    A section without its own default header (or footer) shows the one of the
    section before it — python-docx's ``preceding::w:sectPr[1]`` — and so on
    back; with none anywhere, an empty one. python-docx walks that chain by
    recursion, once per section, and on reaching the first section with none
    *adds* a header part to the document. This walks it iteratively over one
    document-order list of every ``w:sectPr``, remembers each answer, adds
    nothing, and reads each definition's text once.
    """

    def __init__(self, document: Any) -> None:
        from docx.oxml.ns import qn

        self._document = document
        self._order = list(document.element.iter(qn("w:sectPr")))
        self._position = {element: index for index, element in enumerate(self._order)}
        self._owner: dict[tuple[Any, bool], Any] = {}
        self._texts: dict[tuple[Any, bool], str] = {}

    def _preceding(self, sectPr: Any) -> Any:
        index = self._position.get(sectPr)
        if index is None:  # pragma: no cover - every sectPr is in the list
            return sectPr.preceding_sectPr
        ancestors = set(sectPr.iterancestors())
        for earlier in range(index - 1, -1, -1):
            candidate = self._order[earlier]
            if candidate not in ancestors:
                return candidate
        return None

    def _owner_of(self, sectPr: Any, header: bool) -> Any:
        from docx.enum.section import WD_HEADER_FOOTER

        chain: list[Any] = []
        current = sectPr
        owner = None
        while current is not None:
            known = (current, header)
            if known in self._owner:
                owner = self._owner[known]
                break
            chain.append(current)
            reference = (
                current.get_headerReference(WD_HEADER_FOOTER.PRIMARY)
                if header
                else current.get_footerReference(WD_HEADER_FOOTER.PRIMARY)
            )
            if reference is not None:
                owner = current
                break
            current = self._preceding(current)
        for element in chain:
            self._owner[(element, header)] = owner
        return owner

    def text(self, sectPr: Any, *, header: bool) -> str:
        from docx.enum.section import WD_HEADER_FOOTER
        from docx.section import _Footer, _Header

        owner = self._owner_of(sectPr, header)
        if owner is None:
            # python-docx would add an empty definition here; its text is "".
            return ""
        key = (owner, header)
        if key not in self._texts:
            kind = _Header if header else _Footer
            part = kind(owner, self._document.part, WD_HEADER_FOOTER.PRIMARY)
            self._texts[key] = normalise("\n".join(p.text for p in part.paragraphs))
        return self._texts[key]


class XlsxParser:
    name = "xlsx"
    version = "1"
    mime_types = frozenset({"application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"})

    def parse(self, source: SourceFile) -> ParseResult:
        import openpyxl

        limits = current_limits()
        handle = io.BytesIO(source.content)
        # Worksheets, shared strings, styles and relationships: every XML part
        # is budgeted before openpyxl loads any (docs/adr/0182).
        guard_office_xml_parts(handle, guard_office_container(handle, limits), limits)

        try:
            # data_only=True asks for the values Excel last cached, which is a
            # read of stored data. It is emphatically not evaluation: openpyxl
            # has no formula engine, and a cell whose cached value is absent
            # simply has none (Stage-2B brief 17).
            workbook = openpyxl.load_workbook(handle, data_only=True, read_only=True)
        except Exception as error:
            raise ExtractionFailed(
                "unreadable_xlsx", "XLSX-i ei õnnestunud avada; fail võib olla rikutud."
            ) from error

        fragments: list[Fragment] = []
        total = 0
        # One walk budget for the whole workbook: a thousand sheets of ten
        # thousand blank cells cost the same as one sheet of ten million.
        budget = TableBudget(limits)
        try:
            for sheet in workbook.worksheets:
                total = self._read_sheet(sheet, fragments, total, limits, budget)
        finally:
            workbook.close()

        if not fragments:
            raise ExtractionFailed("no_text", "Töövihikust ei leitud sisu.")

        return ParseResult(
            derivatives=(
                DerivativePayload(
                    fragments=tuple(fragments),
                    metadata={
                        "sheet_count": len(workbook.sheetnames),
                        "sheets": list(workbook.sheetnames),
                        "formula_values": "cached-only",
                    },
                ),
            )
        )

    def _read_sheet(
        self,
        sheet: object,
        fragments: list[Fragment],
        total: int,
        limits: Limits,
        budget: TableBudget,
    ) -> int:
        """One sheet, grouped into bounded row blocks.

        A worksheet whose used range is a million mostly-empty rows — somebody
        once pressed Ctrl+Down — would be a million fragments if blank rows
        produced any, so they do not: blank cells contribute nothing, and a
        block that produced no text produces no fragment.

        They are not free, though. openpyxl walks the *declared* used range and
        pads every row to its width, so the walk is charged to ``budget``
        whether a cell holds anything or not, and a declared range the budget
        could never afford is refused before the first row is read
        (csf_a74bfe6e, docs/adr/0176).
        """
        where = f'Tööleht "{sheet.title}"'  # type: ignore[attr-defined]
        # After a read-only load these are the worksheet's own dimension
        # record: a claim rather than a count, and absent on some generators'
        # files — which is why every row is charged again as it is read.
        budget.declared(sheet.max_row, sheet.max_column, where)  # type: ignore[attr-defined]

        rows_per_block = 50
        block: list[str] = []
        first_row = 1
        produced = 0
        row_number = 0

        for row_number, row in enumerate(sheet.iter_rows(values_only=True), start=1):  # type: ignore[attr-defined]
            budget.visit(row_number, len(row), where)
            line = " | ".join(
                budget.cell(text, where)
                for text in (_cell_text(value) for value in row if value is not None)
                if text
            )
            if line:
                block.append(f"{row_number}: {line}")
            if row_number - first_row + 1 >= rows_per_block:
                total, produced = self._flush(
                    sheet, block, first_row, row_number, fragments, total, limits, produced
                )
                block = []
                first_row = row_number + 1
            if produced >= limits.max_xlsx_fragments_per_sheet:
                raise ExtractionFailed(
                    "sheet_fragment_limit",
                    f'Tööleht "{sheet.title}" ületab lubatud osade arvu.',  # type: ignore[attr-defined]
                )

        if block:
            total, produced = self._flush(
                sheet, block, first_row, row_number, fragments, total, limits, produced
            )
        return total

    def _flush(
        self,
        sheet: object,
        block: list[str],
        first_row: int,
        last_row: int,
        fragments: list[Fragment],
        total: int,
        limits: Limits,
        produced: int,
    ) -> tuple[int, int]:
        if not block:
            return total, produced
        body = "\n".join(block)
        total += len(body)
        guard_character_budget(total, limits)
        name = sheet.title  # type: ignore[attr-defined]
        fragments.append(
            Fragment(
                text=body,
                locator_kind=LocatorKind.SHEET,
                locator={"sheet": name, "row_from": first_row, "row_to": last_row},
                locator_label=f'leht "{name}", read {first_row}–{last_row}',
            )
        )
        return total, produced + 1


def _cell_text(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "jah" if value else "ei"
    return normalise(str(value))


class PptxParser:
    name = "pptx"
    version = "1"
    mime_types = frozenset(
        {"application/vnd.openxmlformats-officedocument.presentationml.presentation"}
    )

    def parse(self, source: SourceFile) -> ParseResult:
        import pptx

        limits = current_limits()
        handle = io.BytesIO(source.content)
        # Slides, layouts, masters, notes and relationships: every XML part is
        # budgeted before python-pptx parses any (docs/adr/0182).
        guard_office_xml_parts(handle, guard_office_container(handle, limits), limits)

        try:
            presentation = pptx.Presentation(handle)
        except Exception as error:
            raise ExtractionFailed(
                "unreadable_pptx", "PPTX-i ei õnnestunud avada; fail võib olla rikutud."
            ) from error

        fragments: list[Fragment] = []
        total = 0
        notes_found = 0

        for number, slide in enumerate(presentation.slides, start=1):
            visible = normalise(
                "\n".join(
                    shape.text_frame.text
                    for shape in slide.shapes
                    if getattr(shape, "has_text_frame", False)
                )
            )
            if visible:
                total += len(visible)
                guard_character_budget(total, limits)
                fragments.append(
                    Fragment(
                        text=visible,
                        locator_kind=LocatorKind.SLIDE,
                        locator={"slide": number},
                        locator_label=f"slaid {number}",
                    )
                )

            # Speaker notes are indexed, and labelled. They are frequently where
            # the actual position is written down — and they are also what the
            # presenter never intended the audience to read, so a result that
            # quotes them has to say where it got them.
            notes = _slide_notes(slide)
            if notes:
                notes_found += 1
                total += len(notes)
                guard_character_budget(total, limits)
                fragments.append(
                    Fragment(
                        text=notes,
                        locator_kind=LocatorKind.SLIDE,
                        locator={"slide": number, "part": "notes"},
                        locator_label=f"slaid {number}, esineja märkmed",
                    )
                )

        if not fragments:
            raise ExtractionFailed("no_text", "Esitlusest ei leitud teksti.")

        return ParseResult(
            derivatives=(
                DerivativePayload(
                    fragments=tuple(fragments),
                    metadata={
                        "slide_count": len(presentation.slides._sldIdLst),
                        "slides_with_notes": notes_found,
                    },
                ),
            )
        )


def _slide_notes(slide: object) -> str:
    if not getattr(slide, "has_notes_slide", False):
        return ""
    try:
        return normalise(slide.notes_slide.notes_text_frame.text)  # type: ignore[attr-defined]
    except Exception:
        return ""


registry.register(DocxParser())
registry.register(XlsxParser())
registry.register(PptxParser())
