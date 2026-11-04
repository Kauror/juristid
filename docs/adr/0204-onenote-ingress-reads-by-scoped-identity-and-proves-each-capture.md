# 0204 — OneNote ingress reads by scoped identity and proves each capture

Status: **Proposed** (Phase 1 evidence, 2026-11-04). Not implemented in `app/`;
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

1. **Reader.** Phase 2 uses Microsoft Graph with **delegated** `Notes.Read.All`
   plus `offline_access`, on a dedicated, licensed, MFA-protected reader account
   that has read access to the department site only. The refresh-token cache is
   encrypted at rest on KOCORP. No `Notes.ReadWrite*`, no `Sites.*`, no app-only
   permission. Whether `Notes.Read` alone reaches a site notebook is UNVERIFIED;
   test it first and choose the narrower scope if it works. The desktop COM
   reader stays an independent *reference* for fidelity checks and is never the
   production reader.
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
   * **Page text.** It is preserved verbatim per capture. Each file gets
     append-only context records with a structural basis (`ATTACHED_NOTE`,
     `CONTAINER_HEADING`) or a positional one (`ADJACENT_SINGLE_FILE`,
     `ADJACENT_SHARED`, `PAGE_NARRATIVE`). Only structural text is shown or
     indexed as the file's comment.
   * **Where the dates live.** At occurrence and version level. Immutable
     `DocumentVersion` audit fields are untouched.
   * **Teema chronology.** ADR 0092's per-Tallinn-day rows stay, rows are
     added for later versions, and permission applies before grouping.

## Alternatives considered

* **App-only Graph (client credentials).** Unsupported by the OneNote API
  since 31 March 2025, whatever the per-endpoint tables say. Rejected.
* **Desktop COM on a workstation as the production reader.** It is complete and
  needs no token, but it needs an interactive session on one laptop, and file
  data appears only after a page is viewed in the UI. It is not a scheduled
  server process. Kept as a reference reader.
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

* An owner or tenant administrator must create the app registration, approve
  delegated consent, and decide on the reader account (Phase 1 report, B1–B4).
* Unattended access lasts only as long as the refresh token and the account's
  sign-in policy allow. Revocation is an AUTH_FAILED run and a notification,
  never a silent zero-change night.
* Some history is unobservable: content added and removed between two scans
  is missed.
* For an initial Graph ingestion, attachment dates depend on a desktop
  reference read of the same occurrences, with their file data fetched.
  Without that read the dates stay unknown, by design.
* Two things cannot be recovered: when replaced bytes arrived, and the authors'
  names.

## Reversibility

Fully reversible until Phase 2 adds tables. The prototype is outside `app/`
and is imported by nothing.
