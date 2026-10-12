# 0156 — `Hetkeseis` is changed directly from the Teema header

**Status:** accepted
**Date:** 2026-10-12

## Context

The Teema header shows `Hetkeseis <value>`. It was already an inline editor —
a `<details>` holding a `<select>` and a `Salvesta` button posting to
`update_field` (ENG-035 kept it from saving on `change`) — but lawyers read the
value as text and changed the stage by writing a `+ Märge` with `Uus
hetkeseis`, creating a note whose only content was the stage move. The owner
asked (2026-10-12) for the header's value to be the place the stage is chosen,
with no note or dummy activity, through the canonical writer, and without
weakening closure confirmation or closed-Matter rules.

## Decision

1. **The value is a menu, and choosing is the save.** For a writer on an open
   Matter, `Hetkeseis` opens (`<details data-uxpopover>`) a short list of the
   stages, each a submit button (`templates/matters/partials/header_stage.html`).
   One click to open, one to choose. An empty stage reads «Vali». The current
   stage is marked (`aria-current`) and not pressable; «Määramata» clears a
   stage that is set. Retired stages the Matter stands in are named
   «— varasem hetkeseis», as before.
2. **Buttons, not a `<select>` on `change`.** A keyboard walk through a select
   fires `change` at every stop, which is why ENG-035 refused autosubmit; a
   button saves only when pressed. Tab/Enter operate it, the disclosure
   announces its state, Escape and a click outside close it (the popover
   contract in `static/js/ux.js`). Nothing is hover-only.
3. **The canonical writer only.** The menu posts to `update_field` →
   `change_stage` → `stage_transition`, the path `Muuda teemat` uses: the
   Matter lock, a new `Hetkeseis` period (`MatterStageEpisode`),
   `MATTER_STAGE_CHANGED`, `Teema käik`, the legal-process rail and the search
   refresh are exactly those of any other stage change. **No `Entry` is
   created** — no `Märge`, no note, no dummy activity.
4. **A stale page is refused.** `stage` joins `GUARDED_MATTER_FIELDS`: the menu
   posts `revision` (`matter_field_revision(matter, "stage")`), the guard
   compares it under the row lock, and a mismatch — or a missing token — is
   refused with 409 and the header redrawn from the stored stage.
5. **Closure still asks** (docs/adr/0152). A terminal stage on a Matter with
   live work returns the warning and one confirming button, which carries the
   same `revision`; nothing is saved until it is pressed.
6. **Closed Matters are unchanged** (RULE-03, docs/adr/0131 §12): the header
   states the stage with no menu, and the stage moves only through `Ava
   uuesti`. A reader sees the value and no control; the route refuses them.

7. **The header's editors are anchored again.** Investigating why the
   existing select was not recognised found the real cause: every header
   inline editor (`Hetkeseis`, `Vastutaja`, `Valdkond`, `Saabus`, the
   deadline) was anchored on `.metafield`, a class nothing draws any more, so
   its panel opened against the initial containing block — one viewport height
   down the page, off screen. `.metaline__item` is the anchor now (the whole
   meta line under 720px, so a panel opens beneath it at the line's width), and
   the stage menu is placed inside the window by the popover contract
   (`data-uxfloat="fit"`). `e2e/test_koda_proposal_create_flow.py` pins that
   every header editor opens on screen at 1440 and 375px; against the old
   stylesheet `Vastutaja`'s panel measured y=804 in an 800px window.

## Consequences

- `+ Märge` with `Uus hetkeseis` remains for a move that belongs with a note; it
  is no longer the only obvious way.
- Tests posting `update_field` for `stage` send the revision, like the other
  guarded fields.
- The header's visual baselines change where the menu is drawn.

## Reversibility

By code; no migration. The guard and the menu are independent: dropping `stage`
from `GUARDED_MATTER_FIELDS` restores tokenless posts.
