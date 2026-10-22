# ADR 0182 — Uploads are bounded at the request, and parsers before they allocate

- Status: Proposed (2026-10-22), security hardening round 2, Workstream B
- Deciders: the product owner; implemented in Workstream B of round 2
- Findings: csf_35546c06657fae7b9935969d (upload memory), csf_9b7b703ca586c216499d33bd
  (image pixels), csf_5fb4e796365c901966387382 (OOXML directory),
  csf_ea514a0f447d737dfb1bdfb9 (plain text), csf_eb1ed7815b093aba8872620b (DOCX)
- Extends: 0176 (extraction charges the walk), 0177 (staging quotas and held
  uploads). Neither is amended: their rules stand and this adds the layer in
  front of them.

## Context

Round 1 bounded what the parsers *produce* and what staging may *keep*. Five
places still allocated first and counted afterwards:

- **Uploads.** `read_upload` returned `uploaded_file.read()`, so every accepted
  file became `bytes` in the gunicorn worker, and each door kept the whole
  batch until its last file was written: Saabunud, `Uus teema` (chosen, held
  and staged files), document versions, the Teema panels that take evidence and
  the opinion forms. Twenty hundred-megabyte files were two gigabytes in one
  worker before any aggregate rule was asked; a resumed held upload was read
  back into memory whole as well. The only request-level bounds were Django's
  defaults (100 file parts, `DATA_UPLOAD_MAX_MEMORY_SIZE` for non-file fields);
  gunicorn (`config.wsgi`, 3 sync workers, `--timeout 120`, no body limit) and
  the Compose files set none. Cloudflare's request-body limit for this zone is
  **not verified** from the repository and is not relied on here.
- **Images.** `configure_image_safety` set `Image.MAX_IMAGE_PIXELS` to the
  limit, and Pillow only *warns* up to twice that, so an image of up to
  2 × 80 MP decoded in the thumbnail and OCR paths.
- **OOXML.** `zipfile.ZipFile()` reads the whole central directory and builds a
  `ZipInfo` per entry before `guard_office_container` could count them.
- **Plain text.** `TextParser` decoded the whole file into one string and
  `splitlines()` it before the character budget saw a fragment.
- **DOCX.** python-docx parses every XML part it loads into a tree at open and
  builds one object per paragraph, row and cell it is asked about. A `gridSpan`
  attribute expands one cell into that many cell objects; a vertical merge is
  resolved by recursion up the table, and an inherited header by recursion back
  through the sections (which also *writes* a header part into the document).

## Decision

### 1. One upload policy, at the request boundary (csf_35546c06)

`app/documents/upload_limits.py`:

- `UploadLimitHandler` is the first of `FILE_UPLOAD_HANDLERS`, before Django's
  memory and temporary-file handlers, which are unchanged. It refuses a
  multipart request whose declared `Content-Length` exceeds
  `UPLOAD_MAX_REQUEST_BYTES` **without reading the body**; while the body
  streams it counts files (`UPLOAD_MAX_FILES_PER_REQUEST`) and bytes and stops
  the parse when either is passed. A single file over
  `MAX_EVIDENCE_UPLOAD_BYTES` is not stored more than one parser chunk (64 KiB)
  past that ceiling, and the door still refuses it with its own sentence, by
  name, exactly as before.
- `UploadLimitMiddleware` sits before every middleware that reads a form field
  (`RefuseNulMiddleware`, `UploadTitlesMiddleware`, CSRF's `process_view`). It
  parses first; if the handler refused, it closes whatever the parse had
  written and answers **413** before any view runs — a view never acts on a
  request whose files were cut short. A page gets `413.html` with the sentence;
  an htmx request gets the sentence and `X-Juristid-Failure: upload-too-large`,
  which `static/js/app.js` tells as its own line. Nothing was saved, and the
  page says so.
- `check_upload_batch` applies the same two numbers to the files one save
  files **together**: `Uus teema` counts held uploads and newly chosen ones as
  one batch; Saabunud and every `read_uploads` door ask it from the declared
  sizes before opening a file.

### 2. An accepted file is a body, streamed (csf_35546c06)

`app/documents/upload_body.py`. `AcceptedUpload` carries a *body* — Django's
temporary upload file, a held or staged object on the pending volume, or bytes
a caller already has — not `bytes`. `read_upload` makes exactly the old checks
from the size the handler counted and the first `SIGNATURE_HEAD_BYTES` (enough
for the ASiC-E `mimetype` check). `store_body` is the one way a body reaches a
store: streamed in 256 KiB chunks, hashed while it streams (the checksum is of
the bytes written), refused and removed when it did not write the size it
claimed, and removed when it failed part-way. `add_evidence_version`, held
uploads (`hold`/`resume`), staging (`stage_uploads`/`promote_intake_files`) and
every upload view use it; `promote_intake_files` verifies the staged checksum
by streaming and again as the evidence is written (`expected_sha256`).
Immutable evidence, roles, the extension allowlist, the signatures and the
round-1 quotas are unchanged; held and staged quota checks read sizes, not
bytes.

### 3. Images are refused from their header (csf_9b7b703c)

`open_bounded_image` opens the image lazily and refuses
`width × height > EXTRACTION_MAX_IMAGE_PIXELS` with `image_too_large` before
`load()`, `convert()`, `thumbnail()` or OCR. Every image path uses it: the
image parser asks first (so an oversized image is refused the same way
whether or not OCR is installed), the thumbnail returns none, OCR refuses.
`Image.MAX_IMAGE_PIXELS` becomes half the limit, so Pillow's own
`DecompressionBombError` fires at the limit as a backstop for any path that
opens an image; the warning band below it is filtered. PDF rasterisation was
already bounded before rendering (`bounded_render_scale`, AUD-045). Pillow
13.0.0's `open`/`size`/`DecompressionBombError` API is unchanged.

### 4. OOXML directories are checked from the end records (csf_5fb4e796)

`preflight_zip_directory` runs first in `guard_office_container`. It reads
only the file's tail — the EOCD record within the 64 KiB comment window, and a
ZIP64 locator and record when one precedes it — and refuses before any
directory byte is read when the records are missing or malformed, span disks,
use a ZIP64 sentinel without a consistent ZIP64 record (the record must end
where the locator says and the directory must end where the record begins),
declare more than `EXTRACTION_MAX_ARCHIVE_MEMBERS` entries, or a directory
larger than `EXTRACTION_MAX_ZIP_DIRECTORY_BYTES`. It then walks the directory
one 46-byte header at a time, seeking over names and extra fields, stops at the
first entry past the member limit, and refuses a directory whose entries do not
add up to exactly its declared size and count. Only then is `zipfile.ZipFile`
constructed.

### 5. Plain text streams (csf_ea514a0f)

The raw size is checked first (`EXTRACTION_MAX_TEXT_BYTES`). The encoding is
decided by a streaming pass that keeps no text — the same encodings in the
same order, the same refusals, the control-character share over the whole
file. A second pass decodes 1 MiB at a time, splits lines exactly as
`str.splitlines` (a "\r\n" split across pieces is one break), collapses each
line's whitespace as it arrives (a line of a hundred million spaces is the
empty line it always normalised to) and builds each block of two hundred
lines. Lines (`EXTRACTION_MAX_TEXT_LINES`), characters and fragments are
charged as they go; a line is refused before it is whole once even its partial
length cannot fit the character budget. Fragments, line ranges, encoding and
line count are the old ones; `tests/test_parser_bounds_round2.py` keeps the
9a2e834a algorithm as the reference and compares on generated files with chunk
boundaries forced everywhere.

### 6. DOCX is budgeted before python-docx builds it (csf_eb1ed781)

1. Every part python-docx would parse as XML — by **declared content type**
   from `[Content_Types].xml`, so a part renamed `.png` is still XML — is
   charged by its uncompressed size against `EXTRACTION_MAX_DOCX_PART_BYTES`
   and, together, `EXTRACTION_MAX_DOCX_XML_BYTES`, before `docx.Document()`.
   `zipfile` enforces the declared sizes while inflating, so this bounds what
   the XML parser can be handed.
2. Body paragraphs are counted on the tree (`EXTRACTION_MAX_DOCX_PARAGRAPHS`)
   before one paragraph object exists, then read as a stream.
3. Table rows are expanded from the XML, one row at a time, without
   recursion: each row's grid width (spans, vertical merges resolved from the
   row above) is charged to 0176's `TableBudget` before the row is listed, and
   a merged cell's text is read once. A `gridSpan` below one is refused as a
   malformed table.
4. Headers and footers are resolved iteratively over one document-order list
   of every `w:sectPr`, exactly as python-docx's `preceding::w:sectPr[1]`
   would, without writing a part.
5. Characters are charged before every join. All refusals go through
   `guard_parse_result` as before.

## Settings (all environment variables)

| Setting | Default | Why |
| --- | --- | --- |
| `UPLOAD_MAX_FILES_PER_REQUEST` | 100 | Django's existing per-request ceiling, so no upload that worked is refused for its count (a `Kaasamine` round's replies come by the dozen); with files streamed, the bytes bound the worker. |
| `DATA_UPLOAD_MAX_NUMBER_FILES` | policy + 10 (110) | Django's part ceiling (empty inputs included) stays just above the policy's, so the 413 sentence answers first. |
| `UPLOAD_MAX_REQUEST_BYTES` | 512 MiB | Five maximum-size files; a ministry packet is tens of MB. Also the batch ceiling for held + new. |
| `FILE_UPLOAD_HANDLERS` | policy handler, then Django's two | The boundary itself. |
| `EXTRACTION_MAX_TEXT_BYTES` | 100 MiB | The upload ceiling: no file that could be uploaded is refused by it. |
| `EXTRACTION_MAX_TEXT_LINES` | 10 000 000 | The most lines that still fit the 50 000-fragment budget with text in every block. |
| `EXTRACTION_MAX_ZIP_DIRECTORY_BYTES` | 16 MiB | 8 KiB for each of the 2 000 allowed entries; a real OOXML entry is ~100 bytes. |
| `EXTRACTION_MAX_DOCX_PART_BYTES` | 64 MiB | A thousand-page draft's `document.xml` is tens of MB. |
| `EXTRACTION_MAX_DOCX_XML_BYTES` | 128 MiB | All XML parts together (python-docx's own styles parts are ~0.8 MB). |
| `EXTRACTION_MAX_DOCX_PARAGRAPHS` | 1 000 000 | A thousand-page draft has tens of thousands. |

Unchanged and now load-bearing: `MAX_EVIDENCE_UPLOAD_BYTES` (100 MiB),
`DJANGO_FILE_UPLOAD_MAX_MEMORY_SIZE` (10 MiB — Django keeps files in memory only
when the whole request is under it, so it is also the in-memory ceiling per
request), `EXTRACTION_MAX_IMAGE_PIXELS` (80 M, now enforced at the limit).

## Alternatives

- **A per-door check.** Nine doors and counting; the request is one. The
  per-door sentences stay for what they always said.
- **Reverse-proxy limits only.** The tunnel is the only route in, and its
  limit is neither in the repository nor verifiable from it.
- **Parsing each document in a subprocess with an address-space limit.** The
  only *hard* bound on parser memory (see Consequences). Not built: it is a
  new process model for the reader and the worker, and the owner asked for
  in-process bounds first.

## Consequences

- An over-limit upload is a 413 page or line, not a form sentence, and the
  files must be chosen again; the per-file, per-form and staging sentences
  below the limits are unchanged. Only requests over the new numbers change.
- Multipart bodies are still parsed before authentication, as before; the
  handler now bounds what an unauthenticated request can write to the
  temporary directory to the request ceiling, removed when the request ends.
- Refusal codes on a hostile file that breaks two limits at once may differ
  from the old parser's (the earlier limit now answers); an ordinary file's
  output is identical.
- **Residual: parser memory is bounded, not capped.** A 64 MiB XML part still
  becomes an lxml tree several times its size, and PDF, XLSX shared strings,
  PPTX slides and the email parsers still hold one file in memory. In-process
  bounds limit the input each library receives; they cannot cap what it
  allocates. The hard bound is process isolation: run `parse_source` in a
  child with `RLIMIT_AS` and a wall-clock limit, or give `intake-reader` (and a
  future extractor) a Compose `mem_limit`. Proposed here, not built.
- Reversible: every number is a setting; removing the handler and middleware
  lines restores the old boundary without touching data. No migration, no
  Compose change, no search rebuild.
