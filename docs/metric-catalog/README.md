# Metric catalogue

Every metric this system publishes has a versioned definition, and a number
without a definition and a coverage rule is not shipped.

**Since Stage 2E the definitions live in code**, at
`app/reporting/metric_catalogue.py`, and the product renders them: every card
carries a *Kuidas arvutatakse?* panel, and `/statistika/definitsioonid/` is the
whole catalogue as one page. This document is now the rule set those definitions
obey and the reasoning behind it; the definitions themselves are reviewed as
code, in a diff, like any other load-bearing rule (docs/adr/0017).

The move was deliberate. A catalogue kept only as prose drifts from the queries
silently — and the first time somebody notices is when two numbers disagree in a
board paper. In code, `services.COMPUTERS` is asserted at import to cover the
catalogue exactly in both directions, so a definition cannot exist without an
implementation or the reverse.

## Required definition fields

Each metric file records:

| Field | Meaning |
| --- | --- |
| `key` | Stable identifier used in code and exports. |
| `name_et` / `name_en` | Display name. |
| `description` | What question it answers, in one sentence. |
| `source population` | Which records are eligible at all. |
| `numerator` / `denominator` | For ratios only, each defined separately. |
| `eligible origins` | `NATIVE`, reviewed import, `ARCHIVE`, or a subset. |
| `required fields` | Fields that must be present for a record to count. |
| `exclusions` | Records deliberately left out and why. |
| `earliest reliable period` | Before this date the metric is not published. |
| `source-era limitations` | Schema-era breaks that make comparison unsafe. |
| `minimum completeness threshold` | Below this, show *insufficient data*. |
| `coverage` | Count and percentage of the population that qualified. |
| `authorization / drill-through` | The query that opens the exact authorized records. |

## Rules

1. **Coverage travels with the number.** Every displayed metric shows its
   population and coverage. If completeness is below the threshold, the product
   shows *insufficient data* rather than a precise-looking figure.
2. **Every metric drills through.** Clicking a number opens the exact records
   the viewer is authorized to see, through the same authorization chokepoint as
   any other read.
3. **Authorization is not applied after aggregation.** Counts are computed over
   the scoped queryset.
4. **Era boundaries are visible.** A trend line may not silently span a schema
   change; native and legacy evidence are separated or labelled.
5. **Medians and percentiles for skewed durations**, not averages.
6. **Matter inventory is called inventory or portfolio**, never workload or
   productivity.

## Metrics that will never be published

- lawyer productivity or output scoring from Matter or submission counts;
- "workload" inferred from open Matter count alone;
- a response rate derived from the legacy consultation counts, which are
  independent observations without a guaranteed common denominator;
- average Matter duration across mixed process types without segmentation;
- a Koda win rate, an influence percentage or a ministry success ranking;
- outcome rates that silently drop unresolved or unreviewed proposals;
- any AI-generated influence score.

## Where each field lives in code

| Field here | `MetricDefinition` attribute |
| --- | --- |
| `key` | `key` |
| `name_et` | `label_et` |
| `description` | `description_et` |
| source population | `source_population_et` |
| eligible origins | `eligible_origins`, `eligible_record_modes` |
| required fields | `required_fields` |
| exclusions | `exclusions_et` |
| earliest reliable period | `earliest_reliable_period` |
| source-era limitations | `source_era_limitations_et` |
| minimum completeness threshold | `minimum_coverage`, `minimum_population` |
| coverage | `MetricResult.coverage_count` / `coverage_denominator` |
| authorization / drill-through | `drillthrough_et`, and `MetricResult.drillthrough_url` |
| version | `version`, rendered as `KEY@n` |

Time basis has no row in the original template and needs one: `time_basis` says
which clock the period filter is read against, because "periood" is not one
field. A Matter's reporting year, a submission's send date and a OneNote page's
own timestamp are three different facts (docs/adr/0017).

## Metrics that are deliberately not published

Absence with a stated reason is more useful than a number nobody can defend, so
the reasons are data rather than prose: `DEFERRED_METRICS` in the same module,
rendered on the Andmekvaliteet tab. As of Stage 2E that covers the register's
member-feedback counts and its `VÄLJA` outbound dates — present in the source,
read by the importer, but not persisted as queryable columns.

## What Stage 2H changed, and why the versions moved

Until the opinions archive was reconciled, every submission metric carried the
same era note: *structured submission records begin in this system, so there is
no measurement for earlier years*. That statement is now obsolete, and it was
replaced rather than deleted.

`SUBMISSIONS_SENT`, `SUBMISSIONS_SENT_BY_PERIOD`, `SUBMISSIONS_BY_RECIPIENT`,
`SUBMISSIONS_BY_KIND`, `MATTERS_BY_SUBMISSION_COUNT` and
`MATTERS_WITH_MULTIPLE_SUBMISSIONS` are all at **version 2**. What they count
did not change; what populates them did, and a reader comparing a figure across
that boundary is comparing two things.

The new era note says three true things instead of one:

1. historical submissions are reconstructed from the opinions archive;
2. that archive begins in **2020** — the register begins in 2011, and the gap
   is absence of evidence rather than absence of advocacy;
3. even inside 2020–2026 the coverage is partial, because a file that cannot be
   tied to one Matter on evidence waits in a review queue instead of being
   guessed into a count.

`SUBMISSIONS_SENT_BY_PERIOD` therefore declares `earliest_reliable_period` as
2020. The archive's own completeness is reported separately, under
**Andmekvaliteet**, so that "how much did Koda write" and "how much of the
archive have we placed" can never be read as the same number.

## Template

```yaml
key: active_full_matters
name_et: Aktiivsed teemad
name_en: Active full Matters
description: >
  Open Matters in FULL record mode at the reporting moment.
source_population: Matter where record_mode = FULL
numerator: null
denominator: null
eligible_origins: [NATIVE, PROMOTED_LEGACY]
required_fields: [is_open, record_mode]
exclusions: ARCHIVE records; Matters the viewer is not authorized to see
earliest_reliable_period: cutover date
source_era_limitations: none for native records
minimum_completeness_threshold: 100%
coverage: computed per query
drill_through: Matter.objects.visible_to(user).full_records().active()
owner: department head + reporting owner
version: 1
```

## Statistics 2.0: two new answer shapes and five rules

The catalogue grew fourteen definitions when the workspace was made useful for
department reporting. Nothing about the architecture changed — one catalogue,
one `compute(key, context)`, server-rendered charts, authorization before
aggregation — but two answers do not fit a list of segments and needed shapes of
their own.

**`Matrix`** is a two-dimensional count: reporting year × responsibility, month
× responsibility. It renders as a real HTML table with `scope`ed headers, a
caption and printed totals. It is deliberately not a heat map: a grid whose
value lives in a background shade has no data table beneath it, because it *is*
the data table, and a reader who cannot separate two tints has nothing left. Row
totals, column totals and the grand total are computed in Python, never in a
template.

**`Comparison`** is one period measured against a comparable earlier one, and it
carries both window labels and the cutoff they were derived from.

Five rules govern the new definitions.

### 1. Responsibility is a source fact before it is an account

For a Matter carrying a `CurrentRegisterState`, the responsibility label is the
register's own `VASTUTAJA` text. Only a Matter with no register row falls back
to `Matter.owner`, and only a Matter with neither becomes *Määramata*.

The order is load-bearing rather than tidy. Colleagues the register names have
no login here, and grouping by the resolved account would file them under
*Määramata* — discarding the one thing the register is certain about. Inventing
an account to hold them would be worse. The precedence lives in one place,
`app/reporting/selectors/responsibility.py`, and nothing in reporting writes to
`Matter.owner`.

Because these counts are grouped by the source name and the register filters on
the resolved owner, they carry **no drill-through**. A link that opened a list
disagreeing with the number above it is worse than no link.

### 2. A responsibility count is inventory

Not workload, not output, not a ranking. Columns and segments are ordered
alphabetically with *Määramata* last — never by size, because an ordering by
count is a league table however it is captioned. When a matrix is wider than a
table can show, the tail becomes one labelled column that says how many names it
holds, and every name still appears in full in the responsibility composition
beside it. No silent caps.

### 3. Archive evidence is not a canonical Submission

`OpinionArchiveItem` says a file with this name sits at this path in the
archive. `Submission` says Koda sent this document on this date to these
recipients. The first is a statement about a zip file and the second about the
department's conduct, so the two are reported beside each other and never added.
`SUBMISSIONS_SENT` keeps its own metric even while it has little history to
show.

Archive metrics are dated on `filename_date` and are labelled as *arhiivi
dokumendid kuupäeva järgi*. The field's own model comment calls it a matching
signal rather than a sent date, and the register's `VÄLJA` agrees with it on the
same day in 326 cases and the next day in 227.

### 4. The archive counts distinct binaries, and declines when empty

A trend counts distinct SHA-256 values; occurrence inventory stays a separate
metric. The measured corpus happens to hold 767 of each, so the two are equal
today and would stop being equal the moment a later snapshot files a letter
twice — at which point a trend built on occurrences would report a filing habit
as advocacy volume. A binary found at two paths is dated by its earliest
occurrence.

With no catalogued archive at all the metrics report `INSUFFICIENT_DATA`, not
zero. A year axis reading 0 is a confident claim that Koda sent nothing.

### 5. A period comparison is cut at the same date on both sides

`OPINION_ARCHIVE_YOY_CHANGE` derives its cutoff from the latest archive date
that exists; `NEW_NATIVE_MATTERS_YOY_CHANGE` uses today. Both then apply that
same day-of-year to the previous year, so seven months are never measured
against twelve. When the previous comparable period is zero the card shows the
absolute difference and **no** percentage: there is no percentage change from
nothing, and printing one would be read as a measurement.

The wording stays neutral in both directions. More matters arriving is more work
the department was handed; more archived letters is more letters. Neither is a
result, and no colour on the card suggests otherwise.

### Archive links, and what a category total means

Archive metrics that name a Matter read the derived and reviewed
`OpinionArchiveMatterLink` layer, never a `PENDING` `OpinionMatchCandidate` — a
proposal nobody accepted is not coverage. They are scoped through
`visible_matters` before they are grouped, so a link to a restricted Matter
moves nothing; unlinked archive inventory names no Matter and keeps the existing
rule of being counted for everyone.

One archived letter may concern several Matters and the model has no notion of a
primary one. `OPINION_ARCHIVE_LINKED_BY_RESPONSIBILITY` therefore counts
**distinct files per responsibility label**, a file appears under each lawyer it
reaches, and its segments can add up to more than the corpus total. The
definition says so rather than leaving a reader to discover it by adding the
bars up. `OPINION_ARCHIVE_LINK_COVERAGE` counts the same letter once, because it
asks a different question: how much of the corpus is placed at all.

An unlinked archive file is never called a missing opinion. It is evidence Koda
holds that has not yet been tied to a Teema.

## Owner

Metric catalogue owner and the coverage thresholds are an open business decision
(department head + reporting owner). Stage 2E ships with permissive thresholds
and shows coverage on every card, which is the safe default while nobody has
said what "complete enough" means for a given number. Tightening a threshold is
a one-line change to the definition.
