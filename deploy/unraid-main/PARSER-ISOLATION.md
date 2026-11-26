# Parser isolation — operator procedure (SEC-008, ADR 0230)

What changes on the host when the release carrying ADR 0230 is deployed, how to check
it, and how to go back. Nothing here is run by the release itself; the release plan's
ordinary `up -d --no-build` creates the two new containers.

## What is new

| Item | Value |
|---|---|
| Containers | `juristid-main-intake-parser`, `juristid-main-batch-parser` (release image, `python -m app.documents.extraction.isolation_server`) |
| Volumes | `juristid-main_parser_intake_socket`, `juristid-main_parser_batch_socket` — `local` driver, `type=tmpfs`, 1 MiB, uid/gid 10001; hold one Unix socket each; not data, not backed up |
| Changed services | `intake-reader` (+ socket volume, `DOCUMENT_PARSER_ISOLATION=service`, depends on `intake-parser` started); `web` (+ socket volume, same two variables) |
| Not changed | `db`, `tunnel`, migrations (none), backups, cron, the env file |

Each parser container: `network_mode: none`, no `env_file`, read-only root, `/scratch`
tmpfs 256 MiB, `mem_limit`/`memswap_limit` 2560 MiB, `cpus: 1.0`, `pids_limit: 32`,
`cap_drop: ALL`, `no-new-privileges`, `init: true`. Idle cost: one Python process each
(≈ 40–60 MiB RSS); under load at most `mem_limit`.

## Before the deploy

1. Host memory: confirm the two ceilings fit next to what runs today
   (`free -m`; `docker stats --no-stream`). Two parsers busy at once is the worst case
   (≈ 5 GiB); in practice the batch parser works only during the nightly cycle and
   operator corpus runs.
2. Dry run, and read it: `docker compose -p juristid-main -f deploy/unraid-main/compose.yml up -d --no-build --dry-run`
   must show **Create** for `intake-parser` and `batch-parser`, **Recreate** for `web`,
   `intake-reader` (and the other release-image workers), and must not recreate `db`
   or `tunnel` (see the db/tunnel drift note in README).

## Deploy

The release plan unchanged (`scripts/deploy/lib.sh` → `deployment_plan`). Its
`up -d --no-build` creates the parsers.

## Verify (read-only)

```sh
C="docker compose -p juristid-main -f deploy/unraid-main/compose.yml"
$C ps intake-parser batch-parser intake-reader web          # all running
docker inspect -f '{{.HostConfig.NetworkMode}}' juristid-main-intake-parser   # none
docker inspect -f '{{.HostConfig.NetworkMode}}' juristid-main-batch-parser    # none
docker exec juristid-main-intake-parser env | grep -ciE 'postgres|secret|api_key'  # 0
docker exec juristid-main-intake-parser python -m app.documents.extraction.isolation_server --check --socket /run/juristid-parser/parser.sock && echo socket-ok
docker exec juristid-main-web python -m app.documents.extraction.isolation_server --check --socket /run/juristid-parser/parser.sock && echo web-reaches-batch-parser
$C logs --tail 5 intake-reader      # «Failide lugemise isolatsioon: service.»
```

Functional check with a synthetic file only (no production document): on `Uus teema`,
add a small text or PDF file you made for the purpose and watch it read; then
`$C logs --tail 5 intake-parser` shows one `parser child mime=… seconds=… peak_rss_mb=…
code=-` line (numbers only, no content). Remove the form without creating a Teema.

After the first night: `docker logs juristid-main-batch-parser --since 12h | grep -c "parser child"`
counts the Riigikogu reads, and `grep -v "code=-"` shows any file the boundary stopped.

## When something is wrong

* **Reader logs `intake parser unavailable … handed back`** — the parser container is
  down or restarting. Files wait (PENDING) and are read when it is back; forms and
  `Loo teema` keep working. `$C up -d --no-build --no-deps intake-parser`.
* **Riigikogu reads `UNREADABLE (ParserUnavailable)`** — the same for `batch-parser`;
  the night records it as unreadable documents and the next night reads them again.
* **A legitimate file stops at a limit** — its row reads «… Originaal on alles ja
  avatav.» and the parser log line shows the usage. Raise the specific limit in the env
  file (`DOCUMENT_PARSER_*`, read by `web`/`intake-reader`) and, if above the ceiling,
  in the parser service's `JURISTID_PARSER_MAX_*`; owner decision first.

## Rollback

1. Previous image, as for any release (`JURISTID_IMAGE_TAG=<previous>` and the
   previous checkout, then `up -d --no-build`). Older code ignores
   `DOCUMENT_PARSER_*` and parses in process again — that is the rollback's whole
   effect; no data to restore.
2. The previous `compose.yml` has no parser services, so `up -d` with it leaves the two
   containers as orphans: `$C up -d --no-build --remove-orphans` removes them (check the
   dry run first: it must name only `intake-parser` and `batch-parser` as orphans), or
   leave them running — they hold nothing and reach nothing.
3. The tmpfs volumes vanish with their containers; `docker volume rm
   juristid-main_parser_intake_socket juristid-main_parser_batch_socket` if wanted.

No migration, no backup step, no permission change on the host.
