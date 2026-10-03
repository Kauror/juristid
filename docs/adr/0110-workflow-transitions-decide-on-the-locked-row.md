# 0110 — Workflow transitions and owner assignment decide on the locked row

**Status:** accepted
**Date:** 2026-09-24

**Amends ADR 0075 §4, which promised more than the code kept.** §4 says a
double submit of the completion form "finds the action COMPLETED rather than
OPEN, and exactly one result survives". That was true of the composer path,
which locks the Matter and re-reads the action (`complete_current_action`), and
not of the three services every other surface calls. Two findings from the
engineering audit, reproduced on `7a626158` before anything changed (ENG-072,
ENG-075).

---

## 1. Terminal next-action transitions lock, then re-read (ENG-072)

`complete_next_action`, `cancel_next_action` and `acknowledge_review` checked
`status` on the instance the caller handed in — fetched by the view before the
transaction began, without a lock. When another transition landed on the same
action in between, the late one still saw OPEN:

* complete against a replacement left an action COMPLETED with `replaced_by` set;
* complete against a closure wrote NEXT_ACTION_COMPLETED after MATTER_CLOSED;
* two completions wrote two completion events;
* a review against a replacement wrote NEXT_ACTION_REVIEWED on a SUPERSEDED action.

Each now begins with `_lock_for_transition`: the Matter at `FOR NO KEY UPDATE`,
then the action at `FOR NO KEY UPDATE`, re-read, and the decision is taken on
that row. The order is the one `set_next_action` and `close_matter` already use
(Matter → NextAction, `app/matters/locks.py`); the mode conflicts with itself and
with the `FOR UPDATE` `set_next_action` takes, so every transition on one Matter
takes its turn. The refusal is the sentence each transition already had for an
action that is not open — no new message, and nothing written.

Nothing changes for an uncontested call, and the composer path, which already
holds both locks, re-acquires them within its own transaction at no cost.

**No database CHECK tying `replaced_by` to SUPERSEDED.** The audit suggested
one as optional. It would need an invariant query against production data
first, and the services now make the state unreachable through every supported
path; a constraint is a separate decision with its own rollback story, not a
side effect of this change.

## 2. Owner assignment starts with the Matter lock (ENG-075)

`assign_matter` was the one Matter write that did not start with the Matter row:
it retired the previous owner's notices, then saved the Matter. `delete_matter`
locks the Matter and then deletes the notices — the opposite order — so the two
could deadlock, and a stale assignment could commit after a deletion, leaving an
owner, a live notice and a MATTER_ASSIGNED event on a tombstone. The audit's
`from` came from the instance the request started with, not from the row.

It now takes the Matter at `FOR NO KEY UPDATE` first (through `all_objects`, so
a deletion that won is seen rather than raising a lookup error), refuses a
deleted Matter with its own sentence, and reads the previous owner from the
locked row. Every notice write follows the lock.

**A closed Matter still changes hands.** `lock_open_matter_for_business_write`
was the audit's suggestion and would have refused it; correcting who owned a
finished file has always been allowed and stays so. The invariant needed is
"not deleted", not "open".

## Migrations

**None.**

## Amendment, 2026-10-03 — one lock strength on the Matter (SVC-04)

§1 said the transitions' `FOR NO KEY UPDATE` «conflicts with itself and with
the `FOR UPDATE` `set_next_action` takes». `set_next_action` and
`establish_opinion_preparation_action` now take the Matter through
`lock_matter_for_write` and the open step at `FOR NO KEY UPDATE`, like every
other Matter writer. Writers still take turns — the mode conflicts with itself
— and every decision is still taken on the locked row, so one open step, the
supersession chain and «no open step on a closed Matter» hold exactly as
before (`tests/test_next_action_lock_strength.py`).

What changes is what the lock no longer blocks: `FOR KEY SHARE`, which every
insert of a row referencing the Matter takes, and the search rebuild takes at
COMMIT. A composer locked the Matter at the weaker mode and then upgraded it
inside `set_next_action` for the rest of its transaction; a write of an indexed
row after that would close the ENG-027 cycle against a rebuild. Measured on
`main` before the change: a child insert on the Matter waited for the step's
transaction to commit; after it, the insert goes through while the step is held.
No schema change.

