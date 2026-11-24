# 0221 — Haldus is seven groups in a sidebar, and personal preferences live under Isiklikud seaded

Date: 2026-11-25 · Status: Proposed — overnight development 25.11.2026, subject to owner review.
Brief: UX-002 (owner, 24.11.2026), preparation report §3 Package B, overnight brief Workstream B.
Amends the shell part of docs/adr/0186 (the flat strip); keeps every rule of 0186, 0187, 0145 and 0194.

## Context

Haldus grew one tab at a time: nine sections in a flat strip (Ülevaade, Kasutajad ja õigused,
Klassifikaatorid, Osakonna töökorraldus, Ajaloolised kirjad, Registrite jälgimine, Uuendused,
Süsteemi seis, Andmete sünkroonimine), and as many administrative pages outside it again —
notification settings, the opinion and history review queues, the opinion archive, the reporting
administration pages (Andmekvaliteet, Ajaloolised materjalid, Statistika definitsioonid),
Arendusseis, Kiirklahvid. Lovable and OneNote shared one page. Ülevaade mixed personal settings,
the administrator session and a list of the same sections the strip already offered.

## Decision

### 1. A two-level registry, the same predicates

`app/haldus/sections.py` holds `GROUPS`: seven groups, in this order — **Ülevaade**, **Isiklikud
seaded**, **Töökorraldus**, **Andmed**, **Integratsioonid**, **Süsteem ja õigused**, **Rakendus** —
each a tuple of `Item`s. An item is one destination with one `visible(request)` predicate and the
list of every URL name it is the *sidebar home* of (`routes`: the page, its sub-pages and its POST
endpoints). A group is shown only when at least one of its items is.

* `SECTIONS`, `visible_sections()` and `section()` stay as the flattened list, so the account
  menu's Haldus entry (`haldus_link`), the search destinations and older callers read the same
  registry as before.
* The current item is found from `request.resolver_match.view_name` through `routes`
  (`sections.active_key`); a page outside the shell may name one with `haldus_active`.
* **The menu equals the guard.** Each predicate answers exactly what its route answers a GET.
  Where the old menu was narrower than its route, the item now follows the route:
  «Uuendused» and «Kiirklahvid» (behind the shared gate alone, docs/adr/0034) for everybody who
  reaches the shell. One deliberate exception stays narrower: «Teavitused»
  (`notifications:settings` is `login_required`) is offered only to the readers the bell is drawn
  for (UI-03) — a technical administrator without a business role has nothing to set. Narrower
  never offers a page that refuses; the inventory test names the exception. Showing is still not authorizing: no route, guard, step-up table,
  write-boundary registry or fresh-proof rule changed.
* A shared-gate session with no persona reaches the shell only through a verified administrator
  session, as before: `visible_sections()` is empty for a reader who cannot open Ülevaade, and the
  pages open to everybody behind the gate do not count towards it.

### 2. The mapping (route → sidebar home)

| Group | Item | Primary route (unchanged unless marked new) | Other routes it is home of | Visible to (= route guard) |
|---|---|---|---|---|
| Ülevaade | Ülevaade | `haldus:index` | — | a signed-in persona/account, or a verified administrator session |
| Isiklikud seaded | Kuvamine | `haldus:settings` **new** `/haldus/seaded/` | — | signed in |
| | Teavitused | `notifications:settings` | `notifications:reset_settings` | business reader (the bell's readers; the route itself: signed in) |
| Töökorraldus | Osakonna töökorraldus | `haldus:osakond` | — | `may_view_department_management` (persona) |
| | Töö ilma vastutajata | `account_admin:without_owner` | — | `may_administer_accounts` (verified session) |
| Andmed | Klassifikaatorid | `reference_data:index` | every `reference_data:*` | `reference.manage` (persona or verified principal) |
| | Ajaloolised kirjad | `haldus:letters` | `haldus:letters_decide` | business reader |
| | Arvamuste arhiiv | `legacy_import:opinion_archive_browse` | `…_detail`, `…_link`, `…_file` | `may_read_archive` |
| | Arvamuste ülevaatus | `legacy_import:opinion_queue` | `legacy_import:opinion_decide` | administrator persona that reads business content |
| | Ajaloo ülevaatus | `legacy_import:review_queue` | `legacy_import:review_decide` | `may_review_historical_matches` + business reader |
| | Andmekvaliteet | `reporting:quality` | — | signed in |
| | Ajaloolised materjalid | `reporting:historical` | — | signed in |
| Integratsioonid | Lovable | `haldus:data_sync` | `data_sync_check`, `_now`, `_differences`, `_resolve`, `_release_hold`, `_history`, `_names`, `haldus:lovable`, `haldus:lovable_check` | verified technical administrator |
| | OneNote | `haldus:onenote` **new** `/haldus/andmete-sunkroonimine/onenote/` | `data_sync_onenote_verify`, `data_sync_onenote_import`, `onenote_holds` | verified technical administrator |
| | Registrite jälgimine | `haldus:register_watch` | `register_watch_sources`, `register_watch_run` | business reader |
| Süsteem ja õigused | Kasutajad ja õigused | `account_admin:list` | every other `account_admin:*` | `may_administer_accounts` |
| | Süsteemi seis | `haldus:susteem` | — | verified technical administrator |
| | Arendusseis | `core:development_status` | — | department head or the queue's administrator persona |
| | Halduri sisselogimine | `haldus:sign_in` | `sign_in_code`, `enrol`, `reauthenticate`, `sign_out` | shared gate, no verified session yet (the same for everybody, security review P3b) |
| Rakendus | Uuendused | `core:release_notes` | `core:release_notes_lovable`, `…_export` | everybody behind the gate |
| | Kiirklahvid | `core:keyboard_shortcuts` | — | everybody behind the gate |
| | Statistika definitsioonid | `reporting:definitions` | — | signed in |

`tests/test_haldus_route_inventory.py` walks the running URL configuration and proves that every
URL name under `/haldus/`, and each page outside it listed above, has exactly one home; that no
address moved; and, for eight shared-gate personas and roles (lawyer, department head,
reference manager, head holding administration, verified technical administrator with and
without a lawyer persona, verified account administrator, shared gate only) plus two accounts
that are no persona behind the gate (the administrator persona and a reader, signed in), that
every item is shown exactly when its route answers 200.

### 3. The sidebar

`templates/haldus/base.html` draws the sidebar (`{% haldus_sidebar %}`, `_sidebar.html`) beside
the page: a 13.5 rem column from 900 px up, and below that a native `<details>` «Haldus» menu above
the page, closed, naming the current item. One list rendered twice; only one is displayed at a
time, so a screen reader meets one navigation. Plain links, `aria-current="page"` on the current
item, group headings that label their lists. No script. The page heading defaults to the current
item's label («Haldus» on Ülevaade).

Pages that keep their own layout (the queues, the archive, the reporting administration tabs,
Arendusseis, Kiirklahvid, the notification settings) are reached from the sidebar; they do not
draw it. Moving them into the shell is a later, page-by-page choice.

### 4. Integratsioonid: Lovable, OneNote, Registrite jälgimine

The combined «Andmete sünkroonimine» page became two destinations over the same services
(`sync_admin.lovable_panel`, `sync_admin.onenote_panel`): **Lovable** keeps the address
`haldus:data_sync` with its panel, requests and actions, and its sub-strip (Ülevaade, Erinevused,
Ajalugu, Lovable'i nimed, Lovable'i üksikasjad); **OneNote** gets `haldus:onenote` with its panel,
the capture check, the import and its sub-strip (Ülevaade, Ootel kirjed). A recorded OneNote
check or import now returns to the OneNote page instead of `data_sync#onenote`. The request
history stays one page (it lists both lanes) under Lovable, linked from OneNote. No new
synchronisation function; every fresh-proof, audit and runner rule is the same code.

### 5. Ülevaade is an attention page

`app/haldus/overview.py` asks, only for the items the reader may open, the service that item's
own page reads: Lovable (`lovable_panel`: verdict and pending differences), OneNote (held readings
waiting), Registrite jälgimine (`watch_health`), Töövõidud waiting for the head, work without an
active owner, Süsteemi seis (readiness, search freshness, processing errors), and — for a verified
administrator only — the two-administrator rule when it does not hold. A line that needs attention
gets a chip, one sentence and «Ava»; what is fine shares one quiet «Korras» line; a read that
fails is «unknown», never healthy. Counts and verdicts only — never a Teema, a person's work or a
secret. Below: the administrator session panel and the continuity panel (verified session only,
unchanged), then the running build in one line.

### 6. HR-10 PR 3 — the future integration point

HR-10 PR 3 (Lovable comparison texts: dates as `05.01.2026`, the missing-owner display) is not
implemented and this change does not implement it. The comparison presentation was not touched:
it stays in `app/legacy_import/sync_admin.py` (`value_text`, `juristid_value_text`, the
`DifferenceRow` builders used by `haldus:data_sync_differences` and `haldus:data_sync_resolve`),
`app/haldus/views.py` (`_shown`, `_juristid_shown` for `haldus:lovable`) and
`app/legacy_import/lovable_display.py` (`juristid_text`). Those three are where HR-10 PR 3 changes
the texts; the templates `data_sync_differences.html`, `data_sync_resolve.html` and `lovable.html`
only print the strings, and the split above moved none of them.

## Personal preferences

Isiklikud seaded holds two items: **Kuvamine** (`haldus:settings`) and **Teavitused** (the
existing `notifications:settings`). Kuvamine gathers the person's own display choices; each stays
where its data already lives — no second store:

| Choice | Where it lives | Applied |
|---|---|---|
| Kujundus (hele/tume) | browser, `localStorage['juristid-theme']` (ADR 0147, unchanged) | before paint, `static/js/theme.js` |
| Teksti suurus | browser, `localStorage['juristid-text-size']` | before paint, `static/js/text-size.js` |
| Avaleht | server, `accounts.PersonalPreference.landing_page` (migration `accounts/0007`) | at `/` and after choosing a persona |
| Plokid | server, `DisclosurePreference` (ADR 0194, unchanged) | the existing reset |

### Text size

Four sizes: **Väike**, **Tavaline** (the default and exactly the existing appearance), **Suur**,
**Väga suur**. Like the theme it is a choice of this browser, not of a person (behind the shared
gate the persona is a view somebody picked), so it needs no migration and follows ADR 0147's
pattern: a separate non-deferred script in `<head>` sets `<html data-text-size>` before `<body>`
is parsed — no flash at the wrong size — and stores one key; Tavaline is the absence of key and
attribute, so the server-rendered page is byte-for-byte the old one. The sizes are the eleven
typography tokens redefined per value in `tokens.css`; spacing (rem) stays, keeping pages dense.
About seventy rules with a fixed pixel size (small chips and badges) do not scale; that is
deliberate for now and listed as a follow-up. Signing out clears it with the rest of the origin's
storage (ENG-009), like the theme.

Alternative recorded (N-B2): a server-side size would follow the person across devices but needs
the size at render time on every page and a field in the same table; rejected for v1 to match the
theme.

### Landing page

* **Allowlist**, in this order: Minu asjad (default), Teemad, Osakond, Statistika, Monitooring,
  Teavitused (`app/accounts/landing.py`, `LANDING_PAGES`). The stored value is a key, never a URL;
  the redirect target is `reverse()` of the allowlisted URL name, so it cannot be an open redirect.
* **Keyed** to the person the request is signed in as — the selected persona under the shared
  gate, the account under personal sign-in — the same principal as `DisclosurePreference`. One
  row per person, created on the first explicit choice; Minu asjad is stored as "".
* **Validated twice.** `save()` refuses a key not on the list or not the person's to open
  (Monitooring needs `candidate_notices.may_see`; the rest `login_required`, as their routes).
  `landing_page()` asks again at every redirect: a choice no longer permitted falls back to Minu
  asjad and is kept, so it applies again if the page opens to them again.
* **Read by two places only**: `core.views.home` (`/`, where every sign-in lands) and the persona
  choice (`accounts.views.act_as`) when it has no safe `next`. An explicit `next` always wins.
* **Written by one route**: `POST accounts:save_landing_page` (`/konto/avaleht/`), CSRF-protected,
  `login_required`, no parameter naming a person; registered as class C (the signed-in person's
  own presentation state) in `tests/test_business_write_boundary.py`.

## Consequences

* B1: two new GET routes (`haldus:settings`, `haldus:onenote`); no route moved, no migration.
* B2: one POST route (`accounts:save_landing_page`), one additive migration
  (`accounts/0007_personal_preferences`), one head script (`static/js/text-size.js`).
* Visual baselines of Haldus pages change (sidebar).
* A new Haldus page must name its home in the registry, or the inventory test fails.

## Reversibility

Presentation only: reverting restores the flat strip; the two new routes can stay as redirects.
Reverting B2 drops one table of presentation preferences (the landing page falls back to Minu
asjad for everybody) and the text-size script (the browser key is then ignored).
