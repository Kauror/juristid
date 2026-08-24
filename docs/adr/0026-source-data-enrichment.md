# 0026 — Source facts are never rewritten; interpretation is added on top of them

- **Status:** Accepted — implemented on the `feat/source-data-enrichment` branch, pending integration
- **Date:** 2026-08-23
- **Builds on** ADR 0011 (next action and submission modelling), ADR 0012 (legacy register import), ADR 0015 (historical corpus integration), ADR 0020 (historical cutover state), ADR 0021 (the final register cutover), ADR 0024 (test data classification), ADR 0025 (multiple Matter senders).

## Context

Wave 1 is deployed and the imported data is usable. Three source facts, however,
are still only *displayed* rather than integrated, and each is displayed the way
it is because an earlier decision refused to guess about it:

- **`JÄRGMISEKS`** — the register's next-step column, shown verbatim beside the
  structured `NextAction` and labelled *Excelist*, because ADR 0011 established
  that the same sentence can carry a deadline, a reminder and a guess about
  somebody else's timetable and the column never recorded which.
- **The OneNote section** — kept as captured, never treated as a `PolicyArea`,
  because where a lawyer filed something in 2019 and the modern reporting
  taxonomy are different classifications built for different jobs.
- **`Matter.updated_at`** — rendered in the register's *Viimane tegevus* column,
  which for an imported row is the moment the 2026 cutover touched it rather
  than when anybody last worked on the file.

The first two refusals were right and remain right. The third is not a refusal
at all — it is an assumption nobody made deliberately, and it is wrong about
most of the register.

## Decision

**Enrichment adds interpretation on top of immutable evidence. It never rewrites
a source fact.** Every part of this ADR is a consequence of that one sentence.

### `JÄRGMISEKS` — narrow the refusal rather than reverse it

`app.legacy_import.register_next_actions` reads one sentence and returns an
immutable verdict. A minority of the register's instructions say what they mean
*in words* — a named waiting verb, a named monitoring verb, a stated deadline —
and where the wording is explicit the ambiguity ADR 0011 guards against is
simply absent.

Three rules bound it:

- **A closed allowlist of word forms**, matched on word boundaries. No stemmer,
  no similarity, no model, and no AI of any kind. `oodatavasti` is not `ootan`,
  and a stem pattern that swallowed it would convert a hedge into an
  instruction.
- **Written dates only.** Exact days, Estonian month names, quarters, half-years
  and explicit `NNNN. aasta` years. A bare four-digit number is not a stated
  year. Two plausible dates mean review required — never the first, the last or
  the nearest. A date that cannot exist (`5. kvartal`, `V kvartal`, `31.02`)
  stops the reading rather than being ignored. Parser 1.1 adds one further
  refusal: a date whose immediately preceding word is an entry-into-force verb
  belongs to that clause, not to the instruction — *jõustub 1.01.2028* beside a
  waiting verb states when an act takes effect, not when the awaited thing
  arrives.
- **Refusing beats guessing, and every refusal is named.** `NO_KIND`,
  `AMBIGUOUS_KIND`, `AMBIGUOUS_DATE`, `UNREADABLE_DATE`, `DO_WITHOUT_DATE`,
  `DO_DATE_WITHOUT_DEADLINE_WORDING`, `APPROXIMATE_DEADLINE`,
  `DATE_GOVERNED_BY_ANOTHER_CLAUSE`. A report that said
  only "40 of 134 converted" would leave nobody able to tell a rule that is too
  strict from a corpus that is genuinely ambiguous.

**The automation rate is not the goal.** Forty automatic readings and ninety-four
review-required is an excellent result. Broadening a rule to improve the
percentage is how a work queue stops being believed.

#### The auto-creation threshold

A `NextAction` is created only when all of the following hold:

- the derived register state says `CURRENT`, from the one approved snapshot;
- the Matter is open, `FULL` and `REAL`;
- the cell is not blank;
- **the Matter carries no `NextAction` at all** — see below;
- the parser understood the sentence;
- the target period has not wholly passed.

#### Existing human workflow always wins

Any prior `NextAction` — open, completed, cancelled or superseded — stops the
enrichment for that Matter. Not "any open one": a completed action proves
somebody has already worked the file through the structured workflow, and
reviving a spreadsheet sentence over their decision is the one failure this
operation must not have. Production carries at least one hand-made action today
and it comes through untouched.

This also supplies idempotency for free: once an enrichment action exists the
Matter has action history, so a second plan classifies it exactly as it
classifies a hand-made one, and a second apply creates nothing and raises no
second audit event.

#### Date precision and semantics

The existing contract is reused rather than reinvented. A stored approximate
date is **the first day of the period**, through
`app.workflow.dates.bounds_for`, and the precision decides how it is written.
Staleness compares today with the period's **end** — II poolaasta 2026 has not
passed on 2 July 2026, and comparing the 1 July anchor would say it had.

The semantics rules are conservative in one direction only, because only
`DO + DEADLINE` can be reported as late:

| Wording | Kind | Semantics |
| --- | --- | --- |
| waiting verb, with or without a date | `WAIT` | `EXPECTED_AROUND` |
| monitoring verb, with or without a date | `MONITOR` | `REVIEW_ON` |
| action verb + explicit deadline wording + an exact day | `DO` | `DEADLINE` |
| action verb + an approximate period | `DO` | `EXPECTED_AROUND` |
| action verb + an exact day, no deadline wording | — | review required |
| action verb, no date | — | review required |
| deadline wording on a period | — | review required |

A dateless `WAIT` or `MONITOR` is honest and is created; a dateless `DO` is not,
because the model would only accept it by asserting a date meaning the sentence
never stated.

#### Two pins, and a total refusal

`plan` requires `--expect-snapshot-sha256` and validates it against the derived
state table, failing closed if that table describes more than one workbook.
`apply` additionally requires `--expect-plan-sha256`, recomputes the plan, and
re-verifies every row's eligibility and source text inside the transaction. A
single changed row aborts the whole run: a partial apply against an approved
digest would leave a state that neither the plan nor the database describes.

The created action carries the sentence verbatim in `source_text`, and
`CurrentRegisterState.next_action_text` **keeps the register's wording**. The
two are different claims — *the register says this* and *Koda has decided this*
— and the first stays true whatever happens to the second.

`actor` is `None` and stays `None`. A machine read the sentence; attributing it
to whoever ran the command would put a person's name on a parser's reading.
What did decide it is recorded as `provenance` on the existing
`NEXT_ACTION_SET` event: the source, the snapshot digest, the immutable source
reference and the parser version.

### OneNote section → `PolicyArea` — a reviewed interpretation, never a rename

`LegacySourcePage.source_section` stays exactly as captured. Nothing in this
branch writes to it, and the page's original location is still displayed as the
department's own filing vocabulary.

The interpretation lives beside it, in the same shape as `LegacyStatusMapping`:

- **Reviewed alias rules are code**, in
  `app.legacy_import.onenote_policy_areas.REVIEWED_ALIAS_RULES`, keyed on the
  pair *(section group, section)* because the same leaf name can appear under
  two groups. There is deliberately no admin table of mappings — an unreviewed
  row in a database is exactly the guess this design prevents.
- **The registry ships empty**, and that is the honest state. Writing rules
  requires the inventory of what the corpus actually contains, which the
  command's `inventory` mode produces. Nobody here knows what `Muud`, `Üldine`,
  `ARHIIV` or `EL` were used for, and **unmapped is a valid result**.
- **One automatic rule**: a section whose normalised name is exactly one active
  area's canonical Estonian name. That is recognition rather than
  interpretation, and it is counted separately from reviewed aliases because the
  two carry different levels of evidence. Normalisation is whitespace and case
  only — diacritics are meaning here, not noise, so
  `app.core.text.normalize_for_matching` is deliberately **not** used.
- **Additive, always.** An area a lawyer set by hand survives. The modern
  taxonomy and the 2019 filing cabinet are not the same classification, and a
  page that lived in one drawer is not evidence that somebody's own choice was
  wrong. A Matter may gain several areas; no primary is chosen.
- **No taxonomy is created**, ever. A rule naming a missing or inactive area is
  a configuration error to be fixed, not a reason to create the area. **No `Tag`
  is created**: `PolicyArea` and `Tag` remain separate dimensions.
- **`PRIMARY` and `RELATED` links classify; `BACKGROUND` does not.** Background
  material lives in a themed section because of what it is *about*, which is not
  what the Matter is about. Widening this needs evidence, not a default.
- **`TEST` Matters are excluded.** A development record is not the department's
  history and must not enter its reporting classification.
- **Fail closed on capture authority.** Only `ONENOTE_DESKTOP` pages may
  classify. A page from the invalidated Graph export aborts the whole run: its
  page-to-content associations were proven wrong, and its filing is no better
  evidence. The plan's capture identity is a digest over the exact
  `(page key, capture id, XML SHA-256)` set it read, because the archive has no
  single archive-wide capture id — each page carries its own.

Writes go through one named service, `add_source_derived_policy_areas`, which is
only capable of adding. It records `IMPORT_APPLIED` — the event this codebase
already uses for "an import wrote something onto this Matter" — with the mapping
version, the capture digest, the rules and the source pages in the payload. A
new `ChangeEventType` would mean an audit migration for vocabulary alone, and
`IMPORT_APPLIED` is truthful about what happened. It is absent from
`TIMELINE_EVENT_TYPES`, so filing does not push meeting notes out of the
professional narrative. Adding nothing raises nothing, which is what makes a
second apply a genuine no-op in the audit trail as well as in the data.

### Historical activity — a derived answer, not a rewritten timestamp

`app.matters.activity` computes *when work last happened on this file* from
facts that are about the work. `Matter.updated_at`, `created_at` and `closed_at`
are not touched, and **no `closed_at` is inferred** from a OneNote date, a
terminal `HETKESEIS`, a last page edit or an Excel year. Activity display is not
closure reconstruction.

Eligible facts: a recorded closure; an authored entry; a submission that carries
a send date; a next action **a person** set or ended; `received_date`; and the
`source_modified_at` / `source_created_at` of `PRIMARY` and `RELATED` OneNote
pages. Excluded: `ImportBatch.started_at`, `CurrentRegisterState.observed_at`,
search-index refreshes, and — for anything imported — `updated_at`.

Three rules that look similar and are not:

- **The latest actual fact wins**, not the most canonical one. A page modified in
  2019 on a Matter closed in 2021 last saw activity in 2021; a Matter closed in
  2019 whose related page was still edited in 2021 last saw activity in 2021. A
  fixed source-priority list answers one of those pairs wrongly. Precedence
  exists only to name the basis when two facts fall on the same day, and can
  never select an earlier date.
- **A next action counts only when `created_by` or `ended_by` names a person.**
  Without this the `JÄRGMISEKS` enrichment would stamp today onto every Matter
  it touched and call it activity — the same error as `updated_at` arriving
  through a different column.
- **`source_modified_at` is preferred over `source_created_at`**, which falls
  out of "the latest wins" on a coherent page and does the sane thing on an
  incoherent one. If the product owner later prefers the creation date, the
  change is one clause in `activity_of` and the policy is testable there rather
  than buried in a template.

`None` is a real answer. An archive row with no dates at all has no known
activity, and printing today, or the import date, would be an invention. The
basis is exposed (`CLOSURE`, `SUBMISSION`, `ENTRY`, `NEXT_ACTION`, `RECEIVED`,
`ONENOTE_MODIFIED`, `ONENOTE_CREATED`, `NATIVE_RECORD`) so a future UI can say
*why*.

Every fact is a subquery annotation and `activity_of` reads attributes only; it
raises rather than querying when the annotations are absent, which is the guard
that keeps an N+1 from reappearing as a convenience. Child facts are scoped
through each model's `visible_to`, because a date column is a channel like any
other and a restricted entry must not announce itself through it.

## Consequences

- No migration. No new model, no new field, no new audit vocabulary.
- The register table is **not** rewired in this branch. `matter_table.html`
  still renders `matter.updated_at`, deliberately, because the shared table is
  being changed concurrently by the multiple-sender work. **Integration note:**
  after that branch merges, annotate `matter_list_queryset` with
  `annotate_last_activity` and replace the last-activity cell's rendering with
  the computed value. The column keeps its name — *Viimane tegevus* is what the
  value will then honestly be, and *Allika kuupäev* would be wrong for a native
  Matter.
- Both apply paths exist and **neither has been run against production**.
  Applying either is an operator decision taken from a plan somebody read.
- The OneNote enrichment produces nothing until reviewed mappings exist. That is
  the intended first state, not an incomplete implementation.

## Alternatives rejected

- **A language model, embeddings or fuzzy similarity for `JÄRGMISEKS`.** The
  master specification forbids AI writing authoritative state without human
  confirmation, and a work queue nobody believes is worse than none.
- **A database table of section→area mappings.** It would let an unreviewed row
  classify a decade of work. Code review is the control.
- **Replacing a Matter's policy areas from its OneNote section.** It would
  silently overrule the lawyers who set them.
- **Storing the computed activity date as a column.** It would need maintaining
  from six directions and would go stale the first time a write bypassed
  whatever maintained it — the same reasoning that keeps effective visibility
  derived (ADR 0005).
- **A new `ChangeEventType` for policy-area enrichment.** It would force an
  audit migration for vocabulary alone, and `IMPORT_APPLIED` already says
  truthfully what happened.
