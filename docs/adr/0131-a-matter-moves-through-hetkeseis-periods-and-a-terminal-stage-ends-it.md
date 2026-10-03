# 0131 — A Matter moves through `Hetkeseis` periods, and a terminal stage ends it

**Status:** accepted
**Date:** 2026-10-02

The product owner's workflow decision after structured lawyer and customer
feedback. Three migrations — one additive table, one additive column and one
vocabulary row — plus one data migration that gives every active Matter holding
a stage its current period, with no start.

1. **`Hetkeseis` is episodic.** A Matter progresses through successive periods,
   each one the time it held one stage. `Matter.stage` is still where the file
   stands now; the periods are the history beside it.
2. **Every move is a new period** — forward, backward, or back to a stage held
   before. A period is never reopened and never rewritten; its end is the only
   thing that is ever written to it after it begins.
3. **What is not known is not invented.** A stage a Matter already held when
   periods were introduced, and a stage an importer wrote, has no start.
4. **Work belongs to the period it was done in**, tied once, at the moment of the
   act, through the audit seam every act already writes.
5. **An act saved together with a move belongs to the period before the move.**
   On a Matter with no stage yet, it belongs to the first period.
6. **One transition service.** Every surface that changes `Matter.stage` goes
   through it.
7. **`Teema käik` is grouped by period**: the current one open, earlier ones
   closed, any number open at once. The next-stage picker in `+ Märge` and
   `+ Koja arvamus` is ordered by where the file is likely heading.
8. **The heading** is the stage and a compact period: `5.26–10.26`, `10.26`,
   `alates 10.26`, `–10.26`.
9. **«Rohkem ei tegele» is a current stage** (`monitoring_stopped`).
10. **«Jõustunud» and «Rohkem ei tegele» close the Matter**; «Jõustumise ootel»
    does not.
11. **`+ Lõpeta teema` is gone.** The closure machinery behind it is not.
12. **Reopening names the stage the file reopens in**, as a new period.
13. **`Menetluse kulg` keeps the procedure and the opinions that went out**, and
    loses the operational points.
14. **One fact, one word**: «Valdkond» and «Arvamuse tähtaeg» on every surface
    that asks or shows them.

---

## Context

Lawyers read a file in the stages it went through. A bill was an idea, went out
for consultation, reached the government, came back for a second round, went to
the Riigikogu. `Teema käik` answered «what happened» as one list, newest first,
and `Hetkeseis` answered «where is it now» as one value. Nothing answered «what
did we do while it was at the consultation round» — and when the file went back
for a second round, nothing could even say there had been two.

docs/adr/0098 tried to group the history once, by the *procedure's* phases, and
docs/adr/0105 §1 retired it: a phase had to be inferred per row from business
dates, ambiguous rows piled up under `Etapiga sidumata`, and the list stopped
being chronological. The lesson kept here is that a grouping must be **recorded,
not inferred**: the period an act belongs to is a fact written when the act is
done, not a reading of its date against a roadmap.

The second half of the feedback was about ending a file. «Lõpeta teema» asked
how the file ended and for a last word, beside a `Hetkeseis` that could already
say «Jõustunud». Two controls answered one question, and the lawyers' own word
for «we have stopped» — «Rohkem ei tegele» — was not a stage at all
(docs/adr/0032 and `app/workflow/reference_stages.py` decided it was a
disposition, and that decision is superseded here, §9).

---

## Decision

### 1. `Hetkeseis` periods

`MatterStageEpisode` — Matter, stage, a per-Matter `sequence`, `origin`,
`started_at`, `ended_at`, `is_current`.

* **At most one current period per Matter** — a partial unique constraint, so
  two concurrent transitions cannot both open one.
* **Repeated stages are ordinary.** Nothing is unique on `(matter, stage)`.
* **`Määramata` is not a period.** `stage` is required; a Matter created with no
  stage has none, and its first begins when its first stage is chosen. Clearing
  the stage ends the current period and opens none.
* **The same stage again is not a move**: no period, no event.
* **The times are Juristid's own.** `started_at` and `ended_at` are when this
  system recorded the transition. When the ministry actually sent the draft is a
  procedural date a lawyer states on `Menetluse kulg`, and it is never derived
  from a period.

### 2. What is never rewritten

A period's stage, start and sequence are written once. Its end is written once,
by the transition that begins the next period. A backward move — `Riigikogus →
Kooskõlastusringil` — opens a new row; the first consultation round keeps
exactly what it recorded. A reopened Matter keeps its «Jõustunud» period as
history.

### 3. Legacy and imported data: nothing invented

`origin` says how Juristid came to know a period, and therefore what its start
means:

* `RECORDED` — Juristid recorded the transition; `started_at` is required.
* `CARRIED_OVER` — the stage a Matter already held when periods were introduced
  (`matters/0043`), or held from before a register reactivation. **No start.**
* `IMPORTED` — a stage written by an importer or the register refresh. **No
  start**, and an importer's move records no end either.

A `CHECK` holds `started_at` to `origin`. The migration seeds a `CARRIED_OVER`
period for every open, non-deleted Matter holding a stage, and nothing else:
no closed Matter, no Matter without a stage, no earlier period. **No earlier
history is reconstructed** — not from the audit trail, not from activity dates,
not from OneNote prose. The audit trail could not do it honestly anyway: a stage
chosen on `Uus teema` writes no `MATTER_STAGE_CHANGED`, so the start of a
Matter's first stage is not on record. **No existing activity is tied to the
seeded period.** Work recorded before it reads as «Varasem tegevus».

A period whose start is unknown prints only what is known (§8).

### 4. Work is tied to a period through `ChangeEvent`

`ChangeEvent.stage_episode`, one nullable column on the audit seam. Every act on
`Teema käik` already writes a `ChangeEvent` naming its record and its
operation, so one column ties a note, an opinion, a consultation, a file and a
step to their period at once — where a column on each business model would be a
dozen columns saying one thing. `record_change_event` fills it from the
execution context; a caller names it only where it knows better (§5).

**Durable.** The binding is written with the act and the audit table is
append-only. Nothing reads today's `Matter.stage` to decide where an old row
belongs.

`SecurityAuditEvent` is untouched and stays out of `Teema käik`.

### 5. An act saved with a move belongs to the period before it

One save that sends a Koja arvamus and moves the file to «Kooskõlastusringil»:
the opinion was written about the idea, so it reads under «Idee», and
«Kooskõlastusringil» begins after it. The transition pins the outgoing period to
the save before it ends it, so every row the save writes — before the move or
after it — names that period (`app.audit.operations`). The `MATTER_STAGE_CHANGED`
row itself belongs to the period it opens.

On a Matter with no stage, there is no outgoing period: an act saved with the
first stage belongs to the first period.

A closure made by a terminal stage writes its `MATTER_CLOSED` in the terminal
period, and a reopening writes `MATTER_REOPENED` in the period it opens.

`Muuda teemat` corrects several fields as separate facts; its save is one scope
for this purpose, and its stage change is made last.

### 6. One transition service

`change_stage` / `stage_transition` (`app/matters/services.py`) is the one way
`Matter.stage` changes for ordinary work. Under the Matter's row lock and in one
transaction it ends the outgoing period, begins the incoming one, writes
`Matter.stage` and `MATTER_STAGE_CHANGED`, pins the period for the save, and —
on the way out of the block, after whatever the caller recorded inside it —
closes the Matter when the stage ends it.

Its callers: `Uus teema` (`create_matter` opens the first period), `Muuda
teemat`, the header's `Hetkeseis` control, `+ Märge`, `+ Koja arvamus`, and
reopening. The register refresh moves the period through the same bookkeeping
as an import (`IMPORTED`, no end, no closure — retiring imported work is the
register cutover's decision).

### 7. `Teema käik`, grouped — and the next-stage order

**Grouped by period** (`app/matters/episode_timeline.py`): one accordion per
period, newest first; the current one open and collapsible, every earlier one
closed; native `<details>`, so opening one closes nothing. Inside, the rows are
`matter_timeline`'s own, in its own order — one operation is still one row.
Work with no period is placed by the moment it was recorded against the moments
periods began — both immutable system times — under «Varasem tegevus» before the
first period and «Hetkeseis määramata» between two. A Matter that has never had
a period keeps the flat, paged list.

**No boundary rows.** A stage move recorded since periods exist draws no row,
and a `Märge` whose only content was the move draws none: the heading says it.
The events and records are untouched and read in `Kõik muudatused`. A move from
before periods keeps its row in «Varasem tegevus», where no heading says it.

**Whole history in one read.** A period's rows are not a time window — an
opinion recorded in this period can carry last period's date — so the grouped
view reads every row of the Matter once and places each. The flat view still
pages by time (ENG-018, ENG-125); the grouped one trades that bound for being
right, which on the files Juristid now holds costs a handful of rows.

**The next-stage order** (`app/workflow/stage_flow.py`), for `+ Märge → Uus
hetkeseis` and `+ Koja arvamus → Uus hetkeseis` only:

* the procedure the file is in — the current stage if it belongs to one, else
  the most recent period that did, else the reviewed domestic/European
  `Õigusakt` grouping, else none;
* within it: the road ahead; then «Jõustunud», «Muu», «Rohkem ei tegele»; then
  the road behind; then any other stage the Matter has held;
* from «ELi õiguse ülevõtmise ootel» the road ahead begins with «Idee» and
  «Kooskõlastusringil» — the domestic half of the same Matter, with no domestic
  `Õigusakt` required. Once a domestic stage is chosen the file is domestic;
* with no procedure known, every stage in the vocabulary's order.

The current stage is not offered and `Määramata` is never a period. It is an
**order, not a rule**: the form accepts every active stage, nothing is disabled,
no confirmation is asked, and no score, model or warning exists. `Matter.track`
is neither read nor written.

### 8. The heading

The stage, a compact period, a count; «2. kord» for a repeated stage; the
current period marked by weight and the accent bar the page already uses.

| Period | Prints |
| --- | --- |
| across months | `5.26–10.26` |
| within one month | `10.26` |
| current | `alates 10.26` |
| ended, start unknown | `–10.26` |
| current, start unknown | nothing |

Never `10.26–10.26`, and never a start month a record does not state.

### 9. «Rohkem ei tegele» is a current stage

`monitoring_stopped`, label «Rohkem ei tegele», sorted last — reference
vocabulary version 3.0 (`app/workflow/reference_stages.py`, `workflow/0010`).
It is never dimmed by docs/adr/0130's guidance.

**This intentionally supersedes** the rule that «Rohkem ei tegele» is only a
disposition (docs/adr/0032 as reasoned in `reference_stages.py`, and the
docs/adr/0090 statement upholding it). Koda stopping is still recorded as
`Disposition.MONITORING_STOPPED`; the stage is how a lawyer says so.

**History is not reread.** The register's «rohkem pole tegevusi plaanis» is
still read as that disposition, by `workflow/0004` and `app.workflow.vocabulary`;
no imported row is reinterpreted as having held the new stage.

### 10. Terminal stages close the Matter

«Jõustunud» closes an open Matter with `Disposition.COMPLETED`, «Rohkem ei
tegele» with `Disposition.MONITORING_STOPPED`, in the same transaction, through
`close_matter` — so the open step, planned write-ups and feedback waits end
exactly as every closure ends them (`end_live_work_for_closure`). «Jõustumise
ootel» is not terminal.

This applies wherever a stage is chosen: `+ Märge`, `+ Koja arvamus`,
`Muuda teemat`, the header control, and `Uus teema` (a Teema filed as
«Jõustunud» is filed closed, without its first step). A save that names a
terminal stage *and* a next step is refused whole; a future-dated `Märge` with
a terminal stage makes no step. A terminal option says so in its own label —
«Jõustunud — lõpetab teema» — and no dialog asks again.

**Existing data is not closed.** An open Matter that already stood in
«Jõustunud» stays open, carried over as it was; the integrity check reports a
*recorded* terminal period on an open Matter, never a carried-over one.
Production held none.

### 11. `+ Lõpeta teema` is removed

The launcher chip, its panel, its form, its workspace use case and its route are
gone; the superseded composer endpoint refuses a closure answer; the edit page
points to the stage instead. **`close_matter`, `Disposition`, the closure
constraint, `MATTER_CLOSED`, the closed banner and every report are unchanged**
— the ordinary route into them is now `Hetkeseis`. A win is recorded from
`+ Märge → Töövõit`.

### 12. Reopening

«Ava uuesti» on the closed banner names the stage the file reopens in —
required, never `Määramata`, never a terminal stage, offered in the file's
procedure order. `reopen_matter_into_stage` moves the stage first (the terminal
period ends, a new one begins), then clears the closure through `reopen_matter`,
so the file is never open while still reading «Jõustunud». Reopening into the
stage a pre-period closure left the file in continues that period. The register's
own reactivation still calls `reopen_matter` and gets a carried-over period.

**Amended 2026-10-03 (RULE-03): reopening is the only way a closed file's stage
moves.** A closed Matter stays in the period it was closed in — terminal or,
for a file closed before `Hetkeseis` could close it, ordinary. The ordinary
stage door (`change_stage`, behind the header's control and `Muuda teemat`)
refuses a move on a closed Matter with one sentence, «Suletud teema
hetkeseisu ei saa muuta. Ava teema esmalt uuesti.», decided on the locked row
so a tab that still showed the file open cannot move it after a colleague's
closure. Clearing the stage is a move. The stage it already holds is not, so a
closed file's other corrections still save, and `Muuda teemat` stays one save:
a refused stage refuses the title beside it. The closed header states the
stage and offers no editor; `Muuda teemat` states it read-only; `Ava uuesti` is
where the next stage is chosen. The register refresh is separate and
unchanged: it turns the period as an import and never closes or reopens.

Linking a later, separate Matter to an earlier one (a new amendment to a law
that finished) is a different question and is deferred; nothing here assumes a
Matter cannot restart.

### 13. `Menetluse kulg` keeps the procedure and what Koda sent

`Teema käik` is what Koda did, grouped by period. `Menetluse kulg` is the
high-level procedure: its phases, the formal dates (`Arvamuse tähtaeg`,
`Ülevõtmise tähtaeg`, `Jõustumine`, `Lõpetatud`) and **every Koja arvamus that
actually went out**. Two changes:

* **Sent means sent.** It read `.sent()`, so a withdrawn opinion vanished from
  the rail; it now reads `historically_sent()` and a withdrawn or superseded one
  stays, its status as the column's detail. A draft is not a point; a working
  document is not a point.
* **No operational points.** `Tagasiside tähtaeg` (docs/adr/0083) and the
  lawyer-named watched `Oluline tähtaeg` columns (QA-001) are gone from the rail;
  both read in `Teema käik` — the round's reply-by day on its row, a future
  deadline as «Eesolev tähtaeg». ADR 0083's reason was visibility at a glance,
  and the owner judged the rail clearer without it. No record changes.

The two surfaces are not competing timelines: one is the procedure, one is the
work. Episode system times never replace a procedural date.

### 14. One fact, one word

On `Uus teema`, `Muuda teemat` and the Teema page: «Valdkond» (still
`policy_areas`, still several values), «Arvamuse tähtaeg» in the header (it read
«Tähtaeg» beside an editor headed «Arvamuse tähtaeg»), «Saatja» for the rail's
hidden legend, «Saabus» in the edit-conflict summary. Kept on purpose:
«Menetluse lingid» on the card that lists several links, and the department
rail's «Valdkonnad», which is a list of areas rather than one Matter's answer.

---

## Alternatives considered

* **Infer membership at render time from business dates.** docs/adr/0098 did,
  and 0105 retired it; a period must be recorded.
* **A foreign key to the period on every business model.** Twelve columns for
  one fact, each a place to forget it; the audit seam already ties every act to
  its operation.
* **Materialise past periods from `MATTER_STAGE_CHANGED`.** The first stage of
  every Matter has no event, so the reconstruction would be partial and would
  look complete. Not done.
* **Close on «Jõustunud» only on the next save, or ask to confirm.** Two
  answers to one question again; the label says what the save does.
* **A restricted wizard on `Muuda teemat`.** It is the correction surface and
  keeps the whole vocabulary.

## Consequences

* `Teema käik` on a Matter with periods reads its whole history in one pass.
* Every stage write is one more row in a small table and one more column on the
  audit row; every `ChangeEvent` on a Matter costs at most one extra indexed
  read per save to find the current period.
* Old tests that drove `+ Lõpeta teema` now drive the stage.
* Two new integrity checks run with the others (`check_domain_invariants`).

## Reversibility

The three schema migrations reverse cleanly; the data migration's reverse is a
no-op because dropping the table removes its rows. Reverting the code without
the schema would leave periods unwritten and `ChangeEvent.stage_episode` null —
truthful, like every row before this. Restoring `+ Lõpeta teema` is a template,
a form and a route; nothing it wrote was removed.

## Deferred

* The `Märge` composer redesign (the next product step).
* Linking a later Matter to an earlier one as its continuation.
* A gentle «add an Estonian Õigusakt» suggestion after the EU → domestic bridge.
