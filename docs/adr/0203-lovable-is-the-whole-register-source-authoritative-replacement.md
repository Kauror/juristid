# 0203 — Lovable is the whole register: a source-authoritative replacement

- Status: Proposed (owner's «Correction to the Juristid data strategy», 2026-11-03)
- Supersedes in part: 0202 §3–§4 (in-place rebase, preservation of local values and of
  non-Lovable Teemad) — for this environment only
- Extends: 0191 (the reviewed import), 0192 (nightly comparison), 0165 (permanent deletion), 0184
- Relates to: 0096 (tombstones), 0131 §10, 0173

## Context

Juristid is a testing and synchronisation environment; **Lovable is the sole source of truth for
the operational register**. The 0202 rebuild kept 13 non-Lovable Teemad (12 evidence-pilot test
Teemad and a native test Teema `2026_241` whose number collides with a different Lovable topic),
5 pilot Teemad Lovable no longer counts as operational, and every local test value. The owner wants
the business dataset to be **exactly Lovable's operational register — 524 topics on 03.11.2026 —
each with a verified Lovable identity**, and nothing else; users, configuration, permissions and
backup/audit evidence stay.

Three schema facts shape the answer (measured, not assumed — `app.matters.deletion`):

1. A `Matter` row can never be removed: `ChangeEvent.matter` is `PROTECT` and the audit table is
   append-only by trigger. Deletion leaves a **tombstone** (`deleted_at`, every owned business row
   gone).
2. `matters_unique_human_reference` makes `(reference_year, reference_number)` unique **across
   tombstones too**; only a row with *both* `NULL` is outside it.
3. 0191 §5: a reference a permanently deleted Teema once held counts as taken — read from the
   `MATTER_DELETED` security event.

## Decision

### 1. Replace, never wipe

`lovable_pilot replace` performs, in **one** transaction under the reference-sequence and table
locks and the synchronisation run lock, with every 0191/0184 gate (flags, operator intent, package
and plan digests, a named verified backup set):

1. **Retire every current business Teema** through the existing deletion path (`delete_matter`,
   `actor=None`, provenance `LOVABLE_REPLACEMENT`): tombstones, owned rows deleted, audit trail and
   stage history kept exactly as 0165 keeps them. Continuations are unlinked first, predecessors
   before successors. A deletion blocker (legal hold, corrected entry, outside reference) stops
   the whole replacement before anything is written.
2. **Release each retired reference**: the tombstone's `reference_year`/`reference_number` become
   `NULL`; a `REFERENCE_RELEASED` security event (and the existing `MATTER_DELETED` event) keeps
   the original reference, the tombstone id and the package digest. A released reference is free
   for **this replacement only**: 0191 §5's "deleted means taken" rule is waived for exactly the
   references this transaction released, and the plan digest binds that list.
3. **Create the register**: every operational topic of one fresh, reviewed snapshot as a CREATE
   topic (0191/0202 §2 reading rules: open or closed by Lovable's activity; steps only where a
   person read them safely; held fields marked `HELD_FOR_REVIEW` with the source kept; no SENT,
   document, closure event or invented date). Continuations only through Lovable's stage links.
4. **verify** before commit: exactly the package's topics, one Teema per Lovable UUID, no live Teema
   outside the package, every reference as Lovable's.

**Evidence bytes are not deleted by the replacement.** Documents' rows go with their Teemad;
their stored files stay in the evidence store as orphans (reclaimable later by the existing
`prune_orphaned_evidence`, a separate owner decision). The replacement is therefore reversed
completely by restoring the database from the verified set taken immediately before it.

### 2. What stays

Users, personas, roles and permissions; MCP access; organisations, vocabularies and other
reference data; settings; security and change events (the latter under their tombstones); import
batches and pilot provenance records whose Teema is a tombstone; the synchronisation run ledger
and stored snapshots; backups and fingerprints. Per-Teema synchronisation state, decisions and
archives are owned by their Teema and go with it (0192's ledger keeps the run rows; the backup set
keeps everything).

### 3. One clean comparison cohort

`lovable_sync enroll --cohort lovable-register --replaces lovable-operational` (and the retired
pilot) enrols exactly the replacement package's Teemad, baselines from its snapshot, `IMPORT_HELD`
for held fields; every earlier cohort row is retired. COMPARE only; APPLY unchanged and off.

### 4. Gates and rollback

Rehearsed in full on an isolated clone (plan, dry-run, apply, verify, enrol, COMPARE, restore of
the pre-replacement dump proved equal to the pre-replacement digests). In production: site and
MCP stopped for the window, host lock and sync pause, fresh set verified to level 2 and restored
into a disposable database before any write; rollback = restore that set's database dump.

## Consequences

- The register is exactly Lovable's operational topics; Juristid-only test data is gone from the
  live dataset (kept in tombstones' audit trail and in the backup set).
- 0191 §5 and 0202 §3–§4 no longer describe this environment's dataset after the replacement.
- The irreversible parts, short of a database restore: the tombstones, released references and
  deleted owned rows; evidence files are untouched.
