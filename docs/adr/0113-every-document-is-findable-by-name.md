# 0113 — Every document is findable by its name, and the projection holds text

**Status:** accepted
**Date:** 2026-09-25

**Amends ADR 0072's search consequence.** 0072 withdrew *content* search for
uploads that nobody extracts. It said metadata search was unaffected, and it
was not: a document's name reached the projection only through the rows of an
ACTIVE derivative. This ADR makes 0072's sentence true.

## Context

Three defects in what the search projection holds, all confirmed on
`c1aa64ae`:

* **ENG-030.** `SearchDocument` had no row for a Document, only for the pages
  of its extracted text (`DOCUMENT_FRAGMENT`). Since ADR 0072 nothing extracts
  an ordinary upload, so every newly filed document was unfindable by title or
  filename. `/otsing/` answered «Vasteid ei leitud», and the integrity check
  reported the index healthy.
* **ENG-082.** `plain_text` returned nh3's output: markup with the tags removed
  and the entities still escaped. `AS Näide & Partnerid` was indexed as
  `AS Näide &amp; Partnerid`, and the template escaped it again in the snippet.
  `<p>Tere</p><p>kolleeg</p>` became one word.
* **ENG-084.** Authored text was copied into the projection uncapped. The vectors
  are computed inside the business write, so a body of about a megabyte of
  unique words exceeded PostgreSQL's 1 MiB tsvector limit and the save failed
  with a 500.

## Decision

**One `DOCUMENT` row per Document** (`SearchSourceKind.DOCUMENT`,
`child_indexing.document_values`).

* **What the row holds.** The title goes in the title tier. Every filename the
  document's versions were stored under goes in `identifiers`, with the current
  one first. It holds no storage key, no id and no content.
* **How it stays current.**
  * It is written on `Document` save (created, renamed, moved, current version
    changed) and on creating a `DocumentVersion`, so a version stored without
    becoming current still adds its name.
  * The row goes with the Document (FK CASCADE), and a rebuild reconstructs it
    from canonical data.
* **Authorization.** Reading it is authorized exactly like a fragment, through
  `document__visibility_override` and the Matter. A restricted document's name
  is therefore never shown to a reader outside it (tested for NORMAL/NORMAL,
  RESTRICTED document on a NORMAL Matter, and NORMAL document on a RESTRICTED
  Matter).
* **Searching and opening it.** The existing document-title tier searches it,
  and a result opens the document's own page, as a fragment result does. No new
  UI.
* **Integrity.** `check_search_integrity` counts it (`Dokumendid`) and compares
  its Matter with the Document's.

**Plain text is plain text** (`app/core/richtext.plain_text`).

* An HTML parser keeps only visible text nodes and decodes entities once.
* Block boundaries become spaces.
* Script and style contents are dropped.
* The result is a plain string that is never marked safe. Template autoescaping
  stays the only escaping layer, so `&lt;script&gt;` in the source renders as
  the visible text `<script>`.
* `html_body_to_text` for HTML mail uses the same helper.

**The projection is bounded; the record is not.** Every authored body is cut to
`MAX_INDEXED_FRAGMENT_CHARACTERS` (200,000 characters) before its vector is
built: Matter summaries, Entry bodies, Märge notes, Kaasamine notes, a sent
opinion's summary and notes, fragments and historical pages. The same bound
applies to the filenames on a document row.

* The record keeps every character, and the leading text stays searchable.
* No form gains a `max_length`: a limit on what a lawyer may write would hide
  the defect rather than fix it.

**`INDEX_VERSION` moves once, from `TEEMA.1` to `DOKUMENT.1`,** for all three
changes together.

* **Why it must move.** Rows built by the old indexer lack every unextracted
  document's name and hold `&amp;` where the current code writes `&`.
* **Effect.** As with every previous bump, those rows stop being read the
  moment the code is deployed. Search returns too little until the one-time
  `rebuild_search_index`, and never returns something it should not.
* **The migration.** `search/0010_document_search_source` changes Python
  metadata only (the column default and the choices); `sqlmigrate` prints
  `(no-op)` for both. `SearchDocument` already had the `document` foreign key.

## Consequences

* **Production search rebuild required** after deploying this release. Not
  performed by this change.
* **HTML-only e-mail derivatives** extracted before this release keep their
  fused text in `DocumentTextFragment`. A forced re-extraction of those
  versions (operator-only) rewrites them; it is optional, and nothing is lost
  without it.
* **Performance.**
  * The document-title tier now also reads `DOCUMENT` rows through its existing
    unindexed `ILIKE`: one row per document, on top of one per fragment. The
    measured cost is in the round's report.
  * The global query's architecture is ENG-010's, and is not changed here.
