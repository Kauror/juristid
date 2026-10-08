# 0009 — Design-token foundation

- **Status:** Accepted (Stage 0)
- **Date:** 2026-08-18

## Context

The production interface is a new dark-mode-first Koda CVI application, and
component tokens must not make a future accessible light theme prohibitively
expensive. The official CVI package — logo variants, brand colours, licensed web
fonts, dark-mode interpretation — has **not been supplied**, and the
specification is explicit that the coding agent must not guess a brand system
from the public website.

## Decision

**Ship the architecture, not the brand.**

`static/css/tokens.css` defines three layers:

1. **primitives** — raw values, never consumed by components;
2. **semantic tokens** — `--surface-*`, `--text-*`, `--border-*`, `--focus-*`,
   `--brand-*`, `--status-*`, spacing, radius, typography, motion; the only
   thing components may use;
3. **themes** — semantic values for dark (the MVP theme, the default) and a
   light block that exists to prove a light theme needs different values, not
   different components.

   **Superseded on 2026-10-08 for the light block's status — see the amendment
   at the end of this document.** The light block is a supported theme now,
   chosen per browser; dark is still the default (ADR 0147).

Every colour value is **PROVISIONAL** and marked as such in the file, on the
`/disainisusteem/` page and in this ADR. They are contrast-oriented placeholders,
not Koda brand values. Replacing them when the CVI package arrives is an edit to
one file.

Dark-mode rules already encoded: layered dark neutrals rather than absolute
black; a dedicated `--surface-document` container so white document previews do
not create glare; tables readable without zebra fills; a visible focus ring with
offset; status conveyed by label as well as colour; reduced-motion honoured.

**Tailwind is not adopted in Stage 0.** The specification allows it but does not
require it, and adding a Node build step now would buy nothing: Stage 0 has no
components. When Stage 1 builds the component library, Tailwind may be adopted
by mapping its theme to `var(--token)` — the tokens are the contract either way,
so the decision costs nothing to defer and adding a build toolchain to CI early
costs something real.

**HTMX** is likewise added in Stage 1, pinned and vendored, together with the
first interaction that needs it.

## Consequences

- No component will hard-code a brand hex value, because there is none to
  hard-code.
- The CVI hand-over is a token substitution, not a redesign.
- Stage 1 must not introduce a component that reads a primitive directly.

## Open decision

Official CVI package, permitted web fonts and the dark-mode interpretation —
owner: Communications/CVI, required for Stage 1.

## Reversibility

High.

## Update — Stage 1: the CVI package arrived

The placeholder palette is gone. `static/css/tokens.css` now carries the
Chamber's own values: brand blue `#009FDA` and a graphite-derived dark neutral
ramp, both taken from the supplied CVI usage in `Kauror/koda` and
`Kauror/dashkoda` rather than sampled from the public website. The ramp is the
one already validated for colour-blind legibility there.

The three-layer architecture this ADR set up did its job: swapping a placeholder
palette for the real one changed **only** the primitive and semantic values. No
component was touched, and the light-theme block still proves a future light
theme needs different values rather than different components.

**Typeface.** The CVI face is FF DIN Pro and its web licence has not been
delivered. Barlow is the approved visual fallback and ships first in the stack
after it, so adding FF DIN Pro later is an `@font-face` addition, not a
redesign.

**Barlow is self-hosted** (`static/fonts/`, latin and latin-ext, 10 files,
~200 KB) rather than loaded from a font CDN. This application will hold
confidential member material, and a third-party request on every page view
discloses who is using it and when. That is a departure from the design
package, which links Google Fonts; it is visually identical and reversible in
two lines if the Chamber would rather match the handoff exactly.

**Status is never colour alone.** The three action modes differ by shape as well
as tint — TEEN filled, OOTAN solid outline, JÄLGIN dashed — and every date
carries a written label saying whether it is a deadline, a review date or an
expectation.

---

## Amendment, 2026-10-08 — the light block is a supported theme

- Status: accepted, amending «themes — … a light block that exists to prove a
  light theme needs different values, not different components» and the
  Stage-1 update's «the light-theme block still proves a future light theme
  needs different values rather than different components»
- Scope: the light semantic block of `static/css/tokens.css`; how a page
  reaches it; what components may read. Decided in ADR 0147.

### What was decided before

The light block existed only to prove the architecture: no page could reach
it (`templates/base.html` hard-coded `data-theme="dark"`), and its values were
marked provisional.

### Why it is superseded

The owner asked for a light theme as a fully supported alternative, with dark
staying the default (ADR 0147).

### What is decided now

- The light block is a complete, reviewed palette, defining every role the
  dark block defines; a browser reaches it with the switch on the bar, and the
  choice is kept in that browser.
- The proof this ADR asked for held: the light theme needed new values, one new
  role (`--border-input`, equal to `--border-control` in dark) and two
  component rules moved from primitives to roles — no component was redesigned.
- The rules this ADR stated in prose are tests now
  (`tests/test_theme_contract.py`): no component reads a primitive, both blocks
  define the same roles, and no component carries a light-only override.

### What this amendment does not change

The three layers, the rule that components consume roles and never values,
the CVI values of the dark theme, dark as the default and the primary
expression, Barlow, and status never being colour alone. The dark theme draws
what it drew before.
