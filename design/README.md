# Design sources

The approved designs this product is built to, as the department supplied them.

Committed rather than linked, because every ADR and half the template comments
cite them by name: a citation nobody can open is a claim nobody can check.

| File | What it is |
| --- | --- |
| `mockups/Uus teema.dc.html` | The approved `Uus teema` design — 8a the empty form, 8b filled, 8c the edge cases. Implemented by ADR 0032. |
| `UUS_TEEMA_PROMPT.md` | The intent behind it, written to the implementer: what was wrong with the old page, what must not change, and the exact copy. |

## screenshots/

The implementation beside the design, at the viewports the round was reviewed
at. Produced from this repository's own template and stylesheet — the real
`matter_create.html` rendered by Django with the production Valdkonna,
Hetkeseis and Menetlusliik vocabularies and a stubbed choice list, photographed
in headless Chromium. Not a mockup and not a hand-built page.

They exist because a review comparing an implementation with a design needs both
in one place, and because the visual-regression baselines under `e2e/baselines/`
are a different thing: those are a machine's record that nothing moved, taken on
the CI image, and they are unreadable as an argument about whether the design
was followed.

These are a **record of what was approved**, not a live surface. They are
inline-styled standalone HTML with no build step and nothing imports them. When
the implementation and a file here disagree, the implementation is where the
product is and the ADR says which departures were deliberate.
