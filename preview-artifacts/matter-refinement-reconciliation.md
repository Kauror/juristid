# Matter page refinement — functional reconciliation

**For the technical lead, before the production implementation prompt is written.**

This is the inventory of what the current Teema page does, set against what
`design-handoff/matter-page-refinement` draws. It is not a plan and it proposes
no wording: several rows end in a question that only the product owner can
answer, and they are left as questions.

The design's own README says it twice and it governs everything below:

> **THIS DESIGN DOES NOT AUTHORISE REMOVAL OF EXISTING APPLICATION
> FUNCTIONALITY.** Absence from these screenshots is not permission to delete.

Read at `origin/main` = `b3df3179c273d7c89f22db1d05d764f72e236151`.
Preview branch: `preview/matter-page-refinement`.

## Classes

| | |
|---|---|
| **A** | present in the design **and** wired in the preview — the real production control, doing its real job |
| **B** | present in the design, **visual only** in the preview — drawn, but deliberately inert |
| **C** | current functionality **not represented** by the design at all |
| **D** | design and current behaviour **conflict** |
| **E** | **open question** — the design leaves it undetermined and this preview does not answer it |

## Counts

| Class | Count |
|---|---|
| A — present and wired | 19 |
| B — present, visual only | 14 |
| C — not represented by the design | 21 |
| D — conflict | 4 |
| E — open question | 17 |
| **Capabilities inspected** | **75** |

Every B is inert for one reason, stated once here rather than 14 times: the
design defines *appearance* and *open/close*, and `05-state-matrix.md` contains
no saving state, no validation state and no refusal state anywhere on it. A
preview that wired a write would be inventing three states the design does not
have — and a successful save would swap the **production** Matter view's markup
into the middle of the refined page, which is the one thing a visual review must
not show. The preview therefore blocks form submission and HTMX requests inside
its own root (`static/js/matter_refinement_preview.js`). Every control is the
real one; none of them acts.

---

## 1 — Header band

| # | Capability | Current behaviour | Design | Class | Note |
|---|---|---|---|---|---|
| 1.1 | Crumb «Teemad» | Link to the register | Same | **A** | production partial, included unmodified |
| 1.2 | «Muuda teemat» | Link to `matters:matter_edit` | Same, quiet tiny button | **A** | |
| 1.3 | «⋯» action menu | Native `details`, opens the Nähtavus panel | Same (C02, I05) | **A** | |
| 1.4 | Nähtavus select + Salvesta | `matters:update_field/visibility`, swaps `#teema-pais` | Drawn | **B** | |
| 1.5 | Restricted-visibility help sentence | `{% restricted_visibility_help %}` under the select | **absent from the frame** | **C** | Not in the design's TEXT-NOT-PRESENT list either, i.e. not considered. Kept — it is production copy for a retained workflow. |
| 1.6 | «Kontrolli dokumendist leitud andmeid» | Link to the assisted review, only when `document_count` | Drawn | **A** | |
| 1.7 | Title | `matter.title` | Same, 600/21px | **A** | |
| 1.8 | State badge (Avatud) | dot + word | Same | **A** | |
| 1.9 | TEST badge | when `is_test_data` | Same | **A** | |
| 1.10 | Closed / Arhiiv badges | rendered when closed or archived | **not drawn** | **C** | C03: "Closed/archive/restricted variants NOT SPECIFIED" |
| 1.11 | 🔒 Piiratud lock chip | when restricted | **not drawn** | **C** | |
| 1.12 | Field-error line (`formerror`) | after a refused inline save | **not drawn** | **C** | no validation state anywhere in the design |
| 1.13 | Vastutaja inline editor | select + Salvesta, HTMX | Same (C04, I03) | **B** | |
| 1.14 | Valdkond inline editor | checkbox set + one submit; retired areas appended | Same | **B** | the retired-area row is not drawn — **C** in itself |
| 1.15 | Hetkeseis inline editor | select + caret | Same | **B** | |
| 1.16 | Tähtaeg inline editor | date input; four readings of the date | Same | **B** | overdue / today / discharged readings not exercised — **E** |
| 1.17 | «+ Tähtaeg» when none exists | quiet invitation, prefilled with today | **not drawn** | **C** | the design's Matter has a deadline |
| 1.18 | Inline-edit dashed underline | always visible | **on hover only** (§17, I04) | **A** | implemented, CSS only |
| 1.19 | Summary + «Muuda» | `details`, textarea, Ctrl+Enter hint | Same (C05) | **B** | |
| 1.20 | Summary empty prompt | «+ Mida see teema ettevõtjatele tähendab? (2–3 lauset)» | **not drawn** | **C** | |
| 1.21 | Tabs Teema / Dokumendid · N | two tabs | Same (C06) | **A** | |

## 2 — Banners

| # | Capability | Current behaviour | Design | Class | Note |
|---|---|---|---|---|---|
| 2.1 | Restricted banner | one sentence from `visibility_help` | **not drawn** | **C** | rendered by the preview anyway (production partial included) |
| 2.2 | Closed banner + «Ava uuesti…» | disposition, reason, date, reopen POST | **not drawn** | **C** | idem |
| 2.3 | Archive banner | source era, missing-fields note | **not drawn** | **C** | idem |

## 3 — Zone A · Järgmiseks

| # | Capability | Current behaviour | Design | Class | Note |
|---|---|---|---|---|---|
| 3.1 | Step text + date | at stored precision | Same (C07) | **A** | |
| 3.2 | «✓ Tehtud» | POST, swaps `#jargmiseks-rida` only | Drawn | **B** | |
| 3.3 | «Muuda» | focuses the composer (`data-focus`) | Drawn | **A** | works — it moves focus, it does not write |
| 3.4 | «Lükka edasi ▾» | menu of dated options + exact date; **only on an exact date** | **absent from the frame** | **A** | Not a removal. The design's sample step is `I kvartal 2027`, and a quarter-precision step never offers deferral — so the frame shows exactly what production shows for that data. The preview fixture uses quarter precision for the same reason. |
| 3.5 | Overdue row (`.uxnext--overdue`) | red ring + red date + «N p» | class exists, **not exercised** | **E** | |
| 3.6 | «Järgmine samm on määramata» | empty state | **not drawn** | **C** | |
| 3.7 | Register instruction («Excelist» flag) | when imported and no structured action | **not drawn** | **C** | |
| 3.8 | Closed-Matter row | «Puudub — teema on suletud…» | **not drawn** | **C** | |
| 3.9 | Refused completion (`uxnext__error`) | inside the row | **not drawn** | **C** | |
| 3.10 | **Row click toggles the composer** | — | **new interaction** (I01) | **A** | the one script the refinement adds; implemented |

## 4 — Zone A · composer

| # | Capability | Current behaviour | Design | Class | Note |
|---|---|---|---|---|---|
| 4.1 | Collapsed → open | native `details` | Same (C08, I02) | **A** | |
| 4.2 | `L` key hint on the collapsed row | `<kbd>L</kbd>` | **removed** (§20; TEXT NOT PRESENT) | **A** | The hint is gone; **the shortcut is not**. `static/js/ux.js` still binds `L`, and I02 says the design "does not say it is removed". AGENTS.md's rule that every shortcut needs a click equivalent still holds — the whole row is the trigger, and now so is the Järgmiseks row above it. |
| 4.3 | Body textarea | 108px when open | **60px / 2 rows** (§18) | **A** | CSS only |
| 4.4 | «Järgmiseks» control | single-line `TextInput`, placeholder «Näiteks: vaadata uus eelnõu versioon üle» | **1-row textarea, `resize: vertical`, 38px** (§19, I10) | **D** | The preview renders the production input at the design's height. Changing the widget is a form change, and it would drop a production placeholder the design never considered. **Decision needed:** change the widget, or keep the input and drop the resize affordance. |
| 4.5 | Millal? quick chips | write into the date field | Drawn (I09) | **B** | |
| 4.6 | «Kuupäev…» disclosure | opens when it holds a value or a refusal | Drawn | **B** | |
| 4.7 | Chip selected state (`.uxchip.is-selected`) | exists | **not exercised** | **E** | Q15 |
| 4.8 | «+ Manus» panel | file + role, revealed in place | chip drawn | **B** | I08: the panel is NOT SPECIFIED |
| 4.9 | «+ Oluline tähtaeg» panel | date, precision, title | chip drawn | **B** | idem |
| 4.10 | «+ Jõustumine» | today an `intelligence:add_effective_date` chip **in the facts block**, rendered only when the Matter has no commencement | **moved into the composer action row** (§5) | **D** | The design's row is unconditional; production's is conditional on there being no record. The preview draws the design's row and does not wire it. **Decision needed:** does the chip appear on a Matter that already has a commencement? |
| 4.11 | «+ Töövõit» | same shape as 4.10 | idem | **D** | idem |
| 4.12 | «+ Lõpeta teema» panel | six closing questions | chip drawn | **B** | |
| 4.13 | Submit / Ctrl+Enter | one POST, one transaction, up to six records | Drawn | **B** | |
| 4.14 | Composer reopens on a refused save | `{% if composer_error %}open{% endif %}` | **not drawn** | **C** | no refusal state in the design |
| 4.15 | Composer hidden when closed or read-only | `{% if matter.is_open and can_write %}` | **not drawn** | **C** | preserved in the preview |

## 5 — Zone B · facts panel

| # | Capability | Current behaviour | Design | Class | Note |
|---|---|---|---|---|---|
| 5.1 | Olulised tähtajad list | own section, rendered only when non-empty | first section of a shared panel | **A** | |
| 5.2 | `.factrow__dist` («21 p») | **does not exist** | **new element** (C12) | **A** | computed in `design_preview.py`, never in a template |
| 5.3 | Past important dates | rendered below the upcoming ones, `factrow--past` | **future only**; review said "timeline only", but the frame never draws one in the chronology either | **E** | Q2 — the preview draws future rows only and does **not** move past ones anywhere |
| 5.4 | Cancelled / superseded rows | «Tühistatud» / «Asendatud» flags, kept visible | **not drawn** | **C** | |
| 5.5 | Row «Muuda» / «Tühista» | links to the intelligence edit/cancel pages | drawn, hover-only | **B** | I06: "lead nowhere in the prototype" |
| 5.6 | Row actions visible at rest | always visible | **hover / focus-within** (§9) | **A** | CSS only; `:focus-within` gives keyboard parity |
| 5.7 | Add control | «+ Lisa oluline tähtaeg», button in the section head | **«+ Lisa tähtaeg», link under the last row** (§8) | **B** | Copy comes from the design (shorter). What it opens is NOT SPECIFIED (I07). |
| 5.8 | Jõustumine section | its own `.factsection` with its own rows and inline add form | **no such section**; one row reading `1.1.2027 · jõustub · …` sits inside Olulised tähtajad | **E** | **Q1 + Q3, the biggest open question on the page.** The preview reproduces the row exactly as drawn — a real `MatterEffectiveDate`, with the design's own word `jõustub` in the `.factrow__dist` slot — precisely so the product owner can look at it and decide. Nothing in the application changed; the production Jõustumine section is untouched. |
| 5.9 | Töövõidud section | rows, victory state chips, «Kinnita töövõiduks» / «Ei realiseerunud» | **not drawn** | **C** | |
| 5.10 | Kaasamine section | collapsed accordion; count in the label; summary line when closed | **plain section in the facts panel**, always open (§2) | **A** | |
| 5.11 | Engagement row shape | kind · title · date, date trailing | **date leads**, in the same column as the deadlines | **A** | the shared date column is why the two sections share a panel |
| 5.12 | Undated engagement | «Kuupäev teadmata» | not drawn | **A** | production copy kept — the design gives no wording for the state |
| 5.13 | Engagement URL + host label | link + «↗» | not drawn | **A** | kept |
| 5.14 | Engagement edit disclosure | inline form, one shared bound form | drawn as «Muuda» | **B** | |
| 5.15 | «+ Lisa kaasamine» | disclosure with the add form; *is* the section when empty | link under the last row | **B** | |
| 5.16 | Register observation notes | five `factnote` lines: member feedback, «ei saatnud», recorded send date, unreadable VÄLJA, multiple addressees, continuation | **not drawn** | **C** | Six real facts with nowhere to live in the refined layout. **This is the largest single gap.** |
| 5.17 | Empty-state sentences | «Kaasamist ei ole kirja pandud» etc. | **no empty-state prose** (TEXT NOT PRESENT) | **E** | the design's sections are all populated |

## 6 — Zone C · chronology

| # | Capability | Current behaviour | Design | Class | Note |
|---|---|---|---|---|---|
| 6.1 | Open by default | `<details open>` | Same | **A** | |
| 6.2 | Closed summary (quote + next step + count) | shown when closed | Same (I11) | **A** | |
| 6.3 | Caret | visible | **hidden** (§10) | **A** | hidden, not deleted — it still opens and closes |
| 6.4 | «Ava ajajoon» / «Sule» text action | in the head | **removed** (§12; TEXT NOT PRESENT) | **A** | the section is open; the action said what the layout says |
| 6.5 | Timeline filter (Kõik / Sissekanded / Sündmused) | HTMX, swaps the list, `?ajajoon=` is a shareable link | **removed from the frame** (§11) | **E** | **Q10 asks in terms whether this applies to production.** The capability is untouched: `matters:timeline_page` still serves every filter and `?ajajoon=` still works. The preview simply does not draw the control. |
| 6.6 | Entry rows, spine, dots | `.uxtl` grid | Same (C16) | **A** | production partial, included unmodified |
| 6.7 | Timestamp position | right-hand column | **end of the meta line** (§13) | **A** | CSS only |
| 6.8 | Body vs meta weight | meta primary | **body primary** (§13) | **A** | CSS only |
| 6.9 | «Märkus» kind badge | rendered on every note | **hidden** (§14) | **D** | The design's rule names `.uxtl__kind--note` and **production never emits that class** — `timeline_items.html` writes `uxtl__kind--{{ marker }}`, i.e. `--entry` or `--meeting`. The preview hides the *entry* kind badge and keeps the *event* badges (`Järgmiseks tehtud`, `Väljasaadetud · arvamus`), which is the design's stated distinction. **Q4 is the rule itself and remains open.** Note the side effect: a Kohtumine and a Märkus now read alike apart from the spine dot. |
| 6.10 | `groupfacts` rows | «Oluline tähtaeg …», «Järgmiseks …», «Tõend …», «Kaasamine …» under an entry | the prototype draws a flatter `.uxtl__sub` shape | **A** | production markup kept; visually near-identical |
| 6.11 | «Tõend» badge on a document row | rendered | **not drawn** (§16) | **E** | carried in from an earlier decision; production still renders it |
| 6.12 | «Näita varasemaid» paging | button, swaps in place | **not drawn** | **C** | the design's Matter has one page |
| 6.13 | Empty chronology | «Ajajoon on tühi. Esimene sissekanne ilmub siia.» | **not drawn** | **C** | |
| 6.14 | System-event run fold | `details`, «näita ▸» | Same (C17, I12) | **A** | |
| 6.15 | Deep link to an entry (`#sissekanne-<pk>`) | id on each entry | not addressed | **A** | preserved |

## 7 — Rail

| # | Capability | Current behaviour | Design | Class | Note |
|---|---|---|---|---|---|
| 7.1 | Teemaviide | monospace row | Same | **A** | |
| 7.2 | Menetlusliik / Kellelt / Kellele / Saabus | inline editors, «+ Lisa» when empty | Same | **B** | |
| 7.3 | Muu valdkond row | free-text inline editor | **removed from the frame** (§21) | **E** | reviewer said "not used / going away" — that is a product decision, not a design one |
| 7.4 | Andmeklass row | shown when TEST or NATIVE | **removed from the frame** | **E** | idem |
| 7.5 | «Märgi pärisandmeteks» / «Märgi testandmeteks» | POST, no swap | **removed from the frame** | **E** | idem. Retiring it removes the only UI for the data-class boundary. |
| 7.6 | Kirje liik / Suletud / Põhjus / Sulges | rendered on a non-FULL or closed Matter | **not drawn** | **C** | kept verbatim in the preview — the frame simply shows an open, ordinary Matter |
| 7.7 | Koja arvamus card | file links, or «Arvamust ei ole lisatud.» | Same (C19) | **A** | production partial, included unmodified |
| 7.8 | Populated Koja arvamus | one row per opinion document | **not drawn** | **C** | |
| 7.9 | «Seotud» card (eelnev/jätkub/kaastöötaja) | rendered when any exists | **not drawn** | **C** | **omitted from the preview rail.** Worth an explicit decision. |
| 7.10 | Seotud materjalid — position | main-column `.factsection` | **rail card** (§6) | **A** | the move is implemented |
| 7.11 | Confirmed relations + Taustmaterjal | two sub-lists with counts, «Ava», «Eemalda seos» | **not drawn** (the frame is empty) | **C** | the preview renders them in the design's own stacked row shape; removal controls are not drawn |
| 7.12 | «Võimalikud seosed» control | separate button; computes on open | **merged into one `Lisa` disclosure with the search** (§7, I13) | **A** | implemented; the suggestions are the real engine's, with the real reasons |
| 7.13 | Suggestion cards | `relatedcard` with «Seo teemaga» / «Peida» | **stacked rail rows with a `Lisa` link** | **B** | Q9: what «Lisa» does is not defined |
| 7.14 | «Näita veel» / «Näita peidetud · N» / «Peida peidetud» | three pagers | **one «Näita veel 5 ▾»** | **E** | Q8: literal or computed. Production's default limit is already 5, so the drawn number is at least consistent. |
| 7.15 | Picker search | `related_materials:picker`, HTMX-as-you-type, placeholder «Otsi teemat pealkirja järgi» | input drawn, placeholder «Otsi teemat või dokumenti…» | **B** | I13: typing behaviour NOT SPECIFIED. Note the design's placeholder promises documents as well as Matters. |
| 7.16 | Dismiss / restore a suggestion | POST | **not drawn** | **C** | |
| 7.17 | Märkmed textarea | autosave, HTMX, 900ms | drawn, no wiring | **B** | I14: trigger and interval NOT SPECIFIED |
| 7.18 | Märkmed two captions | «ainult sulle nähtav mustand», «Salvestub automaatselt · ei lähe ajajoonele» | **removed** (§22) | **E** | The copy inventory says in terms this is "not authority to delete them from production without a decision". A private box on a shared record has to say who can see it. |
| 7.19 | Märkmed «Salvesta» button | beside the autosave | **removed** | **E** | Q7 |
| 7.20 | «Salvestatud HH:MM» | **does not exist** | drawn | **E** | Q6 — what it says before the first save, while saving and on failure is undefined. The preview renders it only when there is a real saved time. |

## 8 — Cross-cutting

| # | Capability | Current behaviour | Design | Class | Note |
|---|---|---|---|---|---|
| 8.1 | `can_write` gating | every write control hidden from a reader | design shows every affordance | **E** | 05-state-matrix: read-only is NOT SPECIFIED. The preview honours `can_write` throughout. |
| 8.2 | Visibility scoping | `visible_to` on every child query | not addressed | **A** | the preview reads through the Matter page's own context builders, so scoping is identical |
| 8.3 | HTMX swap targets | `#teema-pais`, `#teema-vaade`, `#jargmiseks-rida`, `#teema-faktid`, `#seotud-materjalid`, `#ajalugu-loend` | not addressed | **C** | the refined layout changes what `#teema-faktid` contains — the intelligence app swaps its own block into it, and after the merge of Kaasamine that block is no longer the same thing |
| 8.4 | Full-page fallbacks (no JS) | every disclosure is a real `details`; every add link has an `href` | not addressed | **C** | the preview's add controls are buttons, so this must be re-established in production |
| 8.5 | Validation / refusal paths | error lines in six places | **none drawn** | **E** | |
| 8.6 | Touch | — | hover-only row actions have no touch equivalent | **E** | Q14, and the design's own QA note 3 |
| 8.7 | Narrow rail order | DOM order | not stated | **E** | Q12 |
| 8.8 | Environment badge | `Sünteetilised` chip in the top bar | absent from the frame | **C** | the handoff calls this "presentation of the prototype's sample environment, not a decision"; the preview keeps it |

---

## The four conflicts, restated

1. **4.4 — the `Järgmiseks` control.** Design: resizable textarea. Production: single-line input with a placeholder. Pick one.
2. **4.10 / 4.11 — `+ Jõustumine` and `+ Töövõit`.** The design's composer row is unconditional; production shows them only when the Matter has no such record. Decide whether the chips are always offered.
3. **6.9 — which timeline badges survive.** The design's selector matches nothing in production. The preview's mapping (hide entry kinds, keep event kinds) is a reading, not a decision.
4. **The `.factrow__dist` column.** `06-responsive-behaviour.md` says the date and the distance "stay on one line". Production's `.factrow__date` is a fixed `15ch` basis — deliberately, so a quarter does not widen one row and break every title's alignment — and `I kvartal 2027 · 203 p` does not fit in it. The preview keeps production's column and lets the pair wrap; it is visible in the third row of the screenshots. Either the column widens (and titles stop aligning) or the distance is dropped for approximate dates.

## Two things the design asserts that its own prototype does not do

- **The 32px shared left edge.** `02-layout-spec.md` says every heading, row and
  control in the main column starts at 32px, and lists the composer's collapsed
  summary among them. Measured in the prototype's own capture, the collapsed
  avatar sits at **64px** — `.composer` already carries 32px of horizontal
  padding, so the rule's `padding-left: 32px` adds to it rather than setting it.
  Everything else measures 32. The preview reproduces the prototype (64), not
  the sentence.
- **The `.uxtl__kind--note` selector** (6.9 above) targets a class production
  does not emit.

## One artefact of the preview fixture, not of the design

The folded system run (`Teema loodud …, tegevusi 5`) sorts to the **top** of the
chronology rather than the bottom. `ChangeEvent` is append-only and stamped at
write time, so on a Matter seeded today every system event is "now" while the
entries are backdated to look like real history. On a Matter that accumulated
its history normally the run sits at the bottom, as the design draws it.
