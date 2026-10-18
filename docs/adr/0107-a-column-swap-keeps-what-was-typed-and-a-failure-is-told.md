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

## Amendment, 2026-10-17 — the note is judged against the answer, not the last save (JQA-003)

§2 counted the note as holding unsaved input when its box differed from the
last save **this page had heard back about**. That is the wrong comparison when
two answers cross. A panel's answer can be rendered on the server before the
note's autosave commits and still reach the page after that autosave's 2xx. The
box then counted as saved and was not carried, and the old render replaced it:
the earlier text (often empty) and the earlier revision. The next keystroke
autosaved against that revision and got a 409 «Märget on muudetud teises
aknas…» although there was no other window, and a reload brought back the
earlier text and lost the new.

The note is now judged against **the render that is about to replace it**
(`static/js/app.js` `autosaveOutdatedBy`). It is carried across the swap when:
- its text differs from the text in the render; or
- the render's revision is older than the one the box holds. A stale render
  never moves the token back.

A render with the same text and a newer revision replaces it as before. A
render with **different** text is never adopted over the box, even when the box
holds nothing unsaved. If another tab saved that text, the next autosave gets
the existing 409, with the newer text beside the box (ADR 0077), as §2 already
says. Where the new column has no note at all, a note with unsaved text is
still shown as `Salvestamata sisu`.

The revision is the row's `updated_at` in UTC ISO form, so string order is time
order. This holds even when Python leaves out a zero microsecond, because `+`
sorts before `.`. Nothing changed on the server: `save_note` already moves the
token on with every 2xx, and `rail.html` already renders it inside the form.

The race is pinned in `e2e/test_workspace_failures_and_drafts.py`
`test_a_panel_answer_rendered_before_the_note_saved_does_not_set_it_back`. The
test holds both requests in the browser and lets them go in that order, so it
no longer depends on the timing that made its sibling flaky.

## Amendment, 2026-10-18 — a refused save says which files to choose again (JUX-001)

§2 carries a host across a swap by moving the element, so chosen files survive
when *another* form saves. The form that sent the request is excluded on
purpose: its answer is the server's to give. When that answer is a refusal, the
server re-renders the form with the typed text and the errors, but it cannot
send the files back, and no browser lets a page put a file into a file input.
The drop box used to reset with no notice. The corrected save then stored the
record with **no files**, while the person believed they were attached.

This affected every form that takes files and answers a refusal with a
re-render: every `LISA TEEMALE` door (`Muu`, `Kohtumine / arutelu`, `Lisa
tagasiside`, `Alusta kaasamist`, `Oluline tähtaeg`, `Jõustumine`, `Töövõit`,
`Koja arvamus`), `✓ Tehtud`, the follow-up check, and the rows' `+ Lisa fail`.

The client now does three things (`static/js/app.js`, «Files a refusal could
not keep»):
- **As the request leaves**, it takes the names of the chosen files from the
  form, with where to find the form again: the panel or draft host around it,
  by id, and the form's own address inside it.
- **When the answer is a refusal** (400, 409 or 422), the re-rendered form gets
  a notice right after its drop box: «Fail tuleb uuesti valida: <names>»
  («Failid …» for several). It is a `role="status"` live region, in words and
  not only in colour, and it does not take focus. The refusal's own focus rule
  (`focusFirstRefusal`) is unchanged.
- **A save pressed while that notice stands and nothing has been chosen** is
  cancelled in the page, before htmx marks a request in flight. Nothing is
  posted, so the save-once token (docs/adr/0167) stays unspent. The notice
  takes focus and offers `Vali fail uuesti` and `Salvesta ilma failita`.
  Choosing files again, by picking or dropping, removes the notice.
  `Salvesta ilma failita` removes it and submits the form.

Only the names are kept, and only in the page. Nothing is stored in the browser
(§2). This is option (a) of the remediation plan. Staging uploads on selection,
as `Uus teema` does (`#hoitud-failid`), would keep the files themselves and
remains the larger follow-up.

Nothing changed on the server. Files stay optional, and a page with no script
has no notice and no guard. A save without files is accepted as before.

Pinned in `e2e/test_files_after_refusal.py`: `Muu`, and the same sequence over
`Kohtumine` and `Lisa tagasiside`. Each test refuses a save with a PDF chosen,
reads the notice naming it, has the next save stopped with nothing posted, then
chooses the file again and finds the stored row with that one file.
`Salvesta ilma failita` stores the row with none.
