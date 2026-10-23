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
| AI writes: why prepare → approve → commit, and the gateway | ADR 0189; *Phase 2: AI writes* below |

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

**It cannot** write anything — there is no write tool (Phase 1; AI *writes*,
each one approved by the person on Juristid's own page, are the separate
gateway in *Phase 2: AI writes* below, off unless switched on), the process connects
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
  (MCPSEC-07);
- `MCP_TOKEN_DERIVATION_KEY` — what a rotated refresh token's successor is
  derived under (NEW-01), so that `DJANGO_SECRET_KEY` and one refresh token
  are not enough to compute a connection's later tokens (mcp_access.E005).

Generate them straight into the file, so no value reaches the terminal or the
shell history. The helper keeps a key that is already set (it never rotates
one), replaces the template's commented line, a placeholder or a short value,
appends the line when the file has none at all, and then proves its own write:

```bash
scripts/deploy/juristid-mcp-web-keys.sh --env-file /mnt/user/appdata/juristid-main/config/juristid.env
```

It must end with `Only now set MCP_ENABLED=1.` Check it yourself as well; each
of these must print exactly `1`:

```bash
grep -c '^MCP_AUTHENTICATOR_KEY=.\{40,\}' /mnt/user/appdata/juristid-main/config/juristid.env
```

```bash
grep -c '^MCP_TOKEN_DERIVATION_KEY=.\{40,\}' /mnt/user/appdata/juristid-main/config/juristid.env
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
authenticators. **Rotating `MCP_TOKEN_DERIVATION_KEY`** costs at most a
refresh retried inside the 30-second grace window (that family is revoked and
the person reconnects); nobody re-enrols. The helper never rotates a key it
finds: to rotate one, delete its line in an editor and run the helper again.

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
by default, statement_timeout 60s, temp files at most 256MB, 1 table(s)
withheld; password set.` Besides SELECT, the role carries its bounds as role
settings, applied at every login (NEW-02): `temp_file_limit` 256 MB (which it
cannot raise), `statement_timeout` and `idle_in_transaction_session_timeout`
equal to the tool-call timeout, `default_transaction_read_only = on`, and a
connection limit of 16. If `juristid-mcp.env` sets `MCP_CALL_TIMEOUT_SECONDS`
to something other than 60, add `--statement-timeout-seconds <that value>`.
`juristid.E047` stops `mcp` if any of them is missing. It is idempotent: run it
again whenever in doubt, and after rotating the password. To read the SQL first,
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
| Is anybody locked or suspended right now? | the `mcp_access list` command above | `lukus kuni` is `-` for everybody, no `PEATATUD`, `valesid koode` small. Nothing pages anybody (NEW-04): this and the WARNING line are the alert, so look at both at every release and whenever a colleague says connecting fails |

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
question asked or the text returned. The MCP SDK's own log is held at WARNING,
because its INFO lines quote what the caller sent (a resource URI, a prompt or
tool name; NEW-10), and no JSON-RPC error the server answers carries the
caller's text either. A traceback can still carry what the code held, so like
every log here it stays on this host (OPERATOR.md).

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

## Phase 2: AI writes — prepare, approve in Juristid, commit (docs/adr/0189)

A colleague can ask ChatGPT to *prepare* an ordinary, reversible change — a
note, a meeting, a procedural link, the next step, completing a step, the
opinion deadline, an important date, a new Teema, its title or summary, its
owner. **Nothing is written because a model asked.** Every change is:

1. **prepared** — the MCP client calls a `prepare_<operation>` tool; `mcp`
   forwards it, with the person's own bearer token, to the write gateway, which
   validates it through the same rules as the page and stores a *proposal*
   with a preview. No business record changes;
2. **approved** — the person opens the approval link on Juristid's own page
   (`/mcp-ligipaas/muudatus/<id>/`, behind the gate, as their own persona),
   reads the stored preview and confirms with their own MCP authenticator. Only
   `web` writes that approval row;
3. **committed** — the client calls `commit_prepared_action`; the gateway
   checks the approval, re-checks that the record has not moved on, and runs
   the canonical service the page runs — once, whatever the retries.

No delete, no file upload, no URL fetched, no mail or notification sent, no
SQL, no account, credential or settings change, no «opinion sent». A technical
administrator without a business role never writes, whatever a token says.

### Topology

| Service | Container | Environment | Database role | Networks | Reaches |
| --- | --- | --- | --- | --- | --- |
| `web` | `juristid-main-web` | `juristid.env` + `MCP_WRITES_ENABLED` (nothing else new) | owner | `internal` | serves the approval page; never calls the gateway |
| `mcp` | `juristid-main-mcp` | `juristid-mcp.env` + `MCP_WRITES_ENABLED`, `MCP_WRITE_GATEWAY_KEY`; `MCP_WRITE_GATEWAY_URL=http://mcp-writer:8002` from compose.yml | `juristid_mcp`, SELECT only, read-only sessions — unchanged | `internal`, `mcp_edge`, `mcp_write` | `db`; the gateway over `mcp_write` |
| `mcp-writer` | `juristid-main-mcp-writer` | `juristid-mcp-writer.env` only | `juristid_mcp_writer` (least privilege, below) | `internal`, `mcp_write` | `db`; listens on `mcp-writer:8002`, no host port |
| `mcp-tunnel` | `juristid-main-mcp-tunnel` | unchanged | — | `mcp_edge` only | `mcp` — **not** `mcp-writer`: the two share no network |

`mcp_write` (`juristid-main-mcp-write`) is `internal: true` — no route off the
host — and holds `mcp` and `mcp-writer` only. `mcp-writer` is behind the `mcp`
profile, runs the release image with a read-only root, every capability
dropped, `no-new-privileges`, bounded memory/CPU/processes, and mounts nothing.
Its checks refuse it: writes off (E051), no strong gateway key (E052), no https
origin (E053), MCP access off or no https audiences — it verifies each
forwarded bearer token itself — (E054), debug (E055), any browser secret: gate
password, MFA key, MCP authenticator or derivation key, mail password (E056),
and a database role that is not the writer role below (E050). `web` refuses to
start holding the gateway key (E057); no process but `mcp-writer` may connect
as the writer role (E058).

The gateway key is **not** authorization. It proves the caller is the `mcp`
container; every request is authorized by the person's own token, re-verified
by the gateway (hash, `juristid.write` scope, grant active and write-enabled,
account active, security epoch, a business role), and every commit by the
approval row.

### The writer's database role

`juristid_mcp_writer` — `app/mcp_writes/db_role.py`, `WRITE_ALLOWLIST`, each
table with its reason. LOGIN; NOSUPERUSER, NOCREATEDB, NOCREATEROLE, NOINHERIT,
NOREPLICATION, NOBYPASSRLS; member of no role, owner of nothing; no CREATE on
the database or schema, no TRUNCATE, REFERENCES or TRIGGER anywhere. SELECT on
every table but `django_session`. Writes only:

| Table | Privileges | Why |
| --- | --- | --- |
| `matters_matter` | INSERT, UPDATE | create, title, summary, owner, deadline columns; the Matter row lock every write takes |
| `matters_matterreferencesequence` | INSERT, UPDATE | the `YYYY_N` counter (locked; created for a year's first Matter) |
| `matters_matterassignmentnotice` | INSERT, UPDATE | «Uus asi» raised and retired by an assignment |
| `matters_entry` | INSERT | note, meeting, the result of a completed step |
| `matters_mattersaveonce` | INSERT | the save-once ledger (the proposal id is the token) |
| `matters_matterprocedurallink` | INSERT, UPDATE | the link, and its idempotency row lock |
| `matters_matterresponsedeadline` | INSERT | the deadline's request history (append-only) |
| `workflow_nextaction` | INSERT, UPDATE | the current step: new, superseded, completed |
| `workflow_matterplanstep` | INSERT, UPDATE | the plan a new Teema gets; a plan-derived step completed |
| `workflow_opinionfollowup` | UPDATE | a follow-up check's outcome |
| `intelligence_matterimportantdate` | INSERT | an important date |
| `audit_changeevent` | INSERT | the history every service appends (append-only triggers) |
| `search_searchdocument` | INSERT, UPDATE, DELETE | the search projection's synchronous refresh — **the one DELETE**: stale projection rows are replaced; derived data, rebuilt by `rebuild_search_index` |
| `mcp_writes_mcpwriteproposal` | INSERT, UPDATE | the gateway's proposals |
| `mcp_writes_mcpwriteevent` | INSERT | the gateway's append-only events |

**Never** any write on `accounts_*`, `mcp_access_*`, `django_*`, `auth_*` or
`mcp_writes_mcpwriteapproval` (SELECT only: only `web` approves) — the command
refuses an allow-list naming one. The list is proven, not guessed:
`tests/test_mcp_writer_allowlist.py` runs every canonical service of every
operation under the role itself and fails both on a missing grant and on a
grant no operation uses, and `tests/test_mcp_writer_role_runs_operations.py`
runs the operations' whole test suite again as the role. Role settings:
`statement_timeout` and `idle_in_transaction_session_timeout` equal to
`MCP_WRITE_CALL_TIMEOUT_SECONDS` (30), `temp_file_limit` 64 MB, `CONNECTION
LIMIT` 8. New tables are readable by default and never writable: a release
whose operations write a new table adds it to the list and re-runs the command
(the gateway's E050 names the table it cannot write).

### First-time enablement of writes

Precondition: Phase 1 runs (`mcp` and `mcp-tunnel` healthy), the release that
carries Phase 2 is deployed with writes off everywhere, and the owner has said
go. The full sequence with its checks is the release kit's
(`C:\CC\_worklogs\mcp-phase2\DEPLOY-PLAN.md`); in short, on the host, from the
checkout, with the running release's exports from step 4 in this shell:

**1. The writer's environment file**, root's and 600:

```bash
install -m 600 -o root -g root deploy/unraid-main/juristid-mcp-writer.env.example /mnt/user/appdata/juristid-main/config/juristid-mcp-writer.env
```

Fill in `JURISTID_PUBLIC_ORIGIN` and `MCP_RESOURCE_URLS` in an editor — the same
values as in `juristid-mcp.env`. Leave `POSTGRES_USER=juristid_mcp_writer`.

**2. The secrets**, generated into the files and never printed — the writer's
signing key and database password, and one gateway key into both
`juristid-mcp-writer.env` and `juristid-mcp.env` (and a check that
`juristid.env` holds none):

```bash
scripts/deploy/juristid-mcp-writer-keys.sh --writer-env-file /mnt/user/appdata/juristid-main/config/juristid-mcp-writer.env --mcp-env-file /mnt/user/appdata/juristid-main/config/juristid-mcp.env --web-env-file /mnt/user/appdata/juristid-main/config/juristid.env
```

It must end with `Every secret the write gateway needs is in place.`

**3. The writer's database role**, created from `web` (its role owns the tables
and is the image's superuser); the password goes from the file to stdin and is
printed nowhere, and PostgreSQL is handed only its SCRAM verifier:

```bash
sed -n 's/^POSTGRES_PASSWORD=//p' /mnt/user/appdata/juristid-main/config/juristid-mcp-writer.env | docker compose -p juristid-main -f deploy/unraid-main/compose.yml exec -T web python manage.py mcp_writer_db_role --apply --password-stdin
```

It answers `Role juristid_mcp_writer: SELECT on schema public (owner …) except
1 withheld table(s); writes on 15 allow-listed table(s); statement_timeout 30s,
temp files at most 64MB; password set.` A line `Not yet in this database` means
the release's migrations have not run — stop. To read the SQL first,
`… exec -T web python manage.py mcp_writer_db_role --print-sql` changes
nothing. Idempotent; run it again after rotating the password and after every
release that adds a table to the allow-list.

**4. Start the gateway** — its checks run first, so a refused configuration
exits rather than listening:

```bash
docker compose -p juristid-main -f deploy/unraid-main/compose.yml up -d --no-build --no-deps mcp-writer
```

```bash
docker inspect --format '{{.Name}} {{.State.Health.Status}}' juristid-main-mcp-writer
```

```bash
docker compose -p juristid-main -f deploy/unraid-main/compose.yml exec -T mcp-writer python manage.py check_mcp_write_gateway
```

A restart loop naming `juristid.E050` means the role step was skipped or
`POSTGRES_USER` is wrong; E056 means a browser secret is in its file.

**5. Prove the tunnel cannot reach it** — the tunnel client must not even
resolve the name (busybox in its image):

```bash
docker exec juristid-main-mcp-tunnel nslookup mcp-writer
```

```bash
docker exec juristid-main-mcp-tunnel wget -q -T 3 -O /dev/null http://mcp-writer:8002/healthz
```

Both must fail (`can't resolve` / `bad address`). If either succeeds, stop the
gateway (*Switching AI writes off*) and fix the networks before anything else.

**6. Switch writes on**, `mcp` first, then `web` (the approval page and the
scope): set `MCP_WRITES_ENABLED=1` in `juristid-mcp.env` and recreate `mcp`;
then in `juristid.env` and recreate the application services:

```bash
docker compose -p juristid-main -f deploy/unraid-main/compose.yml up -d --no-build --no-deps mcp
```

```bash
docker compose -p juristid-main -f deploy/unraid-main/compose.yml up -d --no-build --no-deps web intake-reader searchindex intake-sweeper notifications
```

**7. Refresh the connector** (*Updating the tools and refreshing the
connector*): the write tools are new tools, and ChatGPT shows them only after
an admin refreshes and publishes. Review that every `prepare_*` tool and
`commit_prepared_action` are listed with their annotations.

**8. Let one person write.** Write is granted per person, on top of an existing
grant, only for an active account with a business role:

```bash
docker compose -p juristid-main -f deploy/unraid-main/compose.yml exec -T web python manage.py mcp_access grant-write --user <upn>
```

The person then reconnects ChatGPT (a new authorization asks for
`juristid.write`; the consent page says «Lugemine ja muudatuste
ettevalmistamine»). `mcp_access list` shows the write state.

### Smoke tests

On the host, after step 6, nothing changing any record:

- the eight read tools still answer (a `search` from ChatGPT, as in step 9 of
  Phase 1);
- `mcp-writer` is `healthy` and `check_mcp_write_gateway` passes;
- step 5's two commands fail from `juristid-main-mcp-tunnel`;
- from `juristid-main-web`, which holds no key, a gateway call is refused —
  the key, not the network, is what `internal` neighbours lack:

```bash
docker compose -p juristid-main -f deploy/unraid-main/compose.yml exec -T web python -c "import urllib.request as u; r=u.Request('http://mcp-writer:8002/v1/prepare', data=b'{}', method='POST'); print(u.urlopen(r).status)"
```

  must end in `HTTP Error 401` — never a 2xx.

Then, with the owner, one end-to-end change on a test Teema the owner names:
prepare a note from ChatGPT, open the link, check the preview, approve with the
code, commit; the note appears once, its history row says «ChatGPT kaudu,
isiklikult kinnitatud»; commit again — `replayed`, still one note.

### Switching AI writes off — four switches, any one is enough

```bash
docker compose -p juristid-main -f deploy/unraid-main/compose.yml stop mcp-writer
```

**Stopping `mcp-writer` disables every AI write at once while reading goes
on**: prepare and commit answer «writes unavailable», the read tools are
untouched, nothing depends on the gateway. It stays stopped through releases,
restores and reboots until somebody starts it by name.

- **One person:** `… exec -T web python manage.py mcp_access revoke-write --user <upn> --reason "<why>"` — their read access stays; their next token refresh drops the write scope, and the gateway refuses their current token at once (it re-checks the grant on every call).
- **Everybody's write grants:** `… exec -T web python manage.py mcp_access revoke-all-writes` — reading untouched.
- **A process:** `MCP_WRITES_ENABLED=0` on any one of `web` (no approval page, no write scope), `mcp` (no write tools listed) or `mcp-writer` (refuses to start, E051), then recreate that service.

In an incident: `stop mcp-writer` first, `revoke-all-writes` straight after,
then the flags. Pending proposals simply expire (`MCP_WRITE_PROPOSAL_SECONDS`).

### Rolling back

- **Writes only:** the switches above. No canonical record is rewritten by
  switching off; committed changes stay as ordinary records with their history
  rows, changed back through the pages like any other edit.
- **The code:** a rollback to the previous release (`PREVIOUS_SHA`) follows the
  release runbook; first `stop mcp-writer` and set `MCP_WRITES_ENABLED=0` on
  `web` and `mcp` (the previous image does not know the setting or the
  service). The new tables (`mcp_writes_*`, `mcp_access` 0002's columns) stay
  in the schema; the previous code ignores them. Dropping the role is not part
  of a rollback; if decided: `REASSIGN`/`DROP OWNED` are unnecessary (it owns
  nothing) — `DROP ROLE juristid_mcp_writer` as the owner.

### What AI writes leave open

- **The gateway shares `internal` with `web`, the workers and `tunnel`**, because
  it needs `db` and `db` is on `internal` only (moving `db` is the deliberate
  change *db/tunnel* pinning forbids). Anything on `internal` can open a TCP
  connection to `mcp-writer:8002`; without the gateway key and a write-scoped
  token of a real person it is refused. `web` cannot hold the key (E057).
- **Code running inside `mcp` holds the gateway key.** It still needs a
  person's token with `juristid.write` and, for any effect, that person's
  approval on Juristid's page with their authenticator.
- **Code running inside `mcp-writer`** can write what its role may — the
  allow-listed tables, through any SQL, not only through the services — but
  never an approval, an account, a grant or a token, and nothing is deleted
  but search projection rows (rebuildable). Its temporary-table and
  advisory-lock residuals are the read role's (NEW-02, above).
- **An approval is consent to the preview, not to the text's truth.** A note
  prepared from a prompt-injected document says what it says; the preview
  shows it in full, autoescaped, before the code is asked for.
- **The approval code is the MCP authenticator's**, the same factor that
  authorizes the connection: somebody who holds the gate password and a
  colleague's phone can connect *and* approve as them (ADR 0185,
  *Consequences*, unchanged).

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
- **What the SELECT-only role can still do (NEW-02).** Code running inside
  `mcp` (which takes a compromise of that process) can read every table but
  `django_session` — nothing in them is usable as a credential — and can still:
  switch its session's read-only default off and create *temporary* tables,
  because TEMPORARY stays with PUBLIC (revoking it is a database-wide change
  that would reach `web`); lower or lift its own `statement_timeout` and
  `idle_in_transaction_session_timeout`, which are user-settable; and take
  advisory locks (`pg_advisory_lock`) on keys `web` uses, blocking its
  writers until the session ends — no privilege prevents that. What holds:
  spilled sorts and hashes are bounded at 256 MB per session by
  `temp_file_limit`, which only a superuser can raise; at most 16
  connections; and stopping `mcp` ends every session, and with it every lock
  and every temporary table. What does **not** hold: PostgreSQL does not count
  explicit temporary tables against `temp_file_limit`, so such a session could
  still fill the database disk with them. Closing that is
  `REVOKE TEMPORARY ON DATABASE juristid FROM PUBLIC` plus a `GRANT` to the
  owner role — a database-wide change, deliberately not made by
  `mcp_db_role`, and the owner's decision.
- **Locks and suspensions page nobody (NEW-04).** A lock or a suspension
  writes an `McpAccessEvent` (`GRANT_LOCKED`, `GRANT_SUSPENDED`) and one
  WARNING line in the `web` log, and `mcp_access list` shows it (`lukus kuni`,
  `PEATATUD`). It is not also a `SecurityAuditEvent`: a new event type there
  is a migration in `app.audit`, outside this app. So a suspended colleague
  may learn of it from the consent page before an operator does — the two
  *Health* rows above are the check.
- **Anybody with the gate password can suspend a colleague's MCP access
  (NEW-05, by design).** Twenty wrong codes across the throttle windows, or
  from two gate sessions, suspend that person's grant until
  `mcp_access unlock`. Tokens already issued keep working — a suspension
  signals guessing, not theft — so the colleague loses only *new*
  connections. Thirty failures from the office's one shared address throttle
  everybody there for 15 minutes. What holds: the WARNING line and
  `mcp_access list` say who; talk to the person, then unlock.
- **A timing difference between «no grant» and «wrong code» (NEW-06).** The
  no-grant answer skips one or two local database round trips that a wrong
  code costs. Through Cloudflare that is hard to measure, every sample costs a
  throttle unit, and it reveals only whether a persona holds an MCP grant —
  the message and the status are identical.
- **`mcp` could reach `web` and choose its own client address (NEW-07a).**
  `web` believes `CF-Connecting-IP` from any peer once `TRUST_CF_CONNECTING_IP`
  is on (`app/core/client_ip.py` checks the header, not where it came from),
  and `mcp` shares the `internal` network with `web` because it needs `db`.
  `mcp` has no reason to call `web` at all, so code running inside `mcp` (a
  compromise of that process) could call `web:8000` directly with an address
  of its choosing for the audit rows and the throttles — never an identity
  and never an authorization input (ADR 0016). Closing it is a network
  change — `db` on a network of its own that `mcp` joins instead of
  `internal` — not made in this release.
- **The tunnel client's own egress (NEW-07b).** `mcp-tunnel` reaches only
  `mcp` by name, but its bridge's default route still reaches the Docker host
  and the office LAN, as every container's here does. Pre-existing, generic.
- **A release moves `mcp`, never `mcp-tunnel` (NEW-07c).** A future change to
  `mcp-tunnel`'s network or configuration takes effect only when somebody
  recreates it by name (*Starting and restarting only the MCP component*).
  First enablement creates both fresh.
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
  enrolment link — not to check it, not in a "masked" form. The same for the
  gateway key and the writer's database password.
- **Never give `mcp-writer` a host port, `mcp_edge`, a volume, `juristid.env`
  or `juristid-mcp.env`**, never put any other service on `mcp_write`, never
  put `MCP_WRITE_GATEWAY_KEY` into `juristid.env`, and never grant the writer
  role a write on an account, grant, token or approval table — not "for a
  quick fix", not by hand (`mcp_writer_db_role --apply` narrows it again, and
  E050 stops the gateway on it).
- **Never start `mcp-writer` as a side effect**, and never approve a change for
  somebody else: an approval is one person, one proposal, one button.
- **Never grant an account because an address looks right.** A grant is for a
  named person who asked, approved by the owner.
