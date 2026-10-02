# 0132 — A `Kaasamine` is open until it is finished, and its deadline is optional

**Status:** accepted
**Date:** 2026-10-02

**Narrows ADR 0086 §3 and §6** on one point: what opens a consultation round.
Until now `feedback_deadline` *was* the lifecycle. A round with a reply-by date
was an open wait, a round without one was in no state at all, and
`matters_engagement_feedback_closure_needs_deadline` refused to let it be
completed. Now a round is open from the moment it is recorded, with a deadline
or without one, and `Lõpeta kaasamine` is the act that ends it. Everything else
in that record still stands: the one `WorkItem`, its owner, its due-day
reading, the closure on `close_matter`, the audit, and the list of things a wait
never creates.

**Narrows ADR 0091 §2** on two points. First, recording a new round is no longer
a completed act with an optional wait added on top: the round is the
consultation, and the consultation is open. Second, `Ootan tagasisidet` no
longer requires a date. The act survives, but only for rounds filed as history
(§3).

**Supersedes ADR 0086 §6's «clearing the deadline clears the closure»** and its
refusal of «a round with no `feedback_deadline` has no wait to finish».

## Context

The owner's brief (sections 4A, 9A, 9B and 51A) states as a hard product
requirement that `Tagasisidet ootame kuni` is optional. A consultation round must
work completely normally with no feedback deadline, with a deadline today, with
one tomorrow, or with one weeks away. A round with no deadline is a first-class
ordinary state: «we are collecting feedback, but there is no specific due date
recorded». It must stay visible as open work, be completable, take provider
links, files, counts and the round's feedback summary, and never become overdue
or receive a fabricated date.

The code said the opposite. `has_open_feedback_wait` was
`feedback_deadline is not None and feedback_closed_at is None`, so a round with
no deadline reached no work surface and offered no `Lõpeta kaasamine`, and
`complete_engagement_feedback` refused it in words: «Sellel kaasamisel ei ole
tagasiside tähtaega». The database refused it too. The deadline was not an
attribute of the round. It was the round's lifecycle.

## Decision

### 1. Two separate concepts

| concept | column | meaning |
| --- | --- | --- |
| lifecycle | `lifecycle_tracked`, `feedback_closed_at` | OPEN while `feedback_closed_at` is `NULL`, COMPLETED once set |
| due date | `feedback_deadline` | optional day the lawyer asked for feedback by |

* Creating a round opens it, with a deadline or without one.
* Reaching or passing the deadline completes nothing.
* Adding, moving or clearing the deadline of an open round leaves it open and
  never creates a second round. Clearing the deadline of a completed round
  leaves it completed.
* `Lõpeta kaasamine` is the explicit completion act. `close_matter`, including
  a terminal `Hetkeseis` under ADR 0131 §10, ends every open round on the file,
  with or without a deadline. A non-terminal stage transition touches no round.
* A passed deadline still changes presentation only: «Tagasiside tähtaeg
  möödus», the due/overdue band, `Üle tähtaja`. It never changes the lifecycle.

### 2. The only date rule

When both dates are known, `Tagasisidet ootame kuni` may not fall before the
first day of `Kaasamise kuupäev`'s period
(`feedback_deadline_precedes_engagement`, ADR 0091 amendment of 2026-09-26).
The same day is valid, and so is any later day. There is no minimum lead time
and no default (ADR 0120 §3), and a deadline with no engagement date is valid.
No other relationship between the two dates exists.

### 3. History is not reinterpreted: `lifecycle_tracked`

One new boolean, `MatterEngagement.lifecycle_tracked`, `True` by default.

It is `False` only for a round recorded **as history**. The register outreach
importer files decades of past consultations, and the alternatives are both
inventions. Opening them would put a decade of rounds on work surfaces, hold
every imported Matter back in the cutover review and trip the closed-Matter
integrity check. Completing them would write a closure nobody made. Such a row
is neither open nor completed: it records that a consultation happened.
`Ootan tagasisidet` remains as the one act that starts its lifecycle, now with
an optional date. Setting a reply-by date through `Muuda` does the same, because
a reply-by date on a round nobody is waiting for is not a statement anybody can
make.

The migration (`matters/0044`) keeps every existing row's reading exactly:

* a row with a deadline was an open or a completed wait, and stays one
  (`True`);
* a row with neither a deadline nor a closure was in no state, and stays a
  record of history (`False`). That covers the register's imported rounds and
  native rounds filed as completed acts under ADR 0091 §2. None of them becomes
  somebody's work, and no closure is manufactured.

Two checks replace `..._closure_needs_deadline`:
`matters_engagement_closure_needs_lifecycle` (only a round with a lifecycle can
be completed) and `matters_engagement_deadline_needs_lifecycle`.

### 4. Where an open round with no deadline appears

* **Teema page**: on `PRAEGUNE TEGEVUS` as «Ootame tagasisidet · Tähtaeg
  määramata», after the dated rounds. On its chronology row as «Ootame
  tagasisidet · tähtaeg määramata», with `Lõpeta kaasamine`.
* **Minu asjad**: in the `Kuupäevata` rail of the Matter's owner, and in its
  `?too=kuupaevata` population. An ownerless Matter's round goes to the
  unassigned half. It never enters the dated read model, so it is never
  `Üle tähtaja`, never in a deadline window, and never `Vajab sekkumist` for
  lacking a date.
* **`WorkItem.date_display`** reads «Tähtaeg määramata» for such a round. It
  does not say «Kuupäev määramata», which ADR 0106 kept for a step whose *date*
  is unknown: what this round lacks is precisely its `Tagasiside tähtaeg`, the
  name every surface already gives the column.
* It creates no `NextAction`, which is unchanged from ADR 0086 §3.

## Alternatives considered

**Open = `feedback_closed_at IS NULL`, with no new column.** Rejected. Every
imported register round would become open work and a cutover review reason, and
every closed imported Matter would raise a `closed-matter-open-feedback-wait`
finding. That manufactures a historical state.

**Backfill history as completed.** Rejected. It writes a closure timestamp and
an audit trail for a decision nobody made.

**A status enum (`OPEN` / `COMPLETED`).** Rejected. It would duplicate
`feedback_closed_at`, which already says when and by whom a round was completed,
and two columns for one fact can disagree.

**Default the deadline or require it before the round can open.** Forbidden by
the brief, and already refused by ADR 0120 §3.

## Consequences

Every round recorded through `+ Kaasamine` or the composer is now open work
until somebody finishes it. A lawyer who records a consultation that is already
over presses `Lõpeta kaasamine` on it, and may write what came back in the same
step. That is the explicit completion act this record asks for, and it is the
cost of not inferring completion from the absence of a date.

The seeded e2e Matter's round is now open and has no deadline, so its Teema page
shows one more `PRAEGUNE TEGEVUS` line and offers `Lõpeta kaasamine` in place of
`Ootan tagasisidet`. The visual baselines that include it move.

## Reversibility

The reverse migration drops the column and re-adds the old check. It fails, and
should fail, on a database holding a completed round with no deadline, because
that row cannot be expressed in the old schema.
