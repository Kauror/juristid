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

## Owner

Metric catalogue owner and the coverage thresholds are an open business decision
(department head + reporting owner). Stage 2E ships with permissive thresholds
and shows coverage on every card, which is the safe default while nobody has
said what "complete enough" means for a given number. Tightening a threshold is
a one-line change to the definition.
