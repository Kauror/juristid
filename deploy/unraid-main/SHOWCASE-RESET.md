# Showcase reset — replacing a disposable business-data world

How `juristid-main` moves from a used-up TEST/showcase dataset to the clean
six-package showcase world, without touching append-only history and without
the old data ever stopping being restorable. Written for the overnight
stabilization round (2026-10-08); the first execution's record lives in the
operator reports beside the backups.

This is a **data operation**, separate from any code release
(docs/production-readiness.md §3). It needs the owner's explicit,
current authorization every time, and it is only ever performed against a
**disposable dataset** — the eight-condition gate below decides that, not
anyone's memory.

## Why not purge, and why not drop

`purge_test_data` is a planner with no apply path, because append-only
`ChangeEvent` history makes an in-place purge architecturally unsafe — that
decision stands. The 2026-09-21 pilot reset dropped and recreated the
database under backup protection; this procedure is one notch safer still:
the old database is **renamed aside, not dropped**, so the rollback is a
rename back, and the backup set is the second line rather than the first.

The live database keeps the name `juristid`, because
`scripts/deploy/juristid-backup.sh` and `juristid-restore.sh` default to that
name: a world switched by pointing `POSTGRES_DB` elsewhere would quietly make
every later documented backup dump the wrong database.

## The gate (all eight, re-proven read-only, writers held)

1. Every `legacy_import` table is 0.
2. ARCHIVE Matters: 0.
3. Every `OpinionArchive*` table is 0.
4. Every Matter is NATIVE — nothing imported survives in the set.
5. No real operational use: every Matter is TEST-classified **or** a named,
   provenance-known smoke artifact of a release round; no Submission belongs
   to anything but those.
6. No cross-boundary references out of the disposable set.
7. A dedicated backup set exists, taken with writers held, verified level 2.
8. The whole sequence (migrate → reference plan/apply/verify → identities →
   `seed_showcase_data` → `check_domain_invariants`) was rehearsed end to end
   on a throwaway database, and the reference plan digest there matches the
   one production prints in step 6 below.

Any condition failing = stop, start the writers again, report. Ambiguity
about condition 5 is a failure of condition 5.

## Sequence

One shell on the host; `JURISTID_GIT_SHA`/`JURISTID_IMAGE_TAG` exported to
the deployed revision; `C="docker compose -p juristid-main -f
deploy/unraid-main/compose.yml"` from the repo root.

1. **Write smoke on the old world** (it is about to be discarded, so the
   smoke may write): the release round's smoke script, TEST data only.
2. **Hold the writers:** `$C stop web intake-reader searchindex`
   (db and tunnel stay up).
3. **Re-prove the gate** read-only (counts only), then take the dedicated
   backup: `scripts/deploy/juristid-backup.sh --project juristid-main
   --compose-file deploy/unraid-main/compose.yml --data-root
   /mnt/user/appdata/juristid-main --backup-root
   /mnt/user/backups/juristid-main`, then `juristid-verify-backup.sh … --level 2`.
4. **Move the evidence and derivative trees aside** (never delete):
   `mv evidence evidence-pre-showcase-<UTCstamp>`, same for `derivatives`,
   then `install -d -o 10001 -g 10001 evidence derivatives`.
   `legacy-source` stays — it is import material, not business data.
5. **Rename the old database aside and create the fresh one** (inside the
   running db container; no volume, no cluster, no container is touched):

       docker exec juristid-main-db psql -U juristid -d postgres \
         -c "ALTER DATABASE juristid RENAME TO juristid_pre_showcase_<stamp>"
       docker exec juristid-main-db psql -U juristid -d postgres \
         -c "CREATE DATABASE juristid OWNER juristid TEMPLATE template1"

   Check encoding/collation before and after (`UTF8 / en_US.utf8`).
6. **Build the world** with the deployed image, `run --rm --no-deps -T web`:
   `migrate`, then `reference_data plan` (digest must match the rehearsal),
   `reference_data apply --expect-plan-sha256 <digest>`, `reference_data verify`.
7. **Carry the identities across, host-locally** (no hashes leave the host;
   every account already has an unusable password under `shared_gate`):

       docker exec juristid-main-db pg_dump -U juristid \
         -d juristid_pre_showcase_<stamp> --table public.accounts_user \
         --data-only | docker exec -i juristid-main-db psql -U juristid \
         -d juristid -v ON_ERROR_STOP=1

8. **Seed:** `$C run --rm --no-deps -T web python manage.py
   seed_showcase_data --operator-intent showcase-world`.
9. **Health before serving:** `check_domain_invariants`,
   `deployment_readiness` (expect `shared_gate`, real data yes, debug off),
   `check_evidence_integrity --verify-sha`, counts (7 Matters, all TEST,
   1 RESTRICTED, 0 REAL).
10. **Serve:** `$C up -d --no-build --no-deps web intake-reader searchindex`
    (dry-run first; db and tunnel must not move), then the ordinary
    post-flight: healthz revision, gate page, persona list, pages, search.
11. **Last verification is read-only.** No write smoke against the seeded
    packages — the morning state is the showcase, not the test run's
    leftovers.

## Rollback

`$C stop web intake-reader searchindex`; rename the fresh database aside and
the old one back; move the evidence trees back; `up -d --no-build --no-deps
web intake-reader searchindex`. The dedicated backup set from step 3 is the
second line of defence; `juristid-restore.sh` is the third.

## Notes

- The first backup after a reset whose evidence tree is still empty needs
  `--allow-empty evidence`; after `seed_showcase_data` the tree is not empty.
- A fresh database needs no search rebuild: the services write
  `SearchDocument` rows synchronously, and `rebuild_search_index` stays
  available and always safe.
- Old `juristid_pre_showcase_*` databases are removed later, by name, as a
  deliberate maintenance step with the owner — never as part of this
  procedure.
