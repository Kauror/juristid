# ADR 0046 — Tähtajad in two groups: the calendar week whole, the rest of the month behind «Näita veel», and one line past it

- Status: accepted; **superseded in part by ADR 0049**, which merged Ülevaade's
  *Tähtajad* and Osakond's *Eesolev* into one panel of five windows. The two
  boundary cases this record works out are unchanged and are why that panel
  partitions; what no longer holds is *Kaugemal* as a one-line summary — it is
  a real list there.
- Date: 2026-08-29
- Stage: pre-QA (shared-gate development phase)
- Related: ADR 0033 (`?too=` makes the dated-work populations addressable),
  ADR 0043 §3 (one parameterised `?too=tahtaeg-vahemik`, and the four windows
  this record replaces)

## Context

Ülevaade's *Tähtajad* panel cut the department's dated commitments into four
windows: this week, next week, thirty days, and everything beyond. Three
problems with that, and only the first is cosmetic.

**The near window was too narrow to be a week.** It ran from *today* to Sunday.
On a Wednesday it answered "what is left of my week", which is not the question
anybody asks out loud — they ask what is *in* the week, and Monday's date is
part of the answer whether or not it has passed.

**The middle two were arithmetic, not a horizon.** "Next week" and "thirty days"
are two headings over one planning question, and the second of them ends in the
middle of a week nobody chose: on the 4th of a month, *30 päeva* ran to the 3rd
of the next one, so a deadline on the 2nd and a deadline on the 5th sat in the
same group while the month turned over between them.

**Four headings is more structure than the content carries.** A department with
eleven dated commitments in a month was reading four group headers, three of
them over one or two rows.

## Decision

### 1. Two groups a reader plans in, and one line past them

*Sel nädalal* is the **calendar week**: Monday to Sunday, cut by the calendar
rather than counted from today. It is never truncated — nine deadlines in a week
print nine rows — because the whole point of the group is that somebody can see
their week without opening anything.

*Ülejäänud kuu* is what is left of the calendar month after that Sunday. Five
rows, and the rest behind the existing «Näita veel N ▾», which opens where the
reader is standing.

*Kaugemal* stays, unchanged, as one line: the next date and how many more sit
behind it. It is not a third group in the design sense — it is the guard that
stopped a deadline five weeks out from being on no screen at all (ADR 0043,
design handoff 1a), and removing it while narrowing the horizon from thirty days
to a month end would have reopened exactly that hole.

### 2. The week is cut by the week, and the month by the month

Two boundary cases decide the whole design, and they are handled in one function
(`overview.deadline_windows`) rather than in three expressions written where they
are read:

- **A week that starts in the previous month.** Monday 31.08 read on Wednesday
  02.09: *Sel nädalal* holds 31.08 all the same, and *Ülejäänud kuu* still starts
  on 07.09. The cut between the groups is the **week's end**, never the month's
  start, so a date is in exactly one of them.
- **A week that runs past the month end.** Monday 28.09, Sunday 04.10: there is
  no rest of September after that Sunday, so the middle window is returned as
  the empty interval it is and the panel omits it — and *Kaugemal* begins the day
  after the **week**, not the day after the month, or the first four days of
  October would be in two windows at once.

The three windows touch and the last is open-ended, so every dated commitment is
in exactly one. Asserted over every day of a year, because the boundaries move
with the weekday *and* with the length of the month.

**Rejected:** clipping the week at today, which is the behaviour being replaced.
It makes the group a rolling seven days wearing a calendar's name: a date on the
list on Tuesday is off it on Wednesday, and the panel moves under somebody
halfway through the week they are reading.

**Accepted consequence:** a deadline between Monday and yesterday is now in *Sel
nädalal* **and** in *Üle tähtaja*. That is the point of the week group rather
than a defect of it — the week is what somebody looks at to see their week,
missed days included — and *Üle tähtaja* remains the count of what is actually
late.

### 3. The panel's windows are parameterised, and the fixed names are left alone

Every group now links through `?too=tahtaeg-vahemik&too_alates=&too_kuni=`, the
one parameterised population ADR 0043 §3 introduced for Osakond's *Eesolev*. The
same selector answers the header's count and the list behind «kõik N →», so the
two cannot disagree.

This **supersedes** the last paragraph of ADR 0043 §3, which recorded that
`tahtaeg-30` and `tahtaeg-kaugemal` "complete Ülevaade's deadline panel". They no
longer feed it. All four fixed deadline names — `tahtaeg-nadalal`,
`tahtaeg-jargmisel`, `tahtaeg-30`, `tahtaeg-kaugemal` — are **kept** as register
populations: they are in the register's own control, they are in people's
bookmarks and pasted links, and none of them has become untrue. What changed is
which of them a panel reads, not what any of them means.

`WORK_DEADLINE_THIS_WEEK` in particular is deliberately **not** redefined to the
calendar week. It is a different question with the same words: Osakond's SEIS
strip asks what is still ahead of the department this week, and a figure that
started counting Monday's missed deadline as upcoming would disagree with «üle
tähtaja» standing next to it.

**Rejected:** two new fixed populations, `tahtaeg-kalendrinadal` and
`tahtaeg-kuu-lopuni`. They would be two more names in a dropdown that already
has one called "Tähtaeg sel nädalal" meaning something adjacent, for windows
that move with the calendar and can therefore only ever be approximated by a
name.

### 4. Nothing about a deadline itself changed

`real_deadlines` is untouched: a DO deadline and an `Oluline tähtaeg` are what
the department may honestly call a deadline, and a WAIT's expected date and a
MONITOR's review date stay in the intervention list (master specification 18.8).
So is date precision — a commitment recorded to a month still prints *september
2026* and suppresses its weekday letter, because the window arithmetic reads the
sort anchor and the row renders the recorded precision (master specification
3.5).

The row partial, the disclosure partial and every `uxdl` class are reused as they
stand. The change adds no CSS and no JavaScript: «Näita veel» is the existing
`<details>`, which works with scripting switched off.

## Consequences

- One month-end helper, `app.core.dates.end_of_month`, shared by this panel and
  Osakond's *Eesolev*; the private copy in `department_dashboard` is gone, as is
  its private `_week_start` in favour of `work_items.start_of_iso_week`.
- `DEADLINE_PREVIEW` is 5, matching Osakond's `UPCOMING_PREVIEW`: the two panels
  answer the same question at different scopes.
- The `ulevaade` visual baselines change — the group headings are part of the
  baseline by design, and only the dates and ranges inside them are masked.
