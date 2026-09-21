# 0100 — `Menetluse kulg` is where the file is going; `Teema käik` is what happened

**Status:** accepted
**Date:** 2026-09-21

**Succeeds ADR 0099 and amends ADR 0098 §8 and docs/adr/0074 §12.1.** 0099 merged
the phase rail and the dated strip into one row and took seven explanatory
labels off it. That was right, and this record does not undo any of it: one
rail, no second strip, almost no words, `Muuda` tailors it, hiding is per-Matter
and reversible.

What 0099 left was a rail trying to answer two questions with one drawing. It
carried the roadmap *and* borrowed history to date it, and a full exploratory QA
round found eight separate defects that are all the same defect
(2026-09-21 QA-005 … QA-013, and the owner's own reading, OWNER-03).

The division is now stated rather than implied:

```
MENETLUSE KULG   where the procedure stands · what may still come · the dates somebody recorded
TEEMA KÄIK       what actually happened, in the phases it happened in
```

`Teema käik` already answers historical repetition properly — a section per
occurrence, in date order, with its own `alates` heading. The rail does not try
to, and that single sentence resolves most of what follows.

---

## Six decisions

### 1. The rail has no `Alustatud`, and nothing replaces it

`Alustatud` was drawn from `Matter.created_at` for a natively filed Matter. The
note defending it explained carefully why neither `received_date` nor an
importer's timestamp would do — and was right about both, and wrong about
`created_at`, which is equally a fact about this database rather than about a
procedure.

Every consequence followed from that:

* a file entered a month after it arrived drew `Saabus 1.9` in its header and
  `Alustatud 21.9` on its rail;
* a file with a backdated opinion drew `Koja arvamus 20.9` **before**
  `Alustatud 21.9` — the rail saying the opinion went out the day before the
  work began (QA-013);
* on a long file the column sat in the middle of dates running
  5.3 → 2.4 → 21.9 → 3.6 → 17.6, because today is not where the procedure is
  (QA-006).

The owner's reading is shorter and is the one that decided it: `Alustatud` and
`Algus` are one conceptual beginning said twice (OWNER-03). The pattern's own
first phase is the beginning the procedure has. `Saabus` stays in the header,
where it is a fact about the post and is labelled as one.

**No synonym.** A `Loodud`, an `Avatud` or an `Algatatud` drawn from the same
column would be the same fabricated milestone under a different word.
`PHASE_STARTED = 0` is retired rather than reused, because the constants are
read by `_MILESTONE_PHASE` and renumbering would change what every other kind
means.

### 2. A phase node is dated by the roadmap, never by history

The node used to take the date of the first recorded step filed under that
phase. On a file that had gone out for consultation twice, it read
`Kooskõlastusring 2.4.2026` an inch above a `Teema käik` whose current section
said `alates 20.08.2026`: two answers to «since when is this file on the
coordination round», on one screen (QA-007). Borrowing history in pattern order
also made the dates non-monotonic, because a file that goes forward and comes
back is a loop and a rail is a line (QA-006).

A node now carries an explicit `MatterTimelineStep` date and otherwise nothing.

**Consequence for the panel:** `Muuda` offers a date box on every phase. It used
to withhold one wherever a `Märge` already dated the phase — correct while the
rail borrowed that date, and wrong now, because withholding it would leave a
phase with no date and no way to give it one.

### 3. A canonical fact is folded onto its phase, not drawn beside it

A commencement was inserted as its own column next to `Jõustumine`, so the rail
read `Jõustumine · Jõustumine 1.1.2027` — two adjacent columns with one name,
both saying `Tulevikus`. A file with two commencement dates drew three. The
description that told them apart lived only in a `title` tooltip, which a touch
screen, a printout and a screen reader all fail to deliver (QA-005).

A milestone whose kind names a phase the file draws is folded onto that phase:
one column, carrying each fact as a visible line — «põhiosa 27.9.2027», «osad
sätted 1.1.2028». A kind that names no phase, or whose phase this pattern does
not have, goes on reading as a point of its own, which is what an unanchored
point is.

### 4. Two phases cannot be taken off the rail

**The current phase.** Hiding it left the header saying `Kooskõlastusringil`
while the rail no longer drew that phase at all — and the unanchored deadline
rule, which places a future point *beside the current phase*, then fell back to
sorting it after every undated future one, reproducing the exact placement
docs/adr/0099 had just fixed (QA-009).

**A phase carrying a canonical dated fact.** Hiding `Jõustumine` while a
commencement existed did not take the fact with it: it became unanchored and
re-sorted by date, so `Jõustumine 1.1.2027` drew before `Valitsuses` and
`Riigikogus` — a rail claiming the act enters into force before the bill reaches
government (QA-008).

Both are disabled in the panel with the reason beside the chip, and both are
re-asserted where the service call is built, because «the control was not
rendered» is never how a rule is kept here. A date on an optional phase can
still be cleared, and clearing it releases the phase.

### 5. Explicit roadmap dates run in the procedure's order

`Kooskõlastusring 01.12` with `Valitsuses 25.09` was accepted, and drew a
left-to-right time rail reading December before September (QA-010). The rail
does not reorder phases — a legal process has the order it has — so the drawing
was simply false.

Only explicit dates are compared, and only against each other. The error lands
on the box that is out of order and the panel keeps what was typed.

**A file that went back to an earlier phase is not describing its roadmap out of
order.** That is history; it belongs in `Teema käik`, and these boxes are not
where it is recorded.

### 6. A closed file's own clock stops

A Matter archived that morning read `Tähtaeg 15.10.2026 · 24 p` in its header
while `Praegune tegevus` under it said the file was closed and every work list
had already dropped it (QA-011).

The date stays: it is part of the record, and the header goes on stating it.
The countdown does not. `ActiveDeadline.is_active` is false on a closed Matter,
and `Rohkem ei tegele` needs no clause of its own: `Disposition` answers *why a
file is closed*, and `matters_closure_fields_consistent` refuses a disposition
on an open row — so a stood-down file is already one of the states that test
covers, and a second comparison would read as a rule about open Matters while
being unreachable on every one of them.

The **external** procedure may well continue, and the rail goes on drawing the
phases that may still come — `Koda ei tegele edasi` beside it says what this
office is doing, which is a different sentence and stays a different sentence
(docs/adr/0032).

---

## What is unchanged

The late-entry rule (an earlier phase with no evidence reads `Teadmata`, never
completed), `Rohkem ei tegele` as a disposition and never a node, permission
before projection, no writes from any of this, one rail rather than two, and the
unanchored-deadline placement 0099 introduced. `Teema käik` is untouched by this
record except that it is now the only surface answering historical repetition —
which it already did.

## Migrations

**None.** Every change here is projection and presentation over data that
already exists.
