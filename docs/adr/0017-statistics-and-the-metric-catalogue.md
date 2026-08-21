# 0017 — Statistics, the metric catalogue, and operational snapshots

- **Status:** Accepted — implemented on the `stage-2e-statistics` branch, pending integration
- **Date:** 2026-08-21
- **Supersedes nothing.** Implements the contract ADR 0007 fixed in Stage 0.

## Context

ADR 0007 settled what a metric owes its reader — population, coverage, era
limits, drill-through — and deliberately built nothing. Stage 2E builds it.

By now the corpus is real: a register going back to 2011, a OneNote archive
imported as product data, and an operational system a few days old. That
combination is what makes statistics dangerous here rather than merely useful.
Three facts about it shape every decision below.

**Most of the corpus predates most of the fields.** The register gained a status
column in 2023 and a next-action column in 2025. `Submission` records begin with
this system. A chart that averages across that boundary is not describing Koda's
work; it is describing when Koda started writing things down.

**The archive's dates belong to the archive.** The historical importer fills a
OneNote-only Matter's `reporting_year` from the page's own `source_created_at`,
which is the only date such a page has. It is not a reporting year.

**Two correct security decisions produce a frightening number.** Historical
evidence is imported unscanned, and an unscanned file is not opened by a parser.
Flatten the extraction states and the deployment reports sixteen thousand
failures, none of which is one.

## Decision

### The catalogue is code

Every published metric has a frozen `MetricDefinition` in
`app/reporting/metric_catalogue.py`: key, version, population, time basis,
eligible record modes and origins, exclusions, earliest reliable period,
source-era limitations, thresholds, coverage description and drill-through.

**No admin UI writes metric definitions.** A definition editable through the
product is a definition nobody reviewed, and "who changed the population"
becomes unanswerable exactly when somebody disputes a figure in a board paper.

The definition is **enforced, not described**: `selectors.base.eligible_matters`
reads the eligibility fields and `metric_types.grade` reads the thresholds.
`services.COMPUTERS` is asserted at import to cover the catalogue exactly in
both directions, so a definition cannot exist without an implementation or the
reverse.

### Every answer carries its own population

`MetricResult` carries value, population, eligible count, coverage pair,
status, period, as-of time, drill-through URL, segments and notes. Four
statuses, and the last two are different failures that are never collapsed:

- `AVAILABLE` / `PARTIAL` — a number, with its coverage;
- `INSUFFICIENT_DATA` — the records exist but too few carry the field;
- `NOT_APPLICABLE` — the question does not apply and never will.

Where the source cannot support an honest number the result declines. It does
not return zero, because a reader takes zero for a measurement.

### Authorization precedes aggregation

Every selector begins at `visible_to(viewer)` — or at a child queryset scoped
through its Matter — and only then filters, groups, counts, ranks, slices and
exports. This is not a preference. Counting everything and hiding rows at render
time leaves the hidden rows inside the totals, and nothing on screen looks
wrong: **the count is the disclosure.**

Consequences that follow and are tested: a restricted Matter contributes nothing
to a total, a year bar, an owner tally, a policy-area bar, a coverage
denominator, an organisation top-list, a byte count, a CSV row or a grouped
"other" bucket. The technical administrator role reads no more than an unrelated
specialist. The shared gate's department scope sees NORMAL content and no
participation.

### Every number opens exactly what it counted

A statistic that cannot be checked is a statistic people argue with. So each
metric and each chart segment carries a link, and the test suite follows that
link through the real view and compares the count the page reports with the
number the card claimed.

Making that true meant giving the register the filters the statistics actually
count on — `aasta` (built from the same `Q` the year chart uses), `paritolu`,
`allikas`, `tegevus`, `saatja`, `adressaat`, and a `puudub` sentinel so every
*määramata* bucket is a link like any other bar.

**Where an honest link is impossible there is no link.** Evidence versions,
distinct file contents and entries have no list in this product; a link that
opened something else would be worse than none, because the reader believes it.
Those metrics say so in their definition instead.

Two new lists exist because nothing else could answer their drill-through:
`/statistika/arvamused/` (the product's only list of sent Submissions) and
`/statistika/materjalid/` (its only list of historical file occurrences).
Everything that already has a surface — the register, the reconciliation queue,
Minu töö — is linked to rather than reimplemented.

### Period is a semantic filter

`Period` carries a span of years and nothing else. Which *column* that span is
compared against is a property of the metric's `TimeBasis`: a Matter's reporting
year, a submission's send date, an entry's occurrence time, a source page's own
timestamp. A single date filter that quietly chose a column would give four
different answers to one question depending on which page you asked it from.

There is deliberately **no `created_at` basis**. The row for a 2014 register
matter was written in 2026.

Metrics about the corpus rather than about a window declare
`respects_period=False`, are labelled *kogu korpus*, and drop the year from
their drill-through link.

### A OneNote-only Matter has no reporting year

`REGISTER_YEAR_ORIGINS` in `app/matters/enums.py` names the origins whose
`reporting_year` came from a register reference or a native allocation.
`LEGACY_ONENOTE` is absent, so those Matters appear in **Teadmata aasta** on
every Matter-year axis, and their page timestamps are analysed separately as
source history.

This does not change the importer, which is right to record the only date it
has. It changes what reporting is willing to call a reporting year.

### An occurrence is not a file

`HISTORICAL_RESOURCE_OCCURRENCES` counts `LegacySourceResource` rows: one file
as it sits on one page. The same bytes attached to two pages are two, because
the corpus really does contain the thing twice.
`HISTORICAL_UNIQUE_BINARY_CONTENTS` counts distinct SHA-256 values. They are
shown side by side and never presented as one number.

### Extraction has five states, and two of them are successes

`NOT_APPLICABLE` is the correct outcome for a signed container. *Waiting on a
malware scanner* is neither a failure nor a queue. The eligibility rule is
imported from `documents.extraction.orchestrator`, not restated, and the
searchability coverage excludes what no parser opens — otherwise the figure
could never reach 100 % and nobody would use it.

### Materialisation has four states

Imported, still to copy, empty in the source, copy failed. The SQL form lives in
`reporting.selectors.historical.materialisation_q` and the rendered form in
`legacy_import.historical_views._file_state`; the suite asserts they agree on
every branch, the same arrangement main already uses for extraction eligibility.

### Data quality separates tasks from limitations

Reading-order ambiguity, files waiting on a scanner and attachments that are
empty in OneNote are *coverage notes*, not queue items, and are excluded from
the attention total. Archive sparsity — an ARCHIVE Matter with no next action,
an old Matter with no stage, a OneNote-only Matter with no reference — is not
listed at all. A queue that cries wolf is a queue people stop opening.

### PostgreSQL, and no warehouse

Ordinary Django querysets with `annotate`, filtered aggregates, `Exists` and
`Subquery`. No ClickHouse, no Elasticsearch for statistics, no OLAP, no Redis
cache, no event pipeline. **No materialized views**: none has been justified by
a measurement, and adding one before measuring buys a refresh problem.

Charts are server-rendered — CSS bars and one inline SVG polyline whose
coordinates are computed in Python. No chart library. Three things follow: the
numbers on screen are the numbers the selectors computed with no second
aggregation in the browser; every segment is a real `<a>` that works with a
keyboard and a screen reader; and the page is legible with JavaScript off.

### Operational snapshots begin at cutover

`OperationalMatterSnapshot` is the one exception to "answer from canonical
tables", and it exists for a question those tables genuinely cannot answer: how
many active Matters had no next action last March. A trend built by re-deriving
the past from the present would be a confident line describing today, drawn
backwards.

So the table starts accumulating when the command starts running, and there is
**no backfill**. It photographs open FULL Matters only. It stores **no
visibility**: reads join the live Matter and authorize there, so restricting a
Matter today removes it from last month's aggregate for anybody who may not see
it now. One row per Matter per date, enforced by a unique constraint, which is
what makes the command idempotent by construction.

## Alternatives considered

**Metric definitions in the database, editable by a reporting owner.** Rejected:
it removes the review that makes a definition worth anything, and the first
disputed figure becomes unanswerable.

**One period filter over a single date column.** Simpler, and wrong. It is the
register's own defect — one column meaning several things — reproduced in the
tool built to replace it.

**Aggregating first and filtering the display.** Faster to write and the
disclosure is invisible.

**Materialized views for the archive counts.** Deferred until a measurement
justifies one. The corpus is thousands of rows, not millions.

**Fabricating historical Submissions from the register's `VÄLJA` dates.**
Refused, preserving Stage 2D's decision: a sent opinion with no evidence is an
unverifiable claim about what Koda argued.

**Historical member-feedback counts.** Deferred. They exist in the source and
the importer reads them, but nothing persists them as queryable columns — they
survive only inside `MatterSourceReference.source_row_raw`, keyed by spreadsheet
column letter. Recovering them would mean re-resolving each row's era contract
and re-parsing free text. Recorded in `DEFERRED_METRICS` with the reason, and
shown on the Andmekvaliteet tab, so an absent number reads as a decision rather
than as zero.

## Consequences

- A new number requires a catalogue entry, a selector and a test. That is the
  intended friction.
- The register gained six filters and a sentinel. They are small, and each one
  exists because a statistic promised a list.
- Trends are short until the system has been running, and the pages say so.
  This is the honest state of a young deployment, not a defect to design around.
- Production scheduling of `capture_operational_snapshot` is a deployment step,
  not part of this branch.

## Reversibility

High for the metric set, the page layout and the chart rendering — all are code
in one app. Moderate for the register's new filters, which other surfaces may
come to depend on. Low for two principles: authorization before aggregation, and
a coverage figure travelling with every number.
