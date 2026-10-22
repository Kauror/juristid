# The first release that carries AI access — what the operator does differently

ADR 0185, Phase 1. Read this beside
[`../../deploy/unraid-main/README.md`](../../deploy/unraid-main/README.md)
§"Deploying a release", which stays the procedure: this file names only what is
different about the one release that first carries `app.mcp_server`,
`app.mcp_access` and the two new Compose services. Every later release is the
ordinary procedure, with step 9's conditional `mcp` line.

## Two decisions, two sessions

**Shipping the code** and **switching AI access on** are separate, and each
needs its own approval:

1. *The release* — this file, then the README. With `MCP_ENABLED` unset (the
   template says `0`) and no `juristid-mcp.env` on the host, it changes nothing
   anybody can see: every OAuth route answers 404, no grant can be made, and
   neither new service starts. It is a code release with one additive migration.
2. *The enablement* — [`OPERATIONS.md`](OPERATIONS.md) §"First-time
   enablement", afterwards, once the owner says so and the OpenAI tunnel exists.

Doing them in one session is possible; doing them in one *step* is not. A
release that fails half-way must not leave AI access half-on.

## What the release contains

| | |
| --- | --- |
| New tables | `app.mcp_access`: grants, enrolled authenticators, authorization codes, tokens — migration `mcp_access.0001_initial`. Additive: no existing table changes, no row is rewritten. |
| New services | `mcp`, `mcp-tunnel` in `deploy/unraid-main/compose.yml`, both behind the `mcp` profile — not started by this release. |
| New routes on `web` | `/.well-known/oauth-authorization-server`, `/oauth/authorize`, `/oauth/token`, `/oauth/revoke`, `/mcp-ligipaas/…` — 404 while `MCP_ENABLED` is off. |
| New dependency | `mcp` 2.3.0 (and through it `starlette`, `uvicorn`, `httpx`, `pydantic`), in the same image; the release image is built by `release-image.yml` exactly as before. |
| New pinned third-party image | `ghcr.io/openai/tunnel-client:v0.0.17@sha256:67b473b4bc23bc984e4a381752e9a2fa75649746edc06e46b27025611835c5c0` — pulled only when `mcp-tunnel` is first started, not by the release. |
| Search index | unchanged — the manifest's `search_rebuild_required` says `NO` unless something else in the range moved `INDEX_VERSION`. |
| New environment templates | `deploy/unraid-main/juristid-mcp.env.example`, `juristid-mcp-tunnel.env.example`; three new lines in `.env.example`. |

## The release, step by step — only where it differs

**A. Build.** No change: `release-image.yml` for the merge commit, one image,
same artifact contract. Nothing new is built and nothing is built on the host.

**Steps 1–5** (record the revision, preflight, checkout, exports, verify and
load). No change. The preflight now also reports «AI access is not set up here»
— expected on this release, since neither MCP file exists yet.

**Step 6, the migration plan.** `migration_plan` lists exactly the new app's
migrations — `mcp_access.0001_initial` (and any later `mcp_access` migration
the release carries) — and calls them additive. Anything else in the plan is
another change in the range and is reviewed as usual. A non-additive
`mcp_access` operation on this first release is a **stop**: there is nothing
for it to alter.

**Step 7, pre-migration audits.** None for this release.

**Steps 8–9, back up, migrate, replace.** No change. The dry run must still
show neither `db` nor `tunnel` being recreated, and it must not list `mcp` or
`mcp-tunnel` at all — they are behind the profile. If it does, the Compose on
the host is older than the profile semantics this relies on: **stop** and
report `docker compose version`. Step 9's AI-access line does not apply: no
`juristid-main-mcp` exists yet, and the `docker ps` check there says so.

**Step 10, post-flight.** As always — `deployment_readiness`, `healthz`, the
fingerprint comparison, which now names the new `mcp_access` leaf and new
counts of zero rows, both expected from the plan. Then three AI-access facts,
all of which must hold:

```bash
curl -s -o /dev/null -w '%{http_code}\n' https://juristid.orgusaar.ee/.well-known/oauth-authorization-server
```

prints `404` — the OAuth surface exists in the code and is off.

```bash
docker ps -a --filter label=com.docker.compose.project=juristid-main --format '{{.Names}}'
```

lists no `juristid-main-mcp` and no `juristid-main-mcp-tunnel`.

```bash
docker compose -p juristid-main -f deploy/unraid-main/compose.yml exec -T web python manage.py mcp_access list
```

runs, from the new image, and lists nobody.

**Release notes.** Whether this release needs an entry in
`docs/release-notes/uuendused.toml` follows the rule in
`docs/release-notes/README.md`: with AI access off there is nothing a lawyer
will notice. The **enablement** is what lawyers notice, and its entry is dated
the day the first colleague is granted, not the day the code shipped.

## Then, separately: the enablement

[`OPERATIONS.md`](OPERATIONS.md) §"First-time enablement", in order: the tunnel
and restricted runtime key on Platform; the two environment files, root's and
600; the three web lines; the running release named in the shell; preflight;

```bash
docker compose -p juristid-main -f deploy/unraid-main/compose.yml up -d --no-build --no-deps web intake-reader searchindex intake-sweeper mcp
```

once both are healthy and `check_mcp_server` passes,

```bash
docker compose -p juristid-main -f deploy/unraid-main/compose.yml up -d --no-build --no-deps mcp-tunnel
```

then `/readyz`, the ChatGPT plugin, the first grant, and the end-to-end proof
with one granted and one ungranted account. Any failure there is answered by
*Switching AI access off*, which leaves the release itself in place.

## What this release must not do

- start `mcp` or `mcp-tunnel`;
- create `juristid-mcp.env` or `juristid-mcp-tunnel.env`, or set `MCP_ENABLED=1`;
- grant anybody access;
- recreate `db` or `tunnel` (the standing rule, unchanged).
