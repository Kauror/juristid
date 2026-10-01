# 0129 — An opinion keeps its working documents, and a file says which record it belongs to

**Status:** accepted
**Date:** 2026-10-01

JUR-CASE-06 and JUR-CASE-12 from the Juristieksami living-dossier QA, built on
one relation. **Two migrations** (`documents/0013`, a nullable column and the
link table's constraints; `documents/0014`, a carry-over of the dormant column
that writes nothing on an empty one). No new model, no second file store, no
audit migration, and no change to what a send stands on.

1. **What went out stays what went out.** A sent `Koja arvamus` is a
   `Submission` whose `final_version` is the exact immutable bytes that were
   sent — the signed `.asice` or whatever left the office (docs/adr/0125). The
   `SENT ⇒ sent_at + final_version` `CHECK` and the evidence triggers are
   untouched.
2. **An opinion may also keep zero or more working documents.** The editable
   DOCX it was drafted in — the file a lawyer reuses, edits and searches next
   year — is an ordinary `Document` + `DocumentVersion` with the role
   `Töödokument`, tied to that exact `Submission` by a `DocumentLink`.
3. **`DocumentLink.submission` is the canonical association.** The eighth typed
   target column, with the exactly-one `CHECK` and the one-link-per-pair
   uniqueness generated from `TARGET_FIELDS` like the seven before it.
   `Submission.working_document` stays dormant (§3 below).
4. **Working documents use the normal lifecycle.** `Lisa uus versioon`, the
   version history, the document page, search and `Eemalda` are the ones every
   file has; there is no opinion-specific versioning.
5. **A working document never satisfies the sent-evidence rule.** It cannot be
   bound as a send's evidence, and a send's evidence cannot be filed as a
   working document.
6. **Dokumendid shows where a file came from**, from the explicit links and
   nothing else, through both ends' visibility (`Seotud kirje`).
7. **No second document store**, no `OpinionDraft`, no
   `SubmissionWorkingDocument`, no draft-opinion state.

---

## Context

A real Chamber opinion has two kinds of file. The **sent evidence** is the
exact signed artefact (`koda_opinion.asice`), and since 0125 it is accepted at
the door. The **working file** is the DOCX the letter was written in — «16 03
2023 arvamus seoses juristieksami seaduse eelnõuga.docx» — and it is what a
lawyer actually reopens. Until now the evidence belonged to the opinion and the
DOCX had to be uploaded separately through `Lae dokument` as a generic
`Töödokument`, where nothing tied it to the letter it became (JUR-CASE-06).

The same QA found the Dokumendid list unable to say which record a file
belonged to — «which of the two consultations was this reply to» — although
every file captured on a Teema panel has had an explicit `DocumentLink` to its
record since docs/adr/0075 §6. The relation was stored and not read
(JUR-CASE-12).

Both are the document layer reading and writing **explicit** relations, so they
ship together; the batch file-role picker and bulk `Muuda liiki`
(JUR-CASE-11) are a separate decision and are **not** part of this record.

---

## Decision

### §1 The sent evidence is unchanged

`Submission.final_version` (PROTECT, pinned to one `DocumentVersion`) remains
the single answer to «what did Koda send». `register_sent_opinion`,
`select_final_evidence`, `attach_final_evidence`, `mark_submission_sent` and
`check_evidence_is_usable` keep every rule they had. The `Saadetud fail` of
`+ Koja arvamus` is still exactly one file and goes through the same
`read_upload` door (0125's signed-container check included).

### §2 Working documents are ordinary documents linked to the send

`+ Koja arvamus` gains an optional, multi-file **`Töödokumendid`** box beside
`Saadetud fail`. Each file becomes a `Document` + `DocumentVersion`, is filed
as `DocumentRole.WORKING_DOCUMENT` («Töödokument») and gets a `DocumentLink`
with `submission` set to the opinion being recorded.

**The role is not inferred.** 0075 §6 keeps a panel's files `OTHER` because
«the button a file arrived through is not a business role». Here the *input* is
the classification: a person put the file in a box that says what it is, which
is the same reasoning that gives `Väline seisukoht` its `EXTERNAL_POSITION`
files. Every other panel — `+ Märge`, `Kaasamine`, `Lõpeta kaasamine`,
`Uus teema` — keeps its role behaviour exactly as it was.

**Naming.** The box, the row group and the button use the role's own word,
`Töödokument`, so the opinion and the Dokumendid `Roll` column say one thing.
Not «mustand», which already means the private note and suggests a draft
opinion.

### §3 `Submission.working_document` stays dormant, and is not the mechanism

The column has existed since the foundational schema: one nullable `SET_NULL`
pointer at a single `Document`. Nothing in the product writes or reads it — no
form, service, importer or admin — and it sits outside `DocumentLink.visible_to`,
the integrity checks, the chronology's linked-file read and the removal
machinery, all of which would have to be duplicated for it. It holds one file
where an opinion has several. So it is **not reused**.

It is **not dropped** either: removing a column is a schema-cleanup decision for
its own change. `documents/0014` carries over any pointer a row does hold — set
by hand, in a shell — as a `DocumentLink`, under the relation's own rules (same
Matter only; never an opinion's own letter; no invented author). Deterministic
and idempotent, and it writes nothing where the column is empty. Going forward
`DocumentLink.submission` is the only relation.

### §4 Visibility: both ends, and the working file inherits the restriction

`DocumentLink.visible_to` is the conjunction of the document's and the record's
visibility; the `submission` column joins that conjunction through
`Submission.visible_to`'s clause, so a link to a restricted opinion is invisible
to a reader who may not see the opinion. The removal clause is now asked only
of the target columns whose model *is* removable — read off the models, so a
`Submission`, which is withdrawn and never taken off the file, neither raises a
`FieldError` nor is silently filtered.

A working document is created **with the opinion's `visibility_override`**, so
an opinion restricted below its Matter has working documents restricted with it:
they cannot be listed, downloaded or found in search by somebody who may not see
the letter. The Dokumendid tab count, search and every list read
`Document.visible_to` as before; nothing new is counted.

### §5 A working document is never evidence, in either direction

* `link_document_to_record` refuses to file a document as a working document
  when it is an opinion's letter — the role `KODA_SUBMISSION_FINAL`, or the
  `final_version` of any Submission.
* `check_evidence_is_usable` refuses a version whose document is linked as a
  Submission's working document. Every binding — attach, select and the send's
  own re-check — goes through it, so a working DOCX cannot satisfy
  evidence-before-SENT.

### §6 One press, one transaction, one row

`add_matter_koda_opinion` reads the sent file **and every working document**
before anything is written; a refused DOCX refuses the whole save — no
`Submission`, no letter, no completed step (0126 §2) and no orphaned evidence
bytes (ENG-086). Then one transaction and one `composer_operation`: the letter,
the send, the working documents and their links, and the step completion when
ticked. The chronology keeps **one** «Arvamus välja» row; a working document's
`EVIDENCE_VERSION_ADDED` is not a «lisas dokumendi» row of its own, because its
document is linked to a record that draws a row (`_versions_shown_on_their_record`,
0092 §5).

### §7 `+ Lisa töödokument` on a sent opinion

The opinion row in `Teema käik` gains `+ Lisa töödokument` beside `Muuda`: a
picker in the row, the shape `+ Lisa fail` has on a `Märge`. It adds working
documents to **that** Submission and touches nothing else — not the letter, the
send date, the addressees, the `Kokkuvõte` or the status — and writes no second
opinion and no chronology row. Allowed on any letter that went out
(`historically_sent`: sent, or withdrawn or superseded since); refused on a
draft. **Open Teema only**, under the Matter's lock (0076 §2): unlike `Muuda`,
which corrects what the file wrote down, this adds new content. Audit is the
existing `DOCUMENT_CREATED` + `EVIDENCE_VERSION_ADDED` under one operation, with
the relation itself on the `DocumentLink` row (`created_by`, `created_at`); no
new event type.

### §8 The opinion row says which file went out

On an opinion row that has working documents the files read in two labelled
groups, **`Saadetud`** (the `final_version`) and **`Töödokumendid`** (the
links). An opinion without working documents renders exactly as before. Links
respect authorization: the letter through `Document.visible_to`, the working
documents through `DocumentLink.visible_to`.

### §9 The rail tells opinions apart

`Koja arvamus` in the rail lists the same files as before, and each one the
reader may see as a send's letter now carries that send's day and addressees —
«8.9.2026 · Justiits- ja Digiministeerium» — plus «tagasi võetud» or «asendatud»
where it no longer stands (UQ-28). Its working documents follow as quiet links
named `Töödokument`. Three queries when the Matter has an opinion, none when it
has not. The rail does not become a file list: no sizes, versions or badges.

### §10 `Seotud kirje` on Dokumendid (JUR-CASE-12)

Each row of the evidence table may carry a sub-line naming the record the file
came in with — «Koja arvamus · 26.9.2026 · Justiits- ja Digiministeerium»,
«Kaasamine · 17.9.2026 · liikmed», «Märge · 15.9.2026 · …», «Meile saadetud
tagasiside · … · Metallitööstuse Liit». Rules:

* **Explicit links only.** Read from `DocumentLink`; never from an upload
  minute, an `operation_id`, a filename, a `NextAction` or row order (0092 §6).
  A file without a link — every plain upload, every `Uus teema` batch — prints
  nothing.
* **Both ends visible or nothing**, through `DocumentLink.visible_to`; a record
  taken off the file prints nothing (0102).
* **Deterministic and compact**: ordered by when the link was written, identical
  lines printed once, a record's own words cut at 70 characters.
* **The upload date and the order are not touched.** `Kuupäev` stays the upload
  day and the list stays newest-upload-first; the record's date is printed in
  the line, never used to move a row. No backdating, no historical
  reconstruction.
* An opinion's letter keeps its existing «Saadetud <day> · <addressee>» line
  (it is tied by `final_version`, not by a link); its working document reads
  «Koja arvamus · <day> · <addressee>». The two identify the same letter while
  their `Roll` — `Arvamus`, `Töödokument` — keeps them different files.

One query per page, plus one for addressees when an opinion's working document
is on it.

### §11 «SharePointi viited»

The Dokumendid accordion that lists SharePoint links was headed `Töödokumendid`
and read «Viiteid ei ole» directly under a table row whose `Roll` is
`Töödokument` — a page contradicting itself the moment §2 shipped. It is now
headed **`SharePointi viited`** (and its empty state says so). Nothing else about
it changes: same links, same form, same anchor id. The wider document-role
vocabulary is JUR-CASE-11's.

---

## Consequences

* A Koja arvamus can carry its signed letter and its editable source together,
  and the source is found under the letter, in Dokumendid with its context, in
  the rail, and in search — scoped exactly like the letter.
* Older opinions and Matters with no working documents read exactly as before
  in `Teema käik`; the rail's opinion lines gain the send's day and addressees.
* `DocumentLink` now has a target whose model is not removable; the removal
  clause is derived from the models instead of assuming every target is.
* `Submission.working_document` remains in the schema, dormant, for a later
  cleanup change that drops it.

## Not decided here

* **JUR-CASE-11** — a batch `Failide liik` picker on the Teema panels, bulk
  `Muuda liiki`, and any role inference: untouched. `+ Märge`, `Kaasamine` and
  `Uus teema` keep their role behaviour.
* Linking a document **already on the Matter** to an opinion as its working
  document (the panel and the row take new uploads only).
* `E-kirja manus` provenance on Dokumendid: `EmailAttachmentLink` is explicit
  too, but it is not a `DocumentLink` and is left for a later round.
* Dropping `Submission.working_document`.
