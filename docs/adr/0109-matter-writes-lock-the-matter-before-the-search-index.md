# 0109 — Matter writes lock the Matter before the search index, and inline whole-value editors carry the version of their own value

**Status:** accepted
**Date:** 2026-09-24

**Amends ADR 0040 §3 and extends ADR 0104 §1 to the editors it did not list.**
Everything else in both stands.

Three findings from the engineering audit, one mechanism (ENG-027, ENG-028,
ENG-029). All three were reproduced on `7a626158` with forced interleavings on
real PostgreSQL connections before anything changed.

---

## 1. `FOR NO KEY UPDATE` on closure and on a new evidence version (ENG-027)

ADR 0040 §3 moved the Submission lock to `FOR NO KEY UPDATE` because a plain
`FOR UPDATE` held across a `post_save` search refresh closes a cycle with a
running rebuild: the rebuild holds the gate exclusively and needs `FOR KEY SHARE`
on the row at COMMIT for the projection it re-inserted, while the writer holds
the row and waits for the gate.

Two more writers had the same shape:

* `close_matter` locked the Matter at `FOR UPDATE` and then saved it, and the
  save refreshes the Matter's projection;
* `add_evidence_version` locked the Document at `FOR UPDATE`, and saving the new
  current version refreshes the document's fragments — which reaches the gate
  whenever an earlier version already has ACTIVE extracted text.

**0040 §3's sentence that `add_evidence_version` "never asks for the rebuild
gate, so there is no cycle to close" was true only for documents nobody had
extracted.** Both now lock at `FOR NO KEY UPDATE`. That mode conflicts with
itself and with `FOR UPDATE`, so two closures, a closure and a reopen, a closure
and a business write, and two uploads to one document still take turns; it
stops blocking the rebuild's `FOR KEY SHARE`.

Measured before: against a paused rebuild, both a closure and a new version of
an extracted document deadlocked, with the rebuild as the victim. After: both
complete, the rebuild completes, and the index is sound.

**Not changed, reported instead.** `set_next_action` still locks the Matter at
plain `FOR UPDATE`, but a `NextAction` save refreshes no search projection, so it
never reaches the gate. The legacy import commands (`final_cutover`,
`historical_cutover`, `current_register`, `owner_backfill`,
`next_action_enrichment`, `onenote_policy_areas`, `register_outreach`) do the
same inside operator-run transactions. Neither was demonstrated to deadlock, and
this ADR does not widen the change on static reading alone.

## 2. The set editors lock the Matter first (ENG-029)

`set_policy_areas`, `set_organisations` and `set_tags` wrote the join table with
`.set()`, which fires the search refresh **before** the Matter row is saved. The
refresh deletes and re-inserts the Matter's `SearchDocument`, so two overlapping
saves of one Teema took the projection row and the Matter row in opposite
orders:

* Valdkonnad against Lühikokkuvõte: deadlock;
* Valdkonnad against Valdkonnad: both deleted the same projection row, and the
  second re-insert hit `search_one_document_per_source_object`.

Either way one lawyer's business write rolled back.

Each of the three now begins with `lock_matter_for_write` (`app/matters/locks.py`)
— the Matter at `FOR NO KEY UPDATE` — and reads what it compares from the locked
row. Every writer of a Teema therefore queues on the Matter before touching the
projection, which is the order the scalar setters already had. The lock is in
the services, so `Muuda teemat` and every other caller get it, not only the
inline endpoints.

Rejected: an advisory lock per Matter (still deadlocked in the audit's
measurement, because the row locks remain), retrying on `IntegrityError` (would
replay a non-idempotent business write), and upserting the projection (Django
cannot target the partial unique index, and it would hide rather than order the
race).

## 3. Inline whole-value editors carry the revision of their own value (ENG-028)

ADR 0104 gave a token to every editor that posts a whole *record*. The inline
Valdkonnad, Saatja and Lühikokkuvõte editors each post a whole *value* — the
full set, the full paragraph — and had none, so a second tab silently reverted
a colleague's save. So did the three structured-fact editors (Oluline tähtaeg,
Jõustumine, Töövõit), which no Teema page links to since `af7ace0` but whose
routes still work.

**The inline editors' token is the value's own digest, not the Matter's
`updated_at`.** `matter_field_revision` is a SHA-256 over the sorted primary
keys of the set, or over the summary text. The reason is the page, not the
database: Valdkonnad lives in the header, Saatja in the rail and Lühikokkuvõte
under the meta line, and each re-renders only its own fragment after a save. A
token that moved on every Matter write would make the Saatja box stale the
moment the same person saved Valdkonnad, and refuse them in their own tab — the
opposite of what ADR 0104 is for. The property that matters is 0104's: the
editor must not overwrite a value *of its own field* different from the one it
showed. That value is what the token is.

`guard_matter_field_revision` takes the Matter at `FOR NO KEY UPDATE` — the same
lock §2 takes — and compares under it, so one lock both decides that the save is
stale and serialises it against the write it would have overwritten. **A missing
token is a conflict for these three forms**, unlike `guard_matter_revision`,
because they always render one: a POST without it came from a page older than
the guard.

The fact editors use their row's `updated_at`, under a `FOR NO KEY UPDATE` lock
on the fact, with the status re-checked on the locked row. Their services keep
`expected_revision=None` as "no form was rendered", so importers and tests are
unchanged; the edit views always pass the form's value.

A refusal answers **409**, writes nothing and says so where the control is. The
set editors re-render their surface from the stored value, so the colleague's
set is what the person now sees. The summary and the fact editors keep what the
person typed and move the token to the stored version, so a second press — made
having seen both — is a decision rather than another refusal. That is what
`Muuda teemat` already does (`matter_edit`). The rail printed no refusal at all
before this; it prints the same `formerror` the header does.

## Migrations

**None.** No schema change, no data migration, and no search-index version
change: what is indexed is unchanged, only the order in which it is written.
