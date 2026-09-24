# 0107 — A column swap keeps what was typed, and a failed request is told

**Status:** accepted
**Date:** 2026-09-24

**Amends ADR 0075 §3's premise.** It does not change what 0075 §3 decided. It
changes one sentence of its reasoning. The Decision section of 0075 is left as
written; this record says which part of the reasoning no longer holds and what
now takes its place. It also records the failure policy for htmx requests that
the engineering audit found missing (ENG-012, ENG-034).

**No migrations. No search-index change.**

---

## 1. What 0075 §3 assumed, and why that stopped being true

0075 §3 re-renders the whole `#teema-vaade` column after `Salvesta tegevus`, and
it justifies that with one sentence:

> ADR 0052 §8 kept `✓ Tehtud` off that target so a half-typed composer
> underneath was not thrown away; with one control there is nothing underneath
> left to discard, so the problem is removed rather than worked around.

When that was written it was true. It is no longer true. The column now holds:
- `PRAEGUNE TEGEVUS` with its `Mida tegid?` box and its `Muuda`;
- the `LISA TEEMALE` panels;
- `Muuda` on every chronology row;
- the `Menetluse kulg` editor;
- the rail's `Saatja` editor;
- the private `Märkmed`, which autosaves 900 ms after the last keystroke.

Nineteen forms answer by replacing the column. Any one of them discarded
unsaved text in every other place, without warning. The engineering audit
reproduced this in a real browser for the composer, an open `+ Märge`, a row
`Muuda` and the note's last keystrokes (ENG-034).

**What stays:** the column is still replaced whole. The reason for that is ADR
0074 §10, and it still applies: a single render keeps the task, the process
strip and the chronology from disagreeing about the save that just happened.
Narrowing the swap would bring that disagreement back.

## 2. Decision — what a swap carries across

Each place a lawyer types into is marked as a **draft host**: `data-draft-host`
on an element that has an id. The hosts are:
- the composer;
- `Muuda` beside the task;
- each `LISA TEEMALE` sub-panel;
- each chronology row's `…-sisu`;
- each planned overview row;
- the `Menetluse kulg` editor;
- the rail's `Saatja` editor;
- the note.

Before a swap lands, the client looks at every host inside the target that holds
unsaved input, except the host of the form that sent the request, because the
answer to that form is the server's to give. It marks the matching element in
the response with htmx's own `hx-preserve`. htmx then moves the existing element
into the new column instead of the server's copy. The element moves, rather than
being copied, so the text, the cursor, any chosen files and the listeners all
come with it.

A `LISA TEEMALE` panel opens off radios that the server sets. If a panel was
fully open when the swap began, it is reopened afterwards. If the person had
already switched away from it, it stays closed.

A host is carried only into **its own place**. The new column must have an
element with the same id **and** the same `data-draft-state`. The state is used
where a host is about a particular record: for the composer and for `Muuda`, it
is the `NextAction` they were rendered against. If the task was finished or
replaced in another tab, carrying the composer across would post a description
against a task the page no longer shows. In that case the typed text is shown
instead as `Salvestamata sisu`. It appears read-only, above the task, in the
same form the server already uses for the same situation (QA-06).

Carrying an editor across never overwrites anything newer:
- **Row editors** keep the revision they were opened with. Saving one later
  over a newer version is refused as a conflict (ADR 0104).
- **The note** keeps its revision as well. If another tab has saved since, the
  next autosave gets the existing 409 and the newer text is shown beside the box
  (ADR 0077).

**Nothing is stored in the browser.** There is no `localStorage` or
`sessionStorage`, and there is no draft copy anywhere except the page it was
typed into. This is the constraint behind ENG-009. It is also why a reload still
loses unsaved text; the page tells the person to copy it first.

## 3. Decision — what a failed request says

htmx swaps only 2xx responses and the inline refusals (400, 409, 422 with a
body). Every other failure is now told **in the form that sent it**, in
Estonian, as one sentence. The notice uses `role="alert"`, carries a text lead
and not only a colour, never moves focus, and never covers the form. The
failures are:
- 5xx;
- network loss;
- a timeout;
- 403;
- 404, worded generically so it cannot be used to probe whether a record
  exists;
- an empty 4xx;
- an expired session;
- a stale CSRF token.

Nothing is swapped, so what was typed is still on the page. **Nothing is sent
again on the person's behalf.** A POST that may or may not have reached the
database is the person's to repeat.

The server changes only the **shape** of two refusals:

**Signing in.** For an htmx request, `HtmxSignInMiddleware` turns a redirect to
the gate or to `LOGIN_URL` into a `401` with `X-Juristid-Failure: sign-in` and
`X-Juristid-Sign-In: <address>`.
- The refusal itself was already made below it, and the aged-out session was
  already logged out.
- Before this change the browser followed the redirect, and htmx swapped the
  password page into the column.
- It is deliberately not `HX-Redirect`. Navigating away would throw away the
  text this record exists to keep.
- The address returns to the page (`HX-Current-URL`) rather than to the POST
  endpoint, but only when that address is on this site.

**CSRF.** `CSRF_FAILURE_VIEW` answers an htmx request with an empty 403 marked
`X-Juristid-Failure: csrf`, and a navigation with an Estonian page.
- The check is Django's, and it is unchanged.
- There is no automatic re-token and no retry. The usual cause is choosing a
  persona in another tab, which rotates the token, and a quiet retry would save
  this tab's text under somebody else's name.
- The notice offers `Laadi leht uuesti` and tells the person to copy the text
  first.

The note's `Salvestatud HH:mm` moves only on a 2xx for the value that was sent.
After a failure it reads `Salvestamata`. htmx fires `afterRequest` on errors
too, and moving the saved marker there had disarmed the unload flush for exactly
the text that failed. There is now one delegated `beforeunload` listener for the
page, where before each swap added another.

404, 500 and the CSRF page are Estonian templates that need no context. The 404
reads the same whatever caused it.

## 4. Consequences

- A lawyer can have text in several panels at once, and saving one of them does
  not cost the others.
- A failure is never silent, and never looks like a save.
- A save that lands while something else is half-typed can now leave the column
  showing **both** the fresh server state and the carried draft. That is the
  intent: the draft is what the person is still working on, and the rest of the
  column is what the server says now.
- A new place to type into has to be marked `data-draft-host` with an id. A
  region that is not marked gets the old behaviour. The browser tests in
  `e2e/test_workspace_failures_and_drafts.py` cover the marked hosts. They
  include a test that a clean column carries nothing, so a host that counts
  itself dirty without being typed into is caught.
