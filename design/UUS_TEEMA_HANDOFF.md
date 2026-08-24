# Uus teema — design handoff

**Design source:** [`design/mockups/Uus teema.dc.html`](mockups/Uus%20teema.dc.html) — component plate, 8a empty, 8b filled, 8c edge/error, 8d 1024 px.
**Stylesheet:** [`design/mockups/uus-teema.css`](mockups/uus-teema.css) — every rule below, written against the real semantic tokens. It can be lifted into `static/css/app.css` almost verbatim.
**Target:** `templates/matters/matter_create.html`.
**Token authority:** `static/css/tokens.css`. No hex, no raw px for colour/space/radius in the template or in new CSS. Where this document names a literal it says why.

This is a presentation change. **The model, the form classes and the validation do not move.** Every field keeps its name, its type and its cardinality.

---

## 1. What is actually wrong, measured

Rendered from production (`b69ac724`) at 1440 px, both disclosures opened:

| Section | Height |
| --- | ---: |
| Pealkiri | 58 px |
| Failid | 76 px |
| Vastutaja | 75 px |
| Saabus | 52 px |
| **Saatja** | **393 px** |
| **Valdkonnad** | **283 px** |
| **`+ Täpsusta teema andmeid`** | **539 px** |
| **`+ Järgmine tegevus`** | **522 px** |
| **Title → `Loo teema`** | **2 139 px** |

Four sections are 82 % of the form. The cause is not the number of fields — it is that every one of them is a bordered box of equal weight, and that two seventeen- and twenty-five-item lists are rendered as full-height checkbox rows. Nine boxes in a column have no hierarchy, so nothing reads as optional, and the page becomes a survey.

Two further consequences, both visible in 8c's counterpart on `main`:

- half the form is inside `<details>`, so a validation error can land somewhere the reader cannot see;
- `Arvamuse tähtaeg` is *inside* `+ Täpsusta teema andmeid`, which is the wrong place for a date that is frequently the reason the Matter exists.

**After:** title → `Loo teema` is **1 023 px** filled, **982 px** empty, **1 055 px** at 1024 px wide. That is a 52 % reduction *while showing six fields that used to be hidden*. The empty form is 85 px taller than the current collapsed one; those 85 px buy Hetkeseis, Menetlusliik, Adressaat, Andmeklass, Arvamuse tähtaeg and the whole next-action block out of two accordions.

---

## 2. Information architecture

One column, `max-width: 1060px`, centred, `padding: 24px 32px 40px`. Sections are separated by `border-top: 1px solid var(--border-subtle)` with `padding: 14px 0` — **no cards, no fieldset borders, no accordions**.

| # | Section | Grid | Fields |
| --- | --- | --- | --- |
| 1 | Pealkiri | — | `title` |
| 2 | Millest teema räägib · Märkmed | `minmax(0,1fr) 320px` | `brief_summary`, `notes` |
| 3 | Failid · Saabus · Arvamuse tähtaeg | `minmax(0,1fr) 146px 146px` | `files`, `received_date`, `response_deadline` |
| 4 | Vastutaja · Saatja | `246px minmax(0,1fr)` | `owner`, `source_organisations` |
| 5 | Valdkonnad | — | `policy_areas`, `policy_area_other` |
| 6 | Hetkeseis / Menetlusliik / Adressaat · Andmeklass | last row `minmax(0,1fr) 150px` | `stage`, `track`, `addressee_organisation(s)`, `is_test_data` |
| 7 | Järgmine tegevus | panel | `action_form.*` |
| 8 | Loo teema · Loobu | — | — |

Grid gap is `var(--spacing-5)` (20 px) throughout. Section 6's three groups are separated by `margin-top: var(--spacing-35)` (14 px) rather than by rules — they are one section about how the Matter is classified.

**Reading order is the DOM order in every state and at every width.** Nothing reflows across a section boundary.

---

## 3. The selection chip — `.pick`

> Named `.pick` and not `.chip`: `.chip` was already taken by the restricted badge beside a Matter title, which is uppercase and heavy, and every selection control inherited that through the label wrapper. Two different objects, two names.

One control, two behaviours. This is the change that removes most of the height: a pick row is 26 px where a `checkitem` column is 26 px *per item*.

```html
<label class="pick">
  <input type="checkbox" name="policy_areas" value="{{ area.pk }}">
  <span class="pick__name">Keskkond<span class="pick__x" aria-hidden="true">×</span></span>
</label>
```

`type="radio"` for `owner`, `stage`, `track` and the action kind; `type="checkbox"` for `policy_areas`, `source_organisations`, `addressee_organisations`. **The input type is the promise, not the CSS.** Never render a radio group as checkboxes to reuse a style.

### Geometry

| | Value | Token |
| --- | --- | --- |
| padding | `3px 11px` | literal — see note |
| radius | 4 px | `--radius-sm` |
| font | 12 px / 400 | `--typography-size-meta` |
| border | 1 px solid | — |
| row gap | 6 px | `--spacing-15` |
| `×` gap | 4 px | `--spacing-1` |

The vertical `3px` is off the 4 px scale deliberately: at `--spacing-1` (4 px) a pick is 26 px tall and the form's eight pick rows gain ~40 px for nothing. The border is present in **every** state so selection never changes the box size and a row never reflows on click.

### States

| State | Background | Border | Text |
| --- | --- | --- | --- |
| Unselected | `--surface-raised` | `--border-control` | `--text-body`, 400 |
| Hover | unchanged | `--border-strong` | unchanged |
| **Selected** | `--accent-soft` | `--accent-border` | `--text-primary`, 600 |
| Focus | unchanged | unchanged | + `outline: 2px solid var(--accent-link); outline-offset: 2px` |

Hover changes the border and nothing else. Focus is an outline and is **never** removed — the input is `opacity: 0` but covers the label, so it is focusable, and `:focus-visible + .pick__name` is what paints the ring.

The `×` renders only on `input[type=checkbox]:checked`. A checked radio does not get one: you replace a radio, you do not clear it, and offering an × that cannot do anything is a lie about the control.

### Dashed means "this opens something optional"

`Vali nimekirjast (N)`, `Muu`, and nothing else: `border: 1px dashed var(--border-strong)`, `color: var(--text-secondary)`, transparent background. Same geometry as a pick so the row stays even. `Muu` **keeps its dash when checked** (border colour moves to `--accent-link`) — selected, it is still the control that revealed the free-text field, and losing the dash would make it look like a governed area, which is the one thing it must never look like.

This convention is borrowed from the Teema page, where the rail's `+` chip and the SharePoint rows already use it.

---

## 4. Workflow picks — TEEN / OOTAN / JÄLGIN

Deliberately **not** the selection pick. This is the vocabulary `Minu töö` and the Teema page already use and it has to read as the same object here.

| | Value |
| --- | --- |
| padding | `3px 12px` |
| radius | 4 px (`--radius-sm`) |
| font | 11 px / 700 (`--typography-size-label`) |
| tracking | `--typography-tracking-chip` (0.06 em) |
| case | as written — TEEN, OOTAN, JÄLGIN |

| Mode | Selected | Unselected |
| --- | --- | --- |
| TEEN | `--accent-primary` fill, `--text-inverse` text, solid border | `--surface-elevated`, `--border-control`, `--text-muted` |
| OOTAN | `--surface-selected`, **solid** `--accent-link` border, `--text-primary` | as above, solid border |
| JÄLGIN | `--surface-selected`, **dashed** `--accent-link` border, `--text-primary` | as above, **dashed** border |

Filled / solid / dashed is the distinction that survives greyscale, and JÄLGIN keeps its dash in both states. This is ADR 0031 §7 exactly; do not re-derive it.

**The distinction from selection picks is weight, case and tracking — not radius.** `tokens.css` says *"radius: never pill except avatars"*, and the Teema page's own taxonomy badges are 4 px. See §10.

---

## 5. Sender and addressee long tail

```
[ Rahandusministeerium × ] [ Majandus- ja Kommunikatsiooniministeerium ] [ Kliimaministeerium ] …
[ Vali nimekirjast (10) ]
Kui saatjat siin ei ole, tuleb asutus enne lisada asutuste alla — teema vormilt uut asutust ei teki.
```

- Frequent organisations (`organisations_by_usage(viewer)`) render as picks inline.
- The rest go inside `<details class="tail">` whose `<summary>` is chip-shaped and dashed. Inside: the existing search input (progressive enhancement — it filters the picks below and does nothing else) and the remaining organisations as picks.
- **The count in the summary is the number of organisations inside the disclosure**, after promotion.

### The promotion rule — get this right

> A chosen long-tail organisation is rendered in the **visible** row **and removed from the list inside the disclosure**.

Not duplicated. Two inputs sharing one `name` and `value` post the value twice, and a choice that exists in two places has two states to keep in step. This was a live bug in the first draft of this mockup and is easy to reintroduce.

Nothing on this page creates an organisation. No `+ Lisa asutus`, no free text, no silent master-data creation (ADR 0025, master specification 14.7). The sentence above stays.

**Without scripting**, `<details>` is still openable and every pick is tickable, so the whole catalogue remains reachable and the form submits.

### Adressaat

Same control. The design shows it multi-select, which is where the model should go — a Matter is answered to a ministry and a committee at once, exactly as ADR 0025 argued for senders. **If `addressee_organisation` is still a `ForeignKey` at implementation time, render it as a single-value radio pick group.** The layout does not change and this uncertainty must not distort it.

---

## 6. Valdkonnad

All 23 visible, wrapping, no disclosure. Measured: at a 1060 px column the governed vocabulary is **three rows, ~120 px including the label**. That is not visually excessive, and a `Veel N` control would trade the taxonomy's scannability — the thing a lawyer actually needs from this list — for eighty pixels.

`Muu` is the last pick in the row. Ticking it reveals the `policy_area_other` text input directly below the row, `max-width: 420px`, labelled `MUU VALDKOND`, placeholder `Millisesse valdkonda see kuulub?`. No modal. Unticking it clears the text server-side, as `MatterCreateForm.clean` already does.

A retired area a Matter already carries is offered and marked; on a *create* form there is no such case, so this only matters when the same pick control is reused on `Muuda teemat`.

---

## 7. Järgmine tegevus — the one panel

```
JÄRGMINE TEGEVUS
[TEEN] [OOTAN] [JÄLGIN]        [ Mida järgmisena teed või ootad?                    ]
[Tähtaeg] [Oodatav aeg] [Vaatan üle]                              [ pp.kk.aaaa      ]
```

| | Value |
| --- | --- |
| background | `--surface-capture` (#131b21) |
| border | `1px solid var(--accent-border)` |
| left border | `2px solid var(--accent-primary)` |
| radius | `--radius-sm` |
| padding | `12px 16px 14px` |
| row gap | `--spacing-25` (10 px) |
| text field | `flex: 1 1 320px` |
| date field | `146px`, fixed at every width |

Always expanded. Everything inside stays optional.

The blue left edge is what marks a capture surface throughout the product — it is the Teema composer's own treatment, and this is the same act: saying what happens next.

### Date meaning

Three picks, on the row with the date, in the **selection** pick style (rounded-family, 12 px / 400), not the workflow style. That is the point: they qualify the date, they are not a fourth mode.

Derivation: `DO → Tähtaeg`, `WAIT → Oodatav aeg`, `MONITOR → Vaatan üle`. The meaning re-derives when the kind changes **until the user picks one explicitly**; after that the choice is theirs and every combination the model permits stays reachable. Implement with a `data-touched` flag on the group, set on first user change.

The `data-datelabel-for` label-swapping in `app.js` is no longer needed — the pick states what the date means.

**No `responsible` field.** The action inherits the Matter's Vastutaja from section 4; the service already falls back to `matter.owner`. Reassignment happens on the Teema page.

**No kind descriptions** ("Mul endal tuleb midagi teha" etc.). Three words plus a meaning pick carry it. They stay on the Teema-page composer if they help there.

### One required change to `NextActionForm`

`target_date` currently carries `initial=timezone.localdate`. **Remove it.**

That default was safe while the block was a closed `<details>` — nothing read the date's emptiness because `wants_action` is driven by `next-text`. With the panel always visible it is no longer safe: a person who picks TEEN and types a step without touching the date silently gets a deadline of *today*. That is precisely the failure ADR 0031 §5 names — *default a date box only when nothing reads its emptiness* — and `set_next_action` refuses DO + DEADLINE with no date, which means something does read it.

`received_date` and `response_deadline` **keep** their `initial=timezone.localdate`. Nothing reads their emptiness, and 8a shows them carrying today.

---

## 8. Copy

Every `field__help` line goes except two.

**Keep:**

> `Kui saatjat siin ei ole, tuleb asutus enne lisada asutuste alla — teema vormilt uut asutust ei teki.`

> `Ülejäänud andmeid saab lisada ka hiljem teema lehel.`

**Delete:** "Pealkiri on ainus kohustuslik väli", "Viide antakse automaatselt", "Vali failid või lohista need siia. Iga fail salvestatakse muutumatu tõendina koos teemaga.", "Kellelt teema tuli. Saatjaid võib olla mitu.", "Kus menetlus praegu on. Üks valik.", "Mis liiki menetlusega on tegemist. Üks valik.", "Kellele Koda vastab. Eraldi fakt saatjast.", "Arenduseks loodud teema; ei kuulu päris aruandlusse.", "Vabatekst. Siit ei teki uut valdkonda ega silti.", the `Arvamuse tähtaeg on eraldi` paragraph, and the date-semantics derivation note.

### Exact labels

| Element | Copy |
| --- | --- |
| Page title | `Uus teema` |
| Section 1 | `PEALKIRI` |
| — placeholder | `Näiteks: Pakendiseaduse muutmise eelnõu` |
| Section 2 | `MILLEST TEEMA RÄÄGIB` |
| — placeholder | `Mida see teema puudutatud ettevõtete jaoks tähendab ja mida Koda tahab?` |
| | `MÄRKMED` (no placeholder) |
| Section 3 | `FAILID` · `või lohista siia` · `SAABUS` · `ARVAMUSE TÄHTAEG` |
| — date placeholder | `pp.kk.aaaa` |
| Section 4 | `VASTUTAJA` · `SAATJA` · `Vali nimekirjast (N)` · `Otsi asutust nime järgi` |
| Section 5 | `VALDKONNAD` · `Muu` · `MUU VALDKOND` |
| Section 6 | `HETKESEIS` · `MENETLUSLIIK` · `ADRESSAAT` · `ANDMEKLASS` · `Testandmed` |
| Section 7 | `JÄRGMINE TEGEVUS` · `Mida järgmisena teed või ootad?` · `Tähtaeg` · `Oodatav aeg` · `Vaatan üle` |
| Section 8 | `Loo teema` · `Loobu` |

Micro-labels are 11 px / 700 / `--typography-tracking-label` (0.08 em), uppercase, `--text-muted`, 8 px below.

---

## 9. Errors

State 8c shows three at once, which is the state to build against.

- **Banner**, above Pealkiri: `--status-danger-soft` fill, `1px solid var(--status-danger-border)`, `2px solid var(--status-danger)` left edge, `role="alert"`.
  `**Teemat ei salvestatud.** Kaks välja vajavad tähelepanu. Kõik sisestatu on alles.`
- **Field**: `border-color: var(--status-danger-border)` plus `box-shadow: 0 0 0 3px rgba(239,125,110,.08)`. The literal rgba has no token; add one if it is used a third time.
- **Message**: 12 px `--status-danger`, 6 px below the control.
- **Label** of an erroring field turns `--status-danger`.
- **Chip group** in error: the label turns danger and the message goes under the row. Do not put a red border on twenty-three picks.
- **`Loo teema` inactive** while the title is empty: `--surface-elevated` fill, `--text-muted` text, `cursor: not-allowed`, `aria-disabled="true"` — **not** the `disabled` attribute. A `disabled` button is unfocusable and announces nothing, so a keyboard user meets a control that does not exist. The server refuses a blank title regardless.

Every value the user typed re-renders in place. Nothing is inside a closed disclosure, so no error can hide — which is the structural reason the accordions had to go, not just an aesthetic one.

---

## 10. Two decisions for you

**A. Chip radius — 4 px or 13 px.** The brief asked for ~13 px so selection picks read as distinct from the square workflow picks. `tokens.css` states *"radius: never pill except avatars"*, and the Teema page's own taxonomy badges are 4 px. The component plate shows both side by side. **Recommendation: 4 px.** The distinction is already carried by weight (700 vs 400), case and the filled/solid/dashed triad, and 13 px would make the form's picks the only pill in the product — including TEEN, which would weaken exactly the vocabulary the brief wants protected. Switching to B is one value in `.pick__name`.

**B. Adressaat cardinality.** The design is multi-select. Shipping it needs `addressee_organisation` (FK) → `addressee_organisations` (M2M) with a through-model, mirroring ADR 0025. If that migration is not in scope for this round, ship single-select with the same pick control; nothing in the layout moves.

---

## 11. Acceptance

1. Title + submit creates a Matter; nothing else is required.
2. Every field present on `main` is still present and still posts the same name and value.
3. `owner` / `stage` / `track` accept exactly one value; `policy_areas` / senders / addressees accept several. No name+value pair appears twice in the DOM.
4. No organisation can be created from this page.
5. Scripting off: every pick is visible and tickable, both `<details>` are openable, the form submits.
6. Keyboard: tab reaches every pick, space toggles, the focus ring is visible on all of them, and `Loo teema` is reachable even while inactive.
7. A refused save re-renders every typed value, and every error is visible without opening anything.
8. Title → `Loo teema` is ≤ 1 100 px at 1440 px and ≤ 1 100 px at 1024 px; no horizontal overflow at 1440, 1280 or 1024.
