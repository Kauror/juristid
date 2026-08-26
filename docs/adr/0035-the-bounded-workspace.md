# 0035 — The shell spans the viewport, the workspace does not

- **Status:** Accepted
- **Date:** 2026-08-26
- **Depends on** ADR 0009 (the token architecture: primitives, semantic tokens,
  and components that consume only the semantic layer) and ADR 0010
  (server-rendered HTML, browser tests as the verifier). Neither is amended.
- **Relates to** ADR 0030 (the Teema workspace redesign) and ADR 0033 (the
  Ülevaade rebuild), whose full-bleed band layouts are the surfaces this
  bounds. Neither design decision is reversed.

## Context

A human QA pass on a 3440px ultrawide monitor found the application hard to
scan. Nothing was broken, nothing overflowed, and every assertion in the browser
suite passed. The pages were simply given the whole screen.

The failure is specific and measurable. Ülevaade's intervention rows are a grid
of `96px | 1fr | 132px | 116px` — a date and its meaning, the title, the stage,
the owner. On a 1440px monitor that row is 1036px wide and reads as a row. On a
3440px monitor it is 3036px wide: the title sits at the left bezel and the stage
and owner that qualify it are 2800px away, with two thousand pixels of empty
canvas in between. The facts rail, being the last column of a grid that had the
viewport to spend, ends up against the opposite bezel. Reading one row means
moving your head.

The same shape appears on Minu töö and on the Teema page, and for the same
reason: all three are deliberately **full-bleed** surfaces. ADR 0030 and ADR
0033 chose bands separated by hairlines over a column of cards, and a band that
does not reach the edge of its container reads as a card with the border filed
off. Each of them therefore sets `max-width: none` and pulls itself back out of
`.app__main`'s padding with a negative margin. That was right. It was also
unbounded, and the viewport grew past the design.

The centred pages did not have the problem, because `.page` already carried
`max-width: 1376px` — the content width of the 1440px primary viewport. So the
application already believed work should be bounded. It only said so in one
place, and that place was the container the dashboards deliberately do not use.

## Decision

**The shell spans the viewport. The workspace does not.**

The bound moves up one level, from the page container to the shell's main
region, so that both families inherit it:

- `.app__main` carries `max-width: var(--layout-workspace-max)` and centres
  itself. The full-bleed pages keep their negative margins and now bleed to the
  edges of that box rather than to the edges of the monitor.
- `.page` derives its own maximum from the same token
  (`--layout-workspace-content`, the workspace less its two gutters) instead of
  carrying an unrelated 1376px. Above 1600px the register and the dashboard now
  begin and end in the same place; below it, nothing changes.
- The top bar, the global search, the shell background and the footer band
  continue to span the viewport. They are chrome, and a hairline that stops in
  mid-air on a wide monitor reads as a rendering fault rather than as a
  decision. What the footer *says* is aligned with the workspace; the band
  itself is not.

Three tokens, in the primitive spacing block:

```css
--layout-gutter: var(--spacing-8);
--layout-workspace-max: 1600px;
--layout-workspace-content: calc(var(--layout-workspace-max) - var(--layout-gutter) * 2);
```

`--layout-gutter` is the shell's own horizontal padding, named rather than
repeated. Every full-bleed page pulls itself out by exactly that amount, so the
two numbers must not be able to drift — and they had. Below 860px the shell
drops to a 12px gutter while the Teema page was still pulling itself out by 32,
which put 20px of horizontal scrollbar on that page at every width from 721 to
860. That is fixed here as a consequence of naming the number, not as a separate
change.

### Why 1600px

Tuned against the pages, at 3440, with 1500 and 1650 rendered beside it:

| | Ülevaade row | flexible title cell |
|---|---|---|
| 1440 (primary viewport, today) | 1036px | 594px |
| **1600 (this decision)** | **1196px** | **754px** |
| 3440 unbounded (the QA screenshot) | 3036px | 2594px |

1500 squeezes the Seis strip back onto one crowded line. 1650 reopens a visible
gap between a row's title and the stage and owner that qualify it. 1600 leaves
an ultrawide screen better than 1440 — which is the point of having one — while
keeping a row inside a single comfortable sweep.

The bound first bites at 1920, which is a wide desktop rather than an ultrawide
one. That is deliberate: the alternative is a maximum that only engages on
hardware almost nobody has, which would leave 1920 and 2560 unfixed.

### What is deliberately not changed

**The rails keep their three different widths** — 372px on Minu töö, 340px on
Ülevaade, 300px on the Teema page, all narrowing to 300px below 1400. They hold
different things, they were tuned separately, and each is locked by a committed
visual baseline at 1440. Unifying them is a design decision about three
surfaces, not a layout-system correction, and folding it into this change would
have made a one-primitive diff into a redesign nobody asked for.

**The rails stay flush against the main column**, with a border and a quieter
surface rather than a gap. They are bordered panels, not floating columns; a
gap would turn the rail into a card and contradict ADR 0030. "Attached to the
main content" is asserted as *the rail's right edge is the workspace's right
edge* — which is now true, and was the actual complaint.

**No page gets a `max-width` of its own.** One primitive and no exceptions; if a
future surface genuinely needs a different measure, it gets a named token and a
reason, not a number in a page stylesheet.

## Consequences

- Every width in the supported desktop ladder — 1440, 1366, 1280, 1024 — renders
  byte-identically. This was verified by rendering both stylesheets on one
  machine and comparing: fifteen scenarios, zero differing pixels. No committed
  visual baseline needed regenerating.
- Four new baselines cover 3440: Ülevaade, Minu töö, Teemad and Statistika.
- `e2e/test_ultrawide_workspace.py` asserts the relationships a screenshot
  cannot: bounded, centred, no overflow, content inside the workspace, rail
  flush with it — and, on the other side, that 1280–1440 keeps every pixel it
  had. That last one is the guard against this decision being over-applied
  later.
- Horizontal overflow was swept across 27 widths on seven surfaces, before and
  after. Three pre-existing overflows are fixed (the Teema page at 740–860px)
  and none are introduced.
- Pre-existing overflow at 861–900px on every page, and on Statistika below
  400px, is **not** addressed here. It sits below the supported ladder, it is
  unchanged by this branch, and it is a different defect.
