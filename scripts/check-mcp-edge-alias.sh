#!/usr/bin/env bash
# Can the tunnel client still find `mcp` after `mcp` is recreated — and only `mcp`?
#
# The 2026-10-25 incident (docs/mcp/OPERATIONS.md, "After recreating `mcp`"):
# after `up -d --no-deps mcp` the container carried the alias `mcp` on
# `mcp_write` only, the tunnel client — on `mcp_edge` alone — got NXDOMAIN for
# MCP_SERVER_URL's host and disabled itself for good. compose.yml now names the
# alias on `mcp_edge`; tests/test_deployment_unraid_main.py reads that off the
# file. This proves what Docker actually does with it, on the real network
# model (`deploy/unraid-main/compose.yml`, `--profile mcp`), through the same
# recreate commands the runbook uses:
#
#   1. create `mcp` and `mcp-writer`;
#   2. recreate `mcp` with `up -d --no-deps mcp` after an environment-file
#      change (the incident's path), then once more with `--force-recreate`;
#   3. after each: from the tunnel client's own service definition, `mcp`
#      resolves and answers on `mcp_edge`; `mcp-writer` resolves under neither
#      name and its fixed `mcp_write` address is unreachable;
#   4. the probe is not vacuous: with the alias detached by hand — the state
#      the incident left — it fails, and a recreate from the file repairs it;
#   5. the same check after a Docker daemon restart (the host rebooted that
#      morning too), where the runner allows one.
#
# `mcp` and `mcp-writer` are stand-ins (the release image running a plain HTTP
# listener on their ports, no database): what is under test is the wiring, not
# the servers. A synthetic project and temporary environment files; nothing
# here touches /mnt/user. Usage: scripts/check-mcp-edge-alias.sh IMAGE
set -euo pipefail

# CI only. compose.yml fixes the container and network names
# (`juristid-main-mcp*`, `juristid-main-mcp-edge`, ...), so `-p` is no
# namespace: on the production host this would detach the real `mcp` from its
# network and restart the Docker daemon. It refuses anywhere but a GitHub
# Actions runner, and wherever a `juristid-main` stack exists at all.
if [ "${GITHUB_ACTIONS:-}" != "true" ]; then
  echo "refused: CI only (GITHUB_ACTIONS is not true); this script recreates and detaches containers" >&2
  exit 2
fi
if [ -n "$(docker ps -aq --filter label=com.docker.compose.project=juristid-main)" ]; then
  echo "refused: a juristid-main stack exists on this Docker host" >&2
  exit 2
fi

image="${1:?usage: $0 IMAGE (the release image, e.g. juristid:ci)}"
project=juristid-mcp-alias-ci
edge=juristid-main-mcp-edge
server=juristid-main-mcp
writer_address=10.213.250.10

work="$(mktemp -d)"
export JURISTID_IMAGE_TAG=mcpaliasci
docker tag "$image" "juristid-main-web:${JURISTID_IMAGE_TAG}"
for name in JURISTID_ENV_FILE JURISTID_MCP_ENV_FILE JURISTID_MCP_TUNNEL_ENV_FILE JURISTID_MCP_WRITER_ENV_FILE; do
  : > "$work/$name.env"
  export "$name=$work/$name.env"
done
cat > "$work/stand-ins.yml" <<'YAML'
services:
  mcp:
    entrypoint: ["python", "-m", "http.server"]
    command: ["8001"]
    healthcheck:
      disable: true
  mcp-writer:
    entrypoint: ["python", "-m", "http.server"]
    command: ["8002"]
    healthcheck:
      disable: true
YAML

compose() {
  docker compose -p "$project" --profile mcp \
    -f deploy/unraid-main/compose.yml -f "$work/stand-ins.yml" "$@"
}
cleanup() {
  compose down --remove-orphans >/dev/null 2>&1 || true
  rm -rf "$work"
}
trap cleanup EXIT

# What the tunnel client can see, from its own service definition (networks,
# user, read-only root) with only the entrypoint replaced. busybox `wget`
# fails both on a name it cannot resolve and on an address it cannot reach.
probe() {
  compose run --rm --no-deps --entrypoint /bin/sh mcp-tunnel -c '
    ok=""
    for _ in 1 2 3 4 5 6 7 8 9 10; do
      if wget -q -T 3 -O /dev/null http://mcp:8001/; then ok=1; break; fi
      sleep 1
    done
    if [ -z "$ok" ]; then echo "FAIL: http://mcp:8001/ is not reachable from mcp_edge"; exit 1; fi
    echo "ok: mcp resolves and answers on mcp_edge"
    for url in http://mcp-writer:8002/ http://juristid-main-mcp-writer:8002/ "http://$1:8002/"; do
      if wget -q -T 3 -O /dev/null "$url" 2>/dev/null; then
        echo "FAIL: the tunnel client reaches $url"; exit 1
      fi
      echo "ok: $url is out of reach"
    done
  ' probe "$writer_address"
}

check() {
  echo "--- $1"
  aliases="$(docker inspect -f "{{json (index .NetworkSettings.Networks \"$edge\").Aliases}}" "$server")"
  echo "aliases of $server on $edge: $aliases"
  if ! grep -q '"mcp"' <<<"$aliases"; then
    echo "FAIL: $server has no alias mcp on $edge"
    exit 1
  fi
  probe
}

container_id() { docker inspect -f '{{.Id}}' "$server"; }

docker version --format 'Docker Engine {{.Server.Version}} (API {{.Server.APIVersion}})'
docker compose version

compose up -d --no-deps mcp mcp-writer
check "created"

before="$(container_id)"
echo "MCP_RESOURCE_URLS=https://api.openai.com/v1/mcp/tunnel_ci" >> "$JURISTID_MCP_ENV_FILE"
compose up -d --no-deps mcp
if [ "$(container_id)" = "$before" ]; then
  echo "FAIL: an environment-file change did not recreate mcp"
  exit 1
fi
check "recreated by up -d --no-deps mcp after an environment-file change"

compose up -d --no-deps --force-recreate mcp
check "recreated by up -d --no-deps --force-recreate mcp"

echo "--- the probe sees the incident state: alias detached by hand"
docker network disconnect "$edge" "$server"
docker network connect "$edge" "$server"
if probe; then
  echo "FAIL: the probe passed without the alias, so it proves nothing"
  exit 1
fi
echo "ok: without the alias the tunnel client cannot find mcp"

compose up -d --no-deps --force-recreate mcp
check "repaired by a recreate from compose.yml"

# The host also rebooted that morning, between the recreate and the moment the
# missing alias was seen; which of the two dropped it was not established. So
# the daemon restarts too, and `restart: unless-stopped` brings `mcp` back.
echo "--- after a Docker daemon restart"
if command -v systemctl >/dev/null 2>&1 && sudo -n true 2>/dev/null; then
  sudo systemctl restart docker
  running=""
  for _ in $(seq 1 60); do
    if [ "$(docker inspect -f '{{.State.Running}}' "$server" 2>/dev/null || true)" = true ]; then
      running=1
      break
    fi
    sleep 1
  done
  if [ -z "$running" ]; then
    echo "FAIL: $server did not come back after the daemon restart"
    exit 1
  fi
  check "restarted with the Docker daemon"
else
  echo "skipped: no passwordless sudo and systemd here"
fi
