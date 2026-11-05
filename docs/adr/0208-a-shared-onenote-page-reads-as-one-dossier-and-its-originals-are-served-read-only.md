# 0208 — A shared OneNote page reads as one dossier, and its originals are served read-only

Status: accepted (owner brief «Coordinated corrective round», 2026-11-05; release awaits owner review)
Date: 2026-11-05
Builds on [0205](0205-a-sealed-onenote-desktop-capture-enriches-the-lovable-teemad.md) §4
(shared-page files held, D4), [0206](0206-imported-onenote-files-read-on-their-onenote-day-and-a-held-file-is-not-a-failed-one.md)
(file states from import rows, OneNote dates), 0207 (the continuation chain,
`app/matters/continuations.py`), [0181](0181-one-internal-read-policy-for-all-business-content.md) (the read
policy) and [0076](0076-closed-matter-dokumendid-is-read-only.md) §3 (the
privileged import path, which this decision does not use).

## Context

65 OneNote pages in production are linked to 143 Teemad: one
`MatterSourcePage` per Teema. The capture import (0205) catalogued each such
page's files (`LegacySourceResource`, SHA-256 and size) and held every one —
`LegacySourceResourceImport` `SKIPPED · SHARED_PAGE_AWAITING_REVIEW`, no
`Document` — because a shared page does not prove which file belongs to which
Teema. The bytes are not in the evidence store; they exist only in the sealed
capture package in the read-only source corpus (`HISTORICAL_SOURCE_ROOT`).

Two things went missing for a lawyer. The page read as a list of files
«ootab teemaga seostamist» with no sense that the page itself is whole and
readable, and with no way to open a held file at all. And a shared page is
often one multi-stage dossier — Teema 2025_140 continues as 2025_196
(`Matter.superseded_by`) and both link the same page — which nothing said.

## Decision

### 1. How a shared page's Teemad relate, per reader

`app/legacy_import/shared_dossiers.shared_dossier(page, viewer)` reads the
page's links, keeps the live Teemad the viewer may read
(`Matter.objects.visible_to`), counts the others (`hidden`, never named) and
walks `superseded_by` forward from each readable one (bounded by
`continuations.MAX_HOPS`, through readable Matters only, a revisit ends the
walk). It classifies:

| Class | When |
| --- | --- |
| `ONE_CHAIN` | every linked Teema is on one linear chain, and none is hidden |
| `PARTIAL_CHAIN` | a chain covers some linked Teemad (or all readable ones while some are hidden) |
| `COMPLEX` | linked Teemad converge on a later stage, or a walk loops |
| `NONE` | no linked Teema continues another, as far as this reader sees |

A stage between two linked Teemad that is not on the page does not break a
chain. A stage the reader may not read ends the walk: a continuation edge never
opens a Matter, and a shared page, a title or a date is never read as an edge.
One confirmed pair does not clear the other Teemad on the same page.

### 2. The page says both facts

On `/ajalugu/<link>/` a shared page carries one block in «Allikas»:
«Ühine toimik · N teemat» — «Leht on tervikuna hõivatud ja siin täielikult
loetav. Milline fail millisele teemale kuulub, ei ole otsustatud: failid
ootavad teemaga seostamist ega ole ühegi teema dokumentide all; originaalid on
lähtekorpuses alles.» Then the relationship: «Sama menetluse etapid: 2025_140 →
2025_196» (each other stage a link to its own view of the page, through its
own scope), or «Teemade vahel ei ole registris jätku seost.», «Ülejäänud
teemade vahel …», «Teemade jätku seosed ei moodusta üht ahelat …», and «Lisaks
on leht seotud N teemaga, mida selles kasutajavaates ei kuvata.» for hidden
ones. It replaces «Sama leht on seotud ka» on a shared page; that line stays for
any other case.

### 3. The narrowest read-only download of a held original

`/ajalugu/<link pk>/allikas/<resource pk>/` (`legacy_import:source_original`,
`app/legacy_import/source_originals.py`):

* **Gate.** `login_required`; the link through `visible_links(user)`; the
  resource on that link's page; a `SKIPPED · SHARED_PAGE_AWAITING_REVIEW` row
  with no Document *on that link*; a resource above 0 bytes; and no Document of
  that resource on any link that the reader may not read (ENG-047: the original
  is never a way round a stricter document). Otherwise 404 — not audited, like
  `source_xml` for a page without stored XML, because nothing was attempted.
  A file held as `LARGER_THAN_EVIDENCE_LIMIT` is not served: that is a separate
  decision nobody has made.
* **Locating the package.** Never from the request, the stored
  `archive_relative_path` or the page. The corpus root is scanned (depth ≤ 3,
  ≤ 5,000 entries, no symbolic links, `blobs/` and `pages/` skipped) for a
  `SEAL.json` whose `manifest_sha256` is the row's
  `ImportBatch.source_snapshot_sha256` and whose capture is the row's capture —
  the batch's recorded `capture_id`, else `LegacySourcePage.capture_id` (a page
  row reused from an earlier capture keeps its old id, the batch names the one
  the row was applied from). The seal's `SHA256SUMS` and `manifest.json` are
  re-hashed against it; a seal that does not name its capture is matched by
  the capture its proved manifest names. A found package is cached per process
  (root, capture, digest); a missing one is looked for again next time.
* **The blob.** `blobs/<resource.sha256>`, the digest matching
  `^[0-9a-f]{64}$` and listed in the sealed `SHA256SUMS`; a symbolic link or a
  non-file is refused. Size (`fstat`) and SHA-256 (streamed) are checked on the
  very handle that is then served.
* **Answer.** `application/octet-stream`, `Content-Disposition: attachment`
  with the catalogued filename (`app.core.http.content_disposition`),
  `X-Content-Type-Options: nosniff`, `Cache-Control: private, no-store`. Never
  inline.
* **Audit.** `DOCUMENT_DOWNLOADED` through `shared_gate.audit_detail`, subject
  the resource, detail `source = "onenote_source_original"`, `matter`, `sha256`,
  `disposition`, `outcome` (`served`, or `package_missing`, `blob_not_sealed`,
  `blob_missing`, `size_mismatch`, `hash_mismatch`, `resource_digest_invalid`,
  `no_capture_batch`, `blob_unreadable`). Any failure after the gate is a 404
  with a failed audit row, never a 500, and the answer names no path, key or
  digest.
* **HEAD (amended 2026-11-05, QA-07).** Every byte route — evidence download and
  open, thumbnail, opinion-archive letter, page XML (`source_xml`) and this held
  original — answers HEAD with GET's access checks, status and headers, and
  `Content-Length` from the recorded size (or the directory entry). It reads no
  file content, does no hashing and writes no `DOCUMENT_DOWNLOADED` row: a HEAD
  tells the reader only what the page already shows. A held original's HEAD is
  answered from the catalogue alone; only the GET proves size and digest.
  Other methods stay 405 with `Allow: GET, HEAD` and no row.

Nothing but the audit row is written: no Document, no import row, no decision.
On the page, a held shared-page file offers «Laadi alla (allikas)»; an imported
file keeps its Document links; a withheld file stays unnamed.

### 4. Dokumendid: the earlier stages' pages

For a Teema with readable earlier stages (`historical_predecessors(matter,
viewer).stages`: every branch, the same graph «Varasemad etapid» lists, 0209
§1), «Eelnevate etappide ajalooline materjal» lists those stages' own linked
pages (amended 2026-11-05, QA-11: it had used `continuations_for`'s linear chain
plus the direct predecessors, which left out converging grand-predecessors) that this Teema does not link itself — each once,
by page identity, linking to the *predecessor's* page view (its scope, not this
Teema's), with the stage reference, section, file count and that link's import
status chip. A page this Teema links itself is not repeated; its own row in
«Ajalooline OneNote» says «ka eelneva teema 2025_140 toimikus». Nothing is
counted into this Teema's files or pages.

### 5. Ambiguous pages get a read-only overview

For `NONE`, `PARTIAL_CHAIN` and `COMPLEX`, the page view adds «Lehe teemad ja
failid»: each readable Teema (reference, title, «Avatud»/«Suletud», its chain
relation — «Etapp 1/2», «Jätku seos teiste selle lehe teemadega puudub»,
«Jätku seos ei ole lineaarne») and each file with its OneNote day (Tallinn) or
«Lisamise kuupäev teadmata». No buttons, no form, no write.

## Consequences

* No migration, no model change, no new setting. One route, two modules.
* Files are not reassigned, Documents not created or reparented, holds not
  approved, counts not inflated; a continuation edge grants nothing.
* The download depends on the corpus mount being present in the web container.
  Without it every held original is a 404 with a `package_missing` audit row.
* Each process re-hashes a package's `manifest.json` and `SHA256SUMS` once;
  each download re-hashes its blob before serving.
