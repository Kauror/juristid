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
