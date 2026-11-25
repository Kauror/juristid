# 0222 — A person keeps named Teemad views, and the address is still the view

Date: 2026-11-25 · Status: Proposed — overnight development 25.11.2026, subject to owner review. Owner decision BLOCK-1 (24.11.2026): Juristid may store personal named views on a development branch, personal only, at most 20 per persona. **Supersedes ADR 0043 §2** («A saved view is a named URL, and nothing is stored») where it says nothing is stored; everything else in 0043 stands. One additive migration, `matters/0052`.

## Context

ADR 0043 §2 gave Teemad four named views — *Minu avatud*, *Tähtaeg sel kuul*,
*Vastutajata*, *Kogu osakond* — as links whose query string is their definition,
and turned «+ Salvesta praegune filter vaatena» into a copy-the-link popover. It
rejected a stored `SavedView` because it «would have to answer sharing,
staleness, ownership, deletion and migration», and because a bookmark already
covers the need.

Lawyers asked for their own combinations beside the four, by name, on every
device they sign in from — which a bookmark does not give a shared-gate
workstation where several personas use one browser (UX programme, UX-007). The
owner approved storing them (BLOCK-1). This record answers 0043's five questions.

## Decision

1. **What is stored is an address, never a result.** `matters.SavedRegisterView`
   holds `user`, `name`, `query` and `position` (display order). `query` is the
   canonical query string of the register's own parameters
   (`app.matters.saved_views.canonical_query`): only names the register reads —
   `REGISTER_PARAMS` (every filter, `olek`, `jarjestus`, `q`, the commencement
   presentation), `veerud` and `kaupa` — sorted, values de-duplicated, blanks
   dropped (a blank `olek` is kept: it means «every state»), and `leht` never.
   No Matter id, no row, no count and nothing authorization decided is stored.
2. **Opening a view is the ordinary register request.** The chip is a link to
   `/teemad/?<query>`; the register reads it for whoever opens it, through
   `visible_to` and today's data, exactly as any address. A view never widens
   access, and a view saved before a Teema became visible or invisible to its
   owner simply lists what they may see now. Columns and sort travel with the
   address because they are part of the register's URL contract (0071, «Vali
   veerud»).
3. **Ownership.** A view belongs to the person the request is signed in as —
   the selected persona under the shared gate, the account under personal
   sign-in — like a remembered block (0194 §3). The endpoints take no person
   parameter; every read and write is keyed on `request.user`, and a view id
   that is not the sender's own is a **404**. Presentation state: not read by
   authorization, not identity proof.
4. **Sharing.** A view is never shared. Its **address** is, as before:
   «Kopeeri» in the same popover hands over the link, and a colleague who opens
   it sees it through their own access. There is no «share with» and no
   department-wide view; the four predefined chips remain the shared ones.
5. **Naming.** 1–60 characters after collapsing whitespace, no control
   characters, unique per person regardless of case (a functional unique
   constraint on `(user, lower(name))`). Refusals are said in words.
6. **Limit.** At most **20** per person. The save locks the person's row for the
   count, so two tabs cannot pass the cap together. At the cap the save form is
   replaced by a sentence saying to remove one first.
7. **Staleness.** On every read the stored query is filtered through the same
   list of parameters again (`saved_views.stored_query`). A parameter a later
   release retires is dropped when the view is opened rather than emptying or
   breaking it, and the view says so («Mõni filter ei kehti enam ja jäeti
   välja»). A value the register no longer understands is the register's own
   business, exactly as in a bookmark: an unreadable filter empties the list and
   its chip says why (0043 §3, register_filters). Nothing is rewritten in place.
8. **Deletion.** «Eemalda» deletes the person's row and nothing else, after a
   confirmation inside the list. Deleting a person deletes their views
   (`CASCADE`). Writes to one person's views queue on that person's own row, so
   two tabs saving or renaming to one name at once end with one write and one
   refusal in words; the database's one-name constraint, if ever reached past
   that, is answered the same way inside a savepoint, never as a server error.
   There is no undo and no audit row: this is presentation state
   (class C in `tests/test_business_write_boundary.py`), like a remembered block.
9. **One filtering engine, bounded counts.** Each chip shows a count. Counts go
   through `register_selection.selection_from` → `searched` → `filtered`
   (`apply_register_filters`) — the path the register's table and its Excel
   export read — so a chip reading 12 opens twelve rows. All of a person's views
   are counted in **one aggregate statement** (`COUNT(*) FILTER (WHERE id IN
   (<view>))` per view over the reader's visible Matters), plus the search's own
   two reads (word normalisation, search index) for each view carrying free text and at most one shared read of the work
   model for every `?too=` view. With the cap of 20 the cost is bounded and does
   not grow with the register. Counted on the full page only, never on a
   keystroke of the live search (as the four chips).
10. **A compact strip.** The four predefined chips first, then the person's first
    six as chips — three on a screen 720 px wide or narrower, so twenty views
    never push the list below a phone's first screen (CSS only, `NARROW_CHIPS`)
    — then «Minu vaated ▾» (with «+N» counting the chips that width does not
    draw), whose list holds every view with its count, «Nimeta ümber» and
    «Eemalda».
    «+ Salvesta praegune filter vaatena» gains a name field and keeps the copy
    link. Plain forms and redirects: everything works without script.
11. **Migration.** `matters/0052_saved_register_view` creates the table; it is
    additive, nothing existing reads it, no backfill. Reversing it drops only
    names and query strings. The release fingerprint helper picks it up like any
    migration.

## Routes

`POST /teemad/vaated/` (`matters:save_view`), `POST /teemad/vaated/<id>/nimi/`
(`matters:rename_view`), `POST /teemad/vaated/<id>/eemalda/`
(`matters:remove_view`): `login_required`, POST only, CSRF, redirect back into
Teemad (`next` accepted only when it is a Teemad address on this site). Listed
in `CLASSIFIED_ELSEWHERE` as class C — the sender's own rows, no business
service, no `ChangeEvent`; a READER may keep views as they keep a scratchpad.

## Consequences

- ADR 0043 §2's «no model, no user-preferences table and no session state» no
  longer holds for named personal views; its «the address is the view» does,
  and is now the rule that keeps the stored row harmless.
- `tests/test_ux_pass.py`'s «nothing is stored» test is rewritten to this record:
  the chip is still a shareable link and «Kopeeri» still hands it over, and a
  stored view is a canonical query that opens the same rows its chip counts.
- Not decided here: reordering views by hand, sharing a view with a colleague,
  and views on other lists (Minu asjad, Osakond). Each would need its own
  decision.
