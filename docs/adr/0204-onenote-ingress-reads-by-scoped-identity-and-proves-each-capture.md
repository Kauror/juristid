# 0204 — OneNote ingress reads by scoped identity and proves each capture

Status: **Proposed** (Phase 1 evidence, 2026-11-04; reader decided by the owner 2026-11-04: desktop OneNote, not Graph). Not implemented in `app/`;
no schema, command, schedule or deployment follows from it until the owner
approves Phase 2.

## Context

Lovable is the register (ADR 0203). Its `onenote_link` points into the
department's SharePoint team-site notebook, which holds each topic's source
material. Juristid should enrich existing Matters with that material and keep
doing so as pages change, without trusting a link as a key (master
specification §2.2, §19.7) and without repeating the ADR 0015 failure, where a
Graph export filed one page's HTML under 342 others and reported PASS.

Phase 1 established (details in `docs/onenote-ingress-phase1.md`):

* The Graph OneNote API is delegated-only. No app registration, consent or
  token cache exists for a server-side reader.
* The desktop client reads the notebook completely and read-only, but only on a
  signed-in workstation. On a team-site notebook it returns file objects only
  after the page has been viewed.
* Register links carry the desktop page GUID. The notebook GUID (`sourcedoc`)
  is shared by every link, and the older proof-of-concept mistakes it for a
  page ID.

## Decision (proposed)

1. **Reader — owner decision 2026-11-04: the OneNote desktop client on the
   owner's workstation; Graph is not used.**
   * The reader is the read-only COM members (`GetHierarchy`,
     `GetHyperlinkToObject`, `GetPageContent`) plus `NavigateTo`, which makes
     OneNote fetch file data.
   * It runs on the owner's laptop when an operator session runs it. The laptop
     may stay online for this purpose.
   * The owner chose this because the desktop XML carries what Graph's page
     HTML does not:
     * each attachment block's creation and modification time (ADR 0092 §6);
     * outline structure and nesting, for commentary;
     * files that only appear once a page is opened.
   * The same route produced the August 2026 archive (755 pages, 10,916
     files) and the October evidence pilot. The D1 pilot showed that
     navigation writes nothing back to SharePoint (§9.5a of the design).
   * Captures travel to KOCORP as a verified, read-only archive, the same
     pattern as `onenote-desktop-archive`. KOCORP never reads OneNote.
   * The Graph route (delegated `Notes.Read*`, a reader account, a token cache)
     was specified and then **not pursued**. Its prototype modules
     (`transport.py`, the Graph half of `capture.py` and `resources.py`) are
     kept only as reference and are not part of the adopted pipeline.
2. **Identity.** Internal `SourcePage` identities; provider IDs are scoped by
   reader and notebook and kept verbatim; locators are append-only
   observations. Only an exact scoped ID, or a recorded reviewer decision,
   resolves a topic. Title, filename or SHA-256 equality never merges
   identities.
3. **Capture.** A body is kept only if it is bound to the requested identity
   (echoed request ID, `<title>` and `created` markers equal to the metadata,
   unique non-trivial body). The stale-body regression test is
   release-blocking.
4. **Associations.** Page-to-Matter associations are many-to-many; each
   resource occurrence on a shared page needs its own association decision.
5. **Promotion.** Evidence enters only through the ADR 0076 privileged import
   path, as new immutable DocumentVersions (ADR 0003). Visibility follows the
   Matter and the record (ADR 0005, 0137). Nothing infers dates, statuses,
   opinions or Valdkond. The Excel-era guards of ADR 0203 stay as they are.
6. **Detection.** One pipeline with PRIORITY / PERIODIC / FULL jobs and a
   reserved FULL share. Watermarks never pass unresolved work. A run is
   `RUN_COMPLETE` only when every planned scope was fully read.
7. **Historical dates and commentary (amendment, 2026-11-04).** This extends
   ADR 0092 §6 and replaces nothing in it. Full contract:
   `docs/onenote-ingress-phase1.md` §9.
   * **The source addition date.** Only a verified desktop
     `one:OE/@creationTime` counts (`ONENOTE_BLOCK_CREATED`). It is kept per
     occurrence with its reader, capture and precision (seconds, UTC).
   * **Other times stay separate.** Block modification, file-internal metadata,
     page dates and Juristid's own detection, verification and promotion times
     are stored apart and never substituted for it.
   * **Graph occurrences.** Graph output HTML carries no attachment time. A
     Graph occurrence is dated only through a verified mapping to a
     byte-identical desktop observation; otherwise it reads «Lisamise kuupäev
     teadmata».
   * **Conflicts and later times.** Conflicting observations make the date
     unknown. A replaced binary is a new version, dated only by its change
     window. A first detection is a window, never a source date.
   * **Floating attachments (owner decision D8, 2026-11-04).** A file placed on
     the page canvas has no creation time, so it stays undated.
     * Its own last-modified time is kept and may be shown separately, as
       source metadata.
     * That time is never an inferred addition date or a timeline date.
     * These files remain eligible for import.
   * **Page text.** It is preserved verbatim per capture. Each file gets
     append-only context records with a structural basis (`ATTACHED_NOTE`,
     `CONTAINER_HEADING`) or a positional one (`ADJACENT_SINGLE_FILE`,
     `ADJACENT_SHARED`, `PAGE_NARRATIVE`). Only structural text is shown or
     indexed as the file's comment. Text beside a single file
     (`ADJACENT_SINGLE_FILE`) stays page text only (owner decision D9,
     2026-11-04).
   * **Where the dates live.** At occurrence and version level. Immutable
     `DocumentVersion` audit fields are untouched.
   * **Teema chronology.** ADR 0092's per-Tallinn-day rows stay, rows are
     added for later versions, and permission applies before grouping.

## Alternatives considered

* **App-only Graph (client credentials).** Unsupported by the OneNote API
  since 31 March 2025, whatever the per-endpoint tables say. Rejected.
* **Microsoft Graph, delegated, on KOCORP.** This was the first proposal. It
  needs an app registration, a reader account, consent, Conditional Access
  exclusions and a token cache. Its page HTML carries no attachment times, and
  attachments still unfetched by OneNote are invisible to it, so for history it
  would still depend on desktop evidence. The owner rejected it on 2026-11-04;
  the requirements stay in `JURISTID_ONENOTE_GRAPH_ACCESS_REQUIREMENTS_2026-11-04.md`,
  outside Git, in case it is reconsidered.
* **Desktop COM on a schedule without an operator.** Possible as a later step,
  as a Windows scheduled task on the same laptop. It is not adopted now:
  `NavigateTo` drives the visible OneNote window, and the local copy can lag the
  server by hours, so each run needs a sync check and a person who knows that
  it is running.
* **SharePoint `driveItem` delta on the `.one` files.** It reports that a
  section file changed, not which page. It may be used later as a "scan sooner"
  hint, once its coverage is measured. Never as the source.
* **Date files by the page's dates, the file's own metadata or the scan that
  found them.** Rejected. Each is a different fact, and in the sample, file
  metadata postdates the block for replaced binaries.
* **Attribute the nearest paragraph to a file.** Rejected. 14 of 20 sampled
  files sit in runs of several files, where nearby text belongs to no single
  file.
* **Reuse `tools/onenote_export` as is.** Its link reconciliation collapses
  real register links onto the notebook GUID. It lacks a response-size cap and
  capture binding, and it requests no `offline_access`. Its safe parts (host
  allowlist, GET-only client) informed `transport.py`.

## Consequences

* No tenant changes are needed: no app registration, consent, reader account
  or token cache (B1–B4 withdrawn 2026-11-04).
* Ingestion and re-scans depend on the owner's laptop being online, signed in,
  with OneNote running and synced. A run records how fresh the replica is and
  stops if it cannot tell.
* Fetching file data moves the visible OneNote window and the owner's
  read/unread marks (approved). It writes no SharePoint revision (verified
  2026-11-04).
* Some history is unobservable: content added and removed between two scans
  is missed.
* Two things cannot be recovered: when replaced bytes arrived, and the authors'
  names.

## Reversibility

Fully reversible until Phase 2 adds tables. The prototype is outside `app/`
and is imported by nothing.
