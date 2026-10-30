# 0159 — A department-wide change view reads state changes beside Tehtud

**Status:** accepted — owner's decision of 2026-10-13 («Separate head-only page», option 1 of §6)
**Date:** 2026-10-13
**Amends:** ADR 0049 §7; answers DS-25

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
third, and it is the department feed ADR 0049 removed in a new shape. The first
round of this work therefore built only the read model and put the page to the
owner (§6). **The owner decided on 2026-10-13: a separate page, the department
head's only** — ADR 0049 §7 is amended to say so, and DS-25 is answered by it.

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

> **Amended 2026-10-14 — owner's decision D1 (QA remediation 2026-10-14,
> QA-DATESVIEWS-01).** Muudatused reflects a person's deliberate acts; one
> operation is never several user decisions. Presentation only — every audit
> row stays exactly as written.
>
> * **A step change the system made on a person's behalf is not their act**,
>   though its row names them as actor. Recognised by the marker its writer
>   puts in the payload (`department_changes._automatic_q`, JSON containment):
>   the first follow-up check a send schedules (`follow_up.automatic: true`,
>   ADR 0146 §10 — the send itself is *Tehtud*'s); a current check put back on
>   the plan by new work (`returned_to_plan: true`; the new work's own event,
>   which carries the check's id as a string, stays the person's); a planned
>   step made current because the current one was finished (`promoted` — it
>   was «määras» once, when planned); a check re-dated from a corrected send
>   date (`recalculated`). A check cancelled because its opinion was withdrawn
>   (reason «Arvamus võeti tagasi» with the `follow_up` block) is the
>   withdrawal's consequence, not a separate «tühistas» (owner, 2026-10-14);
>   so is one cancelled because its opinion was replaced (reason «Arvamus
>   asendati», same shape) — the replacement's consequence.
>   These are **counted aside and marked as the system's**
>   (`Coverage.automatic_steps`, «Süsteemi automaatseid tegevuse muudatusi …
>   mida ei loeta kellegi tegevuseks»), never as a person's «määras» or
>   «tühistas». A next check the lawyer dated (`automatic: false`) and a check
>   moved by a person remain that person's acts.
> * **One closure is one entry.** The `NEXT_ACTION_CANCELLED` rows a closure
>   writes for the current and every planned step (reason «Teema suleti», or a
>   `closure` key should a writer add one) are grouped by **the closure that
>   names them** — `MATTER_CLOSED.payload.ended.actions`, the ids that closure
>   ended (ADR 0152 §7) — and, for a closure recorded without that list, by the
>   closing event that follows them on their Matter (`MATTER_CLOSED`, or a
>   cutover's), into one «Järgmised tegevused» row: «sulges teema: lõppes N tegevust», in the
>   closer's name, counted once (breakdown «sulges teema»). A cancellation with
>   no closing event after it stands alone. The closure itself remains
>   *Tehtud*'s output; N is what the reader may see (scoped before grouping).

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

> **Amended 2026-10-13 — Accepted, owner's decision 2026-10-13.** One period
> calculation, `department_dashboard.period_from`, serves *Tehtud* and
> *Muudatused*; the same `periood` gives both the same two dates.
>
> * **«N päeva» is exactly N calendar dates including today**:
>   `today − (N − 1) … today` for 7, 14 and 30 (`last_days`, `ROLLING_DAYS`).
>   «7 päeva» on 13 October is 7–13 October. It used to be `today − N`, N + 1
>   dates under a label that says N.
> * **`periood=kvartal` is «Viimased 13 nädalat»**: rolling, exactly 91 calendar
>   dates including today (`today − 90 … today`), not a calendar quarter. The URL
>   value `kvartal` is kept so saved links still open the same window.
> * **«Aasta» is the current calendar year to date**: 1 January … today.
> * **A custom range** includes both typed dates.
> * Bounds are Europe/Tallinn local dates, and both pages print them
>   (`Period.bounds`, «7.10.2026–13.10.2026»); *Muudatused* also names the
>   window («Viimased 13 nädalat · …») because its chips are 7 / 14 / 30 only.
>   *Muudatused* reads every named key, not only its chips.

Queries are bounded: one grouped count, one capped row query per category
(`ROW_LIMIT` = 50, with `truncated` when the count is larger), four coverage
aggregates, at most three snapshot queries, plus the digest. The count does not
grow with rows (asserted). *(Superseded 2026-10-30 by §7: the per-category lists became one paginated table.)*

### 6. The page — `/osakond/muudatused/`

Three options were put to the owner: (1) amend ADR 0049 §7 and build a separate
page; (2) answer DS-25 «inside Tehtud» as new `DigestRow` kinds; (3) answer it
«no» and withdraw this module with `activity_feed`. **The owner chose (1) on
2026-10-13.**

`GET /osakond/muudatused/` (`matters:department_changes`,
`app/matters/department_views.py`), `@gate_required`, and **404** unless
`may_view_department_management(request.user)` — the authenticated identity,
the rule Meeskond and Tehtud are built by. Those two are sections and are not
built for anybody else; a page of its own answers the way the manager-only
`/osakonna-too/` did and a colleague's desk still does: it does not exist.
Nothing is computed before the check, so a shared-gate session without a
persona and a specialist get the same 404.

The page draws the five categories with counts and rows (title, verb,
before → after, actor, date, linking to the Teema), the window control (7 / 14 /
30 päeva and a custom range whose refusal is printed with the typed dates
redisplayed), an empty state, and a rail with three blocks kept apart:
«Tehtud samal perioodil» (Tehtud's own numbers and a link to `/osakond/` on the
same window, `#tehtud`), «Hetktõmmiste võrdlus», and «Mida see ei näita»
(coverage). Existing components and tokens only.

> **Amended 2026-10-13 (visual QA).** A window that ends before the first
> recorded change is **unknown, not empty**: its counts read «—», the heading
> «Muudatuste arv teadmata», and the coverage note says the whole window lies
> before recording began («Sel perioodil muudatusi veel ei salvestatud»). A
> window that starts before and ends after keeps «osaliselt kaetud». A row's
> change is its own element and wraps on this page only
> (`.uxdg__text--wrap`; Tehtud's digest keeps one line), and a refused range
> draws its form inline under the message rather than in a popover over it.

**Entry point:** one «Muudatused →» link in the *Tehtud* section heading on
`/osakond/`, carrying Tehtud's window (Kvartal / Aasta / a custom range travel as
their dates). It sits inside the digest's own condition, so it is the head's
exactly as Tehtud is. No top-bar item (the bar is coordinator-owned).

### 7. KPIs, a current-owner table, one filterable change table

> **Amended 2026-10-30 — notifications package, Feature E; owner's decision D4
> for the work-state history.** The page and its read model stay one
> (`build_changes`); no export, no second page.

**One scoped population.** The visible current-work Teemad
(`matter_visibility_q`, FULL), narrowed by the filters, then their curated
events through `scope_change_events` — authorization first, filters next,
aggregates last. The category counts, the KPI cards, the per-person table and
the detail rows are all read from it.

**KPI cards** (`department_changes._kpis`, each with its unit on the card —
«teemat», «muudatust», «arvamust» — so topics and events are never added):

| Card | Definition |
| --- | --- |
| Muudetud teemad | `COUNT(DISTINCT matter)` over the person-made curated events in the window, after the §2 exclusions (system rows, imports, automatic steps); a closure's cancelled steps are its one row, so its Teema counts once |
| Muudatusi | those events counted, a closure once — the page heading's number and the change table's total |
| Uued teemad | person-made `MATTER_CREATED` on `MatterOrigin.NATIVE` |
| Välja saadetud arvamused | `department_dashboard.sent_submissions(...).count()` — one per submission, counted whole (the digest lists at most `DIGEST_LIMIT`) |
| Lõpetatud teemad | `department_dashboard.closed_matters(...).count()` — *Tehtud*'s «suletud», now one function both use |
| Menetluses olevad teemad | the §3 snapshot comparison (open Teemad, start → end, net); «ei mõõdetud» without two photographs, never 0 |
| Tähelepanu vajavad / Tähtaja ületanud | today's value from the live canonical populations for this reader (`work_items.WORK_NEEDS_ATTENTION` / `WORK_OVERDUE`); start → end from the photographs' capture-time memberships (below), «ei mõõdetud» when either boundary did not record them. One isolated function, `work_state_kpis` |

Unfiltered, the two output cards are exactly *Tehtud*'s numbers for the window
(the rail keeps *Tehtud*'s own digest beside them).

**Per-person table — by the Teema's *current* owner** (`Matter.owner`), never
the actor: Marko's change on a file now carried by Kati counts on Kati's line,
while the change row still names Marko. «Vastutajata» is its own line; an
inactive owner keeps a named line marked «(mitteaktiivne)». Columns: Muudetud
teemad (distinct), Muudatusi, Uued teemad, Saadetud arvamused, Lõpetatud
teemad. Every column partitions its card's population by one key, so each sums
to the card exactly (asserted). Sortable by column (`?isikud=`).

**The change table replaces the five per-category lists.** One server-paginated
table (`PAGE_SIZE` = 50, `?leht=`), sorted in the database by time (default,
newest first), Teema title or category (`?sort=`). Columns: Aeg, Teema (link and
reference), Praegune vastutaja, Liik, Muudatus, Enne, Pärast, Tegija. A closure
stays one row («sulges teema: lõppes N tegevust»): its cancellations are grouped
by the §2 rule and represented by their latest event, which is what makes the
grouping sortable and paginable. The category counts stay above the table as
links that are the «Liik» filter. Only person acts are rows (system and
automatic rows stay coverage figures, as before). The rail — «Tehtud samal
perioodil», «Hetktõmmiste võrdlus», «Mida see ei näita» — is unchanged.

**Evidence rule for «Enne» / «Pärast».** Only what the writing service put in
the payload for exactly this: the stage labels (`from_label` / `to_label`), the
Vastutaja names (`from_name` / `to_name`), a re-dating's `from` / `to`, and a new
step's `target_date` (as «Pärast» only). A key recorded as empty is a recorded
fact — «määramata», «vastutajata», «kuupäevata». Where nothing recorded the
earlier value «Enne» is «teadmata»; where nothing recorded the later one
«Pärast» is empty. A new Teema's «Enne» is «—» (there was no earlier state).
Nothing is reconstructed from today's data; no free text is shown.

**Filters** (GET; every one is preserved across sort, page and window;
«Tühjenda filtrid» keeps only the window): the window (§5); `vastutaja` — a
current owner's id or `puudub`, chosen from the department's active workers plus
anyone who owns a visible Teema; `liik` — a category key; `otsi` — title
(case-insensitive) or a `YYYY_N` reference over the visible Teemad. An unknown
or unreadable value is no filter, never a 500. What each narrows:

* Vastutaja and search narrow the Teemad, so every event figure, both output
  cards, the work-state cards' current values, the people and the rows;
* «Liik» narrows the event cards, the people's event columns and the rows; the
  category counts beside it (so the reader can switch), the outputs and the
  work-state cards are not change categories and keep their population;
* the snapshot comparisons are department-wide; the card says so when a filter
  is active.

**Work-state history (D4).** `OperationalMatterSnapshot` gained four additive
columns (`reporting` migration 0003, forward only, no backfill):
`next_action_date_precision`, `next_action_responsible`, and two memberships,
`is_overdue` and `needs_attention`, decided at capture time by the live
canonical selectors over the whole department (`snapshots.work_state_memberships`,
evaluated as a business-read reader that is not a user). A row captured before
2026-10-30 holds NULL: «ei mõõdetud». A boundary is measured only when every
row of that day's photograph recorded the flags. Because a membership can be
caused by a restricted child the row does not name, `visible_snapshots`
releases the flags only to a scope that sees every restricted child; any other
scope reads NULL. Since ADR 0181 every business reader's scope does, so the
rule costs nothing today; it is stated and tested. The step's precision and
responsible person blank with the other step columns.

**Daily capture.** `snapshots.capture_daily_if_missing(now)` takes the day's
photograph once, from 03:00 Europe/Tallinn, under advisory lock `(24608, 1)`
(24607 is the Lovable sync's), and never re-captures later the same day — the
capture refreshes rows, which would overwrite the early-morning picture. The
`notifications` loop worker is to call it; the management command stays the
manual, refreshing path.

**Queries** stay constant in the data's size (asserted, filters included):
about thirty for the whole page, including the digest and one read of the work
model.

## Consequences

- One additive key in `period_from` (`?periood=14` on `/osakond/` now reads a
  fortnight where it used to fall back to seven days).
- **Superseded 2026-10-13 by the §5 amendment (owner's decision 2026-10-13):**
  `period_from`'s «7 päeva» was `today − 7 …
  today`, eight calendar days inclusive («14 päeva» fifteen, «30 päeva»
  thirty-one). Kept identical to *Tehtud* by the owner's instruction of
  2026-10-13, so the two sections agree; changing the meaning would change both.
- `ENGAGEMENT_ADDED` (a Kaasamine added), which the old feed carried, is not a
  category; nor are important dates, effective dates or work-victory proposals.
- `overview.activity_feed` and its tests stay; retiring them is separate.
- `NEXT_ACTION_SET` is written for a new step and for a `Muuda` that replaces
  one; both read «määras järgmise tegevuse» (system-made ones excepted, §2
  amendment 2026-10-14). One act that moved the stage and
  set a step counts once in each category; `operation_id` grouping is not used.
- Like *Tehtud*, populations are not narrowed to `real_data()`.

## Reversibility

Total: a read-only module, one parser key and tests. Nothing stored changes.
