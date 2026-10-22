# AI access — operating the read-only MCP server

How a colleague's ChatGPT reaches Juristid, how to switch it on for the first
time on the real-data host, how to let one person in and keep everybody else
out, how to tell what is wrong, and how to switch it off in one command without
touching the rest of Juristid. The decision and its reasons are
[ADR 0185](../adr/0185-a-read-only-mcp-server-for-ai-clients.md); this file is
the procedure.

| What | Where |
| --- | --- |
| Why it is built this way | [`../adr/0185-a-read-only-mcp-server-for-ai-clients.md`](../adr/0185-a-read-only-mcp-server-for-ai-clients.md) |
| The first release that carries it | [`RELEASE-NOTES-FOR-OPERATORS.md`](RELEASE-NOTES-FOR-OPERATORS.md) |
| Every later release (step 9 replaces a running `mcp`) | [`../../deploy/unraid-main/README.md`](../../deploy/unraid-main/README.md) §"Deploying a release" |
| Reaching the host safely, keeping secrets out of the transcript | [`../../deploy/unraid-main/OPERATOR.md`](../../deploy/unraid-main/OPERATOR.md) |
| Operating AI access | **this file** |

Every command below runs **on the host, from the checkout**, exactly as the
release runbook's do:

```bash
cd /mnt/user/appdata/juristid-main/repo
```

OPERATOR.md's rules hold here unchanged: one shell, commands typed or run from a
file rather than piped into `ssh`, no `docker compose … config`, no printed
environment, no `set -x`. Two values on this page are credentials — the OpenAI
runtime key and a person's one-time enrolment link — and neither belongs in a
chat, a ticket, a screenshot or a transcript.

## What it can and cannot do

**It can** answer, from a colleague's own ChatGPT conversation in the Chamber's
workspace, the questions Juristid's pages answer: search the register and the
documents' extracted text, fetch one record, show a Teema's snapshot, its
chronology and its documents, list what enters into force in a window, and list
the matters a colleague is responsible for or Koda is waiting on an organisation
for (`list_matters`). Eight tools, every one read-only, every answer carrying links back to the records.
Every call runs **as the person who connected**, through the same visibility
rules as the browser (ADR 0181): nothing they could not open in Juristid comes
back, and an unknown id and a forbidden one look the same.

**It cannot** write anything — there is no write tool, the process connects
as its own SELECT-only database role `juristid_mcp`, and its connections also
start read-only (`default_transaction_read_only=on`); send
mail or notifications; read a stored file's bytes (document text comes from the
extracted fragments in PostgreSQL); run a query, a path, a URL or a model name a
caller supplies; or act as anybody other than the person whose token it holds.

**Nobody can connect** unless an operator granted that account, the account is
active with a business role, the person enrolled their own authenticator, and at
the moment of connecting they pass the shared gate, act as their own persona and
type a current code. The gate password alone is never enough (ADR 0185 §3).

**What it holds.** No volume, no evidence mount, none of the browser's secrets.
`mcp` has its own environment file; `mcp-tunnel` holds only OpenAI's runtime
key, the tunnel id and the one origin it may trust. Grants and tokens are rows
in the Juristid database (`app.mcp_access`), backed up and restored with
everything else; tokens are stored only as SHA-256 digests.

**Stated rather than hidden:** a person who knows the gate password **and**
holds a colleague's authenticator can connect as that colleague (ADR 0185,
*Consequences*). The other residual risks are under *Residual risks* below.

## The pieces

| Service | Container | Image | Environment | Reaches |
| --- | --- | --- | --- | --- |
| `web` (existing) | `juristid-main-web` | the release image | `juristid.env` + `MCP_ENABLED`, `JURISTID_PUBLIC_ORIGIN`, `MCP_RESOURCE_URLS` | serves `/.well-known/oauth-authorization-server`, `/oauth/authorize`, `/oauth/token`, `/oauth/revoke` and the enrolment pages through the Cloudflare tunnel |
| `mcp` | `juristid-main-mcp` | the release image | `juristid-mcp.env` only; database role `juristid_mcp` (SELECT only) | `db` on `internal`; listens on `mcp:8001` on `mcp_edge`, no host port |
| `mcp-tunnel` | `juristid-main-mcp-tunnel` | `ghcr.io/openai/tunnel-client:v0.0.17@sha256:67b473b4…` | `juristid-mcp-tunnel.env` only | only `mcp_edge`: `http://mcp:8001/mcp`; `api.openai.com:443` outbound; the public origin's OAuth metadata — never `db`, `web` or `tunnel` |

Both new services are behind the `mcp` profile. A release's `up -d --no-build`,
a restore's `up -d tunnel` and the backup scripts neither start, replace nor
stop them; they start only when named, and that is the switch. Both run
read-only, with every capability dropped, `no-new-privileges`, bounded memory,
CPU and processes, and bounded logs. `mcp-tunnel` runs as `nobody`.

## First-time enablement

**Before any of it.** The owner has approved switching AI access on; the release
that carries it is deployed and its migration applied
([`RELEASE-NOTES-FOR-OPERATORS.md`](RELEASE-NOTES-FOR-OPERATORS.md)); and you
know the full commit production runs now.

### 1. The tunnel and its runtime key — on platform.openai.com

Done once, in the browser, by somebody with the Platform roles for it:

1. **Tunnels management** (`platform.openai.com/settings/organization/tunnels`):
   create a tunnel named for this purpose (`Juristid`), scoped to the Chamber's
   ChatGPT workspace **Kaubanduskoda**. A tunnel created without the workspace
   scope exists in Platform and never appears in ChatGPT. Note its id,
   `tunnel_` and 32 hex characters.
2. **API keys**: create a **Restricted** key with Tunnels **Read** + **Use** and
   nothing else. Not an admin key, not "All". It is shown once: put it straight
   into the file in step 2, never anywhere else.

The person who creates the ChatGPT connector later needs Tunnels **Read** +
**Use** on their own account, or the tunnel will not be offered to them.

### 2. The two environment files — on the host

Root's, mode 600, in the config directory beside `juristid.env`, from the
templates in the checkout:

```bash
install -m 600 -o root -g root deploy/unraid-main/juristid-mcp.env.example /mnt/user/appdata/juristid-main/config/juristid-mcp.env
```

```bash
install -m 600 -o root -g root deploy/unraid-main/juristid-mcp-tunnel.env.example /mnt/user/appdata/juristid-main/config/juristid-mcp-tunnel.env
```

The MCP server's own signing key and its own database password, each
generated straight into its file so it reaches neither the terminal nor the
shell history:

```bash
sed -i "s|^DJANGO_SECRET_KEY=.*|DJANGO_SECRET_KEY=$(openssl rand -base64 64 | tr -d '\n/+=')|" /mnt/user/appdata/juristid-main/config/juristid-mcp.env
```

```bash
sed -i "s|^POSTGRES_PASSWORD=.*|POSTGRES_PASSWORD=$(openssl rand -base64 48 | tr -d '\n/+=')|" /mnt/user/appdata/juristid-main/config/juristid-mcp.env
```

Then fill in the rest **in an editor**, reading the comments in each file as
you go:

| File | Key | Value |
| --- | --- | --- |
| `juristid-mcp.env` | `POSTGRES_USER` | `juristid_mcp`, as the template has it — **not** `juristid.env`'s role |
| `juristid-mcp.env` | `POSTGRES_PASSWORD` | already generated above; its own, never `juristid.env`'s |
| `juristid-mcp.env` | `JURISTID_PUBLIC_ORIGIN` | `https://juristid.orgusaar.ee` |
| `juristid-mcp.env` | `MCP_RESOURCE_URLS` | the tunnel's MCP endpoint, `https://api.openai.com/v1/mcp/<tunnel id>` (confirm in step 7) |
| `juristid-mcp-tunnel.env` | `CONTROL_PLANE_API_KEY` | the restricted runtime key from step 1 |
| `juristid-mcp-tunnel.env` | `CONTROL_PLANE_TUNNEL_ID` | the tunnel id from step 1 |
| `juristid-mcp-tunnel.env` | `MCP_OAUTH_TRUSTED_ORIGINS` | `https://juristid.orgusaar.ee` |

No `replace-me…` may remain in either file: a placeholder stops the process
(juristid.E016). The preflight now checks both files' modes and refuses one
that names a key it must not hold.

### 3. The web environment — the keys first, then the switch

**The keys before `MCP_ENABLED=1`, always.** `web` will not start on real data
with `MCP_ENABLED=1` and a missing or weak key (mcp_access.E004) — and `web` is
all of Juristid, not only AI access (security review NEW-09). With
`MCP_ENABLED=0` it needs none of them and starts as before.

The keys live in `juristid.env` only — never in `juristid-mcp.env`, which the
preflight and juristid.E046 refuse:

- `MCP_AUTHENTICATOR_KEY` — what the personal authenticators are sealed under
  (MCPSEC-07).

Generate them straight into the file, so no value reaches the terminal or the
shell history. The helper keeps a key that is already set (it never rotates
one), replaces the template's commented line, a placeholder or a short value,
appends the line when the file has none at all, and then proves its own write:

```bash
scripts/deploy/juristid-mcp-web-keys.sh --env-file /mnt/user/appdata/juristid-main/config/juristid.env
```

It must end with `Only now set MCP_ENABLED=1.` Check it yourself as well; this
must print exactly `1`:

```bash
grep -c '^MCP_AUTHENTICATOR_KEY=.\{40,\}' /mnt/user/appdata/juristid-main/config/juristid.env
```

Anything other than `1` — or a `FAIL` from the helper — and you stop here,
with `MCP_ENABLED` still off. Run the helper again; it is idempotent.

**Only then**, in `juristid.env`, in an editor, the three lines the template
added (read their comments in `deploy/unraid-main/.env.example`):

| Key | Value |
| --- | --- |
| `MCP_ENABLED` | `1` |
| `JURISTID_PUBLIC_ORIGIN` | `https://juristid.orgusaar.ee` — identical to `juristid-mcp.env` |
| `MCP_RESOURCE_URLS` | identical to `juristid-mcp.env` |

The two files must agree on the last two: `web` issues a token for a resource,
`mcp` accepts it only if the resource is on its own list. The preflight in
step 5 refuses a `juristid.env` that has `MCP_ENABLED` on without the keys, so
the recreate below is never reached with a `web` that cannot start.

**Rotating it** makes every enrolled authenticator unreadable: the next
authorization of each colleague is refused with the generic message, the `web`
log says `the authenticator secret cannot be read`, and nobody's wrong-code
counter moves. Rotate only together with re-enrolling everybody —
`mcp_access revoke --user …`, `grant`, `enrolment-link`, for each person. A
`DJANGO_SECRET_KEY` rotation, by contrast, no longer touches the
authenticators.

### 4. Name the release that is running

Starting `mcp` resolves the release image from the same two variables a release
does, and without them it resolves `juristid-main-web:local` — whatever was
last hand-built here. So read the running release and export it, in this shell:

```bash
docker inspect --format '{{.Config.Image}}' juristid-main-web
```

```bash
docker exec juristid-main-web cat /app/GIT_SHA
```

```bash
export JURISTID_GIT_SHA=<the full 40-character sha the second command printed>
```

```bash
export JURISTID_IMAGE_TAG=${JURISTID_GIT_SHA:0:12}
```

The first command must print `juristid-main-web:` followed by exactly
`$JURISTID_IMAGE_TAG`. Anything else and you are not looking at a release this
runbook knows — stop.

### 5. Preflight, then start the server

```bash
scripts/deploy/juristid-deploy-preflight.sh --repo /mnt/user/appdata/juristid-main/repo --target $JURISTID_GIT_SHA --compose-file deploy/unraid-main/compose.yml --env-file /mnt/user/appdata/juristid-main/config/juristid.env --data-root /mnt/user/appdata/juristid-main --backup-root /mnt/user/backups/juristid-main
```

It must say both MCP files are mode 600, that the AI-access services resolve
and that web's AI-access keys are in place. Then recreate the application services with the new web environment
— every service named, `--no-deps` so neither `db` nor `tunnel` moves,
`--no-build` so a missing tag is an error and never a build:

```bash
docker compose -p juristid-main -f deploy/unraid-main/compose.yml up -d --no-build --no-deps web intake-reader searchindex intake-sweeper
```

**Create the MCP server's database role before it ever starts** (MCPSEC-03).
`web` runs it, because `web`'s role owns the tables; the password goes from
`juristid-mcp.env` to the command's stdin and is printed nowhere — not by
`sed`, not by the command, which hands PostgreSQL only its SCRAM verifier:

```bash
sed -n 's/^POSTGRES_PASSWORD=//p' /mnt/user/appdata/juristid-main/config/juristid-mcp.env | docker compose -p juristid-main -f deploy/unraid-main/compose.yml exec -T web python manage.py mcp_db_role --apply --password-stdin
```

It answers `Role juristid_mcp: SELECT only on schema public (owner …), read-only
by default, 1 table(s) withheld; password set.` It is idempotent: run it again
whenever in doubt, and after rotating the password. To read the SQL first,
`… exec -T web python manage.py mcp_db_role --print-sql` prints it and changes
nothing. A later release's new tables are readable without re-running it (the
role has a default privilege on the owner's future tables).

Then start the MCP server:

```bash
docker compose -p juristid-main -f deploy/unraid-main/compose.yml up -d --no-build --no-deps mcp
```

If it restarts in a loop with `juristid.E047`, it is connected as a role that
can write — `POSTGRES_USER` in `juristid-mcp.env` is not `juristid_mcp`, or the
role step above was skipped.

Wait for both to be healthy (`starting` for up to a minute is normal):

```bash
docker inspect --format '{{.Name}} {{.State.Health.Status}}' juristid-main-web juristid-main-mcp
```

Then the deeper check, inside the server:

```bash
docker compose -p juristid-main -f deploy/unraid-main/compose.yml exec -T mcp python manage.py check_mcp_server
```

And confirm the OAuth side answers through the public name — this is metadata,
not data, and safe to read:

```bash
curl -s https://juristid.orgusaar.ee/.well-known/oauth-authorization-server
```

It must name `https://juristid.orgusaar.ee` as `issuer` and `S256` among the
code challenge methods. A 404 means `web` did not pick up `MCP_ENABLED=1`.

### 6. Start the tunnel client

```bash
docker compose -p juristid-main -f deploy/unraid-main/compose.yml up -d --no-build --no-deps mcp-tunnel
```

```bash
docker compose -p juristid-main -f deploy/unraid-main/compose.yml exec -T mcp-tunnel wget -q -O - http://127.0.0.1:8080/readyz
```

`ready` is the answer to wait for (the first minute may say `503 oauth discovery
pending`). What else it can say is under *Diagnosing* below. The client's own
log shows it polling OpenAI:

```bash
docker compose -p juristid-main -f deploy/unraid-main/compose.yml logs --tail 50 mcp-tunnel
```

### 7. Connect ChatGPT — in the workspace

By a workspace admin of **Kaubanduskoda** who has Tunnels **Read** + **Use**
(labels as ChatGPT shows them in October 2026; the console moves things around):

1. ChatGPT → **Settings** → **Plugins** (Apps & Connectors) → **Add** →
   **Add custom MCP server**.
2. Name `Juristid`. **Connection: Tunnel**, and pick the tunnel from step 1 or
   paste its id. The tunnel is offered only while `mcp-tunnel` is ready.
3. **Authentication: OAuth.** No client id or secret to enter: ChatGPT
   identifies itself by its client metadata document, which Juristid has
   pre-registered (`MCP_OAUTH_CLIENTS`). ChatGPT reads the metadata, finds
   `https://juristid.orgusaar.ee` as the authorization server and opens it in
   the admin's browser.
4. Complete the sign-in (step 8 must have been done for the admin's own account
   first, or this step is refused — correctly). ChatGPT then lists the eight
   tools; every one should show as read-only.
5. **Confirm the resource.** If the token step fails with a resource refusal,
   the `web` log names the resource ChatGPT asked for (step *Diagnosing*); put
   exactly that URL in `MCP_RESOURCE_URLS` in **both** files, recreate `web` and
   `mcp` (*Restarting only the MCP component* and the step 5 command), and try
   again.
6. **Admin Console → Plugins** (Connectors): **publish** the plugin to the
   workspace, restricted to the people who will use it. ChatGPT keeps a frozen
   copy of the tool list from this moment (see *Updating the tools*).

### 8. Let the first person in

*Granting and revoking a person*, below, for the admin who completes step 7
first, then for each colleague.

### 9. Prove it end to end

From a granted colleague's ChatGPT: «Mis seis on teemaga <a known reference>?»
The answer must cite `https://juristid.orgusaar.ee/…` links, and opening one
must show the record. Then from an account that is **not** granted: connecting
must be refused at the authorization step. Record both in the enablement report.

## Health

| Question | Command | Healthy answer |
| --- | --- | --- |
| Are both containers up and healthy? | `docker inspect --format '{{.Name}} {{.State.Health.Status}}' juristid-main-mcp juristid-main-mcp-tunnel` | `healthy` twice |
| Does the server answer and see its database? | `docker compose -p juristid-main -f deploy/unraid-main/compose.yml exec -T mcp python manage.py check_mcp_server` | exit 0 |
| Is the tunnel client ready to take work? | `docker compose -p juristid-main -f deploy/unraid-main/compose.yml exec -T mcp-tunnel wget -q -O - http://127.0.0.1:8080/readyz` | `ready` |
| What has the tunnel client observed? | `docker compose -p juristid-main -f deploy/unraid-main/compose.yml exec -T mcp-tunnel wget -q -O - 'http://127.0.0.1:8080/health?details=true'` | `"ready": true`; per-component detail |
| Who holds access? | `docker compose -p juristid-main -f deploy/unraid-main/compose.yml exec -T web python manage.py mcp_access list` | the people you granted, nobody else; `lukus kuni` `-` and `valesid koode` small |
| Is anybody guessing authenticator codes? | `docker compose -p juristid-main -f deploy/unraid-main/compose.yml logs --since 24h web \| grep 'MCP grant'` | nothing. Every lock and every suspension prints one WARNING line naming the grant and the account (never a code); a suspension means twenty wrong codes without a success |

The tunnel client's health and admin endpoints listen on the container's
loopback only (`HEALTH_LISTEN_ADDR=127.0.0.1:8080`): they are reachable through
`exec` and from nowhere else, which is deliberate — its embedded admin UI can
change the log level and export logs.

`docker inspect` showing `unhealthy` for `mcp-tunnel` while `mcp` is healthy is
almost always the OpenAI side (key, tunnel id, network) — *Diagnosing*.

## Granting and revoking a person

The grant is an explicit, audited, per-account decision (ADR 0185 §3), made in
`web`, which is the process that may write:

```bash
docker compose -p juristid-main -f deploy/unraid-main/compose.yml exec -T web python manage.py mcp_access grant --user <upn>
```

It refuses an account that is inactive or has no business role — an
ADMINISTRATOR without one included. Then the person's one-time enrolment link,
which lets them register their own authenticator (TOTP) inside their own gate
session and persona:

```bash
docker compose -p juristid-main -f deploy/unraid-main/compose.yml exec -T web python manage.py mcp_access enrolment-link --user <upn>
```

**The link is a credential.** It is valid for `MCP_ENROLMENT_LINK_SECONDS`
(30 minutes) and works once. Give it to that person directly — in person or in a
private message to them — and never in a shared channel, a ticket or a report.
If it expires, run the command again.

Ending one person's access ends the grant and every token issued under it at
once; their next ChatGPT call is refused:

```bash
docker compose -p juristid-main -f deploy/unraid-main/compose.yml exec -T web python manage.py mcp_access revoke --user <upn>
```

Deactivating the account, changing its role or bumping its security epoch has
the same effect on its tokens without a separate command. The audit trail of
grants and revocations is in Juristid's own audit log.

<!-- TODO(integrator, workstream C): reconcile the flags above with
`app/mcp_access/management/commands/mcp_access.py` (`--user` per ADR 0185 §3)
before the first enablement. -->

## Switching AI access off — immediately, without stopping Juristid

One command, and ChatGPT can no longer reach anything. `web`, the gate, search
and every other service carry on untouched:

```bash
docker compose -p juristid-main -f deploy/unraid-main/compose.yml stop mcp-tunnel mcp
```

`stop`, not `down`: the containers stay for the logs and for a quick restart.
Because both are behind the profile, nothing restarts them — not a release, not
a host reboot's `unless-stopped` (a stopped container stays stopped), not a
restore.

To make every outstanding token worthless as well — for a suspected leak, or
before a long pause — revoke every grant:

```bash
docker compose -p juristid-main -f deploy/unraid-main/compose.yml exec -T web python manage.py mcp_access revoke-all
```

To also close the OAuth routes on `web` (they then answer 404 and no new grant,
enrolment or token can be made), set `MCP_ENABLED=0` in `juristid.env` and
recreate the application services with the running release's exports from
*First-time enablement* step 4:

```bash
docker compose -p juristid-main -f deploy/unraid-main/compose.yml up -d --no-build --no-deps web intake-reader searchindex intake-sweeper
```

Order of strength: `stop` cuts the path, `revoke-all` voids the credentials,
`MCP_ENABLED=0` removes the door. In an incident do the first at once and the
other two straight after.

## Starting and restarting only the MCP component

Restart without changing anything (a wedged process, a rotated key file):

```bash
docker compose -p juristid-main -f deploy/unraid-main/compose.yml restart mcp mcp-tunnel
```

`restart` keeps the container's configuration. After **editing an environment
file**, recreate instead, with the running release's exports from
*First-time enablement* step 4 in this shell:

```bash
docker compose -p juristid-main -f deploy/unraid-main/compose.yml up -d --no-build --no-deps mcp
```

```bash
docker compose -p juristid-main -f deploy/unraid-main/compose.yml up -d --no-build --no-deps mcp-tunnel
```

Start them again after `stop` the same way. Then the checks under *Health*.

## Diagnosing a failed search or a refused connection

**Where the logs are.** All of them are bounded container logs (50 MB each):

```bash
docker compose -p juristid-main -f deploy/unraid-main/compose.yml logs --tail 200 mcp
```

```bash
docker compose -p juristid-main -f deploy/unraid-main/compose.yml logs --tail 200 mcp-tunnel
```

```bash
docker compose -p juristid-main -f deploy/unraid-main/compose.yml logs --tail 200 web
```

The `mcp` log goes to stdout and never to the database (ADR 0185 §1): it is
meant to record each call by tool name, outcome and duration, never the
question asked or the text returned. A traceback can still carry what the code
held, so like every log here it stays on this host (OPERATOR.md).

**What a refusal means.**

| Seen | Where | Means | Do |
| --- | --- | --- | --- |
| HTTP 401 on a tool call | `mcp` log; ChatGPT asks the person to reconnect | the bearer token is missing, unknown, expired (1 hour), revoked, or issued for a resource not in `MCP_RESOURCE_URLS`; or the grant ended, the account was deactivated or its security epoch moved | usually nothing — ChatGPT refreshes or re-authorizes. Repeated for everybody after a change: compare `MCP_RESOURCE_URLS` in both files |
| HTTP 403 on a tool call | `mcp` log | the token is valid but lacks the `juristid.read` scope | a client asking for the wrong scope; reconnect the plugin |
| A tool answers «not found» | ChatGPT | the id is unknown **or** not visible to that person — deliberately indistinguishable | check the record in the browser *as that person's persona* |
| ChatGPT asks a colleague to reconnect every 90 days | ChatGPT | the token family's absolute lifetime (`MCP_REFRESH_FAMILY_MAX_SECONDS`, MCPSEC-08): the connection is as old as the authenticator code typed for it | nothing; it is the design. `McpAccessEvent` reason `family_expired` |
| `invalid_grant` / `invalid_target` at `/oauth/token` | `web` log | an expired or reused code, a PKCE mismatch, or a `resource` not in `MCP_RESOURCE_URLS` | for a resource refusal, the log names the resource requested: that is the URL `MCP_RESOURCE_URLS` must list |
| «Kood ei sobi või sellel kasutajal pole MCP ligipääsu» | browser | a wrong code, no grant, a pending grant (never enrolled), wrong persona, or no business role — deliberately one message, so the page does not reveal who holds a grant | `mcp_access list`; a new `enrolment-link` if they never enrolled |
| «Liiga palju valesid koode» (after the person's own right code) | browser | the grant is locked: five wrong codes in a row; consecutive locks last 15 min, 1 h, 4 h | wait, or `mcp_access unlock --user <upn>` once you know the wrong codes were the person's own |
| «Ligipääs on peatatud» | browser; `web` log WARNING `MCP grant … suspended` | the fourth consecutive lock — twenty wrong codes without a success. Treat it as somebody guessing at that colleague's code | talk to the person first; then `mcp_access unlock --user <upn>`. The wrong-code count stays in `mcp_access list` until their next success |
| «Liiga palju katseid» | browser | this gate session or this address failed too often in 15 minutes, whichever personas it tried | wait; repeated, it is somebody spraying codes — the `web` log and `McpAccessEvent` rows with reason `throttled` show when |
| `/readyz` says `503 oauth discovery failed` | `mcp-tunnel` | the client could not read `mcp`'s protected-resource metadata, or the metadata named an origin it does not trust | `MCP_OAUTH_TRUSTED_ORIGINS` must be exactly the public origin; `mcp` must be healthy |
| `/readyz` says `503 mcp probe failed` | `mcp-tunnel` | the client cannot reach `http://mcp:8001/mcp` | `mcp` stopped or unhealthy |
| `mcp-tunnel` log: 401/403 from the control plane | `mcp-tunnel` | the runtime key is wrong, expired or lacks Tunnels Read + Use, or the tunnel id is not the key's organisation's | a new restricted key in the tunnel file, then recreate `mcp-tunnel` |
| The tunnel is not offered in ChatGPT | ChatGPT | the tunnel lacks the workspace scope, the admin lacks Tunnels Use, or `mcp-tunnel` is not ready | Platform → Tunnels; `/readyz` |
| `mcp` restarts in a loop | `docker inspect`, `mcp` log | a system check refused its configuration (`SystemCheckError`, `juristid.E0xx`) | the message names the variable; fix the file, never the check |

<!-- TODO(integrator, workstreams B and C): align the 401/403 rows and the
`/oauth/token` error names with the final server and token-endpoint code. -->

## Rolling back

**A code rollback** of Juristid follows the release runbook; where `mcp` is
running, step 9's `mcp` line moves it to the rolled-back image with the others.

**Rolling back AI access itself** — switching it off after an enablement went
wrong — is *Switching AI access off* above: `stop`, then `revoke-all`, then
`MCP_ENABLED=0`. Nothing else changed: no existing table was altered, no
record rewritten, no index rebuilt (ADR 0185, *Consequences*). The
`mcp_access` tables stay, empty of any live grant; rolling the *code* back below
the release that created them is RECOVERY.md's schema decision tree, like any
migration.

**Upgrading the tunnel client** is a deliberate change like `tunnel`'s: read
the release notes at `https://github.com/openai/tunnel-client/releases`, resolve
the new tag's index digest from `ghcr.io`, change tag and digest in
`compose.yml` (`tests/test_supply_chain_pinning.py` holds the format), release
that commit, then recreate only `mcp-tunnel`. Rolling it back is the same with
the previous pin.

## Updating the tools and refreshing the connector

ChatGPT keeps a **frozen snapshot** of the tool list taken when the plugin was
added or last refreshed. A release that adds, removes or changes a tool's
name, description or schema is not seen by anybody until a workspace admin:

1. deploys the release (step 9 replaces `mcp`);
2. opens **Admin Console → Plugins → Juristid** and chooses **Refresh** (it
   re-reads the tool list through the tunnel — `mcp-tunnel` must be ready);
3. reviews the tools shown, every one still read-only;
4. **publishes** again.

A release whose notes do not mention the MCP tools needs none of this.

## Residual risks

What the security review (2026-10-22) left open on purpose, so that nobody
mistakes a known gap for an oversight. MCPSEC-01 to -10, -13, -14 and -16 are
fixed in code; these are not, and each says what holds instead.

- **Consent phishing (MCPSEC-11).** A colleague can start a connection in their
  own ChatGPT and send the `/oauth/authorize?…` address to somebody else to
  approve. Whether that binds the approver's account to the sender's connector
  depends on how ChatGPT ties `state` to the browser, which Juristid cannot see.
  What holds: the consent page names the client and the acting persona, and
  says to continue only if you started this connection yourself, just now, and
  to press «Keeldu» if somebody sent you the link. Tell colleagues the same
  when you grant them.
- **The enrolment link is a bearer secret (MCPSEC-12).** For 30 minutes,
  whoever holds it and the gate password can enrol their own phone as that
  person; the real person then only sees «link invalid». So: hand it over in
  person or read it out by phone, never by e-mail, Teams or a ticket; and
  after `ENROLMENT_COMPLETED` appears (`mcp_access list` shows `autentija
  jah`), confirm with the person that it was them before they connect
  ChatGPT. If they say no: `mcp_access revoke`, then `grant` again.
- **The audience list, not the arrival resource (MCPSEC-15).** A token minted
  for any `MCP_RESOURCE_URLS` entry is accepted at every entry. Harmless while
  the tunnel URL is the only one. Before a public MCP URL is ever added (which
  needs the owner's approval anyway), bind the token to the resource it
  arrived at.
- **Search freshness (MCPSEC-17).** `search` and `search_records` titles and
  snippets come from the search projection, so a record removed a moment ago
  can still surface by title until it is reindexed; `fetch` re-checks and
  answers not-found. `check_search_freshness` covers it as it does for
  `/otsing/`.
- **Two factors, not one person (ADR 0185, *Consequences*).** Somebody who
  knows the gate password and holds a colleague's authenticator can connect as
  that colleague. The lockout (MCPSEC-01) bounds guessing; it cannot help
  against a phone that was handed over.

## What must not be done

- **Never give `mcp` or `mcp-tunnel` a host port**, a Cloudflare hostname or
  another network, and never put any other service on `mcp_edge`: the tunnel
  client must reach `mcp` and nothing else (MCPSEC-02,
  `tests/test_deployment_unraid_main.py`). The fallback ADR 0185 names — a public MCP hostname — widens
  the surface and needs the owner's explicit approval first.
- **Never put `juristid.env` in either service**, and never copy the gate
  password, the MFA key or a mail credential into `juristid-mcp.env`.
  `tests/test_service_secret_isolation.py` and the preflight both refuse it.
- **Never start `mcp` or `mcp-tunnel` as a side effect** — of a release, a
  restore, a script. They start because somebody decided, by name.
- **Never print** the runtime key, the signing key, the database password or an
  enrolment link — not to check it, not in a "masked" form.
- **Never grant an account because an address looks right.** A grant is for a
  named person who asked, approved by the owner.
