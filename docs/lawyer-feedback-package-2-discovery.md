# Lawyer feedback — Package 2 discovery report

**Classification, intake semantics and initial action. Discovery and design only.**

- Inspected: `origin/main` at **`4171597e5f3d51bdde3c891b0aa71fda6f95e020`**
  ("Merge pull request #228 from Kauror/feature/similar-matters").
  The working tree was clean and identical to that commit throughout.
- PR #230 (Package 1) was read **for context only**. Nothing in it was
  altered, and nothing below depends on its unmerged code.
- **Nothing was implemented.** No source file, migration, template, test or
  ADR was added or changed. The only new file in this branch is this report.

---

## 1. What was inspected, and how

Read end to end: `app/workflow/` (models, enums, selectors, services,
vocabulary, all six migrations), `app/taxonomy/` (models, `legal_instruments.py`,
`vocabulary.py`, all six migrations), `app/matters/` (models, services, forms,
views, `work_items.py`, `process_timeline.py`, `register_filters.py`,
`register_dates.py`, `intake.py`), `app/submissions/models.py`,
`app/reporting/` (models, selectors, filters, context, exports,
metric catalogue), `app/search/indexing.py`, `app/legacy_import/`
(contracts, resolution, register_refresh, current_state, final_cutover),
`templates/matters/matter_create.html`, `templates/matters/partials/header.html`,
`templates/matters/partials/rail.html`, and ADRs 0011, 0025, 0032, 0050, 0052,
0054, 0063, 0067, 0069, 0070, 0074, 0079, 0086, plus 0088 on PR #230's head.

Two claims in this report were **verified by running code** against a local
PostgreSQL, in throwaway probe tests that were deleted immediately afterwards
(the tree is clean):

1. a `StageVocabulary` row marked `is_active=False` is **not** safely retired
   today — §3.6;
2. an open `NextAction` **does** displace `Arvamuse tähtaeg` from every work
   surface — §6.3.

---

## 2. PR #230 state observed (context only)

| | |
| --- | --- |
| Number / title | #230 — *Uus teema is manual-first: withdraw the document reading, empty Saatja, fold Valdkond* |
| State | `open`, not draft, not merged, `mergeable_state: blocked` |
| Head / base | `6783d2d66f18e1132965bb80b9a83120710ac042` on `claude/juristid-uus-teema-usability-9hcfdw` → `main` @ `4171597` |
| Size | 36 files, +1855 / −201, 2 commits |
| Blocker | two visual baselines (`uus-teema`, `uus-teema-viga`) need adopting from CI bytes |
| Data impact | one data migration `taxonomy/0007` — `is_active=False` on `Koalitsioonilepped` and `ELi õiguse ülevõtmine` (Valdkonnad). No schema, no backfill, no reindex |
| New ADR | 0088, which records discovery notes on Packages 2–5 |

**One correction to ADR 0088, and it matters for Package 2.** ADR 0088 §"What
this round deliberately did not do" states:

> **Hetkeseis.** `StageVocabulary.is_active` is the same safe retirement
> mechanism `PolicyArea` has […]

It is not. See §3.6 — this is the single hard prerequisite for Package 2.

A second, smaller correction: ADR 0088 and PR #230's body say `Matter.track`
is referenced "by imports". It is not. `app/legacy_import/` contains **zero**
references to `track`, and the metric catalogue says so in the product's own
words: *"Register ei sisaldanud menetlusliiki; see täidetakse selles
süsteemis."* The rest of that paragraph's dependency list is accurate.

---

## 3. Hetkeseis — current model and vocabulary

### 3.1 The model

`app/workflow/models.py::StageVocabulary` (`BaseModel`, UUID pk):

| field | type | note |
| --- | --- | --- |
| `key` | `SlugField(64, unique)` | the stable identity; the query-string filter value |
| `label_et` | `CharField(200)` | what a lawyer reads |
| `help_text` | `TextField(blank)` | the Uus teema tooltip; written by `workflow/0006` |
| `is_active` | `Boolean(default True)` | intended retirement switch — see §3.6 |
| `sort_order` | `PositiveSmallInt(default 100)` | reviewed sequence, not a ranking |
| `applicable_tracks` | `ArrayField(CharField(choices=Track))` | **seeded empty on every row, and `applies_to()` is called from nowhere in the codebase.** Dead weight today |
| `is_provisional` | `Boolean(default False)` | **True on all ten seeded rows.** Nothing but `/admin` reads it |

`Meta.ordering = ["sort_order", "label_et"]`.

### 3.2 The ten seeded values

Seeded by `workflow/0004_seed_stage_vocabulary`, help text replaced by
`workflow/0006_stage_help_from_the_department` (the department's own wording,
supplied 2026-08-25, transcribed sentence for sentence).

| # | `key` | `label_et` | `sort_order` | raw workbook label it maps from |
| --- | --- | --- | --- | --- |
| 1 | `idea` | Idee | 10 | `idee` |
| 2 | `consultation` | Kooskõlastusringil | 20 | `kooskõlastusringil` |
| 3 | `government` | Valitsuses | 30 | `valitsuses` |
| 4 | `parliament` | Riigikogus | 40 | `Riigikogus` |
| 5 | `awaiting_entry` | **Ootan jõustumist** | 50 | `ootan jõustumist` |
| 6 | `in_force` | Jõustunud | 60 | `jõustunud` |
| 7 | `estonian_eu_position` | **Eesti seisukoht** | 70 | `Eesti seisukoht` |
| 8 | `eu_procedure` | ELi menetluses | 80 | `ELi menetluses` |
| 9 | `awaiting_transposition` | **Ootan ELi õiguse ülevõtmist** | 90 | `ootan ELi õiguse ülevõtmist` |
| 10 | `other` | Muu | 100 | `muu` |

The **eleventh** raw workbook value, `rohkem pole tegevusi plaanis`, is
deliberately *not* a stage. `workflow/0004` seeds it as a
`LegacyStatusMapping` whose `disposition` is `MONITORING_STOPPED`, with the
note *"Ei ole menetlusetapp: kirjeldab Koja töö lõpetamist, mitte välise
menetluse seisu."* This is the most load-bearing fact in this section.

### 3.3 Everything that stores or reads a stage

| consumer | how it refers to a stage | effect of a **label** change | effect of a **key** change |
| --- | --- | --- | --- |
| `Matter.stage` | FK, `on_delete=PROTECT`, nullable | none | none (FK is by pk) |
| `OperationalSnapshot.stage` + `.stage_key` + `.stage_label` | FK **plus frozen key and label text** | none — historical snapshots keep the label the reader saw | breaks nothing; `stage_key` is a frozen copy |
| `LegacyStatusMapping.stage` | FK per raw workbook label, per era | none | none |
| `app/workflow/vocabulary.py::RAW_LABEL_TO_STAGE` | raw workbook label → **key** | none | **breaks** — pinned by `tests/test_workflow_vocabulary.py` against `workflow/0004`'s frozen copy |
| `register_filters.py` `?hetkeseis=` | `stage__key=` (any row, active or not) | none | breaks bookmarks |
| `reporting/context.py` `PARAM_STAGE="hetkeseis"` | `stage__key=` | none | breaks bookmarks |
| `reporting/selectors/matters.py::matters_by_stage` | groups on `stage__key`/`stage__label_et`/`stage__sort_order` | chart labels change | drill-through URLs change |
| `reporting/selectors/portfolio.py::active_full_matters_by_stage` | same | same | same |
| `reporting/selectors/activity.py::active_without_stage` | `stage__isnull=True` | none | none |
| `reporting/filters.py` | offers **active** rows; resolves a pill label from **any** key | pill text changes | none |
| `reporting/exports.py` | CSV column `hetkeseis` = `stage.label_et` | CSV text changes | none |
| `search/views.py::_suggestion_context` | prints `stage.label_et` | dropdown text changes | none |
| `search/indexing.py` | **does not index stage at all** | none | none — **no reindex, `INDEX_VERSION` untouched** |
| `legacy_import/register_refresh.py` | resolves the raw label to a `StageVocabulary` row and **writes `Matter.stage`** | none | none |
| `legacy_import/resolution.py::resolve_status` | `LegacyStatusMapping` lookup; **never creates a row** | none | none |
| `matters/process_timeline.py` | **does not read `Hetkeseis` at all** (ADR 0074 §12.1 retired it from the strip) | none | none |
| `matters/services.py::change_stage` | records `MATTER_STAGE_CHANGED` with `from_label`/`to_label` **frozen at event time** | history keeps old wording — correct | none |
| workflow behaviour keyed on a particular stage | **none exists.** Grepping every seeded key finds only `seed_dev_data` | — | — |

**Conclusion:** stage *keys* are load-bearing; stage *labels* are not stored
anywhere that a rename would corrupt. A pure relabelling of the existing ten
rows is the cheapest change in this whole package.

### 3.4 Proposed Hetkeseis mapping

| # | proposed visible value | class | acts on | action |
| --- | --- | --- | --- | --- |
| 1 | Idee | **A** | `idea` | no change |
| 2 | Kooskõlastusringil | **A** | `consultation` | no change |
| 3 | Valitsuses | **A** | `government` | no change |
| 4 | Riigikogus | **A** | `parliament` | no change |
| 5 | Jõustumise ootel | **A** | `awaiting_entry` | label only: *Ootan jõustumist* → *Jõustumise ootel* |
| 6 | Jõustunud | **A** | `in_force` | no change |
| 7 | Eesti seisukoht koostamisel | **A** | `estonian_eu_position` | label only: *Eesti seisukoht* → *Eesti seisukoht koostamisel* |
| 8 | ELi menetluses | **A** | `eu_procedure` | no change |
| 9 | ELi õiguse ülevõtmise ootel | **A** | `awaiting_transposition` | label only: *Ootan ELi õiguse ülevõtmist* → *ELi õiguse ülevõtmise ootel* |
| 10 | **Rohkem ei tegele** | **not a stage** | `Disposition.MONITORING_STOPPED` | see §3.5 — this is a closure, and it already exists |
| 11 | Muu | **A** | `other` | no change |

**Ten of the eleven proposed values are label-only adjustments to rows that
already exist, in the order they already have.** No new vocabulary value, no
consolidation, no migration of any `Matter`. The three renamed labels are the
same three concepts written in the impersonal voice the other seven already
use — *Ootan …* was the odd one out, and the lawyer is asking for consistency,
not for new meaning.

Two caveats on the renames:

- `LegacyStatusMapping.raw_label` holds the **workbook's** strings
  (`ootan jõustumist`, …), not `label_et`, so imports are untouched. But if
  the department starts writing *Jõustumise ootel* in the workbook, a new
  era-aware `LegacyStatusMapping` row is needed. Flagged, not needed now.
- `workflow/0006`'s `adopt()` matches on `help_text`; a new migration must
  follow the same **fail-closed** shape — leave alone any row a person has
  since edited.

### 3.5 "Rohkem ei tegele" — the recommended contract

This is the one place where implementing the lawyer's list literally would
undo a decision the product has already made, twice.

**The evidence.**

- `workflow/0004` seeds `rohkem pole tegevusi plaanis` as a **disposition**
  (`MONITORING_STOPPED`), not a stage, with the reason written into the
  migration.
- `docs/open-decisions.md` line 19 records this as *decided and deployed*.
- `AGENTS.md` product principles: *"Stage, disposition and next action are
  separate concepts."*
- The closure UI already offers it. `CLOSURE_CHOICES` has
  `MONITORING_STOPPED` → *"Koda ei tegele edasi"*, and the compact composer's
  three chips (`COMPOSER_CLOSURE_CHOICES`) label it **"Loobuti"**.
- `close_matter()` sets `is_open=False`, `disposition`, `closed_at`,
  `closed_by`, and calls `end_open_action_for_closure()` — which cancels the
  open `NextAction`. Closure is the only thing that ends live work.

**Recommendation — do not add it to Hetkeseis. Relabel the closure chip.**

1. `Rohkem ei tegele` is **not** offered as a `StageVocabulary` row.
2. `COMPOSER_CLOSURE_CHOICES`'s `MONITORING_STOPPED` chip is relabelled from
   *Loobuti* to **`Rohkem ei tegele`** — the lawyer's own words, on the
   control that already does exactly what they mean. One constant in
   `app/matters/forms.py`. **No migration**: the stored value, the enum, the
   register mapping, the statistics and every closed Matter are untouched.
   `CLOSURE_CHOICES`'s longer label stays as the register-facing wording.
3. Where the lawyer looks for it — the Hetkeseis chip row on `Uus teema` and
   on the Teema header — the row gains a quiet pointer:
   *"Kui Koda enam ei tegele, lõpeta teema (« Rohkem ei tegele »)."*
   A sentence, not a twelfth chip.

**Why this is the clean contract.** It is the only design in which the
inconsistent state named in the brief is *unreachable by construction*:

> Hetkeseis = Rohkem ei tegele, but the Matter remains operationally open
> with active work and deadlines

cannot happen, because the answer is not a stage at all. The two alternatives
both fail:

- *a stage with a hidden side effect that closes the Matter* — a label that
  silently closes a file is the "hidden side effect" the brief rules out, and
  it would also silently cancel the open `NextAction` from a control that says
  nothing about doing so;
- *a stage with no side effect* — that is exactly the inconsistent state.

### 3.6 `Hetkeseis = Jõustunud` — separate from closure. Confirmed.

The repository is explicit and consistent, in four places:

- `workflow/0004` help text: *"Akt on jõustunud. See ei tähenda, et Koja töö
  on lõppenud — rakendamise jälgimine on tavaline töö ja teema sulgemine on
  eraldi otsus."*
- `services.change_stage` docstring: *"`jõustunud` means the act entered into
  force, not that the file is closed."*
- `Disposition.COMPLETED` is separately labelled *"Lõpetatud või jõustunud"* —
  closure has its own way of saying it.
- No code branches on `in_force`.

**Default assumption holds. No change, and nothing in Package 2 may couple
them.**

### 3.7 ⚠ Prerequisite: retiring a stage is *not* safe today

**Verified by running code.** With a `StageVocabulary` row flipped to
`is_active=False` while a Matter still points at it:

```
RETIRED STAGE OFFERED ON EDIT FORM:  False
BOUND VALID WITH RETIRED STAGE:      False
  → "Valige korrektne väärtus. Valitud väärtus ei ole valitav."
VALID WITHOUT STAGE:                 True  → cleaned stage: None
```

Three surfaces narrow the *validating queryset*, not only the rendered list:

| surface | line | what it does |
| --- | --- | --- |
| `MatterEditForm.__init__` | `app/matters/forms.py:1648` | `set_choices(self, "stage", active_stages())` |
| `MatterFieldForm.__init__` | `app/matters/forms.py:3452` | same |
| `_header_context` | `app/matters/views.py:2558` | `"stages": StageVocabulary.objects.filter(is_active=True)` |

Consequence: on a Matter carrying a retired stage, opening *Muuda teemat* and
saving **any unrelated field** — a typo in the title — silently clears
`Matter.stage` to `NULL` via `change_stage(matter, stage=None)`. The header's
inline `<select>` has the same shape.

`PolicyArea` and `LegalInstrumentType` do **not** have this defect: both union
the Matter's own held rows into the offered list and validate against the whole
table (`forms.py:1737-1741` and `1748-1753`).

**This is a bug on `main`, not a Package 2 feature.** It is latent today only
because no stage has ever been retired. It must be fixed **before** any
`is_active=False` is written to a stage row. The fix is three lines shaped
exactly like the Valdkond/Õigusakt ones, plus a `retired_stage_id` marker for
the template so a retired chip reads as *varasem hetkeseis*.

The good news for Package 2: the recommended Hetkeseis change is
**label-only and retires nothing**, so it does not depend on this fix. The
Õigusakt change (§4) does.

---

## 4. Õigusakt — current model and vocabulary

### 4.1 Verification of the previous discovery

| claim | verdict | evidence |
| --- | --- | --- |
| `LegalInstrumentType` has `is_active` and `sort_order` | ✅ | `app/taxonomy/models.py:66-69` |
| `Matter.legal_instruments` is multi-select | ✅ | `ManyToManyField`, `blank=True`, `app/matters/models.py:244` |
| historical values can be retained | ✅ | `MatterEditForm` and the register both keep them; `is_active` only narrows what is *offered* |
| `legal_instrument_raw` is immutable import provenance | ✅ | `CurrentRegisterState.legal_instrument_raw`, never written from the application; `contracts.py` marks the column **`mapped`** — a reviewed reading exists and the importer deliberately does not apply it |

One addition the earlier note did not carry: `Matter.legal_instrument_other`
is a **third** field — one Matter's own free text, revealed by the real `Muu`
vocabulary row, never taxonomy. It is distinct from both the vocabulary and
from `legal_instrument_raw`.

### 4.2 The seventeen current values

Source: a read-only survey of `Tööd eelnõudega.xlsx`, column `ÕIGUSAKT`, sheets
2011–2026 — **2418 non-empty cells in 58 distinct spellings**, collapsed to
sixteen concepts and a `Muu`. Seeded by `taxonomy/0006`, manifest in
`app/taxonomy/legal_instruments.py` (`REFERENCE_LEGAL_INSTRUMENT_VERSION = "1.0"`).

| `key` | `label_et` | sort | reviewed aliases |
| --- | --- | --- | --- |
| `seadus` | Seadus | 10 | seadus, S |
| `maarus` | Määrus | 20 | määrus, määrused, M |
| `vtk` | VTK | 30 | VTK |
| `eelnou` | Eelnõu | 40 | eelnõu |
| `direktiiv` | Direktiiv | 50 | direktiiv, D, EL direktiiv, ELi direktiiv, … |
| `el-maarus` | EL määrus | 60 | EL määrus, ELi määrus, EK määrus, EL M |
| `el-teatis` | EL teatis | 70 | EL teatis, EK teatis |
| `konsultatsioon` | Konsultatsioon | 80 | EL konsultatsioon, ELi konsultatsioon, EK konsultatsioon, avalik konsultatsioon |
| `strateegia` | Strateegia | 90 | strateegia, EL strateegia, EK strateegia |
| `arengukava` | Arengukava | 100 | arengukava |
| `tegevuskava` | Tegevuskava | 110 | tegevuskava |
| `visioon` | Visioon | 120 | visioon |
| `korraldus` | Korraldus | 130 | korraldus, VV korraldus |
| `kaskkiri` | Käskkiri | 140 | käskkiri |
| `ettepanek` | Ettepanek | 150 | ettepanek |
| `kusitlus` | Küsitlus | 160 | küsitlus |
| `muu` | Muu | 200 | muu — **1130 of the 2418 historical cells** |

### 4.3 Everything that reads Õigusakt

Far fewer consumers than Hetkeseis:

- `Matter.legal_instruments` (M2M) and `Matter.legal_instrument_other` (text);
- `MatterCreateForm` / `MatterEditForm` via `LegalInstrumentChoicesMixin`;
- `services.set_legal_instruments` / `set_legal_instrument_other` (audited);
- `taxonomy/vocabulary.py::selectable_legal_instrument_types` — the single read;
- `matters/intake_suggestions/` — keyed by `LegalInstrumentType.key`
  (withdrawn from `Uus teema` by PR #230; still live on `Muuda teemat`);
- `legacy_import` — **raw only**, never written to the canonical model;
- **no register filter, no reporting metric, no export column, no search
  index entry, no timeline use.**

That last line is what makes the Õigusakt vocabulary the cheapest thing in
Package 2 to change, and the one place where a new value costs nothing
downstream.

### 4.4 Proposed Õigusakt mapping

`→` action key: **KEEP** / **RENAME** (label only, same key, same meaning) /
**NEW** / **RETIRE** (`is_active=False`, offered no more, everything preserved).

#### Domestic family

| proposed visible value | current value | action | historical behaviour | import behaviour |
| --- | --- | --- | --- | --- |
| VTK | `vtk` "VTK" | **KEEP** | unchanged | `VTK`, `VTK eelnõu` still read |
| Seadus | `seadus` "Seadus" | **KEEP** | unchanged | `seadus`, `S` still read |
| Määrus | `maarus` "Määrus" | **KEEP** | unchanged | `määrus`, `M` still read |
| Koja ettepanek või pöördumine | `ettepanek` "Ettepanek" | **NEW** `koja-ettepanek` + **RETIRE** `ettepanek` | every Matter on `ettepanek` keeps it, renders it, reports it | raw `ettepanek` still reads to `ettepanek`, **not** to the new row |
| Strateegia, arengukava või tegevuskava | `strateegia`, `arengukava`, `tegevuskava` | **see §4.5** | — | — |
| Muu siseriiklik | `muu` "Muu" | **NEW** `muu-siseriiklik`; `muu` **KEEP, active** | 1130 historical `muu` answers untouched and un-reinterpreted | raw `muu` still reads to `muu` |

#### EU family

| proposed visible value | current value | action | historical behaviour | import behaviour |
| --- | --- | --- | --- | --- |
| ELi konsultatsioon | `konsultatsioon` "Konsultatsioon" | **NEW** `el-konsultatsioon`; `konsultatsioon` **KEEP, active** | every held row keeps its meaning | raw `EL konsultatsioon` / `avalik konsultatsioon` still read to `konsultatsioon` |
| ELi direktiiv | `direktiiv` "Direktiiv" | **RENAME** | zero risk | unchanged — `EL direktiiv` is already an alias |
| ELi määrus | `el-maarus` "EL määrus" | **RENAME** | zero risk | unchanged |
| Muu ELi dokument | `el-teatis` "EL teatis" | **NEW** `muu-el-dokument` + **RETIRE** `el-teatis` | held rows keep `EL teatis` | raw `EL teatis` / `EK teatis` still read to `el-teatis` |

#### Retired from new selection (kept, readable, reportable, editable)

`eelnou`, `visioon`, `korraldus`, `kaskkiri`, `kusitlus`, `ettepanek`,
`el-teatis` — and, if §4.5 option **A** is chosen, `strateegia`, `arengukava`,
`tegevuskava`.

#### The two renames are safe; the two look-alike renames are not

`direktiiv → "ELi direktiiv"` and `el-maarus → "ELi määrus"` are pure label
changes: the manifest already documents both as unconditionally European
(*"Direktiiv on alati ELi akt"*), and `EL direktiiv` / `ELi määrus` are already
reviewed aliases of those exact keys.

`konsultatsioon → "ELi konsultatsioon"` and `ettepanek → "Koja ettepanek või
pöördumine"` are **not** renames and must not be done as renames. Both would
narrow an existing concept and silently re-describe every Matter already
filed under it:

- `konsultatsioon` is deliberately EU-neutral — its description says so, and
  `avalik konsultatsioon` (a domestic round) is one of its aliases. Relabelling
  it would assert that every historical domestic consultation was European;
- `ettepanek`'s description is *"Ettepanek, sealhulgas Euroopa Komisjoni
  ettepanek õigusakti kohta"* — it covers the **Commission's** proposals.
  Relabelling it *Koja ettepanek* would invert the author on those rows.

Hence: new row, old row retired or kept, nothing remapped.

#### Multi-select survives

`Matter.legal_instruments` stays many-to-many. Nothing found in this
inspection argues otherwise, and ADR 0070 and the register both require it:
`S, M`, `direktiiv ja määrus`, `direktiiv/määrus` and `VTK eelnõu` are all in
the historical column, and `canonical_legal_instrument_keys` splits them into
several keys by design. The examples the brief names —
*Seadus + ELi direktiiv* for a transposition, *Koja ettepanek + Seadus* —
must and do remain representable.

### 4.5 Recommendation: "Strateegia, arengukava või tegevuskava"

The three values are genuinely distinct in the manifest, each with its own
alias and its own one-line definition. They carry historical assignments.

| option | information kept | UI the lawyer gets | migration |
| --- | --- | --- | --- |
| **A** one new combined value, three retired | history kept; **new records lose the 3-way distinction** | one chip | vocabulary data migration |
| **B** three stored, one UI grouping | everything | one *summary*, three chips inside it | **none at all** |
| **C** (rejected) combined value + backfill | destroys history | one chip | destructive |

**Recommended: B.**

Shape — and the repository already ships the component, twice:

```
▸ Strateegia, arengukava või tegevuskava
```
closed by default; opening it reveals the three existing chips; once something
is chosen the summary states the answer:
```
▾ Strateegia, arengukava või tegevuskava · Arengukava
```

This is exactly what PR #230 does to `Valdkonnad` (`<details
class="chipdetails" data-stay-closed>` with a summary naming the answer) and
what `Adressaat` already does on `Uus teema`. It works with scripting off, it
opens server-side for a refusal, and it costs one `<details>` in the template.

Why B over A:

- **it is the only option with no migration and no information loss** — the
  default preference stated in the brief and in `AGENTS.md`;
- the reviewed reading of the register (`canonical_legal_instrument_keys`)
  continues to produce three distinct keys; under A the raw reading and the
  offered vocabulary would disagree, and the disagreement would be invisible;
- the lawyer's stated need is *"I do not want to choose between three chips at
  a glance"*, which a collapsed summary answers. It does not obviously follow
  that the department wants to stop being able to say *arengukava*.
- `visioon` is deliberately **outside** the group. It is a fourth concept, the
  lawyer did not name it, and folding it in would be this report making a
  vocabulary decision.

If Kaur decides the department genuinely never needs the distinction again,
**A is acceptable** under two conditions: (i) the three existing rows are
`is_active=False` and never remapped; (ii) the decision is recorded in the ADR
as *"new work stops distinguishing these three"*, not as a tidy-up.
**A is blocked on the §3.7 fix**, because it retires rows.

---

## 5. Menetlusliik — complete dependency map, and the domestic/EU contract

### 5.1 `Matter.track` — every dependency

`app/workflow/enums.py::Track(models.TextChoices)`, seven values:
`DOMESTIC` (Riigisisene), `EU_INITIATIVE` (ELi algatus),
`NATIONAL_TRANSPOSITION` (ELi õiguse ülevõtmine),
`STRATEGY` (Strateegia või arengukava), `KODA_INITIATIVE` (Koja algatus),
`IMPLEMENTATION` (Rakendamine või järelevalve), `OTHER` (Muu).

| # | dependency | file | nature | breaks on key removal? |
| --- | --- | --- | --- | --- |
| 1 | `Matter.track` | `app/matters/models.py:217` | `CharField(32, choices, blank, default="")` | yes |
| 2 | `StageVocabulary.applicable_tracks` | `app/workflow/models.py:44` | `ArrayField(CharField(choices=Track))` — **empty on all ten rows**; `applies_to()` called from nowhere | yes, structurally; no, behaviourally |
| 3 | `OperationalSnapshot.track` | `app/reporting/models.py:88` | frozen historical column | yes |
| 4 | `matters_by_track` metric | `app/reporting/selectors/matters.py:376` | groups on `track`, labels from `Track.choices`, drill-through `?menetlusliik=` | yes |
| 5 | `ReportingContext.track` / `PARAM_TRACK="menetlusliik"` | `app/reporting/context.py:48,197` | filter + URL param | bookmarks |
| 6 | reporting filter pill | `app/reporting/filters.py:66,156,232` | label map + `<select>` | yes |
| 7 | CSV export column `menetlusliik` | `app/reporting/exports.py:122,144` | `get_track_display()` | text only |
| 8 | register filter `?menetlusliik=` | `app/matters/register_filters.py:576` | `track=` or `MISSING` | bookmarks |
| 9 | similar-matter engine | `app/related_materials/engine.py:147,318,453,736` | **tie-break only, above the 3.0 threshold**; never qualifies a candidate on its own | degrades quietly |
| 10 | intake suggestions | `app/matters/intake_suggestions/vocabulary.py:396-459`, `analysis.py:810-870`, `prefill.py:156` | `TRACK_RULES` per Track value; **off on `Uus teema` after PR #230**, still live on `Muuda teemat` | yes |
| 11 | `create_matter` validation | `app/matters/services.py:200` | `track not in Track.values` → `DomainError` | yes |
| 12 | `change_track` | `app/matters/services.py:645` | audited single-field write | yes |
| 13 | `MatterCreateForm.track` | `app/matters/forms.py:1035` | radios/chips | — |
| 14 | `MatterEditForm.track` | `app/matters/forms.py:1544` | radios/chips | — |
| 15 | `MatterFieldForm.track` | `app/matters/forms.py:3404` | inline rail edit | — |
| 16 | Teema rail inline `<select>` | `templates/matters/partials/rail.html:86` | `update_field field='track'` | — |
| 17 | register `<select>` | `templates/matters/matter_list.html:202` | — | — |
| 18 | reporting `<select>` | `templates/reporting/base.html:149` | — | — |
| 19 | `/admin` list filter | `app/core/admin.py:81` | — | — |
| 20 | migrations with the choices literal | `matters/0001`, `reporting/0001`, `workflow/0001` | frozen copies | must never be edited |
| 21 | seed commands | `seed_dev_data`, `seed_e2e_data` | `Track.DOMESTIC` | — |
| **—** | **imports** | `app/legacy_import/**` | **no reference whatsoever** | **no** |
| **—** | **search projection** | `app/search/indexing.py` | not indexed | **no** |
| **—** | **process timeline** | `app/matters/process_timeline.py` | not read | **no** |

Two dependencies are weaker than they look and should be named:
**#2 is dead code** (empty arrays, no caller), and **#9 is a 0.5 tie-break
applied only to candidates that already scored ≥3.0**.

### 5.2 The four designs assessed

| criterion | **A** infer/set track from Õigusakt | **B** keep track, edit-only | **C** derive dynamically, retire track | **D** hybrid: legacy semantics + new two-family |
| --- | --- | --- | --- | --- |
| mixed instrument selections | writes one value where the record says two — **lossy** | untouched, human answers | reads as a *set*: `Eesti + EL` — honest | two overlapping classifications to keep in step |
| national law implementing a directive | `Seadus + ELi direktiiv` would write `EU_INITIATIVE` or need a bespoke rule — **the exact trap the brief names** | `NATIONAL_TRANSPOSITION` stays a deliberate human answer | reads `Eesti + EL`, which is what it is | ambiguous which classification wins |
| Koda proposal about EU law | would write `EU_INITIATIVE`, erasing `KODA_INITIATIVE` | `KODA_INITIATIVE` survives | `Eesti` + `EL` if both ticked | as above |
| `StageVocabulary.applicable_tracks` | would start mattering with no review | unaffected (still empty) | would need retiring too | unaffected |
| imports | unaffected (no track in imports) | unaffected | unaffected | unaffected |
| reports | `matters_by_track` silently changes meaning for new rows | stable, coverage falls honestly | metric must be replaced | two metrics |
| search / filter | `?menetlusliik=` starts answering a derived question | unchanged; add a second param | `?menetlusliik=` must be kept working for history | two params, two meanings |
| timeline | unaffected | unaffected | unaffected | unaffected |
| historical records | unaffected only if the inference is create-time-only and never backfilled | unaffected | unaffected | unaffected |
| **verdict** | **reject** | **V1** | long-term, after C's preconditions | reject — two classifications is the problem, not the fix |

### 5.3 Recommended V1 contract

> **Menetlusliik leaves `Uus teema`. It does not leave the product, it is
> never inferred, and domestic/EU is read from Õigusakt as a derived set.**

1. **`Matter.track` is untouched.** Same column, same seven keys, same
   `Track.choices` literal in every migration. No schema change, no data
   migration, no backfill, no renaming.
2. **The control is removed from `MatterCreateForm` and
   `matter_create.html` only.** It stays on `MatterEditForm` (*Muuda teemat*)
   and on the Teema rail's inline `<select>`, which is where a lawyer
   correcting a record already sets it. Nothing becomes unanswerable — the
   rail control ships today.
3. **A new Matter carries `track = ""`.** That is already the field's default
   and already a valid, common state (`active_without_track` is a
   coverage-aware metric with `minimum_coverage=0.0`, and `Saabunud` has
   always been able to create Matters without one).
4. **Domestic/EU is a derived, read-only reading of `Matter.legal_instruments`.**
   One reviewed constant beside the vocabulary it classifies, in
   `app/taxonomy/legal_instruments.py` — not a new column, not a new table:

   ```
   EU_FAMILY_KEYS        = {direktiiv, el-maarus, el-teatis,
                            el-konsultatsioon, muu-el-dokument}
   DOMESTIC_FAMILY_KEYS  = {seadus, maarus, vtk, korraldus, kaskkiri,
                            koja-ettepanek, muu-siseriiklik}
   # everything else is UNSPECIFIED, deliberately:
   #   eelnou, konsultatsioon, strateegia, arengukava, tegevuskava,
   #   visioon, ettepanek, kusitlus, muu
   ```

   Each assignment is defensible from the manifest's own descriptions: the
   five EU keys name acts with no domestic equivalent or an explicit EU
   author; the seven domestic keys name Estonian instruments; the nine
   unspecified keys are ones the module *already documents as neutral about
   who runs them*. Nothing is guessed.
5. **The reading answers with a set, never with one value.**
   `Eesti` · `EL` · `Eesti + EL` · `Määramata`. A Matter carrying
   `Seadus + ELi direktiiv` reads **Eesti + EL**, which is the truthful
   description of a transposition and is exactly the case the brief warns
   against collapsing.
6. **Nothing is ever written.** `Matter.track` is never set, cleared or
   suggested from Õigusakt. The derivation has no write path. This is the
   rule that keeps the transposition trap closed: a domestic implementing law
   becomes an EU Matter only if a person ticks an EU instrument, which is a
   statement, not an inference.
7. **No text inference.** `TRACK_RULES` in `intake_suggestions` is untouched
   and stays off `Uus teema` (PR #230). Title and document text never decide
   the family.
8. **Filtering.** `?menetlusliik=` is unchanged and keeps meaning exactly what
   it means today. If a scope filter is wanted, it is a **second** parameter
   — `?ulatus=eesti|el` — over `legal_instruments__key__in=…`, with "at least
   one of" semantics and no exclusivity claim. Two filters, two meanings,
   neither derived from the other. This can ship after Package 2.
9. `StageVocabulary.applicable_tracks` stays empty and unread. If the new
   Hetkeseis vocabulary later wants applicability (e.g. *Eesti seisukoht
   koostamisel* only for EU work), the field is the right home — but nothing
   calls `applies_to()` today, so wiring it is new work and its own decision.

### 5.4 Longer-term cleanup path (not Package 2)

1. Ship V1; let a season of new Matters accumulate a reviewed Õigusakt.
2. Measure `matters_by_track` coverage on native records created after V1. If
   it collapses, `track` is de facto unused for new work and the question
   becomes real.
3. Convert `Track` from `TextChoices` to a `ProcedureType` vocabulary table
   with `is_active` and `sort_order`, migrating the seven stored keys 1:1 —
   the same shape `LegalInstrumentType` already has. Then, and only then, is
   retiring individual procedure kinds a safe `is_active` edit.
4. Replace `matters_by_track` with a family metric, or keep both.

Steps 3–4 need their own ADR and are not implied by Package 2.

---

## 6. Adressaat on `Uus teema`

### 6.1 Verification — the three relations are genuinely distinct

| concept | where | cardinality | meaning |
| --- | --- | --- | --- |
| **Saatja** | `Matter.source_organisations` → `MatterSourceOrganisation` | **many** (ADR 0025) | who sent or initiated it. *"There is deliberately no singular `source_organisation` accessor and no notion of a primary sender."* |
| **Adressaat** | `Matter.addressee_organisation` FK, `PROTECT`, nullable | **one** | who the Matter is addressed to — the register's `KELLELE` |
| **Saaja** | `Submission.recipients` → `SubmissionRecipient(role)` | **many**, roled `ADDRESSEE` / info | who Koda formally wrote to, per outgoing submission |

`reporting/selectors/organisations.py` opens by refusing to merge them, and
records the era boundary: the workbook's single counterparty column meant
**KELLELT (sender) 2011–2019** and **KELLELE (addressee) from 2020**.
`legacy_import/contracts.py` enforces it: *"a single sheet cannot carry both
KELLELT and KELLELE; they are different facts and no year uses both."*

**ADR 0069 behaviour** (`_default_addressee`, `forms.py:871`): on `Uus teema`
only, when (i) nothing already answers Adressaat, (ii) `addressee_is_manual`
is unset, and (iii) **exactly one** sender is named, the sender is written
into the *bound data* as the addressee. A manual answer wins for ever; two
senders produce no default; `Muuda teemat` and `Saabunud` are explicitly out
of scope.

**Other consumers of `Matter.addressee_organisation`:**
`reporting/exports.py` (CSV column `adressaat`),
`matters_by_addressee_organisation` metric, `search/views.py`'s suggestion
context line, `matters/selectors.py` (organisation page),
`legacy_import/register_refresh.py` (**writes it** from the register's
KELLELE column), `final_cutover.py`, and the quality queue's
`_unresolved_legacy_organisations` — which counts a Matter only when it has
**neither** a sender **nor** an addressee, so a NULL addressee beside a named
sender creates no quality-queue row.

**No automation depends on a Matter-level addressee.** Nothing reads it to
decide a recipient, a deadline, a work item or a submission.

### 6.2 Answer

**Yes — `Uus teema` can stop asking for Adressaat with the data model
completely untouched.** There is a shipped precedent: `Saabunud`
(`IncomingIntakeForm` / `matters/intake.py::register_incoming`) has never
asked for an addressee and creates Matters with `addressee_organisation =
NULL` today.

### 6.3 What a new Matter should carry: **NULL**

**Recommendation: retire the ADR 0069 default together with the control.
Do not keep deriving it silently.**

The reasoning is ADR 0069's own. Its whole justification for overturning
ADR 0067's refusal was the *visibility* of the guess:

> A guessed counterparty is invisible only if the page never says what it
> guessed. This round folds Adressaat behind a summary that names the answer,
> so the field states its own value on every visit […] A wrong default is then
> one line of text away from being noticed, at the moment the record is being
> made, by the person making it.

Remove the disclosure and its summary — which is what "stop asking" means —
and that justification is gone. What remains is precisely the thing ADR 0067
refused and ADR 0069 never argued for: *auto-selecting the sender as the
addressee, invisibly.*

Four concrete costs of keeping the silent copy:

1. **The Ministry → Koda → Riigikogu case.** A ministry sends a draft; Koda
   later writes to the Riigikogu. The `Submission.recipients` record is
   correct either way — it is a separate relation — so the outgoing semantics
   are **not** corrupted. What is corrupted is the Matter's own `KELLELE`:
   `matters_by_addressee_organisation` would count that Matter against the
   ministry, and the CSV would say so.
2. **`register_refresh` noise.** The refresh compares its resolved KELLELE
   against `Matter.addressee_organisation` and reports a `FieldChange` for
   every disagreement. Silently seeding the sender turns a clean refresh
   report into a wall of spurious changes for an operator to adjudicate.
3. **The search dropdown's second line** prints owner · addressee · stage.
   A guessed addressee is read there as a fact.
4. **It is unfalsifiable after the fact.** A guessed addressee and a chosen
   one are the same row.

NULL is honest, first-class, already produced by `Saabunud`, rendered by the
rail as a quiet `+ Lisa` rather than an em dash, and answerable in three
existing places: the Teema rail's inline *Kellele* editor, `Muuda teemat`, and
the register refresh.

### 6.4 Smallest safe V1

- Remove `addressee_organisation`, `addressee_name` and `addressee_is_manual`
  from `MatterCreateForm`; remove the disclosure block from
  `matter_create.html`; remove the `_default_addressee` call from
  `MatterCreateForm.__init__`.
- Keep `_default_addressee` **as a function** and keep
  `resolve_addressee` — the latter is used by `matter_edit`.
- Keep `_promote_named_senders`? No: with no addressee control on the page
  there is nothing to promote. It is used only by `MatterCreateForm`;
  `MatterEditForm` builds its own shortlist. Delete the call, keep the
  function only if a test still covers it.
- **Supersede ADR 0069** with a short record saying the default went with the
  control it made visible, and that ADR 0067's original refusal stands again
  for this surface.
- **No model change, no migration, no backfill, no reindex.**
- Consequence to state plainly in the ADR: `matters_by_addressee_organisation`
  coverage will fall for natively created work. That is the metric becoming
  honest, not a regression, and the metric is already coverage-aware
  (`minimum_coverage=0.0`) and already documents the era boundary.

---

## 7. The initial "Koostan arvamuse" action

### 7.1 What already exists

| piece | where |
| --- | --- |
| `NextAction` model | `app/workflow/models.py:170` — `matter`, `text`, `kind`, `date_semantics`, `target_date`, `date_precision`, `responsible`, `status`, `replaced_by` |
| **one open action per Matter** | `UniqueConstraint(fields=["matter"], condition=status=OPEN)` — `workflow_one_open_action_per_matter` |
| **a DO+DEADLINE must have a date** | `CheckConstraint` — `workflow_deadline_requires_a_date` |
| service | `set_next_action_for_new_work()` → `responsible_for_new_work()` → `set_next_action()` — supersedes the previous action, never overwrites |
| **the form already on `Uus teema`** | `NextActionForm` (prefix `next`), rendered as section 7 of `matter_create.html` — `Järgmiseks` text + `Millal?` with four quick-span chips and an exact-date disclosure |
| binding rule | `views.py:1718` — `wants_action = any(POST["next-text"] or POST["next-target_date"])`. The form is bound **only** when a person wrote something |
| creation | inside `matter_create`'s single `transaction.atomic()`, after the Matter, with `default_responsible=data["owner"]` |
| native classification | always `DO` / `DEADLINE` / `EXACT`, decided in `as_service_kwargs`, never posted (ADR 0052 §3) |
| Minu asjad | `work_items.dated_actions()` → `action_item()`; ownership by `NextAction.responsible`, defaulting to `matter.owner` |
| closure | `close_matter()` → `end_open_action_for_closure()` cancels the open action |
| concurrency | `set_next_action` takes `select_for_update()` on the **Matter** row, the same lock `close_matter` takes, in the same order |

**So mechanism C — "another existing work-item mechanism" — is already the
answer to half the question.** Nothing new needs building; what Package 2
changes is what that panel arrives holding.

### 7.2 Recommendation: **B**, as a pre-ticked, visible proposal

> A checkbox chip in the existing `Järgmine tegevus` panel, ticked by default
> **only when `Arvamuse tähtaeg` has been answered**, which pre-fills
> `Järgmiseks` with `Koostan arvamuse` and `Millal?` with the date rule in §8.

```
┌─ Järgmine tegevus ──────────────────────────────────────┐
│ ☑ Koostan arvamuse                                      │
│ Järgmiseks  [ Koostan arvamuse                        ] │
│ Arvamuse koostan  [Täna][+1 näd][+2 näd][Kuu] [Kuupäev…]│
│                    ↳ 27.01.2026                          │
└──────────────────────────────────────────────────────────┘
```

Why **not A (automatic)** — three independent reasons, each sufficient:

1. **It would silently remove `Arvamuse tähtaeg` from every work surface.**
   Verified by running code. ADR 0050's precedence is *any open `NextAction`
   outranks `Arvamuse tähtaeg`*, and it is not a comparison of dates:

   ```
   BEFORE:  [('',                 2026-10-01, 'RESPONSE_DEADLINE')]
   AFTER:   [('Koostan arvamuse', 2026-10-01, 'NEXT_ACTION')]
   ```
   Auto-creating on every Matter makes `outstanding_response_deadlines()`
   permanently empty for new work — on Minu asjad, Ülevaade, Osakonna töö and
   every `?too=` register population. With B this happens too, but only
   because a person said so.
   (`response_obligations()` is unaffected in both cases — the Chamber still
   owes the answer until a `SENT` Submission discharges it. That separation is
   deliberate and holds.)
2. **Not every Matter produces an opinion.** A file entered at Riigikogu
   stage, a monitoring file, a Koda-initiated proposal and a corrected archive
   row all exist, and inventing *Koostan arvamuse* on each is the "survey"
   failure Package 1 just removed.
3. **It could refuse the whole Matter creation.**
   `responsible_for_new_work()` raises `DEPARTED_OWNER_REFUSAL` when the
   Matter's owner is not an assignable department worker. An automatic action
   would put that refusal in the path of creating a Matter that has nothing
   to do with next steps.

### 7.3 The three requirements in the brief, and how the design meets them

| requirement | mechanism |
| --- | --- |
| **see what will be created before saving** | the panel is already visible and un-collapsed on `Uus teema` (it was deliberately taken out of a disclosure). The prefilled text and date are ordinary editable controls |
| **change or remove it** | edit the text box; clear it; or untick the chip. **Unticking must clear both `next-text` and `next-target_date`**, because `wants_action` reads those two fields, not the chip |
| **no duplicate from repeated/refused submissions** | four layers, three of which already hold |

On duplicates, precisely:

- a refused save rolls the whole `transaction.atomic()` back — **no orphan
  action can survive a refusal**, today;
- `workflow_one_open_action_per_matter` makes two open actions on one Matter
  impossible at the database level;
- `set_next_action` supersedes rather than inserts, so a retry against the
  same Matter replaces;
- **the residual risk is at the Matter level, not the action level**: a
  double-click on `Loo teema` creates two Matters, each with one action. That
  is a pre-existing property of `matter_create` (no idempotency token), and
  Package 2 does not worsen it. ADR 0087's `Sarnased teemad` is the shipped
  mitigation. A submit-once guard is out of scope — listed in §13.
- **The refused-form re-render must not re-apply the proposal.** This is the
  same class of bug `addressee_is_manual` was invented for. A plain unticked
  checkbox posts nothing, so the correct implementation is a real
  `BooleanField` whose absence means "the person took it off" — and the
  re-render must read the POST, never re-derive from `response_deadline`.

---

## 8. Date semantics for the initial action

### 8.1 Every relevant date primitive on `main`

| date | where | meaning | can be late? |
| --- | --- | --- | --- |
| `Matter.received_date` | Matter | *Saabus* — the day the file arrived. Defaults to today on `Uus teema` (an observation) | no |
| `Matter.response_deadline` | Matter | **`Arvamuse tähtaeg`** — the day Koda's opinion is due to whoever asked. Deliberately **no** `initial` on the create form: *"a commitment, usually somebody else's"* | yes (`real_deadlines`) |
| `NextAction.target_date` + `date_precision` | NextAction | **"the day the work will be done"** (ADR 0052 §3). Renders as **`Plaanis`**, never `Tähtaeg` (ADR 0054 amendment) | yes, when DO+DEADLINE |
| `NextAction.created_at` | NextAction | provenance | no |
| `MatterImportantDate.date_value` / `period_end` | intelligence | **`Oluline tähtaeg`** — an externally meaningful milestone; enters `real_deadlines` | yes |
| `MatterEngagement.feedback_deadline` | Matter | **`Tagasiside tähtaeg`** — what Koda asked of *its own members*; explicitly **not** in `real_deadlines` (ADR 0086 §3) | not a deadline |
| `Submission.sent_at` + `sent_at_precision` | Submission | when the opinion actually went | n/a |
| `DatePrecision` | workflow | EXACT / MONTH / QUARTER / HALF_YEAR / YEAR / INFERRED; four are offered to people (ADR 0079) | — |

**There is no "prepare by" or internal target field anywhere.** There is also
no second deadline column, and the product has been careful about that: ADR
0054 records that it already has *two* things called a `tähtaeg` and refuses
to create a third by printing `Plaanis` for a lawyer's own date.

### 8.2 The five options

| | option | verdict |
| --- | --- | --- |
| **A** | the official external response deadline | **the right default**, see below |
| **B** | the same day as the official deadline | identical to A in practice |
| **C** | an internal preparation target entered separately | **this is what `NextAction.target_date` already is.** No new field needed |
| **D** | automatically calculated N days before the deadline | **refused.** It requires inventing a number, which the brief forbids and which nothing in the repository supplies |
| **E** | an undated initial action | **impossible.** `workflow_deadline_requires_a_date` refuses DO+DEADLINE with no date; `set_next_action` raises *"Tähtajaline tegevus vajab kuupäeva."*; `NextActionForm.clean` refuses text without a date. Making it possible would mean reintroducing `WAIT`/`MONITOR` to native creation, which ADR 0052 retired |

### 8.3 Recommendation

> **`Arvamuse koostan` is the initial `NextAction`'s own date — an internal
> target (option C) — pre-filled with the official deadline (option A/B),
> and offered only when that deadline is known.**

Concretely:

1. **`Vastamise tähtaeg` = `Matter.response_deadline`, unchanged.** Same
   field, same name (`Arvamuse tähtaeg`), same position on the form, same
   meaning, still with no default of its own.
2. **`Arvamuse koostan` = the proposed action's `target_date`**, stored as
   `DO` / `DEADLINE` / `EXACT` exactly as every other native next step. It
   needs **no new column, no new model and no migration** — ADR 0052 §3
   already defines this field as *the day the work gets done*.
3. **The label on the panel changes from `Millal?` to `Arvamuse koostan`
   while the proposal is ticked**, and reverts to `Millal?` when it is not.
   One label, two words of the lawyer's own.
4. **The pre-filled value is the same day as `Arvamuse tähtaeg`.** It is the
   only deterministic date the record contains, and it invents nothing. The
   four existing quick-span chips and the exact-date box are already on the
   control for moving it earlier.
5. **If `Arvamuse tähtaeg` is empty, the proposal is not offered ticked.**
   There is nothing to date it from, and a ticked proposal with an empty date
   would refuse a title-only save — which is the one thing `Uus teema` must
   never do.
6. **No number of days is invented.** If the department wants *"three working
   days before"*, that is an explicit product constant to be reviewed like any
   other vocabulary. Listed in §13 as an open decision, deliberately unchosen
   here.

**Consequences, stated plainly:**

- the register's *Kuupäev* column already prefers the open action's date over
  `Arvamuse tähtaeg` (`app/matters/register_dates.py`), so with the default
  the displayed day is unchanged and only its source moves;
- on Minu asjad the row count is unchanged; the text changes from the response
  deadline row's own label to `Koostan arvamuse`;
- a DO+DEADLINE qualifies for `real_deadlines()`, so the item stays in
  *Tähtajad* — it is not demoted to an intervention;
- if a lawyer moves the prep date **earlier**, ADR 0050 means the earlier date
  is what shows and the official deadline goes back to being a fact in the
  header. That is the documented contract working as intended, and it is the
  behaviour the lawyer is asking for.

---

## 9. Proposed `Uus teema` layout after Package 2

Built on PR #230's direction. **Removed from the page: Menetlusliik,
Adressaat.** **Added: the Koostan arvamuse proposal.** **Folded: the three
strategy instruments.** Everything else keeps its position, its field name and
its posted value.

```
┌──────────────────────────────────────────────────────────────────────┐
│  Uus teema                                                            │
├──────────────────────────────────────────────────────────────────────┤
│ 1  Pealkiri            [_______________________________________]  ●   │
├──────────────────────────────────────────────────────────────────────┤
│ 2  Millest teema räägib [                                      ]      │
│    Märkmed (privaatsed)  [                                     ]      │
├──────────────────────────────────────────────────────────────────────┤
│ 3  Failid  [ Vali failid ]         Saabus  [16.09.2026]               │
│                                    Arvamuse tähtaeg [          ]      │
├──────────────────────────────────────────────────────────────────────┤
│ 4  Vastutaja    (•) Kaur  ( ) Mari  ( ) Jaan                          │
│    Saatja       [ Otsi või lisa asutus…            ] +                │
├──────────────────────────────────────────────────────────────────────┤
│ 5  ▸ Valdkonnad · Ehitus, Keskkond                                    │
├──────────────────────────────────────────────────────────────────────┤
│ 6  Hetkeseis                                                          │
│    (•)Määramata ( )Idee ( )Kooskõlastusringil ( )Valitsuses           │
│    ( )Riigikogus ( )Jõustumise ootel ( )Jõustunud                     │
│    ( )Eesti seisukoht koostamisel ( )ELi menetluses                   │
│    ( )ELi õiguse ülevõtmise ootel ( )Muu                        (?)   │
│    ⓘ Kui Koda enam ei tegele, lõpeta teema teema lehel.               │
├──────────────────────────────────────────────────────────────────────┤
│ 7  Õigusakt  (2)                                                      │
│    EESTI     [✓VTK] [ Seadus] [ Määrus]                               │
│              [ Koja ettepanek või pöördumine]                         │
│              ▸ Strateegia, arengukava või tegevuskava                 │
│              [ Muu siseriiklik]                                       │
│    EL        [ ELi konsultatsioon] [✓ELi direktiiv] [ ELi määrus]     │
│              [ Muu ELi dokument]                                      │
│              [ Muu] ─▸ [ Õigusakti liik: ______________ ]             │
│    ⓘ Ulatus: Eesti + EL                                    (derived)  │
├──────────────────────────────────────────────────────────────────────┤
│ 8  Järgmine tegevus                                                   │
│    [✓] Koostan arvamuse                                               │
│    Järgmiseks       [ Koostan arvamuse                            ]   │
│    Arvamuse koostan [Täna][+1 näd][+2 näd][Kuu] ▸Kuupäev… 27.01.2026  │
├──────────────────────────────────────────────────────────────────────┤
│    [ Loo teema ]  Loobu     Ülejäänud andmeid saab lisada ka hiljem.  │
├──────────────────────────────────────────────────────────────────────┤
│    Sarnased teemad  (HTMX, GET, writes nothing)                       │
└──────────────────────────────────────────────────────────────────────┘
```

| aspect | decision |
| --- | --- |
| **field order** | title → substance → files & dates → people → Valdkond → Hetkeseis → Õigusakt → next action → submit |
| **always visible** | Pealkiri, Lühikokkuvõte, Märkmed, Failid, Saabus, Arvamuse tähtaeg, Vastutaja, Saatja, Hetkeseis, Õigusakt, Järgmine tegevus |
| **disclosures** | Valdkonnad (PR #230), *Strateegia, arengukava või tegevuskava* (§4.5), `Kuupäev…` on the action date (existing), Saatja's long tail (PR #230) |
| **no longer asked** | **Menetlusliik**, **Adressaat**, Nähtavus (already gone), Testandmed (already gone), the document reader's suggestion panel (PR #230) |
| **where the initial action appears** | section 8, the existing `Järgmine tegevus` panel, un-collapsed, with a pre-ticked chip above the two fields |
| **multi-select display** | Õigusakt keeps its `field__count (N)` beside the legend and `×` on each chosen chip — the asymmetry that used to distinguish it from Menetlusliik's radios (ADR 0070 §4). With Menetlusliik gone, the asymmetry is now against Hetkeseis's radios, and the convention holds unchanged |
| **the two family headings** | `EESTI` / `EL` are **sub-legends inside one fieldset**, one `legal_instruments` field, one posted name. They are not a second control and must not be made into one |
| **the derived `Ulatus` line** | read-only, computed from §5.3, updated by the same browser island that maintains the chip count. It states a reading; it posts nothing |
| **where procedural links would later fit** | between §7 and §8, as a disclosure — a place is reserved in the layout and nothing is built |

Two notes for whoever implements it:

- the page gets **shorter again**. PR #230 already takes it from 1247 px to
  1135 px; removing Menetlusliik's seven chips and Adressaat's disclosure row
  removes roughly another two rows. **The `uus-teema` and `uus-teema-viga`
  visual baselines will need adopting again, from CI bytes.**
- `MatterCreateForm.data_class` stays a constant, `visibility` stays decided
  server-side. Neither is affected.

---

## 10. Compatibility matrix

For each case: **display** / **store** / **preserve** / **never auto-change**.

| record | Package 2 displays | stores | preserves | does not automatically change |
| --- | --- | --- | --- | --- |
| **hand-created new Matter** | new Hetkeseis labels; two-family Õigusakt; no Menetlusliik; no Adressaat; a proposed `Koostan arvamuse` | `track=""`, `addressee_organisation=NULL`, chosen instruments, optional NextAction | — | nothing; every value is the person's |
| **historical imported Matter** | held stage under its new label; held instruments under their labels (renamed or retired); `Menetlusliik` on the rail if it has one; `Adressaat` on the rail if the register gave one | unchanged | `legal_instrument_raw`, `CurrentRegisterState`, `source_era`, provenance | stage, track, instruments, addressee — nothing is rewritten, remapped or backfilled |
| **OneNote / register imported Matter** | as above; `origin` badge unchanged | unchanged | source pages, observations, raw cells | the same; `register_refresh` keeps writing stage and addressee from the source exactly as today |
| **Matter with a retired Valdkond** (PR #230) | *varasem valdkond* marker on the edit form | unchanged | relation, statistics, `?valdkond=` bookmark | nothing |
| **Matter with an old Hetkeseis** | the **same row** under its new label — no stage is retired by this package | unchanged FK | `OperationalSnapshot.stage_label` keeps the wording the reader saw at the time; `MATTER_STAGE_CHANGED` keeps `from_label`/`to_label` | the stage. **Prerequisite §3.7 must land before any stage is ever retired** |
| **Matter with an old LegalInstrumentType** | the held row, offered back on `Muuda teemat` (already unioned in), marked as no longer offered | unchanged M2M | the relation and the raw cell | never remapped to a new value |
| **`NATIONAL_TRANSPOSITION` Matter** | `Menetlusliik: ELi õiguse ülevõtmine` on the rail; `Ulatus` derived from its instruments — `Eesti + EL` if it carries both | `track` untouched | the human answer | **the track is never re-derived from Õigusakt, in either direction** |
| **EU legislative Matter** | EU-family instruments; `Ulatus: EL`; `ELi menetluses` / `Eesti seisukoht koostamisel` available | as chosen | — | `track` |
| **domestic implementation of EU law** | `Seadus` + `ELi direktiiv` if the lawyer ticks both → `Ulatus: Eesti + EL` | as chosen | — | **not classified as EU work by any text reference.** The only input is what a person ticked |
| **Koda-initiated proposal** | `Koja ettepanek või pöördumine` (new value); `track=KODA_INITIATIVE` if set on the rail | as chosen | existing `ettepanek` rows keep their meaning | the historical `Ettepanek` rows are not migrated to the new value |
| **Matter first entered at Riigikogu stage** | `Hetkeseis: Riigikogus`; **the Koostan arvamuse proposal is offered unticked** if there is no `Arvamuse tähtaeg` | no NextAction unless asked for | — | no action is invented |
| **closed Matter** | read-only surfaces unchanged; `Rohkem ei tegele` reads as the closure reason it already is | unchanged | `disposition`, `closed_at`, `closed_by`, the cancelled action | reopening; and `set_next_action` still refuses a closed Matter |

---

## 11. Migration assessment

**No migrations were created.** Anticipated, by class:

### No migration

- removing Menetlusliik from `MatterCreateForm` and `matter_create.html`;
- removing Adressaat from `MatterCreateForm` and `matter_create.html`;
- the initial `Koostan arvamuse` proposal (form + template + view);
- the date semantics (`NextAction.target_date` already exists);
- relabelling the `MONITORING_STOPPED` closure chip to `Rohkem ei tegele`
  (a constant in `app/matters/forms.py`);
- the `EU_FAMILY_KEYS` / `DOMESTIC_FAMILY_KEYS` reading (constants in
  `app/taxonomy/legal_instruments.py`);
- the §3.7 stage-retention fix (three form/context lines, one template marker);
- **no search reindex.** `INDEX_VERSION` and `ARCHIVE_INDEX_VERSION` are
  untouched: `indexed_text_for()` projects title, identifiers, aliases and
  authored summaries, and carries **no** stage, track or instrument text.

### Schema migration

**None.** Nothing in Package 2 requires one. The domestic/EU family is
deliberately a reviewed constant rather than a `LegalInstrumentType.family`
column, specifically so that this line can read "none".

### Vocabulary data migration

Two, both `RunPython` with a working reverse and both **fail-closed** (refuse
outright if a row has been renamed or edited since review) — the shape
`workflow/0006` and `taxonomy/0007` already use.

| migration | does | does not |
| --- | --- | --- |
| `workflow/0007_hetkeseis_vocabulary` | three `label_et` renames (`awaiting_entry`, `estonian_eu_position`, `awaiting_transposition`); new `help_text` for all ten from the lawyer's review; clears `is_provisional`; resolves the `other` / `eu_procedure` duplicate description | touch any `Matter`; touch any key; touch `applicable_tracks`; create or delete a row |
| `taxonomy/0008_oigusakt_two_families` | two `label_et` renames (`direktiiv`, `el-maarus`); four new rows (`koja-ettepanek`, `el-konsultatsioon`, `muu-siseriiklik`, `muu-el-dokument`); `is_active=False` on `eelnou`, `visioon`, `korraldus`, `kaskkiri`, `kusitlus`, `ettepanek`, `el-teatis` (+ the three strategy rows only under §4.5 option A); bumps `REFERENCE_LEGAL_INSTRUMENT_VERSION` to `2.0` with a new frozen baseline | remap a single `Matter.legal_instruments` row; touch `legal_instrument_raw`; touch `canonical_legal_instrument_keys`' readings of historical spellings |

`taxonomy/0008` retires rows, so **it is blocked on the §3.7 fix only if the
same defect exists for `LegalInstrumentType` — it does not** (the edit form
already unions held types). It is blocked on nothing.

`workflow/0007` retires nothing, so it is blocked on nothing either. **The
§3.7 fix is a prerequisite for any *future* stage retirement, and should land
in this package regardless, because it is a live latent defect.**

### Historical backfill

**None proposed. None needed. None acceptable.**

Every case the new vocabulary would "tidy" is a case where the old value is
the department's own answer: 1130 historical `muu` cells cannot be split into
*Muu siseriiklik* and *Muu ELi dokument* without a person reading each one,
and a `Strateegia` row is not improved by becoming a combined value. The
register's raw column is immutable provenance and is never rewritten
(`contracts.py` marks `ÕIGUSAKT` as `mapped`: a reviewed reading exists and
the importer deliberately does not apply it, *because whether a spreadsheet
cell may overwrite a lawyer's answer is a precedence question nobody has
answered*).

### Optional cleanup only

- clearing `is_provisional` on the ten stage rows once the lawyer signs off
  the wording — folded into `workflow/0007`;
- `docs/open-decisions.md` line 147 and line 497 close;
- `app/workflow/selectors.py` says "eleven stages" where the seeded count is
  ten — a comment fix;
- `StageVocabulary.applicable_tracks` is dead (`applies_to()` has no caller).
  Leave it. Removing an `ArrayField` is a schema migration for no benefit,
  and it is the right home if stage applicability is ever wanted.

---

## 12. Implementation dependency and order

| # | step | depends on | migration |
| --- | --- | --- | --- |
| 0 | **§3.7 stage-retention fix** — union the Matter's held stage into `MatterEditForm`, `MatterFieldForm` and `_header_context`; `retired_stage_id` template marker; regression test | — | none |
| 1 | **ADR** — one record covering domestic/EU, Adressaat (superseding ADR 0069 for this surface) and the initial action; or three short ones | Kaur's answers to §13 | — |
| 2 | `workflow/0007` — Hetkeseis labels, help text, `is_provisional` | 1 | vocabulary data |
| 3 | `taxonomy/0008` + `EU_FAMILY_KEYS` in the manifest | 1 | vocabulary data |
| 4 | Remove Menetlusliik from `MatterCreateForm` + template | 3 (the EU values are what replaces its intake role) | none |
| 5 | Remove Adressaat from `MatterCreateForm` + template; supersede ADR 0069 | 1 | none |
| 6 | The `Koostan arvamuse` proposal + `Arvamuse koostan` label | 1 | none |
| 7 | *(optional, can follow Package 2)* derived `Ulatus` line and `?ulatus=` filter | 3 | none |
| 8 | Adopt `uus-teema` / `uus-teema-viga` visual baselines from CI bytes | 4, 5, 6 | — |

Steps 2, 3, 5 and 6 are independent of each other and can be reviewed
separately. **Step 4 must not land before step 3** — removing Menetlusliik
before the Õigusakt EU family exists leaves the product with no way at all to
say a new Matter is European.

**Ordering against PR #230:** every step above touches
`MatterCreateForm`/`matter_create.html`, which PR #230 also changes. Package 2
should start from `main` **after** #230 merges, not beside it.

---

## 13. Test plan

Server-side tests unless marked **[e2e]**.

### A. Hetkeseis vocabulary
1. `selectable_stages()` returns exactly the eleven-minus-one = **ten** rows, in `sort_order`, with the reviewed labels — one assertion per proposed value.
2. `tests/test_stage_vocabulary_seed.py` extended: `workflow/0007` applied then reversed then re-applied leaves byte-identical rows.
3. `workflow/0007` **refuses** to run when a row's `label_et` or `help_text` differs from the frozen pre-state (fail-closed).
4. `app/workflow/vocabulary.py::RAW_LABEL_TO_STAGE` still agrees with `workflow/0004`'s frozen copy after the relabelling (the existing `tests/test_workflow_vocabulary.py` must stay green untouched).
5. `resolve_legacy_status("ootan jõustumist")` still resolves to `awaiting_entry` after the label change.
6. A Matter whose stage is `awaiting_entry` renders *Jõustumise ootel* on the header, the register and the CSV export.
7. `OperationalSnapshot` rows written before the rename still print their frozen `stage_label`.
8. `MATTER_STAGE_CHANGED` events written before the rename still carry the old `from_label`/`to_label`.
9. **[e2e]** every proposed Hetkeseis chip is reachable by keyboard and shows its tooltip at 420 px.

### B. Retired stage (the §3.7 fix)
10. A Matter carrying an `is_active=False` stage: `MatterEditForm(initial=…)` **offers** that stage, and the chip is marked as no longer offered.
11. Submitting `MatterEditForm` with only the title changed **keeps** the retired stage (today it silently clears it — this test fails on `main`).
12. Submitting the retired stage's own value is **valid**.
13. The header's inline stage `<select>` includes the held retired row.
14. `MatterFieldForm` accepts the held retired stage.
15. `?hetkeseis=<retired key>` still filters the register.

### C. Õigusakt
16. Multi-selection round-trips: create with `{vtk, ELi direktiiv}`, reopen, both are ticked, `set()` not `add()` semantics hold for a duplicated post.
17. `Seadus + ELi direktiiv` (the transposition shape) saves, renders and exports.
18. The combined strategy presentation (§4.5 B): the `<details>` summary names the chosen value(s); it renders **open** on a refusal and on a Matter that carries one; it works with scripting off.
19. A Matter carrying `arengukava` is still editable and keeps it after an unrelated edit.
20. A Matter carrying a **retired** type (`ettepanek`) is offered it back on `Muuda teemat` and keeps it after an unrelated save.
21. `canonical_legal_instrument_keys` is byte-for-byte unchanged: the existing `tests/test_reference_legal_instruments.py` passes untouched, including `"S, M"`, `"direktiiv ja määrus"`, `"VTK eelnõu"`, `"EL"` → `()` and `"muu"` → `("muu",)`.
22. `taxonomy/0008` refuses when a row has been renamed since review; reverses cleanly.
23. `REFERENCE_LEGAL_INSTRUMENT_VERSION` is `2.0` and the migration's frozen baseline matches the manifest.
24. `legal_instrument_other` is still cleared when `Muu` is unticked, and still refused when `Muu` is ticked and empty.

### D. Domestic / EU
25. `instrument_family({vtk})` → `{EESTI}`; `{ELi direktiiv}` → `{EL}`; `{Seadus, ELi direktiiv}` → `{EESTI, EL}`; `{muu}` → `∅`.
26. Every one of the 17 legacy keys plus the 4 new ones has a family assignment (or an explicit UNSPECIFIED) — a completeness test over the manifest, so a new value cannot be added without answering the question.
27. **`Matter.track` is never written by any Õigusakt path.** Create with EU instruments; assert `matter.track == ""`. Edit instruments on a Matter with `track=DOMESTIC`; assert the track is unchanged.
28. A `NATIONAL_TRANSPOSITION` Matter reads `Eesti + EL` when it carries both families and keeps its track.
29. A Matter whose **title** mentions "direktiiv" but whose instruments are `{Seadus}` reads `Eesti`, not `EL` — no text inference.
30. A Koda proposal about EU law (`Koja ettepanek` + `ELi direktiiv`) reads `Eesti + EL`.
31. `?menetlusliik=` filters exactly as it does today — a regression test against `main`'s behaviour.
32. `matters_by_track` still returns the same segments for a fixture built before the change.
33. **The intake control is gone but the semantics are not:** `MatterCreateForm` has no `track` field; a POST carrying `track=EU_INITIATIVE` is **ignored**, and the created Matter has `track=""`.
34. `MatterEditForm` and the rail inline `<select>` still set and clear every one of the seven tracks.

### E. Adressaat
35. `MatterCreateForm` has no `addressee_organisation`, `addressee_name` or `addressee_is_manual` field.
36. Creating a Matter with exactly one sender leaves `addressee_organisation` **NULL** (this asserts the reversal of ADR 0069 explicitly).
37. A forged POST carrying `addressee_organisation=<pk>` to `matter_create` is ignored; the Matter is created with NULL.
38. The Teema rail's *Kellele* inline editor still sets, changes and clears the addressee.
39. `MatterEditForm` still sets it, including a typed new institution through `resolve_addressee` inside one transaction.
40. **Outgoing Submission to an organisation other than the original sender:** a Matter whose sender is `Kliimaministeerium` and whose `Submission` has `SubmissionRecipient(role=ADDRESSEE, organisation=Riigikogu)` reports Riigikogu in `submissions` metrics and does **not** acquire a Matter-level addressee.
41. `register_refresh` still writes `addressee_organisation` from the source's KELLELE onto a Matter that has NULL, and reports the change.
42. `_unresolved_legacy_organisations` does **not** count a new Matter that has a sender and a NULL addressee.
43. `matters_by_addressee_organisation` still reports historical rows correctly and its coverage figure reflects the drop honestly.

### F. The initial action
44. `Arvamuse tähtaeg` answered → the page renders the proposal **ticked**, `next-text` prefilled `Koostan arvamuse`, `next-target_date` prefilled with the deadline.
45. `Arvamuse tähtaeg` empty → the proposal renders **unticked** and both fields empty; a title-only save creates a Matter with **no** NextAction.
46. **User removes it:** unticking clears both fields; the save creates a Matter with no NextAction.
47. **User changes its date:** posting a different `next-target_date` stores that date, `DO`/`DEADLINE`/`EXACT`.
48. **User changes its text:** posting different text stores it verbatim, trimmed, capped at 2000.
49. **Refused create does not duplicate the action:** a POST with a valid action and an invalid title creates neither a Matter nor a NextAction (transaction rollback), and the re-rendered form still holds the action the person typed.
50. **A refused save does not re-apply the proposal:** untick, submit with a bad title, and the re-render comes back **unticked**, not re-derived from `response_deadline`.
51. **Retry does not duplicate:** posting the same create form twice creates two Matters, each with exactly one open action; `workflow_one_open_action_per_matter` is never violated.
52. `set_next_action_for_new_work` is the service used (not `set_next_action`), so a departed owner is refused with `DEPARTED_OWNER_REFUSAL`.
53. The action's `responsible` defaults to the owner chosen on the same form.
54. **Minu asjad sees it:** the owner's `work_items()` contains one `NEXT_ACTION` row reading `Koostan arvamuse` on the chosen day — and **not** a second `RESPONSE_DEADLINE` row for the same Matter (ADR 0050).
55. `response_obligations()` still counts the Matter as unanswered until a `SENT` Submission discharges it.
56. `real_deadlines()` includes the row (it is DO+DEADLINE).
57. The register's *Kuupäev* column shows the action's date and sorts on it (`register_dates` both readings agree).
58. Closing the Matter cancels the action (`end_open_action_for_closure`).

### G. Closure / "Rohkem ei tegele"
59. The compact closure composer's `MONITORING_STOPPED` chip reads **`Rohkem ei tegele`**.
60. `Disposition.MONITORING_STOPPED`'s **stored value** is unchanged, and a Matter closed before the relabelling still reports correctly.
61. `resolve_legacy_status("rohkem pole tegevusi plaanis")` still resolves to the disposition, **not** to a stage.
62. **No `StageVocabulary` row named *Rohkem ei tegele* exists** — an explicit negative test, so nobody adds one later by accident.
63. `Hetkeseis = Jõustunud` leaves `is_open=True` and creates no closure — an explicit negative test.

### H. Permissions, accessibility, cross-cutting
64. `matter_create` still requires `@login_required` + `@business_write_required`; a read-only persona gets the refusal, not a partial form.
65. A restricted `NextAction` still does not leak onto the Teema page for a reader who may see the Matter but not the step (`current_action_of`, ADR 0038).
66. The similar-matter engine's track tie-break still behaves for Matters created without a track.
67. **[e2e]** keyboard-only path: tab through the whole form, tick one instrument in the strategy group, untick the action proposal, submit.
68. **[e2e]** 420 px: no horizontal scroll; the two Õigusakt family sub-legends stack; the action panel is reachable.
69. **[e2e]** scripting off: the strategy `<details>` opens, the instrument chips submit, the action proposal's checkbox posts, and the created Matter matches what the page said.
70. Visual baselines `uus-teema` and `uus-teema-viga` adopted from CI bytes; the 52 other renderings unchanged.

---

## 14. Decisions that still require Kaur / lawyer confirmation

| # | decision | why it cannot be settled here | recommended answer |
| --- | --- | --- | --- |
| 1 | **`Rohkem ei tegele` is a closure, not a Hetkeseis** — is the department content that it lives on the *Lõpeta teema* control rather than in the stage list? | It is the one place the lawyer's list contradicts a decision the product has already made twice (`workflow/0004`, `open-decisions.md` line 19) | Yes — relabel the closure chip |
| 2 | **Strategy consolidation: option A or B?** | Whether the department ever again needs to distinguish *strateegia* from *arengukava* from *tegevuskava* is a business question | **B** — one collapsed group, three stored values, no migration |
| 3 | Does **`Visioon`** belong inside that group, or is it retired separately? | The lawyer did not name it | Retire from new selection, keep it readable |
| 4 | **`Muu siseriiklik` / `Muu ELi dokument`** — does the plain `Muu` stay offered as a third catch-all, or is it retired from new selection? | 1130 historical rows hang on it | Keep `Muu` **active** as the "I genuinely cannot say" answer |
| 5 | **`Koja ettepanek või pöördumine`** — confirm it is a *new* concept and that the existing `Ettepanek` (which covers Commission proposals) is retired rather than relabelled | Relabelling would invert the author on held rows | New value; retire `Ettepanek` |
| 6 | **Which of `ELi menetluses` and `Muu`** the duplicated Hetkeseis description belongs to | `workflow/0006` shipped the duplication visibly and flagged it; `open-decisions.md` line 497 is still open | Resolve in `workflow/0007` while the lawyer is reviewing the wording anyway |
| 7 | The **unbalanced parenthesis** in the `Idee` description supplied on 2026-08-25 | `workflow/0006` transcribed rather than guessed | Fix in `workflow/0007` with the lawyer's own words |
| 8 | **Should `Arvamuse koostan` default to the deadline, or to *N days before*?** | The brief forbids inventing a number; if the department has one, it is theirs to state | Default to the deadline; move it with the existing quick spans |
| 9 | **Is the `Koostan arvamuse` proposal ticked by default, or offered unticked?** | It is the difference between "the next step is already there" and "the next step is one click away" | Ticked **when `Arvamuse tähtaeg` is answered**, unticked otherwise |
| 10 | Accepting that a ticked proposal **replaces `Arvamuse tähtaeg` on Minu asjad / Ülevaade / Tähtajad** (ADR 0050) | It is a visible change to three pages, even though the date shown is the same | Accept — the label is more informative and the obligation is still counted |
| 11 | **Adressaat on new Matters: NULL, or keep ADR 0069's silent copy?** | This report recommends reversing a nine-day-old accepted ADR | NULL, and supersede ADR 0069 for this surface |
| 12 | Accepting that `matters_by_addressee_organisation` **coverage falls** for natively created work | It is a number the department head reads | Accept — it becomes honest |
| 13 | **Is `Menetlusliik` wanted on `Muuda teemat` and the Teema rail at all**, or does the department want it gone from the UI entirely? | The V1 recommendation keeps it in both; "gone entirely" is a bigger decision with a reporting consequence | Keep on the rail; revisit after a season of data |
| 14 | The **domestic/EU family assignment of the nine "unspecified" legacy keys** | `eelnou`, `konsultatsioon`, `strateegia`, `arengukava`, `tegevuskava`, `visioon`, `ettepanek`, `kusitlus`, `muu` are genuinely neutral in the manifest | Leave unspecified; a Matter carrying only these reads `Ulatus: määramata` |
| 15 | Whether a **`?ulatus=` register filter** is wanted in Package 2 or later | It is additive and independent | Later |
| 16 | Whether **`Uus teema` should get a submit-once guard** | A double-click creates two Matters today; that is pre-existing and out of Package 2's scope | Separate ticket |
| 17 | Priority of the **§3.7 stage-retention fix** | It is a live latent defect on `main` that no Package 2 change triggers, but that any future stage retirement would | Land it in Package 2 regardless |
