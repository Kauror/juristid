# ADR 0176 — Document extraction charges the walk, not only the output

- Status: Proposed (2026-10-22), security remediation of the 2026-10-22 scan
- Deciders: the product owner; implemented in Workstream B of that remediation
- Findings: csf_a74bfe6e (XLSX), csf_8da83d23 (EML/MSG), csf_5fa01ed3 (CSV)

## Context

ADR 0014 made every extraction limit a refusal rather than a truncation and
put each one in settings. The limits it set all measured *output*: characters
written, pages, archive members. Three parsers could spend unbounded time or
memory before producing any output to measure:

- **XLSX.** openpyxl's read-only reader walks the worksheet's *declared* used
  range and pads every row to its width. A 5 KB workbook with one word in A1
  and one styled empty cell at XFD1048576 declares 17 billion cells; blank
  cells produce no text, so no budget was ever charged.
- **EML/MSG.** Decoded headers and the body (plain, or HTML flattened by the
  sanitiser at about 1 MB/s) were never checked against
  `EXTRACTION_MAX_CHARACTERS`. The script/style pre-pass was a backtracking
  regular expression: 64 KB of unclosed `<script>` took seven seconds and a few
  hundred kilobytes would hold the worker for hours.
- **CSV.** `list(csv.reader(...))` built the whole row graph before the
  character budget ran, so one record of thirty million separators was thirty
  million cells in memory first.

## Decision

1. **Walk budgets** (`TableBudget`, `app/documents/extraction/limits.py`).
   XLSX and CSV charge every row and every cell they *visit*, empty ones
   included (an empty row costs one cell): `EXTRACTION_MAX_TABLE_ROWS` per
   sheet or file (1 048 576, Excel's last row), `EXTRACTION_MAX_TABLE_COLUMNS`
   per row (2 000), `EXTRACTION_MAX_TABLE_CELLS` across the whole file
   (100 000 000, about fifteen seconds of one worker) and
   `EXTRACTION_MAX_TABLE_CELL_CHARACTERS` per cell (131 072). A worksheet's
   declared dimensions are checked against the same numbers **before its first
   row is read** (`sheet_dimension_limit`); the walk is charged again as it
   goes, because the dimension record can be absent or understate the sheet.
2. **CSV streams.** The file's size is checked before decoding
   (`EXTRACTION_MAX_CSV_BYTES`, 32 MiB); each record's raw length is checked
   before the csv module builds it (`EXTRACTION_MAX_CSV_RECORD_CHARACTERS`,
   1 000 000); rows, cells and characters are charged as rows arrive. Delimiter
   sniffing, row locators, fragment boundaries and metadata are unchanged; a
   test keeps the 4818203e algorithm as the definition of "unchanged".
3. **Email is one budget.** Each decoded header and the flattened body are
   charged to a `CharacterBudget` as they are made; the decoded body is
   measured before flattening (`EXTRACTION_MAX_EMAIL_BODY_CHARACTERS`,
   16 000 000, about 15 s of sanitiser). The script/style pre-pass is a linear
   scan that removes exactly what the old expression removed.
4. **A central postcondition.** `guard_parse_result` runs in `parse_source`,
   the one path the corpus worker and the intake reader share, on every
   parser's result: total fragment text within `EXTRACTION_MAX_CHARACTERS`,
   fragment count within `EXTRACTION_MAX_FRAGMENTS` (50 000), locator labels and
   metadata within `EXTRACTION_MAX_CHARACTERS`. A parser that forgets a budget
   fails like one that enforced it.
5. **Every refusal is the existing failure path.** An `ExtractionFailed` with
   a named code; the version or staged file ends `FAILED` with the Estonian
   reason, the original bytes are untouched, nothing is truncated, and the
   loop goes on to the next file. No new state, service or dependency.

The numbers are sized against the corpus: the historical registers are a few
thousand rows by a few dozen columns, the 500-fragments-per-sheet cap already
stops a sheet at 25 000 rows with text, and visiting the full 10 M-cell budget
takes about 1.5 s. All are environment variables.

## Consequences

- A workbook whose dimension record claims more than 2 000 columns or 100 M
  cells is refused even if most of it is empty. It is reported FAILED with the
  declared size in the reason. No sheet is refused on its height alone: the
  "somebody pressed Ctrl+Down to row 1 048 576 and typed there" file still
  reads, a few dozen columns wide, at a cost of seconds — legitimate Office
  documents keep working, and the orchestrator chose that over a tighter
  budget (2026-10-22 review).
- Container-level CPU, memory and PID limits for the reader remain the
  deployment's job (Compose); they are recommended, not set, here.
- openpyxl still parses one XML `<row>` whole and loads shared strings at
  open; those stay bounded by ADR 0014's uncompressed-size and ratio guards.

## Status update 2026-10-29 — merged and deployed

This section records what happened after the decision; it does not amend the decision, and the status is unchanged (no formal acceptance was recorded).

- **Merged** in the security remediation integration (merge `9a2e834a, #514 = #506–#513, 2026-10-22`).
- **Deployed** to production as `1270ca01` (2026-10-23, the release of #521, which carried the security baseline merged before it).
