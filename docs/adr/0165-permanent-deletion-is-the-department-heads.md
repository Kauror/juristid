# 0165 — Permanent deletion of a Teema is the department head's

**Status:** accepted — owner decision D6 of the QA remediation round, 2026-10-14
(independent QA finding DC-04).
**Date:** 2026-10-14

**One migration, choices only** (`audit/0037_matter_permanent_deletion_events`:
two `SecurityEventType` values). No schema change to any business table, no data
migration, no backfill, no search rebuild, `INDEX_VERSION` unchanged.

Amends [0096](0096-uus-teema-is-the-master-and-a-teema-can-be-deleted.md) §4.3,
§4.5 and §4.6. The purge itself — the ownership walk, the refusals of §4.4, the
tombstone, the lock and the post-commit storage cleanup — stands.

## Context

ADR 0096 §4.5 gave deletion to the same cohort that may edit a Teema
(`business_write_required`: SPECIALIST and DEPARTMENT_HEAD), on the argument
that no narrower permission over destructive Matter operations existed. QA
(DC-04) measured the consequence: any specialist could delete a Teema, and with
it the final evidence of the Chamber's *sent* opinions — the one class of record
the product otherwise treats as immutable.

The owner's decision (D6) keeps deletion, including Teemad with sent opinions
and final evidence, and narrows who may do it.

## Decision

### 1. What may be deleted

Unchanged from 0096 §4: everything the Matter owns, sent opinions and their
final-evidence bytes included, unless one of §4.4's refusals applies (legal
hold, corrected entry, successor, outside reference, straddling row).

### 2. Who — an explicit capability, not a role check

A new business capability, `matter.delete_permanently` «Teema jäädav
kustutamine» (`app.accounts.enums.Capability.DELETE_MATTERS`), resolved through
`app.core.authorization.has_capability` like every other:

* **default holder:** DEPARTMENT_HEAD only;
* **grantable by name** to a SPECIALIST (or withdrawn from a department head)
  through the existing account administration, as an individual override;
* **requires business write** (`REQUIRES_BUSINESS_WRITE`): a READER or a
  technical ADMINISTRATOR holds nothing even with an explicit grant, and the
  account-administration capabilities add nothing. Technical administration is
  not business authority (AGENTS.md; ADR 0005).

"A role explicitly granted the same capability" in D6 is implemented as this
individual grant, which is the repository's one mechanism for widening a
capability beyond its role default (ADR 0145 §3). No role other than
DEPARTMENT_HEAD carries it by default.

`app.matters.deletion.may_delete_permanently(user)` is the predicate; the route
(`matters:matter_delete`), the Teema header's «Kustuta» and the edit page's
«Teema kustutamine» section all ask it. Hiding the control is presentation; the
route refuses on its own:

| Actor | GET / POST `/teemad/<pk>/kustuta/` |
|---|---|
| anonymous; shared-gate visitor without a persona | redirect to sign in / choose a persona |
| READER, ADMINISTRATOR (granted or not) | 404 (`business_write_required`) |
| SPECIALIST without the grant, DEPARTMENT_HEAD with a deny | 403; a POST also writes `ACCESS_REFUSED` |
| DEPARTMENT_HEAD, SPECIALIST with the grant | the confirmation; a restricted Matter they may not read stays a 404 |

`permanently_delete_matter` — the only product caller of `delete_matter` —
re-checks the capability, so the service refuses a specialist too.

### 3. The flow

«Kustuta teema» → the confirmation dialog → «Seda tegevust ei saa tagasi
võtta.» → «Loobu» / «Kustuta jäädavalt».

The dialog is the server-rendered confirmation step at `/teemad/<pk>/kustuta/`,
marked `role="alertdialog"` with its title and warning as accessible name and
description, `Loobu` holding the initial focus. It is deliberately **not** a
script-opened modal and not a `confirm()` (0096 §4.3): the server must bind the
answer to what was shown (§4), and the question must exist without scripting.
It names the Teema and counts what goes — entries, steps, kaasamised, positions,
notes, overviews, opinions *and how many of them were sent*, documents, stored
files — and says the sent opinions go with their final files. It says nothing
about backups: a backup copy follows its own retention and the dialog must not
imply otherwise.

### 4. Not by GET, not twice, not on a stale confirmation

* The GET writes nothing; a `confirm_digest` on a GET is ignored.
* `DeletionPlan.digest` is a SHA-256 over the ids of every row the plan would
  remove, per model, and every evidence key. The dialog posts it back;
  `delete_matter` rebuilds the plan under the Matter's row lock and refuses a
  different digest with «Teema sisu muutus pärast kinnituse avamist…» (409),
  re-rendering the dialog with the new counts and digest. A note deleted and
  another added changes it although no count moves. Derived data — the search
  projection, `DocumentDerivative`, `DocumentTextFragment` — is left out, so a
  search refresh or an extraction run finishing while the dialog is open does
  not refuse the person. Retained audit rows are left out too.
* A POST without a digest is refused (400).
* A repeated POST finds no live Matter (404); a second service call on the
  tombstone returns an empty plan. Nothing is deleted or audited twice.

### 5. What stays, and what it says

The tombstone of 0096 §4.2 stays (the audit trail is `PROTECT` onto it), but it
**stops saying what it was about**: on deletion its `title` becomes «Kustutatud
teema» and `brief_summary`, `position_summary`, `rationale_summary`,
`disposition_reason`, `policy_area_other` and `legal_instrument_other` are
emptied. The reference (`YYYY_N`) stays as the identifier the audit names.

Two audit rows are written in the deleting transaction, both minimal:

* `ChangeEvent MATTER_DELETED` — summary «Teema kustutati jäädavalt»; payload
  the reference, per-kind counts and per-model row counts. It **no longer
  carries the title** (0096 wrote `title` into both summary and payload).
* `SecurityAuditEvent MATTER_DELETED` — actor, time, subject Matter id, IP and
  user agent; detail the reference and counts (documents, opinions, sent
  opinions, kaasamised, entries, evidence and derivative objects).

Neither holds a title, summary, file name or note text.

**Residue the decision cannot remove:** the Matter's earlier `ChangeEvent` rows
and any `ImportRowLedger` rows are append-only by database trigger and remain
under the tombstone with whatever their summaries and payloads recorded at the
time (some name a title or a changed value). Rewriting them would break the
audit guarantee 0096 §4.1 rests on; it is not done. **Amended 2026-10-14 (owner
review B):** the confirmation says so — «Turvalogisse jääb kustutamise kirje
(kes, millal, viitenumber ja kogused); varasemad logikirjed säilivad muutmata.»
— rather than promising absolute erasure, and
`tests/test_permanent_deletion_residue.py` proves the residue is reachable
from no ordinary surface (register, search, Teema URLs, files, work and
department pages, Statistika figures, drill-throughs and exports, related
Teemad).

### 6. Evidence bytes

Unchanged from 0096 §4.6: removed only **after the commit**, so a rollback never
leaves rows pointing at missing bytes. New: before deleting a key the cleanup
asks whether *any* remaining row still names it (`EVIDENCE_REFERENCES` for
evidence, `DocumentDerivative` for derivatives); if so the object stays. Version
keys are minted per version today, so this guards the opinion archive's
hash-deduplicated objects, restores, imports and future holders. Cross-Matter
dependencies (another Matter's submission on a version here, a background
citation, an e-mail attachment link) remain §4.4 refusals.

### 7. Failure recovery

Every row is deleted in one transaction; any failure rolls all of it back and no
byte has been touched. If a byte delete fails *after* the commit, the database
is already consistent (nothing references the object); the failed keys are
written to one `SecurityAuditEvent MATTER_DELETION_STORAGE_PENDING`
(`succeeded=False`, keys only) and `prune_orphaned_evidence` /
`prune_orphaned_derivatives` reclaim them on their next run.

## Consequences

* Specialists lose the «Kustuta» link in the header and the «Teema kustutamine»
  section on «Muuda teemat». Visual baselines that showed them as a specialist
  change; the confirmation baseline is now captured as the department head.
* The capability appears in account administration («Teema jäädav
  kustutamine») and can be granted to a specialist by an account administrator.
* Existing tombstones keep the title they were deleted with; nothing is
  rewritten retroactively.

## Alternatives rejected

* **Role check `is_department_head`.** D6 asks for "a role explicitly granted
  the same capability"; a hard-coded role cannot be granted.
* **"Administrator implies".** Technical administration is not business
  authority, and D6 excludes it by name.
* **A script-opened `<dialog>`.** It cannot carry a server-verified binding to
  what was shown without the same round trip, and it vanishes without
  scripting.
* **A pending-deletion table for failed byte deletes.** A schema change for what
  the orphan pruners already find; the security event records it for the
  operator.
