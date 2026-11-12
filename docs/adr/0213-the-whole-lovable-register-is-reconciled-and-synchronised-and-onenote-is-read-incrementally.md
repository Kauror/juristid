# 0213 — The whole Lovable register is reconciled and synchronised, and OneNote is read incrementally

- Status: Proposed — ready for owner acceptance (TL-02 approved 12.11.2026)
- History: proposed with the owner brief «Full Lovable Register Reconciliation, Live Synchronization and OneNote Ingestion» (2026-11-09); amended 2026-11-11 by §10 (Lovable is the source of truth, owner approval of r54); amended 2026-11-12 by §10.13 (TL-02: a closure from Lovable is held while Juristid-only work is live)
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

> **Amended 2026-11-11 by §10 (r54).** The sentence above, and the AUTO / CONDITIONAL / REVIEW classes of this section, now hold only for a tombstone (`_plan_archive`). For every live synchronised Teema the stage and the lifecycle follow Lovable: «jõustunud», «rohkem ei tegele» and a stage link close a Teema, and a Teema Lovable has open is reopened (§10.4). The text above is kept as the record of what was decided on 2026-11-09.
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
6. ~~The disposition used for historical topics whose source stage is not terminal.~~ Answered by the amendment below (§9.1): none is invented.

Rollback is the verified backup set taken before each data step. The migrations are additive, so the code rollback goes to r51 only while the new tables are empty.

## 9. Amendment 2026-11-09 (round 2): completion before activation

**9.1 Closure reasons are the source's.** The reader (`lovable-reading/2`, `closure_reading`) records why a closed topic's work ended only where Lovable states it:
- its own `hetkeseis` through the explicit table `CLOSURE_BY_STATUS` («jõustunud» → COMPLETED; «rohkem ei tegele» or «rohkem pole tegevusi plaanis» → MONITORING_STOPPED; whitespace and case only), or
- its structured stage link to a successor (SUPERSEDED).

Being outside Lovable's operational scope is not a reason, and «ei saatnud» is not a reason: it stays the source's statement (`valja` and the deadline source-only, reason NOT_SENT). A historical topic with no stated reason is created closed ARCHIVE with a blank disposition, which `matters_closure_fields_consistent` already allows for an archive row; the importer accepts a blank only in `lovable-history` (`BLANK_DISPOSITION_COHORTS`). The Teema reads «Mitteaktiivne» and its banner «Lõpetamise põhjus pole allikas kirjas.». An operational topic Lovable calls finished without a stated reason is refused (`CLOSURE_REASON_UNKNOWN`) and held for a person. `Lõpeta teema` is unchanged. A history package re-reads every closure from its own source copy and is refused when read by another reader version (`STALE_READER`) — on both `lovable_inventory backfill` and the generic `lovable_pilot` path.

**9.2 Stage mapping.** All 14 labels of `STAGE_MAPPING` equal, case aside, a reviewed label of the same stage key (V1–V3 reference stages); a test binds them. Terminal stages stay human-only in the sync.

**9.3 Reading a later capture.** The source page opens at the latest captured version (the page row is version 1, each `OneNotePageRevision` the next); `?hoive=algne|<revision>` opens any version behind the same visibility check. Block changes are stated only where OneNote object IDs prove them. A file version is dated by its block's OneNote modification time only when that time lies between the block's creation and the import; otherwise undated. «Tegevused» projects notes from the latest version and links each to `?hoive=<revision>#plokk-N`. Nothing earlier is rewritten.

**9.4 Held OneNote readings.** A verified technical administrator decides each held item once, with a reason, in an append-only `OneNoteHoldDecision`:
- associate a shared page's file with ONE candidate Teema (through the ordinary import path);
- accept a changed reading as the next `DocumentVersion`;
- reject; or keep holding.

A decision settles only the held rows it saw; a Teema linked to the page later opens a new item. Captured rows are never altered.

**9.5 Desktop capture.** `capture_pages.ps1` was rehearsed against the OneNote desktop on one archived page (a defect fixed: the namespace manager was unrolled). The capture equalled the 04.11 capture in GUID, blocks and file hashes, planned `NO_CHANGES` and its apply wrote nothing. Navigation, placeholder and `-Section` paths are not yet rehearsed.

**9.6 Request runner.** A Lovable request records the mode its confirmation stated (`mode_at_request`); the runner refuses it (`MODE_CHANGED`) when the mode differs, never runs a PENDING request older than two hours, and asks per lane through `data_sync_requests poll`, which records a heartbeat (`DataSyncRunnerBeat`) and needs no credentials. Haldus says whether a runner was ever seen. Host-side blocks refuse the waiting request by name. A failure keeps its error class and code; a message only when it is one of the code base's fixed sentences. «Vaata erinevusi» shows next-step corrections first, one card per Teema with its current step, and never classifies free text.

**9.7 Owner and source links.** `owner` is AUTO only when Juristid still holds the accepted owner and the new name resolves to exactly one active, assignable business user; it goes through `assign_matter(provenance=…)` (Matter lock, `MATTER_ASSIGNED` audit, the open step moves with it); anything else is held. Nothing is ever written back to Lovable or OneNote: a wrong or dead Lovable link stays as provenance, a verified page is recorded as a reviewed local `MatterSourcePage` (`REVIEWED_MATCH`), and the sync never removes one (it touches no `MatterSourcePage`; a changed Lovable link is added as a new source reference).

**9.8 Migrations.** legacy_import 0029 (hold decisions) and 0030 (request mode, runner heartbeat) follow 0025–0028 in the same release; 0030 assumes no LOVABLE_SYNC request without a mode exists, which holds because 0027 ships with it.

**9.9 Amendment 2026-11-10: the historical register's import holds are shown apart.** On a rehearsal clone, enrolling `lovable-history` made every Teema whose Lovable `hetkeseis` is «lõpetatud» carry a permanent IMPORT_HELD `stage` item, and the run's «vajab ülevaatamist» went 46 → 1,869. The pages now separate them; the engine does not change. A pending difference is *historical* (`lovable_sync.historical_hold`) when its Teema's `LovableSyncState.cohort` is `lovable-history`, its reason is IMPORT_HELD **and** Lovable still says what was accepted at import (C = A). The last condition matters because `_hold_imported` gives every import-held field the reason IMPORT_HELD whatever Lovable says: Lovable reopening a «lõpetatud» topic is a real change and stays operational. The cohort is the stored, stable fact that the Teema is part of the closed archive (set once at enrolment, one column already read with the decision); the reason narrows it to the import's own hold, so a real change in Lovable on a historical Teema (MATTER_CLOSED, a gone topic) stays in the operational groups where somebody will see it. «Vaata erinevusi» lists historical holds after everything else, in one closed disclosure «Ajalooline register: impordil ootele jäetud väljad (N)», summarised by field and reason and named by reference 50 at a time (`?ajalugu=N`, opened when asked for); never in a next-step card, an operational group or «Nähtavaid erinevusi kokku». The overview's field counts («Ülevaatust ootavaid välju», «Neist impordil ootele jäetud», «Ootel erinevusi kokku») count operational work only, and the history cohort's numbers (from the run's own `by_cohort` part, and the field decisions that are such holds) are shown beneath as «Ajalooline register». A request's run line takes out of «vajab ülevaatamist» the Teemad whose every review field is such a hold (one query per page of requests) and names them apart; the run's stored counts are unchanged. Uuendused → Lovable and Haldus → Lovable show the operational figure (`SyncStatus.pending_review_operational`) and the historical one beside it; Haldus → Lovable counts the holds instead of listing them and lists at most 200 other changes. The MCP `get_lovable_sync_status` output keeps `pending_review` as the total — its schema is part of the frozen read contract (docs/adr/0185 §2), so nothing was added there. `pending_differences` now reads in a bounded number of queries (one for the settles, one for the distinct source values since) instead of three per item, with the same answers; what is pending, applied or resolved is unchanged, and a historical hold is resolved on the same «Otsusta» page.

## 10. Amendment 2026-11-11: Lovable is the source of truth (r54, as built)

**Basis.** The owner's decision of 11.11.2026, «Lovable is the source of truth at all times», approved with release r54 and its supervised first run (Approval A). This section records what r54 does. It was written after the release (post-audit round of 2026-11-12, finding LS-02), so the code cited «§10» before the section existed. Nightly APPLY is a separate owner approval (Approval B, §10.12); production runs in COMPARE until it is given.

**What it supersedes.** For every live synchronised Teema it replaces §4's AUTO / CONDITIONAL / REVIEW classes, 0192 §5's allowlist and A/B/C table, and 0202 §6's rule that an import-held field stays held. A tombstone keeps the earlier rules (`lovable_sync._plan_archive`). The engine's machinery is unchanged: snapshots, per-Teema locks with re-plan, idempotency keys, read-back verification, the run ledger and its decisions.

### 10.1 The rule

Everything Lovable owns mirrors Lovable after every successful APPLY run, including when Lovable did not change and Juristid did. With A = the last mirrored source value, B = what Juristid holds and C = what Lovable says tonight, and *target* = what the rule derives from C (`lovable_sync._plan_authoritative`):

| Case | Outcome |
|---|---|
| B is C's target, C = A | UNCHANGED |
| B is C's target, C ≠ A | ALREADY_CONVERGED; the baseline moves, nothing is written |
| C ≠ A, B is not C's target | ELIGIBLE → APPLIED: a source change, written over any local value |
| C = A, B is not C's target | ELIGIBLE → APPLIED with reason `RESTORED_SOURCE_VALUE` («Lovable'i väärtus taastati»): a local edit is put back |
| C's target cannot be stated honestly | a technical hold (`TECHNICAL_HOLDS`), never «restored over» |

A field the engine never mirrored (held by the import, or only ever observed) converges once and is then settled (`mirrored`). A restore is a restore only when the decision's whole input is unchanged: a target that moved because another Lovable field changed, or because time passed (a drafting step's day), is a source or derived change (`_restore_reason`).

### 10.2 Field ownership

**Lovable-owned** (`AUTHORITATIVE_FIELDS`, written through the named service, `FIELD_POLICY`):

| Field | Service | Held when |
|---|---|---|
| title | `set_matter_title` | blank |
| owner | `assign_matter` (the open step moves with it) | the name is not exactly one active, assignable user; on the historical register `HISTORY_OWNER_UNRESOLVED` (former staff) |
| OneNote link | a new `MatterSourceReference` (appended, never edited or removed) | not https, or removed in Lovable |
| current step (`jargmiseks` + `tegevuse_tahtaeg`, one operation) | `set_next_action` / `cancel_next_action` | a day with no sentence |
| stage, lifecycle and continuation (one operation) | §10.4 | §10.4 |
| opinion request (`arvamuse_tahtaeg`) | `change_response_deadline` / `resolve_response_deadline` | §10.5 |
| sent / «ei saatnud», its day and addressee | `LovableOpinionOutcome` | contradictory, unreadable or future |
| Koja ettepanek | `change_track` | the track before the initiative is not recorded |
| legal act | `set_legal_instruments` | not exactly one current instrument |
| received date | `set_matter_dates` | a future day, or set on a Chamber initiative |
| commencements | §10.6 | §10.6 |

**Not representable** (reported, never written): operational scope, a vanished source topic, a changed Lovable number or id.

**Juristid-owned, never touched by the sync** (§10.9): everything else.

### 10.3 The current step and «Liik määramata»

The step is a pure function of Lovable's current values, re-derived every run (`_step_decisions_authoritative`):
- **The kind** comes from one fixed rule, `lovable_reading.step_kind` (`STEP_RULE_VERSION` = `lovable-step/1`). The sentence's first word, case and surrounding punctuation aside, must be one of the closed forms of `STEP_KIND_FORMS` (DO, WAIT, MONITOR), and the sentence must name no other kind in the reviewed register vocabulary. Anything else is `ActionKind.SOURCE`, «Liik määramata»: the sentence verbatim, no kind invented.
- **The day.** A DO's day is a deadline; WAIT, MONITOR and SOURCE are looked at again on their day (`REVIEW_ON`) and are never late.
- **No sentence.** An open request with nothing sent gives the structured drafting step (0148 §10); otherwise there is no step. A day with no sentence is held.
- **Writing.** A differing step is replaced through `set_next_action`: the old step is superseded and kept, never completed. Its provenance names the rule version (`kind_basis`). A closed Teema holds no current step.
- **Changing the rule** re-derives the kind of every synchronised step at once, so a new rule version ships as its own reviewed release with a production-copy run.
- *Reconcile (post-audit stream A, PR #616, DL-01/DL-02):* how an open SOURCE step relates to an outstanding Arvamuse tähtaeg (ADR 0050). See that PR; at r54, `outstanding_response_deadlines` keeps only WAIT, so an open SOURCE step hides the deadline.

### 10.4 Lifecycle and continuation

`desired_lifecycle` reads Lovable's `hetkeseis` and stage link, in this order and nothing beyond it:
1. a stage link naming exactly one successor → closed SUPERSEDED under that successor's Teema; a successor not in Juristid holds (`SUCCESSOR_NOT_IN_JURISTID`), two successors or itself hold (`SUCCESSOR_AMBIGUOUS`);
2. a stage that ends the work → closed with its own reason (`TERMINAL_DISPOSITIONS`);
3. Lovable's disposition-only words (`CLOSURE_BY_STATUS`) → closed MONITORING_STOPPED, the stage kept;
4. «lõpetatud» → any closure is it; an open Teema is held (`CLOSURE_REASON_UNKNOWN`), because closing it would invent a reason;
5. any other mapped stage → open at it;
6. anything else → `STAGE_UNMAPPED`.

**How Juristid gets there** (`_apply_lifecycle`):
- **Open → closed:** `close_matter_from_source`. It moves the stage as an import, then `close_matter` with Lovable's reason. A source closure ends only what the source owns — the current step, cancelled with the run's provenance — and `MATTER_CLOSED` carries the run's provenance. **While Juristid-only work is live the closure is held, not applied** (§10.13): the plan holds it (`LOCAL_WORK_AT_CLOSURE`), and `close_matter_from_source` itself refuses under the Matter's lock (`SourceClosureHeld`).
- **Closed → open:** `reopen_matter_into_stage`. The request the Teema closed with stays current only when it is Lovable's day.
- **Open → open:** `change_stage`, as an import.
- **Closed → closed** for another reason or another successor: `change_closure_from_source`, one `MATTER_CLOSURE_REASON_CHANGED`. Not a reopening and a new closure: the closing moment and person stay.

A lawyer's local close or reopen is put back (§10.1); a reopened Teema that meanwhile gained Juristid-only work is held rather than closed again (§10.13). A historical Teema is reopened only when its label or its scope changed in Lovable. Inside the operational scope it is promoted to a full record and moves to the register (`MembershipBasis.MOVED`); outside it, it is held (`HISTORY_REOPENED_OUTSIDE_SCOPE`).

*Reconcile (post-audit stream C, PR #617, LS-01):* at r54 a successor created in the same run is linked only on the next run. See that PR.

### 10.5 The opinion request and the outcome's identity

- **The outcome.** `desired_outcome`: «ei saatnud» → NOT_SENT; a day in `valja_kuupaev` or `valja` → SENT on it; blank → nothing sent. A blank or a passed deadline is never «sent». Recorded as a new `LovableOpinionOutcome` row (the newest is current, none is edited), never a `Submission`, document, recipient or signature («Arvamuse dokument pole veel saadaval»).
- **Identity is the request, not its dates.** The request Lovable's outcome answers is the one whose `response_requested_at` was recorded with the baseline (`REQUESTED_AT`; a MOVED request keeps it). A sent outcome ends that request as answered, and «ei saatnud» ends it as not answering. A request that is not Lovable's (a REPLACED or fresh one) is withdrawn with Lovable's words for what was sent. No answer is claimed.
- **The request** (`_request_decision_authoritative`):
  - Lovable has no day → the current request is withdrawn and kept in the history;
  - a current request → moved to Lovable's day;
  - none current, nothing sent and the day not passed → one is set.

  There is never a second consultation. A request a person ended in Juristid while Lovable's `valja` is still blank is held (`ANSWERED_IN_JURISTID_NOT_IN_LOVABLE`).

### 10.6 Commencements and their provenance

A commencement is Lovable's when it carries `source_text = "Lovable"` (`LOVABLE_COMMENCEMENT_MARK`) or the import's `EvidencePilotRecord`. Only those are reconciled, as a set compared by (kind, day, scope note):
- **One Lovable no longer states** is cancelled through `cancel_effective_date` / `cancel_important_date`, with its reason, and kept.
- **One it states and Juristid lacks** is added.
- **An estimate** stays an estimate.

A person's own important or commencement dates are never touched. An unreadable row (`COMMENCEMENT_UNREADABLE`) and a closed Teema, which the date services refuse, are held.

### 10.7 The historical register and exclusions

The closed historical register (`lovable-history`) follows Lovable as well: owner, sent status and the other owned fields. Its archive mode, its closed state and the blank reason of «lõpetatud» are kept.

**The exclusion rule.** Only Teemad in an enrolled cohort are synchronised. A source topic that is in none of them is counted (`outside_cohort`), never written. These are:
- the inventory's LINK_ENRICHMENT, ARCHIVE_UNLINKED and POSSIBLE_DUPLICATE rows (§1);
- topics whose id or reference only a tombstone holds;
- anything a person has not decided.

This record states the rule. The list of excluded topics is operational data and is kept out of the repository.

### 10.8 Breakers, COMPARE and APPLY

- **The mode** comes only from `LovableSyncControl` (OFF / COMPARE / APPLY):
  - **COMPARE** plans, records every decision and advances converged baselines. It writes no business record.
  - **CHECK** («Kontrolli muudatusi») compares the stored reading and writes nothing.
  - **APPLY** also needs a named backup set (`NO_VERIFIED_BACKUP`).
- **The source-change breaker** (`MAX_CHANGED_MATTERS` = 20). More than 20 Teemad with any source or derived change (a non-restore write) holds the whole night REVIEW_ONLY (`CIRCUIT_BREAKER`).
- **The restore breaker** (`MAX_RESTORED_MATTERS` = 100). More than 100 restore-only Teemad holds the restores (`RESTORE_BREAKER`), and the source changes are still written. A Teema with a source change applies its restores with it, because the change can depend on them.
- **Per-run limits.** `run_sync(max_changed=…, max_restored=…)` (`lovable_sync run --max-changed --max-restored`) raises a limit for one supervised run. It is recorded in the run's counts; no stored setting changes.
- **Unchanged breakers:** `MAX_MISSING` (5) fails the run; `MAX_NEW_TOPICS` (10); a failed read-back stops every later Teema (0192 §7). `MAX_NEW_CONFLICTS` is inert under source authority, because nothing is a CONFLICT any more (finding LS-07).
- *Reconcile (post-audit stream C, PR #617):* a verified-backup requirement for APPLY, bounded per-run overrides, a stop of new-topic creation after a failed read-back, a breaker on new technical holds, and the same-run successor. See that PR; the r54 baseline is as stated above.

### 10.9 Provenance and what is never touched

**Provenance.** Every write goes through its domain service with `actor=None`; no human actor is ever recorded for the sync. Each write carries the run's provenance: `provenance_source` = `LOVABLE_SYNC`, `sync_run`, `source_reference`, `source_version`, `source_snapshot_sha256`, `mapping_version`, and the step's `kind_basis`. Each field also has its `LovableSyncDecision` row, with A, B, C, outcome, reason and idempotency key.

**The post-audit round (finding LS-03)** threads the same provenance through every other source-driven write, so no write of the sync is an anonymous system act:
- reopening and stage moves: `reopen_matter_into_stage`, `reopen_matter`, `change_stage`, and the stage move inside `close_matter_from_source`;
- the work a source closure ends (`end_live_work_for_closure`): each cancelled step, website overview and Kaasamine wait;
- the opinion request: `change_response_deadline`, `resolve_response_deadline` and the deadline a reopening ends;
- commencements: `add_` / `cancel_effective_date` and `add_` / `cancel_important_date`;
- the track (`change_track`), the legal act (`set_legal_instruments`) and the received date (`set_matter_dates`);
- the closure-reason change.

Each event records its before and after values, beside the provenance, which is nested under `provenance` so it can never shadow a key of the change itself.

**Stage periods the sync begins are IMPORTED**, as the sync's other stage moves are; a person's «Ava uuesti» stays RECORDED. An IMPORTED period stores no start, and the period it ends stores no end, so a duration or a «since» display has no moment for a sync reopening or stage move. That is deliberate: Juristid learns the change from Lovable, not when it happened.

`MATTER_CLOSURE_REASON_CHANGED` is in the `Kõik muudatused` vocabulary (finding LK-01). Each side's successor is named only to a reader who may open it. `Kõik muudatused` names «Lovable'i sünkroonimine» as the actor of a row the sync wrote.

**Never touched by the sync:**
- Juristid-only notes, entries, documents and their versions, submissions, evidence and OneNote imports;
- a person's own important and commencement dates;
- Kaasamine, website overviews, tags and relations — and, since §10.13, a closure from Lovable no longer ends Juristid-only *pending work* either: planned Tegevused, «Koja arvamuse järelkontroll», planned «Kodulehe ülevaade» and open Kaasamine waits hold the closure instead;
- `MatterSourcePage` rows;
- the audit trail.

Nothing is ever written to Lovable or OneNote.

### 10.10 Holds

A hold is only ever technical: `TECHNICAL_HOLDS` names each code by its kind:
- unresolvable identity;
- missing successor;
- duplicate reference;
- contradictory snapshot;
- unrepresentable value;
- invalid read;
- unsafe operation.

A hold changes nothing, and a later unchanged night is not «restored over» it. Historical import holds are shown apart (§9.9). `LOCAL_WORK_AT_CLOSURE` (§10.13) is an «unsafe operation» hold that is shown every night it stands, in the operational group, never as a standing or historical hold, and it counts toward no breaker.

### 10.11 Rollback

- **Stopping writes.** Setting the mode to COMPARE stops all further writes at once. Anything wrong is then corrected through the normal services.
- **Code rollback** is image-only, to r53. There is never a migration back: `audit` 0042, `legacy_import` 0031, `workflow` 0017 and `reporting` 0004 are additive. Once «Liik määramata» steps, `LovableOpinionOutcome` rows or `MATTER_CLOSURE_REASON_CHANGED` events exist, the older schema cannot hold them.
- **A database restore** is a rollback only inside the write freeze of a supervised run. After the freeze it would discard people's work.

### 10.12 Decisions this record does not take

1. **Approval B**, the nightly APPLY. Item 3 of the r54 approval document, «automatic nightly mirroring», was **not** granted with Approval A. It is Approval B, still open; production runs COMPARE, and every write so far came from the supervised first run.
2. **DECIDED 12.11.2026 — TL-02: a source closure and Juristid-only planned work.** The owner chose to hold the closure (Option A of the decision memo); see §10.13. At r54, `close_matter_from_source` cancelled that work with `follow_ups_confirmed=True`, and a later reopening did not bring it back. That is no longer the behaviour.
3. **OPEN — LS-05:** whether an overdue drafting step with nothing sent ends the day after its deadline (as built) or stays until the request is resolved.
4. Lovable-owned fields marked or read-only on synchronised Teemad (TL-01; a precondition of Approval B in the audit of 2026-11-11; post-audit stream B, PR #618).
5. A name alias, or an account, for operational owners with no active Juristid account.
6. **Latent, not decided here — successor names outside `Kõik muudatused`.** `MATTER_CLOSED`'s summary, `disposition_reason` (printed on the closed banner), and the raw `reason` in the closure events' payloads still carry the sync's own words, «Lovable: jätkub teemana NNNN_N». The admin's `ChangeEvent` page shows payloads raw. Since ADR 0181, anyone who may open the predecessor may open every Teema, so nothing leaks today. A future per-Teema restriction would make these name a successor the reader may not open. Not redesigned in this round.

### 10.13 Amendment 2026-11-12: a closure from Lovable is held while Juristid-only work is live (TL-02)

**Basis.** The owner's decision of 12.11.2026 on audit finding TL-02 (Option A of the TL-02 decision memo, with an attributed release). It amends 0152 §5 and 0146 §8 for one caller: a closure from the source is no longer an exception to «no outstanding work is ended unseen». It does not change who owns the lifecycle. Lovable still decides whether the Teema is open; Juristid only waits, visibly, until its own work is settled.

**Juristid-only pending work** (`closure_work.juristid_only_work`) is everything a closure would end (`live_work_at_closure`, 0152) except the current step:
- every planned Tegevus (`PLANNED`, no follow-up; 0143);
- every «Koja arvamuse järelkontroll», current or planned (a step with a follow-up; 0146);
- every live planned «Kodulehe ülevaade» (0081 §5);
- every live open Kaasamine feedback wait (0086 §7).

A removed («Kustuta») overview or Kaasamine is not live work (QA-WORKFLOW-01). **Lovable's own current step is not Juristid-only.** On a synchronised Teema the `OPEN` step that is not a check is Lovable's `jargmiseks` (§10.3), and a closure ends it as Lovable says.

**The hold.**
- `_lifecycle_decisions` holds every closure of an open Teema while that work exists: a new closure, a closure under a successor (SUPERSEDED), and a lawyer's local reopening put back (`RESTORED`). The `stage` decision (and `continuation`, when the link moved) is REVIEW_REQUIRED `LOCAL_WORK_AT_CLOSURE`, an «unsafe operation» in `TECHNICAL_HOLDS`.
- **The step and the request are held with it**, so there is no half-applied closure. Their decisions carry the same code when Lovable changed them, and nothing is written for either.
- The decision's `current` names the work in counts and a digest (`local_work`), the hold's first night (`held_since`, carried from the state's previous observed run while the hold stands) and its escalation day (`escalates_on`, 14 days later). There is no new table: the hold is the newest reading's decision, where every technical hold lives.
- The domain service is the backstop. `close_matter_from_source` refuses under the Matter's lock while the work exists (`SourceClosureHeld`). Work added between the plan and the write is held at the write, and the run records the hold for that Teema, never a failure.
- **It counts toward neither Teemad breaker.** It is not a source change for `MAX_CHANGED_MATTERS`, not a restore, and it is excluded from the new-holds breaker (`BREAKER_EXEMPT_HOLDS`).
- **Its own cap.** More than `MAX_NEW_CLOSURE_HOLDS` (20) holds *beginning* in one night (`held_since` = tonight) is a mass closure in Lovable or a broken reading. An APPLY night over it is REVIEW_ONLY with `CLOSURE_HOLD_BREAKER` and writes nothing; `sync_health` and the summary say so. **Proposed value; the owner's approval is needed.**
- **Age warning.** A hold older than `MAX_CLOSURE_HOLD_AGE_DAYS` (21) makes `sync_health` not ok. **Proposed value; the owner's approval is needed.**
- COMPARE and CHECK record it identically and write nothing. Repeat runs are idempotent: one hold decision per run, no event, no baseline move, the same first night.
- **Work that appears at the write.** If the domain guard refuses at the write, that Teema's other eligible fields are recorded as not executed (`CLOSURE_HELD_AT_WRITE`) and wait for the next night.

**Only while the synchronisation writes** (lead decision 12.11.2026, the more conservative choice before Approval B). A hold may lead to a write — a release — or to an escalation only in APPLY and before the final cutover:
- **COMPARE:** the night records the hold, so it shows what APPLY would do. Haldus lists it as proposed. The Teema banner is informational only: Lovable shows the Teema closed, and the closure *would* be held once the synchronisation writes. There is no release and no escalation.
- **OFF:** no release and no escalation.
- **After the cutover, or with the Teema's cohort retired:** no standing hold at all, and nothing is listed or escalated.

**Visibility.**
- **Run counts:** `held_local_work`, `new_closure_holds` and `closure_hold_limit`. The night's summary says «Lovable'is suletud, Juristides pooleli töö tõttu ootel: N teemat (täna lisandus n/20)». `sync_health` stays ok and says «N closure(s) held for Juristid-only work», unless a hold is older than 21 days. The hold is never reported as an unexplained difference and never suppressed.
- **Haldus → Lovable'i üksikasjad** lists each held closure by reference, first night, age and kind of work (counts only), with the release.
- **The Teema page** shows a banner, «Lovable on teema sulgenud, kuid Juristides on pooleli: …», with the counts (reader-blind, `LiveWork.phrases`), wherever the source-owned lock applies.
- **The in-repo independent oracle** (`tests/lovable_contract/oracle.py`) reads such a Teema as HELD: a difference with a recorded hold, never MATCH. A Lovable-owned value that differs in a way the hold does not explain (for example a step written during the hold) stays DIFFERENT.
- **The operator's `parity_check.py`** (outside the repository) does not know the hold yet. It must be extended in the same way before the first APPLY night that can hold a closure; the rehearsal round does that.

**Resolution.**
1. **The lawyer finishes, cancels or moves the work.** The next night closes the Teema with Lovable's reason, as a source change (it counts once toward the 20).
2. **The lawyer closes the Teema themselves.** While the hold stands, the lock frees the closure (`source_ownership.Ownership.held`, open-decisions E11), so «Lõpeta teema» with the 0152 confirmation is allowed. The next night sets Lovable's reason with `change_closure_from_source` and ends nothing again. While Lovable shows the Teema open, E3 still refuses it.
3. **Lovable reopens it.** The next reading holds nothing, and the hold simply clears.
4. **Release** (`lovable_closure_hold.release_closure_hold`).
   - **Who:** the Teema's owner (from the Teema page) or a verified technical administrator (from Haldus). A person, always: there is no release without one, and nothing is attributed to anybody else. `close_matter_from_source` checks this itself, so no caller can pass a release around it.
   - **When:** only while the synchronisation writes (APPLY, not cut over).
   - **What they see:** Lovable's reason, and what is cancelled in their name, in counts.
   - **The recheck:** under the Matter's and the state's locks, the release reads the stored Lovable reading again (if it no longer closes the Teema, nothing changes). A newer stored reading of the topic that no longer closes it the same way refuses the release. The web process holds no Lovable credentials, so there is no live re-read; both confirmation pages show when the reading was taken. It reads the work again: the confirmation is the digest of exactly the work shown, and changed work is refused with the renewed counts.
   - **After it:** «Ava uuesti» stays refused. The hold's E11 freeing ends with the closure, although the hold's decision stays the newest reading's until the next night.
   - **The closure:** `close_matter_from_source` with Lovable's reason and stage, the hold's run provenance plus `closure_hold: RELEASED`, and the releasing person as the actor of `MATTER_CLOSED` and of every cancellation.

**Escalation** (the notifications package; see the 0198 amendment of 2026-11-12). A hold still standing on its 14th day (`escalates_on`) gives its owner one «Vajab tegevust» row (category `CLOSURE_HOLD`). The next morning, every holder of `department.view_management` — the department head — gets one row. Both are keyed on the hold's escalation day, so a re-run never delivers twice, and both retire as DISCHARGED when the hold ends. Only while the synchronisation writes (APPLY). The category vocabulary is a CHECK constraint, so the category adds the migration `notifications` 0004: schema forward; on the way back it deletes the `CLOSURE_HOLD` rows before narrowing the CHECK.

**Reopening still restores nothing** (0086 §7, 0081 §5, 0146 D9). The hold makes that loss unnecessary rather than undoing it.
