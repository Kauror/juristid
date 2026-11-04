# 0205 — A sealed OneNote desktop capture enriches the Lovable Teemad

Status: accepted (owner brief «Phase 2: Validate and import OneNote evidence»,
2026-11-04)
Date: 2026-11-04
Builds on [0204](https://github.com/Kauror/juristid/pull/595) (Proposed, Phase 1:
the desktop reader and the source-identity contract), [0203](0203-lovable-is-the-whole-register-source-authoritative-replacement.md),
[0092](0092-substantive-matter-history-and-evidence-safe-process-timeline.md) §6,
[0076](0076-closed-matter-dokumendid-is-read-only.md) §3,
[0173](0173-the-evidence-pilot-imports-reconstructed-history-from-a-reviewed-package.md).

## Context

Lovable is the whole operational register (0203): 524 Teemad in the
`lovable-register` cohort. Its OneNote link names one desktop page GUID exactly
for 472 of them, on 394 pages. On 2026-11-04 the owner's laptop read every one
of those pages through the OneNote desktop client and copied every file it
holds: 6,614 occurrences, 6,520 distinct originals, 2.51 GB. Each occurrence
keeps its block's creation time (6,412 have one; 202 sit on the page canvas and
have none), and each page keeps its text and structure.

The owner authorised adding that material to the existing Teemad, without new
Teemad and without touching anything Lovable derives.

## Decision

1. **The input is a sealed capture, not the notebook.** A directory
   `manifest.json` + `pages/<tag>/{pre,post}.xml` + `blobs/<sha256>`, pinned by
   `SHA256SUMS` and `SEAL.json` (schema `juristid.onenote-desktop-capture/2`).
   It lives in the read-only source corpus, never in Git, CI or a database. The
   importer reads it only through the seal: a changed, missing or extra file, an
   unsafe path or a count that does not reconcile is refused.
2. **A new, separately gated command**: `onenote_capture_import validate | plan |
   apply | verify | digest`.
   * `apply` needs `JURISTID_ONENOTE_CAPTURE_IMPORT` and `REAL_DATA_ALLOWED` on
     the one-off container, plus:
     * `--operator-intent onenote-capture-import`;
     * the sealed manifest digest;
     * the reviewed plan digest;
     * a named backup set.
   * It never calls the Excel-era write paths that 0203's guard closes, and it
     never re-enables them.
   * It is not the evidence pilot, which imports only into an empty database.
3. **Identity.**
   * **Page.** A page is `LegacySourcePage(page_key = "p-<page GUID>")`. That
     key is the one the evidence pilot already used, and the GUID survives
     moves and archiving (0204, D2). A page that already has a row is reused
     *unchanged*, because 0203's preservation proof covers it.
   * **Occurrence.** An occurrence is `LegacySourceResource(resource_key = the
     file block's object ID)`.
   * **Association.** A Teema's association is `MatterSourcePage(PRIMARY,
     LOVABLE_EXACT_PAGE_GUID)`.
   * **Resolution.** A Teema is resolved by its Lovable topic UUID through
     `LovableSyncState`. Every (page, Teema) pair is checked:
     * the UUID must agree with the Teema the capture names, in that cohort;
     * a topic names exactly one Teema, and a Teema exactly one topic;
     * the Teema's own stored Lovable OneNote link must name the page GUID.

     Titles, filenames, reference numbers and SHA-256 equality never decide
     anything.
4. **One decision per (Teema, occurrence).** Each is recorded once in
   `LegacySourceResourceImport`.
   * **IMPORTED.** A single-Teema page's file becomes a `Document` and an
     immutable `DocumentVersion` through `create_document` and
     `add_evidence_version`. That is the import path of 0076 §3, so it is
     written on closed Teemad too.
     * It has no actor and is outside any composer operation, so the Teema
       chronology files it on its OneNote day.
     * The bytes are hashed before and after storing.
     * Repeated bytes stay separate occurrences, and separate documents.
   * **SKIPPED `SHARED_PAGE_AWAITING_REVIEW`.** The page is linked by several
     Teemad, which does not prove which file belongs to which. The page and its
     files are catalogued once; no Document is made (owner decision D4).
   * **SKIPPED `EMPTY_FILE`.** A 0-byte file is catalogued; no empty evidence is
     made.
   * **SKIPPED `LARGER_THAN_EVIDENCE_LIMIT`.** A file above
     `MAX_EVIDENCE_UPLOAD_BYTES` is catalogued and held, never a failure.
   * A page with no file is still catalogued and linked to its Teema.
   * **SKIPPED `EARLIER_CAPTURE_NOT_IMPORTED`.** An earlier capture's resource
     row on a reused page is accounted for, never imported.
5. **Dates and context.** These are ADR 0092 §6 and 0204 point 7, unchanged.
   * **Dated files.** `source_added_at` is the block's creation time, with basis
     `ONENOTE_BLOCK_CREATED`.
   * **Floating files.** Such a file stays undated. Its own last-modified time
     appears only as source metadata in the document's provenance note (D8).
   * **Separate times.** The capture time goes to `acquired_at`. File metadata
     and block-modification times are never a date.
   * **Content may be newer.** A block modified more than an hour after it was
     created is noted as «faili sisu võib olla hiljem uuendatud».
   * **Comments.** Only `ATTACHED_NOTE` / `CONTAINER_HEADING` text becomes the
     file's comment, verbatim, in the provenance note. That note is not
     indexed. Adjacent and page text stays page text (D9).
6. **Batches and recovery.**
   * One transaction per page, with the page's Teemad locked.
   * The first failure rolls its page back whole and stops the run; the pages
     before it stay.
   * Every writing run is one `ImportBatch`, carrying the plan digest, the
     backup set and its report.
   * A finished page is skipped, so a repeated run writes nothing and opens no
     batch.
   * The plan digest depends only on the decisions, so it does not move as
     batches complete.
7. **Untouched.** These are never written:
   * Matter rows and Lovable references or sync state;
   * existing source rows;
   * the Excel-era paths;
   * Lovable and OneNote themselves.

   `digest` gives, per Teema, a digest of the Matter, its sync state and its
   source references, compared before and after each batch. `lovable_pilot
   verify` no longer counts a Document this import made as a discrepancy.

## Consequences

* The changes are:
  * one command;
  * one setting;
  * one choices-only migration (`legacy_import/0022`);
  * one documented exception in `lovable_pilot verify`.
* The 65 shared pages are review work: their files are in the source corpus,
  linked to no Teema's Dokumendid until a person decides.
* A Teema's computed «viimane tegevus» now also considers its OneNote page's
  dates (`ActivityBasis.ONENOTE_*`). The register's stored fields do not change.
* Evidence storage grows by the bytes of the promoted occurrences, once per
  Teema; no content-addressed sharing.
