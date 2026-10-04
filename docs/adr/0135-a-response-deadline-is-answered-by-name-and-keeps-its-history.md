# 0135 — A response deadline is answered by name and keeps its history

**Status:** accepted
**Date:** 2026-10-04

**`Arvamuse tähtaeg` is a request with a history, and only an answer that names
it ends it.** `Matter.response_deadline` holds the current deadline and nothing
else; every deadline that stops being current is kept in
`MatterResponseDeadline` with what happened to it. A `Koja arvamus` save may
name the deadline it answers, the rounds whose wait it ends and the plan step it
does — and nothing it does not name moves. A `LISA TEEMALE` record may be named
as a `Tööplaan` step's work.

**Four migrations, all additive:** `matters/0045` (`Matter.response_requested_at`,
the `MatterResponseDeadline` table), `matters/0046` (`MatterSaveOnce`),
`workflow/0012` (`MatterPlanStep.fulfilled_by_operation` /
`fulfilled_by_record`), `audit/0033` (`RESPONSE_DEADLINE_ENDED` on the event
choices, no SQL). **No data migration and no backfill.**

## Context

The historical replay of 2026-10-04 (findings F-005, F-006, UX-002, UX-005)
found:

* a replaced deadline vanished from every surface but the audit trail;
* an answered deadline read as owed, in the warning colour, in the header and
  the register — even on a closed, enacted file;
* any sent opinion, sent for any earlier request, discharged a later one
  (`work_items._discharge_exists` compared no dates and no requests);
* sending the opinion finished nothing it answered: the step, the round and the
  deadline were each closed by hand, and one was forgotten in two of five cases;
* `Tööplaan` never noticed work recorded from `LISA TEEMALE`, and a closed file
  kept offering its suggestions.

## Decision

### 1. One owner per fact

| fact | owner |
| --- | --- |
| the current deadline | `Matter.response_deadline` |
| when it was recorded as a request | `Matter.response_requested_at` |
| every deadline no longer current, and how it ended | `MatterResponseDeadline` |

A deadline ends with one of `ANSWERED` (naming a sent `Submission`, or with the
person's own explanation — creating no `Submission` and counting in no
statistic), `NOT_ANSWERING`, `SUPERSEDED`, `MOVED`, `CANCELLED`, or `CLOSED`
(left behind by a closed file at reopening). Ending it clears the current field,
or gives it its successor, in the same transaction
(`app/matters/response_deadlines.py`).

### 2. Asked, never inferred

* A date where there is none is a new request, recorded now.
* A different date on an existing deadline asks: **the same request moved**, or
  **a new request** — which ends the old one with an outcome the person chooses
  in the same form. A new request is never an answer to the old one.
* Emptying the field withdraws the deadline, unless an outcome is chosen.
* `Lõpeta tähtaeg` ends it as answered, declined or withdrawn.
* Reopening a closed file carries its deadline forward only when the person
  ticks `Arvamuse tähtaeg … kehtib edasi`; closing answered nothing.

Both doors — the header editor and `Muuda teemat` — ask the same questions from
one partial. A stale tab (the deadline's revision) is refused.

### 3. The legacy reading is kept

A deadline with `response_requested_at IS NULL` — every deadline from before this
release, and every one an importer writes — keeps ADR 0059's reading: any
visible sent opinion or the register's `VÄLJA` discharges it. A deadline recorded
as a request is discharged only by an answer that names it. Nothing is
backfilled: no request time, no answer and no link is invented for an old
deadline.

### 4. Settled is not owed

A deadline on a closed file, or a legacy deadline the old reading discharges, is
drawn without the warning colour and says why («teema suletud», «lõpetatud») in
the header and the register's Kuupäev cell. An ended deadline reads quietly in
the header when none is current, and every ended one is listed in the rail's
`Arvamuse tähtajad`. A linked opinion withdrawn since leaves the answer
«vajab ülevaatamist»; nothing reopens automatically.

### 5. The opinion finishes what it names

The `Koja arvamus` form offers, all unticked: `Vastab arvamuse küsimisele`
(the current deadline, carrying its revision), `Märgi praegune tegevus tehtuks`
(unchanged, ADR 0126), one `Lõpeta kaasamise tagasiside ootamine` box per open
round, and `Tööplaani samm, mille see täidab`. Everything named is checked under
the Matter's lock before a byte is stored; any refusal refuses the whole save.
A round finished this way gets no invented summary. The history keeps one
«Arvamus välja» row with what it finished under it — read off the explicit
`MatterResponseDeadline.submission` link and the save's own operation id.

A drawn form carries a one-time token (`MatterSaveOnce`): a second press of the
same form is refused rather than recorded as a second opinion.

### 6. The plan learns from recorded work, by name

A `Kaasamine`, a published `Ülevaade / uudis` or a `Koja arvamus` saved from
`LISA TEEMALE` may name one plan step of its kind still ahead (suggested or
planned, not current). That step becomes `COMPLETED` with
`fulfilled_by_operation` / `fulfilled_by_record` — read as «Tehtud salvestatud
tööga», distinguishable from a step started and finished. No `NextAction` is
created to be finished at once; no other action is touched; nothing is matched
by title. The current step is still finished only through its own action.

On a closed file the plan is folded into `Tööplaan teema sulgemise ajal`, with
no `Alusta` and no `Soovitatud järgmisena`; its steps keep their states.

## Not changed

`workflow_one_open_action_per_matter`; a deadline creates no `NextAction`
(ADR 0133 §8); a round's lifecycle (ADR 0132); evidence before `SENT`; the
register importers; `Teema käik` periods (ADR 0131); search.

## Out of scope (recorded, not built)

Relative or EIS-only deadlines; a candidate / not-realised work-win lifecycle;
WAIT/MONITOR steps; repeated procedural phases on the rail; historical event
times for periods, closure and documents.

## Reversibility

Unapplying the four migrations drops the column, the two tables, the two plan
columns and the event choice, losing only history recorded after this release.
Restoring the ungated `_discharge_exists` restores the old discharge reading.
