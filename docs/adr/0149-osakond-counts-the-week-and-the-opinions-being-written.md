# 0149 — Osakond counts the week's opinions and the opinions being written

**Status:** accepted
**Date:** 2026-10-09
**Amends:** 0049 §4 (the strip), ENG-019's `opinion_state_q` (what «koostamisel» reads), 0148 Consequences (Osakond's ARVAMUS KOOSTAMISEL)

The owner's KPI round. Osakond's strip said «N arvamust välja · 7 p», a rolling
window, and nothing on the page said how many opinions were being written. The
team table's ARVAMUS KOOSTAMISEL column existed, but in the 2026 Excel pilot it
read zero on every row: the selector behind it knew only a DRAFT `Submission` and
the imported register's blank `VÄLJA`, and since docs/adr/0061 (amendment of
2026-09-27) the interface starts no DRAFT — an opinion is recorded sent, in one
act, from `Lisa teemale → Koja arvamus`. The twenty pilot opinions being written
carried exactly what the native workflow records instead: an open «Koostan
arvamuse» step and an `Arvamuse tähtaeg` still owed.

Creating twenty DRAFT records to make the column reach twenty was considered and
refused with the owner: a draft the interface cannot finish or remove would have
stayed «koostamisel» after the real opinion went out through `Koja arvamus`. The
count is made correct from the facts the product already keeps.

## 1. «arvamust välja sel nädalal»

The sixth figure counts SENT opinions this reader may see whose **local** sending
date falls in the current ISO calendar week, Monday up to and including today
(`department_dashboard.sent_this_week`, through the existing `sent_submissions`).
Europe/Tallinn decides the date for both precisions: a DATE send is stored at
local midnight, a timestamp at its moment. Nothing is stored, scheduled or reset —
on Monday the window simply starts that day, and a send dated later this week is
not counted before its day. Still no link: the Arvamused workspace narrows by year
and month, not by week (DS-24). The team table's previous-week and year columns
are unchanged.

## 2. «arvamust koostamisel», last on the strip

A seventh figure, after the sent one: `drafting_matters(user)` — open FULL Matters
this reader may see with an opinion being written, at any horizon, overdue or
months away. It is the team table's ARVAMUS KOOSTAMISEL total by construction and
links to the register's `?olek=avatud&liik=FULL&arvamus=koostamisel`, the same
population (`tests/test_osakond_week_and_drafting.py` asserts count, column and
list by id). Informational, so no tone. The strip is five risks followed by two
informational figures; all seven show at zero.

## 3. One definition of «koostamisel», now reading the native workflow

`register_filters.opinion_state_q(user, "koostamisel")` — behind the strip
figure, the team column, the register filter and every future surface — is the
union of:

1. a DRAFT Submission the reader may see (unchanged; historical drafts still
   count, and a Matter with two is one row);
2. a CURRENT register row with a blank `VÄLJA`, unless a readable SENT exists
   (unchanged);
3. **new — the native opinion work:** an OPEN `NextAction` the reader may see that
   is the opinion step — the product's own sentence `OPINION_PREPARATION_TEXT`
   («Koostan arvamuse», docs/adr/0091 §1), or the work plan's opinion step,
   recognised by `PlanStepOperation.SUBMISSION` and never by its words
   (docs/adr/0133 §6) — **and** a response obligation still outstanding, read by
   `work_items.response_obligation_outstanding_q`, the population
   `response_obligations` lists.

Neither half of (3) alone is drafting: an open file with an unanswered deadline
may only be waited on or watched, and a step with no deadline owed is a plan. The
obligation is what ends it, with its existing semantics (docs/adr/0059, the
historical-regression round's requests): a `Koja arvamus` that answers the
deadline ends it, so the Matter leaves the count on the next read **even if the
old step is never marked done**; a send that does not answer a request-tracked
deadline leaves the work standing; a later request after an earlier opinion is
drafting again; a deadline from before requests were tracked — every imported
one — is discharged by any sent opinion. «Ei saatnud» ends the obligation as not
answering; closing the Matter leaves the open-FULL population.

**Native creation is unchanged** (0133 §8): a new Teema gets no step from its
deadline, so it counts only once a lawyer is on the opinion step.

## Consequences

- The 2026 Excel pilot reads 20 «koostamisel» (9 / 7 / 4 by owner) on the
  unchanged 35-Matter dataset, with **no** DRAFT Submission created and no pilot
  record changed. 0148's note that ARVAMUS KOOSTAMISEL «counts native drafts
  only» is superseded.
- No migration, no new model, no change to `Submission`, to sending, to the
  evidence rule or to follow-up scheduling.
