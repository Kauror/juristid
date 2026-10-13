# 0160 — Management statistics: deadlines, timeliness, comparisons and composition

**Status:** proposed (draft PR, five-track round of 2026-10-13; owner review pending)
**Date:** 2026-10-13
**Extends:** 0017 (the metric catalogue), 0007 (reporting contract). **Reads:** 0135
(a response deadline keeps its history), 0151 (a Koda proposal is a Teema the
Chamber starts), 0090 §4 (the `Õigusakt` group), 0149 (Osakond's opinion counts).
**No migration.** No second analytics stack, no new navigation item.

## Context

The owner asked for management statistics of the kind the Lovable alpha sketches:
how long Koda is given to answer, whether answers go in on time, this year
against the same stretch of last year, how much of the work is European, how
much the Chamber starts itself, and monthly/yearly opinion volume. The existing
catalogue was inventoried first, so nothing is built twice:

| Question | Before this ADR | Decision |
| --- | --- | --- |
| 1. Time to prepare (Saabus → Arvamuse tähtaeg) | **NEW** — no metric reads both dates | `RESPONSE_PREPARATION_TIME` |
| 2. Opinions on time (sent date vs deadline) | **NEW** — `RESPONSE_DEADLINES_OPEN` / `OVERDUE_DO_DEADLINE` are point-in-time work counts, nothing compares a send with a deadline | `OPINIONS_ON_TIME` |
| 3. Equivalent-period comparison | **PARTIAL** — `NEW_NATIVE_MATTERS_YOY_CHANGE` (arrivals) and `OPINION_ARCHIVE_YOY_CHANGE` (archive files) exist; nothing for canonical sent opinions, and no comparison was rendered on any tab | `SUBMISSIONS_SENT_YOY_CHANGE`, and a comparison partial |
| 4. EU-related share by year | **PARTIAL** — the structured fact exists (`Õigusakt` v2.0 groups, `EU_LEGAL_INSTRUMENT_KEYS`); no metric reads it | `EU_RELATED_MATTERS_BY_YEAR` |
| 5. Chamber initiatives by year and share | **PARTIAL** — `MATTERS_BY_TRACK` counts «Koja algatus» for the period, with no year axis and no share | `KODA_INITIATIVES_BY_YEAR` |
| 6. Monthly/yearly opinion volume | **PARTIAL** — `SUBMISSIONS_SENT_BY_PERIOD` (years), `OPINION_ARCHIVE_BY_MONTH` (archive files by month), `NEW_NATIVE_FULL_MATTERS_BY_MONTH` (arrivals); no canonical sends by month | `SUBMISSIONS_SENT_BY_MONTH_AND_YEAR` (month × year table) |

## Decision

### 1. Six catalogue entries, one selector module

Each metric is a `MetricDefinition` in `metric_catalogue.py` with its population,
time basis, exclusions, era limits, coverage and — new, optional on every
definition — `numerator_et` / `denominator_et`, rendered as «Lugeja» and
«Nimetaja» on the card and on `/statistika/definitsioonid/`. The computers live
in `app/reporting/selectors/management.py` and are reached only through
`services.compute`. `COMPUTERS` still covers the catalogue exactly.

### 2. A Chamber initiative is not an incoming request

`Matter.track = KODA_INITIATIVE` is the one stored fact (0151 §1). Such a Teema is
excluded from both deadline populations — including a historical one that still
holds dates — and each card says how many were excluded.

Since the 0151 amendment of 2026-10-13 (PR #427) a native initiative may hold an
optional `Arvamuse tähtaeg` of its own, never a `Saabus`, and «Märgi saadetud»
answers that deadline with the sent proposal (`answer_current_deadline_with`).
That deadline is still not one anybody gave Koda, so it changes nothing here:
the answered row is excluded from `OPINIONS_ON_TIME` by the Matter's track and
counted in its note; the initiative has no arrival, so it is not in the
preparation population either, and the preparation note counts it separately
(«neist N on ilma saabumiseta, kuid oma tähtajaga perioodis» — placed in the
period by any deadline it held, current or historical). `KODA_INITIATIVES_BY_YEAR`
reads the track alone and is unaffected by any deadline.

### 3. Time to prepare reads the *first* deadline

Population: visible real Matters, `received_date` in the period, not an
initiative. Per Matter: days from `received_date` to the first deadline the
system knows — the oldest `MatterResponseDeadline` row, or the current field
when there is no history. A move or a later request never changes what Koda was
first given. Median and percentiles (nearest rank), never a mean; buckets 0–7,
8–14, 15–21, 22–30, 31–60, >60 calendar days. **No deadline** and **deadline
before arrival** are their own visible groups, outside the distribution;
coverage is Matters with a deadline / incoming Matters. A first deadline from
before the history was kept (`requested_at IS NULL`) is counted and its number
stated, because it may itself be an untracked revision.

### 4. On time is judged against the deadline in force at sending

Population: `MatterResponseDeadline` rows with outcome `ANSWERED` that name a
sent opinion this reader may see (`Submission.visible_to`, status
SENT/WITHDRAWN/SUPERSEDED with `sent_at`), on a visible non-initiative Matter,
with the send in the period. Nothing is inferred from dates: an opinion counts
only where a person named the deadline it answers (0135 §5).

The deadline an answer names is the one in force at sending **only if the
history proves it**. An answer is *not verifiable* — counted, listed, never in
the percentage — when:

- another deadline row of the Matter ended after the send and before the answer
  was recorded (moved, replaced, a new request): the deadline changed after the
  letter went;
- the request was recorded after the send;
- the deadline predates the history (`requested_at IS NULL`): its revisions
  were never kept;
- the named opinion has since been withdrawn (0135 §4 «vajab ülevaatamist»).

**A deadline recorded after the send stays not verifiable** (owner decision,
round 2 of 2026-10-13). Writing a deadline down after the letter went does not
establish what was in force when it went, so no historical certainty is
manufactured from it; the definition says so in its exclusions and notes.

For a date-only send (`SentAtPrecision.DATE`) a change on the sending day itself
is treated as after it: the order within that day is unknown. The comparison is
the Tallinn calendar date of `sent_at` ≤ the deadline. Value: on time / verifiable,
as a percentage; coverage: verifiable / all answered. The card also says how many
deadlines were answered in words only (no send date) and how many opinions sent
in the period named no deadline. **The current, revised deadline is never used to
judge an earlier send.**

### 5. Same period last year

Canonical SENT Submissions, 1 January to today against 1 January to the same
calendar day last year (29 February → 28 February), both read in Europe/Tallinn.
A previous year before the first recorded Submission is *not measured*, and the
card declines rather than reporting a rise from zero. Each side opens the
Statistika submission list cut at the same day (`?aasta=&kuni=MM-DD`).

### 6. EU share: two bases, `Õigusakt` first

Owner decision of 2026-10-13: a Matter whose `Õigusakt` is unclassified but whose
stored `Menetlusliik` is «ELi algatus» (`EU_INITIATIVE`) or «ELi õiguse
ülevõtmine» (`NATIONAL_TRANSPOSITION`) counts as EU-related, on a basis kept
separately identifiable. Four columns, each Matter in exactly one, decided by
one ordered `Case` (the order is the precedence):

1. *ELiga seotud õigusakti järgi* — at least one instrument in
   `EU_LEGAL_INSTRUMENT_KEYS` (whatever the track);
2. *Siseriiklik* — instruments present and all in
   `DOMESTIC_LEGAL_INSTRUMENT_KEYS` (also on an EU track: a `Seadus` transposing
   a directive stays domestic, and the card counts how many such Matters exist);
3. *ELiga seotud ainult menetlusliigi järgi* — the `Õigusakt` group is unknown
   (no instrument, or one the reviewed list puts in neither group) and the
   track is one of the two above;
4. *Määramata* — everything else.

The share is (*õigusakti järgi* + *ainult menetlusliigi
järgi*) / (those + *siseriiklik*); *määramata* is never in the denominator and
never domestic. Every cell opens exactly its Matters. Nothing is read from
titles or notes. Year basis is the register reporting year; *Teadmata aasta*
appears only over all years, as on the year chart.

**The historical «EL teatis» (`el-teatis`) is not classified** — pending an owner
decision. Evidence: it is a version-1.0 row (`taxonomy/0006`, «Euroopa Komisjoni
teatis või muu ELi institutsiooni teatis», aliases «EL teatis», «EK teatis»);
ADR 0090 retired it from selection (`RETIRED_LEGAL_INSTRUMENT_KEYS_V2`) and
`legal_instruments.py` deliberately places *every* retired row in neither group
(«a retired version-1.0 row is in neither, which is the honest answer»), with no
reviewed equivalence to the new «Muu ELi dokument» (whose own description
mentions a *teatis*, but which carries no aliases by design). The era contracts
give `ÕIGUSAKT` the `mapped` authority — no importer writes it — and
`current_legal_instrument_keys` refuses a 2026 cell that resolves only to a
retired key, so `el-teatis` reaches a Matter only by a person's choice made
while version 1.0 was offered, or kept by «Jäta alles». Its own description is
unambiguously European, unlike «Konsultatsioon»; adding it to the EU group is a
one-line change, but it would reverse an accepted ADR's explicit rule and is
the owner's to make. Until then such a Matter is *määramata* (or, on an EU
track, *ainult menetlusliigi järgi*), and the card names how many there are.

### 7. Initiatives by year

Columns «Koja algatus», «Muu menetlusliik», «Menetlusliik määramata»; share =
initiatives / all Matters of the year. Because many historical rows have no
track, earlier years' count is a lower bound, and the card says so.

### 8. Sends by month and year

Canonical SENT Submissions, months down and measured years across, so a month
reads across years. Columns run from the first recorded send (never before
2020) to this year; a month of this year still to come is a dash, not 0.
Archive files stay in their own block on the same tab and are never added.

### 9. Every number opens its rows

Every matrix cell opens `/statistika/loend/` (which has a CSV). For the
initiative and no-track groups the list also offers «Ava registris», the same
group in the Teemad register. Submission windows link to the Statistika submission list, which gains `kuu`
and `kuni` (each refused without `aasta` or when it names no real day). Every
other group opens `/statistika/loend/?naitaja=<KEY>&ruhm=<group>[&aasta=]`, which
pages the very observation list the number was taken from under the same
reporting context, and exports it as CSV (`/statistika/eksport/loend.csv`). A
group or metric nobody linked to is a 404.

**Same data class, same population, everywhere.** Every population starts at
`visible_matters` (authorization + `real_data()`). The Statistika list, the
submission list and both CSVs are computed from the same reporting context, so
TEST Matters never appear in them. The Teemad register defaults to `andmed=koik`,
so every register link drawn by these metrics carries `andmed=paris`; a test per
metric asserts cell = list = CSV with TEST and restricted Matters present.
(The register links of the *older* metrics do not carry `andmed=paris` yet — out
of this ADR's scope, reported to the coordinator.)

**Coverage is on the card**, not only on the definitions page: each card's first
note starts «Katvus:» — deadline history and linked deadlines since 4.10.2026,
how many Teemad have no track, how many cannot be placed EU/domestic, and from
which year sent opinions are structured.

**Query count is constant in the rows.** Each metric is a fixed number of
statements (1–5); the two composition matrices are one grouped query each. A
test grows the data across more years and asserts the count does not move.

### 10. Placement

Tegevus gains «Võrdlus ja kuud» (the comparison and the month × year table) and
«Arvamuse tähtajad» (both deadline cards and their compositions); Teemad gains
«Aastate koosseis» (the two composition tables). Ülevaade is unchanged, so its
visual baseline does not move. No navigation item is added.

## Consequences

- Shared partials grew optional parts that render nothing for existing metrics:
  `_matrix.html` (cell links, a dash for an unmeasured cell, an optional share
  column, the caption's column noun), `_composition.html` (a distribution strip
  when the result carries one), `_definition.html` (Lugeja / Nimetaja).
- `tests/test_empty_application.py` lists `OPINIONS_ON_TIME` among the
  percentages that decline on an empty denominator.
- Most production answers recorded before 2026-10-04 have no named deadline, so
  `OPINIONS_ON_TIME` will start small and grow; the coverage figure says so.

## Open owner decisions

1. ~~Should *ELiga seotud* also count «ELi algatus» / «ELi õiguse ülevõtmine» on a
   Matter whose `Õigusakt` is unclassified?~~ Decided 2026-10-13: yes, as its own
   basis, `Õigusakt` first (§6).
2. Whether the historical «EL teatis» instrument should join the European group
   (today: unclassified, named on the card; evidence in §6).
3. ~~Whether a request recorded after the opinion was sent should be judged?~~
   Decided 2026-10-13: no, it stays not verifiable (§4).

## Reversibility

Entirely by code: no migration, no stored value. Removing the six entries and the
module restores the previous catalogue.
