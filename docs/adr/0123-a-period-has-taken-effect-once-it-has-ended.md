# 0123 — A period has taken effect once it has ended, and it is never today

**Status:** accepted
**Date:** 2026-09-30

**The two readings docs/adr/0122 §2 audited and deliberately left out**, decided
together because they are one question: where a date recorded as a month, a
quarter or a year stands against today, on the surfaces that draw time.
**No migrations**: every record already carries its anchor, its `period_end`
and its `DatePrecision`.

1. **A `Jõustumine` recorded as a period has taken effect once the period has
   ended** — on `Teema käik`, on the `Menetluse kulg` strip and on its rail, the
   day `MatterEffectiveDate.has_passed` and Statistika already say so. Until
   then, from its first day, it reads «Jõustub».
2. **Every dated point on the strip and the rail is read by one function**,
   `app.matters.process_timeline.dated_state`: reached once its period has
   ended, today only for a day, ahead otherwise. **A period is never today** —
   not a commencement, not a watched `Oluline tähtaeg` on its last day, and not
   a step a person added to the rail on its anchor day.
3. **A period column sits at its period's last day**, as `Oluline tähtaeg`
   already did, so a column's position and its state are one date.

---

## The defect

A `MatterEffectiveDate` stores its period's first day as `date_value` (the
anchor, docs/adr/0079 §2) and its last day as `period_end`. Two readings of
«has it taken effect» ran side by side:

| Reads the anchor | Reads the period's end |
|---|---|
| `Teema käik` (`timeline.projected_milestones`): not drawn while the anchor is ahead, «Jõustus» once the anchor is behind, else «Jõustub» | `MatterEffectiveDate.has_passed` |
| The strip (`process_timeline.process_steps`): the column sat at `date_value` and read reached / today / ahead from it | Statistika's «teemat jõustunud aktiga» (`overview_strip._in_force_params`, a register containment window ending yesterday) |
| The rail (`legal_process.matter_rail`): `milestone.sort_on <= today` placed a commencement among what had happened | *Jõustuvad aktid* (`intelligence.selectors.effective_dates`, `PAST` / `HORIZON`), the calendar's `CalendarEntry.has_taken_effect`, the register's `?joustub_alates` / `?joustub_kuni` |

So on 15 October an «oktoober 2026» commencement read «Jõustus» in the
chronology and reached on the strip, while an «oktoober 2026» `Oluline tähtaeg`
on the same strip — which sits at `period_end` — read ahead, and Statistika did
not count the act as in force. «Jõustus» on 2 October is a claim that the act
is in force, made about a month in which nobody has said on which day it will
be.

And a step added to the rail (docs/adr/0119) and dated as a period compared its
anchor the same way (`legal_process._added_step`), so «Istung oktoober 2026»
carried `aria-current="date"` on 1 October — a day nobody named — and read as
reached from the 2nd.

## The rule

**Taken effect means the whole period is behind us.** It is the lateness
boundary (docs/adr/0079 §4) — «oktoober 2026» is over on 1 November — read
through the same `app.workflow.lateness.is_past_period`, and it is what
`has_passed`, Statistika, *Jõustuvad aktid*, the calendar and the register
window already answer. Nothing that already read the end changes; the three
surfaces that read the anchor now agree with them.

For «oktoober 2026»:

| day | `Teema käik` | strip | rail | Statistika | `has_passed` |
|---|---|---|---|---|---|
| 30.09 | — | ahead | ahead | 0 | no |
| 01.10 | «Jõustub oktoober 2026» | ahead | ahead | 0 | no |
| 15.10 | «Jõustub oktoober 2026» | ahead | ahead | 0 | no |
| 31.10 | «Jõustub oktoober 2026» | ahead — **not today** | ahead | 0 | no |
| 01.11 | «Jõustus oktoober 2026» | reached | reached | 1 | yes |

**A day is unchanged on every surface**: not drawn in the chronology before it,
«Jõustub» and today on the day, «Jõustus» and reached from the next.

**Statistika's figure is this year's**, so the day after a *year* — 1 January —
is the next reporting year, and a 2026 commencement is not in 2027's figure.
That is the figure's year, not a second rule, and
`tests/test_period_taken_effect.py` asserts it as such.

### `Teema käik`

* **Drawn once the period has begun** (`period_starts_after`), as before for a
  day, and still not drawn before: a commencement ahead of us is the strip's,
  not part of what happened.
* **«Jõustub» while the period runs**, its last day included. The row says the
  act takes effect in October, which is all anybody recorded. It is not marked
  «Eesolev»: that word is for a record whose whole period lies after today
  (docs/adr/0121 §3), and October has begun.
* **«Jõustus» once the period has ended.**
* **Still placed on its anchor**, like every other event in the list
  (`engagement_chronology_day`, docs/adr/0079 §2): the placement is not the
  description.

### The strip and the rail: one reading, `dated_state`

`dated_state(when, precision, today)` is the whole temporal grammar of
docs/adr/0074 §12.5 for a dated point, and both surfaces call it — the strip
for each column, the rail for each added step — instead of each comparing a
date:

* **reached** once `is_past_period(when, precision, today)`;
* **today** only when `when == today` and the precision is a day (`EXACT`,
  `INFERRED`);
* **ahead** otherwise.

`when` may be the anchor or the last day; both name the same period.

**A period is never today.** `aria-current="date"` and the accent ring say
«this is today», and neither end of «oktoober 2026» is a day anybody named —
docs/adr/0122 §2's rule for a window with a last day, applied to a column. So:

* a **commencement** recorded as a period is ahead through its last day;
* a **watched `Oluline tähtaeg`** recorded as a period, which already sat at
  `period_end`, is ahead on that last day rather than today (the next day it
  has passed and leaves the strip for the chronology, as before);
* a **step added to the rail** and dated as a period is ahead until its period
  has ended and reached after. It is read *exactly as a dated point does*,
  which is what `_added_step` always promised; the promise now holds for a
  period. This also moves the middle of the period — reached from the anchor's
  next day before, ahead now — because the rail reads every dated point by one
  rule.

**The rail places a point among what has happened by that same reading**
(`milestone.state in _REACHED_STATES`), not by a second comparison of its date.
For a day the two are identical. For a period they differ on its last day,
where the column's own date equals today while the period has not ended.

### Position and state are one date

A strip column is placed and read on `sort_on`, and the rail's fill runs from
one column's `sort_on` to the next (`--tl-reach`). A commencement placed on its
anchor but read on its end would have the fill run solid into a column still
drawn ahead, for the whole period. So **a commencement column sits at
`period_end`**, the position a watched `Oluline tähtaeg` has on the same strip,
and the order docs/adr/0121 §1 ranks milestones in (`milestone_order`: the one
due first). A step added to the rail and dated as a period likewise ends its
segment at the period's last day (its `RailStep.sort_on`); where it is *placed*
is unchanged — the place a person chose, or its own date when that anchor is
gone (docs/adr/0119 §2).

What that moves, and why each is right:

* **An exact point inside the period now reads before the period.** A response
  deadline on 20 October comes before «oktoober 2026», which may commence on the
  31st.
* **Two commencements «2027» and 1.7.2027** order the July one first, and a
  folded `Jõustumine` phase takes the first of its facts as its date
  (`_fold_into_phase`). «2027» may be 31 December; nobody said January.
* **An unfolded commencement's rail key** (`milestone:Jõustumine:<date>`) now
  names its period's end. A step somebody placed after one — possible only on a
  file with no pattern, or whose `Jõustumine` phase was hidden before the
  commencement was recorded — falls back to its own date until the panel is
  saved again, the gone-anchor rule docs/adr/0119 §2 states. That already
  happens whenever a commencement's date is edited.
* **On the period's last day** the connector into its column is full while the
  column is still ahead: the fill is measured in whole days, and today is at
  the column's position. The next day the column is reached. A fraction that
  stops short of a column on the day that column's date *is* today would be a
  second rule to say one day less, and it is not attempted.

## Considered and refused

* **Taken effect once the period has begun** — the anchor reading the three
  surfaces had. It says «Jõustus» about October on 2 October, and it disagrees
  with four surfaces and a documented register rule («a quarter is claimed only
  by a window that contains it»).
* **Moving `has_passed` and Statistika to the anchor instead.** Four surfaces
  and the register's containment rule would change, to agree with a reading
  that claims a precision nobody recorded.
* **A fourth strip state, «in progress».** The strip deliberately has no
  `is-current` (docs/adr/0074 §12.1, §12.5), and on the rail `Praegu` is the
  phase the file stands on. A commencement is not where the file stands.
* **Today for every day of the period.** `aria-current="date"` on 31, 92 or 365
  consecutive days is a claim about today made for a date nobody named.
* **«Eesolev» on a running commencement.** docs/adr/0121 §3 keeps that word for
  a period that has not begun; a running one has.

## The call sites (the audit)

Every comparison of a commencement, or of a date the strip or the rail draws,
with today:

| Surface | Read | Decision |
|---|---|---|
| `Teema käik` `Jõustumine` row (`projected_milestones`) | anchor | **→ drawn from the period's start (`period_starts_after`), «Jõustus» from its end (`is_past_period`)** |
| Strip `Jõustumine` column (`process_steps`) | anchor, position and state | **→ `period_end`, read by `dated_state`** |
| Strip `Oluline tähtaeg` column | `period_end`; today on the last day | **→ `dated_state`: never today** |
| Strip `Arvamuse tähtaeg`, `Tagasiside tähtaeg`, `Koja arvamus`, `Lõpetatud` | a day | `dated_state`; unchanged in effect |
| Rail milestone placement (`matter_rail`) | `sort_on <= today` | **→ the strip's state** |
| Rail added step (`_added_step`) | anchor | **→ `dated_state`, segment ending at `period_end`** |
| `MatterEffectiveDate.has_passed` | period end | correct, unchanged |
| Statistika «teemat jõustunud aktiga» | period end (containment, ending yesterday) | correct, unchanged |
| *Jõustuvad aktid* `PAST` / `HORIZON`; `CalendarEntry.has_taken_effect`; register `?joustub_alates` / `?joustub_kuni` | period end / containment | correct, unchanged |
| Rail phase evidence: a dated phase row (`_keep_recorded_phases`), a `Märge` dating a phase (`recorded_phase_dates`) | period start | **kept on purpose**: phase evidence asks whether a phase has *begun*, not whether a point has been passed (docs/adr/0121 §3) |
| `Teema käik` «Eesolev» (`dated_ahead`) | period start | kept on purpose (docs/adr/0121 §3) |

docs/adr/0122 §2's other left-out items — `defer_action` on a period step, the
dead or test-only anchor readers, and the two «30 p jooksul» captions — are
not touched here.

## Not changed

What may be entered (docs/adr/0121 §3); how a period is written
(`format_at_precision`); the words on every surface («Jõustub», «Jõustus»,
«Jõustumine», «Tulevikus»); the chronology's placement of a commencement on its
anchor; the phase states and the late-entry rule (docs/adr/0092 §13); the
lateness and review rules (docs/adr/0079 §4, §6); every surface's reading of a
day; the stored data.
