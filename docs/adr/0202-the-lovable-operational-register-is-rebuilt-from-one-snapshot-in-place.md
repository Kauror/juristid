# 0202 — The Lovable operational register is rebuilt from one snapshot, in place

- Status: Proposed (owner's brief «Expand the active Lovable register and nightly
  monitoring», 2026-11-03, superseded the same day by «Change of scope — Lovable
  import»: one coherent dataset from one fresh snapshot, not two populations)
- Extends: 0191 (the 100-Matter pilot import), 0192 (its nightly one-way
  synchronisation), 0201 (wider read-only observation), 0184 (import approvals)
- Relates to: 0157 (Lovable mirror, PR #429 — still a draft, not revived),
  0148 (Excel pilot rules), 0131 §10 (a terminal stage closes a Teema), 0120

## Context

The pilot (0191) imported 100 Teemad from Lovable's snapshot of 26.10.2026. The
nightly comparison (0192) has run in COMPARE ever since, so by 03.11 36 of them
differ from Lovable (next steps, stages, commencements, links) and nothing was
applied. Lovable's operational register holds 524 live topics (222 ongoing). Adding
the missing ones from a newer snapshot would leave Juristid with two populations
read a week apart. The owner wants **one coherent, current operational dataset from
one fresh, verified snapshot**, without wiping or replacing anything, without
overwriting what a lawyer wrote, and with the old evidence kept.

"Operational" is Lovable's own scope: a live topic with `operatiivne_ulatus`.
"Ongoing" (Lovable's dashboard) is an operational topic whose stage is neither
`jõustunud` nor `rohkem ei tegele` and which is not the predecessor
(`prev_topic_id`) of a stage link.

## Decision

### 1. One rebuilt cohort, `lovable-operational`, from one snapshot

One reviewed **rebuild package** (format 2 of 0191's package) binds one read-only
snapshot and lists every topic of the population with an **action**:

- **CREATE** — an operational topic no Juristid Teema records (by Lovable UUID)
  and whose reference no Teema holds: a new native Teema, as 0191 writes one.
  Ongoing topics are open; finished ones are created closed (ARCHIVE, a
  disposition, no `closed_at`, no closure event — 0148 §5); a predecessor is
  created closed and continues under its successor.
- **REBASE** — a pilot Teema (0191) whose recorded Lovable UUID is in the
  snapshot: the **same** Teema is brought to the snapshot in place (§3). Its
  identity, history, documents and local work stay.
- Not in the package: a topic whose reference a Teema holds **without** a recorded
  Lovable identity (an identity decision for a person, §8), and every Teema that is
  not Lovable-derived (§4).

The cohort key is the `EvidencePilotRecord.pilot` of every record the rebuild
writes. The pilot's own records (`lovable-pilot`) are immutable and stay.

### 2. Readings, never guesses

Every rule of 0191 holds for CREATE, with the stricter reading the owner asked for:
an accurate minimal Teema beats an invented complete one.

- «ei saatnud» on a live stage: open at the source's stage, held for review;
  never concluded, never SENT. Its past opinion deadline is held too.
- A next step the reviewer cannot read safely (no kind, a wording-only or year-off
  date, an ambiguous instruction, a DO already past): no current step, held.
- WAIT / MONITOR on Lovable's structured day, REVIEW_ON — never a deadline.
- An open request only when open, nothing sent and the day not before **today**.
- An estimate stays an estimate; a known commencement only on an adopted act.
- No SENT opinion, document, meeting, activity, closure event or backdated audit.
- Continuations only through Lovable's structured stage link, and only between
  topics of the package (CREATE or REBASE).

A held field is `HELD_FOR_REVIEW` in the topic's `field_decisions`, with the
reason in `review_notes`.

### 3. Rebasing a pilot Teema: three values, a decision for every conflict

Per field the rebase knows **A** (what the pilot import wrote, from its immutable
provenance and the pilot's sync baseline), **B** (Juristid now) and **C** (the
snapshot, as the reviewer read it).

| | |
|---|---|
| C = A | nothing to do (also when B ≠ A: the lawyer's value stays) |
| C ≠ A, B = A | **WRITE** C through the domain service — Juristid still holds exactly what the import wrote |
| C ≠ A, B ≠ A | **reconciliation decision** in the package: `KEEP_LOCAL` (default) or `TAKE_SOURCE`, with the reviewer's reason; nothing is overwritten without one |
| C not safely readable | **HOLD** — no write, held for review |

Fields the rebase may write, each through its service with `actor=None` and a
`LOVABLE_REBUILD` provenance naming the package, snapshot and plan: the title
(`set_matter_title`), the owner (`assign_matter`), the current step
(`set_next_action`, the pilot's step superseded — never deleted), a stage that
does not end the work (`change_stage`), an open request
(`request_response_deadline`), and an added commencement. **A stage that ends the
work** («jõustunud», «rohkem ei tegele») closes the Teema in Juristid (0131 §10)
and is therefore always a reconciliation decision (default `HOLD`); a closure is
never inferred. A changed or removed commencement, `valja` / `valja_kuupaev`,
`kellele`, `oigusakt`, `sisse` and `koja_ettepanek` are held. Every rebased Teema
gets a new canonical `MatterSourceReference` for the snapshot (the pilot's stays):
that is the Teema's new provenance and carries the OneNote link byte for byte.

### 4. What must not change, proven per Teema

- **Not Lovable-derived** (the evidence pilot's Teemad, native Teemad, anything
  else outside the package): the full per-Teema digest (every related table)
  before and after, inside the transaction and again afterwards. Must be equal.
- **Rebased**: a **local digest** — Valdkond, collaborators, tags, documents,
  entries, personal notes, submissions, every human-authored change event, and
  every related row the rebase does not declare — must be equal; and every field
  the rebase did change must be exactly a change the approved plan declared
  (`lovable_pilot preserved` reports both).

Nothing is deleted. The irreversible operations (listed in the runbook) are: a
superseded step, a recorded stage period, a moved reference sequence, the new
Teemad, and (only on a `TAKE_SOURCE`/close decision) a closure. The rollback is the
verified backup set taken immediately before, restored by the documented
procedure and rehearsed on a clone before production is touched.

### 5. One apply, every gate

One transaction under the reference-sequence and table locks; the plan rebuilt
under the lock must hash to the approved plan digest, which binds every B the
rebase relied on (a lawyer's save after planning changes B and refuses the apply);
`verify` before commit; `JURISTID_LOVABLE_PILOT`, `REAL_DATA_ALLOWED`, the
operator intent (the cohort key), the package and plan digests and a backup set
are required. `source-drift` (GET only) reports what Lovable changed after the
snapshot; drift does not invalidate the package — the first night reports it.

### 6. One baseline for the nightly comparison

`LovableSyncCohort` (migration `legacy_import` 0021) records the enrolled
`lovable-operational` cohort: its package digest and size (CREATE + REBASE).
`lovable_sync enroll --cohort lovable-operational --supersede lovable-pilot`:

- archives each rebased pilot state's baseline and last observation in
  `LovableSyncBaselineArchive` (immutable; the run and decision ledger stays as it
  is) and re-seeds the state from the rebuild's provenance — A is the snapshot;
  what Juristid kept (a lawyer's value, a held field) is seeded `import_held` or
  as a local value, so it is reported, never silently "converged";
- seeds every CREATE Teema from its import;
- retires the pilot cohort (its row says when and by which rebuild), so the
  nightly size check expects the rebuilt cohort only.

Thereafter the nightly run is the 0192 run over one cohort: COMPARE only;
`IMPORT_HELD` fields reported every night until a person resolves them; source
activity (ongoing, finished, continues, not operational, missing) counted; a
Teema joining is «Lisandus võrdlusesse», never a Lovable change. APPLY, its
allowlist and its breakers are unchanged and off.

### 7. OneNote

Not read. A Lovable OneNote link is kept as a plain source reference.

### 8. Left for a person

The 13 ongoing topics whose reference a Teema without recorded identity holds (12
evidence-pilot Teemad, the same topics by title and date; one genuine collision,
2026_241); every reconciliation decision of §3; every held field.

## Consequences

- One population, one snapshot, one baseline; the pilot's evidence kept.
- Migration `legacy_import` 0021 (additive): `LovableSyncCohort`,
  `LovableSyncBaselineArchive`, `LovableSyncState.cohort` with a database default.
- APPLY stays off; its activation is a separate owner decision.
