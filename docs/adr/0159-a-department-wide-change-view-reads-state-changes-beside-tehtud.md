# 0159 — A department-wide change view reads state changes beside Tehtud

**Status:** proposed — the read model is built; the page waits on an owner decision (§6)
**Date:** 2026-10-13

## Context

The owner asked for a department-wide answer to «mis meie poliitikatöös
valitud perioodil tegelikult muutus?» over 7 / 14 / 30 days and a custom range:
new Teemad, `Hetkeseis` moves, hand-overs, next steps set and finished,
closures and reopenings, sent opinions — each row linking to its Teema.

Two existing decisions stand in the way of simply drawing it.

* **ADR 0049 §7** merged `/ulevaade/` into `/osakond/` and dropped the
  *Viimased muudatused* feed: «There is no separate *Viimased muudatused* feed.
  Its question is what *Tehtud* answers, from canonical records rather than from
  the audit stream.» Its consequences record the gap — a stage change, an
  assignment and an added Kaasamine are carried by no canonical *output* — as
  the open item **DS-25** (`app/core/development_status.py`), whose next step
  is an owner decision: either a new `ChangeEvent`-reading kind inside *Tehtud*,
  or retiring `overview.activity_feed` and its tests.
* **ADR 0155** says a retired surface returns only by an owner decision recorded
  as an ADR amendment.

A page at `/osakond/muudatused/` is neither of DS-25's two options; it is a
third, and it is the department feed ADR 0049 removed in a new shape. So the
part of this work that renders it is **stopped and put to the owner** (§6). The
part that does not render anything — the read model — is built, because it is
the same work whichever way DS-25 is answered.

## Decision

### 1. One read model, `app.matters.department_changes`

`build_changes(viewer, params, today=…)` returns `DepartmentChanges`: the
window, a refused custom range in words, five categories with counts and rows,
coverage figures, a snapshot comparison, and *Tehtud*'s own `Digest` for the
same window. No model, no migration, no new event type, no write.

### 2. State changes, not outputs — Tehtud is reused, never restated

| Category | Source | Rows read |
| --- | --- | --- |
| Uued teemad | `MATTER_CREATED` on a Teema whose `origin` is `NATIVE` | the Teema |
| Hetkeseisu muutused | `MATTER_STAGE_CHANGED` | `from_label → to_label` |
| Vastutaja muutused | `MATTER_ASSIGNED` | `from_name → to_name` |
| Järgmised tegevused | `NEXT_ACTION_SET` / `_COMPLETED` / `_CANCELLED` / `_RESCHEDULED`, counted per kind | verb only, never the step's words |
| Taasavatud teemad | `MATTER_REOPENED` | the Teema |

* **Sent opinions, closures and work victories are *Tehtud*'s.** The view
  carries `build_digest(viewer, period)` for the identical `Period`, so the two
  sections cannot print two numbers for one fact. A closure in particular stays
  there: «closed now, closed in the window» (Tehtud) and «somebody pressed
  Sulge» (an event count) diverge the first time a Teema is closed and reopened
  in one week.
* **Not counted:** `NEXT_ACTION_REVIEWED` (a confirmation, not a move), a rename,
  every field edit, cutover/refresh events, `MATTER_PROMOTED`, and anything not
  named in a category — the curated-whitelist rule `Kõik muudatused` and the
  old feed already follow.
* **A person's act only.** An event counts when it has an `actor`. Importers,
  the excel pilot, cutovers and the owner backfill write theirs with none; those
  are counted aside as `Coverage.system_events`, never dropped silently.
* **Imported is not new.** A Teema whose `origin` is not `NATIVE` is never «uus»
  — it existed before it was copied in. Imports in the window are
  `Coverage.imported_matters`. A colleague's later change *to* an imported
  Teema is an ordinary change.
* **Current work only.** Events on `RecordMode.ARCHIVE` Teemad are not read —
  the line *Tehtud* draws for closures.

### 3. Three kinds of knowledge, kept apart

* **Authoritative event history** — the categories above.
* **Snapshot-derived comparison** — open Teemad and Teemad without a Vastutaja
  on the last `OperationalMatterSnapshot` at or before each end of the window,
  read through `visible_snapshots` (live-Matter authorization), with both dates
  returned. One photograph, or none before the window, means no comparison —
  nothing is reconstructed from today. Only Matter-level columns are read; the
  photographed next step carries its own visibility and is not needed.
* **Coverage limits** — the first day a person's change was recorded in the
  reader's population (`starts_after(period)` marks a window reaching before
  it), system rows in the window, imports in the window, and open Teemad whose
  current `Hetkeseis` began at a moment nobody recorded
  (`StageEpisodeOrigin.CARRIED_OVER` / `IMPORTED`, ADR 0131 §3).

### 4. Authorization before arithmetic; who may open it

Matters through `matter_visibility_q` on one resolved scope, FULL only; child
events through `app.audit.visibility.scope_change_events`, so a step restricted
below its Teema reaches neither a count nor a row. Coverage counts come from
the same scoped population. The read model builds only what `viewer` may see;
**whether a reader may open the page is the caller's** and is proposed as
`may_view_department_management` — the `department.view_management` capability
whose default is the department head (ADR 0145), exactly *Tehtud*'s and
*Meeskond*'s gate, because this is a period report naming colleagues' acts
(ADR 0049 §3). No new role or capability.

### 5. The window is Tehtud's

`changes_period` delegates to `department_dashboard.period_from`, which gained
one key, `14` (`PERIOD_NAMES`); *Tehtud*'s control (`PERIOD_LABELS`) is
unchanged. Bounds are the project's zone (`Europe/Tallinn`) through `__date`
lookups, both ends inclusive. A custom range the reader typed and that cannot
be read, is incomplete or is reversed is refused **in words** and the window
falls back to the default — where an unknown named key falls back silently, as
`kind_from` does.

Queries are bounded: one grouped count, one capped row query per category
(`ROW_LIMIT` = 50, with `truncated` when the count is larger), four coverage
aggregates, at most three snapshot queries, plus the digest. The count does not
grow with rows (asserted).

### 6. Stopped for the owner: the page

Proposed, **not built**: `GET /osakond/muudatused/` (`matters:department_changes`)
in `app/matters/department_views.py`, `@gate_required`, 404 unless
`may_view_department_management(request.user)`, rendering the five categories
with counts, a «Tehtud samal perioodil» line linking to `/osakond/?periood=…#tehtud`,
the snapshot comparison and the coverage block; entry point one link from the
*Tehtud* section heading («Muudatused →»), no top-bar item (CONTRACT §1).

It needs one of:

1. **Amend ADR 0049 §7** to say a separate state-change view beside *Tehtud* is
   wanted, and close DS-25 that way — then the page above is built; or
2. **Answer DS-25 «yes, inside Tehtud»** — the categories become new
   `DigestRow` kinds read from this module, no new route; or
3. **Answer DS-25 «no»** — this module and its tests are withdrawn with
   `overview.activity_feed`.

## Consequences

- One additive key in `period_from` (`?periood=14` on `/osakond/` now reads a
  fortnight where it used to fall back to seven days).
- Inherited, not fixed here: `period_from`'s «7 päeva» is `today − 7 … today`,
  eight calendar days inclusive (and «30 päeva» thirty-one). The change view
  matches *Tehtud* so the two agree; changing the meaning is an owner call for
  both.
- `NEXT_ACTION_SET` is written for a new step and for a `Muuda` that replaces
  one; both read «määras järgmise tegevuse». One act that moved the stage and
  set a step counts once in each category; `operation_id` grouping is not used.
- Like *Tehtud*, populations are not narrowed to `real_data()`.

## Reversibility

Total: a read-only module, one parser key and tests. Nothing stored changes.
