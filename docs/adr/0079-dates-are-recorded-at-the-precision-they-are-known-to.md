# 0079 — A planned date is recorded at the precision it is actually known to

**Status:** accepted
**Date:** 2026-09-14

**Narrows** ADR 0011 (`NextAction` modelling) on one point only: the day a
`DO` + `DEADLINE` step becomes late. Everything else that ADR settled — one open
step per Matter, supersession rather than deletion, which combination can be
late at all — is unchanged.

**Narrows** ADR 0074 §11 (`+ Oluline tähtaeg` offers `Täpne päev` / `Kuu` /
`Kvartal`) by adding `Aasta` to that set, and extends the same control to three
more operations.

## Context

`DatePrecision` has existed since Stage 1 and `app/workflow/dates.py` since
Stage 2G. The vocabulary, the period arithmetic and the rendering were all
already right: *II kvartal 2026* is stored as `2026-04-01` + `QUARTER` and
printed with `format_at_precision`, never as `01.04.2026`.

Two things were not right.

**A lawyer could not say it.** Of the four Teema facts where a period is the
honest answer, only `Oluline tähtaeg` offered a precision control, and it
stopped at `Kvartal`. `Järgmine tegevus` took a day. `Jõustumine` took a day.
`Töövõit` took no date at all. So a person who knew *«oktoobris»* had to either
invent 1 October or leave the field empty, and both answers are worse than the
one they had.

**And where a period was stored, it was read as its anchor.** The stored first
day is an ordering anchor, never a fact — but four separate lateness queries and
`NextAction.is_overdue` compared it against today. A step planned for
*september 2026* was reported late on 2 September. `days_late` compounded it:
`«30 p üle»` on 1 October, for a month the lawyer had not overrun by a day.

## Decision

### 1. Four precisions, offered wherever a person states a planned date

`Täpne päev`, `Kuu`, `Kvartal`, `Aasta` — on `Järgmine tegevus` (create **and**
edit), `Oluline tähtaeg`, `Jõustumine` and `Töövõit`. One composer pattern, one
set of prefixed fields, one normalisation through `bounds_for`.

### 2. The anchor is the first day of the period, and is internal

Unchanged from Stage 2G, restated because everything below depends on it. The
anchor exists so that a period has a place in a sort. It is not a day anybody
named, and no surface may print it as one.

### 3. A date is displayed at the precision it was recorded to

`format_at_precision`, or a model property built on it. `2026-10-01` +
`QUARTER` reads *IV kvartal 2026* on every surface that shows it — the Teema
page, the register, Minu asjad, Osakond, the timeline, a work-item row.

### 4. A `DO` + `DEADLINE` step becomes late only after its whole period ends

| Recorded | Not late through | Late from |
|---|---|---|
| `15.09.2026` | 15.09.2026 | 16.09.2026 |
| `september 2026` | 30.09.2026 | 01.10.2026 |
| `III kvartal 2026` | 30.09.2026 | 01.10.2026 |
| `2027` | 31.12.2027 | 01.01.2028 |

Which combination may be late is unchanged: only `DO` + `DEADLINE`. A `WAIT` on
a ministry is not a failure, and widening the date rule does not widen the
population.

`app/workflow/lateness.py` states this once, in both readings the product needs
— a Python predicate for a loaded row and a `Q` for a queryset — because the
four surfaces that count lateness had four copies of the comparison.

### 5. `days_late` counts from the period's last day

One day late on 1 October for a September plan, not thirty.

### 6. `due_for_review` keeps its own boundary, deliberately

*«Vaatan üle oktoobris»* may come round when October opens. *«Plaanis
oktoobris»* is missed only when October closes. A review date is a reminder and
a deadline is a promise; the two boundaries are a month apart on purpose and
neither moves to match the other.

### 7. `HALF_YEAR` is historical compatibility, not a new choice

Still stored, still rendered, still correct in every date comparison. Not
offered as a new-entry chip: the register's own vocabulary uses halves and the
product's does not, and a fifth chip nobody picks is a fifth chip everybody
reads.

### 8. `INFERRED` is provenance, not vagueness

It records that a day was read out of free text. It behaves as an exact day
everywhere, and is likewise not offered for new input.

### 9. An unrelated edit never coerces a precision the UI cannot offer

Editing a step's *text* preserves a stored `HALF_YEAR` or `INFERRED` and its
anchor. Only an explicit choice of a supported precision replaces it. The
alternative — silently rewriting *II poolaasta 2027* to *juuli 2027* because
somebody fixed a typo — is the product editing a record nobody asked it to.

### 10. `Töövõit` asks for its period, and nothing is invented for it

A new `MatterWorkVictory` requires an explicit period; it is not defaulted to
today and not defaulted to the current year. Existing rows with no period keep
none. **No backfill, no guessed year, no data migration.**

### 11. These dates stay exact

`Matter.response_deadline` (`Arvamuse tähtaeg`), `Matter.received_date`,
`Kaasamise kuupäev`, `Tagasisidet ootame kuni`, a `Submission`'s sent date,
document dates, closure dates and `Entry.occurred_at`. Each is a day somebody
recorded or owes, not an estimate of somebody else's timetable.

### 12. Anchor equality is not fact equality

An approximate `NextAction` whose anchor happens to equal a Matter's
`Arvamuse tähtaeg` does **not** suppress the secondary response-deadline line
(PR #208's duplicate-prevention). *IV kvartal 2026* and
*Arvamuse tähtaeg 01.10.2026* are two different facts that share one internal
number; hiding the second because of the first would erase an official
obligation on the strength of a value the reader cannot see. Exact-date
duplicate suppression is unchanged.

### 13. No schema

The models already carry precision and, where a period is stored, its end.
Both implementing pull requests add **zero** migrations, and Django agreeing
(`makemigrations --check`) is part of each one's gate.

## Alternatives considered

**Store a `period_end` on `NextAction`.** Rejected: the anchor plus the
precision already determine the end, a stored copy is a second source of truth
that can disagree with the first, and it would be a migration on a hot table for
arithmetic that costs nothing.

**Make `due_for_review` period-end too, for consistency.** Rejected: the two
questions are different, and the consistency would be cosmetic. §6.

**Offer `Poolaasta` and `Tuletatud tekstist` as chips, since they exist.**
Rejected: a vocabulary a person must choose from is not the same list as the
vocabulary the database must understand. §7, §8.

**Backfill undated `Töövõit` rows to their Matter's year.** Rejected: it would
manufacture exactly the certainty this whole record exists to refuse.

## Consequences

Some rows that were displayed as late stop being late — correctly, and on the
day the change ships. A department head's overdue count can fall without
anything being done, and the release note should say so.

Every lateness query now carries a five-branch `OR` on `date_precision`. The
existing `(status, kind, target_date)` index still serves it; the query-cost
tests hold the budget.

## Reversibility

High. `app/workflow/lateness.py` is the whole semantic change and reverting it
restores the anchor comparison. The composer is additive: removing a chip leaves
every stored row readable, because the storage model did not change.
