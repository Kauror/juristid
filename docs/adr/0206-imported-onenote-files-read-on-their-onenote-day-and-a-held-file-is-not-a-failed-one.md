# 0206 — Imported OneNote files read on their OneNote day, and a held file is not a failed one

Status: accepted (owner brief «Phase 2 follow-up: post-import protection and UI
corrections», 2026-11-05; the release itself waits for the owner's review)
Date: 2026-11-05
Amends [0129](0129-an-opinion-keeps-its-working-documents-and-a-file-says-which-record-it-belongs-to.md) §10
for files an import brought; applies
[0092](0092-substantive-matter-history-and-evidence-safe-process-timeline.md) §4 and §6 (amendment of
2026-10-20) to Dokumendid; builds on
[0205](0205-a-sealed-onenote-desktop-capture-enriches-the-lovable-teemad.md).

## Context

The Phase 2 import (0205) put 4,243 OneNote files on 328 Teemad and held 5,539
shared-page decisions for review. The UI acceptance test found the data right
and three screens misleading:

* **ONENOTE-UI-003.** The OneNote page view called every recorded, non-imported,
  non-empty file «Kopeerimine ebaõnnestus». That included every
  `SKIPPED · SHARED_PAGE_AWAITING_REVIEW` row, so a lawyer opening a shared page
  read 145 files of evidence as lost.
* **ONENOTE-UI-001.** Dokumendid printed and ordered every file by
  `Document.created_at`, so all 4,243 read 5.11.2026 (the import) and the year
  filter offered only 2026. The day the file reached OneNote was in Tegevused
  and inside the collapsed «Dokumendi andmed» only. 0129 §10 had fixed `Kuupäev`
  as the upload day for files a person uploads. Nobody had decided it for files
  an import brought.
* **ONENOTE-UI-002.** «Allikas» said «Lehe sisu ja manuseid ei ole Juristidesse
  imporditud» under the very files it had imported.

## Decision

### 1. One state per file, from the import's own row

`historical_views._file_state` reads the `LegacySourceResourceImport` row of
the (Teema, file) pair. A `SKIPPED` row is a deliberate «not now». It reads as
what it was held for, and never as a failure:

| Row | State | The page prints |
| --- | --- | --- |
| none | `pending` | «Kopeerimisel» |
| has a Document | `imported` | the file, its download and «Päritolu» |
| 0 bytes in OneNote, or `EMPTY_FILE` | `empty` | «OneNote'is oli see fail tühi» |
| `SKIPPED · SHARED_PAGE_AWAITING_REVIEW` | `held_shared` | «Ootab teemaga seostamist», with the reason and that the original is kept |
| `SKIPPED · LARGER_THAN_EVIDENCE_LIMIT` | `held_too_large` | «Suurem kui tõendi ülempiir» |
| `SKIPPED · RECORD_HELD_FOR_REVIEW` | `held_review` | «Ootab ülevaatust» |
| `SKIPPED · EARLIER_CAPTURE_NOT_IMPORTED` | `superseded` | «Varasema hõive kirje» |
| `SKIPPED`, any other code | `not_imported` | «Ei imporditud» |
| `FAILED`, not empty | `unavailable` | «Kopeerimine ebaõnnestus» (the only problem chip) |

A Document the reader may not read keeps its place, with nothing about it
named (ENG-047, unchanged). Nothing here promotes, assigns or changes a row.
The shared-page review stays a person's decision (D4).

### 2. The page's and the Teema's status come from the same rows

`import_statuses` counts those states over each page's file blocks as the page
is now. `superseded` rows are left out, because they are not files on the
current page. This gives each «Ajalooline OneNote» row a chip:
«Kõik 27 faili imporditud», «145 faili ootab teemaga seostamist», «Lehel ei
ole faile», or a breakdown when a page is partial or failed.
`teema_import_status` writes the «Allikas» sentence from the same counts.

The old sentence is said only where no OneNote page is linked, which is still
true there. A page row's existence is never read as an import. With no rows,
the files read as «on veel importimata».

### 3. Dokumendid: each row prints and sorts by one date, and says which

`matters.views.with_list_dates` gives each row a `list_at`:

* For **a file an import brought** (an `IMPORTED` row names the Document), it is
  `LegacySourceResource.source_added_at`. That is the outline element's
  creation time, the day the file reached OneNote (0092 §6). The cell prints
  the Tallinn day and «lisatud OneNote'i». It is never the import moment, the
  copy time (`acquired_at`), the block's later modification or the object's
  own modification time (D8).
* For **a floating file** with no such time, `list_at` is empty. The cell reads
  «Lisamise kuupäev teadmata»; no day is put in its place (0092 §4).
* When **the import rows naming one file disagree** on its addition time, the
  file has no `list_at`. It reads «Lisamise kuupäev teadmata (allikad on
  vastuolus)» and is filtered under «Kuupäev teadmata». No MIN or MAX is chosen
  (`onenote_date_conflict`).
* For **every other file**, it is `created_at`, the upload day, exactly as 0129
  §10 has it.

The list orders by `list_at`, newest first, with ties broken by upload. The
undated imported files come after every dated row: they claim nothing, so they
are not interleaved with days. This is unlike the chronology, where an unknown
row sits at its record time (0092 §4). The chronology needs every row placed on
one line of time. A file list does not, and putting undated files last keeps
the list free of any implied date.

So the order follows exactly the date each row prints, and the cell names its
kind. Two kinds of date in one column are stated once, above the table, only
where the list holds an imported file. A Teema of uploads only has no such
note. Its dates, its order and its year filter are what they were.

The year filter reads the year of `list_at` in Europe/Tallinn (a file added on
31.12.2020 at 22:30 UTC is 2021). It adds «Kuupäev teadmata» (`aasta=teadmata`)
when undated imported files exist. 0129 §10's «no backdating, no historical
reconstruction» still holds: nothing stored moves. The row prints a source
fact under its name, as Tegevused already does.

### 4. The document page states the OneNote day in its first card

«Originaal» shows «Lisatud OneNote'i: <day time>» (or «Lisamise kuupäev
teadmata») next to «Saadi … · kopeeriti OneNote'ist». So when the file reached
OneNote and when these bytes were copied out are two labelled facts. The
block's later modification stays in «Päritolu», as metadata only.

### 5. `onenote_capture_import verify` runs in bounded memory

Verify joined each import row to its whole source page and kept every row in
the queryset cache. Each row therefore carried the page's text, blocks and
links, plus the whole Document. The production run over 9,783 decisions was
killed at the kit's 2 GB limit.

It now reads each page key once and selects only the columns it checks
(`only()`). It streams the rows (`iterator(chunk_size=500)`) and hashes stored
files in 1 MiB chunks. Every relationship it checked is still checked, and the
tests pin both.

## Consequences

* The OneNote page view, «Ajalooline OneNote», «Allikas», Dokumendid and the
  document page agree with each other and with Tegevused. Each date is
  labelled.
* An ordinary upload is unchanged everywhere. On a Teema that holds both kinds,
  an upload sorts by its upload day among the OneNote days. Each cell says
  which day it shows.
* No schema change, no migration, no data change. Two `Exists`/`Subquery`
  annotations per Dokumendid page.

## Not decided here

* **The shared-page review workflow** (D4): who assigns a held file to which
  Teema, and how. Until it exists the files stay held, sealed and visible as
  waiting.
* **Searching the provenance note** (ONENOTE-UI-004).
