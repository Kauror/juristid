# 0130 — `Uus teema` is a lighter intake, and `Õigusakt` guides `Hetkeseis`

**Status:** accepted, amended 2026-10-02 and 2026-10-09
**Date:** 2026-10-01

The product owner's decisions after the latest round of lawyer feedback on the
creation screen. **No migration**, no new model, no new `Matter` field, no new
classification, no change to what any stored value means, and nothing rewritten
on a historical record.

1. **A new order.** Pealkiri; Saatja | Vastutaja; Arvamuse tähtaeg | Menetluse
   link | Saabus; Õigusakt; Valdkonnad; Hetkeseis; Märkmed; Failid; the button.
2. **`Millest teema räägib` is asked after creation, not during it.** The field
   leaves `Uus teema`; `Matter.brief_summary` stays, and the Teema page and
   `Muuda teemat` write it as before.
3. **`Nimetus` is no longer asked for a `Menetluse link`.** The address is the
   whole answer on `Uus teema` and `Muuda teemat`; `ProceduralLink.label` and
   every stored name stay.
4. **`Hetkeseis` gains visual guidance from `Õigusakt`** — two states, normal
   and dimmed; nothing hidden, disabled, refused or rewritten.
5. **The matrix is the owner's**, one reviewed table keyed by stable keys.
6. **`Valdkonnad` are not guided by anything.**
7. **Several instruments combine by union.**
8. **Guidance is `Uus teema` only, and presentation only.**
9. **Machine suggestions are a later decision**, not this one.

---

## Context

The lawyers reported that `Uus teema` still asked more than they could answer at
the moment a file arrives. Two questions in particular were answered badly or
not at all at intake:

* **`Millest teema räägib`** — a plain-language summary of a file nobody has
  read yet. Written at intake it is either empty or a guess, and a guess on the
  record is worse than a blank. The owner's rule: *create the dossier first;
  describe it later.*
* **`Nimetus`** beside `Menetluse link` — a second name for an address. In
  practice it repeated the Teema's own title or the proceeding's number that the
  address already carries, and no surface needed it to tell links apart.

The order was also the product of successive rounds rather than of the intake
itself: the files and `Saabus` near the top, the people in the middle,
`Õigusakt` last among the classifications and `Arvamuse tähtaeg` alone at the
very bottom. The owner rearranged the page to follow what a lawyer actually
knows, in the order they know it.

And one question had no help at all: `Hetkeseis` offers ten stages, and which
of them are even plausible depends on the instrument. A regulation does not go
to the Riigikogu; an EU consultation is not «Valitsuses». The lawyers asked for
the page to show this — without stopping them from recording the real exceptions
that do happen.

## Decision

### 1. The order

```
PEALKIRI                                      (full width)
SAATJA                       | VASTUTAJA
ARVAMUSE TÄHTAEG | MENETLUSE LINK ........ | SAABUS
ÕIGUSAKT
VALDKONNAD
HETKESEIS                                     (guided, §4)
MÄRKMED
FAILID
[Loo teema] [Loobu]
```

**Superseded on 2026-10-02 for the order of the classifications — see the
amendment at the end of this document.** `HETKESEIS` now comes directly after
`ÕIGUSAKT`, and the subject-area row follows it, headed `VALDKOND`.

* **Saatja left, Vastutaja right** (`.createform__pair--sender`). Its own
  modifier rather than `--people` reversed: `Muuda teemat` and `Saabunud` keep
  `--people` the other way round, and this order is a decision about this page.
  The organisation picker, its search, its typed-name creation and its refusal
  of an ambiguous spelling are unchanged.
* **The arrival row** (`.createform__trio--dates`): two compact dates and the
  address between them, which takes the remaining width. It holds one row down
  to 721px and stacks below that; Saatja | Vastutaja likewise.
* **`Arvamuse tähtaeg` moves up, and means exactly what it meant.** It still
  records `Matter.response_deadline` and still establishes the canonical
  `Koostan arvamuse` step once (docs/adr/0094 §5); it is still not `Järgmiseks`
  (0094 §6). What changes is where it is asked: with the other two facts that
  arrive with the file, not as the last question.
* **`Õigusakt` first among the classifications**, because it is what a lawyer
  knows first and what §4 reads. This reverses 0090 §7's placement (Õigusakt
  last) on this page. *(Amended 2026-10-02: `Hetkeseis` is now directly under
  it.)*
* **`Märkmed` second-last and `Failid` last.** `Failid` is moved, not changed:
  the same input, the same server allowlist in `accept` (signed containers
  included, docs/adr/0125), the same staged-upload island, the same
  refusal-held files and one history operation per upload. The intake reader's
  panel follows the files that produce it, directly under them.

### 2. `Millest teema räägib` is deferred, not deleted

* `MatterCreateForm` no longer declares `brief_summary`, so the page does not
  render it, the form does not require it, and a forged `brief_summary=` in a
  POST binds to nothing. `matter_create` passes `brief_summary=""` explicitly.
* `Matter.brief_summary` is untouched: the column, its audit events, the Teema
  page's own summary editor, `Muuda teemat`, search and the similar-matters
  engine all read and write it exactly as before. No historical value is
  touched.
* The similar-matters panel on `Uus teema` stops listening to and posting a
  field that is not on the page; the endpoint still reads `brief_summary` for
  callers that have one.
* **This narrows 0096 §1 on one field.** «The two forms ask exactly the same
  questions» now has one deliberate exception: `brief_summary` is a fact
  `Muuda teemat` asks and `Uus teema` does not
  (`tests/test_teema_live_audit_round_2.py` `EDIT_ONLY`).

### 3. `Nimetus` is not a normal product concept any more

* `ProceduralLinkCreateForm` — and therefore `MatterLinkForm`, which inherits it
  — no longer declares `label`. `Uus teema` and `Muuda teemat` draw one box,
  labelled `Menetluse link`, from the one shared partial
  (`procedural_link_create.html`, now a field rather than a row so each page
  places it).
* **Storage stays.** `ProceduralLink.label` is not dropped, emptied or
  migrated; every historical name is still shown on the Teema page.
* **A stored name is carried, never lost.** A correction through
  `MatterLinkForm` passes the row's own `label` through unchanged
  (`_save_procedural_link`), so an absent box never reads as «empty it».
* **A new link carries no name, and none is made up** — no title read off the
  address, no fetch, no hostname rule.
* **The row's own correction form on the Teema page's `Menetluse lingid` card
  keeps `Nimetus`** (`Muuda` beside each link, `ProceduralLinkEditForm`). It is the correction surface for existing rows,
  and the one place a historical name can still be corrected or cleared
  deliberately. Withdrawing it there, and eventually dropping the column, is a
  separate schema decision for later; nothing in this ADR depends on it.
* This narrows 0094 §4 («an address, and a name for it») and 0089's optional
  name to: an address.

### 4. `Õigusakt -> Hetkeseis` guidance

Once an `Õigusakt` is ticked, the `Hetkeseis` chips that do not normally fit
**any** ticked instrument are drawn dimmed.

* **Exactly two visual states for an unselected chip: normal and dimmed.** No
  «recommended», no ranking, no score, no third state.
* **Nothing ticked: nothing dimmed.**
* **Dimmed is not disabled.** The chip is still a radio, still in the tab order,
  still clickable; hover and keyboard focus bring it to full strength. No
  `disabled`, no `aria-disabled`, no `hidden`, no `tabindex="-1"`. The dimming
  is muted text and a fainter outline at legible contrast — «less likely», not
  «unavailable».
  **Superseded on 2026-10-02 for the strength of the dimming — see the
  amendment at the end of this document.** The words are now
  `--text-atypical`, 20% darker than `--text-muted`.
  **Superseded again on 2026-10-09 for the dark value — see ADR 0147's
  amendment of 2026-10-09.** The dark words are AA on the page (4.59:1) and
  still quieter than `--text-muted` (5.90:1).
  **Superseded again on 2026-10-09 for the outline and both values —
  see the amendment of 2026-10-09 («not colour alone») at the end of this document.** The
  outline is dotted, the words are 5.2:1 in both themes, and the dimming is
  also said in words inside the chip's explanation.
* **Chosen wins.** A chosen chip looks like any chosen chip, whether or not it
  is atypical: the stylesheet stops dimming at `:checked`
  (`.chip--atypical .chip__input:not(:checked) + .chip__name`).
  **Narrowed on 2026-10-09 — see the amendment of that date («not colour alone»).** Colour
  and weight still follow `:checked`; a chosen atypical chip keeps its dotted
  edge and its explanation's note, so a deliberate exception stays
  recognisable.
* **Nothing is ever cleared or changed.** Ticking or unticking an instrument
  re-dims immediately and never touches the chosen stage, including one that
  has just become atypical.
* **No warning, no confirmation, no validation message** when an atypical
  stage is chosen or saved.
* `Määramata` (no stage) and `Muu` (`other`) are never dimmed.
* With scripting off nothing is dimmed — the guidance is absent, and the form is
  exactly as it was.

### 5. The matrix

`app/workflow/stage_guidance.py` — `TYPICAL_STAGES_BY_INSTRUMENT`, a mapping of
`LegalInstrumentType.key` to the `StageVocabulary.key`s that stay normal, as the
owner approved it. `Määramata` and `other` are normal everywhere.

| Õigusakt | Normal stages (besides `Määramata`, `other`) |
| --- | --- |
| `vtk` | `idea` |
| `seadus` | `idea`, `consultation`, `government`, `parliament`, `awaiting_entry`, `in_force` |
| `maarus` | `idea`, `consultation`, `government`, `awaiting_entry`, `in_force` |
| `koja-ettepanek` | `idea` |
| `strateegia-arengukava-tegevuskava` | `idea`, `consultation`, `government` |
| `muu-siseriiklik` | `idea`, `consultation`, `government`, `parliament`, `awaiting_entry`, `in_force` |
| `eli-konsultatsioon` | `estonian_eu_position`, `eu_procedure` |
| `direktiiv` | `estonian_eu_position`, `eu_procedure`, `awaiting_entry`, `awaiting_transposition` |
| `el-maarus` | `estonian_eu_position`, `eu_procedure`, `awaiting_entry`, `in_force` — **not** `awaiting_transposition`: a regulation is directly applicable |
| `muu-eli-dokument` | `estonian_eu_position`, `eu_procedure`, `awaiting_entry`, `in_force`, `awaiting_transposition` |

* **Keys, never labels.** A label can be reworded by the department without
  touching the matrix; `tests/test_stage_guidance.py` holds the matrix to both
  vocabularies' keys and fails first if either gains or loses one.
* **One copy.** The server serialises the matrix into the page with
  `json_script` (`#hetkeseis-juhis`); the inputs carry `data-instrument-key`
  (`LegalInstrumentCheckboxSelect`) and `data-stage-key` (`StageRadioSelect`);
  `bindStageGuidance` in `static/js/app.js` toggles `chip--atypical`. No
  database table, no network call, no persistence, no AI.
* **An instrument without a row dims nothing.** Missing guidance must never read
  as guidance that something is unusual — a type added to the vocabulary later
  is unguided until the owner gives it a row.

### 6. `Valdkonnad` are not guided

No policy area is dimmed, filtered, suggested, recommended or ticked because of
an `Õigusakt`, a title or a sender. Every area is exactly as selectable as
before.

### 7. Union, not intersection

A stage stays normal when it is normal for **at least one** ticked instrument,
and is dimmed only when it is atypical for **every** one.
**Narrowed on 2026-10-09** — one `Õigusakt` is chosen at a time since ADR
0070's amendment of that date; the union now applies to a historical pair kept
by «Jäta alles» on `Muuda teemat`. One living Teema can
legitimately span `ELi direktiiv` and the `Seadus` transposing it, or a `VTK`
and the law that follows it; an intersection would dim exactly the stages such
a file moves through.

### 8. Presentation only, and `Uus teema` only

* **The server accepts every valid combination exactly as before.** Nothing
  in the matrix is imported by a form's `clean`, a service or a model; no
  combination is refused, normalised or rewritten on save.
* **No inference.** Nothing writes `Matter.stage` or `Matter.track` from the
  matrix. `Õigusakt` describes the instrument and `Menetlusliik` the procedure,
  and neither is derived from the other (docs/adr/0090 §4).
* **`Muuda teemat` does not draw the guidance.** It is a correction surface for
  files whose real history may be exactly the exception; the keys on its inputs
  are inert there.
  **Superseded on 2026-10-09 — see the amendment of that date («Muuda
  teemat»).** The edit page draws the same guidance from the same matrix; it is
  still presentation only and touches no stored value. Its own order is also deliberately left as it was — 0096 §1's
  «`Uus teema` is the master» is narrowed accordingly for order: the edit page
  keeps its order until the owner decides otherwise.

### 9. Not now: machine suggestions

A later generation of intake help may suggest `Õigusakt`, `Valdkonnad` or other
classifications from the title, the sender or the uploaded documents' text. None
of it is built here: no title keyword classifier, no sender classifier, no file
text extraction for classification, no LLM, no auto-selection. The seam it
would use is the same one this ADR uses — stable keys on the inputs, guidance
as a presentation layer over a form the server validates on its own — so it can
be added beside §4 without changing it.
**Narrowed on 2026-10-09, awaiting the product owner's approval — see the
amendment of that date «a stage chosen from the instrument».** One rule only:
an instrument whose matrix row names exactly one stage chooses it on an empty,
untouched `Hetkeseis` on `Uus teema`.

## Alternatives considered

* **Disable or hide atypical stages.** Rejected: real files are exceptions, and
  a lawyer who files one is right about it. A disabled chip also tells assistive
  technology that the option does not exist.
* **Three states (recommended / neutral / unusual).** Rejected by the owner as
  more than the page needs; two states answer «is this likely?».
* **Intersection for several instruments.** Rejected: it dims the stages a
  directive-plus-transposition file actually passes through.
* **Validate atypical combinations server-side.** Rejected: this is guidance,
  and the data model already allows every combination for good reason.
* **Drop `ProceduralLink.label`.** Deferred: a schema change and a historical
  rewrite are not needed to stop asking the question.
* **Delete `brief_summary` from the model.** Rejected: the summary is a real
  fact about a Teema; it is asked later, not abolished.

## Consequences

* `Uus teema` asks two questions fewer and reads in the order a file arrives.
* `MatterCreateForm` has no `brief_summary`; `ProceduralLinkCreateForm` and
  `MatterLinkForm` have no `label`.
* The visual baselines `uus-teema` and `uus-teema-viga` change by design (new
  order, two fields fewer).
* Amends: 0089 (the link's optional name), 0090 §7 (Õigusakt last), 0094 §4
  (address and name) and §5 (the deadline as the last question; its meaning
  stands), 0096 §1 (`brief_summary` edit-only; the edit page keeps its order).

## Reversibility

Entirely. No migration and no data changed: restoring a field to a form, or
moving a row in a template, brings any of it back; deleting
`app/workflow/stage_guidance.py` and the `#hetkeseis-juhis` script removes the
guidance without touching a stored value.

---

## Amendment, 2026-10-02 — Hetkeseis under Õigusakt, «Valdkond», and a stronger dim

- Status: accepted, amending §1's order of the classifications, §4's
  «muted text … at legible contrast», and §2's both-forms consequence for one
  more field's wording.
- Scope: `Uus teema` only. Presentation only — no field, model, key, rule,
  migration or stored value changes.

### What was decided before

The classifications read `Õigusakt · Valdkonnad · Hetkeseis` (§1). A dimmed
`Hetkeseis` chip used `--text-muted` (#7d8b99, 5.31:1 against the page) with
the subtle border (§4). Both forms headed `policy_areas` «Valdkonnad».

### Why it is superseded

The owner reviewed the deployed page. `Hetkeseis` is the field the `Õigusakt`
answer guides, and with `Valdkonnad` between them the guidance appeared a whole
row away from the click that caused it. And the dimmed chips read too close to
ordinary ones at a glance — the distinction §4 exists to make was there, but
had to be looked for.

### What is decided now

1. **`Õigusakt · Hetkeseis · Valdkond`.** The guided field sits directly under
   the field that guides it; the subject area follows. A lawyer chooses the
   instrument and immediately sees which stages read normal and which dimmed.
2. **«Valdkond», in the singular,** as the heading on `Uus teema`
   (`MatterCreateForm.__init__` sets the label; it is the owner's wording for
   the question). The field is still `policy_areas`, still several values,
   still a count beside the heading; nothing is renamed below the label.
   `Muuda teemat` keeps «Valdkonnad» until that page is decided on its own —
   recorded as the one wording difference in
   `tests/test_teema_live_audit_round_2.py` (`CREATE_ONLY_WORDING`).
3. **The dimmed state is 20% stronger.** A new token, `--text-atypical`
   (#646f7a dark, #828d96 light), is `--text-muted` taken 20% further down; the
   «i» marker of an unselected dimmed chip follows its words. The normal chip,
   the chosen chip, hover, focus, cursor, hit area and the subtle border are
   unchanged. **The contrast falls from 5.31:1 to 3.61:1** against the page —
   readable, and restored to full strength on hover and keyboard focus, but
   below WCAG AA's 4.5:1 for text of this size. That is the owner's deliberate
   trade for an at-a-glance difference on an option that is still offered, and
   it is recorded here so that it is revisited as a decision rather than found
   as a defect.
   **Superseded on 2026-10-09 for the dark value — see ADR 0147's amendment of
   2026-10-09.** Revisited as that decision: the owner approved dark
   `--text-atypical` #74808d, 4.59:1 on the page (AA), still quieter than
   `--text-muted` (now #8693a1, 5.90:1); everything else in this item stands.

### What this amendment does not change

- The matrix, its stable keys, union semantics, `Määramata` and `Muu` never
  dimmed, and the rule that missing guidance dims nothing (§4–§7).
- No option disabled, hidden, refused, cleared or rewritten; the server accepts
  every combination (§8); no `Matter.stage` or `Matter.track` inference.
- A chosen chip looks exactly like any chosen chip — the dimming still stops at
  `:checked`.
- `Valdkond` is not guided, filtered, suggested or inferred (§6), and it stays
  a multi-select over the same governed vocabulary with the same `Muu`.
- Every other §1 placement: Pealkiri; Saatja | Vastutaja; Arvamuse tähtaeg |
  Menetluse link | Saabus; `Märkmed` then `Failid` last. §2 and §3 stand.
- No migration.

---

## Amendment, 2026-10-09 — the dimmed chip is not colour alone, and a tooltip fits a phone

- Status: accepted, amending §4's «muted text and a fainter outline» and its
  dimmed-state value (as already amended on 2026-10-02 and by ADR 0147's
  amendment of 2026-10-09), and the Hetkeseis tooltip's placement (ADR 0032,
  Uus teema redesign §8).
- Scope: presentation, plus one sentence in the guidance payload. The matrix,
  its keys, its union rule and everything §8 says about validation and
  inference are unchanged. No model or schema change; the `Hetkeseis`
  explanations corrected in the same round are a data migration of their own,
  recorded in ADR 0032's amendment of this date.

### What was decided before

A dimmed chip was quieter words (`--text-atypical`, 4.59:1 dark, 4.51:1 light
on the page) and a fainter solid outline (`--border-subtle`, 1.17:1 dark,
1.14:1 light). The words had been taken as far down as AA allows because the
colour step alone had to be visible at a glance (2026-10-02). A chosen chip
looked exactly like any chosen chip. The tooltip measured its right edge only
and, when that overflowed, re-anchored to the chip's right edge.

### Why it is superseded

The live UI QA of 9 October 2026 measured all of it:

* **The distinction was colour alone**, the words sat a hair over AA with no
  margin, and the outline (1.14–1.17:1) all but vanished — a dimmed chip read
  as half drawn rather than as quieter.
* **A screen reader and a keyboard got nothing.** Hover and focus restore full
  strength, so the one moment a keyboard user reaches the chip is the moment
  the cue disappears, and nothing was said in words.
* **A chosen atypical stage was indistinguishable** from a chosen typical one,
  so a deliberate exception could not be recognised afterwards.
* **At 320px and 375px the Hetkeseis bubble opened off the left edge** — up to
  189px, so most of the explanation was unreadable. The right-edge flip anchored
  a bubble as wide as the screen to a chip in the middle of it.

### What is decided now

1. **Two cues, one of them not colour.** A dimmed chip's outline is **dotted**
   (`border-style`, in `--border-strong`: 2.0:1 dark, 2.7:1 light — visible, and
   broken into dots no heavier than an ordinary chip's solid edge). Dotted,
   never dashed: dashed is this application's «not saved yet».
2. **The words keep a margin.** `--text-atypical` is #7c8996 dark (5.18:1 on the
   page) and #5d6873 light (≥5.16:1 on every chip surface), still quieter than
   `--text-muted` (by 1.14 and 1.08). `tests/test_text_contrast.py` holds it to
   `ATYPICAL_MINIMUM` 5.1 and `QUIETER_BY` 1.07; the at-a-glance difference of
   2026-10-02 is now carried by the outline as well as the words.
3. **The dimming in words.** `ATYPICAL_STAGE_NOTE` — «Valitud õigusakti puhul
   tavaliselt ei kasutata, kuid valida võib.» — travels in the guidance payload,
   and `bindStageGuidance` writes it into an empty `.stagehelp__note` inside the
   chip's explanation while the stage is atypical. The radio is described by
   that explanation (`aria-describedby`), so a screen reader hears it, and the
   bubble shows it on hover and focus. A description, not a warning: it says
   the stage may be chosen.
4. **A chosen atypical chip stays recognisable.** It takes the ordinary chosen
   look (§4's «chosen wins» for colour and weight still holds) but keeps its
   dotted edge and its note. Nothing refuses, warns or asks to confirm.
5. **A tooltip slides, it does not flip.** `bindStageHelp` measures the open
   bubble and moves it sideways by exactly its overflow, left edge first, with
   an 8px margin; the stylesheet's `max-width` (`min(22rem, 100vw - 40px)`)
   guarantees it fits a window with a desktop scrollbar. Hover, focus, touch and
   Escape behave as before; with scripting off the bubble opens where it did.
   `e2e/test_matter_form_ux.py` checks every bubble at 1440–320px by pointer and
   at 375/320px by keyboard in both themes.

### Open product question — EU regulations that need Estonian implementing measures

The QA found the department's explanations inconsistent with the matrix (F3):
«Jõustunud» told the lawyer, for every EU act, to choose it only when Estonia
need not change its own law, which for a regulation points at «ELi õiguse
ülevõtmise ootel» — the stage §5 deliberately dims for `el-maarus`, because a
regulation is directly applicable and is not transposed. The words are
corrected (ADR 0032's amendment of this date); **the matrix is not changed**.

What the vocabulary cannot say on the regulation's own Teema is «in force, and
Estonian implementing measures are still pending». Concrete case: an EU
regulation that obliges Member States to designate a competent authority and
lay down penalties (the AI Act, Regulation (EU) 2024/1689, is one) enters into
force and applies directly, while the Estonian act doing those two things is
still a draft. Today that is «Jõustunud» on the regulation's Teema, with the
Estonian act followed as a `Seadus`/`Määrus` Teema of its own, linked under
«Seotud teemad». The owner decides whether that is enough, or whether the
Chamber needs either (a) «ELi õiguse ülevõtmise ootel» broadened to
«…ülevõtmise või rakendamise ootel» and undimmed for `el-maarus`, or (b) a new
stage. Neither is built until then.

### What this amendment does not change

- The matrix, the stable keys, union semantics (§7), `Määramata` and `Muu`
  never dimmed, and missing guidance dimming nothing.
- Nothing disabled, hidden, refused, cleared or rewritten; the server accepts
  every combination (§8); no `Matter.stage` or `Matter.track` inference.
- Hover and keyboard focus still bring the words to full strength.
- The ordinary chip, the chosen chip's fill and weight, focus ring, cursor and
  hit area.
- Escape still closes a bubble until the pointer or focus leaves the chip.

---

## Amendment, 2026-10-09 — «Muuda teemat»: the same guidance on the edit page

- Status: accepted, amending §8's «`Uus teema` only» and narrowing §7 to the
  held pairs ADR 0070's amendment of the same day keeps.
- Scope: presentation on `Muuda teemat` (F5 of the owner's brief of
  2026-10-09). No validation, no inference, no stored value.

### What was decided before

§8: the edit page did not draw the guidance; it is a correction surface whose
files may be exactly the exception.

### Why it is superseded

The live QA of 9 October 2026 found the two pages disagreeing about the same
pair of fields: a lawyer who learned the dimming on `Uus teema` met none of it
when correcting a Teema, and read the absence as «every stage fits».

### What is decided now

- `Muuda teemat` wraps its `Hetkeseis` row in the same `data-stage-guidance`
  and serialises the same `stage_guidance_payload()` (`_edit_context`); one
  script (`bindStageGuidance`) and one matrix serve both pages.
- A **stored stage is never touched**: dimming is a class and a described note
  (this ADR's «not colour alone» amendment), and a chosen atypical stage stays
  chosen. Opening and saving the page moves nothing that was not changed.
- A **closed file's stage is stated, not offered** (RULE-03): it has no chips,
  so there is nothing to dim.
- The **order** of the edit page is unchanged (`Valdkond`, `Hetkeseis`,
  `Õigusakt`); the guidance does not depend on order.

### What this amendment does not change

- The matrix, its keys, `Määramata` and `Muu` never dimmed, missing guidance
  dimming nothing; nothing disabled, hidden, refused or rewritten.
- The server accepts every valid combination on both pages.

---

## Amendment, 2026-10-09 — a stage chosen from the instrument (awaiting the owner's approval)

- Status: **proposed, awaiting the product owner's approval before merge**;
  amends §9's «no auto-selection» for one rule, and §8's «Nothing writes
  `Matter.stage` from the matrix» only in the sense that the page chooses a
  radio the lawyer then saves — the server still infers nothing.
- Scope: `Uus teema`'s script. No model, no validation, no server-side
  inference; `Muuda teemat` never chooses a stage.

### What was decided before

§9: no auto-selection of any classification. The guidance dims; it never
chooses.

### Why it is proposed

The owner asked for `Õigusakt` to fill `Hetkeseis` where that is safe. For most
instruments it is not: a `Seadus` can be anywhere from an idea to in force, and
choosing the first stage would record a fact nobody stated.

### What is proposed

1. **One rule, read off the approved matrix**
   (`prefill_stage_by_instrument`): an instrument whose normal stages, apart
   from `Muu` and `Rohkem ei tegele`, are exactly one stage chooses it. Today
   that is `VTK` → `Idee` and `Koja ettepanek või pöördumine` → `Idee`; every
   other instrument chooses nothing, and the dimming alone guides.
2. **Only an empty, untouched `Hetkeseis`.** The page must have arrived with
   «Määramata» chosen (a refused save holding a stage counts as touched), and
   no person may have chosen a stage since. Any choice a person makes — a stage
   or «Määramata» — ends the prefill for that page for good, so a stage the
   lawyer cleared is never put back.
3. **It follows the instrument while it is the page's own choice.** Choosing
   an instrument that does not decide the stage, or «Määramata» in `Õigusakt`,
   takes back a stage the page chose — and only that one.
4. **It says so.** «Hetkeseis valiti õigusakti järgi — muuda, kui teema on
   mujal.» appears beside the row, politely announced, while the choice is the
   page's; it goes the moment the lawyer chooses.
5. **The server is unchanged.** The radio is an ordinary answer; what is saved
   is what the page shows. With scripting off nothing is chosen.

### Alternatives for the ambiguous instruments, if the owner wants more

| option | consequence |
| --- | --- |
| choose the first typical stage (`Seadus` → `Idee`) | records an idea stage for a draft already before the government; wrong for most incoming drafts |
| choose by the sender (a ministry → `Kooskõlastusringil`) | inference from a second field the matrix does not cover; a new rule to review |
| choose nothing (this proposal) | the lawyer picks among the highlighted typical stages, as today |

### What this amendment does not change

- The matrix, its keys, the dimming, the note, union for a historical pair.
- `Muuda teemat` and every stored stage.
- The server's acceptance of every combination.
