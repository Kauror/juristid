# OneNote ingress — Phase 1 design and contracts

Status: **proposal for review** (ADR 0204, Proposed). **Reader decision (owner, 2026-11-04): the OneNote desktop client on the owner's workstation. Microsoft Graph is not used.** The Graph sections below are kept as a record of what was evaluated; for the adopted pipeline they are reference only. Nothing here is
implemented in `app/`, migrated, scheduled or deployed. The prototype lives in
`tools/onenote_ingress/` and is exercised only by `tests/test_onenote_ingress.py`
against invented notebooks.

Real results (counts, receipts, gaps) are in the owner's Phase 1 report, which
is kept outside Git. Real manifests, page bodies and files are in private
storage on the reviewing workstation; none of them may enter this repository,
CI, a pull request or the live evidence store.

## 1. What Phase 1 settled

| Question | Answer | Basis |
| --- | --- | --- |
| Can the Graph OneNote API be used app-only? | **No.** The overview says app-only is unsupported (retired 31 March 2025, MC1011142), although per-endpoint permission tables still list `Notes.Read.All` (Application). We follow the overview. | DOCUMENTED |
| Is there a delegated Graph authorization for a server-side reader today? | **No.** There is no Entra app registration, no consent and no token cache, on KOCORP or on the workstation. | OBSERVED |
| Can the department notebook be enumerated completely? | Yes, by the desktop client (`GetHierarchy`, one XML document, no pagination). Not yet by Graph. | OBSERVED (desktop) / UNVERIFIED (Graph) |
| Do Lovable links identify pages? | Mostly. `wd=target(Section.one\|SGUID/Title\|PGUID/)` carries the desktop page GUID, which resolves exactly against the client's hyperlinks. | OBSERVED |
| Does the desktop page GUID survive a move between sections? | In every observed case (links naming an older section that now resolve to a page elsewhere, mostly ARHIIV). Not tested by deliberately moving a page. | OBSERVED, not experimental |
| Do desktop GUIDs equal Graph page IDs? | **No** (October pilot). A Graph reader must map through its own scoped IDs. | OBSERVED earlier |
| Does a desktop page read return all attachments? | **No** on a team-site notebook: file objects not yet fetched by the client appear as empty `one:OE` until the page is viewed. A COM-only resource inventory is incomplete by construction. | OBSERVED |

## 2. Link resolution (Gate B)

`tools/onenote_ingress/locators.py` parses a link without guessing:

* `sourcedoc` names the **notebook** and `wdsectionfileid` the section
  **file**; neither is ever a page identity. The old proof-of-concept's
  `tools/onenote_export/reconcile.page_id_from_link` took the first braced
  GUID (the notebook's) or the `id=` query (a notebook *path* in
  `OneNote.aspx` links) as the page ID. Against real register links it would
  have filed every topic under one identity. **It is retired (owner decision
  D5, 2026-11-04):** the module is removed, the CLI command refuses, and
  regression tests prove notebook IDs are never page IDs.
* GUIDs compare case-insensitively as GUIDs; everything else keeps its case.
  The raw link is preserved; the client-link fragment is parsed, not stripped.
* A `/_vti_history/<n>/` prefix is recorded, not treated as a different page.
* A link holding a web half and an `onenote:` half must name the same page;
  if not, the topic is `MULTIPLE_PAGES`/`AMBIGUOUS`, never resolved.

`resolution.py` gives every topic exactly one state:

| State | Meaning |
| --- | --- |
| `EXACT_PAGE` | the locator's page GUID occurs exactly once, outside the recycle bin |
| `VERIFIED_ALIAS` | a recorded reviewer decision maps this exact locator to one page |
| `MULTIPLE_PAGES` | the locator names, or matches, more than one page |
| `CONTAINER_ONLY` | a section or notebook link: no page is identified |
| `NO_LINK` | Lovable has no link — a visible gap, never "complete" |
| `AMBIGUOUS` | contradictory identifiers, at most one of them found |
| `PAGE_NOT_FOUND` | well-formed page GUID absent from the current inventory — held, not deleted |
| `IN_RECYCLE_BIN` | found only in the notebook's recycle bin — held, not deleted |
| `INACCESSIBLE` | the section exists but its pages are not available to the reader |
| `OUT_OF_SCOPE` | another host, notebook, or a plain SharePoint document path |
| `ERROR` | unparseable; the row is reported, not dropped |

Link state and content state are separate fields. Title equality only fills
`title_candidates` for a reviewer. Notes record drift that does not change the
state: `PAGE_NOW_IN_ANOTHER_SECTION`, `PAGE_IN_ARCHIVE_SECTION`, `SUBPAGE`,
`TITLE_HINT_TRUNCATED` (OneNote shortens titles in links), `TITLE_HINT_DIFFERS`
(renamed), `OBSERVED_DIFFERS_FROM_CANONICAL` (COMPARE left Juristid's link
unchanged).

A page in ARHIIV is in scope, and an ARHIIV move never closes a Matter.

**Shared pages.** One page may serve several topics. That page's
*association* is many-to-many, but its **attachments are not**: each resource
occurrence needs its own association decision (or a reviewed rule naming the
part of the page it belongs to) before it is filed under any Matter. A page
shared by four topics is not permission to file every file on it four times.

## 3. Capture binding (Gate C, the ADR 0015 regression)

`capture.verify_capture` accepts a page body only if:

1. the response is to the URL requested (no redirect), 200, `text/html`, the
   declared length matches, and it is not a sign-in or error page;
2. the response echoes the request's fresh `client-request-id` (a cached or
   misrouted response for another request does not);
3. `<title>` and `<meta name="created">` in the body equal the metadata read
   for that page ID;
4. no other identity already holds the same non-trivial body in this run.

Two pages with the same title created in the same second cannot be told apart
by their bodies: such a capture is `WEAK_BINDING` and needs an independent
reference. Same body under two identities is `EMPTY_TEMPLATE` (trivial text)
or `DUPLICATE_BODY_REVIEW`, never silently kept.

The test `test_a_stale_previous_body_is_never_filed_under_the_next_page`
replays the 342-page failure; removing the marker check makes it fail
(mutation-checked). It is release-blocking for any Phase 2 reader.

Fidelity needs an **independent** reference. Re-reading through the same
reader is not one. The desktop client and Graph are independent of each other,
so the Phase 1 desktop receipts are the reference set for the first Graph
captures. Until Graph runs, fidelity is PARTIAL.

## 4. Resources and originals

`resources.occurrences` reads the page's output HTML: `object[data]` with
`data-attachment` is an **embedded original**; `img[data-fullres-src]` is an
image original (role — picture or printout page — is not documented and stays
unverified); `a[href]` is a link (SharePoint links are a separate access path,
never crawled). A resource URL must be HTTPS on a Graph host **inside the
page's own site scope** with a resource path, or it is never requested.

`verify_download` refuses redirects, non-200, length mismatch, empty bodies,
sign-in/error pages, HTML in place of a file and formats implausible for the
extension (a PNG preview is not the attached PDF). Files above 25 MiB are
`TOO_LARGE_NOT_TESTED`, never truncated. Signed containers (`.asice`, `.bdoc`
and others) are stored byte for byte; `signature_verified` is always false.

## 5. Identity, versions, access (Gate D)

```
SourcePage(internal_id)            ── stable, ours
  └─ ProviderId(reader, scope, value)     many; verbatim; never case-folded
  └─ LocatorObservation(raw, at, by)      append-only
  └─ Capture(capture_id, reader, request_id, body_sha256, verdict)   immutable
       └─ Occurrence(resource_id, ordinal, filename, kind)
            └─ Content(sha256)            bytes stored once
Association(source_page | occurrence → Matter, decided_by, reason)   reviewed
```

* Same scoped provider ID ⇒ same identity, whatever URL, section or title now say.
* A different provider ID is new unless continuity evidence exists
  (`REVIEWER_DECISION` or `PROVIDER_REPORTED_MOVE`). Title, filename or
  SHA-256 equality never merges identities.
* Archive moves: (1) new URL, same ID → same identity, new locator row;
  (2) new ID with evidence → linked; (3) new ID without evidence → new
  identity, offered to a reviewer.
* Files: same ID + same bytes → unchanged; same ID + new bytes → new version of
  that occurrence; new ID + bytes already known → new occurrence, bytes stored
  once; new ID + same filename + new bytes → `POSSIBLE_REPLACEMENT_REVIEW`;
  ID gone → `OCCURRENCE_ABSENT`, prior capture kept.
* 401/403/404 or absence from a listing → `RETRY_PENDING`, then
  `ACCESS_LOST_REVIEW` / `NOT_FOUND_REVIEW` after three scans. Never deleted;
  the last good capture is kept; nothing new is promoted meanwhile.
* **Access to local copies never broadens.** A promoted Document takes the
  Matter's visibility (ADR 0005) and the record's restriction (ADR 0137). Losing
  Microsoft access neither publishes the local copy more widely nor removes it
  silently; it raises a review item. A source reader that can read the whole
  notebook gives no Juristid user any extra access.

Unknown and therefore not assumed: whether Graph page IDs survive section
moves, copies or notebook moves; whether resource IDs survive a page copy or
a file replace. A disposable-notebook experiment (§8) would settle the
first two; until then the conservative rules above apply.

## 6. Evidence-only import boundary (for Phase 2, not built)

Promotion into Juristid reuses the privileged import path of ADR 0076: the leaf
services `create_document` / `add_evidence_version` (ADR 0003) with
`source_identifier = <capture_id>/<occurrence ordinal>` and
`source_url = <provider resource URL>`. They file onto closed Matters without
reopening them; the interactive wrappers keep refusing closed Matters. No
date, status, Valdkond, sent opinion or activity is inferred from ingestion.
`refuse_excel_era_write` (ADR 0203) stays untouched; Phase 2 adds a new,
separately guarded command and never re-enables `historical_import apply` or
`materialise`.

Originals and captures are kept apart from derived text; parsers are
versioned; re-parsing replays approved captures and writes a reprocessing
receipt, without recreating Matters or re-reading the source. New source
artifacts (captures, resource bytes, manifests) must be inside what the nightly
backup and its L2 verification cover. Which tier holds the evidence pool
versus the sets is an open owner decision (G6, 2026-11-04), so Phase 2 must
verify coverage and not assume it.

The August desktop archive of the old copy notebook is a separately identified
historical source. It is not the live source, and its bytes are not expected
to match edited live pages.

## 7. Recurring detection (Gate E)

One pipeline: bootstrap is its first run.

* **PRIORITY**: a new or changed Lovable locator queues the page.
  It is a reason to look, not evidence that anything changed.
* **PERIODIC**: per-section listings (`pagelevel=true`) with
  `lastModifiedDateTime ge watermark − 2 h`, so edits Lovable never mentions are
  found.
* **FULL**: a rotating cursor re-lists every section and re-captures a slice of
  pages and their resource inventories, ARHIIV and closed topics included. It
  keeps at least a quarter of every run's budget and rotates its cursor, so every
  slot is reached within ⌈slots / reserved⌉ runs (`scan.plan`).

Watermarks move only past completely read and verified items. An unfinished
listing chain, a failed capture or a pending retry holds the watermark and is
recorded as a gap. Continuations are used exactly as returned and must stay on
the collection's own origin and path. `Retry-After` is honoured, capped at
300 s; retries are finite; an authentication failure is not retried.

Unchanged-page resources: a page's HTML hash does not prove its files are
unchanged. The FULL cursor re-downloads a rotating sample of resources and
compares SHA-256, so every resource is re-verified at least once per full cycle.

**Known limitation.** Polling sees states, not edits. Content added and
removed between two scans is missed unless a separately verified history
source shows it. We promise auditable *observed* versions and stated coverage,
not a complete edit history. No OneNote-page delta or webhook feed is
documented; `driveItem` and `listItem` change tracking cover the notebook's
files, not pages. A SharePoint change signal may later trigger a scan
sooner, but only after its coverage has been measured.

Monitoring (ADR 0196): a run is `RUN_COMPLETE` only if every planned scope was fully
read. A zero-change run or exit code 0 is not proof. AUTH_FAILED, PARTIAL and
stale-watermark scopes become notifications, and so do topics left unresolved.

Proposed budgets (to be measured on the real tenant before adoption): about
30 sections with fewer than 1,000 pages give roughly 30–60 listing requests per
periodic pass. Run hourly in working hours, that puts maximum detection delay
at about 1 h for edits and about 24 h for the full cycle, with a few hundred
requests per day. Graph throttling limits are documented per tenant, so these
figures need measuring before adoption.

## 8. Disposable-notebook experiment (proposed, not run)

In a new notebook on a test site, never the department notebook, with a
test account: create page P with two files. Then move P between sections, copy
P, rename P, replace one file, delete and restore P. Record the Graph page ID,
resource IDs, `oneNoteWebUrl` and desktop GUID after each step. This settles
the UNVERIFIED rows of §5 before Phase 2 relies on them.

## 9. Historical file dates and OneNote commentary (amendment, 4 Nov 2026)

This section extends ADR 0092 §6 (amendment of 2026-10-20) and replaces
nothing in it. The rules that already hold stay in force:

* A file is dated by the `creationTime` of the outline element that holds it
  (`LegacySourceResource.source_added_at`, `ONENOTE_BLOCK_CREATED`).
* Files are grouped per Europe/Tallinn day of addition.
* Files without a time get «lisamise kuupäev teadmata».
* A file older than its page reads «lisatud enne praeguse lehe loomist»
  (`MOVED_CONTENT`).
* A later read never corrects a recorded time.

Prototype: `tools/onenote_ingress/chronology.py` and `context.py`.
Tests: `tests/test_onenote_ingress_chronology.py`.

### 9.1 What the source actually exposes

Measured on the 20 Phase 1 sample files; the counts are in the Phase 1 report.

**Desktop XML** (`GetPageContent`, piBasic):

* Every file sits in its own `one:OE`, which carries `creationTime` and
  `lastModifiedTime`.
* Times are UTC (`Z`) in whole seconds; the milliseconds are always `.000`.
* `one:InsertedFile` itself carries no time and no caption, only `pathCache`,
  `pathSource` and `preferredName`.
* Authors are opaque `authorResolutionID` / `lastModifiedByResolutionID`
  values. A readable name appeared once, and only as a last modifier.

**Graph output HTML:**

* The documented attributes of `object` are `data`, `data-attachment`,
  `data-id`, `id`, `style` and `type`. Neither `img` nor `p` has a time or
  author attribute.
* The only times are page-level.
* The earlier Graph export is no longer on disk, so this is confirmed from
  Microsoft's attribute tables, not by re-reading captured bodies.
* **Graph cannot date an attachment.** A date must never be derived from the
  page's own dates.

**The block time dates the occurrence, not the bytes.**

* 4 of the 16 files with a file-internal date have that date *after* their
  block's `creationTime`, by 0.1 to 75 days.
* All 4 of those blocks were modified after that internal date.
* So a file can be replaced inside an existing block. The block's creation
  dates when the occurrence appeared on the page; the bytes there now may be
  newer.

### 9.2 Date contract (per occurrence, and per version)

These times are kept apart, each with its basis, reader, capture reference and
precision. None is ever substituted for another (`OccurrenceDates`).

| # | Time | Basis | Used for chronology? |
| --- | --- | --- | --- |
| 1 | block created | `ONENOTE_BLOCK_CREATED` (desktop, SECOND, UTC) | **yes — the only source addition date** |
| 2 | block modified | `ONENOTE_BLOCK_MODIFIED` | no; sets `CONTENT_MAY_BE_NEWER` when more than 1 h after #1, and bounds version windows |
| 3 | file-internal metadata (OOXML core, PDF info, MSG sent, ASiC signing time) | `FILE_INTERNAL_METADATA` + detail | **never**; plausibility only, sets `CONTENT_MAY_BE_NEWER` when later than #1 |
| 4 | page created / modified | `PAGE_CREATED` / `PAGE_MODIFIED` | never; used only for the `PREDATES_PAGE` comparison |
| 5 | first detected by Juristid | `FIRST_DETECTED`, an **interval** from the last complete scan without the file to the first scan with it | never a source date; shown as «Juristid märkas faili vahemikus …» |
| 6 | bytes downloaded and verified | `BYTES_VERIFIED` | technical audit only |
| 7 | imported / promoted into Documents | `PROMOTED` (= `DocumentVersion.created_at`, untouched) | technical audit only |

The rules below are implemented in `chronology_date`, `version_dates`,
`detection_window` and `apply_reference_time`.

* **Unknown stays unknown.** Without a verified block time, `value = NULL` and
  the UI shows «Lisamise kuupäev teadmata». There is no fallback to the page,
  the file or the scan dates.
* **Disagreement means unknown, not first-wins.** If verified observations of
  the same occurrence report different `creationTime` values, all of them are
  kept, the date is NULL and `SOURCE_TIME_CHANGED` sends it for review.
  Today's importer fills an empty time once and never corrects it. Phase 2
  keeps every observation in an append-only log instead.
* **Moved or copied content** keeps its block time and is flagged
  `PREDATES_PAGE`. It is never claimed to have been added to *this* page at
  that time. That OneNote preserves the time across moves is modelled on ADR
  0092's finding; the disposable-notebook experiment (§8) still has to confirm
  it.
* **Versions.** Version 1's source date is the occurrence's date. A later
  binary is dated only by its change window: after the last capture that had
  the old bytes and before the first capture with the new ones, narrowed by
  the block's modification time. It is never dated by the block's creation.
* **Across readers.** A Graph occurrence takes a desktop block time only
  through a verified occurrence mapping **and** byte-identical content.
  Otherwise it stays undated.
* **Detection windows** narrow only on *complete* scans. An incomplete scan
  proves nothing about absence.
* **Tallinn day** = `astimezone(Europe/Tallinn).date()` of the UTC instant.
  Both daylight-saving transitions are tested.

### 9.3 Commentary and context contract

**What is kept.** The whole page is preserved verbatim as an immutable page
capture (desktop XML and/or Graph HTML), split into ordered blocks:

* block kinds: text, file, image, table, blank, and **unfetched object** — an
  empty `one:OE`, possibly a file the client has not downloaded yet;
* per block: outline index, depth, parent, times and the opaque author
  reference.

Each file occurrence gets **context records per capture**, never summaries:

| Basis | Structure | Attributed as the file's comment? |
| --- | --- | --- |
| `ATTACHED_NOTE` | text nested under the file's outline element | yes |
| `CONTAINER_HEADING` | the file is nested under a text element | yes (as its heading) |
| `ADJACENT_SINGLE_FILE` | text directly before or after a run that holds only this file and no unfetched object | **no** (owner decision D9, 2026-11-04): page text only, not shown or indexed with the file |
| `ADJACENT_SHARED` | text beside a run of several files or unknown objects | no: context for all of them, attributed to none |
| `PAGE_NARRATIVE` | the rest of the page | reachable through the page capture |

**Adjacency.** It is counted only within one outline. Separate outlines are
positioned freely, so the reading order between them is ambiguous.

**No captions.** OneNote has no caption field on files, so an explicit comment
can exist only structurally.

**Edits.** A changed comment with unchanged bytes is a **new context record**,
not a new document version, and the earlier record stays.

**Summaries.** A derived summary would be a separately labelled field and
would never replace source text. None is proposed.

### 9.4 Model proposal for Phase 2 (no migration in this assignment)

* **`LegacySourceResource`** (the occurrence) keeps `source_added_at` and
  `source_added_basis` as today. It gains:
  * `source_added_reader`, `source_added_capture`, `source_added_precision`;
  * `first_detected_not_before` and `first_detected_not_after`;
  * a Graph occurrence reference.
* **New, append-only `SourceResourceObservation`**: the occurrence, the
  capture, block created/modified as read, sha256, present/absent, and scan
  completeness. The chronology is computed from it and nothing is
  overwritten.
* **New `SourceResourceVersion`**: the occurrence, sha256, ordinal, change
  window, bytes-verified time, and a `DocumentVersion` FK. Historical dates
  live here, at occurrence and version level. The immutable `DocumentVersion`
  audit fields stay as they are.
* **New, append-only `SourceContextRecord`**: the occurrence, the capture,
  basis, block ordinals, verbatim text and the text's sha256.
* **Page captures** stay immutable (`page.source.xml` / Graph HTML), and
  `LegacySourcePage.blocks` becomes capture-scoped.

### 9.5 Display contract (proposal; `app/matters/timeline.py` stays the implementation)

**Teema chronology** (`chronology_rows`):

* **Permission first.** Files the reader cannot see neither date nor count
  rows, and contribute no context.
* First versions read **one row per Tallinn day of source addition**, keyed on
  that day's earliest addition. Files added to one page on different days
  therefore stand at their own places in the chronology.
* Undated files share one row («… — lisamise kuupäev teadmata»), anchored where
  they were stored, as today.
* A replaced binary gets its own «Faili uuendati OneNote'is» row. It sits on
  the Tallinn day of its change window when the window fits in one day;
  otherwise it reads «uuendatud vahemikus d1–d2».
* Qualifiers go on the subline: «lisatud enne praeguse lehe loomist» and «faili
  sisu võib olla hiljem uuendatud».
* The technical audit chronology (imports, scans, promotions) stays separate
  and unchanged.
* Repeat ingestion draws the same rows, keyed by
  `<source page>/<occurrence>/<sha256>`.

**Document page:**

* «Lisatud OneNote'i: <day time>» with its basis, or «kuupäev teadmata».
* «Juristid märkas: <window>» when a window exists.
* «Imporditud: <time>» (technical).
* An **Allika kontekst** panel, containing:
  * the attributed comment(s), verbatim;
  * «Kõrval olev tekst — ei pruugi käia selle faili kohta» for adjacent text;
  * «Ava lehe salvestus (<capture date>)», which opens the full page
    narrative;
  * earlier context versions.

**Search:**

* Only attributed context is indexed with a document.
* Nearby text and page text belong to the page, and the page's association
  decides who may read them.
* A restricted file's comment is never indexed for anyone who cannot see the
  file.

### 9.5a Lessons from the D1 pilot (desktop `NavigateTo`, 10 pages)

* The root `lastModifiedTime` in page XML becomes the moment of navigation.
  Page times come from the hierarchy only.
* A not-yet-fetched file can hide in a bullet-list block (`one:OE` holding only
  `one:List`) or float on the page canvas. Before fetching, attachment counts
  and attribution rates are lower bounds.
* The desktop hierarchy is a local replica that can lag the server by hours.
  A desktop inventory records when the replica last synced, and two identical
  scans prove only local stability.
* Navigating can move a section file's local `lastModifiedTime`, probably
  through read or view state. The owner checked SharePoint version history
  for both affected section files (04.11) and found no new revision, so the
  change was local only and the pilot wrote nothing back.

### 9.5b Lessons from the full desktop run (394 pages, 6,614 files)

* **A page is complete only when no placeholder is left.** On two pages the
  read was stable for two polls at 9 and 6 empty blocks and 0 files, and a
  second visit returned the files. A page with any remaining placeholder is
  revisited. A run with such a page is not `RUN_COMPLETE`.
* **A floating file has no `creationTime`.** Most floating files appear only
  after the page is opened. **Owner decision D8 (2026-11-04):**
  * Such a file stays undated (`NO_VERIFIED_SOURCE_TIME`, «Lisamise kuupäev
    teadmata») and sits on the undated chronology row.
  * Its own `lastModifiedTime` is kept as source metadata
    (`ONENOTE_OBJECT_MODIFIED`, `OccurrenceDates.object_modified`).
  * It may be displayed separately as «OneNote'i objekti viimane muutmine». It
    is never shown as an addition date, an "added no later than" bound or a
    chronology day.
  * All such files stay eligible for import.
* **Navigation can touch text blocks, never file blocks** (48 text, 0 file).
  Dates are taken from the pre-navigation read.
* **The client refuses calls while busy** (`RPC_E_CALL_REJECTED`). The reader
  retries with backoff. An aborted batch keeps its partial output and is
  re-run.
* Coverage: 96.9 % of files carry a block creation date (median 5 distinct days
  per topic). 9.6 % carry a structural comment (`ATTACHED_NOTE`,
  `CONTAINER_HEADING`). A further 36.9 % have `ADJACENT_SINGLE_FILE` text,
  which stays page text only (D9).

### 9.6 What remains unavailable

* Any attachment time from Graph.
* When the *current bytes* arrived, if a file was replaced inside a block.
  From now on only a window between captures can be recorded.
* Human-readable authors (only opaque IDs exist).
* Explicit captions (OneNote has none; there is only nesting).
* Dates of attachments the desktop client has not fetched (empty `one:OE`).
* Edits made between two scans.
