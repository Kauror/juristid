# ADR 0088 — `Uus teema` is manual-first: the reading is withdrawn, Saatja starts empty, Valdkond folds away

- Status: accepted
- Date: 2026-09-16
- Stage: pre-QA (shared-gate development phase)
- Related: ADR 0060 (deterministic document-assisted intake — **suspended on
  this surface, reversed nowhere**), ADR 0064 (assisted intake happens while the
  Teema is being created — this withdraws its lawyer-facing half and keeps its
  staging), ADR 0072 (document reading is an ephemeral intake capability), ADR
  0073 (one control finds and names an institution — **unchanged; only when it
  offers is changed**), ADR 0063 and ADR 0069 (one organisation catalogue,
  Saatja answers Adressaat), ADR 0087 (create-time similar-Matter suggestions —
  **preserved in full**), ADR 0025 (the shape of a control is a promise about
  the data), `taxonomy/0003` and `taxonomy/0004` (vocabulary retirement by
  `is_active`, never by deletion)
- Number: 0088. 0087 is held by the similar-Matter work merged immediately
  before this branch started.

## Context

The first structured feedback from the lawyers using the demo is not about
missing capability. It is about the capture page. `Uus teema` is the screen a
lawyer opens every day, and three things on it each ask for attention before a
question has been answered:

1. **the document reader's suggestion panel** — a block of machine proposals
   sitting between the file control and the fields, offering a title, a sender,
   a deadline, a Menetlusliik, an Õigusakt and Valdkonnad, each to be read,
   judged and dismissed. Reported as costing more attention than it returns;
2. **Saatja's shortlist** — eight institutions drawn under an empty search box
   on every visit, as a list to read before a question most people answer by
   typing three letters;
3. **Valdkond** — nineteen labels and `Muu`, wrapping across three rows,
   permanently expanded whether or not anybody intends to classify.

None of the three is wrong in itself. Each was argued for on its own terms and
each argument still stands on its own terms. What the feedback says is that the
three of them on one screen at one time make the page read as a survey again,
which is the exact defect the redesign that produced this layout set out to fix.

Two labels in the Valdkond vocabulary drew a separate complaint, and it is a
different kind of complaint: `Koalitsioonilepped` and `ELi õiguse ülevõtmine`
are not areas of law at all.

## Decision

### 1 — Document-derived suggestions are withdrawn from `Uus teema`

The lawyer-facing half of assisted intake on the *creation* form is switched
off: the «Failist leitud» panel, the `data-prefill-*` markers the browser writes
into empty controls, the reading states the form printed while it waited, and
the `suggestion_state` field that carried which proposals were in use.

**It is a setting, not a deletion.** `MATTER_INTAKE_SUGGESTIONS_ENABLED`
defaults to false, and `create_form_suggestions_offered()` is the one function
that reads it. The rules, the vocabulary tables, the reader, the corpus tests
and the staging pipeline are untouched: the decision withdrawn here is about
*where a lawyer meets* a reviewed capability, not about whether that capability
is right. `tests/test_intake_staging.py` runs its suggestion assertions with the
switch on, so the reinstatement path is exercised rather than promised.

**Three boundaries, stated because each of them was a way to get this wrong.**

- **Files are not suggestions.** Choosing a file still stages it, staging still
  promotes it into one Document with one immutable version inside the create
  transaction, and a refused save still holds it. Nothing about upload moved.
- **No invisible remainder.** A withdrawal that removed the panel and left the
  prefill markers or the hidden state field behind would look complete and would
  not be — an automatic form mutation behind a hidden UI is worse than the panel
  it replaced. The gate is applied at `_intake_context`, above all three.
  Because the browser polls only while the panel reports `reading`, and it never
  reports `reading` now, the poll stops without a second switch in the script.
- **ADR 0087 is untouched.** `Sarnased teemad` proposes *Matters*, never form
  values; it is a different feature that happens to share the screen, and it
  stays exactly as it merged.

`Muuda teemat` keeps its reading. That is a page somebody opens in order to look
at what the documents say, about a record that already exists; the feedback was
about the form filled in every day, and it is not evidence about that surface.

### 2 — Saatja opens as an empty box

The picker's shortlist is offered to the search rather than drawn under it. One
flag on `organisation_picker.html`, `quiet`, marks the shortlist chips the way
the long tail is already marked — present as real controls, `hidden` until the
search finds one or somebody has chosen it.

**Nothing about the field's meaning moved.** Same two fields, same names, same
values, same `resolve_source_organisations`, same reuse of an exact or alias
match, same safe creation of a genuinely new body, same refusal of a spelling
that names two, same Adressaat default reading the same ranked row. ADR 0073's
one-control contract is exactly as it was; `quiet` decides when the offer is
made, which is presentation.

**A chip that is an answer is never hidden**, which is the rule the quiet field
rests on: a typed provisional sender and a body chosen before a refused save
both come back visible. With scripting off the `<noscript>` fallback carries the
shortlist as well as the tail — the one arrangement where the fallback offers
more than the scripted control does at rest, and what stops the quiet field
being a regression without JavaScript.

Adressaat keeps its chips. It is behind a disclosure that is shut on the
ordinary visit and answered by the sender before anybody opens it, so it costs
nothing at rest, and this round changed only the field the feedback named. The
resulting asymmetry between the two pickers — and between this Saatja and
`Muuda teemat`'s — is deliberate for one round and is named in the open
questions below.

### 3 — Valdkond is a disclosure, and two labels stop being offered

The vocabulary is drawn inside a `<details>` whose summary carries the answer:
«Valdkonnad · Ehitus, Keskkond». Same checkboxes, same name, same values, same
many-to-many, same `Muu` affordance — ADR 0025's promise is intact, because the
control still says the field holds several.

`<details>` rather than anything scripted, for the reason Adressaat uses one: it
works with scripting off, and the server renders it open wherever the fold would
hide something that has to be read — a refusal, or the free-text box a ticked
`Muu` reveals. It carries `data-stay-closed`, so a refused save with areas
ticked comes back shut and says so in the summary rather than unfolding the
vocabulary to prove an answer the summary already states. A small binding keeps
the summary true while somebody is ticking with the fold open; the server
renders the same text on every load, so scripting off loses nothing but the live
edit in between.

**`Koalitsioonilepped` and `ELi õiguse ülevõtmine` are withdrawn from new
selection** — vocabulary version 4.0, `taxonomy/0007`, `is_active=False` and
nothing else.

- `Koalitsioonilepped` names a *document*. A coalition agreement is a source in
  the way a ministry's draft is, and the product has the field for that:
  `Õigusakt` (ADR 0070). A tax measure announced in a coalition agreement is
  Maksud ja toll, and filing it under the agreement is how it leaves the tax
  report.
- `ELi õiguse ülevõtmine` is Menetlusliik spelled twice.
  `Track.NATIONAL_TRANSPOSITION` has carried those exact four words since
  `matters/0001`. The duplicate made a directive about construction get filed
  under the procedure instead of under Ehitus, and the two answers then
  disagreed about one file.

**Deactivated, never remapped, never deleted**, exactly as the seven retired
before them. Rows stay, relations stay, statistics still count them, the Teema
header still offers them back under "varasem valdkond", and `MatterEditForm`
still validates against the whole table and offers the active vocabulary plus
whatever this Matter already carries — so correcting one field on an old Matter
cannot drop its filing. **In particular nothing infers the `NATIONAL_TRANSPOSITION`
track from the withdrawn area**: a Matter may carry either, both or neither, and
deriving one from the other would write a classification nobody reviewed.

No search-index change and no reindex: `is_active` decides what is *offered*,
and the projection is built from relations this round did not touch. The
register's Valdkond filter resolves a requested key against the whole table, so
a bookmarked filter for a withdrawn area still works; only the offered list
shrinks, which is the contract `taxonomy/0004` already set.

## Consequences

- The capture page is materially quieter: one panel, one chip row and one open
  vocabulary gone, with no field removed and no value lost.
- A lawyer who wants the document reading on `Uus teema` cannot have it until
  the setting is turned back on. That is the decision, and the reinstatement is
  one flag rather than a rewrite.
- The organisation control now behaves differently at rest on `Uus teema` and on
  `Muuda teemat`. One round of that is acceptable; two would be drift.
- The intake reader still reads staged files, and nothing now shows what it
  found. That is deliberate: the reader is what makes the staged file's text
  available and it is also what a reinstatement needs working, and stopping a
  deployed worker is an infrastructure decision this round did not take. The
  cost is bounded by what it already was — one short read per staged file, on a
  queue that is empty almost always (ADR 0072). An operator who wants it idle
  stops the process; nothing in the application depends on it running.
- `onenote_policy_area_enrichment`, a manually-run planning command, proposes an
  area only when a source section is named after an **active** one. It will stop
  proposing the two withdrawn labels. No plan has been applied against real
  material, nothing is backfilled, and this is recorded rather than worked
  around.

## What this round deliberately did not do

The brief's later packages were inspected far enough to be sure this one does
not make them harder, and then left alone:

- **Hetkeseis.** `StageVocabulary` has `is_active`, and `selectable_stages()`
  is the single read of it. **It is not yet the same safe retirement mechanism
  `PolicyArea` has** — this sentence said it was, and the Package 2
  investigation established by running the code that it is not. Three surfaces
  narrow the *validating* queryset to active rows without unioning in the
  stage a Matter already holds:

  * `MatterEditForm.__init__` — `set_choices(self, "stage", active_stages())`;
  * `MatterFieldForm.__init__` — the same line;
  * `_header_context` — `"stages": StageVocabulary.objects.filter(is_active=True)`,
    which is what the header's inline stage `<select>` renders.

  So on a Matter holding a stage that has since been retired, the chip is not
  offered, posting that stage back is refused as an invalid choice, and saving
  **any unrelated field** — a corrected title — clears `Matter.stage` to
  `NULL` through `change_stage(matter, stage=None)`. `PolicyArea` and
  `LegalInstrumentType` do not behave this way: `MatterEditForm` unions each
  Matter's own held rows into the offered list and validates against the whole
  table.

  **This does not affect this PR.** Package 1 retires two `PolicyArea` labels
  and **no `Hetkeseis` value whatsoever**; `taxonomy/0007` does not touch
  `StageVocabulary`, and no stage row has ever been retired, which is why the
  defect is latent rather than live. But a dedicated retention fix on those
  three surfaces — shaped exactly like the Valdkond and Õigusakt unions, plus a
  template marker so a retired chip reads as a former answer — is **required
  before any future stage retirement**, and therefore before the Package 2
  Hetkeseis work. The proposed Package 2 vocabulary is in any case
  label-only on the ten existing rows, so it retires nothing and does not
  itself depend on the fix.
- **Õigusakt.** `LegalInstrumentType` also has `is_active` and `sort_order`, and
  `MatterEditForm` already unions in a Matter's own retired types. Multiple
  selection is real and must survive (ADR 0070); `legal_instrument_raw` on
  `CurrentRegisterState` is immutable import provenance and is never written
  from the application.
- **Menetlusliik.** This one is *not* safe by the same mechanism.
  `Matter.track` is a `TextChoices` column, not a vocabulary table, so there is
  no `is_active` to set; it is referenced by `StageVocabulary.applicable_tracks`
  (an `ArrayField` of the same choices), by the reporting projection and by
  migrations under its stored keys. Removing it from the visible form while
  keeping a domestic/EU distinction is a design decision that needs its own
  ADR, and it is the one place where the later packages could be got wrong
  cheaply.

  **Not by imports.** This sentence listed imports among those references and
  it should not have. `app/legacy_import/` does not populate or reference
  `track` anywhere — the historical register did not carry a Menetlusliik
  column at all, which the metric catalogue states in the product's own words
  on `MATTERS_BY_TRACK`: *«Register ei sisaldanud menetlusliiki; see täidetakse
  selles süsteemis.»* The rest of the dependency list above stands as written.
- **Saatja/Adressaat duplication.** `Matter.source_organisations` (many) and
  `Matter.addressee_organisation` (one) are distinct relations with distinct
  meanings, and `Submission.recipients` is a third, independent of both — so an
  outgoing submission genuinely may be addressed elsewhere. Dropping Adressaat
  from intake is a UI decision that does not require a data change, but it does
  require deciding what ADR 0069's default means when the question is not asked.
