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
| 0.3 | The migration plan has been read | `manage.py migration_plan`, from the target image, before applying rather than after |
| 0.4 | The running build is what you think it is | `manage.py deployment_readiness` |

## 1. Code and schema

Schema deployment is separate from every data operation below it, and stays
separate. A migration that lands with an import is a migration nobody can roll
back independently.

| | Step | Command |
| --- | --- | --- |
| 1.1 | Preflight | `scripts/deploy/juristid-deploy-preflight.sh` |
| 1.2 | Build the target image, then read its migration plan | `deploy/unraid-main/README.md` §"Deploying a new build" |
| 1.3 | Back up, immediately before the schema moves | `scripts/deploy/juristid-backup.sh` |
| 1.4 | Migrate, then replace | the same README section, same exported identity |
| 1.5 | Post-flight | `manage.py deployment_readiness`, then the A–L browser list in the same README |

The build sits ahead of the backup on purpose: the migration plan is a question
about the *target* image, so that image has to exist before it can be asked, and
building one writes no business data and changes no schema. The backup does not
move with it — its value is being the last thing before the first command that
changes the database.

**A first deployment of the reference-data baseline sits inside step 1.** The
nine policy areas arrive with the migration at 1.4; the public institutions do
not, because they are operator-seeded reference data. Between the two,
`deployment_readiness` at 1.5 will refuse — correctly, the deployment really is
missing the vocabulary its features run on. Close the gap before calling the
deployment done:

| | Step | Command |
| --- | --- | --- |
| 1.6 | Read the reference-data plan | `manage.py reference_data plan` |
| 1.7 | Apply it, against the digest you just read | `manage.py reference_data apply --expect-plan-sha256 <digest>` |
| 1.8 | Confirm the baseline | `manage.py reference_data verify`, then `manage.py deployment_readiness` |

`plan` writes nothing. `apply` only ever adds a missing institution or a missing
reviewed alias — it never renames, retypes, merges or deactivates anything that
already exists, and refuses outright on any conflict (ADR 0029).

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
| 3.4 | Opinion archive catalogue | `opinion_archive plan --opinions … --expect-archive-sha256 …` | `opinion_archive catalogue` | Archive digest |
| 3.5 | Opinion archive bytes | `opinion_archive materialize-plan` | `opinion_archive materialize` | Holding bytes is not filing them |
| 3.6 | Opinion canonical records | `opinion_archive plan` (same plan) | `opinion_archive apply` | Only automatic classes file themselves |
| 3.7 | Archive text and search | `opinion_archive_search status` | `extract-text`, then `rebuild` | Extraction is **BLOCKED** where real data lives (ADR 0014) |
| 3.8 | Second-pass proposals | `opinion_archive content-plan` | `content-apply` | Proposals only; nothing files (ADR 0023) |
| 3.10 | Counterparty coverage — diagnostic only | `reference_data coverage --expect-register-snapshot-sha256 <sha>` | *(none — it never writes)* | Whether the reviewed institutions resolve enough of the register to be worth a backfill decision |

**3.4, 3.5 and 3.6 are three different acts, and the order is fixed.**
Cataloguing records what the archive holds; materialising holds the bytes;
applying decides whose letter it is. Run them as

> 3.4 catalogue → 3.5 materialise → 3.7 search → review → 3.6 apply

`opinion_archive catalogue` writes only `OpinionArchiveBatch`,
`OpinionArchiveItem`, `OpinionArchiveMetadata` and `OpinionMatchCandidate`. It
creates **no** Submission — not even for a proposal 3.6 would file without
asking anyone — and its report says `loodud arvamusi 0` rather than leaving that
to be inferred.

`opinion_archive apply` is unchanged and still does 3.4 and 3.6 together, which
is why 3.6's row names it. Use it for 3.6 *after* the review, not as the way to
reach 3.5: doing that files letters before anybody has read them.

Between 3.5 and 3.6 sits the part no command performs. The review queue needs an
identified administrator, and `may_read_archive` refuses under
`AUTH_MODE=shared_gate` — so 3.4 and 3.5 can run on the current deployment while
3.6 cannot honestly be reached on it (ADR 0016, ADR 0019).

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

## Where production actually stands

Facts, not plans — see `docs/open-decisions.md` for the decisions themselves.
Verified by direct read-only inspection of the real instance at revision
`53377932f5cba82fdc1193e35f7eca244c9d6809` on 2026-08-23. Earlier versions of
this section said the register apply and the archive materialisation had not
been run; both had, and the list below is what the instance answers rather than
what a development task last remembered.

### Done, and converged

Converged means the operation's own dry run now reports no work: running it
again would change nothing.

- **Register import** — 2458 source references from the reviewed snapshot
  `f38906c2…`. Two snapshots are present and the newest finished `ImportBatch`
  selects between them, so `select_register_snapshot` is unambiguous.
- **Current/final register cutover** — ACTIVATE 0, RETIRE 0, review 0; 200
  current Matters (60 in 2025, 140 in 2026), 15 still drafting.
- **Historical cutover** — would become historical: 0.
- **Opinion archive catalogue** — 767 items, 759 metadata rows, 244 archive →
  Matter links, 523 unlinked.
- **Opinion archive materialisation** — 767 binaries held.
- **Reference vocabulary** — 9 PolicyAreas by `taxonomy/0002`, 15 public
  Organisations and 13 aliases by `reference_data apply` against a reviewed
  digest. `reference_data verify` reports the baseline complete and
  `deployment_readiness` is green.

### Not done, and each blocked for a stated reason

- **OneNote → PolicyArea relationships** — the first real-data plan exists
  (71 relations, 4 of 24 filing locations exact-matched) and **awaits human
  review**. Nothing is wrong with it; nobody has read it. `Matter.policy_areas`
  is still 0.
- **`JÄRGMISEKS` / NextAction enrichment** — blocked on the parser-safety
  review in PR #49. The plan still reports an AUTO set; the audit established
  that those proposals are not all defensible, so the number is not an approval.
- **Canonical historical opinion Submissions** — blocked by **two independent
  gates, and clearing either one alone is not enough**:
  1. *Operational.* Canonical filing needs an identified administrator, and
     `AUTH_MODE=shared_gate` does not honestly provide one.
  2. *Engineering.* The production canonical-apply path still lacks its P4
     hardening — exact reviewed-plan digest binding, safe unresolved-recipient
     provenance, retryable recipient resolution, conflict/existing-Submission
     parity with the reviewed path, and precise reviewed sent-date provenance.
     P4 is not implemented.

  The plan proposes 244 Submissions. Changing the authentication mode would not
  make applying them correct.
- **Real-archive text extraction** — blocked pending the Secure Pilot Gate
  scanner, so production archive search is metadata-only by design.
- **Second-pass archive content proposals** — downstream of extraction: 767
  examined, 767 without content, 0 proposals. Not a defect.
- **Off-host disaster recovery** — still no destination, and no agreed RPO/RTO.
  The local backup set protects against an operator mistake and a bad
  deployment, and against nothing that happens to the host itself.
