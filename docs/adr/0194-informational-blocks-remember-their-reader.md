# 0194 — Informational blocks remember their reader

- Status: Proposed (owner's coordinated UX & workflow brief of 2026-10-28,
  requirement 9 and owner decisions 2, 5 and 6; merged 2026-10-28 in #563)
- Adds: `accounts.DisclosurePreference` (migration `accounts/0006`, additive),
  `app/accounts/disclosures.py`, `{% disclosure %}`, `static/js/disclosures.js`,
  `POST /konto/plokid/` and `POST /konto/plokid/taasta/`
- Relates to: 0049 (Osakond), 0147 (the theme preference, which stays in the
  browser), 0186 (persona is not authentication)

## Context

Stable informational blocks — *Minu statistika*, the date windows of Osakond's
*Eesolev*, the definitions under Statistika charts — opened the same way for
everybody on every visit. A reader who always closes one, or always opens one,
paid that click every time. The owner decided (2026-10-28) that such blocks
remember the reader's choice, **server-side, keyed to the selected worker**, so
the choice follows the worker across devices; that the system default applies
until the worker explicitly changes it; and that temporary action forms, menus
and error disclosures do not behave like preferences.

## Decision

1. **One registry, semantic keys.** `app/accounts/disclosures.py` lists every
   remembered block with its default: `department.upcoming.today`,
   `my_work.statistics`, `reporting.definition`… Never a position and never a
   value that grows with the data (a Matter id, a date). The save endpoint
   refuses any other key, so a person holds at most one row per registry entry.
2. **Only an explicit choice is stored.** No row means the registered default,
   so a default changed later reaches everybody who never chose otherwise.
3. **The person is the request's signed-in principal.** `request.user` behind
   `login_required`: the selected persona under the shared gate, the account
   under personal sign-in. The endpoint takes no person parameter, so a crafted
   request can only change its own sender's state. Choosing Marko at the
   shared gate and folding a block changes how Marko's desk folds — the
   approved shared-workstation behaviour. A session without a persona has no
   preferences: defaults, and the endpoint answers `login_required`.
4. **Presentation state only.** Never read by authorization, never identity
   proof, and not `User.capability_overrides`, which is authorization
   configuration. The rows hold a key and a boolean, nothing about business
   content.
5. **Drawn on the server.** `{% disclosure "key" %}` renders the `<details>`
   open or closed before any script runs; one query per request reads the
   person's rows (cached on the request). With the script off or the save
   failing, every block is a native `<details>` that still opens and closes.
6. **The script reports changes only.** A `toggle` that agrees with the stored
   state (the browser fires one for a block parsed `open`) sends nothing; one
   request per key is in flight, later toggles collapse into one follow-up
   carrying the final state, so a slow response cannot overwrite a later
   choice; only a `204` counts as saved.
7. **Reset.** Haldus → Rakendus offers «Taasta plokkide vaikeseaded», which
   deletes the person's rows and nothing else.

## Inventory (2026-10-28)

Remembered (registered):

| Key | Default | Where |
| --- | --- | --- |
| `department.upcoming.today` / `tomorrow` / `remaining_week` / `next_week` | open | Osakond → Eesolev |
| `department.upcoming.rest_month` / `far` | closed | Osakond → Eesolev |
| `my_work.statistics` | open | Minu asjad → Minu statistika |
| `my_work.recent_activity` | open | Minu asjad → Viimased tegevused (when drawn as a foldout) |
| `matter_documents.working` | closed | Teema → Dokumendid → SharePointi viited |
| `matter_documents.archive` | closed | Teema → Dokumendid → Seotud arhiivikirjad |
| `document.data` | closed | Dokument → Dokumendi andmed |
| `reporting.overview.definitions` | closed | Statistika → «Mida need arvud tähendavad» |
| `reporting.definition` | closed | Statistika → every «Definitsioon» under a figure (one choice for all) |
| `reporting.chart_table` | closed | Statistika → every chart's data table (one choice for all) |

Deliberately **not** remembered:

- *Temporary forms and editors* — `Lisa tegevus`, `Lisa planeeritud`, `Muuda`,
  `Nihuta kuupäeva`, the Teema header's inline editors, Kulg's step editors,
  `Võta tagasi`, `Kustuta`/`Eemalda` confirmations, `Muuda dokumenti`. Their
  state belongs to one operation, and several open themselves on a validation
  error.
- *Menus and popovers* — the top bar's «Veel», the persona switcher, `⋯` row
  menus, column filters, calendar day/week popovers, KPI ⓘ tooltips, the
  period pickers, the assignment menu.
- *List expansions* — «Näita veel N», «Näita kõiki N sissekannet»: they show
  the rest of a list that was counted, and opening one is not a preference.
- *Search and filter panels* — «Täpsem otsing» and Statistika's filter panel
  open from the active filters.
- *State-driven groupings* — Teema → Käik's stage accordions (the current stage
  opens), Uuendused' nights (the newest opens), Valdkonniti's area rows (the
  first opens). Their keys would be per Matter, per day or per area — unbounded
  or positional — and the rule that opens them is the information.

## Consequences

- One additive table; reversing the migration loses only presentation choices.
- A new informational block becomes remembered by one registry line and one
  attribute. A test refuses an unregistered key in a template.
- Two people sharing one persona at the shared gate share its folding — the
  approved behaviour, and harmless because nothing but presentation is stored.

## Status update 2026-10-29 — merged and deployed

This section records what happened after the decision; it does not amend it, and
the status is unchanged (no formal acceptance was recorded).

- **Merged** 2026-10-28 in #563 (merge `fb37a5ea`, workstream C merge `0e661f37`).
- **Deployed** as production release r36, `fb37a5ea`, 2026-10-28 17:00 UTC, with
  its one additive migration `accounts/0006_disclosure_preferences` applied
  (backup set `20261028T165612Z` taken before it). A code-only rollback leaves
  `0006` in place; the table is additive and unread by authorization.
- **Testing consequence found after the merge** (audit 2026-10-28, E-03): the
  preference is per persona and the browser suite's personas are shared by every
  test in a shard, so a test that toggled a remembered block changed what the
  next test saw. The behaviour above is unchanged; the browser suite now starts
  each signed-in test from the registered defaults (`e2e/conftest.py`).

## Amendment 2026-11-25 — «Varasemad», and a state shown for one view (A1, UX-004)

This amends the decision above for presentation only. Nothing new is stored:
the table, the save and reset endpoints and the registry rule are unchanged.

1. **«Varasemad» on `/teavitused/` is a remembered block** (`notifications.history`,
   default **closed**). Its heading carries the number of history items on all
   pages, `feed.history_page(with_total=True)` — one count per source over the
   same querysets the pages read, so it is exact. Opening or closing it is
   presentation; it reads and marks no notification.

2. **A state shown for one view is not a choice.** Three situations show a
   remembered block in a state the reader did not choose, and none of them is
   saved:
   - **`force_open`** — `{% disclosure "key" force_open=… %}` draws the block
     open for a view the reader explicitly asked for (the next page of the
     history, `?varasemad=`). When the stored state is closed, the tag adds
     `data-disclosure-shown="1"`.
   - **A link to the block** — a URL fragment naming an element that is, or
     directly holds, a closed remembered block (`#varasemad`) opens it, marked
     the same way.
   - **A redraw** — see 3.

   `static/js/disclosures.js` ignores a `toggle` whose result equals
   `data-disclosure-shown`: it is the page's doing, including the browser's
   own parse-time toggle of a block drawn `open`. The reader's first toggle away
   from it removes the mark and is saved as any other choice. The stored state
   is therefore only ever what the reader last chose.

3. **A redraw keeps what the reader sees** (opt-in `data-disclosure-keep`).
   When an htmx swap *replaces* such a block — `/teavitused/` redraws itself on
   `notificationsPageStale` — the new block is shown as the old one was on
   screen, not as the server drew it, and marked as in 2. The server's markup
   may predate a save still in flight; the reader's latest choice wins on
   screen, and the in-flight save (or the follow-up the script queues) is what
   is stored. The state is taken in `htmx:beforeSwap` only from blocks inside
   that swap's target and given back in `htmx:afterSwap` of the same request,
   before the browser dispatches the new block's own toggle, and only to a node
   the swap actually replaced. A swap elsewhere on the page records and changes
   nothing.

Blocks without `force_open`, a fragment link or `data-disclosure-keep` behave
exactly as before. Tests: `tests/test_a1_compact_activity_and_history.py`,
`e2e/test_a1_activity_and_history.py` (redraw during a held save, opening and
closing; an unrelated swap settling while the reader toggles).
