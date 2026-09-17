# ADR 0089 — One question per classification: the reviewed Hetkeseis and Õigusakt vocabularies, and the three questions `Uus teema` asks

- Status: accepted
- Date: 2026-09-17
- Stage: pre-QA (shared-gate development phase)
- Related: ADR 0070 (`Õigusakt` is a governed vocabulary of its own, and it is
  not `Menetlusliik` — **upheld here, not narrowed**), ADR 0032 (stage,
  disposition and next action are separate concepts — **upheld**), ADR 0088 (`Uus
  teema` is manual-first — this builds on it), ADR 0032 and its Amendment
  (vocabulary retirement by `is_active`, and a retired value stays offered on
  the record that holds it), ADR 0069 (Saatja answers Adressaat — **superseded
  on `Uus teema` only**), ADR 0063 and ADR 0073 (one organisation catalogue, one
  control that finds and names an institution — unchanged), ADR 0025 (the shape
  of a control is a promise about the data), ADR 0080 (`Õigusakt` is read from
  one document's head — unchanged and still switched off by ADR 0088), ADR 0087
  (the similar-Matter finder — preserved in full), `taxonomy/0003`,
  `taxonomy/0004` and `taxonomy/0007` (retirement, never deletion, never a fuzzy
  remap)
- Number: 0089. 0088 is held by Package 1, the branch this one builds on.

## Context

The lawyers' second structured feedback round on the demo is not about missing
capability either. Package 1 took three things off `Uus teema` that asked for
attention before a question had been answered (ADR 0088). What this round says
is that the questions themselves overlap: the page asks a lawyer to classify one
file in four ways, and two of the four are the same answer written twice.

A lawyer filing an incoming draft wants to answer three questions:

1. *Mis seisus asi praegu on?* — `Hetkeseis`
2. *Mis tüüpi õigusloome objektiga on tegemist?* — `Õigusakt`
3. *Kes selle meile saatis?* — `Saatja`

The page asked six. `Menetlusliik` asked whether the procedure was domestic or
European, and `Õigusakt` — whose seventeen values included `Direktiiv`,
`EL määrus` and `EL teatis` — had already said. `Adressaat` asked who Koda
answers, on the screen where a file *arrives*, before anybody has decided to
answer anything, and arrived pre-filled with the sender: the same body under a
second label. And on the Teema page the answer to *kes selle meile saatis?* was
labelled `Kellelt`, while the two forms that write it call it `Saatja`.

Two of the vocabularies were also wrong about their own audience. `Hetkeseis`
was read out of the workbook in 2011's words and seeded `is_provisional`,
explicitly pending the review this round is. `Õigusakt` was derived from a decade
of the register's own spellings (ADR 0070) — which is right about what the
register *says* and is not a menu: seventeen kinds, four of them administrative
acts nobody has filed under in years, three of them separate rows for strategy,
development plan and action plan, and no way to tell from the label which of
`Direktiiv` and `Konsultatsioon` is European.

## Decision

### 1 — `Hetkeseis` is the reviewed eleven

| # | Version 2.0 | key | was |
| - | ----------- | --- | --- |
| 1 | Idee | `idea` | *unchanged* |
| 2 | Kooskõlastusringil | `consultation` | *unchanged* |
| 3 | Valitsuses | `government` | *unchanged* |
| 4 | Riigikogus | `parliament` | *unchanged* |
| 5 | Jõustumise ootel | `awaiting_entry` | «Ootan jõustumist» |
| 6 | Jõustunud | `in_force` | *unchanged* |
| 7 | Eesti seisukoht koostamisel | `estonian_eu_position` | «Eesti seisukoht» |
| 8 | ELi menetluses | `eu_procedure` | *unchanged* |
| 9 | ELi õiguse ülevõtmise ootel | `awaiting_transposition` | «Ootan ELi õiguse ülevõtmist» |
| 10 | Muu | `other` | *unchanged* |

**Three rewordings, and nothing else.** Nothing added, nothing retired,
nothing remapped. Every key, row,
`Matter.stage` relation, help text, sort order, register filter and reporting
projection is untouched: a stage is addressed by its key everywhere it is
stored, filtered or counted, so a reword is a display change and nothing else.
Two of the three also stop writing in the first person — a column that says *I
am waiting* is a sentence about whoever is reading it rather than about where
the file stands.

Nothing needed retiring, so nothing was retired. The mechanism exists and is
exercised in `tests/test_reference_stages.py` rather than used: PR #231
established that a Matter standing in a since-retired stage is offered it back
and may keep it (ADR 0032 §Amendment), and that contract is asserted here
against a stage retired inside the test.

**`is_provisional` comes off.** `workflow/0004` set it with a stated condition —
*until the department head and the lawyers have reviewed the stage vocabulary* —
and this round is that review. A flag that outlives its condition says the
wording is still an open question.

#### `Jõustunud` does not close anything

It is a stage, and `workflow/0004` has said so since the vocabulary was seeded:
an act entering into force does not end Koda's work on the file, and monitoring
implementation is ordinary work. Nothing here changes that, no rule fires on it,
and the department's own description of it is transcribed unaltered.

#### `Rohkem ei tegele` is not a stage, and is not added

The feedback asked for it as a Hetkeseis. It is not one, and the product
already implements the concept.

`Hetkeseis` says where the **external** process stands: the Riigikogu has it, it
is on a consultation round, the act is waiting to come into force. *Koda has
stopped working on this* is a different question about a different actor, and it
is `Disposition.MONITORING_STOPPED` — «Koda lõpetas jälgimise», offered on
`Lõpeta teema` as «Koda ei tegele edasi» and in the composer as «Loobuti». ADR
0032 separated stage from disposition deliberately (AGENTS.md, master
specification 3.4), and a stage that meant the second would put both answers in
one column and leave every surface reading it unable to tell which had been
given.

The workbook has agreed since 2011. Its raw value `rohkem pole tegevusi plaanis`
is read by `workflow/0004` as that disposition rather than as a stage, for
exactly this reason — so a stage with neighbouring words would also have made
the historical reading and the current vocabulary disagree about the same words.

**So nothing was added, and nothing needed adding.** The lawyer-facing action
exists, it is implemented against disposition, and this round leaves the
boundary where it was.

### 2 — `Õigusakt` is the reviewed ten, in two named groups

| # | Version 2.0 | key | was |
| - | ----------- | --- | --- |
| 1 | VTK | `vtk` | *unchanged* |
| 2 | Seadus | `seadus` | *unchanged* |
| 3 | Määrus | `maarus` | *unchanged* |
| 4 | Koja ettepanek või pöördumine | `koja-ettepanek` | **new** |
| 5 | Strateegia, arengukava või tegevuskava | `strateegia-arengukava-tegevuskava` | **new** |
| 6 | Muu siseriiklik | `muu-siseriiklik` | **new** |
| 7 | ELi konsultatsioon | `eli-konsultatsioon` | **new** |
| 8 | ELi direktiiv | `direktiiv` | «Direktiiv» |
| 9 | ELi määrus | `el-maarus` | «EL määrus» |
| 10 | Muu ELi dokument | `muu-eli-dokument` | **new** |

Withdrawn from new selection, by `is_active=False` and by nothing else:
`eelnou`, `el-teatis`, `konsultatsioon`, `strateegia`, `arengukava`,
`tegevuskava`, `visioon`, `korraldus`, `kaskkiri`, `ettepanek`, `kusitlus`,
`muu`.

**Two rows are reused and the reuse is the point.** `direktiiv` and `el-maarus`
are *objectively identical* to the reviewed concepts — version 1.0's own
description of `Direktiiv` says a directive is always an EU act, and «EL määrus»
and «ELi määrus» are the same three words — so a new row beside either would
split one classification in two and strand every Matter already carrying it. The
key, the row, the relations and every historical alias are unchanged; only the
label is.

**`konsultatsioon` is deliberately not reused as `ELi konsultatsioon`.** Its
version-1.0 description says outright that it covers a domestic public
consultation and that whether the organiser is European is answered elsewhere.
Relabelling it would rename a row into something some of its Matters are not.
The same reasoning keeps `ettepanek` (which covers a Commission proposal) apart
from `Koja ettepanek või pöördumine`, and keeps `strateegia`, `arengukava` and
`tegevuskava` out of the new combined row: that row is true of *some* of them,
and a migration cannot tell which.

**Nothing is deleted, nothing is remapped.** The twelve keep their row, their
key, their description, their relations, their place in every statistic, their
register filter and their chip on the Matters that carry them. This is
`taxonomy/0003`, `taxonomy/0004` and `taxonomy/0007`'s rule applied a fourth
time, and the refusal is the same refusal: there is no reviewed equivalence
between any withdrawn row and any new label, and writing one down is how a guess
becomes a fact.

**The five new rows carry no aliases.** An alias is a claim that the historical
register wrote this concept down under that spelling, and none of the five was
ever a category the register had. Giving them the withdrawn rows' spellings
would silently reclassify a decade of filing the next time an import ran.
`canonical_legal_instrument_keys` therefore still reads the whole seventeen and
is unchanged: «strateegia» in a 2014 cell still means `strateegia`.

### 3 — `Muu` is a set of rows, not one row

Version 2.0 splits the escape hatch along the axis it cares about, so
`OTHER_LEGAL_INSTRUMENT_KEYS` is `{muu, muu-siseriiklik, muu-eli-dokument}` and
what used to be one key comparison is a membership test. The *behaviour* is
version 1.0's and is unchanged: any of them reveals the free-text
`Õigusakti liik` box, `Muu` without the text is refused on the empty box,
unticking it clears the text, and the free text is never a taxonomy row
(ADR 0070 §8).

Three consequences, each of which was a way to get this wrong:

- **the rail folds the text onto the first such row only.**
  `legal_instrument_other` is one column, so a Matter carrying two of the three
  would otherwise read as having said the same sentence twice;
- **`Seotud materjalid` excludes all three.** Two files that both answered «none
  of these fitted» have agreed about nothing, and that is as true of
  `Muu siseriiklik` as of `Muu`. Without this, every new Matter carrying the
  commonest domestic escape hatch would match every other one (ADR 0087);
- **no rule may ever infer one.** `INSTRUMENT_NEVER_INFERRED` is now the whole
  set. A machine has nothing to write in the box the chip makes required, and a
  suggestion that puts a form into a state it cannot be saved from is worse than
  no suggestion (ADR 0080 §3).

### 4 — `Menetlusliik` leaves `Uus teema`, and is derived from nothing

The reviewed list names the group in the label: six types say *siseriiklik*,
four say *ELi*. That is what keeps the distinction the lawyers asked to retain
answerable — a Matter carries its `Õigusakt` types, and
`DOMESTIC_LEGAL_INSTRUMENT_KEYS` / `EU_LEGAL_INSTRUMENT_KEYS` say which group
each offered type belongs to.

**It is a reading, not a source.** Nothing writes `Matter.track` from it, and
that is the decision rather than an omission.

`Menetlusliik` says what kind of **procedure** a file is on. It has seven values,
not two, and no instrument type entails one. A `Seadus` transposing a directive
is a domestic *instrument* on a `NATIONAL_TRANSPOSITION` *track*; a rule writing
`DOMESTIC` from `seadus` would be wrong about precisely the files the
distinction exists for, and it would quietly reduce a seven-value classification
to a domestic/EU boolean. `KODA_INITIATIVE`, `STRATEGY`, `IMPLEMENTATION` and
`OTHER` are not on that axis at all, so there is no version of the rule that is
both complete and true.

So a Matter created here carries **no `Menetlusliik`**, and the column is
answered where its meaning is actually known: by a person, on `Muuda teemat` and
in the Teema rail's inline editor, both of which offer the whole vocabulary
including `NATIONAL_TRANSPOSITION`.

**`Matter.track` is otherwise untouched.** The column, the seven values,
`StageVocabulary.applicable_tracks`, the register's `?menetlusliik=` filter, the
`MATTERS_BY_TRACK` metric, `change_track`, the `MATTER_TRACK_CHANGED` audit
event, `MatterEditForm` and `MatterFieldForm` all stand. What is gone is the
question on the capture screen — and the *field*, so a forged POST carrying
`track=NATIONAL_TRANSPOSITION` is not part of the request as far as
`MatterCreateForm` is concerned.

**Edits write nothing either.** Changing `Õigusakt` on `Muuda teemat` leaves a
stored `track` exactly as it was. The alternative is a field that silently
rewrites itself under an edit about something else, which is the defect PR #231
fixed for `Hetkeseis`.

### 5 — `Adressaat` leaves `Uus teema`, and stays everywhere else

`addressee_organisation`, `addressee_name` and the `addressee_is_manual` hidden
field are gone from the create form, together with the shortlist promotion, the
disclosure, the summary and the browser half that kept them in step. ADR 0069's
default — *a file that arrived from X is answered to X* — is **superseded on
this surface**: it filled one field from another and therefore printed the same
body twice under two labels, on the screen where a file arrives and before
anybody has decided to answer anything.

**Saatja and Adressaat are not merged, and must never be.** They are different
facts about different acts: an incoming file has a sender, and an opinion Koda
sends has a recipient — a ministry, the Riigikogu, an EU institution or somebody
else — which is not derivable from who wrote in. One catalogue, two relations
(ADR 0063). `Matter.addressee_organisation`, the `update_field` endpoint, the
Teema rail's `Kellele` row, `Muuda teemat`, `resolve_addressee`, the submission
workflow, the importers and every Matter already carrying an addressee are
untouched. A Matter created under this round simply starts without one.

### 6 — One word for one fact: `Kellelt` becomes `Saatja`

`Uus teema` and `Muuda teemat` asked for `Saatja`; the Teema rail and the edit
form's own label answered with `Kellelt`. One fact with two names is two
questions as far as a reader is concerned, which is what the feedback reported.

The rail row and `MatterEditForm.source_organisations` now read `Saatja`. The
field, the relation, the checkboxes, the `Uus saatja` box, the search, the
`update_field` endpoint, the audit events and every stored value are unchanged;
only the word is. `Kellele` keeps its name, because it is the other fact.

The row is **renamed, not removed**. It is the only place the Teema page states
who sent the file — the header band carries Vastutaja, Valdkond, Hetkeseis,
Saabus and Tähtaeg and no sender — so deleting it would leave *kes selle meile
saatis?* with no answer on the page at all, which is the opposite of what the
feedback asked for.

### 7 — What `Uus teema` asks, and in what order

Saatja · Valdkond · Hetkeseis · Õigusakt.

Nothing between them and nothing after them but the file, the dates, the person
and the optional next step. Package 1's decisions are preserved in full:
manual-first with the reader withdrawn, a quiet searchable Saatja, Valdkond as a
`<details>` whose summary carries the answer, ordinary file staging, and ADR
0087's `Sarnased teemad`. The controls are the repository's own server-rendered
chips; the only scripting this round touches is the `Muu` reveal, which now
listens to several chips instead of one and still degrades to what the server
renders.

Deliberately **not** here: procedural links, initial next-action automation, and
per-instrument timeline templates. Each belongs to a package that owns it.

## Consequences

**Search, reporting and filters.** No stored projection changes. The Matter
search row is built from titles, identifiers, organisation/area/tag names and
the authored summaries — it carries neither a stage label nor an instrument
label — so `INDEX_VERSION` and `ARCHIVE_INDEX_VERSION` are untouched and **no
reindex is required**. The register resolves `?hetkeseis=` and `?valdkond=`
against the whole table rather than the active rows, so a bookmarked filter for
a withdrawn `Õigusakt` or a reworded stage still works; reporting's stage filter
resolves by key and renders the current label, which is the intended behaviour
of a reword. No historical category becomes *Teadmata*.

**The process strip is unaffected.** It draws `Alustatud`, `Tagasiside tähtaeg`,
`Koja arvamus`, `Arvamuse tähtaeg`, `Jõustumine` and `Lõpetatud` from canonical
dated records and does not read `StageVocabulary` at all (ADR 0074 §12.1). A
Matter in any stage — new, reworded or retired — renders exactly as it did, and
no milestone is invented for one. Per-instrument timeline templates are a later
package and nothing here anticipates them.

**Migrations.** Two, both `RunPython`, one leaf per app:
`workflow/0007_lawyer_reviewed_stage_vocabulary` (three labels and one flag) and
`taxonomy/0008_lawyer_reviewed_legal_instruments` (on top of Package 1's
`taxonomy/0007`). No schema change, no backfill, no row created in `workflow`,
no Matter reclassified. Both fail closed on a row somebody has renamed since
review, and both hold a frozen copy of the manifest that a test holds to the
manifest.

Neither reverse deletes a classification, and they reach that differently.

`workflow/0007` has nothing to reverse but three labels and a flag, and it
reads no other app in either direction. An earlier draft did — it asked whether
any `Matter` stood in a stage it wanted to delete — and CI proved why a data
migration may not: a reverse runs against whatever historical state the *other*
app happens to be rewound to, and `migrate <app> zero` rewinds `matters` past
this migration's own state before it gets here, producing
`Cannot query "StageVocabulary object": Must be "StageVocabulary" instance`.

`taxonomy/0008` does ask, because it can: it depends on
`matters/0015_matter_legal_instruments`, which is what puts its reverse *before*
any `matters` rewind, exactly as `taxonomy/0006` has done since it was written.
A new type a Matter already carries is deactivated; a pristine unreferenced one
is removed. What it cannot restore is an activation state that was already false
before it ran — every one of the twelve is active today, seeded that way by
`taxonomy/0006` and never since changed, so it is exact for the database this
migrates and would be wrong for a deployment that had deactivated one by hand.

**What this does not do.** It does not delete a vocabulary row, rewrite a
historical classification, remap a Matter, infer a `Menetlusliik` from anything,
merge Saatja with Adressaat, model a disposition as a stage, or change what any
stored value means.
