# 0238 — Lovable parity is checked by the independent oracle over the stored reading, and never writes

Date: 2026-11-28 · Status: Implemented in the r98 candidate (R98-12, R98-13). **Not active in
production**: needs release 98 and migration `legacy_import/0034`. The verifier is an operator command; no
schedule is added.

Builds on 0213 (Lovable is the source of truth for its owned fields) and the contract suite's oracle
(audit §D.2). The 210-field manual comparison of the readiness review becomes a repeatable capability.

## Decisions

1. **The oracle moved into the application** (`app/legacy_import/parity/oracle.py`, `vocabulary.py`), with
   import aliases left in `tests/lovable_contract/` so every existing test is unchanged. Its independence
   rule (I-6) is kept and now guards the new location: it may import only its own package, never
   `lovable_sync`/`lovable_reading`, and I-7 (synthetic data only) still scans both files.
2. **`verifier.py`** gives the oracle a source view built from the newest SCHEDULED `LovableSyncSnapshot` —
   evidence the nightly cycle already holds — so routine verification makes **no request to Lovable**. It
   names the oracle's verdicts in the brief's words: MATCH; EXPLAINED_DIFFERENCE (a value Juristid cannot
   represent by the approved contract); HELD (a closure held for Juristid-only work, recorded); MISMATCH;
   UNVERIFIED (not compared: parity over nothing is no evidence). «Clean» means no mismatch **and** no
   unverified Teema.
3. **`manage.py lovable_parity [--sample N --seed S] [--record]`**: whole cohort or a reproducible sample
   (a seeded hash ordering, same result on any machine). Exit 0 clean, 1 a mismatch/unverified, 2 no
   reading. `--record` keeps `LovableParityReport`: counts and **(reference, field) pairs only** — never a
   title, a name or a step. The verifier only reads (tested by capturing its SQL: no INSERT/UPDATE/DELETE).
4. **Haldus**: the overview carries one line for the latest report (none yet → unknown; a mismatch →
   attention). The run, hold and limit diagnostics of Haldus → Lovable already exist and are unchanged:
   ordinary limit 100, handover limit 150, no breaker is weakened, no hold is resolved.

## What this does not prove

* It compares Juristid with **the reading the cycle took**. A Teema edited in Lovable afterwards is not a
  failure of the previous night, and this command cannot see it; a fresh independent reading is a separate,
  operator-triggered step that is not built here (it would add Lovable traffic, which the brief forbids as a
  routine). The report carries the reading's timestamp and digest so the claim is exactly «as of that reading».
* Juristid-side changes after the run (a person editing a Lovable-owned field before the next APPLY) show as
  MISMATCH; the page cannot tell them from a failed write without the audit trail.
* The 2026_13 reopening/commencement-ordering anomaly is **not** assumed resolved: the first scheduled
  verification of r96 is read by the deployment agent, and any defect it confirms is a separate correction.
