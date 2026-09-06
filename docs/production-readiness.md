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

**And it proves rather than reports.** What the real instance currently holds is
deliberately not written down here — the last section says how to ask it.

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
| 1.2 | Verify and load the off-host-built release image, then read its migration plan | `deploy/unraid-main/README.md` §"Deploying a release" |
| 1.3 | Back up, immediately before the schema moves | `scripts/deploy/juristid-backup.sh` |
| 1.4 | Migrate, then replace | the same README section, same exported identity |
| 1.5 | Post-flight | `manage.py deployment_readiness`, then the A–L browser list in the same README |
| 1.5a | **If the release moves `INDEX_VERSION`** — one rebuild, then prove it | `manage.py rebuild_search_index`, then `manage.py check_search_integrity` — `deploy/unraid-main/README.md` §11 |

**1.5a is not part of every release, and it is not optional on the ones it
belongs to.** The query chokepoint reads only rows carrying the current
`INDEX_VERSION`, so a release that changes it leaves the whole existing corpus
ineligible the moment it starts serving. Nothing converges that on its own: the
`searchindex` worker discharges `SearchRebuildDebt`, every row of which is
written by a *vocabulary edit*, and a deploy is not one — so the worker is
healthy, `check_search_freshness` is clear, and search answers nothing. All
three are true together and none of them is lying. Only
`check_search_integrity` distinguishes them, and any finding it reports stops
the release.

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
| 3.9.2 | Verify and load the target release image (built off-host) |
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
| 4.4a | Nothing is owed to the search index | `manage.py check_search_freshness` |
| 4.5 | Archive search matches what is held | `manage.py opinion_archive_search verify` |
| 4.6 | Era contracts still describe the workbook | `manage.py check_era_contracts` |
| 4.7 | Canonical state changed the way the plan said | `manage.py recovery_fingerprint --compare before.json` |
| 4.8 | It is usable | the A–L browser list in `deploy/unraid-main/README.md` |

`check_search_integrity` reports stale indexed text as well as missing rows.
Renaming an Organisation or a Tag changes what every record naming them
projects, and it still deliberately triggers no fan-out — but since ADR 0041 it
no longer triggers *nothing*: the rename records a durable obligation in its own
transaction and the `searchindex` service discharges it with an atomic full
rebuild. `check_search_freshness` is the one-line question ("is anything owed,
and has it been owed too long"), and is the container's healthcheck.
`check_search_integrity` reports the same debt in context and consumes none of
it. If the debt is old, the first thing to check is whether
`run_search_refresh_worker` is running; `rebuild_search_index` remains the
manual answer and is always safe.

### An optional roll-up: `production_status`

Several checks above are read-only questions about the **currently running**
application and database, and the procedure asks for them one after another.
`manage.py production_status` asks five of them in one go and prints one row
each:

```
PRODUCTION STATUS

Deployment readiness      PASS
Search integrity          PASS
Search freshness          PASS
Era contracts             PASS
Archive projection        PASS

Overall                   PASS
```

Non-zero exit on any `FAIL`, and zero only when every included check passed.
`--detail` adds each failing row's aggregate count; `--json` emits the same
verdicts structured. It calls the same report functions the individual commands
call, rather than running them and reading their output.

**It reports and never repairs.** A failing row points at that check's own
command and that check's own documented remedy; nothing is migrated, rebuilt,
refreshed, retried or reconciled on the way, and the search-rebuild debt it
reports is never consumed (`app/core/production_status.py`).

**It is a convenience, not a gate.** The rows in §0–§4 remain authoritative, and
each of those commands says more than a single PASS can. `production_status`
does not replace any of them, and in particular not these:

| Not covered by `production_status` | Still its own gate |
| --- | --- |
| What the **target image** would do to this database | 0.3 — `migration_plan`, run from the image being deployed |
| The release artifact itself | §1 |
| The pre-deploy backup | §2 |
| `/healthz`, container and compose state | `deploy/unraid-main/README.md` |
| The post-deploy browser pass | 4.8 |
| Evidence bytes | 4.2 — `check_evidence_integrity`, deliberately excluded: even its structural pass probes the filesystem once per version |
| Any repair, rebuild or data operation | §3 and §5, each a separately authorized decision |

The first row is the one to keep hold of. `production_status` runs *inside the
image that is already deployed*, so it can only see the migrations that image
was built with — it describes the current application and database, and it is
not permission to skip the target-image preflight at 0.3.

## 5. Rollback

| | Situation | Answer |
| --- | --- | --- |
| 5.1 | The release is wrong | Redeploy the previous commit — `deploy/unraid-main/README.md` §"Rolling back" |
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

`OPERATIONAL_MODELS` beside it is a shorter list with a different meaning:
`SearchRebuildDebt`, and nothing else. Those rows *are* restored — the dump has
no table list — but their number is never compared, because it says what the
system owed itself at one instant rather than what the register holds. Without
that, a restore taken seconds after somebody renamed a Valdkond reported
canonical divergence for a queue that was about to empty itself (ADR 0041).

## Current state — read it from the instance, not from here

**This document is the procedure. It does not know what production holds.**
Every count, every "converged", every "blocked" is an observation with a date on
it, and a checked-in observation goes stale where nobody looks — the same reason
`APPLICATION_STAGE` is a label rather than a gate.

This section used to answer the question itself, under the heading *Where
production actually stands*. It was true of the instance it described and false
within weeks, and a later audit read it as present state and reopened work that
had already shipped. The observations are kept, dated, in
[`docs/production-snapshots/`](production-snapshots/) — historical evidence, not
authority.

So: for any deployment or data decision, current state is the output of the
relevant read-only check, run against the instance you are about to change, at
the time you are about to change it. This document already says which check
proves which property — nothing new is needed:

| Question | Where it is answered |
| --- | --- |
| What build is running, and is it healthy | 0.4 — `deployment_readiness` |
| What a migration would do | 0.3 — `migration_plan`, from the target image |
| What a data operation would change | 3.x — that operation's own plan / `--dry-run` |
| What the reference baseline holds | `reference_data verify` |
| Whether the last operation landed as planned | 4.x, and `recovery_fingerprint --compare` |

A plan phase writes nothing, so running one to find out where you are is always
safe, and is the answer to "is this still true?" for every row in §3.

Decisions that remain to be *made* are not state and do not live here either;
they are in [`docs/open-decisions.md`](open-decisions.md).
