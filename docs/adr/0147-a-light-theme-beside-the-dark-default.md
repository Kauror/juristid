# ADR 0147 — A light theme beside the dark default, chosen per browser

- Status: accepted
- Date: 2026-10-08
- Stage: owner's light-theme round
- Amends: ADR 0009 (the light block is no longer a provisional proof of the
  architecture; it is a supported theme)
- Related, unchanged: ENG-009 (nothing a page showed is kept in the browser;
  sign-out clears the origin's storage), ENG-101 (small secondary text meets
  AA), ENG-124 (the browser policy: `script-src 'self'`), ADR 0058 (Barlow),
  the header emblem's filter (PR #75)
- Master specification: §17.1 says «dark mode is the MVP theme; a light theme
  is not required unless pilot evidence or accessibility review shows a concrete
  need», and §1.7 and §3.12 that the token layer must keep a light theme cheap.
  The owner asked for the light theme; this ADR records that request as the
  need. Dark stays the primary expression and the default, so the
  specification's dark-first direction is unchanged.

## Context

`static/css/tokens.css` has carried two semantic blocks since Stage 0: the dark
theme every page used, and a light block that existed "so the architecture is
proven to support a future accessible light theme by re-pointing semantic
values, with no component changes" (ADR 0009). `templates/base.html` hard-coded
`data-theme="dark"`, so no page could reach the light values, and they were
marked provisional because nobody had looked at them on a page.

The owner wants the light theme as a real, fully supported alternative, with a
small switch on the bar, dark staying the default, and the choice remembered.

## Decisions

### 1. Dark is the default; light is chosen, never inferred

The server renders every page `data-theme="dark"`, as before. A browser that has
chosen light gets light; one that has not gets dark. The operating system's
preference (`prefers-color-scheme`) is deliberately **not** followed: the owner
wants dark as the default for everybody, and a page that changed with the
laptop's evening setting would make the default a property of the machine.

### 2. One switch, icon-only, beside the account controls

`templates/components/theme_toggle.html`, included on the bar twice: signed in,
between the environment badge and the persona (left of them, so the persona
and «Välju» keep the positions they have always had); signed out, at the bar's
right end.

- **The icon is the action.** A sun offers the light theme; a moon offers the
  dark one. The spoken name says the same in words: «Lülita heledale teemale»,
  «Lülita tumedale teemale». No `aria-pressed` — the name already changes with
  the state, and a toggle that did both would announce itself twice.
- **No state in `<body>`.** Both icons and both names are in the markup; four
  rules in `app.css` show one pair, chosen from `<html data-theme>`. A hidden
  element is out of the accessibility tree too, so the spoken name changes with
  the icon and no script keeps either in step — which is also why nothing an
  htmx swap brings in can disagree with the theme.
- **A native `<button type="button">`**: keyboard and pointer for free, the
  global `:focus-visible` ring, and a press inside a form submits nothing.
- **Shown only when it works.** It does nothing without `static/js/theme.js`,
  so it is `display: none` until that script sets `data-theme-switchable` on
  `<html>` — before `<body>` is parsed, so showing it moves nothing after the
  first paint.
- **It costs the bar nothing.** 30×30, the height of the search field. It keeps
  8px to the persona rather than the bar's 24px, which keeps the one-row bar
  inside its padding at 861px, where the search field is already at its
  minimum. Measured: the bar stays one 48px row from 861px to 1920px, keeps its
  height at 375px, 768px and 860px, and the document never scrolls sideways
  (e2e/test_theme.py).

### 3. A preference of this browser, not of a person

`localStorage["juristid-theme"]`, holding `dark` or `light` and nothing else —
no cookie, no account field, no request, no migration. Behind the shared gate
the person on the bar is a view somebody picked (ADR 0016), so an
account-owned preference would follow the persona rather than the person at the
keyboard; a browser preference cannot.

- A value other than `dark` or `light` is ignored and the dark default stands.
- Storage that throws — a locked-down profile, some privacy modes — leaves a
  working dark page whose switch still works for as long as the page is open.
- Another open tab follows a switch (`storage` event), and a page restored from
  the back-forward cache re-reads the choice (`pageshow`).
- This is the one value the application keeps in the browser. ENG-009's rule is
  that nothing a *page showed* is kept there; a theme name is not page content,
  and `tests/test_theme_contract.py` holds the script to exactly one key with
  exactly two values.

### 4. Signing out clears it, with everything else

`Clear-Site-Data: "storage"` on sign-out (ENG-009) is the promise that no trace
of a session stays in the profile. The preference is not carved out of it: a
cosmetic choice is not worth the first exception to a privacy rule, and a cookie
— the one store that header leaves alone — is exactly what this preference must
not be. So the page signing out lands on is dark, with the switch on it, and a
choice made there carries into the next session. `e2e/test_theme.py` holds
both halves.

### 5. No flash of the wrong theme

`static/js/theme.js` is the first thing in `<head>` of both document roots
(`base.html`, and `error_page.html` for the 404/500/CSRF pages), **before the
stylesheets and without `defer`**. It runs before `<body>` exists, so the first
frame that has anything of the page in it is already in the chosen theme; being
ahead of the stylesheets, it does not wait for them. It is a same-origin file,
so the browser policy is unchanged — no inline script, no `unsafe-eval`, no
new source. It is its own file because `app.js` is deferred, which is exactly
too late. The browser test records `<html data-theme>` on every animation frame
from document creation and fails if any frame with a `<body>` is dark; the same
test fails on a deferred script.

### 6. The light palette, and one new role

The light block in `tokens.css` is rewritten as a complete palette and
documented where it is defined:

- **Surfaces**: a light grey canvas (`--surface-base` #f2f4f7); white for what
  sits on it — the bar, cards, rows, menus and popovers; the head bands a half
  step lighter than the canvas (`--surface-panel`), the facts rail a step
  darker (`--surface-rail`), capture surfaces faintly Chamber-blue. Elevation is
  said with a border and a shadow, as a light interface says it.
- **Ink**: graphite, never black. Every text role, `--text-muted` included, is
  ≥4.5:1 on every surface and every status fill.
- **Chamber blue** #009FDA stays the brand, but as text or under white text it
  is 2.9:1 on white; the interactive blues are its darker steps in the same hue
  (`--accent-primary` #00739f, `--accent-link` #006a93), so links, the primary
  button and the focus ring are AA.
- **Status** colours are the dark theme's hues taken dark enough to be text on
  white and on their own soft fill.
- **Boundaries a person has to find** — a text field, a focus ring, a selected
  option's accent edge, a refused field's danger edge — are ≥3:1 against the
  surfaces they sit on (WCAG 1.4.11). Decorative separators stay quiet.

One role is new: **`--border-input`**, the edge of a field somebody types into.
In the dark theme it *is* `--border-control`, so the dark theme draws exactly
what it drew. In light, a field is found by its edge alone and needs 3:1;
drawing every badge, tag and menu rule that dark would turn a quiet page into a
grid. `.field__input`, `.searchfield__input`, the Teema composer and the rail
note read it, and so does Minu asjad's own search box, which used
`--border-default` — the one change below the bar in the dark theme, 7/255 per
channel on a one-pixel edge, under the visual suite's threshold and invisible.

Two component rules read primitives directly and are now roles with the same
dark value: `.uxav` (`--primitive-neutral-600` → `--surface-overlay-hover`, a
near-black disc under grey initials in light, 1.8:1) and the statistics
"unknown" hatch (`--primitive-neutral-400` → `--border-strong`).

### 7. Components consume roles; a light-only override is refused

The rule ADR 0009 stated in prose is now enforced
(`tests/test_theme_contract.py`):

- no component reads a `--primitive-*`;
- the light block defines every role the dark block does — a role it forgot
  would silently fall through to its dark value;
- no component rule reads the theme attribute except the emblem's filter
  (artwork, not colour) and the switch's own four rules. A
  `[data-theme="light"] .component { … }` fixes one component and teaches the
  next author to fix theirs the same way.

**Adding a component that supports both themes**:

1. Use only semantic roles (`--surface-*`, `--text-*`, `--border-*`,
   `--accent-*`, `--status-*`, `--shadow-*`). Never a primitive, never a hex.
2. Pick the role for its *meaning*, not for how it looks in dark: text on a
   popover is on `--surface-elevated`; a hover inside a popover is
   `--surface-overlay-hover`; a field edge is `--border-input`.
3. Quiet text (`--text-muted`) only on the page's own surfaces; on a selected,
   elevated or tinted surface use `--text-secondary` (ENG-101).
4. If no role is right in both themes, add a role to **both** blocks of
   `tokens.css`, with the dark value equal to what dark draws today if dark is
   not meant to change, and a comment saying why it exists.
5. Look at it in both themes, run `tests/test_text_contrast.py` and
   `e2e/test_theme_contrast.py`, and give it a visual scenario in each theme if
   it is a surface people work in.

### 8. What is measured, in each theme

- **Palette** (`tests/test_text_contrast.py`): every text role on every surface,
  text on every coloured fill, the focus ring and the selected marker on every
  surface — both themes; field, selected and refused edges ≥3:1 in light.
- **Rendered pages** (`e2e/test_theme_contrast.py`): every visible piece of text
  on fifteen pages and overlays, with backgrounds composited and fades applied,
  in both themes.
- **The light theme meets AA throughout.** The dark theme meets it everywhere
  but three gaps it carried before this round and keeps unchanged, because the
  dark theme is the approved design and this round does not redesign it. They
  are named exactly, so they can close but not widen:
  `--text-muted` on `--surface-elevated` (4.49:1; nine popover labels), on
  `--surface-overlay-hover`, `--surface-selected`, `--accent-soft` and
  `--status-success-soft` (4.11–4.43:1, not on any rendered page at rest);
  `--text-atypical` (3.61:1, by design quieter than muted, ADR 0130); and the
  field edge (1.43:1 — a dark field is told from the page by its fill). Closing
  them is a dark-theme change for its own round.

### 9. Visual regression in both themes

- **Dark**: the switch is new on the bar, and the search field, «Uus teema» and
  the environment badge left of it move 38px left. Every dark scenario that
  contains the bar changes there and only there. Before adopting a candidate,
  the difference against the old baseline is checked to lie inside the bar
  (y < 48 px) — a dark candidate that differs anywhere else is not adopted.
  Rendered locally against `main` on nineteen pages, nothing below the bar
  differed except the 7/255 edge in §6.
- **Light**: ten `hele-*` scenarios — the shell, Minu asjad, Osakond, the
  register with and without its narrowing panel, a Teema, its documents, a
  refused Uus teema, the persona popover and the door. Each is the twin of a
  dark scenario and follows that scenario's clock contract (`capture(…,
  rules=…)`), so a pair differs in colour and nothing else. The theme is
  chosen as a person chooses it: the stored preference, there before any page
  script runs.
- Masks are painted in the page's own background colour, read from the page —
  on the dark default exactly the `#101418` the harness used to say literally.

## Alternatives considered

- **Follow the operating system** (`prefers-color-scheme`). Refused by the
  owner: dark is the default for everybody.
- **A server-side preference** (a user field, a session key, a cookie). An
  account field is wrong behind the shared gate (§3); a cookie is sent with
  every request for something only the browser needs, and survives sign-out,
  which the privacy rule says nothing may.
- **Inline `<script>` in `<head>`**. Refused by the browser policy; a nonce
  or hash would be a policy change for one line.
- **Light-only overrides per component.** Refused (§7); the one role added is
  the alternative.

## Consequences

- No migration, no model, no view, no permission and no workflow changes. The
  server renders exactly what it rendered; the switch writes nothing anywhere
  but the browser.
- `tokens.css` is now two complete palettes, and a new role is two values.
- Every future visual change that touches a surface people work in owes a look
  in both themes; the visual suite holds ten of them.

## Reversibility

High. Removing the switch and the script returns every page to the dark theme
it always rendered; the light block can stay as the proof ADR 0009 kept it as.
