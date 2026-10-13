# Off-host backup — prepared procedure, destination pending owner

> **Status (13.10.2026): not in use, and not usable yet.** No off-host
> destination has been approved, so nothing here has been run against one.
> This file is the procedure to follow once the owner has completed the
> [OWNER ACTION REQUIRED](#owner-action-required) checklist. Until then the
> honest statement in [`RECOVERY.md`](RECOVERY.md) stands: production is
> **not** protected against loss of the host.

**What is not an off-host copy**, however it is described:

* the local backup sets under `/mnt/user/backups/juristid-main` — same host,
  same disk as production;
* the CI «Backup and restore rehearsal» job — it restores *synthetic* data on a
  throwaway runner and proves the procedure, never a production set;
* the weekly Unraid *Appdata Backup* and the nightly KOCORP USB snapshot — same
  host, same room, written and deletable by root, unencrypted.

The design and the reasoning behind each choice is in the 13.10.2026 proposal
(restic, client-side encryption, a push key on the host that cannot delete, a
separate prune key held by custodians, object lock at the destination). This
file is the part an operator runs.

## Contents

1. [What is copied](#1-what-is-copied)
2. [Values the owner supplies](#2-values-the-owner-supplies)
3. [One-time setup](#3-one-time-setup)
4. [Each copy](#4-each-copy)
5. [Verifying the stored copy](#5-verifying-the-stored-copy)
6. [Retention](#6-retention)
7. [Restoring from the off-host copy](#7-restoring-from-the-off-host-copy)
8. [What must never enter GitHub or CI](#8-what-must-never-enter-github-or-ci)
9. [OWNER ACTION REQUIRED](#owner-action-required)

## 1. What is copied

**One consistent backup set, produced by the existing script** —
`scripts/deploy/juristid-backup.sh`, unchanged — together with the byte pools
its membership points into. Nothing here dumps the database or reads the live
evidence tree itself: the set *is* the unit of consistency (`RECOVERY.md`,
"Why the evidence is synchronised twice"), and a second, different way of
producing one would be a second, unreviewed backup.

From the backup root `/mnt/user/backups/juristid-main`:

| Path | Why |
| --- | --- |
| `sets/` (never `*.partial`) | the dumps, manifests, checksums and membership inventories |
| `evidence/` | the byte pool the sets' `evidence.files0` names |
| `legacy-source/` | the byte pool the sets' `legacy-source.files0` names |
| `fingerprints/` | the release fingerprints a restore is compared against |
| `offhost-manifests/` | the recovery-identity record this procedure writes ([4.3](#43-record-the-recovery-identity)) |

restic deduplicates, so every snapshot carries every set while only new bytes
travel. Each snapshot is **tagged with the one set it was taken for**, so a
restorer can ask for that set by name.

Not copied, on purpose:

* **secrets** — `config/juristid.env` and the tunnel credential. They are
  escrowed separately ([§3.4](#34-escrow-the-keys-and-the-secrets)), so one stolen
  repository password does not also hand over the database password;
* derivatives and the search projection — rebuildable (ADR 0014);
* the historical source corpus and the August 2026 pre-set leftovers —
  only if the owner says so, as separately tagged one-off snapshots.

## 2. Values the owner supplies

Nothing below has a default. The wrapper refuses to run while any of them is
missing, and there is no fallback destination.

| Variable | What it is | Where it lives |
| --- | --- | --- |
| `JURISTID_OFFHOST_ENV_FILE` | path of a root-owned, mode-600 file holding `RESTIC_REPOSITORY=<scheme>:<location>` and the **push** credential's provider variables (for S3, `AWS_ACCESS_KEY_ID` and `AWS_SECRET_ACCESS_KEY`) | `/mnt/user/appdata/juristid-main/config/offhost/restic.env` (proposed; `config/` is mode 700) |
| `JURISTID_OFFHOST_PASSWORD_FILE` | path of a root-owned, mode-600 file holding the host's restic repository key | `/mnt/user/appdata/juristid-main/config/offhost/restic-password` (proposed) |
| `JURISTID_OFFHOST_RESTIC_IMAGE` | the official restic image **pinned by digest**: `restic/restic@sha256:<digest>`, a release at least two weeks old whose checksum and signature were checked against the upstream release | chosen at install time |

The push credential must be able to add objects and read them back, and must
**not** be able to delete object versions, bypass retention, or change the
bucket's lock settings. restic removes its own lock files, so on a versioned
bucket a plain delete only adds a delete marker; rehearse the exact permission
set against the chosen provider before relying on it.

## 3. One-time setup

Each step is a host change the owner approves first
([OWNER ACTION REQUIRED](#owner-action-required)). Work from the checkout:

```bash
cd /mnt/user/appdata/juristid-main/repo
```

### 3.1 The image

Pull exactly the approved digest, and nothing tagged `latest`:

```bash
docker pull "$JURISTID_OFFHOST_RESTIC_IMAGE"
```

### 3.2 The secrets directory

```bash
install -d -m 700 /mnt/user/appdata/juristid-main/config/offhost
```

A custodian writes the two files into it, mode 600, by hand. Never paste their
contents into a terminal that is being recorded or into a ticket (`OPERATOR.md`).

### 3.3 Initialise the repository

Done **once**, by a custodian, from a machine they control — not by the host's
push key. The custodian then adds the host's own key (`restic key add`) and
writes it into `restic-password` on the host. Each of the two custodians holds a
key of their own, so the host's key can later be removed (`restic key remove`)
without losing access.

### 3.4 Escrow the keys and the secrets

Two named custodians. Each holds, in the Chamber's password manager:

* their own restic repository key;
* the repository location;
* the **read** credential for restores, and the **admin** credential for
  `forget`/`prune` — neither of which ever lives on KOCORP;
* the values of `juristid.env` that a rebuild needs and the tunnel credential
  (closes DR1-C in `RECOVERY.md` once done).

A sealed paper copy of one repository key goes in the office safe. Without a
repository key the copy is unreadable by design, by anybody.

## 4. Each copy

From the checkout, in one shell. The wrapper
[`offhost-backup.sh`](offhost-backup.sh) runs §4.2–§4.5 in order and stops at
the first failure; it is a **draft, untested against a real destination**.
The steps are written out here so that each can be run and read by hand.

### 4.1 Choose the set

Take a fresh set with the existing script, or use the newest one — after a
release that is the set its step 8 took:

```bash
scripts/deploy/juristid-backup.sh --project juristid-main --compose-file deploy/unraid-main/compose.yml --data-root /mnt/user/appdata/juristid-main --backup-root /mnt/user/backups/juristid-main
```

```bash
SET_ID=<the stamp it printed, e.g. 20261013T220000Z>
```

### 4.2 Source consistency check — before anything leaves the host

Levels 1 and 2 of the existing verifier, on exactly that set and its pools.
A set that fails here is not copied:

```bash
scripts/deploy/juristid-verify-backup.sh --project juristid-main --compose-file deploy/unraid-main/compose.yml --set /mnt/user/backups/juristid-main/sets/$SET_ID --backup-root /mnt/user/backups/juristid-main --level 2
```

### 4.3 Record the recovery identity

A small JSON file **stored with the copy**, in `offhost-manifests/` under the
backup root, named `<SET_ID>.json`, holding no secret. The wrapper writes it and
refuses to overwrite one that exists; by hand, create the directory once and
write the same fields:

```bash
install -d -m 700 /mnt/user/backups/juristid-main/offhost-manifests
```

| Field | Source |
| --- | --- |
| `set_id` | `$SET_ID` |
| `set_sha256sums_sha256` | SHA-256 of the set's own `SHA256SUMS` — one hash that pins the dump, the manifest and both inventories |
| `application_revision` | the set's `manifest.json` — the **git SHA** whose image a restore installs |
| `image_id` | `docker image inspect juristid-main-web:<first 12 of that SHA>` — the release image's digest, or `unknown` if it is no longer on the host |
| `fingerprint_file`, `fingerprint_sha256` | the release fingerprint taken with that set, `fingerprints/fp-before-release-<sha12>.json`, if one exists |
| `created_at`, `restic_image` | when, and with which pinned image |

The fingerprint is listed as the closest comparison available, **not** as a
property of the set: it was taken from the live system a minute after the dump
(`RECOVERY.md`, "Fingerprinting the live system").

### 4.4 Encrypt and upload

restic encrypts on the host before anything is sent; the destination only ever
holds ciphertext. The backup root is mounted **read-only**, so the uploader
cannot change a local set:

```bash
docker run --rm --env-file "$JURISTID_OFFHOST_ENV_FILE" -e RESTIC_PASSWORD_FILE=/run/restic-password -v "$JURISTID_OFFHOST_PASSWORD_FILE:/run/restic-password:ro" -v /mnt/user/backups/juristid-main:/data:ro "$JURISTID_OFFHOST_RESTIC_IMAGE" backup --host kocorp-juristid-main --tag "set:$SET_ID" --tag "rev:<application_revision>" --exclude '*.partial' /data/sets /data/evidence /data/legacy-source /data/fingerprints /data/offhost-manifests
```

### 4.5 Check the repository after the push

```bash
docker run --rm --env-file "$JURISTID_OFFHOST_ENV_FILE" -e RESTIC_PASSWORD_FILE=/run/restic-password -v "$JURISTID_OFFHOST_PASSWORD_FILE:/run/restic-password:ro" "$JURISTID_OFFHOST_RESTIC_IMAGE" check
```

```bash
docker run --rm --env-file "$JURISTID_OFFHOST_ENV_FILE" -e RESTIC_PASSWORD_FILE=/run/restic-password -v "$JURISTID_OFFHOST_PASSWORD_FILE:/run/restic-password:ro" "$JURISTID_OFFHOST_RESTIC_IMAGE" snapshots --tag "set:$SET_ID"
```

The second must list the snapshot just taken. Write down its id beside the set
id in the release record.

## 5. Verifying the stored copy

Verification at the source proves the source. These prove the copy.

| When | What | Proves |
| --- | --- | --- |
| after every push | `restic check` (§4.5) | the repository's structure and index are sound |
| weekly | `restic check --read-data-subset=10%` | the stored packs decrypt and match their hashes. restic picks the subset at random, so every pack is read over about ten weeks |
| monthly | restore the newest snapshot's set into an isolated staging root ([§7](#7-restoring-from-the-off-host-copy), steps 2–4) and run levels 1 and 2 on **the copy that came back** | the set survives the round trip |
| quarterly, or as the owner decides | the full restore rehearsal of `RECOVERY.md` ("Rehearsing against a real set"), with the input fetched from off-host, ending in the fingerprint comparison | the deployment comes back |

The weekly check, written out:

```bash
docker run --rm --env-file "$JURISTID_OFFHOST_ENV_FILE" -e RESTIC_PASSWORD_FILE=/run/restic-password -v "$JURISTID_OFFHOST_PASSWORD_FILE:/run/restic-password:ro" "$JURISTID_OFFHOST_RESTIC_IMAGE" check --read-data-subset=10%
```

The monthly and quarterly runs use the **rehearsal** project
`juristid-recovery-rehearsal` with `deploy/recovery-rehearsal/compose.real-data.yml`,
on a disk that is not production's — never `juristid-main`, never a developer's
or a home machine. The fingerprint comparison at the end is the one in
`RECOVERY.md`: the restored stack's full-bytes fingerprint compared with the
`fingerprint_file` from the identity record. It is "near-identical", not a
proof, for the reason given there.

Freshness: alongside `juristid-check-backup-age.sh` for the local root, alert
when `restic snapshots --latest 1` is older than the RPO. Who receives the alert
is an owner decision.

## 6. Retention

Off-host, run **monthly by a custodian with the admin key**, never from KOCORP:

```bash
docker run --rm --env-file <custodian admin env file> -e RESTIC_PASSWORD_FILE=/run/restic-password -v <custodian key file>:/run/restic-password:ro "$JURISTID_OFFHOST_RESTIC_IMAGE" forget --host kocorp-juristid-main --keep-daily 14 --keep-weekly 8 --keep-monthly 12 --prune
```

14 daily / 8 weekly / 12 monthly is the proposal, awaiting approval. The object
lock period at the destination (proposed 30 days) is the floor nothing can go
below, whichever key is used. Local sets are still never deleted
(`RECOVERY.md`, "What the script will not do"), so off-host retention decides how
far back recovery reaches **after the local root itself is lost**, not which
sets exist.

## 7. Restoring from the off-host copy

Scenario D in `RECOVERY.md`: the host is gone. This feeds `RECOVERY.md`
"Restoring / Order" and changes nothing in it. Every value comes from escrow,
not from the dead host.

1. **On the replacement Koda-controlled host**, put the read credential and one
   custodian key into files of their own, mode 600, outside any Git checkout
   and outside the appdata SMB share. Never on a developer's or home machine.
2. **Choose the set.** Newest unless there is a reason otherwise:

   ```bash
   docker run --rm --env-file <read env file> -e RESTIC_PASSWORD_FILE=/run/restic-password -v <custodian key file>:/run/restic-password:ro "$JURISTID_OFFHOST_RESTIC_IMAGE" snapshots --host kocorp-juristid-main
   ```

3. **Fetch one consistent unit** — that set, the pools and the fingerprints —
   into an empty staging root:

   ```bash
   docker run --rm --env-file <read env file> -e RESTIC_PASSWORD_FILE=/run/restic-password -v <custodian key file>:/run/restic-password:ro -v /mnt/user/restore-staging:/restore "$JURISTID_OFFHOST_RESTIC_IMAGE" restore <snapshot id>:/data --target /restore --include /sets/<SET_ID> --include /evidence --include /legacy-source --include /fingerprints --include /offhost-manifests
   ```

4. **Verify the copy before trusting it** — level 1 needs nothing but the files:

   ```bash
   scripts/deploy/juristid-verify-backup.sh --set /mnt/user/restore-staging/sets/<SET_ID> --level 1
   ```

   Then compare `sha256sum /mnt/user/restore-staging/sets/<SET_ID>/SHA256SUMS`
   with `set_sha256sums_sha256` in `offhost-manifests/<SET_ID>.json`.
5. **Install the release image the set names** — `application_revision` in the
   set's `manifest.json`, checked against the identity record — not whatever is
   newest. A set taken before a migration belongs to the *previous* release
   (DR1-A). Rebuild it with `release-image.yml` for that SHA and load it as
   `README.md` part B describes; `deployment_readiness` refuses a mismatch later
   anyway. The rebuilt image's id need not equal `image_id`: the record says
   which revision, the revision says which code.
6. Continue with `RECOVERY.md` "The steps": secrets from escrow, the appdata
   directories, the database alone, then the restore — **from the staging
   root, one set for both halves**:

   ```bash
   scripts/deploy/juristid-restore.sh --project juristid-main --compose-file deploy/unraid-main/compose.yml --set /mnt/user/restore-staging/sets/<SET_ID> --backup-root /mnt/user/restore-staging --data-root /mnt/user/appdata/juristid-main
   ```

   The script re-verifies levels 1 and 2 against the staging pools, refuses a
   database or storage that is not empty, copies only that set's members, and
   restores the database in one transaction. **The database and the evidence
   come from the same `--set`.** Never `--database-only` from one set beside
   evidence from another, and never hand-copied pool objects.
7. **Fingerprint.** With the application services up and `deployment_readiness`
   passing, compare against the identity record's `fingerprint_file` exactly as
   `RECOVERY.md` "Then verify, before anything is published" shows — the file
   handed to the container on stdin — and run `check_evidence_integrity`. If
   there is no fingerprint for that set, record the result as "structurally
   consistent", not "everything came back".
8. Rebuild derivatives and search, then the tunnel last (`RECOVERY.md`).
9. Remove `/mnt/user/restore-staging` once the restore has been accepted. It
   holds real member material.

## 8. What must never enter GitHub or CI

* credentials of any kind: the push, read and admin keys, the restic repository
  passwords, the provider account details;
* `juristid.env`, the tunnel credential, or any value from them;
* any business data: a dump, an evidence object, page XML, a restored tree, a
  fingerprint or identity record taken from production, a `restic snapshots`
  listing — none of it is test material and none of it is documentation;
* the repository location and the provider tenant, which identify where the
  Chamber's data is held.

This file and the wrapper carry **no** destination and no default, on purpose.
CI exercises the wrapper's refusal and its shell syntax only; it never talks to
a repository.

## OWNER ACTION REQUIRED

Nothing above can start until each item is decided and recorded in
`docs/open-decisions.md`.

- [ ] **Destination**: provider and the Koda-owned account or tenant (proposal:
      an EU S3-compatible bucket with versioning and object lock; alternative: a
      Koda-owned machine running `rest-server --append-only`). Not KOCORP, not its
      USB disks, not the same building, not a personal or home machine.
- [ ] **Tenant and region**: is EU/EEA enough, or must it be Estonia?
- [ ] **Legal**: processor DPA; is storing member correspondence off-site covered;
      how long may an off-site copy outlive a deletion in production (interacts
      with the open retention and legal-hold decision).
- [ ] **Custodians**: two named people, their password-manager location, and the
      sealed copy in the safe.
- [ ] **Object lock**: governance or compliance mode, and the period (proposed
      30 days).
- [ ] **Credentials**: the push key's exact permissions, rehearsed against the
      provider; the read and admin keys never on KOCORP.
- [ ] **Host-change approval**, as its own reviewed change: the pinned restic
      image, `config/offhost/`, a cron entry for the schedule, and outbound
      HTTPS from KOCORP to the destination.
- [ ] **Schedule and RPO**: when the backup and the push run, a backup after
      every successful deploy (closes DR1-A), and the `--max-age-hours` value.
- [ ] **Alert recipients**: who is told when a push, a check or the freshness
      alarm fails.
- [ ] **Rehearsals**: who runs the monthly and quarterly checks, and on which
      isolated disk or machine.
- [ ] **Scope**: the 4.5 GB source corpus (once), the August 2026 leftovers,
      `operator-reports/`, and whether release image archives are kept
      off-host too (GitHub keeps workflow artifacts only for a limited time).
- [ ] **Secrets escrow (DR1-C)**: approve the password-manager escrow, and decide
      what to do about the plaintext copies the USB snapshot probably holds.
- [ ] **Prerequisite host fix**: the guest-writable `appdata` SMB share and any
      world-writable root-cron script (both INFERRED from earlier audits; verify
      on the host). A LAN-to-root path makes any uploader on that host
      untrustworthy.
