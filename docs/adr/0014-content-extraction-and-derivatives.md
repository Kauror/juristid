# ADR 0014 — Content extraction: derivatives, the worker, and child-content search

- Status: accepted
- Date: 2026-08-19
- Stage: 2B
- Related: ADR 0003 (document lifecycle), ADR 0005 (authorization), ADR 0006
  (search architecture), ADR 0013 (search projection and child content)

## Context

Stage 2A left Juristid able to store a ministry PDF correctly and unable to say
what was in it. ADR 0003 named `DocumentDerivative` as Stage-2 work and left
`DocumentVersion.extraction_state` as the hook. ADR 0013 deferred Entry,
Submission and document-body search with a specific condition attached: it could
be built once child visibility could be shown to be evaluated from current
source data before ranking, counting or snippet output.

This ADR records what was built and, more usefully, the decisions that were
close.

## Decisions

### Derived content is a separate layer, and it is disposable

`DocumentDerivative` holds one parser's output for one exact `DocumentVersion`.
`DocumentTextFragment` holds that output's text in locator-sized pieces. Both
cascade from the version and both may be deleted at will:

```
DocumentVersion (evidence, immutable)
    → DocumentDerivative     (what a parser made of it)
        → DocumentTextFragment (that text, with somewhere to point)
            → SearchDocument   (that text, vectorised)
```

Each layer is rebuildable from the one below and the bottom layer is bytes.
`rebuild_document_derivatives` deletes the top three for a selected scope and
regenerates them, and a test asserts the regenerated corpus is identical —
same content hashes, same fragment count, same locators, same text. That test
is the whole justification for backing up only PostgreSQL and the evidence
directory (Stage-2B brief 68, 81).

**`EmailAttachmentLink` is deliberately not in that layer**, despite being
created by a parser. It records that one exact binary arrived inside another
exact binary — a fact about the evidence rather than an opinion about its
contents — and it cannot be recomputed once the parser that knew has been
replaced. It sits under `PROTECT` and survives a derivative rebuild.

### One active derivative per version per kind, enforced by the database

A partial unique constraint on `(version, kind) WHERE status = 'ACTIVE'`. New
output is written as `BUILDING`, the previous `ACTIVE` row is demoted to
`SUPERSEDED`, and the new one is promoted — in one transaction, in that order.
Getting the order wrong raises rather than silently leaving duplicates.

The property this buys is that **a parser upgrade cannot empty somebody's search
results**. If the new version fails on a file the old one handled, the old
representation is still `ACTIVE` and still serving. Degraded is recoverable;
empty is indistinguishable from a document that was never there.

### Parsers are pure functions of bytes

An adapter receives a `SourceFile` and returns a `ParseResult`. It does not touch
the database, does not know about `Document` or `Matter`, does not decide
extraction state and does not open a network connection. Everything that writes
lives in `orchestrator.py`.

The payoff is that "the parser raised halfway" has exactly one meaning: nothing
was written. There is one place to reason about partial success rather than nine,
and a parser can be tested against a byte string with no fixtures at all.

### Locators are what the format actually knows

* PDF → page. The format has pages.
* PPTX → slide. The format has slides.
* XLSX → sheet and row range. The format has both.
* DOCX → section, paragraph range or table, and **never a page**.
* TXT/CSV → line or row range.
* Email → headers and body.

Word paginates at render time against a specific printer and font set; the file
contains no page boundaries. An invented `lk 4` sends a lawyer to the wrong place
in a ninety-page draft, and one such experience costs more trust than the locator
ever earns. The DOCX derivative records `pagination: not-available-in-format` so
the absence reads as a decision rather than an oversight.

### OCR runs per page, and only where the page needs it

The trigger is a page whose native text layer is below
`EXTRACTION_OCR_MIN_NATIVE_CHARACTERS` — not a property of the file. The normal
government PDF is a typed covering letter with a photographed annex behind it,
and a per-file decision gets that wrong in both directions.

Where both exist, native text wins. The author's own characters are exact and a
recognition engine's are a guess with a known error rate. Every fragment records
which of the two it is, and the preview badges OCR text, because presenting a
recognition result as the document's own text is a provenance defect.

The engine is local (Tesseract, `est+eng`, shipped in the image). No document
content leaves the host. `manage.py check_ocr_runtime` asserts the engine and its
language data are present, because Tesseract asked for a language it does not
have falls back to English and returns confident nonsense — which reaches the
search index looking exactly like success.

### The queue is PostgreSQL

`SELECT … FOR UPDATE SKIP LOCKED` claims a version; the claim is a row state with
a timestamp; a claim older than `EXTRACTION_STALE_CLAIM_MINUTES` is reclaimed. A
worker that dies leaves evidence of what it was doing rather than a lock nobody
can clear, and its work is picked up without an operator running anything.

**Rejected: Celery with Redis.** At six lawyers and a few files a day it is a
second piece of infrastructure to run, back up, monitor and explain, in exchange
for capabilities none of the work needs (AGENTS.md forbids introducing either
without measured need). If throughput ever becomes the constraint, the answer is
more worker processes against the same table.

### Search: one queryset, and the child's live restriction in the join

ADR 0013's concern was that indexing children would force the projection's
authorization predicate to vary by `source_kind`, turning one scoped queryset
into a union of three — "and a union is exactly where a count stops being
trustworthy."

It does not. `SearchDocument` gains real foreign keys to `Entry`, `Submission`
and `Document`, and `projected_visibility_q` maps each kind to the field path
that reaches its live override:

```
(parent is normal AND this row's own child is normal) OR participation
```

That is `child_visibility_q` with the second half selected by kind, in one `Q`,
over one queryset, producing one count. **The projection still stores no
visibility**, at either level, and a test asserts the column does not exist.
Restricting a document takes effect on the next query with no reindex, asserted
by a test that deliberately does not rebuild.

### Fragment text is copied into the projection

`SearchDocument.body_text` duplicates `DocumentTextFragment.text`. The
alternative — computing the vector from a joined column via a subquery and
reading `ts_headline` through the join — was tried on paper and costs a subquery
per vector plus a second query per result page, in exchange for text PostgreSQL
compresses anyway. The layering still holds: evidence rebuilds fragments,
fragments rebuild this.

### Trigram indexes become partial

Stage 2A had one row per Matter, so indexing every row's title cost nothing.
Stage 2B adds a row per fragment, where the design headroom is millions. Both
trigram indexes are now `WHERE source_kind = 'MATTER'`, and the fuzzy tier in the
query is restricted to the same rows so the planner can use them. Fuzzy matching
is a short-string feature — titles, references, names — and "which page of this
annex is nearly spelled like your typo" is not a question anybody has.

### Snippets are text, not markup

`ts_headline` marks matches with two private-use bracket characters. The service
splits on them and returns a sequence of highlighted and plain runs; the template
writes the `<mark>` itself. There is no path by which output from a document
becomes markup in the page.

### Preview never renders the source format

Office files are shown as extracted text. Emails are shown as parsed fields and a
plain-text body — raw email HTML is never rendered, and script and style
*elements* are removed contents-included before sanitising, because a sanitiser
that strips tags and keeps text folds a stylesheet into the indexed body. Images
are shown only through the authorized download route, which is always
`Content-Disposition: attachment`.

The detail page keeps original and derivative visibly apart: a solid card with a
checksum and a download, and below it a section headed *Tuletatud eelvaade* that
names the parser that produced it.

### Email attachments become evidence with a row, not a sentence

Each ordinary attachment becomes its own `Document` with role
`EMAIL_ATTACHMENT` — never something more specific, because mail arrives from
ministries, members, associations and colleagues alike and guessing would file
half of them wrongly. Inline resources (signature logos, tracking pixels) are
counted in the message's metadata and do not become Documents.

An attachment's MIME type comes from its extension against the upload allowlist,
never from what the message claimed: a message's declaration is
attacker-controlled in exactly the way a browser's `Content-Type` is, and the
upload path already refuses to believe that one. Its malware-scan state starts at
`PENDING` and is never inherited — the message having been scanned says nothing
about what was inside it.

### The malware gate is explicit about the environment

`is_eligible_for_extraction` processes `CLEAN` always; processes `PENDING` only
when `REAL_DATA_ALLOWED` is off. In a real-data environment an unscanned file is
simply not processed, and the scanner that would change that is a Secure Pilot
Gate deliverable. Nothing anywhere turns `PENDING` into `CLEAN`: replacing a
missing control with a lie about one is worse than not having it.

### The OneNote tool ends at an archive

`tools/onenote_export/` is outside the application: nothing in `app/` imports it
and it does not import Django. It reads with delegated `Notes.Read` — the least
privileged scope, and app-only authentication is not available for the OneNote
API in any case — through the device-code flow, so there is no client secret to
store or rotate.

It produces a neutral archive (`manifest.jsonl` plus per-page `page.html`,
`page.txt`, `metadata.json` and attachments) and a reconciliation report. It
creates no Matters and updates no register rows.

The reconciliation refuses to merge on similarity. Four tiers are automatic —
page id, canonical URL, shared reference token, reviewed mapping — and title
similarity is a review queue that can never become a decision. Stage 2A's
discovery work already proved a OneNote hyperlink in this register can point at
the wrong page; a similar title is weaker evidence than that, not stronger.

The token allow-list deserves its own line: page HTML names its resources by
URL, following one means attaching a Microsoft 365 access token, and a tool that
follows a URL it found in a document eventually sends somebody's token to a host
named in that document. Two hosts are permitted and the path shape is checked.

## Alternatives considered

**Synchronous extraction in the web request.** Rejected. OCR on a scanned annex
takes minutes; a lawyer filing incoming mail would wait for it, and a timeout
would leave the intake half-done.

**Celery and Redis.** Rejected — see above.

**Storing extracted text as the document.** Rejected, and it is the invariant
this whole stage is built to protect. The original binary is what Koda received
or sent.

**Rendering email HTML in a sandboxed iframe.** Rejected for Stage 2B. It would
be a second, subtler security surface (CSP, referrer policy, remote resource
blocking) for a gain — seeing the sender's formatting — that nobody asked for.
The original `.msg` is one download away.

**Trigram-indexing document bodies.** Rejected; the master specification (14.1)
names it as how a PostgreSQL search installation becomes unmaintainable.

**An external search engine.** Rejected. Specification 14.6 sets the conditions
for re-evaluating that decision — multi-million searchable fragments, or a
sustained failure to meet latency targets despite sound indexing — and neither
is close.

**A full OneNote migration now.** Out of scope by the brief, and correctly:
matching is unsolved, the links are known to be unreliable, and real content
cannot enter this environment before the Secure Pilot Gate.

## Consequences

A document uploaded through Saabunud is searchable by its contents a few seconds
later, with the page it was found on. Entries and sent opinions are searchable
for the first time, which closes the gap ADR 0013 stated in the empty-results
text.

The derived corpus is larger than the evidence that produced it and is not backed
up. That is the trade the rebuild test exists to justify.

`DocumentVersion` gained two operational columns (`extraction_claimed_at`,
`extraction_note`). Both are outside the immutability trigger's protected set,
which is unchanged.

## Reversibility

Parsers: high, one module each. The derivative and fragment models: moderate —
they are additive tables holding derived data, so dropping them costs the search
corpus and nothing else. The search projection's foreign keys: low, and
deliberately, because removing them would mean going back to a stored visibility
value.
