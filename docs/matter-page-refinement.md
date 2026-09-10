# Teema page refinement — functional reconciliation

The 2026-09 refinement of the Matter page is a change of **order, grouping,
weight and chrome**. It is not a redesign, it introduces no business concept, it
changes no model and it removes no capability.

The design it implements was approved from a working preview rendered on a
populated synthetic Matter (`preview/matter-page-refinement` @ `4cbc71e`), which
is kept as the visual reference. The design package itself is
`design-handoff/matter-page-refinement`, whose README states the governing rule
twice:

> **THIS DESIGN DOES NOT AUTHORISE REMOVAL OF EXISTING APPLICATION
> FUNCTIONALITY.** Absence from these screenshots is not permission to delete.

This document is the audit of that rule. Every capability the page had before is
listed with what happened to it. There are three outcomes and no fourth:

| | |
|---|---|
| **A** | preserved — same behaviour, **new presentation** |
| **B** | preserved — same behaviour, same presentation |
| **C** | explicit approved presentation change |

Nothing is classified "dropped", "visual-only" or "assumed obsolete", because
nothing was.

## What actually changed

**Order.** The main column carried seven sibling blocks of equal visual weight,
so a reader could not tell at a glance which was an action, which a fact and
which history — and the chronology, which is what a lawyer opens the file for,
began below the fold. It is now three zones on three grounds:

```
A   Järgmiseks + composer      action      --surface-capture, one 3px accent edge
B   the facts panel            fact        --surface-panel
C   Ajajoon                    history     the page's own ground
```

**Grouping.** `Olulised tähtajad`, `Jõustumine`, `Töövõidud` and `Kaasamine`
were four unrelated blocks in two apps, separated by the composer. They are the
Matter's dated facts and they now share one panel, one date column and one
heading treatment. `Seotud materjalid` left the main column for the rail: it is
look-up material.

**Weight.** One heading treatment for every section label on the page. The entry
body outranks its metadata in the chronology instead of the reverse. Row actions
and inline-edit underlines appear when reached for.

**Chrome.** Section heads no longer reserve a button's height; add controls sit
under the last row. Timestamps end the meta line instead of standing in a
column. The badge that said «Märkus» on almost every entry is hidden.

## Zone A — Järgmiseks and the composer

| Capability | Outcome | Note |
|---|---|---|
| Step text, date at stored precision | **A** | square and full-bleed, 32px edge, capture ground |
| `✓ Tehtud` | **B** | swaps `#jargmiseks-rida` only, as before |
| `Lükka edasi ▾` and its options | **B** | still offered only on an exact date |
| `Muuda` → focus the composer | **B** | |
| Overdue row | **A** | `.uxnext--overdue` keeps its ring; the accent edge rides inside it |
| Empty / register-instruction / closed variants | **B** | not drawn by the design, untouched here |
| Refused completion message | **B** | |
| Composer open/close, all fields, all panels | **B** | |
| Composer body height | **C** | 108px → 60px at rest (handoff §18); still grows on focus |
| `L` key hint on the collapsed row | **C** | hint removed, **shortcut kept** — `ux.js` still binds it, and the row is its own trigger (§20) |
| `+ Jõustumine` / `+ Töövõit` | **A** | moved from a row under the facts into the composer's action row (§5). Same routes, same `hx-target`, same conditions; only the trigger moved. Their forms still open in the facts block, beside the records they join. |

## Zone B — the facts panel

| Capability | Outcome | Note |
|---|---|---|
| `Olulised tähtajad` list | **A** | first section of the shared panel |
| Distance beside a date | **C** | `.factrow__dist`, new in this design (C12). Sourced from `MatterImportantDate.distance_label`, which reuses the header band's own sixty-day window rather than inventing a rule. Empty for a past date — what one should say is undetermined and is left alone. |
| Past important dates | **B** | still rendered, still `factrow--past` |
| Cancelled / superseded flags | **B** | |
| Row `Muuda` / `Tühista` | **A** | visible on hover **and on `:focus-within`**, so they stay keyboard-reachable; shown unconditionally under `@media (hover: none)`, because a touch device has no hover and an unreachable control is worse than a busy row |
| Add control | **C** | moved from the section head to under the last row; `+ Lisa oluline tähtaeg` → **`+ Lisa tähtaeg`**, the approved design's wording |
| `Jõustumine` section, inline add, cancel, standalone route | **B** | **kept as its own section.** The design draws a commencement as a row inside `Olulised tähtajad` with the qualifier «jõustub», and whether the panel absorbs Jõustumine is the handoff's own open question Q1. Merging them would answer it by fiat and would take the section's add, edit and cancel controls with it. |
| `Töövõidud` section, review controls | **B** | as above |
| `Kaasamine` | **A** | an accordion became a section of the panel: always open, date-first rows in the shared column, add control under the last row |
| Kaasamine count, summary line, `+ Lisa` head button | **C** | a collapsed row needed all three to say what was inside it; an open one shows the rows |
| Kaasamine empty-state sentence | **C** | `Kaasamist ei ole kirja pandud` — an absence the layout states |
| Kaasamine add / edit / refusal reopen | **B** | `engagement_add_open` still governs the disclosure |
| Six register observation notes | **B** | member feedback, «ei saatnud», recorded send date, unreadable VÄLJA, multiple addressees, continuation — all still rendered in the section |

`#teema-faktid` and `#kaasamine` are two HTMX swap targets inside one visual
panel. Nesting Kaasamine inside the intelligence wrapper would have made an
`+ Jõustumine` save silently delete it; the panel is a surface, not an owner.

## Zone C — the chronology

| Capability | Outcome | Note |
|---|---|---|
| Entries, events, spine, dots, deep links | **B** | |
| System-event runs and their expansion | **B** | |
| `Näita varasemaid` paging | **B** | |
| Open by default, closed summary | **B** | |
| Caret | **C** | hidden by the stylesheet; the `<details>` still opens and the whole head is its trigger |
| `Ava ajajoon` text action | **C** | removed — it said what the layout said (§12) |
| Timestamp position | **C** | ends the meta line instead of a right-hand column (§13) |
| Body vs metadata weight | **C** | body primary, actor and meta secondary (§13) |
| Entry kind badge | **C** | hidden for entries, **kept for events**. Every note said «Märkus»; `Järgmiseks tehtud` and `Väljasaadetud · arvamus` each say something that is nowhere else on the line. `item.kind_label` still renders and every reporting surface still reads the stored kind. |
| **Filter (Kõik / Sissekanded / Sündmused)** | **B** | **kept.** The design's frame does not draw it and the reviewer asked for its removal from the drawing, but `?ajajoon=` is a link somebody can send and the route serves every slice. Restyled to three quiet words. Whether the capability should go is the handoff's Q10 and is a product decision, not a presentation one. |

## The rail

| Capability | Outcome | Note |
|---|---|---|
| Teemaviide, Menetlusliik, Kellelt, Kellele, Saabus | **B** | inline editors unchanged |
| Inline-edit dashed underline | **C** | on hover and on `:focus-visible` rather than at rest (§17) |
| Kirje liik, Suletud, Põhjus, Sulges | **B** | |
| **Muu valdkond, Andmeklass, Märgi pärisandmeteks/testandmeteks** | **B** | **kept.** Absent from the frame; the reviewer's note was "not used / going away", which is a product decision nobody has taken. Retiring the data-class control would remove the only UI for that boundary. |
| `Koja arvamus` card | **B** | |
| `Seotud materjalid` | **A** | **moved into the rail** (§6). The section's own template, unforked — the same fragment `related_materials:section` swaps in and the same one `section_page.html` serves without scripting — restyled to a card. Confirmed relations, background material, suggestions, `Seo teemaga`, `Eemalda`, dismiss, restore, the pagers and the picker are all still there. |
| `Seotud` card (eelnev / jätkub / kaastöötaja) | **B** | |
| `Märkmed` autosave, explicit save, two captions | **B** | **kept.** The design draws one label and a saved line. The captions say who can see the box and that it never reaches the chronology — answers a private box on a shared record has to give — and the copy inventory says in terms that their absence is "not authority to delete them from production without a decision". Restyled quiet. |

## Copy

Every user-facing literal in this change comes from the approved design or from
existing production copy retained with its behaviour. **Invented literals: 0.**

| Literal | Source |
|---|---|
| `+ Lisa tähtaeg` | approved design (replaces `+ Lisa oluline tähtaeg`) |
| `Olulised tähtajad`, `Kaasamine`, `Ajajoon`, `Järgmiseks` | approved design, unchanged from production |
| `+ Lisa kaasamine`, `+ Lisa jõustumine`, `+ Lisa töövõit` | existing production copy |
| `Kõik` / `Sissekanded` / `Sündmused` | existing production copy, for a retained capability |
| `Kuupäev teadmata`, the six register notes, the Märkmed captions | existing production copy |
| `N p` | existing production form, already used by the header band for the same idea |

Removed, each named under TEXT NOT PRESENT in the design's copy inventory:
`Ava ajajoon`, `+ Lisa oluline tähtaeg`, `Kaasamist ei ole kirja pandud`, the
composer's `L` hint, and the Kaasamine head's count and summary line.

## Known deviations from the approved screenshots

Three, all deliberate, all in favour of preserving behaviour:

1. **`Jõustumine` renders as its own section** rather than as a `jõustub` row
   inside `Olulised tähtajad`. Handoff Q1/Q3; merging answers them by fiat.
2. **The chronology filter is present**, quietly. Handoff Q10.
3. **`Muu valdkond`, `Andmeklass`, the data-class toggle and the Märkmed
   captions are present.** Handoff §21, §22 and Q7.

A fourth is a difference between the design's prose and its own drawing: the
layout spec says every main-column control starts at 32px and lists the
composer's collapsed summary among them, but the prototype renders that row at
64px, because `.teema .composer` already carries 32px of padding. The drawing
was approved, so the drawing is what is implemented.

## Not changed

No migration, no data migration, no search version, no archive version. No
model field, no service, no selector signature, no route, no permission and no
HTMX contract. One model gained one derived read-only property
(`MatterImportantDate.distance_label`) and no column.
