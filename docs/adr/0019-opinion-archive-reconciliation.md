# 0019 — Reconstructing historical submissions from the opinions archive

- **Status:** Accepted — implemented on the `stage-2h-opinions-archive` branch, pending integration
- **Date:** 2026-08-21
- **Builds on** ADR 0012 (register import), ADR 0015 (historical corpus), ADR 0017 (metrics).

## Context

Stage 2A imported the register and deliberately did **not** create a `Submission`
from a `VÄLJA` date: a sent opinion with no text is an unverifiable claim about
what Koda argued, and the database refuses to hold one. Stage 2D imported the
OneNote corpus, which carries a SHA-256 for every attached file. What was
missing was the third thing — the letters themselves.

The Chamber's opinions archive supplies them. Before any of the design below was
written, the four sources were measured against each other, and the measurements
are the reason for almost every rule that follows.

**The archive is 767 PDFs, all distinct, spanning 2020–2026 only.** The register
begins in 2011. Whatever the archive can support, it cannot support a pre-2020
number, and the difference between "no opinions" and "no evidence of opinions"
is the whole point of the stage.

**Byte identity through OneNote is the strongest edge and the rarest.** Ten of
767 files exist byte-for-byte in the OneNote corpus, and exactly one of those
sits on a page the Stage-2D audit tied to a single register reference. The
archive's PDFs are re-exports, not the attachments. The route stays first
because when it fires it is identity; it is simply not the route that carries
the corpus.

**The register's `VÄLJA` is a dispatch date; the filename's date is not.** The
two agree on the same day 326 times and are one day apart 227 times. The
archive's filename date is the letter's own date.

**Two exact signals are not identity.** Exact date plus exact addressee picks a
unique register row 291 times — and the corpus contains a file about hazardous
waste whose date and ministry match a register row about the Green Deal.

**A producer's spreadsheet already recorded the hashes.** The KodaDash workbook
binds 759 of 759 of its rows to archive files by SHA-256. The same rows matched
by (encoding-tolerant) filename produced three collisions and five wrong
assignments — on data where the exact answer was sitting in the next column.

## Decision

### Evidence classes, not a score

Matching produces a named class and a list of individual signals and conflicts.
There is no similarity threshold, no edit distance and no model. Three classes
may be applied without a person — exact binary resolving to one Matter, an
Excel/OneNote exact page identity, and three independent exact signals — and
everything else waits in an operator queue carrying its evidence.

A score would have been shorter to write and impossible to argue with. "0.87"
is not an answer to *which ministry did we actually write to*.

### The Submission threshold does not move

A canonical `SENT` record still requires a unique Matter, a defensible sent date
and the exact final binary, and the existing database constraint still enforces
the last two. What Stage 2H changes is that the missing evidence now exists for
part of the corpus — not that the bar was lowered for the rest. Incomplete
reconstruction becomes an `OpinionMatchCandidate`, never a `DRAFT` submission
that would look like live work.

Measured against the real sources: **244 of 767** files clear the threshold. The
other 523 keep their evidence and wait.

### One sent action, however many files and copies

Three distinct rules, all learned from the corpus:

- The same bytes at two archive paths are **two occurrences and one binary**,
  and both facts are stored.
- The same bytes already on the Matter — from the OneNote materialisation, or
  from an email attachment — are reused as the final evidence rather than
  stored again.
- Several *different* files on one Matter on one day are **one letter with
  annexes**, not several submissions. `2025_44` is a letter plus its `Lisa 1`;
  `2024_139` is four earlier letters resent together. Filing those separately
  would have overstated the department's output by the number of attachments it
  happened to send. Which file is the letter is a judgement, so the group goes
  to review.

### Derived metadata is stored, and stored as derived

KodaDash's summaries, positions, topic labels and normalised recipient buckets
are preserved under their producer's name, beside their producer's hash, on
`OpinionArchiveMetadata`. None of it is written to `Matter.position_summary`,
`Matter.tags` or `PolicyArea`: a public-app summary is not a lawyer.

Its normalised recipient is explicitly *not* an identity. The producer folds
`Keskkonnaministeerium` into `Kliimaministeerium` in 52 rows and collapses
comma-separated pairs to their first name. That is a defensible cross-era
analytics bucket and an indefensible historical fact, so the raw and the
normalised values live in different columns and only the raw one is resolved
against `Organisation` — by exact identity or a reviewed alias, never by
similarity, and never by creating one.

Its public gate is provenance, not an archival gate. A row the membership app
hid is still a letter the Chamber sent.

### Date-only history says so

`Submission.sent_at` stays a `DateTimeField` so that natively captured
timestamps keep their precision. A reconstructed record carries
`sent_at_precision = DATE` and a local-midnight anchor that the UI never
renders. Local rather than UTC midnight, so the date a query groups by is the
date the register wrote.

### The queue records; the importer executes

A reviewer marks the Matter and, separately, whether the evidence supports
calling the letter sent. The next `opinion_archive apply` — the process that
actually holds the archive — writes the bytes and creates the record, with the
date basis stored as `REVIEWED_DECISION` so a person's judgement never reads as
a register value.

## Consequences

- Historical submission statistics exist for the first time, and the six
  affected metric definitions moved to version 2 with an era note that states
  real coverage instead of denying measurement.
- Roughly two thirds of the archive is visible, catalogued, searchable as
  evidence and **not counted**. That gap is reported per year under
  Andmekvaliteet rather than averaged into a single reassuring percentage.
- Four files sit in `EXACT_BINARY_MULTI_MATTER` because one letter genuinely
  concerns more than one Matter. `Submission` has one primary Matter, and
  inventing a relationship model for four rows would be architecture written
  ahead of evidence. It is recorded as an open decision instead.
- Nothing here can be re-run destructively. Every write is a get-or-create
  against a stable source identity, and a plan refuses to apply if the register
  snapshot or the OneNote capture moved after it was reviewed.
