# 0074 — The Teema detail page is the approved target

**Status:** accepted
**Date:** 2026-09-11

**Supersedes the presentation clauses** of ADR 0027 (engagement vocabulary
constraints), ADR 0031 (`Kaasamine` is not in the composer), ADR 0043 (the
composer is collapsed by default), ADR 0065 (structured facts are added from a
standing panel on the Matter) and the 2026-09 Matter-page refinement
(PR #161). Every domain decision in those ADRs that is not a statement about
this page stands unchanged, and each is named below with what replaced it.

## Context

The 2026-09 refinement rebuilt this page against a specification that had by then
been superseded. The result was a coherent page — three zones, one left edge, one
label style — that was not the approved design. This ADR records the approved
design and the decisions taken to implement it.

The authority for this round is a design handoff, and it is unusually complete:

| File | Role |
|---|---|
| `TEEMA_TARGET.html` | The approved prototype, exported verbatim. Authoritative for DOM, copy, classes and states. |
| `TEEMA_TARGET_SPEC.md` | Authoritative for intent and behaviour. |
| `TEEMA_TARGET_{1440,1024,420}.png` | Visual verification of the same HTML. |

Where that handoff and this repository disagreed about **presentation**, the
handoff won. Where they disagreed about **domain, authorization or evidence**,
the repository won and the handoff was implemented within it. Two clauses of the
handoff were themselves overruled, and both are recorded below.

## Decision

### 1. Three zones, and nothing between them

    MATTER HEADER
    ├── JÄRGMISEKS          always visible
    ├── COMPOSER            open by default
    ├── AJAJOON
    │   ├── process strip   horizontal, derived
    │   └── chronology      two row kinds
    └── RAIL                four blocks

There is no standing facts panel between the composer and `Ajajoon`, no
standalone `Kaasamine` section, no `Jõustumine` section, no `Töövõit` section and
no `Olulised tähtajad` section. Those facts are entered through the composer and
read through the strip and the chronology.

### 2. `Saabus` is header metadata; `Tähtaeg` means `Arvamuse tähtaeg`

`Saabus` moves from the `Teema andmed` rail into the metaline at position 4,
immediately before `Tähtaeg`. Arrival and response deadline are read as a pair:
together they say how much time is left. Moved, not rebuilt — the same
`update_field` endpoint, the same field name, the same audit row.

The header's `Tähtaeg` is `Matter.response_deadline` and nothing else.
`selectors.active_deadline` — nearest future dated fact, else nearest past, over
the response deadline *and* every watched `MatterImportantDate` — still answers
the broader question for the work lists that want it, and is the wrong answer
here: beside `Saabus`, under an editor headed `Arvamuse tähtaeg`, a Riigikogu
reading in three weeks reads as a day Koda owes an opinion on.
`selectors.response_deadline_of` is the new, narrower reader.

### 3. The composer is open by default

`<details class="composer uxcomp" open>` on every initial GET for a writable
active Matter — not only after a refusal, not only after clicking the row.

This reverses ADR 0043. That decision was reasonable on its own terms: a Matter
is read far more often than written to, and an eight-line form standing open
pushed the file's content below the fold. What the target does instead is make
the form short enough to live open — a 60 px body, a one-row next step, and
everything else a chip. Recording what happened is the reason this product
exists, and it must not begin with a click.

Authorization and Matter state still decide whether a composer is rendered at
all. A reader gets none; a closed Matter gets none.

### 4. `MatterEngagement.title` answers `Keda kaasati`

One column, one meaning — the human-readable line that identifies this
engagement — and the question printed above it is the surface's to choose. A
title recorded through the old five-field form said what the outreach was; an
answer recorded through `+ Kaasamine` says who it reached. Both are that line.

A second `audience` column would have left every historical row with an empty new
field and every new row with an empty old one, and the chronology would then have
to choose which of the two to print.

### 5. `Vastuseid` is a real nullable column

`MatterEngagement.response_count`, `PositiveIntegerField(null=True)`. Faking it
with note text, title text or browser-only state would have been a number that
disappears on the next render.

**NULL, never 0, and no backfill.** «Nobody answered» and «nobody counted» are
different facts about a consultation, and a column that cannot tell them apart
reports the second as the first.

Nothing is derived from it: no response *rate*, no contacted count. This model is
a pointer to outreach that happened elsewhere, and a percentage computed from one
of its two halves would be a statistic about a denominator nobody stored.

### 6. The composer stops classifying what it takes

`Roll`, `Sissekande liik`, `Asutus` and `Toimus` are gone from the form, not
hidden on it — the same rule the retired next-step precision group follows, so a
crafted POST cannot reach a control the page does not have. A file dropped on the
composer is ordinary evidence on an ordinary work entry, recorded now.
`compose_update` still takes every one of those as a parameter and the archive
importer still supplies them.

`+ Manus` is gone as a disclosure. The file affordance is always visible at the
right end of the `Millal?` row.

### 7. `+ Jõustumine` is a composer panel

A composer door onto the existing `MatterEffectiveDate`, through
`add_effective_date`. Not an `Entry` pretending to be a commencement, and not a
second commencement model. Stored at `EXACT` precision because the panel asks for
a day; the approximate and general-order kinds the domain also carries are
unchanged on the rows that hold them.

The chip is rendered unconditionally. It used to appear only on a Matter that had
no commencement yet, because the second one was added from the standing facts
section — and that section is gone.

### 8. `+ Töövõit` is a composer panel, and does not require closing the file

Through `add_confirmed_work_victory`, the manual door that already existed. A win
is recorded when it happens, which is usually while the file is still open. The
record is created without a `period_date`: that column is a *reporting* period,
and borrowing today's date for it because the panel happened to be open would
file a win into a reporting year nobody chose.

### 9. `+ Kaasamine` is a composer panel — and `MEETING` is a real kind

This reverses ADR 0031's «one entry point, and it is the standalone section».
That decision was sound while the section existed: two controls for one act is
how one consultation gets recorded twice. The section is gone, so this is the one
entry point rather than the second one.

`EngagementKind.MEETING` is added, reversing the constraint ADR 0027 recorded.
The old reasoning — a meeting is authored chronology and `Entry` already records
it, so a second home would guarantee two records that disagree — held while
`Kaasamine` was filled in separately from the composer. The target folds both
into one save: one `Salvesta` writes the note *and* whichever engagement was
chosen, in one transaction.

The composer offers three kinds — `Küsitlus`, `Koosolek`, `Kirjade voor`.
`WEB_CALL` and `OTHER` remain valid stored values with no chip. A vocabulary the
write surface has stopped offering is not a vocabulary the database has stopped
accepting.

The engagement's date is today, in Europe/Tallinn. The target deliberately does
not ask for one, and the application's convention for «this happened as part of
the work I am writing down now» is the clock `add_entry` stamps with.

### 10. Closing a Matter is not a claim that an opinion was sent

`+ Lõpeta teema` asks two questions: `Kuidas lõppes` and an optional `Lõppsõna`.

The section asked six. The four that went are not a simplification but a
correction of what closing *means*. Koda closes files it never wrote to anybody
about; it sends opinions on files that stay open for another year. Requiring the
sent PDF in order to finish a file made the commonest closure impossible to
record honestly, and made the rarer one — closing *because* the opinion went out
— look like the only shape a closure has.

**No canonical rule moved.** A `SENT` Submission still needs its exact final
evidence and still goes through `mark_submission_sent`. A `Töövõit` is still a
confirmed `MatterWorkVictory`, now recordable without closing anything. A
commencement is still a `MatterEffectiveDate`, now recordable the same way.
`_apply_closure` still accepts `final_opinion`, `work_victory` and
`effective_date`; the composer form no longer sends them, and
`tests/test_teema_closing_flow.py` proves every one of those invariants through
the service instead.

`Jõustus` / `Menetlus lõppes` / `Loobuti` are three chips over the existing
`Disposition` vocabulary — `COMPLETED`, `INITIATIVE_WITHDRAWN`,
`MONITORING_STOPPED`. Nothing was added to it and nothing retired from it.

### 11. `Täpsus` is three chips over one date box

`Täpne päev` / `Kuu` / `Kvartal`, and `_period_anchor` derives the year, month,
quarter and half from the day that was picked. `bounds_for` still normalises, so
a quarter entered here and a quarter entered on `Olulised tähtajad` produce the
same stored anchor. `HALF_YEAR` and `YEAR` remain stored precisions that read
correctly on the records carrying them; they are not worth a chip on a panel
whose point is that it fits on one row.

### 12. The process strip is derived, never stored

`app/matters/process_timeline.py`. There is no `ProcessTimeline` table and no
`Milestone` model: every column is read off a canonical record that exists for
its own reasons — the Matter's creation, a `MatterEngagement`, a sent
`Submission`, the current `StageVocabulary`, a `MatterImportantDate`, a
`MatterEffectiveDate`.

Only milestones that exist are drawn. `Loodud · Küsitlus · Arvamus välja ·
Valitsuses · I lugemine · Jõustub` is what the design's demonstration Matter
happened to hold, not a six-stage rail every file is measured against.

`Loodud` is drawn for a Matter this system created and **not** for an imported
one: `created_at` on a register-archive row is the moment the importer wrote it,
which for a 2019 file is a fact about a migration. That is also why a bare
imported Matter draws no strip at all rather than one lonely dot.

A current stage whose transition date nobody recorded is shown without one.
`Matter.stage` says where the file is, not when it got there.

### 13. The strip and the chronology are scoped before they are derived

Every source is read through its own `visible_to`, and `matter_intelligence` is
built once and passed to both. A restricted child must not change milestone
presence, connector count, spacing, ordering, current/future classification,
labels, dates, the row count or the `{n} kirjet` count. Deriving first and hiding
afterwards would leave exactly those observable traces (AUTH-003).

The test for it is the strong oracle: the page for an unauthorized reader, before
a RESTRICTED child is added and after, byte-for-byte identical with the rotating
CSRF token masked.

### 14. The chronology has two row kinds and no third

A 12 px accent dot for a milestone, a 6 px muted dot for a work entry. The folded
system run — «tegevusi 3 — näita ▸» — is retired: it was a third row style for
events that are now either milestones in their own right (`Teema loodud`,
`Hetkeseis: …`, `Arvamus välja`) or ordinary work. `collapse_system_runs` and
`latest_authored` remain as tested projection helpers; the Teema page stopped
calling them.

Milestone events take a row of their own even when they share a composer
operation with an entry. A save that wrote a note *and* changed the stage did two
separable things to the record.

`lisas olulise tähtaja` and `lisas kaasamise` left `_CLAUSES`. They were clauses
precisely because those facts had standing sections showing them; now that the
canonical records are projected into the chronology, the clause is the duplicate.

### 15. Structured facts are projected, never duplicated

`projected_milestones` reads `MatterWorkVictory`, `MatterImportantDate`,
`MatterEffectiveDate` and `MatterEngagement` and renders each as one row. No
`ChangeEvent` is written and none is read to do it: the canonical record already
carries the date, the wording and the visibility.

Only what has happened. A deadline in October is where the file is going, which
is the strip's question; the chronology answers what has already occurred.

This supersedes ADR 0065's presentation clause — structured facts were kept out
of the professional timeline because each had a standing section showing it. The
sections are gone, so the reasoning is gone with them: a fact nobody can see
anywhere is not a quieter chronology, it is a lost record.

### 16. The `Ajajoon` head is the label and the count

`AJAJOON` and `{n} kirjet`. The preview quote, the duplicated current step and
the `Kõik · Sissekanded · Sündmused` filter row are all gone from it. The
`?ajajoon=` query and `timeline_page` still honour the filter, so an existing
link still works.

### 17. The rail is four blocks

`TEEMA ANDMED` (`Teemaviide`, `Menetlusliik`, `Kellelt`, `Kellele`), `KOJA
ARVAMUS`, `SEOTUD MATERJALID`, `MÄRKMED`.

`Saabus` moved to the header. `Muu valdkond`, `Andmeklass` and `Märgi
testandmeteks` are retired **from this page only** — the columns, the values, the
endpoints and the audit rows are untouched, and `Muuda teemat` still edits what
it edited. The `TEST` badge still appears beside the title: what left a reading
surface is the ability to *change* what a record is.

The special-state rows — `Kirje liik`, `Suletud`, `Põhjus`, `Sulges` — and the
`Seotud` block (successor, predecessor, collaborators) render only when true, so
the normal Matter the target demonstrates carries exactly four blocks. They are
preserved for the same reason `matter_banner` is: they are required only in a
state the target does not show, not presentation the target supersedes.

`Õigusakt` is deliberately **not** added to this rail. The target does not place
it, and a later explicit design can decide where it belongs.

### 18. `Märkmed` autosaves and says so

No save button and no standing caption. The hint — `Salvestatud HH:mm` — is its
own swap target, so the textarea somebody is typing into is never replaced, and
because htmx swaps on 2xx alone a refused save leaves the previous time in place
rather than claiming one that did not happen. It renders nothing at all until
there is something true to say.

`save_note` answers 200 with that fragment instead of 204 and silence. A box that
saves silently and has no button is a box a person cannot tell has saved. The
note is still private, still one row per author, and still writes no
`ChangeEvent`.

### 19. The 420 px drop-zone overlap is fixed, not reproduced

`TEEMA_TARGET_420.png` shows `.cx-drop--corner` still absolutely positioned and
landing on top of the quick-date chips. `TEEMA_TARGET_SPEC.md` §H names this a
prototype defect. Below the 720 px breakpoint the drop area leaves the corner and
becomes a normal-flow full-width row directly under the chips. **Here the spec
overrules the screenshot.**

### 20. `Lükka edasi` leaves the Järgmiseks row

The target's row is the label, the text, the date, `✓ Tehtud` and `Muuda`. A
second disclosure holding four POST buttons and a date box does not belong in the
one row on the page that has to be readable at a glance. `matters:defer_action`,
`defer_choices` and `defer_base` are untouched and still reached from a work row
elsewhere.

### 21. `Seotud materjalid` is one `Lisa` disclosure

The search and the suggestions were two controls that did not know about each
other. They are now one affordance holding the search and then the suggestions,
which is also the order somebody uses them in. The suggestions are still computed
only on the request that opens it, and the deterministic ranking and the
authorization behind them (ADR 0062) are untouched.

### 22. The stale prototype shell is **not** adopted

The export carries a `Tähtajad` navigation item, an old footer build string and
`Stage 2!`. Current main retired the separate `Tähtajad` destination (ADR 0071),
and the shell, the navigation and the release identity are not this design's to
change. The target governs the Matter header, the content column, the composer,
the strip, the chronology and the rail.

### 23. The approved target *is* the visual baseline, and a missing one fails

Eleven baselines were deleted when this page was rebuilt, because every one of
them photographed the superseded design. That was right while the new page was
being built and wrong as a merged state: `compare()` skipped a scenario whose
baseline was absent, so the visual job reported ten Teema scenarios green for a
page nothing was comparing.

So the candidates this branch's own CI produced are reviewed against
`TEEMA_TARGET_1440.png` and committed, and **a missing baseline is now a
failure**. The first run of a genuinely new scenario is therefore red by design:
it writes its candidate into the `test-report-visual` upload, somebody looks at
it, and it is committed on the next push. One round, in exchange for the
property that a green visual job means every scenario in it actually compared.

Three of the deleted eleven do not come back. `kaasamine-tyhi`,
`kaasamine-kirjed` and `kaasamine-lisa` clipped `#kaasamine`, and §9 retired that
section: a clipped baseline of an element the page does not render can only ever
skip. What they covered is covered — recording a consultation is the composer
panel `teema-koostaja` photographs, reading one is a chronology row inside
`teema-ulevaade`, and the behaviour is `e2e/test_engagement.py`'s. Two new ones
take their place, for the two surfaces this design adds: `teema-kaik` clips the
derived `.tl-strip` (§12) and `teema-ajajoon` clips the two-kind chronology
(§14).

**§19 gets a test rather than a baseline.** The corrected 420 px drop area is the
one place the implementation is deliberately unlike its own reference
screenshot, so there is no approved picture to compare against — only a rule, and
a rule is asserted:
`test_at_420_the_drop_area_leaves_the_corner_and_at_1440_it_keeps_it` measures
both halves, because a narrow-width rule at the wrong specificity takes the
desktop corner with it.

## Consequences

* One additive migration, `matters/0017`: `EngagementKind.MEETING`,
  `MatterEngagement.response_count`, and the CHECK constraint dropped and
  recreated to accept the widened vocabulary. No business-data migration, no
  backfill, no production migration.
* The intelligence fragment routes (`#teema-faktid`) and the engagement
  add/update routes remain, with their services, their authorization and their
  tests. They are no longer linked from the Teema page. A later round should give
  *correcting* a structured fact a surface; the target does not place one, and
  deleting the only path to a correction would be a functional regression.
* `INDEX_VERSION` is not bumped and no corpus rebuild is run. Adding a nullable
  column that nothing derives from does not change what search knows.
* `devtools/matter-refinement/` is renamed to `devtools/teema-target/` and its
  render guard rewritten against the approved target: it declared the old
  page approved, and leaving it in the repository would leave the repository
  asserting two different targets.

## Alternatives considered

**Keep the composer collapsed and open it from the row.** Rejected: it is the
decision the target explicitly reverses, and the row-click affordance is
*additional* to open-by-default in the handoff, not a substitute for it.

**Add an `audience` column beside `title`.** Rejected: see §4. It would have
split one concept across two columns by the date the row was written.

**Give the strip its own table.** Rejected: see §12. A second place for facts the
domain already holds is a second place for them to disagree.

**Write `ChangeEvent` rows for the structured facts so the existing timeline
renders them.** Rejected: duplicate business data, and an audit trail is worth
exactly as much as its worst entry.

**Keep the six-question closure and add the two-question one beside it.**
Rejected: two ways to close a file is how two people record the same closure
differently. The four retired questions have surfaces of their own —
`+ Töövõit`, `+ Jõustumine`, and Dokumendid for the sent opinion.
