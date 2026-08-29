# ADR 0047 — Arvamused as a section of Teemad, with two searches that never meet

- Status: proposed
- Date: 2026-08-29
- Stage: pre-QA (shared-gate development phase)
- Related: ADR 0044 (one `Arvamused` destination, whose Saadetud/Arhiiv decision
  this keeps and moves one level in), ADR 0028 (the development archive
  workspace, whose access rules are unchanged here), ADR 0038 (child visibility
  in projections and the `INDEX_VERSION` gate this deliberately does not move),
  ADR 0035 (the bounded workspace), ADR 0041 (search index freshness)
- Number: 0047 rather than 0046. `ux/tahtajad-grouping` has 0046 in flight and
  the two branches are being developed in parallel.

## Context

The bar offered `Minu töö | Teemad | Ülevaade | … | Arvamused`, which says these
are two parallel places to be. They are not.

A Teema is the object the department works on. An arvamus is usually one of the
things that work *finishes with* — a letter sent about a file, filed against
that file, produced by the work the register describes. Presenting the two as
siblings had two concrete costs:

**It hid the relationship.** A reader who found a letter in Arvamused had no
route back to the file it came out of except a second search in another
destination. The relationship exists in the data — every canonical Submission
carries its Matter — and the navigation was the only thing not saying so.

**It made the department choose a destination before it had a question.**
"Arvamused" and "Teemad" are not alternatives; one is a subject and the other is
one of its outcomes. A bar that offers both makes somebody sort that out on
arrival, every time, with no information yet.

ADR 0044 already resolved the neighbouring version of this — the bar used to
offer both `Arvamused` and `Arvamuste arhiiv`, two answers to one question, and
the archive became a captioned tab inside the workspace. This is the same
correction one level up, and it keeps 0044's decision intact: `Saadetud` and
`Arhiiv` still belong together, in one Arvamused workspace, and that workspace is
what moves.

## Decision

**`Arvamused` leaves the top-level navigation entirely** — not into the "Veel"
disclosure, which would still be a first-level destination, but off the bar. The
Arvamused workspace becomes a full-width section of the Teemad page, under the
register, with its own heading, its own Saadetud/Arhiiv tab strip and its own
search box.

**The two searches stay two searches.** This is the load-bearing constraint, and
it is enforced by parameter name rather than by convention:

| | Teemad | Arvamused |
|---|---|---|
| box | «Otsi teemadest» | «Otsi arvamustest» |
| parameter | `?q=` | `?arvamus_q=` |
| source | Matter, through the search projection | Submission (`icontains`) or held archive letter (its own projection) |
| population | `matter_list_queryset` + register filters | `sent_queryset` / `search_archive` |
| live route | `/teemad/` (`_wants_fragment`) | `/arvamused/plokk/` |

Nothing reads the other's parameter. Both states live in one address and both
are answered in one render, so a pasted link carries what was on screen. Typing
in either box leaves the other list exactly as it was — asserted from both
directions in `tests/test_arvamused_under_teemad.py`, with titles chosen so that
neither search could pass by accident.

The two models are untouched. Nothing here turns a Submission into a Matter, and
the archive/canonical boundary the workspace was built to protect is carried
over unchanged: two tabs, two sources, no merged list.

**Nothing was deleted.** `/arvamused/` and `/arvamused/arhiiv/` still resolve,
still carry the full filter set — status, year, kind, recipient, owner — and
still page properly. Every bookmark, internal link and historical archive link
still works, and «Vaata kõiki arvamusi →» is how a reader gets there from the
section, carrying whatever they had typed.

**The section is bounded at twelve rows and says so.** It sits under a register
page that is already fifty rows long; a second fifty-row table would have
consolidated two usable surfaces into one unusable one. Twelve is enough to
recognise recent work in and to make a search feel like it answered. The real
total is printed beside them — `240 vastet · kuvatud 12` — because a page that
showed twelve rows over a bare "240" would be describing a list it is not
showing. There is no second pager: one page must not have two "next page"
controls meaning different things.

**A fragment route of its own, and no pushed URL.** `/arvamused/plokk/` exists
because the register already answers *any* HTMX request to `/teemad/` with its
own results (`matters.views._wants_fragment`) — an opinion search made against
that address would come back as a table of teemad. The fragment is never pushed
into the address bar: the URL a reader must keep is `/teemad/…`, and pushing the
fragment route would leave `/arvamused/plokk/?…` in the bar and in whatever they
paste to a colleague, which is the trap `_wants_fragment` documents for the
register. The plain GET path still produces a real, shareable address on the
register's own URL.

**Plain GET works with JavaScript off.** The form submits to
`/teemad/#arvamused` and the section is rendered server-side from the same
builder. Every register parameter travels as a hidden input, so an opinion
search does not silently widen the teemad list — the same failure the register's
own box carries hidden inputs to avoid. The tab strip is plain links carrying
the whole current address, so choosing a source keeps every register filter, the
register's page number and the opinion query.

**One JavaScript island, for a failure only a browser has.** The tab hrefs are
built from the address the page was *rendered* with. That is correct until the
register's own live search runs: it swaps `#teemad-tulemused` and pushes a new
address, and the tab strip is outside that region — so following a tab would
navigate to the old URL and silently undo the search the reader had just typed.
Twelve lines in `static/js/app.js` rebuild the href from the live address on
click. With JavaScript off there is no live search for it to go stale from, so
the server-rendered href is already right and the island never runs, which is
the rule that file opens with. Found by driving the page by hand rather than by
any server-side test, and now held by `e2e/test_opinion_archive.py`.

**Authorization did not move, and is asked before anything is counted.**
`may_read_archive` decides whether the Arhiiv tab is offered *and* whether the
archive is queried at all; Submissions come through `Submission.objects.visible_to`
exactly as before. A specialist who hand-edits `?arvamus_vaade=arhiiv` gets
Saadetud — resolved in Python before a query is built, so no archive row, no
archive count and no corpus date range reaches the page.

That request **resolves rather than raising**, which is the one place this
differs from the standalone workspace's 403, and it is deliberate: the section is
a passenger on the register, and a crafted opinion parameter must not take the
whole Teemad page away from somebody who was reading teemad. The standalone
route's 403 is unchanged.

**`nav_active` on the standalone workspace becomes `teemad`.** A page marking a
destination the bar no longer carries would leave the bar with nothing current on
it while the reader is plainly somewhere. Arvamused is part of the Teemad area
now, and `is-active` has always meant "the area you are in".

## Consequences

- One fewer thing to read on the bar, and one fewer decision on arrival.
- The relationship is visible in both directions: an opinion row names its Teema
  and links to it, and the register page shows the opinions its work produces.
- The Teemad page does more work per request. The section's counts and rows are
  built only in the full-page branch — the register's own live search returns
  before them — so a keystroke in `Otsi teemadest` pays for nothing here.
- The bounded section is not a replacement for the workspace, and the link out is
  load-bearing rather than decorative. Anybody needing status, kind, recipient or
  owner filters is one click from them.
- `INDEX_VERSION` is untouched at `AUTH003.1`. This composes existing surfaces
  and writes no projection, so no deployment needs a search rebuild because the
  section exists. No migration either: nothing about the schema changed.

## Alternatives rejected

**One search over both.** The brief refuses it and so does the product: "leia
teema, millega me tegeleme" and "leia arvamus, mille me saatsime" are different
questions with different answers, and a single box would have to guess which was
meant. A merged result list would put a Matter and a letter on the same row and
make a badge the only thing between a lawyer and a wrong count — the same failure
ADR 0028 kept the archive and Saadetud apart to avoid.

**Reusing `?q=` for both.** It would collide on the first keystroke and make the
register's own live search unshareable.

**Branching `matter_list` on which fragment was wanted.** Cheaper by one route,
and it would put opinion routing inside the register's view and give a
single-purpose helper a second purpose. Two fragments on one address is the
thing that needs two addresses.

**A redirect from `/arvamused/` to `/teemad/#arvamused`.** Rejected for this
phase. The standalone workspace is still the full surface, the section is a
bounded view of it, and redirecting the complete thing at the partial one would
take filters away from everybody holding a bookmark.
