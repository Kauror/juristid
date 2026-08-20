# ADR 0015 — The historical corpus: OneNote pages as first-class history

- Status: accepted
- Date: 2026-08-20
- Stage: 2D
- Related: ADR 0003 (document lifecycle), ADR 0005 (authorization), ADR 0012
  (legacy register import), ADR 0013 (search projection and child content),
  ADR 0014 (content extraction and derivatives)

## Context

Twenty years of the Chamber's legislative work exists in two places that do not
know about each other:

- **Tööd eelnõudega.xlsx** — 2,455 rows. ADR 0012 imports these as `Matter`
  records in `ARCHIVE` record mode, with the register's own anomalies preserved.
- **A OneNote desktop notebook** — 755 pages, 10,916 attached files, 4.14 GiB.
  The narrative: who was consulted, what was argued, which draft was current,
  and the PDFs and signed containers that went with each.

A prior migration audit compared them and produced five classes of answer:
776 exact matches (the Excel hyperlink's page GUID equals an archived page's
GUID), 76 STRONG, 456 REVIEW_REQUIRED and 3 CONFLICT candidates, and 1,144
Excel Matters plus 148 pages with no candidate at all.

Two facts shaped everything below.

First, **an earlier Graph-based export of the same notebook is wrong**. It
stored one page's HTML under 342 other pages and reported itself as PASS
throughout. It is present on disk, it is plausible-looking, and importing it
would put one lawyer's 2019 argument on 342 unrelated files.

Second, **the audit's answers were checked by a person**. Re-deriving the
matching here would produce a second opinion, and a second opinion that
disagrees with a reviewed one is worse than no opinion.

## Decisions

### Source pages are product data, not importer scaffolding

`LegacySourcePage` sits in `app/legacy_import/source_pages.py` and is a durable
part of the domain model, not a staging table to be dropped after the run. A
lawyer opening a 2019 file wants the page as it was written — the narrative, its
order, its ambiguities and its provenance — and that is a permanent
requirement, not a migration artefact.

The layering mirrors ADR 0014's:

```
LegacySourcePage        the page: blocks, links, derived text, source XML hash
  LegacySourceResource  what was attached: filename, ordinal, SHA-256, size
MatterSourcePage        which Matter claims this page, by which method
  LegacySourceResourceImport   the Document a resource became, per claim
HistoricalMatchCandidate     what a person still has to decide
```

`MatterSourcePage` is many-to-many on purpose. 776 exact matches resolve to 576
distinct pages: 138 pages are claimed by more than one register row, because one
OneNote page genuinely covered two rows of the register.

### Files stay inside the narrative that introduces them

OneNote is a free-form canvas. The archive records blocks with a source ordinal
and files carry the ordinal of the block they sat in, so text and attachments
render as one sequence:

> Ettepaneku eestikeelne variant:
> — ettepanek.pdf

Rendering an alphabetical attachment list at the bottom of the page would have
been simpler and would have thrown away the only thing OneNote was ever good at.
Where the raw XML order and the visual order disagreed, the page says so
(`reading_order_ambiguous`) rather than presenting a guess as a fact.

### The audit is the reconciliation authority; the importer does not re-match

`historical_audit.py` reads the audit's CSVs and nothing else. Exact matches are
applied automatically because a page-GUID equality is deterministic. Everything
else becomes a `HistoricalMatchCandidate` in a review queue, with both sides of
the comparison and the buttons on the same card — a reviewer who has to open two
other pages to decide will decide badly, or not at all, 535 times.

The planner reconciles its own counts against the audit's baseline and reports
the comparison as findings rather than assertions, so a corrected audit is not
refused by a hard-coded number. But `apply` refuses to run if any finding fails
to reconcile: a partially imported historical corpus is indistinguishable from a
complete one to everybody who reads it later.

### Five conditions before a page becomes a Matter of its own

A page with no Excel Matter becomes one only if all of: its audited role is
`MATTER_LIKE`; it has no exact link; it has **no pending review candidate**; it
has a title; and it has at least one file or 200 characters of text.

The third condition is the one worth naming. A page with a candidate could still
turn out to belong to an Excel Matter. Creating a Matter now and linking the page
later would leave two records of one thing and nothing to say which is real. On
the real corpus this makes the importer more conservative than the audit's own
"substantive unmatched" count — 88 rather than 124 — and the difference is
exactly the 67 pages a person has yet to look at.

A OneNote-only Matter gets **no register reference**. The register never had
this work; minting `2019_9999` would put fiction in the column the product
treats as identity.

### The archive is source material, and the importer never writes to it

The plan verifies the register's SHA-256 and the archive manifest's canonical
digest (sorted lines, LF, no trailing newline) against both the operator's
expectation and the audit's own record, and refuses to plan against a source
that changed. `HISTORICAL_SOURCE_ROOT` is mounted read-only. An importer that
can write to its own source is one bad run away from having nothing to re-run
against.

### The Graph export is refused by shape, not by trust

`OneNoteArchive` requires an `archive.json` marker. The wrong export does not
have one, so pointing the importer at it fails immediately rather than
succeeding at importing 342 copies of the wrong page.

### Materialised files are evidence, and duplication is deliberate

Each `(MatterSourcePage, LegacySourceResource)` pair becomes its own `Document`
through the ordinary Stage-2A services, so a historical PDF is subject to the
same immutability trigger, the same download route and the same authorization as
a file uploaded yesterday. Every copy records the same `resource_key` and the
same source SHA-256, so the duplication reads as duplication. `_materialise_one`
asserts the stored digest equals the archive's before recording success.

Scan state is `PENDING`, never `CLEAN`. These files predate any scanner this
system will ever run, and saying otherwise would be inventing a control.

### ASiC-E and BDoc are stored, catalogued, and never opened

Signed containers keep their own bytes and their own MIME type. Nothing unpacks
them, nothing validates a signature, and their versions are marked
`NOT_APPLICABLE` with an operator-facing reason at import time rather than left
`PENDING` in a queue for ever. Unpacking a signed container to index its
contents is how a system starts asserting things about signatures it cannot
check.

Widening `ALLOWED_EVIDENCE_MIME_TYPES` to accept `.asice`, `.bdoc`, `.xltx`,
`.rtf` and the other historical formats is a storage decision, not an extraction
one: refusing to *store* a file the extraction stack cannot parse would lose the
original for no gain.

### Old material is not less confidential for being old

Every read goes through `matter_visibility_q` on the owning Matter, exactly like
every other read in the system. A 2019 file on a member's insolvency is the same
kind of material as this year's. The page's own XML is downloadable but never
rendered — it is untrusted markup like any other stored file.

The reconciliation queue is gated on `UserRole.ADMINISTRATOR` at the URL, not
merely hidden from the navigation: it can create Matters.

### Applying is idempotent through relationships, not titles

Re-running `apply` finds pages by `(source_system, source_page_id)`, links by
`(matter, source_page)`, and OneNote-only Matters by the existence of an
`ONENOTE_ONLY_MATTER` relationship. Matching on title would merge two genuinely
different pages that a lawyer happened to name the same thing.

`materialise` is resumable by asking the database what is still missing rather
than by trusting a cursor, and one page or one file failing costs only itself.

## Consequences

- The register and the notebook are one system. A Matter's Ülevaade says its
  history exists in three numbers; Dokumendid lists the pages; the page itself
  reads as the case file it was.
- 535 decisions remain for a person, by design. The importer created no
  relationship it could not justify from a deterministic GUID match.
- Search gains a fourth child kind, `LEGACY_SOURCE_PAGE`, at index version
  `2D.1`. It projects the page's derived text and is authorized through the same
  Matter chokepoint as every other row.
- The corpus is roughly 4.14 GiB of originals plus one materialised copy per
  claim. The 138 multiply-claimed pages cost their bytes twice; a correct
  historical relationship is worth more than the disk.
- Not built, and deliberately: ASiC-E content parsing, any re-derivation of the
  audit's matching, and any import of the Graph export.
