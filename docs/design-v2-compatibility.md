# Koda Õigusloome v2 — implementation compatibility report

Written against `main` at `3d3a5e6b43f25a6d1a7b594e5b16ba8a2650f470`, before the
first UI change of the v2 design handoff (`handoff_v2/`, read 29.08.2026).

Its only job is to stop the redesign building a second copy of something the
application already has. It is not a re-planning of the handoff and it does not
re-decide anything the design review settled. Where the handoff and the
repository disagree, the resolution is recorded in
`app/core/development_status.py` and shown at `/haldus/arendus/`.

Source-of-truth order used throughout: the implementation brief, then
`01-EHITUSJUHIS.md`, then `02-EKRAANID.md`, then the prototype for visual
structure, then `03-BACKEND.md` read against this architecture, then `lisad/`.

---

## Screen-by-screen mapping

### Minu asjad — `/minu-too/` to `/minu-asjad/`

| | |
| --- | --- |
| **Existing** | `app/matters/my_work.py` (`build_my_work`), `app/matters/views.py:my_work`, `templates/matters/my_work.html`, rail blocks `quietrow`/`undatedrow`, horizon picker `?kuni=`, `workhead__summary` header figures. |
| **Design** | H1 «Minu asjad»; `seis seis--compact` strip under the title; four bands (Üle tähtaja / Sel nädalal / Järgmised 30 päeva / Hiljem); new *Aktiivsed teemad* section with chips, scoped search and one row per Matter; rail = Märkmed, Järgmise tegevuseta, Kuupäevata, statistics foldout; no green tick; no `X` key hint. |
| **Approach** | Reuse `build_my_work` and the shared `work_items` read model; parameterise it with `subject`. Re-band in `work_items.py` (one change, three pages). Add a portfolio selector and a recent-activity selector on top of the existing `annotate_last_activity`/`activity_of`. Restyle the template. Drop the `workhead__summary` figures in favour of the shared `seis` component. |
| **Conflict** | Band counters: the prototype still prints «8 tegevust», `01` §4 says bare number. `01` wins (DS-01). |

### Inimese töölaud — `/inimesed/<uuid>/asjad/`

| | |
| --- | --- |
| **Existing** | Nothing. Osakonna töö team rows currently open the register filtered by owner. `app/core/authorization.is_department_head` is the existing gate; `department_views.py` is the existing pattern (404, not 403). |
| **Design** | The same page as Minu asjad with `is_self` conditionals; crumb and person switcher; Kiirvaade instead of Märkmed; scratchpad never rendered. |
| **Approach** | One template, one read model, two modes. New route, new `PersonalScratchpad` model (additive), new view reusing `build_my_work(user, subject=…)`. Authorization is the *existing* department-head rule, not a new entitlement. |
| **Conflict** | None. Prev/next colleague order is an open question in `01` §9 — implemented in the team-table order the prototype shows, and logged (DS-08). |

### Osakond — `/osakond/`

> Supersedes the two rows this replaced — *Osakonna töö* (`/osakonna-too/`) and
> *Ülevaade* (`/ulevaade/`). They described two destinations, which they no
> longer are: they merged into one page and both old addresses redirect to it
> permanently (ADR 0049). What each said about its *components* still holds —
> the team grid, Eesolev, Tehtud, the area view and the rails are the same
> components, reused rather than rebuilt.

| | |
| --- | --- |
| **Existing** | `app/matters/department_dashboard.py` and `app/matters/overview.py` (the read models, both unchanged in place), `app/matters/department.py` (a thin composition layer, new), `department_views.py`, `templates/matters/department.html` with `partials/department_main.html` and `partials/department_rail.html`. The team grid (`uxstat`), Eesolev (`uxdl`), Tehtud (`uxdg`), `interrow`, `overview_areas.html` and `overview_rail.html` are the components the two old pages carried. |
| **Design** | One page for «kus osakond seisab»: header, one Seis strip of six figures, Meeskond (head only, no heading), Vajab sekkumist, Eesolev in five windows, Tehtud with a period and a row-kind filter (head only), and a three-block Faktid rail. Valdkonniti is a scope of the same route. No Koormus rail, no second Vajab sekkumist, no separate Viimased muudatused feed. |
| **Approach** | Compose the existing selectors; add no definition, no model and no migration. Route `/osakond/`; `/ulevaade/` and `/osakonna-too/` are permanent redirects that keep their query strings, so `?vaade=valdkonniti` and `?periood=…` still name what they always named. `templates/matters/overview.html`, `templates/matters/department_work.html` and `partials/overview_department.html` are deleted — nothing renders them. |
| **Conflict** | None outstanding. `02`'s Koormus rail block on the manager page (DS-04) is resolved by the merge rather than by adding one: Meeskond is the people view. `02`'s three Eesolev tiers versus the prototype's four (DS-05) is superseded by the five approved windows in ADR 0049 §5. |

### Teemad — `/teemad/`

| | |
| --- | --- |
| **Existing** | `matter_list.html` with live search, the `filterpanel` «Täpsem otsing», a segmented state filter, `registercount`, fixed `PAGE_SIZE = 25`, and the Arvamused section (ADR 0047) rendering **tables**. |
| **Design** | Results counter and «näita korraga 12 · 30 · 50 · kõik» (default 12); «Täpsem otsing…» under the search field on the left, in the same place on every search block; Arvamused rows as two-line `submission` cards; no «Saadetud seisukohad ja ajalooline arhiiv» caption; no green «Tõend» box. |
| **Approach** | Add a page-size parameter to the existing register pipeline (the count and the list keep coming from one query). Move the existing `filterpanel` disclosure. Replace the embedded Saadetud table with the `submission` component. |
| **Conflict** | `/teemad/tapsem/` is a prototype **state** of this page, not a route (brief §6.4). No second search is built. |

### Uus teema / Muuda teemat

| | |
| --- | --- |
| **Existing** | `matter_create.html` and `matter_edit.html`, both driven by `app/matters/forms.py`; ADR 0032 already rebuilt Uus teema into the chip-row `createform`. |
| **Design** | One shared visual language; field help text removed (valdkonna vaba tekst, mitme saatja selgitus, adressaadi selgitus); Muuda adds Sildid and Nähtavus and a compact «Muutumatu» strip; no «Järgmine tegevus» panel on Muuda. |
| **Approach** | Share partials where the two forms already agree; remove the named help texts from the *rendered* output. Business operations stay separate. |
| **Conflict** | None. |

### Teema töölaud and sub-pages

| | |
| --- | --- |
| **Existing** | `matter_detail.html`, `matter_documents.html`, `matter_position.html`, `partials/rail.html`, `position_rail.html`, timeline partials. |
| **Design** | Compact «Teema andmed» rail everywhere; the reference back in the rail; no «Aruandlusaasta»; hetkeseis badge beside the H1; timeline open by default; no «Põhjendus» block on the position card; the four `cardnote` sentences removed. |
| **Approach** | CSS density on `railcard` plus template edits. No selector or service changes. |
| **Conflict** | None. |

### Jälgimine — three pages

| | |
| --- | --- |
| **Existing** | **`app.intelligence` already implements all of it**: `MatterImportantDate`, `MatterEffectiveDate`, `MatterWorkVictory`, their selectors, services, forms, authorization and three generated views at `/olulised-tahtajad/`, `/joustuvad-aktid/`, `/toovoidud/`, behind one `Jälgimine` navigation item with the tab strip the design draws. |
| **Design** | `/jalgimine/…` routes, a per-tab H1, a compact seis strip, `table--register` tables, `tbody.uxextra` accordions, two sections per page. |
| **Approach** | **Reshape, do not duplicate.** No `ImportantDeadline` / `EntryIntoForce` / `Win` models are created — `03-BACKEND` §6 says to check first, and the equivalents exist with richer semantics (precision, status, supersession, confirmation). New URLs are added and the old ones keep resolving. |
| **Conflict** | The prototype's `+ Lisa oluline tähtaeg` button on the department view has no Matter to write to; writes are Matter-scoped by design. Not added; logged (DS-06). |

### Statistika — `/statistika/`

| | |
| --- | --- |
| **Existing** | **`app.reporting` is substantial and working**: overview, teemad, tegevus, ajalooline, andmekvaliteet, definitsioonid, two drill-through lists, CSV exports, and a metric catalogue with authorization applied before counting. |
| **Design** | The overview redesigned: five-figure seis strip, three tables, a Praegu / Aruandlus / Inimesed rail, a definitions foldout, a CSV button, no charts. Sub-tabs explicitly **not designed**. |
| **Approach** | Redesign the overview template over the existing metric catalogue. Sub-tabs are left exactly as they are. |
| **Conflict** | The new IA names five tabs (Ülevaade, Teemad, Tegevus, Ajalugu, Definitsioonid); the app has six routes (plus Andmekvaliteet). Not silently dropped; logged (DS-07). |

### Saabunud and Lisa saabunud materjal

| | |
| --- | --- |
| **Existing** | `views.inbox` and `inbox.html`; `views.intake`, `intake.html` and `app/matters/intake.py`. Both routes exist. |
| **Design** | Inbox: compact seis strip, Vastutajata teemad as a register table with four rows and an accordion, Hiljuti loodud, the explanatory `cardnote` gone. Intake: `createform` layout, crumb, Lühikokkuvõte, Märkmed vastutajale, footer note, no «Viide saatja juures». |
| **Approach** | Template rebuilds. `brief_summary` already exists on `Matter`. |
| **Conflict** | «Märkmed vastutajale» has no field in the model. Recorded as the Matter's first timeline `Entry` rather than as a new column, which matches the handoff's own statement that it is visible to everyone who can see the Matter; logged (DS-09). |

### Dokument — `/dokumendid/<pk>/`

| | |
| --- | --- |
| **Existing** | `documents/document_detail.html`. |
| **Design** | The filename in the H1 *is* the download link; Originaal, a Tehnilised andmed foldout, Versioonid; no «Tuletatud eelvaade» block. |
| **Approach** | Template edit. Download authorization unchanged. |
| **Conflict** | Removing the derived-preview block removes the only surface for extracted text. The extraction pipeline, its routes and its data are untouched; logged (DS-10). |

### Vali kasutaja — `/konto/kasutaja/`

| | |
| --- | --- |
| **Existing** | `accounts/choose_persona.html`; the current row is a `<p>`, the others are already submit buttons. |
| **Design** | Every selectable row is a button in full. |
| **Approach** | Template edit only. |
| **Conflict** | None. |

### Arvamused — legacy routes

`/arvamused/`, `/arvamused/arhiiv/`, `/arvamused/plokk/` and the write routes all
keep working and keep their filters and pagers, exactly as ADR 0047 left them.
Arvamused is not returned to the navigation bar. The handoff's «vanad URL-id
suunatakse ümber» is **not** implemented — the current merged behaviour is
backwards-compatible destinations, and the brief forbids changing that without
separate confirmation; logged (DS-03).

### Persona selection and authorization

No authorization rule is widened by this work. `visible_to(request.user)` stays
in front of every count. The person workspace reuses `is_department_head` and
answers 404. The scratchpad endpoint reads and writes `request.user` only, with
no `subject` parameter to widen.

---

## What is deliberately *not* built

- No second tracking subsystem, and no `ImportantDeadline` / `EntryIntoForce` / `Win`.
- No second statistics application, and no redesign of the undesigned sub-tabs.
- No `/teemad/tapsem/` route and no second search implementation.
- No charts, no emoji, no gradients, no decorative animation.
- No «Minu tiim» scope (ADR 0039).
- No new CSS component where one already exists; the density changes named in
  `01` §5 are made in the canonical declarations rather than as overrides.
- No time estimate from the handoff is treated as a requirement.
