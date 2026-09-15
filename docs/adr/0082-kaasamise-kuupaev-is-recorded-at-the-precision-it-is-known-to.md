# 0082 — `Kaasamise kuupäev` is recorded at the precision it is known to

**Status:** accepted
**Date:** 2026-09-15

**Narrows ADR 0079 §11** for exactly one of the dates on its list.
`Kaasamise kuupäev` — `MatterEngagement.occurred_on` — leaves the exact-only
set and takes the four precisions ADR 0079 §1 already offers elsewhere.
Everything else on that list is unchanged, and `Tagasisidet ootame kuni`
(`MatterEngagement.feedback_deadline`) is named here explicitly because it sits
on the same model and in the same form and **stays exact**.

**Completes ADR 0078 §2.** That record made the engagement date visible,
optional and correctable, and left «date *precision* for either date» in its
own *Explicitly not decided here* list. This decides it, one date at a time.

## Context

ADR 0078 §2 removed a false fact: `+ Kaasamine` was stamping
`timezone.localdate()` on every consultation it wrote, so a round held in March
and typed up in September was filed as a September round. The panel now asks,
and an emptied box stores `NULL` — «kuupäev teadmata».

Using that on real files produced the next question immediately, and it is the
question ADR 0079 was written to answer for four other facts. **A lawyer
entering a historical consultation frequently knows the period and not the
day.** «Küsisime liikmetelt kevadel 2019.» «See oli oktoobris.» A mail folder
gives a month; a register row gives a year. The two answers available were an
invented day and an empty field, and ADR 0079's own reasoning about
`Järgmine tegevus` applies here word for word: both are worse than the one the
person actually has.

ADR 0079 §11 put `Kaasamise kuupäev` on the exact list on a rule that is right
about the rest of that list and wrong about this entry:

> Each is a day somebody recorded or owes, not an estimate of somebody else's
> timetable.

`Matter.response_deadline`, `Matter.received_date`, a `Submission`'s sent date,
document dates and closure dates are all days *this office* recorded or owes,
and each of them is known exactly because this office was there. `Kaasamise
kuupäev` is not one of those. It is a statement about **when something
happened out in the world** — a mailing that went out, a questionnaire that was
open, a consultation round that ran — and it is routinely written down by
somebody reconstructing it afterwards. That is the same shape as
`Oluline tähtaeg` and `Jõustumine`, which is why those got the control.

`Tagasisidet ootame kuni` is the opposite, and the two sit side by side in one
panel. It is a day somebody *named to other people*: «vastake 22. septembriks».
A round asked for by «oktoobris» is not a thing anybody was told. There is
nothing for a period to mean, so there is nothing to offer.

## Decision

### 1. Four precisions on `Kaasamise kuupäev`, and no fifth

`Täpne päev`, `Kuu`, `Kvartal`, `Aasta` — ADR 0079 §1's set exactly.
`HALF_YEAR` remains a stored, rendering, comparing precision and is not
offered as a chip (§7); `INFERRED` remains provenance and is not offered (§8).
A record already carrying one of those keeps it through an unrelated edit, by
ADR 0079 §9's «Muutmata» chip, which is built per record and therefore also
refuses a crafted `HALF_YEAR` on a record that never had one.

**No half-year.** The department does not plan or remember in halves, and the
argument ADR 0079 §7 makes about a fifth chip nobody picks is unchanged by
this record.

### 2. `Tagasisidet ootame kuni` stays exact, and stays inert

No precision control, on either form. It keeps its place on ADR 0079 §11's
list, and it keeps every property ADR 0078 §3 gave it: no `NextAction`, no
`MatterImportantDate`, not `Matter.response_deadline`, no work item, no overdue
badge, no count, no statistic, no filter, no register sort, no search row, no
archive projection, not indexed.

The one rule relating the two dates is unchanged in substance and had to be
restated in implementation: a reply-by date before the engagement is a typo and
is refused on the deadline field. It now compares against the **anchor** — the
period's first day — so «kaasamine oktoobris, vastuseid 15. oktoobriks» is
accepted and «vastuseid 20. septembriks» is refused. Comparing against the
period's *end* would refuse the commonest thing a month-long round says.

### 3. One control, one stored meaning, and the anchor is never printed

The existing `matters/partials/period_composer.html` and the existing
`app.workflow.dates.bounds_for`. A quarter stated in `+ Kaasamine` is the same
stored anchor as a quarter stated on `+ Oluline tähtaeg`, or one period would
sort into two places.

ADR 0079 §2 and §3 apply unchanged: the stored date is the period's first day,
it exists so a period has a place in a sort, and **no surface may print it as a
day**. That obligation reaches further here than it did for the four facts of
ADR 0079, because an engagement date is read in more places:

| Surface | Reads |
| --- | --- |
| Teema chronology row | *oktoober 2025*, never `1.10.2025` |
| The correction form | the period's own chips, with the day box **empty** |
| *Viimane tegevus* (register, Minu asjad) | `MatterActivityFact.display_date` |
| *Viimati muudetud* (Minu töö) | `compact_display` — `10.25`, or the period spelled out |
| The `ENGAGEMENT_CHANGED` audit payload | the anchor **and** the precision |

`MatterActivityFact` gains a `date_precision`, defaulted to `EXACT` — not as a
placeholder for an unknown answer, but because eight of its nine bases are days
by construction. `Kaasamine` is the only one that may be a period.

### 4. One additive column, and deliberately no `period_end`

`MatterEngagement.occurred_on_precision`, `varchar(16) NOT NULL DEFAULT
'EXACT'`, with a `CHECK` on the `DatePrecision` vocabulary. One migration,
`matters/0021_engagement_occurred_on_precision`.

Named for its date rather than `date_precision`, which is what the three
Stage-2G facts call theirs: this model carries **two** dates and the other one
is exact-day-only, so a bare `date_precision` beside them would read as
qualifying both.

**No `period_end`.** `MatterImportantDate` and `MatterEffectiveDate` store one
because their question is *has this passed yet* — a question about a period's
last day, asked in SQL, against rows that are in the future. An engagement is
something that already happened. It is never late, never overdue, never a
deadline and never a work item, and the two readers that treat `occurred_on` as
a number — the *Viimane tegevus* maximum and the register's activity sort —
order on the anchor, which is exactly what an anchor is for. `NextAction`
reached the same conclusion in ADR 0079's *Alternatives*, and for the same
reason: a stored copy of derivable arithmetic is a second source of truth that
can disagree with the first.

### 5. An unknown date has no precision

`NULL` + `MONTH` is a period with nothing to qualify, and a row in that state
renders as neither a date nor «kuupäev teadmata» but as whichever of the two
the reading surface guessed. So a missing date normalises the column back to
`EXACT`, in the service, on every path — including a correction that clears
`Kaasamise kuupäev` on a record that was stored as a month.

The inverse matters more: **widening the control must not turn «I do not know»
into an approximation.** An empty box is still `NULL` and still reads «Kuupäev
teadmata». No path invents today, `created_at`, or a year taken from the note,
a provider link or the parent Matter.

### 6. No backfill, no inferred historical precision

Every existing row was written through a box that asked for a day, so `EXACT`
is not a default standing in for an unknown answer — it is what those rows
actually mean. The field default states it once; there is no `RunPython`, no
table rewrite and no guess.

## Alternatives considered

**Leave ADR 0079 §11 alone and keep both dates exact.** This is what the
implementing branch originally chose, on a defensible rule — an editor must not
offer a precision the creating panel cannot write. The rule is right and the
conclusion was wrong: the answer is to give *both* surfaces the control, which
is what this record does. Keeping them exact leaves a lawyer entering a 2019
consultation choosing between inventing a day and recording nothing, which is
the situation ADR 0079 exists to end.

**Give `Tagasisidet ootame kuni` the same control, for symmetry.** Rejected.
Two boxes in one panel are not two of the same thing. A reply-by date is a day
stated to other people; «palun vastake oktoobris» is not a deadline anybody was
given, and a control offering it would invite a record nobody can act on. §2.

**Store a `period_end` beside the anchor, like the Stage-2G facts.** Rejected.
§4. Nothing asks whether an engagement has passed.

**Add `HALF_YEAR` as a chip, since the register uses halves.** Rejected, on
ADR 0079 §7's reasoning, which this record does not reopen.

**Let an approximate engagement contribute its period's *end* to
*Viimane tegevus*, so a month-long round counts as recent for longer.**
Rejected. The column answers *when did work last happen*, and the end of a
period is a day in the future for a round still running — it would report
activity that has not occurred. The anchor is conservative in the safe
direction and is what the SQL twin already orders on.

**Drop an approximate engagement from *Viimane tegevus* altogether, since the
column is a day column.** Rejected: a file whose only recorded work is
«kaasamine oktoobris» has had work happen on it, and reporting that as *nothing
known* is the failure ADR 0026 and `app/matters/activity.py` exist to correct,
arriving through a new door.

## Consequences

A `Kaasamine` can now say something it could not say before, and an existing
one can be corrected into saying it. Nothing that exists today changes meaning,
renders differently, or moves in any sort: every stored row is `EXACT`, and
`display_date` for an exact date is byte-for-byte the `j.n.Y` those surfaces
already printed.

The register's *Viimane tegevus* column can now hold a value that is not a
date — *IV kvartal 2025* is wider than `1.10.2025`. The column is sized for
`j.n.Y` and a quarter is three characters longer; that is a real layout cost,
accepted because the alternative is printing a day nobody named.

`activity_engagement_precision` is a seventh subquery annotation on the
register's queryset. It is one more scalar subquery on a queryset that already
carries six, and it is applied at the same single chokepoint.

## Reversibility

High for the product, and additive in the schema. Removing the four chips from
the two forms leaves every stored row readable — the precision column keeps
working, `format_at_precision` keeps working, and nothing that was written
while the control existed becomes unreadable. Dropping the column itself would
lose what people had said, which is the usual cost of any additive column and
the reason this one is the smallest that answers the question.
