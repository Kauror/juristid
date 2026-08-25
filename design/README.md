# Design sources

The approved designs this product is built to, as the department supplied them.

Committed rather than linked, because every ADR and half the template comments
cite them by name: a citation nobody can open is a claim nobody can check.

| File | What it is |
| --- | --- |
| `mockups/Uus teema.dc.html` | The approved `Uus teema` design — 8a the empty form, 8b filled, 8c the edge cases. Implemented by ADR 0032. |
| `UUS_TEEMA_PROMPT.md` | The intent behind it, written to the implementer: what was wrong with the old page, what must not change, and the exact copy. |

These are a **record of what was approved**, not a live surface. They are
inline-styled standalone HTML with no build step and nothing imports them. When
the implementation and a file here disagree, the implementation is where the
product is and the ADR says which departures were deliberate.
