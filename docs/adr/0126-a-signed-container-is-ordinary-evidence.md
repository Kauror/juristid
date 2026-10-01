# 0126 — A signed container is ordinary evidence, through every door

**Status:** accepted
**Date:** 2026-10-01

**JUR-CASE-01 from the Juristieksami living-dossier QA.** An `.asice` or `.bdoc`
— the Estonian digitally signed container the Chamber's real correspondence
travels in — is accepted by the upload door like any ordinary format, stored as
the bytes that arrived, and opened by nothing. **No migrations**, no new model,
no new storage class.

1. **`read_upload` accepts `.asice` and `.bdoc`** and records both as
   `application/vnd.etsi.asic-e+zip` — the media type the historical importer
   has always recorded for them. Every surface that captures a file reads that
   one validator, so `Uus teema`, `Saabunud`, `Lae dokument`, a new version,
   every `LISA TEEMALE` panel and `+ Koja arvamus` accept a container together.
2. **The content check reads one thing**: the media type every ASiC-E container
   declares in its first ZIP entry, `mimetype`. A PDF, a Word file, an ordinary
   ZIP or another container type renamed to `.asice` is refused, in Estonian,
   naming what is wrong.
3. **Nothing is unpacked, verified or previewed.** The container is one
   `Document` with one immutable `DocumentVersion`: its name, its extension and
   its bytes. No document is taken out of it and presented as the evidence.
4. **Every evidence picker offers exactly the server's list** (`accept`), so the
   browser stops offering a file the server would refuse after it was sent.

---

## The defect

The QA filed a VTK package exactly as the ministry sent it and recorded the
Chamber's opinion exactly as it went out:

* `Uus teema` refused `vtk_package.bdoc` the moment it was chosen:
  «Faililaiend .bdoc ei ole lubatud. Lubatud: .csv, .doc, …, .zip».
* `+ Koja arvamus` let `Saadetud fail` take an `.asice` without complaint and
  refused it only after `Registreeri arvamus`, with the same sentence.

The only way to keep either original was to wrap it in a ZIP — which keeps a
ZIP. The evidence model this product is built on is «the bytes that went out
are the record», and for the Chamber's signed letters those bytes *are* the
container.

## Root cause

Two lists guard two doors (docs/adr/0015). `ALLOWED_EVIDENCE_MIME_TYPES` — what
the evidence store holds — has carried `application/vnd.etsi.asic-e+zip` since
Stage 2D, because 1,049 of the historical corpus's files are signed
containers. `EXTENSION_MIME_TYPES` in `app/documents/uploads.py` — what a
browser may push — was kept deliberately narrow and never gained them: ADR 0015
reasoned that the archive's formats arrived hashed and verified while the door
faces strangers. That reasoning was right about Office templates and saved web
pages and wrong about the one format the Chamber's own present-day work arrives
in.

Every server-side capture path converges on `read_upload`, so the refusal was
one table. There was **no client-side restriction at all**: no file input in
the application carried `accept`, so every picker offered everything and the
refusal came from the server — at once on `Uus teema`, whose files are staged on
choice, and after the save everywhere else. That is the «accepted by the
picker, refused after submit» the QA met on `+ Koja arvamus`.

## Decisions

### 1. `.asice` and `.bdoc` through the upload door, as one media type

`.asice` is the name ETSI EN 319 162-1 gives an ASiC-E container and `.bdoc` is
the Estonian BDOC 2.1 profile's name for the same container, so both record
`application/vnd.etsi.asic-e+zip`. The entries leave
`HISTORICAL_EXTENSION_MIME_TYPES` for `EXTENSION_MIME_TYPES`, and
`historical_materials.mime_type_for` — which reads the upload table first —
answers exactly what it answered before.

### 2. The content check: the container's own first entry

The door has three checks: a size ceiling, an extension allowlist, and a content
signature computed from the leading bytes (docs/adr/0072). A signed container
gets the third the way a PDF does — read from the start of the bytes — one ZIP
header further in: `starts_like_signed_container` requires a ZIP local file
header whose entry is named `mimetype` and whose data begins with
`application/vnd.etsi.asic-e+zip`. That is what every ASiC-E container declares
about itself, and what DigiDoc reads first.

**Measured before it was written.** The rule was checked read-only against all
1,049 signed containers in the Chamber's historical corpus on 2026-10-01
(counts only; no names, no content left the host):

| shape of the first entry | `.asice` | `.bdoc` |
| --- | --- | --- |
| stored, no extra field — the standard layout | 1,016 | 26 |
| stored, with a ZIP extra field | 0 | 1 |
| deflated, sizes in a data descriptor (a streaming encoder) | 6 | 0 |

A rule written from the standard alone — `mimetype` stored at the fixed offset
38 — would have refused seven real containers, which is this defect by a
different door. So the entry's data is found by the lengths its header
declares, and a deflated entry is inflated **in memory, with both input and
output bounded** (4 KiB in, 31 bytes out): nothing is written, nothing else in
the container is read, and a file cannot make the check expensive. All 1,049
pass.

What is refused, each with «Faili sisu ei vasta laiendile .asice: see ei ole
digiallkirjastatud ümbrik. Kontrolli, kas fail on terve ja õiget tüüpi.»: a PDF,
an executable or an Office file renamed; an ordinary ZIP; an OpenDocument file;
an ASiC-S container (`.asics` is not accepted, below); a truncated header; a
first entry that does not inflate. The sentence names the container rather
than repeating the generic «does not match its extension», because somebody
holding a file DigiDoc saved needs to hear that this is not one.

### 3. Opaque, preserved, never opened

A container is stored by the same `create_document` / `add_evidence_version`
as every file, under the same immutability trigger, checksum, download route
and authorization. Nothing unpacks it: unpacking a signed container to show or
index the document inside would present the extract as the evidence, which is
the inversion Stage 2D refused (docs/adr/0015). Extraction finds no parser for
the type and settles it `NOT_APPLICABLE` when it reaches it; `Uus teema`'s
reader does exactly that, and the promoted version says so. The download is an
attachment under the file's own name and type.

### 4. A sent `Koja arvamus` may be the container

`+ Koja arvamus` reads the same door, so the container is `Saadetud fail` like
any letter: one `Document` (`KODA_SUBMISSION_FINAL`), one version, one SENT
`Submission` whose `final_version` is the container's bytes. **The
evidence-before-SENT invariant is untouched**: the upload is read before any
row is written and the whole act is one transaction, so a refused container
leaves neither a `Submission` nor a `Document`; the constraint
`submissions_sent_requires_timestamp_and_evidence` and the final-evidence
triggers are unchanged and indifferent to the type. This settles, for an opinion registered here, the
question docs/open-decisions.md asks of the archive — the container that went
out is the evidence.

### 5. The picker offers the server's list

`UPLOAD_ACCEPT` is `EXTENSION_MIME_TYPES`' keys, joined, and every evidence file
input carries it: the five written in templates through `{% upload_accept %}`,
and every form field's widget through the constant (`MultipleFileInput` sets it
by default). `tests/test_signed_containers.py` walks every template, every form
and three rendered pages and fails on a picker that offers anything else. The
server stays the control — a dropped file, or «All files» in the dialog, still
meets the same refusal — but the browser no longer *offers* what will be
refused.

### 6. An e-mail's signed attachment is kept

`email_intake` decides an attachment's type by the same table, so a container a
ministry attached to an e-mail is now stored beside the message instead of being
skipped as an unknown type. That path keeps its own contract — the extension
decides and the bytes are not signature-checked, as for every attachment type —
because the message itself already passed the door.

## Alternatives considered

* **Accept on the `PK\x03\x04` prefix alone**, as `.docx` and `.zip` are. Simpler,
  and would let any ZIP renamed to `.asice` in. The container's first entry is
  as cheap to read and is what actually distinguishes it.
* **Open the central directory with `zipfile`** to find `mimetype` and catch a
  truncated file. It parses every entry of an untrusted archive inside the
  request, its cost grows with the entry count, and no other format at this door
  is checked past its leading bytes.
* **Verify the signatures**, or show the document inside. Out of scope and
  deliberately so: a system that unpacks a container to display it starts
  asserting things about signatures it cannot check (docs/adr/0015).
* **Accept `.ddoc`, `.asics`, `.edoc`, `.adoc` as well.** `.ddoc` is the legacy
  XML envelope, retired from new signing years ago; it stays the archive's. The
  others have no canonical, tested support anywhere in the repository and are
  not what the Chamber's workflow sends; adding them would be speculation.

## Consequences

* A ministry's `.bdoc` and the Chamber's sent `.asice` are filed as themselves
  on every surface, and download as themselves.
* `Lae dokument`'s help text names the two formats.
* A container's text is not searchable and has no preview, exactly as the
  historical ones; its title and filename are, through the existing
  findability-by-name rule (docs/adr/0113).
* Reversible by removing the two entries; stored containers would stay valid
  evidence, as the historical ones always were.

## What this does not change

The size ceiling; every other format, its MIME type and its content check; the
generic refusal sentence for them; `.ddoc` and the archive's other formats;
`ALLOWED_EVIDENCE_MIME_TYPES`' membership; `add_evidence_version`; the evidence
immutability trigger; the submission constraint and triggers; document roles
(an incoming container is `INCOMING_AUTHORITY` like any incoming file); the
extraction pipeline and its parsers; search and `INDEX_VERSION`; the historical
importer's behaviour and its `NOT_APPLICABLE` note for containers.

---

*Amends* docs/adr/0015 («the interactive upload path stays as narrow as it is»)
for `.asice` and `.bdoc` only, annotated in place there.
