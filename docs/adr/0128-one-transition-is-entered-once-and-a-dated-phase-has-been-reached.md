# 0128 — One transition is entered once, and a phase dated on a day that has come has been reached

**Status:** accepted
**Date:** 2026-10-01

**JUR-CASE-10** from the Juristieksami living-dossier QA, the owner's decisions
D1 and D2, and the two strip defects the discovery found beside it. **No
migrations**, no new model, no second timeline, no inference.

1. **A `+ Märge` that moves `Hetkeseis` onto a phase may also date it**, when the
   person confirms it: `Märgi ka menetluse kulgu: <faas> <päev>`, offered only
   for an unambiguous forward move onto an undated phase, ticked when it
   appears, written once as that phase's roadmap date.
2. **Nothing is written without the tick**, nothing is ever overwritten, and a
   day ahead, a backward move or an ambiguous stage offers nothing.
3. **A phase a person dated on a day that has come reads `Kirjas`**, wherever it
   sits on the rail; the current node is still the one `Hetkeseis` places.
4. **Dated points sort against explicit phase dates across the current node**,
   so `Tagasiside tähtaeg 6.9` and `Koja arvamus 8.9` read after `VTK 1.9`.

---

## Context

**The same transition, entered twice.** On 15 September the lawyer records
«Kooskõlastusring – eelnõu» with `Uus hetkeseis → Kooskõlastusringil`. The
`Hetkeseis` moves — and `Menetluse kulg` stays undated, because a phase node is
dated by its roadmap row alone (docs/adr/0100 §2) and only `Muuda kulgu` writes
one. So the lawyer opens `Muuda kulgu` and types `Kooskõlastusring = 15.09`. One
fact, two boxes, two chances to disagree.

The day was in hand: `add_procedural_development` holds the `Märge`'s
`occurred_on` when it calls `change_stage`, and passed it on to nothing. The
reason was principled, not an oversight: docs/adr/0092 §13 refused to date a
node from a stage change because the event's `occurred_at` is the moment
somebody typed, docs/adr/0100 §2 reversed a rail that borrowed history's dates,
and the docs/adr/0105 amendment of 2026-09-27 refused to infer a phase from
`Hetkeseis`.

**And the rail said «Tulevikus» about the past.** The QA's file — a VTK and a
bill, still on `Idee` — had `VTK = 1.9` set in `Muuda kulgu`. The rail drew
`VTK 1.9 · Tulevikus`, and drew the 6.9 feedback deadline and the 8.9 opinion
*before* it. Two defects: `legal_process_rail` took a node's state only from the
stage and from developments filed under a phase, so a dated `VTK` — which maps
no stage (docs/adr/0098 §2) — could never read reached; and `matter_rail`'s
window for a reached point stopped one past the current phase, so the anchor
`VTK 1.9` was never scanned (docs/adr/0100, amended 2026-09-27, rule 1, says an
explicit roadmap date is an anchor that sorts by date). UQ-05 found the first one
on 29.09.

## 1. One transition, entered once

**The confirmation is the person's statement, so it is the roadmap.** The box
says exactly what it will write — «Märgi ka menetluse kulgu: Kooskõlastusring
15.9.2026» — and the date is the `Märge`'s business day, never an event's
`occurred_at`. What is stored is an ordinary phase row, written once, the same
row `Muuda kulgu` writes, corrected and cleared there. The node is still dated by
its roadmap row and by nothing else (docs/adr/0100 §2 stands); this is a second
door onto that row, opened only by a tick.

**Offered only where the move means one phase** (`confirmable_phase`):

* the target stage is `consultation`, `government`, `parliament`,
  `estonian_eu_position` or `eu_procedure`, and on the file's pattern it is the
  **only** stage its phase holds — so `consultation` on a European file, whose
  `ELi konsultatsioon` began with `idea`, is excluded;
* the move is **forward** on the pattern: the file stood on an earlier phase, or
  had no `Hetkeseis` yet. A move back, onto the same phase, or from a stage the
  pattern cannot place (`Muu`) is a correction or history the rail does not draw
  (docs/adr/0100 §5).

Never offered for `idea` — the beginning, whose date re-anchors every point,
and never `VTK` (docs/adr/0098 §2); never for `awaiting_entry` / `in_force` —
adoption is not commencement, and `Jõustumine` is the commencement record's;
never for `awaiting_transposition`; never for `other`. Nothing is inferred from
`Õigusakt`, and nothing touches `Matter.track`.

**And only while it is true** (`phase_date_offers`, `PhaseDateOffer.allows`):
the phase carries no date yet, the day has come, and the day keeps explicit phase
dates in the procedure's order — the rule `Muuda kulgu` refuses on its own boxes.
The offers are read from the phase rows the page already reads (now read once,
`timeline_step_rows`, and handed to the rail too), so the page's query budget
does not grow.

**Ticked when it appears, and the server decides** — docs/adr/0124 §2–§3's shape.
The script shows the box for a stage and day the server offered; hidden is
disabled; the form drops a tick that no offer allows; and
`add_procedural_development(date_phase=True)` decides again on the locked
Matter's stage *before* the move, then calls `record_confirmed_phase_date`,
which re-reads the phase rows under lock and **writes nothing** for a phase
already dated, a day ahead or a day out of order. Inert rather than refusing: a
phase dated in another tab meanwhile is not a reason to throw away the `Märge`
and the stage move the person saved.

**What it writes**, inside the same `composer_operation`: the phase row (EXACT,
shown) and one `TIMELINE_STEPS_CHANGED` whose payload names the phase, the day
and `via: hetkeseis`. Not a `Teema käik` row — the `Märge` is the act and stays
the one row; the stage change still folds under it.

**What it does not do.** No date from the header's inline `Hetkeseis`, from
`Muuda teemat`, from `Uus teema` (`Saabus` is the post's date, docs/adr/0100 §1),
from the stage history or on a page load. No `Etapp` on the `Märge` and nothing
copied onto `MatterProceduralDevelopment` (the docs/adr/0105 amendment of
2026-09-27 stands). Correcting or removing the `Märge`, or moving the stage
again, never moves the phase date (docs/adr/0124 §4's rule): it is corrected
where it is seen, in `Muuda kulgu` (docs/adr/0120).

## 2. Unticked, nothing

Unticked — or for any move the offer does not cover — the stage moves exactly as
it always did and no phase is dated. A crafted tick is dropped by the form and
again by the use case.

## 3. A dated phase that has come reads `Kirjas`

A shown phase row whose date has come is evidence the phase was reached — the
statement docs/adr/0119 §4 already accepted for a phase the pattern no longer
draws, now accepted for every phase. Read through the strip's own `dated_state`,
so a period counts only once it has ended (docs/adr/0123) and a future date is a
plan. Like a recorded step it is an act rather than a column, so docs/adr/0092
§13's demotion of *stage* evidence ahead of the current node does not touch it:
`VTK 1.9` on a file on `Idee` reads `Kirjas`, and `Algus` is still `Praegu`. The
current node is never moved — that is `Hetkeseis`'s answer alone. A hidden row is
still not evidence.

## 4. Dated points keep the order explicit dates give them

A reached point's window now runs on to the last phase a person dated no later
than the point, and over the dated points already placed after it; past that, an
undated phase or one dated later still bounds it. That is docs/adr/0100's
2026-09-27 rule 1 doing what it says. The rail reads `Algus · VTK 1.9 ·
Tagasiside tähtaeg 6.9 · Koja arvamus 8.9 · Kooskõlastusring …`; the late-entry
rule, the beginning rule and the future-point rules are unchanged, and added
steps are placed after all of it as before (docs/adr/0119 §2).

## Supersedes and narrows

* **docs/adr/0092 §13** — «Recorded means recorded … and nothing else» and «No
  dates on the rail»: a phase row a person dated on a day that has come is
  evidence too, and a confirmed `Märge` day may date a phase. A stage-change
  event's `occurred_at` still dates nothing.
* **docs/adr/0099 §4** and the `MatterTimelineStep` docstring — «one panel writes
  it»: a confirmed `+ Märge` is the second writer, of a phase's date only.
* **docs/adr/0100 §2** — stands; the date is the roadmap's, written on the
  person's word. **The 2026-09-27 amendment, rule 1** — now true across the
  current node.
* **docs/adr/0105, amendment of 2026-09-27** — «nothing is inferred … from
  `Hetkeseis`» stands for `Etapp`; a phase *date* confirmed by the person is not
  an inference.

## Alternatives considered

* **Derive the phase date when the rail is drawn**, from the `Märge` that shares
  an operation with the stage change. Reverses docs/adr/0100 §2 invisibly,
  reopens QA-006's loops, shows a date `Muuda kulgu` cannot edit, and adds
  queries to every page.
* **Write the date on every stage move, unconfirmed.** The `Märge` day is the
  activity's day, not necessarily the phase's; the owner's decision was a box.
* **Map `Idee` + `Õigusakt VTK` to the `VTK` phase.** Refused by docs/adr/0098
  §2: a file sits on `Idee` for a year with no VTK in existence.
* **Move the current marker to a dated phase ahead of it.** A dated `VTK` says
  the VTK happened; it does not say where the file stands now.

## Not changed

`Hetkeseis` and `change_stage`; `Muuda kulgu`, its revision token, hide rules and
added steps; the phase vocabulary and the eight patterns; `Teema käik`;
docs/adr/0124's `Märgi järgmiseks tegevuseks`; docs/adr/0126's direct step and
completion; the search index; permissions. **No migrations.**
