# 0205 — A sealed OneNote desktop capture enriches the Lovable Teemad

Status: accepted (owner brief «Phase 2: Validate and import OneNote evidence»,
2026-11-04)
Date: 2026-11-04
Builds on [0204](0204-onenote-ingress-reads-by-scoped-identity-and-proves-each-capture.md) (the desktop reader and the
source-identity contract), [0203](0203-lovable-is-the-whole-register-source-authoritative-replacement.md),
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

## Amendment 2026-11-05 — a later capture

Phase 2 applied one capture. A later sealed capture of the same notebook (an
owner-started rescan) meets pages this importer already wrote. This amendment
fixes what such a capture may do. `tests/test_onenote_capture_changes.py` pins
it against invented captures: capture 1 is applied, then capture 2 of the same
invented page is planned and applied.

**Earlier rows.** An *earlier row* is a `LegacySourceResourceImport` that this
importer recorded in a batch of another sealed manifest: `source_system`
`ONENOTE_DESKTOP+CAPTURE` and another `source_snapshot_sha256`. Rows of the
October evidence pilot (another importer, other resource keys) are never
continuity candidates. A first capture therefore plans exactly as before, and
its plan digest is unchanged: the new `earlier_blocks` decision enters the
digest only when it is non-empty. A resource that this capture's own batches
wrote is progress, never earlier, so the digest does not move while a later
capture is applied.

**Identity.** The block object ID stays the identity. Without it, continuity
is claimed only for a **pure move**, and every condition must hold:

* the same page GUID, which is the page key;
* exactly one earlier row on this Teema's link with this block creation time
  and filename;
* exactly one block of this capture with that pair;
* an unchanged SHA-256 and size.

Anything weaker that still resembles an earlier file is **held** with a reason
code. It is never matched, and never made into a second Document. A floating
file has no creation time, so it is never continued.

**Kinds of hold.** A *recorded* hold catalogues the new block identity as a
`LegacySourceResource` and writes a `SKIPPED` row with the code. A *plan-only*
hold keeps an earlier capture's block ID while this capture differs. No row
can hold that change under the current schema, because one row exists per
(link, resource) and one resource per (page, key). So the plan names it, the
earlier rows and the immutable `DocumentVersion` stay exactly as they are, and
`verify` keeps reporting the difference.

### The contract, case by case

Each case lists what the code did at `951847a8` (PR #597), then what it does
now. Every outcome is pinned by the tests.

1. **Unchanged capture**
   * Before: nothing was written, and the plan said so only as `pages_done`.
   * Now: nothing is written, no batch is opened, and `verify` is clean. The
     plan also reports `pages_pending: 0` and `items_already_recorded`.
   * Supported.
2. **New attachment on a page an earlier capture completed**
   * Before: it was imported, but the page view never showed it. The view
     renders the stored blocks, which are capture 1's.
   * Now: it is imported, and the page view lists it after the narrative
     (see "The page view" below). The page row stays capture 1's own record.
   * Supported. Completion is per (Teema, occurrence) of *this* capture, so a
     page is never skipped because an earlier capture of it was complete.
3. **Changed bytes under the same block ID**
   * Before: a plan problem that refused the *whole* capture.
   * Now: that occurrence is held as `BYTES_CHANGED_UNDER_SAME_BLOCK`
     (plan-only) and every other occurrence proceeds.
     * The earlier `DocumentVersion` and resource row are unchanged.
     * `verify` reports `resource hash/size differ` for it.
   * A changed creation time under the same ID is held the same way, as
     `BLOCK_TIME_CHANGED_UNDER_SAME_BLOCK`.
   * Importing the new bytes as version 2 is **NOT SUPPORTED** (P1; strict
     xfail).
4. **Changed structural comment, unchanged bytes**
   * Before: silent.
   * Now: held as `COMMENT_CHANGED_SINCE_IMPORT` (plan-only), and the
     Document's provenance note is kept.
     * Detection compares this capture's `Märkus/Pealkiri OneNote'is` lines
       with the earlier Document's note.
     * It only flags; it never writes.
   * Recording the new comment is **NOT SUPPORTED** (P2; strict xfail).
5. **Moved or renamed page, every block ID re-issued**
   * Before: every file became a *second Document* of the same evidence, and
     `verify` reported each earlier row as `unexpected import row`.
   * Now, by situation:

     | Situation | Outcome | Document |
     | --- | --- | --- |
     | Pure move | `SKIPPED · CONTINUES_EARLIER_BLOCK` | none |
     | Move plus byte change | held: `MOVED_BLOCK_BYTES_CHANGED` | none |
     | (time, filename) not unique either side | held: `AMBIGUOUS_EARLIER_BLOCK` | none |
     | Floating file, same name and bytes | held: `RESEMBLES_EARLIER_FILE_UNPROVEN` | none |
     | Floating file, same name, other bytes | promoted as a new file | new |
     | Dated file, same name, another time | promoted as a new file | new |

     For a pure move, the earlier row's decision stands. The detail names the
     earlier block, and the new block identity is catalogued with its own time.
   * Supported, with these bounds. The page row (section, title, blocks) is not
     updated (P3).
6. **Copied page** (new page GUID, the old times kept)
   * Before: a second Document of every file.
   * Now: a new page row and link, which is a new identity, so the copy never
     continues the original.
     * A file whose bytes, filename and creation time equal an earlier
       capture's file on another page of the same Teema is held as
       `SAME_FILE_ON_ANOTHER_PAGE_OF_TEEMA`.
     * A file new on the copy is imported.
   * Supported.
7. **Removed or unavailable attachment**
   * Before: nothing was written, but `verify` reported the earlier row as
     `unexpected import row`.
   * Now:
     * nothing is written and the Document stays;
     * the plan counts the file as `earlier_files_not_in_this_capture`;
     * `verify` counts the earlier row as `earlier_capture_rows`. This is the
       one change to `verify`: a row of an earlier *capture* batch is counted,
       not judged. Any other unexpected row is still a problem.
   * Supported.
8. **Lovable locator change only observed** (`observed` differs, `applied`
   unchanged, COMPARE)
   * The plan checks the **applied** link, the register's accepted record.
     That is right: an observation was never applied, and following it would
     link a page the register does not name.
   * Now:
     * the difference is counted as `observed_link_differs`;
     * a capture that followed the unapplied observation is a problem whose
       text says so.
   * The applied link's production shape `{"source", "target"}` is now read by
     its halves. Earlier, `str()` of the dict passed for a web link by
     accident, and the halves must name one page.
   * Supported.

Also counted, never written: `pages_text_changed_since_stored_row` is the
number of pages whose sealed `post.xml` differs from the stored row's
`source_xml_sha256`. Those rows are kept (§3, P3).

**The page view.** `historical_views._rendered_blocks` renders the stored row's
blocks. It now appends every catalogued file that no stored block reaches,
under one line: «Lehe tekst on hõivest <capture_id> (<date>) …». That covers a
later capture's files and the capture files on an October-pilot page (below).
A row recorded as `CONTINUES_EARLIER_BLOCK` is the file already shown, so it is
not repeated. This is a projection: no stored row changes. The new SKIPPED
codes are not yet in that module's `HELD_STATES`, so they read as «not
imported» until that table is extended.

**The reused pilot pages.** Under §3 the 11 pages that the evidence pilot had
already catalogued kept the pilot's text, blocks, links, source XML and
`capture_id`. Their pilot blocks name the pilot's resource keys. The capture
added its files as resources keyed by block object ID, which no pilot block
names, so the view showed the pilot's body and files and none of the capture's.
The capture's Documents were still on the Teema's Dokumendid. The capture's
`EARLIER_CAPTURE_NOT_IMPORTED` rows on the pilot files read «superseded». A
read-only count of each such page, with no names or text, is
`C:/CC/_worklogs/coordinated-2026-11-05/reused_pages_audit.py`. The projection
above shows the capture's files on those pages without rewriting them.

### Is an owner-started rescan ready?

**Partly.** The tests show that a later capture can be validated, planned and
applied without a second Document or an overwritten version for cases 1, 2 and
5–8, and that cases 3 and 4 are held by name. Three things are not shown:

* a real rescan at scale;
* that `verify --require-complete` is clean when an attachment was edited (by
  design it is not, until P1);
* that the held codes are labelled in the page view.

Treat a rescan as safe to *plan and review*, not yet as a routine.

### Proposals, not implemented (each needs a migration and owner approval)

* **P1 — an edited attachment becomes the next version of its Document.**
  * New model `LegacySourceResourceRevision`:
    * `resource` FK, PROTECT;
    * `capture_id`, `sha256`, `size_bytes`, `source_added_at`,
      `block_modified`, `archive_relative_path`;
    * `context` JSON (P2);
    * `import_batch` FK.
  * Constraint: unique (`resource`, `sha256`).
  * `LegacySourceResourceImport` gains a nullable `revision` FK. Its unique
    (`matter_source_page`, `resource`) becomes (`matter_source_page`,
    `resource`, `revision`) with `nulls_distinct=False`.
  * Backfill: one revision per existing resource from its own columns. Its
    `capture_id` comes from the capture batch whose `source_file_name` prefixes
    `archive_relative_path`, else the page's. Existing import rows get it too.
  * Apply: for `BYTES_CHANGED_UNDER_SAME_BLOCK` whose earlier row is
    IMPORTED, `add_evidence_version` on the same Document (0003: version 1 is
    untouched), then a new revision and an IMPORTED row naming the new version.
  * Tests:
    * v1's bytes, SHA-256 and storage key are unchanged;
    * v2 equals the seal;
    * a replay writes nothing;
    * `verify` reads the latest revision.
* **P2 — a comment per capture.** Store each occurrence's structural context on
  its revision instead of parsing provenance notes. The document page shows each
  comment with its capture. Backfill re-reads the sealed capture-1 manifest from
  the read-only corpus; it is not parsed from notes.
* **P3 — page revisions.**
  * New model `LegacySourcePageRevision`:
    * `page` FK;
    * `capture_id`, `source_xml_storage_key`, `source_xml_sha256`;
    * `derived_text`, `blocks`, `links`;
    * `title`, `section`, `section_groups`;
    * `import_batch`.
  * Constraint: unique (`page`, `capture_id`).
  * The `LegacySourcePage` row stays the first record. The view renders the
    latest revision, labelled with its capture, and links the earlier ones.
  * Backfill: one revision per page from its own columns.
  * **Data correction for the 11 reused pilot pages** (a proposal only):
    * add a capture-1 revision from the sealed `post.xml` and
      `rendered_blocks`;
    * never rewrite the pilot row;
    * prove it with the audit script before and after.
