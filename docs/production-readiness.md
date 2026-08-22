# Production readiness — the gates, in order

Everything the product needs before and during a real-data production step, in
one place. It exists because the sequence was assembleable only from four ADRs
and a deployment README, and the person doing it at 19:00 on a Friday is exactly
the person who should not have to assemble it.

**This is a checklist, not a script, and deliberately so.** Each numbered gate
is a place a human decides whether to continue. A
`deploy-and-import-everything.sh` would remove precisely the review points that
make a consequential data operation safe (ADR 0022).

Nothing here is a new procedure. Every command below already exists and is
documented where it lives; this sequences them and says what each one proves.

---

## 0. Before anything

| | Check | How |
| --- | --- | --- |
| 0.1 | The commit is reviewed and green | `gh pr checks` on the merge commit; CI is the only full verifier |
| 0.2 | The commit id is known and written down | Deploy a commit, never a branch (`scripts/deploy/juristid-deploy-preflight.sh`) |
| 0.3 | The migration plan has been read | `manage.py migration_plan` — before applying, not after |
| 0.4 | The running build is what you think it is | `manage.py deployment_readiness` |

## 1. Code and schema

Schema deployment is separate from every data operation below it, and stays
separate. A migration that lands with an import is a migration nobody can roll
back independently.

| | Step | Command |
| --- | --- | --- |
| 1.1 | Preflight | `scripts/deploy/juristid-deploy-preflight.sh` |
| 1.2 | Back up first | `scripts/deploy/juristid-backup.sh` |
| 1.3 | Build, migrate, replace | `deploy/unraid-main/README.md` §"Deploying a new build" |
| 1.4 | Post-flight | `manage.py deployment_readiness`, then the A–L browser list in the same README |

## 2. Backup — before any data change, not after

| | Check | How |
| --- | --- | --- |
| 2.1 | A fresh set exists | `scripts/deploy/juristid-backup.sh` |
| 2.2 | It is a set, not a directory of hopes | `scripts/deploy/juristid-verify-backup.sh --set DIR --level 2 --compose-file …` (level 1 is checksums only) |
| 2.3 | The evidence tree is in it | The backup refuses a data root with no evidence tree — that refusal is the check |
| 2.4 | Canonical state is recorded | `manage.py recovery_fingerprint --out before.json` |
| 2.5 | Restore has been rehearsed | The CI "Backup and restore rehearsal" job, on every commit |

`recovery_fingerprint` covers **every** canonical holder of evidence bytes:
`DocumentVersion` and `OpinionArchiveBinary` alike, plus the legacy source tree
(`app/documents/references.py`). A restore that lost the opinion archive fails
the comparison rather than passing it.

## 3. Data operations — each one an explicit, separate decision

Every one of these has a plan phase that writes nothing and an apply phase that
commits. Run the plan, read the aggregate output, then decide. None of them is
implied by a deployment.

| | Operation | Plan | Apply | Owner decides |
| --- | --- | --- | --- | --- |
| 3.1 | Register import | `import_legacy_register --dry-run` | `--apply` | Source snapshot hash |
| 3.2 | Current portfolio | `final_register_cutover --snapshot <sha> --dry-run` | `--apply` | The snapshot digest must be in `REVIEWED_SNAPSHOT_SHA256` |
| 3.3 | Historical cutover | `historical_cutover_state --cutover-year 2026 --dry-run` | `--apply` | The cutover year is a reviewed constant |
| 3.4 | Opinion archive catalogue | `opinion_archive plan --opinions … --expect-archive-sha256 …` | `opinion_archive apply` | Archive digest |
| 3.5 | Opinion archive bytes | `opinion_archive materialize-plan` | `opinion_archive materialize` | Holding bytes is not filing them |
| 3.6 | Opinion canonical records | included in 3.4's plan | `opinion_archive apply` | Only automatic classes file themselves |
| 3.7 | Archive text and search | `opinion_archive_search status` | `extract-text`, then `rebuild` | Extraction is **BLOCKED** where real data lives (ADR 0014) |
| 3.8 | Second-pass proposals | `opinion_archive content-plan` | `content-apply` | Proposals only; nothing files (ADR 0023) |

**3.5 and 3.6 are different acts.** Materialising holds a letter's bytes;
applying decides whose letter it is. Tying them together is what left two thirds
of the corpus visible only as catalogue rows.

### A schema change to a derived table

Adding a column to `CurrentRegisterState` is a schema change whose *correct
values* come from a rebuild, not from a default. `opinion_sent_recorded`
(migration `legacy_import.0011`) is the worked example: it defaults to `False`,
and until the derived state is rebuilt from the approved snapshot every current
Matter reads as still being drafted.

The ordering that follows from that, and from the same rule for any future
derived column:

| | Step |
| --- | --- |
| 3.9.1 | Back up |
| 3.9.2 | Build the target image |
| 3.9.3 | Apply the migration with the target image |
| 3.9.4 | **Rebuild the derived state before the new web container serves traffic** — `final_register_cutover --snapshot <approved> --apply`, which is idempotent and, on an already-reconciled portfolio, performs ACTIVATE 0 / RETIRE 0 and only rewrites derived rows |
| 3.9.5 | Verify the derived table before exposing it: row count, CURRENT count, drafting count |
| 3.9.6 | Replace web and extractor |
| 3.9.7 | Verify the dashboard reads what the cutover reads |

Doing 3.9.6 before 3.9.4 shows every reader a wrong number for as long as it
takes to notice.

## 4. Verify — after every data operation

| | Check | Command |
| --- | --- | --- |
| 4.1 | Counts reconcile | the operation's own `status` / `verify` phase |
| 4.2 | Evidence is present and is what was hashed | `manage.py check_evidence_integrity --verify-sha` |
| 4.3 | Nothing is holding bytes nobody references | `manage.py prune_orphaned_evidence` (no `--delete`) |
| 4.4 | Search is complete, current and not stale | `manage.py check_search_integrity` |
| 4.5 | Archive search matches what is held | `manage.py opinion_archive_search verify` |
| 4.6 | Era contracts still describe the workbook | `manage.py check_era_contracts` |
| 4.7 | Canonical state changed the way the plan said | `manage.py recovery_fingerprint --compare before.json` |
| 4.8 | It is usable | the A–L browser list in `deploy/unraid-main/README.md` |

`check_search_integrity` reports stale indexed text as well as missing rows:
renaming an Organisation or a Tag changes what every Matter projects and
deliberately triggers no fan-out, so the rebuild is owed and the check is what
says so. Answer it with `rebuild_search_index`.

## 5. Rollback

| | Situation | Answer |
| --- | --- | --- |
| 5.1 | The build is wrong | Redeploy the previous commit — `deploy/unraid-main/README.md` §"Rolling back" |
| 5.2 | A migration is wrong | Restore; a schema change is not undone by redeploying code |
| 5.3 | A data operation is wrong | Restore from the set taken at gate 2, then re-plan |
| 5.4 | Evidence is missing | `check_evidence_integrity` names the rows; restore the evidence tree |
| 5.5 | Search is wrong | Rebuild. It is derived, and rebuilding it is not a recovery event |

The distinction in 5.5 is the one worth internalising: **derived state is never
a reason to restore.** `REBUILDABLE_MODELS` in `app/core/deployment.py` is the
authoritative list — today `SearchDocument`, `DocumentDerivative`,
`OpinionArchiveSearchDocument` and `OpinionArchiveText` — and it is what a
restore comparison is allowed to find empty. Everything else is canonical until
somebody argues otherwise, which is the safe default rather than an assessment
of each table.

## What is still outstanding

Facts, not plans — see `docs/open-decisions.md` for the decisions themselves.

- the current-register production apply has **not** been run against the real
  instance by any development task;
- the opinion archive has **not** been materialised in production;
- no canonical opinion apply has been run in production;
- real-archive text extraction is blocked pending the Secure Pilot Gate
  scanner, so production archive search is metadata-only by design;
- off-host disaster recovery has no destination yet, and no RPO/RTO is agreed.
