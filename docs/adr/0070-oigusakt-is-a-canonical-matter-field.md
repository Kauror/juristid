# ADR 0070 — Õigusakt is a canonical Matter field, and it is not Menetlusliik

- **Status:** accepted
- **Date:** 2026-09-10
- **Supersedes:** the `authority = "deferred"` decision recorded for the
  `ÕIGUSAKT` column in every era contract `excel-era-2011.toml` …
  `excel-era-2026.toml` (contract schema 1.0)
- **Design:** [`docs/oigusakt-uus-teema-design.md`](../oigusakt-uus-teema-design.md)

## Context

The historical register — `Tööd eelnõudega.xlsx`, one sheet per year from 2011
to 2026 — has always carried a column called `ÕIGUSAKT` in column C. It answers
*what kind of legal or source instrument does this file concern*: `seadus`,
`määrus`, `direktiiv`, `VTK`, `EL määrus`, `muu`, and in the earliest years the
one-letter codes `S`, `M`, `D`.

When the era contracts were reviewed in Stage 2A the column was read and stored
verbatim into `legal_instrument_raw` and marked `authority = "deferred"`, with
the note *«Kanoonilist välja ei ole … Nende ühendamine on juristide sisuline
otsus, mitte parseri oma»*. That was the correct decision at the time and this
ADR does not rewrite it: nobody had said where the value belonged, and a parser
that had guessed would have produced plausible, wrong history.

The department has now decided. `ÕIGUSAKT` is a first-class classification of a
Matter, answered on `Uus teema` alongside Valdkonnad, Hetkeseis and
Menetlusliik.

## Decision

### 1. `Õigusakt` is canonical, and it is not `Menetlusliik`

`Matter.track` (*Menetlusliik*) says what kind of **procedure** a Matter belongs
to. `Matter.legal_instruments` (*Õigusakt*) says what kind of **instrument** it
is about. They are two facts and neither is derivable from the other:

| Menetlusliik | Õigusakt |
| --- | --- |
| `ELi õiguse ülevõtmine` | `Seadus` |
| `ELi algatus` | `EL määrus` |
| `Riigisisene` | `Määrus` |

A control that filtered one by the other, or a report that treated `ELi algatus`
as implying an EU instrument, would be asserting a relationship the data does
not have. `Track` keeps its seven values, its column, its service
(`change_track`) and its own change event, and nothing about it changes here.

### 2. It is multi-select

`Matter.legal_instruments` is a many-to-many to `taxonomy.LegalInstrumentType`.
0..N, blank valid, only `Pealkiri` ever required.

This is what the source says. The register writes combined answers five
different ways — `S, M`, `D, M`, `direktiiv ja määrus`, `direktiiv+määrus`,
`direktiiv/määrus` — and a single-valued field would have to either concatenate
them into a pseudo-value nothing can query or throw one of the two away. The
domain is genuinely plural: a package that amends an Act and a Regulation
together is ordinary work.

The control follows the model, as ADR 0025 requires: checkbox chips, a
`field__count` beside the legend, a `×` on each chosen chip. Menetlusliik
directly above has none of the three because it holds one value, and that
asymmetry is what keeps the two rows from reading as one question split in two.

### 3. A governed reference vocabulary, not a tag and not free text

`LegalInstrumentType` is the third governed vocabulary beside `PolicyArea` and
`Tag`, and it is the one `app/taxonomy/models.py` was holding a place for —
*legal instrument* is named in that module's opening rule as something neither
of the other two may encode.

Rejected alternatives, and why:

- **`Matter.track`** — a different fact (§1).
- **`Tag`** — tags are free subject vocabulary with aliases and merging, and are
  forbidden by the master specification from encoding legal instrument.
- **`PolicyArea`** — Valdkond is *which area of law*; this is *what kind of
  instrument*. Merging them would file a tax Act under two incomparable axes.
- **A comma-separated `CharField`** — that is the spreadsheet, restated. It
  cannot be counted, filtered or renamed, and it is exactly the shape the
  canonicalisation exists to escape.
- **User-created rows** — spelling would create categories. Fifty-eight
  spellings in the source resolve to seventeen concepts, and the whole value of
  the field is that they do.

### 4. The vocabulary is seventeen values, derived from the source

A read-only survey of all sixteen year sheets found **2418 non-empty `ÕIGUSAKT`
cells in 57 distinct spellings**, plus one further spelling (`EK
konsultatsioon`) present only in the department's newer working copies — **58 in
total**. Collapsing case, whitespace, diacritic and abbreviation variants leaves
sixteen concepts and `Muu`.

The full table, every alias and the reasoning per concept are in
`app/taxonomy/legal_instruments.py`. Two of its judgements are worth naming
here because they are not mechanical:

**EU-ness is part of the instrument only where the instrument is a distinct EU
act.** A Regulation and a Directive have no domestic equivalent, so `EL määrus`
and `Direktiiv` are their own concepts and an EU Regulation is never filed as
the Estonian ministerial `Määrus` beside it. A strategy and a consultation are
the same kind of document whoever runs them, so `EL strateegia` is `Strateegia`
and `EL konsultatsioon` is `Konsultatsioon`. Whether the *procedure* is European
is already an answer this product holds, and it is Menetlusliik's.

**`EK` is read as `EL`.** A Commission regulation is an EU regulation. That is a
reading of what the words say, not a merge of two departmental categories.

### 5. Raw source survives, separately and visibly

`CurrentRegisterState.legal_instrument_raw` is unchanged and stays what it has
always been: exactly what the spreadsheet cell said. `Matter.legal_instruments`
is the reviewed canonical interpretation. Both may exist for one record, neither
replaces the other, and nothing in this change mutates historic source evidence
in order to normalise it.

`Matter.legal_instrument_other` is a third thing again and is not a rename of
either: it is what a person typed beside a `Muu` chip.

### 6. One mapping seam

`app.taxonomy.legal_instruments.canonical_legal_instrument_keys(raw)` is the
only place a raw spelling becomes canonical classification. It splits combined
values first (`,` `+` `/` `;` and the word `ja`), normalises each part, and
looks it up in the reviewed alias table. An unrecognised part leaves the **whole
value** unmapped rather than partially mapped: half of a combined answer is a
worse record than none, because nothing downstream could tell it was half.

Scattering this across the importer, the forms and the services is how the same
cell starts meaning two different things depending on which door it came
through.

### 7. Two historic values are deliberately left unmapped

`Muu` is an answer — it says *the kind is some other kind*. Two of the 58
spellings do not say that, and recording them as `Muu` would put a decision in
the department's mouth that the department did not make:

| Raw | Rows | Why unmapped |
| --- | ---: | --- |
| `EL` | 6 | Names a scope, not an instrument. Which EU act it was is not recoverable from the row. |
| `sisendi küsimine VTK ettevalmistamiseks` | 1 | Names an activity: asking for input is not a VTK, it is the step before one. |

Both keep their raw value and get no canonical classification. Seven of 2418
cells, 0.29%. They are listed as data in `UNMAPPABLE_RAW_VALUES` and asserted by
test, so a later reviewer sees them rather than rediscovering them.

### 8. `Muu` is a vocabulary row here, unlike Valdkonnad's

This is the one place the field deliberately differs from the Valdkonnad control
it otherwise copies. `Matter.policy_area_other` is revealed by a checkbox that
is *not* a `PolicyArea`, because no historical record ever needed «some other
area» to be storable. This column does: **1130 of the 2418 historical cells say
exactly `muu`**, and a vocabulary with nowhere to put them would either lose the
department's own answer or invent a category it never chose.

So `muu` is a `LegalInstrumentType`, it is last in the row, and ticking it
reveals `Õigusakti liik` — free text, required while `Muu` is ticked, cleared
when it is not, and creating no vocabulary row ever.

### 9. Era contracts say `mapped`, which is a new and honest level

The contracts could not stay `deferred` — the decision has been made — and could
not become `authoritative`, because that means *the importer writes it to a
canonical field* and the importer does not. `derived` is wrong too: the home is
a Matter field a person edits, not a projection rebuilt from a snapshot.

So `AUTHORITY_LEVELS` gains **`mapped`**: *a real field with a reviewed
canonical home and a reviewed reading of every historical spelling, which the
importer still does not write*. It is deliberately absent from
`ColumnContract.is_written_to_canonical_model`, and `CONTRACT_SCHEMA_VERSION`
moves 1.0 → 1.1 so that a row imported under the old rules can still be traced
back to them.

### 10. No historical data is populated by this change

No backfill, no import, no register refresh, no production access. The reusable
reading exists and is tested; applying it is a separate reviewed operation.

The reason it is separate is a question nobody has answered: **may a spreadsheet
cell overwrite a classification a lawyer chose?** The recurring current-register
refresh (`refresh_matter_from_register`) takes an explicit keyword per field and
`legal_instruments` is deliberately not among them, so nothing picks this up by
accident.

## Consequences

- Four classification rows on `Uus teema` instead of three; one hairline and one
  wrapped chip row of added height. No existing row moved, no new CSS.
- `Matter` gains one relation, one `CharField`, and two change-event types that
  mirror the policy-area pair. Neither appears in the timeline, for the same
  reason those two do not: correcting a filing is data management, not authored
  chronology.
- Downstream surfaces that will later need awareness of
  `Matter.legal_instruments` — none of them touched here, all listed so that the
  omission reads as a decision: Teemad / Täpsem otsing filters, the search index
  (`app/search/indexing.py` — `INDEX_VERSION` is unchanged and this field is not
  indexed), Statistika, the reporting snapshot, the Matter detail page and its
  inline `MatterFieldForm` controls, the DashKoda export contract, and the
  register cutover/delta reports.

## Alternatives considered and rejected

- **Extending `Track` with instrument values.** Would make two independent facts
  mutually exclusive and silently reclassify a decade of filing.
- **Single-select with a «several» value.** A pseudo-value nothing can count.
- **Deriving the vocabulary order from usage counts.** Rejected for the reason
  Valdkonnad rejected it (`app/taxonomy/vocabulary.py`): an order computed from
  records rearranges itself under the reader and is a channel through which the
  contents of the register can be inferred from a checkbox list.
- **Mapping the two unmappable values to `Muu` to reach 100% coverage.** A tidy
  number bought with a fabricated decision.
