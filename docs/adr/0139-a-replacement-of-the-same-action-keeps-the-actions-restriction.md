# 0139 — A replacement of the same action keeps the action's restriction

**Status:** accepted
**Date:** 2026-10-04

The third of three rules that keep a restriction from being lost on the way
from one row to the next (docs/adr/0137 files, docs/adr/0138 completion notes).
**No migrations**, no new model or field, and **no existing row is rewritten**.

1. **`Muuda` is the same work.** Editing the open step's words or day writes a
   new `NextAction` superseding the old one, and that replacement is created
   with the old row's own restriction.
2. **The service decides, the caller declares.** `set_next_action` (through
   `set_next_action_for_new_work`) takes `carry_visibility_override`, the
   sibling of `carry_plan_step`; only `change_current_action` — `Muuda` —
   passes it.
3. **New work does not inherit**, even though it also supersedes whatever was
   open.

## Context

`change_current_action` (`Muuda` beside the open step, docs/adr/0133 §4) writes
the edit through `set_next_action`, which locks the Matter, marks the open row
`SUPERSEDED`, creates a new row and chains `replaced_by`. It carried the
`Tööplaan` step on request (`carry_plan_step`) and nothing else. A step
restricted below a `NORMAL` Matter therefore came back from `Muuda` as an
ordinary one.

Reproduced on the post-0138 tree: before the edit a reader who may see the
Matter but not restricted records saw no trace of the step; after it,
`NextAction.visible_to` returned the replacement, and its words were printed in
`PRAEGUNE TEGEVUS` on the Teema page and in the register's `JÄRGMISEKS` column.
(A person's desk is not open to that reader, `Teema käik` draws no row for a
step change, and next actions are not in the search index.)

**Why the Matter's visibility is not enough.** The restriction is a fact about
the work, and an edit does not change what the work is: «Loe eelnõu» becoming
«Loe eelnõu ja kommenteeri» on another day is the same task, and the people who
were not to see it are still not to see it.

## Decision

### §1 Same work and new work

`set_next_action` supersedes in several situations that mean different things:

| Door | Meaning | Carries |
| --- | --- | --- |
| `Muuda` — `change_current_action` | the **same** work, said differently | plan step **and** restriction |
| `+ Määra järgmine tegevus` — `set_action` with no step named | new work | nothing |
| `Järgmisena` after `Mida tegid?` — `Muu tegevus` or a `Tööplaan` step | new work (docs/adr/0138 §4) | nothing |
| `Alusta` on a plan step — `app.workflow.plan` | new work | its own plan step only |
| `+ Menetluse areng` with a next step | new work | nothing |
| `Uus teema`'s `Koostan arvamuse` | new work | nothing |
| importers, enrichment, seed commands — `set_next_action` directly | recording what a source says | whatever the source says |

Only the first is an edit. The service cannot tell them apart and must not
guess; the caller that knows it is an edit says so, exactly as it already did
for the plan step. The decision is made at the service boundary, under the
Matter's lock, off the row being superseded.

### §2 Copied at creation; fail closed

The replacement's `visibility_override` is set in the `INSERT`: `RESTRICTED`
when the superseded row carries it; empty when that row is empty or `NORMAL`, so
an ordinary step stays ordinary and a step on a restricted Matter inherits the
Matter with no redundant override. It is copied, not joined: relaxing the old
row later leaves the replacement restricted, and an error hides work rather than
exposing it.

### §3 Plan-linked steps

A step started from the `Tööplaan` keeps both on `Muuda`: the same `plan_step`
and the same restriction. Neither is traded for the other.

### §4 Stale edits

Unchanged. `Muuda` names the step it was drawn beside (`_named_open_action`); a
step no longer open refuses with `STALE_ACTION_REFUSAL` before anything is
written, so no row is created, superseded or re-scoped.

### §5 What does not inherit

* New work over a restricted step (§1) — the owner's decision, 2026-10-04.
* Independent records that also complete a step (docs/adr/0138 §3).
* Anything written by an importer: it keeps its documented source contract.

### §6 No backfill

No row is rewritten. The read-only production inventory of 2026-10-04 found no
restricted `NextAction` at all, so no existing chain has a restricted row whose
replacement lost it.

## Consequences

* A restricted step stays invisible to the readers it excludes however often it
  is edited; with 0138 its completion note does too, and with 0137 the note's
  files.
* `tests/test_action_edit_keeps_restriction.py` asserts it through the service
  and the `Muuda` route, with the controls, the plan-linked edit, the three
  new-work doors not inheriting, the stale edit and copy semantics.

## Not decided here

* Rewriting existing chains (§6).
* A restriction control on `Muuda` or any other panel.

## Amendment, 2026-10-06 — `Muuda` carries the restriction only (docs/adr/0141)

`carry_plan_step` is removed: the replacement no longer carries a `Tööplaan`
step, and `Alusta` is gone. The restriction is carried exactly as decided
here. An old row keeps the `plan_step` it was written with.
