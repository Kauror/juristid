# 0204 — OneNote ingress reads by scoped identity and proves each capture

Status: **Proposed** (Phase 1 evidence, 2026-11-04). The owner decided the
architecture on 2026-11-04: **desktop OneNote only, no Microsoft Graph**.
Nothing is implemented in `app/`. No schema, command, schedule, automation or
deployment follows from this ADR until the owner approves production
ingestion. Production ingestion stays disabled.

## Context

Lovable is the register (ADR 0203). Its `onenote_link` points into the
department's SharePoint team-site notebook, which holds each topic's source
material. Juristid should enrich existing Matters with that material and keep
doing so as pages change, without trusting a link as a key (master
specification §2.2, §19.7) and without repeating the ADR 0015 failure, where a
Graph export filed one page's HTML under 342 others and reported PASS.

Phase 1 established (details in `docs/onenote-ingress-phase1.md`):

* The desktop client reads the notebook completely and read-only on a
  signed-in workstation.
  * On a team-site notebook it returns file objects only after the page has
    been opened.
  * Some files appear only then, floating on the page canvas.
  * Its hierarchy is a local replica that can lag the server by hours.
* A full run over all 394 resolved pages captured 6,614 files and changed no
  content. 96.9 % of the files carry a block creation date (report §9c).
* Register links carry the desktop page GUID. The notebook GUID (`sourcedoc`)
  is shared by every link. The older proof-of-concept mistook it for a page ID
  and is retired (D5).
* The Graph OneNote API is delegated-only, and its page HTML carries no
  attachment times. Graph is not used (see Alternatives).

## Decision (proposed)

1. **Reader — owner decision 2026-11-04: the OneNote desktop client on the
   owner's workstation, and only that.** There is no Graph reader, token,
   app registration or KOCORP-side OneNote access.
   * The reader uses the read-only COM members (`GetHierarchy`,
     `GetHyperlinkToObject`, `GetPageContent`) plus `NavigateTo`, which makes
     OneNote fetch file data. It never calls a member that writes
     (`UpdatePageContent`, `UpdateHierarchy`, `DeleteHierarchy`,
     `CreateNewPage`, `OpenHierarchy`, `Publish`, `SyncHierarchy`) on the
     department notebook.
   * It runs on the owner's laptop when an operator session runs it. The laptop
     may stay online for this purpose. There is no unattended schedule (see
     §8).
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
   * The Graph route was specified and then **not pursued**. Its prototype
     modules (`transport.py`, the Graph half of `capture.py` and
     `resources.py`) are reference only. They are not part of the adopted
     pipeline, and Phase 2 does not build on them.
2. **Identity.** Internal `SourcePage` identities; provider IDs are scoped by
   reader and notebook and kept verbatim; locators are append-only
   observations. Only an exact scoped ID, or a recorded reviewer decision,
   resolves a topic. Title, filename or SHA-256 equality never merges
   identities.
   * The D2 experiment (2026-11-04) showed the desktop page GUID survives
     rename, moves between sections, archiving, deletion and restore, and
     section renames. A copy gets a new GUID but keeps the original block
     times.
   * Block object IDs are re-issued by a move. A file occurrence is
     therefore carried across a move by its block creation time and filename
     within the same page GUID, and only when that match is unique.
3. **Capture.** A page capture is kept only if it is bound to the requested
   identity:
   * the page ID and title are read before and after navigation, and they are
     equal;
   * the page is in the same section in the hierarchy before and after;
   * the outline blocks keep their order, kind, text and creation times
     (navigation may touch a text block's modified time only);
   * every copied file's SHA-256 equals its source, and the source was stable
     during the copy.

   Any break stops the batch. The ADR 0015 stale-body regression stays
   release-blocking.
4. **Associations.** Page-to-Matter associations are many-to-many. Each
   attachment occurrence on a shared page needs its own association decision.
   Nothing is associated with every Matter a page is linked to by default
   (owner decision D4). Shared pages are reviewed from a private list first,
   in this order: multi-URL pages, title discrepancies, mixed open/closed.
   Unresolved topics (36 without a link, 8 page not found, 2 in the recycle
   bin, 6 document-library targets) are kept in a private exception register
   and fixed in Lovable by people, never by Juristid (D3). They do not block
   the 472 topics that resolve.
5. **Promotion.** Evidence enters only through the ADR 0076 privileged import
   path, as new immutable DocumentVersions (ADR 0003). Visibility follows the
   Matter and the record (ADR 0005, 0137). Nothing infers dates, statuses,
   opinions or Valdkond. The Excel-era guards of ADR 0203 stay as they are.
6. **Detection — owner decision 2026-11-04: Lovable changes drive it.**
   * The Lovable synchronisation already marks the topics whose register data
     changed. Juristid collects those topics as a worklist.
   * When the owner says OneNote is open and synced, a run reads only those
     topics' pages and looks for new or changed files (PRIORITY).
   * Topics without a OneNote page are skipped and stay in the D3 register.
   * An occasional FULL pass over all resolved pages, started by the owner,
     catches files added in OneNote without any Lovable change. Watermarks
     never pass unresolved work.
   * A run is `RUN_COMPLETE` only when every topic on its worklist was fully
     read. A topic leaves the worklist only then.
   * This lasts until OneNote is retired and files are uploaded to Juristid
     directly.
7. **Historical dates and commentary (amendment, 2026-11-04).** This extends
   ADR 0092 §6 and replaces nothing in it. Full contract:
   `docs/onenote-ingress-phase1.md` §9.
   * **The source addition date.** Only a verified desktop
     `one:OE/@creationTime` counts (`ONENOTE_BLOCK_CREATED`). It is kept per
     occurrence with its reader, capture and precision (seconds, UTC).
   * **Other times stay separate.** Block modification, file-internal metadata,
     page dates and Juristid's own detection, verification and promotion times
     are stored apart and never substituted for it.
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
8. **Operational requirements of the desktop reader.** Each is a precondition
   of a run that may feed production. Phase 2 must prove each one before
   ingestion is enabled.
   1. **Windows session availability.**
      * A run needs the owner's laptop on, signed in and unlocked, with OneNote
        desktop running and the department notebook open under the owner's
        account.
      * The run checks this first and stops with `READER_UNAVAILABLE` rather
        than waiting. Examples: COM not registered, notebook not in the
        hierarchy, or the session locked or asleep.
      * An operator starts each run. There is no unattended schedule until the
        owner approves one separately.
      * OneNote refusing calls while busy (`RPC_E_CALL_REJECTED`) is retried
        with backoff. Repeated refusal stops the batch.
   2. **Source synchronization freshness — owner decision 2026-11-04: the
      owner confirms it.**
      * The owner's laptop holds the up-to-date notebook. A run starts only
        after the owner confirms that OneNote is open and synced.
      * The run records that confirmation, who gave it and when, and each
        section's local `lastModifiedTime`.
      * No server-side *Modified* comparison is made. No SharePoint connector,
        sign-in, browser or new Graph application is needed.
      * A change that had not yet reached the laptop is picked up by a later
        run. It arrives late, never wrong: captures are append-only, and an
        absence never deletes anything.
      * Forcing a sync (`SyncHierarchy`) needs a separate owner decision.
   3. **Complete attachment fetching.**
      * A page counts as captured only when no placeholder remains: no empty
        or list-only block, and every file's cached path exists.
      * "Stable for two polls" is not enough. In the full run two pages were
        stable with 9 and 6 unfetched files.
      * A page with a remaining placeholder is revisited, up to a set limit,
        then reported as `INCOMPLETE_FETCH`. Its occurrences are not promoted
        until a later run completes it.
      * Floating files count. They appear only after opening.
   4. **Immutable capture transfer to Juristid.**
      * Each batch is sealed on the laptop: a manifest with every file's size
        and SHA-256, the pre- and post-navigation page XML, the hierarchy
        before and after, and the verifier's result.
      * It travels to KOCORP as a read-only archive, the
        `onenote-desktop-archive` pattern.
      * KOCORP re-hashes everything on arrival and refuses any mismatch.
        Juristid imports only from a verified archive, through the ADR 0076
        privileged path.
      * Archives are append-only. A re-capture is a new archive, never an
        overwrite.
      * Real captures never enter Git, CI or a chat.
   5. **Resumability.**
      * Work is planned as weighted batches with a persisted cursor.
      * A batch is atomic: if it fails, its partial output is kept as a
        record, set aside, and the batch is re-run from the start.
      * A completed, verified batch is never re-run implicitly.
      * The latest complete capture of a page supersedes earlier ones, and the
        earlier ones are kept.
      * Watermarks never move past an unverified or incomplete batch.
   6. **Failure alerts.**
      * Every stop condition ends the run with a named reason. Examples:
        `READER_UNAVAILABLE`, `SYNC_NOT_CONFIRMED`, `INCOMPLETE_FETCH`, a verifier
        STOP, a hash mismatch on transfer, a busy timeout.
      * The reason is reported to the owner through the existing notification
        channel, and the run is never reported as success.
      * A run that has not completed within its expected window is reported as
        overdue.
      * Silence never means "nothing changed": a zero-change result is valid
        only for a `RUN_COMPLETE` run.

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
  as a Windows scheduled task on the same laptop. It is not adopted, and no
  schedule is authorised: `NavigateTo` drives the visible OneNote window, and
  each run needs the owner's sync confirmation and a person who knows it is running.
* **SharePoint `driveItem` delta on the `.one` files.** It reports that a
  section file changed, not which page. It may be used later as a "scan sooner"
  hint, once its coverage is measured. Never as the source.
* **Date files by the page's dates, the file's own metadata or the scan that
  found them.** Rejected. Each is a different fact, and in the sample, file
  metadata postdates the block for replaced binaries.
* **Attribute the nearest paragraph to a file.** Rejected. 14 of 20 sampled
  files sit in runs of several files, where nearby text belongs to no single
  file.
* **Reuse `tools/onenote_export` as is.** Its link reconciliation collapsed
  real register links onto the notebook GUID. It was retired on 2026-11-04
  (D5): the module is removed and the command refuses. Its Graph exporter is
  not used.

## Consequences

* No tenant changes are needed: no app registration, consent, reader account
  or token cache (B1–B4 withdrawn 2026-11-04).
* Ingestion and re-scans depend on the owner's laptop being online, signed in,
  with OneNote running and synced (§8). The pipeline has a single point of
  failure: one workstation and one account. If the laptop is unavailable, new
  OneNote material is late, not lost. A run starts only on the owner's
  confirmation that OneNote is synced, and records it.
* Fetching file data moves the visible OneNote window and the owner's
  read/unread marks (approved). It writes no SharePoint revision (verified
  2026-11-04).
* Some history is unobservable: content added and removed between two scans
  is missed.
* Two things cannot be recovered: when replaced bytes arrived, and the authors'
  names.
* How page GUIDs, object IDs and dates behave under moves, renames, copies,
  replacement and deletion was measured on a disposable notebook (D2, design
  §8).
  * Web-link resolution and server-side sync were not yet observed.
  * The hierarchy's page `lastModifiedTime` is only a hint, so detection
    compares captured blocks.

## Reversibility

Fully reversible until Phase 2 adds tables. The prototype is outside `app/`
and is imported by nothing.
