# Õigusakt on Uus teema — UI design

Design-only specification. No application code, tests, migrations or PR #169 changes are part of this deliverable.

> **Implementation note, added when this file was committed (2026-09-10).** The
> file below is the design handoff, unchanged. What it deliberately left to the
> technical specification has since been settled in
> [ADR 0070](adr/0070-oigusakt-is-a-canonical-matter-field.md), and the answers
> to the questions this document names are:
>
> | Left open here | Settled as |
> | --- | --- |
> | single vs multi | **multi** — so the checkbox form of §4 is what shipped, with the `field__count` and the `chip__clear` marks |
> | the vocabulary | seventeen reviewed `LegalInstrumentType` rows derived from the register's own 58 spellings; the `[Seadus][Määrus]…` labels in §6 were layout placeholders and the real row is longer |
> | field name | `legal_instruments`, with `legal_instrument_other` beside it |
> | the free-text label | **`Õigusakti liik`** |
> | whether the free text is required when `Muu` is ticked | **yes**, refused on the empty box |
> | legacy `legal_instrument_raw` | preserved unchanged; the canonical reading is a separate seam and no backfill was run |
> | whether the Teema detail page shows it | not in that branch — the field is editable on `Muuda teemat`, and the detail page is untouched |
>
> One difference from §2's stated scale is worth naming rather than leaving to
> be noticed: the reviewed vocabulary is **17 chips, not 8–12**. Everything
> §11 and §15 require still holds — the row is a plain full-width
> `.createform__row`, the chips wrap, nothing is truncated and nothing scrolls
> sideways — and Valdkonnad on the same form carries 22.

- **Target page:** `/teemad/uus/`
- **Designed against:** `Kauror/juristid` PR #169, head `04e467529bd795900c2382a2724e6253036bfa78`
- **Template:** `templates/matters/matter_create.html` @ that head
- **Styles:** `static/css/app.css` (`.createform*`, `.chip*`, `.chipdetails*`, `.field*`), `static/css/tokens.css`
- **Scripts:** `static/js/app.js` (`data-chipcount-for`, `bindOpenChosenDetails`, chip-reveal wiring)

Out of scope, to be settled by the technical/product specification: the exact vocabulary, single-vs-multi storage semantics, what `Muu` stores, legacy mapping, model, migration, import, reporting, search. This file decides **only where the field sits and how it looks and behaves.**

---

## 1. Current page inspected

Yes — PR #169 head, template + CSS + the row-composition e2e contract (`e2e/test_uus_teema_row_composition.py`).

The page is one `form.createform` (max-width **1060px**) built from stacked `.createform__row` elements separated by a single `1px solid var(--border-subtle)` hairline, `11px 0` padding. Rows are either full-width (one field) or a grid (`.createform__pair` / `.createform__trio`), and **every** grid row collapses to one column at `max-width: 1080px`.

Row sequence at PR #169 head:

| # | Row | Composition |
|---|---|---|
| 1 | Pealkiri | `--first` (no hairline, no top padding) |
| 2 | Lühikokkuvõte + Märkmed | `pair--summary` — `1fr / 320px` |
| 3 | Failid + Saabus + Arvamuse tähtaeg | `trio` — `1fr / 146px / 146px` |
| 3b | Intake panel | `--intake`, `display:none` while empty |
| 4 | Vastutaja + Saatja | `pair--people` — `246px / 1fr` |
| 5 | **Valdkonnad** | full width · checkbox chips, all ~22 visible, `Muu` chip + reveal |
| 6 | **Hetkeseis** | full width · radio chips, `chip--explained` tooltips |
| 7 | **Menetlusliik** | full width · radio chips |
| 8 | Adressaat | full width · `chipdetails--field`, folded by default (ADR 0069) |
| 9 | Järgmine tegevus | `--panel` |
| 10 | Actions | `Loo teema` · `Loobu` · note |

Three facts matter for this design:

1. **Every classification field already owns a full-width row.** Valdkonnad, Hetkeseis and Menetlusliik are each one row of wrapping chips. That is the page's grammar for "pick from a vocabulary".
2. **Control shape is a promise about the data** (stated in the template's own header comment, ADR 0025): radio chips where the model holds one value, checkbox chips where it holds several. Multi-select fields additionally carry a `.field__count` beside the legend and a `.chip__clear` `×` inside each chip; single-value fields carry neither.
3. **The vocabulary is not hidden.** Valdkonnad shows 22 labels rather than 10 + "Veel 15", on the reasoning that a disclosure over a vocabulary that size hides half of it, and choosing which half means ordering by usage — which the control deliberately stopped doing.

---

## 2. Design problem

Add a new field, label exactly **`Õigusakt`**, approximately 8–12 short options, possibly multi-select, such that:

- it reads as one of the Matter's classification facts, not as a new form section;
- it is never mistaken for a second way of answering **Menetlusliik**;
- it carries roughly Menetlusliik's weight — less than Pealkiri, Saatja or the files;
- it costs the page as little height as honestly possible;
- it introduces no new visual component and no new CSS.

---

## 3. Chosen placement

> **A new full-width `.createform__row` immediately after the Menetlusliik row and immediately before the Adressaat row — i.e. inserted between the `{{ form.track }}` fieldset row and the Adressaat `chipdetails--field` row. Row 7b in the table above.**

Reasons, in order of weight:

1. **It matches the page's existing grammar exactly.** The three classification rows are full-width single-field rows. A fourth one is invisible as a change — which is the stated goal.
2. **It does not split the process pair.** Hetkeseis (*where the procedure stands*) and Menetlusliik (*what kind of procedure*) are consecutive by design; the template groups them under one comment. Inserting Õigusakt between them would break an intentional adjacency to solve nothing.
3. **Full width is what 8–12 chips need.** See §4 for the rejected pair layout.
4. **It sits before the counterparty question**, so the classification block reads as a run of four and the row order stays: *what it's about → where it stands → what kind of process → what kind of instrument → who we answer*.

### Why not share a row with Menetlusliik

Considered and rejected: `.createform__pair` holding Menetlusliik + Õigusakt.

At the form's 1060px max width each column would be ~521px. Menetlusliik's labels are long — «ELi õiguse ülevõtmine», «Strateegia või arengukava», «Rakendamine või järelevalve» — and today wrap into roughly two rows at full width; in half a row they wrap into three or four. Õigusakt's 8–12 short chips would take two to three. The pair therefore **increases** the height of this area rather than saving it, cramps a field this task must not redesign, and below 1080px it stacks anyway — producing two narrow columns on desktop and the same stacked result on every smaller width. It also visually equates the two fields as two halves of one question, which §3 of the brief explicitly forbids.

---

## 4. Chosen control

> **Checkbox chips: `fieldset.field` + `legend.field__label` + `div.chiprow` of `label.chip`, all options visible, plus a `chip--other` `Muu` chip that reveals a `.createform__other` free-text box. No disclosure. No new CSS classes.**

This is the **Valdkonnad** control, at a smaller vocabulary. It is already the page's multi-select pattern and it already carries the two affordances that mark multi-select:

- `<span class="field__count" data-chipcount-for="…">` beside the legend — filled by `app.js`, absent without scripting, `display:none` when empty;
- `<span class="chip__clear" aria-hidden="true">×</span>` inside each `chip__name`, which appears on a selected chip and says "this one can be taken off again".

Neither appears on Hetkeseis or Menetlusliik, because those hold one value. **That asymmetry is the whole answer to "don't let Õigusakt and Menetlusliik look like one question split in two"** — the two rows are visibly different kinds of control, answered independently, with no explanatory prose added anywhere.

If the technical specification lands on single-select instead, the same row becomes radio chips: drop the `field__count`, drop every `chip__clear`, keep placement, legend, spacing and `Muu` unchanged. Nothing else in this document changes.

### Markup shape (conceptual — mirrors the Valdkonnad block verbatim)

```
<div class="createform__row">
  <fieldset class="field">
    <legend class="field__label">
      Õigusakt
      <span class="field__count" data-chipcount-for="<field name>"></span>
    </legend>

    <div class="chiprow">
      <!-- one per canonical option, in the department's reviewed order -->
      <label class="chip">
        <input class="chip__input" type="checkbox" name="…" value="…">
        <span class="chip__name">Seadus<span class="chip__clear" aria-hidden="true">×</span></span>
      </label>
      …

      <label class="chip chip--other" id="oigusakt-muu">
        <input class="chip__input" type="checkbox" name="…">
        <span class="chip__name">Muu<span class="chip__clear" aria-hidden="true">×</span></span>
      </label>
    </div>

    <div class="createform__other" id="oigusakt-muu-tekst" hidden>
      <label class="field">
        <span class="field__label"><!-- free-text label: technical spec decides --></span>
        <input class="field__input field__input--compact" type="text">
        <span class="field__error">…</span>   <!-- when refused -->
      </label>
    </div>

    <span class="field__error">…</span>       <!-- when refused -->
  </fieldset>
</div>
```

Field names, the free-text label and whether `Muu` reveals anything at all are the technical specification's to decide. The **visual contract** is: if it reveals, it reveals into `.createform__other` directly under the chip row, `max-width: 30rem`, `margin-top: var(--spacing-3)` — exactly as `Valdkond · Muu` does today.

---

## 5. Information hierarchy

From most to least weight on the finished page:

1. **Pealkiri** — `field__input--prominent`, warm ground, brand border, halo. Untouched.
2. **Failid / Saabus / Arvamuse tähtaeg**, **Vastutaja / Saatja** — paired rows, wider tracks.
3. **Valdkonnad · Hetkeseis · Menetlusliik · Õigusakt** — four peer rows of chips, identical label treatment (`11px`, `.08em` tracking, `--text-secondary`), identical `11px 0` row padding, one hairline each. **Õigusakt is a peer of Menetlusliik, by construction: same row type, same legend style, same chip size.**
4. **Adressaat** — folded to a single pill.
5. **Järgmine tegevus** — the one panel.

Õigusakt adds **one hairline and one wrapped chip row** to the page. No card, no panel, no background, no border of its own, no heading.

---

## 6. Before / after composition

### BEFORE — PR #169, Valdkonnad → Adressaat

```
────────────────────────────────────────────────────────────── hairline
VALDKONNAD  ·3
[Keskkond ×][Maksud ja toll][Töösuhted, töökeskkond][Energeetika]
[Riigihanked][Ehitus ja planeerimine][Transport][Finants ja pangandus]
[Kaubandus][Tarbijakaitse][Andmekaitse][Digi ja IT][Haridus][Tervishoiu]
[Põllumajandus][Konkurents][Maksundus][Riigiabi][Ettevõtluskeskkond]
[Töötervishoid][Kestlikkus][⌐Muu¬]
────────────────────────────────────────────────────────────── hairline
HETKESEIS
[Saabunud ⓘ][Kooskõlastusringil ⓘ][Valitsuses ⓘ][Riigikogus ⓘ]
[ELi menetluses ⓘ][Ootan ELi õiguse ülevõtmist ⓘ][Jõustunud ⓘ][Muu]
────────────────────────────────────────────────────────────── hairline
MENETLUSLIIK
[Riigisisene][ELi algatus][ELi õiguse ülevõtmine][Strateegia või arengukava]
[Koja algatus][Rakendamine või järelevalve][Muu]
────────────────────────────────────────────────────────────── hairline
⌐Adressaat · Kliimaministeerium ▾¬
────────────────────────────────────────────────────────────── hairline
JÄRGMISEKS  [ … ]
```

### AFTER — one row inserted, nothing else moved

```
────────────────────────────────────────────────────────────── hairline
VALDKONNAD  ·3
[Keskkond ×][Maksud ja toll][Töösuhted, töökeskkond][Energeetika]
[Riigihanked][Ehitus ja planeerimine][Transport][Finants ja pangandus]
[Kaubandus][Tarbijakaitse][Andmekaitse][Digi ja IT][Haridus][Tervishoiu]
[Põllumajandus][Konkurents][Maksundus][Riigiabi][Ettevõtluskeskkond]
[Töötervishoid][Kestlikkus][⌐Muu¬]
────────────────────────────────────────────────────────────── hairline
HETKESEIS
[Saabunud ⓘ][Kooskõlastusringil ⓘ][Valitsuses ⓘ][Riigikogus ⓘ]
[ELi menetluses ⓘ][Ootan ELi õiguse ülevõtmist ⓘ][Jõustunud ⓘ][Muu]
────────────────────────────────────────────────────────────── hairline
MENETLUSLIIK
[Riigisisene][ELi algatus][ELi õiguse ülevõtmine][Strateegia või arengukava]
[Koja algatus][Rakendamine või järelevalve][Muu]
────────────────────────────────────────────────────────────── hairline   ← NEW ROW
ÕIGUSAKT  ·1
[Seadus ×][Määrus][Eelnõu][VTK][Direktiiv][EL määrus][Otsus]
[Strateegia][Arengukava][Korraldus][Käskkiri][⌐Muu¬]
────────────────────────────────────────────────────────────── hairline
⌐Adressaat · Kliimaministeerium ▾¬
────────────────────────────────────────────────────────────── hairline
JÄRGMISEKS  [ … ]
```

Legend: `[…]` chip · `[… ×]` selected chip, clear mark visible · `⌐…¬` dashed border (`chip--other`, disclosure pills) · `ⓘ` `chip--explained` tooltip marker · `·N` `field__count`.

Chip labels in the AFTER block are **layout placeholders at the stated 8–12 scale**, not a vocabulary proposal.

Notice what the two blocks show: **Menetlusliik has no count and no clear marks; Õigusakt has both.** Two rows, two different controls, two independent answers — carried by the control shape, not by a sentence.

---

## 7. Interaction states

| State | Appearance |
|---|---|
| Rest, nothing chosen | Row of chips, `--surface-*` chip ground, `1px solid var(--border-default)` on `.chip__name`, `--text-secondary` label. Count hidden (`.field__count:empty`). |
| Hover | Existing `.chip:hover .chip__name` — border `--border-strong`, text `--text-primary`. Pointer `cursor` per existing rules. |
| Keyboard focus | Existing focus ring on the input's `:focus-visible` sibling: `var(--focus-ring-width) solid var(--focus-ring)` at `var(--focus-ring-offset-width)`. **Never suppressed.** |
| Selected | Existing `.chip__input:checked + .chip__name` treatment — accent border, accent-soft ground, `--text-primary`, and the `×` clear mark becomes visible. |
| Several selected | Every selected chip renders the same; `.field__count` beside the legend shows the number (`·3`). Order on screen never changes — chips stay in their declared vocabulary order, selected or not, so the row does not reflow under the cursor. |
| Toggling off | Click anywhere on the chip, or the `×`, or `Space` while focused. `×` is decorative (`aria-hidden`) — the whole label is the hit area, as elsewhere. |
| Long label | `.chiprow` is `flex-wrap: wrap`; a long chip wraps to the next line. Labels are never truncated and never ellipsised. |
| No scripting | Chips are `<label>` + real `<input>`, so selection works. Only `.field__count` is absent (it is written by `app.js`) — the chips themselves say which are on. The `Muu` reveal must therefore be server-rendered open when `Muu` is checked, not opened by script alone. |

---

## 8. Selected / multiple-selected state

Multiple selection needs no new affordance:

- each selected chip carries the existing checked treatment plus a visible `×`;
- the legend gains `·N` via the existing `data-chipcount-for` island;
- there is **no** summary pill, **no** token list, **no** "3 valitud" text, **no** reordering of chosen chips to the front. All four exist nowhere else on this page, and Valdkonnad — a 22-option multi-select on the same form — needs none of them.

---

## 9. `Muu` visual state

- Chip label exactly **`Muu`**, last in the row, `chip--other`: dashed border at rest (the page's consistent "opens something optional" signal), becoming solid when checked.
- Ticking it reveals `.createform__other` directly beneath the chip row: `margin-top: var(--spacing-3)`, `max-width: 30rem`, one `.field` with a `field__label` and a `field__input--compact`.
- Unticking it hides the box again. Whether the typed text is discarded or kept is a technical decision; **visually**, the box must return in the state the server rendered it.
- On a refused save with `Muu` checked, the box renders **open** (mirror the existing `{% if not form.policy_area_other_selected.value %}hidden{% endif %}` pattern).
- The free-text field's label is a placeholder in this design. It is **not** a product decision here.

---

## 10. Error state

Existing treatment only — no new warning component, no icon, no coloured panel.

- **Field-level:** `<span class="field__error">` as the last child of the `fieldset.field`, under the chip row (and under `.createform__other` if that is open). Existing style: `--typography-size-meta`, semibold, danger colour.
- **Free-text-level:** its own `.field__error` inside `.createform__other`, under the input. `.createform .field:has(.field__error) .field__input` already paints that input's border `--status-danger-border` with a `--status-danger-halo` ring — colour is never the only signal; the words are always there.
- **Nothing is hidden by an error:** the field uses no disclosure, so an error is always on screen. If `Muu` is the cause, its reveal is rendered open (§9).
- Form-level refusals continue to use the existing `p.formerror[role=alert]` at the top of the form. Untouched.

---

## 11. Responsive behaviour

The row is a **plain full-width `.createform__row`, not a grid row.** It therefore has nothing to stack and no breakpoint of its own — this is a large part of why the placement was chosen.

| Width | Behaviour |
|---|---|
| **1440 px** | Form capped at 1060px, centred. Õigusakt is one full-width row; 8–12 short chips fit in one or two wrapped lines. Label above control. |
| **1024 px** | Below the form's `1080px` breakpoint the *paired* rows above stack; **Õigusakt is unaffected** — it was already one column. Chips wrap to two or three lines. |
| **768 px** | Same. Chips wrap further. `.chiprow` gap unchanged; chip padding and font unchanged — nothing shrinks. |
| **420 px** | Same row, chips wrap to roughly four to six lines. Labels stay full size and full text. No truncation, no horizontal scroll, no `overflow-x`. |

Constraints that hold at every width: label always above the control; chips always wrap rather than scroll; no disclosure appears or disappears with width; the page never scrolls sideways (guarded by `test_the_form_never_scrolls_sideways`, which the new row must keep passing).

Height cost: **one hairline + one to two chip lines** at desktop widths.

---

## 12. Accessibility

Native semantics throughout — the same as Valdkonnad, which is the accessible baseline this reuses.

- **Grouping:** `<fieldset class="field">` + `<legend class="field__label">`. The legend text begins with the words `Õigusakt`; the `.field__count` inside it is supplementary and reads after the label.
- **Inputs:** real `<input type="checkbox">` (or `radio` if the technical spec chooses single-select) wrapped in a `<label>`. **No clickable `div`s.**
- **Keyboard:** `Tab` reaches the group; `Space` toggles the focused checkbox; with radios, arrow keys move within the group and the group is one tab stop. The `×` adds no tab stop (`aria-hidden`, no `button`).
- **Focus:** visible ring on the focused chip via the existing `:focus-visible` rule. Never removed, never replaced by colour alone.
- **Accessible name:** the chip's own words only. The `×` is `aria-hidden="true"` so it never joins the name — the same discipline the Hetkeseis tooltip enforces by keeping its bubble a *sibling* of the label and pointing at it with `aria-describedby` rather than nesting it.
- **No tooltips on this field.** Instrument names need no explanation, so no `chip--explained` marker and no `aria-describedby`.
- **Errors:** the field's `.field__error` is inside the same `fieldset`, so it is announced within the group. The free-text error sits inside its own `label.field`, associated with that input. If the technical implementation adds `aria-describedby`/`aria-invalid`, it must point at these existing elements rather than introduce new ones.

---

## 13. Exact visible copy

| Element | Text |
|---|---|
| Field label (legend) | `Õigusakt` |
| Free-entry chip | `Muu` |
| Count beside legend | `·N` — written by `app.js`, no words |
| Helper text | **none** |
| Disclosure summary | **none** — the field has no disclosure |
| Free-text label | *placeholder in this design; the technical specification names it* |

No `Vali õigusakti liik`, no explanation of the difference from Menetlusliik, no sentence about optionality. The page's rule holds: a page that shows a field is not a page that demands it, and a rule printed on every visit to explain a rare refusal is noise.

---

## 14. Existing UI that remains unchanged

Not touched by this design, in markup, style or position:

Pealkiri · Lühikokkuvõte · Märkmed · Failid and the whole dropzone/intake area · Saabus · Arvamuse tähtaeg · Vastutaja · **Saatja** · **Adressaat, including PR #169's sender-defaults-addressee behaviour and its collapsed-by-default state (ADR 0069)** · Valdkonnad · Hetkeseis and its tooltips · **Menetlusliik itself** · Järgmine tegevus panel · `Loo teema` / `Loobu` / the trailing note · form-level `p.formerror`.

Row composition is unchanged for every existing row: `pair--summary` stays `1fr / 320px`, `pair--people` stays `246px / 1fr`, `trio` stays `1fr / 146px / 146px`, the `1080px` stacking breakpoint is untouched. **No existing row is re-paired, re-ordered or re-tracked to make room** — the new field is an insertion, not a rearrangement.

**No new CSS class and no new CSS rule is required.** Every class named in §4 exists in `static/css/app.css` at PR #169 head.

---

## 15. Visual acceptance criteria

A build satisfies this design when:

1. `/teemad/uus/` renders a field whose legend text is exactly `Õigusakt`.
2. It is a direct child row of `form.createform`, positioned **after** the row containing the Menetlusliik control and **before** the row containing the Adressaat disclosure. (`.createform__row:has([name="track"])` precedes it; `.createform__row:has([data-addressee-disclosure])` follows it.)
3. That row is full width: the field's box width equals the row's content width (±2px) at 1440, 1024, 768 and 420 px.
4. The row is not a `.createform__pair` or `.createform__trio` and shares its row with no other field — the row contains exactly one `fieldset`/`label` child.
5. The control is a `fieldset.field` with a `legend.field__label` and a `div.chiprow` of `label.chip` elements, each containing a real `input` — no clickable `div` anywhere in the field.
6. All options are visible at rest at every width. The field contains no `details` element.
7. If the field is multi-select: the legend carries `span.field__count[data-chipcount-for]`, every `chip__name` contains `span.chip__clear`, and selecting three options makes the count read `·3`. If single-select: neither element is present.
8. A `Muu` chip is present, is last in the row, carries `chip--other`, and its dashed border becomes solid when checked.
9. Ticking `Muu` reveals a `.createform__other` block directly under the chip row, `max-width: 30rem`; unticking hides it; a refused save with `Muu` checked renders it already open.
10. Chip order on screen is the declared vocabulary order and does not change when options are selected.
11. Focus: tabbing to the field shows the application's standard focus ring on a chip; the ring is not suppressed or replaced.
12. Keyboard-only selection works with `Space` (checkboxes) or arrows (radios), and the `×` mark adds no tab stop.
13. With JavaScript disabled the chips still select and post; only `.field__count` is absent.
14. A validation error renders as `span.field__error` inside the field's `fieldset`, visible without any interaction; the free-text error renders inside `.createform__other` and paints that input's danger border via the existing rule.
15. `document.documentElement.scrollWidth` does not exceed `clientWidth` at 1440, 1024, 768 or 420 px.
16. The diff introduces **no new CSS class or rule** in `static/css/app.css`.
17. Every existing row's composition and order is byte-identical to PR #169 head apart from the single inserted row.

---

## Notes for the technical specification (not decided here)

Named only so the boundary is explicit — this file does not solve any of them: the canonical vocabulary and its order; single-vs-multi storage; what `Muu` stores and what its free-text field is called; whether the free text is required when `Muu` is ticked; legacy `legal_instrument_raw` mapping; model, migration and backfill; reporting, export and search inclusion; whether the Teema detail page shows the value (this design only asserts that the value must be available to it, and that the field must be editable on an existing Matter).

The one design constraint that survives into any of those decisions: **the shape of the control is a promise about the data.** If the field ends up holding one value, it must be radio chips; if several, checkbox chips with the count and the clear marks. Do not ship checkbox chips over a single-value field.
