# 0074 — The Teema detail page is the approved target

**Status:** accepted
**Date:** 2026-09-11
**Amended:** 2026-09-12 — §12.1, §12.2 and §12.3 replace §12's process-strip
source clauses. The strip is a sparse major-process projection, not a
chronological digest of every dated fact.
**Amended again:** 2026-09-12 — §12.4 narrows that amendment. The strip carries
two canonical *known future* milestones as well as the historical ones:
`Matter.response_deadline` as `Arvamuse tähtaeg`, and a canonical
`MatterEffectiveDate` as `Jõustumine`. The five sources are
`Alustatud`, `Koja arvamus`, `Arvamuse tähtaeg`, `Jõustumine`, `Lõpetatud`.
A generic `MatterImportantDate` and a `NextAction` remain excluded. Nothing else
in this ADR is affected.

**Amended a third time:** 2026-09-12 — §12.2's sentence «a future milestone is
drawn with the same dot, the same weight and the same date line as a past one»
is superseded by §12.5 below. Milestone membership, sources, ordering, labels
and dates are **unchanged**; only the drawing of them is. §9's «`+ Kaasamine`
asks three things» is likewise narrowed by §9.1: it asks those three, and two
optional provider pointers beside them.

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

#### 9.1 And two optional provider pointers (amended 2026-09-12)

`Liik`, `Keda kaasati`, `Vastuseid` — and, beneath them, `Smaily link` and
`Alchemer link`. Both optional, both external, both inert.

The reason is the one thing a single `MatterEngagement.url` cannot express: one
consultation round routinely has a mailing *and* a questionnaire, so somebody had
to drop one of the two addresses or keep it in a note nobody can click. The
generic `url` is unchanged and means what it always meant; these sit beside it.
See ADR 0027, amended, for why two vendor-named columns are the smaller wrong
than a lost address.

Neither makes a `Kaasamine` valid on its own — `Keda kaasati` is still what
identifies the record — but a typed link **does** count as attempted work, so a
panel holding an address and no audience is refused with a message rather than
silently discarded. The larger «Alustasin arvamuste küsimist» / «Arvamused
saabusid» redesign is still a separate round and is still not begun.

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
its own reasons.

Only milestones that exist are drawn. `Loodud · Küsitlus · Arvamus välja ·
Valitsuses · I lugemine · Jõustub` is what the design's demonstration Matter
happened to hold, not a six-stage rail every file is measured against.

#### 12.1 The strip is a sparse major-process projection (amended 2026-09-12)

**The six-source clause above is superseded.** As first written, this section
drew a column from the Matter's creation, every `MatterEngagement`, every sent
`Submission`, the current `StageVocabulary`, every `MatterImportantDate` and
every `MatterEffectiveDate` — a chronological digest of every dated fact on the
file. That made the strip a second, shorter copy of the chronology with the
header's `Hetkeseis` pinned in the middle of it, and it produced sentences the
record does not support: a closed Matter whose last recorded stage was
`Riigikogus` read `Riigikogus · praegu`.

The strip is a **sparse procedural story**: only major business milestones. It
may derive a milestone from exactly five sources — three named here, and two
added by §12.4, which is where the current table lives.

| Milestone | Source | Rule |
|---|---|---|
| `Alustatud` | `Matter.created_at` | `MatterOrigin.NATIVE` only |
| `Koja arvamus` | a SENT `Submission` with a `sent_at` | one column per genuine send |
| `Lõpetatud` | `Matter.is_open` and `Matter.closed_at` | current state, never a historical event |

Reserved for later rounds, and **not** drawn today: `Arvamuste kogumine` and
`Pöördumine`. Both are real procedural acts and neither has a canonical source
the model can yet identify, so drawing them now would mean guessing — and a
guessed milestone is indistinguishable, on the page, from a recorded one.

Four clauses carry the reasoning:

* **`Hetkeseis` belongs in the header.** `Matter.stage` is where the file
  stands *now*. It is stated once, in the metaline, and no stage is
  whitelisted onto the strip — not `Idee`, not `Riigikogus`, not any other. No
  `StageVocabulary` row or schema changes; the strip simply stops reading it.
* **Important dates belong in the facts and the history.**
  `MatterImportantDate` stays canonical and stays visible in its own fact
  section, in the chronology once it has passed, and on every work surface
  under the existing rules. It draws no column, in either direction. With it
  goes the strip's whole future *grammar* — the `todo` state, the sixty-day
  horizon and the `N p` countdown; the countdown itself is untouched everywhere
  it still has a source. **`MatterEffectiveDate` was excluded by this clause
  and §12.4 puts it back**, as `Jõustumine`, with the grammar still gone: a
  known future commencement is drawn as a plain dated column, not as a muted
  one with a day count.
* **A generic `Kaasamine` is not automatically a process milestone.** A
  `MatterEngagement` is a canonical record, a chronology row and detail
  information. `Küsitlus`, `Koosolek`, `Kirjade voor`, a historical `WEB_CALL`
  and `OTHER` are not evidence of an identifiable consultation round, and
  treating every one of them as `Arvamuste kogumine` would assert a procedural
  act nobody recorded.
* **An imported `Saabus` is not `Alustatud`.** `received_date` is the day Koda
  received something; `Alustatud` is the day the work started. `created_at` on
  an imported row is the moment the importer wrote it. `Matter` holds no
  business-start date besides those, so an imported Matter — `LEGACY_IMPORT`,
  `LEGACY_ONENOTE`, `PROMOTED_LEGACY`, `OTHER` — gets no `Alustatud` at all.
  Honest absence over a fabricated milestone standing leftmost on the page.

`Lõpetatud` is the name of the step and stays the name of the step. The
`Disposition` — `Menetlus lõppes`, `Jõustus`, `Loobuti` — is *why* it ended, it
is secondary information, and it reads as the column's `title` rather than
replacing the label. Closure is read off the Matter's current state and never
by finding a `MATTER_CLOSED` event: a reopened Matter keeps that event forever,
and a strip that went looking for one would show a currently-open file as
finished.

**`MatterWorkVictory` remains excluded**, as it always has been: a win is a
chronology milestone, not a stage of the proceeding.

#### 12.2 No milestone is ever the current one

No column on the strip is marked as the one the Matter is standing on, so the
`praegu` suffix is gone and no domain state says `current`. The rail still has
a visible end because `:last-child` draws no connector — a statement about the
rail, not a claim about the Matter. `is-current` and `is-todo` go with the
sources that produced them; the dot size, typography and connector geometry are
untouched. This is a semantic simplification, not a visual redesign.

The `is-todo` half of that is load-bearing after §12.4. A future milestone is
drawn with the same dot, the same weight and the same date line as a past one:
muting it would make the destination the file is heading for the least legible
thing on the rail, and the strip's claim about a future column is only that the
date is recorded, which is as true of the deadline as of the closure.

> **Superseded 2026-09-12 by §12.5.** The first sentence of this paragraph no
> longer holds: a future milestone *is* muted. The rest of §12.2 stands — no
> column is marked as the one the Matter is standing on, `is-current` and
> `is-todo` remain retired, and `praegu` is still gone.

#### 12.3 Unchanged

`Loodud` is drawn for a Matter this system created and **not** for an imported
one — the rule survives the rename to `Alustatud`, and it survives §12.4
untouched: an imported Matter still gets no `Alustatud` from the moment the
importer wrote its row. What §12.4 changes is that such a Matter is no longer
guaranteed an empty strip. Its `response_deadline` and its commencements are
dates the *source* recorded rather than artefacts of a migration, so an imported
file may now read `Arvamuse tähtaeg` or `Jõustumine` with no `Alustatud` to
their left. That is the honest shape of an imported record and not a gap to be
filled.

The chronology is not touched by this amendment: it may still show stage changes,
important dates, effective dates, `Töövõit`, engagements, submissions, closure
and the associated files exactly as it does today, and it keeps its own wording
`Arvamus välja` for a send — that sentence is about an event, where the strip's
column names the thing itself. The same holds of `Jõustub`/`Jõustus`, which the
chronology keeps and the strip does not: a chronology row has a tense and a
strip column is a name.

#### 12.4 A known beginning and a known destination (amended 2026-09-12)

**§12.1's "historical only" rule is narrowed, not reversed.** A Matter created
this morning with an answer due on 20.09.2026 drew one dot. The file's first
phase already had a recorded destination and the strip withheld it, which made
the sparsest possible strip also the least informative thing on the page. The
correction is two canonical sources, not a return to the six:

| Milestone | Source | Rule |
|---|---|---|
| `Alustatud` | `Matter.created_at` | `MatterOrigin.NATIVE` only |
| `Koja arvamus` | a SENT `Submission` with a `sent_at` | one column per genuine send |
| `Arvamuse tähtaeg` | `Matter.response_deadline` | one Matter-level column when the field is set |
| `Jõustumine` | a `MatterEffectiveDate` that is `ACTIVE` and has a `date_value` | one column per record |
| `Lõpetatud` | `Matter.is_open` and `Matter.closed_at` | current state, never a historical event |

So a new Matter reads `Alustatud · Arvamuse tähtaeg 20.09.2026`, and a
long-running one can read `Alustatud · Arvamuse tähtaeg · Koja arvamus ·
Jõustumine`.

**A future column is not a claim that something happened.** The strip now holds
two kinds of truthful information — acts that have been performed, and canonical
dated points the file is known to be heading for. Both are read off a record
that exists; neither is a prediction. What a future column says is *this is the
next dated point in the process*, and the grammar says it by saying nothing
extra: no `praegu`, no `N p`, no muted state, no redesign (§12.2).

**`Arvamuse tähtaeg` is formal, and it is not `Järgmiseks`.** It comes from
`Matter.response_deadline`, which is a date somebody outside Koda set for the
answer, and it takes that field's own `verbose_name` as its label. A lawyer's
`NextAction.target_date` is a self-set work plan; it is `Plaanis` on the
surfaces that show a plan and it draws **no** process-strip column. Carrying a
date is not what makes a fact a procedural milestone — whose date it is, is. The
strip and the `Järgmiseks` wording are independent decisions on independent
sources and neither depends on the other landing.

**A passed deadline keeps its place.** It is drawn whether it is ahead of us or
behind us, at its own chronological position either way. A deadline that has
gone by was a real point in this file's course; dropping it on the day after
would rewrite the story, and pinning it to one end would misdate it.

**`Jõustumine` does not replace `Arvamuse tähtaeg`.** When a commencement date
becomes known the rightmost column naturally becomes `Jõustumine`, because its
date is later — not because the deadline was erased. Both were real.

**No primary commencement is invented.** `MatterEffectiveDate` is several per
Matter *by design*: one law commences in stages, and nothing in the domain
elects one of them — there is no `is_primary`, no flag and no selection rule to
follow. So each genuine record draws its own column at its own date, «mis
jõustub» reads as that column's `title`, and the strip stays sparse by the
narrowness of its five sources rather than by collapsing a table that means what
it says. `KNOWN_DATE` is the only kind a constraint lets carry a date, so
`date_value is not None` is the kind test; `CANCELLED` and `SUPERSEDED` records
keep their fact section and their chronology row and draw nothing, because two
contradicting commencement dates on one rail is not a sparser strip, it is a
wrong one.

**Ordering is by date, with the proceeding as the tie-break.** Milestones sort
on their own date. Two recorded on the same day fall back to a fixed place in
the procedure — `Alustatud`, `Koja arvamus`, `Arvamuse tähtaeg`, `Jõustumine`,
`Lõpetatud` — so that an opinion sent *on* the deadline day reads as sent by it,
and so that two renders of one unchanged Matter cannot disagree. Two of the same
kind on one day keep their source's own deterministic order: sent opinions are
read `(sent_at, pk)` rather than on the model's default `-sent_at, -created_at`,
which would have handed a same-day pair to the stable sort in reverse.

**Closing discharges nothing.** A Matter closed before its response deadline
still shows that deadline, which places it to the right of `Lõpetatud`. The date
was set and was never withdrawn; deciding here that a closure cancels an
external deadline would invent a discharge rule the domain has not recorded.
That is a separate product seam, and this clause records the scenario rather
than working around it.

#### 12.5 Reached, today and ahead (amended 2026-09-12)

**§12.2's «same dot, same weight» is superseded. Nothing else is.**

The reasoning for drawing a future column exactly like a past one was that
muting the destination would make it the least legible thing on the rail. In use
it cost more than it saved. A Matter created on 12.09, with an answer due on
30.09 and a commencement on 29.10, drew three identical filled dots joined by a
solid accent line — which reads as a file that has already been through all
three. The strip's own sparseness made it worse: there is nothing else on it to
correct the impression.

So the strip now says *where we are* by drawing, and it still says nothing extra
in words:

| state | test against the application's own today | drawn as |
|---|---|---|
| reached | `sort_on < today` | accent dot, accent rail behind it |
| today | `sort_on == today` | accent dot, ringed with `--accent-glow` |
| ahead | `sort_on > today` | muted dot, muted rail, quieter label |

**The connector carries today's position.** Each segment of rail is a two-colour
gradient with one hard stop at `--tl-reach` — the fraction of *that segment's*
calendar days that have gone. The columns are evenly spaced rather than
time-scaled, so the rail is a proportion and not a scale; within one segment the
fraction is real days, which is the only honest thing a fixed-width column can
say about the time inside it. Two milestones on one day take the
whole-segment answer and never a division, because a `NaN` would reach the
stylesheet as an unparseable gradient stop and silently take the rail's colour
with it.

**This is presentation, and the word is load-bearing.** `state` and `reach` are
computed from the already-built, already-scoped step list, after it is sorted.
No milestone changes source, membership, order, label or date because of them,
and the AUTH-003 oracle of §13 covers them by construction: a restricted child a
reader may not see cannot move a state, a fill, or a byte of the strip.

**And it is not the retired grammar coming back.** §12.1 removed a *domain*
grammar — a step the file was standing on, a sixty-day horizon, an `N p`
countdown, `is-current`, `is-todo`, `praegu` — all read off sources the strip no
longer has. These three states are read off the calendar alone, nothing is named
as the current step, and every visible word on the strip is byte-for-byte what
it was.

**Colour is not the only signal.** A column dated today carries
`aria-current="date"`; one still ahead carries a visually hidden «Tulevikus».
A strip that distinguished reached from ahead by colour alone would read as
identical milestones to a screen reader, which is the same defect for the
readers least able to work around it.

**The mobile contract is unchanged.** The fill is the step's own `::before`, so
it moves with the column when the rail scrolls at narrow widths. No new grid
column, no viewport-relative geometry, and the 420px scroller behaves exactly as
§H requires.

**The response-deadline discharge question is untouched.** A Matter closed
before its deadline still shows that deadline to the right of `Lõpetatud`
(§12.4). What this amendment does is make it visibly *ahead*, which is what it
is; whether a closure should discharge it remains a separate product seam.

### 13. The strip and the chronology are scoped before they are derived

The strip reads two child tables through a scope: sent opinions through their
own `visible_to`, and commencements through the page's single
`matter_intelligence`, which is built once and handed to the strip and the
chronology alike (§12.1, §12.4). `Matter.response_deadline` is a column on the
Matter the reader has already been proven to hold, so it carries no scope of
its own.

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
