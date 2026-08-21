# 0018 — Structured Matter facts, and the generated department views

- **Status:** Accepted — implemented on the `stage-2g-matter-intelligence` branch, pending integration
- **Date:** 2026-08-21
- **Supersedes nothing.** Sits beside ADR 0011, which separated `Hetkeseis`, `Järgmiseks` and closure.

## Context

The department keeps three department-wide lists in OneNote, by hand:

- *Olulised tähtajad* — milestones somebody is watching;
- *Jõustuvad aktid* — when acts, or parts of them, come into force;
- *Töövõidud* / *Töövõidu kandidaadid* — advocacy wins claimed and confirmed.

Each of the three is a fact about a particular Teema, written down somewhere
else. That is the whole defect. A date moves, the Matter is updated, and the
department list still says September; a Matter is closed and its line stays on a
list nobody re-reads. The lists are also the department's only answer to
"what is coming up" and "what did we achieve", which makes their drift expensive
rather than merely untidy.

Three properties of the source material shape everything below.

**Most of these dates are not days.** The real lists contain `27.09.2026`,
`2026 I kvartal`, `2027 II poolaasta` and bare `2026`. A model that stored a
`DateField` would have to invent 1 April for a second quarter, and every page
downstream would then read that invention as a commitment.

**A law does not commence once.** One act routinely has a main body, particular
provisions eighteen months later and a register abolished on a third date. Some
commence *üldises korras* with no date at all, and some have no date decided yet.

**A `Töövõit` is a judgement.** The master specification already says so
(3.5, 6.6): it is a reviewed claim, not a computed consequence of an outcome
field. The eventual Proposal → Outcome → Attribution architecture does not exist
yet, and the department needs to record wins now.

## Decision

### These are first-class models, not tags

`taxonomy.Tag` is thematic classification: what a Matter is *about*. A
commencement is a legal fact with a date and a source; a work victory has an
approver, a timestamp and a review state; a milestone is a period with a
precision. None of them is a theme, and each needs validation, status
transitions, provenance and audit that a tag assignment cannot carry. The UI may
render a badge; the record underneath is structured.

Three models — `MatterImportantDate`, `MatterEffectiveDate`, `MatterWorkVictory`
— in a new `app.intelligence`. Not one generic event table: they share date
mechanics and nothing else, and merging them to save two tables would cost
exactly the meaning that makes them worth recording.

### A period is stored as its first and last day, plus its precision

`date_value` is the period's **first** day and `period_end` its last, both
derived by `app.workflow.dates` from what a person chose. `date_precision` says
how the value may be written, and `format_at_precision` is the only supported way
to write it: `01.04.2026` at `QUARTER` precision renders as *II kvartal 2026*,
never as a day.

Storing the end as well as the start is what makes two questions answerable in
SQL rather than in Python:

- **Ordering.** `(date_value, period_end, id)` gives the documented rule: by
  period start, then — among periods starting on the same day — narrowest first.
  01.07.2026, then III kvartal 2026, then II poolaasta 2026. No precision-rank
  expression is needed, because a shorter period simply ends sooner. A wider
  period that begins earlier still sorts earlier: the year 2026 leads the year it
  covers, because it starts in January.
- **"Has this passed?"** decided on the *end*. II poolaasta 2027 has not passed
  on 2 July 2027, and comparing the anchor would say it had.

Year filtering compares the anchor's year, which is exact: every precision the
product offers sits inside one calendar year. Narrower interval filtering is
deliberately not offered — a day-level filter over a quarter-level fact would
answer a question the data cannot support.

### Commencement kind, not a nullable date with a convention

`KNOWN_DATE`, `GENERAL_ORDER`, `UNKNOWN`, with a database CHECK constraint: only
`KNOWN_DATE` may carry a date, and it must. "Jõustub üldises korras" is a real
legal statement rather than a missing value, and a placeholder day stored against
it would be indistinguishable from a real one on every page that reads the table.

### The department views are generated, and nothing is copied

`/olulised-tahtajad/`, `/joustuvad-aktid/` and `/toovoidud/` are built from the
three tables. There is no second list to maintain, so editing a commencement on
the Matter *is* editing the department page.

The combined calendar shows watched milestones and known-date commencements
together, each labelled with its own event kind, as a SQL `UNION ALL` over two
authorized querysets projected onto the same columns. A union rather than a
Python merge, so the count above the list and the rows in it come from one
statement and pagination cannot drift; each side is authorized independently
before the two are combined.

### Business write authority is a role, in the existing chokepoint

`may_write_business_content` and `may_review_work_victory` live in
`app.core.authorization` beside the visibility predicates, not in a second
authorization module. SPECIALIST and DEPARTMENT_HEAD may write — these lists are
maintained collaboratively today and narrowing authorship to the Matter owner
would make the product slower than the OneNote page it replaces. READER may not.
ADMINISTRATOR gains nothing from being one, for the same reason it gains no sight
of RESTRICTED content (specification 5.2). Confirming or rejecting a work victory
is DEPARTMENT_HEAD only: it is the Chamber's own claim about its influence.

Reading follows the existing derived-visibility rule unchanged:
`VisibilityInheritingModel` plus `child_visibility_q`, nothing stored.

## Alternatives considered

**Tags with a date convention.** Cheapest, and it loses precision, validation,
review state, provenance and audit at once.

**One `MatterEvent` table with a `kind` column.** Fewer migrations, and every
constraint becomes conditional on `kind` — including the one that keeps a
fabricated commencement date out of the database.

**Storing only the anchor and deriving the end in SQL.** Possible with a
`CASE` expression over the precision, and it puts the definition of "has this
passed" into every query that asks. The stored `period_end` is derived at write
time by one function, guarded by a CHECK constraint and by a service-layer check
that recomputes it.

**`quantity = 2` for "2 töövõitu".** Refused. A count cannot be described,
reviewed or linked to evidence. Two achievements are two records; a legacy line
that says only "2 töövõitu" will become one candidate preserving the raw wording
for review.

**Cloning a commencement into the milestone table so one query serves the
calendar.** Refused. Two rows for one fact is the defect this stage exists to
remove; the calendar is a presentation problem, not a storage one.

**HTMX fragment swapping for the capture forms.** The Matter overview is
rendered by `app.matters.views` from its own context builders, and swapping part
of it from another app would couple the two. The forms are small server-rendered
pages that POST and redirect back to the section anchor.

**Indexing the structured text in `SearchDocument`.** Deferred, not rejected.
A structured fact deserves its own `SearchSourceKind` row so a result can say
which record matched, which means new nullable foreign keys on `SearchDocument`
and new signal wiring in a module Stage 2E.1 is editing concurrently. Recorded
as a follow-up in `docs/open-decisions.md`.

## Consequences

- The department's three lists become one place each, and the pages that read
  them cannot go stale.
- Adding a fact takes a form with two or three fields, and never asks anybody to
  type an anchor date.
- `NextAction.display_date` now delegates to `app.workflow.dates`, so the
  precision vocabulary has exactly one rendering.
- One more top-level navigation item — *Jälgimine* — with three tabs under it,
  rather than three more links in the shell.
- A work victory in this model carries no evidence link and no attribution. It
  is a marker a person set, and nothing computes influence from it.

## Reversibility

High for the page layouts, the filters and the combined-calendar presentation.
Moderate for the models: a later Proposal/Outcome/Attribution architecture can
reference or migrate `MatterWorkVictory` rows, because the Matter, the period,
the wording, the approver and the timestamp are all recorded. Low for two
principles, and deliberately so: a period is stored with its precision and never
rendered as a day it does not have, and a commencement whose date is unknown
carries no date at all.
