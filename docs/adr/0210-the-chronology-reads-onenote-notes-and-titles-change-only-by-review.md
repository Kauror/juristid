# 0210 — The chronology reads the OneNote page's own notes; document titles change only by review

Status: accepted (owner brief «OneNote Timeline and Document Titles», 2026-11-05)
Date: 2026-11-05
Builds on [0092](0092-substantive-matter-history-and-evidence-safe-process-timeline.md)
§6 (imported files on their OneNote day), [0205](0205-a-sealed-onenote-desktop-capture-enriches-the-lovable-teemad.md)
and [0206](0206-imported-onenote-files-read-on-their-onenote-day-and-a-held-file-is-not-a-failed-one.md)
(the capture and its dates), and owner decision D9 of 2026-11-04 (an adjacent comment is page text).

## Context

Imported OneNote material reached «Tegevused» only as what the importer did:
«OneNote'i lisati 4 dokumenti». What happened is on the page itself, in dated
notes such as «27.07.2026 – KLIM saatis kooskõlastusringile eelnõu …». The same
import set every `Document.title` to its attachment's filename, so 1,580 of
4,243 titles are generic («Seletuskiri.pdf», «SK.pdf», «EELNÕU.pdf»).

Two facts from the sealed capture constrain both answers. First, a
«Märkus OneNote'is» nested under a file is, in list layouts, usually the label
of the *next* file: 64 of 112 such files are followed directly by another
file. Second, most `CONTAINER_HEADING`s are shared category headings
(«Arvamused:»). Neither can tie a file to a note or give it a title.

## Decision

1. **Historical notes are a read-only projection**
   (`app/legacy_import/historical_notes.py`). For each `MatterSourcePage` the
   reader may open (`visible_links`), the page's stored blocks are segmented
   deterministically by the rules in
   `_worklogs/onenote-timeline-titles/agent-a/rules.md` (R0–R12, mirrored in
   the parser's tests):
   * **Where a note starts.** Only at a text block that *begins* with an
     explicit date and a separator. A short heading that ends in a full date
     also starts one, behind the `TRAILING_DATES` switch.
   * **What it holds.** It runs over its sibling lines to an empty or rule line,
     or to the next start, and holds everything nested under it. It never
     crosses outlines.
   * **What it shows.** The text verbatim, minus only the leading date and its
     separator.
   * **Its date.** DAY, MONTH or YEAR precision, placed like a dated milestone
     (`_end_of_day` of the day, or of the period's first day).
   * **What is never an event date.** A date inside prose; a day and month
     without a year; a date more than 45 days after its block was written; any
     block, capture or import time.
   * **What gets no row.** Undated text and «Järgmised tegevused»-type sections.
     They stay on the page every row links to. A dated line inside such a
     section is a row marked «Ajalooline: Järgmised tegevused», and it is never
     a task, deadline or action.
2. **A file is direct to a note only when it is nested under the note's own
   first block.** Structural comments are shown with their file and never tie
   it to a note.
   * A direct file is listed under its note, and only direct files draw the
     note's «Dokument» / «N dokumenti» tag.
   * Every other imported file reads on its OneNote-day document row, headed by
     its files («EELNÕU.pdf ja 3 muud faili», a zero-padded day) instead of
     «OneNote'i lisati N dokumenti».
   * Every imported version is reachable exactly once. Files are matched to
     blocks by `resource_key`, never by a stored block ordinal; later-capture
     rows match only on a unique filename and addition time.
3. **The rows are native `<details>`.** The summary reads date, tag and
   headline (about 50–65 characters, cut at a word). The panel holds the full
   note, the date and its precision, the page, section and capture provenance,
   the visible links, the files, and «Sama OneNote'i lehe failid (N)». A link
   to a row opens it.
4. **Nothing is stored.** The projection writes no Entry, Submission,
   Development, action, Document or schema. Its cost is one read of the linked
   pages per chronology, the same whatever the number of pages, notes or rows.
5. **Document titles change only through review.** Proposals are graded by
   evidence:
   * A — the file's own metadata;
   * B — a structural note that names the file;
   * C — a contextual description;
   * D — none.

   They are kept as a private dataset, never in the repository.
   `apply_document_title_proposals` applies a reviewed plan:
   * it is a dry run by default;
   * `--apply` requires `--expect-plan-sha256`;
   * it applies only APPROVE rows that carry an explicit title;
   * it requires a named, active actor who may write;
   * each change goes through `rename_document`, with the same refusals and one
     `DOCUMENT_TITLE_CHANGED` event per rename;
   * it refuses a row whose current title no longer matches the plan;
   * it is idempotent.

   It changes `Document.title` only: never `DocumentVersion.original_filename`,
   bytes, checksums or versions. No plan has been applied; that is a separate
   owner decision.

## Consequences

Reversible by reverting the code. Nothing to migrate back. Every Teema pays one
more query, and a page with many blocks adds Python parsing time on render.
