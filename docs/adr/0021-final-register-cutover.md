# 0021 — The final register cutover, and the two columns that mean different things

- **Status:** Accepted — implemented on the `cutover/final-register-sync` branch
- **Date:** 2026-08-22
- **Builds on** ADR 0011 (next actions and submissions), ADR 0012 (register import), ADR 0020 (historical cutover state).
- **Refines** ADR 0020. It does not replace it: the historical default still governs every year the maintained register does not describe.
- **Amended 2026-08-22** after a production dry-run — see *The reviewed current scope* below. The amendment states an assumption this ADR relied on without recording; it changes no decision here.

## What changed since ADR 0020

ADR 0020 had to answer "which of 2455 imported Matters are current work" from a
register that recorded no closure before 2025. With no per-row evidence, it
decided a per-*year* default: 2026 is current, everything before it is
historical unless a person says otherwise. That was the right decision on the
evidence available, and this ADR is not a correction of it.

What is new is better evidence. The department has produced a final maintained
snapshot — `Tööd eelnõudega 21.08.26.xlsx`,
`f38906c255f5ad6a58711ce833dd61da5fad7ce7ffd74fb8d2b057c6e8a58df2` — in which
the 2025 and 2026 sheets are both actively maintained, `HETKESEIS` is filled on
every real row, and `JÄRGMISEKS` carries the department's own notes about what
happens next. That is per-row evidence about current work, and it says the
year-only rule is wrong in **both** directions:

- **60 of the 2025 rows are still live work.** A proceeding does not end because
  a calendar year did. The year-only rule had archived all of them.
- **55 of the 195 real 2026 rows are finished.** The year-only rule had made
  every one of them current.

Measured read-only against that workbook, and reproduced by the implementation:

| | |
|---|---|
| Real 2026 rows (a title, not just a number) | 195 |
| Pre-numbered rows carrying a reference and nothing else | 105 |
| 2026 rows with a blank `VÄLJA` | 15 |
| Current after this cutover — 2026 | 140 |
| Current after this cutover — 2025 carry-over | 60 |
| **Current total** | **200** |
| Current rows carrying a `JÄRGMISEKS` instruction | 134 |

## The decision

### A reference is not a row

The current sheet is pre-numbered well past the work that exists: references run
to `2026_300`, titles stop at `2026_195`. A row becomes a Matter when it has a
`TEEMA`, which is what the 2026 contract's `null_semantics` for column B already
said. The 105 numbered blanks stay `BLANK_PADDING` and this operation never
touches them.

### `HETKESEIS` decides whether work is running

Two labels end current work and nothing else does:

    jõustunud
    rohkem pole tegevusi plaanis

Every other controlled value — `idee`, `kooskõlastusringil`, `valitsuses`,
`Riigikogus`, `ootan jõustumist`, `Eesti seisukoht`, `ELi menetluses`,
`ootan ELi õiguse ülevõtmist`, and `muu` — leaves the Matter current. `muu` is a
real status, not a gap, and is treated as one.

An unknown label leaves the Matter **current**. That is the opposite of the
choice authorization makes, and deliberately: authorization whitelists because
showing too much is the harm, while here dropping live work off somebody's list
is the harm and an extra row on a dashboard is not.

`jõustunud` needs its own paragraph, because ADR 0012 says the opposite about it
and both statements are true. As an *import* interpretation it maps to a stage
and never to a closure — an act commencing is not Koda closing a file, and no
`closed_at` may be derived from it. As a *current-portfolio* question it is
terminal: once the act is in force there is no drafting step left to schedule.
Two different questions; this ADR answers only the second, and produces no
disposition and no timestamp in doing so.

### The reviewed current scope

*Added 2026-08-22, after the production dry-run this ADR's implementation failed.*

Everything above describes how a row is judged. It says nothing about **which
rows are judged**, because when it was written that seemed obvious: the
maintained register is the maintained register. The implementation therefore
reconciled every real row the approved snapshot contained, and the approved
snapshot contains every sheet back to 2011.

Run against production it proposed making **2219** Matters current instead of
200 — activating a little over two thousand rows from 2011 to 2024. The gate
caught it and nothing was applied.

The cause is the rule two paragraphs up, applied where it does not belong. *An
unknown label leaves the Matter current* is correct, and it is correct for the
reason given: dropping live work is the harm. But the 2011–2022 era contracts
have **no `HETKESEIS` column at all**, so those rows carry no label to be
unknown — the question is not answered, it is unasked. 2023 and 2024 have the
column and the department had stopped maintaining it. Reading that silence as
"still running" turns the entire archive into current work.

So an approved snapshot now carries **the years it was approved for**, beside
its digest, in `REVIEWED_SNAPSHOTS`. For `f38906c2…` those years are **2025 and
2026**: 415 of its 2458 real rows, producing 60 + 140 = 200 current Matters,
which is the portfolio this ADR always described.

Three things this is *not*:

* **It is not "a blank status means retired."** Inside the reviewed years a
  blank or unknown status still leaves the Matter current, unchanged. The scope
  decides which years the question is asked in; it does not change the answer.
* **It is not a filter.** All 2458 real rows are still classified and still get
  a `CurrentRegisterState` row. An out-of-scope row is retired *by scope*, with
  a reason and a rule identifier that say exactly that — never a terminal
  `HETKESEIS` the register never wrote, and never a disposition or a closure
  date.
* **It is not an operator option.** There is no `--years`. Turning 2014 back
  into current work takes a reviewed code change, for the same reason the digest
  list does.

Outside the reviewed years, ADR 0020's historical default stands — which is what
the *Refines* line above already said, and what the implementation had quietly
stopped honouring.

### `VÄLJA` decides whether the opinion is still being drafted — nothing else

`VÄLJA` is the date Koda sent its opinion. It says the **opinion-writing task**
finished. It does not say the **Matter** finished, and a populated `VÄLJA` on a
Matter still `kooskõlastusringil` is the ordinary shape of the work.

So `VÄLJA` never closes anything. What it answers is

> **Arvamusi koostamisel** = current Matter **and** no recorded send date

which is 15 on this snapshot.

It also never becomes a `Submission`. A SENT submission needs a defensible date
*and* immutable final evidence (ADR 0011); a date alone would create a sent
opinion nobody can produce. Where a canonical SENT Submission exists, that
Submission remains the outbound record and `VÄLJA` remains source metadata
beside it.

### Explicit continuation prevents double-counting

Where `JÄRGMISEKS` says the work moved to another reference, the old Matter is
not a second live file — it is the same work counted twice. The detector
requires **both** halves:

- continuation wording (`jätkub` and its inflections); and
- exactly one `YYYY_N` reference.

Either alone is insufficient. Wording with no reference does not say where the
work went; a bare reference is a cross-reference, which this register uses
constantly for related files. Wording with several references cannot say which,
and is reported for review rather than resolved by taking the first.

Order matters and the data shows why: **24** rows carry both halves and **22 of
them already hold a terminal status**, so continuation is evaluated *after*
`HETKESEIS` and removes two further rows rather than twenty-four. Reversing the
two would reach the same set today and stop doing so the first time somebody
writes a continuation note on a live row.

### `JÄRGMISEKS` is preserved as an instruction, never fabricated into workflow

The latest source text is kept and displayed, labelled as the register's:

> **Järgmiseks (Excelist):** …

It creates no `NextAction` row, no `DO`/`WAIT`/`MONITOR` kind, no
`DEADLINE`/`REVIEW_ON`/`EXPECTED_AROUND` semantic, no date and no overdue state.
The same sentence in this corpus carries a deadline, a review reminder and a
guess about a ministry's timetable interchangeably, and that ambiguity is
precisely why the register's one date column was never trustworthy (ADR 0011).

It is shown **only** where no structured action exists. Once a lawyer writes a
`Järgmiseks` here, the native workflow is the operational authority and the
older Excel wording would invite somebody to act on whichever they read first.

### Leaving the current set invents nothing

A Matter the snapshot retires becomes

    record_mode = ARCHIVE, is_open = False, disposition = "", closed_at = NULL

which is the shape ADR 0020 established and the closure constraint already
permits. It reads *the final register no longer lists this as current; the exact
closure fact is unknown* — which is the truth, because the register records no
closure date, reason or person for any of these rows. `close_matter()` is
untouched and unused here: it means a person is closing live work now, and
rightly demands all three.

### The tie always goes to the person

Four situations produce `REVIEW_REQUIRED` rather than a write:

- the Matter carries a **real** recorded closure (a disposition or a
  `closed_at`) — reversing or restating a professional decision is that
  person's call, and the Stage-2I default is safely reversible precisely
  because it invented neither;
- continuation wording that does not name exactly one reference;
- the snapshot would retire a Matter that carries **authored entries**, an
  **open next action**, or a **submission made here**.

Natively created Matters are never touched at all, however confidently a row
appears to describe the same subject.

### Owners resolve conservatively, or not at all

Source responsibility across the current set is Ireen 102, Sandra 52, Marko 43,
Ann 2, and one row with no name — 200. `Ann` matches no account, and no account
is invented: those two Matters keep their canonical owner unresolved, and the
raw name is retained as source responsibility.

That is why the dashboard's responsibility breakdown groups by the register's
own name rather than by `Matter.owner`. Grouping by the resolved account would
file those two under *Määramata*, discarding the one thing the register is
certain about; inventing an account to hold them would be worse. Supplying an
account for Ann later resolves them with no re-import.

### After this, Juristid is the system of record

This is the **final** Excel-era snapshot. There is no two-way sync, and no
further workbook is expected to carry authority over current state. A later
snapshot could be catalogued as additional immutable evidence, but the operational
record from here is this application.

## Derived state, and why there is a table for it

`CurrentRegisterState` holds one row per Matter: what the approved snapshot said,
and this ADR's interpretation of it. It is **derived data** in exactly the sense
`SearchDocument` is (ADR 0013) — deletable in full, rebuildable from the
immutable `MatterSourceReference` rows it reads, never consulted to decide
anything canonical, and storing no visibility.

It exists because the facts, while already stored, are stored in the shape
provenance needs and not the shape a question needs: `source_row_raw` is keyed by
column *letter*, and the letter differs between eras, so "`VÄLJA` is blank" is not
a predicate any queryset can express and "the latest snapshot's row" is a
per-Matter subquery before the question begins.

The dependency direction is deliberate. `Arvamusi koostamisel` leads with the
**canonical** half — open FULL Matters this reader may see — and consults the
derived table only for the one fact it is authoritative for. A lawyer who closes
a Matter today drops out of the count on the next page load, with nobody
re-running the cutover.

## What was considered and rejected

**Reading `VÄLJA` as closure.** It is the commonest misreading of this register
and it would archive 185 of the 195 real 2026 rows, including work in front of
the Riigikogu.

**Creating SENT Submissions from `VÄLJA`.** A sent opinion with no evidence
breaks the Stage-1 invariant and would put an unproducible claim into the
canonical record.

**Deriving a `NextAction` from `JÄRGMISEKS`.** Rejected in ADR 0011 and rejected
again here for the same reason, now with 134 instructions that would have been
converted.

**Following continuation chains.** `2025_10 → 2026_55 → 2026_190` exists in the
data. Each link is evaluated independently and the chain resolves itself; walking
it would add a traversal with cycle risk and change no outcome.

**A two-way Excel sync.** Explicitly out of scope. The register stops being
authoritative here.

**Overwriting `title` in the field refresh.** The register's wording and the
department's may both be right, later native editing is real work, and the title
is what people navigate by.

## Consequences

- 200 current Matters, spanning two register years, replacing a year-only rule
  that had 140 of them wrong.
- Two Matters carry an unresolved canonical owner until an account for Ann exists.
- One new derived table, one new migration, no change to any canonical schema.
- `promote_current_register` and `historical_cutover_state` are untouched and
  still describe what they did; this operation is additive and idempotent.
