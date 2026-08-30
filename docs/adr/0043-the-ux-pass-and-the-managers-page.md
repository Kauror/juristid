# ADR 0043 — The 2026-08-27 UX pass: an additive layer, views that live in the address, and Osakond narrowed to three questions

- Status: accepted; the parts describing `Ülevaade` and `Osakonna töö` as two
  destinations are **superseded by ADR 0049**, which merged them into one page
  at `/osakond/`. What this record decided about the components — the Seis
  strip, the team table, Eesolev, Tehtud, `?too=tahtaeg-vahemik` — still holds;
  they are the same components on the merged page.
- Date: 2026-08-28
- Stage: pre-QA (shared-gate development phase)
- Related: ADR 0030 (the Teema workspace redesign this refines), ADR 0033
  (`?too=` and drill-down parity, which this extends), ADR 0034 (why
  `Osakonna töö` is offered by role), ADR 0036 (who work may be assigned to),
  ADR 0037 (the business-write HTTP boundary the new routes sit behind),
  ADR 0039 (retiring `Minu tiim`)

## Context

An approved design handoff arrived as HTML prototypes built from this
application's own stylesheet and tokens: five corrections to existing screens
(1a–1e), one register addition (2d), and a rebuild of `Osakonna töö`. Four of
its decisions are architectural rather than visual, and each of them had a
tempting wrong answer.

## Decision

### 1. The pass ships as its own stylesheet and script

`static/css/ux.css` loads after `app.css`; `static/js/ux.js` loads after
`app.js`. Every rule and every binding names something the pass introduced, and
the two exceptions are declared where they sit: `.workrow2 { position: relative }`
(the containing block a quick-complete button needs, changing no pixel on its
own) and `.railrow__value--danger` (a modifier the existing family was missing).

The alternative — folding the new rules into `app.css` — would have made the
pass unreviewable as a unit and impossible to reason about against the parallel
authorization work landing beside it. A contract test enumerates the selectors
in `ux.css` and fails if a third production class appears there.

**Handoff fix 0 needed no code.** The reported symptom is an empty persona pill
in the top bar, caused by `.personamenu { display: flex }` beating the user
agent's `[hidden]`. Two rules already prevent it on this main: the global
`[hidden] { display: none !important }` reset in `base.css`, which is
`!important` precisely so no component can out-specify it, and the component's
own `[hidden]` rule beside its `display`. A third copy would add nothing and
would have to be exempted from the `!important` contract, so the two that exist
are pinned by a test instead.

### 2. A saved view is a named URL, and nothing is stored

The register has kept its whole state in the address since Stage 2E.1: a
narrowing is bookmarkable, pasteable and reachable with Back (master
specification 7.4). Four named views therefore introduce **no model, no
user-preferences table and no session state**. Each chip is a link whose query
string *is* its definition; its count is those parameters run through
`register_population`, so a chip reading 12 opens twelve rows.

`+ Salvesta praegune filter vaatena` hands over the current address rather than
pretending to save something. A named custom view would need somewhere to live,
and there is nowhere for it to live that a bookmark does not already cover
better — while a preferences table would give the product a second place where
"what am I looking at" lives, and the first place already survives a refresh, a
colleague and a browser restart.

**Rejected:** a `SavedView` model keyed on user. It would have to answer sharing,
staleness, ownership, deletion and migration, for a feature whose whole value is
that the answer already exists.

### 3. `?too=tahtaeg-vahemik` — one parameterised work population

ADR 0033 made the dated-work populations addressable: one function turns the
shared read model into a set of Matter primary keys, a figure counts that set,
and the register's `?too=` narrows to the same set. Osakond's *Eesolev* groups
the department's deadlines into today, tomorrow, the rest of next week and the
month after — four windows read once, and every one of them owes a `kõik N →`
that opens exactly what it counted.

Four more fixed population names would have been four names nobody reuses. So
the vocabulary gains **one** value that takes an argument: `?too_alates=` and
`?too_kuni=` narrow it, exactly as `?too_vastutaja=` already narrows `?too=`.
With no window given it means "every real deadline from today on", which makes
it a legitimate thing to pick from the register's own control rather than a
value that selects nothing without companions.

Two further named populations arrive with it and are *not* parameterised,
because both are read from more than one place: `tahtaeg-30` and
`tahtaeg-kaugemal` complete Ülevaade's deadline panel, and `muutusteta-30` is
the department's silence.

> **Superseded in part by ADR 0046.** Ülevaade's deadline panel now cuts two
> windows — the calendar week and the rest of the month — and links all of them
> through `?too=tahtaeg-vahemik`, so `tahtaeg-30` and `tahtaeg-kaugemal` no
> longer feed it. Both remain register populations, with their meanings
> unchanged; `muutusteta-30` is untouched.

`?too_alates=` is deliberately distinct from `?tahtaeg_alates=`. The latter
filters a Matter's own `Arvamuse tähtaeg` column; the former filters the dated
work model, which also holds `Oluline tähtaeg` and excludes a WAIT's expected
date. Conflating them would give the same words two meanings in one URL.

### 4. Deferring is two acts behind one control

`Lükka edasi` moves the current step's date. On a **DO** that is a commitment
Koda made, so moving it is a new instruction that supersedes the old one through
`set_next_action_for_new_work` — chain, audit row and refusals included, with
the responsible person carried over explicitly so the default cannot hand a
colleague's instruction to the Matter's owner. On a **WAIT** or **MONITOR** the
date is a review date, so the control is called `Vaatasin üle` and calls
`acknowledge_review`: the Matter is still waiting on the same thing and the
action keeps its identity.

The view chooses between two existing services and computes no business rule of
its own. It is offered only where the date is exact: a day added to a period
somebody deliberately recorded as *september 2026* would be a day nobody chose
(master specification 3.5).

### 5. Osakond answers three questions, and the rest keeps its path

The page was six KPI cards and four tables. It is now *Meeskond*, *Eesolev* and
*Tehtud* under a risk strip, with four decisions and the year's output in the
rail. Four sections left it, and none of them lost its path: the cross-team
intervention list is Ülevaade's (`?vaade=osakond`), `Ülevaatus või ootamine` is
`?too=ulevaatamiseks`, per-lawyer *recently received* is
`?vastutaja=…&saabus_alates=…`, and `Aktiivne teema ilma hetkeseisuta` is
`?hetkeseis=puudub` and Statistika → Andmekvaliteet.

Three columns of the team table count history — what changed last week, what
went out last week, what has gone out this year — and the register lists Matters
by their *current* state. Those three carry **no link at all**. A link to a list
that does not match the number above it is worse than no link (Stage-2F brief
35), and the alternative convention this replaces — a link to a deliberate
superset, marked with an asterisk — asked the reader to hold a footnote in their
head while scanning a grid.

The total row is the sum of the rows above it rather than a tenth set of
queries, so it reconciles with the risk strip by construction; a test asserts
the reconciliation so the construction cannot quietly change.

**Nothing about who may read the page changed.** It is still gated on
`is_department_head` and answers 404 to everybody else, and every population
still starts from `visible_to(user)` (ADR 0034).

## Consequences

- The register's filter vocabulary gains three population values and two
  companion date parameters. Both are covered by the existing chip, clear-all
  and shared-link machinery because they were added to `FILTER_LABELS` rather
  than handled beside it.
- Two new mutating routes — `assign_owner` and `defer_action`, plus
  `complete_work_item` on Minu töö — sit behind the same business-write
  decorator as every other one and are in the boundary matrix (ADR 0037).
- No schema migration and no business-data migration. Nothing here stores
  anything new.
- Osakond costs a constant ~70 queries, measured at 3, 12 and 30 people and 3,
  12, 30 and 60 Matters. Reaching that constant found a real defect on the way:
  the last-activity population carried a `.only()` while `activity_of` reads four
  stored columns, so it was issuing one query per row — silently, and invisibly
  on a development database.
