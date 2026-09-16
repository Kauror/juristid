# ADR 0089 — One question per classification: the reviewed Hetkeseis and Õigusakt vocabularies, and the three questions `Uus teema` asks

- Status: accepted
- Date: 2026-09-17
- Stage: pre-QA (shared-gate development phase)
- Related: ADR 0070 (`Õigusakt` is a governed vocabulary of its own, and it is
  not `Menetlusliik` — **narrowed here, reversed nowhere**), ADR 0088 (`Uus
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
| 10 | Rohkem ei tegele | `no_further_work` | **new** |
| 11 | Muu | `other` | *unchanged* |

**Three rewordings, one addition, no retirement and no remap.** Every key, row,
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

#### `Rohkem ei tegele` does not close anything either

This is the one genuinely new value, and it is the one a reader could reasonably
take for a closure. It is not.

Stage, disposition and next action are separate concepts (AGENTS.md, master
specification 3.4). The stage says where the *external* process stands and what
this office's attention is on; `Disposition` says why the Matter is **closed**;
`Lõpeta teema` is what closes it. Choosing this stage records the first and
writes none of the others: no `Disposition`, no `close_matter`, no archive, no
audit event beyond the ordinary `MATTER_STAGE_CHANGED`. The Matter stays open,
stays on every work surface it was on, and is closed when somebody closes it.

The stage carries that sentence in its own `help_text`, which is what the
tooltip on `Uus teema` renders.

**The historical reading of the workbook is untouched.** `rohkem pole tegevusi
plaanis` has been read as the `MONITORING_STOPPED` *disposition* since
`workflow/0004`, and it stays that way. A new stage with neighbouring words is a
different claim, and re-pointing the historical mapping at it — or moving the
Matters that carry the disposition onto the stage — would rewrite a decade of
somebody else's filing on a coincidence of wording.

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

### 4 — Domestic or EU is *derived*, and `Menetlusliik` leaves `Uus teema`

The reviewed list names the group in the label: six types say *siseriiklik*,
four say *ELi*. So the high-level classification is read off the answer instead
of asked beside it.

`app.matters.services.derived_track` maps the chosen types to `Track.DOMESTIC`
or `Track.EU_INITIATIVE`, and it refuses three ways:

1. **every chosen type must be in one of the two groups.** The twelve withdrawn
   version-1.0 types are in neither, on purpose — `Konsultatsioon` may be
   European or domestic and `Eelnõu` says nothing either way;
2. **they must agree.** `Õigusakt` is a multi-select and a file really can
   concern a directive and the Estonian act transposing it. That is two answers
   about two instruments, not one about the procedure, so the deterministic
   reading of a mixed set is *no answer*;
3. **nothing chosen is nothing derived.**

It is a pure function called from the create view, deliberately **not** inside
`create_matter`: the legacy importer and the seeding commands go through that
service too, and a rule about what the create form means must not reach a decade
of historical rows.

**`NATIONAL_TRANSPOSITION` is never derived.** A `Seadus` implementing a
directive is a domestic legal instrument. Whether the *procedure* is a
transposition is a separate fact that no instrument type entails — which is the
whole of why ADR 0070 keeps the two apart — and inferring it from a domestic act
whose policy context happens to be European would write a classification nobody
reviewed. It stays a value somebody chooses. This is the same refusal ADR 0088
recorded when it withdrew the `ELi õiguse ülevõtmine` *Valdkond* without
inferring the track from it.

**`KODA_INITIATIVE`, `STRATEGY`, `IMPLEMENTATION` and `OTHER` are never derived
either**, and this is the mismatch worth stating rather than hiding. `Track`'s
seven values are not a clean domestic/EU axis: two of them are that axis and
five are other distinctions that happen to share the column. `Koja ettepanek või
pöördumine` is arguably `KODA_INITIATIVE` and `Strateegia, arengukava või
tegevuskava` is arguably `STRATEGY` — but both are also unambiguously domestic,
and the distinction the lawyers asked to keep is the domestic/EU one. Deriving
the more specific value would mean a Koda proposal no longer reads as domestic,
which is the opposite of the requirement. So the derivation answers exactly the
question it was asked, the other five values stay reachable on `Muuda teemat`
and the Teema rail, and no stored value is falsified to make a UI simpler.

**`Matter.track` itself is untouched.** The column, the seven values,
`StageVocabulary.applicable_tracks`, the register's `?menetlusliik=` filter, the
`MATTERS_BY_TRACK` metric, `change_track`, the `MATTER_TRACK_CHANGED` audit
event, `MatterEditForm`, `MatterFieldForm` and the Teema rail's inline editor
all stand. What is gone is the question on the capture screen — and the *field*,
so a forged POST carrying `track=NATIONAL_TRANSPOSITION` is not part of the
request as far as `MatterCreateForm` is concerned.

**Edits derive nothing.** A Matter created before this round, or one whose track
somebody chose by hand, keeps it — changing `Õigusakt` on `Muuda teemat` does not
re-derive and does not overwrite. The alternative is a field that silently
rewrites itself under an edit about something else, which is exactly the defect
PR #231 fixed for `Hetkeseis`. `Menetlusliik` is still a visible control on the
edit page, so a lawyer who wants a different value sets it there.

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
`workflow/0007_lawyer_reviewed_stage_vocabulary` and
`taxonomy/0008_lawyer_reviewed_legal_instruments` (on top of Package 1's
`taxonomy/0007`). No schema change, no backfill, no Matter reclassified. Both
fail closed on a row somebody has renamed since review, and both hold a frozen
copy of the manifest that a test holds to the manifest.

Both reverses are honest about their limits and neither deletes a
classification: a new row that a Matter already carries is deactivated rather
than removed. What `taxonomy/0008`'s reverse cannot restore is an activation
state that was already false before it ran — every one of the twelve is active
today, seeded that way by `taxonomy/0006` and never since changed, so it is
exact for the database this migrates and would be wrong for a deployment that
had deactivated one by hand.

**What this does not do.** It does not delete a vocabulary row, rewrite a
historical classification, remap a Matter, infer a track from a domestic act,
merge Saatja with Adressaat, close a Matter from a stage, or change what any
stored value means.
