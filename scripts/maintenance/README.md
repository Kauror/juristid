# Maintenance kit

Two small tools for the housekeeping that otherwise gets redone by hand every
few weeks. Both default to read-only. Neither has, or will get, a database
reset, a Docker prune, a volume delete or an evidence delete: those stay
owner-approved one-off commands.

What already exists and is **not** duplicated here:

| Need | Existing tool |
|---|---|
| Containers, `/healthz`, DB, free space, backup age, sync night, auth failures | `scripts/deploy/juristid-monitor.sh` (tiers fast/daily/weekly) |
| Application readiness | `manage.py production_status` (its readiness row writes and removes a zero-byte probe file on each storage root) |
| Backup set verification | `scripts/deploy/juristid-verify-backup.sh`, `juristid-check-backup-age.sh` |
| Lovable sync state | `manage.py lovable_sync status --json`, `report --json` (read-only) |
| Old Docker images | the host's daily `prune_docker.sh` (keeps the newest four `juristid-main-web` tags) |
| Orphaned evidence | `manage.py prune_orphaned_evidence` — **never on production without an owner decision** (ADR 0203 rollback) |

## `juristid-release-artifacts.sh` (host)

The release kits copy `juristid-main-web-<sha12>.tar.gz` (≈155 MB each) onto
the host, and nothing removed them: 118 had built up by 2026-11-04. This script
applies the image policy to the archives. An archive stays while its image is
running, still in Docker (the prune window), or named as a rollback target by a
protected kit's `release.env`, or passed with `--keep`. It touches only files
with exactly that name pattern directly in `releases/<kit>/`, `deploy-staging/`,
`release-images/`, `release-staging/`, `operator/releases/` (and their immediate
subdirectories) or the data root itself. Kit logs and scripts stay.

```bash
cd /mnt/user/appdata/juristid-main/repo
# 1. Look (read-only):
bash scripts/maintenance/juristid-release-artifacts.sh inventory | column -t | less
#   class          sha           bytes      mtime                 path
#   KEEP-RUNNING   2a873242cd26  163617515  2026-11-03T19:01:00Z  /mnt/.../releases/2a873242cd26/juristid-main-web-2a873242cd26.tar.gz
#   KEEP-PROTECTED 1c104efa103b  ...
#   REMOVE         0b035b16590c  ...
#   (stderr) # archive bytes: 19600000000 total, 18900000000 removable

# 2. Plan (writes only the plan file; prints its sha256):
bash scripts/maintenance/juristid-release-artifacts.sh plan --out /mnt/user/appdata/juristid-main/operator/artifacts-plan.tsv
#   files: 236  bytes: 18950000000
#   sha256: 3f9c...

# 3. Review the plan, then apply exactly that plan:
bash scripts/maintenance/juristid-release-artifacts.sh apply \
  --plan /mnt/user/appdata/juristid-main/operator/artifacts-plan.tsv \
  --expect-sha256 3f9c... \
  --log /mnt/user/appdata/juristid-main/operator/artifacts-apply.log
#   plan lines: 236  removed: 236  already gone: 0  bytes freed: 18950000000
```

`apply` takes the host lock (it refuses while a release, backup or sync holds
it), re-derives the protected set, and re-checks every line before removing
anything: a changed plan, a file whose size or mtime changed, a symlink, a path
outside the data root or a SHA that has become protected refuses the whole plan.
Running it twice is harmless (`ALREADY-GONE`). Deleted archives are not
recoverable from the host. The image for any SHA can be rebuilt by the
`release-image.yml` workflow, but not byte-identically, so keep any archive you
may want to restore with `--keep <sha>`.

## `juristid-git-inventory.sh` (any clone)

Read-only branch and worktree classification: KEEP / SAFE / REVIEW. It does no
fetch, no prune and no delete, and it never calls GitHub. Open PRs therefore
have to be checked separately.

```bash
git -C C:/CC/juristid fetch --prune origin
bash scripts/maintenance/juristid-git-inventory.sh --repo C:/CC/juristid > inventory.tsv
awk -F'\t' '{print $1, $2}' inventory.tsv | sort | uniq -c
gh pr list --state open --json headRefName --jq '.[].headRefName'   # exclude these
```

How to act on SAFE rows, one at a time, recording name and SHA:

```bash
git -C C:/CC/juristid worktree remove <path>                               # refuses if dirty
git -C C:/CC/juristid branch -D <branch>                                   # local, tip already in main
git -C C:/CC/juristid push --force-with-lease=<branch>:<sha> origin :<branch>   # remote; refuses if it moved
```

`git worktree remove` also deletes gitignored files in the worktree (dev
uploads, Playwright reports). The `ignored` column counts them. Archive them
first if they might matter.

Never: `git reset --hard`, `git clean`, `git stash` (the stash list is shared
by every worktree), or a force-push.
