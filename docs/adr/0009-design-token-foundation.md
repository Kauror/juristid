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
