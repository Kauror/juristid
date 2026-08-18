# Metric catalogue

Every metric this system publishes has a versioned definition recorded here
before it appears anywhere in the product. A number without a definition and a
coverage rule is not shipped.

No metrics are implemented in Stage 0. This is the template and the rule set.

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
(department head + reporting owner), required by Stage 4.
