# 0212 — The November 2026 UI round and its combined release

Status: accepted (owner's brief «Consolidated UI and Functionality Implementation», 2026-11-06)
Date: 2026-11-06

This ADR records thirteen user-interface and functionality changes (UI-01 to UI-13) and three release-readiness follow-ups (REL-01 to REL-03). They ship as one release together with the held fixes of [0211](0211-post-audit-fixes-cohort-retirement-export-audit-release-image-gate.md).

It amends:
- [0051](0051-uus-asi-new-assignment-notices.md);
- [0147](0147-a-light-theme-beside-the-dark-default.md);
- [0158](0158-joustumised-is-a-presentation-of-the-teemad-register.md);
- [0186](0186-privileged-administrator-sessions-under-the-shared-gate.md);
- [0198](0198-personal-deadline-notifications-and-one-inbox.md);
- [0208](0208-a-shared-onenote-page-reads-as-one-dossier-and-its-originals-are-served-read-only.md);
- [0210](0210-the-chronology-reads-onenote-notes-and-titles-change-only-by-review.md);
- [0211](0211-post-audit-fixes-cohort-retirement-export-audit-release-image-gate.md) §1.

Every other decision of those ADRs stands.

## Decision

### 1. The bell opens a panel, and one read state serves every surface (UI-01)

**The panel.** Pressing the bell opens a panel under it (`app.notifications.feed`); the page stays where it is.
- It lists the reader's unread items first, then their history, each newest first. The history holds read, hidden and retired deadline notices, personal commencement receipts, and viewed or superseded «Uus asi».
- It loads in keyset pages of 15. Every page is a GET that writes nothing.
- Each item has a «⋯» menu with «Märgi loetuks» / «Märgi lugemata», plus «Lükka edasi» on a commencement check.
- The panel also offers «Märgi kõik loetuks», «Teavituste seaded» and «Kõik teavitused».

**The pages keep working.** `/teavitused/` stays as the dedicated page and gains a «Varasemad» history section. The Minu asjad block keeps its place and design, and lists only items still awaiting the reader.

**One recipient-scoped state, built on existing stamps.** There is no schema change:
- deadline notices use `read_at`, `dismissed_at` and retirement;
- commencement checks use `CommencementReminderPersonalState.read_at`;
- «Uus asi» uses `viewed_at` and `superseded_at`.

The bell count, the panel's unread items, the Minu asjad block and the page's inbox sections are the same set.

**Reading is not doing the work.** A read «Vajab tegevust» notice now leaves the bell and the Minu asjad block. This replaces the 2026-11-02 rule of 0198 that it "stays listed until its work is done". Its deadline still appears on the Teema and in the work lists.

**Writes.** Every write is a CSRF-protected POST:
- the recipient is `request.user`, and another person's row is a 404;
- it writes no `ChangeEvent` and calls no business service;
- it changes no deadline, step, Matter lifecycle or other user's row.

**«Märgi kõik loetuks».** It marks the reader's awaiting items that arrived by a server-side boundary: the moment the panel or page was drawn.
- The boundary is capped at the start of any other in-flight transaction in the database (`pg_stat_activity`, falling back to now), so a row written but not yet committed stays unread.
- A commencement check "arrives" at the latest of:
  - its creation;
  - the Teema's newest `MATTER_ASSIGNED`;
  - the reader's own state row;
  - the end of a postponement.

**Re-polling.** Re-polling never revives a read alert. The generator's interval key still raises genuinely new events.

**«Uus asi» (0051).** «Märgi lugemata» clears `viewed_at`, only while the Teema is still the reader's and no newer receipt is active.

### 2. The favicon is the round logo (UI-02)

The favicon, its 16/32/48 PNGs, the `.ico` and the 180 px touch icon are all cut from `static/brand/koda-logo-round.png`, the same file as the top-left emblem. `tools/brand_favicons.py` crops and resamples them; nothing is redrawn.

`components/favicon_links.html` is the one place that declares them. `/favicon.ico` redirects to the round `.ico`.

### 3. The top bar's right-hand order, and «Seaded» (UI-03; amends 0147 and 0186)

**Order.** Search → «+ Uus teema» → persona or account → bell → «Seaded» → «Välju».

**Sizing.** Every control is `--topbar-control-size` tall, with `--topbar-gap` between controls.

**«Seaded».** The gear is a `<details>` menu named «Seaded», holding:
- the light/dark switch (0147: same storage key and behaviour; there is no longer a separate button on the bar);
- «Teavituste seaded», for the bell's readers;
- Haldus, on unchanged `haldus_link` terms (0186).

Signed out, the same gear holds only the theme switch.

**Menu contract.** The top bar's menus — «Seaded», the notification panel, the persona popover and «Veel» — close one another through a `juristid:menu-open` event. Each closes on Escape and on an outside click, returning focus to its trigger.

### 4. Enlarging the calendar, and rest days (UI-04, UI-11; amends 0158)

**Enlarging.**
- In the rail, the active «4 nädalat» or «Kuu», and the KALENDER heading (a button styled as the heading), open the existing calendar panel in the same view and period.
- The address is the page's own with `kalendri_aken=1` added, so a desk stays that desk and Osakond keeps its `vaade`.
- Inactive controls switch the rail. Quarter and year are unchanged.

**Rest days.**
- Every grid marks Saturdays, Sundays and the twelve puhkepäevad of the Pühade ja tähtpäevade seadus in `--calendar-rest-day`. The source is RT I, 20.12.2022, 5, verified on Riigi Teataja on 2026-11-06.
- The marks come from `app.core.holidays.rest_day`, one deterministic provider with Easter computed offline.
- A holiday's name is in its title, its accessible name and its popover, never in colour alone.
- This is informational only: no date is disabled or moved, and nothing is counted differently.

### 5. Osakond, the Teemad tabs and the advanced search (UI-05 to UI-08)

**Hidden zero indicators.** On Osakond, «tähtajaga teemat sel nädalal», «vastutajata» and «uut läbi vaatamata» are left out entirely when they are zero (`SeisFigure.hidden_at_zero`). The other figures, the counts and the drill-downs are unchanged.

**Tabs.** The Teemad status row is Avatud | Suletud | Kõik.
- Archive is a record kind (`liik=ARCHIVE`), not a status.
- `?olek=arhiiv` keeps its exact meaning — every archive record, open or closed — and is shown as a removable chip «Kirje liik: Arhiiv (avatud ja suletud)».
- A closed archive row no longer repeats «Arhiiv» beside «Suletud». `record_mode` and the closure rules are untouched.

**Advanced search.** The panel is grouped and spans the results' width:
- Põhifiltrid;
- Kuupäevad: always shown, with any number of independent ranges at once;
- Asutused: a disclosure, keeping either-role Asutus beside role-specific Saatja and Adressaat;
- Muud tunnused: a disclosure.

A collapsed group's active constraints stay visible as removable chips. No parameter was renamed. Link-only parameters are now carried through a submit instead of being dropped. The result sets of 106 representative query strings are pinned in `tests/ui07_url_semantics.json`.

### 6. The Teema's action row and the current-action toolbar (UI-09, UI-10)

See the amendment below (completed at integration).

### 7. The historical OneNote copy reads as a document (UI-12, UI-13; amends 0208 and 0210)

**The source page.** `/ajalugu/<pk>/` reads as a document:
- one heading, a back-link and a file summary that counts source files apart from canonical Documents;
- real headings and lists, and working http(s) links in prose;
- a contents list built from real headings and dated notes.

Technical provenance lives in «Tehnilised andmed».

**Decoding block text.** Capture-written block text (`source_kind`) is decoded exactly once for display and then escaped; stored text is never rewritten. OneNote stores `one:T` as an HTML fragment, and the reader kept its character references, which were then escaped a second time. That is how «&quot;» reached readers.

**Shared dossiers.** A shared dossier's held state is said once, with links to its Teemad. Only file states that differ are shown per file. The association state is unchanged.

**Anchors and the note row.**
- Blocks carry `id="plokk-<ordinal>"` where the ordinal is unique.
- An opened timeline note shows its first paragraph in place of the preview and the rest once, with no generated date or source row.
- «OneNote'i koopia» opens the source page at the note's anchor. The copy icon right after it copies exactly that address.

### 8. Cohort membership is proven (REL-01; amends 0211 §1)

**What a retirement must name.** A `LovableSyncRetirement` must name a `LovableSyncMembership` of the same cohort, Teema and Lovable id, beside:
- the Teema's tombstone;
- the Teema's own MATTER_DELETED event. That event must have removed a synchronisation state, must not be the replacement, and must carry the retirement's actor.

**Memberships.**
- They are written when a state is seeded from the cohort's provenance. They are append-only and stay under the tombstone.
- A state seeded before migration `legacy_import.0024` gets its membership at deletion, before the purge, only if the cohort's provenance names it with that Lovable id.
- An unproven state is not retired, and its loss stops the night as drift.

**Enforcement.**
- `lovable_sync retire` refuses with NO_MEMBERSHIP_EVIDENCE or WRONG_COHORT before writing anything.
- The database enforces the insert rules (0024 triggers) as well as the model.
- The trust root is the synchronisation-state table: forging a membership requires raw write access to it.
- 0024 refuses to reverse while either table holds rows.

### 9. The release image's remaining findings (REL-02), and the combined release (REL-03)

**REL-02.** The release image keeps 0211 §3's gate: 0 fixable HIGH or CRITICAL. The unfixed findings are assessed in the release report rather than suppressed.
- CVE-2026-6653, in the system libxml2, is not reachable: only Tesseract's libarchive loads that copy.
- The residual exposure is in libraries bundled in Python packages: lxml's libxml2 and CPython's expat, reached by the intake reader's Office parsing. It is mitigated by size budgets and the reader's confinement.
- `lxml` moves to 6.1.3 as defence in depth.

**REL-03.**
- The combined release is built as its own image, under 0211's runtime, scan and id gates. The held r50 image is never deployed.
- It migrates production directly from r49: `legacy_import.0023`, then `0024`, then `core.0003`.
- It may be rolled back by code only, to r49 only, never to the held r50 code. That rollback is refused once a retirement or membership row exists.

## Consequences

- No new frontend framework. No data migration beyond 0024's additive table and triggers. No Lovable APPLY, no OneNote reimport, no file promotion.
- A register Teema permanently deleted on r49 before this release goes live cannot be retired afterwards. The release kit refuses to migrate while such a deletion exists.
