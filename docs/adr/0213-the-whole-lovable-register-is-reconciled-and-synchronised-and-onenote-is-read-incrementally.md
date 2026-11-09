# 0213 — The whole Lovable register is reconciled and synchronised, and OneNote is read incrementally

- Status: Proposed (owner brief «Full Lovable Register Reconciliation, Live Synchronization and OneNote Ingestion», 2026-11-09)
- Extends: 0191, 0192 (and its status updates), 0201, 0202, 0203, 0204, 0205, 0211, 0212
- Migrations: `legacy_import` 0025 (engine), 0026 (inventory), 0027 (requests), 0028 (OneNote revisions and coverage). All four are additive.

Lovable and OneNote stay strictly read-only. Nothing in this record writes to either. The source transport still refuses every method except `GET` and the password sign-in `POST` (0201 §1). The access probe sends no write-shaped request.

## 1. A per-topic inventory of the whole register

**The reading.** `lovable_observation.take_observation(detail="all")` makes the same consistent reading as the nightly observation: twice, compared by hash, three attempts, the same column allowlist (never `markmed`, `mitteametlik_arvamus`, identity or raw columns). It also keeps every live topic's allowlisted bundle. The default observation document and its digest are unchanged.

**The ledger.** `lovable_inventory.build_ledger` gives one row per source topic and exactly one category:

| Category | Meaning |
|---|---|
| EXACT | A Teema carries the Lovable id through approved provenance (synchronisation state, Lovable source reference). |
| LINK_ENRICHMENT | A live Teema holds the same reference and records no Lovable identity. |
| ARCHIVE_UNLINKED | A `LegacySourcePage`'s exact page GUID is the topic's OneNote page, and no Teema links the topic. |
| ABSENT | Proven absent. A Lovable id or reference that survives only on a 03.11 tombstone is noted, not matched. |
| POSSIBLE_DUPLICATE | A shared source reference or identity, or a near-identical same-year title. Suggestions only. |
| DELETED_OR_SUPERSEDED | A Juristid record whose source vanished. Stage-link predecessors are flagged, never excluded. |
| EXCLUDED | No valid reference, with the reason. |

Rules:
- Identity comes only from ids, provenance and exact page GUIDs. Title similarity never links or merges anything.
- Building the ledger writes nothing.
- A stored run (`LovableInventoryRun`) keeps codes and identities, never wording.
- A person's decision on an ambiguous row (`LovableInventoryDecision`, append-only) has no automatic effect.
- `manage.py lovable_inventory read|ledger|report` writes `reading.json`, `ledger.json`, `ledger.csv` and a coverage report. It stores a run only with `--store`.

## 2. Historical backfill: closed, minimal, gated

Only rows that are ABSENT, non-operational, with a valid reference and no tombstone note become a CREATE-only package, cohort `lovable-history`.

- The rows are read by the machine reader (§3) with `historical=True`: closed ARCHIVE with a disposition; no step, request, written commencement, SENT, document, closure event or invented date; anything not safely readable `HELD_FOR_REVIEW`.
- The package is applied only through the importer's existing gates: flags, operator intent, package and plan digests, a named verified backup set, a re-plan under lock, preserved digests of every existing Teema, and verify before commit.
- It never retires, releases, rebases or changes an existing Teema.
- Enrolment follows `preserved --before`: `lovable_sync enroll --cohort lovable-history`.
- Operational absent topics belong to the nightly new-topic synchronisation (§4).
- LINK_ENRICHMENT and ARCHIVE_UNLINKED rows, and topics held on a tombstone, stay with a person.

## 3. The machine reader

`lovable_reading.read_topic(bundle, *, as_of, people, historical)` turns a source bundle into one format-2 CREATE topic (`READER_VERSION` recorded):

- Free-text `jargmiseks` never becomes a DO/WAIT/MONITOR step. It is held with its day.
- The only step it writes is the drafting step from structured fields (0148 §10).
- «ei saatnud» is never SENT and never concluded. A passed deadline is never "sent".
- An estimate stays an estimate.
- Stage comes only through `STAGE_MAPPING`: an explicit table of 14 exact source labels, never label similarity.
- Terminal («jõustunud», «rohkem ei tegele»), continuing and historical topics are created closed.
- It refuses (`ReadingRefused`) where no honest topic can be written.

## 4. The engine

The A/B/C reconciliation, `LOCAL_TEST_CHANGE` / `CONFLICT`, idempotency keys, the per-Teema lock with re-plan, read-back verification and every breaker of 0192 are unchanged.

**Field policy.** `lovable_sync.FIELD_POLICY` classes every reconciled field as AUTO, CONDITIONAL, REVIEW or NOT_REPRESENTABLE:
- **AUTO:** 0192 §5 unchanged — title, owner, the current step's day, OneNote link.
- **CONDITIONAL:**
  - **Stage** changes through `STAGE_MAPPING` only, on an open Teema still at the stage last accepted. A stage that ends the work is REVIEW (`STAGE_ENDS_WORK`) and never closes or reopens anything.
  - **Opinion deadline** is added as a new request only when none is open, `valja` and `valja_kuupaev` are blank, the topic is not a Chamber initiative and the day has not passed. Earlier requests and their history are never edited.
  - **Commencements** are added only when their kind reads exactly, never on a past day and never on a day Juristid already holds. A changed or removed commencement is REVIEW.
- **REVIEW:** next-step wording, `valja` / `valja_kuupaev`, `kellele`, `koja_ettepanek`, `oigusakt`, `sisse`, operational scope, and the continuation. No service writes `superseded_by` without closing the predecessor, which is a lifecycle decision.
- Closed Teemad (`lovable-history` included) only ever get `MATTER_CLOSED` REVIEW. A vanished source is reported and never acted on.

**New topics.** Every run finds operational topics outside the cohorts.
- **Identity:** states, source references (tombstones included), memberships and retirements are checked. A tombstone holding the id means REVIEW, never re-creation.
- **Reference:** taken, deleted (deleted means taken), or released by 0203. A released reference is held until the owner decides.
- **Reading:** the reader, then `validate_package`, then the importer's plan.
- **COMPARE** plans and counts.
- **APPLY:**
  - With every gate passed and at most `MAX_NEW_TOPICS` (10) a night, each topic is created once through the importer's CREATE writer, under its lock and plan digest.
  - It is read back.
  - Its state is seeded and a `NEW_TOPIC` membership is recorded.
  - An APPLIED decision keyed on the Lovable id makes a rerun create nothing.
- The cohort's required size is enrolled + joined − retired. `LovableSyncNewTopic` keeps each candidate's status.

**Runs.**
- Manual and scheduled runs always read Lovable fresh.
- A run's bookkeeping is one transaction, so a failure keeps every baseline and names its stage and the affected Teemad.
- `replay` (`--reading FILE` for a full inventory reading) plans tonight's rules inside a `READ ONLY` transaction.
- `pending_differences(visible_to=)` lists held differences with A, B, C and the reason. `resolve_field(actor=)` and `resolve_new_topic` record the administrator.

## 5. Haldus → Andmete sünkroonimine

**The section.** `/haldus/andmete-sunkroonimine/` is for a verified technical administrator (`may_open_system`; 404 otherwise).
- Lovable and OneNote are shown separately. Every count is taken from the rows it counts, and GET/HEAD write nothing.
- 0192 §9's page stays at its address as «Lovable'i üksikasjad».

**The web process never reaches Lovable and never reads or imports a capture.**
- «Kontrolli muudatusi» is 0192's report-only CHECK.
- «Sünkrooni nüüd», «OneNote'i impordi kontroll» and «Impordi OneNote'i materjalid» each record a `DataSyncRequest`. They are CSRF-protected, need a fresh proof (except the two checks), and are confirmed. The sync confirmation states the current mode, and a request made under another mode is refused.
- The host runner (`data_sync_requests run-next`, from the nightly job's own cron file through `juristid-lovable-sync.sh --on-demand`) claims a request under `SKIP LOCKED`. It runs the same engine as the night, behind every gate of the night, and records the real result.
- At most one PENDING/RUNNING request per lane, enforced by a partial unique index. Statuses move forward only, rows are never deleted (trigger), a PENDING request expires after 2 h, and a RUNNING one fails after 6 h with no runner.
- An import is bound to the plan sha256 of the newest passing check, and the runner verifies it again before writing (`PLAN_CHANGED`).

**Differences.** Each one is shown with A, B and C, named only for Teemad the reader may open and otherwise counted. A decision is `resolve_field`, audited.

**Audit.** Every request, refusal, start, finish and decision writes one `SecurityAuditEvent` (`IMPORT_RUN`, `detail.action`), with codes and ids only.

## 6. OneNote: later captures and coverage (amends 0205's later-capture contract)

**Later captures.** A later sealed capture's reading of an occurrence a Teema link already holds is a `OneNoteResourceRevision`, one per (link, occurrence, manifest).
- **New bytes under the same block** become the next `DocumentVersion` of the same Document (`VERSIONED`). Version 1, the resource row and the import row never change.
- **Held instead (`HELD`, with its reason)** when the earlier file was held, the page is shared, the Document was removed, or the bytes are empty or too large.
- **A changed block time** is held.
- **A changed structural comment** is recorded beside the earlier one.
- **Changed page text, a rename or a move** gives a `OneNotePageRevision`. The page row stays the first record.
- **The same capture applied twice** writes nothing.

**Shared pages.** Their files are never attributed to every Teema.

**Dates.** Block creation dates the occurrence. A new version is dated in «Tegevused» by the block's modification time in OneNote («OneNote'is muudetud»), never by the import day. Floating files stay undated (D8).

**Coverage.** Coverage is measured per page of the operational register.
- **State:** a page's state (`PageState`) is its latest append-only `OneNoteCoverageMark`; without a mark, what the records prove.
- **The archive:** the summary never states the archive is complete.
- **The worklist:** never the notebook. It lists uncaptured operational pages, pages of Teemas whose Lovable source value changed since the last capture, changed or unapplied links, and pages marked for re-inspection.

**Older sections.** `section-plan` inventories one section at a time. It matches only by exact identity, creates no Teema and invents no register number.

**The desktop capture stays manual** (`tools/onenote_capture/RUNBOOK.md`).

## 7. Microsoft Graph

Not usable with existing access. The only connector holds no `Notes.Read*` scope, and the alternative is SharePoint/OneDrive file access, which is excluded. Graph is not pursued (owner decision 04.11 stands).

## 8. Activation (owner decisions, none taken by this record)

1. Deploying the candidate.
2. The historical backfill apply.
3. APPLY mode for the nightly run (after `access-sql` is recorded, 0192 status update 2026-11-02).
4. The on-demand cron line `*/5 * * * * …/juristid-lovable-sync.sh --on-demand`.
5. Whether a reference released by 0203 may go to a new topic.
6. The disposition used for historical topics whose source stage is not terminal.

Rollback is the verified backup set taken before each data step. The migrations are additive, so the code rollback goes to r51 only while the new tables are empty.
