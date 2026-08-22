# 0023 — Making the whole opinions archive searchable evidence

- **Status:** Accepted — implemented on the `stage-2h2-complete-opinion-archive` branch, pending integration
- **Date:** 2026-08-22
- **Builds on** ADR 0019 (opinion-archive reconciliation), ADR 0014 (storage classes and the extraction trust boundary), ADR 0022 (backup and recovery).

## Context

ADR 0019 reconstructed canonical `Submission` records from the opinions archive
and did it carefully: 244 of 767 files cleared the automatic threshold, and the
other 523 stayed in an operator queue carrying their evidence rather than being
filed on a guess.

That was the right threshold and it left a real problem behind. **The archive
existed only as a catalogue.** The bytes stayed in a 105 MB ZIP nobody could
open from the application, the letters could not be read, and two thirds of the
Chamber's outgoing correspondence from 2020 onward was findable by nothing at
all. A migration that ends with "the evidence exists, somewhere, and you cannot
look at it" has moved the problem rather than solved it.

Stage 2H.2 is about the other two thirds. Not filing them — that bar has not
moved and does not move here — but **holding them, reading them, and finding
them**.

## Decision

### The archive keeps its bytes in the evidence store, under its own rows

`OpinionArchiveBinary` is one row per distinct file, keyed by SHA-256, holding a
storage key in the evidence class. Occurrences point at it: two archive paths
containing identical bytes are one piece of evidence found twice, which is what
the corpus actually contains.

Materialisation is a separate command from `apply`, and deliberately so. Holding
a letter's bytes and deciding whose letter it is are different acts with
different bars, and tying them together is what left two thirds of the corpus
visible only as catalogue rows. `opinion_archive materialize` creates no
`Submission` and links no Matter.

**Consequence for the pruner.** "Referenced" used to mean "a `DocumentVersion`
points at it", written out separately in the integrity checker and in the
deleter. The archive now puts canonical bytes in the same store, so the
knowledge moved into `app/documents/references.py` and both callers read it
there. Without that change the first `prune_orphaned_evidence --delete` would
have deleted the entire archive as orphaned. The restore fingerprint walks the
same list, so a lost archive fails a restore check instead of passing one.

### `SearchDocument.matter` stays non-nullable; the archive gets its own projection

Every authorization decision in the global search rests on one property: each
row names the Matter that authorizes it, and the visibility predicate joins that
Matter live before anything is counted or ranked.

An unfiled archive letter cannot answer that question. The two ways to make it
fit were both refused:

- **a fake Matter to hold the unfiled** would put a row in the register that
  nobody opened, and every count over Matters would then include it;
- **a nullable `matter`** would remove the invariant from every *other* row at
  the same time, so the safety of the whole projection would come to rest on
  remembering to filter for a null.

So `OpinionArchiveSearchDocument` is a separate table: derived, rebuildable,
never consulted for a business decision, and declared rebuildable in
`REBUILDABLE_MODELS` so a restore may leave it empty. It answers a different
question from the global search — *what historical evidence do we hold,
including what is not filed* — and keeping the two apart is why neither has to
compromise.

### The archive's authorization is a property of the corpus, not of the row

There is no Matter to inherit a restriction from, so there is no per-row rule to
apply. `may_read_archive` is one predicate, used by the list, the detail page,
the header figures and the file route alike, and it is all-or-nothing: a reader
who may see the coverage numbers can infer the corpus, and a reader who may see
titles but not text can already read a letter's subject and recipient.

It is the reconciliation queue's rule — administrator, because this is migration
work — and **one step stricter**: the queue shows filenames and dates, while
this surface serves document text and the bytes themselves, so a persona behind
one shared department password is refused. An audit row naming a persona is not
a record of who read real correspondence.

### Extraction obeys ADR 0014 rather than carving an exception out of it

The archive would be far more useful with its bodies searchable. That is not by
itself a reason to open 767 unscanned real PDFs with a parser.

Where `REAL_DATA_ALLOWED` is on, every binary is recorded `BLOCKED` — not
`FAILED`, not silently left `PENDING`, and above all not extracted. Where it is
off, extraction runs, so the pipeline that will one day process the real archive
is exercised end to end instead of written and never executed.

**The archive is fully searchable by metadata either way**, which is what makes
obeying the rule affordable. Title, recipient, date, every occurrence path, the
SHA and the register reference are all indexed without opening a single file.

Native PDF text only; no OCR. A scanned letter with no text layer is recorded as
`NO_TEXT_LAYER`, which is a fact about the corpus worth counting rather than a
gap worth filling at the price of running a shared OCR engine over 767 files.

### A retired proposal says so, rather than disappearing or lying

A candidate's identity is `(item, matter, match_class)`, so a rerun that
reclassifies the same occurrence writes a new row and strands the old one. That
row cannot honestly be `APPLIED` — it produced nothing; must not be `REJECTED` —
nobody rejected it; and must not be deleted — it records what the reconciliation
believed at the time.

`SUPERSEDED` names it, points at what replaced it, and keeps every signal and
conflict readable. Only `PENDING` is ever retired: `APPLIED` means a Submission
stands on the row, and the five human states mean somebody looked and answered.
Overwriting either would make the queue's history a function of how many times
the importer happened to run.

### One letter may concern several Matters, and a link is not a Submission

`OpinionArchiveMatterLink` says *this evidence concerns this Matter*. It does not
say the Chamber sent this opinion to that ministry on that date; only a
canonical `Submission` says that, only the apply path creates one, and nothing
in the link surface ever will. The form says as much in the form.

Links never create a Matter, and a link a `Submission` stands on cannot be
withdrawn from the archive screen — a reviewer may undo their own judgement, but
unmaking the record of a filed opinion is a different act with a different bar.

### The second pass reads the letters, and files nothing

The first pass had a filename, a register row and a byte-identical OneNote copy.
For hundreds of letters the filename was all it had, and a filename is what
somebody typed when saving a copy. The letter's own text is a fourth source and
an independent one: its dateline is what Koda wrote.

Three new signals compare the letter's text to a register row by equality —
`CONTENT_EXACT_DATE`, `CONTENT_EXACT_LAW_REFERENCE`, `CONTENT_EXACT_ADDRESSEE` —
named separately from their filename equivalents so that one source cannot
corroborate itself. Two are required before anything is proposed. Nothing
computes a distance, a similarity or a confidence, and two corroborated rows is
a conflict for a person rather than a tie for the importer to break: there is no
comparison between rows anywhere in the module.

**`CONTENT_MULTI_SIGNAL` is not an automatic class.** The bar for adding one was
stated in ADR 0019 as measured precision on the real corpus, and this pass has
never run against it — extraction is blocked wherever the real archive lives, so
its precision is not merely unproven but unmeasured. Promoting it now would be
exactly the move every existing class was written to avoid.

## What would justify promoting the second pass

Recorded here so the next person has a test rather than a judgement call:

1. extraction runs against the real archive — which requires the Secure Pilot
   Gate malware scanner, not a change to this code;
2. the pass proposes against the real register, and a reviewer works the
   resulting queue to completion;
3. the confirmed rate for proposals carrying all three content signals is
   measured. If it is at or near the rate the existing automatic classes were
   accepted on, promotion is a one-line change to `AUTOMATIC_MATCH_CLASSES` with
   a number behind it.

Until then the queue is the answer, and the queue is not a hardship: these
proposals arrive with their signals named and their register row linked.

## Measured against the real sources, read-only

Both sources were read on 2026-08-22 without being modified, moved or copied,
and the register workbook's SHA-256 was checked against the approved snapshot
before a cell was read. **No PDF was opened and no body was parsed**: the
extraction rule is a property of the deployment, and a developer machine with
the flag turned off is not the place to decide that real correspondence may go
through a parser. Row-level output stayed under the ignored `.local/`.

Aggregates only, and each one changed or confirmed something above.

**The archive snapshot holds 759 letters, every one a distinct SHA-256, and no
letter appears at two paths within it.** The binary/occurrence split therefore
costs nothing today and its duplicate-collapsing benefit is, inside one
snapshot, currently hypothetical. It is not hypothetical across snapshots: the
second archive on the same host holds 34 letters and **all 34 are byte-identical
to letters already in the main one**. Materialising it reuses 34 binaries and
writes no bytes, where a path-keyed design would have stored 34 more copies of
real correspondence.

**271 of 759 entries carry UTF-8 names without the UTF-8 flag** — the
mis-decoding case the reader already handles, and a larger share of this
snapshot than the 91 measured for ADR 0019. Worth knowing before anybody
concludes the encoding problem is marginal.

**The register's modern half is 1373 rows, of which 1357 have a dispatch date
and 1370 a counterparty.** For the second pass that is the good news: 1280 of
those counterparties are at least eight characters, so the addressee signal has
reach across nearly the whole register.

**Only 161 of the 1373 titles — 11.7% — name a Riigikogu proceeding number.**
That is the ceiling on `CONTENT_EXACT_LAW_REFERENCE`, and it is low. For the
other 88% the second pass has at most date and addressee to work with, which is
two signals and therefore a proposal — and precisely the combination ADR 0019
measured as *not* identity, since two matters can share a day and a ministry.
It is another reason `CONTENT_MULTI_SIGNAL` waits for a person rather than
filing itself.

## Consequences

- The whole archive is reachable and searchable for an administrator, including
  the two thirds nothing has been filed from.
- The evidence store now has two canonical holders, and everything that reasons
  about orphans, integrity or restores reads one list.
- Extracted text exists in environments that permit extraction and nowhere else,
  so the search's body coverage is an environment property and the coverage
  strip says so on the page.
- Nothing about the `Submission` threshold changed. The corpus is more
  findable, not more filed.
