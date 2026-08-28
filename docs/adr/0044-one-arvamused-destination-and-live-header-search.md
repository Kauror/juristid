# ADR 0044 — One `Arvamused` destination, and live suggestions under the header search

- Status: accepted
- Date: 2026-08-28
- Stage: pre-QA (shared-gate development phase)
- Related: ADR 0028 (the development archive workspace, whose access rules are
  unchanged here), ADR 0038 (child visibility in projections and the
  `INDEX_VERSION` gate this deliberately does not move), ADR 0041 (search index
  freshness), ADR 0042 (department-wide lawyer access, which the suggestion
  endpoint inherits rather than restates); and the identifier-free rule from the
  review of PR #72, which decides what a suggestion row may print

## Context

Two small things on the same 48px bar.

**The bar answered one question twice.** `Arvamused` opened the workspace;
`Arvamuste arhiiv` opened the administrative browse over the 767 held letters.
A reader had to know the difference between a canonical Submission and a held
historical letter *before* they could choose a destination — and the difference,
though real and load-bearing, is not a navigation decision. The Arvamused
workspace has asked it since ADR 0028 landed, in a captioned tab strip
(`Saadetud` / `Arhiiv`) built precisely so that the two sources are never
mistaken for one another. The second bar item was the same question asked a
second time, in a place with less room to answer it.

**The search field could only ever take somebody to a page.** The compact field
in the header is used overwhelmingly for one thing: opening a file somebody
already has in mind. That cost a submit, a full page render and a scan of a
results list ranked across matters, entries, sent opinions and pages of annexes
— to reach a Matter whose title the reader could already have recognised.

## Decision

**`Arvamuste arhiiv` leaves the top-level navigation.** `Arvamused` is the one
destination; the archive is its `Arhiiv` tab. Nothing else moved: the
administrative browse keeps its route, its own workspace and its 403, every
existing archive URL still resolves, and `may_read_archive` still decides who
may open either surface. This is presentation only, which is exactly what ADR
0028 said the link was.

**The header search grows live suggestions**, served by one new read-only route,
`search:suggestions`, and bound onto the existing form by `static/js/app.js`.

The endpoint holds no search of its own. It calls `search_matters` — the
picker-shaped entry point `app/search/services.py` has offered since Stage 2A —
so the rows it returns went through `visible_documents`, the same authorization
predicate, the same deterministic tiers and the same ordering the results page
uses. There is no second ranking, no second visibility rule, and nothing in the
browser filters anything. `INDEX_VERSION` is untouched at `AUTH003.1`: this
reads the projection and does not change what is written into it, so no
deployment needs a rebuild because the dropdown exists.

Five bounds, each chosen against a failure this control has:

- **Matter rows only.** The full page answers with five source kinds; a dropdown
  under a 320px field is a way to *open a file*, and mixing kinds of target into
  it makes the one thing it is for harder. What lies beyond is one row away
  rather than absent.
- **Five results, enforced in SQL.** `LIMIT 6` and render five: the sixth row is
  how "there is more behind this" is answered without a second `COUNT` over the
  whole match set on every keystroke.
- **Two non-whitespace characters minimum.** One character matches most of the
  corpus and ranks it by nothing anybody typed.
- **A 200 ms debounce**, so a word is one request rather than eleven.
- **A monotonically increasing request token**, checked before any response is
  allowed to paint. The superseded request is also aborted, but the abort is not
  the guarantee: a response already parsed when the abort lands would otherwise
  arrive after a newer one and replace it — the dropdown showing results for a
  word that is no longer in the box.

**«Vaata kõiki tulemusi» is offered when the corpus holds something the five
rows do not** — a sixth matter, or a matching entry, opinion or annex page that
this list deliberately does not carry. That second case is a bounded `EXISTS`,
not a count, and it only runs when the dropdown was not filled.

**The form stays a plain GET form.** Everything above is bound onto markup that
already worked. With no JavaScript, before the script runs, or after the
endpoint fails, typing and pressing Enter reaches the full results page exactly
as before — and `Enter` with nothing highlighted is that same submit. The
combobox roles are set by the script rather than written into the template, so a
browser that will never run it is not told about a listbox that cannot open.

## Consequences

- One `Arvamused` on the bar, and one place the archive is reached from by
  somebody who has not memorised a URL. `nav_active == 'haldus'` no longer marks
  the "Veel" disclosure, because no link in it is that page any more.
- A second HTTP door onto search now exists, which is where an authorization
  rule gets quietly re-implemented. `tests/test_header_search_suggestions.py`
  makes every case `tests/test_search_authorization.py` makes about the results
  page again against the endpoint — owner, department lawyer, head, READER,
  ADMINISTRATOR, superuser, anonymous — so the two cannot drift apart in
  silence.
- The endpoint costs two statements against the search index at most, and the
  same number for eight matching matters as for twenty-eight. Asserted, because
  a per-keystroke N+1 is a department typing into a field and a database doing
  a hundred round trips a second.
- No migration, no schema change, no `authorization.py` change and no
  `INDEX_VERSION` change.
- `SUGGESTION_LIMIT`, `MIN_SUGGESTION_CHARACTERS` and the debounce are the three
  numbers to revisit if the dropdown ever feels wrong; they are named constants
  in `app/search/views.py` and `static/js/app.js` rather than literals spread
  through either.
