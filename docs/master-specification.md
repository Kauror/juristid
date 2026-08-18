---
title: "Koda Õigusloome"
subtitle: "Reconciled Master Product, UX, Data, Architecture, Security, Migration and Delivery Specification"
author: "Eesti Kaubandus-Tööstuskoda"
date: "18 August 2026 · Version 1.2"
lang: en
---

# Contents

- Document status and authority
- 1. Executive specification
- 2. Evidence reconciliation
- 3. Product principles
- 4. Product goals and acceptance outcomes
- 5. Users, roles and authorization
- 6. Koda advocacy operating model
- 7. Information architecture and navigation
- 8. Teema page specification
- 9. Functional requirements
- 10. Explicit non-goals
- 11. Canonical domain model
- 12. Technical architecture
- 13. Performance and large-data design
- 14. Estonian search specification
- 15. Document and email architecture
- 16. Security, privacy and governance
- 17. Visual design system and interaction standards
- 18. Reporting, statistics and management continuity
- 19. Migration and historical preservation
- 20. Integration roadmap
- 21. AI and intelligent assistance
- 22. Quality engineering and testing
- 23. Repository, code and delivery standards
- 24. Deployment and operations
- 25. Existing alpha disposition and reuse plan
- 26. Recommended build order
- 27. Pilot, acceptance and cutover
- 28. Open business decisions and deadlines
- 29. Locked decision register
- 30. Implementation kickoff package
- 31. Evidence and official references
- 32. Final implementation mandate

# Document status and authority

This document is the reconciled source of truth for designing and building the next-generation internal legislative-matter and advocacy system for Eesti Kaubandus-Tööstuskoda (Koda).

It reconciles seven evidence streams:

1. the live `Tööd eelnõudega` Excel register covering 2011–2026;
2. the live OneNote workflow and a structured OneNote discovery sample;
3. the existing Lovable alpha application and its operating guide;
4. Koda's real legislative, policy-influence and member-representation work;
5. the original architecture and product handover;
6. the independent adversarial validation report;
7. the second independent devil's-advocate review of v1.1.

Where sources conflict, precedence is:

1. current live operational source or the most recent supplied source copy;
2. current user guide and direct user statements;
3. direct code inspection;
4. structured discovery evidence;
5. validation or design interpretation.

This rule resolves one material conflict: the current workbook supplied for this specification contains **11** authoritative `Hetkeseis` labels and explicitly includes `ootan ELi õiguse ülevõtmist`. The independent validation report's contrary claim about 13 labels and the absence of that status does not match the current workbook and is not adopted.

## Decision status vocabulary

Throughout this specification:

- **LOCKED** — implement as written unless a documented architecture decision record supersedes it.
- **PHASED** — accepted, but intentionally scheduled after the adoption core.
- **OPEN** — business ownership or policy decision still required.
- **REJECTED** — do not implement unless new evidence materially changes the case.

## Version 1.2 reconciliation

Version 1.2 incorporates the second independent adversarial review and tightens the product around the policy record rather than attempting to replace every productivity tool surrounding it.

Material changes from v1.1:

1. **`Submission` becomes MVP data.** One Teema can produce several written submissions; formal opinions are no longer represented by one sent-date field on Matter.
2. **The document model becomes deliberately hybrid.** PostgreSQL owns document metadata, Azure Blob owns immutable evidence, and SharePoint may hold clearly identified collaborative working documents. Final/sent/received evidence is snapshotted immutably to Blob.
3. **Historical records use the same `Matter` model.** `LegacyRegisterRecord` is removed. Older rows become `Matter(record_mode=ARCHIVE)` with provenance and data-quality metadata.
4. **A Secure Pilot Gate precedes real departmental data.** Real member feedback and draft advocacy material may enter only an approved environment with Entra authentication, centralized authorization, approved storage, backups, restore verification and upload protection.
5. **`Järgmiseks` gains explicit action semantics.** `DO`, `WAIT` and `MONITOR` actions behave differently; deadlines, review dates and expected-around dates are not treated as equivalent.
6. **Search gets a rebuildable `SearchDocument` projection.** PostgreSQL remains the search engine, but ranking/indexing is decoupled from the transactional schema and can later feed another engine if measured limits are reached.
7. **Tagging is narrowed.** `PolicyArea` remains the broad reporting classification; `Tag` is a controlled thematic/search concept. Sector and regulatory-instrument dimensions are not hidden inside the tag taxonomy.
8. **Metric coverage becomes part of every statistic.** A metric declares its source population, eligibility, exclusions, earliest reliable period and completeness threshold; insufficient coverage produces no misleading number.
9. **An optional `PolicyThread / Mõjuteema` is added for Phase 2.** It groups several operational Matters around one enduring policy objective without replacing Matter as the unit of work.
10. **Advocacy outcome and attribution are separated.** Whether the result matches Koda's ask and how strongly Koda can claim influence are independent dimensions.
11. **History is simplified to three durable layers:** authored `Entry`, append-only `ChangeEvent`, and `SecurityAuditEvent`.
12. **Exact framework/runtime versions move to ADRs.** The master locks architectural characteristics and required capabilities, not incidental patch/minor versions.
13. **The three-part Matter workspace is retained deliberately:** `Ülevaade`, `Seisukoht ja kaasamine`, `Dokumendid`. The advocacy/consultation view remains lightweight in MVP because it is core Chamber work.
14. **The editor is simplified without becoming plain text.** MVP supports clean Word/Outlook paste, links, lists, emphasis and simple comparison tables.
15. **Dark mode remains the primary Koda CVI expression but the design system is dark-mode-first, not structurally dark-only.**

# 1. Executive specification

## 1.1 Product definition

Koda Õigusloome is a **legislative matter and advocacy management system**. It owns the authoritative policy record; it is not a generic CRM, project-management suite, word processor, email client, SharePoint replacement or analytics product.

The operational centre is a **Teema** (Matter): one identifiable legislative, regulatory, strategic, implementation or proactive policy process that Koda follows or influences.

A Teema brings together:

- ownership, stage, closure and `Järgmiseks`;
- narrative entries and authoritative change history;
- received and working documents, immutable evidence and extracted text;
- member consultation and its evidentiary basis;
- Koda's substantive position and one or more outbound `Submission` records;
- meetings, calls, hearings, working groups, coalition work and public advocacy entries;
- broad policy areas and controlled thematic tags;
- successor/related/implementation relationships;
- source provenance, visibility and auditability;
- later, concrete proposals, outcomes, attribution and work-victory review.

An optional Phase-2 **Mõjuteema / PolicyThread** may group several Matters around one durable policy objective that survives several drafts, procedures or years.

## 1.2 North star

> **The purpose is not to create cleaner administrative records. The purpose is to make Koda better at representing the interests of Estonian companies.**

The system must improve simultaneously:

1. **Daily execution** — lawyers immediately see what requires action, waiting or monitoring and can update work faster than in OneNote.
2. **Management control** — the department head can assign work, inspect the active portfolio, deadlines, gaps and dormant matters without a separate register.
3. **Institutional memory** — future staff can recover what Koda knew, why it took a position, what it submitted, what evidence existed and what happened later.
4. **Advocacy effectiveness** — after adoption is proven, Koda can structure concrete asks and outcomes without turning daily legal work into CRM administration.
5. **Search and reuse** — years of Estonian-language matters, submissions, entries and documents remain quickly retrievable even as the corpus grows substantially.

## 1.3 Product promise to users

The honest adoption promise is:

> **Enter the policy record once instead of maintaining the same matter in Excel and OneNote.**

Koda Õigusloome does not need to replace Word, Outlook, Teams, SharePoint or Smaily as general-purpose productivity tools. It must capture the authoritative outputs and evidence from those tools with less double work than today.

The system succeeds only when lawyers voluntarily stop maintaining Excel and OneNote as parallel registers for new matters because the new workflow is faster and safer.

## 1.4 Final architecture in one paragraph

Build a separate Dockerised Django modular monolith backed by PostgreSQL, using server-rendered HTML/HTMX and only small isolated JavaScript/TypeScript islands where they materially improve interaction. Use a supported Django release selected in an ADR and PostgreSQL **18 or later** at launch so the required Estonian stemming capability is available. PostgreSQL is the canonical application database and initially the search/statistics engine. Search operates through a rebuildable `SearchDocument` projection using Estonian full-text search, exact identifiers, tags/aliases and trigram matching. The database is canonical for document metadata; Azure Blob Storage is canonical for immutable evidentiary binaries; SharePoint may be linked as a collaborative working-document location where Office co-authoring is useful. Final/sent/received evidence is snapshotted to Blob with SHA-256 provenance. Use Microsoft Entra ID for any real-data pilot and production. Develop locally under Docker Compose with synthetic data, then run real-data pilot and production on approved Azure-managed infrastructure. Statistics derive from the same canonical records through versioned metric definitions and coverage rules; no separate warehouse or external search engine is introduced without measured need.

## 1.5 Alpha application decision

**LOCKED:** do not turn `Kauror/cheerful-control` into the long-lived production codebase.

The alpha remains valuable as:

- functional workflow evidence;
- a source of terminology and current operating rules;
- a reference for personal dashboards, active/inactive logic, matter chains, Excel parsing and date extraction;
- a regression oracle for reporting continuity.

It is **not** the visual or UX baseline. Its Lovable styling, layouts, snapshot-oriented database schema and component choices are not carried forward. The production interface is designed again from first principles in a dark-mode-first Koda CVI system.

Create a new production repository. Port proven behaviour and tests deliberately; do not copy the alpha's dataset/snapshot schema as the authoritative domain model.

## 1.6 Scope strategy

The build is intentionally split into four layers:

- **Adoption core:** Teema, one fast capture path, `Järgmiseks`, documents/evidence, `Submission`, thin consultation, search, Minu töö, Teemad and audit/security foundations.
- **Management layer:** portfolio views, reporting continuity, metric catalogue, data-quality indicators and trusted operational statistics.
- **Advocacy depth:** PolicyThread, proposal extraction/review, structured responses, outcome/attribution and work-victory review after operational adoption.
- **Historical/automation intelligence:** deep OneNote backfill, external monitoring, semantic search and AI after the canonical operating system is stable.

This sequencing is a product-quality decision. A narrow system the lawyers live in is more valuable than a complete system that is slower than OneNote.

## 1.7 Visual product direction

The production interface is designed anew using Koda's official CVI and a **dark-mode-first** design system. It must remain fast and legible with long Estonian titles, large tables, multi-year timelines and document-heavy matters.

The visual system must be modern without depending on transient visual fashions: strong typography, restrained color, clear status semantics, excellent focus states, dense but breathable tables, progressive disclosure, no decorative dashboard clutter and no dependency on hover-only interactions. Component tokens should permit a future accessible light theme without redesigning the product architecture.

# 2. Evidence reconciliation

## 2.1 Current operating system

The current workflow consists of three cooperating elements.

### Excel register

The workbook is the structured operational index. Current records include a unique year-based number, title, act/type, received date, opinion deadline, sent date, institution, responsible lawyer, two consultation-count fields, current stage and next action.

Direct inspection confirms important era differences:

- **2011–2017:** core register; counterparty field is `KELLELT`; no modern status/next-action model.
- **2018–2019:** two member-feedback count fields added; counterparty remains `KELLELT`.
- **2020–2022:** counterparty changes to `KELLELE`; two count fields remain.
- **2023–2024:** `HETKESEIS` appears; usage is sparse/inconsistent in early years.
- **2025:** `JÄRGMISEKS` exists and is partly populated; current structure is largely present.
- **2026:** current standardized operating structure and user guidance.

The counterparty change is semantic, not cosmetic: `KELLELT` means source/sender; `KELLELE` means recipient/addressee. The new system therefore has separate source and addressee organization relationships.

The two consultation count fields are independent observations. They are not guaranteed to share a compatible denominator/population; historical response rates must not be derived from them.

### OneNote notebook

OneNote is the detailed case file. Policy-area sections contain narrative chronology, incoming material, email and Office/PDF attachments, member consultation evidence, external views, Koda opinions, follow-up notes and public/process links.

The sampled pages show that one matter can span repeated consultation rounds, several procedural stages, several outbound submissions and successor topics. The notebook's policy sections are useful classifications, but they become policy areas/tags rather than exclusive folders.

### Existing alpha application

The alpha provides a read-only cross-year dashboard over Excel and links users back to Excel/OneNote for editing. It validates:

- department and personal portfolio views;
- active/inactive topic logic;
- deadline and attention logic;
- dense searchable/filterable tables;
- matter-chain display;
- extraction of a next review date from `Järgmiseks` free text;
- upload-based refresh and historical reporting snapshots.

These behaviours become product requirements. The manual upload and external editing loop do not.

## 2.2 OneNote-link quality

The discovery sample found 14 compatible/related OneNote targets and 11 mismatches among 25 inspected workbook-targeted links. The sample is too small to estimate the population error rate, but it proves one critical rule:

**A legacy hyperlink is evidence, not a trustworthy primary key.**

Historical joins preserve raw identifiers and use deterministic IDs/tokens, content signals and human review where necessary.

## 2.3 Reconciled review decisions

### Hetkeseis vocabulary

**Earlier tension:** the validator and current workbook differed.

**Version 1.2:** the current workbook/user guide remain authoritative for legacy labels; canonical stage/closure mapping is reviewed with lawyers in Stage 0.

### OneNote mismatch rate

**Earlier tension:** the 11/25 discovery sample risked being treated as population quality.

**Version 1.2:** use it only as a risk signal; root-cause and stratify before backfill.

### Formal opinions

**Earlier tension:** the earlier model allowed one Matter-level sent date until Phase 2.

**Version 1.2:** `Submission` is MVP and canonical. One Matter may have several submissions.

### Historical rows

**Earlier tension:** v1.1 introduced `LegacyRegisterRecord` beside Matter archive mode.

**Version 1.2:** remove the duplicate business model. Historical rows are `Matter(record_mode=ARCHIVE)` with provenance.

### Document storage

**Earlier tension:** v1.1 made Blob canonical and SharePoint a one-way archive.

**Version 1.2:** hybrid by lifecycle: Blob = immutable evidence; SharePoint = optional mutable working document; DB = canonical metadata. No default mirror tree.

### Real-data pilot

**Earlier tension:** pilot and production-grade controls were separated too late.

**Version 1.2:** Secure Pilot Gate before real departmental data.

### Järgmiseks

**Earlier tension:** one target date risked treating waiting/monitoring as ordinary deadlines.

**Version 1.2:** add `DO`, `WAIT`, `MONITOR` plus `DEADLINE`, `REVIEW_ON`, `EXPECTED_AROUND`.

### Search

**Earlier tension:** the PostgreSQL family was correct but tightly coupled to domain tables.

**Version 1.2:** add rebuildable `SearchDocument` projection; external engine only after measured failure.

### Tag scope

**Earlier tension:** tags risked mixing topics, sectors, instruments and impacts.

**Version 1.2:** PolicyArea = broad domain; Tag = controlled thematic concept; sector/instrument remain separate structured dimensions when needed.

### Advocacy impact

**Earlier tension:** result and causal confidence were partially conflated.

**Version 1.2:** separate outcome match from attribution strength; work victory requires a separate reviewed decision.

### Multi-year advocacy

**Earlier tension:** Matter relationships can become hard to navigate for enduring objectives.

**Version 1.2:** add optional Phase-2 `PolicyThread / Mõjuteema`; Matter remains operational unit.

### Matter page tabs

**Earlier tension:** the second review suggested collapsing to two tabs.

**Version 1.2:** retain `Ülevaade`, `Seisukoht ja kaasamine`, `Dokumendid`; keep the middle tab intentionally lightweight in MVP.

### Rich text

**Earlier tension:** the second review warned against editor complexity.

**Version 1.2:** use a constrained editor but preserve lists, links, clean Word/Outlook paste and simple comparison tables.

### Runtime versions

**Earlier tension:** v1.1 marked exact framework versions as permanent decisions.

**Version 1.2:** the master locks capabilities/architecture; exact supported versions live in ADRs/lockfiles.

# 3. Product principles

## 3.1 One canonical Teema

Every operational or historical legislative/policy process is represented by one canonical `Matter`. Dashboards, deadline lists, archive views and statistics are projections of the same records, not parallel data models.

## 3.2 One fast capture path

A lawyer must not choose among separate note, meeting, document, activity and next-action screens for a routine update. The `Sissekanne` composer accepts text, files, optional activity enrichment and an optional `Järgmiseks` update in one atomic save.

Formal outbound written advocacy is represented by `Submission`, not buried as a generic Entry.

## 3.3 Structured where valuable; free text where reality demands it

Use structured fields for ownership, institutions, stage, visibility, formal submissions, dates, relationships, consultation counts and documents. Preserve fast narrative entries for reasoning, nuance and unpredictable developments.

## 3.4 Stage, action, closure and outcome are separate

- `Hetkeseis` answers: **Where is the external process?**
- `Järgmiseks` answers: **What does Koda do/wait for/monitor next?**
- action date semantics answer: **Is this a real deadline, a review date or an expected-around date?**
- closure/disposition answers: **Why is Koda no longer actively working on the Matter?**
- proposal outcome answers: **How closely did the eventual result match Koda's ask?**
- attribution answers: **How strongly can Koda support a claim of influence?**

No overloaded status field should carry all of these meanings.

## 3.5 Evidence before claims

No accepted proposal, influence claim or `töövõit` exists without linked evidence and an explicit review state. The system must preserve uncertainty rather than manufacture causal certainty.

## 3.6 Original documents are permanent evidence

Original bytes are immutable once stored as evidence. Searchable text, previews, OCR and embeddings are derivatives. A mutable SharePoint working draft is not itself immutable evidence until a specific version is captured to Blob.

## 3.7 Own the policy record, not every productivity tool

Koda Õigusloome should integrate with Word, Outlook, SharePoint, Teams, Smaily and official systems where useful, but it must not rebuild them. It owns the authoritative policy record: Matter, evidence, position, submissions, interactions, next action, relationships and later proposals/outcomes.

## 3.8 Simple by default, detail on demand

Interactive Matter creation requires only a title. Optional fields appear contextually. Completeness is driven through views and manager/data-quality indicators rather than large mandatory forms.

## 3.9 Fast at scale

All large collections use server-side filtering, indexed ordering and pagination/cursor patterns appropriate to the query. No screen loads the complete history/document corpus merely to render a list.

## 3.10 Estonian-first product language

UI terminology is Estonian and reuses the department's familiar language where it is semantically sound: `Teema`, `Hetkeseis`, `Järgmiseks`, `Kaasamine`, `Tagasiside`, `Arvamus`/`Submission`, `Sissekanne`, `Töövõit`.

## 3.11 Boring infrastructure, explicit seams

Prefer one application, one relational database and standard managed storage. Keep explicit seams for storage, search projection, authentication and external integrations so infrastructure can change without rewriting domain logic.

## 3.12 New dark-mode-first CVI interface

Lovable is not the visual baseline. The production design begins from Koda's official CVI, accessibility, dense-data needs and observed workflows. Dark mode is primary; component tokens must not make a future accessible light mode prohibitively expensive.

## 3.13 Controlled taxonomy and tags

`PolicyArea` is a small stable reporting dimension. `Tag` is a governed thematic/search concept with aliases and merge/deprecation rules. Tags never replace stage, institution, legal instrument, owner, visibility or dates. User-created free-form tag sprawl is prohibited.

## 3.14 One source of truth, multiple statistical views

Operational dashboards and statistics derive from canonical records. Every metric defines its eligible population, coverage and source boundary and drills through to the exact underlying Matters/records the viewer is authorized to see.

# 4. Product goals and acceptance outcomes

## Goal A — replace Excel for new operational work

The application becomes the canonical register for every new Matter, its owner, stage, next action, response deadline, submissions, relationships and status history.

**Acceptance outcome:** no new Matter requires parallel entry into the Excel register after cutover; the app produces the reporting export needed by existing management analytics.

## Goal B — replace OneNote for new case work

The Matter page becomes the faster place to capture developments, files, rationale, consultation evidence and follow-up.

**Acceptance outcome:** on real lawyer workflows, create ≤30 seconds; routine update with note/file/next action ≤30 seconds and ≤6 deliberate interactions; no draft loss; users no longer maintain new Matter histories in OneNote.

## Goal C — maintain management and reporting continuity

The department head can see the active portfolio, actual deadlines, DO/WAIT/MONITOR queues, formal submissions and data-quality gaps without maintaining another spreadsheet.

**Acceptance outcome:** the new export contract runs in parallel with the current reporting feed and reaches zero unexplained differences for agreed fields before cutover.

## Goal D — create searchable institutional memory

Search retrieves Matters, submissions, entries, organisations, tags, references and document text in Estonian with source context.

**Acceptance outcome:** a maintained real-query corpus passes agreed precision/recall expectations; known Matters open quickly even as the corpus grows.

## Goal E — make advocacy measurable without creating bureaucracy

After operational adoption, structured proposals/outcomes are added through low-friction review, preferably AI-assisted extraction from finished submissions rather than manual retyping.

**Acceptance outcome:** outcome reporting shows coverage and evidence; no “win rate” or causal claim is calculated from unreviewed or incomplete records.

## Goal F — remain operable for years

A future maintainer can restore data, understand the schema, replace integrations and upgrade supported framework/database versions without reconstructing undocumented assumptions.

**Acceptance outcome:** architecture decisions, migration provenance, restore procedures, tests and export contracts are stored with the code/system and exercised.

## Goal G — provide trustworthy statistics without creating a second reporting system

Authorized users can understand active inventory, deadlines, submissions, subject distribution, consultation activity, data quality and—later—reviewed advocacy outcomes from the canonical database.

**Acceptance outcome:** every metric has a versioned definition and coverage rule, respects authorization, visibly separates native versus legacy evidence where needed and drills through to the exact underlying records.

# 5. Users, roles and authorization

## 5.1 Primary roles

- **Spetsialist / jurist:** create/edit Matters; add entries/documents; manage assigned work, consultation and submissions; search authorized history.
- **Osakonnajuht:** specialist capabilities plus assignment/reassignment, portfolio/data-quality views, closure review and later impact/work-victory review.
- **Süsteemiadministraator:** user/role administration, reference data, imports/exports, integrations, health and technical support. Does **not** automatically receive business access to restricted content.
- **Lugeja** *(later/optional):* read authorized Matters/reports without editing, e.g. management or communications where approved.

External member companies are not application users in MVP.

## 5.2 Visibility model

MVP has two visibility scopes:

- `NORMAL` — visible to authorized department users;
- `RESTRICTED` — visible only to the Matter owner, explicit collaborators and configured business roles such as department head.

The Matter sets the default. Child records inherit that visibility automatically. A child may be **more restrictive** than its parent but may never become less restrictive than the effective parent scope.

All reads pass through one centralized authorization boundary, conceptually `scope_for_user(user)`, including:

- lists and detail pages;
- search projection/results/snippets;
- dashboard counts and metric drill-through;
- exports;
- document/derivative downloads;
- AI retrieval.

Technical administration is separate from restricted-content business access. Emergency support requiring restricted content uses explicit, time-bounded, audited break-glass access rather than permanent administrator visibility.

## 5.3 Authentication and environment gates

### Local development

- local development accounts permitted only on isolated developer environments;
- synthetic/non-confidential fixtures only;
- custom User still reserves immutable Entra object ID from migration 0001;
- no production/member-confidential database copy on developer laptops or home server.

### Secure Pilot Gate — before first real departmental data

Real current Koda work may enter only an approved environment with at minimum:

- HTTPS/TLS;
- Microsoft Entra ID or equivalently approved identity authentication;
- centralized authorization and restricted-record tests;
- approved encrypted PostgreSQL and document storage;
- secret management;
- upload type/size controls and malware scanning/quarantine path;
- successful backup and restore test;
- retention decision for raw email/member feedback;
- controlled developer/support access.

### Production

Production adds/locks:

- Entra OIDC with MFA/Conditional Access as required by Koda;
- no local-password fallback;
- offboarding test;
- second break-glass administrator/continuity path;
- production monitoring, security review and recovery runbooks.

# 6. Koda advocacy operating model

The system models how a Chamber represents member interests, not merely how a bill moves through government.

## 6.1 Sources of a Matter

A Teema may begin from:

- a ministry, government or Riigikogu request;
- EIS or another official consultation channel;
- an EU consultation, proposal, strategy or implementation need;
- a Koda member's problem or proposal;
- an internal proactive initiative;
- a standing working group or committee;
- another business association/chamber;
- an implementation, court, authority or market development;
- a previous Matter that creates a successor process.

Source institution and eventual addressee are separate facts.

## 6.2 Advocacy lifecycle

```text
Signal / incoming proposal / member problem
        ↓
Triage + assignment
        ↓
Analysis + evidence gathering
        ↓
Member/expert consultation where useful
        ↓
Koda position formation
        ↓
One or more submissions and/or meetings/hearings/coalition actions
        ↓
Monitoring government / Riigikogu / EU / implementation
        ↓
Observed result, successor process, closure and reuse
```

This is not a rigid workflow engine. Steps may repeat, reorder or be skipped.

## 6.3 Member evidence

Member consultation is both workflow and evidence. Preserve:

- broad vs targeted audience;
- independently entered contacted/response counts;
- population/basis notes where known;
- named organization-level responses when appropriate;
- conflicting positions and lack of consensus;
- documents/emails/surveys/summaries;
- how the evidence informed the position.

Legacy counts never create a response-rate metric. Native response rate may be calculated later only when contacted and response populations are explicitly compatible and coverage is sufficient.

## 6.4 Position, rationale and submissions

Distinguish:

- **Seisukoht** — Koda's substantive position on the Matter;
- **Põhjendus** — the important rationale/evidence behind that position;
- **Submission** — one outbound written advocacy action communicating some or all of the position;
- **Ettepanek** — one concrete ask, introduced structurally in Phase 2;
- **Sissekanne** — narrative work such as meeting, call, hearing or note;
- **Tagasiside** — external/member evidence, structurally deepened later.

One Matter may have zero, one or many Submissions:

- formal opinion;
- supplementary opinion;
- joint letter;
- parliamentary submission;
- informal written response;
- other written submission.

Absence of a sent Submission does not mean missing work. A Matter may validly conclude with monitoring only, no position due to lack of consensus, immaterial/no-action decision, authority withdrawal or continuation under a successor Matter.

## 6.5 Joint and international advocacy

MVP Submission supports recipient organizations and optional joint submitters. Entries can represent meetings/hearings/coalition work. Phase 2 may add a standing `WorkingGroup` only if repeated retrieval across Matters proves valuable.

EU work must support explicit relationships from EU initiative → Estonia's position → EU act → national implementation/transposition without forcing all steps into one Matter.

## 6.6 Multi-year policy objectives

Matter remains the operational unit. Some Koda objectives continue through several distinct procedures over many years. Phase 2 therefore permits optional `PolicyThread / Mõjuteema`:

- one enduring policy objective;
- current Koda objective/rationale;
- owner/status;
- linked Matters;
- policy areas/tags.

It is not mandatory and is not another workflow engine.

## 6.7 Outcomes, attribution and work victories

For a structured `Proposal`, track two independent axes.

### Outcome match

- `ADOPTED`;
- `SUBSTANTIALLY_ADOPTED`;
- `PARTLY_ADOPTED`;
- `REJECTED`;
- `WITHDRAWN`;
- `UNRESOLVED`;
- `UNKNOWN`.

### Attribution strength

- `NO_ATTRIBUTION_CLAIM`;
- `CONSISTENT_WITH_KODA_POSITION`;
- `SHARED_OR_CONTRIBUTORY_EVIDENCE`;
- `DIRECT_EVIDENCE`.

A result may match Koda's position without evidence that Koda caused it, and Koda may have strong influence evidence for a compromise that only partly matches the original ask.

`Töövõit` is a separate reviewed judgment, not an automatic consequence of either axis.

## 6.8 Chamber work mapped to product capabilities

- **Early warning/proactive proposal:** Matter may start without an incoming authority document.
- **Several letters during one procedure:** multiple MVP `Submission` records under one Matter.
- **Targeted/broad member consultation:** thin `Kaasamine`, independent counts and evidence attachments; deeper Responses later.
- **No member consensus:** explicit no-position closure/disposition and rationale.
- **Meetings/calls/hearings:** fast typed `Sissekanne`; structured enrichment optional.
- **Joint letters:** Submission joint-submitter organizations.
- **Monitoring after sending an opinion:** Matter remains active with stage + DO/WAIT/MONITOR next action.
- **EU → national implementation chain:** typed Matter relationships.
- **Repeated objective across years:** optional Phase-2 PolicyThread.
- **Annual opinion counts:** derived from `Submission`, not number of Matters.
- **Demonstrating influence:** Proposal outcome + attribution + evidence + reviewed WorkVictory.
- **Reusing previous positions:** search across Matters, Submissions, Entries, tags and document derivatives.

The system supports advocacy without becoming a generic public-affairs CRM.

# 7. Information architecture and navigation

## 7.1 MVP navigation

```text
Minu töö
Saabunud
Teemad

[global search / Ctrl+K]

Juhtimine   (manager only)
Admin       (admin only)
```

Later `Statistika` may appear as a manager/authorized workspace once metric coverage is trustworthy. `Mõju / töövõidud` is Phase 2. `Dokumendid` is never a top-level file browser; documents are reached through a Matter or search.

## 7.2 Minu töö

Purpose: answer **What must I do, wait for or monitor now?**

Group by action semantics rather than one mixed due-date list:

### Teen

- overdue real deadlines;
- due today;
- next seven days.

### Ootan / kontrollin

- WAIT actions whose review date has arrived;
- MONITOR actions scheduled for review;
- expected-around developments needing attention.

### Minu aktiivsed teemad

Active Matter inventory sorted primarily by current next action/date and recent meaningful activity.

Key indicators are clickable filters, not decorative cards:

- arvamusi/submissions koostamisel;
- aktiivseid teemasid;
- real deadlines;
- review/monitor queue;
- tähelepanu vajavad;
- without next action;
- unassigned for managers.

Matter count is called **inventory/portfolio**, not workload/productivity.

## 7.3 Saabunud

Purpose: low-friction triage.

### Manual/email intake in MVP

A visible drop target accepts `.msg`, `.eml` and source documents. The system may propose:

- title;
- sender/source organization;
- received date;
- attachments;
- official reference;
- response deadline;
- possible existing related Matter.

Proposals require user confirmation. Create a Matter with minimal fields; do not require a long form.

### Machine intake later

When EIS or another feed is automated, noisy/unreviewed items use a separate derived/queue object such as `IntakeItem`. It becomes a Matter only after human acceptance. Do not create a generic intake framework before an automated feed exists.

## 7.4 Teemad list

The main list preserves the useful Excel mental model but removes spreadsheet maintenance.

Default columns:

1. reference;
2. title;
3. stage;
4. owner;
5. next action;
6. action/deadline date;
7. last meaningful activity.

Filters include owner, stage, action kind, track, policy area, tag, organization, date, record mode and visibility as authorized.

Start with standard presets and URL-persisted state. A generalized user-saved-view framework is deferred until real usage proves the need.

Rows open a fast Matter detail view/page without losing list context.

## 7.5 Juhtimine

Purpose: actionable department control, not executive decoration.

Initial manager views:

- unassigned incoming Matters;
- active inventory by owner;
- real deadlines in 7/30 days;
- WAIT/MONITOR reviews due;
- Matters without next action;
- stale Matters excluding intentionally dormant WAIT/MONITOR cases;
- submission activity;
- data-quality gaps;
- reporting reconciliation status during cutover.

Every count drills through to the exact authorized records.

## 7.6 Global search

A globally visible search field is available from every page. `Ctrl+K` initially focuses/opens search; a broad command palette is deferred.

Search should immediately understand references, official IDs, title words, organization aliases, tags/aliases and document/entry text, and show **why** a result matched.

## 7.7 Statistika workspace (PHASED)

When metric definitions and coverage are trustworthy, statistics provide drillable views for:

- work/deadlines;
- portfolio/subjects;
- submissions;
- consultation;
- data quality;
- later, reviewed proposals/outcomes/impact.

Charts never replace underlying lists. Every metric shows its population/coverage and opens the eligible Matter/Submission/etc. set.

# 8. Teema page specification

## 8.1 Layout

The Matter page remains deliberately simple:

```text
Ülevaade | Seisukoht ja kaasamine | Dokumendid
```

`Ülevaade` is the default working surface and chronology. `Seisukoht ja kaasamine` is retained in MVP because member evidence and outbound position formation are central Chamber work, but it is lightweight until Phase 2. `Dokumendid` separates evidence from collaborative working documents.

## 8.2 Header

Always visible:

- Matter reference + title;
- stage;
- owner/collaborators;
- source/addressee organization when important;
- response deadline if active;
- current `Järgmiseks` with action kind and date semantics;
- restricted indicator;
- quick actions: assign, stage, next action, add Submission/document/relationship as authorized.

Long Estonian titles wrap cleanly and never push the working content below an oversized hero header.

## 8.3 Unified Sissekanne composer

The composer is the primary adoption feature.

Default state: one text box plus obvious attach and `Järgmiseks` controls.

One atomic save may create/update:

- one Entry;
- one or more evidence file uploads;
- optional activity enrichment (meeting/call/hearing, organization);
- the current NextAction.

Keyboard submit uses Ctrl/Cmd+Enter; every shortcut has a visible click path. Draft text survives accidental navigation/escape according to tested product behaviour.

The constrained rich-text schema supports:

- paragraph text;
- bold/italic/underline where needed;
- two heading levels;
- bullets/numbered lists;
- links;
- simple tables for comparison content;
- clean Word/Outlook paste with unsupported styling stripped;
- pasted images converted into document attachments.

Do not build a general document editor.

Formal outbound written advocacy is created as `Submission`, not as a generic Entry. A quick `Lisa väljasaadetud arvamus / kiri` flow may be reachable from the composer or position tab but writes the Submission entity.

## 8.4 Ülevaade

The page body is the professional timeline plus a compact “current state” summary.

Display:

- current next action;
- recent meaningful entries;
- selected ChangeEvents (stage, assignment, submission sent, closure, relationship, document version);
- key additional dates;
- related/successor Matters;
- optional pinned rationale/current position summary.

Newest-first timeline with collapsible month/year groups. Routine field-edit noise is hidden by default.

## 8.5 Seisukoht ja kaasamine

MVP contains:

### Koda seisukoht

- concise substantive position;
- optional rationale/evidence summary;
- supporting links to Entries/Documents/Consultations.

### Kaasamised

- consultation period;
- audience/selection summary;
- contacted and response counts, independently displayed;
- population/basis note where known;
- attached outreach/response evidence;
- external Smaily/campaign reference.

### Väljasaadetud / submissions

List all outbound Submissions for the Matter with kind, recipient(s), status, sent time and immutable final evidence version where sent.

Phase 2 adds Proposal/outcome review and structured Response without making them prerequisites for daily work.

## 8.6 Dokumendid

Documents are grouped by lifecycle/role rather than one flat folder.

### Evidence

Immutable Blob-backed versions such as:

- incoming authority documents;
- original emails;
- member feedback evidence;
- external positions;
- final sent Koda submissions;
- outcome/legal-process evidence.

### Working documents

Optional SharePoint-linked mutable Office files, clearly labelled `Töödokument — SharePoint`, with site/drive/item identity and observed version metadata.

When a Submission is marked sent or a working document becomes evidence, the exact relied-upon binary is snapshotted to Blob and linked as an immutable DocumentVersion.

The app never pretends a mutable working link is immutable evidence.

## 8.7 Right rail / facts panel

Use only for compact facts that support the main work surface:

- key additional dates;
- track/policy areas/tags;
- organizations;
- relationships;
- provenance/source links;
- record mode/data-quality indicator for imported records.

Avoid a permanent wall of every database field. Less common metadata is progressively disclosed.

# 9. Functional requirements

## 9.1 Adoption-core requirements

MVP / pre-cutover must provide:

- Teema create/read/update/close/reopen;
- owner/collaborator assignment;
- stage + closure/disposition;
- one primary NextAction with DO/WAIT/MONITOR and date semantics;
- received date and response/opinion deadline;
- fast Entry composer with attachments and next-action update;
- formal/informal outbound `Submission` with multiple records per Matter;
- immutable evidence DocumentVersion and optional SharePoint working-document link;
- extracted-text derivative hooks and search indexing;
- thin Consultation with independent counts and basis/provenance;
- Organisation, PolicyArea and controlled Tag assignment;
- simplified Matter relationships;
- external official references;
- MatterSourceReference/import provenance;
- Minu töö, Saabunud, Teemad, global search and manager operational views;
- ChangeEvent and SecurityAuditEvent;
- visibility inheritance + break-glass admin model;
- reporting export compatibility;
- backup/restore and secure-pilot controls before real data.

## 9.2 Phase-2 advocacy requirements

Add only after the adoption core is demonstrably used:

- optional `PolicyThread / Mõjuteema`;
- AI-assisted proposal extraction from finished Submissions with human confirmation;
- structured Proposal outcome + attribution evidence;
- organization-level Response where consultation evidence justifies it;
- StakeholderPerson only if professional-contact continuity proves valuable;
- WorkingGroup only if standing membership/representation requires cross-Matter retrieval;
- WorkVictoryReview and impact reporting;
- lightweight DecisionRecord only after real approval rules are agreed;
- advanced statistics based on reviewed/native data.

There is **no separate richer Opinion entity** by default; `Submission` is the durable outbound-document/action object. Add another entity only if real workflow cannot be represented cleanly.

## 9.3 Later automation requirements

Potential later capabilities:

- EIS/Riigikogu/Riigi Teataja/EUR-Lex monitoring;
- Outlook capture integration;
- bulk historical OneNote review/backfill;
- scheduled reminders/monitoring jobs;
- extraction/OCR at scale;
- AI summaries and previous-position retrieval;
- proposal extraction;
- related-Matter suggestions;
- semantic search via pgvector or another measured need;
- external search engine only if PostgreSQL search fails agreed performance/quality thresholds.

# 10. Explicit non-goals

Unless later evidence materially changes the case, do **not** build:

- generic CRM/public-affairs stakeholder database;
- generic project/task-management platform;
- generic BPM/workflow designer;
- custom Word/document editor;
- Outlook/email client;
- Smaily/mass-mailing replacement;
- SharePoint/Teams replacement;
- native mobile app/offline client;
- React SPA or separate frontend/backend codebases by default;
- microservices or Kubernetes;
- Redis/Celery/RabbitMQ before measured need;
- Elasticsearch/OpenSearch before measured search failure;
- separate analytics warehouse/BI cube at the expected scale;
- deep arbitrary tag hierarchy or uncontrolled free-form tags;
- full Office round-trip/WOPI platform unless pilot evidence justifies it;
- autonomous AI changing canonical records or sending external messages;
- automated causal “Koda influence score” or work-victory claim;
- full historical reconstruction before operational cutover;
- per-field permission designer;
- configurable workflow states per Matter type;
- productivity/employee scoring from Matter counts or submission volume.

# 11. Canonical domain model

## 11.1 Modeling conventions

- Internal primary keys use time-sortable UUIDs where supported by the chosen PostgreSQL/Django implementation; human references remain stable and separate.
- Human references retain the familiar immutable `YYYY_N` convention unless Stage-0 business review changes it.
- Code/table/field names are English; user-facing terminology is Estonian.
- Exact framework/database/runtime versions live in ADRs and dependency locks, not as permanent domain invariants.
- Imported raw values are immutable; normalized interpretations are reviewable with provenance.
- All meaningful state changes pass through named application-service functions inside database transactions.
- A fact has one canonical storage location. Derived compatibility fields/views are explicitly derived, not independently editable.

## 11.2 MVP entities

### User

Purpose: authenticated application identity.

Core fields:

- internal ID;
- immutable Entra object ID field (nullable only in isolated dev fixtures);
- UPN/email + display name;
- active flag;
- application role(s);
- created/last-login metadata.

A custom user model exists from migration 0001.

### Matter (`Teema`)

Purpose: canonical operational and historical policy process.

Core fields:

- internal ID;
- `reference_year`, `reference_number`, display reference;
- title + alternate/search titles;
- `record_mode`: `FULL` or `ARCHIVE`;
- `origin`: native, legacy import, promoted legacy, other;
- data-quality/completeness tier;
- source organization FK, nullable;
- addressee organization FK, nullable;
- owner + collaborators;
- track enum;
- PolicyArea M2M;
- controlled Tag assignments;
- stage FK, nullable;
- open/closed + closure/disposition + reason/actor/time;
- received date, nullable;
- response/opinion deadline, nullable;
- concise position summary, nullable;
- rationale/evidence summary, nullable;
- current primary NextAction pointer;
- visibility (`NORMAL`/`RESTRICTED`);
- stable reporting/source-year identity;
- legacy/provenance flags;
- created/updated metadata.

**No canonical `opinion_sent_date` is stored on Matter.** Outbound written advocacy lives in `Submission`; compatibility/report exports derive a sent date using a versioned rule.

Only title is required at interactive creation. Archive imports may have many modern fields null.

### StageVocabulary

Purpose: code-managed procedural-stage reference data.

Seed from the reviewed current workbook vocabulary after separating closure/outcome semantics. Each row has key, Estonian label, help text, active flag and optional track applicability. No arbitrary admin create/delete workflow.

`rohkem pole tegevusi plaanis` remains an imported raw label but maps to closure/disposition rather than procedural stage.

### Track enum

Broad context, not a workflow engine:

- domestic;
- EU initiative;
- national transposition;
- strategy/development plan;
- proactive Koda initiative;
- implementation/enforcement;
- other.

### Closure / disposition

Initial values include:

- completed/entered into force;
- superseded by successor Matter;
- authority stopped/withdrew initiative;
- Koda stopped monitoring;
- no position formed/no consensus;
- response/submission finished and follow-up complete;
- duplicate/merged;
- other.

Closure stores reason, actor and timestamp and is reversible through an audited action.

### Entry (`Sissekanne`)

Purpose: fast authored professional chronology.

Core fields:

- Matter FK;
- author;
- kind;
- occurred-at distinct from created-at;
- constrained rich-text body;
- optional Organisation FK;
- optional Consultation FK;
- effective visibility (inherits Matter; may be more restrictive);
- edit-version/soft-delete metadata.

Initial kinds:

- note;
- meeting;
- call;
- hearing;
- working-group activity;
- public statement/communication;
- other.

Formal outbound written submissions are **not** Entry kinds.

### ChangeEvent

Purpose: append-only authoritative business change history.

Examples:

- Matter created/assigned;
- stage changed;
- next action changed/completed;
- Submission sent/withdrawn;
- evidence version added;
- relationship created;
- Matter closed/reopened;
- selected operational field changed.

The professional timeline renders selected ChangeEvents together with Entries. Do not create a separate fourth field-history subsystem. Sensitive free-text payloads are referenced/versioned rather than redundantly copied into event records where avoidable.

### SecurityAuditEvent

Purpose: security/compliance trace separate from the professional timeline.

Includes references/metadata for:

- restricted access where required;
- downloads;
- exports;
- role/permission/break-glass actions;
- imports/administrative operations;
- authentication/security events.

Normal application code cannot update/delete audit rows.

### NextAction

Purpose: exactly one prominent current operational instruction plus history.

Fields:

- Matter FK;
- text;
- owner;
- `kind`: `DO`, `WAIT`, `MONITOR`;
- `date_semantics`: `DEADLINE`, `REVIEW_ON`, `EXPECTED_AROUND`;
- target date, nullable;
- precision/source: exact, month, quarter, half-year, year, inferred;
- raw/source text when parsed from natural language;
- status: open/completed/cancelled/superseded;
- created/completed metadata.

Only `DO + DEADLINE` becomes automatically overdue. WAIT/MONITOR become due for review without being described as missed obligations.

### Submission

Purpose: one outbound Koda written advocacy action under a Matter.

Core fields:

- Matter FK;
- `kind`: formal opinion, supplementary opinion, joint letter, parliamentary submission, informal written response, other;
- title;
- status: draft, sent, withdrawn, superseded;
- recipient Organisation(s);
- optional joint-submitter Organisation(s);
- sent timestamp;
- channel/reference metadata;
- optional SharePoint working-document relationship;
- immutable final/sent `DocumentVersion` when status is `sent`;
- visibility inherited from Matter;
- provenance + created/updated metadata.

One Matter may have many Submissions. Opinion/submission-volume statistics query this entity.

### AdditionalDate (`MatterDate`)

Purpose: important dates **not already canonical elsewhere**.

Examples:

- meeting/hearing;
- government decision;
- adoption;
- publication;
- entry into force;
- planned external milestone.

Do not duplicate Matter.received date, Matter.response deadline, Submission.sent_at or NextAction target date as separate editable AdditionalDate facts.

Store type, value/precision, source and optional evidence reference.

### Document

Purpose: logical artifact identity and role.

Fields:

- Matter FK;
- optional Entry/Consultation/Submission relationship;
- role/category;
- title;
- current evidence-version pointer, nullable;
- optional working-document metadata/location;
- effective visibility;
- provenance;
- retention/legal-hold metadata;
- created metadata.

A logical Document may have both a mutable SharePoint working reference and immutable Blob evidence versions, but the UI must distinguish them.

### DocumentVersion

Purpose: exact immutable evidentiary binary.

Fields:

- Document FK + version sequence;
- Blob/file storage key;
- original filename;
- MIME type + size;
- SHA-256;
- uploader/acquisition time;
- source path/URL/identifier;
- optional source SharePoint item/version metadata;
- malware/validation state;
- extraction/indexing state;
- created metadata.

Binary bytes never mutate; correction creates a new version.

### DocumentDerivative

Purpose: rebuildable derivative tied to one exact DocumentVersion.

Fields:

- DocumentVersion FK;
- derivative type: extracted text, safe preview, OCR text, thumbnail, later embedding input;
- generator/parser and version;
- derivative hash/storage reference;
- page/section/source locators where available;
- status/error;
- created timestamp.

This is the basis for trustworthy full-text search and future source-cited AI.

### Consultation (`Kaasamine`)

Purpose: thin first-class member/expert outreach record.

Fields:

- Matter FK;
- title/summary;
- opened/closes dates;
- contacted count, nullable;
- response count, nullable;
- contacted-population/basis note, nullable;
- response-population/basis note, nullable;
- count provenance: legacy imported, native manual, later computed;
- audience/selection summary;
- external campaign/Smaily reference;
- effective visibility;
- created/updated metadata.

Legacy counts remain independent. Native response rate is computed only if the metric definition proves compatible populations and sufficient completeness.

### Organisation

Purpose: companies, associations, ministries, authorities, chambers and other institutions.

Fields:

- name + normalized search name;
- registry code/official identifier where known;
- type;
- valid-from/valid-to;
- predecessor/successor links;
- aliases/historical names;
- source reference/member reference where applicable.

Reorganizations are never auto-merged merely because names look similar.

### PolicyArea

Purpose: small stable broad classification, replacing exclusive OneNote folders and supporting reporting.

A Matter may have several PolicyAreas.

### Tag (`Silt`)

Purpose: governed thematic/search concept.

Fields:

- stable key;
- Estonian display name;
- short definition/usage guidance;
- active/deprecated state;
- optional `merged_into`;
- aliases/synonyms/historical terminology;
- governance metadata.

MVP taxonomy is shallow. Tags represent concepts such as `halduskoormus`, `käibemaks`, `VKE`, `AI` or `kestlikkusaruandlus`.

Tags do **not** represent owner, stage, institution, date, confidentiality, legal instrument type or workflow. Sector is not forced into Tag; if later required for analytics it becomes a controlled `AffectedSector` dimension compatible with Chamber/EMTAK needs.

### TagAssignment (`MatterTag`)

Purpose: confirmed association between Matter and Tag.

Fields:

- Matter FK;
- Tag FK;
- source: manual, imported, approved rule;
- reviewer/confirmation metadata;
- timestamps.

AI suggestions are not canonical assignments until a human accepts them. A proposed tag may live transiently in a suggestion/review queue rather than polluting `TagAssignment`.

### MatterRelationship

Purpose: direct typed links among operational Matters.

Initial types only:

- `SUCCESSOR_OF`;
- `RELATED_TO`;
- `IMPLEMENTS_OR_TRANSPOSES`;
- `DUPLICATE_OF`.

Inverse directions are generated/inferred rather than separately maintained. Add a new relation type only after several real examples cannot be expressed by these.

### ExternalReference

Purpose: link a Matter to authoritative external systems/pages.

Types include EIS, Riigikogu, Riigi Teataja, EUR-Lex, ministry registry, Koda page and other. Store external ID, URL, observed/retrieved timestamp and optional metadata. Never present a static link as synchronized unless an integration actively maintains it.

### MatterSourceReference

Purpose: immutable migration/provenance evidence.

Fields include source system, workbook file/sheet/row/raw values, OneNote IDs/URLs, source title/date, match method/confidence/conflict state, import batch and source snapshot hash/reference.

### ImportBatch

Purpose: reproducible migration/import run identity.

Store source snapshot hashes, importer version, mapping/contract version, run time, counts and reconciliation status.

## 11.3 Derived technical projection

### SearchDocument

Purpose: rebuildable PostgreSQL projection for search, not authoritative business data.

Fields conceptually include:

- Matter ID;
- source kind + source object ID;
- title/short metadata;
- normalized identifiers;
- searchable body text;
- Estonian/simple tsvectors;
- policy/tag/organization aliases useful for ranking;
- effective visibility scope;
- source locator for result snippets;
- updated/index version.

It can always be rebuilt from canonical records/derivatives and later feed an external search engine if measured requirements demand one.

## 11.4 Phase-2 entities

### PolicyThread (`Mõjuteema`)

Optional durable container for one substantive objective spanning multiple Matters.

Fields: title, current Koda objective, rationale/background, owner, status, policy areas/tags and linked Matters.

Not required for ordinary Matters and not a workflow engine.

### Proposal (`Ettepanek`)

Purpose: one concrete Koda ask.

Required initially: Matter FK, title/ask text, created/source metadata.

Prefer AI-assisted extraction from finalized Submissions followed by human confirmation rather than manual retyping.

Outcome fields/review may include:

- outcome match: adopted/substantially adopted/partly adopted/rejected/withdrawn/unresolved/unknown;
- attribution strength: no claim/consistent/shared-contributory/direct evidence;
- evidence links;
- reviewer/date.

Matters can close with zero or unresolved Proposals. Outcome reports show reviewed coverage.

### Response (`Tagasiside`)

Purpose: organization-level member/external evidence where structured retrieval is valuable.

Matter FK is required; Consultation FK may be nullable because responses can arrive outside a formal consultation. Separate responding Organisation from any later personal contact identity.

### StakeholderPerson *(conditional)*

Professional contact identity only if repeated use proves necessary: name, Organisation, role and validity dates. No political profiling or person-level stance database.

### WorkingGroup *(conditional)*

Add only when standing representation across multiple Matters requires a durable container.

### WorkVictoryReview

Explicit reviewed judgment linking evidence and Proposal outcomes to a reportable work victory. Does not infer causality automatically.

### DecisionRecord *(conditional)*

Add only after Koda defines a real structured approval/mandate rule that cannot be represented by Submission status + ChangeEvent/audit.

## 11.5 Explicitly removed from the canonical model

- separate `LegacyRegisterRecord` business model;
- separate richer Phase-2 `Opinion` entity by default;
- stored user-editable TimelineEvent parallel to Entry;
- a fourth field-history subsystem;
- StakeholderPosition/person-level stance entity;
- generic PolicyCampaign entity;
- configurable StageDefinition/workflow engine;
- free-form Sector-as-Tag workaround.

# 12. Technical architecture

## 12.1 Architectural stack decision

**LOCKED characteristics:**

- Django modular monolith;
- supported Python runtime;
- supported Django release selected through ADR;
- PostgreSQL **18 or later at launch** because required Estonian text-search capability must be present;
- server-rendered HTML + HTMX as default interaction model;
- small isolated JS/TypeScript islands only where meaningful client state is required;
- Tailwind/CSS-token implementation acceptable but not a domain invariant;
- Docker image as deployment unit;
- managed PostgreSQL + managed object storage in production;
- Entra identity for real-data pilot/production.

Exact Django/Python/HTMX/Tailwind/hosting-service minor versions are implementation decisions recorded in ADRs and dependency locks and are upgraded normally over the product lifetime.

## 12.2 Why a new Django system

The product needs one maintainable codebase, relational domain logic, strong admin/migrations/testing, server-rendered dense forms/tables and long-term ownership by a small team. A separate SPA/API split offers no demonstrated value for the core workflows and adds synchronization/authorization/testing surface.

Credible alternatives such as ASP.NET or a TypeScript monolith remain technically viable, but a stack change requires demonstrated maintenance/ownership advantage, not architectural fashion.

## 12.3 Modular monolith boundaries

Suggested apps/modules:

```text
accounts/
matters/
workflow/
submissions/
consultations/
documents/
organisations/
taxonomy/
search/
audit/
reporting/
integrations/
legacy_import/
advocacy/          # Phase 2
```

These are code/domain boundaries inside one deployment and one primary database, not microservices.

## 12.4 Application-service rule

Business state changes occur through named service/use-case functions, e.g.:

```text
create_matter(...)
assign_matter(...)
change_stage(...)
set_next_action(...)
add_entry(...)
create_submission(...)
mark_submission_sent(...)
add_evidence_version(...)
close_matter(...)
link_successor(...)
```

Views/controllers/forms do not contain hidden workflow logic. This is critical for testing, audit, future jobs/integrations and AI-agent maintainability.

## 12.5 API strategy

No general-purpose public API is required for MVP. Use server-rendered endpoints and narrow internal/integration interfaces. Provide explicit versioned export endpoints/files for DashKoda/reporting and integration adapters for Microsoft/official sources.

## 12.6 Concurrency and consistency

Use PostgreSQL transactions and constraints for invariants:

- one open primary NextAction per Matter;
- unique human Matter reference;
- sent Submission requires sent timestamp and immutable final evidence version (with controlled transitional exception during migration if necessary);
- evidence binaries immutable;
- canonical TagAssignment unique per Matter/Tag;
- ChangeEvent written in same transaction as authoritative changes;
- audit append-only.

Do not introduce distributed-transaction infrastructure when the authoritative state is one PostgreSQL database.

# 13. Performance and large-data design

## 13.1 Scale target

Design comfortably for at least:

- 12,000+ Matters;
- 150,000 documents/versions;
- several hundred thousand Entries/ChangeEvents;
- millions of extracted searchable fragments over a 10–15 year horizon;
- six primary users plus occasional read-only/management users.

This is moderate PostgreSQL scale, but poor query patterns can still make a small system feel slow.

## 13.2 Product performance goals

Measure on production-like data rather than optimize invented micro-benchmarks. Directional goals:

- known Matter open/search feels immediate;
- ordinary filtered list changes stay sub-second where practical;
- common search returns first page around/under one second after warm-up/tuning;
- composer save provides immediate confirmation and never blocks on slow external APIs;
- management dashboard views load quickly enough for interactive drill-through.

Hard performance budgets are established after Stage-1/2 baselines and then regression-tested.

## 13.3 Query design

- server-side pagination/cursors;
- indexes for owner/stage/action date/visibility/reference/source year;
- partial indexes for active/open states where useful;
- select/prefetch relationships deliberately;
- avoid N+1 queries in dense tables;
- summary counts use direct SQL/querysets/views, not Python loops;
- expensive statistics use ordinary SQL views first and materialized views only after measured need;
- `SearchDocument` isolates full-text indexing from transactional table shape.

## 13.4 Timeline loading

The Matter timeline is paginated/lazy-loaded by meaningful chronology groups. It must not load every historical Entry/ChangeEvent/document derivative at once.

## 13.5 Caching

No Redis/cache tier is required initially. Use HTTP/browser caching for static assets and PostgreSQL query/index optimization first. Add application caching only for measured repeated expensive reads with clear invalidation semantics.

# 14. Estonian search specification

## 14.1 Search architecture

Search is a first-class product capability and initially stays inside PostgreSQL.

Canonical records feed a rebuildable `SearchDocument` projection. The projection decouples ranking/indexing from the transactional schema and gives a future external engine a clean feeder without changing domain entities.

Use:

- Estonian full-text search configuration available in PostgreSQL 18+;
- `simple` token search in parallel where useful for identifiers/terms;
- `pg_trgm` for short strings and typo/partial matching;
- exact normalized identifier columns;
- controlled tag aliases and organization aliases;
- authorization scope embedded/enforced during query construction.

Do **not** apply trigram indexes blindly to every large extracted document body.

## 14.2 Indexed sources

`SearchDocument` rows represent searchable content from:

- Matter title/alternate title/position/rationale summary;
- Entry;
- Submission;
- Consultation summary;
- DocumentDerivative extracted text;
- ExternalReference identifiers/titles;
- later Response/Proposal/PolicyThread.

Each row retains source kind/object ID, Matter ID and source locator so results can show why they matched and open the exact source.

## 14.3 Query/ranking order

Prefer deterministic high-value matches before fuzziness:

1. exact Matter reference;
2. exact official identifier (EIS/Riigikogu/EUR-Lex/Riigi Teataja/etc.);
3. normalized exact/near title;
4. Tag/alias/Organisation alias resolution;
5. Estonian FTS;
6. simple/phrase token search;
7. trigram fallback on titles/names/files/short identifiers;
8. later semantic candidate expansion if demonstrated useful.

Search results show matching snippets/source labels, not only Matter titles.

## 14.4 Estonian-language behaviour

Search must handle realistic lawyer queries involving:

- inflected forms;
- compound words;
- abbreviations;
- diacritics/diacritic-free input;
- renamed institutions;
- historical terminology;
- legal and EU identifiers;
- filenames and common typos.

Stemming does not solve all Estonian compounds. Tag/organization aliases, simple tokens and trigram matching are complementary tools.

## 14.5 Acceptance corpus

Before real pilot, create and maintain ~30+ real representative search queries supplied by lawyers, including:

- known exact Matter;
- partial title;
- inflected/compound Estonian concepts;
- ministry/organisation historical name;
- tag synonym;
- official legal reference;
- phrase found only inside an attached document;
- negative queries where irrelevant results must not dominate.

The corpus becomes a regression test whenever ranking/indexing changes.

## 14.6 External search-engine trigger

Do not introduce Elasticsearch/OpenSearch merely because the corpus grows. Trigger a formal architecture evaluation if one or more are true after tuning:

- searchable fragment count is in the multi-million range and continues to grow materially while indexes approach tens of GB;
- production-like search repeatedly fails agreed p95 latency/quality targets despite sound PostgreSQL indexing;
- cross-language/fuzzy passage retrieval/highlighting requirements clearly exceed PostgreSQL capabilities.

A trigger means **evaluate**, not automatically migrate. `SearchDocument` should make migration incremental if ever required.

## 14.7 Tag taxonomy governance

- one accountable taxonomy owner;
- users may assign existing Tags and propose new ones;
- creation/merge/deprecation controlled through admin/governance;
- aliases normalize synonyms/abbreviations/historical names;
- merged/deprecated terms remain searchable through the canonical Tag;
- no deep hierarchy in MVP;
- AI may suggest Tags but cannot create canonical assignments without confirmation;
- PolicyArea is separate and stable;
- sector becomes `AffectedSector` later only if real reporting/selection requires it.

# 15. Document and email architecture

## 15.1 Canonical roles

**LOCKED lifecycle model:**

- PostgreSQL = canonical document metadata/relationships;
- Azure Blob = canonical immutable evidence binaries in production;
- SharePoint = optional collaborative working-document location;
- backup/export = independent recovery boundary, not a second live document hierarchy.

Do not force one storage technology to solve both immutable evidence and Office co-authoring.

## 15.2 Evidence documents

Store immutable Blob-backed DocumentVersion for evidence such as:

- incoming ministry/authority files;
- original `.msg`/`.eml`;
- member feedback evidence;
- external association positions;
- official/publication evidence;
- exact final version of any sent Koda Submission;
- final outcome/legislation evidence.

At write/capture:

- preserve original filename/MIME/size;
- calculate SHA-256;
- store source/provenance;
- scan/quarantine according to environment security design;
- create derivatives asynchronously/later without replacing original.

## 15.3 SharePoint working documents

A Matter/Document may link a mutable SharePoint Office file used for co-authoring.

Store stable Microsoft identifiers where available:

- site/drive ID;
- item ID;
- URL;
- observed version/etag if useful;
- last-observed timestamp.

UI label: **`Töödokument — SharePoint`**.

This link is not treated as immutable evidence. When a Submission is sent or a working document is relied upon as an authoritative record, capture the exact binary/version into Blob as a DocumentVersion.

For highly restricted Matters, SharePoint working links are used only if equivalent permissions can be guaranteed; otherwise the safer Blob/manual workflow may be required.

## 15.4 No default SharePoint mirror

Do not automatically duplicate every evidence file into a human-maintained SharePoint folder tree. That creates a second hierarchy, sync/reconciliation burden and permission ambiguity.

Independent recovery is satisfied through tested database/document backups and periodic **portable archive exports/packages** containing human-readable manifests plus original binaries where business/continuity policy requires it.

## 15.5 Storage abstraction

Use Django's standard Storage API (and maintained Azure backend) rather than inventing a custom storage framework.

Development may use local filesystem storage with the same Document/DocumentVersion semantics. Production uses Blob. Storage-specific details remain outside domain services.

## 15.6 Upload security

- allow-list permitted business formats;
- verify MIME/signature where feasible rather than trusting filename;
- size limits;
- never execute uploaded content;
- serve untrusted HTML/SVG as attachment or sanitize safe previews;
- malware scan/quarantine before general use where real data is involved;
- downloads mediated through authorization;
- log restricted downloads/exports according to policy.

## 15.7 MSG/EML

Preserve original message binary. Derive sender/date/subject/body and attachments into searchable derivatives/linked evidence while keeping provenance from each attachment to its parent message.

Email parsing may suggest intake metadata but never silently overwrites canonical fields.

# 16. Security, privacy and governance

## 16.1 Security baseline

Security is proportional to a six-user internal system containing potentially confidential member/company evidence and long-lived legal-policy records. Avoid enterprise theatre, but do not defer non-retrofittable controls.

Use current OWASP ASVS Level-2-style controls as the practical baseline, with emphasis on:

- deny-by-default authorization;
- Entra/OIDC session security;
- CSRF/output escaping/input validation;
- secure upload/download handling;
- secrets/dependency hygiene;
- append-only audit;
- backup/restore/offboarding tests.

## 16.2 Non-retrofittable decisions from migration 0001

- custom User with Entra object identity;
- one centralized authorization chokepoint;
- inherited visibility on Matter children;
- technical-admin separation and break-glass concept;
- Entry / ChangeEvent / SecurityAuditEvent separation;
- immutable evidence DocumentVersion with checksum/provenance;
- PII separation hooks for later Response/StakeholderPerson;
- retention class + legal-hold metadata where relevant;
- MatterSourceReference/import provenance.

## 16.3 Secure Pilot Gate

Before first **real** current departmental data enters the system, verify:

1. approved hosting and HTTPS;
2. approved strong identity/Entra;
3. central authorization including search/count/export/download tests;
4. encrypted managed database/storage or explicitly approved equivalent;
5. secrets management;
6. upload controls + malware scanning/quarantine path;
7. backup and successful restore;
8. developer/support access policy;
9. retention/lawful-basis treatment for raw email/member feedback;
10. no production-data copy on home/dev machines.

The external penetration test may remain a production go-live requirement rather than blocking early secure pilot.

## 16.4 Personal data

Minimize person-level data. Organisation-level institutional memory is preferred where sufficient.

If structured Response later stores a contact person:

- organization evidence remains permanent according to business/legal policy;
- person identity is separable/pseudonymizable;
- contact retention period is explicit;
- raw emails have their own retention class;
- AI processing of confidential/member content requires a separate processor/privacy decision.

Do not use consent as a convenient default lawful basis for institutional policy evidence without proper legal assessment.

## 16.5 Audit layers

1. **Entry** — human-authored professional content, editable with visible/versioned history.
2. **ChangeEvent** — append-only authoritative business changes.
3. **SecurityAuditEvent** — access/download/export/permissions/security operations.

Avoid a fourth independent history subsystem.

## 16.6 Secrets and privileged access

- no secrets in Git/image/client bundle;
- managed identity/platform secret store where appropriate;
- least-privilege integration permissions;
- second recovery administrator;
- break-glass access logged and reviewed.

## 16.7 Continuity and recovery

Irreplaceable assets:

- PostgreSQL canonical database;
- Blob evidence originals/versions;
- source snapshots/import manifests;
- configuration/keys required for restore.

Required:

- managed PostgreSQL PITR where available;
- portable database dump at agreed cadence;
- independent/off-platform recovery copy with failure boundary different from primary tenant/account where practical;
- document backup/sync matching business RPO;
- encrypted key escrow/continuity ownership;
- quarterly or otherwise agreed restore drill;
- written next-business-day style recovery objective unless management funds/supports a tighter on-call commitment.

The recovery objective is what is locked; exact backup vendor/topology may evolve through ADRs.

# 17. Visual design system and interaction standards

## 17.1 Locked visual direction

The production interface is a **new dark-mode Koda application**. It is not a visual refresh of the Lovable alpha and must not inherit Lovable's generic dashboard aesthetic.

The visual system is derived from the Chamber's official CVI:

- official logo and permitted variants;
- official brand colors translated into dark-theme tokens;
- official typography where web licensing, readability and language support allow;
- approved fallback font stack where the CVI font is unsuitable or unavailable;
- spacing, icon and illustration rules consistent with the Chamber's public identity.

Before implementing production components, the design owner must receive the official CVI source package. Exact color values and font choices remain a design input, not something the coding agent should guess from the public website.

Dark mode is the MVP theme. A light theme is not required unless pilot evidence or accessibility review shows a concrete need.

## 17.2 Product character

The application should feel:

- authoritative but not bureaucratic;
- modern but not fashionable for its own sake;
- compact and information-rich without becoming cramped;
- calm under deadline pressure;
- recognizably Koda rather than a generic SaaS template;
- fast and predictable in every routine action.

Avoid:

- giant cards containing one number;
- decorative gradients, glass effects and excessive shadows;
- oversized page headers that push working data below the fold;
- dense border grids around every element;
- hiding core actions behind icon-only controls;
- using color as the sole status or error indicator;
- animations that delay work.

## 17.3 Design tokens

All visual values are expressed through semantic CSS custom properties and mapped into Tailwind configuration. Components must not hard-code brand hex values.

Minimum token groups:

```text
surface.canvas
surface.primary
surface.raised
surface.overlay
surface.selected
surface.hover

text.primary
text.secondary
text.muted
text.inverse
text.link

border.subtle
border.default
border.strong
focus.ring

brand.primary
brand.secondary
brand.accent

status.info
status.success
status.warning
status.danger
status.neutral

spacing.*
radius.*
shadow.*
typography.*
```

Status colors are semantic and may use CVI-compatible hues, but must remain distinguishable from brand accents.

## 17.4 Dark-mode requirements

- Do not use absolute black as the primary canvas unless the CVI specifically requires it; use layered dark neutral surfaces.
- Surface hierarchy must remain visible in ordinary office lighting and on average monitors.
- Body text, secondary text, disabled text, borders and focus states are contrast-tested separately.
- Tables must remain readable without alternating zebra fills; use spacing, subtle separators, hover and selected states.
- Active, hover, selected, focused, disabled, error and loading states are designed for every interactive component.
- Documents, charts and previews with white backgrounds are visually contained so they do not create uncontrolled glare.

## 17.5 Typography and content density

- Default body text is comfortably readable at 100% browser zoom.
- Dense tables use a slightly smaller but still accessible size than narrative content.
- Titles, references, dates and labels have consistent typographic roles.
- Long legal titles wrap naturally; truncation is used only in lists with full text available on hover/focus or detail view.
- Numeric and date columns align consistently.
- Estonian quotation marks, diacritics and long compound words must render correctly.
- The application supports browser zoom to at least 200% without loss of function.

The default list density should fit meaningful work on a normal 1,920 × 1,080 office display. A user-configurable density toggle is deferred unless pilot users request it.

## 17.6 Component principles

Core reusable components include:

- application shell and navigation;
- command/search palette;
- dense data table with sticky header;
- filter bar and saved-view picker;
- status/disposition badge;
- owner/avatar label;
- date/deadline indicator;
- attention flag;
- inline editable field;
- unified composer;
- timeline entry/system event;
- document row/version history;
- relation chain;
- empty/loading/error states;
- confirmation and destructive-action dialog;
- toast/undo feedback.

Components are server-rendered by default. JavaScript islands are justified only for interactions that materially benefit from client state, such as the rich-text composer, command palette, drag-and-drop upload and advanced typeahead.

## 17.7 Modern interaction requirements

- HTMX updates only the affected surface; routine edits do not reload the full page.
- The user sees immediate save/upload progress and an unambiguous final state.
- Reversible low-risk metadata changes may offer a short undo action.
- Destructive actions require explicit confirmation and explain consequences.
- Draft text is preserved locally and server-side where appropriate.
- Focus returns to a logical position after dialogs, HTMX swaps and saves.
- URLs preserve selected matter, saved view and meaningful filters so browser Back/Forward works predictably.
- New tabs and direct links work for all major records.
- Every keyboard shortcut has a visible alternative and is documented in context.

## 17.8 Accessibility

Target WCAG 2.2 AA for the application shell and core workflows.

Mandatory checks include:

- keyboard-only operation;
- visible focus that is never obscured;
- accessible names and descriptions;
- screen-reader table structure;
- semantic headings and landmarks;
- target size and spacing;
- error identification and recovery;
- no redundant re-entry where the system already knows a value;
- reduced-motion preference;
- contrast testing in the final CVI-derived dark palette;
- no status conveyed by color alone.

Automated accessibility checks are part of CI, but human keyboard and screen-reader review is required before cutover.

## 17.9 Responsive scope

The primary product is desktop-first. Three surfaces must work well at approximately 375 px:

1. `Minu töö`;
2. Teema `Ülevaade` including composer and camera/file attachment;
3. deadline/next-action list.

No native application, offline mode or complete mobile administration is required.

# 18. Reporting, statistics and management continuity

## 18.1 Principle

Operational reporting and statistics are projections of the same canonical records. The app must not recreate the current problem by maintaining a separate manually curated dashboard dataset.

## 18.2 Versioned DashKoda/export contract

The new system replaces the current register-derived feed only after parallel reconciliation.

The export contract includes at least the agreed equivalents of:

- stable Matter identity/reference;
- source year/record mode;
- title;
- owner;
- stage key;
- received date;
- response/opinion deadline;
- derived formal-submission sent date/count according to documented rule;
- source/addressee organization direction;
- independent consultation counts where present;
- active/closed/closure semantics;
- tags/policy areas where consumer needs them.

The contract is versioned. Compatibility fields such as a single sent date are **derived from Submission**, not independent canonical columns.

## 18.3 Export delivery

Start with the simplest reliable consumer interface: versioned CSV/JSON/file or authenticated endpoint agreed with DashKoda. Do not add event streaming or a warehouse for a nightly/small internal feed.

## 18.4 Parallel reconciliation

Before cutover:

- generate old and new exports for the same agreed population;
- diff field-by-field;
- classify every mismatch as expected mapping difference, source anomaly or defect;
- require zero unexplained differences for the sign-off fields;
- store the reconciliation report with migration evidence.

## 18.5 Metric catalogue and coverage contract

Every metric has a versioned definition including:

- key/name/description;
- source population;
- numerator and denominator if applicable;
- eligible record origins (`native`, reviewed import, archive, etc.);
- required fields;
- exclusions;
- earliest reliable period;
- source-era limitations;
- minimum completeness threshold;
- coverage count/percentage;
- authorization/drill-through query.

If minimum completeness is not met, display **insufficient data** rather than a precise-looking number.

## 18.6 Reliable operational metrics

Suitable from MVP/early management layer when definitions are agreed:

- active FULL Matters;
- new FULL Matters created;
- formal Submissions sent (from Submission entity);
- response/submission deadlines;
- active Matters without next action;
- overdue `DO + DEADLINE` actions;
- WAIT/MONITOR review queue;
- active Matter inventory by owner (**not called workload/productivity**);
- Matters by PolicyArea;
- Matters by confirmed Tag;
- Consultations opened;
- contacted/response counts with coverage, not forced rates;
- data-quality/classification gaps;
- dormant active Matters excluding intentional future WAIT/MONITOR states.

## 18.7 Reliable later metrics

Only after native/structured completeness exists:

- native consultation response rate where populations are explicitly compatible;
- median/percentile response/process times with defined start/end semantics;
- stage dwell time from complete ChangeEvent history;
- Proposal outcome distribution;
- Proposal review coverage;
- approved work-victory count;
- PolicyThread longevity;
- outcome distribution by PolicyArea/Tag with coverage.

## 18.8 Misleading/prohibited metrics

Do not ship:

- lawyer productivity from Matter counts or submission counts;
- “workload” inferred solely from open Matter count;
- legacy consultation response rate from incompatible counts;
- average Matter duration across mixed process types without strong segmentation;
- “Koda win rate”;
- percentage causally “caused by Koda”;
- ministry success ranking;
- historical trend lines hiding schema-era breaks;
- opinion volume inferred from Matter count;
- outcome rates that silently drop unresolved/unreviewed proposals;
- AI-generated influence score.

## 18.9 Statistics implementation

Start with PostgreSQL queries/views from canonical tables. Use materialized views only when measured query cost justifies them. No separate warehouse, BI cube or analytics database at the expected scale.

Every dashboard visualization supports drill-through to the exact authorized records. Common filter dimensions include period, owner, PolicyArea, Tag, track, organization, stage, action kind and record origin.

## 18.10 Dashboard UX

Prefer actionable lists and compact time-series/composition charts over decorative cards/gauges.

Rules:

- no 3D charts/decorative gauges;
- use lines for trends, bars/tables for composition;
- use medians/percentiles for skewed duration distributions;
- always show units, population and coverage where relevant;
- persistent filter state/shareable URL where useful;
- accessible non-color-only semantics and textual summary;
- click metric/chart segment → underlying filtered records.

# 19. Migration and historical preservation

## 19.1 Migration objectives

Migration must preserve evidence and uncertainty without delaying the operational replacement of Excel/OneNote.

Priorities:

1. secure current source snapshots;
2. move the attested active set correctly;
3. make recent useful history searchable;
4. preserve older rows/pages honestly as archive evidence;
5. enrich historical records on demand rather than fabricating completeness.

## 19.2 Immediate source snapshot

Before destructive/source-state changes:

- byte-exact workbook copy + hash;
- OneNote export/snapshot using approved tooling, with page metadata and embedded resource binaries where possible;
- native backup/export format as fidelity backstop where practical;
- manifest of source IDs, URLs and checksums;
- immutable/read-only archival copy.

Repeat final snapshot at cutover.

## 19.3 Per-era Excel contracts

Every sheet/year has a reviewed contract mapping each source column to canonical meaning, including direction semantics.

At minimum record:

- original header;
- canonical field;
- meaning/direction;
- type/parser rule;
- null/zero semantics;
- raw preservation rule;
- confidence/reviewer.

Never unify `KELLELT` and `KELLELE` through name similarity.

Dates import as raw value + parsed value + parse-rule/version. Legitimate anomalies (negative intervals, zeros, answered > asked, serial strings) are preserved, not “cleaned” automatically.

## 19.4 One historical business model

All register rows that represent policy matters import into `Matter`:

- `record_mode=FULL` for active/verified operational records;
- `record_mode=ARCHIVE` for historical archive records;
- `origin=LEGACY_IMPORT` plus data-quality/source-era metadata;
- modern fields may be null.

There is no second `LegacyRegisterRecord` business table.

If an archive Matter becomes relevant again, a reviewed “promote to full Matter” action enriches/activates it without changing identity/provenance.

## 19.5 Active-set attestation

Because historical status fields are incomplete, “active at cutover” is a human business decision, not an algorithm.

Generate candidates from current/recent years and future/active indicators; each lawyer reviews their slice; department head signs the active set. Every active Matter receives 100% human verification before go-live.

## 19.6 Historical tiers

### Tier 1 — active at cutover

Fully operational Matter, verified, with current owner/stage/next action/evidence links.

### Tier 2 — recent rich history

Archive/full Matter with high-confidence Excel/OneNote mapping and useful document/chronology import.

### Tier 3 — older register archive

`Matter(record_mode=ARCHIVE)` with verbatim source values/provenance and searchable raw-era panel; no fabricated modern stage/next action.

### Tier 4 — unmatched/orphan source evidence

A searchable migration/source ledger such as `LegacySourceItem` may store unmatched OneNote page/source artifact metadata and extracted text. It is **source evidence, not a second Matter model**. It can later be matched or used to enrich/promote a Matter.

## 19.7 Matching strategy

Use tiers:

1. deterministic identifiers/GUIDs where proven stable;
2. explicit reference tokens/official IDs/exact URLs;
3. multi-signal fuzzy candidates for residue;
4. human review for conflicts/ambiguity.

A deterministic ID contradicted by strong content/title/date evidence is a conflict, not an automatic match.

Preserve match method, confidence and reviewer.

## 19.8 Document identity and duplicates

- same SHA-256 proves identical bytes, not identical business occurrence;
- preserve separate source occurrences/provenance even if physical binary can be deduplicated;
- do not infer version lineage from similar filenames alone;
- original emails/attachments retain parent-child provenance;
- every imported evidence binary is byte-verified where feasible.

## 19.9 Completeness ledgers

Migration produces bidirectional ledgers:

- workbook row → Matter/archive status;
- OneNote page → matched Matter / classified non-matter / orphan;
- legacy hyperlink → resolved/conflict/unmatched;
- attachment/resource → imported/failed/duplicate bytes;
- DashKoda/report export reconciliation.

No source item silently disappears.

## 19.10 Historical metrics

Do not mix imported aggregate counts with native computed Response rows into one metric without explicit source boundary. Historical dashboards expose era/coverage and may intentionally start later than the archive.

## 19.11 Cutover source state

After signed cutover and final snapshots:

- Excel register and OneNote legacy case notebook become read-only/archive according to business/IT policy;
- new operational work is entered only in Koda Õigusloome;
- reporting feed switches atomically to the new export;
- historical sources remain available for verification/backfill, not parallel maintenance.

# 20. Integration roadmap

## 20.1 Integration rules

- canonical user actions never depend synchronously on a slow external API if failure can be deferred/retried;
- integration identities/permissions are least-privilege;
- external objects store stable IDs and observed timestamps;
- imported metadata is distinguishable from user-confirmed canonical values;
- automated intake candidates do not become Matters until accepted;
- every integration has idempotency/retry/reconciliation behaviour before production use.

## 20.2 EIS

EIS automation is **not a prerequisite for core pilot**. After the core workflow is proven, start read-only with the simplest official feed/API available:

- ingest candidate title/source/deadline/link;
- deduplicate idempotently;
- present as `IntakeItem`/Saabunud candidate;
- human accepts/relates/ignores;
- deeper document/status synchronization only after measured value.

## 20.3 Riigikogu, Riigi Teataja and EUR-Lex

Later read-only integrations may update official identifiers/stages and create monitoring candidates/events. External systems remain authoritative for their procedural facts; Koda Õigusloome remains authoritative for Koda's work, evidence and interpretation.

## 20.4 Microsoft 365

### Entra ID

Identity for real-data pilot and production.

### OneNote

Migration source only; do not build new operational dependency on OneNote.

### SharePoint

Optional collaborative working-document location. Store stable drive/item IDs and treat the working file as mutable. Snapshot relied-upon/final versions to Blob evidence. Do not maintain a default full mirror archive.

### Outlook / email

MVP supports drag/drop `.msg`/`.eml`. Deeper Outlook add-in/Graph capture only if pilot shows repeated friction.

## 20.5 Smaily/member outreach

Store campaign/external reference and consultation metadata. Do not rebuild email campaign delivery. Later integration may reconcile campaign metadata if it materially reduces manual work.

## 20.6 Koda member and organization data

Use registry/member identifiers when available to improve organization resolution. Keep Organization master data provenance and avoid silently merging institutions based on names.

## 20.7 Koda website and communications

A Matter/Submission may link to public Koda news/opinion pages. Later reviewed records may generate communication briefs/annual-report evidence packs, but no automatic publishing.

# 21. AI and intelligent assistance

## 21.1 Role of AI

AI is assistive and source-backed. High-value later use cases:

1. **previous Koda position retrieval** — show what Koda argued before, linked to exact sources/evidence;
2. summarize a Matter/document set;
3. extract candidate deadlines/organizations/references from incoming material;
4. extract candidate `Ettepanek` records from finalized Submissions for lawyer confirmation;
5. suggest related Matters/PolicyThreads;
6. suggest existing Tags;
7. drafting support using authorized prior positions/evidence;
8. later semantic search/ranking.

The highest-value AI feature is likely institutional-memory reuse, not generic chat.

## 21.2 Human confirmation

AI may propose but not silently commit material canonical facts such as:

- stage/closure;
- deadline;
- Submission sent state;
- TagAssignment;
- Proposal/outcome/attribution;
- work victory;
- organization merge;
- relationship/PolicyThread membership.

Confirmed AI suggestions become ordinary audited user actions.

## 21.3 Privacy and confidentiality

AI retrieval must use the same authorization scope as ordinary search. Restricted content cannot leak via snippets, embeddings, prompts, logs or aggregate counts.

Using member/person-level confidential content with an external model requires explicit processor/privacy/security approval.

## 21.4 Source-citation design

DocumentDerivative/SearchDocument retains exact source object/version and page/section locator where possible so AI answers can cite/open the evidence that supported them. Embeddings, if later introduced, never become the provenance source themselves.

## 21.5 Evaluation

Every AI feature gets a representative acceptance set and a measurable usefulness/error criterion before production use. “Looks impressive” is not a quality gate.

## 21.6 Explicit prohibitions

AI must not autonomously:

- send messages to ministries/members;
- close/reassign Matters;
- publish Koda positions;
- claim causal influence/work victory;
- alter historical source evidence;
- create uncontrolled Tags/organisations;
- delete or overwrite evidence.

# 22. Quality engineering and testing

## 22.1 CI gate from the first commit

Every main-branch change runs at minimum:

- formatting/lint;
- type/static checks appropriate to stack;
- unit/service tests;
- database migration check;
- security/dependency scan baseline;
- build/container smoke test.

High-value E2E/security/search tests join as their features appear. CI may have an explicit documented emergency skip path only where project policy permits, never silently.

## 22.2 Test pyramid

### Unit tests

Cover:

- stage/closure/action semantics;
- natural-language date extraction;
- legacy parsers/era contracts;
- tag normalization/alias merge;
- metric eligibility/coverage;
- search normalization/ranking helpers;
- document checksum/provenance utilities.

### Service/integration tests

Cover transactional invariants:

- create/assign/change stage;
- one primary NextAction;
- DO/WAIT/MONITOR warning behaviour;
- Submission send/final evidence capture;
- document version/derivative creation;
- visibility inheritance;
- reporting export;
- import reconciliation.

### End-to-end tests

Core user journeys:

- create Matter → assign → Entry + next action → Minu töö;
- drop email/files into Saabunud → confirm → Matter;
- create/send multiple Submissions under one Matter;
- search exact reference + Estonian term + document-only phrase;
- restricted Matter absent from unauthorized list/search/count/export/download;
- close/successor relationship;
- manager drill-through.

## 22.3 Alpha behaviour regression

Convert useful alpha behaviour (active/inactive, deadline/attention, chains, known Excel fixtures) into deterministic tests rather than copying its architecture/UI.

## 22.4 Search quality regression

Maintain the lawyer query corpus and expected acceptable results. Rebuild `SearchDocument` from scratch in tests/staging and prove results/visibility remain correct.

## 22.5 Performance testing

Seed production-like volumes and measure:

- dense Matter lists;
- timeline pagination;
- search first page;
- dashboard drill-through;
- large document-derived corpus;
- export generation.

Set/adjust hard regression budgets from measured baselines.

## 22.6 Security testing

Before real-data pilot, test central authorization across all query surfaces plus upload/download path and backup restore. Before production, run current OWASP/ASVS-based checks and an independent security/penetration assessment appropriate to exposure.

## 22.7 Data-quality/migration tests

Use literal historical anomaly fixtures so future maintainers cannot “clean” valid evidence:

- KELLELT/KELLELE direction;
- blank vs zero counts;
- answered > asked;
- negative intervals;
- serial-string dates;
- status-era absence;
- ambiguous OneNote links.

# 23. Repository, code and delivery standards

## 23.1 Repository decision

Create a separate production repository (provisional name `koda-oigusloome`). Preserve `cheerful-control` as alpha/reference unless separately retired.

## 23.2 Required repository structure

```text
AGENTS.md
README.md
docs/
  master-specification.md
  adr/
  data-contracts/
  metric-catalog/
app/
  accounts/
  matters/
  workflow/
  submissions/
  consultations/
  documents/
  organisations/
  taxonomy/
  search/
  audit/
  reporting/
  integrations/
  legacy_import/
  advocacy/          # Phase 2
scripts/
tests/
Dockerfile
docker-compose.yml
.env.example
```

Exact Django project layout may differ; domain boundaries must remain recognizable.

## 23.3 Architecture decision records

ADRs record choices that can evolve without rewriting the product constitution, including:

- exact Python/Django versions and upgrade policy;
- PostgreSQL baseline/upgrade policy;
- production hosting service (Container Apps/App Service/etc.);
- storage backend configuration;
- Entra/OIDC library;
- SharePoint working-document integration;
- backup topology;
- search ranking/index implementation;
- editor/JS island choices.

The master specification locks capabilities and invariants; ADRs lock current implementation choices.

## 23.4 Coding standards

- explicit domain/service functions;
- small modules with clear ownership;
- no business logic hidden in templates/controllers/signals;
- migrations reviewed as code;
- no magic status strings scattered across app;
- centralized authorization helpers/querysets;
- typed/validated integration payloads;
- no secrets in repository;
- tests accompany bug fixes and domain-rule changes;
- comments explain why/invariant, not obvious syntax.

## 23.5 Agent-development rules

AI coding agents must:

1. read `AGENTS.md` + relevant master/ADR before edits;
2. preserve domain/authorization/storage invariants;
3. make small reviewable changes;
4. run required tests/CI;
5. never weaken tests to make a change pass;
6. update ADR/spec only when a decision truly changes;
7. avoid introducing dependencies/services without explicit justification;
8. never use production/confidential data in local prompts/tests;
9. surface migration/security implications before irreversible schema changes.

# 24. Deployment and operations

## 24.0 Target architecture

```text
Users
  ↓ HTTPS / Entra OIDC
Django modular monolith
  ↓
PostgreSQL (canonical metadata/business state/search/statistics)
  +
Azure Blob (immutable evidence)
  +
optional SharePoint working documents
  +
cron/jobs from same application image

Independent backup/recovery copy
```

No Dapr/service mesh/Kubernetes/event bus is required.

## 24.1 Environments

### Local development

Docker Compose; synthetic fixtures only; local PostgreSQL and filesystem evidence storage matching production semantics.

### Private functional rehearsal

User's controlled/home infrastructure may be used for synthetic or explicitly non-confidential rehearsal only. It is not a production/confidential-data environment.

### Secure real-data pilot

Before Stage-3 real work, deploy to Koda-approved managed/secure infrastructure satisfying the Secure Pilot Gate. Prefer the same application image and the same database/storage classes planned for production so the pilot exercises real operational assumptions.

### Production

Azure-managed deployment. Azure Container Apps is a strong default candidate; App Service is a valid simpler alternative. Choose via ADR based on Koda subscription/operations, not product logic. Managed PostgreSQL + Blob + Entra are preferred production services.

## 24.2 Deployment pipeline

- merge to main after CI;
- build immutable container image;
- apply migrations through controlled deployment step;
- deploy application;
- smoke/health tests;
- rollback strategy for code, and forward-safe migration strategy for schema;
- no production secret baked into image.

## 24.3 Background jobs

Start with scheduled Django management commands/platform jobs from the same image for:

- reporting export;
- search projection maintenance/rebuild support;
- archive/backup checks;
- later EIS polling/extraction/reminders.

Do not add Celery/Redis until job volume/latency/coordination measurably requires a broker/worker model.

## 24.4 Observability

At minimum:

- application errors with request correlation;
- health/readiness;
- DB/storage/job failures;
- backup/export failure alerts;
- integration retry/failure visibility;
- no sensitive body/document contents in ordinary logs;
- shared operational alert destination.

## 24.5 Maintenance and continuity

- documented deploy/restore/offboarding procedures;
- supported dependency upgrade cadence;
- PostgreSQL major upgrade plan over product lifetime;
- second person/admin able to restore/administer essential services;
- annual/quarterly continuity exercise according to policy;
- one-maintainer absence mode explicitly documented (e.g. safe read-only/operational fallback).

# 25. Existing alpha disposition and reuse plan

## 25.1 Preserve

Keep `Kauror/cheerful-control` available during discovery, build and parallel operation.

Preserve as executable evidence:

- whole-department and personal overview behavior;
- active/inactive interpretation;
- attention flags;
- opinion-under-preparation count;
- deadline extraction examples from `Järgmiseks`;
- filters and drill-down behavior;
- matter-chain interpretation;
- workbook upload/parser fixtures;
- snapshot/reporting outputs.

## 25.2 Do not preserve as production design

Do not copy by default:

- Lovable visual design or layout;
- current React/TanStack page components;
- Supabase snapshot schema;
- manual Excel upload as normal operation;
- links back to Excel/OneNote for editing;
- any credential-bearing `.env` history;
- client-side full-dataset filtering patterns;
- alpha-specific route and component organization.

## 25.3 Convert behavior into tests

Before implementing equivalent Django features, create a golden behavior set from the alpha and guide:

- representative workbook fixture;
- expected active/inactive matters;
- expected attention reasons;
- expected next deadlines from each documented text example;
- expected personal/department counts;
- expected predecessor/successor chains;
- expected reporting rows.

The new system may deliberately improve behavior, but every difference is documented and reviewed rather than accidental.

## 25.4 Transition

- alpha remains the read-only dashboard until reporting parity is proven;
- new system becomes source of truth for pilot matters;
- parallel reporting compares both;
- at cutover DashKoda switches to the new export;
- alpha is retained as archived reference, then access can be limited.

# 26. Recommended build order

The build order is optimized for adoption risk first, then secure real-data validation, then reporting and advocacy depth.

## Stage 0 — Decisions + skeleton

**Scope:** new repo, Compose, Django/PostgreSQL baseline, CI, custom User, authorization shape, source snapshots, era-contract framework, stage/action vocabulary workshop, Submission/visibility/document ADRs, CVI tokens and DashKoda contract.

**Exit criterion:** dev/synthetic instance reachable; CI green; snapshots archived; critical ADRs and data contracts reviewed.

## Stage 1 — Core vertical slice

**Scope:** Matter CRUD, owner/stage/closure, NextAction DO/WAIT/MONITOR, Entry/composer, Minu töö, Teemad, quick search, basic PolicyArea/Tags, `Submission`, ChangeEvent and initial dark CVI components.

**Exit criterion:** two users complete timed synthetic workflows: create <30 s; routine update ≤30 s and faster than OneNote.

## Stage 2 — Evidence + search + consultation

**Scope:** Blob-style evidence semantics, optional SharePoint working-document reference, DocumentDerivative, MSG/EML, SearchDocument + Estonian FTS/trigram, thin Consultation, previews, backup/restore and upload protection.

**Exit criterion:** three representative Matters reconstructed; search corpus passes; evidence restore passes; multiple Submissions work correctly.

## Stage 2.5 — Secure Pilot Gate

**Scope:** approved Azure/managed environment, Entra, TLS, central authorization tests, managed DB/storage, secrets, malware scanning/quarantine, backup + restore and restricted-record verification.

**Exit criterion:** business/security owner approves entry of real departmental data.

## Stage 3 — Real-user pilot

**Scope:** 2–3 lawyers run all new Matters in the secure environment; measure speed, search misses, Outlook/SharePoint escapes, fields they refuse to maintain and taxonomy use.

**Exit criterion:** pilot users no longer need Excel/OneNote as parallel registers for new work; top friction issues fixed or prioritized.

## Stage 4 — Department fit + reporting

**Scope:** pilot fixes, manager operational views, reliable MVP statistics/coverage, DashKoda export parallel-run and active-set attestation tooling. A simple EIS watcher is optional only if it cannot distract from core fit.

**Exit criterion:** department head approves move; export diff has zero unexplained sign-off differences; active set signed.

## Stage 5 — Production hardening

**Scope:** production subscription/resources, monitoring, independent backup, support/continuity, offboarding drill, security review/pen test and operational documentation.

**Exit criterion:** restore, offboarding, security and ownership gates pass.

## Stage 6 — Cutover

**Scope:** verified active-set import, final snapshots, sources read-only, reporting feed switch, all-user onboarding and a firm cap on parallel entry.

**Exit criterion:** all users create/maintain new work only in Koda Õigusloome for two consecutive weeks; no unexplained data/reporting loss.

## Phase 2 — Advocacy depth

**Scope:** PolicyThread, AI-assisted Proposal extraction, outcome + attribution review, structured Response, WorkVictoryReview and optional StakeholderPerson/WorkingGroup/DecisionRecord where proven, plus fuller impact statistics.

**Exit criterion:** each feature demonstrates real use and data coverage before the next complexity is added.

## Phase 3 — Historical/automation intelligence

**Scope:** deep backfill/review UI, official-system monitoring, extraction at scale, semantic search/AI and on-demand archive enrichment.

**Exit criterion:** signed per-era reconciliation and measured feature quality.

Do not attach an artificial calendar promise to these stages. The stage exit criteria matter more than a nominal week count.

# 27. Pilot, acceptance and cutover

## 27.1 Pilot rules

- No real current work before Secure Pilot Gate approval.
- Pilot users enter each new Matter once in the new system.
- Record every reason they still open Excel/OneNote and every place they escape to Outlook/SharePoint/local files because Koda Õigusloome is too slow.
- Observe real work, not only requested features.
- Do not solve every historical edge case during pilot.
- Do not let indefinite double entry become normal; set a decision/cutover plan after pilot evidence.

## 27.2 Usability acceptance

On users' own representative Matters:

- create Teema ≤30 seconds;
- routine Entry + attachment + next action ≤30 seconds and ≤6 deliberate interactions;
- capture new email/files with minimal retyping;
- record/send a second Submission under the same Matter without workarounds;
- find/open known Matter ≤5 seconds in ordinary conditions;
- no lost draft/duplicate save;
- keyboard actions also have visible click paths;
- dense table remains understandable with large volumes;
- users distinguish DO/WAIT/MONITOR and deadline/review/expected dates correctly;
- at least two users complete core flows without assistance after onboarding.

These are adoption targets; measured pilot baselines may refine exact latency thresholds without weakening the principle “faster than the old double-entry workflow.”

## 27.3 Functional acceptance

- every new process represented once as Matter;
- historical/archive Matters searchable without second business model;
- assignment/Minu töö correct;
- deadline/review warnings correct;
- multiple Submissions per Matter supported and reporting counts correct;
- evidence versions/checksums/provenance recoverable;
- SharePoint working link clearly distinguished from evidence;
- restricted inheritance enforced across list/search/count/export/download;
- reporting export reconciled;
- relationships correct;
- source provenance visible;
- search corpus/tag aliases/facets pass;
- backup/restore verified.

## 27.4 Production go/no-go

Requires signed approval from business owner, technical owner, security/privacy owner as applicable, reporting owner and continuity owner.

No-go conditions include authorization bypass, failed restore, missing source snapshots, unexplained reporting differences, unreliable composer/draft behaviour, incorrect Submission counts/evidence, or absence of second administrative recovery path.

# 28. Open business decisions and deadlines

The following decisions must be owned explicitly rather than invented by the development agent.

- **Official CVI package, permitted web fonts and dark-mode interpretation** — owner: Communications/CVI; required by Stage 0–1.
- **Final stage help text, track applicability and closure mapping** — owner: department head + lawyers; required by Stage 0.
- **Matter numbering and successor reference rules** — owner: department head; required by Stage 0.
- **Restricted-content business roles and break-glass policy** — owner: department head + privacy/security; required before Secure Pilot Gate.
- **Retention, legal hold, raw email and contact-person treatment** — owner: privacy/legal; required before Secure Pilot Gate.
- **Submission kinds, recipients and what counts as a reportable written opinion** — owner: department head + reporting owner; required Stage 0–1.
- **SharePoint working-document permission rules for restricted Matters** — owner: department + IT/security; required before Secure Pilot Gate.
- **Submission/opinion approval and role of juhatus/management** — owner: department leadership; required before any structured approval feature, ideally before Phase 2.
- **`Töövõit` threshold, evidence and approver** — owner: management + communications/legal leadership; required before impact Phase 2.
- **Production Azure subscription, resource ownership and billing** — owner: management/IT; required before Secure Pilot Gate/production allocation.
- **Internet-facing vs internal/VPN and Conditional Access** — owner: IT/security; required before Secure Pilot Gate.
- **DashKoda export format and consumer owner** — owner: DashKoda owner; required Stage 0–4.
- **Independent backup destination and key custodians** — owner: management/IT; required before production go-live.
- **Support/absence cover and second administrator** — owner: management; required before production go-live.
- **Initial PolicyArea list, controlled Tag seed and taxonomy owner** — owner: department head + lawyers; required Stage 0–1.
- **Metric catalogue owner and coverage thresholds** — owner: department head + reporting owner; required Stage 4.
- **Whether recurrent multi-year objectives justify PolicyThread UI** — owner: department/pilot evidence; required Phase 2.

# 29. Locked decision register

The following decisions are authoritative until a documented master-spec/ADR revision changes them.

- **LOCKED — Product owns the legislative/advocacy policy record, not surrounding productivity tools.** Prevents CRM/DMS/email/editor overreach.
- **LOCKED — `Teema / Matter` is the operational core for native and historical rows.** One search/reporting/domain model.
- **PHASED — Optional multi-year `PolicyThread / Mõjuteema` in Phase 2.** Solves enduring objectives without burdening ordinary Matters.
- **LOCKED — New repository + Django modular monolith.** Long-term maintainability and one codebase.
- **LOCKED — Exact framework/runtime minor versions live in ADRs.** Product architecture outlives 2026 dependency versions.
- **LOCKED — PostgreSQL 18+ at launch; PostgreSQL is the first search/statistics engine.** Required Estonian search capability and sufficient scale.
- **LOCKED — Server-rendered HTMX default; no SPA unless proven needed.** Simpler state, authorization and testing.
- **LOCKED — Lovable alpha is behaviour/reference, not production UX/UI.** Snapshot architecture and user direction.
- **LOCKED — New dark-mode-first Koda CVI design system.** Product identity, accessibility and dense-data usability.
- **LOCKED — Unified `Sissekanne` composer is the adoption feature.** Must beat OneNote update friction.
- **LOCKED — `Submission` is MVP; Matter may have many.** Core Chamber output/reporting semantics.
- **LOCKED — Stage, action, closure, proposal outcome and attribution are separate.** Avoids overloaded status and false causality.
- **LOCKED — NextAction uses DO/WAIT/MONITOR + date semantics.** Prevents false overdue/workload signals.
- **LOCKED — One primary NextAction; no generic task manager in MVP.** Matches proven operating need.
- **LOCKED/PHASED — Thin Consultation in MVP; structured Responses later.** Captures evidence without bureaucracy.
- **LOCKED — PolicyArea broad + controlled thematic Tags; sector separate later if needed.** Clean search/analytics taxonomy.
- **LOCKED — Search uses rebuildable `SearchDocument` projection.** Keeps PostgreSQL simple now and future replacement possible.
- **LOCKED — Database canonical for document metadata; Blob immutable evidence; SharePoint optional working documents.** Combines integrity with Office collaboration.
- **REJECTED — Default full SharePoint evidence mirror.** Avoids second hierarchy/source confusion.
- **LOCKED — Original evidence immutable; derivatives separate and source-locatable.** Evidence/search/AI integrity.
- **LOCKED — One historical Matter model (`FULL`/`ARCHIVE`).** Avoids dual-query/search/analytics architecture.
- **LOCKED — Entra + centralized inherited authorization before real data.** Non-retrofittable security boundary.
- **LOCKED — Technical admin does not automatically read restricted content.** Least privilege and business confidentiality.
- **LOCKED — Secure Pilot Gate precedes real departmental data.** Removes pilot/production contradiction.
- **LOCKED/PHASED — Statistics derive from canonical DB, include coverage and drill-through.** Trustworthy analytics without second system.
- **PHASED — Proposal-level outcome + attribution + WorkVictory in Phase 2.** Valuable but not adoption blocker.
- **LOCKED — Full historical backfill after operational cutover.** Prevents migration from delaying adoption.
- **PHASED — EIS automation does not block core pilot.** Adoption risk is more important than feed automation.
- **LOCKED/PHASED — AI is assistive and source-backed only.** Human responsibility + evidence.
- **REJECTED — Microservices, Kubernetes, generic BPM and external search/worker infrastructure by default.** No measured need at this scale.

# 30. Implementation kickoff package

Before feature development, produce:

1. new repository skeleton + `AGENTS.md`;
2. this v1.2 master under `docs/master-specification.md`;
3. ADRs for supported runtime/framework baseline, PostgreSQL, hosting, identity, document lifecycle, authorization, backup and search projection;
4. Koda CVI dark-mode-first design tokens/component inventory;
5. source snapshot manifest;
6. 2011–2026 Excel column-contract template (2025/2026 completed first);
7. alpha golden fixtures/expected results;
8. initial ER diagram/migration-0001 review including User, Matter, Entry, NextAction, Submission, Document, evidence/version, authorization hooks;
9. authorization/visibility matrix incl. break-glass;
10. DashKoda export contract with Submission-derived compatibility fields;
11. CI pipeline + production Docker image;
12. initial PolicyArea/Tag seed + alias/merge/taxonomy ownership note;
13. `SearchDocument` schema/index plan + lawyer search-query corpus;
14. metric catalogue template with coverage fields and first reliable operational metrics;
15. Secure Pilot Gate checklist/runbook;
16. Stage-1 working vertical slice using synthetic data.

The first feature iteration is the complete vertical slice:

```text
sign in / select dev user
    → create Teema
    → assign owner
    → set DO/WAIT/MONITOR Järgmiseks
    → add Sissekanne
    → create/send a Submission with final evidence
    → see it in Minu töö and Teemad
    → search/open it
    → inspect professional history
```

This slice already uses the CVI design system, authorization boundary, named service functions, ChangeEvent shape, PostgreSQL migrations and test fixtures.

# 31. Evidence and official references

## 31.1 Supplied evidence

- `Tööd eelnõudega(2).xlsx`
- `Eelnõude töölaua rakenduse juhend.docx`
- `executive-summary.md`
- `sample-analysis.md`
- `document-taxonomy.md`
- `information-taxonomy.md`
- `workflow-map.md`
- `onenote-structure.md`
- `onenote-structure.json`
- `sample-matters.csv`
- `Pasted markdown(4).md` — independent adversarial validation report
- `Pasted markdown(5).md` — second devil's-advocate review of Master Specification v1.1
- `KODA_LEGISLATIVE_MATTER_SYSTEM_HANDOVER_VALIDATION_BRIEF_2026-08-17.md`
- GitHub repository `Kauror/cheerful-control`, including current routes, dashboard logic, Supabase types and Lovable agent guidance
- current Koda annual/policy-work source materials supplied in the project

## 31.2 Official technical references

- Django supported versions and roadmap: https://www.djangoproject.com/download/
- Django 5.2 release/LTS notes: https://docs.djangoproject.com/en/5.2/releases/5.2/
- Python 3.14 releases: https://www.python.org/downloads/
- PostgreSQL 18 release notes: https://www.postgresql.org/docs/18/release-18.html
- PostgreSQL text-search dictionaries: https://www.postgresql.org/docs/18/textsearch-dictionaries.html
- PostgreSQL Estonian stemmer listing: https://www.postgresql.org/docs/18/textsearch-psql.html
- PostgreSQL trigram extension: https://www.postgresql.org/docs/18/pgtrgm.html
- Azure Container Apps overview: https://learn.microsoft.com/azure/container-apps/overview
- Azure Container Apps managed identity: https://learn.microsoft.com/azure/container-apps/managed-identity
- Azure Database for PostgreSQL supported versions: https://learn.microsoft.com/azure/postgresql/configure-maintain/concepts-supported-versions
- Azure Blob soft delete/versioning: https://learn.microsoft.com/azure/storage/blobs/soft-delete-blob-overview
- Azure immutable storage: https://learn.microsoft.com/azure/storage/blobs/immutable-storage-overview
- Microsoft Graph OneNote content/structure: https://learn.microsoft.com/graph/onenote-get-content
- OWASP ASVS: https://owasp.org/www-project-application-security-verification-standard/
- WCAG 2.2: https://www.w3.org/TR/WCAG22/
- EIS public system and RSS surfaces: https://eelnoud.valitsus.ee/main/mount/

# 32. Final implementation mandate

Build the **smallest durable system that Koda's legal/policy team prefers over Excel + OneNote for new work**, while preserving a clean path to deeper advocacy intelligence.

The durable policy record is:

```text
optional Mõjuteema
      ↓
     Teema
      ├── evidence / documents
      ├── Seisukoht + rationale
      ├── Submission(s)
      ├── Sissekanne / interactions
      ├── Järgmiseks
      ├── consultation/member evidence
      ├── relationships + provenance
      └── later Ettepanek → outcome + attribution → reviewed mõju/töövõit
```

Implementation priorities, in order:

1. **speed and adoption** — routine work must beat the old double-entry workflow;
2. **truth and evidence** — one canonical fact, immutable originals, explicit provenance/uncertainty;
3. **search and retrieval** — institutional memory remains usable at 10× today's corpus;
4. **security and recoverability** — non-retrofittable controls before real data;
5. **simple maintainable architecture** — one modular monolith, one relational core, managed storage;
6. **trustworthy statistics** — coverage-aware, drillable, no fake precision;
7. **advocacy depth only after adoption** — proposals/outcomes add value without becoming bureaucracy;
8. **AI only where it reduces work and remains source-backed**.

Do not make the system more complex merely because a future feature can be imagined. Do not make it deceptively simple where identity, authorization, document evidence, provenance, search or reporting semantics would require an expensive rewrite later.

The best long-lived outcome is a product whose 2036 maintainer can still explain, restore and evolve the core in plain language:

> **What did Koda know, why did it take this position, what did it submit and do, what should happen next, what happened later, and what evidence supports that history?**

