# ADR 0039 — Retiring `Minu tiim`, and the three counts that outlived it

- Status: accepted; the surface names are **superseded by ADR 0049** (Ülevaade
  and Osakonna töö are one page at `/osakond/`). The decision itself stands:
  there is no team scope, `?vaade=tiim` still resolves to the department, and
  the three counts that outlived it are still counted.
- Date: 2026-08-27
- Stage: pre-QA (shared-gate development phase)
- Related: ADR 0033 (Ülevaade drill-down parity, which specified the
  `N inimest` figure this removes), ADR 0034 (why `Osakonna töö` was *not*
  renamed to `Minu tiim`), ADR 0036 (who work may be assigned to), ADR 0038
  (child visibility in projections)

## Context

Ülevaade shipped with three scopes behind one shell: `Kogu osakond`,
`Minu tiim` and `Valdkonniti`. Two of them answered the same question.

`Minu tiim` grouped the department by person and printed each colleague's
current week as rows. It never had a population of its own. This product has no
team-membership model — no reporting line, no team table, nothing that records
who is in whose team — so the view could not mean what its name said, and the
page had to admit as much in a footnote of its own:

> Tiimi koosseisu ei ole süsteemis eraldi kirjas — siin on kõik teemasid kandvad
> kolleegid, keda sul on õigus näha.

That is the same population `Kogu osakond` already shows in its `Koormus` rail:
every colleague carrying files, at the reader's own authorization. ADR 0034
refused to rename `Osakonna töö` to `Minu tiim` for the neighbouring reason —
the access rule is what the name has to describe. A second scope built on the
first one's population, distinguished only by grouping, is a fork of one
overview into two surfaces that must then be kept in agreement forever.

Three of its numbers were worth keeping: `Sissekandeid sel nädalal`,
`Esitatud arvamusi <kuu>` and `Tähtaegu sel nädalal`. They are not team numbers
and never were — each one counts the whole department at the reader's own
authorization — and they answer at week and month range the question
`Aruandlus` already answers at year range.

## Decision

**`Minu tiim` is retired.** `Kogu osakond` and `Valdkonniti` remain. There is
one operational overview population.

**The three counts move into `Aruandlus`, as rows of that block.** Not a
preserved fragment: they are `CountRow`s in `page.reporting`, rendered by the
same `railrow` markup as the year rows, under the same heading, with no
sub-heading between them. Each row states its own period in its own label —
`sel nädalal`, `augustis`, `2026` — so a week row and a year row can share one
block without one inheriting the other's range. The week and month rows lead,
because they narrow towards the reader's own week.

**The month stays derived and stays Estonian.** The month itself comes from the
date; only its inessive spelling is the table `ESTONIAN_MONTHS_IN`, for the
reason that table already gives — *augusts* and *septemberis* are what a rule
guessed from three examples produces.

**`?vaade=tiim` normalizes to `Kogu osakond`.** No redirect, no hidden mode:
`scope_from` already falls back for any value it does not recognise, and `tiim`
is now simply one of those. An old bookmark opens the surviving overview.

**Everything else built for the view is deleted**, not hidden: the scope
constant, the `Minu tiim` tab, `overview_team.html`, the `Tiimi tähtajad` and
`Tiimi tegevus` rail blocks, the `team_activity` and `is_team` members of
`Overview`, the `N inimest` figure and its `#inimesed` landing point, the
`with_week` / `items` / `later` / `week` members of `PersonLoad` that only its
rows consumed, and the `.personblock` and `.teamrow` CSS.

## Authorization

Nothing here widens a population, and the move is only safe *because* it does
not (ADR 0038).

Each of the three counts is resolved by a scoped selector, once, before any
arithmetic:

| Row | Source | Authorization |
| --- | --- | --- |
| `Sissekandeid sel nädalal` | `Entry` | `Entry.objects.visible_to(user)` → `child_visibility_q` |
| `Esitatud arvamusi <kuu>` | `Submission` | `Populations.submissions`, i.e. `Submission.objects.visible_to(user)` |
| `Tähtaegu sel nädalal` | the shared work read model | `work_items(user)` → `NextAction.objects.visible_to` + `MatterImportantDate.objects.visible_to` |

All three were already department-wide at the reader's own scope on
`Minu tiim`: none of them filtered by `responsible`, and the retired view's
per-person grouping happened after the population was resolved. So the
population before and the population after are the same queryset, resolved by
the same call, and a restricted child a reader may not see is absent from the
count on both sides of this change.

Two of the three reuse the `Populations` the page already built and cost
nothing: the month's opinions narrow the `Submission` population the `Seis`
strip had already resolved, and are handed that strip's own count rather than
counting it again; the week's deadlines are counted in Python off the work
items the page had already read. `Sissekandeid sel nädalal` is the exception
and costs two — the break-glass lookup every `visible_to` performs, because
nothing else on this page resolves the `Entry` population, and the aggregate
itself. So the department page went from 42 queries to 44, and `Valdkonniti`
from 21 to 21: `Populations.entries()` is a method rather than a field
precisely so a scope that never renders `Aruandlus`'s week row pays for
neither query.

Nothing here is per-row. The cost is flat in the number of Matters on the
page — measured at 3, 18 and 30 — which is the property
`tests/test_overview_simplification.py` guards, rather than the absolute
ceiling `tests/test_multiple_senders.py` keeps.

## Consequences

`Esitatud arvamusi <kuu>` is now on the department page twice: as the `Seis`
strip's headline `N esitatud arvamust <kuu>` and as an `Aruandlus` row. That is
deliberate and accepted rather than overlooked — the strip is the headline and
the rail is where a number is looked up beside the year it belongs to. Both are
counted from one population with one filter, so they cannot drift. Removing
either would be a redesign of `Kogu osakond`, which this decision is not.

One boundary was closed in the move. `Sissekandeid sel nädalal` filtered
`occurred_at__date__gte=` Monday with no upper bound, so an entry a colleague
dated into next month counted towards *this week*. It is now bounded at both
ends of the ISO week. The two windows on the block stay deliberately different:
entries are work already written up, so the week runs Monday→Sunday; deadlines
are work still ahead, so `Tähtaegu sel nädalal` runs today→Sunday through
`work_items.week_items`, the same helper and the same window `Minu töö` calls
this week.

ADR 0033's `N inimest` drill-down no longer exists. Its rule — a figure that
counts something the register does not list opens the list of exactly those, on
this page — is unchanged and still enforced for `valdkonda vastutajata`.

No schema migration. No business-data migration. Nothing stored changes.
