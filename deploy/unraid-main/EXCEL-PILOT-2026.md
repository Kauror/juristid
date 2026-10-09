# Excel pilot 2026 — replacing the test-build's data with the reviewed sample

How `juristid-main` moves from its development dataset to the 2026 Excel
operational pilot (docs/adr/0148): a fresh database, the reviewed accounts and
reference data, and the reviewed sample imported by `excel_pilot_2026`. It is
the showcase reset's rename-aside model (`SHOWCASE-RESET.md`) with the pilot
import in place of the showcase seed.

**The current sample is the 35-Matter one** (pilot version 1.1, ADR 0148 §10):
the derived A–L snapshot `SOURCE_2026_2026-10-09_pilot_verified.xlsx` with
`pilot_35_selection.toml` — 20 A + 6 B + 9 C. It replaces the first, 31-Matter
pilot (version 1.0, 08.10 workbook) by the same procedure; a 1.0 manifest no
longer plans. The old world in step 1 is then that pilot's database.

This is a **data operation**, separate from the code release that ships the
command, and it needs the owner's explicit, current authorization. The owner
authorised it for the test-build on 2026-10-09, **including** the removal of a
development Matter classified REAL (`2026_1` «See on määrus») — so, unlike the
showcase gate, «every Matter is TEST» is *not* a condition here. The condition is
that the target is conclusively the test-build and that the old world is
recoverable.

## Preconditions (all, or stop)

1. **The release that ships `excel_pilot_2026` is deployed**, by the ordinary
   release procedure, and healthz names its revision. No other release or data
   operation is running on the host — coordinate with any parallel session first.
2. **The target is the test-build:** project `juristid-main`, database
   `juristid` in `juristid-main-db`, tunnel `juristid.orgusaar.ee`, `AUTH_MODE`
   `shared_gate`, `REAL_DATA_ALLOWED` on, mail delivery disabled.
3. **The rehearsal matched:** the same manifest planned and applied cleanly on
   a throwaway database at this revision, and `reference_data plan` printed the
   digest production prints in step 6.
4. **The source is exact:** the workbook in
   `/mnt/user/juristid-main/source/excel-pilot-2026/` hashes to the manifest's
   `workbook.sha256`, and the manifest's digest is the reviewed one.

## Sequence

One shell on the host; `JURISTID_GIT_SHA`/`JURISTID_IMAGE_TAG` exported to the
deployed revision; `C="docker compose -p juristid-main -f
deploy/unraid-main/compose.yml"` from the repo root; `S=/srv/historical-source/excel-pilot-2026`
is the same folder as the container sees it (mounted read-only).

1. **Inventory the old world, read-only** (counts only): Matters by class,
   origin and mode; Submissions; follow-ups; documents; every `accounts_*`
   table's row count. Record it beside the backup.
2. **Hold the writers:** `$C stop web intake-reader searchindex`
   (db and tunnel stay up).
3. **The dedicated backup**, writers held:
   `scripts/deploy/juristid-backup.sh --project juristid-main --compose-file
   deploy/unraid-main/compose.yml --data-root /mnt/user/appdata/juristid-main
   --backup-root /mnt/user/backups/juristid-main`, then
   `scripts/deploy/juristid-verify-backup.sh --project juristid-main
   --compose-file deploy/unraid-main/compose.yml --set <set> --level 2`.
   A backup that does not verify is a stop.
4. **Move the evidence and derivative trees aside** (never delete):
   `mv evidence evidence-pre-pilot-<UTCstamp>`, same for `derivatives`, then
   `install -d -o 10001 -g 10001 evidence derivatives`.
5. **Rename the old database aside and create the fresh one** — no session may
   be connected to `juristid` (the writers are held):

       docker exec juristid-main-db psql -U juristid -d postgres \
         -c "ALTER DATABASE juristid RENAME TO juristid_pre_pilot_<stamp>"
       docker exec juristid-main-db psql -U juristid -d postgres \
         -c "CREATE DATABASE juristid OWNER juristid TEMPLATE template1"

6. **Schema and reference data** with the deployed image,
   `$C run --rm --no-deps -T web python manage.py …`: `migrate`, then
   `reference_data plan` (digest = rehearsal), `reference_data apply
   --expect-plan-sha256 <digest>`, `reference_data verify`.
7. **Carry the accounts across, host-locally** (no secret leaves the host):

       docker exec juristid-main-db pg_dump -U juristid \
         -d juristid_pre_pilot_<stamp> --table public.accounts_user \
         --data-only | docker exec -i juristid-main-db psql -U juristid \
         -d juristid -v ON_ERROR_STOP=1

   and the same for every other `accounts_*` table step 1 found non-empty.
   Compare `account_counts.py`'s users digest and roles before and after.
8. **Plan, then apply the pilot** — the only step that sets the pilot flag, on
   the one-off container only:

       $C run --rm --no-deps -T web python manage.py excel_pilot_2026 plan \
         --workbook "$S/<workbook>.xlsx" --manifest "$S/manifest.json"
       $C run --rm --no-deps -T -e JURISTID_EXCEL_PILOT=1 web python manage.py \
         excel_pilot_2026 apply --workbook "$S/<workbook>.xlsx" \
         --manifest "$S/manifest.json" --expect-manifest-sha256 <digest> \
         --operator-intent excel-pilot-2026 --backup-set <set>

9. **Health before serving:** `check_domain_invariants`,
   `deployment_readiness`, `check_evidence_integrity --verify-sha`, and the
   counts the manifest promises for the 35-Matter sample:

   | | |
   | --- | --- |
   | Matters | 35 — A 20, B 6, C 9; owners 9 / 14 / 12 |
   | work status | 26 Aktiivne, 3 Mitteaktiivne, 4 Lõpetatud, 2 Jätkub mujal |
   | «Koostan arvamuse» | 20 open `DO` steps, each dated by its Matter's open `Arvamuse tähtaeg` |
   | SENT opinions | 11, each with its own placeholder PDF; 4 «ei saatnud» with none |
   | follow-up checks | 6, `PLANNED`, sending date + 30 (some already past) |
   | notes | 12 (6 beside a drafting step, 6 on finished files) |
   | continuations | 2 |
   | 2026 sequence | 300 |

   The apply's own report prints the same lines (`drafting actions 20`).
10. **Serve:** `$C up -d --no-build --no-deps --dry-run web intake-reader
    searchindex` (db and tunnel must not move), then without `--dry-run`; then
    healthz, the gate page, the persona list, Minu asjad, Osakond, a Teema, search.

## Rollback

`$C stop web intake-reader searchindex`; rename the fresh database to
`juristid_pilot_failed_<stamp>` and `juristid_pre_pilot_<stamp>` back to
`juristid`; move the evidence trees back; `up -d --no-build --no-deps web
intake-reader searchindex`. The step-3 backup set is the second line of
defence; `juristid-restore.sh` the third.

## Notes

- A fresh database ends every gate session: everyone re-enters the department
  password once.
- The intake reader extracts the placeholder PDFs (eleven) after serving; a
  search for «PROOVIMPORT» finds them.
- `juristid_pre_pilot_<stamp>` and the moved trees are removed later, by name,
  with the owner — never as part of this procedure.
- At commissioning this database is replaced again by a new import with real
  documents; no placeholder is ever swapped for a real letter (ADR 0148 §7).
