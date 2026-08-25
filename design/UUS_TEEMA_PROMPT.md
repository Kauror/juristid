# Uus teema — implementation prompt

**Target:** `templates/matters/matter_create.html` (+ `static/css/base.css`)
**Design source:** `design/mockups/Uus teema.dc.html` — 8a empty form, 8b filled form, 8c edge cases. Read it for exact values; this document explains the intent and the diff from what is on `main`.

---

## 1. What is wrong with the current page

The current form is correct in its data promises and wrong in its shape. Every field is a full-width `fieldset` stacked vertically, so nine equal-weight boxes and roughly 2 500 px of scroll separate the title from the submit button. Seventeen sender checkboxes and twenty-five policy-area checkboxes are rendered inline as `checkitem` rows; Hetkeseis (11) and Menetlusliik (8) are `choicecard` radio grids inside a disclosure. Nothing is visually subordinate to anything else, so the page reads as a survey rather than "file this matter".

**Only the presentation changes.** Do not touch the model, the form classes, or the validation.

## 2. What must not change

These were deliberate decisions and the redesign keeps every one of them:

- **Only `title` is required.** A title plus submit creates a valid matter (master specification 3.8).
- **Control shape carries the data promise.** `owner`, `stage`, `track` hold one value each → radios. `source_organisations`, `policy_areas` hold several → checkboxes. Never swap one for the other, and never replace them with `<select>` — for a department of four, a dropdown is a click spent discovering the options (Agent-UI brief 5.1).
- **No institution is created from this form.** The long-tail sender list stays a filtered list of existing organisations with the plain sentence pointing at the organisations section (ADR 0025, master specification 14.7).
- **`is_test_data` stays one unticked checkbox**, not a REAL/TEST choice on the capture path.
- **Nähtavus is not on this page at all.**
- **Järgmine tegevus stays optional** and stays distinct from Arvamuse tähtaeg. TEEN/OOTAN/JÄLGIN are DO/WAIT/MONITOR; the date carries a stored meaning.
- **A refused save never hides typed input** — any block containing an error renders expanded.

## 3. The new layout

One column, max-width 1060 px, sections separated by a 1 px `#1e242b` rule with 11 px of padding above and below — no cards, no boxes, no accordions. Order:

| # | Section | Fields | Notes |
| --- | --- | --- | --- |
| 1 | **Pealkiri** | `title` | 15 px input, autofocus, the only field above the first rule |
| 2 | **Millest teema räägib** + **Märkmed** | `brief_summary`, `notes` | two columns: `minmax(0,1fr) 320px`. Summary is the text the Teema header displays. Märkmed is free-form, dashed border, no placeholder |
| 3 | **Failid** + **Saabus** + **Arvamuse tähtaeg** | `files`, `received_date`, `response_deadline` | three columns: `minmax(0,1fr) 146px 146px`. Dropzone keeps the plain `<input type="file" multiple>` |
| 4 | **Vastutaja** + **Saatja** | `owner`, `source_organisations` | two columns: `246px minmax(0,1fr)`. Both render as chips |
| 5 | **Valdkonnad** | `policy_areas`, `policy_area_other*` | chips, wrapping |
| 6 | *(no heading)* **Hetkeseis**, **Menetlusliik**, then **Adressaat** + **Andmeklass** | `stage`, `track`, `addressee_organisations`, `is_test_data` | previously behind "+ Täpsusta teema andmeid" — now always visible. Adressaat/Andmeklass row is `minmax(0,1fr) 130px` |
| 7 | **Järgmine tegevus** | `action_form.*` | the one panel on the page: `#131b21` fill, `#17506a` border, 2 px `#009fda` left border. Always expanded |
| 8 | Actions | Loo teema (primary) · Loobu | plus the muted line "Ülejäänud andmeid saab lisada ka hiljem teema lehel." |

Both former disclosures are gone. Everything is on screen at load; the height saving comes from paired rows and chip density, not from hiding fields.

## 4. The chip control

This is the single change that removes most of the page height. It replaces `.checkitems .checkitem` and `.choicecards .choicecard` — same inputs, same names, same single/multi semantics, new skin.

```html
<label class="chip">
  <input type="checkbox" name="policy_areas" value="…">   <!-- or type=radio -->
  <span class="chip__name">Keskkond</span>
</label>
```

```css
.chip input { position:absolute; opacity:0; width:0; height:0 }   /* focusable, not visible */
.chip__name {
  display:inline-block; padding:3px 11px; border-radius:13px;
  font-size:12px; color:var(--text-body);            /* #c5ced6 */
  border:1px solid var(--border-default);            /* #2a323b */
  cursor:pointer;
}
.chip input:checked + .chip__name {
  font-weight:600; color:var(--text-primary);        /* #e8edf2 */
  background:var(--accent-soft);                     /* #0e2a37 */
  border-color:var(--accent-border);                 /* #17506a */
}
.chip input:focus-visible + .chip__name {
  outline:2px solid var(--focus-ring); outline-offset:2px;   /* #5cc7ef */
}
```

Rows are `display:flex; gap:6px; flex-wrap:wrap`. A selected multi-select chip appends a `×` in `#5cc7ef` that unticks it. Disclosure-style affordances ("Vali nimekirjast (12) ▾", "Veel 15 ▾", "Muu") use `border:1px dashed #3d4954` and `color:#9aa7b4` — dashed always means "opens something optional".

Radio groups (Vastutaja, Hetkeseis, Menetlusliik) use identical chips; only one can be checked, which the input type already enforces. Keep the `<fieldset><legend>` wrapper for each group — the legend is the 11 px uppercase label.

## 5. Sender and addressee long tail

Frequent organisations render as chips inline. `source_organisations_other` moves behind a chip-shaped `<details>` labelled "Vali nimekirjast (N)". Inside: the existing search input (progressive enhancement — it narrows the chips below and nothing else) and the remaining organisations as chips. The closing sentence stays exactly as written today:

> Kui saatjat siin ei ole, tuleb asutus enne lisada asutuste alla — teema vormilt uut asutust ei teki.

**Adressaat becomes multi-select** — the same chip control, with a "Muu" affordance. This is the one model change in the redesign: `addressee_organisation` (FK) → `addressee_organisations` (M2M), mirroring what ADR 0025 did for senders, because a matter can be answered to a ministry and a committee at once. If you would rather not migrate yet, ship it as a single-value chip group and note it; the layout is unaffected.

"Muu" on Valdkonnad keeps its current behaviour: ticking it reveals the `policy_area_other` text input directly under the chip row (8c shows this).

## 6. Järgmine tegevus

```
JÄRGMINE TEGEVUS
[TEEN] [OOTAN] [JÄLGIN]   [ Mida järgmisena teed või ootad?               ]
[Tähtaeg] [Oodatav aeg] [Vaatan üle]   [ pp.kk.aaaa 📅 ]
```

- Kind chips are square-ish (`border-radius:4px`, 11 px, 700 weight, letter-spacing .05em) so they read as the workflow vocabulary and not as taxonomy chips: TEEN filled `#009fda` with `#06131a` text, OOTAN solid outline, JÄLGIN dashed outline. That is the same TEEN/OOTAN/JÄLGIN treatment as Minu töö and the Teema page — shape distinguishes them, never colour alone.
- **`date_semantics` is no longer a nested disclosure.** The three meanings are chips on the row with the date. The selected one derives from the kind (DO → Tähtaeg, WAIT → Oodatav aeg, MONITOR → Vaatan üle) and re-derives when the kind changes and the user has not touched it; once touched, the choice is theirs and the model's permitted combinations all remain reachable. The dynamic `data-datelabel-for` label swap is no longer needed — the chip states what the date means.
- **No `responsible` field.** The action inherits the matter's Vastutaja chosen in section 4. Keep the model field and default it server-side; re-assignment happens on the Teema page.
- Kind descriptions ("Mul endal tuleb midagi teha" etc.) are dropped from this form — the three words plus the meaning chips carry it. Keep them in the Teema-page composer if they help there.

## 7. Copy

Remove every helper/`field__help` line except the two that state a rule the UI cannot: the "uut asutust ei teki" sentence (§5) and "Ülejäänud andmeid saab lisada ka hiljem teema lehel." next to the submit. In particular drop: "Pealkiri on ainus kohustuslik väli" (the error says it when it matters), "Viide antakse automaatselt", "iga fail salvestatakse muutumatu tõendina", "üks valik", "kus menetlus praegu on", "kellele Koda vastab", "ei kuulu aruandlusse", "arvamuse tähtaeg on eraldi", and the date-semantics derivation note. Field labels are 11 px uppercase, 700 weight, letter-spacing .08 em, `#9aa7b4`.

Validation error: `#ef7d6e` text under the field, `#6b2f28` border plus a 3 px `rgba(239,125,110,.08)` ring on the input, and "Loo teema" renders inactive (`#1e242b` fill, `#7d8b99` text) until a title exists.

## 8. Tokens

Everything above maps to `static/css/tokens.css` semantic tokens — no raw hex in templates. Surfaces `#101418` base / `#14191f` header band / `#131b21` accent panel / `#1e242b` elevated; borders `#1e242b` subtle / `#2a323b` default / `#3d4954` strong; text `#e8edf2` / `#c5ced6` / `#9aa7b4` / `#7d8b99`; accent `#009fda`, soft `#0e2a37`, border `#17506a`, link/focus `#5cc7ef`. Dates use `font-variant-numeric: tabular-nums`. Focus is always a 2 px `#5cc7ef` outline at 2 px offset and is never removed. Full table: `design/UI_DESIGN_SPEC.md`.

## 9. Acceptance

1. Title + submit still creates a matter; nothing else is required.
2. Every field on `main` is still present and still posts the same name and value.
3. `owner`/`stage`/`track` still accept exactly one value; senders/valdkonnad/adressaat accept several.
4. No organisation can be created from this page.
5. With scripting off: every chip is visible and tickable, the long-tail lists are open, and the form submits.
6. Keyboard: tab reaches every chip, space toggles, focus ring visible on all of them.
7. A refused save re-renders with all typed values intact and errors visible without opening anything.
8. At 1440 px the whole form fits in roughly one and a half screens; at 1280 px nothing overflows horizontally.
