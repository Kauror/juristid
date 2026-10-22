# ADR 0178 — Historical source readers stay inside their corpus, and within a budget

- Status: Proposed (2026-10-22), security remediation of the 2026-10-22 review
- Findings: csf_58b192a5e05041464608bf4f (path metadata and symbolic links),
  csf_38e223a165f6a7a5e91a3fa8 (archive and workbook resource limits)

## Context

The legacy importer reads four operator-supplied sources: the OneNote desktop
archive, the opinions archive (a ZIP or a directory), the register workbook and
the KodaDash workbook. Two weaknesses were found in how they are read.

1. **Paths came from the source.** `captureId`, `archivePageKey` and
   `originalPath` in the archive's JSON, and every name in a directory archive,
   were joined to the root and read. A `..` in one, or a symbolic link planted
   inside the archive, read a file outside it — and the bytes became a document
   carrying the archive's provenance.
2. **Nothing bounded the work.** The opinions ZIP was inflated member by member
   into memory with no bound on member count, size or compression ratio, and
   the register workbook was parsed whole whatever range it declared; one cell
   at `XFD1048576` makes the row loop ask openpyxl for 17 billion cells.

## Decision

1. **One path rule for every historical file read** (`app/legacy_import/safe_paths.py`).
   A source path is a relative POSIX path: not absolute, no drive or UNC prefix,
   no backslash, no empty, `.` or `..` segment, no NUL, C0 control or DEL, no
   colon, no trailing dot or space, and no segment that folds (NFKC) into a dot
   segment or a separator. A name is never normalised into acceptability and is
   opened exactly as written, so NFC and NFD Estonian names both still read.
   C1 code points stay allowed: the real opinions archive's names carry
   cp437-as-cp1252 mojibake (`\x81`), which is evidence.
2. **Nothing below the root is a link or a special file.** Every intermediate
   directory and the final file are checked without following links; a link
   (or a Windows junction) or a FIFO, socket or device is *refused*, never
   skipped. The root itself is the operator's choice and may be a link.
3. **The file opened is the file checked.** On POSIX the walk opens one
   component at a time with `dir_fd` and `O_NOFOLLOW`, then `fstat`s the
   opened descriptor and requires the device and inode the check saw. Where the
   host has neither (Windows, development only), the same rules hold through
   `lstat`, with the same identity comparison, a second look at every
   intermediate directory and a containment check of the resolved path.
4. **Archive and workbook limits** (`app/legacy_import/limits.py`), each
   settable in Django settings or the environment under its `LEGACY_IMPORT_*`
   name, refusing rather than truncating. The central directory's claims —
   container size, member count, declared member and total sizes, ratio
   (members over 1 MiB) — are checked before a byte is inflated; members are
   then read in 1 MiB chunks and the bytes actually produced are counted. A
   workbook's size, parts, inflation, worksheet count and every sheet's
   declared range are checked from the ZIP before openpyxl parses it, and the
   loaded extent is checked again before any row loop.
5. **The register workbook stays in normal mode.** `read_only` drops every
   hyperlink — the only pointer to the OneNote page behind a matter — and makes
   `sheet.cell()` rescan the sheet. The parsed rectangle is bounded instead. The
   KodaDash workbook was already `read_only`; its rows are capped as they stream.
6. **ZIP member names keep their old rules.** They are identities, never file
   paths; anything that opens a file by one goes through rule 1. Tightening
   them would change the meaning of catalogued archives.

The defaults are far above the real corpus as measured on 2026-10-22
(opinions ZIP 110 MB, 767 members, worst ratio 2; register 17 sheets, at most
6,358 loaded cells and 5.1 million declared per sheet; KodaDash 35 sheets,
15.6 MB inflated, worst ratio 15), and every reader produced byte-identical
results on that corpus before and after the change.

## Consequences

- No migration, no new service, no new dependency; no deployment setting is
  required.
- A source holding a link or a special file now fails loudly where it used to
  be read; an operator fixes the source, not the importer.
- Residual: a *hard link* to a file outside the root is an ordinary regular
  file and is not detected; creating one needs write access to the corpus and,
  on Linux with `fs.protected_hardlinks`, ownership of the target. The Windows
  fallback narrows but cannot close a deliberate race.
