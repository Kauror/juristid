# 0119 — The lawyer adds the steps the pattern did not draw

**Status:** accepted
**Date:** 2026-09-28

**Extends ADR 0099 §4** (the rail is editable) and keeps ADR 0100's reading of
the rail as the road the file is on. `MatterTimelineStep` gains a second kind of
row; the rail keeps two things it used to lose. One migration,
`matters/0040_timeline_added_steps`: two columns with a database default, the
per-phase uniqueness narrowed to phase rows, and the vocabulary check widened to
the second kind. No backfill, no data migration, no search-index change.

Five decisions:

1. **`+ Lisa samm`, behind `Muuda`, on every file.**
2. **An added step has a stated place, not a derived one.**
3. **A phase the file has reached is drawn whatever a hide said.**
4. **A phase the file recorded survives the file being reclassified.**
5. **An added step is taken off by deleting it, and the audit keeps its name.**

---

## Context

After 0099 the lawyer could hide a phase the pattern drew and date one. They
could not add a step the pattern did not draw — and the pattern is read off the
file's present `Õigusakt` and `Menetlusliik`, so whole classes of file had
nothing to tailor:

* a file read against **no procedure** (no `Õigusakt`, or instruments from both
  families) had no phases, and `Muuda` was not rendered at all;
* a file whose procedure has **no such phase** — `VTK` on a `Määrus` — could
  not say that one happened anyway;
* a file with a **second** consultation round, a committee sitting or any other
  step the reviewed patterns do not name could not put it on its rail.

Inspecting how the rail is built found two ways it lost a milestone that had
already happened, which the owner named as the critical property: the rail is
also a record of how the file progressed, and a completed milestone must not
disappear because something *else* changed.

* **A hide outlived the file reaching the phase.** `matter_rail` skipped every
  hidden phase row, whatever the phase's state. Hide `Valitsuses` while the
  file is out for consultation, then move the file to government and on to
  Parliament: the rail never drew the government phase it went through.
* **A reclassification took recorded phases with it.** Change `Seadus` to
  `Määrus` and the new pattern has no `Riigikogus`, so a `Menetluse areng`
  filed there — and a past date somebody put on the phase — vanished from the
  rail.

Everything else the owner listed was already true and is now asserted:
removing `VTK` removes only `VTK`, editing one phase rewrites no other, nothing
is regenerated on a page load (the rail is a read; no row is ever written by
drawing it), and `Teema käik` and the audit trail are separate surfaces the
panel does not touch.

## 1 — `+ Lisa samm`, behind `Muuda`, on every file

`Muuda` stays the one entry point. The panel keeps its phase rows exactly as
they were, lists the steps already added — one line each, opening to be
corrected — and ends with a `+ Lisa samm` disclosure: `Nimetus`, the shared
`Täpsus` composer, and `Asukoht`.

`Muuda` is now rendered for every writer on an open file, **including one whose
rail is empty**. That file draws a bar holding `Muuda` and no strip
(`lprail--empty`: no band, no hairline); a reader, and a closed file, still get
nothing. A file with nothing on its rail is exactly the file that needs a first
step, and it has no other way in.

**The name is free text.** The phase vocabulary is offered as a `datalist`, so a
standard step is typed the standard way, and nothing is refused for not being
in it — `Komisjoni istung` is as valid as `VTK`. Names are **not unique**: a
second `Kooskõlastusring` or a third `Koja arvamus` is a row of its own.

**The date is the product's one date model.** `occurs_on` + `occurs_on_precision`
through `_precision_fields` / `_period_anchor` / `format_at_precision` — the
composer every other period on the Teema page uses (ADR 0079). No second model.

**One form, one save.** Phases and steps post together, so a panel save is one
revision check (`timeline_steps_revision_token`, whose lines for phase rows are
unchanged — a file with no added step keeps the token it had) and one
`TIMELINE_STEPS_CHANGED` row.

## 2 — An added step has a stated place

`after_key` is the rail key of the item the step follows — a phase key, a
dated point's key, another added step's `samm:<id>` — or `""` for the start.
`Asukoht` offers the rail as drawn now: *Algusesse*, then *Pärast: …* for each
item. New steps default to the end.

The rail places its own items first, by the rules 0099 and 0100 set, and added
steps last, each immediately after its anchor — so an added step never moves a
phase or a dated point. Two steps naming one anchor read newest-nearest, which
is what «right after X» means the second time somebody says it. Creation order
is only that tie-break, never the position.

**An anchor that is gone never drops the step.** A hidden phase: the step reads
where the phase would have been, before the next phase of the pattern still
drawn. An anchor that is itself an added step: placed after it. Anything else —
a dated point whose key changed, a phase of a pattern the file no longer reads
against — falls back to the step's own date among the dated items, or to the end
when undated. The stored anchor is never rewritten by reading, and a panel save
that leaves `Asukoht` alone keeps it. A step placed after itself, however many
steps round, is refused.

**State.** A dated step reads against today like a dated point (`past`,
`today`, `future`). An undated one reads `recorded` behind the file's position
and `possible` after it — the place a person chose is the only thing said.

## 3 — A phase the file has reached is drawn whatever a hide said

A hide is honoured only while the phase is *ahead or unknown*. A phase that is
**current**, or **recorded**, is drawn — decided on read, so the stored `hidden`
row is untouched and a page load writes nothing. This is the deterministic rule
for «an automatically generated step was removed and a real domain act needs it
again»: the act is moving the file's `Hetkeseis` onto the phase, or recording a
step in it, and from then on it is part of what happened.

The panel already refused to hide the current phase (QA-009); this closes the
same hole from the other side, for a hide set before the file got there.

## 4 — A phase the file recorded survives reclassification

*Since docs/adr/0128 §3 the dated-row evidence below also marks a phase the
pattern* does *draw: a shown phase row dated on a day that has come reads
`Kirjas` wherever it sits, through the same `dated_state` reading.*

A phase with explicit evidence that the current pattern does not draw is still
drawn, `recorded`, in the vocabulary's order, just before where the file stands.
Evidence is what 0092 §13 already accepts and nothing more: a `Menetluse areng`
filed in the phase, or a phase row a person dated on or before today and did not
hide. Not the stage history — mapping a stage onto a phase *is* the pattern —
and not a future date, which is a plan for a procedure the file no longer reads
against.

This costs one read on a file read against no procedure (the patterned file
already reads it for its own rail): the Matter page's measured ceiling moves
51 → 52 (`tests/test_teema_redesign.py`).

## 5 — Taken off by deleting it; the audit keeps the name

An added step is not the pattern's, so there is no default to fall back to and
no `hidden` for it (a CHECK says so). `Eemalda samm` deletes the row. The save's
`TIMELINE_STEPS_CHANGED` event carries `steps: [{change, title, step}]` —
`lisatud`, `muudetud`, `eemaldatud` — beside the unchanged `phases` list, so a
removed step is still named in `Kõik muudatused`. No `ChangeEvent` or
`SecurityAuditEvent` is ever deleted or edited.

## What this does not change

* **`Teema käik` is still the record of acts.** An added step is not a
  `Menetluse areng`, is not projected into the chronology, and creates no work,
  deadline or `NextAction` (0078 §3, 0099 §4).
* **The phase rows** — hide, date, the current-phase and anchored-fact
  protections, the ordering check on explicit phase dates — are unchanged.
* **The rail's own placement rules** (0099, 0100 and their amendments) are
  unchanged; added steps are placed after them.
* No workflow engine: no transitions, statuses, assignment or notifications.

## Migration and rollback

`title` and `after_key` carry a database default, so the release still serving
while `migrate` runs keeps inserting phase rows. The constraint change is
flagged consequential by `migration_plan` (Remove/AddConstraint): after it, a
rollback to the previous release still reads and writes phase rows correctly —
it keys them by `phase_key` and never offers `""` — and simply does not draw
added steps. Existing rows all satisfy the new checks (`phase_key` in the
vocabulary, `title` empty).

## Alternatives considered

* **A second table for added steps.** A second timeline beside the first is
  what the owner ruled out, and it would split one revision check and one audit
  row into two.
* **Place added steps by date alone.** The rail's phases are mostly undated, so
  a date alone cannot say «between `Kooskõlastusring` and `Valitsuses`».
* **An integer order over the whole rail.** The rail is a projection that
  gains and loses items as records change; a stored index into it goes stale
  the first time a `Koja arvamus` is recorded.
