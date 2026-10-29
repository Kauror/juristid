# ADR 0179 — Third-party code is pinned by content, and the database gets only its own variables

- **Status:** proposed
- **Date:** 2026-10-22
- **Context:** Codex security scan of `4818203e`, findings
  `csf_b46bef0c0dcacd3af8255291` (mutable image and Action tags, LOW) and
  `csf_14aa9cd147aa48f44b141d39` (the database container receives the
  application's secrets, LOW). Not deployed with the change.

## Decision

### 1. Images: `repo:tag@sha256:<index digest>`

Every image this repository runs and did not build — every Compose file, every
CI service container, every Dockerfile `FROM` — names its tag *and* its digest.
The tag says which release a reader is looking at; the digest is what Docker
pulls, so a re-pushed tag changes nothing. The digest is the multi-platform
index digest, so the same line serves amd64 production and an arm64 laptop.

| Image | Digest | What it is | How it was checked |
| --- | --- | --- | --- |
| `postgres:18` | `sha256:06cad38a…dcd941` | PostgreSQL 18.6 (`PG_VERSION=18.6-1.pgdg13+2`), Debian 13, built 2026-08-13 | the index production was running on 2026-10-22 (`docker image inspect` RepoDigests, read-only); fetched by digest from `registry-1.docker.io/v2/library/postgres`, body hashed to the digest; its amd64 config read. The `18` tag has since moved to a rebuild of the same 18.6 (`sha256:edb0c08d…`); production's is kept so a deployment pulls nothing. |
| `cloudflare/cloudflared:2026.8.2` | `sha256:0aa26e28…89eb38` | cloudflared 2026.8.2, distroless, built 2026-08-14 | production's RepoDigest on 2026-10-22 and what the tag resolves to on Docker Hub today; the amd64 image's `CI_GIT_COMMIT=733bfb93…` is the commit GitHub's `2026.8.2` release tag names. |

The same name has the same digest in every file, so CI, the rehearsals and
production run one PostgreSQL. `aquasec/trivy` and both Dockerfile bases were
already pinned this way and are unchanged.

### 2. Actions: `owner/repo@<40-hex commit> # vX.Y.Z`

Each Action is pinned to the commit its major tag pointed at on 2026-10-22, so
behaviour is unchanged; the comment names the exact release with that commit.
Each was resolved with `gh api repos/<repo>/git/ref/tags/<tag>` (annotated tags
dereferenced) and matched to a published, non-draft release:

| Was | Now | Release |
| --- | --- | --- |
| `actions/checkout@v4` | `11d5960a326750d5838078e36cf38b85af677262` | v4.4.0 |
| `actions/upload-artifact@v4` | `ea165f8d65b6e75b540449e92b4886f43607fa02` | v4.6.2 |
| `actions/download-artifact@v4` | `d3f86a106a0bac45b974a628896c90dbdf5c8093` | v4.3.0 |
| `actions/cache@v4` | `0057852bfaa89a56745cba8c7296529d2fc39830` | v4.3.0 |
| `actions/setup-node@v4` | `49933ea5288caeca8642d1e84afbd3f7d6820020` | v4.4.0 |
| `astral-sh/setup-uv@v5` | `d4b2f3b6ecc6e67c4457f6d3e41ec42d3d0fcb86` | v5.4.2 |

### 3. The database receives `POSTGRES_DB`, `POSTGRES_USER`, `POSTGRES_PASSWORD` and nothing else

`db` no longer has `env_file:`. The application's environment file carries the
Django signing key, the shared-gate password and the mail/MFA settings, none of
which PostgreSQL reads. The image reads the password only to initialise an empty
cluster, so it is an interpolation, `${POSTGRES_PASSWORD:-}`: empty day to day
(the cluster holds it), and supplied by `--env-file <environment file>` on the
one runbook command that starts `db` on an empty `postgres/` (first install,
disaster recovery). `--env-file` feeds Compose's interpolation and passes nothing
into a container. Without it the image refuses to initialise — fail closed. No
new host file; no `.env` beside the Compose file (the preflight still refuses
one). The tunnel already had no environment and keeps none; its credential is
the locally managed file in its volume.

The application services keep the whole file: the system checks they run at
start need the gate and key settings. Narrowing the workers is not part of this.

## Consequences

- `tests/test_supply_chain_pinning.py` refuses an unpinned image, an Action by
  tag or short SHA, a pinned Action with no `# vX.Y.Z`, and one name with two
  digests or commits. `tests/test_service_secret_isolation.py` resolves the
  production file with a sentinel environment file in both invocation shapes;
  the compose smoke job in `ci.yml` makes the same assertion on
  `docker compose config` output.
- Updating an image or Action is an edit: resolve the new digest or commit from
  the upstream registry or repository, check it against the release, and change
  every occurrence (the tests keep them equal).
- **Deployment.** The `image:` strings and `db`'s environment changed, so Compose
  sees new definitions for `db` and `tunnel` even though the images are
  byte-identical to the running ones: any `up` or `run` that includes them
  recreates them (a few seconds of database outage; the bind-mounted cluster is
  untouched). Apply it as an announced maintenance step, not inside a code
  release; until then releases keep `--no-deps`.

## Status update 2026-10-29 — merged and deployed

This section records what happened after the decision; it does not amend the decision, and the status is unchanged (no formal acceptance was recorded).

- **Merged** in the security remediation integration (merge `9a2e834a, #514 = #506–#513, 2026-10-22`; #506).
- **Deployed** to production as `1270ca01` (2026-10-23, the release of #521, which carried the security baseline merged before it).
- **Not verified here**: the database container's own environment (§ on the database receiving only its variables) changes only when the `db` service is recreated, which releases deliberately avoid (`--no-deps` application services). Whether production's `db` container has been recreated on the new definition since is not recorded in this repository.
