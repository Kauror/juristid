# 0202 — The active Lovable register joins as a second reviewed cohort

- Status: Proposed (owner's brief «Expand the active Lovable register and nightly
  monitoring», 2026-11-03)
- Extends: 0191 (the 100-Matter pilot import), 0192 (its nightly one-way
  synchronisation), 0201 (wider read-only observation), 0184 (import approvals)
- Relates to: 0157 (Lovable mirror, PR #429 — still a draft, not merged, not
  revived by this), 0148 (Excel pilot rules), 0120 (informational commencements)

## Context

On 3 November 2026 Lovable's dashboard counted **222 ongoing topics**. 76 of
them are represented in Juristid — 63 by the pilot of 0191 through their
recorded Lovable identity, 13 only by an equal reference number with no
recorded identity (12 written by the evidence pilot of 0173, one created
natively) — and **146 are missing**. The owner wants every ongoing topic in
Juristid, and the nightly comparison of 0192 to follow the new ones too, still
**COMPARE only**.

"Ongoing" is Lovable's own business classification, reproduced exactly: a live
topic with `operatiivne_ulatus`, whose `hetkeseis` is neither `jõustunud` nor
`rohkem ei tegele`, and which is not the predecessor (`prev_topic_id`) of a
stage link — a topic whose work continues under another is not current work.
On 3 November that is 230 − 8 = 222, the dashboard's figure.

## Decision

### 1. One importer, a second cohort

The 0191 importer (`app.legacy_import.lovable_pilot`) is extended, not copied.
A package may name its **cohort**: `lovable-pilot` (0191, the default; its
package and digest are unchanged and still verify) or `lovable-active` (this
record). The cohort is the `EvidencePilotRecord.pilot` of every record the
package writes, the operator intent typed at `apply`, and the key the nightly
synchronisation enrols. Every 0191 guarantee holds for both: a reviewed package
bound by digest to a read-only snapshot, every reference free (native, imported,
archived or permanently deleted), one transaction under the reference-sequence
and table locks, the plan rebuilt under the lock and matched to the approved
digest, `verify` before commit, `actor=None`, no backdated audit, the backup
set named, `JURISTID_LOVABLE_PILOT` and `REAL_DATA_ALLOWED`.

`lovable-active` packages are format version 2. Version 1 packages read exactly
as before.

### 2. Who is selected

Every ongoing topic whose Lovable identity no Juristid Teema records and whose
reference no Teema holds. A topic whose reference is taken by a Teema without
a recorded identity is **not** imported and **not** enrolled: it is listed as
needing a person's identity decision (§7). A predecessor is never imported as
current work.

### 3. What changes from 0191's reading

0191 left out topics it could not read cleanly. The owner's rule now is that
every ongoing topic is either in Juristid or documented as blocked, and that an
**accurate minimal Teema beats an invented complete one**. So:

- **«ei saatnud» on a live stage** (0191 §4 left these out): imported open, at
  the source's stage. `valja` is a source assertion, kept as source only; it
  neither concludes the Teema (0148's rule is for a finished stage) nor creates
  a SENT opinion. The field is **held for review**.
- **A next step the reviewer cannot read safely** (no kind, an ambiguous
  instruction, a DO whose date has passed, a date only in the wording): the
  Teema is imported with **no current step**; `jargmiseks` and
  `tegevuse_tahtaeg` stay in the source reference and are held for review.
- **A WAIT or MONITOR whose review day has passed**: written as 0191 writes it
  (REVIEW_ON Lovable's day) — a review that has come round (0195), never a
  deadline and never overdue.
- **An opinion deadline**: an open request only when the Teema is open, nothing
  was sent and the day is not before the snapshot; any other
  `arvamuse_tahtaeg` is history, source only (0191 §4). A past day with
  nothing sent is held for review, never an overdue obligation.
- **An unmapped act or stage**: left empty, the source value kept and held.
- **Commencements** exactly as 0191 §3: an estimate stays an estimate.
- **Continuations**: written only where Lovable's structured stage link proves
  both identities **and** both Teemad are written by the same package. A link to
  a Teema already in Juristid is source text only — nothing existing is
  changed.

A held field is recorded per topic in the package's `field_decisions` as
`HELD_FOR_REVIEW`, with the reviewer's reason in `review_notes`.

### 4. Nothing existing changes, and that is proven per Teema

Besides 0191's fingerprint, `apply` and `dry-run` take a **per-Teema digest of
every pre-existing Teema and the records hanging off it** (the Teema row, its
instruments and Valdkond, steps, dates, source references, pilot records,
synchronisation state and decisions, and its change-event and security-event
identities) before and after the writes, inside the transaction, and refuse on
any difference. `lovable_pilot preserved --before <file>` repeats the check
afterwards, outside the transaction, against the digest file the apply wrote.

Juristid-only data — **Valdkond**, local notes, actions, documents,
classifications — is never written by the import and never by the
synchronisation; a source value that is empty never clears it.

### 5. Source drift

`lovable_pilot source-drift --package` reads, through the synchronisation's
GET-only transport and service login (0192 §2, 0201 §1), the version of every
package topic and lists those Lovable changed after the snapshot. Drift does
not invalidate the package — the first nightly comparison sees each such change
as a source change against the import baseline (§6) and reports it — but it is
printed before approval and at apply.

### 6. The synchronisation follows approved cohorts

`LovableSyncCohort` (migration `legacy_import` 0021) records each **enrolled**
cohort: its key, the package digest whose provenance defines it, the number of
Teemad it must hold, when and why it was enrolled. The pilot needs no row: it is
the built-in cohort of 0192, 100 Teemad, unchanged. `lovable_sync enroll
--cohort lovable-active --package-sha256 … --expect …` enrols an imported cohort
after its import has verified; until then its Teemad are not synchronised.

- The run's population is the union of enrolled cohorts, found only through
  approved provenance (`EvidencePilotRecord(pilot=<cohort>, package_sha256=…,
  record_kind="MATTER")` and the canonical Lovable `MatterSourceReference`). A
  Teema is never enrolled because its reference matches.
- `LovableSyncState.cohort` says which cohort a state belongs to; the pilot's
  100 states are `lovable-pilot` by default and are not re-seeded, so their
  baselines and reconciliation history are untouched.
- A new state's baseline (A) is seeded from its import snapshot, exactly as
  0192 §4 seeds the pilot's — so a Lovable change made before the import is
  never reported as a Juristid update, and one made between the snapshot and the
  import is reported as a source change.
- **An import-held field** (`HELD_FOR_REVIEW`) is seeded with `import_held`
  and reported `REVIEW_REQUIRED` / `IMPORT_HELD` every night until a person
  resolves it with `lovable_sync resolve` — the import's review queue lives in
  the same place as every other held change, under the same permissions.
- The size check is per cohort: a run whose cohort does not hold exactly its
  recorded number of Teemad stops.
- Each run also classifies every state's source **activity** — ongoing,
  finished, continuing under another topic, out of operational scope, missing —
  and counts open Teemad whose source stopped being ongoing. Counts only; the
  stage stays held (no approved stage mapping, 0191).
- Run counts, Haldus, Uuendused → Lovable, `lovable_sync report` and the MCP
  tools show the population per cohort. A Teema joining the cohort is not a
  Lovable change: Uuendused → Lovable shows it as entering the comparison, never
  as a field edit.
- Mode, breakers and APPLY's rules are unchanged; nothing here switches APPLY
  on or widens what APPLY may write.

### 7. What is left for a person

- The 13 ongoing topics whose reference a Teema without a recorded identity
  holds. Linking one is an identity decision (is that Teema this topic?) and
  is out of scope: they stay as they are, outside the synchronised cohorts, and
  observation keeps showing them as reference candidates (0201 §3).
- Every held field (§3), as `IMPORT_HELD` items.
- The pilot topics Lovable has since finished while Juristid keeps them open —
  already reported nightly as held stage changes.

### 8. OneNote

Not read. An `https://` OneNote link Lovable holds is kept byte for byte as a
source reference (0191 §6); nothing is fetched, parsed or attached.

## Consequences

- Juristid holds every ongoing Lovable topic or a documented reason why not.
- The nightly comparison covers ~246 Teemad in two identifiable cohorts; the
  pilot's 100 baselines and history are unchanged.
- One additive migration (`legacy_import` 0021: a table and a column with a
  database default).
- APPLY stays off; its activation remains a separate owner decision.
