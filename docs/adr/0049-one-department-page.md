# ADR 0049 — One department page: `/ulevaade/` and `/osakonna-too/` become `/osakond/`

- Status: accepted
- Date: 2026-08-30
- Stage: pre-QA (shared-gate development phase)
- Related: ADR 0033 (`?too=` makes the dated-work populations addressable),
  ADR 0039 (retiring *Minu tiim*), ADR 0043 (the UX pass and the manager's
  page), ADR 0046 (the deadline windows this record widens to five),
  ADR 0047 (Arvamused as a section of Teemad)

## Context

The department had two operational pages, and they answered the same question:
**kus osakond seisab.**

`/ulevaade/` was every reader's. It carried a Seis strip of six figures, the
*Vajab sekkumist* list, a *Tähtajad* panel over `real_deadlines`, a *Koormus*
rail of people and a *Viimased muudatused* activity feed.

`/osakonna-too/` was the department head's, gated on the role and 404 for
everybody else. It carried a Seis strip of six figures — three of them the same
figures — the *Meeskond* table, an *Eesolev* panel over the **same**
`real_deadlines` population cut into different windows, a *Tehtud* digest, a
second *Vajab sekkumist* block in its rail, and a second *Aruandlus*.

Six things were on both screens. Where two of them were the same population read
twice they could only agree by luck, and where they were *nearly* the same
population they were guaranteed to differ:

| One thing, twice | `/ulevaade/` | `/osakonna-too/` |
| --- | --- | --- |
| Seis | 6 figures | 6 figures, 3 shared |
| Deadlines | *Tähtajad*, 3 windows | *Eesolev*, 4 windows |
| Attention | the hero list | four rail counts of the same states |
| People | *Koormus* rail | *Meeskond* table |
| Reporting | *Aruandlus* | *Aruandlus*, different definitions |
| What happened | *Viimased muudatused* | *Tehtud* |

The navigation said the same thing about them that the pages did: `Ülevaade` was
a first-level destination and `Osakond` was a second one inside «Veel», visible
only to the head. One reader was offered two answers to one question, and had to
know which page a number lived on before they could look it up.

Two of the duplicated numbers were not merely redundant but wrong. The team
table's `ARVAMUSI VÄLJA · <aasta>` column counted **files that carried a sent
opinion** while Aruandlus's «Saadetud arvamusi» counted **opinions**, so a Matter
that produced two in a year made the two totals differ by one with no way for a
reader to find out why. And Aruandlus's «Töövõite kinnitatud» filtered
`MatterWorkVictory.status` on a value from a different fact's vocabulary
(`FactStatus.ACTIVE`), so it read zero however many the department had confirmed.

## Decision

### 1. One route, one page, one name

`/osakond/` (`matters:department`), headed **Osakond**, is the department's
operational page. It answers *kus osakond seisab* and nothing else answers it.

`Ülevaade` and `Osakonna töö` are no longer names of anything current. They
survive in this record, in the ADRs that describe the surfaces as they were, and
in the compatibility routes below.

### 2. Both old addresses redirect permanently, with their query strings

```
/ulevaade/      301 → /osakond/     (name: matters:overview_legacy)
/osakonna-too/  301 → /osakond/     (name: matters:department_work)
```

`query_string=True`, so `?vaade=valdkonniti` arrives as a scope of the new page
and `?periood=vahemik&alates=…&kuni=…` arrives as the Tehtud window it named.
One hop each: neither redirects via the other.

The route name `matters:overview` is kept, resolving to `/osakond/` itself
rather than to the compatibility redirect. Two callers outside this page's scope
— `app/core/views.py::home` and the sign-in redirect in
`app/accounts/views.py` — reverse that name to decide where somebody lands, and
they belong to the parallel branch moving the root to *Minu asjad*. Pointing the
name at the canonical path means neither file had to be edited and neither now
sends a reader through a redirect they do not need.

### 3. Read access is Ülevaade's; two sections keep Osakonna töö's

The page is `@gate_required`, exactly as Ülevaade was: a shared-gate session
with no persona lands on it straight from the password and reads the department
as the `DEPARTMENT_VIEWER` sentinel — NORMAL visibility, no participation.
Specialists, readers and the technical administrator open it as they opened
Ülevaade.

**Meeskond** and **Tehtud** remain the department head's. They are decided by
the authenticated role (`is_department_head(request.user)`), never by the viewer
the page is authorized as, and for anybody else they are **not built** —
`build_department` is told, and the nine grouped team queries and the digest are
never run. An unauthorized section is not calculated; it is not calculated and
then hidden.

No new permission was created and no existing authority was widened. What
changed is that the *page* stopped being manager-only, because it had to keep
the read access the page it replaced already had.

### 4. Six figures, one strip

`üle tähtaja · tähtaeg sel nädalal · vastutajata · uut läbi vaatamata ·
järgmise tegevuseta · arvamust välja · 7 p`

Each is register parameters counted through the register's own filter pipeline
and linked as the same parameters, so the number and the list are one query, and
each link carries `#tulemused` so a reader who clicked a count lands on the rows
rather than on the filter panel above them. «Uut sel nädalal» left the strip for
the *Uued teemad* rail — arrival is not risk — and «järgmise tegevuseta» took its
place. «Avatud teemat» is the header's.

The sixth carries **no link**. It counts a seven-day window and the Arvamused
workspace narrows by year and by month, so the only destination available holds
more letters than the number beside it. An honest number beats a link to a
different list — the treatment the team table's three historical columns already
get — and giving the workspace a date-range filter is a separate decision
(DS-24). Both halves of this were found by the browser suite's parity sweep the
first time the two pages' figures stood on one strip.

### 5. One deadline panel, five windows

*Eesolev* replaces both `Tähtajad` and the old four-window `Eesolev`:

| Window | Interval | Shown |
| --- | --- | --- |
| TÄNA | today | all |
| \<weekday\> | tomorrow | all |
| JÄRGMINE NÄDAL | the day after tomorrow → end of the next ISO week | all |
| ÜLEJÄÄNUD KUU | from there → that month's end | 5 + «Näita veel N ▾» |
| KAUGEMAL | the day after → everything later | 5 + «Näita kõiki N ▾» |

> **Amended 2026-09-03 — each window is its own disclosure.** The *Shown* column
> above no longer describes the panel. A window is one `<details>`: its heading
> is the `<summary>`, the deadlines are the body, and it arrives shut. The two
> windows that sliced no longer slice — opening a window shows the whole window
> — so the nested «Näita veel N ▾» / «Näita kõiki N ▾» control is gone from
> *Eesolev* and `deadline_more.html` is deleted. The intervals, the partition
> and what is eligible to be in one are untouched; `UpcomingGroup.shown`,
> `preview` and `rest` remain in the read model and are no longer rendered.
>
> The right-hand control is what changed shape: «kõik N →», a link into the
> register, became «kõik N ▾», the summary of the window's own disclosure. See
> the amendment to §6 for what N now counts. The section's own «Kõik tähtajad →»
> is unchanged and still navigation.

Consecutive by construction and the last open-ended, so an eligible future real
deadline is in exactly one of them; asserted day by day over a year across six
awkward calendars. Where next week already runs past the month end, *Ülejäänud
kuu* is the empty interval it is and the panel omits it, and *Kaugemal* starts
after next week — which is what keeps the five touching.

*Kaugemal* is a real list now rather than the one summary line ADR 0046 left it
as. The horizon is every later real deadline, with no invented year-end cut; the
count beside the heading is the honest full population and the disclosure is
what bounds the rendering.

Only `wi.real_deadlines`. **A WAIT's expected date and a MONITOR's review date
are not deadlines**, are not red, and are in *Vajab sekkumist*, where they read
as "look at this again" (master specification 18.8). The merge is exactly the
kind of change that could quietly undo that, so it is asserted for both kinds in
all five windows.

### 6. Rows and Matters stay different numbers

*Vajab sekkumist* prints its **work rows** («41 rida») and links its **unique
Matters** («Ava kõik 33 teemat →»), because one file can be late and unowned at
once. Each *Eesolev* group prints «kõik N →» as unique Matters over a row list
that may be longer. This distinction is preserved through the new aggregator
rather than collapsed by a convenient `distinct()`.

> **Amended 2026-09-03 — a window counts what it opens.** The distinction stands
> and both numbers are still computed; which of them *Eesolev* prints changed
> with the control. «kõik N →» opened the register, which lists files, so it
> counted `matter_count`. «kõik N ▾» opens the deadline rows underneath it, so
> it counts `count`: a Matter carrying two dates in one window is two rows to
> reveal, and a control that opened two and said «kõik 1» would be describing a
> population it does not produce. `matter_count` is unchanged and still the
> honest answer for `UpcomingGroup.url`, which is what the partition is asserted
> through. *Vajab sekkumist* is untouched: it still links Matters, because it
> still navigates.

### 7. Tehtud carries the period and a row-kind filter, both in the URL

`?periood=` (7 päeva · 30 päeva · Kvartal · Aasta · a custom range) and `?liik=`
(Kõik · Arvamused · Töövõidud · Suletud teemad · Sissekanded), each preserving
the other. `?liik=` maps visible Estonian values onto the `DigestRow.kind`
vocabulary the rows already carry; the stored vocabulary was not renamed to make
a query string read well. An unrecognised value means all of them, never none.

**The filter narrows the rows and not the summary.** The four totals describe
the whole selected period, because that line answers what the period produced
and the filter answers "show me one kind of it".

There is no separate *Viimased muudatused* feed. Its question is what *Tehtud*
answers, from canonical records rather than from the audit stream.

### 8. One business fact, one definition

The team table's `ARVAMUSI VÄLJA · <aasta>` Kokku cell and Aruandlus's «Saadetud
arvamusi» are now the same population over the same window — opinions, grouped
by whose file they went out on, over the whole calendar year — and the equality
is a test rather than a hope. «Töövõite kinnitatud» counts
`WorkVictoryStatus.CONFIRMED` on the business `period_date`, which is what the
destination list filters on, and every Aruandlus row carries its year into its
link.

### 9. One navigation destination

`Osakond`, first on the bar, for everybody who could open Ülevaade. There is no
second `Osakond` inside «Veel», and no `Ülevaade` beside it.

### 10. Valdkonniti is a scope, not a page

`?vaade=valdkonniti` keeps the header, the strip and the body shell, swaps the
main column for the current `overview_areas.html` and the rail for the current
area-scope blocks. Both scopes are ordinary links, so a department view can be
pasted into a message. There is no third scope; `?vaade=tiim` still resolves to
the department (ADR 0039).

## Alternatives considered

**Keep two pages and de-duplicate the numbers.** It would have removed the
disagreements and left the question that caused them: a reader still has to know
which page a figure lives on, and the next figure added still has two plausible
homes.

**Make `/osakond/` department-head-only, as `/osakonna-too/` was.** It would
have taken the department view away from every specialist, reader and
shared-gate session that had it — a real loss of access dressed as a merge.

**Show *Meeskond* and *Tehtud* to everybody.** The team table is a per-person
view of colleagues' work and the digest is a period report; both were behind the
role for reasons that have not changed, and widening them is a separate decision
with its own consequences.

**Give Valdkonniti its own route.** It is the same question at a different
scope, shares the shell and the strip, and a second route would mean a second
place to keep the shell in step.

## Consequences

- One page, one route, one place to add the next department figure.
- Two permanent redirects to maintain, and two route names that outlive the
  pages they were named for. Both are cheap and both keep somebody's bookmark
  working.
- `app/matters/department.py` is a new module, and deliberately a thin one: it
  composes `department_dashboard` and `overview` and owns no definition of its
  own. No model, no migration, no cache, no second work-item or reporting
  system.
- Two numbers changed value because they were wrong: the year opinion total now
  counts opinions rather than the files carrying them, and confirmed work
  victories are no longer structurally zero.
- Three counts stop being printed. ADR 0039 moved *Sissekandeid sel nädalal*,
  *Saadetud arvamusi \<kuu\>* and *Tähtaegu sel nädalal* into Aruandlus when it
  retired Minu tiim; the approved rail is the three year figures and nothing
  else, so they are calculated and no longer shown. Their selectors and their
  tests are untouched, which is what makes putting one back a one-line change.
- The `ChangeEvent` activity feed is on no page. Its question is *Tehtud*'s, and
  the two or three events it carried that a canonical record does not — a stage
  change, an assignment, an added Kaasamine — are recorded as an open item
  rather than given a fifth content system on this page (DS-25).
- `overview.build_overview`'s department branch is no longer routed. It is left
  in place rather than removed in this change: it is one branch of a function
  whose other branch serves the area scope, and unpicking it is a cleanup with
  its own risk and its own review. `templates/matters/overview.html`,
  `templates/matters/department_work.html` and
  `templates/matters/partials/overview_department.html` are deleted, because
  nothing renders them.
- Visual-regression scenarios that were named for Ülevaade or Osakonna töö are
  renamed to the surface that replaced them rather than deleted, so the
  historical coverage is continuous.

## Reversibility

High, and cheaply. The two old views were deleted rather than the read model
behind them: `department_dashboard` and `overview` still own every definition,
so restoring a second page means restoring a view and a template over functions
that never moved. No data changed, no schema changed and no migration exists to
undo.

## What this does not change

- Authorization. `visible_to`, `scope_for_user` and `is_department_head` are
  untouched; no role gained or lost an entitlement.
- The shared work model. `app/matters/work_items.py` was not edited: the page
  consumes `real_deadlines()` generically, so a new deadline source added there
  appears in *Eesolev* without an Osakond-specific change.
- The root. `/` still lands where it landed; where it should land is the
  separate decision the parallel branch owns.
- Any stored vocabulary. `DigestRow.kind`, `WorkVictoryStatus` and the register
  parameters are read, never renamed.
