# 0171 — The browser layer is one delegated script, and a feature that fails to start stops only itself

**Status:** proposed — the start-up isolation (§2) is implemented; the AGENTS.md wording (§1) and the rules for adding a feature (§3) wait for the owner's approval.
**Date:** 2026-10-18

**No migrations**, no template change and no change a person can see. This
record describes the JavaScript the application already has, fixes how it
starts, and proposes wording for AGENTS.md. Finding JARCH-004 of the
2026-10-17 audit (Phase 2, G16).

## Context

AGENTS.md lists, among the preferred architecture characteristics:

> minimal isolated JavaScript/TypeScript islands only where UX requires
> client-side state;

That is not what the application is built as, and no decision record says so.
On `integration/hardening-r1` (2026-10-18):

| File | Lines | Loaded | Listeners registered at start (document / body / window) |
|---|---|---|---|
| `static/js/theme.js` | 88 | every page, in `<head>`, synchronous | 1 / 0 / 2 |
| `static/js/app.js` | 5,695 | every page, `defer`, after htmx | 18 / 13 / 3 |
| `static/js/ux.js` | 1,813 | every page, `defer`, after app.js | 20 / 1 / 4 |

`static/vendor/htmx.min.js` loads before both. There is no build step, no
module system and no per-page script. The two large files are each one
immediately-invoked function; between them they read about 120 distinct
`data-*` attributes off the server's markup.

The audit found the consequence of that shape: in a single function, an
exception thrown while one feature starts stops every statement after it, so a
defect in one corner of one page disables unrelated behaviour on every page.
It named the 400/409 refusal display as a casualty. Measured with a failure
injected into the first feature that scans the page at load
(`e2e/test_startup_failure_isolation.py`), the picture is narrower and still
serious:

* **the refused answer itself still showed.** `htmx.config.responseHandling`,
  which tells htmx to swap 400, 409 and 422, is the first statement in
  app.js and ran before the failure;
* **everything app.js does with an answer did not.** The notice beside a form
  whose save failed with a 5xx, a dropped connection, an expired session or a
  stale CSRF token (ENG-012); the drafts carried across a column swap
  (docs/adr/0107); the empty-refusal guard; the «choose these files again»
  notice on a refusal (JUX-001); the autosave state of the private note; the
  open rows of `Teema käik`; and every binder (date pickers, organisation
  pickers, chips, live search). A failed save then looked exactly like nothing
  having happened;
* **in ux.js, one binder that threw after a swap skipped the refusal focus**
  that runs after the binders in the same listener (JUX-003), so a refused
  save no longer put the cursor on the refused field or announced it.

## Decision

### 1. Say what the JavaScript is

The browser layer is **one global, delegated, progressive-enhancement layer**,
not a set of islands. As built:

1. **One global layer.** `app.js` and `ux.js` load on every page and hold every
   behaviour. A feature finds its markup by `data-*` attribute or id, never by
   which page it is on, and does nothing on a page without that markup.
2. **Document-level listeners.** Most behaviour is a delegated `click`,
   `keydown`, `change` or `input` listener on `document`, registered once per
   page. It survives every swap because it is not on anything a swap replaces.
   Where an element needs its own listener or initial state, a `bind…(scope)`
   function finds it inside `scope` and marks it bound (`once()`, the
   `data-bound…` markers), so running it again is free. The binders run on
   `DOMContentLoaded` over the document and on `htmx:afterSwap` over what
   arrived.
3. **The htmx layer.** One configuration at the top of app.js (swap 400, 409
   and 422; no history snapshots; no `eval`, no swapped `<script>`, no injected
   styles — ENG-009, ENG-124). Then listeners on `document.body`:
   `beforeSwap` (focus placement for non-save swaps; the refusal/empty-refusal
   decision and the draft hand-over; which `Teema käik` rows and periods were
   open), `afterSwap` (drafts settled and files-again notice; `Teema käik`;
   re-binding; in ux.js re-binding and the refusal focus), `afterSettle` (focus
   after a save), `beforeRequest`/`afterRequest` (autosave bookkeeping, the
   files of a request in flight, stopping a save that would drop files),
   `responseError`/`sendError`/`timeout` (the failure notice). Per-request state
   is keyed by the request's `xhr`, so an answer that was never swapped cannot
   act later.
4. **Draft keeping** (docs/adr/0107). A place somebody types is a draft host
   (`data-draft-host` with an id). Before a swap replaces it, a host holding
   unsaved input that is not the request's own is carried across with htmx's
   `hx-preserve`; where the new page has no matching place, its text is shown
   read-only as `Salvestamata sisu`. Nothing goes to browser storage.
5. **Refusal display.** The server is the authority: a refused save answers
   400/409/422 with the surface re-rendered, the reason on it and the input
   kept, and htmx swaps it. The script adds three things and no reasons of its
   own: the cursor on the refused control and the message announced (ux.js,
   JUX-003); the files the browser could not keep (app.js, JUX-001); and, for a
   failure that is not a rendered refusal, one Estonian sentence beside the
   form (app.js, ENG-012).

**Proposed AGENTS.md wording** (for the owner; not changed by this record):

> - server-rendered HTML + HTMX by default, with one shared progressive-
>   enhancement layer (`static/js/app.js`, `static/js/ux.js`) loaded on every
>   page: optional behaviour bound to the server's markup through `data-*`
>   attributes, delegated from `document`, each feature started in isolation
>   (docs/adr/0171). A separate, self-contained script only where UX requires
>   client-side state the shared layer cannot reasonably hold;

The «islands» sentence describes a goal the code base never had: no feature is
a separately loaded unit, and turning them into ones would be a rewrite of
7,500 lines for no user-visible gain. What the islands wording was protecting —
small, optional behaviour, the page usable without script, no client-side
application — is true of the layer as built, and the proposed sentence keeps it
while naming the structure honestly.

### 2. A feature that fails to start stops only itself

Implemented in this change, behaviour-preserving:

* `safeInit(name, fn)` in each of app.js and ux.js: runs `fn`, and if it
  throws, writes `juristid: <name> failed` and the error to `console.error`
  and returns. Nothing is retried or hidden; the browser suite's console checks
  (`e2e/test_teema_workspace.py::test_the_workspace_writes_nothing_to_the_console`)
  still see the error.
* **Order in app.js:** the htmx configuration; the removal of htmx's old
  history cache; `safeInit`; then the whole request lifecycle — what a refused
  or failed request shows, draft keeping, files-again, autosave bookkeeping,
  the unload flush (`Workspace failures` block, moved up unchanged) — and only
  then the features. Nothing that can throw at start runs before that block.
* **Guarded:** every load-time scan of the page (`muu-reveal`,
  `uus-teema-files`, `sarnased-restore`), each binder in the `DOMContentLoaded`
  and `htmx:afterSwap` lists (one at a time, in their old order, named by the
  function), the `Teema käik` sync steps, and the two steps of the drafts
  `afterSwap` listener. In ux.js, each binder of `bindAll`, so the refusal
  focus that follows it still runs.
* **Not guarded:** a bare `addEventListener` on `document`, `document.body` or
  `window` — registering a listener on an element that always exists cannot
  throw, and its handler already runs in its own call per event.

Nothing else is restructured: the files are not split, the listeners are not
merged, and no behaviour moves between app.js and ux.js.

### 3. Rules for adding a feature (proposed)

1. Behaviour goes into `app.js` or `ux.js`. A new file is a decision for this
   record's amendment, not a convenience. No inline script, no `hx-on`, no
   `js:` values (ENG-124).
2. Prefer a delegated listener on `document` to binding at start; it needs no
   guard and survives swaps.
3. Anything that does work at start — queries the page, reads storage,
   measures, sets state — is either a `bind…(scope)` function made idempotent
   with `once()` and added to the `DOMContentLoaded` list (and the
   `afterSwap` list when swapped content can carry its markup), or wrapped in
   `safeInit("<feature>", …)`.
4. Nothing is added above the request-lifecycle block in app.js except
   configuration that cannot throw.
5. The page works without the feature (master specification 17.7). A refusal's
   reason is the server's to render; script adds focus and notices, never the
   reason.
6. An htmx listener never stops a 400/409/422 swap that carries a body, and
   keys any per-request state by `detail.xhr`.
7. A feature marks its markup with a `data-*` attribute named for what it does,
   and says in a comment which template carries it.

## Consequences

* A defect in one feature's start-up is a console error and that feature
  missing, not the whole layer missing. The failure notices, draft keeping and
  refusal focus are no longer at the mercy of unrelated code.
* The diff moves one block of app.js (about 800 lines) unchanged; history for
  those lines is best read with `git log --follow -M` / `git blame -C`.
* The «islands» wording stays in AGENTS.md until the owner approves §1.
* A JavaScript syntax and lint step in CI is the second half of JARCH-004
  (FIX-PLAN F8), in its own change.

## Verification

`e2e/test_startup_failure_isolation.py` injects a failure from the test side —
an init script makes `querySelectorAll` throw for one selector that one feature
asks for at start — so the shipped code carries no test hook. Each test first
checks the injected failure fired.

* app.js: with `muu-reveal` failing, a refused `Töövõit` save shows its reason,
  a save answered with a 500 shows «Salvestamine ebaõnnestus.» beside the form
  with the text kept, and the console names `muu-reveal`. On the base, the
  refusal showed and the failure notice never appeared.
* ux.js: with `bindPublicationPreview` failing, a refused `Muu` save puts the
  cursor on `occurred_on`. On the base, it did not.
