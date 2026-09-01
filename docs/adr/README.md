# Architecture Decision Records

ADRs record material technical decisions and any intentional departure from
`docs/master-specification.md`. The master specification locks capabilities and
invariants; ADRs lock the current implementation choices.

Each ADR states status, context, decision, alternatives considered,
consequences and reversibility.

| # | Decision | Status |
| --- | --- | --- |
| [0001](0001-application-architecture-and-versions.md) | Application architecture and supported-version policy | Accepted |
| [0002](0002-database-and-identifier-strategy.md) | Database and identifier strategy | Accepted |
| [0003](0003-document-lifecycle.md) | Document lifecycle: immutable evidence and mutable working documents | Accepted |
| [0004](0004-authentication-direction.md) | Authentication: synthetic local sign-in now, Entra ID for real data | Accepted |
| [0005](0005-authorization-and-visibility-inheritance.md) | Authorization and visibility inheritance | Accepted |
| [0006](0006-search-architecture.md) | Search architecture: PostgreSQL first, through a rebuildable projection | Accepted, implemented in Stage 2A |
| [0007](0007-reporting-continuity-and-export-contract.md) | Reporting continuity and the export contract boundary | Accepted, exporter in Stage 4 |
| [0008](0008-production-deployment-candidate.md) | Production deployment candidate | Proposed, deliberately reversible |
| [0009](0009-design-token-foundation.md) | Design-token foundation | Accepted |
| [0010](0010-stage-1-interaction-and-browser-testing.md) | Stage-1 interaction model and browser testing | Accepted |
| [0011](0011-next-action-and-submission-modelling.md) | NextAction and Submission modelling | Accepted |
| [0012](0012-legacy-register-import.md) | Legacy register import architecture | Accepted |
| [0013](0013-search-projection-and-child-content.md) | The search projection, and why child content waits | Accepted |
| [0014](0014-content-extraction-and-derivatives.md) | Content extraction and rebuildable derivatives | Accepted |
| [0015](0015-historical-corpus-integration.md) | Historical corpus integration | Accepted |
| [0016](0016-authentication-modes-and-the-shared-gate.md) | Authentication modes and the shared gate | Accepted |
| [0017](0017-statistics-and-the-metric-catalogue.md) | Statistics, the metric catalogue and operational snapshots | Accepted, on a feature branch |
| [0018](0018-structured-matter-facts.md) | Structured Matter facts, and the generated department views | Accepted, on a feature branch |
| [0019](0019-opinion-archive-reconciliation.md) | Reconstructing historical submissions from the opinions archive | Accepted, on a feature branch |
| [0020](0020-historical-cutover-current-state.md) | The historical cutover, and what a closed archive row may claim | Accepted, on a feature branch |
| [0021](0021-final-register-cutover.md) | The final register cutover, and the two columns that mean different things | Accepted, on a feature branch |
| [0022](0022-deployment-backup-and-recovery.md) | Deployment, backup and recovery on the Unraid host | Accepted |
| [0023](0023-searchable-opinion-archive.md) | Making the whole opinions archive searchable evidence | Accepted, on a feature branch |
| [0024](0024-test-data-classification.md) | Test data is a stored class on the Matter, and purging it is a later decision | Accepted, on a feature branch |
| [0025](0025-multiple-matter-senders.md) | A Matter has zero, one or several senders; the addressee stays singular | Accepted |
| [0026](0026-source-data-enrichment.md) | Source facts are never rewritten; interpretation is added on top of them | Accepted, on a feature branch |
| [0027](0027-matter-engagement.md) | `Kaasamine` is a pointer to outreach, not an engagement system | Accepted |
| [0028](0028-development-archive-workspace-access.md) | The archive is readable behind the shared gate; the register is not | Accepted |
| [0029](0029-reference-data-foundation.md) | Reference data is governed, additive, and never invented from source strings | Accepted |
| [0030](0030-teema-workspace-redesign.md) | The Teema workspace: one page, two tabs, and one save per professional update | Accepted |
| [0031](0031-teema-human-qa-corrections.md) | What hands-on use changed about the Teema workspace | Accepted |
| [0032](0032-uus-teema-redesign.md) | The approved Uus teema redesign: one screen, one chip control | Accepted |
| [0033](0033-overview-drilldown-parity.md) | Ülevaade drill-down parity: every number opens a list of its own kind | Accepted |
| [0034](0034-persona-candidates-and-the-top-bar-switcher.md) | Persona candidates are a role-based population, and switching happens from the bar | Accepted |
| [0035](0035-the-bounded-workspace.md) | The shell keeps the viewport; the workspace stops at 1600px and centres | Accepted |
| [0036](0036-assignable-department-workers.md) | Who current business work may be assigned to, and who a filter may name | Accepted |
| [0037](0037-the-business-write-http-boundary.md) | Where business-write authorization is enforced, and why the refusal is 404 | Accepted |
| [0038](0038-child-visibility-in-projections.md) | Child visibility in projections, and the index-version gate | Accepted |
| [0039](0039-retiring-minu-tiim.md) | Retiring `Minu tiim`, and where its three period counts went | Accepted |
| [0040](0040-concurrent-final-evidence-integrity.md) | Concurrent final-evidence integrity: one lock order, one strength | Accepted |
| [0041](0041-search-index-freshness.md) | Search index freshness: durable debt instead of a rebuild somebody remembers | Accepted |
| [0042](0042-department-wide-lawyer-access.md) | Department-wide access for the legal team | Accepted |

| [0043](0043-the-ux-pass-and-the-managers-page.md) | The 2026-08-27 UX pass: an additive layer, views that live in the address, and Osakond narrowed to three questions | Accepted |
| [0044](0044-one-arvamused-destination-and-live-header-search.md) | One `Arvamused` destination, and live suggestions under the header search | Accepted |
| [0045](0045-the-repeatable-current-register-refresh.md) | The repeatable current-register refresh: authorship decides, and the sheet's own year settles a date | Accepted |
| [0046](0046-two-deadline-groups-a-week-and-the-rest-of-the-month.md) | Tähtajad in two groups: the calendar week whole, the rest of the month behind «Näita veel», and one line past it | Accepted |
| [0047](0047-arvamused-as-a-section-of-teemad.md) | Arvamused as a section of Teemad, with two searches that never meet | Proposed, on a feature branch |
| [0048](0048-the-v2-design-implementation.md) | Implementing the v2 design over the application that exists | Proposed, on a feature branch |
| [0049](0049-one-department-page.md) | One department page: `/ulevaade/` and `/osakonna-too/` become `/osakond/` | Proposed, on a feature branch |
| [0050](0050-an-open-jargmiseks-outranks-arvamuse-tahtaeg.md) | An open `Järgmiseks` outranks `Arvamuse tähtaeg` in the work model | Proposed, on a feature branch |
| [0051](0051-uus-asi-new-assignment-notices.md) | «Uus asi»: a personal receipt for a Matter somebody just put on your desk | Proposed, on a feature branch |
| [0052](0052-the-simplified-teema-next-action-workflow.md) | The simplified Teema next-action workflow | Proposed, on a feature branch |
| [0053](0053-the-1-september-snapshot-and-two-spellings-the-parser-could-not-see.md) | The 1 September snapshot, and two spellings the parser could not see | Proposed, on a feature branch |
| [0054](0054-the-action-kind-is-not-a-user-facing-concept.md) | The action kind is not a user-facing concept | Proposed, on a feature branch |
| [0055](0055-what-counts-as-evidence-for-an-archive-link.md) | What counts as evidence that a letter belongs to a Matter | Proposed, on a feature branch |
| [0056](0056-the-archive-is-department-work-product.md) | The opinion archive is department work product, not a migration tool | Proposed, on a feature branch |
| [0057](0057-a-baseline-holds-the-product-not-the-clock-or-the-seed.md) | A baseline holds the product, not the clock and not the seed | Proposed, on a feature branch |

Naming: `NNNN-short-decision-title.md`.

## Stage coverage

- 0001–0009 — Stage 0 foundation
- 0010–0011 — Stage 1 vertical slice
- 0012–0013 — Stage 2A import and search foundation
- 0014 — Stage 2B evidence and content intelligence
- 0015–0016 — Stage 2D historical corpus and authentication modes
- 0017 — Stage 2E statistics and reporting
- 0018 — Stage 2G structured Matter facts
- 0019 — Stage 2H opinion archive and historical submissions
- 0020 — Stage 2I historical cutover state
- 0021 — the final register cutover
- 0022 — deployment, backup and recovery on the host the system actually runs on
- 0023 — Stage 2H.2, the searchable opinion archive
- 0024 — real/test data classification and the purge plan that precedes a purge
- 0025 — multiple Matter senders, and the addressee that stays singular
- 0026 — Wave 2 source-data enrichment: JÄRGMISEKS, OneNote filing structure and historical activity
- 0027 — Wave 2 Kaasamine: how members and stakeholders were asked
- 0028 — the development archive workspace: who may read the corpus behind the shared gate, and why that is not the register
- 0029 — the reviewed reference-data baseline: nine policy areas, the core public institutions, and why no backfill yet
- 0030 — the approved Teema workspace redesign: two tabs, one composer save, grouped-not-merged history, and the twenty-three working Valdkonnad
- 0031 — what a working session on real data changed: the position back in the rail, one dated work list, a full edit page beside the inline controls
- 0032 — the approved Uus teema redesign: one screen, one chip control, two Valdkonnad withdrawn, and Hetkeseis explaining itself on the row
- 0033 — Ülevaade drill-down parity: a number opens a list of its own kind, `?too=` makes the dated-work populations addressable, and "Ootab pahavarakontrolli" stops telling readers their archive might be infected
- 0034 — who may be offered as a persona: one role-based population read by both the page and the endpoint, technical accounts excluded, and a top-bar switcher that keeps the page somebody was reading
- 0035 — the bounded workspace: the shell keeps the viewport, the working surface stops at 1600px and centres, and one token says so for every page
- 0036 — who current business work may be assigned to: one role-based rule behind persona and assignment, a separate native entry point so a new step never lands in a departed colleague's queue, and filters that describe stored work instead of narrowing to who may be given it
- 0037 — where business-write authorization is enforced: one HTTP decorator over the existing `may_write_business_content` rule, applied before the view body, answering 404 so a reader learns nothing about what exists for somebody else — and a completeness test that fails if a future mutating route arrives unclassified
- 0038 — child visibility in projections: a `Kaasamine` gets its own search row rather than being folded into the Matter's, one `ChangeEvent` scoping helper for the timeline, filter labels resolved inside the reader's own data, and an index-version gate that makes pre-fix search rows ineligible on deploy rather than after a rebuild
- 0039 — retiring `Minu tiim`: one operational overview population, its three period counts moved into `Aruandlus` as rows of that block, `?vaade=tiim` normalizing to `Kogu osakond`, and no population widened anywhere in the move
- 0040 — concurrent final-evidence integrity: both writers serialise on the Matter row under one global `Matter → Submission → Document` order, the waiter re-reads what it decides on, a `matter_id` trigger refuses reparenting relied-upon evidence, and no automatic repair — DATA-001 keeps the detector
- 0041 — search index freshness: a high-fanout rename records a durable obligation in its own transaction instead of nothing at all, a Compose worker discharges it with the atomic rebuild, `Kaasamine` becomes a bounded synchronous refresh so a recorded consultation is a findable one, and the diagnostic reports the debt without ever draining it
- 0042 — department-wide access for the legal team: the confidentiality boundary is the application rather than the Matter, both lawyer roles read the whole department, ownership and collaborators go back to meaning responsibility, the collaborative write model is unchanged, and `ADMINISTRATOR`, `DepartmentViewer` and `READER` are all deliberately left where they were

- 0043 — the 2026-08-27 UX pass: an additive `ux` stylesheet and script, saved views that are named URLs rather than stored rows, one parameterised `?too=tahtaeg-vahemik` so every deadline group opens exactly its own window, deferring as two existing services behind one control, and Osakond narrowed to what the team is doing, what is ahead and what is done
- 0044 — one `Arvamused` destination on the bar with the held archive as its own tab, and live suggestions under the header search: five Matter rows bounded in SQL, the existing authorized ranking reused rather than reimplemented, a request token that stops a stale answer painting over a newer one, and a plain GET form still underneath it all

- 0045 — the repeatable current-register refresh: authorship rather than existence decides whether the register may speak, a year-less date takes the sheet's year only where the snapshot agrees, a wait beside its own review is one instruction, clause ownership removes a date instead of being defeated by it, the two member-feedback counts live on the derived table with `NULL` kept distinct from a measured zero, and outreach pointers are proposed by a matcher that never writes and created only by a reviewed mapping

- 0046 — Tähtajad in two groups: the calendar week cut by the calendar and never truncated, the rest of the month behind the existing «Näita veel», the one-line pointer past it kept so nothing dated falls off the page, every group linking through the parameterised `?too=tahtaeg-vahemik` rather than a fixed name, and `WORK_DEADLINE_THIS_WEEK` deliberately left counting from today for the SEIS strip beside it
- 0047 — Arvamused as a section of the Teemad page rather than a destination on the bar: the workspace moves one level in with its Saadetud/Arhiiv strip intact, the two searches stay two searches on separate parameters (`q` and `arvamus_q`) so neither box can narrow the other's list, the section is bounded at twelve rows and states its real total, a fragment route of its own keeps the register from answering an opinion search with teemad, and `may_read_archive` is asked before anything is counted — an archive request it may not serve resolves to Saadetud rather than taking the register down
- 0048 — The v2 design implemented over the application that exists rather than beside it: no second tracking subsystem and no second statistics application, one read model serving both modes of Minu asjad, the bands redefined once in `work_items.py` with the WAIT/MONITOR semantics unchanged, `PersonalScratchpad` as the only schema change with its privacy structural rather than checked, every previous address kept as a permanent redirect, and every figure the application cannot express as a list recorded in `app/core/development_status.py` instead of drawn
- 0049 — Two department pages become one `/osakond/`: both old addresses redirect permanently with their query strings, read access stays the broad Ülevaade access the merged page inherited while Meeskond and Tehtud stay the head's and are not calculated for anybody else, one Seis strip of six figures replaces two overlapping ones, one Eesolev of five partitioning windows replaces two deadline panels over the same `real_deadlines` population with Kaugemal becoming a real list, Tehtud gains a URL-backed row-kind filter that never moves its period summary, the team table's year total and Aruandlus are made one population so one business fact has one definition, the bar carries a single Osakond destination, and Valdkonniti stays a scope of the same route — no model, no migration and no second read system
- 0050 — `Arvamuse tähtaeg` is the deadline a file carries until somebody says what happens next: an open `Järgmiseks` outranks it and suppresses it from the active-work model, with no comparison of dates — any open action wins, later date or none at all, DO, WAIT or MONITOR alike, and a register-materialised action counts like a typed one — while the stored `response_deadline` is never cleared and keeps stating itself in the Matter header; one clause in `outstanding_response_deadlines`, reader-blind because it can only remove a row, and `järgmise tegevuseta` and `Oluline tähtaeg` deliberately unchanged
- 0051 — «Uus asi» on Minu asjad: a human owner assignment gives the recipient a notice, self-assignment included, and an automated or imported one never does — the boundary being the `provenance` argument `assign_matter` already carried, now taken by `create_matter` too because that is where an initial owner is written; the notice is personal to its recipient in the sense the scratchpad is, absent from a department head’s view of that desk rather than hidden in it, acknowledged only by opening the Matter *from* the block and never by rendering `matter_detail`, superseded rather than deleted when the file is handed on or taken off every desk, and rendered as no block, no heading and no reserved space when nothing is unread — one additive model, no backfill, and no notification system anywhere else in the product
- 0052 — the simplified Teema next-action workflow: the composer stops asking a lawyer to classify their own work, `Järgmiseks` becomes its own text box beside the entry rather than being derived from it, a native step is stored `DO`/`DEADLINE`/`EXACT` with the date meaning the day it gets done, `ActionKind` and `DateSemantics` keep every stored value and no row is rewritten, `✓ Tehtud` writes no entry and swaps one row so an open composer keeps what was typed into it, superseding stays distinct from completing, and `Sildid` leaves the Teema detail page as a UI retirement with `Muu valdkond` moving into the facts block
- 0053 — the 1 September current-register snapshot: its digest is registered with the unchanged `{2025, 2026}` scope while the never-reviewed 30.08 workbook beside it is deliberately not, `küsi` joins the review verbs but only with `üle` so bare *ask* stays out of the work queue, a comma between a review verb and its particle is read as a typo when a single date and nothing lexical sits there, a review date beside a plainly-stated external milestone stays refused because 2.0 decided that on the record and a refresh brief is not where it gets reversed, and a `HETKESEIS` reading `17` resolves to no stage at all rather than growing the controlled vocabulary — parser 2.0 → 2.1, no schema change, and two 2026 rows convert that did not before
- 0054 — the action kind is not a user-facing concept: `TEEN`/`OOTAN`/`JÄLGIN` is never printed and nothing replaces it, the date meanings `TÄHTAEG`, `VAATAN ÜLE`, `OODATAV AEG`, `OLULINE TÄHTAEG` and `ARVAMUSE TÄHTAEG` stay because they describe the date rather than classify the step, `ActionKind` keeps every value and every row with no migration and no backfill so only `DO`+`DEADLINE` can still be overdue and a passed review date is still ripe rather than late, `?tegevus=` loses its three per-kind values and the two per-kind review values for one `ulevaatus` covering both, `Statistika` retires `NEXT_ACTION_BY_KIND` and merges `WAIT_REVIEW_DUE` and `MONITOR_REVIEW_DUE` into a single `REVIEW_DUE`, and `.mode`, `.modechip` and `.modeselect` are removed with the pre-v2 `work_row.html` that was their last possible renderer
- 0055 — what counts as evidence that a letter belongs to a Matter: the stopword list is written in Estonian and folded at import so intent and effect are one object — it carried `poordumine` for *pöördumine* while `fold` produces `rdumine`, a word on 207 register titles that was carrying automatic links on its own — a recipient becomes a set of named bodies over a reviewed abbreviation table rather than one opaque string, since 163 of the 192 unmatched files had a register row on their own date hidden by *MKM* against the spelled-out ministry, and a Riigikogu proceeding number becomes a route of its own, `EXACT_LAW_REFERENCE_MATTER`, corroborated by addressee or date and never sufficient alone because 25 of 165 numbers name several Matters; it runs *after* the register route rather than before it, because citation-first was measured withdrawing six links production already holds, and the rule is that more independent exact signals outrank fewer — no score, no threshold, `CONTENT_MULTI_SIGNAL` deliberately not promoted even though all 767 PDFs were measured to carry text, and `derive_links` still removes nothing
- 0056 — the opinion archive is department work product rather than a migration tool: `ARCHIVE_READERS` becomes one set asked in every authentication mode, `ROLES_WITH_RESTRICTED_ACCESS` plus ADMINISTRATOR, because ADR 0042 had already made the two lawyer roles read department-wide including every RESTRICTED Matter these letters are filed onto while a specialist still could not open the Chamber's own letter about one, and because narrowing outside the shared gate would have taken the archive from the whole department the day Cloudflare Access replaced the shared password; writing did not move at all, so reading the department's correspondence stops being a privilege while asserting what it concerns remains one, READER is not widened, an administrator reaching a letter still learns nothing about the RESTRICTED Matter it is filed onto, and the embedded section pays exactly the one extra query ADR 0047 already documented for a reader who may read the archive
- 0057 — a baseline holds the product, not the clock and not the seed: five persona images were stale on ADR 0049's top bar and are retaken from a real run's candidates with `E2E_UPDATE_BASELINES` left at `"0"`, after attribution proved each differs in one y-band and nowhere else; a search result's `source_locator` stops being a primary key, removed from the product rather than masked because `kaasamine-<uuid>` told a lawyer nothing the badge beside it did not, fixed at the producers *and* suppressed in the read model by source kind so already-stored rows stop printing one without bumping `INDEX_VERSION` — a fail-closed authorization gate that is the wrong instrument for a field in no `SearchVector`; and the folded system-run date span is masked through `.uxtl__sysrow > span:nth-child(2)`, positional because the element has no class and product markup does not carry test affordances, masked rather than normalised because the columns that differ are the digits and «näita ▸» does not move — which moves `teema-ulevaade` and `teema-1024` once, by construction, since those baselines hold the glyphs a mask now covers
