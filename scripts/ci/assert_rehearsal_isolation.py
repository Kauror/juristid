"""Does the real-data rehearsal template resolve to an isolated stack?

Run against `docker compose config --format json` for
`deploy/recovery-rehearsal/compose.real-data.yml` plus its source overlay, from
the compose smoke job in CI.

`tests/test_deployment_recovery_rehearsal.py` already reads the file. This is
the other half of the same question, and the halves are not interchangeable:
the test reads what was *written*, and Compose is what decides what it *means*.
An interpolation that silently resolves to an empty string, a profile spelling
Compose ignores, an overlay whose merge replaces a list instead of extending it
— none of those are visible in the YAML, and every one of them would first be
noticed by somebody in the middle of a rehearsal with a production backup set
open. So the resolved configuration is checked here, where Compose produced it.

The stack is never started. `config` interpolates and validates; it builds
nothing, starts nothing and touches no host path, so running this can never
bring a real-data stack up.

No Docker and no network of its own: it reads a JSON file.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

#: The project `scripts/deploy/lib.sh` accepts for a rehearsal. Production is
#: `juristid-main`, and the whole point of the template is that it can never
#: resolve to it.
REHEARSAL_PROJECT = "juristid-recovery-rehearsal"

#: Services a plain `up` is allowed to start, and the only names this file may
#: state literally — because *this is the rule*: a rehearsal exists to observe
#: what came back, so the database and a shell are what may start on their own.
DEFAULT_SERVICES = ("db", "web")


def profiled_services(services: dict) -> list[str]:
    """Everything else, derived — and every one of them must be profiled.

    This was a second literal tuple, `("extractor", "searchindex")`, and it went
    stale the same way `assert_deployment_identity.py` did when `extractor` left
    the stacks with docs/adr/0072: the guard failed saying it was "looking at
    the wrong file" when the file was right and the list was old.

    Derived is also *stronger* than the list was. A service added to the
    template with no profile used to pass this check silently, because it was
    not one of the two names being asked about; now it is in scope the moment
    it exists, and the assertion below is that everything outside
    `DEFAULT_SERVICES` sits behind `workers`.
    """
    return sorted(set(services) - set(DEFAULT_SERVICES))


#: Host trees a rehearsal may never bind at all. The source corpus is not here:
#: it is the one production path the overlay may mount, read-only, and it is
#: checked separately below.
PRODUCTION_TREES = ("/mnt/user/appdata/juristid-main", "/mnt/user/backups/juristid-main")


def fail(message: str) -> None:
    print(f"::error::{message}")
    sys.exit(1)


def main(argv: list[str]) -> None:
    if len(argv) != 2:
        fail("usage: assert_rehearsal_isolation.py CONFIG_JSON")

    config = json.loads(Path(argv[1]).read_text(encoding="utf-8"))
    services = config.get("services", {})
    networks = config.get("networks", {})

    if config.get("name") != REHEARSAL_PROJECT:
        fail(
            f"the stack resolved to project '{config.get('name')}' rather than "
            f"'{REHEARSAL_PROJECT}'. A rehearsal must not be able to name production."
        )

    for name in DEFAULT_SERVICES:
        if name not in services:
            fail(f"no '{name}' service; this check is looking at the wrong file")

    profiled = profiled_services(services)
    if not profiled:
        # Guards the guard: a template with nothing but `db` and `web` would
        # satisfy every loop below by having nothing to check.
        fail("no service outside db/web; the rehearsal template defines no workers at all")

    # -- nothing can reach it ------------------------------------------------

    publishing = sorted(name for name, service in services.items() if service.get("ports"))
    if publishing:
        fail(
            f"these publish a host port: {', '.join(publishing)}. The stack holds the "
            "register and has no authenticator in front of it."
        )

    if list(networks) != ["rehearsal"]:
        fail(f"expected one network named 'rehearsal', got {sorted(networks)}")
    if networks["rehearsal"].get("internal") is not True:
        fail("the rehearsal network is not internal; the stack could route off the bridge")

    for name, service in services.items():
        if service.get("network_mode"):
            fail(f"'{name}' sets network_mode, which undoes the internal network")
        if "cloudflare" in str(service.get("image", "")):
            fail(f"'{name}' runs cloudflared; a rehearsal must not be tunnelled")
        if service.get("restart"):
            fail(f"'{name}' sets a restart policy; a rehearsal must not survive a reboot")

    # -- nothing writes to the restored data unless asked --------------------

    for name in profiled:
        if "workers" not in services[name].get("profiles", []):
            fail(f"'{name}' is not behind the 'workers' profile and would start on `up`")
    for name in DEFAULT_SERVICES:
        if services[name].get("profiles"):
            fail(f"'{name}' is behind a profile and would not start at all")

    if "gunicorn" in " ".join(services["web"].get("command", []) or []):
        fail("the rehearsal 'web' runs a server; it must only be driven with `run --rm`")

    # -- the volumes ---------------------------------------------------------

    corpus_mounts = 0
    for name, service in services.items():
        for volume in service.get("volumes", []):
            source = str(volume.get("source", ""))
            target = str(volume.get("target", ""))
            if not source:
                fail(
                    f"'{name}' resolved an empty bind source for '{target}'. An interpolation "
                    "that empties itself is how a rehearsal writes somewhere nobody chose."
                )
            for tree in PRODUCTION_TREES:
                if source == tree or source.startswith(tree + "/"):
                    fail(f"'{name}' binds production at '{source}'")
            if target == "/srv/historical-source":
                corpus_mounts += 1
                if not volume.get("read_only"):
                    fail(
                        f"'{name}' mounts the source corpus writable. It is a manual export "
                        "that a restore does not recover, and this stack exists to be deleted."
                    )

    if corpus_mounts != 1:
        fail(
            f"the source overlay resolved {corpus_mounts} corpus mounts, expected exactly one. "
            "A merge that dropped or duplicated it would change what the rehearsal proves."
        )

    print(
        f"{config['name']}: no published ports, internal network, "
        f"{', '.join(profiled)} behind a profile, corpus read-only"
    )


if __name__ == "__main__":
    main(sys.argv)
