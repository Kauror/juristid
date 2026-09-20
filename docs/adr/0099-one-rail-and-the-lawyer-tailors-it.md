# 0099 — One rail, and the lawyer tailors it

**Status:** accepted
**Date:** 2026-09-21

**Amends ADR 0098 and reverses parts of 0097 §9 and 0092 §13.** 0098 built
`Menetluse kulg` as a phase rail, a road-ahead list and a strip of dated points,
and grouped `Teema käik` into phases. The owner read the result and asked for
less of it: fewer stray labels, fewer stacked systems, the actions in the right
places, and — the one addition — a timeline the lawyer can tailor to the file in
front of them.

0098's load-bearing decisions are **unchanged**: one phase vocabulary, a phase is
not a `Hetkeseis`, one explicit association on `Menetluse areng`, business-dated
intervals, unplaced is an ordinary answer, eight instrument-aware patterns, and
the phase-grouped history.

Six decisions:

1. **One rail, and it is the strip's markup that survived.**
2. **The explanatory words go; nothing becomes colour-only.**
3. **`Hetkeseis` has one home, and the rail is not it.**
4. **The rail is editable: hide a phase, date a phase.**
5. **`Kustuta` joins `Muuda teemat`; `Lõpeta teema` rejoins the chips.**
6. **`Liige` moves into the organisation picker's footer.**

**One additive migration**, `matters/0034_timeline_step`, plus an audit-choices
migration. No backfill, no data migration, and the table is empty for every file
nobody edits.

---

## Context

After 0098 the lower half of the Teema page carried a phase rail, an
`Ees võib olla` list with a `Kogu võimalik teekond` disclosure, and a
`Kirjas olevad kuupäevad` strip — three diagram-shaped things and seven labels
explaining them. Above it, `TEEMA TOIMINGUD` was a section holding one control.

The owner's complaint was not that any one of them was wrong. It was that
together they read like a developer's debugging panel, and that the one thing a
lawyer wanted to do with the roadmap — say *this file will never see a VTK*, or
*commencement is in January* — was the one thing they could not.

## 1 — One rail, and it is the strip's markup that survived

The phase rail and the dated strip are one row. A phase and a dated point are
both *a named thing on this file's course*; drawing them as two kinds of object
is what made a reader sort them before reading either.

**The strip's markup won, not the rail's**, and that is the reason this change is
small. `.tl-strip` already knew how to lay a row of dated points out, scroll
itself instead of the page at 375px, draw what is behind us solid and what is
ahead muted, and put today *between* two columns with `--tl-reach` — a grammar
reviewed at docs/adr/0074 §12.2. The phases join it:

```
current / recorded  → behind us: filled dot, solid rail     (like `past`)
unknown / possible  → not reached: hollow dot, muted rail   (like `future`)
```

300 lines of reviewed CSS and 51 browser tests kept working. `process_strip.html`
is deleted and `process_timeline.process_steps` is untouched — it is still the
one place the seven dated points are read from, now handed to `matter_rail`
rather than rendered directly.

**Ordering** is the pattern's, with each milestone inserted after the last dated
phase it is not earlier than. A phase list has an order that is not
chronological; a date has no place in a pattern. This reconciles them
deterministically.

## 2 — The words go, and nothing becomes colour-only

Removed: `Praegu`, `Kirjas`, `Teadmata`, `Võimalik`, `Ees võib olla`,
`Kogu võimalik teekond`, `Kirjas olevad kuupäevad`, and the pattern's name beside
the heading (`Menetluse kulg · VTK ja eelnõu`).

**What each one said is still said.** The current node carries
`aria-current="step"`, a filled and ringed dot and a heavier name. A node still
ahead carries a visually-hidden «Tulevikus» — the same device docs/adr/0074
§12.2 added when the strip stopped drawing every column alike. `Teadmata` keeps
its dashed dot, which is what separates «may have happened» from «may yet
happen», and both are muted rather than one being a paler shade of the other.

**`Ees võib olla` is gone entirely, projection included.** It was a second
rendering of what the rail beside it already drew: a phase nobody has reached is
muted, undated and announced as future. `LegalProcessRail.ahead`, `ahead_rest`,
`AHEAD_HORIZON`, `CONDITIONAL_LABEL` and `RailStep.conditional` went with it,
because projection code no surface reads is exactly what rots.
`PatternNode.conditional` stays — it is pattern data, asserted directly, and says
something true about a `Määrus` that may never reach the Government.

## 3 — `Hetkeseis` has one home, and the rail is not it

This is the decision most worth writing down, because the merge reopened a closed
one without anybody asking it to.

docs/adr/0074 §12.1 took the current `StageVocabulary` off the strip precisely
because it left *«the header's `Hetkeseis` pinned in the middle of it»*. 0092
§13's amendment then put the canonical stage back on the phase rail's current
node, for a real reason: `Jõustumine · Praegu` reads identically for a file
waiting for commencement and one already in force.

Merging the two rows put §12.1's defect straight back — `Kooskõlastusringil`
under a node called `Kooskõlastusring`, one letter apart, on every ordinary file.
Found in a browser, not by an assertion.

**The stage reads in the header and the phase reads on the rail.** The amendment's
reason is weaker now: the `Praegu` it was disambiguating is gone from the rail
too, and the header states the stage in full, once, where it has always been
stated. One glance for the rare question beats a duplicated line on the common
one. `ProcessNode.stage_label` is untouched and still asserted — what changed is
only that the page stopped printing it twice.

## 4 — The rail is editable

`MatterTimelineStep(matter, phase_key, hidden, occurs_on, occurs_on_precision)`,
unique per Matter and phase, visibility-inheriting like every other Matter child.

**Two questions the projection structurally cannot answer**, both the lawyer's
rather than the data's: which phases are worth showing on *this* file, and when a
phase is expected. A pattern is a reviewed reading of an ordinary procedure, not a
claim about this Matter; and an expectation has no canonical record until it
happens.

**The ordinary answer stores nothing.** A phase that is shown and undated is the
default, so its row is deleted rather than written. The table starts empty and
stays empty for every file nobody edits — which is also what makes «put it back»
work, rather than storing a second kind of default.

**A recorded phase is dated by its record, never by a second value.** Where a
`Menetluse areng` is filed in a phase, that record dates it — the same record the
chronology groups on — and the editor shows the day with «kirja pandud» instead
of offering a box. The box exists only where there is nothing to reuse. Where a
file has both, the fact wins and the expectation is simply no longer interesting.

**Nothing is inferred and nothing is created.** Hiding a phase makes no Matter
late; a date here is not a `NextAction`, not an `Oluline tähtaeg` and not a
deadline anybody is measured against. One audit row per save, and none at all for
a save that moved nothing.

The panel offers **the pattern's own phases and no others** — a `Määrus` is not
offered `Riigikogus` — and the checkbox means *show*, because a ticked box that
removes a step is the one shape of this control somebody gets backwards.

## 5 — Two actions moved, and a section retired

`Kustuta` sits beside `Muuda teemat` in the header. Both answer «this record is
wrong», and the low-visibility link at the foot of the page put the one
irreversible action furthest from the one a person goes looking for. It is still
a link to a confirmation page, still with no `confirm()` and no `hx-confirm`
(docs/adr/0096 §4).

`Lõpeta teema` is a peer chip in the launcher again, **in the same exclusive radio
group**, so picking it closes whatever was open. 0097 §9 moved it out on the
reasoning that closure is not capture and a row mixing them is a row where the
most consequential control looks like the most routine one. That reading stands;
the cure cost more than the complaint. What keeps the distinction is
`disclosure-chip--last` and nothing else. It is deliberately not a fifth
*family*: the four are what a file can have added to it, and this one ends it.

`TEEMA TOIMINGUD` is retired — with both controls moved it had no unique content,
and a heading over one control is the clutter this round is about.

## 6 — `Liige` is in the picker's footer

A second fact about the same answer — who this came from — had a full-width row
of its own between the organisation picker and the date. Same control, same
field, same refusals, one line instead of three. It stacks on a phone.

## Alternatives considered

**Keeping the phase rail's markup and deleting the strip's.** Rejected on
measurement rather than taste: `e2e/test_process_strip.py` references `.tl-step`
21 times and `.tl-strip` 3, so the suite is coupled to the *steps*. Keeping the
strip preserved ~300 lines of reviewed CSS, the `reached / today / ahead`
grammar, the `--tl-reach` gradient and 51 browser tests; keeping the rail would
have preserved a newer, smaller component and destroyed all of that.

**Letting the editor own the dated milestones too.** Rejected. `Arvamuse
tähtaeg`, `Koja arvamus`, `Jõustumine` and `Lõpetatud` each have a canonical
record and an editor of their own, and a second place to change
`Matter.response_deadline` is how two screens start disagreeing. They read on the
rail and are edited where they live.

**Storing the expected date as a `MatterImportantDate` tagged with a phase.**
Rejected as the primary store: an `Oluline tähtaeg` is a *watched* milestone that
projects into the chronology once it passes, and a roadmap annotation is not that.
The two would also have needed a rule about which wins.

**A generic `hidden_steps` JSON column on `Matter`.** Rejected. A column holding
a list is a column no constraint can close, no query can filter and no audit row
can describe usefully.

**Ordering the rail purely by date.** Rejected: a phase with no date has no
position under that rule, and phases with no date are the ordinary case.

## Consequences

- One `.tl-strip` on the page, carrying phases and dated points.
- `process_strip.html` and `teema_toimingud.html` are deleted; their content
  moved rather than being retired.
- `Menetluse kulg` renders whenever there is anything to draw, including on a
  file read against no procedure — it then has dates and no phases.
- One additive table, empty until somebody edits a rail.
- No search, index or reindex change.

## Reversibility

High. The table is additive and optional; dropping the editor leaves rows nothing
reads. Restoring the two rails means restoring one template and one partial —
both are in this commit's parent.
