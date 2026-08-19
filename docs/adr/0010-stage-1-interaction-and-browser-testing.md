# 0010 — Stage-1 interaction model and browser testing

Status: Accepted (Stage 1)

## Context

Stage 1 turns the foundation into a usable application. Two decisions had to be
made before writing screens: how interactive updates work without a client-side
framework, and how anything gets verified when the development machine cannot
run a browser, Docker or PostgreSQL.

## Decision

**Server-rendered HTML with HTMX, and one small JavaScript file.**

There is no build step, no bundler and no component framework. `static/js/app.js`
carries three behaviours: Ctrl/Cmd+K focuses the global search, Ctrl/Cmd+Enter
submits the composer, and a disclosure toggle. Every one of them has a visible
control that works without it.

**HTMX swaps whole surfaces, not fragments.** The composer, the `Järgmiseks`
panel and the timeline are re-rendered together from one set of queries and
swapped as a single block. Patching them individually would let the prominent
action and the chronology disagree about the save that just happened, which is
exactly the confusion the composer exists to remove.

**Validation errors return 400 with the surface re-rendered.** HTMX drops
non-2xx responses by default, so `app.js` opts 400 back into swapping. The
alternative — returning 200 for a failed save — would make the status code lie
about what happened.

**The entry editor is a textarea, not a rich-text component.** It accepts pasted
Word and Outlook markup, and `app.core.richtext` sanitises it server-side
against an allowlist (paragraphs, emphasis, links, lists, simple tables).
Everything else is stripped rather than escaped, so a paste comes out as clean
structure instead of a visible dump of Office markup. `nh3` does the sanitising:
hand-rolling an HTML sanitiser is not a defensible option, and this is the only
dependency Stage 1 adds.

A WYSIWYG toolbar was not built. It would mean a client-side editor library and
a schema negotiation between it and the sanitiser, which is a large surface for
a benefit pilot users have not yet asked for. If they do, the sanitiser is
already the contract it would have to satisfy.

**Playwright is the authoritative Stage-1 check.** The browser suite runs
against a real server on real PostgreSQL 18 in CI and uploads 1440px
screenshots and failure traces. It has no database access on purpose: if the
tests could query around the interface, an authorization bug *in* the interface
would not fail them.

## Alternatives considered

- **React or another SPA.** Rejected by the master specification and by need:
  the workflows are forms, lists and one composer.
- **Per-fragment HTMX swaps with out-of-band updates.** More surgical, but
  introduces the exact class of inconsistency this stage is trying to eliminate.
- **Testing the UI only through the Django test client.** It proves the view
  layer and nothing about whether the page works, which on this machine would
  mean shipping a UI nobody has ever seen running.

## Consequences

- No frontend toolchain to maintain, and no build output to review.
- Whole-surface swaps transfer more markup than a targeted patch. At the size of
  these surfaces that is not measurable, and it buys guaranteed consistency.
- `nh3` is a compiled dependency; it ships wheels for the supported platforms
  and is pinned in the lock file.
- Screenshots must be inspected by a person before the UI is called correct.
  "Playwright passed" only proves the flow works, not that it looks right.
