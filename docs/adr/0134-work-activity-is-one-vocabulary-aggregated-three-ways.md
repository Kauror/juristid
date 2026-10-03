# 0134 — Work activity is one vocabulary, aggregated three ways

**Status:** accepted
**Date:** 2026-10-03

**One definition of «somebody worked on this file», owned by
`app/matters/activity.py`.** Three surfaces read it and aggregate it
differently. Before this record two of them disagreed about what work *is*: the
register and the quiet rail read `activity.py`, and Osakond's «Teemades
muudatusi · eelmine nädal» read `Teema käik`'s event list
(`department_dashboard._ACTIVITY_EVENT_TYPES`). One Matter could be «changed
last week» in the team table and «Muutusteta 30 p» in the rail beside it
(RULE-02). The owner chose one vocabulary (Option A). **No migrations**, no new
model, field or index.

1. **One vocabulary, three aggregations.**
   * «Viimane tegevus» — the *latest* qualifying activity.
   * «Muutusteta 30 p» — the *age* of that latest activity.
   * Osakond «Teemades muudatusi · eelmine nädal» — Matters with *at least one*
     qualifying activity in the window (`activity.work_activity_between`).
     Not «its latest activity falls in the window»: a file worked on last week
     and again today was worked on last week.
   What is shared is the list of sources, their dates and their reader scope —
   not one SQL expression.
2. **What counts**, each on its own business date: a recorded closure; a sent
   `Submission`; an authored `Entry`; a `NextAction` a person set or ended; a
   dated `Kaasamine`; a `+ Märge` recording something done
   (`MatterProceduralDevelopment.occurred_on`); a published `Ülevaade`
   (`MatterWebsiteOverview.published_on`); a dated `Väline seisukoht`
   (`MatterExternalPosition.stated_on`, its own basis, not a Koja letter); the
   Matter's received date; an imported Matter's OneNote page dates. The
   native-record `updated_at` fallback stays where 0026 put it — something true
   to print in «Viimane tegevus» for a Matter with nothing else — and decides
   no window.
3. **Its own business date, never a technical one.** `created_at`,
   `updated_at` and import or observation timestamps never stand in for an
   unknown date. A record whose date nobody wrote down — an undated
   `Kaasamine`, `Märge` or `Väline seisukoht`, a published `Ülevaade` without
   `published_on` — stays on the file and in `Teema käik` and moves no activity
   date. A planned or cancelled `Ülevaade` is not work.
4. **A `Märge` dated ahead is a plan, and stays one.** 0124 §2 decides on the
   day it is saved: past or today is a record of something done; a day ahead is
   information or a plan. The vocabulary asks the same question from stored
   facts — the period's anchor not after the local day of `created_at` — so the
   calendar reaching that day later does not turn the plan into work. A
   correction does not reclassify it (0124 §4).
5. **A period is not a day.** An approximate `Kaasamine`, `Märge` or `Väline
   seisukoht` is ordered on its anchor for «Viimane tegevus» and «Muutusteta
   30 p», printed at its precision (0082), and counts for no bounded window:
   a week never holds a month (`dates.period_in_window`, 0122 §2).
6. **Administrative and machine writes are not work.** A title or metadata
   edit, an owner or collaborator change, a standalone `Hetkeseis` transition,
   a standalone document upload, import and search-index writes,
   `CurrentRegisterState` observations, enrichment, and a `NextAction` an
   importer set. Several of these are history and stay in `Teema käik`.
7. **Scoped to the reader, and removed records do not exist.** Every source is
   read through its own model's `visible_to` (child visibility, `removed_at`
   null — 0102), so a record the reader may not see moves no date, sort
   position, quiet state or count.
8. **Four other questions keep their own answers.** «Viimati muudetud»
   (`Matter.updated_at`, record mutation); `Teema käik`
   (`TIMELINE_EVENT_TYPES`, what belongs in the history); the audit log
   (`ChangeEvent`, every write); Statistika «Tegevus» (workload in a reporting
   window). None of them is a source of work activity, and the work vocabulary
   is not borrowed by any of them.

## Why

A department head reads the rail and the table side by side. Two definitions
of the same word produce a contradiction no explanation repairs: the table
counted a `Hetkeseis` click, an upload and an importer's step, and missed a
dated `Kaasamine` and a `+ Märge`. Fixing the event list would have kept two
lists that drift again with the next record type; moving the definition into
one module makes the next type one decision in one place.

## Rejected

**Count Matters whose latest activity falls in the window.** Rejected by the
owner: it would drop a file from last week's count because somebody worked on
it again today.

**Keep the event-log definition and add the missing event types.** Rejected:
an event's `occurred_at` is when the row was written, not when the work
happened — a backdated `Märge` or a `Kaasamine` entered a week late lands in
the wrong week, and an event survives its record's removal.

**A generic activity table or materialised view.** Rejected: a second copy of
facts every source already stores, with its own freshness problem. One
`EXISTS` per source over the existing tables answers the window.

## Consequences

The Osakond column's numbers change: it now counts what the rail counts.
Stage-only, upload-only and import-only weeks no longer count; a dated
`Kaasamine`, a `+ Märge`, a published `Ülevaade` and a `Väline seisukoht` now
do, on their own dates. «Viimane tegevus» gains three bases — `Märge`,
`Ülevaade avaldatud`, `Väline seisukoht` — and so can move earlier or later on
Matters that carry them.

Three more sources cost three more correlated subqueries wherever the latest
activity is computed for every row — the activity sort and «Muutusteta 30 p».
No query is added to any page. Two guards keep the cost to the rows that can
answer: a published `Ülevaade`'s date and a source's «precision of the latest»
are looked up only for Matters that have such a row. Measured on the
5,016-Matter clone: the Osakond window query itself is faster than the
event-log one it replaces; the activity sort and the quiet list cost a reader
with child-visibility rules roughly 100 ms more, and a department head about
20 ms.

## Reversibility

Code only. Reverting restores the event-list window and the previous sources;
no data was written.
