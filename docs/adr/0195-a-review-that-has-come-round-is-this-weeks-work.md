# 0195 — A review that has come round is this week's work; an expectation is not a review

- Status: Proposed (owner's decision 1 of the post-audit remediation brief,
  2026-10-29; 2026-10-28 audit finding B-02; not merged)
- Amends: 0121 §7 (a period that has not ended is never this week's) for
  reviews that have come round; the review rule's table in
  `app/workflow/lateness.py` for `EXPECTED_AROUND`
- Relates to: 0011 (only `DO` + `DEADLINE` can be overdue), 0079 §6 (a review
  keeps its own boundary), 0122 §2 (a period is never a day), ENG-040 (one
  review rule)

## Context

Two documented rules met in one row and nobody had decided the crossing. ADR
0079 §6 and ENG-040 say a review comes round on the **first** day of the
period it names: *«vaatan üle oktoobris»* is due from 1 October, and every
counter reads it so — `Ülevaatamiseks` (`?too=ulevaatamiseks`), *Vajab
sekkumist*, the manager's Kiirvaade, the `REVIEW_DUE` statistic, the register's
`?tegevus=ulevaatus` and the row's «ülevaatamise aeg on käes». ADR 0121 §7 says
a period that has not ended is never *Sel nädalal*. So on 29 October a MONITOR
review recorded as «oktoober 2026» was ripe everywhere and filed under
*Hiljem* on Minu asjad's timeline, outside the strip's «tegevust sel nädalal».

The same review rule also read an `EXPECTED_AROUND` date — the time somebody
else is expected to act, «vastus oodatav IV kvartalis» — as a reminder that
came round on the period's first day, which put an outside body's timing in
the review queue on 1 October. The rule's own table reserved the other reading
for the owner.

## Decision

1. **A review that has come round is in *Sel nädalal*** while its period runs
   (`work_items.band_of`: after the past-period branch, `is_review_ripe` →
   `BAND_WEEK`). It is due now, by the one review rule every counter reads; the
   band now says the same. It is never red and never late — `is_overdue` is
   untouched and only `DO` + `DEADLINE` can be overdue (ADR 0011).
2. **An `EXPECTED_AROUND` date is not an instruction to review.** It comes round
   on the **last** day of the period it names — the day itself for an exact
   or inferred day — and not because the period began
   (`lateness.expectation_has_come_round`, `expectation_due_date_q`; both
   readings, one definition, `review_due_q` choosing by date meaning). Until
   then it is not in `Ülevaatamiseks`, *Vajab sekkumist*, `REVIEW_DUE` or the
   register's review filter, and it stays in *Hiljem*. It never becomes an
   overdue deadline.
3. **Nothing else moves.** WAIT/MONITOR/DO, the `REVIEW_ON` boundary (period
   start), a review kind's `DEADLINE` date (period start, never late), DO
   lateness (period end, ADR 0079 §4), *Järgmised 30 päeva* (days only) and the
   rest of 0121 §7 — a period that has not ended and has not come round is not
   this week's — are unchanged.
4. **The `REVIEW_DUE` metric is version 2**: its population moved for
   expectations. The week figure's explanation (`seis_help.MY_WEEK_HELP`) states
   both boundaries.

## Consequences

- Scenario D of the remediation brief holds on every reader: on 29.10.2026 a
  MONITOR review «oktoober 2026» is current work (band, strip, counters,
  filters, calendar status) and a WAIT expecting a response «IV kvartal 2026»
  is neither overdue nor a review (`tests/test_mature_review_band.py`,
  `tests/test_review_due_rule.py`).
- A review recorded to a whole year sits in *Sel nädalal* from 1 January until
  somebody records «Vaatasin üle». That is the review rule's own consequence —
  a review stays due until it is reviewed — now shown where it is counted.
- No schema change, no data migration. Stored dates and precisions are read as
  before.

## Reversibility

One branch in `band_of` and one in the review rule; removing either restores
the previous reading without touching data.
