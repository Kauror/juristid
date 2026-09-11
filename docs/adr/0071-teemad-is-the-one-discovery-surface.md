# 0071 — Teemad is the discovery surface, and «Tähtajad» is not a destination

**Status:** accepted
**Date:** 2026-09-10
**Supersedes** the *«one navigation item and three tabs»* grouping of Stage-2G
brief 38, and with it the three generated department-wide reading pages
introduced by ADR 0018 as product destinations. ADR 0018's decision that these
are *structured facts on a Matter* — models rather than tags, with dates, status
transitions, provenance and their own audit trail — is untouched and is the
reason this change is possible at all.
**Related:** ADR 0033 (`?too=` makes the dated-work populations addressable),
ADR 0047 (Arvamused as a section of Teemad), ADR 0049 (one department page),
ADR 0063 (one Organisation catalogue behind Saatja and Adressaat),
ADR 0065 (adding a structured fact happens on the Matter).

## Context

The bar carried a fifth item between Teemad and Statistika. It was called
«Jälgimine», then «Tähtajad», and it opened three generated pages under
`/jalgimine/`:

| Page | What it listed |
| --- | --- |
| Olulised tähtajad | every `MatterImportantDate` in the department, on a calendar unioned with commencements |
| Jõustuvad aktid | every active `MatterEffectiveDate`, on a twelve-month horizon |
| Töövõidud | every confirmed `MatterWorkVictory`, a year at a time |

Each was generated from the Matter's own records, which was the good half of
Stage 2G: nobody maintained a second list, so nobody could forget to update one.

What was wrong was not the generation. It was that **each page is a list of
Matters selected by one property, presented as a place to be.** Four
destinations, and a reader looking for a file had to know in advance which of
them their memory of it belonged to. Worse, the four could not be intersected:
there was no way to ask

> avatud teemad, millel on töövõit

because *avatud* is a register question and *töövõit* was a different page. The
register could express fifteen dimensions and not these two; the fact pages
could express these two and no dimension of the register. Every real question
crosses that line.

*Olulised tähtajad* had a second problem of its own. A deadline the department
is watching belongs to somebody — the person who owns the file — and the read
model has said so since Stage 2F: an active `MatterImportantDate` reaches its
Matter owner's Minu asjad through `work_items`, follows the Matter on a
reassignment, and reaches nobody at all while the Matter is unassigned. So the
department-wide page was a second answer to a question the personal queue was
already answering, for a reader it was not addressed to.

## Decision

**Teemad is the one place a Teema is found.** The separate top-level
destination goes, the three pages go with it, and the two facts that are
genuinely *about* a Matter become dimensions that narrow the register:

* `?toovoit=` — `on`, `puudub`, or a year. A file carries a work victory when
  it has at least one this reader may see, with *this reader may see* meaning
  `MatterWorkVictory.objects.visible_to(user)` and *is a work victory* meaning
  `VISIBLE_VICTORY_STATUS`, imported rather than restated. The year is the
  business period, which is the column the retired page's `?aasta=` read.
* `?joustumine=` — `on` or `puudub`, over the **active** `MatterEffectiveDate`
  records and over nothing else: never inferred from the title, the
  Menetlusliik, the Hetkeseis or a tag.
* `?joustub_alates=` / `?joustub_kuni=` — a closed window on the same records.

**An `Oluline tähtaeg` is personal upcoming work and has no department-wide
destination.** No code was needed for this; what was needed was proof, and
`tests/test_important_dates_are_personal_work.py` asserts it on `/minu-asjad/`
itself rather than through the read model.

**Nothing about the facts changed.** Same models, same rows, same history, same
provenance, same audit events, same write surfaces on the Matter page. No
migration of any kind — not schema, not data, not the search index.

### The window is containment, not overlap

This is the only genuinely difficult decision in the change.

A commencement recorded as *III kvartal 2026* is a claim about a quarter. Asked
for `1.7.2026 – 31.8.2026`, two answers are available and both are defensible:
*overlap* (the period touches the window, so it might commence inside it) and
*containment* (the whole period lies inside the window, so it certainly does).

Containment, because the product has already decided this once. `_direction_q`
in `app/intelligence/selectors.py` decides *möödunud* by `period_end` — II
poolaasta 2027 has not passed on 2 July 2027 — and the window is that rule with
both ends named instead of one. Overlap would let a filter labelled "commences
by 31 August" return a record that may well commence in September, which is the
false precision the precision vocabulary exists to prevent.

`GENERAL_ORDER` and `UNKNOWN` carry no date at all, by database constraint, so
neither bound can ever match one. They are found by `?joustumine=on`, which is
the honest place for a commencement whose date nobody knows.

### The old addresses, and what is lost

Every retired address still resolves, in one hop:

| Address | Now |
| --- | --- |
| `/jalgimine/toovoidud/`, `/toovoidud/` | `/teemad/?toovoit=on` |
| `/jalgimine/joustumised/`, `/joustuvad-aktid/` | `/teemad/?joustumine=on` |
| `/jalgimine/tahtajad/`, `/olulised-tahtajad/` | `/minu-asjad/`, or `/teemad/` with no persona |

The two legacy paths point at the new destination directly rather than at the
`/jalgimine/` route, so a bookmark costs one hop and there is no chain to reason
about. `302` rather than the previous `301`: these destinations are a product
decision one month old, and pinning them into every browser's cache is not a
cost worth paying.

**The old query strings are dropped rather than translated.** `?suund=moodunud`,
`?allikad=joustumised`, `?aasta=2024` and `?staatus=` described windows over a
list of *facts*; the register lists *Matters*, and no narrowing of it means the
same thing. Carrying them across would leave words in the address that nothing
reads — and the register's search box would then carry them forward as hidden
inputs, so a reader would share links containing a filter that was never
applied. The population is preserved; the window is not.

### Two Statistika figures now count Matters

«töövõite» and «jõustunud akte» on the Aruandlus rail, and «N töövõitu» on the
overview strip, counted *facts* and linked to the pages that listed them. Those
lists are gone, so the only list either number can open is a list of Matters —
and this module's first rule is that a number opens the list it counted. So they
are counted through `register_population` in the register's own parameters and
captioned for what they now count: **teemat töövõiduga**, **teemat jõustunud
aktiga**. The numbers differ from the old ones exactly when one file won twice
or commenced in stages.

### The Organisation pool, reported alongside

Reported separately and fixed here because it is the same surface: an
institution added through Uus teema's typed Saatja or Adressaat was "not
reliably available" in Täpsem otsing.

It was never a second catalogue — every control has always read
`Organisation.objects`, exactly as ADR 0063 requires. It was the **first page**.
`_organisation_options("")` returns the first twenty bodies alphabetically, and
the panel populated all three institution controls from that one truncated list
while only `Asutus` had a search box. So `Saatja` and `Adressaat` could not
reach a body outside those twenty at all, and an applied `?saatja=` outside them
was not redisplayed in its own control — the next submit silently dropped a
filter the chip above still claimed was applied. "Reliably available" was
literally true: reliable exactly when the body happened to sort in the first
twenty, which a newly typed one usually does not.

All three are now the same searchable partial over one catalogue, built by one
function so the panel and the HTMX fragment cannot drift, and the typed term is
read from the parameters so the control works without scripting too.

Two adjacent defects surfaced while reproducing it, both fixed: `Asutus` offered
*Määramata* and the register answered it with an empty list, because `puudub`
reached `uuid.UUID()` and was refused as unreadable; and the control renamed
itself from «Asutus (saatja või adressaat)» to «Asutus» the first time somebody
typed into it, because the panel and the fragment held two spellings of one
legend.

## Consequences

* One register, fifteen-plus dimensions, all composable, all in the URL, all
  represented as removable chips and all cleared by `Tühjenda kõik`.
* Both new filters are one correlated `EXISTS` over a child table scoped to the
  reader *before* the existence test contributes anything, so a restricted child
  can make its Matter neither appear under `on` nor disappear under `puudub`.
  The #160/#164 restricted-child hardening is not weakened; it is the mechanism
  being reused.
* A Matter appears once however many facts it carries — `EXISTS` rather than a
  join, so no `.distinct()` is needed and no row is duplicated.
* Cost does not grow with the register: bounded queries, no Python loop, and no
  index added. The two child tables already carry `("matter", "status")` indexes
  from Stage 2G, which is exactly the shape these subqueries probe.
* `app/intelligence/sections.py` and `filters.py` were page-presentation helpers
  with no other consumer and are deleted with the pages. The **selectors** are
  kept: `matter_intelligence` serves the Matter page and `VISIBLE_VICTORY_STATUS`
  and `work_victory_years` serve the register. The calendar-union reads that only
  those three pages called are now unused, keep their own tests, and are left for
  a separate cleanup rather than deleted inside a product change.

## What was considered and refused

**A «Töövõidud» tab under Teemad.** It would have kept the parallel list and
moved it one level in — the same four destinations with a nicer address. A
property of a Matter narrows the Matter register.

**Free-text search over the Töövõit wording.** `MatterWorkVictory.title` and
`detail` are not in the search projection and never have been; adding them means
a new row kind, an `INDEX_VERSION` bump and a corpus-wide rebuild. Reported
rather than done, because the capability this change needs is the structured
filter and a rebuild is not something to acquire as a side effect.

**Putting `Jõustumine` into Minu asjad.** A commencement is a fact about the
legal instrument, not somebody's deadline, not a `NextAction` and never overdue.
Only its discovery surface moved.

**Deleting the important-date records with the page.** Retiring a reading
surface is a decision about where information is consumed. Not one row was
touched.

---

## Amendment, 2026-09-11 — the column headings are controls

**Status:** accepted

Teemad is where a Teema is found, and the table is what a reader actually looks
at. Five of its columns name working dimensions — Hetkeseis, Vastutaja,
Järgmiseks, Kuupäev, Viimane tegevus — and every one of them was passive. Acting
on what you are looking at meant opening Täpsem otsing, finding the matching
control, choosing the same words the row in front of you already says, and
submitting a form.

So the headings are now controls. No second toolbar, no second filter system:
this decision is about which element writes the parameters this ADR already
defines.

### The two behaviours, and why they are not one

| Column | Heading does |
| --- | --- |
| Hetkeseis | filters — `?hetkeseis=` |
| Vastutaja | filters — `?vastutaja=` |
| Järgmiseks | filters — `?tegevus=` |
| Kuupäev | sorts — `?jarjestus=kuupaev_asc` / `kuupaev_desc` |
| Viimane tegevus | sorts — `?jarjestus=viimane_uusim` / `viimane_vanim` |

Generic ascending/descending on every heading would have been less code and the
wrong product. Sorting *Marko / Ireen / Sandra* alphabetically answers nothing
anybody asks of the Vastutaja column; sorting *vaata üle / helistan / ootan
vastust* alphabetically is not a question at all. What those three columns are
for is **show me only these**, so that is what their headings do — and
Järgmiseks filters by the work states `?tegevus=` already understands
(`Puudub`, `Tähtaeg möödas`, `Ülevaatus käes`), never by the letters of one
file's sentence. Free text is what the search box above the table is for.

### The URL is still the whole state

A heading writes the same parameter Täpsem otsing writes, produces the same
removable chip, and reads its own active state back out of the address.
Every menu is populated from the same context list the panel's own select is
populated from — `stages`, `owners` (`owner_filter_choices`, ADR 0036),
`next_action_options` — so "the heading and the panel agree" is not a property
to be maintained but the absence of a second thing to maintain.

Nothing is stored. No session, no `localStorage`, no preferences row, no
migration: a narrowed, sorted register is a link somebody can paste, and that
was already true before the headings could be clicked.

Everything not named survives a heading's activation — `q`, every other filter,
the sort. Only `leht` resets, because a different population starts at its first
page.

### Kuupäev sorts the date the row displays

This is the load-bearing half. The cell has always chosen between the open
`Järgmiseks` step's own date and the Matter's `Arvamuse tähtaeg`, and an
ordering built from either column alone would produce a table reading 12.09,
15.09, 13.09 under a heading insisting it was sorted by date.

So the choice is one rule, `app/matters/register_dates.py`, read two ways: a
Python reader the row renders from, and a `COALESCE` the database orders on.
This is the shape `app/matters/activity.py` already uses for *Viimane tegevus*,
and `?jarjestus=viimane_*` orders on that module's own SQL twin of
`activity_of` — never on `Matter.updated_at`, which for most of this register is
the moment the 2026 cutover touched the row (ADR 0026).

**A restricted step contributes to neither reading.** The prefetch and the
subquery are both `NextAction.objects.visible_to(user)`, so an action this
reader may not open moves neither the date on the row nor the row up the page.
Authorization happens before the sort key exists, not after the text is hidden
(AUTH-003).

**A missing date is last in both directions.** PostgreSQL's own default is NULLS
LAST ascending and NULLS FIRST descending, so «hiliseim enne» would have opened
on a page of em dashes above the rows somebody clicked the heading to see.

### What does not change

* **The default order.** Newest reference first, exactly as before. A column
  becoming clickable is not a decision about what the register opens on.
* **Old sort addresses.** `?jarjestus=reference|title|updated|deadline` keep
  their values *and their meanings*. `deadline` still orders on
  `Matter.response_deadline`, which is deliberately **not** the Kuupäev column;
  it is relabelled «Arvamuse tähtaeg» in the Järjestus control so two sorts on
  one page do not both read as «Tähtaeg».
* **Every other surface's table.** `matters/partials/matter_table.html` is
  shared with Saabunud, which writes no register parameters, so the interactive
  head is a partial of its own included on an explicit flag. A filtering control
  on a page whose address nothing reads would be a control that looks like it
  works and changes nothing.

### Consequences

* Three filter menus are `<details>`/`<summary>` with plain links inside:
  keyboard-operable, Escape-closable and functional with scripting off, which is
  the same pattern the row's own `Määra` menu uses (buttons there, because that
  one writes).
* `aria-sort` on the cell is the single statement of which way a column is
  sorted — the stylesheet draws its arrow from it, so the two cannot disagree.
  It describes the *column*: «uusim enne» is a descending date column even
  though it is the heading's first activation.
* The full register page costs the same number of queries as before. The live
  search fragment costs two more (17 → 19), because the three menus are inside
  the region a keystroke replaces and must still be there afterwards; both are
  bounded reads — a nine-row vocabulary and one union over owners — and neither
  grows with the register.
* The two derived sort keys are annotated only when they are ordered on, so an
  unsorted page pays for neither.

### What was considered and refused

**A toolbar above the table.** The dimensions are the columns; a second row of
controls restating them is the thing this change removes work from.

**Sorting in Python after pagination.** It would have ordered the twelve rows
that happened to land on the current page and called the result a sorted
register.

**Making the values in Järgmiseks clickable filters.** The sentence is one
file's wording, not a category.
